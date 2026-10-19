"""Work history, education and summary for a form, from the resume being uploaded (user 2026-10-14).

Every job of that resume in its order: title, company, location, start / end month and year (or "I currently work
here") and its bullets as the description, one "• bullet" per line with no blank lines. The tailored resume when there
is one (resumegen's diff, 'new' side), else the base resume file uploaded (its .docx), else baseline.json. Education is
the person's own list (Answers page), else the base resume's. The Summary box gets the resume's summary word for word.
"""
import logging

from ..resume import baseline
from . import bank, draft

log = logging.getLogger(__name__)
BULLET = "• "


def description(bullets) -> str:
    """One "• bullet" per line, in order, no blank lines (a bullet's own line breaks become spaces)."""
    return "\n".join(BULLET + " ".join(str(b).split()).lstrip("•-–* ") for b in bullets or [] if str(b or "").strip())


def _from_resume(parsed: dict) -> dict:
    return {"summary": parsed.get("summary") or "",
            "jobs": [{"company": j.get("company"), "title": j.get("title"), "dates": j.get("dates"),
                      "bullets": j.get("bullets") or []} for j in parsed.get("experience") or []]}


def resume_data(pid: str, choice: dict) -> dict:
    """{'summary', 'jobs'} of the exact resume uploaded for the job."""
    if choice.get("rg_id"):
        return draft.content(pid, choice["rg_id"])
    path = draft._base_docx(pid, choice.get("resume_id"))
    if path:
        try:
            return _from_resume(baseline.parse(path))
        except Exception as err:                       # a .docx python-docx cannot read: the baseline instead
            log.warning("base resume %s unreadable for work history: %s", path.name, err)
    return draft.content(pid, None)


def job_rows(jobs: list[dict]) -> list[dict]:
    out = []
    for job in jobs:
        company, where = bank._company(job.get("company"))
        start, end, current = bank._span(job.get("dates"))
        out.append({"title": str(job.get("title") or "").strip(), "company": company, "location": where,
                    "start": start, "end": "" if current else end, "current": current,
                    "description": description(job.get("bullets"))})
    return out


def for_job(conn, pid: str, choice: dict) -> dict:
    """{'summary', 'jobs', 'schools'} for this job's form."""
    data = resume_data(pid, choice)
    saved = bank.history(conn, pid)
    jobs = job_rows(data.get("jobs") or []) or [{**j, "description": ""} for j in saved["jobs"]]   # no jobs read: the user's list
    schools = saved["schools"] or bank.history_from_resume(pid)[1]
    return {"summary": " ".join(str(data.get("summary") or "").split()), "jobs": jobs, "schools": schools}
