"""GOAL33 §3b: connecting never fails with "database is locked" while another process writes: busy_timeout comes
first, the journal is switched to WAL only on a file not in WAL yet, synchronous=NORMAL, the WAL is capped after a
checkpoint, and a connect that still meets a lock tries again."""
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from job_hunt_agent import db, server

HOLD = """
import sqlite3, sys, time
c = sqlite3.connect(sys.argv[1], isolation_level=None)
c.execute("BEGIN IMMEDIATE"); c.execute("CREATE TABLE IF NOT EXISTS zz(x)"); c.execute("INSERT INTO zz VALUES (1)")
print("held", flush=True); time.sleep(float(sys.argv[2])); c.execute("COMMIT")
"""


class ConnectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "t.db"
        db.connect(self.path).close()                       # created, in WAL, schema set up

    def tearDown(self):
        self.tmp.cleanup()

    def test_settings_on_every_connection(self):
        conn = db.connect(self.path)
        self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertEqual(conn.execute("PRAGMA synchronous").fetchone()[0], 1)          # NORMAL
        self.assertEqual(conn.execute("PRAGMA busy_timeout").fetchone()[0], db.BUSY_TIMEOUT_MS)
        self.assertEqual(conn.execute("PRAGMA journal_size_limit").fetchone()[0], db.WAL_LIMIT_BYTES)
        self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        conn.close()

    def test_a_wal_file_is_not_asked_to_switch_again(self):
        seen = []
        real = sqlite3.connect

        def spy(*a, **k):
            conn = real(*a, **k)
            conn.set_trace_callback(seen.append)
            return conn
        with mock.patch.object(db.sqlite3, "connect", spy):
            db.connect(self.path).close()
        self.assertNotIn("PRAGMA journal_mode=WAL", seen)
        self.assertLess(seen.index(f"PRAGMA busy_timeout={db.BUSY_TIMEOUT_MS}"), seen.index("PRAGMA journal_mode"))

    def test_connecting_while_another_process_writes(self):
        holder = subprocess.Popen([sys.executable, "-c", HOLD, str(self.path), "1.5"], stdout=subprocess.PIPE, text=True)
        self.assertEqual(holder.stdout.readline().strip(), "held")
        started = time.monotonic()
        conn = db.connect(self.path)                       # read-only work never waits on the writer
        self.assertLess(time.monotonic() - started, 1.0)
        conn.execute("SELECT count(*) FROM jobs").fetchone()
        with db.tx(conn):                                   # a write waits for it (busy_timeout), then goes through
            conn.execute("CREATE TABLE IF NOT EXISTS mine(x)")
        conn.close()
        holder.wait(10)
        holder.stdout.close()

    def test_a_lock_at_connect_is_tried_again(self):
        calls = []
        real = db._open

        def flaky(path):
            calls.append(path)
            if len(calls) < 3:
                raise sqlite3.OperationalError("database is locked")
            return real(path)
        with mock.patch.object(db, "_open", flaky), mock.patch.object(db, "CONNECT_TRIES", (0.01, 0.01, 0.01)):
            db.connect(self.path).close()
        self.assertEqual(len(calls), 3)

    def test_other_errors_are_not_retried(self):
        with mock.patch.object(db, "_open", side_effect=sqlite3.OperationalError("disk I/O error")) as opened:
            with self.assertRaises(sqlite3.OperationalError):
                db.connect(self.path)
        self.assertEqual(opened.call_count, 1)

    def test_checkpoint_runs_without_waiting_for_a_reader(self):
        reader = db.connect(self.path)
        reader.execute("BEGIN")
        reader.execute("SELECT count(*) FROM jobs").fetchone()
        busy, *_ = db.checkpoint(db.connect(self.path))
        self.assertEqual(busy, 0)                          # PASSIVE: copied what it could, did not block
        reader.execute("COMMIT")


class WarmTest(unittest.TestCase):
    def test_a_warm_up_that_meets_a_lock_tries_again(self):
        tries = []

        def warm(now, first=False):
            tries.append(now)
            if len(tries) == 1:
                raise sqlite3.OperationalError("database is locked")
        with mock.patch.object(server.api, "warm", warm):
            server._warm(retry_s=(0.01,))
        self.assertEqual(len(tries), 2)

    def test_the_warm_up_after_a_change_never_waits_on_a_lock(self):
        tries = []

        def warm(now, first=False):
            tries.append(now)
            raise sqlite3.OperationalError("database is locked")
        with mock.patch.object(server.api, "warm", warm), mock.patch.object(server.time, "sleep") as slept:
            server._warm_after_change()
        self.assertEqual((len(tries), slept.call_count), (1, 0))
        self.assertIn('kwargs={"on_change": _warm_after_change}', Path(server.__file__).read_text())
