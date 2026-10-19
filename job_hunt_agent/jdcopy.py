"""Copy JD helpers for the manual resume workflow (GOAL4 §1/§3): the clean JD with company, title and URL
on top, ready to paste into Claude chat with the resume-editor skill; optionally the keyword match view for
one resume (the best by default, or the one picked in the UI): resume used, match %, matched and missing keywords
(decision 007, read from the fits cache; no LLM). With keywords it is ATS-aware (decision 009): how the job's ATS
handles a resume (vendor docs only), every keyword in the JD's own words with both acronym forms, and the
application questions where the ATS exposes them. GOAL5 §3 (decision 010): the resume used, its % and the
matched / missing lists come from the job's vendor model (with the model's reasoning) when that vendor documents
its matching; otherwise the generic keyword view."""
from . import ats_profile, config, fits, keywords, resumes, vendor_model
from .db import unpack
from .text import strip_boilerplate

RULE = "-" * 40


def header(job: dict) -> str:
    lines = [f"Company: {job['company']}", f"Title: {job['title']}"]
    if job.get("location"):
        lines.append(f"Location: {job['location']}")
    lines.append(f"URL: {job.get('url') or job.get('apply_url') or ''}")
    return "\n".join(lines)


def resume_scores(conn, profile: dict, uid: str, families: str | None = None):
    """(scores per resume, title-mapped id) from the fit cache; (None, None) when the job is not scored yet."""
    fit = conn.execute("SELECT terms FROM fits WHERE profile = ? AND uid = ?", (profile["id"], uid)).fetchone()
    mine = fits.profile_inputs(profile).resumes
    if fit is None or not mine:
        return None, None
    tagged = (families or "").strip(",").split(",")
    title = resumes.for_families(mine, [f for f in profile.get("families") or () if f in tagged])
    return keywords.score_all(fit["terms"], mine), title.id if title else None


def vendor_estimate(conn, profile: dict, job: dict) -> vendor_model.Result | None:
    """The job's vendor model result, when the vendor documents its matching and the row has literal data."""
    fit = conn.execute("SELECT terms FROM fits WHERE profile = ? AND uid = ?", (profile["id"], job["uid"])).fetchone()
    mine = fits.profile_inputs(profile).resumes
    if fit is None or not mine:
        return None
    result = vendor_model.score({**job, "fit_terms": fit["terms"]}, mine, profile.get("years"))
    return result if result.modeled and result.best is not None else None


def pick(scores: list[keywords.ResumeScore], wanted: str | None, title: str | None) -> keywords.ResumeScore:
    chosen = next((s for s in scores if s.id == wanted), None)
    return chosen or keywords.best(scores, title)


def vendor_block(result: vendor_model.Result, chosen: vendor_model.VendorScore, mine, picked: bool,
                 known: frozenset[str] = frozenset()) -> str:
    """The vendor estimate for the chosen resume, every resume's estimate, the keywords in the vendor's form, the
    parsing checks and the model's reasoning (the same function as the card and the drawer)."""
    how = "you picked it" if picked else f"best of {len(result.scores)}" if len(result.scores) > 1 else "your resume"
    grade = f", grade {chosen.grade}" if chosen.grade else ""
    missing = [n + (" (in known_skills: you have it, add it to the resume)" if n in known else "") for n in chosen.missing]
    lines = [f"{result.label}. How this is computed: job-hunt-agent /how/ats?vendor={result.vendor}",
             f"Resume used: {chosen.label} ({how})",
             f"Estimated {result.vendor_label} match: {chosen.pct}%{grade} (generic keyword match {chosen.generic}%)"]
    if len(result.scores) > 1:
        lines.append("All resumes: " + " · ".join(f"{s.label} {s.pct}%" for s in result.scores))
    lines += [f"Matched, in the form {result.vendor_label} needs ({len(chosen.matched)}): "
              f"{', '.join(chosen.matched) or 'none'}",
              f"Missing, in the form {result.vendor_label} needs ({len(chosen.missing)}, most important first): "
              f"{', '.join(missing) or 'none'}",
              "Parsing checks on this resume: " + (", ".join(chosen.parse_fired + chosen.tips) or "none fired"),
              "Why (model reasoning):"]
    resume = next((r for r in mine if r.id == chosen.id), None)
    return "\n".join(lines + vendor_model.explain(result, chosen, resume))


def keywords_block(scores: list[keywords.ResumeScore] | None, chosen: keywords.ResumeScore | None,
                   known: frozenset[str], picked: bool = False) -> str:
    if not scores or chosen is None:
        return "Hard skills in this JD: (not scored yet; try again after the next fetch)"
    how = "you picked it" if picked else f"best of {len(scores)}" if len(scores) > 1 else "your resume"
    missing = [n + (" (in known_skills: you have it, add it to the resume)" if n in known else "") for n in chosen.missing]
    lines = [f"Resume used: {chosen.label} ({how})",
             f"Match: {chosen.pct}% ({len(chosen.matched)} of {chosen.n_total} JD keywords)" if chosen.n_total else
             "Match: none of the tracked skills appear in this JD, so there is no keyword match"]
    if len(scores) > 1:
        lines.append("All resumes: " + " · ".join(f"{s.label} {s.pct}%" for s in scores))
    lines += [f"Hard skills in this JD ({chosen.n_total}): {', '.join(chosen.keywords) or 'none found'}",
              f"Matched keywords ({len(chosen.matched)}): {', '.join(chosen.matched) or 'none'}",
              f"Missing keywords ({len(missing)}, most important first): {', '.join(missing) or 'none'}"]
    return "\n".join(lines)


def jd_text(conn, pid: str, uid: str, keywords: bool = False, resume: str | None = None) -> dict:
    """{'text': clipboard text, 'company', 'title', 'resume', 'resumes'}; KeyError for an unknown uid."""
    row = conn.execute("SELECT j.uid, j.board, j.ext_id, j.company, j.title, j.location, j.url, j.apply_url, "
                       "j.description, j.families, j.min_years, b.key AS board_key FROM jobs j "
                       "LEFT JOIN boards b ON b.board = j.board WHERE j.uid = ?", (uid,)).fetchone()
    if row is None:
        raise KeyError(uid)
    job = dict(row)
    body = strip_boilerplate(unpack(job["description"]) or "") or "(no JD text stored for this job)"
    parts, out = [header(job)], {"company": job["company"], "title": job["title"], "resume": None, "resumes": []}
    if keywords:
        scores, title = resume_scores(conn, config.profile(pid), uid, job["families"]) \
            if pid in config.profile_ids() else (None, None)
        chosen = pick(scores, resume, title) if scores else None
        result = vendor_estimate(conn, config.profile(pid), job) if scores else None
        if result is not None:
            vchosen = next((s for s in result.scores if s.id == resume), None) or result.best
            mine = fits.profile_inputs(config.profile(pid)).resumes
            parts.append(vendor_block(result, vchosen, mine, picked=bool(resume) and vchosen.id == resume,
                                      known=fits.known_skills(pid)))
            chosen = next(s for s in scores if s.id == vchosen.id)
            out.update(resume=vchosen.id, resumes=[{"id": s.id, "label": s.label, "pct": s.pct} for s in result.scores],
                       vendor={"vendor": result.vendor, "label": result.label})
        else:
            parts.append(keywords_block(scores, chosen, fits.known_skills(pid), picked=bool(resume) and
                                        chosen is not None and chosen.id == resume))
            out.update(resume=chosen.id if chosen else None,
                       resumes=[{"id": s.id, "label": s.label, "pct": s.pct} for s in scores or ()])
        ats = ats_profile.ats_of(job["board"])
        box, asked = ats_profile.box(ats), ats_profile.questions(ats, job["board_key"], job["ext_id"])
        forms = ats_profile.keyword_lines(chosen.keywords, set(chosen.matched), chosen.label, body) if chosen else []
        parts[1:1] = ["\n".join(box)]                          # right under the company / title / URL
        parts += ["\n".join(forms)] if forms else []
        parts.append("\n".join(asked))
        out["ats"] = {"name": ats, "box": box, "questions": asked, "keywords": forms}
    parts += [RULE, body]
    return {**out, "text": "\n\n".join(parts) + "\n"}
