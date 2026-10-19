"""Eightfold adapter (decision 011). Career-site JSON, undocumented but public; every tenant checked has
robots 'Allow: /api/pcsx' and 'Allow: /api/apply' (after 'Disallow: /', RFC 9309 longest match).

Key '<host>/<domain>/<api>': host serves the career site, domain is the tenant id the API wants, api is 'pcsx' (current
UI) or 'v2' (legacy /api/apply/v2, e.g. Netflix). A search like Amazon: one query per search term, location United
States, newest first, up to MAX_PAGES pages of PAGE. Absence is never a miss (store.SEARCH_ATS). The list carries no
description; a title-matched posting the DB has no JD for costs one detail request. Tenants answer 429 to 3 requests a
second and Microsoft, Qualcomm also to 1 a second after a burst (2026-10-06), so each host is paced at GAP_S; a 429
still pauses the host and its board carries over (net backoff).
"""
from datetime import datetime, timezone
from typing import Iterator
from urllib.parse import urlencode

from .. import net
from ..net import get_json
from ..text import html_to_text
from . import search
from .common import Ctx, FetchResult, SchemaDrift, iso_utc, make_job, require_list

PAGE = 10                      # the API's page size (num is ignored by pcsx)
MAX_PAGES = 3                  # newest 30 per term
GAP_S = 2.0
MAX_EPOCH_S = 4_102_444_800          # 2100-01-01: larger values are milliseconds or junk
LOCATION = "United States"
APIS = {"pcsx": ("/api/pcsx/search", "/api/pcsx/position_details"),
        "v2": ("/api/apply/v2/jobs", "/api/apply/v2/jobs/{id}")}


def split_key(key: str) -> tuple[str, str, str]:
    host, domain, api = key.split("/")
    if api not in APIS:
        raise ValueError(f"eightfold api must be one of {sorted(APIS)}: {key!r}")
    return host, domain, api


def _when(seconds) -> str | None:
    """Epoch seconds -> UTC text; anything else (milliseconds, junk) -> None rather than a wrong or failed date."""
    if not isinstance(seconds, (int, float)) or not 0 < seconds < MAX_EPOCH_S:
        return None
    return iso_utc(datetime.fromtimestamp(seconds, tz=timezone.utc))


def _pages(host: str, domain: str, api: str, term: str) -> Iterator[dict]:
    for page in range(MAX_PAGES):
        start = page * PAGE
        query = {"domain": domain, "query": term, "location": LOCATION, "start": start, "sort_by": "timestamp",
                 **({"num": PAGE} if api == "v2" else {})}
        payload = get_json(f"https://{host}{APIS[api][0]}?{urlencode(query)}")
        data = payload.get("data") if api == "pcsx" and isinstance(payload, dict) else payload
        if not isinstance(data, dict):
            raise SchemaDrift(f"expected an object{' under data' if api == 'pcsx' else ''}")
        positions = require_list(data, "positions")
        yield from positions
        count = data.get("count")
        if len(positions) < PAGE or not isinstance(count, int) or start + PAGE >= count:
            return


def _detail(host: str, domain: str, api: str, posting: dict) -> str | None:
    if api == "pcsx":
        query = urlencode({"position_id": posting["id"], "domain": domain, "hl": "en"})
        return ((get_json(f"https://{host}{APIS[api][1]}?{query}") or {}).get("data") or {}).get("jobDescription")
    path = APIS[api][1].format(id=posting["id"])
    return (get_json(f"https://{host}{path}?{urlencode({'domain': domain})}") or {}).get("job_description")


def _normalize(board: dict, posting: dict, description: str | None) -> dict:
    host, domain, api = split_key(board["key"])
    pcsx = api == "pcsx"
    places = (posting.get("standardizedLocations") if pcsx else None) or posting.get("locations") or [
        posting.get("location") or ""]
    url = (f"https://{host}{posting['positionUrl']}?domain={domain}" if pcsx and posting.get("positionUrl")
           else posting.get("canonicalPositionUrl") or f"https://{host}/careers/job/{posting['id']}?domain={domain}")
    return make_job(
        ats="eightfold", key=board["key"], ext_id=str(posting["id"]), company=board["company"],
        title=posting.get("name") or posting.get("posting_name") or "", location="; ".join(p for p in places if p),
        workplace=posting.get("workLocationOption") or posting.get("work_location_option"), url=url, apply_url=url,
        req_id=posting.get("displayJobId") or posting.get("display_job_id"), team=posting.get("department"),
        posted_at=_when(posting.get("postedTs") if pcsx else posting.get("t_create")),
        description=html_to_text(description) if description else "",
    )


def fetch(board: dict, ctx: Ctx) -> FetchResult:
    host, domain, api = split_key(board["key"])
    net.pace(host, GAP_S)
    return search.run(board, ctx, lambda term: _pages(host, domain, api, term),
                      id_of=lambda p: str(p["id"]), title_of=lambda p: p.get("name") or p.get("posting_name") or "",
                      normalize=_normalize, detail=lambda p: _detail(host, domain, api, p))
