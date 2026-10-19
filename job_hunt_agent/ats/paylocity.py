"""Paylocity recruiting boards (`recruiting.paylocity.com/Recruiting/Jobs/All/<companyGuid>`, GOAL16 Tier B:
mid-market employers, healthcare, property management, credit unions). recruiting.paylocity.com/robots.txt answers
404 (no rules, checked 2026-10-08); the per-request gate still checks every URL.

Key '<companyGuid>'. The board page is server-rendered with `window.pageData = {...}` holding every open job (id,
title, published date, location, remote flag and a short summary), one request for the whole board, so an absent job
counts as a miss. A title-matched job that is new or changed costs one request for its page, whose schema.org
JobPosting gives the full description, the employer's name and the pay.
"""
import json
import re

from .. import signals
from ..net import HttpError, get_text
from ..text import html_to_text
from . import fulllist, jsonld
from .common import Ctx, FetchResult, SchemaDrift, make_job, parse_iso, schema_failure

BASE = "https://recruiting.paylocity.com/Recruiting/Jobs"
_PAGE_DATA = re.compile(r"window\.pageData\s*=\s*")


def list_url(key: str) -> str:
    return f"{BASE}/All/{key}"


def job_url(job_id) -> str:
    return f"{BASE}/Details/{job_id}"


def page_data(page: str) -> dict:
    """The board's window.pageData object; SchemaDrift when the page has none (not a Paylocity board any more)."""
    found = _PAGE_DATA.search(page or "")
    if not found:
        raise SchemaDrift("expected window.pageData on a Paylocity board")
    try:
        data, _ = json.JSONDecoder().raw_decode(page, found.end())
    except ValueError as err:
        raise SchemaDrift(f"unreadable window.pageData: {err}") from err
    if not isinstance(data, dict) or not isinstance(data.get("Jobs"), list):
        raise SchemaDrift("window.pageData has no Jobs list")
    return data


def jobs(key: str) -> list[dict]:
    return [j for j in page_data(get_text(list_url(key)))["Jobs"]
            if isinstance(j, dict) and j.get("JobId") is not None and not j.get("IsInternal")]


def location(job: dict) -> tuple[str, str | None]:
    place = job.get("JobLocation") or {}
    country = place.get("Country")
    parts = [place.get("City"), place.get("State"), country]
    text = ", ".join(str(p) for p in parts if p) or job.get("LocationName") or ""
    return text, country


def _detail(job_id) -> dict | None:
    return jsonld.job_posting(get_text(job_url(job_id)))


def _salary_text(posting: dict) -> str | None:
    pay = posting.get("baseSalary") or {}
    value = pay.get("value") if isinstance(pay, dict) else None
    if not isinstance(value, dict):
        return None
    low, high = value.get("minValue") or value.get("value"), value.get("maxValue")
    unit = value.get("unitText") or ""
    if not low:
        return None
    return f"${low}{f' - ${high}' if high else ''} {pay.get('currency') or 'USD'} per {unit.lower() or 'year'}"


def _normalize(board: dict, job: dict, posting: dict | None) -> dict:
    posting = posting or {}
    place, country = location(job)
    url = job_url(job["JobId"])
    return make_job(
        ats="paylocity", key=board["key"], ext_id=job["JobId"], company=board["company"],
        title=html_to_text(posting.get("title")) or job.get("JobTitle") or "", location=place, country=country,
        workplace="remote" if job.get("IsRemote") else None, url=url, apply_url=f"{BASE}/Apply/{job['JobId']}",
        team=job.get("HiringDepartment"), salary_text=_salary_text(posting),
        posted_at=parse_iso(job.get("PublishedDate")), precision="exact" if job.get("PublishedDate") else "unknown",
        description=html_to_text(posting.get("description")) if posting else "",
        employment=jsonld.employment(posting) if posting else None,
    )


def fetch(board: dict, ctx: Ctx) -> FetchResult:
    try:
        listed = jobs(board["key"])
    except HttpError as err:
        return FetchResult([], err.status, error=str(err))
    except SchemaDrift as err:
        return schema_failure(err)
    return fulllist.run(board, ctx, listed, id_of=lambda j: str(j["JobId"]), title_of=lambda j: j.get("JobTitle") or "",
                        normalize=_normalize, detail=lambda j: _detail(j["JobId"]),
                        wants_detail=lambda j: signals.is_us(*location(j)) is not False)


def survey(key: str) -> dict:
    """Discovery: every job (dated) and the employer named by one job page's JSON-LD."""
    listed = jobs(key)
    name = None
    if listed:
        try:
            name = jsonld.organization(_detail(listed[0]["JobId"]) or {})
        except HttpError:
            name = None
    return {"n_jobs": len(listed), "name": name,
            "rows": [(parse_iso(j.get("PublishedDate")), *location(j), j.get("JobTitle") or "") for j in listed]}
