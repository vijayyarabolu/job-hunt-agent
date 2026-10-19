"""Read path (decision 001 'one read path', decision 002): open title-matched jobs of a profile, fits joined,
scored with score.final(), best first. Used by `job-hunt-agent top`, the web UI, notifications and the digest."""
import re
import sys
import time
import zlib
from array import array
from datetime import datetime, timedelta

from . import applied, config, feedback, fits, fresh, past_employers, score, text
from .db import iso, unpack
from .fits import family_filter

MOMENTUM_WEEK = timedelta(days=7)
MOMENTUM_BASE = timedelta(days=28)
MOMENTUM_MIN_HISTORY = timedelta(days=35)
SQL_BATCH = 500
JD_KEY_CACHE = 20000         # (uid, crc32 of the stored JD) -> its word checksums; emptied when full (~25 MB at most)
_jd_key_cache: dict[tuple[str, int], array] = {}
SAME_REQ = 0.9               # share of JD words two copies of one req have in common
MIN_JD_WORDS = 40            # below this many distinct words two JDs must be word for word the same
_JOB_COLUMNS = ("j.uid, j.board, b.ats, j.company, j.title, j.location, j.is_us, j.workplace, j.url, j.apply_url, "
                "j.req_id, j.has_team, j.salary_min, j.salary_max, j.min_years, j.is_talent_pool, j.posted_at, "
                "j.first_posted_at, j.precision, j.bumped_at, j.seed, j.chain_origin, j.chain_n, j.repost_count, "
                "j.first_seen, j.closed_at, j.stale, j.employment_type, j.staffing, j.sponsorship, j.families, "
                "b.segment")


MOMENTUM_KEEP_S = 600                      # one aggregate over every job; kept 10 min per family set
_MOMENTUM: dict[tuple, tuple[float, dict]] = {}


def momentum(conn, families: tuple[str, ...], now: datetime,
             companies: tuple[str, ...] | None = None) -> dict[str, tuple[int, int]]:
    """company -> (matching postings first posted in the last 7 days, in the 28 days before). Seed rows and boards
    with < 35 days of history are excluded so a new board's backlog never looks like a hiring burst. `companies`
    limits it to those (a few cards need milliseconds, not the seconds of the aggregate over every job)."""
    data = tuple(conn.execute("SELECT (SELECT max(rowid) FROM jobs), (SELECT max(id) FROM runs)").fetchone())
    key = (str(config.DB_PATH), families, data, now.replace(minute=now.minute // 10 * 10, second=0, microsecond=0),
           companies)
    hit = _MOMENTUM.get(key)
    if hit and time.monotonic() - hit[0] < MOMENTUM_KEEP_S:
        return hit[1]
    found = _momentum(conn, families, now, companies)
    for old in [k for k, (at, _) in _MOMENTUM.items() if time.monotonic() - at >= MOMENTUM_KEEP_S]:
        _MOMENTUM.pop(old, None)
    _MOMENTUM[key] = (time.monotonic(), found)
    return found


def _momentum(conn, families: tuple[str, ...], now: datetime,
              companies: tuple[str, ...] | None) -> dict[str, tuple[int, int]]:
    where, params = family_filter(families)
    if companies is not None:
        where, params = f"{where} AND j.company IN ({','.join('?' * len(companies)) or 'NULL'})", [*params, *companies]
    week, base = iso(now - MOMENTUM_WEEK), iso(now - MOMENTUM_WEEK - MOMENTUM_BASE)
    rows = conn.execute(                      # GOAL21 W17: the covering index, never the 1.6 GB of job rows
        "SELECT j.company, sum(j.first_posted_at >= ?), sum(j.first_posted_at < ? AND j.first_posted_at >= ?) "
        f"FROM jobs j INDEXED BY jobs_fam JOIN boards b ON b.board = j.board "
        f"WHERE j.seed = 0 AND b.baselined_at <= ? AND {where} GROUP BY j.company",
        [week, week, base, iso(now - MOMENTUM_MIN_HISTORY), *params]).fetchall()
    return {r[0]: (r[1] or 0, r[2] or 0) for r in rows}


def dismissed(conn, profile_id: str) -> set[str]:
    """uids the person dismissed in the UI (latest triage state); pushes and the digest leave them out."""
    return {r[0] for r in conn.execute("SELECT uid FROM triage_now WHERE profile = ? AND state = 'dismissed'",
                                       (profile_id,))}


def pipeline(conn, profile_id: str) -> dict[str, str]:
    """uid -> status of the profile's applications that were started but not sent (saved, tailored)."""
    rows = conn.execute("SELECT job_uid, status FROM applications WHERE profile = ? AND job_uid IS NOT NULL "
                        "AND status IN (?, ?)", (profile_id, *score.PIPELINE_CHIPS))
    return {r["job_uid"]: r["status"] for r in rows}


def context(conn, profile: dict, now: datetime, companies: tuple[str, ...] | None = None) -> score.Ctx:
    inp = fits.profile_inputs(profile)
    return score.Ctx(now=now, profile_id=profile["id"], years=profile.get("years"),
                     known=fits.known_skills(profile["id"]), has_baseline=inp.has_baseline,
                     applied=applied.load(conn, profile["id"]), momentum=momentum(conn, inp.families, now, companies),
                     threshold=int(config.settings()["notify_threshold"]), pipeline=pipeline(conn, profile["id"]),
                     max_years=profile.get("max_required_years"),
                     sponsorship_label=bool(profile.get("needs_sponsorship")),
                     prefs=feedback.load(conn, profile["id"]), families=tuple(profile["families"]),
                     dealbreakers=inp.dealbreakers, n_resumes=len(inp.resumes),
                     past=past_employers.profile_keys(profile))


def rank(conn, profile: dict, now: datetime, fresh_only: bool = False,
         states: tuple[str, ...] = ("ok",), uid: str | None = None,
         uids: list[str] | None = None) -> list[tuple[dict, score.Score]]:
    """(job row, Score) for the profile's open matched jobs on fetched boards, highest worth first; jobs whose fit
    is still pending come after every scored job. `uid` limits it to that one job (detail view);
    `uids` to exactly those jobs, closed ones included (the Resumes tab)."""
    shown, picked = None, list(uids) if uids is not None else [uid] if uid is not None else None
    if picked is not None:              # momentum of these jobs' companies only
        marks = ",".join("?" * len(picked))
        shown = tuple(sorted({r[0] for r in conn.execute(f"SELECT company FROM jobs WHERE uid IN ({marks})", picked)}))
    ctx = context(conn, profile, now, shown)
    ranked = []
    for r in _rows(conn, profile, uid, uids):  # both people rank mostly the same jobs: one copy of each string (GOAL7 §4 memory)
        row = {k: sys.intern(v) if type(v) is str else v for k, v in zip(r.keys(), r)}
        fit = {k: row.pop(k) for k in ("jd_state", "terms", "dealbreaker")}
        row["fit_terms"], row["jd_state"] = fit["terms"], fit["jd_state"]      # match.breakdown
        if fresh_only and not fresh.is_fresh(row, now):
            continue
        result = score.final(row, fit if fit["jd_state"] else None, ctx)
        if result.state in states:
            ranked.append((row, result))
    return sorted(ranked, key=lambda pair: (not pair[1].scored, -pair[1].worth, pair[0]["first_seen"]))


def _rows(conn, profile: dict, uid: str | None, uids: list[str] | None) -> list:
    """The jobs to rank with their fit. GOAL21 W17: the open matched jobs are found in the jobs_fam index first (their
    rowids), then read by rowid: one pass over the whole table read every JD and took ~10 s cold."""
    head = (f"SELECT {_JOB_COLUMNS}, f.jd_state, f.terms, f.dealbreaker FROM jobs j "
            "JOIN boards b ON b.board = j.board LEFT JOIN fits f ON f.profile = ? AND f.uid = j.uid ")
    if uids is not None:
        return conn.execute(head + f"WHERE j.uid IN ({','.join('?' * len(uids))})", [profile["id"], *uids]).fetchall()
    where, params = family_filter(tuple(profile["families"]))
    if uid is not None:
        return conn.execute(head + f"WHERE j.closed_at IS NULL AND b.active = 1 AND b.review = 0 AND {where} AND j.uid = ?",
                            [profile["id"], *params, uid]).fetchall()
    ids = sorted(r[0] for r in conn.execute(      # rowid order: the same tie order as the old single scan
        f"SELECT j.rowid FROM jobs j INDEXED BY jobs_fam WHERE j.closed_at IS NULL AND {where}", params))
    rows = []
    for i in range(0, len(ids), SQL_BATCH):
        part = ids[i:i + SQL_BATCH]
        rows += conn.execute(head + f"WHERE j.rowid IN ({','.join('?' * len(part))}) AND b.active = 1 AND b.review = 0",
                             [profile["id"], *part]).fetchall()
    return rows


def _jd_words(conn, uids: list[str]) -> dict[str, frozenset]:
    """uid -> the checksums of its JD's words (boilerplate stripped); jobs with no JD get an empty set."""
    words = {}
    for i in range(0, len(uids), SQL_BATCH):
        part = uids[i:i + SQL_BATCH]
        rows = conn.execute(f"SELECT uid, description FROM jobs WHERE uid IN ({','.join('?' * len(part))})", part)
        for r in rows:
            words[r["uid"]] = frozenset(_jd_word_set(r["uid"], r["description"]))
    return words


def _jd_word_set(uid: str, blob) -> array:
    """The JD's distinct lower-case words as sorted crc32s, kept per (uid, stored text): stripping boilerplate was
    ~40 ms of every feed answer; a re-fetched JD has another checksum, so it is read again."""
    raw = blob if isinstance(blob, (bytes, bytearray)) else (blob or "").encode()
    memo = (uid, zlib.crc32(raw))
    if memo not in _jd_key_cache:
        if len(_jd_key_cache) >= JD_KEY_CACHE:
            _jd_key_cache.clear()
        found = re.findall(r"[a-z0-9+#]+", text.strip_boilerplate(unpack(blob)).lower())
        _jd_key_cache[memo] = array("I", sorted({zlib.crc32(w.encode()) for w in found}))
    return _jd_key_cache[memo]


def same_req(a: set, b: set, loc_a: str, loc_b: str) -> bool:
    """two postings with the same company and title are one req posted twice when their JDs share
    nearly every word (a location or pay line differs); with no JD on one side, only a different place says so."""
    if a and b:
        short = min(len(a), len(b)) < MIN_JD_WORDS                 # too few words for a share to mean anything
        return a == b if short else len(a & b) / len(a | b) >= SAME_REQ
    return (loc_a or "") != (loc_b or "")


def collapse(conn, ranked: list[tuple[dict, score.Score]]) -> list[tuple[dict, score.Score]]:
    """One entry per req posted in several locations or on several boards (same company and title, and the same JD
    give or take a location line): the best-ranked copy, with the other copies as
    row['also'] = ((uid, location), ...). Pushes, the digest and the job lists use this, so one req never costs three
    pushes or three tailored resumes (release review: multi-location duplicates)."""
    def base(row: dict) -> tuple[str, str]:
        return applied.company_key(row["company"]), applied.normalize_title(row["title"])

    bases = [base(row) for row, _ in ranked]              # once per row (it was three times, ~0.1 s a feed)
    counts: dict[tuple[str, str], int] = {}
    for b in bases:
        counts[b] = counts.get(b, 0) + 1
    words = _jd_words(conn, [row["uid"] for (row, _), b in zip(ranked, bases) if counts[b] > 1])
    # groups are formed in uid order, not the list's order: same_req is not transitive (a no-JD copy pairs with any
    # other place), so pairing with whichever copy came first made the count change with the sort
    heads: dict[tuple, list[int]] = {}
    group = list(range(len(ranked)))
    for i in sorted((i for i, b in enumerate(bases) if counts[b] > 1), key=lambda i: ranked[i][0]["uid"]):
        row, mine = ranked[i][0], words.get(ranked[i][0]["uid"], frozenset())
        head = next((n for n in heads.get(bases[i], ()) if same_req(words.get(ranked[n][0]["uid"], frozenset()), mine,
                                                                    ranked[n][0]["location"], row["location"])), None)
        if head is None:
            heads.setdefault(bases[i], []).append(i)
        else:
            group[i] = head
    slot: dict[int, int] = {}
    also: list[list[tuple[str, str]]] = []
    kept: list[tuple[dict, score.Score]] = []
    for i, (row, s) in enumerate(ranked):                 # the best-ranked copy of each group leads
        if group[i] in slot:
            also[slot[group[i]]].append((row["uid"], row["location"] or ""))
            continue
        slot[group[i]] = len(kept)
        kept.append((row, s))
        also.append([])
    return [({**row, "also": tuple(extra)}, s) for (row, s), extra in zip(kept, also)]
