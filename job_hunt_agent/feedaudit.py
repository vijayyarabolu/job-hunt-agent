"""GOAL35 §2: where a day's new jobs go. Per day (UTC first_seen) for one person: the postings fetched, the ones whose
title matched any family, the person's families, then every gate the feed applies (past employer, dealbreaker, the
GenAI JD gate (none since GOAL36: a label), not W2 (GOAL35 §4.3), not US, under 40 % resume match (GOAL36), too senior, fit pending, closed since, board off, removed, Not interested, applied before),
the city-clone merge, and what Recommended shows of that day's jobs now. Read only: run it on a copy of the DB.

    python -m job_hunt_agent.feedaudit --db COPY.db --profiles DIR [--days 14] [--profile alex]
"""
import argparse
import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from . import config, db, genai, ranking, removal, workterms
from .fits import family_filter

ALL_STATES = ("ok", "filtered", "non_us", "too_senior", "pending", "past_employer", "low_match")
GATES = ("fetched", "title_match", "my_families", "past_employer", "dealbreaker", "jd_gate", "not_w2", "non_us", "too_senior", "low_match",
         "pending", "closed_since", "board_off", "removed", "not_interested", "applied_before", "clone_merged",
         "shown", "shown_fresh_24h", "ghost_flagged")


def _day_uids(conn, profile: dict, start: str, end: str) -> tuple[int, int, list[str]]:
    fetched, titled = conn.execute("SELECT count(*), coalesce(sum(families <> ','), 0) FROM jobs INDEXED BY jobs_seen "
                                   "WHERE first_seen >= ? AND first_seen < ? AND seed = 0", (start, end)).fetchone()
    where, params = family_filter(tuple(profile["families"]))
    uids = [r[0] for r in conn.execute(f"SELECT j.uid FROM jobs j INDEXED BY jobs_seen WHERE j.first_seen >= ? "
                                       f"AND j.first_seen < ? AND j.seed = 0 AND {where}", (start, end, *params))]
    return fetched, titled, uids


def _board_off(conn, uids: list[str]) -> set[str]:
    out = set()
    for i in range(0, len(uids), ranking.SQL_BATCH):
        part = uids[i:i + ranking.SQL_BATCH]
        out |= {r[0] for r in conn.execute(f"SELECT j.uid FROM jobs j JOIN boards b ON b.board = j.board WHERE j.uid IN "
                                           f"({','.join('?' * len(part))}) AND (b.active = 0 OR b.review = 1)", part)}
    return out


def _gate(row: dict, s, off: set, hidden: set, states: dict, apps: set) -> str | None:
    """The first gate that keeps this job out of Recommended, or None when it is shown."""
    if s.state == "filtered":
        return "jd_gate" if genai.is_gate_verdict(s.dealbreaker) else "not_w2" if workterms.is_verdict(s.dealbreaker) \
            else "dealbreaker"
    if s.state != "ok":
        return s.state
    uid = row["uid"]
    checks = (("closed_since", bool(row.get("closed_at"))), ("board_off", uid in off), ("removed", uid in hidden),
              ("not_interested", states.get(uid) == "dismissed"), ("applied_before", uid in apps))
    return next((name for name, hit in checks if hit), None)


def day(conn, profile: dict, start: datetime, now: datetime) -> dict:
    """One day's funnel; each gate counts the jobs it was the first to stop."""
    pid = profile["id"]
    fetched, titled, uids = _day_uids(conn, profile, db.iso(start), db.iso(start + timedelta(days=1)))
    n = Counter(fetched=fetched, title_match=titled, my_families=len(uids))
    if uids:
        rows = ranking.rank(conn, profile, now, states=ALL_STATES, uids=uids)
        off, (hidden, _) = _board_off(conn, uids), removal.hidden(conn, pid)
        states = dict(conn.execute("SELECT uid, state FROM triage_now WHERE profile = ?", (pid,)).fetchall())
        apps = {r[0] for r in conn.execute("SELECT job_uid FROM applications WHERE profile = ? AND job_uid IS NOT NULL", (pid,))}
        left = []
        for row, s in rows:
            gate = _gate(row, s, off, hidden, states, apps)
            if gate:
                n[gate] += 1
            else:
                left.append((row, s))
        folded = ranking.collapse(conn, left)
        n["clone_merged"] = len(left) - len(folded)
        n["shown"] = len(folded)
        n["shown_fresh_24h"] = sum(1 for r, _ in folded
                                   if (r.get("first_posted_at") or "") >= db.iso(start - timedelta(hours=24)))
        n["ghost_flagged"] = sum(1 for _, s in folded if s.ghost)
    return {"day": start.date().isoformat(), **{g: n[g] for g in GATES}}


def funnel(conn, profile: dict, now: datetime, days: int) -> list[dict]:
    first = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
    return [day(conn, profile, first + timedelta(days=i), now) for i in range(days)]


def table(rows: list[dict]) -> str:
    head = "| day | " + " | ".join(GATES) + " |"
    rule = "|---" * (len(GATES) + 1) + "|"
    return "\n".join([head, rule, *("| " + " | ".join(str(r[k]) for k in ("day", *GATES)) + " |" for r in rows)])


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", required=True, help="a COPY of job_hunt_agent.db (never the live one)")
    ap.add_argument("--profiles", required=True)
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--profile", action="append")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if Path(args.db).resolve() == config.DB_PATH.resolve():
        raise SystemExit("feedaudit reads a copy: pass a .backup of job_hunt_agent.db, never the live file")
    config.DB_PATH, config.PROFILES = Path(args.db), Path(args.profiles)
    conn = db.connect()
    now = db.utcnow()
    out = {pid: funnel(conn, config.profile(pid), now, args.days) for pid in (args.profile or config.profile_ids())}
    print(json.dumps(out) if args.json else "\n\n".join(f"### {pid}\n\n{table(rows)}" for pid, rows in out.items()))


if __name__ == "__main__":
    main()
