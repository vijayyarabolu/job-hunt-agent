"""strict years caps. A JD asking more than the person's cap is too senior; exactly their years is a full
match; no stated years stays visible (Senior titles too, user decision 2026-10-07); the card says what was read;
each person sets their own cap in Settings (profile.yaml)."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from job_hunt_agent import api, config, feedcache, profile_settings, score
from tests.test_score import NOW, fit_row, job

STRICT = score.Ctx(now=NOW, profile_id="alex", years=4, known=frozenset({"Python"}), has_baseline=True, max_years=4)
JUNIOR = score.Ctx(now=NOW, profile_id="jordan", years=2, known=frozenset(), has_baseline=True, max_years=2)
PROFILE = """name: Alex Rivera
families: [ml_engineer]
years: 4
# a JD whose minimum required years exceed this is hidden
max_required_years: 6
contact: {email: x@example.com}
"""


class StrictCapTest(unittest.TestCase):
    def test_a_jd_above_the_cap_is_too_senior(self):
        for asks in (5, 6):
            result = score.final(job(min_years=asks), fit_row(), STRICT)
            self.assertEqual(result.state, "too_senior")
            self.assertIn(f"Too senior · {asks}+ yrs", result.chips)
        self.assertEqual(score.final(job(min_years=3), fit_row(), JUNIOR).state, "too_senior")

    def test_exactly_your_years_is_a_full_match_with_no_penalty(self):
        at_years = score.final(job(min_years=4), fit_row(), STRICT)
        below = score.final(job(min_years=3), fit_row(), STRICT)
        self.assertEqual(at_years.state, "ok")
        self.assertEqual(at_years.parts["S"], 1.0)
        self.assertEqual(at_years.worth, below.worth)
        self.assertNotIn("4+ yrs", at_years.chips)
        plain = job(min_years=2, title="Machine Learning Engineer")       # no "Senior title" penalty mixed in
        self.assertEqual(score.final(plain, fit_row(), JUNIOR).parts["S"], 1.0)

    def test_a_raised_cap_still_penalizes_a_jd_at_the_cap_above_your_years(self):
        loose = score.Ctx(now=NOW, profile_id="alex", years=4, known=frozenset({"Python"}), has_baseline=True,
                          max_years=6)
        at_cap = score.final(job(min_years=6), fit_row(), loose)
        self.assertEqual(at_cap.state, "ok")
        self.assertLess(at_cap.parts["S"], 1.0)

    def test_no_stated_years_stays_visible_even_with_a_senior_title(self):
        for title in ("Senior Machine Learning Engineer", "Machine Learning Engineer"):
            for ctx in (STRICT, JUNIOR):
                result = score.final(job(title=title, min_years=None), fit_row(), ctx)
                self.assertEqual(result.state, "ok", (title, ctx.profile_id))
                self.assertFalse(any(c.startswith("Too senior") for c in result.chips))


class CardLabelTest(unittest.TestCase):
    def test_the_card_carries_the_years_read_from_the_jd(self):
        row = {**job(min_years=3), "url": "https://jobs.lever.co/acme/1"}
        self.assertEqual(api.card(row, score.final(row, fit_row(), STRICT))["asks_years"], 3)
        none = {**row, "min_years": None}
        self.assertIsNone(api.card(none, score.final(none, fit_row(), STRICT))["asks_years"])


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "alex").mkdir()
        self.path = root / "alex" / "profile.yaml"
        self.path.write_text(PROFILE)
        self.patch = mock.patch.object(config, "PROFILES", root)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_the_cap_is_saved_to_profile_yaml_keeping_its_comments(self):
        out = profile_settings.set_years_cap("alex", 4)
        self.assertEqual(out, {"years": 4, "max_required_years": 4, "feed_min_match": 0, "cold_after_days": 30,
                               "mail_daily_at": "00:15", "past_employers": [], "past_employers_default": []})                          # Cold days, mail interval   # Settings also shows the floor
        text = self.path.read_text()
        self.assertIn("max_required_years: 4\n", text)
        self.assertIn("# a JD whose minimum required years exceed this is hidden", text)
        self.assertEqual(config.profile("alex")["max_required_years"], 4)
        self.assertEqual(profile_settings.get("alex"), {"years": 4, "max_required_years": 4, "feed_min_match": 0,
                                                         "cold_after_days": 30, "mail_daily_at": "00:15",
                                                         "past_employers": [], "past_employers_default": []})   # Settings also shows the floor

    def test_a_missing_cap_line_is_added(self):
        self.path.write_text(PROFILE.replace("max_required_years: 6\n", ""))
        profile_settings.set_years_cap("alex", 3)
        self.assertEqual(config.profile("alex")["max_required_years"], 3)

    def test_bad_caps_are_refused(self):
        for bad in (-1, 31, "four", None, 2.5, True):
            with self.assertRaises(ValueError):
                profile_settings.set_years_cap("alex", bad)
        self.assertIn("max_required_years: 6\n", self.path.read_text())

    def test_the_kept_feed_is_keyed_on_the_cap(self):
        a = feedcache.key_of({"id": "alex", "max_required_years": 6, "years": 4}, False, ("ok",))
        b = feedcache.key_of({"id": "alex", "max_required_years": 4, "years": 4}, False, ("ok",))
        self.assertNotEqual(a, b)


if __name__ == "__main__":
    unittest.main()
