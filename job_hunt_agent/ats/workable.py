"""Workable adapter (decision 011): the documented public jobs widget
(https://apply.workable.com/api/v1/widget/accounts/<slug>?details=true), one request for the whole board with
descriptions. A full list, so a job absent from an ok fetch counts as a miss like Greenhouse/Lever/Ashby.
"""
from datetime import date
from urllib.parse import quote

from ..net import HttpError, get_json
from ..text import html_to_text
from .common import Ctx, FetchResult, SchemaDrift, day_iso, fast_item, make_job, raw_fp, require_list, schema_failure

URL = "https://apply.workable.com/api/v1/widget/accounts/{key}?details=true"


def _day(text: str | None) -> str | None:
    try:
        return day_iso(date.fromisoformat(text)) if text else None
    except ValueError:
        return None


def _location(job: dict) -> tuple[str, str | None]:
    places = [p for p in job.get("locations") or [] if isinstance(p, dict) and not p.get("hidden")]
    if places:
        text = "; ".join(", ".join(x for x in (p.get("city"), p.get("region"), p.get("country")) if x) for p in places)
        codes = {p.get("countryCode") for p in places if p.get("countryCode")}
        return text, (codes.pop() if len(codes) == 1 else None)
    return ", ".join(x for x in (job.get("city"), job.get("state"), job.get("country")) if x), None


def _normalize(board: dict, job: dict) -> dict:
    location, country = _location(job)
    remote = str(job.get("telecommuting")).lower() == "true"
    return make_job(
        ats="workable", key=board["key"], ext_id=job["shortcode"], company=board["company"],
        title=job.get("title") or "", location=location, country=country, workplace="remote" if remote else None,
        url=job.get("url") or "", apply_url=job.get("application_url") or job.get("url") or "",
        req_id=job.get("code") or None, team=job.get("department"),
        posted_at=_day(job.get("published_on") or job.get("created_at")), precision="day",
        description=html_to_text(job.get("description")), employment=job.get("employment_type"),
    )


def fetch(board: dict, ctx: Ctx) -> FetchResult:
    try:
        jobs = require_list(get_json(URL.format(key=quote(board["key"], safe=""))), "jobs")
    except HttpError as err:
        return FetchResult([], err.status, error=str(err))
    except SchemaDrift as err:
        return schema_failure(err)
    items, bad = [], 0
    for job in jobs:
        try:
            fp = raw_fp(job)
            uid = f"workable:{board['key']}:{job['shortcode']}"
            items.append(fast_item("workable", board["key"], job["shortcode"], job.get("title", ""), fp)
                         if ctx.unchanged(uid, fp) else {**_normalize(board, job), "raw_fp": fp})
        except (KeyError, TypeError, ValueError, AttributeError):
            bad += 1
    return FetchResult(items, 200, n_raw=len(jobs), n_parse_err=bad)
