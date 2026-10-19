"""GOAL5 §3 evidence (decision 010): per vendor, how many open title-matched jobs with JD text carry a score for
every resume under that vendor's model, and the model's reasoning for 2 real jobs per vendor (picked
deterministically: the newest open jobs whose JD has at least MIN_TERMS keywords). A modeled vendor with no job on an
active board falls back to jobs kept from a board robots later dropped (their last allowed fetch, scored with the
current extractor and labeled so); a vendor with neither is listed with the reason (decision 011)."""
from collections import Counter, defaultdict

from . import fits, vendor_model


MIN_TERMS = 4
PER_VENDOR = 2
NO_JOBS_REASON = ("no open title-matched job with a JD of >= 4 keywords from an allowed feed applies through it "
                  "(vendors whose feeds are a coverage gap: decision 011)")
REASONS = {"phenom": "Phenom boards are fetched (United, Thermo Fisher, Southwest), but each job applies through the "
                     "tenant's back-end ATS (Taleo, Workday), so that vendor's model scores it"}


def _dropped_rows(conn, profile: dict) -> list[dict]:
    """Open title-matched jobs with JD text on boards robots dropped, with fit terms from the current extractor."""
    where, params = fits.family_filter(tuple(profile["families"]))
    inp = fits.profile_inputs(profile)
    rows = conn.execute(
        "SELECT j.uid, j.board, j.company, j.title, j.url, j.apply_url, j.families, j.min_years, j.description, "
        "b.dropped_at FROM jobs j JOIN boards b ON b.board = j.board WHERE j.closed_at IS NULL AND b.active = 0 "
        f"AND b.dropped LIKE 'robots:%' AND j.description IS NOT NULL AND {where} "
        "ORDER BY coalesce(j.first_posted_at, j.first_seen) DESC", params).fetchall()
    out = []
    for r in rows:
        fit = fits.compute(r["title"], r["description"], inp)
        out.append({**{k: r[k] for k in r.keys() if k != "description"}, "fit_terms": fit["terms"],
                    "jd_state": fit["jd_state"]})
    return out


def _rows(conn, profile: dict):
    where, params = fits.family_filter(tuple(profile["families"]))
    return conn.execute(
        "SELECT j.uid, j.board, j.company, j.title, j.url, j.apply_url, j.families, j.min_years, j.first_posted_at, "
        "j.first_seen, j.description IS NOT NULL AS has_jd, f.terms AS fit_terms, f.jd_state FROM jobs j "
        "JOIN boards b ON b.board = j.board LEFT JOIN fits f ON f.profile = ? AND f.uid = j.uid "
        f"WHERE j.closed_at IS NULL AND b.active = 1 AND b.review = 0 AND {where} "
        "ORDER BY coalesce(j.first_posted_at, j.first_seen) DESC", [profile["id"], *params]).fetchall()


def coverage(conn, profile: dict) -> dict:
    """{vendor: {jobs_with_jd, scored_all, no_keywords, modeled, label}}; 'jobs with JD' = a fits row with JD text
    (ok/thin); a JD naming no tracked skill has no keyword-based estimate and is counted as no_keywords."""
    mine = fits.profile_inputs(profile).resumes
    out: dict = defaultdict(Counter)
    labels = {}
    for r in _rows(conn, profile):
        if r["jd_state"] not in ("ok", "thin"):
            continue
        result = vendor_model.score(dict(r), mine, profile.get("years"))
        c = out[result.vendor]
        c["jobs_with_jd"] += 1
        if not result.terms:
            c["no_keywords"] += 1                       # the card says so: "no tracked skill keywords in this JD"
        else:
            c["scored_all"] += len(mine) > 0 and all(s.pct is not None for s in result.scores)
        labels[result.vendor] = (result.label, result.modeled)
    counts = ("jobs_with_jd", "scored_all", "no_keywords")
    return {v: {**{k: c[k] for k in counts}, "label": labels[v][0], "modeled": labels[v][1], "resumes": len(mine)}
            for v, c in sorted(out.items())}


def _pick(rows, mine, years, per: int, skip=frozenset()) -> dict[str, list]:
    picked: dict[str, list] = defaultdict(list)
    for r in rows:
        if r["jd_state"] != "ok":
            continue
        result = vendor_model.score(dict(r), mine, years)
        if (result.vendor not in skip and len(result.terms) >= MIN_TERMS and len(picked[result.vendor]) < per
                and result.best is not None):
            picked[result.vendor].append((dict(r), result))
    return picked


def samples(conn, profile: dict, per: int = PER_VENDOR) -> dict[str, list]:
    """vendor -> up to `per` (row, result) of its newest open jobs with an 'ok' JD and >= MIN_TERMS keywords; vendors
    with none on an active board take them from robots-dropped boards (rows carry 'dropped_at')."""
    mine, years = fits.profile_inputs(profile).resumes, profile.get("years")
    picked = _pick(_rows(conn, profile), mine, years, per)
    extra = _pick(_dropped_rows(conn, profile), mine, years, per, skip=frozenset(picked))
    return {**picked, **extra}


def explain_job(conn, profile: dict, uid: str) -> list[str]:
    row = next((r for r in _rows(conn, profile) if r["uid"] == uid), None)
    if row is None:
        raise KeyError(uid)
    mine = fits.profile_inputs(profile).resumes
    result = vendor_model.score(dict(row), mine, profile.get("years"))
    return _job_lines(dict(row), result, mine)


def _job_lines(row: dict, result: vendor_model.Result, mine) -> list[str]:
    note = ([f"Source: kept from this board's last allowed fetch; robots.txt dropped the board on "
             f"{row['dropped_at'][:10]} (scored with the current extractor)."] if row.get("dropped_at") else [])
    head = [f"## {row['company']} · {row['title']}", f"{row['url']}", *note,
            f"Vendor: {result.vendor} · {result.label}",
            "| resume | vendor estimate | generic keyword % | grade | parsing checks |", "|---|---|---|---|---|"]
    head += [f"| {s.label}{' (best)' if result.best and s.id == result.best.id else ''} | {s.pct}% | {s.generic}% | "
             f"{s.grade or '-'} | {', '.join(s.parse_fired) or 'none fired'} |" for s in result.scores]
    lines = head + ["", "Reasoning per resume:", "```"]
    for s in result.scores:
        resume = next((r for r in mine if r.id == s.id), None)
        lines += vendor_model.explain(result, s, resume) + [""]
    return lines + ["```", ""]


def evidence(conn, profile: dict) -> list[str]:
    cov = coverage(conn, profile)
    lines = [f"# Vendor models: {profile['id']}", "",
             "Every open title-matched job with JD text, by vendor: how many carry a score for every resume under "
             "the vendor's model, and how many JDs name none of the tracked skills (no keyword-based estimate is "
             f"possible; the card says so). Resumes in this profile: {len(fits.profile_inputs(profile).resumes)}. "
             "(`job-hunt-agent vendor-report`)", "",
             "| vendor | label | jobs with JD | scored for all resumes | JD names no tracked skill |",
             "|---|---|---|---|---|"]
    lines += [f"| {v} | {c['label']} | {c['jobs_with_jd']} | {c['scored_all']} | {c['no_keywords']} |"
              for v, c in cov.items()]
    lines += ["", f"## Reasoning: {PER_VENDOR} jobs per vendor (newest open jobs with >= {MIN_TERMS} JD keywords)", ""]
    mine = fits.profile_inputs(profile).resumes
    found = samples(conn, profile)
    for vendor, picked in sorted(found.items()):
        lines.append(f"# {vendor}")
        for row, result in picked:
            lines += _job_lines(row, result, mine)
    missing = sorted(set(vendor_model.models()) - set(found))
    if missing:
        lines += ["# Modeled vendors without a real job here", ""]
        lines += [f"- {v}: {REASONS.get(v, NO_JOBS_REASON)}" for v in missing]
    return lines


