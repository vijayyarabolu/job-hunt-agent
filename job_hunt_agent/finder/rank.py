"""Ranking the people the panel found: their likely role (recruiter / hiring manager / leader), why they
match the job (same team, same location, named in the JD), and a confidence. Each also gets its kind for
outreach (finder/kinds.py: recruiter / manager / peer, or None)."""
import re

from . import kinds

RECRUITER = re.compile(r"recruit|talent|sourc|people partner|\bta\b", re.I)
LEADER = re.compile(r"\b(cto|ceo|coo|chief|founder|co-founder|president|vp)\b|vice president", re.I)
MANAGER = re.compile(r"manager|head of|director|\blead\b|principal|supervisor", re.I)
TECH = re.compile(r"engineer|\bai\b|\bml\b|machine learning|data|tech|software|platform|product|research|scien|"
                  r"analytics|infrastructure|devops|cloud|security|\bit\b", re.I)
AI_TEAM = r"\b(ai|a\.i\.|ml|machine learning|artificial intelligence|deep learning|nlp|llm|genai|generative|" \
          r"data science|applied scien\w*|research)\b"
TEAMS = {"ai_engineer": AI_TEAM, "genai_engineer": AI_TEAM, "ml_engineer": AI_TEAM, "dl_engineer": AI_TEAM,
         "data_scientist": AI_TEAM[:-3] + r"|data|analytics)\b", "data_engineer": r"\b(data|analytics|platform)\b"}
TEAM_LABELS = {"ai_engineer": "AI/ML", "genai_engineer": "AI/ML", "ml_engineer": "AI/ML", "dl_engineer": "AI/ML",
               "data_scientist": "AI/ML/data", "data_engineer": "data"}
SOURCE_SCORE = {"jd": 1.0, "manual": 0.8, "apollo": 0.6, "hunter": 0.6, "team_page": 0.5, "guess": 0.2}
SOURCE_WHY = {"jd": "named in the JD", "apollo": "listed by Apollo", "hunter": "found by Hunter",
              "team_page": "on the company's team page", "manual": "added by you"}
ROLE_SCORE = {"recruiter": 0.2, "hiring manager": 0.2, "leader": 0.1, "other": 0.0}
ROLE_WHY = {"recruiter": "recruiting role", "hiring manager": "leads a team: likely hiring manager",
            "leader": "company leader"}
TEAM_SCORE, PLACE_SCORE, EMAIL_SCORE, NO_NAME_PENALTY = 0.2, 0.1, 0.05, 0.6
HIGH, MEDIUM = 1.0, 0.7


def likely_role(title: str | None) -> str:
    """recruiter / leader / hiring manager (a manager title on a technical team) / other."""
    title = title or ""
    if RECRUITER.search(title):
        return "recruiter"
    if LEADER.search(title):
        return "leader"
    return "hiring manager" if MANAGER.search(title) and TECH.search(title) else "other"


def _why(person: dict, job: dict, role: str) -> tuple[list[str], float]:
    why, bonus = [SOURCE_WHY.get(person.get("source"), person.get("source") or "")], 0.0
    if role in ROLE_WHY:
        size = job.get("company_size")
        why.append(f"{ROLE_WHY[role]} at a ~{size}-person company" if role == "leader" and size else ROLE_WHY[role])
    team = TEAMS.get(job.get("family") or "")
    if team and re.search(team, person.get("title") or "", re.I):
        why.append(f"same team as the JD ({TEAM_LABELS[job['family']]})")
        bonus += TEAM_SCORE
    city = (person.get("city") or "").strip()
    if city and city.lower() in (job.get("location") or "").lower():
        why.append(f"same location as the JD ({city})")
        bonus += PLACE_SCORE
    if person.get("email"):
        bonus += EMAIL_SCORE
    return [w for w in why if w], bonus


def rank(people: list[dict], job: dict) -> list[dict]:
    """The people best first, each with role, why, score and confidence."""
    out = []
    for person in people:
        role = likely_role(person.get("title"))
        why, bonus = _why(person, job, role)
        score = SOURCE_SCORE.get(person.get("source"), 0.3) + ROLE_SCORE[role] + bonus
        if not person.get("name"):
            score -= NO_NAME_PENALTY
        confidence = "high" if score >= HIGH else "medium" if score >= MEDIUM else "low"
        out.append({**person, "role": role, "kind": kinds.kind_of(person.get("title")), "why": why,
                    "score": round(score, 2), "confidence": confidence})
    return sorted(out, key=lambda p: -p["score"])
