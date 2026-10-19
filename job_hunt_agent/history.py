"""Per-row edit history: who changed which tracker cell, when, old -> new, for edits made in the app,
pulled from the Google Sheet, or undone. One user action (a Save, an inline cell edit, a contacts save) is one batch;
Undo reverts the person's last batch.

record() is called inside the writer's own transaction (it never opens one), so a change and its history row land
together. The batch and the person come from action(); outside one (CLI, sync pulls) each record is its own batch.
"""
import json
import threading
import uuid
from contextlib import contextmanager

from . import db

SHEET = "Google Sheet"
CLI = "job-hunt-agent"
EMAIL = "Email"                     # a status read from a job email (its own Undo in "Updates from email")
COLD = "Cold rule"                  # no reply N days after applying
AUTOMATIC = (CLI, EMAIL, COLD)      # never the person's own edit for the tracker's Undo
SOURCES = ("app", "sheet", "undo")
MAX_VALUE = 50_000
LIST_LIMIT = 50
_local = threading.local()


@contextmanager
def action(who: str, source: str = "app", own: bool = False):
    """Group every change made inside into one batch credited to `who` (re-entrant: the outer action wins, unless
    `own`: an automatic change, e.g. an email read during the person's "Check now", keeps its own batch and author)."""
    outer = (getattr(_local, "batch", None), getattr(_local, "who", None), getattr(_local, "source", None))
    if outer[0] and not own:
        yield outer[0]
        return
    _local.batch, _local.who, _local.source = uuid.uuid4().hex[:16], who, source
    try:
        yield _local.batch
    finally:
        _local.batch, _local.who, _local.source = outer


def _text(value) -> str | None:
    if value is None or value == "":
        return None
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text[:MAX_VALUE]


def record(conn, app_id: int, field: str, old, new, source: str = "app") -> bool:
    """One history row when the value really changed; True if written. Call inside the writer's transaction."""
    before, after = _text(old), _text(new)
    if before == after:
        return False
    batch = getattr(_local, "batch", None)
    if batch and source != "sheet":
        source, who = _local.source or source, _local.who
    else:
        who = None
    who = who or (SHEET if source == "sheet" else CLI)
    profile = conn.execute("SELECT profile FROM applications WHERE id = ?", (app_id,)).fetchone()
    if profile is None:
        return False
    conn.execute("INSERT INTO edit_history(app_id, profile, field, old_value, new_value, at, who, source, batch) "
                 "VALUES (?,?,?,?,?,?,?,?,?)", (app_id, profile["profile"], field, before, after,
                                                 db.iso(db.utcnow()), who, source,
                                                 batch if batch and source != "sheet" else uuid.uuid4().hex[:16]))
    return True


def for_app(conn, app_id: int, limit: int = LIST_LIMIT) -> list[dict]:
    """The row's history, newest first."""
    return [dict(r) for r in conn.execute(
        "SELECT id, field, old_value, new_value, at, who, source, batch, undone_by FROM edit_history "
        "WHERE app_id = ? ORDER BY id DESC LIMIT ?", (app_id, limit))]


def last_batch(conn, pid: str, app_id: int | None = None) -> list[dict]:
    """The person's most recent app edit that is not undone yet (all its rows, oldest first); [] if none. What
    job-hunt-agent did on its own (backfill, JD format, a status from an email, the Cold rule: AUTOMATIC) is never the
    person's edit to undo."""
    where, params = ("AND app_id = ?", [app_id]) if app_id else ("", [])
    marks = ",".join("?" * len(AUTOMATIC))
    row = conn.execute(f"SELECT batch FROM edit_history WHERE profile = ? AND source = 'app' AND who NOT IN ({marks}) "
                       f"AND undone_by IS NULL {where} ORDER BY id DESC LIMIT 1", [pid, *AUTOMATIC, *params]).fetchone()
    if row is None:
        return []
    return [dict(r) for r in conn.execute("SELECT * FROM edit_history WHERE batch = ? ORDER BY id", (row["batch"],))]


def batches_back_to(conn, pid: str, app_id: int, field: str, value: str, limit: int) -> int:
    """How many of the row's newest undoable batches (the person's own) reach back to the one that set `field` to
    `value`, counting it; 0 when it is not among the newest `limit`."""
    marks = ",".join("?" * len(AUTOMATIC))
    rows = conn.execute(f"SELECT batch, field, new_value FROM edit_history WHERE profile = ? AND app_id = ? "
                        f"AND source = 'app' AND who NOT IN ({marks}) AND undone_by IS NULL ORDER BY id DESC",
                        [pid, app_id, *AUTOMATIC]).fetchall()
    seen = []
    for r in rows:
        if r["batch"] not in seen:
            if len(seen) == limit:
                return 0
            seen.append(r["batch"])
        if r["field"] == field and r["new_value"] == value:
            return len(seen)
    return 0


def mark_undone(conn, batch: str, by: str) -> None:
    with db.tx(conn):
        conn.execute("UPDATE edit_history SET undone_by = ? WHERE batch = ?", (by, batch))
