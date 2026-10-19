"""Claude through the user's own Anthropic API key (Settings > Setup > Anthropic). Anthropic has no OAuth sign-in for
third-party apps, so a key is the only way; it stays in <home>/config/secrets.yaml and goes only to api.anthropic.com.

Three things use it: reading job emails (mail/claude.py), the built-in resume tailor (tailor.py) and outreach drafts
(outreach.py). Each degrades to its no-Claude path when no key is saved. When a person has the Claude Code CLI
signed in instead, the email reader can still use `claude -p` (mail/claude.py keeps that path).

The `anthropic` package is imported lazily so the rest of the app (and the offline tests) never need it.
"""
import json
import logging

from . import config, secrets_store

log = logging.getLogger(__name__)
DEFAULT_MODEL = "claude-sonnet-5-5"          # $2 / $10 per million tokens (claude.com/pricing, October 2026)
KEY_PAGE = "https://platform.claude.com/settings/keys"
TIMEOUT_S = 120


class ClaudeError(Exception):
    """A call failed; `kind` is "login" (no key or a refused key), "limit" (rate or spend limit) or "error"."""

    def __init__(self, message: str, kind: str = "error"):
        super().__init__(message)
        self.kind = kind


def configured() -> bool:
    return bool(secrets_store.get("anthropic_api_key"))


def model() -> str:
    return str(config.settings().get("anthropic_model") or DEFAULT_MODEL)


def _client():
    key = secrets_store.get("anthropic_api_key")
    if not key:
        raise ClaudeError("no Anthropic API key yet: add one in Settings > Setup", "login")
    try:
        import anthropic
    except ImportError as err:
        raise ClaudeError("the anthropic package is missing: pip install anthropic", "error") from err
    return anthropic, anthropic.Anthropic(api_key=key, timeout=TIMEOUT_S, max_retries=2)


def _kind(anthropic, err: Exception) -> str:
    if isinstance(err, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return "login"
    if isinstance(err, anthropic.RateLimitError):
        return "limit"
    return "error"


def strict(schema: dict) -> dict:
    """The schema as structured outputs takes it: every object closed (additionalProperties false) and a list of
    types written as anyOf."""
    if not isinstance(schema, dict):
        return schema
    out = {k: strict(v) if isinstance(v, dict) else v for k, v in schema.items()}
    if isinstance(out.get("type"), list):
        kinds = out.pop("type")
        out["anyOf"] = [{"type": k} for k in kinds]
    if "properties" in out:
        out["properties"] = {k: strict(v) for k, v in out["properties"].items()}
    if out.get("type") == "object" or "properties" in out:
        out["additionalProperties"] = False
    if isinstance(out.get("items"), dict):
        out["items"] = strict(out["items"])
    return out


def ask(prompt: str, system: str = "", schema: dict | None = None, max_tokens: int = 16000,
        effort: str = "medium") -> tuple[object, dict]:
    """One Messages API call. Returns (the parsed JSON object when `schema` is given, else the text; token usage).
    Raises ClaudeError."""
    anthropic, client = _client()
    params = {"model": model(), "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}],
              "output_config": {"effort": effort}}
    if system:
        params["system"] = system
    if schema:
        params["output_config"]["format"] = {"type": "json_schema", "schema": strict(schema)}
    try:
        response = client.messages.create(**params)
    except anthropic.APIStatusError as err:
        raise ClaudeError(f"Anthropic answered {err.status_code}: {getattr(err, 'message', err)}",
                          _kind(anthropic, err)) from err
    except anthropic.APIConnectionError as err:
        raise ClaudeError("could not reach api.anthropic.com", "error") from err
    usage = {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens}
    if response.stop_reason == "refusal":
        raise ClaudeError("Claude declined this request", "error")
    if response.stop_reason == "max_tokens":
        raise ClaudeError("the answer was cut off (max_tokens)", "error")
    text = "".join(block.text for block in response.content if block.type == "text")
    if schema is None:
        return text, usage
    try:
        return json.loads(text), usage
    except json.JSONDecodeError as err:
        raise ClaudeError("Claude's answer was not the JSON asked for", "error") from err


def test() -> tuple[bool, str]:
    """Settings' Test button: lists one model, which checks the key without spending tokens."""
    try:
        anthropic, client = _client()
    except ClaudeError as err:
        return False, str(err)
    try:
        client.models.list(limit=1)
    except anthropic.AuthenticationError:
        return False, "Anthropic refused this key (401): check it was copied whole"
    except anthropic.APIStatusError as err:
        return False, f"Anthropic answered {err.status_code}"
    except anthropic.APIConnectionError:
        return False, "could not reach api.anthropic.com"
    return True, f"key works; the app uses {model()}"
