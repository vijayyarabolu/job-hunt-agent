"""GOAL9 area B (the job list rows) in headless Chrome (tests/ui/goal9_b.mjs) against a throwaway DB, a synthetic
profile and a fake resumegen: the row layout overlap check at 1440 / 1280 / 1024 / 390 px over rows with very long
titles, companies and locations and every tag / chip combination (Applied, Applied before, Saved + liked, Too senior,
years not stated, resume chips queued / generating / ready / approved / needs you, the Generate resume slot, the
selected row, picking, the pick bar), the hover labels, the row shortcuts, optimistic updates + rollback,
the "N new jobs" pill. Screenshots land in docs/evidence/v9/b/ with JH_SHOTS=1 (else in the temp dir). Drive uploads are mocked; no network, no credits."""
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

import docx

from job_hunt_agent import api, applydialog, config, db, live, resume_upload, rgclient, server, store
from job_hunt_agent.ats.common import FetchResult, make_job
from tests.test_goal7_ui import CHROME
from tests.test_goal8_flow import PID, PROFILE, TOKEN as RG_TOKEN, FakeResumegen

NOW = db.utcnow().replace(microsecond=0)
RUNNER = Path(__file__).parent / "ui" / "goal9_b.mjs"
EVIDENCE = Path(__file__).parent.parent / "docs" / "evidence" / "v9" / "b"
TOKEN = "ui9b-test-token"
RUN_TIMEOUT_S = 300
LONG_CO = "International Business Machines Corporation Consulting Services Division"
LONG_LOC = "San Francisco, CA; New York, NY; Austin, TX; Seattle, WA; Chicago, IL; Boston, MA"
LONG_TITLE = ("Senior Machine Learning Engineer, Recommendations, Ranking and Personalization Platform "
              "(Remote within the US or Canada, Hybrid)")
# (board key, company, title, location, requirements line)
JOBS = (("acme", "Acme", "Machine Learning Engineer", "Austin, TX", "- 3+ years of experience building ML systems in Python and PyTorch."),
        ("acme", "Acme", "Machine Learning Engineer", "Austin, TX", "- 3+ years of experience building ML systems in Python and PyTorch."),
        ("ibm", LONG_CO, LONG_TITLE, LONG_LOC, "- 2+ years with Python, PyTorch, Kubernetes and AWS at scale."),
        ("acme", "Acme", "Senior AI Engineer", "Austin, TX", "- 8+ years building ML systems in Python and PyTorch on Kubernetes and AWS."),
        ("acme", "Acme", "AI Engineer", "Remote", "- Python, LLM evaluation and RAG pipelines on AWS for production systems."),
        ("ibm", LONG_CO, "ML Platform Engineer", "Austin, TX", "- 2+ years of Kubernetes, MLflow, Python and AWS for model serving."),
        ("acme", "Acme", "Applied AI Engineer", "Austin, TX", "- 1+ years of Python, LLM evaluation and RAG pipelines on AWS."))
FRESH = ("acme", "Acme", "Machine Learning Engineer II", "Austin, TX", "- 2+ years of Python, PyTorch, Kubernetes and AWS in production ML.")
BOARDS = [{"board": "lever:acme", "ats": "lever", "key": "acme", "company": "Acme"},
          {"board": "lever:ibm", "ats": "lever", "key": "ibm", "company": LONG_CO}]


def _item(i, key, company, title, location, line, when):
    return make_job(ats="lever", key=key, ext_id=str(i), company=company, title=title, location=location,
                    url=f"http://127.0.0.1:9/{key}/{i}", posted_at=db.iso(when),
                    description="About the role.\nRequirements:\n" + line + "\n" + "We ship ML products. " * 8)


def seed(conn) -> list[str]:
    db.sync_boards(conn, BOARDS)
    run = db.start_run(conn, "test", NOW)
    items = [_item(i, *spec, NOW - timedelta(minutes=10 * (i + 1))) for i, spec in enumerate(JOBS)]
    for key in ("acme", "ibm"):
        mine = [it for it in items if it["uid"].split(":")[1] == key]
        store.write_board(conn, db.board(conn, f"lever:{key}"), FetchResult(mine, 200, n_raw=len(mine)), "ok", run, NOW,
                          ["fam:ml_engineer", "fam:ai_engineer"], 5)
    db.finish_run(conn, run, NOW, [])
    conn.commit()
    uids = [it["uid"] for it in items]
    applydialog.mark(conn, PID, NOW, uids[0])                        # applied, then closed: job 1 (same role) -> Applied before
    with conn:
        conn.execute("UPDATE jobs SET closed_at = ? WHERE uid = ?", (db.iso(NOW), uids[0]))
    applydialog.mark(conn, PID, NOW, uids[6])                        # Applied
    api.feedback_action(conn, PID, uids[2], "like", None, NOW)       # liked (and so saved)
    api.triage(conn, PID, uids[5], "saved", NOW)                      # Saved
    conn.commit()
    return uids


def seed_fresh(conn) -> None:
    """One more job in a new finished run: the live watcher tells the page, which shows the "1 new job" pill."""
    started = db.utcnow()
    run = db.start_run(conn, "test", started)
    item = _item(99, *FRESH, started)
    store.write_board(conn, db.board(conn, "lever:acme"), FetchResult([item], 200, n_raw=1), "ok", run, started,
                      ["fam:ml_engineer", "fam:ai_engineer"], 5)
    db.finish_run(conn, run, started, [])
    conn.commit()


def feed_when_asked(marker: Path, deadline_s: float) -> None:
    """The driver writes the marker file when it is ready for the fresh row; then the DB gets one."""
    end = time.monotonic() + deadline_s
    while not marker.exists() and time.monotonic() < end:
        time.sleep(0.2)
    if not marker.exists():
        return
    conn = db.connect()
    try:
        seed_fresh(conn)
    finally:
        conn.close()


@unittest.skipUnless(shutil.which("node") and Path(CHROME).exists(), "needs node and Google Chrome")
class Goal9RowsBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        resumes = root / "profiles" / PID / "resumes"
        resumes.mkdir(parents=True)
        (root / "profiles" / PID / "profile.yaml").write_text(PROFILE.replace("families: [ml_engineer]",
                                                                              "families: [ml_engineer, ai_engineer]")
                                                              + "feed_min_match: 0\n")      # the too-senior row scores under 60%
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
        cls.uids = seed(conn)
        conn.close()
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        cls.srv = server.make_server(lan=False, port=0)
        cls.srv.token = TOKEN
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.stop = threading.Event()                                  # the live watcher (SSE) as `serve` runs it
        threading.Thread(target=live.watch, args=(cls.stop,), kwargs={"on_change": server._warm}, daemon=True, name="live-watch").start()
        marker = root / "fresh.marker"
        threading.Thread(target=feed_when_asked, args=(marker, RUN_TIMEOUT_S), daemon=True).start()
        out = subprocess.run(["node", str(RUNNER), f"http://localhost:{cls.srv.server_port}", TOKEN, CHROME,
                              str(root / "chrome"), cls.rg.url, str(EVIDENCE if os.environ.get("JH_SHOTS") else root), str(marker)],
                             capture_output=True, text=True, timeout=RUN_TIMEOUT_S)
        lines = [ln for ln in out.stdout.splitlines() if ln.startswith("{")]
        cls.checks = json.loads(lines[-1])["checks"] if lines else [{"name": "runner", "ok": False, "detail": out.stderr}]

    @classmethod
    def tearDownClass(cls):
        cls.stop.set()
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

    def test_row_layout_never_overlaps_at_every_width(self):
        names = [c["name"] for c in self.checks if c["name"].startswith("no overlap")]
        self.assertGreaterEqual(len(names), 12, "the overlap check must run at 1440 / 1280 / 1024 / 390 in several states")
        self.assert_group(names + ["every tag and chip combination is on the page", "the chips show every status",
                                   "the Generate slot sits in the chip line, not over the time / worth / score",
                                   "the chip replaces the Generate slot once a resume exists",
                                   "screenshots saved"])

    def test_rows_keep_their_place_while_the_resume_layer_loads(self):
        # GOAL7 budget (CLS): rows used to shrink, then grow, ~1.5 s after the first paint (Lighthouse mobile 81)
        self.assert_group([f"rows keep their place while the page loads: {w}" for w in ("1440", "1280", "1024", "390")])

    def test_hover_labels_on_every_row_control(self):
        self.assert_group([c["name"] for c in self.checks if c["name"].startswith("labels:")]
                          + ["the pick box tip", "a tag tip", "a resume chip tip", "the Generate slot tip",
                             "a keyboard-focus tip", "a phone long-press tip shows", "release hides it"])

    def test_row_actions_are_instant_and_roll_back(self):
        self.assert_group(["a click selects the row at once", "the selection bar follows the row", "j / k move the selection",
                           "o opens the posting", "Space picks", "Shift-click picks a range without selecting text",
                           "Clear keeps the focus on the list", "a refused Generate rolls back the chip",
                           "the row Generate shows queued at once", "the row Generate queues that one job",
                           "g queues the selected job", "s saves the row at once", "the detail pane agrees",
                           "a refused save rolls back with a toast", "l likes at once", "x drops the row and the count",
                           "Undo puts it back", "a refused dismiss puts it back",
                           "the new-jobs pill appears", "the pill inserts the fresh row",
                           "the last row scrolls clear of the pick bar", "m opens the Mark applied dialog",
                           "no page errors"])


if __name__ == "__main__":
    unittest.main()
