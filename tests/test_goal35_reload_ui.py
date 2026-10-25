"""GOAL35 §1: a reload shows the same page, with the same filters, sort, segment, search, person and job, fully
drawn (tests/ui/goal35_reload.mjs, headless Chrome, a throwaway DB, the real server). Every page is set by hand,
reloaded 3 times and compared; then the server is restarted on the same port and every saved address is opened
again as a copied link, with the service worker installed. Back / forward restore the page before."""
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from job_hunt_agent import config, db, rgclient, server, tracker
from tests.test_goal7_ui import CHROME
from tests.test_goal9_a import PROFILE, seed

RUNNER = Path(__file__).parent / "ui" / "goal35_reload.mjs"
TOKEN = "ui35-reload-token"
RUN_TIMEOUT_S = 420
PID, OTHER = "tester", "other"
PEOPLE = {PID: PROFILE, OTHER: PROFILE.replace("name: Tess Tester", "name: Olive Other")}
APPS = (("Acme", "Machine Learning Engineer", "2026-10-10", "applied"), ("Acme", "AI Engineer", "2026-10-12", "screen"),
        ("Amazon", "Applied AI Engineer", "2026-10-14", "applied"), ("Globex", "Data Scientist", "2026-09-30", "rejected"))


def start_server(port: int = 0):
    srv = server.make_server(lan=False, port=port)
    srv.token = TOKEN
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def stop_server(srv) -> None:
    srv.shutdown()
    srv.server_close()


def run(srv, root: Path, mode: str) -> list[dict]:
    out = subprocess.run(["node", str(RUNNER), f"http://localhost:{srv.server_port}", TOKEN, CHROME, str(root / "chrome"),
                          mode, str(root / "views.json"), OTHER], capture_output=True, text=True, timeout=RUN_TIMEOUT_S)
    lines = [ln for ln in out.stdout.splitlines() if ln.startswith("{")]
    return json.loads(lines[-1])["checks"] if lines else [{"name": f"runner {mode}", "ok": False, "detail": out.stderr}]


@unittest.skipUnless(shutil.which("node") and Path(CHROME).exists(), "needs node and Google Chrome")
class Goal35ReloadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        for pid, text in PEOPLE.items():
            (root / "profiles" / pid / "resumes").mkdir(parents=True)
            (root / "profiles" / pid / "profile.yaml").write_text(text)
        cls.patches = [mock.patch.object(config, "DB_PATH", root / "ui.db"),
                       mock.patch.object(config, "PROFILES", root / "profiles"),
                       mock.patch.object(rgclient, "_base", lambda: "http://127.0.0.1:9")]     # the Resume app: offline
        for p in cls.patches:
            p.start()
        conn = db.connect()
        seed(conn)
        tracker.ensure_profile(conn, PID)
        with db.tx(conn):
            for company, role, applied_on, status in APPS:
                conn.execute("INSERT INTO applications(profile, company, role_title, applied_on, status) VALUES (?, ?, ?, ?, ?)",
                             (PID, company, role, applied_on, status))
        conn.close()
        srv = start_server()
        port = srv.server_port
        cls.checks = run(srv, root, "record")
        stop_server(srv)
        srv = start_server(port)                       # GOAL35 §1: "also after a server restart"
        cls.checks += run(srv, root, "replay")
        stop_server(srv)

    @classmethod
    def tearDownClass(cls):
        for p in reversed(cls.patches):
            p.stop()
        cls.tmp.cleanup()

    def assert_group(self, names):
        by_name = {c["name"]: c for c in self.checks}
        for name in ("runner record", "runner replay", "runner"):
            self.assertNotIn(name, by_name, by_name.get(name, {}).get("detail"))
        for name in names:
            with self.subTest(name):
                self.assertIn(name, by_name, "check did not run")
                self.assertTrue(by_name[name]["ok"], by_name[name]["detail"])

    def test_every_browser_check_passed(self):
        self.assert_group([c["name"] for c in self.checks])

    def test_every_page_reloads_to_itself(self):
        pages = ["Recommended", "Saved", "Archived", "Tailored resumes", "Tracker", "Applied", "Interviewing", "Follow-ups",
                 "Stats", "Answers", "Settings", "The other person's Recommended"]
        self.assert_group([f"{p}: reload {i} shows the same page" for p in pages for i in (1, 2, 3)]
                          + [f"{p}: a copied link after a server restart shows the same page" for p in pages])

    def test_tailored_resumes_draws_its_board_on_the_first_paint(self):
        self.assert_group([f"Tailored resumes: reload {i} draws the board at once" for i in (1, 2, 3)])

    def test_back_and_forward(self):
        self.assert_group(["back: the page before with its filters, sort and job", "forward: the Tracker again"])

    def test_service_worker_on(self):
        self.assert_group(["Recommended: the service worker controls the page", "replay: the service worker controls the page"])


if __name__ == "__main__":
    unittest.main()
