"""Lever postings adapter: GET api.lever.co/v0/postings/{token}?mode=json."""
from ..net import HttpError, get_json_cond
from ..text import html_to_text
from .common import Ctx, FetchResult, SchemaDrift, fast_item, raw_fp, require_list, schema_failure, from_epoch_ms, make_job

API = "https://api.lever.co/v0/postings/{token}?mode=json"


def _description(job: dict) -> str:
    parts = [job.get("descriptionPlain") or ""]
    for block in job.get("lists") or []:
        parts.append(f"{block.get('text', '')}\n{html_to_text(block.get('content'))}")
    parts.append(job.get("additionalPlain") or "")
    return "\n\n".join(p.strip() for p in parts if p and p.strip())


def _salary(job: dict) -> tuple | None:
    rng = job.get("salaryRange") or {}
    if rng.get("min") and rng.get("max") and rng.get("interval", "per-year-salary") == "per-year-salary":
        return float(rng["min"]), float(rng["max"]), rng.get("currency") or "USD"
    return None


def _normalize(board: dict, job: dict) -> dict:
    cats = job.get("categories") or {}
    return make_job(
        ats="lever", key=board["key"], ext_id=job["id"], company=board["company"], title=job["text"],
        location=cats.get("location") or ", ".join(cats.get("allLocations") or []),
        country=job.get("country"), workplace=job.get("workplaceType"),
        url=job.get("hostedUrl", ""), apply_url=job.get("applyUrl", ""), team=cats.get("team"),
        salary=_salary(job), posted_at=from_epoch_ms(job.get("createdAt")), precision="exact",
        description=_description(job), employment=cats.get("commitment"),
    )


def _item(board: dict, job: dict, ctx: Ctx) -> dict:
    fp = raw_fp(job)
    if ctx.unchanged(f"lever:{board['key']}:{job['id']}", fp):
        return fast_item("lever", board["key"], job["id"], job.get("text", ""), fp)
    return {**_normalize(board, job), "raw_fp": fp}


def fetch(board: dict, ctx: Ctx) -> FetchResult:
    """Conditional GET (Lever sends ETags but no gzip, so a 304 saves megabytes on big boards)."""
    try:
        resp, raw = get_json_cond(API.format(token=board["key"]), board.get("etag"), board.get("last_mod"))
    except HttpError as err:
        return FetchResult([], err.status, error=str(err))
    if resp.status == 304:
        return FetchResult([], 304, not_modified=True, etag=resp.etag, last_mod=resp.last_mod)
    try:
        raw = require_list(raw)
    except SchemaDrift as err:
        return schema_failure(err)
    items, bad = [], 0
    for job in raw:
        try:
            items.append(_item(board, job, ctx))
        except (KeyError, TypeError, ValueError, AttributeError):
            bad += 1
    return FetchResult(items, 200, n_raw=len(raw), n_parse_err=bad, etag=resp.etag, last_mod=resp.last_mod)
