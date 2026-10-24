"""GOAL33 §3: the launchd logs (data/logs/*.log) are kept 7 days.

launchd holds each log open in append mode, so a log is rotated by copying what it holds into
data/logs/archive/<name>.<YYYY-MM-DD> and truncating it in place (the writer goes on at the start). The first server
start of a day rotates (and a check every hour catches midnight); archives older than KEEP_DAYS are deleted.
macOS's "MallocStackLogging: can't turn off malloc stack logging" lines (written by forked children before they exec,
nothing in the environment to switch off) are left out of the archives.
"""
import logging
import re
import threading
from datetime import date, timedelta
from pathlib import Path

from . import atomic, config

log = logging.getLogger(__name__)
KEEP_DAYS = 7
CHECK_S = 3600
NOISE = re.compile(rb"MallocStackLogging: can't turn off malloc stack logging")
_STAMP = re.compile(r"\.(\d{4}-\d{2}-\d{2})$")


def _archive_of(path: Path, day: date) -> Path:
    return path.parent / "archive" / f"{path.name}.{day.isoformat()}"


def rotate(path: Path, today: date) -> Path | None:
    """Today's archive of `path` (made now if not made yet today and the log has anything), else None."""
    target = _archive_of(path, today)
    if target.exists() or not path.exists() or path.stat().st_size == 0:
        return None
    with atomic.replacing(target) as part, path.open("rb") as src, part.open("wb") as out:
        for line in src:
            if not NOISE.search(line):
                out.write(line)
    with path.open("r+b") as live:                       # in place: launchd keeps writing to the same file
        live.truncate(0)
    return target


def prune(folder: Path, today: date) -> list[Path]:
    gone = []
    for old in folder.glob("*.*"):
        m = _STAMP.search(old.name)
        if m and date.fromisoformat(m.group(1)) < today - timedelta(days=KEEP_DAYS):
            old.unlink()
            gone.append(old)
    return gone


def rotate_all(folder: Path | None = None, today: date | None = None) -> list[Path]:
    folder, today = folder or config.DATA / "logs", today or date.today()
    made = []
    for path in sorted(folder.glob("*.log")):
        try:
            done = rotate(path, today)
            made += [done] if done else []
        except OSError as err:
            log.warning("could not rotate %s: %s", path, err)
    if (folder / "archive").is_dir():
        prune(folder / "archive", today)
    return made


def loop(stop: threading.Event) -> None:
    """The server's thread: rotate at start, then look every hour (a new day rotates once)."""
    while not stop.is_set():
        try:
            rotate_all()
        except Exception:                                # rotation never takes the server down
            log.exception("log rotation failed")
        if stop.wait(CHECK_S):
            break
