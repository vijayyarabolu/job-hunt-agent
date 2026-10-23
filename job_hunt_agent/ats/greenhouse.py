"""Greenhouse job board adapter (decision 005 B2): list WITHOUT content (conditional GET, 304 = unchanged), then
`/jobs/{id}` for the content of title-matched jobs whose JD we do not have yet (at most MAX_DETAILS per run; the
rest follow on the next runs). GOAL32: with more of those than MAX_DETAILS (Speechify posted one role in 740 cities and
had 25 JDs after a day), one `?content=true` request brings every JD at once."""
from ..net import HttpError, get_json, get_json_cond
from ..text import html_to_text
from .common import (Ctx, FetchResult, SchemaDrift, fast_item, make_job, parse_iso, raw_fp, require_list,
                     schema_failure)

LIST = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
DETAIL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{id}"
API = LIST + "?content=true"            # kept for callers that want the whole board in one request
MAX_DETAILS = 25


def _location_type(job: dict) -> str | None:
    for meta in job.get("metadata") or []:
        if (meta.get("name") or "").lower() == "location type" and isinstance(meta.get("value"), str):
            return meta["value"]
    return None


def _metadata(job: dict, word: str) -> str | None:
    """A custom-field value whose name mentions `word` (boards add e.g. 'Employment Type'); None when absent."""
    for meta in job.get("metadata") or []:
        if word in (meta.get("name") or "").lower() and isinstance(meta.get("value"), str):
            return meta["value"]
    return None


def _normalize(board: dict, job: dict, content: str | None = None) -> dict:
    first = parse_iso(job.get("first_published"))
    posted, precision = (first, "exact") if first else (parse_iso(job.get("updated_at")), "updated")
    departments = job.get("departments") or []
    return make_job(
        ats="greenhouse", key=board["key"], ext_id=job["id"], company=board["company"],
        title=job["title"], location=(job.get("location") or {}).get("name", ""),
        workplace=_location_type(job), url=job.get("absolute_url", ""), apply_url=job.get("absolute_url", ""),
        req_id=job.get("requisition_id"), team=departments[0].get("name") if departments else None,
        posted_at=posted, precision=precision,
        description=html_to_text(content if content is not None else job.get("content"), double_escaped=True),
        employment=_metadata(job, "employment"),
    )


def _detail(board: dict, job: dict) -> dict | None:
    try:
        return get_json(DETAIL.format(token=board["key"], id=job["id"])) or None
    except HttpError:
        return None                                   # no JD this run; needs_detail stays true for the next


def _uid(board: dict, job: dict) -> str:
    return f"greenhouse:{board['key']}:{job['id']}"


def _contents(board: dict, raw: list, ctx: Ctx) -> dict:
    """{job id: content} from one `?content=true` request when more matched jobs lack a JD than MAX_DETAILS; {} when
    they fit the budget or the request fails (the per-job details go on as before)."""
    wanting = sum(1 for job in raw if ctx.matches(job.get("title", "")) and ctx.needs_detail(_uid(board, job)))
    if wanting <= MAX_DETAILS:
        return {}
    try:
        payload = get_json(API.format(token=board["key"])) or {}
    except HttpError:
        return {}
    return {job.get("id"): job["content"] for job in payload.get("jobs") or [] if isinstance(job, dict) and job.get("content")}


def _item(board: dict, job: dict, ctx: Ctx, budget: list[int], contents: dict) -> dict:
    fp = raw_fp(job)
    uid = _uid(board, job)
    wanted = ctx.matches(job.get("title", "")) and ctx.needs_detail(uid)
    wants = wanted and (job["id"] in contents or budget[0] > 0)
    if ctx.unchanged(uid, fp) and not wants:          # an unchanged match still without its JD asks again
        return fast_item("greenhouse", board["key"], job["id"], job.get("title", ""), fp)
    detail = None
    if wants and job["id"] in contents:
        detail = {"content": contents[job["id"]]}
    elif wants:
        budget[0] -= 1
        detail = _detail(board, job)
    merged = {**job, **(detail or {})}
    return {**_normalize(board, merged, (detail or {}).get("content")), "raw_fp": fp}


def fetch(board: dict, ctx: Ctx) -> FetchResult:
    try:
        resp, payload = get_json_cond(LIST.format(token=board["key"]), board.get("etag"), board.get("last_mod"))
    except HttpError as err:
        return FetchResult([], err.status, error=str(err))
    if resp.status == 304:
        return FetchResult([], 304, not_modified=True, etag=resp.etag, last_mod=resp.last_mod)
    try:
        raw = require_list(payload, "jobs")
    except SchemaDrift as err:
        return schema_failure(err)
    items, bad, budget = [], 0, [MAX_DETAILS]
    contents = _contents(board, raw, ctx)
    for job in raw:
        try:
            items.append(_item(board, job, ctx, budget, contents))
        except (KeyError, TypeError, ValueError, AttributeError):
            bad += 1
    return FetchResult(items, 200, n_raw=len(raw), n_parse_err=bad, etag=resp.etag, last_mod=resp.last_mod)


def board_name(token: str) -> str | None:
    """The board's own display name (used by discover to catch wrong-company slugs)."""
    try:
        return (get_json(f"https://boards-api.greenhouse.io/v1/boards/{token}") or {}).get("name")
    except HttpError:
        return None
