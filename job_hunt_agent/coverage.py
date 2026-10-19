"""`job-hunt-agent doctor` coverage report (decision 005): the live count as of today, boards per ATS x tier x
segment, the discovery funnel, matches per day, the big vs startup share, cadence and t1 on-time per bucket, the
oldest due age, host backoff and robots blocks, run cost, and the monthly-refresh warning.

Live = an active board (not dropped, not review) with at least one US job posted in the last 60 days: from fetched
jobs when the board has been fetched, else from its verification probe. Everything here reads the DB; nothing is
fetched.
"""
import json
from collections import defaultdict
from datetime import datetime, timedelta

from . import buckets, config, db, discover_cc, discover_run, segments, seedlists, tiers

US_WINDOW = timedelta(days=60)
DAYS = 7
CADENCE_LIMIT = {1: timedelta(minutes=40), 2: timedelta(hours=4, minutes=30), 3: timedelta(hours=25)}
NO_NEW_BOARDS_ALERT = timedelta(days=30)


def _ts(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _boards(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM boards WHERE active = 1 AND dropped IS NULL AND review = 0")]


def _segment(row: dict) -> str:
    return row.get("segment") or segments.segment(row["company"], row.get("website"), row["ats"],
                                                  row.get("wd_total") or row.get("last_ok_seen"))


def live_boards(conn, now: datetime) -> dict[str, str]:
    """board -> evidence ('fetched' | 'verified') for boards with a US posting in the last 60 days."""
    since = db.iso(now - US_WINDOW)
    fetched = {r[0] for r in conn.execute(
        "SELECT DISTINCT board FROM jobs WHERE is_us = 1 AND first_posted_at >= ?", (since,))}
    for ats in tiers.UNDATED_ATS:                 # undated postings count from first seen (tiers.newest_us_match)
        fetched |= {r[0] for r in conn.execute(
            "SELECT DISTINCT board FROM jobs WHERE board >= ? AND board < ? AND first_posted_at IS NULL "
            "AND is_us = 1 AND first_seen >= ?", (f"{ats}:", f"{ats};", since))}
    probes = {f"{r['ats']}:{r['slug']}": r["newest_us"] for r in conn.execute(
        "SELECT ats, slug, newest_us FROM discover_probes WHERE us60 > 0")}
    out = {}
    for row in _boards(conn):
        if row["board"] in fetched:
            out[row["board"]] = "fetched"
        elif not row.get("last_ok_at") and (probes.get(row["board"]) or "") >= since:
            out[row["board"]] = "verified"
    return out


def matrix(conn, live: dict[str, str], now: datetime) -> dict:
    """Counts over live boards: by ATS, by tier (computed now), by segment, and the ATS x tier x segment cube."""
    cube: dict[str, int] = defaultdict(int)
    by = {"ats": defaultdict(int), "tier": defaultdict(int), "segment": defaultdict(int)}
    current = tiers.board_tiers(conn, now)
    for r in (r for r in _boards(conn) if r["board"] in live):
        tier, seg = tiers.tier_of(r["ats"], current.get(r["board"], 3)), _segment(r)
        cube[f"{r['ats']}|t{tier}|{seg}"] += 1
        by["ats"][r["ats"]] += 1
        by["tier"][f"t{tier}"] += 1
        by["segment"][seg] += 1
    return {f"by_{k}": dict(v) for k, v in by.items()} | {"ats_tier_segment": dict(sorted(cube.items()))}


def matches_per_day(conn, now: datetime) -> dict:
    """New title-matched US jobs per day (first_seen, not seed), with their segment split over DAYS days."""
    rows = conn.execute(
        "SELECT substr(j.first_seen, 1, 10) AS day, b.company, b.ats, b.segment, b.website, b.wd_total, "
        "b.last_ok_seen FROM jobs j JOIN boards b ON b.board = j.board WHERE j.families <> ',' AND j.is_us = 1 "
        "AND j.first_seen >= ? AND j.seed = 0", (db.iso(now - timedelta(days=DAYS)),)).fetchall()
    per_day: dict[str, int] = defaultdict(int)
    share: dict[str, int] = defaultdict(int)
    for r in rows:
        per_day[r["day"]] += 1
        share[_segment(dict(r))] += 1
    total = sum(share.values()) or 1
    return {"per_day": dict(sorted(per_day.items())), "n": sum(share.values()),
            "segment_share": {k: round(v / total, 3) for k, v in sorted(share.items())}}


def posted_per_day(conn, now: datetime) -> dict:
    """Title-matched US jobs by the ATS posting day over the last DAYS complete days (GOAL5 §4 before/after: unlike
    first_seen, a newly added board's backlog does not inflate it). Only boards the feed shows (active, not in review):
    a robots-dropped or replaced board's jobs stay in the DB but no longer count."""
    end = now.replace(hour=0, minute=0, second=0, microsecond=0)
    rows = conn.execute(
        "SELECT substr(j.first_posted_at, 1, 10) AS day, b.ats, count(*) AS n FROM jobs j JOIN boards b ON "
        "b.board = j.board WHERE b.active = 1 AND b.review = 0 AND j.families <> ',' AND j.is_us = 1 "
        "AND j.first_posted_at >= ? AND j.first_posted_at < ? "
        "GROUP BY day, b.ats", (db.iso(end - timedelta(days=DAYS)), db.iso(end))).fetchall()
    per_day: dict[str, int] = defaultdict(int)
    by_ats: dict[str, int] = defaultdict(int)
    for r in rows:
        per_day[r["day"]] += r["n"]
        by_ats[r["ats"]] += r["n"]
    return {"per_day": dict(sorted(per_day.items())), "by_ats": dict(sorted(by_ats.items())),
            "mean": sum(per_day.values()) / DAYS}


def _bucket(board: str) -> str:
    ats, _, key = board.partition(":")
    if ats == "workday":
        return f"{key.split('/')[1]}.myworkdayjobs.com"
    return ats


def _gap_stats(values: list[float], limit_s: float) -> dict:
    values = sorted(values)
    return {"n": len(values), "p95_min": round(values[int(0.95 * (len(values) - 1))] / 60, 1),
            "on_time": round(sum(v <= limit_s for v in values) / len(values), 3)}


def cadence(conn, now: datetime) -> dict:
    """Gaps between consecutive fetches of the same board over the last day: per tier (p95, share within the tier's
    limit), and for t1 per rate bucket (the judge's 't1 within 40 min on wd5 and wd1', condition 3)."""
    rows = conn.execute(
        "SELECT f.board, b.tier, r.started_at FROM fetch_runs f JOIN runs r ON r.id = f.run_id "
        "JOIN boards b ON b.board = f.board WHERE r.started_at >= ? AND f.status <> 'skipped' "
        "ORDER BY f.board, r.started_at", (db.iso(now - timedelta(days=1)),)).fetchall()
    gaps: dict[int, list[float]] = defaultdict(list)
    t1_by_bucket: dict[str, list[float]] = defaultdict(list)
    last: dict[str, datetime] = {}
    for board, tier, started in rows:
        moment = _ts(started)
        if board in last:
            gap = (moment - last[board]).total_seconds()
            gaps[tier or 3].append(gap)
            if tier == 1:
                t1_by_bucket[_bucket(board)].append(gap)
        last[board] = moment
    out = {f"t{t}": _gap_stats(v, CADENCE_LIMIT.get(t, CADENCE_LIMIT[3]).total_seconds())
           for t, v in sorted(gaps.items())}
    out["t1_by_bucket"] = {b: _gap_stats(v, CADENCE_LIMIT[1].total_seconds()) for b, v in sorted(t1_by_bucket.items())}
    return out


def bucket_load(conn) -> list[dict]:
    """Last run's per-bucket numbers; alert when the due work passes 60 % of the deadline (condition 6)."""
    run = conn.execute("SELECT max(run_id) FROM run_buckets").fetchone()[0]
    if run is None:
        return []
    rows = [dict(r) for r in conn.execute("SELECT * FROM run_buckets WHERE run_id = ? ORDER BY busy_s DESC", (run,))]
    return [{**r, "alert": bool(r["budget_s"] and r["est_need_s"] > buckets.ALERT_SHARE * r["budget_s"])}
            for r in rows]


def oldest_due(conn, now: datetime) -> dict:
    worst, n_over = 0.0, 0
    for row in _boards(conn):
        last = row.get("last_fetch") or row.get("last_ok_at")
        if not last:
            continue
        late = (now - _ts(last) - tiers.interval_for(row, row.get("tier") or 3)).total_seconds()
        n_over += late > tiers.SLACK.total_seconds()
        worst = max(worst, late)
    return {"oldest_due_min": round(worst / 60, 1), "boards_overdue": n_over}


def runs_cost(conn, n: int = 48) -> dict:
    rows = [dict(r) for r in conn.execute(
        "SELECT id, started_at, duration_ms, cpu_ms, cpu_sweep_ms, n_req, n_304, bytes, budget_exhausted, n_due, "
        "n_taken FROM runs WHERE finished_at IS NOT NULL AND cpu_ms IS NOT NULL ORDER BY id DESC LIMIT ?", (n,))]
    full = [r for r in rows if not r["budget_exhausted"]]
    return {"runs": len(rows), "truncated_share": round(1 - len(full) / len(rows), 3) if rows else None,
            "cpu_s_mean_untruncated": round(sum(r["cpu_ms"] for r in full) / len(full) / 1000, 1) if full else None,
            "cpu_s_max": round(max(r["cpu_ms"] for r in rows) / 1000, 1) if rows else None,
            "share_304": round(sum(r["n_304"] or 0 for r in rows) / max(1, sum(r["n_req"] or 0 for r in rows)), 3),
            "recent": rows[:5]}


def blocks(conn, now: datetime) -> dict:
    paused = [dict(r) for r in conn.execute("SELECT host, until, strikes FROM host_state WHERE until > ?",
                                            (db.iso(now),))]
    robots_dropped = [dict(r) for r in conn.execute(
        "SELECT board, dropped, dropped_at FROM boards WHERE dropped LIKE 'robots:%' ORDER BY board")]
    return {"paused_hosts": paused, "robots_dropped": robots_dropped,
            "dead_dropped": conn.execute("SELECT count(*) FROM boards WHERE dropped LIKE 'dead:%'").fetchone()[0],
            "candidates_robots_disallowed": conn.execute(
                "SELECT count(*) FROM discover_probes WHERE name_src = 'robots'").fetchone()[0]}


def funnel(conn) -> dict:
    probes = {r["ats"]: {k: r[k] for k in r.keys() if k != "ats"} for r in conn.execute(
        "SELECT ats, count(*) AS checked, sum(hit) AS live, sum(us60 > 0) AS us_live, "
        "sum(error IS NOT NULL) AS retry_later FROM discover_probes WHERE source IN ('cc', 'seed') GROUP BY ats")}
    build_path = discover_cc.cache_dir() / "build.json"
    names = dict(conn.execute("SELECT coalesce(name_src, 'ats/page'), count(*) FROM discover_probes WHERE us60 > 0 "
                              "GROUP BY 1").fetchall())
    return {"tokens": {a: len(t) for a, t in discover_run.tokens().items()}, "probes": probes,
            "build": json.loads(build_path.read_text()) if build_path.exists() else {}, "name_sources": names}


def sp500_coverage(conn) -> dict:
    have = {seedlists.compact(r[0]) for r in conn.execute(
        "SELECT company FROM boards WHERE active = 1 AND dropped IS NULL")}
    names = seedlists.sp500()
    return {"covered": sum(1 for c in names if c in have), "of": len(names)}


def _alerts(conn, now: datetime, load: list[dict]) -> list[str]:
    alerts = []
    last = discover_run.last_refresh()
    if not last or now - _ts(last) > discover_run.REFRESH_EVERY:
        alerts.append(f"discovery not refreshed since {last or 'never'}: run `job-hunt-agent discover --refresh`")
    newest = conn.execute("SELECT max(verified_at) FROM discover_probes WHERE us60 > 0").fetchone()[0]
    if not newest or now - _ts(newest) > NO_NEW_BOARDS_ALERT:
        alerts.append("no new US-live board verified in 30 days")
    return alerts + [f"{b['bucket']}: due work {b['est_need_s']:.0f}s > 60% of its {b['budget_s']:.0f}s deadline; "
                     "raise bucket_deadline_s or in-flight (t1 is never narrowed)" for b in load if b["alert"]]


def report(conn, now: datetime) -> dict:
    counted = live_boards(conn, now)
    load = bucket_load(conn)
    return {"as_of": db.iso(now), "live_count": len(counted),
            "live_evidence": {k: sum(1 for v in counted.values() if v == k) for k in ("fetched", "verified")},
            **matrix(conn, counted, now),
            "matches": matches_per_day(conn, now), "posted": posted_per_day(conn, now), "cadence": cadence(conn, now), "buckets": load,
            "due": oldest_due(conn, now), "cost": runs_cost(conn), "blocks": blocks(conn, now),
            "funnel": funnel(conn), "sp500": sp500_coverage(conn), "last_refresh": discover_run.last_refresh(),
            "alerts": _alerts(conn, now, load), "registry": {"curated": len(config.companies_raw())}}


def lines(rep: dict) -> list[str]:
    """Human-readable doctor section."""
    out = [f"coverage as of {rep['as_of']}: LIVE BOARDS {rep['live_count']} (US posting in 60 days; "
           f"{rep['live_evidence']['fetched']} from fetched jobs, {rep['live_evidence']['verified']} from verification)",
           "  by ATS: " + ", ".join(f"{k} {v}" for k, v in sorted(rep["by_ats"].items())),
           "  by tier: " + ", ".join(f"{k} {v}" for k, v in sorted(rep["by_tier"].items())),
           "  by segment: " + ", ".join(f"{k} {v}" for k, v in sorted(rep["by_segment"].items())),
           f"  S&P 500 covered: {rep['sp500']['covered']}/{rep['sp500']['of']}"]
    m = rep["matches"]
    out.append(f"  matched US jobs/day (7 d): {m['per_day']} · share: {m['segment_share']}")
    posted = rep["posted"]
    out.append(f"  matched US jobs posted/day (7 complete days, by posting date): mean {posted['mean']:.1f} · "
               f"{posted['per_day']} · by ATS {posted['by_ats']}")
    tiers_only = {t: c for t, c in rep["cadence"].items() if t != "t1_by_bucket"}
    out.append("  cadence: " + ", ".join(f"{t} p95 {c['p95_min']} min, on time {c['on_time']:.0%} (n={c['n']})"
                                         for t, c in tiers_only.items()))
    out.append("  t1 within 40 min by bucket: " + ", ".join(
        f"{b} {c['on_time']:.0%} (n={c['n']})" for b, c in rep["cadence"].get("t1_by_bucket", {}).items()))
    out.append(f"  due: oldest {rep['due']['oldest_due_min']} min late, {rep['due']['boards_overdue']} overdue")
    for b in rep["buckets"][:8]:
        out.append(f"  bucket {b['bucket']:<28} req {b['n_req']:<5} 304 {b['n_304']:<5} busy {b['busy_s']:>6}s "
                   f"need {b['est_need_s']:>7}s/{b['budget_s']:.0f}s t1 {b['t1_done']}/{b['t1_due']} "
                   f"carried {b['carried']}{'  ** ALERT **' if b['alert'] else ''}")
    c = rep["cost"]
    out.append(f"  runs: {c['runs']} measured · CPU mean (untruncated) {c['cpu_s_mean_untruncated']} s, max "
               f"{c['cpu_s_max']} s · truncated share {c['truncated_share']} · 304 share {c['share_304']}")
    out.append("  fetch CPU per run (newest first): " + ", ".join(
        f"#{r['id']} {r['started_at'][5:16].replace('T', ' ')}Z {r['cpu_ms'] / 1000:.1f} s"
        f"{' (budget hit)' if r['budget_exhausted'] else ''}" for r in c["recent"]))
    bl = rep["blocks"]
    paused = ", ".join(f"{h['host']} until {h['until']}" for h in bl["paused_hosts"]) or "none"
    out.append(f"  backoff: {paused} · robots-dropped boards {len(bl['robots_dropped'])} · dead-dropped "
               f"{bl['dead_dropped']} · robots-disallowed candidates {bl['candidates_robots_disallowed']}")
    for row in bl["robots_dropped"][:10]:
        out.append(f"    robots: {row['board']}")
    f = rep["funnel"]
    out.append(f"  discovery funnel: tokens {f['tokens']} · probes {f['probes']}")
    if f["build"]:
        out.append(f"  registry build {f['build'].get('at')}: {f['build'].get('funnel')}")
    out.append(f"  last discovery refresh: {rep['last_refresh']}")
    out += [f"  ALERT: {a}" for a in rep["alerts"]]
    return out
