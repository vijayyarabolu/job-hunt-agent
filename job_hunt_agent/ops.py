"""Background work, run by the server itself (`job-hunt-agent serve`) instead of an OS scheduler, so it behaves the
same on macOS, Linux and Windows. The server already runs the fetch every fetch_interval_min, the 08:00 digest and the
Google Sheet sync; loop() adds the once-a-day email check (each person's own time, Settings > Accounts) and the
monthly H-1B data refresh. A run missed while the computer slept happens at the next tick after it wakes.

Run `job-hunt-agent fetch` from cron (or any scheduler) if you want fetches while the server is not running.
"""
import fcntl
import logging
import threading
from datetime import datetime

from . import config, db

log = logging.getLogger(__name__)
TICK_S = 300
H1B_DAY = 5                     # the DOL posts a quarter's files a few weeks after it ends
H1B_KEY = "h1b.refreshed_month"


def fetch_state() -> dict:
    """{"running": True} while a fetch holds the fetch lock (any process)."""
    path = config.DATA / "fetch.lock"
    if not path.exists():
        return {"running": False}
    try:
        with open(path, "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(lock, fcntl.LOCK_UN)
    except BlockingIOError:
        return {"running": True}
    except OSError:
        return {"running": False}
    return {"running": False}


def schedule_note() -> str:
    """After a person saves a new email time: nothing to reinstall, the server reads it on its next tick."""
    return ""


def status() -> dict:
    """For `doctor`: background work lives in the server, so there is no OS job to check."""
    return {}


def _mail(conn, now: datetime) -> None:
    from .mail import nightly
    nightly.run(conn, now)


def _h1b(conn, now: datetime) -> None:
    from .google import state
    month = f"{now:%Y-%m}"
    if now.day < H1B_DAY or state.get(conn, H1B_KEY) == month:
        return
    from .h1b import refresh
    try:
        refresh.refresh(conn, log=log.info)
    finally:
        with db.tx(conn):
            state.put(conn, H1B_KEY, month)


def loop(stop: threading.Event) -> None:
    """The server's "daily" thread: email and H-1B work when due, every TICK_S. Demo mode does nothing."""
    while not stop.wait(TICK_S):
        if config.demo_mode():
            continue
        conn = db.connect()
        try:
            for job in (_mail, _h1b):
                try:
                    job(conn, db.utcnow())
                except Exception:                              # one failed job must not stop the others
                    log.exception("daily loop: %s failed", job.__name__)
        finally:
            conn.close()
