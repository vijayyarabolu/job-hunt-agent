"""Classic iCIMS career portals (`<portal>.icims.com`, GOAL10 §1; decision 011 had them as a gap). Tenant robots.txt
(checked 2026-10-08) disallows only referral, login, candidate, reminder and connect paths and lists /sitemap.xml;
the sitemap and the job pages are allowed and the per-request gate still checks every URL.

Key '<host>' (e.g. careers-stifel.icims.com). The sitemap is the full list of open jobs (id, title slug, lastmod), one
request per board, so an absent job counts as a miss. A title-matched job that is new or changed costs one request
for its page, whose schema.org JobPosting gives the real title, locations, datePosted, employment type and the full
description. Other jobs keep the title spelled out from the URL slug and no location (they never reach a feed).
"""
import re

from ..net import HttpError, get_text
from ..text import html_to_text
from . import fulllist, jsonld
from .common import Ctx, FetchResult, SchemaDrift, make_job, schema_failure

_URL = re.compile(r"<url>\s*<loc>\s*https?://([^/<]+)/jobs/(\d+)/([^/<]*)/job\s*</loc>(?:\s*<lastmod>([^<]*)</lastmod>)?",
                  re.I)
_SITEMAP = re.compile(r"<urlset\b", re.I)


def sitemap_url(host: str) -> str:
    return f"https://{host}/sitemap.xml"


def job_url(host: str, job_id: str, slug: str) -> str:
    return f"https://{host}/jobs/{job_id}/{slug or 'job'}/job"


def slug_title(slug: str) -> str:
    """'sr.-ai-cloud-platform-engineer' -> 'Sr. Ai Cloud Platform Engineer'; '---' was ' - '."""
    words = slug.replace("---", " \x00 ").replace("-", " ").replace("\x00", "-").split()
    return " ".join(w[:1].upper() + w[1:] for w in words)


def entries(host: str, xml: str) -> list[dict]:
    """Sitemap -> [{id, slug, lastmod}] for this host's job pages; SchemaDrift when it is not a sitemap at all."""
    if not _SITEMAP.search(xml or ""):
        raise SchemaDrift("expected a <urlset> sitemap")
    out, seen = [], set()
    for found_host, job_id, slug, lastmod in _URL.findall(xml):
        if found_host.lower() != host.lower() or job_id in seen:
            continue
        seen.add(job_id)
        out.append({"id": job_id, "slug": slug, "lastmod": (lastmod or "").strip() or None})
    return out


def _detail(host: str, entry: dict) -> dict | None:
    return jsonld.job_posting(get_text(f"{job_url(host, entry['id'], entry['slug'])}?in_iframe=1"))


def _normalize(board: dict, entry: dict, posting: dict | None) -> dict:
    host = board["key"]
    url = job_url(host, entry["id"], entry["slug"])
    posting = posting or {}
    location, country = jsonld.location(posting) if posting else ("", None)
    return make_job(
        ats="icims_classic", key=host, ext_id=entry["id"], company=board["company"],
        title=html_to_text(posting.get("title")) or slug_title(entry["slug"]), location=location, country=country,
        url=url, apply_url=url, req_id=entry["id"], team=posting.get("occupationalCategory"),
        posted_at=jsonld.posted(posting) if posting else None, precision="exact" if posting else "unknown",
        description=html_to_text(posting.get("description")), employment=jsonld.employment(posting),
    )


def fetch(board: dict, ctx: Ctx) -> FetchResult:
    host = board["key"]
    try:
        listed = entries(host, get_text(sitemap_url(host)))
    except HttpError as err:
        return FetchResult([], err.status, error=str(err))
    except SchemaDrift as err:
        return schema_failure(err)
    return fulllist.run(board, ctx, listed, id_of=lambda e: e["id"], title_of=lambda e: slug_title(e["slug"]),
                        normalize=_normalize, detail=lambda e: _detail(host, e))
