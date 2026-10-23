"""Applied as cards (2026-10-16): applications by the day they were sent (newest first), segments with steady counts,
search by company or role, a feed job gets its feed card, a job with no feed uid gets a card that opens its row."""
from datetime import timedelta

from job_hunt_agent import appliedview, db, trackapi
from tests.test_goal13_resumes import TODAY_DATE, ResumesTabCase
from tests.test_goal8_flow import NOW, PID


class AppliedCardsTest(ResumesTabCase):
    def row(self, uid=None, status="applied", day=TODAY_DATE, company="Acme", role="ML Engineer"):
        app = trackapi.create(self.conn, PID, {"job_uid": uid, "status": "saved", "company": company,
                                               "role_title": role})["id"]
        with db.tx(self.conn):
            self.conn.execute("UPDATE applications SET status = ?, applied_on = ? WHERE id = ?",
                              (status, day.isoformat() if day else None, app))
        return app

    def board(self, seg="all", q=None):
        return appliedview.board(self.conn, PID, seg, q, NOW)

    def test_grouped_by_applied_day_newest_first_with_feed_cards(self):
        old = self.row(self.uids[0], day=TODAY_DATE - timedelta(days=3))
        new = self.row(self.uids[1])
        got = self.board()
        self.assertEqual([c["ap"]["app"] for c in got["jobs"]], [new, old])
        self.assertTrue(got["jobs"][0]["ap"]["day"].startswith("Today · "))
        self.assertTrue(got["jobs"][0]["ap"]["day"].endswith("(1)"))
        self.assertEqual(got["jobs"][0]["uid"], self.uids[1])          # the feed card itself (JD pane opens it)
        self.assertIn("match", got["jobs"][0])

    def test_saved_rows_are_not_applications(self):
        trackapi.create(self.conn, PID, {"job_uid": self.uids[0], "status": "saved"})
        self.assertEqual(self.board()["total"], 0)

    def test_segments_and_counts(self):
        self.row(self.uids[0])
        self.row(self.uids[1], status="interview")
        self.row(self.uids[2], status="rejected")
        got = self.board("interviewing")
        self.assertEqual([c["ap"]["status"] for c in got["jobs"]], ["interview"])
        self.assertEqual(got["counts"], {"all": 3, "waiting": 1, "interviewing": 1, "rejected": 1})
        with self.assertRaises(ValueError):
            self.board("nope")

    def test_search_by_company_or_role_keeps_counts(self):
        self.row(None, company="Capital One", role="Data Scientist")
        self.row(None, company="Roku", role="ML Engineer")
        got = self.board(q="capital")
        self.assertEqual([c["company"] for c in got["jobs"]], ["Capital One"])
        self.assertEqual(got["counts"]["all"], 2)
        self.assertEqual([c["company"] for c in self.board(q="ml eng")["jobs"]], ["Roku"])

    def test_a_job_with_no_feed_uid_opens_its_tracker_row(self):
        app = self.row(None, company="Recruiter Co", role="AI Engineer")
        card = self.board()["jobs"][0]
        self.assertEqual((card["uid"], card["outside"], card["company"]), (f"app:{app}", app, "Recruiter Co"))

    def test_a_row_with_no_date_goes_last(self):
        self.row(None, company="Undated", day=None)
        self.row(None, company="Dated")
        self.assertEqual([c["company"] for c in self.board()["jobs"]], ["Dated", "Undated"])
        self.assertTrue(self.board()["jobs"][1]["ap"]["day"].startswith("No date"))


class FollowupsNewestFirstTest(AppliedCardsTest):
    def mail(self, app, kind, state="applied"):
        with db.tx(self.conn):
            self.conn.execute("INSERT INTO mail_messages (profile, msg_id, sender, subject, sent_at, kind, method, app_id, "
                              "state, read_at) VALUES (?, ?, 'jobs@acme.com', ?, '2026-10-15T10:00:00Z', ?, 'claude', ?, ?, "
                              "'2026-10-15T11:00:00Z')", (PID, f"m{app}{kind}", f"About your {kind}", kind, app, state))

    def test_newest_application_first_and_answered_ones_drop_out(self):
        older = self.row(None, company="Old Co", day=TODAY_DATE - timedelta(days=8))
        newer = self.row(None, company="New Co", day=TODAY_DATE - timedelta(days=4))
        answered = self.row(None, company="Answered Co", day=TODAY_DATE - timedelta(days=5))
        acked = self.row(None, company="Acked Co", day=TODAY_DATE - timedelta(days=6))
        dismissed = self.row(None, company="Dismissed Co", day=TODAY_DATE - timedelta(days=7))
        self.mail(answered, "screen", state="needs")
        self.mail(acked, "received")
        self.mail(dismissed, "rejected", state="dismissed")
        got = trackapi.followups(self.conn, PID, today=TODAY_DATE)
        self.assertEqual([r["id"] for r in got["overdue"]], [newer, acked, dismissed, older])
        self.assertEqual(got["overdue"][1]["mail"]["kind"], "received")
        self.assertIsNone(got["overdue"][0]["mail"])
