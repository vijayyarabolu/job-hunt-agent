"""Registry discovery (GOAL §2): slug candidates -> probe Greenhouse/Lever/Ashby, verify Workday tenants, `add <url>`."""
import logging
import re
import threading
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlsplit

import openpyxl
import yaml

from . import board_content, config, db
from .ats import detect, workday
from .net import HttpError, get_json, get_text, post_json
from .text import html_to_text, norm

PROBE_TTL = timedelta(days=30)
MAX_SLUGS = 4
PROBE_ATS = ("greenhouse", "lever", "ashby")
_SUFFIXES = {"inc", "llc", "corp", "corporation", "co", "ltd", "limited", "technologies", "technology", "systems",
             "labs", "ai", "hq", "group", "holdings", "plc", "com", "io"}
# Generic words a board name may add after the company name ('Rubrik Job Board', 'Chime Financial, Inc').
# Industry words (health, bio, ...) are NOT here: 'Runway' vs 'Runway Health' is a different company.
_DESCRIPTORS = _SUFFIXES | {"security", "financial", "data", "lab", "software", "job", "jobs", "board", "careers",
                            "company", "platform", "global"}
_ATS_DOMAINS = {"greenhouse.io", "lever.co", "ashbyhq.com", "myworkdayjobs.com", "myworkdaysite.com",
                "smartrecruiters.com", "careerpuck.com", "workable.com", "jobvite.com", "icims.com", "bamboohr.com",
                "taleo.net", "oraclecloud.com", "successfactors.com", "successfactors.eu"}
_SECOND_LEVEL = {"co.uk", "org.uk", "ac.uk", "com.au", "co.in", "com.br", "co.jp", "github.io"}
_DOMAIN_IN_TEXT = re.compile(r"(?:https?://|@)(?:www\.)?([a-z0-9-]+(?:\.[a-z0-9-]+)+)", re.I)
log = logging.getLogger(__name__)
_DB_LOCK = threading.Lock()      # one sqlite connection is shared by the probe threads
PROBE_URLS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
    "lever": "https://api.lever.co/v0/postings/{slug}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}",
}
_ASHBY_SITE = re.compile(r'"publicWebsite":"(https?://[^"]+)"')
_TITLE = re.compile(r"<title>(.*?)</title>", re.S | re.I)
_LEVER_LOGO = re.compile(r'<a[^>]*class="main-header-logo"[^>]*>', re.I)
_HREF = re.compile(r'href="([^"]+)"')


class Probe(NamedTuple):
    n_jobs: int                  # 0 = no board / no jobs
    board_name: str | None       # the ATS's own display name for the board
    website: str | None          # company website the board links to, when the ATS exposes one


def _ascii(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def slug_candidates(name: str) -> list[str]:
    """Ordered slug guesses: whole name joined/hyphenated, then without corporate suffixes, then +ai."""
    words = re.sub(r"[^\w\s-]", " ", _ascii(name).lower().replace("&", " ")).split()
    stripped = list(words)
    while len(stripped) > 1 and stripped[-1] in _SUFFIXES:
        stripped.pop()
    if all(w in _SUFFIXES for w in stripped):       # nothing but 'AI'/'Labs' left (e.g. a CJK name + 'AI')
        return []
    options = ["".join(words), "-".join(words), "".join(stripped), "-".join(stripped), "".join(stripped) + "ai"]
    return [s for s in dict.fromkeys(options) if s and re.fullmatch(r"[a-z0-9-]+", s)][:MAX_SLUGS]


def _name_tokens(name: str) -> list[str]:
    words = norm(_ascii(name)).split()
    while len(words) > 1 and words[-1] in _SUFFIXES:
        words.pop()
    return words


def names_match(company: str, board_name: str | None) -> bool:
    """Same name up to corporate suffixes, spacing and generic trailing words: 'Character AI' ~ 'Character.AI',
    'Domino Data Lab' ~ 'Domino'. Other extra words are NOT accepted ('Neon' !~ 'Neon Pagamentos', 'Ro' !~ 'Ross')."""
    if not board_name:
        return False
    a, b = _name_tokens(company), _name_tokens(board_name)
    if not a or not b:
        return False
    short, long_ = sorted((a, b), key=len)
    if long_[:len(short)] == short and set(long_[len(short):]) <= _DESCRIPTORS:
        return True
    no_initials = all(len(w) > 1 or w.isdigit() for w in a + b)   # 'S.H.I.E.L.D.' must not become 'shield'
    return no_initials and "".join(a) == "".join(b)


def domain_of(url: str | None) -> str | None:
    """'https://www.gomotive.com/careers' -> 'gomotive.com'; 'news.bbc.co.uk' -> 'bbc.co.uk'."""
    if not url:
        return None
    host = (urlsplit(url if "://" in url else f"https://{url}").hostname or "").lower()
    labels = host.removeprefix("www.").split(".")
    keep = 3 if ".".join(labels[-2:]) in _SECOND_LEVEL else 2
    return ".".join(labels[-keep:]) if len(labels) >= keep else None


def _company_site(url: str | None) -> str | None:
    """A link counts as the company website only if it is not an ATS-hosted page."""
    domain = domain_of(url)
    return url if domain and domain not in _ATS_DOMAINS else None


def identity_ok(company: str, hint_domain: str | None, probe_: Probe) -> bool:
    """Board belongs to the company: names match and, when a domain hint is known and the ATS exposes a website,
    the domains agree (needed for same-name collisions such as Circle = circle.com vs circle.so)."""
    if not names_match(company, probe_.board_name):
        return False
    if not hint_domain:
        return True
    return domain_of(probe_.website) == domain_of(hint_domain)     # a hint without a visible website: review


def _board_identity(ats: str, slug: str, jobs: list) -> tuple[str | None, str | None]:
    """(display name, website). Transient errors propagate so a failed lookup is never cached."""
    if ats == "greenhouse":
        try:
            board = get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}") or {}
        except HttpError as err:
            if err.is_transient:
                raise
            board = {}
        urls = [j.get("absolute_url") for j in jobs if isinstance(j, dict)]
        urls += [f"https://{d}" for d in _DOMAIN_IN_TEXT.findall(html_to_text(board.get("content"), True))]
        return board.get("name"), next((u for u in urls if _company_site(u)), None)
    if ats == "lever":
        try:
            page = get_text(f"https://jobs.lever.co/{slug}")
        except HttpError as err:
            if err.is_transient:
                raise
            return None, None                # hosted page gone (404) while the postings API works: review
        title = _TITLE.search(page)
        logo = _LEVER_LOGO.search(page)
        href = _HREF.search(logo.group(0)) if logo else None
        return (html_to_text(title.group(1)).strip() if title else None), _company_site(href and href.group(1))
    # Ashby: the hosted page's <title> ('OpenAI Jobs') and publicWebsite. jobs.ashbyhq.com robots.txt disallows
    # /api/, so the GraphQL endpoint v1 used is not called any more (decision 005).
    try:
        page = get_text(f"https://jobs.ashbyhq.com/{slug}")
    except HttpError as err:
        if err.is_transient:
            raise
        return None, None
    title, site = _TITLE.search(page), _ASHBY_SITE.search(page)
    name = re.sub(r"\s+(Jobs|Careers)$", "", html_to_text(title.group(1)).strip()) if title else None
    return name, _company_site(site and site.group(1))


def _probe_live(ats: str, slug: str) -> Probe:
    try:
        payload = get_json(PROBE_URLS[ats].format(slug=slug))
    except HttpError as err:
        if err.status in (400, 404, 410):
            return Probe(0, None, None)
        raise
    jobs = payload if isinstance(payload, list) else (payload or {}).get("jobs") or []
    if not jobs:
        return Probe(0, None, None)
    return Probe(len(jobs), *_board_identity(ats, slug, jobs))


def probe(conn, ats: str, slug: str, now) -> Probe:
    """Cached probe (30-day TTL in discover_probes). Transient failures raise and are not cached."""
    with _DB_LOCK:
        row = conn.execute("SELECT * FROM discover_probes WHERE ats=? AND slug=?", (ats, slug)).fetchone()
    if row and db.iso(now - PROBE_TTL) < row["checked_at"]:
        return Probe(row["n_jobs"] or 0, row["board_name"], row["website"])
    found = _probe_live(ats, slug)
    with _DB_LOCK, db.tx(conn):
        conn.execute("INSERT OR REPLACE INTO discover_probes(ats, slug, checked_at, hit, n_jobs, board_name, website) "
                     "VALUES (?,?,?,?,?,?,?)", (ats, slug, db.iso(now), int(found.n_jobs > 0), *found))
    return found


def content_note(ats: str, slug: str, jds: list[frozenset[str]] | None) -> str | None:
    """Review note when the tracker's stored JD shares nothing with the board's postings (same-name stranger)."""
    if not jds:
        return None
    try:
        same = board_content.matches(jds, board_content.board_text(ats, slug))
    except (HttpError, ValueError, TypeError, AttributeError) as err:     # odd payload: no evidence either way
        log.warning("content check %s:%s skipped: %s", ats, slug, err)
        return None
    return "tracker JD text does not match this board's postings" if same is False else None


def _discover_one(conn, name: str, hint_domain: str | None, now, jds: list[frozenset[str]] | None = None) -> list[dict]:
    """First live slug per ATS; review=True unless the board's own name (and website, if hinted) match and, for
    tracker names, the stored JD resembles the board's postings."""
    hits = []
    for ats in PROBE_ATS:
        for slug in slug_candidates(name):
            try:
                found = probe(conn, ats, slug, now)
            except HttpError:
                break                       # transient failure: try again next discover run
            if found.n_jobs:
                ok = identity_ok(name, hint_domain, found)
                note = None if ok else f"board name {found.board_name!r} does not match"
                note = note or content_note(ats, slug, jds)
                hits.append({"name": name, "ats": ats, "token": slug, "n_jobs": found.n_jobs,
                             "board_name": found.board_name, "review": bool(note), "note": note})
                break
    return hits


def best_hit(hits: list[dict]) -> dict | None:
    """One board per company: a verified board beats a review one; then the most jobs."""
    return min(hits, key=lambda h: (h["review"], -h["n_jobs"]), default=None)


def parse_seed_line(line: str) -> tuple[str, str | None]:
    """'Circle | circle.com' -> ('Circle', 'circle.com'); the domain hint is optional."""
    name, _, domain = line.partition("|")
    return name.strip(), (domain.strip().lower() or None)


def seed_names(tracker: str | None = None, seed_file: str | None = None) -> tuple[dict[str, list[str]], dict]:
    """({'tracker': [...], 'seed': [...]} de-duplicated across sources, {name: domain hint})."""
    sources: dict[str, list[str]] = {"tracker": [], "seed": []}
    tracker_path = tracker or config.DATA / "import" / "Application_Tracker.xlsx"
    try:
        sheet = openpyxl.load_workbook(tracker_path, read_only=True).worksheets[0]
        sources["tracker"] = [str(r[0]).strip() for r in sheet.iter_rows(min_row=2, max_col=1, values_only=True)
                              if r[0] and str(r[0]).strip()]
    except FileNotFoundError:
        pass
    seed_path = Path(seed_file or config.shipped("seed_ai_companies.txt"))
    lines = seed_path.read_text().splitlines() if seed_path.exists() else []
    seeds = [parse_seed_line(ln) for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]
    sources["seed"] = [name for name, _ in seeds]
    hints = {norm(name): domain for name, domain in seeds if domain}
    unique: dict[str, str] = {}
    for src in ("tracker", "seed"):
        sources[src] = [unique.setdefault(norm(n), n) for n in sources[src] if norm(n) and norm(n) not in unique]
    return sources, {name: hints[norm(name)] for name in unique.values() if norm(name) in hints}


def discover(workers: int = 6) -> dict:
    """Probe every seed name; add each company's best verified board to companies.yaml (review-flagged boards
    are added with review: true and are not fetched until a human clears the flag)."""
    conn, now = db.connect(), db.utcnow()
    sources, hints = seed_names()
    jds = board_content.tracker_jds(conn)
    names = [n for src in sources.values() for n in src]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = dict(zip(names, pool.map(lambda n: _discover_one(conn, n, hints.get(n), now, jds.get(norm(n))),
                                           names)))
    existing = config.companies_raw()
    have = {(e["ats"], e["token"]) for e in existing}
    have_names = {norm(e["name"]) for e in existing if not e.get("review")}   # a review-only name may still
                                                                                # get its verified board
    added = []
    for name in names:
        hit = best_hit(results[name])
        if not hit or (hit["ats"], hit["token"]) in have or norm(name) in have_names:
            continue
        entry = {"name": name, "ats": hit["ats"], "token": hit["token"]}
        if hit["review"]:
            entry |= {"review": True, "note": hit["note"]}
        added.append(entry)
        have.add((hit["ats"], hit["token"]))
    config.save_companies(existing + added)
    return _report(sources, results)


def _report(sources: dict[str, list[str]], results: dict[str, list[dict]]) -> dict:
    """Hit rate counts only verified (non-review) boards; review hits are reported separately."""
    report = {}
    for src, names in sources.items():
        best = [h for h in (best_hit(results[n]) for n in names) if h]
        verified = [h for h in best if not h["review"]]
        report[src] = {"names": len(names), "hits": len(verified),
                       "hit_rate": round(len(verified) / max(1, len(names)), 3),
                       "by_ats": dict(Counter(h["ats"] for h in verified)),
                       "needs_review": len(best) - len(verified)}
    return report


def _reverify_one(conn, entry: dict, hint_domain: str | None, now, jds: list[frozenset[str]] | None = None) -> str | None:
    """Live identity check of an existing board; returns a review note when it fails, else None."""
    try:
        found = _probe_live(entry["ats"], entry["token"])
    except HttpError:
        return None                          # unreachable now: leave the entry as it is
    with _DB_LOCK, db.tx(conn):
        conn.execute("INSERT OR REPLACE INTO discover_probes(ats, slug, checked_at, hit, n_jobs, board_name, website) "
                     "VALUES (?,?,?,?,?,?,?)", (entry["ats"], entry["token"], db.iso(now), int(found.n_jobs > 0),
                                                *found))
    if not found.n_jobs:
        return "board has no live jobs (gone or renamed?)"
    if not identity_ok(entry["name"], hint_domain, found):
        return f"board is {found.board_name!r} ({domain_of(found.website) or 'no website'})"
    return content_note(entry["ats"], entry["token"], jds)


def reverify(workers: int = 6) -> dict:
    """`discover --reverify`: re-check every Greenhouse/Lever/Ashby entry's board identity and flag mismatches with
    review: true (+ note). Never removes entries, never clears a flag, skips entries a human marked verified: true."""
    conn, now = db.connect(), db.utcnow()
    _, hints = seed_names()
    hints = {norm(k): v for k, v in hints.items()}
    entries = config.companies_raw()
    todo = [e for e in entries if e["ats"] in PROBE_ATS and not e.get("review") and not e.get("verified")]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        jds = board_content.tracker_jds(conn)
        notes = list(pool.map(lambda e: _reverify_one(conn, e, hints.get(norm(e["name"])), now,
                                                      jds.get(norm(e["name"]))), todo))
    flagged = {id(e): note for e, note in zip(todo, notes) if note}
    updated = [{**e, "review": True, "note": flagged[id(e)]} if id(e) in flagged else e for e in entries]
    config.save_companies(updated)
    return {"checked": len(todo), "flagged": sorted(f"{e['name']} -> {e['ats']}:{e['token']}: {flagged[id(e)]}"
                                                   for e in entries if id(e) in flagged)}


def verify_workday(entry: dict) -> int:
    """Live check of a Workday tenant: total postings for an empty search (0 = dead/misconfigured)."""
    _, api = workday.urls(f"{entry['token']}/{entry['wd']}/{entry['site']}")
    try:
        page = post_json(f"{api}/jobs", {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""})
    except HttpError:
        return 0
    return int((page or {}).get("total") or 0)


def verify_workday_candidates(path=None) -> dict:
    """Verify config/workday_candidates.yaml live; merge passing tenants into companies.yaml; drop failures."""
    candidates = yaml.safe_load(open(path or config.shipped("workday_candidates.yaml")))["tenants"]
    with ThreadPoolExecutor(max_workers=8) as pool:
        totals = list(pool.map(verify_workday, candidates))
    passed = [{**c, "ats": "workday"} for c, n in zip(candidates, totals) if n > 0]
    existing = config.companies_raw()
    have = {(e["ats"], e["token"], e.get("site")) for e in existing}
    config.save_companies(existing + [p for p in passed if ("workday", p["token"], p["site"]) not in have])
    failed = [c["name"] for c, n in zip(candidates, totals) if n == 0]
    return {"candidates": len(candidates), "verified": len(passed),
            "hit_rate": round(len(passed) / max(1, len(candidates)), 3), "failed": failed}


def _live_count(found: dict) -> int:
    if found["ats"] == "workday":
        return verify_workday(found)
    if found["ats"] == "smartrecruiters":
        payload = get_json(f"https://api.smartrecruiters.com/v1/companies/{found['token']}/postings?limit=1")
        return int((payload or {}).get("totalFound") or 0)
    return _probe_live(found["ats"], found["token"]).n_jobs


def add(url: str, name: str | None = None) -> dict:
    """`job-hunt-agent add <careers-url>`: detect ATS + token, verify live, append to companies.yaml."""
    found = detect(url)
    if found and found["ats"] == "smartrecruiters":
        raise ValueError("SmartRecruiters is not supported: api.smartrecruiters.com robots.txt disallows all agents")
    if not found:
        raise ValueError(f"not a supported ATS careers URL: {url} (supported: boards/job-boards.greenhouse.io, "
                         "jobs.lever.co, jobs.ashbyhq.com, jobs.smartrecruiters.com, *.myworkdayjobs.com, "
                         "*.myworkdaysite.com)")
    entry = {"name": name or found["token"], **found}
    try:
        n_jobs = _live_count(found)
    except HttpError as err:
        raise ValueError(f"could not reach {found['ats']} board {found['token']!r}: {err}") from err
    if not n_jobs:
        raise ValueError(f"{found['ats']} board {found['token']!r} has no live jobs; not added")
    existing = config.companies_raw()
    if not any(e["ats"] == entry["ats"] and e["token"] == entry["token"] and e.get("site") == entry.get("site")
               for e in existing):
        config.save_companies(existing + [entry])
    return {**entry, "n_jobs": n_jobs}
