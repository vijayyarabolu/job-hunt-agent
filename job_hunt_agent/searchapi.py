"""GOAL35 §5.2: GET /api/search?q=: the ⌘K box. Before, it searched only the jobs already drawn on the page, so a role
applied to last month could never come up. Now one query, the shared rules (textsearch), the current person only:
- applications: every tracker row, any status and age (trackfind: company, title, city, status, notes, JD, dates);
- companies: name, careers domain, ATS board name and aliases ("Meta" finds Facebook) of every board we read;
- jobs: the person's ranked jobs (company, title, place, role).
2026-10-24: each list puts what the words name first (textsearch tiers: the company itself, its alias, the title, a
short field, the JD); a company's search leaves out the rows only its JD mentions, and says when the person has no
listed job at that company ("no_jobs_at")."""
from datetime import datetime

from . import config, feedcache, feedfind, textsearch, trackfind
from .api import FEED_STATES

SHOWN = 8
LISTED = ("ok", "pending")          # what Recommended lists without "show senior / filtered"
STATUS_ORDER = {s: i for i, s in enumerate(("saved", "tailored", "applied", "screen", "interview", "offer", "rejected",
                                            "ghosted"))}


def _companies(conn) -> textsearch.Index:
    mark = tuple(conn.execute("SELECT count(*), coalesce(max(rowid), 0), sum(active) FROM boards").fetchone())

    def records():
        rows = conn.execute("SELECT company, group_concat(DISTINCT coalesce(board_name, '')), group_concat(DISTINCT "
                            "coalesce(website, '')), group_concat(DISTINCT ats), group_concat(DISTINCT key) FROM boards "
                            "WHERE active = 1 GROUP BY company")
        return [textsearch.Record(r[0], {"company": r[0], "board": r[1], "domain": r[2], "ats": r[3], "key": r[4]})
                for r in rows]
    return textsearch.cached(("companies", str(config.DB_PATH), mark), records)


def _applications(conn, pid: str, q: str) -> tuple[dict, textsearch.Found]:
    found = trackfind.find(conn, pid, q)
    keep = found.strong()                       # a company's own rows; the ones only its JD names wait in the Tracker
    ids = [found.index.records[i].id for i in keep]
    if not ids:
        return {"total": 0, "rows": [], "terms": []}, found
    where = {found.index.records[i].id: found.where(i) for i in keep}
    tier = {found.index.records[i].id: found.tier(i) for i in keep}
    rows = [dict(r) for r in conn.execute(f"SELECT id, company, role_title, status, applied_on FROM applications WHERE id IN "
                                          f"({','.join('?' * len(ids))})", ids)]
    rows.sort(key=lambda r: (r["applied_on"] or "", r["id"]), reverse=True)
    rows.sort(key=lambda r: (tier[r["id"]], STATUS_ORDER.get(r["status"], 99)))    # what it names first, then status
    return {"total": len(ids), "terms": list(found.terms),
            "rows": [{**r, "where": where.get(r["id"], [])} for r in rows[:SHOWN]]}, found


def _named_company(boards: textsearch.Found, apps: textsearch.Found) -> str | None:
    """The company the query names (a board's company, else one of the user's rows'), for "No open Amazon jobs ..."."""
    for found in (boards, apps):
        name = lambda i: (found.index.records[i].fields.get("company") or "").strip()     # noqa: E731
        best = min(found.positions, key=lambda i: (found.tier(i), len(name(i))), default=None)   # "Amazon", not "Amazon Music"
        if best is not None and found.tier(best) <= textsearch.COMPANY_ALIAS and name(best):
            return name(best)
    return None


def everything(conn, pid: str, q: str, now: datetime) -> dict:
    q = (q or "").strip()[:textsearch.MAX_QUERY]
    if not textsearch.parse(q):
        return {"q": q, "applications": {"total": 0, "rows": [], "terms": []}, "companies": [], "jobs": []}
    query = textsearch.parse(q)
    found = _companies(conn).search(query)
    exact = lambda i: not set(query.words) <= found.index.field_words[i].get("company", set())   # "Meta" before "Metals"
    best = sorted(found.positions, key=lambda i: (exact(i), len(found.index.records[i].id)))[:SHOWN]
    companies = [{"company": found.index.records[i].id, "ats": found.index.records[i].fields["ats"]} for i in best]
    kept = feedcache.ranked(conn, config.profile(pid), now, False, FEED_STATES)
    hits = feedfind.find(conn, pid, kept, q)
    listed = [r for r, s in kept.ranked if r["uid"] in hits.uids and s.state in LISTED]
    listed.sort(key=lambda r: hits.tiers[r["uid"]])               # the company's own jobs first, rank order inside
    jobs = [{"uid": r["uid"], "company": r["company"], "title": r["title"], "location": r.get("location")}
            for r in listed[:SHOWN]]
    applications, apps = _applications(conn, pid, q)
    company = _named_company(found, apps)
    own = any(hits.tiers[r["uid"]] <= textsearch.COMPANY_ALIAS for r in listed)
    return {"q": q, "applications": applications, "companies": companies, "jobs": jobs,
            "jobs_total": len(listed), "terms": list(dict.fromkeys([*hits.terms, *found.terms])),
            "no_jobs_at": company if company and not own else None}


def warm(conn) -> None:
    _companies(conn)
