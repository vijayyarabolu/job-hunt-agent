"""GOAL33 §3b: the DB stops growing by logs nobody reads. A job that matches none of the user's title families gets no JD
versions and no job events (its JD text was never kept either); once a day, after the verified backup, job events
older than 14 days, the JD versions and events of jobs outside the user's families, and the per-board fetch log of runs
older than 14 days are archived (gzip JSON lines in data/archive/) and then deleted, a few thousand rows per
transaction. Jobs, applications and everything the user typed are never touched."""
import gzip
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

from job_hunt_agent import config, db, fetch, prune, store, tracker
from job_hunt_agent.ats.common import FetchResult, make_job
from tests.test_goal8_flow import JD, NOW

OLD = NOW - timedelta(days=20)


class PruneCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.patches = [mock.patch.object(config, "DB_PATH", self.root / "t.db"), mock.patch.object(config, "DATA", self.root)]
        for p in self.patches:
            p.start()
        self.conn = db.connect()
        db.sync_boards(self.conn, [{"board": "lever:acme", "ats": "lever", "key": "acme", "company": "Acme"}])

    def tearDown(self):
        self.conn.close()
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def write(self, when, *titles):
        items = [make_job(ats="lever", key="acme", ext_id=str(n), company="Acme", title=t, location="Austin, TX",
                          url=f"https://jobs.lever.co/acme/{n}", posted_at=db.iso(when), description=JD)
                 for n, t in enumerate(titles)]
        run = db.start_run(self.conn, "test", when)
        store.write_board(self.conn, db.board(self.conn, "lever:acme"), FetchResult(items, 200, n_raw=len(items)), "ok", run,
                          when, ["fam:ml_engineer"], 5)
        return [i["uid"] for i in items]

    def count(self, table, uid=None):
        if uid:
            return self.conn.execute(f"SELECT count(*) FROM {table} WHERE uid = ?", (uid,)).fetchone()[0]
        return self.conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


class WriteTest(PruneCase):
    def test_a_job_outside_his_families_gets_no_versions_or_events(self):
        ml, chef = self.write(NOW, "Machine Learning Engineer", "Pastry Chef")
        self.assertEqual((self.count("jd_versions", ml), self.count("job_events", ml)), (1, 1))
        self.assertEqual((self.count("jd_versions", chef), self.count("job_events", chef)), (0, 0))
        self.assertEqual(self.count("jobs"), 2)                                # the job itself is kept


class PruneTest(PruneCase):
    def setUp(self):
        super().setUp()
        self.old_ml, self.chef = self.write(OLD, "Machine Learning Engineer", "Pastry Chef")
        tracker.ensure_profile(self.conn, "alex")
        with db.tx(self.conn):                                                 # what the chef's job had before the fix
            self.conn.execute("INSERT INTO jd_versions(uid, content_hash, seen_at) VALUES (?, 'h', ?)", (self.chef, db.iso(NOW)))
            self.conn.execute("INSERT INTO job_events(run_id, uid, at, kind) VALUES (1, ?, ?, 'new')", (self.chef, db.iso(NOW)))
            self.conn.execute("INSERT INTO applications(profile, company, role_title, status, updated_at) "
                              "VALUES ('alex', 'Acme', 'Pastry Chef', 'applied', ?)", (db.iso(OLD),))
        self.write(NOW, "Machine Learning Engineer", "Pastry Chef")             # a recent run: both seen again

    def test_a_dry_run_counts_and_changes_nothing(self):
        before = [self.count(t) for t in ("job_events", "jd_versions", "fetch_runs")]
        got = prune.run(self.conn, NOW, apply=False)
        self.assertEqual(got["job_events"], 2)                                 # the 20-day-old one, the chef's
        self.assertEqual(got["jd_versions"], 1)
        self.assertEqual(got["fetch_runs"], 0)                                 # the board's newest rows stay
        self.assertEqual([self.count(t) for t in ("job_events", "jd_versions", "fetch_runs")], before)

    def test_archived_then_deleted_and_nothing_else_touched(self):
        jobs, apps = self.count("jobs"), self.count("applications")
        got = prune.run(self.conn, NOW, apply=True, batch=1)
        self.assertEqual((got["job_events"], got["jd_versions"], got["fetch_runs"]), (2, 1, 0))
        self.assertEqual(self.count("job_events", self.chef), 0)
        old = self.conn.execute("SELECT count(*) FROM job_events WHERE at < ?", (db.iso(NOW - timedelta(days=14)),)).fetchone()[0]
        self.assertEqual(old, 0)
        self.assertEqual(self.count("jd_versions", self.old_ml), 1)            # a family job's versions stay
        self.assertEqual((self.count("jobs"), self.count("applications")), (jobs, apps))
        archived = {p.name.split("-")[0]: [json.loads(ln) for ln in gzip.open(p, "rt")]
                    for p in (self.root / "archive").glob("*.jsonl.gz")}
        self.assertEqual(sorted(archived), ["jd_versions", "job_events"])
        self.assertEqual(len(archived["job_events"]), 2)
        self.assertEqual(archived["jd_versions"][0]["uid"], self.chef)

    def test_a_boards_newest_fetch_rows_stay_whatever_their_age(self):
        for n in range(prune.KEEP_PER_BOARD + 2):                             # a board fetched once a week, long ago
            self.write(OLD - timedelta(days=7 * (n + 1)), "Machine Learning Engineer")
        old = self.conn.execute("SELECT count(*) FROM fetch_runs WHERE run_id IN (SELECT id FROM runs WHERE started_at < ?)",
                                (db.iso(NOW - timedelta(days=prune.KEEP_DAYS)),)).fetchone()[0]
        got = prune.run(self.conn, NOW, apply=True)
        self.assertEqual(got["fetch_runs"], old - prune.KEEP_PER_BOARD)       # the newest 5 by run are all old here
        self.assertEqual(len(db.prior_statuses(self.conn, "lever:acme", prune.KEEP_PER_BOARD)), prune.KEEP_PER_BOARD)

    def test_after_the_daily_backup_only(self):
        with mock.patch.object(fetch.db, "backup", return_value=None), mock.patch.object(fetch.prune, "run") as ran:
            fetch._daily(self.conn, NOW)
        ran.assert_not_called()                                                # no new verified backup today: no prune
        with mock.patch.object(fetch.db, "backup", return_value=self.root / "b.db"), mock.patch.object(fetch.prune, "run") as ran:
            fetch._daily(self.conn, NOW)
        ran.assert_called_once()
