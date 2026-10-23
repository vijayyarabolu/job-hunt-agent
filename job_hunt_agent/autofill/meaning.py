"""GOAL30 §1: a form question in words no rule knows yet ("Location (City, State, Country)", "Current employment
status") still finds the field it means, or the saved answer the user gave to the same question asked another way.

1. Words first, free: synonyms and filler words folded away ("Are you currently employed?" and "Current employment
   status" are both {now, employ}); learn.similar uses concepts() for the user's saved questions.
2. Claude only when that fails: ONE short Haiku call for all of a page's unknown questions, on the person's own
   Claude login, medium effort: "which known field is this?" with the label, the page's choices, the list of known
   fields and the user's saved questions. Claude only maps a question; it never writes an answer. Every result (none too)
   is cached by person and normalized wording forever, so each new wording costs one call once.
3. Answers the user chose to keep for one job only (the panel's "for this job only"), and the user's Location choice for a job.

The two tables are created here on first use (job_hunt_agent/autofill owns them; schema.sql is not touched).
"""
import json
import logging
import re
import time

from .. import db
from . import keys

log = logging.getLogger(__name__)
HAIKU = "claude-haiku-5-5"
MAX_ASK = 15                         # unknown questions per call
MAX_SAVED = 60                       # the user's saved questions offered per call
MAX_LABEL = 300
NONE = "none"
REST_S = 600                         # after a failed call (usage limit, no login): no call for 10 minutes
SAVED = "saved:"
JOB_LOCATION = "__location__"        # §3: the user's Location choice for one job (resume | home)

_DDL = (
    "CREATE TABLE IF NOT EXISTS autofill_meaning (profile TEXT NOT NULL, qnorm TEXT NOT NULL, target TEXT NOT NULL, "
    "label TEXT, at TEXT NOT NULL, PRIMARY KEY (profile, qnorm))",
    "CREATE TABLE IF NOT EXISTS autofill_job_answers (profile TEXT NOT NULL, job_key TEXT NOT NULL, "
    "target TEXT NOT NULL, question TEXT, answer TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY (profile, job_key, target))",
)

# ---- 1. words ------------------------------------------------------------------------------------------------
SYNONYMS = {
    "employed": "employ", "employment": "employ", "employ": "employ", "working": "employ",
    "currently": "now", "current": "now", "presently": "now", "present": "now",
    "relocate": "relocate", "relocation": "relocate", "relocating": "relocate",
    "sponsorship": "sponsor", "sponsored": "sponsor",
    "authorised": "authorized", "authorization": "authorized", "eligible": "authorized",
    "starting": "start", "begin": "start", "join": "start",
    "soonest": "earliest", "availability": "available",
    "salary": "pay", "compensation": "pay", "wage": "pay", "earn": "pay",
    "remotely": "remote", "yrs": "year", "years": "year",
    "mobile": "phone", "cell": "phone", "telephone": "phone", "healthcare": "health",
}
FILLER = frozenset("a an the you your are is do does did to of in for at on with this that and or be have has will "
                   "would please company s what which status kindly indicate whether below following answer question "
                   "tell us me our we if any".split())
_WORDS = re.compile(r"[a-z0-9]+")


def concepts(qnorm: str) -> frozenset[str]:
    """The ideas a question asks about, wording aside: {'now', 'employ'} for both "Are you currently employed?" and
    "Current employment status"."""
    return frozenset(SYNONYMS.get(w, w) for w in _WORDS.findall(qnorm or "") if w not in FILLER)


# ---- tables ---------------------------------------------------------------------------------------------------
def ensure(conn) -> None:
    """The two tables, made once per database (asked each time: a closed connection's id is reused by the next one)."""
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name IN "
                                       "('autofill_meaning', 'autofill_job_answers')")}
    if len(have) == len(_DDL):
        return
    with db.tx(conn):
        for ddl in _DDL:
            conn.execute(ddl)


def job_key(uid: str | None, app_id: int | None) -> str:
    return uid or (f"app:{app_id}" if app_id else "")


def job_answers(conn, pid: str, key: str) -> dict[str, str]:
    """{bank key or 'q:<question>' or JOB_LOCATION: answer} the user kept for this job only."""
    if not key:
        return {}
    ensure(conn)
    return {r["target"]: r["answer"] for r in conn.execute(
        "SELECT target, answer FROM autofill_job_answers WHERE profile = ? AND job_key = ?", (pid, key))}


def keep_for_job(conn, pid: str, key: str, target: str, answer: str, question: str | None = None) -> None:
    if not key:
        raise ValueError("this page is not matched to a job yet: pick the job first")
    ensure(conn)
    with db.tx(conn):
        conn.execute("INSERT INTO autofill_job_answers(profile, job_key, target, question, answer, at) VALUES "
                     "(?,?,?,?,?,?) ON CONFLICT(profile, job_key, target) DO UPDATE SET answer = excluded.answer, "
                     "question = excluded.question, at = excluded.at",
                     (pid, key, target, question, answer, db.iso(db.utcnow())))


# ---- 2. Claude ------------------------------------------------------------------------------------------------
PROMPT = """Each item below is one question from a job application form. Say which KNOWN FIELD it asks for, or which
of the applicant's SAVED QUESTIONS asks the same thing in other words. Only map the question: never answer it.

Rules:
- "field" is a key from KNOWN FIELDS, or "saved:<n>" for SAVED QUESTION n, or "none".
- Only when the meaning is the same. A question about someone else, a follow-up ("If yes, ..."), or a different
  fact (years with one tool, another company's policy, a different time) is "none".
- When unsure, "none".

Return a JSON array only: [{{"id": "<id>", "field": "<key | saved:n | none>"}}], one entry per item, same ids.

KNOWN FIELDS:
{fields}

SAVED QUESTIONS:
{saved}

ITEMS:
{items}
"""


def known_fields() -> list[tuple[str, str]]:
    return [(k.key, k.label) for k in keys.CATALOGUE
            if k.key not in keys.SETTINGS and k.key not in keys.DERIVED and k.key != "cover_letter"]


def cached(conn, pid: str, qnorm: str) -> str | None:
    ensure(conn)
    row = conn.execute("SELECT target FROM autofill_meaning WHERE profile = ? AND qnorm = ?", (pid, qnorm)).fetchone()
    return row["target"] if row else None


def _remember(conn, pid: str, qnorm: str, target: str, label: str) -> None:
    with db.tx(conn):
        conn.execute("INSERT INTO autofill_meaning(profile, qnorm, target, label, at) VALUES (?,?,?,?,?) "
                     "ON CONFLICT(profile, qnorm) DO UPDATE SET target = excluded.target, at = excluded.at",
                     (pid, qnorm, target, label[:MAX_LABEL], db.iso(db.utcnow())))


def _valid(target, saved: list[str]) -> str:
    """A key, 'q:<saved question>' or 'none' (anything else Claude says is 'none')."""
    target = str(target or "").strip()
    if target.startswith(SAVED):
        try:
            return "q:" + saved[int(target[len(SAVED):])]
        except (ValueError, IndexError):
            return NONE
    entry = keys.BY_KEY.get(target)
    return target if entry and entry.key not in keys.SETTINGS and entry.key not in keys.DERIVED else NONE


_resting: dict[str, float] = {}      # profile -> when a call may run again (time.monotonic)


def ask(conn, pid: str, items: list[dict], saved: list[str], runner=None) -> dict[str, str]:
    """{item id: bank key | 'q:<saved question>'} for the items Claude could map, cached results first. items:
    [{id, label, qnorm, options}]. A failed call (usage limit, no login) maps nothing and caches nothing, and no
    call is made for REST_S after it, so each Autofill does not wait on another call that will fail."""
    out, todo = {}, []
    for item in items:
        hit = cached(conn, pid, item["qnorm"])
        if hit is None:
            todo.append(item)
        elif hit != NONE:
            out[item["id"]] = hit
    todo = todo[:MAX_ASK]
    if not todo or time.monotonic() < _resting.get(pid, 0):
        return out
    saved = saved[:MAX_SAVED]
    listed = [{"id": str(i["id"]), "question": " ".join(str(i["label"]).split())[:MAX_LABEL],
               **({"choices": [str(o)[:80] for o in i.get("options") or []][:12]} if i.get("options") else {})}
              for i in todo]
    prompt = PROMPT.format(fields="\n".join(f"{k}: {label}" for k, label in known_fields()),
                           saved="\n".join(f"{n}: {q}" for n, q in enumerate(saved)) or "(none)",
                           items=json.dumps(listed, indent=1))
    from .assist import _run
    try:
        text = _run(prompt, pid, HAIKU, runner)
    except ValueError as err:
        log.warning("autofill meaning for %s: %s (no call for %d s)", pid, err, REST_S)
        _resting[pid] = time.monotonic() + REST_S
        return out
    found = re.search(r"\[.*\]", text or "", re.S)
    try:
        got = json.loads(found.group(0)) if found else []
    except ValueError:
        got = []
    by_id = {str(g.get("id")): _valid(g.get("field"), saved) for g in got if isinstance(g, dict)}
    for item in todo:
        target = by_id.get(str(item["id"]))
        if target is None:                              # Claude skipped it: not cached, asked again next time
            continue
        _remember(conn, pid, item["qnorm"], target, item["label"])
        if target != NONE:
            out[item["id"]] = target
    return out


def label_of(target: str) -> str:
    """What the panel says Claude read a question as."""
    if target.startswith("q:"):
        return f"your saved question “{target[2:]}”"
    entry = keys.BY_KEY.get(target)
    return f"“{entry.label}”" if entry else target
