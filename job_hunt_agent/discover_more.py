"""GOAL16 discovery for the new systems: one generic live check per board, and tenant seed lists.

check(): the adapter's survey() lists the board's open postings as (posted, location, country, title). A board is
US-live (us60 > 0) when a US posting is open and, if the list carries dates, was posted in the last 60 days; lists
with no dates (BambooHR, Rippling) count an open US posting as current. Unlike the GOAL10 search boards these cost one
list request per fetch, so no data/AI title is required to admit a board: the tiers fetch a board with no matched job
once a day and every 30 minutes once it has one.

Seeds (import_seeds): the MIT-licensed tenant lists of github.com/kalil0321/ats-scrapers (`ats-companies/<ats>.csv`,
name,slug,url; Copyright (c) 2026 Kalil Bouzigues). Only the board keys are taken, each re-validated by a strict
pattern before any URL is built (seed data is never trusted); the listed name is kept as the board's fallback name.
They land in data/cc/seed_tokens.json next to the Common Crawl tokens and go through the same live verification.
"""
import csv
import io
import json
import logging
import re
from datetime import datetime
from typing import Callable

from . import company_names, config, seedlists, signals, titles
from .ats import (KEY_FIELDS, adp, bamboohr, breezy, cornerstone, dayforce, detect, gem, hibob, jazzhr, jobscore,
                  paylocity, pinpoint, recruitee, rippling)
from .ats.common import SchemaDrift
from .discover_verify import Verdict, us_recent
from .net import HttpError, get_text

SEED_URL = "https://raw.githubusercontent.com/kalil0321/ats-scrapers/main/ats-companies/{name}.csv"
SURVEYS: dict[str, Callable[[str], dict]] = {"bamboohr": bamboohr.survey, "rippling": rippling.survey,
                                             "gem": gem.survey, "adp": adp.survey, "paylocity": paylocity.survey,
                                             "dayforce": dayforce.survey, "cornerstone": cornerstone.survey,
                                             "jazzhr": jazzhr.survey, "recruitee": recruitee.survey,
                                             "breezy": breezy.survey, "pinpoint": pinpoint.survey,
                                             "hibob": hibob.survey, "jobscore": jobscore.survey}
_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,99}$")
_SUBDOMAIN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_GUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
KEY_PATTERNS = {"bamboohr": _SUBDOMAIN, "rippling": _SLUG, "gem": _SLUG,
                "adp": re.compile(rf"^{_GUID}/[0-9A-Za-z_]{{1,40}}$"), "paylocity": re.compile(rf"^{_GUID}$"),
                "dayforce": re.compile(r"^[a-z0-9_-]{1,64}/[A-Za-z0-9_-]{1,64}$"),
                "cornerstone": re.compile(r"^[a-z0-9][a-z0-9-]{0,62}/\d{1,6}$"), "jazzhr": _SUBDOMAIN,
                "recruitee": _SUBDOMAIN, "breezy": _SUBDOMAIN, "pinpoint": _SUBDOMAIN, "hibob": _SUBDOMAIN,
                "jobscore": re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")}
_TOKEN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")          # discover_cc.TOKEN: Greenhouse / Lever / Ashby slugs
EXISTING = {"greenhouse": _TOKEN, "lever": _TOKEN, "ashby": _TOKEN,            # GOAL16 "expand existing": seeds for
            "workday": re.compile(r"^[a-z0-9-]{1,64}/wd\d{1,3}/[A-Za-z0-9_-]{1,64}$")}   # systems read already
NO_SEEDS = {"jazzhr", "hibob", "jobscore"}        # JazzHR: its own global feed; HiBob, JobScore: the crawl only
SEED_FILES = {ats: ats for ats in (*KEY_PATTERNS, *EXISTING) if ats not in NO_SEEDS}
LOWER_SEEDS = {"greenhouse", "lever", "ashby"}               # stored lower-case, as the crawl tokens are
log = logging.getLogger(__name__)


def _now(now: datetime) -> str:
    return now.strftime("%Y-%m-%dT%H:%M:%SZ")


def us_live(rows: list[tuple], now: datetime) -> tuple[int, str | None]:
    """(open US postings that count as recent, the newest of them); undated US postings count as posted now."""
    dated = [(p, loc, c) for p, loc, c, *_ in rows if p]
    us60, newest = us_recent(dated, now)
    undated = sum(1 for p, loc, c, *_ in rows if not p and signals.is_us(loc, c))
    if undated:
        us60, newest = us60 + undated, max(newest or "", _now(now))
    return us60, newest


def tidy_name(text: str | None) -> str | None:
    """A company name as the board or the seed list writes it, legal suffixes off ('15 Lightyears Inc.' -> '15
    Lightyears'; a leading number is part of the name here, unlike Workday's '2100 NVIDIA' entity codes)."""
    name = company_names.tidy(" ".join(str(text or "").strip().strip('"').split()))
    return name if len(name) >= 2 and any(ch.isalpha() for ch in name) else None


def name_for(ats: str, key: str, found: str | None, listed: str | None) -> tuple[str, str]:
    """board_names.csv, else the name the board shows, else the seed list's name, else the key."""
    pinned = seedlists.board_name(ats, key)
    if pinned:
        return pinned, "list"
    for name, src in ((tidy_name(found), "ats"), (tidy_name(listed), "seed")):
        if name:
            return name, src
    return key.split("/")[0].replace("-", " ").replace("_", " ").title(), "slug"


def check(ats: str, key: str, now: datetime, names: Callable[[str], str | None] = lambda key: None) -> Verdict:
    try:
        found = SURVEYS[ats](key)
    except SchemaDrift:
        return Verdict(0)
    us60, newest = us_live(found["rows"], now)
    if not us60:
        return Verdict(found["n_jobs"], 0, None)
    name, src = name_for(ats, key, found.get("name"), names(key) or seed_names().get(ats, {}).get(key))
    return Verdict(found["n_jobs"], us60, newest, name, src, found.get("website"))


# ---- seed lists ------------------------------------------------------------------------------------------------

def seed_path():
    return config.DATA / "cc" / "seed_tokens.json"


def _pattern(ats: str):
    return KEY_PATTERNS.get(ats) or EXISTING.get(ats)


def key_from_url(url: str) -> tuple[str, str] | None:
    """(ats, board key) of a board URL on one of these systems, strictly validated; None otherwise."""
    found = detect(url)
    if not found or not _pattern(found["ats"]):
        return None
    key = "/".join([found["token"], *(str(found.get(f) or "") for f in KEY_FIELDS.get(found["ats"], ()))])
    key = key.lower() if found["ats"] in LOWER_SEEDS else key
    return (found["ats"], key) if _pattern(found["ats"]).match(key) else None


def parse_seed(ats: str, text: str) -> dict[str, str]:
    """key -> listed name, for rows whose URL is this ATS's board (else whose slug passes its strict key pattern)."""
    pattern = _pattern(ats)
    out: dict[str, str] = {}
    for row in csv.DictReader(io.StringIO(text)):
        found = key_from_url((row.get("url") or "").strip())
        key = found[1] if found and found[0] == ats else (row.get("slug") or "").strip()
        key = key.lower() if ats in LOWER_SEEDS else key
        if pattern.match(key):
            out.setdefault(key, (row.get("name") or "").strip()[:120])
    return out


def import_seeds(atss: tuple[str, ...] | None = None) -> dict[str, int]:
    """Download the seed lists into data/cc/seed_tokens.json (merged with what is there); counts per ATS."""
    current = seed_tokens()
    for ats, name in SEED_FILES.items():
        if atss and ats not in atss:
            continue
        try:
            current[ats] = {**current.get(ats, {}), **parse_seed(ats, get_text(SEED_URL.format(name=name)))}
        except HttpError as err:
            log.warning("seed list %s unavailable: %s", name, err)
    if not atss or "jazzhr" in atss:
        tenants = jazzhr.feed_tenants(titles.compile_union(sorted(titles.FAMILIES)))
        current["jazzhr"] = {**current.get("jazzhr", {}),
                             **{t: "" for t in tenants if _SUBDOMAIN.match(t)}}
    seed_path().parent.mkdir(parents=True, exist_ok=True)
    seed_path().write_text(json.dumps(current, indent=0, sort_keys=True))
    seed_names.cache_clear()
    return {ats: len(keys) for ats, keys in current.items()}


def seed_tokens() -> dict[str, dict[str, str]]:
    try:
        data = json.loads(seed_path().read_text())
    except (OSError, ValueError):
        return {}
    return {ats: {k: v for k, v in keys.items() if _pattern(ats).match(k)}
            for ats, keys in data.items() if isinstance(keys, dict) and _pattern(ats)}


_SEED_NAMES: list[dict] = []


def seed_names() -> dict[str, dict[str, str]]:
    if not _SEED_NAMES:
        _SEED_NAMES.append(seed_tokens())
    return _SEED_NAMES[0]


seed_names.cache_clear = _SEED_NAMES.clear
