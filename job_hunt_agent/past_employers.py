"""companies a person already worked at are never shown to them ("Don't bring the jobs at companies I
already worked at"). Per profile, a `past_employers` list in profile.yaml (Settings, editable); until the person saves
one, it defaults to the companies of their base resume's Work Experience (baseline.json).

A job's company is a past employer when its GOAL11 brand key (applied.company_key) is a listed name's key, one of the
name's known aliases (legal names, subsidiaries: 'Deloitte Consulting', 'IBM Global Services', 'Accenture Federal
Services'), or starts with one of those keys as whole words ('Deloitte Digital'). score.final marks
such a job 'past_employer', so it is in no feed, no push, no "new matches" count and gets no resume; a board whose
company is a past employer of every profile is not fetched at all (registry.boards). The tracker is never touched.
"""
import functools
import json
import re

from . import config
from .applied import company_key

KEY = "past_employers"
MAX_NAMES = 30
MAX_NAME = 80
# brand key -> other names the same employer posts under (matched like the brand itself, word prefix included)
ALIASES = {
    "deloitte": ("Deloitte Consulting", "Deloitte LLP", "Deloitte Digital", "Deloitte Touche", "Deloitte Services"),
    "ibm": ("IBM Consulting", "IBM Tech", "International Business Machines", "IBM Global Services", "IBM Software",
            "IBMSoftware"),
    "accenture": ("Accenture Federal Services", "Accenture LLP", "Accenture Song", "Avanade"),
    "pwc": ("PricewaterhouseCoopers", "PwC US", "PwC Advisory"),
}
_PLACE_TAIL = re.compile(r"\s*,\s*[A-Za-z .]{2,30}\.?\s*$")       # 'Deloitte, USA.' -> 'Deloitte'


def clean(name) -> str:
    """'Globex Properties, USA.' -> 'Globex Properties'; whitespace collapsed."""
    return " ".join(_PLACE_TAIL.sub("", str(name or "")).split()).strip(" .,")


def from_resume(pid: str) -> list[str]:
    """The companies in the person's base resume Work Experience, in resume order ([] without a baseline)."""
    path = config.PROFILES / pid / "baseline.json"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return []
    return list(_from_resume(str(path), mtime))


@functools.lru_cache(maxsize=8)
def _from_resume(path: str, mtime: float) -> tuple[str, ...]:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return ()
    names = [clean(e.get("company")) for e in data.get("experience") or [] if isinstance(e, dict)]
    return tuple(dict.fromkeys(n for n in names if n))


def names(profile: dict) -> tuple[str, ...]:
    """The profile's saved list, else the resume default."""
    saved = profile.get(KEY)
    if isinstance(saved, list):
        return tuple(dict.fromkeys(clean(n) for n in saved if clean(n)))
    return tuple(from_resume(profile["id"]))


def keys(listed) -> frozenset[str]:
    """Company keys of the listed names and their aliases."""
    out = set()
    for name in listed:
        key = company_key(name)
        if key:
            out.add(key)
            out.update(company_key(alias) for alias in ALIASES.get(key, ()))
    return frozenset(k for k in out if k)


def matches(company: str | None, past: frozenset[str]) -> bool:
    if not past or not company:
        return False
    key = company_key(company)
    flat = key.replace(" ", "")
    return any(key == k or flat == k.replace(" ", "") or key.startswith(f"{k} ") for k in past)


def profile_keys(profile: dict) -> frozenset[str]:
    return keys(names(profile))


def skip_board(company: str, profiles: list[dict]) -> bool:
    """A board nobody may see: its company is a past employer of every profile."""
    return bool(profiles) and all(matches(company, profile_keys(p)) for p in profiles)


def validate(given) -> list[str]:
    """Settings input (a list, or text with one name per line) -> the list to save; ValueError for anything but up to
    MAX_NAMES names of up to MAX_NAME characters."""
    if isinstance(given, str):
        given = re.split(r"[\n;]", given)
    if not isinstance(given, list) or not all(isinstance(n, str) for n in given):
        raise ValueError("past employers: a list of company names")
    cleaned = list(dict.fromkeys(" ".join(n.split()) for n in given if n.strip()))
    if len(cleaned) > MAX_NAMES or any(len(n) > MAX_NAME for n in cleaned):
        raise ValueError(f"past employers: up to {MAX_NAMES} names of up to {MAX_NAME} characters")
    return cleaned
