""""Draft" next to a free-text question ("Why do you want to work here?", a required cover letter).

Only on the person's click, one question at a time: Claude (claude-sonnet-5-5, effort medium, the same minimal
headless call as the email reader) gets the question, the JD and the tailored resume (else the base resume) and
writes 2-4 plain sentences in the person's voice with no fact the resume does not state. Dashes are taken out
(the person does not use em dashes). The draft is kept with the job (autofill_drafts).

Also here: the tailored resume's content (summary, skills, jobs) that Workday's Skills box and the Draft use.
"""
import json
import logging
import re
import subprocess

from .. import config, db, rgclient, rgjobs
from ..mail import claude as mail_claude
from ..resume import baseline
from . import resume

log = logging.getLogger(__name__)
MODEL = "claude-sonnet-5-5"
TIMEOUT_S = 180
JD_MAX = 7000
MAX_QUESTION = 600
SYSTEM = "You write short answers for a job application form, in the applicant's own voice. Answer with the text only."
PROMPT = """Write the applicant's answer to this application question.

Rules:
- 2 to 4 plain sentences, first person, friendly and specific to this company and role.
- Use ONLY facts stated in the resume below (companies, titles, numbers, tools). Never invent a fact, a number,
  a project or a reason that is not there. If the question asks for something the resume does not say, answer with
  what the resume does say.
- No em dashes or en dashes, no bullet points, no greeting or sign-off, no placeholders.

Question: {question}
Company: {company}
Role: {role}

Job description:
{jd}

Resume:
{resume}
"""
_DASHES = re.compile(r"\s*[—–]\s*")


_LABEL = re.compile(r"^[A-Z][A-Za-z/&+ -]{1,30}:\s*")         # "Languages:", "Cloud & DevOps:": a label, not a skill


def _parts(text: str) -> list[str]:
    """Split on , | ; and bullets outside parentheses."""
    out, depth, cur = [], 0, []
    for ch in text:
        depth += ch == "("
        depth -= ch == ")" and depth > 0
        if ch in ",|;•" and depth == 0:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return [x.strip() for x in out if x.strip()]


def skills_of(text: str | None) -> list[str]:
    """GOAL23 §2 (user 14:24): every skill of a Skills line, one by one: split on commas, "|", ";"; category labels
    dropped; "AWS (S3, Glue)" is AWS, S3 and Glue."""
    found = []
    for part in _parts(str(text or "")):
        part = _LABEL.sub("", part).strip()
        head, _, inner = part.partition("(")
        inner = inner.rsplit(")", 1)[0] if inner else ""
        subs = [x.strip() for x in re.split(r"[,/;]", inner) if x.strip()] if "," in inner or ";" in inner else []
        found += [head.strip()] + subs if subs else [part]
    return [s for s in found if s]


def _unique(items: list[str]) -> list[str]:
    seen, out = set(), []
    for s in items:
        k = re.sub(r"[^a-z0-9+#]+", "", s.lower())
        if k and k not in seen:
            seen.add(k)
            out.append(s)
    return out


def _split(text: str | None) -> list[str]:
    return skills_of(text)


def _from_diff(diff: dict) -> dict:
    skills = _unique([s for cat in diff.get("skills") or []
                      for s in skills_of((cat.get("items") or {}).get("new") or (cat.get("items") or {}).get("base"))])
    jobs = [{"company": j.get("company"), "title": j.get("title"), "dates": j.get("dates"),
             "bullets": [b.get("new") or b.get("base") for b in j.get("bullets") or [] if isinstance(b, dict)]}
            for j in diff.get("jobs") or []]
    return {"summary": (diff.get("summary") or {}).get("new") or "", "skills": skills, "jobs": jobs}


def _tailored(rg_id: int) -> dict:
    """resumegen's diff of that tailored resume ({} when it has none); raises when resumegen cannot give it."""
    got = rgclient.request("GET", f"/api/jobs/{int(rg_id)}")
    return ((got.get("job") or {}).get("result") or {}).get("diff") or {}


def _baseline(pid: str) -> dict:
    try:
        return json.loads((config.PROFILES / pid / "baseline.json").read_text())
    except (OSError, ValueError):
        return {}


def _flat_skills(resume_dict: dict) -> list[str]:
    return _unique([x for c in resume_dict.get("skills") or [] for s in c.get("items") or [] for x in skills_of(s)])


def content(pid: str, rg_id: int | None) -> dict:
    """{'summary', 'skills', 'jobs'} for the Draft: the tailored resume when there is one (resumegen's diff, 'new'
    side), else the base resume (baseline.json)."""
    if rg_id:
        try:
            diff = _tailored(rg_id)
            if diff:
                return _from_diff(diff)
        except (rgclient.Offline, KeyError, ValueError) as err:
            log.warning("tailored resume %s unreadable, using the base resume: %s", rg_id, err)
    base = _baseline(pid)
    return {"summary": base.get("summary") or "", "skills": _flat_skills(base),
            "jobs": [{"company": j.get("company"), "title": j.get("title"), "dates": j.get("dates"),
                      "bullets": j.get("bullets") or []} for j in base.get("experience") or []]}


def _base_docx(pid: str, resume_id: str | None):
    """The .docx of the base resume that is uploaded for this job (the profile's resumes entry), or None."""
    entries = config.profile(pid).get("resumes") or []
    entry = next((e for e in entries if str(e.get("id")) == str(resume_id)), None)
    path = config.PROFILES / pid / "resumes" / str(entry.get("file") or "") if entry else None
    return path if path and path.suffix.lower() == ".docx" and path.is_file() else None


def _base_skills(pid: str, resume_id: str | None) -> list[str]:
    path = _base_docx(pid, resume_id)
    if path:
        try:
            return _flat_skills(baseline.parse(path))
        except Exception as err:                       # a .docx python-docx cannot read: the baseline's skills
            log.warning("base resume %s unreadable for skills: %s", path.name, err)
    return _flat_skills(_baseline(pid))


def skills_for(pid: str, choice: dict) -> list[str]:
    """GOAL23 §2 (user 14:24): the Skills section of the exact resume uploaded for this job: the tailored one when
    there is one (never the base instead: unreadable -> no skills, the user fills the box), else that base resume file."""
    if choice.get("rg_id"):
        try:
            return _from_diff(_tailored(choice["rg_id"]))["skills"]
        except (rgclient.Offline, KeyError, ValueError) as err:
            log.warning("tailored resume %s unreadable, Skills left for the user: %s", choice["rg_id"], err)
            return []
    return _base_skills(pid, choice.get("resume_id"))


def _resume_text(data: dict) -> str:
    lines = [data["summary"], "Skills: " + ", ".join(data["skills"])]
    for job in data["jobs"]:
        lines.append(f"{job['title']}, {job['company']} ({job['dates']})")
        lines += [f"- {b}" for b in job["bullets"] if b]
    return "\n".join(x for x in lines if x)


def _jd(conn, pid: str, uid: str | None, app_id: int | None) -> tuple[str, str, str]:
    if uid:
        row = conn.execute("SELECT company, title FROM jobs WHERE uid = ?", (uid,)).fetchone()
        if row is None:
            raise KeyError(uid)
        return row["company"], row["title"], rgjobs.jd_for(conn, pid, uid)
    row = conn.execute("SELECT a.company, a.role_title, coalesce(nullif(s.text, ''), a.jd_text, '') AS jd "
                       "FROM applications a LEFT JOIN jd_snapshots s ON s.app_id = a.id "
                       "WHERE a.id = ? AND a.profile = ?", (int(app_id), pid)).fetchone()
    if row is None:
        raise KeyError(app_id)
    return row["company"], row["role_title"], row["jd"]


def run(prompt: str, pid: str) -> str:
    """One minimal headless call; the answer text, or ValueError saying why it failed."""
    cmd = [mail_claude.claude_bin(), "-p", "--output-format", "json", "--model", MODEL, "--effort", "medium",
           "--system-prompt", SYSTEM, "--tools", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
           "--setting-sources", "", "--disable-slash-commands", "--no-session-persistence", "--max-turns", "1"]
    try:
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=TIMEOUT_S,
                              env=mail_claude.env_for(pid))
    except (OSError, subprocess.TimeoutExpired) as err:
        raise ValueError(f"Draft failed: Claude did not answer ({type(err).__name__})") from None
    try:
        raw = json.loads((proc.stdout or "").strip() or "{}")
    except ValueError:
        raw = {}
    text = str(raw.get("result") or "").strip()
    if raw.get("is_error") or not text:
        noise = f"{text} {proc.stderr or ''}"
        why = ("usage limit reached" if mail_claude.LIMIT.search(noise) else
               "Claude is not signed in on this Mac" if mail_claude.LOGIN.search(noise) else "no answer came back")
        log.warning("autofill draft for %s failed (exit %s): %s", pid, proc.returncode, (proc.stderr or "")[-300:])
        raise ValueError(f"Draft failed: {why}")
    return text


def clean(text: str) -> str:
    """Plain sentences: no dashes, no surrounding quotes, single spaces."""
    out = _DASHES.sub(", ", text.strip().strip('"').strip())
    return re.sub(r"[ \t]+", " ", out).strip()


def draft(conn, pid: str, uid: str | None, app_id: int | None, question: str, runner=None) -> dict:
    """The person clicked Draft: one answer, kept with the job."""
    question = " ".join(str(question or "").split())[:MAX_QUESTION]
    if not question:
        raise ValueError("no question to draft an answer for")
    if not uid and not app_id:
        raise ValueError("this page is not matched to a job yet: pick the job in the panel first")
    app = int(app_id) if app_id else None
    company, role, jd = _jd(conn, pid, uid, app)
    choice = resume.choose(conn, pid, uid, app)
    prompt = PROMPT.format(question=question, company=company, role=role, jd=(jd or "")[:JD_MAX],
                           resume=_resume_text(content(pid, choice.get("rg_id"))))
    text = clean((runner or run)(prompt, pid))
    with db.tx(conn):
        conn.execute("INSERT INTO autofill_drafts(profile, job_uid, app_id, question, text, at) VALUES (?,?,?,?,?,?)",
                     (pid, uid, app, question, text, db.iso(db.utcnow())))
    return {"text": text}
