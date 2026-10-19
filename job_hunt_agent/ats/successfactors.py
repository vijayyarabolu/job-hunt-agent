"""SAP SuccessFactors Recruiting Marketing career sites (`jobs.<company>.com` and the like). The JSON and
RSS feeds live under /services/, which these sites' robots.txt disallow, and `career<N>.successfactors.com` is
`Disallow: /`; the HTML search and job pages are allowed (checked per site by the per-request gate).

Key '<host>' or '<host>/<path>' when the site lives under a path (careers.ey.com/ey). A search like Eightfold: one
exact-phrase query per term in relevance order, one page of rows (the plain keyword matches anywhere in a JD, and
newest-first then shows unrelated jobs); rows whose location is clearly outside the US are dropped. Absence is never a miss (store.SEARCH_ATS). The rows carry title, location and the job link; a
title-matched posting the DB has no JD for costs one request for its page, whose schema.org microdata gives
datePosted, every location and the description.
"""
import html
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Iterator
from urllib.parse import quote_plus

from .. import signals
from ..net import get_text
from ..text import html_to_text
from . import search
from .common import Ctx, FetchResult, SchemaDrift, iso_utc, make_job

_ROW = re.compile(r'<tr class="data-row[^"]*">(.*?)</tr>', re.S)
_HREF = re.compile(r'href="([^"]*/job/[^"]*?/(\d+)/?)"')
_TITLE_LINK = re.compile(r'<a\b[^>]*class="jobTitle-link\b[^"]*"[^>]*>(.*?)</a>', re.S)
_PLACE = re.compile(r'<span class="jobLocation">(.*?)</span>', re.S)
_DATE = re.compile(r'<span class="jobDate[^"]*">\s*([^<]+?)\s*</span>')
_META = re.compile(r'<meta itemprop="(\w+)" content="([^"]*)"')
_MORE = re.compile(r"\+\d+ more.*$", re.S)
_RESULTS_PAGE = re.compile(r'id="searchresults"|class="searchResults|paginationLabel|id="noresults"', re.I)
_TILE = re.compile(r'<li class="job-tile\b[^"]*"(.*?)</li>', re.S)          # the tile layout (e.g. L3Harris)
_TILE_FIELD = r'class="section-field {}\b[^"]*"[^>]*>(.*?)</div>'
_LABEL = re.compile(r'<span[^>]*-label"[^>]*>.*?</span>', re.S)


def split_key(key: str) -> tuple[str, str]:
    host, _, path = key.partition("/")
    return host, f"/{path}" if path else ""


def search_url(key: str, term: str | None, start: int = 0) -> str:
    """A term is searched as an exact phrase in relevance order (the plain keyword matches anywhere in a JD, so
    newest-first fills the page with unrelated jobs); no term lists the newest postings."""
    host, path = split_key(key)
    if term:
        return f"https://{host}{path}/search/?q={quote_plus(chr(34) + term + chr(34))}&startrow={start}"
    return f"https://{host}{path}/search/?q=&sortColumn=referencedate&sortDirection=desc&startrow={start}"


def _text(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment or "")).split())


def rows(page: str) -> list[dict]:
    """[{id, title, location, path, date}] from a search page; SchemaDrift when it is not a results page."""
    if not _RESULTS_PAGE.search(page or "") and not _ROW.search(page or "") and not _TILE.search(page or ""):
        raise SchemaDrift("expected a SuccessFactors search results page")
    out, seen = [], set()
    for row in _ROW.findall(page) or _TILE.findall(page):
        href, title = _HREF.search(row), _TITLE_LINK.search(row)
        if not href or not title or href.group(2) in seen:
            continue
        seen.add(href.group(2))
        place, day = _PLACE.search(row), _DATE.search(row)
        location = _text(place.group(1)) if place else _tile_field(row, "location")
        out.append({"id": href.group(2), "title": _text(title.group(1)), "path": html.unescape(href.group(1)),
                    "location": _MORE.sub("", location).strip(" ,"),
                    "date": day.group(1) if day else (_tile_field(row, "date") or None)})
    return out


def _tile_field(tile: str, name: str) -> str:
    found = re.search(_TILE_FIELD.format(name), tile, re.S)
    return _text(_LABEL.sub(" ", found.group(1))) if found else ""


class _Element(HTMLParser):
    """Inner HTML of the first element carrying itemprop=<prop>."""

    def __init__(self, prop: str) -> None:
        super().__init__(convert_charrefs=False)
        self.prop, self.depth, self.parts, self.done = prop, 0, [], False

    def handle_starttag(self, tag, attrs):
        if self.done:
            return
        if self.depth:
            self.depth += 1
            self.parts.append(self.get_starttag_text() or "")
        elif dict(attrs).get("itemprop") == self.prop:
            self.depth = 1

    def handle_endtag(self, tag):
        if not self.depth or self.done:
            return
        self.depth -= 1
        if self.depth == 0:
            self.done = True
        else:
            self.parts.append(f"</{tag}>")

    def handle_data(self, data):
        if self.depth and not self.done:
            self.parts.append(data)

    def handle_entityref(self, name):
        self.handle_data(f"&{name};")

    def handle_charref(self, name):
        self.handle_data(f"&#{name};")


def _inner(page: str, prop: str) -> str:
    parser = _Element(prop)
    parser.feed(page)
    return "".join(parser.parts)


def posted(text: str | None) -> str | None:
    """'Wed Oct 07 02:01:00 UTC 2026' (or an ISO day) -> UTC text."""
    if not text:
        return None
    for fmt in ("%a %b %d %H:%M:%S UTC %Y", "%Y-%m-%d"):
        try:
            return iso_utc(datetime.strptime(text.strip(), fmt).replace(tzinfo=timezone.utc))
        except ValueError:
            continue
    return None


def parse_detail(page: str) -> dict | None:
    body = _inner(page or "", "description")
    if not body:
        return None
    meta: dict[str, list[str]] = {}
    for prop, value in _META.findall(page):
        meta.setdefault(prop, []).append(html.unescape(value))
    countries = meta.get("addressCountry", [])
    places = [", ".join(p for p in parts if p) for parts in
              zip(meta.get("addressLocality", []), meta.get("addressRegion", []), countries)]
    return {"description": html_to_text(body), "posted": posted((meta.get("datePosted") or [None])[0]),
            "location": "; ".join(dict.fromkeys(places)),
            "country": countries[0] if len(set(countries)) == 1 else None,
            "title": html_to_text(_inner(page, "title")) or None,
            "organization": (meta.get("hiringOrganization") or [None])[0]}


def _pages(key: str, term: str) -> Iterator[dict]:
    yield from rows(get_text(search_url(key, term)))


def is_us(row: dict) -> bool:
    return signals.is_us(row.get("location") or "", None) is not False


def job_url(key: str, row: dict) -> str:
    host, _ = split_key(key)
    return f"https://{host}{row['path']}" if row["path"].startswith("/") else row["path"]


def _normalize(board: dict, row: dict, detail: dict | None) -> dict:
    url = job_url(board["key"], row)
    detail = detail or {}
    when = detail.get("posted") or posted(row.get("date"))
    return make_job(
        ats="successfactors", key=board["key"], ext_id=row["id"], company=board["company"],
        title=detail.get("title") or row["title"], location=detail.get("location") or row["location"],
        country=detail.get("country"), url=url, apply_url=url, req_id=row["id"], posted_at=when,
        precision="exact" if when else "unknown", description=detail.get("description") or "",
    )


def fetch(board: dict, ctx: Ctx) -> FetchResult:
    key = board["key"]
    return search.run(board, ctx, lambda term: _pages(key, term), id_of=lambda r: r["id"],
                      title_of=lambda r: r["title"], normalize=_normalize, keep=is_us,
                      detail=lambda r: parse_detail(get_text(job_url(key, r))))
