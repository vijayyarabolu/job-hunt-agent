"""Move a person's app-made files from the owner's Drive to their own (user 2026-10-08: "For her use
everything hers, not mine"). Runs in the background right after the person connects Google, and again by hand with
`job-hunt-agent google move --profile <p>` (resumable: google_moves remembers every file done). a move the server
was running when it stopped ("running" in google_state, its lock free) resumes when the server starts again; one
lock file per person keeps a hand-run and the server's from running at once.

Each file is read with the owner's token and re-made with the person's, same name:
  - resume uploads and approved resumes (PDF / DOCX)  -> job-hunt-agent/Resumes/<Profile>/ in their Drive
  - "Open in Google Docs" Docs (resumegen)            -> resumegen/docs/ in their Drive (exported as .docx, re-imported)
  - JD Docs (§1)                                      -> written again from the stored JD into their folder
Then every link follows: resume_uploads and jd_docs here, resumegen's jobs (POST /api/google/relink), and the
person's sheet (Resume chips and links, JD links: only the link targets change, never the cell text). The owner's
old copies go to "job-hunt-agent/Old - <Name> (moved)" in the owner's Drive; nothing is deleted.
"""
import fcntl
import logging
import re
import threading
import time
from contextlib import contextmanager

from . import config, db, gdrive_upload, gsync, jd_doc, rgclient
from .google import DOCX, GDOC, PDF, GoogleError, accounts, api, base
from .google import state as gstate

log = logging.getLogger(__name__)
STATE_KEY = "google_move.{pid}"
PACE_S = 0.2
PROGRESS_EVERY = 25                         # JD Docs between progress notes on the Google account page
RESUMING = "Stopped by a restart, resuming"
LOCK_WAIT_S = 120
_FILE_ID = re.compile(r"(?:drive|docs)\.google\.com/(?:[a-z]+/)*d/([A-Za-z0-9_-]+)")
_RUNNING: set[str] = set()
_RUNNING_LOCK = threading.Lock()


@contextmanager
def _held(pid: str):
    """This person's move lock (fcntl, across processes: the server and a hand-run). Yields False when it is taken."""
    config.DATA.mkdir(parents=True, exist_ok=True)
    with open(config.DATA / f"google_move.{pid}.lock", "w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def last(pid: str) -> dict | None:
    conn = db.connect()
    try:
        return gstate.get_json(conn, STATE_KEY.format(pid=pid))
    finally:
        conn.close()


def _note(conn, pid: str, state: str, summary: str) -> None:
    with db.tx(conn):
        gstate.put(conn, STATE_KEY.format(pid=pid), {"state": state, "summary": summary, "at": db.iso(db.utcnow())})


def soon(pid: str, resumed: bool = False) -> threading.Thread:
    """Start the move in the background (one per person at a time)."""
    worker = threading.Thread(target=_run_quietly, args=(pid, resumed), name=f"google-move-{pid}", daemon=True)
    worker.start()
    return worker


def resume_stopped(pids: list[str]) -> list[str]:
    """Server start: every move left "running" with its lock free was stopped by a restart; it goes on in the
    background. Returns the people resumed."""
    resumed = []
    for pid in pids:
        if (last(pid) or {}).get("state") != "running" or not accounts.connected(pid):
            continue
        with _held(pid) as free:
            if not free:                                       # a hand-run is moving them right now
                continue
        conn = db.connect()
        try:
            _note(conn, pid, "running", RESUMING)
        finally:
            conn.close()
        soon(pid, resumed=True)
        resumed.append(pid)
    return resumed


def _run_quietly(pid: str, resumed: bool = False) -> None:
    conn = db.connect()
    try:
        run(conn, pid, resumed=resumed)
    except Exception as err:                                  # shown on the Google account page; re-runnable
        log.exception("moving %s's files failed", pid)
        _note(conn, pid, "stopped", f"{type(err).__name__}: {err} (it continues where it stopped on the next run)")
    finally:
        conn.close()


# ---- what there is to move ----

def _moved(conn, pid: str) -> dict[str, dict]:
    return {r["old_id"]: dict(r) for r in conn.execute("SELECT * FROM google_moves WHERE profile = ?", (pid,))}


def _files(conn, pid: str) -> list[dict]:
    """The person's app-made files in the owner's Drive: kind, old id, name, mime."""
    out = {}
    for r in conn.execute("SELECT file_id, name, mime FROM resume_uploads WHERE profile = ? AND file_id IS NOT NULL",
                          (pid,)):
        out[r["file_id"]] = {"kind": "upload", "old_id": r["file_id"], "name": r["name"], "mime": r["mime"]}
    try:
        jobs = rgclient.request("GET", f"/api/jobs?profile={pid}").get("jobs") or []
    except (rgclient.Offline, ValueError, KeyError) as err:
        raise GoogleError(f"resumegen is not answering, so its files cannot move yet: {err}") from err
    for job in (j for j in jobs if j.get("profile") == pid):
        if job.get("drive_file_id"):
            out.setdefault(job["drive_file_id"], {"kind": "approved", "old_id": job["drive_file_id"],
                                                  "name": job.get("drive_name"), "mime": PDF})
        if job.get("gdoc_id"):
            out.setdefault(job["gdoc_id"], {"kind": "doc", "old_id": job["gdoc_id"], "name": None, "mime": GDOC})
    return list(out.values())


# ---- one file: read with the owner's token, re-made with the person's ----

def _read(old: dict) -> tuple[bytes, str, str]:
    """(bytes, mime, name) of the owner's copy (a Google Doc comes as .docx)."""
    with accounts.as_owner():
        meta = api.get(f"{base('drive')}/files/{old['old_id']}", {"fields": "name,mimeType"})
        if meta["mimeType"] == GDOC:
            data = api.call("GET", f"{base('drive')}/files/{old['old_id']}/export", params={"mimeType": DOCX}).body
            return data, DOCX, meta["name"]
        data = api.call("GET", f"{base('drive')}/files/{old['old_id']}", params={"alt": "media"}).body
        return data, meta["mimeType"], meta["name"]


def _remake(conn, pid: str, old: dict, data: bytes, mime: str, name: str) -> dict:
    """The person's copy, same name, in their Drive (the person's token must be the one in use)."""
    if old["kind"] == "doc":
        parent = gdrive_upload.folder(conn, ("resumegen", "docs"))["id"]
        meta = {"name": name, "parents": [parent], "mimeType": GDOC, "appProperties": {"moved_from": old["old_id"]}}
        body, ctype = api.multipart(meta, data, DOCX)
        return api.call("POST", f"{base('upload')}/files", params={"uploadType": "multipart",
                                                                   "fields": gdrive_upload.FILE_FIELDS},
                        data=body, ctype=ctype).json()
    folder = gdrive_upload.profile_folder(conn, pid)
    return gdrive_upload.upload(folder["id"], name, data, mime, {"moved_from": old["old_id"], "profile": pid})


def _record(conn, pid: str, old: dict, name: str, made: dict) -> dict:
    row = {"old_id": old["old_id"], "profile": pid, "kind": old["kind"], "name": name, "new_id": made["id"],
           "new_link": made.get("webViewLink"), "moved_at": db.iso(db.utcnow()), "parked_at": None}
    with db.tx(conn):
        conn.execute("INSERT INTO google_moves(old_id, profile, kind, name, new_id, new_link, moved_at) "
                     "VALUES (:old_id, :profile, :kind, :name, :new_id, :new_link, :moved_at)", row)
    return row


def _move_files(conn, pid: str, done: dict) -> int:
    count, theirs = 0, {row["new_id"] for row in done.values()}
    for old in _files(conn, pid):
        if old["old_id"] in done or old["old_id"] in theirs:       # done, or already the person's own copy
            continue
        data, mime, name = _read(old)
        with accounts.as_profile(pid):
            made = _remake(conn, pid, old, data, mime, old["name"] or name)
        done[old["old_id"]] = _record(conn, pid, old, old["name"] or name, made)
        count += 1
        time.sleep(PACE_S)
    with db.tx(conn):                                          # the app's own records follow
        for old_id, row in done.items():
            if row["kind"] in ("upload", "approved"):
                conn.execute("UPDATE resume_uploads SET file_id = ?, link = ? WHERE profile = ? AND file_id = ?",
                             (row["new_id"], row["new_link"], pid, old_id))
    return count


def _jd_to_move(conn, pid: str, done: dict) -> list[dict]:
    """Saved JD Docs still in the owner's Drive. A row a stopped move had cleared (file_id NULL, the old link kept)
    is found again by its link; _save() then finds the person's copy by its tag if it was made already."""
    new_ids, out = {d["new_id"] for d in done.values()}, []
    for r in conn.execute("SELECT app_id, file_id, link, name FROM jd_docs WHERE profile = ? AND state = 'saved' "
                          "AND (file_id IS NOT NULL OR link IS NOT NULL)", (pid,)):
        old_id = r["file_id"] or _file_id(r["link"])
        if old_id and old_id not in done and old_id not in new_ids:
            out.append({**dict(r), "old_id": old_id})
    return out


def _move_jd_docs(conn, pid: str, done: dict, say=lambda text: None) -> int:
    """JD Docs are written again from the stored JD (same name) into the person's folder."""
    count, folders, rows = 0, {}, _jd_to_move(conn, pid, done)
    before = sum(1 for d in done.values() if d["kind"] == "jd_doc")
    for r in rows:
        with db.tx(conn):                                      # forget the owner's copy: _save() makes a new one
            conn.execute("UPDATE jd_docs SET file_id = NULL, sha = NULL WHERE app_id = ?", (r["app_id"],))
        with accounts.as_profile(pid):
            made = jd_doc._save(conn, jd_doc._app(conn, r["app_id"]), False, folders)
        if made["file_id"] == r["old_id"]:                     # hers already (a stop between save and record)
            continue
        done[r["old_id"]] = _record(conn, pid, {"old_id": r["old_id"], "kind": "jd_doc"}, r["name"],
                                    {"id": made["file_id"], "webViewLink": made["link"]})
        count += 1
        if count % PROGRESS_EVERY == 0:
            say(f"writing your JD Docs: {before + count} of {before + len(rows)} in your Drive")
        time.sleep(PACE_S)
    return count


# ---- links ----

def _file_id(uri: str | None) -> str | None:
    found = _FILE_ID.search(uri or "")
    return found.group(1) if found else None


def _swap_runs(runs: list[dict], links: dict[str, str]) -> list[dict]:
    out = []
    for run in runs:
        uri = ((run.get("format") or {}).get("link") or {}).get("uri")
        moved = links.get(_file_id(uri))
        out.append({**run, "format": {**run["format"], "link": {"uri": moved}}} if moved else run)
    return out


def _swap_chips(chips: list[dict], links: dict[str, str]) -> list[dict]:
    out = []
    for chip in chips:
        uri = ((chip.get("chip") or {}).get("richLinkProperties") or {}).get("uri")
        moved = links.get(_file_id(uri))
        out.append({**chip, "chip": {"richLinkProperties": {"uri": moved}}} if moved else chip)
    return out


def swapped_cell(cell: dict, links: dict[str, str]) -> tuple[dict, str] | None:
    """(the cell's runs / chips / whole-cell link with moved files' links swapped, the fields mask); None when the
    cell links no moved file. links: old file id -> new link."""
    out, fields = {}, []
    runs, chips = cell.get("textFormatRuns") or [], cell.get("chipRuns") or []
    new_runs, new_chips = _swap_runs(runs, links), _swap_chips(chips, links)
    if new_runs != runs:
        out["textFormatRuns"] = new_runs
        fields.append("textFormatRuns")
    if new_chips != chips:
        out["chipRuns"] = new_chips
        fields.append("chipRuns")
    whole = (((cell.get("userEnteredFormat") or {}).get("textFormat") or {}).get("link") or {}).get("uri")
    if not runs and links.get(_file_id(whole)):
        out["userEnteredFormat"] = {"textFormat": {"link": {"uri": links[_file_id(whole)]}}}
        fields.append("userEnteredFormat.textFormat.link")
    return (out, ",".join(fields)) if fields else None


def _column_cells(target: gsync.Target, col: int) -> list[dict]:
    a1 = f"{gsync.quoted(target.title)}!{gsync.letter(col)}1:{gsync.letter(col)}"
    got = api.get(gsync.sheets_url(target.sid), {
        "ranges": a1, "fields": "sheets(data(rowData(values(textFormatRuns,chipRuns,userEnteredFormat(textFormat)))))"})
    rows = [row for sheet in got.get("sheets", []) for block in sheet.get("data", [])
            for row in block.get("rowData", [])]
    return [(row.get("values") or [{}])[0] for row in rows]


def relink_sheet(conn, pid: str, done: dict) -> int:
    """The person's sheet: Resume and JD cells that link a moved file link the new copy (text untouched)."""
    links = {old_id: row["new_link"] for old_id, row in done.items() if row.get("new_link")}
    if not links or not gsync.linked_sid(conn, pid):
        return 0
    changed = 0
    with gstate.lock(wait_s=LOCK_WAIT_S) as held:
        if not held:
            raise GoogleError("the Sheet sync is busy: the move continues on the next run")
        for target in gsync.targets(conn, pid):
            requests = []
            for col in (c for c, k in target.synced if k.field == "resume" or k.header == "JD"):
                for index, cell in enumerate(_column_cells(target, col)):
                    swap = swapped_cell(cell, links) if index else None
                    if swap:
                        requests.append({"updateCells": {
                            "start": {"sheetId": target.sheet_id, "rowIndex": index, "columnIndex": col},
                            "rows": [{"values": [swap[0]]}], "fields": swap[1]}})
            gsync.batch(target.sid, requests)
            changed += len(requests)
    return changed


def _relink_resumegen(pid: str, done: dict) -> dict:
    files = {old_id: {"id": row["new_id"], "link": row["new_link"], "name": row["name"]}
             for old_id, row in done.items() if row["kind"] in ("approved", "doc", "upload")}
    return rgclient.request("POST", "/api/google/relink", {"profile": pid, "files": files}) if files else {}


# ---- the owner's old copies: parked, never deleted ----

def _park(conn, pid: str, done: dict) -> int:
    name = (config.profile(pid).get("name") or pid).split()[0]
    count = 0
    with accounts.as_owner():
        parked = gdrive_upload.folder(conn, ("job-hunt-agent", f"Old - {name} (moved)"))["id"]
        if accounts.expected_email(pid):                    # an old link anywhere still opens for the person
            gdrive_upload.share(conn, parked, accounts.expected_email(pid))
        for old_id, row in done.items():
            if row.get("parked_at"):
                continue
            try:
                parents = api.get(f"{base('drive')}/files/{old_id}", {"fields": "parents"}).get("parents") or []
                if parked not in parents:
                    api.call("PATCH", f"{base('drive')}/files/{old_id}", params={
                        "addParents": parked, "removeParents": ",".join(parents), "fields": "id"}, json_body={})
            except GoogleError as err:
                if err.status != 404:
                    raise
                log.warning("old copy %s is gone already", old_id)
            with db.tx(conn):
                conn.execute("UPDATE google_moves SET parked_at = ? WHERE old_id = ?", (db.iso(db.utcnow()), old_id))
            count += 1
    return count


def run(conn, pid: str, resumed: bool = False) -> dict:
    """Move everything; safe to run again (finished files are skipped)."""
    if not accounts.connected(pid):
        raise ValueError(f"{pid} has not connected their own Google account yet")
    with _RUNNING_LOCK:
        if pid in _RUNNING:
            return {"skipped": "a move is already running"}
        _RUNNING.add(pid)
    try:
        with _held(pid) as free:
            if not free:
                return {"skipped": "a move is already running (another process)"}
            return _run(conn, pid, f"{RESUMING}: " if resumed else "")
    finally:
        with _RUNNING_LOCK:
            _RUNNING.discard(pid)


def _run(conn, pid: str, lead: str) -> dict:
    def say(text: str) -> None:
        _note(conn, pid, "running", lead + text)

    say("copying your files to your Drive")
    done = _moved(conn, pid)
    files = _move_files(conn, pid, done)
    say(f"{files} resumes and Docs copied; writing your JD Docs")
    jd_docs = _move_jd_docs(conn, pid, done, say)
    resumegen = _relink_resumegen(pid, done)
    sheet = accounts.with_fallback(pid, relink_sheet, conn, pid, done)
    sheet_jd = dict(jd_doc.link_rows(conn, pid))
    parked = _park(conn, pid, _moved(conn, pid))
    summary = (f"{files} resumes and Docs and {jd_docs} JD Docs now in your Drive; {sheet} sheet links updated; "
               f"{parked} old copies parked")
    _note(conn, pid, "done", summary)
    return {"files": files, "jd_docs": jd_docs, "resumegen": resumegen, "sheet": sheet, "sheet_jd": sheet_jd,
            "parked": parked}
