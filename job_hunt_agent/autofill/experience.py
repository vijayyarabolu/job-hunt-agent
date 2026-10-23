"""Work history, education and summary for a form, from the resume being uploaded (user 2026-10-14).

Every job of that resume in its order: title, company, location, start / end month and year (or "I currently work
here") and its bullets as the description, one "• bullet" per line with no blank lines. The tailored resume when there
is one (resumegen's diff, 'new' side), else the base resume file uploaded (its .docx), else baseline.json. Education is
the person's own list (Answers page), else the base resume's. The Summary box gets the resume's summary word for word.
"""
import logging
import re

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


_US = re.compile(r"\b(usa|u s( a)?|us|united states( of america)?|america)\b", re.I)
_INDIA = re.compile(r"\b(india|bengaluru|bangalore|hyderabad|chennai|mumbai|pune|noida|gurgaon|gurugram|delhi|new delhi|"
                    r"kolkata|ahmedabad|kochi|coimbatore|visakhapatnam|warangal|trivandrum|thiruvananthapuram|"
                    r"jaipur|indore|mysore|mysuru|nagpur|chandigarh|lucknow|bhubaneswar|telangana|karnataka|maharashtra|"
                    r"tamil nadu|andhra pradesh|kerala|gujarat)\b", re.I)
_COUNTRIES = ("Canada", "United Kingdom", "Germany", "Ireland", "Singapore", "Australia", "Mexico", "Brazil", "France",
              "Netherlands", "Japan", "China", "Israel", "Poland", "Spain", "Philippines", "UAE", "United Arab Emirates")


def country(where: str | None) -> str:
    """1.3.1 (user 2026-10-15): a past job's Location on any form is the employer's country: a US state or city ->
    "United States", an Indian city or "India" -> "India", another country as itself; '' when it says none."""
    from .. import resume_place
    text = " ".join(str(where or "").replace(".", " ").split())
    if not text:
        return ""
    if _INDIA.search(text):
        return "India"
    hit = next((c for c in _COUNTRIES if re.search(rf"\b{re.escape(c)}\b", text, re.I)), None)
    if hit:
        return hit
    code = re.search(r"(?:^|[\s,])([A-Z]{2})\s*$", str(where).strip())
    if _US.search(text) or resume_place._STATE_NAME.search(text) or (code and code.group(1) in resume_place.STATE_CODES.values()) \
            or text.lower() in resume_place.KNOWN_CITIES:
        return "United States"
    return ""


def split_company(raw: str | None) -> tuple[str, str]:
    """'Globex Corp, Austin TX' / 'Globex Properties, McLean, VA' -> (company, the place written after it)."""
    parts = [x.strip() for x in str(raw or "").strip().rstrip(".").split(",")]
    if len(parts) > 1 and country(", ".join(parts[1:])):
        return parts[0], ", ".join(parts[1:])
    return bank._company(raw)


def _key(company: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", re.sub(r"(?i)\b(inc|llc|ltd|corp|corporation|company|co|pvt|private|limited)\b", "",
                                           split_company(company)[0])).lower()


def job_rows(jobs: list[dict], base: list[dict] | None = None) -> list[dict]:
    """The form's rows for the resume's jobs. `base`: the user's own work history (the base resume / Answers page), which
    says where each employer really is: the tailored copy may carry the target job's city on every job."""
    own = {_key(b.get("company")): b for b in base or [] if b.get("company")}
    out = []
    for i, job in enumerate(jobs):
        company, where = split_company(job.get("company"))
        start, end, current = bank._span(job.get("dates"))
        mine = own.get(_key(job.get("company"))) or (base[i] if base and len(base) == len(jobs) and i < len(base) else None)
        where = country((mine or {}).get("location")) or country(where) or (mine or {}).get("location") or ""
        out.append({"title": str(job.get("title") or "").strip(), "company": company, "location": where,
                    "country": country(where) or where,
                    "start": start, "end": "" if current else end, "current": current,
                    "description": description(job.get("bullets"))})
    return out


_STATE = re.compile(r"^[A-Z]{2}$")             # "MO"


def school_place(raw: str | None) -> dict:
    """GOAL30 §0: where a school is, from the base resume's "Lakeview State University, Springfield, IL, USA":
    {'location': 'Springfield, IL', 'city': 'Springfield', 'state': 'Missouri', 'country': 'United States'}; an
    Indian college keeps its own city and India. {} when the resume names no place."""
    from .. import resume_place
    parts = [x.strip() for x in str(raw or "").split(",")[1:] if x.strip()]
    if not parts:
        return {}
    land = country(", ".join(parts))
    city = parts[0] if not country(parts[0]) or len(parts) > 1 else ""
    codes = [x.upper() if _STATE.match(x.upper()) and x.upper() in resume_place.STATE_CODES.values()
             else resume_place.STATE_CODES.get(x.lower(), "") for x in parts[1:]]
    code = next((c for c in codes if c), "")
    state = bank.STATE_NAMES.get(code, "")
    where = f"{city}, {code}" if city and code else ", ".join(x for x in (city, land) if x)
    return {k: v for k, v in (("location", where), ("city", city), ("state", state), ("country", land)) if v}


def _schools(pid: str, saved: list[dict]) -> list[dict]:
    """The user's schools (Answers page, else the base resume), each with its own place from the base resume."""
    try:
        import json
        from .. import config
        raw = [e.get("school") for e in json.loads((config.PROFILES / pid / "baseline.json").read_text()).get("education") or []]
    except (OSError, ValueError):
        raw = []
    places = {_key(str(r).split(",")[0]): school_place(r) for r in raw if r}
    schools = saved or bank.history_from_resume(pid)[1]
    return [{**places.get(_key(str(s.get("school") or "")), {}), **{k: v for k, v in s.items() if v not in ("", None)}}
            for s in schools]                           # a blank saved place never hides the resume's


def for_job(conn, pid: str, choice: dict) -> dict:
    """{'summary', 'jobs', 'schools'} for this job's form."""
    data = resume_data(pid, choice)
    saved = bank.history(conn, pid)
    base = saved["jobs"] or bank.history_from_resume(pid)[0]          # where each employer really is
    jobs = job_rows(data.get("jobs") or [], base) or [{**j, "location": country(j.get("location")) or j.get("location") or "",
                                                       "description": ""} for j in saved["jobs"]]   # no jobs read: the user's list
    schools = _schools(pid, saved["schools"])
    return {"summary": " ".join(str(data.get("summary") or "").split()), "jobs": jobs, "schools": schools}
