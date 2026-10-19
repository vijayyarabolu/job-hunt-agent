"""Per-rate-bucket scheduling for a fetch run (decision 005 run 2 (b), judge conditions 1, 2, 6).

Every rate bucket (net.bucket_key: boards-api.greenhouse.io, api.ashbyhq.com, api.lever.co, each Workday data center,
www.amazon.jobs) has its own queue, already in priority order (tiers.plan: overdue t1 by
lateness, then t2, t3, then Workday term sweeps). A bucket keeps at most `in_flight` boards running: 1, or 3 for a
Workday data center (net still keeps the 0.34 s start-to-start gap per bucket). A board starts only if its estimated
cost fits in what is left of the bucket's deadline. Costs are in SECONDS: the EWMA of the board's recent fetch
latency (its latency already includes the per-request max(gap, response time)), else a per-ATS default.

Boards a bucket could not start carry over (no fetch_runs row, no last_fetch advance). The per-bucket result goes to
run_buckets; doctor alerts when a bucket's t1 need passes 60 % of its deadline, and the response is a longer
deadline or more in-flight, never a narrower t1 (condition 6).
"""
import time
from collections import defaultdict
from dataclasses import dataclass, field

from . import net

DEADLINE_S = 600.0                # launchd runs every 30 min; each bucket may work 10 min of it
WORKDAY_IN_FLIGHT = 3
EWMA_ALPHA = 0.5
EWMA_SAMPLES = 5
ALERT_SHARE = 0.6
DEFAULT_COST_S = {"greenhouse": 0.6, "ashby": 0.8, "lever": 2.0, "workday": 4.0, "amazon": 15.0,
                  "smartrecruiters": 3.0, "workable": 1.0,
                  "eightfold": 20.0, "phenom": 10.0, "icims": 50.0,     # icims: 9 terms x robots crawl-delay 5 s
                  "oracle_recruiting": 15.0, "taleo": 25.0, "successfactors": 25.0,   # 11 terms, 1 s gap
                  "icims_classic": 3.0, "jobvite": 3.0,
                  "bamboohr": 1.0, "rippling": 1.0, "gem": 0.8,                       # one list request
                  "adp": 2.0, "paylocity": 1.0, "dayforce": 3.0,
                  "cornerstone": 12.0,                                    # page + search under a 10 s crawl delay
                  "jazzhr": 1.0, "ibm": 15.0,
                  "recruitee": 0.8, "breezy": 1.0, "pinpoint": 0.8, "hibob": 0.8, "jobscore": 0.8}


def in_flight_for(bucket: str) -> int:
    return WORKDAY_IN_FLIGHT if bucket.endswith("myworkdayjobs.com") else 1


def ewma(latencies_ms: list[int]) -> float | None:
    """Newest-first latencies -> EWMA seconds (None without samples)."""
    value = None
    for ms in reversed([m for m in latencies_ms if m is not None]):
        value = ms / 1000 if value is None else EWMA_ALPHA * (ms / 1000) + (1 - EWMA_ALPHA) * value
    return value


def board_costs(conn, board_ids: list[str]) -> dict[str, float]:
    """board -> EWMA seconds over its last EWMA_SAMPLES fetches that got an answer."""
    if not board_ids:
        return {}
    rows = conn.execute(
        "SELECT board, latency_ms FROM (SELECT board, latency_ms, row_number() OVER (PARTITION BY board "
        "ORDER BY run_id DESC) AS rn FROM fetch_runs WHERE status <> 'skipped') WHERE rn <= ?", (EWMA_SAMPLES,))
    samples: dict[str, list[int]] = defaultdict(list)
    wanted = set(board_ids)
    for board, ms in rows:
        if board in wanted:
            samples[board].append(ms)
    return {b: v for b, v in ((b, ewma(s)) for b, s in samples.items()) if v is not None}


@dataclass
class Bucket:
    name: str
    in_flight: int
    deadline_s: float
    queue: list = field(default_factory=list)
    running: int = 0
    busy_s: float = 0.0
    taken: int = 0
    t1_due: int = 0
    t1_done: int = 0
    est_need_s: float = 0.0
    closed: bool = False


class Scheduler:
    """Pure bookkeeping (the caller submits and reports completions). clock is injectable for tests."""

    def __init__(self, tasks: list, bucket_of, cost_of, deadline_s: float = DEADLINE_S, clock=time.monotonic):
        self.clock, self.start = clock, clock()
        self.cost_of = cost_of
        self.buckets: dict[str, Bucket] = {}
        for task in tasks:
            name = bucket_of(task)
            bucket = self.buckets.setdefault(name, Bucket(name, in_flight_for(name), deadline_s))
            bucket.queue.append(task)
            bucket.t1_due += task.tier == 1
            bucket.est_need_s += cost_of(task) / bucket.in_flight
        self._started: dict[int, float] = {}

    def ready(self, stop: bool = False) -> list[tuple[str, object]]:
        """(bucket, task) pairs to start now. stop=True (CPU budget spent) closes every bucket."""
        out = []
        elapsed = self.clock() - self.start
        for bucket in self.buckets.values():
            if stop:
                bucket.closed = True
            while not bucket.closed and bucket.queue and bucket.running < bucket.in_flight:
                task = bucket.queue[0]
                if elapsed + self.cost_of(task) / bucket.in_flight > bucket.deadline_s:
                    bucket.closed = True                  # the rest of this bucket carries over
                    break
                bucket.queue.pop(0)
                bucket.running += 1
                bucket.taken += 1
                self._started[id(task)] = self.clock()
                out.append((bucket.name, task))
        return out

    def done(self, bucket_name: str, task) -> None:
        bucket = self.buckets[bucket_name]
        bucket.running -= 1
        bucket.busy_s += (self.clock() - self._started.pop(id(task), self.clock())) / bucket.in_flight
        bucket.t1_done += task.tier == 1

    def cancel(self, bucket_name: str, task) -> None:
        """A started board that could not run (host paused): it carries over and counts as not done."""
        self.buckets[bucket_name].running -= 1
        self.buckets[bucket_name].taken -= 1
        self.buckets[bucket_name].queue.append(task)
        self._started.pop(id(task), None)

    def carried(self) -> int:
        return sum(len(b.queue) for b in self.buckets.values())

    def rows(self, run_id: int) -> list[tuple]:
        stats = net.bucket_stats()
        return [(run_id, b.name, stats.get(b.name, {}).get("n_req", 0), stats.get(b.name, {}).get("n_304", 0),
                 round(b.busy_s, 1), b.deadline_s, round(b.est_need_s, 1), b.t1_due, b.t1_done, b.taken,
                 len(b.queue)) for b in self.buckets.values()]


def save(conn, rows: list[tuple]) -> None:
    with conn:
        conn.executemany("INSERT OR REPLACE INTO run_buckets(run_id, bucket, n_req, n_304, busy_s, budget_s, "
                         "est_need_s, t1_due, t1_done, taken, carried) VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
