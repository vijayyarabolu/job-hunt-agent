"""iCIMS adapter for career sites on iCIMS's Jibe front end (decision 011): GET <domain>/api/jobs, the JSON
the site's own search page reads; robots 'Allow: /' with 'crawl-delay: 5' (net.gap_for honors it). Classic
*.icims.com portals have no JSON and stay a gap.

Key '<host>' (the company's careers domain, e.g. www.pepsicojobs.com). A search: one keyword query per term with the
United States country filter, one page of PAGE (relevance order). Descriptions are in the list, so no detail requests.
Absence is never a miss (store.SEARCH_ATS).
"""
from datetime import datetime
from typing import Iterator
from urllib.parse import urlencode

from ..net import get_json
from ..text import html_to_text
from . import search
from .common import Ctx, FetchResult, SchemaDrift, iso_utc, make_job, require_list

PAGE = 100
COUNTRY = "United States"


def _when(text: str | None) -> str | None:
    try:
        return iso_utc(datetime.fromisoformat(text)) if text else None
    except ValueError:
        return None


def _pages(host: str, term: str) -> Iterator[dict]:
    query = urlencode({"keywords": term, "country": COUNTRY, "page": 1, "limit": PAGE})
    for row in require_list(get_json(f"https://{host}/api/jobs?{query}"), "jobs"):
        posting = row.get("data") if isinstance(row, dict) else None
        if not isinstance(posting, dict):
            raise SchemaDrift("expected jobs[].data")
        yield posting


def _normalize(board: dict, posting: dict, _description: str | None) -> dict:
    url = f"https://{board['key']}/jobs/{posting['slug']}"
    body = "\n\n".join(html_to_text(posting.get(k)) for k in ("description", "responsibilities", "qualifications")
                       if posting.get(k))
    return make_job(
        ats="icims", key=board["key"], ext_id=str(posting["slug"]), company=board["company"],
        title=posting.get("title") or "", location=posting.get("full_location") or posting.get("location_name") or "",
        country=posting.get("country_code") or posting.get("country"), url=url,
        apply_url=posting.get("apply_url") or url, req_id=posting.get("req_id"),
        team=posting.get("department"), posted_at=_when(posting.get("posted_date") or posting.get("create_date")),
        description=body, employment=posting.get("employment_type"),
    )


def fetch(board: dict, ctx: Ctx) -> FetchResult:
    return search.run(board, ctx, lambda term: _pages(board["key"], term), id_of=lambda p: str(p["slug"]),
                      title_of=lambda p: p.get("title") or "", normalize=_normalize)
