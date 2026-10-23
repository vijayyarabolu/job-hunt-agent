"""The Find contacts panel (decision 012). Per job it looks for three kinds of person (finder/kinds.py: a recruiter,
the AI/ML hiring manager, one peer engineer), cheapest source first: the JD and the company's team page (free),
Apollo's documented People API Search by domain + titles (0 credits), then Hunter's Domain Search filtered by
department, only for the kinds still missing. panel() is free and makes no network call; lookup() runs one source on
the person's click (their own key, the cost shown before, the daily cap checked first, the answer cached so it is
never paid twice); add() saves a contact (kind, source, found_at) into the tracker's contacts (-> the sheet's Target
Name / Role / Email cells through the sync). The panel is never empty: a kind nobody was found for gets LinkedIn's own
people search for it and its ready-to-send message (outreach.compose; nothing is sent)."""
import re
import threading
from datetime import datetime

from .. import contacts, db, jdsnap, net, tracker, trackapi
from . import apollo, hunter, keys, kinds, ledger, rank, signals
from .http import ProviderError

SOURCES = ("jd", "team_page", "apollo", "hunter", "linkedin-manual", "guess", "manual")
DOMAIN_RX = re.compile(r"^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$")
SEARCH, DOMAIN_SEARCH, TEAM_PAGE = apollo.SEARCH, "domain-search", "team_page"
DOMAIN_CALLS = (SEARCH, DOMAIN_SEARCH, TEAM_PAGE)
TEAM_PATHS = ("/team", "/about", "/about-us", "/company", "/leadership")
APOLLO_CREDITS_PAGE = "https://app.apollo.io/#/settings/credits/current"
CONTACT_FIELDS = ("name", "role", "email", "linkedin", "notes")
PRIVATE_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".home", ".corp", ".test", ".example", ".invalid",
                    ".intranet", ".home.arpa")
PER_KIND_SHOWN = 3                        # people shown under each kind (best first); the rest stay in the list
PLAN_LOCKED = "plan-locked"               # Apollo refused the search for the person's plan: remembered for the day
_LOCKED_RX = re.compile(r"not included in your .*plan|API_INACCESSIBLE|not permitted to call", re.I)
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
    text = snap.get("text") or row.get("jd") or db.unpack(job.get("description")) or ""
    links = [u for u in (row.get("job_url"), job.get("url"), job.get("apply_url")) if u]   # the posting's own links
    return {"row": row, "text": text, "links": links,
            "job": {"role_title": row.get("role_title"), "location": row.get("location") or job.get("location"),
                    "family": row.get("family")}}


def _today() -> str:
    return datetime.now().astimezone().date().isoformat()


def _plan_lock(conn, pid: str) -> str | None:
    """Apollo's refusal message when it refused the people search for this person's plan today."""
    return (ledger.cached(conn, pid, "apollo", SEARCH, f"{PLAN_LOCKED}|{_today()}") or {}).get("locked")


def _cached_people(conn, pid: str, domain: str) -> tuple[list[dict], str | None]:
    """Every answer already paid for (or free) at this domain: team page, Apollo per kind, Hunter (any filter)."""
    people = list((ledger.cached(conn, pid, "free", TEAM_PAGE, domain) or {}).get("people", []))
    for kind in kinds.KINDS:
        people += (ledger.cached(conn, pid, "apollo", SEARCH, f"{domain}|{kind}") or {}).get("people", [])
    pattern = None
    for found in ledger.cached_prefix(conn, pid, "hunter", DOMAIN_SEARCH, domain):
        people += found.get("people", [])
        pattern = pattern or found.get("pattern")
    return people, pattern


def _email_status(person: dict) -> str | None:
    if not person.get("email"):
        return None
    return "named in the JD" if person["source"] == "jd" else person.get("email_status") or "unverified"


def _suggestions(ctx: dict, people: list[dict], domain: str | None, pattern: str | None) -> list:
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
    return rank.rank(out, ctx["job"])


def _found(suggestions: list[dict]) -> dict[str, list[dict]]:
    """The named people of each kind, best first."""
    out = {k: [] for k in kinds.KINDS}
    for s in suggestions:
        if s.get("name") and s.get("kind") in out:
            out[s["kind"]].append(s)
    return out


def _missing(found: dict[str, list[dict]]) -> list[str]:
    return [k for k in kinds.KINDS if not found[k]]


def _apollo_estimate(conn, pid: str, domain: str | None, missing: list[str]) -> dict:
    """What the Apollo button would spend now: its people search costs 0 credits (documented)."""
    if not domain:
        return {"credits": 0, "detail": "enter the company domain first"}
    locked = _plan_lock(conn, pid)
    if locked:
        return {"credits": 0, "locked": True, "detail": f"Apollo refused its people search for your plan today: {locked}"}
    if not missing:
        return {"credits": 0, "detail": "a recruiter, a manager and a peer are already found: nothing to ask Apollo"}
    todo = [k for k in missing if ledger.cached(conn, pid, "apollo", SEARCH, f"{domain}|{k}") is None]
    if not todo:
        return {"credits": 0, "detail": "already looked up: Apollo found no " + ", ".join(
            kinds.LABELS[k].lower() for k in missing) + " at this domain"}
    return {"credits": apollo.SEARCH_CREDITS, "kinds": todo,
            "detail": "People API Search: 0 credits (documented); asks for " +
                      ", ".join(kinds.LABELS[k].lower() for k in todo)}


def _hunter_estimate(conn, pid: str, domain: str | None, missing: list[str]) -> dict:
    if not domain:
        return {"credits": 0, "detail": "enter the company domain first"}
    if not missing:
        return {"credits": 0, "detail": "a recruiter, a manager and a peer are already found: Hunter not needed"}
    deps = kinds.departments(missing)
    if ledger.cached(conn, pid, "hunter", DOMAIN_SEARCH, f"{domain}|{deps}") is not None:
        return {"credits": 0, "detail": "already looked up: shown free"}
    return {"credits": 1, "departments": deps,
            "detail": f"Domain Search ({deps.replace(',', ', ')}): 1 credit for up to {hunter.DOMAIN_LIMIT} people, "
                      "0 if Hunter finds nobody"}


def _providers(conn, pid: str, domain: str | None, missing: list[str]) -> dict:
    usage = ledger.usage(conn, pid)
    hunter_on = keys.key("hunter", pid) is not None
    return {"apollo": {"configured": keys.key("apollo", pid) is not None, **usage,
                       "estimate": _apollo_estimate(conn, pid, domain, missing), "credits_page": APOLLO_CREDITS_PAGE,
                       "balance_note": "Apollo does not let a Free plan read its balance through the API: the numbers "
                                       "here count what job-hunt-agent spent."},
            "hunter": {"configured": hunter_on, **usage, "estimate": _hunter_estimate(conn, pid, domain, missing),
                       "note": None if hunter_on else f"off: add hunter.{pid}_api_key to config/secrets.yaml"}}


def _kind_cards(pid: str, ctx: dict, found: dict[str, list[dict]]) -> list[dict]:
    """One card per kind: the best people found, or LinkedIn's people search for it; always its ready message."""
    from .. import outreach                      # outreach imports finder.kinds: imported here, at call time
    row, company = ctx["row"], (ctx["row"].get("company") or "").strip()
    cards = []
    for kind in kinds.KINDS:
        people = found[kind][:PER_KIND_SHOWN]
        person = {"name": people[0]["name"], "role": people[0]["title"]} if people else None
        draft = outreach.compose(pid, {**row, "company": company, "role_title": row.get("role_title") or ""},
                                 ctx["text"], kind, person)
        cards.append({"kind": kind, "label": kinds.LABELS[kind], "people": people,
                      "links": kinds.linkedin_search(company, kind),
                      "draft": {k: draft[k] for k in ("note", "subject", "email", "prompt")}})
    return cards


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
        value = value.split("|")[0] if value else None
        source = "used in your last lookup"
        if not value:
            value, source = signals.domain_guess(company, [ctx["text"]], ctx["links"])
    people, pattern = signals.jd_people(ctx["text"], company), None
    if value:
        cached, pattern = _cached_people(conn, pid, value)
        people += cached
    suggestions = _suggestions(ctx, people, value, pattern)
    found = _found(suggestions)
    return {"app": app_id, "uid": uid, "company": company, "role_title": row.get("role_title"),
            "domain": {"value": value, "source": source}, "org": None,
            "suggestions": suggestions, "kinds": _kind_cards(pid, ctx, found), "missing": _missing(found),
            "providers": _providers(conn, pid, value, _missing(found)),
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


def _apollo_lookup(conn, pid: str, app_id: int | None, domain: str, missing: list[str]) -> tuple[float, str | None]:
    key = _need_key("apollo", pid)
    estimate = _apollo_estimate(conn, pid, domain, missing)
    if not estimate.get("kinds"):
        return 0.0, estimate["detail"]
    _check_cap(conn, pid, estimate["credits"])
    found = 0
    for kind in estimate["kinds"]:
        try:
            people, total = apollo.people_search(key, domain, kinds.APOLLO_TITLES[kind])
        except ProviderError as err:
            if err.status == 403 and _LOCKED_RX.search(err.message or ""):
                ledger.record(conn, pid, app_id, "apollo", SEARCH, f"{PLAN_LOCKED}|{_today()}", 0, True,
                              {"locked": err.message})
                return 0.0, ("Apollo's people search is not on your plan (Apollo said: "
                             f"{err.message}). Nothing was spent; try Hunter or LinkedIn below.")
            _failed(conn, pid, app_id, "apollo", SEARCH, f"{domain}|{kind}", err)
        ledger.record(conn, pid, app_id, "apollo", SEARCH, f"{domain}|{kind}", apollo.SEARCH_CREDITS, True,
                      {"people": people, "total": total})
        found += len(people)
    return float(apollo.SEARCH_CREDITS), None if found else f"Apollo lists nobody with those titles at {domain}."


def _hunter_lookup(conn, pid: str, app_id: int | None, domain: str, missing: list[str]) -> tuple[float, str | None]:
    key = _need_key("hunter", pid)
    estimate = _hunter_estimate(conn, pid, domain, missing)
    if not estimate["credits"]:
        return 0.0, estimate["detail"]
    _check_cap(conn, pid, estimate["credits"])
    deps = estimate["departments"]
    try:
        found, credits = hunter.domain_search(key, domain, deps)
    except ProviderError as err:
        _failed(conn, pid, app_id, "hunter", DOMAIN_SEARCH, f"{domain}|{deps}", err)
    ledger.record(conn, pid, app_id, "hunter", DOMAIN_SEARCH, f"{domain}|{deps}", credits, True, found)
    hunter.account(pid, key, fresh=True)                       # the panel's "searches left" after the spend
    return float(credits), None if found["people"] else f"Hunter lists nobody in {deps} at {domain}."


def hunter_account(pid: str) -> dict:
    """the person's Hunter searches / verifications left (free call, kept a few minutes)."""
    key = keys.key("hunter", pid)
    return {"configured": key is not None, "account": hunter.account(pid, key) if key else None}


def _team_lookup(conn, pid: str, app_id: int | None, domain: str, missing: list[str]) -> tuple[float, str | None]:
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
    """One source, on the person's click: {'spent': credits, 'note': ..., 'panel': the refreshed panel}. Apollo and
    Hunter only look for the kinds still missing. For a feed job without a row the answer is cached and counted by
    domain under the person (app_id NULL); no row is made."""
    if provider not in LOOKUPS:
        raise ValueError(f"provider must be one of {', '.join(LOOKUPS)}")
    domain = clean_domain(domain)
    app_id, uid = _target(conn, pid, app_id, uid)
    if app_id is not None:
        trackapi._own(conn, pid, app_id)
    with _LOOKUP_LOCK:                      # a double click or a second tab waits, then hits the cache
        before = panel(conn, pid, app_id, domain, "used in this lookup", uid)
        spent, note = LOOKUPS[provider](conn, pid, app_id, domain, before["missing"])
    return {"spent": spent, "note": note, "panel": panel(conn, pid, app_id, domain, "used in this lookup", uid)}


def add(conn, pid: str, app_id: int | None, contact: dict, uid: str | None = None) -> dict:
    """Add contact (form or a suggestion): appended to the row's contacts, which write the sheet's contact cells.
    GOAL28 §5: each keeps its kind (recruiter / manager / peer, read from the title when not given), its source and
    the day it was found. A feed job without a row gets one now, as 'saved' (the row Apply ↗ makes; saved rows never
    reach the Sheet): GOAL9 §3 C, the row is created only when a contact is saved, never on open."""
    if not isinstance(contact, dict):
        raise ValueError("contact must be an object")
    source = str(contact.get("source") or "manual").strip()
    if source not in SOURCES:
        raise ValueError(f"source must be one of {', '.join(SOURCES)}")
    new = {f: str(contact.get(f) or "").strip() for f in CONTACT_FIELDS}
    new["role"] = new["role"] or str(contact.get("title") or "").strip()
    if not (new["name"] or new["email"]):
        raise ValueError("a contact needs a name or an email")
    kind = str(contact.get("kind") or "").strip() or kinds.kind_of(new["role"]) or ""
    if kind and kind not in kinds.KINDS:
        raise ValueError(f"kind must be one of {', '.join(kinds.KINDS)}")
    app_id, uid = _target(conn, pid, app_id, uid)
    if app_id is None:
        app_id = tracker.track_job(conn, pid, uid, source="ui")
    row = trackapi.get(conn, pid, app_id)
    contacts.save(conn, app_id, contacts.plain(row["contacts"]) + [{**new, "source": source, "kind": kind,
                                                                     "found_at": _today()}])
    return trackapi.get(conn, pid, app_id)
