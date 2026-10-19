"""Deterministic signals parsed once at ingest: US location, salary, years, team, talent pool, dealbreakers,
employment type (full-time / contract / contract-to-hire) and staffing-agency resume-harvesting signals."""
import re

US_STATES = (
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM "
    "NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC"
).split()
# Country-level US mentions: a segment naming one of these is US-eligible even when it also names another country
# ('United States & Canada', 'Remote - US or Canada'). "US" must be upper case and not part of "Non-US".
_US_COUNTRY = re.compile(r"(?i)(?<!non-)(?<!non )\b(?:united states|usa|us[- ]remote|remote[- ,(]*us|"
                         r"u\.s\.(?:a\.)?)(?![a-z])|(?<![A-Za-z-])(?-i:US)(?![A-Za-z])")
_US_CITIES = re.compile(
    r"(?i)\b(puerto rico|san juan, pr|new york|san francisco|seattle|boston|austin|chicago|los angeles|denver|atlanta|"
    r"washington,? d\.?c|bay area|silicon valley|mountain view|palo alto|santa clara|"
    r"sunnyvale|san jose|kansas city|dallas|houston|miami|pittsburgh)\b"
)
# US cities shown without a state ('San Diego', 'Plano North'); US only when no foreign place is named
# ('Phoenix Equinox Tower 2, Hyderabad', 'Charlotte Street - MANCHESTER')
_US_CITIES_ALONE = re.compile(
    r"(?i)\b(san diego|detroit|nashville|plano|raleigh|jacksonville|santa monica|culver city|redwood city|san mateo|"
    r"boca raton|mclean|herndon|pleasanton|lehi|bethesda|long beach|beverly hills|fort lauderdale|philadelphia|"
    r"phoenix|charlotte)\b"
)
_STATE_NAMES = re.compile(
    r"(?i)\b(alabama|alaska|arizona|arkansas|california|colorado|connecticut|delaware|florida|georgia|hawaii|idaho|"
    r"illinois|indiana|iowa|kansas|kentucky|louisiana|maine|maryland|massachusetts|michigan|minnesota|mississippi|"
    r"missouri|montana|nebraska|nevada|new hampshire|new jersey|new mexico|new york|north carolina|north dakota|ohio|"
    r"oklahoma|oregon|pennsylvania|rhode island|south carolina|south dakota|tennessee|texas|utah|vermont|virginia|"
    r"washington|west virginia|wisconsin|wyoming|district of columbia)\b"
)
_STATE_ALT = "|".join(US_STATES)
_STATE_SUFFIX = re.compile(rf"(?:,\s*|\bUS,\s*|\bUS-|-\s*)({_STATE_ALT})\b|^\s*({_STATE_ALT})\s+-\s")
_CA_PROVINCE = re.compile(r",\s*(?:ON|QC|BC|AB|MB|SK|NS|NB|NL|PE|YT|NT|NU)\b")
# Non-US countries and regions. 'Remote - Bulgaria' is as non-US as 'Sofia, Bulgaria' (SS&C leak).
# Georgia is left out (a US state); "new mexico" is a US state, not Mexico.
_COUNTRIES = (
    "canada|united kingdom|uk|great britain|england|scotland|wales|northern ireland|ireland|germany|deutschland|"
    "france|netherlands|holland|belgium|luxembourg|spain|portugal|italy|switzerland|austria|poland|czech republic|"
    "czechia|slovakia|slovenia|hungary|romania|bulgaria|greece|cyprus|malta|croatia|serbia|bosnia|montenegro|"
    "north macedonia|macedonia|albania|kosovo|moldova|ukraine|belarus|russia|estonia|latvia|lithuania|finland|"
    "sweden|norway|denmark|iceland|turkey|türkiye|turkiye|israel|armenia|azerbaijan|kazakhstan|uzbekistan|"
    "india|pakistan|bangladesh|sri lanka|nepal|china|hong kong|macau|taiwan|japan|south korea|korea|mongolia|"
    "singapore|malaysia|indonesia|philippines|thailand|vietnam|viet nam|cambodia|myanmar|australia|new zealand|"
    "mexico|guatemala|honduras|el salvador|nicaragua|costa rica|panama|cuba|dominican republic|jamaica|"
    "trinidad|belize|colombia|venezuela|ecuador|peru|bolivia|chile|argentina|uruguay|paraguay|brazil|brasil|"
    "egypt|morocco|maroc|algeria|tunisia|nigeria|ghana|kenya|ethiopia|uganda|rwanda|tanzania|south africa|"
    "senegal|ivory coast|cote d'ivoire|cameroon|zambia|zimbabwe|mauritius|"
    "saudi arabia|ksa|united arab emirates|uae|qatar|kuwait|bahrain|oman|jordan|lebanon|"
    "emea|apac|latam|latin america|europe|asia|africa|middle east|oceania|european union"
)
# Countries that are also US town names ('Lebanon, PA', 'Peru, IN', 'Mexico, MO'): non-US unless a US state follows.
_US_TOWN_COUNTRIES = {"lebanon", "peru", "mexico", "poland", "denmark", "norway", "jordan", "panama", "cuba",
                      "jamaica", "macedonia", "holland", "wales", "brazil", "scotland", "malta"}
# 'IND - TN - CHENNAI', 'CAN, ON, Mississauga', 'POL-Warsaw': an upper-case ISO-3 country code opening the place
_ISO3_ABROAD = re.compile(r"(?:^\W*|(?P<tail>,\s*))(?:IND|CAN|POL|DEU|GBR|FRA|ESP|ITA|PRT|NLD|BEL|CHE|AUT|IRL|SWE|NOR|DNK|FIN|CZE|SVK|"
                          r"HUN|ROU|BGR|GRC|HRV|SRB|UKR|TUR|ISR|ARE|SAU|EGY|ZAF|NGA|KEN|CHN|JPN|KOR|TWN|SGP|MYS|PHL|"
                          r"THA|VNM|IDN|PAK|BGD|LKA|AUS|NZL|MEX|BRA|ARG|COL|CHL|PER|URY|CRI)\b")
# 'Ramos Arizpe, MX', 'Diegem, BE, 1831', 'SG, 048583', 'Remote - MX': an upper-case ISO-2 country code standing
# alone as a place part. Codes that are also US state codes (CO, DE, IN, PA ...) are never read as countries.
_ISO2_ABROAD = re.compile(r"(^|,|-)\s*(" + "|".join(sorted(
    set("MX CR BE SG NL FR GB UK ES IT PT CH AT PL CZ SE NO DK FI IE BR CN JP KR TW PH MY TH VN AU NZ CL PE UY HK "
        "IL AE SA EG ZA NG KE PK BD LK RO BG HU GR HR RS UA TR".split()) - set(US_STATES))) + r")\s*(?:,|$|\d)")
_NON_US_COUNTRY = re.compile(r"(?i)(?<!new )\b(" + _COUNTRIES + r")\b")
_NON_US_CITY = re.compile(
    r"(?i)\b(toronto|vancouver|montreal|montréal|ottawa|calgary|edmonton|winnipeg|waterloo|quebec|ontario|"
    r"british columbia|alberta|london|dublin|cork|galway|belfast|edinburgh|glasgow|manchester|cambridge, uk|"
    r"bristol|birmingham|leeds|reading|oxford|berlin|munich|hamburg|frankfurt|cologne|stuttgart|dusseldorf|"
    r"düsseldorf|paris|lyon|amsterdam|rotterdam|eindhoven|brussels|madrid|barcelona|valencia|seville|malaga|"
    r"lisbon|porto|milan|rome|turin|zurich|geneva|vienna|warsaw|warszawa|krakow|kraków|wroclaw|gdansk|prague|"
    r"budapest|bucharest|cluj|sofia|belgrade|zagreb|athens|stockholm|copenhagen|oslo|helsinki|tallinn|riga|"
    r"vilnius|wrocław|utrecht|penang|belo horizonte|kyiv|kiev|lviv|istanbul|tel aviv|haifa|yerevan|dubai|abu dhabi|riyadh|jeddah|doha|cairo|lagos|"
    r"nairobi|cape town|johannesburg|casablanca|delhi|new delhi|mumbai|bangalore|bengaluru|hyderabad|pune|"
    r"chennai|noida|gurgaon|gurugram|kolkata|indore|ahmedabad|kochi|trivandrum|bhubaneswar|coimbatore|"
    r"karnataka|telangana|maharashtra|karachi|lahore|islamabad|dhaka|colombo|tokyo|osaka|seoul|seongnam|"
    r"shanghai|beijing|shenzhen|hangzhou|chengdu|taipei|manila|makati|taguig|cebu|jakarta|kuala lumpur|"
    r"bangkok|hanoi|ho chi minh|sydney|melbourne|brisbane|perth|auckland|são paulo|sao paulo|"
    r"rio de janeiro|buenos aires|bogota|bogotá|medellin|medellín|santiago|lima|montevideo|quito|guadalajara|"
    r"monterrey|mexico city|ciudad de mexico|"
    r"billund|bratislava|ljubljana|canary wharf|puteaux|oberkochen|immenstaad|farringdon|welwyn|hilversum|lucknow|"
    r"netanya|nicosia|lausanne|"                          # abroad places shown as "maybe US"
    r"capetown|trento|semarang|singapore|wiesloch|walldorf|haute garonne|beit haemek|wien|johor|taleigao|(?-i:Goa)|"
    r"toulouse|petaling jaya|weinheim|thane|ariana|gütersloh|guetersloh|mönchengladbach|monchengladbach|ramat gan|"
    r"herzliya|wiesbaden|ditzingen|groningen|yokohama|zug|basel|quezon city|kronberg|bundesweit|carlow|cdmx|"
    r"marseille|amman|distrito capital|curitiba|recife|leverkusen|frankenthal|gijon|albacete|eastleigh|"
    r"san salvador|sao jose dos campos|assago|thalheim|puchong|moscavide|heredia|sant cugat|hørsholm|"
    r"napoli|(?<!new )ulm|cundinamarca|cudinamarca|ebene|lodz|łódź|oberoi garden city|cambridgeshire|schwalbach|"
    r"kato scholari|thessaloniki|merida|canadian head office)\b|東京|(?-i:\bEU\b)"
)
# Foreign city names that are also US towns ('Dublin, CA', 'Warsaw, IN', 'Geneva, IL', 'Brisbane, CA'). With a US
# state they are US even when the state code is also a country code; alone, the newer ones are unknown, not abroad.
_US_TOWN_CITIES = {"waterloo", "london", "dublin", "belfast", "manchester", "bristol", "birmingham", "reading",
                   "oxford", "berlin", "paris", "valencia", "rome", "vienna", "warsaw", "athens", "cairo", "lima",
                   "santiago", "melbourne", "perth", "vancouver", "delhi", "cambridge", "ontario", "quebec", "geneva",
                   "brisbane", "glasgow", "leeds", "milan", "edinburgh"}
_BARE_UNKNOWN_TOWNS = _US_TOWN_CITIES - {"waterloo", "london", "dublin", "belfast", "manchester", "berlin", "paris",
                                         "melbourne", "vancouver", "delhi", "edinburgh", "quebec", "ontario", "warsaw"}
# US state codes that are also the ISO code of a country: 'Stuttgart, DE', 'Trivandrum, IN', 'Tel Aviv, IL'.
_COLLIDING_CODES = {"DE", "IN", "AR", "IL", "CO", "ID", "MA", "AL", "MT", "CA", "TN"}     # TN: Tamil Nadu
# 'city, region, country' (SuccessFactors): a German / Indian region before "DE" / "IN" makes it the country,
# not Delaware / Indiana ('Leverkusen, Nordrhein-Westfalen, DE'); a US address has its city there ('…, Newark, DE')
_DE_REGIONS = ("baden-württemberg|bayern|bavaria|berlin|brandenburg|bremen|hamburg|hessen|hesse|mecklenburg-vorpommern|"
               "niedersachsen|lower saxony|nordrhein-westfalen|north rhine-westphalia|rheinland-pfalz|saarland|sachsen|"
               "saxony|sachsen-anhalt|schleswig-holstein|thüringen|thuringia|münchen|muenchen|munich|frankfurt|köln|"
               "cologne|stuttgart|düsseldorf|bw|by|be|bb|hb|hh|the user|mv|ni|nw|rp|sl|sn|st|sh|th")
_IN_REGIONS = ("karnataka|telangana|telengana|maharashtra|uttar pradesh|haryana|tamil nadu|new delhi|delhi|gujarat|"
               "west bengal|kerala|andhra pradesh|rajasthan|bengaluru|bangalore|mumbai|pune|hyderabad|chennai|gurgaon|"
               "gurugram|noida|kolkata|trivandrum|thiruvananthapuram|kochi|ahmedabad|ka|ts|tg|mh|up|hr|tn|dl|gj|wb|kl|"
               "ap|rj")
_REGION_THEN_CODE = re.compile(rf"(?i),\s*(?:(?:{_DE_REGIONS})\s*,\s*DE|(?:{_IN_REGIONS})\s*,\s*IN)\s*$")
_CA_PROVINCE_NAME = re.compile(r"(?i),\s*(?:ontario|quebec|british columbia|alberta|manitoba|saskatchewan|"
                               r"nova scotia|new brunswick|newfoundland)\b")
_LOCATION_SPLIT = re.compile(r"[;|/]")
# 'Toronto, ON | Hybrid', 'Warsaw - Poland; Digital': a work-style segment names no place, so it can't keep a clearly
# foreign location at "maybe US" ('Remote' can: it may include the US)
_MODE_ONLY = re.compile(r"(?i)^\W*(?:hybrid|digital|on-?site|in-?office|télécommande|full[- ]time)\W*$")
_PART_SPLIT = re.compile(r"\s+(?:or|OR|and|AND)\s+(?=[A-Za-z])|\s*&\s*")
US_COUNTRY_CODES = {"us", "usa", "united states", "united states of america"}

_AMOUNT = r"(?:\$\s?(?:\d{2,3}(?:,\d{3})+(?:\.\d\d)?|\d{2,3}(?:\.\d+)?\s?[kK])\b|\b\d{2,3}(?:,\d{3})+(?:\.\d+)?\s?USD\b)"
_MONEY_RANGE = re.compile(rf"({_AMOUNT})(?:\s*(?:USD|per year|/yr|annually))?\s*(?:-|–|—|to)\s*({_AMOUNT})")
_NUM = r"(?<![\d.])(\d{1,2})(?:\.\d+)?"                   # '7.5 years' -> 7, never 5
_YEARS = re.compile(                                  # '5+ years', '5-8 years', '5 to 8 yrs', 'minimum of 5 years'
    rf"(?i)\b(?:minimum (?:of )?|at least |a minimum of )?{_NUM}\s*\+?\s*(?:(?:-|–|to)\s*\d{{1,2}}\s*\+?\s*)?"
    r"(?:or more\s+)?(?:years?|yrs?)\b(?=(?P<tail>[^.\n]{0,70}))"
)
_EXPERIENCE = re.compile(r"(?i)\bexperience|\bexp\b|\bprofessional|\bindustry|\bworking|\bhands[- ]on|\bbuilding|"
                         r"\bdeveloping|\bwork(?:ing)? (?:in|with|on)\b|\bin (?:a |an )?(?:\w+ ){0,3}(?:role|position|engineering)")
_LEAD_IN = re.compile(r"(?i)\b(?:experience|exp)\s*(?:of|:|-|–|\()?\s*"            # 'Extensive experience (typically '
                      r"(?:typically|usually|generally|approximately|approx\.?|about|around|roughly)?\s*$")
# 'N+ years of Python' / '3+ years in data analytics': a marked requirement ('+', a range, 'at least') counts even
# without the word 'experience' ('18+ years of age' never does)
_MARKED = re.compile(r"(?i)\+|[-–]|\bto\b|\bor more\b|\bminimum\b|\bat least\b")
_OF_IN = re.compile(r"(?i)^\s*(?:of|in|as|with)\b(?!\s+age\b)")
# 'Up to three years of experience' is a ceiling (0-3 years), not a requirement of 3
_UPPER_BOUND = re.compile(r"(?i)\b(?:up to|less than|fewer than|under|no more than|not more than|at most|"
                          r"a maximum of|maximum of|max\.?)\s*$")
_BULLET = re.compile(r"^\s*[-*\u2022\u00b7]")
_NICE = re.compile(r"(?i)\b(?:preferred|nice[- ]to[- ]have|bonus|a plus|great if|ideally|desired)\b")
_NUMBER_WORDS = {w: str(i) for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve "
                                                "thirteen fourteen fifteen".split())}
_WORD = "|".join(_NUMBER_WORDS)
_WORD_PAREN = re.compile(rf"(?i)\b(?:{_WORD})\s*\((\d{{1,2}})\)")                     # 'five (5) years' -> '5 years'
_WORD_YEARS = re.compile(rf"(?i)\b({_WORD})\b(?=\s*(?:\+\s*)?(?:(?:-|–|to|or more)\s*(?:{_WORD}|\d+)?\s*)?"
                         r"(?:years?|yrs?)\b)")                                       # 'Two to three years' -> '2 to 3'
# 'We are seeking a Staff Engineer' under a 'Senior Engineer' title (Shield AI): a JD that names a Staff / Principal
# hire and states no years reads as STAFF_ASSUMED_YEARS, above both profiles' max_required_years
STAFF_ASSUMED_YEARS = 8
_STAFF_SOUGHT = re.compile(r"(?i)\b(?:seeking|looking for|hiring|searching for)\s+(?:an?\s+)?(?:(?:highly\s+)?"
                           r"(?:experienced|talented|skilled|motivated|exceptional)\s+)?"
                           r"(?:staff|principal|senior staff|distinguished)\b(?!\s+(?:member|of)\b)")
_PHD = re.compile(r"(?i)\b(ph\.?\s?d|doctorate|doctoral)\b")
_APOS = "['’]?"
_DEGREE = re.compile(                                 # which degree path a years requirement belongs to
    r"(?i)(?P<phd>\bph\.?\s?d\b|\bdoctorate\b|\bdoctoral\b)"
    rf"|(?P<ms>\bmaster{_APOS}s\b|\bmasters?\s+(?:degree|of)\b|\bm\.\s?sc?\.?(?![a-z])|\bmba\b|"
    r"\b(?:advanced|graduate|post-?graduate)\s+degree\b|(?-i:\bMS\b))"
    rf"|(?P<bs>\bbachelor{_APOS}s\b|\bbachelors?\s+(?:degree|of)\b|\bb\.\s?[sa]\.?(?![a-z])|"
    r"\bundergraduate\s+degree\b|\bb\.?\s?tech\b|(?-i:\bB[SA]\b))"
    rf"|(?P<assoc>\bassociate{_APOS}s?\s+degree\b)"
)
_DEGREE_SUBSTITUTE = re.compile(r"(?i)\b(?:equivalent|in lieu|instead of a degree|without a degree)\b")
# '<degree> or equivalent with 6+ years': the years come on top of the degree (or its equivalent), not instead of it
_EQUIVALENT_PLUS = re.compile(r"(?i)\bequivalent(?:\s+degree)?\s+(?:with|and|plus|\+)\s+"
                              r"(?:at least\s+|a minimum of\s+)?$")
_OR_END = re.compile(r"(?i)\bor\W*$")
_OR_START = re.compile(r"(?i)^\W*(?:or|in the alternative|alternatively)\b")    # Stripe: "In the alternative, ..."
RUNG_ORDER = ("ms", "bs", None, "phd")       # the rung of a degree ladder that applies to an MS holder
# A line is split into clauses before the degree path is read: Amazon JDs put every bullet on one '<br/>' line.
_CLAUSE_SPLIT = re.compile(r"<br\s*/?>|;|\s[\u2022\u00b7]\s|(?<=[a-z0-9)])\.\s+(?=[A-Z])")
_TEAM = re.compile(r"(?i)\b(report(?:ing)? (?:directly )?to (?:the )?[A-Z]|join (?:the|our) [A-Z][\w&/ -]{2,40} team)")
_TALENT_POOL = re.compile(
    r"(?i)\b(talent (?:pool|community|network)|future opportunit(?:y|ies)|general application|"
    r"expression of interest|general interest|evergreen)\b"
)
_NEGATORS = re.compile(r"(?i)\b(no|not|without|never|nor|n't)\b")
NEGATION_WINDOW_TOKENS = 6
# Words that may sit between the match and "not required"; a clearance word there ("(TS not required)") means the
# negation is about that other clearance, and "not required to be TS" / "at start" / "until" qualify, not negate.
_FILLER = r"(?:(?!(?:ts|sci|secret|top|polygraph)\b)[\w']+\W+)"
_NOT_REQUIRED_AFTER = re.compile(rf"(?i)\W*{_FILLER}{{0,3}}?(?:(?:is|are)\s+(?:not|never)|(?:is|are)n[’']t|not|never)\s+"
                                 r"(?:required|needed|necessary|a\s+requirement)(?!\s+(?:to|at|until|initially|upon)\b)"
                                 rf"|\W*{_FILLER}{{0,2}}?(?:is|are)\s+optional"
                                 r"|\W*(?:required|needed)\s*\?\s*no\b")     # PwC: 'Government Clearance Required? No'


def is_us(location: str | None, country: str | None = None) -> bool | None:
    """True/False when the location is clearly US/non-US, None when unknown (e.g. bare 'Remote').

    The ATS country field decides, except that a US country field loses to a location whose every part is clearly
    abroad and names a foreign country or region: Workday reported 'United States of America' for SS&C's
    'Remote - Poland; Remote - Bulgaria'. A bare 'Manchester' with a US country field stays US."""
    verdict = _location_is_us(location)
    if not country:
        return verdict
    if country.strip().lower() not in US_COUNTRY_CODES:
        return False
    return not (verdict is False and _NON_US_COUNTRY.search(location or ""))


def _location_is_us(location: str | None) -> bool | None:
    """Any US segment -> True; every segment abroad -> False; else unknown. Segments are ';', '|' or '/' parts."""
    verdicts = [_segment_is_us(seg) for seg in _LOCATION_SPLIT.split(location or "")
                if seg.strip() and not _MODE_ONLY.match(seg)]
    if any(v is True for v in verdicts):
        return True
    if verdicts and all(v is False for v in verdicts):
        return False
    return None


def _segment_is_us(segment: str) -> bool | None:
    """'United States & Canada' or 'Berlin or Palo Alto, CA' is US-eligible when any alternative is US; the other
    alternatives say abroad only when one of them is clearly abroad."""
    verdicts = [_part_is_us(part) for part in _PART_SPLIT.split(segment) if part.strip()]
    if any(v is True for v in verdicts):
        return True
    return False if any(v is False for v in verdicts) else None


def _part_is_us(part: str) -> bool | None:
    """One place. A country-level US mention wins; then a named foreign country (unless a US town of that name:
    'Lebanon, PA'); then Canadian provinces ('Toronto, ON, CA' looks like a US state suffix); then foreign cities,
    which are US when a US state is named ('Dublin, CA', 'London, Kentucky') unless that state code is the
    country's own code ('Munich, DE', 'New Delhi, IN')."""
    part = part.replace("_", " ")                       # 'Shanghai_Tianshan': a word boundary, not a letter
    if _US_COUNTRY.search(part):
        return True
    if _REGION_THEN_CODE.search(part):
        return False
    suffix = _STATE_SUFFIX.search(part)
    state_name = _STATE_NAMES.search(part)
    country = _NON_US_COUNTRY.search(part)
    if country:
        town = country.group(1).lower() in _US_TOWN_COUNTRIES
        if not (town and (suffix or (state_name and country.start() == len(part) - len(part.lstrip())))):
            return False
    iso3 = _ISO3_ABROAD.search(part)            # 'Rehovot,ISR'; 'Durham, NC, … Toronto, CAN' keeps its US state
    if _CA_PROVINCE.search(part) or _CA_PROVINCE_NAME.search(part) or (
            iso3 and not (iso3.group("tail") and (suffix or state_name))):
        return False
    code = _ISO2_ABROAD.search(part)            # 'San Jose, CR' is Costa Rica; 'Dallas-Fort Worth - JP' a site code
    if code and not (suffix or state_name or (code.group(1) == "-" and _US_CITIES.search(part))):
        return False
    city = _NON_US_CITY.search(part)
    if city:
        name = (city.group(1) or city.group(0)).lower()
        if _US_CITIES.search(part) or state_name:
            return True
        if suffix:
            code = suffix.group(1) or suffix.group(2)
            return code not in _COLLIDING_CODES or name in _US_TOWN_CITIES
        return None if name in _BARE_UNKNOWN_TOWNS else False
    if suffix or state_name or _US_CITIES.search(part) or _US_CITIES_ALONE.search(part):
        return True
    return None


def _money_value(token: str) -> float:
    token = token.lower().replace(",", "").replace(" ", "").replace("$", "").replace("usd", "")
    return float(token[:-1]) * 1000 if token.endswith("k") else float(token)


def parse_salary(text: str | None) -> tuple[float | None, float | None, str | None]:
    """First USD *range* like '$120,000 - $150,000', '$257K – $335K' or '148,000 USD - 235,750 USD'.
    Single amounts (bonuses, stipends) are ignored; ranges below $20k (hourly) are ignored."""
    for low, high in _MONEY_RANGE.findall(text or ""):
        lo, hi = _money_value(low), _money_value(high)
        if lo >= 20_000 and hi >= lo:
            return lo, hi, "USD"
    return None, None, None


def _degree_path(clause: str, start: int, head: str, tail: str) -> str | None:
    """The degree rung one years requirement belongs to: 'phd' / 'ms' / 'bs' / 'assoc', 'alt' for a degree
    substitute ('or equivalent (minimum 12 years)', 'in lieu of a degree, 12+ years'), else None. A degree named
    right after the years wins ('2 years with a Master's'), else the nearest one before them in the clause."""
    if _PHD.search(head[-30:]) or _PHD.search(tail):          # 'PhD + 2 years' / '2 years with a PhD'
        return "phd"
    if _DEGREE_SUBSTITUTE.search(head) and not _EQUIVALENT_PLUS.search(head):
        return "alt"
    after = _DEGREE.search(tail)
    before = list(_DEGREE.finditer(clause[:start]))
    found = after or (before[-1] if before else None)
    return found.lastgroup if found else None


def _clause_mentions(clause: str) -> list[tuple[int, str | None]]:
    """(years, degree rung) for every 'N years ... experience' requirement in one clause; a bare 'N+ years' counts
    right after a degree ('a Master's degree and 6+ years')."""
    out = []
    clause = _WORD_YEARS.sub(lambda m: _NUMBER_WORDS[m.group(1).lower()], _WORD_PAREN.sub(r"\1", clause))
    for found in _YEARS.finditer(clause):
        n = int(found.group(1))
        before = clause[max(0, found.start() - 45):found.start()]
        head = re.split(r",|;|\bor\b", before)[-1]
        tail = re.split(r",|;|\bor\b|\band\b", found["tail"], maxsplit=1)[0]
        marked = bool(_MARKED.search(found.group(0)) and _OF_IN.search(found["tail"]))
        if _UPPER_BOUND.search(before):
            continue
        if 0 < n <= 20 and (_EXPERIENCE.search(found["tail"]) or _LEAD_IN.search(before) or _DEGREE.search(head)
                            or marked):
            out.append((n, _degree_path(clause, found.start(), head, tail)))
    return out


def _rung_years(group: list[tuple[int, str | None]]) -> list[int]:
    """One group of alternatives joined by 'or' (a clause, or clauses chained by a trailing / leading 'or'): the
    years of the rung an MS holder takes (Master's, else Bachelor's, else the no-degree wording, else PhD), the
    largest number within that rung. Associate's and degree-substitute rungs never apply to a degree holder."""
    for rung in RUNG_ORDER:
        years = [n for n, path in group if path == rung]
        if years:
            return [max(years)]
    return []


def _year_groups(lines: list[str]) -> list[list[tuple[int, str | None]]]:
    groups, joined = [], False
    for line in lines:
        for clause in _CLAUSE_SPLIT.split(line):
            mentions = _clause_mentions(clause)
            if groups and (joined or _OR_START.search(clause)):
                groups[-1] += mentions
            else:
                groups.append(mentions)
            joined = bool(_OR_END.search(clause))
    return groups


def min_years(text: str | None) -> int | None:
    """The years of experience a JD requires: the LARGEST required 'N years ... experience' ('1+ year of
    data modeling' beside 'At least 4 years of application development' must not read as 1). Ranges count their low
    end ('5-8 years' -> 5). Degree ladders count the rung an MS holder takes, since both profiles hold an MS and
    neither a PhD: 'BS and 6+ years ..., a Master's degree and 1-3 years' -> 1, 'PhD plus 5+ years, or a Master's
    degree plus 10+ years' -> 10. Years in preferred / nice-to-have lines count only when no required years exist,
    and requirement bullets under headings the section reader skips ('🙌 You should apply if:' after 'About Wayve')
    only when nothing else states years. A JD stating no years that seeks a Staff / Principal hire reads as
    STAFF_ASSUMED_YEARS. None when the JD states no years."""
    from .resume.ats import PREFERRED, REQUIRED, weighted_lines     # late import: resume.ats imports lexicon
    lines = weighted_lines(text or "")
    for chosen in ([ln for w, ln in lines if w == REQUIRED], [ln for w, ln in lines if w == PREFERRED],
                   _skipped_requirement_bullets(lines, REQUIRED, PREFERRED)):
        years = [n for group in _year_groups(chosen) for n in _rung_years(group)]
        if years:
            return max(years)
    return STAFF_ASSUMED_YEARS if _STAFF_SOUGHT.search(text or "") else None


def _skipped_requirement_bullets(lines: list[tuple[float, str]], *kept: float) -> list[str]:
    """Bullets of skipped sections, minus those under a nice-to-have heading ('It would be great if you also have:')."""
    out, nice = [], False
    for weight, line in lines:
        if weight in kept:
            continue
        if not _BULLET.match(line):
            nice = bool(_NICE.search(line))
        elif not nice and not _NICE.search(line):
            out.append(line)
    return out


# ---------- employment type ----------

FULL_TIME, CONTRACT, CONTRACT_TO_HIRE, PART_TIME, INTERNSHIP = "full_time", "contract", "contract_to_hire", \
    "part_time", "internship"
EMPLOYMENT_LABELS = {FULL_TIME: "Full-time", CONTRACT: "Contract", CONTRACT_TO_HIRE: "Contract-to-hire",
                     PART_TIME: "Part-time", INTERNSHIP: "Internship"}
_C2H = re.compile(r"(?i)\bcontract[- ]to[- ](?:hire|perm(?:anent)?)\b|\bc2h\b|\btemp(?:orary)?[- ]to[- ](?:hire|perm)")
_CONTRACT_RAW = re.compile(r"(?i)\b(contract(?:or)?|temp(?:orary)?|fixed[- ]term|freelance|consultant|seasonal)\b")
_CONTRACT_TEXT = re.compile(
    r"(?i)\bw-?2 contract|\bcontract (?:role|position|assignment|engagement|opportunity|duration|length|term)\b"
    r"|\b\d{1,2}\s*(?:\+\s*)?(?:-|–|to)?\s*(?:\d{1,2}\s*)?months?\s*(?:\+\s*)?(?:contract|engagement|assignment)\b"
    r"|\bduration\s*:\s*\d{1,2}\s*(?:\+\s*)?months?|\bcontract\s*\(\s*\d{1,2}\s*months?|\b1099\b"
    r"|\bcorp[- ]to[- ]corp\b|\bc2c\b|^\s*contract\s*$|\(contract\)|\bcontract\s*[-–:|]\s*remote\b")
_FULL_RAW = re.compile(r"(?i)\b(full[- _]?time|fulltime|permanent|regular|salaried)\b")


def employment_type(raw: str | None, title: str | None = "", text: str | None = "") -> str | None:
    """Normalize the ATS field (Lever commitment, Ashby employmentType, Workday timeType, SmartRecruiters
    typeOfEmployment, Greenhouse metadata) and the JD: contract-to-hire beats contract beats the raw full-time
    field (ATS forms default to 'Full-time' even for agency contracts). None when nothing says."""
    raw_text = (raw or "").replace("_", " ")
    head = f"{title or ''}\n{(text or '')[:4000]}"
    if _C2H.search(raw_text) or _C2H.search(head):
        return CONTRACT_TO_HIRE
    if _CONTRACT_RAW.search(raw_text) or _CONTRACT_TEXT.search(head):
        return CONTRACT
    if re.search(r"(?i)\bintern(ship)?\b", raw_text):
        return INTERNSHIP
    if re.search(r"(?i)\bpart[- _]?time\b", raw_text):
        return PART_TIME
    if _FULL_RAW.search(raw_text):
        return FULL_TIME
    return None


# ---------- staffing-agency resume harvesting ----------

_UNNAMED_CLIENT = re.compile(r"(?i)\b(?:our|my|a) (?:direct |esteemed |fortune[- ]\d+ )?client\b|\bend[- ]client\b|"
                             r"\bimplementation partner\b|\bprime vendor\b|\bclient (?:is|located) (?:in|seeking)\b")
_MULTIPLE_OPENINGS = re.compile(r"(?i)\b(?:multiple|several|various|numerous) (?:openings|positions|roles|requirements|"
                                r"opportunities|vacancies)\b|\bmass hiring\b|\bbulk hiring\b")
_VENDOR = re.compile(r"(?i)\bc2c\b|\bcorp[- ]to[- ]corp\b|\bh-?1b transfer\b|\bw-?2 only\b|\b(?:usc|gc)\s*/\s*(?:gc|usc)\b|"
                     r"\bbill rate\b|\bpay rate\s*:|\$\s?\d{2,3}\s*/\s*h(?:ou)?r\b|\bper hour on w-?2\b|"
                     r"\bimmediate (?:joiners?|start) (?:only|required)\b|\bsubmit (?:your|updated) (?:resume|cv) to\b")


def staffing_signals(text: str | None) -> int:
    """How many of the 3 resume-harvesting signal kinds a post shows: an unnamed client ('our client', 'end client',
    'implementation partner'), 'multiple openings', vendor-only boilerplate (C2C, bill rate, H1B transfer, W2 only).
    A contract role from a named company scores 0; two kinds or more is treated as harvesting by the scorer."""
    body = text or ""
    return sum(bool(rx.search(body)) for rx in (_UNNAMED_CLIENT, _MULTIPLE_OPENINGS, _VENDOR))


def has_named_team(text: str | None, team: str | None = None) -> bool:
    if team and team.strip() and team.strip().lower() not in {"engineering", "general", "other", "n/a"}:
        return True
    return bool(_TEAM.search(text or ""))


def is_talent_pool(title: str | None, text: str | None) -> bool:
    return bool(_TALENT_POOL.search(title or "")) or bool(_TALENT_POOL.search((text or "")[:600]))


def _negated(text: str, start: int, end: int | None = None) -> bool:
    preceding = re.findall(r"[\w']+", text[max(0, start - 80):start])[-NEGATION_WINDOW_TOKENS:]
    if any(_NEGATORS.fullmatch(tok) or tok.lower().endswith("n't") for tok in preceding):
        return True
    following = re.split(r"[.;\n]", text[end:end + 80], maxsplit=1)[0] if end is not None else ""
    return bool(_NOT_REQUIRED_AFTER.match(following))      # "clearance is not required" (verifier M9)


def dealbreaker(text: str | None, patterns: list[str]) -> str | None:
    """Return the first dealbreaker pattern that matches un-negated, else None."""
    body = text or ""
    for pattern in patterns:
        for found in re.finditer(pattern, body, flags=re.IGNORECASE):
            if not _negated(body, found.start(), found.end()):
                return pattern
    return None


# ---------- visa sponsorship (informational label; GOAL2 G, the user's answer 2026-10-02) ----------

_CITIZEN_ONLY = re.compile(r"(?im)\b(?:u\.?s\.? citizens? only|must be (?:a )?u\.?s\.? citizens?|"
                           r"u\.?s\.? citizenship (?:is )?required|requires? u\.?s\.? citizenship)\b"
                           # a requirement bullet that is just the citizenship, e.g. "- US Citizen (Public Trust ...)"
                           r"|^[ \t]*(?:[-*\u2022\u00b7][ \t]*)?u\.?s\.? citizen(?:ship)?\b(?![ \t]*(?:or|and|,))")
_NO_SPONSOR = re.compile(
    r"(?i)\b(?:unable|not able|cannot|can ?not|can't|won't|will not|do(?:es)? not|are not able|is not able) "
    r"(?:to )?(?:currently )?(?:sponsor|provide (?:visa |immigration )?sponsorship|offer (?:visa )?sponsorship)"
    r"|\bwithout (?:the need for |requiring )?(?:current or future )?(?:visa |employer |immigration )?sponsorship"
    r"|\bsponsorship (?:is )?(?:not|un)(?: ?available| offered| provided)|\bnot eligible for (?:visa )?sponsorship"
    r"|\bno (?:visa |h-?1b )?sponsorship")
_YES_SPONSOR = re.compile(r"(?i)\b(?:will|can|able to|happy to|we) sponsor (?:(?:a |an |your |the |most |all |many )?(?:visas?|h-?1b|"
                          r"work (?:visas?|permits?|authori[sz]ation)|employment visas?|immigration))\b|\bvisa sponsorship (?:is )?(?:available|"
                          r"offered|provided)\b|\bsponsorship (?:is )?available\b|\bh-?1b (?:sponsorship|transfers?) "
                          r"(?:is |are )?(?:available|supported|welcome)")


# "unable to sponsor ... for roles outside of engineering and product", "no sponsorship except for ...": a
# sentence that carves out an exception does not say this job has no sponsorship
_EXCEPTION = re.compile(r"(?i)\b(?:(?:roles?|positions?|jobs?|functions?|teams?|departments?) outside\b|"
                        r"with the exception of (?:certain|some|select|specific|our)\b|"
                        r"except (?:for )?(?:[\w-]+ ){0,3}(?:roles|positions|jobs|teams)\b|"
                        r"for (?:certain|some|select|non-?technical) (?:roles|positions))")


def _sentence(body: str, start: int, end: int) -> str:
    begin = max(body.rfind(c, 0, start) for c in ".!?\n") + 1
    ends = [i for i in (body.find(c, end) for c in ".!?\n") if i >= 0]
    return " ".join(body[begin:min(ends) + 1 if ends else len(body)].split())


def _no_sponsor(body: str):
    """The first "no sponsorship" match whose sentence has no exception, or None."""
    return next((m for m in _NO_SPONSOR.finditer(body)
                 if not _EXCEPTION.search(_sentence(body, m.start(), m.end()))), None)


def sponsorship(text: str | None) -> str | None:
    """'citizen' (US citizens only), 'no' (no visa sponsorship), 'yes' (sponsors), or None (the JD does not say).
    A label for the user only: neither profile filters or penalizes on it."""
    body = text or ""
    if _CITIZEN_ONLY.search(body):
        return "citizen"
    if _no_sponsor(body):
        return "no"
    if _NO_SPONSOR.search(body):          # only with an exception ("roles outside of engineering"): it says neither
        return None
    if _YES_SPONSOR.search(body):
        return "yes"
    return None


SPONSOR_QUOTE_CHARS = 220


def sponsorship_quote(text: str | None) -> str | None:
    """the JD sentence that gave sponsorship() its answer (the badge's evidence), at most ~220 chars."""
    body = text or ""
    found = _CITIZEN_ONLY.search(body) or _no_sponsor(body) or _YES_SPONSOR.search(body)
    if not found:
        return None
    sentence = _sentence(body, found.start(), found.end())
    return sentence if len(sentence) <= SPONSOR_QUOTE_CHARS else sentence[:SPONSOR_QUOTE_CHARS - 1].rstrip() + "…"
