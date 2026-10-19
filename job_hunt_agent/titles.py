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


def match_job(title: str, description: str | None, families: list[str] | None = None) -> list[str]:
    """match() plus the JD: an academic position (Job Family Group: Academic / Faculty) matches no family."""
    return [] if academic_jd(description) else match(title, families)


def compile_union(families: list[str]):
    """Return a predicate title -> bool for the union of the given families."""
    return lambda title: bool(match(title, families))
