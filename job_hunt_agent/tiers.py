"""Tiered fetch schedule (decision 005 B2 step 4). A board is due when now - last_fetch >= its interval:

- tier 1: at least one job with a title match (any profile family), is_us = 1 and first_posted_at in the last 30
  days (decision 005 run 2 (c); never narrowed as a load fallback) -> every 30 min
- tier 2: such a job 30-60 days ago -> every 4 h
- tier 3: none in 60 days (and every new board until its first fetch shows otherwise) -> daily
- dead: the last answer was 404/410 -> weekly re-check; 4 straight 404/410 -> dropped (active=0, history kept)

A never-fetched board is seeded with last_fetch = now - (crc32(board) % interval), which spreads newly admitted
boards over a day instead of fetching thousands at once. Each run takes due boards tier-1 first, then oldest first.
Every board gets one unconditional list request per day (validators dropped), so a stale CDN 304 cannot hide changes.

Workday has a second, daily 'terms' pass (v1's search terms) when the tenant has an open matched job not seen in 3
days: terms reach past the 2,000-posting cap, so those jobs never go stale (skeptic objection 4). Term passes queue
after t3 (priority 4, the 'Workday sweep quota') and keep their own fetch_runs key ('<board>#terms').

robots: a board whose list URL (or, for Workday, its HTML career site, e.g. citi/wd5/2 'Disallow: /2/') is
disallowed is dropped (active=0, dropped='robots: ...', history kept); robots-dropped boards are re-checked daily
and come back when robots.txt allows them again (decision 005 run 2 conditions 4-5).
"""
import zlib
from datetime import datetime, timedelta
from typing import NamedTuple

from . import db, fresh
from .net import ROBOTS_BLOCKED

# t3 is 23 h, not 24: launchd start drift (31-50 min gaps measured) must not push a daily board past 24 h
INTERVALS = {1: timedelta(minutes=30), 2: timedelta(hours=4), 3: timedelta(hours=23)}
DEAD_INTERVAL = timedelta(days=7)
UNDATED_ATS = ("jobvite", "successfactors", "taleo",       # their postings may carry no date
               "bamboohr", "rippling", "jazzhr",           # dated only once a matched posting's detail is read
               "pinpoint")                                 # its feed has no date at all
DEAD_DROP_AFTER = 4
SLACK = timedelta(minutes=10)            # a 30-min launchd cadence must not skip every other tier-1 run
T1_WINDOW, T2_WINDOW = timedelta(days=30), timedelta(days=60)
FULL_EVERY = timedelta(hours=24)
TERMS_EVERY = timedelta(hours=24)
TERMS_WHEN_UNSEEN = timedelta(days=3)
FORCED_TIER: dict[str, int] = {}
MIN_TIER = {"jobscore": 2}              # JobScore asks feed readers to poll at most once an hour
TERMS_ATS = {"workday"}
TERMS_PRIORITY = 4
ROBOTS_RECHECK = timedelta(hours=24)


def tier_of(ats: str, earned: int) -> int:
    """The tier a board runs on: its earned tier, unless its system forces one or sets a floor."""
    return FORCED_TIER.get(ats, max(earned, MIN_TIER.get(ats, 1)))


class Task(NamedTuple):
    board: dict          # registry board + DB row + conditional validators + task kind
    kind: str            # 'list' | 'terms'
    tier: int
    overdue_s: float


def tier_for(newest_match: str | None, now: datetime) -> int:
    moment = fresh.parse(newest_match) if newest_match else None
    if moment and now - moment <= T1_WINDOW:
        return 1
    if moment and now - moment <= T2_WINDOW:
        return 2
    return 3


def newest_us_match(conn, since: str) -> dict[str, str]:
    """board -> newest first_posted_at of a title-matched US job since `since`. A posting with no date at all (
    Jobvite, some SuccessFactors / Taleo sites) counts from when it was first seen, as fresh.is_fresh reads it."""
    out = dict(conn.execute("SELECT board, max(first_posted_at) FROM jobs WHERE families <> ',' AND is_us = 1 "
                            "AND first_posted_at >= ? GROUP BY board", (since,)).fetchall())
    for ats in UNDATED_ATS:                       # board range: the jobs_board index, not a scan of every job
        for board, seen in conn.execute(
                "SELECT board, max(first_seen) FROM jobs WHERE board >= ? AND board < ? AND first_posted_at IS NULL "
                "AND families <> ',' AND is_us = 1 AND first_seen >= ? GROUP BY board", (f"{ats}:", f"{ats};", since)):
            out[board] = max(out.get(board) or "", seen)
    return out


def board_tiers(conn, now: datetime) -> dict[str, int]:
    return {board: tier_for(newest, now) for board, newest in newest_us_match(conn, db.iso(now - T2_WINDOW)).items()}


def interval_for(row: dict, tier: int) -> timedelta:
    return DEAD_INTERVAL if (row.get("dead_streak") or 0) > 0 else INTERVALS[tier]


def seeded_last_fetch(board_id: str, interval: timedelta, now: datetime) -> datetime:
    return now - timedelta(seconds=zlib.crc32(board_id.encode()) % int(interval.total_seconds()))


def _parse(ts: str | None) -> datetime | None:
    return fresh.parse(ts) if ts else None


def needs_terms(conn, now: datetime) -> set[str]:
    """Workday boards with an open matched job unseen for TERMS_WHEN_UNSEEN."""
    cutoff = db.iso(now - TERMS_WHEN_UNSEEN)
    return {r[0] for r in conn.execute(
        "SELECT DISTINCT board FROM jobs WHERE board LIKE 'workday:%' AND closed_at IS NULL AND families <> ',' "
        "AND last_seen < ?", (cutoff,))}


def _recheck_robots(conn, boards: list[dict], rows: dict, now: datetime, urls_of) -> None:
    """Robots-dropped boards whose drop is older than a day: undrop when robots.txt allows them again, else restart
    their day."""
    from . import robots
    back, still = [], []
    for board in boards:
        row = rows.get(board["board"], {})
        if not (row.get("dropped") or "").startswith("robots:"):
            continue
        if (_parse(row.get("dropped_at")) or now) > now - ROBOTS_RECHECK:
            continue
        if all(robots.allowed(u) for u in urls_of(board)):
            back.append((board["board"],))
            rows[board["board"]] = {**row, "dropped": None, "active": 1}
        else:
            still.append((db.iso(now), board["board"]))
    if back or still:
        with db.tx(conn):
            conn.executemany("UPDATE boards SET dropped=NULL, dropped_at=NULL, active=1 WHERE board=?", back)
            conn.executemany("UPDATE boards SET dropped_at=? WHERE board=?", still)


def plan(conn, boards: list[dict], now: datetime, only: str | None = None, urls_of=None) -> list[Task]:
    """Due tasks for this run: overdue t1 by lateness, then t2, then t3, then Workday term sweeps.
    `only` forces one board regardless of due."""
    rows = {r["board"]: dict(r) for r in conn.execute("SELECT * FROM boards")}
    if urls_of is not None:
        _recheck_robots(conn, boards, rows, now, urls_of)
    tiers, terms = board_tiers(conn, now), needs_terms(conn, now)
    tasks, seeds = [], []
    for board in boards:
        row = rows.get(board["board"], {})
        if row.get("dropped") or not row.get("active", 1) or board.get("review"):
            continue
        if only is not None and only not in (board["board"], board["company"]):
            continue
        tier = tier_of(board["ats"], tiers.get(board["board"], 3))
        interval = interval_for(row, tier)
        last = _parse(row.get("last_fetch")) or _parse(row.get("last_ok_at"))
        if last is None:
            last = seeded_last_fetch(board["board"], interval, now)
            seeds.append((db.iso(last), board["board"]))
        overdue = (now - last - interval + SLACK).total_seconds()
        full_due = (_parse(row.get("last_full_at")) or datetime.min.replace(tzinfo=now.tzinfo)) <= now - FULL_EVERY
        base = {**row, **board, "etag": None if full_due else row.get("etag"),
                "last_mod": None if full_due else row.get("last_mod")}
        if overdue >= 0 or only is not None:
            tasks.append(Task({**base, "task": "list"}, "list", tier, max(overdue, 0.0)))
        terms_last = _parse(row.get("terms_at"))
        if (board["ats"] in TERMS_ATS and board["board"] in terms
                and (terms_last is None or now - terms_last >= TERMS_EVERY - SLACK)):
            late = (now - (terms_last or now - TERMS_EVERY)).total_seconds()
            tasks.append(Task({**base, "task": "terms"}, "terms", TERMS_PRIORITY, late))
    if seeds:
        with db.tx(conn):
            conn.executemany("UPDATE boards SET last_fetch=? WHERE board=? AND last_fetch IS NULL", seeds)
    return sorted(tasks, key=lambda t: (t.tier, -t.overdue_s))


def after_fetch(conn, task: Task, result, status: str, now: datetime) -> None:
    """Advance the board's schedule and validators after a fetch that got an answer (never for skipped/backoff)."""
    board, now_s = task.board, db.iso(now)
    if result.http_status == ROBOTS_BLOCKED:
        with db.tx(conn):
            conn.execute("UPDATE boards SET dropped=?, dropped_at=?, active=0 WHERE board=? AND dropped IS NULL",
                         (f"robots: {(result.error or '')[:160]}", now_s, board["board"]))
        return
    if task.kind == "terms":
        with db.tx(conn):
            conn.execute("UPDATE boards SET terms_at=? WHERE board=?", (now_s, board["board"]))
        return
    gone = result.http_status in (404, 410)
    streak = (board.get("dead_streak") or 0) + 1 if gone else 0
    if result.not_modified:
        etag, last_mod, full_at = board.get("etag"), board.get("last_mod"), board.get("last_full_at")
    elif status == "ok" and (result.etag or result.last_mod):
        etag, last_mod, full_at = result.etag, result.last_mod, now_s
    else:
        etag, last_mod, full_at = None, None, (now_s if status == "ok" else board.get("last_full_at"))
    dropped = f"dead: {DEAD_DROP_AFTER} straight 404/410 answers" if streak >= DEAD_DROP_AFTER else None
    with db.tx(conn):
        conn.execute(
            "UPDATE boards SET last_fetch=?, etag=?, last_mod=?, last_full_at=?, dead_streak=?, tier=?, "
            "wd_total=coalesce(?, wd_total), dropped=coalesce(dropped, ?), "
            "dropped_at=CASE WHEN ? IS NOT NULL AND dropped IS NULL THEN ? ELSE dropped_at END, "
            "active=CASE WHEN ? IS NOT NULL THEN 0 ELSE active END WHERE board=?",
            (now_s, etag, last_mod, full_at, streak, task.tier, getattr(result, "total", None), dropped, dropped,
             now_s, dropped, board["board"]))
