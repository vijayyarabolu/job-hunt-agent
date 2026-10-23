"""GOAL31 §1: the live DB was found malformed (b-tree pages pointing past the end of the file) while a virtual
machine with ~/Documents shared into it held it open. SQLite's locks do not cross such a share, so job-hunt-agent now refuses
to open its DB from a shared or network mount. The daily backup is checked before it counts: a bad one is kept
apart as .db.bad, raises a notice, and never pushes a good backup out of the rotation."""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from job_hunt_agent import config, db, notify

MOUNTS = """/dev/vda1 / ext4 rw,relatime 0 0
proc /proc proc rw 0 0
mac /mnt/mac virtiofs rw,relatime 0 0
share /mnt/mac/Documents/job_hunt_agent/data 9p rw 0 0
tmpfs /tmp tmpfs rw 0 0
"""


class SharedMountTest(unittest.TestCase):
    def test_the_mount_of_a_path_is_its_longest_mount_point(self):
        self.assertEqual(db.mount_type("/mnt/mac/Documents/job_hunt_agent/data/job_hunt_agent.db", MOUNTS), "9p")
        self.assertEqual(db.mount_type("/mnt/mac/Documents/other.db", MOUNTS), "virtiofs")
        self.assertEqual(db.mount_type("/home/me/job_hunt_agent.db", MOUNTS), "ext4")
        self.assertEqual(db.mount_type("/mnt/macintosh/x.db", MOUNTS), "ext4")        # a prefix, not a mount point

    def test_a_db_on_a_shared_mount_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(db.sys, "platform", "linux"), \
                mock.patch.object(db, "_mounts", lambda: f"share {Path(tmp).resolve()} virtiofs rw 0 0\n"), \
                mock.patch.dict("os.environ", {}):
            os.environ.pop(db.ALLOW_SHARED, None)
            with self.assertRaises(db.SharedDatabase) as caught:
                db.connect(Path(tmp) / "job_hunt_agent.db")
            self.assertIn("virtiofs", str(caught.exception))
            self.assertFalse((Path(tmp) / "job_hunt_agent.db").exists())                    # not even created

    def test_a_copy_on_a_share_can_be_opened_on_purpose(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(db.sys, "platform", "linux"), \
                mock.patch.object(db, "_mounts", lambda: f"share {Path(tmp).resolve()} 9p rw 0 0\n"), \
                mock.patch.dict("os.environ", {db.ALLOW_SHARED: "1"}):
            db.connect(Path(tmp) / "job_hunt_agent.db").close()

    def test_local_disks_and_the_mac_are_not_checked_against_shares(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(db.sys, "platform", "linux"), \
                mock.patch.object(db, "_mounts", lambda: f"/dev/vda1 / ext4 rw 0 0\ntmpfs {Path(tmp).resolve()} tmpfs rw 0 0\n"):
            db.connect(Path(tmp) / "job_hunt_agent.db").close()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(db.sys, "platform", "darwin"), \
                mock.patch.object(db, "_mounts", side_effect=AssertionError("read /proc/mounts on a Mac")):
            db.connect(Path(tmp) / "job_hunt_agent.db").close()


class VerifiedBackupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "profiles").mkdir()
        self.backups = root / "data" / "backups"
        self.backups.mkdir(parents=True)
        self.conn = db.connect(root / "data" / "job_hunt_agent.db")
        self.patches = [mock.patch.object(config, "PROFILES", root / "profiles"),
                        mock.patch.object(notify, "send", return_value=True)]
        self.sent = [p.start() for p in self.patches][1]
        self.today = self.backups / f"job_hunt_agent-{db.utcnow():%Y%m%d}.db"

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.conn.close()
        self.tmp.cleanup()

    def test_a_good_backup_is_checked_then_counted(self):
        self.assertEqual(db.backup(self.conn, self.backups), self.today)
        self.assertEqual(sqlite3.connect(self.today).execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(list(self.backups.glob("*.part")), [])
        self.sent.assert_not_called()

    def test_a_bad_backup_is_kept_apart_and_raises_a_notice(self):
        old = [self.backups / f"job_hunt_agent-202609{d:02d}.db" for d in range(1, db.BACKUPS_KEPT + 3)]
        for path in old:
            path.write_text("a good one")
        with mock.patch.object(db, "_check", return_value="Tree 4 page 571955 cell 0: invalid page number 573268"):
            self.assertIsNone(db.backup(self.conn, self.backups))
        self.assertFalse(self.today.exists())
        self.assertTrue(self.today.with_name(self.today.name + ".bad").exists())
        self.assertTrue(all(p.exists() for p in old))                    # no good backup rotated out by a bad one
        self.sent.assert_called_once()
        self.assertIn("failed its check", self.sent.call_args.args[1])
        notice = self.conn.execute("SELECT kind, text FROM app_notices").fetchone()
        self.assertEqual(notice["kind"], "db")
        self.assertIn("invalid page number", notice["text"])

    def test_a_failed_check_does_not_count_as_todays_backup(self):
        with mock.patch.object(db, "_check", return_value="malformed"):
            db.backup(self.conn, self.backups)
        self.assertEqual(db.backup(self.conn, self.backups), self.today)  # the next run tries again


if __name__ == "__main__":
    unittest.main()
