"""the answer bank, one per person (autofill_answers).

Seeded once from what the person already gave (profile.yaml contact fields, answers.json, the base resume's work
history and education); every value they have not given stays empty, so a form asking it gets "Needs you". A
question answered on the Answers page is kept with that question (key 'q:<question>') and fills it everywhere from
then on. resolve() turns a page's fields into answers: the person's own saved questions first, then built-in keys
by attribute or label (keys.classify). Legal / government / EEO keys come only from a saved answer.
"""
import json
import re
from datetime import date

from .. import config, db, past_employers, profile_settings, resume_place
from . import keys

MAX_FIELDS = 150                     # one page's fields per call
MAX_LABEL = 500
MAX_ANSWER = 4000
CUSTOM = "q:"
SIGN_SECTIONS = ("disclosures", "self_identify")   # a bare "Name" there is the signature
# the field's own heading on any board: a bare "Name" / "Date" under it is a signature and its date
SIGN_HEADING = re.compile(r"self[- ]?identif|disabilit|veteran|signature|e-?sign|attestation|"
                          r"voluntary (disclosure|self)|equal (employment )?opportunity", re.I)
HISTORY_KEYS = ("history", "education_list")
ADDRESS_KEYS = ("address1", "address2", "city", "state", "zip", "location", "country")   # = address.ADDRESS_KEYS
CHOICE_KINDS = ("select", "radio", "checkboxes")
REPEATER_GROUPS = ("work", "edu")
US_COUNTRY = "United States of America"
STATE_NAMES = {code: name.title() for name, code in resume_place.STATE_CODES.items()}
_DATES = re.compile(r"([A-Za-z]{3,9})\.?\s+(\d{4})")
_MONTHS = {m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}
_SUFFIX = re.compile(r",\s*([^,]+?)\.?$")
_YES_NO = re.compile(r":\s*(yes|no)\b", re.I)
_MONTH_YEAR = re.compile(r"(0[1-9]|1[0-2])/\d{4}")
HISTORY_FIELDS = ("company", "title", "location", "start", "end", "current", "school", "degree", "field")
SPONSOR_AUTO = "sponsor_auto"         # GOAL24 §6 setting: On / Off (autofill/sponsor.py reads it)
START_WEEKS = "start_weeks"           # GOAL30 §1.5 setting: start date = this many weeks from today
START_DEFAULT = "2"
MAX_WEEKS = 26
_A_WAIT = re.compile(r"\b\d+\s*(day|week|month)s?\b|\b(one|two|three|four|a|an)\s+(day|week|month)s?\b|immediate|asap|"
                     r"as soon as possible|right away|^\d{1,2}/(\d{1,2}/)?\d{4}$|^\d{4}-\d{2}(-\d{2})?$", re.I)
ON_OFF = ("On", "Off")
_YES = re.compile(r"^\s*yes\b", re.I)


def _now() -> str:
    return db.iso(db.utcnow())


def _month_year(text: str | None) -> str:
    """'Jun 2026' -> '06/2026'; '' for anything else (Present)."""
    found = _DATES.search(text or "")
    month = _MONTHS.get(found.group(1)[:3].lower()) if found else None
    return f"{month:02d}/{found.group(2)}" if month else ""


def _span(dates: str | None) -> tuple[str, str, bool]:
    parts = re.split(r"\s+[–—-]\s+", str(dates or ""), maxsplit=1)
    end = parts[1] if len(parts) > 1 else ""
    return _month_year(parts[0]), _month_year(end), bool(re.search(r"(?i)present|current|now", end))


def _company(raw: str | None) -> tuple[str, str]:
    """'Globex Properties, USA.' -> ('Globex Properties', 'USA')."""
    text = str(raw or "").strip().rstrip(".")
    found = _SUFFIX.search(text)
    return (text[:found.start()].strip(), found.group(1).strip()) if found else (text, "")


def _degree(text: str | None) -> tuple[str, str]:
    """'Master of Science in Computer Science' -> ("Master's Degree", 'Computer Science')."""
    raw = str(text or "").strip()
    field = raw.split(" in ", 1)[1].strip() if " in " in raw else ""
    low = raw.lower()
    if re.search(r"ph\.?d|doctor", low):
        return "Doctorate", field
    if re.search(r"master|\bm\.?s\b", low):
        return "Master's Degree", field
    if re.search(r"bachelor|\bb\.?s\b|b\.?tech|\bb\.?e\b", low):
        return "Bachelor's Degree", field
    return raw, field


def history_from_resume(pid: str) -> tuple[list[dict], list[dict]]:
    """The base resume's work history and education as Workday's My Experience wants them."""
    try:
        base = json.loads((config.PROFILES / pid / "baseline.json").read_text())
    except (OSError, ValueError):
        return [], []
    jobs = []
    for item in base.get("experience") or []:
        company, where = _company(item.get("company"))
        start, end, current = _span(item.get("dates"))
        jobs.append({"company": company, "title": str(item.get("title") or "").strip(), "location": where,
                     "start": start, "end": "" if current else end, "current": current})
    schools = []
    for item in base.get("education") or []:
        degree, field = _degree(item.get("degree"))
        start, end, _ = _span(item.get("dates"))
        schools.append({"school": str(item.get("school") or "").split(",")[0].strip(), "degree": degree,
                        "field": field, "start": start, "end": end})
    return jobs, schools


def _sponsorship(profile: dict, saved: dict) -> str:
    """answers.json's sponsorship answer when profile.yaml agrees with it; '' when they disagree (ask, never guess)."""
    found = _YES_NO.search(str(saved.get("work_auth") or ""))
    if not found:
        return ""
    said = found.group(1).title()
    flag = profile.get("needs_sponsorship")
    if isinstance(flag, bool) and flag != (said == "Yes"):
        return ""
    return said


def seed_values(pid: str) -> dict[str, str]:
    """What the person already gave, as bank answers (contact, links, years, sponsorship, education, history)."""
    profile = config.profile(pid)
    saved = profile_settings.answers(pid, profile)
    name = str(profile.get("name") or "").split()
    city, _, code = str(profile.get("location") or "").partition(", ")
    jobs, schools = history_from_resume(pid)
    school = schools[0] if schools else {}
    years = re.search(r"\d+", str(saved.get("years") or profile.get("years") or ""))
    sponsor = _sponsorship(profile, saved)
    return {"full_name": " ".join(name), "first_name": name[0] if name else "",
            "last_name": name[-1] if len(name) > 1 else "", "email": str(profile.get("email") or ""),
            "phone": str(profile.get("phone") or ""), "phone_type": "Mobile",
            "phone_code": f"{US_COUNTRY} (+1)" if profile.get("phone") else "",
            "city": city.strip(), "state": STATE_NAMES.get(code.strip().upper(), code.strip()),
            "country": US_COUNTRY if code else "", "location": str(profile.get("location") or ""),
            "linkedin": str(saved.get("linkedin") or profile.get("linkedin") or ""),
            "github": str(saved.get("github") or profile.get("github") or ""),
            "salary": str(saved.get("salary") or ""), "start_date": str(saved.get("start") or ""),
            "relocate": str(saved.get("relocate") or ""), "years_exp": years.group(0) if years else "",
            "sponsor_now": sponsor, "sponsor_future": sponsor, SPONSOR_AUTO: "On", START_WEEKS: START_DEFAULT,
            "degree": school.get("degree", ""), "field_of_study": school.get("field", ""),
            "school": school.get("school", ""), "grad_date": school.get("end", ""),
            "history": json.dumps(jobs), "education_list": json.dumps(schools)}


def ensure(conn, pid: str) -> None:
    """Seed the person's bank once (a key they later cleared stays cleared). A bank from before GOAL24 gets the
    sponsorship setting row once (On, as the goal says)."""
    have = {r["key"] for r in conn.execute("SELECT key FROM autofill_answers WHERE profile = ? AND key IN (?, ?, ?)",
                                           (pid, "sponsor_now", SPONSOR_AUTO, START_WEEKS))}
    if "sponsor_now" in have and START_WEEKS not in have:  # GOAL30 §1.5: a bank from before gets the setting (2 weeks)
        entry = keys.BY_KEY[START_WEEKS]
        with db.tx(conn):
            conn.execute("INSERT OR IGNORE INTO autofill_answers(profile, key, label, category, answer, updated_at) "
                         "VALUES (?,?,?,?,?,?)", (pid, START_WEEKS, entry.label, entry.category, START_DEFAULT, _now()))
        have.add(START_WEEKS)
    if have == {"sponsor_now", START_WEEKS}:
        entry = keys.BY_KEY[SPONSOR_AUTO]
        with db.tx(conn):
            conn.execute("INSERT OR IGNORE INTO autofill_answers(profile, key, label, category, answer, updated_at) "
                         "VALUES (?,?,?,?,?,?)", (pid, SPONSOR_AUTO, entry.label, entry.category, "On", _now()))
        return
    if have or conn.execute("SELECT 1 FROM autofill_answers WHERE profile = ? LIMIT 1", (pid,)).fetchone():
        return
    values, now = seed_values(pid), _now()
    rows = [(pid, k.key, k.label, k.category, values.get(k.key, ""), now) for k in keys.CATALOGUE
            if k.key not in keys.DERIVED]
    rows += [(pid, "history", "Work history", "history", values["history"], now),
             (pid, "education_list", "Education history", "history", values["education_list"], now)]
    with db.tx(conn):
        conn.executemany("INSERT OR IGNORE INTO autofill_answers(profile, key, label, category, answer, updated_at) "
                         "VALUES (?,?,?,?,?,?)", rows)


def load(conn, pid: str) -> dict[str, dict]:
    ensure(conn, pid)
    return {r["key"]: {**dict(r), "options": json.loads(r["options"] or "{}")} for r in conn.execute(
        "SELECT key, label, category, question, answer, options FROM autofill_answers WHERE profile = ?", (pid,))}


def _answer(bank: dict, key: str) -> str:
    return str((bank.get(key) or {}).get("answer") or "").strip()


def _list(bank: dict, key: str) -> list:
    try:
        found = json.loads(_answer(bank, key) or "[]")
    except ValueError:
        return []
    return found if isinstance(found, list) else []


def history(conn, pid: str) -> dict:
    """Workday's My Experience data: {'jobs': [...], 'schools': [...], 'links': {...}}."""
    bank = load(conn, pid)
    return {"jobs": _list(bank, "history"), "schools": _list(bank, "education_list"),
            "links": {k: _answer(bank, k) for k in ("linkedin", "github", "portfolio")}}


def _derived(key: str, bank: dict, ctx: dict) -> str:
    """Answers worked out from other answers or the job (never for a legal key)."""
    if key == "email_confirm":
        return _answer(bank, "email")
    if key == "signature_date":                     # the date beside the user's signature: only once the user has one saved
        return date.today().strftime("%m/%d/%Y") if _answer(bank, "signature_name") else ""
    if key == "full_name":
        return " ".join(x for x in (_answer(bank, "first_name"), _answer(bank, "last_name")) if x)
    if key == "location":
        city, state = _answer(bank, "city"), _answer(bank, "state")
        return f"{city}, {state}" if city and state else ""
    if key == "prev_worked" and ctx.get("company"):
        return "Yes" if past_employers.matches(ctx["company"], ctx.get("past") or frozenset()) else "No"
    if key == "how_heard":
        found_on_board = re.search(r"linkedin\.|jobright\.|indeed\.|glassdoor\.|ziprecruiter\.|dice\.com",
                                   str(ctx.get("source_url") or ""))     # by where the user found the job
        return "Job Board" if found_on_board else "Company Website"
    if key in ("current_company", "current_title"):         # GOAL27: the resume this job uploads, else the user's list
        jobs = (ctx["resume_jobs"]() if callable(ctx.get("resume_jobs")) else None) or _list(bank, "history")
        return str(jobs[0].get("company" if key == "current_company" else "title") or "") if jobs else ""
    return ""


def _out(field: dict, key: keys.Key | None, value: str, option: str, source: str, status: str) -> dict:
    return {"id": field.get("id"), "key": key.key if key else None, "label": key.label if key else None,
            "value": value, "option": option, "legal": bool(key and key.legal), "source": source, "status": status,
            "personal": bool(key and key.no_guess)}


def weeks(bank: dict) -> int:
    """GOAL30 §1.5: the Answers page's "start date = N weeks from today" (2 when not set)."""
    try:
        return max(0, min(MAX_WEEKS, int(_answer(bank, START_WEEKS) or START_DEFAULT)))
    except ValueError:
        return int(START_DEFAULT)


def _start(key: keys.Key, bank: dict) -> tuple[str, str]:
    """(value, source) for a start date / notice question: the user's saved words when they say a wait or a date ("2 weeks
    after offer acceptance"), else the user's N weeks setting ("2 weeks"). A date box turns either into a date (extension)."""
    stored = _answer(bank, key.key)
    if stored and _A_WAIT.search(stored):
        return stored, "bank"
    if key.key == "notice" and _answer(bank, "start_date") and _A_WAIT.search(_answer(bank, "start_date")):
        return _answer(bank, "start_date"), "bank"
    return f"{weeks(bank)} weeks", "setting"


def _signing(field: dict, ctx: dict) -> bool:
    return ctx.get("section") in SIGN_SECTIONS or bool(SIGN_HEADING.search(str(field.get("section") or "")[:200]))


def _own_answer(own: dict, qnorm: str, fuzzy: bool = True) -> dict | None:
    """The user's answer to this question, asked in these words or (only when no built-in answer covers it) in
    other words the user answered before."""
    from .learn import similar
    found = own.get(qnorm)
    if found is None and fuzzy:
        near = similar(qnorm, own)
        found = own.get(near) if near else None
    return found if found and found["answer"] else None


def _one(field: dict, bank: dict, own: dict, ctx: dict) -> dict:
    label = str(field.get("label") or "")[:MAX_LABEL]
    required = bool(field.get("required"))
    board = str(ctx.get("board") or "")
    qnorm = keys.norm(label, ctx.get("company"))
    company = ctx.get("overrides") or {}                # this company's own answers (legal / EEO)
    job = ctx.get("job_answers") or {}                  # what the user kept for this job only
    if job.get(CUSTOM + qnorm):
        return _out(field, None, job[CUSTOM + qnorm], "", "job", "fill")
    if company.get(CUSTOM + qnorm):
        return _out(field, None, company[CUSTOM + qnorm], "", "company", "fill")
    if field.get("group") in REPEATER_GROUPS:           # GOAL30 §0: a field of one job / school entry
        return _out(field, None, "", "", "", "skip")    # comes from the work history, never the user's address
    hinted = keys.BY_KEY.get(str(field.get("key") or ""))   # GOAL27: the extension's classifier, when it is sure
    key = ((hinted if hinted and keys.hint_ok(hinted, label) else None)
           or keys.classify(qnorm, field.get("hints") or []) or keys.by_choices(field.get("options")))
    if key and key.key == "full_name" and _signing(field, ctx):
        key = keys.BY_KEY["signature_name"]
    if key and job.get(key.key) and key.key not in ADDRESS_KEYS:
        return _out(field, key, job[key.key], "", "job", "fill")
    if key and company.get(key.key):
        option = ((bank.get(key.key) or {}).get("options") or {}).get(board, "")
        option = option if keys.norm(option).startswith(keys.norm(company[key.key])) else ""
        return _out(field, key, company[key.key], option, "company", "fill")
    saved = _own_answer(own, qnorm, fuzzy=key is None)
    if saved:
        out = _out(field, None, saved["answer"], saved["options"].get(board, ""), "saved", "fill")
        origin = ctx["origin"](saved.get("key")) if callable(ctx.get("origin")) else None
        return {**out, "origin": origin} if origin else out
    if key is None:
        long_q = ctx.get("long_question")               # saved long answers are offered, never filled
        offer = bool(long_q) and field.get("kind") == "textarea" and bool(long_q.search(qnorm))
        asks = required or field.get("kind") in CHOICE_KINDS or offer
        return _out(field, None, "", "", "", "needs" if asks else "skip")
    if key.key in ("account_password", "cover_letter"):  # a cover letter stays empty and is never asked (2026-10-14)
        return _out(field, key, "", "", "", "skip")
    place = ctx.get("address")                          # the address of the resume's city
    if place is not None and key.key in ADDRESS_KEYS:   # never a question (user 2026-10-14): filled or left empty
        value = str(place.get(key.key) or "")
        out = _out(field, key, value, "", "address", "fill" if value else "skip")
        if key.key in ("city", "location") and place.get("city"):   # a city typeahead: the one in this state, US
            out["place"] = {"city": place["city"], "state": place.get("state") or "",
                            "code": str(place.get("location") or "").rpartition(", ")[2]}
        return out
    if key.key == "sponsor_future" and ctx.get("sponsor_yes"):   # a Sponsors / Cap-exempt company
        saved_yes = bool(_YES.match(_answer(bank, key.key)))
        option = ((bank.get(key.key) or {}).get("options") or {}).get(board, "") if saved_yes else ""
        return _out(field, key, "Yes", option, "sponsor", "fill")
    if key.key in ("start_date", "notice"):              # GOAL30 §1.5: one fact, the user's N weeks
        value, source = _start(key, bank)
        return {**_out(field, key, value, "", source, "fill"), "weeks": weeks(bank)}
    stored = _answer(bank, key.key)
    value = stored or ("" if key.legal else _derived(key.key, bank, ctx))
    if value:
        option = ((bank.get(key.key) or {}).get("options") or {}).get(board, "")
        return _out(field, key, value, option, "bank" if stored else "derived", "fill")
    return _out(field, key, "", "", "", "skip" if key.blank_ok and not required else "needs")


def own_questions(bank: dict) -> dict:
    """question (normalized) -> the person's saved answer to it."""
    return {key[len(CUSTOM):]: {"answer": entry["answer"], "options": entry["options"], "key": key}
            for key, entry in bank.items() if key.startswith(CUSTOM)}


def resolve(conn, pid: str, fields, ctx: dict) -> list[dict]:
    """One answer per field: status fill (value, and the board's exact option text when saved), needs (ask the
    person), skip (may stay empty) or hold (an address field while the person has not confirmed the address for the
    resume's city: ctx['address'] = {'hold': True}; a confirmed one is ctx['address'] = {bank key: value})."""
    if not isinstance(fields, list):
        raise ValueError("fields must be a list")
    bank = load(conn, pid)
    from .learn import LONG_QUESTION, overrides_for
    snippets = conn.execute("SELECT 1 FROM autofill_snippets WHERE profile = ? LIMIT 1", (pid,)).fetchone()
    ctx = {**ctx, "past": past_employers.profile_keys({**config.profile(pid), "id": pid}),
           "overrides": overrides_for(conn, pid, ctx.get("company")), "long_question": LONG_QUESTION if snippets else None}
    own = own_questions(bank)
    from . import meaning, origin
    ctx = {**ctx, "job_answers": meaning.job_answers(conn, pid, ctx.get("job_key") or ""),
           "origin": lambda key: origin.of(conn, pid, key, ctx.get("job_key") or "")}
    fields = [f for f in fields[:MAX_FIELDS] if isinstance(f, dict)]
    out = [_one(f, bank, own, ctx) for f in fields]
    if ctx.get("ask_claude"):                           # GOAL30 §1: wordings no rule knows, one short call
        out = _by_meaning(conn, pid, fields, out, bank, own, ctx)
    return out


ASK_MAX_LABEL = 300


def _askable(field: dict, got: dict, ctx: dict) -> bool:
    """A question worth one Haiku mapping: unanswered (needs the user, or optional and left empty), in the user's own words not
    known, not a long free-text question, not a follow-up, not inside a job / school entry."""
    label = str(field.get("label") or "")
    if got["status"] not in ("needs", "skip") or got["key"] or not label or len(label) > ASK_MAX_LABEL:
        return False
    if field.get("group") or field.get("kind") in ("file", "checkbox"):
        return False
    qnorm = keys.norm(label, ctx.get("company"))
    long_q = ctx.get("long_question_all")
    if keys.FOLLOW_UP.search(qnorm) or (field.get("kind") == "textarea" and long_q and long_q.search(qnorm)):
        return False
    return True


def _by_meaning(conn, pid: str, fields: list, out: list, bank: dict, own: dict, ctx: dict) -> list:
    from . import meaning
    items = [{"id": f.get("id"), "label": f.get("label"), "qnorm": keys.norm(f.get("label"), ctx.get("company")),
              "options": f.get("options") or []} for f, got in zip(fields, out) if _askable(f, got, ctx)]
    if not items:
        return out
    mapped = meaning.ask(conn, pid, items, [q for q in own if own[q]["answer"]], runner=ctx.get("runner"))
    if not mapped:
        return out
    by_id = {str(f.get("id")): f for f in fields}
    fixed = []
    for got in out:
        target = mapped.get(got["id"]) if got["id"] is not None else None
        field = by_id.get(str(got["id"]))
        if not target or field is None:
            fixed.append(got)
            continue
        why = f"Claude read this question as {meaning.label_of(target)}: check it"
        if target.startswith(CUSTOM):
            saved = own.get(target[len(CUSTOM):])
            again = (_out(field, None, saved["answer"], saved["options"].get(str(ctx.get("board") or ""), ""), "saved",
                          "fill") if saved and saved["answer"] else got)
        else:
            again = _one({**field, "key": target, "mapped": True}, bank, own, ctx)
        fixed.append({**again, "review": again["status"] == "fill", "why": why, "mapped": target})
    return fixed


def _clean_history(value: str) -> str:
    try:
        items = json.loads(value or "[]")
    except ValueError:
        raise ValueError("work history must be a JSON list") from None
    if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
        raise ValueError("work history must be a list of entries")
    for item in items:
        for part in ("start", "end"):
            if item.get(part) and not _MONTH_YEAR.fullmatch(str(item[part])):
                raise ValueError(f"dates are MM/YYYY (got {item[part]!r})")
    return json.dumps([{k: (bool(i[k]) if k == "current" else str(i[k]).strip()[:200]) for k in HISTORY_FIELDS
                        if k in i} for i in items])


def save(conn, pid: str, key: str, answer, board: str | None = None, option: str | None = None,
         question: str | None = None) -> dict:
    """Set one answer (a built-in key, the work history, or a question the person answered). `option` is the
    exact choice text `board` uses for it (kept so that board's dropdown gets it word for word)."""
    ensure(conn, pid)
    key = str(key or "").strip()
    text = str(answer if answer is not None else "").strip()
    if len(text) > MAX_ANSWER:
        raise ValueError(f"answers are at most {MAX_ANSWER} characters")
    if key in HISTORY_KEYS:
        text = _clean_history(text)
        if key == "education_list":
            _sync_education(conn, pid, json.loads(text))
    elif key == "address1":                             # §7: street only, never an apartment / unit number
        from .address import strip_unit, usps
        text = usps(strip_unit(text))
    elif key == "address2":
        text = ""
    elif key == SPONSOR_AUTO:
        text = next((v for v in ON_OFF if v.lower() == text.lower()), None) or ""
        if not text:
            raise ValueError("this setting is On or Off")
    elif key == START_WEEKS:                            # GOAL30 §1.5: "2" or "2 weeks", 0 to 26
        found = re.fullmatch(r"(\d{1,2})( ?weeks?)?", text.lower())
        if not found or int(found.group(1)) > MAX_WEEKS:
            raise ValueError(f"a number of weeks from 0 to {MAX_WEEKS}")
        text = str(int(found.group(1)))
    elif not key.startswith(CUSTOM) and (key not in keys.BY_KEY or key in keys.DERIVED):
        raise ValueError(f"unknown answer {key!r}")
    if key.startswith(CUSTOM) and len(key) <= len(CUSTOM):
        raise ValueError("the question is empty")
    row = conn.execute("SELECT options FROM autofill_answers WHERE profile = ? AND key = ?", (pid, key)).fetchone()
    options = json.loads(row["options"] or "{}") if row else {}
    if board and option:
        options = {**options, str(board)[:40]: str(option)[:300]}
    entry = keys.BY_KEY.get(key)
    label = entry.label if entry else str(question or key[len(CUSTOM):])[:MAX_LABEL]
    category = entry.category if entry else ("history" if key in HISTORY_KEYS else "other")
    with db.tx(conn):
        conn.execute("INSERT INTO autofill_answers(profile, key, label, category, question, answer, options, "
                     "updated_at) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(profile, key) DO UPDATE SET "
                     "answer = excluded.answer, options = excluded.options, updated_at = excluded.updated_at, "
                     "question = coalesce(excluded.question, autofill_answers.question)",
                     (pid, key, label, category, question, text, json.dumps(options), _now()))
    return {"key": key, "answer": text, "options": options}


EDUCATION_KEYS = {"school": "school", "degree": "degree", "field_of_study": "field", "grad_date": "end"}


def _sync_education(conn, pid: str, schools: list[dict]) -> None:
    """The single education answers (Highest degree, School, Field, Graduation date) follow the first (highest)
    degree of the list, so a form asking only one of them gets the same school the list has."""
    first = schools[0] if schools else {}
    now = _now()
    with db.tx(conn):
        for key, part in EDUCATION_KEYS.items():
            conn.execute("UPDATE autofill_answers SET answer = ?, updated_at = ? WHERE profile = ? AND key = ?",
                         (str(first.get(part) or ""), now, pid, key))


def forget(conn, pid: str, key: str) -> dict:
    """Remove one question the person answered (built-in keys are cleared with save(..., ''))."""
    if not str(key).startswith(CUSTOM):
        raise ValueError("only your own questions can be removed; clear a built-in answer instead")
    with db.tx(conn):
        gone = conn.execute("DELETE FROM autofill_answers WHERE profile = ? AND key = ?", (pid, key)).rowcount
    if not gone:
        raise KeyError(key)
    return {"removed": key}


def listing(conn, pid: str) -> list[dict]:
    """The Answers page's bank: every category in order, built-in keys in catalogue order, own questions last. A
    choice question carries its dropdown (`choices`: common answers + what boards showed)."""
    from . import choices
    bank = load(conn, pid)
    boards = choices.learned(conn, pid)
    order = {k.key: i for i, k in enumerate(keys.CATALOGUE)}
    cats = [c for c, _ in keys.CATEGORIES]
    rows = []
    for key, entry in bank.items():
        known = keys.BY_KEY.get(key)
        rows.append({"key": key, "label": entry["label"], "category": entry["category"], "answer": entry["answer"],
                     "question": entry.get("question"), "options": entry["options"],
                     "legal": bool(known and known.legal), "blank_ok": bool(known and known.blank_ok),
                     "choices": choices.for_key(key, boards), "order": order.get(key, len(order))})
    rows.sort(key=lambda r: (cats.index(r["category"]) if r["category"] in cats else len(cats), r["order"],
                             r["label"]))
    return rows
