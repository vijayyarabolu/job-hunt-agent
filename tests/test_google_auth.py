"""OAuth (loopback + PKCE), the 0600 token file, refresh / invalid_grant -> NeedsConsent, and the REST helper's
retries (429, 403 rate limit, 401 refresh, timeouts) against the fake Google."""
import os
import stat
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from unittest import mock

from job_hunt_agent import google
from job_hunt_agent.google import NeedsConsent, NotConfigured, api, auth
from tests.google_case import GoogleCase


def browser(url: str) -> None:
    """Play the user's browser in the background: the fake consent page redirects to the loopback server."""
    threading.Thread(target=lambda: urllib.request.urlopen(url, timeout=10).read(), daemon=True).start()


class LoginTest(GoogleCase):
    connect = False

    def test_loopback_login_with_pkce_saves_a_private_token(self):
        token = auth.login(open_browser=browser, timeout_s=10)
        self.assertTrue(token["refresh_token"])
        self.assertEqual(stat.S_IMODE(os.stat(auth.token_path()).st_mode), 0o600)
        self.assertTrue(auth.status()["connected"])
        query = next(q for m, p, q in self.fake.requests if p == "/o/oauth2/v2/auth")
        self.assertEqual(query["code_challenge_method"], "S256")
        self.assertEqual(set(query["scope"].split()), set(google.SCOPES))
        self.assertEqual((query["access_type"], query["prompt"]), ("offline", "consent"))

    def test_wrong_state_is_refused(self):
        with self.assertRaisesRegex(google.GoogleError, "state mismatch"):
            auth.login(open_browser=lambda url: browser(url.replace("state=", "state=x")), timeout_s=10)
        self.assertFalse(auth.token_path().exists())

    def test_missing_client_file_says_how_to_fix(self):
        auth.client_path().unlink()
        with self.assertRaisesRegex(NotConfigured, "Settings > Setup"):
            auth.login(open_browser=browser)
        self.assertFalse(auth.status()["configured"])


class TokenTest(GoogleCase):
    def test_refresh_when_expired_and_invalid_grant_needs_consent(self):
        auth.save({**auth.load(), "expires_at": time.time() - 5})
        self.assertIn(auth.access_token(), self.fake.access)              # refreshed
        auth.save({**auth.load(), "expires_at": time.time() - 5})
        self.fake.refresh_error = "invalid_grant"
        with self.assertRaisesRegex(NeedsConsent, "connect again"):
            auth.access_token()
        self.assertTrue(auth.load()["lapsed"])
        self.assertFalse(auth.status()["connected"])
        self.assertTrue(auth.status()["warn"])
        calls = len(self.fake.requests)
        with self.assertRaises(NeedsConsent):                             # no network call once lapsed
            auth.access_token()
        self.assertEqual(len(self.fake.requests), calls)

    def test_token_mode_is_rechecked_on_load(self):
        os.chmod(auth.token_path(), 0o644)
        auth.load()
        self.assertEqual(stat.S_IMODE(os.stat(auth.token_path()).st_mode), 0o600)

    def test_day_six_warning(self):
        start = datetime.now(timezone.utc) - timedelta(days=6, hours=1)
        auth.save({**auth.load(), "refresh_obtained_at": start.strftime("%Y-%m-%dT%H:%M:%SZ")})
        state = auth.status()
        self.assertTrue(state["connected"] and state["warn"])
        self.assertLessEqual(state["days_left"], 1)

    def test_base_override_must_be_loopback(self):
        with mock.patch.dict(os.environ, {"JOB_HUNT_AGENT_GOOGLE_BASE": "https://evil.example.com"}):
            with self.assertRaises(NotConfigured):
                google.base("drive")


class ApiTest(GoogleCase):
    def drive(self, path=""):
        return google.base("drive") + "/files" + path

    def test_429_and_rate_limit_403_are_retried(self):
        self.fake.fail("GET", r"/drive/v3/files$", 429, headers={"Retry-After": "1"})
        self.fake.fail("GET", r"/drive/v3/files$", 403, reason="userRateLimitExceeded")
        self.assertEqual(api.get(self.drive(), {"q": "trashed = false"}), {"files": []})
        self.assertEqual(self.fake.count("GET", r"/drive/v3/files$"), 3)

    def test_401_refreshes_once(self):
        self.fake.access.clear()                                          # access token revoked server-side
        self.assertEqual(api.get(self.drive())["files"], [])

    def test_other_errors_raise_with_status(self):
        with self.assertRaises(google.GoogleError) as caught:
            api.get(self.drive("/nope"))
        self.assertEqual(caught.exception.status, 404)

    def test_total_deadline(self):
        self.fake.fail("GET", r"/drive/v3/files$", 503, times=50)
        with self.assertRaises(google.GoogleError):
            api.call("GET", self.drive(), total_s=0.5)

    def test_multipart_body_round_trips(self):
        body, ctype = api.multipart({"name": "a.pdf"}, b"%PDF-bytes", "application/pdf")
        made = api.call("POST", google.base("upload") + "/files", params={"uploadType": "multipart"},
                        data=body, ctype=ctype).json()
        self.assertEqual(self.fake.files[made["id"]]["content"], b"%PDF-bytes")
