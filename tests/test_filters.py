"""Feed filters: query-string parsing (bad values -> ValueError -> HTTP 400) and the per-job predicate."""
import unittest
from datetime import datetime, timedelta, timezone

from job_hunt_agent import filters

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)


def iso(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def row(**over) -> dict:
    base = {"title": "ML Engineer", "location": "Austin, TX", "families": ",ml_engineer,", "workplace": "hybrid",
            "precision": "exact", "first_posted_at": iso(30), "first_seen": iso(30), "seed": 0, "min_years": 3,
            "salary_min": 120000, "salary_max": 160000}
    return {**base, **over}


def keep(f, r=None, state="ok", employment=None, applied=False, pct=80):
    return filters.keep(f, r or row(), state, employment, applied, pct, NOW)


class ParseTest(unittest.TestCase):
    def test_empty_query_is_no_filter(self):
        self.assertEqual(filters.parse({}), filters.Filters())

    def test_all_fields(self):
        f = filters.parse({"family": "ml_engineer", "state": "tx", "workplace": "remote", "posted": "3d",
                           "min_match": "70", "salary": "150000", "level": "3to5", "senior": "1",
                           "hide_applied": "true", "employment": "contract"})
        self.assertEqual(f, filters.Filters("ml_engineer", "TX", "remote", 72, 70, 150000, "3to5", True, True,
                                            "contract"))

    def test_bad_values_raise(self):
        bad = [{"family": "chef"}, {"state": "ZZ"}, {"workplace": "moon"}, {"posted": "1y"}, {"min_match": "101"},
               {"min_match": "abc"}, {"salary": "-5"}, {"level": "guru"}, {"employment": "gig"}]
        for args in bad:
            with self.assertRaises(ValueError, msg=args):
                filters.parse(args)

    def test_job_boards_any_posted_and_paging(self):
        # several boards at once (prefixes from the DB, so a new system needs no code), 30d, a page offset
        f = filters.parse({"board": "workday, oracle_recruiting,workday", "posted": "30d", "offset": "300"})
        self.assertEqual((f.boards, f.posted_hours, f.offset), (("oracle_recruiting", "workday"), 720, 300))
        self.assertEqual(filters.parse({"board": "", "posted": ""}).boards, ())
        for bad in ({"board": "work day"}, {"board": "x:y"}, {"offset": "-1"}):
            with self.assertRaises(ValueError, msg=bad):
                filters.parse(bad)

    def test_board_prefix_and_labels(self):
        self.assertEqual(filters.board_of({"board": "icims_classic:acme"}), "icims_classic")
        self.assertEqual(filters.board_label("icims"), "iCIMS / Jibe")
        self.assertEqual(filters.board_label("brand_new_ats"), "Brand New Ats")

    def test_unchecked_booleans_are_false(self):
        f = filters.parse({"senior": "0", "hide_applied": ""})
        self.assertFalse(f.show_senior or f.hide_applied)


class KeepTest(unittest.TestCase):
    def test_too_senior_hidden_unless_shown(self):
        self.assertFalse(keep(filters.Filters(), state="too_senior"))
        self.assertTrue(keep(filters.Filters(show_senior=True), state="too_senior"))

    def test_location_workplace_family(self):
        self.assertTrue(keep(filters.Filters(state="TX")))
        self.assertFalse(keep(filters.Filters(state="WA")))
        self.assertFalse(keep(filters.Filters(workplace="remote")))
        self.assertTrue(keep(filters.Filters(workplace="remote"), row(workplace=None, location="Remote, US")))
        self.assertFalse(keep(filters.Filters(family="data_engineer")))

    def test_posted_window(self):
        self.assertFalse(keep(filters.Filters(posted_hours=24)))
        self.assertTrue(keep(filters.Filters(posted_hours=72)))
        self.assertFalse(keep(filters.Filters(posted_hours=72), row(seed=1)))

    def test_min_match_salary_level_applied_employment(self):
        self.assertFalse(keep(filters.Filters(min_match=90)))
        self.assertFalse(keep(filters.Filters(min_match=50), pct=None))
        self.assertFalse(keep(filters.Filters(salary_floor=200000)))
        self.assertTrue(keep(filters.Filters(salary_floor=200000), row(salary_min=None, salary_max=None)))
        self.assertTrue(keep(filters.Filters(level="3to5")))
        self.assertFalse(keep(filters.Filters(level="6plus")))
        self.assertTrue(keep(filters.Filters(level="le2"), row(min_years=None)))
        self.assertFalse(keep(filters.Filters(hide_applied=True), applied=True))
        self.assertTrue(keep(filters.Filters(employment="full_time")))
        self.assertFalse(keep(filters.Filters(employment="contract")))
        self.assertTrue(keep(filters.Filters(employment="contract"), employment="contract"))


if __name__ == "__main__":
    unittest.main()
