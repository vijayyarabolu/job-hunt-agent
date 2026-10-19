"""Tiered fetch (decision 005 B2): tiers and due rules, the per-bucket scheduler, the CPU budget, and the M9 closing
semantics on every new path (304 aging, raw-fingerprint fast path, budget-skipped boards, Workday partial reads,
sticky drops). No network."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from job_hunt_agent import buckets, coverage, db, fetch, runstats, store, tiers
from job_hunt_agent.ats.common import FetchResult, fast_item, make_job

T0 = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
SCOPES = ["fam:ml_engineer"]


def job(ext_id, ats="lever", key="acme", title="Machine Learning Engineer", posted="2026-10-02T10:00:00Z",
        location="San Francisco, CA", precision="exact", fp=None):
    item = make_job(ats=ats, key=key, ext_id=ext_id, company="Acme", title=title, location=location,
                    url=f"https://x/{ext_id}", posted_at=posted, precision=precision, description="Build ML.")
    return {**item, "raw_fp": fp if fp is not None else int(ext_id)}


class _Db(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.db")
        db.sync_boards(self.conn, [{"board": "lever:acme", "ats": "lever", "key": "acme", "company": "Acme"},
                                   {"board": "workday:acme/wd5/Ext", "ats": "workday", "key": "acme/wd5/Ext",
                                    "company": "Acme"}])

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def write(self, items, now, board="lever:acme", status="ok"):
        run_id = db.start_run(self.conn, "test", now)
        row = db.board(self.conn, board)
        return store.write_board(self.conn, row, FetchResult(items, 200, n_raw=len(items)), status, run_id, now,
                                 SCOPES, 10)

    def not_modified(self, now, board="lever:acme"):
        run_id = db.start_run(self.conn, "test", now)
        return store.write_not_modified(self.conn, db.board(self.conn, board), run_id, now, 5)

    def one(self, sql, *args):
        return self.conn.execute(sql, args).fetchone()


class ClosingOnNewPathsTest(_Db):
    def test_304_ages_already_missing_jobs_and_closes_them_like_a_200(self):
        self.write([job("1"), job("2")], T0)
        self.write([job("1")], T0 + timedelta(hours=1))              # job 2: miss 1
        self.not_modified(T0 + timedelta(hours=2))                     # same list again: miss 2, but < 4 h missing
        self.assertIsNone(self.one("SELECT closed_at FROM jobs WHERE ext_id='2'")[0])
        out = self.not_modified(T0 + timedelta(hours=6))
        self.assertEqual(out["n_closed"], 1)
        self.assertEqual(self.one("SELECT closed_at FROM jobs WHERE ext_id='2'")[0], db.iso(T0 + timedelta(hours=1)))
        self.assertIsNone(self.one("SELECT closed_at FROM jobs WHERE ext_id='1'")[0])
        self.assertEqual(self.one("SELECT http_status, status FROM fetch_runs ORDER BY run_id DESC")[:], (304, "ok"))

    def test_304_never_rewrites_seen_rows(self):
        self.write([job("1")], T0)
        self.not_modified(T0 + timedelta(hours=1))
        self.assertEqual(self.one("SELECT last_seen, miss_count FROM jobs")[:], (db.iso(T0), 0))

    def test_fast_path_matches_observe_for_unchanged_open_rows(self):
        self.write([job("1"), job("2")], T0)
        fast = [fast_item("lever", "acme", "1", "Machine Learning Engineer", 1)]
        out = self.write(fast + [job("2")], T0 + timedelta(hours=1))
        self.assertEqual((out["n_new"], out["n_match"]), (0, 2))
        self.assertEqual(self.one("SELECT last_seen, miss_count FROM jobs WHERE ext_id='1'")[:],
                         (db.iso(T0 + timedelta(hours=1)), 0))

    def test_reappearing_closed_job_takes_the_slow_path_and_reopens(self):
        self.write([job("1"), job("2")], T0)
        for hours in (1, 6):
            self.write([job("1")], T0 + timedelta(hours=hours))
        self.assertIsNotNone(self.one("SELECT closed_at FROM jobs WHERE ext_id='2'")[0])
        self.write([job("1"), job("2")], T0 + timedelta(hours=7))   # not fast: the row is closed
        self.assertIsNone(self.one("SELECT closed_at FROM jobs WHERE ext_id='2'")[0])
        self.assertEqual(self.one("SELECT count(*) FROM job_events WHERE kind='reopened'")[0], 1)

    def test_workday_partial_reads_never_close_and_mark_stale_after_7_days(self):
        wd = [job(str(n), ats="workday", key="acme/wd5/Ext", precision="day", posted="2026-10-02T00:00:00Z")
              for n in (1, 2)]
        self.write(wd, T0, board="workday:acme/wd5/Ext")
        self.write(wd[:1], T0 + timedelta(days=1), board="workday:acme/wd5/Ext")
        self.assertEqual(self.one("SELECT closed_at, stale FROM jobs WHERE ext_id='2'")[:], (None, 0))
        self.write(wd[:1], T0 + timedelta(days=8), board="workday:acme/wd5/Ext")
        self.assertEqual(self.one("SELECT closed_at, stale FROM jobs WHERE ext_id='2'")[:], (None, 1))

    def test_workday_partial_read_is_not_a_partial_status(self):
        self.write([job(str(n), ats="workday", key="acme/wd5/Ext", precision="day") for n in range(20)], T0,
                   board="workday:acme/wd5/Ext")
        board = {"board": "workday:acme/wd5/Ext", "ats": "workday", "key": "acme/wd5/Ext"}
        small = FetchResult([job("1", ats="workday", key="acme/wd5/Ext", precision="day")], 200, n_raw=1)
        self.assertEqual(fetch.board_status(self.conn, board, small, T0 + timedelta(hours=1)), ("ok", False))

    def test_list_only_resighting_keeps_the_content_hash(self):
        self.write([job("1", title="Accountant")], T0)
        before = self.one("SELECT content_hash FROM jobs")[0]
        bare = {**job("1", title="Accountant"), "description": ""}
        self.write([bare], T0 + timedelta(hours=1))
        self.assertEqual(self.one("SELECT content_hash FROM jobs")[0], before)
        self.assertEqual(self.one("SELECT count(*) FROM job_events WHERE kind='changed'")[0], 0)


class TiersTest(_Db):
    def test_tier_rule(self):
        self.assertEqual(tiers.tier_for("2026-09-20T00:00:00Z", T0), 1)
        self.assertEqual(tiers.tier_for("2026-08-20T00:00:00Z", T0), 2)
        self.assertEqual(tiers.tier_for("2026-06-01T00:00:00Z", T0), 3)
        self.assertEqual(tiers.tier_for(None, T0), 3)

    def test_t1_needs_a_us_match_by_first_posted(self):
        self.write([job("1", location="Berlin, Germany")], T0)
        self.assertEqual(tiers.board_tiers(self.conn, T0), {})
        self.write([job("2")], T0)
        self.assertEqual(tiers.board_tiers(self.conn, T0), {"lever:acme": 1})

    def test_an_undated_posting_counts_from_first_seen(self):
        db.sync_boards(self.conn, [{"board": "jobvite:acme", "ats": "jobvite", "key": "acme", "company": "Acme"}])
        self.write([job("3", ats="jobvite", posted=None, precision="unknown")], T0, board="jobvite:acme")
        self.assertEqual(tiers.board_tiers(self.conn, T0 + timedelta(days=2)), {"jobvite:acme": 1})   # GOAL10
        self.assertEqual(tiers.board_tiers(self.conn, T0 + timedelta(days=45)), {"jobvite:acme": 2})
        self.assertEqual(coverage.live_boards(self.conn, T0 + timedelta(days=2)), {"jobvite:acme": "fetched"})
        self.write([job("4", posted=None, precision="unknown")], T0)          # Lever always dates its postings
        self.assertNotIn("lever:acme", tiers.board_tiers(self.conn, T0))

    def test_due_rules_and_budget_skipped_boards_carry_over(self):
        boards = [{"board": "lever:acme", "ats": "lever", "key": "acme", "company": "Acme", "review": False}]
        self.write([job("1")], T0)                                    # t1, last_ok_at = T0
        self.assertEqual(tiers.plan(self.conn, boards, T0 + timedelta(minutes=10)), [])
        due = tiers.plan(self.conn, boards, T0 + timedelta(minutes=25))
        self.assertEqual([(t.kind, t.tier) for t in due], [("list", 1)])
        # a run that never reached the board leaves last_fetch alone: it is still due next run
        self.assertEqual(len(tiers.plan(self.conn, boards, T0 + timedelta(minutes=55))), 1)
        tiers.after_fetch(self.conn, due[0], FetchResult([], 200, etag='W/"1"'), "ok", T0 + timedelta(minutes=55))
        self.assertEqual(tiers.plan(self.conn, boards, T0 + timedelta(minutes=60)), [])
        row = db.board(self.conn, "lever:acme")
        self.assertEqual((row["etag"], row["tier"]), ('W/"1"', 1))

    def test_new_boards_are_spread_and_seeded_once(self):
        boards = [{"board": f"lever:b{i}", "ats": "lever", "key": f"b{i}", "company": "B", "review": False}
                  for i in range(200)]
        db.sync_boards(self.conn, boards)
        due = tiers.plan(self.conn, boards, T0)
        self.assertLess(len(due), 60)                                 # ~1/48 of a day's worth per 30-min run
        later = tiers.plan(self.conn, boards, T0 + timedelta(hours=25))
        self.assertEqual(len(later), 200)

    def test_four_straight_404s_drop_the_board_and_sync_keeps_it_dropped(self):
        boards = [{"board": "lever:acme", "ats": "lever", "key": "acme", "company": "Acme", "review": False}]
        task = tiers.Task({**boards[0], **db.board(self.conn, "lever:acme")}, "list", 3, 0)
        for n in range(4):
            tiers.after_fetch(self.conn, task._replace(board={**task.board, **db.board(self.conn, "lever:acme")}),
                              FetchResult([], 404, error="gone"), "http_error", T0 + timedelta(days=7 * n))
        row = db.board(self.conn, "lever:acme")
        self.assertEqual((row["active"], row["dead_streak"]), (0, 4))
        self.assertTrue(row["dropped"].startswith("dead"))
        db.sync_boards(self.conn, [{"board": "lever:acme", "ats": "lever", "key": "acme", "company": "Acme"}])
        self.assertEqual(db.board(self.conn, "lever:acme")["active"], 0)
        self.assertEqual(tiers.plan(self.conn, boards, T0 + timedelta(days=60)), [])

    def test_dead_board_waits_a_week(self):
        self.assertEqual(tiers.interval_for({"dead_streak": 1}, 1), tiers.DEAD_INTERVAL)

    def test_robots_blocked_board_is_dropped_then_rechecked_daily(self):
        boards = [{"board": "lever:acme", "ats": "lever", "key": "acme", "company": "Acme", "review": False}]
        task = tiers.Task({**boards[0], **db.board(self.conn, "lever:acme")}, "list", 3, 0)
        tiers.after_fetch(self.conn, task, FetchResult([], 451, error="robots.txt disallows x"), "skipped", T0)
        self.assertTrue(db.board(self.conn, "lever:acme")["dropped"].startswith("robots:"))
        with mock.patch("job_hunt_agent.robots.allowed", return_value=True):
            tiers.plan(self.conn, boards, T0 + timedelta(hours=1), urls_of=lambda b: ["u"])
            self.assertIsNotNone(db.board(self.conn, "lever:acme")["dropped"])          # < 1 day: not yet
            tiers.plan(self.conn, boards, T0 + timedelta(hours=25), urls_of=lambda b: ["u"])
        self.assertIsNone(db.board(self.conn, "lever:acme")["dropped"])
        self.assertEqual(db.board(self.conn, "lever:acme")["active"], 1)

    def test_workday_terms_pass_when_matches_go_unseen(self):
        wd = [job("1", ats="workday", key="acme/wd5/Ext", precision="day")]
        self.write(wd, T0, board="workday:acme/wd5/Ext")
        boards = [{"board": "workday:acme/wd5/Ext", "ats": "workday", "key": "acme/wd5/Ext", "company": "Acme",
                   "review": False}]
        planned = tiers.plan(self.conn, boards, T0 + timedelta(days=4))
        self.assertEqual(sorted(t.kind for t in planned), ["list", "terms"])
        self.assertEqual([t.tier for t in planned if t.kind == "terms"], [tiers.TERMS_PRIORITY])


class SchedulerTest(unittest.TestCase):
    def task(self, name, tier=1):
        return tiers.Task({"board": name, "ats": "greenhouse"}, "list", tier, 0)

    def test_one_in_flight_per_bucket_and_three_for_workday(self):
        tasks = [self.task(f"g{i}") for i in range(3)] + [self.task(f"w{i}") for i in range(5)]
        sched = buckets.Scheduler(tasks, lambda t: "wd5.myworkdayjobs.com" if t.board["board"][0] == "w"
                                  else "boards-api.greenhouse.io", lambda t: 1.0, clock=lambda: 0.0)
        started = [b for b, _ in sched.ready()]
        self.assertEqual(started.count("wd5.myworkdayjobs.com"), 3)
        self.assertEqual(started.count("boards-api.greenhouse.io"), 1)

    def test_deadline_in_seconds_carries_the_rest_over(self):
        now = [0.0]
        tasks = [self.task(f"g{i}") for i in range(10)]
        sched = buckets.Scheduler(tasks, lambda t: "gh", lambda t: 30.0, deadline_s=100, clock=lambda: now[0])
        done = 0
        while True:
            ready = sched.ready()
            if not ready:
                break
            for name, task in ready:
                now[0] += 30
                sched.done(name, task)
                done += 1
        self.assertEqual((done, sched.carried()), (3, 7))
        with mock.patch.object(buckets.net, "bucket_stats", return_value={}):
            row = sched.rows(1)[0]
        self.assertEqual((row[7], row[8], row[9], row[10]), (10, 3, 3, 7))     # t1_due, t1_done, taken, carried

    def test_cpu_budget_stop_closes_every_bucket(self):
        sched = buckets.Scheduler([self.task("a"), self.task("b")], lambda t: t.board["board"], lambda t: 1.0)
        self.assertEqual(sched.ready(stop=True), [])
        self.assertEqual(sched.carried(), 2)

    def test_cancel_requeues(self):
        sched = buckets.Scheduler([self.task("a")], lambda t: "x", lambda t: 1.0)
        (name, task), = sched.ready()
        sched.cancel(name, task)
        self.assertEqual(sched.carried(), 1)

    def test_ewma_cost_in_seconds(self):
        self.assertAlmostEqual(buckets.ewma([1000, 3000]), 2.0)
        self.assertIsNone(buckets.ewma([]))


class BudgetTest(unittest.TestCase):
    def test_budget_spends_on_cpu_or_wall(self):
        cpu, wall = [0.0], [0.0]
        budget = runstats.Budget(cpu_s=200, wall_s=1200, clock=lambda: cpu[0], wall=lambda: wall[0])
        self.assertFalse(budget.spent())
        cpu[0] = 201
        self.assertTrue(budget.spent())
        other = runstats.Budget(cpu_s=200, wall_s=1200, clock=lambda: 0.0, wall=lambda: wall[0])
        wall[0] = 1300
        self.assertTrue(other.spent())

    def test_rlimit_sets_soft_and_hard_and_a_handler(self):
        with mock.patch.object(runstats.resource, "setrlimit") as setrlimit, \
                mock.patch.object(runstats.signal, "signal") as handler:
            runstats.limit_cpu()
        setrlimit.assert_called_once_with(runstats.resource.RLIMIT_CPU, (300, 330))
        handler.assert_called_once()

    def test_backoff_round_trips_through_host_state(self):
        from job_hunt_agent import net
        with tempfile.TemporaryDirectory() as tmp:
            conn = db.connect(Path(tmp) / "t.db")
            net._backoff.clear()
            net._backoff["boards-api.greenhouse.io"] = (datetime.now(timezone.utc).timestamp() + 3600, 1)
            alerts = runstats.save_backoff(conn, datetime.now(timezone.utc))
            self.assertIn("boards-api.greenhouse.io", alerts[0])
            net._backoff.clear()
            runstats.load_backoff(conn)
            self.assertIsNotNone(net.backoff_until("boards-api.greenhouse.io"))
            net._backoff.clear()
            conn.close()


if __name__ == "__main__":
    unittest.main()


class CoverageReportTest(_Db):
    """doctor's coverage numbers come from the DB alone (no network)."""

    def test_live_count_needs_a_us_posting_in_60_days(self):
        from job_hunt_agent import coverage
        now = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)
        self.write([job("1")], now)
        self.write([job("1", ats="workday", key="acme/wd5/Ext", precision="day", location="Berlin, Germany")], now,
                   board="workday:acme/wd5/Ext")
        with mock.patch.object(coverage.discover_run, "tokens", return_value={}), \
                mock.patch.object(coverage.discover_run, "last_refresh", return_value=None), \
                mock.patch.object(coverage.seedlists, "sp500", return_value={"acme": "Acme"}):
            rep = coverage.report(self.conn, now)
        self.assertEqual(rep["live_count"], 1)
        self.assertEqual(rep["by_ats"], {"lever": 1})
        self.assertEqual(rep["by_tier"], {"t1": 1})
        self.assertEqual(rep["sp500"], {"covered": 1, "of": 1})
        self.assertTrue(any("refresh" in a for a in rep["alerts"]))
        self.assertTrue(coverage.lines(rep)[0].startswith("coverage as of"))

    def test_posted_per_day_counts_title_matched_us_jobs_by_posting_day(self):
        from job_hunt_agent import coverage
        now = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)
        self.write([job("1", posted="2026-10-02T10:00:00Z"), job("2", posted="2026-10-02T23:00:00Z"),
                    job("3", posted="2026-10-06T08:00:00Z"),                   # today: not a complete day
                    job("4", posted="2026-10-03T08:00:00Z", title="Office Manager"),
                    job("5", posted="2026-10-03T08:00:00Z", location="Berlin, Germany")], now)
        got = coverage.posted_per_day(self.conn, now)
        self.assertEqual(got["per_day"], {"2026-10-02": 2})
        self.assertEqual(got["by_ats"], {"lever": 2})
        self.assertAlmostEqual(got["mean"], 2 / 7)

    def test_posted_per_day_counts_only_boards_the_feed_shows(self):
        from job_hunt_agent import coverage
        now = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)
        self.write([job("1", posted="2026-10-02T10:00:00Z")], now)
        self.write([job("9", ats="workday", key="acme/wd5/Ext", precision="day", posted="2026-10-02T10:00:00Z")],
                   now, board="workday:acme/wd5/Ext")
        self.conn.execute("UPDATE boards SET active = 0, dropped = 'robots: x' WHERE board = 'workday:acme/wd5/Ext'")
        got = coverage.posted_per_day(self.conn, now)
        self.assertEqual(got["per_day"], {"2026-10-02": 1})
        self.assertEqual(got["by_ats"], {"lever": 1})

    def test_bucket_alert_at_60_percent_of_deadline(self):
        from job_hunt_agent import coverage
        buckets.save(self.conn, [(1, "wd5.myworkdayjobs.com", 10, 0, 50.0, 600.0, 400.0, 5, 5, 5, 0),
                                 (1, "boards-api.greenhouse.io", 10, 9, 5.0, 600.0, 100.0, 5, 5, 5, 0)])
        alerts = {b["bucket"]: b["alert"] for b in coverage.bucket_load(self.conn)}
        self.assertEqual(alerts, {"wd5.myworkdayjobs.com": True, "boards-api.greenhouse.io": False})
