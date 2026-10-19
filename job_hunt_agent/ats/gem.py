"""Gem job boards (`jobs.gem.com/<vanity>`, GOAL16 Tier A). Gem's documented public job board API (help.gem.com "The
job board API") answers `api.gem.com/job_board/v0/<vanity>/job_posts/` with every posting in a Greenhouse-like shape,
the description included, so a board is one request and an absent job counts as a miss. api.gem.com/robots.txt
answers 403 (RFC 9309: unavailable = no rules); the per-request gate still checks every URL.

Key '<vanity>' (the path slug of jobs.gem.com). Dates: first_published_at (exact), else created_at.
"""
from ..net import HttpError, get_json
from ..text import html_to_text
from . import fulllist
from .common import Ctx, FetchResult, SchemaDrift, make_job, parse_iso, require_list, schema_failure

API = "https://api.gem.com/job_board/v0/{key}/job_posts/"
BOARD = "https://jobs.gem.com/{key}"
_WORKPLACE = {"in_office": "onsite", "hybrid": "hybrid", "remote": "remote"}


def list_url(key: str) -> str:
    return API.format(key=key)


def postings(key: str) -> list[dict]:
    return [p for p in require_list(get_json(list_url(key))) if isinstance(p, dict)]


def _location(post: dict) -> str:
    places = [(o.get("location") or {}).get("name") for o in post.get("offices") or [] if isinstance(o, dict)]
    places = [p for p in places if p] or [(post.get("location") or {}).get("name") or ""]
    return "; ".join(dict.fromkeys(places))


def posted(post: dict) -> tuple[str | None, str]:
    first = parse_iso(post.get("first_published_at"))
    return (first, "exact") if first else (parse_iso(post.get("created_at")), "updated")


def _normalize(board: dict, post: dict, _detail) -> dict:
    when, precision = posted(post)
    departments = post.get("departments") or []
    url = post.get("absolute_url") or BOARD.format(key=board["key"])
    return make_job(
        ats="gem", key=board["key"], ext_id=post["id"], company=board["company"], title=post["title"],
        location=_location(post), workplace=_WORKPLACE.get(post.get("location_type") or ""), url=url, apply_url=url,
        req_id=post.get("requisition_id"), team=departments[0].get("name") if departments else None,
        posted_at=when, precision=precision,
        description=post.get("content_plain") or html_to_text(post.get("content")),
        employment=post.get("employment_type"),
    )


def fetch(board: dict, ctx: Ctx) -> FetchResult:
    try:
        listed = postings(board["key"])
    except HttpError as err:
        return FetchResult([], err.status, error=str(err))
    except SchemaDrift as err:
        return schema_failure(err)
    return fulllist.run(board, ctx, listed, id_of=lambda p: str(p["id"]), title_of=lambda p: p.get("title") or "",
                        normalize=_normalize, detail=None)


def survey(key: str) -> dict:
    """Discovery: every open posting as (posted, location, country, title); the API names no company."""
    listed = postings(key)
    return {"n_jobs": len(listed), "rows": [(posted(p)[0], _location(p), None, p.get("title") or "") for p in listed]}
