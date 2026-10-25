"""GOAL35 §2: an ATS outage is not a dead board.

On 2026-10-21 from 14:03 to 17:42 UTC BambooHR answered 404 for every company's careers list. Each answer raised that
board's dead_streak, which puts a board on the weekly schedule (tiers.DEAD_INTERVAL), so 1,933 BambooHR boards (14,303
listed jobs) were not asked again for a week while they answered 200 again the same evening. The old guard only
looked per host, and every BambooHR company has its own host (acme.bamboohr.com), so it never saw the outage.

- collapsed(): >= 80 % of >= 3 boards failed on one host OR one rate bucket (bamboohr.com, wd5.myworkdayjobs.com).
- forgive(): those boards' failures count against nothing: fail_streak and dead_streak go back down, and a board this
  run dropped as dead (4 straight 404s) is taken back.
- heal(): a board still on the dead schedule whose last answer was a 404 inside such an outage goes back to its normal
  schedule (run at the start of every fetch run, so outages missed before this fix heal by themselves).
"""
from collections import defaultdict
from typing import Callable, NamedTuple

from . import db

GONE = (404, 410)
FAILED = ("http_error", "skipped")
MIN_BOARDS = 3
FAIL_RATIO = 0.8


class Outcome(NamedTuple):
    board: str
    status: str
    http_status: int | None
    host: str
    bucket: str


def _collapsed(statuses: list[str]) -> bool:
    return len(statuses) >= MIN_BOARDS and sum(s in FAILED for s in statuses) / len(statuses) >= FAIL_RATIO


def collapsed(outcomes: list[Outcome]) -> tuple[list[str], dict[str, int | None]]:
    """(alerts, board -> its http status) for the failed boards of every host or bucket that collapsed."""
    groups: dict[str, list[Outcome]] = defaultdict(list)
    for o in outcomes:
        groups[o.host].append(o)
        if o.bucket != o.host:
            groups[o.bucket].append(o)
    alerts, failed = [], {}
    for name, rows in groups.items():
        if _collapsed([o.status for o in rows]):
            bad = [o for o in rows if o.status in FAILED]
            alerts.append(f"{name}: {len(bad)}/{len(rows)} boards failed")
            failed.update({o.board: o.http_status for o in bad})
    return alerts, failed


def forgive(conn, failed: dict[str, int | None]) -> None:
    """The run's failures inside an outage count against no board."""
    with db.tx(conn):
        for board, http_status in failed.items():
            conn.execute("UPDATE boards SET fail_streak=max(0, fail_streak-1) WHERE board=?", (board,))
            if http_status in GONE:
                conn.execute("UPDATE boards SET dead_streak=max(0, dead_streak-1), "
                             "active=CASE WHEN dropped LIKE 'dead:%' THEN 1 ELSE active END, "
                             "dropped_at=CASE WHEN dropped LIKE 'dead:%' THEN NULL ELSE dropped_at END, "
                             "dropped=CASE WHEN dropped LIKE 'dead:%' THEN NULL ELSE dropped END WHERE board=?", (board,))


def _outage_buckets(conn, run_id: int, bucket_of: Callable[[dict], str]) -> set[str]:
    rows = conn.execute("SELECT f.board, f.status, b.ats, b.key FROM fetch_runs f JOIN boards b ON b.board = f.board "
                        "WHERE f.run_id = ?", (run_id,)).fetchall()
    by_bucket: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        by_bucket[bucket_of({"ats": r["ats"], "key": r["key"], "board": r["board"]})].append(r["status"])
    return {b for b, statuses in by_bucket.items() if _collapsed(statuses)}


def heal(conn, bucket_of: Callable[[dict], str]) -> int:
    """Boards a past outage left on the dead schedule go back to their normal one. Returns how many."""
    last = {}
    for b in conn.execute("SELECT board, ats, key FROM boards WHERE dead_streak > 0 AND active = 1").fetchall():
        row = conn.execute("SELECT run_id, http_status FROM fetch_runs WHERE board = ? ORDER BY run_id DESC LIMIT 1",
                           (b["board"],)).fetchone()
        if row and row["http_status"] in GONE:
            last[b["board"]] = (row["run_id"], bucket_of({"ats": b["ats"], "key": b["key"], "board": b["board"]}))
    outages = {run: _outage_buckets(conn, run, bucket_of) for run in {run for run, _ in last.values()}}
    healed = [board for board, (run, bucket) in last.items() if bucket in outages[run]]
    with db.tx(conn):
        conn.executemany("UPDATE boards SET dead_streak=0 WHERE board=?", [(b,) for b in healed])
    return len(healed)
