"""A streaming reader for the big OFLC disclosure workbooks: one sheet, a header row, then ~100k-600k
rows of ~100 columns. openpyxl takes many minutes on a 250 MB file; this keeps only the wanted columns and walks the
sheet XML once with lxml's iterparse (shared strings are read first, as Excel stores text once per workbook)."""
import re
import zipfile
from collections.abc import Iterator
from datetime import date, timedelta

from lxml import etree

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_COL = re.compile(r"[A-Z]+")
EXCEL_EPOCH = date(1899, 12, 30)


def _col_index(ref: str) -> int:
    n = 0
    for ch in _COL.match(ref).group():
        n = n * 26 + ord(ch) - 64
    return n - 1


def _shared(book: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in book.namelist():
        return []
    out = []
    with book.open("xl/sharedStrings.xml") as fh:
        for _, si in etree.iterparse(fh, tag=f"{NS}si"):
            out.append("".join(t.text or "" for t in si.iter(f"{NS}t")))
            si.clear()
    return out


def _sheet_path(book: zipfile.ZipFile) -> str:
    names = sorted(n for n in book.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n))
    if not names:
        raise ValueError("no worksheet in the workbook")
    return names[0]


def _value(c, strings: list[str]) -> str | None:
    if c.get("t") == "inlineStr":
        return "".join(t.text or "" for t in c.iter(f"{NS}t"))
    v = c.find(f"{NS}v")
    if v is None:
        return None
    return strings[int(v.text)] if c.get("t") == "s" else v.text


def rows(path, wanted: tuple[str, ...]) -> Iterator[dict[str, str]]:
    """{column name: text} per data row for the `wanted` header names (missing ones are absent from the dict)."""
    with zipfile.ZipFile(path) as book:
        strings = _shared(book)
        picks: dict[int, str] | None = None
        with book.open(_sheet_path(book)) as fh:
            for _, row in etree.iterparse(fh, tag=f"{NS}row"):
                cells = {}
                for c in row.iter(f"{NS}c"):
                    i = _col_index(c.get("r"))
                    if picks is not None and i not in picks:
                        continue
                    value = _value(c, strings)
                    if value is not None:
                        cells[i] = value
                row.clear()
                while row.getprevious() is not None:
                    del row.getparent()[0]
                if picks is None:
                    picks = {i: name.strip().upper() for i, name in cells.items()
                             if name.strip().upper() in wanted}
                    continue
                yield {picks[i]: value for i, value in cells.items()}


def header(path) -> list[str]:
    """The sheet's column names (first row)."""
    with zipfile.ZipFile(path) as book:
        strings = _shared(book)
        with book.open(_sheet_path(book)) as fh:
            for _, row in etree.iterparse(fh, tag=f"{NS}row"):
                return [_value(c, strings) or "" for c in row.iter(f"{NS}c")]
    return []


def to_date(value: str | None) -> date | None:
    """An Excel serial number ("45838") or an ISO / US date string -> date."""
    if not value:
        return None
    text = value.strip()
    try:
        return EXCEL_EPOCH + timedelta(days=int(float(text)))
    except ValueError:
        pass
    for pattern, order in ((r"(\d{4})-(\d{1,2})-(\d{1,2})", "ymd"), (r"(\d{1,2})/(\d{1,2})/(\d{4})", "mdy")):
        m = re.match(pattern, text)
        if m:
            a, b, c = (int(x) for x in m.groups())
            try:
                return date(a, b, c) if order == "ymd" else date(c, a, b)
            except ValueError:
                return None
    return None
