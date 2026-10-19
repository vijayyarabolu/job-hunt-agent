"""Cell values between the DB and a person's real Google Sheet (decision 008). Pure functions.

The sync compares *canonical text* on both sides, so a cell's display format never looks like an edit:
  - the Sheet is read with valueRenderOption=FORMULA + dateTimeRenderOption=SERIAL_NUMBER: a formula comes back as
    '=...', a date as its serial number, a number as a number;
  - the DB side is tracker_layout.cell() (what the Excel export writes): a datetime becomes the same serial number.
Writes send typed values (a string is always a stringValue, so typed text that looks like a formula never runs; only
a formula imported from the sheet itself goes back as a formula) and only `userEnteredValue` + wrap CLIP, so the
sheet's own formatting, number formats and dropdowns stay exactly as they are.
Every cell the app writes into a visible column is ONE clipped line (user, 2026-10-06 evening: no row ever grows):
line breaks become spaces, and the comparison reads a line break and a space as the same, so a person's own
multi-line cells never look edited. Only the hidden "JD Text" column keeps the JD's lines.
"""
import re
from datetime import date, datetime, timedelta

SHEETS_EPOCH = datetime(1899, 12, 30)
DATE_HEADERS = ("Date Applied",)          # the only columns holding real date cells in both real sheets
LINKED_HEADERS = ("JD",)                  # every URL in these cells is written as a real hyperlink
MAX_CELL = 49_000                         # Sheets holds 50,000 characters per cell
JD_TEXT_HEADER = "JD Text"                # the hidden column with the full JD (gsync_jd.py): keeps its lines
CLIP = {"wrapStrategy": "CLIP"}
_BREAK = re.compile(r"[ \t]*(?:\r\n|\r|\n)+[ \t]*")
EMPTY = ""
_URL = re.compile("https?://[^\\s<>\"'​-‍⁠﻿]+")   # zero-width characters end a link too
_URL_TAIL = ".,;:!?)]}'\""                # punctuation that ends a sentence, not the link


def one_line(text: str) -> str:
    """The text on one line: each line break (and the spaces around it) becomes one space."""
    return _BREAK.sub(" ", text)


def urls(text: str) -> list[tuple[int, int, str]]:
    """(start, end, url) of every http(s) link in the text, trailing punctuation left out (Python indexes)."""
    out = []
    for match in _URL.finditer(text or ""):
        url = match.group(0).rstrip(_URL_TAIL)
        if len(url) > len("https://"):
            out.append((match.start(), match.start() + len(url), url))
    return out


def _utf16(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2              # Sheets run indexes count UTF-16 code units


def link_runs(text: str) -> list[dict]:
    """textFormatRuns that make every URL in the text a clickable link (the API never auto-links a written
    string); [] when the text has no URL. Plain runs reset the format after each link."""
    found = urls(text)
    if not found:
        return []
    runs, total = [], _utf16(text)
    if found[0][0] > 0:
        runs.append({"startIndex": 0, "format": {}})
    for start, end, url in found:
        runs.append({"startIndex": _utf16(text[:start]), "format": {"link": {"uri": url}}})
        if _utf16(text[:end]) < total:
            runs.append({"startIndex": _utf16(text[:end]), "format": {}})
    return runs


def serial(moment: datetime | date) -> float:
    if not isinstance(moment, datetime):
        moment = datetime(moment.year, moment.month, moment.day)
    delta = moment.replace(tzinfo=None) - SHEETS_EPOCH
    return delta.days + delta.seconds / 86400


def from_serial(number: float) -> datetime:
    return SHEETS_EPOCH + timedelta(days=float(number))


def _number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else repr(round(float(value), 10))


def canon(value, header: str | None = None) -> str:
    """One comparable text for a cell value from either side ('' = empty). In a date column the same day compares
    equal however it is stored (a real date cell or typed text '05/12/2026')."""
    if header in DATE_HEADERS and isinstance(value, str):
        from .tracker import parse_applied
        iso = parse_applied(value)
        value = datetime.fromisoformat(iso) if iso else value
    if value is None:
        return EMPTY
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (datetime, date)):
        return _number(serial(value))
    if isinstance(value, (int, float)):
        return _number(value)
    return str(value).replace("\r\n", "\n") if header == JD_TEXT_HEADER else one_line(str(value))


def to_cell(value, as_text: bool, link: str | None = None, new_row: bool = False,
            header: str | None = None, restore: bool = False) -> dict:
    """A Sheets CellData for one DB value: one clipped line in a visible column (the JD Text column keeps its
    lines). A new row also gets a date format on dates and the cell's link (an existing cell keeps its own format
    and link: only its value and wrap are written). In a LINKED_HEADERS column (JD) every URL in the text becomes a
    real link, on new and existing cells alike. restore: a cell put back with the text the Sheet had."""
    cell: dict = {}
    if isinstance(value, str) and not restore and header != JD_TEXT_HEADER:
        value = one_line(value)
    if value is None or value == "":
        cell["userEnteredValue"] = {}
    elif isinstance(value, bool):
        cell["userEnteredValue"] = {"boolValue": value}
    elif isinstance(value, (datetime, date)):
        cell["userEnteredValue"] = {"numberValue": serial(value)}
        if new_row:
            cell["userEnteredFormat"] = {"numberFormat": {"type": "DATE", "pattern": "mm/dd/yyyy"}}
    elif isinstance(value, (int, float)):
        cell["userEnteredValue"] = {"numberValue": value}
    elif isinstance(value, str) and value.startswith("=") and not as_text:
        cell["userEnteredValue"] = {"formulaValue": value}          # a formula the sheet itself had
    else:
        cell["userEnteredValue"] = {"stringValue": str(value)[:MAX_CELL]}
    if header in LINKED_HEADERS and isinstance(value, str) and "stringValue" in cell["userEnteredValue"]:
        cell["textFormatRuns"] = link_runs(cell["userEnteredValue"]["stringValue"])
    elif new_row and link and isinstance(value, str) and value:
        cell["textFormatRuns"] = [{"startIndex": 0, "format": {"link": {"uri": link}}}]
    cell["userEnteredFormat"] = {**cell.get("userEnteredFormat", {}), **CLIP}
    return cell


def fields(new_row: bool, cell: dict | None = None) -> str:
    """The update mask: the value, plus whatever else this cell carries (links, chips, a note, clipping)."""
    cell = cell or {}
    out = ["userEnteredValue"]
    if new_row:
        out.append("userEnteredFormat.numberFormat")
    if new_row or "textFormatRuns" in cell:
        out.append("textFormatRuns")
    out += [k for k in ("chipRuns", "note") if k in cell]
    if "wrapStrategy" in (cell.get("userEnteredFormat") or {}):
        out.append("userEnteredFormat.wrapStrategy")
    return ",".join(out)


DRIVE_FILE = re.compile(r"^https://(drive|docs)\.google\.com/")
CHIP = "@"                                # Sheets: a chip is written as an "@" placeholder its chip run points at
FILE_GAP = " "                            # between two files in one Resume cell


def _units(text: str) -> bytes:
    return text.encode("utf-16-le")


def _pieces(cell: dict) -> list[tuple[str, str | None, str | None]]:
    """The cell's text cut at its runs: (text, chip uri, link uri). Run indexes count UTF-16 units."""
    entered = cell.get("userEnteredValue") or {}
    text = entered.get("stringValue") or cell.get("formattedValue") or ""
    marks = {}                            # start -> (chip uri, link uri)
    for run in cell.get("textFormatRuns") or ([{"startIndex": 0, "format": {"link": {"uri": cell["hyperlink"]}}}]
                                              if cell.get("hyperlink") else []):
        marks[run.get("startIndex", 0)] = (None, ((run.get("format") or {}).get("link") or {}).get("uri"))
    for run in cell.get("chipRuns") or []:
        uri = ((run.get("chip") or {}).get("richLinkProperties") or {}).get("uri")
        start = run.get("startIndex", 0)
        marks[start] = (uri, None) if uri else (None, marks.get(start, (None, None))[1])
    raw, total = _units(text), _utf16(text)
    starts = sorted({0, *(i for i in marks if 0 <= i < total)})
    out = []
    for start, end in zip(starts, [*starts[1:], total]):
        chip, link = marks.get(start, (None, None))
        out.append((raw[2 * start:2 * end].decode("utf-16-le"), chip, link))
    return [p for p in out if p[0]]


def chip_cell(cell: dict, extra: list[tuple[str, str]] = ()) -> dict:
    """The cell rewritten with every Drive file (a chip, or a link to a Drive file) as a Drive file chip, other text
    and links kept, plus `extra` (name, Drive link) files; one clipped line, files apart by a space (as the person's
    older rows have them)."""
    pieces = []
    for text, chip, link in _pieces(cell):
        drive = link if link and DRIVE_FILE.match(link) else None
        pieces.append((CHIP, chip or drive, None) if chip or drive else (one_line(text), None, link))
    for _, uri in extra:
        pieces += [(FILE_GAP, None, None)] if pieces else []
        pieces.append((CHIP, uri, None))
    text = "".join(p[0] for p in pieces)
    total, at, chips, links = _utf16(text), 0, [], []
    for piece, chip, link in pieces:
        end = at + _utf16(piece)
        if chip:
            chips += [{"startIndex": at, "chip": {"richLinkProperties": {"uri": chip}}},
                      *([{"startIndex": end}] if end < total else [])]
        elif link:
            links += [{"startIndex": at, "format": {"link": {"uri": link}}},
                      *([{"startIndex": end, "format": {}}] if end < total else [])]
        at = end
    return {"userEnteredValue": {"stringValue": text}, "chipRuns": chips, "textFormatRuns": links,
            "userEnteredFormat": {"wrapStrategy": "CLIP"}}


def to_db(raw, header: str):
    """A Sheet value as the Python value an import of the same cell would store (dates in date columns become
    datetimes, whole numbers ints, empty None)."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        if header in DATE_HEADERS:
            return from_serial(raw)
        return int(raw) if float(raw).is_integer() else raw
    return str(raw)


def text_of(value) -> str | None:
    """The text a DB text column stores for a pulled value (dates as MM/DD/YYYY, like the tracker import)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime("%m/%d/%Y")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)
