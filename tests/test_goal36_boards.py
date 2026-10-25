"""GOAL36 §2 on a throwaway DB: the state job bank employers get their own boards looked up once a day, rate limited;
a board is added only when one live request answers with open jobs (never guessed, a doubtful name is not added); the
fetch list then carries it; the per-state report counts employers, new companies, tried and boards found; the state
bank is also searched by the masters' skills. ZZ_TEST data, no network."""
import json
import os
import tempfile
from pathlib import Path
from unittest import mock

from job_hunt_agent import config, db, discover, employer_boards, registry, store
from job_hunt_agent.ats import careeronestop
from job_hunt_agent.ats.common import Ctx

from tests.test_goal8_flow import NOW, Goal8Case
from tests.test_goal35_jobbanks import FIXTURE, KEY_ENV

CURATED = [{"name": "Acme", "ats": "lever", "token": "acme"}]


class BoardsCase(Goal8Case):
    def setUp(self):
        super().setUp()
        self.data = tempfile.TemporaryDirectory()
        self.more = [mock.patch.object(config, "DATA", Path(self.data.name)), mock.patch.dict(os.environ, KEY_ENV),
                     mock.patch.object(config, "companies_raw", lambda: CURATED),
                     mock.patch.object(registry, "discovered_raw", lambda: [])]
        for p in self.more:
            p.start()
        db.sync_boards(self.conn, [registry.JOBBANK_BOARD])
        ctx = Ctx(search_terms=("machine learning engineer",), known_elsewhere=lambda co, t: co == "Acme")
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

    @staticmethod
    def finder(conn, employer, now):
        if employer["name"] == "ZZ_TEST Prairie Analytics":
            return {"name": employer["name"], "ats": "greenhouse", "token": "zztestprairie", "n_jobs": 12,
                    "how": "name probe"}
        return None


class DailyRunTest(BoardsCase):
    def test_the_employers_on_job_bank_postings(self):
        got = {e["name"]: sorted(e["states"]) for e in employer_boards.employers(self.conn, NOW)}
        self.assertEqual(got, {"ZZ_TEST Prairie Analytics": ["KS"], "ZZ_TEST Vendor Staffing": ["KS"],
                               "ZZ_TEST Lone Star Labs": ["TX"]})

    def test_a_found_board_goes_on_the_fetch_list_and_the_report_counts_per_state(self):
        report = employer_boards.run(self.conn, NOW, finder=self.finder)
        self.assertEqual(report["states"], {"KS": {"employers": 2, "new_companies": 2, "tried": 2, "boards": 1},
                                            "TX": {"employers": 1, "new_companies": 1, "tried": 1, "boards": 0}})
        self.assertEqual(report["added"], [{"name": "ZZ_TEST Prairie Analytics", "ats": "greenhouse",
                                            "token": "zztestprairie", "n_jobs": 12, "how": "name probe",
                                            "state": "KS"}])
        boards = {b["board"]: b for b in registry.boards()}
        self.assertEqual(boards["greenhouse:zztestprairie"]["source"], "jobbank")
        self.assertIn("lever:acme", boards)
        text = employer_boards.text(employer_boards.last_report())
        self.assertRegex(text, r"KS\s+2\s+2\s+2\s+1")
        self.assertIn("added: ZZ_TEST Prairie Analytics (greenhouse:zztestprairie, 12 open jobs, by name probe)", text)

    def test_the_next_day_tries_nobody_twice_and_knows_the_new_board(self):
        employer_boards.run(self.conn, NOW, finder=self.finder)
        calls = []
        again = employer_boards.run(self.conn, NOW, finder=lambda c, e, n: calls.append(e["name"]))
        self.assertEqual(calls, [])
        self.assertEqual(again["states"]["KS"]["new_companies"], 1)        # Prairie has its board now
        self.assertEqual(len(employer_boards.learned_raw()), 1)

    def test_at_most_the_limit_a_day(self):
        report = employer_boards.run(self.conn, NOW, limit=1, finder=self.finder)
        self.assertEqual(sum(v["tried"] for v in report["states"].values()), 1)
        self.assertEqual(report["left"], 2)
        self.assertIn("2 more employers wait for the next days", employer_boards.text(report))

    def test_a_broken_learned_file_is_skipped(self):
        employer_boards.boards_path().write_text("companies: [unclosed")
        with self.assertLogs("job_hunt_agent.employer_boards", "WARNING"):
            self.assertIn("lever:acme", {b["board"] for b in registry.boards()})


class FindBoardTest(BoardsCase):
    EMPLOYER = {"name": "ZZ_TEST Prairie Analytics", "states": {"KS"},
                "links": ["https://www.kansasworks.com/jobs/ZZTEST-KS-1",
                          "https://boards.greenhouse.io/zztestprairie/jobs/77"]}

    def test_the_postings_own_ats_link_is_verified_with_one_request(self):
        with mock.patch.object(discover, "_live_count", return_value=7) as live, \
                mock.patch.object(discover, "_discover_one", side_effect=AssertionError("not needed")):
            got = employer_boards.find_board(self.conn, self.EMPLOYER, NOW)
        self.assertEqual(live.call_count, 1)
        self.assertEqual((got["ats"], got["token"], got["n_jobs"], got["how"]),
                         ("greenhouse", "zztestprairie", 7, "the posting's own link"))

    def test_an_empty_or_missing_board_is_never_added(self):
        with mock.patch.object(discover, "_live_count", return_value=0), \
                mock.patch.object(discover, "_discover_one", return_value=[]):
            self.assertIsNone(employer_boards.find_board(self.conn, self.EMPLOYER, NOW))

    def test_a_board_whose_name_does_not_match_is_not_added(self):
        doubtful = [{"name": "ZZ_TEST Prairie Analytics", "ats": "lever", "token": "prairie", "n_jobs": 3,
                     "board_name": "Prairie Dental", "review": True, "note": "board name does not match"}]
        with mock.patch.object(discover, "_live_count", return_value=0), \
                mock.patch.object(discover, "_discover_one", return_value=doubtful):
            self.assertIsNone(employer_boards.find_board(self.conn, self.EMPLOYER, NOW))


class SearchTermsTest(BoardsCase):
    def test_the_state_bank_is_searched_by_the_masters_skills_too(self):
        got = careeronestop.terms(("machine learning", "AI engineer"))
        self.assertEqual(got[:2], ["machine learning", "AI engineer"])
        for skill in ("LLM", "RAG", "LangChain", "PyTorch", "NLP", "MLOps", "vector search"):
            self.assertIn(skill, got)
        self.assertEqual(len(got), len({t.lower() for t in got}))          # "machine learning" once
