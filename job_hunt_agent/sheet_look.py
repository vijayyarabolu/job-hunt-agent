"""How each person's Google Sheet looks, so the Tracker grid shows the same: the header row's colors,
the alternating row colors, each column's width, the frozen rows and the simple conditional colors ("JD and Resume
cells turn green when filled"). Read-only, one small read (the header row) at most once per LOOK_TTL_S, cached in
google_state; a page load never waits for it (a missing or old look is refreshed in the background)."""
import json
import logging
import threading

from . import db, gsync
from .google import accounts, api
from .google import state as gstate

log = logging.getLogger(__name__)
LOOK_TTL_S = 6 * 3600
RULES = ("NOT_BLANK", "BLANK", "TEXT_EQ", "TEXT_CONTAINS")
FIELDS = ("sheets(properties(title,gridProperties(frozenRowCount,frozenColumnCount)),bandedRanges(range,rowProperties),"
          "conditionalFormats(ranges,booleanRule(condition,format(backgroundColor,textFormat(foregroundColor,bold)))),"
          "data(columnMetadata(pixelSize,hiddenByUser),"
          "rowData(values(formattedValue,effectiveFormat(backgroundColor,textFormat(foregroundColor,bold))))))")
_busy: set[str] = set()
_lock = threading.Lock()


def color(c: dict | None) -> str | None:
    """A Sheets color as #rrggbb (missing channels are 0, as the API omits them)."""
    if not c:
        return None
    return "#" + "".join(f"{round(255 * float(c.get(k, 0))):02x}" for k in ("red", "green", "blue"))


def _rule(cf: dict, head: list[str]) -> dict | None:
    rule = cf.get("booleanRule") or {}
    cond, fmt = rule.get("condition") or {}, rule.get("format") or {}
    if cond.get("type") not in RULES:
        return None
    headers = [head[i] for r in cf.get("ranges") or [] for i in range(r.get("startColumnIndex", 0),
                                                                     min(r.get("endColumnIndex", len(head)), len(head)))
               if head[i]]
    value = ((cond.get("values") or [{}])[0]).get("userEnteredValue")
    text = fmt.get("textFormat") or {}
    out = {"headers": headers, "when": cond["type"].lower(), "value": value, "bg": color(fmt.get("backgroundColor")),
           "fg": color(text.get("foregroundColor")), "bold": bool(text.get("bold"))}
    return out if headers and (out["bg"] or out["fg"] or out["bold"]) else None


def parse(sheet: dict) -> dict:
    """One tab's look out of its spreadsheets.get answer (header row only)."""
    props, data = sheet.get("properties") or {}, (sheet.get("data") or [{}])[0]
    cells = ((data.get("rowData") or [{}])[0]).get("values") or []
    head = [str(v.get("formattedValue") or "").strip() for v in cells]
    first = (cells[0].get("effectiveFormat") or {}) if cells else {}
    band = ((sheet.get("bandedRanges") or [{}])[0]).get("rowProperties") or {}
    meta = data.get("columnMetadata") or []
    grid = props.get("gridProperties") or {}
    return {"header_bg": color(first.get("backgroundColor")),
            "header_fg": color((first.get("textFormat") or {}).get("foregroundColor")),
            "band": [b for b in (color(band.get("firstBandColor")), color(band.get("secondBandColor"))) if b],
            "widths": {h: m.get("pixelSize") for h, m in zip(head, meta) if h and m.get("pixelSize")},
            "hidden": [h for h, m in zip(head, meta) if h and m.get("hiddenByUser")],
            "rules": [r for r in (_rule(cf, head) for cf in sheet.get("conditionalFormats") or []) if r],
            "frozen_rows": grid.get("frozenRowCount", 0), "frozen_cols": grid.get("frozenColumnCount", 0)}


def read(conn, pid: str) -> dict:
    """{tab title: look} for every application tab of the person's linked Sheet (their own token, else the owner's)."""
    targets = accounts.with_fallback(pid, gsync.targets, conn, pid)
    if not targets:
        return {}
    got = accounts.with_fallback(pid, api.get, gsync.sheets_url(targets[0].sid),
                                 {"ranges": [f"{gsync.quoted(t.title)}!1:1" for t in targets], "fields": FIELDS,
                                  "includeGridData": "true"})
    return {s["properties"]["title"]: parse(s) for s in got.get("sheets", [])}


def _refresh(pid: str) -> None:
    conn = db.connect()
    try:
        looks = read(conn, pid)
        with db.tx(conn):
            gstate.put(conn, f"look.{pid}", json.dumps({"at": db.iso(db.utcnow()), "tabs": looks}))
    except Exception:                                   # the grid keeps its own look; tried again later
        log.exception("sheet look: could not read %s's Sheet", pid)
    finally:
        conn.close()
        with _lock:
            _busy.discard(pid)


def get(conn, pid: str, background: bool = True) -> dict | None:
    """The cached look ({"at", "tabs"}), or None; an old or missing one starts a refresh in the background."""
    cached = gstate.get_json(conn, f"look.{pid}")
    at = gsync.parse_time((cached or {}).get("at"))
    fresh = at is not None and (db.utcnow() - at).total_seconds() < LOOK_TTL_S
    if not fresh and background and gsync.linked_sid(conn, pid):
        with _lock:
            start = pid not in _busy
            _busy.add(pid)
        if start:
            threading.Thread(target=_refresh, args=(pid,), daemon=True, name=f"sheet-look-{pid}").start()
    return cached
