"""GOAL35 §4.2: the "state job banks" source (CareerOneStop's Jobs API, which carries the National Labor Exchange: every
state's job bank). Off by default: CareerOneStop gives a key only after the NLx Research Hub approves a data request
, and no state site allows a scraper. The switch lives in data/sources.json (app-wide, not in git);
the key in the environment (CAREERONESTOP_TOKEN, CAREERONESTOP_USER_ID) or config/secrets.yaml
`careeronestop: {token: ..., user_id: ...}` (git-ignored). The key is never sent to the page."""
import json
import logging
import os

from . import config
from .profile_settings import _write

log = logging.getLogger(__name__)

SWITCH_KEY = "state_job_banks"
DEFAULT_ON = False               # §4.1: CareerOneStop needs an approved data request first
TOKEN_ENV, USER_ENV = "CAREERONESTOP_TOKEN", "CAREERONESTOP_USER_ID"
REQUEST_URL = "https://nlxresearchhub.org/request-nlx-data"


def _sources_path():
    return config.DATA / "sources.json"


def _saved() -> dict:
    try:
        data = json.loads(_sources_path().read_text())
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as err:          # a hand edit gone wrong is the default, never a crash
        log.warning("data/sources.json unreadable (%s): state job banks stay off", err)
        return {}


def key() -> tuple[str, str] | None:
    """(token, user id) from the environment, else config/secrets.yaml; None when either is missing."""
    secret = (config._read_yaml(config.CONFIG / "secrets.yaml", {}) or {}).get("careeronestop") or {}
    token = (os.environ.get(TOKEN_ENV) or str(secret.get("token") or "")).strip()
    user = (os.environ.get(USER_ENV) or str(secret.get("user_id") or "")).strip()
    return (token, user) if token and user else None


def switched_on() -> bool:
    return bool(_saved().get(SWITCH_KEY, DEFAULT_ON))


def enabled() -> bool:
    """The fetcher adds the board only when the switch is on and a key is there."""
    return switched_on() and key() is not None


def status() -> dict:
    """What Settings shows: the switch, whether a key is set (never the key), and where to ask for one."""
    return {"on": switched_on(), "has_key": key() is not None, "request_url": REQUEST_URL,
            "key_env": [TOKEN_ENV, USER_ENV]}


def set_on(value) -> dict:
    if not isinstance(value, bool):
        raise ValueError("on must be true or false")
    path = _sources_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    _write(path, json.dumps({**_saved(), SWITCH_KEY: value}, indent=1) + "\n", 0o600)
    return status()
