"""GOAL23 §4 "Needs you": questions the extension could not answer, grouped (one entry per question, however many
forms asked it) and answered once on the Answers page. An answer goes into the bank: to its built-in key when the
question is one (a ZIP code, sponsorship), else as the person's own question; with the board's exact choice text
when the form offered choices, so that dropdown gets it word for word next time."""
import json
import re

from .. import db
from . import bank, keys

KINDS = ("text", "textarea", "select", "radio", "checkbox", "checkboxes", "date", "number", "file")
MAX_ITEMS = 60
MAX_OPTIONS = 80
MAX_OPTION = 300


def _merge(old: str, new: str | None, cap: int = 20) -> str:
    items = json.loads(old or "[]")
    if new and new not in items:
        items = (items + [new])[-cap:]
    return json.dumps(items)


def _clean(item: dict) -> dict | None:
    text = " ".join(str(item.get("label") or "").split())[:bank.MAX_LABEL]
    if not text:
        return None
    kind = str(item.get("kind") or "text")
    options = [" ".join(str(o).split())[:MAX_OPTION] for o in (item.get("options") or [])[:MAX_OPTIONS]
               if str(o).strip()]
    key = item.get("key") if item.get("key") in keys.BY_KEY else None
    if key is None:                                     # the page did not say: the label decides (legal flag too)
        hints = item.get("hints") if isinstance(item.get("hints"), list) else []
        known = keys.classify(text, [str(h)[:MAX_OPTION] for h in hints[:10]]) or keys.by_choices(options)
        key = known.key if known else None
    return {"text": text, "kind": kind if kind in KINDS else "text", "options": options, "key": key}


# never a question (user 2026-10-14): the address comes from the job's address, a cover letter stays empty
NEVER_ASKED = frozenset(bank.ADDRESS_KEYS) | {"county", "cover_letter"}


_GLUED = re.compile(r"(?<=[a-z])(?=[A-Z])")             # "CountryUnited States", "StateFL": a label glued to its pick


def _never(key: str | None, text: str) -> bool:
    if key in NEVER_ASKED:
        return True
    for words in dict.fromkeys([text, _GLUED.split(str(text or ""), maxsplit=1)[0]]):
        known = keys.classify(words)
        if known and known.key in NEVER_ASKED:
            return True
    return False


EEO_KEYS = frozenset({"gender", "race", "hispanic", "veteran", "disability", "transgender", "orientation"})
_LANGUAGE = re.compile(r"^(preferred )?language$")


def _foreign(key: str | None, text: str, options) -> bool:
    """GOAL31: an EEO question carrying another question's choices (1.5.0 read a Workday list while the last one was
    still on screen: "What is your gender?" with races), or Self Identify's Language dropdown taken for one. Its
    chips would save the wrong answer; asked again by 1.6.1, it comes with its own choices."""
    if key not in EEO_KEYS:
        return False
    if _LANGUAGE.match(keys.norm(text)):
        return True
    other = keys.by_choices(options)
    return bool(other and other.key != key and other.key in EEO_KEYS)


def tidy(conn, pid: str) -> int:
    """Open questions the extension should never have asked (an address field, a cover letter): answered from the
    job's address / left empty, so they leave Needs you; GOAL31: and EEO questions shown with another question's
    choices. Returns how many were closed."""
    rows = conn.execute("SELECT id, key, text, options FROM autofill_questions WHERE profile = ? AND state = 'open'", (pid,)).fetchall()
    done = [r["id"] for r in rows if _never(r["key"], r["text"]) or _foreign(r["key"], r["text"], json.loads(r["options"] or "[]"))]
    if done:
        with db.tx(conn):
            conn.executemany("UPDATE autofill_questions SET state = 'answered' WHERE id = ?", [(i,) for i in done])
    return len(done)


def record(conn, pid: str, items, board: str | None, company: str | None) -> int:
    """Keep the page's unanswered questions; one asked again after it was answered is open again (its answer did
    not fit this form). Returns how many are open now for the person."""
    if not isinstance(items, list):
        raise ValueError("needs must be a list")
    now = db.iso(db.utcnow())
    with db.tx(conn):
        for raw in items[:MAX_ITEMS]:
            item = _clean(raw) if isinstance(raw, dict) else None
            if item is None or _never(item["key"], item["text"]) or _foreign(item["key"], item["text"], item["options"]):
                continue
            qnorm = keys.norm(item["text"], company)
            found = conn.execute("SELECT * FROM autofill_questions WHERE profile = ? AND qnorm = ?",
                                 (pid, qnorm)).fetchone()
            if found is None:
                conn.execute("INSERT INTO autofill_questions(profile, qnorm, key, text, kind, options, boards, "
                             "companies, first_at, last_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                             (pid, qnorm, item["key"], item["text"], item["kind"], json.dumps(item["options"]),
                              json.dumps([board] if board else []), json.dumps([company] if company else []),
                              now, now))
                continue
            state = "dismissed" if found["state"] == "dismissed" else "open"
            conn.execute("UPDATE autofill_questions SET text = ?, kind = ?, options = ?, key = coalesce(?, key), "
                         "boards = ?, companies = ?, seen = seen + 1, last_at = ?, state = ? WHERE id = ?",
                         (item["text"], item["kind"], json.dumps(item["options"] or json.loads(found["options"])),
                          item["key"], _merge(found["boards"], board), _merge(found["companies"], company), now,
                          state, found["id"]))
    return count(conn, pid)


def count(conn, pid: str) -> int:
    return conn.execute("SELECT count(*) FROM autofill_questions WHERE profile = ? AND state = 'open'",
                        (pid,)).fetchone()[0]


def open_list(conn, pid: str) -> list[dict]:
    """Newest first, with the choices each form offered."""
    out = []
    for r in conn.execute("SELECT * FROM autofill_questions WHERE profile = ? AND state = 'open' "
                          "ORDER BY last_at DESC, id DESC", (pid,)):
        known = keys.BY_KEY.get(r["key"] or "")
        out.append({"id": r["id"], "text": r["text"], "kind": r["kind"], "options": json.loads(r["options"]),
                    "boards": json.loads(r["boards"]), "companies": json.loads(r["companies"]), "seen": r["seen"],
                    "last_at": r["last_at"], "key": r["key"], "key_label": known.label if known else None,
                    "legal": bool(known and known.legal)})
    return out


def _own(conn, pid: str, qid: int):
    row = conn.execute("SELECT * FROM autofill_questions WHERE id = ? AND profile = ?", (int(qid), pid)).fetchone()
    if row is None:
        raise KeyError(qid)
    return row


def answer(conn, pid: str, qid: int, value) -> dict:
    """Answer once: kept in the bank (built-in key or the person's own question) and the question is done."""
    row = _own(conn, pid, qid)
    text = str(value if value is not None else "").strip()
    if not text:
        raise ValueError("type or pick an answer")
    options = json.loads(row["options"])
    exact = next((o for o in options if o.strip().lower() == text.lower()), None)
    key = row["key"] if row["key"] in keys.BY_KEY and row["key"] not in keys.DERIVED else None
    target = key or bank.CUSTOM + row["qnorm"]
    saved = None
    for board in json.loads(row["boards"]) or [None]:     # the exact choice text for every board that asked it
        saved = bank.save(conn, pid, target, exact or text, board=board, option=exact,
                          question=None if key else row["text"])
    with db.tx(conn):
        conn.execute("UPDATE autofill_questions SET state = 'answered' WHERE id = ?", (row["id"],))
        if key:                                # the same key asked in other words: answered too
            conn.execute("UPDATE autofill_questions SET state = 'answered' WHERE profile = ? AND key = ? "
                         "AND state = 'open'", (pid, key))
    return {"id": row["id"], "saved": saved, "open": count(conn, pid)}


def dismiss(conn, pid: str, qid: int) -> dict:
    """Not a question to keep (a one-off): it stops counting and is not asked again."""
    row = _own(conn, pid, qid)
    with db.tx(conn):
        conn.execute("UPDATE autofill_questions SET state = 'dismissed' WHERE id = ?", (row["id"],))
    return {"id": row["id"], "open": count(conn, pid)}
