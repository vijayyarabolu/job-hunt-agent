import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

from job_hunt_agent import db, notify

NOW = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)
PROFILE = {"id": "p1", "name": "Tester"}
BATCH = [({"uid": "u1", "company": "Acme", "title": "Data Scientist"}, SimpleNamespace(worth=80)),
         ({"uid": "u2", "company": "Beta", "title": "ML Engineer"}, SimpleNamespace(worth=70))]


class DeliveryTest(unittest.TestCase):
    """Release review M5: a push is recorded only after a channel took it, so a failed send is retried."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(os.path.join(self.tmp.name, "t.db"))

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _recorded(self) -> list[str]:
        return [r[0] for r in self.conn.execute("SELECT uid FROM notifications ORDER BY uid")]

    def _notify(self, delivered: bool) -> int:
        with mock.patch.object(notify, "pending_pushes", return_value=BATCH), \
                mock.patch.object(notify, "send", return_value=delivered) as send:
            count = notify.notify_new(self.conn, [PROFILE], NOW)
        send.assert_called_once()
        return count

    def test_failed_send_records_nothing(self):
        self.assertEqual(self._notify(False), 0)
        self.assertEqual(self._recorded(), [])

    def test_delivered_send_records_batch(self):
        self.assertEqual(self._notify(True), 2)
        self.assertEqual(self._recorded(), ["u1", "u2"])

    def test_collapsed_copies_are_recorded_with_their_lead(self):
        batch = [({**BATCH[0][0], "also": (("u9", "Dallas, TX"),)}, BATCH[0][1])]
        with mock.patch.object(notify, "pending_pushes", return_value=batch), \
                mock.patch.object(notify, "send", return_value=True):
            notify.notify_new(self.conn, [PROFILE], NOW)
        self.assertEqual(self._recorded(), ["u1", "u9"])          # the Dallas copy never pushes on its own

    def test_osascript_reports_exit_code(self):
        for code, expected in ((0, True), (1, False)):
            done = subprocess.CompletedProcess([], code)
            with mock.patch.object(notify.subprocess, "run", return_value=done), \
                    mock.patch.object(notify.sys, "platform", "darwin"):
                self.assertIs(notify._osascript("t", "m"), expected)
        with mock.patch.object(notify.sys, "platform", "linux"):
            self.assertFalse(notify._osascript("t", "m"))          # only a Mac has banners

    def test_osascript_missing_is_not_delivered(self):
        with mock.patch.object(notify.subprocess, "run", side_effect=OSError("no osascript")), \
                mock.patch.object(notify.sys, "platform", "darwin"):
            self.assertFalse(notify._osascript("t", "m"))

    def test_send_true_when_telegram_takes_it(self):
        with mock.patch.dict(os.environ, {"JOB_HUNT_AGENT_NO_NOTIFY": ""}), \
                mock.patch.object(notify, "_osascript", return_value=False), \
                mock.patch.object(notify, "telegram_settings", return_value=("tok", "chat")), \
                mock.patch.object(notify, "post_json") as post:
            self.assertTrue(notify.send("t", "m"))
        post.assert_called_once()

    def test_send_disabled_is_not_delivered(self):
        with mock.patch.dict(os.environ, {"JOB_HUNT_AGENT_NO_NOTIFY": "1"}):
            self.assertFalse(notify.send("t", "m"))


class DismissedTest(unittest.TestCase):
    """A job dismissed in the UI is never pushed (verifier M8 round 3)."""

    def test_dismissed_job_is_not_pending(self):
        ranked = [({**row, "also": ()}, SimpleNamespace(push=True, worth=s.worth)) for row, s in BATCH]
        with mock.patch.object(notify.ranking, "rank", return_value=ranked), \
                mock.patch.object(notify.ranking, "collapse", side_effect=lambda conn, r: r), \
                mock.patch.object(notify.ranking, "dismissed", return_value={"u1"}):
            conn = mock.MagicMock()
            conn.execute.return_value = []
            pending = notify.pending_pushes(conn, PROFILE, NOW)
        self.assertEqual([row["uid"] for row, _ in pending], ["u2"])


class TelegramChatTest(unittest.TestCase):
    """Release review (partner access): a profile's own Telegram chat wins over the shared one."""

    SECRETS = {"telegram_token": "tok", "telegram_chat": "shared", "telegram_chats": {"partner": "p-chat"}}

    def test_profile_chat_overrides_shared_chat(self):
        with mock.patch.object(notify.config, "_read_yaml", return_value=self.SECRETS), \
                mock.patch.dict(os.environ, {"JOB_HUNT_AGENT_TELEGRAM_TOKEN": "", "JOB_HUNT_AGENT_TELEGRAM_CHAT": ""}):
            self.assertEqual(notify.telegram_settings("partner"), ("tok", "p-chat"))
            self.assertEqual(notify.telegram_settings("alex"), ("tok", "shared"))
            self.assertEqual(notify.telegram_settings(), ("tok", "shared"))
            self.assertTrue(notify.own_chat("partner"))
            self.assertFalse(notify.own_chat("alex"))

    def test_push_goes_to_the_profiles_chat(self):
        with mock.patch.object(notify.config, "_read_yaml", return_value=self.SECRETS), \
                mock.patch.dict(os.environ, {"JOB_HUNT_AGENT_NO_NOTIFY": "", "JOB_HUNT_AGENT_TELEGRAM_TOKEN": "",
                                             "JOB_HUNT_AGENT_TELEGRAM_CHAT": ""}), \
                mock.patch.object(notify, "_osascript", return_value=False), \
                mock.patch.object(notify, "post_json") as post:
            self.assertTrue(notify.send("t", "m", "partner"))
        self.assertEqual(post.call_args[0][1]["chat_id"], "p-chat")


if __name__ == "__main__":
    unittest.main()
