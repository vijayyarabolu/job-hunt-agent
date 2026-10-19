"""ATS-aware "Copy JD + keywords" (GOAL4 §3 addition, decision 009): how the job's ATS handles a resume, built only
from vendor documentation cited in config/ats_profiles.yaml ("not publicly documented" where no vendor page says
it), the JD's keywords in the posting's exact wording plus the acronym + spelled-out form, and the application
questions when the ATS's public API exposes them (Greenhouse only; the others say why not)."""
import logging
import re
import threading
import time
from functools import lru_cache
from pathlib import Path

import yaml

from . import config, lexicon
from .net import HttpError, get_json

log = logging.getLogger(__name__)
SECTIONS = (("parsing", "Parsing / what breaks it"), ("file_types", "Files: PDF vs DOCX, size"),
            ("ranking", "Auto-rank / AI matching vs keyword search"), ("questions", "Knockout / screening questions"),
            ("keywords", "Keyword wording"), ("acronyms", "Acronym vs spelled-out"),
            ("placement", "Skills section vs bullets"))
UNDOCUMENTED = "not publicly documented"
EXPOSED = (True, False, "undocumented")
GREENHOUSE_QUESTIONS = "https://boards-api.greenhouse.io/v1/boards/{key}/jobs/{id}?questions=true"
STANDARD_FIELDS = {"first_name", "last_name", "email", "phone", "resume", "resume_text", "cover_letter",
                   "cover_letter_text", "latitude", "longitude"}         # the last two: hidden parts of Location
MAX_OPTIONS = 8
CACHE_S = 6 * 3600
CACHE_MAX = 200
ACRONYM = re.compile(r"^[a-z0-9]{2,6}$")
SMALL_WORDS = {"and", "of", "for", "the", "&"}
_cache: dict[str, tuple[float, list[str]]] = {}
_cache_lock = threading.Lock()


def _check_line(ats: str, section: str, item) -> None:
    if not isinstance(item, dict) or not str(item.get("text") or "").strip():
        raise config.ConfigError(f"ats_profiles.yaml {ats}.{section}: every line needs text")
    if not str(item.get("source") or "").startswith("https://"):
        raise config.ConfigError(f"ats_profiles.yaml {ats}.{section}: {item['text'][:40]!r} needs an https source")


@lru_cache(maxsize=2)
def profiles(path: str | None = None) -> dict:
    """The validated profiles (cached): see load()."""
    return load(path)


def facts(prof: dict) -> dict[str, dict]:
    """A vendor's facts by id (decision 010: a vendor model cites facts by id)."""
    return {str(item["id"]): item for section, _ in SECTIONS for item in prof.get(section) or [] if item.get("id")}


def load(path: str | None = None) -> dict:
    """Every fact has an https source and a unique id when it has one; questions_api.exposed is
    true/false/undocumented."""
    data = yaml.safe_load(Path(path or config.shipped("ats_profiles.yaml")).read_text()) or {}
    for ats, prof in data.items():
        ids = []
        for section, _ in SECTIONS:
            for item in prof.get(section) or []:
                _check_line(ats, section, item)
                ids += [str(item["id"])] if item.get("id") else []
        if len(ids) != len(set(ids)):
            raise config.ConfigError(f"ats_profiles.yaml {ats}: duplicate fact ids")
        api = prof.get("questions_api") or {}
        if api.get("exposed") not in EXPOSED or not str(api.get("source") or "").startswith("https://"):
            raise config.ConfigError(f"ats_profiles.yaml {ats}.questions_api: exposed must be true/false/undocumented "
                                     "with an https source")
    return data


PROFILE_OF = {"icims_classic": "icims"}       # adapters that share a vendor profile


def ats_of(board: str | None) -> str:
    ats = (board or "").split(":", 1)[0]
    return PROFILE_OF.get(ats, ats)


def box(ats: str) -> list[str]:
    """'How <ATS> handles your resume', each fact with a numbered source; undocumented sections said so."""
    prof = profiles().get(ats)
    if prof is None:
        return [f"How this ATS ({ats or 'unknown'}) handles your resume: {UNDOCUMENTED} (no vendor profile in "
                "config/ats_profiles.yaml)"]
    sources: list[str] = []
    lines = [f"How {prof['label']} handles your resume (vendor docs only; [n] = source below):"]
    for section, title in SECTIONS:
        items = prof.get(section) or []
        if not items:
            lines.append(f"- {title}: {UNDOCUMENTED}")
            continue
        lines.append(f"- {title}:")
        for item in items:
            if item["source"] not in sources:
                sources.append(item["source"])
            lines.append(f"    {item['text']} [{sources.index(item['source']) + 1}]")
    lines.append("Sources: " + " ".join(f"[{i}] {url}" for i, url in enumerate(sources, 1)))
    return lines


# ---------- keyword forms ----------

@lru_cache(maxsize=2)
def _terms(path: str | None = None) -> dict[str, list[str]]:
    data = yaml.safe_load(Path(path or config.shipped("lexicon.yaml")).read_text())
    return {name: [name, *(aliases or [])] for entries in data["categories"].values() for name, aliases in entries.items()}


def _initials(phrase: str) -> str:
    return "".join(w[0] for w in re.split(r"[\s-]+", phrase.lower()) if w and w not in SMALL_WORDS)


def both_forms(name: str) -> str | None:
    """'Natural Language Processing (NLP)' for a skill the lexicon knows by an acronym and its spelled-out words;
    None when it has no such pair."""
    if re.search(r"\([A-Za-z0-9&/+.-]{2,8}\)$", name):
        return name
    terms = _terms().get(name, [name])
    for short in terms:
        if not ACRONYM.match(short.lower()):
            continue
        for long in terms:
            if (" " in long.strip() or "-" in long.strip()) and _initials(long) == short.lower():
                spelled = long if long != long.lower() else long.title()
                return f"{spelled} ({name if name.lower() == short.lower() else short.upper()})"   # "dbt" stays dbt
    return None


def jd_wording(jd: str) -> dict[str, str]:
    """Canonical skill -> the posting's own words at its first mention (e.g. 'AWS SageMaker', 'NLP')."""
    found: dict[str, str] = {}
    for start, end, name in lexicon.spans(jd):
        found.setdefault(name, " ".join(jd[start:end].split()))
    return found


def keyword_lines(order: tuple[str, ...], have: set[str], resume_label: str, jd: str) -> list[str]:
    """Every JD keyword, most important first: in the resume or missing, the JD's exact wording, and both forms."""
    if not order:
        return []
    words = jd_wording(jd)
    lines = [f"Keywords by importance (✓ in your {resume_label} resume, ✗ missing). Use the JD's exact words; for an "
             "acronym write both forms (no vendor documents acronym matching):"]
    for n, name in enumerate(order, 1):
        said, pair = words.get(name), both_forms(name)
        notes = [f'JD says "{said}"' if said and said.lower() != name.lower() else None,
                 f'write "{pair}"' if pair and pair.lower() != (said or "").lower() else None]
        extra = "; ".join(x for x in notes if x)
        lines.append(f"  {n}. {'✓' if name in have else '✗'} {name}{f' ({extra})' if extra else ''}")
    return lines


# ---------- application questions ----------

def _question_line(q: dict) -> str | None:
    fields = q.get("fields") or []
    if fields and all(f.get("name") in STANDARD_FIELDS for f in fields):
        return None
    options = [v.get("label") for f in fields for v in (f.get("values") or []) if v.get("label")]
    shown = f" [{' / '.join(options[:MAX_OPTIONS])}{' / …' if len(options) > MAX_OPTIONS else ''}]" if options else ""
    return f"  - {(q.get('label') or '').strip()}{' (required)' if q.get('required') else ''}{shown}"


def _greenhouse(key: str, ext_id: str) -> list[str]:
    data = get_json(GREENHOUSE_QUESTIONS.format(key=key, id=ext_id)) or {}
    asked = (data.get("questions") or []) + (data.get("location_questions") or [])
    lines = [x for x in (_question_line(q) for q in asked) if x]
    eeo = len(data.get("compliance") or []) + len((data.get("demographic_questions") or {}).get("questions") or [])
    return (lines or ["  (only the standard name / contact / resume fields)"]) + (
        [f"  plus {eeo} voluntary EEO / demographic question group(s)"] if eeo else [])


def questions(ats: str, board_key: str | None, ext_id: str | None) -> list[str]:
    """The posting's application questions (Greenhouse, live, cached), or why they cannot be read."""
    prof = profiles().get(ats) or {}
    api = prof.get("questions_api") or {}
    if api.get("exposed") is not True:
        label = prof.get("label", ats or "this ATS")
        why = (f"{UNDOCUMENTED} in {label}'s public API (only an undocumented path has them, which job-hunt-agent does "
               "not use)"
               if api.get("exposed") == "undocumented" else f"not exposed by {label}'s public API")
        return [f"Application questions: {why}. {api.get('how', 'No vendor profile.')} [{api.get('source', '-')}]"]
    if not board_key or not ext_id:
        return ["Application questions: this job has no board / posting id to look them up"]
    cache_key = f"{ats}:{board_key}:{ext_id}"
    with _cache_lock:
        hit = _cache.get(cache_key)
    if hit and time.monotonic() - hit[0] < CACHE_S:
        return hit[1]
    try:
        lines = ["Application questions (from the posting, live):", *_greenhouse(board_key, ext_id)]
    except (HttpError, OSError, ValueError) as err:
        log.warning("greenhouse questions %s: %s", cache_key, err)
        return [f"Application questions: could not be read just now ({err})"]
    with _cache_lock:
        if len(_cache) >= CACHE_MAX:
            _cache.pop(next(iter(_cache)))
        _cache[cache_key] = (time.monotonic(), lines)
    return lines
