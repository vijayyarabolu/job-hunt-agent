"""robots.txt gate on every request (decision 005 run 2 (a), RFC 9309) and before registering a board.

- A host's robots.txt is fetched once a day (through net, with the gate itself off) and cached in memory and in the
  `robots` table (host, status, fetched_at, sha, body), so a fetch run does not re-download hundreds of files.
- RFC 9309: a 4xx answer (401/403/404/...) means no rules, everything allowed; a 5xx or no answer means disallow for
  now and retry later (allowed() returns None, net raises a transient error, the board carries over).
- net.request refuses any URL the gate disallows with HttpError(451, robots=True).
- Matching follows RFC 9309 section 2.2.2: the group naming our agent (else `*`; same-agent groups merge), longest
  matching rule wins, Allow on a tie, `*` and `$` in patterns, percent-encoding compared decoded, /robots.txt always
  allowed. (The stdlib RobotFileParser applies the first matching line and no wildcards, which refused URLs a later,
  longer Allow permits, e.g. Eightfold's `Disallow: /` then `Allow: /api/pcsx`.)
- CASELESS_HOSTS serve a path under any spelling (Workday: /ThermoFisherCareers/ is /thermofishercareers/), so their
  rules and paths are compared without case there: a site the owner disallowed is refused in every spelling (user
  decision 2026-10-06; 48 boards had passed only by case). RFC 9309's case-sensitive match applies everywhere else.
- DATASET_HOSTS are exempt: data.commoncrawl.org serves Common Crawl's index files, which Common Crawl documents for
  direct HTTP download; its robots.txt ('Disallow: /') keeps search crawlers off the bucket listing. We read only the
  documented cluster.idx / cdx shard files, serially, 3 s apart (net.HOST_GAPS_S).
"""
import hashlib
import math
import threading
import time
from dataclasses import dataclass, replace
from urllib.parse import quote, unquote, urlsplit

AGENT = "job-hunt-agent"
TTL_S = 24 * 3600
RETRY_S = 15 * 60
THROTTLED = {408, 429}           # a robots.txt answer that means "not now": unreachable (retry), never "no rules"
MAX_CRAWL_DELAY_S = 60.0
DATASET_HOSTS = {"data.commoncrawl.org"}
CASELESS_HOSTS = (".myworkdayjobs.com", ".myworkdaysite.com")    # host suffixes whose paths ignore case
_lock = threading.Lock()
_dirty: set[str] = set()


@dataclass(frozen=True)
class Rule:
    allow: bool
    length: int                 # octets of the normalized pattern: the longest match wins
    pieces: tuple[str, ...]     # literal runs between '*' wildcards (normalized)
    anchored: bool              # pattern ended in an unencoded '$'

    def matches(self, path: str) -> bool:
        """Glob match in linear passes (no regex: a pattern full of '*' cannot blow up). Leftmost placement of each
        literal run is always safe for '*'-only patterns."""
        first, rest = self.pieces[0], self.pieces[1:]
        if not path.startswith(first):
            return False
        if not rest:
            return not self.anchored or path == first
        pos = len(first)
        for piece in rest[:-1]:
            found = path.find(piece, pos)
            if found < 0:
                return False
            pos = found + len(piece)
        last = rest[-1]
        if self.anchored:
            return path.endswith(last) and len(path) - len(last) >= pos
        return path.find(last, pos) >= 0


@dataclass(frozen=True)
class Group:
    names: frozenset[str]
    rules: tuple[Rule, ...]
    delay: float | None         # Crawl-delay (not in RFC 9309; honored by net.gap_for as a politeness floor)


@dataclass(frozen=True)
class Rules:
    groups: tuple[Group, ...]

    def _groups(self, agent: str) -> tuple[Group, ...]:
        mine = tuple(g for g in self.groups if agent.lower() in g.names)
        return mine or tuple(g for g in self.groups if "*" in g.names)

    def _for(self, agent: str) -> tuple[Rule, ...]:
        return tuple(r for g in self._groups(agent) for r in g.rules)

    def crawl_delay(self, agent: str) -> float | None:
        delays = [g.delay for g in self._groups(agent) if g.delay is not None]
        return max(delays) if delays else None

    def can_fetch(self, agent: str, url: str, caseless: bool = False) -> bool:
        parts = urlsplit(url)
        path = _norm(parts.path or "/") + (f"?{_norm(parts.query)}" if parts.query else "")
        if path == "/robots.txt":
            return True
        rules = self._for(agent)
        if caseless:
            path = path.lower()
            rules = tuple(replace(r, pieces=tuple(p.lower() for p in r.pieces)) for r in rules)
        hits = [r for r in rules if r.matches(path)]
        if not hits:
            return True
        best = max(r.length for r in hits)
        return any(r.allow for r in hits if r.length == best)


def _norm(text: str) -> str:
    """One spelling per path: decode %XX, then re-encode everything outside the URI unreserved/reserved sets."""
    return quote(unquote(text), safe="/?&=:;@!$'()*+,-._~%")


def _rule(allow: bool, pattern: str) -> Rule:
    """Wildcards are the pattern's own '*' and final '$', found before decoding: '%2A' and '%24' stay literal."""
    anchored = pattern.endswith("$")
    pieces = tuple(_norm(piece) for piece in (pattern[:-1] if anchored else pattern).split("*"))
    return Rule(allow, len("*".join(pieces)) + anchored, pieces, anchored)


def _seconds(value: str) -> float | None:
    """A Crawl-delay in seconds: finite and >= 0, capped at MAX_CRAWL_DELAY_S (a stall guard for one bucket)."""
    try:
        seconds = float(value)
    except ValueError:
        return None
    return min(seconds, MAX_CRAWL_DELAY_S) if math.isfinite(seconds) and seconds >= 0 else None


def parser_for(text: str) -> Rules:
    groups: list[tuple[set[str], list[Rule], list[float]]] = []
    in_agents = False
    for raw in text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        key, sep, value = raw.split("#", 1)[0].partition(":")
        if not sep:
            continue
        key, value = key.strip().lower(), value.strip()
        if key == "user-agent":
            if not in_agents:
                groups.append((set(), [], []))
                in_agents = True
            groups[-1][0].add(value.lower())
        elif key in ("allow", "disallow") and groups:
            in_agents = False
            if value:
                groups[-1][1].append(_rule(key == "allow", value))
        elif key == "crawl-delay" and groups and _seconds(value) is not None:
            in_agents = False
            groups[-1][2].append(_seconds(value))
    return Rules(tuple(Group(frozenset(names), tuple(rules), max(delays) if delays else None)
                       for names, rules, delays in groups))


_cache: dict[str, tuple[Rules | None, float, int | None, str]] = {}   # host -> parser, fetched, status, body


def _fetch(scheme: str, host: str) -> tuple[Rules | None, int | None, str]:
    from .net import HttpError, request
    try:
        resp = request(f"{scheme}://{host}/robots.txt", accept="text/plain,text/html", gate=False)
        body = resp.body.decode("utf-8", "replace")
        return parser_for(body), resp.status, body
    except HttpError as err:
        if err.status is not None and 400 <= err.status < 500 and err.status not in THROTTLED:
            return parser_for(""), err.status, ""             # RFC 9309: unavailable = no rules
        return None, err.status, ""                           # unreachable: disallow, retry later


_fetching: dict[str, threading.Lock] = {}          # one robots.txt request per host when threads race


def _fresh(host: str, now: float) -> tuple[bool, Rules | None]:
    with _lock:
        cached = _cache.get(host)
    if cached and now - cached[1] < (TTL_S if cached[0] is not None else RETRY_S):
        return True, cached[0]
    return False, None


def _entry(scheme: str, host: str) -> Rules | None:
    hit, parser = _fresh(host, time.time())
    if hit:
        return parser
    with _lock:
        host_lock = _fetching.setdefault(host, threading.Lock())
    with host_lock:
        return _entry_locked(scheme, host)


def _entry_locked(scheme: str, host: str) -> Rules | None:
    now = time.time()
    hit, parser = _fresh(host, now)
    if hit:
        return parser
    parser, status, body = _fetch(scheme, host)
    with _lock:
        _cache[host] = (parser, now, status, body)
        if parser is not None:
            _dirty.add(host)
    return parser


def allowed(url: str) -> bool | None:
    """True/False for our agent; None when robots.txt could not be read right now (treat as disallowed for now)."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host in DATASET_HOSTS:
        return True
    parser = _entry(parts.scheme or "https", host)
    if parser is None:
        return None
    return parser.can_fetch(AGENT, url, caseless=host.endswith(CASELESS_HOSTS))


def crawl_delay(host: str) -> float | None:
    """The cached robots.txt Crawl-delay for our agent (no request; None when not cached or not set)."""
    with _lock:
        cached = _cache.get(host.lower())
    return cached[0].crawl_delay(AGENT) if cached and cached[0] is not None else None


def status_of(host: str) -> int | None:
    with _lock:
        cached = _cache.get(host.lower())
    return cached[2] if cached else None


def load(conn) -> None:
    """Warm the cache from the robots table (entries younger than TTL_S are used without a request)."""
    from datetime import datetime, timezone
    for host, status, fetched_at, body in conn.execute("SELECT host, status, fetched_at, body FROM robots"):
        moment = datetime.strptime(fetched_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
        with _lock:
            if host not in _cache:
                _cache[host] = (parser_for(body or ""), moment, status, body or "")


def save(conn) -> int:
    """Persist robots.txt files fetched by this process; returns how many."""
    from datetime import datetime, timezone
    with _lock:
        rows = [(h, _cache[h][2], datetime.fromtimestamp(_cache[h][1], tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                 hashlib.sha1(_cache[h][3].encode()).hexdigest(), _cache[h][3]) for h in _dirty if h in _cache]
        _dirty.clear()
    if rows:
        with conn:
            conn.executemany("INSERT INTO robots(host, status, fetched_at, sha, body) VALUES (?,?,?,?,?) "
                             "ON CONFLICT(host) DO UPDATE SET status=excluded.status, fetched_at=excluded.fetched_at, "
                             "sha=excluded.sha, body=excluded.body", rows)
    return len(rows)


def clear() -> None:
    with _lock:
        _cache.clear()
        _dirty.clear()
