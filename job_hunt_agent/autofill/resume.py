"""GOAL23 §4 "Which resume?": the file the extension uploads for a job.

The approved tailored resume from resumegen for that job; else its Ready (not approved) one, with a warning; else
the base resume of the job's role family (the panel says which). The job site gets the plain base resume name, never
the company or a _v2 (user 2026-10-14: resume_upload.upload_name, e.g. Alex_Rivera_Data_Scientist_Resume.pdf); only
the copy kept with the application (tracker / Drive) is named with "(Company)" (resume_upload.file_name).
"""
from .. import applyback, config, outside, resume_upload, resumes, rgclient, rgjobs, titles, trackapi

PDF = "application/pdf"


def _family(conn, pid: str, uid: str | None, app_id: int | None) -> str | None:
    if uid:
        row = conn.execute("SELECT families, title FROM jobs WHERE uid = ?", (uid,)).fetchone()
        fams = [f for f in ((row["families"] if row else "") or "").split(",") if f]
        if fams:
            return fams[0]
        title = row["title"] if row else ""
        return next((name for name, rx in titles.FAMILIES.items() if rx.search(title or "")), None)
    if app_id:
        return trackapi.family_of(trackapi._own(conn, pid, app_id))
    return None


def base_entry(pid: str, family: str | None) -> dict | None:
    """The profile.yaml resume meant for the family, else the first one listed."""
    entries = [e for e in (config.profile(pid).get("resumes") or []) if isinstance(e, dict) and e.get("id")]
    for entry in entries:
        if family and family in (entry.get("families") or []):
            return entry
    return entries[0] if entries else None


def tailored_job(conn, pid: str, uid: str | None, app_id: int | None, jobs: list[dict] | None) -> dict | None:
    """The resumegen job whose resume is used: the newest approved, else the newest ready (None: a base resume)."""
    if not jobs or not (uid or app_id):
        return None
    links = outside.links(conn, pid) if app_id else {}
    mine = [j for j in jobs if j.get("status") == "ready" and not j.get("archived_at") and
            ((uid and j.get("source_job_id") == uid) or (app_id and links.get(j["id"]) == app_id))]
    if not mine:
        return None
    approved = [j for j in mine if j.get("drive_file_id")]
    return max(approved or mine, key=lambda j: j["id"])


def _where(conn, pid: str, uid: str | None, app_id: int | None) -> tuple[str, str]:
    """(company, role) of the job, for the file name."""
    if uid:
        row = conn.execute("SELECT company, title FROM jobs WHERE uid = ?", (uid,)).fetchone()
    else:
        row = conn.execute("SELECT company, role_title AS title FROM applications WHERE id = ? AND profile = ?",
                           (app_id, pid)).fetchone() if app_id else None
    return (row["company"] or "", row["title"] or "") if row else ("", "")


def _name(conn, profile: dict, uid: str | None, app_id: int | None, resume_id: str | None) -> str:
    _, role = _where(conn, profile["id"], uid, app_id)
    return resume_upload.upload_name(profile, {"resume_used": resume_id, "role_title": role}, ".pdf")


def pick(conn, pid: str, uid: str | None = None, app_id: int | None = None, jobs: list[dict] | None = None,
         offline: bool = False) -> dict:
    """{'kind': approved | ready | base | none, 'rg_id', 'resume_id', 'label', 'file_name', 'warning'}."""
    profile = {**config.profile(pid), "id": pid}
    job = tailored_job(conn, pid, uid, app_id, jobs)
    if job:
        base = (job.get("result") or {}).get("base") or {}
        approved = bool(job.get("drive_file_id"))
        title = resumes.title_label((job.get("result") or {}).get("headline") or _where(conn, pid, uid, app_id)[1])
        built = base.get("label")
        label = (f"Tailored resume ({title}, built from {built} base)" if title and built and title != built
                 else f"Tailored resume ({title or built or 'for this job'})")
        return {"kind": "approved" if approved else "ready", "rg_id": job["id"], "resume_id": base.get("id"),
                "label": label,
                "file_name": _name(conn, profile, uid, app_id, base.get("id")),
                "warning": None if approved else "Not approved yet: review it in job-hunt-agent before you submit"}
    entry = base_entry(pid, _family(conn, pid, uid, app_id))
    path = applyback.base_pdf(pid, entry["id"]) if entry else None
    if path is None:
        return {"kind": "none", "rg_id": None, "resume_id": None, "label": "No resume PDF found", "file_name": None,
                "warning": "No base resume PDF in your profile folder: attach the file yourself"}
    why = "Resume app is offline: " if offline else "No tailored resume for this job: "
    return {"kind": "base", "rg_id": None, "resume_id": entry["id"], "label": f"Base resume ({entry.get('label')})",
            "file_name": _name(conn, profile, uid, app_id, entry["id"]),
            "warning": why + f"using your base {entry.get('label')} resume"}


def choose(conn, pid: str, uid: str | None, app_id: int | None, jobs: list[dict] | None = None) -> dict:
    """pick() with resumegen's current list (offline -> the base resume, said so)."""
    if jobs is None:
        try:
            jobs = rgjobs.listing(pid)
        except rgclient.Offline:
            return pick(conn, pid, uid, app_id, None, offline=True)
    return pick(conn, pid, uid, app_id, jobs)


def file(conn, pid: str, uid: str | None, app_id: int | None) -> tuple[bytes, str, str]:
    """(PDF bytes, content type, file name) of the chosen resume."""
    choice = choose(conn, pid, uid, app_id)
    if choice["rg_id"]:
        data, kind, _ = rgjobs.file(pid, int(choice["rg_id"]), "pdf")
        return data, kind, choice["file_name"]
    if choice["kind"] == "none":
        raise KeyError("no resume PDF for this job")
    return applyback.base_pdf(pid, choice["resume_id"]).read_bytes(), PDF, choice["file_name"]
