"""GOAL37 §1 / §2 in headless Chrome (tests/ui/goal37_ui.mjs, a throwaway DB, the real server): the Tracker's company
search with its "N more where only the JD mentions it: show" toggle (wide=1, kept by a reload, reset by new search
text), the grid's column sort and filters in the address, and ⌘K's order on the Tracker vs Recommended with the
"No open X jobs in your list right now" line. Skipped without node or Google Chrome."""
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from job_hunt_agent import config, db, rgclient, server
from tests.test_goal37_search import seed_apps, seed_feed
from tests.test_goal7_ui import CHROME
from tests.test_goal9_a import PROFILE

RUNNER = Path(__file__).parent / "ui" / "goal37_ui.mjs"
TOKEN = "ui37-search-token"
RUN_TIMEOUT_S = 300
PID = "tester"


@unittest.skipUnless(shutil.which("node") and Path(CHROME).exists(), "needs node and Google Chrome")
class Goal37UiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        (root / "profiles" / PID / "resumes").mkdir(parents=True)
        (root / "profiles" / PID / "profile.yaml").write_text(PROFILE)
        cls.patches = [mock.patch.object(config, "DB_PATH", root / "ui.db"),
                       mock.patch.object(config, "PROFILES", root / "profiles"),
                       mock.patch.object(rgclient, "_base", lambda: "http://127.0.0.1:9")]     # the Resume app: offline
        for p in cls.patches:
            p.start()
        conn = db.connect()
        seed_feed(conn)
        seed_apps(conn, PID)
        conn.close()
        srv = server.make_server(lan=False, port=0)
        srv.token = TOKEN
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        out = subprocess.run(["node", str(RUNNER), f"http://localhost:{srv.server_port}", TOKEN, CHROME,
                              str(root / "chrome"), PID], capture_output=True, text=True, timeout=RUN_TIMEOUT_S)
        srv.shutdown()
        srv.server_close()
        lines = [ln for ln in out.stdout.splitlines() if ln.startswith("{")]
        cls.checks = json.loads(lines[-1])["checks"] if lines else [{"name": "runner", "ok": False,
                                                                     "detail": out.stderr[-3000:]}]

    @classmethod
    def tearDownClass(cls):
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

    def test_the_tracker_shows_what_was_named_and_the_jd_only_rows_on_request(self):
        self.assert_group(["the Tracker loads", "a company search shows the rows that name it",
                           "the found line offers the JD-only rows", "the address has the search, not wide",
                           "show lists the JD-only rows too", "the toggle now hides them", "the address keeps wide=1",
                           "a reload keeps the JD-only rows shown", "new search text starts without the JD-only rows",
                           "back to the company: the toggle says show again"])

    def test_the_grid_sort_and_filters_are_in_the_address(self):
        self.assert_group(["a column sort goes into the address", "a reload keeps the column sort",
                           "a column filter goes into the address", "a reload keeps the column filter",
                           "clearing them takes them out of the address", "a broken filter in a link is ignored"])

    def test_command_k_order(self):
        self.assert_group(["⌘K on the Tracker: applications first", "⌘K on Recommended: Amazon's open jobs first",
                           "⌘K: an AWS title at another company is not an Amazon job",
                           "⌘K says when there is no open job at the company", "the Company entry comes next",
                           "no page errors"])


if __name__ == "__main__":
    unittest.main()
