"""choice questions on the Answers page get a dropdown instead of a free-text box, so a typo
("Hetrosexual") can't stop the answer from matching a form's choices. The dropdown holds the common answers plus the
real choices boards showed for that question (autofill_choices, learned in §1). A saved answer that is a near miss
of one choice is corrected to it once (and logged, so Undo on the Learned list puts the old text back)."""
import difflib
import json

from .. import db
from . import keys

DECLINE = "I don't wish to answer"
YES_NO = ("Yes", "No")
COMMON = {
    "gender": ("Male", "Female", "Non-binary", DECLINE),
    "orientation": ("Heterosexual / Straight", "Gay", "Lesbian", "Bisexual", "Asexual", "Queer", "Prefer to self-describe",
                    DECLINE),
    "transgender": ("Yes", "No", DECLINE),
    "hispanic": ("Yes", "No", DECLINE),
    "race": ("American Indian or Alaska Native", "Asian", "Black or African American", "Hispanic or Latino",
             "Native Hawaiian or Other Pacific Islander", "White", "Two or More Races", DECLINE),
    "veteran": ("I am not a protected veteran", "I identify as one or more of the classifications of protected veteran",
                DECLINE),
    "disability": ("No, I do not have a disability and have not had one in the past",
                   "Yes, I have a disability (or previously had a disability)", "I do not want to answer"),
    "pronouns": ("The user/the user", "The user/her", "They/them", DECLINE),
    "phone_type": ("Mobile", "Home", "Work"),
}
YES_NO_KEYS = ("work_auth", "sponsor_now", "sponsor_future", "age18", "relocate", "background_check", "drug_test",
               "criminal", "gov_official", "noncompete", "travel", "related_employee", "referred", "prev_worked",
               "sms_consent")
NEAR_MISS = 0.8
MAX_CHOICES = 40


def common(key: str) -> tuple[str, ...]:
    if key in COMMON:
        return COMMON[key]
    return YES_NO if key in YES_NO_KEYS else ()


def learned(conn, pid: str) -> dict[str, list[str]]:
    """{bank key: every choice a board showed for it} (newest boards first)."""
    out: dict[str, list[str]] = {}
    for r in conn.execute("SELECT key, options FROM autofill_choices WHERE profile = ? ORDER BY at DESC", (pid,)):
        try:
            found = json.loads(r["options"])
        except ValueError:
            continue
        out.setdefault(r["key"], []).extend(str(o) for o in found if str(o).strip())
    return out


def _dedupe(items) -> list[str]:
    seen, out = set(), []
    for item in items:
        mark = keys.norm(item)
        if mark and mark not in seen and not mark.startswith("select"):
            seen.add(mark)
            out.append(item)
    return out[:MAX_CHOICES]


def for_key(key: str, boards: dict[str, list[str]]) -> list[str]:
    """The dropdown for one answer: common answers first, then what boards showed; [] for a free-text answer."""
    base = common(key)
    seen = boards.get(key) or []
    return _dedupe([*base, *seen]) if base or seen else []


def nearest(answer: str, options: list[str]) -> str | None:
    """The choice a saved answer means: itself, else one it is a near miss of ("Hetrosexual" -> "Heterosexual /
    Straight"); None when it is far from all of them."""
    text = keys.norm(answer)
    if not text or not options:
        return None
    for option in options:
        if keys.norm(option) == text:
            return option
    best, score = None, 0.0
    for option in options:
        words = keys.norm(option).split()
        parts = [" ".join(words[:n]) for n in range(1, len(words) + 1)]   # "heterosexual" in "heterosexual straight"
        ratio = max(difflib.SequenceMatcher(None, text, p).ratio() for p in [keys.norm(option), *parts])
        if ratio > score:
            best, score = option, ratio
    return best if score >= NEAR_MISS else None


def _kept(conn, pid: str, key: str, answer: str) -> bool:
    """The user undid a fix of exactly these words: they stay the user's."""
    return conn.execute("SELECT 1 FROM autofill_learned WHERE profile = ? AND key = ? AND prev = ? AND how = 'panel' "
                        "AND undone_at IS NOT NULL LIMIT 1", (pid, key, answer)).fetchone() is not None


def fix_typos(conn, pid: str, bank_rows: dict) -> list[dict]:
    """Correct saved choice answers that are a near miss of one choice (logged as learned, so Undo restores them)."""
    boards = learned(conn, pid)
    fixed = []
    for key, entry in bank_rows.items():
        options = for_key(key, boards)
        answer = str(entry.get("answer") or "").strip()
        if not options or not answer or any(keys.norm(o) == keys.norm(answer) for o in options):
            continue
        match = nearest(answer, options)
        if not match or _kept(conn, pid, key, answer):
            continue
        now = db.iso(db.utcnow())
        with db.tx(conn):
            conn.execute("UPDATE autofill_answers SET answer = ?, updated_at = ? WHERE profile = ? AND key = ?",
                         (match, now, pid, key))
            conn.execute("INSERT INTO autofill_learned(profile, scope, key, question, answer, prev, how, at) "
                         "VALUES (?,?,?,?,?,?,?,?)", (pid, "global", key, entry.get("label") or key, match, answer,
                                                      "panel", now))
        fixed.append({"key": key, "label": entry.get("label") or key, "was": answer, "now": match})
    return fixed
