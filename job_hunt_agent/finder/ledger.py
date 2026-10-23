"""The Find-contacts cache and credit ledger (contact_lookups): a provider answer is stored once per person, provider,
call and key (company domain / organization), so the same lookup never spends twice; credits are counted per person
and local day against the daily cap (settings.yaml `contacts_daily_cap`, default 5). Never the key, never a phone."""
import json
from datetime import datetime, timedelta, timezone

from .. import config, db

DAILY_CAP = 5


def cap() -> float:
    return float(config.settings().get("contacts_daily_cap", DAILY_CAP))


def _since(days: int = 0, month: bool = False) -> str:
    local = datetime.now().astimezone()
    start = local.replace(day=1) if month else local - timedelta(days=days)
    start = start.replace(hour=0, minute=0, second=0, microsecond=0)
    return db.iso(start.astimezone(timezone.utc))


def spent(conn, pid: str, since: str) -> float:
    row = conn.execute("SELECT coalesce(sum(credits), 0) FROM contact_lookups WHERE profile = ? AND provider != 'free' "
                       "AND at >= ?", (pid, since)).fetchone()
    return float(row[0])


def usage(conn, pid: str) -> dict:
    """Credits spent through job-hunt-agent today / this month, and what today's cap still allows."""
    today, limit = spent(conn, pid, _since()), cap()
    return {"cap": limit, "used_today": today, "left_today": max(limit - today, 0),
            "used_month": spent(conn, pid, _since(month=True))}


def cached(conn, pid: str, provider: str, call: str, key: str) -> dict | None:
    row = conn.execute("SELECT result FROM contact_lookups WHERE profile = ? AND provider = ? AND call = ? "
                       "AND cache_key = ? AND ok = 1", (pid, provider, call, key)).fetchone()
    return json.loads(row["result"]) if row and row["result"] else None


def cached_prefix(conn, pid: str, provider: str, call: str, key: str) -> list[dict]:
    """Every good answer for `key` itself or `key|<filter>` (GOAL28: Hunter by department), oldest first."""
    rows = conn.execute("SELECT result FROM contact_lookups WHERE profile = ? AND provider = ? AND call = ? AND ok = 1 "
                        "AND (cache_key = ? OR substr(cache_key, 1, ?) = ?) ORDER BY id",
                        (pid, provider, call, key, len(key) + 1, key + "|")).fetchall()
    return [json.loads(r["result"]) for r in rows if r["result"]]


def record(conn, pid: str, app_id: int | None, provider: str, call: str, key: str, credits: float, ok: bool,
           result: dict) -> None:
    """Store one answer (a retry after a failure replaces it; credits only ever add up)."""
    with db.tx(conn):
        conn.execute("INSERT INTO contact_lookups(profile, app_id, provider, call, cache_key, at, credits, ok, result) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(profile, provider, call, cache_key) DO UPDATE SET "
                     "app_id = excluded.app_id, at = excluded.at, credits = contact_lookups.credits + excluded.credits, "
                     "ok = excluded.ok, result = excluded.result",
                     (pid, app_id, provider, call, key, db.iso(db.utcnow()), credits, int(ok), json.dumps(result)))


def last_key(conn, pid: str, app_id: int, calls: tuple[str, ...]) -> str | None:
    """The domain the person used in their last lookup for this application."""
    marks = ",".join("?" * len(calls))
    row = conn.execute(f"SELECT cache_key FROM contact_lookups WHERE profile = ? AND app_id = ? AND call IN ({marks}) "
                       "ORDER BY at DESC, id DESC LIMIT 1", (pid, app_id, *calls)).fetchone()
    return row["cache_key"] if row else None
