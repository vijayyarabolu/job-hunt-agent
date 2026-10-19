"""`job-hunt-agent autofill ...`: `canary` runs the Autofill canary now (fixtures + live pages; --no-live: fixtures
only); `nightly` waits for a running fetch to finish, then runs the canary (schedule it with cron if you want it
every night; it needs node and Chrome)."""
import argparse
import json
import sys

from .. import db
from . import canary


def register(sub) -> None:
    p = sub.add_parser("autofill", help="the Autofill extension's nightly canary")
    p.add_argument("action", choices=("canary", "nightly"))
    p.add_argument("--no-live", action="store_true", help="canary: saved fixtures only, no live apply pages")
    p.set_defaults(func=cmd_autofill)


def _canary(live: bool) -> dict:
    conn = db.connect()
    try:
        return canary.run(conn, live=live)
    finally:
        conn.close()


def cmd_autofill(args: argparse.Namespace) -> int:
    out = {}
    if args.action == "nightly":
        out["quiet"] = canary.wait_quiet()
    try:
        out.update(_canary(live=not args.no_live))
    except (OSError, RuntimeError) as err:
        print(f"error: canary: {err}", file=sys.stderr)
        print(json.dumps(out, indent=1))
        return 2
    print(json.dumps(out, indent=1))
    return 1 if out.get("drops") and args.action == "canary" else 0   # nightly: a drop is a notice, not a failed job
