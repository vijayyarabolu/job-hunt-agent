"""Orchestrates the crawl discovery pipeline for `job-hunt-agent discover` (decision 005 B1):

  --cc       harvest board tokens from Common Crawl (cached under data/cc/)
  --verify   one live list request per token not verified in 30 days -> discover_probes
  --build    write config/companies.discovered.yaml (curated companies.yaml wins)
  --refresh  the monthly re-run: cc + verify everything again (--refresh TTL) + build. doctor warns when the last
             refresh is older than REFRESH_EVERY (skeptic objection 1: the live count decays)
"""
import json
import logging
from datetime import datetime, timedelta, timezone

from . import db, discover_cc, discover_more, discover_verify, registry, seedlists

REFRESH_EVERY = timedelta(days=30)
log = logging.getLogger(__name__)


def tokens() -> dict[str, list[str]]:
    path = discover_cc.cache_dir() / "tokens.json"
    if not path.exists():
        return {}
    return {ats: sorted(toks) for ats, toks in json.loads(path.read_text()).items()
            if ats in registry.FETCHABLE}


def seed_only() -> dict[str, list[str]]:
    """tenant keys from the seed lists that the crawl did not find (verified with source 'seed16')."""
    crawled = {ats: set(keys) for ats, keys in tokens().items()}
    return {ats: sorted(set(keys) - crawled.get(ats, set())) for ats, keys in discover_more.seed_tokens().items()
            if ats in registry.FETCHABLE}


def interleave(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Round-robin across ATSs so every host's rate bucket stays busy (one slow host never idles the pool)."""
    by_ats: dict[str, list] = {}
    for pair in pairs:
        by_ats.setdefault(pair[0], []).append(pair)
    out, queues = [], list(by_ats.values())
    while queues:
        out.extend(q.pop(0) for q in queues)
        queues = [q for q in queues if q]
    return out


def verify(conn, refresh: bool = False, atss: tuple[str, ...] | None = None, limit: int | None = None,
           workers: int = 8) -> dict:
    now = db.utcnow()
    report: dict = {"todo": 0, "counts": {}}
    for source, found in (("cc", tokens()), ("seed16", seed_only())):
        candidates = {a: t for a, t in found.items() if atss is None or a in atss}
        pairs = interleave(discover_verify.todo(conn, candidates, now, refresh))
        pairs = pairs[:max(0, limit - report["todo"])] if limit else pairs
        log.info("verifying %d %s tokens", len(pairs), source)
        report["todo"] += len(pairs)
        counts = discover_verify.verify_all(conn, pairs, source, now, workers, seedlists.sp500_name_for_slug)
        report["counts"] = {**report["counts"], **{f"{source}:{a}": c for a, c in counts.items()}}
    return report


def refresh(conn) -> dict:
    harvested = discover_cc.harvest()
    report = {"at": db.iso(db.utcnow()), "tokens": {a: len(t) for a, t in harvested.items()},
              "verify": verify(conn, refresh=True), "build": registry.build(conn)}
    (discover_cc.cache_dir() / "refresh.json").write_text(json.dumps(report, indent=1))
    return report


def last_refresh() -> str | None:
    """When the discovered registry was last rebuilt (refresh.json, else the YAML's mtime)."""
    path = discover_cc.cache_dir() / "refresh.json"
    if path.exists():
        return json.loads(path.read_text()).get("at")
    yaml_path = registry.discovered_path()
    if yaml_path.exists():
        return db.iso(datetime.fromtimestamp(yaml_path.stat().st_mtime, tz=timezone.utc))
    return None
