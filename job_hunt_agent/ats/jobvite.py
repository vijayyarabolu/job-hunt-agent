"""Jobvite career sites (`jobs.jobvite.com/<company>`, GOAL10 §1; decision 011 had them as a gap). No JSON: the
board is HTML. jobs.jobvite.com has no robots.txt (404: no rules); the per-request gate still checks every URL.

Key '<company>' (the path slug). The /jobs page lists every opening by category unless a category is cut short with
"Show More"; then the paged /search list (PER_PAGE a page, at most MAX_PAGES) is read instead. Either way it is the
full list, so an absent job counts as a miss (a list cut at MAX_PAGES is marked truncated: no misses). A
title-matched job that is new or changed costs one request for its page: title, category, location, requisition
number and the description. Jobvite pages carry no posting date, so freshness is first-seen.
"""
import html
import re
from urllib.parse import quote

from .. import signals
from ..net import HttpError, get_text
from ..text import html_to_text
from . import fulllist
from .common import Ctx, FetchResult, SchemaDrift, make_job, schema_failure

BASE = "https://jobs.jobvite.com"
PER_PAGE = 50
MAX_PAGES = 20
_ROW = re.compile(r'<td class="jv-job-list-name">(.*?)</td>\s*<td class="jv-job-list-location">(.*?)</td>', re.S)
_LINK = re.compile(r'<a href="/([^/"]+)/job/(\w+)"[^>]*>(.*?)</a>', re.S)
_SPAN = re.compile(r'<span(?: class="([^"]*)")?>(.*?)</span>', re.S)
_MORE = re.compile(r'href="/[^/"]+/search\?c=')
_LIST_MARK = re.compile(r'class="jv-(?:job-list|page-body|careersite)')
_TOTAL = re.compile(r'jv-pagination-text">\s*\d+-\d+ of (\d+)')
_TITLE = re.compile(r'<h2 class="jv-header">(.*?)</h2>', re.S)
_META = re.compile(r'<p class="jv-job-detail-meta">(.*?)</p>', re.S)
_DESC = re.compile(r'class="jv-job-detail-description"[^>]*>(.*?)<div class="jv-job-detail-bottom-actions"', re.S)
_REQ = re.compile(r"Req\.?\s*Num\.?:?\s*([\w-]+)", re.I)
_SEP = re.compile(r"<span class=.jv-inline-separator.></span>")
_MULTI = re.compile(r"^\d+ Locations$")
_JOBVITE_PAGE = re.compile(r'class="jv-')            # absent once the slug redirects off Jobvite (ADP, support page)


def _text(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment or "")).split())


def is_jobvite(page: str) -> bool:
    return bool(_JOBVITE_PAGE.search(page or ""))


def rows(company: str, page: str) -> list[dict]:
    """[{id, title, location}] from a /jobs or /search page; SchemaDrift when it is not a Jobvite list at all."""
    if not _LIST_MARK.search(page or ""):
        raise SchemaDrift("expected a Jobvite job list page")
    out, seen = [], set()
    for name_cell, place_cell in _ROW.findall(page):
        link = _LINK.search(name_cell)
        if not link or link.group(1).lower() != company.lower() or link.group(2) in seen:
            continue
        seen.add(link.group(2))
        out.append({"id": link.group(2), "title": _text(link.group(3)), "location": _place(name_cell, place_cell)})
    return out


def _place(name_cell: str, place_cell: str) -> str:
    """The location cell; custom templates leave it to an Apply button and put the place in a bare <span> of the name
    cell after the link (the category span carries a class)."""
    place = _text(re.sub(r"<a\b.*?</a>", " ", place_cell, flags=re.S))
    if not place:
        after = name_cell[_LINK.search(name_cell).end():]
        place = next((_text(text) for cls, text in _SPAN.findall(after) if not cls), "")
    return "" if _MULTI.match(place) else place


def listing(company: str) -> tuple[list[dict], bool]:
    """(rows, truncated): the /jobs page when complete, else the paged search. 404 when the slug left Jobvite."""
    url = f"{BASE}/{quote(company)}/jobs"
    first = get_text(url)
    if not is_jobvite(first):
        raise HttpError(url, 404, "not a Jobvite career site any more")
    if not _MORE.search(first):
        return rows(company, first), False
    found: dict[str, dict] = {}
    total = 0
    for page in range(MAX_PAGES):
        text = get_text(f"{BASE}/{quote(company)}/search?q=&p={page}")
        batch = rows(company, text)
        found.update({r["id"]: r for r in batch})
        match = _TOTAL.search(text)
        total = int(match.group(1)) if match else len(found)
        if len(batch) < PER_PAGE or (page + 1) * PER_PAGE >= total:
            return list(found.values()), False
    return list(found.values()), len(found) < total


def parse_detail(page: str) -> dict | None:
    title, desc, meta = _TITLE.search(page or ""), _DESC.search(page or ""), _META.search(page or "")
    if not title or not desc:
        return None
    parts = [_text(part) for part in _SEP.split(meta.group(1))] if meta else []
    req = next((m.group(1) for m in map(_REQ.search, parts) if m), None)
    places = [p for p in parts if p and not _REQ.search(p)]
    return {"title": _text(title.group(1)), "team": places[0] if len(places) > 1 else None,
            "location": places[-1] if places else "", "req_id": req, "description": html_to_text(desc.group(1))}


def job_url(company: str, job_id: str) -> str:
    return f"{BASE}/{quote(company)}/job/{job_id}"


def _normalize(board: dict, row: dict, detail: dict | None) -> dict:
    url = job_url(board["key"], row["id"])
    detail = detail or {}
    return make_job(
        ats="jobvite", key=board["key"], ext_id=row["id"], company=board["company"],
        title=detail.get("title") or row["title"], location=detail.get("location") or row["location"], url=url,
        apply_url=f"{url}/apply", req_id=detail.get("req_id"), team=detail.get("team"), posted_at=None,
        precision="unknown", description=detail.get("description") or "",
    )


def fetch(board: dict, ctx: Ctx) -> FetchResult:
    company = board["key"]
    try:
        listed, truncated = listing(company)
    except HttpError as err:
        return FetchResult([], err.status, error=str(err))
    except SchemaDrift as err:
        return schema_failure(err)
    notes = [f"truncated at {len(listed)} jobs ({MAX_PAGES} pages)"] if truncated else []
    return fulllist.run(board, ctx, listed, id_of=lambda r: r["id"], title_of=lambda r: r["title"],
                        normalize=_normalize, detail=lambda r: parse_detail(get_text(job_url(company, r["id"]))),
                        truncated=truncated, notes=notes,
                        wants_detail=lambda r: signals.is_us(r["location"], None) is not False)
