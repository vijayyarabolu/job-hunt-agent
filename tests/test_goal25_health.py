"""GOAL25 §4 (user 2026-10-14 17:06): Autofill never fails silently. Errors and unknown layouts are saved grouped by
board + step (never a password); Settings › Autofill › Health shows the last 7 days; the nightly canary's drop
against its last good run is a notice job-hunt-agent shows the next morning; job-hunt-agent knows which extension version each
person's Chrome uses (GOAL23's sends none) and says when a newer one is ready."""
from datetime import datetime, timezone

from job_hunt_agent import server
from job_hunt_agent.autofill import api, health, package

from tests.test_goal23_autofill import AutofillCase
from tests.test_goal8_flow import PID

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)


class HealthTest(AutofillCase):
    def setUp(self):
        super().setUp()
        health._seen.clear()

    def test_an_old_extension_is_seen_as_1_0_0_and_told_to_update(self):
        server.route(self.conn, "GET", "/api/autofill/ping", {"profile": PID, "_ext": ""}, NOW)
        got = api.get(self.conn, PID, "/api/autofill/notices", {})["extension"]
        self.assertEqual((got["installed"], got["current"], got["newer"]), ("1.0.0", package.version(), True))
        server.route(self.conn, "GET", "/api/autofill/ping", {"profile": PID, "_ext": package.version()}, NOW)
        self.assertFalse(api.get(self.conn, PID, "/api/autofill/notices", {})["extension"]["newer"])

    def test_the_answers_page_itself_is_not_an_extension(self):
        server.route(self.conn, "GET", "/api/autofill/answers", {"profile": PID}, NOW)
        self.assertIsNone(health.extension(self.conn, PID)["installed"])

    def test_errors_are_grouped_by_board_and_step_with_the_latest_message(self):
        for n in (1, 2):
            api.post(self.conn, PID, "/api/autofill/error", {"board": "workday", "step": "questions1", "kind": "error",
                                                              "message": f"boom {n}", "html": "<form></form>", "version": "1.1.0"}, NOW)
        api.post(self.conn, PID, "/api/autofill/error", {"board": "lever", "kind": "unknown_layout", "message": "new page"}, NOW)
        errors = health.report(self.conn, PID, NOW)["errors"]
        self.assertEqual([(e["board"], e["step"], e["kind"], e["n"], e["message"]) for e in errors],
                         [("workday", "questions1", "error", 2, "boom 2"), ("lever", "", "unknown_layout", 1, "new page")])
        with self.assertRaises(ValueError):
            api.post(self.conn, PID, "/api/autofill/error", {"kind": "anything"}, NOW)

    def test_an_error_older_than_7_days_is_not_in_health(self):
        api.post(self.conn, PID, "/api/autofill/error", {"board": "workday", "message": "old"},
                 datetime(2026, 10, 1, tzinfo=timezone.utc))
        self.assertEqual(health.report(self.conn, PID, NOW)["errors"], [])

    def test_a_canary_drop_is_a_notice_until_closed(self):
        page = {"name": "greenhouse-fixture", "board": "greenhouse", "source": "fixture", "step": "apply", "fields": 12}
        self.assertEqual(health.record_canary(self.conn, "2026-10-13T03:10:00Z", [{**page, "filled": 10}]), [])
        self.assertEqual(health.notices(self.conn), [])
        drops = health.record_canary(self.conn, "2026-10-14T03:10:00Z", [{**page, "filled": 4}])
        self.assertEqual(drops, ["greenhouse: apply page filled 4 of 12, down from 10 of 12 on Oct 13"])
        notes = api.get(self.conn, PID, "/api/autofill/notices", {})["notices"]
        self.assertEqual(len(notes), 1)
        self.assertIn("greenhouse: apply page filled 4 of 12, down from 10 of 12 on Oct 13", notes[0]["text"])
        last = health.report(self.conn, PID, NOW)["canary"]
        self.assertEqual((last["at"], last["failed"]), ("2026-10-14T03:10:00Z", 1))
        api.post(self.conn, PID, "/api/autofill/notice/close", {"id": notes[0]["id"]}, NOW)
        self.assertEqual(health.notices(self.conn), [])

    def test_a_page_below_its_expected_fill_or_with_an_error_fails(self):
        drops = health.record_canary(self.conn, "2026-10-14T03:10:00Z", [
            {"name": "lever-fixture", "board": "lever", "step": "apply", "fields": 10, "filled": 5, "expected": 8},
            {"name": "ashby-live", "board": "ashby", "source": "live", "fields": 0, "filled": 0, "error": "page did not load"}])
        self.assertEqual(drops, ["lever: apply page filled 5 of 10, it should fill at least 8",
                                 "ashby: live page filled 0 of 0: page did not load"])

    def test_a_live_page_fails_only_when_it_fills_nothing_and_a_closed_one_is_skipped(self):
        drops = health.record_canary(self.conn, "2026-10-14T03:10:00Z", [
            {"name": "greenhouse-live", "board": "greenhouse", "source": "live", "fields": 20, "filled": 3},
            {"name": "lever-live", "board": "lever", "source": "live", "fields": 12, "filled": 0},
            {"name": "ashby-live", "board": "ashby", "source": "live", "skip": "no application form on the page (posting closed?)"}])
        self.assertEqual(drops, ["lever: live page filled 0 of 12"])

    def test_fill_rate_per_board_counts_autofill_needs_and_hand(self):
        with self.conn:
            self.conn.execute("INSERT INTO autofill_log(profile, board, filled, needs, at) VALUES (?,?,?,?,?)",
                              (PID, "greenhouse", 8, 1, "2026-10-14T10:00:00Z"))
            self.conn.execute("INSERT INTO autofill_hand(profile, job_uid, board, step, hand, at) VALUES (?,?,?,?,?,?)",
                              (PID, "J1", "greenhouse", "", 1, "2026-10-14T10:00:00Z"))
        rows = health.report(self.conn, PID, NOW)["boards"]
        self.assertEqual([(r["board"], r["filled"], r["needs"], r["hand"], r["rate"]) for r in rows], [("greenhouse", 8, 1, 1, 80)])
