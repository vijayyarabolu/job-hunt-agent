"""GOAL19 §2-§4 in headless Chrome (tests/ui/goal19_lists.mjs), on a throwaway DB with a fake resumegen:
§2 Not interested's 5th chip "Other…" (a text box: Esc closes, Enter saves; shown on the Archived row, counted in
stats) in the feed, the job panel, the Resumes queue and on a phone; §3 × Remove / Shift+X with an 8 s Undo on feed
rows, Archived application rows, the Tracker grid and phone cards (the tracker ones ask first), a removed job never
coming back, duplicates marked "Duplicate of …" and counted; §4 the resume panel showing the new file at once after
Re-import (the file URL carries the files' time), resumegen's "what changed" line, Approve again after an edit, and
Undo my edit / Cancel (each pressed twice)."""
import json
import shutil
import subprocess
import threading
import unittest
from datetime import timedelta
from pathlib import Path

from job_hunt_agent import config, db, feedcache, fits, server, trackapi
from tests.test_goal7_ui import CHROME
from tests.test_goal8_flow import JD, NOW, PID, Goal8Case, seed_job

RUNNER = Path(__file__).parent / "ui" / "goal19_lists.mjs"
TOKEN = "ui19l-test-token"
RUN_TIMEOUT_S = 300
OTHER_JD = ("Requirements:\n" + "Own ranking models for search relevance with Spark, Scala and Airflow; "
            "run A/B tests and write design docs for the retrieval stack. " * 4)


@unittest.skipUnless(shutil.which("node") and Path(CHROME).exists(), "needs node and Google Chrome")
class Goal19ListsBrowserTest(Goal8Case):
    def test_other_reason_remove_duplicates_and_the_new_resume(self):
        dup_old = seed_job(self.conn, "21", title="Machine Learning Engineer, Search", description=JD)
        dup_new = seed_job(self.conn, "22", title="Machine Learning Engineer, Search", description=OTHER_JD)
        earlier = db.iso(NOW - timedelta(hours=1))                     # the older copy is the duplicate
        self.conn.execute("UPDATE jobs SET first_posted_at = ?, posted_at = ? WHERE uid = ?", (earlier, earlier, dup_old))
        # GOAL32: three copies of one role are one card and Not interested covers all three; this runner passes on
        # one and then works with the others, so they are other roles here (another level)
        for uid, level in zip(self.uids[1:], ("II", "III")):
            self.conn.execute("UPDATE jobs SET title = ? WHERE uid = ?", (f"Machine Learning Engineer {level}", uid))
        self.conn.commit()
        for i in range(3):
            trackapi.create(self.conn, PID, {"company": f"Row Co {i}", "role_title": "ML Engineer", "status": "applied"})
        trackapi.create(self.conn, PID, {"company": "Gone Co", "role_title": "ML Engineer", "status": "rejected"})
        rg_id = self.rg.add(PID, "Acme", "Machine Learning Engineer, Search", OTHER_JD, source_job_id=dup_new, status="ready",
                            result={"files": {"pdf": "/x/r.pdf"}, "base": {"id": "ml", "label": "ML Engineer"},
                                    "approved": {"link": "https://drive.example/F1", "name": "Tess_Resume.pdf"}},
                            drive_file_id="F1", drive_link="https://drive.example/F1", gdoc_link="https://docs.example/d1",
                            files_version="2026-10-12T21:00:00Z", undo_count=1, approved_stale=True,
                            what_changed="Your note: Summary shorter", times={"made": "2026-10-12T20:00:00Z"})
        needs = self.rg.add(PID, "Acme", "Machine Learning Engineer", JD, source_job_id=self.uids[1], status="needs_you",
                            result={"files": {"pdf": "/x/n.pdf"}, "base": {"id": "ml", "label": "ML Engineer"}},
                            files_version="2026-10-12T22:00:00Z", undo_count=0,
                            reasons=[{"area": "Length", "text": "2 pages"}], times={"made": "2026-10-12T21:50:00Z"})
        fits.refresh(self.conn, config.profile(PID), NOW)
        feedcache.clear()
        srv = server.make_server(lan=False, port=0)
        srv.token = TOKEN
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            out = subprocess.run(["node", str(RUNNER), f"http://localhost:{srv.server_port}", TOKEN, CHROME,
                                  str(Path(self.tmp.name) / "chrome"),
                                  json.dumps({"pid": PID, "rg": self.rg.url, "rg_id": rg_id, "needs": needs, "dup_old": dup_old,
                                              "dup_new": dup_new})],
                                 capture_output=True, text=True, timeout=RUN_TIMEOUT_S)
        finally:
            srv.shutdown()
            srv.server_close()
        lines = [ln for ln in out.stdout.splitlines() if ln.startswith("{")]
        checks = json.loads(lines[-1])["checks"] if lines else [{"name": "runner", "ok": False, "detail": out.stderr[-3000:]}]
        for check in checks:
            with self.subTest(check["name"]):
                self.assertTrue(check["ok"], check["detail"])
        self.assertGreaterEqual(len(checks), 30, [c["name"] for c in checks])
        sent = [name for job, name, _ in self.rg.actions if job == rg_id]
        for name in ("reimport", "undo_edit", "approve", "cancel"):
            self.assertIn(name, sent)
        # the feed's Not interested ("Not remote") on this job archives its resume in resumegen first (this job is
        # now the first row when Shift+X removes one and Undo brings it back, an archive of its own)
        self.assertEqual([name for job, name, _ in self.rg.actions if job == needs][-4:],
                         ["archive", "fix", "accept", "regenerate"])


if __name__ == "__main__":
    unittest.main()
