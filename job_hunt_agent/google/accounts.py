"""Google sign-ins from the web UI: OAuth 2.0 authorization code with PKCE, redirected back to this app's own port.

The app's Google account (Settings > Setup > Connect Google, pid MAIN) holds the Sheets and Drive token every profile
uses by default (data/google_token.json). A profile can also connect its own Google account (its address in
profile.yaml `drive_share_with`); its token is data/google_token.<profile>.json and its files then live in its own
Drive. Each person can also connect a job-application mailbox (kind GMAIL, scope gmail.readonly alone, token
data/gmail_token_<profile>.json): a token belongs to one account, so Disconnecting the mailbox never touches Drive.

Google sends the browser to http://127.0.0.1:<port>/?code=...&state=... (the loopback form Google documents for
Desktop clients). When the browser runs on another device that page cannot load, so the person pastes its address into
the box on the Google account page instead; the code is useless without the verifier kept here. All tokens are 0600.

as_profile(pid): Google calls inside use the person's token when they are connected, else the app account's.
with_fallback(pid, fn): the person's token first; refused (401/403/404, or lapsed), the app account's.
"""
import json
import logging
import os
import secrets
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from urllib.parse import parse_qs, urlencode, urlsplit

from .. import config
from ..mail import GMAIL_SCOPE
from ..mail.address import same_mailbox
from . import SCOPES, GoogleError, NeedsConsent, auth, base

log = logging.getLogger(__name__)
PENDING_S = 15 * 60                        # a consent link is good for 15 minutes
CALLBACK_PATH = "/"                        # Google's documented loopback form: http://127.0.0.1:<port>/
LEGACY_CALLBACK = "/google/callback"
GOOGLE, GMAIL = "google", "gmail"          # the person's Google account / job-application mailbox
MAIN = "_app"                              # the app's own Google account (Settings > Setup), not a profile


def expected_email(pid: str) -> str | None:
    """The person's own Google address (profile.yaml drive_share_with), or None for the owner."""
    if pid == MAIN:
        return None
    try:
        email = config.profile(pid).get("drive_share_with")
    except (config.ConfigError, KeyError, OSError):
        return None
    return str(email).strip().lower() if email else None


def connected(pid: str | None) -> bool:
    if not pid or not expected_email(pid):
        return False
    token = auth.load(pid)
    return bool(token and token.get("refresh_token") and not token.get("lapsed"))


@contextmanager
def as_profile(pid: str | None):
    """Google calls inside use the person's own token when they connected one, else the owner's."""
    reset = auth._ACCOUNT.set(pid if connected(pid) else None)
    try:
        yield auth._ACCOUNT.get()
    finally:
        auth._ACCOUNT.reset(reset)


@contextmanager
def as_owner():
    reset = auth._ACCOUNT.set(None)
    try:
        yield
    finally:
        auth._ACCOUNT.reset(reset)


def refused(err: GoogleError) -> bool:
    """Google said no to this token: 401/403/404, or a Sheets write whose Drive chip the token cannot see (400 "Error
    fetching chip data: Permission denied", GOAL18 §1: a file in the other person's Drive)."""
    return err.status in auth.FALLBACK_STATUSES or (err.status == 400 and "Permission denied" in str(err))


def with_fallback(pid: str | None, fn, *args, **kwargs):
    """fn under the person's token; if Google refuses it (or it lapsed), again under the owner's (as before)."""
    with as_profile(pid) as account:
        if account is None:
            return fn(*args, **kwargs)
        try:
            return fn(*args, **kwargs)
        except NeedsConsent:
            log.warning("%s's Google sign-in lapsed: using the owner's token", pid)
        except GoogleError as err:
            if not refused(err):
                raise
            log.warning("%s's token was refused (%s): using the owner's token", pid, err)
    with as_owner():
        return fn(*args, **kwargs)


# ---- the person's consent (from their own profile, over Tailscale) ----

def token_file(pid: str, kind: str = GOOGLE):
    auth._account(pid)                                  # a profile id, never a path
    if pid == MAIN:
        return auth.token_path(None) if kind == GOOGLE else config.DATA / "gmail_token__app.json"
    return config.DATA / f"gmail_token_{pid}.json" if kind == GMAIL else auth.token_path(pid)


def _pending_path(pid: str, kind: str = GOOGLE):
    auth._account(pid)                                  # a profile id, never a path
    return config.DATA / f"{kind if kind == GMAIL else GOOGLE}_pending.{pid}.json"


def _write_private(path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, auth.PRIVATE)
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh)
    os.chmod(path, auth.PRIVATE)


def start(pid: str, port: int, kind: str = GOOGLE, email: str | None = None) -> str:
    """Google's consent URL for the person (their address pre-filled); the verifier stays here. kind GMAIL: read-only
    Gmail for `email`, the job-application address (checked by the caller, mail/signin.py)."""
    if kind == GMAIL:
        if not email:
            raise ValueError("type the job-application email address first")
        scopes = (GMAIL_SCOPE,)
    else:
        email, scopes = expected_email(pid), SCOPES
    if not email and pid != MAIN:
        raise ValueError(f"profile {pid} has no Google address of its own (drive_share_with in profile.yaml)")
    entry = auth.client()
    verifier, challenge = auth.pkce()
    state = secrets.token_urlsafe(16)
    redirect_uri = f"http://127.0.0.1:{int(port)}{CALLBACK_PATH}"
    _write_private(_pending_path(pid, kind), {"state": state, "verifier": verifier, "redirect_uri": redirect_uri,
                                              "at": time.time(), "kind": kind, "email": email})
    return auth.auth_url(entry["client_id"], redirect_uri, challenge, state, email, scopes)


def _answer(text: str) -> dict:
    """The code and state out of the page address Google sent the browser to (or its query string)."""
    text = (text or "").strip()
    query = urlsplit(text).query if "://" in text else text.lstrip("?")
    found = {k: v[0] for k, v in parse_qs(query).items()}
    if "error" in found:
        raise ValueError(f"Google says: {found['error']} (consent was not given)")
    if not found.get("code") or not found.get("state"):
        raise ValueError("paste the whole address of the page Google opened (it has code=… and state=… in it)")
    return found


def _waiting(pid: str) -> dict:
    """{kind: pending sign-in} for this profile."""
    found = {}
    for kind in (GOOGLE, GMAIL):
        try:
            found[kind] = json.loads(_pending_path(pid, kind).read_text())
        except (OSError, ValueError):
            continue
    return found


def waiting_for(pid: str, answer: str) -> str | None:
    """Which sign-in of this profile (GOOGLE / GMAIL) is waiting for this answer (its state matches), or None."""
    try:
        state = _answer(answer)["state"]
    except ValueError:
        return None
    kind = next((k for k, pending in _waiting(pid).items() if pending.get("state") == state), None)
    return kind if kind == GMAIL or (kind and (pid == MAIN or expected_email(pid))) else None


def answers_pending(query: str) -> bool:
    """this callback query carries the state of a sign-in someone started in the last 15 minutes and that
    has not finished yet. That state is the secret (random, one use), so Google's redirect to 127.0.0.1 may finish the
    sign-in on the Mac without the app's LAN cookie."""
    try:
        state = _answer(query)["state"]
    except ValueError:
        return False
    if not state.isascii():                 # compare_digest takes ASCII text only; real states are URL-safe base64
        return False
    return any(secrets.compare_digest(str(pending.get("state") or ""), state)
               and time.time() - float(pending.get("at") or 0) <= PENDING_S
               for pid in [MAIN, *config.profile_ids()] for pending in _waiting(pid).values())


def _email_of(access_token: str, kind: str = GOOGLE) -> str:
    """Who signed in: Drive's about (GOAL11's scopes) or Gmail's profile (gmail.readonly alone)."""
    url = (f"{base('gmail')}/users/me/profile" if kind == GMAIL
           else f"{base('drive')}/about?{urlencode({'fields': 'user(emailAddress)'})}")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {access_token}"})
    try:
        with urllib.request.urlopen(req, timeout=auth.HTTP_TIMEOUT_S) as resp:
            body = json.loads(resp.read())
            return str(body["emailAddress"] if kind == GMAIL else body["user"]["emailAddress"]).lower()
    except urllib.error.HTTPError as err:                   # say Google's reason ("Gmail API has not been used ...")
        raise GoogleError(f"could not read which Google account signed in: {err.code} {_reason(err)}",
                          err.code) from err
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as err:
        raise GoogleError(f"could not read which Google account signed in: {err}") from err


def _reason(err: urllib.error.HTTPError) -> str:
    try:
        return str(json.loads(err.read() or b"{}").get("error", {}).get("message") or err.reason)[:300]
    except (ValueError, AttributeError, OSError):
        return str(err.reason)
    finally:
        err.close()


def finish(pid: str, pasted: str) -> dict:
    """Exchange the code for the person's token; refused unless it is the address asked for (their own Google
    address, or for GMAIL the job-application address typed in Settings). waiting_for() says which."""
    waiting = _waiting(pid)
    if not waiting:
        raise ValueError("no sign-in is waiting: press Connect again")
    answer = _answer(pasted)
    kind = next((k for k, pending in waiting.items() if pending.get("state") == answer["state"]), None)
    if kind is None:
        raise ValueError("this address belongs to another sign-in: press Connect again and use the new link")
    pending, path = waiting[kind], _pending_path(pid, kind)
    if time.time() - float(pending.get("at") or 0) > PENDING_S:
        raise ValueError("the sign-in link is older than 15 minutes: press Connect again")
    path.unlink(missing_ok=True)            # one use, success or not (the state opens the callback)
    entry = auth.client()
    token = auth._granted(auth._post_form({"code": answer["code"], "client_id": entry["client_id"],
                                           "client_secret": entry["client_secret"],
                                           "redirect_uri": pending["redirect_uri"],
                                           "grant_type": "authorization_code", "code_verifier": pending["verifier"]}),
                          (GMAIL_SCOPE,) if kind == GMAIL else SCOPES)
    email = _email_of(token["access_token"], kind)
    if kind == GMAIL:
        wanted = pending["email"]
        if not same_mailbox(email, wanted):
            raise ValueError(f"you signed in as {email}, not {wanted}: Connect again and pick {wanted} "
                             f"(or type {email} as the job-application email)")
        auth.save({**token, "email": email, "address": wanted}, path=token_file(pid, GMAIL))
    elif pid == MAIN:
        auth.save({**token, "email": email})
    else:
        wanted = expected_email(pid)
        if email != wanted:
            raise ValueError(f"signed in as {email}, but this profile's Google account is {wanted}: "
                             f"sign in with that one")
        auth.save({**token, "email": email}, pid)
    return {"email": email}


def disconnect(pid: str, kind: str = GOOGLE) -> bool:
    """Revoke the person's token at Google and forget it here (the owner's token takes over, as before; GMAIL:
    nothing is read any more)."""
    token = auth.load(path=token_file(pid, kind))
    if token is None:
        return False
    if token.get("refresh_token"):
        try:
            urllib.request.urlopen(urllib.request.Request(
                base("revoke"), data=urlencode({"token": token["refresh_token"]}).encode(), method="POST",
                headers={"Content-Type": "application/x-www-form-urlencoded"}), timeout=auth.HTTP_TIMEOUT_S).close()
        except (urllib.error.URLError, TimeoutError) as err:          # forgotten here either way
            log.warning("revoking %s's Google token failed: %s", pid, err)
    token_file(pid, kind).unlink(missing_ok=True)
    return True


def main_status() -> dict:
    """The app's own Google account: configured (a client is saved), connected, who, lapsed."""
    configured = auth.client_path().exists()
    token = auth.load() if configured else None
    lapsed = bool(token and (token.get("lapsed") or not token.get("refresh_token")))
    return {"configured": configured, "connected": bool(token) and not lapsed, "lapsed": lapsed,
            "email": (token or {}).get("email")}


def status(pid: str) -> dict:
    token = auth.load(pid) if expected_email(pid) else None
    return {"own_account": expected_email(pid), "connected": connected(pid),
            "email": (token or {}).get("email"), "lapsed": bool(token and token.get("lapsed"))}
