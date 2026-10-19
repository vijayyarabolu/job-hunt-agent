"""GOAL9 area E in headless Chrome (tests/ui/goal9_e.mjs) against a throwaway DB, a synthetic profile and a fake
resumegen: the resume side panel (opens at once, Esc with the PDF focused, focus return, busy / optimistic actions,
chips agreeing everywhere, no stale panel), Settings (inline validation, re-rank without a reload), "Did you apply?" on
the phone, the phone sheet / More menu / ? sheet / pick bar, standalone-mode flows, the offline Resume app, and the
GOAL9 §6 hover labels on every control of the area. Drive uploads are mocked. Set GOAL9_SHOTS=<dir> for PNGs."""
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import docx

from job_hunt_agent import config, db, resume_upload, rgclient, server
from tests.test_goal7_ui import CHROME
from tests.test_goal8_flow import PID, PROFILE, TOKEN as RG_TOKEN, FakeResumegen
from tests.test_goal8_ui import seed

RUNNER = Path(__file__).parent / "ui" / "goal9_e.mjs"
TOKEN = "ui9e-test-token"
RUN_TIMEOUT_S = 240


@unittest.skipUnless(shutil.which("node") and Path(CHROME).exists(), "needs node and Google Chrome")
class Goal9AreaETest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        resumes = root / "profiles" / PID / "resumes"
        resumes.mkdir(parents=True)
        (root / "profiles" / PID / "profile.yaml").write_text(PROFILE.replace("families: [ml_engineer]",
                                                                              "families: [ml_engineer, ai_engineer]"))
        (resumes / "Tess_ML.pdf").write_bytes(b"%PDF-1.4 base")
        doc = docx.Document()
        doc.add_paragraph("Tess Tester. ML Engineer. Python, PyTorch, Kubernetes, AWS. 2021 - 2025.")
        doc.save(resumes / "Tess_ML.docx")
        cls.rg = FakeResumegen()
        made = {"name": "Tess_Tester_ML_Resume(Acme).pdf", "link": "https://drive.example/x", "sheet": "not on the Sheet yet"}
        cls.patches = [mock.patch.object(config, "DB_PATH", root / "ui.db"),
                       mock.patch.object(config, "PROFILES", root / "profiles"),
                       mock.patch.object(rgclient, "_base", lambda: cls.rg.url),
                       mock.patch.object(rgclient, "_token", lambda: RG_TOKEN),
                       mock.patch.object(resume_upload, "upload", return_value=made),
                       mock.patch.object(resume_upload, "link_existing", return_value=made)]
        for p in cls.patches:
            p.start()
        conn = db.connect()
        seed(conn)
        conn.close()
        cls.srv = server.make_server(lan=False, port=0)
        cls.srv.token = TOKEN
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        shots = os.environ.get("GOAL9_SHOTS", "")
        out = subprocess.run(["node", str(RUNNER), f"http://localhost:{cls.srv.server_port}", TOKEN, CHROME,
                              str(root / "chrome"), cls.rg.url, *([shots] if shots else [])],
                             capture_output=True, text=True, timeout=RUN_TIMEOUT_S)
        lines = [ln for ln in out.stdout.splitlines() if ln.startswith("{")]
        cls.checks = json.loads(lines[-1])["checks"] if lines else [{"name": "runner", "ok": False, "detail": out.stderr}]
        if shots:
            (Path(shots) / "checks.json").write_text(json.dumps(cls.checks, indent=1))

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

    def test_resume_panel_reacts_at_once_and_chips_agree(self):
        self.assert_group(["row chip -> panel opens at once", "the panel fills", "the panel keeps the focus off the PDF viewer",
                           "Esc closes the panel and restores the detail", "focus is back on the list after Esc",
                           "detail chip -> panel", "the close button gives the focus back to the detail chip",
                           "Regenerate: the row chip says queued at once", "and the panel says queued at once",
                           "the resume app gets the note", "the chip stays queued once the app answers", "the detail chip agrees",
                           "a refused Regenerate rolls the chip back with a toast", "and the button is usable again",
                           "Approve goes busy at once", "the panel shows Approved after the upload", "a second click sends nothing",
                           "the row chip says approved", "the re-rendered PDF does not keep the focus", "the detail chip says approved too", "Re-import goes busy at once",
                           "the open panel refreshes when the status moves", "a closed panel stays closed when its status moves"])

    def test_settings_cap_reranks_without_reload(self):
        self.assert_group(["Settings… opens the page", "an out-of-range cap shows an inline message and stays open",
                           "without a request", "the cap saves on its own", "Back closes Settings at once", "the list shows it is re-ranking",
                           "the feed re-ranks with the new cap, no reload", "the cap is saved", "Esc closes Settings"])

    def test_phone_sheet_and_menus(self):
        self.assert_group(["a row opens the sheet at once", "the sheet takes the focus", "the sticky bar stays inside the screen",
                           "the ⋯ button is reachable by thumb", "the sheet's ⋯ menu opens inside the viewport", "and closes on an outside tap",
                           "the sheet's chip opens the panel", "the phone panel shows the page image, not a PDF frame",
                           "closing the panel brings the job sheet back", "swipe down closes the sheet and focuses the list",
                           "Esc closes the sheet at once", "with the focus back on the list", "More opens", "More closes on an outside tap",
                           "More closes on Esc with the focus back on it", "a second tap on More closes it",
                           "More -> Settings… closes the menu and opens the page inside the viewport", "the ? sheet fits the phone",
                           "the pick bar sits inside the viewport"])

    def test_did_you_apply_on_the_phone(self):
        self.assert_group(["coming back asks Applied? at the top of the sheet",            # on the job's sheet
                           "coming back asks Did you apply? on the phone", "the dialog and its buttons fit the 390 px screen",
                           "its buttons are 44 px", "the focus starts on Yes", "Tab stays inside the dialog", "Later reacts at once",
                           "Later puts a badge on Applications", "the focus leaves the closed dialog",
                           "several are asked newest first with 1 of N", "the dialog still fits", "then the next one",
                           "Yes marks applied and attaches the approved resume",
                           "the form shows only the empty sheet columns, inside the viewport",
                           "the row is applied with what the form said", "the list row says Applied without a reload",
                           "the sheet's Applied button agrees"])

    def test_hover_labels_everywhere_in_the_area(self):
        names = [c["name"] for c in self.checks if c["name"].startswith("labels: ") or c["name"].startswith("hover label: ")]
        self.assertGreaterEqual(len(names), 18, names)
        self.assert_group(names + ["keyboard focus shows the label", "long press shows the label inside the viewport",
                                   "letting go hides it and the tab did not switch",
                                   "the label stays hidden after the long press ends", "a short tap shows no label"])

    def test_standalone_and_offline(self):
        self.assert_group(["opening the app asks the Later one again", "the manifest and the worker still serve",
                           "the app-shell worker is registered", "the toasts keep clear of the bottom tabs and the sheet's bar",
                           "pageshow (back from Safari) asks at the top of the sheet, inside the viewport",
                           "offline: the phone sheet says so and its bar still fits", "offline: job-hunt-agent keeps working at 1440",
                           "no page errors"])


if __name__ == "__main__":
    unittest.main()
