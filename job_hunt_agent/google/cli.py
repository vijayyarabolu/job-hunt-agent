"""`job-hunt-agent google setup|login|status|link|add-columns|push-diverged|pull-diverged|compare|sync|selftest|log|linkify-jd|
jd-format|move|owners` (decision 008; move / owners: GOAL11 §5, a person's own Google account): two-way sync
with each person's existing sheet. `link` and `add-columns` always print their plan first and change the Sheet only
after a yes (or --yes)."""
import argparse
import json
import sys

from .. import config, db, gsync, gsync_link
from . import GoogleError, api, auth, base, runner, setup, state

ACTIONS = ("setup", "login", "status", "link", "add-columns", "push-diverged", "pull-diverged", "compare", "sync",
           "selftest", "log", "linkify-jd", "jd-format", "move", "owners")
LOG_LINES = 30


def register(sub) -> None:
    p = sub.add_parser("google", help="two-way sync with your existing Google Sheets (setup, link, sync, ...)")
    p.add_argument("action", choices=ACTIONS)
    p.add_argument("url", nargs="?", help="link: the sheet's URL (from the browser's address bar)")
    p.add_argument("-p", "--profile", default=None)
    p.add_argument("--yes", action="store_true", help="link / add-columns / push-/pull-diverged: apply without asking")
    p.add_argument("--column", default=None, help="push-diverged: the column (default Date Applied); "
                                                  "pull-diverged: one column (default every column)")
    p.add_argument("--rows", default=None, help="jd-format: only these application ids (comma-separated), a pilot")
    p.add_argument("--launchd", action="store_true", help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_google)


def _print(data) -> None:
    print(json.dumps(data, indent=1, default=str))


def token_line(token: dict) -> str:
    if not token["configured"]:
        return "not set up (job-hunt-agent google setup)"
    if token["lapsed"]:
        return "sign-in lapsed: run `job-hunt-agent google login`"
    if not token["connected"]:
        return "client saved, not signed in (job-hunt-agent google login)"
    if token["days_left"] is not None:
        return f"connected (Testing client: sign-in good for {token['days_left']} more days)"
    return "connected (production client: no 7-day expiry)"


def sync_line(conn) -> str:
    """One line for `doctor` and `google status`."""
    h = runner.health(conn)
    if not h["linked"]:
        return "sheet sync: no sheet linked yet (job-hunt-agent google link --profile <p> <sheet url>)"
    errors = "; ".join(f"{p}: {e}" for p, e in h["errors"].items())
    return (f"sheet sync: {'healthy' if h['healthy'] else 'NOT healthy'} - linked {', '.join(h['linked'])}; "
            f"last sync {h['last_sync_at'] or 'never'}, last good {h['last_ok_at'] or 'never'}; conflicts (7 days) "
            f"{h['conflicts_7d']}; rows gone from the Sheet {h['missing_rows']}" + (f"; errors: {errors}" if errors
                                                                                   else "") + _diverged_text(h))


def _diverged_text(h: dict) -> str:
    """Cells that already differed when linked (left alone by design): said, so 'healthy' never hides them."""
    if not h.get("diverged"):
        return "; cells that differ between Sheet and app: 0"
    parts = [f"{pid} " + ", ".join(f"{col} {n}" for col, n in sorted(cols.items())) for pid, cols in h["diverged"].items()]
    return (f"; cells that differ between Sheet and app (left as they are): {'; '.join(parts)} "
            "(preview: job-hunt-agent google pull-diverged --profile <p> for the sheet's values, or push-diverged "
            "--column <column> for the app's)")


def _status(conn) -> int:
    print(f"Google: {token_line(auth.status())}")
    for pid in config.profile_ids():
        sid = gsync.linked_sid(conn, pid)
        print(f"{pid}: " + (f"https://docs.google.com/spreadsheets/d/{sid}/edit" if sid else "no sheet linked"))
    print(sync_line(conn))
    print(f"changes waiting since {state.dirty(conn)[0] or '-'}")
    return 0


LOCK_WAIT_S = 120


def _locked(fn):
    """Run a Sheet-changing command under google.lock (never alongside the server's sync)."""
    with state.lock(wait_s=LOCK_WAIT_S) as got:
        if not got:
            raise GoogleError("another sync is still running; try again in a minute")
        return fn()


def _confirm(lines: list[str], yes: bool, question: str) -> bool:
    print("\n".join(lines))
    return yes or setup._ask(f"\n{question} [y/N] ").lower() == "y"


def _link(conn, args) -> int:
    pid = args.profile or config.default_profile()
    url = args.url or gsync.linked_sid(conn, pid)
    if not url:
        print("usage: job-hunt-agent google link --profile <p> '<sheet url>'", file=sys.stderr)
        return 2
    plan = gsync_link.link(conn, pid, url)
    if not _confirm(plan["lines"], args.yes, "Link this sheet (adds the hidden key column and the keys)?"):
        print("nothing changed")
        return 0
    _print(_locked(lambda: gsync_link.link(conn, pid, url, apply=True))["first_sync"])
    return 0


def _add_columns(conn, args) -> int:
    pid = args.profile or config.default_profile()
    plan = gsync_link.add_columns(conn, pid)
    if plan["lines"][0].startswith("nothing to add"):
        print(plan["lines"][0])
        return 0
    if not _confirm(plan["lines"], args.yes, "Insert the column(s) into your sheet?"):
        print("nothing changed")
        return 0
    _print(_locked(lambda: gsync_link.add_columns(conn, pid, apply=True)).get("sync"))
    return 0


def _push_diverged(conn, args) -> int:
    pid = args.profile or config.default_profile()
    column = args.column or "Date Applied"
    plan = gsync_link.push_diverged(conn, pid, column)
    if not plan["count"]:
        print(plan["lines"][0])
        return 0
    if not _confirm(plan["lines"], args.yes, "Write the app's values into your sheet?"):
        print("nothing changed")
        return 0
    _print(_locked(lambda: gsync_link.push_diverged(conn, pid, column, apply=True)).get("sync"))
    return 0


def _pull_diverged(conn, args) -> int:
    """the person's sheet wins; copy its values into the app (the sheet is not written)."""
    pid = args.profile or config.default_profile()
    plan = gsync_link.pull_diverged(conn, pid, args.column)
    if not plan["count"]:
        print(plan["lines"][0])
        return 0
    if not _confirm(plan["lines"], args.yes, "Copy your sheet's values into the app?"):
        print("nothing changed")
        return 0
    _print(_locked(lambda: gsync_link.pull_diverged(conn, pid, args.column, apply=True)).get("sync"))
    return 0


def _compare(conn, args) -> int:
    """Read-only: the live sheet against the app, every cell (`--profile all`: every linked person; exit 1 when
    anything differs)."""
    pids = gsync.profiles_linked(conn) if args.profile in (None, "all") else [args.profile]
    total = 0
    for pid in pids:
        found = gsync_link.compare(conn, pid)
        total += len(found["differences"]) + found["waiting_to_append"] + \
            sum(t["untaken_typed_rows"] + t["unknown_keys"] for t in found["tabs"].values())
        for title, t in found["tabs"].items():
            print(f"{pid} / {title}: {t['rows']} rows, {t['keyed']} keyed, {t['cells']} cells compared, "
                  f"unknown keys {t['unknown_keys']}, typed rows not in the app {t['untaken_typed_rows']}")
        print(f"{pid}: cells that differ {len(found['differences'])}; app rows waiting to be appended "
              f"{found['waiting_to_append']}")
        for title, row, header, s, d in found["differences"][:20]:
            print(f"  {title} row {row} {header}: sheet {s[:40]!r} | app {d[:40]!r}")
    return 1 if total else 0


def _linkify(conn, args) -> int:
    """posting links already in JD cells become real links (text never sent); --yes applies."""
    from .. import gsync_linkify
    pids = gsync.profiles_linked(conn) if args.profile in (None, "all") else [args.profile]
    for pid in pids:
        if not args.yes:
            for entry in gsync_linkify.plan(conn, pid):
                print(f"{pid} / {entry['target'].title}: {entry['counts']} -> {len(entry['fix'])} to link")
            continue
        for title, r in gsync_linkify.apply(conn, pid).items():
            print(f"{pid} / {title}: linked {r['linked']}, repaired {r['repaired']}, not linked {r['not_linked']}, "
                  f"before {r['counts_before']}, left alone {len(r['left'])}")
    if not args.yes:
        print("dry run: add --yes to write the links (only the links; no cell text is sent)")
    return 0


def _jd_format(conn, args) -> int:
    """The JD cell as the older rows have it (title link, clipped, JD note; full text in the hidden JD Text column)
    and resumes as Drive chips; without --rows also every row the app wrote clipped at normal height; dry run without
    --yes."""
    from .. import gsync_jd, gsync_oneline
    pids = gsync.profiles_linked(conn) if args.profile in (None, "all") else [args.profile]
    rows = {int(r) for r in args.rows.split(",")} if args.rows else None
    for pid in pids:
        todo = [a for a in gsync_jd.old_style(conn, pid) if rows is None or a in rows]
        if not args.yes:
            print(f"{pid}: {len(todo)} JD cells in the URL + JD style to convert: {todo}")
            continue
        with state.lock(wait_s=LOCK_WAIT_S) as held:
            if not held:
                raise ValueError("the Sheet sync is busy: try again in a minute")
            done = gsync_jd.apply(conn, pid, rows)
            if rows is None:                                  # all: rows the app wrote get clip + normal height
                done["clipped_app_rows"] = gsync_oneline.fix_app_rows(conn, pid)
            _print({pid: done})
    if not args.yes:
        print("dry run: add --yes to convert them (adds the hidden JD Text column first)")
    return 0


def _log(conn, pid: str | None) -> int:
    rows = conn.execute("SELECT at, profile, tab, app_id, kind, column_name, sheet_value, app_value, winner FROM "
                        "gsync_log WHERE (? IS NULL OR profile = ?) ORDER BY id DESC LIMIT ?",
                        (pid, pid, LOG_LINES)).fetchall()
    for r in reversed(rows):
        detail = (f" {r['column_name']}: sheet {str(r['sheet_value'])[:40]!r} app {str(r['app_value'])[:40]!r}"
                  if r["column_name"] else "")
        print(f"{r['at']} {r['profile']}/{r['tab'] or '-'} #{r['app_id'] or '-'} {r['kind']}{detail}"
              + (f" -> {r['winner']} wins" if r["winner"] else ""))
    return 0


def _sync(conn, args) -> int:
    report = runner.run(conn)
    if args.launchd:                                          # launchd: log one line, never fail the job
        print(json.dumps(report, default=str))
        return 0
    _print(report)
    return 1 if "error" in report or "errors" in report else 0


def _move(conn, args) -> int:
    """the person's files from the owner's Drive to their own (resumable)."""
    from .. import google_move
    _print(google_move.run(conn, args.profile or ""))
    return 0


def _owners(conn, args) -> int:
    """who owns the person's newest files (approved resumes, Docs, JD Docs), read from Drive."""
    from . import accounts
    pid = args.profile or ""
    ids = [r["file_id"] for r in conn.execute(
        "SELECT file_id FROM (SELECT file_id, uploaded_at AS at FROM resume_uploads WHERE profile = ? UNION ALL "
        "SELECT file_id, saved_at FROM jd_docs WHERE profile = ? AND file_id IS NOT NULL) ORDER BY at DESC LIMIT 10",
        (pid, pid))]
    out = []
    for file_id in ids:
        try:
            meta = accounts.with_fallback(pid, api.get, f"{base('drive')}/files/{file_id}",
                                          {"fields": "name,owners(emailAddress)"})
        except GoogleError as err:
            meta = {"name": file_id, "error": str(err)}
        out.append({"name": meta.get("name"), "owners": [o["emailAddress"] for o in meta.get("owners") or []],
                    **({"error": meta["error"]} if "error" in meta else {})})
    _print({"account": accounts.status(pid), "files": out})
    return 0


def _selftest(conn) -> int:
    steps = setup.selftest(conn)
    for name, ok, detail in steps:
        print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
    return 0 if all(ok for _, ok, _ in steps) else 1


def cmd_google(args: argparse.Namespace) -> int:
    conn = db.connect()
    try:
        if args.action == "setup":
            return setup.setup(conn)
        if args.action == "login":
            auth.login()
            print("connected to Google; the next sync catches up")
            return 0
        return {"status": lambda: _status(conn), "link": lambda: _link(conn, args),
                "add-columns": lambda: _add_columns(conn, args), "sync": lambda: _sync(conn, args),
                "push-diverged": lambda: _push_diverged(conn, args),
                "pull-diverged": lambda: _pull_diverged(conn, args),
                "compare": lambda: _compare(conn, args), "linkify-jd": lambda: _linkify(conn, args),
                "jd-format": lambda: _jd_format(conn, args),
                "selftest": lambda: _selftest(conn), "log": lambda: _log(conn, args.profile),
                "move": lambda: _move(conn, args), "owners": lambda: _owners(conn, args)}[args.action]()
    except (GoogleError, ValueError, config.ConfigError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 0 if args.launchd else 2
    finally:
        conn.close()
