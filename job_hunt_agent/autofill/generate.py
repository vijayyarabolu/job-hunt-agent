"""GOAL29 §5: "Generate" next to an open application question (the extension panel and the job page's Application
questions box). job-hunt-agent gathers what the answer is written from (the question, the field's limit, the job's JD and
company, the tailored resume the person uploads for this job) and hands the work to resumegen's worker queue, which
writes it on the person's own Claude login with their fact bank and SKILL.md. Only on a click; nothing is filled here.

The answer is cached in resumegen by person, job and question, so reopening the page shows it again without a call.
(GOAL23's Draft, draft.py, is the older one-call version; the panel now offers Generate.)"""
from .. import rgclient
from . import draft, learn, origin, resume

MAX_QUESTION = 1000
MAX_QUESTIONS = 50
MAX_CHARS_LIMIT = 20000
MIN_JD = 200
VARIANTS = ("", "regenerate", "shorter")


def _job(args: dict) -> tuple:
    uid = str(args.get("uid") or "").strip() or None
    app = args.get("app")
    app_id = int(app) if str(app or "").strip().isdigit() else None
    if not uid and not app_id:
        raise ValueError("this page is not matched to a job yet: pick the job first")
    return uid, app_id, uid or f"app:{app_id}"


def _question(args: dict) -> str:
    text = " ".join(str(args.get("question") or "").split())[:MAX_QUESTION]
    if not text:
        raise ValueError("no question to answer")
    return text


def _limit(args: dict) -> int | None:
    try:
        value = int(args.get("max_chars") or 0)
    except (TypeError, ValueError):
        return None
    return value if 0 < value <= MAX_CHARS_LIMIT else None


def generate(conn, pid: str, args: dict) -> dict:
    """{"answer": {id, state, label, ...}}: queued in resumegen ("Writing with Claude…")."""
    uid, app_id, key = _job(args)
    question = _question(args)
    variant = str(args.get("variant") or "")
    if variant not in VARIANTS:
        raise ValueError("variant: regenerate or shorter")
    company, role, jd = draft._jd(conn, pid, uid, app_id)
    if len(jd or "") < MIN_JD:
        raise ValueError("this job has no description stored: open the posting in job-hunt-agent first")
    choice = resume.choose(conn, pid, uid, app_id)
    body = {"profile": pid, "job_key": key, "question": question, "max_chars": _limit(args),
            "company": company, "role": role, "jd_text": jd, "rg_job_id": choice.get("rg_id"), "variant": variant,
            "previous": str(args.get("previous") or "")[:6000] or None}
    return {"answer": rgclient.request("POST", "/api/answers", body).get("answer")}


def status(args: dict) -> dict:
    try:
        aid = int(args.get("id"))
    except (TypeError, ValueError):
        raise ValueError("id: the answer to look at") from None
    return {"answer": rgclient.request("GET", f"/api/answers/{aid}").get("answer")}


def lookup(pid: str, args: dict, conn=None) -> dict:
    """{"answers": {question: view}, "filled": {question: {origin, names}}}: the cached answers of the page's open
    questions (no Claude call), and (GOAL30 §2) for open questions already holding text: whose job that text was
    written for, and another company's name in it."""
    uid, app_id, key = _job(args)
    asked = [" ".join(str(q).split())[:MAX_QUESTION] for q in (args.get("questions") or [])[:MAX_QUESTIONS]
             if str(q).strip()]
    found = rgclient.request("POST", "/api/answers/lookup", {"profile": pid, "job_key": key, "questions": asked}) \
        if asked else {}
    return {"answers": found.get("answers") or {}, "filled": _filled(conn, pid, args, uid, app_id, key)}


def _filled(conn, pid: str, args: dict, uid, app_id, key: str) -> dict:
    """{question: {"origin": {company, title, at} | None, "names": "OldCo" | None}} for the filled open questions."""
    items = args.get("filled") if isinstance(args.get("filled"), dict) else {}
    if conn is None or not items:
        return {}
    company = str(args.get("company") or "")
    if not company:
        try:
            company = draft._jd(conn, pid, uid, app_id)[0]
        except KeyError:
            company = ""
    out = {}
    for question, text in list(items.items())[:MAX_QUESTIONS]:
        text = str(text or "")[:6000]
        view = origin.of_snippet(conn, pid, text, key)
        names = origin.names_other(conn, pid, text, company)
        if view or names:
            out[" ".join(str(question).split())[:MAX_QUESTION]] = {"origin": view, "names": names,
                                                                     "label": origin.label_of(view)}
    return out


def save(conn, pid: str, args: dict) -> dict:
    """Save to Answers: kept as a saved long answer, offered for the same question at other companies."""
    uid, app_id, _ = _job(args)
    text = str(args.get("text") or "").strip()
    if not text:
        raise ValueError("no answer to save")
    company, _, _ = draft._jd(conn, pid, uid, app_id)
    return learn.save_answer(conn, pid, _question(args), text, uid, app_id, company)
