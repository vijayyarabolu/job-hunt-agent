"""Hunter.io (decision 012), off until `hunter.<person>_api_key` is in config/secrets.yaml. Domain Search: 1 credit per
1-10 emails returned, none when nothing comes back (help.hunter.io/en/articles/1970956, read 2026-10-06); the Free plan
caps limit + offset at 10, so one search spends at most 1 credit. Email Finder: 1 credit when an email is found, 0 when
not. Account information is free (searches / verifications left). Never a phone number."""
import math
import threading
import time

from . import http

BASE = "https://api.hunter.io/v2"
DOMAIN_LIMIT = 10                  # emails per Domain Search: the Free plan's maximum, and still 1 credit
EMAILS_PER_CREDIT = 10
ACCOUNT_TTL_S = 300                # the free account answer is kept 5 min (one per person, not per panel open)
ACCOUNT_CACHE: dict[str, tuple[float, dict]] = {}
_ACCOUNT_LOCK = threading.Lock()


def _headers(key: str) -> dict:
    return {"X-API-KEY": key}


def _person(entry: dict) -> dict:
    status = (entry.get("verification") or {}).get("status")
    score = entry.get("confidence") or entry.get("score")
    return {"name": f"{entry.get('first_name') or ''} {entry.get('last_name') or ''}".strip(),
            "title": entry.get("position") or "", "email": entry.get("value") or entry.get("email"),
            "email_status": status or (f"Hunter confidence {score}%" if score else None),
            "linkedin_url": entry.get("linkedin") or entry.get("linkedin_url"), "seniority": entry.get("seniority"),
            "source": "hunter"}


def domain_search(key: str, domain: str) -> tuple[dict, int]:
    """({'pattern': '{first}.{last}', 'people': [...]}, credits used)."""
    got = http.call("hunter", "GET", f"{BASE}/domain-search", _headers(key), {"domain": domain, "limit": DOMAIN_LIMIT, "type": "personal"})
    data = got.get("data") or {}
    people = [_person(e) for e in data.get("emails") or [] if isinstance(e, dict)]
    return {"pattern": data.get("pattern"), "people": people}, math.ceil(len(people) / EMAILS_PER_CREDIT)


def _left(block: dict | None) -> int | None:
    if not isinstance(block, dict) or block.get("available") is None:
        return None
    return max(int(block["available"]) - int(block.get("used") or 0), 0)


def account(pid: str, key: str, fresh: bool = False) -> dict:
    """The person's plan, searches / verifications left and reset date (0 credits; never the account email), kept
    ACCOUNT_TTL_S; {'error': ...} when Hunter refuses."""
    with _ACCOUNT_LOCK:
        hit = ACCOUNT_CACHE.get(pid)
        if hit and not fresh and time.monotonic() - hit[0] < ACCOUNT_TTL_S:
            return hit[1]
        try:
            data = http.call("hunter", "GET", f"{BASE}/account", _headers(key)).get("data") or {}
        except http.ProviderError as err:
            return {"error": f"Hunter: {f'HTTP {err.status}: ' if err.status else ''}{err.message}"}
        used = data.get("requests") or {}
        out = {"plan": data.get("plan_name"), "reset_date": data.get("reset_date"),
               "searches_left": _left(used.get("searches")), "verifications_left": _left(used.get("verifications")),
               "credits_left": _left(used.get("credits"))}
        ACCOUNT_CACHE[pid] = (time.monotonic(), out)
        return out


def email_finder(key: str, domain: str, first: str, last: str) -> tuple[dict | None, int]:
    """(the person with their email, credits used): 0 credits when Hunter finds nothing."""
    got = http.call("hunter", "GET", f"{BASE}/email-finder", _headers(key),
                    {"domain": domain, "first_name": first, "last_name": last})
    data = got.get("data") or {}
    if not data.get("email"):
        return None, 0
    return _person({**data, "value": data["email"]}), 1
