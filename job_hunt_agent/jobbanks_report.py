"""GOAL35 §4.3: `job-hunt-agent jobbanks-report`, per state: found (the last run's answer), same job already on one of our
boards (left out, the company's link kept), stored, dropped and why (workterms blocks), kept, and new in the last day."""
import json
from collections import Counter, defaultdict
from datetime import timedelta

from . import workterms
from .ats import careeronestop
from .db import iso

BOARD = "careeronestop:us"
NEW_WITHIN = timedelta(days=1)


def _last_run() -> dict:
    try:
        return json.loads(careeronestop.report_path().read_text())
    except (OSError, ValueError):
        return {}


def counts(conn, now) -> dict:
    """{"at": last run day or None, "states": {XX: {found, same_as_board, stored, dropped: {why: n}, kept, new}}}."""
    last = _last_run()
    states: dict[str, dict] = defaultdict(lambda: {"found": 0, "same_as_board": 0, "stored": 0, "dropped": Counter(),
                                                   "kept": 0, "new": 0})
    for st, got in (last.get("states") or {}).items():
        states[st]["found"], states[st]["same_as_board"] = int(got.get("found", 0)), int(got.get("same_as_board", 0))
    since = iso(now - NEW_WITHIN)
    for r in conn.execute("SELECT location, work_terms, first_seen FROM jobs WHERE board = ? AND closed_at IS NULL",
                          (BOARD,)):
        row = states[careeronestop.state_of(r["location"]) or "?"]
        row["stored"] += 1
        why = workterms.verdict(r["work_terms"], workterms.RULE_W2)
        if why:
            row["dropped"][why.removeprefix(workterms.VERDICT)] += 1
            continue
        row["kept"] += 1
        row["new"] += r["first_seen"] >= since
    return {"at": last.get("at"), "states": {st: {**v, "dropped": dict(v["dropped"])} for st, v in sorted(states.items())}}


def text(report: dict) -> str:
    if not report["states"]:
        return "state job banks: nothing fetched yet (Settings > Job sources, and a CareerOneStop key)"
    lines = [f"state job banks, last run {report['at'] or 'never'}",
             f"{'state':<6}{'found':>6}{'on our boards':>14}{'stored':>8}{'dropped':>8}{'kept':>6}{'new':>5}  why dropped"]
    for st, v in report["states"].items():
        why = ", ".join(f"{k} {n}" for k, n in sorted(v["dropped"].items(), key=lambda kv: -kv[1]))
        lines.append(f"{st:<6}{v['found']:>6}{v['same_as_board']:>14}{v['stored']:>8}{sum(v['dropped'].values()):>8}"
                     f"{v['kept']:>6}{v['new']:>5}  {why}")
    return "\n".join(lines)
