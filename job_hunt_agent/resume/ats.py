"""ATS keyword coverage (GOAL §7). The same scorer feeds the worth score's fit term (decision 002), so `job-hunt-agent ats`
and the fit chip always report the same number.

extract(jd) -> weighted hard-skill terms (section-aware); coverage(terms, have, known) -> hit weight / total weight.
"""
import math
import re
from dataclasses import dataclass

from .. import lexicon

REQUIRED, PREFERRED, IGNORED_SECTION = 1.0, 0.5, 0.0
EXTRACTOR_VERSION = "7"     # bump when weighted_lines/extract change: every cached fit is rebuilt (fits.py)
# resume-editor Phase 2 auto-ignore: these languages count only when the profile confirmed them (known_skills).
IGNORED_LANGUAGES = frozenset({"Java", "C++", "Ruby", "Perl", "Scala"})
MAX_HEADING_CHARS = 80
MAX_HEADING_WORDS = 5

# Company-blurb headings ('About Acme', 'Our mission'): skipped, unless that leaves no counted keyword at all (then the
# JD never said where the role starts: Zocdoc, Turing) and extract() reads again without these skips.
_BLURB = re.compile(r"^\W*about\b(?!\s+(the\s+|this\s+)?(role|you|job|position|opportunity|team|work))|"
                    r"\b(who we are|our (mission|story|values|culture)|life at)\b", re.I)
_SKIP = re.compile(r"\b(benefits|perks|what we offer|compensation|pay (range|transparency)|salary|"
                   r"equal (employment )?opportunity|eeo|accommodations?|privacy|disclaimer|"
                   r"(we('ll| will)|we) do for you)\b", re.I)
# Inside a skipped section ('About Signify') a heading like these ends the skip ('More about you', "S'more about the
# team", 'Your day-to-day'); benefit items such as 'Free Snowflake credits' do not.
_RESUME = re.compile(r":\s*$|\b(you|your|role|team|position)\b", re.I)
_PREFERRED = re.compile(r"\b(preferred|nice[- ]to[- ]have|bonus|a plus|pluses|desired|good to have|ideally|"
                        r"extra credit|great if|would be great)\b", re.I)
# Inside a preferred section only these switch back to required without a colon ('Frontend depth and platform
# experience' is a preferred item, not a heading, though it is short and says 'experience').
_REQUIRED_STRONG = re.compile(r"\b(requirements?|qualifications?|must[- ]haves?|responsibilities|what you('ll| will) do|"
                              r"minimum|basic|about the role|the role|job description)\b", re.I)
_REQUIRED = re.compile(r"\b(requirements?|qualifications?|must[- ]haves?|you (have|bring|will)|what you('ll| will)|"
                       r"responsibilities|what we('re| are) looking for|skills|experience|about the role|the role|"
                       r"job description|minimum|basic|day-to-day|you should apply|working on)\b", re.I)


@dataclass(frozen=True)
class Term:
    name: str          # canonical lexicon name
    weight: float      # 1.0 required/unsectioned, 0.5 preferred
    need: bool         # appears in a required (weight 1.0) line
    count: int = 1     # mentions in counted lines (decision 007)
    first: int = 0     # position of the first mention (orders ties)
    forms: tuple[str, ...] = ()   # the JD's own wordings, first mention first (decision 010: literal vendors)

    @property
    def importance(self) -> float:
        """Section weight x (1 + log2(mentions)): what orders the missing list (decision 007)."""
        return self.weight * (1 + math.log2(max(1, self.count)))


@dataclass(frozen=True)
class Coverage:
    hw: float                  # hit weight
    tw: float                  # total weight
    pct: float                 # hw / tw (0.0 when tw == 0)
    missing: tuple[str, ...]   # by weight desc, then name


def _is_heading(line: str) -> bool:
    """'Preferred Qualifications', 'Bonus points if you have:' -- short or colon-terminated, not a sentence."""
    text = line.strip()
    if not 0 < len(text) <= MAX_HEADING_CHARS:
        return False
    if re.search(r"\d", text) and not text.endswith(":"):      # '3+ years of Python' is a requirement, not a heading
        return False
    return text.endswith(":") or (len(text.split()) <= MAX_HEADING_WORDS and text[-1] not in ".!?")


def weighted_lines(jd: str, blurbs: bool = True) -> list[tuple[float, str]]:
    """Split a JD into (weight, line). Headings switch the section; an inline 'preferred'/'a plus' halves one line."""
    section, out = REQUIRED, []
    for line in (jd or "").splitlines():
        if not line.strip():
            continue
        if _is_heading(line):
            probe = line.replace("\u2019", "'")        # "What you\u2019ll" must match like "What you'll"
            if _SKIP.search(probe) or (blurbs and _BLURB.search(probe)):
                section = IGNORED_SECTION
                continue
            if _PREFERRED.search(probe):
                section = PREFERRED
            elif _REQUIRED.search(probe) and (section != PREFERRED or probe.strip().endswith(":")
                                              or _REQUIRED_STRONG.search(probe)):
                section = REQUIRED
            elif section == IGNORED_SECTION and _RESUME.search(probe):
                section = REQUIRED
        weight = PREFERRED if section == REQUIRED and _PREFERRED.search(line) else section
        out.append((weight, line))
    return out


def extract(jd: str) -> list[Term]:
    """Hard-skill terms of a JD with their best section weight, mention count and first position; terms only in
    skipped sections are dropped. When the company-blurb skips leave nothing, the JD is read again without them."""
    return _extract(jd, blurbs=True) or _extract(jd, blurbs=False)


def _extract(jd: str, blurbs: bool) -> list[Term]:
    best: dict[str, float] = {}
    count: dict[str, int] = {}
    first: dict[str, int] = {}
    forms: dict[str, list[str]] = {}
    offset = 0
    for weight, line in weighted_lines(jd, blurbs):
        if weight > 0:
            for start, end, name in lexicon.spans(line):
                best[name] = max(best.get(name, 0.0), weight)
                count[name] = count.get(name, 0) + 1
                first.setdefault(name, offset + start)
                _add_form(forms.setdefault(name, []), line[start:end])
        offset += len(line) + 1
    return [Term(name, w, w >= REQUIRED, count[name], first[name], tuple(forms[name]))
            for name, w in sorted(best.items())]


MAX_FORMS = 3


def _add_form(seen: list[str], surface: str) -> None:
    """Keep up to MAX_FORMS distinct wordings (case and spacing ignored), in order of appearance."""
    form = " ".join(surface.split())
    if form and len(seen) < MAX_FORMS and form.lower() not in {f.lower() for f in seen}:
        seen.append(form)


def resume_text(resume: dict) -> str:
    """The searchable text of a resume.json: headline, summary, one line per skill category, every bullet."""
    skills = [f"{s['category']}: {', '.join(s['items'])}" for s in resume.get("skills", [])]
    bullets = [b for job in resume.get("experience", []) for b in job.get("bullets", [])]
    return "\n".join([resume.get("headline", ""), resume.get("summary", ""), *skills, *bullets])


def resume_terms(resume: dict) -> frozenset[str]:
    return frozenset(lexicon.find(resume_text(resume)))


def counted(terms: list[Term], known: set[str]) -> list[Term]:
    """Terms that count toward coverage: skill-ignored languages only when confirmed in known_skills."""
    return [t for t in terms if t.name not in IGNORED_LANGUAGES or t.name in known]


def coverage(terms: list[Term], have: set[str], known: set[str] = frozenset()) -> Coverage:
    """Weighted keyword coverage of `have` (canonical names) over the JD terms."""
    usable = counted(terms, known)
    tw = sum(t.weight for t in usable)
    hw = sum(t.weight for t in usable if t.name in have)
    missing = tuple(t.name for t in sorted(usable, key=lambda t: (-t.weight, t.name)) if t.name not in have)
    return Coverage(hw, tw, hw / tw if tw else 0.0, missing)


def needed_languages(terms: list[Term], known: set[str]) -> list[str]:
    """Required skill-ignored languages the profile has not confirmed ('Needs Java')."""
    return sorted(t.name for t in terms if t.need and t.name in IGNORED_LANGUAGES and t.name not in known)
