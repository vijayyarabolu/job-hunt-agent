"""Like / Not interested feedback and the bounded ranking multipliers it produces for one profile.

A 'pass' carries a reason: company (demotes that company's other jobs), location (demotes jobs whose every state was
passed on), wrong_role (demotes that title family), too_senior (demotes senior-titled jobs), other (a typed
reason in `note`, kept and counted, no ranking effect). A 'like' gives a small
boost to the same company and family. The newest feedback per job wins; 'clear' (restore / unlike) cancels it.
The table is created idempotently here (same pattern as generate.DDL), not by a db.py migration.
"""
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from . import applied, db, signals

KINDS = ("like", "pass", "clear")
REASONS = ("too_senior", "wrong_role", "location", "company", "other")
REASON_LABELS = {"too_senior": "Too senior", "wrong_role": "Wrong role", "location": "Location", "company": "Company",
                 "other": "Other"}
NOTE_MAX = 200                  # the "Other…" text box
COMPANY_PASS = 0.5              # one company pass halves that company's other jobs
LOCATION_PASS, ROLE_PASS, SENIOR_PASS = 0.8, 0.85, 0.85      # per pass, compounding down to the floor below
REASON_FLOOR = 0.6
LIKE_COMPANY = 1.10
LIKE_FAMILY_STEP, LIKE_FAMILY_MAX = 0.02, 1.06
FEEDBACK_FLOOR, FEEDBACK_CEIL = 0.4, 1.15   # the combined feedback multiplier always stays inside this band
DDL = """CREATE TABLE IF NOT EXISTS feedback (
  id INTEGER PRIMARY KEY, profile TEXT NOT NULL, uid TEXT NOT NULL, at TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('like', 'pass', 'clear')),
  reason TEXT CHECK (reason IS NULL OR reason IN ('too_senior', 'wrong_role', 'location', 'company', 'other')),
  company TEXT NOT NULL DEFAULT '', family TEXT NOT NULL DEFAULT '', states TEXT NOT NULL DEFAULT '', note TEXT);
CREATE INDEX IF NOT EXISTS feedback_profile_uid ON feedback(profile, uid, id);"""
_SENIOR_TITLE = re.compile(r"(?i)\b(senior|sr|lead|staff|principal|architect)\b")
_STATE = re.compile(r",\s*(" + "|".join(signals.US_STATES) + r")\b")


@dataclass(frozen=True)
class Prefs:
    liked_companies: frozenset[str] = frozenset()
    liked_families: dict = field(default_factory=dict)       # family -> likes
    passed_companies: frozenset[str] = frozenset()
    passed_states: dict = field(default_factory=dict)        # state -> passes
    passed_families: dict = field(default_factory=dict)      # family -> passes
    too_senior: int = 0


_COLUMNS = "id, profile, uid, at, kind, reason, company, family, states"


def ensure(conn) -> None:
    """Create the table; GOAL19 §2: a table from before 'other' (its CHECK names four reasons, no note column) is
    rebuilt once with every row kept (SQLite cannot change a CHECK in place)."""
    old = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'feedback'").fetchone()
    if old is not None and "'other'" not in old[0]:
        with db.tx(conn):
            conn.execute("ALTER TABLE feedback RENAME TO feedback_old")
            conn.execute("DROP INDEX IF EXISTS feedback_profile_uid")
            conn.execute(DDL.split(";")[0])
            conn.execute(f"INSERT INTO feedback({_COLUMNS}) SELECT {_COLUMNS} FROM feedback_old")
            conn.execute("DROP TABLE feedback_old")
    conn.executescript(DDL)


def clean_note(reason: str | None, note) -> str | None:
    """The typed reason: required for 'other' (1..NOTE_MAX characters, one line), refused for the four chips."""
    text = " ".join(str(note or "").split())
    if reason != "other":
        if text:
            raise ValueError("only the 'other' reason takes a typed note")
        return None
    if not text:
        raise ValueError("type a reason for Other")
    if len(text) > NOTE_MAX:
        raise ValueError(f"the reason is too long ({len(text)} characters, at most {NOTE_MAX})")
    return text


def states_of(location: str | None) -> frozenset[str]:
    return frozenset(_STATE.findall(location or ""))


def family_of(row_families: str | None, profile_families: tuple[str, ...]) -> str:
    """The job's first family in the profile's order ('' when none)."""
    tagged = set((row_families or "").strip(",").split(","))
    return next((f for f in profile_families if f in tagged), "")


def record(conn, pid: str, uid: str, kind: str, reason: str | None, now: datetime,
           profile_families: tuple[str, ...], note: str | None = None) -> dict:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    if kind == "pass" and reason not in REASONS:
        raise ValueError(f"reason must be one of {REASONS}")
    if kind != "pass" and reason:
        raise ValueError("only 'pass' takes a reason")
    note = clean_note(reason, note)
    job = conn.execute("SELECT company, location, families FROM jobs WHERE uid = ?", (uid,)).fetchone()
    if job is None:
        raise KeyError(uid)
    ensure(conn)
    entry = {"company": applied.company_key(job["company"]), "family": family_of(job["families"], profile_families),
             "states": ",".join(sorted(states_of(job["location"])))}
    with db.tx(conn):
        conn.execute("INSERT INTO feedback(profile, uid, at, kind, reason, company, family, states, note) "
                     "VALUES (?,?,?,?,?,?,?,?,?)", (pid, uid, db.iso(now), kind, reason, *entry.values(), note))
    return {"kind": kind, "reason": reason, "note": note, **entry}


def reason_text(entry: dict | None) -> str | None:
    """'Too senior' / 'Other: commute too long' for a pass, None otherwise (the Archived row, Stats)."""
    if not entry or entry.get("kind") != "pass" or not entry.get("reason"):
        return None
    label = REASON_LABELS.get(entry["reason"], entry["reason"])
    return f"{label}: {entry['note']}" if entry.get("note") else label


def reason_counts(conn, pid: str) -> dict:
    """GOAL19 §2 Stats: the jobs whose newest feedback is a pass, by reason (Other too), and the typed reasons."""
    passes = [f for f in latest(conn, pid).values() if f["kind"] == "pass"]
    counts = {k: sum(1 for f in passes if f["reason"] == k) for k in REASONS}
    typed = [f["note"].strip() for f in sorted(passes, key=lambda f: f["id"]) if f["reason"] == "other" and f["note"]]
    notes = Counter(t.lower() for t in typed)                  # "Not remote" and "not remote" count as one,
    shown = {t.lower(): t for t in typed}                      # shown as last typed
    return {"total": len(passes), "by_reason": [{"key": k, "label": REASON_LABELS[k], "n": counts[k]} for k in REASONS],
            "other_notes": [{"note": shown[n], "n": c} for n, c in notes.most_common(10)]}


def _bump(counts: dict, key: str) -> dict:
    return {**counts, key: counts.get(key, 0) + 1} if key else counts


def latest(conn, pid: str) -> dict[str, dict]:
    """uid -> the newest feedback row of this profile for that job."""
    ensure(conn)
    rows = conn.execute("SELECT * FROM feedback WHERE id IN (SELECT max(id) FROM feedback WHERE profile = ? "
                        "GROUP BY uid)", (pid,)).fetchall()
    return {r["uid"]: dict(r) for r in rows}


def load(conn, pid: str) -> Prefs:
    """Aggregate the newest feedback per job of this profile."""
    return prefs_of(list(latest(conn, pid).values()))


def prefs_of(rows: list[dict]) -> Prefs:
    liked_c, liked_f, passed_c, passed_s, passed_f, senior = set(), {}, set(), {}, {}, 0
    for r in rows:
        if r["kind"] == "like":
            liked_c.add(r["company"])
            liked_f = _bump(liked_f, r["family"])
        elif r["kind"] == "pass" and r["reason"] == "company":
            passed_c.add(r["company"])
        elif r["kind"] == "pass" and r["reason"] == "location":
            for state in filter(None, r["states"].split(",")):
                passed_s = _bump(passed_s, state)
        elif r["kind"] == "pass" and r["reason"] == "wrong_role":
            passed_f = _bump(passed_f, r["family"])
        elif r["kind"] == "pass" and r["reason"] == "too_senior":
            senior += 1
    return Prefs(frozenset(liked_c - {""}), liked_f, frozenset(passed_c - {""}), passed_s, passed_f, senior)


def _down(base: float, n: int) -> float:
    return max(REASON_FLOOR, base ** n) if n else 1.0


def factors(prefs: Prefs | None, row: dict, profile_families: tuple[str, ...]) -> dict[str, float]:
    """{chip label: multiplier} for one job (empty when no feedback applies). One combined entry, clamped to
    [FEEDBACK_FLOOR, FEEDBACK_CEIL], so feedback can never bury or inflate a job without bound."""
    if prefs is None:
        return {}
    company = applied.company_key(row.get("company"))
    family = family_of(row.get("families"), profile_families)
    states = states_of(row.get("location"))
    parts: list[tuple[str, float]] = []
    if company and company in prefs.passed_companies:
        parts.append(("you passed on this company before", COMPANY_PASS))
    if states and states <= set(prefs.passed_states):
        parts.append(("you passed on jobs in this place before", _down(LOCATION_PASS, max(prefs.passed_states[s] for s in states))))
    if family and prefs.passed_families.get(family):
        parts.append(("you passed on this role type before", _down(ROLE_PASS, prefs.passed_families[family])))
    if prefs.too_senior and _SENIOR_TITLE.search(row.get("title") or ""):
        parts.append(("you passed on senior titles before", _down(SENIOR_PASS, prefs.too_senior)))
    if company and company in prefs.liked_companies:
        parts.append(("you liked this company", LIKE_COMPANY))
    if family and prefs.liked_families.get(family):
        parts.append(("you liked this role type", min(LIKE_FAMILY_MAX, 1 + LIKE_FAMILY_STEP * prefs.liked_families[family])))
    if not parts:
        return {}
    product = 1.0
    for _, value in parts:
        product *= value
    value = max(FEEDBACK_FLOOR, min(FEEDBACK_CEIL, product))
    if value == 1.0:
        return {}
    # was "Your feedback: role type passed on"; now says which way it moved the job and why
    return {("Ranked higher: " if value > 1 else "Ranked lower: ") + "; ".join(name for name, _ in parts): round(value, 4)}
