"""fresh.observe / is_fresh (decision 001 §4 incl. judge conditions C1, C3) and guard.classify."""
import random
import unittest
from datetime import datetime, timedelta, timezone

from job_hunt_agent import fresh, guard

NOW = datetime(2026, 10, 2, 15, 0, tzinfo=timezone.utc)


def item(ats="greenhouse", posted="2026-10-02T10:00:00Z", precision="exact", capped=False):
    return {"ats": ats, "posted_at": posted, "precision": precision, "capped": capped}


def stored(patch: dict) -> dict:
    return {"closed_at": None, "stale": 0, "repost_count": 0, "bumped_at": None, **patch}


class ObserveTest(unittest.TestCase):
    def test_new_exact_job_is_fresh(self):
        patch, events = fresh.observe(None, item(), NOW, seeding=False)
        self.assertEqual(events, [("new", {})])
        self.assertTrue(fresh.is_fresh(stored(patch), NOW))

    def test_exact_job_older_than_24h_is_not_fresh(self):
        patch, _ = fresh.observe(None, item(posted="2026-10-01T14:00:00Z"), NOW, seeding=False)
        self.assertFalse(fresh.is_fresh(stored(patch), NOW))

    def test_workday_bump_30plus_then_today_is_not_fresh(self):
        day1 = NOW - timedelta(days=1)
        first, _ = fresh.observe(None, item("workday", "2026-08-31T00:00:00Z", "day", capped=True), day1, False)
        row = stored(first)
        patch, events = fresh.observe(row, item("workday", "2026-10-02T00:00:00Z", "day"), NOW, False)
        row = {**row, **patch}
        self.assertFalse(fresh.is_fresh(row, NOW))
        self.assertEqual(row["first_posted_at"], "2026-08-31T00:00:00Z")
        self.assertEqual(row["posted_at"], "2026-10-02T00:00:00Z")
        self.assertEqual(row["repost_count"], 1)
        self.assertEqual([k for k, _ in events], ["bumped"])
        self.assertGreater(fresh.age_hours(row, NOW), 24 * 30)

    def test_smartrecruiters_and_ashby_republish_are_bumps(self):
        for ats in ("smartrecruiters", "ashby"):
            first, _ = fresh.observe(None, item(ats, "2026-08-01T09:00:00Z"), NOW - timedelta(days=1), False)
            patch, events = fresh.observe(stored(first), item(ats, "2026-10-02T09:00:00Z"), NOW, False)
            with self.subTest(ats=ats):
                self.assertEqual([k for k, _ in events], ["bumped"])
                self.assertFalse(fresh.is_fresh({**stored(first), **patch}, NOW))

    def test_c1_capped_30plus_reading_never_bumps(self):
        first, _ = fresh.observe(None, item("workday", "2026-09-01T00:00:00Z", "day", capped=True),
                                 NOW - timedelta(days=3), False)
        patch, events = fresh.observe(stored(first), item("workday", "2026-09-04T00:00:00Z", "day", capped=True),
                                      NOW, False)
        self.assertEqual(events, [])
        self.assertEqual(patch["posted_at"], "2026-09-01T00:00:00Z")

    def test_day_precision_midnight_jitter_is_not_a_bump(self):
        first, _ = fresh.observe(None, item("workday", "2026-10-01T00:00:00Z", "day"), NOW - timedelta(hours=5), False)
        _, events = fresh.observe(stored(first), item("workday", "2026-10-02T00:00:00Z", "day"), NOW, False)
        self.assertEqual(events, [])

    def test_greenhouse_updated_precision_never_bumps(self):
        first, _ = fresh.observe(None, item(posted="2026-09-01T00:00:00Z", precision="updated"),
                                 NOW - timedelta(days=2), False)
        patch, events = fresh.observe(stored(first), item(posted="2026-10-02T12:00:00Z", precision="updated"),
                                      NOW, False)
        self.assertEqual(events, [])
        self.assertFalse(fresh.is_fresh({**stored(first), **patch}, NOW))   # first_seen 2 days ago

    def test_c3_null_posted_at_never_nulls_first_posted_at(self):
        first, _ = fresh.observe(None, item(posted="2026-10-02T10:00:00Z"), NOW - timedelta(hours=2), False)
        patch, _ = fresh.observe(stored(first), item(posted=None), NOW, False)
        self.assertEqual(patch["first_posted_at"], "2026-10-02T10:00:00Z")

    def test_c3_null_first_posted_at_is_not_fresh(self):
        row = stored({"precision": "exact", "first_posted_at": None, "first_seen": "2026-10-02T14:00:00Z"})
        self.assertFalse(fresh.is_fresh(row, NOW))

    def test_seed_jobs_are_never_fresh(self):
        patch, _ = fresh.observe(None, item("ashby"), NOW, seeding=True)
        self.assertEqual(patch["seed"], 1)
        self.assertFalse(fresh.is_fresh(stored(patch), NOW))
        immutable, _ = fresh.observe(None, item("lever"), NOW, seeding=True)
        self.assertEqual(immutable["seed"], 0)

    def test_an_undated_job_on_a_boards_first_run_is_backlog(self):
        # GOAL16's new Jobvite / BambooHR / JazzHR boards put months-old undated postings at the top of
        # Newest as "First seen 1h ago · be an early applicant"; their age is unknown, so they are backlog
        undated, _ = fresh.observe(None, item("jobvite", posted=None, precision="unknown"), NOW, seeding=True)
        self.assertEqual(undated["seed"], 1)
        later, _ = fresh.observe(None, item("jobvite", posted=None, precision="unknown"), NOW, seeding=False)
        self.assertEqual(later["seed"], 0)                       # a later new posting is new

    def test_future_dates_fall_back_to_unknown_precision(self):
        patch, _ = fresh.observe(None, item(posted="2026-10-05T00:00:00Z"), NOW, False)
        self.assertEqual(patch["precision"], "unknown")
        self.assertIsNone(patch["first_posted_at"])

    def test_property_first_posted_at_never_increases(self):
        rng = random.Random(7)
        for _ in range(200):
            row, when = None, NOW - timedelta(days=40)
            lowest = None
            for _ in range(8):
                posted = (when - timedelta(hours=rng.randint(0, 900))).strftime("%Y-%m-%dT%H:%M:%SZ")
                patch, _ = fresh.observe(row, item("ashby", rng.choice([posted, None])), when, False)
                row = {**stored({}), **(row or {}), **patch}
                if row["first_posted_at"] is not None:
                    self.assertTrue(lowest is None or row["first_posted_at"] <= lowest)
                    lowest = row["first_posted_at"]
                when += timedelta(hours=rng.randint(1, 72))


class GuardTest(unittest.TestCase):
    def test_classify(self):
        self.assertEqual(guard.classify(False, 0, 0, 0, 100, []), "http_error")
        self.assertEqual(guard.classify(True, 100, 0, 0, 100, []), "drift")
        self.assertEqual(guard.classify(True, 100, 90, 10, 100, []), "drift")
        self.assertEqual(guard.classify(True, 40, 40, 0, 100, []), "partial")
        self.assertEqual(guard.classify(True, 40, 40, 0, 100, ["partial", "partial"]), "ok")
        self.assertEqual(guard.classify(True, 95, 95, 0, 100, []), "ok")
        self.assertEqual(guard.classify(True, 0, 0, 0, 0, []), "ok")


if __name__ == "__main__":
    unittest.main()
