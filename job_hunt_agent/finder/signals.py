"""Free contact signals (GOAL6 §4 step 1) and the links that hand the search to the person: people named in the JD,
the company's team page, the company domain, LinkedIn's own people-search URLs (opened by the person; nothing is
scraped or called), the "Open in Apollo" web search, and the email-pattern guess (labelled, never presented as
verified)."""
import html as htmllib
import re
import unicodedata
from urllib.parse import quote, urlencode, urlsplit

from ..gsync_values import urls

GUESS_LABEL = "guessed, unverified"
GUESSED_DOMAIN = "guessed from the company name: check it before a paid lookup"
NON_COMPANY_HOSTS = ("greenhouse.io", "lever.co", "ashbyhq.com", "myworkdayjobs.com", "myworkdaysite.com",
                     "workday.com", "workable.com", "smartrecruiters.com", "icims.com", "eightfold.ai", "jobvite.com",
                     "bamboohr.com", "linkedin.com", "lnkd.in", "indeed.com", "glassdoor.com", "ziprecruiter.com",
                     "google.com", "phenompeople.com", "successfactors.com", "taleo.net", "oraclecloud.com", "adp.com",
                     "ultipro.com", "paylocity.com", "rippling.com", "dover.com", "wellfound.com", "builtin.com",
                     "bit.ly", "tinyurl.com", "youtube.com", "facebook.com", "twitter.com", "x.com", "instagram.com",
                     "eeoc.gov", "dol.gov", "e-verify.gov", "uscis.gov")
COMPANY_SUFFIX = {"inc", "llc", "ltd", "corp", "corporation", "co", "company", "the", "group", "technologies",
                  "technology", "holdings", "labs", "plc", "gmbh", "international", "americas", "usa", "us"}
GENERIC_PART = re.compile(r"^(careers?|jobs?|recruit\w*|talent\w*|acquisition|hr|people|hiring|apply|applications?|"
                          r"candidates?|ac+om+odations?|ada|eeo|disability|accessibility|privacy|benefits|info|hello|"
                          r"contact|help|\w*support|customer\w*|fraud|report|no-?reply|admin|team|office)$", re.I)
NOT_A_NAME = {"Equal", "Opportunity", "Employer", "United", "States", "Machine", "Learning", "The", "Our", "Team",
              "Talent", "Acquisition", "Hiring", "Manager", "Recruiter", "Please", "We", "You", "Head", "Director",
              "Data", "Engineering", "Human", "Resources", "Inc", "Contact", "Apply", "Senior", "Lead", "Questions",
              "Chief", "Officer", "About", "Join", "Benefits", "Learn", "More"}
_NAME = r"[A-Z][a-z]+(?:[-'][A-Z][a-z]+)?(?: [A-Z]\.)?(?: [A-Z][a-z]+(?:[-'][A-Z][a-z]+)?){1,2}"
_CUE = (r"(?i:(?P<cue>talent acquisition (?:partner|lead|manager)|talent partner|recruiting (?:lead|manager|partner)|"
        r"technical recruiter|recruiter|sourcer|hiring manager))")
_CUE_THEN_NAME = re.compile(rf"\b{_CUE}\b\s*[,:\-–]?\s*(?:is\s+)?(?P<name>{_NAME})")
_NAME_THEN_CUE = re.compile(rf"(?P<name>{_NAME})\s*[,\-–(]\s*(?:(?i:our|the|a)\s+)?{_CUE}\b")
_REPORTS_TO = re.compile(r"(?i:report(?:s|ing)?\s+(?:directly\s+)?to)\s+(?:(?i:the|our|a)\s+)?"
                         r"(?P<title>(?:Senior\s+|Sr\.?\s+)?(?:VP|Vice President|Head|Director|Chief|Engineering Manager|"
                         r"Manager|Lead)\b[^.,;\n(]{0,60})")
_TEAM = re.compile(r"\b(?:the|our)\s+(?P<team>[A-Z][\w&/+-]*(?: [A-Z&][\w&/+-]*){0,3})\s+team\b")
_EMAIL = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")
RELEVANT_TITLE = re.compile(r"\b(cto|ceo|chief (?:technology|technical|data|ai|product|executive|operating|science|"
                            r"scientific)|co-?founder|founder|vp|vice president|head of|director|engineering manager|"
                            r"recruit\w*|talent)\b", re.I)
FAMILY_WORDS = {"ai_engineer": "AI", "genai_engineer": "generative AI", "ml_engineer": "machine learning",
                "dl_engineer": "deep learning", "data_scientist": "data science", "data_engineer": "data engineering"}
APOLLO_TITLES = ("recruiter", "talent acquisition", "engineering manager", "head of ai", "head of machine learning",
                 "head of data", "vp engineering", "cto")
LINKEDIN_SEARCH = "https://www.linkedin.com/search/results/people/"
APOLLO_PEOPLE = "https://app.apollo.io/#/people"
MAX_TITLE = 80


def _person(name: str, title: str, email: str | None, source: str, note: str) -> dict:
    return {"name": name, "title": title, "email": email, "linkedin_url": None, "source": source, "note": note}


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _real_name(name: str, company: str) -> bool:
    """Not a heading word, and not just the company's own name."""
    words = name.replace(".", "").split()
    return not any(w in NOT_A_NAME for w in words) and not _words(name) <= _words(company)


def _inbox(local: str) -> bool:
    """careers@, talent.acquisition@, Disability_Accommodation@: a functional mailbox, not a person."""
    return any(GENERIC_PART.match(part) for part in re.split(r"[._+-]", local) if part)


def _host(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def registrable(host: str) -> str:
    """example.com from careers.example.com (example.co.uk kept whole)."""
    parts = host.lower().strip(".").split(".")
    if len(parts) >= 3 and parts[-2] in ("co", "com", "ac", "org", "net") and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _not_company(domain: str) -> bool:
    return any(domain == h or domain.endswith("." + h) for h in NON_COMPANY_HOSTS)


def jd_people(text: str, company: str) -> list[dict]:
    """Recruiters / managers the JD names, the title it says the role reports to, and the emails it lists."""
    text = text or ""
    found: dict[str, dict] = {}
    for rx in (_CUE_THEN_NAME, _NAME_THEN_CUE):
        for m in rx.finditer(text):
            name = m.group("name")
            if _real_name(name, company) and name not in found:
                found[name] = _person(name, m.group("cue").title(), None, "jd", "named in the JD")
    hints = []
    for m in _REPORTS_TO.finditer(text):
        title = re.sub(r"\s+(?:and|who|with|in|on)\b.*$", "", m.group("title")).strip()
        if title and all(h["title"] != title for h in hints):
            hints.append(_person("", title, None, "jd", "the JD says the role reports to this person"))
    for email in dict.fromkeys(_EMAIL.findall(text)):
        email = email.rstrip(".")
        local, domain = email.split("@", 1)
        if _not_company(domain.lower()):
            continue
        owner = next((p for p in found.values() if not p["email"] and any(
            len(t) >= 3 and t.lower() in local.lower() for t in p["name"].split())), None)
        if owner:
            owner["email"] = email
        elif _inbox(local):
            hints.append(_person("", f"team inbox ({local})", email, "jd", "an inbox the JD lists"))
        elif re.fullmatch(r"[a-z]+[._][a-z]+", local, re.I):
            name = " ".join(p.capitalize() for p in re.split(r"[._]", local))
            found.setdefault(name, _person(name, "", email, "jd", "email listed in the JD"))
    return list(found.values()) + hints


def team_of(text: str) -> str | None:
    """'Machine Learning' from "Join our Machine Learning team"."""
    m = _TEAM.search(text or "")
    return m.group("team") if m else None


def _tokens(company: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", _ascii(company).lower()) if t and t not in COMPANY_SUFFIX]


def _matches(domain: str, company: str) -> bool:
    label, toks = domain.split(".")[0], _tokens(company)
    if not toks:
        return False
    joined = "".join(toks)
    initials = "".join(t[0] for t in toks)
    return joined in label or (len(label) >= 4 and label in joined) or any(len(t) >= 4 and t in label for t in toks) \
        or (len(toks) >= 2 and label == initials)


def domain_guess(company: str, texts: list[str], links: list[str]) -> tuple[str | None, str | None]:
    """(domain, where it came from): a company-looking domain in the posting links, then in the JD's links and
    emails, else the company name + .com (labelled a guess, to be checked before any paid lookup)."""
    seen: list[tuple[str, str]] = []
    for link in links:
        seen.append((registrable(_host(link)), "posting link"))
    for text in texts:
        seen += [(registrable(_host(u)), "link in the JD") for _, _, u in urls(text or "")]
        seen += [(registrable(e.rstrip(".").split("@", 1)[1]), "link in the JD") for e in _EMAIL.findall(text or "")]
    for domain, source in seen:
        if domain and not _not_company(domain) and _matches(domain, company):
            return domain, source
    toks = _tokens(company)
    return (f"{''.join(toks)}.com", GUESSED_DOMAIN) if toks else (None, None)


def linkedin_searches(company: str, family: str | None, team: str | None) -> list[dict]:
    """LinkedIn's own people-search page for four searches (opened by the person in a new tab)."""
    words = FAMILY_WORDS.get(family or "", "engineering")
    queries = [f"{company} recruiter {words}", f"{company} talent acquisition", f"{company} {team or words} manager",
               f"{company} head of AI ML data"]
    return [{"label": f"Find on LinkedIn: {q}", "url": f"{LINKEDIN_SEARCH}?{urlencode({'keywords': q})}"}
            for q in queries]


def apollo_web_url(domain: str, titles: tuple[str, ...] = APOLLO_TITLES) -> str:
    """Apollo's web people search filtered by the company domain and these titles (the person reveals an email there
    by hand, with their own credits)."""
    pairs = [("qOrganizationDomainsList[]", domain)] + [("personTitles[]", t) for t in titles] + [("page", "1")]
    return f"{APOLLO_PEOPLE}?" + "&".join(f"{quote(k, safe='[]')}={quote(v)}" for k, v in pairs)


def _ascii(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def guess_email(name: str, domain: str | None, pattern: str | None = None) -> str | None:
    """first.last@domain (or the company's known pattern), for a full name; None when it cannot be built."""
    if not domain or "*" in (name or ""):
        return None
    parts = [p for p in (name or "").split() if not re.fullmatch(r"[A-Z]\.?", p)]
    if len(parts) < 2:
        return None
    first, last = (re.sub(r"[^a-z]", "", _ascii(p).lower()) for p in (parts[0], parts[-1]))
    if not first or not last:
        return None
    local = (pattern or "{first}.{last}").replace("{first}", first).replace("{last}", last) \
        .replace("{f}", first[0]).replace("{l}", last[0])
    return f"{local}@{domain}" if re.fullmatch(r"[a-z0-9._-]+", local) else None


def _lines(page: str) -> list[str]:
    page = re.sub(r"<(script|style|noscript|svg)\b[^>]*>.*?</\1>", " ", page, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", "\n", page)
    return [re.sub(r"\s+", " ", htmllib.unescape(line)).strip() for line in text.split("\n") if line.strip()]


def team_page_people(page: str) -> list[dict]:
    """(name, title) pairs from a company team/about page: a name line followed by a leadership or recruiting title."""
    lines, out = _lines(page or ""), []
    for name, title in zip(lines, lines[1:]):
        if re.fullmatch(_NAME, name) and _real_name(name, "\0") and len(title) <= MAX_TITLE \
                and RELEVANT_TITLE.search(title):
            out.append(_person(name, title, None, "team_page", "on the company's team page"))
    return out
