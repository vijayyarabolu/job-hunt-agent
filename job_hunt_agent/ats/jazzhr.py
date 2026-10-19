"""JazzHR (Employ) career pages (`<company>.applytojob.com/apply`, GOAL16 Tier B: US SMB). Tenant robots.txt (checked
2026-10-08) disallows only /cb and lists JazzHR's global Google feed; the per-request gate still checks every URL.
Every tenant shares one rate bucket (net.SHARED_BUCKETS).

Key '<company>' (the subdomain). The /apply page lists every open job (link with its token and title slug, title,
location), one request for the whole board, so an absent job counts as a miss. A title-matched job that is new or
changed costs one request for its page, whose schema.org JobPosting gives datePosted, the employer, the employment
type and the full description. No date otherwise: first-seen freshness until the page is read.

Discovery (feed_tenants): `app.jazz.co/feeds/google/xml/0..4` lists every open JazzHR job of every tenant
(~108,800 URLs over ~7,500 tenants on 2026-10-08; files past the last one are empty). Only tenants with a title that
matches a profile family today are verified, so ~7,000 tenants of hourly roles are not fetched every day.
"""
import html
import re

from .. import signals
from ..net import HttpError, get_text
from ..text import html_to_text
from . import fulllist, jsonld
from .common import Ctx, FetchResult, SchemaDrift, make_job, schema_failure

FEEDS = tuple(f"http://app.jazz.co/feeds/google/xml/{n}" for n in range(5))
_ITEM = re.compile(r'<li class="list-group-item">(.*?)</ul>', re.S)
_LINK = re.compile(r'<a href="https://([a-z0-9-]+)\.applytojob\.com/apply/([A-Za-z0-9]{4,20})/([^"?#]*)[^"]*">(.*?)</a>',
                   re.S)
_PLACE = re.compile(r"fa-map-marker'></i>(.*?)</li>", re.S)
_LIST_MARK = re.compile(r"job-board-list|list-group-item|resumator", re.I)
_TITLE = re.compile(r"<title>(.*?)</title>", re.S | re.I)
_FEED_URL = re.compile(r"<loc>\s*https://([a-z0-9-]+)\.applytojob\.com/apply/([A-Za-z0-9]{4,20})/([^<?\s]*)")


def list_url(key: str) -> str:
    return f"https://{key}.applytojob.com/apply"


def job_url(key: str, token: str, slug: str) -> str:
    return f"https://{key}.applytojob.com/apply/{token}/{slug}"


def _text(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment or "")).split())


def rows(key: str, page: str) -> list[dict]:
    """[{id, slug, title, location}] from a tenant's /apply page; SchemaDrift when it is not a JazzHR board."""
    if not _LIST_MARK.search(page or ""):
        raise SchemaDrift("expected a JazzHR job list page")
    out, seen = [], set()
    for item in _ITEM.findall(page):
        link = _LINK.search(item)
        if not link or link.group(1) != key or link.group(2) in seen:
            continue
        seen.add(link.group(2))
        place = _PLACE.search(item)
        out.append({"id": link.group(2), "slug": link.group(3), "title": _text(link.group(4)),
                    "location": _text(place.group(1)) if place else ""})
    return out


def board_title(page: str) -> str | None:
    found = _TITLE.search(page or "")
    return re.sub(r"(?i)\s*-\s*career page\s*$", "", _text(found.group(1))) if found else None


def _detail(key: str, row: dict) -> dict | None:
    return jsonld.job_posting(get_text(job_url(key, row["id"], row["slug"])))


def _normalize(board: dict, row: dict, posting: dict | None) -> dict:
    posting = posting or {}
    place, country = jsonld.location(posting) if posting else ("", None)
    url = job_url(board["key"], row["id"], row["slug"])
    when = jsonld.posted(posting) if posting else None
    return make_job(
        ats="jazzhr", key=board["key"], ext_id=row["id"], company=board["company"],
        title=html_to_text(posting.get("title")) or row["title"], location=place or row["location"],
        country=country, url=url, apply_url=url, req_id=posting.get("uniqueJobCode"),
        posted_at=when, precision="day" if when else "unknown", description=html_to_text(posting.get("description")),
        employment=jsonld.employment(posting) if posting else None,
    )


def fetch(board: dict, ctx: Ctx) -> FetchResult:
    key = board["key"]
    try:
        listed = rows(key, get_text(list_url(key)))
    except HttpError as err:
        return FetchResult([], err.status, error=str(err))
    except SchemaDrift as err:
        return schema_failure(err)
    return fulllist.run(board, ctx, listed, id_of=lambda r: r["id"], title_of=lambda r: r["title"],
                        normalize=_normalize, detail=lambda r: _detail(key, r),
                        wants_detail=lambda r: signals.is_us(r["location"], None) is not False)


def survey(key: str) -> dict:
    """Discovery: the open jobs (no dates on the list: an open posting is current) and the page's company name."""
    page = get_text(list_url(key))
    listed = rows(key, page)
    return {"n_jobs": len(listed), "name": board_title(page),
            "rows": [(None, r["location"], None, r["title"]) for r in listed]}


def feed_tenants(matches) -> dict[str, list[str]]:
    """tenant -> its job titles (from the URL slugs) that `matches` accepts, over JazzHR's global feed."""
    found: dict[str, list[str]] = {}
    for url in FEEDS:
        try:
            text = get_text(url)
        except HttpError:
            continue
        hits = _FEED_URL.findall(text)
        if not hits:
            break                                          # past the last feed file
        for tenant, _token, slug in hits:
            title = slug.replace("-", " ")
            if matches(title):
                found.setdefault(tenant, []).append(title)
    return found
