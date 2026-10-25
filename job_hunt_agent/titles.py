"""Title matching by regex families (GOAL §3). No LLM."""
import re

_ROLE = r"\b(engineer|developer|architect|scientist|specialist)s?\b"
_ENG = r"\b(engineer|developer)s?\b"

FAMILIES: dict[str, re.Pattern] = {
    "ai_engineer": re.compile(
        rf"(?i)\bai\s*/\s*ml\b.*{_ENG}|\bapplied ai\b|\bartificial intelligence\b.*{_ENG}|\bagentic\b"
        rf"|\bai\b.*{_ENG}|{_ENG}.*\bai\b|\bai agents?\b"
    ),
    "genai_engineer": re.compile(
        rf"(?i)\b(gen\s?ai|generative ai|llms?|rag|nlp|natural language|foundation models?)\b.*{_ROLE}"
        rf"|{_ROLE}.*\b(gen\s?ai|generative ai|llms?|rag|nlp|natural language|foundation models?)\b"
    ),
    "ml_engineer": re.compile(
        rf"(?i)\bmachine learning\b.*{_ENG}|{_ENG}.*\bmachine learning\b"
        rf"|\bml\b.*{_ENG}|{_ENG}.*\bml\b|\bmlops\b|\bml ?ops\b"
        rf"|\bapplied (ml |machine learning )?scientist\b|\bmachine learning scientist\b"
    ),
    "dl_engineer": re.compile(
        rf"(?i)\b(deep learning|computer vision|perception)\b.*{_ROLE}|{_ROLE}.*\b(deep learning|computer vision|perception)\b"
    ),
    "data_scientist": re.compile(r"(?i)\bdata scien(tist|ce)\b"),
    "data_engineer": re.compile(rf"(?i)\bdata engineer|\banalytics engineer|\bdata platform\b.*{_ENG}"),
}

EXCLUDE = re.compile(
    r"(?i)\b(intern|internship|co-?op|new (college )?grad(uate)?|university grad(uate)?|"
    r"manager|director|head of|vp|(?<!assistant )vice president|principal|staff|distinguished|fellow|"
    r"sales|pre-?sales|account executive|solutions? (architect|engineer)|recruit(er|ing)|"
    r"ai (trainer|tutor|annotator|evaluator)|(trainer|tutor|annotator|evaluator|rater)\b|(?:data )?annotation|labeling specialist|"
    r"tpm|technical program|program manager|pm|product manager|support|customer success|"
    # GOAL2 A.1 (4-year IC profiles): executive grades, designers, leads, architects, heads, chiefs, people managers
    # AVP alone is an IC grade at banks (Citi, Deloitte): excluded only with manager wording (caught above)
    r"executive director|managing director|svp|senior vice president|"
    r"(?:product|ux|ui|visual|interaction|experience|graphic|motion) designer|designer|"
    r"lead(?!\s+(?:scoring|generation|gen)\b)|leader|architect|head|chief|people (?:manager|leader)|manager of|"
    # customer-facing roles ("Sr. Tech CSM - ... AWS Applied AI Solutions") and managers by another name
    # ("Managing Engineer, Data Engineering"). Forward-deployed engineers stay: they ship code with customers.
    r"csm|tam|technical account|customer engineers?(?!ing)|solutions? consultant|developer (?:advocate|relations)|"
    r"devrel|managing|supervisor)\b"
)
_EXEC_GRADE = re.compile(r"(?:^|[\s,(/-])(ED|SVP|EVP)(?=$|[\s,)/-])")     # case-sensitive; not MD (= Maryland)


# resumegen GOAL3 §9 (user, 2026-10-07): teaching / academic jobs are not software roles ("Education Assistant
# Professor - Biostatistics & Data Science" matched Data Scientist). "Teaching Assistant tools" is a product, not a job.
ACADEMIC = re.compile(
    r"(?i)\b(professors?|professorship|lecturers?|faculty|instructors?|teachers?|"
    r"teaching(?!\s+assistant\s+(?:tools?|platforms?|products?|apps?|software|agents?))|"
    r"tutors?|adjunct|post-?docs?|postdoctoral|deans?|research fellows?)\b"
)
# the JD says it is an academic position: "Job Family Group: Academic", "Position Type: Faculty" (KUMC writes the value
# on the next line)
ACADEMIC_JD = re.compile(r"(?i)\b(?:job family group|job family|position type|employee type|job type|employee class|"
                         r"position category)\s*:\s*(?:academic|faculty)\b")


def academic_jd(text: str | None) -> bool:
    return bool(text) and bool(ACADEMIC_JD.search(text))


# "Member of Technical Staff" is the standard IC title at AI labs, not the Staff seniority level.
_IC_STAFF = re.compile(r"(?i)\bmember of (the )?technical staff\b")
_NOT_SOFTWARE = re.compile(
    r"(?i)\b(applied ai architect|hardware|firmware|asic|fpga|silicon|mechanical|electrical|"
    r"physical design|network operations|data cent(er|re))\b"
)


def match(title: str, families: list[str] | None = None) -> list[str]:
    """Return the families a title belongs to (restricted to `families` if given); [] if excluded."""
    if not title:
        return []
    if EXCLUDE.search(_IC_STAFF.sub("", title)) or _EXEC_GRADE.search(title) or _NOT_SOFTWARE.search(title) \
            or ACADEMIC.search(title):
        return []
    wanted = families or list(FAMILIES)
    return [name for name in wanted if FAMILIES[name].search(title)]


# GOAL35 §2 (user: "match by JD content, not title only"): a plain software title ("Software Engineer", "Member of
# Technical Staff") matched no family, so its JD was never kept and GOAL28's "generic title + 2 GenAI terms" gate never
# saw it. Now its JD is fetched and read: 2+ GenAI terms in the required / preferred lines make it an AI Engineer job.
JD_FAMILY = "ai_engineer"
JD_MIN_TERMS = 2


def generic(title: str | None) -> bool:
    """A plain software title (genai.is_generic_title) that no exclusion (Staff, Manager, Intern, ...) removes."""
    from .genai import is_generic_title           # late: genai -> resume.ats -> lexicon
    return bool(title) and is_generic_title(title) and not EXCLUDE.search(_IC_STAFF.sub("", title)) \
        and not _EXEC_GRADE.search(title) and not _NOT_SOFTWARE.search(title) and not ACADEMIC.search(title)


def by_jd(title: str, description: str | None, families: list[str] | None = None) -> list[str]:
    """[JD_FAMILY] for a generic title whose JD names JD_MIN_TERMS+ GenAI terms (when that family is wanted)."""
    if not description or (families is not None and JD_FAMILY not in families) or not generic(title):
        return []
    from .genai import terms
    return [JD_FAMILY] if len(terms(description)) >= JD_MIN_TERMS else []


def match_job(title: str, description: str | None, families: list[str] | None = None) -> list[str]:
    """match() plus the JD: an academic position (Job Family Group: Academic / Faculty) matches no family; a generic
    title matches by its JD's GenAI work (by_jd)."""
    if academic_jd(description):
        return []
    return match(title, families) or by_jd(title, description, families)


# GOAL36 §1 (user 2026-10-23: "get them based on the resume, not by title"): a title only keeps a job out when it is
# clearly another field. The deny list lives here and nowhere else; every drop by it is counted per day and reason
# (store.write_board -> title_denials). Anything not denied may come in by its JD's match to a person's master resume
# (resumematch). Seniority words (Staff, Principal, Lead, Architect) are not denied: they are a label and a rank.
_TECH = re.compile(r"(?i)\b(?:engineer(?:ing)?|developer|scientist|programmer|researcher|data|software|ml|ai|"
                   r"machine learning|llm|nlp|mlops|devops|platform|back[\s-]?end|full[\s-]?stack|quant(?:itative)?|"
                   r"statistician|modeler|swe|computer vision|deep learning)\b")
_DENY_ALWAYS = {          # another field, whatever else the title says
    "sales": r"sales\b|pre-?sales\b|account executive|account manager|business development|\b(?:bdr|sdr)\b|"
             r"solutions? (?:architect|engineer|consultant)|customer success|\bcsm\b|\btam\b|technical account|"
             r"customer engineers?(?!ing)|developer (?:advocate|relations)|devrel",
    "recruiting": r"recruit(?:er|ing)|talent acquisition|sourcer|human resources|\bhr (?:generalist|business partner)",
    "management": r"manager|director|head of|\bvp\b|(?<!assistant )vice president|chief|managing|supervisor|"
                  r"executive director|\bsvp\b|\bevp\b|general manager|people (?:manager|leader)",
    "intern": r"interns?\b|internships?\b|co-?ops?\b|apprentice|new (?:college )?grad(?:uate)?|university grad(?:uate)?",
    "annotation": r"ai (?:trainer|tutor|annotator|evaluator)|(?:trainer|tutor|annotator|evaluator|rater)\b|"
                  r"(?:data )?annotation|labeling specialist",
    "teaching": ACADEMIC.pattern.removeprefix("(?i)"),
    "program_product": r"\btpm\b|technical program|program manager|product manager|product owner|scrum master",
}
_DENY_UNLESS_TECH = {     # another field unless a tech word is in the title too ("Healthcare Data Scientist" stays)
    "healthcare": r"nurs(?:e|es|ing)|\b(?:rn|lpn|lvn|cna)\b|physician|therapist|pharmac(?:ist|y)|medical assistant|"
                  r"dental|caregiver|clinician|surgeon|patient|phlebotom|radiolog|sonograph|veterinar",
    "driver": r"driver|\bcdl\b|courier|delivery associate",
    "accounting": r"accountant|accounting|bookkeep|payroll|auditor|\btax\b|controller|accounts (?:payable|receivable)",
    "warehouse": r"warehouse|forklift|picker|packer|material handler|loader|fulfillment associate",
    "retail_food": r"cashier|barista|(?:food|restaurant|banquet) server|\bcook\b|chef|dishwasher|retail|"
                   r"store (?:associate|manager)|sales associate|crew member|housekeep",
    "trades": r"electrician|plumber|hvac|mechanic|welder|carpenter|machinist|installer|janitor|custodian|"
              r"maintenance (?:technician|worker)|construction|laborer|painter",
    "legal": r"attorney|paralegal|lawyer|counsel\b",
    "admin_support": r"administrative|receptionist|office assistant|customer service|call center|customer support|"
                     r"help ?desk|support (?:specialist|technician|representative)|desktop support|clerk",
    "marketing": r"marketing (?:coordinator|specialist|associate)|copywriter|content writer|social media|public relations",
    "security_guard": r"security (?:officer|guard)|loss prevention",
}
# another field unless the title names AI / ML / data work ("ML Engineer, Hardware Acceleration" stays)
_DENY_UNLESS_AI = {"hardware": r"hardware|firmware|asic|fpga|silicon|mechanical|electrical|physical design|"
                               r"network operations|data cent(?:er|re) technician|rf engineer|manufacturing"}
_AI_DATA = re.compile(r"(?i)\b(?:ml|ai|machine learning|deep learning|llm|nlp|data|computer vision|perception)\b")
_DENY = [(reason, re.compile(rf"(?i)\b(?:{rx})")) for reason, rx in _DENY_ALWAYS.items()]
_DENY_HARD = [(reason, re.compile(rf"(?i)\b(?:{rx})")) for reason, rx in _DENY_UNLESS_AI.items()]
_DENY_SOFT = [(reason, re.compile(rf"(?i)\b(?:{rx})")) for reason, rx in _DENY_UNLESS_TECH.items()]
_SENIOR_TITLE = re.compile(r"(?i)\b(?:staff|principal|distinguished|fellow|lead|architect)\b")


def denied(title: str | None) -> str | None:
    """The deny-list reason this title is another field ("sales", "healthcare", ...), or None."""
    if not (title or "").strip():
        return "no_title"
    text = _IC_STAFF.sub(" technical staff ", title)
    hit = next((reason for reason, rx in _DENY if rx.search(text)), None) or \
        ("management" if _EXEC_GRADE.search(text) else None)
    if hit:
        return hit
    if not _AI_DATA.search(text):
        hit = next((reason for reason, rx in _DENY_HARD if rx.search(text)), None)
        if hit:
            return hit
    if _TECH.search(text) or _IC_STAFF.search(title):
        return None
    return next((reason for reason, rx in _DENY_SOFT if rx.search(text)), None)


def senior_title(title: str | None) -> bool:
    """Staff / Principal / Lead / Architect: shown with a label and ranked lower, never denied (GOAL36 §1)."""
    return bool(_SENIOR_TITLE.search(_IC_STAFF.sub("", title or "")))


def worth_reading(title: str | None) -> bool:
    """Fetch this posting's JD (one detail call on the boards that need one): not denied, and a family title, a generic
    software title or a tech word in it. A non-tech title that is not denied is read only where the list carries the JD."""
    if denied(title):
        return False
    return any(rx.search(title) for rx in FAMILIES.values()) or generic(title) or bool(_TECH.search(title))


def compile_union(families: list[str]):
    """Return a predicate title -> bool: worth fetching the JD of. GOAL36 §1: every title worth_reading(), whatever the
    person's families (a family is a label now, not a gate)."""
    return worth_reading
