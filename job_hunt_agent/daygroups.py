"""the Resumes tab's rows grouped by the day their resume was made (Chicago time), newest day first, one
"More than 30 days" group last; inside a day the list keeps its own order, numbered 1, 2, 3 …, each with its time.

stamp() orders the cards and adds `day` (the header, with the group's count: "Today · Mon Oct 12 (7)"), `n` (the
row's number in its day) and `time` ("9:14 AM") to each card's `rq`. The page draws a header where `day` changes.
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo

CHICAGO = ZoneInfo("America/Chicago")
OLD_DAYS = 30
OLD = "More than 30 days"


def local(ts: str | None) -> datetime | None:
    """An ISO time from resumegen (UTC, 'Z' or an offset) in Chicago time; None when missing or unreadable."""
    try:
        moment = datetime.fromisoformat(str(ts).replace("Z", "+00:00")) if ts else None
    except ValueError:
        return None
    return moment.astimezone(CHICAGO) if moment and moment.tzinfo else None


def label(day: date | None, today: date) -> str:
    if day is None or (today - day).days > OLD_DAYS:
        return OLD
    name = f"{day:%a %b} {day.day}"
    gap = (today - day).days
    return f"Today · {name}" if gap == 0 else f"Yesterday · {name}" if gap == 1 else name


def clock(moment: datetime | None) -> str:
    return f"{moment:%I:%M %p}".lstrip("0") if moment else ""


def stamp(cards: list[dict], now: datetime, keep_order: bool = False) -> list[dict]:
    """The cards by day (newest day first; the order inside a day kept), each rq with day / n / time. keep_order (the
    Queue: its run order is the point) groups consecutive rows of a day without moving any."""
    today = now.astimezone(CHICAGO).date()
    timed = [(card, local(card["rq"].get("made_at"))) for card in cards]
    days = [(card, at, label(at.date() if at else None, today)) for card, at in timed]
    if not keep_order:                                   # stable: a day keeps the list's order (needs you first …)
        days.sort(key=lambda d: (d[2] == OLD, -(d[1].date().toordinal() if d[1] and d[2] != OLD else 0)))
    runs: list[list] = []
    for entry in days:
        if not runs or runs[-1][0][2] != entry[2]:
            runs.append([])
        runs[-1].append(entry)
    return [{**card, "rq": {**card["rq"], "day": f"{name} ({len(run)})", "n": n, "time": clock(at)}}
            for run in runs for n, (card, at, name) in enumerate(run, 1)]
