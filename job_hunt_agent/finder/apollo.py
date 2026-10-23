"""Apollo.io (decision 012; GOAL28 §5). One documented call: People API Search, POST /api/v1/mixed_people/api_search
(docs.apollo.io/reference/people-api-search, read 2026-10-18): people at an organization domain with given titles,
0 credits; it returns no emails or phones (only has_email), and last names obfuscated. A Free plan is refused (HTTP
403 API_INACCESSIBLE, seen 2026-10-18): the finder records that once a day and moves on to Hunter. The
undocumented organization_top_people call and the 500-employee cap are gone. Only what the panel shows is kept:
never a phone number, never Apollo's locked placeholder email."""
from . import http

BASE = "https://api.apollo.io/api/v1"
SEARCH = "mixed_people/api_search"
SEARCH_CREDITS = 0                 # documented: "This endpoint does not consume credits" (one real call: PROGRESS28)
PER_KIND = 5                       # people asked for per kind (one page)
PERSON_FIELDS = ("id", "title", "headline", "linkedin_url", "city", "state", "country", "seniority", "email_status",
                 "has_email")
LOCKED_EMAIL = "email_not_unlocked"


def _headers(key: str) -> dict:
    return {"x-api-key": key, "Cache-Control": "no-cache"}


def health(key: str) -> dict:
    """GET auth/health: 0 credits (checks the key)."""
    return http.call("apollo", "GET", f"{BASE}/auth/health", _headers(key))


def people_search(key: str, domain: str, titles: tuple[str, ...], per_page: int = PER_KIND) -> tuple[list[dict], int]:
    """(people at `domain` whose current title matches one of `titles`, Apollo's total count)."""
    params = {"q_organization_domains_list[]": [domain], "person_titles[]": list(titles), "page": 1,
              "per_page": per_page}
    got = http.call("apollo", "POST", f"{BASE}/{SEARCH}", _headers(key), params)
    people = [person(p) for p in got.get("people") or [] if isinstance(p, dict)]
    return people, int(got.get("total_entries") or len(people))


def person(raw: dict) -> dict:
    """An Apollo person as the panel keeps it (whitelisted fields only)."""
    last = raw.get("last_name") or raw.get("last_name_obfuscated") or ""
    name = (raw.get("name") or f"{raw.get('first_name') or ''} {last}").strip()
    email = raw.get("email") or None
    if email and LOCKED_EMAIL in email:
        email = None
    return {**{f: raw.get(f) for f in PERSON_FIELDS}, "name": name, "email": email, "source": "apollo"}
