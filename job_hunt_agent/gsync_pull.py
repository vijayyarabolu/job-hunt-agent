"""Sheet -> DB for the two-way sync (decision 008): one edited cell at a time, and rows typed straight into
the Sheet. A pulled cell is "re-imported": an imported row's typed cell (raw_json) takes the Sheet's value and the
field stops being an app edit, exactly what `job-hunt-agent tracker import` of the same sheet would do; a row the app made
gets the value in its column. Pulls are never stamped as app edits, so last-writer-wins keeps the app's own times.
"""
import json

from . import contacts, db, history, tracker, tracker_layout
from .gsync_values import text_of, to_db
from .tracker import COLUMNS, FIELDS

STATUS_FIELD = "status_ar"


def _raw(app: dict) -> list | None:
    return json.loads(app["raw_json"]) if app.get("raw_json") else None


def _tag(raw: list, col: str, value) -> list:
    """raw_json with the 'col'-tagged cell set to value (added when missing, dropped when emptied)."""
    kept = [c for c in raw if not (isinstance(c, dict) and c.get("col") == col)]
    return kept if value is None else [*kept, {**tracker.encode(value), "col": col}]


def _not_edited(app: dict, field: str) -> str:
    return json.dumps(sorted(set(json.loads(app.get("edited") or "[]")) - {field}))


def _update(conn, app_id: int, sets: dict) -> None:
    with db.tx(conn):
        conn.execute(f"UPDATE applications SET {', '.join(f'{k} = ?' for k in sets)}, updated_at = ? WHERE id = ?",
                     [*sets.values(), db.iso(db.utcnow()), app_id])


def _pipeline(app: dict, sets: dict) -> str | None:
    """The pipeline status a pulled Status(A/R) cell asks for: the status typed, or (cell cleared on the Sheet) the
    default one, so the app shows the blank cell the Sheet has (the Sheet wins)."""
    if sets[STATUS_FIELD] in (None, ""):
        return tracker.default_status(sets.get("applied_on", app.get("applied_on")))
    return tracker._sheet_status(sets[STATUS_FIELD])


def _field(conn, app: dict, field: str, value) -> None:
    """One of the 14 tracker fields."""
    raw = _raw(app)
    sets = {field: text_of(value)}
    if raw:                                                   # an imported row: its typed cell is the record
        raw = list(raw)
        raw[FIELDS.index(field)] = tracker.encode(value)
        sets.update(raw_json=json.dumps(raw), edited=_not_edited(app, field))
    if field == "date_applied":
        sets["applied_on"] = tracker.parse_applied(value)
    if field == STATUS_FIELD:
        sets["sheet_status"] = tracker._sheet_status(text_of(value)) or ""
    _update(conn, app["id"], sets)
    wanted = _pipeline(app, sets) if field == STATUS_FIELD else None
    if wanted and wanted != app["status"]:                    # "Rejected" typed in Status(A/R) moves the pipeline
        tracker.set_status(conn, app["id"], wanted, source="sheet")
    if field == "jd":                                         # a JD typed in the Sheet is kept as text
        from . import jdsnap                                  # late: jdsnap imports tracker
        jdsnap.snapshot(conn, app["id"])
    if field in contacts.LEGACY:
        edited_in_app = conn.execute("SELECT 1 FROM contacts WHERE app_id = ? AND origin = 'app' LIMIT 1",
                                     (app["id"],)).fetchone()
        if edited_in_app:                                     # the typed cell spreads over the app's contacts
            with history.action(history.SHEET, "sheet"):
                contacts.set_packed(conn, app["id"], field, text_of(value))
        else:
            contacts.refresh_legacy(conn, [app["id"]])


def _extra(conn, app: dict, column: tracker_layout.Column, value, mapped: bool) -> None:
    """Address / Team Name / JD Text: the column, plus (mapped profiles) the imported cell the export reads."""
    sets = {column.field: text_of(value)}
    raw = _raw(app)
    if mapped and raw is not None and column != tracker_layout.JD_TEXT:     # JD Text is the app's own column
        sets.update(raw_json=json.dumps(_tag(raw, column.target or column.header, value)),
                    edited=_not_edited(app, column.field))
    _update(conn, app["id"], sets)


def _passthrough(conn, app: dict, header: str, value) -> None:
    """A column with no app field (Jordan's "Column 1"): kept as the imported cell."""
    raw = _tag(_raw(app) or [tracker.encode(None)] * len(FIELDS), header, value)
    _update(conn, app["id"], {"raw_json": json.dumps(raw)})


def _current(app: dict, column: tracker_layout.Column):
    if column.field:
        return app.get(column.field)
    tagged = next((c for c in (_raw(app) or []) if isinstance(c, dict) and c.get("col") == column.header), None)
    return text_of(tracker.decode(tagged)) if tagged else None


def apply(conn, app: dict, column: tracker_layout.Column, raw_value, mapped: bool) -> None:
    """Write one Sheet cell into the DB (and its edit history: who = the Google Sheet)."""
    value = to_db(raw_value, column.header)
    if column.field in FIELDS:
        _field(conn, app, column.field, value)
    elif column.field:
        _extra(conn, app, column, value, mapped)
    else:
        _passthrough(conn, app, column.header, value)
    after = dict(conn.execute("SELECT * FROM applications WHERE id = ?", (app["id"],)).fetchone())
    with db.tx(conn):
        history.record(conn, app["id"], column.field or f"sheet:{column.header}", _current(app, column),
                       _current(after, column), "sheet")


def is_application(values: dict) -> bool:
    """A typed row the app takes in: it names a company or a role (date-only placeholder rows are skipped)."""
    return any(str(values.get(h) or "").strip() for h in ("Company Name", "Role Title"))


def create(conn, profile: str, tab: tracker_layout.Tab, row_values: dict, xlsx_row: int) -> int:
    """A row typed into the Sheet becomes an application, stored as the tracker import stores a row (typed cells
    in raw_json, so the next push writes back the very same cells). Returns its id."""
    by_target = {(c.target or c.header): c for c in tab.columns}
    values = [to_db(row_values.get(by_target[c].header), c) if c in by_target else None for c in COLUMNS]
    tagged = [tracker.encode(v) for v in values]
    for column in tab.columns:
        if column.field in FIELDS:
            continue
        value = to_db(row_values.get(column.header), column.header)
        if value is not None:
            tagged.append({**tracker.encode(value), "col": column.target or column.header})
    key = tracker.import_key(profile, *values[:3])
    row = tracker.SheetRow(xlsx_row, values, tagged, None, None, key)
    text = dict(zip(FIELDS, (tracker.as_text(v) for v in values)))
    stale = tracker._stale(tracker.parse_applied(values[2]), db.utcnow().date())
    before = conn.execute("SELECT coalesce(max(id), 0) FROM applications").fetchone()[0]
    with db.tx(conn):
        tracker._insert(conn, profile, row, text, stale)
        app_id = conn.execute("SELECT max(id) FROM applications WHERE profile = ? AND id > ?",
                              (profile, before)).fetchone()[0]
        conn.execute("UPDATE applications SET source = 'sheet' WHERE id = ?", (app_id,))
    contacts.refresh_legacy(conn, [app_id])
    from . import jdsnap                                      # late: jdsnap imports tracker
    jdsnap.snapshot(conn, app_id)                             # the JD typed with the row is kept as text
    return app_id
