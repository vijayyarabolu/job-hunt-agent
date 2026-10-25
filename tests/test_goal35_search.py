"""GOAL35 §5 (user 2026-10-23: "Tracker search is not finding the previous roles I applied"): the shared search
(textsearch) and every box that uses it. The 10 roles of §5.1 (tests/fixtures/goal35/applied_roles.json, roles the user
applied to, taken from a backup copy) must each be found by company, part of the title, a typo, the city, a JD skill
and the applied date; the page's JS twin (find.js) and the extension's JH.find follow the same rules; the user's whole
history searches in under 200 ms. No live DB, no network."""
import json
import shutil
import statistics
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from job_hunt_agent import api, appliedview, config, db, filters, searchapi, textsearch, tracker, trackapi, trackfind

ROOT = Path(__file__).resolve().parent.parent
ROLES = json.loads((Path(__file__).parent / "fixtures" / "goal35" / "applied_roles.json").read_text())["roles"]
PID = "tester"
BUDGET_MS = 200
HISTORY_ROWS, HISTORY_JD_WORDS = 700, 400       # about the user's tracker: ~600 rows, most with a pasted JD
# (query, text, should match) for the rules all three twins share (aliases and dates are the server's alone)
SHARED = [("ai ml", "AI/ML Engineer", True), ("AI/ML", "AI ML Engineer", True), ("aiml", "AI/ML Engineer", True),
          ("engineer ai", "Applied AI Engineer", True), ("ai", "Airline Engineer", False),
          ("antrhopic", "Anthropic", True), ("anthrop", "Anthropic", True), ("prue", "Pure Storage", False),
          ("sotrage", "Pure Storage", True), ("cafe resume", "Café Résumé", True),
          ("data scientist cvs", "CVS Health Data Scientist", True),
          ("data scientist meta", "CVS Health Data Scientist", False),
          ("machine learnin", "Machine Learning Engineer", True)]


def seed_apps(conn, extra: int = 0, jd_words: int = 0) -> None:
    tracker.ensure_profile(conn, PID)
    filler = " ".join(f"word{i % 997} skill{i % 389}" for i in range(jd_words))
    sql = ("INSERT INTO applications(profile, company, role_title, location, applied_on, status, jd_text) "
           "VALUES (?, ?, ?, ?, ?, ?, ?)")
    with db.tx(conn):
        for r in ROLES:
            conn.execute(sql, (PID, r["company"], r["title"], r["location"], r["applied_on"], r["status"], r["jd_excerpt"]))
        for i in range(extra):
            conn.execute(sql, (PID, f"Filler Co {i}", f"Backend Developer {i}", "Austin, TX",
                               f"2026-0{1 + i % 9}-1{i % 9}", "ghosted", filler))


class _Db(unittest.TestCase):
    def setUp(self):
        from tests.test_goal9_a import PROFILE
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "profiles" / PID / "resumes").mkdir(parents=True)
        (root / "profiles" / PID / "profile.yaml").write_text(PROFILE)
        self.patches = [mock.patch.object(config, "DB_PATH", root / "t.db"),
                        mock.patch.object(config, "PROFILES", root / "profiles")]
        for p in self.patches:
            p.start()
        self.conn = db.connect()

    def tearDown(self):
        self.conn.close()
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def tracker_hits(self, q: str, **kw) -> dict:
        return trackapi.rows(self.conn, PID, q=q, limit=trackapi.MAX_PAGE, **kw)


class RealRolesTest(_Db):
    def test_every_role_is_found_by_every_search_shape(self):
        seed_apps(self.conn, extra=40)
        for r in ROLES:
            for shape, q in r["searches"].items():
                with self.subTest(company=r["company"], shape=shape, q=q):
                    rows = self.tracker_hits(q)["rows"]
                    self.assertIn((r["company"], r["title"]), [(x["company"], x["role_title"]) for x in rows])

    def test_a_search_groups_by_status_with_counts_and_counts_the_other_statuses(self):
        seed_apps(self.conn, extra=5)
        out = self.tracker_hits("engineer")
        order = [trackapi.UI_STATUSES.index(x["status"].replace("tailored", "saved")) for x in out["rows"]]
        self.assertEqual(order, sorted(order))
        self.assertEqual(sum(out["found"]["counts"].values()), out["found"]["total"])
        self.assertEqual(out["found"]["total"], out["total"])
        only = self.tracker_hits("engineer", status="rejected")
        self.assertLess(only["total"], only["found"]["total"])          # the page offers "N more in other statuses"
        self.assertTrue(only["found"]["terms"])

    def test_a_jd_only_match_says_where_it_was_found(self):
        seed_apps(self.conn)
        r = next(x for x in ROLES if x["company"] == "Harbor Health")
        out = self.tracker_hits(r["searches"]["JD skill"])
        hit = next(x for x in out["rows"] if x["company"] == "Harbor Health")
        self.assertEqual(out["found"]["where"][str(hit["id"])], ["jd"])

    def test_nothing_found_is_an_empty_page_not_everything(self):
        seed_apps(self.conn)
        out = self.tracker_hits("zebracorn quantumfoam")
        self.assertEqual((out["total"], out["rows"], out["found"]["total"]), (0, [], 0))

    def test_his_whole_history_searches_in_under_200_ms(self):
        seed_apps(self.conn, extra=HISTORY_ROWS, jd_words=HISTORY_JD_WORDS)
        trackfind.index(self.conn, PID)                                  # built once per change, and at startup
        times = []
        for r in ROLES:
            for q in r["searches"].values():
                t = time.perf_counter()
                self.assertTrue(trackfind.find(self.conn, PID, q).ids(), q)
                times.append((time.perf_counter() - t) * 1000)
        self.assertLess(max(times), BUDGET_MS, f"median {statistics.median(times):.1f} ms")


class TextSearchTest(unittest.TestCase):
    def found(self, q, *texts, dates=()):
        return bool(textsearch.search([textsearch.Record(1, {"t": " ".join(texts)}, dates)], q).ids())

    def test_the_shared_rules(self):
        for q, text, want in SHARED:
            with self.subTest(q=q, text=text):
                self.assertEqual(self.found(q, text), want)

    def test_aliases_both_ways_and_only_as_whole_names(self):
        self.assertTrue(self.found("meta", "Facebook"))
        self.assertTrue(self.found("facebook", "Meta Platforms"))
        self.assertTrue(self.found("aws", "Amazon"))
        self.assertTrue(self.found("amazon web services", "Amazon"))
        self.assertFalse(self.found("facebook", "Metals Inc"))

    def test_dates_in_any_common_form(self):
        for q in ("2026-10-14", "10/14", "10/14/2026", "Oct 14", "October 14, 2026", "14 Oct"):
            with self.subTest(q=q):
                self.assertTrue(self.found(q, "Acme", dates=("2026-10-14",)))
                self.assertFalse(self.found(q, "Acme", dates=("2026-10-15",)))

    def test_every_word_must_match(self):
        self.assertFalse(self.found("acme zebra", "Acme ML Engineer"))

    def test_a_long_jd_matches_its_words_but_not_joined_pairs(self):
        found = textsearch.search([textsearch.Record(1, {"jd": "Python and PyTorch"})], "andpy")
        self.assertEqual(found.ids(), [])


class OtherBoxesTest(_Db):
    def test_applied_cards_use_the_same_search(self):
        seed_apps(self.conn)
        out = appliedview.board(self.conn, PID, "all", "lrakspur", db.utcnow())
        self.assertEqual([c["company"] for c in out["jobs"]], ["Larkspur"])
        self.assertTrue(out["terms"])
        status = next(r["status"] for r in ROLES if r["company"] == "Larkspur")
        other = next(s for s, members in appliedview.SEGMENTS.items() if s != "all" and status not in members)
        elsewhere = appliedview.board(self.conn, PID, other, "lrakspur", db.utcnow())
        self.assertEqual((elsewhere["total"], elsewhere["other_segments"]), (0, 1))

    def test_the_feed_search_and_the_jobs_outside_the_filters(self):
        from tests.test_goal9_a import seed
        seed(self.conn)
        now = db.utcnow()
        out = api.jobs(self.conn, PID, "recommended", now, filters.parse({"q": "machine lerning"}))
        self.assertIn("Machine Learning Engineer", [j["title"] for j in out["jobs"]])
        self.assertNotIn("Data Engineer", [j["title"] for j in out["jobs"]])
        self.assertTrue(out["terms"])
        narrowed = api.jobs(self.conn, PID, "recommended", now, filters.parse({"q": "machine learning", "workplace": "hybrid"}))
        self.assertEqual(narrowed["total"], 0)
        self.assertGreaterEqual(narrowed["q_outside"], 1)

    def test_the_command_k_box_finds_applications_companies_and_jobs(self):
        from tests.test_goal9_a import seed
        seed(self.conn)
        seed_apps(self.conn)
        now = db.utcnow()
        self.assertEqual(searchapi.everything(self.conn, PID, "zebracorn", now)["applications"]["total"], 0)
        out = searchapi.everything(self.conn, PID, "summit bank", now)
        self.assertEqual([r["company"] for r in out["applications"]["rows"]], ["Summit Bank"])
        out = searchapi.everything(self.conn, PID, "amazn", now)
        self.assertIn("Amazon", [c["company"] for c in out["companies"]])
        self.assertTrue(out["jobs"])


@unittest.skipUnless(shutil.which("node"), "needs node")
class JsTwinsTest(unittest.TestCase):
    def run_node(self, script: str) -> list:
        out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_the_page_twin_follows_the_same_rules(self):
        got = self.run_node(f"""
            const f = await import({json.dumps((ROOT / "job_hunt_agent/static/js/find.js").as_uri())});
            console.log(JSON.stringify({json.dumps(SHARED)}.map(([q, t]) => !!f.match(q, t))));""")
        self.assertEqual(got, [want for _, _, want in SHARED])

    def test_the_extension_follows_the_same_rules(self):
        core = (ROOT / "extension/content/core.js").read_text().split("JH.isTop")[0]
        got = self.run_node(f"""
            (0, eval)({json.dumps(core)});
            console.log(JSON.stringify({json.dumps(SHARED)}.map(([q, t]) => !!globalThis.JH.find(q, t))));""")
        self.assertEqual(got, [want for _, _, want in SHARED])


if __name__ == "__main__":
    unittest.main()
