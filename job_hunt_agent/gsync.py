"""Two-way sync with each person's EXISTING Google Sheet (decision 008): the Sheet side. The app never
creates a spreadsheet, a tab or a visible column: it reads and writes the person's own columns (matched by header
text, so moved columns are fine) and keeps one hidden last column, "job-hunt-agent id", whose cells say which application
a row is ("jh:<id>"). gsync_cycle.py runs a cycle on top of this; gsync_link.py links a sheet the first time.
"""
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import config, db, tracker_layout
from .google import GoogleError, api, base, state
from .gsync_values import CLIP, DATE_HEADERS, from_serial, serial
from .gsync_values import fields as cell_fields
from .tracker_map import MAX_MONTH, SHEET_ROW_STRIDE, load_mapping

KEY_HEADER = "job-hunt-agent id"
KEY_PREFIX = "jh:"
JD_TEXT_HEADER = tracker_layout.JD_TEXT.header    # hidden, right of the key column (gsync_jd.add_columns)
LONG_COLUMNS = ("JD", JD_TEXT_HEADER)    # ~5 KB a cell: read on their own schedule, not every cycle
FULL_READ_S = 180                        # a JD typed in the Sheet reaches the app within 5 min (gzip)
WRITE_CHUNK = 500
REACHED = ("applied", "screen", "interview", "offer", "rejected", "ghosted")
SHEET_FIELDS = "sheets(properties(sheetId,title,gridProperties))"
_SID = re.compile(r"/spreadsheets/d/([A-Za-z0-9_-]{20,})")


@dataclass(frozen=True)
class Target:
    """One of the person's application tabs, as the Sheet has it now."""
    pid: str
    sid: str
    tab: tracker_layout.Tab              # the layout tab (the person's columns)
    sheet_id: int
    headers: tuple[str, ...]             # the Sheet's own header row, key column included
    rows: int                            # grid size
    cols: int
    mapped: bool
    swap_dates: bool = False             # Jordan: dates typed mm/dd into a dd/mm sheet (tracker_map swap_date_cells)

    @property
    def title(self) -> str:
        return self.tab.name

    @property
    def key_col(self) -> int | None:
        return self.headers.index(KEY_HEADER) if KEY_HEADER in self.headers else None

    @property
    def synced(self) -> list[tuple[int, tracker_layout.Column]]:
        """(sheet column, layout column) for every layout column the Sheet has (+ the JD Text column once added);
        others are left alone."""
        columns = [*self.tab.columns, tracker_layout.JD_TEXT]
        return [(self.headers.index(c.header), c) for c in columns if c.header in self.headers]

    def missing_columns(self) -> list[str]:
        return [c.header for c in self.tab.columns if c.header not in self.headers]

    def unknown_columns(self) -> list[str]:
        known = {c.header for c in self.tab.columns} | {KEY_HEADER, JD_TEXT_HEADER, ""}
        return [h for h in self.headers if h not in known]


@dataclass
class SheetRow:
    index: int                           # 0-based grid row (1 = the first row under the header)
    key: str
    raw: dict = field(default_factory=dict)     # header -> typed value from the Sheet


def spreadsheet_id(url_or_id: str) -> str:
    text = (url_or_id or "").strip()
    match = _SID.search(text)
    if match:
        return match.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{20,}", text):
        return text
    raise ValueError(f"not a Google Sheets link: {url_or_id!r} (copy it from the browser's address bar)")


def letter(index: int) -> str:
    out, n = "", index + 1
    while n:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out


def quoted(title: str) -> str:
    return "'" + title.replace("'", "''") + "'"


def sheets_url(sid: str) -> str:
    return f"{base('sheets')}/spreadsheets/{sid}"


def batch(sid: str, requests: list[dict]) -> None:
    for start in range(0, len(requests), WRITE_CHUNK):
        api.post(f"{sheets_url(sid)}:batchUpdate", {"requests": requests[start:start + WRITE_CHUNK]})


def get_values(sid: str, ranges: list[str]) -> list[list[list]]:
    got = api.get(f"{sheets_url(sid)}/values:batchGet", {"ranges": ranges, "valueRenderOption": "FORMULA",
                                                         "dateTimeRenderOption": "SERIAL_NUMBER"})
    return [vr.get("values", []) for vr in got.get("valueRanges", [])]


def linked_sid(conn, pid: str) -> str | None:
    return state.get(conn, f"gsync.{pid}.sid")


def sheet_url(conn, pid: str) -> str | None:
    """The person's linked sheet (for "Open in Sheet"), or None before `job-hunt-agent google link`."""
    sid = linked_sid(conn, pid)
    return f"https://docs.google.com/spreadsheets/d/{sid}/edit" if sid else None


def profiles_linked(conn) -> list[str]:
    return [p for p in config.profile_ids() if linked_sid(conn, p) and state.get(conn, f"gsync.{p}.linked")]


def targets(conn, pid: str, sid: str | None = None) -> list[Target]:
    """The person's application tabs in their linked spreadsheet (or `sid`, before it is linked); a tab the Sheet
    lacks is an error."""
    sid = sid or linked_sid(conn, pid)
    if not sid:
        raise GoogleError(f"{pid}: no Google Sheet linked yet (job-hunt-agent google link --profile {pid} <url>)")
    layout = tracker_layout.of_profile(pid)
    meta = {s["properties"]["title"]: s["properties"]
            for s in api.get(sheets_url(sid), {"fields": SHEET_FIELDS}).get("sheets", [])}
    missing = [t.name for t in layout.app_tabs if t.name not in meta]
    if missing:
        raise GoogleError(f"{pid}: the linked sheet has no tab {missing} (renamed?); nothing was written")
    headers = get_values(sid, [f"{quoted(t.name)}!1:1" for t in layout.app_tabs])
    swap = bool(layout.mapping and load_mapping(layout.mapping).swap_date_cells)
    out = []
    for tab, head in zip(layout.app_tabs, headers):
        props = meta[tab.name]
        row = tuple(str(v).strip() for v in (head[0] if head else []))
        grid = props.get("gridProperties", {})
        out.append(Target(pid, sid, tab, props["sheetId"], row, grid.get("rowCount", 1000),
                          grid.get("columnCount", 26), layout.mapping is not None, swap))
    return out


def _swapped(moment: datetime) -> datetime:
    return moment.replace(month=moment.day, day=moment.month) if moment.day <= MAX_MONTH else moment


def to_app(target: Target, header: str, raw):
    """A Sheet value in the app's convention: on a swap_date_cells sheet a date cell typed mm/dd into a dd/mm sheet
    is swapped back, exactly as tracker_map.fix_date does on import."""
    if target.swap_dates and header in DATE_HEADERS and isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return serial(_swapped(from_serial(raw)))
    return raw


def to_sheet(target: Target, header: str, value):
    """The inverse of to_app for a value the app writes (a date swapped when its day is <= 12)."""
    if not (target.swap_dates and header in DATE_HEADERS):
        return value
    if isinstance(value, datetime):
        return _swapped(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return serial(_swapped(from_serial(value)))
    return value


def read(target: Target, full: bool) -> list[SheetRow]:
    """Every row under the header: its key and the synced columns' typed values (JD only when full)."""
    wanted = [(i, c.header) for i, c in target.synced if full or c.header not in LONG_COLUMNS]
    if target.key_col is not None:
        wanted.append((target.key_col, KEY_HEADER))
    ranges = [f"{quoted(target.title)}!{letter(i)}2:{letter(i)}" for i, _ in wanted]
    columns = get_values(target.sid, ranges) if ranges else []
    height = max((len(c) for c in columns), default=0)
    rows = []
    for r in range(height):
        raw = {h: to_app(target, h, col[r][0] if r < len(col) and col[r] else "")
               for (_, h), col in zip(wanted, columns)}
        key = str(raw.pop(KEY_HEADER, "") or "").strip()
        rows.append(SheetRow(r + 1, key, raw))
    return rows


def app_id_of(key: str) -> int | None:
    if key.startswith(KEY_PREFIX) and key[len(KEY_PREFIX):].isdigit():
        return int(key[len(KEY_PREFIX):])
    return None


def apps_of(conn, pid: str) -> dict[int, dict]:
    return {r["id"]: dict(r) for r in conn.execute("SELECT * FROM applications WHERE profile = ?", (pid,))}


def snapshots(conn, pid: str, title: str) -> dict[int, dict]:
    found = conn.execute("SELECT app_id, cells FROM gsync_rows WHERE profile = ? AND tab = ? AND missing_since IS NULL",
                         (pid, title))
    return {r["app_id"]: json.loads(r["cells"]) for r in found}


def save_snapshot(conn, pid: str, title: str, app_id: int, cells: dict) -> None:
    conn.execute("INSERT INTO gsync_rows(profile, tab, app_id, cells, synced_at, missing_since) VALUES (?,?,?,?,?,NULL)"
                 " ON CONFLICT(profile, app_id) DO UPDATE SET tab = excluded.tab, cells = excluded.cells, "
                 "synced_at = excluded.synced_at, missing_since = NULL",
                 (pid, title, app_id, json.dumps(cells), db.iso(db.utcnow())))


def log_event(conn, pid: str, kind: str, title: str | None = None, app_id: int | None = None,
              column: str | None = None, sheet_value: str | None = None, app_value: str | None = None,
              winner: str | None = None) -> None:
    conn.execute("INSERT INTO gsync_log(at, profile, tab, app_id, kind, column_name, sheet_value, app_value, winner)"
                 " VALUES (?,?,?,?,?,?,?,?,?)", (db.iso(db.utcnow()), pid, title, app_id, kind, column,
                                                 None if sheet_value is None else sheet_value[:500],
                                                 None if app_value is None else app_value[:500], winner))


def parse_time(text: str | None) -> datetime | None:
    if not text:
        return None
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)


def app_edit_time(app: dict, column: tracker_layout.Column) -> datetime | None:
    """When the app last edited what this column shows (per-field stamps; Status(A/R) also follows the pipeline)."""
    stamps = json.loads(app.get("field_at") or "{}")
    names = [n for n in (column.field, "status" if column.field == "status_ar" else None) if n]
    times = [parse_time(stamps[n]) for n in names if stamps.get(n)]
    return max(times) if times else None


def xlsx_row(target: Target, index: int) -> int:
    """The row number an import of this sheet would store (mapped profiles: tab-striped)."""
    number = index + 1
    return target.tab.index * SHEET_ROW_STRIDE + number if target.mapped else number


def write_cell(target: Target, index: int, col: int, cell: dict, new_row: bool = False) -> dict:
    return {"updateCells": {"start": {"sheetId": target.sheet_id, "rowIndex": index, "columnIndex": col},
                            "rows": [{"values": [cell]}], "fields": cell_fields(new_row, cell)}}


def key_cell(target: Target, index: int, app_id: int) -> dict:
    return write_cell(target, index, target.key_col, {"userEnteredValue": {"stringValue": f"{KEY_PREFIX}{app_id}"},
                                                      "userEnteredFormat": CLIP})


def eligible_new(conn, pid: str, apps: dict[int, dict]) -> list[dict]:
    """Applications the app made (not imported from the sheet) that reached 'applied' and have no row yet."""
    from .gridapi import is_blank                                # an inserted row waits for a name
    from .removal import readd                                   # put back after its Sheet row went
    known = {r["app_id"] for r in conn.execute("SELECT app_id FROM gsync_rows WHERE profile = ?", (pid,))}
    back = readd(conn, pid)
    return [a for a in apps.values() if a["id"] not in known and a["status"] in REACHED and not is_blank(a)
            and ((a.get("source") or "") not in ("import", "sheet") or a["id"] in back)]


def is_full_read(conn, target: Target) -> bool:
    last = parse_time(state.get(conn, f"gsync.{target.pid}.{target.title}.full_at"))
    return last is None or (db.utcnow() - last).total_seconds() >= FULL_READ_S
