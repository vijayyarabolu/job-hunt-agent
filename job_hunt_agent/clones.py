"""GOAL32 §1: one role posted once per city ("Software Engineer, AI Data - Mountain View, CA, USA", "... - Menlo Park,
CA, USA", 700 more) is one role. The title's trailing place is split off, so the copies share a base title and
ranking folds them into one card led by the best-located copy ("+N locations" lists the rest).

A trailing part is a place only when it reads as one: Remote / Hybrid / US / USA, a place signals.is_us can tell
(a US state, a country, a known city), or the job's own location. "Software Engineer - Payments" and "Engineer II"
keep their team and level, so different roles never share a base title."""
import functools
import re

from . import signals

_TAILS = (re.compile(r"^(?P<base>.+?)\s*\((?P<part>[^()]+)\)\s*$"),       # "X (Seattle)"
          re.compile(r"^(?P<base>.+)(?:\s+[-–—|:]\s*|[-–—|:]\s+)(?P<part>(?:[^-–—|:]|(?<=\w)-(?=\w))+)$"),
          # "X - Mountain View, CA, USA", "X- Winston-Salem, NC" (a dash inside a word is part of the place)
          re.compile(r"^(?P<base>.+),\s*(?P<part>[^-–—|:,]+)$"))                 # "X, Remote"
_NOT_A_PLACE = re.compile(r"[0-9()\[\]@_/+&]")
_MODE = re.compile(r"(?i)^(?:remote|hybrid|on-?site|in-?office|anywhere|nationwide|us|usa|u\.s\.(?:a\.)?|"
                   r"united states(?: of america)?|(?:remote|hybrid)\s*[-,(]?\s*(?:us|usa|united states)\)?)$")
_WORD = re.compile(r"[a-z0-9]+")
_METRO = {"round rock": "austin", "bellevue": "seattle", "redmond": "seattle",
          "jersey city": "new york", "santa clara": "san jose", "cambridge": "boston"}   # suburb -> its metro's big city
_FILLER = {"the", "a", "of", "and"}
_SAME = {"sr": "senior", "jr": "junior", "ml": "machine learning", "mle": "machine learning engineer",
         "swe": "software engineer", "ds": "data scientist", "genai": "generative ai"}
MAX_TAILS = 3               # "... - Remote - US" is two places
MAX_PLACE_WORDS = 6         # "Mountain View, CA, USA" is 4; "Wholesale Banking Lending @ING Hubs Romania" is a team
MAX_CHUNK_WORDS = 3         # each comma part of a place: "Continental United States", not "US Department of State"
MIN_LOCATION_MATCH = 3      # a tail found inside the job's location field must be a word this long, not "AI"
SPLITS_KEPT = 50_000        # split is a pure function of (title, location); the feed asks it for every copy, every call


def _in_location(part: str, location: str) -> bool:
    """`part` inside the location field as a whole word (no letter either side), any case. A plain search: a regex
    built per call was 0.5 s of a cold feed (thousands of patterns, past re's cache)."""
    if len(part) < MIN_LOCATION_MATCH:
        return False
    low, want = location.lower(), part.lower()
    at = low.find(want)
    while at >= 0:
        end = at + len(want)
        if not (at and "a" <= low[at - 1] <= "z") and not (end < len(low) and "a" <= low[end] <= "z"):
            return True
        at = low.find(want, at + 1)
    return False


def _is_place(part: str, location: str) -> bool:
    if _MODE.match(part):
        return True
    if not part or _NOT_A_PLACE.search(part) or len(part.split()) > MAX_PLACE_WORDS or \
            any(len(chunk.split()) > MAX_CHUNK_WORDS for chunk in part.split(",")):
        return False
    return _in_location(part, location) or signals.is_us(part) is not None


def _cut(title: str, location: str) -> tuple[str, str] | None:
    """(rest, place) for the first tail shape that ends in a place, else None."""
    for shape in _TAILS:
        m = shape.match(title)
        if m and _is_place(m.group("part").strip(), location):
            return m.group("base").strip(" -–—|:,"), m.group("part").strip()
    return None


@functools.lru_cache(maxsize=SPLITS_KEPT)
def split(title: str | None, location: str | None = None) -> tuple[str, str]:
    """(base title, the place cut off its end, '' when none): 'X - Mountain View, CA, USA' -> ('X', 'Mountain View,
    CA, USA'); 'Software Engineer, AI Data' and 'Engineer - Payments' stay whole."""
    base, places = (title or "").strip(), []
    for _ in range(MAX_TAILS):
        cut = _cut(base, location or "")
        if not cut or not cut[0]:
            break
        base, places = cut[0], [cut[1], *places]
    return base, ", ".join(places)


def title_key(title: str | None, location: str | None = None) -> str:
    """The base title as words (case, punctuation, 'Sr.' = 'Senior'); the level stays: 'Engineer II' is not
    'Engineer I', 'Senior Data Engineer' is not 'Data Engineer'."""
    words = _WORD.findall(split(title, location)[0].lower())
    return " ".join(_SAME.get(w, w) for w in words if w not in _FILLER)


def place(row: dict) -> str:
    """Where this copy is: the place in its title, else its location field."""
    return split(row.get("title"), row.get("location"))[1] or (row.get("location") or "")


def preferences(profile: dict | None) -> tuple[str, ...]:
    """The person's places, best first: their city, its metro's big city, their state ('Round Rock, TX' ->
    'round rock', 'austin', 'tx')."""
    home = ((profile or {}).get("location") or "").strip()
    city, _, state = (part.strip().lower() for part in home.rpartition(","))
    city = city or home.lower()
    return tuple(p for p in (city, _METRO.get(city, ""), state) if p)


def place_rank(row: dict, prefs: tuple[str, ...]) -> int:
    """Lower is better: the person's places in order, then Remote, then a US place, then the rest."""
    where = place(row)
    for i, p in enumerate(prefs):
        if re.search(rf"(?i)(?<![a-z]){re.escape(p)}(?![a-z])", where):
            return i
    if re.search(r"(?i)\bremote\b", where) or (not where and (row.get("workplace") or "").lower() == "remote"):
        return len(prefs)
    return len(prefs) + (1 if signals.is_us(where) else 2)
