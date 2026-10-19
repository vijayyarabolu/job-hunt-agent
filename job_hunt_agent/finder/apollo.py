"""Apollo.io (decision 012). The people's scoped Free-plan keys allow organizations/search, organizations/enrich and
mixed_people/organization_top_people (people search / match are locked on Free). Only what the panel shows is kept:
never a phone number, never Apollo's locked placeholder email."""
from . import http

BASE = "https://api.apollo.io/api/v1"
ENRICH_CREDITS = 1                 # docs.apollo.io/docs/api-pricing: "1 credit per organization"
TOP_PEOPLE_CREDITS = 1             # not documented by Apollo: counted as 1 so the ledger never under-counts
ORG_FIELDS = ("id", "name", "primary_domain", "website_url", "estimated_num_employees", "industry", "linkedin_url",
              "city", "state", "country")
PERSON_FIELDS = ("id", "title", "headline", "linkedin_url", "city", "state", "country", "seniority", "email_status")
LOCKED_EMAIL = "email_not_unlocked"
WRONG_METHOD = (404, 405)


def _headers(key: str) -> dict:
    return {"x-api-key": key, "Cache-Control": "no-cache"}


def health(key: str) -> dict:
    """GET auth/health: 0 credits (checks the key)."""
    return http.call("apollo", "GET", f"{BASE}/auth/health", _headers(key))


def enrich_org(key: str, domain: str) -> dict | None:
    """The organization behind a domain (1 credit), or None when Apollo does not know it."""
    got = http.call("apollo", "GET", f"{BASE}/organizations/enrich", _headers(key), {"domain": domain})
    org = got.get("organization")
    return {f: org.get(f) for f in ORG_FIELDS} if isinstance(org, dict) and org.get("id") else None


def top_people(key: str, org_id: str) -> tuple[list[dict], dict]:
    """(the organization's top people: names, titles, LinkedIn; no emails or phones, the answer's layout)."""
    url, params = f"{BASE}/mixed_people/organization_top_people", {"organization_id": org_id}
    try:
        got = http.call("apollo", "GET", url, _headers(key), params)
    except http.ProviderError as err:              # the endpoint is undocumented: a wrong method is refused, not paid
        if err.status not in WRONG_METHOD:
            raise
        got = http.call("apollo", "POST", url, _headers(key), body=params)
    return [person(p) for p in _people_in(got)], shape(got)


def _people_in(got) -> list[dict]:
    """The person records in an answer whose layout Apollo does not document: the first list of records with a name
    or a title, at the top level or one level down (people, contacts, top_people, ...)."""
    if not isinstance(got, dict):
        return []
    places = [*got.values(), *(v for d in got.values() if isinstance(d, dict) for v in d.values())]
    for value in places:
        if isinstance(value, list) and any(isinstance(p, dict) and (p.get("first_name") or p.get("name") or
                                                                     p.get("title")) for p in value):
            return [p for p in value if isinstance(p, dict)]
    return []


def shape(got) -> dict:
    """The answer's layout without its data (key -> 'list[n]' / type; counts and flags as they are), kept to explain
    an empty answer."""
    if not isinstance(got, dict):
        return {"answer": type(got).__name__}
    def kind(v):
        if isinstance(v, list):
            return f"list[{len(v)}]"
        if isinstance(v, dict):
            return {k: kind(x) for k, x in v.items()}
        return v if isinstance(v, (int, float, bool)) or v is None else type(v).__name__
    return {k: kind(v) for k, v in got.items()}


def person(raw: dict) -> dict:
    """An Apollo person as the panel keeps it (whitelisted fields only)."""
    last = raw.get("last_name") or raw.get("last_name_obfuscated") or ""
    name = (raw.get("name") or f"{raw.get('first_name') or ''} {last}").strip()
    email = raw.get("email") or None
    if email and LOCKED_EMAIL in email:
        email = None
    return {**{f: raw.get(f) for f in PERSON_FIELDS}, "name": name, "email": email, "source": "apollo"}
