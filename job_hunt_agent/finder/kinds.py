"""The three people worth reaching for one job (GOAL28 §5): a recruiter, the AI/ML hiring or engineering manager, and
one peer engineer on the team. Each kind has the titles Apollo's people search is asked for, the Hunter departments
that hold it, the LinkedIn people-search keywords the person opens when nobody is found, and the one small ask its
message ends with. kind_of() reads a person's title."""
import re
from urllib.parse import urlencode

from . import signals

KINDS = ("recruiter", "manager", "peer")
LABELS = {"recruiter": "Recruiter", "manager": "AI/ML hiring manager", "peer": "Peer engineer"}
APOLLO_TITLES = {
    "recruiter": ("recruiter", "technical recruiter", "talent acquisition", "talent partner"),
    "manager": ("machine learning manager", "engineering manager machine learning", "ai engineering manager",
                "head of ai", "head of machine learning", "director of machine learning", "director of ai"),
    "peer": ("machine learning engineer", "ai engineer", "applied scientist", "llm engineer"),
}
HUNTER_DEPARTMENTS = {"recruiter": ("hr",), "manager": ("management", "it"), "peer": ("it", "research")}
LINKEDIN_WORDS = {"recruiter": ("recruiter",), "manager": ("machine learning manager", "AI engineering manager"),
                  "peer": ("AI engineer",)}
ASKS = {
    "recruiter": "Could you point me to the hiring manager or take a look at my application?",
    "manager": "Would you have 15 minutes to tell me about the team's work?",
    "peer": "What is the team like to work on? If it fits, I'd welcome a referral.",
}
AI_WORDS = re.compile(r"\b(ai|a\.i\.|ml|machine learning|artificial intelligence|deep learning|nlp|llms?|genai|"
                      r"generative|applied scien\w*|data scien\w*|research|computer vision)\b", re.I)
RECRUITER = re.compile(r"recruit|talent|sourc|people partner|\bta\b|\bhr\b|human resources", re.I)
MANAGER = re.compile(r"manager|head of|director|\bvp\b|vice president|\blead\b|principal|chief|cto", re.I)
ENGINEER = re.compile(r"engineer|scientist|developer|researcher", re.I)


def kind_of(title: str | None) -> str | None:
    """recruiter / manager (a manager or leader title on AI/ML or engineering work) / peer (an AI/ML engineer or
    scientist, not a manager) / None (anyone else)."""
    title = title or ""
    if RECRUITER.search(title):
        return "recruiter"
    if MANAGER.search(title) and (AI_WORDS.search(title) or ENGINEER.search(title) or "engineering" in title.lower()):
        return "manager"
    if ENGINEER.search(title) and AI_WORDS.search(title):
        return "peer"
    return None


def linkedin_search(company: str, kind: str) -> list[dict]:
    """LinkedIn's own people search for this kind at the company (opened by the person; nothing is called)."""
    return [{"label": f"Find on LinkedIn: {company} {words}",
             "url": f"{signals.LINKEDIN_SEARCH}?{urlencode({'keywords': f'{company} {words}'})}"}
            for words in LINKEDIN_WORDS[kind]]


def departments(missing: list[str]) -> str:
    """Hunter's comma-delimited department filter for the kinds still missing, in a fixed order (the cache key)."""
    wanted = {d for k in missing for d in HUNTER_DEPARTMENTS[k]}
    return ",".join(sorted(wanted))
