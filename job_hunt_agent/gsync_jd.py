"""The JD cell in the Google Sheets (user decision 2026-10-06 evening, both sheets; replaces "URL + JD"): like the
person's older rows, one clipped line at normal height. The role title (or "JD ↗") linked to the posting, with the
first NOTE_CHARS characters of the JD as a hover note. The full text is in the hidden "JD Text" column the app adds at
the far right (synced both ways, read on the long-column schedule) and in the app (drawer, Copy JD).

apply() moves rows written in the "URL + JD" style to this format and resets their height; the person's own older
rows are left as they are. Uploaded resumes become Drive file chips (gsync_values.chip_cell).
"""
from . import gsync, jd_doc, jdsnap, resume_upload
from .google import api
from .gsync_values import CLIP, chip_cell, one_line, urls

NOTE_CHARS = 2000                        # the hover note: the start of the JD
NOTE_TAIL = " …"
ROW_PX = 21                              # Sheets' default row height (the older rows are 20-21 px)


def note_of(app: dict) -> str | None:
    text = (app.get("jd_text") or "").strip()
    if len(text) <= NOTE_CHARS:
        return text or None
    return text[:NOTE_CHARS - len(NOTE_TAIL)].rstrip() + NOTE_TAIL


def jd_cell(cell: dict, app: dict, doc_link: str | None = None) -> dict:
    """A JD cell holding one short line (the title): the whole line links to the row's JD Doc, else to
    the posting, clipped, with the JD as a note. Cells with JD text (the older rows) keep their text and own links;
    with a Doc, their first line without a link links to it."""
    text = (cell.get("userEnteredValue") or {}).get("stringValue") or ""
    link = doc_link or app.get("job_url") or app.get("apply_url")
    if doc_link and text and ("\n" in text or urls(text)):
        runs, _ = jd_doc.linked_runs(text, cell.get("textFormatRuns") or [], doc_link)
        return {**cell, "textFormatRuns": runs} if runs else cell
    if not text or "\n" in text or urls(text) or not link:
        return cell
    out = {**cell, "textFormatRuns": [{"startIndex": 0, "format": {"link": {"uri": link}}}],
           "userEnteredFormat": {**(cell.get("userEnteredFormat") or {}), **CLIP}}
    note = note_of(app)
    return {**out, "note": note} if note else out


def styled(header: str, cell: dict, app: dict, doc_link: str | None = None) -> dict:
    """The cell the sync writes (the JD column: jd_cell; others as to_cell made them)."""
    return jd_cell(cell, app, doc_link) if header == "JD" else cell


def note_request(target: gsync.Target, index: int, app: dict) -> dict | None:
    """The JD cell's note again, after its JD Text changed (only the note is written)."""
    if "JD" not in target.headers or not (app.get("job_url") or app.get("apply_url")):
        return None
    return {"updateCells": {"start": {"sheetId": target.sheet_id, "rowIndex": index,
                                      "columnIndex": target.headers.index("JD")},
                            "rows": [{"values": [{"note": note_of(app) or ""}]}], "fields": "note"}}


def height_requests(target: gsync.Target, indexes: list[int]) -> list[dict]:
    return [{"updateDimensionProperties": {"range": {"sheetId": target.sheet_id, "dimension": "ROWS",
                                                     "startIndex": i, "endIndex": i + 1},
                                           "properties": {"pixelSize": ROW_PX}, "fields": "pixelSize"}}
            for i in indexes]


def _column_requests(target: gsync.Target) -> list[dict]:
    at = len(target.headers)
    grow = [{"appendDimension": {"sheetId": target.sheet_id, "dimension": "COLUMNS", "length": at + 1 - target.cols}}]
    span = {"sheetId": target.sheet_id, "startIndex": at, "endIndex": at + 1}
    return [*(grow if at >= target.cols else []),
            gsync.write_cell(target, 0, at, {"userEnteredValue": {"stringValue": gsync.JD_TEXT_HEADER}}),
            {"updateDimensionProperties": {"range": {**span, "dimension": "COLUMNS"},
                                           "properties": {"hiddenByUser": True}, "fields": "hiddenByUser"}},
            {"repeatCell": {"range": {"sheetId": target.sheet_id, "startColumnIndex": at, "endColumnIndex": at + 1},
                            "cell": {"userEnteredFormat": CLIP}, "fields": "userEnteredFormat.wrapStrategy"}}]


def add_columns(conn, pid: str) -> list[str]:
    """The hidden "JD Text" column right of the last header of each application tab (once). Returns the tabs
    that got it."""
    added = []
    for target in gsync.targets(conn, pid):
        if gsync.JD_TEXT_HEADER in target.headers:
            continue
        gsync.batch(target.sid, _column_requests(target))
        added.append(target.title)
    return added


def rows_of(target: gsync.Target, app_ids: set[int]) -> dict[int, int]:
    """app id -> grid row, from the key column."""
    col = gsync.letter(target.key_col)
    keys = gsync.get_values(target.sid, [f"{gsync.quoted(target.title)}!{col}1:{col}"])[0]
    found = {gsync.app_id_of(str(k[0]).strip()): i for i, k in enumerate(keys) if k}
    return {a: i for a, i in found.items() if a in app_ids}


def old_style(conn, pid: str) -> list[int]:
    """Rows whose JD cell is in the "URL + JD" style (the app wrote it)."""
    return [r["id"] for r in conn.execute("SELECT id, jd FROM applications WHERE profile = ?", (pid,))
            if jdsnap.URL_PLUS_JD.match((r["jd"] or "").strip())]


def _resume_cells(target: gsync.Target, col: int) -> list[dict]:
    a1 = f"{gsync.quoted(target.title)}!{gsync.letter(col)}1:{gsync.letter(col)}"
    got = api.get(gsync.sheets_url(target.sid), {
        "ranges": a1, "fields": "sheets(data(rowData(values(userEnteredValue,formattedValue,hyperlink,"
                                "textFormatRuns,chipRuns))))"})
    rows = [row for sheet in got.get("sheets", []) for block in sheet.get("data", [])
            for row in block.get("rowData", [])]
    return [(row.get("values") or [{}])[0] for row in rows]


def chip_resumes(conn, pid: str, app_ids: set[int] | None = None) -> list[int]:
    """Resume cells holding uploaded files as links (written before chips) become Drive file chips. Only rows with
    an upload; a cell with a formula is left alone. Returns the app ids changed."""
    uploaded = {r["app_id"] for r in conn.execute("SELECT app_id FROM resume_uploads WHERE profile = ?", (pid,))}
    wanted, done = uploaded if app_ids is None else uploaded & app_ids, []
    for target in gsync.targets(conn, pid):
        found = [(c, k) for c, k in target.synced if k.field == resume_upload.RESUME_FIELD]
        if not found or target.key_col is None or not wanted:
            continue
        (col, column), cells, changed = found[0], _resume_cells(target, found[0][0]), []
        for app_id, index in rows_of(target, wanted).items():
            cell = cells[index] if index < len(cells) else {}
            if "formulaValue" in (cell.get("userEnteredValue") or {}) or not cell.get("textFormatRuns"):
                continue
            new = chip_cell(cell)
            if new["chipRuns"]:
                changed.append((app_id, gsync.write_cell(target, index, col, new),
                                one_line(cell.get("formattedValue") or "")))
        gsync.batch(target.sid, [request for _, request, _ in changed])
        for app_id, _, text in changed:                      # the app shows what the Sheet now shows
            app = dict(conn.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone())
            resume_upload.record_pull(conn, pid, target, app, column, text)
            done.append(app_id)
    return done


def apply(conn, pid: str, app_ids: set[int] | None = None) -> dict:
    """Add the JD Text column, move the "URL + JD" rows (all, or only `app_ids`) to the title-link format, write
    them (one sync, long columns read), reset their height, and make uploaded resumes chips."""
    from .gsync_cycle import sync_profile                    # late: gsync_cycle imports this module
    added = add_columns(conn, pid)
    rows = [a for a in old_style(conn, pid) if app_ids is None or a in app_ids]
    converted = [a for a in rows if jdsnap.fill_cell(conn, a)]
    sync_profile(conn, pid, full=True)
    for target in gsync.targets(conn, pid):
        if target.key_col is not None and converted:
            gsync.batch(target.sid, height_requests(target, list(rows_of(target, set(converted)).values())))
    return {"columns_added": added, "converted": converted, "chipped": chip_resumes(conn, pid, app_ids)}
