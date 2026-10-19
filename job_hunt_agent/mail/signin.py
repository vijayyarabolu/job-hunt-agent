"""Each person's own job-application mailbox, read-only, through the per-person Google sign-in of GOAL11 §5
(google/accounts.py, kind GMAIL): the same consent link, /google/callback answer page, pasted answer from a phone and
Disconnect (revoked at Google). Scope gmail.readonly alone, its own token per person
(data/gmail_token_<profile>.json, 0600); the app's Google account (Sheets / Drive) is a different address and its
token is never touched.

The person types the address in Settings -> "Job-application email" and presses Connect: Google's sign-in for THAT
address. Not Gmail or Google Workspace -> it says so and points to IMAP (imap.py). The token is kept only if the
account that signed in is that mailbox (Gmail ignores dots and +tags); it records both (token "email", "address").
"""
import webbrowser

from .. import config
from ..google import GoogleError, accounts, auth as gauth
from . import SCOPES, address as addr

redirect_port = 8765                    # the server sets its real port at start (server.serve)
NOT_GOOGLE = ("{} is not a Gmail or Google Workspace address, so Google sign-in cannot read it: use IMAP below "
              "(an app password from your mail provider)")


def token_path(pid: str):
    return accounts.token_file(pid, accounts.GMAIL)


def _token(pid: str) -> dict | None:
    return gauth.load(path=token_path(pid)) if token_path(pid).exists() else None


def status(conn, pid: str) -> dict:
    token = _token(pid)
    lapsed = bool(token and (token.get("lapsed") or not token.get("refresh_token")))
    return {"connected": bool(token) and not lapsed, "lapsed": lapsed, "account": (token or {}).get("email")}


def address_of(conn, pid: str) -> str | None:
    """The job-application address the person typed (kept with the token once the sign-in matched it)."""
    return (_token(pid) or {}).get("address")


def _known(pid: str) -> None:
    if pid not in config.profile_ids():
        raise ValueError(f"unknown profile {pid!r}")


def google_address(email: str | None) -> str:
    """The typed address, checked for a Google sign-in (ValueError: not an address / not Google -> IMAP)."""
    email = addr.valid(email)
    if addr.provider(email) == "other":
        raise ValueError(NOT_GOOGLE.format(email))
    return email


def begin(conn, pid: str, email: str | None = None, port: int | None = None) -> str:
    """The Google sign-in link for this person's job-application address; its answer comes back to finish()."""
    _known(pid)
    return accounts.start(pid, port or redirect_port, accounts.GMAIL, google_address(email or address_of(conn, pid)))


def finish(conn, answer: str, pid: str | None = None) -> str:
    """The pasted address of the page Google opened (a phone cannot load 127.0.0.1); returns the profile."""
    pid = pid or next((p for p in config.profile_ids() if accounts.waiting_for(p, answer) == accounts.GMAIL), None)
    if pid is None:
        raise ValueError("this sign-in link is unknown or was already used: press Connect again")
    _known(pid)
    if accounts.waiting_for(pid, answer) != accounts.GMAIL:
        raise ValueError("this is not the answer of a job-application email sign-in: press Connect again")
    accounts.finish(pid, answer)
    return pid


def login(conn, pid: str, email: str, open_browser=webbrowser.open) -> dict:
    """CLI on this Mac: the browser consent with its own one-shot loopback page, then the same address check."""
    _known(pid)
    email = google_address(email)
    token = gauth.login(open_browser, login_hint=email, scopes=SCOPES, path=token_path(pid))
    from .gmail import GmailReader                            # late: gmail imports this module
    try:
        account = GmailReader(pid).account().lower()
    except GoogleError:
        token_path(pid).unlink(missing_ok=True)
        raise
    if not addr.same_mailbox(account, email):
        token_path(pid).unlink(missing_ok=True)
        raise ValueError(f"you signed in as {account}, not {email}: run it again and pick {email}")
    gauth.save({**token, "email": account, "address": email}, path=token_path(pid))
    return token


def disconnect(conn, pid: str) -> None:
    """Stop reading this person's Gmail: the token is revoked at Google and deleted here."""
    _known(pid)
    accounts.disconnect(pid, accounts.GMAIL)
