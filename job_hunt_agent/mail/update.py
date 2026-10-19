"""Turn a read email into a status change, safely, plus the Cold rule and the person's actions.

- Forward only: applied -> screen -> interview -> offer; rejected from any of those but offer. Cold (ghosted) counts
  as applied, so a later email revives it; a confirmation revives a Cold row to applied.
- A status the person set by hand (app, CLI or their Sheet) after the email's date is never overwritten.
- Only the status changes (tracker.set_status, which also stamps it for the Sheet sync); no other cell.
- An email's change is one history batch credited to "Email"; its Undo puts the old status back while the row still
  shows what the email set. The tracker's own Undo never reverts it (history.AUTOMATIC).
"""
from datetime import date, datetime, timedelta

from .. import db, history, profile_settings, tracker
from ..google import state
from . import STATUS_OF
from .match import Match

RANK = {"applied": 0, "ghosted": 0, "screen": 1, "interview": 2, "offer": 3}
HAND_SOURCES = ("ui", "cli", "sheet")
MOVE, SAME, CONFLICT = "move", "same", "conflict"
COLD_KEY = "mail.cold.{}"
COLD_KEPT = 30                      # sweeps remembered for Undo


def transition(current: str, wanted: str) -> tuple[str, str]:
    """(MOVE | SAME | CONFLICT, why) for an email asking `wanted` of a row at `current`."""
    if wanted == current:
        return SAME, f"already {wanted}"
    if wanted == "applied":                                    # a confirmation
        return (MOVE, "") if current == "ghosted" else (SAME, "a confirmation changes nothing")
    if current == "rejected":
        return CONFLICT, f"the row is rejected; the email says {wanted}"
    if wanted == "rejected":
        return (CONFLICT, "the row has an offer; the email says rejected") if current == "offer" else (MOVE, "")
    if current not in RANK or wanted not in RANK:
        return CONFLICT, f"{current} -> {wanted} is not a step forward"
    if RANK[wanted] > RANK[current]:
        return MOVE, ""
    return SAME, f"the row is already at {current}"


def hand_set_after(conn, app_id: int, moment: str) -> str | None:
    """The status the person set by hand after `moment` (UTC ISO), if any."""
    row = conn.execute("SELECT to_status FROM application_events WHERE app_id = ? AND source IN "
                       f"({','.join('?' * len(HAND_SOURCES))}) AND at > ? ORDER BY id DESC LIMIT 1",
                       (app_id, *HAND_SOURCES, moment)).fetchone()
    return row["to_status"] if row else None


def _status(conn, app_id: int) -> str:
    return conn.execute("SELECT status FROM applications WHERE id = ?", (app_id,)).fetchone()["status"]


def note(sent_at: str, subject: str | None, link: str | None) -> dict:
    """The "Email" note on the row's status history: which email, when, and its link."""
    return {"at": sent_at, "subject": subject, "link": link}


def _noted(mail) -> dict:
    return note(db.iso(mail.sent_at), mail.subject, mail.link)


def change(conn, app_id: int, status: str, who: str, source: str = "email", rule: str = "email",
           email: dict | None = None) -> str:
    """Set the status as one history batch; returns the batch. An automatic author (Email) always gets its own batch,
    even inside the person's request ("Check now"), so each email's Undo reverts only that email."""
    snapshot = {"rule": rule, **({"email": email} if email else {})}
    with history.action(who, own=who in history.AUTOMATIC) as batch:
        tracker.set_status(conn, app_id, status, source=source, snapshot=snapshot)
    return batch


def record(conn, pid: str, mail, kind: str, method: str, found: Match | None, state_: str, *, app_id=None,
           to_status=None, reason=None, from_status=None, batch=None, now: datetime, rule_kind=None,
           claude_reason=None, confidence=None) -> int:
    """One mail_messages row. An email that is not job mail keeps only its id and date (nothing it said)."""
    keep = state_ != "ignored"
    with db.tx(conn):
        cur = conn.execute(
            "INSERT OR IGNORE INTO mail_messages(profile, msg_id, sender, subject, sent_at, snippet, link, kind, "
            "method, company_guess, app_id, to_status, state, reason, from_status, batch, read_at, acted_at, "
            "rule_kind, claude_reason, confidence) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (pid, mail.msg_id, mail.sender if keep else None, mail.subject if keep else None, db.iso(mail.sent_at),
             mail.snippet if keep else None, mail.link if keep else None, kind, method,
             found.company if keep and found else None, app_id, to_status, state_, reason, from_status, batch,
             db.iso(now), db.iso(now) if state_ == "applied" else None, rule_kind if keep else None,
             claude_reason if keep else None, confidence if keep else None))
    return cur.lastrowid


def decide(conn, pid: str, mail, kind: str | None, method: str, found: Match, job_mail: bool, now: datetime) -> str:
    """Store what this email does and do it when it is sure. Returns the row's state."""
    if not job_mail or (kind in (None, "other") and found.key is None and not found.from_ats):
        return _store(conn, pid, mail, kind or "other", method, found, "ignored", now=now)
    if kind == "other" and method == "llm":
        return _store(conn, pid, mail, "other", method, found, "ignored", now=now)
    if kind == "received":
        return _received(conn, pid, mail, method, found, now)
    wanted = STATUS_OF.get(kind or "")
    if wanted is None or found.app is None or method != "rule":
        guess = found.app or _likely(found)
        why = ("no application matched" if found.key is None else
               "several applications match" if found.app is None else
               "Claude classified it" if method == "llm" else "the rules could not tell what it says")
        return _store(conn, pid, mail, kind or "other", method, found, "needs", now=now,
                      app_id=guess.id if guess else None, to_status=wanted, reason=why)
    return _auto(conn, pid, mail, method, found, wanted, kind, now)


def _likely(found: Match):
    """The candidate to suggest when unsure: an open one, most recently applied."""
    pool = [a for a in found.apps if a.status != "rejected"] or list(found.apps)
    return max(pool, key=lambda a: a.applied_on or "", default=None)


def _received(conn, pid, mail, method, found: Match, now, extra: dict | None = None, revive: bool = True) -> str:
    """A confirmation / reply: recorded on its application; it revives a Cold row (only when sure)."""
    app, extra = found.app, extra or {}
    if (revive and app and _status(conn, app.id) == "ghosted"
            and not hand_set_after(conn, app.id, db.iso(mail.sent_at))):
        batch = change(conn, app.id, "applied", history.EMAIL, email=_noted(mail))
        return _store(conn, pid, mail, "received", method, found, "applied", now=now, app_id=app.id,
                      to_status="applied", from_status="ghosted", batch=batch, reason="a reply revives a Cold row",
                      **extra)
    if app is None and found.key is None:
        return _store(conn, pid, mail, "received", method, found, "needs", now=now, reason="no application matched",
                      **extra)
    return _store(conn, pid, mail, "received", method, found, "recorded", now=now, app_id=app.id if app else None,
                  reason=None if app else "several applications match", **extra)


def _auto(conn, pid, mail, method, found: Match, wanted: str, kind: str, now, extra: dict | None = None) -> str:
    app_id, extra = found.app.id, extra or {}
    by_hand = hand_set_after(conn, app_id, db.iso(mail.sent_at))
    if by_hand:
        return _store(conn, pid, mail, kind, method, found, "recorded", now=now, app_id=app_id, to_status=wanted,
                      reason=f"you set {by_hand} by hand after this email", **extra)
    current = _status(conn, app_id)
    step, why = transition(current, wanted)
    if step != MOVE:
        return _store(conn, pid, mail, kind, method, found, "recorded" if step == SAME else "needs", now=now,
                      app_id=app_id, to_status=wanted, reason=why, **extra)
    batch = change(conn, app_id, wanted, history.EMAIL, email=_noted(mail))
    return _store(conn, pid, mail, kind, method, found, "applied", now=now, app_id=app_id, to_status=wanted,
                  from_status=current, batch=batch, **extra)


def _store(conn, pid, mail, kind, method, found, state_, *, now, **fields) -> str:
    record(conn, pid, mail, kind, method, found, state_, now=now, **fields)
    return state_


# ---- the person's actions on "Needs you" and "Updates from email" ----

def _row(conn, pid: str, row_id: int) -> dict:
    row = conn.execute("SELECT * FROM mail_messages WHERE id = ? AND profile = ?", (row_id, pid)).fetchone()
    if row is None:
        raise KeyError(row_id)
    return dict(row)


def _own_app(conn, pid: str, app_id) -> int:
    app_id = int(app_id)
    if conn.execute("SELECT 1 FROM applications WHERE id = ? AND profile = ?", (app_id, pid)).fetchone() is None:
        raise KeyError(app_id)
    return app_id


def confirm(conn, pid: str, row_id: int, who: str, app_id=None, status: str | None = None) -> dict:
    """The person agrees (or picked another application / status): the change is theirs, made now."""
    row = _row(conn, pid, row_id)
    if row["state"] != "needs":
        raise ValueError("this email was already handled")
    if not (app_id or row["app_id"]):
        raise ValueError("pick the application this email is about")
    target = _own_app(conn, pid, app_id or row["app_id"])
    wanted = status or row["to_status"]
    if wanted and wanted not in tracker.STATUSES:
        raise ValueError(f"unknown status {wanted!r}")
    current, batch = _status(conn, target), None
    if wanted and wanted != current:
        batch = change(conn, target, wanted, who, source="ui", rule="email confirmed",
                       email=note(row["sent_at"], row["subject"], row["link"]))
    _set(conn, row_id, state="confirmed", app_id=target, to_status=wanted, from_status=current if batch else None,
         batch=batch)
    return _row(conn, pid, row_id)


def link(conn, pid: str, row_id: int, app_id) -> dict:
    """GOAL14 §1d "Match to…": an unmatched / ambiguous email that changed nothing is linked to the application the
    person picked (no status change: a status still goes through Needs you's Confirm)."""
    row = _row(conn, pid, row_id)
    if row["state"] != "recorded" or row["app_id"]:
        raise ValueError("only an unmatched email can be matched here")
    if not app_id:
        raise ValueError("pick the application this email is about")
    _set(conn, row_id, app_id=_own_app(conn, pid, app_id), reason=None)
    return _row(conn, pid, row_id)


def dismiss(conn, pid: str, row_id: int) -> dict:
    if _row(conn, pid, row_id)["state"] != "needs":
        raise ValueError("this email was already handled")
    _set(conn, row_id, state="dismissed", reason="not a job email (you said)")
    return _row(conn, pid, row_id)


def undo(conn, pid: str, row_id: int, who: str) -> dict:
    """Put back the status this email's change replaced, while the row still shows what the email set."""
    row = _row(conn, pid, row_id)
    if row["state"] not in ("applied", "confirmed") or not row["batch"]:
        raise ValueError("this email changed nothing to undo")
    if _status(conn, row["app_id"]) != row["to_status"]:
        raise ValueError("the status changed since; nothing undone")
    with history.action(who, "undo") as batch:
        tracker.set_status(conn, row["app_id"], row["from_status"], source="ui", snapshot={"rule": "email undo"})
    history.mark_undone(conn, row["batch"], batch)
    _set(conn, row_id, state="undone")
    return _row(conn, pid, row_id)


def _set(conn, row_id: int, **fields) -> None:
    with db.tx(conn):
        conn.execute(f"UPDATE mail_messages SET {', '.join(f'{k} = ?' for k in fields)}, acted_at = ? WHERE id = ?",
                     (*fields.values(), db.iso(db.utcnow()), row_id))


# ---- Cold: no reply and no status change N days after applying ----

def cold_days(pid: str) -> int:
    return int(profile_settings.get(pid)[profile_settings.COLD_KEY])


def cold_due(conn, pid: str, today: date, days: int | None = None) -> list[int]:
    """Applied rows past the Cold line with no email reply and no status change since the line."""
    days = days or cold_days(pid)
    line = today - timedelta(days=days)
    since = db.iso(datetime.combine(line, datetime.min.time()).astimezone())
    rows = conn.execute(
        "SELECT a.id FROM applications a WHERE a.profile = ? AND a.status = 'applied' AND a.applied_on IS NOT NULL "
        "AND a.applied_on <= ? AND NOT EXISTS (SELECT 1 FROM application_events e WHERE e.app_id = a.id "
        "AND e.source IN ('ui', 'cli', 'sheet', 'email') AND e.at >= ?) AND NOT EXISTS (SELECT 1 FROM mail_messages m "
        "WHERE m.app_id = a.id AND m.kind NOT IN ('received', 'other') "
        "AND m.state IN ('applied', 'recorded', 'confirmed', 'needs') AND m.sent_at >= a.applied_on) ORDER BY a.id",
        (pid, line.isoformat(), since)).fetchall()
    return [r["id"] for r in rows]


def cold_sweep(conn, pid: str, today: date) -> dict:
    """Every due row goes Cold in ONE batch (one Undo reverts the sweep)."""
    due = cold_due(conn, pid, today)
    if not due:
        return {"cold": 0}
    with history.action(history.COLD, own=True) as batch:
        for app_id in due:
            tracker.set_status(conn, app_id, "ghosted", source="email", snapshot={"rule": "cold"})
    done = sweeps(conn, pid)
    with db.tx(conn):
        state.put(conn, COLD_KEY.format(pid), [*done, {"batch": batch, "at": db.iso(db.utcnow()), "n": len(due),
                                                       "days": cold_days(pid)}][-COLD_KEPT:])
    return {"cold": len(due), "batch": batch}


def undo_cold(conn, pid: str, batch: str, who: str) -> int:
    """Rows of that sweep still Cold go back to applied; rows that moved on since are left alone."""
    done = sweeps(conn, pid)
    if batch not in {s["batch"] for s in done}:
        raise ValueError("no such Cold sweep")
    rows = conn.execute("SELECT app_id, old_value FROM edit_history WHERE batch = ? AND field = 'status' "
                        "AND undone_by IS NULL", (batch,)).fetchall()
    back = 0
    with history.action(who, "undo") as undo_batch:
        for r in rows:
            if _status(conn, r["app_id"]) == "ghosted":
                tracker.set_status(conn, r["app_id"], r["old_value"] or "applied", source="ui",
                                   snapshot={"rule": "cold undo"})
                back += 1
    history.mark_undone(conn, batch, undo_batch)
    with db.tx(conn):
        state.put(conn, COLD_KEY.format(pid), [s for s in done if s["batch"] != batch])
    return back


def sweeps(conn, pid: str) -> list[dict]:
    return list(state.get_json(conn, COLD_KEY.format(pid)) or [])
