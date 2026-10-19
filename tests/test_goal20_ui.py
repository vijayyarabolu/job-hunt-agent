"""GOAL20 audit fixes in headless Chrome (tests/ui/goal20_ui.mjs) against a throwaway DB and a fake resumegen: one
check per problem found while walking the live app, both people, desktop (1440 px) and phone (390 px)."""
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import docx

from job_hunt_agent import config, db, rgclient, server
from tests.test_goal7_ui import CHROME
from tests.test_goal8_flow import PROFILE, TOKEN as RG_TOKEN, FakeResumegen
from tests.test_goal13_ui import PEOPLE, seed

RUNNER = Path(__file__).parent / "ui" / "goal20_ui.mjs"
TOKEN = "ui20-test-token"
RUN_TIMEOUT_S = 240


@unittest.skipUnless(shutil.which("node") and Path(CHROME).exists(), "needs node and Google Chrome")
class Goal20BrowserTest(unittest.TestCase):
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
            conn.execute("UPDATE jobs SET min_years = 1 WHERE uid = ?", (uids["AI Engineer"],))
        conn.close()
        cls.rg.add("tester", "Globex", "Data Engineer", "Pasted JD. " * 20, status="ready",   # an outside job (pasted JD)
                   finished_at=db.iso(db.utcnow()))
        cls.rg_id = cls.rg.add("tester", "Acme", "ML Platform Engineer", "JD text. " * 20,       # a ready resume with a Doc
                               source_job_id=uids["ML Platform Engineer"], status="ready",
                               result={"files": {"pdf": "/x/r.pdf"}, "base": {"id": "ml", "label": "ML Engineer"}},
                               gdoc_link="https://docs.example/d1", files_version="2026-10-12T21:00:00Z", undo_count=1,
                               times={"made": "2026-10-12T20:00:00Z"})
        cls.srv = server.make_server(lan=False, port=0)
        cls.srv.token = TOKEN
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        out = subprocess.run(["node", str(RUNNER), f"http://localhost:{cls.srv.server_port}", TOKEN, CHROME,
                              str(root / "chrome"), json.dumps({"uids": uids, "rg": cls.rg.url, "rg_id": cls.rg_id})],
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

    def test_header_and_cards(self):
        self.assert_group(["desktop 1440: every tab fully visible with 3-digit counts", "card: Asks 1 yr (not 1 yrs)",
                           "no page errors"])

    def test_answers_on_hand_edit_in_place(self):
        self.assert_group(["Answers: an empty chip opens an editor on that field, in the panel",
                           "Answers: Enter saves it and the chip is filled", "Answers: Edit shows every answer filled in",
                           "Answers: Esc cancels the edit and keeps the job open",
                           "Answers: Enter on Cancel cancels (saves nothing)",
                           "Answers: a chip copies and the toast shows what was copied"])

    def test_resume_buttons_stay_busy_until_the_new_resume_shows(self):
        self.assert_group(["Re-import: busy (disabled, 'Re-importing', seconds) while the new PDF loads",
                           "Re-import: the other actions wait", "Re-import: a status line says what is running",
                           "Re-import: back to normal once the new PDF has loaded",
                           "Re-import: the busy button says the step it is on",
                           "Fix with note: busy line while the worker runs",
                           "Fix with note: ends when the new resume is on screen"])

    def test_resumes_tab(self):
        self.assert_group(["Resumes: an outside job says 'outside job', not 'scoring'"])


if __name__ == "__main__":
    unittest.main()
