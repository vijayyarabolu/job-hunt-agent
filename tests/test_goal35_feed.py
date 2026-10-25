"""GOAL35 §2 ("a lot less jobs"): an ATS outage is not a dead board (BambooHR answered 404 for every company on
2026-10-21 and 1,933 boards went on the weekly schedule), a generic software title comes in by its JD's GenAI work,
and the per-day gate audit counts where a day's jobs go. No network."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from job_hunt_agent import config, db, feedaudit, fetch, outage, tiers, titles

T0 = datetime(2026, 10, 21, 14, 0, tzinfo=timezone.utc)
BAMBOO = [{"board": f"bamboohr:co{i}", "ats": "bamboohr", "key": f"co{i}", "company": f"Co {i}"} for i in range(5)]
LLM_JD = "Requirements:\n- Build LLM applications with RAG and AI agents in production.\n- Python and AWS."
JAVA_JD = "Requirements:\n- Java, Spring and Kafka services.\n- 3+ years of backend work."


def outcome(board: dict, status: str, http: int | None) -> outage.Outcome:
    return outage.Outcome(board["board"], status, http, fetch.host_of(board), fetch._board_bucket(board))


class OutageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.db")
        db.sync_boards(self.conn, BAMBOO)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def streaks(self):
        return [tuple(r) for r in self.conn.execute("SELECT dead_streak, fail_streak, active FROM boards ORDER BY board")]

    def test_every_bamboohr_company_has_its_own_host_but_one_bucket(self):
        hosts = {fetch.host_of(b) for b in BAMBOO}
        self.assertEqual(len(hosts), 5)
        self.assertEqual({fetch._board_bucket(b) for b in BAMBOO}, {"bamboohr.com"})

    def test_a_bucket_wide_404_is_an_outage_and_counts_against_no_board(self):
        rows = [outcome(b, "http_error", 404) for b in BAMBOO[:4]] + [outcome(BAMBOO[4], "ok", 200)]
        alerts, failed = outage.collapsed(rows)
        self.assertEqual(alerts, ["bamboohr.com: 4/5 boards failed"])
        self.assertEqual(set(failed), {b["board"] for b in BAMBOO[:4]})
        with db.tx(self.conn):
            self.conn.execute("UPDATE boards SET dead_streak=1, fail_streak=1")
            self.conn.execute("UPDATE boards SET dead_streak=4, dropped='dead: 4 straight 404/410 answers', active=0, "
                              "dropped_at='x' WHERE board='bamboohr:co0'")
        outage.forgive(self.conn, failed)
        self.assertEqual(self.streaks(), [(3, 0, 1), (0, 0, 1), (0, 0, 1), (0, 0, 1), (1, 1, 1)])
        self.assertIsNone(self.conn.execute("SELECT dropped FROM boards WHERE board='bamboohr:co0'").fetchone()[0])

    def test_a_few_dead_boards_on_a_healthy_bucket_stay_dead(self):
        rows = [outcome(BAMBOO[0], "http_error", 404)] + [outcome(b, "ok", 200) for b in BAMBOO[1:]]
        self.assertEqual(outage.collapsed(rows), ([], {}))

    def test_two_boards_are_too_few_to_call_an_outage(self):
        self.assertEqual(outage.collapsed([outcome(b, "http_error", 404) for b in BAMBOO[:2]]), ([], {}))

    def fetch_row(self, run_id, board, status, http):
        self.conn.execute("INSERT INTO fetch_runs(run_id, board, status, http_status) VALUES (?, ?, ?, ?)",
                          (run_id, board, status, http))

    def test_heal_puts_boards_a_past_outage_marked_dead_back_on_their_schedule(self):
        outage_run, normal_run = db.start_run(self.conn, "t", T0), db.start_run(self.conn, "t", T0 + timedelta(hours=1))
        with db.tx(self.conn):
            for b in BAMBOO:
                self.fetch_row(outage_run, b["board"], "http_error", 404)
            self.fetch_row(normal_run, "bamboohr:co4", "http_error", 404)      # a real 404 later, others fine
            for i in range(3):
                self.conn.execute("INSERT INTO boards(board, ats, key, company) VALUES (?, 'bamboohr', ?, 'X')",
                                  (f"bamboohr:ok{i}", f"ok{i}"))
                self.fetch_row(normal_run, f"bamboohr:ok{i}", "ok", 200)
            self.conn.execute("UPDATE boards SET dead_streak=1 WHERE board LIKE 'bamboohr:co%'")
        self.assertEqual(outage.heal(self.conn, fetch._board_bucket), 4)
        dead = [r[0] for r in self.conn.execute("SELECT board FROM boards WHERE dead_streak > 0")]
        self.assertEqual(dead, ["bamboohr:co4"])
        self.assertEqual(outage.heal(self.conn, fetch._board_bucket), 0)         # idempotent

    def test_a_healed_board_is_due_on_its_tier_not_in_a_week(self):
        self.assertEqual(tiers.interval_for({"dead_streak": 0}, 3), tiers.INTERVALS[3])
        self.assertEqual(tiers.interval_for({"dead_streak": 1}, 3), tiers.DEAD_INTERVAL)


class JdContentMatchTest(unittest.TestCase):
    def test_a_generic_title_with_genai_work_in_the_jd_is_an_ai_engineer_job(self):
        for title in ("Software Engineer", "Senior Software Engineer, Backend", "Member of Technical Staff",
                      "Backend Engineer", "Full Stack Engineer", "Founding Engineer", "Forward Deployed Engineer"):
            with self.subTest(title=title):
                self.assertEqual(titles.match_job(title, LLM_JD), ["ai_engineer"])
                self.assertEqual(titles.match_job(title, JAVA_JD), [])

    def test_excluded_titles_stay_out_whatever_the_jd_says(self):
        for title in ("Staff Software Engineer", "Principal Software Engineer", "Software Engineering Manager",
                      "Software Engineer Intern", "Firmware Software Engineer"):
            with self.subTest(title=title):
                self.assertEqual(titles.match_job(title, LLM_JD), [])

    def test_a_title_match_wins_and_is_unchanged(self):
        self.assertEqual(titles.match_job("Machine Learning Engineer", JAVA_JD), ["ml_engineer"])

    def test_a_person_without_the_ai_engineer_family_gets_no_jd_matches(self):
        self.assertEqual(titles.match_job("Software Engineer", LLM_JD, ["data_scientist"]), [])

    def test_the_fetcher_reads_the_jd_of_generic_titles(self):
        wanted = titles.compile_union(["ai_engineer", "ml_engineer"])
        self.assertTrue(wanted("Software Engineer"))
        self.assertTrue(wanted("Machine Learning Engineer"))
        # GOAL36 §1: a Staff title is a label and a rank now, and the person's families no longer limit what is read
        self.assertTrue(wanted("Staff Software Engineer"))
        self.assertFalse(wanted("Accountant"))
        self.assertTrue(titles.compile_union(["data_scientist"])("Software Engineer"))


class FeedAuditTest(unittest.TestCase):
    def test_every_job_of_the_day_stops_at_exactly_one_gate_or_is_shown(self):
        from tests.test_goal9_a import PROFILE, seed
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "profiles" / "tester" / "resumes").mkdir(parents=True)
            (root / "profiles" / "tester" / "profile.yaml").write_text(PROFILE)
            with mock.patch.object(config, "DB_PATH", root / "t.db"), mock.patch.object(config, "PROFILES", root / "profiles"):
                conn = db.connect()
                seed(conn)
                rows = feedaudit.funnel(conn, config.profile("tester"), db.utcnow(), 7)
                conn.close()
        self.assertEqual(len(rows), 7)
        for row in rows:
            gates = feedaudit.GATES[feedaudit.GATES.index("past_employer"):feedaudit.GATES.index("clone_merged") + 1]
            stopped = sum(row[g] for g in gates)
            self.assertEqual(row["my_families"], stopped + row["shown"], row)
        self.assertGreater(sum(r["my_families"] for r in rows), 0)

    def test_it_refuses_the_live_db(self):
        with self.assertRaises(SystemExit):
            feedaudit.main(["--db", str(config.DB_PATH), "--profiles", "x"])


if __name__ == "__main__":
    unittest.main()
