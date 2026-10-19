"""Phenom adapter (decision 011). The career site's own widget JSON (POST /widgets), undocumented but public;
robots on every tenant checked leaves /widgets allowed, and Phenom's terms exclude customer career sites.

Key '<host>/<refNum>/<pageId>/<site>': refNum and pageId come from the tenant's search-results page (phApp.ddo), site
is its path country ('us' or 'global'). Many tenants only front Workday (their applyUrl is a myworkdayjobs.com link);
those are registered as Workday boards instead, so a Phenom board is one whose applications go elsewhere (e.g. Taleo).
A search: one keyword query per term, up to MAX_PAGES pages of PAGE; Phenom's 'Most recent' sort is not reliable, so
the cap is per relevance. Non-US postings are dropped (the country value differs per tenant). Absence is never a miss.
"""
from datetime import datetime
from typing import Iterator

from .. import signals
from ..net import post_json
from ..text import html_to_text
from . import search
from .common import Ctx, FetchResult, SchemaDrift, iso_utc, make_job, require_list

PAGE = 50
MAX_PAGES = 2
LANG = "en_us"


def split_key(key: str) -> tuple[str, str, str, str]:
    host, ref, page_id, site = key.split("/")
    return host, ref, page_id, site


def _when(text: str | None) -> str | None:
    try:
        return iso_utc(datetime.fromisoformat(text)) if text else None
    except ValueError:
        return None


def _body(ref: str, page_id: str, site: str, term: str, start: int) -> dict:
    return {"lang": LANG, "deviceType": "desktop", "country": site, "pageName": "search-results",
            "ddoKey": "refineSearch", "sortBy": "", "subsearch": "", "from": start, "size": PAGE, "jobs": True,
            "counts": False, "all_fields": [], "clearAll": False, "jdsource": "facets", "isSliderEnable": False,
            "pageId": page_id, "siteType": "external", "keywords": term, "global": True, "selected_fields": {},
            "locationData": {}, "refNum": ref}


def _pages(key: str, term: str) -> Iterator[dict]:
    host, ref, page_id, site = split_key(key)
    for page in range(MAX_PAGES):
        payload = post_json(f"https://{host}/widgets", _body(ref, page_id, site, term, page * PAGE))
        found = payload.get("refineSearch") if isinstance(payload, dict) else None
        if not isinstance(found, dict) or not isinstance(found.get("data"), dict):
            raise SchemaDrift("expected refineSearch.data")
        jobs = require_list(found["data"], "jobs")
        yield from jobs
        if len(jobs) < PAGE or (page + 1) * PAGE >= (found.get("totalHits") or 0):
            return


def _detail(key: str, posting: dict) -> str | None:
    host, ref, _, site = split_key(key)
    payload = post_json(f"https://{host}/widgets", {
        "lang": LANG, "deviceType": "desktop", "country": site, "pageName": "job", "ddoKey": "jobDetail",
        "jobSeqNo": posting["jobSeqNo"], "refNum": ref, "siteType": "external"})
    return (((payload or {}).get("jobDetail") or {}).get("data") or {}).get("job", {}).get("description")


def _location(posting: dict) -> str:
    return "; ".join(posting.get("multi_location") or []) or posting.get("location") or ""


def _is_us(posting: dict) -> bool:
    return signals.is_us(_location(posting), posting.get("country")) is not False


def _normalize(board: dict, posting: dict, description: str | None) -> dict:
    host, _, _, site = split_key(board["key"])
    url = f"https://{host}/{site}/{LANG.split('_')[0]}/job/{posting['jobId']}"
    return make_job(
        ats="phenom", key=board["key"], ext_id=str(posting["jobId"]), company=board["company"],
        title=posting.get("title") or "", location=_location(posting), country=posting.get("country"), url=url,
        apply_url=posting.get("applyUrl") or url, req_id=posting.get("reqId"), team=posting.get("category"),
        posted_at=_when(posting.get("postedDate") or posting.get("dateCreated")),
        description=html_to_text(description) if description else "", employment=posting.get("type"),
    )


def fetch(board: dict, ctx: Ctx) -> FetchResult:
    key = board["key"]
    return search.run(board, ctx, lambda term: _pages(key, term), id_of=lambda p: str(p["jobId"]),
                      title_of=lambda p: p.get("title") or "", normalize=_normalize,
                      detail=lambda p: _detail(key, p), keep=_is_us)
