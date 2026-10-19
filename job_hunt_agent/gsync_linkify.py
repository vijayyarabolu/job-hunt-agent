"""`job-hunt-agent google linkify-jd` (user decision 2026-10-06): every posting link already sitting in a JD cell
of either person's Sheet becomes a real clickable link. Links written through the API never auto-link, and earlier
rows were pasted or written as plain text.

Only the cell's text-format runs are written (fields=textFormatRuns): the text itself is never sent, so no cell text
can change. Cells are left alone when they are formulas, have no URL ("don't invent one": listed), are already
linked, or carry other rich formatting (bold, colours: listed, not touched). Under the sync lock; every written cell
is read back and checked (a sort racing the write is repaired from the cell's own text).
"""
import logging
from urllib.parse import unquote

from . import gsync
from .google import api
from .google import state as gstate
from .gsync_values import link_runs, urls

log = logging.getLogger(__name__)
JD_HEADER = "JD"
GRID_FIELDS = "sheets(data(rowData(values(userEnteredValue,formattedValue,textFormatRuns,hyperlink))))"
WRITE_CHUNK = 200
LOCK_WAIT_S = 120


def _cells(target: gsync.Target, col: int) -> list[tuple[int, dict]]:
    """(grid row index, cell data) for every row of the column under the header."""
    a1 = f"{gsync.quoted(target.title)}!{gsync.letter(col)}2:{gsync.letter(col)}"
    got = api.get(gsync.sheets_url(target.sid), {"ranges": a1, "fields": GRID_FIELDS})
    rows = []
    for sheet in got.get("sheets", []):
        for block in sheet.get("data", []):
            rows += [(i + 1, (r.get("values") or [{}])[0]) for i, r in enumerate(block.get("rowData", []))]
    return rows


def _norm(uri: str) -> str:
    """A link as compared: Sheets stores it percent-encoded and with a lowercased host."""
    return unquote(uri).strip().lower()


def _linked(cell: dict) -> set[str]:
    found = {r["format"]["link"]["uri"] for r in cell.get("textFormatRuns") or []
             if (r.get("format") or {}).get("link", {}).get("uri")}
    return {_norm(u) for u in found | ({cell["hyperlink"]} if cell.get("hyperlink") else set())}


LINK_STYLE = {"link", "foregroundColor", "foregroundColorStyle", "underline"}   # what Sheets adds to a link run


def _rich(run: dict) -> bool:
    """A run with formatting of its own (bold, colours), not just a link and the link look Sheets gives it."""
    keys = set(run.get("format") or {})
    return bool(keys - LINK_STYLE) if "link" in keys else bool(keys)


def classify(cell: dict) -> str:
    """'empty' | 'formula' | 'no_url' | 'rich' | 'linked' | 'fix'."""
    if "formulaValue" in (cell.get("userEnteredValue") or {}):
        return "formula"
    text = cell.get("formattedValue") or ""
    if not text.strip():
        return "empty"
    found = {_norm(u) for _, _, u in urls(text)}
    if not found:
        return "no_url"
    if any(_rich(r) for r in cell.get("textFormatRuns") or []):
        return "rich"
    return "linked" if found <= _linked(cell) else "fix"


def _keys(target: gsync.Target) -> dict[int, int | None]:
    col = gsync.letter(target.key_col)
    keys = gsync.get_values(target.sid, [f"{gsync.quoted(target.title)}!{col}2:{col}"])[0]
    return {i + 1: gsync.app_id_of(str(k[0]).strip()) if k else None for i, k in enumerate(keys)}


def plan(conn, pid: str) -> list[dict]:
    """One entry per JD column of the person's tabs: the cells to fix and the counts of the rest."""
    out = []
    for target in gsync.targets(conn, pid):
        col = next((c for c, k in target.synced if k.header == JD_HEADER), None)
        if col is None or target.key_col is None:
            continue
        keys, kinds, fix, left = _keys(target), {}, [], []
        for index, cell in _cells(target, col):
            kind = classify(cell)
            kinds[kind] = kinds.get(kind, 0) + 1
            if kind == "fix":
                fix.append({"index": index, "text": cell["formattedValue"], "app": keys.get(index)})
            elif kind in ("no_url", "rich"):
                left.append({"index": index, "app": keys.get(index), "kind": kind})
        out.append({"target": target, "col": col, "counts": kinds, "fix": fix, "left": left})
    return out


def _write(target: gsync.Target, col: int, cells: list[dict]) -> None:
    requests = [{"updateCells": {"start": {"sheetId": target.sheet_id, "rowIndex": c["index"], "columnIndex": col},
                                 "rows": [{"values": [{"textFormatRuns": link_runs(c["text"])}]}],
                                 "fields": "textFormatRuns"}} for c in cells]
    for i in range(0, len(requests), WRITE_CHUNK):
        gsync.batch(target.sid, requests[i:i + WRITE_CHUNK])


def apply(conn, pid: str) -> dict:
    """Link every 'fix' cell of the person's JD columns; returns counts and anything repaired or skipped."""
    with gstate.lock(wait_s=LOCK_WAIT_S) as held:
        if not held:
            raise ValueError("the Sheet sync is busy: try again in a minute")
        report = {}
        for entry in plan(conn, pid):
            target, col = entry["target"], entry["col"]
            _write(target, col, entry["fix"])
            after = dict(_cells(target, col))
            wanted = {c["index"]: c["text"] for c in entry["fix"]}
            moved = [i for i, text in wanted.items() if (after.get(i) or {}).get("formattedValue") != text]
            repair = [{"index": i, "text": (after.get(i) or {}).get("formattedValue") or ""} for i in moved]
            if repair:                                     # a sort moved rows meanwhile: link each cell's own text
                log.warning("%s/%s: %d JD cells moved during linkify; relinked from their own text", pid,
                            target.title, len(repair))
                _write(target, col, [c for c in repair if urls(c["text"])])
                after = dict(_cells(target, col))
            bad = [i for i in wanted if classify(after.get(i) or {}) not in ("linked", "no_url", "empty")]
            report[target.title] = {"linked": len(wanted) - len(bad), "repaired": len(repair), "not_linked": bad,
                                    "counts_before": entry["counts"], "left": entry["left"]}
        return report
