"""GOAL15 §4 in headless Chrome (tests/ui/goal15_resume.mjs): on a tracker row, × -> "Upload resume" shows -> upload
-> the cell and the Sheet chip follow; Replace ▾ -> a ready resumegen version -> linked; Undo restores; the × hides
until hover on a desktop and shows on a phone. The real server against the fake Google (Sheet + Drive) and a fake
resumegen; downloads go to the harness's temp dir."""
import json
import shutil
import subprocess
import threading
import unittest
from pathlib import Path

from job_hunt_agent import config, resume_swap, resume_upload, server
from tests.test_goal15_resume import APPROVED, ResumeSwapCase
from tests.test_goal7_ui import CHROME
from tests.test_resume_upload import PDF_BYTES

RUNNER = Path(__file__).parent / "ui" / "goal15_resume.mjs"
TOKEN = "ui15r-test-token"
RUN_TIMEOUT_S = 180


@unittest.skipUnless(shutil.which("node") and Path(CHROME).exists(), "needs node and Google Chrome")
class ResumeSwapBrowserTest(ResumeSwapCase):
    def test_x_upload_replace_and_undo_from_the_tracker_cell(self):
        shutil.copy(config.ROOT / "config" / "lexicon.yaml", self.root / "config")       # the feed tab loads too
        first = resume_upload.upload(self.conn, "jane", self.ids["Gamma"], "first.pdf", PDF_BYTES)
        self.ready_job(7)
        srv = server.make_server(lan=False, port=0)
        srv.token = TOKEN
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            out = subprocess.run(["node", str(RUNNER), f"http://localhost:{srv.server_port}", TOKEN, CHROME,
                                  str(self.root / "chrome"), str(self.ids["Gamma"]), first["name"], APPROVED],
                                 capture_output=True, text=True, timeout=RUN_TIMEOUT_S)
        finally:
            srv.shutdown()
            srv.server_close()
        lines = [ln for ln in out.stdout.splitlines() if ln.startswith("{")]
        checks = json.loads(lines[-1])["checks"] if lines else [{"name": "runner", "ok": False, "detail": out.stderr}]
        for check in checks:
            with self.subTest(check["name"]):
                self.assertTrue(check["ok"], check["detail"])
        self.assertGreaterEqual(len(checks), 9, [c["name"] for c in checks])
        # after Undo the row is back on the file uploaded in the browser, and the Sheet chip points at it
        now = resume_swap.current(self.conn, self.ids["Gamma"])
        self.assertEqual([f["name"] for f in now], ["Jane_Doe_AI_Engineer_Resume(Gamma)_v2.pdf"])
        self.assertEqual((self.cell("Gamma")["v"], self.chips("Gamma")), (now[0]["name"], [now[0]["link"]]))
        self.assertEqual(self.parent(first["link"])["name"], "Replaced")
        self.assertEqual(self.parent(self.rg_jobs[7]["drive_link"])["name"], "Replaced")
        self.assertEqual(self.fake.count("DELETE", r"/drive/"), 0, "nothing deleted")


if __name__ == "__main__":
    unittest.main()
