"""IBM careers (GOAL16 Tier C: a big employer on its own career site). careers.ibm.com's job search reads IBM's public
site search, POST www-api.ibm.com/search/api/v2 (appId careers, scope careers2; robots.txt allows it, checked
2026-10-08). The job pages are a script shell with no schema.org data, so the search's own fields are the job.

One board ('ibm'). Like Amazon the result set is a search: one request per search term, US only (the country field
filter), at most RESULTS per term. Absence is never a miss; unseen 7 days = stale. dcdate is a day (day precision);
body is the posting text.
"""
from datetime import date

from ..net import HttpError, post_json
from ..text import html_to_text
from . import search
from .common import Ctx, FetchResult, SchemaDrift, day_iso, make_job

API = "https://www-api.ibm.com/search/api/v2"
RESULTS = 100
SOURCE = ["title", "url", "body", "description", "dcdate", "field_text_01", "field_keyword_05", "field_keyword_08",
          "field_keyword_17", "field_keyword_18", "field_keyword_19"]
_LEVEL = {"internship": "internship"}


def body(term: str, size: int = RESULTS, offset: int = 0) -> dict:
    return {"appId": "careers", "scopes": ["careers2"], "size": size, "from": offset, "lang": "zz",
            "query": {"bool": {"must": [{"simple_query_string": {"query": term, "fields": [
                "title^3", "description^2", "body"]}}],
                "filter": [{"term": {"field_keyword_05": "United States"}}]}},
            "sm": {"query": term, "lang": "zz"}, "_source": SOURCE}


def hits(payload) -> list[dict]:
    found = ((payload or {}).get("hits") or {}).get("hits") if isinstance(payload, dict) else None
    if not isinstance(found, list):
        raise SchemaDrift("expected hits.hits from the IBM search")
    return [h["_source"] for h in found if isinstance(h, dict) and isinstance(h.get("_source"), dict)]


def _pages(term: str) -> list[dict]:
    return hits(post_json(API, body(term)))


def job_id(posting: dict) -> str:
    found = str(posting.get("field_text_01") or "").strip()
    if not found:
        url = str(posting.get("url") or "")
        found = url.rsplit("jobId=", 1)[1] if "jobId=" in url else ""
    if not found:
        raise KeyError("no IBM job id")
    return found


def _day(text) -> str | None:
    try:
        return day_iso(date.fromisoformat(str(text)[:10])) if text else None
    except ValueError:
        return None


def _normalize(board: dict, posting: dict, _description) -> dict:
    place = posting.get("field_keyword_19") or ""
    place = "" if place.lower() == "multiple cities" else place
    level = (posting.get("field_keyword_18") or "").lower()
    url = posting.get("url") or f"https://careers.ibm.com/careers/JobDetail?jobId={job_id(posting)}"
    when = _day(posting.get("dcdate"))
    return make_job(
        ats="ibm", key=board["key"], ext_id=job_id(posting), company=board["company"],
        title=html_to_text(posting.get("title")), location=place or "United States", country="US",
        workplace=posting.get("field_keyword_17") or None, url=url, apply_url=url, team=posting.get("field_keyword_08"),
        posted_at=when, precision="day" if when else "unknown",
        description=html_to_text(posting.get("body") or posting.get("description")),
        employment=_LEVEL.get(level),
    )


def fetch(board: dict, ctx: Ctx) -> FetchResult:
    try:
        return search.run(board, ctx, _pages, id_of=job_id, title_of=lambda p: html_to_text(p.get("title")),
                          normalize=_normalize)
    except HttpError as err:
        return FetchResult([], err.status, error=str(err))
