"""Run the Sheet sync from the server thread and the CLI without ever crashing them (decision 008).

App edits only mark the DB dirty; the server thread syncs DEBOUNCE_S after the first mark (app -> Sheet well
inside a minute) and polls every POLL_S (Sheet -> app inside 5 minutes). google.lock prevents overlap with a CLI
sync. A lapsed token becomes one notification a day plus the UI banner, never an exception.
"""
import json
import logging
import threading
from datetime import datetime, timedelta, timezone

from .. import db, gsync, gsync_cycle
from . import GoogleError, NeedsConsent, auth, state

log = logging.getLogger(__name__)
DEBOUNCE_S = 10
POLL_S = 120
TICK_S = 5
RETRY_S = 30                           # a failed cycle (quota, network) keeps its edits waiting, retried after this
STALE_S = 15 * 60                      # no good sync for this long while linked: the banner says so
CONFLICT_DAYS = 7


def _age_s(stamp: str | None, now: datetime) -> float | None:
    if not stamp:
        return None
    return (now - datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)).total_seconds()


def due(conn, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    dirty = _age_s(state.dirty(conn)[0], now)
    last = _age_s(state.get(conn, "last_sync_at"), now)
    if last is None or last >= POLL_S:
        return True
    if dirty is None or dirty < DEBOUNCE_S:
        return False
    return not state.get_json(conn, "last_sync_errors") or last >= RETRY_S


def _say(conn, key: str, title: str, message: str) -> None:
    from .. import notify
    if state.once_per_day(conn, key):
        notify.send(title, message)


def sync(conn) -> dict:
    """One cycle over every linked person's sheet; one person's failure never stops the other's."""
    with state.lock() as got:
        if not got:
            return {"skipped": "another sync is running"}
        seq, report, errors = state.dirty(conn)[1], {}, {}
        before = state.get_json(conn, "last_sync_errors") or {}
        for pid in gsync.profiles_linked(conn):
            # GOAL33 §3: one line when a failure starts (or changes) and one when it works again, not one per retry
            # (1,177 a person in two offline days)
            try:
                report[pid] = gsync_cycle.sync_profile(conn, pid)
                if pid in before:
                    log.info("Google sync %s works again", pid)
            except NeedsConsent:
                raise
            except GoogleError as err:
                errors[pid] = str(err)
                log.log(logging.DEBUG if before.get(pid) == errors[pid] else logging.ERROR, "Google sync %s failed: %s", pid, err)
            except Exception as err:                     # a bug in one person's cycle must not stop the other's
                errors[pid] = f"sync error: {type(err).__name__}: {err}"
                if before.get(pid) == errors[pid]:
                    log.debug("Google sync %s crashed again: %s", pid, err)
                else:
                    log.exception("Google sync %s crashed", pid)
        now = db.iso(db.utcnow())
        with db.tx(conn):
            state.put(conn, "last_sync_at", now)
            state.put(conn, "last_sync_errors", errors or None)
            state.put(conn, "last_report", report)
            if not errors:                         # a failed person's edits stay waiting (due() retries them)
                state.put(conn, "last_ok_at", now)
                state.clear_dirty(conn, seq)
        return {"profiles": report, **({"errors": errors} if errors else {})}


def run(conn) -> dict:
    """sync(); every Google failure is turned into a report entry (and a daily notice for consent)."""
    status = auth.status()
    if not status["configured"] or not (status["connected"] or status["lapsed"]):
        return {"skipped": "Google is not set up yet (job-hunt-agent google setup)"}
    if not gsync.profiles_linked(conn):
        return {"skipped": "no sheet linked yet (job-hunt-agent google link --profile <p> <sheet url>)"}
    try:
        return sync(conn)
    except NeedsConsent as err:
        _say(conn, "consent", "job-hunt-agent · reconnect Google", str(err))
        with db.tx(conn):
            state.put(conn, "last_sync_errors", {"all": str(err)})
        return {"error": str(err), "needs_consent": True}


def loop(stop: threading.Event, tick_s: float = TICK_S) -> None:
    """The server's sync thread: survives every failure, does nothing until Google is connected and linked."""
    while not stop.wait(tick_s):
        try:
            if not auth.is_ready():
                continue
            conn = db.connect()
            try:
                if gsync.profiles_linked(conn) and due(conn):
                    run(conn)
            finally:
                conn.close()
        except Exception:                                     # the thread must outlive any single bad cycle
            log.exception("google sync loop: cycle failed")


def health(conn, now: datetime | None = None, cells: bool = True) -> dict:
    """Sync status for the UI and doctor: linked people, last sync, last good sync, errors, conflicts (7 days).
    `cells`: also the cells left differing (every linked row's cells; the page never shows them, GOAL30-app)."""
    now = now or datetime.now(timezone.utc)
    errors = state.get_json(conn, "last_sync_errors") or {}
    linked = gsync.profiles_linked(conn)
    since = db.iso(now - timedelta(days=CONFLICT_DAYS))
    conflicts = conn.execute("SELECT count(*) FROM gsync_log WHERE kind = 'conflict' AND at >= ?",
                             (since,)).fetchone()[0]
    missing = conn.execute("SELECT count(*) FROM gsync_rows WHERE missing_since IS NOT NULL").fetchone()[0]
    last_ok = state.get(conn, "last_ok_at")
    ok_age = _age_s(last_ok, now)
    healthy = bool(linked) and not errors and ok_age is not None and ok_age < STALE_S
    found = {"linked": linked, "last_sync_at": state.get(conn, "last_sync_at"), "last_ok_at": last_ok,
             "errors": errors, "conflicts_7d": conflicts, "missing_rows": missing, "healthy": healthy}
    return {**found, "diverged": diverged(conn)} if cells else found


def diverged(conn) -> dict[str, dict[str, int]]:
    """Cells where the Sheet and the app still disagree (left alone since linking, decision 008), per profile and
    column: {'jordan': {'JD': 11, ...}}. `job-hunt-agent google push-diverged` previews and fixes one column."""
    out: dict[str, dict[str, int]] = {}
    for row in conn.execute("SELECT profile, cells FROM gsync_rows WHERE missing_since IS NULL"):
        for header, pair in json.loads(row["cells"]).items():
            if isinstance(pair, list) and len(pair) == 2 and pair[0] != pair[1]:
                per = out.setdefault(row["profile"], {})
                per[header] = per.get(header, 0) + 1
    return out


def banner(conn, now: datetime | None = None) -> dict:
    """For the web UI header: token state, sync health, and one message when something needs a person."""
    now = now or datetime.now(timezone.utc)
    status, sync_state = auth.status(now), health(conn, now, cells=False)
    message = None
    if status["lapsed"]:
        message = "Google sign-in lapsed: run `job-hunt-agent google login` on the Mac (the Sheets catch up after that)"
    elif status["warn"]:
        message = f"Google sign-in expires in {status['days_left']} days: run `job-hunt-agent google login` on the Mac"
    elif sync_state["errors"]:
        message = "Sheet sync failed: " + "; ".join(f"{p}: {e}" for p, e in sync_state["errors"].items())
    elif sync_state["linked"] and not sync_state["healthy"]:
        message = "the Google Sheets have not synced for over 15 minutes (see `job-hunt-agent google status`)"
    return {**status, "sync": sync_state, "message": message, "last_sync_at": sync_state["last_sync_at"]}
