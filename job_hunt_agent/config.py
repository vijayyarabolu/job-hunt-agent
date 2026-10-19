"""Load and validate the shipped registry (config/ in the repo) and the user's own files.

Everything personal lives outside the repo, in the app home: ~/.job-hunt-agent by default, or JOB_HUNT_AGENT_HOME.

    <home>/config/   settings.yaml, secrets.yaml (API keys, 0600), google_client.json, local.yaml
    <home>/data/     the SQLite database, tokens, logs, backups
    <home>/profiles/ one folder per person: profile.yaml, baseline.json, resumes/

A file in <home>/config/ with the same name as a shipped registry file (companies.yaml, lexicon.yaml, ...) wins over
the shipped one, so `add` and `discover` never have to edit the checkout.
"""
import os
import re
from pathlib import Path

import yaml

from .ats import ADAPTERS, KEY_FIELDS, board_key
from .titles import FAMILIES

REPO = Path(__file__).resolve().parent.parent
SHIPPED = REPO / "config"
HOME_ENV = "JOB_HUNT_AGENT_HOME"
ROOT = Path(os.environ.get(HOME_ENV) or Path.home() / ".job-hunt-agent").expanduser()
CONFIG = ROOT / "config"
DATA = ROOT / "data"
PROFILES = ROOT / "profiles"
APPLICATIONS = ROOT / "applications"
DB_PATH = DATA / "job_hunt_agent.db"


def shipped(name: str) -> Path:
    """A registry file: the user's copy in <home>/config when there is one, else the repo's."""
    own = CONFIG / name
    return own if own.exists() else SHIPPED / name


DEFAULT_SETTINGS = {
    # Workday throttles per data center, so keep this short: each term costs up to 5 POSTs per tenant.
    "workday_search_terms": ["machine learning", "AI engineer", "generative AI", "LLM", "deep learning",
                             "applied scientist", "data scientist", "data engineer", "computer vision"],
    "fetch_interval_min": 30,
    "notify_threshold": 70,
    "fetch_workers": 16,
    "server_port": 8765,
}
MAX_DEALBREAKER_CHARS = 200
# GOAL §5: every profile filters these out unless its profile.yaml says `dealbreakers_default: false`.
DEFAULT_DEALBREAKERS = (r"\b(?:security|active|secret|top[ -]?secret|ts)\s+clearance\b", r"\bTS\s*/\s*SCI\b",
                        r"\b(?:active\s+)?top[ -]secret\b", r"\bpolygraph\b")
_NESTED_QUANTIFIER = re.compile(r"\([^)]*[+*}][^)]*\)\s*[+*{]")      # (a+)+ style ReDoS on the LAN server
PROFILE_FIELDS = ("name", "families")   # years/contact optional (partner before a baseline)


class ConfigError(ValueError):
    pass


def _read_yaml(path: Path, default):
    if not path.exists():
        return default
    try:
        return yaml.safe_load(path.read_text()) or default
    except yaml.YAMLError as err:
        raise ConfigError(f"{path}: invalid YAML: {err}") from err


def settings() -> dict:
    return {**DEFAULT_SETTINGS, **_read_yaml(CONFIG / "settings.yaml", {})}


def validate_company(entry: dict) -> dict:
    """Return a board dict {board, ats, key, company, tier} or raise ConfigError."""
    if not isinstance(entry, dict) or not entry.get("name") or not entry.get("token"):
        raise ConfigError(f"company entry needs name and token: {entry!r}")
    if entry.get("ats") not in ADAPTERS:
        raise ConfigError(f"{entry['name']}: unknown ats {entry.get('ats')!r}")
    fields = KEY_FIELDS.get(entry["ats"], ())
    if not all(entry.get(f) for f in fields):
        raise ConfigError(f"{entry['name']}: {entry['ats']} entries need {' and '.join(fields)}")
    key = board_key(entry)
    return {"board": f"{entry['ats']}:{key}", "ats": entry["ats"], "key": key,
            "company": entry["name"], "tier": entry.get("tier"), "review": bool(entry.get("review"))}


def companies_raw() -> list[dict]:
    return _read_yaml(shipped("companies.yaml"), {}).get("companies", [])


def companies() -> list[dict]:
    boards = [validate_company(e) for e in companies_raw()]
    dupes = {b["board"] for b in boards if sum(x["board"] == b["board"] for x in boards) > 1}
    if dupes:
        raise ConfigError(f"duplicate boards in companies.yaml: {sorted(dupes)}")
    return boards


def save_companies(entries: list[dict]) -> None:
    """Rewrite companies.yaml sorted by name (used by `add` and `discover`)."""
    ordered = sorted(entries, key=lambda e: (e["name"].lower(), e["ats"]))
    for entry in ordered:
        validate_company(entry)
    CONFIG.mkdir(parents=True, exist_ok=True)
    header = "# Company registry: {name, ats, token, wd?, site?, tier?}. Managed by `job-hunt-agent add|discover`.\n"
    body = yaml.safe_dump({"companies": ordered}, sort_keys=False, allow_unicode=True, width=120)
    (CONFIG / "companies.yaml").write_text(header + body)


def profile_ids() -> list[str]:
    return sorted(p.parent.name for p in PROFILES.glob("*/profile.yaml"))


def profile(pid: str) -> dict:
    data = _read_yaml(PROFILES / pid / "profile.yaml", None)
    if data is None:
        raise ConfigError(f"profile {pid!r} not found at {PROFILES / pid / 'profile.yaml'}")
    missing = [f for f in PROFILE_FIELDS if not data.get(f)]
    if missing:
        raise ConfigError(f"profile {pid}: missing {missing}")
    unknown = [f for f in data["families"] if f not in FAMILIES]
    if unknown:
        raise ConfigError(f"profile {pid}: unknown title families {unknown}; choose from {sorted(FAMILIES)}")
    own = list(data.get("dealbreakers") or [])
    defaults = list(DEFAULT_DEALBREAKERS) if data.get("dealbreakers_default", True) else []
    data = {**data, "dealbreakers": own + [d for d in defaults if d not in own]}
    for pattern in data["dealbreakers"]:
        if len(pattern) > MAX_DEALBREAKER_CHARS or _NESTED_QUANTIFIER.search(pattern):
            raise ConfigError(f"profile {pid}: dealbreaker {pattern!r} is too long or has nested quantifiers")
        try:
            re.compile(pattern)
        except re.error as err:
            raise ConfigError(f"profile {pid}: bad dealbreaker regex {pattern!r}: {err}") from err
    return {"id": pid, "search_terms": [], "dealbreakers": [], **data}


def default_profile() -> str | None:
    """settings.yaml `default_profile` when that profile exists, else the first profile with a resume baseline,
    else the first profile folder, else None."""
    ids = profile_ids()
    wanted = settings().get("default_profile")
    if wanted in ids:
        return wanted
    ranked = sorted(ids, key=lambda pid: not (PROFILES / pid / "baseline.json").exists())
    return ranked[0] if ranked else None


def profiles() -> list[dict]:
    return [profile(pid) for pid in profile_ids()]


DEMO_ENV = "JOB_HUNT_AGENT_DEMO"


def demo_mode() -> bool:
    """`job-hunt-agent demo`: fixture jobs and fake backends, nothing sent anywhere."""
    return os.environ.get(DEMO_ENV) == "1"


def set_home(path) -> None:
    """Point the app at another home (the demo's temp folder), after import."""
    global ROOT, CONFIG, DATA, PROFILES, APPLICATIONS, DB_PATH
    ROOT = Path(path).expanduser()
    os.environ[HOME_ENV] = str(ROOT)
    CONFIG, DATA, PROFILES, APPLICATIONS = ROOT / "config", ROOT / "data", ROOT / "profiles", ROOT / "applications"
    DB_PATH = DATA / "job_hunt_agent.db"
