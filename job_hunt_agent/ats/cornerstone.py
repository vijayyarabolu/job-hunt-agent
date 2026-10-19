"""Cornerstone OnDemand career sites (`<tenant>.csod.com/ux/ats/careersite/<siteId>/home?c=<tenant>`, GOAL16 Tier B:
enterprise, healthcare, public sector, banks). Tenant robots.txt sets `Crawl-delay: 10` (honored by net.gap_for; every
*.csod.com host shares one rate bucket); the per-request gate still checks every URL.

Key '<tenant>/<siteId>' (entry: token = tenant, site = siteId). The career site page embeds the API base ("cloud")
and an anonymous token; its own search, POST <cloud>rec-job-search/external/jobs with that bearer token, returns 25
requisitions a page with title, posting day, locations and the full external description. Reading every page to
totalCount is the full list, so an absent job counts as a miss (cut at MAX_PAGES: truncated, no misses).
"""
import re
from datetime import datetime
from urllib.parse import quote

from ..net import HttpError, get_text, post_json
from ..text import html_to_text
from . import fulllist
from .common import Ctx, FetchResult, SchemaDrift, day_iso, make_job, require_list, schema_failure

PER_PAGE = 25
MAX_PAGES = 20
_CLOUD = re.compile(r'"endpoints"\s*:\s*\{[^}]*"cloud"\s*:\s*"(https://[a-z0-9.-]+\.csod\.com/)"')
_TOKEN = re.compile(r'"token"\s*:\s*"(eyJ[A-Za-z0-9_.-]+)"')
_TITLE = re.compile(r"<title>(.*?)</title>", re.S | re.I)


def split_key(key: str) -> tuple[str, str]:
    tenant, site = key.split("/", 1)
    return tenant, site


def home_url(key: str) -> str:
    tenant, site = split_key(key)
    return f"https://{tenant}.csod.com/ux/ats/careersite/{quote(site)}/home?c={quote(tenant)}"


def job_url(key: str, req_id) -> str:
    tenant, site = split_key(key)
    return f"https://{tenant}.csod.com/ux/ats/careersite/{quote(site)}/home/requisition/{req_id}?c={quote(tenant)}"


def session(key: str) -> tuple[str, str, str]:
    """(api base, bearer token, page title) from the career site page; SchemaDrift when it carries neither."""
    page = get_text(home_url(key))
    cloud, token = _CLOUD.search(page), _TOKEN.search(page)
    if not cloud or not token:
        raise SchemaDrift("no API endpoint or token on the Cornerstone career site")
    title = _TITLE.search(page)
    return cloud.group(1), token.group(1), html_to_text(title.group(1)) if title else ""


def _page(api: str, token: str, site: str, number: int) -> dict:
    site_id = int(site) if site.isdigit() else site
    body = {"careerSiteId": site_id, "careerSitePageId": site_id, "pageNumber": number, "pageSize": PER_PAGE,
            "cultureId": 1, "cultureName": "en-US", "searchText": "", "states": [], "countryCodes": [], "cities": [],
            "customFieldCheckboxKeys": [], "customFieldDropdowns": [], "customFieldRadios": []}
    payload = post_json(f"{api}rec-job-search/external/jobs", body, {"Authorization": f"Bearer {token}"})
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise SchemaDrift("expected data from the Cornerstone job search")
    return data


def listing(key: str) -> tuple[list[dict], int, bool, str]:
    """(requisitions, totalCount, truncated, page title)."""
    _, site = split_key(key)
    api, token, title = session(key)
    found: dict[str, dict] = {}
    total = 0
    for number in range(1, MAX_PAGES + 1):
        data = _page(api, token, site, number)
        batch = [r for r in require_list(data, "requisitions") if isinstance(r, dict) and r.get("requisitionId")]
        total = int(data.get("totalCount") or 0)
        before = len(found)
        found.update({str(r["requisitionId"]): r for r in batch})
        if len(batch) < PER_PAGE or len(found) >= total or len(found) == before:
            return list(found.values()), total, False, title
    return list(found.values()), total, len(found) < total, title


def posted(req: dict) -> str | None:
    text = str(req.get("postingEffectiveDate") or "")
    try:
        return day_iso(datetime.strptime(text, "%m/%d/%Y").date())
    except ValueError:
        return None


def location(req: dict) -> tuple[str, str | None]:
    places, countries = [], set()
    for loc in req.get("locations") or []:
        if isinstance(loc, dict):
            places.append(", ".join(str(loc[k]) for k in ("city", "state", "country") if loc.get(k)))
            if loc.get("country"):
                countries.add(loc["country"])
    return "; ".join(dict.fromkeys(p for p in places if p)), countries.pop() if len(countries) == 1 else None


def _normalize(board: dict, req: dict, _detail) -> dict:
    place, country = location(req)
    url = job_url(board["key"], req["requisitionId"])
    when = posted(req)
    return make_job(
        ats="cornerstone", key=board["key"], ext_id=req["requisitionId"], company=board["company"],
        title=req.get("displayJobTitle") or "", location=place, country=country, url=url, apply_url=url,
        req_id=str(req["requisitionId"]), posted_at=when, precision="day" if when else "unknown",
        description=html_to_text(req.get("externalDescription")),
    )


def fetch(board: dict, ctx: Ctx) -> FetchResult:
    try:
        listed, total, truncated, _ = listing(board["key"])
    except HttpError as err:
        return FetchResult([], err.status, error=str(err))
    except SchemaDrift as err:
        return schema_failure(err)
    notes = [f"truncated at {len(listed)} of {total} requisitions"] if truncated else []
    return fulllist.run(board, ctx, listed, id_of=lambda r: str(r["requisitionId"]),
                        title_of=lambda r: r.get("displayJobTitle") or "", normalize=_normalize, detail=None,
                        truncated=truncated, notes=notes)


def survey(key: str) -> dict:
    listed, total, _, title = listing(key)
    name = re.sub(r"(?i)\b(career site|careers?|jobs?|home)\b", " ", title)
    return {"n_jobs": total or len(listed), "name": " ".join(name.split(" - ")[0].split()) or None,
            "rows": [(posted(r), *location(r), r.get("displayJobTitle") or "") for r in listed]}
