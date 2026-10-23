"""GOAL29 §2: the Offer check panel. The free areas (Level fit, Location and remote,
Sponsorship, Hiring health) each get a grade A to F, a word and one line of evidence; Claude Haiku grades Growth, Team
signals and Mission only for jobs with Match >= 70, once per JD (cached by content hash), while today's spend is under
$1; a quote Claude gives that is not in the JD drops its grade. Match % and Priority never change."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from job_hunt_agent import db, offer

NOW = datetime(2026, 10, 19, 18, 0, tzinfo=timezone.utc)
JD = ("You will own the ranking models behind Search used by 40 million riders. Join a team of 6 ML engineers and "
      "a research scientist. You will learn large scale distributed training. Our mission is safer rides.")
PROFILE = {"years": 4, "max_required_years": 4, "location": "Denver, CO", "needs_sponsorship": True,
           "headline_default": "AI Engineer"}


def row(**kw):
    base = {"uid": "gh:acme:1", "company": "Acme", "title": "ML Engineer", "location": "Austin, TX", "workplace": "onsite",
            "is_us": 1, "salary_min": None, "salary_max": None, "salary_ccy": "USD", "min_years": 3,
            "content_hash": "h1", "jd": JD}
    return {**base, **kw}


class OtherFreeAreasTest(unittest.TestCase):
    def test_level_fit_reuses_the_seniority_logic(self):
        self.assertEqual(offer.level(row(min_years=3), PROFILE)["grade"], "A")
        self.assertIn(offer.level(row(min_years=8, title="Staff ML Engineer"), PROFILE)["grade"], ("D", "F"))

    def test_location(self):
        self.assertEqual(offer.location(row(workplace="remote", location="Remote, US"), PROFILE, "Yes")["grade"], "A")
        self.assertEqual(offer.location(row(location="Denver, CO"), PROFILE, "No")["grade"], "A")
        moves = offer.location(row(), PROFILE, "Yes")
        self.assertEqual((moves["grade"], moves["evidence"]), ("B", "On site in Austin, TX; you said you would relocate"))
        self.assertEqual(offer.location(row(), PROFILE, "No")["grade"], "D")
        self.assertEqual(offer.location(row(is_us=0, location="Toronto, ON"), PROFILE, "Yes")["grade"], "F")

    def test_sponsorship(self):
        self.assertEqual(offer.sponsorship({**PROFILE, "needs_sponsorship": False}, None)["grade"], "A")
        self.assertEqual(offer.sponsorship(PROFILE, {"kind": "sponsors", "why": "120 H-1B filings in 2025"})["grade"], "A")
        self.assertEqual(offer.sponsorship(PROFILE, {"kind": "no", "why": "The post says: no sponsorship"})["grade"], "F")
        self.assertIsNone(offer.sponsorship(PROFILE, None)["grade"])


class DbCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.db")
        self.conn.execute("INSERT INTO boards(board, ats, key, company) VALUES ('gh:acme', 'greenhouse', 'acme', 'Acme')")
        self.n = 0

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def add(self, first_seen, closed_at=None, jd=JD, salary=(150000, 190000)):
        self.n += 1
        uid = f"gh:acme:{self.n}"
        self.conn.execute("INSERT INTO jobs(uid, board, ext_id, company, title, families, location, workplace, is_us, "
                          "salary_min, salary_max, salary_ccy, min_years, precision, description, content_hash, repost_key, "
                          "first_seen, last_seen, closed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (uid, "gh:acme", str(self.n), "Acme", "ML Engineer", ",ml_engineer,", "Austin, TX", "onsite", 1,
                           *salary, "USD", 3, "exact", db.pack(jd), f"h{self.n}", f"rk{self.n}", first_seen, first_seen, closed_at))
        return uid


class HealthTest(DbCase):
    def test_opened_versus_closed_in_60_days(self):
        for d in (5, 10, 20, 30):
            self.add(db.iso(NOW - timedelta(days=d)))
        self.add(db.iso(NOW - timedelta(days=90)), closed_at=db.iso(NOW - timedelta(days=3)))
        got = offer.hiring_health(self.conn, "Acme", NOW)
        self.assertEqual((got["grade"], got["evidence"]), ("A", "Opened 4 and closed 1 roles on our boards in 60 days"))
        self.assertIsNone(offer.hiring_health(self.conn, "Nobody", NOW)["grade"])


class CheckTest(DbCase):
    def setUp(self):
        super().setUp()
        self.uid = self.add(db.iso(NOW - timedelta(days=2)))
        self.calls = []
        p1 = mock.patch.object(offer.config, "profile", lambda pid: PROFILE)
        p2 = mock.patch.object(offer.profile_settings, "answers", lambda pid, profile=None: {"relocate": "Yes"})
        p1.start(); p2.start()
        self.addCleanup(p1.stop); self.addCleanup(p2.stop)

    def runner(self, prompt):
        self.calls.append(prompt)
        return ({"growth": {"grade": "B", "quote": "You will learn large scale distributed training"},
                 "team": {"grade": "A", "quote": "Join a team of 6 ML engineers and a research scientist"},
                 "mission": {"grade": "C", "quote": "a promise not in the posting"}}, 0.004)

    def test_claude_areas_only_at_match_70_and_once_per_jd(self):
        low = offer.check(self.conn, "alex", self.uid, 65, None, NOW, run_llm=True, runner=self.runner)
        self.assertEqual((low["llm"], self.calls), ("skipped", []))
        got = offer.check(self.conn, "alex", self.uid, 82, {"kind": "sponsors", "why": "H-1B filings"}, NOW,
                          run_llm=True, runner=self.runner)
        self.assertEqual((got["llm"], len(self.calls)), ("new", 1))
        areas = {a["area"]: a for a in got["areas"]}
        self.assertEqual(areas["Team signals"]["grade"], "A")
        self.assertEqual(areas["Team signals"]["evidence"], "“Join a team of 6 ML engineers and a research scientist”")
        self.assertIsNone(areas["Mission and product"]["grade"])           # its quote is not in the JD: no grade
        again = offer.check(self.conn, "alex", self.uid, 82, None, NOW, run_llm=True, runner=self.runner)
        self.assertEqual((again["llm"], len(self.calls)), ("cached", 1))     # never asked twice for one JD
        self.assertEqual(len(again["areas"]), 7)

    def test_daily_budget_pauses_it(self):
        self.conn.execute("INSERT INTO offer_llm(content_hash, areas, model, cost_usd, day, at) VALUES ('x', '[]', 'm', 1.0, ?, ?)",
                          (NOW.astimezone().strftime("%Y-%m-%d"), db.iso(NOW)))
        got = offer.check(self.conn, "alex", self.uid, 90, None, NOW, run_llm=True, runner=self.runner)
        self.assertEqual((got["llm"], got["note"], self.calls), ("paused", "Offer check paused: daily budget reached", []))
        self.assertIn("Offer check paused: daily budget reached", [a["evidence"] for a in got["areas"]])

    def test_the_overall_line_and_the_log(self):
        got = offer.check(self.conn, "alex", self.uid, 90, {"kind": "sponsors", "why": "H-1B filings"}, NOW,
                          run_llm=True, runner=self.runner)
        self.assertRegex(got["line"], r"^Offer check: [A-F], good ")
        self.assertIn("unclear mission", got["line"])
        logged = self.conn.execute("SELECT grade, line FROM offer_checks WHERE profile = 'alex' AND uid = ?", (self.uid,)).fetchone()
        self.assertEqual(tuple(logged), (got["grade"], got["line"]))
        self.assertTrue(all(a["word"] and a["evidence"] for a in got["areas"]))

    def test_overall_wording(self):
        areas = [offer._area("Level fit", "A", "x"), offer._area("Team signals", "B", "x"), offer._area("Growth", None, "x"),
                 offer._area("Sponsorship", "C", "x")]
        self.assertEqual(offer.overall(areas), ("B", "Offer check: B, good level fit and team signals, unclear growth"))

    def test_no_dashes_in_any_panel_text(self):
        got = offer.check(self.conn, "alex", self.uid, 90, None, NOW, run_llm=True, runner=self.runner)
        text = json.dumps(got, ensure_ascii=False)
        self.assertNotRegex(text, "[—–]| - ")


class ClaudeOutputTest(unittest.TestCase):
    def test_structured_output_and_cost(self):
        out = json.dumps({"type": "result", "is_error": False, "result": "", "total_cost_usd": 0.0031,
                          "structured_output": {"growth": {"grade": "B", "quote": "q"}}})
        with mock.patch.object(offer.subprocess, "run", lambda *a, **k: mock.Mock(stdout=out, stderr="", returncode=0)):
            self.assertEqual(offer.run_claude("p"), ({"growth": {"grade": "B", "quote": "q"}}, 0.0031))

    def test_a_usage_limit_is_said_in_words(self):
        out = json.dumps({"type": "result", "is_error": True, "result": "You've hit your usage limit"})
        with mock.patch.object(offer.subprocess, "run", lambda *a, **k: mock.Mock(stdout=out, stderr="", returncode=1)):
            with self.assertRaisesRegex(ValueError, "Claude usage limit"):
                offer.run_claude("p")
