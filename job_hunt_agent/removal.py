"""× Remove: a job or a tracker row taken out for good (a duplicate, junk), with Undo.

A removed feed job is hidden from every list (feed tabs, Archived, Resumes) and never comes back from a fetch: its
uid and its job key (company + title + place) are kept, so the same posting fetched again, or posted again under a
new id, stays hidden. A removed tracker row is deleted with everything that hangs off it; the whole row is kept as a
snapshot here so Undo puts it back exactly (same id). Its Sheet row is deleted by the next sync once the Undo toast
is over (SHEET_GRACE_S); an Undo after that puts the row back in the app and the sync adds it to the Sheet again.
The table is created idempotently here (as feedback.DDL), not by a db.py migration."""
import json
import re
from datetime import datetime, timedelta

from . import applied, db
from .google import state as gstate

DDL = """CREATE TABLE IF NOT EXISTS removed (
  id INTEGER PRIMARY KEY, profile TEXT NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('job', 'app')),
  uid TEXT, job_key TEXT, app_id INTEGER, label TEXT, why TEXT, snapshot TEXT, at TEXT NOT NULL,
  sheet_tab TEXT, sheet_done_at TEXT, undone_at TEXT);
CREATE INDEX IF NOT EXISTS removed_profile ON removed(profile, undone_at);"""
SHEET_GRACE_S = 12                     # the Undo toast shows 8 s; the Sheet row goes only after it
WHY = ("duplicate", "unwanted")
# records kept when a row goes (the paid contact lookups: never paid twice; the mail history): unlinked, not deleted,
# and linked again by Undo
UNLINK = ("contact_lookups", "mail_messages")
_WORD = re.compile(r"[a-z0-9]+")


def ensure(conn) -> None:
    conn.executescript(DDL)


def job_key(company: str | None, title: str | None, location: str | None) -> str:
    """The same posting under any id: company key | title words | first place's words."""
    place = re.split(r"[;|]", location or "")[0]
    return "|".join((applied.company_key(company), " ".join(_WORD.findall((title or "").lower())),
                     " ".join(_WORD.findall(place.lower()))))


def _child_tables(conn) -> list[str]:
    """Every table whose rows belong to an application (a foreign key on app_id)."""
    names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
    return [t for t in names if any(fk[2] == "applications" and fk[3] == "app_id"
                                    for fk in conn.execute(f"PRAGMA foreign_key_list({t})"))]


def _hide_job(conn, pid: str, uid: str, why: str, now: datetime, label: str | None = None) -> int:
    job = conn.execute("SELECT company, title, location FROM jobs WHERE uid = ?", (uid,)).fetchone()
    if job is None:
        raise KeyError(uid)
    # a duplicate goes by its own id only: its job key is the kept copy's too (dupes.py), which must stay
    key = None if why == "duplicate" else job_key(job["company"], job["title"], job["location"])
    cur = conn.execute("INSERT INTO removed(profile, kind, uid, job_key, label, why, at) VALUES (?,?,?,?,?,?,?)",
                       (pid, "job", uid, key,
                        label or f"{job['company']} · {job['title']}", why, db.iso(now)))
    return cur.lastrowid


def _why(why: str) -> str:
    if why not in WHY:
        raise ValueError(f"why must be one of {WHY}")
    return why


def remove_job(conn, pid: str, uid: str, why: str = "unwanted", now: datetime | None = None) -> dict:
    """A feed job hidden for good (a tracker row of it, if any, stays: remove that one from its own list)."""
    _why(why)
    ensure(conn)
    with db.tx(conn):
        rid = _hide_job(conn, pid, uid, why, now or db.utcnow())
    return {"removed": rid, "uid": uid}


def remove_app(conn, pid: str, app_id: int, why: str = "unwanted", now: datetime | None = None) -> dict:
    """A tracker row deleted with what hangs off it, kept whole for Undo; its posting is hidden too."""
    _why(why)
    ensure(conn)
    now = now or db.utcnow()
    app = conn.execute("SELECT * FROM applications WHERE id = ? AND profile = ?", (app_id, pid)).fetchone()
    if app is None:
        raise KeyError(app_id)
    tables = _child_tables(conn)
    children = {t: [dict(r) for r in conn.execute(f"SELECT * FROM {t} WHERE app_id = ?", (app_id,))] for t in tables}
    tab = (children.get("gsync_rows") or [{}])[0].get("tab")
    label = f"{app['company'] or '(company)'} · {app['role_title'] or '(role)'}"
    with db.tx(conn):
        cur = conn.execute("INSERT INTO removed(profile, kind, uid, app_id, label, why, snapshot, at, sheet_tab) "
                           "VALUES (?,?,?,?,?,?,?,?,?)",
                           (pid, "app", app["job_uid"], app_id, label, why,
                            json.dumps({"app": dict(app), "children": children}), db.iso(now), tab))
        if app["job_uid"] and conn.execute("SELECT 1 FROM jobs WHERE uid = ?", (app["job_uid"],)).fetchone():
            _hide_job(conn, pid, app["job_uid"], why, now, label)
        for t in tables:
            verb = "UPDATE {} SET app_id = NULL" if t in UNLINK else "DELETE FROM {}"
            conn.execute(verb.format(t) + " WHERE app_id = ?", (app_id,))
        conn.execute("DELETE FROM applications WHERE id = ?", (app_id,))
        gstate.mark_dirty(conn)
    return {"removed": cur.lastrowid, "app": app_id, "sheet": bool(tab)}


def _insert(conn, table: str, row: dict) -> None:
    cols = list(row)
    conn.execute(f"INSERT INTO {table}({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                 [row[c] for c in cols])


def undo(conn, pid: str, removed_id: int, now: datetime | None = None) -> dict:
    """Put back one removal (and the job hidden with a row). A row whose Sheet row is already deleted comes back
    without its sync snapshot, so the sync adds it to the Sheet again."""
    ensure(conn)
    rec = conn.execute("SELECT * FROM removed WHERE id = ? AND profile = ? AND undone_at IS NULL",
                       (removed_id, pid)).fetchone()
    if rec is None:
        raise KeyError(removed_id)
    stamp = db.iso(now or db.utcnow())
    with db.tx(conn):
        if rec["kind"] == "app":
            snap = json.loads(rec["snapshot"])
            _insert(conn, "applications", snap["app"])
            for table, rows in snap["children"].items():
                if table == "gsync_rows" and rec["sheet_done_at"]:
                    continue                                    # the Sheet row went (or is going): the sync adds it again
                for row in rows:
                    if table in UNLINK:
                        conn.execute(f"UPDATE {table} SET app_id = ? WHERE id = ? AND app_id IS NULL", (rec["app_id"], row["id"]))
                    else:
                        _insert(conn, table, row)
            conn.execute("UPDATE removed SET undone_at = ? WHERE profile = ? AND kind = 'job' AND uid = ? "
                         "AND undone_at IS NULL AND at = ?", (stamp, pid, rec["uid"], rec["at"]))
            gstate.mark_dirty(conn)
        conn.execute("UPDATE removed SET undone_at = ? WHERE id = ?", (stamp, removed_id))
    snap = json.loads(rec["snapshot"] or "{}") if rec["kind"] == "job" else {}
    return {"undone": removed_id, "app": rec["app_id"], "uid": rec["uid"], "rg": snap.get("rg", []),
            "rg_requeue": snap.get("rg_requeue", [])}


def note_resumes(conn, removed_id: int, rg_ids: list[int], requeue: list[int] = ()) -> None:
    """the resumegen resumes archived with a removed job (and the ones it cancelled), for Undo."""
    with db.tx(conn):
        conn.execute("UPDATE removed SET snapshot = ? WHERE id = ? AND kind = 'job'",
                     (json.dumps({"rg": rg_ids, "rg_requeue": list(requeue)}), removed_id))


def hidden(conn, pid: str) -> tuple[set[str], set[str]]:
    """(removed uids, removed job keys) of the person."""
    ensure(conn)
    rows = conn.execute("SELECT uid, job_key FROM removed WHERE profile = ? AND kind = 'job' AND undone_at IS NULL",
                        (pid,)).fetchall()
    return {r["uid"] for r in rows if r["uid"]}, {r["job_key"] for r in rows if r["job_key"]}


def keep(conn, pid: str, items: list, job_of=lambda item: item) -> list:
    """The items (jobs, or (job, score) pairs with job_of) whose job is not removed: by uid, or by job key."""
    uids, keys = hidden(conn, pid)
    if not uids:
        return items
    out = []
    for item in items:
        job = job_of(item)
        if job["uid"] in uids or job_key(job["company"], job["title"], job["location"]) in keys:
            continue
        out.append(item)
    return out


def removed_apps(conn, pid: str) -> set[int]:
    """Tracker rows removed and not put back: a Sheet row still carrying their key is not taken in again."""
    ensure(conn)
    return {r[0] for r in conn.execute("SELECT app_id FROM removed WHERE profile = ? AND kind = 'app' "
                                       "AND undone_at IS NULL", (pid,))}


def readd(conn, pid: str) -> set[int]:
    """Rows put back after their Sheet row was deleted: the sync adds them to the Sheet again (any source)."""
    ensure(conn)
    return {r[0] for r in conn.execute("SELECT r.app_id FROM removed r JOIN applications a ON a.id = r.app_id "
                                       "WHERE r.profile = ? AND r.kind = 'app' AND r.undone_at IS NOT NULL "
                                       "AND r.sheet_done_at IS NOT NULL AND r.app_id NOT IN "
                                       "(SELECT app_id FROM gsync_rows WHERE profile = ?)", (pid, pid))}


def sheet_deletes(conn, pid: str, title: str, now: datetime | None = None) -> list[int]:
    """Removed rows of this tab whose Sheet row is to go now (the Undo toast is over)."""
    ensure(conn)
    return _pending(conn, pid, title, now)


def _pending(conn, pid: str, title: str, now: datetime | None) -> list[int]:
    before = db.iso((now or db.utcnow()) - timedelta(seconds=SHEET_GRACE_S))
    return [r[0] for r in conn.execute("SELECT app_id FROM removed WHERE profile = ? AND kind = 'app' AND sheet_tab = ? "
                                       "AND sheet_done_at IS NULL AND undone_at IS NULL AND at <= ?",
                                       (pid, title, before))]


def claim_sheet_deletes(conn, pid: str, title: str, now: datetime | None = None) -> tuple[list[int], str]:
    """The rows whose Sheet row the sync deletes now, marked done in one transaction first: an Undo after this sees
    sheet_done_at and puts the row back without its sync snapshot, so the next sync adds it to the Sheet again (no
    Undo is lost to a delete already on its way). Returns (app ids, the claim's stamp for release_claim)."""
    ensure(conn)                                                # (its DDL would end the transaction below)
    stamp = db.iso(now or db.utcnow())
    with db.tx(conn):
        ids = _pending(conn, pid, title, now)
        for app_id in ids:
            conn.execute("UPDATE removed SET sheet_done_at = ? WHERE profile = ? AND kind = 'app' AND app_id = ? "
                         "AND sheet_done_at IS NULL AND undone_at IS NULL", (stamp, pid, app_id))
    return ids, stamp


def release_claim(conn, pid: str, app_ids: list[int], stamp: str) -> None:
    """The Sheet refused the delete: the rows are deleted on a later sync instead. A row put back meanwhile keeps
    its claim: whether or not its Sheet row went, the sync adds it again only when no Sheet row carries its key."""
    with db.tx(conn):
        for app_id in app_ids:
            conn.execute("UPDATE removed SET sheet_done_at = NULL WHERE profile = ? AND kind = 'app' AND app_id = ? "
                         "AND sheet_done_at = ? AND undone_at IS NULL", (pid, app_id, stamp))
