"""Run cost bookkeeping for fetch (decision 005 B2 step 5, B3): the CPU/wall budget that stops a run from taking new
boards, the RLIMIT_CPU backstop, per-host backoff persistence, and the runs cost columns doctor reports.

The budget stops taking boards at CPU_BUDGET_S of process CPU (all threads) so the after-loop work (fits, notify,
Sheet flag, backup) still fits under the RLIMIT_CPU soft limit (skeptic minor 1). Boards a run did not reach get no
fetch_runs row and no last_fetch advance: they carry over to the next run.
"""
import logging
import resource
import signal
import time
from datetime import datetime, timezone

from . import db, net

CPU_BUDGET_S = 200
WALL_BUDGET_S = 20 * 60          # never run into the next 30-minute launchd start
RLIMIT_CPU_S = 300
RLIMIT_HARD_S = 330
log = logging.getLogger(__name__)


class Budget:
    def __init__(self, cpu_s: float = CPU_BUDGET_S, wall_s: float = WALL_BUDGET_S,
                 clock=time.process_time, wall=time.monotonic):
        self._cpu_s, self._wall_s, self._clock, self._wall = cpu_s, wall_s, clock, wall
        self.cpu0, self.wall0 = clock(), wall()
        self.exhausted = False

    def cpu_used(self) -> float:
        return self._clock() - self.cpu0

    def spent(self) -> bool:
        if self.cpu_used() > self._cpu_s or self._wall() - self.wall0 > self._wall_s:
            self.exhausted = True
        return self.exhausted


def _on_cpu_limit(signum, frame) -> None:
    raise SystemExit(f"fetch stopped: RLIMIT_CPU {RLIMIT_CPU_S}s reached (signal {signum})")


def limit_cpu(soft_s: int = RLIMIT_CPU_S, hard_s: int = RLIMIT_HARD_S) -> None:
    """Backstop for a CLI/launchd fetch process (never the web server): SIGXCPU at soft_s CPU seconds."""
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (soft_s, hard_s))
        signal.signal(signal.SIGXCPU, _on_cpu_limit)
    except (ValueError, OSError) as err:
        log.warning("could not set RLIMIT_CPU: %s", err)


def load_backoff(conn) -> None:
    rows = conn.execute("SELECT host, until, strikes FROM host_state").fetchall()
    state = {}
    for host, until, strikes in rows:
        moment = datetime.strptime(until, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) if until else None
        state[host] = {"until": moment.timestamp() if moment else 0, "strikes": strikes}
    net.load_backoff(state)


def save_backoff(conn, now: datetime) -> list[str]:
    """Persist net's backoff; returns alert lines for hosts paused right now."""
    alerts = []
    with db.tx(conn):
        for host, value in net.backoff_state().items():
            until = datetime.fromtimestamp(value["until"], tz=timezone.utc) if value["until"] else None
            paused = until is not None and until > now
            conn.execute("INSERT INTO host_state(host, until, strikes, updated_at) VALUES (?,?,?,?) "
                         "ON CONFLICT(host) DO UPDATE SET until=excluded.until, strikes=excluded.strikes, "
                         "updated_at=excluded.updated_at",
                         (host, db.iso(until) if paused else None, value["strikes"], db.iso(now)))
            if paused:
                alerts.append(f"{host}: refused us (403/429); paused until {db.iso(until)}, its boards carry over")
    return alerts


def finish(conn, run_id: int, budget: Budget, loop_cpu: float, n_due: int, n_taken: int) -> None:
    stats = net.stats()
    total = budget.cpu_used()
    with conn:
        conn.execute("UPDATE runs SET cpu_ms=?, cpu_sweep_ms=?, n_req=?, n_304=?, bytes=?, budget_exhausted=?, "
                     "n_due=?, n_taken=? WHERE id=?",
                     (int(total * 1000), int((total - loop_cpu) * 1000), stats["n_req"], stats["n_304"],
                      stats["bytes"], int(budget.exhausted), n_due, n_taken, run_id))
