"""API keys and other secrets in <home>/config/secrets.yaml, written 0600. Never in the repo, the DB or a log.

Flat keys for the app-wide values (anthropic_api_key, apollo_api_key, hunter_api_key, telegram_token,
telegram_chat, resume_tailor_token). A person's own Apollo or Hunter key can still sit under the provider
(`apollo: {alex_api_key: ...}`) and wins over the shared one for that profile.

Settings that are not secret (the resume tailor URL, the Claude model) live in settings.yaml: save_setting().
"""
import os
import threading

import yaml

from . import atomic, config

KEYS = ("anthropic_api_key", "apollo_api_key", "hunter_api_key", "telegram_token", "telegram_chat",
        "resume_tailor_token")
SETTINGS = ("resume_tailor_url", "anthropic_model", "default_profile")
MAX_VALUE = 500
PRIVATE = 0o600
_LOCK = threading.Lock()


def path():
    return config.CONFIG / "secrets.yaml"


def load() -> dict:
    found = path()
    if found.exists() and found.stat().st_mode & 0o077:
        found.chmod(PRIVATE)
    data = config._read_yaml(found, {})
    return data if isinstance(data, dict) else {}


def _write_yaml(target, data: dict) -> None:
    atomic.write_text(target, yaml.safe_dump(data, sort_keys=True, allow_unicode=True), PRIVATE)


def get(name: str) -> str | None:
    """A flat secret (or its environment override, e.g. ANTHROPIC_API_KEY), or None."""
    env = {"anthropic_api_key": "ANTHROPIC_API_KEY", "telegram_token": "JOB_HUNT_AGENT_TELEGRAM_TOKEN",
           "telegram_chat": "JOB_HUNT_AGENT_TELEGRAM_CHAT"}.get(name)
    if env and os.environ.get(env):
        return os.environ[env].strip()
    value = load().get(name)
    text = str(value).strip() if value is not None else ""
    return text or None


def put(name: str, value: str | None) -> None:
    """Save one flat secret; an empty value removes it."""
    if name not in KEYS:
        raise ValueError(f"unknown key {name!r}")
    text = " ".join(str(value or "").split())
    if len(text) > MAX_VALUE:
        raise ValueError("that value is too long to be a key")
    with _LOCK:
        data = load()
        if text:
            data[name] = text
        else:
            data.pop(name, None)
        _write_yaml(path(), data)


def masked(value: str | None) -> str | None:
    """What the UI shows for a saved key: never the key itself."""
    if not value:
        return None
    return f"saved, ends in {value[-4:]}" if len(value) > 8 else "saved"


def save_setting(name: str, value) -> None:
    """One non-secret setting in settings.yaml (empty removes it, so the default applies again)."""
    if name not in SETTINGS:
        raise ValueError(f"unknown setting {name!r}")
    target = config.CONFIG / "settings.yaml"
    with _LOCK:
        data = config._read_yaml(target, {}) or {}
        text = str(value or "").strip()
        if text:
            data[name] = text
        else:
            data.pop(name, None)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(yaml.safe_dump(data, sort_keys=True, allow_unicode=True))
