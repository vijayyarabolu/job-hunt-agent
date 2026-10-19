"""GOAL18 §2 / §3 in headless Chrome (tests/ui/goal18_ui.mjs) against a throwaway DB and a fake resumegen: Settings
(a page since GOAL21) closes by its Back button, Esc, the browser's Back and the menu, back to the same tab, and a field
saves on its own (desktop and phone); the Resumes tab groups its
rows by the day the resume was made (Chicago time) with numbered rows and their time, keys still move in that order,
and the phone has no sideways scroll."""
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import docx

from job_hunt_agent import config, daygroups, db, rgclient, server
from tests.test_goal7_ui import CHROME
from tests.test_goal8_flow import PROFILE, TOKEN as RG_TOKEN, FakeResumegen
from tests.test_goal13_ui import NOW, PEOPLE, seed

RUNNER = Path(__file__).parent / "ui" / "goal18_ui.mjs"
TOKEN = "ui18-test-token"
RUN_TIMEOUT_S = 240


def ready_times() -> list[tuple[str, str, datetime]]:
    """(title, status, finished) for tester: two today (needs you older), one yesterday, one 3 days ago, one 40."""
    start = datetime.now(daygroups.CHICAGO).replace(hour=0, minute=0, second=0, microsecond=0)
    now = max(NOW.astimezone(daygroups.CHICAGO), start + timedelta(minutes=2))
    return [("Machine Learning Engineer", "ready", now - timedelta(minutes=1)),
            ("AI Engineer", "needs_you", max(start, now - timedelta(minutes=30))),
            ("ML Platform Engineer", "ready", start - timedelta(hours=10)),
            ("Applied AI Engineer", "ready", start - timedelta(days=3) + timedelta(hours=9, minutes=14)),
            ("Senior Machine Learning Engineer", "ready", start - timedelta(days=40))]


def expected() -> dict:
    now = datetime.now(daygroups.CHICAGO)
    labels = [daygroups.label(at.date(), now.date()) for _, _, at in ready_times()]
    heads = [f"{name} ({labels.count(name)})" for name in dict.fromkeys(labels)]
    rows = ["AI Engineer", "Machine Learning Engineer", "ML Platform Engineer", "Applied AI Engineer",
            "Senior Machine Learning Engineer"]                     # needs you first inside today
    times = {t: daygroups.clock(at) for t, _, at in ready_times()}
    return {"heads": heads, "rows": rows, "numbers": [1, 2, 1, 1, 1], "times": [times[r] for r in rows]}


@unittest.skipUnless(shutil.which("node") and Path(CHROME).exists(), "needs node and Google Chrome")
class Goal18BrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        for pid, name in PEOPLE.items():
            resumes = root / "profiles" / pid / "resumes"
            resumes.mkdir(parents=True)
            (root / "profiles" / pid / "profile.yaml").write_text(
                PROFILE.replace("Tess Tester", name).replace("families: [ml_engineer]", "families: [ml_engineer, ai_engineer]"))
            doc = docx.Document()
            doc.add_paragraph(f"{name}. ML Engineer. Python, PyTorch, Kubernetes, AWS. 2021 - 2025.")
            doc.save(resumes / "Tess_ML.docx")
        cls.rg = FakeResumegen()
        cls.patches = [mock.patch.object(config, "DB_PATH", root / "ui.db"),
                       mock.patch.object(config, "PROFILES", root / "profiles"),
                       mock.patch.object(rgclient, "_base", lambda: cls.rg.url),
                       mock.patch.object(rgclient, "_token", lambda: RG_TOKEN)]
        for p in cls.patches:
            p.start()
        conn = db.connect()
        uids = seed(conn)
        with db.tx(conn):
            conn.execute("UPDATE jobs SET closed_at = NULL")
        conn.close()
        for title, status, at in ready_times():
            cls.rg.add("tester", "Acme", title, "JD text. " * 20, source_job_id=uids[title], status=status,
                       finished_at=db.iso(at.astimezone(daygroups.CHICAGO).astimezone(NOW.tzinfo)))
        cls.srv = server.make_server(lan=False, port=0)
        cls.srv.token = TOKEN
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        out = subprocess.run(["node", str(RUNNER), f"http://localhost:{cls.srv.server_port}", TOKEN, CHROME,
                              str(root / "chrome"), json.dumps(expected())],
                             capture_output=True, text=True, timeout=RUN_TIMEOUT_S)
        lines = [ln for ln in out.stdout.splitlines() if ln.startswith("{")]
        cls.checks = json.loads(lines[-1])["checks"] if lines else [{"name": "runner", "ok": False, "detail": out.stderr}]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        for p in reversed(cls.patches):
            p.stop()
        cls.rg.stop()
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

    def test_settings_closes_every_way(self):
        ways = ("its Back button", "Esc", "Back", "the menu")
        self.assert_group([f"{size}: Settings closes by {how}" for size in ("desktop", "phone") for how in ways]
                          + [f"{size}: closing Settings ({how}) goes back to the same tab" for size in ("desktop", "phone")
                             for how in ways[:3]]
                          + ["desktop: a changed field says Saved", "desktop: the field saved the years cap"])

    def test_resumes_by_day(self):
        self.assert_group(["Ready: one header per day, newest first, More than 30 days last",
                           "Ready: rows numbered inside each day", "Ready: each row shows its time",
                           "Ready: needs you first inside a day", "j moves down the rows in the order shown",
                           "phone: no sideways scroll on Resumes", "no page errors"])


if __name__ == "__main__":
    unittest.main()
