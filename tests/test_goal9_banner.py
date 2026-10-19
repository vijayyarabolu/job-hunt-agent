"""GOAL9 verifier findings on the notice line above the filters (tests/ui/goal9_banner.mjs, headless Chrome, phone
size, two people, /api/health held back 1.5 s by the browser): the remembered notice is drawn in the first frame so
nothing moves when health lands (it used to push every row 57 px down 4-5 s after they painted), a profile switch
shows the new person's notices at once, the drift dismissal ignores percentages, and the address drops the old
person after a switch."""
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from job_hunt_agent import config, db, health, server
from tests.test_goal7_ui import CHROME
from tests.test_goal9_a import DRIFT, PROFILE, seed

RUNNER = Path(__file__).parent / "ui" / "goal9_banner.mjs"
TOKEN = "ui9-banner-token"
RUN_TIMEOUT_S = 120
PEOPLE = {"tester": PROFILE, "other": PROFILE.replace("name: Tess Tester", "name: Olive Other")}


@unittest.skipUnless(shutil.which("node") and Path(CHROME).exists(), "needs node and Google Chrome")
class Goal9BannerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        for pid, text in PEOPLE.items():
            (root / "profiles" / pid / "resumes").mkdir(parents=True)
            (root / "profiles" / pid / "profile.yaml").write_text(text)
        cls.patches = [mock.patch.object(config, "DB_PATH", root / "ui.db"),
                       mock.patch.object(config, "PROFILES", root / "profiles"),
                       mock.patch.object(health, "drift", return_value=list(DRIFT))]
        for p in cls.patches:
            p.start()
        conn = db.connect()
        seed(conn)
        conn.close()
        cls.srv = server.make_server(lan=False, port=0)
        cls.srv.token = TOKEN
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        out = subprocess.run(["node", str(RUNNER), f"http://localhost:{cls.srv.server_port}", TOKEN, CHROME,
                              str(root / "chrome")], capture_output=True, text=True, timeout=RUN_TIMEOUT_S)
        lines = [ln for ln in out.stdout.splitlines() if ln.startswith("{")]
        cls.checks = json.loads(lines[-1])["checks"] if lines else [{"name": "runner", "ok": False, "detail": out.stderr}]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        for p in reversed(cls.patches):
            p.stop()
        cls.tmp.cleanup()

    def assert_group(self, names):
        by_name = {c["name"]: c for c in self.checks}
        runner = by_name.get("runner")
        self.assertIsNone(runner, runner and runner["detail"])
        for name in names:
            with self.subTest(name):
                self.assertIn(name, by_name, "check did not run")
                self.assertTrue(by_name[name]["ok"], by_name[name]["detail"])

    def test_every_browser_check_passed(self):
        self.assert_group([c["name"] for c in self.checks])

    def test_a_slow_health_answer_moves_nothing(self):
        self.assert_group(["first visit: the drift notice shows", "the remembered notice is drawn before /api/health answers",
                           "the filters and rows keep their place when /api/health lands"])

    def test_dismissal_and_profile_switch(self):
        self.assert_group(["Hide until it changes hides the drift notice", "the dismissal ignores the percentages",
                           "a switch shows the other person's notice at once", "the address no longer names the previous person",
                           "a reload stays on the person switched to", "switching back hides the dismissed notice at once",
                           "no page errors"])
