"""GOAL13 UI in headless Chrome (tests/ui/goal13_ui.mjs) against a throwaway DB, two synthetic people and a fake
resumegen: the Resumes tab (Queue / Ready / Done today, counts, Cancel, Move to top, "Added to queue (#n)"), Apply ->
resume in hand -> "Applied?" on the row -> Yes -> Done today -> next job, Undo, keys, today's progress, remembered
place, one card per req, the answers, Settings, the second person and the phone. Drive uploads are mocked."""
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
from tests.test_goal8_flow import PROFILE, TOKEN as RG_TOKEN, FakeResumegen

NOW = db.utcnow().replace(microsecond=0)
RUNNER = Path(__file__).parent / "ui" / "goal13_ui.mjs"
TOKEN = "ui13-test-token"
RUN_TIMEOUT_S = 300
PEOPLE = {"tester": "Tess Tester", "tester2": "Sam Second"}
LINE = "- 3+ years building ML systems in Python and PyTorch on AWS."
TITLES = ("Machine Learning Engineer", "AI Engineer", "ML Platform Engineer", "Applied AI Engineer",
          "Senior Machine Learning Engineer")
FILLER = ("Vision", "Speech", "Search", "Ranking", "Fraud", "Ads", "Robotics", "Health", "Payments", "Maps", "Retail",
          "Music", "Video", "Games", "Security", "Cloud", "Edge", "Mobile", "Growth", "Pricing")


def _job(i: int, title: str, location: str = "Austin, TX", minutes: int | None = None) -> dict:
    return make_job(ats="lever", key="acme", ext_id=str(i), company="Acme", title=title, location=location,
                    url=f"http://127.0.0.1:9/acme/{i}", req_id=f"R-{100 + i}",
                    posted_at=db.iso(NOW - timedelta(minutes=minutes if minutes is not None else 10 * (i + 1))),
                    description=f"About the role ({title}).\nRequirements:\n{LINE}\n" + "We ship ML products. " * 8)


def seed(conn) -> dict:
    """Jobs (the five resume jobs, a req posted in two places, 20 more to scroll) -> uid by title."""
    db.sync_boards(conn, [{"board": "lever:acme", "ats": "lever", "key": "acme", "company": "Acme"}])
    run = db.start_run(conn, "test", NOW)
    items = [_job(i, t) for i, t in enumerate(TITLES)]
    items += [_job(10, "Recommendation ML Engineer", "Austin, TX", 3), _job(11, "Recommendation ML Engineer", "Denver, CO", 4)]
    items += [_job(20 + i, f"{w} Machine Learning Engineer") for i, w in enumerate(FILLER)]
    store.write_board(conn, db.board(conn, "lever:acme"), FetchResult(items, 200, n_raw=len(items)), "ok", run, NOW,
                      ["fam:ml_engineer", "fam:ai_engineer"], 5)
    uids = {i["title"]: i["uid"] for i in items[:5]}
    with db.tx(conn):
        conn.execute("UPDATE jobs SET salary_min = 150000, salary_max = 180000 WHERE uid = ?",
                     (uids["Machine Learning Engineer"],))
        conn.execute("UPDATE jobs SET closed_at = ? WHERE uid = ?", (db.iso(NOW), uids["Senior Machine Learning Engineer"]))
    return uids


@unittest.skipUnless(shutil.which("node") and Path(CHROME).exists(), "needs node and Google Chrome")
class Goal13BrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        for pid, name in PEOPLE.items():
            resumes = root / "profiles" / pid / "resumes"
            resumes.mkdir(parents=True)
            (root / "profiles" / pid / "profile.yaml").write_text(
                PROFILE.replace("Tess Tester", name).replace("families: [ml_engineer]", "families: [ml_engineer, ai_engineer]"))
            (resumes / "Tess_ML.pdf").write_bytes(b"%PDF-1.4 base")
            doc = docx.Document()
            doc.add_paragraph(f"{name}. ML Engineer. Python, PyTorch, Kubernetes, AWS. 2021 - 2025.")
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
        uids = seed(conn)
        conn.close()
        jd = "JD text. " * 20
        for pid, title, status, extra in (
                ("tester", "Machine Learning Engineer", "ready", {"finished_at": "2026-10-08T09:00:00Z"}),
                ("tester", "AI Engineer", "needs_you", {"error": "page 2 has 3 lines", "finished_at": "2026-10-08T10:00:00Z"}),
                ("tester", "Senior Machine Learning Engineer", "ready", {"finished_at": "2026-10-08T08:00:00Z"}),
                ("tester", "ML Platform Engineer", "generating", {"started_at": db.iso(NOW - timedelta(minutes=2))}),
                ("tester", "Applied AI Engineer", "queued", {}),
                ("tester2", "Machine Learning Engineer", "ready", {"finished_at": "2026-10-08T11:00:00Z"})):
            cls.rg.add(pid, "Acme", title, jd, source_job_id=uids[title], status=status, **extra)
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

    def test_queue(self):
        self.assert_group(["the Resumes tab shows the ready count", "Queue shows generating now first", "the running time ticks with the list's clocks",
                           "Queue numbers the order they will run", "Move to top reaches the resume app",
                           "Cancel reaches the resume app", "Generate says Added to queue (#n)",
                           "View queue opens the queue"])

    def test_ready_apply_done(self):
        self.assert_group(["Ready lists needs-you first, then newest ready, closed last",
                           "GOAL14 §3b: the Resumes tab paints its rows in < 500 ms",
                           "GOAL14 §3b: back on Resumes the last board paints at once, no skeleton rows",
                           "GOAL14 §3b: after a page reload the kept board paints at once",
                           "a closed posting is badged Posting closed", "the Ready segment says how many need you (GOAL21 W13)", "a needs-you row is badged Needs you",
                           "a applies from the Resumes tab", "x passes on a Ready job and Undo brings it back",
                           "Apply downloads the ready resume",
                           "the download is the PDF under the base name", "Apply copies company, title and req id",
                           "coming back asks Applied? on the job's row", "and at the top of its detail",
                           "Yes marks it applied", "Yes approves its resume to Drive", "it moves to Done today",
                           "the next Ready job is selected", "the day's progress counts it",
                           "Undo puts it back in Ready", "Not interested takes it out of Ready"])

    def test_keys_place_duplicates_answers_settings(self):
        self.assert_group(["j and k move in the Resumes tab", "m opens Mark applied", "Enter opens the job",
                           "closing the resume panel keeps the list's place",
                           "switching tabs and back keeps the list's place", "a refresh keeps the selected job",
                           "a req in two places is one card with +1 location", "the answer chips are in the detail",
                           "a chip copies its answer", "the job's pay range is the Posted pay chip", "My salary ask is the saved answer, not the job's pay",
                           "an empty answer opens its editor in the panel", "Settings saves the daily goal", "Settings saves an answer",
                           "Apply in hand can be turned off"])

    def test_second_person_and_phone(self):
        self.assert_group(["the second person sees only her own resumes", "her Apply asks Applied? and Yes applies it",
                           "the phone bar has Resumes with its count", "the phone row has its buttons",
                           "the phone sheet asks Applied? at its top", "the phone sheet closes back to the same place",
                           "no page errors"])


if __name__ == "__main__":
    unittest.main()
