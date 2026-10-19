"""the real Autofill extension in headless Chrome against the real job-hunt-agent server (a throwaway DB, a fake
person, a fake resume app): its settings page and a whole Workday application on fixture pages (Workday's real Create
Account / Sign In pages, saved 2026-10-14, then Workday-shaped steps) up to the person's Submit, which marks the job
Applied with the address used. Driven by tests/ui/goal23_extension.mjs. Skipped without node or Google Chrome."""
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import zlib
import unittest
from html import escape as html_escape
from pathlib import Path
from unittest import mock

from job_hunt_agent import config, db, rgclient, server, store
from job_hunt_agent.ats.common import FetchResult, make_job
from job_hunt_agent.autofill import address, bank, draft
from tests import test_goal8_flow as g8

ROOT = Path(__file__).resolve().parent.parent
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
RUNNER = Path(__file__).parent / "ui" / "goal23_extension.mjs"
FIXTURES = Path(__file__).parent / "fixtures" / "autofill"
TOKEN = "ext-test-token"
PASSWORD = 'Zz&Test"Pass-9!'
PID = "zztest"
URL = "https://zztest.wd5.myworkdayjobs.com/en-US/External/job/Foster-City-CA/AI-Engineer_JR0001"
RUN_TIMEOUT_S = 300
DRAFT = "I build retrieval and evaluation systems in Python. This role needs exactly that."
PROFILE = """name: Zz Testcase
phone: 555-010-0199
email: zz.testcase@example.com
location: Austin, TX
linkedin: https://www.linkedin.com/in/zz-testcase-example
github: https://github.com/zz-testcase-example
years: 3
needs_sponsorship: false
families: [ai_engineer]
resumes:
  - {id: ai, label: AI Engineer, file: Zz_Testcase_AI_Engineer_Resume.docx, families: [ai_engineer]}
"""
BASELINE = {"headline": "AI Engineer", "summary": "Fake.", "skills": [{"category": "AI", "items": ["Python"]}],
            "experience": [{"company": "Examplecorp, USA", "dates": "Jul 2024 – Present", "title": "AI Engineer", "bullets": ["x"]},
                           {"company": "Testworks, India", "dates": "Jun 2020 – Jul 2022", "title": "ML Engineer", "bullets": ["x"]}],
            "education": [{"degree": "Master of Science in Computer Science",
                           "school": "Lakeview State University, Springfield, IL, USA", "dates": "Aug 2022 – May 2024"}]}
DIFF = {"summary": {"new": "Fake."}, "jobs": [],          # the tailored Skills section, as resumegen's diff carries it
        "skills": [{"category": "Languages", "items": {"base": "Python", "new": "Languages: Python | PySpark, python"}},
                   {"category": "AI", "items": {"base": "", "new": "PyTorch, LangChain, RAG, LLM evaluation"}},
                   {"category": "Cloud", "items": {"base": "", "new": "Cloud: AWS (S3, Glue)"}}]}


@unittest.skipUnless(shutil.which("node") and Path(CHROME).exists(), "needs node and Google Chrome")
class ExtensionWorkdayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls.tmp.name)
        prof = tmp / "profiles" / PID
        (prof / "resumes").mkdir(parents=True)
        (prof / "profile.yaml").write_text(PROFILE)
        (prof / "baseline.json").write_text(json.dumps(BASELINE))
        (prof / "answers.json").write_text("{}")
        cls.patches = [mock.patch.object(config, "DB_PATH", tmp / "ext.db"), mock.patch.object(config, "PROFILES", tmp / "profiles")]
        cls.fake = g8.FakeResumegen()
        cls.patches += [mock.patch.object(rgclient, "_base", lambda: cls.fake.url),
                        mock.patch.object(rgclient, "_token", lambda: g8.TOKEN),
                        mock.patch.object(draft, "run", lambda prompt, pid: DRAFT)]           # no real Claude call in tests
        for p in cls.patches:
            p.start()
        conn = db.connect()
        item = make_job(ats="workday", key="zztest/wd5/External", ext_id="JR0001", company="ZZ Test Corp", title="AI Engineer",
                        location="Foster City, CA", url=URL, posted_at=db.iso(db.utcnow()), description="AI Engineer. Python.")
        db.sync_boards(conn, [{"board": "workday:zztest/wd5/External", "ats": "workday", "key": "zztest/wd5/External", "company": "ZZ Test Corp"}])
        run = db.start_run(conn, "test", db.utcnow())
        store.write_board(conn, db.board(conn, "workday:zztest/wd5/External"), FetchResult([item], 200, n_raw=1), "ok", run,
                          db.utcnow(), ["fam:ai_engineer"], 5)
        cls.uid = item["uid"]
        rg = cls.fake.add(PID, "ZZ Test Corp", "AI Engineer", "JD", source_job_id=cls.uid, status="ready", location="Foster City, CA",
                          workplace="onsite", result={"base": {"id": "ai", "label": "AI Engineer"}, "diff": DIFF}, drive_file_id="F1",
                          times={"made": db.iso(db.utcnow()), "approved": db.iso(db.utcnow())})
        cls.pdf = (b"%PDF-1.4\n% ZZ_TEST fake resume\n" + b"0" * 900)
        cls.fake.files[(rg, "pdf")] = (cls.pdf, "application/pdf")
        bank.ensure(conn, PID)
        for key, value in {"work_auth": "Yes", "sponsor_now": "No", "sponsor_future": "No", "age18": "Yes", "salary": "$120,000",
                           "address1": "11 Test Street", "zip": "78701",
                           "start_date": "2 weeks after offer acceptance"}.items():   # user 16:29: a wait, not a date
            bank.save(conn, PID, key, value)
        conn.execute("INSERT INTO osm_places(city_state, candidates, fetched_at) VALUES (?,?,?)",
                     ("Foster City, CA", json.dumps([{"street": "808 Comet Dr", "zip": "94404", "name": "Kula Kai Apartments", "own_zip": True}]),
                      db.iso(db.utcnow())))
        conn.commit()
        address.use(conn, PID, "Foster City, CA")
        conn.close()
        cls.srv = server.make_server(lan=False, port=0)
        cls.srv.token = TOKEN
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{cls.srv.server_port}"
        if os.environ.get("JH_HOLD"):                    # debugging: serve until that file is removed
            Path(os.environ["JH_HOLD"]).write_text(base)
            while Path(os.environ["JH_HOLD"]).exists():
                time.sleep(1)
        out = subprocess.run(["node", str(RUNNER), base, TOKEN, CHROME, str(tmp / "chrome"), str(ROOT / "extension"), str(FIXTURES)],
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

    def test_settings_keep_the_account_on_the_laptop(self):
        self.assert_group(["the extension loads with its fixed id", "Test connection reaches job-hunt-agent", "the password box is masked",
                           "the account is kept in chrome.storage.local", "a request carrying the password is refused"])
        stored = (Path(self.tmp.name) / "ext.db").read_bytes()
        for form in (PASSWORD, html_escape(PASSWORD), json.dumps(PASSWORD)[1:-1]):
            self.assertNotIn(form.encode(), stored)

    def test_workday_account_step(self):
        self.assert_group(["the panel shows the matched job", "the panel shows the tailored resume",
                           "the panel shows the address for the resume's city", "the posting says click Apply",
                           "an apply page keeps the posting this tab opened", "Not this job? shows the picker",
                           "a pick is matched", "the pick is remembered for the page",
                           "Create Account is filled on its own", "the email is the job-site email", "the password is filled",
                           "the terms box is ticked", "the robots-only box stays empty", "it says click Create Account",
                           "Create Account is never clicked", "a page snapshot is sent to job-hunt-agent", "an existing account switches to Sign In",
                           "Sign In gets the email and password", "it says click Sign In",
                           "only the Sign In link was clicked, never Sign In itself", "Capital One's real Sign In page is filled",
                           "Sign In is never clicked", "the email-check step says check your email",
                           "the email-check step clicks nothing", "NVIDIA's real Sign In chooser: email chosen, then filled",
                           "only Sign in with email was chosen, never Sign In", "a human check pauses it", "nothing is filled under a check",
                           "a robot box is left for the user, the form filled"])

    def test_workday_my_information_and_experience(self):
        self.assert_group(["My Information fills on its own", "How did you hear: Company Website", "previously worked here: No",
                           "legal name", "address for the resume's city, street only", "phone type, country code and number",
                           "the panel counts what it filled", "My Experience fills on its own after the upload",
                           "the tailored resume was uploaded", "work history is exactly the user's: wrong rows fixed, junk removed",
                           "education is exactly the user's", "websites: exactly LinkedIn and GitHub (junk parsed rows fixed or removed)",
                           "every skill of the tailored resume, one at a time, closest match, no duplicates",
                           "a skill Workday does not list is named in the panel",
                           "Next was never clicked by the extension"])

    def test_workday_questions_and_disclosures(self):
        self.assert_group(["saved answers fill the questions", "a start date saved as \"2 weeks after offer acceptance\" fills Workday's date parts with today + 2 weeks",
                           "and its dropdown picks the closest choice (Within 2 weeks)", "an unknown question is left for the user",
                           "the panel lists it under Needs you", "clicking a Needs-you item goes to it", "job-hunt-agent has it in Needs you, with its choices",
                           "answered once in job-hunt-agent, it fills", "the auto-fill toggle can be turned off", "the user moves to the next step", "and on again",
                           "with auto-fill off a new step waits", "a free-text question offers Draft",
                           "Draft puts Claude's text in the box, marked as a draft",
                           "a field the user typed in is never overwritten", "EEO questions are never guessed", "saved EEO answers fill",
                           "Self Identify: signature name, today's date, disability"])

    def test_workday_review_submit_and_applied(self):
        self.assert_group(["Review says review, then click Submit", "the extension never clicked Next or Submit",
                           "the confirmation marks it Applied", "with the address it used",
                           "another site opened in the same tab is not auto-filled with the last job",
                           "a generic site's Skills typeahead gets every skill it lists, one at a time",
                           "its missing skill is named, nothing typed is left behind", "a mm/dd/yyyy box gets today + 2 weeks",
                           "a date input gets today + 2 weeks", "a start dropdown gets the narrowest choice holding 2 weeks", "the generic form is never submitted",
                           "no page errors"])
        conn = db.connect()
        try:
            row = conn.execute("SELECT * FROM applications WHERE profile = ? AND job_uid = ?", (PID, self.uid)).fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "applied")
        self.assertEqual(row["address"], "808 Comet Dr, Foster City, CA 94404")

    def test_the_snapshot_carries_no_password(self):
        conn = db.connect()
        try:
            rows = conn.execute("SELECT html FROM autofill_snapshots WHERE profile = ?", (PID,)).fetchall()
        finally:
            conn.close()
        self.assertTrue(rows)
        html = zlib.decompress(rows[0]["html"]).decode()
        self.assertIn("createAccountSubmitButton", html)
        for form in (PASSWORD, html_escape(PASSWORD), html_escape(PASSWORD, quote=False), "Zz&amp;Test"):
            self.assertNotIn(form, html)

    def test_a_draft_is_kept_with_the_application(self):
        conn = db.connect()
        try:
            row = conn.execute("SELECT * FROM autofill_drafts WHERE profile = ?", (PID,)).fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row["text"], DRAFT)


@unittest.skipUnless(shutil.which("node"), "needs node")
class StartWaitTest(unittest.TestCase):
    """User 16:29: a start date saved as a wait ("2 weeks after offer acceptance") is today + the wait in a date box,
    and the closest choice in a dropdown (pick.js, run in node)."""

    def run_pick(self, expr):
        pick = Path(__file__).parent.parent / "extension" / "content" / "pick.js"
        out = subprocess.run(["node", "-e", f"const p = require({json.dumps(str(pick))}); console.log(JSON.stringify({expr}))"],
                             capture_output=True, text=True, timeout=30, check=True)
        return json.loads(out.stdout)

    def test_waits_become_dates_and_choices(self):
        a = "2 weeks after offer acceptance"
        self.assertEqual(self.run_pick(f"p.waitDate({json.dumps(a)}, new Date(2026, 11, 25))"),
                         {"mm": "01", "dd": "08", "yyyy": "2027"})
        self.assertIsNone(self.run_pick('p.waitDate("10/15/2026")'))
        self.assertIsNone(self.run_pick('p.waitDate("Yes")'))
        best = lambda opts: self.run_pick(f"(p.best({json.dumps(opts)}, [{json.dumps(a)}]) || {{}}).text || null")
        self.assertEqual(best(["Immediately", "2 weeks", "Within 2 weeks", "1 month"]), "2 weeks")
        self.assertEqual(best(["Immediately", "Within 2 weeks", "Within 1 month"]), "Within 2 weeks")
        self.assertEqual(best(["Immediately", "Less than 1 month", "1-3 months", "More than 3 months"]), "Less than 1 month")
        self.assertIsNone(best(["Immediately", "More than 1 month"]))            # nothing holds 2 weeks: Needs you
        self.assertEqual(self.run_pick('p.wait("1-3 months")'), {"lo": 30, "hi": 90})
