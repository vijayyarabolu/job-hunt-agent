"""Worth score v5 (docs/decisions/002-worth-score.md).

    worth = round(100 · clamp01(.35F + .30R + .20Q + .15S) · max(.10, ΠM))

final(row, fit, ctx) is pure arithmetic over one jobs row, its fits row and per-profile context; it never raises.
"""
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from . import applied, feedback, fresh, genai, keywords, past_employers, resumematch, signals, titles, workterms
from .resume import ats
from .text import norm

WEIGHTS = {"F": 0.35, "R": 0.30, "Q": 0.20, "S": 0.15}
APPLY_TODAY, WORTH_A_LOOK = 70, 50
PIPELINE_CHIPS = {"tailored": "Tailored · apply now", "saved": "Saved"}   # a started application is not a cold lead
DEGRADED_PUSH = 75                       # profile without a baseline: stricter bar, "fit not scored"
FRESH_SCALE_H = 48                       # R = 1/(1+(h/48)^2): .80 @24h, .50 @48h, .20 @96h
DAY_MIDPOINT_H = 12
MIN_TERMS_WEIGHT = 4
FIT_PRIOR, FIT_PRIOR_WEIGHT = 0.6, 3      # (hits + 3·0.6) / (total + 3) shrinks thin JDs toward neutral
LOW_EVIDENCE_TERMS = 8                   # fewer counted JD skills than this -> "Low evidence" chip
AT_MAX_YEARS_S = 0.6                     # a JD asking exactly the profile's max_required_years (
                                         # only when that is above the person's own years)
STAFFING_SIGNALS, STAFFING_MULT = 2, 0.5  # >= 2 harvesting signal kinds -> ×0.5
SPONSORSHIP_CHIPS = {"no": "No sponsorship", "citizen": "US citizens only", "yes": "Sponsors visas"}
MIN_PUSH_SENIORITY = 0.5                 # amendment 13: never push a role far above the profile's years
MULT_FLOOR, REPOST_FLOOR, LANG_FLOOR = 0.10, 0.5, 0.5
REQ_ID_ATS = {"greenhouse", "workday", "smartrecruiters"}     # Lever/Ashby expose no requisition id
_GENERIC_PLACES = {"", "united states", "united states of america", "usa", "us", "u s", "remote", "anywhere",
                   "multiple locations", "americas", "north america", "various locations"}
_SENIOR = re.compile(r"\b(senior|sr)\b", re.I)
_LEAD = re.compile(r"\b(lead|architect)\b", re.I)
_JUNIOR = re.compile(r"\b(junior|jr|entry|associate)\b", re.I)


@dataclass(frozen=True)
class Ctx:
    now: datetime
    profile_id: str
    years: int | None
    known: frozenset[str]
    has_baseline: bool
    applied: tuple = ()
    momentum: dict = field(default_factory=dict)     # company -> (postings last 7 d, postings the 28 d before)
    threshold: int = APPLY_TODAY
    pipeline: dict = field(default_factory=dict)     # uid -> application status before applying (saved | tailored)
    max_years: int | None = None                     # profile max_required_years: a JD asking more is hidden
    sponsorship_label: bool = False                  # profile needs_sponsorship: show the informational label
    prefs: feedback.Prefs | None = None              # Like / Not interested feedback of this profile
    families: tuple = ()                             # the profile's title families, in profile.yaml order
    dealbreakers: tuple = ()                         # profile dealbreaker regexes, re-checked on the title (GOAL3 §2)
    n_resumes: int = 1                               # GOAL4 §1: the fit uses the best of the profile's resumes
    past: frozenset = frozenset()                    # GOAL16 §0: company keys of the person's past employers
    ghost: dict = field(default_factory=dict)        # GOAL29 §3: uid -> (penalty points, badge text) (ghost.load)
    work_rule: str = workterms.RULE_W2               # GOAL35 §4.3: profile work_terms (w2 hides C2C-only etc.; any)
    fresh_window: timedelta = fresh.FRESH_WINDOW     # GOAL36 §1: "new" = posted within 1 / 3 / 7 days (Settings)


@dataclass(frozen=True)
class Score:
    state: str                 # ok | filtered | non_us | too_senior (JD years > max) | pending (fit not computed yet)
    #                            | low_match (GOAL36 §1: no family vouches and the resume match is under MIN_PCT)
    #                            | past_employer (GOAL16 §0: a company the person worked at; never shown)
    worth: int
    band: str
    parts: dict
    mults: dict
    chips: tuple
    push: bool
    fit_pct: float | None = None
    missing: tuple = ()
    dealbreaker: str | None = None
    pipeline: str | None = None
    muted: bool = False         # this req (or its twin) was applied to recently
    employment: str | None = None   # signals.EMPLOYMENT_LABELS key
    scored: bool = True         # False while the fit is pending: the UI shows "—" and sorts it after scored jobs
    ghost: str | None = None    # GOAL29 §3: "Possible ghost job: ..." (flagged, never hidden)
    resume_pct: int | None = None   # GOAL36 §1: the JD's match % against the person's master resume
    vouched: bool = True        # GOAL36 §1: one of the person's title families (and no JD-gate verdict) keeps it


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def _ago(hours: float) -> str:
    return f"{max(1, int(hours))}h" if hours < 48 else f"{int(hours // 24)}d"


def freshness(row: dict, now: datetime) -> tuple[float, float | None]:
    if row.get("seed"):
        return 0.0, fresh.age_hours(row, now)
    h = fresh.age_hours(row, now)
    if h is None:
        return 0.2, None
    hr = max(0.0, h - DAY_MIDPOINT_H) if row.get("precision") == "day" and not row.get("chain_origin") else h
    r = 1 / (1 + (hr / FRESH_SCALE_H) ** 2)
    return (r * 0.8 if row.get("precision") in ("updated", "unknown") else r), h


def specific_us(row: dict) -> bool:
    place = norm(row.get("location"))
    if row.get("is_us") != 1 or place in _GENERIC_PLACES or re.fullmatch(r"\d+ locations?", place):
        return False
    return True


def real_req(row: dict) -> tuple[float, list[tuple[str, float]]]:
    """Q = points earned / points this ATS can expose, plus the positive chips that earned them."""
    pts, avail, chips = 0.0, 7 + 4 + 4, []
    lo, hi = row.get("salary_min"), row.get("salary_max")
    if lo and hi:
        wide = hi / lo > 2
        pts += 3.5 if wide else 7
        chips.append((f"${lo / 1000:.0f}k to ${hi / 1000:.0f}k" + (" (wide)" if wide else ""), 3.5 if wide else 7))
    if row.get("ats") in REQ_ID_ATS:
        avail += 5
        pts += 5 if row.get("req_id") else 0
    if row.get("has_team"):
        pts += 4
        chips.append(("Named team", 4))
    if specific_us(row):
        pts += 4
    return pts / avail, chips


def seniority(row: dict, years: int | None, max_years: int | None = None) -> tuple[float, list[str]]:
    title, min_years, chips = row.get("title") or "", row.get("min_years"), []
    if years is None or min_years is None:
        sy = 0.8
    else:
        gap = min_years - years
        sy = 1.0 if gap <= 2 else 0.5 ** (gap - 2)
        if max_years is not None and min_years >= max_years and min_years > years:
            sy = min(sy, AT_MAX_YEARS_S)
        if sy < 1:
            chips.append(f"{min_years}+ yrs")
    st = 1.0
    if _LEAD.search(title):
        st, chips = 0.6, chips + ["Lead/architect title"]
    elif years is not None and years < 4 and _SENIOR.search(title):
        st, chips = 0.6, chips + ["Senior title"]
    elif years is not None and years >= 3 and _JUNIOR.search(title):
        st, chips = 0.7, chips + ["Junior title"]
    return max(0.25, sy * st), chips


def required_years(row: dict) -> int:
    """The JD's required years; 0 when it states none. GOAL8 §4 (user decision 2026-10-07): a Senior title with no
    stated years is no longer read as 5+ years; it stays in the feed with the "years not stated" badge."""
    return row.get("min_years") or 0


def multipliers(row: dict, h: float | None, need_langs: list[str]) -> dict[str, float]:
    m: dict[str, float] = {}
    if (row.get("staffing") or 0) >= STAFFING_SIGNALS:      # contract roles themselves are never penalized
        m["Staffing post?"] = STAFFING_MULT
    if row.get("chain_n"):
        m[f"Reposted ×{row['chain_n']}"] = max(REPOST_FLOOR, 0.85 ** row["chain_n"])
    days = (h or 0) / 24
    if days >= 120:
        m[f"Open {int(days)}d"] = 0.4
    elif days >= 60:
        m[f"Open {int(days)}d"] = 0.6
    if row.get("is_talent_pool"):
        m["Talent pool"] = 0.3
    if row.get("is_us") == 0:
        m["Non-US"] = 0.2
    elif row.get("is_us") is None:
        m["US unclear"] = 0.9
    if need_langs:
        m["Needs " + ", ".join(need_langs)] = max(LANG_FLOOR, 0.7 ** len(need_langs))
    return m


def _worth(parts: dict, mults: dict) -> int:
    base = _clamp(sum(WEIGHTS[k] * parts[k] for k in WEIGHTS))
    product = 1.0
    for value in mults.values():
        product *= value
    return min(100, round(100 * base * max(MULT_FLOOR, product)))     # a feedback boost never passes 100


def _fit(fit: dict | None, ctx: Ctx) -> dict:
    """F plus everything the chips and the push gate need to know about the fit."""
    out = {"F": 0.5, "known": False, "pct": None, "missing": (), "langs": [], "unknown": None, "thin": False,
           "n_terms": 0}
    if fit is None:
        return {**out, "unknown": "Scoring…"}
    if fit["jd_state"] in ("none", "unreadable"):
        return {**out, "unknown": "No JD" if fit["jd_state"] == "none" else "JD unreadable"}
    pairs = keywords.unpack(fit["terms"])
    terms = [t for t, _ in pairs]
    cov = max((ats.coverage(terms, {t.name for t, mask in pairs if mask >> bit & 1} | set(ctx.known), set(ctx.known))
               for bit in range(max(1, ctx.n_resumes))), key=lambda c: (c.pct, c.hw))
    out.update(pct=cov.pct, missing=cov.missing, langs=ats.needed_languages(terms, set(ctx.known)),
               n_terms=len(ats.counted(terms, set(ctx.known))))
    if not ctx.has_baseline:
        return {**out, "unknown": "Fit not scored"}
    s = (cov.hw + FIT_PRIOR_WEIGHT * FIT_PRIOR) / (cov.tw + FIT_PRIOR_WEIGHT)
    thin = fit["jd_state"] == "thin" or cov.tw < MIN_TERMS_WEIGHT
    unknown = ("Thin JD" if fit["jd_state"] == "thin" else "Few hard skills listed") if thin else \
        ("Low evidence" if out["n_terms"] < LOW_EVIDENCE_TERMS else None)
    return {**out, "F": _clamp(s), "known": not thin, "thin": thin, "full": cov.hw == cov.tw > 0,
            "unknown": unknown}


def _age_chip(row: dict, h: float | None, now: datetime) -> str:
    if row.get("seed"):
        return "Backlog (first run)"
    if row.get("chain_origin"):
        days = (now - fresh.parse(row["chain_origin"])).days
        return f"Reposted ×{row.get('chain_n') or 1} · first seen {days}d ago"
    if h is None:
        return "Date unknown"
    if row.get("precision") == "day":
        return "Posted today" if h < 24 else f"Posted ~{_ago(h)} ago"
    verb = "Posted" if row.get("precision") == "exact" else "First seen"
    return f"{verb} {_ago(h)} ago"


def _ranked_chips(parts: dict, mults: dict, worth: int, extra: list[tuple[str, float]]) -> list[str]:
    """Penalty and info chips ordered by |Δworth| (what the score would be without that factor)."""
    deltas = []
    for name in mults:
        deltas.append((name, _worth(parts, {k: v for k, v in mults.items() if k != name}) - worth))
    deltas.extend(extra)
    return [name for name, _ in sorted(deltas, key=lambda d: -abs(d[1]))]


LOW_MATCH = "low_match"
LITTLE_GENAI, SENIOR_TITLE = "Little GenAI work in the JD", "Staff / lead title"
RANK_LOWER = {LITTLE_GENAI: 0.6, SENIOR_TITLE: 0.7}     # GOAL36 §1: what used to hide these jobs now ranks them lower


def _own_family(row: dict, families: tuple) -> bool:
    tagged = (row.get("families") or ",")
    return any(f",{f}," in tagged for f in families)


def final(row: dict, fit: dict | None, ctx: Ctx) -> Score:
    f = _fit(fit, ctx)
    r, h = freshness(row, ctx.now)
    q, q_chips = real_req(row)
    s, s_chips = seniority(row, ctx.years, ctx.max_years)
    parts = {"F": f["F"], "R": r, "Q": q, "S": s}
    # GOAL36 §1: the JD gate no longer hides a job; its title family just stops vouching for it (the resume decides)
    fit_gate = genai.is_gate_verdict((fit or {}).get("dealbreaker"))
    vouched = _own_family(row, ctx.families) and not fit_gate
    resume_pct = resumematch.pct(fit.get("terms"), ctx.n_resumes) if fit else None
    mults = {**multipliers(row, h, f["langs"]), **feedback.factors(ctx.prefs, row, ctx.families)}
    if fit_gate:
        mults[LITTLE_GENAI] = RANK_LOWER[LITTLE_GENAI]
    if not vouched and titles.senior_title(row.get("title")):
        mults[SENIOR_TITLE] = RANK_LOWER[SENIOR_TITLE]
    raw = _worth(parts, mults)
    penalty, ghost = ctx.ghost.get(row["uid"], (0, None))
    worth = max(0, raw - penalty)                     # GOAL29 §3: a few points (<= ghost.PENALTY_CAP), never hidden
    same_role, n_company = applied.match(row["uid"], row.get("company", ""), row.get("title", ""), ctx.applied)
    muted = bool(same_role and same_role.applied_on and
                 ctx.now.date() - same_role.applied_on <= timedelta(days=applied.MUTE_DAYS))
    # the title is checked here too, so a clearance title ("AI Engineer Associate - TS/SCI with Polygraph") is
    # filtered even before its fit row exists
    dealbreaker = (None if fit_gate else (fit or {}).get("dealbreaker")) \
        or workterms.verdict(row.get("work_terms"), ctx.work_rule) \
        or signals.dealbreaker(row.get("title"), list(ctx.dealbreakers))
    years_needed = required_years(row)
    too_senior = ctx.max_years is not None and years_needed > ctx.max_years
    non_us = row.get("is_us") == 0                    # both profiles need a US job; unknown (None) stays, at x0.9
    # GOAL36 §1: a job its title does not vouch for stays when its JD matches the master resume MIN_PCT % or more
    low_match = not vouched and fit is not None and (fit.get("jd_state") != "ok" or resume_pct is None
                                                      or resume_pct < resumematch.MIN_PCT)
    state = "past_employer" if past_employers.matches(row.get("company"), ctx.past) else \
        "filtered" if dealbreaker else "non_us" if non_us else "too_senior" if too_senior else \
        "pending" if fit is None else LOW_MATCH if low_match else "ok"

    extra = [(c, WEIGHTS["S"] * 100 * (1 - s)) for c in s_chips]
    extra += [(c, WEIGHTS["Q"] * 100 * pts / 15) for c, pts in q_chips]
    w, p = ctx.momentum.get(row.get("company"), (0, 0))
    if w >= 3 and (w + 1) / (p / 4 + 1) >= 2:
        extra.append(("Hiring ↑", 0.1))
    pipeline = ctx.pipeline.get(row["uid"])
    chips = [PIPELINE_CHIPS[pipeline]] if pipeline in PIPELINE_CHIPS else []
    chips.append(_age_chip(row, h, ctx.now))
    employment = row.get("employment_type")
    if employment and employment != signals.FULL_TIME:
        chips.append(signals.EMPLOYMENT_LABELS[employment])
    if too_senior:
        chips.append(f"Too senior · {row['min_years']}+ yrs")
    if row.get("stale"):                                        # Workday: unseen 7 d+, maybe closed (release review)
        chips.append("Unverified 7d+")
    if f["pct"] is not None and ctx.has_baseline:              # the denominator shows how much "100%" covers (M7)
        missing = f" · missing {', '.join(f['missing'][:2])}" if f["missing"] else ""
        noun = "skill" if f["n_terms"] == 1 else "skills"
        chips.append(f"Fit {round(100 * f['pct'])}% of {f['n_terms']} {noun}{missing}")
    if f["unknown"]:
        chips.append(f["unknown"])
    if not vouched and resume_pct is not None:
        chips.append(f"Resume match {resume_pct}%")
    if same_role:
        chips.append(f"Applied {same_role.applied_on:%b %d}" if same_role.applied_on else "Applied before")
    elif n_company:
        chips.append(f"Applied here ×{n_company}")
    chips += _ranked_chips(parts, mults, raw, extra)
    if ctx.sponsorship_label and row.get("sponsorship") in SPONSORSHIP_CHIPS:
        chips.append(SPONSORSHIP_CHIPS[row["sponsorship"]])

    fit_ok = f["known"] or (f["thin"] and f.get("full") and r >= 0.8)
    if ctx.has_baseline:
        gate = worth >= ctx.threshold and fit_ok
    else:
        gate = worth >= DEGRADED_PUSH and r >= 0.8 and q >= 0.5
    push = state == "ok" and gate and s >= MIN_PUSH_SENIORITY and fresh.is_fresh(row, ctx.now, ctx.fresh_window) and not muted
    band = "Apply today" if worth >= APPLY_TODAY else "Worth a look" if worth >= WORTH_A_LOOK else "Low"
    if band == "Apply today" and s < MIN_PUSH_SENIORITY:           # far above the profile's years (release review M2)
        band = "Worth a look"
    if fit is None:                                   # no number before the fit exists
        band = "Scoring"
    return Score(state, worth, band, parts, mults, tuple(chips), push, f["pct"], tuple(f["missing"]), dealbreaker,
                 pipeline, muted, employment, fit is not None, ghost, resume_pct, vouched)
