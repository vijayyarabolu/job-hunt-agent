"""The full JD as a Google Doc: one Doc per application, "<Company> - <Role Title> - JD", in
job-hunt-agent/Resumes/<Profile>/ (the resume uploads' folder, scope and share). Saved when a row is marked applied (in the
background, never failing the click) and by `job-hunt-agent jd-docs` (the backfill, newest first, paced).

One Doc per application: jd_docs keeps its file id and the hash of what was written; a later save with new content
updates that same Doc in place (Drive re-imports the HTML), never a second one. A row with no JD text anywhere gets
one posting fetch; when that finds nothing the row says "posting gone, no JD saved".

The Sheet (decision 11): the row's JD cell links to the Doc. A one-line cell the app wrote (the role title linked to
the posting) links to the Doc instead; the person's older multi-line cells keep their text, format and own links,
and only their first line without a link becomes the link (textFormatRuns only, under the sync lock). The posting URL
stays in the Doc's header and the app.
"""
import hashlib
import logging
import sqlite3
import threading
import time
from collections import Counter
from html import escape

from . import db, gdrive_upload, gsync, jdsnap, jobfetch
from . import jd_doc_html as html
from .google import GDOC, GoogleError, NeedsConsent, NotConfigured, accounts, api, auth, base
from .google import state as gstate
from .gsync_values import urls
from .text import strip_boilerplate

log = logging.getLogger(__name__)
APP_PROP = "job_hunt_agent_jd"
PACE_S = 0.5                              # between Drive writes in the backfill (Drive allows ~3 writes/s)
LOCK_WAIT_S = 60
PROGRESS_EVERY = 50
DOC_HOST = "docs.google.com/document/"
FILE_FIELDS = "id,name,webViewLink,trashed"
BACKGROUND = True                         # Mark applied saves the Doc in the background; the test package turns it off
MISSING_PER_SYNC = 5                      # Docs started per Sheet sync for rows that have none (rows typed there)


def get(conn, app_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM jd_docs WHERE app_id = ?", (app_id,)).fetchone()
    return dict(row) if row else None


def link_of(conn, app_id: int) -> str | None:
    row = conn.execute("SELECT link FROM jd_docs WHERE app_id = ? AND state = 'saved'", (app_id,)).fetchone()
    return row["link"] if row else None


def doc_name(pairs: list[tuple[str, str]]) -> str:
    return f"{html.title(pairs)} - JD"


def _app(conn, app_id: int) -> dict:
    row = conn.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
    if row is None:
        raise KeyError(app_id)
    return dict(row)


def _record(conn, app: dict, state: str, **values) -> dict:
    v = {"file_id": None, "link": None, "name": None, "sha": None, "source": None, "detail": None, **values}
    with db.tx(conn):
        conn.execute("INSERT INTO jd_docs(app_id, profile, state, file_id, link, name, sha, source, detail, saved_at) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(app_id) DO UPDATE SET state = excluded.state, "
                     "file_id = coalesce(excluded.file_id, jd_docs.file_id), link = coalesce(excluded.link, "
                     "jd_docs.link), name = coalesce(excluded.name, jd_docs.name), sha = excluded.sha, "
                     "source = excluded.source, detail = excluded.detail, saved_at = excluded.saved_at",
                     (app["id"], app["profile"], state, v["file_id"], v["link"], v["name"], v["sha"], v["source"],
                      v["detail"], db.iso(db.utcnow())))
    return get(conn, app["id"])


def fetch_once(conn, app: dict) -> tuple[str | None, str]:
    """One read of the live posting for a row with no JD text: (the text as posted, what happened). The snapshot
    keeps the cleaned text, as before."""
    url = jdsnap.posting_url(app)
    if not url:
        return None, "no posting link on this row"
    state, text, detail = jdsnap.fetch_text(url, jobfetch.db_resolver(conn), raw=True)
    jdsnap._save(conn, app["id"], url, strip_boilerplate(text) if text else None, "fetch" if text else None,
                 "saved" if text else state, detail)
    return text, (detail or state)


def _tried(conn, app_id: int) -> bool:
    """The posting was already read for this row and was gone (fetch once, not on every save)."""
    snap, have = jdsnap.get(conn, app_id), get(conn, app_id)
    return bool((have and have["state"] == "gone") or (snap and snap["state"] in ("gone", "no_url")))


def content(conn, app: dict, fetch: bool = False) -> tuple[list[tuple[str, str]], str | None, str | None, str]:
    """(header, JD text, its source, detail) for the row; with fetch, one posting read when nothing holds the JD."""
    job = html.job_of(conn, app)
    text, source = html.source_text(conn, app, job)
    detail = ""
    if not text and fetch and not _tried(conn, app["id"]):
        text, detail = fetch_once(conn, app)
        source = "fetch" if text else None
    elif not text:
        detail = (jdsnap.get(conn, app["id"]) or {}).get("detail") or "no JD text kept for this row"
    return html.header(app, job), text, source, detail


def _folder(conn, pid: str) -> str:
    return gdrive_upload.profile_folder(conn, pid)["id"]


def _find(folder_id: str, app_id: int) -> str | None:
    """A Doc this app made for the row earlier (a lost jd_docs row must not make a second Doc)."""
    q = (f"appProperties has {{ key='{APP_PROP}' and value='{app_id}' }} and '{folder_id}' in parents and "
         f"mimeType = '{GDOC}' and trashed = false")
    found = api.get(f"{base('drive')}/files", {"q": q, "fields": "files(id)", "spaces": "drive"}).get("files") or []
    return found[0]["id"] if found else None


def _patch(file_id: str, name: str, page: str) -> dict:
    body, ctype = api.multipart({"name": name}, page.encode(), "text/html")
    return api.call("PATCH", f"{base('upload')}/files/{file_id}",
                    params={"uploadType": "multipart", "fields": FILE_FIELDS}, data=body, ctype=ctype).json()


def _create(folder_id: str, name: str, page: str, app: dict) -> dict:
    """A new Doc holding only its title, then the content as an update: Drive's create-time import turned
    "$122k–$167k" into an equation image (2026-10-08, the Analog Devices Doc); its update import keeps the text."""
    meta = {"name": name, "parents": [folder_id], "mimeType": GDOC,
            "appProperties": {APP_PROP: str(app["id"]), "profile": app["profile"]}}
    body, ctype = api.multipart(meta, f"<p>{escape(name)}</p>".encode(), "text/html")
    made = api.call("POST", f"{base('upload')}/files", params={"uploadType": "multipart", "fields": FILE_FIELDS},
                    data=body, ctype=ctype).json()
    return _patch(made["id"], name, page)


def _update(file_id: str, name: str, page: str) -> dict | None:
    """The same Doc with new content and name; None when it is gone or in the trash (then a new one is made)."""
    try:
        if not gdrive_upload._alive(file_id):
            return None
    except GoogleError as err:
        if err.status in (403, 404):
            return None
        raise
    return _patch(file_id, name, page)


def save(conn, app_id: int, fetch: bool = False, folders: dict | None = None) -> dict:
    """Create or update the row's Doc (in the person's own Drive when they connected Google). Returns the
    jd_docs row plus "action": created | updated | unchanged | gone. folders: (account, profile) -> folder id, kept
    across one backfill (the folder is checked once, not per Doc)."""
    app = _app(conn, app_id)
    return accounts.with_fallback(app["profile"], _save, conn, app, fetch, folders)


def _save(conn, app: dict, fetch: bool, folders: dict | None) -> dict:
    app_id = app["id"]
    pairs, text, source, detail = content(conn, app, fetch)
    have = get(conn, app_id)
    if not text:
        why = f"{html.GONE} ({detail})" if detail else html.GONE
        return {**_record(conn, app, "gone", detail=why), "action": "gone"}
    page, name = html.doc_html(pairs, text), doc_name(pairs)
    sha = hashlib.sha256(page.encode()).hexdigest()
    if have and have["state"] == "saved" and (have["sha"], have["name"]) == (sha, name):
        return {**have, "action": "unchanged"}
    folders, key = ({} if folders is None else folders), (auth.current(), app["profile"])
    folder_id = folders.get(key) or folders.setdefault(key, _folder(conn, app["profile"]))
    file_id = (have or {}).get("file_id") or _find(folder_id, app_id)
    made = _update(file_id, name, page) if file_id else None
    action = "updated" if made else "created"
    made = made or _create(folder_id, name, page, app)
    return {**_record(conn, app, "saved", file_id=made["id"], link=made.get("webViewLink"), name=name, sha=sha,
                      source=source), "action": action}


# ---- the Sheet: the JD cell links to the Doc ----

def _utf16(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2              # Sheets run indexes count UTF-16 code units


def _norm(runs: list[dict]) -> list[dict]:
    return sorted(({"startIndex": r.get("startIndex", 0), "format": r.get("format") or {}} for r in runs),
                  key=lambda r: r["startIndex"])


def _format_at(runs: list[dict], index: int) -> dict:
    found = {}
    for run in runs:
        if run["startIndex"] <= index:
            found = run["format"]
    return found


def _links_over(runs: list[dict], start: int, end: int, size: int) -> set[str]:
    ends = [r["startIndex"] for r in runs[1:]] + [size]
    return {r["format"]["link"]["uri"] for r, stop in zip(runs, ends)
            if r["startIndex"] < end and stop > start and (r["format"].get("link") or {}).get("uri")}


def _lines(text: str):
    """(start, end, line) of each non-empty line, trimmed; start and end in UTF-16 units."""
    at = 0
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped:
            lead = at + len(line) - len(line.lstrip())
            yield _utf16(text[:lead]), _utf16(text[:lead + len(stripped)]), stripped
        at += len(line) + 1


def _unlinked(fmt: dict) -> dict:
    return {k: v for k, v in fmt.items() if k != "link"}


def _merge(runs: list[dict], start: int, end: int, link: str, size: int, replaced) -> list[dict]:
    out = [r for r in runs if r["startIndex"] < start]
    if start > 0 and not out:
        out.append({"startIndex": 0, "format": {}})
    out.append({"startIndex": start, "format": {**_unlinked(_format_at(runs, start)), "link": {"uri": link}}})
    out += [{"startIndex": r["startIndex"], "format": {**_unlinked(r["format"]), "link": {"uri": link}}}
            for r in runs if start < r["startIndex"] < end]
    if end < size and not any(r["startIndex"] == end for r in runs):
        after = _format_at(runs, end)
        uri = (after.get("link") or {}).get("uri")
        out.append({"startIndex": end, "format": _unlinked(after) if uri and replaced(uri) else after})
    return out + [r for r in runs if r["startIndex"] >= end]


def linked_runs(text: str, runs: list[dict], link: str, replaceable: tuple = ()) -> tuple[list[dict] | None, str]:
    """The cell's runs with its first free line linked to the Doc: (runs, "linked") or (None, why not). A line is
    free when it is not a URL and has no link of its own (a link to one of our Docs, or to `replaceable` - the
    posting, on the one-line cell the app wrote - may be replaced)."""
    if not text.strip():
        return None, "the JD cell is empty"
    runs, size = _norm(runs), _utf16(text)

    def replaced(uri: str) -> bool:
        return uri == link or DOC_HOST in uri or uri in replaceable

    for start, end, line in _lines(text):
        found = _links_over(runs, start, end, size)
        if found == {link}:
            return None, "already linked"
        if urls(line) or not all(replaced(u) for u in found):
            continue
        return _merge(runs, start, end, link, size, replaced), "linked"
    return None, "every line of the JD cell has its own link"


def _jd_cells(target: gsync.Target, col: int) -> list[dict]:
    a1 = f"{gsync.quoted(target.title)}!{gsync.letter(col)}1:{gsync.letter(col)}"
    got = api.get(gsync.sheets_url(target.sid), {
        "ranges": a1, "fields": "sheets(data(rowData(values(userEnteredValue,formattedValue,hyperlink,"
                                "textFormatRuns,chipRuns))))"})
    rows = [row for sheet in got.get("sheets", []) for block in sheet.get("data", [])
            for row in block.get("rowData", [])]
    return [(row.get("values") or [{}])[0] for row in rows]


def _cell_plan(cell: dict, link: str, app: dict) -> tuple[list[dict] | None, str]:
    entered = cell.get("userEnteredValue") or {}
    if "formulaValue" in entered:
        return None, "the JD cell holds a formula"
    if cell.get("chipRuns"):
        return None, "the JD cell holds chips"
    text = entered.get("stringValue") or cell.get("formattedValue") or ""
    posting = tuple(u for u in (app.get("job_url"), app.get("apply_url")) if u) if "\n" not in text else ()
    runs = cell.get("textFormatRuns") or ([{"format": {"link": {"uri": cell["hyperlink"]}}}]
                                          if cell.get("hyperlink") else [])
    return linked_runs(text, runs, link, posting)


def _rows_of(target: gsync.Target, app_ids: set[int]) -> dict[int, int]:
    from .gsync_jd import rows_of                          # late: gsync_jd imports this module
    return rows_of(target, app_ids)


def link_rows(conn, pid: str, app_ids: set[int] | None = None) -> Counter:
    """Link the JD cells of the profile's rows (all with a Doc, or `app_ids`) to their Docs. Counts per outcome.
    On the person's own token when connected (their sheet), else the owner's."""
    return accounts.with_fallback(pid, _link_rows, conn, pid, app_ids)


def _link_rows(conn, pid: str, app_ids: set[int] | None) -> Counter:
    docs = {r["app_id"]: r["link"] for r in conn.execute(
        "SELECT app_id, link FROM jd_docs WHERE profile = ? AND state = 'saved' AND link IS NOT NULL", (pid,))
        if app_ids is None or r["app_id"] in app_ids}
    counts = Counter()
    if not docs or not gsync.linked_sid(conn, pid):
        counts["not on a Sheet"] += len(docs)
        return +counts
    with gstate.lock(wait_s=LOCK_WAIT_S) as held:
        if not held:
            raise GoogleError("the Sheet sync is busy: run `job-hunt-agent jd-docs --link-only` again in a minute")
        for target in gsync.targets(conn, pid):
            if "JD" not in target.headers or target.key_col is None:
                continue
            col = target.headers.index("JD")
            rows, cells, requests = _rows_of(target, set(docs)), _jd_cells(target, col), []
            for app_id, index in rows.items():
                cell = cells[index] if index < len(cells) else {}
                runs, why = _cell_plan(cell, docs[app_id], _app(conn, app_id))
                counts[why] += 1
                if runs is not None:
                    requests.append({"updateCells": {
                        "start": {"sheetId": target.sheet_id, "rowIndex": index, "columnIndex": col},
                        "rows": [{"values": [{"textFormatRuns": runs}]}], "fields": "textFormatRuns"}})
            gsync.batch(target.sid, requests)
    counts["not on a Sheet"] += len(docs) - sum(counts.values())
    return +counts


# ---- when a row is marked applied ----

def _google_ready() -> bool:
    if not BACKGROUND:
        return False
    try:
        return auth.load() is not None
    except (OSError, ValueError, GoogleError):
        return False


def save_and_link(app_id: int) -> None:
    """The background part of Mark applied: its own connection; failures are logged (the backfill retries)."""
    conn = db.connect()
    try:
        done = save(conn, app_id, fetch=True)
        if done.get("state") == "saved":
            link_rows(conn, done["profile"], {app_id})
    except Exception:                                       # network, quota, a busy sync: never the click's problem
        log.exception("JD Doc failed for application %s", app_id)
    finally:
        conn.close()


def missing(conn, pid: str, limit: int = MISSING_PER_SYNC) -> list[int]:
    """Rows with no Doc yet, newest first: typed into the Sheet, a save whose Doc failed, or a "gone" row changed
    since (its JD pasted later). A re-save that is still gone stamps saved_at, so no row is retried every cycle."""
    return [r["id"] for r in conn.execute(
        "SELECT a.id FROM applications a LEFT JOIN jd_docs d ON d.app_id = a.id WHERE a.profile = ? AND "
        "(d.app_id IS NULL OR (d.state = 'gone' AND a.updated_at > d.saved_at)) ORDER BY a.id DESC LIMIT ?",
        (pid, limit))]


def soon(app_id: int) -> threading.Thread | None:
    """Start the row's Doc in the background (Mark applied). None when Google is not connected."""
    if not _google_ready():
        return None
    worker = threading.Thread(target=save_and_link, args=(app_id,), name=f"jd-doc-{app_id}", daemon=True)
    worker.start()
    return worker


# ---- the backfill ----

def _newest_first(app: dict) -> tuple:
    month, _, rest = (app.get("date_applied") or "").partition("/")
    day, _, year = rest.partition("/")
    if not (year.isdigit() and month.isdigit() and day.isdigit()):
        return ("", "", "", app["id"])
    year = f"20{year}" if len(year) == 2 else year               # the older rows say "01/28/26"
    return (year, month.zfill(2), day.zfill(2), app["id"])


BUSY_TRIES, BUSY_WAIT_S = 3, 30


def _save_waiting(conn, app_id: int, fetch: bool, folders: dict) -> str:
    """save(), waiting out a database the live server holds busy (a re-try finds the Doc it made by its tag)."""
    for attempt in range(1, BUSY_TRIES + 1):
        try:
            return save(conn, app_id, fetch=fetch, folders=folders)["action"]
        except sqlite3.OperationalError as err:
            if "locked" not in str(err) or attempt == BUSY_TRIES:
                raise
            log.warning("JD Doc for application %s: database busy, again in %ss", app_id, BUSY_WAIT_S)
            time.sleep(BUSY_WAIT_S)


def backfill(conn, pids: list[str], limit: int | None = None, pace_s: float = PACE_S, fetch: bool = True,
             link: bool = True, say=print) -> dict:
    """Every row with JD text gets its Doc (newest first; unchanged ones cost no request), rows without text one
    posting fetch, then each profile's JD cells link to the Docs. Returns counts per profile."""
    out = {}
    for pid in pids:
        apps = sorted((dict(r) for r in conn.execute("SELECT id, date_applied FROM applications WHERE profile = ?",
                                                      (pid,))), key=_newest_first, reverse=True)
        counts, written, folders = Counter(), 0, {}
        for n, app in enumerate(apps, 1):
            if limit is not None and written >= limit:
                counts["left for later"] += 1
                continue
            try:
                action = _save_waiting(conn, app["id"], fetch, folders)
            except (NeedsConsent, NotConfigured):
                raise
            except GoogleError as err:
                log.warning("JD Doc for application %s: %s", app["id"], err)
                action = "failed"
            except KeyError:                       # merged into its Sheet twin by a sync during the run
                action = "merged away"
            counts[action] += 1
            if action in ("created", "updated"):
                written += 1
                time.sleep(pace_s)
            if n % PROGRESS_EVERY == 0:
                say(f"{pid}: {n}/{len(apps)} {dict(counts)}")
        out[pid] = {"docs": dict(counts), "sheet": dict(link_rows(conn, pid)) if link else {}}
        say(f"{pid}: {out[pid]}")
    return out


# ---- the app's "Full JD" page ----

def page(conn, pid: str, app_id: int | None = None, uid: str | None = None) -> str:
    """The Full JD page for a tracker row (app_id) or a job (uid; its row when there is one). A row with no JD text
    gets one posting fetch; when that finds nothing the page says "posting gone, no JD saved"."""
    if app_id is not None:
        app = _app(conn, app_id)
        if app["profile"] != pid:
            raise KeyError(app_id)
    else:
        found = conn.execute("SELECT * FROM applications WHERE profile = ? AND job_uid = ?", (pid, uid)).fetchone()
        app = dict(found) if found else None
    if app is None:
        job = html.job_of(conn, None, uid)
        if not job:
            raise KeyError(uid)
        return html.page_html(html.header(None, job), html.source_text(conn, None, job)[0], None)
    pairs, text, _, detail = content(conn, app, fetch=True)
    if not text:
        _record(conn, app, "gone", detail=f"{html.GONE} ({detail})" if detail else html.GONE)
    return html.page_html(pairs, text, link_of(conn, app["id"]))
