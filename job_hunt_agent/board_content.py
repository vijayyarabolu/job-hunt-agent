"""Content identity check for tracker-sourced companies (milestone 2, verifier round 3).

A tracker row stores the JD Alex applied to. A company's postings share its intro paragraph, so the right board
shares many 6-word sequences with that JD; a same-name stranger shares none ('daydream' SEO agent vs DayDream
dental: 0; real boards: 40+). Few shared sequences => review (a human confirms reworded intros). No usable text on
either side => unknown (no flag).

Only *distinctive* sequences count: a sequence found in the JDs of two or more different tracker companies is
standard legal text (pay-transparency provisions, fair-chance ordinances, AI-screening notices) and is dropped, or
unrelated companies would "match" (verifier round 4: amazon/zillow shared 133 such sequences).
"""
from collections import defaultdict

from .net import get_json
from .text import html_to_text, norm, strip_boilerplate

SHINGLE_WORDS = 6
MIN_SHARED = 10
MIN_JD_WORDS = 60
SAMPLE_JOBS = 15


def shingles(text: str) -> set[str]:
    words = norm(strip_boilerplate(text)).split()
    return {" ".join(words[i:i + SHINGLE_WORDS]) for i in range(len(words) - SHINGLE_WORDS + 1)}


def board_text(ats: str, slug: str) -> str:
    """Concatenated descriptions of up to SAMPLE_JOBS current postings (HttpError propagates)."""
    if ats == "greenhouse":
        jobs = (get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true") or {}).get("jobs")
        return "\n".join(html_to_text(j.get("content"), True) for j in (jobs or [])[:SAMPLE_JOBS])
    if ats == "lever":
        jobs = get_json(f"https://api.lever.co/v0/postings/{slug}?mode=json") or []
        return "\n".join(_lever_text(j) for j in jobs[:SAMPLE_JOBS] if isinstance(j, dict))
    jobs = (get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}") or {}).get("jobs")
    return "\n".join(j.get("descriptionPlain") or "" for j in (jobs or [])[:SAMPLE_JOBS])


def _lever_text(job: dict) -> str:
    """Lever keeps some postings' whole text in `lists` (descriptionPlain empty, e.g. Qloo)."""
    lists = " ".join(f"{x.get('text') or ''} {html_to_text(x.get('content'), True)}"
                     for x in job.get("lists") or [] if isinstance(x, dict))
    return f"{job.get('descriptionPlain') or ''}\n{lists}\n{job.get('additionalPlain') or ''}"


def matches(tracker_sets: list[frozenset[str]], text: str) -> bool | None:
    """True/False when both sides have enough text, else None. tracker_sets come from distinctive()."""
    board = shingles(text)
    usable = [s for s in tracker_sets if s]
    if not board or not usable:
        return None
    return max(len(s & board) for s in usable) >= MIN_SHARED


def distinctive(jds_by_company: dict[str, list[str]]) -> dict[str, list[frozenset[str]]]:
    """Per company, the shingle set of each long-enough JD minus sequences other companies' JDs share."""
    sets = {c: [shingles(jd) for jd in jds if len(norm(jd).split()) >= MIN_JD_WORDS]
            for c, jds in jds_by_company.items()}
    owners: dict[str, set[str]] = defaultdict(set)
    for company, company_sets in sets.items():
        for s in company_sets:
            for sh in s:
                owners[sh].add(company)
    common = {sh for sh, cs in owners.items() if len(cs) > 1}
    return {c: [frozenset(s - common) for s in company_sets] for c, company_sets in sets.items()}


def tracker_jds(conn) -> dict[str, list[frozenset[str]]]:
    """norm(company) -> distinctive shingle sets of the JDs stored in the tracker (applications.jd)."""
    out: dict[str, list[str]] = defaultdict(list)
    for company, jd in conn.execute("SELECT company, jd FROM applications WHERE jd IS NOT NULL AND jd <> ''"):
        out[norm(company)].append(jd)
    return distinctive(dict(out))
