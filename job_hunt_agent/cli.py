"""Command-line entry point: `job-hunt-agent <command> ...`."""
import argparse
import json
import sys
import time
from pathlib import Path

from . import titles
from .ats import ADAPTERS, Ctx

SMOKE_BOARDS = [                       # no SmartRecruiters: its robots.txt disallows us (decision 005)
    {"ats": "greenhouse", "key": "anthropic", "company": "Anthropic"},
    {"ats": "lever", "key": "palantir", "company": "Palantir"},
    {"ats": "ashby", "key": "openai", "company": "OpenAI"},
    {"ats": "amazon", "key": "amazon", "company": "Amazon"},
    {"ats": "workday", "key": "nvidia/wd5/NVIDIAExternalCareerSite", "company": "NVIDIA"},
]
SMOKE_TERMS = ("machine learning engineer", "AI engineer", "data scientist")
SAMPLES = 3


DIGEST_LOCK_WAIT_S = 15 * 60

def cmd_smoke(args: argparse.Namespace) -> int:
    """Live smoke test: fetch one known board per ATS, print counts and sample matched jobs."""
    ctx = Ctx(matches=lambda t: bool(titles.match(t)), search_terms=SMOKE_TERMS)
    failures = 0
    for board in SMOKE_BOARDS:
        if args.ats and board["ats"] != args.ats:
            continue
        started = time.monotonic()
        result = ADAPTERS[board["ats"]](board, ctx)
        elapsed = time.monotonic() - started
        matched = [j for j in result.items if titles.match(j["title"])]
        status = "OK" if result.error is None and result.items else "FAIL"
        failures += status == "FAIL"
        print(f"\n[{status}] {board['ats']:<16} {board['key']}  raw={result.n_raw} parsed={len(result.items)} "
              f"parse_err={result.n_parse_err} matched={len(matched)}  {elapsed:.1f}s")
        if result.error:
            print(f"    error: {result.error}")
        for job in sorted(matched, key=lambda j: j["posted_at"] or "", reverse=True)[:SAMPLES]:
            salary = f"${job['salary_min']:,.0f}-${job['salary_max']:,.0f}" if job["salary_min"] else "-"
            print(f"    - {job['title'][:60]} | {job['location'][:35]} | posted {job['posted_at']} "
                  f"({job['precision']}) | us={job['is_us']} | {salary} | jd={len(job['description'])}ch")
            print(f"      {job['uid']}  {job['apply_url'][:90]}")
    return 1 if failures else 0


def cmd_fetch(args: argparse.Namespace) -> int:
    from . import fetch, runstats
    runstats.limit_cpu()                 # RLIMIT_CPU backstop for this process only (never the web server)
    summary = fetch.run(trigger=args.trigger, force=args.force, only=args.board, verbose=not args.quiet)
    print(json.dumps(summary, indent=1))
    return 0


def _discover_crawl(args: argparse.Namespace) -> dict | None:
    """GOAL2 B (decision 005): Common Crawl tokens -> live verification -> companies.discovered.yaml."""
    import logging
    from . import db, discover_cc, discover_run, discover_seed, registry, seedlists
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    conn = db.connect()
    atss = tuple(args.ats.split(",")) if args.ats else None
    if args.refresh:
        return discover_run.refresh(conn)
    report: dict = {}
    if args.lists:
        report["lists"] = seedlists.refresh()
    if args.seeds:                                  # MIT tenant seed lists for the new systems
        from . import discover_more
        report["seeds"] = discover_more.import_seeds(atss)
    if args.cc:                                     # --ats limits the harvest (only the new systems' prefixes)
        found = discover_cc.harvest(atss=atss) if atss else discover_cc.harvest()
        report["tokens"] = {a: len(t) for a, t in found.items()}
    if args.verify:
        report["verify"] = discover_run.verify(conn, atss=atss, limit=args.limit)
    if args.seed:
        report["seed"] = discover_seed.seed(conn, limit=args.limit)
    if args.build:                                  # --ats stages which new systems are admitted
        report["build"] = registry.build(conn, atss) if atss else registry.build(conn)
    return report or None


def cmd_discover(args: argparse.Namespace) -> int:
    from . import discover
    crawl = _discover_crawl(args)
    if crawl is not None:
        print(json.dumps(crawl, indent=1))
        return 0
    if args.reverify:
        report = discover.reverify()
    else:
        report = discover.verify_workday_candidates() if args.workday else discover.discover()
    print(json.dumps(report, indent=1))
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    from . import discover
    try:
        print(json.dumps(discover.add(args.url, args.name), indent=1))
    except ValueError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    return 0


def cmd_tracker(args: argparse.Namespace) -> int:
    from . import config, db, tracker
    conn = db.connect()
    if args.action == "import":
        print(json.dumps(tracker.import_tracker(args.file, args.profile, conn=conn), indent=1))
    elif args.action == "import-mapped":
        return _import_mapped(args, conn)
    elif args.action == "export":                                # each person's own layout, or --full
        from . import export_full
        out = export_full.export(conn, args.profile or config.default_profile(), "full" if args.full else "original",
                                 Path(args.out) if args.out else None)
        print(f"{out}  {export_full.summary(out)}")
    else:
        for row in tracker.followups_due(conn, args.profile):
            print(f"#{row['id']:<4} {row['company'][:28]:<28} {row['role_title'][:40]:<40} applied {row['applied_on']} "
                  f"FU1 {row['due_1']} FU2 {row['due_2']}")
    return 0


def _import_mapped(args: argparse.Namespace, conn) -> int:
    """A workbook with its own layout, mapped by profiles/<p>/tracker_map.yaml."""
    from . import config, tracker_map
    if not (args.profile and args.file):
        print("import-mapped needs --profile and --file", file=sys.stderr)
        return 2
    mapping = Path(args.map) if args.map else config.PROFILES / args.profile / "tracker_map.yaml"
    result = tracker_map.import_mapped(Path(args.file), args.profile, mapping, conn, dry=args.dry_run)
    print(json.dumps(result, indent=1))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    from . import db, tracker
    try:
        tracker.set_status(db.connect(), args.app_id, args.state, source="cli")
    except ValueError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    print(f"application {args.app_id} -> {args.state}")
    return 0


def cmd_baseline(args: argparse.Namespace) -> int:
    from . import config
    from .resume import baseline
    try:
        resume = baseline.import_baseline(args.docx, config.PROFILES / args.profile)
    except (ValueError, FileNotFoundError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    jobs = ", ".join(f"{j['company']} ({len(j['bullets'])} bullets)" for j in resume["experience"])
    info = resume["_import"]
    print(f"template.docx + baseline.json written for {args.profile}: {len(resume['skills'])} skill lines; {jobs}")
    if info["template_backup"]:
        print(f"previous template kept as {info['template_backup']}")
    if info["known_added"]:
        print(f"known_skills.txt += {', '.join(info['known_added'])}")
    print("the fit score now ranks jobs against this resume's skills (next fetch, or `job-hunt-agent resignal` now)")
    return 0


def _top_line(rank: int, row: dict, result) -> str:
    more = len(row.get("also", ()))
    where = (row.get("location") or "")[:28] + (f" +{more} locations" if more else "")
    worth = f"{result.worth:>3}" if result.scored else "  —"
    head = f"{rank:>2}. {worth}  {row['company'][:22]:<22} {row['title'][:58]:<58} {where}"
    return f"{head}\n            " + " · ".join(f"[{c}]" for c in result.chips[:6])


def cmd_resignal(args) -> int:
    """Re-derive families / years / employment / staffing / sponsorship for stored jobs, then rebuild fits."""
    from . import config, db, fits, resignal
    conn = db.connect()
    stats = resignal.run(conn, dry=args.dry)
    print(json.dumps(stats))
    if not args.dry:
        now = db.utcnow()
        for profile in config.profiles():
            print(f"fits rebuilt for {profile['id']}: {fits.refresh(conn, profile, now)}")
    return 0


def cmd_top(args: argparse.Namespace) -> int:
    from . import config, db, fits, ranking
    try:
        profile = config.profile(args.profile)
    except config.ConfigError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    conn, now = db.connect(), db.utcnow()
    fits.refresh(conn, profile, now)
    ranked = ranking.collapse(conn, ranking.rank(conn, profile, now, fresh_only=args.fresh))[:args.n]
    if args.json:
        print(json.dumps([{**row, "worth": r.worth if r.scored else None, "band": r.band, "chips": r.chips, "parts": r.parts,
                           "push": r.push} for row, r in ranked], indent=1, default=str))
        return 0
    label = "fresh (<24h) " if args.fresh else ""
    print(f"Top {len(ranked)} {label}jobs for {profile['name']} (score v5)")
    for i, (row, result) in enumerate(ranked, 1):
        print(_top_line(i, row, result))
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    from . import demo
    return demo.run(args.home, args.port, args.open)


def cmd_serve(args: argparse.Namespace) -> int:
    from . import server
    server.serve(lan=args.lan, port=args.port, fetch_loop=not args.no_fetch, sync=not args.no_sync)
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    from . import db, health
    conn, now = db.connect(), db.utcnow()
    if args.coverage:                                          # just the coverage report
        from . import coverage
        rep = coverage.report(conn, now)
        print(json.dumps(rep, indent=1, default=str) if args.json else "\n".join(coverage.lines(rep)))
        return 0
    h = health.health(conn, now)
    if args.json:
        print(json.dumps(h, indent=1, default=str))
        return 0
    last = h["last_run"] or {}
    print(f"last run: #{last.get('id')} {last.get('started_at')} ({(last.get('duration_ms') or 0) / 1000:.0f}s); "
          f"last successful board fetch {h['last_ok_fetch_min']} min ago{'  ** STALE **' if h['stale'] else ''}")
    for ats, stats in sorted(h["by_ats"].items()):
        states = " ".join(f"{k}={v}" for k, v in stats.items() if k not in ("jobs", "new"))
        print(f"  {ats:<16} {states:<28} jobs={stats['jobs']:<6} new={stats['new']}")
    print(f"open jobs: {h['open_jobs']} · review boards (not fetched): {h['review_boards']}")
    print("failing 3+ runs in a row: " + (", ".join(f"{b['board']} ({b['fail_streak']})" for b in h["failing"]) or "none"))
    if h["gone"]:                                              # 404/410: re-checked weekly, dropped after 4 (fine)
        print(f"gone (404/410, re-checked weekly, dropped after 4): " +
              ", ".join(f"{b['board']} ({b['dead_streak']})" for b in h["gone"]))
    print(f"pending fits: {h['pending_fits']} · pushes/day (7d): {h['pushes_per_day']}")
    print("drift: " + ("; ".join(h["drift"]) or "none"))
    from .google import auth as gauth, cli as gcli                # token + two-way sheet sync
    print(f"google: {gcli.token_line(gauth.status())} (details: job-hunt-agent google status)")
    print(gcli.sync_line(conn))
    from .mail import cli as mailcli                           # each person's mail, last check, today
    for line in mailcli.doctor_lines(conn):
        print(line)
    rows_ok = _sheet_rows_line(conn)
    from . import coverage                                     # coverage report (decision 005)
    for line in coverage.lines(coverage.report(conn, now)):
        print(line)
    return 1 if h["stale"] or h["failing"] or not rows_ok else 0


def _sheet_rows_line(conn) -> bool:
    """User, 2026-10-06 evening: flag any sheet row taller than normal. Prints the line; False on a problem."""
    from .google import GoogleError
    from .gsync import profiles_linked
    from .gsync_oneline import doctor_line
    if not profiles_linked(conn):
        return True
    try:
        line = doctor_line(conn)
    except GoogleError as err:                                # offline / signed out: the sync line says why
        print(f"sheet rows: not checked ({err})")
        return True
    print(line)
    return "PROBLEM" not in line


def cmd_vendor_explain(args: argparse.Namespace) -> int:
    """the vendor model's reasoning for one job, every resume (decision 010)."""
    from . import config, db, vendor_report
    conn = db.connect()
    try:
        print("\n".join(vendor_report.explain_job(conn, config.profile(args.profile), args.uid)))
    except KeyError:
        print(f"error: {args.uid} is not an open title-matched job for {args.profile}", file=sys.stderr)
        return 2
    finally:
        conn.close()
    return 0


def cmd_vendor_report(args: argparse.Namespace) -> int:
    """per-vendor coverage of the vendor scores + reasoning for 2 jobs per vendor (markdown)."""
    from pathlib import Path
    from . import config, db, vendor_report
    conn = db.connect()
    try:
        text = "\n".join(vendor_report.evidence(conn, config.profile(args.profile))) + "\n"
    finally:
        conn.close()
    if args.out:
        Path(args.out).write_text(text)
        print(f"written {args.out}")
    else:
        print(text)
    return 0


def cmd_digest(args: argparse.Namespace) -> int:
    from . import db, fetch, notify
    if args.fetch:                       # wait for a running fetch instead of digesting stale data (verifier M8)
        fetch.run(trigger="digest", wait_s=DIGEST_LOCK_WAIT_S)
    print(notify.digest(db.connect(), db.utcnow()))
    return 0


def cmd_names(args: argparse.Namespace) -> int:
    """company names as the company writes them (check prints the plan; apply also writes it)."""
    from . import db, names_fix
    conn = db.connect()
    changes = names_fix.check(conn, live=not args.offline)
    for c in changes:
        print(f"{c['board']}\t{c['old']!r} -> {c['new']!r}\t{c['why']}")
    for d in names_fix.drifted(conn):
        print(f"{d['board']}\t{d['jobs']} stored jobs say {d['job_name']!r}, the board says {d['board_name']!r}")
    if args.action == "apply":
        result = names_fix.apply(conn, changes)
        print(json.dumps({k: v for k, v in result.items() if k != "tracker_rows"}, indent=1))
        for row in result["tracker_rows"]:
            print(f"tracker #{row['id']} ({row['profile']}): {row['old']!r} -> {row['new']!r}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="job-hunt-agent", description="Fresh AI/ML jobs from company ATS boards, ranked by fit, with a tracker")
    sub = parser.add_subparsers(dest="command", required=True)
    names = sub.add_parser("names", help="company names as the company writes them")
    names.add_argument("action", choices=["check", "apply"])
    names.add_argument("--offline", action="store_true", help="no live look at the ATS (stored JDs only)")
    names.set_defaults(func=cmd_names)
    smoke = sub.add_parser("smoke", help="live smoke test of every ATS adapter")
    smoke.add_argument("--ats", choices=sorted(ADAPTERS))
    smoke.set_defaults(func=cmd_smoke)
    fetch = sub.add_parser("fetch", help="fetch every board in config/companies.yaml into the DB")
    fetch.add_argument("--board", help="only this board id (ats:key) or company name")
    fetch.add_argument("--force", action="store_true", help="skip the 25-minute guard (the lock still applies)")
    fetch.add_argument("--quiet", action="store_true")
    fetch.add_argument("--trigger", default="cli", help=argparse.SUPPRESS)
    fetch.set_defaults(func=cmd_fetch)
    disc = sub.add_parser("discover", help="probe Greenhouse/Lever/Ashby slugs for tracker + seed companies")
    disc.add_argument("--workday", action="store_true", help="verify config/workday_candidates.yaml live instead")
    disc.add_argument("--reverify", action="store_true", help="re-check board identity of existing entries; flag mismatches")
    disc.add_argument("--cc", action="store_true", help="harvest board tokens from Common Crawl's index (cached)")
    disc.add_argument("--verify", action="store_true", help="verify crawl tokens live (US posting in 60 days)")
    disc.add_argument("--seed", action="store_true", help="probe Workday tenants for S&P 500 names (robots first)")
    disc.add_argument("--build", action="store_true", help="write config/companies.discovered.yaml")
    disc.add_argument("--refresh", action="store_true", help="monthly: --cc + re-verify all + --build")
    disc.add_argument("--lists", action="store_true", help="download the S&P 500 + YC lists into config/lists/")
    disc.add_argument("--seeds", action="store_true", help="download tenant seed lists for the GOAL16 systems")
    disc.add_argument("--ats", help="limit --verify to these ATSs (comma-separated)")
    disc.add_argument("--limit", type=int, help="verify/seed at most N tokens")
    disc.set_defaults(func=cmd_discover)
    add = sub.add_parser("add", help="add a company from any careers URL (verified live)")
    add.add_argument("url")
    add.add_argument("--name")
    add.set_defaults(func=cmd_add)
    track = sub.add_parser("tracker", help="import/export the application tracker xlsx; list follow-ups due")
    track.add_argument("action", choices=["import", "import-mapped", "export", "due"])
    track.add_argument("--profile", default=None)
    track.add_argument("--file", help="xlsx to import (default data/import/Application_Tracker.xlsx)")
    track.add_argument("-o", "--out", help="export path (default data/export/...)")
    track.add_argument("--map", help="import-mapped: mapping yaml (default profiles/<p>/tracker_map.yaml)")
    track.add_argument("--dry-run", action="store_true", help="import-mapped: counts only, write nothing")
    track.add_argument("--full", action="store_true", help="export: every field + contacts + vendors (view only)")
    track.set_defaults(func=cmd_tracker)
    status = sub.add_parser("status", help="set an application's pipeline status")
    status.add_argument("app_id", type=int)
    status.add_argument("state", choices=["saved", "applied", "screen", "interview", "offer", "rejected", "ghosted"])
    status.set_defaults(func=cmd_status)
    base = sub.add_parser("baseline", help="parse a resume .docx into profiles/<p>/baseline.json")
    base.add_argument("action", choices=["import"])
    base.add_argument("docx")
    base.add_argument("-p", "--profile", default=None)
    base.set_defaults(func=cmd_baseline)
    top = sub.add_parser("top", help="best open jobs for a profile by worth score, with reason chips")
    top.add_argument("-p", "--profile", default=None)
    top.add_argument("--fresh", action="store_true", help="only jobs that are fresh (first posted < 24h)")
    top.add_argument("-n", type=int, default=20)
    top.add_argument("--json", action="store_true")
    top.set_defaults(func=cmd_top)
    resig = sub.add_parser("resignal", help="re-derive title families, required years, employment type, staffing and "
                                             "sponsorship signals for stored jobs, then rebuild fits")
    resig.add_argument("--dry", action="store_true", help="only count what would change")
    resig.set_defaults(func=cmd_resignal)
    demo = sub.add_parser("demo", help="try the app on fictional data: no keys, no network")
    demo.add_argument("--home", help="keep the demo data here (default: a new temp folder)")
    demo.add_argument("--port", type=int)
    demo.add_argument("--open", action="store_true", help="open the browser")
    demo.set_defaults(func=cmd_demo)
    serve = sub.add_parser("serve", help="web UI on http://localhost:8765 (--lan: whole network, token required)")
    serve.add_argument("--lan", action="store_true")
    serve.add_argument("--port", type=int)
    serve.add_argument("--no-fetch", action="store_true", help="do not run the 30-minute fetch loop in the server")
    serve.add_argument("--no-sync", action="store_true", help="no Google Sheet sync (a second server, e.g. the design lab)")
    serve.set_defaults(func=cmd_serve)
    doctor = sub.add_parser("doctor", help="per-ATS health, failing boards, last-run stats")
    doctor.add_argument("--json", action="store_true")
    doctor.add_argument("--coverage", action="store_true", help="only the coverage report (live board count, tiers, "
                        "segments, funnel, buckets, cost)")
    doctor.set_defaults(func=cmd_doctor)
    digest = sub.add_parser("digest", help="morning digest: notification + data/digests/<date>.md")
    digest.add_argument("--fetch", action="store_true", help="run a fetch first")
    digest.set_defaults(func=cmd_digest)
    vexp = sub.add_parser("vendor-explain", help="how the job's ATS vendor model scores each resume")
    vexp.add_argument("uid")
    vexp.add_argument("-p", "--profile", default=None)
    vexp.set_defaults(func=cmd_vendor_explain)
    vrep = sub.add_parser("vendor-report", help="vendor-model coverage per vendor + reasoning for 2 jobs per vendor")
    vrep.add_argument("-p", "--profile", default=None)
    vrep.add_argument("-o", "--out", help="write the markdown here instead of printing it")
    vrep.set_defaults(func=cmd_vendor_report)
    from .google import cli as gcli                            # job-hunt-agent google ...
    gcli.register(sub)
    from . import jdcli                                         # job-hunt-agent jd-backfill
    jdcli.register(sub)
    from .mail import cli as mailcli                           # job-hunt-agent mail ...
    mailcli.register(sub)
    from .h1b import cli as h1bcli                             # job-hunt-agent h1b ...
    h1bcli.register(sub)
    from .autofill import cli as afcli                          # job-hunt-agent autofill canary | nightly
    afcli.register(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    from . import config
    args = build_parser().parse_args(argv)
    if "profile" in vars(args) and args.profile is None:       # no name in code: settings or the first profile
        args = argparse.Namespace(**{**vars(args), "profile": config.default_profile()})
        if args.profile is None:
            print("error: no profile yet; run scripts/setup.sh or create profiles/<id>/profile.yaml", file=sys.stderr)
            return 2
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
