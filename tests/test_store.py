"""store.write_board against a throwaway SQLite DB: idempotency, closing, reposts, seeding, staleness."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from job_hunt_agent import db, fresh, resignal, store
from job_hunt_agent.ats.common import FetchResult, make_job

T0 = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
SCOPES = ["fam:ml_engineer"]


def job(ext_id: str, ats="lever", key="acme", title="Machine Learning Engineer", posted="2026-10-02T10:00:00Z",
        location="San Francisco, CA", precision="exact", capped=False, req_id=None):
    return make_job(ats=ats, key=key, ext_id=ext_id, company="Acme", title=title, location=location,
                    url=f"https://x/{ext_id}", posted_at=posted, precision=precision,
                    description="Build ML systems with PyTorch.", capped=capped, req_id=req_id)


class _DbCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.db")
        self.boards = {}

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def write(self, items, now, ats="lever", key="acme", status="ok", count_misses=True, scopes=SCOPES):
        board_id = f"{ats}:{key}"
        if board_id not in self.boards:
            db.sync_boards(self.conn, [{"board": board_id, "ats": ats, "key": key, "company": "Acme"}])
            self.boards[board_id] = True
        run_id = db.start_run(self.conn, "test", now)
        row = db.board(self.conn, board_id)
        return store.write_board(self.conn, row, FetchResult(items, 200, n_raw=len(items)), status, run_id, now,
                                 scopes, 10, count_misses=count_misses)

    def rows(self, sql, *args):
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]


class StoreTest(_DbCase):

    def test_fetch_twice_creates_no_duplicates(self):
        items = [job("1"), job("2"), job("3")]
        first = self.write(items, T0)
        second = self.write(items, T0 + timedelta(minutes=30))
        self.assertEqual((first["n_new"], second["n_new"]), (3, 0))
        self.assertEqual(self.rows("SELECT count(*) n, count(DISTINCT uid) d FROM jobs")[0], {"n": 3, "d": 3})
        self.assertEqual(self.rows("SELECT count(*) n FROM job_events WHERE kind='new'")[0]["n"], 3)

    def test_job_closes_after_two_misses_and_four_hours_then_reopens(self):
        self.write([job("1"), job("2")], T0)
        self.write([job("1")], T0 + timedelta(hours=1))
        self.write([job("1")], T0 + timedelta(hours=2))
        self.assertIsNone(self.rows("SELECT closed_at FROM jobs WHERE ext_id='2'")[0]["closed_at"])
        result = self.write([job("1")], T0 + timedelta(hours=6))
        self.assertEqual(result["n_closed"], 1)
        closed = self.rows("SELECT closed_at, missing_since FROM jobs WHERE ext_id='2'")[0]
        self.assertEqual(closed["closed_at"], closed["missing_since"])
        self.write([job("1"), job("2")], T0 + timedelta(hours=7))
        self.assertIsNone(self.rows("SELECT closed_at FROM jobs WHERE ext_id='2'")[0]["closed_at"])
        kinds = [r["kind"] for r in self.rows("SELECT kind FROM job_events WHERE uid='lever:acme:2' ORDER BY id")]
        self.assertEqual(kinds, ["new", "closed", "reopened"])

    def test_partial_and_truncated_runs_never_count_misses(self):
        self.write([job("1"), job("2")], T0)
        self.write([job("1")], T0 + timedelta(hours=5), status="partial")
        self.write([job("1")], T0 + timedelta(hours=10), count_misses=False)
        self.assertEqual(self.rows("SELECT miss_count FROM jobs WHERE ext_id='2'")[0]["miss_count"], 0)

    def test_repost_with_new_id_increments_repost_count(self):
        self.write([job("1")], T0)
        self.write([], T0 + timedelta(hours=1))
        self.write([], T0 + timedelta(hours=6))
        self.write([job("99", title="Machine Learning Engineer (R-1234)")], T0 + timedelta(days=2))
        repost = self.rows("SELECT repost_count FROM jobs WHERE ext_id='99'")[0]
        self.assertEqual(repost["repost_count"], 1)
        self.assertEqual(len(self.rows("SELECT * FROM job_events WHERE kind='repost'")), 1)

    def test_same_run_siblings_are_not_reposts(self):
        self.write([job("1"), job("2")], T0)
        self.assertEqual(self.rows("SELECT sum(repost_count) s FROM jobs")[0]["s"], 0)

    def test_mutable_board_first_run_is_seed_then_new_jobs_are_fresh(self):
        now = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
        self.write([job("1", ats="ashby", posted="2026-10-02T11:00:00Z")], now, ats="ashby")
        later = now + timedelta(hours=1)
        self.write([job("1", ats="ashby", posted="2026-10-02T11:00:00Z"),
                    job("2", ats="ashby", posted="2026-10-02T12:30:00Z")], later, ats="ashby")
        rows = {r["ext_id"]: r for r in self.rows("SELECT * FROM jobs WHERE board='ashby:acme'")}
        self.assertEqual((rows["1"]["seed"], rows["2"]["seed"]), (1, 0))
        self.assertFalse(fresh.is_fresh(rows["1"], later))
        self.assertTrue(fresh.is_fresh(rows["2"], later))

    def test_workday_absence_is_stale_not_closed(self):
        wd = {"ats": "workday", "key": "acme/wd1/Ext", "precision": "day", "posted": "2026-10-01T00:00:00Z"}
        self.write([job("JR1", **wd)], T0, ats="workday", key="acme/wd1/Ext")
        self.write([], T0 + timedelta(days=3), ats="workday", key="acme/wd1/Ext")
        self.assertEqual(self.rows("SELECT stale, closed_at FROM jobs")[0], {"stale": 0, "closed_at": None})
        self.write([], T0 + timedelta(days=8), ats="workday", key="acme/wd1/Ext")
        self.assertEqual(self.rows("SELECT stale, closed_at FROM jobs")[0], {"stale": 1, "closed_at": None})

    def test_sql_guard_never_raises_or_nulls_first_posted_at(self):
        self.write([job("1", posted="2026-10-01T10:00:00Z")], T0)
        self.write([job("1", posted=None)], T0 + timedelta(hours=1))
        self.write([job("1", posted="2026-10-02T11:00:00Z")], T0 + timedelta(hours=2))
        row = self.rows("SELECT first_posted_at, posted_at FROM jobs")[0]
        self.assertEqual(row["first_posted_at"], "2026-10-01T10:00:00Z")

    def test_jd_change_logs_changed_event_and_version(self):
        self.write([job("1")], T0)
        edited = job("1")
        edited["description"] = "Build ML systems with PyTorch and Kubernetes."
        self.write([edited], T0 + timedelta(hours=1))
        self.assertEqual(len(self.rows("SELECT * FROM jd_versions WHERE uid='lever:acme:1'")), 2)
        self.assertEqual(len(self.rows("SELECT * FROM job_events WHERE kind='changed'")), 1)



class VerifierM3RegressionTest(_DbCase):
    """Milestone 3 verifier findings H1, H2, M2, L2."""

    def test_detail_less_resighting_keeps_detail_fields(self):
        full = make_job(ats="smartrecruiters", key="acme", ext_id="9", company="Acme", title="ML Engineer",
                        location="Austin, TX, US", country="us", url="https://x/9-ml-engineer", req_id="REF-9",
                        team="Search", posted_at="2026-10-02T10:00:00Z",
                        description="Build ranking models. 5+ years of experience. Pay: $150,000 - $190,000.")
        self.write([full], T0, ats="smartrecruiters")
        bare = make_job(ats="smartrecruiters", key="acme", ext_id="9", company="Acme", title="ML Engineer",
                        location="", url="https://x/9", posted_at="2026-10-02T10:00:00Z")
        self.write([bare], T0 + timedelta(minutes=30), ats="smartrecruiters")
        row = self.rows("SELECT * FROM jobs WHERE ext_id='9'")[0]
        self.assertEqual((row["location"], row["is_us"], row["salary_min"], row["min_years"], row["req_id"],
                          row["url"], row["has_team"]), ("Austin, TX, US", 1, 150000, 5, "REF-9",
                                                         "https://x/9-ml-engineer", 1))
        self.assertEqual(self.rows("SELECT count(*) n FROM job_events WHERE kind='changed'")[0]["n"], 0)
        self.assertEqual(self.rows("SELECT count(*) n FROM jd_versions")[0]["n"], 1)

    def test_empty_location_jobs_are_not_reposts_of_each_other(self):
        self.write([job("1", location="")], T0)
        self.write([job("1", location=""), job("2", location="")], T0 + timedelta(days=1))
        self.assertEqual(self.rows("SELECT sum(repost_count) s FROM jobs")[0]["s"], 0)

    def test_workday_seed_is_per_search_term(self):
        wd = {"ats": "workday", "key": "acme/wd1/Ext", "precision": "day", "posted": "2026-10-02T00:00:00Z"}
        board = {"ats": "workday", "key": "acme/wd1/Ext"}
        self.write([{**job("A", **wd), "scopes": ["term:llm"]}], T0, scopes=["term:llm"], **board)
        later = T0 + timedelta(hours=1)
        self.write([{**job("B", **wd), "scopes": ["term:llm", "term:rag"]},
                    {**job("C", **wd), "scopes": ["term:rag"]}], later, scopes=["term:llm", "term:rag"], **board)
        seeds = {r["ext_id"]: r["seed"] for r in self.rows("SELECT ext_id, seed FROM jobs")}
        self.assertEqual(seeds, {"A": 1, "B": 0, "C": 1})

    def test_http_error_writes_nothing_counts_no_misses_and_raises_fail_streak(self):
        self.write([job("1"), job("2")], T0)
        self.write([job("1")], T0 + timedelta(hours=5), status="http_error")
        self.write([], T0 + timedelta(hours=10), status="http_error")
        rows = self.rows("SELECT miss_count, closed_at FROM jobs")
        self.assertTrue(all(r["miss_count"] == 0 and r["closed_at"] is None for r in rows))
        self.assertEqual(self.rows("SELECT fail_streak FROM boards")[0]["fail_streak"], 2)
        self.assertEqual(self.rows("SELECT count(*) n FROM job_events")[0]["n"], 2)

    def test_future_dated_resighting_keeps_precision(self):
        self.write([job("1")], T0)
        self.write([job("1", posted="2027-01-01T00:00:00Z")], T0 + timedelta(hours=1))
        row = self.rows("SELECT precision, posted_at FROM jobs")[0]
        self.assertEqual((row["precision"], row["posted_at"]), ("exact", "2026-10-02T10:00:00Z"))


class RepostChainTest(_DbCase):
    """Decision 002: a repost of a recently ended req is chained (never fresh, ages from the chain origin)."""

    def close_prior(self, day: int, **kw):
        self.write([job("1", **kw)], T0, **{k: v for k, v in kw.items() if k in ("ats", "key")})
        for hours in (0, 5):
            self.write([], T0 + timedelta(days=day, hours=hours), **{k: v for k, v in kw.items() if k in ("ats", "key")})

    def chain(self, ext_id):
        return self.rows("SELECT * FROM jobs WHERE ext_id=?", ext_id)[0]

    def test_repost_after_close_is_chained_not_fresh(self):
        self.close_prior(29)
        now = T0 + timedelta(days=30)
        self.write([job("2", posted="2026-11-01T10:00:00Z")], now)
        row = self.chain("2")
        self.assertEqual((row["chain_origin"], row["chain_n"]), ("2026-10-02T10:00:00Z", 1))
        self.assertFalse(fresh.is_fresh(row, now + timedelta(hours=2)))
        self.assertGreater(fresh.age_hours(row, now), 30 * 24)

    def test_day_precision_workday_repost_after_going_stale_is_chained(self):
        kw = {"ats": "workday", "key": "acme/wd1/ext", "precision": "day", "posted": "2026-10-02T00:00:00Z"}
        self.write([job("JR1", req_id="JR1", **kw)], T0, ats="workday", key="acme/wd1/ext")
        self.write([], T0 + timedelta(days=8), ats="workday", key="acme/wd1/ext")
        self.assertEqual(self.chain("JR1")["stale"], 1)
        now = T0 + timedelta(days=9)
        kw["posted"] = "2026-10-11T00:00:00Z"
        self.write([job("JR2", req_id="JR2", **kw)], now, ats="workday", key="acme/wd1/ext")
        self.assertEqual(self.chain("JR2")["chain_n"], 1)
        self.assertFalse(fresh.is_fresh(self.chain("JR2"), now))

    def test_revival_after_ninety_days_is_fresh(self):
        self.close_prior(10)
        now = T0 + timedelta(days=111)
        self.write([job("2", posted="2027-01-21T10:00:00Z")], now)
        row = self.chain("2")
        self.assertEqual((row["chain_origin"], row["chain_n"]), (None, 0))
        self.assertEqual(row["repost_count"], 1)
        self.assertTrue(fresh.is_fresh(row, now))

    def test_open_prior_with_same_req_id_is_chained(self):
        self.write([job("1", req_id="R-77")], T0)
        self.write([job("1", req_id="R-77"), job("2", req_id="R-77", posted="2026-10-03T10:00:00Z")],
                   T0 + timedelta(days=1))
        self.assertEqual(self.chain("2")["chain_n"], 1)

    def test_open_prior_with_other_req_id_is_concurrent_headcount(self):
        self.write([job("1", req_id="R-1")], T0)
        now = T0 + timedelta(days=1)
        self.write([job("1", req_id="R-1"), job("2", req_id="R-2", posted="2026-10-03T10:00:00Z")], now)
        row = self.chain("2")
        self.assertEqual((row["chain_origin"], row["chain_n"], row["repost_count"]), (None, 0, 1))
        self.assertTrue(fresh.is_fresh(row, now + timedelta(hours=1)))


class ResignalBacklogTest(_DbCase):
    def test_undated_postings_stored_by_a_first_fetch_become_backlog(self):
        # rows the first fetch of GOAL16's new boards stored as new before 22eedde; a later posting stays new
        undated = dict(ats="jobvite", posted=None, precision="unknown")
        self.write([job("1", **undated), job("2", **undated)], T0, ats="jobvite")
        self.conn.execute("UPDATE jobs SET seed = 0")                  # as the old code stored them
        self.write([job("1", **undated), job("2", **undated), job("3", **undated)], T0 + timedelta(hours=1),
                   ats="jobvite")
        dry = resignal.run(self.conn, dry=True)
        self.assertEqual(dry["backlog"], 2)
        self.assertEqual(self.rows("SELECT sum(seed) s FROM jobs")[0]["s"], 0, "a dry run writes nothing")
        self.assertEqual(resignal.run(self.conn)["backlog"], 2)
        seeds = {r["ext_id"]: r["seed"] for r in self.rows("SELECT ext_id, seed FROM jobs")}
        self.assertEqual(seeds, {"1": 1, "2": 1, "3": 0})
        self.assertEqual(resignal.run(self.conn)["backlog"], 0, "once only")


if __name__ == "__main__":
    unittest.main()


class FetchLockTest(unittest.TestCase):
    """The 08:00 digest waits for a running fetch instead of digesting stale data (verifier M8)."""

    def test_run_waits_for_the_lock_then_proceeds_or_gives_up(self):
        import fcntl
        import threading
        from unittest import mock
        from job_hunt_agent import config, fetch
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(config, "DATA", Path(tmp)), \
                mock.patch.object(fetch, "LOCK_POLL_S", 0.05), \
                mock.patch.object(fetch, "_run_locked", return_value={"ran": True}), \
                mock.patch.object(fetch.db, "connect"), mock.patch.object(fetch.db, "last_run_started", return_value=None):
            holder = open(Path(tmp) / "fetch.lock", "w")
            fcntl.flock(holder, fcntl.LOCK_EX)
            self.assertEqual(fetch.run(wait_s=0.2), {"skipped": "another fetch is running"})
            threading.Timer(0.2, holder.close).start()                  # the other run finishes
            self.assertEqual(fetch.run(wait_s=5), {"ran": True})


class BoardDriftTest(_DbCase):
    """Release review C1: a renamed JSON key or a dead board must never silently close jobs or leave ghosts."""

    def setUp(self):
        super().setUp()
        from job_hunt_agent import fetch
        self.fetch = fetch
        self.items = [job(str(n)) for n in range(10)]
        self.write(self.items, T0)                                       # last_ok_seen = 10
        self.board = {"board": "lever:acme", "ats": "lever", "key": "acme"}

    def status(self, result, now, verdict):
        calls = []

        def probe(conn, board_id, exclude):
            calls.append(board_id)
            return verdict
        return self.fetch.board_status(self.conn, self.board, result, now, probe=probe), calls

    def test_schema_error_is_drift_and_closes_nothing(self):
        result = FetchResult([], 200, error="schema: no 'jobs' list", n_parse_err=1)
        self.assertEqual(self.status(result, T0, "closed")[0], ("drift", False))

    def test_big_drop_is_accepted_only_when_the_missing_jobs_are_really_closed(self):
        for _ in range(2):                                               # two partial runs first
            self.write([self.items[0]], T0 + timedelta(hours=1), status="partial")
        small = FetchResult([self.items[0]], 200, n_raw=1)
        self.assertEqual(self.status(small, T0 + timedelta(hours=2), "open"), (("drift", False), ["lever:acme"]))
        self.assertEqual(self.status(small, T0 + timedelta(hours=2), "unknown")[0], ("partial", False))
        self.assertEqual(self.status(small, T0 + timedelta(hours=2), "closed")[0], ("ok", False))

    def test_an_emptied_board_is_no_longer_suspect_once_accepted(self):
        """Verifier M9: after an empty list is accepted, the board's size is 0, not the old 10."""
        self.write([], T0 + timedelta(hours=1))
        self.assertEqual(db.board(self.conn, "lever:acme")["last_ok_seen"], 0)
        self.write(self.items[:2], T0 + timedelta(hours=2), count_misses=False)           # truncated: only raises
        self.assertEqual(db.board(self.conn, "lever:acme")["last_ok_seen"], 2)
        self.write([], T0 + timedelta(hours=3), count_misses=False)
        self.assertEqual(db.board(self.conn, "lever:acme")["last_ok_seen"], 2)

    def test_dead_board_closes_its_jobs_after_a_day_when_probes_agree(self):
        gone = FetchResult([], 404, error="HTTP 404")
        self.assertEqual(self.status(gone, T0 + timedelta(hours=3), "closed"), (("http_error", False), []))
        self.assertEqual(self.status(gone, T0 + timedelta(hours=30), "open")[0], ("drift", False))   # moved: alert
        verdict, _ = self.status(gone, T0 + timedelta(hours=30), "closed")
        self.assertEqual(verdict, ("http_error", True))
        run_id = db.start_run(self.conn, "test", T0 + timedelta(hours=30))
        out = store.write_board(self.conn, db.board(self.conn, "lever:acme"), gone, "http_error", run_id,
                                T0 + timedelta(hours=30), SCOPES, 10, close_all=True)
        self.assertEqual(out["n_closed"], 10)
        self.assertEqual(self.rows("SELECT count(*) n FROM jobs WHERE closed_at IS NULL")[0]["n"], 0)
        self.assertEqual(self.rows("SELECT min(closed_at) c FROM jobs")[0]["c"], db.iso(T0))   # last seen ok


class LivenessTest(unittest.TestCase):
    def test_ashby_with_a_renamed_list_key_is_unknown_not_closed(self):
        from unittest import mock
        from job_hunt_agent import liveness
        job_row = {"board": "ashby:acme", "ext_id": "1", "url": "https://x/1"}
        with mock.patch.object(liveness, "get_json", return_value={"postings": []}):
            self.assertEqual(liveness.live_status(job_row), "unknown")
        with mock.patch.object(liveness, "get_json", return_value={"jobs": [{"id": "2"}]}):
            self.assertEqual(liveness.live_status(job_row), "closed")


class BackupTest(unittest.TestCase):
    """Release review data safety: the daily backup also saves the gitignored profile files; everything is 0600/0700."""

    def test_backup_tars_profiles_and_locks_down_modes(self):
        import stat
        import tarfile
        from unittest import mock
        from job_hunt_agent import config
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "profiles" / "me").mkdir(parents=True)
            for name in ("profile.yaml", "baseline.json", "known_skills.txt", "template.docx"):
                (root / "profiles" / "me" / name).write_text("x")
            (root / "data").mkdir()
            conn = db.connect(root / "data" / "job_hunt_agent.db")
            with mock.patch.object(config, "PROFILES", root / "profiles"):
                (root / "data" / "backups").mkdir()                       # today's DB copy predates the tarball
                (root / "data" / "backups" / f"job_hunt_agent-{db.utcnow():%Y%m%d}.db").write_text("")
                self.assertIsNone(db.backup(conn, root / "data" / "backups"))
                (root / "data" / "backups" / f"job_hunt_agent-{db.utcnow():%Y%m%d}.db").unlink()
                target = db.backup(conn, root / "data" / "backups")
                again = db.backup(conn, root / "data" / "backups")
            conn.close()
            tars = list((root / "data" / "backups").glob("profiles-*.tar.gz"))
            with tarfile.open(tars[0]) as tar:
                names = sorted(tar.getnames())
            modes = {p.name: stat.S_IMODE(p.stat().st_mode) for p in [root / "data", *(root / "data").rglob("*")]}
        self.assertTrue(target and again is None)                        # once a day
        self.assertEqual(len(tars), 1)
        self.assertEqual(names, ["me/baseline.json", "me/known_skills.txt", "me/profile.yaml"])
        self.assertEqual(modes.pop("data"), 0o700)
        self.assertEqual(modes.pop("backups"), 0o700)
        self.assertEqual(set(modes.values()), {0o600}, modes)

    def test_failed_profile_tar_leaves_no_part_file_and_still_locks_down(self):
        import stat
        import tarfile
        from unittest import mock
        from job_hunt_agent import config
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "profiles" / "me").mkdir(parents=True)
            (root / "profiles" / "me" / "profile.yaml").write_text("x")
            (root / "data").mkdir()
            conn = db.connect(root / "data" / "job_hunt_agent.db")
            with mock.patch.object(config, "PROFILES", root / "profiles"), \
                    mock.patch.object(tarfile.TarFile, "add", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    db.backup(conn, root / "data" / "backups")
            conn.close()
            names = sorted(p.name for p in (root / "data" / "backups").iterdir())
            modes = {stat.S_IMODE(p.stat().st_mode) for p in (root / "data" / "backups").iterdir()}
        self.assertEqual(len(names), 1)                                     # today's DB copy only, no .part
        self.assertTrue(names[0].startswith("job_hunt_agent-"))
        self.assertEqual(modes, {0o600})

