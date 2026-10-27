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
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import AyatanaAppIndicator3, Gio, GLib, Gtk

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


def select_bucket(result):
    buckets = result.get("rateLimitsByLimitId") or {}
    bucket = buckets.get("codex")
    if bucket is None:
        legacy = result.get("rateLimits") or {}
        bucket = legacy if legacy.get("limitId") in (None, "codex") else {}
    return bucket


def select_windows(result):
    bucket = select_bucket(result)
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


def select_credits(result):
    credits = select_bucket(result).get("credits")
    balance = "미제공"
    if isinstance(credits, dict):
        if credits.get("unlimited") is True:
            balance = "무제한"
        elif isinstance(credits.get("balance"), str):
            try:
                value = Decimal(credits["balance"])
                if value.is_finite() and value >= 0:
                    balance = credits["balance"]
            except InvalidOperation:
                pass
    resets = result.get("rateLimitResetCredits")
    resets = resets if isinstance(resets, dict) else {}
    count = resets.get("availableCount")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        count = None
    rows = resets.get("credits")
    expiries = []
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict) or row.get("status") != "available":
                continue
            expiry = row.get("expiresAt", "unknown")
            if expiry is not None:
                try:
                    if isinstance(expiry, bool) or not isinstance(expiry, int):
                        raise ValueError()
                    datetime.fromtimestamp(expiry, KST)
                except (ValueError, OverflowError, OSError):
                    expiry = "unknown"
            expiries.append(expiry)
    return {"balance": balance, "count": count, "expiries": expiries, "provided": isinstance(rows, list)}


def format_credits(credits):
    count = credits["count"]
    expiries = credits["expiries"]
    details = ["일반 크레딧 잔액: " + credits["balance"], "사용 가능한 리셋권: " + ("미제공" if count is None else f"{count}개")]
    if not credits["provided"]:
        details.append("리셋권 상세: 미제공")
    elif not expiries and count == 0:
        details.append("리셋권 상세: 없음")
    else:
        total = "미제공" if count is None else str(count)
        details.append(f"리셋권 상세: {len(expiries)}/{total}개" + (" (일부 상세)" if count is not None and len(expiries) < count else ""))
        for index, expiry in enumerate(expiries, 1):
            when = "만료 없음" if expiry is None else ("미제공" if expiry == "unknown" else datetime.fromtimestamp(expiry, KST).strftime("%Y-%m-%d %H:%M KST"))
            details.append(f"  ↳ 리셋권 {index} 기한: {when}")
    return details


def send_expiry_notification(expiries):
    dates = [datetime.fromtimestamp(expiry, KST).strftime("%Y-%m-%d %H:%M KST") for expiry in expiries]
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    bus.call_sync(
        "org.freedesktop.Notifications", "/org/freedesktop/Notifications",
        "org.freedesktop.Notifications", "Notify",
        GLib.Variant("(susssasa{sv}i)", (
            "Codex 사용량", 0, "dialog-warning", "Codex 리셋권 만료 예정",
            f"72시간 이내 만료되는 사용 가능한 리셋권 {len(expiries)}개\n" + "\n".join(dates),
            [], {}, -1,
        )), GLib.VariantType.new("(u)"), Gio.DBusCallFlags.NONE, 3000, None,
    )


class Indicator:
    def __init__(self):
        self.credits = select_credits({})
        self.windows = None
        self.last_success = None
        self.failure = None
        self.busy = False
        self.stop = threading.Event()
        self.worker = None
        self.rendered = None
        self.last_tick = (time.time(), time.monotonic())
        self.clock_generation = 0
        self.pending_noon = None
        self.notification_failure = False
        self.notification_retry = None
        self.notification_day = None
        self.notification_state_failure = False
        self.notification_worker = None
        self.notifying = False
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
        if self.stop.is_set():
            return False
        now, monotonic = time.time(), time.monotonic()
        previous, previous_monotonic = self.last_tick
        self.last_tick = now, monotonic
        noon = datetime.fromtimestamp(now, KST).replace(hour=12, minute=0, second=0, microsecond=0).timestamp()
        awake = abs((now - previous) - (monotonic - previous_monotonic)) < 0.5
        if not awake:
            self.clock_generation += 1
            self.pending_noon = None
            self.notification_retry = None
        if previous < noon <= now < noon + 60 and awake:
            self.pending_noon = noon
            self.refresh()
        if self.notification_retry is not None and now >= self.notification_retry[1]:
            retry_noon, _ = self.notification_retry
            self.notification_retry = None
            if now < retry_noon + 60:
                self.pending_noon = retry_noon
                self.refresh()
        label, details = format_windows(self.windows, now)
        if self.windows is None:
            label = "Codex 조회 실패" if self.failure else "Codex 조회 중"
        elif self.failure:
            label += " !"
        if self.failure == "login":
            label = "Codex 로그인 필요"
        details.extend(detail + (" !" if self.failure else "") for detail in format_credits(self.credits))
        if self.notification_state_failure:
            details.append("리셋권 알림 기록 실패")
        elif self.notification_failure:
            details.append("리셋권 알림 실패")
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
        self.query_started = time.time()
        self.render()

        def query():
            try:
                result = read_rate_limits(self.stop)
                windows = select_windows(result)
                credits = select_credits(result)
                failure = None
            except Exception as error:
                windows = None
                credits = None
                failure = "login" if isinstance(error, RuntimeError) and str(error) == "login" else "failed"
            GLib.idle_add(self.complete, windows, failure, credits)

        self.worker = threading.Thread(target=query)
        self.worker.start()
        return True

    def complete(self, windows, failure, credits):
        if self.stop.is_set():
            return False
        self.busy = False
        self.failure = failure
        if failure is None:
            self.credits = credits
            self.windows = windows
            self.last_success = datetime.fromtimestamp(time.time(), KST)
        if self.pending_noon is not None:
            noon = self.pending_noon
            now = time.time()
            awake = abs((now - self.last_tick[0]) - (time.monotonic() - self.last_tick[1])) < 0.5
            if not noon <= now < noon + 60 or not awake:
                self.pending_noon = None
            elif self.query_started < noon:
                self.refresh()
            else:
                self.pending_noon = None
                if failure is None:
                    expiries = [expiry for expiry in credits["expiries"] if isinstance(expiry, int) and 0 < expiry - noon <= 72 * 3600 and expiry > time.time()]
                    if credits["count"] and expiries:
                        self.notify_once(noon, expiries)
                elif time.time() < noon + 60:
                    self.notification_retry = (noon, time.time() + 5)
        self.render()
        return False

    def notify_once(self, noon, expiries):
        day = datetime.fromtimestamp(noon, KST).strftime("%Y-%m-%d")
        if self.notification_day == day or self.notifying:
            return
        self.notifying = True
        generation = self.clock_generation

        def deliver():
            delivered = False
            failure = False
            state = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "codex-usage-indicator" / "last-reset-notification"
            try:
                state.parent.mkdir(parents=True, exist_ok=True)
                with state.open("a+") as saved:
                    fcntl.flock(saved, fcntl.LOCK_EX)
                    saved.seek(0)
                    if saved.read() == day:
                        delivered = True
                    else:
                        now = time.time()
                        awake = abs((now - self.last_tick[0]) - (time.monotonic() - self.last_tick[1])) < 0.5
                        if self.stop.is_set() or generation != self.clock_generation or not noon <= now < noon + 60 or not awake:
                            GLib.idle_add(self.notification_complete, noon, day, False, False)
                            return
                        send_expiry_notification(expiries)
                        delivered = True
                        saved.seek(0)
                        saved.truncate()
                        saved.write(day)
                        saved.flush()
                        os.fsync(saved.fileno())
            except Exception:
                failure = True
            GLib.idle_add(self.notification_complete, noon, day, delivered, failure)

        self.notification_worker = threading.Thread(target=deliver)
        self.notification_worker.start()

    def notification_complete(self, noon, day, delivered, failure):
        self.notifying = False
        if self.stop.is_set():
            return False
        if delivered:
            self.notification_day = day
            self.notification_state_failure = failure
            self.notification_failure = False
        elif failure:
            self.notification_failure = True
            self.notification_retry = (noon, time.time() + 5)
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
            if self.notification_worker:
                self.notification_worker.join()


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
