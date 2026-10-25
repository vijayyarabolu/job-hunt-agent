"""Feed filters: parse the query string once (ValueError -> HTTP 400) and test one ranked job.

Company size is the board's segment from segments.py, a written rule over data we hold (S&P 500 / YC lists,
open postings on the board, ATS, name, domain), not a headcount: big, startup, mid-size / enterprise, public sector.
"""
import re
from dataclasses import dataclass
from datetime import datetime

from . import feedback, fresh, match, signals, titles

WORKPLACES = ("remote", "hybrid", "onsite")
POSTED_HOURS = {"1h": 1, "6h": 6, "24h": 24, "3d": 72, "7d": 168, "30d": 720}   # GOAL4 §0 quick chips; GOAL11 §8 30d
SORTS = ("newest", "match")                 # Newest first (default) | Best match
SPONS = ("friendly", "no")                  # Sponsor-friendly | No sponsorship (absent = All)
LEVELS = {"le2": (0, 2), "3to5": (3, 5), "6plus": (6, 99)}     # by the JD's minimum required years
EMPLOYMENT = ("full_time", "contract", "contract_to_hire")
SIZES = ("big", "startup", "other", "public_sector")
MAX_SALARY_FLOOR = 1_000_000
TRUE = ("1", "true", "on", "yes")
MAX_OFFSET = 1_000_000
MAX_QUERY = 200
# GOAL11 §8 "Job board": one option per board prefix found in the DB ("workday:acme" -> workday); names as the
# vendors write them, an unknown prefix is title-cased (a new system shows up with no code)
BOARD_PREFIX = re.compile(r"^[a-z0-9_]{1,40}$")
BOARD_LABELS = {"workday": "Workday", "greenhouse": "Greenhouse", "ashby": "Ashby", "lever": "Lever",
                "oracle_recruiting": "Oracle Recruiting", "taleo": "Taleo", "icims_classic": "iCIMS classic",
                "icims": "iCIMS / Jibe", "successfactors": "SuccessFactors", "jobvite": "Jobvite",
                "eightfold": "Eightfold", "phenom": "Phenom", "smartrecruiters": "SmartRecruiters", "amazon": "Amazon", "careeronestop": "State job banks",
                "workable": "Workable",
                # GOAL16
                "bamboohr": "BambooHR", "rippling": "Rippling", "gem": "Gem", "adp": "ADP Workforce Now",
                "paylocity": "Paylocity", "dayforce": "Dayforce", "cornerstone": "Cornerstone", "avature": "Avature",
                "jazzhr": "JazzHR", "usajobs": "USAJOBS", "recruitee": "Recruitee", "breezy": "Breezy HR",
                "pinpoint": "Pinpoint", "teamtailor": "Teamtailor", "consider": "Consider (VC boards)",
                "hibob": "HiBob", "trakstar": "Trakstar Hire", "personio": "Personio", "jobscore": "JobScore",
                "taleo_be": "Taleo Business Edition", "apple": "Apple", "ibm": "IBM", "adp_myjobs": "ADP MyJobs"}


def board_of(row: dict) -> str:
    return (row.get("board") or "").partition(":")[0]


def board_label(prefix: str) -> str:
    return BOARD_LABELS.get(prefix) or prefix.replace("_", " ").title()


@dataclass(frozen=True)
class Filters:
    family: str | None = None
    state: str | None = None
    workplace: str | None = None
    posted_hours: int | None = None
    min_match: int = 0
    salary_floor: int = 0
    level: str | None = None
    show_senior: bool = False
    hide_applied: bool = False
    employment: str | None = None
    size: str | None = None
    sort: str = "newest"
    boards: tuple[str, ...] = ()            # any of these board prefixes; () = every board
    offset: int = 0                         # with a board picked, the page starts here
    dupes_only: bool = False                # GOAL21 W9: only the older copies of a job posted twice
    spons: str | None = None                # GOAL24 §4: Recommended's sponsorship switch (friendly | no; None = All)
    q: str = ""                             # GOAL35 §5.2: the feed's search (textsearch: company, title, place, role)


def _choice(args: dict, key: str, allowed) -> str | None:
    value = (args.get(key) or "").strip()
    if value and value not in allowed:
        raise ValueError(f"{key} must be one of {sorted(allowed)}")
    return value or None


def _int(args: dict, key: str, low: int, high: int) -> int:
    raw = (args.get(key) or "").strip()
    if not raw:
        return 0
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{key} must be a whole number") from None
    if not low <= value <= high:
        raise ValueError(f"{key} must be between {low} and {high}")
    return value


def _boards(args: dict) -> tuple[str, ...]:
    picked = {b.strip() for b in (args.get("board") or "").split(",") if b.strip()}
    bad = sorted(b for b in picked if not BOARD_PREFIX.match(b))
    if bad:
        raise ValueError(f"board must be board names like workday, not {bad[0]!r}")
    return tuple(sorted(picked))


def parse(args: dict) -> Filters:
    state = (args.get("state") or "").strip().upper() or None
    if state and state not in signals.US_STATES:
        raise ValueError("state must be a two-letter US state code")
    posted = _choice(args, "posted", POSTED_HOURS)
    return Filters(family=_choice(args, "family", titles.FAMILIES), state=state,
                   workplace=_choice(args, "workplace", WORKPLACES),
                   posted_hours=POSTED_HOURS[posted] if posted else None,
                   min_match=_int(args, "min_match", 0, 100), salary_floor=_int(args, "salary", 0, MAX_SALARY_FLOOR),
                   level=_choice(args, "level", LEVELS),
                   show_senior=(args.get("senior") or "").lower() in TRUE,
                   hide_applied=(args.get("hide_applied") or "").lower() in TRUE,
                   dupes_only=(args.get("dupes") or "").lower() in TRUE,
                   employment=_choice(args, "employment", EMPLOYMENT), size=_choice(args, "size", SIZES),
                   sort=_choice(args, "sort", SORTS) or "newest", boards=_boards(args),
                   offset=_int(args, "offset", 0, MAX_OFFSET), spons=_choice(args, "spons", SPONS),
                   q=(args.get("q") or "").strip()[:MAX_QUERY])


def _employment_ok(f: Filters, kind: str | None) -> bool:
    if f.employment is None:
        return True
    if f.employment == "full_time":                        # unknown counts as full-time (most ATS leave it empty)
        return kind not in ("contract", "contract_to_hire")
    return kind == f.employment


def _level_ok(f: Filters, min_years: int | None) -> bool:
    if f.level is None:
        return True
    low, high = LEVELS[f.level]
    return low <= (min_years or 0) <= high                  # 'not stated' sits with the entry bucket


def keep(f: Filters, row: dict, state: str, employment: str | None, applied: bool, pct: int | None,
         now: datetime) -> bool:
    """One ranked job against the filters. A job with no posted pay stays visible under a salary floor; one with
    no match % yet stays visible under a min-match filter only when the filter is 0."""
    if state == "too_senior" and not f.show_senior:
        return False
    if f.hide_applied and applied:
        return False
    if f.family and f",{f.family}," not in (row.get("families") or ""):
        return False
    if f.size and row.get("segment") != f.size:
        return False
    if f.state and f.state not in feedback.states_of(row.get("location")):
        return False
    if f.workplace and match.workplace_of(row) != f.workplace:
        return False
    if f.posted_hours is not None:
        hours = fresh.feed_age_hours(row, now)
        if hours is None or hours > f.posted_hours:
            return False
    if f.min_match and (pct is None or pct < f.min_match):
        return False
    top_pay = max(row.get("salary_min") or 0, row.get("salary_max") or 0)
    if f.salary_floor and top_pay and top_pay < f.salary_floor:
        return False
    return _level_ok(f, row.get("min_years")) and _employment_ok(f, employment)
