"""Breezy HR career sites (`<company>.breezy.hr`, GOAL16 Tier A: US SMB). `/json` lists every open position (id,
title, published date, locations, type, salary text; no description), one request for the whole board, so an absent
job counts as a miss. A title-matched position that is new or changed costs one request for its page, whose
schema.org JobPosting carries the description. Tenant robots.txt allows both (checked 2026-10-08); every tenant shares
one rate bucket (net.SHARED_BUCKETS).

Key '<company>' (the subdomain).
"""
from .. import signals
from ..net import HttpError, get_json, get_text
from ..text import html_to_text
from . import fulllist, jsonld
from .common import Ctx, FetchResult, SchemaDrift, make_job, parse_iso, require_list, schema_failure


def list_url(key: str) -> str:
    return f"https://{key}.breezy.hr/json"


def positions(key: str) -> list[dict]:
    return [p for p in require_list(get_json(list_url(key))) if isinstance(p, dict) and p.get("id")]


def _place(loc: dict) -> tuple[str, str | None]:
    country = (loc.get("country") or {}).get("id") if isinstance(loc.get("country"), dict) else loc.get("country")
    state = (loc.get("state") or {}).get("id") if isinstance(loc.get("state"), dict) else loc.get("state")
    text = loc.get("name") or ", ".join(str(p) for p in (loc.get("city"), state) if p)
    if country and country not in text:
        text = f"{text}, {country}" if text else str(country)
    return text, country


def location(post: dict) -> tuple[str, str | None]:
    places = [_place(loc) for loc in post.get("locations") or [post.get("location") or {}] if isinstance(loc, dict)]
    countries = {c for _, c in places if c}
    return "; ".join(dict.fromkeys(t for t, _ in places if t)), countries.pop() if len(countries) == 1 else None


def _remote(post: dict) -> bool:
    return any(isinstance(loc, dict) and loc.get("is_remote")
               for loc in post.get("locations") or [post.get("location") or {}])


def _detail(post: dict) -> dict | None:
    return jsonld.job_posting(get_text(post["url"]))


def _normalize(board: dict, post: dict, posting: dict | None) -> dict:
    posting = posting or {}
    place, country = location(post)
    when = parse_iso(post.get("published_date"))
    return make_job(
        ats="breezy", key=board["key"], ext_id=post["id"], company=board["company"], title=post.get("name") or "",
        location=place, country=country, workplace="remote" if _remote(post) else None, url=post["url"],
        apply_url=f"{post['url']}/apply", team=(post.get("department") or None), salary_text=post.get("salary"),
        posted_at=when, precision="exact" if when else "unknown", description=html_to_text(posting.get("description")),
        employment=(post.get("type") or {}).get("name") if isinstance(post.get("type"), dict) else post.get("type"),
    )


def fetch(board: dict, ctx: Ctx) -> FetchResult:
    try:
        listed = [p for p in positions(board["key"]) if p.get("url")]
    except HttpError as err:
        return FetchResult([], err.status, error=str(err))
    except SchemaDrift as err:
        return schema_failure(err)
    return fulllist.run(board, ctx, listed, id_of=lambda p: str(p["id"]), title_of=lambda p: p.get("name") or "",
                        normalize=_normalize, detail=_detail,
                        wants_detail=lambda p: signals.is_us(*location(p)) is not False)


def survey(key: str) -> dict:
    listed = positions(key)
    name = next(((p.get("company") or {}).get("name") for p in listed if (p.get("company") or {}).get("name")), None)
    return {"n_jobs": len(listed), "name": name,
            "rows": [(parse_iso(p.get("published_date")), *location(p), p.get("name") or "") for p in listed]}
