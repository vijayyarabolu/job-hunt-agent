"""one name key for a filing employer and for a job-hunt-agent company, so "Amazon.com Services LLC" (the DOL
file) and "Amazon" (the job board) meet; plus which filings are data / AI roles and which employers are cap-exempt.

key(): applied.company_key (legal suffixes off, the board renames) with letter-by-letter abbreviations joined
("U.S.A." -> usa), a "dba" part cut off, generic words at the end dropped while a word is left ("Amazon com
services" -> amazon, "Capital One Services" -> capital one, "Meta Platforms" -> meta) and the parent / subsidiary
aliases of config/lists/h1b_aliases.csv applied ("Amazon Web Services" -> amazon). Subsidiaries with their own
brand stay apart ("Cohere Health" is not "Cohere"), as applied.same_company decides.
"""
import functools
import re

from .. import applied

# generic words a filing name or a board name ends with (dropped from the end only, never the last word)
TAIL = frozenset({"services", "service", "com", "associates", "solutions", "enterprises", "operations", "platforms",
                  "systems", "software", "technologies", "technology", "tech", "international", "global", "america",
                  "americas", "north", "usa", "us", "na", "group", "holdings", "holding", "corp", "llp", "lp", "pllc",
                  "inc", "incorporated", "co", "company", "limited", "ltd", "pc", "pa", "plc", "llc", "bank",
                  "industries", "resources"})
JOINERS = frozenset({"of", "and", "the", "for", "at", "de"})      # a key never ends with one ("Bank of America")
# a dba that names nobody: "N/A", "Not applicable", "same", a street address, "(an Abbott Laboratories company)"
_JUNK_DBA = re.compile(r"(?i)^\W*(?:n\W*/?\W*a\b.*|not applicable|none|same|no dba|dba|tbd|-+|\d.*|\(?an? .* company\)?|"
                       r"operating name.*|trade name.*|formerly.*|previously.*|it services|.{0,2})\W*$")
_DBA = re.compile(r"(?i)\s*[,(]?\s*\b(?:d\s*/?\s*b\s*/?\s*a|doing business as|a/k/a|aka|f/k/a|fka)\b[\s.:]*")
ALIASES_FILE = "h1b_aliases.csv"
NOT_DBA_FILE = "h1b_not_dba.csv"       # filers whose trade name is not the company of that name (an agency "dba Allstate")

# data / AI / ML / analytics / data-engineering roles: by SOC code, else by the job title
DATA_SOC = ("15-2051", "15-1221", "15-2041", "15-2031", "15-2098", "15-2099", "15-1243", "15-1242", "15-1199.08")
_DATA_TITLE = re.compile(
    r"(?i)\b(?:data|machine learning|ml|mlops|ai|a\.i\.|artificial intelligence|deep learning|nlp|natural language|"
    r"computer vision|llm|genai|generative|analytics|statistic\w*|quantitative|big data|etl|business intelligence|"
    r"bi (?:developer|engineer)|applied scientist|research scientist|decision scien\w*)\b")

# cap-exempt (no lottery): universities and colleges, their hospitals, government / non-profit research labs. The
# LCA files carry no cap-exempt column, so it is the employer's industry code (NAICS) plus name rules.
CAP_NAICS_UNI = ("6113", "6112")                         # colleges, universities, junior colleges
CAP_NAICS_RESEARCH = ("5417", "8132", "8133", "8139", "9271", "9261", "9211")   # R&D, foundations, government
CAP_NAICS_HOSPITAL = ("622",)
_UNI = re.compile(r"(?i)\b(?:university|universities|college|institute of technology|polytechnic|school of medicine|"
                  r"community college|state univ)\b")
_NOT_UNI = re.compile(r"(?i)\b(?:college board|college of american pathologists|collegeboard|"
                      r"university (?:health )?(?:partners|staffing|consulting)|univ(?:ersity)? recruiting)\b")
# for-profit employers the name rules would take for cap-exempt ones (a company's research arm, a for-profit
# university, a lab chain): cap exemption needs a public / non-profit institution
_FOR_PROFIT = re.compile(r"(?i)\b(?:toyota research|underwriters laborator\w*|laboratory corporation|labcorp|"
                         r"merck research|university of phoenix|devry|capella university|strayer|walden university|"
                         r"full sail|grand canyon education|american public university|research laboratories,? llc)\b|"
                         r"\bl\.?\s?l\.?\s?c\.?$")
_RESEARCH = re.compile(r"(?i)\b(?:national laborator(?:y|ies)|national lab|research institute|research foundation|"
                       r"research center|institute|laborator(?:y|ies)|foundation|nasa|national institutes? of health|"
                       r"federally funded)\b")
_GOV_LAB = re.compile(r"(?i)\b(?:national laborator(?:y|ies)|national lab|jet propulsion laboratory|"
                      r"national renewable energy|lawrence (?:livermore|berkeley)|los alamos|sandia|oak ridge|"
                      r"argonne|brookhaven|fermi|pacific northwest national|idaho national|mitre|battelle)\b")
_ACADEMIC_HOSPITAL = re.compile(r"(?i)\b(?:university|academic|college|school of medicine|children'?s hospital|"
                                r"mayo clinic|cleveland clinic|medical college)\b")


def _join_letters(text: str) -> str:
    """"u s a" -> "usa", "n a" -> "na": a run of one-letter words is one word."""
    return re.sub(r"\b(?:[a-z] ){1,}[a-z]\b", lambda m: m.group().replace(" ", ""), text)


def split_dba(name: str | None) -> tuple[str, str | None]:
    """("XYZ Holdings", "Acme") for "XYZ Holdings dba Acme"; the dba part is None when there is none."""
    text = (name or "").strip()
    parts = _DBA.split(text, maxsplit=1)
    if len(parts) == 2 and parts[0].strip() and parts[1].strip(" )"):
        return parts[0].strip(" ,"), parts[1].strip(" )")
    return text, None


@functools.lru_cache(maxsize=1)
def _aliases() -> dict[str, str]:
    from ..seedlists import _rows                         # late: seedlists reads config files
    out = {}
    for r in _rows(ALIASES_FILE):
        alias, canon = _base(r.get("alias")), _base(r.get("company"))
        if alias and canon and alias != canon:
            out[alias] = canon
    return out


@functools.lru_cache(maxsize=1)
def _not_dba() -> frozenset[str]:
    from ..seedlists import _rows                         # late: seedlists reads config files
    return frozenset(filter(None, (_base(r.get("filer")) for r in _rows(NOT_DBA_FILE))))


def dba_counts(name: str | None, dba: str | None) -> bool:
    """A filer's trade name stands for a company of that name (unless h1b_not_dba.csv says it does not)."""
    return real_dba(dba) and _base(split_dba(name)[0]) not in _not_dba()


def _words(name: str | None) -> list[str]:
    text = re.sub(r"(?i)\bnational association\b", " ", name or "")
    words = _join_letters(applied.company_key(text)).split()
    while len(words) > 1 and words[-1] in TAIL and words[-2] not in JOINERS:
        words.pop()
    while len(words) > 1 and words[0] == "the":
        words.pop(0)
    return words


def _base(name: str | None) -> str:
    return "".join(_words(name))


def words(name: str | None) -> tuple[str, ...]:
    """The key's words, not yet joined (the dba part cut): "Anthropic, PBC" -> ("anthropic", "pbc")."""
    return tuple(_words(split_dba(name)[0]))


LEGAL = frozenset({"inc", "incorporated", "corp", "corporation", "llc", "llp", "lp", "pllc", "co", "company", "limited",
                   "ltd", "pc", "pa", "plc"})


def board_words(name: str | None) -> tuple[str, ...]:
    """A board name's words with only the legal form cut ("Cadence Solutions" keeps "solutions", so it never stands
    for "Cadence Design Systems"); "the" first is dropped."""
    words = [x for x in _join_letters(applied.norm(split_dba(name)[0])).split() if x != "and"]
    while len(words) > 1 and words[-1] in LEGAL:
        words.pop()
    while len(words) > 1 and words[0] == "the":
        words.pop(0)
    return tuple(words)


def real_dba(dba: str | None) -> bool:
    """A trade name that names a company (not "N/A", an address or "(an X company)")."""
    return bool(dba and dba.strip() and not _JUNK_DBA.match(dba.strip()))


@functools.lru_cache(maxsize=65536)
def key(name: str | None) -> str:
    """The match key of a company or a filing employer ('' for an empty name). Spaces are dropped, so "Data Bricks"
    and "Databricks" meet."""
    main, _ = split_dba(name)
    base = _base(main)
    return _aliases().get(base, base)


def is_data_role(soc: str | None, title: str | None) -> bool:
    code = (soc or "").strip()
    if code and code.startswith(DATA_SOC):
        return True
    return bool(_DATA_TITLE.search(title or ""))


def cap_exempt(name: str | None, naics: str | None) -> str | None:
    """Why an employer is cap-exempt ("university", "research", "academic hospital", "government lab"), or None."""
    text, code = name or "", (naics or "").strip()
    if _NOT_UNI.search(text):
        return None
    if _GOV_LAB.search(text):                            # national labs are run by LLCs: before the for-profit rule
        return "government lab"
    if _FOR_PROFIT.search(text.strip()):
        return None
    if code.startswith(CAP_NAICS_UNI) or _UNI.search(text):
        return "university"
    if code.startswith(CAP_NAICS_HOSPITAL) and _ACADEMIC_HOSPITAL.search(text):
        return "academic hospital"
    if code.startswith(CAP_NAICS_RESEARCH) and _RESEARCH.search(text):
        return "research"
    return None
