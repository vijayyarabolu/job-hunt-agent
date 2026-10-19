"""Tracker "Upload resume (PDF/DOCX)" (GOAL4 §4 addition, decision 008): the file goes to the owner's Drive in
job-hunt-agent/Resumes/<Profile>/ under the person's naming pattern, and the row's Resume cell in the real Sheet gains a
clickable link to it. Whatever text and links the cell already had stay; the new file name is added on its own line.

Never deletes or overwrites: every upload is a new Drive file and a new resume_uploads row (version 1, 2, ...;
v2+ get a _v<n> suffix). A row not on the Sheet yet keeps the name in its Resume field and is written with the link
when the sync appends it (gsync_cycle.append).
with replace=True (Replace ▾, a picked resumegen version, an Approve) the row's old files retire first
(resume_swap: moved to Resumes/<Profile>/Replaced/, Undo) and the Resume field becomes just the new name, which the
next sync writes as that file's chip. Only current files (not removed) count as the row's chips.
"""
import hashlib
import io
import logging
import re
import threading
import zipfile

from . import config, db, gdrive_upload, gsync, resume_swap, resumes, trackapi
from . import gsync_pull as pull
from .google import DOCX, PDF, GoogleError, accounts, api
from .google import state as gstate
from .gsync_values import CLIP, FILE_GAP, canon, chip_cell, fields, one_line, to_cell

log = logging.getLogger(__name__)
MAX_BYTES = 10 * 1024 * 1024
KINDS = {".pdf": PDF, ".docx": DOCX}
TITLE_CHARS = 60
LOCK_WAIT_S = 60
RESUME_FIELD = "resume"
_NOT_WORD = re.compile(r"[^A-Za-z0-9]+")
_UPLOADING = threading.Lock()                # one upload at a time: version numbers never collide


def check_file(original: str, data: bytes) -> tuple[str, str]:
    """(extension, mime) of an accepted upload; ValueError with a plain reason otherwise."""
    ext = ("." + original.rsplit(".", 1)[-1].lower()) if "." in (original or "") else ""
    if ext not in KINDS:
        raise ValueError("only PDF or DOCX files can be uploaded")
    if not data:
        raise ValueError("the file is empty")
    if len(data) > MAX_BYTES:
        raise ValueError(f"the file is over {MAX_BYTES // (1024 * 1024)} MB")
    if ext == ".pdf" and not data.startswith(b"%PDF-"):
        raise ValueError("that file is not a PDF")
    if ext == ".docx" and not _is_docx(data):
        raise ValueError("that file is not a Word .docx document")
    return ext, KINDS[ext]


def _is_docx(data: bytes) -> bool:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            return "word/document.xml" in archive.namelist()
    except zipfile.BadZipFile:
        return False


def _slug(text: str) -> str:
    return _NOT_WORD.sub("_", text).strip("_")[:TITLE_CHARS].strip("_")


def upload_name(profile: dict, app: dict, ext: str) -> str:
    """The name a job site gets: the plain base resume name, never the company and never _v2 (user 2026-10-14),
    e.g. Alex_Rivera_ML_Engineer_Resume.pdf for an ML role (the title follows the role). The tracker / Drive copy keeps "(Company)" (file_name)."""
    base = resumes.base_file_name(profile, app.get("resume_used"), "", role=app.get("role_title"))
    if base:
        return f"{base}{ext}"
    title = _slug(app.get("role_title") or "") or _slug(str(profile.get("headline_default") or "Resume"))
    return f"{_slug(str(profile.get('name') or profile['id']))}_{title}_Resume{ext}"


def file_name(profile: dict, app: dict, ext: str, version: int) -> str:
    """The person's pattern with the row's role title, e.g. Alex_Rivera_ML_Engineer_Resume(Acme).pdf;
    re-uploads get _v2, _v3, ..."""
    base = resumes.base_file_name(profile, app.get("resume_used"), "", role=app.get("role_title"))
    if base:                                        # base name + (Company) (user, 2026-10-08)
        firm = resumes._UNSAFE_FILE_CHARS.sub(" ", (app.get("company") or "").strip()).strip()
        return f"{base}{f'({firm})' if firm else ''}{f'_v{version}' if version > 1 else ''}{ext}"
    pattern = str(profile.get("resume_file_pattern") or resumes.DEFAULT_FILE_PATTERN)
    title = _slug(app.get("role_title") or "") or _slug(str(profile.get("headline_default") or "Resume"))
    name = _slug(str(profile.get("name") or profile["id"]))
    company = resumes._UNSAFE_FILE_CHARS.sub(" ", (app.get("company") or "").strip()).strip()
    try:
        stem = pattern.format(name=name, title=title, company=company)
    except (KeyError, IndexError, ValueError) as err:
        raise ValueError(f"resume_file_pattern {pattern!r} in profile.yaml: use only {{name}}, {{title}}, "
                         f"{{company}} ({err})") from None
    stem = stem[:-4] if stem.lower().endswith(".pdf") else stem
    return f"{stem}{f'_v{version}' if version > 1 else ''}{ext}"


def merged_cell(existing: dict, name: str, link: str) -> dict:
    """The Resume cell with the uploaded file added as a Drive file chip on a new line (like the person's older
    rows), keeping the cell's text, its chips and links; a Drive file linked before becomes a chip too."""
    return chip_cell(existing, [(name, link)])


def shown_text(existing: dict, name: str) -> str:
    """What the merged cell shows (and a sync read returns): the old text on one line, then the chip's file name."""
    before = existing.get("formattedValue") or (existing.get("userEnteredValue") or {}).get("stringValue") or ""
    return f"{one_line(before)}{FILE_GAP}{name}" if before else name


def _read_cell(target: gsync.Target, index: int, col: int) -> dict:
    a1 = f"{gsync.quoted(target.title)}!{gsync.letter(col)}{index + 1}"
    got = api.get(gsync.sheets_url(target.sid), {
        "ranges": a1, "fields": "sheets(data(rowData(values(userEnteredValue,formattedValue,hyperlink,textFormatRuns,chipRuns))))"})
    for sheet in got.get("sheets", []):
        for block in sheet.get("data", []):
            for row in block.get("rowData", []):
                return (row.get("values") or [{}])[0]
    return {}


def _row_index(target: gsync.Target, app_id: int) -> int | None:
    col = gsync.letter(target.key_col)
    keys = gsync.get_values(target.sid, [f"{gsync.quoted(target.title)}!{col}1:{col}"])[0]
    wanted = f"{gsync.KEY_PREFIX}{app_id}"
    return next((i for i, row in enumerate(keys) if row and str(row[0]) == wanted), None)


def _write(target: gsync.Target, index: int, col: int, cell: dict) -> None:
    gsync.batch(target.sid, [{"updateCells": {
        "start": {"sheetId": target.sheet_id, "rowIndex": index, "columnIndex": col},
        "rows": [{"values": [cell]}], "fields": fields(False, cell)}}])


def _sheet_tab(conn, pid: str, app_id: int) -> str | None:
    row = conn.execute("SELECT tab FROM gsync_rows WHERE profile = ? AND app_id = ? AND missing_since IS NULL",
                       (pid, app_id)).fetchone()
    return row["tab"] if row else None


def link_in_sheet(conn, pid: str, app: dict, name: str, link: str) -> str:
    """Add the linked name to the row's Resume cell (under the sync lock, so no cycle runs in between), then record
    it in the app as a pulled cell. Returns where it went."""
    current = (app.get(RESUME_FIELD) or "").strip()
    return _in_sheet(conn, pid, app, f"{current}{FILE_GAP}{name}" if current else name,
                     lambda before: (merged_cell(before, name, link), shown_text(before, name)))


def rewrite(conn, pid: str, app_id: int, text: str) -> str:
    """the Resume cell becomes exactly `text`, each current file it names a Drive chip, written now: a
    replacement often keeps the old file's name, and the sync would see no change and leave the chip on the old
    file. If the Sheet cannot be written now, the field changes and the next sync carries it."""
    app = trackapi._own(conn, pid, app_id)
    plain = {"userEnteredValue": {"stringValue": text} if text else {}}
    cell = {"chipRuns": [], "textFormatRuns": [], "userEnteredFormat": CLIP, **upload_chips(conn, app_id, plain)}
    try:
        return _in_sheet(conn, pid, app, text, lambda before: (cell, text))
    except (GoogleError, ValueError, StopIteration) as err:
        trackapi.update(conn, pid, app_id, {RESUME_FIELD: text})
        return f"the Sheet follows on the next sync ({str(err) or 'no Resume column'})"


def sheet_cell(conn, pid: str, app_id: int) -> dict | None:
    """the row's Resume cell as the Sheet has it now, chips and links included (kept for Undo); None off
    the Sheet, for a formula, or when it cannot be read."""
    tab = _sheet_tab(conn, pid, app_id) if gsync.linked_sid(conn, pid) else None
    if tab is None:
        return None
    try:
        target = next(t for t in gsync.targets(conn, pid) if t.title == tab)
        col = next(c for c, k in target.synced if k.field == RESUME_FIELD)
        index = _row_index(target, app_id)
        cell = None if index is None else _read_cell(target, index, col)
    except (GoogleError, StopIteration) as err:
        log.warning("%s: row %s's Resume cell not read for Undo: %s", pid, app_id, err)
        return None
    return None if cell is None or "formulaValue" in (cell.get("userEnteredValue") or {}) else cell


def as_written(cell: dict) -> dict:
    """A cell as read, put back by a write: chips through chip_cell (as every chip write), else its text with its
    link runs or whole-cell link, one clipped line."""
    if any("chip" in r for r in cell.get("chipRuns") or []):
        return chip_cell(cell)
    text = (cell.get("userEnteredValue") or {}).get("stringValue") or cell.get("formattedValue") or ""
    runs = [{"startIndex": r.get("startIndex", 0), "format": {"link": {"uri": r["format"]["link"]["uri"]}}
             if ((r.get("format") or {}).get("link") or {}).get("uri") else {}} for r in cell.get("textFormatRuns") or []]
    if not runs and cell.get("hyperlink") and text:
        runs = [{"startIndex": 0, "format": {"link": {"uri": cell["hyperlink"]}}}]
    return {"userEnteredValue": {"stringValue": text} if text else {}, "textFormatRuns": runs, "chipRuns": [],
            "userEnteredFormat": CLIP}


def put_back(conn, pid: str, app_id: int, cell: dict, text: str) -> str:
    """Undo: the Resume cell exactly as it was (its text, chips and links), the field with it."""
    restored = as_written(cell)
    shown = cell.get("formattedValue") or (restored["userEnteredValue"].get("stringValue") or "")
    try:
        return _in_sheet(conn, pid, trackapi._own(conn, pid, app_id), shown, lambda before: (restored, shown))
    except (GoogleError, ValueError, StopIteration) as err:
        trackapi.update(conn, pid, app_id, {RESUME_FIELD: text})
        return f"the Sheet follows on the next sync ({str(err) or 'no Resume column'})"


def _in_sheet(conn, pid: str, app: dict, text_off_sheet: str, make) -> str:
    tab = _sheet_tab(conn, pid, app["id"]) if gsync.linked_sid(conn, pid) else None
    if tab is None:
        trackapi.update(conn, pid, app["id"], {RESUME_FIELD: text_off_sheet})
        return "not on the Sheet yet: the name is in the Resume field and the link is added when the row is written"
    # on the person's own token (their Sheet; a chip needs a writer who can see the file, and the owner's
    # token cannot see a file in the person's own Drive), else the owner's as before
    return accounts.with_fallback(pid, _in_tab, conn, pid, app, tab, make)


def _in_tab(conn, pid: str, app: dict, tab: str, make) -> str:
    with gstate.lock(wait_s=LOCK_WAIT_S) as held:
        if not held:
            raise ValueError("the Sheet sync is busy: the file is uploaded; try linking it again in a minute")
        target = next(t for t in gsync.targets(conn, pid) if t.title == tab)
        col, column = next((c, k) for c, k in target.synced if k.field == RESUME_FIELD)
        index, text = _link_cell(conn, target, app["id"], col, make)
        record_pull(conn, pid, target, app, column, text)
    return f"{tab} row {index + 1}, column {gsync.letter(col)}"


def _link_cell(conn, target: gsync.Target, app_id: int, col: int, make) -> tuple[int, str]:
    """Read the row's cell, write make(cell) -> (new cell, its text); read the key column again in case a sort moved
    the row meanwhile (the window is about a second). Returns (row index, new cell text)."""
    for _ in range(2):
        index = _row_index(target, app_id)
        if index is None:
            raise ValueError(f"row #{app_id} is not in {target.title} any more: the file is uploaded, the cell is "
                             "not linked")
        before = _read_cell(target, index, col)
        if "formulaValue" in (before.get("userEnteredValue") or {}):
            raise ValueError("the Resume cell holds a formula, left as it is: the file is uploaded, add its link by "
                             "hand")
        cell, text = make(before)
        _write(target, index, col, cell)
        moved = _row_index(target, app_id)
        if moved == index or (moved is not None and _read_cell(target, moved, col).get("formattedValue") == text):
            return moved, text                                # landed on our row (it may have moved since)
        _restore_other(conn, target, index, col)              # the write hit another row: give it its text back
    raise ValueError("the Sheet kept being re-sorted: the file is uploaded; link it again")


def _restore_other(conn, target: gsync.Target, index: int, col: int) -> None:
    """The row now at `index` got our merged cell: write back its last synced value (as plain text)."""
    key = gsync.get_values(target.sid, [f"{gsync.quoted(target.title)}!{gsync.letter(target.key_col)}{index + 1}"])[0]
    other = gsync.app_id_of(str(key[0][0])) if key and key[0] else None
    header = target.headers[col]
    value = gsync.snapshots(conn, target.pid, target.title).get(other, {}).get(header, [""])[0] if other else ""
    _write(target, index, col, {**to_cell(value or None, True, header=header, restore=True), "textFormatRuns": []})
    log.warning("%s/%s: a sort raced the resume link; row %d's %s restored", target.pid, target.title, index + 1,
                header)


def record_pull(conn, pid: str, target: gsync.Target, app: dict, column, text: str) -> None:
    """The app takes the cell exactly as a sync pull would, and the stored pair says both sides agree."""
    pull.apply(conn, app, column, text, target.mapped)
    snaps = gsync.snapshots(conn, pid, target.title).get(app["id"], {})
    shown = canon(text, column.header)
    gsync.save_snapshot(conn, pid, target.title, app["id"], {**snaps, column.header: [shown, shown]})
    gsync.log_event(conn, pid, "resume_link", target.title, app["id"], column.header, shown, shown)
    conn.commit()


def _next_version(conn, app_id: int) -> int:
    row = conn.execute("SELECT max(version) AS v FROM resume_uploads WHERE app_id = ?", (app_id,)).fetchone()
    return (row["v"] or 0) + 1


def _record(conn, pid: str, app_id: int, version: int, original: str, made: dict, data: bytes, mime: str,
            folder_id: str) -> int:
    with db.tx(conn):
        cur = conn.execute(
            "INSERT INTO resume_uploads(profile, app_id, version, name, original_name, mime, bytes, md5, file_id, "
            "link, folder_id, uploaded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (pid, app_id, version, made["name"], original[:200], mime, len(data), hashlib.md5(data).hexdigest(),
             made["id"], made["webViewLink"], folder_id, db.iso(db.utcnow())))
    return cur.lastrowid


def _set_sheet_note(conn, upload_id: int, note: str) -> None:
    with db.tx(conn):
        conn.execute("UPDATE resume_uploads SET sheet = ? WHERE id = ?", (note[:300], upload_id))


def upload(conn, pid: str, app_id: int, original: str, data: bytes, replace: bool = False) -> dict:
    """Upload one resume file for one of the profile's applications; returns what happened, in plain words."""
    with _UPLOADING:                                      # into the person's own Drive when connected
        made = accounts.with_fallback(pid, _upload, conn, pid, app_id, original, data, replace)
    return {**made, "row": trackapi.get(conn, pid, app_id)}


def _upload(conn, pid: str, app_id: int, original: str, data: bytes, replace: bool) -> dict:
    app = trackapi._own(conn, pid, app_id)
    ext, mime = check_file(original, data)
    profile = {**config.profile(pid), "id": pid}
    version = _next_version(conn, app_id)
    name = file_name(profile, app, ext, version)
    try:
        folder = gdrive_upload.profile_folder(conn, pid)
        made = gdrive_upload.upload(folder["id"], name, data, mime, {"jha_app": app_id, "profile": pid,
                                                                     "version": version})
    except GoogleError as err:
        raise ValueError(f"Google Drive: {err}") from err
    share_with, shared = folder["shared_with"], folder["newly_shared"]
    retired = resume_swap.retire(conn, pid, app) if replace else None
    upload_id = _record(conn, pid, app_id, version, original, made, data, mime, folder["id"])
    with db.tx(conn):                                     # the "resume missing" chip goes away
        conn.execute("UPDATE applications SET resume_missing = 0 WHERE id = ?", (app_id,))
    swapped = _linked(conn, pid, app, upload_id, made["name"], made["webViewLink"], retired)
    return {"name": made["name"], "link": made["webViewLink"], "version": version, "folder": folder.get("webViewLink"),
            "shared_with": share_with, "newly_shared": shared, **swapped}


def _linked(conn, pid: str, app: dict, upload_id: int, name: str, link: str, retired: dict | None) -> dict:
    """{'sheet': where the link went (+ 'undo' after a replacement)}: a replacement is the row's only file, the Sheet
    follows on the next sync; otherwise the name is added to the cell now."""
    if retired is not None:
        swapped = resume_swap.finish(conn, pid, app["id"], retired, upload_id, name)
    else:
        try:
            swapped = {"sheet": link_in_sheet(conn, pid, app, name, link)}
        except (GoogleError, ValueError, StopIteration) as err:
            swapped = {"sheet": f"not linked in the Sheet: {str(err) or 'no Resume column'}"}
    _set_sheet_note(conn, upload_id, swapped["sheet"])
    return swapped


def link_existing(conn, pid: str, app_id: int, name: str, file_id: str, link: str, replace: bool = False) -> dict:
    """a resume already in Drive (approved in resumegen, never re-uploaded) becomes this row's file: a
    resume_uploads row (so a new Sheet row gets its chip) and the Resume cell's chip when the row is on the Sheet."""
    if not (name and file_id and link):
        raise ValueError("the approved resume has no Drive file")
    with _UPLOADING:
        app = trackapi._own(conn, pid, app_id)
        version = _next_version(conn, app_id)
        retired = resume_swap.retire(conn, pid, app) if replace else None
        with db.tx(conn):
            cur = conn.execute(
                "INSERT INTO resume_uploads(profile, app_id, version, name, original_name, mime, bytes, md5, file_id, "
                "link, folder_id, uploaded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (pid, app_id, version, name, "resumegen (approved)", PDF, 0, "", file_id, link, None,
                 db.iso(db.utcnow())))
            conn.execute("UPDATE applications SET resume_missing = 0 WHERE id = ?", (app_id,))
        swapped = _linked(conn, pid, app, cur.lastrowid, name, link, retired)
    return {"name": name, "link": link, "version": version, **swapped}


def upload_chips(conn, app_id: int, cell: dict) -> dict:
    """A new row's Resume cell: each file uploaded for this row that the cell names becomes that file's Drive chip."""
    files = {r["name"]: r["link"] for r in conn.execute("SELECT name, link FROM resume_uploads WHERE app_id = ? "
                                                        "AND removed_at IS NULL", (app_id,))}
    text = (cell.get("userEnteredValue") or {}).get("stringValue") or ""
    found = sorted((text.find(name), name) for name in files if name and name in text)
    if not found:
        return cell
    runs, end = [], 0
    for at, name in found:
        if at < end:                                          # a name inside another one: already covered
            continue
        runs += [{"startIndex": _units(text[:at]), "format": {"link": {"uri": files[name]}}},
                 {"startIndex": _units(text[:at + len(name)]), "format": {}}]
        end = at + len(name)
    return chip_cell({"userEnteredValue": {"stringValue": text}, "textFormatRuns": runs})


def _units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2                 # Sheets run indexes count UTF-16 units
