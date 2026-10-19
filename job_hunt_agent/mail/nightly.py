"""Each person's job email is read once a day (Settings: "Check email daily at", default 00:15 local) by the
server's daily thread (ops.py); a run missed while the computer slept happens on the next tick, and `due()` catches it
up. "Check now" runs it at once.

One run for one person: every email since the last run (Trash too, never Spam / Sent: gmail.py) -> pre-filter
(prefilter.py, no AI; skips logged) -> Claude reads the rest in full, in chunks (claude.py) -> judge.py applies the
sure ones and leaves the rest in "Needs you". A usage limit or a lapsed Claude sign-in stops the run: the emails not
answered stay unread (the cursor stops before the first of them) and the next run (or Check now) takes them; no status
ever moves on keywords alone. After the run: one line ("Overnight email: 2 rejected, 1 interview invite, 1 needs you")
for the app header and a desktop / phone notification when something changed or waits.
"""
import logging
from datetime import datetime, timedelta

from .. import config, db, notify, profile_settings
from ..google import state
from . import MailError, claude, classify, judge, match, poll, prefilter, update

log = logging.getLogger(__name__)
KEY = poll.KEY
WORDS = {"rejected": ("rejected", "rejected"), "screen": ("screen", "screens"),
         "interview": ("interview invite", "interview invites"), "offer": ("offer", "offers")}
RUN_STALE = timedelta(minutes=15)
STOPPED = {"limit": "Claude usage limit", "login": "Claude sign-in needed", "error": "Claude did not answer"}


def daily_at(pid: str) -> tuple[int, int]:
    hour, minute = profile_settings.get(pid)[profile_settings.MAIL_AT_KEY].split(":")
    return int(hour), int(minute)


def last_slot(pid: str, now: datetime) -> datetime:
    """The most recent scheduled moment at or before `now` (local time), as UTC."""
    local = now.astimezone()
    hour, minute = daily_at(pid)
    slot = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return slot if slot <= local else slot - timedelta(days=1)


def due(conn, pid: str, now: datetime) -> bool:
    """Not run since the last scheduled moment (a Mac asleep at 00:15 runs it on wake)."""
    ran = state.get(conn, KEY.format(pid, "nightly_at"))
    return not ran or datetime.fromisoformat(ran.replace("Z", "+00:00")) < last_slot(pid, now)


def summary(outcomes: list[tuple[str, str]], waiting: int, stopped: str | None) -> str:
    """'Overnight email: 2 rejected, 1 interview invite, 1 needs you' from (state, kind) per email."""
    parts = []
    for kind, (one, many) in WORDS.items():
        n = sum(1 for st, k in outcomes if st == "applied" and k == kind)
        if n:
            parts.append(f"{n} {one if n == 1 else many}")
    needs = sum(1 for st, _ in outcomes if st == "needs")
    if needs:
        parts.append(f"{needs} need{'s' if needs == 1 else ''} you")
    if waiting:
        parts.append(f"{waiting} wait for the next run ({STOPPED.get(stopped, stopped)})")
    return "Overnight email: " + (", ".join(parts) or "no updates")


def _cursor(mails: list, waiting: list) -> str | None:
    """Everything before this has been handled: just before the first email still waiting for Claude."""
    if waiting:
        return db.iso(min(m.sent_at for m in waiting) - timedelta(seconds=1))
    return db.iso(max(m.sent_at for m in mails)) if mails else None


def _decide_all(conn, pid: str, asked: list, answers: claude.Result, now) -> tuple[list, list]:
    outcomes, waiting = [], []
    for i, (mail, found, verdict) in enumerate(asked):
        if i >= answers.answered:
            waiting.append(mail)
        elif i in answers.answers:
            outcomes.append((judge.decide(conn, pid, mail, found, verdict, answers.answers[i], now),
                             answers.answers[i]["kind"]))
        else:
            outcomes.append((judge.unanswered(conn, pid, mail, found, verdict, now), verdict.guess))
    return outcomes, waiting


def read_mail(conn, pid: str, mails: list, now: datetime, runner=claude.run_claude) -> dict:
    """Pre-filter, Claude, decide. Returns the run's report (no state written but the emails' own rows)."""
    apps = match.candidates(conn, pid)
    asked, outcomes = [], []
    for mail in mails:
        found = match.find(mail.sender, mail.subject, mail.text, apps)
        keep, why = prefilter.decide(mail.sender, mail.subject, found)
        if keep:
            asked.append((mail, found, classify.classify(mail.subject, mail.text, found.from_ats)))
            continue
        prefilter.log_skip(pid, mail, why)
        outcomes.append((update._store(conn, pid, mail, "other", "prefilter", found, "ignored", now=now), "other"))
    answers = claude.read(pid, [(m, f.apps) for m, f, _ in asked], runner) if asked else claude.Result()
    decided, waiting = _decide_all(conn, pid, asked, answers, now)
    outcomes += decided
    return {"read": len(mails), "skipped": len(mails) - len(asked), "asked": len(asked), "outcomes": outcomes,
            "waiting": waiting, "stopped": answers.stopped if waiting else None, "usage": answers.usage,
            "cursor": _cursor(mails, waiting)}


def _claim(conn, pid: str, now: datetime) -> bool:
    """One run per person at a time (the 00:15 job and a "Check now" never pay Claude twice for the same mail); a mark
    older than RUN_STALE is a crashed run."""
    with db.tx(conn):
        mark = state.get(conn, KEY.format(pid, "running"))
        if mark and now - datetime.fromisoformat(mark.replace("Z", "+00:00")) < RUN_STALE:
            return False
        state.put(conn, KEY.format(pid, "running"), db.iso(now))
    return True


def run_person(conn, pid: str, now: datetime | None = None, reader=None, runner=claude.run_claude,
               notify_person: bool = True) -> dict:
    now = now or db.utcnow()
    reader = reader or poll.reader_for(conn, pid)
    if reader is None:
        return {"skipped": "mail not connected"}
    if not _claim(conn, pid, now):
        return {"skipped": "a check is already running"}
    try:
        return _run(conn, pid, now, reader, runner, notify_person)
    finally:
        with db.tx(conn):
            state.put(conn, KEY.format(pid, "running"), None)


def _run(conn, pid: str, now: datetime, reader, runner, notify_person: bool) -> dict:
    after = poll._since(conn, pid)
    try:
        mails = reader.since(after, poll._known(conn, pid, after))
    except MailError as err:
        with db.tx(conn):
            state.put(conn, KEY.format(pid, "error"), str(err))
            state.put(conn, KEY.format(pid, "checked_at"), db.iso(now))
            state.put(conn, KEY.format(pid, "nightly_at"), db.iso(now))
        return {"error": str(err)}
    report = read_mail(conn, pid, mails, now, runner)
    line = summary(report["outcomes"], len(report["waiting"]), report["stopped"])
    _save(conn, pid, now, reader, report, line)
    if notify_person and (report["waiting"] or any(st in ("applied", "needs") for st, _ in report["outcomes"])):
        notify.send("job-hunt-agent", line, pid)
    log.info("mail %s: %s (read %d, skipped %d, Claude %d; usage %s)", pid, line, report["read"], report["skipped"],
             report["asked"], report["usage"])
    return {**{k: v for k, v in report.items() if k not in ("waiting", "outcomes")}, "line": line,
            "waiting": len(report["waiting"]), "states": [st for st, _ in report["outcomes"]]}


def _save(conn, pid: str, now: datetime, reader, report: dict, line: str) -> None:
    with db.tx(conn):
        if report["cursor"]:
            state.put(conn, KEY.format(pid, "cursor"), report["cursor"])
        state.put(conn, KEY.format(pid, "checked_at"), db.iso(now))
        state.put(conn, KEY.format(pid, "nightly_at"), db.iso(now))
        state.put(conn, KEY.format(pid, "error"), STOPPED.get(report["stopped"]) if report["stopped"] else None)
        state.put(conn, KEY.format(pid, "via"), reader.kind)
        state.put(conn, KEY.format(pid, "overnight"), {"day": now.astimezone().date().isoformat(), "line": line,
                                                       "at": db.iso(now), "usage": report["usage"]})


def overnight_line(conn, pid: str, now: datetime | None = None) -> str | None:
    """Today's summary line for the app header (None when there was no run today)."""
    found = state.get_json(conn, KEY.format(pid, "overnight")) or {}
    today = (now or db.utcnow()).astimezone().date().isoformat()
    return found.get("line") if found.get("day") == today else None


def run(conn, now: datetime | None = None, force: bool = False) -> dict:
    """The daily job: every person whose daily time has come and who has not run since; one person's failure
    never stops the other's."""
    now = now or db.utcnow()
    report = {}
    for pid in config.profile_ids():
        try:
            entry = {"cold": poll.cold_once_a_day(conn, pid, now)}
            if force or due(conn, pid, now):
                entry["mail"] = run_person(conn, pid, now)
            report[pid] = entry
        except Exception as err:                               # a bug in one person's run must not stop the other's
            log.exception("nightly mail %s crashed", pid)
            report[pid] = {"error": f"{type(err).__name__}: {err}"}
    return report
