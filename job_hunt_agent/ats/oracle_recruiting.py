"""Oracle Recruiting Cloud adapter. The candidate-experience site's own REST resources, public and
anonymous: `recruitingCEJobRequisitions` (search) and `recruitingCEJobRequisitionDetails` (one job). robots.txt on
the tenant hosts answers 403 or 404 to everyone, which RFC 9309 reads as "no rules"; the per-request gate still runs.

Key '<host>/<site>': the tenant host (e.g. jpmc.fa.oraclecloud.com) and the site number from the career site's URL
(/hcmUI/CandidateExperience/en/sites/CX_1001). A search like Eightfold: one exact-phrase query per search term,
newest first, one page of PAGE (50); non-US requisitions are dropped. Absence is never a miss (store.SEARCH_ATS). The
list has the posting day, title and locations; a title-matched posting the DB has no JD for costs one detail
request (description, responsibilities, qualifications and the salary flex field).
"""
import re
from datetime import date
from typing import Iterator
from urllib.parse import quote

from .. import net, signals
from ..net import get_json
from ..text import html_to_text
from . import search
from .common import Ctx, FetchResult, SchemaDrift, day_iso, make_job, require_list

PAGE = 50                      # newest 50 per term: a 30-min cadence sees every new posting
GAP_S = 1.0
API = "https://{host}/hcmRestApi/resources/latest/"
_FINDER_UNSAFE = re.compile(r'[,;"=]')       # finder syntax characters: never part of a keyword
SANDBOX = re.compile(r"^[a-z0-9]+-(?:dev|test|stage|stg|uat)\d*\.")   # efds-test.fa…: a tenant's test copy
DETAIL_TEXT = ("ExternalDescriptionStr", "ExternalResponsibilitiesStr", "ExternalQualificationsStr")


def split_key(key: str) -> tuple[str, str]:
    host, site = key.split("/")
    return host, site


def job_url(host: str, site: str, req_id: str) -> str:
    return f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{req_id}"


def list_url(host: str, site: str, term: str | None, limit: int = PAGE) -> str:
    keyword = f',keyword="{_FINDER_UNSAFE.sub(" ", term).strip()}"' if term else ""
    finder = f"findReqs;siteNumber={site},limit={limit}{keyword},sortBy=POSTING_DATES_DESC"
    return (f"{API.format(host=host)}recruitingCEJobRequisitions?onlyData=true"
            f"&expand=requisitionList.secondaryLocations&finder={quote(finder, safe=';,=')}")


def requisitions(payload) -> list[dict]:
    items = require_list(payload, "items")
    if not items:
        return []
    if not isinstance(items[0], dict):
        raise SchemaDrift("expected items[0] to be an object")
    return require_list(items[0], "requisitionList")


def _pages(host: str, site: str, term: str) -> Iterator[dict]:
    yield from requisitions(get_json(list_url(host, site, term)))


def detail_text(detail: dict) -> str:
    """Description + responsibilities + qualifications, then the flex fields (salary is usually one)."""
    parts = [html_to_text(detail.get(k)) for k in DETAIL_TEXT if detail.get(k)]
    for flex in detail.get("requisitionFlexFields") or []:
        if isinstance(flex, dict) and flex.get("Prompt") and flex.get("Value"):
            parts.append(f"{flex['Prompt']}: {html_to_text(str(flex['Value']))}")
    return "\n\n".join(p for p in parts if p)


def _detail(host: str, site: str, posting: dict) -> str | None:
    finder = quote(f'ById;Id="{posting["Id"]}",siteNumber={site}', safe=";,=")
    payload = get_json(f"{API.format(host=host)}recruitingCEJobRequisitionDetails?expand=all&onlyData=true"
                       f"&finder={finder}")
    items = require_list(payload, "items")
    return detail_text(items[0]) if items and isinstance(items[0], dict) else None


def _places(posting: dict) -> list[tuple[str, str | None]]:
    out = [(posting.get("PrimaryLocation") or "", posting.get("PrimaryLocationCountry"))]
    for loc in posting.get("secondaryLocations") or []:
        if isinstance(loc, dict) and loc.get("Name"):
            out.append((loc["Name"], loc.get("CountryCode")))
    return [(name, country) for name, country in out if name or country]


def is_us(posting: dict) -> bool:
    """Any location in the US (a Bangalore requisition that also lists Plano counts); no location at all is kept."""
    places = _places(posting)
    return not places or any(signals.is_us(name, country) is not False for name, country in places)


def posted_day(text: str | None) -> str | None:
    try:
        return day_iso(date.fromisoformat(text[:10])) if text else None
    except ValueError:
        return None


def _normalize(board: dict, posting: dict, description: str | None) -> dict:
    host, site = split_key(board["key"])
    us_first = sorted(_places(posting), key=lambda p: signals.is_us(*p) is not True)
    url = job_url(host, site, str(posting["Id"]))
    return make_job(
        ats="oracle_recruiting", key=board["key"], ext_id=str(posting["Id"]), company=board["company"],
        title=posting.get("Title") or "", location="; ".join(name for name, _ in us_first if name),
        country=us_first[0][1] if us_first else None, workplace=posting.get("WorkplaceType") or None, url=url,
        apply_url=url, req_id=str(posting["Id"]), team=posting.get("JobFamily") or posting.get("JobFunction"),
        posted_at=posted_day(posting.get("PostedDate")), precision="day",
        description=description or "", employment=posting.get("JobSchedule") or posting.get("JobType"),
    )


def fetch(board: dict, ctx: Ctx) -> FetchResult:
    host, site = split_key(board["key"])
    net.pace(host, GAP_S)
    return search.run(board, ctx, lambda term: _pages(host, site, term), id_of=lambda p: str(p["Id"]),
                      title_of=lambda p: p.get("Title") or "", normalize=_normalize,
                      detail=lambda p: _detail(host, site, p), keep=is_us)
