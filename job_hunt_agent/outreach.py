"""Draft outreach: for one contact, or one kind of person nobody was found for yet (recruiter / AI/ML manager / peer
engineer), a LinkedIn note and a cold email built from THAT person's own resume, and a ready-to-paste prompt for their
own Claude chat under the same rules, plus, when an Anthropic key is saved, a one-click draft written by Claude from the
same prompt (claude_draft). The app only builds text: the person copies it and sends everything themselves (nothing
here sends or mails anything).

The note (<= 300 characters): greeting, "I applied for <role> at <company>", one JD hook (the JD skills a resume line
also names), one proof line (that real resume line, "At <company>, I ..."), one small ask by kind. The email (< 120
words, with a subject): the same, with the whole resume line. Plain words, no flattery, no em dashes. Proof lines come
from the person's fact bank when they have one (fact_bank.md Part 1 in their profile folder), else their current resume
(baseline.json).
"""
import json
import re

from . import claude_api, config, jdsnap, lexicon, resumes, trackapi
from .finder import kinds, rank, signals
from .resume import ats

MAX_REQUIREMENTS = 5
MAX_ACHIEVEMENTS = 3
MIN_ACHIEVEMENTS = 2
LINE_MIN, LINE_MAX = 40, 320
EMAIL_WORDS = 120                 # the ask: one cold email under 120 words
NOTE_CHARS = 300                  # one LinkedIn connection note of at most 300 characters
MAX_HOOK = 2                      # JD skills named in the hook
MAX_NOTE_PROOFS = 5               # resume lines tried for the note's proof line, best match first
MIN_CLAUSE_WORDS = 8              # a proof line cut at a comma keeps at least this many words
PLACEHOLDER = "[first name]"      # a kind nobody was found for: the person types the name they find on LinkedIn
_BULLET = re.compile(r"^[\s\-•*·–]+")
_SKILL_LINE = re.compile(r"^[^.:]{2,40}:\s")         # "Languages & Data: Python, SQL, ..." (a skills row)
_JOB_HEAD = re.compile(r"^###\s+(?P<company>[^:]+?)(?:,\s*[A-Z][\w .]*)?:\s")   # "### Deloitte, USA: AI Engineer (...)"
_SENTENCE = re.compile(r"(?<=[a-z0-9%)])\.\s+(?=[A-Z])")
_DASHES = str.maketrans({"—": ", ", "–": "-"})
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


def _short_company(company: str) -> str:
    """'Deloitte, USA' -> 'Deloitte'."""
    return re.sub(r",\s*[A-Z][\w .]*$", "", (company or "").strip())


def _fact_bank_lines(text: str) -> list[tuple[str, str]]:
    """(company, bullet) from fact_bank.md Part 1 (the master resume verbatim); Part 2 never gives proof lines."""
    out, company = [], None
    for line in text.split("## Part 2")[0].splitlines():
        head = _JOB_HEAD.match(line)
        if head:
            company = head.group("company").strip()
        elif line.startswith("### "):
            company = None
        elif company and line.startswith("- "):
            out.append((company, line[2:].strip()))
    return out


def proof_lines(pid: str) -> list[tuple[str, str]]:
    """The person's own result lines with their employer, from their fact bank, else their current resume."""
    folder = config.PROFILES / pid
    bank = folder / "fact_bank.md"
    if bank.exists():
        found = _fact_bank_lines(bank.read_text())
        if found:
            return found
    try:
        baseline = json.loads((folder / "baseline.json").read_text())
    except (OSError, json.JSONDecodeError):
        return []
    return [(_short_company(job.get("company") or ""), b) for job in baseline.get("experience") or []
            for b in job.get("bullets") or []]


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


def _jd_terms(jd: str) -> list[ats.Term]:
    return sorted(ats.extract(jd), key=lambda t: (-t.need, -t.count, t.first))


def proofs(jd: str, lines: list[tuple[str, str]]) -> list[dict]:
    """Resume lines ranked by the JD skills they also name (required first; a number breaks ties): each with the
    hook skills it supports, in the JD's own wording."""
    terms = _jd_terms(jd)
    ranked = []
    for company, text in lines:
        named = {name for _, _, name in lexicon.spans(text)}
        hits = [t for t in terms if t.name in named]
        score = sum(2 if t.need else 1 for t in hits) * 2 + bool(re.search(r"\d", text))
        ranked.append((score, {"company": company, "text": text,
                               "hook": [(t.forms[0] if t.forms else t.name) for t in hits][:MAX_HOOK]}))
    ranked.sort(key=lambda x: -x[0])
    return [p for _, p in ranked]


def _joined(words: list[str]) -> str:
    return ", ".join(words[:-1]) + f" and {words[-1]}" if len(words) > 1 else "".join(words)


def _first_person(company: str, sentence: str) -> str:
    """'Developed a Bedrock SQL agent ...' -> 'At Deloitte, I developed a Bedrock SQL agent ...'."""
    sentence = sentence.strip().rstrip(".")
    verb, _, rest = sentence.partition(" ")
    lead = verb.lower() if verb[:1].isupper() and verb[1:].islower() else verb
    return f"At {company}, I {lead} {rest}.".replace(" .", ".") if company else f"I {lead} {rest}."


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE.split(text.strip()) if s.strip()]


def _plain(text: str) -> str:
    return re.sub(r"[ \t]+", " ", text.translate(_DASHES)).replace(" ,", ",")


def _first(name: str | None) -> str:
    parts = (name or "").split()
    return parts[0] if parts else "there"


def _clauses(sentence: str) -> list[str]:
    """The sentence, then its shorter leading parts cut at a comma (each still the resume's own words), longest first."""
    sentence = sentence.strip().rstrip(".")
    cuts = [m.start() for m in re.finditer(r",\s", sentence)]
    parts = [sentence] + [sentence[:i] for i in reversed(cuts)]
    return [x for x in parts if len(x.split()) >= MIN_CLAUSE_WORDS]


def _hook(proof: dict) -> str:
    return f" The role asks for {_joined(proof['hook'])}." if proof["hook"] else ""


def note(me: dict, first: str, role: str, company: str, kind: str, ranked: list[dict]) -> tuple[str, dict | None]:
    """(the LinkedIn note, the resume line it uses): greeting + applied + hook + proof + ask, the first version that
    fits NOTE_CHARS (the top resume lines and their shorter leading clauses are tried before the hook or the proof is
    left out)."""
    who = (me.get("name") or "").split(" ")[0]
    applied = f"Hi {first}, I applied for {role} at {company}."
    tail = f" {kinds.ASKS[kind]} {who}"
    full, no_hook = [], []
    hooked = [p for p in ranked if p["hook"]] or ranked          # a line naming a JD skill is the proof first
    for proof in hooked[:MAX_NOTE_PROOFS]:
        for clause in _clauses(_sentences(proof["text"])[0]):
            line = " " + _first_person(proof["company"], clause)
            full.append((applied + _hook(proof) + line + tail, proof))
            no_hook.append((applied + line + tail, proof))
    hook_only = [(applied + _hook(p) + tail, None) for p in ranked[:1] if p["hook"]]
    texts = [(_plain(t).strip(), p) for t, p in full + no_hook + hook_only + [(applied + tail, None)]]
    return next(((t, p) for t, p in texts if len(t) <= NOTE_CHARS), (texts[-1][0][:NOTE_CHARS], None))


def email(me: dict, first: str, role: str, company: str, kind: str, proof: dict | None, applied_on: str | None) -> dict:
    """{subject, email}: the fullest version under EMAIL_WORDS."""
    name, link = me.get("name") or "", me.get("linkedin")
    when = f" on {applied_on}" if applied_on else ""
    sign = f"\n\nThanks,\n{name}" + (f"\n{link}" if link else "")
    head = f"Hi {first},\n\nI applied for the {role} role at {company}{when}."
    options = []
    if proof:
        hook = _hook(proof)
        sentences = _sentences(proof["text"])
        lead = _first_person(proof["company"], sentences[0])
        options += [f"{head}{hook} {' '.join([lead] + sentences[1:])}\n\n{kinds.ASKS[kind]}{sign}",
                    f"{head}{hook} {lead}\n\n{kinds.ASKS[kind]}{sign}"]
    options.append(f"{head}\n\n{kinds.ASKS[kind]}{sign}")
    texts = [_plain(t) for t in options]
    body = next((t for t in texts if len(t.split()) < EMAIL_WORDS), texts[-1])
    return {"subject": _plain(f"{role} at {company}: {name}".strip(" :")), "email": body}


def _prompt(me: dict, contact: dict, kind: str, row: dict, team: str | None, reqs: list[str], picks: list[dict],
            label: str) -> str:
    who = contact.get("name") or "a person I will find on LinkedIn"
    lines = [
        "I applied for a job and want to reach out to someone there. Write two short messages that I will send "
        "myself.", "",
        f"Contact: {who}, {contact.get('role') or kinds.LABELS[kind].lower()} at {row['company']} "
        f"(the {kinds.LABELS[kind].lower()}).",
        f"Job: {row['role_title']} at {row['company']}" + (f", {team} team" if team else "") +
        (f". I applied on {row['date_applied']}." if row.get("date_applied") else "."),
        "", "Key requirements from the job description:", *[f"- {r}" for r in reqs], "",
        f"The resume I used: {label}. My real results that match this job (use only these facts and numbers):",
        *[f"- At {p['company']}: {p['text']}" for p in picks], "",
        "Write:",
        "1. One LinkedIn connection note under 300 characters: a greeting, \"I applied for <role> at <company>\", one "
        "hook from the job description, one proof line from my results above, and this ask: " + kinds.ASKS[kind],
        "2. One cold email: a subject line, then a body under 120 words with the same parts.",
        "Rules: specific to this person and this role; no flattery; no em dashes; plain, direct words; never a fact "
        f"or number that is not above; sign as {me.get('name') or 'me'}" +
        (f" and add my LinkedIn {me['linkedin']}" if me.get("linkedin") else "") + "."]
    return "\n".join(lines)


def compose(pid: str, row: dict, jd: str, kind: str, contact: dict | None = None) -> dict:
    """The drafts for one kind of person (and that person, when known): {kind, note, subject, email, prompt, proof}."""
    me = config.profile(pid)
    contact = contact or {}
    first = _first(contact.get("name")) if (contact.get("name") or "").strip() else (
        "there" if contact else PLACEHOLDER)
    ranked = proofs(jd, proof_lines(pid))
    text, proof = note(me, first, row["role_title"], row["company"], kind, ranked)
    proof = proof or (ranked[0] if ranked else None)              # the email names the same result as the note
    mail = email(me, first, row["role_title"], row["company"], kind, proof, row.get("date_applied"))
    resume = _resume(pid, row.get("resume_used"))
    return {"kind": kind, "label": kinds.LABELS[kind], "note": text, **mail,
            "prompt": _prompt(me, contact, kind, row, signals.team_of(jd), requirements(jd), ranked[:MAX_ACHIEVEMENTS],
                              resume.label if resume else "my resume"),
            "proof": proof}


def kind_of_contact(contact: dict) -> str:
    """The contact's saved kind, else read from their title (a company leader is asked as a manager)."""
    title = contact.get("role") or ""
    return contact.get("kind") or kinds.kind_of(title) or ("manager" if rank.LEADER.search(title) else "recruiter")


def draft(conn, pid: str, app_id: int, contact_id: int) -> dict:
    """{'prompt', 'template': {subject, email, linkedin}, 'kind', 'requirements', 'achievements', 'resume', 'contact'}."""
    row = trackapi.get(conn, pid, app_id)
    contact = _contact(row, contact_id)
    jd = (jdsnap.get(conn, app_id) or {}).get("text") or row.get("jd") or ""
    kind = kind_of_contact(contact)
    out = compose(pid, row, jd, kind, contact)
    resume = _resume(pid, row.get("resume_used"))
    return {"prompt": out["prompt"], "kind": kind, "label": out["label"],
            "template": {"subject": out["subject"], "email": out["email"], "linkedin": out["note"]},
            "requirements": requirements(jd),
            "achievements": [p["text"] for p in proofs(jd, proof_lines(pid))][:MAX_ACHIEVEMENTS],
            "resume": {"id": resume.id, "label": resume.label} if resume else None,
            "contact": {k: contact.get(k) for k in ("id", "name", "role", "email", "linkedin", "email_sent",
                                                     "linkedin_sent", "kind", "source", "found_at")}}


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
