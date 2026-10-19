"""The Google account pages (server-rendered, no JavaScript). Every action link carries an HMAC of the action and the
profile, so a link from another site cannot connect or disconnect anyone.

/google/account?profile=_app       the app's own Google account (Settings > Setup): Connect, status, Disconnect
/google/account?profile=p          a profile's own Google account (optional; its address is drive_share_with)
/google/connect?profile=p&t=...    -> Google's consent screen (PKCE; the person's address pre-filled when known)
/google/finish?profile=p&t=...&url=...  the pasted address, when the browser is on another device
/?code=...&state=...               Google's redirect when the browser is on this computer
/google/disconnect?profile=p&t=...  revokes the token at Google and deletes it here
After a profile connects its own account, its existing files move to its own Drive in the background (google_move.py).
/google/connect?kind=gmail&address=... starts the same sign-in for the person's job-application mailbox.
"""
import hashlib
import hmac
import secrets
from html import escape
from urllib.parse import urlencode

from . import config, google_move
from .google import GoogleError, accounts

_SECRET = secrets.token_bytes(32)          # per server process: a page from before a restart asks for a reload


def _tag(action: str, pid: str) -> str:
    return hmac.new(_SECRET, f"{action}:{pid}".encode(), hashlib.sha256).hexdigest()[:32]


def _check(action: str, pid: str, tag: str | None) -> None:
    if not tag or not hmac.compare_digest(tag, _tag(action, pid)):
        raise PermissionError("this link is stale: reload the Google account page and try again")


def tag(action: str, pid: str) -> str:
    """The HMAC a form outside this page carries (GOAL12's Connect in Settings: action "connect-gmail")."""
    return _tag(action, pid)


def _href(action: str, pid: str) -> str:
    return f"/google/{action}?{urlencode({'profile': pid, 't': _tag(action, pid)})}"


def _page(body: str) -> str:
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" '
            'content="width=device-width, initial-scale=1"><title>Google account</title>'
            f'<link rel="stylesheet" href="/jdpage.css"></head><body><article><h1>Google account</h1>{body}'
            '<p><a href="/#settings">Back to Job Hunt Agent</a></p></article></body></html>')


def _moves(pid: str) -> str:
    done = google_move.last(pid)
    if not done:
        return ""
    return f"<p>Moving your files to your Drive: {escape(done.get('state', ''))}. {escape(done.get('summary', ''))}</p>"


def _paste_form(pid: str) -> str:
    return ("<p>If this browser is on another device, Google then opens a page that does not load (its address starts "
            "with http://127.0.0.1). Copy that whole address from the address bar and paste it here:</p>"
            f'<form method="get" action="/google/finish"><input type="hidden" name="profile" value="{escape(pid)}">'
            f'<input type="hidden" name="t" value="{_tag("finish", pid)}">'
            '<p><input name="url" required placeholder="http://127.0.0.1:…"></p>'
            '<p><button type="submit">Finish connecting</button></p></form>')


def render_main(note: str = "") -> str:
    """The app's own Google account (Sheets and Drive for every profile that has not connected its own)."""
    info = accounts.main_status()
    said = f"<p><b>{escape(note)}</b></p>" if note else ""
    if not info["configured"]:
        return _page(f"{said}<p>Save your Google OAuth client in Settings &gt; Setup first (the numbered steps there "
                     "walk you through creating it).</p>")
    if info["connected"]:
        return _page(f"{said}<p>Connected as <b>{escape(info['email'] or 'your Google account')}</b>. Sheets sync and "
                     f"resume uploads use this account.</p><p><a href=\"{_href('disconnect', accounts.MAIN)}\">"
                     "Disconnect</a> (revokes the token at Google and deletes it here)</p>")
    lapsed = "<p>The Google sign-in lapsed or was revoked: connect again.</p>" if info["lapsed"] else ""
    return _page(f"{said}{lapsed}<p><a href=\"{_href('connect', accounts.MAIN)}\">Connect Google</a>: Google shows "
                 "its consent screen for your own OAuth client. Allow both boxes (Sheets, and Drive files this app "
                 "creates).</p>" + _paste_form(accounts.MAIN))


def render(pid: str, note: str = "") -> str:
    """The Google account section for one profile."""
    if pid == accounts.MAIN:
        return render_main(note)
    if pid not in config.profile_ids():
        raise KeyError(pid)
    name = config.profile(pid).get("name") or pid
    info = accounts.status(pid)
    said = f"<p><b>{escape(note)}</b></p>" if note else ""
    if not info["own_account"]:
        return _page(f"{said}<p>{escape(name)} uses the app's Google account (Settings &gt; Setup). To give this "
                     "profile its own Drive and Sheet, put its Google address in profile.yaml as "
                     "<code>drive_share_with</code>.</p>")
    if info["connected"]:
        return _page(f"{said}<p>Connected as <b>{escape(info['email'] or '')}</b>. Your resumes, Google Docs, JD Docs "
                     f"and tracker sheet use your own Google account.</p>{_moves(pid)}"
                     f"<p><a href=\"{_href('disconnect', pid)}\">Disconnect</a></p>")
    lapsed = "<p>Your Google sign-in lapsed: connect again.</p>" if info["lapsed"] else ""
    return _page(
        f"{said}{lapsed}<p>Until you connect, your files are made with the app's Google account and shared with "
        f"<b>{escape(info['own_account'])}</b>.</p>"
        f"<p><a href=\"{_href('connect', pid)}\">Connect {escape(info['own_account'])}</a> and allow access.</p>"
        + _paste_form(pid))


def connect_url(pid: str, tag: str | None, port: int, kind: str | None = None, address: str | None = None) -> str:
    if kind == accounts.GMAIL:                                  # the job-application mailbox, read-only
        _check("connect-gmail", pid, tag)
        from .mail import signin
        return accounts.start(pid, port, accounts.GMAIL, signin.google_address(address))
    _check("connect", pid, tag)
    return accounts.start(pid, port)


def finish(pid: str, tag: str | None, pasted: str) -> str:
    _check("finish", pid, tag)
    return _finish(pid, pasted)


def callback(query: str) -> str:
    """Google's redirect when the browser is on this Mac."""
    pid = next((p for p in [accounts.MAIN, *config.profile_ids()] if accounts.waiting_for(p, query)), None)
    if pid is None:
        raise ValueError("no sign-in is waiting for this answer: open Settings -> Google account and connect again")
    return _finish(pid, f"?{query}")


def _mailbox(pid: str, pasted: str) -> str:
    """the job-application mailbox's answer (on the Mac; a phone pastes it into Settings instead)."""
    try:
        email = accounts.finish(pid, pasted)["email"]
    except (ValueError, GoogleError) as err:
        return _page(f"<p><b>Job-application email not connected:</b> {escape(str(err))}</p>")
    return _page(f"<p>Connected as <b>{escape(email)}</b>. You can close this tab.</p><p>Job-application email "
                 f"connected: the app now reads it for {escape(config.profile(pid).get('name') or pid)}, read-only.</p>")


def _finish(pid: str, pasted: str) -> str:
    if accounts.waiting_for(pid, pasted) == accounts.GMAIL:
        return _mailbox(pid, pasted)
    try:
        email = accounts.finish(pid, pasted)["email"]
    except (ValueError, GoogleError) as err:
        return render(pid, f"Not connected: {err}")
    if pid == accounts.MAIN:
        return render_main(f"Connected as {email}. You can close this tab and go back to Settings.")
    google_move.soon(pid)
    return render(pid, f"Connected as {email}. You can close this tab. Your existing files are moving to your Drive "
                       "now.")


def disconnect(pid: str, tag: str | None) -> str:
    _check("disconnect", pid, tag)
    accounts.disconnect(pid)
    if pid == accounts.MAIN:
        return render_main("Disconnected: the token was revoked at Google and deleted here.")
    return render(pid, "Disconnected: the app's Google account is used again.")
