"""resumes from resumegen, as job-hunt-agent shows them.

- statuses(): one chip per job (queued / generating / ready / needs you) for the person, keyed by feed job uid and,
  for outside jobs (pasted JDs, §3), by tracker row id. The browser polls it only while the page is open.
- generate(): Generate resume / Generate for selected (1-20 jobs, one POST). Nothing runs without that click.
- detail() / file() / action(): the side panel (PDF or page image, diff, ATS before/after) and its buttons
  (Approve, Open as Google Doc, Re-import, Regenerate), each checked to be the person's own resume.
- resumegen GOAL3: a "needs you" resume gets Looks good / Fix with note, an archived one Restore, an outdated one
  Regenerate with current resume; chips and the panel carry when it was made and the Outdated badge. statuses()
  also keeps the tailored match scores in step (tailored.sync) and returns them.
- GOAL13: the Resumes tab (rqueue) reads the same list; Cancel and Move to top are two more actions, Generate says
  each job's place in the queue, and a Yes to "Did you apply?" approves the job's ready resume first.
- GOAL22: a job whose city can't be worked out (or that names several) is not sent: generate() returns it under
  `needs_place` with its cities, and the browser asks; the pick ("City, ST" or Remote) is kept for that job.
"""
import threading
import time
from urllib.parse import quote

from . import config, db, outside, resume_place, resumes, rg_html, rgclient, rqueue, tailored
from .text import strip_boilerplate

CHIPS = {"queued": "queued", "waiting": "queued", "generating": "generating", "checking": "generating",
         "ready": "ready", "needs_you": "needs you", "failed": "needs you", "needs_place": "needs you"}
ACTIVE = ("queued", "waiting", "generating", "checking")
BUSY = ACTIVE + ("needs_place",)     # waiting in resumegen for the person's city; Generate doesn't send it twice
MAX_BATCH = 20
MIN_JD_CHARS = 80                    # resumegen refuses shorter JDs
FILES = {"pdf": "pdf", "png": "png", "base_png": "base.png"}
FILE_TYPES = ("application/pdf", "image/png")
ACTIONS = ("approve", "gdoc", "reimport", "regenerate", "accept", "fix", "restore", "cancel", "top", "undo_edit")
MAX_NOTE = 500
REMOTE = "remote"                    # the picker's "Remote -> <own city>"
HOME = ""           # profile.yaml `location` when it has none: no city on the resume


def chip(job: dict) -> dict | None:
    name = CHIPS.get(job.get("status"))
    if name is None:                                           # cancelled
        return None
    why = job.get("error") if name in ("needs you", "queued") else None
    return {"id": job["id"], "chip": name, "status": job["status"], "approved": bool(job.get("drive_file_id")),
            "why": why, "attempts": job.get("attempts"), "elapsed_s": job.get("elapsed_s"),
            "made": (job.get("times") or {}).get("made"), "badge": job.get("badge"),
            "archived": bool(job.get("archived_at"))}


def _latest(jobs: list[dict], key: str) -> dict:
    """key value -> chip of its newest job that was not cancelled."""
    out: dict = {}
    for job in sorted(jobs, key=lambda j: j["id"]):
        made = chip(job)
        if made is not None and job.get(key):
            out[job[key]] = made
    return out


# One answer past the 4 s timeout (the resume service paged out on a computer short of memory) showed "Resume app is
# offline" and Queue 0 / Ready 0 / Done today 0. The list waits longer; askers share the outcome of a call that
# started after they asked (never an older one: it may predate their own change); and for the page's lists
# (keep=True) one failed call answers the last good list (if not older than LAST_GOOD_MAX_S) and only two failed
# tries in a row are offline. Autofill, approving and archiving ask strictly: an old list would pick a resume it
# cannot get.
LISTING_TIMEOUT_S = 10
OFFLINE_AFTER = 2
LAST_GOOD_MAX_S = 300
_LISTED: dict[tuple, dict] = {}   # (resume service address, pid) -> {"jobs", "good_at", "tried", "error", "fails"}
_NOTHING = {"jobs": None, "good_at": 0.0, "tried": -1.0, "error": None, "fails": 0}
_LISTING_LOCKS: dict[tuple, threading.Lock] = {}
_LOCKS_LOCK = threading.Lock()


def _list_once(pid: str) -> list[dict]:
    jobs = rgclient.request("GET", f"/api/jobs?profile={quote(pid)}&lean=1",       # all, small
                            timeout=LISTING_TIMEOUT_S).get("jobs") or []
    return [j for j in jobs if j.get("profile") == pid]


def _fetch(pid: str, kept: dict) -> list[dict]:
    try:
        return _list_once(pid)
    except rgclient.Offline:
        if kept["jobs"] is not None:
            raise
        return _list_once(pid)                   # nothing to show meanwhile: a second try decides


def _attempt(key: tuple, pid: str, kept: dict) -> dict:
    started = time.monotonic()
    try:
        jobs = _fetch(pid, kept)
    except rgclient.Offline as err:
        return {**kept, "tried": started, "error": str(err), "fails": kept["fails"] + 1}
    return {"jobs": jobs, "good_at": started, "tried": started, "error": None, "fails": 0}


def listing(pid: str, keep: bool = False) -> list[dict]:
    asked, key = time.monotonic(), (rgclient.address(), pid)
    with _LOCKS_LOCK:
        lock = _LISTING_LOCKS.setdefault(key, threading.Lock())
    with lock:
        state = _LISTED.get(key) or _NOTHING
        if state["tried"] < asked:               # no call started since this ask: make one (else share its outcome)
            state = _LISTED[key] = _attempt(key, pid, state)
    if state["error"] is None:
        return state["jobs"]
    fresh = state["jobs"] is not None and time.monotonic() - state["good_at"] <= LAST_GOOD_MAX_S
    if keep and fresh and state["fails"] < OFFLINE_AFTER:
        return state["jobs"]
    raise rgclient.Offline(state["error"])


def scores(conn, pid: str) -> dict:
    """uid -> {'pct', 'base'}: the stored tailored match of each job with a ready tailored resume."""
    return {uid: {"pct": t["pct"], "base": t["base_pct"]} for uid, t in tailored.load(conn, pid).items()}


def statuses(conn, pid: str, rescore=None, now=None) -> dict:
    """{'online', 'jobs': uid -> chip, 'apps': outside row id -> chip, 'active', 'outside_new', 'scores'} plus the
    Resumes tab's counts and today's progress; offline -> message and the stored tailored scores (each from
    a real ready resume). `rescore(jobs)` brings them in step."""
    now = now or db.utcnow()
    try:
        jobs = listing(pid, keep=True)
        new = outside.sync(conn, pid, jobs)
        if rescore is not None:
            rescore(jobs)
        extra = rqueue.summary(conn, pid, jobs, now)
    except rgclient.Offline as err:
        return {"online": False, "message": str(err), "jobs": {}, "apps": {}, "active": 0, "scores": scores(conn, pid),
                **rqueue.progress(conn, pid, now)}
    gone = set(extra.pop("cancelled_closed"))
    jobs = [{**j, "status": "cancelled"} if j["id"] in gone else j for j in jobs]
    links = outside.links(conn, pid)
    by_app = {links[j["id"]]: chip(j) for j in sorted(jobs, key=lambda j: j["id"]) if j["id"] in links and chip(j)}
    return {"online": True, "jobs": {**_latest(jobs, "source_job_id"), **_record_chips(conn, pid, by_app)},
            "apps": by_app, "outside_new": new,
            "active": sum(1 for j in jobs if j.get("status") in ACTIVE), "scores": scores(conn, pid), **extra}


def _record_chips(conn, pid: str, by_app: dict) -> dict:
    """GOAL36 §5.1: an outside job's card is its job record's, so its resume chip is keyed by the record's uid too."""
    if not by_app:
        return {}
    rows = conn.execute("SELECT id, job_uid FROM applications WHERE profile = ? AND job_uid LIKE 'outside:%'", (pid,))
    return {r["job_uid"]: by_app[r["id"]] for r in rows if r["id"] in by_app}


def jd_for(conn, pid: str, uid: str) -> str:
    """The JD text from the stored snapshot: the person's kept JD for this job, else the feed's."""
    kept = conn.execute("SELECT coalesce(nullif(s.text, ''), a.jd_text) AS t FROM applications a "
                        "LEFT JOIN jd_snapshots s ON s.app_id = a.id WHERE a.profile = ? AND a.job_uid = ?",
                        (pid, uid)).fetchone()
    if kept and kept["t"] and len(kept["t"].strip()) >= MIN_JD_CHARS:
        return kept["t"].strip()
    row = conn.execute("SELECT description FROM jobs WHERE uid = ?", (uid,)).fetchone()
    if row is None:
        raise KeyError(uid)
    return strip_boilerplate(db.unpack(row["description"]) or "").strip()


def _read_posting(conn, uid: str, url: str | None, name: str) -> str:
    """A job first seen when already older than the detail window (Workday reads JDs only for jobs <= 2 days old) has
    no JD stored: read its posting once (robots-gated, polite) and keep the JD on the job. The fetcher keeps
    a stored JD on later sightings."""
    from . import jdsnap, jobfetch                                 # late: jdsnap imports tracker -> gsync
    state, raw, detail = jdsnap.fetch_text(url, jobfetch.db_resolver(conn), raw=True) if url else \
        ("unreadable", None, "no posting link")
    text = strip_boilerplate(raw or "").strip()
    if state != "saved" or len(text) < MIN_JD_CHARS:
        raise ValueError(f"{name}: no JD text stored and the posting gave none ({detail}), so no resume can be tailored")
    with db.tx(conn):
        conn.execute("UPDATE jobs SET description = ? WHERE uid = ? AND description IS NULL", (db.pack(raw), uid))
    return text


def home(pid: str) -> str:
    """The person's own city (the Remote choice)."""
    return str((config.profile(pid) or {}).get("location") or "").strip() or HOME


def picked(conn, pid: str, uid: str) -> str | None:
    """the city the person picked for this job ("City, ST" or REMOTE), None when never asked."""
    row = conn.execute("SELECT location FROM resume_places WHERE profile = ? AND uid = ?", (pid, uid)).fetchone()
    return None if row is None else (row["location"] or REMOTE)


def clean_pick(value) -> str:
    """REMOTE or a "City, ST" resumegen takes; typed text is read like a job location ("brooklyn park mn" too)."""
    text = " ".join(str(value or "").split())
    if text.lower() == REMOTE:
        return REMOTE
    words = text.replace(",", " ").split()
    for guess in (text, " ".join(words[:-1] + [words[-1].upper()]) if words else "", text.title()):
        place = resume_place.city_state(guess)
        if place:
            city, state = place.rsplit(", ", 1)
            return f"{city.title() if city.islower() else city}, {state}"
    raise ValueError(f"Type the city as 'Brooklyn Park, MN' (got {text!r})")


def remember(conn, pid: str, uid: str, value) -> str:
    place = clean_pick(value)
    with db.tx(conn):
        conn.execute("INSERT OR REPLACE INTO resume_places(profile, uid, location, picked_at) VALUES (?, ?, ?, ?)",
                     (pid, uid, None if place == REMOTE else place, db.iso(db.utcnow())))
    return place


def _ask(row, uid: str, place: dict, pid: str) -> dict:
    return {"uid": uid, "company": row["company"], "title": row["title"], "location": row["location"] or "",
            "choices": place["choices"], "home": home(pid)}


def _item(conn, pid: str, uid: str) -> dict:
    """What resumegen gets for one job, or {"ask": ...} when the person must pick the city first."""
    row = conn.execute("SELECT company, title, url, apply_url, workplace, location FROM jobs WHERE uid = ?",
                       (uid,)).fetchone()
    if row is None:
        raise KeyError(uid)
    text = jd_for(conn, pid, uid)
    if len(text) < MIN_JD_CHARS:
        text = _read_posting(conn, uid, row["url"] or row["apply_url"], f"{row['company']} · {row['title']}")
    place = resume_place.resolve(row["workplace"], row["location"], text)      # the contact line's city
    chosen = picked(conn, pid, uid)
    if chosen is None and place["pick"]:
        return {"ask": _ask(row, uid, place, pid)}
    workplace, location = place["workplace"], place["location"]
    if chosen is not None:
        workplace, location = ("remote", None) if chosen == REMOTE else (workplace, chosen)
    return {"profile": pid, "company": row["company"], "role": row["title"], "jd_text": text,
            "jd_url": row["url"] or row["apply_url"], "source_job_id": uid, "workplace": workplace,
            "location": location}


def generate(conn, pid: str, uids, places=None) -> dict:
    """Queue 1-20 jobs in resumegen in one request. Jobs already queued or generating are skipped, as are jobs
    with no stored JD (named in `skipped`); Offline when resumegen is down. `places` {uid: "City, ST" |
    "remote"} are the person's picks (kept for the job); a job still needing one comes back in `needs_place`."""
    if not isinstance(uids, list) or not uids or not all(isinstance(u, str) and u for u in uids):
        raise ValueError("pick at least one job")
    uids = list(dict.fromkeys(uids))
    if len(uids) > MAX_BATCH:
        raise ValueError(f"pick at most {MAX_BATCH} jobs at once ({len(uids)} picked)")
    if places is not None and not isinstance(places, dict):
        raise ValueError("places must be {job: city}")
    busy = {u for u, c in _latest(listing(pid), "source_job_id").items() if c["status"] in BUSY}
    for uid, value in (places or {}).items():
        if uid in uids and uid not in busy:                       # kept only for a job this Generate sends
            remember(conn, pid, uid, value)
    items, skipped, asks = [], [], []
    for uid in uids:
        if uid in busy:
            skipped.append({"uid": uid, "why": "already in the queue"})
            continue
        try:
            item = _item(conn, pid, uid)
        except ValueError as err:
            skipped.append({"uid": uid, "why": str(err)})
            continue
        (asks if "ask" in item else items).append(item.get("ask") or item)
    ids = rgclient.request("POST", "/api/jobs", {"jobs": items}).get("ids", []) if items else []
    try:                                                           # "Added to queue (#3)"
        place = rqueue.positions(listing(pid)) if ids else {}
    except rgclient.Offline:
        place = {}
    return {"queued": len(ids), "ids": ids, "skipped": skipped, "needs_place": asks,
            "positions": {it["source_job_id"]: place.get(i) for it, i in zip(items, ids)},
            "chips": {it["source_job_id"]: {"id": i, "chip": "queued", "status": "queued", "approved": False}
                      for it, i in zip(items, ids)}}


def _own(pid: str, rg_id: int) -> dict:
    got = rgclient.request("GET", f"/api/jobs/{int(rg_id)}")
    job = got.get("job") or {}
    if job.get("profile") != pid:
        raise KeyError(rg_id)
    return job


def detail(pid: str, rg_id: int) -> dict:
    """What the side panel shows; file links go through job-hunt-agent (/api/resumes/file)."""
    job = _own(pid, rg_id)
    result = job.get("result") or {}
    files = result.get("files") or {}
    return {"id": job["id"], "company": job["company"], "role": job["role"], "status": job["status"],
            "chip": (chip(job) or {}).get("chip"), "error": job.get("error"), "attempts": job.get("attempts"),
            "elapsed_s": job.get("elapsed_s"), "base": result.get("base"), "ats": result.get("ats"),
            "diff": result.get("diff"), "approved": result.get("approved"), "gdoc_link": job.get("gdoc_link"),
            "failures": (result.get("checks") or {}).get("failures") or [], "note": job.get("note"),
            "files": [name for name, key in FILES.items() if files.get(key.replace(".", "_"))],
            "source_job_id": job.get("source_job_id"), "headline": result.get("headline"),
            "file_name": _file_name(pid, result), "reasons": job.get("reasons") or [], "times": job.get("times") or {},
            "outdated": job.get("outdated") or [], "badge": job.get("badge"), "archived_at": job.get("archived_at"),
            "fix_note": job.get("fix_note"), "ats_html": rg_html.ats(result.get("ats")),
            "diff_html": rg_html.diff(result.get("diff"), result.get("mirror")),
            # GOAL19 §4 (resumegen GOAL5): the last edit in one line, how many edits Undo can take back (None: this
            # resumegen has no Undo, the button stays hidden), and when the files were made (file URLs carry it so
            # no viewer keeps the old PDF after a note or a re-import)
            "what_changed": job.get("what_changed"), "undo_count": job.get("undo_count"),
            "approved_stale": bool(job.get("approved_stale")),
            "step": (job.get("step") or {}).get("step"),      # what a running Re-import is doing now
            "files_version": job.get("files_version") or (job.get("times") or {}).get("updated") or job.get("updated_at")}


def _file_name(pid: str, result: dict, ext: str = ".pdf") -> str:
    """Downloads are named like the person's real resumes, with the title of the role it was tailored for (resumegen's
    headline), e.g. Alex_Rivera_ML_Engineer_Resume.pdf even when it was built from the Data Scientist base."""
    profile = {**config.profile(pid), "id": pid}
    return (resumes.base_file_name(profile, (result.get("base") or {}).get("id"), ext, role=result.get("headline"))
            or f"resume{ext}")


def file(pid: str, rg_id: int, which: str, download: bool = False) -> tuple[bytes, str, str]:
    """download=True asks resumegen to stamp "downloaded" on the card (resumegen GOAL4); the panel's view does not."""
    if which not in FILES:
        raise ValueError(f"file must be one of {sorted(FILES)}")
    job = _own(pid, rg_id)
    data, ctype = rgclient.request("GET", f"/api/jobs/{int(rg_id)}/file/{FILES[which]}" + ("?dl=1" if download else ""),
                                   timeout=rgclient.FILE_TIMEOUT_S, raw=True)
    kind = ctype.split(";")[0].strip().lower()
    if kind not in FILE_TYPES:                                  # only a PDF or a PNG is passed to the browser
        raise rgclient.Offline(f"{rgclient.OFFLINE} (it sent {kind or 'no type'} for the {which})")
    name = _file_name(pid, job.get("result") or {}, "." + which) if which == "pdf" else f"{which}.png"
    return data, kind, name


def _body(name: str, note: str | None, current: bool) -> dict:
    text = str(note or "").strip()[:MAX_NOTE]
    if name == "fix" and not text:
        raise ValueError("write what to fix (e.g. \"only make the summary shorter\")")
    if name == "regenerate":
        return {**({"note": text} if text else {}), **({"current": True} if current else {})}
    return {"note": text} if name == "fix" else {}


def action(pid: str, rg_id: int, name: str, note: str | None = None, current: bool = False) -> dict:
    """Approve (Drive upload; again after an edit: the same Drive file gets the new PDF), Open as Google Doc, Re-import
    the edited Doc, Regenerate (with an optional note; with current resume), Looks good (accept a needs-you resume as
    is), Fix with note, Undo my edit (the version before the last note / re-import), Cancel (drop it: Archived,
    restorable), Restore (un-archive)."""
    if name not in ACTIONS:
        raise ValueError(f"action must be one of {ACTIONS}")
    _own(pid, rg_id)
    body = _body(name, note, bool(current))
    if name == "restore":
        out = rgclient.request("POST", "/api/unarchive", {"ids": [int(rg_id)]})
        return {**out, "panel": detail(pid, rg_id)}
    slow = name in ("approve", "gdoc", "reimport")                       # Google Drive round trips
    out = rgclient.request("POST", f"/api/jobs/{int(rg_id)}/{name}", body,
                           timeout=rgclient.FILE_TIMEOUT_S * 4 if slow else rgclient.TIMEOUT_S)
    return {**out, "panel": detail(pid, rg_id)}


def approved_for(conn, pid: str, uid: str | None = None, app_id: int | None = None) -> dict | None:
    """The newest approved tailored resume for this feed job (or outside row): {'rg_id', 'file_id', 'link', 'name'}.
    None when there is none; Offline propagates (the caller falls back to the base resume)."""
    links = outside.links(conn, pid) if app_id else {}
    mine = [j for j in listing(pid) if j.get("drive_file_id") and
            ((uid and j.get("source_job_id") == uid) or (app_id and links.get(j["id"]) == app_id))]
    if not mine:
        return None
    job = max(mine, key=lambda j: j["id"])
    approved = (job.get("result") or {}).get("approved") or {}
    return {"rg_id": job["id"], "file_id": job["drive_file_id"], "link": job.get("drive_link") or approved.get("link"),
            "name": job.get("drive_name") or approved.get("name")}


def approve_ready(conn, pid: str, uid: str | None = None, app_id: int | None = None) -> dict | None:
    """a Yes to "Did you apply?" first approves (uploads to Drive) the job's newest ready resume when it
    is not approved yet, so the tracker row gets the tailored file. Returns what approve answered, None when there
    is nothing to approve; Offline / a refusal propagate (the caller attaches the base resume instead)."""
    links = outside.links(conn, pid) if app_id else {}
    mine = [j for j in listing(pid) if j.get("status") == "ready" and
            ((uid and j.get("source_job_id") == uid) or (app_id and links.get(j["id"]) == app_id))]
    if not mine:
        return None
    job = max(mine, key=lambda j: j["id"])
    return None if job.get("drive_file_id") else action(pid, job["id"], "approve")
