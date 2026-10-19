"""Where the resume says the person is (user 2026-10-08: "if they say hybrid or onsite change the location
to whatever location they put; if remote, my home city is fine").

resolve() -> {"workplace": remote | hybrid | onsite, "location": "City, ST" | None, "why": ...}:
  - workplace: the job's stored workplace; when empty, the posting's own words (remote / hybrid / on-site /
    "in office 3 days"); stated nowhere -> on-site;
  - remote -> location None (the resume keeps the person's own city);
  - hybrid / on-site -> the job's first US city as "City, ST" ("US, MA, Wilmington" -> "Wilmington, MA",
    "West Milwaukee +1 more" -> the first one; GOAL11 §9: "United States-Texas-Temple", "TX - Temple",
    "Lincoln NE" too); no usable US city -> None;
  - no stored location (a pasted / manual JD): the posting's own "Location:" lines, else a "City, ST"
    line in its first lines.
resumegen writes the location on the contact line and its location check accepts exactly that value.

GOAL22 (user 2026-10-13: Target's "7000 Target Pkwy N,NCD-0375 Brooklyn Park,MN 55445" failed Generate): a street
address gives its city ("Brooklyn Park, MN": street numbers, mail codes, ZIPs and the street itself dropped), and
city_state() only ever returns a "City, ST" that resumegen accepts. A job whose place still can't be worked out, or
that lists several cities, is marked `pick` with its `choices`: Generate asks the person (resolve()).
"""
import re

from .signals import US_STATES, is_us

STATE_CODES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO",
    "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
    "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC", "north dakota": "ND", "ohio": "OH",
    "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA",
    "washington": "WA", "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY", "district of columbia": "DC",
}
# a bare city name a posting often gives without its state
KNOWN_CITIES = {
    "new york": ("New York", "NY"), "new york city": ("New York", "NY"), "nyc": ("New York", "NY"),
    "san francisco": ("San Francisco", "CA"), "seattle": ("Seattle", "WA"), "boston": ("Boston", "MA"),
    "austin": ("Austin", "TX"), "chicago": ("Chicago", "IL"), "los angeles": ("Los Angeles", "CA"),
    "denver": ("Denver", "CO"), "atlanta": ("Atlanta", "GA"), "dallas": ("Dallas", "TX"),
    "houston": ("Houston", "TX"), "miami": ("Miami", "FL"), "pittsburgh": ("Pittsburgh", "PA"),
    "palo alto": ("Palo Alto", "CA"), "mountain view": ("Mountain View", "CA"), "sunnyvale": ("Sunnyvale", "CA"),
    "san jose": ("San Jose", "CA"), "santa clara": ("Santa Clara", "CA"), "san mateo": ("San Mateo", "CA"),
    "san diego": ("San Diego", "CA"), "washington dc": ("Washington", "DC"), "washington, dc": ("Washington", "DC"),
    "washington d.c.": ("Washington", "DC"), "washington d.c": ("Washington", "DC"), "philadelphia": ("Philadelphia", "PA"), "phoenix": ("Phoenix", "AZ"),
    "kansas city": ("Kansas City", "MO"), "minneapolis": ("Minneapolis", "MN"),
    "detroit": ("Detroit", "MI"), "nashville": ("Nashville", "TN"), "charlotte": ("Charlotte", "NC"),
    "raleigh": ("Raleigh", "NC"), "salt lake city": ("Salt Lake City", "UT"), "portland": ("Portland", "OR"),
    "jersey city": ("Jersey City", "NJ"), "cambridge": ("Cambridge", "MA"), "redmond": ("Redmond", "WA"),
    "bellevue": ("Bellevue", "WA"), "menlo park": ("Menlo Park", "CA"), "cupertino": ("Cupertino", "CA"),
    "dallas-fort worth": ("Dallas", "TX"), "dallas fort worth": ("Dallas", "TX"), "dfw": ("Dallas", "TX"),
}
_WORK = {"remote": "remote", "work from home": "remote", "hybrid": "hybrid", "onsite": "onsite",
         "on-site": "onsite", "client site": "onsite", "in office": "onsite"}
_HYBRID = re.compile(r"(?i)\bhybrid\b|\bin[- ](?:the[- ])?office\s+(?:\w+\s+){0,3}days?\b|"
                     r"\b(?:\d|two|three|four)\s+days?\s+(?:a|per|each)\s+week\s+(?:in|on)[- ](?:the\s+)?(?:office|site)")
_ONSITE = re.compile(r"(?i)\bon[- ]?site\b|\bin[- ]person\b|\bfully in[- ]office\b|\b5 days (?:a|per) week in")
_REMOTE = re.compile(r"(?i)\b(?:fully|100%|completely)\s+remote\b|\bremote[- ](?:first|role|position|eligible|"
                     r"opportunity|friendly)\b|\b(?:this|the)\s+(?:role|position|job)\s+is\s+(?:fully\s+)?remote\b|"
                     r"\bwork\s+from\s+(?:home|anywhere)\b|\bremote\s*\((?:us|usa|united states)\)")
_MORE = re.compile(r"(?i)\s*\+\s*\d+\s+more\b.*$")
_SEGMENTS = re.compile(r"\s*(?:;|\||\n|\s/\s|\bor\b|\band\b(?=\s+[A-Z]))\s*")
_NOISE = re.compile(r"(?i)\bbay area\b|\bclient site\b|\bin[- ]person\b|\b(?:hq|headquarters|office|offices|"
                    r"metro(?:politan)?|area|campus|remote|hybrid|on-?site|greater|downtown|unavailable)\b")
_US_TAIL = re.compile(r"(?i)[,\s-]*\b(?:united states(?: of america)?|usa|u\.s\.a?\.?|us)\s*$")
_US_HEAD = re.compile(r"(?i)^\s*(?:united states(?: of america)?|usa|us)\s*[-–:,]\s*")
_COUNTRY_ONLY = re.compile(r"(?i)^(?:united states(?: of america)?|usa|us|remote)?$")
_ZIP = re.compile(r"\s+\d{5}(?:-\d{4})?\b")
_STATE_NAME = re.compile(r"(?i)\b(" + "|".join(sorted(STATE_CODES, key=len, reverse=True)) + r")\b")
_DASH = re.compile(r"\s*[-–—]\s*")
_SPACED = re.compile(r"^(.+?)\s+([A-Z]{2})$")                       # "Lincoln NE"
_SPACED_FIRST = re.compile(r"^([A-Z]{2})\s+(.+)$")                # "CA Los Angeles"
_BRACKETED = re.compile(r"\([^)]*\)")
_STORE = re.compile(r"(?i)^(?:store\s*)?#?\d+\s*[-–]\s*")              # "2458 - Cedar Park", "Store 192- Columbia"
_LOCATION_LINE = re.compile(r"(?im)^[ \t*•·-]*(?:job |work |primary |office )?locations?(?:\(s\))?[ \t]*[:–-][ \t]*(\S.*)$")
_CITY_LINE = re.compile(r"^[A-Z][A-Za-z .'-]+,\s*(?:[A-Z]{2}\b|(?i:" + "|".join(STATE_CODES) + r")\b)")
HEADER_LINES, HEADER_LINE_CHARS = 10, 80
# what resumegen accepts on the contact line (resumegen server.LOCATION)
LOCATION = re.compile(r"^[A-Za-z][A-Za-z .'-]{0,40}, [A-Z]{2}$")
MIN_CITY_CHARS = 3                                                   # "W" (of "2900 W Oklahoma Ave") is no city
MAX_CITY_WORDS = 4                                                   # "Lake Havasu City"; a 5th word is a place name
_HIER = re.compile(r"\s*>\s*|(?<=\w)/(?=\w)")                         # "US > Arizona > Phoenix", "USA/Tennessee/Lexington"
_TAIL_PARTS = re.compile(r"\s*(?:[,(|:#]|\)|\s[-–—]\s)\s*")
_DIGIT = re.compile(r"\d")
_STREET = {"st", "street", "ave", "av", "avenue", "blvd", "boulevard", "rd", "road", "dr", "drive", "ln", "lane",
           "pkwy", "parkway", "hwy", "highway", "ct", "cir", "circle", "pl", "plz", "way", "wy", "ter", "trl", "expy",
           "fwy", "pike", "suite", "ste", "fl", "floor", "bldg", "building", "unit", "mall"}
_DIRECTION = {"n", "s", "e", "w", "ne", "nw", "se", "sw"}
# words no city has ("Store, WA", "Work from Home, OH", "Location Varies, KY", "week in Whitwell")
_NOT_CITY = {"store", "virtual", "home", "site", "varies", "corporate", "customer", "client", "work", "week", "days",
             "office", "building", "location", "locations", "remote", "hybrid", "campus", "hq", "headquarters",
             "plant", "warehouse", "facility", "hospital", "clinic", "resort", "hotel", "in", "covering", "all",
             "statewide", "across", "within", "anywhere"}
# real cities that hold one of those words (GOAL22 verifier re-check: these asked needlessly)
_REAL_CITIES = {"plant city", "mountain home", "lake in the hills"}
_STATE_PREFIX = re.compile(r"^[A-Za-z]{2}-")                          # "In-indianapolis" (IN-INDIANAPOLIS)
_STREET_AFTER = _STREET | {"court", "street", "avenue"}
_NO_PLACE = re.compile(r"(?i)\b(?:united states(?: of america)?|usa|us|u\.s\.a?\.?|remote|anywhere|global|any|tbd|"
                       r"multiple|various|several|locations?|nationwide|north america|americas?|unavailable|"
                       r"hybrid|on-?site|in[- ]office|flexible|virtual|home|based|n/?a)\b|[^A-Za-z]+")


def workplace_of(stored: str | None, location: str | None, jd_text: str | None) -> tuple[str, str]:
    """(remote | hybrid | onsite, where it was read)."""
    known = _WORK.get((stored or "").strip().lower())
    if known:
        return known, "job"
    place = location or ""
    if re.search(r"(?i)\bremote\b", place) and not re.search(r"(?i)\bhybrid\b", place):
        return "remote", "location"
    if re.search(r"(?i)\bhybrid\b", place):
        return "hybrid", "location"
    text = jd_text or ""
    for kind, pattern in (("hybrid", _HYBRID), ("onsite", _ONSITE), ("remote", _REMOTE)):
        if pattern.search(text):
            return kind, "JD"
    return "onsite", "not stated (treated as on-site)"


def _city(text: str) -> str:
    words = _NOISE.sub(" ", _STORE.sub("", text.strip())).split()
    return " ".join(w if not w.isupper() or len(w) <= 2 else w.capitalize() for w in words).strip(" ,-")


def _code(part: str) -> str | None:
    part = part.strip(" ,.")
    return part if part in US_STATES else STATE_CODES.get(part.lower())


def _dashed(seg: str) -> tuple[str, str] | None:
    """"Texas-Temple", "TX - Temple", "MA-Boston", "Atlanta-GA", "Client Site - CA - Woodland Hills - 6150 Canoga Ave":
    the city next to a state part (after it first, as Taleo / Oracle / Workday write them)."""
    parts = [p for piece in _BRACKETED.sub(" ", seg).split(",") for p in (x.strip() for x in _DASH.split(piece))
             if p and not _COUNTRY_ONLY.match(p)]
    for i, part in enumerate(parts):
        code = _code(part)
        for j in (i + 1, i - 1) if code else ():
            near = parts[j] if 0 <= j < len(parts) else ""
            city = _city(near) if not _code(near) or near.lower() in KNOWN_CITIES or code == "DC" else ""  # "NY-New York"
            if len(city) > 2 and not re.search(r"\d", city):                               # "BE, DE" is Berlin
                return city, code
    return None


def _spaced(seg: str) -> tuple[str, str] | None:
    """"Lincoln NE", "Austin TX", "CA Los Angeles" (no comma)."""
    for piece in seg.split(","):
        last, first = _SPACED.match(_city(piece)), _SPACED_FIRST.match(_city(piece))
        if last and last.group(2) in US_STATES:
            return last.group(1), last.group(2)
        if first and first.group(1) in US_STATES:
            return first.group(2), first.group(1)
    return None


def _street_end(words: list[str]) -> bool:
    return len(words) > 1 and words[-1].lower().rstrip(".") in _STREET


def _tail_words(part: str) -> list[str]:
    words = part.split()
    chained = max((i for i, w in enumerate(words) if w.count("-") >= 2), default=-1)       # "Center-ANN-Lynchburg"
    if chained >= 0:
        words = [words[chained].rsplit("-", 1)[-1]] + words[chained + 1:]
    digit = max((i for i, w in enumerate(words) if _DIGIT.search(w)), default=-1)
    if digit < 0:
        return words
    words = words[digit + 1:]
    street = next((i for i, w in enumerate(words) if w.lower().rstrip(".") in _STREET), -1)
    words = words[street + 1:]
    while len(words) > 1 and words[0].lower().rstrip(".") in _DIRECTION:
        words = words[1:]
    return words


def _tail_city(text: str) -> str:
    """the city at the end of what comes before the state: "7000 Target Pkwy N,NCD-0375 Brooklyn Park" ->
    "Brooklyn Park", "43 New Scotland Avenue Albany" -> "Albany", "Main Campus (Washington" -> "Washington". After a
    street number the street itself (up to its "Ave" / "Pkwy" and a trailing "N") is dropped too; a last part that
    is only a street ("Santa Fe Springs - Clark St.") or a word like "Hybrid" gives way to the part before it."""
    for part in reversed(_TAIL_PARTS.split(text.strip())):
        words = _tail_words(part)
        city = "" if _street_end(words) else _city(" ".join(words))
        if city:
            return city
    return ""


def _one(segment: str) -> tuple[str, str] | None:
    """(City, ST) from one place, US only."""
    if is_us(segment) is False:                                           # "Hybrid (Bengaluru, Karnataka, IN)"
        return None
    workday = re.match(r"^(?:US|USA|United States)\s*[,-]\s*([A-Z]{2})\s*[,-]\s*(.+)$", segment.strip())
    if workday and workday.group(1) in US_STATES:
        return _city(workday.group(2)), workday.group(1)
    seg = _ZIP.sub("", _US_TAIL.sub("", _US_HEAD.sub("", _HIER.sub(" - ", segment.strip()))).strip(" ,-"))
    coded = re.search(r",\s*([A-Z]{2})\b", seg)          # the first code only: "Berlin, BE, DE" is not Delaware
    city = _tail_city(seg[:coded.start()]) if coded and coded.group(1) in US_STATES else ""
    if city and city.upper() not in US_STATES:                            # "…Brooklyn Park,MN" (no space too)
        return city, coded.group(1)
    for named in _STATE_NAME.finditer(seg):
        after = seg[named.end():].split()
        if after and after[0].lower().rstrip(".,") in _STREET_AFTER:    # "220 VIRGINIA AVE", "Washington Court House"
            continue
        city = _tail_city(seg[:named.start()].strip(" ,-"))
        code = STATE_CODES[named.group(1).lower()]
        if named.start() > 0 and city and city.upper() not in US_STATES and \
                (city.lower() not in STATE_CODES or code == "DC"):              # "MO-Kansas City": the dashed rule
            return city, code
    found = _dashed(seg) or _spaced(seg)
    if found:
        return found
    pieces = [_city(seg)] + [_city(piece) for piece in seg.split(",")]           # "901 Yorkchester, Houston"
    return next((KNOWN_CITIES[p.lower().rstrip(".")] for p in pieces if p.lower().rstrip(".") in KNOWN_CITIES), None)


def _state_in_jd(city: str, jd_text: str) -> str | None:
    """"West Milwaukee" + the JD's "West Milwaukee, WI" (or ", Wisconsin") -> "West Milwaukee, WI"."""
    if not city or not jd_text:
        return None
    names = "|".join(sorted(STATE_CODES, key=len, reverse=True))
    found = re.search(re.escape(city) + r",?\s+([A-Z]{2}\b|(?i:" + names + r")\b)", jd_text, re.I)
    if not found:
        return None
    state = found.group(1)
    code = state.upper() if len(state) == 2 else STATE_CODES.get(state.lower())
    return f"{city}, {code}" if code in US_STATES else None


def _usable(found: tuple[str, str] | None) -> str | None:
    """"City, ST" when resumegen would take it (never a street, a code or a whole sentence)."""
    if not found or not found[0] or _COUNTRY_ONLY.match(found[0]) or len(found[0].split()) > MAX_CITY_WORDS or \
            found[0].upper() in US_STATES or len(found[0]) < MIN_CITY_CHARS or \
            _STATE_PREFIX.match(found[0]) or \
            (_NOT_CITY & set(found[0].lower().split()) and found[0].lower() not in _REAL_CITIES):   # "Store, WA"
        return None
    place = f"{found[0]}, {found[1]}"
    return place if LOCATION.match(place) else None


def _segments(location: str | None) -> list[str]:
    text = _MORE.sub("", (location or "").replace("\u00a0", " "))
    return [seg for seg in _SEGMENTS.split(text) if seg and seg.strip()]


def places(location: str | None) -> list[str]:
    """every US city the location names, as "City, ST", in order and without repeats."""
    found = (_usable(_one(segment)) for segment in _segments(location))
    return list(dict.fromkeys(p for p in found if p))


def city_state(location: str | None, jd_text: str | None = None) -> str | None:
    """The job's first US city as "City, ST" (None when there is none); a city given without its state takes the
    state the JD names with it. Whatever it returns is a "City, ST" resumegen accepts."""
    segments = _segments(location)
    first = next(iter(places(location)), None)
    if first:
        return first
    for segment in segments:
        city = _city(_US_HEAD.sub("", segment))
        if not _COUNTRY_ONLY.match(city):
            found = _state_in_jd(city, jd_text or "")
            return found if found and LOCATION.match(found) else None
    return None


def names_a_place(location: str | None) -> bool:
    """the location says more than "United States" / "Remote" / "TBD" (so a person could tell where)."""
    return bool(_NO_PLACE.sub("", location or "").strip())


def location_in_jd(jd_text: str | None) -> str | None:
    """The posting's own place: its "Location:" / "Job Location:" lines (joined with "; "), else a "City, ST" line among
    its first lines (a pasted JD's header). None when it states neither."""
    text = jd_text or ""
    lines = [m.group(1).strip() for m in _LOCATION_LINE.finditer(text)]
    if lines:
        return "; ".join(lines)
    head = [ln.strip() for ln in text.splitlines() if ln.strip()][:HEADER_LINES]
    return next((ln for ln in head if len(ln) <= HEADER_LINE_CHARS and _CITY_LINE.match(ln)), None)


def resolve(stored_workplace: str | None, location: str | None, jd_text: str | None) -> dict:
    """What resumegen needs: the workplace and, for hybrid / on-site jobs, the city to put on the contact line. With
    no stored location (a manual JD) the JD's own location lines stand in for it.
    `pick` is True when the person should choose (a hybrid / on-site job naming several cities, or a place
    that is not a US "City, ST"); `choices` are the job's cities ("City, ST")."""
    location = location or location_in_jd(jd_text)
    kind, source = workplace_of(stored_workplace, location, jd_text)
    if kind == "remote":
        return {"workplace": kind, "location": None, "why": f"remote ({source}): the person's own city",
                "pick": False, "choices": []}
    place = city_state(location, jd_text)
    choices = list(dict.fromkeys(places(location) + places(location_in_jd(jd_text)) + ([place] if place else [])))
    several = len(choices) > 1 or bool(_MORE.search(location or ""))
    unclear = place is None and names_a_place(location)
    why = f"{kind} ({source}): " + (f"the job's city {place}" if place else "no US city in the job's location")
    return {"workplace": kind, "location": place, "why": why, "pick": several or unclear, "choices": choices}
