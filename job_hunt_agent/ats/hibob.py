"""HiBob career sites (`<tenant>.careers.hibob.com`, GOAL16 Tier A: tech mid-market). `api/job-ad` is every open job ad
with its full sections, one request for the whole board, so an absent job counts as a miss. It answers 401 without a
Referer from the tenant's own /jobs page. Tenant robots.txt is the site's HTML shell (no rules; checked 2026-10-08);
every tenant shares one rate bucket (net.SHARED_BUCKETS).

Key '<tenant>' (the subdomain). publishedAt is ISO with nanoseconds (exact). country is a country name.
"""
from ..net import HttpError, get_json
from ..text import html_to_text
from . import fulllist
from .common import Ctx, FetchResult, SchemaDrift, make_job, parse_iso, require_list, schema_failure

SECTIONS = ("description", "responsibilities", "requirements", "benefits")


def list_url(key: str) -> str:
    return f"https://{key}.careers.hibob.com/api/job-ad"


def job_url(key: str, ad_id: str) -> str:
    return f"https://{key}.careers.hibob.com/jobs/{ad_id}"


def ads(key: str) -> list[dict]:
    payload = get_json(list_url(key), headers={"Referer": f"https://{key}.careers.hibob.com/jobs"})
    return [a for a in require_list(payload, "jobAdDetails") if isinstance(a, dict) and a.get("id")]


def location(ad: dict) -> tuple[str, str | None]:
    site, country = ad.get("site") or "", ad.get("country") or None
    return (f"{site}, {country}" if site and country and country not in site else site or country or ""), country


def _salary(ad: dict) -> tuple | None:
    low = ad.get("payTransparencyMinSalary")
    period = str(ad.get("payTransparencySalaryPayPeriod") or "year").lower()
    if not isinstance(low, (int, float)) or not low or not period.startswith(("year", "annual")):
        return None
    high = ad.get("payTransparencyMaxSalary")
    return float(low), float(high) if isinstance(high, (int, float)) else None, \
        ad.get("payTransparencySalaryCurrency") or "USD"


def _normalize(board: dict, ad: dict, _detail) -> dict:
    place, country = location(ad)
    url = job_url(board["key"], ad["id"])
    when = parse_iso(ad.get("publishedAt"))
    return make_job(
        ats="hibob", key=board["key"], ext_id=ad["id"], company=board["company"], title=ad.get("title") or "",
        location=place, country=country, workplace=ad.get("workspaceTypeId") or None, url=url, apply_url=url,
        team=ad.get("department"), salary=_salary(ad), posted_at=when, precision="exact" if when else "unknown",
        description=html_to_text("\n".join(str(ad.get(k) or "") for k in SECTIONS)),
        employment=ad.get("employmentType"),
    )


def fetch(board: dict, ctx: Ctx) -> FetchResult:
    try:
        listed = ads(board["key"])
    except HttpError as err:
        return FetchResult([], err.status, error=str(err))
    except SchemaDrift as err:
        return schema_failure(err)
    return fulllist.run(board, ctx, listed, id_of=lambda a: str(a["id"]), title_of=lambda a: a.get("title") or "",
                        normalize=_normalize, detail=None)


def survey(key: str) -> dict:
    listed = ads(key)
    return {"n_jobs": len(listed),
            "rows": [(parse_iso(a.get("publishedAt")), *location(a), a.get("title") or "") for a in listed]}
