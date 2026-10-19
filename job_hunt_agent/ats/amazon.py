"""Amazon adapter (decision 005: amazon.jobs search.json, verified live 2026-10-02; robots allows /en/search.json).

One board ('amazon'). Like Workday the result set is a search: one request per search term, US only, newest first
(sort=recent, top RESULTS per term). Absence is never a miss; unseen 7 days = stale (store treats it like Workday).
posted_date is a calendar day ('October  2, 2026'): day precision.
"""
from datetime import datetime
from urllib.parse import quote

from ..net import HttpError, get_json
from .common import Ctx, FetchResult, SchemaDrift, day_iso, fast_item, make_job, raw_fp, require_list, schema_failure
from ..text import html_to_text

SEARCH = ("https://www.amazon.jobs/en/search.json?country=USA&result_limit={n}&offset=0&sort=recent"
          "&base_query={q}")
RESULTS = 100
FP_SALT = "text-2"                # bump when _normalize changes, so stored postings are re-normalized once
BASE = "https://www.amazon.jobs"


def _posted(text: str | None) -> str | None:
    try:
        return day_iso(datetime.strptime(" ".join((text or "").split()), "%B %d, %Y").date())
    except ValueError:
        return None


def _normalize(board: dict, job: dict, scopes: set[str]) -> dict:
    description = "\n\n".join(x for x in (                 # headings let the extractor weigh preferred terms half
        html_to_text(job.get("description")),             # the fields carry <br/> and entities
        f"Basic Qualifications\n{html_to_text(job['basic_qualifications'])}" if job.get("basic_qualifications")
        else None,
        f"Preferred Qualifications\n{html_to_text(job['preferred_qualifications'])}"
        if job.get("preferred_qualifications") else None)
        if x)
    item = make_job(
        ats="amazon", key=board["key"], ext_id=job["id_icims"], company=board["company"], title=job["title"],
        location=job.get("normalized_location") or job.get("location") or "", country="US",
        url=f"{BASE}{job.get('job_path', '')}", apply_url=job.get("url_next_step") or f"{BASE}{job.get('job_path', '')}",
        req_id=job.get("id_icims"), team=job.get("job_category"), posted_at=_posted(job.get("posted_date")),
        precision="day", description=description, employment=job.get("job_schedule_type"),
    )
    return {**item, "scopes": sorted(scopes)}


def fetch(board: dict, ctx: Ctx) -> FetchResult:
    seen: dict[str, dict] = {}
    found_by: dict[str, set[str]] = {}
    try:
        for term in ctx.search_terms:
            payload = get_json(SEARCH.format(n=RESULTS, q=quote(term)))
            for job in require_list(payload, "jobs"):
                key = str(job.get("id_icims") or job.get("id"))
                seen.setdefault(key, job)
                found_by.setdefault(key, set()).add(f"term:{term}")
    except HttpError as err:
        return FetchResult([], err.status, error=str(err))
    except SchemaDrift as err:
        return schema_failure(err)
    items, bad = [], 0
    for key, job in seen.items():
        try:
            fp = raw_fp(job, FP_SALT)
            uid = f"amazon:{board['key']}:{job['id_icims']}"
            items.append(fast_item("amazon", board["key"], job["id_icims"], job.get("title", ""), fp)
                         if ctx.unchanged(uid, fp) else {**_normalize(board, job, found_by[key]), "raw_fp": fp})
        except (KeyError, TypeError, ValueError, AttributeError):
            bad += 1
    return FetchResult(items, 200, n_raw=len(seen), n_parse_err=bad)
