"""GOAL25 §5 (user 2026-10-14 17:06): "Apply on laptop". A job queued on the phone (button or swipe) waits in that
person's Apply queue; on the laptop "Apply queue (N)" and "Open next 5" open the apply pages in new tabs, each tab told
its job by the extension; Mark applied (anywhere) takes it out; Jordan has her own queue. The browser part runs
tests/ui/goal25_queue.mjs with the real extension (skipped without node / Chrome)."""
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

from job_hunt_agent import api, applyq, config, db, rgclient, server
from tests.test_goal7_ui import CHROME
from tests.test_goal8_flow import PID, PROFILE, TOKEN as RG_TOKEN, FakeResumegen, Goal8Case
from tests.test_goal13_ui import NOW, PEOPLE, seed

RUNNER = Path(__file__).parent / "ui" / "goal25_queue.mjs"
EXT = Path(__file__).parent.parent / "extension"
TOKEN = "q25-test-token"
RUN_TIMEOUT_S = 300


class QueueTest(Goal8Case):
    def setUp(self):
        super().setUp()
        self.uids = seed(self.conn)
        self.later = NOW + timedelta(minutes=5)

    def test_a_queued_job_is_listed_with_its_apply_page_and_queuing_again_moves_it_to_the_end(self):
        a = applyq.add(self.conn, PID, self.uids["AI Engineer"], None, NOW)
        applyq.add(self.conn, PID, self.uids["Machine Learning Engineer"], None, NOW + timedelta(minutes=1))
        got = applyq.items(self.conn, PID, NOW)
        self.assertEqual([(g["company"], g["title"]) for g in got], [("Acme", "AI Engineer"), ("Acme", "Machine Learning Engineer")])
        self.assertTrue(got[0]["url"].startswith("http://127.0.0.1:9/acme/"))
        again = applyq.add(self.conn, PID, self.uids["AI Engineer"], None, self.later)
        self.assertEqual((again["id"], again["count"]), (a["id"], 2))
        self.assertEqual(applyq.items(self.conn, PID, NOW)[-1]["title"], "AI Engineer")

    def test_bad_requests_say_why(self):
        with self.assertRaises(ValueError):
            applyq.add(self.conn, PID, None, None, NOW)
        with self.assertRaises(KeyError):
            applyq.add(self.conn, PID, "lever:nope:1", None, NOW)
        with self.assertRaises(KeyError):
            applyq.remove(self.conn, PID, 999, NOW)

    def test_open_next_takes_the_unopened_first_then_the_longest_since_opened(self):
        for i, title in enumerate(("Machine Learning Engineer", "AI Engineer", "ML Platform Engineer")):
            applyq.add(self.conn, PID, self.uids[title], None, NOW + timedelta(seconds=i))
        first = applyq.take(self.conn, PID, 2, NOW)
        self.assertEqual([f["title"] for f in first], ["Machine Learning Engineer", "AI Engineer"])
        second = applyq.take(self.conn, PID, 2, self.later)
        self.assertEqual([f["title"] for f in second], ["ML Platform Engineer", "Machine Learning Engineer"])

    def test_mark_applied_anywhere_takes_it_out_and_the_queue_can_mark_it_too(self):
        applyq.add(self.conn, PID, self.uids["AI Engineer"], None, NOW)
        q = applyq.add(self.conn, PID, self.uids["Machine Learning Engineer"], None, NOW)
        api.mark_applied(self.conn, PID, self.uids["AI Engineer"], NOW)            # from job_hunt_agent, not the queue
        self.assertEqual([i["title"] for i in applyq.items(self.conn, PID, NOW)], ["Machine Learning Engineer"])
        out = applyq.applied(self.conn, PID, q["id"], NOW)
        self.assertEqual(out["count"], 0)
        status = self.conn.execute("SELECT status FROM applications WHERE id = ?", (out["app"],)).fetchone()["status"]
        self.assertEqual(status, "applied")

    def test_each_person_has_their_own_queue(self):
        applyq.add(self.conn, PID, self.uids["AI Engineer"], None, NOW)
        self.assertEqual(applyq.count(self.conn, "tester2", NOW), 0)
        got = server.route(self.conn, "GET", "/api/applyq", {"profile": PID}, NOW)
        self.assertEqual(got["count"], 1)
        with self.assertRaises(KeyError):
            applyq.remove(self.conn, "tester2", got["items"][0]["id"], NOW)          # another person's row


@unittest.skipUnless(shutil.which("node") and Path(CHROME).exists(), "needs node and Google Chrome")
class QueueBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        for pid, name in PEOPLE.items():
            resumes = root / "profiles" / pid / "resumes"
            resumes.mkdir(parents=True)
            (root / "profiles" / pid / "profile.yaml").write_text(
                PROFILE.replace("Tess Tester", name).replace("families: [ml_engineer]", "families: [ml_engineer, ai_engineer]"))
            doc = docx.Document()
            doc.add_paragraph(f"{name}. ML Engineer. Python, PyTorch, Kubernetes, AWS. 2021 - 2025.")
            doc.save(resumes / "Tess_ML.docx")
        cls.rg = FakeResumegen()
        cls.patches = [mock.patch.object(config, "DB_PATH", root / "q.db"), mock.patch.object(config, "PROFILES", root / "profiles"),
                       mock.patch.object(rgclient, "_base", lambda: cls.rg.url), mock.patch.object(rgclient, "_token", lambda: RG_TOKEN)]
        for p in cls.patches:
            p.start()
        conn = db.connect()
        cls.uids = seed(conn)
        cls.srv = server.make_server(lan=False, port=0)
        cls.srv.token = TOKEN
        base = f"http://localhost:{cls.srv.server_port}"
        with db.tx(conn):                                   # apply pages a tab can load (the throwaway server's 404)
            conn.execute("UPDATE jobs SET apply_url = ? || '/zz-apply/' || ext_id", (base,))
        conn.close()
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        out = subprocess.run(["node", str(RUNNER), base, TOKEN, CHROME, str(root / "chrome"), str(EXT),
                              cls.uids["Machine Learning Engineer"]], capture_output=True, text=True, timeout=RUN_TIMEOUT_S)
        lines = [ln for ln in out.stdout.splitlines() if ln.startswith("{")]
        cls.checks = json.loads(lines[-1])["checks"] if lines else [{"name": "runner", "ok": False, "detail": out.stderr[-3000:]}]
        conn = db.connect()
        cls.queue = [dict(r) for r in conn.execute("SELECT profile, job_uid, done_at IS NOT NULL AS done FROM apply_queue ORDER BY id")]
        conn.close()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        for p in reversed(cls.patches):
            p.stop()
        cls.rg.stop()
        cls.tmp.cleanup()

    def test_every_browser_check_passed(self):
        for c in self.checks:
            with self.subTest(c["name"]):
                self.assertTrue(c["ok"], c["detail"])
        self.assertGreaterEqual(len(self.checks), 8)

    def test_the_queue_ends_as_the_person_left_it(self):
        mine = [q for q in self.queue if q["profile"] == "tester"]
        self.assertEqual(sorted(q["done"] for q in mine), [0, 1])              # one marked applied, one waits
        self.assertEqual([q["done"] for q in self.queue if q["profile"] == "tester2"], [0])
