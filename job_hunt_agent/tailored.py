"""resumegen GOAL3 §4: the match ring follows the tailored resume.

When a job's newest resumegen resume is ready, job-hunt-agent's own match (match.breakdown: the same parts, the same weights)
is computed again with the tailored resume's text as that job's only resume input and stored per (person, job). The
ring, the row, the floor and "Best match" then use that number for that job only, with the live base number kept
beside it ("77% → 88% with your tailored resume").

sync() runs where job-hunt-agent already reads resumegen's list (rgjobs.statuses, polled while the page is open): a job
whose ready resume is new (another resumegen id, or a new made / updated time: regenerated, re-imported, accepted) is
fetched (.docx) and scored once; a job whose newest resume is no longer ready loses its row and falls back to the base
score. Other jobs' rows are never touched. resumegen offline: nothing is fetched and the stored numbers (each from a
real ready resume) stay.
"""
import hashlib
import json
import logging
from dataclasses import replace
from datetime import datetime

from . import keywords, match, ranking, resumes, rgclient
from .db import iso, tx

log = logging.getLogger(__name__)
ALL_STATES = ("ok", "filtered", "non_us", "too_senior", "pending")
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TAILORED_ID, TAILORED_LABEL = "tailored", "Tailored"
MAX_PER_SYNC = 4                 # files fetched per status poll (~0.1-0.3 s each): the first sync after an upgrade
                                 # catches up over a few polls instead of holding one poll for seconds


def load(conn, pid: str) -> dict:
    """uid -> {'pct', 'base_pct', 'rg_id'} for the person's jobs whose ready tailored resume was scored."""
    rows = conn.execute("SELECT uid, rg_id, pct, base_pct FROM tailored_scores WHERE profile = ? AND pct IS NOT NULL",
                        (pid,))
    return {r["uid"]: {"pct": r["pct"], "base_pct": r["base_pct"], "rg_id": r["rg_id"]} for r in rows}


def view(entry: dict | None, base_pct: int | None) -> dict:
    """Card fields: 'match' becomes the tailored %, 'match_base' keeps the live base %; {} without a tailored score."""
    if not entry:
        return {}
    return {"match": entry["pct"], "match_base": base_pct, "tailored": {"pct": entry["pct"], "base": base_pct,
                                                                       "rg_id": entry["rg_id"]}}


def tailored_resume(data: bytes, families: tuple) -> resumes.Resume:
    """The tailored .docx as a resumes.Resume, read exactly like a base resume file (whole .docx text, lexicon)."""
    digest = hashlib.md5(data).hexdigest()
    text = resumes._docx_text(digest, data)
    return resumes.Resume(TAILORED_ID, TAILORED_LABEL, None, tuple(families), resumes._terms(digest, text), digest,
                          resumes.normalized(text))


def tailored_pct(row: dict, who: match.Who, industry: str | None, tailored: resumes.Resume) -> int:
    """match.breakdown with the tailored resume as the job's only resume: the JD's terms re-packed against it."""
    terms = [t for t, _ in keywords.unpack(row.get("fit_terms"))]
    packed = json.dumps(keywords.pack(terms, (tailored.terms,), (tailored.text,)))
    return match.breakdown({**row, "fit_terms": packed}, replace(who, resumes=(tailored,)), industry)["pct"]


def newest_by_uid(jobs: list) -> dict:
    """uid -> its newest resumegen job that was not cancelled (the one the chip shows)."""
    out = {}
    for job in sorted(jobs, key=lambda j: j["id"]):
        if job.get("source_job_id") and job.get("status") != "cancelled":
            out[job["source_job_id"]] = job
    return out


def stamp(job: dict) -> str:
    times = job.get("times") or {}
    return f"{job['id']}:{times.get('made') or job.get('finished_at') or ''}:{times.get('updated') or ''}"


def _families_of(job: dict, who: match.Who) -> tuple:
    base_id = ((job.get("result") or {}).get("base") or {}).get("id") or job.get("base_id")
    found = next((r for r in who.resumes if r.id == base_id), None)
    return found.families if found else ()


def _store(conn, pid: str, uid: str, job: dict, pct: int | None, base_pct: int | None, now: datetime) -> None:
    with tx(conn):
        conn.execute("INSERT OR REPLACE INTO tailored_scores(profile, uid, rg_id, stamp, pct, base_pct, computed_at) "
                     "VALUES (?,?,?,?,?,?,?)", (pid, uid, int(job["id"]), stamp(job), pct, base_pct, iso(now)))


def _score_one(conn, profile: dict, uid: str, job: dict, who: match.Who, industries, now: datetime) -> bool:
    """Score one job's ready resume; an unreadable file is stored as 'no tailored score' (so it is not fetched on
    every poll) and the job keeps its base score. Offline propagates."""
    ranked = ranking.rank(conn, profile, now, states=ALL_STATES, uid=uid)
    if not ranked:                                            # closed, or no longer in the person's jobs
        return False
    row, _ = ranked[0]
    industry = industries([row]).get(uid)
    base_pct = match.breakdown(row, who, industry)["pct"]
    _store(conn, profile["id"], uid, job, _file_pct(row, uid, job, who, industry), base_pct, now)
    return True


def _file_pct(row: dict, uid: str, job: dict, who: match.Who, industry) -> int | None:
    """The match with the job's tailored .docx; None (the base score stays) when there is no readable .docx."""
    try:
        data, ctype = rgclient.request("GET", f"/api/jobs/{int(job['id'])}/file/docx",
                                       timeout=rgclient.FILE_TIMEOUT_S, raw=True)
    except KeyError as err:                   # the run's file is gone (404), asked once like a bad file
        log.warning("tailored score: resumegen job %s has no .docx (%s)", job["id"], err)
        return None
    if ctype.split(";")[0].strip().lower() != DOCX_TYPE:
        log.warning("tailored score: resumegen job %s sent %s, not a .docx", job["id"], ctype)
        return None
    try:
        return tailored_pct(row, who, industry, tailored_resume(data, _families_of(job, who)))
    except Exception as err:                  # an unreadable .docx keeps the base score, for this job only
        log.warning("tailored score for %s (resumegen job %s) failed: %s", uid, job["id"], err)
        return None


def sync(conn, profile: dict, jobs: list, who: match.Who, industries, now: datetime) -> int:
    """Bring the stored tailored scores in line with resumegen's list; returns how many jobs changed. Only jobs whose
    own resume changed are touched. `industries(rows)` -> {uid: industry}. rgclient.Offline propagates."""
    pid = profile["id"]
    stored = {r["uid"]: r["stamp"] for r in conn.execute("SELECT uid, stamp FROM tailored_scores WHERE profile = ?",
                                                         (pid,))}
    newest = newest_by_uid(jobs)
    gone = [uid for uid in stored if newest.get(uid, {}).get("status") != "ready"]
    if gone:
        with tx(conn):
            conn.executemany("DELETE FROM tailored_scores WHERE profile = ? AND uid = ?", [(pid, u) for u in gone])
    todo = [(uid, job) for uid, job in newest.items() if job.get("status") == "ready" and stored.get(uid) != stamp(job)]
    return len(gone) + sum(_score_one(conn, profile, uid, job, who, industries, now) for uid, job in todo[:MAX_PER_SYNC])
