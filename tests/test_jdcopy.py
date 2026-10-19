"""Copy JD / Copy JD + keywords, the server's 08:00 digest schedule, and resume generation being gone."""
import json
import re
import tempfile
import unittest
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from unittest import mock

from job_hunt_agent import cli, config, db, jdcopy, notify, server
from tests.test_server import seed_job

ROOT = Path(__file__).resolve().parent.parent


class CopyJdTest(unittest.TestCase):
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

    def _fit(self, terms):
        with db.tx(self.conn):
            self.conn.execute("INSERT OR REPLACE INTO fits(profile, uid, content_hash, lexicon_hash, baseline_hash, "
                              "dealbreaker_hash, jd_state, terms, computed_at) VALUES ('alex', ?, 'h', 'l', 'b', 'd', "
                              "'ok', ?, '2026-10-05T00:00:00Z')", (self.uid, json.dumps(terms)))

    def test_copy_jd_has_company_title_url_on_top_then_the_clean_jd(self):
        out = jdcopy.jd_text(self.conn, "alex", self.uid)
        head, body = out["text"].split(jdcopy.RULE)
        self.assertEqual(head.strip().splitlines(), ["Company: Acme", "Title: Machine Learning Engineer",
                                                     "Location: Austin, TX", "URL: https://jobs.lever.co/acme/1"])
        self.assertIn("Python, PyTorch, Kubernetes and AWS.", body)
        self.assertNotIn("Hard skills", out["text"])

    def test_keywords_list_the_jd_skills_and_the_ones_missing_from_the_resume(self):
        # masks over Alex's 4 resumes (profile.yaml order: AI, ML, DS, DE): bit 1 = ML has it
        self._fit([["Python", 1.0, 15, 1, 3, 10], ["Kubernetes", 0.5, 0, 0, 1, 90], ["AWS", 1.0, 2, 1, 1, 40],
                   ["PyTorch", 1.0, 3, 1, 2, 20]])
        with mock.patch("job_hunt_agent.fits.known_skills", return_value=frozenset({"Kubernetes"})):
            out = jdcopy.jd_text(self.conn, "alex", self.uid, keywords=True)
        text = out["text"]
        # a Lever job uses the Lever model (Talent Fit counts only required keywords: Kubernetes is preferred)
        self.assertIn("Estimated Lever match (modeled on documented behavior)", text)
        self.assertIn("Resume used: ML Engineer (best of 4)", text)
        self.assertIn("Estimated Lever match: 100% (generic keyword match 75%)", text)
        self.assertIn("All resumes: AI Engineer 67% · ML Engineer 100% · Data Scientist 33% · Data Engineer 33%", text)
        self.assertIn("Matched, in the form Lever needs (3): Python, PyTorch, AWS", text)
        self.assertIn("Missing, in the form Lever needs (1, most important first): Kubernetes (in known_skills", text)
        self.assertIn("weights: only required keywords count", text)                       # the reasoning
        self.assertLess(text.index("Why (model reasoning)"), text.index(jdcopy.RULE))
        self.assertEqual((out["resume"], [r["pct"] for r in out["resumes"]]), ("ml_engineer", [67, 100, 33, 33]))

    def test_a_vendor_without_documented_matching_keeps_the_generic_keyword_view(self):
        self._fit([["Python", 1.0, 15, 1, 3, 10], ["Kubernetes", 0.5, 0, 0, 1, 90], ["AWS", 1.0, 2, 1, 1, 40],
                   ["PyTorch", 1.0, 3, 1, 2, 20]])
        with mock.patch("job_hunt_agent.fits.known_skills", return_value=frozenset({"Kubernetes"})), \
                mock.patch("job_hunt_agent.vendor_model.models", return_value={}):
            out = jdcopy.jd_text(self.conn, "alex", self.uid, keywords=True)
        text = out["text"]
        self.assertIn("Resume used: ML Engineer (best of 4)", text)
        self.assertIn("Match: 75% (3 of 4 JD keywords)", text)
        self.assertIn("All resumes: AI Engineer 50% · ML Engineer 75% · Data Scientist 25% · Data Engineer 25%", text)
        self.assertIn("Matched keywords (3): Python, PyTorch, AWS", text)                    # most important first
        self.assertIn("Missing keywords (1, most important first): Kubernetes (in known_skills: you have it", text)
        self.assertLess(text.index("Missing keywords"), text.index(jdcopy.RULE))
        self.assertEqual((out["resume"], [r["pct"] for r in out["resumes"]]), ("ml_engineer", [50, 75, 25, 25]))

    def test_picking_another_resume_from_the_dropdown(self):
        self._fit([["Python", 1.0, 15, 1, 1, 0], ["Spark", 1.0, 8, 1, 1, 9]])
        text = jdcopy.jd_text(self.conn, "alex", self.uid, keywords=True, resume="data_engineer")["text"]
        self.assertIn("Resume used: Data Engineer (you picked it)", text)
        self.assertIn("Estimated Lever match: 100% (generic keyword match 100%)", text)

    def test_unscored_job_says_so_and_unknown_uid_is_a_key_error(self):
        text = jdcopy.jd_text(self.conn, "alex", self.uid, keywords=True)["text"]
        self.assertIn("not scored yet", text)
        with self.assertRaises(KeyError):
            jdcopy.jd_text(self.conn, "alex", "lever:acme:nope")

    def test_a_jd_naming_no_tracked_skill_says_so_not_zero_percent(self):
        self._fit([])
        text = jdcopy.jd_text(self.conn, "alex", self.uid, keywords=True)["text"]
        self.assertIn("Match: none of the tracked skills appear in this JD, so there is no keyword match", text)
        self.assertNotIn("Match: 0%", text)

    def test_route_answers_the_clipboard_text(self):
        out = server.route(self.conn, "GET", "/api/jd_text", {"profile": "alex", "uid": self.uid, "keywords": "1"},
                           datetime.now(timezone.utc))
        self.assertTrue(out["text"].startswith("Company: Acme\n"))


class DigestScheduleTest(unittest.TestCase):
    def test_due_once_a_day_in_the_morning_window(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(config, "DATA", Path(tmp)):
            at = lambda h: datetime(2026, 10, 5, h, 5).astimezone().astimezone(timezone.utc)    # local hour h
            self.assertFalse(notify.digest_due(at(7), 8, 4))
            self.assertTrue(notify.digest_due(at(8), 8, 4))
            self.assertTrue(notify.digest_due(at(11), 8, 4))                   # Mac asleep at 08:00
            self.assertFalse(notify.digest_due(at(12), 8, 4))                  # no "morning" digest at noon
            notify.digest_path(at(9)).parent.mkdir(parents=True)
            notify.digest_path(at(9)).write_text("sent")
            self.assertFalse(notify.digest_due(at(10), 8, 4))                  # already sent today


class ResumeGenerationGoneTest(unittest.TestCase):
    """GOAL3 Done-when 1: no generation in the UI, CLI or launchd; nothing launches soffice."""

    def test_no_live_code_mentions_soffice_or_libreoffice(self):
        hits = [str(p) for p in (ROOT / "job_hunt_agent").rglob("*") if p.is_file() and p.suffix in (".py", ".js", ".html")
                and re.search(r"(?i)soffice|libreoffice", p.read_text(errors="ignore"))]
        self.assertEqual(hits, [])

    def test_cli_has_no_generation_commands(self):
        commands = set(cli.build_parser()._subparsers._group_actions[0].choices)
        self.assertFalse(commands & {"generate", "pregen", "render", "jd", "ats", "known", "finish"})
        self.assertIn("baseline", commands)                                    # the fit score's resume import stays

    def test_ui_has_no_generation_routes_or_buttons(self):
        conn = mock.MagicMock()
        for method, path in [("GET", "/api/gen"), ("GET", "/api/resume"), ("POST", "/api/generate"),
                             ("POST", "/api/generate/known")]:
            with self.assertRaises(KeyError):
                server.route(conn, method, path, {"profile": config.default_profile()}, datetime.now(timezone.utc))
        root = resources.files("job_hunt_agent").joinpath("static")
        static = root.joinpath("index.html").read_text() + "".join(
            f.read_text() for f in root.joinpath("js").iterdir() if f.name.endswith(".js"))
        for gone in ("Generate Resume", "resume.js", "/api/generate", "ATS coverage", "/tailor"):
            self.assertNotIn(gone, static)
        self.assertIn("Copy JD + keywords", static)

    def test_commands_folder_has_no_tailor(self):
        self.assertFalse((ROOT / ".claude" / "commands" / "tailor.md").exists())
        self.assertFalse((ROOT / ".claude" / "commands" / "tailor-batch.md").exists())


if __name__ == "__main__":
    unittest.main()


class DoctorGoneBoardsTest(unittest.TestCase):
    """a board answering 404 is on decision 005's dead path (weekly re-check, dropped after 4), so the
    doctor lists it as gone instead of failing; any other streak of failures still turns the doctor red."""

    def test_404_board_is_gone_not_failing(self):
        from job_hunt_agent import health
        with tempfile.TemporaryDirectory() as tmp:
            conn = db.connect(Path(tmp) / "t.db")
            db.sync_boards(conn, [{"board": f"greenhouse:{k}", "ats": "greenhouse", "key": k, "company": k}
                                  for k in ("gone", "broken")])
            with db.tx(conn):
                conn.execute("UPDATE boards SET fail_streak = 3, dead_streak = 1 WHERE board = 'greenhouse:gone'")
                conn.execute("UPDATE boards SET fail_streak = 3 WHERE board = 'greenhouse:broken'")
            h = health.health(conn, datetime.now(timezone.utc))
            self.assertEqual([b["board"] for b in h["failing"]], ["greenhouse:broken"])
            self.assertEqual([b["board"] for b in h["gone"]], ["greenhouse:gone"])
            conn.close()
