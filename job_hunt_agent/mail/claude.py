"""Claude reads each job email in full, once a night, in as few calls as possible.

One call per chunk of CHUNK emails for one person: the Messages API with the Anthropic key saved in Settings, or,
without a key, a headless `claude -p` on that person's own Claude Code sign-in (settings.yaml `claude_config_dirs`
can point each profile at its own CLAUDE_CONFIG_DIR). Each email goes with its FULL plain text (HTML already stripped by
parse.py; quoted replies and footers trimmed here; at most BODY_MAX characters), sender, subject, date and the
person's open applications at the companies it may be about. A minimal call: a short system prompt in place of
Claude Code's own, no tools, no MCP, no settings, no skills, no session kept (about 600 input tokens of overhead
instead of about 2,750, measured 2026-10-08).

The full text only lives in this process's memory and in the prompt piped to Claude; nothing here is stored.
A usage limit or a missing sign-in stops the run: the emails not answered wait for the next run.
"""
import json
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .. import claude_api, config
from . import KINDS

log = logging.getLogger(__name__)
MODEL = "claude-sonnet-5-5"
CHUNK = 15
BODY_MAX = 8000
TIMEOUT_S = 600
CONFIDENCE = ("high", "medium", "low")
DEFAULT_CONFIG_DIRS: dict[str, str] = {}      # settings.yaml claude_config_dirs: {profile: ~/.claude-<name>}
_STRIP_ENV = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_CHILD_SESSION",
              "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_PID", "CLAUDE_CODE_EXECPATH",
              "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_CONFIG_DIR")
LIMIT = re.compile(r"hit your (?:usage |weekly |session )?limit|usage limit|rate limit|limit reached|limit exceeded|"
                   r"too many requests|\b429\b|out of (?:extra )?usage", re.I)
LOGIN = re.compile(r"not logged in|please run /login|please log ?in|invalid api key|authentication_error|"
                   r"oauth token (?:expired|revoked)|no credentials|unauthori[sz]ed", re.I)
SYSTEM = ("You read emails a job seeker received and decide what each one means for their job applications. "
          "Answer only with the JSON asked for.")
SCHEMA = {"type": "object", "required": ["results"], "properties": {"results": {"type": "array", "items": {
    "type": "object", "required": ["i", "kind", "application", "candidates", "confidence", "reason"],
    "properties": {"i": {"type": "integer"}, "kind": {"type": "string", "enum": list(KINDS)},
                   "application": {"type": ["integer", "null"]},
                   "candidates": {"type": "array", "items": {"type": "integer"}},
                   "confidence": {"type": "string", "enum": list(CONFIDENCE)},
                   "reason": {"type": "string"}}}}}}
PROMPT = """Each email below came to a job seeker. For each, decide its kind:
- rejected: the company will not move forward with this application (other candidates, position filled, not selected,
  no longer under consideration). A rejection sentence beats any thank-you or "we received your application" text.
- screen: an assessment / coding test to take, or a first call with a recruiter (a short call / chat / conversation to
  learn about you, a phone screen) being set up. A call with the hiring manager or the team is an interview.
- interview: a CONCRETE invitation to interview: asks for availability, gives a scheduling link, a date or time, or
  says "we'd like to invite you". A conditional sentence ("if you are selected", "should your qualifications match,
  we will contact you") is NOT an invitation: that email is "received".
- offer: a job offer.
- received: confirms the application was received / is under review, no decision yet (also account or status notes).
- other: not about a job application at all (job alerts, newsletters, marketing, receipts, codes, personal).
The kind is what the email says even when none of the listed applications fits it (then "application" is null).
Then which of the listed applications it is about: "application" = that application's id when exactly one fits
(company, title, requisition id, dates), else null; "candidates" = every listed id that could fit (empty if none).
"confidence": high only when the text says it plainly and the application is clear. "reason": one sentence that
quotes the deciding words of the email.
Answer {"results": [{"i", "kind", "application", "candidates", "confidence", "reason"}]}, one entry per email.

Emails (JSON, one per line):
"""
_QUOTED = re.compile(r"(?m)^\s*>.*$")
_WROTE = re.compile(r"(?im)^\s*(?:on .{5,200}wrote:|-{2,}\s*original message\s*-{2,}|from: .+\n\s*sent: .+)\s*$")
_FOOTER = re.compile(r"(?im)^.*(?:\bunsubscribe\b|you (?:are )?receiv(?:ed|ing) this (?:e-?mail|message)|"
                     r"this (?:e-?mail|message) was sent (?:to|by)|privacy policy|manage (?:your )?(?:email )?"
                     r"preferences|©|\(c\) \d{4}).*$")


@dataclass
class Result:
    answers: dict[int, dict] = field(default_factory=dict)        # email index -> Claude's answer
    stopped: str | None = None                                     # why the run stopped early (limit / login / error)
    usage: list[dict] = field(default_factory=list)                # tokens of each call
    answered: int = 0                                              # emails in the chunks Claude answered


def config_dir(pid: str) -> str | None:
    dirs = config.settings().get("claude_config_dirs") or DEFAULT_CONFIG_DIRS
    found = dirs.get(pid)
    return str(Path(found).expanduser()) if found else None


def claude_bin() -> str:
    return config.settings().get("claude_bin") or shutil.which("claude") or str(Path.home() / ".local/bin/claude")


def env_for(pid: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in _STRIP_ENV}
    if config_dir(pid):
        env["CLAUDE_CONFIG_DIR"] = config_dir(pid)
    return env


def trim(text: str) -> str:
    """The email's own words: quoted replies, the thread below "On ... wrote:" and footer lines dropped."""
    found = _WROTE.search(text or "")
    body = (text or "")[:found.start()] if found else (text or "")
    body = _FOOTER.sub("", _QUOTED.sub("", body))
    body = re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n\n", body)).strip()
    return body if len(body) <= BODY_MAX else body[:BODY_MAX - 1] + "…"


def item(i: int, mail, apps) -> str:
    return json.dumps({"i": i, "from": mail.sender, "subject": mail.subject, "date": mail.sent_at.date().isoformat(),
                       "text": trim(mail.text),
                       "applications": [{"id": a.id, "company": a.company, "title": a.role_title, "req_id": a.req_id,
                                         "applied_on": a.applied_on} for a in apps]}, ensure_ascii=False)


def run_claude(prompt: str, pid: str) -> tuple[dict | None, str, dict]:
    """The API with the saved Anthropic key when there is one, else the Claude Code CLI if it is installed."""
    if claude_api.configured():
        return run_api(prompt, pid)
    return run_cli(prompt, pid)


def run_api(prompt: str, pid: str) -> tuple[dict | None, str, dict]:
    """One Messages API call with the saved key: (parsed answer or None, "ok" | "limit" | "login" | "error", usage)."""
    try:
        out, usage = claude_api.ask(prompt, SYSTEM, SCHEMA, effort="medium")
    except claude_api.ClaudeError as err:
        log.warning("mail: the Claude API gave no answer for %s (%s): %s", pid, err.kind, err)
        return None, err.kind, {"error": str(err)[:200]}
    return (out, "ok", usage) if isinstance(out, dict) else (None, "error", usage)


def run_cli(prompt: str, pid: str) -> tuple[dict | None, str, dict]:
    """One minimal headless call: (parsed answer or None, "ok" | "limit" | "login" | "error", token usage)."""
    cmd = [claude_bin(), "-p", "--output-format", "json", "--json-schema", json.dumps(SCHEMA), "--model", MODEL, "--effort", "medium",
           "--system-prompt", SYSTEM, "--tools", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
           "--setting-sources", "", "--disable-slash-commands", "--no-session-persistence", "--max-turns", "2"]
    try:
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=TIMEOUT_S, env=env_for(pid))
    except (OSError, subprocess.TimeoutExpired) as err:
        return None, "error", {"error": str(err)[:200]}
    try:
        raw = json.loads((proc.stdout or "").strip() or "{}")
    except json.JSONDecodeError:
        raw = {}
    usage = {k: v for k, v in (raw.get("usage") or {}).items() if k.endswith("_tokens")}
    usage["cost_usd"] = raw.get("total_cost_usd")
    text = f"{raw.get('result') or ''} {proc.stdout if not raw else ''} {proc.stderr or ''}"
    out = raw.get("structured_output")
    if isinstance(out, dict) and not raw.get("is_error"):
        return out, "ok", usage
    kind = "login" if LOGIN.search(text) else "limit" if LIMIT.search(text) else "error"
    log.warning("mail: claude -p for %s gave no answer (%s, exit %s): %s", pid, kind, proc.returncode,
                (proc.stderr or "")[-300:])                    # stderr only: the result may quote an email
    return None, kind, usage


def _valid(entry: dict, allowed: set[int]) -> dict | None:
    if not isinstance(entry, dict) or entry.get("kind") not in KINDS:
        return None
    app = entry.get("application")
    candidates = [c for c in entry.get("candidates") or [] if isinstance(c, int) and c in allowed]
    return {"kind": entry["kind"], "application": app if isinstance(app, int) and app in allowed else None,
            "candidates": candidates, "confidence": entry.get("confidence") if entry.get("confidence") in CONFIDENCE
            else "low", "reason": str(entry.get("reason") or "")[:500]}


def read(pid: str, emails: list[tuple], runner=run_claude) -> Result:
    """emails: [(mail, candidate apps)]. Claude's answer per index, chunk by chunk, stopping at the first chunk that
    gets none (usage limit, sign-in, error): later chunks wait for the next run, so emails are answered in order."""
    result = Result()
    for start in range(0, len(emails), CHUNK):
        chunk = emails[start:start + CHUNK]
        prompt = PROMPT + "\n".join(item(i, mail, apps) for i, (mail, apps) in enumerate(chunk))
        out, kind, usage = runner(prompt, pid)
        result.usage.append({**usage, "emails": len(chunk), "prompt_chars": len(prompt)})
        if out is None:
            result.stopped = kind
            break
        result.answered = start + len(chunk)
        for entry in out.get("results") or []:
            i = entry.get("i") if isinstance(entry, dict) else None
            if isinstance(i, int) and 0 <= i < len(chunk):
                answer = _valid(entry, {a.id for a in chunk[i][1]})
                if answer:
                    result.answers[start + i] = answer
    return result
