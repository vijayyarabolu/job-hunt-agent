"""GOAL27 §4-§6: what the extension asks job-hunt-agent for besides answers.

- suggest(): a page's own short questions that no saved answer covers, answered from the tailored resume and the JD by
  Claude Haiku in one call (short, honest, nothing the resume does not say; a choice question gets one of its own
  choices or nothing). Filled with a "review" mark. Legal, EEO, sponsorship and sensitive questions are never sent;
  long free-text questions are offered (the panel's Draft), never filled.
- cover(): a cover letter on request (the person's click), from the tailored resume and the JD; kept with the job and
  given as text to paste or as a PDF named like the resume.
- score(): the match % and the missing keywords of any job page against each of the person's resumes.
- save_job(): "Save job" from any page: a tracker row with status Saved (the page's JD text kept).
"""
import json
import re

from .. import config, db, keywords, resumes, rgjobs, trackapi
from ..resume import ats
from . import draft, keys, match, resume

HAIKU = "claude-haiku-5-5"
MAX_QUESTIONS = 25
MAX_SHORT = 160                      # characters: a "short answer"; anything longer is offered, not filled
MAX_PAGE_TEXT = 60000
MIN_JD_CHARS = 200
COVER_MAX = 3000
_CACHE: dict[tuple, dict] = {}       # (profile, job, question) -> answer: a refilled page costs no second call

SUGGEST_SYSTEM = ("You fill short fields of a job application from the applicant's resume. Answer with JSON only. "
                  "Never invent a fact.")
SUGGEST_PROMPT = """Answer each application question below for the applicant, using ONLY the resume and job description.

Rules:
- Short: a number, a few words, or one sentence (at most {max_short} characters).
- Only facts the resume states (employers, titles, years, tools, degrees). If the resume does not answer it, the answer
  is "" (empty). Never guess, never invent.
- A question with "choices": the answer is exactly one of its choices, copied word for word, or "".
- "How many years of X": count from the resume's dates; whole number only.
- No dashes, no quotes around the answer.

Return a JSON array: [{{"id": "<id>", "answer": "<text>"}}] with one entry per question, same ids.

Questions:
{questions}

Job: {role} at {company}
Job description:
{jd}

Resume:
{resume}
"""
COVER_PROMPT = """Write a cover letter for this application.

Rules:
- 3 short paragraphs, 180 to 260 words, first person, plain and specific to this company and role.
- Use ONLY facts stated in the resume (companies, titles, numbers, tools). Never invent a fact, number or project.
- Start with "Dear Hiring Team," and end with "Sincerely," and the applicant's name on the next line.
- No em dashes or en dashes, no bullet points, no placeholders like [Company].

Applicant: {name}
Company: {company}
Role: {role}

Job description:
{jd}

Resume:
{resume}
"""


def _run(prompt: str, pid: str, model: str, runner=None) -> str:
    if runner:
        return runner(prompt, pid)
    old = draft.MODEL
    draft.MODEL = model
    try:
        return draft.run(prompt, pid)
    finally:
        draft.MODEL = old


def _job(conn, pid: str, uid: str | None, app_id: int | None) -> tuple[str, str, str, str]:
    """(company, role, JD, resume text) of the matched job."""
    company, role, jd = draft._jd(conn, pid, uid, app_id)
    choice = resume.choose(conn, pid, uid, app_id)
    return company, role, jd or "", draft._resume_text(draft.content(pid, choice.get("rg_id")))


def _askable(q: dict) -> bool:
    """Not legal / EEO / sponsorship / sensitive, not a long free-text question."""
    label = str(q.get("label") or "")
    text = keys.norm(label)
    known = keys.BY_KEY.get(str(q.get("key") or "")) or keys.classify(text, q.get("hints") or [])
    if known and (known.no_guess or known.category == "eeo"):    # GOAL30 §1.4: legal and personal facts are the user's
        return False
    if keys.LEGAL_WORDS.search(text) or re.search(r"\b(ssn|social security|birth|passport|license|salary|gender|race|"
                                                   r"veteran|disabilit|criminal|convicted|employed|employment status|"
                                                   r"health ?care|hcp|physician|start date|notice|compensation|"
                                                   r"terminated|disciplin|relative|relationship)\b", text):
        return False
    from .learn import LONG_QUESTION
    return not (q.get("kind") == "textarea" and LONG_QUESTION.search(text))


def suggest(conn, pid: str, args: dict, runner=None) -> dict:
    """{'answers': [{id, answer, status: review | none}]}: short answers from the resume for the questions job-hunt-agent had
    no saved answer to."""
    uid, app_id = str(args.get("uid") or "") or None, int(args["app"]) if str(args.get("app") or "").isdigit() else None
    if not uid and not app_id:
        raise ValueError("this page is not matched to a job yet: pick the job in the panel first")
    raw = [q for q in (args.get("questions") or [])[:MAX_QUESTIONS] if isinstance(q, dict) and q.get("id")]
    asked = [q for q in raw if _askable(q)]
    out = {str(q["id"]): "" for q in raw}
    job_key = uid or f"app{app_id}"
    todo = []
    for q in asked:
        hit = _CACHE.get((pid, job_key, keys.norm(q.get("label")), tuple(q.get("options") or ())))
        if hit is not None:
            out[str(q["id"])] = hit["answer"]
        else:
            todo.append(q)
    if todo:
        company, role, jd, resume_text = _job(conn, pid, uid, app_id)
        listed = [{"id": str(q["id"]), "question": " ".join(str(q.get("label") or "").split())[:400],
                   **({"choices": [str(o)[:120] for o in (q.get("options") or [])[:40]]} if q.get("options") else {})}
                  for q in todo]
        prompt = SUGGEST_PROMPT.format(max_short=MAX_SHORT, questions=json.dumps(listed, indent=1), role=role,
                                       company=company, jd=jd[:draft.JD_MAX], resume=resume_text)
        text = _run(prompt, pid, HAIKU, runner)
        found = re.search(r"\[.*\]", text, re.S)
        try:
            got = json.loads(found.group(0)) if found else []
        except ValueError:
            got = []
        by_id = {str(g.get("id")): str(g.get("answer") or "").strip() for g in got if isinstance(g, dict)}
        for q in todo:
            answer = draft.clean(by_id.get(str(q["id"]), ""))[:MAX_SHORT] if by_id.get(str(q["id"])) else ""
            options = [str(o) for o in q.get("options") or []]
            if options and answer and answer not in options:          # a choice question: its own words or nothing
                answer = next((o for o in options if o.strip().lower() == answer.lower()), "")
            out[str(q["id"])] = answer
            _CACHE[(pid, job_key, keys.norm(q.get("label")), tuple(q.get("options") or ()))] = {"answer": answer}
    return {"answers": [{"id": i, "answer": a, "status": "review" if a else "none"} for i, a in out.items()]}


CHOOSE_PROMPT = """For each form question below, pick the ONE option that means the same as the applicant's own saved
answer. Copy the option exactly, word for word. If no option clearly means the same, the pick is "" (empty).
Never pick an option that changes the meaning (a different gender, race, veteran or disability status, a Yes for a No).

Return a JSON array: [{{"id": "<id>", "pick": "<option text or empty>"}}] with one entry per question, same ids.

Questions:
{questions}
"""


def choose(conn, pid: str, args: dict, runner=None) -> dict:
    """1.3.1 (AUTOFILL-BUGS #3): the user's saved answer matched no option by wording or meaning: Claude Haiku picks the
    option that means the same, only from the form's own list (anything else is dropped), for the panel's ? review.
    The answer is the user's own; nothing about the user beyond it is sent."""
    items = [i for i in (args.get("items") or [])[:MAX_QUESTIONS]
             if isinstance(i, dict) and i.get("id") and i.get("answer") and isinstance(i.get("options"), list) and i["options"]]
    if not items:
        return {"picks": []}
    listed = [{"id": str(i["id"]), "question": " ".join(str(i.get("question") or "").split())[:300],
               "saved_answer": str(i["answer"])[:300], "options": [str(o)[:200] for o in i["options"][:60]]} for i in items]
    text = _run(CHOOSE_PROMPT.format(questions=json.dumps(listed, indent=1)), pid, HAIKU, runner)
    found = re.search(r"\[.*\]", text, re.S)
    try:
        got = json.loads(found.group(0)) if found else []
    except ValueError:
        got = []
    by_id = {str(g.get("id")): str(g.get("pick") or "").strip() for g in got if isinstance(g, dict)}
    out = []
    for i in listed:
        pick = by_id.get(i["id"], "")
        exact = next((o for o in i["options"] if o.strip().lower() == pick.lower()), "") if pick else ""
        out.append({"id": i["id"], "pick": exact, "status": "review" if exact else "none"})
    return {"picks": out}


def cover(conn, pid: str, args: dict, runner=None) -> dict:
    """The person clicked Cover letter: {'text', 'file_name'} (kept with the job; the PDF is cover.pdf)."""
    uid, app_id = str(args.get("uid") or "") or None, int(args["app"]) if str(args.get("app") or "").isdigit() else None
    if not uid and not app_id:
        raise ValueError("this page is not matched to a job yet: pick the job in the panel first")
    company, role, jd, resume_text = _job(conn, pid, uid, app_id)
    name = str(config.profile(pid).get("name") or "").strip()
    prompt = COVER_PROMPT.format(name=name, company=company, role=role, jd=jd[:draft.JD_MAX], resume=resume_text)
    text = draft.clean((runner or (lambda p, who: _run(p, who, draft.MODEL)))(prompt, pid))[:COVER_MAX]
    text = "\n".join(line.strip() for line in text.splitlines())
    with db.tx(conn):
        conn.execute("INSERT INTO autofill_drafts(profile, job_uid, app_id, question, text, at) VALUES (?,?,?,?,?,?)",
                     (pid, uid, app_id, "Cover letter", text, db.iso(db.utcnow())))
    return {"text": text, "file_name": cover_name(conn, pid, uid, app_id)}


def cover_name(conn, pid: str, uid: str | None, app_id: int | None) -> str:
    """The resume's upload name with Cover_Letter for Resume (Alex_Rivera_Data_Scientist_Cover_Letter.pdf)."""
    name = str(resume.pick(conn, pid, uid, app_id).get("file_name") or "")
    if "Resume" in name:
        return name.replace("Resume", "Cover_Letter")
    stem = name.rsplit(".", 1)[0] if name else "_".join(str(config.profile(pid).get("name") or "Cover").split())
    return f"{stem}_Cover_Letter.pdf"


def cover_text(conn, pid: str, uid: str | None, app_id: int | None) -> str:
    row = conn.execute("SELECT text FROM autofill_drafts WHERE profile = ? AND question = 'Cover letter' AND "
                       "((? IS NOT NULL AND job_uid = ?) OR (? IS NOT NULL AND app_id = ?)) ORDER BY id DESC LIMIT 1",
                       (pid, uid, uid, app_id, app_id)).fetchone()
    if row is None:
        raise KeyError("no cover letter for this job yet: click Cover letter first")
    return row["text"]


def score(conn, pid: str, args: dict) -> dict:
    """GOAL27 §6: {'pct', 'resume', 'matched', 'missing', 'n_total', 'job'} for any job page. The JD is the matched
    job's stored one, else the page's text the extension sent."""
    uid, app_id = str(args.get("uid") or "") or None, int(args["app"]) if str(args.get("app") or "").isdigit() else None
    jd = ""
    if uid:
        try:
            jd = rgjobs.jd_for(conn, pid, uid)
        except KeyError:
            jd = ""
    elif app_id:
        jd = draft._jd(conn, pid, None, app_id)[2] or ""
    if len(jd) < MIN_JD_CHARS:
        jd = str(args.get("text") or "")[:MAX_PAGE_TEXT]
    if len(jd.strip()) < MIN_JD_CHARS:
        raise ValueError("no job description on this page to score")
    mine = resumes.of_profile({**config.profile(pid), "id": pid})
    if not mine:
        raise ValueError("no resume in your profile to score against")
    terms = ats.extract(jd)
    packed = keywords.pack(terms, tuple(r.terms for r in mine), tuple(r.text for r in mine))
    scores = keywords.score_all(json.dumps(packed), mine)
    top = keywords.best(scores)
    return {"pct": top.pct, "resume": top.label, "matched": list(top.matched)[:30], "missing": list(top.missing)[:15],
            "n_total": top.n_total, "all": [{"label": s.label, "pct": s.pct} for s in scores]}


def save_job(conn, pid: str, args: dict) -> dict:
    """"Save job" from any page: the matched feed job, else what the page says (title, company, URL, JD)."""
    url = str(args.get("url") or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("this page has no web address to save")
    found = match.find(conn, pid, url, (), jobs=None)
    job = found.get("job")
    if job and job.get("app"):
        return {"app": job["app"], "already": True}
    data = {"job_url": url, "apply_url": url, "status": "saved"}
    if job and job.get("uid"):
        data["job_uid"] = job["uid"]
    else:
        data.update({"company": str(args.get("company") or "").strip()[:200],
                     "role_title": str(args.get("title") or "").strip()[:200],
                     "location": str(args.get("location") or "").strip()[:200] or None})
    try:
        row = trackapi.create(conn, pid, data)
    except ValueError as err:
        found_row = re.search(r"#(\d+)", str(err))
        if found_row:
            return {"app": int(found_row.group(1)), "already": True}
        raise
    text = str(args.get("text") or "")[:trackapi.MAX_LONG_TEXT].strip()
    if text and not (job and job.get("uid")):
        with db.tx(conn):
            conn.execute("UPDATE applications SET jd_text = coalesce(nullif(jd_text, ''), ?) WHERE id = ?",
                         (text, row["id"]))
    return {"app": row["id"], "already": False, "company": row.get("company"), "role": row.get("role_title")}
