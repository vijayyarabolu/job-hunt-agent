"""One sync cycle per linked tab (decision 008). See gsync.py for the Sheet side.

Per cell, against the pair (Sheet value, app value) stored at the last sync:
  only the Sheet changed -> pull it into the app; only the app changed -> write that one cell;
  both changed -> last writer wins: the app field's own edit time against the Sheet's edit time (the API has no
  per-cell times, so the midpoint between the two reads that bracket the edit); the loser is logged.
  A pair that already differed when the sheet was linked stays as it is until one side changes.
Rows: a row typed into the Sheet (a company or a role, no key) becomes an application and gets its key; an
application marked applied in the app is written as a new row at the end; a key that vanished (row deleted on the
Sheet) is reported in `google status`, never re-added and never deleted in the app.
Snapshots are saved only after the writes went through, so a failed cycle simply repeats.
"""
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from datetime import date

from . import db, gsync, gsync_jd, jd_doc, removal, resume_upload, tracker_layout
from . import gsync_pull as pull
from .applied import company_key, same_company
from .google import GoogleError, accounts, state
from .gsync_values import DATE_HEADERS, canon, to_cell, to_db
from .tracker import parse_applied

log = logging.getLogger(__name__)


@dataclass
class Plan:
    requests: list = field(default_factory=list)
    pushed: list = field(default_factory=list)          # (row index, key, sheet col, header, cell)
    snaps: dict = field(default_factory=dict)            # app id -> cells
    counts: Counter = field(default_factory=Counter)


def _sheet_time(conn, target: gsync.Target, now):
    """When a Sheet edit seen now happened: between the previous read and this one (midpoint)."""
    prev = gsync.parse_time(state.get(conn, f"gsync.{target.pid}.{target.title}.read_at"))
    return prev + (now - prev) / 2 if prev and prev < now else now


def _pair(app: dict, column, layout) -> tuple:
    value, link, as_text = tracker_layout.cell(app, column, layout)
    return canon(value, column.header), value, link, as_text


def _reload(conn, app_id: int) -> dict:
    return dict(conn.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone())


def keep_date(value, sheet_value, header: str):
    """A date the app holds as text ('05/12/2026') goes into a cell that is a real date as a date, so the cell
    stays a date (its own format shows it)."""
    if header in DATE_HEADERS and isinstance(value, str) and isinstance(sheet_value, (int, float)) \
            and not isinstance(sheet_value, bool):
        iso = parse_applied(value)
        return datetime.fromisoformat(iso) if iso else value
    return value


PAIR_DAYS = 7


def similar_company(a, b) -> bool:
    """Same company despite spelling: 'clera' / 'Clera', 'AspenView' / 'Aspenview Technology Partners'."""
    x, y = company_key(a), company_key(b)
    if not x or not y:
        return False
    xs, ys = x.replace(" ", ""), y.replace(" ", "")
    return same_company(x, y) or xs.startswith(ys) or ys.startswith(xs)


def _near(row, app: dict) -> bool:
    day = parse_applied(to_db(row.raw.get("Date Applied"), "Date Applied"))
    if not day or not app.get("applied_on"):
        return True
    return abs((date.fromisoformat(day) - date.fromisoformat(app["applied_on"])).days) <= PAIR_DAYS


def pair_typed(rows: list, apps: list[dict]) -> list[tuple]:
    """Rows typed into the Sheet that are the same job as an application the app made (the person entered it in
    both places): pair them instead of making a second application and a second row. Only unambiguous pairs: one
    row and one application with that company, applied within PAIR_DAYS of each other."""
    pairs, used = [], set()
    for app in apps:
        found = [r for r in rows if similar_company(r.raw.get("Company Name"), app["company"]) and _near(r, app)]
        if len(found) != 1 or found[0].index in used:
            continue
        row = found[0]
        if sum(1 for a in apps if similar_company(row.raw.get("Company Name"), a["company"])) != 1:
            continue
        used.add(row.index)
        pairs.append((row, app))
    return pairs


def decide(s: str, d: str, prior: list | None, app_at, sheet_at) -> str | None:
    """'pull' | 'push' | 'conflict-pull' | 'conflict-push' | 'same' | 'diverged' | None (nothing to do)."""
    if prior is None:                                    # a column new to the sync (e.g. Address just added)
        return "same" if s == d else "push" if s == "" else "pull" if d == "" else "diverged"
    ps, pd = prior
    sheet_changed, app_changed = s != ps, d != pd
    if not (sheet_changed or app_changed):
        return None
    if s == d:
        return "same"
    if sheet_changed and app_changed:
        return "conflict-pull" if app_at is None or sheet_at > app_at else "conflict-push"
    return "pull" if sheet_changed else "push"


def _row(conn, target, layout, row, app, prior: dict, columns, sheet_at, plan: Plan) -> None:
    cells, pushed = dict(prior), set()
    for col, column in columns:
        h = column.header
        s = canon(row.raw.get(h), h)
        d, value, _, as_text = _pair(app, column, layout)
        action = decide(s, d, prior.get(h), gsync.app_edit_time(app, column), sheet_at)
        if action is None:
            continue
        if action in ("same", "diverged"):
            cells[h] = [s, d]
            if action == "diverged":
                gsync.log_event(conn, target.pid, "diverged", target.title, app["id"], h, s, d)
            continue
        if action.startswith("conflict"):
            plan.counts["conflicts"] += 1
            gsync.log_event(conn, target.pid, "conflict", target.title, app["id"], h, s, d,
                            "sheet" if action.endswith("pull") else "app")
        if action.endswith("pull"):
            pull.apply(conn, app, column, row.raw.get(h), target.mapped)
            app = _reload(conn, app["id"])
            cells[h] = [s, _pair(app, column, layout)[0]]
            plan.counts["pulled"] += 1
            gsync.log_event(conn, target.pid, "pull", target.title, app["id"], h, s, d)
        else:
            cell = to_cell(gsync.to_sheet(target, h, keep_date(value, row.raw.get(h), h)), as_text, header=h)
            cell = gsync_jd.styled(h, cell, app, jd_doc.link_of(conn, app["id"]))
            if column.field == resume_upload.RESUME_FIELD:                   # uploaded files stay Drive chips
                cell = {"chipRuns": [], "textFormatRuns": [], **resume_upload.upload_chips(conn, app["id"], cell)}
            plan.requests.append(gsync.write_cell(target, row.index, col, cell))
            pushed.add(h)
            plan.pushed.append((row.index, row.key, col, h, cell))
            cells[h] = [d, d]
            plan.counts["pushed"] += 1
            gsync.log_event(conn, target.pid, "push", target.title, app["id"], h, s, d)
    if gsync.JD_TEXT_HEADER in pushed and "JD" not in pushed:   # the JD cell's hover note follows its text
        plan.requests += [r for r in [gsync_jd.note_request(target, row.index, app)] if r]
    plan.snaps[app["id"]] = cells


def _new_from_sheet(conn, target, layout, row, apps, plan: Plan) -> None:
    """A row typed into the Sheet: one application (re-used if a failed cycle already made it) and its key."""
    number = gsync.xlsx_row(target, row.index)
    found = conn.execute("SELECT a.id FROM applications a LEFT JOIN gsync_rows g ON g.app_id = a.id WHERE "
                         "a.profile = ? AND a.source = 'sheet' AND a.xlsx_row = ? AND g.app_id IS NULL",
                         (target.pid, number)).fetchone()
    app_id = found["id"] if found else pull.create(conn, target.pid, target.tab, row.raw, number)
    app = apps[app_id] = _reload(conn, app_id)
    plan.requests.append(gsync.key_cell(target, row.index, app_id))
    plan.snaps[app_id] = {c.header: [canon(row.raw.get(c.header), c.header), _pair(app, c, layout)[0]]
                          for _, c in target.synced if c.header in row.raw}
    plan.counts["new_from_sheet"] += 1
    gsync.log_event(conn, target.pid, "new_from_sheet", target.title, app_id, None,
                    str(row.raw.get("Company Name") or ""), app.get("role_title"))


def append(conn, target, layout, apps: list[dict], start: int, plan: Plan) -> None:
    """Applications marked applied in the app: new rows at the end, every column typed, plus the key."""
    if not apps:
        return
    if start + len(apps) > target.rows:
        plan.requests.append({"appendDimension": {"sheetId": target.sheet_id, "dimension": "ROWS",
                                                  "length": start + len(apps) - target.rows}})
    for offset, app in enumerate(apps):
        index, cells = start + offset, {}
        for col, column in target.synced:
            value, link, as_text = tracker_layout.cell(app, column, layout)
            if value not in (None, ""):
                cell = to_cell(gsync.to_sheet(target, column.header, value), as_text, link, True, column.header)
                cell = gsync_jd.styled(column.header, cell, app, jd_doc.link_of(conn, app["id"]))
                if column.field == resume_upload.RESUME_FIELD:               # uploaded files: Drive file chips
                    cell = resume_upload.upload_chips(conn, app["id"], cell)
                plan.requests.append(gsync.write_cell(target, index, col, cell, True))
            cells[column.header] = [canon(value, column.header)] * 2
        plan.requests.append(gsync.key_cell(target, index, app["id"]))
        plan.requests += gsync_jd.height_requests(target, [index])           # normal height, like the older rows
        plan.snaps[app["id"]] = cells
        plan.counts["appended"] += 1
        gsync.log_event(conn, target.pid, "appended", target.title, app["id"], None, None,
                        f"{app.get('company')} · {app.get('role_title')}")


def _verify(target: gsync.Target, rows: list, plan: Plan) -> int:
    """Read the key column back: a push that landed on another row (a sort raced it) is moved to its row, and the
    cell it hit gets its own value back."""
    if not plan.pushed:
        return 0
    col = gsync.letter(target.key_col)
    keys = gsync.get_values(target.sid, [f"{gsync.quoted(target.title)}!{col}2:{col}"])[0]
    at = {i + 1: (str(k[0]).strip() if k else "") for i, k in enumerate(keys)}
    where = {k: i for i, k in at.items() if k}
    by_key = {r.key: r for r in rows if r.key}
    fixes = []
    for index, key, column, header, cell in plan.pushed:
        if at.get(index) == key or key not in where:
            continue
        fixes.append(gsync.write_cell(target, where[key], column, cell))
        other = by_key.get(at.get(index))
        if other is not None and header in other.raw:
            fixes.append(gsync.write_cell(target, index, column,         # the cell it hit gets its own text back
                                          to_cell(gsync.to_sheet(target, header, other.raw[header] or None), False,
                                                  header=header, restore=True)))
    if fixes:
        log.warning("%s/%s: %d writes raced a sort on the Sheet; corrected", target.pid, target.title, len(fixes))
        gsync.batch(target.sid, fixes)
    return len(fixes)


def long_pending(target: gsync.Target, apps: dict[int, dict], layout, snaps: dict) -> bool:
    """An app edit of a long column (JD) not written yet: this cycle reads that column, so the edit reaches the
    Sheet within a minute instead of waiting for the scheduled read."""
    for _, column in target.synced:
        if column.header not in gsync.LONG_COLUMNS:
            continue
        for app_id, cells in snaps.items():
            prior = cells.get(column.header)
            if app_id in apps and prior and _pair(apps[app_id], column, layout)[0] != prior[1]:
                return True
    return False


def run_tab(conn, target: gsync.Target, apps: dict[int, dict], layout, appendable: list[dict],
            full: bool = False) -> dict:
    if target.key_col is None:
        raise GoogleError(f"{target.pid}/{target.title}: not linked (no '{gsync.KEY_HEADER}' column): run "
                          f"`job-hunt-agent google link --profile {target.pid} <sheet url>`")
    snaps, plan, seen, typed = gsync.snapshots(conn, target.pid, target.title), Plan(), set(), []
    full = full or gsync.is_full_read(conn, target) or long_pending(target, apps, layout, snaps)
    now = db.utcnow()
    sheet_at = _sheet_time(conn, target, now)
    rows = gsync.read(target, full)
    columns = [(i, c) for i, c in target.synced if full or c.header not in gsync.LONG_COLUMNS]
    removed = removal.removed_apps(conn, target.pid)           # deleted in the app, row going
    for row in rows:
        app_id = gsync.app_id_of(row.key)
        if app_id in removed and app_id not in apps:
            continue
        if app_id in apps and app_id not in seen and app_id in snaps:
            seen.add(app_id)
            _row(conn, target, layout, row, apps[app_id], snaps[app_id], columns, sheet_at, plan)
        elif app_id in apps and app_id not in seen:      # a keyed row the snapshot does not know: adopt as is
            seen.add(app_id)
            plan.snaps[app_id] = {c.header: [canon(row.raw.get(c.header), c.header), _pair(apps[app_id], c, layout)[0]]
                                  for _, c in columns}
        elif pull.is_application(row.raw):               # typed on the Sheet, or a copied row's duplicate key
            if row.key:
                gsync.log_event(conn, target.pid, "duplicate_key", target.title, app_id, None, row.key)
            typed.append(row)
    pairs = pair_typed([r for r in typed if not r.key], appendable)
    for row, app in pairs:
        plan.requests.append(gsync.key_cell(target, row.index, app["id"]))
        plan.snaps[app["id"]] = {c.header: [canon(row.raw.get(c.header), c.header), _pair(app, c, layout)[0]]
                                 for _, c in columns}
        plan.counts["paired"] += 1
        gsync.log_event(conn, target.pid, "paired", target.title, app["id"], None,
                        str(row.raw.get("Company Name") or ""), app.get("company"))
    paired_rows, paired_apps = {r.index for r, _ in pairs}, {a["id"] for _, a in pairs}
    for row in typed:
        if row.index not in paired_rows:
            _new_from_sheet(conn, target, layout, row, apps, plan)
    appendable = [a for a in appendable if a["id"] not in paired_apps and a["id"] not in seen]   # keyed: never twice
    gone = [a for a in snaps if a not in seen]
    start = max([r.index for r in rows if r.key or any(v != "" for v in r.raw.values())], default=0) + 1
    append(conn, target, layout, appendable, start, plan)
    gsync.batch(target.sid, plan.requests)
    fixed = _verify(target, rows, plan)
    with db.tx(conn):
        for app_id, cells in plan.snaps.items():
            gsync.save_snapshot(conn, target.pid, target.title, app_id, cells)
        for app_id in gone:
            conn.execute("UPDATE gsync_rows SET missing_since = ? WHERE profile = ? AND app_id = ?",
                         (db.iso(now), target.pid, app_id))
            gsync.log_event(conn, target.pid, "missing", target.title, app_id)
        state.put(conn, f"gsync.{target.pid}.{target.title}.read_at", db.iso(now))
        if full:
            state.put(conn, f"gsync.{target.pid}.{target.title}.full_at", db.iso(now))
    deleted = delete_rows(conn, target)
    return {**plan.counts, "rows": len(rows), "missing": len(gone), "race_fixes": fixed, "full_read": full,
            "deleted": deleted}


def delete_rows(conn, target: gsync.Target) -> int:
    """the Sheet rows of tracker rows deleted in the app (after the Undo toast), found by their key read
    just now (a sort since the cycle's read moves nothing here), bottom first so no index shifts under another."""
    claimed, stamp = removal.claim_sheet_deletes(conn, target.pid, target.title)
    wanted = set(claimed)
    if not wanted:
        return 0
    try:
        col = gsync.letter(target.key_col)
        keys = gsync.get_values(target.sid, [f"{gsync.quoted(target.title)}!{col}2:{col}"])[0]
        hits = sorted((i + 1 for i, k in enumerate(keys) if k and gsync.app_id_of(str(k[0]).strip()) in wanted),
                      reverse=True)
        gsync.batch(target.sid, [{"deleteDimension": {"range": {"sheetId": target.sheet_id, "dimension": "ROWS",
                                                                "startIndex": i, "endIndex": i + 1}}} for i in hits])
    except Exception:
        removal.release_claim(conn, target.pid, claimed, stamp)
        raise
    for app_id in sorted(wanted):
        gsync.log_event(conn, target.pid, "deleted", target.title, app_id)
    return len(hits)


def sync_profile(conn, pid: str, full: bool = False) -> dict:
    """Every application tab of one person's sheet; new app rows go to the first tab. full: read the long
    columns (JD) now instead of on their own schedule. on the person's own Google token when they
    connected one (their sheet is theirs), else, or when it is refused, on the owner's as before."""
    return accounts.with_fallback(pid, _sync_profile, conn, pid, full)


def _sync_profile(conn, pid: str, full: bool) -> dict:
    from .gsync_twins import detach_reused, merge_twins      # late: gsync_twins imports this module
    layout = tracker_layout.of_profile(pid)
    apps = gsync.apps_of(conn, pid)
    appendable, apps = merge_twins(conn, pid, gsync.eligible_new(conn, pid, apps), apps)
    report = {}
    for target in gsync.targets(conn, pid):
        mine = [a for a in appendable if tracker_layout.tab_of(layout, a).name == target.title]
        report[target.title] = run_tab(conn, target, apps, layout, mine, full)
    detach_reused(conn, pid)                                  # a row the person reused for another job
    for app_id in jd_doc.missing(conn, pid):                  # a row typed into the Sheet gets its Doc
        jd_doc.soon(app_id)
    return report
