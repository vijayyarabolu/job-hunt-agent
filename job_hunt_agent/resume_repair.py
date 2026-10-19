"""find (and fix) tracker rows whose Sheet Resume cell is a bare file name instead of the Drive file chip
of a file the app uploaded or linked for that row (row 392: a stale page wrote the replaced file's name back and the
sync pushed it as plain text).

scan() only reads the Sheet. repair() rewrites just the "bare" rows: the cell becomes the row's current file(s) as
Drive chips (resume_upload.rewrite, the same write a Replace makes). A cell the person typed something else into is
reported, never changed.

    python -m job_hunt_agent.resume_repair [--profile alex] [--apply]
"""
import argparse
import re

from . import db, gsync, resume_upload
from .google import GoogleError, accounts, api
from .gsync_values import FILE_GAP

DRIVE_ID = re.compile(r"/d/([A-Za-z0-9_-]+)|[?&]id=([A-Za-z0-9_-]+)")
CELL_FIELDS = "sheets(data(rowData(values(formattedValue,hyperlink,textFormatRuns,chipRuns))))"


def _file_id(uri: str | None) -> str | None:
    found = DRIVE_ID.search(uri or "")
    return (found.group(1) or found.group(2)) if found else None


def cell_files(cell: dict) -> set[str]:
    """The Drive file ids a cell points at: its chips, its link runs and its whole-cell link."""
    uris = [((r.get("chip") or {}).get("richLinkProperties") or {}).get("uri") for r in cell.get("chipRuns") or []]
    uris += [((r.get("format") or {}).get("link") or {}).get("uri") for r in cell.get("textFormatRuns") or []]
    uris.append(cell.get("hyperlink"))
    return {f for f in map(_file_id, uris) if f}


def _column(target: gsync.Target, col: int) -> list[dict]:
    a1 = f"{gsync.quoted(target.title)}!{gsync.letter(col)}1:{gsync.letter(col)}"
    got = api.get(gsync.sheets_url(target.sid), {"ranges": a1, "fields": CELL_FIELDS})
    rows = got["sheets"][0]["data"][0].get("rowData", []) if got.get("sheets") else []
    return [(r.get("values") or [{}])[0] for r in rows]


def _uploads(conn, pid: str) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for r in conn.execute("SELECT app_id, name, file_id, removed_at FROM resume_uploads WHERE profile = ? "
                          "ORDER BY version", (pid,)):
        entry = out.setdefault(r["app_id"], {"current": [], "old": []})
        entry["old" if r["removed_at"] else "current"].append((r["name"], r["file_id"]))
    return out


def verdict(text: str, files: set[str], current: list, old: list) -> str:
    """'chip' (every current file it names points at that file), 'bare' (no file link at all and the whole cell is
    one of the row's file names: safe to rewrite), else 'other' (typed by the person, or no current file)."""
    if not current:
        return "other"
    named = [(n, f) for n, f in current if n and n in text]
    if named and all(f in files for _, f in named):
        return "chip"
    if not files and text.strip() in {n for n, _ in current + old}:
        return "bare"
    return "other"


def _scan_tab(conn, target: gsync.Target, uploads: dict) -> list[dict]:
    col = next((c for c, k in target.synced if k.field == resume_upload.RESUME_FIELD), None)
    if col is None:
        return []
    keys = gsync.get_values(target.sid, [f"{gsync.quoted(target.title)}!{gsync.letter(target.key_col)}1:"
                                         f"{gsync.letter(target.key_col)}"])[0]
    cells = _column(target, col)
    out = []
    for index, key in enumerate(keys):
        app_id = gsync.app_id_of(str(key[0])) if key else None
        if app_id not in uploads:
            continue
        cell = cells[index] if index < len(cells) else {}
        text, files = cell.get("formattedValue") or "", cell_files(cell)
        up = uploads[app_id]
        out.append({"app": app_id, "tab": target.title, "row": index + 1, "text": text,
                    "current": [n for n, _ in up["current"]],
                    "verdict": verdict(text, files, up["current"], up["old"])})
    return out


def scan(conn, pid: str) -> list[dict]:
    """Every Sheet row of the person that has an uploaded / linked resume file, with its verdict (read only)."""
    uploads = _uploads(conn, pid)
    if not gsync.linked_sid(conn, pid):
        return []
    return accounts.with_fallback(pid, lambda: [r for t in gsync.targets(conn, pid)
                                                for r in _scan_tab(conn, t, uploads)])


def repair(conn, pid: str, apply: bool = False) -> list[dict]:
    """The 'bare' rows, each rewritten to its current file chip(s) when apply is set."""
    fixed = []
    for found in (r for r in scan(conn, pid) if r["verdict"] == "bare"):
        text = FILE_GAP.join(found["current"])
        where = resume_upload.rewrite(conn, pid, found["app"], text) if apply else "dry run"
        fixed.append({**found, "to": text, "result": where})
    return fixed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profile", action="append", help="alex / jordan (default: both)")
    parser.add_argument("--apply", action="store_true", help="rewrite the bare cells (default: report only)")
    parser.add_argument("--db", help="a database copy to read (default: the app's)")
    parser.add_argument("--all", action="store_true", help="list every row with a file, not only the bare ones")
    args = parser.parse_args(argv)
    conn = db.connect(args.db) if args.db else db.connect()
    for pid in args.profile or ["alex", "jordan"]:
        try:
            rows = scan(conn, pid) if args.all else repair(conn, pid, args.apply)
        except GoogleError as err:
            print(f"{pid}: {err}")
            continue
        print(f"{pid}: {len(rows)} row(s)")
        for r in rows:
            print(f"  {r['tab']} row {r['row']} (#{r['app']}) {r['verdict']}: {r['text']!r} -> "
                  f"{r.get('to', r['current'])!r} {r.get('result', '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
