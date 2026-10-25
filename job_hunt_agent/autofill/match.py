"""GOAL23 §4 "Which job is this page?": an apply page's URL -> the feed job (uid) or the outside job (tracker row).

In order: a page the person matched by hand before (autofill_urls); the feed job the URL names (jobfetch.parse_url,
else an id in the URL on the same site or board, via the jobs_ext index); the resumegen job whose posting link is
this page (a feed job, or a pasted JD's tracker row via rg_links); a tracker row whose posting / apply link is this
page. Nothing found (a LinkedIn post that sent the user to the company's Workday) -> the panel shows the user's recent resumes
to pick from, and the pick is remembered for every step of that application.
"""
import re
from datetime import timedelta
from urllib.parse import parse_qsl, urlsplit

from .. import db, jobfetch, outside, outside_jobs, rgclient, rgjobs
from ..text import norm

RECENT_DAYS = 14
MAX_RECENT = 60
MAX_URL = 2000
MAX_FRAMES = 6
MAX_TRACKER_ROWS = 5000
LONG_ID = 12                                                          # an id this long is unique on its own
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_ID = re.compile(r"^(?=[A-Za-z0-9-]*\d)[A-Za-z0-9-]{3,40}$")
_HEAD_ID = re.compile(r"^([A-Za-z0-9]*\d[A-Za-z0-9]{5,})-")          # breezy / smartrecruiters "<id>-slug"
_TAIL_ID = re.compile(r"_([A-Za-z0-9-]*\d[A-Za-z0-9-]*)$")            # workday "Title_R0123"
ID_PARAMS = ("jobid", "job", "gh_jid", "token", "id", "jid", "req", "requisitionid", "reqid", "jobreqid",
             "postingid", "job_id", "ashby_jid")
APPLY_TAIL = re.compile(r"/(apply(/[a-z]+)?|application|applymanually|autofillwithresume|usemylastapplication)$",
                        re.I)
KEY_PARAMS = (*ID_PARAMS, "company", "for", "career_job_req_id", "jobreq")
LANG = re.compile(r"^/[a-z]{2}(-[a-z]{2})?(?=/)", re.I)


def _check(url) -> str:
    text = str(url or "").strip()
    if not text.startswith(("http://", "https://")) or len(text) > MAX_URL:
        raise ValueError("a page address (http or https) is required")
    return text


def host(url: str) -> str:
    name = (urlsplit(url).hostname or "").lower()
    return name[4:] if name.startswith("www.") else name


def url_key(url: str) -> str:
    """host + path, lower case, without the language prefix, the trailing apply step or slash, plus the query's
    job / company ids (?gh_jid=, Taleo ?job=, SuccessFactors ?company=): every step of one application has the same
    key, and two jobs on one careers page never share one."""
    parts = urlsplit(url)
    path = LANG.sub("", parts.path or "").rstrip("/")
    while APPLY_TAIL.search(path):
        path = APPLY_TAIL.sub("", path).rstrip("/")
    ids = sorted(f"{k.lower()}={v.strip().lower()}" for k, v in parse_qsl(parts.query)
                 if k.lower() in KEY_PARAMS and v.strip())
    return (host(url) + path).lower()[:400] + ("?" + "&".join(ids) if ids else "")[:100]


def tokens(url: str) -> list[str]:
    """Id-like parts of a URL (uuids, numbers, requisition ids), longest first."""
    parts = urlsplit(url)
    found = set(_UUID.findall(url))
    for seg in parts.path.split("/"):
        for rule in (_TAIL_ID, _HEAD_ID):
            hit = rule.search(seg)
            if hit:
                found.add(hit.group(1))
        if _ID.match(seg):
            found.add(seg)
    for name, value in parse_qsl(parts.query):
        if name.lower() in ID_PARAMS and _ID.match(value.strip()):
            found.add(value.strip())
    return sorted(found, key=len, reverse=True)[:12]


def _same_site(url: str, job: dict) -> bool:
    page = host(url)
    for link in (job["url"], job["apply_url"]):
        other = host(link or "")
        if other and (other == page or ("." in other and other.split(".", 1)[1] == page.split(".", 1)[-1])):
            return True
    board = str(job["board"] or "").split(":", 1)[-1].split("/")[0].lower()
    return len(board) > 2 and board in url.lower()


def feed_job(conn, url: str) -> str | None:
    """The uid of the feed job this URL is (one clear answer, else None)."""
    parsed = jobfetch.parse_url(url, jobfetch.db_resolver(conn))
    if parsed and conn.execute("SELECT 1 FROM jobs WHERE uid = ?", (parsed["uid"],)).fetchone():
        return parsed["uid"]
    ids = tokens(url)
    if not ids:
        return None
    rows = [dict(r) for r in conn.execute(f"SELECT uid, board, url, apply_url FROM jobs WHERE ext_id IN "   # a short id
                                          f"({','.join('?' * len(ids))}) ORDER BY instr(lower(coalesce(url, '')), ?) = 0 "
                                          f"LIMIT 50", [*ids, host(url)])]                # ("444") is shared: this site first
    exact = [r for r in rows if host(url) in (host(r["url"] or ""), host(r["apply_url"] or ""))]
    if len(exact) == 1:                                 # niyamit.bamboohr.com/careers/444, not another company's 444
        return exact[0]["uid"]
    near = [r for r in rows if _same_site(url, r)]
    if len(near) == 1:
        return near[0]["uid"]
    unique = [r for r in rows if len(r["uid"].rsplit(":", 1)[-1]) >= LONG_ID]
    return unique[0]["uid"] if not near and len(unique) == 1 else None


def _job_view(conn, uid: str, how: str) -> dict | None:
    row = conn.execute("SELECT uid, company, title, url, apply_url FROM jobs WHERE uid = ?", (uid,)).fetchone()
    if row is None:
        return None
    return {"uid": row["uid"], "app": None, "company": row["company"], "title": row["title"],
            "url": row["url"] or row["apply_url"], "source": "feed", "how": how}


def _app_view(conn, pid: str, app_id: int, how: str) -> dict | None:
    row = conn.execute("SELECT id, job_uid, company, role_title, job_url FROM applications WHERE id = ? AND "
                       "profile = ?", (app_id, pid)).fetchone()
    if row is None:
        return None
    if row["job_uid"] and not outside_jobs.is_outside(row["job_uid"]):
        found = _job_view(conn, row["job_uid"], how)
        if found:
            return found
    # an outside job keeps its tracker row (its tailored resume is linked to the row, rg_links), plus its GOAL36 record
    return {"uid": row["job_uid"], "app": row["id"], "company": row["company"], "title": row["role_title"],
            "url": row["job_url"], "source": "outside", "how": how}


def _remembered(conn, pid: str, url: str) -> dict | None:
    row = conn.execute("SELECT job_uid, app_id FROM autofill_urls WHERE profile = ? AND url_key = ?",
                       (pid, url_key(url))).fetchone()
    if row is None:
        return None
    if row["job_uid"]:
        return _job_view(conn, row["job_uid"], "picked")
    return _app_view(conn, pid, row["app_id"], "picked")


def _same_link(url: str, link: str | None) -> bool:
    if not link or not str(link).startswith(("http://", "https://")):
        return False
    if url_key(url) == url_key(link):
        return True
    return host(url) == host(link) and bool(set(tokens(url)) & set(tokens(link)))


def _from_resumegen(conn, pid: str, url: str, jobs: list[dict]) -> dict | None:
    links = outside.links(conn, pid)
    for job in sorted(jobs, key=lambda j: j["id"], reverse=True):
        if job.get("status") == "cancelled" or not _same_link(url, job.get("jd_url")):
            continue
        if job.get("source_job_id"):
            return _job_view(conn, job["source_job_id"], "resume")
        if job["id"] in links:
            return _app_view(conn, pid, links[job["id"]], "resume")
    return None


def _from_tracker(conn, pid: str, url: str) -> dict | None:
    for row in conn.execute("SELECT id, job_url, apply_url FROM applications WHERE profile = ? AND "
                            "(job_url IS NOT NULL OR apply_url IS NOT NULL) ORDER BY id DESC LIMIT ?",
                            (pid, MAX_TRACKER_ROWS)):
        if _same_link(url, row["job_url"]) or _same_link(url, row["apply_url"]):
            return _app_view(conn, pid, row["id"], "tracker")
    return None


def listing(conn, pid: str) -> list[dict] | None:
    """resumegen's jobs for the person (new outside jobs adopted first), None when it is offline."""
    try:
        jobs = rgjobs.listing(pid)
    except rgclient.Offline:
        return None
    outside.sync(conn, pid, jobs)
    return jobs


_UNSET = object()


_SUFFIX = re.compile(r"\b(inc|llc|ltd|corp|corporation|co|company|plc|group|holdings|technologies|usa|us)\b\.?", re.I)
_TITLE_NOISE = re.compile(r"^(job application for|apply for|apply to|application for)\s+|\s*[|·].*$|\s+(at|@)\s+.+$|"
                          r"\s+[-–]\s+[^-–]*(careers?|jobs?|hiring|recruiting)[^-–]*$", re.I)
NAME_MIN = 0.75                 # share of title words two titles need in common to be one job
MAX_NAME_ROWS = 400
MAX_FOUND = 20


def company_key(name: str | None) -> str:
    return " ".join(_SUFFIX.sub(" ", norm(name)).split())


def clean_title(title: str | None) -> str:
    """The page's title without the site's words: 'Job Application for ML Engineer at Acme' -> 'ML Engineer'."""
    text = " ".join(str(title or "").split())[:200]
    for _ in range(3):
        text = _TITLE_NOISE.sub("", text).strip()
    return text


def _title_words(title: str) -> set:
    return set(norm(title).split())


def _same_title(a: str, b: str) -> bool:
    x, y = _title_words(a), _title_words(b)
    return bool(x and y) and len(x & y) / max(len(x), len(y)) >= NAME_MIN


def _company_names(conn, company: str) -> list[str]:
    """The spellings of this company in the boards we fetch (job rows carry the board's name)."""
    want = company_key(company)
    if not want:
        return []
    names = {company.strip()}
    for r in conn.execute("SELECT DISTINCT company FROM boards WHERE company IS NOT NULL"):
        if company_key(r["company"]) == want:
            names.add(r["company"])
    return sorted(names)


def by_name(conn, pid: str, company: str | None, title: str | None) -> dict:
    """GOAL36 §5.2: a page whose URL is unknown, matched by the company and job title it shows. {'job': ...} for one
    clear answer (a tracker row first, then an open feed job), else {'candidates': [...]} for the picker."""
    title = clean_title(title)
    want = company_key(company)
    if not want or not title:
        return {"candidates": []}
    rows = [r for r in conn.execute("SELECT id, company, role_title FROM applications WHERE profile = ? "
                                    "ORDER BY id DESC LIMIT ?", (pid, MAX_TRACKER_ROWS))
            if company_key(r["company"]) == want and _same_title(r["role_title"] or "", title)]
    if len(rows) == 1:
        return {"job": _app_view(conn, pid, rows[0]["id"], "name")}
    names = _company_names(conn, company)
    jobs = [dict(r) for r in conn.execute(
        f"SELECT uid FROM jobs WHERE company IN ({','.join('?' * len(names))}) AND closed_at IS NULL "
        f"ORDER BY first_seen DESC LIMIT ?", [*names, MAX_NAME_ROWS])] if names else []
    views = [_job_view(conn, j["uid"], "name") for j in jobs]
    same = [v for v in views if v and _same_title(v["title"] or "", title)]
    exact = [v for v in same if norm(v["title"]) == norm(title)]
    if len(exact) == 1 or (len(same) == 1 and not rows):
        return {"job": (exact or same)[0]}
    picks = [_app_view(conn, pid, r["id"], "name") for r in rows] + same
    return {"candidates": [v for v in picks if v][:MAX_FOUND]}


def search(conn, pid: str, q: str) -> list[dict]:
    """GOAL36 §5.2 "Link to a job": the person's tracker rows and the open feed jobs whose company or title holds
    every word typed, newest first."""
    words = norm(q).split()
    if not words or len(" ".join(words)) < 2:
        return []
    hit = lambda *texts: all(w in norm(" ".join(t or "" for t in texts)) for w in words)    # noqa: E731
    out = [_app_view(conn, pid, r["id"], "search") for r in conn.execute(
        "SELECT id, company, role_title FROM applications WHERE profile = ? ORDER BY id DESC LIMIT ?",
        (pid, MAX_TRACKER_ROWS)) if hit(r["company"], r["role_title"])]
    names = [r["company"] for r in conn.execute("SELECT DISTINCT company FROM boards WHERE company IS NOT NULL")
             if any(w in norm(r["company"]) for w in words)][:50]
    if names:
        for r in conn.execute(f"SELECT uid, company, title FROM jobs WHERE company IN ({','.join('?' * len(names))}) "
                              f"AND closed_at IS NULL ORDER BY first_seen DESC LIMIT ?", [*names, MAX_NAME_ROWS]):
            if hit(r["company"], r["title"]):
                out.append(_job_view(conn, r["uid"], "search"))
    seen, uniq = set(), []
    for v in out:
        key = v and (v["uid"] or f"app:{v['app']}")
        if v and key not in seen:
            seen.add(key)
            uniq.append(v)
    return uniq[:MAX_FOUND]


def find(conn, pid: str, url: str, frames=(), jobs=_UNSET, shows: dict | None = None) -> dict:
    """{'job': {...} | None, 'recent': [...] when none was found, 'resumegen': online?}. `frames`: the addresses
    of application forms embedded in the page (a Greenhouse iframe on a company site); `jobs`: resumegen's list
    when the caller already has it (None: offline); `shows`: the company and title the page shows (GOAL36 §5.2:
    tried after every address way, with 'candidates' when more than one job fits)."""
    pages = [_check(url)] + [str(f) for f in list(frames or [])[:MAX_FRAMES]
                             if str(f).startswith(("http://", "https://")) and len(str(f)) <= MAX_URL]
    for page in pages:
        found = _remembered(conn, pid, page)
        if found:
            return {"job": found, "resumegen": True}
    for page in pages:
        uid = feed_job(conn, page)
        if uid:
            return {"job": _job_view(conn, uid, "url"), "resumegen": True}
    jobs = listing(conn, pid) if jobs is _UNSET else jobs
    for page in pages:
        found = (_from_resumegen(conn, pid, page, jobs) if jobs else None) or _from_tracker(conn, pid, page)
        if found:
            return {"job": found, "resumegen": jobs is not None}
    named = by_name(conn, pid, shows.get("company"), shows.get("title")) if shows else {}
    if named.get("job"):
        return {"job": named["job"], "resumegen": jobs is not None}
    return {"job": None, "recent": recent(conn, pid, jobs), "resumegen": jobs is not None,
            "candidates": named.get("candidates") or []}


def recent(conn, pid: str, jobs: list[dict] | None) -> list[dict]:
    """The person's ready / approved resumes of the last 14 days, newest first (the picker)."""
    if not jobs:
        return []
    cutoff = db.iso(db.utcnow() - timedelta(days=RECENT_DAYS))
    links = outside.links(conn, pid)
    out = []
    for job in jobs:
        made = (job.get("times") or {}).get("made") or job.get("finished_at") or ""
        uid, app = job.get("source_job_id"), links.get(job["id"])
        if job.get("status") != "ready" or made < cutoff or job.get("archived_at") or not (uid or app):
            continue
        out.append({"rg_id": job["id"], "uid": uid, "app": app, "company": job.get("company"),
                    "title": job.get("role"), "made": made, "approved": bool(job.get("drive_file_id"))})
    return sorted(out, key=lambda j: j["made"], reverse=True)[:MAX_RECENT]


def remember(conn, pid: str, url: str, uid: str | None = None, app_id: int | None = None) -> dict:
    """The picker's choice: this page (every step of it) is that job from now on."""
    url = _check(url)
    if bool(uid) == bool(app_id):
        raise ValueError("pick one job")
    found = _job_view(conn, str(uid), "picked") if uid else _app_view(conn, pid, int(app_id), "picked")
    if found is None:
        raise KeyError(uid or app_id)
    with db.tx(conn):
        conn.execute("INSERT OR REPLACE INTO autofill_urls(profile, url_key, job_uid, app_id, picked_at) "
                     "VALUES (?,?,?,?,?)", (pid, url_key(url), found["uid"], found["app"], db.iso(db.utcnow())))
    return {"job": found}
