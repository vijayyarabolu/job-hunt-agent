"""GOAL30 §0c: the nightly check said "found a drop: ashby: apply filled 5 of 14 (was 5)" four mornings running. The
page had found 5 more fields (9 to 14) while its fills held at 5, so its fill rate fell; nothing dropped. A notice
now fires only on a real drop (fewer fields filled than the last good run) or a fixture filling fewer than it should,
says which in plain words, and the same failure night after night is one notice, not one a night."""
from job_hunt_agent.autofill import health, keys

from tests.test_goal23_autofill import AutofillCase

ASHBY = {"name": "ashby-fixture", "board": "ashby", "source": "fixture", "step": "apply", "expected": 4}


class CanaryNoticeTest(AutofillCase):
    def notices(self):
        return [n["text"] for n in health.notices(self.conn)]

    def test_more_fields_with_the_same_fills_is_no_drop(self):
        self.assertEqual(health.record_canary(self.conn, "2026-10-15T08:10:05Z", [{**ASHBY, "fields": 9, "filled": 5}]), [])
        self.assertEqual(health.record_canary(self.conn, "2026-10-17T08:10:06Z", [{**ASHBY, "fields": 14, "filled": 5}]), [])
        self.assertEqual(self.notices(), [])
        row = self.conn.execute("SELECT ok, detail FROM autofill_canary WHERE run_at = '2026-10-17T08:10:06Z'").fetchone()
        self.assertEqual((row["ok"], row["detail"]), (1, "14 fields found (was 9 on Oct 15), fills held at 5"))

    def test_a_real_drop_says_down_from_and_the_day(self):
        health.record_canary(self.conn, "2026-10-15T08:10:05Z", [{**ASHBY, "fields": 14, "filled": 9}])
        lines = health.record_canary(self.conn, "2026-10-16T08:10:05Z", [{**ASHBY, "fields": 14, "filled": 5}])
        self.assertEqual(lines, ["ashby: apply page filled 5 of 14, down from 9 of 14 on Oct 15"])
        self.assertEqual(self.notices(), ["Autofill's nightly check: ashby: apply page filled 5 of 14, down from 9 of 14 on Oct 15"])

    def test_below_what_the_fixture_should_fill_says_so_without_drop(self):
        lines = health.record_canary(self.conn, "2026-10-16T08:10:05Z", [{**ASHBY, "fields": 14, "filled": 3}])
        self.assertEqual(lines, ["ashby: apply page filled 3 of 14, it should fill at least 4"])
        self.assertNotIn("drop", self.notices()[0])
        self.assertNotIn("(was", self.notices()[0])

    def test_a_failed_page_keeps_why_for_the_health_page(self):
        """The Health page lists each failed page with its detail: the reason, not an empty cell."""
        health.record_canary(self.conn, "2026-10-15T08:10:05Z", [{**ASHBY, "fields": 14, "filled": 9}])
        health.record_canary(self.conn, "2026-10-16T08:10:05Z", [{**ASHBY, "fields": 14, "filled": 5}])
        health.record_canary(self.conn, "2026-10-16T08:10:05Z", [{**ASHBY, "name": "ashby-2", "fields": 14, "filled": 3,
                                                                  "error": "timeout"}])
        got = dict(self.conn.execute("SELECT name, detail FROM autofill_canary WHERE ok = 0").fetchall())
        self.assertEqual(got, {"ashby-fixture": "down from 9 of 14 on Oct 15", "ashby-2": "it should fill at least 4; timeout"})

    def test_the_same_failure_again_is_not_a_new_notice(self):
        health.record_canary(self.conn, "2026-10-15T08:10:05Z", [{**ASHBY, "fields": 14, "filled": 9}])
        health.record_canary(self.conn, "2026-10-16T08:10:05Z", [{**ASHBY, "fields": 14, "filled": 5}])
        self.assertEqual(health.record_canary(self.conn, "2026-10-17T08:10:05Z", [{**ASHBY, "fields": 14, "filled": 5}]), [])
        self.assertEqual(len(self.notices()), 1)
        worse = health.record_canary(self.conn, "2026-10-18T08:10:05Z", [{**ASHBY, "fields": 14, "filled": 2}])
        self.assertEqual(worse, ["ashby: apply page filled 2 of 14, down from 9 of 14 on Oct 15"])
        self.assertEqual(len(self.notices()), 2)

    def test_a_long_question_naming_email_is_not_the_email_field(self):
        take_home = ("Are you comfortable completing a frontend take-home assessment? You’ll receive an email from "
                     "CodeSignal with the challenge details.")
        self.assertIsNone(keys.classify(take_home))
        self.assertEqual(keys.classify("Email").key, "email")
        self.assertEqual(keys.classify("If located in the US, in what city and state do you reside?").key, "location")
