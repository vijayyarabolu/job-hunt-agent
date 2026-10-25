"""GenAI relevance of a JD (GOAL28 §4): which of a fixed list of GenAI terms its required / preferred lines name.

- terms(jd): the distinct terms (LLM, RAG, agent, ...) in the lines ats.weighted_lines counts (requirements, the role,
  preferred / nice-to-have; never the company blurb, benefits or EEO text).
- gate_verdict(): a person's JD-content gate. A job whose title is only a Data Scientist one (or a generic one: Software
  Engineer, Applied Scientist, Research Engineer, Member of Technical Staff) stays in their set only when its JD names
  at least `min_terms` of them; otherwise the fit row carries the verdict and the job is filtered like a dealbreaker.
- The match breakdown's "GenAI work" part (both people, match.py) reads the same terms: it sorts, never blocks.
Pure and deterministic: no I/O, no LLM.
"""
import re

from .resume import ats

# the GOAL28 list. Spellings of one idea count once (decision 2026-10-18): "LLM" / "large language model",
# "generative AI" / "GenAI", "RAG" / "retrieval-augmented", "agent" / "agentic"; so "Generative AI (GenAI)" alone is
# one term, not the two the gate needs.
TERMS: dict[str, re.Pattern] = {name: re.compile(rx, re.I) for name, rx in {
    "LLM": r"\bllms?\b|\blarge[\s-]+language[\s-]+models?\b",
    "generative AI": r"\bgenerative[\s-]+ai\b|\bgen[\s-]?ai\b",
    "RAG": r"\b(?-i:RAG)\b|\bretrieval[\s-]+augmented\b",
    "agents": r"\b(?:ai[\s-]+)?agents?\b(?![\s-]+of\s+change)|\bagentic\b",
    "prompt engineering": r"\bprompt[\s-]+engineering\b",
    "fine-tuning": r"\bfine[\s-]?tun(?:e|es|ed|ing)\b",
    "LangChain": r"\blangchain\b",
    "LangGraph": r"\blanggraph\b",
    "vector database": r"\bvector[\s-]+(?:databases?|dbs?|stores?)\b",
    "embeddings": r"\bembeddings?\b",
    "MCP": r"\b(?-i:MCP)\b|\bmodel[\s-]+context[\s-]+protocol\b",
    "tool calling": r"\btool[\s-]+(?:calling|calls)\b|\bfunction[\s-]+calling\b",
}.items()}
DEFAULT_MIN_TERMS = 2
# GOAL35 §2: the other plain software titles an LLM-heavy JD hides behind (Backend Engineer, Founding Engineer, ...)
GENERIC_TITLE = re.compile(r"(?i)\b(?:software (?:engineer|developer)|applied scientist|research engineer|"
                           r"member of (?:the )?technical staff|back[\s-]?end (?:engineer|developer)|"
                           r"full[\s-]?stack (?:engineer|developer)|forward[\s-]deployed (?:software )?engineer|"
                           r"founding engineer|product engineer|platform engineer)\b")
# a title naming AI / ML / data work is never "generic", whatever else it says ("Software Engineer, Machine Learning")
_SPECIFIC = re.compile(r"(?i)(?<![a-z0-9])(?:ai|a\.i\.|artificial[\s-]+intelligence|ml|mlops|machine[\s-]+learning|deep[\s-]+learning|llms?|"
                       r"gen\s?ai|generative|nlp|natural language|computer vision|perception|data|analytics|agentic|"
                       r"agents?|rag|inference|models?|modeling)(?![a-z0-9])")
VERDICT = "jd_gate: no GenAI work in the JD"
PCT_BY_COUNT = (30, 55, 75, 90)           # 0, 1, 2, 3 terms; 4 or more = 100


def jd_lines(jd: str | None) -> list[str]:
    """The lines the ATS reading counts (weight > 0); the whole JD when no section says where the role starts."""
    lines = [line for weight, line in ats.weighted_lines(jd or "") if weight > 0]
    return lines or [line for weight, line in ats.weighted_lines(jd or "", blurbs=False) if weight > 0]


def terms(jd: str | None) -> list[str]:
    """The distinct GenAI terms the JD's required / preferred lines name, in TERMS order."""
    text = "\n".join(jd_lines(jd))
    return [name for name, rx in TERMS.items() if rx.search(text)] if text else []


def is_generic_title(title: str | None) -> bool:
    return bool(GENERIC_TITLE.search(title or "")) and not _SPECIFIC.search(GENERIC_TITLE.sub(" ", title or ""))


def _tagged(families: str | None) -> set[str]:
    return {f for f in (families or "").strip(",").split(",") if f}


def gated(title: str | None, families: str | None, gate: dict | None, profile_families: tuple = ()) -> bool:
    """True when the person's gate applies to this job: every family the person holds it by is a gated one
    (data_scientist), or its title is generic."""
    if not gate:
        return False
    if gate.get("generic_titles") and is_generic_title(title):
        return True
    held = _tagged(families) & set(profile_families or _tagged(families))
    return bool(held) and held <= set(gate.get("families") or ())


def gate_verdict(title: str | None, families: str | None, jd: str | None, gate: dict | None,
                 profile_families: tuple = (), found: list[str] | None = None) -> str | None:
    """The fit row's dealbreaker text when the gate keeps this job out, else None. `found`: terms(jd), when known."""
    if not gated(title, families, gate, profile_families):
        return None
    names = terms(jd) if found is None else found
    if len(names) >= int(gate.get("min_terms") or DEFAULT_MIN_TERMS):
        return None
    return VERDICT + (f" (only {', '.join(names)})" if names else "")


def is_gate_verdict(dealbreaker: str | None) -> bool:
    return bool(dealbreaker) and dealbreaker.startswith(VERDICT)


def pct(names: list[str] | None) -> int | None:
    """The match breakdown's GenAI part (None = not computed for this job yet)."""
    if names is None:
        return None
    return PCT_BY_COUNT[len(names)] if len(names) < len(PCT_BY_COUNT) else 100
