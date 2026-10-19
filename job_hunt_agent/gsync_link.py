"""Linking a person's existing Google Sheet the first time, and adding the app's columns to it.

`job-hunt-agent google link` always shows the plan first (a dry run) and changes the Sheet only after a yes:
  - every tab's header row is checked against the person's layout (their own columns, in their order);
  - each row is matched to the application imported from it: same row number and same company + role, else the
    only application with that company + role (+ date). Unmatched rows become applications on the first sync; an
    imported application with no row left is recorded as missing (never re-added); cells that differ (e.g. the
    approved date fixes) are listed and left as they are;
  - the hidden last column "job-hunt-agent id" gets one key per row; nothing else on the Sheet changes.
`job-hunt-agent google add-columns` previews, then inserts, the columns the app adds (Alex's Address after Role Title):
Sheets moves the columns to its right together with their formatting, dropdowns and conditional rules; the next
sync fills the new cells from the job location.
"""
import json
from collections import Counter

from . import db, gsync, tracker_layout
from . import gsync_pull as pull
from .google import state
from .gsync_cycle import pair_typed, sync_profile
from .gsync_values import DATE_HEADERS, canon, from_serial, to_db
from .tracker import parse_applied

SAMPLE = 10


def _norm(value) -> str:
    return " ".join(str(value or "").casefold().split())


def _has_content(row) -> bool:
    return any(v != "" for v in row.raw.values())


def _app_key(app: dict, with_date: bool) -> tuple:
    base = (_norm(app["company"]), _norm(app["role_title"]))
    return (*base, app.get("applied_on") or "") if with_date else base


def _row_key(row, with_date: bool) -> tuple:
    base = (_norm(row.raw.get("Company Name")), _norm(row.raw.get("Role Title")))
    day = parse_applied(to_db(row.raw.get("Date Applied"), "Date Applied")) or "" if with_date else None
    return (*base, day) if with_date else base


def match(target: gsync.Target, rows: list, candidates: list[dict]) -> dict[int, int]:
    """sheet row index -> application id, for rows without a key."""
    free = [r for r in rows if not r.key and _has_content(r)]
    by_number = {a["xlsx_row"]: a for a in candidates if a.get("xlsx_row")}
    claimed: dict[int, int] = {}
    for row in free:                                           # 1. the row it was imported from
        app = by_number.get(gsync.xlsx_row(target, row.index))
        if app and _app_key(app, False) == _row_key(row, False):
            claimed[row.index] = app["id"]
    for with_date in (False, True):                            # 2. the only one with that identity
        used = set(claimed.values())
        apps = [a for a in candidates if a["id"] not in used]
        open_rows = [r for r in free if r.index not in claimed]
        app_count = Counter(_app_key(a, with_date) for a in apps)
        row_count = Counter(_row_key(r, with_date) for r in open_rows)
        lookup = {_app_key(a, with_date): a for a in apps}
        for row in open_rows:
            k = _row_key(row, with_date)
            if app_count[k] == 1 and row_count[k] == 1:
                claimed[row.index] = lookup[k]["id"]
    return claimed


def _candidates(conn, target: gsync.Target, layout, apps: dict[int, dict]) -> list[dict]:
    linked = {r["app_id"] for r in conn.execute("SELECT app_id FROM gsync_rows WHERE profile = ?", (target.pid,))}
    return [a for a in apps.values() if a["id"] not in linked and a.get("source") == "import"
            and tracker_layout.tab_of(layout, a).name == target.title]


def plan_tab(conn, target: gsync.Target, layout, apps: dict[int, dict]) -> dict:
    rows = gsync.read(target, True)
    candidates = _candidates(conn, target, layout, apps)
    claimed = match(target, rows, candidates)
    keyed = {gsync.app_id_of(r.key) for r in rows if r.key} - {None}
    content = [r for r in rows if not r.key and _has_content(r) and r.index not in claimed]
    matched = set(claimed.values()) | keyed
    diverged = []
    for row in rows:
        app = apps.get(claimed.get(row.index) or gsync.app_id_of(row.key) or -1)
        for _, column in (target.synced if app else ()):
            s, d = (canon(row.raw.get(column.header), column.header),
                    canon(tracker_layout.cell(app, column, layout)[0], column.header))
            if s != d:
                diverged.append((row.index + 1, column.header, s, d))
    return {"target": target, "rows": rows, "claimed": claimed, "already_keyed": len(keyed), "diverged": diverged,
            "new_rows": [r for r in content if pull.is_application(r.raw)],
            "placeholders": [r for r in content if not pull.is_application(r.raw)],
            "not_found": [a for a in candidates if a["id"] not in matched]}


def plans(conn, pid: str, sid: str) -> tuple[list[dict], dict[str, list]]:
    layout = tracker_layout.of_profile(pid)
    apps = gsync.apps_of(conn, pid)
    found = [plan_tab(conn, t, layout, apps) for t in gsync.targets(conn, pid, sid)]
    appendable: dict[str, list] = {}
    for app in gsync.eligible_new(conn, pid, apps):
        appendable.setdefault(tracker_layout.tab_of(layout, app).name, []).append(app)
    for plan in found:                              # typed in both places: paired, never duplicated
        title = plan["target"].title
        plan["paired"] = pair_typed(plan["new_rows"], appendable.get(title, []))
        rows, ids = {r.index for r, _ in plan["paired"]}, {a["id"] for _, a in plan["paired"]}
        plan["new_rows"] = [r for r in plan["new_rows"] if r.index not in rows]
        appendable[title] = [a for a in appendable.get(title, []) if a["id"] not in ids]
    return found, appendable


def shown(header: str, text: str) -> str:
    """A canonical value as a person reads it (a date serial as MM/DD/YYYY)."""
    if header in DATE_HEADERS and text.replace(".", "", 1).isdigit():
        return from_serial(float(text)).strftime("%m/%d/%Y")
    return text


def _listed(items, render) -> str:
    return "".join(f"\n    {render(i)}" for i in items[:SAMPLE]) + (f"\n    ... {len(items) - SAMPLE} more"
                                                                    if len(items) > SAMPLE else "")


def describe(pid: str, found: list[dict], appendable: dict[str, list]) -> list[str]:
    out = []
    for p in found:
        t = p["target"]
        new = appendable.get(t.title, [])
        out += [f"{pid} · tab {t.title!r}: {len(p['rows'])} rows under the header",
                f"  columns in the Sheet: {', '.join(h for h in t.headers if h and h != gsync.KEY_HEADER)}"]
        if t.missing_columns():
            out.append(f"  app columns not in the Sheet yet (only via `job-hunt-agent google add-columns`): "
                       f"{', '.join(t.missing_columns())}")
        if t.unknown_columns():
            out.append(f"  your columns the app does not know (left untouched): {', '.join(t.unknown_columns())}")
        out += [f"  rows matched to their application: {len(p['claimed'])} (already keyed: {p['already_keyed']})",
                f"  rows you typed that are the app's own applications (paired, not duplicated): {len(p['paired'])}"
                + _listed(p["paired"], lambda ra: f"row {ra[0].index + 1}: {ra[0].raw.get('Company Name')} · "
                                                  f"{ra[0].raw.get('Role Title')}  <->  #{ra[1]['id']} "
                                                  f"{ra[1]['company']} · {ra[1]['role_title']}"),
                f"  rows that become new applications: {len(p['new_rows'])}"
                + _listed(p["new_rows"], lambda r: f"row {r.index + 1}: {r.raw.get('Company Name')} · "
                                                    f"{r.raw.get('Role Title')}"),
                f"  date-only rows (no company, no role), left alone: {len(p['placeholders'])}",
                f"  imported applications with no row on the Sheet any more (kept in the app, not written back): "
                f"{len(p['not_found'])}" + _listed(p["not_found"], lambda a: f"#{a['id']}: {a['company']} · "
                                                                            f"{a['role_title']}"),
                f"  cells that differ between the Sheet and the app (left as they are): {len(p['diverged'])}"
                + _listed(p["diverged"], lambda d: f"row {d[0]} {d[1]}: sheet {shown(d[1], d[2])[:40]!r} | app {shown(d[1], d[3])[:40]!r}"),
                f"  applications marked applied in the app, added as new rows at the end: {len(new)}"
                + _listed(new, lambda a: f"#{a['id']}: {a['company']} · {a['role_title']}"),
                f"  hidden key column: {gsync.letter(_key_at(t))} \"{gsync.KEY_HEADER}\" "
                f"({'already there' if t.key_col is not None else 'added and hidden'})"]
    return out


def _key_at(target: gsync.Target) -> int:
    if target.key_col is not None:
        return target.key_col
    return max((i for i, h in enumerate(target.headers) if h), default=-1) + 1


def _key_column(target: gsync.Target) -> list[dict]:
    """Requests that add the hidden 'job-hunt-agent id' column right after the last header (when it is not there yet)."""
    if target.key_col is not None:
        return []
    at = _key_at(target)
    requests = [{"appendDimension": {"sheetId": target.sheet_id, "dimension": "COLUMNS",
                                     "length": at - target.cols + 1}}] if at >= target.cols else []
    return requests + [
        gsync.write_cell(target, 0, at, {"userEnteredValue": {"stringValue": gsync.KEY_HEADER}}),
        {"updateDimensionProperties": {"range": {"sheetId": target.sheet_id, "dimension": "COLUMNS",
                                                 "startIndex": at, "endIndex": at + 1},
                                       "properties": {"hiddenByUser": True}, "fields": "hiddenByUser"}}]


def _store(conn, pid: str, target: gsync.Target, plan: dict, layout, apps: dict[int, dict], now: str) -> None:
    by_index = {r.index: r for r in plan["rows"]}
    with db.tx(conn):
        for index, app_id in plan["claimed"].items():
            cells = {c.header: [canon(by_index[index].raw.get(c.header), c.header),
                                canon(tracker_layout.cell(apps[app_id], c, layout)[0], c.header)]
                     for _, c in target.synced}
            gsync.save_snapshot(conn, pid, target.title, app_id, cells)
        for app in plan["not_found"]:
            gsync.save_snapshot(conn, pid, target.title, app["id"], {})
            conn.execute("UPDATE gsync_rows SET missing_since = ? WHERE profile = ? AND app_id = ?",
                         (now, pid, app["id"]))
        gsync.log_event(conn, pid, "link", target.title, None, None, f"{len(plan['claimed'])} matched",
                        f"{len(plan['diverged'])} differ")
        state.put(conn, f"gsync.{pid}.{target.title}.read_at", now)
        state.put(conn, f"gsync.{pid}.{target.title}.full_at", now)


def link(conn, pid: str, url: str, apply: bool = False) -> dict:
    """Dry run (default): what linking would do. apply=True: add the key column and keys, then the first sync."""
    sid = gsync.spreadsheet_id(url)
    found, appendable = plans(conn, pid, sid)
    lines = describe(pid, found, appendable)
    if not apply:
        return {"applied": False, "lines": lines}
    layout, apps, now = tracker_layout.of_profile(pid), gsync.apps_of(conn, pid), db.iso(db.utcnow())
    for plan in found:
        gsync.batch(sid, _key_column(plan["target"]))
    fresh = {t.title: t for t in gsync.targets(conn, pid, sid)}
    for plan in found:
        target = fresh[plan["target"].title]
        gsync.batch(sid, [gsync.key_cell(target, i, a) for i, a in plan["claimed"].items()])
        _store(conn, pid, target, plan, layout, apps, now)
    with db.tx(conn):
        state.put(conn, f"gsync.{pid}.sid", sid)
        state.put(conn, f"gsync.{pid}.linked", now)
    return {"applied": True, "lines": lines, "first_sync": sync_profile(conn, pid)}


def _insert_plan(conn, target: gsync.Target, layout, apps: dict[int, dict], header: str, after: str) -> tuple:
    at = target.headers.index(after) + 1
    column = next(c for c in target.tab.columns if c.header == header)
    on_sheet = gsync.snapshots(conn, target.pid, target.title)  # rows on this tab now
    filled = sum(1 for i in on_sheet if i in apps and tracker_layout.cell(apps[i], column, layout)[0] not in (None, ""))
    shown = [h for h in target.headers if h and h != gsync.KEY_HEADER]
    after_row = [*shown[:shown.index(after) + 1], header, *shown[shown.index(after) + 1:]]
    moved = len(shown) - shown.index(after) - 1
    lines = [f"{target.title}: insert column {gsync.letter(at)} \"{header}\" right after \"{after}\"",
             f"  header row now:   {' | '.join(shown)}",
             f"  header row after: {' | '.join(after_row)}",
             f"  {moved} columns move one to the right; Sheets moves their formatting, dropdowns and conditional "
             f"rules with them (a dropdown range that spans the spot widens to cover the new column)",
             f"  the next sync fills {filled} {header} cells from the job location; the others stay empty"]
    requests = [{"insertDimension": {"range": {"sheetId": target.sheet_id, "dimension": "COLUMNS",
                                               "startIndex": at, "endIndex": at + 1}, "inheritFromBefore": True}},
                gsync.write_cell(target, 0, at, {"userEnteredValue": {"stringValue": header}})]
    return lines, requests


def add_columns(conn, pid: str, apply: bool = False) -> dict:
    """Preview (default) or insert the layout's added columns (Alex: Address after Role Title) into the user's Sheet."""
    layout, apps = tracker_layout.of_profile(pid), gsync.apps_of(conn, pid)
    lines, todo = [], []
    for target in gsync.targets(conn, pid):
        for header, after in layout.added:
            if header in target.headers:
                continue
            if after not in target.headers:
                lines.append(f"{target.title}: no \"{after}\" column to put {header} after; nothing changed")
                continue
            more, requests = _insert_plan(conn, target, layout, apps, header, after)
            lines += more
            todo.append((target, requests))
    if not todo:
        return {"applied": False, "lines": lines or ["nothing to add: the Sheet already has every column"]}
    if not apply:
        return {"applied": False, "lines": lines}
    for target, requests in todo:
        gsync.batch(target.sid, requests)
    with db.tx(conn):
        gsync.log_event(conn, pid, "add_columns", None, None, None, None, ", ".join(h for h, _ in layout.added))
    return {"applied": True, "lines": lines, "sync": sync_profile(conn, pid)}


def _diverged(conn, pid: str, header: str | None) -> list[tuple]:
    """(app id, tab, header, [sheet, app]) of every cell still differing since linking; header None = every column.
    A pair whose app side is None is already queued for a push and is not a difference to settle."""
    found = []
    for row in conn.execute("SELECT app_id, tab, cells FROM gsync_rows WHERE profile = ? AND missing_since IS NULL "
                            "ORDER BY app_id", (pid,)):
        for h, pair in json.loads(row["cells"]).items():
            if (header is None or h == header) and isinstance(pair, list) and len(pair) == 2 \
                    and pair[1] is not None and pair[0] != pair[1]:
                found.append((row["app_id"], row["tab"], h, pair))
    return found


def _settle(conn, pid: str, found: list[tuple], side: int, kind: str, label: str) -> None:
    """Mark one side of each pair as changed (side 0: the Sheet's, so the next sync pulls it; side 1: the app's,
    so it pushes it), logged once."""
    with db.tx(conn):
        for app_id, _, header, _ in found:
            cells = json.loads(conn.execute("SELECT cells FROM gsync_rows WHERE profile = ? AND app_id = ?",
                                            (pid, app_id)).fetchone()["cells"])
            cells[header] = [None, cells[header][1]] if side == 0 else [cells[header][0], None]
            conn.execute("UPDATE gsync_rows SET cells = ? WHERE profile = ? AND app_id = ?",
                         (json.dumps(cells), pid, app_id))
        gsync.log_event(conn, pid, kind, None, None, label, None, f"{len(found)} cells")


def push_diverged(conn, pid: str, header: str, apply: bool = False) -> dict:
    """Cells of one column that differed at link time (the app holds an approved fix, e.g. Alex's rows 320-327
    12/05/2026 -> 05/12/2026): preview, or let the app's value win so the next sync writes it to the Sheet."""
    found = _diverged(conn, pid, header)
    lines = [f"{pid}: {len(found)} '{header}' cells where your sheet and the app differ; the app's value would be "
             f"written to the sheet:"] + [f"  #{a} ({t}): sheet {p[0][:30]!r} -> app {p[1][:30]!r}"
                                          for a, t, _, p in found[:SAMPLE * 3]]
    if not apply or not found:
        return {"applied": False, "lines": lines, "count": len(found)}
    _settle(conn, pid, found, 1, "push_diverged", header)
    return {"applied": True, "lines": lines, "count": len(found), "sync": sync_profile(conn, pid)}


def pull_diverged(conn, pid: str, header: str | None = None, apply: bool = False) -> dict:
    """The Sheet wins (Jordan): every cell (or one column's) that still differs since linking takes the
    Sheet's value in the app; nothing is written to the Sheet. Preview first."""
    found = _diverged(conn, pid, header)
    lines = [f"{pid}: {len(found)} {'cells' if header is None else repr(header) + ' cells'} where your sheet and "
             f"the app differ; your sheet's value would be copied into the app (your sheet is not changed):"] + \
        [f"  #{a} ({t}) {h}: app {p[1][:30]!r} -> sheet {p[0][:30]!r}" for a, t, h, p in found]
    if not apply or not found:
        return {"applied": False, "lines": lines, "count": len(found)}
    _settle(conn, pid, found, 0, "pull_diverged", header or "all columns")
    return {"applied": True, "lines": lines, "count": len(found), "sync": sync_profile(conn, pid, full=True)}


def compare(conn, pid: str) -> dict:
    """Read-only check: every cell of every keyed row of the live Sheet against the app's value for it,
    plus typed rows the app has not taken in yet and app rows still waiting to be appended. Writes nothing."""
    layout, apps = tracker_layout.of_profile(pid), gsync.apps_of(conn, pid)
    tabs, differ = {}, []
    for target in gsync.targets(conn, pid):
        rows, cells = gsync.read(target, True), 0
        keyed = [(r, apps.get(gsync.app_id_of(r.key) or -1)) for r in rows if r.key]
        for row, app in keyed:
            for _, column in (target.synced if app else ()):
                h = column.header
                s, d = canon(row.raw.get(h), h), canon(tracker_layout.cell(app, column, layout)[0], h)
                cells += 1
                if s != d:
                    differ.append((target.title, row.index + 1, h, s, d))
        untaken = [r for r in rows if not r.key and pull.is_application(r.raw)]
        tabs[target.title] = {"rows": len(rows), "keyed": len(keyed), "unknown_keys": sum(1 for _, a in keyed if not a),
                              "cells": cells, "untaken_typed_rows": len(untaken)}
    for tab in (t for t in layout.tabs if t.kind == "vendors"):
        tabs[tab.name] = _compare_vendors(conn, pid, tab, differ)
    return {"profile": pid, "tabs": tabs, "differences": differ,
            "waiting_to_append": len(gsync.eligible_new(conn, pid, apps))}


def _compare_vendors(conn, pid: str, tab, differ: list) -> dict:
    """A vendors tab (Jordan's Sheet2, not synced row by row): the live tab's non-empty rows, in order, against the
    app's vendor list."""
    from . import vendors
    sid = gsync.linked_sid(conn, pid)
    grid = gsync.get_values(sid, [f"{gsync.quoted(tab.name)}!A1:{gsync.letter(len(tab.columns) - 1)}"])[0]
    header = [str(h).strip() for h in (grid[0] if grid else [])]
    rows = [r for r in grid[1:] if any(str(v).strip() for v in r)]
    listed = vendors.listing(conn, pid)
    cells = 0
    for n in range(max(len(rows), len(listed))):
        live = rows[n] if n < len(rows) else []
        app = listed[n] if n < len(listed) else {}
        for column in tab.columns:
            at = header.index(column.header) if column.header in header else None
            s = str(live[at]).strip() if at is not None and at < len(live) else ""
            d = str(app.get(column.field) or "").strip()
            cells += 1
            if s != d:
                differ.append((tab.name, n + 2, column.header, s, d))
    return {"rows": len(rows), "keyed": len(listed), "unknown_keys": 0, "cells": cells, "untaken_typed_rows": 0}
