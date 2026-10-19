"""The Find contacts panel (decision 012). panel() is free and makes no network call; lookup() runs one
source on the person's click (their own key, the cost shown before, the daily cap checked first, the answer cached so
it is never paid twice); add() saves a contact into the tracker's contacts (-> the sheet's Target Name / Role / Email
cells through the sync)."""
import re
import threading

from .. import contacts, db, jdsnap, net, tracker, trackapi
from . import apollo, hunter, keys, ledger, rank, signals
from .http import ProviderError

SOURCES = ("jd", "team_page", "apollo", "hunter", "linkedin-manual", "guess", "manual")
MAX_EMPLOYEES = 500                       # user: Apollo top people only for companies under ~500 employees
DOMAIN_RX = re.compile(r"^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$")
ENRICH, TOP, DOMAIN_SEARCH, TEAM_PAGE = ("organizations/enrich", "mixed_people/organization_top_people",
                                         "domain-search", "team_page")
DOMAIN_CALLS = (ENRICH, DOMAIN_SEARCH, TEAM_PAGE)
TEAM_PATHS = ("/team", "/about", "/about-us", "/company", "/leadership")
APOLLO_CREDITS_PAGE = "https://app.apollo.io/#/settings/credits/current"
CONTACT_FIELDS = ("name", "role", "email", "linkedin", "notes")
PRIVATE_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".home", ".corp", ".test", ".example", ".invalid",
                    ".intranet", ".home.arpa")
_LOOKUP_LOCK = threading.Lock()           # one lookup at a time: cache, cap, call and ledger in one step


def clean_domain(text: str | None) -> str:
    domain = re.sub(r"^https?://", "", (text or "").strip().lower()).split("/")[0]
    domain = domain[4:] if domain.startswith("www.") else domain
    if not DOMAIN_RX.match(domain) or domain.endswith(PRIVATE_SUFFIXES):
        raise ValueError(f"not a company domain: {text!r} (for example acme.com)")
    return domain


def _row_of_uid(conn, pid: str, uid: str) -> int | None:
    """The person's tracker row for a feed job, when one exists (Apply ↗, Mark applied or a saved contact made it)."""
    found = conn.execute("SELECT id FROM applications WHERE profile = ? AND job_uid = ?", (pid, uid)).fetchone()
    return found["id"] if found else None


def _target(conn, pid: str, app_id: int | None, uid: str | None) -> tuple[int | None, str | None]:
    """a panel is keyed by a tracker row (app) or, for any feed job, by its uid; a uid whose row already
    exists uses that row. Nothing is created here."""
    if app_id is not None:
        return app_id, uid
    if not uid:
        raise ValueError("Find contacts needs an app or uid")
    if conn.execute("SELECT 1 FROM jobs WHERE uid = ?", (uid,)).fetchone() is None:
        raise KeyError(uid)
    return _row_of_uid(conn, pid, uid), uid


def _job_context(conn, uid: str) -> dict:
    """What a feed job without a tracker row gives the finder: company, role, links, location, the JD text."""
    job = dict(conn.execute("SELECT company, title, url, apply_url, location, families, description FROM jobs "
                            "WHERE uid = ?", (uid,)).fetchone())
    family = next((f for f in (job.get("families") or "").split(",") if f), None)
    row = {"company": job["company"], "role_title": job["title"], "job_url": job["url"], "location": job["location"],
           "family": family, "contacts": []}
    return {"row": row, "text": db.unpack(job.get("description")),
            "links": [u for u in (job["url"], job["apply_url"]) if u],
            "job": {"role_title": job["title"], "location": job["location"], "family": family}}


def _context(conn, pid: str, app_id: int | None, uid: str | None = None) -> dict:
    if app_id is None:
        return _job_context(conn, uid)
    row = trackapi.get(conn, pid, app_id)
    snap = jdsnap.get(conn, app_id) or {}
    job = {}
    if row.get("job_uid"):
        found = conn.execute("SELECT url, apply_url, location, description FROM jobs WHERE uid = ?",
                             (row["job_uid"],)).fetchone()
        job = dict(found) if found else {}
    text = snap.get("text") or row.get("jd") or job.get("description") or ""
    links = [u for u in (row.get("job_url"), job.get("url"), job.get("apply_url")) if u]   # the posting's own links
    return {"row": row, "text": text, "links": links,
            "job": {"role_title": row.get("role_title"), "location": row.get("location") or job.get("location"),
                    "family": row.get("family")}}


def _cached_people(conn, pid: str, domain: str) -> tuple[list[dict], dict | None, str | None]:
    org = (ledger.cached(conn, pid, "apollo", ENRICH, domain) or {}).get("org")
    people = (ledger.cached(conn, pid, "free", TEAM_PAGE, domain) or {}).get("people", [])
    if org:
        people = people + (ledger.cached(conn, pid, "apollo", TOP, org["id"]) or {}).get("people", [])
    found = ledger.cached(conn, pid, "hunter", DOMAIN_SEARCH, domain) or {}
    return people + found.get("people", []), org, found.get("pattern")


def _email_status(person: dict) -> str | None:
    if not person.get("email"):
        return None
    return "named in the JD" if person["source"] == "jd" else person.get("email_status") or "unverified"


def _suggestions(ctx: dict, people: list[dict], domain: str | None, pattern: str | None, org: dict | None) -> list:
    merged: dict[str, dict] = {}
    for p in people:
        merged.setdefault((p.get("name") or "").lower() or f"{p.get('title')}|{p.get('email')}", p)
    out = []
    for p in merged.values():
        item = {"name": p.get("name") or "", "title": p.get("title") or "", "email": p.get("email"),
                "email_status": _email_status(p), "linkedin": p.get("linkedin_url"), "source": p["source"],
                "note": p.get("note"), "city": p.get("city")}
        guess = signals.guess_email(item["name"], domain, pattern) if item["name"] and not item["email"] else None
        if guess:
            item.update(email_guess=guess, email_guess_status=signals.GUESS_LABEL)
        out.append(item)
    return rank.rank(out, {**ctx["job"], "company_size": (org or {}).get("estimated_num_employees")})


def _apollo_estimate(conn, pid: str, domain: str | None) -> dict:
    """What the Apollo button would spend now (cached answers are free)."""
    undocumented = "organization_top_people: cost not documented by Apollo, counted as 1"
    if not domain:
        return {"credits": 0, "detail": "enter the company domain first"}
    hit = ledger.cached(conn, pid, "apollo", ENRICH, domain)
    if hit is None:
        return {"credits": apollo.ENRICH_CREDITS + apollo.TOP_PEOPLE_CREDITS,
                "detail": f"organizations/enrich: 1 credit (documented); {undocumented}"}
    org = hit.get("org")
    if not org:
        return {"credits": 0, "detail": "Apollo does not know this domain (already asked)"}
    if (org.get("estimated_num_employees") or 0) > MAX_EMPLOYEES:
        return {"credits": 0, "detail": f"over {MAX_EMPLOYEES} employees: top people skipped"}
    top = ledger.cached(conn, pid, "apollo", TOP, org["id"])
    if top is not None:                          # an empty answer says so on every reopen, not just after the click
        found = len(top.get("people") or [])
        return {"credits": 0, "detail": f"already looked up: {found} top people from Apollo, shown free" if found
                else f"already looked up: Apollo lists no top people for {org.get('name') or 'this company'}"}
    return {"credits": apollo.TOP_PEOPLE_CREDITS, "detail": undocumented}


def _providers(conn, pid: str, domain: str | None) -> dict:
    usage = ledger.usage(conn, pid)
    hunter_cached = bool(domain) and ledger.cached(conn, pid, "hunter", DOMAIN_SEARCH, domain) is not None
    hunter_on = keys.key("hunter", pid) is not None
    return {"apollo": {"configured": keys.key("apollo", pid) is not None, **usage,
                       "estimate": _apollo_estimate(conn, pid, domain), "credits_page": APOLLO_CREDITS_PAGE,
                       "balance_note": "Apollo does not let a Free plan read its balance through the API: the numbers "
                                       "here count what job-hunt-agent spent."},
            "hunter": {"configured": hunter_on, **usage,
                       "estimate": {"credits": 0 if hunter_cached else 1,
                                    "detail": "already looked up: shown free" if hunter_cached else
                                              f"Domain Search: 1 credit for up to {hunter.DOMAIN_LIMIT} people, "
                                              "0 if Hunter finds nobody"},
                       "note": None if hunter_on else f"off: add hunter.{pid}_api_key to config/secrets.yaml"}}


def panel(conn, pid: str, app_id: int | None, domain: str | None = None, source: str = "typed",
          uid: str | None = None) -> dict:
    """Everything the panel shows, from the JD and earlier (cached) lookups only: no paid call, no network. Keyed by
    a tracker row (app_id) or by a feed job (uid, GOAL9: no row needed, none created)."""
    app_id, uid = _target(conn, pid, app_id, uid)
    ctx = _context(conn, pid, app_id, uid)
    row = ctx["row"]
    company = (row.get("company") or "").strip()
    if domain:
        value = clean_domain(domain)
    else:
        value = ledger.last_key(conn, pid, app_id, DOMAIN_CALLS) if app_id is not None else None
        source = "used in your last lookup"
        if not value:
            value, source = signals.domain_guess(company, [ctx["text"]], ctx["links"])
    people, org, pattern = signals.jd_people(ctx["text"], company), None, None
    if value:
        found, org, pattern = _cached_people(conn, pid, value)
        people += found
    return {"app": app_id, "uid": uid, "company": company, "role_title": row.get("role_title"),
            "domain": {"value": value, "source": source},
            "org": {k: org.get(k) for k in ("name", "estimated_num_employees", "industry")} if org else None,
            "suggestions": _suggestions(ctx, people, value, pattern, org), "providers": _providers(conn, pid, value),
            "links": {"linkedin": signals.linkedin_searches(company, row.get("family"), signals.team_of(ctx["text"])),
                      "apollo": signals.apollo_web_url(value) if value else None},
            "contacts": row.get("contacts") or []}


def _need_key(provider: str, pid: str) -> str:
    key = keys.key(provider, pid)
    if not key:
        raise ValueError(f"No {provider.title()} key for {pid}: add {provider}.{pid}_api_key to config/secrets.yaml")
    return key


def _check_cap(conn, pid: str, needed: float) -> None:
    left = ledger.usage(conn, pid)["left_today"]
    if needed > left:
        raise ValueError(f"This lookup can cost up to {needed:g} credits; {left:g} left of today's {ledger.cap():g}-"
                         "credit daily cap (settings.yaml contacts_daily_cap). Nothing was spent.")


def _failed(conn, pid: str, app_id: int, provider: str, call: str, key: str, err: ProviderError) -> None:
    ledger.record(conn, pid, app_id, provider, call, key, 0, False, {"error": err.message, "status": err.status})
    raise ValueError(f"{provider.title()} refused {call}: {err.message}"
                     f"{f' (HTTP {err.status})' if err.status else ''}. No credits counted.") from None


def _apollo_lookup(conn, pid: str, app_id: int | None, domain: str) -> tuple[float, str | None]:
    key = _need_key("apollo", pid)
    _check_cap(conn, pid, _apollo_estimate(conn, pid, domain)["credits"])
    spent, hit = 0.0, ledger.cached(conn, pid, "apollo", ENRICH, domain)
    if hit is None:
        try:
            org = apollo.enrich_org(key, domain)
        except ProviderError as err:
            _failed(conn, pid, app_id, "apollo", ENRICH, domain, err)
        cost = apollo.ENRICH_CREDITS if org else 0
        ledger.record(conn, pid, app_id, "apollo", ENRICH, domain, cost, True, {"org": org})
        spent += cost
    else:
        org = hit.get("org")
    if not org:
        return spent, f"Apollo does not know {domain}."
    size = org.get("estimated_num_employees")
    if size and size > MAX_EMPLOYEES:
        return spent, (f"{org.get('name') or domain} has about {size} employees (over {MAX_EMPLOYEES}): top people "
                       "skipped. Use Find on LinkedIn or Open in Apollo.")
    if ledger.cached(conn, pid, "apollo", TOP, org["id"]) is None:
        try:
            people, layout = apollo.top_people(key, org["id"])
        except ProviderError as err:
            _failed(conn, pid, app_id, "apollo", TOP, org["id"], err)
        ledger.record(conn, pid, app_id, "apollo", TOP, org["id"], apollo.TOP_PEOPLE_CREDITS, True,
                      {"people": people, "shape": layout})
        spent += apollo.TOP_PEOPLE_CREDITS
        if not people:
            return spent, f"Apollo lists no top people for {org.get('name') or domain}."
    return spent, None


def _hunter_lookup(conn, pid: str, app_id: int | None, domain: str) -> tuple[float, str | None]:
    key = _need_key("hunter", pid)
    if ledger.cached(conn, pid, "hunter", DOMAIN_SEARCH, domain) is not None:
        return 0.0, "already looked up: shown from the cache"
    _check_cap(conn, pid, 1)
    try:
        found, credits = hunter.domain_search(key, domain)
    except ProviderError as err:
        _failed(conn, pid, app_id, "hunter", DOMAIN_SEARCH, domain, err)
    ledger.record(conn, pid, app_id, "hunter", DOMAIN_SEARCH, domain, credits, True, found)
    hunter.account(pid, key, fresh=True)                       # the panel's "searches left" after the spend
    return float(credits), None if found["people"] else f"Hunter lists nobody at {domain}."


def hunter_account(pid: str) -> dict:
    """the person's Hunter searches / verifications left (free call, kept a few minutes)."""
    key = keys.key("hunter", pid)
    return {"configured": key is not None, "account": hunter.account(pid, key) if key else None}


def _team_lookup(conn, pid: str, app_id: int | None, domain: str) -> tuple[float, str | None]:
    """The company's own team/about page (free, robots.txt respected, one polite request per page tried)."""
    if ledger.cached(conn, pid, "free", TEAM_PAGE, domain) is not None:
        return 0.0, "already checked: shown from the cache"
    people, url = [], None
    for path in TEAM_PATHS:
        try:
            resp = net.request(f"https://{domain}{path}", accept="text/html,application/xhtml+xml")
        except net.HttpError:
            continue
        people = signals.team_page_people(resp.body.decode("utf-8", "replace"))
        if people:
            url = f"https://{domain}{path}"
            break
    ledger.record(conn, pid, app_id, "free", TEAM_PAGE, domain, 0, True, {"people": people, "url": url})
    return 0.0, f"{len(people)} people on {url}" if url else "no team page with names and titles found"


LOOKUPS = {"apollo": _apollo_lookup, "hunter": _hunter_lookup, "team_page": _team_lookup}


def lookup(conn, pid: str, app_id: int | None, provider: str, domain: str, uid: str | None = None) -> dict:
    """One source, on the person's click: {'spent': credits, 'note': ..., 'panel': the refreshed panel}. For a feed
    job without a row the answer is cached and counted by domain under the person (app_id NULL); no row is made."""
    if provider not in LOOKUPS:
        raise ValueError(f"provider must be one of {', '.join(LOOKUPS)}")
    domain = clean_domain(domain)
    app_id, uid = _target(conn, pid, app_id, uid)
    if app_id is not None:
        trackapi._own(conn, pid, app_id)
    with _LOOKUP_LOCK:                      # a double click or a second tab waits, then hits the cache
        spent, note = LOOKUPS[provider](conn, pid, app_id, domain)
    return {"spent": spent, "note": note, "panel": panel(conn, pid, app_id, domain, "used in this lookup", uid)}


def add(conn, pid: str, app_id: int | None, contact: dict, uid: str | None = None) -> dict:
    """Add contact (form or a suggestion): appended to the row's contacts, which write the sheet's contact cells.
    A feed job without a row gets one now, as 'saved' (the row Apply ↗ makes; saved rows never reach the Sheet):
    GOAL9 §3 C, the row is created only when a contact is saved, never on open."""
    if not isinstance(contact, dict):
        raise ValueError("contact must be an object")
    source = str(contact.get("source") or "manual").strip()
    if source not in SOURCES:
        raise ValueError(f"source must be one of {', '.join(SOURCES)}")
    new = {f: str(contact.get(f) or "").strip() for f in CONTACT_FIELDS}
    new["role"] = new["role"] or str(contact.get("title") or "").strip()
    if not (new["name"] or new["email"]):
        raise ValueError("a contact needs a name or an email")
    app_id, uid = _target(conn, pid, app_id, uid)
    if app_id is None:
        app_id = tracker.track_job(conn, pid, uid, source="ui")
    row = trackapi.get(conn, pid, app_id)
    contacts.save(conn, app_id, contacts.plain(row["contacts"]) + [{**new, "source": source}])
    return trackapi.get(conn, pid, app_id)
