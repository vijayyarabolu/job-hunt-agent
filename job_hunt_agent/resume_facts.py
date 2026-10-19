"""Facts a vendor model reads from a resume file (decision 010): total years of experience from its date
ranges, and the layout features vendors document as parsing risks.

Years: every "Mon YYYY – Mon YYYY|Present" or "YYYY – YYYY|Present" range outside education/certification sections,
overlaps counted once. Month ranges count the end month; "Present" is the current month; year-only ranges count whole
years (2019 - 2021 = 2). Parsing risks are only what the file shows: tables, text boxes, text in headers/footers,
multi-column sections, images, a file type outside DOCX/PDF, and an unreadable file.
"""
import io
import re
import zipfile
from datetime import date

MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                      "dec"), start=1)}
_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_POINT = rf"(?:({_MONTH})\s+)?((?:19|20)\d\d)"
_RANGE = re.compile(rf"\b{_POINT}\s*(?:[-–—]+|to)\s*(?:{_POINT}|(present|current|now|today))\b", re.I)
_SKIPPED = re.compile(r"^\s*(education|certifications?|licenses?|publications?|awards?)\b", re.I)
_COUNTED = re.compile(r"^\s*((work|professional|relevant)\s+)?(experience|employment|work history)\b", re.I)
KNOWN_TYPES = (".docx", ".pdf")
MB = 1024 * 1024


def _start(month: str | None, year: str) -> int:
    return int(year) * 12 + (MONTHS[month[:3].lower()] - 1 if month else 0)


def _end(month: str | None, year: str | None, now: str | None, today: date) -> int:
    if now:
        return today.year * 12 + today.month           # the current month, counted
    return int(year) * 12 + (MONTHS[month[:3].lower()] if month else 0)


def _ranges(text: str, today: date) -> list[tuple[int, int]]:
    out, counting = [], True
    for line in text.splitlines():
        if _SKIPPED.match(line):
            counting = False
        elif _COUNTED.match(line):
            counting = True
        if not counting:
            continue
        for m in _RANGE.finditer(line):
            start, end = _start(m.group(1), m.group(2)), _end(m.group(3), m.group(4), m.group(5), today)
            if end > start:
                out.append((start, end))
    return out


def years_of(text: str, today: date | None = None) -> float | None:
    """Years of experience covered by the resume's date ranges (union), or None when it has none."""
    spans = sorted(_ranges(text or "", today or date.today()))
    if not spans:
        return None
    months, cur_start, cur_end = 0, *spans[0]
    for start, end in spans[1:]:
        if start > cur_end:
            months += cur_end - cur_start
            cur_start, cur_end = start, end
        else:
            cur_end = max(cur_end, end)
    return (months + cur_end - cur_start) / 12


def size_mb(data: bytes) -> float:
    return round(len(data) / MB, 2)


def _docx_risks(data: bytes) -> tuple[str, ...]:
    try:
        book = zipfile.ZipFile(io.BytesIO(data))
        body = book.read("word/document.xml").decode("utf8", "ignore")
    except (zipfile.BadZipFile, KeyError):
        return ("unreadable",)
    names = book.namelist()
    furniture = [n for n in names if re.fullmatch(r"word/(header|footer)\d*\.xml", n)]
    risks = {
        "tables": "<w:tbl>" in body or "<w:tbl " in body,
        "text_boxes": "txbxContent" in body,
        "header_footer_text": any(re.sub(r"<[^>]+>", "", book.read(n).decode("utf8", "ignore")).strip()
                                  for n in furniture),
        "columns": bool(re.search(r'<w:cols\b[^>]*w:num="([2-9])"', body)),
        "images": any(n.startswith("word/media/") for n in names),
    }
    return tuple(k for k, found in risks.items() if found)


def parse_risks(data: bytes, filename: str) -> tuple[str, ...]:
    """Layout features of one resume file that vendors document as parsing risks (codes, file order)."""
    name = filename.lower()
    if not name.endswith(KNOWN_TYPES):
        return ("file_type",)
    if name.endswith(".docx"):
        return _docx_risks(data)
    return ()                                           # a PDF's layout is not inspected (stdlib only)
