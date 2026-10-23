"""Match breakdown for the job feed: experience, skills, industry and title match, each with a short "why"
line, the overall match %, "Why you're a good fit" / "Gaps" bullets and the card labels. Pure and deterministic:
no I/O, no LLM. Inputs are a jobs row (from ranking.rank, with `fit_terms`), its score.Score and a Who.
"""
import re
from dataclasses import dataclass
from datetime import datetime

from . import fresh, genai, keywords, score, signals

# GOAL28 §4.3: "genai" (the JD's GenAI work, both people) sorts the strongest fits first; it never hides a job. The
# overall % renormalizes over the parts that have data, so a job whose fit predates it keeps its old %.
WEIGHTS = {"skills": 0.40, "experience": 0.25, "title": 0.20, "industry": 0.15, "genai": 0.20}
FAMILY_LABELS = {"ai_engineer": "AI Engineer", "genai_engineer": "GenAI Engineer", "ml_engineer": "ML Engineer",
                 "dl_engineer": "Deep Learning Engineer", "data_scientist": "Data Scientist",
                 "data_engineer": "Data Engineer"}
PRIMARY_FAMILIES = 3            # the first N families in profile.yaml are the person's core track
SECONDARY_FAMILY = 0.85         # title in one of the later families
NO_YEARS_PCT = 85               # the JD states no minimum years
CLOSE_YEARS_PCT, STRETCH_YEARS_PCT, YEARS_STEP, YEARS_FLOOR = 85, 65, 20, 15
LEVELS = ("junior", "mid", "senior", "lead")
LEVEL_NAMES = {"junior": "Junior", "mid": "Mid-level", "senior": "Senior", "lead": "Lead/Staff"}
LEVEL_PCT = {-2: 80, -1: 100, 0: 100, 1: 65}       # title level minus the person's level -> title pct
LEVEL_FAR = 35
INDUSTRY_PCT = {"same": 100, "related": 70, "unknown": 60, "other": 35}
MIN_INDUSTRY_SCORE = 3          # keyword evidence needed before naming a JD's industry
COMPANY_WEIGHT = 3              # a keyword in the company name counts this many times
MAX_REPEAT_HITS = 3             # repeated mentions of one industry add at most this much
STRONG_PCT = 85
GOOD_SKILLS_PCT = 60
MAX_BULLETS, MAX_GAPS, MAX_NAMED = 3, 3, 3
EARLY_HOURS = 24
EXACT_AGE_DAYS = 7          # past a week the card shows whole days only

# Deterministic industry table: canonical name -> keywords (whole words, case-insensitive).
INDUSTRIES: dict[str, tuple[str, ...]] = {
    "financial services": ("bank", "banking", "financial services", "fintech", "asset management", "wealth management",
                           "capital markets", "investment banking", "brokerage", "hedge fund", "payments", "lending",
                           "credit card", "custody", "trading", "financial institution"),
    "market data": ("market data", "financial data", "credit ratings", "ratings agency", "indices", "benchmarks",
                    "market intelligence", "commodity insights"),
    "insurance": ("insurance", "insurer", "underwriting", "actuarial", "policyholders", "reinsurance"),
    "real estate": ("real estate", "proptech", "realty", "multifamily", "homebuyers", "commercial real estate",
                    "manufactured housing", "home communities"),
    "property management": ("property management", "property manager", "residents", "tenants", "leasing"),
    "healthcare": ("healthcare", "health care", "hospital", "clinical", "patients", "patient care", "ehr", "medicare",
                   "health plan", "medical"),
    "biotech / pharma": ("biotech", "pharmaceutical", "drug discovery", "life sciences", "genomics", "therapeutics"),
    "automotive": ("automotive", "vehicle", "vehicles", "autonomous driving", "adas", "oem", "mobility", "ev charging"),
    "IT services": ("it services", "consulting", "consultancy", "systems integrator", "managed services",
                    "outsourcing", "digital transformation", "professional services"),
    "software services": ("software services", "software development services", "custom software",
                          "product engineering services"),
    "SaaS": ("saas", "software-as-a-service", "software as a service", "b2b software", "crm", "cloud platform",
             "subscription software"),
    "e-commerce / retail": ("e-commerce", "ecommerce", "retail", "retailer", "marketplace", "shoppers", "merchants"),
    "government / defense": ("federal", "government", "defense", "public sector", "dod", "national security",
                             "intelligence community"),
    "cybersecurity": ("cybersecurity", "cyber security", "threat detection", "security operations", "siem", "xdr"),
    "education": ("edtech", "education", "students", "learners", "k-12", "higher education"),
    "media / entertainment": ("media", "streaming", "entertainment", "gaming", "video games", "publishing"),
    "telecom": ("telecom", "telecommunications", "wireless", "5g", "carrier network"),
    "energy": ("energy", "utilities", "utility", "oil and gas", "renewable", "power grid", "solar"),
    "logistics": ("logistics", "supply chain", "freight", "shipping", "fulfillment", "warehouse"),
    "semiconductors": ("semiconductor", "semiconductors", "chip design", "silicon", "gpu"),
}
# Well-known employers whose JDs rarely name their own industry; matched against the company name only.
COMPANY_HINTS: dict[str, tuple[str, ...]] = {
    "financial services": ("wells fargo", "capital one", "jpmorgan", "jp morgan", "chase", "goldman sachs",
                           "morgan stanley", "citi", "citigroup", "bank of america", "american express",
                           "charles schwab", "fidelity", "blackrock", "visa", "mastercard", "paypal", "us bank"),
    "insurance": ("allstate", "progressive", "state farm", "geico", "liberty mutual", "travelers", "nationwide",
                  "usaa", "metlife", "prudential", "aflac", "humana"),
    "telecom": ("verizon", "at&t", "t-mobile", "comcast", "charter communications"),
    "e-commerce / retail": ("target", "walmart", "costco", "home depot", "lowe's", "best buy", "kroger", "wayfair"),
    "market data": ("s&p global", "moody's", "msci", "bloomberg", "factset", "morningstar"),
    "SaaS": ("salesforce", "servicenow", "workday", "atlassian", "hubspot", "snowflake", "datadog"),
    "semiconductors": ("nvidia", "amd", "intel", "qualcomm", "micron", "broadcom"),
}
_HINTS = {name: re.compile(r"(?i)(?<![\w-])(" + "|".join(re.escape(k) for k in words) + r")(?![\w-])")
          for name, words in COMPANY_HINTS.items()}
HINT_WEIGHT = 6                 # a known employer outweighs incidental JD keywords
RELATED: dict[str, frozenset[str]] = {
    "financial services": frozenset({"market data", "insurance"}),
    "market data": frozenset({"financial services"}),
    "insurance": frozenset({"financial services"}),
    "real estate": frozenset({"property management"}),
    "property management": frozenset({"real estate"}),
    "IT services": frozenset({"software services", "SaaS"}),
    "software services": frozenset({"IT services", "SaaS"}),
    "SaaS": frozenset({"software services", "IT services"}),
}
_PATTERNS = {name: re.compile(r"(?i)(?<![\w-])(" + "|".join(re.escape(k) for k in words) + r")(?![\w-])")
             for name, words in INDUSTRIES.items()}
_LOWER = {name: tuple(k.lower() for k in words) for name, words in INDUSTRIES.items()}
# Benefits / perks lines ("medical, dental and life insurance", "401(k)") say nothing about the employer's industry.
_BENEFITS = re.compile(r"(?i)\b(401\(?k\)?|dental|paid time off|pto|parental leave|tuition (?:assistance|reimbursement)|"
                       r"insurance (?:coverage|plans?|options|benefits)|(?:health|life|disability|medical|pet) insurance|"
                       r"benefits? (?:package|plans?|include|program)|comprehensive benefits)\b")
_LEAD = re.compile(r"(?i)\b(lead|architect|principal|staff|head)\b")
_SENIOR = re.compile(r"(?i)\b(senior|sr)\b")
_JUNIOR = re.compile(r"(?i)\b(junior|jr|entry[- ]level|associate|graduate)\b")
_ROMAN = re.compile(r"(?<![\w/])(I{1,3}|IV|V)(?![\w/])")       # case-sensitive grade: Engineer I .. V
_ROMAN_LEVEL = {"I": "junior", "II": "mid", "III": "senior", "IV": "lead", "V": "lead"}
_IC_STAFF = re.compile(r"(?i)\bmember of (the )?technical staff\b")
_APPLIED_CHIP = re.compile(r"^Applied (\w{3} \d{2}|before)$")


@dataclass(frozen=True)
class Who:
    """The per-profile inputs of a match (built once per request from profile.yaml + known_skills.txt)."""
    years: int | None
    max_years: int | None = None
    families: tuple[str, ...] = ()
    industries: tuple[str, ...] = ()
    known: frozenset[str] = frozenset()
    has_baseline: bool = True
    sponsorship_label: bool = False
    resumes: tuple = ()        # resumes.Resume in profile order (bit i of a term's hit mask)


@dataclass(frozen=True)
class Part:
    key: str
    label: str
    pct: int | None            # None: not enough data (left out of the overall %)
    why: str
    hits: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {"key": self.key, "label": self.label, "pct": self.pct, "why": self.why}


def who_of(profile: dict, known: frozenset[str], has_baseline: bool, resumes: tuple = ()) -> Who:
    return Who(profile.get("years"), profile.get("max_required_years"), tuple(profile.get("families") or ()),
               tuple(str(i) for i in profile.get("industries") or ()), known, has_baseline,
               bool(profile.get("needs_sponsorship")), tuple(resumes))


@dataclass(frozen=True)
class _OneResume:
    id: str = "main"
    label: str = "Resume"
    families: tuple = ()


def resume_scores(row: dict, who: Who) -> tuple[list[keywords.ResumeScore], keywords.ResumeScore | None, str | None]:
    """(score per resume, the best one, the title-mapped resume id) for a ranked row."""
    mine = who.resumes or (_OneResume(),)
    scores = keywords.score_all(row.get("fit_terms"), mine)
    title = next((r.id for fam in row_families(row, who) for r in mine if fam in r.families), None)
    return scores, keywords.best(scores, title), title


def _canon(name: str) -> str | None:
    low = name.strip().lower()
    return next((k for k in INDUSTRIES if k.lower() == low), None)


def _hits(name: str, rx: re.Pattern, text: str, low: str) -> list[str]:
    """rx's matches, read only when one of its keywords occurs at all: most industries have none in a JD, and the
    substring test is far cheaper than the regex (the first feed after a restart waited ~13 s on 6k JDs)."""
    return rx.findall(text) if any(k in low for k in _LOWER[name]) else []


def industry_of(company: str | None, text: str | None) -> str | None:
    """The JD's industry from the keyword table (company name counts triple), or None when the evidence is thin
    or tied."""
    body = "\n".join(line for line in re.split(r"\n|(?<=[.;])\s+", text or "") if not _BENEFITS.search(line))
    low, company = body.lower(), company or ""
    scores = []
    for name, rx in _PATTERNS.items():
        found = _hits(name, rx, body, low)
        hint = HINT_WEIGHT if name in _HINTS and _HINTS[name].search(company) else 0
        hits = hint + len(_hits(name, rx, company, company.lower())) * COMPANY_WEIGHT + \
            len({m.lower() for m in found}) + min(len(found), MAX_REPEAT_HITS)
        scores.append((hits, name))
    scores.sort(key=lambda s: (-s[0], s[1]))
    best, second = scores[0], scores[1]
    if best[0] < MIN_INDUSTRY_SCORE or best[0] == second[0]:
        return None
    return best[1]


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def industry_part(industry: str | None, who: Who) -> Part:
    mine = [c for c in (_canon(i) for i in who.industries) if c]
    if industry is None:
        return Part("industry", "Industry", INDUSTRY_PCT["unknown"], "The JD does not make the industry clear")
    if industry in mine:
        return Part("industry", "Industry", INDUSTRY_PCT["same"], f"{_cap(industry)}: matches your background")
    near = [m for m in mine if industry in RELATED.get(m, ())]
    if near:
        return Part("industry", "Industry", INDUSTRY_PCT["related"],
                    f"{_cap(industry)}: close to your {near[0]} background")
    return Part("industry", "Industry", INDUSTRY_PCT["other"], f"{_cap(industry)}: a new domain for you")


def _years_pct(gap: int) -> int:
    if gap <= 0:
        return 100
    if gap == 1:
        return CLOSE_YEARS_PCT
    return max(YEARS_FLOOR, STRETCH_YEARS_PCT - YEARS_STEP * (gap - 2))


def experience_part(min_years: int | None, who: Who) -> Part:
    if who.years is None:
        return Part("experience", "Experience", None, "Add `years` to profile.yaml to compare")
    if min_years is None:
        return Part("experience", "Experience", NO_YEARS_PCT, f"JD states no minimum; you have {who.years} yrs")
    pct = _years_pct(min_years - who.years)
    if who.max_years is not None and min_years > who.max_years:
        return Part("experience", "Experience", pct,
                    f"JD asks {min_years}+ yrs; above your {who.max_years}-yr ceiling (too senior)")
    if pct == 100:
        return Part("experience", "Experience", pct, f"You have {who.years} yrs; JD asks {min_years}+")
    return Part("experience", "Experience", pct, f"JD asks {min_years}+ yrs; you have {who.years}")


def skills_part(fit_terms: str | None, jd_state: str | None, who: Who, families: str | None = None) -> Part:
    """The best resume's keyword match % (resume text only; decision 007)."""
    if not who.has_baseline:
        return Part("skills", "Skills", None, "No baseline resume yet, so skills are not compared")
    if fit_terms is None:
        return Part("skills", "Skills", None, "Still scoring this JD")
    if jd_state in ("none", "unreadable"):
        return Part("skills", "Skills", None, "No readable JD text to compare")
    _, top, _ = resume_scores({"fit_terms": fit_terms, "families": families}, who)
    if top is None or not top.n_total:
        return Part("skills", "Skills", None, "The JD lists no hard skills we track")
    named = f"{top.label} resume has" if who.resumes else "Your resume has"
    why = f"{named} {len(top.matched)}/{top.n_total} JD keywords"
    why += f"; missing: {', '.join(top.missing[:MAX_NAMED])}" if top.missing else " (all of them)"
    if jd_state == "thin":
        why += " (short JD)"
    return Part("skills", "Skills", top.pct, why, top.matched, top.missing)


def level_of(title: str | None) -> str:
    t = _IC_STAFF.sub("", title or "")
    if _LEAD.search(t):
        return "lead"
    if _SENIOR.search(t):
        return "senior"
    if _JUNIOR.search(t):
        return "junior"
    grade = _ROMAN.search(t)
    return _ROMAN_LEVEL[grade.group(1)] if grade else "mid"


def person_level(years: int | None) -> str:
    if years is None:
        return "mid"
    return "junior" if years < 2 else "mid" if years < 4 else "senior" if years < 8 else "lead"


def row_families(row: dict, who: Who) -> list[str]:
    tagged = set((row.get("families") or "").strip(",").split(","))
    return [f for f in who.families if f in tagged]


def title_part(row: dict, who: Who) -> Part:
    fams = row_families(row, who)
    family = FAMILY_LABELS.get(fams[0], fams[0]) if fams else None
    primary = bool(fams) and who.families.index(fams[0]) < PRIMARY_FAMILIES
    level = level_of(row.get("title"))
    diff = LEVELS.index(level) - LEVELS.index(person_level(who.years))
    level_pct = LEVEL_PCT.get(diff, LEVEL_FAR if diff > 0 else LEVEL_PCT[-2])
    pct = round(level_pct * (1.0 if primary else SECONDARY_FAMILY))
    track = f"{family} track" if family else "Outside your title families"
    yrs = f"{who.years} yrs" if who.years is not None else "your level"
    fit = "fits" if diff in (-1, 0) else "is above" if diff > 0 else "is below"
    return Part("title", "Title", pct, f"{track}; {LEVEL_NAMES[level].lower()} title {fit} {yrs}")


def genai_part(found: str | None) -> Part:
    """GOAL28 §4.3: how much GenAI work the JD's required / preferred lines name (fits.genai: comma-joined terms;
    None = this job's fit was computed before GOAL28, left out of the %)."""
    if found is None:
        return Part("genai", "GenAI work", None, "Not read for this job yet")
    names = [n for n in found.split(",") if n]
    if not names:
        return Part("genai", "GenAI work", genai.pct(names), "The JD names no GenAI work")
    return Part("genai", "GenAI work", genai.pct(names), f"The JD names {_named(tuple(names))}", tuple(names))


def breakdown(row: dict, who: Who, industry: str | None) -> dict:
    """{'pct': overall match %, 'parts': [Part...]} over the parts that have data (weights renormalized). The first four
    are the ring's quarters; GenAI work is listed after them."""
    parts = [experience_part(row.get("min_years"), who),
             skills_part(row.get("fit_terms"), row.get("jd_state"), who, row.get("families")),
             industry_part(industry, who), title_part(row, who), genai_part(row.get("genai"))]
    scored = [(WEIGHTS[p.key], p.pct) for p in parts if p.pct is not None]
    total = sum(w for w, _ in scored)
    pct = round(sum(w * v for w, v in scored) / total) if total else None
    return {"pct": pct, "parts": parts}


def age_label(hours: float | None) -> str | None:
    if hours is None:
        return None
    return f"{max(1, int(hours))}h ago" if hours < 48 else f"{int(hours // 24)}d ago"


def exact_age(hours: float | None, exact: bool = True) -> str | None:
    """GOAL4 §0 card age: '32 min ago', '5 h 12 min ago', '2 d 2 h ago', '8 d ago'; '~' when only the day is known."""
    if hours is None:
        return None
    minutes = max(1, int(hours * 60))
    h, d = minutes // 60, minutes // (60 * 24)
    if minutes < 60:
        text = f"{minutes} min ago"
    elif h < 24:
        text = f"{h} h {minutes % 60} min ago"
    elif d < EXACT_AGE_DAYS:
        text = f"{d} d {h % 24} h ago"
    else:
        text = f"{d} d ago"
    return text if exact else f"~{text}"


def salary_label(lo, hi) -> str | None:
    if lo and hi:
        return f"${lo / 1000:.0f}k to ${hi / 1000:.0f}k"
    return f"${lo / 1000:.0f}k+" if lo else None


def workplace_of(row: dict) -> str | None:
    """remote | hybrid | onsite from the ATS field, else 'remote' when the location says so."""
    if row.get("workplace"):
        return row["workplace"]
    return "remote" if re.search(r"(?i)\bremote\b", row.get("location") or "") else None


def is_early(row: dict, now: datetime) -> bool:
    hours = fresh.age_hours(row, now)
    return hours is not None and hours < EARLY_HOURS and fresh.is_fresh(row, now)


def labels(row: dict, s: score.Score, now: datetime, who: Who) -> dict:
    hours = fresh.feed_age_hours(row, now)
    years = f" · {row['min_years']}+ yrs" if row.get("min_years") else ""
    applied = next((c for c in s.chips if _APPLIED_CHIP.match(c)), None)
    sponsor = score.SPONSORSHIP_CHIPS.get(row.get("sponsorship") or "") if who.sponsorship_label else None
    return {"posted": exact_age(hours, fresh.feed_moment(row)[1]), "early": is_early(row, now),
            "salary": salary_label(row.get("salary_min"), row.get("salary_max")), "workplace": workplace_of(row),
            "seniority": LEVEL_NAMES[level_of(row.get("title"))] + years,
            "employment": signals.EMPLOYMENT_LABELS.get(s.employment) if s.employment else None,
            "applied_before": applied, "too_senior": s.state == "too_senior", "sponsorship": sponsor}


def _named(names: tuple[str, ...]) -> str:
    extra = len(names) - MAX_NAMED
    return ", ".join(names[:MAX_NAMED]) + (f" (+{extra} more)" if extra > 0 else "")


def good_fit(row: dict, s: score.Score, parts: list[Part], now: datetime, who: Who) -> list[str]:
    """Up to 3 'Why you're a good fit' bullets, strongest first, all from the score and the breakdown."""
    by = {p.key: p for p in parts}
    sk, ex, ti, ind = by["skills"], by["experience"], by["title"], by["industry"]
    out = []
    if sk.pct is not None and sk.pct >= GOOD_SKILLS_PCT and sk.hits:
        out.append(f"You cover {len(sk.hits)} of {len(sk.hits) + len(sk.missing)} listed skills, "
                   f"incl. {', '.join(sk.hits[:MAX_NAMED])}")
    if ex.pct == 100 and row.get("min_years"):
        out.append(f"Your {who.years} years meet the {row['min_years']}+ the JD asks")
    if ti.pct is not None and ti.pct >= STRONG_PCT and row_families(row, who):
        out.append(ti.why.split(";")[0] + " role at a level that fits you")
    if ind.pct is not None and ind.pct >= INDUSTRY_PCT["related"]:
        out.append(ind.why)
    if is_early(row, now):
        out.append(f"{fresh.age_word(row)} {age_label(fresh.age_hours(row, now))}: you can be an early applicant")
    pay = salary_label(row.get("salary_min"), row.get("salary_max"))
    if pay:
        out.append(f"Pay is posted: {pay}")
    if row.get("has_team"):
        out.append("Names the team you would join")
    if "Hiring ↑" in s.chips:
        out.append("The company is adding matching roles fast this week")
    return out[:MAX_BULLETS]


def gaps(row: dict, s: score.Score, parts: list[Part]) -> list[str]:
    """Up to 3 gaps: missing skills, years, title level, a new domain, then the score's penalties."""
    by = {p.key: p for p in parts}
    out = []
    if by["skills"].missing:
        out.append(f"Missing {_named(by['skills'].missing)}")
    if by["experience"].pct is not None and by["experience"].pct < STRONG_PCT and row.get("min_years"):
        out.append(by["experience"].why)
    if by["title"].pct is not None and by["title"].pct < STRONG_PCT:
        out.append(by["title"].why)
    if by["industry"].pct == INDUSTRY_PCT["other"]:
        out.append(by["industry"].why)
    out += [name for name, mult in s.mults.items() if mult < 1]
    return out[:MAX_GAPS]
