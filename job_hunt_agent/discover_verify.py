"""`discover --verify`: one list request per crawl token, admitted only with a US posting in the last 60 days
(decision 005 B1 step 2, GOAL2 B1 "US filter at the board level").

Every check returns a Verdict; nothing here writes YAML. Results go to discover_probes (main thread only). A 429, 5xx,
timeout or host backoff writes `error` and `next_try = +1 day` and never records a miss. Names come from the ATS
(Greenhouse company_name), the hosted page (Ashby/Lever), or, for Workday, the S&P list, the
posting's hiring organization, then the tenant slug, so no board waits for a human to name it.
"""
import fcntl
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Callable, NamedTuple

from . import company_names, config, db, robots, signals
from .ats.common import from_epoch_ms, parse_iso
from .ats.workday import parse_posted_on, urls
from .net import HttpError, get_json, get_text, post_json
from .text import html_to_text

US_WINDOW = timedelta(days=60)
VERIFY_TTL = timedelta(days=30)
RETRY_AFTER = timedelta(days=1)
WD_RECENT_DAYS = 30                         # Workday "Posted 30+ Days Ago" is capped: only < 30 is a sure date
_US_FACET = re.compile(r"^(united states( of america)?|usa|us|u\.s\.a?\.?)$", re.I)
_TITLE = re.compile(r"<title>(.*?)</title>", re.S | re.I)
_ASHBY_SITE = re.compile(r'"publicWebsite":"(https?://[^"]+)"')
_LEVER_LOGO = re.compile(r'<a[^>]*class="main-header-logo"[^>]*href="([^"]+)"', re.I)
_LEGAL_TAIL = re.compile(r"(?i)[\s,]+(inc|llc|l\.l\.c|ltd|limited|corp|corporation|co|plc|gmbh|s\.a|n\.a|lp|llp|"
                         r"usa|us|u\.s\.|united states|america|north america|holdings?)\.?$")
_LEAD_CODE = re.compile(r"^(?:\d+\s+|[\s\-|:•·,_]+)+")      # "2100 NVIDIA"; never "6sense", "[solidcore]", "#paid"
_SITE_WORDS = re.compile(r"(?i)\s*[-|:]?\s*\b(jobs|careers?|job board|career site|opportunities)\b\s*$")
log = logging.getLogger(__name__)


class Verdict(NamedTuple):
    n_jobs: int
    us60: int = 0
    newest_us: str | None = None
    name: str | None = None
    name_src: str | None = None
    website: str | None = None
    wd_total: int | None = None


def _recent(posted: str | None, now: datetime) -> bool:
    if not posted:
        return False
    moment = datetime.strptime(posted[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    return now - US_WINDOW <= moment <= now + timedelta(days=1)


def us_recent(rows: list[tuple[str | None, str | None, str | None]], now: datetime) -> tuple[int, str | None]:
    """rows of (posted ISO, location, country) -> (US postings in 60 days, newest of them)."""
    hits = sorted((p for p, loc, country in rows if _recent(p, now) and signals.is_us(loc, country)), reverse=True)
    return len(hits), (hits[0] if hits else None)


def clean_name(text: str | None) -> str | None:
    """'2100 NVIDIA USA' -> 'NVIDIA'; 'Wolters Kluwer United States Inc.' -> 'Wolters Kluwer'; 'OpenAI Jobs' ->
    'OpenAI'."""
    name = html_to_text(text or "").strip()
    name = _LEAD_CODE.sub("", name)
    for _ in range(3):
        cut = _LEGAL_TAIL.sub("", _SITE_WORDS.sub("", name)).strip(" ,-|")
        if not cut or company_names.DANGLING.search(cut):     # "Bank of America" never "Bank of"
            break
        name = cut
    name = company_names.tidy(name)
    return name if len(name) >= 2 and re.search(r"[A-Za-z]", name) else None


def _site_of(url: str | None) -> str | None:
    from .discover import _company_site
    return _company_site(url)


def check_greenhouse(token: str, now: datetime) -> Verdict:
    jobs = (get_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs") or {}).get("jobs") or []
    rows = [(parse_iso(j.get("first_published") or j.get("updated_at")), (j.get("location") or {}).get("name"), None)
            for j in jobs if isinstance(j, dict)]
    us60, newest = us_recent(rows, now)
    name = next((j.get("company_name") for j in jobs if isinstance(j, dict) and j.get("company_name")), None)
    site = next((u for u in (j.get("absolute_url") for j in jobs if isinstance(j, dict)) if _site_of(u)), None)
    return Verdict(len(jobs), us60, newest, clean_name(name) or name, "ats" if name else None, site)


def check_lever(token: str, now: datetime) -> Verdict:
    jobs = get_json(f"https://api.lever.co/v0/postings/{token}?mode=json") or []
    jobs = [j for j in jobs if isinstance(j, dict)] if isinstance(jobs, list) else []
    rows = [(from_epoch_ms(j.get("createdAt")), (j.get("categories") or {}).get("location"), j.get("country"))
            for j in jobs]
    us60, newest = us_recent(rows, now)
    if not us60:
        return Verdict(len(jobs), 0, None)
    page = get_text(f"https://jobs.lever.co/{token}")
    title, logo = _TITLE.search(page), _LEVER_LOGO.search(page)
    name = clean_name(title.group(1)) if title else None
    return Verdict(len(jobs), us60, newest, name, "page" if name else None, _site_of(logo and logo.group(1)))


def check_ashby(token: str, now: datetime) -> Verdict:
    payload = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{token}") or {}
    jobs = [j for j in payload.get("jobs") or [] if isinstance(j, dict) and j.get("isListed") is not False]
    rows = [(parse_iso(j.get("publishedAt")), j.get("location"),
             ((j.get("address") or {}).get("postalAddress") or {}).get("addressCountry")) for j in jobs]
    us60, newest = us_recent(rows, now)
    if not us60:
        return Verdict(len(jobs), 0, None)
    page = get_text(f"https://jobs.ashbyhq.com/{token}")
    title, site = _TITLE.search(page), _ASHBY_SITE.search(page)
    name = clean_name(title.group(1)) if title else None
    return Verdict(len(jobs), us60, newest, name, "page" if name else None, _site_of(site and site.group(1)))


def _us_facet(facets: list) -> tuple[str, str] | None:
    """(facetParameter, id) of the United States value, searching nested facet groups."""
    for facet in facets or []:
        if not isinstance(facet, dict):
            continue
        for value in facet.get("values") or []:
            if not isinstance(value, dict):
                continue
            if value.get("values"):
                found = _us_facet([value])
                if found:
                    return found
            elif _US_FACET.match((value.get("descriptor") or "").strip()) and value.get("id"):
                return facet.get("facetParameter"), value["id"]
    return None


def _wd_page(api: str, facets: dict | None = None) -> dict:
    body = {"appliedFacets": facets or {}, "limit": 20, "offset": 0, "searchText": ""}
    return post_json(f"{api}/jobs", body) or {}


def wd_us_rows(postings: list, now: datetime, all_us: bool) -> list[tuple[str | None, str | None, str | None]]:
    rows = []
    for p in postings:
        days, capped = parse_posted_on(p.get("postedOn"))
        if days is None or capped or days >= WD_RECENT_DAYS:
            continue
        posted = (now - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00Z")
        rows.append((posted, "United States" if all_us else p.get("locationsText"), None))
    return rows


def check_workday(key: str, now: datetime, names: Callable[[str], str | None] = lambda slug: None) -> Verdict:
    tenant, wd, site = key.split("/")
    base, api = urls(key)
    for url in (f"{base}/{site}/", f"{api}/jobs"):
        verdict = robots.allowed(url)
        if verdict is None:
            raise HttpError(url, None, "robots.txt unreadable")
        if not verdict:
            return Verdict(0, 0, None, None, "robots")
    first = _wd_page(api)
    total = int(first.get("total") or 0)
    if not total:
        return Verdict(0, wd_total=0)
    facet = _us_facet(first.get("facets") or [])
    us_page = _wd_page(api, {facet[0]: [facet[1]]}) if facet else first
    us60, newest = us_recent(wd_us_rows(us_page.get("jobPostings") or [], now, bool(facet)), now)
    if not us60:
        return Verdict(total, 0, None, wd_total=total)
    name, src = _wd_name(api, us_page.get("jobPostings") or [], tenant, names)
    return Verdict(total, us60, newest, name, src, None, total)


def _wd_name(api: str, postings: list, tenant: str, names: Callable[[str], str | None]) -> tuple[str, str]:
    listed = names(tenant)
    if listed:
        return listed, "list"
    path = next((p.get("externalPath") for p in postings if p.get("externalPath")), None)
    if path:
        try:
            org = ((get_json(f"{api}{path}") or {}).get("hiringOrganization") or {}).get("name")
        except HttpError:
            org = None
        if clean_name(org):
            return clean_name(org), "hiring_org"
    return tenant.replace("-", " ").title(), "slug"


CHECKS = {"greenhouse": check_greenhouse, "lever": check_lever, "ashby": check_ashby}


def check(ats: str, token: str, now: datetime, names: Callable[[str], str | None]) -> Verdict:
    if ats == "workday":
        return check_workday(token, now, names)
    from .discover_more import SURVEYS                     # GOAL16 systems (imports this module)
    if ats in SURVEYS:
        from .discover_more import check as check_more
        return check_more(ats, token, now, names)
    if ats not in CHECKS:
        from .discover_enterprise import CHECKS as ENTERPRISE      # GOAL10 systems (imports this module)
        return ENTERPRISE[ats](token, now)
    return CHECKS[ats](token, now)


def todo(conn, candidates: dict[str, list[str]], now: datetime, refresh: bool = False) -> list[tuple[str, str]]:
    """(ats, token) pairs that need a live check: never verified, verified > 30 days ago (or --refresh), and not
    waiting out a transient failure."""
    rows = {(r["ats"], r["slug"]): r for r in conn.execute("SELECT ats, slug, verified_at, next_try FROM discover_probes")}
    out = []
    for ats, tokens in candidates.items():
        for token in tokens:
            row = rows.get((ats, token))
            if row and row["next_try"] and row["next_try"] > db.iso(now):
                continue
            if row and row["verified_at"] and not refresh and row["verified_at"] > db.iso(now - VERIFY_TTL):
                continue
            out.append((ats, token))
    return out


def record(conn, ats: str, token: str, source: str, now: datetime, verdict: Verdict | None,
           error: str | None = None) -> None:
    """One discover_probes row. A transient error keeps the previous verdict and only schedules a retry."""
    now_s = db.iso(now)
    with db.tx(conn):
        if verdict is None:
            conn.execute("INSERT INTO discover_probes(ats, slug, checked_at, hit, source, error, next_try) "
                         "VALUES (?,?,?,0,?,?,?) ON CONFLICT(ats, slug) DO UPDATE SET error=excluded.error, "
                         "next_try=excluded.next_try", (ats, token, now_s, source, error, db.iso(now + RETRY_AFTER)))
            return
        conn.execute(
            "INSERT INTO discover_probes(ats, slug, checked_at, hit, n_jobs, board_name, website, source, error, "
            "next_try, us60, newest_us, name_src, verified_at) VALUES (?,?,?,?,?,?,?,?,NULL,NULL,?,?,?,?) "
            "ON CONFLICT(ats, slug) DO UPDATE SET checked_at=excluded.checked_at, hit=excluded.hit, "
            "n_jobs=excluded.n_jobs, board_name=coalesce(excluded.board_name, discover_probes.board_name), "
            "website=coalesce(excluded.website, discover_probes.website), "
            "source=coalesce(discover_probes.source, excluded.source), error=NULL, next_try=NULL, "
            "us60=excluded.us60, newest_us=excluded.newest_us, name_src=coalesce(excluded.name_src, "
            "discover_probes.name_src), verified_at=excluded.verified_at",
            (ats, token, now_s, int(verdict.n_jobs > 0), verdict.n_jobs, verdict.name, verdict.website, source,
             verdict.us60, verdict.newest_us, verdict.name_src, now_s))


FETCH_POLL_S = 10
# no board here: 404/410, a Workday tenant that moved data center or never existed there (422), a disabled site
# (401/403), a malformed token (400); 451 = robots.txt disallows it (never registered)
GONE_STATUSES = (400, 401, 403, 404, 410, 422, 451)


def wait_for_fetch() -> None:
    """Discovery shares hosts with the fetcher; it pauses while a fetch run holds data/fetch.lock, so the two never
    add up past the per-host rate (<= 3 req/s, Lever 1 req/s)."""
    path = config.DATA / "fetch.lock"
    while path.exists():
        with open(path, "a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
                fcntl.flock(lock, fcntl.LOCK_UN)
                return
            except BlockingIOError:
                pass
        time.sleep(FETCH_POLL_S)


def _one(ats: str, token: str, now: datetime, names) -> tuple[Verdict | None, str | None]:
    wait_for_fetch()
    try:
        return check(ats, token, now, names), None
    except HttpError as err:
        if err.status in GONE_STATUSES:
            return Verdict(0), None
        return None, str(err)[:300]
    except (ValueError, TypeError, AttributeError, KeyError) as err:     # odd payload: retry later, never a miss
        return None, f"parse: {err!r}"[:300]


def verify_all(conn, pairs: list[tuple[str, str]], source: str, now: datetime, workers: int = 8,
               names: Callable[[str], str | None] = lambda slug: None) -> dict:
    """Check every pair (threads), record on this thread. Returns counts per ATS. Each ATS gets its own threads: a
    vendor is one rate bucket, so a slow one (Cornerstone's 10 s Crawl-delay) must not hold every thread."""
    counts: dict[str, dict[str, int]] = {}
    pools = {ats: ThreadPoolExecutor(max_workers=workers) for ats in dict.fromkeys(a for a, _ in pairs)}
    try:
        futures = {pools[ats].submit(_one, ats, token, now, names): (ats, token) for ats, token in pairs}
        for i, future in enumerate(as_completed(futures), 1):
            ats, token = futures[future]
            verdict, error = future.result()
            record(conn, ats, token, source, now, verdict, error)
            bucket = counts.setdefault(ats, {"checked": 0, "live": 0, "us_live": 0, "errors": 0})
            bucket["checked"] += 1
            bucket["errors"] += error is not None
            bucket["live"] += bool(verdict and verdict.n_jobs)
            bucket["us_live"] += bool(verdict and verdict.us60)
            if i % 200 == 0:
                log.info("verified %d/%d %s", i, len(pairs), counts)
    finally:
        for pool in pools.values():
            pool.shutdown(cancel_futures=True)
    return counts
