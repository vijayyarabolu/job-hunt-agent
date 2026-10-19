"""What a job email says: rejected / screen / interview / offer / received (confirmation) / other. Rules first.

Each rule is a phrase with a strength. A strong phrase settles its kind ("not moving forward", "pleased to offer",
a GoodTime / Calendly link); weak ones only count when no strong phrase is present ("unfortunately",
"availability"); a screening vendor's name (Karat, HackerRank, ...) outranks a generic interview phrase, since a
"Karat interview" is the screen. A strong rejection or offer outranks interview / screen phrases (the decision ends
those steps). One kind at the highest strength is the answer; two or more is a disagreement, and no hit at all is
"nothing found": both go to Claude (llm.py) when the email came from a matched company or an ATS. "received" (an
application confirmation) is the weakest: any status phrase outranks it, because rejections and invites usually
also thank you for applying.

A status phrase only counts in job mail: the text has to talk about an application / role / candidate / interview /
recruiting, name a screening vendor, or come from an ATS. "Unfortunately, your payment..." is not a rejection.
"""
import re
from dataclasses import dataclass

VENDOR, STRONG, WEAK = 3, 2, 1      # VENDOR: a screening vendor named (a "Karat interview" is the screen)
_RULES: dict[str, list[tuple[str, int]]] = {
    "offer": [
        (r"\boffer letter\b", STRONG), (r"\bpleased to (?:extend|offer you|make you an offer)", STRONG),
        (r"\bextend(?:ing)? (?:you )?(?:a |an )?(?:formal |verbal |written )?offer\b", STRONG),
        (r"\boffer of employment\b", STRONG), (r"\b(?:verbal|formal|written) offer\b", STRONG),
        (r"\bcongratulations\b.{0,80}\boffer\b", STRONG), (r"\baccept(?:ing)? (?:the|this|our) offer\b", WEAK)],
    "rejected": [
        (r"\bnot (?:be )?mov(?:e|ing) (?:you |your (?:application|candidacy) )?forward\b", STRONG),
        (r"\bwon'?t be mov(?:e|ing) (?:you |your (?:application|candidacy) )?forward\b", STRONG),
        (r"\bmove forward with (?:other|another|different) candidates?\b", STRONG),
        (r"\b(?:pursue|proceed with|go with|selected) (?:other|another|a different) candidates?\b", STRONG),
        (r"\bother candidates (?:whose|who)\b", STRONG), (r"\bposition has (?:been|now been) filled\b", STRONG),
        (r"\b(?:role|position|requisition|job) (?:has been|was|is now) (?:filled|closed|cancell?ed)\b", STRONG),
        (r"\bregret to inform\b", STRONG), (r"\b(?:have|has|were|was) not been (?:selected|chosen)\b", STRONG),
        (r"\bnot (?:been )?(?:selected|chosen) to (?:move|proceed|advance|continue)\b", STRONG),
        (r"\bno longer (?:being )?consider(?:ed|ing)\b", STRONG),
        (r"\bdecided not to (?:proceed|move|pursue)\b", STRONG),
        (r"\bwill not be (?:proceeding|advancing|moving|progressing)\b", STRONG),
        (r"\bnot (?:to )?(?:proceed|progress|advance) (?:with )?your (?:application|candidacy)\b", STRONG),
        (r"\bdecided to (?:move|proceed|go) (?:forward )?with (?:other|another|a different)\b", STRONG),
        (r"\bnot (?:a|the right) (?:fit|match) (?:for this|at this)\b", STRONG),
        (r"\bunable to (?:offer you|move you forward|proceed)\b", STRONG),
        (r"\bunfortunately\b", WEAK), (r"\bother candidates\b", WEAK)],
    "interview": [
        (r"goodtime\.io|calendly\.com|modernloop|\bgem\.com/schedul|prelude\.co|cal\.com/", STRONG),
        (r"\b(?:schedule|scheduling|set up|book) (?:an?|your|the|a time for (?:an?|your)) (?:\w+ )?interview", STRONG),
        (r"\binvit(?:e|ing) you (?:to|for) (?:an?|the)? ?(?:\w+ )?(?:interview|onsite|on-site)\b", STRONG),
        (r"\b(?:onsite|on-site|virtual onsite|final[- ]round|panel|technical|hiring manager|loop) interviews?\b",
         STRONG),
        (r"\bnext round\b", STRONG), (r"\bmove (?:you )?forward to the next (?:step|stage|round)\b", STRONG),
        (r"\binterview (?:invitation|invite|confirmation|schedule|details)\b", STRONG),
        (r"\byour (?:availability|available times)\b", WEAK), (r"\bavailability\b", WEAK), (r"\bonsite\b", WEAK),
        (r"\bpanel\b", WEAK), (r"\bspeak with (?:the|our) (?:hiring manager|team)\b", WEAK)],
    "screen": [
        (r"hackerrank|codesignal|\bkarat\b|codility|hirevue|coderpad|testgorilla|codescreen", VENDOR),
        (r"\b(?:online|technical|coding|skills?) (?:assessment|challenge|test|exercise)\b", STRONG),
        (r"\btake[- ]home\b", STRONG), (r"\bphone screen(?:ing)?\b", STRONG),
        (r"\brecruiter (?:call|screen|chat|conversation)\b", STRONG),
        (r"\b(?:initial|introductory|intro|quick|short|brief) (?:call|chat|conversation|phone call)\b", STRONG),
        (r"\bcomplete (?:the|this|an|our) assessment\b", STRONG), (r"\bassessment\b", WEAK),
        (r"\b(?:learn more about you|get to know you)\b", WEAK)],
    "received": [
        (r"\bthank(?:s| you) for (?:applying|your application|submitting)\b", WEAK),
        (r"\b(?:we(?:'ve| have)? |has been |was )?received your application\b", WEAK),
        (r"\bapplication (?:has been |was )?(?:received|submitted)\b", WEAK),
        (r"\bthank(?:s| you) for (?:your )?interest in\b", WEAK), (r"\bsuccessfully (?:applied|submitted)\b", WEAK)],
}
COMPILED = {kind: [(re.compile(p, re.I | re.S), s) for p, s in rules] for kind, rules in _RULES.items()}
JOB_CONTEXT = re.compile(r"\b(?:applica(?:tion|nt)s?|applied|applying|candida(?:te|cy)|position|role|interview\w*|"
                         r"recruit\w*|hiring|talent acquisition|requisition|job|career|opportunit(?:y|ies))\b", re.I)
STATUS_KINDS = ("rejected", "offer", "interview", "screen")
# A sentence that only promises a step IF the person is picked says nothing yet: "If you are selected to move forward,
# a member of our team will contact you to schedule an interview" is a confirmation (live mail, 2026-10-08). Such a
# sentence has a condition word AND a selection word; "let us know if you are available for an interview" has none.
CONDITIONAL = re.compile(r"[^.!?\n]*\b(?:if|should|once|in the event)\b[^.!?\n]*\b(?:selected|shortlisted|chosen|"
                         r"move forward|moving forward|meets?|match(?:es)?|corresponds?|fits?|qualif\w*|we decide|"
                         r"interested in (?:moving|proceeding))\b[^.!?\n]*[.!?]?", re.I)


@dataclass(frozen=True)
class Verdict:
    kind: str | None            # the rules' answer; None when they disagree or found nothing
    hits: dict                  # kind -> strongest strength seen
    job_mail: bool              # talks about applying / interviewing (or came from an ATS)

    @property
    def status_hits(self) -> list[str]:
        return [k for k in STATUS_KINDS if k in self.hits]

    @property
    def guess(self) -> str | None:
        """Best single guess when the rules did not settle it (for "Needs you"): the strongest, then this order."""
        if self.kind:
            return self.kind
        found = self.status_hits
        if not found:
            return "received" if "received" in self.hits else None
        return max(found, key=lambda k: (self.hits[k], -STATUS_KINDS.index(k)))


def hits(text: str) -> dict[str, int]:
    found: dict[str, int] = {}
    for kind, rules in COMPILED.items():
        best = max((s for rx, s in rules if rx.search(text)), default=0)
        if best:
            found[kind] = best
    return found


def _pool(found: dict[str, int]) -> list[str]:
    """The status kinds still in the running. A decision outranks the steps it ends: a strong rejection or offer
    phrase wins over interview / screen mentions ("thank you for your time at the panel interview ... we will move
    forward with other candidates"); rejection against offer stays a disagreement. Otherwise the kinds at the highest
    strength seen."""
    strong = [k for k in STATUS_KINDS if found.get(k, 0) >= STRONG]
    for decision, other in (("rejected", "offer"), ("offer", "rejected")):
        if decision in strong and other not in strong:
            return [decision]
    top = max((found[k] for k in STATUS_KINDS if k in found), default=0)
    return [k for k in STATUS_KINDS if top and found.get(k) == top]


def classify(subject: str, text: str, from_ats: bool = False) -> Verdict:
    whole = f"{subject}\n{text}"
    found = hits(CONDITIONAL.sub(" ", whole))
    job = from_ats or bool(JOB_CONTEXT.search(whole)) or VENDOR in found.values()   # "invited you to a test"
    if not job:
        return Verdict("other", {}, False)
    pool = _pool(found)
    if len(pool) == 1:
        return Verdict(pool[0], found, True)
    if len(pool) > 1:
        return Verdict(None, found, True)
    return Verdict("received" if "received" in found else None, found, True)
