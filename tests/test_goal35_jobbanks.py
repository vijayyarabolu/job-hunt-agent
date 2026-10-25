"""GOAL35 §4: W2 and legit roles only, and the state job banks source (CareerOneStop), on a throwaway DB with ZZ_TEST
data and no network. C2C / 1099 only, no W2, US citizens / green card only and no OPT are hidden for a W2 person
(both by default) and counted in the footer; a W2 job gets the W2 tag; a vague third-party repost is flagged, never
hidden; the state job bank board exists only with the Settings switch on and a key; a posting already open on one of
our boards is left out; every card says "via <the state's bank>"; the per-state report counts found / kept / dropped
and why / new."""
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from job_hunt_agent import api, config, db, filters, jobbanks, jobbanks_report, registry, resignal, store, workterms
from job_hunt_agent.ats import careeronestop
from job_hunt_agent.ats.common import Ctx, make_job

from tests.test_goal8_flow import JD, NOW, PID, Goal8Case, seed_job

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "goal35" / "careeronestop_search.json"
KEY_ENV = {jobbanks.TOKEN_ENV: "zz-test-token", jobbanks.USER_ENV: "zz-test-user"}

CASES = {                                       # JD wording -> what workterms.read() says
    "C2C only. Rate DOE.": ("c2c_only",),
    "Corp to Corp only": ("c2c_only",),
    "Only C2C candidates will be considered.": ("c2c_only",),
    "1099 contract only": ("1099_only",),
    "No W2": ("no_w2",),
    "W2 is not available for this role.": ("no_w2",),
    "USC/GC only": ("citizens_only",),
    "US citizens or green card holders only": ("citizens_only",),
    "Must be a US citizen.": ("citizens_only",),
    "No OPT, CPT, STEM/OPT or visa sponsorship now or in future.": ("no_opt",),
    "OPT candidates are not accepted": ("no_opt",),
    "This role is W2 only, no C2C": ("w2",),
    "Open to W2 or C2C": ("w2",),
    "Tax Terms : W2 / 1099 Only": ("w2",),
    "Open to US citizens, green card holders, H1B and OPT EAD holders.": (),
    "We do not offer visa sponsorship.": (),               # stays the informational label (2026-10-02)
    "Full-time with benefits.": (),
    "not on W2": (),
    "Our client is hiring. C2C. Pay DOE.": ("vague",),
    "Our client needs an engineer. Send resumes to zz.test@gmail.com": ("vague",),
    "Acme is hiring. Send resumes to zz.test@gmail.com": (),        # a named employer: not a vague repost
}


class ReadTest(unittest.TestCase):
    def test_every_wording(self):
        for text, want in CASES.items():
            with self.subTest(text=text):
                self.assertEqual(workterms.read(text), want)

    def test_a_w2_person_has_the_blocks_hidden_and_any_turns_it_off(self):
        self.assertEqual(workterms.verdict("c2c_only,vague", None), "work terms: C2C only")
        self.assertEqual(workterms.verdict("citizens_only,w2", "w2"), "work terms: US citizens / green card only")
        self.assertIsNone(workterms.verdict("w2,vague", "w2"))
        self.assertIsNone(workterms.verdict("c2c_only", "any"))
        self.assertTrue(workterms.is_verdict(workterms.verdict("no_opt", "w2")))

    def test_make_job_stores_the_label(self):
        item = make_job(ats="lever", key="zz", ext_id="1", company="ZZ_TEST", title="ML Engineer",
                        description="USC/GC only. W2.")
        self.assertEqual(item["work_terms"], "citizens_only,w2")


class FeedTest(Goal8Case):
    """Three ordinary jobs from Goal8Case plus one per kind: hidden, counted, tagged."""

    def setUp(self):
        super().setUp()
        self.c2c = seed_job(self.conn, "c2c", description=JD + "\nC2C only.")
        self.usc = seed_job(self.conn, "usc", description=JD + "\nUSC/GC only.")
        self.w2 = seed_job(self.conn, "w2", description=JD + "\nFull-time W2 with benefits.")
        self.vague = seed_job(self.conn, "vague", description=JD + "\nOur client is hiring. C2C. Pay DOE.")

    def feed(self):
        return api.jobs(self.conn, PID, "recommended", NOW, filters.parse({}))

    def test_blocked_jobs_are_hidden_and_counted_apart_from_dealbreakers(self):
        out = self.feed()
        shown = {c["uid"] for c in out["jobs"]}
        self.assertFalse({self.c2c, self.usc} & shown)
        self.assertLessEqual({self.w2, self.vague, self.uids[0]}, shown)      # (2 and 3 are listed under 1: same role)
        self.assertEqual(out["not_w2"], 2)
        self.assertEqual(out["filtered"], 0)

    def test_the_w2_tag_and_the_vague_flag(self):
        cards = {c["uid"]: c for c in self.feed()["jobs"]}
        self.assertEqual(cards[self.w2]["work"], {"w2": True, "contract": False, "vague": False, "via": None})
        self.assertEqual(cards[self.vague]["work"], {"w2": False, "contract": False, "vague": True, "via": None})
        self.assertIsNone(cards[self.uids[0]]["work"])

    def test_work_terms_any_shows_them_again(self):
        path = config.PROFILES / PID / "profile.yaml"
        path.write_text(path.read_text() + "work_terms: any\n")
        out = self.feed()
        self.assertIn(self.c2c, {c["uid"] for c in out["jobs"]})
        self.assertEqual(out["not_w2"], 0)

    def test_a_bad_rule_is_a_config_error(self):
        path = config.PROFILES / PID / "profile.yaml"
        path.write_text(path.read_text() + "work_terms: sometimes\n")
        with self.assertRaises(config.ConfigError):
            config.profile(PID)

    def test_the_backfill_labels_rows_stored_before_schema_38(self):
        with db.tx(self.conn):
            self.conn.execute("UPDATE jobs SET work_terms = NULL")
        got = resignal.work_terms_labels(self.conn)
        self.assertEqual(got["changed"], 4)
        self.assertEqual(got["by_kind"], {"c2c_only": 1, "citizens_only": 1, "vague": 1, "w2": 1})
        stored = dict(self.conn.execute("SELECT uid, work_terms FROM jobs").fetchall())
        self.assertEqual(stored[self.c2c], "c2c_only")
        self.assertEqual(resignal.work_terms_labels(self.conn)["changed"], 0)

    def test_migration_38_adds_the_column(self):
        self.assertEqual(self.conn.execute("PRAGMA user_version").fetchone()[0], db.SCHEMA_VERSION)
        self.assertIn("work_terms", {r[1] for r in self.conn.execute("PRAGMA table_info(jobs)")})


class AdapterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [mock.patch.object(config, "DATA", Path(self.tmp.name)), mock.patch.dict(os.environ, KEY_ENV)]
        for p in self.patches:
            p.start()
        self.asked = []

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def answer(self, url, headers=None):
        self.asked.append((url, headers))
        return json.loads(FIXTURE.read_text())

    def fetch(self, answer=None):
        ctx = Ctx(search_terms=("machine learning engineer",),
                  known_elsewhere=lambda co, t: (co, t) == ("Acme", "Machine Learning Engineer"))
        with mock.patch.object(careeronestop, "get_json", answer or self.answer):
            return careeronestop.fetch(registry.JOBBANK_BOARD, ctx)

    def test_one_search_per_term_with_the_key_in_a_header(self):
        result = self.fetch()
        self.assertEqual(result.http_status, 200)
        url, headers = self.asked[0]
        self.assertIn("/v1/jobsearch/zz-test-user/machine%20learning%20engineer/US/", url)
        self.assertEqual(headers, {"Authorization": "Bearer zz-test-token"})
        self.assertNotIn("zz-test-token", url)

    def test_a_job_already_on_our_boards_is_left_out_and_the_rest_are_jobs(self):
        items = {i["ext_id"]: i for i in self.fetch().items}
        self.assertEqual(sorted(items), ["ZZTEST-KS-1", "ZZTEST-KS-2", "ZZTEST-TX-1"])
        ks = items["ZZTEST-KS-1"]
        self.assertEqual((ks["uid"], ks["company"], ks["location"]),
                         ("careeronestop:us:ZZTEST-KS-1", "ZZ_TEST Prairie Analytics", "Overland Park, KS"))
        self.assertEqual(ks["posted_at"], "2026-10-22T00:00:00Z")
        self.assertEqual(ks["work_terms"], "w2")
        self.assertEqual(items["ZZTEST-KS-2"]["work_terms"], "c2c_only,vague")
        self.assertEqual(items["ZZTEST-TX-1"]["work_terms"], "citizens_only")

    def test_the_last_run_counts_per_state(self):
        self.fetch()
        got = json.loads(careeronestop.report_path().read_text())
        self.assertEqual(got["states"], {"KS": {"found": 2}, "MO": {"found": 1, "same_as_board": 1}, "TX": {"found": 1}})

    def test_a_changed_shape_is_schema_drift_not_an_empty_board(self):
        self.assertTrue(self.fetch(lambda url, headers=None: {"jobs": []}).error.startswith("schema:"))
        self.assertTrue(self.fetch(lambda url, headers=None: {"Jobs": [{"Title": "x"}]}).error.startswith("schema:"))

    def test_no_key_no_request(self):
        with mock.patch.dict(os.environ, {jobbanks.TOKEN_ENV: ""}), \
                mock.patch.object(config, "CONFIG", Path(self.tmp.name)):
            result = self.fetch()
        self.assertEqual(self.asked, [])
        self.assertIn("no CareerOneStop key", result.error)

    def test_via_names_the_states_own_bank(self):
        self.assertEqual(careeronestop.via("Overland Park, KS"), "via KansasWorks")
        self.assertEqual(careeronestop.via("Austin, TX"), "via WorkInTexas")
        self.assertEqual(careeronestop.via("Somewhere, ZZ"), "via state job bank (ZZ)")
        self.assertEqual(careeronestop.via(""), "via state job bank")


class SwitchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "config").mkdir()
        self.patches = [mock.patch.object(config, "DATA", root / "data"), mock.patch.object(config, "CONFIG", root / "config"),
                        mock.patch.dict(os.environ, {jobbanks.TOKEN_ENV: "", jobbanks.USER_ENV: ""})]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def test_off_by_default_and_on_needs_a_key_too(self):
        self.assertEqual(jobbanks.status()["on"], False)
        self.assertFalse(jobbanks.enabled())
        self.assertNotIn(registry.JOBBANK_BOARD["board"], {b["board"] for b in registry.boards()})
        jobbanks.set_on(True)
        self.assertFalse(jobbanks.enabled())                     # switched on, no key yet
        (config.CONFIG / "secrets.yaml").write_text("careeronestop: {token: zz-secret, user_id: zz-user}\n")
        self.assertTrue(jobbanks.enabled())
        self.assertIn(registry.JOBBANK_BOARD["board"], {b["board"] for b in registry.boards()})

    def test_the_switch_file_is_private_and_the_key_never_reaches_the_page(self):
        (config.CONFIG / "secrets.yaml").write_text("careeronestop: {token: zz-secret, user_id: zz-user}\n")
        out = jobbanks.set_on(True)
        self.assertEqual((config.DATA / "sources.json").stat().st_mode & 0o777, 0o600)
        self.assertNotIn("zz-secret", json.dumps(out))
        self.assertEqual((out["on"], out["has_key"]), (True, True))
        with self.assertRaises(ValueError):
            jobbanks.set_on("yes")

    def test_a_broken_switch_file_is_off(self):
        config.DATA.mkdir()
        (config.DATA / "sources.json").write_text("{not json")
        with self.assertLogs("job_hunt_agent.jobbanks", "WARNING"):
            self.assertFalse(jobbanks.switched_on())


class StoredAndReportTest(Goal8Case):
    """The fetched postings go through the store like any board; the report adds what the DB holds."""

    def setUp(self):
        super().setUp()
        self.data = tempfile.TemporaryDirectory()
        self.more = [mock.patch.object(config, "DATA", Path(self.data.name)), mock.patch.dict(os.environ, KEY_ENV)]
        for p in self.more:
            p.start()
        db.sync_boards(self.conn, [registry.JOBBANK_BOARD])
        elsewhere = {careeronestop.same_key(r[0], r[1]) for r in self.conn.execute("SELECT company, title FROM jobs")}
        ctx = Ctx(search_terms=("machine learning engineer",),
                  known_elsewhere=lambda co, t: careeronestop.same_key(co, t) in elsewhere)
        with mock.patch.object(careeronestop, "get_json", lambda url, headers=None: json.loads(FIXTURE.read_text())):
            result = careeronestop.fetch(registry.JOBBANK_BOARD, ctx)
        run = db.start_run(self.conn, "test", NOW)
        store.write_board(self.conn, db.board(self.conn, registry.JOBBANK_BOARD["board"]), result, "ok", run, NOW,
                          ["fam:ml_engineer"], 5)

    def tearDown(self):
        for p in reversed(self.more):
            p.stop()
        self.data.cleanup()
        super().tearDown()

    def test_the_report_per_state(self):
        report = jobbanks_report.counts(self.conn, NOW)
        got = report["states"]
        self.assertEqual(got["KS"], {"found": 2, "same_as_board": 0, "stored": 2, "dropped": {"C2C only": 1},
                                     "kept": 1, "new": 1})
        self.assertEqual(got["MO"], {"found": 1, "same_as_board": 1, "stored": 0, "dropped": {}, "kept": 0, "new": 0})
        self.assertEqual(got["TX"]["dropped"], {"US citizens / green card only": 1})
        self.assertRegex(jobbanks_report.text(report), r"KS\s+2\s+0\s+2\s+1\s+1\s+1\s+C2C only 1")

    def test_the_card_says_via_the_states_bank(self):
        out = api.jobs(self.conn, PID, "recommended", NOW, filters.parse({}))
        cards = {c["uid"]: c for c in out["jobs"]}
        self.assertEqual(cards["careeronestop:us:ZZTEST-KS-1"]["work"],
                         {"w2": True, "contract": False, "vague": False, "via": "via KansasWorks"})
        self.assertNotIn("careeronestop:us:ZZTEST-KS-2", cards)                  # C2C only
        self.assertEqual(out["not_w2"], 2)


class PageTest(unittest.TestCase):
    def test_the_tags_footer_and_switch_are_on_the_page(self):
        feed = (ROOT / "job_hunt_agent" / "static" / "js" / "feed.js").read_text()
        self.assertIn('icon("check", "ic sm"), job.work.contract ? "W2 contract" : "W2")', feed)     # GOAL36 §3
        self.assertIn("Vague repost", feed)
        self.assertIn("hidden: not W2", feed)
        settings = (ROOT / "job_hunt_agent" / "static" / "js" / "settings.js").read_text()
        self.assertRegex(settings, re.compile(r'\["sources", "Job sources"'))
        self.assertIn('api("/api/settings/jobbanks", { on: on.checked })', settings)


if __name__ == "__main__":
    unittest.main()
