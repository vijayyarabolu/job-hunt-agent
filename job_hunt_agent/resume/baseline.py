"""`job-hunt-agent baseline import <file.docx>`: parse a resume .docx into baseline.json (GOAL "Resume is NOT final yet").

Generic on purpose: no employer names, bullet counts or dates are hardcoded. Sections are found by heading text,
jobs by a "Company<TAB>Dates" line (or dates pushed right with a run of spaces), bullets by Word list numbering (or a leading bullet glyph).
"""
import json
import re
import shutil
from datetime import datetime
from pathlib import Path

import docx

SECTION_NAMES = {"summary": "summary", "professional summary": "summary", "skills": "skills",
                 "technical skills": "skills", "education": "education", "work experience": "experience",
                 "experience": "experience", "professional experience": "experience", "projects": "projects"}
_MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
_DATE = rf"(?:{_MONTH}\s+\d{{4}}|\d{{4}})"
DATES = re.compile(rf"^\s*(?P<dates>{_DATE}\s*[–—-]\s*(?:Present|Current|Now|{_DATE}))\s*$", re.I)
ONE_DATE = re.compile(rf"^\s*(?P<dates>{_MONTH}\s+\d{{4}}|(?:Expected|Graduated)\s+{_MONTH}\s+\d{{4}})\s*$", re.I)
# what pushes the dates right: a tab (plus stray spaces), or 3+ spaces typed as padding
PADDING = re.compile(r"\t[ \t\u00a0]*|[ \u00a0]{3,}")
_BULLET_GLYPH = re.compile(r"^\s*[•●▪◦\-–*]\s+")


def _clean(text: str) -> str:
    return re.sub(r"[ \t ]+", " ", text).strip()


def _is_list_item(paragraph) -> bool:
    """Numbering on the paragraph (Google Docs export), on its style ('List Bullet'), or a typed bullet glyph."""
    style = paragraph.style
    style_numbered = style is not None and ("<w:numPr>" in style.element.xml or "list" in (style.name or "").lower())
    return "<w:numPr>" in paragraph._p.xml or style_numbered or bool(_BULLET_GLYPH.match(paragraph.text))


def _split_items(items: str) -> list[str]:
    """Split on commas that are not inside parentheses: 'AWS (S3, Glue), GCP' -> ['AWS (S3, Glue)', 'GCP']."""
    parts, depth, current = [], 0, []
    for ch in items:
        depth += ch == "("
        depth -= ch == ")"
        if ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return [_clean(p) for p in parts if _clean(p)]


def padded_span(text: str, single: bool = False) -> tuple[int, int] | None:
    """(start, end) of the padding between left text and right-hand dates: 'X<TAB>Jan 2021 - Jul 2023' or
    'X<30 spaces>Jun 2026 – Oct 2026' (single=True also accepts one date, 'X<spaces>May 2025', for education).
    None when the line does not end in dates."""
    for m in PADDING.finditer(text):
        rest = text[m.end():]
        if text[:m.start()].strip() and (DATES.match(rest) or (single and ONE_DATE.match(rest))):
            return m.span()
    return None


def split_dated(text: str, single: bool = False) -> tuple[str, str] | None:
    """'Deloitte, USA<TAB>Jul 2025 – Present' -> ('Deloitte, USA', 'Jul 2025 – Present')."""
    span = padded_span(text, single)
    return (_clean(text[:span[0]]), _clean(text[span[1]:])) if span else None


def _job_header(text: str) -> tuple[str, str] | None:
    return split_dated(text)


def walk(document) -> list[tuple[object, str]]:
    """[(paragraph, kind)] for every body paragraph. Kinds: name, headline, contact, preamble, heading, summary,
    skill, edu, job_header, job_title, bullet, blank, other. Shared by parse() and the renderer (docxfill)."""
    out, section, preamble, prev = [], None, 0, None
    for p in document.paragraphs:
        text = _clean(p.text)
        heading = SECTION_NAMES.get(text.rstrip(":").lower())
        if not text:
            kind = "blank"
        elif heading:
            kind, section = "heading", heading
        elif section is None:
            kind = ("name", "headline", "contact")[preamble] if preamble < 3 else "preamble"
            preamble += 1
        elif section == "summary":
            kind = "summary"
        elif section == "skills":
            kind = "skill" if ":" in text else "other"
        elif section == "education":
            kind = "edu"
        elif section == "experience":
            kind = ("job_header" if _job_header(p.text) else "bullet" if _is_list_item(p) else
                    "job_title" if prev == "job_header" else "other")
        else:
            kind = "other"
        out.append((p, kind))
        prev = kind if kind != "blank" else prev
    return out


def parse(path: Path | str) -> dict:
    resume = {"headline": "", "summary": "", "skills": [], "experience": [], "education": []}
    summary: list[str] = []
    for paragraph, kind in walk(docx.Document(str(path))):
        text = _clean(paragraph.text)
        if kind == "headline":
            resume["headline"] = text
        elif kind == "summary":
            summary.append(text)
        elif kind == "skill":
            category, _, items = text.partition(":")
            resume["skills"].append({"category": _clean(category), "items": _split_items(items)})
        elif kind == "edu":
            dated = split_dated(paragraph.text, single=True)
            left = dated[0] if dated else text
            degree, sep, school = left.partition("|")
            entry = {"degree": _clean(degree), "school": _clean(school) if sep else ""}
            resume["education"].append({**entry, "dates": dated[1]} if dated else entry)
        elif kind == "job_header":
            company, dates = _job_header(paragraph.text)
            resume["experience"].append({"company": company, "dates": dates, "title": "", "bullets": []})
        elif kind == "job_title" and resume["experience"]:
            resume["experience"][-1]["title"] = text
        elif kind == "bullet" and resume["experience"]:
            resume["experience"][-1]["bullets"].append(_clean(_BULLET_GLYPH.sub("", paragraph.text)))
    resume["summary"] = " ".join(summary)
    return resume


def validate(resume: dict) -> list[str]:
    """Problems that would make the baseline unusable (empty sections, jobs without bullets)."""
    problems = [f"missing {key}" for key in ("headline", "summary", "skills", "experience", "education")
                if not resume.get(key)]
    for job in resume.get("experience", []):
        if not job["title"] or not job["bullets"]:
            problems.append(f"{job['company']}: title or bullets missing")
    return problems


def skill_terms(resume: dict) -> list[str]:
    """Flatten the skills block: 'AWS (S3, Glue)' -> ['AWS', 'S3', 'Glue']."""
    terms = []
    for line in resume["skills"]:
        for item in line["items"]:
            outer, _, inner = item.partition("(")
            terms.append(_clean(outer))
            terms.extend(_split_items(inner.rstrip(")")) if inner else [])
    return list(dict.fromkeys(t for t in terms if t))


KNOWN_HEADER = "# Skills I really have. /tailor auto-injects only these; others need a yes/no first.\n"


def _install_template(docx_path: Path, profile_dir: Path) -> Path | None:
    """Copy the resume in as template.docx (the renderer's layout). A different old template is kept as
    template.<timestamp>.docx, never deleted. Returns the backup path, if one was made."""
    target = profile_dir / "template.docx"
    if target.exists() and target.resolve() == docx_path.resolve():
        return None
    backup = None
    if target.exists() and target.read_bytes() != docx_path.read_bytes():
        backup = profile_dir / f"template.{datetime.now():%Y%m%d-%H%M%S}.docx"
        shutil.copy2(target, backup)
    shutil.copy2(docx_path, target)
    return backup


def _merge_known(resume: dict, known: Path) -> list[str]:
    """Union the baseline's skill terms into known_skills.txt (a baseline skill is a known skill)."""
    existing = known.read_text().splitlines() if known.exists() else [KNOWN_HEADER.rstrip("\n")]
    have = {line.strip().lower() for line in existing if line.strip() and not line.startswith("#")}
    added = [t for t in skill_terms(resume) if t.lower() not in have]
    if added or not known.exists():
        known.write_text("\n".join(existing + added) + "\n")
    return added


def import_baseline(docx_path: Path | str, profile_dir: Path) -> dict:
    """`baseline import`: parse the resume, then install it as template.docx, write baseline.json and add new
    skill terms to known_skills.txt. Nothing is written if the file does not parse. The renderer's skills-line
    record (render.json) goes stale by checksum and is re-measured on the next render."""
    docx_path = Path(docx_path)
    resume = parse(docx_path)
    problems = validate(resume)
    if problems:
        raise ValueError(f"could not parse {docx_path}: {problems}")
    profile_dir.mkdir(parents=True, exist_ok=True)
    backup = _install_template(docx_path, profile_dir)
    (profile_dir / "baseline.json").write_text(json.dumps(resume, indent=2, ensure_ascii=False) + "\n")
    added = _merge_known(resume, profile_dir / "known_skills.txt")
    return {**resume, "_import": {"template_backup": str(backup) if backup else None, "known_added": added}}
