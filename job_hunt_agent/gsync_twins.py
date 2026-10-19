"""One job, two rows (GOAL6 §1 cleanup finding): the person types the job into the Sheet first, then marks it applied
in the app. The typed row is already keyed (it became an application on the last cycle), so pair_typed() no longer
sees it, and the app used to append a second row the person then had to blank by hand (Torc, iCapital, Solace,
Blissway, Appnovation on 2026-10-06).

Before appending, an application the app made is merged into the one typed row of the same job: same company
(spelling aside), the same role title (or one contains the other), dated within PAIR_DAYS. Only an unambiguous
pair merges. The typed row keeps every Sheet column (the Sheet wins); it gains the app's own fields it lacks (posting
link, job id, resume used, ...), and the app-made application's history, contacts, uploads and JD copy move over.
The app-made application was never on the Sheet, so nothing on the Sheet changes.
"""
import logging
from datetime import date

from . import db, gsync
from .gsync_cycle import PAIR_DAYS, similar_company
from .text import norm

log = logging.getLogger(__name__)
APP_ONLY = ("job_uid", "job_url", "apply_url", "location", "workplace", "salary", "employment_type", "resume_used",
            "match_pct", "apply_clicked_at", "resume_missing")
DROPPED = ("sheet_sync", "resume_versions", "gsync_rows")    # the app row's own sync state goes with it
ONE_PER_APP = ("jd_snapshots", "jd_docs")                    # the Sheet row's own stays when it has one


MIN_ROLE_WORDS = 2                         # "Engineer" alone is inside too many titles to call it the same role


def same_role(a: str | None, b: str | None) -> bool:
    x, y = norm(a or ""), norm(b or "")
    if not (x and y):
        return False
    short, long_ = sorted((x, y), key=len)
    return x == y or (short in long_ and len(short.split()) >= MIN_ROLE_WORDS)


def _near(a: dict, b: dict) -> bool:
    if not a.get("applied_on") or not b.get("applied_on"):
        return False                           # undated: not sure it is the same application
    return abs((date.fromisoformat(a["applied_on"]) - date.fromisoformat(b["applied_on"])).days) <= PAIR_DAYS


def find_twins(conn, pid: str, appendable: list[dict], apps: dict[int, dict]) -> list[tuple[dict, dict]]:
    """(app-made application, typed application) pairs to merge; each side at most once."""
    on_sheet = {r["app_id"] for r in conn.execute(
        "SELECT app_id FROM gsync_rows WHERE profile = ? AND missing_since IS NULL", (pid,))}
    typed = [a for a in apps.values() if a["id"] in on_sheet and a.get("source") == "sheet" and not a.get("job_uid")]
    pairs, used = [], set()
    for made in appendable:
        found = [t for t in typed if similar_company(t["company"], made["company"])
                 and same_role(t["role_title"], made["role_title"]) and _near(t, made)]
        if len(found) != 1 or found[0]["id"] in used:
            continue
        rivals = [m for m in appendable if m is not made and similar_company(found[0]["company"], m["company"])
                  and same_role(found[0]["role_title"], m["role_title"])]
        if rivals:
            continue
        used.add(found[0]["id"])
        pairs.append((made, found[0]))
    return pairs


def child_tables(conn) -> list[str]:
    """Every table whose app_id points at applications, read from the schema so a new one is never missed."""
    return [r["name"] for r in conn.execute(
        "SELECT m.name FROM sqlite_master m JOIN pragma_foreign_key_list(m.name) f "
        "WHERE m.type = 'table' AND f.\"table\" = 'applications' AND f.\"from\" = 'app_id' ORDER BY m.name")]


def _move_children(conn, table: str, made_id: int, typed_id: int) -> None:
    if table in DROPPED or (table in ONE_PER_APP and conn.execute(
            f"SELECT 1 FROM {table} WHERE app_id = ?", (typed_id,)).fetchone()):
        conn.execute(f"DELETE FROM {table} WHERE app_id = ?", (made_id,))
    elif table == "contacts":                      # the app's contacts go after the Sheet row's own
        after = conn.execute("SELECT coalesce(max(pos), -1) + 1 FROM contacts WHERE app_id = ?",
                             (typed_id,)).fetchone()[0]
        conn.execute("UPDATE contacts SET app_id = ?, pos = pos + ? WHERE app_id = ?", (typed_id, after, made_id))
    else:
        conn.execute(f"UPDATE {table} SET app_id = ? WHERE app_id = ?", (typed_id, made_id))


def merge(conn, pid: str, made: dict, typed: dict) -> None:
    """Fold the app-made application into the typed one (one transaction) and log it."""
    fills = {f: made[f] for f in APP_ONLY if made.get(f) not in (None, "") and typed.get(f) in (None, "", 0)}
    with db.tx(conn):
        for table in child_tables(conn):
            _move_children(conn, table, made["id"], typed["id"])
        conn.execute("DELETE FROM applications WHERE id = ?", (made["id"],))
        if fills:                                  # after the delete: job_uid is unique per person
            conn.execute(f"UPDATE applications SET {', '.join(f'{f} = ?' for f in fills)} WHERE id = ?",
                         [*fills.values(), typed["id"]])
        gsync.log_event(conn, pid, "merged", None, typed["id"], None,
                        f"{typed.get('company')} · {typed.get('role_title')}",
                        f"app #{made['id']} folded in: {', '.join(sorted(fills)) or 'history only'}")
    log.info("%s: app #%d merged into the typed row #%d", pid, made["id"], typed["id"])


POSTING_FIELDS = ("job_uid", "job_url", "apply_url", "location", "workplace", "salary", "employment_type", "match_pct",
                  "apply_clicked_at", "resume_used")


def detach_reused(conn, pid: str) -> list[int]:
    """A Sheet row the person reused for another job (2026-10-06: Jordan typed Invenergy into the row the user had blanked
    for an Appnovation duplicate). When the Sheet changed the row's company to one that is not the linked posting's,
    the old posting's app-only fields and saved JD no longer belong to it: they are dropped and the JD is saved again
    from the row's own cell. Only when the Sheet changed both the company and the role away from the posting's (a
    renamed company alone, "Alphabet" -> "Google", keeps everything). None of these fields is a Sheet column, so
    nothing on the Sheet changes."""
    from . import jdsnap                                         # late: jdsnap imports tracker -> gsync
    last = "(SELECT h.source FROM edit_history h WHERE h.app_id = a.id AND h.field = '{}' ORDER BY h.id DESC LIMIT 1)"
    rows = conn.execute(
        "SELECT a.id, a.company, a.role_title, j.company AS posting, j.title AS posting_title FROM applications a "
        f"JOIN jobs j ON j.uid = a.job_uid WHERE a.profile = ? AND trim(coalesce(a.company, '')) != '' AND "
        f"{last.format('company')} = 'sheet' AND {last.format('role_title')} = 'sheet'", (pid,)).fetchall()
    detached = [r for r in rows if not similar_company(r["company"], r["posting"])       # a spelling fix keeps it
                and not same_role(r["role_title"], r["posting_title"])]
    for row in detached:
        with db.tx(conn):
            conn.execute(f"UPDATE applications SET {', '.join(f'{f} = NULL' for f in POSTING_FIELDS)} WHERE id = ?",
                         (row["id"],))
            conn.execute("DELETE FROM jd_snapshots WHERE app_id = ?", (row["id"],))
            gsync.log_event(conn, pid, "detached", None, row["id"], None, row["company"],
                            f"the row now holds another job: the {row['posting']} posting link was removed")
        jdsnap.snapshot(conn, row["id"])
        log.info("%s: #%d reused for %s, %s posting detached", pid, row["id"], row["company"], row["posting"])
    return [r["id"] for r in detached]


def merge_twins(conn, pid: str, appendable: list[dict], apps: dict[int, dict]) -> tuple[list[dict], dict[int, dict]]:
    """Merge every unambiguous twin; returns (applications still to append, the profile's applications now)."""
    pairs = find_twins(conn, pid, appendable, apps)
    for made, typed in pairs:
        merge(conn, pid, made, typed)
    if not pairs:
        return appendable, apps
    merged = {m["id"] for m, _ in pairs}
    return [a for a in appendable if a["id"] not in merged], gsync.apps_of(conn, pid)
