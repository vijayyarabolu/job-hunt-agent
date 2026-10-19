"""Authenticated Google REST calls over urllib: retries with jittered backoff on 429/5xx and 403 rate limits
(Retry-After honoured), one forced token refresh on 401, a 10 s connect/read limit and a total deadline per call
(decision 006 condition 9: 10 s connect, 90 s total)."""
import gzip
import json
import random
import socket
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from urllib.parse import urlencode

from . import GoogleError, auth

CONNECT_TIMEOUT_S = 10
TOTAL_TIMEOUT_S = 90
MAX_TRIES = 5
BACKOFF_S = (1, 2, 4, 8)
RETRY_AFTER_CAP_S = 30
MAX_BYTES = 50 * 1024 * 1024
RETRYABLE = {429, 500, 502, 503, 504}
RATE_REASONS = ("rateLimitExceeded", "userRateLimitExceeded")
GZIP_AGENT = "job-hunt-agent (gzip)"   # Google sends gzip only when the User-Agent also says gzip
_sleep = time.sleep             # tests replace it


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes

    def json(self):
        return json.loads(self.body or b"{}")


def _reason(body: bytes) -> tuple[str, str]:
    """(reason, message) out of a Google error body."""
    try:
        error = json.loads(body or b"{}").get("error") or {}
    except (json.JSONDecodeError, AttributeError):
        return "", body[:200].decode(errors="replace")
    if not isinstance(error, dict):
        return str(error), ""
    reasons = [e.get("reason", "") for e in error.get("errors") or []]
    return (reasons[0] if reasons else error.get("status", "")), error.get("message", "")


def _retry_after(err: urllib.error.HTTPError) -> float | None:
    value = err.headers.get("Retry-After") if err.headers else None
    return min(float(value), RETRY_AFTER_CAP_S) if value and value.strip().isdigit() else None


def _pause(attempt: int, hint: float | None) -> float:
    return hint if hint is not None else BACKOFF_S[min(attempt, len(BACKOFF_S) - 1)] + random.random() * 0.5


def _short(method: str, url: str) -> str:
    return f"{method} {url.split('?')[0]}"


def _inflate(body: bytes, headers) -> bytes:
    """Google answers gzip when asked (Accept-Encoding + 'gzip' in the User-Agent): JD columns shrink ~4x."""
    if body and (headers.get("Content-Encoding") or "").lower() == "gzip":
        return gzip.decompress(body)
    return body


def call(method: str, url: str, *, params: dict | None = None, json_body=None, data: bytes | None = None,
         ctype: str | None = None, total_s: float = TOTAL_TIMEOUT_S, token=None) -> Response:
    """One Google request with retries; raises GoogleError (status None = transport/timeout). token: another
    sign-in's access_token (auth.token_for), else the Sheets / Drive one."""
    token = token or auth.access_token
    if params:
        url = f"{url}?{urlencode(params, doseq=True)}"         # repeated keys: values:batchGet ranges
    if json_body is not None:
        data, ctype = json.dumps(json_body).encode(), "application/json; charset=UTF-8"
    deadline = time.monotonic() + total_s
    refreshed, force, last = False, False, GoogleError(f"{_short(method, url)}: no attempt made")
    for attempt in range(MAX_TRIES):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            last = GoogleError(f"{_short(method, url)}: no answer within {int(total_s)} s")
            break
        headers = {"Authorization": f"Bearer {token(force=force)}", "Accept-Encoding": "gzip",
                   "User-Agent": GZIP_AGENT}
        force = False
        if ctype:
            headers["Content-Type"] = ctype
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        hint = None
        try:
            with urllib.request.urlopen(req, timeout=min(CONNECT_TIMEOUT_S, remaining)) as resp:
                return Response(resp.status, _inflate(resp.read(MAX_BYTES), resp.headers))
        except urllib.error.HTTPError as err:
            body = _inflate(err.read(), err.headers)
            err.close()
            reason, message = _reason(body)
            last = GoogleError(f"{_short(method, url)}: {err.code} {reason} {message}".strip(), err.code)
            if err.code == 401 and not refreshed:
                refreshed = force = True
                continue
            if err.code not in RETRYABLE and not (err.code == 403 and reason in RATE_REASONS):
                raise last from err
            hint = _retry_after(err)
        except (urllib.error.URLError, TimeoutError, ConnectionError, socket.timeout) as err:
            last = GoogleError(f"{_short(method, url)}: {getattr(err, 'reason', err)}")
        if attempt < MAX_TRIES - 1:
            _sleep(min(_pause(attempt, hint), max(0.0, deadline - time.monotonic())))
    raise last


def get(url: str, params: dict | None = None, total_s: float = TOTAL_TIMEOUT_S) -> dict:
    return call("GET", url, params=params, total_s=total_s).json()


def post(url: str, body: dict, params: dict | None = None, total_s: float = TOTAL_TIMEOUT_S) -> dict:
    return call("POST", url, params=params, json_body=body, total_s=total_s).json()


def multipart(metadata: dict, media: bytes, media_type: str) -> tuple[bytes, str]:
    """A multipart/related body for Drive uploadType=multipart (metadata part + media part)."""
    boundary = f"jha-{uuid.uuid4().hex}"
    head = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{json.dumps(metadata)}\r\n"
            f"--{boundary}\r\nContent-Type: {media_type}\r\n\r\n").encode()
    return head + media + f"\r\n--{boundary}--\r\n".encode(), f"multipart/related; boundary={boundary}"
