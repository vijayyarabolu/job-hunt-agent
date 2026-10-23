"""Fetch-time fit cache (decision 002, "two passes"). Per profile and open title-matched job: the JD's weighted
lexicon terms with mention counts and a per-resume hit mask (decision 007), the JD state and the dealbreaker
verdict. score.final() then only does
arithmetic. Rows are rebuilt newest first whenever the JD, lexicon, baseline or dealbreakers change; a row that
fails to parse becomes jd_state='unreadable' so nothing stays pending.
"""
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from . import config, genai, keywords, lexicon, resumes, signals
from .db import iso, tx, unpack
from .resume import ats
from .text import sha1, strip_boilerplate

log = logging.getLogger(__name__)
THIN_JD_CHARS = 300
MIN_TERMS = 4
REFRESH_CHUNK = 500             # rows per transaction: a full rebuild never holds the SQLite write lock for long
PENDING_MARK_SQL = ("SELECT (SELECT max(id) FROM runs), (SELECT finished_at FROM runs ORDER BY id DESC LIMIT 1), "
                    "(SELECT max(rowid) FROM jobs), (SELECT count(*) || '/' || max(computed_at) FROM fits WHERE profile = ?), "
                    "(SELECT count(*) || '/' || total(active) || '/' || total(review) FROM boards)")
PENDING_KEEP_S = 300            # at most this old, whatever moved
_PENDING: dict[tuple, tuple[tuple, int]] = {}   # (db file, profile) -> (mark, count)


@dataclass(frozen=True)
class ProfileInputs:
    id: str
    families: tuple[str, ...]
    has_baseline: bool
    resumes: tuple[resumes.Resume, ...]     # profile.yaml order; bit i of a term's hit mask = resumes[i]
    baseline_hash: str
    lexicon_hash: str
    dealbreakers: tuple[str, ...]
    dealbreaker_hash: str
    jd_gate: dict | None = None             # GOAL28 §4.1: profile.yaml jd_gate (a GenAI-content gate), or None

    @property
    def resume_terms(self) -> tuple[frozenset[str], ...]:
        return tuple(r.terms for r in self.resumes)

    @property
    def resume_texts(self) -> tuple[str, ...]:
        return tuple(r.text for r in self.resumes)


_KNOWN: dict[str, tuple] = {}            # file -> ((mtime, size), names): read again only when the file changes


def known_skills(pid: str) -> frozenset[str]:
    """Canonical lexicon names of profiles/<pid>/known_skills.txt (skills the person confirmed they have). GOAL33 §4:
    kept per file version (3.6 ms of lexicon work per call, called for every card that builds a match)."""
    path = config.PROFILES / pid / "known_skills.txt"
    try:
        st = path.stat()
        mark = (st.st_mtime_ns, st.st_size)
    except OSError:
        return frozenset()
    kept = _KNOWN.get(str(path))
    if kept is None or kept[0] != mark:
        lines = path.read_text().splitlines()
        kept = _KNOWN[str(path)] = (mark, frozenset(lexicon.find("\n".join(ln for ln in lines if ln.strip() and not ln.startswith("#")))))
    return kept[1]


def profile_inputs(profile: dict) -> ProfileInputs:
    mine = resumes.of_profile(profile)
    patterns = tuple(profile.get("dealbreakers") or ())
    resume_key = json.dumps([[r.id, r.digest, sorted(r.terms)] for r in mine])   # a wording edit rebuilds (010)
    gate = profile.get("jd_gate") or None
    # the gate is part of the dealbreaker inputs: a person who has one gets every row re-judged (the GOAL28 backfill);
    # a person without one keeps the same hash, so their rows are never rebuilt for it
    verdicts = sha1(json.dumps(patterns)) if not gate else sha1(json.dumps([patterns, gate], sort_keys=True))
    return ProfileInputs(profile["id"], tuple(profile["families"]), bool(mine), mine, sha1(resume_key),
                         sha1(lexicon.lexicon_hash(), ats.EXTRACTOR_VERSION), patterns, verdicts,
                         dict(gate) if gate else None)


def family_filter(families: tuple[str, ...], alias: str = "j") -> tuple[str, list[str]]:
    """SQL clause matching jobs whose ',a,b,' families column contains any of `families`."""
    clause = " OR ".join(f"instr({alias}.families, ?) > 0" for _ in families) or "0"
    return f"({clause})", [f",{f}," for f in families]


def _stale_where(inp: ProfileInputs) -> tuple[str, list]:
    where, params = family_filter(inp.families)
    return ("FROM jobs j INDEXED BY jobs_open_fit JOIN boards b ON b.board = j.board "
            "LEFT JOIN fits f ON f.profile = ? AND f.uid = j.uid "
            f"WHERE j.closed_at IS NULL AND j.families <> ',' AND b.active = 1 AND b.review = 0 AND {where} AND (f.uid IS NULL "
            "OR f.content_hash <> j.content_hash OR f.lexicon_hash <> ? OR f.baseline_hash <> ? "
            "OR f.dealbreaker_hash <> ?)", [inp.id, *params, inp.lexicon_hash, inp.baseline_hash, inp.dealbreaker_hash])


def _stale(conn, inp: ProfileInputs) -> list:
    sql, params = _stale_where(inp)
    return conn.execute(f"SELECT j.uid, j.title, j.families, j.content_hash, j.description {sql} "
                        "ORDER BY coalesce(j.first_posted_at, j.first_seen) DESC", params).fetchall()


def _count_stale(conn, inp: ProfileInputs) -> int:
    sql, params = _stale_where(inp)
    return conn.execute(f"SELECT count(*) {sql}", params).fetchone()[0]


def compute(title: str, description: bytes | None, inp: ProfileInputs, families: str | None = None) -> dict:
    """One fits row (without keys/hashes). Never raises: a parse failure is jd_state='unreadable'. `genai`: the JD's
    GenAI terms, comma-joined (GOAL28 §4.3; '' = none); the person's JD gate adds its verdict (GOAL28 §4.1)."""
    try:
        text = unpack(description)
        terms = ats.extract(text) if text else []
        state = "none" if not text else "thin" if len(text) < THIN_JD_CHARS or len(terms) < MIN_TERMS else "ok"
        found = genai.terms(text)
        verdict = signals.dealbreaker(f"{title}\n{strip_boilerplate(text)}", list(inp.dealbreakers)) \
            or genai.gate_verdict(title, families, text, inp.jd_gate, inp.families, found)
        packed = keywords.pack(terms, inp.resume_terms, inp.resume_texts)
        return {"jd_state": state, "jd_len": len(text), "terms": json.dumps(packed), "dealbreaker": verdict,
                "genai": ",".join(found)}
    except Exception as err:          # one bad JD must not leave the profile without scores
        log.warning("fit failed for %r: %s", title, err)
        return {"jd_state": "unreadable", "jd_len": 0, "terms": "[]", "dealbreaker": None, "genai": None}


def refresh(conn, profile: dict, now: datetime, stop: Callable[[], bool] = lambda: False) -> int:
    """Rebuild stale fits rows for one profile, REFRESH_CHUNK rows per transaction; returns how many were written.
    `stop` is checked after each chunk (a fetch run's CPU ceiling): rows not reached stay stale for the next run."""
    inp = profile_inputs(profile)
    rows = _stale(conn, inp)
    written = 0
    for i in range(0, len(rows), REFRESH_CHUNK):
        if written and stop():
            break
        with tx(conn):
            for r in rows[i:i + REFRESH_CHUNK]:
                fit = compute(r["title"], r["description"], inp, r["families"])
                conn.execute(
                    "INSERT OR REPLACE INTO fits(profile, uid, content_hash, lexicon_hash, baseline_hash, "
                    "dealbreaker_hash, jd_state, jd_len, terms, dealbreaker, computed_at, genai) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (inp.id, r["uid"], r["content_hash"], inp.lexicon_hash, inp.baseline_hash, inp.dealbreaker_hash,
                     fit["jd_state"], fit["jd_len"], fit["terms"], fit["dealbreaker"], iso(now), fit["genai"]))
        written += len(rows[i:i + REFRESH_CHUNK])
    return written


def pending(conn, profile: dict) -> int:
    """Rows still waiting for a fit (doctor / UI header). The count walks every open job (~0.4 s) and the header asks
    once a minute per open tab, so it is kept until a run, a job, a fit or a board changes (or the person's inputs)."""
    inp = profile_inputs(profile)
    mark = (*conn.execute(PENDING_MARK_SQL, (inp.id,)).fetchone(), inp.families, inp.lexicon_hash,
            inp.baseline_hash, inp.dealbreaker_hash, int(time.time() // PENDING_KEEP_S))   # writes outside a run too
    key = (str(config.DB_PATH), inp.id)
    kept = _PENDING.get(key)
    if kept and kept[0] == mark:
        return kept[1]
    n = _count_stale(conn, inp)
    _PENDING[key] = (mark, n)
    return n
