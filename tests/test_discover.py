"""Registry discovery: slug guesses, board identity checks (name + website hint), best-board choice, probe cache."""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from job_hunt_agent import db, discover
from job_hunt_agent.discover import Probe
from job_hunt_agent.net import HttpError

NOW = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)


class SlugTest(unittest.TestCase):
    def test_whole_name_first_then_suffix_stripped(self):
        self.assertEqual(discover.slug_candidates("Scale AI"), ["scaleai", "scale-ai", "scale"])

    def test_ampersand_and_punctuation(self):
        self.assertEqual(discover.slug_candidates("AT&T Labs")[0], "attlabs")

    def test_non_ascii_names_still_produce_slugs(self):
        self.assertIn("nestle", discover.slug_candidates("Nestlé"))

    def test_names_without_ascii_letters_produce_no_slugs(self):
        self.assertEqual(discover.slug_candidates("北京"), [])
        self.assertEqual(discover.slug_candidates(""), [])
        self.assertEqual(discover.slug_candidates("智谱 AI"), [])


class NamesMatchTest(unittest.TestCase):
    def test_accepts_suffix_and_spacing_variants(self):
        for company, board in [("Character AI", "Character.AI"), ("Cerebras", "Cerebras Systems"),
                               ("1X Technologies", "1X"), ("Hugging Face", "HuggingFace"), ("d-Matrix", "d-Matrix"),
                               ("Scale AI", "Scale AI, Inc."), ("Abnormal Security", "Abnormal"),
                               ("Chime", "Chime Financial, Inc"), ("Rubrik", "Rubrik Job Board"), ("Calm", "Calm.com"),
                               ("Honeycomb", "Honeycomb.io"), ("Domino Data Lab", "Domino"),
                               ("1Password", "1 Password")]:
            with self.subTest(company=company):
                self.assertTrue(discover.names_match(company, board))

    def test_rejects_wrong_companies_seen_live(self):
        for company, board in [("Ro", "Ross Stores"), ("AI", "OpenAI"), ("Shield AI", "S.H.I.E.L.D."),
                               ("Neon", "Neon Pagamentos"), ("LinkedIn", "LinkedIn Partner Sandbox - RSC testing"),
                               ("Runway", "cfo.ai"), ("Motive", None), ("Runway", "Runway Health"),
                               ("Circle", "Circle Health"), ("Apple", "Apple Bio")]:
            with self.subTest(company=company):
                self.assertFalse(discover.names_match(company, board))


class IdentityTest(unittest.TestCase):
    def test_domain_hint_catches_same_name_collision(self):
        circle_so = Probe(12, "Circle", "https://circle.so/")
        self.assertFalse(discover.identity_ok("Circle", "circle.com", circle_so))
        self.assertTrue(discover.identity_ok("Circle", "circle.com", Probe(12, "Circle", "https://www.circle.com")))

    def test_no_hint_falls_back_to_name(self):
        self.assertTrue(discover.identity_ok("Circle", None, Probe(12, "Circle", "https://circle.so/")))

    def test_hint_without_visible_website_needs_review(self):
        self.assertFalse(discover.identity_ok("Galileo", "galileo.ai", Probe(14, "Galileo", None)))

    def test_ats_hosted_links_are_not_company_websites(self):
        self.assertIsNone(discover._company_site("https://job-boards.greenhouse.io/anthropic/jobs/1"))
        self.assertEqual(discover._company_site("https://wearemotive.com/joinus?gh_jid=1"),
                         "https://wearemotive.com/joinus?gh_jid=1")
        self.assertEqual(discover.domain_of("https://www.gomotive.com/careers"), "gomotive.com")
        self.assertEqual(discover.domain_of("https://news.bbc.co.uk/x"), "bbc.co.uk")
        self.assertIsNone(discover._company_site("https://lyft.careerpuck.com/job/1"))

    def test_seed_line_hint(self):
        self.assertEqual(discover.parse_seed_line("Circle | circle.com"), ("Circle", "circle.com"))
        self.assertEqual(discover.parse_seed_line("Anthropic"), ("Anthropic", None))


class BestHitTest(unittest.TestCase):
    def test_verified_board_beats_bigger_review_board(self):
        hits = [{"ats": "lever", "n_jobs": 90, "review": True}, {"ats": "ashby", "n_jobs": 4, "review": False}]
        self.assertEqual(discover.best_hit(hits)["ats"], "ashby")
        self.assertIsNone(discover.best_hit([]))


class ProbeCacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.db")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_hit_is_cached_with_name_and_website(self):
        live = Probe(5, "Acme", "https://acme.com")
        with mock.patch.object(discover, "_probe_live", return_value=live) as probe_live:
            self.assertEqual(discover.probe(self.conn, "ashby", "acme", NOW), live)
            self.assertEqual(discover.probe(self.conn, "ashby", "acme", NOW), live)
        self.assertEqual(probe_live.call_count, 1)

    def test_transient_failure_is_not_cached(self):
        with mock.patch.object(discover, "_probe_live", side_effect=HttpError("u", 503, "busy")):
            with self.assertRaises(HttpError):
                discover.probe(self.conn, "greenhouse", "acme", NOW)
        self.assertIsNone(self.conn.execute("SELECT 1 FROM discover_probes").fetchone())

    def test_discover_one_flags_mismatched_board(self):
        def fake(conn, ats, slug, now):
            return Probe(7, "Neon Pagamentos", None) if ats == "lever" and slug == "neon" else Probe(0, None, None)
        with mock.patch.object(discover, "probe", side_effect=fake):
            hits = discover._discover_one(self.conn, "Neon", None, NOW)
        self.assertEqual([(h["ats"], h["token"], h["review"]) for h in hits], [("lever", "neon", True)])


class BoardIdentityTest(unittest.TestCase):
    def test_greenhouse_board_content_reveals_the_website(self):
        board = {"name": "Galileo", "content": "<p>Questions? Email jobs@galileo.io</p>"}
        jobs = [{"absolute_url": "https://job-boards.greenhouse.io/galileo/jobs/1"}]
        with mock.patch.object(discover, "get_json", return_value=board):
            name, site = discover._board_identity("greenhouse", "galileo", jobs)
        self.assertEqual((name, discover.domain_of(site)), ("Galileo", "galileo.io"))
        self.assertFalse(discover.identity_ok("Galileo", "galileo.ai", Probe(14, name, site)))


class ContentCheckTest(unittest.TestCase):
    INTRO = ("daydream is building the world's best SEO agent for growth teams that want to rank in search and "
             "in AI answers. We are a small team in San Francisco backed by great investors and we ship fast every "
             "single week with customers who love the product and tell their friends about it all the time. ")

    LEGAL = ("The base pay range for this position is determined by job related knowledge skills experience and "
             "market location and the range below reflects the full scope of compensation we reasonably expect to "
             "pay for this role in accordance with the federal pay rules and applicable fair chance ordinances. ")

    def sets(self, jd):
        return discover.board_content.distinctive({"daydream": [jd]})["daydream"]

    def test_same_intro_matches_and_stranger_does_not(self):
        jd = self.INTRO + "You will build agents with Python and LLMs."
        self.assertTrue(discover.board_content.matches(self.sets(jd), self.INTRO + "Account Executive role."))
        dental = ("DayDream helps dentists serve their patients by automating insurance verification and claims. " * 5)
        self.assertFalse(discover.board_content.matches(self.sets(jd), dental))
        self.assertIsNone(discover.board_content.matches(self.sets(jd), ""))
        self.assertIsNone(discover.board_content.matches(self.sets("too short"), dental))

    def test_legal_text_shared_across_companies_is_not_evidence(self):
        amazon = "Amazon builds the everything store and cloud for every customer on earth. " * 3 + self.LEGAL
        zillow = "Zillow helps people find and unlock their next home with data and design. " * 3 + self.LEGAL
        sets = discover.board_content.distinctive({"amazon": [amazon], "zillow": [zillow]})
        self.assertTrue(discover.board_content.matches(sets["amazon"], amazon))
        self.assertFalse(discover.board_content.matches(sets["amazon"], zillow))

    def test_lever_text_includes_lists(self):
        job = {"descriptionPlain": "", "lists": [{"text": "About Qloo", "content": "<li>Taste AI for brands</li>"}]}
        self.assertIn("Taste AI", discover.board_content._lever_text(job))

    def test_odd_payload_is_no_evidence_not_a_crash(self):
        with mock.patch.object(discover.board_content, "board_text", side_effect=AttributeError("'list'")):
            self.assertIsNone(discover.content_note("ashby", "acme", self.sets(self.INTRO * 2)))

    def test_discover_flags_tracker_name_with_foreign_postings(self):
        with mock.patch.object(discover, "probe", return_value=Probe(5, "DayDream", None)), \
             mock.patch.object(discover.board_content, "board_text", return_value="Dental claims " * 80):
            hits = discover._discover_one(None, "daydream", None, NOW, self.sets(self.INTRO * 2))
        self.assertTrue(hits[0]["review"])
        self.assertIn("tracker JD", hits[0]["note"])


class ReverifyTest(unittest.TestCase):
    ENTRIES = [{"name": "Neon", "ats": "lever", "token": "neon"},
               {"name": "Acme", "ats": "ashby", "token": "acme"},
               {"name": "Gone", "ats": "greenhouse", "token": "gone"},
               {"name": "Held", "ats": "ashby", "token": "held", "review": True, "note": "x"},
               {"name": "Mine", "ats": "ashby", "token": "mine", "verified": True},
               {"name": "Big", "ats": "workday", "token": "big"}]

    def test_flags_mismatches_and_dead_boards_and_skips_human_decisions(self):
        live = {"neon": Probe(7, "Neon Pagamentos", None), "acme": Probe(3, "Acme", None), "gone": Probe(0, None, None)}
        saved = []
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(discover.config, "DB_PATH", Path(tmp) / "t.db"), \
                mock.patch.object(discover, "seed_names", return_value=({}, {})), \
                mock.patch.object(discover.config, "companies_raw", return_value=[dict(e) for e in self.ENTRIES]), \
                mock.patch.object(discover.config, "save_companies", side_effect=saved.append), \
                mock.patch.object(discover, "_probe_live", side_effect=lambda ats, slug: live[slug]), \
                mock.patch.object(discover.board_content, "tracker_jds", return_value={}):
            out = discover.reverify(workers=2)
        self.assertEqual(out["checked"], 3)
        by_name = {e["name"]: e for e in saved[0]}
        self.assertTrue(by_name["Neon"]["review"])
        self.assertIn("no live jobs", by_name["Gone"]["note"])
        self.assertNotIn("review", by_name["Acme"])
        self.assertEqual(by_name["Held"]["note"], "x")
        self.assertNotIn("review", by_name["Mine"])
        self.assertEqual(len(saved[0]), len(self.ENTRIES))

    def test_lever_hosted_page_404_means_review_not_abort(self):
        with mock.patch.object(discover, "get_text", side_effect=HttpError("u", 404, "not found")):
            self.assertEqual(discover._board_identity("lever", "qloo", []), (None, None))


class AddTest(unittest.TestCase):
    def test_unreachable_board_is_a_clean_error(self):
        with mock.patch.object(discover, "_probe_live", side_effect=HttpError("u", None, "timed out")):
            with self.assertRaisesRegex(ValueError, "could not reach"):
                discover.add("https://jobs.ashbyhq.com/acme")

    def test_smartrecruiters_is_refused_for_robots(self):
        with self.assertRaisesRegex(ValueError, "robots"):
            discover.add("https://jobs.smartrecruiters.com/ServiceNow")

    def test_ashby_identity_comes_from_the_hosted_page_not_the_api(self):
        page = '<title>Acme AI Jobs</title> ... "publicWebsite":"https://acme.ai/"'
        with mock.patch.object(discover, "get_text", return_value=page) as fetched, \
                mock.patch.object(discover, "post_json") as posted:
            self.assertEqual(discover._board_identity("ashby", "acme", []), ("Acme AI", "https://acme.ai/"))
        fetched.assert_called_once_with("https://jobs.ashbyhq.com/acme")
        posted.assert_not_called()

    def test_unsupported_url_lists_supported_hosts(self):
        with self.assertRaisesRegex(ValueError, "myworkdayjobs"):
            discover.add("https://jobs.eu.lever.co/acme")


if __name__ == "__main__":
    unittest.main()
