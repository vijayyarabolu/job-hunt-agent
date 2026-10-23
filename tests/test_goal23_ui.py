"""GOAL23 §4/§6/§7 UI in a real browser: the Answers page (Needs you with option buttons, the bank, Addresses), its
badge, Settings › Autofill extension, the phone. A throwaway DB, the real server, headless Chrome driven by
tests/ui/goal23_answers.mjs. Skipped when node or Chrome is not installed."""
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from job_hunt_agent import config, db, rgclient, server
from job_hunt_agent.autofill import address, bank, questions

CHROME = os.environ.get("JH_CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
RUNNER = Path(__file__).parent / "ui" / "goal23_answers.mjs"
TOKEN = "ui-test-token"
RUN_TIMEOUT_S = 120
NEEDS = [{"label": "Have you ever worked for Acme before?", "kind": "radio", "options": ["Yes", "No"], "required": True},
         {"label": "Why do you want to work at Acme?", "kind": "textarea", "options": [], "required": True},
         {"label": "Are you a protected veteran?", "kind": "select", "required": True,
          "options": ["I am not a protected veteran", "I identify as a protected veteran", "I don't wish to answer"]}]


@unittest.skipUnless(shutil.which("node") and Path(CHROME).exists() and "alex" in config.profile_ids(),
                     "needs node, Google Chrome and the alex profile")
class AnswersPageUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.patch = mock.patch.object(config, "DB_PATH", Path(cls.tmp.name) / "ui.db")
        cls.patch.start()
        cls.offline = mock.patch.object(rgclient, "_base", lambda: "http://127.0.0.1:9")   # no resumegen here
        cls.offline.start()
        cls.who = mock.patch.object(config, "default_profile", lambda: "alex")    # the page opens as the user
        cls.who.start()
        conn = db.connect()
        bank.ensure(conn, "alex")
        bank.save(conn, "alex", "address1", "11 Test Street")
        bank.save(conn, "alex", "zip", "78701")
        bank.save(conn, "alex", "city", "Austin")
        bank.save(conn, "alex", "state", "Kansas")
        conn.execute("INSERT INTO osm_places(city_state, candidates, fetched_at) VALUES (?,?,?)",
                     ("McLean, VA", json.dumps([{"street": "7480 Birdwood Ave", "zip": "22102", "name": None,
                                                 "own_zip": True}]), db.iso(db.utcnow())))
        conn.commit()
        address.use(conn, "alex", "McLean, VA")
        questions.record(conn, "alex", NEEDS, "greenhouse", "Acme")
        conn.close()
        cls.srv = server.make_server(lan=False, port=0)
        cls.srv.token = TOKEN
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        base = f"http://localhost:{cls.srv.server_port}"
        out = subprocess.run(["node", str(RUNNER), base, TOKEN, CHROME, str(Path(cls.tmp.name) / "chrome")],
                             capture_output=True, text=True, timeout=RUN_TIMEOUT_S)
        lines = [ln for ln in out.stdout.splitlines() if ln.startswith("{")]
        cls.checks = json.loads(lines[-1])["checks"] if lines else [{"name": "runner", "ok": False, "detail": out.stderr}]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.offline.stop()
        cls.who.stop()
        cls.patch.stop()
        cls.tmp.cleanup()

    def assert_group(self, names):
        by_name = {c["name"]: c for c in self.checks}
        runner = by_name.get("runner")
        self.assertIsNone(runner, runner and runner["detail"])
        for name in names:
            with self.subTest(name):
                self.assertIn(name, by_name, "check did not run")
                self.assertTrue(by_name[name]["ok"], by_name[name]["detail"])

    def test_needs_you_is_answered_once_on_the_answers_page(self):
        self.assert_group(["the side menu has Answers with the Needs-you count", "Answers opens with Needs you on top",
                           "a choice question shows its options as buttons", "a legal question is marked your answer only",
                           "answering takes it off Needs you", "the badge counts down", "the answer is saved in the bank",
                           "a typed answer saves too"])

    def test_addresses_and_the_bank_are_editable(self):
        self.assert_group(["Addresses shows the home line and the saved city",
                           "an edited address is saved street only, USPS style", "an address can be removed",
                           "the whole bank is editable", "a bank edit saves on its own", "the server has the edit",
                           "work history is listed"])

    def test_the_start_date_setting_is_a_weeks_box(self):
        """GOAL30 §1.5."""
        self.assert_group(["the start date setting is a weeks box at 2", "3 weeks saves", "40 weeks is refused",
                           "the server keeps 3"])

    def test_settings_hands_out_the_extension(self):
        self.assert_group(["Settings has Get the Autofill extension", "it shows the 4 install steps",
                           "the token can be copied", "the download is the extension zip"])

    def test_phone(self):
        self.assert_group(["the phone's More menu has Answers", "Answers opens on a phone",
                           "nothing scrolls sideways on a phone", "no page errors"])
