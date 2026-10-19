"""'Applied before' flag (GOAL §5, decision 002): job_uid link first, else fuzzy company + title vs the tracker.

Company: same words after stripping suffixes (spacing ignored), one name plus only corporate extras ('JPMorgan' ~
'JPMorgan Chase', 'Meta' ~ 'Meta Platforms'), or a typo-level SequenceMatcher >= .95. Subsidiaries do NOT match
('Accenture' !~ 'Accenture Federal Services', 'Cohere' !~ 'Cohere Health').
Same role: title token Jaccard >= .6 AND SequenceMatcher >= .80 on the normalized title. A flag only, never a score.
"""
import difflib
import functools
import re
from dataclasses import dataclass
from datetime import date

from .text import norm

COMPANY_RATIO = 0.95
TITLE_JACCARD = 0.6
TITLE_RATIO = 0.80
MUTE_DAYS = 180
APPLIED_STATES = ("applied", "screen", "interview", "offer", "rejected", "ghosted")
_COMPANY_SUFFIXES = {"inc", "llc", "corp", "corporation", "co", "ltd", "limited", "plc", "the", "group", "holdings",
                     "technologies", "technology", "company", "ai", "labs", "hq"}
_COMPANY_EXTRAS = {"chase", "platforms", "bank", "usa", "us", "america", "international", "global", "security",
                   "financial", "software", "systems"}
_TITLE_DROP = {"sr", "senior", "jr", "junior", "i", "ii", "iii", "iv", "1", "2", "3", "the", "a", "of", "and"}
_TITLE_EXPAND = {"ml": "machine learning", "mle": "machine learning engineer", "swe": "software engineer",
                 "ds": "data scientist", "genai": "generative ai"}


@dataclass(frozen=True)
class AppliedRef:
    company_key: str
    title: str                 # normalized title
    applied_on: date | None
    job_uid: str | None


RENAMES_FILE = "company_renames.csv"          # names_fix.py writes it
KEY_CACHE = 65536            # company / title keys are pure functions of the text; feeds ask for the same ones


@functools.lru_cache(maxsize=KEY_CACHE)
def _plain_key(name: str | None) -> str:
    """norm() drops '&', so 'and' goes too: 'Robots & Pencils' and 'Robots and Pencils' share a key."""
    return " ".join(w for w in norm(name).split() if w not in _COMPANY_SUFFIXES and w != "and")


@functools.lru_cache(maxsize=1)
def _renamed() -> dict[str, str]:
    """a board renamed to how the company writes itself ('sense' -> '6sense') keeps matching tracker rows
    typed with the old name: former key -> current key, from config/lists/company_renames.csv."""
    from .seedlists import _rows                              # late: seedlists reads config files
    pairs = ((_plain_key(r.get("old")), _plain_key(r.get("new"))) for r in _rows(RENAMES_FILE))
    return {old: new for old, new in pairs if old and new and old != new}


def company_key(name: str | None) -> str:
    """'JPMorgan Chase & Co.' -> 'jpmorgan chase'; 'Meta Platforms, Inc.' -> 'meta platforms'; a former board name
    -> its current name's key."""
    key = _plain_key(name)
    return _renamed().get(key, key)


@functools.lru_cache(maxsize=KEY_CACHE)
def normalize_title(title: str | None) -> str:
    words = []
    for w in norm(re.sub(r"[/,\-–—()]", " ", title or "")).split():
        if w not in _TITLE_DROP:
            words.extend(_TITLE_EXPAND.get(w, w).split())
    return " ".join(words)


def _core(key: str) -> str:
    return "".join(w for w in key.split() if w not in _COMPANY_EXTRAS) or key.replace(" ", "")


def _close(x: str, y: str) -> bool:
    """SequenceMatcher ratio >= COMPANY_RATIO; the quick ratios are upper bounds, so they only skip sure misses."""
    sm = difflib.SequenceMatcher(None, x, y)
    return sm.real_quick_ratio() >= COMPANY_RATIO and sm.quick_ratio() >= COMPANY_RATIO and sm.ratio() >= COMPANY_RATIO


def same_company(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a.replace(" ", "") == b.replace(" ", "") or _core(a) == _core(b):
        return True
    x, y = a.replace(" ", ""), b.replace(" ", "")
    if 2 * min(len(x), len(y)) / (len(x) + len(y)) < COMPANY_RATIO:      # ratio can't reach the bar: skip difflib
        return False
    return difflib.SequenceMatcher(None, x, y).ratio() >= COMPANY_RATIO


class AppliedIndex:
    """Applications of one profile with per-company matches memoized (one lookup per distinct company). Same answer
    as same_company() pair by pair (the feed scores ~10k jobs per request): exact and core keys by dict,
    the typo-level check only against names whose length can pass the ratio bar."""

    def __init__(self, refs: tuple[AppliedRef, ...]):
        self.refs = refs
        self.by_uid = {r.job_uid: r for r in refs if r.job_uid}
        self._by_key: dict[str, list[AppliedRef]] = {}
        for r in refs:
            if r.company_key:
                self._by_key.setdefault(r.company_key, []).append(r)
        self._by_flat: dict[str, set[str]] = {}
        self._by_core: dict[str, set[str]] = {}
        self._by_len: dict[int, list[tuple[str, str]]] = {}
        for k in self._by_key:
            flat = k.replace(" ", "")
            self._by_flat.setdefault(flat, set()).add(k)
            self._by_core.setdefault(_core(k), set()).add(k)
            self._by_len.setdefault(len(flat), []).append((k, flat))
        self._companies: dict[str, list[AppliedRef]] = {}

    def _keys(self, key: str) -> set[str]:
        flat = key.replace(" ", "")
        hits = self._by_flat.get(flat, set()) | self._by_core.get(_core(key), set())
        n = len(flat)
        for length in range(int(n * COMPANY_RATIO / (2 - COMPANY_RATIO)), int(n * (2 - COMPANY_RATIO) / COMPANY_RATIO) + 2):
            if 2 * min(n, length) / (n + length) < COMPANY_RATIO:
                continue
            for k, other in self._by_len.get(length, ()):
                if k not in hits and _close(flat, other):
                    hits = hits | {k}
        return hits

    def at_company(self, company: str) -> list[AppliedRef]:
        key = company_key(company)
        if key not in self._companies:
            keys = self._keys(key) if key else set()
            self._companies[key] = [r for k, rs in self._by_key.items() if k in keys for r in rs]
        return self._companies[key]

    def __bool__(self) -> bool:
        return bool(self.refs)


def same_title(a: str, b: str) -> bool:
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return False
    jaccard = len(ta & tb) / len(ta | tb)
    return jaccard >= TITLE_JACCARD and difflib.SequenceMatcher(None, a, b).ratio() >= TITLE_RATIO


_LOADED: dict[str, AppliedIndex] = {}       # profile -> last index: its per-company memo survives requests


def load(conn, profile_id: str) -> AppliedIndex:
    marks = ",".join("?" * len(APPLIED_STATES))
    rows = conn.execute(f"SELECT company, role_title, applied_on, job_uid FROM applications "
                        f"WHERE profile = ? AND status IN ({marks})", (profile_id, *APPLIED_STATES)).fetchall()
    refs = tuple(AppliedRef(company_key(r["company"]), normalize_title(r["role_title"]),
                            date.fromisoformat(r["applied_on"]) if r["applied_on"] else None, r["job_uid"]) for r in rows)
    known = _LOADED.get(profile_id)
    if known is not None and known.refs == refs:
        return known
    _LOADED[profile_id] = index = AppliedIndex(refs)
    return index


def match(uid: str, company: str, title: str, refs) -> tuple[AppliedRef | None, int]:
    """(newest same-role application or None, number of applications at this company)."""
    index = refs if isinstance(refs, AppliedIndex) else AppliedIndex(tuple(refs))
    at_company = index.at_company(company)
    linked = index.by_uid.get(uid)
    if linked:
        return linked, max(1, len(at_company))
    wanted = normalize_title(title)
    same = [r for r in at_company if same_title(wanted, r.title)]
    return max(same, key=lambda r: r.applied_on or date.min, default=None), len(at_company)
