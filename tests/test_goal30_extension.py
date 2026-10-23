"""GOAL30: the real Autofill extension in headless Chrome against the real job-hunt-agent server (a throwaway DB, GOAL23's
fake person and resume app) on NVIDIA's My Experience and Voluntary Disclosures and Exelixis' Application Questions,
in Workday's shape. Driven by tests/ui/goal30_ext.mjs. JH_EXT_DIR=<extension dir> runs it against another extension
(the GOAL29 one fails §0, §0b, §1.3, §1.5 and §3); JH_TIMINGS=<file> writes the §5 timings there;
JH_SHOTS=<dir> saves the panel screenshots (chips, Location, Regenerate) there. Skipped without node
or Google Chrome."""
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
from job_hunt_agent.autofill import address, bank, draft, keys
from tests import test_goal8_flow as g8
from tests.test_goal23_extension import BASELINE, CHROME, DIFF, DRAFT, FIXTURES, JD, PID, PROFILE, ROOT, TOKEN, URL

RUNNER = Path(__file__).parent / "ui" / "goal30_ext.mjs"
OPEN_Q = "What draws you to this role?"
OPEN_A = "I loved building retrieval systems at Globex and want to keep doing that work."
RUN_TIMEOUT_S = 300
ANSWERS = {"work_auth": "Yes", "sponsor_now": "No", "sponsor_future": "No", "age18": "Yes", "address1": "11 Test Street",
           "zip": "78701", "gender": "Male", "race": "Asian", "hispanic": "No", "veteran": "No", "terms": "Yes"}


@unittest.skipUnless(shutil.which("node") and Path(CHROME).exists(), "needs node and Google Chrome")
class ExtensionGoal30Test(unittest.TestCase):
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
        cls.patches = [mock.patch.object(config, "DB_PATH", tmp / "ext.db"), mock.patch.object(config, "PROFILES", tmp / "profiles"),
                       mock.patch.object(rgclient, "_base", lambda: cls.fake.url), mock.patch.object(rgclient, "_token", lambda: g8.TOKEN),
                       mock.patch.object(draft, "run", lambda prompt, pid: "[]")]        # no real Claude call in tests
        for p in cls.patches:
            p.start()
        cls._seed()
        cls.srv = server.make_server(lan=False, port=0)
        cls.srv.token = TOKEN
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{cls.srv.server_port}"
        ext = os.environ.get("JH_EXT_DIR") or str(ROOT / "extension")
        out = subprocess.run(["node", str(RUNNER), base, TOKEN, CHROME, str(tmp / "chrome"), ext, str(FIXTURES)],
                             capture_output=True, text=True, timeout=RUN_TIMEOUT_S)
        lines = [ln for ln in out.stdout.splitlines() if ln.startswith("{")]
        got = json.loads(lines[-1]) if lines else {"checks": [{"name": "runner", "ok": False, "detail": out.stderr[-3000:]}]}
        cls.checks, cls.timings = got["checks"], got.get("timings") or {}
        if os.environ.get("JH_TIMINGS"):                 # with the run's per-field log, for looking into a failure
            conn = db.connect()
            fields = [dict(r) for r in conn.execute("SELECT board, step, label, key, status, why FROM autofill_fields ORDER BY id")]
            conn.close()
            Path(os.environ["JH_TIMINGS"]).write_text(json.dumps({"timings": cls.timings, "checks": cls.checks, "fields": fields}, indent=1))

    @classmethod
    def _seed(cls):
        conn = db.connect()
        item = make_job(ats="workday", key="zztest/wd5/External", ext_id="JR0001", company="ZZ Test Corp", title="AI Engineer",
                        location="Foster City, CA", url=URL, posted_at=db.iso(db.utcnow()), description=JD)
        db.sync_boards(conn, [{"board": "workday:zztest/wd5/External", "ats": "workday", "key": "zztest/wd5/External", "company": "ZZ Test Corp"}])
        run = db.start_run(conn, "test", db.utcnow())
        store.write_board(conn, db.board(conn, "workday:zztest/wd5/External"), FetchResult([item], 200, n_raw=1), "ok", run,
                          db.utcnow(), ["fam:ai_engineer"], 5)
        rg = cls.fake.add(PID, "ZZ Test Corp", "AI Engineer", "JD", source_job_id=item["uid"], status="ready", location="Foster City, CA",
                          workplace="onsite", result={"base": {"id": "ai", "label": "AI Engineer"}, "diff": DIFF}, drive_file_id="F1",
                          times={"made": db.iso(db.utcnow()), "approved": db.iso(db.utcnow())})
        cls.fake.files[(rg, "pdf")] = (b"%PDF-1.4\n% ZZ_TEST fake resume\n" + b"0" * 900, "application/pdf")
        bank.ensure(conn, PID)
        for key, value in ANSWERS.items():
            bank.save(conn, PID, key, value)
        bank.save(conn, PID, "start_date", "")              # no saved date: the user's "2 weeks from today" setting
        # §2: the user's answer to an open question, written for Globex's job (it names Globex)
        qnorm = keys.norm(OPEN_Q, "ZZ Test Corp")
        bank.save(conn, PID, bank.CUSTOM + qnorm, OPEN_A, question=OPEN_Q)
        conn.execute("INSERT INTO autofill_learned(profile, scope, key, question, answer, how, company, job_uid, at) VALUES "
                     "(?,?,?,?,?,?,?,?,?)", (PID, "global", bank.CUSTOM + qnorm, OPEN_Q, OPEN_A, "hand", "Globex", "J-globex",
                                             "2026-10-01T10:00:00Z"))
        conn.execute("INSERT INTO autofill_snippets(profile, company, question, qnorm, text, at) VALUES (?,?,?,?,?,?)",
                     (PID, "Globex", OPEN_Q, qnorm, OPEN_A, "2026-10-01T10:00:00Z"))
        conn.execute("INSERT INTO osm_places(city_state, candidates, fetched_at) VALUES (?,?,?)",
                     ("Foster City, CA", json.dumps([{"street": "808 Comet Dr", "zip": "94404", "name": "Kula Kai Apartments", "own_zip": True}]),
                      db.iso(db.utcnow())))
        conn.commit()
        address.use(conn, PID, "Foster City, CA")
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

    def test_the_panel_opens_and_autofill_finishes_in_time(self):
        """§5 targets with room for a loaded test machine: the panel on a page in a running Chrome under 3 s (300 ms
        target; PROGRESS30-ext has the measured times), a whole Application Questions page under 10 s (3 s target),
        Regenerate to "Writing…" painted under 300 ms (100 ms target)."""
        self.assertLess(self.timings.get("panel_open_ms", 1e9), 3000, self.timings)
        self.assertLess(self.timings.get("autofill_to_last_field_ms", 1e9), 10000, self.timings)
        self.assertLess(self.timings.get("generate_to_writing_ms", 1e9), 300, self.timings)    # 100 ms target, painted


if __name__ == "__main__":
    unittest.main()
