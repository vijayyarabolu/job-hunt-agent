"""GOAL36 §2: every company we see. For the employers in the state job bank results (CareerOneStop; Adzuna and JSearch
have no adapter in job-hunt-agent, so there is nothing else "on" to read), find the employer's own ATS board, verify it with
one fetch, and add it to the fetch list. A board is never guessed: it is added only when its list request answers
with open jobs (a 404 or an empty board adds nothing).

Per employer, cheapest first: the posting's own link when it is an ATS careers link (detect + one live count), else the
slug probes `job-hunt-agent discover` uses (Greenhouse / Lever / Ashby, cached 30 days in discover_probes, misses too). Only a
board whose own name matches the employer is added (discover's identity check); a doubtful match is reported, not added.

Runs once a day from fetch._daily after the backup, rate limited: at most MAX_PER_DAY employers and BUDGET_S seconds,
and an employer tried in the last RETRY_DAYS is skipped. Found boards go to data/employer_boards.yaml (git-ignored,
read by registry.boards() after the curated and discovered files); the run's per-state report (employers seen, new
companies, tried, boards found) goes to data/employer_boards_last.json for `job-hunt-agent employer-boards`."""
import json
import logging
import time
from collections import defaultdict
from datetime import timedelta

import yaml

from . import config, db, discover
from .ats import careeronestop, detect
from .net import HttpError
from .text import norm

log = logging.getLogger(__name__)

SOURCES = (careeronestop.ATS,)
MAX_PER_DAY = 60
BUDGET_S = 180
SEEN_WITHIN = timedelta(days=7)
RETRY_DAYS = 30
LINK_ATS = ("greenhouse", "lever", "ashby", "workday")      # what discover._live_count can verify in one request
SOURCE = "jobbank"


def boards_path():
    return config.DATA / "employer_boards.yaml"


def report_path():
    return config.DATA / "employer_boards_last.json"


def learned_raw() -> list[dict]:
    try:
        return (yaml.safe_load(boards_path().read_text()) or {}).get("companies", []) or []
    except FileNotFoundError:
        return []
    except (OSError, yaml.YAMLError) as err:            # a broken file is skipped, never a failed fetch
        log.warning("data/employer_boards.yaml unreadable (%s): its boards are skipped", err)
        return []


def last_report() -> dict:
    try:
        return json.loads(report_path().read_text())
    except (OSError, ValueError):
        return {}


def employers(conn, now) -> list[dict]:
    """{name, states, links} per employer named on a job bank posting seen in the last SEEN_WITHIN."""
    since = db.iso(now - SEEN_WITHIN)
    where = " OR ".join("board LIKE ?" for _ in SOURCES)
    out: dict[str, dict] = {}
    for r in conn.execute(f"SELECT company, location, url, apply_url FROM jobs WHERE ({where}) AND last_seen >= ? "
                          "AND company IS NOT NULL AND company != ''", (*(f"{s}:%" for s in SOURCES), since)):
        key = norm(r["company"])
        if not key:
            continue
        row = out.setdefault(key, {"name": r["company"].strip(), "states": set(), "links": []})
        row["states"].add(careeronestop.state_of(r["location"]) or "?")
        row["links"] += [u for u in (r["apply_url"], r["url"]) if u and u not in row["links"]]
    return sorted(out.values(), key=lambda e: e["name"].lower())


def known_names() -> set:
    """Companies that already have a board we fetch (curated, discovered or learned here)."""
    from . import registry                              # registry reads this module's file
    return {norm(b["company"]) for b in registry.boards()} | {norm(e.get("name") or "") for e in learned_raw()}


def find_board(conn, employer: dict, now) -> dict | None:
    """{name, ats, token, ..., n_jobs, how} for the employer's own board, verified live; None when there is none."""
    for link in employer["links"]:
        found = detect(link)
        if not found or found["ats"] not in LINK_ATS:
            continue
        try:
            n_jobs = discover._live_count(found)
        except HttpError as err:
            log.info("employer board %s: %s link check failed (%s), tried again later", employer["name"],
                     found["ats"], err)
            continue
        if n_jobs:
            return {"name": employer["name"], **found, "n_jobs": n_jobs, "how": "the posting's own link"}
    hit = discover.best_hit(discover._discover_one(conn, employer["name"], None, now))
    if hit and not hit["review"]:
        return {"name": employer["name"], "ats": hit["ats"], "token": hit["token"], "n_jobs": hit["n_jobs"],
                "how": "name probe"}
    return None


def _save(added: list[dict]) -> None:
    existing = learned_raw()
    have = {(e["ats"], e["token"], e.get("site")) for e in existing}
    fresh = []
    for entry in added:
        clean = {k: v for k, v in entry.items() if k not in ("n_jobs", "how")}
        config.validate_company(clean)                  # never write an entry the registry would refuse
        if (clean["ats"], clean["token"], clean.get("site")) not in have:
            fresh.append(clean)
            have.add((clean["ats"], clean["token"], clean.get("site")))
    if fresh:
        path = boards_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        body = yaml.safe_dump({"companies": existing + fresh}, sort_keys=False, allow_unicode=True, width=120)
        path.write_text("# GOAL36 §2: employers' own boards found from the state job banks (job_hunt_agent.employer_boards)\n"
                        + body)


def run(conn, now, limit: int = MAX_PER_DAY, budget_s: float = BUDGET_S, finder=None) -> dict:
    """One rate-limited discovery pass; the report is saved and returned."""
    finder = finder or find_board
    cutoff = db.iso(now - timedelta(days=RETRY_DAYS))[:10]
    tried = {k: v for k, v in (last_report().get("tried") or {}).items() if v >= cutoff}
    known = known_names()
    states = defaultdict(lambda: {"employers": 0, "new_companies": 0, "tried": 0, "boards": 0})
    todo = []
    for employer in employers(conn, now):
        new = norm(employer["name"]) not in known
        for st in employer["states"]:
            states[st]["employers"] += 1
            states[st]["new_companies"] += new
        if new and norm(employer["name"]) not in tried:
            todo.append(employer)
    added, started, today = [], time.monotonic(), db.iso(now)[:10]
    done = 0
    for employer in todo[:limit]:
        if time.monotonic() - started > budget_s:
            break
        done += 1
        tried[norm(employer["name"])] = today
        try:
            board = finder(conn, employer, now)
        except HttpError as err:                         # one host failing never stops the rest
            log.info("employer board %s: %s", employer["name"], err)
            continue
        for st in employer["states"]:
            states[st]["tried"] += 1
            states[st]["boards"] += bool(board)
        if board:
            added.append({**board, "source": SOURCE, "state": sorted(employer["states"])[0], "found": today})
    _save(added)
    report = {"at": today, "states": {st: v for st, v in sorted(states.items())},
              "added": [{k: a.get(k) for k in ("name", "ats", "token", "n_jobs", "how", "state")} for a in added],
              "left": len(todo) - done, "tried": tried}
    path = report_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=1) + "\n")
    return report


def text(report: dict) -> str:
    if not report.get("states"):
        return "employer boards: no state job bank postings yet (Settings > Job sources, and a CareerOneStop key)"
    lines = [f"employer boards from the state job banks, last run {report.get('at') or 'never'}",
             f"{'state':<6}{'employers':>10}{'new companies':>15}{'tried':>7}{'boards found':>14}"]
    for st, v in report["states"].items():
        lines.append(f"{st:<6}{v['employers']:>10}{v['new_companies']:>15}{v['tried']:>7}{v['boards']:>14}")
    lines += [f"added: {a['name']} ({a['ats']}:{a['token']}, {a['n_jobs']} open jobs, by {a['how']})"
              for a in report.get("added") or []]
    if report.get("left"):
        lines.append(f"{report['left']} more employers wait for the next days (at most {MAX_PER_DAY} a day)")
    return "\n".join(lines)
