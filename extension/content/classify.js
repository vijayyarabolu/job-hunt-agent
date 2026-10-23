// Job Hunt Agent Autofill 1.3, the field classifier (GOAL27 §2, the core). Every control on any page becomes one canonical
// field ("first_name", "email", "phone_code", "work.title", "edu.school", "sponsor_now", "gender", ...) from all the
// signals a person would read: its label / question, aria-label, placeholder, name / id / automation ids, autocomplete
// token, the section heading it sits under, its kind and the choices it offers. Each signal is scored against a
// synonym table; the best key wins when it clears a threshold and beats the runner-up, else the field is "unknown"
// (Job Hunt Agent's saved questions, then the resume, then "Needs you"). Pure (no DOM): unit-tested in Node.
// Sensitive fields (SSN, birth date, ID / passport / licence / bank numbers) classify as "sensitive": never filled,
// never sent to Job Hunt Agent.
"use strict";
var JH = globalThis.JH || (globalThis.JH = {});

(() => {
  const THRESHOLD = 6;
  const MARGIN = 1.5;

  // "firstName", "first_name", "applicant.first-name", "urls[LinkedIn]" -> "first name", "urls linkedin"
  const words = (s) => String(s == null ? "" : s).replace(/([a-z])([A-Z])/g, "$1 $2").replace(/([A-Z]+)([A-Z][a-z])/g, "$1 $2")
    .toLowerCase().replace(/[’']/g, "").replace(/&/g, " and ").replace(/[^a-z0-9+#]+/g, " ").trim();
  const NOISE = /\b(required|optional|please (select|choose|enter|provide)( one| your)?|select one|choose one)\b/g;
  const clean = (s) => words(String(s || "").replace(/\*/g, " ")).replace(NOISE, " ").replace(/\s+/g, " ").trim();

  // ---- the synonym table ------------------------------------------------------------------------------------------
  // where: l = label / question text, a = name / id / automation ids (as words), c = autocomplete token (exact),
  // t = input type, p = placeholder, o = the choices (joined), s = section heading. A negative weight rules a key out.
  // Within one `where` the best match counts; the `where`s add up.
  const SEC = {
    work: /\b(work|employment|professional|job|career) (experience|history)\b|\bexperience\b|\bemployment\b|\bemployers?\b|\bpositions? held\b/,
    edu: /\beducation(al)?\b|\bschools?\b|\bacademic\b|\bdegrees?\b|\buniversit/,
    eeo: /self ?identif|voluntary|equal (employment )?opportunity|\beeo\b|demographic|diversity|disabilit|veteran/,
    phone: /\bphone\b/,
    sign: /signature|e ?sign|attest|self ?identif|disabilit|voluntary/,
    address: /\baddress\b|\bresidence\b|where you live/,
  };
  const R = (where, re, w, cond) => ({ where, re, w, cond });
  const LINK = (name, re) => [R("l", re, 10), R("a", re, 8), R("p", re, 6), R("p", new RegExp(`${name}\\.com`), 10)];

  const KEYS = {
    // contact
    first_name: [R("l", /^(legal )?(first|given|fore) ?name\b|^name first$|^first$|^prenom/, 10), R("a", /\b(first ?name|fname|given ?name|firstname)\b/, 8),
      R("c", "given-name", 12), R("p", /^first( name)?$/, 6), R("l", /\bpreferred|nick/, -20)],
    last_name: [R("l", /^(legal )?(last|family|sur) ?name\b|^surname|^name last$|^last$/, 10), R("a", /\b(last ?name|lname|family ?name|surname|lastname)\b/, 8),
      R("c", "family-name", 12), R("p", /^last( name)?$/, 6)],
    middle_name: [R("l", /^(legal )?middle (name|initial)/, 10), R("a", /\bmiddle ?(name|initial)\b/, 8), R("c", "additional-name", 12)],
    preferred_name: [R("l", /\bpreferred (first )?name\b|\bnick ?name\b|\bname you go by\b/, 11), R("a", /\bpreferred ?name\b/, 8)],
    full_name: [R("l", /^(your |applicant |candidate )?(full |legal |complete )*name$|^name (first and last|in full)|^full legal name|^first and last name|\byour full (legal )?name\b/, 9),
      R("a", /^(full ?name|name|candidate name|applicant name)$|\bsystemfield name\b/, 7), R("c", "name", 12),
      R("s", SEC.sign, -4), R("l", /\b(company|school|reference|referr|emergency|manager|university|employer|file)\b/, -20)],
    email: [R("l", /\be ?mail( address)?\b/, 10), R("t", "email", 10), R("a", /\be ?mail\b/, 8), R("c", "email", 12), R("p", /@|e ?mail/, 6),
      R("l", /\b(confirm|re ?enter|verify|repeat|retype)\b/, -20), R("l", /\b(referr\w*|reference|manager|recruiter|emergency)\b/, -20)],
    email_confirm: [R("l", /\b(confirm|re ?enter|verify|repeat|retype)\b.*\be ?mail\b|\be ?mail\b.*\b(again|confirm)/, 12), R("a", /\b(confirm|verify|repeat)\b.*\bemail\b|\bemail\b.*\bconfirm/, 9)],
    phone_code: [R("l", /\bcountry (phone |calling |dial(ing)? )?code\b|\bphone (country )?code\b|\bdial(ing)? code\b|\bcountry code\b/, 12),
      R("a", /\bcountry ?phone ?code\b|\bphone ?country\b|\bdial ?code\b|\bcountry ?code\b/, 9), R("c", "tel-country-code", 12),
      R("l", /^country$/, 14, { s: SEC.phone }), R("o", /\+1\b.*\+44|\+44.*\+1\b|\(\+\d{1,3}\)/, 4)],
    phone_type: [R("l", /\bphone (device )?type\b|\bdevice type\b|\btype of phone\b/, 12), R("a", /\bphone ?(device )?type\b/, 9)],
    phone_ext: [R("l", /^(phone )?ext(ension)?$|\bextension\b/, 9), R("a", /\bextension\b|\bphone ext\b/, 7), R("c", "tel-extension", 12)],
    phone: [R("l", /\b(phone|mobile|cell|telephone|contact number)( number)?\b|\bbest number\b/, 10), R("a", /\b(phone|mobile|cell|tel)( ?number)?\b/, 8),
      R("c", "tel", 12), R("c", "tel-national", 12), R("p", /\(\d{3}\)|\d{3}[ -]\d{3}|phone/, 5),
      R("l", /\b(type|code|extension|ext|referr\w*|reference|emergency|manager)\b/, -20)],
    address1: [R("l", /\baddress( line)? ?1\b|\bstreet( address)?\b|^(home |mailing |current |residential |permanent )?address$|^address line$/, 10),
      R("a", /\baddress ?(line)? ?1\b|\bstreet\b|\baddress1\b/, 8), R("c", "address-line1", 12), R("c", "street-address", 12),
      R("l", /\b(e ?mail|web|ip|company|employer|school|line 2|apt)\b/, -20)],
    address2: [R("l", /\baddress( line)? ?2\b|\bapt\b|\bapartment\b|\bsuite\b|\bunit\b/, 10), R("a", /\baddress ?(line)? ?2\b|\baddress2\b/, 8), R("c", "address-line2", 12)],
    city: [R("l", /^(current )?city\b|\bcity$|\btown\b|\blocality\b|\b(what|which) city\b|\bcity of residence\b/, 10), R("a", /\bcity\b|\blocality\b/, 8),
      R("c", "address-level2", 12), R("l", /\bstate\b|\bzip\b/, -3), R("s", SEC.work, -4), R("s", SEC.edu, -4)],
    state: [R("l", /^state$|\bstate( or |\/| )(province|region)\b|^province$|^state of residence|\bwhich state\b|^administrative area|^state ?select$|^region$/, 10),
      R("a", /\bstate\b|\bprovince\b|\bregion\b|\badministrative ?area\b/, 7), R("c", "address-level1", 12), R("o", /\balabama\b.*\balaska\b|\bAL\b.*\bAK\b/i, 4),
      R("l", /\b(statement|united states)\b/, -20)],
    zip: [R("l", /\bzip( ?code)?\b|\bpostal( code)?\b|\bpost ?code\b|\bpin ?code\b/, 10), R("a", /\bzip\b|\bpostal\b|\bpost ?code\b/, 8), R("c", "postal-code", 12)],
    country: [R("l", /^country( of residence)?$|\bcountry (region|of residence)\b|\b(which|what) country\b|^country\/region$|\bcountry (where|in which) you (currently )?(reside|live)\b/, 10), R("a", /\bcountry\b/, 6),
      R("c", "country", 12), R("c", "country-name", 12), R("o", /\bunited states\b.*\bcanada\b|\bafghanistan\b.*\balbania\b/, 4),
      R("l", /\b(authori|eligib|citizen|visa|code|phone)\w*/, -20), R("s", SEC.phone, -4)],
    // GOAL30: "Location (City, State, Country)", "City, State", "Where do you live?", "In what city and state do you reside?"
    location: [R("l", /^(current )?location( city)?$|^location \(?city\)?$|\bwhere are you (currently )?(located|based)\b|\bcity (and|,) state\b|\bcurrent (city|location)\b|^city, state$/, 10),
      R("l", /^(your |current )?location (city|city state|city state country|city state zip)$|^city state( country)?$|\b(what|which) city (and|or) state\b|\bwhere do you (currently )?(live|reside)\b|\bcity of residence\b.*\bstate\b/, 11),
      R("a", /\b(candidate )?location\b/, 7), R("l", /\b(work|office|preferred|job) location\b|\brelocat/, -20), R("s", SEC.work, -6)],
    county: [R("l", /\bcounty\b/, 10)],
    // links
    linkedin: LINK("linkedin", /\blinked ?in\b/),
    github: LINK("github", /\bgit ?hub\b/),
    twitter: LINK("twitter", /\btwitter\b|\bx com\b/),
    other_url: [R("l", /\bother (web ?site|url|link)\b|\badditional (link|url|website)\b/, 11), R("a", /\bother\b/, 3)],
    portfolio: [R("l", /\bportfolio\b|\bpersonal (web ?)?site\b|^website$|\bweb ?site( url)?$|\bblog\b|^(personal )?url$|^other website/, 9),
      R("a", /\bportfolio\b|\bwebsite\b|^url$/, 7), R("c", "url", 8), R("p", /linkedin|github/, -10), R("l", /\b(company|linkedin|github|twitter)\b/, -20)],
    // work and availability
    sponsor_future: [R("l", /\bsponsor\w*\b.*\bfuture\b|\bfuture\b.*\bsponsor|(now|currently) or (in the future|will).*sponsor|\b(will|would) you (now or in the future )?(ever )?require\b.*\bsponsor|\b(visa|immigration) (assistance|support|sponsorship)\b.*\b(future|later)\b/, 12)],
    sponsor_now: [R("l", /\bsponsor(ship)?\b|\bh ?1 ?b\b|\bvisa (support|transfer)\b/, 10), R("l", /\bfuture\b/, -4)],
    work_auth: [R("l", /\b(legally )?(authori[sz]ed|eligible|permitted|entitled) to work\b|\bwork authori[sz]ation\b|\bauthori[sz]ation to work\b|\bright to work\b|\bemployment eligibility\b|\blegal(ly)? (right|able) to work\b/, 11),
      R("l", /\bsponsor/, -8)],
    visa_status: [R("l", /\bvisa (status|type)\b|\bimmigration status\b|\bcurrent (work )?(visa|permit)\b|\bwork permit\b/, 10)],
    citizenship: [R("l", /\bcitizen(ship)?\b|\bexport (controls?|administration)\b|\bitar\b|\bu ?s person\b|\bpermanent resident\b/, 9)],
    clearance: [R("l", /\bclearance\b/, 10)],
    gov_official: [R("l", /\bgovernment official\b|\bpublic official\b|\bpolitically exposed\b|\bfederal (employee|agency)\b|\bcivil serv|\belected\b|\bgovernment (employee|entity|agency)\b/, 9), R("l", /\bsponsor|\bimmigration\b/, -10)],
    criminal: [R("l", /\bconvicted\b|\bfelony\b|\bcriminal\b|\bmisdemeanor\b/, 10)],
    noncompete: [R("l", /\bnon ?compete\b|\bnon ?solicit|\brestrictive covenant|\b(employment|confidential\w*) agreement\b/, 10)],
    background_check: [R("l", /\bbackground (check|investigation|screen)/, 10)],
    drug_test: [R("l", /\bdrug (test|screen)|\bsubstance\b/, 10)],
    age18: [R("l", /\b18\b.*\b(year|age|old)|\b(age|old) of 18\b|\blegal age\b|\bat least 18\b|\bage 18\b|\b18 (or|and) (over|older)\b/, 11)],
    prev_worked: [R("l", /\b(previously|ever|formerly|before) (been )?(work|employ)|\b(former|previous|current or former) (employee|worker)\b|\bworked (for|at|with)\b.*\b(before|previously|in the past)\b|\b(are|were) you\b.*\b(an )?employee of\b|\bhave you (ever )?worked (at|for)\b|\b(former|previous|past) (\w+ ){1,3}employee\b/, 10),
      R("a", /\bprevious ?worker\b|\bcandidate ?is ?previous/, 9)],
    related_employee: [R("l", /\brelat(ed|ives?)\b|\bfamily member\b|\bknow (anyone|someone) (who )?(works|employed)|\bpersonal relationship with\b.*\bemployee\b/, 9), R("l", /\brelocat/, -20)],
    referred: [R("l", /\breferr?(ed|al)\b(?!.*source)/, 8), R("l", /\b(name|email|who)\b/, -6)],
    how_heard: [R("l", /\bhow did you (hear|find|learn|come)\b|\bwhere did you (hear|find|see|learn)\b|^(referral |job |candidate |application )?source$|\bhow .* (hear|find out) about\b/, 11),
      R("a", /\bsource\b|\bhow ?heard\b|\breferral ?source\b/, 7)],
    salary: [R("l", /\bsalary\b|\bcompensation\b|\bpay (expectation|requirement|range)|\bdesired (pay|rate|wage)\b|\bexpected (base )?(pay|salary|rate)\b|\bexpect(ed)? to earn\b/, 10), R("a", /\bsalary\b|\bcompensation\b/, 7)],
    // GOAL30 §1.5: start date, earliest availability and "when can you start" are one fact (Answers' N weeks setting)
    start_date: [R("l", /\bstart date\b|\b(available|able) to start\b|\bearliest (date|start)\b|\bwhen (can|could|would) you (start|begin|join)\b|\bavailability date\b|\bdate available\b|\bearliest availability\b|\bwhen (are you|would you be) available to (start|begin|join|work)\b|\bavailab(le|ility) to (start|begin|join)\b|\bwhen are you available for (full ?time |part ?time )?(work|employment)\b/, 10),
      R("l", /\bearliest (you|that you) (would|could|can)\b.*\b(start|available|begin|join)/, 11),
      R("s", SEC.work, -8), R("s", SEC.edu, -8)],
    notice: [R("l", /\bnotice period\b|\bweeks? notice\b|\bhow much notice\b|\bnotice (do you need|required|to give)\b/, 10)],
    employed: [R("l", /\b(are you|currently|presently) (currently |presently |now )?(employed|working)\b(?! (by|at|for|with)\b)|\bcurrent(ly)? employment status\b|\bemployment status\b|\bdo you (currently |presently )?have a job\b|\bemployed (right now|currently|at the moment)\b/, 10),
      R("l", /\b(employed|working) (by|at|for|with)\b/, -12), R("s", SEC.work, -6)],
    hcp: [R("l", /\bhealth ?care (provider|professional|practitioner)s?\b|\bhcp\b/, 11)],
    relocate: [R("l", /\brelocat/, 10)],
    onsite: [R("l", /\bon ?site\b|\bin (the |our )?office\b|\bhybrid\b|\bcommut|\bdays (a|per) week\b|\bin person\b|\bdays? (from|in|at) (one of )?(our|the) (office|offices|hubs?|office hubs?)\b/, 8)],
    travel: [R("l", /\btravel\b/, 8)],
    // the user's total years only: years with one skill ("years of Python experience", "... with AWS") come from the resume
    years_exp: [R("l", /\b(total |overall )?years of (professional |relevant |work |industry |full time )?experience\b|\bhow many years (have you worked|of experience)\b/, 9),
      R("l", /\b(with|in|using) (?!the industry|industry|total)\w+|\byears of (?!(professional|relevant|work|industry|full time) )\w+ experience/, -12)],
    current_company: [R("l", /\bcurrent (company|employer)\b|\b(most recent|present|latest) (company|employer)\b|\bcurrent or (previous|most recent|last) (company|employer)\b/, 10), R("c", "organization", 10), R("s", SEC.work, -3)],
    current_title: [R("l", /\bcurrent (job )?(title|position|role)\b|\b(most recent|present) (job )?title\b|\bcurrent or (previous|most recent|last) (job )?(title|position)\b|^(job )?title$|^headline$/, 9), R("c", "organization-title", 10),
      R("s", SEC.work, -3)],
    cover_letter: [R("l", /\bcover letter\b/, 10), R("a", /\bcover ?letter\b/, 9)],
    sms_consent: [R("l", /\btext messag|\bsms\b|\bwhats ?app\b/, 9), R("o", /\btext messag|\bsms\b/, 6)],   // GOAL30 §0c: Ashby's "Yes - I consent to receiving text messages"
    terms: [R("l", /\bi (have read|agree|acknowledge|certify|consent|understand|attest|accept)\b|\bterms (and|&) conditions\b|\bprivacy (policy|notice|statement|acknowledg)|\bterms of (use|service)\b|\battest\b|\btrue and (correct|complete)\b/, 9),
      R("a", /\bterms\b|\bagreement ?checkbox\b|\bprivacy\b|\bconsent\b/, 6), R("l", /\btext messag|\bsms\b|\bwhats ?app\b/, -6)],
    // education (single answers, outside an education list)
    degree: [R("l", /\bdegree\b|\blevel of education\b|\bhighest (level of )?education\b|\beducation level\b|\bqualification\b/, 9), R("a", /\bdegree\b/, 7)],
    field_of_study: [R("l", /\bfield of study\b|\bmajor\b|\bdiscipline\b|\barea of study\b|\bconcentration\b/, 10), R("a", /\bfield ?of ?study\b|\bmajor\b/, 8)],
    school: [R("l", /\bschool\b|\buniversit|\bcollege\b|\binstitution\b/, 9), R("a", /\bschool\b|\buniversity\b|\binstitution\b/, 7), R("l", /\bhigh school\b.*\b(diploma|ged)\b/, -10)],
    grad_date: [R("l", /\bgraduat|\bcompletion date\b|\bdegree (date|year)\b/, 9)],
    gpa: [R("l", /\bgpa\b|\bgrade point\b/, 11), R("a", /\bgpa\b/, 9)],
    // signature and EEO
    signature_date: [R("l", /\btoday ?s date\b|\bdate signed\b|^date$|\bsignature date\b/, 9), R("a", /\bdate ?signed\b|\btodays ?date\b/, 8)],
    signature_name: [R("l", /\bsignature\b|\bsign(ed)? (your )?name\b|\btype your (full )?(legal )?name\b|\belectronic(ally)? sign|^name of (applicant|candidate)$/, 10),
      R("l", /^(your |full |legal )*name$/, 9, { s: SEC.sign })],
    hispanic: [R("l", /\bhispanic\b|\blatin[ox]\b/, 11)],
    race: [R("l", /\brace\b|\bethnicit/, 10), R("a", /\bethnicity\b|\brace\b/, 8), R("o", /\basian\b.*\bwhite\b|\bwhite\b.*\basian\b|black or african/, 7)],
    gender: [R("l", /\bgender( identity)?\b|^sex$|\bsex\b/, 10), R("a", /\bgender\b/, 8), R("o", /^(?=.*\bmale\b)(?=.*\bfemale\b)/, 7), R("l", /\btransgender\b|\bsexual orientation\b/, -20)],
    pronouns: [R("l", /\bpronoun/, 11)],
    orientation: [R("l", /\bsexual orientation\b|\blgbt/, 12)],
    transgender: [R("l", /\btransgender\b/, 12)],
    veteran: [R("l", /\bveteran\b|\b(military|armed forces) (service|status)\b|\bserved in the (military|armed forces)\b|\bservice member\b/, 10), R("a", /\bveteran/, 8), R("o", /protected veteran|not a veteran/, 7)],
    // GOAL31: on Workday's Self Identify form every id says "disability", its Language dropdown too
    disability: [R("l", /\bdisabilit/, 10), R("a", /\bdisability/, 8), R("o", /\bdisabilit/, 7), R("l", /^(preferred )?language\b/, -20)],
    // what the resume gives (not bank keys)
    skills: [R("l", /^(key |technical |relevant |your |top )?skills\b|\bskills?( and| &) (tools|technologies|expertise)\b|^type to add skills|^technologies$/, 10), R("a", /^skills?$/, 7)],
    summary: [R("l", /^(professional |experience |career |profile |personal |executive )?summary\b|\babout (you|yourself|me)\b|\btell us about yourself\b|^profile$|^bio$/, 9),
      R("l", /\bwhy\b/, -6)],
    // files
    resume: [R("l", /\bresume\b|\bcv\b|\bcurriculum/, 10), R("a", /\bresume\b|\bcv\b/, 9), R("l", /\bcover\b|\btranscript\b/, -20)],
    cover_letter_file: [R("l", /\bcover letter\b/, 10), R("a", /\bcover ?letter\b/, 9)],
    other_file: [R("l", /\btranscript\b|\bportfolio\b|\bwriting sample\b|\badditional (documents?|files?|attachments?)\b|\bother (documents?|files?)\b/, 10)],
    // never filled, never sent: the user's to type
    sensitive: [R("l", /\bsocial security\b|\bssn\b|\bsocial insurance\b|\bsin number\b|\bdate of birth\b|\bbirth ?date\b|\bdob\b|\bdriver'?s? licen[cs]e( number| no)?\b|\bpassport (number|no)\b|\bnational (id|identity|insurance)\b|\btax (id|identification)\b|\bitin\b|\b(bank|routing|account) (number|no)\b|\biban\b|\bmother'?s maiden\b|\bsecurity question\b|\bcredit card\b/, 20),
      R("a", /\bssn\b|\bsocial ?security\b|\bdate ?of ?birth\b|\bbirth ?date\b|\bdob\b|\bpassport\b|\bnational ?id\b|\btax ?id\b/, 14), R("c", "bday", 20), R("c", "cc-number", 20)],
  };

  // repeating entries (Work history, Education): the role of a field inside one entry
  const ROLE = {
    work: {
      current: [R("l", /\bcurrent(ly)?\b|\bpresent\b|\bstill (work|employed)\b|\bi work here\b/, 10), R("a", /\bcurrent(ly)?\b/, 8)],
      desc: [R("l", /\bdescription\b|\bresponsibilit|\bduties\b|\bachievements\b|\baccomplishments\b|\bwhat (did|do) you do\b|\bsummary\b/, 10), R("a", /\bdescription\b|\bresponsibilit|\bsummary\b/, 8)],
      company: [R("l", /\bcompany\b|\bemployer\b|\borgani[sz]ation\b|\bbusiness name\b/, 10), R("a", /\bcompany\b|\bemployer\b|\borganization\b/, 8)],
      title: [R("l", /\b(job )?title\b|\bposition\b|\brole\b|\bdesignation\b/, 10), R("a", /\btitle\b|\bposition\b|\brole\b/, 8), R("l", /\bdescription\b/, -20)],
      // GOAL30 §0: a past job's place is its country (a City / State box inside an entry stays empty)
      location: [R("l", /\blocation\b|\bwhere\b/, 9), R("a", /\blocation\b/, 7)],
      country: [R("l", /^country\b|\bcountry\/region\b/, 10), R("a", /\bcountry\b/, 8)],
      city: [R("l", /^city\b|\bcity$/, 10), R("a", /\bcity\b/, 8)],
      state: [R("l", /^(state|province|region)\b|\bstate\/province\b/, 10), R("a", /\b(state|province|region)\b/, 7)],
      start_month: [R("l", /\b(start|from|begin)\w*\b.*\bmonth\b|\bmonth\b.*\b(start|from)\b/, 12), R("a", /\b(start|from)\w* ?month\b|\bmonth ?(start|from)\b/, 10)],
      start_year: [R("l", /\b(start|from|begin)\w*\b.*\byear\b|\byear\b.*\b(start|from)\b/, 12), R("a", /\b(start|from)\w* ?year\b|\byear ?(start|from)\b/, 10)],
      end_month: [R("l", /\b(end|to|until|finish)\w*\b.*\bmonth\b|\bmonth\b.*\b(end|to)\b/, 12), R("a", /\b(end|to)\w* ?month\b/, 10)],
      end_year: [R("l", /\b(end|to|until|finish)\w*\b.*\byear\b|\byear\b.*\b(end|to)\b/, 12), R("a", /\b(end|to)\w* ?year\b/, 10)],
      start: [R("l", /\bstart\b|^from\b|\bbegin|\bdate started\b/, 9), R("a", /\bstart\b|\bfrom ?date\b|\bdate ?from\b/, 7)],
      end: [R("l", /\bend\b|^to$|^to\b|\buntil\b|\bfinish|\bdate ended\b/, 9), R("a", /\bend ?date\b|\bto ?date\b|\bdate ?to\b|\bend\b/, 7)],
    },
    edu: {
      school: [R("l", /\bschool\b|\buniversit|\bcollege\b|\binstitution\b/, 10), R("a", /\bschool\b|\buniversity\b|\binstitution\b/, 8)],
      degree: [R("l", /\bdegree\b|\bqualification\b|\bdiploma\b/, 10), R("a", /\bdegree\b/, 8)],
      field: [R("l", /\bfield\b|\bmajor\b|\bdiscipline\b|\barea of study\b|\bconcentration\b|\bsubject\b|\bspeciali[sz]ation\b/, 10), R("a", /\bfield ?of ?study\b|\bmajor\b|\bdiscipline\b/, 8)],
      gpa: [R("l", /\bgpa\b|\bgrade\b/, 10), R("a", /\bgpa\b/, 8)],
      // GOAL30 §0: the school's own place ("Springfield, IL"), never the user's address
      location: [R("l", /\blocation\b|\bwhere\b/, 9), R("a", /\blocation\b/, 7)],
      country: [R("l", /^country\b|\bcountry\/region\b/, 10), R("a", /\bcountry\b/, 8)],
      city: [R("l", /^city\b|\bcity$/, 10), R("a", /\bcity\b/, 8)],
      state: [R("l", /^(state|province|region)\b|\bstate\/province\b/, 10), R("a", /\b(state|province|region)\b/, 7)],
      current: [R("l", /\bcurrent(ly)?\b|\bpresent\b|\bstill (attend|enrolled)\b/, 9)],
      start_month: [R("l", /\b(start|from|begin)\w*\b.*\bmonth\b/, 12), R("a", /\b(start|from)\w* ?month\b/, 10)],
      start_year: [R("l", /\b(start|from|begin)\w*\b.*\byear\b|\bfirst year\b/, 12), R("a", /\b(start|from)\w* ?year\b|\bfirst ?year/, 10)],
      end_month: [R("l", /\b(end|to|graduat|complet)\w*\b.*\bmonth\b/, 12), R("a", /\b(end|to|grad)\w* ?month\b/, 10)],
      end_year: [R("l", /\b(end|to|graduat|complet)\w*\b.*\byear\b|^year$|\blast year\b/, 12), R("a", /\b(end|to|grad)\w* ?year\b|\blast ?year/, 10)],
      start: [R("l", /\bstart\b|^from\b|\bbegin/, 9), R("a", /\bstart\b|\bfrom\b/, 7)],
      end: [R("l", /\bend\b|^to$|^to\b|\bgraduat|\bcomplet|\bfinish/, 9), R("a", /\bend\b|\bto ?date\b|\bgrad/, 7)],
    },
  };

  // which kinds a key can be (a gender question is never a file; an email is never a checkbox)
  const CHOICE = /^(select|combo|listbox|radio|checkboxes|buttons)$/;
  const TEXTY = /^(text|textarea|combo|date)$/;
  const KINDS = {
    resume: /^file$/, cover_letter_file: /^file$/, other_file: /^file$/,
    email: TEXTY, email_confirm: TEXTY, phone: /^(text)$/, first_name: TEXTY, last_name: TEXTY, full_name: TEXTY, middle_name: TEXTY,
    preferred_name: TEXTY, address1: TEXTY, address2: TEXTY, zip: TEXTY, linkedin: TEXTY, github: TEXTY, twitter: TEXTY, portfolio: TEXTY,
    summary: /^(text|textarea)$/, skills: /^(text|textarea|combo)$/, terms: /^(checkbox|checkboxes|radio|select|combo|listbox|buttons)$/,
    gender: CHOICE, race: CHOICE, veteran: CHOICE, disability: CHOICE, hispanic: CHOICE, orientation: CHOICE, transgender: CHOICE,
    phone_type: CHOICE, phone_code: CHOICE, signature_date: /^(text|date)$/, signature_name: TEXTY,
  };
  const NOT_FILE = /^(?!file$)/;

  function scoreRules(rules, sig) {
    const best = {};
    for (const r of rules) {
      if (r.cond && r.cond.s && !r.cond.s.test(sig.section)) continue;
      let hit = false;
      if (r.where === "c") hit = sig.autocomplete === r.re;
      else if (r.where === "t") hit = sig.type === r.re;
      else if (r.where === "l") hit = Boolean(sig.label) && r.re.test(sig.label);
      else if (r.where === "a") hit = sig.attrs.some((a) => r.re.test(a));
      else if (r.where === "p") hit = Boolean(sig.placeholder) && r.re.test(sig.placeholder);
      else if (r.where === "o") hit = Boolean(sig.options) && r.re.test(sig.options);
      else if (r.where === "s") hit = Boolean(sig.section) && r.re.test(sig.section);
      if (!hit) continue;
      const k = r.w < 0 ? `-${r.where}` : r.where;
      best[k] = r.w < 0 ? Math.min(best[k] || 0, r.w) : Math.max(best[k] || 0, r.w);
    }
    return Object.values(best).reduce((a, b) => a + b, 0);
  }

  function signals(f) {
    const label = clean(f.label || f.question || "");
    const attrs = [...new Set((f.hints || []).concat([f.name, f.id, f.automation]).filter(Boolean).map(words).filter((a) => a && !/^(input|field|question)? ?\d+$/.test(a)))];
    return { label, attrs, long: label.split(" ").length > 12, followUp: FOLLOW_UP.test(label), autocomplete: String(f.autocomplete || "").toLowerCase().trim().split(/\s+/).pop(),
             placeholder: clean(f.placeholder || ""), type: String(f.type || "").toLowerCase(), section: clean(f.section || ""), kind: f.kind || "text",
             options: (f.options || []).slice(0, 60).map(words).join(" | ") };
  }

  function rank(table, sig, kinds) {
    const out = [];
    for (const [key, rules] of Object.entries(table)) {
      let s = scoreRules(rules, sig);
      if (s <= 0) continue;
      if (sig.long && !QUESTION.test(key) && !(key === "location" && WHERE_LIVE.test(sig.label))) s -= 6;
      if (sig.followUp) s -= 8;
      const allowed = (kinds && kinds[key]) || NOT_FILE;
      if (!allowed.test(sig.kind)) s -= 12;
      if (sig.kind === "file" && !kinds) s -= 12;
      out.push({ key, score: s });
    }
    return out.sort((a, b) => b.score - a.score);
  }

  // the repeater a field belongs to (work / edu), from its section heading, entry title or its own attributes
  function groupOf(sig, f) {
    if (f.group) return f.group;
    const attr = sig.attrs.join(" ");
    if (/\b(work ?experience|employment|employer|jobs?|positions?|experiences?)\b/.test(attr) && !/\beducation\b/.test(attr)) return "work";
    if (/\b(educations?|schools?|degrees?)\b/.test(attr) && /\[\d+\]|\b\d+\b/.test(attr)) return "edu";
    if (SEC.edu.test(sig.section)) return "edu";
    if (SEC.work.test(sig.section) && !/\byears of\b/.test(sig.label)) return "work";
    return null;
  }

  // an index from the field's own name ("jobs[1][title]", "education-2--school", "workExperience-3") or null
  function indexOf(f) {
    for (const s of [f.name, f.id, f.automation, ...(f.hints || [])]) {
      const m = /\[(\d{1,2})\]|(?:^|[-_.])(\d{1,2})(?:[-_.]|$)/.exec(String(s || ""));
      if (m) return Number(m[1] || m[2]);
    }
    return null;
  }

  // keys that are themselves questions keep their full score in a long question; a plain field ("Email", "City")
  // named inside a long sentence is not that field ("... contact you via SMS ... the email you provided")
  const QUESTION = /^(employed|hcp|sponsor_|work_auth|visa_status|citizenship|clearance|gov_official|criminal|noncompete|background_check|drug_test|age18|prev_worked|related_employee|referred|how_heard|salary|start_date|notice|relocate|onsite|travel|years_exp|sms_consent|terms|hispanic|race|gender|orientation|transgender|veteran|disability|pronouns)/;
  const WHERE_LIVE = /\b(what|which) city\b|\bwhere do you (currently )?(live|reside)\b/;   // location asked as a sentence
  const FOLLOW_UP = /^(if (yes|no|so|other|not|applicable|you (selected|answered|checked))|please (explain|specify|elaborate))\b/;

  function field(f) {
    const sig = signals(f);
    if (f.kind === "file") {
      const r = rank({ resume: KEYS.resume, cover_letter_file: KEYS.cover_letter_file, other_file: KEYS.other_file }, sig, KINDS);
      return r.length && r[0].score >= THRESHOLD ? { key: r[0].key, score: r[0].score } : { key: "file", score: 0 };
    }
    const sens = scoreRules(KEYS.sensitive, sig);
    if (sens >= 14) return { key: "sensitive", score: sens };
    const group = groupOf(sig, f);
    if (group) {
      const r = rank(ROLE[group], sig, null);
      if (r.length && r[0].score >= THRESHOLD) {
        const role = r[0].key;
        if (role === "current" ? /^(checkbox|checkboxes|radio)$/.test(sig.kind) : !/^(checkbox)$/.test(sig.kind)) {
          // GOAL30 §0: "Work Experience 2" is the second job; "Education 2012" names no entry
          const nth = /[a-z#]\s*(\d{1,2})\s*$/i.exec(sig.section);
          return { key: `${group}.${role}`, group, role, index: indexOf(f), entry: nth && Number(nth[1]) >= 1 ? Number(nth[1]) - 1 : null,
            score: r[0].score };
        }
      }
    }
    const { sensitive, resume, cover_letter_file, other_file, ...table } = KEYS;   // eslint-disable-line no-unused-vars
    const r = rank(table, sig, KINDS);
    if (!r.length || r[0].score < THRESHOLD) return { key: null, score: r.length ? r[0].score : 0, near: r[0] && r[0].key };
    if (r[1] && r[0].score - r[1].score < MARGIN && r[1].key !== r[0].key) {
      return { key: null, score: r[0].score, near: r[0].key, also: r[1].key };   // two keys fit as well: not a guess
    }
    return { key: r[0].key, score: r[0].score };
  }

  // the entry each repeater field belongs to, in page order: its heading's number, else the k-th field of its group
  // and role is the k-th entry (unnumbered entries never all land on the first job)
  function entries(list) {
    const seen = {};
    return list.map((c) => {
      if (!c || !c.group) return null;
      const k = `${c.group}.${c.role}`;
      const nth = seen[k] || 0;
      seen[k] = nth + 1;
      return c.entry != null ? c.entry : nth;
    });
  }

  JH.classify = { field, entries, words, clean, KEYS, ROLE, THRESHOLD, signals };
  if (typeof module !== "undefined") module.exports = JH.classify;
})();
