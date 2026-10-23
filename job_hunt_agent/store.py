"""Per-board transactional write: upsert via fresh.observe, events, reposts, misses/closing (decision 001 §4)."""
import json
import re
import sqlite3
from datetime import datetime, timedelta

from . import fresh, titles
from .db import CHAIN_SELECT, iso, pack, unpack
from .db import tx as db_tx
from .text import norm, sha1, strip_boilerplate

MISSES_TO_CLOSE = 2
MIN_MISSING_BEFORE_CLOSE = timedelta(hours=4)
WORKDAY_STALE_AFTER = timedelta(days=7)
_TITLE_NOISE = re.compile(r"\(.*?\)|\[.*?\]|\b[A-Z]{1,4}-?\d{3,}\b")
JOB_FIELDS = ("company", "title", "families", "location", "country", "is_us", "workplace", "url", "apply_url",
              "req_id", "has_team", "salary_min", "salary_max", "salary_ccy", "min_years", "is_talent_pool",
              "employment_type", "staffing", "sponsorship", "description", "content_hash", "repost_key", "raw_fp")
TERM_SEARCH_ATS = {"amazon", "eightfold", "phenom", "icims",   # one query per search term on every fetch
                   "oracle_recruiting", "taleo", "successfactors", "ibm"}
SEARCH_ATS = {"workday"} | TERM_SEARCH_ATS    # relevance/newest-limited results: absence is never a miss, only staleness
FAST_CHUNK = 500
STATE_FIELDS = ("posted_at", "first_posted_at", "precision", "bumped_at", "seed", "repost_count", "first_seen",
                "last_seen", "miss_count", "missing_since", "closed_at", "stale", "chain_origin", "chain_n")
_CHAIN_AT_INSERT = CHAIN_SELECT.format(rk=":rk", uid=":uid", first_seen=":now", req=":req", order="1")


# Fields that only a detail call (or a full list payload) provides; a later detail-less sighting must not wipe them.
DETAIL_FIELDS = ("location", "country", "is_us", "workplace", "url", "apply_url", "req_id", "has_team",
                 "salary_min", "salary_max", "salary_ccy", "min_years", "is_talent_pool", "employment_type", "staffing",
                 "sponsorship")


def repost_key(company: str, title: str, location: str, uid: str = "") -> str:
    """hash(company + title + location). Without a location the key is per-uid: an empty location would make every
    same-title req at a company look like a repost of the others."""
    place = norm(location)
    return sha1(norm(company), norm(_TITLE_NOISE.sub(" ", title)), place or f"uid:{uid}")


def _with_stored_detail(item: dict, old: dict | None) -> dict:
    """A re-sighting without a JD (detail skipped because the JD is already stored) keeps the stored detail-derived
    values wherever they are set; list-only values fill only the gaps."""
    if item.get("description") or not old or not old.get("description"):
        return item
    kept = {k: old[k] for k in DETAIL_FIELDS if old.get(k) not in (None, "")}
    return {**item, **kept}


def _list_only_resighting(item: dict, old: dict | None) -> bool:
    """A list-only sighting (Greenhouse lists without content) of a job whose JD we never stored, with the same title
    and location: keep its content hash instead of re-hashing without the description."""
    return bool(old) and not item.get("description") and not old.get("description") \
        and norm(item["title"]) == norm(old["title"]) and norm(item.get("location")) == norm(old.get("location"))


def content_hash(item: dict, description: str) -> str:
    return sha1(norm(item["title"]), norm(item["location"]), norm(strip_boilerplate(description))[:20000],
                str(item.get("salary_min")), str(item.get("salary_max")), item.get("req_id") or "")


def _event(conn, run_id: int, uid: str, now_s: str, kind: str, detail: dict) -> None:
    conn.execute("INSERT INTO job_events(run_id, uid, at, kind, detail) VALUES (?,?,?,?,?)",
                 (run_id, uid, now_s, kind, json.dumps(detail) if detail else None))


def _job_values(item: dict, old: dict | None) -> dict:
    item = _with_stored_detail(item, old)
    description = item["description"] or (unpack(old["description"]) if old else "")
    families = titles.match_job(item["title"], description)
    return {
        **{k: item.get(k) for k in JOB_FIELDS if k in item},
        "families": "," + ",".join(families) + "," if families else ",",
        "is_us": None if item.get("is_us") is None else int(item["is_us"]),
        "has_team": int(bool(item.get("has_team"))), "is_talent_pool": int(bool(item.get("is_talent_pool"))),
        "description": pack(description) if families else None,     # keep JD text only for matched families
        "content_hash": content_hash(item, description),
        "repost_key": repost_key(item["company"], item["title"], item["location"], item["uid"]),
    }


def _upsert(conn, board_id: str, item: dict, values: dict, state: dict) -> None:
    cols = ["uid", "board", "ext_id", *values, *state]
    params = [item["uid"], board_id, item["ext_id"], *values.values(), *state.values()]
    updates = ", ".join(f"{c}=excluded.{c}" for c in [*values, *state] if c != "first_posted_at")
    conn.execute(
        f"INSERT INTO jobs({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) "
        f"ON CONFLICT(uid) DO UPDATE SET {updates}, first_posted_at = CASE "
        "WHEN jobs.first_posted_at IS NULL OR excluded.first_posted_at < jobs.first_posted_at "
        "THEN excluded.first_posted_at ELSE jobs.first_posted_at END",
        params,
    )


def _write_item(conn, run_id: int, board_id: str, item: dict, now: datetime, seeding: bool) -> bool:
    """Upsert one job; returns True if it was new."""
    now_s = iso(now)
    row = conn.execute("SELECT * FROM jobs WHERE uid=?", (item["uid"],)).fetchone()
    old = dict(row) if row else None
    values = _job_values(item, old)
    if _list_only_resighting(item, old):            # no JD on either side: nothing new to hash (decision 005 B2)
        values["content_hash"] = old["content_hash"]
    patch, events = fresh.observe(old, item, now, seeding)
    state = {k: patch.get(k, old.get(k) if old else None) for k in STATE_FIELDS}
    if old is None:
        prior = conn.execute(
            "SELECT count(*) FROM jobs WHERE repost_key=? AND uid<>? AND (closed_at IS NOT NULL OR first_seen<?)",
            (values["repost_key"], item["uid"], now_s)).fetchone()[0]
        origin, chained = conn.execute(_CHAIN_AT_INSERT, {"rk": values["repost_key"], "uid": item["uid"], "now": now_s,
                                                          "req": item.get("req_id") or ""}).fetchone()
        state["chain_origin"], state["chain_n"] = origin, chained
        if prior:
            state["repost_count"] = prior
            events.append(("repost", {"prior": prior, "chained": chained}))
    _upsert(conn, board_id, item, values, state)
    if values["families"] == ",":                   # GOAL33 §3b: outside the user's families no JD history and no events,
        return old is None                          # like no JD text (1.3 M rows nothing read)
    if old is None or old["content_hash"] != values["content_hash"]:
        conn.execute("INSERT OR IGNORE INTO jd_versions(uid, content_hash, seen_at) VALUES (?,?,?)",
                     (item["uid"], values["content_hash"], now_s))
        if old is not None:
            events.append(("changed", {}))
    for kind, detail in events:
        _event(conn, run_id, item["uid"], now_s, kind, detail)
    return old is None


def _count_misses(conn, run_id: int, board_id: str, is_workday: bool, now: datetime) -> int:
    """Only called for ok runs. Returns the number of jobs closed. is_workday covers every SEARCH_ATS board."""
    now_s = iso(now)
    if is_workday:   # relevance-truncated results: absence is never a miss, only staleness
        stale_before = iso(now - WORKDAY_STALE_AFTER)
        rows = conn.execute("SELECT uid FROM jobs WHERE board=? AND closed_at IS NULL AND stale=0 AND last_seen<?",
                            (board_id, stale_before)).fetchall()
        for r in rows:
            conn.execute("UPDATE jobs SET stale=1 WHERE uid=?", (r["uid"],))
            _event(conn, run_id, r["uid"], now_s, "stale", {})
        return 0
    conn.execute("UPDATE jobs SET miss_count=miss_count+1, missing_since=coalesce(missing_since, ?) "
                 "WHERE board=? AND closed_at IS NULL AND last_seen<?", (now_s, board_id, now_s))
    rows = conn.execute("SELECT uid, missing_since FROM jobs WHERE board=? AND closed_at IS NULL AND miss_count>=? "
                        "AND missing_since<=?", (board_id, MISSES_TO_CLOSE,
                                                 iso(now - MIN_MISSING_BEFORE_CLOSE))).fetchall()
    for r in rows:
        conn.execute("UPDATE jobs SET closed_at=missing_since WHERE uid=?", (r["uid"],))
        _event(conn, run_id, r["uid"], now_s, "closed", {"missing_since": r["missing_since"]})
    return len(rows)


def _close_board(conn, run_id: int, board_id: str, gone_at: str, now: datetime) -> int:
    rows = conn.execute("SELECT uid FROM jobs WHERE board=? AND closed_at IS NULL", (board_id,)).fetchall()
    for r in rows:
        conn.execute("UPDATE jobs SET closed_at=coalesce(missing_since, ?) WHERE uid=?", (gone_at, r["uid"]))
        _event(conn, run_id, r["uid"], iso(now), "closed", {"board_gone": True})
    return len(rows)


def _is_seed(board: dict, item: dict, fams: set[str], new_scopes: set[str], baselined: set[str]) -> bool:
    """Backlog rule C2: first run of a board, a newly added family, or (Workday) found only by search terms that are
    not baselined yet. Items without provenance fall back to 'any new term seeds the board'."""
    if not board.get("baselined_at") or fams & new_scopes:
        return True
    found_by = set(item.get("scopes") or [])
    if found_by:
        return not found_by & baselined
    return any(s.startswith("term:") for s in new_scopes)


def _touch_unchanged(conn, fast: list[dict], now: datetime) -> int:
    """Raw-identical postings on open rows (decision 005 B2 raw_fp): exactly what observe() writes for an unchanged
    sighting (last_seen, miss_count=0, missing_since=NULL, stale=0), in batched UPDATEs. Returns how many matched."""
    now_s, matched = iso(now), 0
    uids = [i["uid"] for i in fast]
    for start in range(0, len(uids), FAST_CHUNK):
        chunk = uids[start:start + FAST_CHUNK]
        marks = ",".join("?" * len(chunk))
        conn.execute(f"UPDATE jobs SET last_seen=?, miss_count=0, missing_since=NULL, stale=0 WHERE uid IN ({marks})",
                     [now_s, *chunk])
        matched += conn.execute(f"SELECT count(*) FROM jobs WHERE uid IN ({marks}) AND families <> ','",
                                chunk).fetchone()[0]
    return matched


def _age_missing(conn, run_id: int, board_id: str, now: datetime) -> int:
    """304 aging: jobs already missing from the last OK list take one more miss and may close, exactly as an OK 200
    without them would. Seen jobs are not rewritten (their last_seen is refreshed by the next 200; boards.last_fetch
    records the check), so a 304 costs no per-row writes (run-2 skeptic 5: SSD wear, CPU)."""
    now_s = iso(now)
    conn.execute("UPDATE jobs SET miss_count=miss_count+1 WHERE board=? AND closed_at IS NULL AND miss_count>0",
                 (board_id,))
    rows = conn.execute("SELECT uid, missing_since FROM jobs WHERE board=? AND closed_at IS NULL AND miss_count>=? "
                        "AND missing_since<=?", (board_id, MISSES_TO_CLOSE,
                                                 iso(now - MIN_MISSING_BEFORE_CLOSE))).fetchall()
    for r in rows:
        conn.execute("UPDATE jobs SET closed_at=missing_since WHERE uid=?", (r["uid"],))
        _event(conn, run_id, r["uid"], now_s, "closed", {"missing_since": r["missing_since"], "via": "304"})
    return len(rows)


def write_not_modified(conn: sqlite3.Connection, board: dict, run_id: int, now: datetime, latency_ms: int) -> dict:
    """A 304 (decision 005 B2): the list equals the last OK one, so this is an OK run that saw exactly the jobs that
    run saw. Only conditional GETs reach here (Greenhouse/Lever/Ashby, never the search boards). No classify, no
    probes."""
    board_id, now_s = board["board"], iso(now)
    with db_tx(conn):
        n_closed = _age_missing(conn, run_id, board_id, now)
        seen = conn.execute("SELECT count(*), coalesce(sum(families <> ','), 0) FROM jobs WHERE board=? AND "
                            "closed_at IS NULL AND miss_count=0", (board_id,)).fetchone()
        conn.execute("UPDATE boards SET fail_streak=0, last_ok_at=? WHERE board=?", (now_s, board_id))
        conn.execute(
            "INSERT OR REPLACE INTO fetch_runs(run_id, board, status, http_status, n_raw, n_jobs, n_match, "
            "n_parse_err, n_new, n_closed, latency_ms, error) VALUES (?,?,'ok',304,?,?,?,0,0,?,?,NULL)",
            (run_id, board_id, seen[0], seen[0], seen[1], n_closed, latency_ms))
    return {"status": "ok", "n_jobs": seen[0], "n_new": 0, "n_closed": n_closed, "n_match": seen[1]}


def write_board(conn: sqlite3.Connection, board: dict, result, status: str, run_id: int, now: datetime,
                scopes: list[str], latency_ms: int, count_misses: bool = True, close_all: bool = False,
                run_key: str | None = None) -> dict:
    """One BEGIN IMMEDIATE transaction for one board's fetch result. count_misses=False for truncated boards;
    close_all=True for a board that has been gone (404/410) for a day and whose jobs no longer answer."""
    board_id = board["board"]
    baselined = set(json.loads(board.get("baselined_scopes") or "[]"))
    new_scopes = set(scopes) - baselined
    n_new = n_closed = n_match = 0
    items = result.items if status != "http_error" else []
    with db_tx(conn):
        n_match += _touch_unchanged(conn, [i for i in items if i.get("fast")], now)
        for item in (i for i in items if not i.get("fast")):
            fams = {f"fam:{f}" for f in titles.match(item["title"])}
            seeding = _is_seed(board, item, fams, new_scopes, baselined)
            n_new += _write_item(conn, run_id, board_id, item, now, seeding)
            n_match += bool(fams)
        if status == "ok":
            if count_misses:
                n_closed = _count_misses(conn, run_id, board_id, board["ats"] in SEARCH_ATS, now)
            conn.execute("UPDATE boards SET fail_streak=0, last_ok_at=?, last_ok_seen=?, baselined_at=coalesce("
                         "baselined_at, ?), baselined_scopes=? WHERE board=?",
                         # a complete, accepted list is the new size, even 0 (else an emptied board stays
                         # "suspect" forever, verifier M9); a truncated one sets it unless it came back empty
                         (iso(now), len(result.items) if count_misses else
                          (len(result.items) or board.get("last_ok_seen") or 0), iso(now),
                          json.dumps(sorted(baselined | set(scopes))), board_id))
        elif status in ("http_error", "drift"):
            conn.execute("UPDATE boards SET fail_streak=fail_streak+1 WHERE board=?", (board_id,))
            if close_all:
                n_closed = _close_board(conn, run_id, board_id, board.get("last_ok_at") or iso(now), now)
        conn.execute(
            "INSERT OR REPLACE INTO fetch_runs(run_id, board, status, http_status, n_raw, n_jobs, n_match, "
            "n_parse_err, n_new, n_closed, latency_ms, error) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, run_key or board_id, status, result.http_status, result.n_raw, len(result.items), n_match,
             result.n_parse_err, n_new, n_closed, latency_ms, result.error))
    return {"status": status, "n_jobs": len(result.items), "n_new": n_new, "n_closed": n_closed, "n_match": n_match}
