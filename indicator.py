#!/usr/bin/python3
# Managed by codex_limit_widget installer
"""현재 Codex 한도를 GNOME AppIndicator에 표시한다."""

import fcntl
import json
import math
import os
from pathlib import Path
import selectors
import signal
import subprocess
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import AyatanaAppIndicator3, GLib, Gtk

CODEX = os.environ.get("CODEX_USAGE_CODEX", "codex")
KST = ZoneInfo("Asia/Seoul")


def read_rate_limits(stop):
    """원문을 저장하지 않고 읽기 전용 RPC 결과를 반환한다."""
    deadline = time.monotonic() + 20
    env = dict(os.environ)
    process = subprocess.Popen(
        [CODEX, "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, start_new_session=True, env=env,
    )
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            pending = bytearray()

            def send(message):
                process.stdin.write(json.dumps(message).encode() + b"\n")
                process.stdin.flush()

            def receive(request_id):
                while not stop.is_set():
                    while b"\n" in pending:
                        line, _, remainder = pending.partition(b"\n")
                        pending[:] = remainder
                        message = json.loads(line)
                        if message.get("id") != request_id:
                            continue
                        if "error" in message:
                            error = str(message["error"]).lower()
                            auth = ("not logged in", "unauthorized", "authentication required", "login required")
                            raise RuntimeError("login" if any(term in error for term in auth) else "failed")
                        return message["result"]
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError()
                    if selector.select(min(remaining, 0.2)):
                        chunk = os.read(process.stdout.fileno(), 65536)
                        if not chunk:
                            raise RuntimeError("failed")
                        pending.extend(chunk)
                raise RuntimeError("stopped")

            send({"id": 1, "method": "initialize", "params": {
                "clientInfo": {"name": "codex_usage_indicator", "version": "1.0"},
            }})
            receive(1)
            send({"method": "initialized"})
            send({"id": 2, "method": "account/rateLimits/read"})
            return receive(2)
    finally:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        process.stdin.close()
        process.stdout.close()


def select_windows(result):
    buckets = result.get("rateLimitsByLimitId") or {}
    bucket = buckets.get("codex")
    if bucket is None:
        legacy = result.get("rateLimits") or {}
        bucket = legacy if legacy.get("limitId") in (None, "codex") else {}
    windows = []
    for key in ("primary", "secondary"):
        window = bucket.get(key)
        if window is None:
            continue
        used = window["usedPercent"]
        minutes = window.get("windowDurationMins")
        reset = window.get("resetsAt")
        if isinstance(used, bool) or not isinstance(used, (int, float)) or not math.isfinite(used) or used < 0:
            raise ValueError("invalid usage")
        if minutes is not None and (isinstance(minutes, bool) or not isinstance(minutes, int) or minutes <= 0):
            raise ValueError("invalid period")
        if reset is not None:
            if isinstance(reset, bool) or not isinstance(reset, (int, float)) or not math.isfinite(reset):
                raise ValueError("invalid reset")
            datetime.fromtimestamp(reset, KST)
        name = {300: "5h", 10080: "주간", None: "기간 미제공"}.get(minutes, f"{minutes}분")
        windows.append({"name": name, "used": used, "reset": reset})
    return windows


def format_windows(windows, now):
    if not windows:
        return "Codex 한도 정보 없음", []
    label = "Codex " + " · ".join(f"{w['name']} 남음 {max(0, 100 - w['used']):g}%" for w in windows)
    future = [w for w in windows if w["reset"] is not None and w["reset"] > now]
    if future:
        next_window = min(future, key=lambda w: w["reset"])
        label += " · ↻" + datetime.fromtimestamp(next_window["reset"], KST).strftime("%m/%d %H:%M")
    elif any(w["reset"] is not None for w in windows):
        label += " · 리셋 확인 중"
    details = []
    for window in windows:
        reset = window["reset"]
        when = "리셋 시각 미제공" if reset is None else datetime.fromtimestamp(reset, KST).strftime("%Y-%m-%d %H:%M KST")
        if reset is not None and reset <= now:
            when += " (리셋 확인 중)"
        details.append(f"{window['name']} 남음 {max(0, 100 - window['used']):g}% · 사용 {window['used']:g}% · {when}")
    if future:
        details.append(f"다음 리셋 기간: {next_window['name']}")
    return label, details


class Indicator:
    def __init__(self):
        self.windows = None
        self.last_success = None
        self.failure = None
        self.busy = False
        self.stop = threading.Event()
        self.worker = None
        self.rendered = None
        self.indicator = AyatanaAppIndicator3.Indicator.new(
            "codex-usage-indicator", "utilities-system-monitor",
            AyatanaAppIndicator3.IndicatorCategory.APPLICATION_STATUS,
        )
        self.indicator.set_title("Codex 사용량")
        self.indicator.set_status(AyatanaAppIndicator3.IndicatorStatus.ACTIVE)
        self.render()
        self.refresh()
        GLib.timeout_add_seconds(60, self.refresh)
        GLib.timeout_add_seconds(1, self.render)

    def render(self):
        label, details = format_windows(self.windows, time.time())
        if self.windows is None:
            label = "Codex 조회 실패" if self.failure else "Codex 조회 중"
        elif self.failure:
            label += " !"
        if self.failure == "login":
            label = "Codex 로그인 필요"
        details.append("갱신 실패" if self.failure else ("조회 중" if self.busy else "갱신 완료"))
        when = self.last_success.strftime("%Y-%m-%d %H:%M:%S KST") if self.last_success else "없음"
        details.append("마지막 성공 갱신: " + when)
        snapshot = (label, tuple(details), self.busy)
        if snapshot == self.rendered:
            return True
        self.rendered = snapshot
        self.indicator.set_label(label, "")
        menu = Gtk.Menu()
        for detail in details:
            item = Gtk.MenuItem.new_with_label(detail)
            item.set_sensitive(False)
            menu.append(item)
        menu.append(Gtk.SeparatorMenuItem())
        refresh = Gtk.MenuItem.new_with_label("새로고침")
        refresh.set_sensitive(not self.busy)
        refresh.connect("activate", lambda *_: self.refresh())
        menu.append(refresh)
        quit_item = Gtk.MenuItem.new_with_label("종료")
        quit_item.connect("activate", self.quit)
        menu.append(quit_item)
        menu.show_all()
        self.indicator.set_menu(menu)
        return True

    def refresh(self):
        if self.busy or self.stop.is_set():
            return True
        self.busy = True
        self.render()

        def query():
            try:
                windows = select_windows(read_rate_limits(self.stop))
                failure = None
            except Exception as error:
                windows = None
                failure = "login" if isinstance(error, RuntimeError) and str(error) == "login" else "failed"
            GLib.idle_add(self.complete, windows, failure)

        self.worker = threading.Thread(target=query)
        self.worker.start()
        return True

    def complete(self, windows, failure):
        if self.stop.is_set():
            return False
        self.busy = False
        self.failure = failure
        if failure is None:
            self.windows = windows
            self.last_success = datetime.now(KST)
        self.render()
        return False

    def quit(self, *_):
        self.stop.set()
        Gtk.main_quit()

    def run(self):
        try:
            Gtk.main()
        finally:
            self.stop.set()
            if self.worker:
                self.worker.join()


def main():
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    with (runtime / "codex-usage-indicator.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        app = Indicator()
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, app.quit)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, app.quit)
        app.run()


if __name__ == "__main__":
    main()
