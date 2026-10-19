"""Oracle Taleo Enterprise career sections (`<tenant>.taleo.net/careersection/<section>/`, GOAL10 §1; decision 011
had them as a gap). The career section's own search JSON (POST /careersection/rest/jobboard/searchjobs), public and
anonymous; taleo.net tenant hosts answer robots.txt with 404 (RFC 9309: no rules) and the per-request gate still runs.

Key '<host>/<section>' (e.g. hdr.taleo.net/ex). The section's jobsearch.ftl page is read once per process (one extra
request per board per fetch run) and decides the mode: 'rest' when it carries the portal number the JSON search needs,
'legacy' for the older UI, whose search page itself holds the newest page of requisitions in its initialHistory field
and takes the keyword as a URL parameter (e.g. Weyerhaeuser). A search like Eightfold: one keyword query per term, up
to MAX_PAGES pages of the section's page size (25; one page in legacy mode), in the section's default order (newest
first on the sections checked); non-US rows are dropped. Absence is never a miss (store.SEARCH_ATS). The list columns are
configured per tenant: the title is the linked column, places the location columns, the posting day the column that
reads like 'Oct 5, 2026'. A title-matched posting the DB has no JD for costs one request for jobdetail.ftl, whose
description parts sit percent-encoded in the page's initialHistory field.
"""
import html
import json
import re
import threading
from datetime import datetime
from typing import Iterator
from urllib.parse import quote, unquote

from .. import net, signals
from ..net import HttpError, get_text, post_json
from ..text import html_to_text
from . import search
from .common import Ctx, FetchResult, SchemaDrift, day_iso, make_job, require_list, schema_failure

MAX_PAGES = 2
GAP_S = 1.0
_DAY = re.compile(r"^[A-Z][a-z]{2,8}\.? \d{1,2}, \d{4}$")
_STAMP = re.compile(r"^([A-Z][a-z]{2,8}\.? \d{1,2}, \d{4})(?:, \d{1,2}:\d\d(?::\d\d)? [AP]M)?$")
_HISTORY = re.compile(r'id="initialHistory"[^>]*value="([^"]*)"|value="([^"]*)"[^>]*id="initialHistory"')
_PORTAL = re.compile(r"portal(?:No)?['\"]?\s*[:=]\s*['\"]?(\d{5,})")
_ESCAPED = re.compile(r"\\(.)")
_TALEO_PAGE = re.compile(r"/careersection/\d{4}PRD")          # Taleo's own asset path: absent once a section redirects away
RICH = "!*!"
HEADERS = {"tz": "GMT+00:00"}         # the search answers 500 without the browser's time-zone header
MULTIPLE = "Multiple Locations"
UNAVAILABLE = "Career Section Unavailable"


_SUBMISSION = re.compile(r"^Submission for the position\\?: (.*) - \(Job Number\\?: ([^)]+)\)$", re.S)
_CODED_PLACE = r"[A-Z][A-Za-z .'()]*-[A-Z][A-Za-z0-9 .'()]*(?:-[A-Za-z0-9 .'()]+){0,2}"            # USA-CO-Henderson
_PLACE = re.compile(rf"^{_CODED_PLACE}(?:, {_CODED_PLACE})*$|^[A-Z][A-Za-z .']+, [A-Z][A-Za-z .']+$")   # or Denver, CO
_modes: dict[str, tuple[str, str]] = {}
_modes_lock = threading.Lock()
REST, LEGACY = "rest", "legacy"


def split_key(key: str) -> tuple[str, str]:
    host, section = key.split("/")
    return host, section


def search_body(term: str | None, page: int) -> dict:
    return {"multilineEnabled": False,
            "sortingSelection": {"sortBySelectionParam": "3", "ascendingSortingOrder": "false"},
            "fieldData": {"fields": {"KEYWORD": term or "", "LOCATION": ""}, "valid": True},
            "filterSelectionParam": {"searchFilterSelections": []},
            "advancedSearchFiltersSelectionParam": {"searchFilterSelections": []}, "pageNo": page}


def search_url(host: str, portal: str) -> str:
    return f"https://{host}/careersection/rest/jobboard/searchjobs?lang=en&portal={quote(portal)}"


def detail_url(host: str, section: str, contest: str) -> str:
    return f"https://{host}/careersection/{quote(section)}/jobdetail.ftl?job={quote(contest)}&lang=en"


def search_page_url(host: str, section: str, term: str | None = None) -> str:
    keyword = f"&keyword={quote(term)}" if term else ""
    return f"https://{host}/careersection/{quote(section)}/jobsearch.ftl?lang=en{keyword}"


def _history(page: str) -> list[str] | None:
    found = _HISTORY.search(page or "")
    if not found:
        return None
    return [unquote(p) for p in html.unescape(found.group(1) or found.group(2) or "").split("!|!")]


def legacy_rows(page: str) -> list[dict]:
    """Requisitions from an old-UI jobsearch.ftl page, in the REST row shape (title, location, day columns). Each
    record opens with '<job id>, <title>' and ends with its 'Submission for the position: <title> - (Job Number: <n>)'
    text; the first place-like value before its first 'Oct 7, 2026' is its location, that date its posting day."""
    parts = _history(page)
    if parts is None:
        raise SchemaDrift("expected the initialHistory field on a legacy career section page")
    rows, start = [], 0
    for index, part in enumerate(parts):
        found = _SUBMISSION.match(part)
        if not found:
            continue
        window = [_ESCAPED.sub(r"\1", p) for p in parts[start:index]]
        start = index + 1
        name = _ESCAPED.sub(r"\1", found.group(1)).strip()
        at = next((i for i, p in enumerate(window) if p.strip() == name and i and window[i - 1].isdigit()), None)
        if at is None:                         # the record opens with '<job id>, <title>'; the window may also hold
            continue                           # the previous record's tail (its id, 'Add to My Job Cart', ...)
        job_id, window = window[at - 1], window[at - 1:]
        before_day = window[:next((i for i, p in enumerate(window) if _DAY.match(p)), len(window))]
        place = next((p for p in before_day[2:] if _PLACE.match(p)), "")       # after '<id>, <title>'
        day = next((p for p in window if _DAY.match(p)), "")
        rows.append({"jobId": job_id, "contestNo": _ESCAPED.sub(r"\1", found.group(2)).strip(),
                     "column": [name, json.dumps([place] if place else []), day], "linkedColumn": 0,
                     "locationsColumns": [1]})
    return rows


def _pages(host: str, section: str, term: str | None) -> Iterator[dict]:
    kind, portal = mode(host, section)
    if kind == LEGACY:
        yield from legacy_rows(get_text(search_page_url(host, section, term)))
        return
    for page in range(1, MAX_PAGES + 1):
        payload = post_json(search_url(host, portal), search_body(term, page), HEADERS)
        rows = require_list(payload, "requisitionList")
        yield from rows
        paging = payload.get("pagingData") or {}
        size, total = paging.get("pageSize") or len(rows), paging.get("totalCount") or 0
        if not rows or page * size >= total:
            return


def search_rows(host: str, section: str, term: str | None) -> list[dict]:
    """Every row the section's search returns for `term` (None: the newest), in either mode (discovery)."""
    return list(_pages(host, section, term))


def _column(posting: dict, index) -> str:
    columns = posting.get("column") or []
    value = columns[index] if isinstance(index, int) and 0 <= index < len(columns) else ""
    return value if isinstance(value, str) else ""


def title(posting: dict) -> str:
    return html.unescape(_column(posting, posting.get("linkedColumn", 0)))


def places(posting: dict) -> list[str]:
    out = []
    for index in posting.get("locationsColumns") or []:
        raw = _column(posting, index)
        try:
            value = json.loads(raw) if raw.startswith("[") else [raw]
        except json.JSONDecodeError:
            value = [raw]
        out.extend(str(v) for v in value if v and str(v) != MULTIPLE)
    return out


def posted_day(posting: dict) -> str | None:
    for text in posting.get("column") or []:
        if isinstance(text, str) and _DAY.match(text.strip()):
            try:
                return day_iso(datetime.strptime(text.strip().replace(".", ""), "%b %d, %Y").date())
            except ValueError:
                continue
    return None


def is_us(posting: dict) -> bool:
    where = places(posting)
    return not where or any(signals.is_us(p, None) is not False for p in where)


def description(page: str) -> str | None:
    """The rich-text parts of jobdetail.ftl's initialHistory field, as plain text; None when the field is absent."""
    parts = _history(page)
    if parts is None:
        return None
    rich = [_ESCAPED.sub(r"\1", p[len(RICH):]) for p in parts if p.startswith(RICH)]
    return "\n\n".join(t for t in (html_to_text(r) for r in rich) if t)


def _posting_day(parts: list[str]) -> str | None:
    """The first 'Sep 16, 2026' / 'Sep 16, 2026, 11:33:48 AM' part: the posting day (an unposting day follows it)."""
    for part in parts:
        found = _STAMP.match(_ESCAPED.sub(r"\1", part).strip())
        if found:
            try:
                return day_iso(datetime.strptime(found.group(1).replace(".", ""), "%b %d, %Y").date())
            except ValueError:
                continue
    return None


def detail(page: str) -> dict | None:
    """jobdetail.ftl: the JD and, on sections whose list has no day column (UTSW), the day the page shows."""
    text = description(page)
    return None if text is None else {"description": text, "posted_at": _posting_day(_history(page))}


def portal_of(page: str) -> str | None:
    """The portal number from a jobsearch.ftl page."""
    found = _PORTAL.search(page or "")
    return found.group(1) if found else None


def mode(host: str, section: str) -> tuple[str, str]:
    """('rest', portal number) or ('legacy', ''), cached per process; 404 for a closed section (or one that sends us
    to another site), SchemaDrift for a page that is neither."""
    key = f"{host}/{section}"
    with _modes_lock:
        if key in _modes:
            return _modes[key]
    url = search_page_url(host, section)
    page = get_text(url)
    found = portal_of(page)
    if found:
        result = (REST, found)
    elif "listRequisition" in page and _history(page) is not None:
        result = (LEGACY, "")
    elif UNAVAILABLE in page or not _TALEO_PAGE.search(page):
        raise HttpError(url, 404, "career section unavailable")       # closed or moved off Taleo: gone, not drift
    else:
        raise SchemaDrift("neither a portal number nor a legacy job list on the career section page")
    with _modes_lock:
        _modes[key] = result
    return result


def _contest(posting: dict) -> str:
    return str(posting.get("contestNo") or posting["jobId"])


def _normalize(board: dict, posting: dict, page: dict | None) -> dict:
    host, section = split_key(board["key"])
    url = detail_url(host, section, _contest(posting))
    page = page or {}
    day = posted_day(posting) or page.get("posted_at")       # read once with the JD; later fetches keep it
    return make_job(
        ats="taleo", key=board["key"], ext_id=str(posting["jobId"]), company=board["company"], title=title(posting),
        location="; ".join(places(posting)), url=url, apply_url=url, req_id=_contest(posting),
        posted_at=day, precision="day" if day else "unknown", description=page.get("description") or "",
    )


def fetch(board: dict, ctx: Ctx) -> FetchResult:
    host, section = split_key(board["key"])
    net.pace(host, GAP_S)
    try:
        mode(host, section)
    except HttpError as err:
        return FetchResult([], err.status, error=str(err))
    except SchemaDrift as err:
        return schema_failure(err)
    return search.run(board, ctx, lambda term: _pages(host, section, term), id_of=lambda p: str(p["jobId"]),
                      title_of=title, normalize=_normalize, keep=is_us,
                      detail=lambda p: detail(get_text(detail_url(host, section, _contest(p)))))
