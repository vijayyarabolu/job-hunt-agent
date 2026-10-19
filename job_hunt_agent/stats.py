"""Tracker stats per profile: applications per week, response rate (reached screen or later) overall and by
role family, company size and how old the posting was when applied, and median days to a response. Pure reads.

A response is a pipeline status of screen, interview or offer at any time (current status or an event); rejected and
ghosted are not responses. Timing uses only status changes made in the app (an import's events carry the import
time, not when the reply came). Postings are dated from the job the row came from or, for imported rows, the fetched
posting at the same company with the same title (applied.same_title) first posted on or before the apply date.
"""
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta

from . import applied, feedback, trackapi

RESPONSE = ("screen", "interview", "offer")
SUBMITTED = ("applied", "screen", "interview", "offer", "rejected", "ghosted")
MIN_WEEKS, MAX_WEEKS = 8, 52      # the chart spans the first application to today, within these bounds
SQL_BATCH = 500
POSTED_BUCKETS = ((1, "0 to 1 days"), (3, "2 to 3 days"), (7, "4 to 7 days"), (14, "8 to 14 days"), (10_000, "15+ days"))
UNKNOWN_POSTED = "Unknown"
SIZE_LABELS = {"big": "Big company", "startup": "Startup", "other": "Mid-size / enterprise",
               "public_sector": "Public sector", "unknown": "Unknown size"}
FAMILY_LABELS = {"ai_engineer": "AI Engineer", "genai_engineer": "GenAI Engineer", "ml_engineer": "ML Engineer",
                 "dl_engineer": "Deep Learning", "data_scientist": "Data Scientist", "data_engineer": "Data Engineer",
                 "other": "Other"}
TIMED_SOURCES = ("ui", "cli", "sheet")
# callbacks by sponsorship badge (the company's badge today; shape + word, as the feed shows it)
SPONSOR_LABELS = {"sponsors": "✓ Sponsors", "cap_exempt": "★ Cap-exempt", "maybe": "? Sponsored before",
                  "no": "✕ No sponsorship", "unknown": "No badge (nothing known)"}


def _apps(conn, pid: str) -> list[dict]:
    rows = conn.execute(
        "SELECT a.id, a.company, a.role_title, a.status, a.applied_on, a.job_uid, j.families AS job_families, "
        "j.first_posted_at, j.sponsorship, b.segment FROM applications a LEFT JOIN jobs j ON j.uid = a.job_uid "
        "LEFT JOIN boards b ON b.board = j.board WHERE a.profile = ? AND a.applied_on IS NOT NULL "
        f"AND a.status IN ({','.join('?' * len(SUBMITTED))})", (pid, *SUBMITTED))
    return [dict(r) for r in rows]


def _events(conn, ids: list[int]) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = defaultdict(list)
    for i in range(0, len(ids), SQL_BATCH):
        part = ids[i:i + SQL_BATCH]
        for r in conn.execute(f"SELECT app_id, at, to_status, source FROM application_events WHERE app_id IN "
                              f"({','.join('?' * len(part))}) ORDER BY id", part):
            out[r["app_id"]].append(dict(r))
    return out


def _segments(conn) -> dict[str, str]:
    """company key -> board segment, for rows without a job (any board we know the company by)."""
    out = {}
    for r in conn.execute("SELECT company, segment FROM boards WHERE segment IS NOT NULL"):
        out.setdefault(applied.company_key(r["company"]), r["segment"])
    return out


def _postings(conn, apps: list[dict]) -> dict[int, str]:
    """app id -> first posted date of the matching fetched posting, for rows without a job of their own."""
    want = {applied.company_key(a["company"]) for a in apps if not a["first_posted_at"] and a["company"]}
    by_company: dict[str, list[tuple[str, str]]] = defaultdict(list)
    # the company spellings first (index only), then just their jobs: reading all ~600k jobs took 4-7 s
    names = [r[0] for r in conn.execute("SELECT DISTINCT company FROM jobs")
             if r[0] and applied.company_key(r[0]) in want]
    for i in range(0, len(names), SQL_BATCH):
        part = names[i:i + SQL_BATCH]
        for r in conn.execute(f"SELECT company, title, first_posted_at FROM jobs WHERE company IN "
                              f"({','.join('?' * len(part))}) AND first_posted_at IS NOT NULL AND families <> ','", part):
            by_company[applied.company_key(r["company"])].append((r["title"], r["first_posted_at"][:10]))
    out = {}
    for a in apps:
        if a["first_posted_at"] or not a["role_title"]:
            continue
        dates = [d for t, d in by_company.get(applied.company_key(a["company"]), ())
                 if d <= a["applied_on"] and applied.same_title(a["role_title"], t)]
        if dates:
            out[a["id"]] = max(dates)
    return out


def _bucket_posted(days: int | None) -> str:
    if days is None:
        return UNKNOWN_POSTED
    return next(label for limit, label in POSTED_BUCKETS if days <= limit)


def _rate(group: dict[str, list[bool]], labels: dict[str, str] | None = None) -> list[dict]:
    rows = [{"key": k, "label": (labels or {}).get(k, k), "n": len(v), "responses": sum(v),
             "rate": round(sum(v) / len(v), 3) if v else None} for k, v in group.items()]
    return sorted(rows, key=lambda r: (-r["n"], r["label"]))


def _weekly(apps: list[dict], today: date) -> list[dict]:
    monday = today - timedelta(days=today.weekday())
    counts: dict[str, int] = defaultdict(int)
    for a in apps:
        day = date.fromisoformat(a["applied_on"])
        counts[(day - timedelta(days=day.weekday())).isoformat()] += 1
    first = min((date.fromisoformat(w) for w in counts), default=monday)
    span = max(MIN_WEEKS, min(MAX_WEEKS, (monday - first).days // 7 + 1))
    weeks = [(monday - timedelta(weeks=w)).isoformat() for w in range(span - 1, -1, -1)]
    return [{"week": w, "n": counts.get(w, 0)} for w in weeks]


def _first_days(a: dict, timed: list[dict], wanted: set[str]) -> int | None:
    first = next((e for e in timed if e["to_status"] in wanted), None)
    if first is None:
        return None
    responded = datetime.fromisoformat(first["at"].replace("Z", "+00:00")).astimezone().date()   # local day
    return max(0, (responded - date.fromisoformat(a["applied_on"])).days)


_SLOW: dict[str, tuple[tuple, tuple[dict, dict]]] = {}     # person -> (key, (segments, matched postings))


def _slow_parts(conn, pid: str, apps: list[dict]) -> tuple[dict, dict]:
    """the board segments and the posting dates matched by company + title read the 1M-job table (~1 s); they
    change only with a fetch run or with the applications themselves, so they are kept until one of those changes."""
    run = conn.execute("SELECT max(id) FROM runs").fetchone()[0]
    path = conn.execute("PRAGMA database_list").fetchone()[2]          # another DB (a test's) never shares it
    key = (path, run, hash(tuple((a["id"], a["company"], a["role_title"], a["applied_on"], a["first_posted_at"]) for a in apps)))
    hit = _SLOW.get(pid)
    if hit and hit[0] == key:
        return hit[1]
    parts = (_segments(conn), _postings(conn, apps))
    _SLOW[pid] = (key, parts)
    return parts


def report(conn, pid: str, today: date | None = None) -> dict:
    today = today or date.today()
    apps = _apps(conn, pid)
    events = _events(conn, [a["id"] for a in apps])
    segments, matched = _slow_parts(conn, pid, apps)
    by_family, by_size, by_posted, by_sponsor = defaultdict(list), defaultdict(list), defaultdict(list), defaultdict(list)
    from .h1b import label                                       # late: h1b reads config / the profile list
    idx = label.index(conn)
    to_response, to_rejection = [], []
    for a in apps:
        responded = bool({a["status"], *(e["to_status"] for e in events[a["id"]])} & set(RESPONSE))
        by_family[trackapi.family_of(a)].append(responded)
        badge = label.badge({"company": a["company"], "sponsorship": a.get("sponsorship")}, idx)
        by_sponsor[badge.kind if badge else "unknown"].append(responded)
        by_size[a["segment"] or segments.get(applied.company_key(a["company"])) or "unknown"].append(responded)
        posted = a["first_posted_at"][:10] if a["first_posted_at"] else matched.get(a["id"])
        days = (date.fromisoformat(a["applied_on"]) - date.fromisoformat(posted)).days if posted else None
        by_posted[_bucket_posted(max(days, 0) if days is not None else None)].append(responded)
        timed = [e for e in events[a["id"]] if e["source"] in TIMED_SOURCES]
        for wanted, sink in ((set(RESPONSE), to_response), ({"rejected"}, to_rejection)):
            days_to = _first_days(a, timed, wanted)
            if days_to is not None:
                sink.append(days_to)
    responses = sum(sum(v) for v in by_family.values())
    order = [label for _, label in POSTED_BUCKETS] + [UNKNOWN_POSTED]
    weekly = _weekly(apps, today)
    statuses = {r["status"]: r["n"] for r in conn.execute(
        "SELECT replace(status, 'tailored', 'saved') AS status, count(*) AS n FROM applications WHERE profile = ? "
        "GROUP BY 1", (pid,))}
    return {
        "profile": pid, "applications": len(apps), "responses": responses,
        "rate": round(responses / len(apps), 3) if apps else None, "this_week": weekly[-1]["n"], "weekly": weekly,
        "by_family": _rate(by_family, FAMILY_LABELS), "by_size": _rate(by_size, SIZE_LABELS),
        "by_sponsor": sorted(_rate(by_sponsor, SPONSOR_LABELS), key=lambda r: list(SPONSOR_LABELS).index(r["key"])),
        "by_days_posted": sorted(_rate(by_posted), key=lambda r: order.index(r["key"])),
        "posting_dated": sum(1 for a in apps if a["first_posted_at"] or a["id"] in matched),
        "median_days_to_response": statistics.median(to_response) if to_response else None,
        "n_timed_responses": len(to_response),
        "median_days_to_rejection": statistics.median(to_rejection) if to_rejection else None,
        "n_timed_rejections": len(to_rejection), "status_counts": statuses,
        "not_interested": feedback.reason_counts(conn, pid)}
