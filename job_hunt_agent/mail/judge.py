"""what one email does, from Claude's answer (the full email read) with the rules as a cross-check.

Applied automatically only when Claude is confident ("high") AND exactly one application matches; the change then
goes through update.py's guards as before (forward only, a status set by hand after the email wins, Undo). Anything
else waits in "Needs you" with both answers shown. The rules never decide on their own any more: they only cross-check,
and when they name a status change that Claude does not (or another one), the email waits for the person.
"""
from dataclasses import replace

from . import STATUS_OF, update
from .classify import STATUS_KINDS, Verdict
from .match import Match

METHOD = "claude"
LABEL = {"rejected": "rejected", "screen": "screen", "interview": "interview", "offer": "offer",
         "received": "received", "other": "not a job email", None: "nothing"}


def disagree(rule_kind: str | None, kind: str) -> bool:
    """The rules name a status change and Claude says something else."""
    return rule_kind in STATUS_KINDS and rule_kind != kind


def _needs(conn, pid, mail, kind, found: Match, app_id, reason, now, extra) -> str:
    return update._store(conn, pid, mail, kind, METHOD, found, "needs", now=now, app_id=app_id,
                         to_status=STATUS_OF.get(kind), reason=reason, **extra)


def plan(found: Match, verdict: Verdict, answer: dict) -> tuple[str, object, str | None]:
    """(action, application, why) for Claude's answer: action "needs" | "ignore" | "received" | "auto". The
    application is the one it is about when sure, else the best guess shown in "Needs you"."""
    kind = answer["kind"]
    by_id = {a.id: a for a in found.apps}
    app = by_id.get(answer["application"])
    single = app is not None and set(answer["candidates"]) <= {app.id}
    guess = app or by_id.get(next(iter(answer["candidates"]), None)) or update._likely(found)
    if disagree(verdict.kind, kind):
        return "needs", guess, f"the rules say {LABEL[verdict.kind]}, Claude says {LABEL[kind]}"
    if kind == "other":
        return "ignore", None, None
    if kind == "received":
        return "received", app if single else None, None
    if not single or answer["confidence"] != "high":
        return "needs", guess, ("no application matched" if not found.apps else "several applications match"
                                if not single else f"Claude is not sure ({answer['confidence']})")
    return "auto", app, None


def decide(conn, pid: str, mail, found: Match, verdict: Verdict, answer: dict, now) -> str:
    """Store what this email does and do it when sure. Returns the row's state."""
    kind = answer["kind"]
    extra = {"rule_kind": verdict.kind, "claude_reason": answer["reason"], "confidence": answer["confidence"]}
    action, app, why = plan(found, verdict, answer)
    if action == "needs":
        return _needs(conn, pid, mail, kind, found, app.id if app else None, why, now, extra)
    if action == "ignore":
        return update._store(conn, pid, mail, "other", METHOD, found, "ignored", now=now)
    if action == "received":
        return update._received(conn, pid, mail, METHOD, replace(found, app=app), now, extra,
                                revive=answer["confidence"] == "high")
    return update._auto(conn, pid, mail, METHOD, replace(found, app=app), STATUS_OF[kind], kind, now, extra)


def unanswered(conn, pid: str, mail, found: Match, verdict: Verdict, now) -> str:
    """Claude answered the chunk but not this email: the person decides (with the rules' guess shown)."""
    guess = found.app or update._likely(found)
    extra = {"rule_kind": verdict.kind}
    return _needs(conn, pid, mail, verdict.guess or "other", found, guess.id if guess else None,
                  "Claude gave no answer for this email", now, extra)
