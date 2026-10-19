"""Hard-skill lexicon matcher shared by the fit score and `job-hunt-agent ats` (one scorer, GOAL §5/§7)."""
import hashlib
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from . import config

_EDGE = r"[a-z0-9+#]"
# "AWS SageMaker" = "SageMaker": a vendor word in front belongs to the product's mention (decision 007)
_VENDOR = r"(?:(?:aws|amazon|apache|google|microsoft|azure|gcp)[\s-]+)?"


@dataclass(frozen=True)
class Skill:
    name: str
    category: str
    pattern: re.Pattern
    literals: tuple[str, ...] = ()       # cheap substring prefilter: the first word of every alias
    item: re.Pattern | None = None       # short names ("R", "Go"): case-sensitive, only as an item of a list


def _literals(aliases: list[str]) -> tuple[str, ...]:
    return tuple({re.split(r"[\s-]+", a.strip().lower())[0] for a in aliases if a.strip()})


def _alias_pattern(aliases: list[str]) -> re.Pattern:
    parts = sorted({a.strip().lower() for a in aliases if a.strip()}, key=len, reverse=True)
    if not parts:
        return re.compile(r"(?!)")                                  # a short name without aliases: items only
    body = "|".join(re.escape(p).replace(r"\ ", r"[\s-]+") + ("s?" if p[-1].isalpha() else "") for p in parts)
    return re.compile(rf"(?<!{_EDGE}){_VENDOR}(?:{body})(?!{_EDGE})")    # "LLMs", "APIs": plural s (decision 007)


def _item_pattern(name: str) -> re.Pattern:
    """'R' in "Python, R, SQL", "Python or R.", "proficient in R", "C++ or Go is a plus" or its own line; never "R&D",
    "Go-to-market", "Go/No-Go" or "let's go" (case-sensitive, and both sides must look like a list or a skill slot)."""
    before = r"(?:^|[,/;(:]|(?:^|\s)(?:or|and|in|with|like|using))[ \t]*"
    after = r"(?=[ \t]*(?:[,/;):.—–]|$|\s(?:or|and|is|to)\s))(?!/No)"
    lead = r"(?:^|(?<=\s))" + re.escape(name) + r"(?=,[ \t]*[A-Z])"         # "entire Go, Python, and TypeScript"
    return re.compile(f"{before}{re.escape(name)}{after}|{lead}", re.MULTILINE)


@lru_cache(maxsize=4)
def load(path: str | None = None) -> tuple[Skill, ...]:
    data = yaml.safe_load(Path(path or config.shipped("lexicon.yaml")).read_text())
    skills = []
    items_only = set(data.get("list_items_only") or [])
    for category, entries in data["categories"].items():
        for name, aliases in entries.items():
            # Bare names of <=2 letters ("R", "Go") are too ambiguous in prose ("R&D", "go-to-market"): they
            # match case-sensitively as list items only (skill lines, "Python or R", known_skills.txt lines).
            short = (len(name) <= 2 and name.isalpha()) or name in items_only
            terms = [*([] if short else [name]), *(aliases or [])]
            literals = (*_literals(terms), name.lower()) if short else _literals(terms)
            skills.append(Skill(name, category, _alias_pattern(terms), literals,
                                _item_pattern(name) if short else None))
    return tuple(skills)


def lexicon_hash(path: str | None = None) -> str:
    return hashlib.sha1(Path(path or config.shipped("lexicon.yaml")).read_bytes()).hexdigest()[:12]


@lru_cache(maxsize=4)
def settings(path: str | None = None) -> dict:
    """The `keywords:` block (decision 007): display_ignore, pct_mode, min_corpus_jds."""
    data = yaml.safe_load(Path(path or config.shipped("lexicon.yaml")).read_text())
    return {"display_ignore": [], "pct_mode": "count", "min_corpus_jds": 3, **(data.get("keywords") or {})}


def spans(text: str, skills: tuple[Skill, ...] | None = None) -> list[tuple[int, int, str]]:
    """(start, end, canonical) of every mention. A mention lying inside a longer mention of another skill is dropped
    (decision 007): "AWS SageMaker" is SageMaker only, "Spark SQL" Apache Spark only, "Azure OpenAI" Azure AI Foundry."""
    text = text or ""
    lowered = text.lower()
    hits: list[tuple[int, int, str]] = []
    for skill in skills or load():
        if skill.literals and not any(lit in lowered for lit in skill.literals):
            continue
        hits += [(m.start(), m.end(), skill.name) for m in skill.pattern.finditer(lowered)]
        if skill.item:      # the item pattern consumes the delimiter before the name; the name ends the match
            hits += [(m.end() - len(skill.name), m.end(), skill.name) for m in skill.item.finditer(text)]
    hits.sort(key=lambda h: (h[0], -(h[1] - h[0])))
    return [h for h in hits if not any(o[2] != h[2] and o[0] <= h[0] and h[1] <= o[1] and o[1] - o[0] > h[1] - h[0]
                                       for o in hits)]


def find(text: str, skills: tuple[Skill, ...] | None = None) -> dict[str, int]:
    """Canonical skill -> number of mentions in text (case-insensitive, word-boundary aware, longest mention wins)."""
    found: dict[str, int] = {}
    for _, _, name in spans(text, skills):
        found[name] = found.get(name, 0) + 1
    return found


def category_of(name: str) -> str | None:
    return next((s.category for s in load() if s.name == name), None)


@lru_cache(maxsize=4)
def related_groups(path: str | None = None) -> tuple[frozenset[str], ...]:
    """config/related_skills.yaml (decision 010): curated groups of interchangeable tools; unknown names are refused."""
    file = Path(path or config.shipped("related_skills.yaml"))
    if not file.exists():
        return ()
    known = {s.name for s in load()}
    groups = []
    for group in (yaml.safe_load(file.read_text()) or {}).get("groups") or []:
        unknown = [g for g in group if g not in known]
        if unknown:
            raise config.ConfigError(f"related_skills.yaml: not lexicon names: {unknown}")
        groups.append(frozenset(group))
    return tuple(groups)


def related(name: str) -> frozenset[str]:
    """Skills curated as interchangeable with `name` (not itself)."""
    return frozenset().union(*(g for g in related_groups() if name in g)) - {name}
