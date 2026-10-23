"""GOAL29 §3: the ghost job check. Each signal on its own (reposted 3+ times in 60 days, first seen 45+ days ago and
open, evergreen wording, a staffing agency's unnamed client, a JD under 300 characters), the badge words, the penalty
cap (10 points), the log of every applied penalty, and that a flagged job is never hidden, only ranked a little lower."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from job_hunt_agent import config, db, ghost, score

NOW = datetime(2026, 10, 19, 18, 0, tzinfo=timezone.utc)
LONG_JD = "Requirements\n" + "Build retrieval and evaluation pipelines in Python on AWS for our product teams. " * 8


def stamp(days_ago: int) -> str:
    return db.iso(NOW - timedelta(days=days_ago))


class SignalTest(unittest.TestCase):
    def test_reposted_three_times_in_sixty_days(self):
        self.assertIn("reposted", ghost.time_kinds(stamp(1), ["2026-08-20", "2026-09-10", "2026-10-01"], NOW))
        self.assertNotIn("reposted", ghost.time_kinds(stamp(1), ["2026-09-10", "2026-10-01"], NOW))

    def test_first_seen_more_than_45_days_ago(self):
        self.assertEqual(ghost.time_kinds(stamp(52), [], NOW), {"old": "52"})
        self.assertEqual(ghost.time_kinds(stamp(45), [], NOW), {})

    def test_evergreen_wording(self):
        for words in ("We are always accepting applications.", "This is an evergreen requisition.",
                      "This posting is not tied to a specific opening.", "This role is ongoing."):
            with self.subTest(words):
                self.assertIn("evergreen", ghost.text_kinds("Acme", LONG_JD + words))
        self.assertIn("evergreen", ghost.text_kinds("Acme", LONG_JD, "AI Engineer, Talent Community"))
        self.assertNotIn("evergreen", ghost.text_kinds("Acme", LONG_JD))
        for title in ("AI Pipeline Engineer", "Data Engineer, Big Data Pipeline Development"):
            self.assertNotIn("evergreen", ghost.text_kinds("Acme", LONG_JD, title))

    def test_a_real_jobs_blurb_or_footer_is_not_evergreen(self):
        # seen on the 2026-10-19 backup: company blurbs and talent-community footers under real reqs
        for words in ("We're always looking for new ways of working that bring out our best.",
                      "Join our Talent Community to stay up to date on our career opportunities.",
                      "We will keep your resume in our database for future opportunities.",
                      "Airbnb is committed to working with the broadest talent pool possible.",
                      "Partner with the ongoing requirements of the platform team."):
            with self.subTest(words):
                self.assertNotIn("evergreen", ghost.text_kinds("Acme", LONG_JD + words))

    def test_a_staffing_agency_with_an_unnamed_client(self):
        self.assertIn("agency", ghost.text_kinds("Apex Staffing Group", LONG_JD + "Our client, a leading bank, needs an ML engineer."))
        self.assertNotIn("agency", ghost.text_kinds("Apex Staffing Group", LONG_JD + "You will join Capital One's AI team."))
        self.assertNotIn("agency", ghost.text_kinds("Capital One", LONG_JD + "You will work with our client teams."))

    def test_a_jd_under_300_characters(self):
        self.assertEqual(ghost.text_kinds("Acme", "ML engineer. Python. Apply now."), {"short": "31"})
        self.assertNotIn("short", ghost.text_kinds("Acme", LONG_JD))
        self.assertEqual(ghost.text_kinds("Acme", ""), {})          # no JD read is not a signal


class VerdictTest(unittest.TestCase):
    def test_badge_words_and_penalty(self):
        penalty, reason = ghost.verdict({"reposted": "4|2026-08-20"})
        self.assertEqual((penalty, reason), (4, "Possible ghost job: reposted 4 times since Aug 20"))

    def test_the_penalty_is_capped_at_10(self):
        every = {"reposted": "3|2026-09-01", "old": "60", "evergreen": "talent community", "agency": "x", "short": "90"}
        self.assertEqual(ghost.verdict(every)[0], ghost.PENALTY_CAP)
        self.assertEqual(ghost.PENALTY_CAP, 10)

    def test_no_signal_no_badge(self):
        self.assertEqual(ghost.verdict({}), (0, None))

    def test_badge_text_has_no_dashes(self):
        every = {"reposted": "3|2026-09-01", "old": "60", "evergreen": "talent community", "agency": "x", "short": "90"}
        self.assertNotRegex(ghost.verdict(every)[1], "[—–]| - ")


class ScoreTest(unittest.TestCase):
    def test_a_flagged_job_ranks_lower_and_is_never_hidden(self):
        row = {"uid": "g:1", "title": "ML Engineer", "company": "Acme", "first_seen": stamp(1), "first_posted_at": stamp(1),
               "precision": "exact", "posted_at": stamp(1), "seed": 0, "is_us": 1}
        ctx = score.Ctx(now=NOW, profile_id="p", years=4, known=frozenset(), has_baseline=False)
        plain = score.final(row, None, ctx)
        flagged = score.final(row, None, score.Ctx(**{**ctx.__dict__, "ghost": {"g:1": (4, "Possible ghost job: x")}}))
        self.assertEqual(flagged.worth, max(0, plain.worth - 4))
        self.assertEqual(flagged.state, plain.state)
        self.assertEqual(flagged.ghost, "Possible ghost job: x")
        self.assertIsNone(plain.ghost)


class ScanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.db")
        self.conn.execute("INSERT INTO boards(board, ats, key, company) VALUES ('gh:acme', 'greenhouse', 'acme', 'Acme')")
        self.n = 0

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def add(self, title, first_seen, jd=LONG_JD, company="Acme", closed=False, families=",ml_engineer,", location="Boston, MA"):
        self.n += 1
        uid = f"gh:acme:{self.n}"
        self.conn.execute("INSERT INTO jobs(uid, board, ext_id, company, title, families, precision, description, "
                          "content_hash, repost_key, first_seen, last_seen, closed_at, location) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (uid, "gh:acme", str(self.n), company, title, families, "exact", db.pack(jd), f"h{self.n}{len(jd)}",
                           f"rk{self.n}", first_seen, first_seen, first_seen if closed else None, location))
        return uid

    def test_scan_flags_logs_and_counts(self):
        reposted = [self.add("Machine Learning Engineer", stamp(d), closed=d != 2) for d in (50, 30, 12, 2)]
        for d in (40, 20, 10):                                                  # same title, another city: not reposts
            self.add("Machine Learning Engineer", stamp(d), closed=True, location="Austin, TX")
        short = self.add("AI Engineer", stamp(3), jd="AI Engineer. Python.")
        fine = self.add("Data Scientist", stamp(3))
        self.add("Sales Manager", stamp(70), families=",")                     # not a matched title: never checked
        got = ghost.scan(self.conn, NOW)
        self.assertEqual((got["checked"], got["flagged"]), (3, 2))
        self.assertEqual(got["by_kind"]["reposted"], 1)
        self.assertEqual(got["by_kind"]["short"], 1)
        loaded = ghost.load(self.conn)
        self.assertEqual(loaded[reposted[-1]], (4, "Possible ghost job: reposted 3 times since Aug 30"))
        self.assertEqual(loaded[short][0], 3)
        self.assertNotIn(fine, loaded)
        log = self.conn.execute("SELECT uid, penalty, kinds FROM ghost_log ORDER BY id").fetchall()
        self.assertEqual(sorted(tuple(r) for r in log), sorted([(reposted[-1], 4, "reposted"), (short, 3, "short")]))
        ghost.scan(self.conn, NOW)                                              # unchanged: nothing logged again
        self.assertEqual(self.conn.execute("SELECT count(*) FROM ghost_log").fetchone()[0], 2)
        self.assertEqual(ghost.scan(self.conn, NOW)["read"], 0)                # JDs read once per content hash

    def test_a_job_that_ages_past_45_days_is_logged_when_its_penalty_changes(self):
        uid = self.add("AI Engineer", stamp(40))
        ghost.scan(self.conn, NOW)
        ghost.scan(self.conn, NOW + timedelta(days=10))
        rows = self.conn.execute("SELECT penalty, kinds FROM ghost_log WHERE uid = ?", (uid,)).fetchall()
        self.assertEqual([tuple(r) for r in rows], [(3, "old")])
        self.assertIn("first seen 50 days ago and still open", ghost.load(self.conn)[uid][1])
