"""GOAL15 §1 in headless Chrome (tests/ui/goal15_ui.mjs) against a throwaway DB: with "Updates from email" open the
tracker table still scrolls (1440x900, 1024x768, a phone), the panel starts folded to one summary line, keeps the
person's open/closed choice for the tab session, shows today's Cold sweep as ONE line (Show: 20 names at a time; one
Undo all) and today's emails newest first, 20 at a time."""
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

from job_hunt_agent import config, db, server
from job_hunt_agent.mail import Mail, update
from tests.test_goal7_ui import CHROME
from tests.test_goal8_flow import PROFILE

RUNNER = Path(__file__).parent / "ui" / "goal15_ui.mjs"
TOKEN = "ui15-test-token"
RUN_TIMEOUT_S = 240
PID = "tester"
COLD_ROWS = 45                      # over GROUP_OVER: one grouped line
LIVE_ROWS = 60                      # applied recently: the tracker has rows to scroll past
EMAILS = 25                         # over one page of 20


def seed(conn) -> None:
    today = date.today()
    old, recent = (today - timedelta(days=60)).isoformat(), (today - timedelta(days=2)).isoformat()
    with db.tx(conn):
        conn.execute("INSERT INTO profiles(id, name) VALUES (?, 'Tess Tester')", (PID,))
        for i in range(COLD_ROWS + LIVE_ROWS):
            conn.execute("INSERT INTO applications(profile, company, role_title, status, applied_on, source) "
                         "VALUES (?, ?, 'ML Engineer', 'applied', ?, 'feed')",
                         (PID, f"ZZ_TEST Co {i:03d}", old if i < COLD_ROWS else recent))
    update.cold_sweep(conn, PID, today)
    ids = [r[0] for r in conn.execute("SELECT id FROM applications WHERE applied_on = ? ORDER BY id", (recent,))]
    start = datetime.combine(today, datetime.min.time()).astimezone() + timedelta(minutes=5)
    for i in range(EMAILS):
        mail = Mail(f"m{i}", "jobs@zz-test.example", f"ZZ_TEST thanks {i:02d}", start + timedelta(minutes=i),
                    "Thanks for applying.")
        update.record(conn, PID, mail, "received", "llm", None, "recorded", app_id=ids[i], now=start)


@unittest.skipUnless(shutil.which("node") and Path(CHROME).exists(), "needs node and Google Chrome")
class Goal15BrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        (root / "profiles" / PID).mkdir(parents=True)
        (root / "profiles" / PID / "profile.yaml").write_text(PROFILE)
        (root / "data").mkdir()
        cls.patches = [mock.patch.object(config, "DB_PATH", root / "ui.db"),
                       mock.patch.object(config, "DATA", root / "data"),
                       mock.patch.object(config, "PROFILES", root / "profiles"),
                       mock.patch.dict("os.environ", {"JOB_HUNT_AGENT_NO_NOTIFY": "1"})]
        for p in cls.patches:
            p.start()
        conn = db.connect()
        seed(conn)
        conn.close()
        cls.srv = server.make_server(lan=False, port=0)
        cls.srv.token = TOKEN
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        out = subprocess.run(["node", str(RUNNER), f"http://localhost:{cls.srv.server_port}", TOKEN, CHROME,
                              str(root / "chrome"), str(COLD_ROWS), str(EMAILS)],
                             capture_output=True, text=True, timeout=RUN_TIMEOUT_S)
        lines = [ln for ln in out.stdout.splitlines() if ln.startswith("{")]
        cls.checks = json.loads(lines[-1])["checks"] if lines else [{"name": "runner", "ok": False, "detail": out.stderr}]
        conn = db.connect()
        cls.applied_after = conn.execute("SELECT COUNT(*) FROM applications WHERE status = 'applied'").fetchone()[0]
        conn.close()

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

    def test_the_tracker_scrolls_with_the_panel_open(self):
        self.assert_group([f"{size}: {what}" for size in ("1440x900", "1024x768", "phone 390x844")
                           for what in ("the open panel stays within half the screen",
                                        "the tracker table is on screen below the open panel",
                                        "the tracker table scrolls to its last row")])

    def test_the_panel_is_one_line_until_opened_and_remembers_the_choice(self):
        self.assert_group(["the panel starts folded", "the folded line sums up today",
                           "the open choice is kept on reload", "the closed choice is kept on reload"])

    def test_cold_is_one_grouped_line_with_paging_and_one_undo(self):
        self.assert_group(["Cold is one line, no names until Show", "Show lists 20 names", "Show more lists 20 more",
                           "today's emails: 20 newest first", "Show more shows the rest",
                           "Undo all puts every Cold row back", "no page errors"])
        self.assertEqual(self.applied_after, COLD_ROWS + LIVE_ROWS, "every Cold row is Applied again")


if __name__ == "__main__":
    unittest.main()
