"""Workday CXS adapter. Two passes (decision 005 B2 + skeptic objection 4):

- list (board["task"] == "list", the tiered pass): empty search, which Workday returns newest first; read pages until
  one is entirely older than the last OK fetch minus a day, never past the FIRST page's total (later pages report
  total=0 and offset 2000 wraps to page 0), at most MAX_LIST_PAGES.
- terms (the default, v1's pass; due when matched jobs need refreshing): POST per search term (top 100). Terms reach
  jobs past position 2,000 on capped tenants, so open matches there never go stale.

Detail GET only for fresh (<=2 d) title matches without a stored JD (GOAL §1). The CXS list is a POST: never
conditional.
"""
import re
from datetime import date

from ..net import HttpError, get_json, post_json
from ..text import html_to_text
from .common import (Ctx, FetchResult, SchemaDrift, day_iso, days_before, fast_item, make_job, raw_fp, require_list,
                     schema_failure)

PAGE_SIZE = 20
TOP_N = 100                     # results are relevance-sorted; read the top 100 per term
CAPPED_DAYS = 30                # "Posted 30+ Days Ago"
DETAIL_MAX_AGE_DAYS = 2
MAX_LIST_PAGES = 15             # 300 newest postings per list pass
_POSTED = re.compile(r"(?i)posted\s+(today|yesterday|(\d+)\+?\s+days?\s+ago)")
_REQ_SUFFIX = re.compile(r"_([A-Za-z]*-?\d[\w-]*)$")


def split_key(key: str) -> tuple[str, str, str]:
    tenant, wd, site = key.split("/", 2)
    return tenant, wd, site


def urls(key: str) -> tuple[str, str]:
    tenant, wd, site = split_key(key)
    base = f"https://{tenant}.{wd}.myworkdayjobs.com"
    return base, f"{base}/wday/cxs/{tenant}/{site}"


def parse_posted_on(text: str | None) -> tuple[int | None, bool]:
    """'Posted Today' -> (0, False); 'Posted 30+ Days Ago' -> (30, True); unknown -> (None, False)."""
    found = _POSTED.search(text or "")
    if not found:
        return None, False
    word = found.group(1).lower()
    if word == "today":
        return 0, False
    if word == "yesterday":
        return 1, False
    days = int(found.group(2))
    return days, "+" in found.group(1) or days >= CAPPED_DAYS


def ext_id(posting: dict) -> str:
    path = posting.get("externalPath") or ""
    found = _REQ_SUFFIX.search(path)
    if found:
        return found.group(1)
    bullets = posting.get("bulletFields") or []
    return bullets[0] if bullets else path


def _search(api: str, term: str) -> list[dict]:
    postings: list[dict] = []
    total = None
    for offset in range(0, TOP_N, PAGE_SIZE):
        body = {"appliedFacets": {}, "limit": PAGE_SIZE, "offset": offset, "searchText": term}
        page = post_json(f"{api}/jobs", body)
        batch = require_list(page, "jobPostings")
        total = total or page.get("total") or 0
        postings.extend(batch)
        if len(batch) < PAGE_SIZE or offset + PAGE_SIZE >= total:
            break
    return postings


_REQ_LIKE = re.compile(r"^[A-Za-z]{0,5}[-_ ]?\d{3,}[\w-]*$")


def list_location(posting: dict) -> str:
    """locationsText, else the non-requisition bulletFields ('R00347102', 'Ebene'), else the city in the path
    ('/job/Ebene/AI-ML-Engineer_R00347102'). Some tenants (e.g. Accenture) leave locationsText empty."""
    if posting.get("locationsText"):
        return posting["locationsText"]
    bullets = [b.strip() for b in posting.get("bulletFields") or [] if isinstance(b, str) and b.strip()]
    places = [b for b in bullets if not _REQ_LIKE.match(b)]
    if places:
        return "; ".join(places)
    parts = [p for p in (posting.get("externalPath") or "").split("/") if p]
    return parts[1].replace("-", " ") if len(parts) >= 3 and parts[0] == "job" else ""


def _normalize(board: dict, posting: dict, info: dict | None, today: date) -> dict:
    base, _ = urls(board["key"])
    site = split_key(board["key"])[2]
    days, capped = parse_posted_on(posting.get("postedOn"))
    posted = days_before(today, days) if days is not None else None
    info = info or {}
    if info.get("startDate"):
        posted, capped = day_iso(date.fromisoformat(info["startDate"][:10])), False
    extra = info.get("additionalLocations") or []
    location = "; ".join([info.get("location") or list_location(posting)] + extra).strip("; ")
    url = info.get("externalUrl") or f"{base}/{site}{posting.get('externalPath', '')}"
    return make_job(
        ats="workday", key=board["key"], ext_id=ext_id(posting),
        company=board["company"], title=posting["title"], location=location,
        country=(info.get("country") or {}).get("descriptor"), url=url, apply_url=url,
        req_id=info.get("jobReqId") or ext_id(posting), posted_at=posted, precision="day",
        description=html_to_text(info.get("jobDescription")), capped=capped, employment=info.get("timeType"),
    )


def list_cutoff_days(board: dict, today: date) -> int:
    """Read postings up to this many days old: since the last OK fetch, plus one day of slack (day precision)."""
    last = (board.get("last_ok_at") or "")[:10]
    if not last:
        return CAPPED_DAYS
    return max(1, min(CAPPED_DAYS, (today - date.fromisoformat(last)).days + 1))


def _newest(api: str, cutoff: int) -> tuple[list[dict], int]:
    """Newest-first pages until a page is entirely older than `cutoff` days; (postings, first page's total)."""
    postings: list[dict] = []
    total = 0
    for page_no in range(MAX_LIST_PAGES):
        offset = page_no * PAGE_SIZE
        if page_no and offset >= total:
            break
        page = post_json(f"{api}/jobs", {"appliedFacets": {}, "limit": PAGE_SIZE, "offset": offset, "searchText": ""})
        batch = require_list(page, "jobPostings")
        total = total or int(page.get("total") or 0)
        postings.extend(batch)
        ages = [parse_posted_on(p.get("postedOn"))[0] for p in batch]
        if len(batch) < PAGE_SIZE or all(a is not None and a > cutoff for a in ages):
            break
    return postings, total


def _collect(board: dict, api: str, ctx: Ctx) -> tuple[dict[str, dict], dict[str, set[str]], int | None]:
    seen: dict[str, dict] = {}
    found_by: dict[str, set[str]] = {}          # which search terms surfaced each posting (per-term seeding, C2)
    if board.get("task") == "list":
        postings, total = _newest(api, list_cutoff_days(board, ctx.today))
        for posting in postings:
            seen.setdefault(posting.get("externalPath") or posting.get("title", ""), posting)
        return seen, found_by, total
    for term in ctx.search_terms:
        for posting in _search(api, term):
            path = posting.get("externalPath") or posting.get("title", "")
            seen.setdefault(path, posting)
            found_by.setdefault(path, set()).add(f"term:{term}")
    return seen, found_by, None


def _item(board: dict, api: str, posting: dict, scopes: set[str], ctx: Ctx, notes: list[str]) -> dict:
    fp = raw_fp(posting, ctx.today.isoformat())       # relative dates ('Posted 3 Days Ago') change daily
    uid = f"workday:{board['key']}:{ext_id(posting)}"
    days, _ = parse_posted_on(posting.get("postedOn"))
    fresh_enough = days is not None and days <= DETAIL_MAX_AGE_DAYS
    wants = fresh_enough and ctx.matches(posting.get("title", "")) and ctx.needs_detail(uid)
    if ctx.unchanged(uid, fp) and not wants:          # an unchanged match still without its JD asks again
        return fast_item("workday", board["key"], ext_id(posting), posting.get("title", ""), fp)
    info = None
    if wants:
        try:
            info = (get_json(f"{api}{posting['externalPath']}") or {}).get("jobPostingInfo")
        except HttpError as err:
            notes.append(f"detail {posting.get('externalPath')}: {err}")
    item = {**_normalize(board, posting, info, ctx.today), "raw_fp": fp}
    return {**item, "scopes": sorted(scopes)} if scopes else item


def fetch(board: dict, ctx: Ctx) -> FetchResult:
    _, api = urls(board["key"])
    try:
        seen, found_by, total = _collect(board, api, ctx)
    except HttpError as err:
        return FetchResult([], err.status, error=str(err))
    except SchemaDrift as err:
        return schema_failure(err)
    items, bad, notes = [], 0, []
    for path, posting in seen.items():
        try:
            items.append(_item(board, api, posting, found_by.get(path, set()), ctx, notes))
        except (KeyError, TypeError, ValueError, AttributeError):
            bad += 1
    return FetchResult(items, 200, n_raw=len(seen), n_parse_err=bad, notes=notes, total=total)
