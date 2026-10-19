"""ADP Workforce Now career centers (`workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid=..&ccId=..`,
GOAL16 Tier B: mid-market employers, hospitals, banks, nonprofits). The career center's own page reads the anonymous
public endpoint `careercenter/public/events/staffing/v1/job-requisitions`. workforcenow.adp.com/robots.txt redirects
to a login page (no rules, checked 2026-10-08); the per-request gate still checks every URL.

Key '<cid>/<ccId>' (entry: token = cid, ccid = ccId). The list pages 20 requisitions at a time ($skip is a 1-based row
offset; $top above 20 is ignored) with title, posting date, locations and pay range; reading every page to the total is
the full list, so an absent job counts as a miss (a list cut at MAX_PAGES is marked truncated: no misses). A
title-matched requisition that is new or changed costs one request for its description.
"""
from urllib.parse import quote

from .. import signals
from ..net import HttpError, get_json
from ..text import html_to_text
from . import fulllist
from .common import Ctx, FetchResult, SchemaDrift, make_job, parse_iso, require_list, schema_failure

API = "https://workforcenow.adp.com/mascsr/default/careercenter/public/events/staffing/v1/job-requisitions"
PAGE = "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html"
PER_PAGE = 20
MAX_PAGES = 25
ANNUAL_FLOOR = 10_000          # a pay range below this is hourly: never read as a yearly salary


def split_key(key: str) -> tuple[str, str]:
    cid, ccid = key.split("/", 1)
    return cid, ccid


def _query(key: str) -> str:
    cid, ccid = split_key(key)
    return f"cid={quote(cid)}&ccId={quote(ccid)}&lang=en_US&locale=en_US"


def list_url(key: str, skip: int = 1) -> str:
    return f"{API}?{_query(key)}&$top={PER_PAGE}&$skip={skip}"


def job_url(key: str, item_id: str) -> str:
    return f"{PAGE}?{_query(key)}&jobId={quote(str(item_id))}"


def listing(key: str) -> tuple[list[dict], int, bool]:
    """(requisitions, total, truncated)."""
    found: dict[str, dict] = {}
    total = 0
    for page in range(MAX_PAGES):
        payload = get_json(list_url(key, 1 + page * PER_PAGE))
        batch = [r for r in require_list(payload, "jobRequisitions") if isinstance(r, dict) and r.get("itemID")]
        total = int(((payload.get("meta") or {}).get("totalNumber")) or 0)
        before = len(found)
        found.update({str(r["itemID"]): r for r in batch})
        if len(batch) < PER_PAGE or len(found) >= total or len(found) == before:
            return list(found.values()), total, False
    return list(found.values()), total, len(found) < total


def places(req: dict) -> list[str]:
    out = []
    for loc in req.get("requisitionLocations") or []:
        if not isinstance(loc, dict):
            continue
        name = " ".join(str((loc.get("nameCode") or {}).get("shortName") or "").split())
        if not name:
            addr = loc.get("address") or {}
            region = (addr.get("countrySubdivisionLevel1") or {}).get("codeValue")
            name = ", ".join(str(p) for p in (addr.get("cityName"), region) if p)
        if name:
            out.append(name)
    return list(dict.fromkeys(out))


def location(req: dict) -> str:
    return "; ".join(places(req))


def _salary(req: dict) -> tuple | None:
    pay = req.get("payGradeRange") or {}
    low, high = (pay.get("minimumRate") or {}), (pay.get("maximumRate") or {})
    lo, hi = low.get("amountValue"), high.get("amountValue")
    if not isinstance(lo, (int, float)) or lo < ANNUAL_FLOOR:
        return None
    return float(lo), float(hi) if isinstance(hi, (int, float)) else None, low.get("currencyCode") or "USD"


def detail(key: str, item_id: str) -> dict | None:
    found = get_json(f"{API}/{quote(str(item_id))}?{_query(key)}")
    return found if isinstance(found, dict) and found.get("itemID") else None


def _normalize(board: dict, req: dict, found: dict | None) -> dict:
    found = found or {}
    url = job_url(board["key"], req["itemID"])
    return make_job(
        ats="adp", key=board["key"], ext_id=req["itemID"], company=board["company"],
        title=req.get("requisitionTitle") or "", location=location(req), url=url, apply_url=url,
        req_id=req.get("clientRequisitionID"), salary=_salary(req), posted_at=parse_iso(req.get("postDate")),
        precision="exact" if req.get("postDate") else "unknown",
        description=html_to_text(found.get("requisitionDescription")),
        employment=(req.get("workLevelCode") or {}).get("shortName"),
    )


def fetch(board: dict, ctx: Ctx) -> FetchResult:
    key = board["key"]
    try:
        listed, total, truncated = listing(key)
    except HttpError as err:
        return FetchResult([], err.status, error=str(err))
    except SchemaDrift as err:
        return schema_failure(err)
    notes = [f"truncated at {len(listed)} of {total} requisitions"] if truncated else []
    return fulllist.run(board, ctx, listed, id_of=lambda r: str(r["itemID"]),
                        title_of=lambda r: r.get("requisitionTitle") or "", normalize=_normalize,
                        detail=lambda r: detail(key, r["itemID"]), truncated=truncated, notes=notes,
                        wants_detail=lambda r: signals.is_us(location(r), None) is not False)


def survey(key: str) -> dict:
    """Discovery: every requisition's posting date and places (the list is not newest first); ADP names no company
    in this API."""
    reqs, total, _ = listing(key)
    return {"n_jobs": total or len(reqs),
            "rows": [(parse_iso(r.get("postDate")), location(r), None, r.get("requisitionTitle") or "") for r in reqs]}
