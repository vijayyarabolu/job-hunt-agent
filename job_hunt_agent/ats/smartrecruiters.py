"""SmartRecruiters adapter: paginated postings list, detail GET only for title-matched jobs."""
from ..net import HttpError, get_json
from ..text import html_to_text
from .common import Ctx, FetchResult, SchemaDrift, require_list, schema_failure, make_job, parse_iso

API = "https://api.smartrecruiters.com/v1/companies/{company}/postings"
PAGE_SIZE = 100
MAX_PAGES = 20


def _location(loc: dict) -> str:
    parts = [loc.get("city"), loc.get("region"), (loc.get("country") or "").upper()]
    return ", ".join(p for p in parts if p)


def _workplace(loc: dict) -> str:
    return "remote" if loc.get("remote") else "hybrid" if loc.get("hybrid") else "onsite"


def _detail(company: str, posting_id: str) -> dict:
    return get_json(f"{API.format(company=company)}/{posting_id}")


def _description(detail: dict) -> str:
    sections = ((detail.get("jobAd") or {}).get("sections") or {}).values()
    return "\n\n".join(
        f"{s.get('title', '')}\n{html_to_text(s.get('text'))}".strip() for s in sections if isinstance(s, dict)
    )


def _normalize(board: dict, job: dict, detail: dict | None) -> dict:
    loc = job.get("location") or {}
    company = board["key"]
    default_url = f"https://jobs.smartrecruiters.com/{company}/{job['id']}"
    detail = detail or {}
    return make_job(
        ats="smartrecruiters", key=company, ext_id=job["id"], company=board["company"], title=job["name"],
        location=_location(loc), country=loc.get("country"), workplace=_workplace(loc),
        url=detail.get("postingUrl") or default_url, apply_url=detail.get("applyUrl") or default_url,
        req_id=job.get("refNumber"), team=(job.get("department") or {}).get("label"),
        posted_at=parse_iso(job.get("releasedDate")), precision="exact",
        description=_description(detail) if detail else "",
        employment=(job.get("typeOfEmployment") or {}).get("label"),
    )


def _list_all(company: str) -> tuple[list[dict], bool]:
    raw: list[dict] = []
    total = 0
    for page in range(MAX_PAGES):
        payload = get_json(f"{API.format(company=company)}?q=&limit={PAGE_SIZE}&offset={page * PAGE_SIZE}")
        content = require_list(payload, "content")
        raw.extend(content)
        total = payload.get("totalFound") or 0
        if len(content) < PAGE_SIZE or len(raw) >= total:
            break
    return raw, len(raw) < total


def fetch(board: dict, ctx: Ctx) -> FetchResult:
    company = board["key"]
    try:
        raw, truncated = _list_all(company)
    except HttpError as err:
        return FetchResult([], err.status, error=str(err))
    except SchemaDrift as err:
        return schema_failure(err)
    items, bad, notes = [], 0, []
    for job in raw:
        try:
            uid = f"smartrecruiters:{company}:{job['id']}"
            detail = None
            if ctx.matches(job.get("name", "")) and ctx.needs_detail(uid):
                try:
                    detail = _detail(company, job["id"])
                except HttpError as err:
                    notes.append(f"detail {job['id']}: {err}")
            items.append(_normalize(board, job, detail))
        except (KeyError, TypeError, ValueError, AttributeError):
            bad += 1
    if truncated:
        notes.append(f"truncated at {len(raw)} postings")
    return FetchResult(items, 200, n_raw=len(raw), n_parse_err=bad, notes=notes, truncated=truncated)
