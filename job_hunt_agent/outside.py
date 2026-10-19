"""outside jobs. A resume generated in resumegen from a pasted JD (a recruiter post, not in job-hunt-agent's feed)
becomes a Saved tracker row here: company, role, posting link and the JD text. It is an ordinary app-made row (source
'manual', status 'saved'), recognised as outside by rg_links, so Mark applied works the same way and the Sheet gets
the row when it is applied (saved rows are not written to the Sheet). A pasted JD for a role already in the tracker
links to that row instead of adding a second one."""
import threading

from . import db, jdsnap, rgclient, tracker
from .google import state as gstate

PRE_APPLY = ("saved", "tailored")
PLACEHOLDERS = ("unknown", "role", "")          # resumegen's names for a JD it could not read a company / role from
_ADOPTING = threading.Lock()        # two tabs (or the phone and the laptop) polling at once must not add a job twice
_ROW = ("SELECT a.id, a.company, a.role_title, a.job_url, a.status, a.applied_on, l.rg_id FROM rg_links l "
        "JOIN applications a ON a.id = l.app_id WHERE l.profile = ?")


def links(conn, pid: str) -> dict[int, int]:
    """resumegen job id -> tracker row id, for the person's outside jobs."""
    return {r["rg_id"]: r["app_id"] for r in conn.execute("SELECT rg_id, app_id FROM rg_links WHERE profile = ?",
                                                          (pid,))}


def _same_row(conn, pid: str, company: str, role: str) -> int | None:
    if company.strip().lower() in PLACEHOLDERS or role.strip().lower() in PLACEHOLDERS:
        return None
    found = conn.execute("SELECT id FROM applications WHERE profile = ? AND lower(trim(company)) = lower(trim(?)) "
                         "AND lower(trim(role_title)) = lower(trim(?)) ORDER BY id DESC LIMIT 1",
                         (pid, company, role)).fetchone()
    return found["id"] if found else None


def _insert(conn, pid: str, company: str, role: str, url: str | None) -> int:
    tracker.ensure_profile(conn, pid)
    now = db.iso(db.utcnow())
    with db.tx(conn):
        cur = conn.execute("INSERT INTO applications(profile, company, role_title, job_url, jd, status, source, "
                           "updated_at) VALUES (?,?,?,?,?, 'saved', 'manual', ?)", (pid, company, role, url, url, now))
        conn.execute("INSERT INTO application_events(app_id, at, from_status, to_status, source) "
                     "VALUES (?,?,NULL,'saved','ui')", (cur.lastrowid, now))
        gstate.mark_dirty(conn)
    return cur.lastrowid


def _adopt(conn, pid: str, job: dict) -> int | None:
    """The tracker row for one outside resumegen job (found or made); a new row keeps the pasted JD as its snapshot
    (a row found by company + role keeps its own). None when resumegen no longer has the job."""
    try:
        text = (rgclient.request("GET", f"/api/jobs/{int(job['id'])}").get("jd_text") or "").strip()
    except KeyError:
        return None
    company, role = (job.get("company") or "").strip() or "Unknown", (job.get("role") or "").strip() or "Role"
    found = _same_row(conn, pid, company, role)
    app_id = found or _insert(conn, pid, company, role, job.get("jd_url"))
    if text and not found:
        jdsnap.keep(conn, app_id, job.get("jd_url"), text, "pasted")
        jdsnap.fill_cell(conn, app_id)
    with db.tx(conn):
        conn.execute("INSERT OR IGNORE INTO rg_links(rg_id, profile, app_id, created_at) VALUES (?,?,?,?)",
                     (int(job["id"]), pid, app_id, db.iso(db.utcnow())))
    return app_id


def sync(conn, pid: str, jobs: list[dict]) -> int:
    """Adopt the person's outside resumegen jobs not seen before (cancelled ones are skipped). Returns how many."""
    with _ADOPTING:
        known = links(conn, pid)
        new = [j for j in jobs if j.get("profile") == pid and not j.get("source_job_id") and j["id"] not in known
               and j.get("status") != "cancelled"]
        return sum(1 for job in new if _adopt(conn, pid, job) is not None)


def saved_rows(conn, pid: str) -> list[dict]:
    """The Saved tab's outside jobs (not applied yet), newest first, one entry per row."""
    rows, seen = [], set()
    for r in conn.execute(_ROW + " ORDER BY l.rg_id DESC", (pid,)):
        if r["status"] in PRE_APPLY and r["id"] not in seen:
            seen.add(r["id"])
            rows.append({k: r[k] for k in ("id", "company", "role_title", "job_url", "status", "rg_id")})
    return rows
