"""GOAL36 §1: match a job by the person's MASTER RESUME, not by its title (user 2026-10-23: "we need to get them based
on the resume, not by title").

- prefilter(): at ingest, a posting whose title is not denied (titles.denied) keeps its JD when the JD names at least
  PREFILTER_MIN skills from any person's master resume. Its families then carry the MARK ("resume"), so every person's
  fit rows and feed see it (fits.family_filter).
- pct(): the person's resume-only keyword match % of a scored JD (the Match ring's Skills part: the best of the
  person's resumes, never known_skills.txt). score.final keeps a job when its title is one of the person's families,
  or when this is MIN_PCT or more.
The fit scorer is the existing local one (no Claude call per posting)."""
import time

from . import config, keywords, lexicon, resumes

MARK = "resume"
MIN_PCT = 40                 # the user's bar (GOAL36 §1)
PREFILTER_MIN = 3            # distinct master skills a JD must name to be kept and scored at all
_KEEP_S = 300
_TERMS: dict[str, tuple[float, frozenset[str]]] = {}


def master_terms() -> frozenset[str]:
    """The union of every person's resume skills (canonical lexicon names), re-read at most every 5 minutes."""
    key = str(config.PROFILES)
    kept = _TERMS.get(key)
    if kept and time.monotonic() - kept[0] < _KEEP_S:
        return kept[1]
    names: set[str] = set()
    for profile in config.profiles():
        for resume in resumes.of_profile(profile):
            names |= resume.terms
    _TERMS[key] = (time.monotonic(), frozenset(names))
    return _TERMS[key][1]


def forget() -> None:
    _TERMS.clear()


def prefilter(text: str | None, terms: frozenset[str] | None = None) -> bool:
    """The JD names PREFILTER_MIN or more of the masters' skills (word-bounded lexicon matches)."""
    if not text:
        return False
    wanted = master_terms() if terms is None else terms
    return len(wanted.intersection(lexicon.find(text))) >= PREFILTER_MIN


def pct(fit_terms: str | None, n_resumes: int) -> int | None:
    """The best resume's keyword match % of this JD (keywords.score_one, as the Skills part shows it); None when the
    JD lists no skill we track."""
    pairs = keywords.unpack(fit_terms)
    if not pairs:
        return None
    best = max((keywords.score_one(pairs, bit, str(bit), "") for bit in range(max(1, n_resumes))), key=lambda s: s.pct)
    return best.pct if best.n_total else None
