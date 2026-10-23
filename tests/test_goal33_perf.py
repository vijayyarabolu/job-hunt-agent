"""GOAL33 §3b / §4: Applied read every application's events without an index (the table rebuilds of versions 9 and
24 left application_events with none): 60 ms of the 170 ms page on 580 rows, and its p95 over 1 s under load.
Version 37 adds app_events_app (app_id, to_status, at), so the "applied at" time of each row is one index search."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from job_hunt_agent import appliedview, config, db


class AppliedIndexTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "t.db"
        self.patch = mock.patch.object(config, "DB_PATH", self.path)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def plan_of_rows(self, conn) -> str:
        seen = []
        conn.set_trace_callback(seen.append)
        try:
            appliedview._rows(conn, "alex")
        finally:
            conn.set_trace_callback(None)
        sql = next(s for s in seen if "FROM application_events e" in s)
        return " ".join(r[3] for r in conn.execute("EXPLAIN QUERY PLAN " + sql))

    def test_the_applied_time_of_each_row_is_an_index_search(self):
        conn = db.connect()
        try:
            self.assertIn("USING COVERING INDEX app_events_app (app_id=? AND to_status=?)", self.plan_of_rows(conn))
        finally:
            conn.close()

    def test_a_version_36_db_gets_the_index(self):
        db.connect().close()
        raw = sqlite3.connect(self.path)
        raw.executescript("DROP INDEX IF EXISTS app_events_app; PRAGMA user_version=36;")
        raw.close()
        conn = db.connect()
        try:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], db.SCHEMA_VERSION)
            names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE tbl_name = 'application_events'")}
            self.assertIn("app_events_app", names)
        finally:
            conn.close()


class StartupWarmTest(unittest.TestCase):
    """GOAL33 §4: after a restart the first Applied took 1.2 s and the first Archived 1.1 s (the H-1B index, the ATS
    profiles and the vendor models load on first use). The start-up warm-up opens both once per person; the warm-up
    after every change (live.watch) does not, so a fetch run costs no more than before."""
    def warm(self, **kw):
        from job_hunt_agent import api, feedcache, health, stats
        with mock.patch.object(api, "jobs") as jobs, mock.patch.object(api, "_industries"), \
                mock.patch.object(feedcache, "ranked"), mock.patch.object(health, "summary"), \
                mock.patch.object(stats, "report"), mock.patch.object(appliedview, "board") as board, \
                mock.patch.object(config, "profile_ids", return_value=["alex", "jordan"]), \
                mock.patch.object(config, "profile", return_value={"families": []}), \
                mock.patch.object(db, "connect"):
            api.warm(db.utcnow(), **kw)
        return [c.args[2] for c in jobs.call_args_list], [c.args[1] for c in board.call_args_list]

    def test_the_first_warm_up_opens_applied_and_archived(self):
        tabs, boards = self.warm(first=True)
        self.assertEqual(tabs.count("archived"), 2)
        self.assertEqual(boards, ["alex", "jordan"])

    def test_the_warm_up_after_a_change_does_not(self):
        tabs, boards = self.warm()
        self.assertNotIn("archived", tabs)
        self.assertEqual(boards, [])

    def test_the_server_starts_the_first_one(self):
        src = (Path(__file__).resolve().parent.parent / "job_hunt_agent" / "server.py").read_text()
        self.assertIn('threading.Thread(target=_warm, kwargs={"first": True}', src)


class AppliedIndexThreadsTest(unittest.TestCase):
    """GOAL33 review: the index keeps a SequenceMatcher per applied name (f275029); set_seq1 then ratio() is not
    atomic, and the request threads and the warm-up share one index, so a thread could score another's company.
    Each thread now has its own matchers."""
    def test_each_thread_compares_with_its_own_matcher(self):
        import threading
        from job_hunt_agent import applied
        index = applied.AppliedIndex((applied.AppliedRef(applied.company_key("Acme Robotics"), "t", None, None),))
        used = []

        def compare():
            index._close("acmerobotic", "acmerobotics")
            used.append(next(iter(index._local.matchers.values())))
        compare()
        other = threading.Thread(target=compare)
        other.start()
        other.join()
        self.assertIsNot(used[0], used[1])
        self.assertTrue(index._close("acmerobotic", "acmerobotics"))


if __name__ == "__main__":
    unittest.main()
