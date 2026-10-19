"""Company names exactly as the company writes them.

Display name = the brand the company uses for itself: the ATS's own company field / career page title, its JD text
("About 6sense", "At 8am, we …"), with the company's own styling (impact.com, thatgamecompany, d-Matrix, OPSWAT).
Legal suffixes go (Inc., LLC, Corporation, Corp, Ltd, PLC, ", Inc.", "& Co.") unless the brand includes them; never
all caps unless the company writes it that way. config/lists/board_names.csv pins a board's name and wins over
everything (registry.boards applies it), so a fix survives rediscovery.

This module is pure: tidy() for every name the registry loads, suspicious() / propose() for the one-time check
(names_fix.py runs it over every board with the stored JDs and, for the suspicious ones, a live look at the ATS).
"""
import re
from collections import Counter
from dataclasses import dataclass

LEGAL = re.compile(r"(?i)(?:\s*,\s*|\s+)(?:&\s*co|inc|incorporated|llc|l\.l\.c|ltd|limited|corp|corporation|plc|pllc|llp|"
                   r"lp|l\.p|co)\.?$")
DANGLING = re.compile(r"(?i)(?:\s+|^)(?:of|&|and|the|for|de|at)$")
JUNK = frozenset({"unavailable", "careers", "career", "jobs", "job", "home", "search", "external", "n/a", "na",
                  "none", "null", "undefined", "test", "default", "welcome"})
SHORT_CAPS = 6                      # a one-word all-caps name this long or shorter is an acronym / brand (OPSWAT)
_WORD = re.compile(r"[A-Za-z0-9]")
# a country / site tag after the name: "Guidewire Software, Inc. (US)", "CLEAR - Corporate", "AB InBev | Growth Group"
_TAG = re.compile(r"(?i)\s*(?:\((?:us|usa|u\.s\.a?\.?|united states|corporate)\)|\s[-–|]\s*corporate)$")
_STYLED = re.compile(r"[a-z][A-Z]|\w\.\w|^[a-z]+[A-Z]")      # internal capitals or a dot: the company's own styling
# a Workday hiringOrganization that is a legal-entity code, not a brand: "US01 VALEO", "LE001 Sterling Mets",
# "CPY0001 Young Life", "USA-PVH RETAIL STORES", "USA) Chemical Technologies"
_ENTITY = re.compile(r"^(?:(?:[A-Z]{1,8}_?\d{1,}[A-Z_\d-]*|USA?-[A-Z]+)\s+(?:-\s+)?|USA?-|[A-Za-z]+\)\s+)(?=\S)")
_ABOUT = re.compile(r"(?m)^\s*About (?:the )?([A-Z0-9#\[][\w.&'’+\-\[\]]*(?: [A-Z0-9][\w.&'’+\-]*){0,3})\s*[:\n]")
_AT = re.compile(r"\bAt ([A-Z0-9#\[][\w.&'’+\-\[\]]*(?: [A-Z0-9][\w.&'’+\-]*){0,3}), (?:we|our|you)\b")
_JOIN = re.compile(r"\b(?:Join|join) ([A-Z0-9#\[][\w.&'’+\-\[\]]*(?: [A-Z0-9][\w.&'’+\-]*){0,3})(?: and|!|,|\.| as| in)")
# "Here at DigiFlight we …", "Science Systems and Applications, Inc. (SSAI) is seeking …" (a line's opening)
_HERE = re.compile(r"\bHere at ([A-Z0-9#\[][\w.&'’+\-\[\]]*(?: [A-Z0-9][\w.&'’+\-]*){0,3}),? (?:we|our|you)\b")
_SEEKING = re.compile(r"(?m)^\s*([A-Z0-9][\w.&'’+\-]*(?: (?:and|of|&|[A-Z0-9][\w.&'’+\-]*)){0,5})"
                      r"(?:,? (?:Inc|LLC|Corp|Corporation|Co)\.?)?(?: \([A-Z]{2,8}\))? is (?:seeking|looking for|hiring)\b")
_EOE = re.compile(r"\b([A-Z0-9#\[][\w.&'’+\-\[\]]*(?: [A-Z0-9][\w.&'’+\-]*){0,3}) is (?:an|a proud) [Ee]qual "
                  r"[Oo]pportunity")
# a tenant GUID standing in for a name (ADP cid, Paylocity companyGuid boards from GOAL16's discovery)
_ID = re.compile(r"^[0-9a-f]{8}[\s-][0-9a-f]{4}[\s-][0-9a-f]{4}[\s-][0-9a-f]{4}[\s-][0-9a-f]{12}$", re.I)
ID_NAME = "an id, not a name"
_ACRONYMS = frozenset({"USA", "CPA", "CPAS"})     # kept in caps by _title; vowel-less short words are kept too
_WORDS3 = frozenset({"the", "new", "one", "all", "inn", "air", "aid", "oak", "out", "end", "arc", "pro", "art", "age",
                     "ace", "ice", "eye", "old", "our", "own", "app", "add", "ave", "elm", "ivy"})
_CVC = re.compile(r"^[^aeiou][aeiou]([^aeiou]|e)$")  # "SAM", "BOX", "RUE" read as words; "AHP", "MSO", "IKG" don't
_NOT_NAMES = frozenset({"the company", "the team", "the role", "the job", "the position", "this role", "us", "you",
                        "our company", "our team", "your team", "the opportunity", "company", "team", "role", "job",
                        "we", "our client", "the client", "client", "my client", "the candidate"})


def compact(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _dangles(text: str) -> bool:
    return bool(DANGLING.search(text.strip()))


def tidy(name: str | None) -> str:
    """Legal suffixes and stray punctuation off, whitespace squeezed. Never leaves a dangling "of" / "&" (so
    "Bank of America" and "Brown Brothers Harriman & Co." stay whole words) and never returns empty."""
    text = " ".join((name or "").split()).strip(" ,;|-–")
    text = _TAG.sub("", text).strip(" ,;|-–") or text
    for _ in range(3):
        cut = LEGAL.sub("", text).strip(" ,;|-–")
        if cut == text or not cut or _dangles(cut) or not _WORD.search(cut):
            break
        text = cut
    return text or (name or "").strip()


def shouting(name: str) -> bool:
    """All caps where the company would not write it so: several words, or one long word."""
    letters = [c for c in name if c.isalpha()]
    if len(letters) < 4 or name.upper() != name:
        return False
    return " " in name.strip() or len(letters) > SHORT_CAPS


def suspicious(name: str, key: str, ats: str = "") -> str | None:
    """Why a board's current name needs a closer look (None when it looks like a real brand name)."""
    text = (name or "").strip()
    slug = key.split("/")[0].split(".")[0]
    if _ID.match(text):
        return ID_NAME
    if not text or compact(text) in JUNK or text.lower() in JUNK:
        return "junk"
    if _dangles(text):
        return "cut off"
    if "_" in text:                                # "Booz Allen Hamilton_United States", "Semtech_Corporation"
        return "underscore"
    if tidy(text) != text:
        return "legal suffix"
    if ats == "workday" and _ENTITY.search(text):
        return "legal entity code"
    if shouting(text):
        return "all caps"
    if text.count("[") != text.count("]") or text.count("(") != text.count(")") or re.match(r"^[\W_]", text):
        return "stray punctuation"
    cs, cn = compact(slug), compact(text)
    if cn and cs != cn and cs.endswith(cn) and re.match(r"^\d", cs):
        return "lost leading digits"
    if len(cn) <= 4 and text.islower() and cn != cs:
        return "cut from the slug"                         # "am" (8am, greenhouse:affinipay1), "paid" (#paid)
    if len(cn) <= 3 and text == text.title() and text.lower() == slug.lower() and not text.isupper():
        return "slug"
    return None


@dataclass(frozen=True)
class Evidence:
    variants: Counter                      # exact spellings of the name found in the JDs
    claims: Counter                        # what the JDs call the company ("About X", "At X, we", "Join X")


def jd_evidence(name: str, texts: list[str]) -> Evidence:
    want = compact(name)
    variants: Counter = Counter()
    if want:
        pattern = re.compile(r"(?<![\w#\[])" + r"[\W_]*".join(re.escape(c) for c in tidy(name) if c.isalnum())
                             + r"(?![\w])", re.I)
        for text in texts:
            variants.update(m.group(0) for m in pattern.finditer(text)
                            if compact(m.group(0)) == want and _as_name(text, m.start(), m.end())
                            and not _in_heading(text, m.start(), m.group(0)))
        variants = Counter({v: n for v, n in variants.items() if _styling(v, name, texts)})
    claims: Counter = Counter()
    for text in texts:
        found = {tidy(m.strip()) for rx in (_ABOUT, _AT, _HERE, _JOIN, _EOE, _SEEKING) for m in rx.findall(text)}
        claims.update(c for c in found if c and c.lower() not in _NOT_NAMES and len(c) <= 40)
    return Evidence(variants, claims)


def _as_name(text: str, start: int, end: int) -> bool:
    """Not inside a URL, an e-mail address or a domain ("purestorage.com", "@gong.io", "/careers/modal")."""
    before, after = text[max(0, start - 1):start], text[end:end + 2]
    return before not in ("@", "/", ".", "-", "_") and not re.match(r"[@/]|\.[A-Za-z]", after)


def _in_heading(text: str, start: int, variant: str) -> bool:
    """An all-caps spelling on an all-caps line ("ABOUT RAMP") is a heading style, not the company's styling."""
    if variant.upper() != variant:
        return False
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", start)
    letters = [c for c in text[line_start:line_end if line_end >= 0 else None] if c.isalpha()]
    return not letters or sum(c.isupper() for c in letters) > 0.6 * len(letters)


def _styling(variant: str, name: str, texts: list[str]) -> bool:
    """A spelling counts as the company's own styling unless it is a handle or a plain word: an all-lowercase
    spelling must also open a sentence in lowercase somewhere ("insitro is …", never "modal" the word), and a
    spelling may not lowercase a capitalized word of the current name or just drop its trailing period."""
    current = tidy(name)
    if "\n" in variant or "\u00a0" in variant or re.search(r"['’]s$", variant) and not re.search(r"['’]s$", current):
        return False                                       # "Arize\n\nAI", "Blockwork's"
    if not shouting(current) and len(variant) == len(current) and any(
            a.isupper() and b.islower() for a, b in zip(current, variant)) and not variant.islower():
        return False                                       # never "UIPATH" -> "Uipath", "NVIDIA" -> "Nvidia"
    if current[:1].islower() and variant[:1].isupper() and variant[1:] == current[1:]:
        mid = re.compile(r"[a-z,] " + re.escape(variant) + r"\b")
        return any(mid.search(t) for t in texts)           # "Lemonade" mid-sentence, not just opening one
    if variant.isupper() and len([c for c in variant if c.isalpha()]) <= 3 and not current.isupper():
        return False                                       # "BOX" for "Box"
    if variant[:1].islower() and current[:1].isupper() and not variant.islower():
        return False                                       # "the Trade Desk"
    if variant.islower() and not current.islower():
        if len(variant.split()) < len(current.split()) or "." in variant:
            return False                                   # "purestorage", "gong.io": a handle or a domain
        starts = re.compile(r"(?:^|[.!?]\s+|\n\s*)" + re.escape(variant) + r"\b", re.M)
        return any(starts.search(t) for t in texts)
    if len(variant) < len(current) and current.startswith(variant) and not current[len(variant):].strip(" .,"):
        return False                                       # "Canon U.S.A" for "Canon U.S.A."
    for word in variant.split():
        if word.islower() and len(word) > 3 and any(w.lower() == word and w[:1].isupper() for w in current.split()):
            return False                                   # "Robots and pencils"
    return True


def _dominant(counts: Counter, minimum: int = 2, share: float = 0.5) -> str | None:
    if not counts:
        return None
    best, n = counts.most_common(1)[0]
    return best if n >= minimum and n >= share * sum(counts.values()) else None


def _related(candidate: str, name: str, key: str) -> bool:
    """The candidate is the same company: same letters, or the current (cut) name is part of it, or it is the slug."""
    cc, cn, cs = compact(candidate), compact(name), compact(key.split("/")[0].split(".")[0])
    return bool(cc) and (cc == cn or (cn and cn in cc) or cc == cs or (len(cc) >= 4 and cc in cs))


def propose(name: str, key: str, evidence: Evidence, ats_name: str | None = None, ats: str = "") -> tuple[str, str]:
    """(display name, why) for one board. `ats_name` is the ATS's own company field / page title, when read. A why
    starting with "needs a name" means no evidence was good enough: board_names.csv must say."""
    current = tidy(name)
    why = "legal suffix" if current != (name or "").strip() else ""
    reason = suspicious(current, key, ats)
    source = tidy(ats_name) if ats_name and not suspicious(tidy(ats_name), key, ats) else None
    claim = _dominant(Counter({c: n for c, n in evidence.claims.items() if _related(c, current, key)}), minimum=1)
    if reason == ID_NAME:                                  # nothing to keep: the ATS's own name, else the JDs'
        if not source and ats_name and shouting(tidy(ats_name)):   # 'CHRIST PANOS FOOD': a Paylocity page title
            source = None if suspicious(_title(tidy(ats_name)), key, ats) else _title(tidy(ats_name))
        better = source or _dominant(evidence.claims, minimum=1)
        if better:
            return better, f"{reason}: {'ATS says' if better == source else 'JD says'} {better!r}"
        return current, f"needs a name ({reason})"
    if reason == "legal entity code":                       # Workday's hiringOrganization: "US01 VALEO"
        rest = tidy(_ENTITY.sub("", current, count=1))
        rest = _title(rest) if shouting(rest) else rest
        tenant = compact(key.split("/")[0])
        if claim and (compact(claim) in tenant or tenant in compact(claim)):
            return claim, f"{reason}: JD says {claim!r}"
        if tenant and (tenant in compact(rest) or compact(rest).startswith(tenant[:5])):
            return rest, f"{reason}: {rest!r} without the code"
        return current, f"needs a name ({reason}: {rest!r} is not the tenant {tenant!r})"
    if reason in ("junk", "cut off", "lost leading digits", "stray punctuation", "slug", "cut from the slug"):
        better = claim or (source if source and _related(source, current, key) or reason in ("junk", "slug")
                           else None)
        if better and better != current:
            return better, f"{reason}: {'JD says' if better == claim else 'ATS says'} {better!r}"
    styled = _dominant(evidence.variants)
    if styled and _STYLED.search(current) and not reason:
        styled = None                                      # "ExxonMobil", "Otter.ai", "bioMérieux" are already styled
    if styled and styled != current and compact(styled) == compact(current) and not shouting(styled):
        return styled, (why + "; " if why else "") + f"JD writes {styled!r}"
    if shouting(current):
        if source and compact(source) == compact(current) and not shouting(source):
            return source, (why + "; " if why else "") + f"ATS writes {source!r}"
        if styled and compact(styled) == compact(current):            # the company itself writes it in caps
            return current, why or "kept"
        return _title(current), (why + "; " if why else "") + "all caps"
    return current, why or "kept"


def _title(text: str) -> str:
    """"GAP INTERNATIONAL" -> "Gap International"; short tokens that read as acronyms (AI, USA, R&D) stay."""
    small = {"of", "and", "the", "for", "at", "in", "on", "de", "to", "a"}
    out = []
    for i, word in enumerate(text.split()):
        low = word.lower()
        if i and low in small:
            out.append(low)
        elif len([c for c in word if c.isalpha()]) <= 3 and not low.isalpha():
            out.append(word)                                   # R&D, AT&T, 3M
        elif word in _ACRONYMS or (len(word) <= 4 and not re.search(r"[aeiou]", low)):
            out.append(word)                                   # USA, CPAS, NYC
        elif word[:1] == "(" and word[-1:] == ")":
            out.append(word)                                   # (CDFI)
        elif len(word) == 3 and word.isalpha() and not (_CVC.match(low) or low in _WORDS3):
            out.append(word)                                   # AHP, MSO, IKG
        elif len(word) <= 2:
            out.append(word)
        else:
            out.append("-".join(part[:1] + part[1:].lower() for part in word.split("-")))   # AAAG-NEVADA
    return " ".join(out)
