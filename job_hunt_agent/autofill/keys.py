"""the questions application forms ask, as answer-bank keys.

Each Key says how a form's question is recognised: by the field's own attributes (`hints`: autocomplete tokens,
Workday automation ids, input names) first, then by its label text (`patterns`, tried in catalogue order, so the
specific ones come before the general ones). `legal` keys (work authorization, sponsorship, government, criminal,
consents, EEO, signature) are answered only from what the person saved: never a default, never a guess.
`blank_ok` keys may stay empty without asking (middle name, address line 2, extension, ...).
"""
import re
from dataclasses import dataclass

CATEGORIES = (("contact", "Contact"), ("links", "Links"), ("work", "Work and availability"),
              ("education", "Education"), ("history", "Work history (for Workday's My Experience)"),
              ("legal", "Legal and government"), ("eeo", "Voluntary self-identification (EEO)"),
              ("other", "Other questions you answered"))


@dataclass(frozen=True)
class Key:
    key: str
    label: str
    category: str
    patterns: tuple[str, ...] = ()
    hints: tuple[str, ...] = ()
    legal: bool = False
    blank_ok: bool = False


def _k(key, label, category, patterns=(), hints=(), legal=False, blank_ok=False):
    return Key(key, label, category, tuple(patterns), tuple(hints), legal, blank_ok)


# Order matters: the first key whose hint or pattern fits wins.
CATALOGUE = (
    # account fields never reach job-hunt-agent (the extension fills them from its own settings); listed so they never ask
    _k("account_password", "Job-site password", "other", [r"\bpassword\b"], ["password", "verifypassword"],
       blank_ok=True),
    _k("email_confirm", "Email (confirm)", "contact", [r"(confirm|re ?enter|verify|repeat|retype) (your )?e ?mail"]),
    _k("phone_type", "Phone device type", "contact", [r"phone (device )?type", r"device type", r"type of phone"],
       ["phone-device-type", "phonetype", "phonenumber--phonetype"]),
    _k("phone_code", "Phone country code", "contact", [r"country (phone )?code", r"phone code", r"dial(ing)? code"],
       ["countryphonecode", "country-phone-code", "phonenumber--countryphonecode"]),
    _k("phone_ext", "Phone extension", "contact", [r"\bextension\b", r"\bext\b"], ["phone-extension", "extension"],
       blank_ok=True),
    _k("preferred_name", "Preferred name", "contact", [r"preferred (first )?name", r"nick ?name"],
       ["preferredname", "preferred-name"], blank_ok=True),
    _k("middle_name", "Middle name", "contact", [r"middle (name|initial)"], ["middlename", "additional-name"],
       blank_ok=True),
    _k("first_name", "First name", "contact", [r"\bfirst name\b", r"\bgiven name\b", r"\bforename\b"],
       ["given-name", "firstname", "first_name", "legalname--firstname", "legalnamesection_firstname"]),
    _k("last_name", "Last name", "contact", [r"\blast name\b", r"\bfamily name\b", r"\bsurname\b"],
       ["family-name", "lastname", "last_name", "legalname--lastname", "legalnamesection_lastname"]),
    _k("full_name", "Full name", "contact", [r"^(your )?(full |legal |complete )*name$", r"^name (first and last|in full)",
       r"^full legal name"], ["_systemfield_name", "fullname", "full_name", "candidate-name"]),
    _k("email", "Email", "contact", [r"\be ?mail\b"], ["email", "_systemfield_email"]),
    _k("phone", "Phone", "contact", [r"\b(phone|mobile|cell|telephone)\b"],
       ["tel", "tel-national", "phone", "phone-number", "phonenumber", "phonenumber--phonenumber",
        "_systemfield_phone"]),
    _k("address2", "Address line 2", "contact", [r"address (line )?2", r"\bapt\b", r"\bsuite\b", r"apartment"],
       ["address-line2", "addressline2", "address--addressline2", "addresssection_addressline2"], blank_ok=True),
    _k("address1", "Street address", "contact", [r"address (line )?1", r"street( address)?",
       r"^(home |mailing |current )?address$"],
       ["address-line1", "addressline1", "address--addressline1", "addresssection_addressline1", "street-address"]),
    _k("zip", "ZIP / postal code", "contact", [r"\bzip\b", r"postal", r"post code", r"zip ?code"],
       ["postal-code", "postalcode", "address--postalcode", "addresssection_postalcode", "zip"]),
    _k("country", "Country", "contact", [r"^country( of residence)?$", r"country (region|of residence)",
       r"which country", r"what country"], ["country", "country-name", "countrydropdown", "address--country"]),
    _k("location", "Location (City, State)", "contact", [r"^(current )?location( city)?$",
       r"where are you (currently )?(located|based)", r"city (and|,) state", r"current (city|location)"],
       ["_systemfield_location", "location"]),
    _k("state", "State", "contact", [r"^state$", r"\bstate( or |/| )province\b", r"^province$", r"^state of residence",
       r"which state", r"administrative area", r"^state ?select$", r"^(state|province|region)( (or|/) (province|region))?$",
       r"^state province region$"], ["address-level1", "countryregion", "address--countryregion",
       "addresssection_countryregion", "state", "stateselect", "administrativearea", "administrative-area"]),
    _k("city", "City", "contact", [r"^city\b", r"\bcity$", r"\btown\b", r"\blocality\b", r"\bwhat city\b", r"\bwhich city\b",
       r"\bcity of residence\b"], ["address-level2", "city", "address--city", "addresssection_city", "locality"]),
    _k("county", "County", "contact", [r"\bcounty\b"], blank_ok=True),

    _k("linkedin", "LinkedIn URL", "links", [r"linked ?in"], ["linkedin", "urls[linkedin]"]),
    _k("github", "GitHub URL", "links", [r"git ?hub"], ["github", "urls[github]"]),
    _k("twitter", "Twitter / X", "links", [r"twitter", r"\bx com\b"], blank_ok=True),
    _k("portfolio", "Portfolio / website", "links", [r"portfolio", r"personal (web ?)?site", r"^website$",
       r"web ?site( url)?$", r"\bblog\b"], ["url", "urls[portfolio]", "website"], blank_ok=True),
    _k("other_url", "Other link", "links", [r"other (web ?site|url|link)", r"additional (link|url)"], blank_ok=True),

    _k("sponsor_future", "Will you need visa sponsorship (now or in the future)?", "legal",
       [r"sponsor\w*.*\bfuture\b", r"\bfuture\b.*sponsor", r"(now|currently) or .*sponsor"], legal=True),
    _k("sponsor_now", "Do you need visa sponsorship now?", "legal", [r"sponsor", r"\bh ?1 ?b\b"], legal=True),
    # a setting, not a form question (no patterns: no field is ever classified as it)
    _k("sponsor_auto", "At companies marked Sponsors or Cap-exempt, answer the future-sponsorship question: Yes",
       "legal", blank_ok=True),
    _k("work_auth", "Are you legally authorized to work in the US?", "legal",
       [r"(legally )?(authori[sz]ed|eligible|permitted|entitled) to work", r"work authori[sz]ation",
        r"right to work", r"employment eligibility", r"legal right to work"], legal=True),
    _k("visa_status", "Current visa / immigration status", "legal",
       [r"visa (status|type)", r"immigration status", r"current (work )?(visa|permit)", r"work permit"], legal=True),
    _k("citizenship", "Citizenship / export control (US person)", "legal",
       [r"citizen", r"export (control|administration)", r"\bitar\b", r"u ?s person", r"permanent resident"], legal=True),
    _k("clearance", "Security clearance", "legal", [r"clearance"], legal=True),
    _k("gov_official", "Government / public official questions", "legal",
       [r"government", r"public official", r"politically exposed", r"federal (employee|agency)", r"\bgovt\b",
        r"civil serv", r"elected"], legal=True),
    _k("criminal", "Criminal history", "legal", [r"convicted", r"felony", r"criminal", r"misdemeanor"], legal=True),
    _k("noncompete", "Non-compete / non-solicit agreement", "legal",
       [r"non ?compete", r"non ?solicit", r"restrictive covenant", r"(employment|confidential\w*) agreement"],
       legal=True),
    _k("background_check", "Consent to a background check", "legal", [r"background (check|investigation|screen)"],
       legal=True),
    _k("drug_test", "Consent to a drug test", "legal", [r"drug (test|screen)", r"substance"], legal=True),
    _k("age18", "Are you 18 or older?", "legal", [r"\b18\b.*(year|age|old)", r"(age|old) of 18", r"legal age",
       r"at least 18"], legal=True),
    _k("prev_worked", "Have you worked for this company before?", "work",
       [r"(previously|ever|formerly|before) (been )?(work|employ)", r"(former|previous|current or former) (employee|worker)",
        r"worked (for|at|with) .* (before|previously)", r"(are|were) you .* (an )?employee of",
        r"worked (for|at|with) company (in the past|before)\b"],
       ["candidateispreviousworker", "previousworker"]),
    _k("related_employee", "Related to anyone who works there?", "work",
       [r"\brelat(ed|ive)\b", r"family member", r"know (anyone|someone) (who )?(works|employed)"]),
    _k("referred", "Were you referred by an employee?", "work", [r"\breferr?(ed|al)\b(?!.*source)"]),
    _k("how_heard", "How did you hear about us?", "work",
       [r"how did you (hear|find|learn|come)", r"where did you (hear|find|see|learn)",
        r"^(referral |job |candidate )?source$", r"how .* (hear|find out) about"],
       ["source", "sourceprompt", "source--source"]),
    _k("salary", "Salary expectation", "work", [r"salary", r"compensation", r"pay (expectation|requirement|range)",
       r"desired (pay|rate|wage)", r"expected (base )?(pay|salary|rate)"]),
    _k("start_date", "Earliest start date", "work", [r"start date", r"(available|able) to start",
       r"earliest (date|start)", r"when (can|could|would) you (start|begin|join)", r"availability date",
       r"date available"]),
    _k("notice", "Notice period", "work", [r"notice period", r"weeks? notice"]),
    _k("relocate", "Willing to relocate?", "work", [r"relocat"]),
    _k("onsite", "Willing to work onsite / hybrid?", "work",
       [r"on ?site", r"in (the |our )?office", r"\bhybrid\b", r"commut", r"days (a|per) week", r"in person"]),
    _k("travel", "Willing to travel?", "work", [r"\btravel\b"]),
    _k("years_exp", "Years of experience", "work", [r"years of (\w+ )?(\w+ )?experience", r"how many years"]),
    _k("current_company", "Current / most recent employer", "work",
       [r"current (company|employer)", r"(most recent|present|latest) (company|employer)"], ["org", "organization"]),
    _k("current_title", "Current / most recent job title", "work",
       [r"current (job )?(title|position|role)", r"(most recent|present) (job )?title"]),
    _k("cover_letter", "Cover letter", "work", [r"cover letter"], ["cover_letter"], blank_ok=True),
    _k("sms_consent", "Text message (SMS) consent", "legal", [r"text messag", r"\bsms\b"], legal=True, blank_ok=True),
    _k("terms", "Agree to terms / privacy notice / certify the application", "legal",
       [r"\bi (have read|agree|acknowledge|certify|consent|understand|attest|accept)", r"terms (and|&) conditions",
        r"privacy (policy|notice|statement)", r"\bterms of (use|service)\b", r"\battest", r"true and (correct|complete)"],
       ["createaccountcheckbox", "agreementcheckbox", "termsandconditions"], legal=True),

    _k("degree", "Highest degree", "education", [r"\bdegree\b", r"level of education",
       r"highest (level of )?education", r"education level"], ["degree", "education--degree"]),
    _k("field_of_study", "Field of study / major", "education",
       [r"field of study", r"\bmajor\b", r"discipline", r"area of study", r"concentration"], ["field-of-study"]),
    _k("school", "School / university", "education", [r"\bschool\b", r"universit", r"college", r"institution"],
       ["school", "schoolitem", "school-name"]),
    _k("grad_date", "Graduation date (MM/YYYY)", "education", [r"graduat", r"completion date", r"degree (date|year)"]),
    _k("gpa", "GPA", "education", [r"\bgpa\b", r"grade point"], ["gpa"], blank_ok=True),

    _k("signature_date", "Today's date (filled automatically)", "eeo", [r"today ?s date", r"date signed",
       r"^date$", r"signature date"], ["datesignedon", "todaysdate"]),
    _k("signature_name", "Signature (your full name)", "eeo", [r"signature", r"sign(ed)? (your )?name",
       r"type your (full )?(legal )?name", r"electronic(ally)? sign", r"^name of (applicant|candidate)$"], legal=True),
    _k("hispanic", "Hispanic or Latino?", "eeo", [r"hispanic", r"latin[ox]"], legal=True),
    _k("race", "Race / ethnicity", "eeo", [r"\brace\b", r"ethnicit"], ["ethnicitydropdown", "ethnicitymultiselect"],
       legal=True),
    _k("gender", "Gender", "eeo", [r"\bgender\b", r"\bsex\b"], ["gender"], legal=True),
    _k("pronouns", "Pronouns", "eeo", [r"pronoun"], legal=True, blank_ok=True),
    _k("orientation", "Sexual orientation", "eeo", [r"sexual orientation", r"\blgbt"], legal=True),
    _k("transgender", "Transgender", "eeo", [r"transgender"], legal=True),
    _k("veteran", "Veteran status", "eeo", [r"veteran", r"military", r"armed forces", r"\bservice member"],
       ["veteranstatus"], legal=True),
    _k("disability", "Disability status", "eeo", [r"disabilit"], ["disabilitystatus"], legal=True),
)
BY_KEY = {k.key: k for k in CATALOGUE}
_COMPILED = tuple((k, tuple(re.compile(p) for p in k.patterns)) for k in CATALOGUE)
# derived from other answers when a form asks them: never stored, never their own row on the Answers page
DERIVED = frozenset({"email_confirm", "signature_date", "account_password"})
_LABEL_NOISE = re.compile(r"\((required|optional|mm/yyyy|mm/dd/yyyy)\)|\brequired\b|\*|please select( one)?|select one"
                          r"|\(select all that apply\)|select all that apply")
_SPACES = re.compile(r"[^a-z0-9]+")


_FIRM_WORDS = {"services", "service", "group", "technologies", "technology", "systems", "solutions", "federal", "international",
               "global", "american", "general", "united", "national", "holdings", "corporation", "company", "partners",
               "consulting", "software", "health", "financial", "capital", "digital", "networks", "the"}


def norm(text: str | None, company: str | None = None) -> str:
    """A question as a stable key: lower case, words only, the company's own name as 'company'."""
    out = _SPACES.sub(" ", _LABEL_NOISE.sub(" ", (text or "").lower())).strip()
    firm = _SPACES.sub(" ", (company or "").lower()).strip()
    if len(firm) > 1:
        out = re.sub(rf"\b{re.escape(firm)}\b", "company", out)
        for word in firm.split():                       # "Accenture" for "Accenture Federal Services"
            if len(word) >= 5 and word not in _FIRM_WORDS:
                out = re.sub(rf"\b{re.escape(word)}\b", "company", out)
                break
    return out[:300]


_BY_CHOICES = (("disability", re.compile(r"\bdisabilit")), ("veteran", re.compile(r"protected veteran|\bveteran\b")),
               ("race", re.compile(r"\basian\b.*|\bwhite\b|black or african")), ("gender", re.compile(r"^(fe)?male$")))


def by_choices(options) -> Key | None:
    """An EEO question asked with a bare label ("Please check one of the boxes below:"): its choices say which."""
    texts = [norm(o) for o in (options or [])][:80]
    for key, rx in _BY_CHOICES:
        if sum(1 for t in texts if rx.search(t)) >= (2 if key in ("race", "gender") else 1):
            return BY_KEY[key]
    return None


# someone else's details (a referrer's email, an emergency contact's phone): never the user's own contact answers
OTHER_PERSON = re.compile(r"\b(referr\w*|refer(red)? (you|by)|recruiter|emergency|reference|manager|supervisor|spouse|"
                          r"relative|next of kin|guardian|employee who)\b")
# legal wording: only a legal key may answer it ("In which country are you legally authorized to work?")
LEGAL_WORDS = re.compile(r"\b(authori[sz]\w*|eligib\w*|sponsor\w*|visa|citizen\w*|right to work|work permit)\b")


def classify(label: str | None, hints=()) -> Key | None:
    """The bank key a form field asks for: its attributes first, then its label; None when it is none of them."""
    text = norm(label)
    other = bool(OTHER_PERSON.search(text))
    legal_words = bool(LEGAL_WORDS.search(text))

    def allowed(key: Key) -> bool:
        return not (other and key.category == "contact") and (key.legal or not legal_words)
    words = {str(h).strip().lower() for h in (hints or []) if str(h).strip()}
    if words:
        for key in CATALOGUE:
            if words.intersection(key.hints) and allowed(key):
                return key
    if not text:
        return None
    for key, patterns in _COMPILED:
        if any(p.search(text) for p in patterns) and allowed(key):
            return key
    return None
