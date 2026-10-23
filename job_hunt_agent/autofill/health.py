"""Autofill must not keep breaking, and never fail silently.

- check(): the extension asks before every fill (is job-hunt-agent up, its database readable, which extension version is
  current), so a sleeping Mac says so instead of a half-filled form.
- record_error(): a fill error, a timeout or a layout no adapter knows, with the form's HTML (the extension already
  removed password / ID values), grouped by board + step.
- report(): Settings › Autofill › Health: the last 7 days' fill rate per board, the errors, the top unknown questions,
  and the last canary run.
- record_canary() / notices(): the nightly canary's results; a drop against the last good run of the same page is a
  notice job-hunt-agent shows the next morning (board and step).
"""
import json
import time
import zlib
from datetime import datetime, timedelta, timezone

from .. import db
from . import package

KINDS = ("error", "unknown_layout", "timeout")
MAX_HTML = 3 * 1024 * 1024
DAYS = 7
TOP_QUESTIONS = 10
DROP = 0.15                                          # a canary page filling 15 points less than its last good run
OLD_VERSION = "1.0.0"                                # GOAL23's extension sends no version
SEEN_EVERY_S = 3600                                  # the version an extension uses is written at most hourly
_seen: dict[str, tuple[str, float]] = {}


def _iso(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def check(conn, pid: str) -> dict:
    conn.execute("SELECT 1 FROM autofill_answers LIMIT 1").fetchone()       # the database answers
    return {"ok": True, "profile": pid, "extension": package.version(), "at": db.iso(db.utcnow())}


def _parts(version: str) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in str(version).split(".")[:3])
    except ValueError:
        return (0,)


def seen(conn, pid: str, version: str | None) -> None:
    """The extension version this person's Chrome is using (every extension call says it; GOAL23's says nothing)."""
    version = str(version or OLD_VERSION)[:20]
    last = _seen.get(pid)
    if last and last[0] == version and time.monotonic() - last[1] < SEEN_EVERY_S:
        return
    _seen[pid] = (version, time.monotonic())
    with db.tx(conn):
        conn.execute("INSERT INTO autofill_seen(profile, version, at) VALUES (?,?,?) ON CONFLICT(profile) DO UPDATE SET "
                     "version = excluded.version, at = excluded.at", (pid, version, db.iso(db.utcnow())))


def extension(conn, pid: str) -> dict:
    """{current: this job-hunt-agent's extension, installed: the one last seen in this person's Chrome, newer: update it}"""
    row = conn.execute("SELECT version, at FROM autofill_seen WHERE profile = ?", (pid,)).fetchone()
    current = package.version()
    installed = row["version"] if row else None
    return {"current": current, "installed": installed, "seen_at": row["at"] if row else None,
            "newer": bool(installed) and _parts(current) > _parts(installed)}


def record_error(conn, pid: str, args: dict, now) -> dict:
    kind = str(args.get("kind") or "error")
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    html = str(args.get("html") or "")[:MAX_HTML]
    with db.tx(conn):
        cur = conn.execute("INSERT INTO autofill_errors(profile, board, step, kind, message, url, html, version, at) "
                           "VALUES (?,?,?,?,?,?,?,?,?)",
                           (pid, str(args.get("board") or "generic")[:40], str(args.get("step") or "")[:60], kind,
                            str(args.get("message") or "")[:500], str(args.get("url") or "")[:2000],
                            zlib.compress(html.encode()) if html else None, str(args.get("version") or "")[:20],
                            db.iso(now or db.utcnow())))
    return {"id": cur.lastrowid}


def _fill_rates(conn, pid: str, since: str) -> list[dict]:
    rows = {}
    for r in conn.execute("SELECT coalesce(board, '?') AS b, sum(filled) AS f, sum(needs) AS n, count(*) AS steps "
                          "FROM autofill_log WHERE profile = ? AND at >= ? GROUP BY 1", (pid, since)):
        rows[r["b"]] = {"board": r["b"], "filled": r["f"] or 0, "needs": r["n"] or 0, "hand": 0, "steps": r["steps"]}
    for r in conn.execute("SELECT coalesce(board, '?') AS b, sum(hand) AS h FROM autofill_hand WHERE profile = ? AND "
                          "at >= ? GROUP BY 1", (pid, since)):
        rows.setdefault(r["b"], {"board": r["b"], "filled": 0, "needs": 0, "hand": 0, "steps": 0})["hand"] = r["h"] or 0
    out = []
    for row in rows.values():
        total = row["filled"] + row["needs"] + row["hand"]
        out.append({**row, "rate": round(100 * row["filled"] / total) if total else None})
    return sorted(out, key=lambda r: (r["rate"] if r["rate"] is not None else 101, r["board"]))


def _errors(conn, pid: str, since: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT g.board, g.step, g.kind, g.n, g.last_at, g.last_id, e.message, e.version FROM (SELECT board, step, kind, "
        "count(*) AS n, max(at) AS last_at, max(id) AS last_id FROM autofill_errors WHERE profile = ? AND at >= ? "
        "GROUP BY board, step, kind) g JOIN autofill_errors e ON e.id = g.last_id ORDER BY g.n DESC, g.last_at DESC",
        (pid, since))]


def _questions(conn, pid: str, since: str) -> list[dict]:
    return [{"text": r["text"], "seen": r["seen"], "boards": json.loads(r["boards"])} for r in conn.execute(
        "SELECT text, seen, boards FROM autofill_questions WHERE profile = ? AND state = 'open' AND last_at >= ? "
        "ORDER BY seen DESC, last_at DESC LIMIT ?", (pid, since, TOP_QUESTIONS))]


FIELD_STATUSES = ("filled", "review", "needs", "failed", "kept", "skipped")
MAX_FIELDS = 150
KEEP_FIELD_DAYS = 30


def record_fields(conn, pid: str, board: str | None, step: str, items, version: str, now) -> int:
    """GOAL27: one row per field of a fill (label, kind, canonical key, result and why; never a value). Rows older
    than 30 days go."""
    rows = []
    for item in (items if isinstance(items, list) else [])[:MAX_FIELDS]:
        if not isinstance(item, dict) or item.get("status") not in FIELD_STATUSES:
            continue
        rows.append((pid, str(board or "generic")[:40], str(step or "")[:60], " ".join(str(item.get("label") or "").split())[:200],
                     str(item.get("kind") or "text")[:20], str(item.get("key") or "")[:40] or None, item["status"],
                     str(item.get("why") or "")[:200], str(version or "")[:20], db.iso(now or db.utcnow())))
    with db.tx(conn):
        conn.executemany("INSERT INTO autofill_fields(profile, board, step, label, kind, key, status, why, version, at) "
                         "VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
        conn.execute("DELETE FROM autofill_fields WHERE at < ?",
                     (_iso((now or datetime.now(timezone.utc)) - timedelta(days=KEEP_FIELD_DAYS)),))
    return len(rows)


def _field_results(conn, pid: str, since: str) -> dict:
    """Per board: how many fields ended filled / review / needs / failed, and the fields that failed or needed the user
    most (with why), so a break shows in one place."""
    boards: dict[str, dict] = {}
    for r in conn.execute("SELECT board, status, count(*) AS n FROM autofill_fields WHERE profile = ? AND at >= ? "
                          "GROUP BY board, status", (pid, since)):
        boards.setdefault(r["board"], {"board": r["board"], **{k: 0 for k in FIELD_STATUSES}})[r["status"]] = r["n"]
    worst = [dict(r) for r in conn.execute(
        "SELECT board, label, kind, key, status, why, count(*) AS n, max(at) AS last_at FROM autofill_fields "
        "WHERE profile = ? AND at >= ? AND status IN ('failed', 'needs') GROUP BY board, label, status, why "
        "ORDER BY status = 'failed' DESC, n DESC, last_at DESC LIMIT 20", (pid, since))]
    return {"boards": sorted(boards.values(), key=lambda b: (-b["failed"], b["board"])), "worst": worst}


def last_canary(conn) -> dict | None:
    row = conn.execute("SELECT max(run_at) AS at FROM autofill_canary").fetchone()
    if not row or not row["at"]:
        return None
    pages = [dict(r) for r in conn.execute("SELECT name, board, source, step, fields, filled, expected, ok, detail "
                                           "FROM autofill_canary WHERE run_at = ? ORDER BY ok, name", (row["at"],))]
    return {"at": row["at"], "pages": pages, "failed": sum(1 for p in pages if not p["ok"])}


def report(conn, pid: str, now: datetime | None = None) -> dict:
    since = _iso((now or datetime.now(timezone.utc)) - timedelta(days=DAYS))
    return {"days": DAYS, "boards": _fill_rates(conn, pid, since), "errors": _errors(conn, pid, since),
            "questions": _questions(conn, pid, since), "canary": last_canary(conn), "notices": notices(conn),
            "fields": _field_results(conn, pid, since),
            "extension": extension(conn, pid)}


def _rate(filled: int, fields: int) -> float:
    return filled / fields if fields else 0.0


def _day(at) -> str:
    """'Oct 15' from '2026-10-15T08:10:05Z'."""
    try:
        return datetime.fromisoformat(str(at).replace("Z", "+00:00")).strftime("%b %-d")
    except ValueError:
        return str(at or "")[:10]


def _judge(conn, page: dict) -> tuple[bool, str, str]:
    """(ok, the notice line when not ok, what to keep in detail) for one canary page. GOAL30 §0c: a fixture fails on
    a real drop (fewer fields filled than its last good run, and clearly less of the page) or when it fills fewer than
    its fixture expects; more fields found while the fills held is no drop (the detail says so). A live page is
    another posting each night: it fails only on an error or when it filled nothing; a closed posting (no form) is
    skipped."""
    name, fields, filled = str(page["name"]), int(page.get("fields") or 0), int(page.get("filled") or 0)
    why = f": {page['error']}" if page.get("error") else ""
    if page.get("skip"):
        return True, "", str(page["skip"])
    ok = not page.get("error")
    if page.get("source") == "live":
        ok = ok and (fields == 0 or filled > 0)
        return ok, "" if ok else f"{page.get('board') or '?'}: live page filled {filled} of {fields}{why}", page.get("error") or ""
    prev = conn.execute("SELECT fields, filled, run_at FROM autofill_canary WHERE name = ? AND ok = 1 AND fields > 0 "
                        "ORDER BY run_at DESC LIMIT 1", (name,)).fetchone()
    expected = page.get("expected")
    short = expected is not None and filled < int(expected)
    dropped = bool(prev) and filled < prev["filled"] and _rate(filled, fields) + DROP < _rate(prev["filled"], prev["fields"])
    grew = (f"{fields} fields found (was {prev['fields']} on {_day(prev['run_at'])}), fills held at {filled}"
            if prev and fields > prev["fields"] and filled >= prev["filled"] else "")
    if ok and not short and not dropped:
        return True, "", grew
    reason = (f"down from {prev['filled']} of {prev['fields']} on {_day(prev['run_at'])}" if dropped
              else f"it should fill at least {expected}" if short else "")
    what = f"{page.get('board') or '?'}: {page.get('step') or name} page filled {filled} of {fields}"
    return False, what + (f", {reason}" if reason else "") + why, "; ".join(x for x in (reason, page.get("error"), grew) if x)


def _again(conn, page: dict) -> bool:
    """The same failure as the run before (same page, same counts): already in a notice, not a new one."""
    last = conn.execute("SELECT fields, filled, ok FROM autofill_canary WHERE name = ? ORDER BY run_at DESC, id DESC "
                        "LIMIT 1", (str(page["name"]),)).fetchone()
    return bool(last) and not last["ok"] and (last["fields"], last["filled"]) == (int(page.get("fields") or 0),
                                                                                int(page.get("filled") or 0))


def record_canary(conn, run_at: str, pages: list[dict]) -> list[str]:
    """One nightly run: each page's fill. A page that dropped (or fills below what its fixture expects) is a notice
    for the morning, once: '<board>: <step> page filled 4 of 12, down from 11 of 12 on Oct 13'. The same failure
    night after night is not a new notice."""
    lines = []
    with db.tx(conn):
        for page in pages:
            ok, line, detail = _judge(conn, page)
            repeat = not ok and _again(conn, page)
            conn.execute("INSERT INTO autofill_canary(run_at, name, board, source, step, fields, filled, expected, ok, detail) "
                         "VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (run_at, str(page["name"]), str(page.get("board") or "?"), str(page.get("source") or "fixture"),
                          str(page.get("step") or ""), int(page.get("fields") or 0), int(page.get("filled") or 0),
                          page.get("expected"), int(ok), str(detail or "")[:500]))
            if not ok and not repeat:
                lines.append(line)
        if lines:
            conn.execute("INSERT INTO app_notices(kind, text, at) VALUES (?,?,?)",
                         ("canary", "Autofill's nightly check: " + "; ".join(lines[:6]), run_at))
    return lines


def notices(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT id, kind, text, at FROM app_notices WHERE closed_at IS NULL "
                                          "ORDER BY at DESC LIMIT 5")]


def close_notice(conn, nid: int) -> dict:
    with db.tx(conn):
        conn.execute("UPDATE app_notices SET closed_at = ? WHERE id = ?", (db.iso(db.utcnow()), int(nid)))
    return {"closed": int(nid)}
