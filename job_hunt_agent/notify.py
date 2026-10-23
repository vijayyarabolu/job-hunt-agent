"""Notifications (GOAL §9, decision 001 amendment 5, decision 002 amendment 4).

After each fetch: one summary per profile for new pushable jobs (worth >= threshold, fresh, fit known, not applied),
at most MAX_PUSHES_PER_DAY per profile; the overflow waits for the 08:00 digest. Channels: macOS (osascript, on a Mac)
and, only when configured, Telegram. Every pushed uid is recorded in `notifications` so nothing is sent twice.
"""
import logging
import os
import subprocess
import sys
from datetime import datetime, timedelta

from . import config, health, ranking, secrets_store, tracker
from .db import iso, tx
from .net import HttpError, post_json

log = logging.getLogger(__name__)
MAX_PUSHES_PER_DAY = 8
DIGEST_TOP = 5


def telegram_settings(profile_id: str | None = None) -> tuple[str, str] | None:
    """(bot token, chat id). A profile's own chat (`telegram_chats: {partner: <id>}` in secrets.yaml) wins over the
    shared `telegram_chat`, so each person can get their own pushes on their phone."""
    secrets = secrets_store.load()
    token = secrets_store.get("telegram_token")
    own = (secrets.get("telegram_chats") or {}).get(profile_id) if profile_id else None
    chat = own or secrets_store.get("telegram_chat")
    return (str(token), str(chat)) if token and chat else None


def own_chat(profile_id: str) -> bool:
    secrets = secrets_store.load()
    return bool((secrets.get("telegram_chats") or {}).get(profile_id))


def _quote(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _osascript(title: str, message: str) -> bool:
    """A macOS notification banner; other systems have only Telegram (or the in-app banner)."""
    if sys.platform != "darwin":
        return False
    script = f'display notification "{_quote(message)}" with title "{_quote(title)}"'
    try:
        done = subprocess.run(["osascript", "-e", script], check=False, timeout=10, capture_output=True)
    except (OSError, subprocess.TimeoutExpired) as err:
        log.warning("macOS notification failed: %s", err)
        return False
    if done.returncode != 0:
        log.warning("macOS notification failed: exit %s", done.returncode)
    return done.returncode == 0


def _telegram(title: str, message: str, tg: tuple[str, str]) -> bool:
    try:
        post_json(f"https://api.telegram.org/bot{tg[0]}/sendMessage", {"chat_id": tg[1], "text": f"{title}\n{message}"})
        return True
    except HttpError as err:
        log.warning("Telegram notification failed: %s", err.status)
        return False


def send(title: str, message: str, profile_id: str | None = None) -> bool:
    """Deliver on every configured channel; a failing channel is logged, never raised. True if any channel took it
    (macOS cannot report a notification hidden by Focus mode, so True means 'handed over', not 'seen')."""
    if os.environ.get("JOB_HUNT_AGENT_NO_NOTIFY"):
        return False
    delivered = _osascript(title, message)
    tg = telegram_settings(profile_id)
    if tg and _telegram(title, message, tg):
        delivered = True
    return delivered


def _pushed_today(conn, profile_id: str, now: datetime) -> int:
    return conn.execute("SELECT count(*) FROM notifications WHERE profile = ? AND channel = 'push' AND sent_at >= ?",
                        (profile_id, iso(now - timedelta(days=1)))).fetchone()[0]


def _uids(row: dict) -> list[str]:
    """A collapsed row's uid plus its other-location copies (one req, one push)."""
    return [row["uid"], *(uid for uid, _ in row.get("also", ()))]


def pending_pushes(conn, profile: dict, now: datetime) -> list:
    """Pushable (row, score) pairs not notified yet, best first."""
    sent = {r[0] for r in conn.execute("SELECT uid FROM notifications WHERE profile = ? AND channel = 'push'",
                                       (profile["id"],))}
    hidden = sent | ranking.dismissed(conn, profile["id"])
    ranked = ranking.collapse(conn, ranking.rank(conn, profile, now, fresh_only=True))
    return [(row, s) for row, s in ranked if s.push and not hidden & set(_uids(row))]


def notify_new(conn, profiles: list[dict], now: datetime) -> int:
    """One summary notification per profile for new pushable jobs; returns the number of jobs announced."""
    total = 0
    for profile in profiles:
        slots = MAX_PUSHES_PER_DAY - _pushed_today(conn, profile["id"], now)
        batch = pending_pushes(conn, profile, now)[:max(0, slots)]
        if not batch:
            continue
        top_row, top = batch[0]
        if not send(f"job-hunt-agent · {profile['name']}",
                    f"{len(batch)} new to apply today. Top: {top_row['company']} · {top_row['title']} ({top.worth})",
                    profile["id"]):
            continue                                    # not recorded: the next fetch tries again (release review M5)
        with tx(conn):
            conn.executemany("INSERT OR IGNORE INTO notifications(profile, uid, channel, sent_at) "
                             "VALUES (?,?,'push',?)",
                             [(profile["id"], uid, iso(now)) for row, _ in batch for uid in _uids(row)])
        total += len(batch)
    return total


def _digest_line(row: dict, s) -> str:
    return f"- {s.worth} {row['company']} · {row['title']} ({' · '.join(s.chips[:3])}) {row['apply_url'] or row['url']}"


def digest_path(now: datetime):
    return config.DATA / "digests" / f"{now.astimezone():%Y-%m-%d}.md"


def digest_due(now: datetime, hour: int, window_h: int) -> bool:
    """Once a day, from `hour` local time for `window_h` hours (a Mac that wakes at 10:00 still gets it; one that
    wakes at 23:00 does not get a 'morning' digest), unless today's digest file already exists."""
    local = now.astimezone()
    return hour <= local.hour < hour + window_h and not digest_path(now).exists()


def digest(conn, now: datetime) -> str:
    """Morning digest text (also written to data/digests/<date>.md and sent as one notification)."""
    h = health.health(conn, now)
    lines = [f"# job-hunt-agent digest {now.astimezone():%Y-%m-%d}", ""]
    if h["stale"]:
        lines.append(f"**Fetcher stale:** last successful fetch {h['last_ok_fetch_min']} min ago.")
    if h["failing"]:
        lines.append(f"**Failing boards:** {', '.join(b['board'] for b in h['failing'])}")
    for profile in config.profiles():
        gone = ranking.dismissed(conn, profile["id"])                # dismissed in the UI (M8 round 3)
        ranked = ranking.collapse(conn, [(r, s) for r, s in ranking.rank(conn, profile, now) if r["uid"] not in gone])
        today = [(r, s) for r, s in ranked                          # already applied is not "to apply" (M8 round 2)
                 if s.band == "Apply today" and not s.muted]
        due = tracker.followups_due(conn, profile["id"])
        section = [f"## {profile['name']}: {len(today)} to apply today · {len(due)} follow-ups due",
                   *(_digest_line(row, s) for row, s in today[:DIGEST_TOP])]
        lines += ["", *section]
        tg = telegram_settings(profile["id"]) if own_chat(profile["id"]) else None
        if tg and not os.environ.get("JOB_HUNT_AGENT_NO_NOTIFY"):
            _telegram("job-hunt-agent · morning digest", "\n".join(section), tg)      # the full list on their own phone
    text = "\n".join(lines) + "\n"
    out = digest_path(now)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    send("job-hunt-agent · morning digest", " | ".join(ln.removeprefix("## ") for ln in lines if ln.startswith("## ")))
    return text
