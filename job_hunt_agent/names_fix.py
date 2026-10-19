"""`job-hunt-agent names check|apply`: every board's display name as the company writes it.

check: company_names.propose() for every active board, with the board's stored JDs and, for a suspicious name on
       Greenhouse / Lever / Ashby, one live look at the ATS's own name (Greenhouse's board name, the Lever / Ashby
       page title; robots gate and pacing through net.py). Pinned names in config/lists/board_names.csv are final.
apply: the changed names go into board_names.csv (so they survive rediscovery), the boards table follows, stored jobs
       take the new name (and their repost key, so repost chains hold), the old name is kept in
       config/lists/company_renames.csv (applied-before matching still finds rows typed with it), and tracker rows
       the app made whose Company no person ever edited take it too (the sheet sync writes them).
jobs.company always equals boards.company (one name per board), so the board is the one place to fix.
"""
import csv
import logging
import re
import zlib
from collections import Counter
from pathlib import Path

from . import applied, company_names, db, history, registry, seedlists, store, tracker
from .ats import paylocity
from .discover_verify import _TITLE, clean_name
from .net import HttpError, get_json, get_text

log = logging.getLogger(__name__)
JD_SAMPLE = 25
RENAMES = applied.RENAMES_FILE
LIVE_REASONS = ("lost leading digits", "cut off", "stray punctuation", "junk", "cut from the slug", "all caps")
LIVE_ATS = ("greenhouse", "lever", "ashby", "paylocity")
_PAYLOCITY_TITLE = re.compile(r"<title>\s*(.*?)\s+-\s+Job Opportunities\s*</title>", re.S | re.I)
MATCHED_ID_BOARDS = ("SELECT DISTINCT b.board, b.ats, b.key, b.company FROM boards b JOIN jobs j ON j.board = b.board "
                     "WHERE b.active = 1 AND b.ats IN ('adp', 'paylocity') AND j.closed_at IS NULL "
                     "AND j.families <> ','")


def ats_name(ats: str, key: str) -> str | None:
    """The ATS's own name for the board, cleaned (None when it can't be read)."""
    try:
        if ats == "greenhouse":
            return clean_name((get_json(f"https://boards-api.greenhouse.io/v1/boards/{key}") or {}).get("name"))
        if ats == "paylocity":                    # '<title>Tanaq Technical Services LLC - Job Opportunities'
            found = _PAYLOCITY_TITLE.search(get_text(paylocity.list_url(key)) or "")
            return clean_name(found.group(1)) if found else None
        if ats in ("lever", "ashby"):
            host = "jobs.lever.co" if ats == "lever" else "jobs.ashbyhq.com"
            found = _TITLE.search(get_text(f"https://{host}/{key}") or "")
            return clean_name(found.group(1)) if found else None
    except (HttpError, ValueError) as err:
        log.info("names: %s:%s: %s", ats, key, err)
    return None


def _texts(conn, board: str) -> list[str]:
    rows = conn.execute("SELECT description FROM jobs WHERE board = ? AND description IS NOT NULL "
                        "ORDER BY last_seen DESC LIMIT ?", (board, JD_SAMPLE))
    return [zlib.decompress(r[0]).decode() for r in rows]


def _one(conn, b, live: bool) -> tuple[str, str]:
    evidence = company_names.jd_evidence(b["company"], _texts(conn, b["board"]))
    reason = company_names.suspicious(b["company"], b["key"], b["ats"])
    looks = reason in LIVE_REASONS or (reason == company_names.ID_NAME and not evidence.claims)
    source = ats_name(b["ats"], b["key"]) if live and looks and b["ats"] in LIVE_ATS else None
    new, why = company_names.propose(b["company"], b["key"], evidence, source, b["ats"])
    if source and new == company_names.tidy(b["company"]) and reason in LIVE_REASONS[:5]:
        return source, f"{reason}: ATS says {source!r}"
    return new, why


def check(conn, live: bool = True) -> list[dict]:
    """One row per active board whose name changes or needs a person: {board, ats, key, old, new, why}."""
    pinned = seedlists._board_names()
    out = []
    for b in conn.execute("SELECT board, ats, key, company FROM boards WHERE active = 1 ORDER BY board").fetchall():
        if (b["ats"], b["key"]) in pinned:
            new, why = pinned[(b["ats"], b["key"])], "pinned in board_names.csv"
        else:
            new, why = _one(conn, b, live)
        if new != b["company"] or why.startswith("needs a name"):
            out.append({"board": b["board"], "ats": b["ats"], "key": b["key"], "old": b["company"], "new": new,
                        "why": why})
    return out


def learn(conn, live: bool = True) -> dict:
    """after a fetch run, an ADP / Paylocity board still named by its tenant id that now has matched open
    jobs takes the name its JDs give ("About the CDC Foundation") or, on Paylocity, its own page title. The name is
    kept in seedlists.learned_path() (pins in config stay final) and its stored jobs follow."""
    pinned = seedlists._board_names()
    changes = []
    for b in conn.execute(MATCHED_ID_BOARDS).fetchall():
        if (b["ats"], b["key"]) in pinned or \
                company_names.suspicious(b["company"], b["key"], b["ats"]) != company_names.ID_NAME:
            continue
        new, why = _one(conn, b, live)
        if new != b["company"] and not why.startswith("needs a name"):
            changes.append({"ats": b["ats"], "key": b["key"], "name": new, "board": b["board"]})
    if not changes:
        return {"boards": 0, "jobs": 0}
    rows = seedlists._read(seedlists.learned_path()) + [{k: c[k] for k in ("ats", "key", "name")} for c in changes]
    _write_rows(seedlists.learned_path(), ["ats", "key", "name"], sorted(rows, key=lambda r: (r["ats"], r["key"])))
    seedlists._board_names.cache_clear()
    db.sync_boards(conn, registry.boards())
    with db.tx(conn):
        jobs = sum(_rename_jobs(conn, c["board"], c["name"]) for c in changes)
    return {"boards": len(changes), "jobs": jobs}


def _write_rows(path: Path, header: list[str], rows: list[dict]) -> None:
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)


def pin(changes: list[dict]) -> int:
    """Changed names into board_names.csv (existing pins win; rows sorted). Returns how many were added."""
    rows = {(r["ats"], r["key"]): r for r in seedlists._rows("board_names.csv")}
    added = 0
    for c in changes:
        if (c["ats"], c["key"]) not in rows and not c["why"].startswith("needs a name"):
            rows[(c["ats"], c["key"])] = {"ats": c["ats"], "key": c["key"], "name": c["new"]}
            added += 1
    _write_rows(seedlists.lists_dir() / "board_names.csv", ["ats", "key", "name"],
                sorted(rows.values(), key=lambda r: (r["ats"], r["key"])))
    seedlists._board_names.cache_clear()
    return added


def _remember_renames(pairs: set[tuple[str, str]]) -> None:
    rows = {(r["old"], r["new"]) for r in seedlists._rows(RENAMES)} | pairs
    _write_rows(seedlists.lists_dir() / RENAMES, ["old", "new"], [{"old": o, "new": n} for o, n in sorted(rows)])
    applied._renamed.cache_clear()


def _rename_jobs(conn, board: str, new: str) -> int:
    rows = conn.execute("SELECT uid, title, location FROM jobs WHERE board = ? AND company <> ?", (board, new)).fetchall()
    conn.executemany("UPDATE jobs SET company = ?, repost_key = ? WHERE uid = ?",
                     [(new, store.repost_key(new, r["title"], r["location"] or "", r["uid"]), r["uid"]) for r in rows])
    return len(rows)


def _person_edited(conn, app_id: int) -> bool:
    """A person changed the Company cell (in the app or the Sheet); only job-hunt-agent's own writes don't count."""
    return conn.execute("SELECT 1 FROM edit_history WHERE app_id = ? AND field = 'company' AND who <> ? LIMIT 1",
                        (app_id, history.CLI)).fetchone() is not None


def _rename_apps(conn, renamed: dict[str, str], former: dict[str, str]) -> list[dict]:
    """Tracker rows the app made from a renamed board's job whose Company still holds exactly the board's former
    name (what the app wrote) and that no person ever edited: the new name. Anything else was typed: left alone."""
    done = []
    for app in conn.execute("SELECT a.id, a.profile, a.company, j.board FROM applications a JOIN jobs j "
                            "ON j.uid = a.job_uid WHERE a.source = 'feed'").fetchall():
        new = renamed.get(app["board"])
        if not new or (app["company"] or "").strip() != (former.get(app["board"]) or "").strip() \
                or _person_edited(conn, app["id"]):
            continue
        with history.action(history.CLI):
            tracker.edit_field(conn, app["id"], "company", new)
        conn.commit()
        done.append({"id": app["id"], "profile": app["profile"], "old": app["company"], "new": new})
    return done


def drifted(conn) -> list[dict]:
    """Open jobs whose company is not their board's name: a fetch takes the name from the board, so these were written
    by a fetch that ran while the board was being renamed (378 Booz Allen jobs kept "…_United States")."""
    return [dict(r) for r in conn.execute(
        "SELECT j.board, b.company AS board_name, j.company AS job_name, count(*) AS jobs FROM jobs j "
        "JOIN boards b ON b.board = j.board WHERE j.closed_at IS NULL AND j.company <> b.company "
        "GROUP BY j.board, b.company, j.company ORDER BY j.board")]


def apply(conn, changes: list[dict]) -> dict:
    """Pin, sync the boards, rename stored jobs and app-made tracker rows. Returns counts and the rows changed."""
    added = pin(changes)
    db.sync_boards(conn, registry.boards())
    names = {r["board"]: r["company"] for r in conn.execute("SELECT board, company FROM boards WHERE active = 1")}
    moved = {c["board"]: names[c["board"]] for c in changes if names.get(c["board"]) not in (None, c["old"])}
    with db.tx(conn):
        jobs = sum(_rename_jobs(conn, board, new) for board, new in moved.items())
        stale = sum(_rename_jobs(conn, d["board"], d["board_name"]) for d in drifted(conn))
    _remember_renames({(c["old"], moved[c["board"]]) for c in changes if c["board"] in moved})
    apps = _rename_apps(conn, moved, {c["board"]: c["old"] for c in changes})
    return {"pinned": added, "boards": len(moved), "jobs": jobs, "drifted_jobs": stale, "tracker_rows": apps,
            "by_reason": dict(Counter(c["why"].split(":")[0].split(";")[0] for c in changes))}
