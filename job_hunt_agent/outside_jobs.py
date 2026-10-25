"""GOAL36 §5.1: an outside job (a JD pasted in the Resume app, a link we do not track) gets a real job record, so its
card and detail page are an inside job's: match ring, Why this match, years asked, place, sponsorship, ghost, Mark
applied, tracker, follow-ups and the Sheet, with only a small "outside job" tag.

The record is a `jobs` row `outside:<person>:<tracker row id>` on the person's own board `outside:<person>`. That board
is marked review, so the feed, alerts, push, the daily count and the fetch-time fit refresh never see it (every one of
them reads only active, non-review boards); it is shown where the person put it: Tailored resumes, Applied, Saved and
its detail page (ranking reads it by uid). Years and place are read from the JD the way an inside job's are
(make_job: signals.min_years, workterms, sponsorship; the place is the tracker row's, else the JD's own Location line).
The person's fit is written here (fits.compute, the same scorer), again whenever the JD or the person's inputs change.
The tracker row points at the record (applications.job_uid), which is what makes every inside path take it."""
import logging

from . import config, db, fits, jdsnap, resume_place, store
from .ats.common import make_job
from .db import iso, pack

log = logging.getLogger(__name__)

ATS = "outside"
SOURCE = "manual"                  # the app-made tracker rows (outside.py): pasted in the Resume app or added by link


def board_id(pid: str) -> str:
    return f"{ATS}:{pid}"


def uid_of(pid: str, app_id: int) -> str:
    return f"{ATS}:{pid}:{int(app_id)}"


def is_outside(uid: str | None) -> bool:
    return (uid or "").startswith(f"{ATS}:")


def _board(conn, pid: str) -> None:
    conn.execute("INSERT OR IGNORE INTO boards(board, ats, key, company, review, active, source) "
                 "VALUES (?, ?, ?, 'Outside jobs', 1, 0, ?)", (board_id(pid), ATS, pid, ATS))


def _item(pid: str, app: dict, text: str) -> dict:
    place = app.get("location") or resume_place.location_in_jd(text) or ""
    return make_job(ats=ATS, key=pid, ext_id=str(app["id"]), company=(app.get("company") or "").strip() or "Unknown",
                    title=(app.get("role_title") or "").strip() or "Role", location=place,
                    workplace=app.get("workplace"), url=app.get("job_url") or "", description=text, precision="day")


def _write_job(conn, pid: str, app: dict, text: str, now) -> str:
    item = _item(pid, app, text)
    values = store._job_values(item, None)
    values.pop("_denied", None)
    families, _ = store.job_families(item["title"], text)
    values |= {"families": "," + ",".join(families) + "," if families else ",", "description": pack(text) or None}
    old = conn.execute("SELECT first_seen FROM jobs WHERE uid = ?", (item["uid"],)).fetchone()
    seen = old["first_seen"] if old else (app.get("updated_at") or iso(now))
    # last_seen: when the record was last written from the row (_todo compares it with the row's and JD's stamps)
    state = {"posted_at": None, "first_posted_at": None, "precision": "day", "first_seen": seen, "last_seen": iso(db.utcnow()),
             "miss_count": 0, "missing_since": None, "closed_at": None, "stale": 0}
    store._upsert(conn, board_id(pid), item, values, state)
    return item["uid"]


def _write_fit(conn, inp: fits.ProfileInputs, uid: str, now) -> None:
    """The person's fit for the record, unless the kept one is for this JD and today's inputs."""
    job = conn.execute("SELECT title, families, content_hash, description FROM jobs WHERE uid = ?", (uid,)).fetchone()
    kept = conn.execute("SELECT content_hash, lexicon_hash, baseline_hash, dealbreaker_hash FROM fits "
                        "WHERE profile = ? AND uid = ?", (inp.id, uid)).fetchone()
    if kept and tuple(kept) == (job["content_hash"], inp.lexicon_hash, inp.baseline_hash, inp.dealbreaker_hash):
        return
    fit = fits.compute(job["title"], job["description"], inp, job["families"])
    conn.execute("INSERT OR REPLACE INTO fits(profile, uid, content_hash, lexicon_hash, baseline_hash, dealbreaker_hash, "
                 "jd_state, jd_len, terms, dealbreaker, computed_at, genai) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                 (inp.id, uid, job["content_hash"], inp.lexicon_hash, inp.baseline_hash, inp.dealbreaker_hash,
                  fit["jd_state"], fit["jd_len"], fit["terms"], fit["dealbreaker"], iso(now), fit["genai"]))


def ensure(conn, pid: str, app_id: int, now=None, inp: fits.ProfileInputs | None = None) -> str | None:
    """The record of one app-made tracker row (made or brought up to date); None for a row that is not one, or one
    with no JD text yet (its small card says so; the record comes once the JD does)."""
    now = now or db.utcnow()
    row = conn.execute("SELECT id, company, role_title, job_url, location, workplace, updated_at, job_uid, source "
                       "FROM applications WHERE id = ? AND profile = ?", (int(app_id), pid)).fetchone()
    if row is None or row["source"] != SOURCE or (row["job_uid"] and not is_outside(row["job_uid"])):
        return None
    text = ((jdsnap.get(conn, row["id"]) or {}).get("text") or "").strip()
    if not text:
        return None
    inp = inp or fits.profile_inputs({**config.profile(pid), "id": pid})
    with db.tx(conn):
        _board(conn, pid)
        uid = _write_job(conn, pid, dict(row), text, now)
        _write_fit(conn, inp, uid, now)
        conn.execute("UPDATE applications SET job_uid = ? WHERE id = ? AND job_uid IS NULL", (uid, row["id"]))
    return uid


def _todo(conn, pid: str) -> list[int]:
    """The app-made rows whose record is missing, or older than the row's last edit or its JD."""
    return [r["id"] for r in conn.execute(
        "SELECT a.id FROM applications a LEFT JOIN jobs j ON j.uid = a.job_uid LEFT JOIN jd_snapshots s ON s.app_id = a.id "
        f"WHERE a.profile = ? AND a.source = ? AND (a.job_uid IS NULL OR (a.job_uid LIKE '{ATS}:%' AND (j.uid IS NULL "
        "OR j.last_seen < coalesce(a.updated_at, '') OR j.last_seen < coalesce(s.fetched_at, ''))))", (pid, SOURCE))]


def _stale_fits(conn, inp: fits.ProfileInputs) -> list[str]:
    """Records whose fit is missing or was scored with older inputs (a new resume, lexicon or dealbreakers)."""
    return [r["uid"] for r in conn.execute(
        "SELECT j.uid FROM jobs j LEFT JOIN fits f ON f.profile = ? AND f.uid = j.uid WHERE j.board = ? AND (f.uid IS NULL "
        "OR f.content_hash <> j.content_hash OR f.lexicon_hash <> ? OR f.baseline_hash <> ? OR f.dealbreaker_hash <> ?)",
        (inp.id, board_id(inp.id), inp.lexicon_hash, inp.baseline_hash, inp.dealbreaker_hash))]


def backfill(conn, pid: str, now=None) -> int:
    """Records for the person's app-made rows that need one (new, or the row or JD changed since), and fits rescored
    for the ones the person's inputs moved past. Every tab that lists outside jobs calls this, so a row with nothing
    new costs one query. A row that cannot be scored (no JD text yet) keeps its small card; the next look tries again.
    Returns how many records were written."""
    now = now or db.utcnow()
    rows = _todo(conn, pid)
    try:
        inp = fits.profile_inputs({**config.profile(pid), "id": pid})
    except (config.ConfigError, KeyError, ValueError) as err:
        log.warning("outside job records for %s: %s", pid, err)
        return 0
    done = 0
    for app_id in rows:
        try:
            done += ensure(conn, pid, app_id, now, inp) is not None
        except (ValueError, KeyError, OSError) as err:            # one odd row never stops the rest
            log.warning("outside job record for row %s: %s", app_id, err)
    for uid in _stale_fits(conn, inp):
        with db.tx(conn):
            _write_fit(conn, inp, uid, now)
    return done
