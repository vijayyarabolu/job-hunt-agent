"""Is a posting still open? Asked of the ATS's own per-job endpoint (decision 001, release review C1).

Used by /tailor before writing a resume and by fetch before believing that a board really emptied or died: a list
endpoint whose JSON key was renamed looks empty, but its jobs still answer on their own pages.
"""
import random

from .ats import workday
from .net import HttpError, get_json

PROBE_N = 3


def live_status(job) -> str:
    """'open' | 'closed' | 'unknown' from the ATS's own job endpoint (a closed posting answers 404)."""
    ats_name, _, key = job["board"].partition(":")
    ext = job["ext_id"]
    try:
        if ats_name == "greenhouse":
            get_json(f"https://boards-api.greenhouse.io/v1/boards/{key}/jobs/{ext}")
        elif ats_name == "lever":
            get_json(f"https://api.lever.co/v0/postings/{key}/{ext}")
        elif ats_name == "smartrecruiters":            # robots.txt: Disallow: / (decision 005): never asked
            return "unknown"
        elif ats_name == "ashby":                       # no per-job endpoint: the board list is the only source
            listed = (get_json(f"https://api.ashbyhq.com/posting-api/job-board/{key}") or {}).get("jobs")
            if not isinstance(listed, list):
                return "unknown"                        # a changed list format proves nothing about the job
            return "open" if any(j.get("id") == ext for j in listed if isinstance(j, dict)) else "closed"
        elif ats_name == "workday" and "/job/" in (job["url"] or ""):
            _, api = workday.urls(key)
            get_json(f"{api}/job/{job['url'].split('/job/', 1)[1]}")
        else:
            return "unknown"
    except HttpError as err:
        return "closed" if err.status in (404, 410) else "unknown"
    return "open"


def probe(conn, board_id: str, exclude: set[str]) -> str:
    """Ask up to PROBE_N open jobs of a board that the list no longer shows: 'open' if any still answers (the list is
    broken), 'closed' if all are gone, 'unknown' otherwise. Costs at most PROBE_N requests."""
    rows = conn.execute("SELECT uid, board, ext_id, url FROM jobs WHERE board = ? AND closed_at IS NULL",
                        (board_id,)).fetchall()
    candidates = [r for r in rows if r["uid"] not in exclude]
    if not candidates:
        return "closed"
    answers = [live_status(r) for r in random.sample(candidates, min(PROBE_N, len(candidates)))]
    if "open" in answers:
        return "open"
    return "closed" if all(a == "closed" for a in answers) else "unknown"
