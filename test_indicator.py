#!/usr/bin/python3
"""오프라인 한도 표시와 실제 JSONL 프로세스 경계를 확인한다."""
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("indicator", Path(__file__).with_name("indicator.py"))
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)
RESET = 1793322975
WEEK = {"usedPercent": 33, "windowDurationMins": 10080, "resetsAt": RESET}
RESULT = {"rateLimitsByLimitId": {"codex": {"primary": WEEK, "secondary": None}}}

FAKE = '''#!/usr/bin/python3
import json, os, signal, sys
mode = open(os.environ["TEST_MODE_FILE"]).read()
def send(value):
    print(json.dumps(value), flush=True)
assert sys.argv[1:] == ["app-server"]
first = json.loads(input())
assert first["id"] == 1 and first["method"] == "initialize"
assert first["params"]["clientInfo"]["name"] == "codex_usage_indicator"
send({"method": "notice"})
send({"id": 91, "error": "unrelated error"})
send({"id": 1, "result": {}})
assert json.loads(input()) == {"method": "initialized"}
request = json.loads(input())
assert request == {"id": 2, "method": "account/rateLimits/read"}
if mode == "eof":
    sys.exit(0)
if mode == "malformed":
    print("not JSON", flush=True)
elif mode in ("auth", "error"):
    send({"id": 2, "error": "authentication required" if mode == "auth" else "service unavailable"})
elif mode == "timeout":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
else:
    send({"id": 17, "result": {"wrong": True}})
    send({"id": 2, "result": {"rateLimits": {"primary": {"usedPercent": 33, "windowDurationMins": 10080, "resetsAt": 1793322975}}}})
while True:
    signal.pause()
'''


class DisplayTest(unittest.TestCase):
    def test_used_63_percent_displays_remaining_37_percent(self):
        windows = app.select_windows({"rateLimits": {"primary": dict(WEEK, usedPercent=63)}})
        self.assertEqual(app.format_windows(windows, RESET - 1), ("Codex 주간 남음 37% · ↻10/30 10:16", ["주간 남음 37% · 사용 63% · 2026-10-30 10:16 KST", "다음 리셋 기간: 주간"]))
        for used, remaining in ((0, 100), (100, 0), (101, 0)):
            with self.subTest(used=used):
                windows = app.select_windows({"rateLimits": {"primary": dict(WEEK, usedPercent=used)}})
                label, details = app.format_windows(windows, RESET - 1)
                self.assertIn(f"주간 남음 {remaining}%", label)
                self.assertIn(f"주간 남음 {remaining}% · 사용 {used}%", details[0])

    def test_returned_periods_and_codex_bucket_are_used(self):
        expected = [{"name": "주간", "used": 33, "reset": RESET}]
        self.assertEqual(app.select_windows(RESULT), expected)
        self.assertEqual(app.select_windows({"rateLimits": {"primary": WEEK}}), expected)
        self.assertEqual(app.select_windows({"rateLimits": {"limitId": "other", "primary": WEEK}}), [])
        self.assertEqual(app.select_windows({"rateLimitsByLimitId": {"other": {"primary": WEEK}}}), [])
        mixed = {"rateLimitsByLimitId": {"codex": {"secondary": WEEK}}, "rateLimits": {"primary": {"usedPercent": 99}}}
        self.assertEqual(app.select_windows(mixed), expected)
        for minutes, name in ((300, "5h"), (60, "60분"), (None, "기간 미제공")):
            with self.subTest(minutes=minutes):
                self.assertEqual(app.select_windows({"rateLimits": {"primary": {"usedPercent": 12.5, "windowDurationMins": minutes}}}), [{"name": name, "used": 12.5, "reset": None}])
        for result in ({}, {"rateLimits": None}, {"rateLimitsByLimitId": {}}, {"rateLimits": {"primary": None, "secondary": None}}):
            self.assertEqual(app.select_windows(result), [])

    def test_malformed_values_are_rejected(self):
        for field, values in (("usedPercent", [True, -1, "33", float("nan"), float("inf")]), ("windowDurationMins", [True, 0, -1, 3.5, "300"]), ("resetsAt", [True, "tomorrow", float("nan"), float("inf"), 1e100])):
            for value in values:
                with self.subTest(field=field, value=value):
                    with self.assertRaises((ValueError, OverflowError, OSError)):
                        app.select_windows({"rateLimits": {"primary": dict(WEEK, **{field: value})}})
        with self.assertRaises(KeyError):
            app.select_windows({"rateLimits": {"primary": {}}})

    def test_kst_nearest_future_and_elapsed_reset_keep_usage(self):
        weekly = app.select_windows(RESULT)
        self.assertEqual(app.format_windows(weekly, RESET - 1), ("Codex 주간 남음 67% · ↻10/30 10:16", ["주간 남음 67% · 사용 33% · 2026-10-30 10:16 KST", "다음 리셋 기간: 주간"]))
        self.assertEqual(app.format_windows(weekly, RESET), ("Codex 주간 남음 67% · 리셋 확인 중", ["주간 남음 67% · 사용 33% · 2026-10-30 10:16 KST (리셋 확인 중)"]))
        both = [{"name": "5h", "used": 18, "reset": RESET - 3600}] + weekly
        label, details = app.format_windows(both, RESET - 7200)
        self.assertEqual(label, "Codex 5h 남음 82% · 주간 남음 67% · ↻10/30 09:16")
        self.assertEqual(details[-1], "다음 리셋 기간: 5h")
        label, details = app.format_windows(both, RESET - 1800)
        self.assertTrue(label.endswith("↻10/30 10:16"))
        self.assertIn("리셋 확인 중", details[0])
        self.assertEqual(details[-1], "다음 리셋 기간: 주간")
        self.assertEqual(app.format_windows([], RESET), ("Codex 한도 정보 없음", []))
        self.assertEqual(app.format_windows([{"name": "기간 미제공", "used": 12.5, "reset": None}], RESET), ("Codex 기간 미제공 남음 87.5%", ["기간 미제공 남음 87.5% · 사용 12.5% · 리셋 시각 미제공"]))


class ProcessIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="codex-usage-test-")
        self.addCleanup(self.tmp.cleanup)
        self.mode = Path(self.tmp.name, "mode")
        cli = Path(self.tmp.name, "codex")
        cli.write_text(FAKE)
        cli.chmod(0o700)
        self.addCleanup(patch.stopall)
        patch.object(app, "CODEX", str(cli)).start()
        patch.dict(os.environ, TEST_MODE_FILE=str(self.mode)).start()
        self.children = []
        real_popen = subprocess.Popen
        def launch(*args, **kwargs):
            child = real_popen(*args, **kwargs)
            self.children.append(child)
            return child
        patch.object(subprocess, "Popen", launch).start()

    def assert_reaped(self):
        for child in self.children:
            self.assertIsNotNone(child.returncode)
            with self.assertRaises(ChildProcessError):
                os.waitpid(child.pid, os.WNOHANG)
            with self.assertRaises(ProcessLookupError):
                os.killpg(child.pid, 0)

    def test_jsonl_success_ignores_unrelated_ids_and_reaps_child(self):
        self.mode.write_text("success")
        result = app.read_rate_limits(threading.Event())
        self.assertEqual(app.select_windows(result), [{"name": "주간", "used": 33, "reset": RESET}])
        self.assert_reaped()

    def test_protocol_errors_eof_and_malformed_json_reap_children(self):
        for mode, exception, message in (("auth", RuntimeError, "login"), ("error", RuntimeError, "failed"), ("eof", RuntimeError, "failed"), ("malformed", json.JSONDecodeError, None)):
            with self.subTest(mode=mode):
                self.mode.write_text(mode)
                with self.assertRaises(exception) as caught:
                    app.read_rate_limits(threading.Event())
                if message:
                    self.assertEqual(str(caught.exception), message)
                self.assert_reaped()

    def test_total_timeout_forces_and_reaps_unresponsive_child(self):
        self.mode.write_text("timeout")
        with self.assertRaises(TimeoutError):
            app.read_rate_limits(threading.Event())
        self.assertEqual(self.children[-1].returncode, -signal.SIGKILL)
        self.assert_reaped()

    def test_gui_keeps_stale_value_and_recovers_after_success(self):
        self.mode.write_text("success")
        widget = app.Indicator()
        def settled():
            deadline = time.monotonic() + 5
            while widget.busy and time.monotonic() < deadline:
                app.GLib.MainContext.default().iteration(True)
            self.assertFalse(widget.busy)
        try:
            settled()
            first = widget.last_success
            self.assertEqual(widget.indicator.get_label(), "Codex 주간 남음 67% · ↻10/30 10:16")
            self.mode.write_text("error")
            widget.refresh()
            settled()
            self.assertTrue(widget.indicator.get_label().endswith(" !"))
            self.assertEqual(widget.last_success, first)
            labels = [row.get_label() for row in widget.indicator.get_menu().get_children() if isinstance(row, app.Gtk.MenuItem)]
            self.assertIn("갱신 실패", labels)
            self.mode.write_text("success")
            widget.refresh()
            settled()
            self.assertFalse(widget.indicator.get_label().endswith(" !"))
            self.assertGreaterEqual(widget.last_success, first)
            self.assertEqual(widget.failure, None)
        finally:
            widget.stop.set()
            if widget.worker:
                widget.worker.join()
            widget.indicator.set_status(app.AyatanaAppIndicator3.IndicatorStatus.PASSIVE)
        self.assert_reaped()


if __name__ == "__main__":
    unittest.main(verbosity=2)
