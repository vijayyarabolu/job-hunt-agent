"""One health(conn) for `doctor`, the UI header banner and the digest (decision 001; decision 002 amendments 2, 4, 7).

Per-ATS status of the last run, boards failing 3+ times in a row, staleness of the fetcher, pending fits, pushes
per profile per day, and feature drift (share of JDs / salaries / req ids per ATS vs the week before).
"""
import logging
import threading
import time
from datetime import datetime, timedelta

from . import config, db, fits, fresh
from .db import iso

STALE_AFTER = timedelta(hours=2)
FAILING_STREAK = 3
DRIFT_DROP = 0.30
DRIFT_MIN_JOBS = 20
VOLUME_DAYS = 7
# each open tab asks once a minute and the drift scan reads every stored job (2-5 s live); it and the open-job
# count only move when a fetch run or a new job lands, so they are kept until then, and never longer than KEEP_S
KEEP_S = 600
KEEP_MARK_SQL = ("SELECT (SELECT max(id) FROM runs), (SELECT finished_at FROM runs ORDER BY id DESC LIMIT 1), "
                 "(SELECT max(rowid) FROM jobs)")
_KEPT: dict[str, tuple[tuple, dict]] = {}       # db file -> (mark, {"drift", "open_jobs"})
# when those keeps run out, the pending-fit count and the drift scan read the whole 1.6 GB table (10-20 s live),
# so /api/health answers the last result at once and refreshes it behind, at most once per SNAP_FRESH_S
SNAP_FRESH_S = 30
_SNAP: dict[str, dict] = {}                       # db file -> {"data", "at", "thread"}
_SNAP_LOCK = threading.Lock()
log = logging.getLogger(__name__)


def _last_run(conn) -> dict | None:
    row = conn.execute("SELECT * FROM runs WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def _by_ats(conn, run_id: int) -> dict:
    rows = conn.execute(
        "SELECT substr(board, 1, instr(board, ':') - 1) AS ats, status, count(*) AS n, sum(n_jobs) AS jobs, "
        "sum(n_new) AS new FROM fetch_runs WHERE run_id = ? GROUP BY 1, 2", (run_id,)).fetchall()
    out: dict = {}
    for r in rows:
        entry = out.setdefault(r["ats"], {"jobs": 0, "new": 0})
        entry[r["status"]] = r["n"]
        entry["jobs"] += r["jobs"] or 0
        entry["new"] += r["new"] or 0
    return out


# GOAL30-app: covered by the jobs_seen index (3.5 s over every job row -> ~25 ms on the 1.2M-job DB)
DRIFT_SQL = ("SELECT substr(board, 1, instr(board, ':') - 1) AS ats, first_seen >= ? AS recent, count(*) AS n, "
             "avg(description IS NOT NULL) AS jd, avg(salary_max IS NOT NULL) AS salary, avg(req_id IS NOT NULL) AS req "
             "FROM jobs WHERE families <> ',' AND first_seen >= ? GROUP BY 1, 2")


def drift(conn, now: datetime) -> list[str]:
    """Matched jobs first seen in the last 24 h vs the 7 days before: flag a > 30 % drop in JD/salary/req-id share."""
    day, week = iso(now - timedelta(days=1)), iso(now - timedelta(days=8))
    rows = conn.execute(DRIFT_SQL, (day, week)).fetchall()
    stats = {(r["ats"], r["recent"]): r for r in rows}
    alerts = []
    for (ats, recent), current in stats.items():
        before = stats.get((ats, 0))
        if not recent or not before or current["n"] < DRIFT_MIN_JOBS or before["n"] < DRIFT_MIN_JOBS:
            continue
        for name in ("jd", "salary", "req"):
            if before[name] and current[name] < before[name] * (1 - DRIFT_DROP):
                alerts.append(f"{ats}: {name} share {current[name]:.0%} (was {before[name]:.0%})")
    return alerts


def pushes_per_day(conn, profile_id: str, now: datetime) -> float:
    """Average pushed jobs per day over the last week (decision 002 amendment 4: target 3-15)."""
    n = conn.execute("SELECT count(*) FROM notifications WHERE profile = ? AND channel = 'push' AND sent_at >= ?",
                     (profile_id, iso(now - timedelta(days=VOLUME_DAYS)))).fetchone()[0]
    return round(n / VOLUME_DAYS, 1)


def _job_totals(conn, now: datetime) -> dict:
    mark = (*conn.execute(KEEP_MARK_SQL).fetchone(), int(time.time() // KEEP_S))
    key = conn.execute("PRAGMA database_list").fetchone()[2]
    kept = _KEPT.get(key)
    if kept and kept[0] == mark:
        return kept[1]
    totals = {"open_jobs": conn.execute("SELECT count(*) FROM jobs WHERE closed_at IS NULL").fetchone()[0],
              "drift": drift(conn, now)}
    _KEPT[key] = (mark, totals)
    return totals


def health(conn, now: datetime) -> dict:
    last = _last_run(conn)
    ok_at = conn.execute("SELECT max(last_ok_at) FROM boards WHERE active = 1").fetchone()[0]
    ago = (now - fresh.parse(ok_at)) if ok_at else None
    failing = [dict(r) for r in conn.execute(           # a board answering 404/410 is on the dead path instead
        "SELECT board, company, fail_streak FROM boards WHERE active = 1 AND review = 0 AND fail_streak >= ? "
        "AND dead_streak = 0 ORDER BY fail_streak DESC", (FAILING_STREAK,))]
    gone = [dict(r) for r in conn.execute(               # decision 005: weekly re-check, dropped after 4 answers
        "SELECT board, company, dead_streak FROM boards WHERE active = 1 AND dead_streak > 0 ORDER BY dead_streak DESC")]
    profiles = config.profiles()
    totals = _job_totals(conn, now)
    return {
        "last_run": last,
        "last_ok_fetch_min": int(ago.total_seconds() // 60) if ago else None,
        "stale": ago is None or ago > STALE_AFTER,
        "by_ats": _by_ats(conn, last["id"]) if last else {},
        "failing": failing,
        "gone": gone,
        "review_boards": conn.execute("SELECT count(*) FROM boards WHERE active = 1 AND review = 1").fetchone()[0],
        "open_jobs": totals["open_jobs"],
        "pending_fits": {p["id"]: fits.pending(conn, p) for p in profiles},
        "pushes_per_day": {p["id"]: pushes_per_day(conn, p["id"], now) for p in profiles},
        "drift": totals["drift"],
    }


def _refresh(key: str, now: datetime) -> None:
    conn = db.connect(key)
    try:
        _SNAP[key].update(data=health(conn, now), at=time.monotonic())
    except Exception:                                # the last answer stays; the next ask tries again
        log.exception("health refresh failed")
    finally:
        conn.close()


def summary(conn, now: datetime, key: str | None = None) -> dict:
    """The UI header's health: the first ask computes it, later asks get the last answer at once (a refresh starts
    behind when it is older than SNAP_FRESH_S)."""
    key = key or conn.execute("PRAGMA database_list").fetchone()[2]
    with _SNAP_LOCK:
        snap = _SNAP.get(key)
        if snap is None:
            snap = _SNAP[key] = {"data": health(conn, now), "at": time.monotonic(), "thread": None}
            return snap["data"]
        old = time.monotonic() - snap["at"] > SNAP_FRESH_S
        if old and not (snap["thread"] and snap["thread"].is_alive()):
            snap["thread"] = threading.Thread(target=_refresh, args=(key, now), daemon=True, name="health-refresh")
            snap["thread"].start()
        return snap["data"]
