"""the address a form gets matches the city on the resume used for that job.

The resume's city follows the job (resumegen's tailored resume carries it: remote -> the person's own city, onsite /
hybrid -> the job's city; a base resume shows the person's own city). Own city -> the person's real home address
(answer bank). Any other City+State -> their address book (autofill_addresses): a saved one is used at once, with no
network. A city with none gets real apartment-building street addresses from OpenStreetMap (Nominatim's apartment
buildings in that city; if it has none, Overpass's within about 2 km of the city's center), fetched once in the
background and cached (osm_places), offered one at a time: Use / Edit / Pick another. Nothing is used before the
person confirms it.

Street only: an apartment / unit / suite / building number is stripped from OSM results and from anything typed, and
Address line 2 stays empty. The tracker's Address column gets exactly "7480 Birdwood Ave, McLean, VA 22102".

OSM: the usage policies allow app lookups at most 1 request per second with a real User-Agent and cached results
(both hosts' robots.txt disallow their API paths to crawlers; this is one lookup per new city on the person's
behalf, so it goes out with the robots gate off for these two hosts only, paced at 1.1 s).
"""
import json
import logging
import re
import threading
import time
import urllib.parse
import urllib.request

from .. import config, db, net, resume_place
from . import bank

log = logging.getLogger(__name__)
NOMINATIM = "https://nominatim.openstreetmap.org/search"
# Overpass's own servers: the main one often answers 504 (busy) or an empty "runtime error" 200; the next is tried
OVERPASS = ("https://overpass-api.de/api/interpreter", "https://z.overpass-api.de/api/interpreter",
            "https://lz4.overpass-api.de/api/interpreter")
OVERPASS_TIMEOUT_S = 45                                    # its own query timeout is 25 s, then the queue
OSM_GAP_S = 1.1
BOX_DEG = 0.02                                             # ~2 km each way from the city's center
MAX_CANDIDATES = 25
STATE_CODES = resume_place.STATE_CODES                     # "kansas" -> "KS"
STATE_NAMES = {code: name.title() for name, code in STATE_CODES.items()}
SUFFIXES = {"avenue": "Ave", "street": "St", "road": "Rd", "drive": "Dr", "boulevard": "Blvd", "lane": "Ln",
            "court": "Ct", "parkway": "Pkwy", "place": "Pl", "terrace": "Ter", "circle": "Cir", "highway": "Hwy",
            "trail": "Trl", "square": "Sq", "expressway": "Expy", "freeway": "Fwy", "crossing": "Xing",
            "plaza": "Plz", "point": "Pt", "ridge": "Rdg"}
DIRECTIONS = {"north": "N", "south": "S", "east": "E", "west": "W", "northeast": "NE", "northwest": "NW",
              "southeast": "SE", "southwest": "SW"}
# a unit word followed by a unit id (one holding a digit, or a single letter): "Apt 4B", "Unit A", "# 3"; never
# "Space Center Blvd", "Ste Genevieve Ave" or "Unit Rd"
_UNIT_ID = r"(?:(?=[\w-]*\d)[\w-]+|[A-Za-z](?![\w-]))"
_UNIT = re.compile(r"(?i)[,\s]*(?:\b(?:apt|apartment|unit|suite|ste|bldg|building|fl|floor|rm|room|lot|spc|space)\b\.?"
                   r"\s*#?\s*" + _UNIT_ID + r"|#\s*[\w-]+)")
_ZIP = re.compile(r"^\d{5}$")
_FULL = re.compile(r"^\s*(\d+[A-Za-z]?\s+[^,]+),\s*([^,]+),\s*([A-Za-z][A-Za-z .]*?)\s+(\d{5})(?:-\d{4})?\s*$")
LOOKUP_WAIT_S = 12                                          # a fill waits this long for a city's first OSM lookup
LOOKUP_POLL_S = 0.25
_HOUSE = re.compile(r"^\d+[A-Za-z]?$")
_LOOKUPS: dict[str, str] = {}                               # city_state -> "looking" | "failed: ..." (this process)
_LOCK = threading.Lock()
ADDRESS_KEYS = ("address1", "address2", "city", "state", "zip", "location", "country")


def strip_unit(text: str | None) -> str:
    """'1781 Chain Bridge Rd Apt 4B' -> '1781 Chain Bridge Rd' (no apartment, unit, suite, building, '#')."""
    return re.sub(r"\s{2,}", " ", _UNIT.sub("", str(text or ""))).strip(" ,")


def _word(word: str) -> str:
    if word.isdigit() or any(c.isupper() for c in word[1:]):
        return word
    return word[:1].upper() + word[1:]


def usps(street: str | None) -> str:
    """Street suffixes and directions the USPS way: '1781 Chain Bridge Road' -> '1781 Chain Bridge Rd'."""
    words = strip_unit(street).split()
    lows = [w.lower().strip(".,") for w in words]
    # only the street type itself is shortened: the last suffix word, with at most directions after it
    # ("12 Old Court Road" -> "12 Old Court Rd", "9 Park Avenue South" -> "9 Park Ave S")
    kind = next((i for i in range(len(words) - 1, 1, -1)
                 if lows[i] in SUFFIXES and all(x in DIRECTIONS for x in lows[i + 1:])), None)
    out = []
    for i, word in enumerate(words):
        low = lows[i]
        if i == kind:
            out.append(SUFFIXES[low])
        elif low in DIRECTIONS and (i == 1 or i == len(words) - 1):
            out.append(DIRECTIONS[low])
        else:
            out.append(_word(word))
    return " ".join(out)


def code_of(state: str | None) -> str:
    text = str(state or "").strip()
    return text.upper() if text.upper() in STATE_NAMES else STATE_CODES.get(text.lower(), "")


def line(street: str, city: str, state: str, zip_code: str) -> str:
    """The tracker's Address: '7480 Birdwood Ave, McLean, VA 22102'."""
    return f"{usps(street)}, {city}, {code_of(state)} {str(zip_code).strip()[:5]}"


def _split(city_state: str) -> tuple[str, str]:
    city, _, state = str(city_state or "").rpartition(",")
    code = code_of(state)
    if not city.strip() or not code:
        raise ValueError(f"a city is written 'McLean, VA' (got {city_state!r})")
    return city.strip(), code


def canonical(conn, pid: str, city_state) -> str:
    """The spelling already known for this City+State (saved or looked up), else the cleaned one: 'mclean, va' is
    the same city as 'McLean, VA'."""
    text = clean_city(city_state)
    row = conn.execute("SELECT city_state FROM autofill_addresses WHERE profile = ? AND lower(city_state) = lower(?) "
                       "UNION ALL SELECT city_state FROM osm_places WHERE lower(city_state) = lower(?) LIMIT 1",
                       (pid, text, text)).fetchone()
    return row["city_state"] if row else text


def clean_city(city_state) -> str:
    city, code = _split(" ".join(str(city_state or "").split()))
    return f"{city.title() if city.islower() or city.isupper() else city}, {code}"


def _view(street: str, city: str, state: str, zip_code: str, kind: str, city_state: str, **extra) -> dict:
    return {"kind": kind, "city_state": city_state, "street": usps(street), "city": city,
            "state": STATE_NAMES.get(code_of(state), state), "state_code": code_of(state), "zip": zip_code,
            "line": line(street, city, state, zip_code), **extra}


def home(conn, pid: str) -> dict:
    """The person's real address from the answer bank (kind 'home'; 'none' when the street or ZIP is missing)."""
    values = bank.load(conn, pid)

    def get(key: str) -> str:
        return str((values.get(key) or {}).get("answer") or "").strip()
    street, city, state, zip_code = strip_unit(get("address1")), get("city"), get("state"), get("zip")
    city_state = f"{city}, {code_of(state)}" if city and code_of(state) else ""
    if not (street and city and code_of(state) and _ZIP.match(zip_code[:5])):
        return {"kind": "none", "city_state": city_state,
                "why": "your home street address and ZIP are not saved yet (Answers page, Contact)"}
    return _view(street, city, state, zip_code[:5], "home", city_state)


def resume_city(pid: str, rg_job: dict | None) -> str | None:
    """The City, ST on the resume used for the job; None = the person's own city (remote, or a base resume)."""
    if not rg_job or str(rg_job.get("workplace") or "").lower() == "remote":
        return None
    place = resume_place.city_state(str(rg_job.get("location") or ""))
    own = str((config.profile(pid) or {}).get("location") or "").strip()
    return None if not place or place.lower() == own.lower() else place


def saved(conn, pid: str, city_state: str) -> dict | None:
    row = conn.execute("SELECT * FROM autofill_addresses WHERE profile = ? AND city_state = ?",
                       (pid, city_state)).fetchone()
    if row is None:
        return None
    city, code = _split(city_state)
    return _view(row["street"], city, code, row["zip"], "saved", city_state, source=row["source"])


def for_job(conn, pid: str, rg_job: dict | None, fetch: bool = True) -> dict:
    """What the job's forms get: home | saved | suggest (waiting for Use) | looking | failed | none."""
    city_state = resume_city(pid, rg_job)
    if city_state is None:
        return home(conn, pid)
    return saved(conn, pid, city_state) or offer(conn, pid, city_state, fetch=fetch)


def _candidates(conn, city_state: str) -> list[dict] | None:
    row = conn.execute("SELECT candidates FROM osm_places WHERE city_state = ?", (city_state,)).fetchone()
    return json.loads(row["candidates"]) if row else None


def offer(conn, pid: str, city_state: str, fetch: bool = True) -> dict:
    """The suggestion offered now for a city with no saved address (starts the OSM lookup the first time)."""
    found = _candidates(conn, city_state)
    if found is None:
        state = _LOOKUPS.get(city_state)
        if state is None and fetch:
            start_lookup(city_state)
            state = "looking"
        if state and state.startswith("failed"):
            return {"kind": "failed", "city_state": city_state, "why": state[len("failed: "):]}
        return {"kind": "looking", "city_state": city_state}
    if not found:
        return {"kind": "failed", "city_state": city_state,
                "why": f"OpenStreetMap has no apartment building with a full address near {city_state}: type one"}
    row = conn.execute("SELECT idx FROM autofill_address_offers WHERE profile = ? AND city_state = ?",
                       (pid, city_state)).fetchone()
    idx = (row["idx"] if row else 0) % len(found)
    pick = found[idx]
    city, code = _split(city_state)
    return _view(pick["street"], city, code, pick.get("zip") or "", "suggest", city_state, name=pick.get("name"),
                 n=len(found), idx=idx)


def another(conn, pid: str, city_state: str) -> dict:
    """Pick another: the next suggestion (after a failed lookup: try once more)."""
    city_state = canonical(conn, pid, city_state)
    found = _candidates(conn, city_state)
    if not found:                                       # failed, or found nothing (OSM was busy?): ask once more
        if found is not None:
            with db.tx(conn):
                conn.execute("DELETE FROM osm_places WHERE city_state = ?", (city_state,))
        _LOOKUPS.pop(city_state, None)
        return offer(conn, pid, city_state)
    with db.tx(conn):
        conn.execute("INSERT INTO autofill_address_offers(profile, city_state, idx) VALUES (?, ?, 1) "
                     "ON CONFLICT(profile, city_state) DO UPDATE SET idx = idx + 1", (pid, city_state))
    return offer(conn, pid, city_state)


def use(conn, pid: str, city_state: str, street: str | None = None, zip_code: str | None = None) -> dict:
    """Use (the suggestion as offered) or Edit (the person's own street / ZIP): saved for every later job there."""
    city_state = canonical(conn, pid, city_state)
    source = "typed"
    if not street:
        offered = offer(conn, pid, city_state, fetch=False)
        if offered["kind"] != "suggest":
            raise ValueError(f"no suggestion for {city_state} yet: type the street address")
        street, zip_code, source = offered["street"], zip_code or offered["zip"], "osm"
    street = usps(strip_unit(street))
    zip_code = str(zip_code or "").strip()[:5]
    if not street or not _HOUSE.match(street.split()[0]) or len(street.split()) < 2:
        raise ValueError("write the street address with its number, e.g. 7480 Birdwood Ave")
    if not _ZIP.match(zip_code):
        raise ValueError("the ZIP code is 5 digits")
    with db.tx(conn):
        conn.execute("INSERT OR REPLACE INTO autofill_addresses(profile, city_state, street, zip, source, saved_at) "
                     "VALUES (?,?,?,?,?,?)", (pid, city_state, street, zip_code, source, db.iso(db.utcnow())))
    return saved(conn, pid, city_state)


def forget(conn, pid: str, city_state: str) -> dict:
    city_state = canonical(conn, pid, city_state)
    with db.tx(conn):
        gone = conn.execute("DELETE FROM autofill_addresses WHERE profile = ? AND city_state = ?",
                            (pid, city_state)).rowcount
    if not gone:
        raise KeyError(city_state)
    return {"removed": city_state}


def listing(conn, pid: str) -> list[dict]:
    rows = conn.execute("SELECT city_state FROM autofill_addresses WHERE profile = ? ORDER BY city_state",
                        (pid,)).fetchall()
    return [saved(conn, pid, r["city_state"]) for r in rows]


USABLE = ("home", "saved", "tracker")


def from_tracker(text: str | None) -> dict | None:
    """The tracker row's Address when it is a full street address ("7480 Birdwood Ave, McLean, VA 22102"): what
    this company was (or will be) told, so it comes first. A bare "McLean, VA" (the job's location) is not one."""
    found = _FULL.match(str(text or ""))
    if not found or not code_of(found.group(3)):
        return None
    street, city, state, zip_code = found.groups()
    city = city.strip()
    return _view(strip_unit(street), city, state, zip_code, "tracker", f"{city}, {code_of(state)}")


def _wait_lookup(city_state: str, wait_s: float) -> None:
    for _ in range(int(wait_s / LOOKUP_POLL_S)):
        if not str(_LOOKUPS.get(city_state) or "").startswith("looking"):
            return
        time.sleep(LOOKUP_POLL_S)


def auto(conn, pid: str, rg_job: dict | None, wait_s: float = 0) -> dict:
    """The address for the resume's city with no question asked (user 2026-10-14): home, a saved one, or the best
    OpenStreetMap address picked and saved at once (the panel's Change replaces it). With wait_s, a fill waits that
    long for a city's first lookup."""
    view = for_job(conn, pid, rg_job)
    if view["kind"] == "looking" and wait_s:
        _wait_lookup(view["city_state"], wait_s)
        view = for_job(conn, pid, rg_job, fetch=False)
    if view["kind"] == "suggest":
        view = {**use(conn, pid, view["city_state"]), "auto": True}
    return view


def answers_for(view: dict) -> dict:
    """Bank keys -> values for the job's address. Without a street yet (OSM still looking or found none, or no home
    street saved): the city, state and country of the resume's city, the street and ZIP left empty (never a
    question)."""
    if view.get("kind") in USABLE:
        return {"address1": view["street"], "address2": "", "city": view["city"], "state": view["state"],
                "zip": view["zip"], "location": f"{view['city']}, {view['state_code']}", "country": bank.US_COUNTRY}
    try:
        city, code = _split(view.get("city_state") or "")
    except ValueError:                                  # no city known (nothing on the resume or saved): only the country
        return {"address2": "", "country": bank.US_COUNTRY}
    return {"address1": "", "address2": "", "zip": "", "city": city, "state": STATE_NAMES.get(code, code),
            "location": f"{city}, {code}", "country": bank.US_COUNTRY}


# ---- OpenStreetMap (background, once per city) ----------------------------------------------------------------

def _get_json(url: str):
    host = urllib.parse.urlsplit(url).hostname or ""
    net.pace(host, OSM_GAP_S)
    if "overpass" not in host:
        return json.loads(net.request(url, gate=False).body)
    req = urllib.request.Request(url, headers={"User-Agent": net.user_agent(), "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=OVERPASS_TIMEOUT_S) as resp:     # one try per server: the next is a mirror
        return json.loads(resp.read())


def _overpass(query: str, get_json) -> dict:
    """The first Overpass server that answers the query in full."""
    last = None
    for base in OVERPASS:
        try:
            found = get_json(base + "?data=" + urllib.parse.quote(query))
        except (net.HttpError, ValueError, OSError) as err:
            last = err
            continue
        if "error" not in str(found.get("remark") or "").lower():
            return found
        last = ValueError(f"Overpass: {found.get('remark')}")
    raise last or OSError("no Overpass server answered")


def _candidate(tags: dict, city_zip: str) -> dict | None:
    number = str(tags.get("addr:housenumber") or "").split(";")[0].split("-")[0].strip()
    if not _HOUSE.match(number) or not tags.get("addr:street"):
        return None
    own_zip = str(tags.get("addr:postcode") or "")[:5]
    zip_code = own_zip if _ZIP.match(own_zip) else city_zip
    if not zip_code:
        return None
    return {"street": usps(f"{number} {tags['addr:street']}"), "zip": zip_code, "name": tags.get("name"),
            "own_zip": zip_code == own_zip}


def _unique(found: list[dict]) -> list[dict]:
    out, seen = [], set()
    for made in found:
        if made and made["street"].lower() not in seen:
            seen.add(made["street"].lower())
            out.append(made)
    out.sort(key=lambda c: not c["own_zip"])
    return out[:MAX_CANDIDATES]


def _from_nominatim(city: str, code: str, get_json) -> list[dict]:
    """Nominatim's own apartment buildings in the city (structured search, one fast request)."""
    query = urllib.parse.urlencode({"amenity": "apartments", "city": city, "state": code, "country": "us",
                                    "format": "jsonv2", "addressdetails": 1, "limit": MAX_CANDIDATES * 2})
    found = []
    for place in get_json(f"{NOMINATIM}?{query}") or []:
        if place.get("type") != "apartments":
            continue
        where = place.get("address") or {}
        town = next((where.get(k) for k in ("city", "town", "village", "hamlet", "municipality") if where.get(k)), "")
        if town.strip().lower() != city.strip().lower():  # "Santa Clara" must not get a San Jose street
            continue
        found.append(_candidate({"addr:housenumber": where.get("house_number"), "addr:street": where.get("road"),
                                 "addr:postcode": where.get("postcode"), "name": place.get("name")}, ""))
    return _unique(found)


def fetch_candidates(city_state: str, get_json=_get_json) -> list[dict]:
    """Apartment buildings in the city with a real house number and street, those with their own ZIP first:
    Nominatim's, else Overpass's within about 2 km of the city's center."""
    city, code = _split(city_state)
    found = _from_nominatim(city, code, get_json)
    if found:
        return found
    query = urllib.parse.urlencode({"city": city, "state": code, "country": "us", "format": "jsonv2",
                                    "addressdetails": 1, "limit": 1})
    places = get_json(f"{NOMINATIM}?{query}")
    if not places:
        return []
    lat, lon = places[0]["lat"], places[0]["lon"]
    city_zip = str((places[0].get("address") or {}).get("postcode") or "")[:5]
    city_zip = city_zip if _ZIP.match(city_zip) else ""
    lat, lon = float(lat), float(lon)                    # a box (cheaper for Overpass than "around")
    box = f"{lat - BOX_DEG:.4f},{lon - BOX_DEG:.4f},{lat + BOX_DEG:.4f},{lon + BOX_DEG:.4f}"
    found = _overpass(f'[out:json][timeout:25];nwr["building"="apartments"]["addr:housenumber"]["addr:street"]'
                      f'({box});out center tags {MAX_CANDIDATES * 2};', get_json)
    elements = [el for el in found.get("elements") or []           # a building tagged with another city is not this one
                if str((el.get("tags") or {}).get("addr:city") or city).strip().lower() == city.strip().lower()]
    return _unique([_candidate(el.get("tags") or {}, city_zip) for el in elements])


def _lookup(city_state: str) -> None:
    try:
        found = fetch_candidates(city_state)
    except Exception as err:                                # any failure: the panel says so, never "looking" forever
        log.warning("autofill: OpenStreetMap lookup for %s failed: %s", city_state, err)
        _LOOKUPS[city_state] = "failed: OpenStreetMap did not answer: Pick another to try again, or type the address"
        return
    conn = db.connect()
    try:
        with db.tx(conn):
            conn.execute("INSERT OR REPLACE INTO osm_places(city_state, candidates, fetched_at) VALUES (?,?,?)",
                         (city_state, json.dumps(found), db.iso(db.utcnow())))
    finally:
        conn.close()
    _LOOKUPS.pop(city_state, None)


def start_lookup(city_state: str) -> None:
    with _LOCK:
        if _LOOKUPS.get(city_state) == "looking":
            return
        _LOOKUPS[city_state] = "looking"
    threading.Thread(target=_lookup, args=(city_state,), daemon=True, name=f"osm-{city_state}").start()
