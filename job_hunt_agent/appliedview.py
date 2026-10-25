"""Applied as cards (user, 2026-10-16): every job the user applied to, as the same cards and detail pane the feed has,
grouped by the day the user applied (newest day first), so an email from a company is one search and one click from its
JD, the resume the user sent and the tracker row. The Tracker tab keeps the table.

Segments: all sent applications, waiting (applied, no answer yet), interviewing (screen / interview / offer) and
rejected (rejected / cold). A search uses the Tracker's (GOAL35 §5: company and its aliases, role, city, JD, dates,
typos; trackfind). A job of the feed gets its feed card; a job added by
link or pasted in the Resume app (no feed uid) gets a small card that opens its tracker row.
"""
from datetime import date, datetime

from . import api, daygroups, outside_card, outside_jobs, trackfind

SEGMENTS = {
    "all": ("applied", "screen", "interview", "offer", "rejected", "ghosted"),
    "waiting": ("applied",),
    "interviewing": ("screen", "interview", "offer"),
    "rejected": ("rejected", "ghosted"),
}


def _rows(conn, pid: str) -> list[dict]:
    statuses = SEGMENTS["all"]
    rows = conn.execute(
        "SELECT a.id, a.job_uid, a.company, a.role_title, a.job_url, a.location, a.status, a.applied_on, "
        "  (SELECT min(e.at) FROM application_events e WHERE e.app_id = a.id AND e.to_status = 'applied') AS applied_at "
        f"FROM applications a WHERE a.profile = ? AND a.status IN ({','.join('?' * len(statuses))}) "
        "ORDER BY coalesce(a.applied_on, '') DESC, a.id DESC", (pid, *statuses))
    return [dict(r) for r in rows]


def _local(ts: str | None) -> datetime | None:
    """An ISO time (UTC, 'Z' or an offset) on this computer's clock, the one applied_on days are written in."""
    try:
        moment = datetime.fromisoformat(str(ts).replace("Z", "+00:00")) if ts else None
    except ValueError:
        return None
    return moment.astimezone() if moment and moment.tzinfo else None


def _when(row: dict, today) -> tuple[str, str]:
    """(day header, time) of a row: applied_on is the day; the time comes from the 'applied' event when it is the
    same day (rows imported from the Sheet have a day only)."""
    try:
        day = datetime.fromisoformat(row["applied_on"]).date() if row.get("applied_on") else None
    except ValueError:
        day = None
    at = _local(row.get("applied_at"))
    clock = daygroups.clock(at) if at and day and at.date() == day else ""
    if day is None:
        return "No date", clock
    gap = (today - day).days
    name = f"{day:%a %b} {day.day}"
    return (f"Today · {name}" if gap == 0 else f"Yesterday · {name}" if gap == 1 else name), clock


def board(conn, pid: str, seg: str, q: str | None, now: datetime) -> dict:
    """GET /api/jobs?tab=applied&view=cards&seg=<segment>&q=<search>: the cards, by applied day, with each segment's
    count (counts ignore the search, so the segment row stays steady while typing)."""
    if seg not in SEGMENTS:
        raise ValueError(f"seg must be one of {tuple(SEGMENTS)}")
    outside_jobs.backfill(conn, pid, now)                   # GOAL36 §5.1: outside rows get their job record
    rows = _rows(conn, pid)
    counts = {name: sum(1 for r in rows if r["status"] in statuses) for name, statuses in SEGMENTS.items()}
    found = trackfind.find(conn, pid, q) if (q or "").strip() else None
    if found is not None:
        hit = set(found.ids())
        rows = [r for r in rows if r["id"] in hit]
    in_seg = [r for r in rows if r["status"] in SEGMENTS[seg]]
    other = len(rows) - len(in_seg)                 # found, but under another segment
    rows = in_seg
    feed = api.cards_for(conn, pid, list(dict.fromkeys(r["job_uid"] for r in rows if r["job_uid"])), now)
    today = date.fromisoformat(api.today(now))          # the clock applied_on was written with (Mark applied)
    who = outside_card.person(pid) if any(not r["job_uid"] for r in rows) else None
    cards, seen = [], set()
    for row in rows:
        card = feed.get(row["job_uid"]) if row["job_uid"] else None
        card = dict(card) if card else outside_card.card(conn, who, f"app:{row['id']}", row, now=False)
        if card["uid"] in seen:                     # two rows for one posting: the newest one is the card
            continue
        seen.add(card["uid"])
        day, clock = _when(row, today)
        card["ap"] = {"app": row["id"], "status": row["status"], "applied_on": row["applied_on"], "day": day,
                      "time": clock}
        cards.append(card)
    days: dict[str, int] = {}
    for card in cards:
        days[card["ap"]["day"]] = days.get(card["ap"]["day"], 0) + 1
    for card in cards:
        card["ap"]["day"] = f"{card['ap']['day']} ({days[card['ap']['day']]})"
    return {"tab": "applied", "view": "cards", "seg": seg, "q": q or "", "jobs": cards, "total": len(cards),
            "counts": counts, "terms": list(found.terms) if found else [], "other_segments": other if found else 0,
            **api.progress(conn, pid, now)}
