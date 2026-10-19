"""Google's redirect to http://127.0.0.1:<port>/ finishes the sign-in by itself on the Mac,
even though that browser has no app cookie for 127.0.0.1 (the server runs with the LAN token). The answer's `state`
is the secret: it must match a sign-in started in the last 15 minutes, and works once. Anything else on that path
still needs the LAN token. The phone's paste box keeps working."""
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from unittest import mock

from job_hunt_agent import google_page, server
from job_hunt_agent.google import accounts
from job_hunt_agent.mail import signin
from tests.test_google_accounts import JANE, OwnAccountCase
from tests.test_mail_gmail import GMAIL_ADDRESS, ServerCase, redirect_target

LAN = "lan-secret-token"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def fetch(url: str, cookie: str | None = None) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"Cookie": f"jh_token={cookie}"} if cookie else {})
    try:
        with urllib.request.build_opener(_NoRedirect()).open(req, timeout=10) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as err:
        with err:
            return err.code, err.read().decode()


class _LanServer:
    """The app server as `serve --lan` runs it: the LAN token required (a browser on 127.0.0.1 has no cookie)."""

    def start_lan(self):
        self.httpd.token = LAN

    def local(self, answer: str) -> str:
        return self.base + answer[answer.index("/?"):]


class MailboxCallbackTest(_LanServer, ServerCase):
    def setUp(self):
        super().setUp()
        self.start_lan()

    def connect(self) -> str:
        """Connect pressed in Settings (the person has the cookie); Google answers -> the callback address."""
        query = {"profile": "jane", "t": google_page.tag("connect-gmail", "jane"), "kind": "gmail",
                 "address": GMAIL_ADDRESS}
        req = urllib.request.Request(f"{self.base}/google/connect?{urllib.parse.urlencode(query)}",
                                     headers={"Cookie": f"jh_token={LAN}"})
        try:
            urllib.request.build_opener(_NoRedirect()).open(req, timeout=10)
        except urllib.error.HTTPError as err:
            err.close()
            return redirect_target(err.headers["Location"])
        raise AssertionError("connect did not redirect")

    def test_the_callback_finishes_without_the_app_cookie(self):
        code, page = fetch(self.local(self.connect()))
        self.assertEqual(code, 200, page)
        self.assertIn(f"Connected as <b>{GMAIL_ADDRESS}</b>. You can close this tab", page)
        self.assertTrue(signin.status(self.conn, "jane")["connected"])

    def test_the_same_answer_works_once(self):
        answer = self.local(self.connect())
        self.assertEqual(fetch(answer)[0], 200)
        self.assertEqual(fetch(answer)[0], 401)

    def test_a_forged_or_old_state_still_needs_the_lan_token(self):
        answer = self.local(self.connect())
        self.assertEqual(fetch(answer.replace("state=", "state=x"))[0], 401)
        with mock.patch.object(accounts.time, "time", return_value=time.time() + 16 * 60):
            self.assertEqual(fetch(answer)[0], 401)
        self.assertEqual(fetch(f"{self.base}/google/callback")[0], 401)
        state = urllib.parse.parse_qs(urllib.parse.urlsplit(answer).query)["state"][0]
        self.assertEqual(fetch(f"{self.base}/google/account?profile=jane&state={state}")[0], 401)
        self.assertEqual(fetch(f"{self.base}/api/jobs?profile=jane&state={state}")[0], 401)
        self.assertFalse(signin.status(self.conn, "jane")["connected"])

    def test_a_non_ascii_state_is_refused_without_breaking_the_server(self):
        self.connect()
        self.assertEqual(fetch(f"{self.base}/google/callback?code=x&state=%C3%A9")[0], 401)
        self.assertFalse(accounts.answers_pending("code=x&state=%C3%A9"))

    def test_the_phone_paste_still_works(self):
        answer = self.connect()                                  # a phone cannot load it: its address is pasted
        body = json.dumps({"profile": "jane", "view": "settings", "act": "finish", "value": answer}).encode()
        req = urllib.request.Request(f"{self.base}/api/mail/act", method="POST", data=body, headers={
            "Content-Type": "application/json", "X-Jobhunt": "1", "Cookie": f"jh_token={LAN}"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            self.assertIn("Connected (read-only)", json.loads(resp.read())["msg"])
        self.assertTrue(signin.status(self.conn, "jane")["connected"])


class OwnGoogleAccountCallbackTest(_LanServer, OwnAccountCase):
    """GOAL11 §5's own Google account (Jordan's Drive / Sheets) goes through the same callback."""

    def setUp(self):
        super().setUp()
        self.httpd = server.make_server(port=0)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"
        self.start_lan()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        super().tearDown()

    def test_the_callback_finishes_without_the_app_cookie(self):
        self.fake.next_email = JANE
        answer = redirect_target(accounts.start("jane", self.httpd.server_port))
        with mock.patch("job_hunt_agent.google_move.soon"):
            code, page = fetch(self.local(answer))
        self.assertEqual(code, 200, page)
        self.assertIn(f"Connected as {JANE}. You can close this tab", page)
        self.assertTrue(accounts.connected("jane"))

    def test_a_failed_sign_in_spends_its_state_too(self):
        self.fake.next_email = "someone@else.com"                    # the wrong account signs in
        answer = self.local(redirect_target(accounts.start("jane", self.httpd.server_port)))
        code, page = fetch(answer)
        self.assertEqual(code, 200)
        self.assertIn("signed in as someone@else.com", page)
        self.assertEqual(fetch(answer)[0], 401)                      # one use: the callback is closed again
        self.assertFalse(accounts.connected("jane"))
