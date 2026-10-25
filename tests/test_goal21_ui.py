"""GOAL21 §2 / §4 in headless Chrome (tests/ui/goal21_ui.mjs) against a throwaway DB and a fake resumegen: the grouped
side menu, the person switch, the status button, Settings as a page, the phone's bottom bar and section chips."""
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

RUNNER = Path(__file__).parent / "ui" / "goal21_ui.mjs"
TOKEN = "ui21-test-token"
RUN_TIMEOUT_S = 240


@unittest.skipUnless(shutil.which("node") and Path(CHROME).exists(), "needs node and Google Chrome")
class Goal21BrowserTest(unittest.TestCase):
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
                              str(root / "chrome")],
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

    def test_side_menu_and_person_switch(self):
        self.assert_group(["side menu: Jobs, Resumes, Applications, Insights, Autofill", "side menu: the views in their groups' order",
                           "side menu: exactly one active item", "side menu: every item has a name and a hover label",
                           "side menu: a click opens the view and marks it", "Tracker: Status comes right after the frozen columns",
                           "person switch: both people on screen, one pressed",
                           "person switch: one click switches", "person switch: the resumegen link follows the person"])

    def test_status_and_settings(self):
        self.assert_group(["status: a dot and a word, labelled", "status: a click opens its popover", "status: Esc closes it",
                           "Settings: the gear opens the page", "Settings: the eight sections", "Settings: every section shows its content",
                           "Settings: the resume generator section links to resumegen",
                           "Settings: the view title and no active menu item", "Settings: Back returns to the list",
                           "Settings: the , key opens it", "Settings: Esc closes it", "type: body text is at least 14 px"])

    def test_job_list_and_actions(self):
        self.assert_group(["list head: new jobs counted over the last 24 h", "rows say Priority, not worth",
                           "job actions: an icon and a word each", "job detail: Priority with its explanation",
                           "rows: Generate resume looks like a button, not a tag"])

    def test_phone(self):
        self.assert_group(["phone: the bottom bar has the five sections", "phone: the side menu is not on screen",
                           "phone: Jobs shows its three views as chips", "phone: the person switch is on screen",
                           "phone: the list has no sideways scroll", "phone: Applications opens the Tracker with its four views",
                           "phone: the top bar names the view", "phone: a chip switches inside the section",
                           "phone: the Tracker views have no sideways scroll",
                           "phone: a row of chips that scrolls sideways fades at the side with more (W15)", "phone: More has Settings, the theme and resumegen, no keys", "phone: Settings opens from More",
                           "phone: Settings has no sideways scroll", "phone: Esc goes back from Settings", "no page errors"])


if __name__ == "__main__":
    unittest.main()
