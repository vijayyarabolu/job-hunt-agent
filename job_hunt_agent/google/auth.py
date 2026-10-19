"""OAuth 2.0 for an installed app: authorization code + PKCE (S256), loopback redirect, stdlib only (decision 006).

Google does not allow an open-source app to ship a shared client secret, so each user creates their own OAuth client
in Google Cloud (Settings > Setup walks through it) and the app keeps it in <home>/config/google_client.json (0600).
Tokens: <home>/data/google_token.json, always written 0600 (the mode is re-checked on every load). A consent screen
"In production" keeps the refresh token alive; one still in Testing (settings.yaml `google_consent: testing`) gets
Google's 7-day clock and a day-6 warning. A refresh answered with `invalid_grant` (revoked, or a Testing token's
7 days) marks the token lapsed and raises NeedsConsent, which every caller turns into a "connect again" message.

Sign-in normally starts from the web UI (google/accounts.py); `job-hunt-agent google login` is the terminal way.
"""
import base64
import contextvars
import hashlib
import json
import logging
import os
import re
import secrets
import time
import urllib.error
import urllib.request
import webbrowser
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

from .. import config
from . import REAL, SCOPES, GoogleError, NeedsConsent, NotConfigured, base

REAL_AUTH, REAL_TOKEN = REAL["auth"], REAL["token"]

log = logging.getLogger(__name__)
TESTING_TOKEN_DAYS = 7
WARN_DAYS_LEFT = 1              # the day-6 warning (decision 006 condition 9)
REFRESH_MARGIN_S = 60
LOGIN_TIMEOUT_S = 300
HTTP_TIMEOUT_S = 20
PRIVATE = 0o600
_DONE_PAGE = b"<html><body><h3>Job Hunt Agent is connected to Google. You can close this tab.</h3></body></html>"
# a profile with its own Google account has its own token; None = the app account's
_ACCOUNT: contextvars.ContextVar[str | None] = contextvars.ContextVar("google_account", default=None)
ACCOUNT_ID = re.compile(r"^[a-z0-9_-]{1,40}$")
FALLBACK_STATUSES = (401, 403, 404)       # the person's token was refused: the owner's token does it (as before)


def client_path():
    return config.CONFIG / "google_client.json"


def _account(account: str | None) -> str | None:
    account = _ACCOUNT.get() if account is None else (account or None)
    if account is not None and not ACCOUNT_ID.match(account):
        raise ValueError(f"not a profile id: {account!r}")
    return account


def token_path(account: str | None = None):
    """The owner's token (data/google_token.json), or a person's own (data/google_token.<profile>.json)."""
    account = _account(account)
    return config.DATA / (f"google_token.{account}.json" if account else "google_token.json")


def current() -> str | None:
    """Whose token Google calls use right now (None: the owner's)."""
    return _ACCOUNT.get()


def client() -> dict:
    """client_id + client_secret of the Desktop OAuth client, or NotConfigured with the fix."""
    path = client_path()
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError as err:
        raise NotConfigured("no Google OAuth client saved yet: Settings > Setup > Google") from err
    except json.JSONDecodeError as err:
        raise NotConfigured(f"{path} is not valid JSON: {err}") from err
    entry = data.get("installed") or data.get("web") or {}
    if not entry.get("client_id") or not entry.get("client_secret"):
        raise NotConfigured(f"{path} is not an OAuth client file (expected an 'installed' section)")
    return entry


CLIENT_ID = re.compile(r"^[0-9]+-[0-9a-z]+\.apps\.googleusercontent\.com$")


def save_client(client_id: str, client_secret: str) -> dict:
    """Settings > Setup: the user's own Desktop OAuth client, saved 0600. Either a pasted ID + secret, or the
    downloaded JSON (parse_client_json)."""
    client_id, client_secret = (client_id or "").strip(), (client_secret or "").strip()
    if not CLIENT_ID.match(client_id):
        raise ValueError("that does not look like a client ID (it ends in .apps.googleusercontent.com)")
    if not re.fullmatch(r"[A-Za-z0-9_-]{10,100}", client_secret):
        raise ValueError("that does not look like a client secret")
    save({"installed": {"client_id": client_id, "client_secret": client_secret,
                        "auth_uri": REAL_AUTH, "token_uri": REAL_TOKEN, "redirect_uris": ["http://127.0.0.1"]}},
         path=client_path())
    return {"client_id": client_id}


def parse_client_json(text: str) -> tuple[str, str]:
    """(client_id, client_secret) out of the JSON file the Cloud console downloads."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as err:
        raise ValueError("that is not the JSON file Google gave you") from err
    entry = (data.get("installed") or data.get("web") or {}) if isinstance(data, dict) else {}
    return entry.get("client_id") or "", entry.get("client_secret") or ""


def forget_client() -> None:
    client_path().unlink(missing_ok=True)


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def auth_url(client_id: str, redirect_uri: str, challenge: str, state: str, login_hint: str | None = None,
             scopes: tuple[str, ...] = SCOPES) -> str:
    query = {"client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code", "scope": " ".join(scopes),
             "code_challenge": challenge, "code_challenge_method": "S256", "state": state,
             "access_type": "offline", "prompt": "consent"}
    if login_hint:
        query["login_hint"] = login_hint
    return f"{base('auth')}?{urlencode(query)}"


def _post_form(fields: dict) -> dict:
    """POST to the token endpoint. Returns the JSON body; an OAuth error body comes back as {'error': ...}."""
    req = urllib.request.Request(base("token"), data=urlencode(fields).encode(), method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as err:
        try:
            body = json.loads(err.read() or b"{}")
        except json.JSONDecodeError:
            body = {}
        err.close()
        return {"error": body.get("error") or f"http_{err.code}", "error_description": body.get("error_description")}
    except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as err:
        raise GoogleError(f"token endpoint unreachable: {err}") from err


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def save(token: dict, account: str | None = None, path: Path | None = None) -> None:
    """path: another sign-in's own token file (a person's Gmail token), else the account's."""
    path = path or token_path(account)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, PRIVATE)
    with os.fdopen(fd, "w") as fh:
        json.dump(token, fh)
    os.chmod(tmp, PRIVATE)
    tmp.replace(path)


def load(account: str | None = None, path: Path | None = None) -> dict | None:
    path = path or token_path(account)
    if not path.exists():
        return None
    if path.stat().st_mode & 0o077:
        log.warning("%s was readable by others; set back to 0600", path)
        path.chmod(PRIVATE)
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        log.error("%s is corrupt; connect Google again in Settings", path)
        return None


def consent_mode() -> str:
    """'production' (default) or 'testing' (settings.yaml google_consent), recorded with the token."""
    mode = str(config.settings().get("google_consent") or "production").strip().lower()
    return mode if mode in ("production", "testing") else "production"


def _granted(answer: dict, scopes: tuple[str, ...] = SCOPES) -> dict:
    if "error" in answer:
        raise GoogleError(f"Google refused the sign-in: {answer['error']} {answer.get('error_description') or ''}")
    missing = [s for s in scopes if s not in (answer.get("scope") or "").split()]
    if missing:
        raise GoogleError(f"consent did not grant {missing}: run login again and tick every box")
    return {"access_token": answer["access_token"], "refresh_token": answer.get("refresh_token"),
            "expires_at": time.time() + int(answer.get("expires_in", 3600)), "scope": answer["scope"],
            "refresh_obtained_at": _now_iso(), "lapsed": False, "mode": consent_mode()}


class _Redirect(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 (http.server API)
        parts = urlsplit(self.path)
        if parts.path == "/":
            self.server.query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(_DONE_PAGE)

    def log_message(self, fmt, *args):
        pass


def _wait_for_redirect(server: HTTPServer, timeout_s: float) -> dict:
    deadline = time.monotonic() + timeout_s
    while server.query is None:
        if time.monotonic() > deadline:
            raise GoogleError(f"no answer from the browser within {int(timeout_s)} s")
        server.handle_request()
    return server.query


def login(open_browser=webbrowser.open, timeout_s: float = LOGIN_TIMEOUT_S, login_hint: str | None = None,
          scopes: tuple[str, ...] = SCOPES, path: Path | None = None) -> dict:
    """Browser consent on this Mac; saves and returns the token. Never runs headless (nothing calls it but the CLI)."""
    entry = client()
    verifier, challenge = pkce()
    state = secrets.token_urlsafe(16)
    with HTTPServer(("127.0.0.1", 0), _Redirect) as server:
        redirect_uri = f"http://127.0.0.1:{server.server_port}/"
        server.query, server.timeout = None, 1.0
        open_browser(auth_url(entry["client_id"], redirect_uri, challenge, state, login_hint, scopes))
        query = _wait_for_redirect(server, timeout_s)
    return exchange(query, state, verifier, redirect_uri, scopes, path)


def exchange(query: dict, state: str, verifier: str, redirect_uri: str, scopes: tuple[str, ...] = SCOPES,
             path: Path | None = None) -> dict:
    """The browser's answer (code + state) traded for a token, which is saved and returned."""
    if query.get("state") != state:
        raise GoogleError("the browser answer did not match this login (state mismatch); try again")
    if "code" not in query:
        raise GoogleError(f"consent was not given: {query.get('error', 'no code')}")
    entry = client()
    token = _granted(_post_form({"code": query["code"], "client_id": entry["client_id"],
                                 "client_secret": entry["client_secret"], "redirect_uri": redirect_uri,
                                 "grant_type": "authorization_code", "code_verifier": verifier}), scopes)
    save(token, path=path)
    return token


def access_token(force: bool = False, account: str | None = None, path: Path | None = None) -> str:
    """A valid access token, refreshed when it expires within a minute (or force=True after a 401)."""
    account = None if path else _account(account)
    token = load(account, path)
    if token is None:
        raise NotConfigured("not connected to Google yet: Settings > Setup > Connect Google")
    if token.get("lapsed") or not token.get("refresh_token"):
        raise NeedsConsent()
    if not force and token.get("expires_at", 0) - time.time() > REFRESH_MARGIN_S:
        return token["access_token"]
    entry = client()
    answer = _post_form({"client_id": entry["client_id"], "client_secret": entry["client_secret"],
                         "refresh_token": token["refresh_token"], "grant_type": "refresh_token"})
    if answer.get("error") == "invalid_grant":
        save({**token, "lapsed": True}, account, path)
        raise NeedsConsent()
    if "error" in answer:
        raise GoogleError(f"token refresh failed: {answer['error']}")
    save({**token, "access_token": answer["access_token"],
          "expires_at": time.time() + int(answer.get("expires_in", 3600)),
          "refresh_token": answer.get("refresh_token") or token["refresh_token"]}, account, path)
    return answer["access_token"]


def token_for(path: Path):
    """access_token bound to another token file (a person's Gmail token), for api.call(token=...)."""
    return lambda force=False: access_token(force, path=path)


def days_left(token: dict, now: datetime | None = None) -> float | None:
    """Days until a Testing-mode token lapses; None for a production client (no 7-day expiry)."""
    obtained = token.get("refresh_obtained_at")
    if not obtained or token.get("mode", "testing") != "testing":
        return None
    start = datetime.strptime(obtained, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    age = (now or datetime.now(timezone.utc)) - start
    return round(TESTING_TOKEN_DAYS - age.total_seconds() / 86400, 1)


def status(now: datetime | None = None) -> dict:
    """For `google status`, the UI banner and doctor: configured / connected / lapsed / days left / warn."""
    configured = client_path().exists()
    token = load() if configured else None
    lapsed = bool(token and (token.get("lapsed") or not token.get("refresh_token")))
    left = days_left(token, now) if token else None
    connected = bool(token) and not lapsed
    return {"configured": configured, "connected": connected, "lapsed": lapsed, "days_left": left,
            "mode": (token or {}).get("mode", "testing") if token else consent_mode(),
            "warn": lapsed or (connected and left is not None and left <= WARN_DAYS_LEFT)}


def is_ready() -> bool:
    return status()["connected"]
