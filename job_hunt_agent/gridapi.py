"""The Sheets-like Tracker grid's server side: many cells written in one request (a paste, a fill, a
range cleared, an undo) with the values they replaced, so the page can undo and redo; a blank row inserted to type
into; and the Sheet conflicts of the last days, flagged on their cells.

Each row's changes go through trackapi.update exactly like a single cell edit (history, the Sheet marked dirty, the
stale-resume guard); a row that refuses its changes is reported and left as it was, the others are written."""
from datetime import date, timedelta

from . import contacts, db, tracker, trackapi
from .google import state as gstate

MAX_EDITS = 5000                       # cells in one request (a paste of 200 rows x 25 columns)
CONFLICT_DAYS = 7


def value_of(view: dict, field: str):
    """The full value a field holds in a row view (get()), as the page would write it back."""
    if field.startswith(trackapi.SHEET_ONLY):
        sheet = view.get("sheet") or {}
        headers = [c["header"] for c in sheet.get("columns") or []]
        header = field[len(trackapi.SHEET_ONLY):]
        return sheet["cells"][headers.index(header)] if header in headers else None
    return view.get(field)


def _changes(edits) -> dict[int, dict]:
    """{app id: {field: value}} out of [{app, changes}], later edits of the same cell winning."""
    if not isinstance(edits, list) or not edits:
        raise ValueError("edits must be a non-empty list")
    by_app: dict[int, dict] = {}
    for edit in edits:
        if not isinstance(edit, dict) or not isinstance(edit.get("changes"), dict):
            raise ValueError("each edit needs an app and its changes")
        by_app.setdefault(int(edit["app"]), {}).update(edit["changes"])
    if sum(len(c) for c in by_app.values()) > MAX_EDITS:
        raise ValueError(f"at most {MAX_EDITS} cells at once")
    return by_app


def update_many(conn, pid: str, edits, today: date | None = None, guard: bool = True) -> dict:
    """Write [{app, changes}] -> {"rows": fresh list rows, "before": [{app, field, value}], "errors": [{app, error}]}.
    `guard`: the page's own writes pass the stale-resume check; an undo of the page's own edit does too."""
    rows, before, errors = [], [], []
    for app_id, changes in _changes(edits).items():
        try:
            old = trackapi.get(conn, pid, app_id, today)
            if guard:
                trackapi.not_stale_resume(conn, pid, app_id, changes)
            trackapi.update(conn, pid, app_id, changes, today)
        except KeyError:
            errors.append({"app": app_id, "error": "no such row (deleted?)"})
            continue
        except ValueError as err:
            errors.append({"app": app_id, "error": str(err)})
            continue
        before += [{"app": app_id, "field": f, "value": value_of(old, f)} for f in changes]
        rows.append(app_id)
    return {"rows": view_rows(conn, pid, rows, today), "before": before, "errors": errors}


def view_rows(conn, pid: str, ids: list[int], today: date | None = None) -> list[dict]:
    """These rows as the tracker list sends them (compact, with their conflicts)."""
    if not ids:
        return []
    today, layout = today or date.today(), trackapi._layout(pid)
    marks = ",".join("?" * len(ids))
    found = [dict(r) for r in conn.execute(f"{trackapi._ROW_SQL} AND a.id IN ({marks})", [pid, *ids])]
    people = contacts.for_apps(conn, [r["id"] for r in found])
    flags = conflicts(conn, pid, [r["id"] for r in found])
    order = {app_id: i for i, app_id in enumerate(ids)}
    from .h1b.feed import app_views                             # late: h1b imports config users
    spons = app_views(conn, pid, found)                         # GOAL24 §3
    views = [{**trackapi.compact(trackapi.view(r, people[r["id"]], today, layout)), "conflicts": flags.get(r["id"]),
              "sponsor": spons.get(r["id"])} for r in found]
    return sorted(views, key=lambda v: order[v["id"]])


def conflicts(conn, pid: str, ids: list[int], days: int = CONFLICT_DAYS) -> dict[int, dict]:
    """{app id: {Sheet header: {winner, sheet, app, at}}}: the newest conflict per cell in the last `days` (both
    sides changed the cell between two syncs; the newer edit won and the other value is shown on the cell)."""
    if not ids:
        return {}
    since = db.iso(db.utcnow() - timedelta(days=days))
    marks = ",".join("?" * len(ids))
    out: dict[int, dict] = {}
    for r in conn.execute(f"SELECT app_id, column_name, sheet_value, app_value, winner, at FROM gsync_log "
                          f"WHERE kind = 'conflict' AND profile = ? AND at >= ? AND app_id IN ({marks}) ORDER BY id",
                          [pid, since, *ids]):
        out.setdefault(r["app_id"], {})[r["column_name"]] = {"winner": r["winner"], "sheet": r["sheet_value"],
                                                             "app": r["app_value"], "at": r["at"]}
    return out


def insert_blank(conn, pid: str, status: str = "applied", today: date | None = None) -> dict:
    """A blank row to type into (right-click → Insert row). It stays in the app until it names a company or a role;
    then the sync adds it to the Sheet like any row (at the end: the Sheet's own order is the person's)."""
    if status not in trackapi.UI_STATUSES:
        raise ValueError(f"status must be one of {trackapi.UI_STATUSES}")
    tracker.ensure_profile(conn, pid)
    now = db.iso(db.utcnow())
    with db.tx(conn):
        cur = conn.execute("INSERT INTO applications(profile, status, source, updated_at) VALUES (?,?,?,?)",
                           (pid, status, "manual", now))
        conn.execute("INSERT INTO application_events(app_id, at, from_status, to_status, source) VALUES (?,?,?,?,?)",
                     (cur.lastrowid, now, None, status, "ui"))
        gstate.mark_dirty(conn)
    return view_rows(conn, pid, [cur.lastrowid], today)[0]


def is_blank(app: dict) -> bool:
    """A row with neither a company nor a role (just inserted): not written to the Sheet yet."""
    return not (app.get("company") or "").strip() and not (app.get("role_title") or "").strip()
