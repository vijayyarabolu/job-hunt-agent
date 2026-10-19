"""how much of each application Autofill filled by itself vs by hand (Answers page › Stats).

Per application and step the largest count reported counts once (clicking Autofill twice on a step is not twice the
fields): auto = autofill_log.filled, by hand = autofill_hand.hand. The trend is the share filled automatically per
week, so learning shows up as the line going up."""
from datetime import datetime, timedelta, timezone

WEEKS = 8
APPS = 20


def _per_step(conn, pid: str, table: str, column: str, since: str) -> dict:
    out: dict = {}
    for r in conn.execute(f"SELECT coalesce(job_uid, '') AS uid, coalesce(app_id, 0) AS app, coalesce(board, '') AS b, "
                          f"coalesce(step, '') AS s, max({column}) AS n, max(at) AS at FROM {table} WHERE profile = ? "
                          f"AND at >= ? AND (job_uid IS NOT NULL OR app_id IS NOT NULL) GROUP BY 1, 2, 3, 4",
                          (pid, since)):
        job = (r["uid"], r["app"])
        entry = out.setdefault(job, {"n": 0, "at": ""})
        entry["n"] += r["n"] or 0
        entry["at"] = max(entry["at"], r["at"] or "")
    return out


def _pct(auto: int, hand: int) -> int | None:
    return round(100 * auto / (auto + hand)) if auto + hand else None


def _names(conn, pid: str, job: tuple) -> tuple[str, str]:
    uid, app = job
    row = None
    if app:
        row = conn.execute("SELECT company, role_title AS title FROM applications WHERE id = ? AND profile = ?",
                           (app, pid)).fetchone()
    elif uid:
        row = conn.execute("SELECT company, title FROM jobs WHERE uid = ?", (uid,)).fetchone()
    return (row["company"] or "", row["title"] or "") if row else ("", "")


def _week(at: str) -> str:
    day = datetime.strptime(at[:10], "%Y-%m-%d")
    return (day - timedelta(days=day.weekday())).strftime("%Y-%m-%d")


def summary(conn, pid: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(weeks=WEEKS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    auto = _per_step(conn, pid, "autofill_log", "filled", since)
    hand = _per_step(conn, pid, "autofill_hand", "hand", since)
    jobs = sorted(set(auto) | set(hand), key=lambda j: max((auto.get(j) or {}).get("at", ""),
                                                           (hand.get(j) or {}).get("at", "")), reverse=True)
    apps, weeks = [], {}
    for job in jobs:
        a, h = (auto.get(job) or {}).get("n", 0), (hand.get(job) or {}).get("n", 0)
        at = max((auto.get(job) or {}).get("at", ""), (hand.get(job) or {}).get("at", ""))
        week = weeks.setdefault(_week(at), [0, 0])
        week[0] += a
        week[1] += h
        if len(apps) < APPS:
            company, title = _names(conn, pid, job)
            apps.append({"company": company, "title": title, "auto": a, "hand": h, "pct": _pct(a, h), "at": at})
    total_a, total_h = sum(w[0] for w in weeks.values()), sum(w[1] for w in weeks.values())
    return {"overall": {"auto": total_a, "hand": total_h, "pct": _pct(total_a, total_h)},
            "weeks": [{"week": w, "auto": v[0], "hand": v[1], "pct": _pct(*v)} for w, v in sorted(weeks.items())],
            "apps": apps}
