"""The Mark applied dialog: from a feed card, the job drawer or a tracker row, "Mark applied" first asks
which resume was used (the person's resumes, the best-scoring one preselected) and offers to upload the tailored
file (PDF/DOCX -> Drive job-hunt-agent/Resumes/<Profile>/, linked in the Sheet's Resume cell: resume_upload.py).
"Mark applied without file" leaves the row with the "resume missing" chip until a file is uploaded.

The browser sends the file in a second request (POST /api/tracker/resume), so a failed upload never loses the
applied mark: the row simply keeps its chip and its "Upload resume" button.
"""
from datetime import datetime

from . import api, config, db, jd_doc, jdsnap, ranking, resumes, tracker, trackapi

STATES = ("ok", "pending", "filtered", "too_senior")


def _app(conn, pid: str, uid: str | None, app_id: int | None) -> dict | None:
    if app_id:
        return trackapi._own(conn, pid, app_id)
    found = conn.execute("SELECT * FROM applications WHERE profile = ? AND job_uid = ?", (pid, uid)).fetchone()
    return dict(found) if found else None


def _scored(conn, profile: dict, uid: str, now: datetime) -> dict | None:
    ranked = ranking.rank(conn, profile, now, states=STATES, uid=uid)
    return api.resume_view(ranked[0][0], api._who(profile)) if ranked else None


def options(conn, pid: str, now: datetime, uid: str | None = None, app_id: int | None = None) -> dict:
    """What the dialog shows: the resumes (with their % for this job when it is scored), which one is preselected
    (the best under the job's vendor model; for a row without a scored job, the one for the row's role family), the
    row if it exists, and whether it already has an uploaded file."""
    if not uid and not app_id:
        raise ValueError("uid or app is required")
    profile = {**config.profile(pid), "id": pid}
    app = _app(conn, pid, uid, app_id)
    uid = uid or (app or {}).get("job_uid")
    mine = api._who(profile).resumes
    view = _scored(conn, profile, uid, now) if uid else None
    pcts = {x["id"]: x["pct"] for x in (view or {}).get("all", [])}
    best = (view or {}).get("best") or {}
    if not best and mine:
        family = trackapi.family_of(app) if app else None
        chosen = resumes.for_families(mine, [family] if family else []) or mine[0]
        best = {"id": chosen.id}
    job = conn.execute("SELECT company, title FROM jobs WHERE uid = ?", (uid,)).fetchone() if uid else None
    uploads = 0 if app is None else conn.execute("SELECT count(*) FROM resume_uploads WHERE app_id = ? "
                                                 "AND removed_at IS NULL", (app["id"],)).fetchone()[0]
    return {"resumes": [{"id": r.id, "label": r.label, "pct": pcts.get(r.id)} for r in mine],
            "best": best.get("id"), "vendor_label": (view or {}).get("label"),
            "job": {"company": job["company"], "title": job["title"]} if job else None,
            "app": None if app is None else {"id": app["id"], "status": app["status"], "company": app["company"],
                                             "role_title": app["role_title"], "uploads": uploads,
                                             "resume_used": app.get("resume_used")},
            "upload_max_mb": 10}


def _mark_row(conn, pid: str, app: dict, resume_id: str | None, prefill: bool) -> None:
    """A tracker row with no feed job (typed in the Sheet, added by hand): applied today with the resume picked."""
    profile = {**config.profile(pid), "id": pid}
    if app["status"] in api.PRE_APPLY:
        tracker.set_status(conn, app["id"], "applied", source="ui")
    mine = api._who(profile).resumes
    chosen = next((r for r in mine if r.id == resume_id), None)
    if chosen is not None:
        with db.tx(conn):
            conn.execute("UPDATE applications SET resume_used = ? WHERE id = ?", (chosen.id, app["id"]))
    if prefill and not (app.get("resume") or "").strip() and chosen is not None:
        tracker.edit_field(conn, app["id"], "resume", resumes.file_name(profile, chosen, app["company"], app.get("role_title")))
    jdsnap.on_save(conn, app["id"], fetch=True)                  # read the posting while it is live
    jd_doc.soon(app["id"])                                       # the full JD as a Google Doc


def mark(conn, pid: str, now: datetime, uid: str | None = None, app_id: int | None = None,
         resume_id: str | None = None, with_file: bool = False) -> int:
    """Mark applied from the dialog. with_file: the browser uploads the file next, so no "resume missing" chip;
    without it the chip shows until an upload (rows that already have a file never get it)."""
    if not uid and not app_id:
        raise ValueError("uid or app is required")
    app = _app(conn, pid, None, app_id) if app_id else None
    job = uid or (app or {}).get("job_uid")
    if job:                                  # with a file, the upload writes its own name into the Resume cell
        app_id = api.mark_applied(conn, pid, job, now, resume_id, prefill=not with_file)
    else:
        _mark_row(conn, pid, app, resume_id, prefill=not with_file)
    has_file = conn.execute("SELECT 1 FROM resume_uploads WHERE app_id = ? AND removed_at IS NULL",
                            (app_id,)).fetchone() is not None
    with db.tx(conn):
        conn.execute("UPDATE applications SET resume_missing = ? WHERE id = ?",
                     (0 if with_file or has_file else 1, app_id))
    return app_id
