"""GOAL24 §3/§4: the sponsorship side of a job list. Each job's badge (label.badge), the Recommended switch
(Sponsor-friendly = Sponsors + Cap-exempt + Sponsored before · No sponsorship · All, with counts; Unknown only under
All) and, for a person who needs sponsorship, the order: within the same posting day (newest first) or the same 10-point
match band (best match), Sponsors / Cap-exempt first, then Unknown, then Sponsored before; No sponsorship after every
other job. So a fresh great fit is never pushed below an older or weaker one, only below the sponsors of its own day
or band, and nothing is hidden unless the switch says so."""
import threading

from .. import config, fresh, signals
from ..db import unpack
from . import label

VIEWS = ("friendly", "no")
MATCH_BAND = 10
QUOTE_CACHE_MAX = 20_000
SQL_BATCH = 500
_QUOTES: dict[str, str | None] = {}
_QUOTE_LOCK = threading.Lock()


def badges(conn, pairs: list) -> dict:
    """uid -> Badge | None for (row, Score) pairs; the score's dealbreaker (a clearance) counts as the JD's word."""
    idx = label.index(conn)
    return {r["uid"]: label.badge(r, idx, s.dealbreaker) for r, s in pairs}


def counts(pairs: list, found: dict) -> dict:
    groups = [label.group(found.get(r["uid"])) for r, _ in pairs]
    return {"friendly": groups.count("friendly"), "no": groups.count("no"), "all": len(pairs)}


def only(pairs: list, found: dict, view: str | None) -> list:
    if view not in VIEWS:
        return pairs
    return [(r, s) for r, s in pairs if label.group(found.get(r["uid"])) == view]


def _bucket(row: dict, sort: str, pcts: dict) -> tuple:
    if sort == "match":
        pct = pcts.get(row["uid"])
        return (0, -(pct // MATCH_BAND)) if pct is not None else (1, 0)
    moment = fresh.feed_moment(row)[0]
    return (0, -moment.astimezone().date().toordinal()) if moment else (1, 0)


def order(pairs: list, sort: str, pcts: dict, found: dict) -> list:
    """The list as ranked (newest / best match), re-ordered by sponsorship tier inside each day or match band; No
    sponsorship last. Stable: inside a tier the list keeps its order."""
    place = {r["uid"]: i for i, (r, _) in enumerate(pairs)}

    def key(pair):
        uid = pair[0]["uid"]
        tier = label.tier(found.get(uid))
        return (tier == 3, _bucket(pair[0], sort, pcts), tier, place[uid])
    return sorted(pairs, key=key)


def quotes(conn, uids: list[str]) -> dict[str, str | None]:
    """uid -> the JD sentence about sponsorship (only asked for jobs whose JD said something), kept in-process."""
    need = [u for u in uids if u not in _QUOTES]
    with _QUOTE_LOCK:
        if len(_QUOTES) + len(need) > QUOTE_CACHE_MAX:
            _QUOTES.clear()
        for i in range(0, len(need), SQL_BATCH):
            part = need[i:i + SQL_BATCH]
            for r in conn.execute(f"SELECT uid, description FROM jobs WHERE uid IN ({','.join('?' * len(part))})", part):
                _QUOTES[r["uid"]] = signals.sponsorship_quote(unpack(r["description"]))
    return {u: _QUOTES.get(u) for u in uids}


def view(b: label.Badge | None, quote: str | None = None) -> dict | None:
    """What a card carries: {kind, text, why, source, evidence}; the JD's sentence is the why when there is one."""
    if b is None:
        return None
    out = b.as_dict()
    if quote and b.source == "jd":
        out["why"] = f"The post says: “{quote}”"
        out["evidence"] = {**out["evidence"], "jd": quote}
    return out


def card_views(conn, rows: list, found: dict) -> dict[str, dict | None]:
    """uid -> view() for the cards shown, with the JD's sentence for the badges the JD decided."""
    said = [r["uid"] for r in rows if found.get(r["uid"]) and found[r["uid"]].source == "jd" and r.get("sponsorship")]
    got = quotes(conn, said) if said else {}
    return {r["uid"]: view(found.get(r["uid"]), got.get(r["uid"])) for r in rows}


def app_views(conn, pid: str, apps: list[dict]) -> dict[int, dict | None]:
    """Tracker rows: app id -> the badge of its company (with the job's own JD wording when it came from the feed);
    {} for a person who does not need sponsorship."""
    if pid not in config.profile_ids() or not config.profile(pid).get("needs_sponsorship"):
        return {}
    idx = label.index(conn)
    return {a["id"]: view(label.badge({"company": a.get("company"), "sponsorship": a.get("job_sponsorship")}, idx))
            for a in apps}
