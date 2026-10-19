"""GOAL25 §1/§3/§4 (user 2026-10-14 17:06) in headless Chrome (tests/ui/goal25_pages.mjs): the Answers page's Learned
(with Remove = Undo), Company answers by company, Saved long answers, Stats, the typo fix note, choice dropdowns, the
education list with start dates and "+ Add a degree", the Empty note leaving once saved; Settings › Autofill ›
Health (fill rate per board, errors, the nightly check, the version); and, when job-hunt-agent opens, the nightly check's
drop (until Got it) and the newer-extension notice. Skipped without node / Chrome."""
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from job_hunt_agent import config, db, rgclient, server
from job_hunt_agent.autofill import api as afapi, bank, health
from tests.test_goal7_ui import CHROME
from tests.test_goal8_flow import PROFILE, TOKEN as RG_TOKEN, FakeResumegen
from tests.test_goal13_ui import NOW, seed

RUNNER = Path(__file__).parent / "ui" / "goal25_pages.mjs"
TOKEN = "p25-test-token"
PID = "tester"
RUN_TIMEOUT_S = 240
LONG = "I want to build reliable ML systems at Acme Robotics because robots in warehouses are a hard, real problem. " * 3


def _teach(conn, uid, company, *items):
    afapi.post(conn, PID, "/api/autofill/learn", {"items": list(items), "board": "greenhouse", "company": company,
                                                   "uid": uid, "hand": len(items)}, NOW)


def _item(label, value, kind="text", options=()):
    return {"label": label, "value": value, "kind": kind, "options": list(options), "how": "hand", "hints": []}


@unittest.skipUnless(shutil.which("node") and Path(CHROME).exists(), "needs node and Google Chrome")
class PagesBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        (root / "profiles" / PID / "resumes").mkdir(parents=True)
        (root / "profiles" / PID / "profile.yaml").write_text(PROFILE.replace("families: [ml_engineer]", "families: [ml_engineer, ai_engineer]"))
        cls.rg = FakeResumegen()
        cls.patches = [mock.patch.object(config, "DB_PATH", root / "p.db"), mock.patch.object(config, "PROFILES", root / "profiles"),
                       mock.patch.object(rgclient, "_base", lambda: cls.rg.url), mock.patch.object(rgclient, "_token", lambda: RG_TOKEN)]
        for p in cls.patches:
            p.start()
        conn = db.connect()
        uids = seed(conn)
        bank.ensure(conn, PID)
        bank.save(conn, PID, "orientation", "Hetrosexual")                  # a typo: fixed on the page, can be undone
        _teach(conn, uids["AI Engineer"], "Acme Robotics", _item("What is your notice period?", "2 weeks"),
               _item("Will you now or in the future require sponsorship?", "No", "radio", ["Yes", "No"]),
               _item("Why do you want to work at Acme Robotics?", LONG.strip(), "textarea"))
        with db.tx(conn):
            conn.execute("INSERT INTO autofill_log(profile, job_uid, board, step, filled, needs, at) VALUES (?,?,?,?,?,?,?)",
                         (PID, uids["AI Engineer"], "greenhouse", "", 8, 1, db.iso(NOW)))
        health.record_error(conn, PID, {"board": "workday", "step": "questions1", "kind": "error", "message": "boom (test)",
                                        "url": "https://zz.wd5.myworkdayjobs.com/x", "html": "<form></form>"}, NOW)
        page = {"name": "greenhouse-fixture", "board": "greenhouse", "source": "fixture", "step": "apply", "fields": 12}
        health.record_canary(conn, "2026-10-13T03:10:00Z", [{**page, "filled": 10}])
        health.record_canary(conn, "2026-10-14T03:10:00Z", [{**page, "filled": 4}])
        health._seen.clear()
        health.seen(conn, PID, "1.0.0")                                    # the user's Chrome still has GOAL23's extension
        conn.close()
        cls.srv = server.make_server(lan=False, port=0)
        cls.srv.token = TOKEN
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        out = subprocess.run(["node", str(RUNNER), f"http://localhost:{cls.srv.server_port}", TOKEN, CHROME, str(root / "chrome")],
                             capture_output=True, text=True, timeout=RUN_TIMEOUT_S)
        lines = [ln for ln in out.stdout.splitlines() if ln.startswith("{")]
        cls.checks = json.loads(lines[-1])["checks"] if lines else [{"name": "runner", "ok": False, "detail": out.stderr[-3000:]}]
        conn = db.connect()
        cls.open_notices = health.notices(conn)
        cls.learned_left = conn.execute("SELECT count(*) FROM autofill_learned WHERE undone_at IS NULL AND profile = ?", (PID,)).fetchone()[0]
        conn.close()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        for p in reversed(cls.patches):
            p.stop()
        cls.rg.stop()
        cls.tmp.cleanup()

    def test_every_browser_check_passed(self):
        for c in self.checks:
            with self.subTest(c["name"]):
                self.assertTrue(c["ok"], c["detail"])
        self.assertGreaterEqual(len(self.checks), 12)

    def test_got_it_closed_the_notice_and_remove_undid_one_lesson(self):
        self.assertEqual(self.open_notices, [])
        self.assertGreaterEqual(self.learned_left, 1)
