"""the "Job board" filter in the real UI (tests/ui/goal11_boards.mjs, headless Chrome, a throwaway DB, the
real server): one option per board with live counts, greyed at 0, several at once, independent of Posted (Any / 30d),
and with a board picked "Show more" pages past the card cap (set to 2 here so the 5 Lever jobs need paging)."""
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from job_hunt_agent import api, config, db, server
from tests.test_goal7_ui import CHROME
from tests.test_goal9_a import BOARDS, PROFILE, seed

RUNNER = Path(__file__).parent / "ui" / "goal11_boards.mjs"
TOKEN = "ui11-boards-token"
RUN_TIMEOUT_S = 120
PAGE = 2
EMPTY_BOARD = {"board": "ashby:zed", "ats": "ashby", "key": "zed", "company": "Zed", "segment": "startup"}


@unittest.skipUnless(shutil.which("node") and Path(CHROME).exists(), "needs node and Google Chrome")
class Goal11BoardsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        (root / "profiles" / "tester" / "resumes").mkdir(parents=True)
        (root / "profiles" / "tester" / "profile.yaml").write_text(PROFILE)
        cls.patches = [mock.patch.object(config, "DB_PATH", root / "ui.db"),
                       mock.patch.object(config, "PROFILES", root / "profiles"),
                       mock.patch.object(api, "MAX_CARDS", PAGE)]
        for p in cls.patches:
            p.start()
        conn = db.connect()
        seed(conn)
        db.sync_boards(conn, [*BOARDS, EMPTY_BOARD])            # a board system with no jobs: greyed, not hidden
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

    def test_job_board_filter(self):
        self.assert_group(["Posted has Any (pressed: no limit) and 30d",
                           "the chip opens with one option per board and the server's counts",
                           "a board with no jobs is greyed, not hidden", "a multi-select: the menu stays open after a pick",
                           "two boards: the list is both boards' jobs", "with Posted 30d the board filter still holds",
                           "past the cap: the next pages load by themselves", "Show more pages through every match of the board",
                           "phone: the chip is in the strip and filters the list", "no page errors"])


if __name__ == "__main__":
    unittest.main()
