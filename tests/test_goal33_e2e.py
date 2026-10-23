"""GOAL33 §5: the user's daily flow end to end, on COPIES of both apps' data, for each person: Recommended -> a job posted in
the last 24 h -> Generate -> the Resume app and Tailored resumes -> Needs you (live) -> Accept as it is -> Ready ->
Autofill (the real extension on Workday fixture pages at the job's own address) fills that PDF, the user's work history with
the employers' countries, the user's school's place and the user's EEO answers -> Mark applied -> an Applied card -> the row the
Sheets sync would append (a dry run: nothing is sent to Google).

Runs only when pointed at throwaway servers on copies (never the live data; it refuses those paths):
  JH_E2E_URL      job-hunt-agent server on the copy (e.g. http://127.0.0.1:8802), JH_E2E_HOME its JOB_HUNT_AGENT_HOME
  JH_E2E_RG_URL   resumegen server on the copy, JH_E2E_RG_HOME its resumegen dir (data/ holds the copy)
  JH_E2E_PEOPLE   person:finished resumegen job whose files the worker stand-in reuses, e.g. alex:331,jordan:330
  JH_E2E_RG_PY    resumegen's Python (default python3)
Driven by tests/ui/goal33_e2e.mjs; the worker is played by tests/e2e_worker.py (no Claude call)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from job_hunt_agent import config, db, gsync, tracker_layout
from tests.test_goal23_extension import CHROME, FIXTURES, ROOT

RUNNER = Path(__file__).parent / "ui" / "goal33_e2e.mjs"
WORKER = Path(__file__).parent / "e2e_worker.py"
RUN_TIMEOUT_S = 600
LIVE = [Path.home() / "Documents" / "job-hunt-agent", Path.home() / "Documents" / "resumegen"]
ENV = ("JH_E2E_URL", "JH_E2E_HOME", "JH_E2E_RG_URL", "JH_E2E_RG_HOME", "JH_E2E_PEOPLE")


def _live(path) -> bool:
    real = Path(path).resolve()
    return any(real.is_relative_to(p.resolve()) for p in LIVE)


@unittest.skipUnless(all(os.environ.get(k) for k in ENV) and shutil.which("node") and Path(CHROME).exists(),
                     "needs JH_E2E_* pointing at copies, node and Google Chrome")
class DailyFlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home, cls.rg_home = Path(os.environ["JH_E2E_HOME"]), os.environ["JH_E2E_RG_HOME"]
        if _live(cls.home) or _live(cls.rg_home):
            raise unittest.SkipTest("refusing the live data: JH_E2E_HOME / JH_E2E_RG_HOME must be copies")
        rg_token = (Path(cls.rg_home) / "data" / "token").read_text().strip()
        token = (cls.home / "data" / "lan_token").read_text().strip()
        worker = json.dumps([os.environ.get("JH_E2E_RG_PY", "python3"), str(WORKER), cls.rg_home])
        cls.runs = {}
        for pair in os.environ["JH_E2E_PEOPLE"].split(","):
            pid, source = pair.split(":")
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as chrome:
                out = subprocess.run(["node", str(RUNNER), os.environ["JH_E2E_URL"], token, pid, os.environ["JH_E2E_RG_URL"], rg_token,
                                      CHROME, chrome, str(ROOT / "extension"), str(FIXTURES), worker, source],
                                     capture_output=True, text=True, timeout=RUN_TIMEOUT_S)
            lines = [ln for ln in out.stdout.splitlines() if ln.startswith("{")]
            cls.runs[pid] = json.loads(lines[-1]) if lines else {"checks": [{"name": "runner", "ok": False, "detail": out.stderr[-3000:]}], "facts": {}}
        if os.environ.get("JH_TIMINGS"):
            Path(os.environ["JH_TIMINGS"]).write_text(json.dumps(cls.runs, indent=1))

    def test_every_step_for_each_person(self):
        for pid, run in self.runs.items():
            for c in run["checks"]:
                with self.subTest(person=pid, check=c["name"]):
                    self.assertTrue(c["ok"], c["detail"])

    def test_the_sheets_sync_would_append_the_applied_row(self):
        for pid, run in self.runs.items():
            job = run["facts"].get("job")
            with self.subTest(person=pid):
                self.assertIsNotNone(job, "the flow did not reach a job")
                with mock.patch.object(config, "PROFILES", self.home / "profiles"):
                    conn = db.connect(self.home / "data" / "job_hunt_agent.db")
                    try:
                        app = conn.execute("SELECT * FROM applications WHERE profile = ? AND job_uid = ? ORDER BY id DESC",
                                           (pid, job["uid"])).fetchone()
                        self.assertIsNotNone(app, "no tracker row for the job")
                        self.assertEqual(app["status"], "applied")
                        new = gsync.eligible_new(conn, pid, gsync.apps_of(conn, pid))
                        self.assertIn(app["id"], [a["id"] for a in new])
                        _, cells = tracker_layout.cells(dict(app), tracker_layout.of_profile(pid))
                    finally:
                        conn.close()
                text = " | ".join(str(c) for c in cells if c not in (None, ""))
                self.assertIn(job["company"], text)
                self.assertIn(job["title"], text)


if __name__ == "__main__":
    sys.exit(unittest.main())
