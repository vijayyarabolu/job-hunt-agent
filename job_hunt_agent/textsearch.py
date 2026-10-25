"""GOAL35 §5.3: the one search every search box uses.

- case, accents, punctuation and slashes are ignored: "AI/ML" = "AI ML" = "ai-ml", and "AIML" finds "AI ML" (the text
  is also compared with its spaces taken out);
- words in any order, and every word must match somewhere in the record;
- a word of 5+ letters may be one edit off (two for 9+): "antrhopic" finds Anthropic;
- company aliases both ways ("Meta" finds Facebook, "AWS" finds Amazon; config/lists/h1b_aliases.csv);
- a date in the query ("2026-10-12", "10/12", "Oct 12") matches the record's dates instead of its text.

Decision: SQLite FTS5 is present, but none of its tokenizers forgive a typo, join "AI ML" to
"AIML" or know an alias, so records are normalized once and kept in memory per (id, version); the user's whole history
(~600 applications with their JDs) searches in well under the 200 ms budget (measured in tests/test_goal35_search.py).
"""
import csv
import re
import unicodedata
import zlib
from bisect import bisect_left
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache

from . import config

TYPO_MIN = 5                   # words this long may be one edit off
TYPO_TWO = 9                   # and this long, two
MAX_QUERY = 200
SHORT = 2                      # a word this short matches whole words (and joined pairs), not as a prefix: "ai" != "aim"
LONG_FIELDS = {"jd", "answer", "text"}       # long text: its words only (no joined pairs: build time)
MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                      "dec"), 1)}
_ISO = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_US = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b")
_MON = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
_NAMED = re.compile(rf"\b{_MON}\s+(\d{{1,2}})(?:,?\s+(\d{{4}}))?\b", re.I)
_NAMED_DAY_FIRST = re.compile(rf"\b(\d{{1,2}})\s+{_MON}(?:,?\s+(\d{{4}}))?\b", re.I)


def norm(text) -> str:
    """Lower case, no accents, every run of non-letters / digits one space ("AI/ML Eng." -> "ai ml eng")."""
    if text is None:
        return ""
    plain = unicodedata.normalize("NFKD", str(text))
    plain = "".join(ch for ch in plain if not unicodedata.combining(ch)).lower().replace("&", " and ")
    return " ".join(re.findall(r"[a-z0-9]+", plain))


@dataclass(frozen=True)
class Query:
    raw: str
    words: tuple[str, ...]
    dates: tuple[tuple[int | None, int, int], ...] = ()        # (year or None, month, day)

    def __bool__(self) -> bool:
        return bool(self.words or self.dates)


def _year(text: str | None) -> int | None:
    if not text:
        return None
    n = int(text)
    return n + 2000 if n < 100 else n


_DATE_FORMS = ((_ISO, lambda m: (int(m[1]), int(m[2]), int(m[3]))),
               (_US, lambda m: (_year(m[3]), int(m[1]), int(m[2]))),
               (_NAMED, lambda m: (_year(m[3]), MONTHS[m[1].lower()[:3]], int(m[2]))),
               (_NAMED_DAY_FIRST, lambda m: (_year(m[3]), MONTHS[m[2].lower()[:3]], int(m[1]))))


def parse(q: str | None) -> Query:
    """The query's words (normalized) and its dates (taken out of the words)."""
    text, dates = str(q or "")[:MAX_QUERY], []
    for rx, pick in _DATE_FORMS:
        for m in list(rx.finditer(text)):
            y, mo, d = pick(m)
            if 1 <= mo <= 12 and 1 <= d <= 31:
                dates.append((y, mo, d))
                text = text.replace(m[0], " ")
    return Query(str(q or "")[:MAX_QUERY], tuple(dict.fromkeys(norm(text).split())), tuple(dates))


@lru_cache(maxsize=1)
def _alias_groups() -> dict[str, frozenset[str]]:
    """normalized name -> every normalized name of the same company (h1b_aliases.csv, both ways)."""
    groups: dict[str, set[str]] = {}
    try:
        with open(config.shipped("lists") / "h1b_aliases.csv", newline="", encoding="utf-8") as f:
            pairs = [(norm(r["alias"]), norm(r["company"])) for r in csv.DictReader(f)]
    except (OSError, KeyError):
        pairs = []
    for alias, company in pairs:
        group = groups.get(alias, set()) | groups.get(company, set()) | {alias, company}
        for name in group:
            groups[name] = group
    return {k: frozenset(v) for k, v in groups.items()}


def aliases(text: str) -> frozenset[str]:
    """Every name of the company a normalized name names ("meta" -> {"facebook", "meta"})."""
    return _alias_groups().get(text, frozenset())


def edits(a: str, b: str, limit: int) -> int:
    """Optimal string alignment distance (a swap of two letters is one edit), stopped once it passes `limit`."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev2, prev = None, list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] != b[j - 1]))
            if prev2 is not None and i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        if min(cur) > limit:
            return limit + 1
        prev2, prev = prev, cur
    return prev[-1]


def _allowed(word: str) -> int:
    return 2 if len(word) >= TYPO_TWO else 1 if len(word) >= TYPO_MIN else 0


def _as_date(value) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


@dataclass
class Record:
    """One searchable thing: its id, a version (the cache key), its fields' raw text and its dates."""
    id: object
    fields: dict
    dates: tuple = ()
    version: object = None


@dataclass
class Index:
    """Records normalized once: token -> record positions, with each word's run of the next one or two words joined
    ("ai ml" is also "aiml"), each record's per-field words (where it matched) and dates."""
    records: list
    postings: dict = field(default_factory=dict)
    vocab: list = field(default_factory=list)
    plain: dict = field(default_factory=dict)          # word length -> the plain words (typo candidates)
    field_words: list = field(default_factory=list)
    dates: list = field(default_factory=list)

    @classmethod
    def build(cls, records: list[Record]) -> "Index":
        ix = cls(list(records))
        for i, r in enumerate(ix.records):
            per_field, plain = _tokens(r)
            for tokens in per_field.values():
                for token in tokens:
                    ix.postings.setdefault(token, set()).add(i)
            for w in plain:
                ix.plain.setdefault(len(w), set()).add(w)
            ix.field_words.append(per_field)
            ix.dates.append({d for d in map(_as_date, r.dates) if d})
        ix.vocab = sorted(ix.postings)
        return ix

    def _prefixed(self, word: str) -> list[str]:
        lo = bisect_left(self.vocab, word)
        hi = bisect_left(self.vocab, word + "\uffff")
        return self.vocab[lo:hi]

    def _starts_word(self, rest: str) -> bool:
        return any(rest[:n] in self.plain.get(n, ()) for n in range(1, len(rest) + 1))

    def _typos(self, word: str) -> list[str]:
        limit = _allowed(word)
        if not limit:
            return []
        near = (v for n in range(len(word) - limit, len(word) + limit + 1) for v in self.plain.get(n, ()))
        return [v for v in near if v[:1] in (word[:1], word[1:2]) and edits(word, v, limit) <= limit]

    def wants(self, word: str) -> "Want":
        """What a query word stands for: the tokens it starts, its company aliases, else the tokens it is a typo of."""
        found = self._prefixed(word)
        if len(word) <= SHORT:                       # the word itself, or it joined to the next word ("ai ml")
            found = [v for v in found if v == word or (v not in self.plain.get(len(v), ()) and self._starts_word(v[len(word):]))]
        alias = [t for t in (n.replace(" ", "") for n in aliases(word) if n != word) if t in self.postings]   # whole names
        typos = [] if found or alias else self._typos(word)
        return Want(frozenset([word, *typos]), frozenset(found or typos), frozenset(alias), tuple(found + alias or typos))

    def tokens(self, word: str) -> list[str]:
        """The record tokens a query word stands for: those it starts, an alias, else those it is a typo of."""
        return list(self.wants(word).tokens)

    def _alias_phrases(self, q: Query) -> tuple[set[str], list[str], set[str]]:
        """Multi-word aliases in the query ("amazon web services"): the words they cover, their names' tokens and the
        tokens of the names typed."""
        text, covered, tokens, typed = f" {' '.join(q.words)} ", set(), [], set()
        for name, group in _alias_groups().items():
            if " " in name and f" {name} " in text:
                covered |= set(name.split())
                typed.add(name.replace(" ", ""))
                tokens += [t for t in (g.replace(" ", "") for g in group) if t in self.postings]
        return covered, tokens, typed

    def search(self, q: Query) -> "Found":
        """The positions of the records every query word and date matches, in record order, and the tokens to
        highlight."""
        hits = set(range(len(self.records)))
        for want in q.dates:
            hits &= {i for i in hits if any(_same_day(want, d) for d in self.dates[i])}
        covered, phrase, typed = self._alias_phrases(q) if len(q.words) > 1 else (set(), [], set())
        wants = [Want(frozenset(typed), frozenset(typed), frozenset(phrase) - typed, tuple(phrase))] if phrase else []
        wants += [self.wants(w) for w in q.words if w not in covered]
        lit = []
        for want in wants:
            hits &= set().union(*(self.postings[t] for t in want.tokens)) if want.tokens else set()
            lit += want.tokens
        return Found(sorted(hits), tuple(dict.fromkeys(lit)), self, tuple(wants))


_MEMO: dict = {}
MEMO_MAX = 20000                 # records' tokens kept between index builds (an edit re-reads one record, not all)


def _tokens(r: Record) -> tuple[dict, set]:
    """(field -> its tokens, the plain words) of one record, memoized by its id and a checksum of its text."""
    key = (r.id, zlib.crc32("\x1f".join(f"{k}={v or ''}" for k, v in r.fields.items()).encode()))
    hit = _MEMO.get(key)
    if hit is None:
        per_field, plain = {}, set()
        for name, value in r.fields.items():
            words = norm(value).split()
            plain.update(words)
            per_field[name] = set(words) if name in LONG_FIELDS else {
                t for k, w in enumerate(words) for t in (w, w + "".join(words[k + 1:k + 2]), w + "".join(words[k + 1:k + 3]))}
        if len(_MEMO) >= MEMO_MAX:
            _MEMO.clear()
        hit = _MEMO[key] = (per_field, plain)
    return hit


def _same_day(want: tuple[int | None, int, int], d: date) -> bool:
    y, m, day = want
    return d.month == m and d.day == day and (y is None or d.year == y)


@dataclass(frozen=True)
class Want:
    """One query word (or a multi-word alias typed whole): the words it is exactly (itself, or its typos), the tokens
    it stands for by itself (prefixes, or typos), its company aliases ("aws" for "amazon"), and all it matched."""
    exact: frozenset
    own: frozenset
    alias: frozenset
    tokens: tuple


# 2026-10-24 ("I typed Amazon and it shows something else"): how well a record matched; lower is better
COMPANY, COMPANY_ALIAS, COMPANY_PART, NAMED, SHORT_FIELD, LONG_ONLY = range(6)
NAME_FIELDS = ("company", "title")


@dataclass
class Found:
    positions: list
    terms: tuple
    index: Index
    wants: tuple = ()
    _tiers: dict = field(default_factory=dict, repr=False)

    def ids(self) -> list:
        return [self.index.records[i].id for i in self.positions]

    def tier(self, position: int) -> int:
        """COMPANY every word is a word of the company name (or its typo); COMPANY_ALIAS or one of its aliases ("AWS"
        for Amazon); COMPANY_PART or starts one ("meta" in "Metals"); NAMED every word in the company or the title
        (an alias counts only in the company); SHORT_FIELD every word in a short field (city, status, notes, contacts,
        resume, link); LONG_ONLY the rest: a word only in long text (the JD) or only by an alias outside the company."""
        if position not in self._tiers:
            self._tiers[position] = self._tier(self.index.field_words[position])
        return self._tiers[position]

    def _tier(self, fw: dict) -> int:
        if not self.wants:
            return SHORT_FIELD                       # a date-only query
        company = fw.get("company", set())
        if all(company & w.exact for w in self.wants):
            return COMPANY
        if all(company & (w.exact | w.alias) for w in self.wants):
            return COMPANY_ALIAS
        if all(company & (w.own | w.alias) for w in self.wants):
            return COMPANY_PART
        named = lambda names: all(company & w.alias or any(fw.get(f, set()) & w.own for f in names) for w in self.wants)
        if named(NAME_FIELDS):
            return NAMED
        return SHORT_FIELD if named([f for f in fw if f not in LONG_FIELDS]) else LONG_ONLY

    def ranked(self) -> list:
        """The positions, best tier first (record order kept inside a tier)."""
        return sorted(self.positions, key=self.tier)

    def names_company(self) -> bool:
        """The query is a company's name (or an alias of one) that some record has as its company."""
        return any(self.tier(i) <= COMPANY_ALIAS for i in self.positions)

    def strong(self) -> list:
        """The positions to show: when the query names a company, the records that name it somewhere other than long
        text ("amazon": not every JD that says "Amazon Bedrock" or every title that says AWS); else all of them (one title
        that says "LangGraph" or "RAG" must not hide the JDs that ask for the skill)."""
        if not self.names_company():
            return list(self.positions)
        return [i for i in self.positions if self.tier(i) < LONG_ONLY]

    def where(self, position: int) -> list[str]:
        """The fields this record matched in ("jd" when only its JD has the words)."""
        terms = set(self.terms)
        return [name for name, words in self.index.field_words[position].items() if words & terms]


def search(records: list[Record], q: str | Query) -> Found:
    """One-off search (build the index each time); a page that searches often keeps an Index (cached())."""
    return Index.build(records).search(q if isinstance(q, Query) else parse(q))


_CACHE: dict = {}
CACHE_KEEP = 8


def cached(key, records_of) -> Index:
    """The index for `key` (e.g. ('tracker', profile, fingerprint)); built from records_of() when the key is new."""
    hit = _CACHE.get(key)
    if hit is None:
        hit = Index.build(records_of())
        if len(_CACHE) >= CACHE_KEEP:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[key] = hit
    return hit
