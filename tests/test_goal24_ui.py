"""GOAL24 in headless Chrome against the real job-hunt-agent server (a throwaway DB, synthetic DOL filings, a fake person
who needs sponsorship): badges with icon + word on the feed / job panel / Tracker / phone / the Autofill extension,
checked under deuteranopia and protanopia simulation; the Recommended switch and its counts; No sponsorship shown
last; Load more past the first 300 jobs; the Answers-page setting and the extension's future-sponsorship answer.
Driven by tests/ui/goal24_ui.mjs. Skipped without node or Google Chrome. Screenshots: JH_SHOTS=<dir> keeps them."""
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

from job_hunt_agent import config, db, rgclient, server, store, tracker
from job_hunt_agent.ats.common import FetchResult, make_job
from job_hunt_agent.autofill import bank
from job_hunt_agent.h1b import label, load
from tests import test_goal8_flow as g8
from tests.test_goal24_h1b import LCA_HEAD, LCA_ROWS, write_book

ROOT = Path(__file__).resolve().parent.parent
CHROME = os.environ.get("JH_CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
RUNNER = Path(__file__).parent / "ui" / "goal24_ui.mjs"
FORM = Path(__file__).parent / "fixtures" / "autofill" / "sponsor_form.html"
TOKEN = "ui24-test-token"
PID = "zztest"
HOST = "https://careers.zztest-sponsor.example/jobs"
FILLERS = 300
RUN_TIMEOUT_S = 300
PROFILE = """name: Zz Sponsortest
email: zz.sponsor@example.com
location: Austin, TX
years: 4
max_required_years: 4
needs_sponsorship: true
families: [ml_engineer]
resumes:
  - {id: ml, label: ML Engineer, file: Zz_ML.docx, families: [ml_engineer]}
"""
# company, JD (the 4 badges and an unknown; Globex's post rules sponsorship out)
KEY_JOBS = [("Acme Robotics", "acme-1", "Build ML models in Python."),
            ("State University of Testing", "uni-1", "Research ML engineer for our lab."),
            ("Initech", "initech-1", "ML engineer for our platform."),
            ("Globex", "globex-1", "ML engineer. We are unable to sponsor work visas for this role; sponsorship is not available."),
            ("Nobody Filed Inc", "nobody-1", "ML engineer, early-stage team.")]


def seed(conn) -> dict[str, str]:
    now = db.utcnow()
    items = [make_job(ats="lever", key="zzspons", ext_id=ext, company=company, title="Machine Learning Engineer",
                      location="Austin, TX", url=f"{HOST}/{ext}", posted_at=db.iso(now), description=jd)
             for company, ext, jd in KEY_JOBS]
    items += [make_job(ats="lever", key="zzspons", ext_id=f"fill-{n}", company=f"ZZ Filler {n:03d}",
                       title="Machine Learning Engineer", location="Austin, TX", url=f"{HOST}/fill-{n}",
                       posted_at=db.iso(now), description="ML engineer. Python.") for n in range(FILLERS)]
    db.sync_boards(conn, [{"board": "lever:zzspons", "ats": "lever", "key": "zzspons", "company": "ZZ Sponsor Test"}])
    run = db.start_run(conn, "test", now)
    store.write_board(conn, db.board(conn, "lever:zzspons"), FetchResult(items, 200, n_raw=len(items)), "ok", run, now,
                      ["fam:ml_engineer"], 5)
    uids = {i["company"]: i["uid"] for i in items}
    with db.tx(conn):
        conn.execute("UPDATE jobs SET closed_at = NULL")
        conn.execute("UPDATE jobs SET sponsorship = 'no' WHERE uid = ?", (uids["Globex"],))
    return uids


@unittest.skipUnless(shutil.which("node") and Path(CHROME).exists(), "needs node and Google Chrome")
class Goal24BrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls.tmp.name)
        prof = tmp / "profiles" / PID
        (prof / "resumes").mkdir(parents=True)
        (prof / "profile.yaml").write_text(PROFILE)
        (prof / "answers.json").write_text("{}")
        doc = docx.Document()
        doc.add_paragraph("Zz Sponsortest. ML Engineer. Python, PyTorch. 2021 - 2025.")
        doc.save(prof / "resumes" / "Zz_ML.docx")
        filings = tmp / "h1b"
        filings.mkdir()
        write_book(filings / "LCA_Disclosure_Data_FY2026_Q3.xlsx", LCA_HEAD, LCA_ROWS)
        cls.fake = g8.FakeResumegen()
        cls.patches = [mock.patch.object(config, "DB_PATH", tmp / "ui24.db"), mock.patch.object(config, "PROFILES", tmp / "profiles"),
                       mock.patch.object(rgclient, "_base", lambda: cls.fake.url),
                       mock.patch.object(rgclient, "_token", lambda: g8.TOKEN)]
        for p in cls.patches:
            p.start()
        conn = db.connect()
        load.load_dir(conn, filings, log=lambda _m: None)
        uids = seed(conn)
        tracker.ensure_profile(conn, PID)
        with db.tx(conn):
            conn.execute("INSERT INTO applications(profile, company, role_title, applied_on, status, job_uid) "
                         "VALUES (?, 'Acme Robotics', 'Machine Learning Engineer', '2026-10-10', 'applied', ?)",
                         (PID, uids["Acme Robotics"]))
        for key, value in {"first_name": "Zz", "email": "zz.sponsor@example.com", "sponsor_now": "No",
                           "sponsor_future": "No", "work_auth": "Yes"}.items():
            bank.save(conn, PID, key, value)
        conn.close()
        label.forget()
        cls.srv = server.make_server(lan=False, port=0)
        cls.srv.token = TOKEN
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        shots = Path(os.environ.get("JH_SHOTS") or tmp / "shots")
        shots.mkdir(parents=True, exist_ok=True)
        out = subprocess.run(["node", str(RUNNER), f"http://127.0.0.1:{cls.srv.server_port}", TOKEN, CHROME, str(tmp / "chrome"),
                              str(ROOT / "extension"), str(FORM), str(shots)], capture_output=True, text=True, timeout=RUN_TIMEOUT_S)
        lines = [ln for ln in out.stdout.splitlines() if ln.startswith("{")]
        cls.checks = json.loads(lines[-1])["checks"] if lines else [{"name": "runner", "ok": False, "detail": out.stderr[-3000:]}]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.fake.stop()
        for p in reversed(cls.patches):
            p.stop()
        label.forget()
        cls.tmp.cleanup()

    def assert_group(self, names):
        by_name = {c["name"]: c for c in self.checks}
        runner = by_name.get("runner")
        self.assertIsNone(runner, runner and runner["detail"])
        for name in names:
            with self.subTest(name):
                self.assertIn(name, by_name, "check did not run")
                self.assertTrue(by_name[name]["ok"], by_name[name]["detail"])

    def test_badges_are_shape_and_word_in_safe_colours(self):
        self.assert_group(["the feed loads", "a sponsors badge has its icon and word", "a cap_exempt badge has its icon and word",
                           "a maybe badge has its icon and word", "a no badge has its icon and word", "hover shows the evidence",
                           "a job with nothing known has no badge", "no badge is green",
                           "Sponsors is solid blue, No sponsorship solid red", "every badge's word is readable (4.5:1)",
                           "deuteranopia: every badge's word stays readable (4.5:1)",
                           "deuteranopia: Sponsors and No sponsorship still look different",
                           "protanopia: every badge's word stays readable (4.5:1)",
                           "protanopia: Sponsors and No sponsorship still look different",
                           "the four badges differ by icon and word", "dark mode: the Sponsors word is readable"])

    def test_ranking_switch_and_load_more(self):
        self.assert_group(["sponsors lead the day", "the first page is 300 jobs", "Load more brings the rest (no cap)",
                           "No sponsorship is shown, ranked last", "Sponsored before comes after the unknowns of its day",
                           "the switch shows counts", "No sponsorship lists only those",
                           "Sponsor-friendly lists Sponsors, Cap-exempt and Sponsored before (no Unknown)",
                           "the switch is remembered", "the address names the switch", "a reload keeps Sponsor-friendly",
                           "a link without the switch shows All"])

    def test_panel_tracker_answers_and_phone(self):
        self.assert_group(["the job panel shows the badge and its evidence", "a Tracker row shows its company's badge, icon and word",
                           "Answers shows the sponsorship setting, On", "turning it off is saved",
                           "phone: badges show with their word", "phone: the switch fits", "phone: no sideways scroll",
                           "no page errors (web UI)"])

    def test_extension_panel_and_sponsorship_answer(self):
        self.assert_group(["the extension connects", "the panel shows the job's badge (icon + word)",
                           "the panel says which answer it will fill", "at a sponsor the future question gets Yes",
                           "the need-it-now question keeps the saved answer",
                           "with the setting Off the panel says the saved answer",
                           "with the setting Off the future question gets the saved answer", "the form is never submitted",
                           "no page errors (extension)"])


if __name__ == "__main__":
    unittest.main()
