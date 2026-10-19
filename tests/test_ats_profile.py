"""GOAL4 §3 addition (decision 009): ATS-aware Copy JD + keywords. Vendor-documented facts only, each with an https
source; "not publicly documented" wherever no vendor page says it; the JD's exact wording plus both acronym forms;
application questions only where the ATS's public API exposes them (Greenhouse)."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from job_hunt_agent import ats_profile, config, db, jdcopy, store
from job_hunt_agent.ats.common import FetchResult, make_job
from job_hunt_agent.net import HttpError
from tests.test_server import NOW

GH_QUESTIONS = {
    "questions": [
        {"label": "First Name", "required": True, "fields": [{"name": "first_name", "type": "input_text"}]},
        {"label": "Resume/CV", "required": True, "fields": [{"name": "resume", "type": "input_file"}]},
        {"label": "Will you now or in the future require sponsorship?", "required": True,
         "fields": [{"name": "question_1", "type": "multi_value_single_select",
                     "values": [{"label": "Yes", "value": 1}, {"label": "No", "value": 0}]}]},
        {"label": "LinkedIn Profile", "required": False, "fields": [{"name": "question_2", "type": "input_text"}]}],
    "location_questions": [{"label": "Latitude", "required": True, "fields": [{"name": "latitude"}]}],
    "compliance": [{"type": "eeoc", "questions": []}]}


class ProfilesTest(unittest.TestCase):
    def test_the_shipped_profiles_cite_an_https_vendor_page_for_every_fact(self):
        found = ats_profile.profiles()
        self.assertLessEqual({"workday", "greenhouse", "lever", "ashby", "smartrecruiters", "amazon", "icims", "taleo",
                              "oracle_recruiting", "successfactors", "eightfold", "phenom"}, set(found))
        for ats, prof in found.items():
            for section, _ in ats_profile.SECTIONS:
                for item in prof.get(section) or []:
                    self.assertTrue(item["source"].startswith("https://"), (ats, section))

    def test_a_fact_without_an_https_source_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ats.yaml"
            path.write_text("x:\n  label: X\n  parsing:\n    - {text: guess, source: 'http://blog'}\n"
                            "  questions_api: {exposed: false, how: n, source: 'https://x'}\n")
            with self.assertRaisesRegex(config.ConfigError, "https source"):
                ats_profile.profiles(str(path))

    def test_the_box_cites_sources_and_says_not_publicly_documented_where_vendors_are_silent(self):
        lines = ats_profile.box("ashby")
        self.assertEqual(lines[0], "How Ashby handles your resume (vendor docs only; [n] = source below):")
        self.assertIn("- Keyword wording: not publicly documented", lines)
        self.assertIn("- Acronym vs spelled-out: not publicly documented", lines)
        self.assertTrue(lines[-1].startswith("Sources: [1] https://docs.ashbyhq.com/"))
        self.assertTrue(any(line.strip().endswith("[1]") for line in lines[1:-1]))

    def test_an_unknown_ats_is_not_publicly_documented(self):
        self.assertIn("not publicly documented", ats_profile.box("notavendor")[0])


class KeywordFormsTest(unittest.TestCase):
    def test_both_forms_from_the_lexicon(self):
        self.assertEqual(ats_profile.both_forms("Natural Language Processing (NLP)"), "Natural Language Processing (NLP)")
        self.assertEqual(ats_profile.both_forms("AWS"), "Amazon Web Services (AWS)")
        self.assertEqual(ats_profile.both_forms("dbt"), "Data Build Tool (dbt)")
        self.assertIsNone(ats_profile.both_forms("Python"))

    def test_the_jd_wording_is_the_postings_own_words(self):
        words = ats_profile.jd_wording("Experience with AWS SageMaker, NLP and\nPython.")
        self.assertEqual(words["SageMaker"], "AWS SageMaker")
        self.assertEqual(words["Natural Language Processing (NLP)"], "NLP")

    def test_keyword_lines_mark_have_and_missing_in_importance_order(self):
        lines = ats_profile.keyword_lines(("Python", "Natural Language Processing (NLP)"), {"Python"}, "ML Engineer",
                                          "Python and NLP")
        self.assertIn("ML Engineer resume", lines[0])
        self.assertEqual(lines[1:], ["  1. ✓ Python", '  2. ✗ Natural Language Processing (NLP) (JD says "NLP"; '
                                                     'write "Natural Language Processing (NLP)")'])


class QuestionsTest(unittest.TestCase):
    def setUp(self):
        ats_profile._cache.clear()

    def test_greenhouse_questions_are_read_live_without_the_standard_fields(self):
        with mock.patch.object(ats_profile, "get_json", return_value=GH_QUESTIONS) as fetch:
            lines = ats_profile.questions("greenhouse", "acme", "123")
            again = ats_profile.questions("greenhouse", "acme", "123")
        fetch.assert_called_once_with("https://boards-api.greenhouse.io/v1/boards/acme/jobs/123?questions=true")
        self.assertEqual(lines, again)                                                # cached
        self.assertEqual(lines, ["Application questions (from the posting, live):",
                                 "  - Will you now or in the future require sponsorship? (required) [Yes / No]",
                                 "  - LinkedIn Profile",
                                 "  plus 1 voluntary EEO / demographic question group(s)"])

    def test_a_failed_read_says_so(self):
        with mock.patch.object(ats_profile, "get_json", side_effect=HttpError("https://x", 503, "unavailable")):
            self.assertIn("could not be read just now", ats_profile.questions("greenhouse", "acme", "9")[0])

    def test_other_atss_say_why_there_are_no_questions(self):
        with mock.patch.object(ats_profile, "get_json") as fetch:
            lever = ats_profile.questions("lever", "acme", "1")[0]
            workday = ats_profile.questions("workday", "x", "1")[0]
        fetch.assert_not_called()
        self.assertIn("not publicly documented", lever)
        self.assertIn("not exposed by Workday's public API", workday)
        self.assertIn("[https://", workday)


class JdTextTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = mock.patch.object(config, "DB_PATH", Path(self.tmp.name) / "t.db")
        self.patch.start()
        self.conn = db.connect()
        item = make_job(ats="greenhouse", key="acme", ext_id="777", company="Acme", title="ML Engineer",
                        location="Austin, TX", url="https://job-boards.greenhouse.io/acme/jobs/777",
                        posted_at=db.iso(NOW), description="Requirements:\nPython and AWS.")
        db.sync_boards(self.conn, [{"board": "greenhouse:acme", "ats": "greenhouse", "key": "acme", "company": "Acme"}])
        run = db.start_run(self.conn, "test", NOW)
        store.write_board(self.conn, db.board(self.conn, "greenhouse:acme"), FetchResult([item], 200, n_raw=1), "ok",
                          run, NOW, ["fam:ml_engineer"], 5)
        self.uid = item["uid"]
        with db.tx(self.conn):
            self.conn.execute("INSERT INTO fits(profile, uid, content_hash, lexicon_hash, baseline_hash, "
                              "dealbreaker_hash, jd_state, terms, computed_at) VALUES ('alex', ?, 'h', 'l', 'b', 'd', "
                              "'ok', ?, '2026-10-05T00:00:00Z')",
                              (self.uid, json.dumps([["Python", 1.0, 15, 1, 3, 10], ["AWS", 1.0, 0, 1, 1, 40]])))
        ats_profile._cache.clear()

    def tearDown(self):
        self.conn.close()
        self.patch.stop()
        self.tmp.cleanup()

    def test_copy_jd_plus_keywords_is_ats_aware(self):
        with mock.patch.object(ats_profile, "get_json", return_value=GH_QUESTIONS) as fetch:
            out = jdcopy.jd_text(self.conn, "alex", self.uid, keywords=True)
        fetch.assert_called_once_with("https://boards-api.greenhouse.io/v1/boards/acme/jobs/777?questions=true")
        text = out["text"]
        self.assertTrue(text.split("\n\n")[1].startswith("How Greenhouse handles your resume"))   # under the header
        self.assertIn('  2. ✗ AWS (write "Amazon Web Services (AWS)")', text)
        self.assertIn("require sponsorship? (required) [Yes / No]", text)
        self.assertLess(text.index("Application questions"), text.index(jdcopy.RULE))
        self.assertEqual(out["ats"]["name"], "greenhouse")

    def test_plain_copy_jd_has_no_ats_box(self):
        with mock.patch.object(ats_profile, "get_json") as fetch:
            out = jdcopy.jd_text(self.conn, "alex", self.uid)
        fetch.assert_not_called()
        self.assertNotIn("How Greenhouse", out["text"])
