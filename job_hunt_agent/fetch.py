"""Fetch run: lock, tiered due boards, a bounded thread pool, per-board classify + write on the main thread
(decision 001 §2, §5, §7; decision 005 B2: tiers, conditional GETs, raw fingerprints, CPU budget).

Only boards that are due (tiers.py) are taken, tier 1 first; the run stops taking boards when the CPU or wall
budget is spent (runstats.py), and boards it did not reach carry over. A host that refuses us (403/429 runs) is
paused by net; its boards carry over too and doctor shows the pause.
"""
import fcntl
import logging
import sqlite3
import threading
import time
from collections import defaultdict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timedelta

from . import (buckets, config, db, employer_boards, fits, guard, liveness, net, outage, prune, registry, robots, runstats,
               store, tiers, titles)
from .ats import ADAPTERS, Ctx, FetchResult, careeronestop
from .ats.common import SCHEMA_ERROR
from .ats.workday import split_key
from .net import RETRYABLE

MIN_GAP = timedelta(minutes=25)
LOCK_POLL_S = 5.0
EMPTY_SUSPECT_MIN = 5                 # a board that listed this many jobs and then far fewer gets probed
GONE_AFTER = timedelta(hours=24)      # a 404/410 board this long is probed and, if its jobs are gone, closed
BREAKER_TRIP = 5
STATIC_HOSTS = {"greenhouse": "boards-api.greenhouse.io", "lever": "api.lever.co",
                "ashby": "api.ashbyhq.com", "smartrecruiters": "api.smartrecruiters.com",
                "amazon": "www.amazon.jobs", "workable": "apply.workable.com", "jobvite": "jobs.jobvite.com",
                "rippling": "ats.rippling.com", "gem": "api.gem.com",
                "adp": "workforcenow.adp.com", "paylocity": "recruiting.paylocity.com", "dayforce": "jobs.dayforcehcm.com",
                "ibm": "www-api.ibm.com", "jobscore": "careers.jobscore.com",
                "careeronestop": "api.careeronestop.org"}
HOST_TEMPLATES = {"bamboohr": "{head}.bamboohr.com",        # GOAL16: the key starts with the tenant's own subdomain
                  "cornerstone": "{head}.csod.com", "jazzhr": "{head}.applytojob.com",
                  "recruitee": "{head}.recruitee.com", "breezy": "{head}.breezy.hr",
                  "pinpoint": "{head}.pinpointhq.com", "hibob": "{head}.careers.hibob.com"}
KEYED_HOSTS = {"eightfold", "phenom", "icims",          # the key starts with the tenant's own careers host
               "oracle_recruiting", "taleo", "successfactors", "icims_classic"}
CARRY_MARKS = ("host backoff", "robots.txt unreadable")     # no answer to record: the board carries over
MAX_THREADS = 32
FITS_CPU_CEILING_S = runstats.RLIMIT_CPU_S - 50       # leave notify, Sheet flag and backup room under the backstop
LIST_URLS = {"greenhouse": "https://boards-api.greenhouse.io/v1/boards/{key}/jobs",
             "lever": "https://api.lever.co/v0/postings/{key}?mode=json",
             "ashby": "https://api.ashbyhq.com/posting-api/job-board/{key}",
             "smartrecruiters": "https://api.smartrecruiters.com/v1/companies/{key}/postings",
             "amazon": "https://www.amazon.jobs/en/search.json",
             "workable": "https://apply.workable.com/api/v1/widget/accounts/{key}",
             "eightfold": "https://{host}/api/{api}",
             "phenom": "https://{host}/widgets",
             "icims": "https://{host}/api/jobs",
             "oracle_recruiting": "https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions",
             "taleo": "https://{host}/careersection/rest/jobboard/searchjobs",
             "successfactors": "https://{key}/search/",
             "icims_classic": "https://{host}/sitemap.xml",
             "jobvite": "https://jobs.jobvite.com/{key}/jobs",
             "bamboohr": "https://{key}.bamboohr.com/careers/list",          # GOAL16
             "rippling": "https://ats.rippling.com/api/v1/board/{key}/jobs",
             "gem": "https://api.gem.com/job_board/v0/{key}/job_posts/",
             "adp": "https://workforcenow.adp.com/mascsr/default/careercenter/public/events/staffing/v1/job-requisitions",
             "paylocity": "https://recruiting.paylocity.com/Recruiting/Jobs/All/{key}",
             "dayforce": "https://jobs.dayforcehcm.com/api/geo/{head}/jobposting/search",
             "cornerstone": "https://{head}.csod.com/ux/ats/careersite/{tail}/home",
             "jazzhr": "https://{key}.applytojob.com/apply",
             "ibm": "https://www-api.ibm.com/search/api/v2",
             "recruitee": "https://{key}.recruitee.com/api/offers/",
             "breezy": "https://{key}.breezy.hr/json",
             "pinpoint": "https://{key}.pinpointhq.com/postings.json",
             "hibob": "https://{key}.careers.hibob.com/api/job-ad",
             "jobscore": "https://careers.jobscore.com/jobs/{key}/feed.json"}


def host_of(board: dict) -> str:
    if board["ats"] == "workday":
        tenant, wd, _ = split_key(board["key"])
        return f"{tenant}.{wd}.myworkdayjobs.com"
    if board["ats"] in KEYED_HOSTS:
        return board["key"].split("/")[0]
    if board["ats"] in HOST_TEMPLATES:
        return HOST_TEMPLATES[board["ats"]].format(head=board["key"].split("/")[0])
    return STATIC_HOSTS[board["ats"]]


class Breaker:
    """Per-host consecutive transient failures (transport, 429, 5xx). 404/empty boards never count."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._streak: dict[str, int] = defaultdict(int)

    def tripped(self, host: str) -> bool:
        with self._lock:
            return self._streak[host] >= BREAKER_TRIP

    def record(self, host: str, result: FetchResult) -> None:
        transient = result.error is not None and (result.http_status is None or result.http_status in RETRYABLE)
        with self._lock:
            self._streak[host] = self._streak[host] + 1 if transient else 0


def _refresh_fits(conn, profiles: list[dict], now, budget: runstats.Budget | None = None) -> int:
    """Second pass of decision 002: fit rows for new/changed matched jobs. A failure never fails the fetch. A big
    rebuild (extractor version, a new resume) stops at FITS_CPU_CEILING_S of run CPU and continues next run, so the
    run still finishes under RLIMIT_CPU."""
    written = 0
    stop = (lambda: budget.cpu_used() > FITS_CPU_CEILING_S) if budget else (lambda: False)
    for profile in profiles:
        try:
            written += fits.refresh(conn, profile, now, stop)
        except (sqlite3.Error, OSError, ValueError) as err:
            logging.getLogger(__name__).error("fit refresh failed for %s: %s", profile["id"], err)
    return written


def _ghost_scan(conn, now) -> None:
    """GOAL29 §3: re-check the open matched jobs for ghost signals (badge + small Priority penalty). A failure never
    fails the fetch."""
    from . import ghost
    try:
        ghost.scan(conn, now)
    except (sqlite3.Error, OSError, ValueError) as err:
        logging.getLogger(__name__).error("ghost check failed: %s", err)


def _notify(conn, profiles: list[dict], now) -> int:
    """Summary notifications for new pushable jobs; a failure never fails the fetch."""
    from . import notify
    try:
        return notify.notify_new(conn, profiles, now)
    except (sqlite3.Error, OSError, ValueError) as err:
        logging.getLogger(__name__).error("notify failed: %s", err)
        return 0


def _sheet_dirty(conn) -> None:
    """the Google Sheet may show changed job data; the sync thread pushes it (no network
    here). A failure never fails the fetch."""
    from .google import state
    try:
        state.mark_dirty(conn)
    except sqlite3.Error as err:
        logging.getLogger(__name__).error("could not mark the Google Sheet dirty: %s", err)


def search_scope(profiles: list[dict], settings: dict) -> tuple[list[str], list[str]]:
    families = sorted({f for p in profiles for f in p["families"]}) or sorted(titles.FAMILIES)
    terms = list(dict.fromkeys(t.lower() for t in settings["workday_search_terms"] +
                               [t for p in profiles for t in p.get("search_terms", [])]))
    return families, terms


def robots_urls(board: dict) -> list[str]:
    """URLs whose robots.txt verdict decides whether we may fetch this board. A Workday board needs its HTML career
    site allowed too (policy for e.g. citi/wd5/2, 'Disallow: /2/', decision 005 run 2 condition 4)."""
    if board["ats"] == "workday":
        tenant, wd, site = split_key(board["key"])
        base = f"https://{tenant}.{wd}.myworkdayjobs.com"
        return [f"{base}/{site}/", f"{base}/wday/cxs/{tenant}/{site}/jobs"]
    if board["ats"] == "eightfold":
        host, _, api = board["key"].split("/")
        return [LIST_URLS["eightfold"].format(host=host, api="pcsx/search" if api == "pcsx" else "apply/v2/jobs")]
    head, _, tail = board["key"].partition("/")
    return [LIST_URLS[board["ats"]].format(key=board["key"], host=host_of(board), head=head, tail=tail)]


def _robots_block(board: dict) -> FetchResult | None:
    for url in robots_urls(board):
        verdict = robots.allowed(url)
        if verdict is None:
            return FetchResult([], None, error=f"robots.txt unreadable for {url}; retry later")
        if not verdict:
            return FetchResult([], net.ROBOTS_BLOCKED, error=f"robots.txt disallows {url}")
    return None


def _work(board: dict, ctx: Ctx, breaker: Breaker) -> tuple[FetchResult | None, int]:
    host = host_of(board)
    if breaker.tripped(host):
        return None, 0
    started = time.monotonic()
    blocked = _robots_block(board)                  # re-checked before every board fetch (condition 5)
    if blocked is not None:
        return blocked, 0
    try:
        result = ADAPTERS[board["ats"]](board, ctx)
    except Exception as err:   # an adapter bug must not kill the run; it is recorded as drift
        result = FetchResult([], 200, n_parse_err=1, error=f"adapter crash: {err!r}")
    breaker.record(host, result)
    return result, int((time.monotonic() - started) * 1000)


def board_status(conn, board: dict, result: FetchResult | None, now, probe=liveness.probe) -> tuple[str, bool]:
    """(status, close_all) for one board fetch. Before believing that a board emptied, or died with 404/410, ask a
    few of its missing jobs on their own pages (release review C1): a renamed JSON key looks empty, a real hiring
    freeze does not answer. close_all=True closes every open job of a board that has been gone for a day."""
    if result is None:
        return "skipped", False
    if (result.error or "").startswith(SCHEMA_ERROR):
        return "drift", False
    row = db.board(conn, board["board"])
    # search boards (Workday newest-first / terms, Amazon) return a different slice every run: no size checks
    last_seen = 0 if board["ats"] in store.SEARCH_ATS else (row.get("last_ok_seen") or 0)
    status = guard.classify(result.error is None, result.n_raw, len(result.items), result.n_parse_err,
                            last_seen, db.prior_statuses(conn, board["board"]))
    if status == "ok" and last_seen >= EMPTY_SUSPECT_MIN and len(result.items) < guard.PARTIAL_RATIO * last_seen:
        verdict = probe(conn, board["board"], {i["uid"] for i in result.items})
        return {"open": "drift", "closed": "ok"}.get(verdict, "partial"), False
    gone_since = datetime.fromisoformat(row["last_ok_at"]) if row.get("last_ok_at") else None
    if (status == "http_error" and result.http_status in (404, 410) and gone_since
            and now - gone_since >= GONE_AFTER and _has_open(conn, board["board"])):
        verdict = probe(conn, board["board"], set())
        if verdict == "open":                       # the list URL died but the jobs answer: it moved (verifier M9)
            return "drift", False
        return "http_error", verdict == "closed"
    return status, False


def _has_open(conn, board_id: str) -> bool:
    return conn.execute("SELECT 1 FROM jobs WHERE board = ? AND closed_at IS NULL LIMIT 1", (board_id,)).fetchone() \
        is not None


def _host_alerts(conn, run_id: int, outcomes: list[outage.Outcome]) -> list[str]:
    """>=80% of >=3 boards on one host or rate bucket failed: one alert, and those failures do not count against
    boards (neither their fail streak nor, GOAL35 §2, their dead streak)."""
    alerts, failed = outage.collapsed(outcomes)
    outage.forgive(conn, failed)
    drifted = sorted(o.board for o in outcomes if o.status == "drift")
    if drifted:                                      # an ATS changed its format: nothing closed, but say so now
        alerts.append(f"format drift (no jobs closed): {', '.join(drifted[:10])}")
    return alerts


def _acquire(lock, wait_s: float) -> bool:
    """Take the fetch lock, polling up to wait_s seconds while another run holds it."""
    deadline = time.monotonic() + wait_s
    while True:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(min(LOCK_POLL_S, max(0.0, deadline - time.monotonic())))


def run(trigger: str = "cli", force: bool = False, only: str | None = None, verbose: bool = False,
        wait_s: float = 0.0) -> dict:
    """One fetch run. wait_s > 0 waits for a running fetch to finish first (the 08:00 digest), so the caller sees
    fresh data either way."""
    config.DATA.mkdir(parents=True, exist_ok=True)
    with open(config.DATA / "fetch.lock", "w") as lock:
        if not _acquire(lock, wait_s):
            return {"skipped": "another fetch is running"}
        conn = db.connect()
        try:
            now = db.utcnow()
            last = db.last_run_started(conn)
            if not force and last and now - last < MIN_GAP:
                return {"skipped": f"last run started {int((now - last).total_seconds() // 60)} min ago"}
            return _run_locked(conn, trigger, now, only, verbose)
        finally:
            conn.close()


def _daily(conn, now) -> None:
    """The daily verified backup; after a new good one, the logs nobody reads are archived and pruned (GOAL33 §3b),
    and the state job bank employers get their own boards looked up (GOAL36 §2)."""
    if db.backup(conn) is None:
        return
    try:
        prune.run(conn, now)
    except (sqlite3.Error, OSError):                 # never fails the fetch: the next day tries again
        logging.getLogger(__name__).exception("daily prune failed")
    try:                                             # GOAL36 §2: the job bank employers' own boards, rate limited
        employer_boards.run(conn, now)
    except (sqlite3.Error, OSError, ValueError, config.ConfigError):
        logging.getLogger(__name__).exception("daily employer board discovery failed")


def _ctx(conn, board: dict, matches, terms: list[str], now) -> Ctx:
    """Per-board context built on the main thread (the sqlite connection is not shared with workers)."""
    rows = conn.execute("SELECT uid, raw_fp, description IS NOT NULL AS jd, closed_at IS NULL AND stale = 0 AS open "
                        "FROM jobs WHERE board = ?", (board["board"],)).fetchall()
    with_jd = {r["uid"] for r in rows if r["jd"]}
    open_fp = {r["uid"]: r["raw_fp"] for r in rows if r["open"] and r["raw_fp"] is not None}
    elsewhere = _open_elsewhere(conn, board) if board["ats"] == careeronestop.ATS else frozenset()
    return Ctx(matches=matches, search_terms=tuple(terms), today=now.astimezone().date(),
               needs_detail=lambda uid: uid not in with_jd, unchanged=lambda uid, fp: open_fp.get(uid) == fp,
               known_elsewhere=lambda company, title: careeronestop.same_key(company, title) in elsewhere)


def _open_elsewhere(conn, board: dict) -> frozenset:
    """GOAL35 §4.2: (company, title) of every open job on our other boards: a state job bank copy of one is left out."""
    return frozenset(careeronestop.same_key(r[0], r[1]) for r in conn.execute(
        "SELECT company, title FROM jobs WHERE closed_at IS NULL AND stale = 0 AND board <> ?", (board["board"],)))


def _record(conn, task, result, latency: int, run_id: int, now, scopes: list[str]) -> tuple[str, dict]:
    board = task.board
    row = {**board, **db.board(conn, board["board"])}
    if result is not None and result.not_modified:
        return "ok", store.write_not_modified(conn, row, run_id, now, latency)
    status, close_all = board_status(conn, board, result, now)
    if result is not None and result.http_status == net.ROBOTS_BLOCKED:
        status, close_all = "skipped", False               # never fetched: no misses, no fail streak
    summary = store.write_board(conn, row, result or FetchResult([], None, error="skipped: host breaker"), status,
                                run_id, now, scopes, latency, count_misses=not (result and result.truncated),
                                close_all=close_all,
                                run_key=f"{board['board']}#terms" if task.kind == "terms" else None)
    return status, summary


def _bucket_of(task) -> str:
    return net.bucket_key(host_of(task.board))


def _board_bucket(board: dict) -> str:
    return net.bucket_key(host_of(board))


def _cost_fn(conn, tasks: list):
    costs = buckets.board_costs(conn, sorted({t.board["board"] for t in tasks}))

    def cost_of(task) -> float:
        if task.kind == "terms":
            return buckets.DEFAULT_COST_S["workday"] * 4
        return costs.get(task.board["board"], buckets.DEFAULT_COST_S.get(task.board["ats"], 2.0))
    return cost_of


def _run_locked(conn, trigger: str, now, only: str | None, verbose: bool) -> dict:
    budget = runstats.Budget()
    settings = config.settings()
    boards = registry.boards()
    db.sync_boards(conn, boards)
    runstats.load_backoff(conn)
    robots.load(conn)
    net.reset_stats()
    profiles = config.profiles()
    families, terms = search_scope(profiles, settings)
    outage.heal(conn, _board_bucket)                 # GOAL35 §2: a past outage left these on the weekly schedule
    tasks = tiers.plan(conn, boards, now, only, urls_of=robots_urls)
    run_id = db.start_run(conn, trigger, now)
    breaker, outcomes, totals = Breaker(), [], defaultdict(int)
    matches = titles.compile_union(families)
    sched = buckets.Scheduler(tasks, _bucket_of, _cost_fn(conn, tasks),
                              float(settings.get("bucket_deadline_s", buckets.DEADLINE_S)))
    threads = min(MAX_THREADS, sum(b.in_flight for b in sched.buckets.values()) or 1)
    with ThreadPoolExecutor(max_workers=threads) as pool:
        pending: dict = {}

        def start_ready() -> None:
            stop = budget.spent()
            for name, task in sched.ready(stop=stop):
                if net.backoff_until(host_of(task.board)):
                    sched.cancel(name, task)
                    totals["carried_backoff"] += 1   # host paused: no row, no last_fetch advance, next run retries
                    continue
                ctx = _ctx(conn, task.board, matches, terms, now)
                pending[pool.submit(_work, task.board, ctx, breaker)] = (name, task)

        start_ready()
        while pending:
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                name, task = pending.pop(future)
                sched.done(name, task)
                _handle(conn, task, future.result(), run_id, now, families, terms, outcomes, totals, verbose)
            start_ready()
    loop_cpu = budget.cpu_used()
    buckets.save(conn, sched.rows(run_id))
    robots.save(conn)
    totals["named"] = _learn_names(conn)
    alerts = _host_alerts(conn, run_id, outcomes) + runstats.save_backoff(conn, db.utcnow())
    totals["fits"] = _refresh_fits(conn, profiles, now, budget)
    _ghost_scan(conn, now)
    totals["notified"] = _notify(conn, profiles, now)
    _sheet_dirty(conn)
    db.finish_run(conn, run_id, now, alerts)
    _daily(conn, now)
    taken = sum(b.taken for b in sched.buckets.values())
    runstats.finish(conn, run_id, budget, loop_cpu, len(tasks), taken)
    return {"run_id": run_id, "due": len(tasks), "taken": taken, "carried": sched.carried(),
            "budget_exhausted": budget.exhausted, "cpu_s": round(budget.cpu_used(), 1), **net.stats(), **totals,
            "host_alerts": alerts}


def _learn_names(conn) -> int:
    """a board named by a tenant id that now has matched jobs takes its name (names_fix.learn)."""
    from . import names_fix                                  # imported here: it pulls in the tracker and Sheet code
    try:
        return names_fix.learn(conn)["boards"]
    except Exception:                                        # naming never fails a fetch run
        logging.getLogger(__name__).exception("naming the id-named boards failed")
        return 0


def _handle(conn, task, outcome, run_id: int, now, families, terms, outcomes, totals, verbose: bool) -> None:
    result, latency = outcome
    board = task.board
    if result is not None and any(mark in (result.error or "") for mark in CARRY_MARKS):
        totals["carried_backoff"] += 1
        return
    scopes = [f"fam:{f}" for f in families] + (
        [f"term:{t}" for t in terms] if task.kind == "terms" or board["ats"] in store.TERM_SEARCH_ATS else [])
    status, summary = _record(conn, task, result, latency, run_id, now, scopes)
    if result is not None:
        tiers.after_fetch(conn, task, result, status, now)
    outcomes.append(outage.Outcome(board["board"], status, result.http_status if result is not None else None,
                                   host_of(board), _bucket_of(task)))
    totals[status] += 1
    totals["304"] += bool(result is not None and result.not_modified)
    totals["robots_blocked"] += bool(result is not None and result.http_status == net.ROBOTS_BLOCKED)
    totals["new"] += summary["n_new"]
    totals["closed"] += summary["n_closed"]
    totals["matched"] += summary["n_match"]
    if verbose:
        print(f"  {status:<10} {task.kind:<5} T{task.tier} {board['board']:<55} jobs={summary['n_jobs']:<5} "
              f"new={summary['n_new']:<5} closed={summary['n_closed']:<3} {latency / 1000:.1f}s", flush=True)
