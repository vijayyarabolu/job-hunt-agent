"""Draft outreach: for one contact of one application (a recruiter, the hiring manager or a peer on the
team), a ready-to-paste prompt for the person's own Claude chat, a plain template that works without Claude, and,
when an Anthropic key is saved, a one-click draft written by Claude from the same prompt (claude_draft). The app only
builds text: the person copies it and sends everything themselves (nothing here sends or mails anything)."""
import json
import re

from . import claude_api, config, jdsnap, lexicon, resumes, trackapi
from .finder import rank, signals
from .resume import ats

MAX_REQUIREMENTS = 5
MAX_ACHIEVEMENTS = 3
MIN_ACHIEVEMENTS = 2
LINE_MIN, LINE_MAX = 40, 320
EMAIL_WORDS = 120                 # the ask: one cold email under 120 words
NOTE_CHARS = 300                  # one LinkedIn connection note under 300 characters
ACHIEVEMENT_WORDS = 28
_BULLET = re.compile(r"^[\s\-•*·–]+")
_SKILL_LINE = re.compile(r"^[^.:]{2,40}:\s")         # "Languages & Data: Python, SQL, ..." (a skills row)
MAX_LIST_COMMAS = 5


def _is_result(text: str) -> bool:
    """A resume line that reads as an achievement, not a skills list."""
    return not _SKILL_LINE.match(text) and text.count(",") <= MAX_LIST_COMMAS


def _contact(row: dict, contact_id: int) -> dict:
    found = next((c for c in row.get("contacts") or [] if c["id"] == contact_id), None)
    if found is None:
        raise KeyError(contact_id)
    return found


def _resume(pid: str, wanted: str | None) -> resumes.Resume | None:
    mine = resumes.of_profile(config.profile(pid))
    return next((r for r in mine if r.id == wanted), mine[0] if mine else None)


def _resume_lines(pid: str, resume: resumes.Resume | None) -> list[str]:
    """The resume's own lines (bullets keep their wording; the stored text is lowercased for matching)."""
    if resume is None:
        return []
    if resume.file:
        data = (config.PROFILES / pid / resumes.RESUME_DIR / resume.file).read_bytes()
        return resumes._docx_text(resume.digest, data).split("\n")
    baseline = json.loads((config.PROFILES / pid / "baseline.json").read_text())
    return [b for job in baseline.get("experience") or [] for b in job.get("bullets") or []]


def requirements(jd: str) -> list[str]:
    """The JD's required lines that name the most hard skills, in the JD's order."""
    lines = [(_BULLET.sub("", line).strip(), len(lexicon.spans(line)))
             for weight, line in ats.weighted_lines(jd) if weight >= ats.REQUIRED]
    picked = sorted((x for x in lines if x[1] and 15 <= len(x[0]) <= 220), key=lambda x: -x[1])[:MAX_REQUIREMENTS]
    keep = {text for text, _ in picked}
    return [text for text, _ in lines if text in keep]


def achievements(lines: list[str], jd: str) -> list[str]:
    """The 2-3 resume lines sharing the most JD skills (numbers break ties)."""
    wanted = {t.name for t in ats.extract(jd)}
    scored = []
    for line in lines:
        text = _BULLET.sub("", line).strip()
        if not LINE_MIN <= len(text) <= LINE_MAX or not _is_result(text):
            continue
        shared = len({name for _, _, name in lexicon.spans(text)} & wanted)
        scored.append((shared * 2 + bool(re.search(r"\d", text)), text))
    scored.sort(key=lambda x: -x[0])
    best = [text for score, text in scored if score >= 2][:MAX_ACHIEVEMENTS]
    return best if len(best) >= MIN_ACHIEVEMENTS else [text for _, text in scored[:MIN_ACHIEVEMENTS]]


def _skills(jd: str, n: int) -> list[str]:
    terms = sorted(ats.extract(jd), key=lambda t: (-t.need, -t.count, t.first))
    return [(t.forms[0] if t.forms else t.name) for t in terms[:n]]


def _joined(words: list[str]) -> str:
    return ", ".join(words[:-1]) + f" and {words[-1]}" if len(words) > 1 else "".join(words)


def _clip_words(text: str, n: int) -> str:
    words = text.rstrip(".").split()
    return " ".join(words[:n]) + ("…" if len(words) > n else "")


def _prompt(me: dict, contact: dict, row: dict, team: str | None, reqs: list[str], resume, wins: list[str]) -> str:
    role = rank.likely_role(contact.get("role"))
    lines = [
        "I applied for a job and want to reach out to someone there. Write two short messages that I will send "
        "myself.", "",
        f"Contact: {contact.get('name') or 'unknown name'}, {contact.get('role') or 'title unknown'} at "
        f"{row['company']} (likely the {role}).",
        f"Job: {row['role_title']} at {row['company']}" + (f", {team} team" if team else "") +
        (f". I applied on {row['date_applied']}." if row.get("date_applied") else "."),
        "", "Key requirements from the job description:", *[f"- {r}" for r in reqs], "",
        f"The resume I used: {resume.label if resume else 'my resume'}.",
        "My most relevant achievements for this job:", *[f"- {w}" for w in wins], "",
        "Write:",
        "1. One cold email: a subject line, then a body under 120 words.",
        "2. One LinkedIn connection note under 300 characters.",
        "Rules: specific to this person and this role; no flattery; plain, direct words; say I already applied; "
        "end with one clear small ask (a 15-minute call, or whether they are the right person to talk to about "
        f"this role); sign as {me.get('name') or 'me'}" + (f" and add my LinkedIn {me['linkedin']}"
                                                            if me.get("linkedin") else "") + "."]
    return "\n".join(lines)


def _template(me: dict, contact: dict, row: dict, team: str | None, skills: list[str], wins: list[str]) -> dict:
    first = ((contact.get("name") or "").split() or ["there"])[0]
    my_name, my_first = me.get("name") or "", (me.get("name") or "").split(" ")[0]
    applied = f" on {row['date_applied']}" if row.get("date_applied") else ""
    focus = f"The role's focus on {_joined(skills[:3])} is close to my own work. " if skills else ""
    ask = f"on the {team} team" if team else "for this role"
    for words in (ACHIEVEMENT_WORDS, ACHIEVEMENT_WORDS // 2, 0):
        example = f"One example: {_clip_words(wins[0], words)}. " if wins and words else ""
        email = (f"Hi {first},\n\nI applied for the {row['role_title']} role at {row['company']}{applied}. "
                 f"{focus}{example}\n\nWould you have 15 minutes for a short call, or could you point me to the right "
                 f"person {ask}?\n\nThanks,\n{my_name}" + (f"\n{me['linkedin']}" if me.get("linkedin") else ""))
        if len(email.split()) < EMAIL_WORDS:
            break
    for n in (2, 1, 0):
        work = f" My work is in {_joined(skills[:n])}." if n and skills else ""
        note = (f"Hi {first}, I applied for the {row['role_title']} role at {row['company']}.{work} Would you be open "
                f"to a short chat, or point me to the right person? Thanks, {my_first}")
        if len(note) <= NOTE_CHARS:
            break
    return {"subject": f"{row['role_title']} application: {my_name}".strip(), "email": email,
            "linkedin": note[:NOTE_CHARS]}


def draft(conn, pid: str, app_id: int, contact_id: int) -> dict:
    """{'prompt', 'template': {subject, email, linkedin}, 'requirements', 'achievements', 'resume', 'contact'}."""
    row = trackapi.get(conn, pid, app_id)
    contact = _contact(row, contact_id)
    jd = (jdsnap.get(conn, app_id) or {}).get("text") or row.get("jd") or ""
    me = config.profile(pid)
    resume = _resume(pid, row.get("resume_used"))
    team = signals.team_of(jd)
    reqs, wins = requirements(jd), achievements(_resume_lines(pid, resume), jd)
    return {"prompt": _prompt(me, contact, row, team, reqs, resume, wins),
            "template": _template(me, contact, row, team, _skills(jd, 3), wins),
            "requirements": reqs, "achievements": wins,
            "resume": {"id": resume.id, "label": resume.label} if resume else None,
            "contact": {k: contact.get(k) for k in ("id", "name", "role", "email", "linkedin", "email_sent",
                                                     "linkedin_sent")}}


DRAFT_SCHEMA = {"type": "object", "required": ["subject", "email", "linkedin"], "properties": {
    "subject": {"type": "string"}, "email": {"type": "string"}, "linkedin": {"type": "string"}}}


def claude_draft(conn, pid: str, app_id: int, contact_id: int) -> dict:
    """The same prompt, answered by Claude with the saved key: {subject, email, linkedin, by}. Without a key (or in
    demo mode) it returns the plain template, saying so."""
    made = draft(conn, pid, app_id, contact_id)
    if config.demo_mode() or not claude_api.configured():
        why = "demo mode" if config.demo_mode() else "no Anthropic key saved (Settings > Setup)"
        return {**made["template"], "by": f"template ({why})"}
    out, _ = claude_api.ask(made["prompt"] + "\nAnswer as JSON: subject, email (the body), linkedin (the note).",
                            schema=DRAFT_SCHEMA, max_tokens=4000, effort="low")
    note = str(out.get("linkedin") or "").strip()
    return {"subject": str(out.get("subject") or "").strip(), "email": str(out.get("email") or "").strip(),
            "linkedin": note[:NOTE_CHARS], "by": f"Claude ({claude_api.model()})"}
