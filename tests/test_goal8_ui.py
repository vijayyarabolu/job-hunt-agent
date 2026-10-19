"""GOAL8 UI in headless Chrome (tests/ui/goal8_ui.mjs) against a throwaway DB, a synthetic profile and a fake
resumegen: Generate resume, multi-select + Generate for selected, g, live chips, the resume panel, "Did you apply?"
(Yes + the empty-columns form, No, Later, several newest first, asked again on app open), outside jobs under Saved,
Settings, the years labels, the phone sheet and "Resume app is offline". Drive uploads are mocked."""
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

import docx

from job_hunt_agent import config, db, resume_upload, rgclient, server, store
from job_hunt_agent.ats.common import FetchResult, make_job
from tests.test_goal7_ui import CHROME
from tests.test_goal8_flow import PID, PROFILE, TOKEN as RG_TOKEN, FakeResumegen

NOW = db.utcnow().replace(microsecond=0)
RUNNER = Path(__file__).parent / "ui" / "goal8_ui.mjs"
TOKEN = "ui8-test-token"
RUN_TIMEOUT_S = 240
JOBS = (("Machine Learning Engineer", "- 3+ years of experience building ML systems in Python and PyTorch."),
        ("Senior Machine Learning Engineer", "- Build ML systems in Python, PyTorch and Kubernetes on AWS."),
        ("AI Engineer", "- 2+ years of experience with Python, LLMs and RAG systems in production."),
        ("ML Platform Engineer", "- Kubernetes, MLflow, Python and AWS for model serving at scale."),
        ("Applied AI Engineer", "- Python, LLM evaluation and RAG pipelines on AWS for production systems."))


def seed(conn) -> None:
    db.sync_boards(conn, [{"board": "lever:acme", "ats": "lever", "key": "acme", "company": "Acme"}])
    run = db.start_run(conn, "test", NOW)
    items = [make_job(ats="lever", key="acme", ext_id=str(i), company="Acme", title=title, location="Austin, TX",
                      url=f"http://127.0.0.1:9/acme/{i}", posted_at=db.iso(NOW - timedelta(minutes=10 * (i + 1))),
                      description="About the role.\nRequirements:\n" + line + "\n" + "We ship ML products. " * 8)
             for i, (title, line) in enumerate(JOBS)]
    store.write_board(conn, db.board(conn, "lever:acme"), FetchResult(items, 200, n_raw=len(items)), "ok", run, NOW,
                      ["fam:ml_engineer", "fam:ai_engineer"], 5)
    conn.commit()


@unittest.skipUnless(shutil.which("node") and Path(CHROME).exists(), "needs node and Google Chrome")
class Goal8BrowserTest(unittest.TestCase):
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
        out = subprocess.run(["node", str(RUNNER), f"http://localhost:{cls.srv.server_port}", TOKEN, CHROME,
                              str(root / "chrome"), cls.rg.url], capture_output=True, text=True, timeout=RUN_TIMEOUT_S)
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

    def test_generate_select_and_live_chips(self):
        self.assert_group(["feed loads", "the resume app connects after the first paint",
                           "the detail pane has Generate resume", "its chip shows queued at once",
                           "one click sends one job to the resume app", "Space selects a job",
                           "Shift-click selects the range", "the bar counts them", "Generate for selected",
                           "it queues them in one request", "the selection clears", "g on a queued job says so",
                           "the chip turns ready by itself"])

    def test_resume_panel(self):
        self.assert_group(["the chip opens the resume panel", "it shows ATS before and after", "the PDF is shown inline",
                           "the PDF passes through job-hunt-agent", "the diff marks the changes", "Regenerate takes a note",
                           "the resume app gets the note"])

    def test_did_you_apply(self):
        self.assert_group(["coming back asks Did you apply?", "it names the company and role",
                           "Yes marks applied and attaches the tailored resume",
                           "the form lists only the empty sheet columns",
                           "the row is applied today with what the form said", "several are asked newest first",
                           "then the next one", "No closes it and Later snoozes it", "No leaves the job unapplied",
                           "Later puts a badge on Follow-ups", "coming back does not ask a Later one again",
                           "opening the app asks it again"])

    def test_outside_jobs_settings_labels_phone_offline(self):
        self.assert_group(["an outside job shows under Saved", "with its resume chip", "Mark applied works for it",
                           "its row is applied", "Settings shows the years cap", "it is saved to the profile",
                           "each job says what years were read", "the phone sheet has the resume actions",
                           "Select shows the checkboxes on the phone", "job-hunt-agent keeps working when the resume app is down",
                           "it says Resume app is offline", "no page errors"])


if __name__ == "__main__":
    unittest.main()
