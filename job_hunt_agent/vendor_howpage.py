"""The "how this is computed" page (decision 010), rendered from config/ats_profiles.yaml so it cannot drift
from the scoring code: the vendor's channels and features (on with the cited fact, or off), the parsing checks, and
every number that is our modeling choice."""
from html import escape

from . import ats_profile, vendor_model as vm
from .vendor_explain import FEATURE_NAMES

STYLE = """
:root{--bg:#fff;--fg:#1d1d1f;--muted:#6e6e73;--line:#d2d2d7;--ours:#8a5a00;--ours-bg:#fff4dc;--on:#0a7d32}
@media (prefers-color-scheme:dark){:root{--bg:#161618;--fg:#f2f2f4;--muted:#a1a1a6;--line:#3a3a3d;--ours:#ffcf70;
--ours-bg:#3a2d10;--on:#5fd38a}}
body{background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,system-ui,sans-serif;margin:0 auto;
max-width:860px;padding:16px}
h1{font-size:22px}h2{font-size:17px;margin-top:28px}li{margin:4px 0}a{color:inherit}
.muted{color:var(--muted)}.on{color:var(--on);font-weight:600}.ours{background:var(--ours-bg);color:var(--ours);
padding:1px 6px;border-radius:4px;font-size:12px;font-weight:600}
nav a{margin-right:10px}table{border-collapse:collapse;width:100%}td{border-top:1px solid var(--line);padding:6px 4px;
vertical-align:top}
"""


def _facts(model: vm.Model, prof: dict, ids) -> str:
    facts = ats_profile.facts(prof)
    return "".join(f'<li>{escape(facts[i]["text"])} <a href="{escape(facts[i]["source"])}">[source]</a></li>'
                   for i in ids)


def _channel(model: vm.Model, prof: dict, ch: vm.Channel) -> str:
    mode = ("literal: the JD's own words; <span class=ours>case-insensitive, whole words, no plural, alias or acronym "
            "credit (our reading where undocumented)</span>" if ch.skills == "literal"
            else "taxonomy: a skill or any of its aliases counts")
    rows = [f"<tr><td>Turned on by</td><td><ul>{_facts(model, prof, ch.cite)}</ul></td></tr>",
            f"<tr><td>Skills</td><td>{mode}</td></tr>"]
    feats = dict(ch.features)
    for key, name in FEATURE_NAMES.items():
        cell = (f"<span class=on>on</span><ul>{_facts(model, prof, feats[key])}</ul>" if key in feats
                else "<span class=muted>off: no cited fact</span>")
        rows.append(f"<tr><td>{escape(name.capitalize())}</td><td>{cell}</td></tr>")
    return f"<h2>Channel: {escape(ch.name)}</h2><table>{''.join(rows)}</table>"


def _checks(model: vm.Model, prof: dict) -> str:
    if not model.checks:
        return "<h2>Parsing checks</h2><p class=muted>None documented.</p>"
    items = "".join(f"<li><b>{escape(c.name)}</b> ({escape(c.effect)}{f', {escape(str(c.value))}' if c.value else ''})"
                    f"<ul>{_facts(model, prof, c.cite)}</ul></li>" for c in model.checks)
    return f"<h2>Parsing checks on your resume file</h2><ul>{items}</ul>"


def render(vendor: str) -> str:
    profiles, models = ats_profile.profiles(), vm.models()
    prof, model = profiles.get(vendor) or {}, models.get(vendor)
    label = escape(str(prof.get("label") or vendor or "this ATS"))
    nav = " ".join(f'<a href="/how/ats?vendor={escape(v)}">{escape(str(p.get("label") or v))}</a>'
                   for v, p in profiles.items())
    head = (f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,"
            f"initial-scale=1'><title>How {label} is scored</title><style>{STYLE}</style></head><body>"
            f"<nav class=muted>{nav}</nav><h1>Estimated {label} match: how this is computed</h1>"
            "<p>Vendors do not publish their scoring formulas. job-hunt-agent models each one only from the vendor's own public "
            "documentation; every feature below is either turned on by a cited vendor page or off. Numbers marked "
            "<span class=ours>ours</span> are our modeling choices, not the vendor's. The result is an estimate: an "
            "employer's own settings (which features are enabled, screening rules) are not visible.</p>")
    if model is None or not model.channels:
        body = (f"<p><b>{label}'s candidate matching is not publicly documented</b>, so jobs on {label} show the generic "
                "keyword match (JD keywords found in your resume / JD keywords), labeled as such.</p>")
        body += _checks(model, prof) if model else ""
    else:
        body = "".join(_channel(model, prof, ch) for ch in model.channels) + _checks(model, prof)
        body += ("<h2>Putting it together</h2><p>Each channel: skills, title and years combined; the vendor estimate: "
                 "the mean of its channels, times the parsing factor. Best resume: the highest estimate.</p>")
    ours = "".join(f"<li><b>{escape(k)}</b> <span class=ours>ours</span>: {escape(v)}</li>" for k, v in vm.OURS)
    return head + body + f"<h2>Our modeling choices</h2><ul>{ours}</ul></body></html>"
