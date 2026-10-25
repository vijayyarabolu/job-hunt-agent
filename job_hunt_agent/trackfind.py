"""GOAL35 §5.2 Tracker search: every application the person ever saved, applied to, interviewed for, was rejected by or
went cold on, any age, found by company (and its aliases), title, city, status, notes, contacts, the resume's file
name and the JD text (the row's JD Text, its kept snapshot and the feed posting). textsearch does the matching; the
index is kept per person until anything it reads changes.

He reported (2026-10-23) "Tracker search is not finding the previous roles I applied": the old search was one
lower(...) LIKE '%whole query%' over seven columns, so a typo, words in another order or split by punctuation
("Engineer, Vector AI" for "engineer vector"), a JD skill, a status or a date never matched.
"""
from . import config, textsearch, tracker
from .db import unpack

STATUS_WORDS = {"saved": "saved", "tailored": "saved tailored", "applied": "applied", "screen": "screen interviewing",
                "interview": "interview interviewing", "offer": "offer", "rejected": "rejected archived",
                "ghosted": "cold ghosted archived"}
_FINGERPRINT = ("SELECT (SELECT count(*) || '/' || coalesce(max(id), 0) || '/' || coalesce(max(updated_at), '') "
                "FROM applications WHERE profile = ?), (SELECT count(*) || '/' || coalesce(max(fetched_at), '') "
                "FROM jd_snapshots), (SELECT coalesce(max(id), 0) FROM resume_uploads WHERE profile = ?)")


def _texts(conn, sql: str, pid: str) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    for app_id, text in conn.execute(sql, (pid,)):
        if text:
            out.setdefault(app_id, []).append(text)
    return out


def _record(a: dict, snaps: list[str], files: list[str], posted: str) -> textsearch.Record:
    jd = " ".join([a.get("jd_text") or "", a.get("jd") or "", *snaps, posted])
    return textsearch.Record(a["id"], {
        "company": a.get("company"), "title": a.get("role_title"), "city": a.get("location"),
        "status": STATUS_WORDS.get(a.get("status"), a.get("status")),
        "notes": " ".join(filter(None, (a.get("notes"), a.get("team_name"), a.get("address")))),
        "contacts": " ".join(filter(None, (a.get("target_name"), a.get("target_role"), a.get("email_address")))),
        "resume": " ".join(filter(None, (a.get("resume"), a.get("resume_manual"), a.get("resume_used"), *files))),
        "link": " ".join(filter(None, (a.get("job_url"), a.get("apply_url")))),
        "jd": jd}, (a.get("applied_on"), tracker.parse_applied(a.get("date_applied")), (a.get("updated_at") or "")[:10]))


def records(conn, pid: str) -> list[textsearch.Record]:
    """One record per application of the person."""
    snaps = _texts(conn, "SELECT s.app_id, s.text FROM jd_snapshots s JOIN applications a ON a.id = s.app_id "
                         "WHERE a.profile = ?", pid)
    files = _texts(conn, "SELECT app_id, coalesce(name, '') || ' ' || coalesce(original_name, '') FROM resume_uploads "
                         "WHERE profile = ? AND removed_at IS NULL", pid)
    posted = {r[0]: unpack(r[1]) for r in conn.execute(
        "SELECT a.id, j.description FROM applications a JOIN jobs j ON j.uid = a.job_uid WHERE a.profile = ? "
        "AND j.description IS NOT NULL", (pid,))}
    return [_record(dict(a), snaps.get(a["id"], []), files.get(a["id"], []), posted.get(a["id"], ""))
            for a in conn.execute("SELECT * FROM applications WHERE profile = ?", (pid,))]


def index(conn, pid: str) -> textsearch.Index:
    mark = tuple(conn.execute(_FINGERPRINT, (pid, pid)).fetchone())
    return textsearch.cached(("tracker", str(config.DB_PATH), pid, mark), lambda: records(conn, pid))


def find(conn, pid: str, q: str) -> textsearch.Found:
    return index(conn, pid).search(textsearch.parse(q))
