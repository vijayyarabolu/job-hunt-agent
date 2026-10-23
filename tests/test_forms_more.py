"""1.2.1 (user 2026-10-14): the real Autofill extension in headless Chrome on forms with the sites' exact labels.
Paylocity's address (Postal Code, Administrative Area, Locality, StateSelect) and Cover Letter: filled or left empty,
never a question. Ashby's city typeahead with many Springfields: the one in the address's state, United States.
Breezy's Add Position / Add Education repeaters, a site no board script knows (its own words, an entry it parsed
wrong, its layout learned) and Workday's My Experience: every job of the tailored resume in order with its "• "
bullets, education without a start date unless required, the Summary box word for word, nothing duplicated.
Driven by tests/ui/forms_more.mjs. Skipped without node or Google Chrome."""
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from job_hunt_agent import config, db, rgclient, server, store
from job_hunt_agent.ats.common import FetchResult, make_job
from job_hunt_agent.autofill import bank, experience, match
from tests import test_goal8_flow as g8

ROOT = Path(__file__).resolve().parent.parent
CHROME = os.environ.get("JH_CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
RUNNER = Path(__file__).parent / "ui" / "forms_more.mjs"
FIXTURES = Path(__file__).parent / "fixtures" / "autofill"
TOKEN = "forms-test-token"
PID = "zztest"
JOB_URL = "https://zztest.wd5.myworkdayjobs.com/en-US/External/job/Springfield-MO/ML-Engineer_JR0002"
PAGES = ["https://recruiting.paylocity.com/Recruiting/Jobs/Apply/9001", "https://jobs.ashbyhq.com/zztest/abc-123/application",
         "https://zz.breezy.hr/p/abc123-ml-engineer/apply", "https://careers.zz-unknown.example/jobs/ml-engineer/apply"]
RUN_TIMEOUT_S = 300
PROFILE = """name: Zz Testcase
phone: 555-010-0199
email: zz.testcase@example.com
location: Kansas City, MO
years: 3
needs_sponsorship: false
families: [ml_engineer]
resumes:
  - {id: ml, label: ML Engineer, file: Zz_Testcase_ML_Engineer_Resume.docx, families: [ml_engineer]}
"""
BASELINE = {"headline": "ML Engineer", "summary": "Base.", "skills": [{"category": "ML", "items": ["Python"]}],
            "experience": [{"company": "Basecorp, USA", "dates": "Jan 2021 – Present", "title": "Engineer", "bullets": ["x"]}],
            "education": [{"degree": "Master of Science in Computer Science",
                           "school": "Lakeview State University, Springfield, IL, USA", "dates": "Aug 2022 – May 2024"}]}
SUMMARY = "ML engineer who ships retrieval and ranking systems in Python, from data pipelines to evaluation."
TAILORED = {"summary": SUMMARY, "jobs": [   # the tailored resume being uploaded (synthetic)
    {"company": "Globex Corp, Austin TX", "title": "Senior ML Engineer", "dates": "Mar 2023 – Present",
     "bullets": ["Built retrieval pipelines in Python.", "Cut model latency 40% with\n quantization."]},
    {"company": "Initech, USA", "title": "ML Engineer", "dates": "Jun 2020 – Feb 2023",
     "bullets": ["Shipped fraud models.", "- Ran A/B tests."]}]}


@unittest.skipUnless(shutil.which("node") and Path(CHROME).exists(), "needs node and Google Chrome")
class FormsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls.tmp.name)
        prof = tmp / "profiles" / PID
        (prof / "resumes").mkdir(parents=True)
        (prof / "profile.yaml").write_text(PROFILE)
        (prof / "baseline.json").write_text(json.dumps(BASELINE))
        (prof / "answers.json").write_text("{}")
        cls.fake = g8.FakeResumegen()
        cls.patches = [mock.patch.object(config, "DB_PATH", tmp / "forms.db"), mock.patch.object(config, "PROFILES", tmp / "profiles"),
                       mock.patch.object(rgclient, "_base", lambda: cls.fake.url), mock.patch.object(rgclient, "_token", lambda: g8.TOKEN),
                       mock.patch.object(experience, "resume_data", lambda pid, choice: TAILORED)]
        for p in cls.patches:
            p.start()
        conn = db.connect()
        item = make_job(ats="workday", key="zztest/wd5/External", ext_id="JR0002", company="ZZ Test Corp", title="ML Engineer",
                        location="Springfield, MO", url=JOB_URL, posted_at=db.iso(db.utcnow()), description="ML Engineer. Python.")
        db.sync_boards(conn, [{"board": "workday:zztest/wd5/External", "ats": "workday", "key": "zztest/wd5/External", "company": "ZZ Test Corp"}])
        run = db.start_run(conn, "test", db.utcnow())
        store.write_board(conn, db.board(conn, "workday:zztest/wd5/External"), FetchResult([item], 200, n_raw=1), "ok", run,
                          db.utcnow(), ["fam:ml_engineer"], 5)
        cls.uid = item["uid"]
        rg = cls.fake.add(PID, "ZZ Test Corp", "ML Engineer", "JD", source_job_id=cls.uid, status="ready", location="Springfield, MO",
                          workplace="onsite", result={"base": {"id": "ml", "label": "ML Engineer"}, "diff": {"summary": {"new": SUMMARY}, "jobs": []}},
                          drive_file_id="F2", times={"made": db.iso(db.utcnow()), "approved": db.iso(db.utcnow())})
        cls.fake.files[(rg, "pdf")] = (b"%PDF-1.4\n% ZZ_TEST fake resume\n" + b"0" * 900, "application/pdf")
        bank.ensure(conn, PID)
        conn.execute("INSERT INTO osm_places(city_state, candidates, fetched_at) VALUES (?,?,?)",
                     ("Springfield, MO", json.dumps([{"street": "123 Main Street", "zip": "65801", "name": "Zz Test Apartments", "own_zip": True}]),
                      db.iso(db.utcnow())))
        conn.commit()
        for page in PAGES + [JOB_URL]:
            match.remember(conn, PID, page, uid=cls.uid)
        conn.close()
        cls.srv = server.make_server(lan=False, port=0)
        cls.srv.token = TOKEN
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{cls.srv.server_port}"
        out = subprocess.run(["node", str(RUNNER), base, TOKEN, CHROME, str(tmp / "chrome"), str(ROOT / "extension"), str(FIXTURES),
                              json.dumps(PAGES), JOB_URL, json.dumps({"summary": SUMMARY, "jobs": experience.job_rows(TAILORED["jobs"])})],
                             capture_output=True, text=True, timeout=RUN_TIMEOUT_S)
        lines = [ln for ln in out.stdout.splitlines() if ln.startswith("{")]
        cls.checks = json.loads(lines[-1])["checks"] if lines else [{"name": "runner", "ok": False, "detail": out.stderr[-3000:]}]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.fake.stop()
        for p in reversed(cls.patches):
            p.stop()
        cls.tmp.cleanup()

    def assert_group(self, names):
        by_name = {c["name"]: c for c in self.checks}
        runner = by_name.get("runner")
        self.assertIsNone(runner, runner and runner["detail"])
        for name in names:
            with self.subTest(name):
                self.assertIn(name, by_name, "check did not run")
                self.assertTrue(by_name[name]["ok"], by_name[name]["detail"])

    def test_paylocity_address_is_never_a_question(self):
        self.assert_group(["Paylocity: street, Locality, Administrative Area, StateSelect and Postal Code filled from the address",
                           "Paylocity: a label showing its dropdown's pick is still the address",
                           "Paylocity: the cover letter is left empty", "Paylocity: nothing in Needs you"])
        conn = db.connect()
        try:
            asked = [r["text"] for r in conn.execute("SELECT text FROM autofill_questions WHERE profile = ?", (PID,))]
        finally:
            conn.close()
        self.assertEqual([x for x in asked if any(w in x.lower() for w in ("postal", "locality", "administrative", "stateselect", "street", "cover"))], [])

    def test_ashby_city_typeahead_picks_the_addresss_state(self):
        self.assert_group(["Ashby: the Springfield in Missouri, United States"])

    def test_breezy_repeaters(self):
        self.assert_group(["Breezy: one position per resume job, in order", "Breezy: dates and I currently work here",
                           "Breezy: each position's bullets, one \"• \" line each", "Breezy: education without a start date",
                           "Breezy: the Summary box is the resume's summary", "Breezy: the cover letter is left empty",
                           "Breezy: a second Autofill adds nothing", "Breezy: nothing in Needs you"])

    def test_an_unknown_site(self):
        self.assert_group(["unknown site: the entry it parsed is corrected, one added, in order",
                           "unknown site: MM/YYYY dates and Still employed here", "unknown site: Responsibilities are the bullets",
                           "unknown site: education by its own words, graduation month, no start date",
                           "unknown site: About you is the summary", "unknown site: its layout is remembered",
                           "unknown site: the next visit fills it the same way"])

    def test_workday_my_experience(self):
        self.assert_group(["Workday: the resume's jobs in order", "Workday: Role Description is the job's bullets", "no page errors"])


class ExperienceRowsTest(unittest.TestCase):
    def test_bullets_are_one_dot_line_each_in_order_without_blank_lines(self):
        got = experience.description(["  Built pipelines.", "", "- Ran A/B\n tests.", "• Kept one dot."])
        self.assertEqual(got, "• Built pipelines.\n• Ran A/B tests.\n• Kept one dot.")

    def test_jobs_keep_the_resume_order_with_dates_and_current(self):
        rows = experience.job_rows(TAILORED["jobs"])
        self.assertEqual([(r["title"], r["company"], r["location"], r["start"], r["end"], r["current"]) for r in rows],
                         [("Senior ML Engineer", "Globex Corp", "United States", "03/2023", "", True),   # 1.3.1: the country
                          ("ML Engineer", "Initech", "United States", "06/2020", "02/2023", False)])
        self.assertEqual(rows[1]["description"], "• Shipped fraud models.\n• Ran A/B tests.")


if __name__ == "__main__":
    unittest.main()
