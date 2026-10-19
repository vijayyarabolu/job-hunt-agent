"""GOAL8 §2 "Did you apply?" when you come back.

Apply ↗ remembers the job and when (pending_applies, per person, on the server so the phone and the laptop agree).
When the job-hunt-agent tab is visible again the page asks about each open entry, newest first:
  - Yes: confirm() marks the job applied, attaches the approved tailored resume from resumegen when there is one
    (else uploads the base resume that was used) as a Drive chip in the Sheet's Resume cell, fills what the app
    knows (Address from the location), and returns ONLY the Sheet columns still empty for that person's row, for a
    short form (Save writes them with the tracker's own update; Skip is allowed).
  - No: the job stays where it was.  - Later: snoozed; asked again the next time the app opens (Follow-ups badge).
Entries are never marked applied on their own: one older than 7 days drops off with a one-time note.
"""
import logging
import threading
from datetime import datetime, timedelta

from . import applydialog, config, db, resume_upload, rgclient, rgjobs, trackapi
from .google import GoogleError

log = logging.getLogger(__name__)
WINDOW = timedelta(days=7)
OPEN = ("pending", "snoozed")
ANSWERS = {"yes": "yes", "no": "no", "later": "snoozed"}
# Sheet columns the form never asks for: the app fills them (date, JD, resume, status) or they record outreach
# that happens later (follow-ups, email sent, LinkedIn request), with their own buttons in the tracker.
NOT_ASKED = frozenset({"company", "role_title", "date_applied", "jd", "resume", "status_ar", "follow_up_1",
                       "follow_up_2", "email_sent", "linkedin_request"})
PRE_APPLY = ("saved", "tailored")
_ATTACHING = threading.Lock()       # two tabs answering Yes at once: one file per row, never two


def _job_names(conn, pid: str, uid: str | None, app_id: int | None) -> tuple[str, str]:
    if uid:
        row = conn.execute("SELECT company, title FROM jobs WHERE uid = ?", (uid,)).fetchone()
        if row is None:
            raise KeyError(uid)
        return row["company"], row["title"]
    app = trackapi._own(conn, pid, app_id)
    return app.get("company") or "", app.get("role_title") or ""


def remember(conn, pid: str, now: datetime, uid: str | None = None, app_id: int | None = None) -> int:
    """Apply ↗ was clicked: one open entry per job (a second click moves its time forward and asks again)."""
    if not uid and not app_id:
        raise ValueError("uid or app is required")
    company, role = _job_names(conn, pid, uid, app_id)
    key, value = ("job_uid", uid) if uid else ("app_id", app_id)
    with db.tx(conn):
        found = conn.execute(f"SELECT id FROM pending_applies WHERE profile = ? AND {key} = ? AND state IN (?, ?)",
                             (pid, value, *OPEN)).fetchone()
        if found:
            conn.execute("UPDATE pending_applies SET opened_at = ?, state = 'pending' WHERE id = ?",
                         (db.iso(now), found["id"]))
            return found["id"]
        cur = conn.execute(f"INSERT INTO pending_applies(profile, {key}, company, role, opened_at) VALUES (?,?,?,?,?)",
                           (pid, value, company, role, db.iso(now)))
    return cur.lastrowid


def _applied_already(conn, pid: str, entry: dict) -> bool:
    if entry["job_uid"]:
        row = conn.execute("SELECT status FROM applications WHERE profile = ? AND job_uid = ?",
                           (pid, entry["job_uid"])).fetchone()
    else:
        row = conn.execute("SELECT status FROM applications WHERE id = ?", (entry["app_id"],)).fetchone()
    return bool(row) and row["status"] not in PRE_APPLY


def _close(conn, entry_id: int, state: str, now: datetime) -> None:
    with db.tx(conn):
        conn.execute("UPDATE pending_applies SET state = ?, answered_at = ? WHERE id = ?", (state, db.iso(now), entry_id))


def _view(entry: dict) -> dict:
    return {k: entry[k] for k in ("id", "job_uid", "app_id", "company", "role", "opened_at", "state")}


def due(conn, pid: str, now: datetime, boot: bool = False, peek: bool = False) -> dict:
    """{'ask': open entries newest first (snoozed ones only when the app was just opened), 'dropped': entries that
    passed 7 days (reported once), 'snoozed': how many wait for the next app open}. `peek` changes nothing (the
    Follow-ups list): no entry is expired, closed or reported."""
    cutoff = db.iso(now - WINDOW)
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM pending_applies WHERE profile = ? AND (state IN (?, ?) OR (state = 'expired' AND noted = 0)) "
        "ORDER BY opened_at DESC, id DESC", (pid, *OPEN))]
    ask, dropped = [], []
    for entry in rows:
        if entry["state"] == "expired" or entry["opened_at"] < cutoff:
            if not peek:
                dropped.append(_view({**entry, "state": "expired"}))
                with db.tx(conn):
                    conn.execute("UPDATE pending_applies SET state = 'expired', noted = 1 WHERE id = ?", (entry["id"],))
        elif _applied_already(conn, pid, entry):         # marked applied some other way meanwhile: nothing to ask
            if not peek:
                _close(conn, entry["id"], "yes", now)
        elif entry["state"] == "pending" or boot:
            ask.append(_view(entry))
    snoozed = conn.execute("SELECT count(*) FROM pending_applies WHERE profile = ? AND state = 'snoozed'",
                           (pid,)).fetchone()[0]
    return {"ask": ask, "dropped": dropped, "snoozed": snoozed}


def answer(conn, pid: str, entry_id: int, reply: str, now: datetime) -> dict:
    """The entry is claimed first (only one tab's answer counts); a Yes whose marking fails is put back."""
    if reply not in ANSWERS:
        raise ValueError(f"answer must be one of {sorted(ANSWERS)}")
    row = conn.execute("SELECT * FROM pending_applies WHERE id = ? AND profile = ?", (int(entry_id), pid)).fetchone()
    if row is None:
        raise KeyError(entry_id)
    entry, state = dict(row), ANSWERS[reply]
    with db.tx(conn):
        claimed = conn.execute(
            "UPDATE pending_applies SET state = ?, answered_at = ? WHERE id = ? AND state IN (?, ?)",
            (state, None if reply == "later" else db.iso(now), entry["id"], *OPEN)).rowcount
    if not claimed:                                          # another tab got there first: say what it answered
        now_state = conn.execute("SELECT state FROM pending_applies WHERE id = ?", (entry["id"],)).fetchone()[0]
        raise ValueError(f"already answered ({now_state})")
    out = {"id": entry["id"], "state": state}
    if reply == "yes":
        try:
            out.update(confirm(conn, pid, now, entry["job_uid"], entry["app_id"]))
        except Exception:
            with db.tx(conn):
                conn.execute("UPDATE pending_applies SET state = ?, answered_at = NULL WHERE id = ?",
                             (entry["state"], entry["id"]))
            raise
    return out


def confirm(conn, pid: str, now: datetime, uid: str | None = None, app_id: int | None = None,
            resume_id: str | None = None) -> dict:
    """Yes / Mark applied on an outside job: applied today, resume attached, known fields filled; returns the row id,
    what was attached and the Sheet columns still empty (the short form). resume_id: the base resume used, when known
    (the one the Autofill extension uploaded)."""
    outside_row = None if uid else app_id
    app_id = applydialog.mark(conn, pid, now, uid, app_id, resume_id, with_file=True)
    try:                                       # its ready resume goes to Drive first, then is attached
        rgjobs.approve_ready(conn, pid, uid, outside_row)
    except (rgclient.Offline, KeyError, ValueError) as err:
        log.warning("approving the ready resume for row %s failed: %s", app_id, err)
    attached = attach(conn, pid, app_id, uid)
    _fill_known(conn, pid, app_id)
    row = trackapi.get(conn, pid, app_id)
    return {"app": app_id, "attached": attached, "ask": missing(row),
            "company": row.get("company"), "role": row.get("role_title")}


def attach(conn, pid: str, app_id: int, uid: str | None) -> dict:
    """The approved tailored resume (resumegen), else the base resume used; never a second file for a row that has
    one. A failure leaves the row applied with the "resume missing" chip and the reason."""
    with _ATTACHING:
        if conn.execute("SELECT 1 FROM resume_uploads WHERE app_id = ? AND removed_at IS NULL",
                        (app_id,)).fetchone():
            return {"kind": "existing"}
        return _attach(conn, pid, app_id, uid)


def _attach(conn, pid: str, app_id: int, uid: str | None) -> dict:
    note = None
    try:
        tailored = rgjobs.approved_for(conn, pid, uid, app_id)
    except rgclient.Offline as err:
        tailored, note = None, f"{err}: attached the base resume"
    try:
        if tailored:
            made = resume_upload.link_existing(conn, pid, app_id, tailored["name"], tailored["file_id"],
                                               tailored["link"])
            return {"kind": "tailored", "name": made["name"], "link": made["link"], "sheet": made["sheet"],
                    "note": None}
        path = base_pdf(pid, trackapi._own(conn, pid, app_id).get("resume_used"))
        if path is None:
            raise ValueError("no base resume PDF in profiles/<you>/resumes")
        made = resume_upload.upload(conn, pid, app_id, path.name, path.read_bytes())
        return {"kind": "base", "name": made["name"], "link": made["link"], "sheet": made["sheet"], "note": note}
    except (ValueError, GoogleError, OSError) as err:
        log.warning("%s: resume not attached to row %s: %s", pid, app_id, err)
        with db.tx(conn):
            conn.execute("UPDATE applications SET resume_missing = 1 WHERE id = ?", (app_id,))
        return {"kind": "none", "error": str(err)}


def base_pdf(pid: str, resume_id: str | None):
    """The PDF of the base resume: the .pdf beside the profile's .docx for that resume, else the only PDF there."""
    folder = config.PROFILES / pid / "resumes"
    entries = config.profile(pid).get("resumes") or []
    entry = next((e for e in entries if str(e.get("id")) == str(resume_id)), None) or (entries[0] if entries else None)
    if entry and entry.get("file"):
        pdf = folder / (str(entry["file"]).rsplit(".", 1)[0] + ".pdf")
        if pdf.is_file():
            return pdf
    pdfs = sorted(folder.glob("*.pdf")) if folder.is_dir() else []
    return pdfs[0] if len(pdfs) == 1 else None


def _fill_known(conn, pid: str, app_id: int) -> None:
    """What the app knows and the row lacks: Address from the job's location."""
    app = trackapi._own(conn, pid, app_id)
    if not (app.get("address") or "").strip() and (app.get("location") or "").strip():
        trackapi.update(conn, pid, app_id, {"address": app["location"]})


def missing(row: dict) -> list[dict]:
    """The person's Sheet columns still empty on this row that a person types (one entry per field)."""
    sheet = row.get("sheet") or {}
    out, seen = [], set()
    for column, cell in zip(sheet.get("columns") or [], sheet.get("cells") or []):
        field = column.get("field")
        if not field or field in NOT_ASKED or field in seen or field not in trackapi.EDITABLE:
            continue
        seen.add(field)
        if cell in (None, ""):
            out.append({"field": field, "header": column["header"]})
    return out
