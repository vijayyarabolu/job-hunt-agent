"""GOAL33 §3b: the logs that made the DB grow (2.0 M job events, 1.3 M JD versions, 1.1 M per-board fetch rows: about a
third of 2.7 GB), none of which a page reads, are archived and then deleted once a day after the verified backup:
- job events older than KEEP_DAYS, and every event of a job outside the person's title families;
- JD versions of jobs outside the families (their JD text was never kept either);
- the per-board fetch log of runs older than KEEP_DAYS, but never a board's newest KEEP_PER_BOARD rows (the
  scheduler and Health read the last few runs only).
Every row goes first to data/archive/<table>-<day>.jsonl.gz, then is deleted BATCH rows per transaction so a page
or the fetcher never waits long. Jobs, applications and everything typed into the app are never touched. SQLite
reuses the freed pages (the file stops growing); shrinking the file itself is `job-hunt-agent compact` (VACUUM INTO a new
file, checked, swapped by hand while nothing runs)."""
import gzip
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from . import config, db

log = logging.getLogger(__name__)
KEEP_DAYS = 14
BATCH = 5000
KEEP_PER_BOARD = 5          # a board's newest fetch rows stay whatever their age: buckets' EWMA reads 5, guard reads 2,
                            # and a dead board is fetched once a week (tiers.DEAD_INTERVAL)
OUTSIDE = "SELECT uid FROM jobs WHERE families = ','"
KEPT_TABLES = ("applications", "application_events", "jobs", "autofill_answers", "triage_events", "edit_history")


def _targets(now: datetime) -> dict:
    """table -> (rowid SELECT, params): the rows to archive and delete."""
    cutoff = db.iso(now - timedelta(days=KEEP_DAYS))
    return {
        "job_events": (f"SELECT rowid FROM job_events WHERE at < ? OR uid IN ({OUTSIDE})", (cutoff,)),
        "jd_versions": (f"SELECT rowid FROM jd_versions WHERE uid IN ({OUTSIDE})", ()),
        "fetch_runs": ("SELECT rowid FROM (SELECT rowid, run_id, row_number() OVER (PARTITION BY board ORDER BY run_id DESC) AS rn "
                       "FROM fetch_runs) WHERE rn > ? AND run_id IN (SELECT id FROM runs WHERE started_at < ?)",
                       (KEEP_PER_BOARD, cutoff)),
    }


def _archive(folder: Path, table: str, now: datetime, rows: list) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    with gzip.open(folder / f"{table}-{now:%Y%m%d}.jsonl.gz", "at") as out:
        for row in rows:
            out.write(json.dumps(dict(row), default=str) + "\n")


def _prune(conn, table: str, ids: list[int], folder: Path, now: datetime, batch: int) -> None:
    for start in range(0, len(ids), batch):
        part = ids[start:start + batch]
        marks = ",".join("?" * len(part))
        rows = conn.execute(f"SELECT * FROM {table} WHERE rowid IN ({marks})", part).fetchall()
        _archive(folder, table, now, rows)                  # archived before it is deleted
        with db.tx(conn):
            conn.execute(f"DELETE FROM {table} WHERE rowid IN ({marks})", part)


def run(conn, now: datetime | None = None, apply: bool = True, batch: int = BATCH, folder: Path | None = None) -> dict:
    """{table: rows pruned (or that would be, apply=False)}."""
    now = now or db.utcnow()
    folder = folder or config.DATA / "archive"
    counts = {}
    for table, (query, params) in _targets(now).items():
        ids = [r[0] for r in conn.execute(query, params)]
        counts[table] = len(ids)
        if apply and ids:
            _prune(conn, table, ids, folder, now, batch)
    if apply and any(counts.values()):
        log.info("pruned (archived in %s): %s", folder, counts)
    return counts


def compact(conn, into: Path) -> dict:
    """VACUUM INTO a new file (the open DB is only read), then check it: integrity and the row counts of the tables
    that hold the user's data. The swap is by hand, with every service stopped (Prompts/PROGRESS33.md › Compact)."""
    into = Path(into)
    if into.exists():
        raise ValueError(f"{into} exists: pick a new file")
    conn.execute("VACUUM INTO ?", (str(into),))
    into.chmod(db.PRIVATE_FILE)                         # private like the DB itself
    check = db.connect(into)
    try:
        ok = check.execute("PRAGMA integrity_check").fetchone()[0]
        counts = {t: (conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0], check.execute(f"SELECT count(*) FROM {t}").fetchone()[0])
                  for t in KEPT_TABLES}
    finally:
        check.close()
    return {"file": str(into), "bytes": into.stat().st_size, "integrity": ok, "counts": counts,
            "ok": ok == "ok" and all(a == b for a, b in counts.values())}
