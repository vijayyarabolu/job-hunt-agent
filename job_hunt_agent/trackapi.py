"""The tracker behind the web UI: one tracker per profile with every field both trackers use (Alex's 14
columns, Jordan's Address / Team Name and vendors) plus the job's facts, origin, pipeline status, follow-up dues and
several contacts per row. Plain functions over a sqlite connection returning JSON-ready dicts; the DB stays the
source of truth and every write marks the Google Sheet dirty (tracker.edit_field / set_status do)."""
import json
import sqlite3
from datetime import date, datetime, timedelta

from . import config, contacts, db, dupes, history, jd_doc, signals, titles, tracker, trackfind, tracker_layout
from .google import state as gstate
from .mail import WEB as MAIL_LINK

COLD_NOTES = {"cold": "Cold rule: no reply in time", "cold undo": "Cold undone"}

UI_STATUSES = ("saved", "applied", "screen", "interview", "offer", "rejected", "ghosted")
# GOAL35 §5.2: a search's results grouped by status (the chip order), newest first within each
SEARCH_GROUP_SQL = ("CASE replace(a.status, 'tailored', 'saved') " +
                    " ".join(f"WHEN '{s}' THEN {i}" for i, s in enumerate(UI_STATUSES)) + " ELSE 99 END")
GROUPS = {"active": ("applied", "screen", "interview", "offer"), "interviewing": ("screen", "interview", "offer"),
          "archived": ("rejected", "ghosted"), "saved": ("saved", "tailored")}     # tab filters
EXTRA_FIELDS = ("notes", "job_url", "apply_url", "location", "workplace", "salary", "employment_type", "address",
                "team_name")
EDITABLE = (*tracker.FIELDS, *EXTRA_FIELDS)
EXPORTED_EXTRAS = ("address", "team_name")       # columns of Jordan's own workbook: export reads them when edited
WORKPLACES = ("remote", "hybrid", "onsite")
PAGE = 100
MAX_PAGE = 2000
MAX_TEXT = 5000
MAX_LONG_TEXT = 49_000                  # the JD cell (Sheets holds 50,000 characters)
LONG_FIELDS = ("jd", "notes")
SHEET_ONLY = "sheet:"                   # a Sheet column with no app field, e.g. "sheet:Column 1"
UPCOMING_DAYS = 3
FOLLOWUP_WINDOW_DAYS = tracker.SUPPRESS_FOLLOWUPS_AFTER_DAYS
def _twins(conn, pid: str) -> dict[int, dict]:
    """row id -> {'id', 'label'} of the newer row it duplicates."""
    found = dupes.apps(conn, pid)
    if not found:
        return {}
    marks = ", ".join("?" * len(set(found.values())))
    newer = {r["id"]: r for r in conn.execute(f"SELECT id, company, role_title FROM applications WHERE id IN ({marks})",
                                              list(set(found.values())))}
    return {dup: {"id": keep, "label": dupes.label(newer[keep]["company"], newer[keep]["role_title"])}
            for dup, keep in found.items()}


_ROW_SQL = ("SELECT a.*, j.families AS job_families, j.first_posted_at AS job_posted_at, j.closed_at AS job_closed_at, "
            "j.sponsorship AS job_sponsorship, "
            "(SELECT count(*) FROM resume_uploads u WHERE u.app_id = a.id AND u.removed_at IS NULL) "
            "AS uploads_n "
            "FROM applications a LEFT JOIN jobs j ON j.uid = a.job_uid WHERE a.profile = ?")


def _own(conn, pid: str, app_id: int) -> dict:
    row = conn.execute(_ROW_SQL + " AND a.id = ?", (pid, app_id)).fetchone()
    if row is None:
        raise KeyError(app_id)
    return dict(row)


def family_of(app: dict) -> str:
    """The role family: the posting's families when it came from the feed, else the title's (exclusions ignored:
    an applied-for 'Lead ML Engineer' is still ML), else 'other'."""
    fams = [f for f in (app.get("job_families") or "").split(",") if f]
    if fams:
        return fams[0]
    title = app.get("role_title") or ""
    return next((name for name, rx in titles.FAMILIES.items() if rx.search(title)), "other")


def _outreach_on(people: list[dict], applied_on: str | None) -> date | None:
    """the first outreach the person marked sent (a dated Email sent / LinkedIn sent tick) on or after
    applying. Follow-ups then count from it; legacy marks like "y" carry no date and are ignored."""
    if not applied_on:
        return None
    applied, days = date.fromisoformat(applied_on), []
    for person in people:
        for flag in ("email_sent", "linkedin_sent"):
            try:
                day = datetime.strptime((person.get(flag) or "").strip(), "%m/%d/%Y").date()
            except ValueError:
                continue
            if day >= applied:
                days.append(day)
    return min(days) if days else None


def _due(app: dict, n: int, today: date, outreach: date | None = None) -> dict | None:
    """Follow-up n: {'due': ISO, 'state': done | overdue | today | upcoming, 'after': what it counts from} for applied
    rows (+3 / +6 days after the first outreach sent, else after applying); None otherwise."""
    if app["status"] != "applied" or not app.get("applied_on"):
        return None
    applied = date.fromisoformat(app["applied_on"])
    base = outreach if outreach and outreach >= applied else applied
    after = f"outreach {base.strftime('%m/%d/%Y')}" if base != applied else "applied"
    due = base + timedelta(days=tracker.FOLLOWUP_DAYS[n - 1])
    if (app.get(f"follow_up_{n}") or "").strip():
        return {"due": due.isoformat(), "state": "done", "done": app[f"follow_up_{n}"], "after": after}
    if app.get("followups_suppressed") or (today - base).days > FOLLOWUP_WINDOW_DAYS or applied > today:
        return None
    state = "overdue" if due < today else "today" if due == today else "upcoming"
    return {"due": due.isoformat(), "state": state, "after": after}


def _shown(value) -> str | None:
    """A sheet cell as the UI shows it: dates MM/DD/YYYY, whole numbers without .0, everything else as text."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.strftime("%m/%d/%Y")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def view(app: dict, people: list[dict], today: date, layout=None) -> dict:
    """One tracker row for the UI (all fields + computed dues, family and contacts). With the profile's layout
 also `sheet`: the row's tab and its cells in the person's own column order, and `extras`: the app's
    own fields, kept apart."""
    out = {k: app.get(k) for k in ("id", "job_uid", "status", "applied_on", "updated_at", "source", "resume_used",
                                    "match_pct", *EDITABLE)}
    out.update(resume_missing=bool(app.get("resume_missing")), has_upload=bool(app.get("uploads_n")),
               has_resume=bool(app.get("uploads_n") or (app.get("resume") or "").strip()),     # ×
               family=family_of(app), posted_at=app.get("job_posted_at"), job_closed=bool(app.get("job_closed_at")),
               fu1=_due(app, 1, today, _outreach_on(people, app.get("applied_on"))),
               fu2=_due(app, 2, today, _outreach_on(people, app.get("applied_on"))),
               contacts=[{k: c[k] for k in ("id", "pos", *contacts.FIELDS, "origin")} for c in people],
               employment_label=signals.EMPLOYMENT_LABELS.get(app.get("employment_type") or ""))
    if out["status"] == "tailored":                          # pre-GOAL3 rows (migrated to saved)
        out["status"] = "saved"
    if layout is not None:
        tab, values = tracker_layout.cells(app, layout)
        out["sheet"] = {"tab": tab.name, "index": tab.index, "cells": [_shown(v) for v in values]}
        out["extras"] = {k: _shown(v) for k, v in tracker_layout.extras(app, out).items()}
    return out


def _search(conn, pid: str, q: str | None, wide: bool = False) -> tuple[str, list, dict | None, str]:
    """GOAL35 §5: (where, params, what the search found, the order inside a status by how well each row matched)
    for the shared search over every row of the person. 2026-10-24 ("I typed Amazon and it's showing something
    else"): a search for a company shows the rows that name it (company, title, city, notes ...) alone, best first in
    each status; the rows whose JD only mentions it ("Amazon Bedrock") are counted in `jd_only`, listed with `wide`."""
    if not q or not q.strip():
        return "", [], None, ""
    found = trackfind.find(conn, pid, q)
    named = found.strong()
    keep = found.positions if wide else named
    ids = [found.index.records[i].id for i in keep]
    where = {str(found.index.records[i].id): found.where(i) for i in keep}
    status_of = dict(conn.execute(f"SELECT id, replace(status, 'tailored', 'saved') FROM applications WHERE id IN "
                                  f"({','.join('?' * len(ids)) or 'NULL'})", ids).fetchall())
    counts: dict[str, int] = {}
    for app_id in ids:
        counts[status_of[app_id]] = counts.get(status_of[app_id], 0) + 1
    return (f" AND a.id IN ({','.join('?' * len(ids)) or 'NULL'})", list(ids),
            {"q": q.strip(), "terms": list(found.terms), "counts": counts, "total": len(ids), "where": where,
             "jd_only": len(found.positions) - len(named), "wide": bool(wide)}, _tier_order(found, keep))


def _tier_order(found, keep: list) -> str:
    """ORDER BY part: the rows by how well they matched (textsearch tiers), best first; '' when all match alike."""
    tiers: dict[int, list[int]] = {}
    for i in keep:
        tiers.setdefault(found.tier(i), []).append(int(found.index.records[i].id))      # int(): safe to inline
    if len(tiers) < 2:
        return ""
    whens = " ".join(f"WHEN a.id IN ({','.join(map(str, ids))}) THEN {t}" for t, ids in sorted(tiers.items()))
    return f"CASE {whens} END, "


def _tab_filter(layout, sheet: int | None) -> tuple[str, list]:
    """Rows of one of a mapped profile's tabs (its import row numbers; app-created rows live on the first tab)."""
    if not sheet or layout is None or not layout.mapping:
        return "", []
    from .tracker_map import SHEET_ROW_STRIDE
    n = len(layout.app_tabs)
    index = "CAST(coalesce(a.xlsx_row, 0) / ? AS INTEGER)"
    if sheet == 1:
        return f" AND ({index} = 1 OR {index} NOT BETWEEN 1 AND ?)", [SHEET_ROW_STRIDE, SHEET_ROW_STRIDE, n]
    return f" AND {index} = ?", [SHEET_ROW_STRIDE, sheet]


def _layout(pid: str):
    try:
        return tracker_layout.of_profile(pid)
    except (config.ConfigError, OSError, ValueError):
        return None                     # a profile without its files (tests): rows without the sheet view


CLIP_CHARS = 240                       # a list row carries one line of each long cell (the JD, notes)


def _clip(value):
    return f"{value[:CLIP_CHARS - 1]}…" if isinstance(value, str) and len(value) > CLIP_CHARS else value


def compact(row: dict) -> dict:
    """The row with long text cut to CLIP_CHARS; `clipped` names the cut fields ('sheet:<i>' for sheet cells) so the
    page loads the full row before editing one."""
    out = {k: _clip(v) for k, v in row.items()}          # each value clipped once (GOAL30-app: 2.5 MB lists)
    cut = [k for k, v in row.items() if v != out[k]]
    if row.get("sheet"):
        cells = row["sheet"]["cells"]
        clipped = [_clip(v) for v in cells]
        cut += [f"sheet:{i}" for i, (v, c) in enumerate(zip(cells, clipped)) if v != c]
        out["sheet"] = {**row["sheet"], "cells": clipped}
    if row.get("extras"):
        out["extras"] = {k: _clip(v) for k, v in row["extras"].items()}
    return {**out, "clipped": cut}


def rows(conn, pid: str, status: str | None = None, q: str | None = None, limit: int = PAGE, offset: int = 0,
         today: date | None = None, sheet: int | None = None, clip: bool = False, wide: bool = False) -> dict:
    """A page of the profile's tracker, newest application first, with per-status counts for the filter chips, the
    person's sheet layout (GOAL4 §2) and, for a mapped profile, one tab's rows (`sheet` = tab index). `clip`: long
    text cut for a list view (compact). `wide`: a search also lists the rows only their JD matched (_search)."""
    today = today or date.today()
    layout = _layout(pid)
    if status and status not in (*UI_STATUSES, *GROUPS):
        raise ValueError(f"status must be one of {UI_STATUSES} or {tuple(GROUPS)}")
    limit, offset = max(1, min(int(limit), MAX_PAGE)), max(0, int(offset))
    where, params, found, by_tier = _search(conn, pid, q, wide)
    tab_where, tab_params = _tab_filter(layout, sheet)
    where, params = where + tab_where, [*params, *tab_params]
    if status in GROUPS:
        where, params = where + f" AND a.status IN ({','.join('?' * len(GROUPS[status]))})", [*params, *GROUPS[status]]
    elif status:
        where, params = where + " AND a.status = ?", [*params, status]
    newest = "coalesce(a.applied_on, substr(a.updated_at, 1, 10), '') DESC, a.id DESC"
    order = f" ORDER BY {SEARCH_GROUP_SQL}, {by_tier}{newest}" if found else f" ORDER BY {newest}"   # GOAL35: by status
    total = conn.execute(f"SELECT count(*) FROM applications a WHERE a.profile = ?{where}", [pid, *params]).fetchone()[0]
    page = [dict(r) for r in conn.execute(_ROW_SQL + where + order + " LIMIT ? OFFSET ?",
                                          [pid, *params, limit, offset])]
    people = contacts.for_apps(conn, [r["id"] for r in page])
    counts = {r["status"]: r["n"] for r in conn.execute(
        "SELECT replace(status, 'tailored', 'saved') AS status, count(*) AS n FROM applications WHERE profile = ? "
        "GROUP BY 1", (pid,))}
    from .gridapi import conflicts                              # late: gridapi imports this module
    flags = conflicts(conn, pid, [r["id"] for r in page])
    twins = _twins(conn, pid)
    from .h1b.feed import app_views                             # late: h1b imports config users
    spons = app_views(conn, pid, page)                          # GOAL24 §3: the company's sponsorship badge
    views = [{**view(r, people[r["id"]], today, layout), "conflicts": flags.get(r["id"]), "dup_of": twins.get(r["id"]),
              "sponsor": spons.get(r["id"])} for r in page]
    return {"rows": [compact(v) for v in views] if clip else views, "total": total, "offset": offset,
            "counts": counts, "statuses": UI_STATUSES, "found": found,
            "layout": tracker_layout.describe(layout) if layout else None}


def _event(ev: dict) -> dict:
    """A status event for the editor; GOAL12: a change an email made carries an "Email" note (date, subject, link)."""
    snapshot = json.loads(ev.pop("snapshot") or "{}")
    rule = snapshot.get("rule") if isinstance(snapshot, dict) else None
    if rule in COLD_NOTES:                            # the Cold rule writes source 'email' too: say what it was
        return {**ev, "note": COLD_NOTES[rule]}
    email = snapshot.get("email") if isinstance(snapshot, dict) else None
    if not email:
        return ev
    link = email.get("link") or ""
    return {**ev, "note": f"Email {(email.get('at') or '')[:10]}: {email.get('subject') or '(no subject)'}",
            "link": link if link.startswith(MAIL_LINK) else None}


def get(conn, pid: str, app_id: int, today: date | None = None) -> dict:
    app, layout = _own(conn, pid, app_id), _layout(pid)
    out = view(app, contacts.for_apps(conn, [app_id])[app_id], today or date.today(), layout)
    if layout is not None:                       # the editor lists the row's sheet columns in the person's order
        tab = tracker_layout.tab_of(layout, app)
        out["sheet"]["columns"] = [{"header": c.header, "field": c.field} for c in tab.columns]
    out["events"] = [_event(dict(r)) for r in conn.execute(
        "SELECT at, from_status, to_status, source, snapshot FROM application_events WHERE app_id = ? ORDER BY id",
        (app_id,))]
    out["uploads"] = [dict(r) for r in conn.execute(                # resume files uploaded to Drive
        "SELECT version, name, link, uploaded_at, sheet FROM resume_uploads WHERE app_id = ? AND removed_at IS NULL "
        "ORDER BY version DESC",
        (app_id,))]
    out["history"] = history.for_app(conn, app_id)                    # who, when, old -> new
    snap = conn.execute("SELECT url, text, source, state, fetched_at, checked_at, detail FROM jd_snapshots "
                        "WHERE app_id = ?", (app_id,)).fetchone()
    out["jd_snapshot"] = dict(snap) if snap else None                   # Original posting + full JD
    out["jd_text"] = app.get("jd_text")                                  # the JD Text column (typed in app or Sheet)
    out["sync"] = sync_state(conn, pid, app_id)
    return out


UNDO_TO_READY_MAX = 5           # Done today's Undo reaches back at most this many edits to the applied mark


def undo(conn, pid: str, app_id: int | None = None, who: str = history.CLI, today: date | None = None,
         field: str | None = None) -> dict:
    """revert the person's last edit (the whole batch: one Save, one cell, one contacts save), on one row
    or anywhere. Returns the row it changed and what was reverted. With field (a toast's Undo): only when
    that last edit is the one the toast was about."""
    rows = history.last_batch(conn, pid, app_id)
    if not rows:
        raise ValueError("nothing to undo")
    if field and not any(r["field"] == field for r in rows):
        raise ValueError("this row was changed again since: undo that change in the row first")
    target = rows[0]["app_id"]
    app = _own(conn, pid, target)
    restored_contacts = any(r["field"] == "contacts" for r in rows)
    with history.action(who, "undo") as batch:
        for rec in reversed(rows):
            if rec["field"] == "contacts":
                contacts.save(conn, target, json.loads(rec["old_value"] or "[]"), keep_empty=True)
            elif rec["field"] in contacts.LEGACY and restored_contacts:
                continue                                    # re-packed by the contacts restore
            elif rec["field"] == "status":
                tracker.set_status(conn, target, rec["old_value"] or tracker.default_status(app["applied_on"]),
                                   source="ui")
            elif rec["field"].startswith(SHEET_ONLY):
                _set_sheet_only(conn, _own(conn, pid, target), rec["field"][len(SHEET_ONLY):], rec["old_value"])
            else:
                _restore(conn, _own(conn, pid, target), rec["field"], rec["old_value"])
    history.mark_undone(conn, rows[0]["batch"], batch)
    return {"row": get(conn, pid, target, today), "undone": [{k: r[k] for k in ("field", "old_value", "new_value")}
                                                             for r in rows]}


def undo_to_ready(conn, pid: str, app_id: int, who: str = history.CLI, today: date | None = None) -> dict:
    """GOAL13 Done today → Undo: back to before the applied mark in one click, with the edits made after it (the sheet
    fields filled after Yes, the uploaded resume). Without that mark among the row's newest edits: its last edit only."""
    steps = history.batches_back_to(conn, pid, app_id, "status", "applied", UNDO_TO_READY_MAX) or 1
    done = [undo(conn, pid, app_id, who, today) for _ in range(steps)]
    return {"row": done[-1]["row"], "undone": [u for d in done for u in d["undone"]]}


def _restore(conn, app: dict, field: str, value: str | None) -> None:
    """Put a field back as it was (a date that no longer parses goes back as the text it was)."""
    try:
        _set_field(conn, app, field, value)
    except ValueError:
        if field in tracker.FIELDS:
            tracker.edit_field(conn, app["id"], field, value)
        else:
            _set_extra(conn, app["id"], field, value)


def sync_state(conn, pid: str, app_id: int) -> dict:
    """GOAL6 §2 "saved ✓ / synced ✓": is every app value of this row already in the person's Sheet?"""
    from .gsync_values import canon
    snap = conn.execute("SELECT cells, synced_at FROM gsync_rows WHERE profile = ? AND app_id = ? "
                        "AND missing_since IS NULL", (pid, app_id)).fetchone()
    if snap is None:
        app = _own(conn, pid, app_id)
        reached = app["status"] not in ("saved", "tailored")
        return {"state": "not_on_sheet", "synced_at": None, "pending": [],
                "why": "added to the Sheet on the next sync" if reached and app.get("source") != "import"
                else "saved rows are added to the Sheet when you mark them applied"}
    layout, app = _layout(pid), _own(conn, pid, app_id)
    cells = json.loads(snap["cells"])
    tab = tracker_layout.tab_of(layout, app) if layout else None
    pending = [c.header for c in (tab.columns if tab else ()) if c.header in cells
               and canon(tracker_layout.cell(app, c, layout)[0], c.header) != cells[c.header][1]]
    return {"state": "pending" if pending else "synced", "pending": pending, "synced_at": snap["synced_at"],
            "last_sync_at": gstate.get(conn, "last_sync_at"),
            "errors": gstate.get_json(conn, "last_sync_errors") or {}}


def _date_cell(value: str) -> tuple[str | None, str | None]:
    """A date typed or picked in the UI -> (tracker cell text MM/DD/YYYY, ISO date); (None, None) when cleared."""
    if not value:
        return None, None
    iso = tracker.parse_applied(value)
    if iso is None:
        raise ValueError("date must be MM/DD/YYYY (or YYYY-MM-DD)")
    return datetime.strptime(iso, "%Y-%m-%d").strftime("%m/%d/%Y"), iso


def _text(value, field: str = "") -> str | None:
    if value is None:
        return None
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        raise ValueError("field values must be text")
    text = str(value).strip()
    limit = MAX_LONG_TEXT if field in LONG_FIELDS else MAX_TEXT
    if len(text) > limit:
        raise ValueError(f"at most {limit} characters")
    return text or None


def _check(field: str, value: str | None) -> None:
    if field == "date_applied":
        _date_cell(value or "")                                 # raises on a bad date before anything is written
    if field == "workplace" and value and value not in WORKPLACES:
        raise ValueError(f"workplace must be one of {WORKPLACES}")
    if field == "employment_type" and value and value not in signals.EMPLOYMENT_LABELS:
        raise ValueError(f"employment type must be one of {tuple(signals.EMPLOYMENT_LABELS)}")


def _set_extra(conn, app_id: int, field: str, value: str | None) -> None:
    now = db.iso(db.utcnow())
    row = conn.execute(f"SELECT edited, field_at, {field} AS old FROM applications WHERE id = ?",
                       (app_id,)).fetchone()
    edited = set(json.loads(row["edited"] or "[]")) | ({field} if field in EXPORTED_EXTRAS else set())
    stamps = {**json.loads(row["field_at"] or "{}"), field: now}
    with db.tx(conn):
        conn.execute(f"UPDATE applications SET {field} = ?, edited = ?, field_at = ?, updated_at = ? WHERE id = ?",
                     (value, json.dumps(sorted(edited)), json.dumps(stamps), now, app_id))
        history.record(conn, app_id, field, row["old"], value)
        gstate.mark_dirty(conn)


def _set_field(conn, app: dict, field: str, value: str | None) -> None:
    if field == "date_applied":
        cell, iso = _date_cell(value or "")
        if cell != app.get("date_applied") or iso != app.get("applied_on"):
            tracker.edit_field(conn, app["id"], "date_applied", cell)
            with db.tx(conn):
                conn.execute("UPDATE applications SET applied_on = ? WHERE id = ?", (iso, app["id"]))
    elif (app.get(field) or None) == value:
        return
    elif field == "jd" and "\n" in (value or "").strip():
        from . import jdsnap                                         # late: jdsnap imports tracker
        jdsnap.split_typed(conn, app, value)                         # the JD cell stays one linked line
    elif field in contacts.LEGACY and _has_app_contacts(conn, app["id"]):
        contacts.set_packed(conn, app["id"], field, value)          # a contact cell edited inline
    elif field in tracker.FIELDS:
        tracker.edit_field(conn, app["id"], field, value)
        if field in contacts.LEGACY:
            contacts.refresh_legacy(conn, [app["id"]])
    else:
        _set_extra(conn, app["id"], field, value)


def _set_sheet_only(conn, app: dict, header: str, value: str | None) -> None:
    """A Sheet column with no app field (Jordan's "Column 1"): its cell kept on the row, written back by the sync."""
    from .gsync_pull import _raw, _tag
    from .tracker import encode
    layout = _layout(app["profile"])
    tab = tracker_layout.tab_of(layout, app) if layout else None
    if tab is None or not any(c.header == header and c.field is None for c in tab.columns):
        raise ValueError(f"not editable: {header!r}")
    before = tracker_layout._passthrough(app, header)[0]
    raw = _tag(_raw(app) or [encode(None)] * len(tracker.FIELDS), header, value)
    with db.tx(conn):
        conn.execute("UPDATE applications SET raw_json = ?, updated_at = ? WHERE id = ?",
                     (json.dumps(raw), db.iso(db.utcnow()), app["id"]))
        history.record(conn, app["id"], f"{SHEET_ONLY}{header}", before, value)
        gstate.mark_dirty(conn)


def _has_app_contacts(conn, app_id: int) -> bool:
    return conn.execute("SELECT 1 FROM contacts WHERE app_id = ? AND origin = 'app'", (app_id,)).fetchone() is not None


def not_stale_resume(conn, pid: str, app_id: int, changes: dict) -> None:
    """a page that had not caught up with a Replace sent the replaced file's name back (row 392), and the
    sync wrote it as plain text over the new file's chip. Refused: a Resume from the page that names only files this
    row retired, while it has a current file. Only the page's writes are checked (server.py): the app's own writes
    (Undo's fallback puts the old text back) are not."""
    _own(conn, pid, app_id)                                          # another person's row: KeyError, nothing said
    text = _text(changes.get("resume"), "resume") if "resume" in changes else None
    files = conn.execute("SELECT name, removed_at FROM resume_uploads WHERE app_id = ?", (app_id,)).fetchall()
    now = [f["name"] for f in files if f["removed_at"] is None and f["name"]]
    if not text or not now or any(name in text for name in now):
        return
    if any(f["name"] and f["name"] in text for f in files if f["removed_at"] is not None):
        raise ValueError(f"this row's resume was replaced by {', '.join(now)}: reload to see it "
                         "(to use the old file again, pick it in Replace ▾)")


def update(conn, pid: str, app_id: int, changes: dict, today: date | None = None) -> dict:
    """Edit any tracker fields and/or the pipeline status of one of the profile's applications."""
    app = _own(conn, pid, app_id)
    if not isinstance(changes, dict):
        raise ValueError("changes must be an object")
    unknown = {f for f in changes if not f.startswith(SHEET_ONLY)} - set(EDITABLE) - {"status"}
    if unknown:
        raise ValueError(f"not editable: {sorted(unknown)}")
    status = changes.get("status")
    if status and status not in UI_STATUSES:
        raise ValueError(f"status must be one of {UI_STATUSES}")
    values = {f: _text(v, f) for f, v in changes.items() if f != "status"}
    for field, value in values.items():
        _check(field, value)
    for field, value in values.items():
        if field.startswith(SHEET_ONLY):
            _set_sheet_only(conn, app, field[len(SHEET_ONLY):], value)
        else:
            _set_field(conn, app, field, value)
    if status and status != app["status"]:
        tracker.set_status(conn, app_id, status, source="ui")
        if status == "applied":
            jd_doc.soon(app_id)                                  # the full JD as a Google Doc
    return get(conn, pid, app_id, today)


def _salary_text(low: float | None, high: float | None) -> str | None:
    if low and high and low != high:
        return f"${low / 1000:.0f}k to ${high / 1000:.0f}k"
    return f"${(low or high) / 1000:.0f}k" if (low or high) else None


def job_facts(conn, uid: str | None) -> dict:
    """The tracker fields a fetched job prefills (company, role, links, location, workplace, salary, type)."""
    if not uid:
        return {}
    job = conn.execute("SELECT * FROM jobs WHERE uid = ?", (uid,)).fetchone()
    if job is None:
        return {}
    return {"company": job["company"], "role_title": job["title"], "job_url": job["url"],
            "apply_url": job["apply_url"] or job["url"], "location": job["location"], "address": job["location"],
            "workplace": job["workplace"],
            "salary": _salary_text(job["salary_min"], job["salary_max"]), "employment_type": job["employment_type"]}


def create(conn, pid: str, data: dict, today: date | None = None) -> dict:
    """Add a job by hand (paste any URL; ATS links come back auto-filled from lookup()). Company and role
    are required; status defaults to applied today. A posting already tracked is refused with its row id."""
    today = today or date.today()
    if not isinstance(data, dict):
        raise ValueError("expected an object")
    uid = _text(data.get("job_uid"))
    typed = {k: _text(data.get(k)) for k in EDITABLE if k in data}
    for field, value in typed.items():
        _check(field, value)
    fields = {**{k: v for k, v in job_facts(conn, uid).items() if v}, **{k: v for k, v in typed.items() if v}}
    if fields.get("location") and not fields.get("address"):          # Address from the job location
        fields["address"] = fields["location"]
    if not fields.get("company") or not fields.get("role_title"):
        raise ValueError("company and role are required")
    status = data.get("status") or "applied"
    if status not in UI_STATUSES:
        raise ValueError(f"status must be one of {UI_STATUSES}")
    if uid:
        found = conn.execute("SELECT id FROM applications WHERE profile = ? AND job_uid = ?", (pid, uid)).fetchone()
        if found:
            raise ValueError(f"already in the tracker (row #{found['id']})")
    cell, iso = _date_cell(fields.pop("date_applied", None) or "")
    if status != "saved" and not iso:
        cell, iso = today.strftime("%m/%d/%Y"), today.isoformat()
    fields["jd"] = fields.get("jd") or fields.get("job_url")
    tracker.ensure_profile(conn, pid)
    now = db.iso(db.utcnow())
    cols = {**fields, "date_applied": cell, "applied_on": iso, "status": status, "source": "manual",
            "job_uid": uid, "updated_at": now}
    try:
        with db.tx(conn):
            cur = conn.execute(f"INSERT INTO applications(profile, {', '.join(cols)}) VALUES (?{',?' * len(cols)})",
                               [pid, *cols.values()])
            conn.execute("INSERT INTO application_events(app_id, at, from_status, to_status, source) "
                         "VALUES (?,?,?,?,?)", (cur.lastrowid, now, None, status, "ui"))
            gstate.mark_dirty(conn)
    except sqlite3.IntegrityError:                              # a double submit raced the check above
        raise ValueError("already in the tracker") from None
    contacts.refresh_legacy(conn, [cur.lastrowid])
    from . import jdsnap                                         # late: jdsnap imports tracker
    jdsnap.on_save(conn, cur.lastrowid, fetch=True)              # keep the JD while the posting is live
    return get(conn, pid, cur.lastrowid, today)


def lookup(conn, url: str) -> dict:
    """Prefill for a pasted URL: the fetched job when we have it, else the ATS's public JSON (jobfetch), else just
    the URL (any other site: the user types the rest)."""
    from . import jobfetch
    url = (url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("paste a job link (https://...)")
    bare = {"job_url": url, "apply_url": url}
    resolve = jobfetch.db_resolver(conn)
    parsed = jobfetch.parse_url(url, resolve)
    if parsed is None:
        return {"supported": False, "fields": bare}
    facts = job_facts(conn, parsed["uid"])
    if facts:
        return {"supported": True, "from": "feed", "job_uid": parsed["uid"], "fields": facts}
    try:
        posting = jobfetch.fetch_posting(url, resolve)
    except ValueError as err:
        return {"supported": True, "error": str(err), "job_uid": parsed["uid"], "fields": bare}
    if not posting or posting.get("blocked"):
        why = "the site's robots.txt does not allow automated reads" if posting else "nothing readable came back"
        return {"supported": True, "error": f"auto-fill skipped ({why}): type the details",
                "job_uid": parsed["uid"], "fields": bare}
    board = conn.execute("SELECT company FROM boards WHERE board = ?", (f"{parsed['ats']}:{parsed['key']}",)).fetchone()
    return {"supported": True, "from": posting["ats"], "job_uid": posting.get("uid") or parsed["uid"],
            "fields": {"company": board["company"] if board else posting.get("company"),
                       "role_title": posting.get("title"),
                       "job_url": posting.get("url") or url, "apply_url": posting.get("apply_url") or url,
                       "location": posting.get("location"), "workplace": posting.get("workplace"),
                       "salary": _salary_text(posting.get("salary_min"), posting.get("salary_max")),
                       "employment_type": posting.get("employment_type")}}


ANSWERED_KINDS = ("rejected", "screen", "interview", "offer")
MAIL_GONE = ("dismissed", "undone", "ignored")


def _mail_by_app(conn, pid: str, app_ids: list[int]) -> dict[int, dict]:
    """app id -> {'answered': the company answered by email, 'latest': its newest matched email (kind, day, subject,
    link)}. Emails the person dismissed or undid do not count."""
    if not app_ids:
        return {}
    out: dict[int, dict] = {}
    for at in range(0, len(app_ids), 500):
        part = app_ids[at:at + 500]
        rows = conn.execute(
            "SELECT app_id, kind, sent_at, subject, link FROM mail_messages "
            f"WHERE profile = ? AND app_id IN ({','.join('?' * len(part))}) "
            f"AND state NOT IN ({','.join('?' * len(MAIL_GONE))}) ORDER BY sent_at", (pid, *part, *MAIL_GONE))
        for r in rows:
            got = out.setdefault(r["app_id"], {"answered": False, "latest": None})
            got["answered"] |= r["kind"] in ANSWERED_KINDS
            got["latest"] = {"kind": r["kind"], "day": (r["sent_at"] or "")[:10], "subject": r["subject"], "link": r["link"]}
    return out


def followups(conn, pid: str, today: date | None = None, count_only: bool = False) -> dict:
    """Follow-ups overdue / due today (and due in the next UPCOMING_DAYS), newest application first (user,
    2026-10-16). A company that already answered (a read email matched to the row says rejected / screen /
    interview / offer, not dismissed or undone) needs no follow-up; each row carries its latest matched email.
    count_only: just {"due_count"} for the header badge (the full answer is ~1 MB with many applications)."""
    today = today or date.today()
    found = [dict(r) for r in conn.execute(_ROW_SQL + " AND a.status = 'applied' AND a.applied_on >= ? "
                                           "ORDER BY a.applied_on DESC, a.id DESC",
                                           (pid, (today - timedelta(days=FOLLOWUP_WINDOW_DAYS)).isoformat()))]
    mail = _mail_by_app(conn, pid, [r["id"] for r in found])
    found = [r for r in found if not mail.get(r["id"], {}).get("answered")]
    people = contacts.for_apps(conn, [r["id"] for r in found])
    soon = (today + timedelta(days=UPCOMING_DAYS)).isoformat()
    out = {"overdue": [], "today": [], "upcoming": []}
    for app in found:
        row = {k: v for k, v in view(app, people[app["id"]], today).items() if k != "jd"}   # ~5 KB a row
        row["mail"] = mail.get(app["id"], {}).get("latest")
        pending = [d for d in (row["fu1"], row["fu2"]) if d and d["state"] != "done"]
        for bucket in ("overdue", "today", "upcoming"):
            if any(d["state"] == bucket and (bucket != "upcoming" or d["due"] <= soon) for d in pending):
                out[bucket].append(row)
                break
    due = {"due_count": len(out["overdue"]) + len(out["today"])}
    return due if count_only else {**out, **due}


def followup_done(conn, pid: str, app_id: int, n: int, today: date | None = None) -> dict:
    """Mark follow-up 1 or 2 sent: stamps the date into the tracker's Follow-Up cell."""
    if n not in (1, 2):
        raise ValueError("follow-up must be 1 or 2")
    _own(conn, pid, app_id)
    tracker.edit_field(conn, app_id, f"follow_up_{n}", (today or date.today()).strftime("%m/%d/%Y"))
    return get(conn, pid, app_id, today)
