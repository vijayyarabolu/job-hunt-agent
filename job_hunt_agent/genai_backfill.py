"""`job-hunt-agent genai-backfill` (GOAL28 §4): re-judge the existing fit rows once, instead of waiting for every row to be
rebuilt by the next fetch runs.

For every open job a person's families match and that has a fit row: the JD's GenAI terms are written (fits.genai,
both people; the match's "GenAI work" part), and for a person with a jd_gate its verdict replaces the row's
dealbreaker (the regex dealbreakers are re-checked first, exactly as fits.compute does) with the new dealbreaker hash,
so the row is not rebuilt again. Counts per family before / after (visible = not filtered) are returned; with
dry_run nothing is written. Run it on a backup first (--db).
"""
from collections import Counter

from . import config, fits, genai, signals
from .db import tx, unpack
from .text import strip_boilerplate

BATCH = 500


def _families(value: str | None, mine: tuple) -> list[str]:
    tagged = {f for f in (value or "").strip(",").split(",") if f}
    return [f for f in mine if f in tagged] or ["(none)"]


def run(conn, profile: dict, dry_run: bool = False) -> dict:
    inp = fits.profile_inputs(profile)
    where, params = fits.family_filter(inp.families)
    rows = conn.execute(
        "SELECT j.uid, j.title, j.families, j.description, f.dealbreaker FROM jobs j JOIN boards b ON b.board = j.board "
        "JOIN fits f ON f.profile = ? AND f.uid = j.uid WHERE j.closed_at IS NULL AND b.active = 1 AND b.review = 0 "
        f"AND {where}", [inp.id, *params]).fetchall()
    before, after, gated, updates = Counter(), Counter(), Counter(), []
    for r in rows:
        text = unpack(r["description"])
        found = genai.terms(text)
        verdict = r["dealbreaker"]
        if inp.jd_gate is not None:
            verdict = signals.dealbreaker(f"{r['title']}\n{strip_boilerplate(text)}", list(inp.dealbreakers)) \
                or genai.gate_verdict(r["title"], r["families"], text, inp.jd_gate, inp.families, found)
        for fam in _families(r["families"], inp.families):
            before[fam] += r["dealbreaker"] is None
            after[fam] += verdict is None
            gated[fam] += genai.is_gate_verdict(verdict)
        updates.append((",".join(found), verdict, r["uid"]))
    if not dry_run:
        for i in range(0, len(updates), BATCH):
            with tx(conn):
                for found, verdict, uid in updates[i:i + BATCH]:
                    if inp.jd_gate is None:
                        conn.execute("UPDATE fits SET genai = ? WHERE profile = ? AND uid = ?", (found, inp.id, uid))
                    else:
                        conn.execute("UPDATE fits SET genai = ?, dealbreaker = ?, dealbreaker_hash = ? "
                                     "WHERE profile = ? AND uid = ?", (found, verdict, inp.dealbreaker_hash, inp.id, uid))
    return {"profile": inp.id, "rows": len(rows), "gate": inp.jd_gate, "dry_run": dry_run,
            "visible_before": dict(before), "visible_after": dict(after), "hidden_by_gate": dict(gated)}


def report(result: dict) -> str:
    lines = [f"{result['profile']}: {result['rows']} open jobs with a fit row"
             + (" (dry run, nothing written)" if result["dry_run"] else "")
             + (f"; gate {result['gate']}" if result["gate"] else "; no JD gate (GenAI terms only)")]
    for fam in sorted(set(result["visible_before"]) | set(result["visible_after"])):
        lines.append(f"  {fam:16} visible {result['visible_before'].get(fam, 0):5} -> "
                     f"{result['visible_after'].get(fam, 0):5}   hidden by the gate {result['hidden_by_gate'].get(fam, 0)}")
    return "\n".join(lines)


def main(conn, pids: list[str] | None, dry_run: bool) -> list[dict]:
    return [run(conn, config.profile(pid), dry_run) for pid in (pids or config.profile_ids())]
