"""The full JD as a document: a header (company, role, location, workplace, salary, req id, posting URL,
posted, applied, resume used) and the JD exactly as posted, never boilerplate-stripped: the feed's raw
jobs.description, else the row's JD Text, else its kept snapshot, else the JD cell's own text. Headings are bold and
bullets are bullets. The same HTML goes to the Google Doc (Drive converts it) and to the app's "Full JD" page.
"""
import re
from html import escape

from . import db, jdsnap

GONE = "posting gone, no JD saved"
HEADING_MAX = 80
TITLE_HEADING_WORDS = 8
_BULLET = re.compile(r"^\s*[-•·*▪●◦‣–]\s+")
_END_PUNCT = re.compile(r"[.!?,;]$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def job_of(conn, app: dict | None, uid: str | None = None) -> dict:
    uid = uid or (app or {}).get("job_uid")
    row = conn.execute("SELECT * FROM jobs WHERE uid = ?", (uid,)).fetchone() if uid else None
    return dict(row) if row else {}


def source_text(conn, app: dict | None, job: dict) -> tuple[str | None, str | None]:
    """(the JD as posted, where it came from): raw feed text first; (None, None) when nothing holds it."""
    raw = db.unpack(job.get("description")).strip() if job.get("description") else ""
    if raw:
        return raw, "feed"
    if not app:
        return None, None
    if (app.get("jd_text") or "").strip():
        return app["jd_text"].strip(), "jd_text"
    snap = jdsnap.get(conn, app["id"])
    if snap and snap["state"] == "saved" and (snap["text"] or "").strip():
        return snap["text"].strip(), f"snapshot ({snap['source'] or 'kept'})"
    text = jdsnap._cell_text(app, jdsnap.posting_url(app))
    return (text, "sheet") if text else (None, None)


def _salary(app: dict | None, job: dict) -> str | None:
    if (app or {}).get("salary"):
        return app["salary"]
    low, high = job.get("salary_min"), job.get("salary_max")
    if not (low or high):
        return None
    money = [f"{int(v):,}" for v in (low, high) if v]
    return f"{' - '.join(dict.fromkeys(money))} {job.get('salary_ccy') or ''}".strip()


def _day(value: str | None) -> str | None:
    return value[:10] if value and _DATE.match(value) else value


def header(app: dict | None, job: dict) -> list[tuple[str, str]]:
    """(label, value) pairs, only the ones known."""
    app = app or {}
    pairs = [("Company", app.get("company") or job.get("company")),
             ("Role title", app.get("role_title") or job.get("title")),
             ("Location", app.get("location") or job.get("location")),
             ("Workplace", app.get("workplace") or job.get("workplace")),
             ("Salary", _salary(app, job)),
             ("Req ID", job.get("req_id")),
             ("Posting URL", (jdsnap.posting_url(app) if app else None) or job.get("url") or job.get("apply_url")),
             ("Posted", _day(job.get("first_posted_at") or job.get("posted_at"))),
             ("Date applied", app.get("date_applied")),
             ("Resume used", app.get("resume") or app.get("resume_used"))]
    return [(label, str(value).strip()) for label, value in pairs if value and str(value).strip()]


def title(pairs: list[tuple[str, str]]) -> str:
    found = dict(pairs)
    return " - ".join(v for v in (found.get("Company"), found.get("Role title")) if v) or "Job description"


def _is_heading(line: str, before: str, after: str) -> bool:
    if len(line) > HEADING_MAX or _BULLET.match(line):
        return False
    if line.endswith(":"):
        return True
    letters = [c for c in line if c.isalpha()]
    if len(letters) >= 3 and line.isupper():
        return True
    words = line.split()
    capped = sum(w[0].isupper() for w in words if w[0].isalpha())
    return (len(words) <= TITLE_HEADING_WORDS and not _END_PUNCT.search(line) and not before.strip()
            and bool(after.strip()) and capped * 2 >= len(words) and line[0].isupper())


def blocks(text: str) -> list[tuple[str, str]]:
    """The JD as ("h" | "li" | "p", line) blocks, in order."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out = []
    for i, raw in enumerate(lines):
        line = raw.strip()
        if not line:
            continue
        before = lines[i - 1] if i else ""
        after = lines[i + 1] if i + 1 < len(lines) else ""
        if _BULLET.match(line):
            out.append(("li", _BULLET.sub("", line)))
        elif _is_heading(line, before, after):
            out.append(("h", line))
        else:
            out.append(("p", line))
    return out


def body_html(text: str) -> str:
    parts, in_list = [], False
    for kind, line in blocks(text):
        if kind == "li" and not in_list:
            parts.append("<ul>")
        elif kind != "li" and in_list:
            parts.append("</ul>")
        in_list = kind == "li"
        tag = {"li": "<li>{}</li>", "h": "<p><b>{}</b></p>", "p": "<p>{}</p>"}[kind]
        parts.append(tag.format(escape(line)))
    return "".join(parts + (["</ul>"] if in_list else []))


def _value(label: str, value: str) -> str:
    if label == "Posting URL" and value.startswith(("http://", "https://")):
        return f'<a href="{escape(value)}">{escape(value)}</a>'
    return escape(value)


def header_html(pairs: list[tuple[str, str]]) -> str:
    return "".join(f"<p><b>{escape(label)}:</b> {_value(label, value)}</p>" for label, value in pairs)


def doc_html(pairs: list[tuple[str, str]], text: str | None) -> str:
    """The Google Doc's content (Drive converts this HTML to a Doc)."""
    body = body_html(text) if text else f"<p><i>{GONE}</i></p>"
    return (f'<html><head><meta charset="utf-8"><title>{escape(title(pairs))}</title></head><body>'
            f"<h1>{escape(title(pairs))}</h1>{header_html(pairs)}<hr><h2>Job description</h2>{body}</body></html>")


def page_html(pairs: list[tuple[str, str]], text: str | None, doc_link: str | None) -> str:
    """The app's "Full JD" page: the same header and text, Copy and Print (static CSS and JS: the CSP allows no
    inline code), readable on a phone."""
    doc = f'<a href="{escape(doc_link)}" target="_blank" rel="noopener">Google Doc</a>' if doc_link else ""
    body = body_html(text) if text else f'<p class="gone">{GONE}</p>'
    name = escape(title(pairs))
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1"><title>{name} - JD</title>'
            f'<link rel="stylesheet" href="/jdpage.css"><script type="module" src="/js/jdpage.js"></script></head>'
            f'<body><nav><button id="copy" type="button">Copy</button><button id="print" type="button">Print</button>'
            f'{doc}</nav><article id="jd"><h1>{name}</h1>{header_html(pairs)}<hr>'
            f"<h2>Job description</h2>{body}</article></body></html>")
