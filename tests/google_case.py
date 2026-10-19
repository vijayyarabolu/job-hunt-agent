"""Shared setUp for the Google tests: a temp JOBHUNT tree (config, data, profiles, applications, DB), the fake
Google on 127.0.0.1 with JOB_HUNT_AGENT_GOOGLE_BASE pointing at it, a Desktop client file and (optionally) a token."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from job_hunt_agent import config, db
from job_hunt_agent.google import api, auth
from tests.fake_google import FakeGoogle

PROFILE_YAML = "name: Jane Doe\nfamilies: [ml_engineer]\n"


class GoogleCase(unittest.TestCase):
    connect = True              # False: no token yet (login tests)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for name in ("config", "data", "profiles", "applications"):
            (self.root / name).mkdir()
        self.fake = FakeGoogle().start()
        self.patches = [mock.patch.object(config, "CONFIG", self.root / "config"),
                        mock.patch.object(config, "DATA", self.root / "data"),
                        mock.patch.object(config, "PROFILES", self.root / "profiles"),
                        mock.patch.object(config, "APPLICATIONS", self.root / "applications"),
                        mock.patch.object(config, "DB_PATH", self.root / "data" / "t.db"),
                        mock.patch.object(api, "_sleep", lambda s: None),
                        mock.patch.dict("os.environ", {"JOB_HUNT_AGENT_NO_NOTIFY": "1"}),
                        self.fake.env()]
        for p in self.patches:
            p.start()
        (self.root / "config" / "google_client.json").write_text(json.dumps(
            {"installed": {"client_id": "cid.apps.googleusercontent.com", "client_secret": "secret",
                           "redirect_uris": ["http://localhost"]}}))
        self.add_profile("jane", PROFILE_YAML)
        if self.connect:
            auth.save(self.fake.grant())
        self.conn = db.connect()
        self.conn.execute("INSERT INTO profiles(id, name) VALUES ('jane', 'Jane Doe')")

    def tearDown(self):
        self.conn.close()
        for p in reversed(self.patches):
            p.stop()
        self.fake.stop()
        self.tmp.cleanup()

    def add_profile(self, pid: str, yaml_text: str) -> Path:
        folder = self.root / "profiles" / pid
        folder.mkdir(exist_ok=True)
        (folder / "profile.yaml").write_text(yaml_text)
        return folder
