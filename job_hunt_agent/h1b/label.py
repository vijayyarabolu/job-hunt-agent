"""the sponsorship badge of a job. The JD's own words win over the filing history:
  no          the JD says no sponsorship / US citizens only / a clearance  -> "No sponsorship" / "Citizens only"
  sponsors    the JD says sponsorship is available, or the company filed >= 1 data / AI H-1B LCA in the last 12
              months (or >= 3 of any role)                                   -> "Sponsors · 42 in 12 mo"
  no (learned) a person who needs sponsorship got a rejection email within 3 days of applying there, at a company
              with fewer than 10 data / AI filings in 12 months (a big filer's quick rejection is about the role; it
              only shows in the evidence)                                    -> "No sponsorship (learned)"
  cap_exempt  a university / research employer that files (no lottery)      -> "Cap-exempt"
  maybe       only older filings, a few unrelated ones, or green cards only -> "Sponsored before"
  None        nothing either way: no badge.
The index (companies + learned) is read once and kept until the data is rebuilt or LEARNED_KEEP_S passes.
"""
import json
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import date

from .. import config
from . import names

LEARNED_DAYS = 3
LEARNED_KEEP_S = 300
LEARNED_OVERRULED = 10      # data / AI filings in 12 months that outweigh one quick rejection (Amazon, 2 days)
_CLEARANCE = re.compile(r"(?i)clearance|secret|ts\b|polygraph")
TEXT = {"sponsors": "Sponsors", "cap_exempt": "Cap-exempt", "maybe": "Sponsored before", "no": "No sponsorship"}
KINDS = ("sponsors", "cap_exempt", "maybe", "no")
FRIENDLY = ("sponsors", "cap_exempt", "maybe")          # §4 "Sponsor-friendly" switch group


@dataclass(frozen=True)
class Badge:
    kind: str                   # sponsors | cap_exempt | maybe | no
    text: str                   # what the badge says
    why: str                    # one line of evidence (hover / tap)
    source: str                 # jd | filings | learned
    evidence: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Index:
    companies: dict             # key -> h1b_companies row (dict)
    learned: dict               # key -> {company, profile, applied_on, rejected_at, days}
    ref: str | None             # last day of the filing data
    built: str | None
    by_first: dict = field(default_factory=dict)   # first word -> [(words, key words, row)] of big data / AI filers
    memo: dict = field(default_factory=dict)       # company key -> lookup() answer


# A board name shorter than the filer's ("Anthropic" / "Anthropic, PBC", "OpenAI" / "OpenAI OpCo", "Target" /
# "Target Enterprise"): when a company's own key has no filer, or only a small one, the biggest data / AI filer whose
# name is that name plus at most two more words stands in. Word by word, so "Apple" never meets "Applebee's".
PREFIX_MIN_LETTERS = 4
PREFIX_MAX_EXTRA = 2
PREFIX_MIN_DATA = 5
WEAK_FILINGS = 3
PREFIX_DOMINANCE = 2


def _prefix_index(companies: dict) -> dict:
    out: dict = {}
    for c in companies.values():
        if c["recent_data"] >= PREFIX_MIN_DATA:
            w = names.board_words(c["name"])
            if len(w) > 1:
                out.setdefault(w[0], []).append((w, len(names.words(c["name"])), c))
    return out


def _weak(c: dict | None) -> bool:
    return c is None or (c["recent_all"] < WEAK_FILINGS and not c["recent_data"] and c["status"] != "cap_exempt")


def lookup(idx: "Index", company: str | None) -> dict | None:
    """The filings row for a company name: its own key, else (missing or weak) the one big filer named like it plus up
    to two words. Two such filers of a size ("Archer" -> Archer Daniels Midland and Archer Aviation) is no answer. A
    small filer under its own name stays only when that name starts with the company's ("Cadence, Inc." is not
    Cadence Solutions)."""
    key = names.key(company)
    w = names.board_words(company)
    memo = (key, w)
    if memo in idx.memo:
        return idx.memo[memo]
    found = idx.companies.get(key)
    if _weak(found):
        if found and names.key(found["name"]) == key and not _starts(found["name"], w):
            found = None
        if len("".join(w)) >= PREFIX_MIN_LETTERS:
            big, tied = _longer(idx, w)
            found = big or (None if tied else found)       # "Archer": neither ADM nor a small Archer Technologies
    idx.memo[memo] = found
    return found


def _starts(name: str, w: tuple) -> bool:
    """The filer's name begins with the company's words ("Boston Analytical, Inc." / "Bostonanalytical")."""
    return "".join(names.board_words(name)).startswith("".join(w))


def _longer(idx: "Index", w: tuple) -> tuple[dict | None, bool]:
    """(the big data / AI filer named w plus one or two words, tied): the only one, or one with at least
    PREFIX_DOMINANCE times the data / AI filings of the next (FedEx Freight over FedEx Dataworks); tied when there are
    several of a size."""
    seen = {c["name"]: c for cw, size, c in idx.by_first.get(w[0], ())
            if cw[:len(w)] == w and len(cw) > len(w) and size - len(w) <= PREFIX_MAX_EXTRA}
    ranked = sorted(seen.values(), key=lambda c: (c["recent_data"], c["recent_all"]), reverse=True)
    if len(ranked) == 1 or (ranked and ranked[0]["recent_data"] >= PREFIX_DOMINANCE * ranked[1]["recent_data"]):
        return ranked[0], False
    return None, bool(ranked)


_LOCK = threading.Lock()
_CACHE: dict = {}


def _days(applied_on: str | None, rejected_at: str | None) -> int | None:
    try:
        return (date.fromisoformat(rejected_at[:10]) - date.fromisoformat(applied_on[:10])).days
    except (TypeError, ValueError):
        return None


def _learned(conn) -> dict:
    """Companies that rejected a person who needs sponsorship within LEARNED_DAYS days of applying (the Gmail
    reader's rejection, applied / confirmed / already recorded on that row)."""
    needs = [p["id"] for p in config.profiles() if p.get("needs_sponsorship")]
    if not needs:
        return {}
    marks = ",".join("?" * len(needs))
    rows = conn.execute(
        "SELECT a.company, a.profile, a.applied_on, min(m.sent_at) AS rejected_at FROM applications a "
        "JOIN mail_messages m ON m.app_id = a.id AND m.profile = a.profile "
        f"WHERE a.profile IN ({marks}) AND m.kind = 'rejected' AND m.state IN ('applied', 'confirmed', 'recorded') "
        "AND a.applied_on IS NOT NULL GROUP BY a.id ORDER BY a.applied_on", needs).fetchall()
    out = {}
    for r in rows:
        days = _days(r["applied_on"], r["rejected_at"])
        k = names.key(r["company"])
        if k and days is not None and 0 <= days <= LEARNED_DAYS and k not in out:
            out[k] = {"company": r["company"], "profile": r["profile"], "applied_on": r["applied_on"],
                      "rejected_at": r["rejected_at"][:10], "days": days}
    return out


def index(conn) -> Index:
    meta = dict(conn.execute("SELECT key, value FROM h1b_meta").fetchall())
    stamp = (str(config.DB_PATH), meta.get("built_at"))
    with _LOCK:
        hit = _CACHE.get("idx")
        if hit and hit[0] == stamp and time.monotonic() - hit[1] < LEARNED_KEEP_S:
            return hit[2]
        companies = {r["key"]: dict(r) for r in conn.execute("SELECT * FROM h1b_companies WHERE status IS NOT NULL")}
        idx = Index(companies, _learned(conn), meta.get("ref_date"), meta.get("built_at"), _prefix_index(companies))
        _CACHE["idx"] = (stamp, time.monotonic(), idx)
        return idx


def forget() -> None:
    """Drop the kept index (after a load, or a test's new data)."""
    with _LOCK:
        _CACHE.clear()


def _evidence(c: dict, ref: str | None) -> dict:
    trend = "up" if c["recent_all"] > c["prior_all"] else "down" if c["recent_all"] < c["prior_all"] else "same"
    return {"recent": c["recent_all"], "recent_data": c["recent_data"], "prior": c["prior_all"],
            "prior_data": c["prior_data"], "perm": c["perm_all"], "perm_data": c["perm_data"], "level": c["level"],
            "states": json.loads(c["states"] or "[]"), "titles": json.loads(c["titles"] or "[]"),
            "filed_as": [n for n, _ in json.loads(c["employers"] or "[]")][:3], "trend": trend,
            "cap_exempt": c["cap_exempt"], "through": ref}


def why_line(ev: dict) -> str:
    """'42 H-1B filings in the last 12 months (9 data / AI), 30 in the 12 before · typical wage level II · 12
    green-card (PERM) cases · filed as Example Corp'."""
    bits = [f"{ev['recent']:,} H-1B filing{'s' if ev['recent'] != 1 else ''} in the last 12 months "
            f"({ev['recent_data']:,} data / AI), {ev['prior']:,} in the 12 before"]
    if ev.get("level"):
        bits.append(f"typical wage level {ev['level']}")
    if ev.get("perm"):
        bits.append(f"{ev['perm']:,} green-card (PERM) case{'s' if ev['perm'] != 1 else ''}")
    if ev.get("cap_exempt"):
        bits.append(f"cap-exempt ({ev['cap_exempt']}): no lottery")
    if ev.get("filed_as"):
        bits.append("filed as " + ", ".join(ev["filed_as"]))
    return " · ".join(bits)


def _clearance(dealbreaker: str | None) -> bool:
    return bool(dealbreaker and _CLEARANCE.search(dealbreaker))


def _first_name(pid: str) -> str:
    return config.profile(pid)["name"].split()[0] if pid in config.profile_ids() else pid


def _learned_badge(learned: dict, ev: dict) -> Badge:
    days = learned["days"]
    return Badge("no", "No sponsorship (learned)",
                 f"{_first_name(learned['profile'])} applied {learned['applied_on']} and was rejected {days} day"
                 f"{'s' if days != 1 else ''} later: this company likely turns down people who need sponsorship",
                 "learned", {**ev, "learned": learned})


def badge(row: dict, idx: Index, dealbreaker: str | None = None, quote: str | None = None) -> Badge | None:
    """The job's badge (None: nothing known either way). `quote` is the JD sentence when the caller has the JD."""
    jd = row.get("sponsorship")
    key = names.key(row.get("company"))
    c = lookup(idx, row.get("company"))
    ev = {**(_evidence(c, idx.ref) if c else {}), "jd": quote}
    if jd == "citizen" or _clearance(dealbreaker):
        why = quote or ("The post says US citizens only" if jd == "citizen" else "The post asks for a security clearance")
        return Badge("no", "Citizens only", why, "jd", ev)
    if jd == "no":
        return Badge("no", TEXT["no"], quote or "The post says it does not sponsor visas", "jd", ev)
    if jd == "yes":
        count = f" · {c['recent_all']:,} in 12 mo" if c and c["recent_all"] else ""
        return Badge("sponsors", TEXT["sponsors"] + count, quote or "The post says sponsorship is available", "jd", ev)
    learned = idx.learned.get(key)
    if learned and not (c and c["recent_data"] >= LEARNED_OVERRULED):
        return _learned_badge(learned, ev)
    if learned:
        ev = {**ev, "learned": learned}
    if not c:
        return None
    why = why_line(ev)
    if c["status"] == "cap_exempt":
        return Badge("cap_exempt", TEXT["cap_exempt"], why, "filings", ev)
    if c["status"] == "sponsors":
        return Badge("sponsors", f"{TEXT['sponsors']} · {c['recent_all']:,} in 12 mo", why, "filings", ev)
    return Badge("maybe", TEXT["maybe"], why, "filings", ev)


def tier(b: Badge | None) -> int:
    """§4 ranking for people who need sponsorship: Sponsors / Cap-exempt 0, Unknown 1, Maybe 2, No sponsorship 3."""
    if b is None:
        return 1
    return {"sponsors": 0, "cap_exempt": 0, "maybe": 2}.get(b.kind, 3)


def group(b: Badge | None) -> str:
    """§4 switch group: friendly (Sponsors, Cap-exempt, Sponsored before) | no | unknown."""
    if b is None:
        return "unknown"
    return "friendly" if b.kind in FRIENDLY else "no"
