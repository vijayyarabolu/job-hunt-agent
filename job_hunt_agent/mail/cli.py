"""`job-hunt-agent mail ...`: connect each person's Gmail (read-only), poll now, see what was read, the Cold rule.
`nightly` is the daily job (each person whose daily time has come; --now: everyone at once) and `review`
shows what Claude makes of a day's mail without changing anything (a dry run, for checking by hand)."""
import argparse
import json
import webbrowser
from datetime import date, datetime, timezone

from .. import config, db
from ..google import GoogleError, state
from . import MailError, address as addr, claude, classify, imap, judge, match, nightly, poll, prefilter, signin, update

ACTIONS = ("setup", "login", "link", "finish", "imap", "poll", "status", "list", "cold", "redo", "nightly", "review")
SETUP = """\
Let Job Hunt Agent read job emails (read-only), one time, about 5 minutes, in the same Google Cloud project you made
for Sheets (Settings > Setup lists every step):

 1. Turn on the Gmail API: https://console.cloud.google.com/apis/library/gmail.googleapis.com
 2. Data access: https://console.cloud.google.com/auth/scopes -> add
      https://www.googleapis.com/auth/gmail.readonly
    Read-only: the app can never send, delete or change mail.

Then each person connects the address they apply with: Settings > Accounts > Job-application email -> type the
address -> Connect. Or from a terminal: job-hunt-agent mail login --profile <p> --address <the address>

Not Gmail / Google Workspace? Use IMAP with an app password from your mail provider (same read-only result):
 job-hunt-agent mail imap --profile <p> --address <the address> [--host imap.example.com]
 On a Mac the password goes to the macOS Keychain and never into the repo or the database (IMAP needs a Mac).
"""


def register(sub) -> None:
    p = sub.add_parser("mail", help="read job emails (read-only) and update application status")
    p.add_argument("action", choices=ACTIONS)
    p.add_argument("answer", nargs="?", help="finish: the address of the page Google opened after Allow")
    p.add_argument("-p", "--profile", dest="person", default=None, help="one person (default: everyone)")
    p.add_argument("--address", default=None, help="login / link / imap: the job-application address")
    p.add_argument("--host", default=None, help="imap: the IMAP server (default: guessed from the address)")
    p.add_argument("--day", default=None, help="list: YYYY-MM-DD (default today)")
    p.add_argument("--yes", action="store_true", help="cold / redo: do it now (default: only list)")
    p.add_argument("--now", action="store_true", help="nightly: everyone now, whatever their daily time")
    p.set_defaults(func=cmd_mail)


def _people(args) -> list[str]:
    if args.person and args.person not in config.profile_ids():
        raise SystemExit(f"error: unknown profile {args.person!r}")
    return [args.person] if args.person else config.profile_ids()


def _one(args) -> str:
    if not args.person:
        raise SystemExit("error: say whose mail: --profile <p>")
    return _people(args)[0]


def cmd_mail(args: argparse.Namespace) -> int:
    conn = db.connect()
    try:
        return HANDLERS[args.action](conn, args)
    except (GoogleError, ValueError) as err:
        print(f"error: {err}")
        return 1
    finally:
        conn.close()


def _setup(conn, args) -> int:
    print(SETUP)
    return 0


def _login(conn, args) -> int:
    pid = _one(args)
    if not args.address:
        raise SystemExit("error: --address <the job-application Gmail address>")
    print(f"A browser window opens: sign in as {args.address} and allow read-only access.")
    signin.login(conn, pid, args.address, open_browser=webbrowser.open)
    print(f"Connected: {signin.status(conn, pid)['account'] or '(address unknown)'} (read-only)")
    return 0


def _link(conn, args) -> int:
    print(signin.begin(conn, _one(args), args.address))
    print("Open this link, allow, then: job-hunt-agent mail finish '<the address of the page that opened>'")
    return 0


def _finish(conn, args) -> int:
    pid = signin.finish(conn, args.answer or "")
    print(f"Connected {pid}: {signin.status(conn, pid)['account'] or '(address unknown)'} (read-only)")
    return 0


def _imap(conn, args) -> int:
    pid = _one(args)
    if not args.address:
        raise SystemExit("error: --address <the job-application address>")
    email = addr.valid(args.address)
    host = args.host or addr.imap_host(email, addr.provider(email))
    print("macOS asks for the app password (it is kept in the Keychain only).")
    if not imap.store_password(email):
        print("error: the password was not saved")
        return 1
    try:
        imap.ImapReader(email, host).check()
    except MailError as err:
        print(f"error: {err}")
        return 1
    with db.tx(conn):
        state.put(conn, imap.ADDRESS_KEY.format(pid), email)
        state.put(conn, imap.HOST_KEY.format(pid), host)
    print(f"IMAP set for {pid}: {email} on {host} (read-only)")
    return 0


def _poll(conn, args) -> int:
    now = db.utcnow()
    report = {pid: {"cold": poll.cold_once_a_day(conn, pid, now),
                    "mail": nightly.run_person(conn, pid, now, notify_person=False)} for pid in _people(args)}
    print(json.dumps(report, indent=1, default=str))
    return 0


def _counts(conn, pid: str, day: str) -> dict:
    rows = conn.execute("SELECT state, kind, count(*) AS n FROM mail_messages WHERE profile = ? AND "
                        "date(sent_at, 'localtime') = ? GROUP BY state, kind", (pid, day)).fetchall()
    return {f"{r['state']}/{r['kind']}": r["n"] for r in rows}


def _status(conn, args) -> int:
    day = date.today().isoformat()
    for pid in _people(args):
        gmail = signin.status(conn, pid)
        address = state.get(conn, imap.ADDRESS_KEY.format(pid))
        via = (f"Gmail {gmail['account'] or ''}" if gmail["connected"] else f"IMAP {address}" if address else
               "not connected (job-hunt-agent mail setup)" + (" - sign-in lapsed" if gmail["lapsed"] else ""))
        checked = state.get(conn, poll.KEY.format(pid, "checked_at")) or "never"
        error = state.get(conn, poll.KEY.format(pid, "error"))
        print(f"{pid}: {via}; last check {checked}" + (f"; error: {error}" if error else ""))
        print(f"  today ({day}): {_counts(conn, pid, day) or 'nothing read yet'}")
        print(f"  Cold after {update.cold_days(pid)} days; sweeps kept: {len(update.sweeps(conn, pid))}")
    return 0


def doctor_lines(conn) -> list[str]:
    """One line per person for `job-hunt-agent doctor`."""
    lines, day = [], date.today().isoformat()
    for pid in config.profile_ids():
        gmail = signin.status(conn, pid)
        address = state.get(conn, imap.ADDRESS_KEY.format(pid))
        if not (gmail["connected"] or address):
            lines.append(f"mail {pid}: not connected" + (" (sign-in lapsed)" if gmail["lapsed"] else "")
                         + " - job-hunt-agent mail setup")
            continue
        error = state.get(conn, poll.KEY.format(pid, "error"))
        counts = _counts(conn, pid, day)
        read = sum(counts.values())
        needs = sum(n for k, n in counts.items() if k.startswith("needs/"))
        lines.append(f"mail {pid}: {'Gmail' if gmail['connected'] else 'IMAP'} read-only, last check "
                     f"{state.get(conn, poll.KEY.format(pid, 'checked_at')) or 'never'}; today {read} read, "
                     f"{needs} wait in Needs you" + (f"; ERROR {error}" if error else ""))
    return lines


def _list(conn, args) -> int:
    day = args.day or date.today().isoformat()
    for pid in _people(args):
        rows = conn.execute(
            "SELECT m.*, a.company, a.role_title FROM mail_messages m LEFT JOIN applications a ON a.id = m.app_id "
            "WHERE m.profile = ? AND date(m.sent_at, 'localtime') = ? ORDER BY m.sent_at", (pid, day)).fetchall()
        print(f"== {pid} {day}: {len(rows)} emails")
        for r in rows:
            when = datetime.fromisoformat(r["sent_at"].replace("Z", "+00:00")).astimezone().strftime("%H:%M")
            target = f"{r['company']} | {r['role_title']}" if r["company"] else (r["company_guess"] or "-")
            print(f"{when} {r['state']:9} {r['kind']:9} {r['method']:9} {r['to_status'] or '':9} {target} || "
                  f"{r['sender'] or '(not job mail: nothing kept)'} || {r['subject'] or ''}"
                  + (f" || {r['reason']}" if r["reason"] else "")
                  + (f" || Claude ({r['confidence']}): {r['claude_reason']}" if r["claude_reason"] else ""))
    return 0


def _cold(conn, args) -> int:
    today = date.today()
    for pid in _people(args):
        if args.yes:
            result = update.cold_sweep(conn, pid, today)
            print(f"{pid}: {result['cold']} applications went Cold (batch {result.get('batch')})")
        else:
            print(f"{pid}: {len(update.cold_due(conn, pid, today))} applications are due to go Cold after "
                  f"{update.cold_days(pid)} days (--yes makes them Cold now; the server does it once a day)")
    return 0


def _redo(conn, args) -> int:
    """After a rules fix: read again the emails that changed nothing (they are listed first; --yes does it)."""
    pid = _one(args)
    waiting = conn.execute(f"SELECT state, count(*) FROM mail_messages WHERE profile = ? AND state IN "
                           f"({','.join('?' * len(poll.REDO_STATES))}) GROUP BY state", (pid, *poll.REDO_STATES))
    print(f"{pid}: emails that changed nothing: {dict(waiting.fetchall()) or 'none'}")
    if not args.yes:
        print("--yes forgets these and reads them again now (pre-filter + Claude, as at night)")
        return 0
    print(f"forgot {poll.redo(conn, pid)}; read again: {nightly.run_person(conn, pid, notify_person=False)}")
    return 0


def _nightly(conn, args) -> int:
    """The daily job: everyone whose daily time has come (--now: everyone)."""
    report = nightly.run(conn, force=args.now)
    print(json.dumps(report, indent=1, default=str))
    return 0


def _review(conn, args) -> int:
    """Dry run: every email of the day (--day, default today) through the pre-filter and Claude, printed with what
    would happen. Writes nothing (no status, no mail row, no cursor); Claude is called as at night."""
    pid, day = _one(args), date.fromisoformat(args.day or date.today().isoformat())
    reader = poll.reader_for(conn, pid)
    if reader is None:
        raise ValueError(f"{pid}'s mail is not connected")
    start = datetime.combine(day, datetime.min.time()).astimezone().astimezone(timezone.utc)
    mails = [m for m in reader.since(max(start, poll.start())) if m.sent_at.astimezone().date() == day]
    apps = match.candidates(conn, pid)
    rows = []
    for mail in mails:
        found = match.find(mail.sender, mail.subject, mail.text, apps)
        keep, why = prefilter.decide(mail.sender, mail.subject, found)
        rows.append((mail, found, classify.classify(mail.subject, mail.text, found.from_ats) if keep else None, why))
    asked = [r for r in rows if r[2] is not None]
    answers = claude.read(pid, [(m, f.apps) for m, f, _, _ in asked])
    print(f"== {pid} {day}: {len(mails)} emails, {len(asked)} to Claude, usage {answers.usage}"
          + (f", STOPPED: {answers.stopped}" if answers.stopped else ""))
    by_mail = {id(m): answers.answers.get(i) for i, (m, *_) in enumerate(asked)}
    for mail, found, verdict, why in rows:
        when = mail.sent_at.astimezone().strftime("%H:%M")
        if verdict is None:
            print(f"{when} SKIP ({why}) || {mail.sender} || {mail.subject}")
            continue
        answer = by_mail[id(mail)]
        if answer is None:
            print(f"{when} NO ANSWER || {mail.sender} || {mail.subject}")
            continue
        action, app, reason = judge.plan(found, verdict, answer)
        target = f"#{app.id} {app.company} | {app.role_title}" if app else (found.company or "-")
        print(f"{when} {action:8} claude={answer['kind']}/{answer['confidence']} rules={verdict.kind} -> {target}"
              f"{f' ({reason})' if reason else ''} || {mail.sender} || {mail.subject}\n      \"{answer['reason']}\"")
    return 0


HANDLERS = {"setup": _setup, "login": _login, "link": _link, "finish": _finish, "imap": _imap, "poll": _poll,
            "status": _status, "list": _list, "cold": _cold, "redo": _redo, "nightly": _nightly,
            "review": _review}
