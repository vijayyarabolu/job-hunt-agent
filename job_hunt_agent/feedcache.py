"""the scored feed kept per person between requests.

ranking.rank() scores every open job of the person's families (~10k rows, ~2 s of CPU). Its inputs only move when a
fetch run starts or ends (jobs, fits, boards), when the person's applications change (applied-before flags, the
pipeline) or when they give feedback (likes / passes shape the ranking). One cheap fingerprint query over those
decides whether the kept answer still holds; it is also dropped after TTL_S because worth decays with time
(the server's live watcher recomputes it in the background when a run or an edit lands, so a page rarely waits). Filters,
sorting and the cards are still built per request. Match % per job (match.breakdown) is memoized next to it.
An answer that is only old (nothing it depends on moved) is still served at once and ranked again in the background,
so the first visit after an idle spell does not wait for the ~2-5 s ranking. Only one ranking runs at a time (two tabs,
the live watcher's warm-up and a background re-rank would otherwise each hold one in memory at once).
"""
import json
import logging
import threading
import time
from dataclasses import dataclass, field, replace

from . import config, db, feedback, past_employers, ranking

log = logging.getLogger(__name__)

TTL_S = 600                         # worth decays slowly; the page recomputes ages itself
FINGERPRINT_SQL = ("SELECT (SELECT max(id) FROM runs), (SELECT finished_at FROM runs ORDER BY id DESC LIMIT 1), "
                   "(SELECT max(rowid) FROM jobs), (SELECT count(*) || '/' || max(computed_at) FROM fits WHERE profile = ?), "
                   "(SELECT count(*) FROM applications WHERE profile = ?), "
                   "(SELECT max(updated_at) FROM applications WHERE profile = ?), "
                   "(SELECT max(id) FROM feedback WHERE profile = ?)")


@dataclass
class Entry:
    fingerprint: tuple
    at: float
    ranked: list
    views: dict = field(default_factory=dict)      # uid -> (match breakdown, resume view) for api.jobs, on demand
    profile_sig: str = ""                          # the profile it was ranked for (views are only reused for it)


_ENTRIES: dict[tuple, Entry] = {}
_LOCK = threading.Lock()
_BUSY: set[tuple] = set()                       # keys being ranked again in the background
_RANK = threading.Lock()        # one ranking at a time: each holds ~50 MB while it runs (GOAL7 §4 server memory)


def fingerprint(conn, pid: str) -> tuple:
    feedback.ensure(conn)
    return tuple(conn.execute(FINGERPRINT_SQL, (pid, pid, pid, pid)).fetchone())


def key_of(profile: dict, fresh_only: bool, states: tuple[str, ...]) -> tuple:
    """the years cap and years are part of the key, so a Settings change ranks again at once (
    the past employers too)."""
    return (str(config.DB_PATH), profile["id"], fresh_only, states, profile.get("max_required_years"),
            profile.get("years"), past_employers.names(profile))


def ranked(conn, profile: dict, now, fresh_only: bool, states: tuple[str, ...], wait: bool = False) -> Entry:
    """The person's ranked jobs. A kept answer is served at once (re-ranked in the background when it is old or
    what it depends on moved, e.g. during a fetch run); `wait` ranks now when it moved (the watcher's warm-up)."""
    key = key_of(profile, fresh_only, states)
    mark = fingerprint(conn, profile["id"])
    with _LOCK:
        hit = _ENTRIES.get(key)
    same = hit is not None and hit.fingerprint == mark
    if hit and (same or not wait):
        if not same or time.monotonic() - hit.at >= TTL_S:
            _revalidate(key, profile, fresh_only, states)
        return hit
    with _RANK:                                     # another tab may have ranked it while this one waited
        with _LOCK:
            hit = _ENTRIES.get(key)
        if hit and hit.fingerprint == mark:
            return hit
        entry = Entry(mark, time.monotonic(), ranking.rank(conn, profile, now, fresh_only=fresh_only, states=states),
                      profile_sig=_sig(profile))
        with _LOCK:
            _ENTRIES[key] = entry
    return entry


def _revalidate(key: tuple, profile: dict, fresh_only: bool, states: tuple[str, ...]) -> None:
    """One background re-rank per key at a time."""
    with _LOCK:
        if key in _BUSY:
            return
        _BUSY.add(key)
    threading.Thread(target=_rerank, args=(key, profile, fresh_only, states), daemon=True, name="feed-rerank").start()


def _rerank(key: tuple, profile: dict, fresh_only: bool, states: tuple[str, ...]) -> None:
    try:
        conn = db.connect(key[0])                   # the DB the request asked about, whatever DB_PATH is by now
        try:
            with _RANK:
                mark = fingerprint(conn, profile["id"])
                entry = Entry(mark, time.monotonic(), ranking.rank(conn, profile, db.utcnow(), fresh_only=fresh_only,
                                                                   states=states), profile_sig=_sig(profile))
        finally:
            conn.close()
        with _LOCK:
            old = _ENTRIES.get(key)
            if old and old.fingerprint == entry.fingerprint and old.profile_sig == entry.profile_sig:
                entry = replace(entry, views=old.views)  # nothing moved, so a click after a TTL re-rank stays fast
            _ENTRIES[key] = entry
    except Exception:                                   # the old answer stays for another TTL_S (no retry storm)
        log.exception("feed re-rank failed for %s", profile["id"])
        with _LOCK:
            if key in _ENTRIES:
                _ENTRIES[key] = replace(_ENTRIES[key], at=time.monotonic())
    finally:
        with _LOCK:
            _BUSY.discard(key)


def _sig(profile: dict) -> str:
    return json.dumps(profile, sort_keys=True, default=str)


def clear() -> None:
    with _LOCK:
        _ENTRIES.clear()
