"""The board registry the fetcher uses: curated config/companies.yaml + generated config/companies.discovered.yaml
(decision 005 B1 step 4) + the employers' own boards found from the state job banks (data/employer_boards.yaml,
GOAL36 §2). The curated file wins on every conflict and v1 boards are never double-counted.

build() turns verified discover_probes rows into the discovered file:
- admitted = live (hit=1) with >= 1 US posting in the last 60 days, on a fetchable ATS, robots-allowed
- Workday, Oracle Recruiting, Taleo: one site per tenant host (the largest; an Oracle tenant shows the same
  requisitions under several site numbers), and no tenant the curated file already has
- dedupe across ATSs on the compact company name (+ website domain when both boards expose one): the curated board
  wins, else the board with more recent US postings, then the newest US posting
- every board gets a segment (segments.py); nothing goes to a human review queue
"""
import json
import logging
from collections import defaultdict
from pathlib import Path

import yaml

from . import company_names, config, db, employer_boards, jobbanks, past_employers, segments, seedlists
from .ats import ADAPTERS, KEY_FIELDS, board_key

DISCOVERED = "companies.discovered.yaml"
_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
SITED_ATS = ("workday", "oracle_recruiting", "taleo")      # one tenant, several sites over the same postings
FETCHABLE = ("greenhouse", "lever", "ashby", "workday",    # Workable deferred (decision 005 run 2)
             "oracle_recruiting", "taleo", "successfactors", "icims_classic", "jobvite",      # GOAL10
             "icims",                  # iCIMS Jibe career sites (GOAL5 adapter), found by GOAL10's seed probe
             "bamboohr", "rippling", "gem", "recruitee", "breezy", "pinpoint", "hibob", "jobscore",  # GOAL16 Tier A
             "adp", "paylocity", "dayforce", "cornerstone", "jazzhr")                          # GOAL16 Tier B
GOAL16_ATS = ("bamboohr", "rippling", "gem", "recruitee", "breezy", "pinpoint", "hibob", "jobscore",
              "adp", "paylocity", "dayforce", "cornerstone", "jazzhr")
log = logging.getLogger(__name__)
JOBBANK_BOARD = {"board": "careeronestop:us", "ats": "careeronestop", "key": "us", "company": "State job banks",
                 "tier": None, "review": False, "segment": None, "source": "jobbank", "website": None}


def discovered_path() -> Path:
    """Where the discovered registry is read from: the user's rebuilt copy, else the shipped one."""
    return config.shipped(DISCOVERED)


def discovered_raw() -> list[dict]:
    path = discovered_path()
    if not path.exists():
        return []
    # the C loader when PyYAML has it: the GOAL16 file (24k boards) takes ~5 s of CPU on the pure-Python one
    return (yaml.load(path.read_text(), Loader=_LOADER) or {}).get("companies", [])


def _board(entry: dict, source: str) -> dict:
    """the display name is the one pinned in board_names.csv, else the listed name without legal suffixes."""
    board = config.validate_company(entry)
    name = seedlists.board_name(board["ats"], board["key"]) or company_names.tidy(board["company"])
    return {**board, "company": name, "segment": entry.get("segment"), "source": entry.get("source", source),
            "website": entry.get("website")}


def boards() -> list[dict]:
    """Curated + discovered boards for fetch. Unknown ATSs in the discovered file are skipped (never fatal). 
    a board whose company is a past employer of every profile is left out (never fetched; its rows stay)."""
    curated = [_board(e, "curated") for e in config.companies_raw()]
    seen = {b["board"] for b in curated}
    out = list(curated)
    for entry in discovered_raw():
        if entry.get("ats") not in ADAPTERS:
            continue
        try:
            board = _board(entry, "cc")
        except config.ConfigError as err:
            log.warning("skipping discovered entry: %s", err)
            continue
        if board["board"] not in seen:
            seen.add(board["board"])
            out.append(board)
    for entry in employer_boards.learned_raw():     # GOAL36 §2: employers' own boards found from the job banks
        try:
            board = _board(entry, employer_boards.SOURCE)
        except config.ConfigError as err:
            log.warning("skipping learned employer board: %s", err)
            continue
        if board["board"] not in seen and entry.get("ats") in ADAPTERS:
            seen.add(board["board"])
            out.append(board)
    if jobbanks.enabled():                     # GOAL35 §4.2: the state job banks (Settings switch + a key)
        out.append(JOBBANK_BOARD)
    return _without_past_employers(out)


def _without_past_employers(found: list[dict]) -> list[dict]:
    try:
        profiles = config.profiles()
    except config.ConfigError as err:               # a broken profile must not stop the fetch
        log.warning("past employers not applied: %s", err)
        return found
    return [b for b in found if not past_employers.skip_board(b["company"], profiles)]


def _entry(row: dict) -> dict:
    name = row["board_name"] or row["slug"]
    entry = {"name": name, "ats": row["ats"]}
    fields = KEY_FIELDS.get(row["ats"], ())
    if fields:
        parts = row["slug"].split("/", len(fields))
        entry |= {"token": parts[0], **dict(zip(fields, parts[1:]))}
    else:
        entry["token"] = row["slug"]
    seg = segments.segment(name, row["website"], row["ats"], row["n_jobs"])
    return {**entry, "segment": seg, "source": row["source"] or "cc", "us60": row["us60"],
            **({"website": row["website"]} if row["website"] else {}),
            **({"name_src": row["name_src"]} if row["name_src"] not in (None, "ats", "page") else {})}


def _rank(row: dict) -> tuple:
    return (-(row["us60"] or 0), -(row["n_jobs"] or 0), row["newest_us"] or "")


def _identity(row: dict) -> tuple[str, str]:
    return seedlists.compact(row["board_name"] or row["slug"]), seedlists.domain(row["website"]) or ""


def _curated_key(entry: dict) -> str:
    return f"{entry['ats']}:{board_key(entry)}"


def _site_rank(row: dict, current: set[str]) -> tuple:
    """The most jobs, then a site whose name is pinned in board_names.csv, then the site already in
    companies.discovered.yaml (no churn on a tie), then the slug: the pick never depends on the DB's row order."""
    return (-(row["n_jobs"] or 0), -(row["us60"] or 0), row.get("name_src") != "list",
            f"{row['ats']}:{row['slug']}" not in current, row["slug"])


def _one_site_per_tenant(rows: list[dict], have_tenants: set[str], funnel: dict,
                         current: set[str] = frozenset()) -> list[dict]:
    """One site per tenant; it carries the tenant's pinned name when any of its sites has one."""
    tenants: dict[str, dict] = {}
    pinned: dict[str, str] = {}
    for row in sorted(rows, key=lambda r: _site_rank(r, current)):
        tenant = row["slug"].split("/")[0].lower()
        if row.get("name_src") == "list":
            pinned.setdefault(tenant, row["board_name"])
        if tenant in have_tenants:
            funnel["already_curated"] += 1
        elif tenant in tenants:
            funnel[f"{row['ats']}_extra_sites"] += 1
        else:
            tenants[tenant] = row
    return [{**row, "board_name": pinned[tenant], "name_src": "list"} if tenant in pinned else row
            for tenant, row in tenants.items()]


def select(rows: list[dict], curated: list[dict], current: set[str] = frozenset()) -> tuple[list[dict], dict]:
    """Pure: verified probe rows -> (kept rows, funnel counts). `current`: 'ats:key' of the boards the discovered
    file lists now, which keep their site on a tie."""
    have_boards = {_curated_key(e) for e in curated}

    have_names = {seedlists.compact(e["name"]) for e in curated}
    funnel: dict[str, int] = defaultdict(int)
    live = [r for r in rows if r["ats"] in FETCHABLE and r["hit"] and (r["us60"] or 0) > 0]
    funnel["us_live"] = len(live)
    fresh = [r for r in live if f"{r['ats']}:{r['slug']}" not in have_boards]
    funnel["already_curated"] = len(live) - len(fresh)
    sited = [row for ats in SITED_ATS for row in _one_site_per_tenant(
        [r for r in fresh if r["ats"] == ats], {e["token"].lower() for e in curated if e["ats"] == ats}, funnel,
        current)]
    by_name: dict[str, list[dict]] = defaultdict(list)
    for row in [r for r in fresh if r["ats"] not in SITED_ATS] + sited:
        by_name[_identity(row)[0]].append(row)
    kept = []
    for compact_name, group in by_name.items():
        if compact_name and compact_name in have_names:
            funnel["dupe_of_curated"] += len(group)
            continue
        # a board already listed wins a name tie: a newly found namesake (GOAL16 seeds: 'Glide', 'Leap') never
        # displaces a live board whose jobs are held; a company that moved leaves its old board dead anyway
        group = sorted(group, key=lambda r: (f"{r['ats']}:{r['slug']}" not in current, _rank(r)))
        best_site = _identity(group[0])[1]
        kept.append(group[0])
        for row in group[1:]:
            site = _identity(row)[1]
            if compact_name and (not site or not best_site or site == best_site):
                funnel["dupe_same_company"] += 1
            else:
                kept.append(row)
    funnel["admitted"] = len(kept)
    return sorted(kept, key=lambda r: (r["ats"], r["slug"])), dict(funnel)


def build(conn, atss: tuple[str, ...] | None = None) -> dict:
    """Rewrite companies.discovered.yaml from verified probes. Returns the funnel and counts per ATS / segment.
    GOAL16 staging: with `atss`, only those of the new systems are admitted (the others wait for their stage);
    boards of the systems read before GOAL16 are always rebuilt as before."""
    rows = [dict(r) for r in conn.execute("SELECT * FROM discover_probes WHERE verified_at IS NOT NULL")]
    if atss is not None:
        rows = [r for r in rows if r["ats"] not in GOAL16_ATS or r["ats"] in atss]
    kept, funnel = select(rows, config.companies_raw(), {_curated_key(e) for e in discovered_raw()})
    entries = [_entry(r) for r in kept]
    header = ("# GENERATED by `job-hunt-agent discover --build` from verified Common Crawl / seed probes (decision 005).\n"
              "# Do not edit: put manual entries in companies.yaml, which wins on every conflict.\n")
    body = yaml.safe_dump({"companies": entries}, sort_keys=False, allow_unicode=True, width=120)
    config.CONFIG.mkdir(parents=True, exist_ok=True)
    (config.CONFIG / DISCOVERED).write_text(header + body)
    by_ats: dict[str, int] = defaultdict(int)
    by_segment: dict[str, int] = defaultdict(int)
    for e in entries:
        by_ats[e["ats"]] += 1
        by_segment[e["segment"]] += 1
    result = {"at": db.iso(db.utcnow()), "funnel": funnel, "by_ats": dict(by_ats), "by_segment": dict(by_segment),
              "written": len(entries)}
    (config.DATA / "cc").mkdir(parents=True, exist_ok=True)
    (config.DATA / "cc" / "build.json").write_text(json.dumps(result, indent=1))
    return result
