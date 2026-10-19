"""Profiles from Settings > Profiles: one person or several, each a folder <home>/profiles/<id>/ with profile.yaml and
a base resume. Nothing here is personal by default; the person types their own details.

create() writes profile.yaml (name, target title families, years, location, contact lines for the tailored resume).
upload_resume() takes a .docx, parses it like `job-hunt-agent baseline import`, and keeps the file under resumes/.
"""
import json
import re

import yaml

from . import config, db
from .resume import baseline
from .titles import FAMILIES

ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
FIELDS = ("name", "headline_default", "email", "phone", "location", "linkedin", "github")
MAX_FIELD = 200
MAX_RESUME = 5 * 1024 * 1024
DOCX_MAGIC = b"PK\x03\x04"


def slug(name: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:40]
    return text or "me"


def families() -> list[str]:
    return sorted(FAMILIES)


def create(args: dict) -> dict:
    """A new profile; ValueError with the reason when a field is wrong or the id is taken."""
    name = " ".join(str(args.get("name") or "").split())[:MAX_FIELD]
    if not name:
        raise ValueError("type the person's name")
    pid = str(args.get("id") or slug(name.split()[0]))
    if not ID.match(pid):
        raise ValueError("the profile id is lower-case letters, digits, - and _")
    if pid in config.profile_ids():
        raise ValueError(f"there is already a profile called {pid}")
    wanted = args.get("families") or ["ml_engineer"]
    wanted = wanted if isinstance(wanted, list) else [w.strip() for w in str(wanted).split(",") if w.strip()]
    unknown = [f for f in wanted if f not in FAMILIES]
    if unknown:
        raise ValueError(f"unknown title families {unknown}; choose from {families()}")
    try:
        years = int(args.get("years") or 0)
    except (TypeError, ValueError):
        raise ValueError("years of experience is a whole number") from None
    if not 0 <= years <= 50:
        raise ValueError("years of experience is 0 to 50")
    data = {"name": name, "families": wanted, "years": years}
    for field in FIELDS[1:]:
        value = " ".join(str(args.get(field) or "").split())[:MAX_FIELD]
        if value:
            data[field] = value
    folder = config.PROFILES / pid
    folder.mkdir(parents=True, exist_ok=False)
    (folder / "profile.yaml").write_text("# Made in Settings > Profiles. Edit freely; the app reads it on every "
                                         "request.\n" + yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    conn = db.connect()
    try:
        with db.tx(conn):
            conn.execute("INSERT OR IGNORE INTO profiles(id, name) VALUES (?, ?)", (pid, name))
    finally:
        conn.close()
    return {"id": pid, "name": name}


def upload_resume(pid: str, file_name: str, data: bytes) -> dict:
    """The person's base resume (.docx): kept under resumes/ and parsed into baseline.json for matching and tailoring."""
    if pid not in config.profile_ids():
        raise ValueError(f"unknown profile {pid!r}")
    if not data or len(data) > MAX_RESUME:
        raise ValueError("the resume must be a .docx under 5 MB")
    if not data.startswith(DOCX_MAGIC) or not (file_name or "").lower().endswith(".docx"):
        raise ValueError("upload the resume as a Word .docx (Google Docs: File > Download > Microsoft Word)")
    safe = re.sub(r"[^A-Za-z0-9._ ()-]+", "_", file_name).strip() or "resume.docx"
    folder = config.PROFILES / pid / "resumes"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / safe
    target.write_bytes(data)
    try:
        resume = baseline.parse(target)
    except Exception as err:                                   # python-docx raises several kinds on a bad file
        target.unlink(missing_ok=True)
        raise ValueError(f"that .docx could not be opened: {err}") from None
    if not resume.get("experience") and not resume.get("skills"):
        target.unlink(missing_ok=True)
        raise ValueError("no Skills or Work Experience section was found. Headings named Summary, Skills and Work "
                         "Experience, with bullets as a Word list, read best.")
    home = config.PROFILES / pid
    (home / "baseline.json").write_text(json.dumps(resume, indent=2, ensure_ascii=False) + "\n")
    baseline._merge_known(resume, home / "known_skills.txt")
    return {"file": safe, "jobs": len(resume.get("experience") or []),
            "skills": sum(len(s.get("items") or []) for s in resume.get("skills") or []),
            "warnings": baseline.validate(resume)}
