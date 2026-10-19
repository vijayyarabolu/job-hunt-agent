"""The email parts of the UI, drawn here as HTML so the UI's JavaScript stays inside its 185 KB cap: tracker.js
only inserts this HTML and sends each button's act to /api/mail/act.

- view "panel": "Updates from email" on the Applied / Tracker page: the mailbox line (Check now), "Needs you"
  (Confirm, pick another application or status, Not a job email) and today's changes (filterable by status, each
  with its link and Undo) plus today's Cold sweep (Undo). folded to one summary line by default; today's
  emails newest first, 20 at a time (Show more); Cold is one line ("366 applications went Cold") with Show (its names,
  20 at a time) and one Undo all. Chips and paging buttons carry the whole view in their value
  ("filter|emails shown|Cold names shown"), so the JavaScript only sends it back.
- view "settings": Settings -> "Job-application email": the address to read, Connect (Google sign-in for that
  address, in a new tab), the pasted answer from a phone, IMAP instead, Check now, Disconnect, the time of the daily
  check ("Check email daily at"), and after how many days an application goes Cold.

Every value shown is escaped (subjects and snippets are other people's text); the only mail link is the Gmail one
this app builds from the message id. Every control carries a hover label.
"""
from datetime import datetime
from html import escape

from .. import db, google_page, ops, profile_settings
from ..applied import APPLIED_STATES
from ..google import GoogleError, state
from . import WEB, MailError, address as addr, imap, nightly, poll, signin, update

FILTERS = ("rejected", "screen", "interview", "offer", "received", "cold")
LABEL = {"rejected": "Rejected", "screen": "Screen", "interview": "Interview", "offer": "Offer",
         "received": "Received", "other": "Other", "applied": "Applied", "ghosted": "Cold", "cold": "Cold"}
STATE_NOTE = {"confirmed": "you confirmed", "recorded": "no change", "undone": "undone",
              "dismissed": "not a job email"}
CONFIRMABLE = ("rejected", "screen", "interview", "offer", "applied")
NEEDS_SHOWN = 50
PAGE = 20                                   # today's emails and Cold names shown per "Show more"
GROUP_OVER = 10                             # a Cold group this big shows as one line until "Show"
MAX_SHOWN = 2000


def _e(value) -> str:
    return escape(str(value if value is not None else ""), quote=True)


def _button(act: str, text: str, title: str, value: str | None = None) -> str:
    extra = f' value="{_e(value)}"' if value is not None else ""
    return (f'<button type="button" class="btn small" data-mail="{act}"{extra} '
            f'title="{_e(title)}">{_e(text)}</button>')


def _input(attrs: str, title: str) -> str:
    """An input with its hover label; `attrs` is trusted markup built here (values in it already escaped)."""
    return f'<input {attrs} title="{_e(title)}" aria-label="{_e(title)}">'


def _when(stamp: str | None) -> str:
    if not stamp:
        return ""
    moment = datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone()
    today = moment.date() == datetime.now().astimezone().date()
    return moment.strftime("%-I:%M %p" if today else "%b %-d %-I:%M %p")


def _link(row: dict) -> str:
    subject = _e(row["subject"] or "(no subject)")
    link = row.get("link") or ""
    if not link.startswith(WEB):
        return f"<b>{subject}</b>"
    return f'<a href="{_e(link)}" target="_blank" rel="noopener" title="Open this email in Gmail">{subject}</a>'


def _app_text(row: dict) -> str:
    if row.get("company"):
        return _e(f"{row['company']} · {row['role_title'] or 'role?'}")
    return _e(f"{row['company_guess']} (not in your tracker)") if row.get("company_guess") else "no application"


def _reading(conn, pid: str) -> tuple[str | None, str | None]:
    """(what is read, e.g. 'Gmail a@b.com' / 'IMAP a@b.com', or None; the last check's error)."""
    gmail = signin.status(conn, pid)
    address = state.get(conn, imap.ADDRESS_KEY.format(pid))
    if gmail["connected"]:
        via = f"Gmail {gmail['account'] or ''}".strip()
    elif address:
        via = f"IMAP {address}"
    else:
        return None, None
    return via, state.get(conn, poll.KEY.format(pid, "error"))


def _checked(conn, pid: str) -> str:
    checked = state.get(conn, poll.KEY.format(pid, "checked_at"))
    at = profile_settings.get(pid)[profile_settings.MAIL_AT_KEY]
    return (f"last check {_when(checked)}" if checked else "not checked yet") + f", daily at {at}"


def _connected_line(conn, pid: str, label: str, extra: str = "") -> str | None:
    via, error = _reading(conn, pid)
    if not via:
        return None
    warn = f' <span class="warn">{_e(error)}</span>' if error else ""
    return (f'<p class="small" data-id="mail">{label} <b>{_e(via)}</b> (read-only) · {_checked(conn, pid)}{warn} '
            f'{_button("check", "Check now", "Read new email now")}{extra}</p>')


# ---- view "panel" ----

def _mailbox_line(conn, pid: str) -> str:
    return _connected_line(conn, pid, "Reading") or (
        '<p class="small muted" data-id="mail">Email is not connected yet: Settings → Job-application email. '
        'Nothing is read until you connect.</p>')


def _rows(conn, pid: str, where: str, params: tuple, limit: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        f"SELECT m.*, a.company, a.role_title FROM mail_messages m LEFT JOIN applications a ON a.id = m.app_id "
        f"WHERE m.profile = ? AND {where} ORDER BY m.sent_at DESC LIMIT ?", (pid, *params, limit))]


def _status_select(current: str | None) -> str:
    options = "".join(f'<option value="{s}"{" selected" if s == current else ""}>{LABEL[s]}</option>'
                      for s in CONFIRMABLE)
    return f'<select data-st title="The status to set" aria-label="The status to set">{options}</select>'


PICKER = _input('data-pick list="mail-apps" placeholder="Pick another application"', "Pick another application")
MATCH_TO = _input('data-pick list="mail-apps" placeholder="Match to…"', "The application this email is about")


def _need(r: dict) -> str:
    reason = f" · {_e(r['reason'])}" if r["reason"] else ""
    return (f'<li class="fu-row" data-id="{r["id"]}"><p>{_link(r)} <span class="muted">{_e(r["sender"])} · '
            f'{_when(r["sent_at"])}</span></p><p class="muted small">{_e(r["snippet"])}</p>'
            f'<p class="small">Looks like <b>{_e(LABEL.get(r["to_status"] or r["kind"], r["kind"]))}</b> for '
            f'<b>{_app_text(r)}</b>{reason}</p>{_answers(r)}<p class="row-btns">{_status_select(r["to_status"])} '
            f'{_button("confirm", "Confirm", "Set this status on this application")} '
            f'{PICKER if r["app_id"] else MATCH_TO} '
            f'{_button("dismiss", "Not a job email", "Ignore this email")}</p></li>')


def _answers(r: dict) -> str:
    """both answers, Claude's (with the words it went by) and the rules' cross-check."""
    if not r.get("claude_reason") and not r.get("rule_kind"):
        return ""
    claude = (f'Claude: <b>{_e(LABEL.get(r["kind"], r["kind"]))}</b> ({_e(r["confidence"] or "?")}): '
              f'{_e(r["claude_reason"])}' if r.get("claude_reason") else "")
    rules = f'Rules: {_e(LABEL.get(r["rule_kind"], r["rule_kind"]) if r.get("rule_kind") else "no answer")}'
    return f'<p class="small muted">{" · ".join(x for x in (claude, rules) if x)}</p>'


def _datalist(conn, pid: str) -> str:
    apps = conn.execute(f"SELECT id, company, role_title FROM applications WHERE profile = ? AND status IN "
                        f"({','.join('?' * len(APPLIED_STATES))}) ORDER BY company, role_title",
                        (pid, *APPLIED_STATES)).fetchall()
    options = "".join(f'<option value="{_e(a["company"])} · {_e(a["role_title"])} #{a["id"]}">' for a in apps)
    return f'<datalist id="mail-apps">{options}</datalist>'


def _needs(conn, pid: str) -> tuple[str, int]:
    rows = _rows(conn, pid, "m.state = 'needs'", (), NEEDS_SHOWN)
    if not rows:
        return "", 0
    return (f'<h3 class="small">Needs you <span class="muted">{len(rows)}</span></h3><ul class="fu-list">'
            f'{"".join(_need(r) for r in rows)}</ul>'), len(rows)


def _view(value: str | None) -> tuple[str, int, int]:
    """'filter|emails shown|Cold names shown' (a chip's or a paging button's value) -> its parts, within bounds."""
    parts = [*(value or "").split("|"), "", "", ""][:3]

    def count(text: str, default: int) -> int:
        return min(max(int(text), 0), MAX_SHOWN) if text.isdigit() else default

    return (parts[0] if parts[0] in FILTERS else ""), max(count(parts[1], PAGE), PAGE), count(parts[2], 0)


def _value(wanted: str, shown: int, names: int) -> str:
    return f"{wanted}|{shown}|{names}"


def _chips(counts: dict[str, int], wanted: str) -> str:
    shown = [("", "All", sum(counts.values()))] + [(k, LABEL[k], counts[k]) for k in FILTERS if counts[k]]
    return "".join(f'<button type="button" class="chip" data-mail="view" value="{_value(k, PAGE, 0)}" aria-pressed='
                   f'"{str(k == wanted).lower()}" title="Show {"all" if not k else text} updates">{text} {n}</button>'
                   for k, text, n in shown)


def _midnight() -> str:
    return db.iso(datetime.combine(datetime.now().astimezone().date(), datetime.min.time()).astimezone())


TODAY = "m.state NOT IN ('ignored', 'needs') AND (m.sent_at >= ? OR m.acted_at >= ?)"


def _counts(conn, pid: str, midnight: str, sweeps: list[dict]) -> dict[str, int]:
    found = dict(conn.execute(f"SELECT m.kind, COUNT(*) FROM mail_messages m WHERE m.profile = ? AND {TODAY} "
                              f"GROUP BY m.kind", (pid, midnight, midnight)).fetchall())
    return {**{k: found.get(k, 0) for k in FILTERS}, "cold": sum(s["n"] for s in sweeps)}


def summary_line(counts: dict[str, int]) -> str:
    """'Email today: 1 rejected · 1 offer · 5 received · 366 went Cold' (the folded panel's one line)."""
    said = [f"{n} went Cold" if k == "cold" else f"{n} {LABEL[k].lower()}" for k, n in counts.items() if n]
    return "Email today: " + (" · ".join(said) if said else "nothing yet")


def _today(conn, pid: str, wanted: str, shown: int, names: int) -> tuple[str, dict[str, int]]:
    midnight = _midnight()
    sweeps = [s for s in update.sweeps(conn, pid) if s["at"] >= midnight]
    counts = _counts(conn, pid, midnight, sweeps)
    if not any(counts.values()):
        return '<h3 class="small">Today</h3><p class="muted small">No email updates today yet.</p>', counts
    items = ""
    if wanted != "cold":
        kind = " AND m.kind = ?" if wanted else ""
        rows = _rows(conn, pid, TODAY + kind, (midnight, midnight, *((wanted,) if wanted else ())), shown + 1)
        more = _button("view", "Show more", f"Show {PAGE} more emails", _value(wanted, shown + PAGE, names))
        items = (f'<ul class="fu-list">{"".join(_change(r) for r in rows[:shown])}</ul>'
                 f'{f"<p>{more}</p>" if len(rows) > shown else ""}')
    cold = _cold(conn, sweeps, wanted, shown, names) if wanted in ("", "cold") and sweeps else ""
    return (f'<h3 class="small">Today</h3><div class="t-chips" data-id="filter">{_chips(counts, wanted)}</div>'
            f'{items}{cold}'), counts


def _change(r: dict) -> str:
    moved = (f'{_e(LABEL.get(r["from_status"], r["from_status"]))} → {_e(LABEL.get(r["to_status"], r["to_status"]))}'
             if r["from_status"] and r["state"] in ("applied", "confirmed", "undone") else "")
    note = " · ".join(x for x in (moved, STATE_NOTE.get(r["state"], ""), _e(r["reason"] or "")) if x)
    undo = (_button("undo", "Undo", "Put the status back as it was")
            if r["state"] in ("applied", "confirmed") and r["batch"] else "")
    if r["state"] == "recorded" and not r["app_id"]:                  # unmatched / ambiguous
        undo = f'{MATCH_TO} {_button("match", "Match", "Link this email to that application")}'
    return (f'<li class="fu-row" data-id="{r["id"]}"><span class="tag" title="What the email says">'
            f'{_e(LABEL.get(r["kind"], r["kind"]))}</span> <b>{_app_text(r)}</b> <span class="small">{note}</span> '
            f'{undo}<br><span class="small">{_link(r)} <span class="muted">{_when(r["sent_at"])}</span></span></li>')


def _cold_names(conn, batches: list[str], limit: int) -> list[str]:
    marks = ",".join("?" * len(batches))
    rows = conn.execute(f"SELECT a.company, a.role_title FROM edit_history h JOIN applications a ON a.id = h.app_id "
                        f"WHERE h.batch IN ({marks}) AND h.field = 'status' ORDER BY a.company, a.role_title LIMIT ?",
                        (*batches, limit)).fetchall()
    return [f"{r['company']} ({r['role_title'] or 'role?'})" for r in rows]


def _cold(conn, sweeps: list[dict], wanted: str, shown: int, names: int) -> str:
    """Today's Cold sweeps as ONE line: the count, Show (names, 20 at a time) and one Undo all."""
    total, batches = sum(s["n"] for s in sweeps), [s["batch"] for s in sweeps]
    days = sweeps[-1].get("days", 30)
    names = names or (total if total <= GROUP_OVER else 0)
    listed = _cold_names(conn, batches, names) if names else []
    show = (_button("view", "Show", "List the applications that went Cold", _value(wanted, shown, PAGE))
            if not listed else "")
    hide = (_button("view", "Hide", "Hide the list", _value(wanted, shown, 0))
            if listed and total > GROUP_OVER else "")
    more = (_button("view", "Show more", f"Show {PAGE} more", _value(wanted, shown, names + PAGE))
            if listed and total > len(listed) else "")
    items = "".join(f"<li>{_e(name)}</li>" for name in listed)
    word = "application" if total == 1 else "applications"
    return (f'<div class="small cold" data-id="{_e(",".join(batches))}"><p><span class="tag" title="No reply">Cold'
            f'</span> <b>{total} {word} went Cold</b> (no reply in {days} days) {show}{hide} '
            f'{_button("cold-undo", "Undo all", f"Put all {total} back to Applied")}</p>'
            f'{f"<ul class=cold-names>{items}</ul>" if items else ""}{f"<p>{more}</p>" if more else ""}</div>')


def panel(conn, pid: str, value: str = "", is_open: bool = False) -> str:
    """Folded by default (one summary line); open when the person acted inside it. The page keeps the
    person's open/closed choice for the tab session."""
    wanted, shown, names = _view(value)
    needs, n_needs = _needs(conn, pid)
    today, counts = _today(conn, pid, wanted, shown, names)
    count = f" · {n_needs} need you" if n_needs else ""
    picks = _datalist(conn, pid) if "data-pick" in needs + today else ""
    return (f'<link rel="stylesheet" href="/mail.css"><details class="mail"{" open" if is_open else ""}><summary '
            f'title="Show or hide what job emails changed">{_e(summary_line(counts))}{count}</summary>'
            f'{_mailbox_line(conn, pid)}{needs}{today}{picks}</details>')


# ---- view "settings" ----

def _connect_form(conn, pid: str) -> str:
    typed = _e(signin.address_of(conn, pid) or state.get(conn, imap.ADDRESS_KEY.format(pid)) or "")
    address = _input(f'type="email" name="address" required value="{typed}" placeholder="you@gmail.com"',
                     "The email address you apply with")
    pasted = _input('data-v type="url" placeholder="http://127.0.0.1:…/google/callback?…"',
                    "The address of the page Google opened")
    hidden = {"profile": pid, "t": google_page.tag("connect-gmail", pid), "kind": "gmail"}
    return (f'<form class="row-btns" method="get" action="/google/connect" target="_blank">'
            f'{"".join(f"<input type=hidden name={k} value=\"{_e(v)}\">" for k, v in hidden.items())}{address} '
            f'<button type="submit" class="btn primary small" title="Sign in to Google as this address (read-only), '
            f'in a new tab">Connect</button></form>'
            f'<p class="small muted">On a phone the page after "Allow" cannot load: copy its address, paste it '
            f'here.</p>'
            f'<p class="row-btns" data-id="finish">{pasted} '
            f'{_button("finish", "Finish", "Finish the sign-in with the pasted address")}</p>')


def _imap_form(conn, pid: str) -> str:
    typed = _e(state.get(conn, imap.ADDRESS_KEY.format(pid)) or signin.address_of(conn, pid) or "")
    host = _e(state.get(conn, imap.HOST_KEY.format(pid)) or "")
    fields = (_input(f'data-v type="email" value="{typed}" placeholder="you@example.com"', "The email address"),
              _input('data-pw type="password" autocomplete="off" placeholder="App password"',
                     "An app password from your mail provider (kept in the Mac Keychain only)"),
              _input(f'data-host value="{host}" placeholder="Server (optional)"',
                     "The IMAP server, e.g. imap.gmail.com (empty: guessed from the address)"))
    return (f'<details class="small"><summary title="Read the mailbox over IMAP with an app password">Not Gmail, '
            f'or Google refuses? Use IMAP</summary><p class="row-btns" data-id="imap">{" ".join(fields)} '
            f'{_button("imap", "Use IMAP", "Check the password and read this mailbox over IMAP")}</p></details>')


def _status_block(conn, pid: str) -> str:
    disconnect = " " + _button("disconnect", "Disconnect", "Stop reading this mailbox and forget its sign-in")
    line = _connected_line(conn, pid, "Connected:", disconnect)
    if line:
        return line
    lapsed = " The Google sign-in lapsed: Connect again." if signin.status(conn, pid)["lapsed"] else ""
    return f'<p class="small" data-id="mail">Not connected: nothing is read.{lapsed}</p>'


def _choices(pid: str) -> str:
    current = profile_settings.get(pid)
    at = _input(f'data-v type="time" step="900" required value="{_e(current[profile_settings.MAIL_AT_KEY])}"',
                "The time each day Claude reads the new job email (Check now reads it at once)")
    days = _input(f'data-v type="number" min="1" max="365" value="{current[profile_settings.COLD_KEY]}"',
                  "Days with no reply before an application goes Cold")
    return (f'<p class="small row-btns" data-id="daily-at">Check email daily at {at} '
            f'{_button("daily-at", "Save", "Save the time of the daily email check")} '
            f'{_button("check", "Check now", "Read new email now")}</p>'
            f'<p class="small row-btns" data-id="cold-days">Go Cold after {days} days with no reply '
            f'{_button("cold-days", "Save", "Save the Cold days")}</p>')


def settings(conn, pid: str) -> str:
    return (f'<link rel="stylesheet" href="/mail.css"><div class="mail"><h3 class="small">Job-application email</h3>'
            f'<p class="small muted">The address you apply with (not the app\'s Google account). job-hunt-agent reads it '
            f'read-only and keeps only sender, subject, date, a short snippet and the link.</p>'
            f'{_status_block(conn, pid)}{_connect_form(conn, pid)}{_imap_form(conn, pid)}{_choices(pid)}</div>')


def render(conn, pid: str, view: str = "panel", value: str = "", is_open: bool = False) -> str:
    return settings(conn, pid) if view == "settings" else panel(conn, pid, value, is_open)


# ---- actions ----

def _picked(value: str | None) -> int | None:
    """'Company · Role #123' from the picker -> 123."""
    text = (value or "").strip()
    if not text:
        return None
    tail = text.rsplit("#", 1)[-1]
    if "#" not in text or not tail.isdigit():
        raise ValueError("pick an application from the list")
    return int(tail)


def _use_imap(conn, pid: str, args: dict) -> str:
    """Settings' "Use IMAP": the app password goes to the Keychain only, then one read-only sign-in proves it."""
    email = addr.valid(args.get("value"))
    secret = (args.get("secret") or "").strip()
    if not secret:
        raise ValueError("paste the app password")
    host = (args.get("host") or "").strip() or addr.imap_host(email, addr.provider(email))
    if not imap.store_password(email, secret):
        raise ValueError("the Mac Keychain did not keep the password")
    try:
        imap.ImapReader(email, host).check()
    except MailError as err:
        imap.forget(email)
        raise ValueError(str(err)) from err
    with db.tx(conn):
        state.put(conn, imap.ADDRESS_KEY.format(pid), email)
        state.put(conn, imap.HOST_KEY.format(pid), host)
        state.put(conn, poll.KEY.format(pid, "error"), None)
    return f"Reading {email} over IMAP (read-only)"


def _disconnect(conn, pid: str) -> str:
    signin.disconnect(conn, pid)
    address = state.get(conn, imap.ADDRESS_KEY.format(pid))
    if address:
        imap.forget(address)
    with db.tx(conn):
        for key in (imap.ADDRESS_KEY, imap.HOST_KEY):
            state.put(conn, key.format(pid), None)
        state.put(conn, poll.KEY.format(pid, "error"), None)
    return "Disconnected: nothing is read now"


def _undo_sweep(conn, pid: str, batch: str, who: str) -> int:
    """One sweep of Undo all; one already undone (another tab) counts 0 instead of failing the rest."""
    try:
        return update.undo_cold(conn, pid, batch, who)
    except ValueError:
        return 0


def act(conn, pid: str, args: dict, who: str) -> dict:
    """One button press; answers the redrawn view (+ a message)."""
    name, row_id = args.get("act") or "view", args.get("id")
    out: dict = {}
    if name == "confirm":
        update.confirm(conn, pid, int(row_id), who, app_id=_picked(args.get("app")), status=args.get("status"))
        out["msg"] = "Status updated (Undo is under Today)"
    elif name == "dismiss":
        update.dismiss(conn, pid, int(row_id))
    elif name == "match":
        update.link(conn, pid, int(row_id), _picked(args.get("app")))
        out["msg"] = "Matched"
    elif name == "undo":
        update.undo(conn, pid, int(row_id), who)
        out["msg"] = "Undone"
    elif name == "cold-undo":                                  # one Undo all: every sweep of today's Cold line
        back = sum(_undo_sweep(conn, pid, batch, who) for batch in str(row_id or "").split(",") if batch)
        out["msg"] = f"{back} applications are back to Applied"
    elif name == "cold-days":
        profile_settings.set_cold_days(pid, args.get("value"))
        out["msg"] = "Saved"
    elif name == "daily-at":
        profile_settings.set_mail_at(pid, args.get("value"))
        out["msg"] = "Saved" + ops.schedule_note()
    elif name == "finish":
        try:
            signin.finish(conn, args.get("value") or "", pid)
        except GoogleError as err:                            # shown to the person, not a 500
            raise ValueError(f"Not connected: {err}. Press Connect again.") from err
        out["msg"] = "Connected (read-only)"
    elif name == "imap":
        out["msg"] = _use_imap(conn, pid, args)
    elif name == "disconnect":
        out["msg"] = _disconnect(conn, pid)
    elif name == "check":
        result = nightly.run_person(conn, pid, notify_person=False)
        out["msg"] = result.get("error") or result.get("skipped") or (
            f"Read {result['read']} new emails: {result['line'].removeprefix('Overnight email: ')}")
    elif name != "view":
        raise ValueError(f"unknown action {name!r}")
    value = (args.get("value") or "") if name == "view" else ""
    acted = name != "view" or bool(value)                      # pressed inside the panel: it stays open
    return {**out, "html": render(conn, pid, args.get("view") or "panel", value, acted)}
