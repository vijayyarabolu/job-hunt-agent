"""The ONLY definition of freshness (decision 001 §4). Pure functions; no I/O.

observe() merges a new sighting of a job into its stored row and reports events.
is_fresh() / age_hours() read a stored row.
"""
from datetime import date, datetime, timedelta, timezone

FRESH_WINDOW = timedelta(hours=24)
EXACT_BUMP_TOLERANCE = timedelta(hours=24)
DAY_BUMP_TOLERANCE_DAYS = 1
FUTURE_SLACK = timedelta(hours=1)
MUTABLE_ATS = {"ashby", "smartrecruiters", "workday"}   # sources whose posting date can move forward


STAMP_LEN = len("2026-01-01T00:00:00Z")


def parse(ts: str | None) -> datetime | None:
    """'2026-10-07T12:00:00Z' -> aware UTC datetime. The stored form takes the fast path (strptime was ~30 ms
    of every feed answer); anything else goes through strptime, which raises on a malformed stamp as before."""
    if not ts:
        return None
    if len(ts) == STAMP_LEN and ts[10] == "T" and ts[-1] == "Z":
        return datetime.fromisoformat(ts[:-1]).replace(tzinfo=timezone.utc)
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def fmt(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_mutable(ats: str, precision: str) -> bool:
    return ats in MUTABLE_ATS or precision == "updated"


def _earliest(old: str | None, new: str | None) -> str | None:
    """min() that never lets NULL win (decision 001 C3)."""
    if old is None:
        return new
    if new is None:
        return old
    return min(old, new)


def _is_bump(precision: str, old_posted: str | None, new_posted: str | None) -> bool:
    if precision not in ("exact", "day") or not old_posted or not new_posted:
        return False
    if precision == "exact":
        return parse(new_posted) - parse(old_posted) > EXACT_BUMP_TOLERANCE
    return (date.fromisoformat(new_posted[:10]) - date.fromisoformat(old_posted[:10])).days > DAY_BUMP_TOLERANCE_DAYS


def observe(old: dict | None, item: dict, now: datetime, seeding: bool) -> tuple[dict, list[tuple[str, dict]]]:
    """Return (fields to write, events) for one sighting. `seeding` = this board/scope is not baselined yet."""
    now_s = fmt(now)
    posted, precision = item.get("posted_at"), item.get("precision", "exact")
    if posted and parse(posted) > now + FUTURE_SLACK:
        posted, precision = None, "unknown"
    if old is None:
        patch = {"posted_at": posted, "first_posted_at": posted, "precision": precision,
                 "first_seen": now_s, "last_seen": now_s, "miss_count": 0, "missing_since": None,
                 "seed": int(seeding and (is_mutable(item["ats"], precision) or posted is None)),  # an
                 "repost_count": 0,                                         # undated first-run posting's age is unknown
                 "bumped_at": None, "closed_at": None, "stale": 0}
        return patch, [("new", {})]

    events: list[tuple[str, dict]] = []
    if item.get("capped") or posted is None:        # Workday "30+" never moves a date forward (C1)
        posted = old["posted_at"]
        precision = old.get("precision") or precision  # nor does a dropped (future/absent) date change precision
    patch = {"last_seen": now_s, "miss_count": 0, "missing_since": None, "stale": 0, "precision": precision}
    if _is_bump(precision, old["posted_at"], posted):
        patch["repost_count"] = (old.get("repost_count") or 0) + 1
        patch["bumped_at"] = old.get("bumped_at") or now_s
        events.append(("bumped", {"from": old["posted_at"], "to": posted, "prec": precision}))
    patch["posted_at"] = posted
    patch["first_posted_at"] = _earliest(old.get("first_posted_at"), posted)
    if old.get("closed_at"):
        patch["closed_at"] = None
        events.append(("reopened", {"closed_at": old["closed_at"]}))
    return patch, events


def _local_today(now: datetime) -> date:
    return now.astimezone().date()


def is_fresh(row: dict, now: datetime) -> bool:
    if row.get("closed_at") or row.get("stale") or row.get("bumped_at") or row.get("seed") or row.get("chain_origin"):
        return False                                  # chain_origin: a repost of a recently closed req (decision 002)
    first_seen = parse(row.get("first_seen"))
    first_posted = row.get("first_posted_at")
    precision = row.get("precision")
    if precision == "exact":
        return bool(first_posted) and now - parse(first_posted) <= FRESH_WINDOW
    if precision == "day":
        if not first_posted or not first_seen:
            return False
        yesterday = _local_today(now) - timedelta(days=1)
        return date.fromisoformat(first_posted[:10]) >= yesterday and now - first_seen <= FRESH_WINDOW
    return bool(first_seen) and now - first_seen <= FRESH_WINDOW and not row.get("repost_count")


def age_hours(row: dict, now: datetime) -> float | None:
    """Hours since the job was first posted (first_seen for 'updated'/'unknown' precision); for a repost chain, the
    age of the chain's oldest prior when that is older (decision 002)."""
    own = _own_age_hours(row, now)
    chain = parse(row.get("chain_origin"))
    if chain is None:
        return own
    chain_age = (now - chain).total_seconds() / 3600
    return chain_age if own is None else max(own, chain_age)


def feed_moment(row: dict) -> tuple[datetime | None, bool]:
    """(when the job appeared, exact?) for the feed's Newest-first order, the 1h/6h/24h/3d chips and the card age
: the ATS's exact posted time; for day precision (Workday) our first_seen when we saw it on its posted
    day or the day after, else the posted day's midnight (approximate); first_seen for updated/unknown dates. A repost
    chain is as old as its origin. Backlog (seed) rows have no moment."""
    if row.get("seed"):
        return None, False
    precision, posted, seen = row.get("precision"), row.get("first_posted_at"), parse(row.get("first_seen"))
    if precision == "exact" and posted:
        own, exact = parse(posted), True
    elif precision == "day" and posted:
        day = date.fromisoformat(posted[:10])
        late = seen is None or (seen.date() - day).days > DAY_BUMP_TOLERANCE_DAYS
        own, exact = (datetime.combine(day, datetime.min.time(), timezone.utc), False) if late else (seen, True)
    else:
        own, exact = seen, seen is not None
    chain = parse(row.get("chain_origin"))
    if chain is not None and (own is None or chain < own):
        return chain, True
    return own, exact


def found_kind(row: dict) -> str | None:
    """GOAL32 §2: whose time the card shows. 'day' when the board gives only the posted day (Workday: "posted today")
    and the time is when we found it that day; 'unknown' when the board gives no posted date and the time is ours;
    None when it is the board's own time (or a repost chain's), which the card shows as an age."""
    moment, exact = feed_moment(row)
    if moment is None or not exact or moment == parse(row.get("chain_origin")):
        return None
    if not row.get("first_posted_at"):
        return "unknown"
    return {"exact": None, "day": "day"}.get(row.get("precision"), "unknown")


def age_word(row: dict) -> str:
    """'Posted' when the source dates the job, 'First seen' when the age is ours (no date: Jobvite, some Taleo)."""
    return "Posted" if row.get("precision") in ("exact", "day") and row.get("first_posted_at") else "First seen"


def feed_age_hours(row: dict, now: datetime) -> float | None:
    moment, _ = feed_moment(row)
    return None if moment is None else max(0.0, (now - moment).total_seconds() / 3600)


def _own_age_hours(row: dict, now: datetime) -> float | None:
    precision = row.get("precision")
    if precision == "exact" and row.get("first_posted_at"):
        return (now - parse(row["first_posted_at"])).total_seconds() / 3600
    if precision == "day" and row.get("first_posted_at"):
        local_now = now.astimezone()
        midnight = datetime.combine(date.fromisoformat(row["first_posted_at"][:10]), datetime.min.time())
        return max(0.0, (local_now.replace(tzinfo=None) - midnight).total_seconds() / 3600)
    first_seen = parse(row.get("first_seen"))
    return (now - first_seen).total_seconds() / 3600 if first_seen else None
