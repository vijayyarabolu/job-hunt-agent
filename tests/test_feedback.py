"""Like / Not interested feedback: reasons become bounded, named ranking multipliers for that profile only;
the newest feedback per job wins; the table is created idempotently; score.final applies the multiplier."""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from job_hunt_agent import config, db, feedback, score
from tests.test_server import seed_job

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
FAMS = ("ai_engineer", "ml_engineer", "data_engineer")


def job(**over) -> dict:
    return {"company": "Acme", "title": "Machine Learning Engineer", "location": "Austin, TX",
            "families": ",ml_engineer,", **over}


def entry(kind, reason=None, company="acme", family="ml_engineer", states="TX") -> dict:
    return {"kind": kind, "reason": reason, "company": company, "family": family, "states": states}


def mult(prefs, **over) -> float:
    out = feedback.factors(prefs, job(**over), FAMS)
    return next(iter(out.values())) if out else 1.0


class FactorsTest(unittest.TestCase):
    def test_company_pass_demotes_that_company_only(self):
        prefs = feedback.prefs_of([entry("pass", "company")])
        self.assertEqual(mult(prefs), feedback.COMPANY_PASS)
        self.assertEqual(mult(prefs, company="Other Co"), 1.0)

    def test_location_pass_needs_every_state_passed(self):
        prefs = feedback.prefs_of([entry("pass", "location", states="TX")])
        self.assertEqual(mult(prefs, company="Other"), feedback.LOCATION_PASS)
        self.assertEqual(mult(prefs, company="Other", location="Austin, TX; Seattle, WA"), 1.0)
        self.assertEqual(mult(prefs, company="Other", location="Remote"), 1.0)

    def test_wrong_role_demotes_the_family_and_compounds_to_the_floor(self):
        one = feedback.prefs_of([entry("pass", "wrong_role")])
        self.assertEqual(mult(one, company="Other"), feedback.ROLE_PASS)
        many = feedback.prefs_of([entry("pass", "wrong_role")] * 10)
        self.assertEqual(mult(many, company="Other"), feedback.REASON_FLOOR)
        self.assertEqual(mult(many, company="Other", families=",data_engineer,"), 1.0)

    def test_too_senior_demotes_senior_titles_only(self):
        prefs = feedback.prefs_of([entry("pass", "too_senior")])
        self.assertEqual(mult(prefs, company="Other", title="Senior ML Engineer"), feedback.SENIOR_PASS)
        self.assertEqual(mult(prefs, company="Other"), 1.0)

    def test_like_boosts_company_and_family_within_the_ceiling(self):
        prefs = feedback.prefs_of([entry("like")])
        self.assertAlmostEqual(mult(prefs), feedback.LIKE_COMPANY * (1 + feedback.LIKE_FAMILY_STEP), places=4)
        self.assertAlmostEqual(mult(prefs, company="Other"), 1 + feedback.LIKE_FAMILY_STEP)
        lots = feedback.prefs_of([entry("like", company=f"c{i}") for i in range(20)] + [entry("like")])
        self.assertLessEqual(mult(lots), feedback.FEEDBACK_CEIL)

    def test_combined_multiplier_never_drops_below_the_floor(self):
        rows = [entry("pass", "company"), entry("pass", "location")] + [entry("pass", "wrong_role")] * 5
        rows += [entry("pass", "too_senior")] * 5
        prefs = feedback.prefs_of(rows)
        self.assertEqual(mult(prefs, title="Senior ML Engineer"), feedback.FEEDBACK_FLOOR)

    def test_no_feedback_no_factor(self):
        self.assertEqual(feedback.factors(None, job(), FAMS), {})
        self.assertEqual(feedback.factors(feedback.Prefs(), job(), FAMS), {})

    def test_constants_are_bounded(self):
        for value in (feedback.COMPANY_PASS, feedback.LOCATION_PASS, feedback.ROLE_PASS, feedback.SENIOR_PASS,
                      feedback.REASON_FLOOR, feedback.FEEDBACK_FLOOR):
            self.assertTrue(0 < value < 1, value)
        self.assertTrue(1 < feedback.LIKE_COMPANY <= feedback.FEEDBACK_CEIL < 1.5)
        self.assertLessEqual(feedback.LIKE_FAMILY_MAX, feedback.FEEDBACK_CEIL)


class ScoreTest(unittest.TestCase):
    def test_score_applies_feedback_and_caps_at_100(self):
        from tests.test_score import CTX, fit_row, job as score_job
        base = score.final(score_job(), fit_row(), CTX)
        prefs = feedback.prefs_of([entry("pass", "company")])
        demoted = score.final(score_job(families=",ml_engineer,"), fit_row(),
                              score.Ctx(**{**CTX.__dict__, "prefs": prefs, "families": FAMS}))
        self.assertLess(demoted.worth, base.worth)
        self.assertTrue(any(c.startswith("Ranked lower: you passed on") for c in demoted.chips), demoted.chips)
        parts = {"F": 1.0, "R": 1.0, "Q": 1.0, "S": 1.0}
        self.assertEqual(score._worth(parts, {"Your feedback: liked company": 1.15}), 100)


class StoreTest(unittest.TestCase):
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

    def test_record_load_newest_wins_and_profiles_are_separate(self):
        feedback.record(self.conn, "alex", self.uid, "pass", "company", NOW, FAMS)
        self.assertEqual(feedback.load(self.conn, "alex").passed_companies, frozenset({"acme"}))
        self.assertEqual(feedback.load(self.conn, "jordan"), feedback.Prefs())
        feedback.record(self.conn, "alex", self.uid, "clear", None, NOW, FAMS)
        self.assertEqual(feedback.load(self.conn, "alex"), feedback.Prefs())

    def test_record_keeps_state_and_family(self):
        out = feedback.record(self.conn, "alex", self.uid, "pass", "location", NOW, FAMS)
        self.assertEqual((out["states"], out["family"]), ("TX", "ml_engineer"))

    def test_validation(self):
        with self.assertRaises(ValueError):
            feedback.record(self.conn, "alex", self.uid, "pass", None, NOW, FAMS)
        with self.assertRaises(ValueError):
            feedback.record(self.conn, "alex", self.uid, "like", "company", NOW, FAMS)
        with self.assertRaises(ValueError):
            feedback.record(self.conn, "alex", self.uid, "love", None, NOW, FAMS)
        with self.assertRaises(KeyError):
            feedback.record(self.conn, "alex", "lever:acme:nope", "like", None, NOW, FAMS)

    def test_ensure_is_idempotent(self):
        feedback.ensure(self.conn)
        feedback.ensure(self.conn)
        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(feedback)")]
        self.assertEqual(cols, ["id", "profile", "uid", "at", "kind", "reason", "company", "family", "states", "note"])


if __name__ == "__main__":
    unittest.main()
