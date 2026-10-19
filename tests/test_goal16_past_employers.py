"""jobs at a person's past employers are never shown. The list defaults to the companies of the base
resume's Work Experience, is editable in Settings (profile.yaml), matches subsidiaries and legal names, drops the job
at scoring time (state past_employer), and a board whose company is a past employer of every profile is not fetched.
Real board names from the DB: 'Deloitte' (workday:deloitte/wd1/Global), 'Initech Global' (workday:initech/...)."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from job_hunt_agent import config, feedcache, past_employers, profile_settings, registry, score
from tests.test_score import NOW, fit_row, job

ALEX = """name: Alex Rivera
families: [ml_engineer]
years: 4
"""
JORDAN = """name: Jordan Lee
families: [ai_engineer]
years: 3
"""


def ctx(*names: str) -> score.Ctx:
    return score.Ctx(now=NOW, profile_id="alex", years=4, known=frozenset({"Python"}), has_baseline=True,
                     past=past_employers.keys(names))


class MatchTest(unittest.TestCase):
    def test_brand_aliases_and_subsidiaries_match(self):
        past = past_employers.keys(["Deloitte", "IBM", "Globex Properties"])
        for company in ("Deloitte", "Deloitte Consulting", "Deloitte Digital",
                        "DELOITTE LLP", "IBM Consulting", "IBM Global Services Inc.", "International Business Machines",
                        "Globex Properties"):
            self.assertTrue(past_employers.matches(company, past), company)
        for company in ("Stater Bros", "Street Smart", "Charles Schwab", "Globex Mechanical", "Hulu", ""):
            self.assertFalse(past_employers.matches(company, past), company)

    def test_accenture_and_pwc_variants_match(self):
        past = past_employers.keys(["Accenture", "PwC"])
        for company in ("Accenture", "Accenture Federal Services", "Avanade", "PricewaterhouseCoopers",
                        "PwC Advisory"):
            self.assertTrue(past_employers.matches(company, past), company)
        self.assertFalse(past_employers.matches("Spotify", past))

    def test_a_resume_company_loses_its_place_suffix(self):
        self.assertEqual(past_employers.clean("Globex Properties, USA."), "Globex Properties")
        self.assertEqual(past_employers.clean("IBM, India"), "IBM")
        self.assertEqual(past_employers.clean("Initech Global, USA"), "Initech Global")


class ScoreTest(unittest.TestCase):
    def test_a_job_at_a_past_employer_is_never_ok(self):
        result = score.final(job(company="Deloitte Consulting"), fit_row(), ctx("Deloitte"))
        self.assertEqual(result.state, "past_employer")
        self.assertFalse(result.push)

    def test_the_same_job_is_shown_to_someone_who_never_worked_there(self):
        self.assertEqual(score.final(job(company="Deloitte"), fit_row(), ctx("Initech Global")).state, "ok")
        self.assertEqual(score.final(job(company="Deloitte"), fit_row(), ctx()).state, "ok")


class ProfileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        for pid, text, companies in (("alex", ALEX, ["Globex Properties, USA.", "Deloitte, USA.", "IBM, India"]),
                                     ("jordan", JORDAN, ["Initech Global, USA", "Hooli, India"])):
            (root / pid).mkdir()
            (root / pid / "profile.yaml").write_text(text)
            (root / pid / "baseline.json").write_text(json.dumps(
                {"experience": [{"company": c, "title": "AI Engineer"} for c in companies]}))
        self.patch = mock.patch.object(config, "PROFILES", root)
        self.patch.start()
        self.root = root

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_the_default_is_the_resume_work_experience(self):
        self.assertEqual(past_employers.names(config.profile("alex")), ("Globex Properties", "Deloitte", "IBM"))
        self.assertEqual(past_employers.names(config.profile("jordan")), ("Initech Global", "Hooli"))

    def test_settings_save_the_list_and_keep_the_file(self):
        out = profile_settings.set_past_employers("alex", "Deloitte\nIBM\n\nAcme \\ Labs")
        self.assertEqual(out["past_employers"], ["Deloitte", "IBM", "Acme \\ Labs"])
        self.assertEqual(out["past_employers_default"], ["Globex Properties", "Deloitte", "IBM"])
        self.assertEqual(config.profile("alex")["past_employers"], ["Deloitte", "IBM", "Acme \\ Labs"])
        self.assertIn("name: Alex Rivera\n", (self.root / "alex" / "profile.yaml").read_text())
        profile_settings.set_past_employers("alex", [])
        self.assertEqual(past_employers.names(config.profile("alex")), ())      # an empty list hides nobody

    def test_bad_lists_are_refused(self):
        for bad in (None, 3, [1, 2], ["x" * 81], [f"C{i}" for i in range(31)]):
            with self.assertRaises(ValueError):
                profile_settings.set_past_employers("alex", bad)

    def test_the_kept_feed_is_keyed_on_the_list(self):
        a = feedcache.key_of({"id": "alex", "past_employers": ["Deloitte"]}, False, ("ok",))
        b = feedcache.key_of({"id": "alex", "past_employers": []}, False, ("ok",))
        self.assertNotEqual(a, b)

    def test_only_a_board_every_profile_worked_at_is_skipped(self):
        self.assertFalse(past_employers.skip_board("Deloitte", config.profiles()))   # Jordan may see it
        profile_settings.set_past_employers("jordan", ["Initech Global", "Hooli", "Deloitte"])
        profiles = config.profiles()
        self.assertTrue(past_employers.skip_board("Deloitte", profiles))
        self.assertFalse(past_employers.skip_board("Anthropic", profiles))
        curated = [{"name": "Deloitte", "ats": "workday", "token": "deloitte", "wd": "wd1", "site": "Global"},
                   {"name": "Anthropic", "ats": "greenhouse", "token": "anthropic"}]
        with mock.patch.object(config, "companies_raw", return_value=curated), \
                mock.patch.object(registry, "discovered_raw", return_value=[]):
            self.assertEqual([b["board"] for b in registry.boards()], ["greenhouse:anthropic"])


if __name__ == "__main__":
    unittest.main()
