"""SQLite connection, schema, boards and run bookkeeping. One file: data/job_hunt_agent.db (WAL)."""
import json
import sqlite3
import sys
import tarfile
import zlib
from contextlib import contextmanager
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path

from . import config

SCHEMA_VERSION = 31
# Repost chain (decision 002): priors on the same repost_key that ended (closed, or went stale) within 90 days
# before this job was first seen, or that were still open with the same specific req_id (has a digit; not
# 'MULTI'). Shared by the v4 backfill and store._write_item.
# A stale Workday prior (never closed, unseen >= 7 days) counts as ended at its last_seen.
_ENDED = "coalesce(p.closed_at, CASE WHEN p.stale = 1 THEN p.last_seen END)"
CHAIN_SELECT = ("SELECT min(coalesce(p.first_posted_at, p.first_seen)), count(*) FROM jobs p "
                "WHERE p.repost_key = {rk} AND p.uid <> {uid} AND {order} AND ("
                f"({_ENDED} <= {{first_seen}} "
                f"AND {_ENDED} >= strftime('%Y-%m-%dT%H:%M:%SZ', {{first_seen}}, '-90 days')) OR "
                f"(({_ENDED} IS NULL OR {_ENDED} > {{first_seen}}) AND {{req}} GLOB '*[0-9]*' AND p.req_id = {{req}}))")
_PLAIN_WORKPLACE = "trim(replace(replace(lower(workplace), '-', ''), '_', ''))"
BOARD_V10 = ("etag TEXT", "last_mod TEXT", "last_fetch TEXT", "last_full_at TEXT", "terms_at TEXT",
             "dead_streak INTEGER NOT NULL DEFAULT 0", "dropped TEXT", "dropped_at TEXT", "segment TEXT",
             "source TEXT", "website TEXT", "wd_total INTEGER")
RUNS_V10 = ("cpu_ms INTEGER", "cpu_sweep_ms INTEGER", "n_req INTEGER", "n_304 INTEGER", "bytes INTEGER",
            "budget_exhausted INTEGER NOT NULL DEFAULT 0", "n_due INTEGER", "n_taken INTEGER")
PROBES_V10 = ("source TEXT", "error TEXT", "next_try TEXT", "us60 INTEGER", "newest_us TEXT", "name_src TEXT",
              "segment TEXT", "verified_at TEXT")
# Statements run before re-applying schema.sql when upgrading TO that version.
MIGRATIONS = {2: ["DROP VIEW IF EXISTS followups_due"],
              3: ["ALTER TABLE discover_probes ADD COLUMN website TEXT",
                  # v2 probes lack Lever/Ashby names: expire them (kept, not deleted)
                  "UPDATE discover_probes SET checked_at = '1970-01-01T00:00:00Z'"],
              4: ["ALTER TABLE jobs ADD COLUMN chain_origin TEXT",
                  "ALTER TABLE jobs ADD COLUMN chain_n INTEGER NOT NULL DEFAULT 0",
                  "ALTER TABLE fits RENAME TO fits_v3",     # old (empty) cache layout, kept; schema.sql makes the new
                  "UPDATE jobs SET (chain_origin, chain_n) = (" + CHAIN_SELECT.format(
                      rk="jobs.repost_key", uid="jobs.uid", first_seen="jobs.first_seen",
                      req="coalesce(jobs.req_id, '')",
                      order="(p.first_seen < jobs.first_seen OR (p.first_seen = jobs.first_seen AND p.uid < jobs.uid))")
                  + ")"],
              # rows stored before ats.common._workplace normalized 'On-site' / 'Hybrid (travel-required)'
              5: ["UPDATE jobs SET workplace = CASE " + " ".join(
                  f"WHEN {_PLAIN_WORKPLACE} LIKE '{kind}%' THEN '{kind}'" for kind in ("remote", "hybrid", "onsite"))
                  + " ELSE workplace END WHERE workplace IS NOT NULL"],
              6: ["ALTER TABLE applications ADD COLUMN sheet_status TEXT"],
              # rows synced before v6: what their Status(A/R) cell showed, so an old file is no "edit" (M9 verify)
              7: ["UPDATE applications SET sheet_status = CASE WHEN lower(trim(status_ar)) IN "
                  "('saved', 'tailored', 'applied', 'screen', 'interview', 'offer', 'rejected', 'ghosted') "
                  "THEN lower(trim(status_ar)) ELSE '' END WHERE sheet_status IS NULL AND raw_json IS NOT NULL"],
              # employment type + staffing-agency signals (backfilled by `job-hunt-agent resignal`)
              8: ["ALTER TABLE jobs ADD COLUMN employment_type TEXT",
                  "ALTER TABLE jobs ADD COLUMN staffing INTEGER NOT NULL DEFAULT 0",
                  "ALTER TABLE jobs ADD COLUMN sponsorship TEXT"],
              # GOAL2 C2 (decision 006): Sheets/Drive sync state; events may come from the Sheet or Google.
              # SQLite cannot widen a CHECK in place: copy application_events, swap it in (same ids, same rows).
              9: ["ALTER TABLE applications ADD COLUMN notes TEXT",
                  "ALTER TABLE applications ADD COLUMN field_at TEXT NOT NULL DEFAULT '{}'",
                  "ALTER TABLE applications ADD COLUMN resume_manual TEXT",
                  "ALTER TABLE applications ADD COLUMN downloaded_at TEXT",
                  "CREATE TABLE application_events_v9 (id INTEGER PRIMARY KEY, "
                  "app_id INTEGER NOT NULL REFERENCES applications(id), at TEXT NOT NULL, from_status TEXT, "
                  "to_status TEXT, source TEXT NOT NULL CHECK (source IN ('ui', 'cli', 'import', 'sheet', 'google')), "
                  "snapshot TEXT)",
                  "INSERT INTO application_events_v9 SELECT id, app_id, at, from_status, to_status, source, snapshot "
                  "FROM application_events",
                  "DROP TABLE application_events",
                  "ALTER TABLE application_events_v9 RENAME TO application_events"],
              # GOAL2 B (decision 005): tiered conditional fetching, sticky drops, run cost, discovery funnel.
              10: [*(f"ALTER TABLE boards ADD COLUMN {c}" for c in BOARD_V10),
                   "ALTER TABLE jobs ADD COLUMN raw_fp INTEGER",
                   *(f"ALTER TABLE runs ADD COLUMN {c}" for c in RUNS_V10),
                   *(f"ALTER TABLE discover_probes ADD COLUMN {c}" for c in PROBES_V10),
                   # robots.txt of api.smartrecruiters.com is 'Disallow: /' (decision 005): stop, keep history
                   "UPDATE boards SET active = 0, dropped = 'robots: api.smartrecruiters.com disallows /', "
                   "dropped_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE ats = 'smartrecruiters'"]}
APPS_V11 = ("job_url TEXT", "apply_url TEXT", "location TEXT", "workplace TEXT", "salary TEXT", "employment_type TEXT",
            "source TEXT", "address TEXT", "team_name TEXT")
_SALARY_TEXT = ("CASE WHEN j.salary_min IS NOT NULL AND j.salary_max IS NOT NULL AND j.salary_max <> j.salary_min "
                "THEN printf('$%.0fk to $%.0fk', j.salary_min / 1000.0, j.salary_max / 1000.0) "
                "WHEN coalesce(j.salary_min, j.salary_max) IS NOT NULL "
                "THEN printf('$%.0fk', coalesce(j.salary_min, j.salary_max) / 1000.0) END")


def _raw_extra(header: str) -> str:
    """SQL: a mapped-import extra column (tagged cell with 'col', tracker_map.py) out of raw_json, as text."""
    return (f"(SELECT CASE json_extract(e.value, '$.t') WHEN 'num' THEN CAST(json_extract(e.value, '$.v') AS TEXT) "
            f"ELSE json_extract(e.value, '$.v') END FROM json_each(CASE WHEN json_valid(applications.raw_json) "
            f"THEN applications.raw_json ELSE '[]' END) e WHERE json_extract(e.value, '$.col') = '{header}')")


MIGRATIONS[11] = [                  # GOAL3 tracker: job facts on the row, origin, Jordan's extras; 'tailored' retired
    *(f"ALTER TABLE applications ADD COLUMN {c}" for c in APPS_V11),
    "UPDATE applications SET source = CASE WHEN raw_json IS NOT NULL THEN 'import' "
    "WHEN job_uid IS NOT NULL THEN 'feed' ELSE 'manual' END",
    "UPDATE applications SET (job_url, apply_url, location, workplace, employment_type, salary) = "
    f"(SELECT j.url, coalesce(j.apply_url, j.url), j.location, j.workplace, j.employment_type, {_SALARY_TEXT} "
    "FROM jobs j WHERE j.uid = applications.job_uid) WHERE job_uid IS NOT NULL",
    f"UPDATE applications SET address = {_raw_extra('Address')}, team_name = {_raw_extra('Team Name')} "
    "WHERE raw_json IS NOT NULL",
    "INSERT INTO application_events(app_id, at, from_status, to_status, source) "
    "SELECT id, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), 'tailored', 'saved', 'cli' FROM applications "
    "WHERE status = 'tailored'",
    "UPDATE applications SET status = 'saved' WHERE status = 'tailored'",
    # the vendors table as v11 first made it (v12 adds origin): lets a v10 database take step 12 too
    "CREATE TABLE IF NOT EXISTS vendors (id INTEGER PRIMARY KEY, profile TEXT NOT NULL REFERENCES profiles(id), "
    "pos INTEGER NOT NULL DEFAULT 0, company TEXT, target_name TEXT, target_role TEXT, linkedin TEXT, email TEXT, "
    "email_sent TEXT, notes TEXT, updated_at TEXT)"]
MIGRATIONS[12] = ["ALTER TABLE vendors ADD COLUMN origin TEXT NOT NULL DEFAULT 'import' "
                  "CHECK (origin IN ('import', 'app'))"]
MIGRATIONS[13] = ["ALTER TABLE applications ADD COLUMN resume_used TEXT",      # resume id at Mark applied
                  "ALTER TABLE applications ADD COLUMN match_pct INTEGER"]     # its keyword % then
# Address (Alex's new column, Jordan's own) filled from the job's location on rows the app created; only
# empty cells, never an imported or typed address
MIGRATIONS[14] = ["UPDATE applications SET address = location WHERE coalesce(address, '') = '' "
                  "AND coalesce(location, '') <> '' AND source IN ('feed', 'manual')"]
# gsync_rows + gsync_log (new tables only: schema.sql creates them)
# GOAL4 §4 addition: resume_uploads (version 16; new table only)
# resume_missing (rows the app marked applied with no uploaded file get the "resume missing" chip),
# contacts.source; edit_history, jd_snapshots, contact_lookups are new tables (schema.sql)
MIGRATIONS[17] = ["ALTER TABLE applications ADD COLUMN resume_missing INTEGER NOT NULL DEFAULT 0",
                  "ALTER TABLE contacts ADD COLUMN source TEXT",
                  "UPDATE applications SET resume_missing = 1 WHERE source IN ('feed', 'manual') AND status IN "
                  "('applied', 'screen', 'interview', 'offer', 'rejected', 'ghosted') "
                  "AND id NOT IN (SELECT app_id FROM resume_uploads)"]
# GOAL6 JD format (2026-10-06): the full JD text a row's JD cell links to, synced with the hidden "JD Text" column
MIGRATIONS[18] = ["ALTER TABLE applications ADD COLUMN jd_text TEXT"]
# GOAL7 §4 (version 19): jobs_company index (schema.sql) so the job detail's company panel is not a 500k-row scan
# GOAL11 §1 (version 22): jd_docs (schema.sql), the full JD as a Google Doc per application
# GOAL11 §5 (version 23): google_moves (schema.sql), a person's files moved to their own Drive
# GOAL12 (version 24; GOAL11 took 22 and 23): status changes read from job emails are their own event source
# (the CHECK is widened by a copy, as in step 9); mail_messages is a new table (schema.sql)
MIGRATIONS[24] = ["CREATE TABLE application_events_v24 (id INTEGER PRIMARY KEY, "
                  "app_id INTEGER NOT NULL REFERENCES applications(id), at TEXT NOT NULL, from_status TEXT, "
                  "to_status TEXT, source TEXT NOT NULL CHECK (source IN "
                  "('ui', 'cli', 'import', 'sheet', 'google', 'email')), snapshot TEXT)",
                  "INSERT INTO application_events_v24 SELECT id, app_id, at, from_status, to_status, source, snapshot "
                  "FROM application_events",
                  "DROP TABLE application_events",
                  "ALTER TABLE application_events_v24 RENAME TO application_events"]
# GOAL14 §0 (version 25): Claude reads every job email nightly; the row keeps Claude's one-sentence reason and how sure
# it was, and the rules' own answer (the cross-check shown in "Needs you")
MIGRATIONS[25] = ["ALTER TABLE mail_messages ADD COLUMN rule_kind TEXT",
                  "ALTER TABLE mail_messages ADD COLUMN claude_reason TEXT",
                  "ALTER TABLE mail_messages ADD COLUMN confidence TEXT"]
# GOAL15 §4 (version 26): a row's resume can be removed or replaced; the file stays in Drive (moved to Replaced/) and
# its upload row says when and in which batch (Undo)
MIGRATIONS[26] = ["ALTER TABLE resume_uploads ADD COLUMN removed_at TEXT",
                  "ALTER TABLE resume_uploads ADD COLUMN removed_batch TEXT"]
# GOAL21 W17 (version 27): jobs_fam index (schema.sql), the ranking's covering index (~5 s to build on 1M jobs)
# GOAL22 (version 28): resume_places (schema.sql), the city a person picked for a job's resume
# GOAL23 (version 29): the Autofill tables and jobs_ext (schema.sql; the index takes a few seconds on 1M jobs)
# GOAL24 (version 30): the h1b_* tables (schema.sql), public H-1B / PERM filings per employer and company
# GOAL25 (version 31): learned answers, company overrides, snippets, fill errors, canary, apply queue
# (new tables only, schema.sql)
BUSY_TIMEOUT_MS = 10_000
BACKUPS_KEPT = 14
PROFILE_FILES = ("profile.yaml", "baseline.json", "known_skills.txt")
PRIVATE_DIR, PRIVATE_FILE = 0o700, 0o600


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(path or config.DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys=ON")
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version < SCHEMA_VERSION and _upgrade(conn, version) and 0 < version < 12:
        _adopt_goal3(conn)                                      # contacts + vendors out of the old cells
    return conn


def _adopt_goal3(conn: sqlite3.Connection) -> None:
    """Derive contacts from the tracker cells and adopt vendors.json. Both are idempotent and re-runnable (the
    contacts by `job-hunt-agent tracker import`, the vendors on the next import), so a failure is logged, not raised."""
    from . import contacts, vendors
    for step in (contacts.refresh_legacy, vendors.adopt_json):
        try:
            step(conn)
        except (sqlite3.Error, ValueError, OSError) as err:
            print(f"job-hunt-agent: upgrade step {step.__module__}.{step.__name__} failed: {err}", file=sys.stderr)


def _upgrade(conn: sqlite3.Connection, version: int) -> bool:
    """Migrations + schema.sql + user_version in ONE transaction, so a failed upgrade leaves the old DB intact.
    False when another process (fetch vs server at login) upgraded the DB while this one waited for the lock."""
    steps = [s for v in range(version + 1, SCHEMA_VERSION + 1) for s in MIGRATIONS.get(v, [])] if version else []
    schema = resources.files("job_hunt_agent").joinpath("schema.sql").read_text()
    try:
        conn.executescript("BEGIN IMMEDIATE;\n" + "".join(f"{s};\n" for s in steps) + schema
                           + f"\nPRAGMA user_version={SCHEMA_VERSION};\nCOMMIT;")
    except sqlite3.Error:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        if conn.execute("PRAGMA user_version").fetchone()[0] >= SCHEMA_VERSION:
            return False
        raise
    return True


@contextmanager
def tx(conn: sqlite3.Connection):
    """Explicit write transaction (connections run in autocommit mode, so `with conn:` alone is not atomic)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def pack(text: str | None) -> bytes | None:
    return zlib.compress(text.encode(), 6) if text else None


def unpack(blob: bytes | None) -> str:
    return zlib.decompress(blob).decode() if blob else ""


def sync_boards(conn: sqlite3.Connection, boards: list[dict]) -> None:
    """The registry (companies.yaml + companies.discovered.yaml) lists the boards: upsert them, deactivate the rest.
    A board the fetcher dropped (dead after 4 straight 404s, robots) stays inactive while `dropped` is set
    (decision 005 skeptic objection 5); its history is kept."""
    with tx(conn):
        for b in boards:
            conn.execute(
                "INSERT INTO boards(board, ats, key, company, tier, review, active, segment, source, website) "
                "VALUES (?,?,?,?,?,?,1,?,?,?) "
                "ON CONFLICT(board) DO UPDATE SET company=excluded.company, tier=coalesce(excluded.tier, boards.tier), "
                "review=excluded.review, active=CASE WHEN boards.dropped IS NULL THEN 1 ELSE 0 END, "
                "segment=coalesce(excluded.segment, boards.segment), source=coalesce(excluded.source, boards.source), "
                "website=coalesce(excluded.website, boards.website)",
                (b["board"], b["ats"], b["key"], b["company"], b.get("tier"), int(bool(b.get("review"))),
                 b.get("segment"), b.get("source"), b.get("website")),
            )
        listed = [b["board"] for b in boards]
        marks = ",".join("?" * len(listed)) or "''"
        conn.execute(f"UPDATE boards SET active=0 WHERE board NOT IN ({marks})", listed)


def board(conn: sqlite3.Connection, board_id: str) -> dict:
    row = conn.execute("SELECT * FROM boards WHERE board=?", (board_id,)).fetchone()
    return dict(row) if row else {}


def prior_statuses(conn: sqlite3.Connection, board_id: str, limit: int = 2) -> list[str]:
    rows = conn.execute(
        "SELECT status FROM fetch_runs WHERE board=? ORDER BY run_id DESC LIMIT ?", (board_id, limit)
    ).fetchall()
    return [r["status"] for r in rows]


def uids_with_jd(conn: sqlite3.Connection, board_id: str) -> set[str]:
    rows = conn.execute("SELECT uid FROM jobs WHERE board=? AND description IS NOT NULL", (board_id,))
    return {r["uid"] for r in rows}


def start_run(conn: sqlite3.Connection, trigger: str, now: datetime) -> int:
    with conn:
        cur = conn.execute("INSERT INTO runs(trigger, started_at) VALUES (?,?)", (trigger, iso(now)))
    return cur.lastrowid


def finish_run(conn: sqlite3.Connection, run_id: int, started: datetime, host_alerts: list[str]) -> None:
    finished = utcnow()
    with conn:
        conn.execute(
            "UPDATE runs SET finished_at=?, duration_ms=?, host_alerts=? WHERE id=?",
            (iso(finished), int((finished - started).total_seconds() * 1000), json.dumps(host_alerts), run_id),
        )


def last_run_started(conn: sqlite3.Connection) -> datetime | None:
    row = conn.execute("SELECT started_at FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    return datetime.strptime(row[0], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) if row else None


def backup(conn: sqlite3.Connection, folder: Path | None = None) -> Path | None:
    """Daily sqlite3 backup into data/backups/ plus a tar of the profiles' gitignored files (profile.yaml,
    baseline.json, known_skills.txt), keeping the newest BACKUPS_KEPT of each. Private files: data/ 0700, files 0600."""
    folder = folder or config.DATA / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"job_hunt_agent-{utcnow():%Y%m%d}.db"
    profiles = folder / f"profiles-{utcnow():%Y%m%d}.tar.gz"
    made = None
    if not target.exists():                                     # the database first: it matters most
        dest = sqlite3.connect(target)
        with dest:
            conn.backup(dest)
        dest.close()
        made = target
    try:
        if not profiles.exists():                               # its own daily check (M9 verify rounds 3-4)
            _backup_profiles(profiles)
        for pattern in ("job_hunt_agent-*.db", "profiles-*.tar.gz"):
            for old in sorted(folder.glob(pattern))[:-BACKUPS_KEPT]:
                old.unlink()
    finally:
        secure(conn, folder)                                    # every run, even when the tar failed (M9 verify)
    return made


def _backup_profiles(target: Path) -> None:
    """Written to a temporary name and renamed, so a failed tar never counts as today's backup."""
    partial = target.with_name(target.name + ".part")
    try:
        with tarfile.open(partial, "w:gz") as tar:
            for path in sorted(config.PROFILES.glob("*/*")):
                if path.name in PROFILE_FILES:
                    tar.add(path, arcname=str(path.relative_to(config.PROFILES)))
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)


def secure(conn: sqlite3.Connection, backups: Path) -> None:
    """Owner-only access to the DB's folder, the DB with its WAL files, and the backups (release review)."""
    db_file = Path(conn.execute("PRAGMA database_list").fetchone()[2])
    for path in (db_file.parent, backups):
        path.chmod(PRIVATE_DIR)
    for path in [*db_file.parent.glob(db_file.name + "*"), *backups.iterdir()]:
        if path.is_file():
            path.chmod(PRIVATE_FILE)
