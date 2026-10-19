"""Every row stays at normal height (user, 2026-10-06 evening, both sheets): the check `doctor` runs, and the fix for
rows the app wrote before every write was one clipped line.

Sheets reports a row's set height (rowMetadata.pixelSize), not the height it grows to on its own when a cell wraps,
so a row is flagged when its set height is above normal, or when it is a row the app wrote and one of its filled
visible cells is not set to clip (the fix: clip them and set the row's height; text the person typed into those
rows stays as typed). The person's own rows keep the heights and wrapping they chose; only a set height above
normal flags one of them. What the app itself writes is one clipped line (gsync_values.to_cell; guarded in tests).
"""
from . import gsync
from .google import api
from .gsync_jd import ROW_PX, height_requests
from .gsync_values import CLIP

APP_MADE = ("feed", "manual")
RANGES_PER_READ = 100


def _sheet(target: gsync.Target, ranges: list[str], fields: str) -> list[dict]:
    got = api.get(gsync.sheets_url(target.sid), {"ranges": ranges, "fields": f"sheets(data({fields}))"})
    return [block for sheet in got.get("sheets", []) for block in sheet.get("data", [])]


def _app_rows(conn, target: gsync.Target) -> tuple[list, dict[int, int]]:
    """(row heights, grid row -> app id of each row the app made), from one read of the key column."""
    col = gsync.letter(target.key_col)
    blocks = _sheet(target, [f"{gsync.quoted(target.title)}!{col}1:{col}"],
                    "rowMetadata(pixelSize),rowData(values(formattedValue))")
    block = blocks[0] if blocks else {}
    made = {r["id"] for r in conn.execute(f"SELECT id FROM applications WHERE profile = ? AND source IN "
                                          f"({','.join('?' * len(APP_MADE))})", (target.pid, *APP_MADE))}
    keys = [((r.get("values") or [{}])[0].get("formattedValue") or "") for r in block.get("rowData", [])]
    rows = {i: gsync.app_id_of(k.strip()) for i, k in enumerate(keys) if i}
    return [m.get("pixelSize") for m in block.get("rowMetadata", [])], {i: a for i, a in rows.items() if a in made}


def _growing(target: gsync.Target, rows: list[int]) -> list[int]:
    """Rows (of `rows`) with a filled visible cell not set to clip."""
    last = gsync.letter(target.key_col - 1)
    out = []
    for start in range(0, len(rows), RANGES_PER_READ):
        part = rows[start:start + RANGES_PER_READ]
        ranges = [f"{gsync.quoted(target.title)}!A{i + 1}:{last}{i + 1}" for i in part]
        blocks = _sheet(target, ranges, "rowData(values(formattedValue,userEnteredFormat(wrapStrategy)))")
        for index, block in zip(part, blocks):
            cells = [c for r in block.get("rowData", []) for c in r.get("values", []) if c.get("formattedValue")]
            if any((c.get("userEnteredFormat") or {}).get("wrapStrategy") != "CLIP" for c in cells):
                out.append(index)
    return out


def check(conn, pid: str) -> dict[str, dict]:
    """Per tab: 'tall' sheet rows (1-based) with a set height above normal, 'may_grow' rows the app wrote that
    could grow. Read-only (two reads a tab, plus one per 100 rows the app made)."""
    out = {}
    for target in gsync.targets(conn, pid):
        if target.key_col is None:
            continue
        heights, made = _app_rows(conn, target)
        tall = [i + 1 for i, h in enumerate(heights) if i and h and h > ROW_PX]
        out[target.title] = {"tall": tall, "may_grow": [i + 1 for i in _growing(target, sorted(made))],
                             "app_rows": len(made)}
    return out


def fix_app_rows(conn, pid: str) -> dict[str, int]:
    """Rows the app made: every visible cell set to clip (the format only: no value changes) and the row height
    normal. Returns rows fixed per tab."""
    out = {}
    for target in gsync.targets(conn, pid):
        if target.key_col is None:
            continue
        _, made = _app_rows(conn, target)
        requests = [{"repeatCell": {"range": {"sheetId": target.sheet_id, "startRowIndex": i, "endRowIndex": i + 1,
                                              "startColumnIndex": 0, "endColumnIndex": target.key_col},
                                    "cell": {"userEnteredFormat": CLIP}, "fields": "userEnteredFormat.wrapStrategy"}}
                    for i in sorted(made)]
        gsync.batch(target.sid, requests + height_requests(target, sorted(made)))
        out[target.title] = len(made)
    return out


def doctor_line(conn) -> str:
    """One line for `doctor`: rows taller than normal, per person and tab."""
    problems, rows = [], 0
    for pid in gsync.profiles_linked(conn):
        for title, found in check(conn, pid).items():
            rows += found["app_rows"]
            if found["tall"]:
                problems.append(f"{pid}/{title} rows taller than normal: {_short(found['tall'])}")
            if found["may_grow"]:
                problems.append(f"{pid}/{title} rows the app wrote that can grow: {_short(found['may_grow'])}")
    if problems:
        return "sheet rows: PROBLEM - " + "; ".join(problems) + " (fix: job-hunt-agent google jd-format --yes)"
    return f"sheet rows: ok - every row at normal height ({rows} rows the app wrote, all one clipped line)"


def _short(rows: list[int], shown: int = 10) -> str:
    return ", ".join(map(str, rows[:shown])) + (f" (+{len(rows) - shown} more)" if len(rows) > shown else "")
