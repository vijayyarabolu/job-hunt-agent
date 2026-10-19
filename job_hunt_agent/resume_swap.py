"""change a row's resume from wherever the person is (the tracker's Resume cell, the job detail, the
Resumes tab), from now on only (old rows change only when the person clicks).

- × (remove): the row's current files (resume_uploads not removed) leave the row, each Drive file moves to
  job-hunt-agent/Resumes/<Profile>/Replaced/ (kept, never deleted), the Resume field is cleared (the next sync empties the
  Sheet cell) and the cell offers "Upload resume" again. Undo puts files, folder and text back.
- Replace ▾ (menu(), server HTML): "Upload a file…" (the existing upload, replacing), the job's ready / approved
  resumegen versions newest first (picking one approves it to Drive if needed and links it), "Generate again".
- retire() / finish(): every new file for a row that already has one goes through here (an upload, a picked version,
  an Approve in resumegen): the old files retire first, then the Resume field is exactly the new file's name, which
  the sync writes as that file's Drive chip: one chip, never a second. Undo of a replacement brings the old ones
back.
"""
import logging
import uuid
from html import escape

from . import db, gdrive_upload, outside, rgclient, rgjobs, trackapi
from .applied import APPLIED_STATES
from .google import GoogleError, accounts, api, base
from .google import state as gstate

log = logging.getLogger(__name__)
UNDO_KEY = "resume_swap.{batch}"
REPLACED = "Replaced"
FIELD = "resume"                    # resume_upload.RESUME_FIELD
VERSIONS_SHOWN = 10
READY = ("ready", "needs_you")


def current(conn, app_id: int) -> list[dict]:
    """The row's files now (an upload stays current until it is removed or replaced)."""
    return [dict(r) for r in conn.execute("SELECT * FROM resume_uploads WHERE app_id = ? AND removed_at IS NULL "
                                          "ORDER BY version", (app_id,))]


def _by_ids(conn, ids: list[int]) -> list[dict]:
    if not ids:
        return []
    return [dict(r) for r in conn.execute(f"SELECT * FROM resume_uploads WHERE id IN ({','.join('?' * len(ids))})",
                                          ids)]


# ---- Drive: the old file is moved, never deleted ----

def _move(conn, pid: str, file_id: str, into_replaced: bool) -> None:
    home = (*gdrive_upload.ROOT_PATH, pid.title())
    target = gdrive_upload.folder(conn, (*home, REPLACED) if into_replaced else home)["id"]
    parents = api.get(f"{base('drive')}/files/{file_id}", {"fields": "parents"}).get("parents") or []
    if target not in parents:
        api.call("PATCH", f"{base('drive')}/files/{file_id}", json_body={}, params={
            "addParents": target, "removeParents": ",".join(parents), "fields": "id"})


def _move_all(conn, pid: str, uploads: list[dict], into_replaced: bool) -> str:
    """Best effort: the row changes either way; a file Google will not move stays where it is (said in the note)."""
    stuck = []
    for up in uploads:
        try:
            accounts.with_fallback(pid, _move, conn, pid, up["file_id"], into_replaced)
        except GoogleError as err:
            log.warning("%s: resume file %s not moved: %s", pid, up["file_id"], err)
            stuck.append(up["name"])
    return f" ({', '.join(stuck)} stayed where it was in Drive)" if stuck else ""


# ---- remove / replace / undo ----

def _save_undo(conn, batch: str, record: dict) -> None:
    with db.tx(conn):
        gstate.put(conn, UNDO_KEY.format(batch=batch), record)


def retire(conn, pid: str, app: dict) -> dict:
    """Take the row's current files off it (moved to Replaced/) and remember what to bring back, the Sheet cell as
    it was included (its chips and links); the caller then links the new file (finish) or leaves the row empty."""
    from . import resume_upload                                    # it imports this module
    files, batch = current(conn, app["id"]), uuid.uuid4().hex[:16]
    record = {"app": app["id"], "profile": pid, "text": app.get(FIELD) or "", "old": [f["id"] for f in files],
              "new": [], "note": "", "cell": resume_upload.sheet_cell(conn, pid, app["id"])}
    _save_undo(conn, batch, record)                                 # first: whatever fails later, Undo knows
    with db.tx(conn):
        conn.executemany("UPDATE resume_uploads SET removed_at = ?, removed_batch = ? WHERE id = ?",
                         [(db.iso(db.utcnow()), batch, f["id"]) for f in files])
    record = {**record, "note": _move_all(conn, pid, files, True)}
    _save_undo(conn, batch, record)
    return {**record, "batch": batch}


def finish(conn, pid: str, app_id: int, retired: dict, upload_id: int, name: str) -> dict:
    """The new file is the row's only resume: the Resume field is its name (the sync writes it as its chip)."""
    from . import resume_upload                                    # it imports this module
    _save_undo(conn, retired["batch"], {**{k: v for k, v in retired.items() if k != "batch"}, "new": [upload_id]})
    with db.tx(conn):
        conn.execute("UPDATE applications SET resume_missing = 0 WHERE id = ?", (app_id,))
    return {"undo": retired["batch"], "sheet": resume_upload.rewrite(conn, pid, app_id, name) + retired["note"]}


def remove(conn, pid: str, app_id: int) -> dict:
    app = trackapi._own(conn, pid, app_id)
    if not current(conn, app_id) and not (app.get(FIELD) or "").strip():
        raise ValueError("this row has no resume to remove")
    from . import resume_upload
    retired = retire(conn, pid, app)
    resume_upload.rewrite(conn, pid, app_id, "")
    return {"msg": "Resume removed from the row: the file is kept in Drive (Resumes → Replaced)" + retired["note"],
            "undo": retired["batch"], "row": trackapi.get(conn, pid, app_id)}


def undo(conn, pid: str, batch: str) -> dict:
    record = gstate.get_json(conn, UNDO_KEY.format(batch=batch)) if batch.isalnum() else None
    if not record or record.get("profile") != pid:
        raise ValueError("nothing to undo")
    if sorted(f["id"] for f in current(conn, record["app"])) != sorted(record["new"]):
        raise ValueError("this row's resume changed since: use Replace ▾ instead")
    new, old = _by_ids(conn, record["new"]), _by_ids(conn, record["old"])
    with db.tx(conn):
        conn.executemany("UPDATE resume_uploads SET removed_at = ?, removed_batch = ? WHERE id = ?",
                         [(db.iso(db.utcnow()), f"undo-{batch}", f["id"]) for f in new])
        conn.executemany("UPDATE resume_uploads SET removed_at = NULL, removed_batch = NULL WHERE id = ?",
                         [(f["id"],) for f in old])
        gstate.put(conn, UNDO_KEY.format(batch=batch), None)
    from . import resume_upload
    note = _move_all(conn, pid, new, True) + _move_all(conn, pid, old, False)
    if record.get("cell"):
        resume_upload.put_back(conn, pid, record["app"], record["cell"], record["text"])
    else:
        resume_upload.rewrite(conn, pid, record["app"], record["text"])
    return {"msg": "Undone: the row's resume is back" + note, "row": trackapi.get(conn, pid, record["app"])}


# ---- resumegen: the job's versions, Use one, Generate again, Approve ----

def _rg_jobs(conn, pid: str, app: dict) -> list[dict]:
    """This row's resumegen jobs, newest first: the feed job's (by uid) or the outside job's (rg_links)."""
    links = outside.links(conn, pid)
    mine = (j for j in rgjobs.listing(pid)
            if (app.get("job_uid") and j.get("source_job_id") == app["job_uid"]) or links.get(j["id"]) == app["id"])
    return sorted(mine, key=lambda j: j["id"], reverse=True)


def _approved(job: dict) -> dict:
    approved = (job.get("result") or {}).get("approved") or {}
    return {"file_id": job.get("drive_file_id"), "link": job.get("drive_link") or approved.get("link"),
            "name": job.get("drive_name") or approved.get("name")}


def link_version(conn, pid: str, app_id: int, job: dict) -> dict:
    """An approved resumegen version becomes the row's resume (replacing what it had); a no-op when it already is."""
    from . import resume_upload                                    # it imports this module
    got = _approved(job)
    if any(f["file_id"] == got["file_id"] for f in current(conn, app_id)):
        return {"msg": "That version is already this row's resume"}
    if conn.execute("SELECT 1 FROM resume_uploads WHERE file_id = ? AND removed_at IS NOT NULL",
                    (got["file_id"],)).fetchone():                  # picked again after a ×: out of Replaced/
        _move_all(conn, pid, [{"file_id": got["file_id"], "name": got["name"]}], False)
    made = resume_upload.link_existing(conn, pid, app_id, got["name"], got["file_id"], got["link"], replace=True)
    return {"msg": f"Linked {made['name']} · {made['sheet']}", "undo": made.get("undo")}


def use(conn, pid: str, app_id: int, rg_id: int) -> dict:
    app = trackapi._own(conn, pid, app_id)
    job = next((j for j in _rg_jobs(conn, pid, app) if j["id"] == rg_id), None)
    if job is None:
        raise ValueError("that resume is not one of this job's")
    if not job.get("drive_file_id"):
        rgjobs.action(pid, job["id"], "approve")                        # to Drive, the usual …Resume(Company).pdf
        job = rgjobs._own(pid, job["id"])
    return {**link_version(conn, pid, app_id, job), "row": trackapi.get(conn, pid, app_id)}


def generate(conn, pid: str, app_id: int) -> dict:
    app = trackapi._own(conn, pid, app_id)
    if app.get("job_uid"):
        out = rgjobs.generate(conn, pid, [app["job_uid"]])
        if not out["queued"]:
            raise ValueError(out["skipped"][0]["why"] if out["skipped"] else "not queued")
    else:
        jobs = _rg_jobs(conn, pid, app)
        if not jobs:
            raise ValueError("this row has no job description to tailor a resume from")
        rgjobs.action(pid, jobs[0]["id"], "regenerate")
    return {"msg": "Generating again: it shows in Resumes → Ready; approving it replaces this row's resume"}


def after_approve(conn, pid: str, rg_id: int) -> str | None:
    """An Approve in resumegen (Resumes tab, resume panel) for a job already on the tracker as applied: the approved
    file becomes that row's resume, replacing the old one. None when there is no such row."""
    job = rgjobs._own(pid, rg_id)
    app_id = outside.links(conn, pid).get(job["id"])
    if app_id is None and job.get("source_job_id"):
        found = conn.execute("SELECT id FROM applications WHERE profile = ? AND job_uid = ?",
                             (pid, job["source_job_id"])).fetchone()
        app_id = found["id"] if found else None
    if not _applied(conn, app_id):                            # the row typed into the Sheet for it
        app_id = _applied_by_url(conn, pid, job.get("jd_url"))
    if app_id is None or not job.get("drive_file_id"):
        return None
    return link_version(conn, pid, app_id, job)["msg"]


def _applied(conn, app_id: int | None) -> bool:
    app = conn.execute("SELECT status FROM applications WHERE id = ?", (app_id,)).fetchone() if app_id else None
    return bool(app and app["status"] in APPLIED_STATES)


def _applied_by_url(conn, pid: str, url: str | None) -> int | None:
    """The applied row for the same posting that has no job link (typed into the Sheet), found by its posting URL."""
    if not url:
        return None
    marks = ", ".join("?" * len(APPLIED_STATES))
    found = conn.execute(
        f"SELECT a.id FROM applications a LEFT JOIN jd_snapshots s ON s.app_id = a.id WHERE a.profile = ? "
        f"AND a.status IN ({marks}) AND ? IN (a.job_url, a.apply_url, s.url) ORDER BY a.id DESC LIMIT 1",
        (pid, *APPLIED_STATES, url)).fetchone()
    return found["id"] if found else None


# ---- Replace ▾ (server HTML; the page sends data-r / value back) ----

def _item(text: str, title: str, attrs: str) -> str:
    return f'<button type="button" class="opt" {attrs} title="{escape(title)}"><span>{escape(text)}</span></button>'


def _versions(conn, pid: str, app: dict) -> str:
    try:
        jobs = [j for j in _rg_jobs(conn, pid, app) if j.get("status") in READY or j.get("drive_file_id")]
    except rgclient.Offline as err:
        return f'<p class="pop-h">{escape(str(err))}</p>'
    if not jobs:
        return '<p class="pop-h">No ready resume for this job in resumegen</p>'
    now = {f["file_id"] for f in current(conn, app["id"])}
    items = []
    for j in jobs[:VERSIONS_SHOWN]:
        made = str((j.get("times") or {}).get("made") or j.get("finished_at") or "")[:16].replace("T", " ")
        state = "on this row" if j.get("drive_file_id") in now else "approved" if j.get("drive_file_id") else "ready"
        items.append(_item(" · ".join(x for x in (f"#{j['id']}", state, made) if x),
                           "Make this version the row's resume (approved to Drive first if needed)",
                           f'value="{int(j["id"])}"'))
    return '<p class="pop-h">Use a ready resume from resumegen</p>' + "".join(items)


def menu(conn, pid: str, app_id: int) -> str:
    app = trackapi._own(conn, pid, app_id)
    return (_item("Upload a file…", "Upload a PDF or DOCX: it replaces this row's resume", 'data-r="upload"')
            + _versions(conn, pid, app)
            + _item("Generate again", "Queue a new tailored resume; approving it replaces this row's resume",
                    'data-r="generate"'))


def act(conn, pid: str, args: dict) -> dict:
    """POST /api/resume/act {app, act, id}: one button of the Resume cell, the job detail or a Resumes row."""
    name, app_id, value = str(args.get("act") or ""), int(args["app"]), args.get("id")
    if name == "remove":
        return remove(conn, pid, app_id)
    if name == "undo":
        return undo(conn, pid, str(value or ""))
    if name == "use":
        if not str(value or "").isdigit():
            raise ValueError("pick a version from the list")
        return use(conn, pid, app_id, int(value))
    if name == "generate":
        return generate(conn, pid, app_id)
    if name == "menu":
        return {"html": menu(conn, pid, app_id)}
    raise ValueError("act must be one of remove, undo, use, generate, menu")
