"""`job-hunt-agent h1b ...`: refresh = the monthly job (the server runs it on the 5th) (download a new DOL quarter, load the last 8);
load = read the files already in data/h1b again; rollup = the companies again from the
stored employers; report = match rate, labels of the open US jobs, the biggest filers
job-hunt-agent does not fetch; company NAME = what the filings say about one company; boards = look for the career boards
of the biggest data / AI filers job-hunt-agent does not fetch (--add puts the verified ones in config/companies.yaml)."""
import argparse
import json

from .. import db
from . import boards, label, load, names, refresh, report

ACTIONS = ("refresh", "load", "rollup", "jd", "report", "company", "boards")


def register(sub) -> None:
    p = sub.add_parser("h1b", help="public H-1B / PERM filings: which companies sponsor now")
    p.add_argument("action", choices=ACTIONS)
    p.add_argument("name", nargs="?", help="company: the company's name")
    p.add_argument("--force", action="store_true", help="refresh: load again even with no new file")
    p.add_argument("--limit", type=int, default=200, help="boards: how many of the biggest unfetched filers")
    p.add_argument("--cap", type=int, default=50, help="boards: how many cap-exempt employers after them")
    p.add_argument("--add", action="store_true", help="boards: add the verified boards to config/companies.yaml")
    p.set_defaults(func=cmd_h1b)


def _refresh(conn, force: bool) -> int:
    try:
        done = refresh.refresh(conn, force=force)
    except (refresh.RefreshError, OSError, ValueError) as err:
        print(f"h1b refresh failed: {err}")
        return 1
    if not done["changed"]:
        print("h1b: no new DOL file; nothing to do")
        return 0
    print(f"h1b: loaded {done['load']['companies']:,} companies, data through {done['load']['ref']}"
          f" (downloaded {len(done['downloaded'])}, removed {len(done['removed'])})")
    return 0


def cmd_h1b(args: argparse.Namespace) -> int:
    conn = db.connect()
    if args.action == "refresh":
        return _refresh(conn, args.force)
    if args.action == "load":
        result = load.load_dir(conn)
        label.forget()
        print(f"h1b: {result['employers']:,} employers, {result['companies']:,} companies, data through {result['ref']}")
        return 0
    if args.action == "rollup":                     # after a rule / alias change: no need to read the files again
        print(f"h1b: {load.rerollup(conn):,} companies")
        label.forget()
        return 0
    if args.action == "jd":                         # the stored "no sponsorship" labels again (exceptions)
        from .. import resignal
        print(json.dumps(resignal.sponsorship_labels(conn)))
        return 0
    if args.action == "report":
        print(report.text(report.build(conn)))
        return 0
    if args.action == "boards":
        print(boards.text(boards.run(args.limit, args.cap, write=args.add)))
        return 0
    if not args.name:
        print("error: h1b company needs the company's name")
        return 2
    row = conn.execute("SELECT * FROM h1b_companies WHERE key = ?", (names.key(args.name),)).fetchone()
    if row is None:
        print(f"{args.name}: no H-1B or PERM filings found (key {names.key(args.name)!r})")
        return 1
    print(json.dumps(dict(row), indent=2))
    return 0
