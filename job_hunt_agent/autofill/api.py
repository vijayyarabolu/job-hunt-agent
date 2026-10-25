"""/api/autofill/* (the extension and the Answers page). server.route() hands every such path here.

The extension sends the LAN token in X-Jobhunt-Token (server._authorized); the web UI uses its cookie as usual.
"""
import json
import zlib

from .. import applyback, db, trackapi
from . import (address, assist, bank, canary, choices, draft, experience, fillstats, generate, health, keys, learn,
               match, meaning, package, pdftext, questions, resume, sponsor)
from .learn import LONG_QUESTION
from .keys import CATEGORIES

PREFIX = "/api/autofill/"
PRE_APPLY = ("saved", "tailored")
MAX_SNAPSHOT = 3 * 1024 * 1024
MAX_COUNT = 500
STEP_MAX = 60


class File:
    """Bytes for the browser: (data, content type, file name). server.py sends it as a Raw answer."""
    def __init__(self, data: bytes, ctype: str, name: str):
        self.data, self.ctype, self.name = data, ctype, name


def _job_args(args: dict) -> tuple[str | None, int | None]:
    uid = str(args.get("uid") or "").strip() or None
    app = args.get("app")
    app_id = int(app) if str(app or "").strip().isdigit() else None
    return uid, app_id


def _count(value) -> int:
    try:
        return max(0, min(MAX_COUNT, int(value or 0)))
    except (TypeError, ValueError):
        return 0


def _frames(raw) -> list[str]:
    if isinstance(raw, list):
        return [str(x) for x in raw]
    try:
        found = json.loads(raw or "[]")
    except ValueError:
        return []
    return [str(x) for x in found] if isinstance(found, list) else []


def _listing(conn, pid: str):
    return match.listing(conn, pid)


def _app_row(conn, pid: str, uid: str | None, app_id: int | None):
    if app_id:
        return conn.execute("SELECT id, address FROM applications WHERE profile = ? AND id = ?", (pid, int(app_id))).fetchone()
    return conn.execute("SELECT id, address FROM applications WHERE profile = ? AND job_uid = ?", (pid, uid)).fetchone()


def job_address(conn, pid: str, uid: str | None, app_id: int | None, jobs=match._UNSET, wait_s: float = 0) -> dict:
    """§7 + user 2026-10-14: the tracker row's Address when it is a full one; else the address for the city on this
    job's resume (home, saved, or the best OpenStreetMap one picked at once), written to the row's Address silently
    (the Sheet follows with the next sync). No confirm click."""
    if not uid and not app_id:
        return address.home(conn, pid)
    app = _app_row(conn, pid, uid, app_id)
    choice = meaning.job_answers(conn, pid, meaning.job_key(uid, app_id)).get(meaning.JOB_LOCATION)
    if choice == "home":                                # GOAL30 §3: the user chose "Where you live" for this job
        place = {**address.home(conn, pid), "choice": "home"}
        if app and place["kind"] in address.USABLE and app["address"] != place["line"]:
            trackapi.update(conn, pid, app["id"], {"address": place["line"]})
        return place
    jobs = _listing(conn, pid) if jobs is match._UNSET else jobs
    rg_job = resume.tailored_job(conn, pid, uid, app_id, jobs)
    own = address.from_tracker(app["address"]) if app else None
    # GOAL30 §3: the tracker row's address only when it is in the resume's city: the resume and the form never disagree
    if own and own["city_state"].lower() == _resume_city(conn, pid, rg_job).lower():
        return own
    place = address.auto(conn, pid, rg_job, wait_s)
    if app and place["kind"] in address.USABLE:
        trackapi.update(conn, pid, app["id"], {"address": place["line"]})
    return place


def _resume_city(conn, pid: str, rg_job: dict | None) -> str:
    """The City, ST the resume for this job shows (the user's own city for remote jobs and base resumes)."""
    return address.resume_city(pid, rg_job) or str(address.home(conn, pid).get("city_state") or "")


def location_view(conn, pid: str, place: dict | None, uid: str | None, app_id: int | None, jobs=match._UNSET) -> dict:
    """GOAL30 §3: the panel's Location row: the resume's city for this job, where the user lives, and which one forms get."""
    jobs = _listing(conn, pid) if jobs is match._UNSET else jobs
    home_city = str(address.home(conn, pid).get("city_state") or "")
    resume_city = _resume_city(conn, pid, resume.tailored_job(conn, pid, uid, app_id, jobs)) if uid or app_id else home_city
    choice = "home" if (place or {}).get("choice") == "home" else "resume"
    return {"resume": resume_city, "home": home_city, "choice": choice}


def choose_location(conn, pid: str, args: dict) -> dict:
    """POST address/choose {choice: resume | home}: for this job only; the tracker row follows."""
    uid, app_id = _job_args(args)
    choice = str(args.get("choice") or "")
    if choice not in ("resume", "home"):
        raise ValueError("choice: resume or home")
    meaning.keep_for_job(conn, pid, meaning.job_key(uid, app_id), meaning.JOB_LOCATION, choice, "Location")
    place = job_address(conn, pid, uid, app_id)
    return {"address": place, "answers": address.answers_for(place), "location": location_view(conn, pid, place, uid, app_id)}


SCOPES = ("everywhere", "company", "job")


def keep(conn, pid: str, args: dict) -> dict:
    """GOAL30 §1.3 / §4: a choice chip clicked or a ✎ edit in the panel, saved where the user chose: everywhere (default),
    this company, or this job only (the default for Location and open questions)."""
    uid, app_id = _job_args(args)
    label = " ".join(str(args.get("label") or "").split())[:bank.MAX_LABEL]
    value = str(args.get("value") if args.get("value") is not None else "").strip()
    scope = str(args.get("scope") or "everywhere")
    if not label or not value:
        raise ValueError("a question and an answer are needed")
    if scope not in SCOPES:
        raise ValueError("scope: everywhere, company or job")
    company = str(args.get("company") or "")[:200] or None
    options = [" ".join(str(o).split())[:300] for o in (args.get("options") or [])[:80] if str(o).strip()] \
        if isinstance(args.get("options"), list) else []
    hints = [str(h)[:80] for h in (args.get("hints") or [])[:10]] if isinstance(args.get("hints"), list) else []
    qnorm = keys.norm(label, company)
    key = keys.BY_KEY.get(str(args.get("key") or "")) or keys.classify(qnorm, hints) or keys.by_choices(options)
    if key is not None and (key.key in keys.SETTINGS or key.key in keys.DERIVED or key.key in bank.ADDRESS_KEYS
                            or key.key == "account_password"):
        key = None                                      # an address field: kept for this question's words
    target = key.key if key else bank.CUSTOM + qnorm
    item = {"label": label, "value": value, "kind": str(args.get("kind") or "text")[:20], "hints": hints,
            "options": options, "section": "", "how": "panel"}
    ctx = {"uid": uid, "app": app_id, "board": str(args.get("board") or "")[:40] or None, "company": company,
           "step": ""}
    if scope == "job":
        meaning.keep_for_job(conn, pid, meaning.job_key(uid, app_id), target, value, label)
    elif scope == "company":
        if not company:
            raise ValueError("this page is not matched to a company yet: pick the job first")
        learn._save_override(conn, pid, item, target, ctx, "")
    else:
        if key is not None and key.key in learn.OWN_QUESTION_KEYS:
            key, target = None, bank.CUSTOM + qnorm
        learn._save_global(conn, pid, item, key, qnorm, ctx, bank.load(conn, pid))
    return {"saved": scope, "target": target, "label": key.label if key else label, "answer": value}


def match_page(conn, pid: str, args: dict) -> dict:
    """The panel's first call: the job, the resume it will upload, the address for its city, and the person's
    open Needs-you count. With uid / app: that job (the posting this tab matched before the apply page); with
    recent: the picker's list."""
    jobs = _listing(conn, pid)
    uid, app_id = _job_args(args)
    if args.get("find"):                                # GOAL36 §5.2 "Link to a job": a search of the user's jobs
        return {"job": None, "found": match.search(conn, pid, str(args.get("find"))[:200])}
    if args.get("recent"):                              # "Not this job?": the picker, whatever the page matched
        found = {"job": None, "recent": match.recent(conn, pid, jobs), "resumegen": jobs is not None}
    elif uid or app_id:                                 # the job this tab came from (its posting matched)
        view = match._job_view(conn, uid, "tab") if uid else match._app_view(conn, pid, app_id, "tab")
        if view is None:
            raise KeyError(uid or app_id)
        found = {"job": view, "resumegen": jobs is not None}
    else:
        shows = {k: str(args.get(k) or "")[:200] for k in ("company", "title")}
        found = match.find(conn, pid, str(args.get("url") or ""), _frames(args.get("frames")), jobs=jobs,
                           shows=shows if any(shows.values()) else None)
    job = found.get("job")
    choice = place = badge = None
    if job:
        choice = resume.pick(conn, pid, job["uid"], job["app"], jobs, offline=jobs is None)
        place = job_address(conn, pid, job["uid"], job["app"], jobs)
        badge = sponsor.badge_for(conn, pid, job["uid"], job["app"])
    return {**found, "resume": choice, "address": place, "needs": questions.count(conn, pid),
            "sponsor": sponsorship(conn, pid, job, badge) if job else None,
            "location": location_view(conn, pid, place, job["uid"], job["app"], jobs) if job else None}


def sponsorship(conn, pid: str, job: dict, badge: dict | None) -> dict:
    """The panel's sponsorship slot (GOAL25 §2 slot, GOAL24 §6 content): the job's badge, the future-sponsorship answer
    it will fill and why (this company's own answer first, then GOAL24's Yes at a Sponsors / Cap-exempt company, then
    the saved one) and the "need it now" answer."""
    saved = bank.load(conn, pid)
    out = sponsor.panel(badge, saved)
    own = learn.overrides_for(conn, pid, job.get("company"))
    mine = f"your answer for {job.get('company')}"
    if own.get("sponsor_future"):
        out = {**out, "future": own["sponsor_future"], "why": mine}
    now = own.get("sponsor_now") or str((saved.get("sponsor_now") or {}).get("answer") or "")
    return {**out, "now": now, "now_why": mine if own.get("sponsor_now") else "your saved answer"}


def _resolve(conn, pid: str, args: dict, ask_claude: bool = True) -> dict:
    uid, app_id = _job_args(args)
    ctx = {k: str(args.get(k) or "")[:300] for k in ("board", "company", "section", "source_url")}
    ctx["job_key"] = meaning.job_key(uid, app_id)          # GOAL30 §4: the user's answers for this job only
    ctx["ask_claude"] = ask_claude and pid != canary.PID    # GOAL30 §1: unknown wordings, one Haiku call (never the
                                                            # nightly check's fake person: no one's Claude usage)
    ctx["long_question_all"] = LONG_QUESTION
    place = job_address(conn, pid, uid, app_id, wait_s=address.LOOKUP_WAIT_S)
    ctx["address"] = address.answers_for(place)
    ctx["sponsor_yes"] = sponsor.says_yes(sponsor.badge_for(conn, pid, uid, app_id), bank.load(conn, pid))
    jobs: list = []

    def resume_jobs() -> list:                         # read once, only when a form asks for the current job
        if not jobs and (uid or app_id):
            jobs.extend(experience.for_job(conn, pid, resume.choose(conn, pid, uid, app_id)).get("jobs") or [])
        return jobs
    ctx["resume_jobs"] = resume_jobs
    return {"answers": bank.resolve(conn, pid, args.get("fields"), ctx), "address": place}


def profile_data(conn, pid: str, uid: str | None, app_id: int | None) -> dict:
    """Work history (with each job's "• " bullets), education and the summary of the resume being uploaded (user
    2026-10-14: every board, Workday's My Experience too), links from the bank, skills from the tailored resume."""
    choice = resume.choose(conn, pid, uid, app_id) if uid or app_id else {}
    return {**bank.history(conn, pid), **experience.for_job(conn, pid, choice), "skills": draft.skills_for(pid, choice)}


PROFILE_GROUPS = {"contact": ("first_name", "last_name", "full_name", "preferred_name", "email", "phone", "phone_code",
                              "phone_type", "address1", "city", "state", "zip", "country", "location"),
                  "links": ("linkedin", "github", "portfolio", "twitter"),
                  "work": ("work_auth", "sponsor_now", "sponsor_future", "salary", "start_date", "notice", "relocate",
                           "onsite", "travel", "years_exp", "current_company", "current_title", "how_heard"),
                  "education": ("degree", "school", "field_of_study", "grad_date", "gpa"),
                  "eeo": ("gender", "hispanic", "race", "veteran", "disability", "orientation", "transgender", "pronouns",
                          "signature_name")}


def full_profile(conn, pid: str, args: dict) -> dict:
    """GOAL27 "profile first, once": the tailored resume and the saved answers as one structured profile, before
    any page is touched. Every built-in question key resolved for this job (company answers, the sponsorship
    setting, the address for the resume's city, legal / EEO only from what the user saved), plus the work history with its
    "• " bullets, education, skills and summary of the resume this job uploads. The extension fills canonical fields
    from it; a page's own questions still go through resolve (the user's saved wording, company-only answers)."""
    uid, app_id = _job_args(args)
    data = profile_data(conn, pid, uid, app_id)
    fields = [{"id": k.key, "key": k.key, "label": k.label, "kind": "text", "required": True}
              for k in keys.CATALOGUE if k.key not in keys.DERIVED and (k.patterns or k.hints)]
    got = _resolve(conn, pid, {**args, "fields": fields}, ask_claude=False)
    answers = {a["id"]: {"value": a["value"], "option": a["option"], "status": a["status"], "legal": a["legal"],
                         "source": a["source"], **({"place": a["place"]} if a.get("place") else {})}
               for a in got["answers"]}
    groups = {name: {k: answers[k]["value"] for k in ks if answers.get(k, {}).get("status") == "fill"}
              for name, ks in PROFILE_GROUPS.items()}
    missing = sorted(k for k, a in answers.items() if a["status"] == "needs" and not keys.BY_KEY[k].blank_ok)
    return {**data, "answers": answers, **groups, "address": got["address"], "missing": missing,
            "counts": {"jobs": len(data.get("jobs") or []), "schools": len(data.get("schools") or []),
                       "skills": len(data.get("skills") or []), "answers": sum(1 for a in answers.values() if a["status"] == "fill")}}


def applied(conn, pid: str, args: dict, now) -> dict:
    """The confirmation page (or the panel's Mark applied): the same code as Mark applied, once."""
    uid, app_id = _job_args(args)
    if not uid and not app_id:
        raise ValueError("this page is not matched to a job")
    if uid:
        row = conn.execute("SELECT id, status FROM applications WHERE profile = ? AND job_uid = ?",
                           (pid, uid)).fetchone()
    else:
        row = conn.execute("SELECT id, status FROM applications WHERE profile = ? AND id = ?",
                           (pid, app_id)).fetchone()
        if row is None:
            raise KeyError(app_id)
    if row is not None and row["status"] not in PRE_APPLY:
        return {"app": row["id"], "already": True, "status": row["status"]}
    choice = resume.choose(conn, pid, uid, None if uid else app_id)
    base_id = choice.get("resume_id") if choice.get("kind") == "base" else None     # the base file it uploaded
    out = applyback.confirm(conn, pid, now, uid, None if uid else app_id, resume_id=base_id)
    place = job_address(conn, pid, uid, app_id)
    if place["kind"] in ("home", "saved"):             # §7: what the user told this company, in the Address column
        trackapi.update(conn, pid, out["app"], {"address": place["line"]})
    return {"app": out["app"], "already": False, "attached": out.get("attached"), "ask": out.get("ask"),
            "address": place.get("line")}


def report(conn, pid: str, args: dict, now) -> dict:
    """After a fill: one log line (counts only) and the questions it could not answer."""
    uid, app_id = _job_args(args)
    board = str(args.get("board") or "")[:40] or None
    company = str(args.get("company") or "")[:200] or None
    needs = args.get("needs") if isinstance(args.get("needs"), list) else []
    open_now = questions.record(conn, pid, needs, board, company)
    with db.tx(conn):
        conn.execute("INSERT INTO autofill_log(profile, job_uid, app_id, board, step, filled, needs, skipped, at) "
                     "VALUES (?,?,?,?,?,?,?,?,?)",
                     (pid, uid, app_id, board, str(args.get("step") or "")[:STEP_MAX], _count(args.get("filled")),
                      _count(len(needs)), _count(args.get("skipped")), db.iso(now)))
    if isinstance(args.get("fields"), list):            # GOAL27: every field's result, for Health
        health.record_fields(conn, pid, board, str(args.get("step") or ""), args["fields"], str(args.get("version") or ""), now)
    return {"open": open_now}


def snapshot(conn, pid: str, args: dict, now) -> dict:
    """"Send page snapshot": the page's form HTML (password fields carry no value) kept to fix a failing page."""
    html = str(args.get("html") or "")
    if not html:
        raise ValueError("no page to keep")
    if len(html) > MAX_SNAPSHOT:
        raise ValueError("the page is too large to keep")
    with db.tx(conn):
        cur = conn.execute("INSERT INTO autofill_snapshots(profile, url, board, html, at) VALUES (?,?,?,?,?)",
                           (pid, str(args.get("url") or "")[:match.MAX_URL], str(args.get("board") or "")[:40],
                            zlib.compress(html.encode()), db.iso(now)))
    return {"id": cur.lastrowid}


def answers_page(conn, pid: str) -> dict:
    questions.tidy(conn, pid)                                         # address / cover letter: never questions
    fixed = choices.fix_typos(conn, pid, bank.load(conn, pid))       # "Hetrosexual" -> the real choice
    return {"needs": questions.open_list(conn, pid), "bank": bank.listing(conn, pid),
            "addresses": address.listing(conn, pid), "home": address.home(conn, pid),
            "categories": [list(c) for c in CATEGORIES], "open": questions.count(conn, pid),
            "learned": learn.learned_list(conn, pid), "overrides": learn.overrides_list(conn, pid),
            "snippets": learn.snippets(conn, pid, limit=50), "stats": fillstats.summary(conn, pid), "fixed": fixed,
            "extension": {"version": package.version(), "zip": PREFIX + "extension.zip"}}


def get(conn, pid: str, path: str, args: dict):
    name = path[len(PREFIX):]
    uid, app_id = _job_args(args)
    if name == "ping":
        return {"ok": True, "profile": pid, "open": questions.count(conn, pid), "version": package.version()}
    if name == "health":                                # the extension asks before every fill
        return health.check(conn, pid)
    if name == "health/report":                         # Settings › Autofill › Health
        return health.report(conn, pid)
    if name == "notices":                               # shown when job-hunt-agent opens: canary drops, a newer extension
        return {"notices": health.notices(conn), "extension": health.extension(conn, pid)}
    if name == "match":
        return match_page(conn, pid, args)
    if name == "resume":
        if not uid and not app_id:
            raise ValueError("uid or app is required")
        return File(*resume.file(conn, pid, uid, app_id))
    if name == "profile":
        return full_profile(conn, pid, args)
    if name == "address":                              # the panel polls while OSM is looking
        return job_address(conn, pid, uid, app_id)
    if name == "answers":
        return answers_page(conn, pid)
    if name == "count":
        return {"open": questions.count(conn, pid)}
    if name == "answer/status":                        # the panel polls a Generate click
        return generate.status(args)
    if name == "snippets":                              # the panel's "Use a saved answer"
        return {"snippets": learn.snippets(conn, pid, str(args.get("q") or "")[:500])}
    if name == "cover.pdf":                            # GOAL27 §5: the cover letter as a file for an upload box
        text = assist.cover_text(conn, pid, uid, app_id)
        return File(pdftext.render(text, "Cover letter"), "application/pdf", assist.cover_name(conn, pid, uid, app_id))
    if name == "extension.zip":
        return File(package.zip_bytes(), "application/zip", package.ZIP_NAME)
    raise KeyError(path)


def post(conn, pid: str, path: str, args: dict, now):
    name = path[len(PREFIX):]
    uid, app_id = _job_args(args)
    if name == "resolve":
        return _resolve(conn, pid, args)
    if name == "address/use":                          # the panel's Change: this city's address, and this job's row
        place = address.use(conn, pid, str(args.get("city_state") or ""), args.get("street") or None,
                            args.get("zip") or None)
        app = _app_row(conn, pid, uid, app_id) if uid or app_id else None
        if app:
            trackapi.update(conn, pid, app["id"], {"address": place["line"]})
        return place
    if name == "address/choose":                       # GOAL30 §3: Location from the resume or where the user lives
        return choose_location(conn, pid, args)
    if name == "answer/keep":                          # GOAL30 §1.3 / §4: a chip or a ✎ edit, saved where the user chose
        return keep(conn, pid, args)
    if name == "address/another":
        return address.another(conn, pid, str(args.get("city_state") or ""))
    if name == "address/forget":
        return address.forget(conn, pid, str(args.get("city_state") or ""))
    if name == "report":
        return report(conn, pid, args, now)
    if name == "remember":
        found = match.remember(conn, pid, str(args.get("url") or ""), uid, app_id)
        job = found["job"]
        return {**found, "resume": resume.choose(conn, pid, job["uid"], job["app"])}
    if name == "applied":
        return applied(conn, pid, args, now)
    if name == "snapshot":
        return snapshot(conn, pid, args, now)
    if name == "suggest":                              # GOAL27 §4: short answers from the resume (review marks)
        return assist.suggest(conn, pid, args)
    if name == "choose":                               # 1.3.1: a saved answer no option matched: Claude picks one
        return assist.choose(conn, pid, args)
    if name == "cover":                                # GOAL27 §5: a cover letter on the user's click
        return assist.cover(conn, pid, args)
    if name == "score":                                # GOAL27 §6: match % and missing keywords on any job page
        return assist.score(conn, pid, args)
    if name == "save":                                 # GOAL27 §6: Save job to the tracker from any page
        return assist.save_job(conn, pid, args)
    if name == "answer/generate":                      # GOAL29 §5: Generate / Regenerate / Shorter, on the user's click
        return generate.generate(conn, pid, args)
    if name == "answer/lookup":                        # the cached answers of a page's open questions
        return generate.lookup(pid, args, conn)
    if name == "answer/save":                          # Save to Answers
        return generate.save(conn, pid, args)
    if name == "answer":
        return bank.save(conn, pid, str(args.get("key") or ""), args.get("answer"))
    if name == "forget":
        return bank.forget(conn, pid, str(args.get("key") or ""))
    if name == "needs/answer":
        return questions.answer(conn, pid, int(args["id"]), args.get("answer"))
    if name == "needs/dismiss":
        return questions.dismiss(conn, pid, int(args["id"]))
    if name == "learn":                                 # what the user filled by hand on a step
        return learn.learn(conn, pid, args)
    if name == "learn/undo":
        return learn.undo(conn, pid, int(args["id"]))
    if name == "override":
        return learn.set_override(conn, pid, str(args.get("company") or ""), str(args.get("key") or ""),
                                  args.get("answer"))
    if name == "snippet/remove":
        return learn.remove_snippet(conn, pid, int(args["id"]))
    if name == "error":                                 # a fill error / unknown layout, with the page
        return health.record_error(conn, pid, args, now)
    if name == "notice/close":
        return health.close_notice(conn, int(args["id"]))
    raise KeyError(path)
