"""Polite HTTP client: one rate bucket per host, gzip, conditional GETs (304), retries with backoff, size cap, and a
per-host backoff when a host starts refusing us (403/429), so a ban pauses that host instead of failing its boards.

urllib raises 304 Not Modified as an HTTPError; it is handled explicitly here and returned as Response(status=304),
never as a failure (decision 005 skeptic minor 4).
"""
import gzip
import json
import os
import random
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import yaml

MIN_INTERVAL_S = 0.34          # <= 3 requests/second per host
HOST_GAPS_S = {"api.lever.co": 1.0, "jobs.lever.co": 1.0,          # Lever robots.txt: Crawl-delay: 1
               "data.commoncrawl.org": 3.0,                         # bulk index files: serial, 3 s apart
               "www.amazon.jobs": 1.0,
               "jobs.jobvite.com": 1.0}                             # every Jobvite board shares this one host
TIMEOUT_S = 20
MAX_BYTES = 40 * 1024 * 1024
MAX_TRIES = 4
BACKOFF_S = (1, 2, 4)
RETRY_AFTER_CAP_S = 30
RETRYABLE = {429, 500, 502, 503, 504}
REFUSALS = {403, 429}          # a run of these from one host = we are being refused: back off that host
REFUSALS_TO_BACKOFF = 3
BACKOFF_STEPS_S = (15 * 60, 60 * 60, 4 * 3600, 24 * 3600)
AGENT = "job-hunt-agent/0.2 (personal job search{contact})"

_buckets: dict[str, list] = {}
_buckets_lock = threading.Lock()
_state_lock = threading.Lock()
_refusals: dict[str, int] = {}             # bucket -> consecutive refusals
_backoff: dict[str, tuple[float, int]] = {}  # bucket -> (until epoch seconds, strike count)
_stats = {"n_req": 0, "n_304": 0, "bytes": 0}
_by_bucket: dict[str, dict[str, int]] = {}
_agent: list[str] = []


class HttpError(Exception):
    """Raised when a request finally fails. status is None for transport errors and host backoff."""

    def __init__(self, url: str, status: int | None, message: str, backoff: bool = False, robots: bool = False):
        super().__init__(f"{status or 'transport'} {message} <{url}>")
        self.url = url
        self.status = status
        self.backoff = backoff
        self.robots = robots

    @property
    def is_transient(self) -> bool:
        return self.status is None or self.status in RETRYABLE


@dataclass(frozen=True)
class Response:
    status: int                 # 200, 206 or 304
    body: bytes                 # decoded body (b"" for 304)
    etag: str | None = None
    last_mod: str | None = None
    cookies: tuple[str, ...] = ()   # 'name=value' of each Set-Cookie (Dayforce's CSRF pair)


def user_agent() -> str:
    """Real User-Agent; the contact address comes from <home>/config/local.yaml (never hardcoded)."""
    if not _agent:
        home = Path(os.environ.get("JOB_HUNT_AGENT_HOME") or Path.home() / ".job-hunt-agent").expanduser()
        try:
            local = yaml.safe_load((home / "config" / "local.yaml").read_text()) or {}
        except (OSError, yaml.YAMLError):
            local = {}
        email = str(local.get("contact_email") or "").strip()
        _agent.append(AGENT.format(contact=f"; contact: {email}" if email else ""))
    return _agent[0]


# vendors that serve every tenant from one platform under per-tenant subdomains share one rate bucket, so
# thousands of small boards never add up past one polite rate on the vendor (<= 3 req/s across all its tenants)
SHARED_BUCKETS = (".bamboohr.com", ".breezy.hr", ".recruitee.com", ".teamtailor.com", ".pinpointhq.com",
                  ".applytojob.com", ".csod.com", ".avature.net", ".careers.hibob.com", ".hire.trakstar.com",
                  ".jobs.personio.com", ".tbe.taleo.net")


def bucket_key(host: str) -> str:
    """Rate-limit key. Workday throttles per data center (all *.wd5.myworkdayjobs.com tenants share one limit)."""
    if host.endswith(".myworkdayjobs.com"):
        return ".".join(host.split(".")[-3:])
    for suffix in SHARED_BUCKETS:
        if host.endswith(suffix):
            return suffix[1:]
    return host


_paced: dict[str, float] = {}                    # adapter-set gaps for tenant hosts (net.pace)


def pace(host: str, seconds: float) -> None:
    """An adapter's gap for a tenant host HOST_GAPS_S cannot list in advance (e.g. Eightfold careers domains)."""
    with _state_lock:
        _paced[host] = max(seconds, _paced.get(host, 0.0))


def gap_for(host: str) -> float:
    """Seconds between requests to this host: our fixed or adapter-set gap, or robots.txt Crawl-delay when longer."""
    from . import robots
    with _state_lock:
        paced = _paced.get(host, 0.0)
    return max(HOST_GAPS_S.get(host, MIN_INTERVAL_S), paced, robots.crawl_delay(host) or 0.0)


def _wait_turn(host: str) -> None:
    key = bucket_key(host)
    with _buckets_lock:
        bucket = _buckets.setdefault(key, [threading.Lock(), 0.0])
    with bucket[0]:
        delay = bucket[1] - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        bucket[1] = time.monotonic() + gap_for(host)


# ---- host backoff (403/429 runs) -------------------------------------------------------------------------------

def backoff_until(host: str) -> float | None:
    """Epoch seconds until which this host is paused, or None."""
    with _state_lock:
        until = _backoff.get(bucket_key(host), (0.0, 0))[0]
    return until if until > time.time() else None


def backoff_state() -> dict[str, dict]:
    """bucket -> {until, strikes} for persistence (fetch writes it to the DB) and doctor."""
    with _state_lock:
        return {k: {"until": u, "strikes": s} for k, (u, s) in _backoff.items()}


def load_backoff(state: dict[str, dict]) -> None:
    """Restore persisted backoff (start of a run)."""
    with _state_lock:
        for key, value in state.items():
            _backoff[key] = (float(value.get("until") or 0), int(value.get("strikes") or 0))


def _note_status(host: str, status: int | None, retry_after: float | None = None) -> None:
    key = bucket_key(host)
    # a Workday 403 is one tenant's disabled site, not the data center refusing us: only 429 counts there (so
    # on every vendor bucket shared by many tenants, e.g. one closed BambooHR site never pauses the others)
    refusals = {429} if host.endswith(".myworkdayjobs.com") or host.endswith(SHARED_BUCKETS) else REFUSALS
    with _state_lock:
        if status in refusals:
            _refusals[key] = _refusals.get(key, 0) + 1
            if _refusals[key] >= REFUSALS_TO_BACKOFF or (retry_after or 0) > RETRY_AFTER_CAP_S:
                strikes = _backoff.get(key, (0.0, 0))[1]
                pause = max(BACKOFF_STEPS_S[min(strikes, len(BACKOFF_STEPS_S) - 1)], retry_after or 0)
                _backoff[key] = (time.time() + pause, strikes + 1)
                _refusals[key] = 0
        elif status is not None and status < 500:
            _refusals[key] = 0
            if key in _backoff and _backoff[key][0] <= time.time():
                _backoff[key] = (0.0, max(0, _backoff[key][1] - 1))     # healthy again: decay the strikes


# ---- stats -----------------------------------------------------------------------------------------------------

def reset_stats() -> None:
    with _state_lock:
        _stats.update(n_req=0, n_304=0, bytes=0)
        _by_bucket.clear()


def stats() -> dict:
    with _state_lock:
        return dict(_stats)


def bucket_stats() -> dict[str, dict[str, int]]:
    with _state_lock:
        return {k: dict(v) for k, v in _by_bucket.items()}


def _count(status: int, nbytes: int, host: str = "") -> None:
    with _state_lock:
        _stats["n_req"] += 1
        _stats["n_304"] += status == 304
        _stats["bytes"] += nbytes
        per = _by_bucket.setdefault(bucket_key(host), {"n_req": 0, "n_304": 0, "bytes": 0})
        per["n_req"] += 1
        per["n_304"] += status == 304
        per["bytes"] += nbytes


# ---- core ------------------------------------------------------------------------------------------------------

def _retry_after(err: urllib.error.HTTPError) -> float | None:
    value = err.headers.get("Retry-After") if err.headers else None
    if value and value.strip().isdigit():
        return float(value)
    return None


def _headers(accept: str, data: bytes | None, etag: str | None, last_mod: str | None,
             extra: dict | None) -> dict:
    headers = {"User-Agent": user_agent(), "Accept": accept, "Accept-Encoding": "gzip"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if etag:
        headers["If-None-Match"] = etag
    if last_mod:
        headers["If-Modified-Since"] = last_mod
    return {**headers, **(extra or {})}


def _read(resp) -> tuple[bytes, int]:
    raw = resp.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise HttpError(resp.geturl(), None, "response exceeds size cap")
    wire = len(raw)
    if (resp.headers.get("Content-Encoding") or "").lower() == "gzip":
        raw = gzip.decompress(raw)
    return raw, wire


def _is_robots(url: str) -> bool:
    return urlsplit(url).path == "/robots.txt"


ROBOTS_BLOCKED = 451             # status of HttpError for a URL robots.txt disallows (never sent on the wire)


def _gate(url: str) -> None:
    """Robots gate (decision 005 run 2): refuse disallowed URLs; unreadable robots.txt = transient, retry later."""
    from . import robots
    verdict = robots.allowed(url)
    if verdict is None:
        raise HttpError(url, None, "robots.txt unreadable right now; retry later")
    if not verdict:
        raise HttpError(url, ROBOTS_BLOCKED, "robots.txt disallows this URL", robots=True)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A 3xx answer is returned as an HTTPError: the caller decides (a dead BambooHR board redirects to the
    vendor's marketing or login page, which must never be read as the board)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_STAY = urllib.request.build_opener(_NoRedirect)
REDIRECTS = {301, 302, 303, 307, 308}


def request(url: str, *, body: dict | None = None, accept: str = "application/json", etag: str | None = None,
            last_mod: str | None = None, headers: dict | None = None, gate: bool = True,
            redirects: bool = True) -> Response:
    """One polite request with retries. Returns 200/206/304 responses; raises HttpError otherwise (with
    redirects=False a 3xx answer too, its status kept)."""
    data = json.dumps(body).encode() if body is not None else None
    host = urlsplit(url).hostname or ""
    if gate:
        _gate(url)
    if backoff_until(host):
        raise HttpError(url, None, f"host backoff ({bucket_key(host)} refused us; paused)", backoff=True)
    sent = _headers(accept, data, etag, last_mod, headers)
    last: HttpError | None = None
    for attempt in range(MAX_TRIES):
        _wait_turn(host)
        pause = None
        try:
            req = urllib.request.Request(url, data=data, headers=sent)
            with (urllib.request.urlopen if redirects else _STAY.open)(req, timeout=TIMEOUT_S) as resp:
                raw, wire = _read(resp)
                _count(resp.status, wire, host)
                _note_status(host, resp.status)
                return Response(resp.status, raw, resp.headers.get("ETag"), resp.headers.get("Last-Modified"),
                                tuple(c.split(";", 1)[0].strip() for c in resp.headers.get_all("Set-Cookie") or ()))
        except urllib.error.HTTPError as err:
            err.close()
            _count(err.code, 0, host)
            if err.code == 304:
                _note_status(host, 304)
                return Response(304, b"", err.headers.get("ETag") or etag,
                                err.headers.get("Last-Modified") or last_mod)
            retry_after = _retry_after(err)
            if not _is_robots(url):         # a 403 robots.txt means "no rules" (RFC 9309), never a refusal
                _note_status(host, err.code, retry_after)
            last = HttpError(url, err.code, err.reason or "http error")
            if err.code not in RETRYABLE or backoff_until(host):
                raise last from err
            pause = min(retry_after, RETRY_AFTER_CAP_S) if retry_after is not None else None
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, EOFError) as err:
            last = HttpError(url, None, str(getattr(err, "reason", err)))
        if attempt < MAX_TRIES - 1:
            time.sleep(pause if pause is not None else BACKOFF_S[attempt] + random.random() * 0.5)
    raise last  # type: ignore[misc]


def _json(url: str, resp: Response) -> object:
    try:
        return json.loads(resp.body)
    except (json.JSONDecodeError, UnicodeDecodeError) as err:
        raise HttpError(url, 200, f"invalid JSON: {err}") from err


def _json_retrying(url: str, body: dict | None, headers: dict | None = None, redirects: bool = True) -> object:
    """A truncated body is usually transient: one more try before giving up."""
    try:
        return _json(url, request(url, body=body, headers=headers, redirects=redirects))
    except HttpError as err:
        if err.status != 200:
            raise
        return _json(url, request(url, body=body, headers=headers, redirects=redirects))


def get_json(url: str, headers: dict | None = None, redirects: bool = True) -> object:
    return _json_retrying(url, None, headers, redirects)


def post_json(url: str, body: dict, headers: dict | None = None) -> object:
    return _json_retrying(url, body, headers)


def get_json_with_cookies(url: str, headers: dict | None = None) -> tuple[object, tuple[str, ...]]:
    """GET JSON and the cookies it sets (no retry on a bad body: the caller asks again for a fresh pair)."""
    resp = request(url, headers=headers)
    return _json(url, resp), resp.cookies


def get_json_cond(url: str, etag: str | None = None, last_mod: str | None = None) -> tuple[Response, object]:
    """Conditional GET: (response, parsed JSON or None on 304)."""
    resp = request(url, etag=etag, last_mod=last_mod)
    return resp, (None if resp.status == 304 else _json(url, resp))


def get_text(url: str, headers: dict | None = None, redirects: bool = True) -> str:
    """Polite GET of a public HTML/text page (discovery: board titles, robots.txt)."""
    return request(url, accept="text/html,text/plain", headers=headers,
                   redirects=redirects).body.decode("utf-8", "replace")


def get_range(url: str, start: int, end: int) -> bytes:
    """Bytes [start, end] of a static file (Common Crawl index files)."""
    resp = request(url, accept="*/*", headers={"Range": f"bytes={start}-{end}", "Accept-Encoding": "identity"})
    return resp.body


def remote_size(url: str) -> int:
    """Total size of a static file, from a 1-byte range read's Content-Range header."""
    host = urlsplit(url).hostname or ""
    _wait_turn(host)
    req = urllib.request.Request(url, headers={"Range": "bytes=0-0", "User-Agent": user_agent()})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            _count(resp.status, 1, host)
            return int((resp.headers.get("Content-Range") or "").rsplit("/", 1)[1])
    except (urllib.error.URLError, TimeoutError, OSError, IndexError, ValueError) as err:
        raise HttpError(url, getattr(err, "code", None), f"no size: {err}") from err
