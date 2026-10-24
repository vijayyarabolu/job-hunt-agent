"""`discover --cc`: board tokens from Common Crawl's public URL index (decision 005 B1 step 1).

Common Crawl publishes, per crawl, a sorted `cluster.idx` (one line per 3,000-line block of the CDX index) and the
gzip CDX shards. The CDX query server (index.commoncrawl.org) disallows robots, so this reads the documented data
files directly: binary-search cluster.idx with HTTP Range reads for each ATS's SURT prefix, then range-read only the
blocks that cover it. Requests are serial and spaced (net.HOST_GAPS_S: 3 s); every block is cached under data/cc/
and checked for completeness, so a restart costs nothing.

Crawl data is never trusted: tokens must pass strict patterns before any URL is built. ats.detect() runs on it only
for the GOAL10 systems (Oracle, Taleo, classic iCIMS, Jobvite), and every key it returns is re-checked against the
same kind of strict pattern before use.
"""
import gzip
import json
import logging
import re
import zlib
from pathlib import Path
from typing import Callable, Iterator, NamedTuple

from . import atomic, config
from .net import HttpError, get_range, remote_size

BASE = "https://data.commoncrawl.org/"
# Newest three crawls (skeptic minor 6) + the two the design measured for Lever's long tail.
CRAWLS = ("CC-MAIN-2026-39", "CC-MAIN-2026-34", "CC-MAIN-2026-30", "CC-MAIN-2025-08", "CC-MAIN-2024-10")
PREFIXES = {                        # SURT host prefixes per ATS (SmartRecruiters dropped: robots Disallow: /)
    "greenhouse": ("io,greenhouse,boards)/", "io,greenhouse,job-boards)/"),
    "lever": ("co,lever,jobs)/",),
    "ashby": ("com,ashbyhq,jobs)/",),
    "workday": ("com,myworkdayjobs,", "com,myworkdaysite,"),
    "oracle_recruiting": ("com,oraclecloud,",),           # GOAL10 enterprise systems
    "taleo": ("net,taleo,",),
    "icims_classic": ("com,icims,",),
    "jobvite": ("com,jobvite,jobs)/",),
    "bamboohr": ("com,bamboohr,",),                       # <tenant>.bamboohr.com
    "rippling": ("com,rippling,ats)/",),
    "gem": ("com,gem,jobs)/",),
    "adp": ("com,adp,workforcenow)/mascsr/default/mdf/recruitment/recruitment.html",),
    "paylocity": ("com,paylocity,recruiting)/recruiting/jobs/all/",),     # /Details/ pages name no board
    "dayforce": ("com,dayforcehcm,jobs)/",),
    "cornerstone": ("com,csod,",),
    "recruitee": ("com,recruitee,",),
    "breezy": ("hr,breezy,",),
    "pinpoint": ("com,pinpointhq,",),
    "hibob": ("com,hibob,careers,",),
    "jobscore": ("com,jobscore,careers)/careers/", "com,jobscore,careers)/jobs/"),
}
GOAL16 = ("bamboohr", "rippling", "gem",                  # keys from ats.detect, re-checked by discover_more
          "adp", "paylocity", "dayforce", "cornerstone",
          "recruitee", "breezy", "pinpoint", "hibob", "jobscore")
PROBE_BYTES = 16 * 1024             # one cluster.idx probe during the binary search
SCAN_BYTES = 256 * 1024             # forward scan window once the start is found
MAX_BLOCKS_PER_PREFIX = 600
TOKEN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
WD_HOST = re.compile(r"^([a-z0-9-]+)\.(wd\d{1,3})\.myworkdayjobs\.com$")
WD_SITE_HOST = re.compile(r"^(wd\d{1,3})\.myworkdaysite\.com$")
WD_SITE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_LOCALE = re.compile(r"^[a-z]{2}-[A-Za-z]{2}$")
_RESERVED = {"embed", "api", "jobs", "job", "v1", "static", "assets", "careers", "favicon.ico", "robots.txt",
             "sitemap.xml", "login", "search", "wday", "recruiting", "cdn-cgi", "j", "_next", "apply", "details"}
_URL = re.compile(r"^https?://([^/:?#]+)(?::\d+)?(/[^?#]*)?(?:\?([^#]*))?", re.I)
log = logging.getLogger(__name__)


class Block(NamedTuple):
    shard: str          # cdx-00123.gz
    offset: int
    length: int


def cache_dir() -> Path:
    return config.DATA / "cc"


def _idx_url(crawl: str) -> str:
    return f"{BASE}cc-index/collections/{crawl}/indexes/cluster.idx"


def parse_idx_line(line: str) -> tuple[str, Block] | None:
    """'com,foo)/bar 2026...\\tcdx-00001.gz\\t123\\t456\\t7' -> (surt key, Block)."""
    parts = line.split("\t")
    if len(parts) < 4 or not parts[2].isdigit() or not parts[3].isdigit():
        return None
    return parts[0].split(" ")[0], Block(parts[1], int(parts[2]), int(parts[3]))


def _first_line_after(crawl: str, offset: int) -> str | None:
    chunk = get_range(_idx_url(crawl), offset, offset + PROBE_BYTES).decode("utf-8", "replace")
    lines = chunk.split("\n")
    if offset == 0:
        return lines[0]
    return lines[1] if len(lines) > 2 else None


def find_start(prefix: str, size: int, probe: Callable[[int], str | None]) -> int:
    """Byte offset in cluster.idx at or before the last block line whose key sorts before `prefix`."""
    lo, hi = 0, size
    while hi - lo > SCAN_BYTES // 2:
        mid = (lo + hi) // 2
        line = probe(mid)
        parsed = parse_idx_line(line) if line else None
        if parsed is None or parsed[0] < prefix:
            lo = mid
        else:
            hi = mid
    return lo


def select_blocks(lines: list[str], prefix: str, previous: Block | None) -> tuple[list[Block], Block | None, bool]:
    """Pure scan step: (blocks under prefix, last block before it, done). The block that starts before the prefix
    may hold the prefix's first URLs, so it is included once the prefix begins."""
    found: list[Block] = []
    for line in lines:
        parsed = parse_idx_line(line)
        if parsed is None:
            continue
        key, block = parsed
        if key < prefix:
            previous = block
            continue
        if previous is not None:
            found.append(previous)
            previous = None
        if not key.startswith(prefix):
            return found, None, True
        found.append(block)
    return found, previous, False


def blocks_for(crawl: str, prefix: str, size: int) -> list[Block]:
    """Blocks whose key range can contain URLs under `prefix` (cached as JSON)."""
    cached = cache_dir() / crawl / f"blocks-{re.sub(r'[^a-z0-9]+', '_', prefix)}.json"
    if cached.exists():
        return [Block(*b) for b in json.loads(cached.read_text())]
    offset = find_start(prefix, size, lambda off: _first_line_after(crawl, off))
    found: list[Block] = []
    previous, done, first = None, False, offset > 0
    while not done and offset < size and len(found) < MAX_BLOCKS_PER_PREFIX:
        raw = get_range(_idx_url(crawl), offset, min(size - 1, offset + SCAN_BYTES - 1))
        lines = raw.split(b"\n")
        if first:                               # started mid-line: the first fragment is not a real line
            offset += len(lines.pop(0)) + 1
            raw = raw[raw.index(b"\n") + 1:] if b"\n" in raw else b""
            first = False
        tail = lines.pop() if offset + len(raw) < size else b""     # partial last line: re-read next window
        offset += len(raw) - len(tail)
        step, previous, done = select_blocks([ln.decode("utf-8", "replace") for ln in lines], prefix, previous)
        found.extend(step)
        if not lines:
            break
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text(json.dumps([list(b) for b in found]))
    return found


def idx_size(crawl: str) -> int:
    cached = cache_dir() / crawl / "cluster.size"
    if cached.exists():
        return int(cached.read_text())
    size = remote_size(_idx_url(crawl))
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text(str(size))
    return size


def read_block(crawl: str, block: Block) -> list[str]:
    """CDX lines of one block, from the cache when complete (length match + clean gzip end), else downloaded."""
    path = cache_dir() / crawl / f"{block.shard}-{block.offset}.gz"
    if path.exists() and path.stat().st_size == block.length:
        try:
            return gzip.decompress(path.read_bytes()).decode("utf-8", "replace").splitlines()
        except (OSError, EOFError, zlib.error):
            path.unlink()                       # truncated cache file: fetch again
    raw = get_range(f"{BASE}cc-index/collections/{crawl}/indexes/{block.shard}", block.offset,
                    block.offset + block.length - 1)
    lines = gzip.decompress(raw).decode("utf-8", "replace").splitlines()     # raises on an incomplete block
    atomic.write_bytes(path, raw)
    return lines


def token_from_url(ats: str, url: str) -> str | None:
    """Strictly validated board key from one crawled URL, or None. Workday keys are 'tenant/wdN/site'."""
    found = _URL.match(url.strip())
    if not found:
        return None
    host = found.group(1).lower()
    segments = [s for s in (found.group(2) or "").split("/") if s]
    if ats == "workday":
        return _workday_key(host, segments)
    if ats in ENTERPRISE:
        return _enterprise_key(ats, url.strip())
    if ats in GOAL16:
        return _goal16_key(ats, url.strip())
    if ats == "greenhouse":
        embedded = re.search(r"(?:^|&)for=([^&]+)", found.group(3) or "")
        if embedded:
            return _clean(embedded.group(1))
    return _clean(segments[0]) if segments else None


def _clean(token: str) -> str | None:
    token = token.lower()
    return token if TOKEN.match(token) and token not in _RESERVED else None


def _workday_key(host: str, segments: list[str]) -> str | None:
    rest = [s for s in segments if not _LOCALE.match(s)]
    tenant_host = WD_HOST.match(host)
    if tenant_host and rest and WD_SITE.match(rest[0]) and rest[0].lower() not in _RESERVED:
        return f"{tenant_host.group(1)}/{tenant_host.group(2)}/{rest[0]}"
    site_host = WD_SITE_HOST.match(host)
    if site_host and len(rest) >= 3 and rest[0] == "recruiting":
        tenant, site = rest[1].lower(), rest[2]
        if TOKEN.match(tenant) and WD_SITE.match(site):
            return f"{tenant}/{site_host.group(1)}/{site}"
    return None


ENTERPRISE = ("oracle_recruiting", "taleo", "icims_classic", "jobvite")
_ORACLE_HOST = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)*\.oraclecloud\.com$")
_ORACLE_SITE = re.compile(r"^[A-Za-z0-9_]{1,40}$")
_TALEO_SECTION = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def _enterprise_key(ats: str, url: str) -> str | None:
    """GOAL10 keys from ats.detect's URL patterns, re-validated strictly (crawl data is never trusted)."""
    from .ats import detect, oracle_recruiting
    found = detect(url) if _URL.match(url) else None
    if not found or found["ats"] != ats:
        return None
    if ats == "oracle_recruiting":
        ok = (_ORACLE_HOST.match(found["token"]) and _ORACLE_SITE.match(found["site"])
              and not oracle_recruiting.SANDBOX.match(found["token"]))
        return f"{found['token']}/{found['site']}" if ok else None
    if ats == "taleo":
        return f"{found['token']}/{found['section']}" if _TALEO_SECTION.match(found["section"]) else None
    return found["token"] if ats == "icims_classic" or TOKEN.match(found["token"]) else None


def _goal16_key(ats: str, url: str) -> str | None:
    from .discover_more import key_from_url
    key = key_from_url(url) if _URL.match(url) else None
    if not key or key[0] != ats:
        return None
    return key[1] if key[1].split("/")[0].lower() not in _RESERVED else None


def tokens_in(ats: str, lines: list[str]) -> Iterator[str]:
    for line in lines:
        parts = line.split(" ", 2)
        if len(parts) < 3:
            continue
        try:
            url = json.loads(parts[2]).get("url") or ""
        except (json.JSONDecodeError, AttributeError):
            continue
        token = token_from_url(ats, url)
        if token:
            yield token


def harvest(crawls: tuple[str, ...] = CRAWLS, atss: tuple[str, ...] = tuple(PREFIXES)) -> dict[str, dict[str, list]]:
    """{ats: {token: [crawl, ...]}} unioned over crawls; also written to data/cc/tokens.json."""
    out: dict[str, dict[str, set]] = {a: {} for a in atss}
    for crawl in crawls:
        try:
            size = idx_size(crawl)
        except (HttpError, OSError, ValueError) as err:
            log.warning("cluster.idx of %s unavailable: %s", crawl, err)
            continue
        for ats in atss:
            for prefix in PREFIXES[ats]:
                for block in blocks_for(crawl, prefix, size):
                    try:
                        lines = read_block(crawl, block)
                    except (HttpError, OSError, EOFError, zlib.error) as err:
                        log.warning("block %s %s skipped: %s", crawl, block, err)
                        continue
                    for token in tokens_in(ats, [ln for ln in lines if ln.startswith(prefix)]):
                        out[ats].setdefault(token, set()).add(crawl)
                log.info("%s %s: %d tokens so far", crawl, ats, len(out[ats]))
    result = {a: {t: sorted(c) for t, c in sorted(toks.items())} for a, toks in out.items()}
    cache_dir().mkdir(parents=True, exist_ok=True)
    path = cache_dir() / "tokens.json"
    kept = json.loads(path.read_text()) if path.exists() else {}      # other ATSs' tokens stay
    path.write_text(json.dumps({**kept, **result}, indent=0))
    return result
