"""BambooHR career sites (`<company>.bamboohr.com/careers`, GOAL16 Tier A). Tenant robots.txt (checked 2026-10-08)
blocks only /jobs/embed*.php; the per-request gate still checks every URL. Every tenant shares one rate bucket
(net.SHARED_BUCKETS), so thousands of small boards stay one polite rate on BambooHR.

Key '<company>' (the subdomain). `careers/list` is the whole board in one request (id, title, department, employment
status, location, remote flag; no date, no description), so an absent job counts as a miss. A title-matched posting
that is new or changed costs one request for `careers/<id>/detail`: the description, datePosted (a day) and the
compensation text. A dead board answers 302 (to the marketing site, login.php or an 'expired' page); redirects are
never followed, and the 3xx is read as 404, so the board goes the way of any dead board.
"""
from datetime import date

from .. import signals
from ..net import REDIRECTS, HttpError, get_json
from ..text import html_to_text
from . import fulllist
from .common import Ctx, FetchResult, SchemaDrift, day_iso, make_job, require_list, schema_failure

LIST = "https://{key}.bamboohr.com/careers/list"
DETAIL = "https://{key}.bamboohr.com/careers/{id}/detail"
PAGE = "https://{key}.bamboohr.com/careers/{id}"
_WORKPLACE = {"0": "onsite", "1": "remote", "2": "hybrid"}
GONE = 404


def list_url(key: str) -> str:
    return LIST.format(key=key)


def _get(url: str):
    try:
        return get_json(url, redirects=False)
    except HttpError as err:
        if err.status in REDIRECTS:
            raise HttpError(url, GONE, f"redirected off the board ({err.status}): no such BambooHR board") from err
        raise


def postings(key: str) -> list[dict]:
    payload = _get(list_url(key))
    return [p for p in require_list(payload, "result") if isinstance(p, dict) and p.get("id") is not None]


def detail(key: str, job_id: str) -> dict | None:
    found = ((_get(DETAIL.format(key=key, id=job_id)) or {}).get("result") or {}).get("jobOpening")
    return found if isinstance(found, dict) else None


def location(post: dict) -> tuple[str, str | None]:
    """'Tampa, Florida, United States' and the country; the ATS location wins over the legacy one."""
    ats_loc, legacy = post.get("atsLocation") or {}, post.get("location") or {}
    city = ats_loc.get("city") or legacy.get("city")
    region = ats_loc.get("state") or ats_loc.get("province") or legacy.get("state")
    country = ats_loc.get("country") or legacy.get("addressCountry")
    return ", ".join(str(p) for p in (city, region, country) if p), country or None


def _workplace(post: dict) -> str | None:
    if post.get("isRemote"):
        return "remote"
    return _WORKPLACE.get(str(post.get("locationType") or ""))


def posted(found: dict | None) -> str | None:
    text = (found or {}).get("datePosted")
    try:
        return day_iso(date.fromisoformat(text[:10])) if isinstance(text, str) and text else None
    except ValueError:
        return None


def _normalize(board: dict, post: dict, found: dict | None) -> dict:
    found = found or {}
    place, country = location({**post, **{k: v for k, v in found.items() if k in ("atsLocation", "location") and v}})
    url = found.get("jobOpeningShareUrl") or PAGE.format(key=board["key"], id=post["id"])
    when = posted(found)
    return make_job(
        ats="bamboohr", key=board["key"], ext_id=post["id"], company=board["company"],
        title=(found.get("jobOpeningName") or post.get("jobOpeningName") or "").strip(), location=place,
        country=country, workplace=_workplace({**post, **found}), url=url, apply_url=url,
        team=post.get("departmentLabel"), salary_text=found.get("compensation"),
        posted_at=when, precision="day" if when else "unknown", description=html_to_text(found.get("description")),
        employment=found.get("employmentStatusLabel") or post.get("employmentStatusLabel"),
    )


def fetch(board: dict, ctx: Ctx) -> FetchResult:
    key = board["key"]
    try:
        listed = postings(key)
    except HttpError as err:
        return FetchResult([], err.status, error=str(err))
    except SchemaDrift as err:
        return schema_failure(err)
    return fulllist.run(board, ctx, listed, id_of=lambda p: str(p["id"]),
                        title_of=lambda p: (p.get("jobOpeningName") or "").strip(), normalize=_normalize,
                        detail=lambda p: detail(key, p["id"]),
                        wants_detail=lambda p: signals.is_us(*location(p)) is not False)


def survey(key: str) -> dict:
    """Discovery: the open postings (the list has no dates: an open posting is current)."""
    listed = postings(key)
    return {"n_jobs": len(listed),
            "rows": [(None, *location(p), (p.get("jobOpeningName") or "").strip()) for p in listed]}
