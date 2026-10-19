"""Greenhouse job board adapter (decision 005 B2): list WITHOUT content (conditional GET, 304 = unchanged), then
`/jobs/{id}` for the content of title-matched jobs whose JD we do not have yet (at most MAX_DETAILS per run; the
rest follow on the next runs)."""
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


def _item(board: dict, job: dict, ctx: Ctx, budget: list[int]) -> dict:
    fp = raw_fp(job)
    uid = f"greenhouse:{board['key']}:{job['id']}"
    wants = budget[0] > 0 and ctx.matches(job.get("title", "")) and ctx.needs_detail(uid)
    if ctx.unchanged(uid, fp) and not wants:          # an unchanged match still without its JD asks again
        return fast_item("greenhouse", board["key"], job["id"], job.get("title", ""), fp)
    detail = None
    if wants:
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
    for job in raw:
        try:
            items.append(_item(board, job, ctx, budget))
        except (KeyError, TypeError, ValueError, AttributeError):
            bad += 1
    return FetchResult(items, 200, n_raw=len(raw), n_parse_err=bad, etag=resp.etag, last_mod=resp.last_mod)


def board_name(token: str) -> str | None:
    """The board's own display name (used by discover to catch wrong-company slugs)."""
    try:
        return (get_json(f"https://boards-api.greenhouse.io/v1/boards/{token}") or {}).get("name")
    except HttpError:
        return None
