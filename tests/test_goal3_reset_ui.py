"""resumegen GOAL3 §7: every reload or new open of job-hunt-agent comes back to the default view (Recommended, no filters,
Newest, the cursor on the first row, the list at the top); only the person and the theme are kept
(tests/ui/goal3_reset.mjs, headless Chrome, a throwaway DB, the real server)."""
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from job_hunt_agent import config, db, server
from tests.test_goal7_ui import CHROME
from tests.test_goal9_a import PROFILE, seed

RUNNER = Path(__file__).parent / "ui" / "goal3_reset.mjs"
TOKEN = "ui3-reset-token"
RUN_TIMEOUT_S = 120
PEOPLE = {"tester": PROFILE, "other": PROFILE.replace("name: Tess Tester", "name: Olive Other")}


@unittest.skipUnless(shutil.which("node") and Path(CHROME).exists(), "needs node and Google Chrome")
class Goal3ResetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        for pid, text in PEOPLE.items():
            (root / "profiles" / pid / "resumes").mkdir(parents=True)
            (root / "profiles" / pid / "profile.yaml").write_text(text)
        cls.patches = [mock.patch.object(config, "DB_PATH", root / "ui.db"),
                       mock.patch.object(config, "PROFILES", root / "profiles")]
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

    def test_reload_keeps_the_page_and_resets_filters_and_sort(self):     # GOAL33 §3d (was: the tab reset too)
        self.assert_group(["first open: the default view", "the view was changed: filters, sort, selection", "and the tab",
                           "a reload stays on the page with its default filters and sort", "a reload comes back to the job that was open",
                           "a reload on Saved stays on Saved", "a reload keeps the person",
                           "nothing about the view is stored", "a new open starts on Recommended",
                           "while the page is open the filters stay", "no page errors"])


if __name__ == "__main__":
    unittest.main()
