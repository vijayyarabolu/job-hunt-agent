"""Vendor / recruiter contacts (Jordan's workbook "Sheet2"): a per-profile list kept in the DB. The first
time, the list written by the mapped import (profiles/<p>/vendors.json, which stays as it is) is adopted; a later
mapped import replaces the list only while nobody has edited it in the app (origin 'app')."""
import json

from . import config, db

FIELDS = ("company", "target_name", "target_role", "linkedin", "email", "email_sent", "notes")
MAX_VENDORS = 2000
MAX_FIELD = 500


def _json_path(pid: str):
    return config.PROFILES / pid / "vendors.json"


def _clean(entry: dict) -> dict:
    if not isinstance(entry, dict):
        raise ValueError("each vendor must be an object")
    return {f: str(entry.get(f) or "").strip()[:MAX_FIELD] or None for f in FIELDS}


def _replace(conn, pid: str, entries: list[dict], origin: str) -> None:
    now = db.iso(db.utcnow())
    conn.execute("INSERT OR IGNORE INTO profiles(id, name) VALUES (?, ?)", (pid, pid))
    conn.execute("DELETE FROM vendors WHERE profile = ?", (pid,))
    for pos, entry in enumerate(entries):
        conn.execute(f"INSERT INTO vendors(profile, pos, {', '.join(FIELDS)}, origin, updated_at) "
                     f"VALUES (?,?,{','.join('?' * len(FIELDS))},?,?)", (pid, pos, *entry.values(), origin, now))


def adopt_json(conn) -> int:
    """Load every profile's vendors.json into an empty vendor list (idempotent). Returns vendors adopted."""
    done = 0
    for pid in config.profile_ids():
        path = _json_path(pid)
        if not path.exists() or conn.execute("SELECT 1 FROM vendors WHERE profile = ?", (pid,)).fetchone():
            continue
        entries = [_clean(e) for e in json.loads(path.read_text() or "[]")]
        with db.tx(conn):
            _replace(conn, pid, entries, "import")
        done += len(entries)
    return done


def from_import(conn, pid: str, entries: list[dict]) -> bool:
    """A mapped import's vendor sheet: replaces the list unless it was edited in the app. True when replaced."""
    if conn.execute("SELECT 1 FROM vendors WHERE profile = ? AND origin = 'app'", (pid,)).fetchone():
        return False
    with db.tx(conn):
        _replace(conn, pid, [_clean(e) for e in entries], "import")
    return True


def listing(conn, pid: str) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM vendors WHERE profile = ? ORDER BY pos, id", (pid,))]


def save(conn, pid: str, entries: list[dict], clear: bool = False) -> list[dict]:
    """Replace the profile's vendor list with what the UI sends (the whole list, in order). Emptying a list that
    has vendors needs clear=True, so a stale tab or a client bug cannot wipe it."""
    if not isinstance(entries, list) or len(entries) > MAX_VENDORS:
        raise ValueError(f"vendors must be a list of at most {MAX_VENDORS}")
    rows = [e for e in (_clean(e) for e in entries) if any(e.values())]
    if not rows and not clear and listing(conn, pid):
        raise ValueError("refusing to delete every vendor without clear")
    with db.tx(conn):
        _replace(conn, pid, rows, "app")
    return listing(conn, pid)
