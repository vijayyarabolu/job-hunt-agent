"""Public company lists used for naming and segments (decision 005 B1 step 3, B3): the S&P 500 constituents and the
Y Combinator company directory, both public files, refreshed by `job-hunt-agent discover --lists` into config/lists/.

- S&P 500: github.com/datasets/s-and-p-500-companies (constituents.csv; Symbol, Security, ...)
- YC: yc-oss.github.io/api/companies/all.json (name, website, team_size, stage, status)
"""
import csv
import io
import json
import re
from functools import lru_cache
from pathlib import Path

from . import config
from .net import get_text

SP500_URL = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
YC_URL = "https://yc-oss.github.io/api/companies/all.json"
_SUFFIX = re.compile(r"(?i)\b(inc|incorporated|corp|corporation|co|company|companies|plc|ltd|holdings?|group|"
                     r"the|class [a-z]|n\.?v|s\.?a|llc)\b\.?")


def lists_dir() -> Path:
    return config.shipped("lists")


def compact(name: str | None) -> str:
    """'The Walt Disney Company' -> 'waltdisney'; '3M' -> '3m'."""
    return re.sub(r"[^a-z0-9]", "", _SUFFIX.sub(" ", (name or "").lower()))


def domain(url: str | None) -> str | None:
    from .discover import domain_of
    return domain_of(url)


def refresh() -> dict:
    """Download both lists into config/lists/ (small, public, committed)."""
    lists_dir().mkdir(parents=True, exist_ok=True)
    sp = list(csv.DictReader(io.StringIO(get_text(SP500_URL))))
    with open(lists_dir() / "sp500.csv", "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["symbol", "name"])
        writer.writerows(sorted((r["Symbol"], r["Security"]) for r in sp))
    yc = json.loads(get_text(YC_URL))
    with open(lists_dir() / "yc.csv", "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["name", "domain", "team_size", "stage", "status"])
        writer.writerows(sorted((c.get("name") or "", domain(c.get("website")) or "", c.get("team_size") or "",
                                 c.get("stage") or "", c.get("status") or "") for c in yc if c.get("name")))
    sp500.cache_clear()
    yc_companies.cache_clear()
    return {"sp500": len(sp), "yc": len(yc)}


def _rows(name: str) -> list[dict]:
    return _read(lists_dir() / name)


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="") as handle:
        return list(csv.DictReader(handle))


def learned_path() -> Path:
    """names the fetch learned for boards named by a tenant id (names_fix.learn); kept with the data, not in
    the repo, and a pin in config/lists/board_names.csv wins over them."""
    return Path(config.DB_PATH).parent / "board_names.learned.csv"


@lru_cache(maxsize=1)
def sp500() -> dict[str, str]:
    """compact name -> display name."""
    return {compact(r["name"]): r["name"] for r in _rows("sp500.csv") if compact(r["name"])}


@lru_cache(maxsize=1)
def yc_companies() -> tuple[dict[str, dict], dict[str, dict]]:
    """(by compact name, by domain) -> {name, team_size, stage, status}."""
    by_name, by_domain = {}, {}
    for row in _rows("yc.csv"):
        if row["status"].lower() in ("inactive", "acquired"):
            continue
        by_name.setdefault(compact(row["name"]), row)
        if row["domain"]:
            by_domain.setdefault(row["domain"], row)
    return by_name, by_domain


def sp500_name_for_slug(slug: str) -> str | None:
    """Workday tenant slug -> S&P display name, only on an exact compact match ('generalmotors' -> 'General
    Motors'); abbreviations ('wf') stay unnamed rather than guessed."""
    return sp500().get(compact(slug))


@lru_cache(maxsize=1)
def _board_names() -> dict[tuple[str, str], str]:
    rows = [*_read(learned_path()), *_rows("board_names.csv")]
    return {(r["ats"], r["key"]): r["name"] for r in rows if r.get("name")}


def board_name(ats: str, key: str) -> str | None:
    """A board's display name from config/lists/board_names.csv (an Oracle tenant titled 'JPMC' is
    JPMorgan Chase, so the S&P match and the feed show the real employer)."""
    return _board_names().get((ats, key))


def in_sp500(name: str | None) -> bool:
    return bool(name) and compact(name) in sp500()


def yc_row(name: str | None, website: str | None) -> dict | None:
    by_name, by_domain = yc_companies()
    site = domain(website)
    return (by_domain.get(site) if site else None) or by_name.get(compact(name))
