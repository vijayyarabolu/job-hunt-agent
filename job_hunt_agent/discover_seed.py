"""`discover --seed`: Workday tenants for S&P 500 companies the crawl did not surface (decision 005 B1 step 3).

A Workday tenant's robots.txt lists its public career sites ('Allow: /Careers_GM/') and answers 422 for a tenant that
does not exist, so one robots.txt GET per (tenant guess, data center) finds the tenant AND tells us which sites we
may use; disallowed sites are never registered. The tenant guess is the company's compact name only
('General Motors' -> 'generalmotors'), so a hit is that company; abbreviations ('wf') are left to the crawl.
Found sites go through the normal live verification (US posting in 60 days) with the S&P name as the board name.
"""
import logging
import re

from . import config, db, discover_verify, seedlists
from .net import HttpError, request

DATA_CENTERS = ("wd1", "wd5", "wd3", "wd12", "wd103", "wd501")
_ALLOW = re.compile(r"(?im)^\s*allow:\s*/([A-Za-z0-9_-]{1,64})/\s*$")
_SKIP_SITES = {"refreshfacet", "private", "talentcommunity"}
log = logging.getLogger(__name__)


def sites_from_robots(text: str) -> list[str]:
    """Allowed career sites listed in a Workday tenant robots.txt."""
    return [s for s in dict.fromkeys(_ALLOW.findall(text)) if s.lower() not in _SKIP_SITES]


def tenant_sites(tenant: str) -> list[str]:
    """'tenant/wdN/site' keys for every data center where the tenant's robots.txt lists sites."""
    keys = []
    for wd in DATA_CENTERS:
        try:
            text = request(f"https://{tenant}.{wd}.myworkdayjobs.com/robots.txt", accept="text/plain",
                           gate=False).body.decode("utf-8", "replace")
        except HttpError:
            continue                                   # 422 = no such tenant here
        keys.extend(f"{tenant}/{wd}/{site}" for site in sites_from_robots(text))
    return keys


def uncovered(conn) -> dict[str, str]:
    """compact tenant guess -> S&P name, for names with no curated or verified US-live board yet."""
    covered = {seedlists.compact(e["name"]) for e in config.companies_raw()}
    covered |= {seedlists.compact(r[0]) for r in conn.execute(
        "SELECT board_name FROM discover_probes WHERE us60 > 0 AND board_name IS NOT NULL")}
    covered |= {r[0].split("/")[0] for r in conn.execute(
        "SELECT slug FROM discover_probes WHERE ats = 'workday' AND verified_at IS NOT NULL")}
    return {slug: name for slug, name in seedlists.sp500().items() if slug not in covered and len(slug) >= 3}


def seed(conn, limit: int | None = None, workers: int = 6) -> dict:
    todo = list(uncovered(conn).items())[:limit] if limit else list(uncovered(conn).items())
    names = dict(todo)
    pairs = []
    for i, (tenant, _name) in enumerate(todo, 1):
        discover_verify.wait_for_fetch()
        pairs.extend(("workday", key) for key in tenant_sites(tenant))
        if i % 50 == 0:
            log.info("seed: %d/%d names probed, %d sites found", i, len(todo), len(pairs))
    counts = discover_verify.verify_all(conn, pairs, "seed", db.utcnow(), workers, lambda t: names.get(t))
    return {"names": len(todo), "sites": len(pairs), "counts": counts}
