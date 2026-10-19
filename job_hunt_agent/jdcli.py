"""`job-hunt-agent jd-backfill`: JD snapshots for every existing application; see jdbackfill.py.
`job-hunt-agent jd-docs`: the full JD as a Google Doc per application; see jd_doc.py."""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

EVIDENCE = Path("docs/evidence/v6")


def register(sub) -> None:
    p = sub.add_parser("jd-backfill", help="keep every application's JD as text: snapshots + posting gone")
    p.add_argument("--plan", action="store_true", help="only count what would happen (no requests, no writes)")
    p.add_argument("-p", "--profile", default="all", help="one profile, or all (default)")
    p.add_argument("--no-fetch", action="store_true", help="only text the app already has (no requests)")
    p.add_argument("--no-fill", action="store_true", help="do not write the JD cell of link-only rows")
    p.add_argument("--limit", type=int, default=None, help="at most this many posting requests")
    p.add_argument("--report", default=str(EVIDENCE / "jd_backfill.md"))
    p.set_defaults(func=cmd_jd)
    d = sub.add_parser("jd-docs", help="the full JD as a Google Doc per application, linked from the JD cell")
    d.add_argument("-p", "--profile", default="all", help="one profile, or all (default)")
    d.add_argument("--app", type=int, default=None, help="only this application id")
    d.add_argument("--limit", type=int, default=None, help="at most this many Docs written (created or updated)")
    d.add_argument("--pace", type=float, default=None, help="seconds between Drive writes (default 0.5)")
    d.add_argument("--no-fetch", action="store_true", help="rows with no JD text: no posting fetch")
    d.add_argument("--no-link", action="store_true", help="do not link the Sheet's JD cells")
    d.add_argument("--link-only", action="store_true", help="only link the JD cells of rows that have a Doc")
    d.set_defaults(func=cmd_docs)


def cmd_jd(args: argparse.Namespace) -> int:
    from . import config, db, jdbackfill
    pids = config.profile_ids() if args.profile == "all" else [args.profile]
    conn = db.connect()
    try:
        if args.plan:
            print(json.dumps(jdbackfill.plan(conn, pids), indent=1))
            return 0
        result = jdbackfill.run(conn, pids, fetch=not args.no_fetch, fill=not args.no_fill, limit=args.limit)
    finally:
        conn.close()
    report = Path(args.report)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(jdbackfill.report_md(result, datetime.now().strftime("%Y-%m-%d %H:%M")))
    no_url = report.with_name("jd_no_url_rows.txt")               # local only (.gitignore): company + role per row
    no_url.write_text(jdbackfill.no_url_md(result))
    print(json.dumps({"counts": result["counts"], "fetch_requests": result["fetch_requests"],
                      "filled": len(result["filled"]), "gone": len(result["gone"]),
                      "no_posting_link": len(result["no_url"])}, indent=1))
    print(f"report: {report}; rows with no posting link: {no_url}", file=sys.stderr)
    return 0


def cmd_docs(args: argparse.Namespace) -> int:
    from . import config, db, jd_doc
    pids = config.profile_ids() if args.profile == "all" else [args.profile]
    conn = db.connect()
    try:
        if args.app is not None:
            done = jd_doc.save(conn, args.app, fetch=not args.no_fetch)
            sheet = {} if args.no_link or done["state"] != "saved" else jd_doc.link_rows(conn, done["profile"], {args.app})
            print(json.dumps({**done, "sheet": dict(sheet)}, indent=1))
        elif args.link_only:
            print(json.dumps({pid: dict(jd_doc.link_rows(conn, pid)) for pid in pids}, indent=1))
        else:
            result = jd_doc.backfill(conn, pids, limit=args.limit, pace_s=jd_doc.PACE_S if args.pace is None
                                     else args.pace, fetch=not args.no_fetch, link=not args.no_link,
                                     say=lambda line: print(line, file=sys.stderr, flush=True))
            print(json.dumps(result, indent=1))
    finally:
        conn.close()
    return 0
