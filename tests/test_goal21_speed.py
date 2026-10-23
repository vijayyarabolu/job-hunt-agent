"""GOAL21 W17: a cold first page took ~26 s on the 1M-job DB because the ranking and its momentum read every job row.
The jobs_fam index (schema 27) covers the family filter, so ranking finds the open matched jobs in the index and reads
only those rows (the same jobs as the old single scan, whose order SQLite never promised either)."""
import sqlite3
import unittest
from unittest import mock

from job_hunt_agent import config, db, ranking
from job_hunt_agent.fits import family_filter
from tests import test_goal21_lists as lists     # a module, so its tests are not collected twice

NOW = lists.NOW


def _old_rows(conn, profile):
    """The ranking's rows as the old single query read them (the reference)."""
    where, params = family_filter(tuple(profile["families"]))
    return conn.execute(
        f"SELECT {ranking._JOB_COLUMNS}, f.jd_state, f.terms, f.dealbreaker, f.genai FROM jobs j "
        "JOIN boards b ON b.board = j.board LEFT JOIN fits f ON f.profile = ? AND f.uid = j.uid "
        f"WHERE j.closed_at IS NULL AND b.active = 1 AND b.review = 0 AND {where}", [profile["id"], *params]).fetchall()


class Goal21SpeedTest(unittest.TestCase):
    setUp, tearDown = lists.Goal21ListTest.setUp, lists.Goal21ListTest.tearDown    # the same five Acme jobs

    def test_new_and_upgraded_db_have_the_covering_index(self):
        self.assertTrue(self.conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'jobs_fam'").fetchone())
        self.conn.execute("DROP INDEX jobs_fam")
        self.conn.execute("PRAGMA user_version = 26")
        self.conn.close()
        self.conn = db.connect()
        self.assertTrue(self.conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'jobs_fam'").fetchone())
        self.assertGreaterEqual(self.conn.execute("PRAGMA user_version").fetchone()[0], 27)

    def test_rank_reads_the_same_jobs_in_small_batches(self):
        profile = config.profile("tester")
        self.conn.execute("UPDATE jobs SET closed_at = ? WHERE uid = ?", [db.iso(NOW), self.uid["5"]])
        with mock.patch.object(ranking, "SQL_BATCH", 2):
            got = ranking._rows(self.conn, profile, None, None)
        want = _old_rows(self.conn, profile)
        self.assertEqual(sorted(tuple(r) for r in got), sorted(tuple(r) for r in want))
        self.assertEqual(len(got), 4)                       # the closed job is left out
        self.conn.execute("UPDATE boards SET active = 0")
        self.assertEqual(ranking._rows(self.conn, profile, None, None), [])

    def test_ranking_queries_use_the_index(self):
        where, params = family_filter(tuple(config.profile("tester")["families"]))
        plan = " ".join(r[3] for r in self.conn.execute(
            f"EXPLAIN QUERY PLAN SELECT j.rowid FROM jobs j INDEXED BY jobs_fam WHERE j.closed_at IS NULL AND {where}", params))
        self.assertIn("COVERING INDEX jobs_fam", plan)
        plan = " ".join(r[3] for r in self.conn.execute(
            f"EXPLAIN QUERY PLAN SELECT j.company FROM jobs j INDEXED BY jobs_fam WHERE {where} GROUP BY j.company", params))
        self.assertIn("COVERING INDEX jobs_fam", plan)
        self.assertIsInstance(ranking._momentum(self.conn, tuple(config.profile("tester")["families"]), NOW, None), dict)

    def test_one_job_and_a_uid_list_still_rank(self):
        profile = config.profile("tester")
        self.assertEqual([r["uid"] for r in ranking._rows(self.conn, profile, self.uid["1"], None)], [self.uid["1"]])
        self.assertEqual({r["uid"] for r in ranking._rows(self.conn, profile, None, [self.uid["2"], self.uid["3"]])},
                         {self.uid["2"], self.uid["3"]})


if __name__ == "__main__":
    unittest.main()
