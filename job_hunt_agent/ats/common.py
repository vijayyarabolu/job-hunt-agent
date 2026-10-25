"""Shared adapter types: fetch context, fetch result, and the normalized Job dict builder."""
import json
import zlib
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Callable

from .. import signals, workterms

MAX_DESCRIPTION_CHARS = 60_000


SCHEMA_ERROR = "schema:"           # FetchResult.error prefix: the ATS changed its JSON shape (classified as drift)


class SchemaDrift(ValueError):
    pass


def require_list(payload, key: str | None = None) -> list:
    """The job list of an ATS response, or SchemaDrift when the shape changed (a renamed key must never look like an
    empty board; release review C1). An empty list under the right key is a real empty board."""
    value = payload.get(key) if key and isinstance(payload, dict) else (payload if key is None else None)
    if not isinstance(value, list):
        raise SchemaDrift(f"expected a list{f' under {key!r}' if key else ''}, got {type(value).__name__}")
    return value


def schema_failure(err: SchemaDrift) -> "FetchResult":
    return FetchResult([], 200, error=f"{SCHEMA_ERROR} {err}", n_parse_err=1)


@dataclass(frozen=True)
class Ctx:
    """What an adapter needs from the outside world. Defaults make adapters usable stand-alone."""
    matches: Callable[[str], bool] = lambda title: True
    search_terms: tuple[str, ...] = ("machine learning engineer",)
    today: date = field(default_factory=date.today)
    needs_detail: Callable[[str], bool] = lambda uid: True   # False when the DB already has this uid's JD
    unchanged: Callable[[str, int], bool] = lambda uid, fp: False   # open row with the same raw fingerprint
    known_elsewhere: Callable[[str, str], bool] = lambda company, title: False   # GOAL35: open on another board


@dataclass
class FetchResult:
    items: list[dict]
    http_status: int | None = 200
    n_raw: int = 0
    n_parse_err: int = 0
    error: str | None = None
    notes: list[str] = field(default_factory=list)
    truncated: bool = False   # board larger than we read: never count misses
    not_modified: bool = False                 # 304: the list equals the last OK one (decision 005 B2)
    etag: str | None = None                    # validators of this full list response
    last_mod: str | None = None
    total: int | None = None                   # Workday: the first page's total (2000 = capped)


def raw_fp(posting, extra: str = "") -> int:
    """crc32 of the raw posting JSON (+ context such as today's date for relative Workday dates)."""
    return zlib.crc32((json.dumps(posting, sort_keys=True, default=str) + extra).encode())


def fast_item(ats: str, key: str, ext_id, title: str, fp: int) -> dict:
    """An unchanged posting: the store only refreshes last_seen (no normalization, no signals)."""
    return {"uid": f"{ats}:{key}:{ext_id}", "ext_id": str(ext_id), "title": title, "raw_fp": fp, "fast": True}


def iso_utc(moment: datetime | None) -> str | None:
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str | None) -> str | None:
    """Parse any ISO-8601 timestamp (with Z or offset) to canonical UTC text."""
    if not value:
        return None
    return iso_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


def from_epoch_ms(value: int | float | None) -> str | None:
    return iso_utc(datetime.fromtimestamp(value / 1000, tz=timezone.utc)) if value else None


def day_iso(day: date) -> str:
    """Day-precision timestamps keep the local calendar date; the time part carries no meaning."""
    return f"{day.isoformat()}T00:00:00Z"


def days_before(today: date, days: int) -> str:
    return day_iso(today - timedelta(days=days))


def _workplace(value: str | None) -> str | None:
    """'On-site' / 'onsite' / 'ONSITE' -> 'onsite'; 'Hybrid (travel-required)' -> 'hybrid'."""
    text = (value or "").lower().replace("-", "").replace("_", "").strip()
    for kind in ("remote", "hybrid", "onsite"):
        if text.startswith(kind):
            return kind
    return text or None


def make_job(*, ats: str, key: str, ext_id: str, company: str, title: str, location: str = "",
             country: str | None = None, workplace: str | None = None, url: str = "", apply_url: str = "",
             req_id: str | None = None, team: str | None = None, salary_text: str | None = None,
             salary: tuple | None = None, posted_at: str | None = None, precision: str = "exact",
             description: str = "", capped: bool = False, employment: str | None = None) -> dict:
    """Build one normalized Job dict, deriving the deterministic signals from the raw fields."""
    body = (description or "")[:MAX_DESCRIPTION_CHARS]
    smin, smax, sccy = salary if salary and salary[0] else signals.parse_salary(f"{salary_text or ''}\n{body}")
    return {
        "uid": f"{ats}:{key}:{ext_id}",
        "ats": ats, "key": key, "ext_id": str(ext_id), "company": company, "title": title.strip(),
        "location": location or "", "country": country, "is_us": signals.is_us(location, country),
        "workplace": _workplace(workplace), "url": url, "apply_url": apply_url or url,
        "req_id": req_id or None, "has_team": signals.has_named_team(body, team),
        "salary_min": smin, "salary_max": smax, "salary_ccy": sccy,
        "min_years": signals.min_years(body), "is_talent_pool": signals.is_talent_pool(title, body),
        "employment_type": signals.employment_type(employment, title, body),
        "staffing": signals.staffing_signals(body), "sponsorship": signals.sponsorship(body),
        "work_terms": workterms.label(body),
        "posted_at": posted_at, "precision": precision, "description": body, "capped": capped,
    }
