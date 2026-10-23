"""The Stats tab as server HTML (moved from stats.js to keep the UI inside its JavaScript budget, as GOAL12's mail panel
did). Every value is escaped. The CSP blocks inline styles, so each bar carries its scale in data-s / data-ax and
stats.js applies it as a transform."""
import math
from html import escape

STATUS_LABELS = {"saved": "Saved", "applied": "Applied", "screen": "Screen", "interview": "Interview", "offer": "Offer",
                 "rejected": "Rejected", "ghosted": "Cold"}
WEEK_LABELS = 13
PHONE_WEEK_LABELS = 6
SIZE_NOTE = "Size = the company's board segment (S&P 500 / YC lists, open postings, ATS)."


def _e(value) -> str:
    return escape(str(value), quote=True)


def _pct(rate) -> str:
    """Halves round up, as the old JS view did (Python's round() goes to even)."""
    return "none yet" if rate is None else f"{math.floor(rate * 1000 + 0.5) / 10:g}%"


def _days(value) -> str:
    return "none yet" if value is None else f"{value:g} d"


def _week(iso: str) -> str:
    _, m, d = iso.split("-")
    return f"{int(m)}/{int(d)}"


def _tile(value, label: str, note: str = "") -> str:
    note_html = f'<div class="muted small">{_e(note)}</div>' if note else ""
    return f'<div class="tile"><div class="tile-value">{_e(value)}</div><div class="tile-label">{_e(label)}</div>{note_html}</div>'


def _weekly(weeks: list[dict], phone: bool) -> str:
    top = max([1, *(w["n"] for w in weeks)])
    step = -(-len(weeks) // (PHONE_WEEK_LABELS if phone else WEEK_LABELS))
    said = ", ".join(f"{_week(w['week'])} {w['n']}" for w in weeks)
    cols = "".join(
        f'<div class="wcol" title="Week of {_e(w["week"])}: {w["n"]}"><span class="wn">{w["n"] if w["n"] and step == 1 else ""}</span>'
        f'<span class="wtrack"><span class="wbar" data-ax="Y" data-s="{w["n"] / top:.4f}"></span></span>'
        f'<span class="wlabel">{"" if (len(weeks) - 1 - i) % step else _week(w["week"])}</span></div>'
        for i, w in enumerate(weeks))
    return f'<div class="weeks" role="img" aria-label="Applications per week: {_e(said)}">{cols}</div>'


def _rates(title: str, rows: list[dict], note: str = "") -> str:
    top = max([1, *(r["n"] for r in rows)])
    head = "".join(f'<th scope="col">{x}</th>' for x in ("", "Applied", "Responses", "Rate"))
    body = "".join(
        f'<tr><th scope="row">{_e(r["label"])}</th><td>{r["n"]}</td><td>{r["responses"]}</td><td>{_pct(r["rate"])}</td>'
        f'<td class="rbar"><span class="rfill" data-ax="X" data-s="{r["n"] / top:.4f}"></span></td></tr>' for r in rows)
    note_html = f'<p class="muted small">{_e(note)}</p>' if note else ""
    return (f'<section class="stat-block"><h3>{_e(title)}</h3>{note_html}<table class="rates"><thead><tr>{head}'
            f'<th scope="col"><span class="sr">Share</span></th></tr></thead><tbody>{body}</tbody></table></section>')


def _not_interested(n: dict | None) -> str:
    """why jobs were passed on, the four chips and Other, plus the typed Other reasons."""
    if not n or not n["total"]:
        return ""
    top = max([1, *(r["n"] for r in n["by_reason"])])
    body = "".join(f'<tr><th scope="row">{_e(r["label"])}</th><td>{r["n"]}</td>'
                   f'<td class="rbar"><span class="rfill" data-ax="X" data-s="{r["n"] / top:.4f}"></span></td></tr>'
                   for r in n["by_reason"])
    notes = ", ".join(f'{_e(x["note"])} ({x["n"]})' for x in n["other_notes"])
    notes_html = f'<p class="muted small">Other: {notes}</p>' if notes else ""
    return (f'<section class="stat-block"><h3>Not interested: why</h3><p class="muted small">{n["total"]} jobs passed on '
            f'(the newest reason per job)</p><table class="rates"><thead><tr><th scope="col"></th><th scope="col">Jobs</th>'
            f'<th scope="col"><span class="sr">Share</span></th></tr></thead><tbody>{body}</tbody></table>{notes_html}</section>')


def render(s: dict, phone: bool = False) -> str:
    """stats.report(...) as the Stats view."""
    statuses = " · ".join(f"{v} {s['status_counts'].get(k, 0)}" for k, v in STATUS_LABELS.items())
    timed = s["n_timed_responses"]
    tiles = "".join((
        _tile(s["applications"], "applications", statuses),
        _tile(_pct(s["rate"]), "response rate", f"{s['responses']} reached screen or later"),
        _tile(s["this_week"], "this week"),
        _tile(_days(s["median_days_to_response"]), "median days to a response",
              f"from {timed} repl{'y' if timed == 1 else 'ies'} set here" if timed else "set Screen / Interview in the tracker to time replies"),
        _tile(_days(s["median_days_to_rejection"]), "median days to a rejection",
              f"from {s['n_timed_rejections']}" if s["n_timed_rejections"] else "none timed yet")))
    grid = "".join((
        _rates("By role family", s["by_family"]), _rates("By company size", s["by_size"], SIZE_NOTE),
        _rates("By visa sponsorship badge", s.get("by_sponsor") or [],                     # GOAL24 §4
               "Does Sponsors convert better? The company's badge today, from public H-1B filings and the post.")
        if s.get("by_sponsor") else "",
        _rates("By days since posted, when you applied", s["by_days_posted"],
               f"{s['posting_dated']} of {s['applications']} applications have a known posting date."),
        _not_interested(s.get("not_interested"))))
    return (f'<div class="stats"><header class="t-head"><h1>Stats</h1><p class="muted small">What gets callbacks</p></header>'
            f'<div class="tiles">{tiles}</div><section class="stat-block"><h3>Applications per week</h3>'
            f'{_weekly(s["weekly"], phone)}</section><div class="stat-grid">{grid}</div></div>')
