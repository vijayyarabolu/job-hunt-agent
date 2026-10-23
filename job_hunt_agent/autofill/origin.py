"""GOAL30 §2: where a saved answer came from, so the panel can say "Written for Acme, AI Engineer" and offer
Regenerate for this job, and flag an answer that names another company ("Names Acme") before the user submits it.

Read only: the learned log (an answer the user typed), saved long answers (Save to Answers), the job and tracker rows."""
import re

MIN_NAME = 3
MAX_COMPANIES = 400


def _title(conn, pid: str, uid: str | None, app_id: int | None) -> str:
    if uid:
        row = conn.execute("SELECT title FROM jobs WHERE uid = ?", (uid,)).fetchone()
        if row:
            return row["title"]
    if app_id:
        row = conn.execute("SELECT role_title FROM applications WHERE id = ? AND profile = ?", (app_id, pid)).fetchone()
        if row:
            return row["role_title"] or ""
    return ""


def _view(conn, pid: str, row, job_key: str) -> dict | None:
    mine = row["job_uid"] or (f"app:{row['app_id']}" if row["app_id"] else "")
    if not row["company"] or (job_key and mine == job_key):
        return None                                     # written for this very job: nothing to say
    return {"company": row["company"], "title": _title(conn, pid, row["job_uid"], row["app_id"]),
            "at": str(row["at"] or "")[:10]}


def of(conn, pid: str, key: str | None, job_key: str = "") -> dict | None:
    """{company, title, at} of the job a saved answer ('q:<question>') was last written for, else None."""
    if not key:
        return None
    row = conn.execute("SELECT company, job_uid, app_id, at FROM autofill_learned WHERE profile = ? AND key = ? AND "
                       "undone_at IS NULL ORDER BY at DESC, id DESC LIMIT 1", (pid, key)).fetchone()
    return _view(conn, pid, row, job_key) if row else None


def of_snippet(conn, pid: str, text: str, job_key: str = "") -> dict | None:
    """{company, title, at} of the saved long answer this text is, else None."""
    if not str(text or "").strip():
        return None
    row = conn.execute("SELECT company, job_uid, app_id, at FROM autofill_snippets WHERE profile = ? AND text = ? "
                       "ORDER BY at DESC, id DESC LIMIT 1", (pid, text)).fetchone()
    return _view(conn, pid, row, job_key) if row else None


def _firm(name: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(name or "").lower()).strip()


def names_other(conn, pid: str, text: str, company: str | None) -> str | None:
    """The first company the user applied to (or saved an answer for) that this text names, other than this job's
    company: an old "Why Acme?" answer reused at Exelixis."""
    body = str(text or "")
    if not body.strip():
        return None
    mine = _firm(company)
    names = [r["company"] for r in conn.execute(
        "SELECT company FROM applications WHERE profile = ? AND company IS NOT NULL UNION "
        "SELECT company FROM autofill_snippets WHERE profile = ? AND company IS NOT NULL LIMIT ?",
        (pid, pid, MAX_COMPANIES))]
    for name in sorted({n.strip() for n in names if n and len(n.strip()) >= MIN_NAME}, key=len, reverse=True):
        firm = _firm(name)
        if not firm or firm == mine or (mine and (firm in mine or mine in firm)):
            continue
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])", body):
            return name
    return None


def label_of(view: dict | None) -> str:
    """"Written for Acme, AI Engineer" (no dashes)."""
    if not view:
        return ""
    return f"Written for {view['company']}" + (f", {view['title']}" if view.get("title") else "")

