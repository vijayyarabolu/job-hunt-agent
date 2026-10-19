"""Autofill learns from what the person fills or changes by hand on a form.

The extension sends, when the user leaves a step, each question the user answered by hand (or changed after Autofill filled it):
the question, the field kind, the choices the form showed, the user's answer, the board, company and job. Never a password,
a file, or anything in an SSN / ID-number / bank / birth-date field (dropped here too, whatever the extension sent).
- an ordinary question -> the user's answer for it everywhere (a built-in bank key, or the user's own question 'q:<question>',
  merged with a question the user already answered in other words);
- sponsorship, work authorization, any legal / government / certify question and EEO -> an override for THAT company
  only (autofill_overrides); the global answer stays;
- long free text (> 300 characters, "why us", cover letters) -> a snippet kept with the application, offered in the
  panel, never filled on its own;
- the address (per city), a start date typed as a date (the saved wait stays), how-did-you-hear and
  previously-worked-here (they depend on the job) are not learned.
Every learned change is an autofill_learned row holding the answer it replaced: Undo puts that back.
"""
import difflib
import json
import re

from .. import db, past_employers
from . import bank, keys

MAX_ITEMS = 80
LONG_TEXT = 300
MAX_HAND = 500
SIMILAR_RATIO = 0.88
SIMILAR_JACCARD = 0.8
TWIN_RATIO = 0.6                                       # "weekdays" / "weekends", "employed" / "unemployed"
NEGATIONS = frozenset({"not", "no", "never", "without", "none", "neither", "nor", "cannot", "unable"})
LEARNED_LIST = 200
SNIPPETS_LIST = 10
NO_LEARN_STEPS = ("auth", "verify", "experience", "review")      # account, email check, resume history, review
# never kept, whatever the field kind: secrets, ID numbers, bank details, birth dates
SECRET = re.compile(r"pass ?(word|code|phrase)|\bpin\b|\bssn\b|social security|\bsin\b|tax (id|identification)|\btin\b|"
                    r"\bitin\b|national (id|identity|insurance)|passport|driver ?s? licen[cs]e|licen[cs]e (number|no)|"
                    r"\bid (number|no)\b|identification number|\bbank\b|routing|account (number|no)|\biban\b|\bswift\b|"
                    r"credit card|card number|\bcvv\b|\bcvc\b|security code|date of birth|birth ?date|\bdob\b|"
                    r"mother s maiden|security question")
LONG_QUESTION = re.compile(r"\bwhy\b|cover letter|tell us|describe|motivat|interest(ed)? in (this|the|working|joining)|"
                           r"about yourself|additional information|anything else")
CERTIFY = re.compile(r"\bcertif|\battest|\bi (agree|acknowledge|consent|understand)\b|\blegal\b|\bgovernment\b")
# depend on the job or the city, not on the person: never learned as a global answer
JOB_KEYS = frozenset({"how_heard", "prev_worked", "current_company", "current_title", "related_employee", "referred"})
NO_LEARN = frozenset(bank.ADDRESS_KEYS) | keys.DERIVED | JOB_KEYS | {"address2", "county", "cover_letter"}
# asked about one thing ("years of experience with LangChain"): kept as that question, never as the general answer
OWN_QUESTION_KEYS = frozenset({"years_exp"})
SEARCH_BOX = re.compile(r"^(search|filter|find|keywords?|sort by)\b")
DATE_ONLY = re.compile(r"^\d{1,4}[/.-]\d{1,2}([/.-]\d{1,4})?$")
CHOICE_KINDS = ("select", "radio", "checkboxes", "checkbox")
_WORDS = re.compile(r"[a-z0-9]+")
_STOP = frozenset("a an the you your are is do does did to of in for at on with this that and or be have has will "
                  "would please company s what which".split())


def _now() -> str:
    return db.iso(db.utcnow())


def company_key(name: str | None) -> str:
    return past_employers.company_key(str(name or "")) or ""


def _words(qnorm: str) -> set[str]:
    return {w for w in _WORDS.findall(qnorm or "") if w not in _STOP}


def _opposite(mine: set[str], theirs: set[str]) -> bool:
    """Two wordings that differ by a negation or a near-twin word ask opposite things ("employed" / "unemployed",
    "willing" / "not willing", "weekdays" / "weekends"): never the same question."""
    if (mine & NEGATIONS) != (theirs & NEGATIONS):
        return True
    for a in mine - theirs:
        for b in theirs - mine:
            if a in b or b in a or difflib.SequenceMatcher(None, a, b).ratio() >= TWIN_RATIO:
                return True
    return False


def similar(qnorm: str, candidates) -> str | None:
    """The candidate question asking the same thing in other words ("Notice period?" / "What is your notice
    period"), else None. Numbers must agree ("5+ years of Python" is not "3+ years of Python") and opposites never
    match ("Are you currently unemployed?" is not "Are you currently employed?")."""
    if not qnorm:
        return None
    if qnorm in candidates:
        return qnorm
    mine, digits = _words(qnorm), set(re.findall(r"\d+", qnorm))
    best, score = None, 0.0
    for other in candidates:
        if set(re.findall(r"\d+", other)) != digits:
            continue
        theirs = _words(other)
        if _opposite(mine, theirs):
            continue
        jac = len(mine & theirs) / len(mine | theirs) if mine and theirs else 0.0
        ratio = difflib.SequenceMatcher(None, " ".join(sorted(mine)), " ".join(sorted(theirs))).ratio()
        hit = max(jac / SIMILAR_JACCARD, ratio / SIMILAR_RATIO)
        if (jac >= SIMILAR_JACCARD or ratio >= SIMILAR_RATIO) and hit > score:
            best, score = other, hit
    return best


def _clean(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    label = " ".join(str(raw.get("label") or "").split())[:bank.MAX_LABEL]
    value = str(raw.get("value") if raw.get("value") is not None else "").strip()
    if not label or not value or len(value) > bank.MAX_ANSWER:
        return None
    hints = [str(h)[:80] for h in (raw.get("hints") or [])[:10]] if isinstance(raw.get("hints"), list) else []
    options = [" ".join(str(o).split())[:300] for o in (raw.get("options") or [])[:80] if str(o).strip()] \
        if isinstance(raw.get("options"), list) else []
    return {"label": label, "value": value, "kind": str(raw.get("kind") or "text")[:20], "hints": hints,
            "options": options, "section": str(raw.get("section") or "")[:200],
            "how": raw.get("how") if raw.get("how") in ("hand", "override", "panel") else "hand"}


def _search_box(item: dict) -> bool:
    """A site's search or filter box (a board's job list, a careers page): never an answer about the user."""
    return item["kind"] == "search" or bool(SEARCH_BOX.match(keys.norm(item["label"])))


def _secret(item: dict) -> bool:
    return bool(SECRET.search(keys.norm(" ".join([item["label"], *item["hints"]]))))


def _key_for(item: dict, company: str | None) -> keys.Key | None:
    qnorm = keys.norm(item["label"], company)
    key = keys.classify(qnorm, item["hints"]) or keys.by_choices(item["options"])
    if key and key.key == "full_name" and bank.SIGN_HEADING.search(item["section"]):
        key = keys.BY_KEY["signature_name"]
    return key


def _company_only(key: keys.Key | None, qnorm: str) -> bool:
    if key is not None and (key.legal or key.category in ("legal", "eeo")):
        return True
    return bool(keys.LEGAL_WORDS.search(qnorm) or CERTIFY.search(qnorm))


def _snippet(item: dict, key: keys.Key | None, qnorm: str) -> bool:
    if len(item["value"]) > LONG_TEXT:
        return True
    return item["kind"] == "textarea" and (bool(key and key.key == "cover_letter") or bool(LONG_QUESTION.search(qnorm)))


def _same(a: str | None, b: str | None) -> bool:
    return keys.norm(a) == keys.norm(b)


def _log(conn, pid: str, scope: str, key: str, item: dict, prev, ctx: dict, ref: int | None = None) -> int:
    cur = conn.execute("INSERT INTO autofill_learned(profile, scope, key, question, answer, prev, how, board, company, "
                       "job_uid, app_id, ref, at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (pid, scope, key, item["label"], item["value"], prev, item["how"], ctx["board"], ctx["company"],
                        ctx["uid"], ctx["app"], ref, _now()))
    return cur.lastrowid


def _entry(lid: int, scope: str, label: str, answer: str, company: str | None) -> dict:
    return {"id": lid, "scope": scope, "label": label, "answer": answer, "company": company}


def _save_snippet(conn, pid: str, item: dict, qnorm: str, ctx: dict) -> dict:
    with db.tx(conn):
        old = conn.execute("SELECT id, text FROM autofill_snippets WHERE profile = ? AND qnorm = ? AND "
                           "coalesce(job_uid, '') = ? AND coalesce(app_id, 0) = ?",
                           (pid, qnorm, ctx["uid"] or "", ctx["app"] or 0)).fetchone()
        if old and old["text"] == item["value"]:
            return {}
        if old:
            conn.execute("UPDATE autofill_snippets SET text = ?, question = ?, at = ? WHERE id = ?",
                         (item["value"], item["label"], _now(), old["id"]))
            sid = old["id"]
        else:
            sid = conn.execute("INSERT INTO autofill_snippets(profile, job_uid, app_id, company, question, qnorm, text, "
                               "at) VALUES (?,?,?,?,?,?,?,?)", (pid, ctx["uid"], ctx["app"], ctx["company"],
                                                                item["label"], qnorm, item["value"], _now())).lastrowid
        lid = _log(conn, pid, "snippet", "", item, old["text"] if old else None, ctx, sid)
    return _entry(lid, "snippet", item["label"], item["value"], ctx["company"])


def _save_override(conn, pid: str, item: dict, target: str, ctx: dict, usual: str) -> dict:
    ckey = company_key(ctx["company"])
    with db.tx(conn):
        old = conn.execute("SELECT answer FROM autofill_overrides WHERE profile = ? AND company = ? AND key = ?",
                           (pid, ckey, target)).fetchone()
        if _same(old["answer"] if old else usual, item["value"]):
            return {}                                   # the answer Autofill gives this company anyway
        conn.execute("INSERT INTO autofill_overrides(profile, company, company_name, key, question, answer, updated_at) "
                     "VALUES (?,?,?,?,?,?,?) ON CONFLICT(profile, company, key) DO UPDATE SET answer = excluded.answer, "
                     "question = excluded.question, company_name = excluded.company_name, updated_at = excluded.updated_at",
                     (pid, ckey, ctx["company"], target, item["label"], item["value"], _now()))
        lid = _log(conn, pid, "company", target, item, old["answer"] if old else None, ctx)
    return _entry(lid, "company", item["label"], item["value"], ctx["company"])


def _choices(conn, pid: str, target: str, board: str | None, options: list[str]) -> None:
    if not options or not board:
        return
    with db.tx(conn):
        conn.execute("INSERT INTO autofill_choices(profile, key, board, options, at) VALUES (?,?,?,?,?) "
                     "ON CONFLICT(profile, key, board) DO UPDATE SET options = excluded.options, at = excluded.at",
                     (pid, target, board, json.dumps(options), _now()))


def _save_global(conn, pid: str, item: dict, key: keys.Key | None, qnorm: str, ctx: dict, saved: dict) -> dict:
    if key is not None and key.key in OWN_QUESTION_KEYS:
        key = None
    if key is not None:
        target = key.key
    else:
        own = bank.own_questions(saved)
        target = bank.CUSTOM + (similar(qnorm, own) or qnorm)
    choice = item["kind"] in CHOICE_KINDS and bool(item["options"])
    _choices(conn, pid, target, ctx["board"], item["options"])
    current = (saved.get(target) or {}).get("answer")
    exists = target in saved
    if current and (_same(current, item["value"]) or (choice and keys.norm(item["value"]).startswith(keys.norm(current)))):
        if choice and ctx["board"]:                     # the same answer in this board's words: kept word for word
            bank.save(conn, pid, target, current, board=ctx["board"], option=item["value"])
        return {}
    bank.save(conn, pid, target, item["value"], board=ctx["board"] if choice else None,
              option=item["value"] if choice else None, question=None if key else item["label"])
    with db.tx(conn):
        lid = _log(conn, pid, "global", target, item, current if exists else None, ctx)
        conn.execute("UPDATE autofill_questions SET state = 'answered' WHERE profile = ? AND state = 'open' AND "
                     "(qnorm = ? OR (key IS NOT NULL AND key = ?))", (pid, qnorm, key.key if key else None))
    label = key.label if key else item["label"]
    return _entry(lid, "global", label, item["value"], None)


def _usual(conn, pid: str, target: str, ctx: dict, saved: dict) -> str:
    """The answer Autofill gives this company anyway (Yes to future sponsorship at a Sponsors / Cap-exempt
    company while that setting is On), so changing it by hand is kept as this company's own answer."""
    if target == "sponsor_future":
        from . import sponsor
        if sponsor.says_yes(sponsor.badge_for(conn, pid, ctx["uid"], ctx["app"]), saved):
            return "Yes"
    return bank._answer(saved, target)


def learn_one(conn, pid: str, item: dict, ctx: dict, saved: dict) -> dict:
    """One hand-filled question -> what was learned ({} when nothing: same answer, a secret, not learnable)."""
    if _secret(item) or _search_box(item):
        return {}
    qnorm = keys.norm(item["label"], ctx["company"])
    key = _key_for(item, ctx["company"])
    if _snippet(item, key, qnorm):                     # cover letters too: kept with the application, offered
        return _save_snippet(conn, pid, item, qnorm, ctx)
    if key is not None and key.key in NO_LEARN:
        return {}
    if key is not None and key.key == "start_date" and DATE_ONLY.match(item["value"]):
        return {}                                       # a date for this job; the saved wait stays
    if _company_only(key, qnorm):
        if not ctx["company"]:
            return {}
        target = key.key if key else bank.CUSTOM + qnorm
        _choices(conn, pid, target, ctx["board"], item["options"])
        return _save_override(conn, pid, item, target, ctx, _usual(conn, pid, target, ctx, saved))
    return _save_global(conn, pid, item, key, qnorm, ctx, saved)


def _ctx(args: dict) -> dict:
    uid = str(args.get("uid") or "").strip() or None
    app = args.get("app")
    return {"uid": uid, "app": int(app) if str(app or "").strip().isdigit() else None,
            "board": str(args.get("board") or "")[:40] or None, "company": str(args.get("company") or "")[:200] or None,
            "step": str(args.get("step") or "")[:60]}


def _hand_count(value) -> int:
    try:
        return max(0, min(MAX_HAND, int(value or 0)))
    except (TypeError, ValueError):
        return 0


def learn(conn, pid: str, args: dict) -> dict:
    """POST /api/autofill/learn: one step's hand-filled answers (+ how many fields the user filled by hand there)."""
    items = args.get("items")
    if not isinstance(items, list):
        raise ValueError("items must be a list")
    ctx = _ctx(args)
    hand = _hand_count(args.get("hand"))
    if hand and (ctx["uid"] or ctx["app"]):
        with db.tx(conn):
            conn.execute("INSERT INTO autofill_hand(profile, job_uid, app_id, board, step, hand, at) VALUES (?,?,?,?,?,?,?)",
                         (pid, ctx["uid"], ctx["app"], ctx["board"], ctx["step"], hand, _now()))
    if ctx["step"].lower() in NO_LEARN_STEPS:
        return {"learned": []}
    out = []
    for raw in items[:MAX_ITEMS]:
        item = _clean(raw)
        if item is None:
            continue
        try:
            got = learn_one(conn, pid, item, ctx, bank.load(conn, pid))
        except ValueError:                              # an answer the bank refuses (too long, bad dates): skipped
            continue
        if got:
            out.append(got)
    return {"learned": out}


def _restore_global(conn, pid: str, row) -> None:
    current = conn.execute("SELECT answer FROM autofill_answers WHERE profile = ? AND key = ?",
                           (pid, row["key"])).fetchone()
    if current is not None and not _same(current["answer"], row["answer"]):
        raise ValueError("this answer was changed again since; edit it on the Answers page")
    if row["prev"] is None and row["key"].startswith(bank.CUSTOM):
        conn.execute("DELETE FROM autofill_answers WHERE profile = ? AND key = ?", (pid, row["key"]))
    else:
        conn.execute("UPDATE autofill_answers SET answer = ?, updated_at = ? WHERE profile = ? AND key = ?",
                     (row["prev"] or "", _now(), pid, row["key"]))


def _restore_company(conn, pid: str, row) -> None:
    ckey = company_key(row["company"])
    if row["prev"] is None:
        conn.execute("DELETE FROM autofill_overrides WHERE profile = ? AND company = ? AND key = ?", (pid, ckey, row["key"]))
    else:
        conn.execute("UPDATE autofill_overrides SET answer = ?, updated_at = ? WHERE profile = ? AND company = ? AND key = ?",
                     (row["prev"], _now(), pid, ckey, row["key"]))


def undo(conn, pid: str, lid: int) -> dict:
    """Undo one learned answer: the answer it replaced comes back (or it is removed when there was none)."""
    row = conn.execute("SELECT * FROM autofill_learned WHERE id = ? AND profile = ?", (int(lid), pid)).fetchone()
    if row is None:
        raise KeyError(lid)
    if row["undone_at"]:
        return {"id": row["id"], "undone": True}
    if row["scope"] == "global":
        bank.ensure(conn, pid)
    with db.tx(conn):
        if row["scope"] == "global":
            _restore_global(conn, pid, row)
        elif row["scope"] == "company":
            _restore_company(conn, pid, row)
        elif row["prev"] is None:
            conn.execute("DELETE FROM autofill_snippets WHERE id = ? AND profile = ?", (row["ref"], pid))
        else:
            conn.execute("UPDATE autofill_snippets SET text = ? WHERE id = ? AND profile = ?", (row["prev"], row["ref"], pid))
        conn.execute("UPDATE autofill_learned SET undone_at = ? WHERE id = ?", (_now(), row["id"]))
    return {"id": row["id"], "undone": True}


def overrides_for(conn, pid: str, company: str | None) -> dict[str, str]:
    """This company's own answers: {bank key or 'q:<question>': answer}."""
    ckey = company_key(company)
    if not ckey:
        return {}
    return {r["key"]: r["answer"] for r in conn.execute(
        "SELECT key, answer FROM autofill_overrides WHERE profile = ? AND company = ?", (pid, ckey))}


def learned_list(conn, pid: str) -> list[dict]:
    """The Answers page's Learned section: newest first, the latest change per answer."""
    rows = conn.execute("SELECT * FROM autofill_learned WHERE profile = ? AND undone_at IS NULL AND scope <> 'snippet' "
                        "ORDER BY at DESC, id DESC LIMIT ?", (pid, LEARNED_LIST)).fetchall()
    seen, out = set(), []
    for r in rows:
        mark = (r["scope"], r["key"], company_key(r["company"]) if r["scope"] == "company" else "")
        if mark in seen:
            continue
        seen.add(mark)
        known = keys.BY_KEY.get(r["key"])
        out.append({"id": r["id"], "scope": r["scope"], "key": r["key"], "question": r["question"],
                    "label": known.label if known else r["question"], "answer": r["answer"], "how": r["how"],
                    "board": r["board"], "company": r["company"], "at": r["at"]})
    return out


def overrides_list(conn, pid: str) -> list[dict]:
    """Company answers (sponsorship, work authorization, legal, EEO), by company."""
    out = []
    for r in conn.execute("SELECT * FROM autofill_overrides WHERE profile = ? ORDER BY company_name, key", (pid,)):
        known = keys.BY_KEY.get(r["key"])
        out.append({"company": r["company_name"], "company_key": r["company"], "key": r["key"],
                    "label": known.label if known else r["question"], "answer": r["answer"], "at": r["updated_at"]})
    return out


def set_override(conn, pid: str, company: str, key: str, answer) -> dict:
    """Edit (or, with an empty answer, remove) one company answer from the Answers page."""
    row = conn.execute("SELECT * FROM autofill_overrides WHERE profile = ? AND company = ? AND key = ?",
                       (pid, str(company or ""), str(key or ""))).fetchone()
    if row is None:
        raise KeyError(key)
    text = str(answer if answer is not None else "").strip()[:bank.MAX_ANSWER]
    with db.tx(conn):
        if text:
            conn.execute("UPDATE autofill_overrides SET answer = ?, updated_at = ? WHERE profile = ? AND company = ? "
                         "AND key = ?", (text, _now(), pid, row["company"], row["key"]))
        else:
            conn.execute("DELETE FROM autofill_overrides WHERE profile = ? AND company = ? AND key = ?",
                         (pid, row["company"], row["key"]))
    return {"company": row["company_name"], "key": row["key"], "answer": text, "removed": not text}


def snippets(conn, pid: str, question: str | None = None, limit: int = SNIPPETS_LIST) -> list[dict]:
    """Saved long answers, the ones asking the same question first (the panel's "Use a saved answer ▾")."""
    rows = conn.execute("SELECT * FROM autofill_snippets WHERE profile = ? ORDER BY at DESC, id DESC LIMIT 200",
                        (pid,)).fetchall()
    qnorm = keys.norm(question)
    near = similar(qnorm, {r["qnorm"] for r in rows}) if qnorm else None
    words = _words(qnorm)

    def rank(r) -> tuple:
        overlap = len(words & _words(r["qnorm"])) / (len(words) or 1)
        return (r["qnorm"] != near, -overlap)
    return [{"id": r["id"], "question": r["question"], "text": r["text"], "company": r["company"], "at": r["at"]}
            for r in sorted(rows, key=rank)[:limit]]


def remove_snippet(conn, pid: str, sid: int) -> dict:
    with db.tx(conn):
        gone = conn.execute("DELETE FROM autofill_snippets WHERE id = ? AND profile = ?", (int(sid), pid)).rowcount
    if not gone:
        raise KeyError(sid)
    return {"removed": int(sid)}
