"""`job-hunt-agent jd-backfill`: give every existing application its JD snapshot, reading the posting while it is
still live for rows that only have a link, and marking "posting gone" when it is gone. Rows the app made whose JD
cell held only the link (and any cell in the old "URL + JD" style) get the linked role title, the text going to the
hidden JD Text column (jdsnap.fill_cell, user decision 2026-10-06 evening); the sync writes both.

One polite request per posting (net.py: robots gate, per-host pacing, retries); rows already saved are skipped, so
the command can be run again at any time and only retries what is still missing.
"""
from collections import Counter

from . import jdsnap

ROW_FIELDS = "id, profile, company, role_title, date_applied, status, jd, job_url, job_uid"


def _rows(conn, pids: list[str]) -> list[dict]:
    marks = ",".join("?" * len(pids))
    return [dict(r) for r in conn.execute(f"SELECT {ROW_FIELDS} FROM applications WHERE profile IN ({marks}) "
                                          "ORDER BY profile, id", pids)]


def classify(conn, app: dict) -> str:
    """What the backfill will do with a row before any request: 'have', 'feed', 'sheet', 'fetch', 'no_url'."""
    snap = jdsnap.get(conn, app["id"])
    if snap and snap["state"] == "saved" and snap["text"]:
        return "have"
    if jdsnap._feed_text(conn, app)[0]:
        return "feed"
    url = jdsnap.posting_url(app)
    if jdsnap._cell_text(app, url):
        return "sheet"
    return "fetch" if url else "no_url"


def plan(conn, pids: list[str]) -> dict:
    """Counts per action and per person, no network."""
    counts: dict[str, Counter] = {}
    for app in _rows(conn, pids):
        counts.setdefault(app["profile"], Counter())[classify(conn, app)] += 1
    return {"by_profile": {p: dict(c) for p, c in counts.items()}}


def _brief(app: dict, snap: dict) -> dict:
    return {"id": app["id"], "profile": app["profile"], "company": app["company"], "role": app["role_title"],
            "date": app["date_applied"], "url": snap.get("url"), "detail": snap.get("detail")}


def run(conn, pids: list[str], fetch: bool = True, fill: bool = True, limit: int | None = None) -> dict:
    """Snapshot every row (fetching up to `limit` postings); returns counts and the rows to show the person."""
    counts: dict[str, Counter] = {}
    lists: dict[str, list] = {"gone": [], "unreadable": [], "no_url": [], "filled": [], "fetched": []}
    fetched = 0
    for app in _rows(conn, pids):
        before = classify(conn, app)
        allow = fetch and before == "fetch" and (limit is None or fetched < limit)
        snap = jdsnap.snapshot(conn, app["id"], fetch=allow)
        fetched += int(allow)
        state = snap.get("state")
        key = f"{state}:{snap.get('source')}" if state == "saved" else state
        counts.setdefault(app["profile"], Counter())[key] += 1
        if state in ("gone", "unreadable"):
            lists[state].append(_brief(app, snap))
        elif allow:
            lists["fetched"].append(_brief(app, snap))
        if not snap.get("url"):                                  # no posting link anywhere: listed, never invented
            lists["no_url"].append(_brief(app, snap))
        if fill and jdsnap.fill_cell(conn, app["id"]):
            lists["filled"].append(_brief(app, snap))
    return {"counts": {p: dict(c) for p, c in counts.items()}, **lists, "fetch_requests": fetched}


def report_md(result: dict, when: str) -> str:
    """The backfill report: how many saved (and from where), how many "posting gone", per person."""
    lines = [f"# JD backfill ({when})", "", "| Person | Saved (feed) | Saved (sheet text) | Saved (fetched) | "
             "Posting gone | Not readable | No JD text, no link | Total | No posting link |",
             "|---|---|---|---|---|---|---|---|---|"]
    for pid, c in sorted(result["counts"].items()):
        no_link = sum(1 for r in result["no_url"] if r["profile"] == pid)
        lines.append(f"| {pid} | {c.get('saved:feed', 0)} | {c.get('saved:sheet', 0)} | {c.get('saved:fetch', 0)} | "
                     f"{c.get('gone', 0)} | {c.get('unreadable', 0)} | {c.get('no_url', 0)} | {sum(c.values())} | "
                     f"{no_link} |")
    lines += ["", f"Postings fetched this run: {result['fetch_requests']}. JD cells set to the linked title "
              f"with the text in JD Text: {len(result['filled'])}. Rows with no posting link "
              "anywhere are listed separately (local file), never invented.", ""]
    for name, title in (("gone", "Posting gone"), ("unreadable", "Not readable (left as they are)"),
                        ("fetched", "Fetched"), ("filled", "JD cell filled")):
        rows = result[name]
        lines.append(f"## {title} ({len(rows)})")
        lines += [f"- {r['profile']} #{r['id']} {r['company']} · {r['role']} ({r['date'] or 'no date'}): "
                  f"{r['detail'] or r['url'] or ''}" for r in rows] or ["- none"]
        lines.append("")
    return "\n".join(lines)


def no_url_md(result: dict) -> str:
    """Rows with no posting link anywhere (user: "don't invent one; list them for me")."""
    rows = result["no_url"]
    lines = [f"# Rows with no posting link ({len(rows)})", "",
             "The app has no job URL for these rows and their JD cell has no link. Nothing was invented.", ""]
    lines += [f"- {r['profile']} #{r['id']} {r['company']} · {r['role']} ({r['date'] or 'no date'})" for r in rows]
    return "\n".join(lines) + "\n"
