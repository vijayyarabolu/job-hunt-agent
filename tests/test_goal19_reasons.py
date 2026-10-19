"""Not interested → "Other…" with a typed reason: stored, shown on the Archived row, counted in Stats; the
feedback table from before (four reasons, no note) is rebuilt once with its rows kept."""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from job_hunt_agent import api, config, db, feedback, stats, stats_html, store
from job_hunt_agent.ats import FetchResult
from tests.test_server import make_job, seed_job

NOW = datetime(2026, 10, 12, 21, 0, tzinfo=timezone.utc)
FAMS = ("ai_engineer", "ml_engineer", "data_engineer")
OLD_DDL = """CREATE TABLE feedback (
  id INTEGER PRIMARY KEY, profile TEXT NOT NULL, uid TEXT NOT NULL, at TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('like', 'pass', 'clear')),
  reason TEXT CHECK (reason IS NULL OR reason IN ('too_senior', 'wrong_role', 'location', 'company')),
  company TEXT NOT NULL DEFAULT '', family TEXT NOT NULL DEFAULT '', states TEXT NOT NULL DEFAULT '');
CREATE INDEX feedback_profile_uid ON feedback(profile, uid, id);"""


def seed_more(conn, key: str, company: str) -> str:
    """One more fetched job on its own board (synthetic)."""
    item = make_job(ats="lever", key=key, ext_id="1", company=company, title="Machine Learning Engineer",
                    location="Austin, TX", url=f"https://jobs.lever.co/{key}/1", posted_at=db.iso(NOW),
                    description="Python")
    db.sync_boards(conn, [{"board": f"lever:{key}", "ats": "lever", "key": key, "company": company}])
    run = db.start_run(conn, "test", NOW)
    store.write_board(conn, db.board(conn, f"lever:{key}"), FetchResult([item], 200, n_raw=1), "ok", run, NOW,
                      ["fam:ml_engineer"], 5)
    return item["uid"]


class OtherReasonTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = mock.patch.object(config, "DB_PATH", Path(self.tmp.name) / "t.db")
        self.patch.start()
        self.conn = db.connect()
        self.uid = seed_job(self.conn)

    def tearDown(self):
        self.conn.close()
        self.patch.stop()
        self.tmp.cleanup()

    def test_other_needs_a_typed_reason_and_keeps_it_on_one_line(self):
        with self.assertRaises(ValueError):
            feedback.record(self.conn, "alex", self.uid, "pass", "other", NOW, FAMS)
        with self.assertRaises(ValueError):
            feedback.record(self.conn, "alex", self.uid, "pass", "other", NOW, FAMS, "x" * 201)
        with self.assertRaises(ValueError):
            feedback.record(self.conn, "alex", self.uid, "pass", "company", NOW, FAMS, "a note on a chip")
        out = feedback.record(self.conn, "alex", self.uid, "pass", "other", NOW, FAMS, "needs a\nclearance ")
        self.assertEqual(out["note"], "needs a clearance")
        self.assertEqual(feedback.reason_text(feedback.latest(self.conn, "alex")[self.uid]), "Other: needs a clearance")

    def test_other_has_no_ranking_effect(self):
        feedback.record(self.conn, "alex", self.uid, "pass", "other", NOW, FAMS, "commute")
        self.assertEqual(feedback.load(self.conn, "alex"), feedback.Prefs())

    def test_reason_text(self):
        self.assertEqual(feedback.reason_text({"kind": "pass", "reason": "too_senior"}), "Too senior")
        self.assertIsNone(feedback.reason_text({"kind": "like", "reason": None}))
        self.assertIsNone(feedback.reason_text(None))

    def test_the_old_table_is_rebuilt_once_with_its_rows(self):
        self.conn.execute("DROP TABLE IF EXISTS feedback")
        self.conn.executescript(OLD_DDL)
        self.conn.execute("INSERT INTO feedback(profile, uid, at, kind, reason, company) VALUES "
                          "('alex', ?, '2026-10-01T00:00:00Z', 'pass', 'company', 'acme')", (self.uid,))
        self.conn.commit()
        feedback.ensure(self.conn)
        feedback.ensure(self.conn)
        rows = self.conn.execute("SELECT reason, company, note FROM feedback").fetchall()
        self.assertEqual([tuple(r) for r in rows], [("company", "acme", None)])
        feedback.record(self.conn, "alex", self.uid, "pass", "other", NOW, FAMS, "visa")
        self.assertEqual(feedback.latest(self.conn, "alex")[self.uid]["note"], "visa")
        names = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master")}
        self.assertIn("feedback_profile_uid", names)
        self.assertNotIn("feedback_old", names)

    def test_stats_count_every_reason_and_list_the_typed_ones(self):
        other, third = seed_more(self.conn, "beta", "Beta"), seed_more(self.conn, "gamma", "Gamma")
        feedback.record(self.conn, "alex", self.uid, "pass", "too_senior", NOW, FAMS)
        feedback.record(self.conn, "alex", other, "pass", "other", NOW, FAMS, "Contract only")
        feedback.record(self.conn, "alex", third, "pass", "other", NOW, FAMS, "contract only")
        got = feedback.reason_counts(self.conn, "alex")
        self.assertEqual(got["total"], 3)
        self.assertEqual({r["key"]: r["n"] for r in got["by_reason"]}["other"], 2)
        self.assertEqual(got["other_notes"], [{"note": "contract only", "n": 2}])
        html = stats_html.render(stats.report(self.conn, "alex"))
        self.assertIn("Not interested: why", html)
        self.assertIn("contract only (2)", html)
        self.assertNotIn("Not interested: why", stats_html.render(stats.report(self.conn, "jordan")))

    def test_the_api_takes_the_note_and_archives_the_job(self):
        entry = api.feedback_action(self.conn, "alex", self.uid, "pass", "other", NOW, "On-site 5 days")
        self.assertEqual(entry["note"], "On-site 5 days")
        state = self.conn.execute("SELECT state FROM triage_events WHERE uid = ? ORDER BY id DESC",
                                  (self.uid,)).fetchone()
        self.assertEqual(state[0], "dismissed")


if __name__ == "__main__":
    unittest.main()
