"""more boards from the filings. Employers with data / AI H-1B filings in the last 12 months that job-hunt-agent
does not fetch yet, biggest filer first (cap-exempt employers too): find each one's career board with the existing
discover tools, verify it live, add it to config/companies.yaml.

Where a board is looked for, in order (the first verified one wins):
1. Greenhouse / Lever / Ashby slugs made from the name (discover._discover_one: cached probes, and the board's own
   name must be the company's);
2. the Workday tenant named like the company (discover_seed.tenant_sites: the tenant's robots.txt lists the career
   sites it allows);
3. the company's own careers page, on the domain of the filings' contact e-mail: https://<domain>/careers,
   https://careers.<domain>/, https://jobs.<domain>/. Every link on it that ats.detect knows is a candidate. net
   honours robots.txt on every request; job aggregators (LinkedIn, Indeed, Glassdoor, ...) are never used.
A candidate is added only when it lists an open US posting of the last 60 days (discover_verify.check) and, for a
Workday tenant guessed from the name, the board's name matches the company's. A careers page that only links to a
system job-hunt-agent cannot read is counted under that system's name (the report's "unreadable site types").

New boards join the fetch tiers like any other (a board with no matching job is read once a day, every 30 minutes
once it has one) and the fetch run's CPU budget still caps a run; no extra schedule.
"""
import json
import logging
import re
import sqlite3
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlsplit

from .. import config, db, discover, discover_more, discover_seed, discover_verify, seedlists
from ..ats import detect
from ..net import HttpError, get_text
from . import label, names

log = logging.getLogger(__name__)
CAREER_PAGES = ("https://{d}/careers", "https://careers.{d}/", "https://jobs.{d}/")
LINK = re.compile(r"""(?:href|src|action|data-url)\s*=\s*["']([^"'<>\s]{4,500})["']""", re.I)
NEVER = re.compile(r"linkedin\.com|indeed\.com|glassdoor\.com|ziprecruiter\.com|monster\.com|dice\.com|simplyhired", re.I)
# systems job-hunt-agent has no adapter for, by the host a careers page links to (the report's "unreadable site types")
UNREADABLE = (("avature.net", "Avature"), ("brassring.com", "BrassRing (Kenexa)"), ("ultipro.com", "UKG / UltiPro"),
              ("ukg.net", "UKG / UltiPro"), ("paycomonline.net", "Paycom"), ("silkroad.com", "SilkRoad"),
              ("peopleadmin.com", "PeopleAdmin"), ("governmentjobs.com", "NEOGOV"), ("talentbrew.com", "Radancy TalentBrew"),
              ("clinchtalent.com", "Clinch"), ("beamery.com", "Beamery"), ("phenompeople.com", "Phenom (custom)"),
              ("smartrecruiters.com", "SmartRecruiters (its robots.txt forbids)"), ("taleo.net", "Taleo (custom)"),
              ("oraclecloud.com", "Oracle (custom)"), ("eightfold.ai", "Eightfold (custom)"))
MAX_PAGE_CHARS = 3_000_000
WORKERS = 12
REPORT = "boards.json"
PROGRESS = "boards.progress.jsonl"     # one line per employer as it is done: a stopped run resumes where it was
NO_SITE = ("no page", "refused", "own site")
MAX_HOST_LABEL = 63                    # a longer tenant name cannot be a host name


def fetched_keys(conn) -> set[str]:
    """Name keys of every company job-hunt-agent reads now (the active boards), and the filer each one stands for
    (label.lookup: "Anthropic" reads "Anthropic, PBC"'s filings)."""
    idx = label.index(conn)
    out = set()
    for r in conn.execute("SELECT DISTINCT company FROM boards WHERE active = 1"):
        if r["company"]:
            out.add(names.key(r["company"]))
            found = label.lookup(idx, r["company"])
            if found:
                out.add(found["key"])
    return out


def known_boards(conn) -> set[str]:
    """Every board id job-hunt-agent has (fetched or not), lower case: Workday site names differ only in case."""
    return {r[0].lower() for r in conn.execute("SELECT board FROM boards")}


def targets(conn, limit: int, cap_limit: int = 0) -> list[dict]:
    """The biggest data / AI filers not fetched yet (one per name), then the biggest cap-exempt ones."""
    have = fetched_keys(conn)
    rows = [dict(r) for r in conn.execute(
        "SELECT key, name, status, recent_data, recent_all, domain FROM h1b_companies WHERE recent_data > 0 "
        "ORDER BY recent_data DESC, recent_all DESC")]
    fresh = [r for r in rows if r["key"] not in have]
    fresh = list({r["name"]: r for r in reversed(fresh)}.values())[::-1]       # a dba group repeats its lead
    top = fresh[:limit]
    cap = [r for r in fresh[limit:] if r["status"] == "cap_exempt"][:cap_limit]
    return top + cap


def links(html: str, base: str) -> list[str]:
    found = (urljoin(base, m.group(1).replace("&amp;", "&")) for m in LINK.finditer(html[:MAX_PAGE_CHARS]))
    return [u for u in dict.fromkeys(found) if u.startswith("http") and not NEVER.search(u)]


def site_type(urls: list[str]) -> str | None:
    hosts = {urlsplit(u).hostname or "" for u in urls}
    return next((label for marker, label in UNREADABLE if any(h == marker or h.endswith("." + marker) for h in hosts)),
                None)


def entry_for(name: str, found: dict) -> dict | None:
    """A companies.yaml entry (plus its board key as _key), or None for a detect() result the registry refuses."""
    entry = {"name": name, **{k: v for k, v in found.items() if v is not None}}
    try:
        return {**entry, "_key": config.validate_company(entry)["key"]}
    except config.ConfigError:
        return None


def from_careers_page(domain: str | None) -> tuple[list[dict], str]:
    """Boards the company's own careers page links to, and what the page was ('ats', a system's name, 'own site',
    'no page', 'refused', 'no domain')."""
    if not domain:
        return [], "no domain"
    kinds = []
    for pattern in CAREER_PAGES:
        url = pattern.format(d=domain)
        try:
            html = get_text(url)
        except HttpError as err:
            kinds.append("refused" if err.status in (401, 403, 451) else "no page")
            continue
        except (OSError, ValueError) as err:
            log.debug("careers page %s: %s", url, err)
            kinds.append("no page")
            continue
        urls = links(html, url)
        found = [f for f in (detect(u) for u in urls) if f]
        if found:
            return list({json.dumps(f, sort_keys=True): f for f in found}.values()), "ats"
        kinds.append(site_type(urls) or "own site")
    named = [k for k in kinds if k not in NO_SITE]
    return [], named[0] if named else next((k for k in ("own site", "refused") if k in kinds), "no page")


def _named_like(name: str, domain: str | None, found: dict) -> bool:
    """The board's token carries the company's compact name, a 4+ letter word of it, or its domain's name."""
    token = re.sub(r"[^a-z0-9]", "", str(found.get("token") or "").lower())
    label_ = (domain or "").split(".")[0].lower()
    words = names.words(name)
    pieces = [seedlists.compact(name), label_, *(w for w in words if len(w) >= 4), *words[:1]]
    return any(p and len(p) >= 2 and p in token for p in pieces)


COMMON_FIRST = frozenset({"american", "national", "united", "first", "global", "general", "international", "north",
                          "south", "pacific", "atlantic", "central", "great", "royal", "state", "community", "premier",
                          "advanced", "applied", "digital", "smart", "southern", "northern", "western", "eastern"})


def _same_name(name: str, board_name: str) -> bool:
    """The board's own employer name is the company's: same name, same key, or the same distinctive first word (5+
    letters, not "American" / "National" ...: "OneMain General Services" / "OneMain Financial")."""
    a, b = names.words(name), names.words(board_name)
    return (discover.names_match(name, board_name) or names.key(name) == names.key(board_name)
            or bool(a and b and a[0] == b[0] and len(a[0]) >= 5 and a[0] not in COMMON_FIRST))


def _verified(conn, name: str, found: dict, now, trusted: bool) -> dict | None:
    """The entry when the board lists a recent US posting (and, unless found through the company's own page or a
    name-checked probe, carries the company's name)."""
    entry = entry_for(name, found)
    if entry is None:
        return None
    try:
        verdict = discover_verify.check(entry["ats"], entry["_key"], now, (lambda _t: name) if trusted else (lambda _t: None))
    except HttpError as err:
        log.info("verify %s:%s: %s", entry["ats"], entry["_key"], err)
        return None
    except (KeyError, ValueError, TypeError) as err:          # a system without a verifier, an odd payload
        log.info("verify %s:%s skipped: %s", entry["ats"], entry["_key"], err)
        return None
    with discover._DB_LOCK:                                  # one sqlite connection, many threads
        discover_verify.record(conn, entry["ats"], entry["_key"], "h1b24", now, verdict)
    if not verdict.us60:
        return None
    if not trusted and not (verdict.name and _same_name(name, verdict.name)):
        return None                                       # a guessed tenant must carry the company's own name
    return {**entry, "us60": verdict.us60, "n_jobs": verdict.n_jobs}


def display_name(raw: str) -> str:
    """The filings' name as a board list writes it: 'GOLDMAN SACHS & CO. LLC' -> 'Goldman Sachs & Co.'."""
    name = discover_more.tidy_name(raw) or raw.strip()
    return name.title() if name.isupper() and len(name) > 4 else name


def find(conn, target: dict, now) -> dict:
    """{name, filings, status, how, board | None, site}: the first verified board for one employer."""
    name = display_name(target["name"])
    out = {"name": name, "target": target["name"], "filings": target["recent_data"], "status": target["status"]}
    for hit in discover._discover_one(conn, name, target["domain"], now):
        if not hit["review"]:
            board = _verified(conn, name, {"ats": hit["ats"], "token": hit["token"]}, now, trusted=True)
            if board:
                return {**out, "how": "slug", "board": board, "site": "ats"}
    tenant = seedlists.compact(name)
    if 3 <= len(tenant) <= MAX_HOST_LABEL:
        for key in discover_seed.tenant_sites(tenant):
            _tenant, wd, site = key.split("/")
            found = {"ats": "workday", "token": _tenant, "wd": wd, "site": site}
            board = _verified(conn, name, found, now, trusted=False)
            if board:
                return {**out, "how": "workday tenant", "board": board, "site": "ats"}
    found, kind = from_careers_page(target["domain"])
    for f in found:                    # a careers page may link to a partner's board: the name must fit
        board = _verified(conn, name, f, now, trusted=_named_like(name, target["domain"], f))
        if board:
            return {**out, "how": "careers page", "board": board, "site": "ats"}
    return {**out, "how": None, "board": None, "site": "board with nothing open in the US" if found else kind}


def _safe_find(conn, target: dict, now) -> dict:
    try:
        return find(conn, target, now)
    except (HttpError, sqlite3.Error, ValueError) as err:      # ValueError: a name no host can carry (idna)                              # a host that refused us: the next run tries again
        log.info("boards %s: %s", target["name"], err)
        return {"name": display_name(target["name"]), "target": target["name"], "filings": target["recent_data"],
                "status": target["status"], "how": None, "board": None, "site": "refused"}


def add(boards: list[dict], known: set[str] = frozenset()) -> list[dict]:
    """Append the new boards to config/companies.yaml (a board already there, or one job-hunt-agent already has from
    discovery (`known`, lower case), is skipped)."""
    existing = config.companies_raw()
    have = {config.validate_company(e)["board"].lower() for e in existing} | set(known)
    added = []
    for b in boards:
        entry = {k: v for k, v in b.items() if not k.startswith("_") and k not in ("us60", "n_jobs")}
        board = config.validate_company(entry)["board"].lower()
        if board not in have:
            added.append(entry)
            have.add(board)
    if added:
        config.save_companies(existing + added)
    return added


def run(limit: int = 200, cap_limit: int = 50, write: bool = False, workers: int = WORKERS) -> dict:
    """Look for boards for the top `limit` unfetched data / AI filers (+ `cap_limit` cap-exempt ones); with write,
    add the verified ones to config/companies.yaml. The result is also kept in data/h1b/boards.json."""
    conn, now = db.connect(), db.utcnow()
    todo = targets(conn, limit, cap_limit)
    progress = config.DATA / "h1b" / PROGRESS
    progress.parent.mkdir(parents=True, exist_ok=True)
    done = _done(progress)
    lock = threading.Lock()

    def one(t: dict) -> dict:
        if t["name"] in done:
            return done[t["name"]]
        result = _safe_find(conn, t, now)
        with lock, progress.open("a") as out:
            out.write(json.dumps(result) + "\n")
        return result
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(one, todo))
    known = known_boards(conn)
    found = [r["board"] for r in results if r["board"]]
    boards = [b for b in found if f"{b['ats']}:{b['_key']}".lower() not in known]
    added = add(boards, known) if write else []
    report = {"checked_at": db.iso(now), "employers_checked": len(todo),
              "cap_exempt_checked": sum(1 for t in todo if t["status"] == "cap_exempt"),
              "boards_found": len(found), "already_read": len(found) - len(boards), "boards_added": len(added),
              "found_by": dict(Counter(r["how"] for r in results if r["board"])),
              "by_ats": dict(Counter(b["ats"] for b in boards)),
              "unreadable": dict(Counter(r["site"] for r in results if not r["board"])),
              "added": added,
              "results": [{**{k: v for k, v in r.items() if k != "board"},
                           "board": f"{r['board']['ats']}:{r['board']['_key']}" if r["board"] else None}
                          for r in results]}
    path = config.DATA / "h1b" / REPORT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=1))
    return report


def _done(path) -> dict[str, dict]:
    """Employers a stopped run already finished (keyed by the filings' name)."""
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return {}
    out = {}
    for line in lines:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        out[r["target"]] = r
    return out


def text(rep: dict) -> str:
    lines = [f"Employers checked: {rep['employers_checked']} (cap-exempt {rep['cap_exempt_checked']}); boards found "
             f"{rep['boards_found']} ({rep['already_read']} job-hunt-agent already reads), added {rep['boards_added']}",
             "Found by: " + ", ".join(f"{k} {v}" for k, v in rep["found_by"].items()),
             "Systems: " + ", ".join(f"{k} {v}" for k, v in sorted(rep["by_ats"].items(), key=lambda kv: -kv[1])),
             "No board (what the careers page was): " + ", ".join(
                 f"{k} {v}" for k, v in sorted(rep["unreadable"].items(), key=lambda kv: -kv[1]))]
    lines += [f"  + {a['name']}: {a['ats']} {a['token']}" for a in rep["added"]]
    return "\n".join(lines)
