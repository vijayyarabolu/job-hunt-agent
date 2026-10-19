"""GOAL8 §4 Settings: each person sets their own years cap (`max_required_years` in their profile.yaml), since
resumegen GOAL3 §8 their minimum match for Recommended (`feed_min_match`, default 0 = every matched job) and, since
GOAL12, after how many days with no reply an application goes Cold (`cold_after_days`, default 30) and, since GOAL14,
when their job-application mailbox is read each day (`mail_daily_at`, "HH:MM", default "00:15"). The one line
is rewritten in place so the file's comments and order stay; the feed re-ranks at once (feedcache keys on the cap).
a daily applications goal (`daily_goal`, 0 hides it), whether Apply ↗ also hands over the ready resume and
the job's facts (`apply_in_hand`, on by default), and the answers most application forms ask (answers.json next to
profile.yaml, personal data, never in git; empty ones fall back to what profile.yaml already says)."""
import json
import logging
import os
import re
import tempfile

from . import config, past_employers

log = logging.getLogger(__name__)

CAP_KEY = "max_required_years"
MAX_CAP = 30
FLOOR_KEY = "feed_min_match"
DEFAULT_FLOOR = 0               # resumegen GOAL3 §8 (user, 2026-10-07): show jobs under 60% too
MAX_FLOOR = 100
COLD_KEY = "cold_after_days"    # an application with no reply and no status change this long goes Cold
DEFAULT_COLD = 30
MAX_COLD = 365
MAIL_AT_KEY = "mail_daily_at"   # the nightly email read, local time (uses the day's leftover Claude limit)
DEFAULT_MAIL_AT = "00:15"
_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
GOAL_KEY = "daily_goal"         # "Applied today 7 · goal 15"
DEFAULT_GOAL = 15
MAX_GOAL = 100
IN_HAND_KEY = "apply_in_hand"   # GOAL13 §2b
ANSWER_LABELS = {"work_auth": "Work authorization", "start": "Earliest start", "salary": "My salary ask",   # GOAL13 §2g (was "Salary")
                 "relocate": "Relocate", "linkedin": "LinkedIn", "github": "GitHub", "years": "Years of experience"}
ANSWER_KEYS = tuple(ANSWER_LABELS)
MAX_ANSWER = 300
_YEARS_LINE = re.compile(r"^years\s*:.*$", re.M)


def get(pid: str) -> dict:
    profile = config.profile(pid)
    return {"years": profile.get("years"), CAP_KEY: profile.get(CAP_KEY),
            FLOOR_KEY: int(profile.get(FLOOR_KEY, DEFAULT_FLOOR) or 0),
            COLD_KEY: int(profile.get(COLD_KEY) or DEFAULT_COLD),
            MAIL_AT_KEY: mail_at(profile),
            past_employers.KEY: list(past_employers.names(profile)),            # GOAL16 §0
            "past_employers_default": past_employers.from_resume(pid)}


def mail_at(profile: dict) -> str:
    """The person's daily email time; an unreadable value falls back to the default (a hand edit never breaks it)."""
    value = str(profile.get(MAIL_AT_KEY) or DEFAULT_MAIL_AT).strip()
    return value if _HHMM.match(value) else DEFAULT_MAIL_AT


def apply_get(pid: str) -> dict:
    """the daily goal, the Apply-in-hand toggle and the answers (GET /api/settings adds them)."""
    profile = config.profile(pid)
    return {GOAL_KEY: int(profile.get(GOAL_KEY, DEFAULT_GOAL) or 0), IN_HAND_KEY: bool(profile.get(IN_HAND_KEY, 1)),
            "answers": answers(pid, profile), "answer_labels": list(ANSWER_LABELS.items())}


def _answers_path(pid: str):
    return config.PROFILES / pid / "answers.json"


def answers(pid: str, profile: dict | None = None) -> dict:
    """The person's saved answers; one never saved falls back to profile.yaml (LinkedIn, GitHub, years, sponsorship).
    A saved empty answer stays empty (cleared in Settings)."""
    profile = profile or config.profile(pid)
    saved = _saved_answers(pid)
    years = profile.get("years")
    sponsor = profile.get("needs_sponsorship")
    known = {"linkedin": profile.get("linkedin"), "github": profile.get("github"),
             "years": f"{years} years" if years is not None else None,
             "work_auth": None if sponsor is None else f"Will require visa sponsorship: {'Yes' if sponsor else 'No'}"}
    return {k: str(saved[k] if k in saved else known.get(k) or "") for k in ANSWER_KEYS}


def _saved_answers(pid: str) -> dict:
    """answers.json, or {} when missing, unreadable or not an object (a hand edit must not break the feed)."""
    try:
        saved = json.loads(_answers_path(pid).read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(saved, dict):
        log.warning("%s: answers.json is not an object; ignored", pid)
        return {}
    return saved


def _clean_answers(given) -> dict:
    if not isinstance(given, dict) or set(given) - set(ANSWER_KEYS):
        raise ValueError(f"answers: only {', '.join(ANSWER_KEYS)}")
    out = {}
    for key, value in given.items():
        if not isinstance(value, str) or len(value) > MAX_ANSWER:
            raise ValueError(f"{key}: text up to {MAX_ANSWER} characters")
        out[key] = " ".join(value.split())
    return out


def set_apply(pid: str, args: dict) -> dict:
    """GOAL13 Settings: any of {'goal': 0-100, 'in_hand': true/false, 'answers': {key: text}}; all checked before
    anything is written."""
    goal = _whole(args["goal"], MAX_GOAL, "the daily goal") if "goal" in args else None
    if "in_hand" in args and not isinstance(args["in_hand"], bool):
        raise ValueError("in_hand must be true or false")
    given = _clean_answers(args["answers"]) if "answers" in args else None
    if goal is not None:
        _save(pid, GOAL_KEY, goal)
    if "in_hand" in args:
        _save(pid, IN_HAND_KEY, int(args["in_hand"]))
    if given is not None:
        _write(_answers_path(pid), json.dumps({**_saved_answers(pid), **given}, indent=1, ensure_ascii=False) + "\n", 0o600)
    return apply_get(pid)


def _whole(value, top: int, what: str) -> int:
    rule = f"{what} must be a whole number from 0 to {top}"
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError(rule)
    text = str(value).strip()
    if not text.isdigit() or int(text) > top:
        raise ValueError(rule)
    return int(text)


def _valid_cap(value) -> int:
    return _whole(value, MAX_CAP, "the years cap")


def _with_line(text: str, key: str, value: int | str) -> str:
    """`key: value` replaced in place, or added after `years:` (else at the end)."""
    line = f"{key}: {value}"
    found = re.compile(rf"^{re.escape(key)}\s*:.*$", re.M)
    if found.search(text):
        return found.sub(lambda _: line, text, count=1)        # a JSON list may hold backslashes
    years = _YEARS_LINE.search(text)
    if years:
        return f"{text[:years.end()]}\n{line}{text[years.end():]}"
    return f"{text.rstrip(chr(10))}\n{line}\n"


def set_years_cap(pid: str, value) -> dict:
    """Save the person's cap; ValueError for anything but a whole number 0-30. The file is replaced atomically with
    its mode kept."""
    return _save(pid, CAP_KEY, _valid_cap(value))


def set_min_match(pid: str, value) -> dict:
    """Save the person's minimum match % for Recommended (0 = every matched job); ValueError outside 0-100."""
    return _save(pid, FLOOR_KEY, _whole(value, MAX_FLOOR, "the minimum match"))


def set_cold_days(pid: str, value) -> dict:
    """Save after how many days with no reply an application goes Cold (1-365)."""
    days = _whole(value, MAX_COLD, "the Cold days")
    if days < 1:
        raise ValueError(f"the Cold days must be a whole number from 1 to {MAX_COLD}")
    return _save(pid, COLD_KEY, days)


def set_mail_at(pid: str, value) -> dict:
    """Save the time of day (HH:MM, 24 h) the mailbox is read. Quoted: YAML reads a bare 00:15 as a number."""
    text = str(value or "").strip()
    if not isinstance(value, str) or not _HHMM.match(text):
        raise ValueError("the email time must be HH:MM (24 h), e.g. 00:15")
    return _save(pid, MAIL_AT_KEY, f'"{text}"')


def set_past_employers(pid: str, value) -> dict:
    """save the person's past employers (jobs there are never shown). Written as a JSON flow list, which
    YAML reads back as the same list; an empty list hides nobody."""
    return _save(pid, past_employers.KEY, json.dumps(past_employers.validate(value), ensure_ascii=False))


def _save(pid: str, key: str, value: int | str) -> dict:
    path = config.PROFILES / pid / "profile.yaml"
    if not path.exists():
        raise ValueError(f"no profile {pid!r}")
    _write(path, _with_line(path.read_text(), key, value), path.stat().st_mode & 0o777)
    return get(pid)


def _write(path, text: str, mode: int) -> None:
    """Replace the file atomically with `mode`."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}.", suffix=path.suffix)
    try:
        with os.fdopen(fd, "w") as out:
            out.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
