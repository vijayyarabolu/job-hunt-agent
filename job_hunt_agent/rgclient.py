"""The resume-tailoring backend behind the Tailored resumes tab. Two kinds, chosen in Settings > Setup:

- a separate service at `resume_tailor_url` (settings.yaml), called over HTTP with the token saved as
  `resume_tailor_token`. The browser never sees that token: every resume call goes through this app's /api/resumes/*.
  When the service is down, calls raise Offline and everything else keeps working;
- no URL: the built-in tailor (tailor.py), answered in-process with the same API and the user's Anthropic key.
"""
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import config, secrets_store

TIMEOUT_S = 4
FILE_TIMEOUT_S = 15
OFFLINE = "The resume service is offline"


class Offline(Exception):
    """The resume service is not reachable."""


def _base() -> str:
    return str(config.settings().get("resume_tailor_url") or "").rstrip("/")


def external() -> bool:
    """True when a separate resume service is configured (else the built-in tailor answers)."""
    return bool(_base())


def address() -> str:
    """Which resumegen this is (its URL), so what job-hunt-agent keeps from one is never shown for another."""
    return _base()


_HOST = re.compile(r"[A-Za-z0-9.-]{1,253}")


def browser_url(host_header: str | None, profile: str | None) -> str:
    """"Open the resume service": the service on the host name the browser used for this app, its own port, the
    person. No token (the browser has the service's own cookie). Without a service: this app's Tailored resumes."""
    if not external():
        return "/#resumes"
    base = urllib.parse.urlsplit(_base())
    name = (host_header or "").rsplit(":", 1)[0] if not (host_header or "").startswith("[") else ""
    host = name if _HOST.fullmatch(name) else base.hostname
    query = urllib.parse.urlencode({"profile": profile} if profile else {})
    return f"{base.scheme}://{host}:{base.port or 80}/" + (f"?{query}" if query else "")


def _token() -> str:
    return secrets_store.get("resume_tailor_token") or ""


def _error_text(err: urllib.error.HTTPError) -> str:
    try:
        return str(json.loads(err.read() or b"{}").get("error") or err.reason)
    except (ValueError, AttributeError):
        return str(err.reason)


def request(method: str, path: str, body: dict | None = None, timeout: float = TIMEOUT_S,
            raw: bool = False) -> dict | tuple[bytes, str]:
    """One call to the resume backend. JSON answers come back as a dict; `raw` returns (bytes, content type) for
    files. Offline when a service cannot be reached; ValueError (its own message) for a 4xx; KeyError for a 404."""
    if not external():
        from . import tailor
        return tailor.handle(method, path, body, raw)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{_base()}{path}", data=data, method=method,
                                 headers={"Content-Type": "application/json",
                                          **({"Authorization": f"Bearer {_token()}"} if _token() else {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read()
            if raw:
                return payload, resp.headers.get("Content-Type", "application/octet-stream")
            return json.loads(payload or b"{}")
    except urllib.error.HTTPError as err:
        if err.code == 404:
            raise KeyError(_error_text(err)) from None
        if 400 <= err.code < 500:
            raise ValueError(f"Resume app: {_error_text(err)}") from None
        raise Offline(f"{OFFLINE} (it answered {err.code}: {_error_text(err)})") from None
    except (urllib.error.URLError, TimeoutError, ConnectionError) as err:
        raise Offline(OFFLINE) from err
    except json.JSONDecodeError as err:                         # not JSON: something else is on that port
        raise Offline(f"{OFFLINE} (unexpected answer)") from err
