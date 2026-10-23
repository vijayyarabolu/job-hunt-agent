"""GOAL33 §3: logs are rotated once a day in place (launchd keeps them open), the macOS malloc noise is left out of the
archives, archives older than 7 days are deleted, and a Sheets outage is one line, not one per retry."""
import logging
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from job_hunt_agent import logrotate
from job_hunt_agent.google import GoogleError, runner
from tests.test_gsync import SyncCase

DAY = date(2026, 10, 22)
NOISE = "Python(51481) MallocStackLogging: can't turn off malloc stack logging because it was not enabled.\n"


class RotateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.logs = Path(self.tmp.name)
        self.serve = self.logs / "serve.log"
        self.serve.write_text("2026-10-21 23:59:00 INFO one request\n" + NOISE + "2026-10-22 00:01:00 INFO another\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_day_is_archived_without_the_noise_and_the_log_starts_empty(self):
        with self.serve.open("a") as writer:                 # like launchd: the writer stays open, in append mode
            made = logrotate.rotate_all(self.logs, DAY)
            writer.write("after\n")
        archive = self.logs / "archive" / "serve.log.2026-10-22"
        self.assertEqual(made, [archive])
        self.assertNotIn("MallocStackLogging", archive.read_text())
        self.assertEqual(archive.read_text().count("\n"), 2)
        self.assertEqual(self.serve.read_text(), "after\n")   # truncated in place, no hole before the new line

    def test_once_a_day(self):
        logrotate.rotate_all(self.logs, DAY)
        self.serve.write_text("later today\n")
        self.assertEqual(logrotate.rotate_all(self.logs, DAY), [])
        self.assertEqual(self.serve.read_text(), "later today\n")
        self.assertEqual(len(logrotate.rotate_all(self.logs, DAY + timedelta(days=1))), 1)

    def test_archives_older_than_seven_days_go(self):
        archive = self.logs / "archive"
        archive.mkdir()
        old, kept = archive / "serve.log.2026-10-14", archive / "serve.log.2026-10-15"
        old.write_text("x")
        kept.write_text("x")
        logrotate.rotate_all(self.logs, DAY)
        self.assertFalse(old.exists())
        self.assertTrue(kept.exists())

    def test_an_empty_log_is_left_alone(self):
        self.serve.write_text("")
        self.assertEqual(logrotate.rotate_all(self.logs, DAY), [])


class SyncOutageTest(SyncCase):
    """serve.log 10-19 to 10-21: 1,177 x "Google sync alex failed: token endpoint unreachable" per person while the
    Mac was offline (one per 30 s retry). Now one line when it starts failing and one when it works again."""

    def test_one_line_per_outage(self):
        self.link()
        down = GoogleError("token endpoint unreachable: <urlopen error nodename nor servname provided>")
        with self.assertLogs(runner.log, "DEBUG") as seen:
            with mock.patch.object(runner.gsync_cycle, "sync_profile", side_effect=down):
                for _ in range(3):
                    runner.run(self.conn)
            with mock.patch.object(runner.gsync_cycle, "sync_profile", return_value={}):
                runner.run(self.conn)
                runner.run(self.conn)
        loud = [r.getMessage() for r in seen.records if r.levelno >= logging.INFO]
        self.assertEqual(loud, ["Google sync jane failed: token endpoint unreachable: <urlopen error nodename nor servname provided>",
                                "Google sync jane works again"])

    def test_a_new_error_is_logged_again(self):
        self.link()
        with self.assertLogs(runner.log, "INFO") as seen:
            for err in ("token endpoint unreachable", "token endpoint unreachable", "HTTP 429 quota"):
                with mock.patch.object(runner.gsync_cycle, "sync_profile", side_effect=GoogleError(err)):
                    runner.run(self.conn)
        self.assertEqual(len(seen.records), 2)
