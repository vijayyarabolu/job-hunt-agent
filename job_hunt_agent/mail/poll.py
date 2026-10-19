"""GOAL12's mail plumbing, kept by GOAL14: where to read from, the cursor, the Cold rule once a day, and the rules
pipeline `process()` (match + rules + Haiku), which is now the harness of the rules' fixtures only.

mail is read once a night by nightly.py (the server's daily thread) and by "Check now"; Claude reads every
kept email in full and the rules only cross-check, so no status ever moves on keywords alone. The server's own "mail"
thread now only runs the Cold rule once a day per person, mail connected or not. Never before 2026-10-08 00:00 local.
"""
import logging
import threading
from datetime import date, datetime, timedelta, timezone

from .. import config, db
from ..google import state
from . import START_DAY, MailError, classify, llm, match, prefilter, update
from .gmail import GmailReader
from .imap import ADDRESS_KEY, HOST_KEY, ImapReader
from .signin import status as signin_status

log = logging.getLogger(__name__)
TICK_S = 60
OVERLAP_S = 5 * 60
KEY = "mail.{}.{}"


def start() -> datetime:
    """2026-10-08 00:00 on this Mac's clock, as UTC."""
    return datetime.combine(date.fromisoformat(START_DAY), datetime.min.time()).astimezone().astimezone(timezone.utc)


def reader_for(conn, pid: str):
    """The person's Gmail reader (token), else IMAP (address set + Keychain), else None: nothing is read until the
    person connects."""
    if signin_status(conn, pid)["connected"]:
        return GmailReader(pid)
    address = state.get(conn, ADDRESS_KEY.format(pid))
    return ImapReader(address, state.get(conn, HOST_KEY.format(pid))) if address else None


def _since(conn, pid: str) -> datetime:
    cursor = state.get(conn, KEY.format(pid, "cursor"))
    if not cursor:
        return start()
    seen = datetime.fromisoformat(cursor.replace("Z", "+00:00")) - timedelta(seconds=OVERLAP_S)
    return max(start(), seen)


def _known(conn, pid: str, after: datetime) -> set[str]:
    rows = conn.execute("SELECT msg_id FROM mail_messages WHERE profile = ? AND sent_at >= ?",
                        (pid, db.iso(after)))
    return {r["msg_id"] for r in rows}


def process(conn, pid: str, mails: list, now: datetime, runner=llm.run_claude) -> dict:
    """Decide every email; returns counts per outcome (applied / recorded / needs / ignored). The GOAL14 pre-filter
    comes first, as in the nightly read: a job alert naming an applied-to company never reaches the rules."""
    apps = match.candidates(conn, pid)
    staged, ask = [], []
    for mail in mails:
        found = match.find(mail.sender, mail.subject, mail.text, apps)
        if not prefilter.decide(mail.sender, mail.subject, found)[0]:
            staged.append((mail, found, classify.Verdict("other", {}, False), False))
            continue
        verdict = classify.classify(mail.subject, mail.text, found.from_ats)
        needs_claude = verdict.job_mail and verdict.kind is None and (found.key is not None or found.from_ats)
        staged.append((mail, found, verdict, needs_claude))
        if needs_claude:
            ask.append((mail.subject, mail.snippet))
    answers = iter(llm.classify_batch(conn, ask, now.astimezone().date(), runner) if ask else [])
    counts: dict[str, int] = {}
    for mail, found, verdict, needs_claude in staged:
        kind, method = verdict.kind, "rule"
        if needs_claude:
            told = next(answers)
            kind, method = (told, "llm") if told else (verdict.guess, "none")
        outcome = update.decide(conn, pid, mail, kind, method, found, verdict.job_mail, now)
        counts[outcome] = counts.get(outcome, 0) + 1
    return counts


def poll_profile(conn, pid: str, now: datetime | None = None, reader=None, runner=None) -> dict:
    """"Check now" and `job-hunt-agent mail poll`: the nightly read, at once."""
    from . import claude, nightly                              # late: nightly uses this module's helpers
    return nightly.run_person(conn, pid, now, reader, runner or claude.run_claude)


REDO_STATES = ("recorded", "ignored")     # changed nothing and wait for nobody; "Needs you" rows stay (an email deleted
                                          # from Gmail since could not be read again, and its question would vanish)


def redo(conn, pid: str) -> int:
    """After a rules fix: forget the emails that changed nothing and rewind the cursor, so the next check reads them
    again with today's rules. Emails that moved a status, wait in "Needs you", or that a person confirmed / dismissed
    / undid, stay (their ids stay known, so they are never read twice)."""
    with db.tx(conn):
        gone = conn.execute(f"DELETE FROM mail_messages WHERE profile = ? AND state IN "
                            f"({','.join('?' * len(REDO_STATES))})", (pid, *REDO_STATES)).rowcount
        state.put(conn, KEY.format(pid, "cursor"), None)
    return gone


def cold_once_a_day(conn, pid: str, now: datetime) -> dict:
    today = now.astimezone().date()
    if state.get(conn, KEY.format(pid, "cold_on")) == today.isoformat():
        return {"cold": "done today"}
    result = update.cold_sweep(conn, pid, today)
    with db.tx(conn):
        state.put(conn, KEY.format(pid, "cold_on"), today.isoformat())
    return result


def run(conn, now: datetime | None = None) -> dict:
    """The server thread's cycle: the Cold rule once a day per person (mail itself is read nightly)."""
    now = now or db.utcnow()
    report = {}
    for pid in config.profile_ids():
        try:
            report[pid] = {"cold": cold_once_a_day(conn, pid, now)}
        except Exception as err:                              # a bug in one person's cycle must not stop the other's
            log.exception("cold cycle %s crashed", pid)
            report[pid] = {"error": f"{type(err).__name__}: {err}"}
    return report


def loop(stop: threading.Event, tick_s: float = TICK_S) -> None:
    """The server's Cold thread: survives every failure."""
    while not stop.wait(tick_s):
        try:
            conn = db.connect()
            try:
                run(conn)
            finally:
                conn.close()
        except Exception:
            log.exception("mail loop: cycle failed")
