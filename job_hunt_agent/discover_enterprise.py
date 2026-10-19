"""`discover --verify` checks for the GOAL10 enterprise systems: Oracle Recruiting Cloud, Taleo, SuccessFactors,
classic iCIMS and Jobvite. Same contract as discover_verify: one Verdict per board, HttpError for a transient failure.

Admission ("employers that hire data / AI / ML people"; §3 "keep it light"): a board counts as US-live
(us60 > 0) only when a US posting is recent AND a data/AI/ML title (any profile family, titles.match) is open in the
US now. Most of these boards are search boards that cost one request per search term on every fetch, so a big board
with no such title is left out rather than fetched every 30 minutes for nothing.

- Oracle: the newest 200 requisitions (posting day, country) plus, when none of them matches, PROBE_TERMS searches.
- Taleo: the section's newest rows and PROBE_TERMS searches (posting day and location columns); a section with no
  date column is read like SuccessFactors (a US matched title open now counts as recent).
- SuccessFactors / Jobvite: the newest rows and PROBE_TERMS searches / the full list; they carry no reliable date, so a US
  row with a matched title open now counts as recent (newest = the job page's date, else now).
- classic iCIMS: the sitemap titles, then up to SAMPLE matched job pages (JSON-LD datePosted, location, employer).

Names: config/lists/board_names.csv (ats, key, name) wins, so an S&P company keeps its S&P name across monthly
re-verifies; else the employer the pages name (iCIMS hiringOrganization, SuccessFactors microdata), else the page
title without its 'Careers' / 'Candidate Experience' words.
"""
import re
from datetime import datetime

from . import seedlists, signals, titles
from .ats import icims, icims_classic, jobvite, jsonld, oracle_recruiting, successfactors, taleo
from .discover_verify import Verdict, clean_name, us_recent
from .ats.common import SchemaDrift
from .net import get_json, get_text

SAMPLE = 3
PROBE_TERMS = ("data scientist", "machine learning", "data engineer")     # stop at the first relevant one
ORACLE_PROBE = 200
_TITLE = re.compile(r"<title>(.*?)</title>", re.S | re.I)
_SITE_WORDS = re.compile(r"(?i)\b(candidate experience( page)?|job search|job listings at|careers? (site|page)|"
                         r"careers?|jobs?|home|external|internal|site|talent acquisition( team)?|experienced|"
                         r"lateral hiring|campus|global support center|unavailable)\b|\((us|usa)\)")
_REPEAT = re.compile(r"(?i)\b(\w+)( \1\b)+")


def page_name(page: str) -> str | None:
    found = _TITLE.search(page or "")
    if not found:
        return None
    return clean_name(_REPEAT.sub(r"\1", " ".join(_SITE_WORDS.sub(" ", re.sub(r"[|\-–:]", " ", found.group(1))).split())))


def relevant(entries: list[tuple[str, str | None, str | None]]) -> bool:
    """(title, location, country) rows: any US (or unknown-place) row with a data/AI/ML title."""
    return any(titles.match(t) and signals.is_us(loc or "", country) is not False for t, loc, country in entries)


_PORTAL_PREFIX = re.compile(r"^(?:[a-z]*careers?|jobs?|external\w*|app|staff|clinical|us|global)-|-(?:careers?|jobs?)$")


def slug_name(ats: str, key: str) -> str:
    """Last-resort display name from the key: 'careers-stifel.icims.com' -> 'Stifel', 'hdr.taleo.net/ex' -> 'Hdr'."""
    head = key.split("/")[0]
    if ats == "jobvite":
        return head.replace("-", " ").title()
    if ats == "successfactors":
        parts = head.split(".")
        return (parts[-2] if len(parts) >= 2 else head).title()
    return _PORTAL_PREFIX.sub("", head.split(".")[0]).replace("-", " ").title()


def _name(ats: str, key: str, found: str | None) -> tuple[str, str]:
    listed = seedlists.board_name(ats, key)
    if listed:
        return listed, "list"
    return (found, "page") if found else (slug_name(ats, key), "slug")


def _now(now: datetime) -> str:
    return now.strftime("%Y-%m-%dT%H:%M:%SZ")


def _probe(search) -> list[tuple[str, str | None, str | None]]:
    """Rows (title, location, country) of the first probe term whose results are relevant, else of the last."""
    seen: list = []
    for term in PROBE_TERMS:
        seen = search(term)
        if relevant(seen):
            break
    return seen


def _oracle_seen(reqs: list[dict]) -> list[tuple[str, str | None, str | None]]:
    return [(r.get("Title") or "", r.get("PrimaryLocation"), r.get("PrimaryLocationCountry")) for r in reqs]


def check_oracle(key: str, now: datetime) -> Verdict:
    host, site = oracle_recruiting.split_key(key)
    if oracle_recruiting.SANDBOX.match(host):
        return Verdict(0, 0, None)
    payload = get_json(oracle_recruiting.list_url(host, site, None, ORACLE_PROBE))
    reqs = oracle_recruiting.requisitions(payload)
    total = int(((payload.get("items") or [{}])[0] or {}).get("TotalJobsCount") or len(reqs))
    us60, newest = us_recent([(oracle_recruiting.posted_day(r.get("PostedDate")), r.get("PrimaryLocation"),
                               r.get("PrimaryLocationCountry")) for r in reqs], now)
    seen = _oracle_seen(reqs)
    if us60 and not relevant(seen):
        seen = _probe(lambda term: _oracle_seen(oracle_recruiting.requisitions(get_json(
            oracle_recruiting.list_url(host, site, term, 50)))))
    if not us60 or not relevant(seen):
        return Verdict(total, 0, None)
    page = get_text(f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}")
    name, src = _name("oracle_recruiting", key, page_name(page))
    return Verdict(total, us60, newest, name, src)


def check_taleo(key: str, now: datetime) -> Verdict:
    host, section = taleo.split_key(key)
    reqs = taleo.search_rows(host, section, None)
    rows = [(taleo.posted_day(r), "; ".join(taleo.places(r)), None) for r in reqs]
    us60, newest = us_recent(rows, now)
    undated = bool(rows) and not any(day for day, _, _ in rows)      # a section that shows no date column
    if not us60 and not undated:
        return Verdict(len(reqs), 0, None)
    seen = _probe(lambda term: [(taleo.title(r), "; ".join(taleo.places(r)), None)
                                for r in taleo.search_rows(host, section, term)])
    if undated:       # as SuccessFactors: a matched title open now in a clearly US place counts as recent
        us60, newest = sum(1 for t, loc, _ in seen if titles.match(t) and signals.is_us(loc, None)), _now(now)
    if not us60 or not relevant(seen):
        return Verdict(len(reqs), 0, None)
    name, src = _name("taleo", key, page_name(get_text(taleo.search_page_url(host, section))))
    return Verdict(len(reqs), us60, newest, name, src)


def check_successfactors(key: str, now: datetime) -> Verdict:
    first_page = get_text(successfactors.search_url(key, None))
    newest_rows = successfactors.rows(first_page)
    if not newest_rows:
        return Verdict(0)
    probe: list[dict] = []
    for term in PROBE_TERMS:
        probe = successfactors.rows(get_text(successfactors.search_url(key, term)))
        if any(titles.match(r["title"]) and successfactors.is_us(r) for r in probe):
            break
    hits = [r for r in newest_rows + probe if titles.match(r["title"]) and successfactors.is_us(r)]
    if not hits:
        return Verdict(len(newest_rows), 0, None)
    detail = successfactors.parse_detail(get_text(successfactors.job_url(key, hits[0]))) or {}
    name, src = _name("successfactors", key, clean_name(detail.get("organization")) or page_name(first_page))
    return Verdict(len(newest_rows), len({r["id"] for r in hits}), detail.get("posted") or _now(now), name, src)


def check_jobvite(slug: str, now: datetime) -> Verdict:
    listed, _ = jobvite.listing(slug)
    hits = [r for r in listed if titles.match(r["title"]) and signals.is_us(r["location"], None) is not False]
    if not hits:
        return Verdict(len(listed), 0, None)
    name, src = _name("jobvite", slug, page_name(get_text(f"{jobvite.BASE}/{slug}/jobs")))
    return Verdict(len(listed), len(hits), _now(now), name, src)


def check_icims(host: str, now: datetime) -> Verdict:
    listed = icims_classic.entries(host, get_text(icims_classic.sitemap_url(host)))
    matched = [e for e in listed if titles.match(icims_classic.slug_title(e["slug"]))]
    if not matched:
        return Verdict(len(listed), 0, None)
    pages = (get_text(f"{icims_classic.job_url(host, e['id'], e['slug'])}?in_iframe=1") for e in matched[:SAMPLE])
    postings = [p for p in map(jsonld.job_posting, pages) if p]
    us60, newest = us_recent([(jsonld.posted(p), *jsonld.location(p)) for p in postings], now)
    if not us60:
        return Verdict(len(listed), 0, None)
    org = next((page_name(f"<title>{jsonld.organization(p)}</title>") for p in postings if jsonld.organization(p)),
               None)
    name, src = _name("icims_classic", host, org)
    return Verdict(len(listed), us60, newest, name, src)


def check_icims_jibe(host: str, now: datetime) -> Verdict:
    """An iCIMS career site on the Jibe front end (GOAL5's `icims` adapter): big employers often close their classic
    portal (robots `Disallow: /`, IP allow-list) and publish through this JSON instead."""
    query = f"https://{host}/api/jobs?country={icims.COUNTRY.replace(' ', '%20')}&page=1&limit=100&sortBy=posted_date"
    payload = get_json(query)
    postings = [row.get("data") for row in payload.get("jobs") or [] if isinstance(row, dict)]
    postings = [p for p in postings if isinstance(p, dict)]
    total = int(payload.get("totalCount") or payload.get("count") or len(postings))
    rows = [(icims._when(p.get("posted_date") or p.get("create_date")), p.get("full_location") or p.get("location_name"),
             p.get("country_code") or p.get("country")) for p in postings]
    us60, newest = us_recent(rows, now)
    seen = [(p.get("title") or "", loc, country) for p, (_, loc, country) in zip(postings, rows)]
    if us60 and not relevant(seen):
        seen = _probe(lambda term: [((d.get("title") or ""), d.get("full_location"), d.get("country_code"))
                                    for d in icims._pages(host, term)])
    if not us60 or not relevant(seen):
        return Verdict(total, 0, None)
    org = next((p.get("hiring_organization") for p in postings if p.get("hiring_organization")), None)
    name, src = _name("icims", host, clean_name(org) or slug_name("successfactors", host))
    return Verdict(total, us60, newest, name, src)


def checked(fn):
    """A page these systems serve but we cannot read as a job list (a custom landing page, an unknown layout) is
    recorded as no board (n_jobs 0), not retried every day as a transient parse error."""
    def run(key: str, now: datetime) -> Verdict:
        try:
            return fn(key, now)
        except SchemaDrift:
            return Verdict(0)
    return run


CHECKS = {ats: checked(fn) for ats, fn in {
    "oracle_recruiting": check_oracle, "taleo": check_taleo, "successfactors": check_successfactors,
    "jobvite": check_jobvite, "icims_classic": check_icims, "icims": check_icims_jibe}.items()}
