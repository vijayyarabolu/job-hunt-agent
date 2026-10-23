"""GOAL29 §3: the ghost job check. A posting that is probably not real or not actively hiring is flagged, never
hidden: a badge with the reason, and Priority lowered by a few points (at most PENALTY_CAP; every applied penalty is
written to ghost_log). Every signal comes from data job-hunt-agent already has:

- reposted: the same company, near-identical title and place posted on REPOST_MIN or more other days in REPOST_DAYS
- old: first seen more than OLD_DAYS ago and still open
- evergreen: a talent pool / "future opportunities" title, or a JD saying this posting is one ("always accepting")
- agency: a staffing agency posting a role for an unnamed client
- short: a JD under SHORT_JD characters (only when a JD was read: no JD is not a signal)

scan() writes one `ghost` row per open title-matched job; the JD-derived signals are kept per content_hash, so a scan
reads only new or changed JDs. ranking.context() reads the penalties (load())."""
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone

from . import applied, signals
from .db import iso, tx, unpack
from .text import norm

REPOST_MIN, REPOST_DAYS = 3, 60
OLD_DAYS = 45
SHORT_JD = 300
PENALTY = {"reposted": 4, "old": 3, "evergreen": 4, "agency": 4, "short": 3}
PENALTY_CAP = 10
ORDER = ("reposted", "agency", "evergreen", "short", "old")          # strongest first, for the badge
SQL_BATCH = 500

# evergreen: the posting says it is a pool or an always-open req. Company blurbs ("we're always looking for new ways")
# and talent-community footers on real jobs ("join our talent community") are not (seen on the 2026-10-19 backup)
EVERGREEN_TITLE = re.compile(r"(?i)\b(talent (?:pool|community|network|pipeline)|general application|future opportunit(?:y|ies)|"
                             r"expression of interest|evergreen (?:role|posting|requisition|opening)|"
                             r"pipeline (?:role|requisition|posting))\b")      # never "AI Pipeline Engineer"
EVERGREEN = re.compile(r"(?i)\b(always accepting (?:applications|resumes|cvs|candidates)|"
                       r"(?:this|the) (?:is an? )?(?:evergreen|pipeline|talent pool|talent community|general|continuous|"
                       r"ongoing) (?:posting|position|role|requisition|req|opening|application)\b|"
                       r"(?:this|the) (?:posting|position|role|requisition|opening) is (?:an? )?(?:evergreen|ongoing|"
                       r"continuous|pipeline)\b(?! basis)|"
                       r"not (?:tied|attached|linked) to (?:a|an|any) (?:specific|current|open|active) (?:opening|position|"
                       r"role|vacancy|requisition)|(?:pool|pipeline) of (?:qualified )?candidates for (?:future|upcoming))")
AGENCY_NAME = re.compile(r"(?i)\b(staffing|recruit(?:ing|ment|ers)|talent solutions|workforce solutions|manpower|"
                         r"search (?:group|partners)|placements?|consultancy)\b")
VAGUE_CLIENT = re.compile(r"(?i)\b(?:our|my|a|the) (?:direct |esteemed |confidential |fortune[- ]\d+ |leading |"
                          r"prestigious |global |major )?client\b|\bend[- ]client\b|\bconfidential (?:client|company)\b|"
                          r"\bclient (?:is|located) (?:in|seeking)\b|\bimplementation partner\b")


def text_kinds(company: str | None, jd: str, title: str | None = None) -> dict:
    """The JD-derived signals: {kind: detail}. `jd` is the stored JD ("" = none read)."""
    out = {}
    hit = EVERGREEN_TITLE.search(title or "") or EVERGREEN.search(jd or "")
    if hit:
        out["evergreen"] = re.sub(r"\s+", " ", hit.group(1).lower())
    agency = AGENCY_NAME.search(company or "") or signals.staffing_signals(jd) >= 2
    if agency and VAGUE_CLIENT.search(jd or ""):
        out["agency"] = "unnamed client"
    body = (jd or "").strip()
    if body and len(body) < SHORT_JD:
        out["short"] = str(len(body))
    return out


def time_kinds(first_seen: str, repost_days: list, now: datetime) -> dict:
    """The date signals: `repost_days` = the OTHER days (YYYY-MM-DD) the same company and title were first seen."""
    out = {}
    if len(repost_days) >= REPOST_MIN:
        out["reposted"] = f"{len(repost_days)}|{min(repost_days)}"
    seen = _parse(first_seen)
    if seen and (_utc(now) - seen).days > OLD_DAYS:
        out["old"] = str((_utc(now) - seen).days)
    return out


def verdict(kinds: dict) -> tuple:
    """(penalty, badge text or None) for one job's signals."""
    if not kinds:
        return 0, None
    penalty = min(PENALTY_CAP, sum(PENALTY[k] for k in kinds))
    return penalty, "Possible ghost job: " + ", ".join(_words(k, kinds[k]) for k in ORDER if k in kinds)


def _words(kind: str, detail: str) -> str:
    if kind == "reposted":
        n, since = detail.split("|")
        return f"reposted {n} times since {datetime.strptime(since, '%Y-%m-%d'):%b} {int(since[8:10])}"
    if kind == "old":
        return f"first seen {detail} days ago and still open"
    if kind == "evergreen":
        return f"talent pool wording (“{detail}”)"
    if kind == "agency":
        return "staffing agency, client not named"
    return f"JD only {detail} characters"


def _parse(stamp: str | None) -> datetime | None:
    try:
        return datetime.strptime((stamp or "")[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def _utc(now: datetime) -> datetime:
    """A naive UTC moment (the DB's stamps are UTC)."""
    return now.astimezone(timezone.utc).replace(tzinfo=None) if now.tzinfo else now


def _matched_rowids(conn, open_only: bool) -> list:
    where = "families <> ','" + (" AND closed_at IS NULL" if open_only else "")
    return [r[0] for r in conn.execute(f"SELECT rowid FROM jobs INDEXED BY jobs_fam WHERE {where}")]


def _by_rowid(conn, cols: str, rowids: list):
    for i in range(0, len(rowids), SQL_BATCH):
        part = rowids[i:i + SQL_BATCH]
        yield from conn.execute(f"SELECT {cols} FROM jobs WHERE rowid IN ({','.join('?' * len(part))})", part)


def _key(company: str, title: str, location: str | None) -> tuple:
    return applied.company_key(company), applied.normalize_title(title), norm(location or "")


def repost_days(conn, now: datetime) -> dict:
    """(company, title, place) -> set of days first seen, for title-matched postings first seen in REPOST_DAYS (closed
    ones too: a repost usually replaces a closed copy). The place is part of it: a big company posts one title for
    many teams on many days, which are not reposts of each other."""
    since = iso(now - timedelta(days=REPOST_DAYS))
    groups: dict = {}
    for r in _by_rowid(conn, "company, title, location, first_seen", _matched_rowids(conn, open_only=False)):
        if r["first_seen"] >= since:
            groups.setdefault(_key(r["company"], r["title"], r["location"]), set()).add(r["first_seen"][:10])
    return groups


def scan(conn, now: datetime) -> dict:
    """Re-check every open title-matched job; returns {"checked", "read", "flagged", "by_kind": {kind: n}}."""
    groups = repost_days(conn, now)
    old = {r["uid"]: r for r in conn.execute("SELECT uid, content_hash, text_kinds, kinds, penalty FROM ghost")}
    rows = list(_by_rowid(conn, "uid, company, title, location, first_seen, content_hash",
                          _matched_rowids(conn, open_only=True)))
    jds = _jds(conn, [r["uid"] for r in rows if r["uid"] not in old or old[r["uid"]]["content_hash"] != r["content_hash"]])
    stamp = iso(now)
    counts = {"checked": len(rows), "read": len(jds), "flagged": 0, "by_kind": {k: 0 for k in ORDER}}
    with tx(conn):
        for r in rows:
            prev = old.get(r["uid"])
            text = text_kinds(r["company"], jds[r["uid"]], r["title"]) if r["uid"] in jds else json.loads(prev["text_kinds"])
            others = sorted(groups.get(_key(r["company"], r["title"], r["location"]), set()) - {r["first_seen"][:10]})
            kinds = {**text, **time_kinds(r["first_seen"], others, now)}
            penalty, reason = verdict(kinds)
            conn.execute("INSERT INTO ghost(uid, content_hash, text_kinds, kinds, reason, penalty, checked_at) "
                         "VALUES (?,?,?,?,?,?,?) ON CONFLICT(uid) DO UPDATE SET content_hash = excluded.content_hash, "
                         "text_kinds = excluded.text_kinds, kinds = excluded.kinds, reason = excluded.reason, "
                         "penalty = excluded.penalty, checked_at = excluded.checked_at",
                         (r["uid"], r["content_hash"], json.dumps(text), json.dumps(kinds), reason, penalty, stamp))
            before = (prev["penalty"], sorted(json.loads(prev["kinds"]))) if prev else (0, [])
            if (penalty, sorted(kinds)) != before:              # every change of a job's penalty is logged once
                conn.execute("INSERT INTO ghost_log(uid, penalty, kinds, reason, at) VALUES (?,?,?,?,?)",
                             (r["uid"], penalty, ",".join(k for k in ORDER if k in kinds), reason, stamp))
            if penalty:
                counts["flagged"] += 1
                for k in kinds:
                    counts["by_kind"][k] += 1
    return counts


def _jds(conn, uids: list) -> dict:
    out = {}
    for i in range(0, len(uids), SQL_BATCH):
        part = uids[i:i + SQL_BATCH]
        for r in conn.execute(f"SELECT uid, description FROM jobs WHERE uid IN ({','.join('?' * len(part))})", part):
            out[r["uid"]] = unpack(r["description"])
    return out


def load(conn) -> dict:
    """uid -> (penalty, badge text) of the flagged jobs (empty before the first scan)."""
    try:
        return {r[0]: (r[1], r[2]) for r in conn.execute("SELECT uid, penalty, reason FROM ghost WHERE penalty > 0")}
    except sqlite3.OperationalError:          # a DB from before migration 34 ranks as before
        return {}
