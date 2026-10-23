"""GOAL33 §1: resumegen's news reaches the page live. Unit: the hub counts each person's resumegen events and wakes
the streams; rgwatch follows a resumegen event stream only while a tab is open. Browser (tests/ui/goal33_live.mjs,
skipped without node / Chrome): a resume made in resumegen shows in Tailored resumes within 2 s and moves through
Queue, Generating, Needs you and Ready without a reload, for both people."""
import json
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import docx

from job_hunt_agent import config, db, live, rgclient, rgwatch, server
from tests.test_goal13_ui import PEOPLE, seed
from tests.test_goal7_ui import CHROME
from tests.test_goal8_flow import PROFILE, TOKEN as RG_TOKEN, FakeResumegen

RUNNER = Path(__file__).parent / "ui" / "goal33_live.mjs"
TOKEN = "live33-test-token"
RUN_TIMEOUT_S = 240


class HubTest(unittest.TestCase):
    def test_each_persons_resumegen_events_are_counted_and_wake_the_streams(self):
        hub = live.Hub()
        seen = hub.version
        hub.bump_rg("tester")
        self.assertNotEqual(hub.wait(seen, 1), seen)
        self.assertEqual((hub.event("tester")["rg"], hub.event("tester2")["rg"]), (1, 0))
        hub.bump_rg(None)                                    # news for no one in particular: everyone asks
        self.assertEqual((hub.event("tester")["rg"], hub.event("tester2")["rg"]), (2, 1))


class WatchTest(unittest.TestCase):
    def setUp(self):
        self.rg = FakeResumegen()
        self.patches = [mock.patch.object(rgclient, "_base", lambda: self.rg.url),
                        mock.patch.object(rgclient, "_token", lambda: RG_TOKEN)]
        for p in self.patches:
            p.start()
        self.hub, self.stop = live.Hub(), threading.Event()

    def tearDown(self):
        self.stop.set()
        for p in reversed(self.patches):
            p.stop()
        self.rg.stop()

    def _until(self, cond, s=5.0):
        end = time.monotonic() + s
        while time.monotonic() < end and not cond():
            time.sleep(0.02)
        return cond()

    def test_an_event_for_a_person_reaches_the_hub_while_a_tab_is_open(self):
        self.hub.join()                                      # one tab open
        threading.Thread(target=rgwatch.watch, args=(self.stop, self.hub), daemon=True).start()
        self.assertTrue(self._until(lambda: self.hub.event("tester2")["rg"] == 1))   # connected: every page asks once
        self.rg.add("tester", "Acme", "AI Engineer", "JD " * 40, status="queued")
        self.assertTrue(self._until(lambda: self.hub.event("tester")["rg"] == 2))
        time.sleep(0.2)
        self.assertEqual(self.hub.event("tester2")["rg"], 1)                         # not someone else's news

    def test_no_tab_open_no_stream(self):
        threading.Thread(target=rgwatch.watch, args=(self.stop, self.hub), daemon=True).start()
        time.sleep(0.5)
        self.rg.add("tester", "Acme", "AI Engineer", "JD " * 40, status="queued")
        time.sleep(0.5)
        self.assertEqual(self.hub.event("tester")["rg"], 0)

    def test_resumegen_offline_is_retried_quietly(self):
        self.rg.down = True
        self.hub.join()
        with self.assertNoLogs(level="WARNING"):
            t = threading.Thread(target=rgwatch.watch, args=(self.stop, self.hub), daemon=True)
            t.start()
            time.sleep(1)
            self.stop.set()
            t.join(5)
        self.assertFalse(t.is_alive())


    def test_a_stream_closed_at_once_backs_off(self):
        """GOAL33 review: resumegen accepting and closing at once (a restart) made a reconnect, and every tab refetch,
        each 0.5 s; a stream that sent nothing now counts as a failed try."""
        class Closed:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def __iter__(self):
                return iter(())
        calls = []
        self.hub.join()
        with mock.patch.object(rgwatch.urllib.request, "urlopen", lambda *a, **k: calls.append(1) or Closed()):
            t = threading.Thread(target=rgwatch.watch, args=(self.stop, self.hub), daemon=True)
            t.start()
            time.sleep(1.3)
            self.stop.set()
            t.join(5)
        self.assertEqual(len(calls), 1)


@unittest.skipUnless(shutil.which("node") and Path(CHROME).exists(), "needs node and Google Chrome")
class LiveBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        for pid, name in PEOPLE.items():
            resumes = root / "profiles" / pid / "resumes"
            resumes.mkdir(parents=True)
            (root / "profiles" / pid / "profile.yaml").write_text(PROFILE.replace("Tess Tester", name))
            doc = docx.Document()
            doc.add_paragraph(f"{name}. ML Engineer. Python, PyTorch, Kubernetes, AWS. 2021 - 2025.")
            doc.save(resumes / "Tess_ML.docx")
        cls.rg = FakeResumegen()
        cls.patches = [mock.patch.object(config, "DB_PATH", root / "l.db"), mock.patch.object(config, "PROFILES", root / "profiles"),
                       mock.patch.object(rgclient, "_base", lambda: cls.rg.url), mock.patch.object(rgclient, "_token", lambda: RG_TOKEN),
                       mock.patch.object(live, "HUB", live.Hub())]
        for p in cls.patches:
            p.start()
        conn = db.connect()
        seed(conn)
        conn.close()
        cls.stop = threading.Event()
        threading.Thread(target=rgwatch.watch, args=(cls.stop,), daemon=True).start()
        cls.srv = server.make_server(lan=False, port=0)
        cls.srv.token = TOKEN
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        out = subprocess.run(["node", str(RUNNER), f"http://localhost:{cls.srv.server_port}", TOKEN, CHROME, str(root / "chrome"),
                              cls.rg.url, ",".join(PEOPLE)], capture_output=True, text=True, timeout=RUN_TIMEOUT_S)
        lines = [ln for ln in out.stdout.splitlines() if ln.startswith("{")]
        cls.checks = json.loads(lines[-1])["checks"] if lines else [{"name": "runner", "ok": False, "detail": out.stderr[-3000:]}]

    @classmethod
    def tearDownClass(cls):
        cls.stop.set()
        live.HUB.stop()
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
        self.assertGreaterEqual(len(self.checks), 11)
