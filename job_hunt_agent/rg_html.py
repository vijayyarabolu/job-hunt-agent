"""The resume panel's read-only blocks as server HTML (moved from rg.js to keep the UI inside its JavaScript budget):
the ATS keyword match and the word-level diff from resumegen's ops. Every value is escaped."""
from html import escape

NO_CHANGES = '<p class="muted">No changes.</p>'


def _e(value) -> str:
    return escape(str(value), quote=True)


def ats(a: dict | None) -> str:
    if not a:
        return ""
    gaps = ", ".join(map(str, a.get("missing") or []))
    missing = f'<p class="small">Still missing: {_e(gaps)}</p>' if gaps else ""
    return (f'<div class="rg-ats"><p title="ATS keyword match: your base resume → the tailored one">ATS keyword match '
            f'<b>{_e(a.get("base_pct"))}%</b> base → <b>{_e(a.get("tailored_pct"))}%</b> tailored</p>'
            f'<p class="muted small">{_e(a.get("label") or "")}</p>{missing}</div>')


def _op(o) -> str:
    """One of resumegen's ops: [op, base, new] with op equal | insert | delete | replace; a malformed one is skipped."""
    if not isinstance(o, (list, tuple)) or not o:
        return ""
    op, a, b = (list(o) + [None, None])[:3]
    if op == "equal":
        return "" if a is None else _e(a)
    gone = f"<del>{_e(a)}</del>" if op != "insert" and a else ""
    return gone + (f"<ins>{_e(b)}</ins>" if op != "delete" and b else "")


def _line(name: str | None, x: dict | None) -> str:
    if not (x or {}).get("changed"):
        return ""
    label = f'<b class="rg-k">{_e(name)}</b>' if name else ""
    return f"<li>{label}<span>{' '.join(filter(None, map(_op, x.get('ops') or [])))}</span></li>"


def _count(value) -> str:
    return "?" if value is None else _e(value)


def diff(x: dict | None) -> str:
    """Open by default; rg.js closes it on a phone."""
    if not x:
        return ""
    body = "".join([_line("Headline", x.get("headline")), _line("Summary", x.get("summary")),
                    *(_line(s.get("category"), s.get("items")) for s in x.get("skills") or []),
                    *(_line(f"{j.get('company')} · bullet {i}", b) for j in x.get("jobs") or []
                      for i, b in enumerate(j.get("bullets") or [], 1))])
    counts = f"{_count(x.get('changed_bullets'))} of {_count(x.get('total_bullets'))} bullets"
    return (f'<details class="rg-diff" open><summary title="Show or hide the word-level changes">Changes vs your base '
            f'resume ({counts})</summary>{f"<ul>{body}</ul>" if body else NO_CHANGES}</details>')
