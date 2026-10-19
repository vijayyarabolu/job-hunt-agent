"""Rippling ATS job boards (`ats.rippling.com/<slug>/jobs`, GOAL16 Tier A). ats.rippling.com/robots.txt (checked
2026-10-08) disallows only /internal/; the per-request gate still checks every URL.

Key '<slug>'. The legacy v1 list `api/v1/board/<slug>/jobs` is the whole board in one request (uuid, name,
department, url, one work location label), so an absent job counts as a miss. A title-matched posting that is new or
changed costs one request for `api/v1/board/<slug>/jobs/<uuid>`: every work location, the employment type, createdOn
(the posting date) and the description (company + role HTML). Other postings keep the list's fields and no date.
"""
from .. import signals
from ..net import HttpError, get_json
from ..text import html_to_text
from . import fulllist
from .common import Ctx, FetchResult, SchemaDrift, make_job, parse_iso, require_list, schema_failure

BASE = "https://ats.rippling.com/api/v1/board/{key}/jobs"
V2 = "https://ats.rippling.com/api/v2/board/{key}/jobs?page=0"        # discovery: 20 postings with country codes
PAGE = "https://ats.rippling.com/{key}/jobs/{uuid}"


def list_url(key: str) -> str:
    return BASE.format(key=key)


def postings(key: str) -> list[dict]:
    return [p for p in require_list(get_json(list_url(key))) if isinstance(p, dict) and p.get("uuid")]


def detail(key: str, uuid: str) -> dict | None:
    found = get_json(f"{list_url(key)}/{uuid}")
    return found if isinstance(found, dict) and found.get("uuid") else None


def _label(value) -> str:
    return (value.get("label") or value.get("id") or "") if isinstance(value, dict) else str(value or "")


def list_location(post: dict) -> str:
    return _label(post.get("workLocation"))


def _description(found: dict) -> str:
    body = found.get("description")
    if isinstance(body, dict):
        return html_to_text("\n".join(str(body.get(k) or "") for k in ("role", "company")))
    return html_to_text(body)


def _employment(found: dict) -> str | None:
    kind = found.get("employmentType")
    return " ".join(str(kind.get(k) or "") for k in ("id", "label")) if isinstance(kind, dict) else kind


def _normalize(board: dict, post: dict, found: dict | None) -> dict:
    found = found or {}
    places = [str(p) for p in found.get("workLocations") or [] if p]
    url = post.get("url") or PAGE.format(key=board["key"], uuid=post["uuid"])
    when = parse_iso(found.get("createdOn")) if found.get("createdOn") else None
    return make_job(
        ats="rippling", key=board["key"], ext_id=post["uuid"], company=board["company"],
        title=(found.get("name") or post.get("name") or "").strip(),
        location="; ".join(places) or list_location(post), url=url, apply_url=url,
        team=_label(post.get("department")) or None, posted_at=when, precision="exact" if when else "unknown",
        description=_description(found) if found else "", employment=_employment(found),
    )


def fetch(board: dict, ctx: Ctx) -> FetchResult:
    key = board["key"]
    try:
        listed = postings(key)
    except HttpError as err:
        return FetchResult([], err.status, error=str(err))
    except SchemaDrift as err:
        return schema_failure(err)
    return fulllist.run(board, ctx, listed, id_of=lambda p: p["uuid"], title_of=lambda p: (p.get("name") or "").strip(),
                        normalize=_normalize, detail=lambda p: detail(key, p["uuid"]),
                        wants_detail=lambda p: signals.is_us(list_location(p), None) is not False)


def _v2_rows(item: dict) -> list[tuple]:
    places = [p for p in item.get("locations") or [] if isinstance(p, dict)] or [{}]
    return [(None, p.get("name") or "", p.get("countryCode") or p.get("country"), (item.get("name") or "").strip())
            for p in places]


def survey(key: str) -> dict:
    """Discovery: the v2 list's first page (its locations carry country codes; the v1 list's one label, e.g. 'AR',
    does not) and the company name from one posting's page data. No dates: an open posting is current."""
    payload = get_json(V2.format(key=key))
    items = [i for i in require_list(payload, "items") if isinstance(i, dict) and i.get("id")]
    name = None
    if items:
        try:
            name = (detail(key, items[0]["id"]) or {}).get("companyName")
        except HttpError:
            name = None
    return {"n_jobs": int(payload.get("totalItems") or len(items)), "name": name,
            "rows": [row for item in items for row in _v2_rows(item)]}
