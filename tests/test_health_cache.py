"""every open tab asks /api/health once a minute, and each answer scanned all ~600k stored jobs for the drift
notice (2-5 s on the live Mac). The drift and open-job count are now kept until a fetch run or a new job changes them."""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from job_hunt_agent import config, db, health
from tests.test_server import seed_job


class HealthKeepTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = mock.patch.object(config, "DB_PATH", Path(self.tmp.name) / "t.db")
        self.patch.start()
        self.conn = db.connect()
        seed_job(self.conn)
        health._KEPT.clear()

    def tearDown(self):
        self.conn.close()
        self.patch.stop()
        self.tmp.cleanup()

    def _drift_scans(self) -> int:
        seen = []
        self.conn.set_trace_callback(seen.append)
        try:
            out = health.health(self.conn, datetime.now(timezone.utc))
        finally:
            self.conn.set_trace_callback(None)
        self.assertIn("drift", out)
        return sum("avg(description IS NOT NULL)" in s for s in seen)

    def test_nothing_changed_means_no_second_scan(self):
        self.assertEqual(self._drift_scans(), 1)
        self.assertEqual(self._drift_scans(), 0)

    def test_a_new_job_means_a_fresh_answer(self):
        self.assertEqual(self._drift_scans(), 1)
        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(jobs)") if r[1] not in ("uid", "ext_id")]
        self.conn.execute(f"INSERT INTO jobs (uid, ext_id, {', '.join(cols)}) SELECT 'lever:acme:2', '2', "
                          f"{', '.join(cols)} FROM jobs LIMIT 1")
        self.assertEqual(self._drift_scans(), 1)


if __name__ == "__main__":
    unittest.main()


class HealthAtOnceTest(unittest.TestCase):
    """on the 1.6 GB live DB the "still scoring" count and the drift check take 10-20 s whenever their 5-10
    minute keep runs out. /api/health now answers the last result at once and refreshes it in the background."""
    def setUp(self):
        health._SNAP.clear()

    def test_answers_the_last_result_at_once_and_refreshes_behind(self):
        calls, gate = [], __import__("threading").Event()

        def slow(conn, now):
            calls.append(now)
            if len(calls) > 1:
                gate.wait(5)
            return {"n": len(calls)}
        with mock.patch.object(health, "health", slow), mock.patch.object(db, "connect", lambda *a: mock.Mock()):
            first = health.summary(mock.Mock(), datetime.now(timezone.utc), key="k")
            with mock.patch.object(health.time, "monotonic", lambda: 10 ** 9):
                second = health.summary(mock.Mock(), datetime.now(timezone.utc), key="k")
                gate.set()
                health._SNAP["k"]["thread"].join(5)
            third = health.summary(mock.Mock(), datetime.now(timezone.utc), key="k")
        self.assertEqual((first, second, third), ({"n": 1}, {"n": 1}, {"n": 2}))

    def test_a_failed_refresh_keeps_the_last_result(self):
        answers = iter([{"n": 1}])

        def flaky(conn, now):
            try:
                return next(answers)
            except StopIteration:
                raise RuntimeError("db busy")
        with mock.patch.object(health, "health", flaky), mock.patch.object(db, "connect", lambda *a: mock.Mock()):
            health.summary(mock.Mock(), datetime.now(timezone.utc), key="k")
            with mock.patch.object(health.time, "monotonic", lambda: 10 ** 9):
                health.summary(mock.Mock(), datetime.now(timezone.utc), key="k")
                health._SNAP["k"]["thread"].join(5)
            self.assertEqual(health.summary(mock.Mock(), datetime.now(timezone.utc), key="k"), {"n": 1})
