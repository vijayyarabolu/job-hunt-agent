"""Stats showed "Loading…" for ~1.6 s: the board segments and the posting dates matched by company + title
read the 1M-job table each time. They are kept per person until a fetch run or the applications change."""
import unittest
from unittest import mock

from job_hunt_agent import db, stats, trackapi
from tests import test_goal21_lists as lists


class Goal21StatsTest(unittest.TestCase):
    setUp, tearDown = lists.Goal21ListTest.setUp, lists.Goal21ListTest.tearDown    # five Acme jobs, one run

    def test_the_slow_part_is_kept_until_a_run_or_an_application_changes(self):
        trackapi.create(self.conn, "tester", {"company": "Acme", "role_title": "ML Engineer", "status": "applied",
                                              "date_applied": "10/12/2026"})
        stats._SLOW.clear()
        with mock.patch.object(stats, "_postings", wraps=stats._postings) as postings:
            first = stats.report(self.conn, "tester")
            again = stats.report(self.conn, "tester")
            self.assertEqual(postings.call_count, 1)                    # the second report reused it
            self.assertEqual(first["applications"], again["applications"])
            trackapi.create(self.conn, "tester", {"company": "Beta", "role_title": "Data Scientist", "status": "applied",
                                                  "date_applied": "10/12/2026"})
            self.assertEqual(stats.report(self.conn, "tester")["applications"], first["applications"] + 1)
            self.assertEqual(postings.call_count, 2)                    # a new application: read again
            db.start_run(self.conn, "test", lists.NOW)
            stats.report(self.conn, "tester")
            self.assertEqual(postings.call_count, 3)                    # a new fetch run: read again


if __name__ == "__main__":
    unittest.main()
