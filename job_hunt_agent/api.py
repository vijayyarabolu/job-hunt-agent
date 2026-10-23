"""Web UI handlers (GOAL §8): plain functions over a sqlite connection returning JSON-ready dicts.

Tabs: recommended · saved · applied (+ follow-ups due) · interviewing · archived; the v1 tabs fresh · all ·
too_senior · followups still answer. Every score comes from ranking.rank() (the one read path); the match breakdown,
labels and bullets come from match.py, the filters from filters.py.
"""
import logging
import sqlite3
import threading
from collections import Counter
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from . import (applied, clones, config, dupes, feedback, feedcache, filters, fits, fresh, genai, gsync, health, jd_doc, jdsnap, match,
               outside, profile_settings, ranking, removal, resumes, rgclient, score, signals, tailored, tracker,
               vendor_model)
from .db import iso, tx, unpack
from .h1b import feed as h1b_feed

log = logging.getLogger(__name__)

TABS = ("recommended", "fresh", "all", "saved", "applied", "interviewing", "archived", "followups", "too_senior")
APP_TABS = {"applied": ("applied",), "interviewing": ("screen", "interview", "offer")}
ARCHIVED_STATUSES = ("rejected", "ghosted")
PRE_APPLY = ("saved", "tailored")             # 'tailored' only on rows from before GOAL3
FEEDBACK_TRIAGE = {"like": "saved", "pass": "dismissed", "clear": "restored"}
MAX_CARDS = 300
RESUME_CARDS = 40       # the first rows carry their resume scores (the pane paints them at once); the rest
                        # get them from /api/job when opened (~0.5 ms and ~1 KB per card saved)
DEFAULT_FEED_MIN_MATCH = 0                  # GOAL4 §0 floor (profile feed_min_match); resumegen GOAL3 §8: default 0,
                                            # every matched job shows; Settings → Minimum match raises it
MAX_COMPANY_ROLES = 5
INDUSTRY_CACHE_MAX = 50_000
_INDUSTRY: dict[str, str | None] = {}       # uid -> detected industry (JDs rarely change industry; cleared when full)
_INDUSTRY_LOCK = threading.Lock()
MAX_EARLIER = 5
TRIAGE_STATES = ("saved", "dismissed", "restored")
FEED_STATES = ("ok", "pending", "filtered", "too_senior")
CONTACT_FIELDS = ("target_name", "target_role", "linkedin_request", "email_address", "email_sent", "follow_up_1",
                  "follow_up_2", "status_ar")


def profiles() -> list[dict]:
    default = config.default_profile()
    return [{"id": p["id"], "name": p["name"], "has_baseline": fits.profile_inputs(p).has_baseline,
             "default": p["id"] == default} for p in config.profiles()]


def _triage(conn, pid: str) -> dict[str, str]:
    return {r["uid"]: r["state"] for r in conn.execute("SELECT uid, state FROM triage_now WHERE profile = ?", (pid,))}


def _apps_by_uid(conn, pid: str) -> dict[str, dict]:
    rows = conn.execute("SELECT id, job_uid, status, applied_on FROM applications "
                        "WHERE profile = ? AND job_uid IS NOT NULL", (pid,))
    return {r["job_uid"]: dict(r) for r in rows}


def _industries(conn, rows: list[dict]) -> dict[str, str | None]:
    """uid -> the JD's industry (match.industry_of), cached in-process so a feed request decompresses each JD once.
    One batch at a time: a page asked during the warm-up's fill takes the JDs it already read."""
    need = [r["uid"] for r in rows if r["uid"] not in _INDUSTRY]
    if len(_INDUSTRY) + len(need) > INDUSTRY_CACHE_MAX:
        _INDUSTRY.clear()
    for i in range(0, len(need), ranking.SQL_BATCH):
        with _INDUSTRY_LOCK:
            part = [uid for uid in need[i:i + ranking.SQL_BATCH] if uid not in _INDUSTRY]
            found = conn.execute(f"SELECT uid, company, description FROM jobs WHERE uid IN "
                                 f"({','.join('?' * len(part))})", part) if part else []
            for r in found:
                _INDUSTRY[r["uid"]] = match.industry_of(r["company"], unpack(r["description"]))
    return {r["uid"]: _INDUSTRY.get(r["uid"]) for r in rows}


def _who(profile: dict) -> match.Who:
    inp = fits.profile_inputs(profile)
    return match.who_of(profile, fits.known_skills(profile["id"]), inp.has_baseline, inp.resumes)


def _flags(s) -> list[str]:
    """The score's penalty / feedback chips and pipeline state: what the labels and breakdown do not already say."""
    pipeline = [chip for chip in s.chips if chip in score.PIPELINE_CHIPS.values()]
    stale = ["Unverified 7d+"] if "Unverified 7d+" in s.chips else []
    return pipeline + list(s.mults) + stale


def card(row: dict, s, triage_state: str | None = None, app: dict | None = None, now: datetime | None = None,
         who: match.Who | None = None, industry: str | None = None, liked: bool = False, memo: dict | None = None,
         with_resumes: bool = True, tailored_score: dict | None = None, sponsor: dict | None = None) -> dict:
    out = {"uid": row["uid"], "sponsor": sponsor, "title": row["title"], "company": row["company"], "location": row["location"],
           "workplace": row.get("workplace"), "url": row["url"], "apply_url": row.get("apply_url") or row["url"],
           "salary": [row.get("salary_min"), row.get("salary_max")], "worth": s.worth if s.scored else None,
           "band": s.band, "employment": s.employment, "size": row.get("segment"),
           "employment_label": signals.EMPLOYMENT_LABELS.get(s.employment) if s.employment else None,
           "chips": list(s.chips), "state": s.state, "triage": triage_state, "app": app, "liked": liked,
           "asks_years": row.get("min_years"),                  # "Asks N yrs" / "years not stated"
           "req_id": row.get("req_id"),                         # copied with Apply
           "ghost": getattr(s, "ghost", None),                  # "Possible ghost job: ..." (never hidden)
           "also": [{"uid": uid, "location": loc} for uid, loc in row.get("also", ())]}
    moment, exact = fresh.feed_moment(row)
    out = {**out, "posted_ts": iso(moment) if moment else None, "posted_exact": exact, "posted_label": fresh.age_word(row),
           "posted_found": fresh.found_kind(row), "posted_day": (row.get("first_posted_at") or "")[:10] or None}
    if who is None or now is None:
        return out
    m, view = _match_view(row, who, industry, memo, with_resumes)
    view = {**view, **tailored.view(tailored_score, m["pct"])}     # the ring follows a ready tailored resume
    return {**out, **view, "labels": match.labels(row, s, now, who), "fit": match.good_fit(row, s, m["parts"], now, who),
            "gaps": match.gaps(row, s, m["parts"]), "flags": _flags(s), "industry": industry}


_NOT_YET = object()


def _memo_breakdown(row: dict, who: match.Who, industry, memo: dict | None, with_resumes: bool = False):
    """(match breakdown, resume view or _NOT_YET) of a job, memoized with the kept ranking (feedcache): the costly
    part of a card and of the floor / Best match filters (~0.2 + 0.5 ms a job). Only the breakdown and the
    resume view are kept; a card builds its small dicts from them each time."""
    memo = {} if memo is None else memo
    m, res = memo.get(row["uid"]) or (match.breakdown(row, who, industry), _NOT_YET)
    if with_resumes and res is _NOT_YET:
        res = resume_view(row, who)
    memo[row["uid"]] = (m, res)
    return m, res


def _match_view(row: dict, who: match.Who, industry, memo: dict | None, with_resumes: bool) -> tuple[dict, dict]:
    m, res = _memo_breakdown(row, who, industry, memo, with_resumes)
    view = {"match": m["pct"], "parts": [p.as_dict() for p in m["parts"]]}
    return m, ({**view, "resumes": res} if with_resumes else view)


def _scored(row: dict, who: match.Who) -> vendor_model.Result | None:
    """Every resume under the job's ATS vendor model; None without resumes or JD text (keywords may be empty)."""
    if not who.has_baseline or not who.resumes or row.get("fit_terms") is None \
            or row.get("jd_state") in ("none", "unreadable"):
        return None
    return vendor_model.score(row, who.resumes, who.years)


def vendor_result(row: dict, who: match.Who) -> vendor_model.Result | None:
    """GOAL5 §3 (decision 010): every resume scored under the job's ATS vendor model; None without resumes, JD text
    or a tracked keyword in it."""
    result = _scored(row, who)
    return result if result is not None and result.terms else None


def resume_view(row: dict, who: match.Who, full: bool = False) -> dict | None:
    """The best resume under the job's vendor model (card: "Best: ML Engineer · Greenhouse est. 73%"), the
    title-mapped one when another scores higher, and every resume's vendor %, generic %, grade, matched and missing
    counts (with the vendor-form keyword lists and parsing checks when `full`, for the detail view)."""
    result = _scored(row, who)
    if result is None:
        return None
    if not result.terms:                                    # JD text with no tracked skill: nothing to estimate
        return {"vendor": result.vendor, "vendor_label": result.vendor_label, "label": result.label,
                "modeled": False, "empty": True, "best": None, "title": None, "all": [],
                "short": f"no tracked skill keywords in this JD: no {result.vendor_label} estimate",
                "how": f"/how/ats?vendor={result.vendor}"}
    pending = result.best is None                           # built before decision 010: until the rebuild
    scores = [s if s.pct is not None else replace(s, pct=s.generic) for s in result.scores]
    top = max(scores, key=lambda s: (s.pct, s.generic)) if pending else result.best
    title_id = next((r.id for f in result.job_families for r in who.resumes if f in r.families), None)
    title = next((s for s in scores if s.id == title_id), None)

    def brief(s):
        return {"id": s.id, "label": s.label, "pct": s.pct, "generic": s.generic, "grade": s.grade,
                "n_matched": len(s.matched), "n_missing": len(s.missing)}
    return {"vendor": result.vendor, "vendor_label": result.vendor_label, "label": result.label,
            "modeled": result.modeled and not pending, "short": result.short(top.pct) if not pending
            else f"keyword {top.pct}% (vendor estimate after the next rebuild)",
            "how": f"/how/ats?vendor={result.vendor}", "best": brief(top),
            "title": brief(title) if title and title.id != top.id else None,
            "all": [s.as_dict() if full else brief(s) for s in scores]}


def _applications(conn, pid: str, statuses: tuple[str, ...]) -> list[dict]:
    rows = conn.execute("SELECT id, job_uid, company, role_title, date_applied, applied_on, status, folder, resume, address "
                        f"FROM applications WHERE profile = ? AND status IN ({','.join('?' * len(statuses))}) "
                        "ORDER BY coalesce(applied_on, '') DESC, id DESC", (pid, *statuses))
    return [dict(r) for r in rows]


def _is_applied(app: dict | None, labels_applied: str | None) -> bool:
    return bool(labels_applied) or bool(app and app.get("status") not in PRE_APPLY)


def _spread(by_uid: dict, group_of: dict) -> dict:
    """GOAL32: a copy of a role with nothing of its own takes the role's (Not interested, saved or applied on one
    city covers every city, and a city posted later); Not interested wins over the rest."""
    of_role: dict = {}
    for uid, value in by_uid.items():
        head = group_of.get(uid)
        if head and (head not in of_role or value == "dismissed"):
            of_role[head] = value
    return {**{uid: of_role[head] for uid, head in group_of.items() if head in of_role}, **by_uid}


def _pool(tab: str, ranked: list, states: dict, apps: dict) -> list:
    if tab == "too_senior":
        return [(r, s) for r, s in ranked if s.state == "too_senior"]
    if tab == "saved":
        return [(r, s) for r, s in ranked if s.state != "filtered" and (states.get(r["uid"]) == "saved"
                or apps.get(r["uid"], {}).get("status") in PRE_APPLY)]
    if tab == "archived":
        return [(r, s) for r, s in ranked if states.get(r["uid"]) == "dismissed"]
    return [(r, s) for r, s in ranked if s.state != "filtered" and states.get(r["uid"]) != "dismissed"]


def jobs(conn, pid: str, tab: str, now: datetime, flt: filters.Filters | None = None, wait: bool = False) -> dict:
    """One tab of the feed. Job tabs are ranked, filtered (filters.keep) and collapsed; applied / interviewing are
    the tracker's applications; archived is dismissed jobs plus rejected / ghosted applications."""
    if tab not in TABS:
        raise ValueError(f"tab must be one of {TABS}")
    flt = flt or filters.Filters()
    if tab in APP_TABS:
        return {"tab": tab, "applications": _applications(conn, pid, APP_TABS[tab]),
                "sheet_url": gsync.sheet_url(conn, pid),
                "followups_due": len(tracker.followups_due(conn, pid)) if tab == "applied" else None}
    if tab == "followups":
        return {"tab": tab, "applications": tracker.followups_due(conn, pid)}
    profile = config.profile(pid)
    flt = replace(flt, show_senior=True) if tab == "too_senior" else flt
    states, apps = _triage(conn, pid), _apps_by_uid(conn, pid)
    latest = feedback.latest(conn, pid)
    likes = {uid for uid, f in latest.items() if f["kind"] == "like"}
    kept = feedcache.ranked(conn, profile, now, tab == "fresh", FEED_STATES, wait)
    ranked = removal.keep(conn, pid, kept.ranked, lambda pair: pair[0])        # removed for good
    group_of = feedcache.groups(conn, kept)                    # one role posted once per city is one card
    states, apps = _spread(states, group_of), _spread(apps, group_of)
    pool = _pool(tab, ranked, states, apps)
    floor = feed_floor(profile) if tab == "recommended" else 0
    flt = replace(flt, min_match=max(flt.min_match, floor))
    needs_pct = bool(flt.min_match or flt.sort == "match")     # else only the cards shown need an industry
    who, inds = _who(profile), _industries(conn, [r for r, _ in pool]) if needs_pct else {}
    keep, pcts, scores = [], {}, tailored.load(conn, pid)
    for r, s in pool:
        pct = _pct(kept.views, r, who, inds[r["uid"]], scores) if needs_pct else None
        labels_applied = match.labels(r, s, now, who)["applied_before"] if flt.hide_applied else None
        if filters.keep(flt, r, s.state, s.employment, _is_applied(apps.get(r["uid"]), labels_applied), pct, now):
            keep.append((r, s))
            pcts[r["uid"]] = pct
    keep = ranking.fold(order(keep, flt.sort, pcts), group_of, clones.preferences(profile))
    needs = bool(profile.get("needs_sponsorship"))
    found = h1b_feed.badges(conn, keep) if needs else {}                     # every job's badge
    if needs:
        keep = h1b_feed.order(keep, flt.sort, pcts, found)                    # §4: sponsors first, No sponsorship last
    listed = [r for r, _ in keep]                              # the whole list: a duplicate's newer twin is in it
    if flt.dupes_only:                                         # GOAL21 W9: the head's "N duplicates" shows them
        twin_of = dupes.jobs(listed)
        keep = [(r, s) for r, s in keep if r["uid"] in twin_of]
    boards = board_counts(conn, keep)
    if flt.boards:                             # with a board picked, page through every match
        keep = [(r, s) for r, s in keep if filters.board_of(r) in flt.boards]
    spons = h1b_feed.counts(keep, found) if needs and tab == "recommended" else None
    if spons:                                                  # §4 switch: Sponsor-friendly / No sponsorship / All
        keep = h1b_feed.only(keep, found, flt.spons)
    start = flt.offset                                         # §5: "Load more" pages through every job, no cap
    page = keep[start:start + MAX_CARDS]
    today = daily_count(ranked, states, profile, now) if tab == "recommended" else None
    inds = inds or _industries(conn, [r for r, _ in page])
    views = h1b_feed.card_views(conn, [r for r, _ in page], found)
    cards = [card(r, s, states.get(r["uid"]), apps.get(r["uid"]), now, who, inds[r["uid"]], r["uid"] in likes, kept.views,
                  with_resumes=i < RESUME_CARDS, tailored_score=scores.get(r["uid"]), sponsor=views.get(r["uid"]))
             for i, (r, s) in enumerate(page, start)]
    twins = _mark_dupes(cards, listed if flt.dupes_only else [r for r, _ in keep])
    out = {"tab": tab, "jobs": cards, "total": len(keep), "offset": start, "dupes": twins, "boards": boards, "sort": flt.sort, "min_match_floor": floor, "filtered": sum(1 for _, s in ranked if s.state == "filtered" and not genai.is_gate_verdict(s.dealbreaker)),
           "jd_gate": sum(1 for _, s in ranked if s.state == "filtered" and genai.is_gate_verdict(s.dealbreaker)),
           "too_senior": sum(1 for _, s in ranked if s.state == "too_senior"), "today": today,
           "spons": spons, "spons_view": flt.spons if spons else None}
    if tab == "archived":
        out["applications"] = _applications(conn, pid, ARCHIVED_STATUSES)
        for c in cards:                                        # the reason shown on the Archived row
            c["pass_reason"] = feedback.reason_text(latest.get(c["uid"]))
    if tab == "saved":                                         # outside jobs (pasted JDs in resumegen)
        out["outside"] = outside.saved_rows(conn, pid)
    if tab == "recommended" and pid in config.profile_ids():  # in the first paint of the head
        out["progress"] = progress(conn, pid, now)
    return out


def _mark_dupes(cards: list[dict], rows: list[dict]) -> int:
    """a card that is a duplicate of a newer one in the same list says which ("Duplicate of …"). Returns
    how many duplicates the whole list has."""
    found = dupes.jobs(rows)
    by_uid = {r["uid"]: r for r in rows}
    for c in cards:
        newer = by_uid.get(found.get(c["uid"]))
        if newer is not None:
            c["dup_of"] = {"uid": newer["uid"], "label": dupes.label(newer["company"], newer["title"])}
    return len(found)


def board_counts(conn, kept: list) -> list[dict]:
    """every board system in the DB (a new one shows up by itself) with its jobs under the other filters."""
    n = Counter(filters.board_of(r) for r, _ in kept)
    found = {r[0] for r in conn.execute("SELECT DISTINCT substr(board, 1, instr(board, ':') - 1) FROM boards")}
    return sorted(({"id": p, "label": filters.board_label(p), "n": n[p]} for p in found | set(n) if p),
                  key=lambda b: b["label"].lower())


def today(now: datetime) -> str:
    return now.astimezone().date().isoformat()


def progress(conn, pid: str, now: datetime) -> dict:
    """{'applied_today', 'goal'} for the person (goal 0 = not shown)."""
    n = conn.execute("SELECT count(*) FROM applications WHERE profile = ? AND applied_on = ? AND status NOT IN (?, ?)",
                     (pid, today(now), *PRE_APPLY)).fetchone()[0]
    return {"applied_today": n, "goal": profile_settings.apply_get(pid)[profile_settings.GOAL_KEY]}


def cards_for(conn, pid: str, uids: list[str], now: datetime) -> dict[str, dict]:
    """feed cards for exactly these jobs (closed postings too, with 'closed'), keyed by uid."""
    if not uids:
        return {}
    profile = config.profile(pid)
    ranked = ranking.rank(conn, profile, now, states=("ok", "pending", "filtered", "too_senior", "non_us"),
                          uids=list(uids))
    states, apps, scores = _triage(conn, pid), _apps_by_uid(conn, pid), tailored.load(conn, pid)
    likes = {uid for uid, f in feedback.latest(conn, pid).items() if f["kind"] == "like"}
    who, inds = _who(profile), _industries(conn, [r for r, _ in ranked])
    found = h1b_feed.badges(conn, ranked) if profile.get("needs_sponsorship") else {}        # GOAL24 §3
    views = h1b_feed.card_views(conn, [r for r, _ in ranked], found)
    return {r["uid"]: {**card(r, s, states.get(r["uid"]), apps.get(r["uid"]), now, who, inds[r["uid"]],
                              r["uid"] in likes, {}, tailored_score=scores.get(r["uid"]), sponsor=views.get(r["uid"])),
                       "closed": bool(r["closed_at"])} for r, s in ranked}


def _pct(memo: dict, row: dict, who, industry, scores: dict | None = None) -> int | None:
    """Match % of a job, memoized with the kept ranking (feedcache): same person, same job, same answer. Shares the
    cards' memo, so the breakdown the floor / Best match needs is never computed twice. A job with a ready
    tailored resume uses its tailored % (that job only)."""
    if scores and row["uid"] in scores:
        return scores[row["uid"]]["pct"]
    return _memo_breakdown(row, who, industry, memo)[0]["pct"]


_TAILORING: dict[str, threading.Lock] = {}


def sync_tailored(conn, pid: str, jobs: list, now: datetime) -> int:
    """resumegen GOAL3 §4: score the jobs whose ready tailored resume is new (called with resumegen's list on each
    resume-status poll). A scoring fault is logged and leaves the stored scores as they were; Offline propagates.
    A poll that comes while this person's scores are being made (the phone and the laptop) leaves it to that one."""
    if pid not in config.profile_ids():
        return 0
    lock = _TAILORING.setdefault(pid, threading.Lock())
    if not lock.acquire(blocking=False):
        return 0
    profile = config.profile(pid)
    try:
        return tailored.sync(conn, profile, jobs, _who(profile), lambda rows: _industries(conn, rows), now)
    except rgclient.Offline:
        raise
    except Exception:                                  # never lose the resume chips over a score
        log.exception("tailored score sync failed for %s", pid)
        return 0
    finally:
        lock.release()


def feed_floor(profile: dict) -> int:
    return int(profile.get("feed_min_match", DEFAULT_FEED_MIN_MATCH))


def order(pairs: list, sort: str, pcts: dict[str, int | None]) -> list:
    """'newest' = feed moment (exact posted time / first_seen) newest first, undated last; 'match' = match %
    best first. Ties keep the worth order rank() produced (sorted() is stable)."""
    if sort == "match":
        return sorted(pairs, key=lambda p: -(pcts.get(p[0]["uid"]) or -1))
    stamps = {r["uid"]: fresh.feed_moment(r)[0] for r, _ in pairs}
    return sorted(pairs, key=lambda p: (stamps[p[0]["uid"]] is None,
                                        -(stamps[p[0]["uid"]].timestamp() if stamps[p[0]["uid"]] else 0)))


def daily_count(ranked: list, states: dict, profile: dict, now: datetime) -> dict:
    """GOAL3 banner: matches first posted in the last 24 hours (shown in the feed, not dismissed), by role family.
    GOAL21 W7: was "since local midnight", which said "0 new matches today" at 2 AM next to 54 jobs under a day old."""
    since = iso(now - timedelta(hours=24))
    by_family: dict[str, int] = {}
    total = 0
    for r, s in ranked:
        if s.state != "ok" or r.get("seed") or states.get(r["uid"]) == "dismissed" \
                or (r.get("first_posted_at") or "") < since:
            continue
        total += 1
        fam = next((f for f in profile["families"] if f",{f}," in (r.get("families") or "")), "other")
        by_family[fam] = by_family.get(fam, 0) + 1
    return {"total": total, "by_family": dict(sorted(by_family.items(), key=lambda kv: -kv[1])), "since": since}


def job_detail(conn, pid: str, uid: str, now: datetime | None = None) -> dict:
    row = conn.execute("SELECT uid, company, title, location, url, apply_url, description FROM jobs WHERE uid = ?",
                       (uid,)).fetchone()
    if row is None:
        raise KeyError(uid)
    detail = {k: row[k] for k in ("uid", "company", "title", "location", "url", "apply_url")}
    detail["jd"] = unpack(row["description"])
    app = conn.execute("SELECT * FROM applications WHERE profile = ? AND job_uid = ?", (pid, uid)).fetchone()
    if app:
        detail["application"] = {k: app[k] for k in ("id", "status", "applied_on", *CONTACT_FIELDS)}
        detail["application"]["sheet_url"] = gsync.sheet_url(conn, pid)
        detail["application"]["resume"] = app["resume"] or (conn.execute(               # × / Replace ▾
            "SELECT name FROM resume_uploads WHERE app_id = ? AND removed_at IS NULL ORDER BY version DESC",
            (app["id"],)).fetchone() or {"name": None})["name"]
    detail["earlier"] = _earlier(conn, pid, row["company"], app["id"] if app else None)
    if pid not in config.profile_ids():                     # tests / a profile folder that is gone: plain detail
        return detail
    profile, now = config.profile(pid), now or datetime.now(timezone.utc)
    ranked = ranking.rank(conn, profile, now, states=("ok", "pending", "filtered", "too_senior", "non_us"), uid=uid)
    if ranked:
        r, s = ranked[0]
        who = _who(profile)
        found = h1b_feed.badges(conn, ranked) if profile.get("needs_sponsorship") else {}    # GOAL24 §3
        quote = signals.sponsorship_quote(detail["jd"]) if r.get("sponsorship") else None
        detail["card"] = card(r, s, _triage(conn, pid).get(uid), None, now, who, _industries(conn, [r])[uid],
                              tailored_score=tailored.load(conn, pid).get(uid),
                              sponsor=h1b_feed.view(found.get(uid), quote))
        detail["card"]["resumes"] = resume_view(r, who, full=True)
    detail["company_panel"] = company_panel(conn, profile, row["company"], uid, now)
    detail["offer"] = offer_panel(conn, pid, uid, detail.get("card"), now)
    return detail


def offer_panel(conn, pid: str, uid: str, job_card: dict | None, now: datetime, run_llm: bool = False) -> dict | None:
    """GOAL29 §2: the Offer check of a job (the free areas at once; Claude's three when run_llm). Never fails the
    page: a problem leaves the panel out."""
    from . import offer
    try:
        return offer.check(conn, pid, uid, (job_card or {}).get("match"), (job_card or {}).get("sponsor"), now,
                           run_llm=run_llm)
    except (sqlite3.Error, KeyError, ValueError, OSError, config.ConfigError) as err:
        log.warning("offer check for %s / %s failed: %s", pid, uid, err)
        return None


def warm(now: datetime, first: bool = False) -> None:
    """Rank every profile's feed now (and fill the industry cache): after a restart, and after a run or an edit
    before the tabs are told (live.watch), so the page then gets the fresh answer at once. The first one after a
    restart also opens Applied and Archived once (GOAL33 §4: the H-1B index, the ATS profiles and the vendor models
    load on first use, 1.2 s on the first Applied)."""
    from . import db
    conn = db.connect()
    try:
        for pid in config.profile_ids():
            jobs(conn, pid, "recommended", now, wait=True)
        for pid in config.profile_ids():        # then every matched JD's industry, for Best match and a floor
            profile = config.profile(pid)
            _industries(conn, [r for r, _ in feedcache.ranked(conn, profile, now, False, FEED_STATES).ranked])
        health.summary(conn, now)               # the header's first ask after a restart answers at once
        from . import stats                     # Stats opens at once too (its slow part is kept per run)
        for pid in config.profile_ids():
            stats.report(conn, pid)
        if first:
            from . import appliedview
            for pid in config.profile_ids():
                appliedview.board(conn, pid, "all", None, now)
                jobs(conn, pid, "archived", now)
    finally:
        conn.close()


def company_panel(conn, profile: dict, company: str, uid: str, now: datetime) -> dict:
    """Open matching roles at the company and hiring momentum. Industry / size / funding are left out: we have no
    verified public source for them, so the UI hides them rather than guess."""
    where, params = fits.family_filter(tuple(profile["families"]))
    rows = conn.execute("SELECT j.uid, j.title, j.location, j.first_posted_at, j.seed FROM jobs j "
                        "JOIN boards b ON b.board = j.board WHERE j.company = ? AND j.closed_at IS NULL "
                        f"AND b.active = 1 AND {where} ORDER BY coalesce(j.first_posted_at, j.first_seen) DESC",
                        [company, *params]).fetchall()
    week_ago = iso(now - ranking.MOMENTUM_WEEK)
    new_week = sum(1 for r in rows if not r["seed"] and (r["first_posted_at"] or "") >= week_ago)
    trend = ranking.momentum(conn, tuple(profile["families"]), now, (company,)).get(company)
    return {"open_roles": len(rows), "new_this_week": new_week,
            "prior_4_weeks": trend[1] if trend else None,
            "roles": [{"uid": r["uid"], "title": r["title"], "location": r["location"]}
                      for r in rows if r["uid"] != uid][:MAX_COMPANY_ROLES]}


def _earlier(conn, pid: str, company: str, skip_id: int | None) -> list[dict]:
    """Other applications at this company (tracker rows included), newest first, with their non-empty contacts."""
    key = applied.company_key(company)
    rows = conn.execute(f"SELECT id, company, role_title, applied_on, status, {', '.join(CONTACT_FIELDS)} "
                        "FROM applications WHERE profile = ?", (pid,)).fetchall()
    same = [r for r in rows if r["id"] != skip_id and applied.same_company(key, applied.company_key(r["company"]))]
    same.sort(key=lambda r: r["applied_on"] or "", reverse=True)
    return [{"company": r["company"], "role_title": r["role_title"], "applied_on": r["applied_on"],
             "status": r["status"], "contacts": {k: r[k] for k in CONTACT_FIELDS if r[k] not in (None, "")}}
            for r in same[:MAX_EARLIER]]


def feedback_action(conn, pid: str, uid: str, kind: str, reason: str | None, now: datetime,
                    note: str | None = None) -> dict:
    """Like (saves) / Not interested with a reason (dismisses) / clear (restores); the reason feeds ranking.
    reason 'other' carries the typed `note`."""
    if kind not in FEEDBACK_TRIAGE:
        raise ValueError(f"kind must be one of {tuple(FEEDBACK_TRIAGE)}")
    entry = feedback.record(conn, pid, uid, kind, reason or None, now, tuple(config.profile(pid)["families"]), note)
    triage(conn, pid, uid, FEEDBACK_TRIAGE[kind], now)
    return entry


def triage(conn, pid: str, uid: str, state: str, now: datetime) -> None:
    if state not in TRIAGE_STATES:
        raise ValueError(f"state must be one of {TRIAGE_STATES}")
    if conn.execute("SELECT 1 FROM jobs WHERE uid = ?", (uid,)).fetchone() is None:
        raise KeyError(uid)
    with tx(conn):                                             # GOAL32: every city copy of the role
        conn.executemany("INSERT INTO triage_events(profile, uid, at, state) VALUES (?,?,?,?)",
                         [(pid, copy, iso(now), state) for copy in feedcache.copies(pid, uid)])


def apply_click(conn, pid: str, uid: str, now: datetime) -> int:
    app_id = tracker.track_job(conn, pid, uid)
    with tx(conn):
        conn.execute("UPDATE applications SET apply_clicked_at = coalesce(apply_clicked_at, ?) WHERE id = ?",
                     (iso(now), app_id))
    return app_id


def mark_applied(conn, pid: str, uid: str, now: datetime, resume_id: str | None = None, prefill: bool = True) -> int:
    """Stamp today's date and snapshot the score parts for later learning (decision 002 §8). record the
    resume used (the picked one, else the best) and its keyword %, and prefill an empty Resume cell in the person's
    naming style (Alex_Rivera_ML_Engineer_Resume(Acme).pdf)."""
    app_id = tracker.track_job(conn, pid, uid)
    profile = config.profile(pid)
    ranked = ranking.rank(conn, profile, now, states=("ok", "pending", "filtered", "too_senior"), uid=uid)
    snapshot = {"worth": ranked[0][1].worth, "parts": ranked[0][1].parts, "mults": ranked[0][1].mults,
                "known_skills": sorted(fits.known_skills(pid))} if ranked else None
    tracker.set_status(conn, app_id, "applied", source="ui", snapshot=snapshot)
    who = _who(profile)
    result = vendor_result(ranked[0][0], who) if ranked else None
    scores = result.scores if result else ()
    used = next((s for s in scores if s.id == resume_id), None) or (result.best if result else None)
    chosen = next((r for r in who.resumes if used and r.id == used.id), None)
    app = conn.execute("SELECT company, resume, role_title FROM applications WHERE id = ?", (app_id,)).fetchone()
    with tx(conn):
        conn.execute("UPDATE applications SET resume_used = coalesce(?, resume_used), "
                     "match_pct = coalesce(?, match_pct) WHERE id = ?",
                     (used.id if used else None, used.pct if used else None, app_id))
    if prefill and not (app["resume"] or "").strip() and who.has_baseline:   # an uploaded file names itself
        tracker.edit_field(conn, app_id, "resume", resumes.file_name(profile, chosen, app["company"], app["role_title"]))
    jdsnap.on_save(conn, app_id)                                   # JD text + URL kept, JD cell filled
    jd_doc.soon(app_id)                                            # the full JD as a Google Doc
    return app_id


def _own_app(conn, pid: str, app_id: int) -> int:
    """An application id of this profile, or KeyError (ids are global; the UI acts on the selected profile)."""
    if conn.execute("SELECT 1 FROM applications WHERE id = ? AND profile = ?", (app_id, pid)).fetchone() is None:
        raise KeyError(app_id)
    return app_id


def set_status(conn, pid: str, app_id: int, status: str) -> None:
    tracker.set_status(conn, _own_app(conn, pid, app_id), status, source="ui")
    if status == "applied":
        jd_doc.soon(app_id)                                        # the full JD as a Google Doc


def followup_done(conn, pid: str, app_id: int, n: int, now: datetime) -> None:
    """Mark follow-up 1 or 2 as sent today; it leaves the Follow-ups due tab and exports to the tracker."""
    if n not in (1, 2):
        raise ValueError("follow-up must be 1 or 2")
    tracker.edit_field(conn, _own_app(conn, pid, app_id), f"follow_up_{n}", now.astimezone().strftime("%m/%d/%Y"))


def google_summary(conn, pid: str) -> dict:
    """the person's linked sheet, the sign-in state and the sync status (last sync, errors). the
    header line also carries today's "Overnight email: ..." summary (the same #gbanner line, no new UI code)."""
    from .google import runner
    from .mail import nightly
    found = runner.banner(conn)
    overnight = nightly.overnight_line(conn, pid)
    message = " · ".join(m for m in (overnight, found["message"]) if m) or None
    # the parts apart, in plain words: "alert" only when the user must act (a banner: the sign-in ran out or soon
    # will), "sync_note" when the Sheet sync lags or failed (it retries by itself: the status button), "overnight"
    alert = None
    if found.get("lapsed"):
        alert = "Google sign-in ran out: the Sheet and Drive wait until you sign in again (on the Mac: job-hunt-agent google login)"
    elif found.get("warn"):
        alert = f"Google sign-in ends in {found.get('days_left')} days: sign in again soon (on the Mac: job-hunt-agent google login)"
    sync = found.get("sync") or {}
    sync_note = None
    if (sync.get("errors") or {}).get(pid) or (sync.get("errors") or {}).get("all"):
        sync_note = f"Google Sheet sync failed: {(sync['errors'].get(pid) or sync['errors'].get('all'))}. It tries again by itself"
    elif pid in (sync.get("linked") or []) and not sync.get("healthy"):
        sync_note = "Google Sheet has not synced for 15+ minutes. It tries again by itself"
    return {**found, "message": message, "alert": alert, "sync_note": sync_note, "overnight": overnight,
            "sheet_url": gsync.sheet_url(conn, pid)}


def health_summary(conn, now: datetime) -> dict:
    return health.summary(conn, now)
