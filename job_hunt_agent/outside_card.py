"""GOAL33 §3d: the small card of a job with no feed card (pasted in the Resume app, added by link), shared by Tailored
resumes and Applied.

- Location is the job's own (the tracker row's Location, else the place resumegen read from the JD), never the row's
  Address: that is the street address Autofill typed into the form for the user (GOAL23 §7), i.e. the user's home.
- Match comes from the JD the user pasted, scored like a feed job (experience, skills, industry, title, GenAI work); a row
  without JD text says so ("no_jd") instead of a bare "?". A score is kept in outside_fits (one row per tracker row,
  scored again when the title, the JD or the profile changes): Tailored resumes scores its few cards at once, Applied
  (900+ rows, 15 s cold) shows the kept ones and a background thread fills the rest ("scoring").
"""
import hashlib
import json
import logging
import queue
import threading

from . import api, config, db, fits, jdsnap, match, signals, titles
from .db import pack

log = logging.getLogger(__name__)
_TODO: queue.Queue = queue.Queue()
_QUEUED: set = set()
_QUEUED_LOCK = threading.Lock()
_FILLER: list = []
PAUSE_S = 0.01                           # between background scores: page requests come first


class Person:
    """One person's scoring inputs, built once per request (not once per card)."""

    def __init__(self, pid: str):
        self.pid = pid
        self.profile = {**config.profile(pid), "id": pid}
        self.who = api._who(self.profile)
        self.inputs = fits.profile_inputs(self.profile)
        self.sig = hashlib.sha1(json.dumps(self.profile, sort_keys=True, default=str).encode()).hexdigest()[:12]


def _jd_text(conn, app_id: int | None) -> str:
    snap = jdsnap.get(conn, app_id) if app_id else None
    return ((snap or {}).get("text") or "").strip()


def _sig(person: Person, title: str, text: str) -> str:
    return hashlib.sha1(f"{person.sig}\n{title}\n{text}".encode()).hexdigest()[:16]


def _score(person: Person, title: str, company: str, text: str) -> dict:
    fams = titles.match_job(title, text, list(person.who.families))
    fit = fits.compute(title, pack(text), person.inputs, ",".join(fams))
    row = {"title": title, "families": "," + ",".join(fams) + ",", "min_years": signals.min_years(text),
           "fit_terms": fit["terms"], "jd_state": fit["jd_state"], "genai": fit["genai"]}
    m = match.breakdown(row, person.who, match.industry_of(company, text))
    return {"match": m["pct"], "parts": [p.as_dict() for p in m["parts"]]}


def _kept(conn, pid: str, app_id: int, sig: str) -> dict | None:
    row = conn.execute("SELECT pct, parts FROM outside_fits WHERE profile = ? AND app_id = ? AND sig = ?",
                       (pid, app_id, sig)).fetchone()
    return {"match": row["pct"], "parts": json.loads(row["parts"])} if row else None


def _keep(conn, pid: str, app_id: int, sig: str, view: dict) -> None:
    with db.tx(conn):
        conn.execute("INSERT INTO outside_fits(profile, app_id, sig, pct, parts, at) VALUES (?,?,?,?,?,?) "
                     "ON CONFLICT(profile, app_id) DO UPDATE SET sig = excluded.sig, pct = excluded.pct, "
                     "parts = excluded.parts, at = excluded.at",
                     (pid, app_id, sig, view["match"], json.dumps(view["parts"]), db.iso(db.utcnow())))


def match_view(conn, person: Person | None, app_id: int | None, title: str, company: str, now: bool = True) -> dict:
    """{'match', 'parts'} from the row's pasted JD; {'match': None, 'no_jd': True} without one; with now=False a
    row not scored yet is queued for the background and says {'match': None, 'scoring': True}."""
    text = _jd_text(conn, app_id)
    if not text:
        return {"match": None, "no_jd": True}
    if person is None:                                    # a profile that cannot be scored (no resume yet)
        return {"match": None}
    sig = _sig(person, title, text)
    hit = _kept(conn, person.pid, app_id, sig)
    if hit is not None:
        return hit
    if not now:
        _queue(person.pid, app_id, title, company)
        return {"match": None, "scoring": True}
    view = _score(person, title, company, text)
    _keep(conn, person.pid, app_id, sig, view)
    return view


def person(pid: str) -> Person | None:
    try:
        return Person(pid)
    except (config.ConfigError, ValueError, KeyError) as err:
        log.warning("outside matches for %s: %s", pid, err)
        return None


def location(app: dict | None, job: dict | None = None) -> str | None:
    """The job's place: the row's Location, else resumegen's (read from the JD); never the row's Address."""
    return (app or {}).get("location") or (job or {}).get("location") or None


def card(conn, who: Person | None, key: str, app: dict | None, job: dict | None = None, now: bool = True) -> dict:
    app, job = app or {}, job or {}
    url = app.get("job_url") or job.get("jd_url")
    company, title = app.get("company") or job.get("company") or "", app.get("role_title") or job.get("role") or ""
    return {"uid": key, "outside": app.get("id"), "company": company, "title": title,
            "location": location(app, job), "url": url, "apply_url": url, "worth": None, "posted_ts": None,
            **match_view(conn, who, app.get("id"), title, company, now),
            "app": {"id": app["id"], "status": app["status"]} if app.get("id") else None}


# ---------- the background filler (Applied's rows) ----------

def _queue(pid: str, app_id: int, title: str, company: str) -> None:
    item = (str(config.DB_PATH), pid, app_id)
    with _QUEUED_LOCK:
        if item in _QUEUED:
            return
        _QUEUED.add(item)
        if not _FILLER or not _FILLER[0].is_alive():
            _FILLER[:] = [threading.Thread(target=_fill, daemon=True, name="outside-fits")]
            _FILLER[0].start()
    _TODO.put((*item, title, company))


def _fill() -> None:
    conns, people = {}, {}
    try:
        while True:
            try:
                path, pid, app_id, title, company = _TODO.get(timeout=30)
            except queue.Empty:
                return                                    # idle: the thread ends; the next ask starts another
            try:
                conn = conns.get(path) or conns.setdefault(path, db.connect(path))
                who = people[(path, pid)] if (path, pid) in people else people.setdefault((path, pid), person(pid))
                if who is not None:
                    match_view(conn, who, app_id, title, company, now=True)
            except Exception:                             # one bad row never stops the others
                log.exception("outside match for row %s failed", app_id)
            finally:
                with _QUEUED_LOCK:
                    _QUEUED.discard((path, pid, app_id))
            threading.Event().wait(PAUSE_S)
    finally:
        for conn in conns.values():
            conn.close()
