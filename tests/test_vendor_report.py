"""GOAL5 §3 evidence report (job-hunt-agent vendor-report): per-vendor coverage of vendor scores, 2 real jobs per vendor with
reasoning, jobs kept from a robots-dropped board as a labeled fallback, and the reason for a modeled vendor with no job."""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from job_hunt_agent import config, db, fits, store, vendor_report
from job_hunt_agent.ats.common import FetchResult, make_job

NOW = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)
JD = ("About the role: you will build and ship machine learning systems for our customers.\n"
      "Requirements:\n- Python and SQL in production\n- PyTorch model training\n- Kubernetes and Docker\n- AWS\n"
      "Nice to have:\n- Spark\n- Airflow\n") * 2


BOARDS = [("lever", "acme", "Acme"), ("smartrecruiters", "Gone", "Gone Inc")]


def seed(conn) -> None:
    db.sync_boards(conn, [{"board": f"{a}:{k}", "ats": a, "key": k, "company": c} for a, k, c in BOARDS])
    run = db.start_run(conn, "test", NOW)
    for ats, key, company in BOARDS:
        item = make_job(ats=ats, key=key, ext_id="1", company=company, title="Machine Learning Engineer",
                        location="Austin, TX", url=f"https://example.com/{ats}/1", posted_at=db.iso(NOW),
                        description=JD)
        store.write_board(conn, db.board(conn, f"{ats}:{key}"), FetchResult([item], 200, n_raw=1), "ok", run, NOW,
                          ["fam:ml_engineer"], 5)


class VendorReportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = mock.patch.object(config, "DB_PATH", Path(self.tmp.name) / "t.db")
        self.patch.start()
        self.conn = db.connect()
        seed(self.conn)
        with self.conn:
            self.conn.execute("UPDATE boards SET active = 0, dropped = 'robots: api disallows /', "
                              "dropped_at = '2026-10-05T00:00:00Z' WHERE board = 'smartrecruiters:Gone'")
        self.profile = config.profile("alex")
        fits.refresh(self.conn, self.profile, NOW)

    def tearDown(self):
        self.conn.close()
        self.patch.stop()
        self.tmp.cleanup()

    def test_coverage_counts_only_active_boards(self):
        cov = vendor_report.coverage(self.conn, self.profile)
        self.assertEqual(set(cov), {"lever"})
        self.assertEqual((cov["lever"]["jobs_with_jd"], cov["lever"]["scored_all"], cov["lever"]["no_keywords"]),
                         (1, 1, 0))
        self.assertTrue(cov["lever"]["label"].startswith("Estimated Lever match"))

    def test_a_jd_naming_no_tracked_skill_is_counted_not_dropped(self):
        with self.conn:
            self.conn.execute("UPDATE fits SET terms = '[]', jd_state = 'thin' WHERE uid = 'lever:acme:1'")
        cov = vendor_report.coverage(self.conn, self.profile)["lever"]
        self.assertEqual((cov["jobs_with_jd"], cov["scored_all"], cov["no_keywords"]), (1, 0, 1))

    def test_evidence_falls_back_to_a_robots_dropped_board_and_names_vendors_without_jobs(self):
        text = "\n".join(vendor_report.evidence(self.conn, self.profile))
        self.assertIn("# lever\n## Acme · Machine Learning Engineer", text)
        self.assertIn("# smartrecruiters\n## Gone Inc · Machine Learning Engineer", text)
        self.assertIn("robots.txt dropped the board on 2026-10-05", text)
        self.assertIn("= Estimated SmartRecruiters match", text)
        self.assertIn("- jobvite: no open title-matched job", text)
        self.assertIn("- phenom: Phenom boards are fetched", text)
        self.assertNotIn("- lever:", text)


if __name__ == "__main__":
    unittest.main()
