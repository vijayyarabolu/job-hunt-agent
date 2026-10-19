"""Keyword match view per resume (GOAL4 §1/§3, decision 007). Pure functions over a fits.terms pack:
[name, weight, hitmask, need, count, first, forms, litmask] where bit i of hitmask = the profile's i-th resume
contains the term (any alias) and bit i of litmask = it contains one of the JD's own wordings `forms` (decision 010;
older 6- and 4-field rows still read, without literal data).

keyword % = matched / extracted (unweighted, resume text only: no known_skills, every language counts, minus
lexicon keywords.display_ignore); pct_mode 'weighted' uses importance instead. Missing keywords come in importance,
count, first-position order. The best resume has the highest keyword %; ties -> the title-mapped one -> weighted.
"""
import json
import re
from dataclasses import dataclass
from functools import lru_cache

from . import lexicon
from .resume import ats


@dataclass(frozen=True)
class ResumeScore:
    id: str
    label: str
    pct: int                      # the keyword % shown to the user
    weighted: float               # importance-weighted coverage 0..1 (tie-break, detail)
    matched: tuple[str, ...]      # by importance
    missing: tuple[str, ...]      # by importance, count, first position
    n_total: int
    keywords: tuple[str, ...] = ()    # every counted JD keyword, most important first

    def as_dict(self) -> dict:
        return {"id": self.id, "label": self.label, "pct": self.pct, "weighted": round(100 * self.weighted),
                "matched": list(self.matched), "missing": list(self.missing), "n_matched": len(self.matched),
                "n_missing": len(self.missing), "n_total": self.n_total}


def unpack(fit_terms: str | None) -> list[tuple[ats.Term, int]]:
    """(Term, hitmask) pairs of a fits.terms pack, every shape (8-, 6- and 4-field)."""
    return [(t, mask) for t, mask, _ in unpack_full(fit_terms)]


def unpack_full(fit_terms: str | None) -> list[tuple[ats.Term, int, int | None]]:
    """(Term, hitmask, litmask) triples; litmask is None for a row built before decision 010."""
    out = []
    for item in json.loads(fit_terms or "[]"):
        name, weight, mask, need = item[:4]
        count, first = (item[4], item[5]) if len(item) >= 6 else (1, 0)
        forms, lit = (tuple(item[6]), int(item[7])) if len(item) >= 8 else ((), None)
        out.append((ats.Term(name, weight, bool(need), count, first, forms), int(mask), lit))
    return out


@lru_cache(maxsize=4096)
def _form_pattern(form: str) -> re.Pattern:
    return re.compile(r"(?<![a-z0-9])" + re.escape(" ".join(form.lower().split())) + r"(?![a-z0-9])")


def has_form(text: str, forms: tuple[str, ...]) -> bool:
    """One of the JD's wordings, word-bounded, in a normalized resume text (case-insensitive; nothing else folded)."""
    return any(_form_pattern(f).search(text) for f in forms)


def pack(terms: list[ats.Term], resume_terms: tuple[frozenset[str], ...],
         resume_texts: tuple[str, ...] = ()) -> list[list]:
    return [[t.name, t.weight, sum(1 << i for i, have in enumerate(resume_terms) if t.name in have), int(t.need),
             t.count, t.first, list(t.forms), sum(1 << i for i, text in enumerate(resume_texts)
                                                  if has_form(text, t.forms))] for t in terms]


def _order(term: ats.Term) -> tuple:
    return -term.importance, -term.count, term.first, term.name


def score_one(pairs: list[tuple[ats.Term, int]], bit: int, rid: str, label: str) -> ResumeScore:
    knobs = lexicon.settings()
    ignore = set(knobs["display_ignore"])
    shown = sorted((t for t, _ in pairs if t.name not in ignore), key=_order)
    hit = {t.name for t, mask in pairs if mask >> bit & 1}
    matched = tuple(t.name for t in shown if t.name in hit)
    missing = tuple(t.name for t in shown if t.name not in hit)
    total_w = sum(t.importance for t in shown)
    weighted = sum(t.importance for t in shown if t.name in hit) / total_w if total_w else 0.0
    count_pct = len(matched) / len(shown) if shown else 0.0
    pct = weighted if knobs["pct_mode"] == "weighted" else count_pct
    return ResumeScore(rid, label, round(100 * pct), weighted, matched, missing, len(shown),
                       tuple(t.name for t in shown))


def score_all(fit_terms: str | None, resumes) -> list[ResumeScore]:
    """One ResumeScore per resume (profile order); `resumes` are resumes.Resume (only id and label are read)."""
    pairs = unpack(fit_terms)
    return [score_one(pairs, i, r.id, r.label) for i, r in enumerate(resumes)]


def best(scores: list[ResumeScore], title_id: str | None = None) -> ResumeScore | None:
    if not scores:
        return None
    return max(scores, key=lambda s: (s.pct, s.id == title_id, s.weighted))    # equal % -> the title's resume
