"""GOAL36 §5.2 to §5.5: the extension reads the pages it could not (a form in a frame from an unknown address, drawn
late, in a shadow root, in an about:blank frame, a second step, a job job-hunt-agent knows only by company + title, a page
with no form), and Generate never ends in silence. The server half (match by company + title, Link to a job search)
runs on a throwaway DB; the browser half is tests/ui/goal36_scan.mjs with the real extension (version 1.7.0) on GOAL27's
fake person. `python -m tests.test_goal36_scan <old extension dir>` prints the per-page before / after table."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from job_hunt_agent import config, db, rgclient, server, store, tracker
from job_hunt_agent.ats.common import FetchResult, make_job
from job_hunt_agent.autofill import api, assist, bank, draft, experience, keys, match
from tests import test_goal8_flow as g8
from tests import test_goal27_matrix as m27
from tests.test_goal8_flow import NOW, PID as G8_PID, Goal8Case

ROOT = Path(__file__).resolve().parent.parent
RUNNER = ROOT / "tests" / "ui" / "goal36_scan.mjs"
FIXTURES = ROOT / "tests" / "fixtures" / "autofill"
COMPANY, TITLE = "ZZ_TEST Prairie Robotics", "Machine Learning Engineer"
PROUD = "Tell us about a project you are proud of"
SAVED_PROUD = "ZZ_TEST saved answer: I built a retrieval service in Python that analysts use every day."
JD = ("ZZ_TEST Prairie Robotics builds field robots. Location: Lawrence, KS.\n\nRequirements:\n"
      + "- Build and ship ML systems in Python and PyTorch, deploy them on AWS, and measure them with evals.\n" * 4)
RUN_TIMEOUT_S = 600


def add_job(conn, company=COMPANY, title=TITLE, ext_id="36001", board="greenhouse:zztestprairie") -> str:
    ats, key = board.split(":", 1)
    db.sync_boards(conn, [{"board": board, "ats": ats, "key": key, "company": company}])
    item = make_job(ats=ats, key=key, ext_id=ext_id, company=company, title=title, location="Lawrence, KS",
                    url=f"https://boards.greenhouse.io/{key}/jobs/{ext_id}", posted_at=db.iso(NOW), description=JD)
    run = db.start_run(conn, "test", NOW)
    store.write_board(conn, db.board(conn, board), FetchResult([item], 200, n_raw=1), "ok", run, NOW, ["fam:ml_engineer"], 5)
    return item["uid"]


class ByNameTest(Goal8Case):
    """§5.2: a page whose address is unknown is matched by the company and title it shows."""

    def setUp(self):
        super().setUp()
        self.uid = add_job(self.conn)

    def test_the_title_loses_the_sites_words(self):
        for raw, want in [("Job Application for Machine Learning Engineer at ZZ_TEST Prairie Robotics", TITLE),
                          ("Machine Learning Engineer - Prairie Careers", TITLE), ("Machine Learning Engineer | Careers", TITLE)]:
            with self.subTest(raw):
                self.assertEqual(match.clean_title(raw), want)
        self.assertEqual(match.company_key("ZZ_TEST Prairie Robotics, Inc."), match.company_key("zz_test prairie robotics"))

    def test_one_open_job_with_that_company_and_title_is_the_match(self):
        got = match.find(self.conn, G8_PID, "https://www.zztest-robotics.com/careers/ml-engineer", jobs=None,
                         shows={"company": "ZZ_TEST Prairie Robotics Inc.", "title": "Machine Learning Engineer | Careers"})
        self.assertEqual((got["job"]["uid"], got["job"]["how"]), (self.uid, "name"))

    def test_two_that_fit_are_offered_not_guessed(self):
        add_job(self.conn, title="Senior Machine Learning Engineer", ext_id="36002")
        add_job(self.conn, title="Machine Learning Engineer II", ext_id="36003")
        got = match.find(self.conn, G8_PID, "https://www.zztest-robotics.com/careers/ml", jobs=None,
                         shows={"company": COMPANY, "title": "Machine Learning Engineer, Robotics"})
        self.assertIsNone(got["job"])
        self.assertGreaterEqual(len(got["candidates"]), 2)

    def test_another_company_or_no_title_is_no_match(self):
        for page in ({"company": "ZZ_TEST Other Co", "title": TITLE}, {"company": COMPANY, "title": ""}):
            with self.subTest(page):
                got = match.find(self.conn, G8_PID, "https://www.zztest-robotics.com/careers/x", jobs=None, shows=page)
                self.assertIsNone(got["job"])
                self.assertEqual(got["candidates"], [])

    def test_link_to_a_job_searches_the_tracker_and_the_feed(self):
        tracker.ensure_profile(self.conn, G8_PID)
        with db.tx(self.conn):
            self.conn.execute("INSERT INTO applications(profile, company, role_title, status, source, updated_at) "
                              "VALUES (?, 'ZZ_TEST Lone Star Labs', 'Data Scientist', 'saved', 'manual', ?)",
                              (G8_PID, db.iso(NOW)))
        self.assertEqual([j["uid"] for j in match.search(self.conn, G8_PID, "prairie machine")], [self.uid])
        self.assertEqual([j["title"] for j in match.search(self.conn, G8_PID, "lone star")], ["Data Scientist"])
        self.assertEqual(match.search(self.conn, G8_PID, "x"), [])
        got = api.match_page(self.conn, G8_PID, {"find": "prairie"})
        self.assertEqual([j["uid"] for j in got["found"]], [self.uid])


def run_runner(ext_dir: Path, mode: str) -> dict:
    """The browser half on a throwaway job-hunt-agent: GOAL27's fake person, the job known by company + title only, a saved
    answer for the project question."""
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    prof = root / "profiles" / m27.PID
    (prof / "resumes").mkdir(parents=True)
    (prof / "profile.yaml").write_text(m27.PROFILE)
    (prof / "baseline.json").write_text(json.dumps(m27.BASELINE))
    (prof / "answers.json").write_text("{}")
    fake = g8.FakeResumegen()
    patches = [mock.patch.object(config, "DB_PATH", root / "scan.db"), mock.patch.object(config, "PROFILES", root / "profiles"),
               mock.patch.object(rgclient, "_base", lambda: fake.url), mock.patch.object(rgclient, "_token", lambda: g8.TOKEN),
               mock.patch.object(experience, "resume_data", lambda pid, choice: m27.TAILORED),
               mock.patch.object(draft, "skills_for", lambda pid, choice: m27.SKILLS),
               mock.patch.object(assist, "_run", lambda prompt, pid, model, runner=None: m27.fake_claude(prompt, pid)),
               mock.patch.object(draft, "run", m27.fake_claude)]
    for p in patches:
        p.start()
    srv = None
    try:
        assist._CACHE.clear()
        conn = db.connect()
        uid = add_job(conn)
        rg = fake.add(m27.PID, COMPANY, TITLE, JD, source_job_id=uid, status="ready", location="Lawrence, KS",
                      workplace="onsite", result={"base": {"id": "ml", "label": "ML Engineer"},
                                                  "diff": {"summary": {"new": m27.SUMMARY}, "jobs": []}},
                      drive_file_id="F36", times={"made": db.iso(db.utcnow()), "approved": db.iso(db.utcnow())})
        fake.files[(rg, "pdf")] = (b"%PDF-1.4\n% ZZ_TEST fake resume\n" + b"0" * 900, "application/pdf")
        bank.ensure(conn, m27.PID)
        for key, value in m27.SAVED.items():
            bank.save(conn, m27.PID, key, value)
        bank.save(conn, m27.PID, bank.CUSTOM + keys.norm(PROUD, COMPANY), SAVED_PROUD, question=PROUD)
        conn.commit()
        conn.close()
        srv = server.make_server(lan=False, port=0)
        srv.token = m27.TOKEN
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_port}"
        out = subprocess.run(["node", str(RUNNER), base, m27.TOKEN, m27.CHROME, str(root / "chrome"), str(ext_dir),
                              str(FIXTURES), mode, json.dumps(m27.pages())], capture_output=True, text=True,
                             timeout=RUN_TIMEOUT_S)
        lines = [ln for ln in out.stdout.splitlines() if ln.startswith("{")]
        return json.loads(lines[-1]) if lines else {"checks": [{"name": "runner", "ok": False, "detail": out.stderr[-3000:]}],
                                                     "measure": []}
    finally:
        if srv is not None:
            srv.shutdown()
            srv.server_close()
        fake.stop()
        for p in reversed(patches):
            p.stop()
        tmp.cleanup()


@unittest.skipUnless(shutil.which("node") and Path(m27.CHROME).exists(), "needs node and Chrome")
class ScanTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        got = run_runner(ROOT / "extension", "after")
        cls.checks, cls.measure = got["checks"], got["measure"]

    def group(self, names):
        by_name = {c["name"]: c for c in self.checks}
        self.assertNotIn("runner", by_name, by_name.get("runner", {}).get("detail"))
        for name in names:
            with self.subTest(name):
                self.assertIn(name, by_name, "check did not run")
                self.assertTrue(by_name[name]["ok"], by_name[name]["detail"])

    def test_each_hard_page_shows_the_panel_with_every_field(self):
        self.group([f"{p}: {what}" for p in ("iframe", "late", "shadow", "blank frame", "unknown job")
                    for what in ("the panel shows on its own", "every field is listed")])

    def test_each_step_is_scanned_again(self):
        self.group(["steps: step 1 is listed", "steps: step 2 is scanned on its own, with nothing typed",
                    "steps: the only click on Save and Continue is the person's"])

    def test_an_unknown_page_is_matched_by_company_and_title_else_offers_link_to_a_job(self):
        self.group(["unknown job: matched by the company and title on the page",
                    "iframe: the panel says the page is not linked to a job"])

    def test_nothing_found_says_why_and_offers_scan_again(self):
        self.group(["no form: the panel says why in words", "no form: Scan again is offered",
                    "no form: Scan again looks again and says why again"])

    def test_generate_never_does_nothing(self):
        self.group(["iframe: Generate on an unlinked page says why", "iframe: with an icon, Retry and Link to a job",
                    "unknown job: Generate writes the answer for this job"])

    def test_generate_next_to_a_saved_answer_leaves_it_alone(self):
        self.group(["unknown job: Autofill fills the saved answer",
                    "unknown job: Generate for this job is offered next to the saved answer",
                    "unknown job: Generate for this job writes a new one", "unknown job: the saved answer stays in the box"])

    def test_every_known_ats_page_still_lists_its_fields(self):
        names = [p["name"] for p in m27.pages() if not p["file"].endswith("_form.html")] + ["Workday"]
        self.group([f"{n}: fields are listed" for n in names])

    def test_no_page_error(self):
        self.group(["the extension reaches job-hunt-agent", "no page error"])


def before_after(old_ext: Path) -> str:
    """Markdown: per page, the panel shown and the fields it lists with the old extension and with this one."""
    old, new = run_runner(old_ext, "before")["measure"], run_runner(ROOT / "extension", "after")["measure"]
    lines = ["| Page | before: panel | before: fields | after: panel | after: fields | after: job |", "|---|---|---|---|---|---|"]
    by_page = {r["page"]: r for r in old}
    for r in new:
        o = by_page.get(r["page"], {})
        lines.append(f"| {r['page']} | {'shown' if o.get('shown') else 'not shown'} | {o.get('rows', 0)} | "
                     f"{'shown' if r['shown'] else 'not shown'} | {r['rows']} | {r['job'] or 'not linked'} |")
    return "\n".join(lines)


if __name__ == "__main__":
    print(before_after(Path(sys.argv[1] if len(sys.argv) > 1 else os.environ["JH_OLD_EXT"])))
