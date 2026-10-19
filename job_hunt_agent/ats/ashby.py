"""Ashby posting API adapter: GET api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true."""
from ..net import HttpError, get_json_cond
from .common import Ctx, FetchResult, SchemaDrift, fast_item, raw_fp, require_list, schema_failure, make_job, parse_iso

API = "https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true"


def _country(job: dict) -> str | None:
    return (((job.get("address") or {}).get("postalAddress") or {}).get("addressCountry")) or None


def _normalize(board: dict, job: dict) -> dict:
    comp = job.get("compensation") or {}
    salary_text = comp.get("scrapeableCompensationSalarySummary") or comp.get("compensationTierSummary")
    secondary = [s.get("location", "") for s in job.get("secondaryLocations") or [] if isinstance(s, dict)]
    location = "; ".join([job.get("location") or ""] + [s for s in secondary if s])
    return make_job(
        ats="ashby", key=board["key"], ext_id=job["id"], company=board["company"], title=job["title"],
        location=location, country=_country(job),
        workplace=job.get("workplaceType") or ("remote" if job.get("isRemote") else None),
        url=job.get("jobUrl", ""), apply_url=job.get("applyUrl", ""),
        team=job.get("team") or job.get("department"), salary_text=salary_text,
        posted_at=parse_iso(job.get("publishedAt")), precision="exact",
        description=job.get("descriptionPlain") or "", employment=job.get("employmentType"),
    )


def _item(board: dict, job: dict, ctx: Ctx) -> dict:
    fp = raw_fp(job)
    if ctx.unchanged(f"ashby:{board['key']}:{job['id']}", fp):
        return fast_item("ashby", board["key"], job["id"], job.get("title", ""), fp)
    return {**_normalize(board, job), "raw_fp": fp}


def fetch(board: dict, ctx: Ctx) -> FetchResult:
    try:
        resp, payload = get_json_cond(API.format(token=board["key"]), board.get("etag"), board.get("last_mod"))
    except HttpError as err:
        return FetchResult([], err.status, error=str(err))
    if resp.status == 304:
        return FetchResult([], 304, not_modified=True, etag=resp.etag, last_mod=resp.last_mod)
    try:
        raw = [j for j in require_list(payload, "jobs") if j.get("isListed") is not False]
    except SchemaDrift as err:
        return schema_failure(err)
    items, bad = [], 0
    for job in raw:
        try:
            items.append(_item(board, job, ctx))
        except (KeyError, TypeError, ValueError, AttributeError):
            bad += 1
    return FetchResult(items, 200, n_raw=len(raw), n_parse_err=bad, etag=resp.etag, last_mod=resp.last_mod)
