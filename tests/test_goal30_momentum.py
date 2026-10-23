"""GOAL30-app §1 #2, #3: the job page and the Applied / Resumes boards ask for the hiring momentum of a few companies.
The query was forced onto jobs_fam (families first), so for ONE company it read all 1.2 M index entries: ~200 ms a job
page on the live DB, up to 0.9 s. jobs_co_fam starts with the company, so those asks read only that company's rows,
from the index alone. The numbers are the same (test_goal14_resumes checks the aggregate for some companies)."""
from job_hunt_agent import config, db, ranking
from tests.test_goal13_resumes import NOW, PID, ResumesTabCase


class MomentumByCompanyTest(ResumesTabCase):
    def setUp(self):
        super().setUp()
        ranking._MOMENTUM.clear()

    def _plan_of_momentum(self, companies):
        seen = []
        self.conn.set_trace_callback(seen.append)
        try:
            ranking.momentum(self.conn, tuple(config.profile(PID)["families"]), NOW, companies=companies)
        finally:
            self.conn.set_trace_callback(None)
        sql = next(s for s in seen if "sum(j.first_posted_at" in s)
        return " ".join(r[3] for r in self.conn.execute("EXPLAIN QUERY PLAN " + sql))

    def test_a_few_companies_are_searched_in_the_company_index(self):
        plan = self._plan_of_momentum(("Acme", "Nobody Inc"))
        self.assertIn("SEARCH j USING COVERING INDEX jobs_co_fam (company=?", plan)
        self.assertNotIn("SCAN j", plan)

    def test_every_company_still_reads_the_family_index(self):
        self.assertIn("COVERING INDEX jobs_fam", self._plan_of_momentum(None))

    def test_the_health_drift_check_reads_only_an_index(self):
        from job_hunt_agent import health
        plan = " ".join(r[3] for r in self.conn.execute("EXPLAIN QUERY PLAN " + health.DRIFT_SQL, ("2026-10-19", "2026-10-12")))
        # macOS's SQLite reads it as a covering index; SQLite 3.45 (Ubuntu 24.04) still searches jobs_seen but does
        # not count its "description IS NOT NULL" column as covering. Either way it never scans the table.
        self.assertRegex(plan, r"USING (COVERING )?INDEX jobs_seen")
        self.assertNotIn("SCAN jobs", plan)

    def test_stats_posting_dates_read_only_an_index(self):
        seen = []
        self.conn.set_trace_callback(seen.append)
        try:
            from job_hunt_agent import stats
            stats._postings(self.conn, [{"id": 1, "company": "Acme", "role_title": "MLE", "first_posted_at": None,
                                         "applied_on": "2026-10-20"}])
        finally:
            self.conn.set_trace_callback(None)
        sql = next(s for s in seen if "SELECT company, title, first_posted_at" in s)
        plan = " ".join(r[3] for r in self.conn.execute("EXPLAIN QUERY PLAN " + sql))
        self.assertIn("COVERING INDEX jobs_co_fam", plan)

    def test_the_feed_fingerprint_reads_only_indexes(self):
        # every job list asks it first; fits rows carry the terms blobs, applications are wide (~150 ms cold, 12 warm)
        from job_hunt_agent import feedcache, feedback
        feedback.ensure(self.conn)
        plan = " ".join(r[3] for r in self.conn.execute("EXPLAIN QUERY PLAN " + feedcache.FINGERPRINT_SQL, (PID,) * 4))
        self.assertIn("SEARCH fits USING COVERING INDEX fits_at", plan)
        self.assertEqual(plan.count("SEARCH applications USING COVERING INDEX apps_at"), 2)

    def test_the_pending_fit_count_reads_only_open_matched_jobs_from_an_index(self):
        # the header's count every 5 min per person read all 1.1 M open rows, and content_hash sits behind the JD blob
        # (10 s cold); 15 k open jobs have a family, and a small partial index holds just those
        from job_hunt_agent import fits
        sql, params = fits._stale_where(fits.profile_inputs(config.profile(PID)))
        plan = " ".join(r[3] for r in self.conn.execute("EXPLAIN QUERY PLAN SELECT count(*) " + sql, params))
        self.assertIn("COVERING INDEX jobs_open_fit", plan)
        self.assertNotIn("jobs_open (closed_at", plan)

    def test_reads_by_person_keep_their_old_order(self):
        # many applications reads have no ORDER BY (a job page's earlier applications break same-day ties by it)
        from job_hunt_agent import api, trackapi
        ids = [trackapi.create(self.conn, PID, {"company": "Acme", "role_title": f"Role {n}"})["id"] for n in range(3)]
        for n, app in enumerate(ids):                   # the newest row was changed first
            self.conn.execute("UPDATE applications SET status = 'applied', applied_on = '2026-10-15', updated_at = ? "
                              "WHERE id = ?", (f"2026-10-1{9 - n}T00:00:00Z", app))
        self.assertEqual([e["role_title"] for e in api._earlier(self.conn, PID, "Acme", None)][-3:],
                         ["Role 0", "Role 1", "Role 2"])
        got = [r[0] for r in self.conn.execute("SELECT id FROM applications WHERE profile = ?", (PID,))]
        self.assertEqual(got, sorted(got))
        self.assertTrue(set(ids) <= set(got))

    def test_the_index_comes_with_the_schema(self):
        self.assertGreaterEqual(db.SCHEMA_VERSION, 35)
        self.assertTrue(self.conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'jobs_co_fam'").fetchone())


if __name__ == "__main__":
    import unittest
    unittest.main()
