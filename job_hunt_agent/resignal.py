"""`job-hunt-agent resignal`: re-derive the stored per-job signals after the parsers change.

Title families, required years, employment type, staffing signals, sponsorship, work terms (GOAL35) and the US verdict (from the stored
location and ATS country) are computed once at ingest
(ats.common.make_job). When those parsers improve, this recomputes them for every stored job from its title and
stored JD, without a fetch. Only these columns change; history, dates and descriptions are untouched. A job whose
ATS gave an explicit employment type keeps it unless the JD itself says contract / contract-to-hire.
also marks as backlog the undated postings a board's first fetch stored as new before fresh.observe did.
"""
from . import signals, store, workterms
from .db import tx, unpack

BATCH = 2000
COLUMNS = ("families", "min_years", "employment_type", "staffing", "sponsorship", "work_terms", "is_us")
BACKLOG = ("FROM jobs WHERE precision = 'unknown' AND seed = 0 AND first_seen <= "
           "(SELECT b.baselined_at FROM boards b WHERE b.board = jobs.board)")      # seen in the board's first run


def _families(title: str, text: str | None = None) -> str:
    found, _denied = store.job_families(title, text)        # GOAL36 §1: the deny list and the resume mark too
    return "," + ",".join(found) + "," if found else ","


def _is_us(row) -> int | None:
    verdict = signals.is_us(row["location"], row["country"])
    return None if verdict is None else int(verdict)


def derive(row) -> dict:
    """New signal values for one jobs row (title + stored JD); the stored employment type is the ATS field's
    reading and is only overridden by contract wording in the JD."""
    text = unpack(row["description"]) if row["description"] else ""
    from_text = signals.employment_type(None, row["title"], text)
    employment = from_text if from_text in (signals.CONTRACT, signals.CONTRACT_TO_HIRE) else \
        row["employment_type"] or from_text
    if not text:          # no stored JD (title outside every family): keep the JD-derived values as they are
        return {"families": _families(row["title"]), "min_years": row["min_years"], "employment_type": employment,
                "staffing": row["staffing"], "sponsorship": row["sponsorship"], "work_terms": row["work_terms"],
                "is_us": _is_us(row)}
    return {"families": _families(row["title"], text), "min_years": signals.min_years(text), "employment_type": employment,
            "staffing": signals.staffing_signals(text), "sponsorship": signals.sponsorship(text),
            "work_terms": workterms.label(text), "is_us": _is_us(row)}


def run(conn, dry: bool = False) -> dict:
    """Recompute COLUMNS for all jobs; returns how many rows changed per column."""
    changed = {c: 0 for c in COLUMNS}
    rows = conn.execute(f"SELECT uid, title, description, location, country, {', '.join(COLUMNS)} FROM jobs").fetchall()
    updates = []
    for row in rows:
        new = derive(row)
        diff = {c: v for c, v in new.items() if v != row[c]}
        for c in diff:
            changed[c] += 1
        if diff:
            updates.append((row["uid"], diff))
    if not dry:
        for start in range(0, len(updates), BATCH):
            with tx(conn):
                for uid, diff in updates[start:start + BATCH]:
                    conn.execute(f"UPDATE jobs SET {', '.join(f'{c} = ?' for c in diff)} WHERE uid = ?",
                                 [*diff.values(), uid])
    return {"jobs": len(rows), "changed_rows": len(updates), **changed, "backlog": _backlog(conn, dry)}


def _backlog(conn, dry: bool) -> int:
    """Undated postings stored as new by their board's first fetch (before 22eedde): their age is unknown, so they
    are backlog ("Backlog (first run)", not "First seen 1h ago" at the top of Newest). Returns how many."""
    n = conn.execute("SELECT count(*) " + BACKLOG).fetchone()[0]
    if n and not dry:
        with tx(conn):
            conn.execute("UPDATE jobs SET seed = 1 WHERE uid IN (SELECT uid " + BACKLOG + ")")
    return n


def work_terms_labels(conn, dry: bool = False) -> dict:
    """GOAL35 §4.3: jobs.work_terms for every open job with a description (the column is new in schema 38).
    {checked, changed, by_kind: {kind: n}} (by_kind counts every open job's terms after the run)."""
    rows = conn.execute("SELECT uid, description, work_terms, employment_type, company FROM jobs WHERE closed_at IS NULL "
                        "AND description IS NOT NULL").fetchall()
    labels = [(workterms.label(unpack(r["description"])), r["uid"], r["work_terms"]) for r in rows]
    updates = [u[:2] for u in labels if u[0] != u[2]]
    if not dry:
        for start in range(0, len(updates), BATCH):
            with tx(conn):
                conn.executemany("UPDATE jobs SET work_terms = ? WHERE uid = ?", updates[start:start + BATCH])
    by_kind: dict[str, int] = {}
    for new, _uid, _old in labels:
        for kind in workterms.kinds(new):
            by_kind[kind] = by_kind.get(kind, 0) + 1
    return {"checked": len(rows), "changed": len(updates), "by_kind": dict(sorted(by_kind.items())),
            "w2": w2_counts(rows, [new for new, _uid, _old in labels])}


def w2_counts(rows, labels: list) -> dict:
    """GOAL36 §3: W2 full time, W2 contract, not said, and dropped by why (a W2 person's blocks)."""
    out = {"w2_full_time": 0, "w2_contract": 0, "not_said": 0, "dropped": {}}
    for row, label in zip(rows, labels):
        why = workterms.verdict(label, workterms.RULE_W2)
        if why:
            kind = why.removeprefix(workterms.VERDICT)
            out["dropped"][kind] = out["dropped"].get(kind, 0) + 1
        elif workterms.W2 in workterms.kinds(label):
            out["w2_contract" if workterms.contract(row["employment_type"], row["company"]) else "w2_full_time"] += 1
        else:
            out["not_said"] += 1
    return out


def sponsorship_labels(conn, dry: bool = False) -> dict:
    """re-derive the JD sponsorship label of every open job with a description (after the classifier changed:
    "unable to sponsor for roles outside of engineering" is no label, "no exceptions" is No sponsorship).
    {checked, changed: {old->new: n}}."""
    rows = conn.execute("SELECT uid, description, sponsorship FROM jobs WHERE closed_at IS NULL "
                        "AND description IS NOT NULL").fetchall()
    updates = [(signals.sponsorship(unpack(r["description"])), r["uid"], r["sponsorship"]) for r in rows]
    updates = [u for u in updates if u[0] != u[2]]
    if not dry:
        for start in range(0, len(updates), BATCH):
            with tx(conn):
                conn.executemany("UPDATE jobs SET sponsorship = ? WHERE uid = ?", [u[:2] for u in updates[start:start + BATCH]])
    changed: dict[str, int] = {}
    for new, _uid, old in updates:
        changed[f"{old}->{new}"] = changed.get(f"{old}->{new}", 0) + 1
    return {"checked": len(rows), "changed": changed}

