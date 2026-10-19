"""GOAL21 §2 / §4 server side: "Open resumegen ↗" (/open/resumegen) sends the browser to resumegen on the host name it
used for job-hunt-agent, with the person and never the token; the Google status splits into an alert (the user must act: a banner),
a sync note (it retries by itself: the status button) and the overnight line; ds.css is the same file in resumegen."""
import http.client
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from job_hunt_agent import api, config, db, rgclient, server

ROOT = Path(__file__).resolve().parents[1]
DS = ROOT / "job_hunt_agent" / "static" / "ds.css"
TOKEN = "goal21-shell-token"


class BrowserUrlTest(unittest.TestCase):
    def setUp(self):
        self.base = mock.patch.object(rgclient, "_base", lambda: "http://127.0.0.1:8770")
        self.base.start()

    def tearDown(self):
        self.base.stop()

    def test_the_host_the_browser_used_and_the_person(self):
        self.assertEqual(rgclient.browser_url("example-mac.tail1234.ts.net:8765", "jordan"),
                         "http://example-mac.tail1234.ts.net:8770/?profile=jordan")

    def test_localhost_and_no_person(self):
        self.assertEqual(rgclient.browser_url("localhost:8765", None), "http://localhost:8770/")

    def test_a_strange_host_falls_back_to_the_configured_one(self):
        for host in ("evil.example/x", "[::1]:8765", "a b", "", None, "x" * 300):
            with self.subTest(host=host):
                self.assertEqual(rgclient.browser_url(host, "alex"), "http://127.0.0.1:8770/?profile=alex")

    def test_the_person_is_quoted_and_no_token_goes_out(self):
        url = rgclient.browser_url("localhost:8765", "a&token=x")
        self.assertEqual(url, "http://localhost:8770/?profile=a%26token%3Dx")


class OpenResumegenRouteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.patches = [mock.patch.object(config, "DB_PATH", Path(cls.tmp.name) / "t.db"),
                       mock.patch.object(rgclient, "_base", lambda: "http://127.0.0.1:8770"),
                       mock.patch.object(rgclient, "_token", lambda: "the-secret-rg-token")]
        for p in cls.patches:
            p.start()
        db.connect().close()
        cls.srv = server.make_server(lan=False, port=0)
        cls.srv.token = TOKEN
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        for p in reversed(cls.patches):
            p.stop()
        cls.tmp.cleanup()

    def get(self, path, cookie=True):
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_port, timeout=10)
        c.request("GET", path, headers={"Cookie": f"jh_token={TOKEN}"} if cookie else {})
        r = c.getresponse()
        r.read()
        c.close()
        return r

    def test_redirects_to_resumegen_with_the_person(self):
        r = self.get("/open/resumegen?profile=jordan")
        self.assertEqual(r.status, 303)
        self.assertEqual(r.getheader("Location"), "http://127.0.0.1:8770/?profile=jordan")

    def test_the_resumegen_token_never_reaches_the_browser(self):
        r = self.get("/open/resumegen?profile=alex")
        self.assertNotIn("the-secret-rg-token", r.getheader("Location"))
        self.assertNotIn("token", r.getheader("Location"))

    def test_it_needs_the_app_sign_in_like_every_page(self):
        self.assertNotEqual(self.get("/open/resumegen", cookie=False).status, 303)


class GoogleSummaryTest(unittest.TestCase):
    def summary(self, banner, overnight=None):
        with mock.patch("job_hunt_agent.google.runner.banner", lambda conn: banner), \
                mock.patch("job_hunt_agent.mail.nightly.overnight_line", lambda conn, pid: overnight), \
                mock.patch("job_hunt_agent.gsync.sheet_url", lambda conn, pid: "https://sheet"):
            return api.google_summary(None, "alex")

    @staticmethod
    def banner(**kw):
        sync = {"errors": {}, "linked": ["alex"], "healthy": True, "last_sync_at": None, **kw.pop("sync", {})}
        return {"lapsed": False, "warn": False, "days_left": 30, "sync": sync, "message": None, "last_sync_at": None, **kw}

    def test_all_well_has_no_alert_and_no_note(self):
        g = self.summary(self.banner())
        self.assertIsNone(g["alert"])
        self.assertIsNone(g["sync_note"])
        self.assertEqual(g["sheet_url"], "https://sheet")

    def test_a_lapsed_sign_in_is_an_alert_in_plain_words(self):
        g = self.summary(self.banner(lapsed=True, message="Google sign-in lapsed: run `job-hunt-agent google login`"))
        self.assertTrue(g["alert"].startswith("Google sign-in ran out"))
        self.assertNotIn("`", g["alert"])
        self.assertIn("lapsed", g["message"])                  # the old one-line message is kept for the mail panel

    def test_a_sign_in_ending_soon_is_an_alert(self):
        self.assertIn("ends in 3 days", self.summary(self.banner(warn=True, days_left=3))["alert"])

    def test_a_failed_or_late_sync_is_a_note_not_an_alert(self):
        failed = self.summary(self.banner(sync={"errors": {"alex": "quota"}}))
        self.assertIsNone(failed["alert"])
        self.assertEqual(failed["sync_note"], "Google Sheet sync failed: quota. It tries again by itself")
        late = self.summary(self.banner(sync={"healthy": False}))
        self.assertIn("has not synced for 15+ minutes", late["sync_note"])
        other = self.summary(self.banner(sync={"healthy": False, "linked": ["jordan"]}))
        self.assertIsNone(other["sync_note"])                    # only this person's Sheet

    def test_the_overnight_line_is_its_own_field(self):
        g = self.summary(self.banner(), overnight="Overnight email: 2 updates")
        self.assertEqual(g["overnight"], "Overnight email: 2 updates")


class SharedDesignTest(unittest.TestCase):
    """ds.css: the tokens and shared parts, the same bytes in both apps (resumegen/static/ds.css)."""

    def test_tokens_are_defined_once_for_light_and_dark(self):
        css = DS.read_text()
        for token in ("--paper", "--raised", "--ink", "--quiet", "--mark", "--bad", "--t-md", "--s4", "--r", "--fast"):
            with self.subTest(token=token):
                self.assertIn(f"{token}:", css)
        self.assertIn('[data-theme="dark"]', css)
        self.assertIn("prefers-color-scheme: dark", css)
        self.assertIn("prefers-reduced-motion", css)

    def test_resumegen_has_the_same_file(self):
        here = os.environ.get("RESUMEGEN_SRC") or str(ROOT.parent / "resumegen")
        other = Path(here) / "resumegen" / "static" / "ds.css"
        if not other.exists():
            self.skipTest(f"no resumegen checkout with ds.css at {here}")
        self.assertEqual(DS.read_bytes(), other.read_bytes(), "ds.css differs between job-hunt-agent and resumegen")


if __name__ == "__main__":
    unittest.main()
