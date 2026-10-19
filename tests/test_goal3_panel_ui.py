"""resumegen GOAL3 §4 / §5 / §6 in job-hunt-agent's browser UI (tests/ui/goal3_panel.mjs, headless Chrome, a throwaway DB, a
fake resumegen): the ring and row show "base% → tailored% with your tailored resume" once the resume poll scored the
ready resume; the resume panel shows when it was made, the Outdated badge with Regenerate with current resume, a
needs-you resume's reasons with Looks good / Fix with note, and Restore on an archived one."""
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

from job_hunt_agent import config, db, rgclient, server, store
from job_hunt_agent.ats.common import FetchResult, make_job
from tests.test_goal3_tailored import TAILORED
from tests.test_goal7_ui import CHROME
from tests.test_goal8_flow import PID, PROFILE, TOKEN as RG_TOKEN, FakeResumegen

NOW = db.utcnow().replace(microsecond=0)
RUNNER = Path(__file__).parent / "ui" / "goal3_panel.mjs"
TOKEN = "ui3-panel-token"
RUN_TIMEOUT_S = 120
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
JOBS = ("- Python, PyTorch, Kubernetes and AWS; pipelines in Spark, Airflow, Snowflake, dbt and Kafka; Docker, MLflow.",
        "- 3+ years of experience building ML systems in Python and PyTorch.",
        "- Build ML systems in Python, PyTorch and Kubernetes on AWS.")
MADE = "2026-10-07T21:30:00Z"


def seed(conn) -> list[str]:
    db.sync_boards(conn, [{"board": "lever:acme", "ats": "lever", "key": "acme", "company": "Acme"}])
    run = db.start_run(conn, "test", NOW)
    items = [make_job(ats="lever", key="acme", ext_id=str(i), company="Acme", title=title, location="Austin, TX",
                      url=f"http://127.0.0.1:9/acme/{i}", posted_at=db.iso(NOW - timedelta(minutes=10 * (i + 1))),
                      description="About the role.\nRequirements:\n" + line + "\n" + "We ship ML products. " * 8)
             for i, (title, line) in enumerate(zip(("Machine Learning Engineer", "ML Platform Engineer",
                                                    "Applied ML Engineer"), JOBS))]
    store.write_board(conn, db.board(conn, "lever:acme"), FetchResult(items, 200, n_raw=len(items)), "ok", run, NOW,
                      ["fam:ml_engineer"], 5)
    conn.commit()
    return [it["uid"] for it in items]


@unittest.skipUnless(shutil.which("node") and Path(CHROME).exists(), "needs node and Google Chrome")
class Goal3PanelBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        resumes = root / "profiles" / PID / "resumes"
        resumes.mkdir(parents=True)
        (root / "profiles" / PID / "profile.yaml").write_text(PROFILE + "feed_min_match: 0\n")
        (resumes / "Tess_ML.pdf").write_bytes(b"%PDF-1.4 base")
        doc = docx.Document()
        doc.add_paragraph("Tess Tester. ML Engineer. Python, PyTorch, Kubernetes, AWS. 2021 - 2025.")
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
        conn.close()
        times = {"made": MADE, "updated": None}
        ready = cls.rg.add(PID, "Acme", "MLE", "z" * 100, source_job_id=uids[0], status="ready", times=times,
                           result={"base": {"id": "ml", "label": "ML Engineer"}},
                           badge="Outdated – made from your old resume", outdated=["made from your old resume"])
        cls.rg.files[(ready, "docx")] = (TAILORED, DOCX)
        cls.rg.add(PID, "Acme", "MLE", "z" * 100, source_job_id=uids[1], status="needs_you", times=times,
                   error="page 2 has 3 lines", reasons=[{"area": "Length", "text": "page 2 has 3 lines"}])
        cls.rg.add(PID, "Acme", "MLE", "z" * 100, source_job_id=uids[2], status="ready", times=times,
                   archived_at="2026-10-07T22:00:00.123Z")
        cls.srv = server.make_server(lan=False, port=0)
        cls.srv.token = TOKEN
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        out = subprocess.run(["node", str(RUNNER), f"http://localhost:{cls.srv.server_port}", TOKEN, CHROME,
                              str(root / "chrome"), cls.rg.url, ",".join(uids)], capture_output=True, text=True,
                             timeout=RUN_TIMEOUT_S)
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

    def test_tailored_score_on_ring_and_row(self):
        self.assert_group(["the row shows base → tailored", "the ring names the tailored resume",
                           "the other jobs keep their base number", "the job pane says it under the ring"])

    def test_resume_panel(self):
        self.assert_group(["the chip says Outdated", "the panel shows when it was made", "the Outdated badge and its reason",
                           "Regenerate with current resume", "needs you shows the reasons in plain words",
                           "Fix with note needs a note", "Fix with note sends the note", "Looks good accepts it",
                           "an archived resume can be restored", "no page errors"])


if __name__ == "__main__":
    unittest.main()
