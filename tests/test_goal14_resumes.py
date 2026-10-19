"""the Resumes tab paints fast. Its cards rank only the jobs shown, so the hiring-momentum aggregate is
computed for those jobs' companies only (the same numbers), never over every job in the DB."""
from job_hunt_agent import config, ranking
from tests.test_goal13_resumes import NOW, PID, ResumesTabCase
from tests.test_goal8_flow import seed_job


class MomentumForShownJobsTest(ResumesTabCase):
    def setUp(self):
        super().setUp()
        ranking._MOMENTUM.clear()
        with self.conn:
            self.conn.execute("UPDATE boards SET baselined_at = '2026-01-01T00:00:00Z'")
            self.conn.execute("UPDATE jobs SET seed = 0")

    def test_momentum_for_some_companies_equals_the_full_aggregate_for_them(self):
        families = tuple(config.profile(PID)["families"])
        full = ranking.momentum(self.conn, families, NOW)
        ranking._MOMENTUM.clear()
        some = ranking.momentum(self.conn, families, NOW, companies=("Acme", "Nobody Inc"))
        self.assertEqual(some, {k: v for k, v in full.items() if k in ("Acme", "Nobody Inc")})
        self.assertIn("Acme", some)

    def test_the_resumes_board_never_aggregates_over_every_job(self):
        seed_job(self.conn, "9")
        self.add(self.uids[0], "ready", finished_at="2026-10-08T15:00:00Z")
        seen = []
        self.conn.set_trace_callback(seen.append)
        try:
            got = self.board("ready")
        finally:
            self.conn.set_trace_callback(None)
        self.assertEqual([j["uid"] for j in got["jobs"]], [self.uids[0]])
        aggregates = [s for s in seen if "GROUP BY j.company" in s]
        self.assertTrue(aggregates)
        self.assertTrue(all("j.company IN" in s for s in aggregates), aggregates)

    def test_the_job_detail_never_aggregates_over_every_job(self):
        # the company panel asked for every company's momentum; opening a job took 1-4 s every 10 minutes
        from job_hunt_agent import api
        seen = []
        self.conn.set_trace_callback(seen.append)
        try:
            detail = api.job_detail(self.conn, PID, self.uids[0], NOW)
        finally:
            self.conn.set_trace_callback(None)
        self.assertIn("company_panel", detail)
        aggregates = [s for s in seen if "GROUP BY j.company" in s]
        self.assertTrue(aggregates)
        self.assertTrue(all("j.company IN" in s for s in aggregates), aggregates)
