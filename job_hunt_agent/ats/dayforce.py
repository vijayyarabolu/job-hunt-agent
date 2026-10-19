"""Dayforce (Ceridian) candidate portals (`jobs.dayforcehcm.com/<culture>/<tenant>/<site>`, GOAL16 Tier B: banks,
credit unions, healthcare, retail). jobs.dayforcehcm.com/robots.txt carries only Cloudflare content-signal comments
(no rules, checked 2026-10-08); the per-request gate still checks every URL.

Key '<tenant>/<site>' (entry: token = tenant, site = the job board code, CANDIDATEPORTAL on legacy portals). The
portal's own search: GET /api/auth/csrf gives a token and its cookie pair, then POST
/api/geo/<tenant>/jobposting/search with X-CSRF-TOKEN returns 25 postings a page (paginationStart offset) with the
full description, start date and locations. Reading every page to maxCount is the full list, so an absent job counts
as a miss (cut at MAX_PAGES: truncated, no misses). A site that answers 400 for en-US is asked once in en-GB.
"""
from urllib.parse import quote

from ..net import HttpError, get_json_with_cookies, post_json
from ..text import html_to_text
from . import fulllist
from .common import Ctx, FetchResult, SchemaDrift, make_job, parse_iso, require_list, schema_failure

HOST = "https://jobs.dayforcehcm.com"
PER_PAGE = 25
MAX_PAGES = 40
CULTURES = ("en-US", "en-GB")


def split_key(key: str) -> tuple[str, str]:
    tenant, site = key.split("/", 1)
    return tenant, site


def search_url(tenant: str) -> str:
    return f"{HOST}/api/geo/{quote(tenant)}/jobposting/search"


def job_url(key: str, posting_id, culture: str = "en-US") -> str:
    tenant, site = split_key(key)
    return f"{HOST}/{culture}/{quote(tenant)}/{quote(site)}/jobs/{posting_id}"


def _session() -> dict:
    payload, cookies = get_json_with_cookies(f"{HOST}/api/auth/csrf")
    token = payload.get("csrfToken") if isinstance(payload, dict) else None
    if not token:
        raise SchemaDrift("no csrfToken from Dayforce")
    return {"X-CSRF-TOKEN": token, "Cookie": "; ".join(cookies)}


def _page(tenant: str, site: str, culture: str, start: int, headers: dict) -> dict:
    body = {"clientNamespace": tenant, "jobBoardCode": site, "cultureCode": culture, "paginationStart": start}
    payload = post_json(search_url(tenant), body, headers)
    if not isinstance(payload, dict):
        raise SchemaDrift("expected an object from the Dayforce search")
    return payload


def listing(key: str) -> tuple[list[dict], int, bool, str]:
    """(postings, maxCount, truncated, culture)."""
    tenant, site = split_key(key)
    headers = _session()
    culture, first = CULTURES[0], None
    for culture in CULTURES:
        try:
            first = _page(tenant, site, culture, 0, headers)
            break
        except HttpError as err:
            if err.status != 400 or culture == CULTURES[-1]:
                raise
    found = {str(p["jobPostingId"]): p for p in _postings(first)}
    total = int(first.get("maxCount") or len(found))
    for page in range(1, MAX_PAGES):
        if len(found) >= total:
            return list(found.values()), total, False, culture
        batch = _postings(_page(tenant, site, culture, page * PER_PAGE, headers))
        before = len(found)
        found.update({str(p["jobPostingId"]): p for p in batch})
        if not batch or len(found) == before:
            return list(found.values()), total, False, culture
    return list(found.values()), total, len(found) < total, culture


def _postings(payload: dict) -> list[dict]:
    return [p for p in require_list(payload, "jobPostings") if isinstance(p, dict) and p.get("jobPostingId")]


def places(posting: dict) -> list[tuple[str, str | None]]:
    out = []
    for loc in posting.get("postingLocations") or []:
        if isinstance(loc, dict):
            text = loc.get("formattedAddress") or ", ".join(
                str(p) for p in (loc.get("cityName"), loc.get("stateCode"), loc.get("isoCountryCode")) if p)
            out.append((text, loc.get("isoCountryCode")))
    return out


def location(posting: dict) -> tuple[str, str | None]:
    found = places(posting)
    countries = {c for _, c in found if c}
    return "; ".join(dict.fromkeys(t for t, _ in found if t)), countries.pop() if len(countries) == 1 else None


def _normalize(board: dict, posting: dict, _detail) -> dict:
    place, country = location(posting)
    url = job_url(board["key"], posting["jobPostingId"], board.get("_culture") or "en-US")
    remote = str(posting.get("hasVirtualLocation")).lower() == "true"
    return make_job(
        ats="dayforce", key=board["key"], ext_id=posting["jobPostingId"], company=board["company"],
        title=posting.get("jobTitle") or "", location=place or ("Remote" if remote else ""), country=country,
        workplace="remote" if remote else None, url=url, apply_url=url, req_id=str(posting.get("jobReqId") or ""),
        posted_at=parse_iso(posting.get("postingStartTimestampUTC")),
        precision="exact" if posting.get("postingStartTimestampUTC") else "unknown",
        description=html_to_text(posting.get("jobDescription")),
    )


def fetch(board: dict, ctx: Ctx) -> FetchResult:
    try:
        listed, total, truncated, culture = listing(board["key"])
    except HttpError as err:
        return FetchResult([], err.status, error=str(err))
    except SchemaDrift as err:
        return schema_failure(err)
    notes = [f"truncated at {len(listed)} of {total} postings"] if truncated else []
    return fulllist.run({**board, "_culture": culture}, ctx, listed, id_of=lambda p: str(p["jobPostingId"]),
                        title_of=lambda p: p.get("jobTitle") or "", normalize=_normalize, detail=None,
                        truncated=truncated, notes=notes)


def survey(key: str) -> dict:
    listed, total, _, _ = listing(key)
    return {"n_jobs": total or len(listed),
            "rows": [(parse_iso(p.get("postingStartTimestampUTC")), *location(p), p.get("jobTitle") or "")
                     for p in listed]}
