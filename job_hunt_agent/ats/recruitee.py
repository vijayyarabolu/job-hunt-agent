"""Recruitee career sites (`<company>.recruitee.com`, GOAL16 Tier A). `api/offers/` is the public careers API: every
published offer with its full description, one request for the whole board, so an absent job counts as a miss.
Tenant robots.txt allows it (checked 2026-10-08); every tenant shares one rate bucket (net.SHARED_BUCKETS).

Key '<company>' (the subdomain). published_at is 'YYYY-MM-DD HH:MM:SS UTC' (exact).
"""
from datetime import datetime, timezone

from ..net import HttpError, get_json
from ..text import html_to_text
from . import fulllist
from .common import Ctx, FetchResult, SchemaDrift, iso_utc, make_job, require_list, schema_failure


def list_url(key: str) -> str:
    return f"https://{key}.recruitee.com/api/offers/"


def offers(key: str) -> list[dict]:
    return [o for o in require_list(get_json(list_url(key)), "offers") if isinstance(o, dict) and o.get("id")]


def posted(offer: dict) -> str | None:
    text = str(offer.get("published_at") or offer.get("created_at") or "").replace(" UTC", "")
    try:
        return iso_utc(datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc))
    except ValueError:
        return None


def location(offer: dict) -> tuple[str, str | None]:
    places = [", ".join(str(p) for p in (loc.get("city"), loc.get("state"), loc.get("country")) if p)
              for loc in offer.get("locations") or [] if isinstance(loc, dict)]
    text = "; ".join(dict.fromkeys(p for p in places if p)) or offer.get("location") or ""
    codes = {loc.get("country_code") for loc in offer.get("locations") or [] if isinstance(loc, dict)} - {None, ""}
    country = codes.pop() if len(codes) == 1 else (offer.get("country_code") if not codes else None)
    return text, country


def _workplace(offer: dict) -> str | None:
    return "remote" if offer.get("remote") else "hybrid" if offer.get("hybrid") else "onsite" if offer.get(
        "on_site") else None


def _salary(offer: dict) -> tuple | None:
    pay = offer.get("salary") or {}
    try:
        low = float(pay.get("min")) if pay.get("min") else None
        high = float(pay.get("max")) if pay.get("max") else None
    except (TypeError, ValueError):
        return None
    if not low or (pay.get("period") or "year") != "year":
        return None
    return low, high, pay.get("currency") or "USD"


def _normalize(board: dict, offer: dict, _detail) -> dict:
    place, country = location(offer)
    url = offer.get("careers_url") or f"https://{board['key']}.recruitee.com/o/{offer.get('slug')}"
    when = posted(offer)
    body = "\n".join(str(offer.get(k) or "") for k in ("description", "requirements"))
    return make_job(
        ats="recruitee", key=board["key"], ext_id=offer["id"], company=board["company"], title=offer.get("title") or "",
        location=place, country=country, workplace=_workplace(offer), url=url,
        apply_url=offer.get("careers_apply_url") or url, team=offer.get("department"), salary=_salary(offer),
        posted_at=when, precision="exact" if when else "unknown", description=html_to_text(body),
        employment=offer.get("employment_type_code"),
    )


def fetch(board: dict, ctx: Ctx) -> FetchResult:
    try:
        listed = offers(board["key"])
    except HttpError as err:
        return FetchResult([], err.status, error=str(err))
    except SchemaDrift as err:
        return schema_failure(err)
    return fulllist.run(board, ctx, listed, id_of=lambda o: str(o["id"]), title_of=lambda o: o.get("title") or "",
                        normalize=_normalize, detail=None)


def survey(key: str) -> dict:
    listed = offers(key)
    name = next((o.get("company_name") for o in listed if o.get("company_name")), None)
    return {"n_jobs": len(listed), "name": name,
            "rows": [(posted(o), *location(o), o.get("title") or "") for o in listed]}
