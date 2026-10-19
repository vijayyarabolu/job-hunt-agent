"""GOAL24 verifier findings: a board name shorter than the filer's ("Anthropic" / "Anthropic, PBC") still finds its
filings (and a quick rejection there is overruled), never a stranger's ("Apple" / "Applebee's"); a college that shares
a key with a big company does not make it cap-exempt; a JD's "no sponsorship" with an exception is not No
sponsorship ("no exceptions" is); two filers named alike, a longer board name and a franchise's "dba Allstate" give
no match; the board finder skips boards job-hunt-agent already has (any case)."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from job_hunt_agent import config, db, resignal, signals
from job_hunt_agent.h1b import boards, label, load, names

from tests.test_goal24_h1b import LCA_HEAD, H1bCase, RECENT, lca, write_book
from tests.test_goal8_flow import seed_job

ROWS = [*(lca(f"C-{i}", "Anthropic, PBC", RECENT, title="Research Scientist", fein="10-0000001") for i in range(6)),
        lca("C-9", "Anthropic LLC", RECENT, title="Office Manager", soc="43-1011.00", fein="10-0000009"),
        *(lca(f"A-{i}", "Applebee's Services", RECENT, title="Data Analyst", fein="20-0000002") for i in range(6)),
        lca("T-1", "The Citadel", RECENT, naics="611310", fein="30-0000003"),
        *(lca(f"Q-{i}", "Citadel LLC", RECENT, title="Quantitative Researcher", naics="523920", fein="30-0000004")
          for i in range(5)),
        # two big filers named "Archer ..." of a size, one "Cadence ..." and a small "Cadence, Inc."; a franchise
        # agency that files "dba Allstate"
        *(lca(f"AD-{i}", "Archer Daniels Midland Company", RECENT, fein="40-0000001") for i in range(8)),
        *(lca(f"AA-{i}", "Archer Aviation Inc.", RECENT, fein="40-0000002") for i in range(6)),
        lca("AT-1", "Archer Technologies LLC", RECENT, title="Office Manager", soc="43-1011.00", fein="40-0000008"),
        *(lca(f"CD-{i}", "Cadence Design Systems, Inc.", RECENT, fein="40-0000003") for i in range(6)),
        lca("CI-1", "Cadence, Inc.", RECENT, title="Office Manager", soc="43-1011.00", fein="40-0000004"),
        *(lca(f"FX-{i}", "FedEx Freight, Inc.", RECENT, fein="40-0000005") for i in range(12)),
        *(lca(f"FD-{i}", "FedEx Dataworks, Inc.", RECENT, fein="40-0000006") for i in range(5)),
        lca("AL-1", "Foresight Insurance Agency Inc", RECENT, dba="ALLSTATE", fein="40-0000007")]


class FixesCase(H1bCase):
    def setUp(self):
        super().setUp()
        self.more = tempfile.TemporaryDirectory()
        write_book(Path(self.more.name) / "LCA_Disclosure_Data_FY2026_Q3.xlsx", LCA_HEAD, ROWS)
        load.load_dir(self.conn, Path(self.more.name), log=lambda _m: None)
        label.forget()

    def tearDown(self):
        self.more.cleanup()
        super().tearDown()


class LookupTest(FixesCase):
    def test_a_shorter_board_name_finds_the_big_filer(self):
        idx = label.index(self.conn)
        found = label.lookup(idx, "Anthropic")              # its own key: one small unrelated filing only
        self.assertEqual((found["name"], found["recent_data"]), ("Anthropic, PBC", 6))
        self.assertEqual(self.badge("Anthropic").kind, "sponsors")
        self.assertIsNone(label.lookup(idx, "Apple"))       # word by word: never Applebee's

    def test_a_quick_rejection_at_a_big_filer_known_by_a_short_name_is_overruled(self):
        with mock.patch.object(label, "LEARNED_OVERRULED", 5):
            self.reject("Anthropic", "2026-10-08", "2026-10-09")
            self.assertEqual(self.badge("Anthropic").kind, "sponsors")


class NameMatchTest(FixesCase):
    def test_two_big_filers_named_alike_are_no_answer_unless_one_is_far_bigger(self):
        idx = label.index(self.conn)
        self.assertIsNone(label.lookup(idx, "Archer"))      # 8 vs 6 data / AI filings; nor the small Archer Technologies
        self.assertEqual(label.lookup(idx, "FedEx")["name"], "FedEx Freight, Inc.")    # 12 vs 5

    def test_a_longer_board_name_is_not_a_shorter_filer(self):
        idx = label.index(self.conn)
        self.assertIsNone(label.lookup(idx, "Cadence Solutions"))         # not Cadence Design, not "Cadence, Inc."
        self.assertEqual(label.lookup(idx, "Cadence")["name"], "Cadence Design Systems, Inc.")

    def allstate(self, not_dba: frozenset):
        with mock.patch.object(names, "_not_dba", lambda: not_dba):
            load.rerollup(self.conn)
        label.forget()
        return label.lookup(label.index(self.conn), "Allstate")

    def test_a_trade_name_on_the_not_dba_list_names_nobody(self):
        self.assertEqual(self.allstate(frozenset())["name"], "Foresight Insurance Agency Inc")
        self.assertIsNone(self.allstate(frozenset({names._base("Foresight Insurance Agency")})))


class CapExemptTest(FixesCase):
    def test_a_college_sharing_the_key_does_not_make_the_company_cap_exempt(self):
        citadel = self.conn.execute("SELECT status, cap_exempt FROM h1b_companies WHERE key = ?",
                                    (names.key("Citadel"),)).fetchone()
        self.assertEqual(tuple(citadel), ("sponsors", None))

    def test_for_profit_names_are_not_cap_exempt(self):
        for name in ("Toyota Research Institute, Inc.", "Underwriters Laboratories Inc.", "University of Phoenix, Inc."):
            with self.subTest(name):
                self.assertIsNone(names.cap_exempt(name, "611310" if "University" in name else "541715"))
        self.assertEqual(names.cap_exempt("Massachusetts Institute of Technology", "611310"), "university")
        self.assertEqual(names.cap_exempt("Lawrence Livermore National Security, LLC", "541715"), "government lab")


class JdExceptionTest(unittest.TestCase):
    def test_an_exception_is_not_no_sponsorship(self):
        self.assertIsNone(signals.sponsorship("We are unable to sponsor visas for roles outside of engineering and product."))
        self.assertIsNone(signals.sponsorship("No sponsorship is offered, except for PhD research roles."))
        self.assertEqual(signals.sponsorship("We cannot sponsor visas for candidates located outside the US."), "no")
        self.assertEqual(signals.sponsorship("We are unable to sponsor work visas for this role."), "no")

    def test_no_exceptions_and_unless_authorized_are_still_no(self):
        for jd in ("No visa sponsorship is available for this position, no exceptions.",
                   "We do not sponsor visas unless you are authorized to work without sponsorship.",
                   "We are unable to sponsor visas unless otherwise noted in the posting.",
                   "Sponsorship is not available, except for TN status."):
            with self.subTest(jd):
                self.assertEqual(signals.sponsorship(jd), "no")

    def test_we_sponsor_needs_a_visa_word(self):
        self.assertIsNone(signals.sponsorship("We sponsor bright women from top universities through our scholarship."))
        self.assertEqual(signals.sponsorship("We will sponsor H-1B visas for this role."), "yes")
        self.assertEqual(signals.sponsorship("- We sponsor most work visas"), "yes")


class StoredLabelTest(FixesCase):
    def test_jd_rerun_rechecks_every_label(self):
        said = {"yes": "We sponsor bright women from top universities.", "None": "No visa sponsorship, no exceptions.",
                "no": "We cannot sponsor visas for roles outside of engineering."}
        uids = {old: seed_job(self.conn, f"zz-{old}", description=text) for old, text in said.items()}
        with db.tx(self.conn):
            for old, uid in uids.items():
                self.conn.execute("UPDATE jobs SET sponsorship = ? WHERE uid = ?", (None if old == "None" else old, uid))
        out = resignal.sponsorship_labels(self.conn)
        self.assertEqual({k: out["changed"].get(k) for k in ("yes->None", "None->no", "no->None")},
                         {"yes->None": 1, "None->no": 1, "no->None": 1})
        now = {old: self.conn.execute("SELECT sponsorship FROM jobs WHERE uid = ?", (uid,)).fetchone()[0]
               for old, uid in uids.items()}
        self.assertEqual(now, {"yes": None, "None": "no", "no": None})


class KnownBoardTest(unittest.TestCase):
    def test_a_board_the_app_already_has_is_not_added_again(self):
        with tempfile.TemporaryDirectory() as cfg, mock.patch.object(config, "CONFIG", Path(cfg)):
            Path(cfg, "companies.yaml").write_text(yaml.safe_dump({"companies": []}))
            board = {"name": "Centene", "ats": "workday", "token": "centene", "wd": "wd5", "site": "centene_external"}
            self.assertEqual(boards.add([board], known={"workday:centene/wd5/centene_external"}), [])
            self.assertEqual(len(boards.add([board])), 1)
