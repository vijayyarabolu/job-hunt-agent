"""Claude for the emails the rules cannot settle: headless `claude -p` (Sonnet, medium effort: GOAL20),
subject + the 300-character snippet only, up to BATCH emails per call, at most DAILY_CAP calls a day (all people
together). No tools, no MCP, no settings, no session kept. Any failure (no CLI, limit, bad JSON) answers None: the
emails wait in "Needs you" with the rules' guess instead.
"""
import json
import logging
import os
import shutil
import subprocess
from datetime import date
from pathlib import Path

from .. import claude_api, config, db
from ..google import state
from . import KINDS

log = logging.getLogger(__name__)
MODEL = "claude-sonnet-5-5"                 # every Claude call in both apps
DAILY_CAP = 50
BATCH = 20
TIMEOUT_S = 180
COUNT_KEY = "mail.llm.{}"
_STRIP_ENV = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_CHILD_SESSION",
              "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_PID", "CLAUDE_CODE_EXECPATH",
              "CLAUDE_CODE_SESSION_ATTENDED")
SCHEMA = {"type": "object", "required": ["results"], "properties": {"results": {"type": "array", "items": {
    "type": "object", "required": ["i", "kind"],
    "properties": {"i": {"type": "integer"}, "kind": {"type": "string", "enum": list(KINDS)}}}}}}
PROMPT = """You sort emails a job seeker received about job applications. For each email (subject + the start of its
text) answer one kind:
- rejected: the company will not move forward with this application (position filled, other candidates, ...)
- screen: an assessment or coding test to take, or a first recruiter / phone screen call
- interview: an invitation to interview, scheduling an interview round, onsite, panel, next round
- offer: a job offer
- received: only confirms the application was received / submitted, no decision yet
- other: anything else (newsletters, job alerts, marketing, receipts, a personal note, not about an application)
Answer JSON {"results": [{"i": <index>, "kind": "<kind>"}]} with one entry per email.

Emails:
"""


def claude_bin() -> str:
    """GOAL33: JOB_HUNT_AGENT_CLAUDE_BIN overrides it (the test package points it at /usr/bin/false: no test calls Claude)."""
    return os.environ.get("JOB_HUNT_AGENT_CLAUDE_BIN") or config.settings().get("claude_bin") or shutil.which("claude") or str(Path.home() / ".local/bin/claude")


def _env() -> dict:
    return {k: v for k, v in os.environ.items() if k not in _STRIP_ENV}


def run_claude(prompt: str) -> dict | None:
    """The API with the saved Anthropic key when there is one, else the Claude Code CLI; the parsed JSON or None."""
    if claude_api.configured():
        try:
            out, _ = claude_api.ask(prompt, schema=SCHEMA, effort="low")
        except claude_api.ClaudeError as err:
            log.warning("mail classify: the Claude API failed: %s", err)
            return None
        return out if isinstance(out, dict) else None
    return run_cli(prompt)


def run_cli(prompt: str) -> dict | None:
    """One headless call; the parsed JSON object, or None."""
    cmd = [claude_bin(), "-p", "--output-format", "json", "--json-schema", json.dumps(SCHEMA), "--model", MODEL, "--effort", "medium",
           "--tools", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}', "--setting-sources", "",
           "--no-session-persistence", "--max-turns", "2"]
    try:
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=TIMEOUT_S, env=_env())
    except (OSError, subprocess.TimeoutExpired) as err:
        log.warning("mail classify: claude -p failed: %s", err)
        return None
    try:
        raw = json.loads((proc.stdout or "").strip() or "{}")
    except json.JSONDecodeError:
        log.warning("mail classify: claude -p gave no JSON (exit %s): %s", proc.returncode, (proc.stderr or "")[-300:])
        return None
    out = raw.get("structured_output")
    if out is None and isinstance(raw.get("result"), str):
        try:
            out = json.loads(raw["result"])
        except json.JSONDecodeError:
            out = None
    return out if isinstance(out, dict) else None


def used_today(conn, today: date) -> int:
    return int(state.get(conn, COUNT_KEY.format(today.isoformat())) or 0)


def classify_batch(conn, items: list[tuple[str, str]], today: date, runner=run_claude) -> list[str | None]:
    """A kind (or None) per (subject, snippet), in order. Calls stop at the daily cap."""
    answers: list[str | None] = [None] * len(items)
    for start in range(0, len(items), BATCH):
        if used_today(conn, today) >= DAILY_CAP:
            log.info("mail classify: the %d Claude calls of %s are used up", DAILY_CAP, today)
            break
        with db.tx(conn):
            state.put(conn, COUNT_KEY.format(today.isoformat()), used_today(conn, today) + 1)
        chunk = items[start:start + BATCH]
        lines = "\n".join(json.dumps({"i": i, "subject": s[:200], "text": t[:300]}) for i, (s, t) in enumerate(chunk))
        result = runner(PROMPT + lines) or {}
        for entry in result.get("results") or []:
            i, kind = entry.get("i"), entry.get("kind")
            if isinstance(i, int) and 0 <= i < len(chunk) and kind in KINDS:
                answers[start + i] = kind
    return answers
