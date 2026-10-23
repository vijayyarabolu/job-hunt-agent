"""GOAL27: the adapter matrix. The real 1.3 extension in headless Chrome against a throwaway job-hunt-agent with a fake person,
one Autofill on each of 20 ATS apply pages (tests/fixtures/autofill/goal27/sites, served at each ATS's own address)
and on the Workday simulator's My Information, Questions and My Experience steps. Every expected control is read back;
the result is written to Prompts/ux27/matrix.md (site x field type: pass / fail) and every cell must pass. Also: never
Submit / Next, sensitive fields stay empty, short custom questions answered from the resume carry a review mark.
Driven by tests/ui/goal27_matrix.mjs. Skipped without node or Chrome (JH_CHROME names another Chromium)."""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from job_hunt_agent import config, db, rgclient, server, store
from job_hunt_agent.ats.common import FetchResult, make_job
from job_hunt_agent.autofill import assist, bank, draft, experience, match
from tests import test_goal8_flow as g8
from tests.test_forms_more import BASELINE, PROFILE, SUMMARY, TAILORED

ROOT = Path(__file__).resolve().parent.parent
CHROME = os.environ.get("JH_CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
RUNNER = Path(__file__).parent / "ui" / "goal27_matrix.mjs"
FIXTURES = Path(__file__).parent / "fixtures" / "autofill"
SITES = FIXTURES / "goal27" / "sites"
OUT = ROOT / "Prompts" / "ux27"
TOKEN = "matrix-test-token"
PID = "zztest"
JOB_URL = "https://zztest.wd5.myworkdayjobs.com/en-US/External/job/Springfield-MO/ML-Engineer_JR0027"
RUN_TIMEOUT_S = 900
SKILLS = ["Python", "PyTorch", "SQL"]
SAVED = {"work_auth": "Yes", "sponsor_now": "No", "sponsor_future": "No", "age18": "Yes", "gender": "Male", "hispanic": "No",
         "race": "Asian", "veteran": "I am not a protected veteran", "disability": "No, I do not have a disability",
         "salary": "150000", "start_date": "2 weeks after offer acceptance", "relocate": "Yes", "terms": "Yes",
         "linkedin": "https://www.linkedin.com/in/zz-testcase", "github": "https://github.com/zz-testcase",
         "signature_name": "Zz Testcase"}
COLUMNS = ["text", "email", "phone", "select", "dropdown", "typeahead", "radio", "checkbox", "yesno", "date", "file",
           "textarea", "repeater", "skills", "legal", "eeo", "resume-answer", "sensitive"]


def fake_claude(prompt: str, pid: str) -> str:
    """Claude Haiku's part, deterministic: a years question gets the resume's count, nothing else is invented."""
    if "pick the ONE option" in prompt:                   # 1.3.1: a choice no wording matched
        items = json.loads(prompt.split("Questions:\n", 1)[1])
        pick = lambda i: next((o for o in i["options"] if i["saved_answer"].lower() == "male" and re.search(r"\bman\b", o.lower())), "")
        return json.dumps([{"id": i["id"], "pick": pick(i)} for i in items])
    if "Write a cover letter" in prompt:
        return "Dear Hiring Team,\n\nI build retrieval systems in Python.\n\nSincerely,\nZz Testcase"
    found = re.search(r"Questions:\n(.*)\n\nJob:", prompt, re.S)
    questions = json.loads(found.group(1)) if found else []
    return json.dumps([{"id": q["id"], "answer": "5" if "years" in q["question"].lower() else ""} for q in questions])


def pages() -> list[dict]:
    spec = importlib.util.spec_from_file_location("make_fixtures", FIXTURES / "goal27" / "make_fixtures.py")
    mf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mf)
    out = []
    for name, (host, path, _title, body) in mf.SITES.items():
        board = name.replace("_form", "")
        out.append({"name": board.title() if board != "smartrecruiters" else "SmartRecruiters", "board": board, "host": host,
                    "path": path, "file": f"{name}.html", **({"frame": "#icims_content_iframe"} if body == "iframe" else {})})
    names = {"Icims": "iCIMS", "Successfactors": "SuccessFactors", "Bamboohr": "BambooHR", "Jazzhr": "JazzHR", "Adp": "ADP",
             "Generic": "Unknown site (generic)"}
    for p in out:
        p["name"] = names.get(p["name"], p["name"])
    return out


def table(rows: list[dict]) -> str:
    """Markdown: one row per site, one column per field type (✓ n / ✕ n / –)."""
    cols = [c for c in COLUMNS if any(c in r["cells"] for r in rows)]
    lines = ["| Site | " + " | ".join(cols) + " | Submit clicked |", "|" + "---|" * (len(cols) + 2)]
    for r in rows:
        cells = []
        for c in cols:
            cell = r["cells"].get(c)
            cells.append("–" if not cell else f"✓ {cell['pass']}" if not cell["fail"] else f"✕ {cell['fail']}/{cell['pass'] + cell['fail']}")
        lines.append(f"| {r['site']} | " + " | ".join(cells) + f" | {'YES' if any(not c.get('trusted') for c in r['clicks']) else 'never'} |")
    return "\n".join(lines)


@unittest.skipUnless(shutil.which("node") and Path(CHROME).exists(), "needs node and Chrome")
class MatrixTest(unittest.TestCase):
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
        cls.patches = [mock.patch.object(config, "DB_PATH", tmp / "matrix.db"), mock.patch.object(config, "PROFILES", tmp / "profiles"),
                       mock.patch.object(rgclient, "_base", lambda: cls.fake.url), mock.patch.object(rgclient, "_token", lambda: g8.TOKEN),
                       mock.patch.object(experience, "resume_data", lambda pid, choice: TAILORED),
                       mock.patch.object(draft, "skills_for", lambda pid, choice: SKILLS),
                       mock.patch.object(assist, "_run", lambda prompt, pid, model, runner=None: fake_claude(prompt, pid)),
                       mock.patch.object(draft, "run", fake_claude)]
        for p in cls.patches:
            p.start()
        assist._CACHE.clear()
        conn = db.connect()
        item = make_job(ats="workday", key="zztest/wd5/External", ext_id="JR0027", company="ZZ Test Corp", title="ML Engineer",
                        location="Springfield, MO", url=JOB_URL, posted_at=db.iso(db.utcnow()), description="ML Engineer. Python.")
        db.sync_boards(conn, [{"board": "workday:zztest/wd5/External", "ats": "workday", "key": "zztest/wd5/External", "company": "ZZ Test Corp"}])
        run = db.start_run(conn, "test", db.utcnow())
        store.write_board(conn, db.board(conn, "workday:zztest/wd5/External"), FetchResult([item], 200, n_raw=1), "ok", run,
                          db.utcnow(), ["fam:ml_engineer"], 5)
        cls.uid = item["uid"]
        rg = cls.fake.add(PID, "ZZ Test Corp", "ML Engineer", "JD", source_job_id=cls.uid, status="ready", location="Springfield, MO",
                          workplace="onsite", result={"base": {"id": "ml", "label": "ML Engineer"}, "diff": {"summary": {"new": SUMMARY}, "jobs": []}},
                          drive_file_id="F27", times={"made": db.iso(db.utcnow()), "approved": db.iso(db.utcnow())})
        cls.fake.files[(rg, "pdf")] = (b"%PDF-1.4\n% ZZ_TEST fake resume\n" + b"0" * 900, "application/pdf")
        bank.ensure(conn, PID)
        for key, value in SAVED.items():
            bank.save(conn, PID, key, value)
        conn.execute("INSERT INTO osm_places(city_state, candidates, fetched_at) VALUES (?,?,?)",
                     ("Springfield, MO", json.dumps([{"street": "123 Main Street", "zip": "65801", "name": "Zz Test Apartments", "own_zip": True}]),
                      db.iso(db.utcnow())))
        conn.commit()
        cls.pages = pages()
        for p in cls.pages:
            match.remember(conn, PID, f"https://{p['host']}{p['path']}", uid=cls.uid)
        match.remember(conn, PID, JOB_URL, uid=cls.uid)
        conn.close()
        cls.srv = server.make_server(lan=False, port=0)
        cls.srv.token = TOKEN
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{cls.srv.server_port}"
        out = subprocess.run(["node", str(RUNNER), base, TOKEN, CHROME, str(tmp / "chrome"), str(ROOT / "extension"), str(FIXTURES),
                              json.dumps(cls.pages), JOB_URL], capture_output=True, text=True, timeout=RUN_TIMEOUT_S)
        lines = [ln for ln in out.stdout.splitlines() if ln.startswith("{")]
        got = json.loads(lines[-1]) if lines else {"checks": [{"name": "runner", "ok": False, "detail": out.stderr[-3000:]}], "rows": []}
        cls.checks, cls.rows = got["checks"], got["rows"]
        conn = db.connect()
        cls.logged = [dict(r) for r in conn.execute("SELECT board, label, key, status, why FROM autofill_fields")]
        conn.close()
        if os.environ.get("JH_DEBUG"):
            Path(os.environ["JH_DEBUG"] + ".fields.json").write_text(json.dumps(cls.logged, indent=1))
        if cls.rows and not os.environ.get("JH_ONLY"):
            OUT.mkdir(parents=True, exist_ok=True)
            (OUT / "matrix.md").write_text("# GOAL27 adapter matrix (headless Chromium, fixtures)\n\n" + table(cls.rows) +
                                           "\n\n✓ n = n checked fields of that type filled right; ✕ f/n = f of n wrong; – = the page has none.\n")
            (OUT / "matrix.json").write_text(json.dumps(cls.rows, indent=1))

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.fake.stop()
        for p in reversed(cls.patches):
            p.stop()
        cls.tmp.cleanup()

    def test_runner(self):
        runner = next((c for c in self.checks if c["name"] == "runner"), None)
        self.assertIsNone(runner, runner and runner["detail"])

    def test_every_site_and_field_type(self):
        if not os.environ.get("JH_ONLY"):                               # a debugging run covers some sites only
            self.assertGreaterEqual(len(self.rows), 20)              # 19 ATSs (Workday among them) + an unknown site
        for c in self.checks:
            with self.subTest(c["name"]):
                self.assertTrue(c["ok"], c["detail"])

    def test_resume_answers_are_marked_for_review(self):
        marked = {r["site"]: r["marks"].get("review", 0) for r in self.rows if "resume-answer" in r["cells"]}
        self.assertTrue(marked)
        for site, n in marked.items():
            with self.subTest(site):
                self.assertGreaterEqual(n, 1)

    def test_a_disagreeing_prefilled_eeo_answer_is_corrected_and_said(self):
        vet = [r for r in self.logged if r["label"] == "Are you a protected Veteran?"]
        self.assertTrue(vet, "not logged")
        self.assertIn(("filled", "corrected: the form had “Yes”"), [(r["status"], r["why"]) for r in vet])   # the first fill
        self.assertNotIn("failed", [r["status"] for r in vet])
        gid = [r for r in self.logged if r["label"] == "Gender identity"]
        self.assertTrue(gid and all(r["status"] == "review" for r in gid), gid)

    def test_every_field_is_logged_without_values(self):
        self.assertTrue(self.logged)
        statuses = {r["status"] for r in self.logged}
        self.assertTrue({"filled", "review"} <= statuses, statuses)
        sensitive = [r for r in self.logged if r["key"] == "sensitive"]
        self.assertTrue(sensitive and all(r["status"] == "needs" for r in sensitive), sensitive)
        text = json.dumps(self.logged)
        for value in ("zz.testcase@example.com", "555-010-0199", "123 Main"):
            self.assertNotIn(value, text)


if __name__ == "__main__":
    unittest.main()
