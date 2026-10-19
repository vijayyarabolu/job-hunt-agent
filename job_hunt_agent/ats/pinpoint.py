"""Pinpoint career sites (`<company>.pinpointhq.com`, GOAL16 Tier A). `postings.json` is every open posting with its
description sections, pay and location, one request for the whole board, so an absent job counts as a miss. No
posting date in the feed: first-seen freshness. Tenant robots.txt allows it (checked 2026-10-08); every tenant shares
one rate bucket (net.SHARED_BUCKETS).

Key '<company>' (the subdomain).
"""
from ..net import HttpError, get_json
from ..text import html_to_text
from . import fulllist
from .common import Ctx, FetchResult, SchemaDrift, make_job, require_list, schema_failure

SECTIONS = ("description", "key_responsibilities", "skills_knowledge_expertise", "benefits")


def list_url(key: str) -> str:
    return f"https://{key}.pinpointhq.com/postings.json"


def postings(key: str) -> list[dict]:
    return [p for p in require_list(get_json(list_url(key)), "data") if isinstance(p, dict) and p.get("id")]


def location(post: dict) -> str:
    loc = post.get("location") or {}
    return loc.get("name") or ", ".join(str(p) for p in (loc.get("city"), loc.get("province")) if p)


def _salary(post: dict) -> tuple | None:
    low = post.get("compensation_minimum")
    if not isinstance(low, (int, float)) or not low or (post.get("compensation_frequency") or "year") != "year":
        return None
    high = post.get("compensation_maximum")
    return float(low), float(high) if isinstance(high, (int, float)) else None, post.get("compensation_currency") or "USD"


def _normalize(board: dict, post: dict, _detail) -> dict:
    job = post.get("job") or {}
    url = post.get("url") or f"https://{board['key']}.pinpointhq.com{post.get('path') or ''}"
    body = "\n".join(str(post.get(k) or "") for k in SECTIONS)
    return make_job(
        ats="pinpoint", key=board["key"], ext_id=post["id"], company=board["company"], title=post.get("title") or "",
        location=location(post), workplace=post.get("workplace_type") or None, url=url, apply_url=url,
        req_id=job.get("requisition_id") or None, team=(job.get("department") or {}).get("name"),
        salary=_salary(post), posted_at=None, precision="unknown", description=html_to_text(body),
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
    listed = postings(key)
    return {"n_jobs": len(listed), "rows": [(None, location(p), None, p.get("title") or "") for p in listed]}
