"""google_state key/value rows, the dirty flag the hooks stamp, and the cross-process google.lock (decision 006).

Hooks (fetch end, tailor finish, status/field edits) only call mark_dirty(): a local SQL write, never a network call.
The server's sync thread (or `google sync` from cron) does the work; a sync clears the flag only when nothing dirtied it while
it ran (dirty_seq unchanged).
"""
import fcntl
import json
import logging
import threading
import time
from contextlib import contextmanager

from .. import config, db

log = logging.getLogger(__name__)
LOCK_POLL_S = 0.5
_local = threading.local()
_thread_lock = threading.Lock()


def get(conn, key: str, default=None):
    row = conn.execute("SELECT value FROM google_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def get_json(conn, key: str, default=None):
    raw = get(conn, key)
    return json.loads(raw) if raw else default


def put(conn, key: str, value) -> None:
    text = value if isinstance(value, str) or value is None else json.dumps(value)
    conn.execute("INSERT INTO google_state(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = "
                 "excluded.value", (key, text))


def mark_dirty(conn) -> None:
    """Something the Sheet shows changed. Keeps the oldest dirty time; bumps the sequence number."""
    conn.execute("INSERT OR IGNORE INTO google_state(key, value) VALUES ('dirty_since', ?)", (db.iso(db.utcnow()),))
    conn.execute("INSERT INTO google_state(key, value) VALUES ('dirty_seq', '1') ON CONFLICT(key) DO UPDATE SET "
                 "value = CAST(value AS INTEGER) + 1")


def dirty(conn) -> tuple[str | None, int]:
    return get(conn, "dirty_since"), int(get(conn, "dirty_seq", "0") or 0)


def clear_dirty(conn, seq_at_start: int) -> bool:
    """Clear only if no hook stamped the flag after the sync started."""
    if dirty(conn)[1] != seq_at_start:
        return False
    conn.execute("DELETE FROM google_state WHERE key = 'dirty_since'")
    return True


@contextmanager
def lock(wait_s: float = 0.0):
    """The google.lock (fcntl, across processes), re-entrant within one thread. Yields False if not acquired."""
    if getattr(_local, "depth", 0):
        _local.depth += 1
        try:
            yield True
        finally:
            _local.depth -= 1
        return
    deadline = time.monotonic() + wait_s
    if not _thread_lock.acquire(timeout=max(wait_s, 0.001)):
        yield False
        return
    try:
        config.DATA.mkdir(parents=True, exist_ok=True)
        with open(config.DATA / "google.lock", "w") as handle:
            if not _flock(handle, deadline):
                yield False
                return
            _local.depth = 1
            try:
                yield True
            finally:
                _local.depth = 0
    finally:
        _thread_lock.release()


def _flock(handle, deadline: float) -> bool:
    while True:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(LOCK_POLL_S)


def once_per_day(conn, key: str) -> bool:
    """True the first time today (local date) for this key, e.g. one 'reconnect Google' notification a day."""
    today = time.strftime("%Y-%m-%d")
    if get(conn, f"notified.{key}") == today:
        return False
    put(conn, f"notified.{key}", today)
    return True
