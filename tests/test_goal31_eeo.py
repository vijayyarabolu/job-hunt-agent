"""GOAL31 §2 (GOAL30-eeo): EEO filled for real. The real extension in headless Chrome against the real job-hunt-agent server
(a throwaway DB, GOAL23's fake person) on the choices the user's boards really showed: Workday with NVIDIA's and 4 other
tenants' exact lists (also a list still loading when it opens, a first click that opens nothing, the last list slow to
go away), the real Ashby page (its EEO survey is a second form after the main one), Lever and Breezy. Every EEO answer
is read back from the page and logged Filled; "Send page snapshot" keeps the page. Driven by tests/ui/goal31_eeo.mjs.
JH_EXT_DIR=<extension dir> runs it against another extension (1.6.0 fails it); JH_TIMINGS=<file> writes the checks,
timings and per-field log there. Skipped without node or Google Chrome."""
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
import zlib
from pathlib import Path
from unittest import mock

from job_hunt_agent import config, db, rgclient, server, store
from job_hunt_agent.ats.common import FetchResult, make_job
from job_hunt_agent.autofill import bank, draft
from tests import test_goal8_flow as g8
from tests.test_goal30_meaning import node_classify
from tests.test_goal23_extension import BASELINE, CHROME, DRAFT, FIXTURES, JD, PID, PROFILE, ROOT, TOKEN, URL

RUNNER = Path(__file__).parent / "ui" / "goal31_eeo.mjs"
RUN_TIMEOUT_S = 420
EEO_KEYS = ("gender", "race", "hispanic", "veteran", "disability")
# the user's saved answers, in the user's own words (autofill_answers, profile alex, 2026-10-20)
ANSWERS = {"gender": "Male", "race": "Asian / South Asian", "hispanic": "No", "veteran": "No",
           "disability": "No, I do not have a disability and have not had one in the past", "terms": "Yes"}


@unittest.skipUnless(shutil.which("node") and Path(CHROME).exists(), "needs node and Google Chrome")
class ExtensionEeoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)    # Chrome may still be writing its profile
        tmp = Path(cls.tmp.name)
        prof = tmp / "profiles" / PID
        (prof / "resumes").mkdir(parents=True)
        (prof / "profile.yaml").write_text(PROFILE)
        (prof / "baseline.json").write_text(json.dumps(BASELINE))
        (prof / "answers.json").write_text("{}")
        cls.fake = g8.FakeResumegen()
        cls.fake.answer_text = DRAFT
        cls.patches = [mock.patch.object(config, "DB_PATH", tmp / "eeo.db"), mock.patch.object(config, "PROFILES", tmp / "profiles"),
                       mock.patch.object(rgclient, "_base", lambda: cls.fake.url), mock.patch.object(rgclient, "_token", lambda: g8.TOKEN),
                       mock.patch.object(draft, "run", lambda prompt, pid: "[]")]        # no model call: every pick is by meaning alone
        for p in cls.patches:
            p.start()
        cls._seed()
        cls.srv = server.make_server(lan=False, port=0)
        cls.srv.token = TOKEN
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        ext = os.environ.get("JH_EXT_DIR") or str(ROOT / "extension")
        out = subprocess.run(["node", str(RUNNER), f"http://127.0.0.1:{cls.srv.server_port}", TOKEN, CHROME, str(tmp / "chrome"), ext,
                              str(FIXTURES)], capture_output=True, text=True, timeout=RUN_TIMEOUT_S)
        lines = [ln for ln in out.stdout.splitlines() if ln.startswith("{")]
        got = json.loads(lines[-1]) if lines else {"checks": [{"name": "runner", "ok": False, "detail": out.stderr[-3000:]}]}
        cls.checks, cls.timings = got["checks"], got.get("timings") or {}
        conn = db.connect()
        cls.fields = [dict(r) for r in conn.execute("SELECT board, step, label, key, status, why FROM autofill_fields ORDER BY id")]
        cls.snapshots = [dict(r) for r in conn.execute("SELECT board, url, html FROM autofill_snapshots")]
        conn.close()
        if os.environ.get("JH_TIMINGS"):
            Path(os.environ["JH_TIMINGS"]).write_text(json.dumps({"timings": cls.timings, "checks": cls.checks, "fields": cls.fields}, indent=1))

    @classmethod
    def _seed(cls):
        conn = db.connect()
        item = make_job(ats="workday", key="zztest/wd5/External", ext_id="JR0001", company="ZZ Test Corp", title="AI Engineer",
                        location="Foster City, CA", url=URL, posted_at=db.iso(db.utcnow()), description=JD)
        db.sync_boards(conn, [{"board": "workday:zztest/wd5/External", "ats": "workday", "key": "zztest/wd5/External", "company": "ZZ Test Corp"}])
        run = db.start_run(conn, "test", db.utcnow())
        store.write_board(conn, db.board(conn, "workday:zztest/wd5/External"), FetchResult([item], 200, n_raw=1), "ok", run,
                          db.utcnow(), ["fam:ai_engineer"], 5)
        bank.ensure(conn, PID)
        for key, value in ANSWERS.items():
            bank.save(conn, PID, key, value)
        conn.commit()
        conn.close()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.fake.stop()
        for p in reversed(cls.patches):
            p.stop()
        cls.tmp.cleanup()

    def test_every_browser_check_passed(self):
        self.assertTrue(self.checks)
        for c in self.checks:
            with self.subTest(c["name"]):
                self.assertTrue(c["ok"], c["detail"][:1500])

    def test_every_eeo_field_is_logged_filled(self):
        """A mapped EEO answer that matches a choice exactly or by meaning is Filled: never "no choice fits" (an empty
        or unopened list) and never "none of its choices means" (the wrong list read)."""
        eeo = [f for f in self.fields if f["key"] in EEO_KEYS]
        self.assertGreaterEqual({f["board"] for f in eeo}, {"workday", "ashby", "lever", "breezy"}, self.fields)
        for f in eeo:
            with self.subTest(board=f["board"], label=f["label"][:60]):
                self.assertEqual(f["status"], "filled", f)

    def test_send_page_snapshot_keeps_the_page(self):
        """Both clicks keep the page: the plain one, and one whose HTML is over 4 MB once JSON-escaped (cut to fit;
        1.6.0 sent it whole, job-hunt-agent refused it, and the panel said "sent")."""
        ashby = [s for s in self.snapshots if s["board"] == "ashby"]
        self.assertEqual(len(ashby), 2, [(s["board"], s["url"]) for s in self.snapshots])
        for snap in ashby:
            html = zlib.decompress(snap["html"]).decode()
            self.assertIn("ashby-survey-form-container", html)      # the EEO section the user will send next time
            self.assertIn("31001", snap["url"])
        self.assertLess(len(zlib.decompress(ashby[1]["html"])), 3 * 1024 * 1024)


SELF_IDENTIFY = "Voluntary Self-Identification of Disability"


@unittest.skipUnless(shutil.which("node"), "needs node")
class SelfIdentifyKeyTest(unittest.TestCase):
    """Workday's Self Identify form (2026-10-19 and 10-21): every box's ids carry "disability", so its Language
    dropdown was taken for the disability question ("none of its choices means No, I do not have a disability")."""

    def key(self, label, kind, hint, options=()):
        f = {"label": label, "kind": kind, "hints": [f"selfIdentifiedDisabilityData--{hint}"], "section": SELF_IDENTIFY,
             "options": list(options)}
        return node_classify("(c.field(xs) || {}).key || null", f)

    def test_the_language_dropdown_is_not_the_disability_question(self):
        self.assertNotEqual(self.key("Language", "listbox", "language", ["English", "Español"]), "disability")

    def test_the_disability_question_still_is(self):
        self.assertEqual(self.key("Please check one of the boxes below:", "checkboxes", "disabilityStatus",
                                  ["Yes, I have a disability, or have had one in the past",
                                   "No, I do not have a disability and have not had one in the past", "I do not want to answer"]),
                         "disability")


if __name__ == "__main__":
    unittest.main()
