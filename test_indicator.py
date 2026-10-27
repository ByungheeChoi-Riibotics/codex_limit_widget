#!/usr/bin/python3
"""오프라인 한도 표시와 실제 JSONL 프로세스 경계를 확인한다."""
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
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
elif mode.startswith("{"):
    send({"id": 2, "result": json.loads(mode)})
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
        patch.dict(os.environ, XDG_STATE_HOME=str(Path(self.tmp.name, "state"))).start()
        self.notifications = []
        patch.object(app, "send_expiry_notification", self.notifications.append, create=True).start()
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

    def settle(self, widget):
        deadline = time.monotonic() + 5
        while (widget.busy or getattr(widget, "notifying", False)) and time.monotonic() < deadline:
            app.GLib.MainContext.default().iteration(True)
        self.assertFalse(widget.busy)
        self.assertFalse(widget.notifying)

    def labels(self, widget):
        return [row.get_label() for row in widget.indicator.get_menu().get_children() if isinstance(row, app.Gtk.MenuItem)]

    def start_widget(self, result):
        self.mode.write_text(json.dumps(result))
        widget = app.Indicator()
        def cleanup():
            widget.stop.set()
            if widget.worker:
                widget.worker.join()
            if widget.notification_worker:
                widget.notification_worker.join()
            widget.indicator.set_status(app.AyatanaAppIndicator3.IndicatorStatus.PASSIVE)
        self.addCleanup(cleanup)
        self.settle(widget)
        return widget

    def test_menu_shows_credit_balance_and_available_reset_expiries_in_kst(self):
        result = {"rateLimitsByLimitId": {"codex": {"primary": WEEK, "credits": {"hasCredits": True, "unlimited": False, "balance": "12345.6789000000"}}}, "rateLimitResetCredits": {"availableCount": 3, "credits": [
            {"status": "available", "expiresAt": 1793322975},
            {"status": "available", "expiresAt": None},
            {"status": "redeemed", "expiresAt": 1793322975},
            {"status": "redeeming", "expiresAt": 1793322975},
            {"status": "unknown", "expiresAt": 1793322975},
        ]}}
        widget = self.start_widget(result)
        self.assertEqual(widget.indicator.get_label(), "Codex 주간 남음 67% · ↻10/30 10:16")
        self.assertEqual(self.labels(widget)[2:7], ["일반 크레딧 잔액: 12345.6789000000", "사용 가능한 리셋권: 3개", "리셋권 상세: 2/3개 (일부 상세)", "리셋권 1 기한: 2026-10-30 10:16 KST", "리셋권 2 기한: 만료 없음"])

    def use_clock(self, now):
        self.now = now
        self.monotonic_offset = 0
        patch.object(app, "time", SimpleNamespace(time=lambda: self.now, monotonic=lambda: time.monotonic() + self.monotonic_offset)).start()

    def advance(self, seconds, suspended=False):
        self.now += seconds
        if not suspended:
            self.monotonic_offset += seconds

    def reset_result(self, expiries, count=None):
        return {"rateLimits": {"primary": WEEK}, "rateLimitResetCredits": {"availableCount": len(expiries) if count is None else count, "credits": [{"status": "available", "expiresAt": expiry} for expiry in expiries]}}

    def test_noon_notifies_up_to_72_hours_once_across_render_and_restart(self):
        self.use_clock(1793069999)
        result = self.reset_result([1793070000, 1793070001, 1793329200, 1793329201, None])
        widget = self.start_widget(result)
        self.assertEqual(self.notifications, [])
        self.advance(1)
        widget.render()
        self.settle(widget)
        self.assertEqual(self.notifications, [[1793070001, 1793329200]])
        widget.render()
        widget.refresh()
        self.settle(widget)
        self.assertEqual(self.notifications, [[1793070001, 1793329200]])
        self.now = 1793069999
        other = self.start_widget(result)
        self.advance(1)
        other.render()
        self.settle(other)
        self.assertEqual(self.notifications, [[1793070001, 1793329200]])
        state_files = [path for path in Path(self.tmp.name, "state").rglob("*") if path.is_file()]
        self.assertEqual([path.read_text() for path in state_files], ["2026-10-27"])

    def test_notification_failure_recovers_with_fresh_retry_and_persists_only_success(self):
        self.use_clock(1793069999)
        widget = self.start_widget(self.reset_result([1793329200]))
        attempts = []
        def notify(expiries):
            attempts.append(expiries)
            if len(attempts) == 1:
                raise RuntimeError("notification unavailable")
            self.notifications.append(expiries)
        patch.object(app, "send_expiry_notification", notify).start()
        self.advance(1)
        widget.render()
        self.settle(widget)
        self.assertIn("리셋권 알림 실패", self.labels(widget))
        self.assertEqual(self.notifications, [])
        widget.render()
        self.assertEqual(len(attempts), 1)
        self.advance(5)
        widget.render()
        self.settle(widget)
        self.assertEqual(self.notifications, [[1793329200]])
        self.assertNotIn("리셋권 알림 실패", self.labels(widget))

    def test_menu_distinguishes_zero_unlimited_missing_partial_and_invalid_credits(self):
        widget = self.start_widget({"rateLimits": {"credits": {"hasCredits": False, "unlimited": False, "balance": "0"}}, "rateLimitResetCredits": {"availableCount": 0, "credits": []}})
        cases = [
            ({"rateLimits": {"credits": {"hasCredits": False, "unlimited": False, "balance": "0"}}, "rateLimitResetCredits": {"availableCount": 0, "credits": []}}, ["일반 크레딧 잔액: 0", "사용 가능한 리셋권: 0개", "리셋권 상세: 없음"]),
            ({"rateLimits": {"credits": {"unlimited": True, "balance": None}}, "rateLimitResetCredits": None}, ["일반 크레딧 잔액: 무제한", "사용 가능한 리셋권: 미제공", "리셋권 상세: 미제공"]),
            ({"rateLimits": {"credits": {"balance": None}}, "rateLimitResetCredits": {"availableCount": 4, "credits": None}}, ["일반 크레딧 잔액: 미제공", "사용 가능한 리셋권: 4개", "리셋권 상세: 미제공"]),
            ({"rateLimitResetCredits": {"availableCount": None, "credits": []}}, ["일반 크레딧 잔액: 미제공", "사용 가능한 리셋권: 미제공", "리셋권 상세: 0/미제공개"]),
            ({"rateLimitResetCredits": {"availableCount": 4, "credits": []}}, ["일반 크레딧 잔액: 미제공", "사용 가능한 리셋권: 4개", "리셋권 상세: 0/4개 (일부 상세)"]),
            ({"rateLimits": {"credits": {"balance": "NaN"}}, "rateLimitResetCredits": {"availableCount": True, "credits": [{"status": "available"}, {"status": "available", "expiresAt": True}, {"status": "available", "expiresAt": "tomorrow"}, {"status": "available", "expiresAt": 10**100}, {"status": "redeemed", "expiresAt": 1793329200}]}}, ["일반 크레딧 잔액: 미제공", "사용 가능한 리셋권: 미제공", "리셋권 상세: 4/미제공개", "리셋권 1 기한: 미제공", "리셋권 2 기한: 미제공", "리셋권 3 기한: 미제공", "리셋권 4 기한: 미제공"]),
        ]
        for result, expected in cases:
            with self.subTest(result=result):
                self.mode.write_text(json.dumps(result))
                widget.refresh()
                self.settle(widget)
                self.assertEqual(self.labels(widget)[:len(expected)], expected)
        self.mode.write_text("error")
        widget.refresh()
        self.settle(widget)
        self.assertIn("일반 크레딧 잔액: 미제공 !", self.labels(widget))
        self.assertIn("사용 가능한 리셋권: 미제공 !", self.labels(widget))
        self.mode.write_text(json.dumps(cases[0][0]))
        widget.refresh()
        self.settle(widget)
        self.assertIn("일반 크레딧 잔액: 0", self.labels(widget))
        self.assertNotIn("일반 크레딧 잔액: 미제공 !", self.labels(widget))

    def test_noon_waits_for_new_query_after_inflight_stale_result(self):
        self.use_clock(1793069999)
        started, release = threading.Event(), threading.Event()
        responses = [self.reset_result([1793329200]), self.reset_result([1793242800])]
        def read(stop):
            result = responses.pop(0)
            if not started.is_set():
                started.set()
                if not release.wait(5):
                    raise TimeoutError()
            return result
        patch.object(app, "read_rate_limits", read).start()
        widget = app.Indicator()
        self.addCleanup(widget.stop.set)
        self.addCleanup(lambda: widget.indicator.set_status(app.AyatanaAppIndicator3.IndicatorStatus.PASSIVE))
        self.assertTrue(started.wait(5))
        self.advance(1)
        widget.render()
        self.assertEqual(self.notifications, [])
        release.set()
        self.settle(widget)
        self.assertEqual(self.notifications, [[1793242800]])
        self.assertIn("리셋권 1 기한: 2026-10-29 12:00 KST", self.labels(widget))

    def test_noon_query_failure_never_notifies_stale_credits_or_later_recovery(self):
        self.use_clock(1793069999)
        widget = self.start_widget(self.reset_result([1793329200]))
        self.mode.write_text("error")
        self.advance(1)
        widget.render()
        self.settle(widget)
        self.assertEqual(self.notifications, [])
        self.assertIn("사용 가능한 리셋권: 1개 !", self.labels(widget))
        self.advance(60)
        self.mode.write_text(json.dumps(self.reset_result([1793329200])))
        widget.refresh()
        self.settle(widget)
        self.assertEqual(self.notifications, [])
        self.assertIn("사용 가능한 리셋권: 1개", self.labels(widget))

    def test_starting_after_noon_or_suspending_across_noon_never_catches_up(self):
        self.use_clock(1793070001)
        widget = self.start_widget(self.reset_result([1793329200]))
        widget.render()
        self.assertEqual(self.notifications, [])
        self.now = 1793156399
        widget.render()
        self.advance(2, suspended=True)
        widget.render()
        widget.refresh()
        self.settle(widget)
        self.assertEqual(self.notifications, [])

    def test_missing_details_unknown_dates_and_unavailable_credits_do_not_notify(self):
        self.use_clock(1793069999)
        result = {"rateLimitResetCredits": {"availableCount": 4, "credits": [{"status": "redeemed", "expiresAt": 1793070001}, {"status": "redeeming", "expiresAt": 1793070001}, {"status": "available", "expiresAt": None}, {"status": "available", "expiresAt": True}, {"status": "available"}]}}
        widget = self.start_widget(result)
        self.advance(1)
        widget.render()
        self.settle(widget)
        self.assertEqual(self.notifications, [])

    def test_awake_main_loop_delay_within_noon_minute_still_notifies(self):
        self.use_clock(1793069999)
        widget = self.start_widget(self.reset_result([1793329200]))
        self.advance(30)
        widget.render()
        self.settle(widget)
        self.assertEqual(self.notifications, [[1793329200]])

    def test_delivery_success_with_state_write_failure_never_repeats_in_same_run(self):
        self.use_clock(1793069999)
        widget = self.start_widget(self.reset_result([1793329200]))
        with patch.object(app.os, "fsync", side_effect=OSError("disk full")):
            self.advance(1)
            widget.render()
            self.settle(widget)
            self.assertEqual(self.notifications, [[1793329200]])
            self.advance(5)
            widget.render()
            self.settle(widget)
            self.assertEqual(self.notifications, [[1793329200]])
        self.assertIn("리셋권 알림 기록 실패", self.labels(widget))

    def test_noon_query_completing_after_1201_does_not_notify_late(self):
        self.use_clock(1793069999)
        widget = self.start_widget(self.reset_result([1793070001, 1793329200, 1793329201]))
        started, release = threading.Event(), threading.Event()
        def read(stop):
            started.set()
            if not release.wait(5):
                raise TimeoutError()
            return self.reset_result([1793070001, 1793329200, 1793329201])
        patch.object(app, "read_rate_limits", read).start()
        self.advance(1)
        widget.render()
        self.assertTrue(started.wait(5))
        self.advance(61)
        release.set()
        self.settle(widget)
        self.assertEqual(self.notifications, [])

    def test_failed_noon_query_retries_only_fresh_results_within_noon_minute(self):
        self.use_clock(1793069999)
        widget = self.start_widget(self.reset_result([1793329200]))
        self.mode.write_text("error")
        self.advance(1)
        widget.render()
        self.settle(widget)
        self.assertEqual(self.notifications, [])
        self.mode.write_text(json.dumps(self.reset_result([1793242800])))
        self.advance(5)
        widget.render()
        self.settle(widget)
        self.assertEqual(self.notifications, [[1793242800]])

    def test_slow_notification_delivery_keeps_gui_responsive(self):
        self.use_clock(1793069999)
        widget = self.start_widget(self.reset_result([1793329200]))
        started, release, finished = threading.Event(), threading.Event(), threading.Event()
        def notify(expiries):
            started.set()
            delivered = release.wait(2)
            finished.set()
            if not delivered:
                raise TimeoutError()
            self.notifications.append(expiries)
        patch.object(app, "send_expiry_notification", notify).start()
        self.addCleanup(release.set)
        self.advance(1)
        widget.render()
        deadline = time.monotonic() + 5
        while widget.busy and time.monotonic() < deadline:
            app.GLib.MainContext.default().iteration(True)
        self.assertTrue(started.wait(5))
        self.assertFalse(finished.is_set())
        widget.render()
        self.assertIn("갱신 완료", self.labels(widget))
        self.assertFalse(finished.is_set())
        release.set()
        self.settle(widget)
        self.assertEqual(self.notifications, [[1793329200]])

    def test_noon_query_completed_after_suspend_within_minute_does_not_notify(self):
        self.use_clock(1793069999)
        widget = self.start_widget(self.reset_result([1793329200]))
        started, release = threading.Event(), threading.Event()
        def read(stop):
            started.set()
            if not release.wait(5):
                raise TimeoutError()
            return self.reset_result([1793329200])
        patch.object(app, "read_rate_limits", read).start()
        self.advance(1)
        widget.render()
        self.assertTrue(started.wait(5))
        self.advance(30, suspended=True)
        release.set()
        self.settle(widget)
        self.assertEqual(self.notifications, [])

    def test_suspended_notification_worker_does_not_send_after_gui_resumes_first(self):
        self.use_clock(1793069999)
        widget = self.start_widget(self.reset_result([1793329200]))
        started, release = threading.Event(), threading.Event()
        real_flock = app.fcntl.flock
        def lock(file, operation):
            if operation == app.fcntl.LOCK_EX:
                started.set()
                if not release.wait(5):
                    raise TimeoutError()
            return real_flock(file, operation)
        patch.object(app.fcntl, "flock", lock).start()
        self.addCleanup(release.set)
        self.advance(1)
        widget.render()
        deadline = time.monotonic() + 5
        while widget.busy and time.monotonic() < deadline:
            app.GLib.MainContext.default().iteration(True)
        self.assertTrue(started.wait(5))
        self.advance(30, suspended=True)
        widget.render()
        release.set()
        self.settle(widget)
        self.assertEqual(self.notifications, [])

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
            if widget.notification_worker:
                widget.notification_worker.join()
            widget.indicator.set_status(app.AyatanaAppIndicator3.IndicatorStatus.PASSIVE)
        self.assert_reaped()


if __name__ == "__main__":
    unittest.main(verbosity=2)
