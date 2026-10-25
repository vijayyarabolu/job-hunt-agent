"""GOAL35 §4.3: W2 and legit roles only (someone on STEM OPT needs a W2 employer; someone else may need sponsorship
too). The employment terms a JD states, read once at ingest into jobs.work_terms (comma-joined):

- "w2": the JD offers W2 (a "W2" tag with an icon on the card);
- a blocking kind, which hides the job for a person whose rule is W2 (both people by default; profile.yaml
  `work_terms: any` turns it off): "c2c_only" (C2C / corp-to-corp only), "1099_only", "no_w2", "citizens_only"
  (US citizens / green card holders only, "USC/GC"), "no_opt" (OPT / CPT / EAD not accepted);
- "vague": a third-party repost with no client named plus a personal-mail contact or "pay DOE" with C2C wording
  (flagged, never hidden; it builds on signals.staffing_signals).

"No sponsorship" stays the informational label it has been since 2026-10-02 (signals.sponsorship): GOAL35 says "drop
or clearly mark"; it is marked, because a no-sponsorship employer still hires on OPT.
GOAL36 §1 / §3 (the user's hard rules): "no sponsorship EVER" blocks: "no_sponsor_ever" when the no-sponsorship sentence
also rules out the future ("now or in the future", "current or future", "at any time"). A W2 job that is a contract or
comes through a staffing firm is a "W2 contract" on the card (api.work_view, contract())."""
import re

from . import signals

W2 = "w2"
VAGUE = "vague"
BLOCKS = {"citizens_only": "US citizens / green card only", "no_opt": "no OPT / EAD", "no_w2": "no W2",
          "c2c_only": "C2C only", "1099_only": "1099 only", "no_sponsor_ever": "no sponsorship ever"}
# GOAL36 §3: staffing firms whose W2 contract roles are kept and tagged (TEKsystems, Insight Global, Robert Half, ...)
STAFFING_FIRMS = re.compile(r"(?i)\b(?:teksystems|insight global|robert half|randstad|apex systems|kforce|aerotek|"
                            r"actalent|allegis|akkodis|modis|collabera|experis|manpower|hays|beacon hill|judge group|"
                            r"motion recruitment|adecco|kelly services|yoh|vaco|addison group|signature consultants|"
                            r"diverse lynx|mindlance|infosys bpm|net2source|pyramid consulting|cybercoders)\b")
_FUTURE = re.compile(r"(?i)\b(?:now or in the future|current or future|now or future|present or future|future|"
                     r"at any (?:time|point)|ever)\b")
VERDICT = "work terms: "                     # the fit row's dealbreaker text for a blocked job: "work terms: C2C only"
RULE_W2, RULE_ANY = "w2", "any"

_W2_WORD = r"w[- ]?2"
_C2C = r"(?:c2c|corp(?:oration)?[- ]?(?:to|2)[- ]?corp(?:oration)?)"
# "C2C only", "only C2C", "C2C candidates only", "strictly C2C" (but "no C2C" / "C2C not available" are W2 jobs)
_C2C_ONLY = re.compile(rf"(?i)\b{_C2C}\s*(?:\(?\s*(?:candidates?|contracts?|basis|roles?)\s*\)?\s*)?only\b"
                       rf"|\b(?:only|strictly|must be)\s+(?:on\s+)?{_C2C}\b")
_1099_ONLY = re.compile(r"(?i)\b1099\s*(?:\(?\s*(?:contract(?:or)?s?|basis|roles?)\s*\)?\s*)?only\b"
                        r"|\b(?:only|strictly)\s+(?:on\s+)?1099\b")
_NO_W2 = re.compile(rf"(?i)\bno\s+{_W2_WORD}\b|\b{_W2_WORD}\s+(?:is\s+)?(?:not\s+(?:available|accepted|possible|an option)"
                    rf"|unavailable)\b|\bnot\s+(?:open|available)\s+(?:to|for)\s+{_W2_WORD}\b")
_CITIZENS = re.compile(r"(?i)\b(?:usc|u\.?s\.?\s*citizens?|citizens?)\s*(?:/|,|or|and|&)\s*(?:gc|green\s*card)(?:\s*holders?)?"
                       r"|\b(?:gc|green\s*card)\s*holders?\s*only\b|\bonly\s+(?:usc|u\.?s\.?\s*citizens?)\b")
_OPEN_TO_VISAS = re.compile(r"(?i)\b(?:opt|cpt|ead|h-?1b?|h4|tn|visa holders?|all work authori[sz]ations?)\b")
_NO_OPT = re.compile(r"(?i)\bno\s+(?:stem[- ])?(?:opt|cpt|ead)(?:[- ]ead)?s?\b"
                     r"|\b(?:opt|cpt|ead)(?:\s*/\s*(?:opt|cpt|ead))*\s+(?:candidates?\s+)?(?:are\s+|is\s+)?"
                     r"(?:not\s+(?:accepted|considered|eligible)|need not apply)\b")
_W2 = re.compile(rf"(?i)\b{_W2_WORD}\b")
_NEGATED_W2 = re.compile(r"(?i)\b(?:no|not|never|without)\s+(?:on\s+|a\s+)?$")
_GMAIL = re.compile(r"(?i)\b[\w.+-]+@(?:gmail|yahoo|hotmail|outlook|aol)\.com\b")
_DOE = re.compile(r"(?i)\b(?:doe|depending on experience)\b")
_C2C_WORD = re.compile(rf"(?i)\b{_C2C}\b")


def _citizens_only(body: str) -> bool:
    """"USC/GC only" style, or signals' "US citizens only"; a sentence that also welcomes OPT / H1B / EAD is open."""
    for m in _CITIZENS.finditer(body):
        if not _OPEN_TO_VISAS.search(signals._sentence(body, m.start(), m.end())):
            return True
    return signals.sponsorship(body) == "citizen"


def _only(rx: re.Pattern):
    """"C2C only" / "1099 only" unless W2 is offered in the same phrase ("W2 / 1099 only", "W2 or C2C only")."""
    def hit(body: str) -> bool:
        return any(not _W2.search(body[max(0, m.start() - 12):m.start()]) for m in rx.finditer(body))
    return hit


_c2c_only, _1099_only = _only(_C2C_ONLY), _only(_1099_ONLY)


def _offers_w2(body: str) -> bool:
    """W2 is named and not as what the job is NOT ("no W2", "not on W2")."""
    return any(not _NEGATED_W2.search(body[max(0, m.start() - 16):m.start()]) for m in _W2.finditer(body))


def _no_sponsor_ever(body: str) -> bool:
    """A no-sponsorship sentence (signals) that also rules out the future."""
    found = signals._no_sponsor(body)
    return bool(found) and bool(_FUTURE.search(signals._sentence(body, found.start(), found.end())))


def read(text: str | None) -> tuple[str, ...]:
    """The JD's employment terms: the first blocking kind found, "w2" when W2 is offered, "vague" for a vague repost."""
    body = text or ""
    found = []
    checks = (("citizens_only", _citizens_only), ("no_opt", _NO_OPT.search), ("no_w2", _NO_W2.search),
              ("c2c_only", _c2c_only), ("1099_only", _1099_only), ("no_sponsor_ever", _no_sponsor_ever))
    block = next((kind for kind, hit in checks if hit(body)), None)
    if block:
        found.append(block)
    if block not in ("no_w2", "c2c_only", "1099_only") and _offers_w2(body):
        found.append(W2)
    if vague_repost(body):
        found.append(VAGUE)
    return tuple(found)


def vague_repost(text: str | None) -> bool:
    """A third-party repost: no client named and (a personal-mail contact, or pay "DOE" with C2C wording)."""
    body = text or ""
    if not signals._UNNAMED_CLIENT.search(body) and signals.staffing_signals(body) < 2:
        return False
    return bool(_GMAIL.search(body)) or bool(_DOE.search(body) and _C2C_WORD.search(body))


def label(text: str | None) -> str | None:
    """jobs.work_terms: the comma-joined read(), None when the JD says nothing about it."""
    return ",".join(read(text)) or None


def contract(employment: str | None, company: str | None) -> bool:
    """GOAL36 §3: a contract role (the ATS or JD says so) or one posted by a staffing firm."""
    return employment in (signals.CONTRACT, signals.CONTRACT_TO_HIRE) or bool(STAFFING_FIRMS.search(company or ""))


def kinds(stored: str | None) -> set[str]:
    return set(filter(None, (stored or "").split(",")))


def verdict(stored: str | None, rule: str | None) -> str | None:
    """The dealbreaker text that hides this job for a person whose rule is W2, else None."""
    if (rule or RULE_W2) == RULE_ANY:
        return None
    block = next((k for k in BLOCKS if k in kinds(stored)), None)
    return VERDICT + BLOCKS[block] if block else None


def is_verdict(dealbreaker: str | None) -> bool:
    return bool(dealbreaker) and dealbreaker.startswith(VERDICT)
