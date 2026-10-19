""""Apply on laptop". A job seen on the phone (button or swipe) waits in the person's Apply queue; on the
laptop "Apply queue (N)" lists them and "Open next 5" opens their apply pages in new tabs, each tab told its job (the
extension relays it, so Autofill knows the job without a pick). A job leaves the queue once it is applied, however
it was marked (job-hunt-agent, the extension's confirmation page, the Sheet), or when it is removed."""
from datetime import datetime

from . import db
from .applied import APPLIED_STATES

PREFIX = "/api/applyq"
OPEN_N = 5
MAX_OPEN = 10
MAX_QUEUE = 200


def _row(conn, pid: str, qid: int):
    row = conn.execute("SELECT * FROM apply_queue WHERE id = ? AND profile = ?", (int(qid), pid)).fetchone()
    if row is None:
        raise KeyError(qid)
    return row


def add(conn, pid: str, uid: str | None, app: int | None, now: datetime) -> dict:
    """Queue a job (or an outside application); queuing it again brings it back to the end, unopened."""
    uid = (str(uid).strip() or None) if uid else None
    app = int(app) if app else None
    if not uid and not app:
        raise ValueError("uid or app is required")
    if uid and conn.execute("SELECT 1 FROM jobs WHERE uid = ?", (uid,)).fetchone() is None:
        raise KeyError(uid)
    if app and conn.execute("SELECT 1 FROM applications WHERE id = ? AND profile = ?", (app, pid)).fetchone() is None:
        raise KeyError(app)
    if conn.execute("SELECT count(*) FROM apply_queue WHERE profile = ? AND done_at IS NULL", (pid,)).fetchone()[0] >= MAX_QUEUE:
        raise ValueError(f"the queue holds at most {MAX_QUEUE} jobs")
    with db.tx(conn):
        conn.execute("INSERT INTO apply_queue(profile, job_uid, app_id, added_at) VALUES (?,?,?,?) "
                     "ON CONFLICT(profile, coalesce(job_uid, ''), coalesce(app_id, 0)) DO UPDATE SET "
                     "added_at = excluded.added_at, opened_at = NULL, done_at = NULL", (pid, uid, app, db.iso(now)))
    qid = conn.execute("SELECT id FROM apply_queue WHERE profile = ? AND coalesce(job_uid, '') = ? AND coalesce(app_id, 0) = ?",
                       (pid, uid or "", app or 0)).fetchone()["id"]
    return {"id": qid, "count": count(conn, pid, now)}


def items(conn, pid: str, now: datetime) -> list[dict]:
    """The open queue, oldest first; one applied since it was queued is done (and left out)."""
    rows = conn.execute(
        "SELECT q.id, q.job_uid, q.app_id, q.added_at, q.opened_at, coalesce(j.company, a.company) AS company, "
        "coalesce(j.title, a.role_title) AS title, coalesce(j.apply_url, j.url, a.apply_url, a.job_url) AS url, "
        "a.id AS app, a.status FROM apply_queue q LEFT JOIN jobs j ON j.uid = q.job_uid "
        "LEFT JOIN applications a ON a.profile = q.profile AND (a.id = q.app_id OR (q.app_id IS NULL AND a.job_uid = q.job_uid)) "
        "WHERE q.profile = ? AND q.done_at IS NULL ORDER BY q.added_at, q.id", (pid,)).fetchall()
    applied = [r["id"] for r in rows if r["status"] in APPLIED_STATES]
    if applied:
        with db.tx(conn):
            conn.executemany("UPDATE apply_queue SET done_at = ? WHERE id = ?", [(db.iso(now), i) for i in applied])
    return [{"id": r["id"], "uid": r["job_uid"], "app": r["app"] or r["app_id"], "company": r["company"] or "",
             "title": r["title"] or "", "url": r["url"] or "", "added_at": r["added_at"], "opened_at": r["opened_at"]}
            for r in rows if r["id"] not in applied]


def count(conn, pid: str, now: datetime) -> int:
    return len(items(conn, pid, now))


def take(conn, pid: str, n: int, now: datetime) -> list[dict]:
    """The next n to open (never opened first, then the longest since opened); they are marked opened."""
    n = max(1, min(MAX_OPEN, int(n or OPEN_N)))
    pick = sorted((i for i in items(conn, pid, now) if i["url"]), key=lambda i: (i["opened_at"] or "", i["added_at"]))[:n]
    with db.tx(conn):
        conn.executemany("UPDATE apply_queue SET opened_at = ? WHERE id = ?", [(db.iso(now), i["id"]) for i in pick])
    return pick


def remove(conn, pid: str, qid: int, now: datetime) -> dict:
    _row(conn, pid, qid)
    with db.tx(conn):
        conn.execute("UPDATE apply_queue SET done_at = ? WHERE id = ?", (db.iso(now), int(qid)))
    return {"count": count(conn, pid, now)}


def applied(conn, pid: str, qid: int, now: datetime) -> dict:
    """Mark applied from the queue: the same as Mark applied in job-hunt-agent, and it leaves the queue."""
    from . import api, tracker
    row = _row(conn, pid, qid)
    if row["job_uid"]:
        app = api.mark_applied(conn, pid, row["job_uid"], now)
    else:
        app = row["app_id"]
        tracker.set_status(conn, app, "applied", source="ui")
    remove(conn, pid, qid, now)
    return {"app": app, "count": count(conn, pid, now)}


def route(conn, pid: str, method: str, path: str, args: dict, now: datetime):
    name = path[len(PREFIX):].strip("/")
    if method == "GET" and name == "":
        got = items(conn, pid, now)
        return {"items": got, "count": len(got)}
    if method != "POST":
        raise KeyError(path)
    if name == "add":
        return add(conn, pid, args.get("uid"), args.get("app"), now)
    if name == "open":
        return {"items": take(conn, pid, args.get("n") or OPEN_N, now)}
    if name == "remove":
        return remove(conn, pid, int(args["id"]), now)
    if name == "applied":
        return applied(conn, pid, int(args["id"]), now)
    raise KeyError(path)
