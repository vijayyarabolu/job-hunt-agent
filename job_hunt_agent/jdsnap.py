"""Keep the JD as text, not just a link (decision 012).

Every application gets a jd_snapshots row: the posting URL plus the full JD text, so the JD survives after the
company removes the posting. Where the text comes from, in order:
  1. feed  - the posting the app fetched (jobs.description), on save / Mark applied;
  2. sheet - the JD cell already holds the JD text (most imported rows): that text, as typed;
  3. fetch - the posting page itself, read once (backfill, or a job added by hand): the ATS JSON (jobfetch.py, robots
             gate and pacing as always) or a career page's schema.org JobPosting; 404/410/closed -> "posting gone".

The Sheet's JD cell (user decision 2026-10-06 evening, both trackers; replaces the earlier "URL + JD" text): the role
title linked to the posting, one clipped line, the JD as its hover note; the full text goes to applications.jd_text and
the hidden "JD Text" column (gsync_jd.py). Written for rows the app made whose cell is empty or only the link, and for
any cell still in the "URL + JD" style; the person's own older rows are never rewritten.
"""
import json
import logging
import re

from . import db, history, jobfetch, tracker
from .gsync_values import urls
from .net import HttpError, get_text
from .text import html_to_text, strip_boilerplate

log = logging.getLogger(__name__)
STATES = ("saved", "gone", "no_url", "unreadable")
MIN_TEXT = 200                    # a JD cell with less text than this is not a JD (a note, a title)
MIN_PAGE_TEXT = 400
URL_PLUS_JD = re.compile(r"^(https?://\S+)\n\n(.+)$", re.S)    # the cell format used before 2026-10-06 evening
NO_TITLE = "JD ↗"
APP_MADE = ("feed", "manual")
_LD_JSON = re.compile(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', re.S | re.I)


def link_only(cell: str | None) -> str | None:
    """The URL when the JD cell holds nothing but one link; None otherwise."""
    text = (cell or "").strip()
    found = urls(text)
    return found[0][2] if len(found) == 1 and text == found[0][2] else None


def posting_url(app: dict) -> str | None:
    """The posting's URL: the row's job link, else a link-only JD cell, else the first link in the JD cell."""
    if app.get("job_url"):
        return app["job_url"]
    cell = app.get("jd") or ""
    only = link_only(cell)
    if only:
        return only
    found = urls(cell)
    return found[0][2] if found else None


def _cell_text(app: dict, url: str | None) -> str | None:
    """The JD text the row already holds (its JD Text, else the cell with a leading URL line dropped), when it is
    real JD text."""
    if len((app.get("jd_text") or "").strip()) >= MIN_TEXT:
        return app["jd_text"].strip()
    cell = (app.get("jd") or "").strip()
    if link_only(cell) or len(cell) < MIN_TEXT:
        return None
    if url and cell.startswith(url):
        cell = cell[len(url):].strip()
    return cell


def _feed_text(conn, app: dict) -> tuple[str | None, bool]:
    """(the fetched posting's cleaned JD text, posting closed) for a row from the feed."""
    if not app.get("job_uid"):
        return None, False
    job = conn.execute("SELECT description, closed_at FROM jobs WHERE uid = ?", (app["job_uid"],)).fetchone()
    if job is None:
        return None, False
    text = strip_boilerplate(db.unpack(job["description"]) or "")
    return (text or None), bool(job["closed_at"])


def _ld_items(data) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("@graph", [data])
    return []


def _json_ld(page: str) -> str | None:
    """The description of the page's schema.org JobPosting, as text."""
    for block in _LD_JSON.findall(page):
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        for item in _ld_items(data):
            if isinstance(item, dict) and item.get("@type") == "JobPosting" and item.get("description"):
                return html_to_text(item["description"])
    return None


def fetch_text(url: str, resolve=None, raw: bool = False) -> tuple[str, str | None, str]:
    """(state, text, detail) for one posting URL: the ATS JSON, else the page's schema.org JobPosting, else the page
    text when it is long enough. One polite request through net.py (robots gate, pacing, retries). raw: the text as
    posted (the JD Doc), not boilerplate-stripped."""
    clean = (lambda text: text) if raw else strip_boilerplate
    try:
        posting = jobfetch.fetch_posting(url, resolve)
    except ValueError as err:
        message = str(err)
        return ("gone" if "closed" in message else "unreadable"), None, message
    if posting is not None:
        if posting.get("blocked"):
            return "unreadable", None, "robots.txt does not allow reading this posting"
        text = clean(posting.get("description") or "")
        return ("saved", text, posting["ats"]) if text else ("unreadable", None, "the ATS answered without a JD")
    try:
        page = get_text(url)
    except HttpError as err:
        if err.status in jobfetch.CLOSED_STATUSES:
            return "gone", None, f"HTTP {err.status}"
        return "unreadable", None, ("robots.txt disallows it" if err.robots else f"HTTP {err.status or 'error'}")
    text = _json_ld(page)
    if text:
        return "saved", clean(text), "schema.org JobPosting"
    plain = html_to_text(page)
    if len(plain) >= MIN_PAGE_TEXT:
        return "saved", plain, "page text"
    return "unreadable", None, "no JD text on the page (it may need JavaScript)"


def get(conn, app_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM jd_snapshots WHERE app_id = ?", (app_id,)).fetchone()
    return dict(row) if row else None


def _save(conn, app_id: int, url: str | None, text: str | None, source: str | None, state: str,
          detail: str | None = None) -> dict:
    """Upsert; text saved earlier is never dropped (a posting gone later stays 'saved' with its text)."""
    now = db.iso(db.utcnow())
    with db.tx(conn):
        conn.execute("INSERT INTO jd_snapshots(app_id, url, text, source, state, fetched_at, checked_at, detail) "
                     "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(app_id) DO UPDATE SET url = coalesce(excluded.url, "
                     "jd_snapshots.url), text = coalesce(excluded.text, jd_snapshots.text), source = "
                     "coalesce(excluded.source, jd_snapshots.source), state = CASE WHEN jd_snapshots.text IS NOT NULL "
                     "AND excluded.text IS NULL THEN 'saved' ELSE excluded.state END, fetched_at = "
                     "coalesce(excluded.fetched_at, jd_snapshots.fetched_at), checked_at = excluded.checked_at, "
                     "detail = excluded.detail",
                     (app_id, url, text, source, state, now if text else None, now, detail))
    return get(conn, app_id)


def keep(conn, app_id: int, url: str | None, text: str, source: str) -> dict:
    """a JD the person pasted elsewhere (resumegen) kept as the row's snapshot."""
    return _save(conn, app_id, url, text, source, "saved")


def snapshot(conn, app_id: int, fetch: bool = False, resolve=None) -> dict:
    """Store the row's JD text + URL (feed, then the cell's own text, then - only when fetch - the live posting).
    A row already saved keeps its text; a gone posting never loses text saved earlier."""
    app = dict(conn.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone())
    url, have = posting_url(app), get(conn, app_id)
    if have and have["state"] == "saved" and have["text"]:
        return have
    text, closed = _feed_text(conn, app)
    if text:
        return _save(conn, app_id, url, text, "feed", "saved", "posting closed since" if closed else None)
    text = _cell_text(app, url)
    if text:
        return _save(conn, app_id, url, text, "sheet", "saved")
    if not url:
        return _save(conn, app_id, None, None, None, "no_url", "no posting link on this row")
    if closed:
        return _save(conn, app_id, url, None, None, "gone", "the feed saw the posting close")
    if not fetch:
        return have or _save(conn, app_id, url, None, None, "unreadable", "not fetched yet")
    state, text, detail = fetch_text(url, resolve or jobfetch.db_resolver(conn))
    return _save(conn, app_id, url, text, "fetch" if text else None, state, detail)


def fill_cell(conn, app_id: int) -> bool:
    """The row's JD cell becomes the linked title and its text goes to jd_text (the sync writes both, the link, the
    note and the JD Text cell). For a row the app made whose cell is empty or only the posting link, and for any cell
    in the old "URL + JD" style; never over text the person typed, never into a row the person emptied in the Sheet
    (no company and no role). Returns True when the row changed."""
    app = dict(conn.execute("SELECT jd, jd_text, company, role_title, job_url, source FROM applications WHERE id = ?",
                            (app_id,)).fetchone())
    if not (app["company"] or "").strip() and not (app["role_title"] or "").strip():
        return False
    cell = (app["jd"] or "").strip()
    old = URL_PLUS_JD.match(cell)
    if not (old or ((app["source"] or "") in APP_MADE and (not cell or link_only(cell)))):
        return False
    snap = get(conn, app_id)
    text = snap["text"] if snap and snap["state"] == "saved" and snap["text"] else old.group(2) if old else None
    url = (snap or {}).get("url") or link_only(cell) or (old.group(1) if old else None)
    if not text:
        return False
    title = (app["role_title"] or "").strip() or NO_TITLE
    if (cell, app["jd_text"]) == (title, text):
        return False
    with history.action(history.CLI):                         # one undo step (inside a click: the person's)
        tracker.edit_field(conn, app_id, "jd", title)
        tracker.set_jd_text(conn, app_id, text, url)
    return True


def split_typed(conn, app: dict, text: str) -> None:
    """A JD typed or pasted in the app with more than one line: the cell gets the linked title and the text goes to
    jd_text (the hidden JD Text column); a leading posting URL line ("URL + JD") becomes the row's link."""
    old = URL_PLUS_JD.match(text.strip())
    body, url = (old.group(2).strip(), old.group(1)) if old else (text.strip(), None)
    title = (app.get("role_title") or "").strip() or NO_TITLE
    if (app.get("jd") or "") != title:
        tracker.edit_field(conn, app["id"], "jd", title)
    tracker.set_jd_text(conn, app["id"], body, url)


def on_save(conn, app_id: int, fetch: bool = False) -> dict:
    """GOAL6 §3 "snapshot on Mark applied / save": keep the text, then fill the JD cell. Never raises (a failed
    snapshot must not fail the click; the backfill retries)."""
    try:
        snap = snapshot(conn, app_id, fetch=fetch)
        fill_cell(conn, app_id)
        return snap
    except Exception:                                       # network, odd page: logged, retried by the backfill
        log.exception("JD snapshot failed for application %s", app_id)
        return {}
