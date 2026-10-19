"""Auto-fill a manually added job from the ATS's own public data ("Add job manually").

parse_url() turns a posting URL into the uid the fetcher would store for it; fetch_posting() makes one polite request
(net.py: robots gate, rate limits, retries) and maps the answer through the adapters' own normalizers, so a row added
by hand looks exactly like a fetched one.

- Greenhouse / Lever / Workday: the per-job JSON endpoints liveness.py already asks.
- Ashby: no per-job endpoint; the board list is read and the posting picked out by id.
- SmartRecruiters: api.smartrecruiters.com's robots.txt disallows every agent (decision 005), so the public posting
  page jobs.smartrecruiters.com/{Company}/{id} is read instead (its robots.txt answers 404 = no rules, RFC 9309) and
  its schema.org JobPosting microdata parsed. If robots ever disallows it, the ids come back with "blocked": "robots".

Any other URL gives None: the caller then saves what the user typed.
"""
import html
import re
from datetime import date
from html.parser import HTMLParser
from typing import Callable
from urllib.parse import parse_qs, urlsplit

from . import ats as ats_registry
from .ats import ashby, greenhouse, lever, workday
from .ats.common import make_job, parse_iso
from .net import HttpError, get_json, get_text
from .text import html_to_text

# (ats, hint, posting id) -> the board key as stored, or None. hint is the career-site host for company pages that only
# carry ?gh_jid= / ?ashby_jid=, or the key parsed from the URL for Workday (whose site names are case-insensitive).
TokenResolver = Callable[[str, str, str], str | None]

FIELDS = ("uid", "ats", "key", "ext_id", "company", "title", "location", "workplace", "salary_min", "salary_max",
          "salary_ccy", "employment_type", "url", "apply_url", "description", "posted_at")
CLOSED_STATUSES = {404, 410}
ATS_NAMES = {"greenhouse": "Greenhouse", "lever": "Lever", "ashby": "Ashby", "workday": "Workday",
             "smartrecruiters": "SmartRecruiters"}

_GREENHOUSE_HOSTS = {"boards.greenhouse.io", "job-boards.greenhouse.io", "boards.eu.greenhouse.io",
                     "job-boards.eu.greenhouse.io"}
_LEVER_API = {"jobs.lever.co": "https://api.lever.co", "jobs.eu.lever.co": "https://api.eu.lever.co"}
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_DIGITS = re.compile(r"^\d+$")
_SR_ID = re.compile(r"^(\d+)(?:-|$)")
_SR_PAGE = "https://jobs.smartrecruiters.com/{company}/{id}"
_WORKDAY_DUP = re.compile(r"(?<=\d)-\d{1,2}$")     # Workday's '-1' on a repeated slug ('..._PT-JR041494-1')


# ---- URL parsing -----------------------------------------------------------------------------------------------

def _ref(ats: str, key: str, ext_id: str, **extra) -> dict:
    return {"ats": ats, "key": key, "ext_id": ext_id, "uid": f"{ats}:{key}:{ext_id}", **extra}


def _first(query: dict, *names: str) -> str | None:
    for name in names:
        value = (query.get(name) or [""])[0].strip()
        if value:
            return value
    return None


def _greenhouse(host: str, segments: list[str], query: dict, resolve: TokenResolver | None) -> dict | None:
    if host in _GREENHOUSE_HOSTS:
        if segments and segments[0] == "embed":                      # /embed/job_app?for={token}&token={id}
            token, job_id = _first(query, "for"), _first(query, "token", "gh_jid")
        elif len(segments) >= 3 and segments[1] == "jobs":           # /{token}/jobs/{id}
            token, job_id = segments[0], segments[2]
        else:                                                        # /{token}?gh_jid={id}
            token, job_id = (segments[0] if segments else None), _first(query, "gh_jid")
    else:                                                            # company site carrying ?gh_jid={id}
        job_id = _first(query, "gh_jid")
        if not job_id or not _DIGITS.match(job_id):
            return None
        token = (resolve("greenhouse", host, job_id) if resolve else None) or _first(query, "for", "board")
    if not token or not job_id or not _DIGITS.match(job_id):
        return None
    return _ref("greenhouse", token, job_id)


def _lever(host: str, segments: list[str]) -> dict | None:
    if len(segments) < 2 or not _UUID.match(segments[1]):
        return None
    return _ref("lever", segments[0].lower(), segments[1].lower(), api=_LEVER_API[host])


def _ashby(segments: list[str]) -> dict | None:
    if len(segments) < 2 or not _UUID.match(segments[1]):
        return None
    return _ref("ashby", segments[0], segments[1].lower())


def _ashby_embedded(host: str, query: dict, resolve: TokenResolver | None) -> dict | None:
    job_id = _first(query, "ashby_jid")
    if not job_id or not _UUID.match(job_id) or not resolve:
        return None
    org = resolve("ashby", host, job_id.lower())
    return _ref("ashby", org, job_id.lower()) if org else None


def _smartrecruiters(segments: list[str]) -> dict | None:
    found = _SR_ID.match(segments[1]) if len(segments) >= 2 else None
    if not found:
        return None
    return _ref("smartrecruiters", segments[0], found.group(1))


def workday_ext_id(job_path: str) -> str:
    """The fetcher's ext_id for a posting path: the requisition suffix ('..._R12345'). Where the fetcher's pattern
    misses the suffix it uses the list's bulletFields[0] (the requisition id, which only the detail call reveals), so
    the best guess from a URL is the text after the last '_' without Workday's '-1' repeat marker, else the path.
    fetch_posting() returns the exact uid."""
    exact = workday.ext_id({"externalPath": job_path})
    if exact != job_path:
        return exact
    tail = _WORKDAY_DUP.sub("", job_path.rsplit("/", 1)[-1].rsplit("_", 1)[-1])
    return tail or job_path


def _workday(segments: list[str], detected: dict, resolve: TokenResolver | None) -> dict | None:
    if "job" not in segments:
        return None
    after = segments[segments.index("job") + 1:]
    if "apply" in after:
        after = after[:after.index("apply")]
    if not after:
        return None
    job_path = "/job/" + "/".join(after)
    key = f"{detected['token']}/{detected['wd']}/{detected['site']}"
    ext_id = workday_ext_id(job_path)
    stored = resolve("workday", key, ext_id) if resolve else None
    return _ref("workday", stored or key, ext_id, job_path=job_path)


def parse_url(url: str, resolve: TokenResolver | None = None) -> dict | None:
    """Posting URL -> {'ats', 'key', 'ext_id', 'uid'} (plus private routing fields), or None for any other URL.

    `resolve` (see db_resolver) names the board for company career sites that only carry ?gh_jid= / ?ashby_jid= and
    the stored spelling of a Workday key; without it those career-site URLs give None unless they name the board
    themselves (?for= / ?board=), and a Workday key keeps the URL's spelling."""
    text = (url or "").strip()
    if not text:
        return None
    parts = urlsplit(text if "://" in text else f"https://{text}")
    host = (parts.hostname or "").lower()
    segments = [s for s in parts.path.split("/") if s]
    query = parse_qs(parts.query)
    if host in _GREENHOUSE_HOSTS or "gh_jid" in query:
        return _greenhouse(host, segments, query, resolve)
    if host in _LEVER_API:
        return _lever(host, segments)
    if host == "jobs.ashbyhq.com":
        return _ashby(segments)
    if "ashby_jid" in query:
        return _ashby_embedded(host, query, resolve)
    if host in {"jobs.smartrecruiters.com", "careers.smartrecruiters.com"}:
        return _smartrecruiters(segments)
    detected = ats_registry.detect(text)
    if detected and detected["ats"] == "workday":
        return _workday(segments, detected, resolve)
    return None


def db_resolver(conn) -> TokenResolver:
    """A resolver backed by the DB. Workday: the registered board whose key matches ignoring case. Greenhouse/Ashby:
    the board that already holds this posting id (their ids are globally unique), else the one board whose stored
    postings live on the same career-site host."""
    def resolve(ats: str, hint: str, posting_id: str) -> str | None:
        if ats == "workday":
            row = conn.execute("SELECT key FROM boards WHERE ats = 'workday' AND lower(key) = lower(?) LIMIT 1",
                               (hint,)).fetchone()
            return row[0] if row else None
        row = conn.execute("SELECT board FROM jobs WHERE ext_id = ? AND board LIKE ? LIMIT 1",
                           (posting_id, f"{ats}:%")).fetchone()
        if row:
            return row[0].split(":", 1)[1]
        boards = conn.execute("SELECT DISTINCT board FROM jobs WHERE board LIKE ? AND (url LIKE ? OR url LIKE ?) "
                              "LIMIT 2", (f"{ats}:%", f"https://{hint}/%", f"http://{hint}/%")).fetchall()
        return boards[0][0].split(":", 1)[1] if len(boards) == 1 else None
    return resolve


# ---- fetching --------------------------------------------------------------------------------------------------

def _company_from_key(key: str) -> str:
    return key.split("/", 1)[0].replace("-", " ").replace("_", " ").title()


def _greenhouse_job(ref: dict) -> dict:
    data = get_json(greenhouse.DETAIL.format(token=ref["key"], id=ref["ext_id"]))
    company = data.get("company_name") or _company_from_key(ref["key"])
    return greenhouse._normalize({"key": ref["key"], "company": company}, data)


def _lever_job(ref: dict) -> dict:
    data = get_json(f"{ref['api']}/v0/postings/{ref['key']}/{ref['ext_id']}")
    return lever._normalize({"key": ref["key"], "company": _company_from_key(ref["key"])}, data)


def _ashby_job(ref: dict) -> dict:
    payload = get_json(ashby.API.format(token=ref["key"]))
    listed = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(listed, list):
        raise ValueError("Ashby answered in an unexpected format; add the details by hand.")
    job = next((j for j in listed if isinstance(j, dict) and str(j.get("id", "")).lower() == ref["ext_id"]), None)
    if job is None:
        raise ValueError("This posting is closed: it is no longer listed on the company's Ashby board.")
    return ashby._normalize({"key": ref["key"], "company": _company_from_key(ref["key"])}, job)


def _workday_job(ref: dict) -> dict:
    _, api = workday.urls(ref["key"])
    data = get_json(f"{api}{ref['job_path']}")
    info = data.get("jobPostingInfo") if isinstance(data, dict) else None
    if not isinstance(info, dict) or not info.get("title"):
        raise ValueError("This posting is closed: Workday no longer serves it.")
    company = (data.get("hiringOrganization") or {}).get("name") or _company_from_key(ref["key"])
    posting = {"title": info["title"], "externalPath": ref["job_path"], "postedOn": info.get("postedOn"),
               "bulletFields": [info["jobReqId"]] if info.get("jobReqId") else []}
    return workday._normalize({"key": ref["key"], "company": company}, posting, info, date.today())


class _JobPostingMicrodata(HTMLParser):
    """First value of each schema.org itemprop on a page: a <meta content> or the element's own markup as text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.is_job_posting = False
        self.props: dict[str, str] = {}
        self.workplace: str | None = None
        self._open: list[list] = []            # [prop, tag, depth, markup parts] per itemprop element being read

    def handle_starttag(self, tag, attrs):
        attr = dict(attrs)
        if "schema.org/jobposting" in (attr.get("itemtype") or "").lower():
            self.is_job_posting = True
        if tag == "spl-job-location" and attr.get("workplacetype"):
            self.workplace = attr["workplacetype"]
        for capture in self._open:
            capture[2] += capture[1] == tag
            capture[3].append(self.get_starttag_text() or "")
        prop = attr.get("itemprop")
        if not prop or "itemscope" in attr:
            return
        if tag == "meta":
            self.props.setdefault(prop, (attr.get("content") or "").strip())
        else:
            self._open.append([prop, tag, 1, []])

    def handle_endtag(self, tag):
        for capture in list(self._open):
            if capture[1] == tag:
                capture[2] -= 1
            if capture[2] == 0:
                self._open.remove(capture)
                self.props.setdefault(capture[0], html_to_text("".join(capture[3])))
            else:
                capture[3].append(f"</{tag}>")

    def handle_data(self, data):
        for capture in self._open:
            capture[3].append(html.escape(data))


def _smartrecruiters_job(ref: dict) -> dict:
    page_url = _SR_PAGE.format(company=ref["key"], id=ref["ext_id"])
    try:
        page = get_text(page_url)
    except HttpError as err:
        if err.robots:
            return {**_public(ref), "url": page_url, "blocked": "robots"}
        raise
    parser = _JobPostingMicrodata()
    parser.feed(page)
    parser.close()
    props = parser.props
    if not parser.is_job_posting or not props.get("title"):
        raise ValueError("This posting is closed: its SmartRecruiters page no longer shows the job.")
    place = [props.get("addressLocality"), props.get("addressRegion"), props.get("addressCountry")]
    return make_job(
        ats="smartrecruiters", key=ref["key"], ext_id=ref["ext_id"],
        company=props.get("name") or _company_from_key(ref["key"]), title=props["title"],
        location=", ".join(p for p in place if p), country=props.get("addressCountry") or None,
        workplace=parser.workplace, url=page_url, apply_url=page_url, posted_at=parse_iso(props.get("datePosted")),
        precision="exact", description=props.get("description", ""), employment=props.get("employmentType"),
    )


_FETCHERS = {"greenhouse": _greenhouse_job, "lever": _lever_job, "ashby": _ashby_job, "workday": _workday_job,
             "smartrecruiters": _smartrecruiters_job}


def _public(job: dict) -> dict:
    return {k: job[k] for k in ("uid", "ats", "key", "ext_id") if k in job}


def _http_message(name: str, err: HttpError) -> str:
    if err.status in CLOSED_STATUSES:
        return f"This posting is closed: {name} answers {err.status} for it."
    if err.robots:
        return f"{name}'s robots.txt does not allow reading this posting; add the details by hand."
    reason = f"HTTP {err.status}" if err.status else "network error"
    return f"Could not reach {name} right now ({reason}); add the details by hand."


def fetch_posting(url: str, resolve: TokenResolver | None = None) -> dict | None:
    """Posting URL -> the FIELDS of the normalized job (description as plain text), or None for a non-ATS URL.

    Raises ValueError with a short human message when the posting is closed or the ATS cannot be reached. A
    SmartRecruiters page that robots.txt disallows gives the ids plus 'blocked': 'robots' and no other fields."""
    ref = parse_url(url, resolve)
    if ref is None:
        return None
    name = ATS_NAMES[ref["ats"]]
    try:
        job = _FETCHERS[ref["ats"]](ref)
    except HttpError as err:
        raise ValueError(_http_message(name, err)) from err
    except (KeyError, TypeError, AttributeError) as err:
        raise ValueError(f"{name} answered in an unexpected format; add the details by hand.") from err
    if job.get("blocked"):
        return job
    return {k: job.get(k) for k in FIELDS}
