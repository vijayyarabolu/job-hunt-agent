"""GOAL35 §4.2: state job banks through CareerOneStop's Jobs API (the U.S. Department of Labor's site; its jobs are the
National Labor Exchange, which every state job bank feeds). One board ('careeronestop:us'), added only when
jobbanks.enabled() (the Settings switch and an approved key). Like Amazon it is a search: one request per search
term, US-wide, posted in the last DAYS days, newest first; absence is never a miss.

The response shape is the published v1 one (Jobs[{JvId, JobTitle, Company, AccquisitionDate, URL, Location}]); v2
keys are granted only after an NLx data request is approved, so it is unverified against a live answer: any other
shape is SchemaDrift (the board shows "schema changed", never "no jobs").

A posting whose company and title are already open on one of our boards is left out (the company's own link wins);
every card says where it came from ("via KansasWorks"). The last run's per-state counts go to data/jobbanks_last.json
for `job-hunt-agent jobbanks-report`. GOAL36 §2: the searches are the fetch's title terms plus the masters' skills
(RESUME_TERMS), and the employers named here get their own boards looked up (employer_boards)."""
import json
import re
from collections import Counter
from datetime import datetime
from urllib.parse import quote

from ..net import HttpError, get_json
from ..text import norm
from .common import Ctx, FetchResult, SchemaDrift, day_iso, fast_item, make_job, raw_fp, require_list, schema_failure

ATS = "careeronestop"
SEARCH = ("https://api.careeronestop.org/v1/jobsearch/{user}/{q}/US/0/accquisitiondate/0/0/{n}/{days}"
          "?showFilters=false")
RESULTS, DAYS = 100, 7
# GOAL36 §2: the state bank is also asked by the skills on the masters, not only by titles (one request each)
RESUME_TERMS = ("LLM", "RAG", "generative AI", "GenAI", "LangChain", "PyTorch", "machine learning", "NLP",
                "computer vision", "MLOps", "Amazon Bedrock", "vector search")
FP_SALT = "cos-1"
_STATE = re.compile(r",\s*([A-Z]{2})\b")
# the state's own job bank (§4.1 research table); any other state is "via state job bank (XX)"
BANKS = {"AL": "AlabamaWorks", "AK": "ALEXsys", "AZ": "Arizona@Work", "AR": "Arkansas JobLink", "CA": "CalJOBS",
         "CO": "Connecting Colorado", "CT": "CTHires", "DE": "Delaware JobLink", "FL": "Employ Florida",
         "GA": "EmployGeorgia", "HI": "HireNet Hawaii", "ID": "IdahoWorks", "IL": "IllinoisJobLink",
         "IN": "Indiana Career Connect", "IA": "IowaWORKS", "KS": "KansasWorks", "KY": "Kentucky Career Center",
         "LA": "Louisiana HiRE", "ME": "Maine JobLink", "MD": "Maryland Workforce Exchange", "MA": "JobQuest",
         "MI": "Pure Michigan Talent Connect", "MN": "CareerForce", "MS": "MS Works", "MO": "MoJobs",
         "MT": "MontanaWorks", "NE": "NEworks", "NV": "EmployNV", "NH": "NH Works", "NJ": "NJ Career Connections",
         "NM": "NM Workforce Connection", "NY": "NY Job Bank", "NC": "NCWorks", "ND": "Job Service North Dakota",
         "OH": "OhioMeansJobs", "OK": "OKJobMatch", "OR": "iMatchSkills", "PA": "PA CareerLink", "RI": "EmployRI",
         "SC": "SC Works", "SD": "SDWORKS", "TN": "Jobs4TN", "TX": "WorkInTexas", "UT": "Utah jobs",
         "VT": "Vermont JobLink", "VA": "Virginia Workforce Connection", "WA": "WorkSourceWA", "WV": "WorkForce WV",
         "WI": "Job Center of Wisconsin", "WY": "Wyoming at Work", "DC": "DC Networks"}


def state_of(location: str | None) -> str | None:
    found = _STATE.findall(location or "")
    return found[-1] if found else None


def via(location: str | None) -> str:
    st = state_of(location)
    return f"via {BANKS[st]}" if st in BANKS else f"via state job bank{f' ({st})' if st else ''}"


def same_key(company: str, title: str) -> tuple[str, str]:
    """The dedupe key against our own boards: company and title, normalized (locations are written too differently
    across systems to compare)."""
    return norm(company), norm(title)


def terms(search_terms) -> list:
    """The fetch's title terms, then RESUME_TERMS, each once (case ignored)."""
    out: dict[str, str] = {}
    for term in (*search_terms, *RESUME_TERMS):
        out.setdefault(term.lower(), term)
    return list(out.values())


def report_path():
    from .. import config          # config imports the adapter registry
    return config.DATA / "jobbanks_last.json"


def _posted(text: str | None) -> str | None:
    try:
        return day_iso(datetime.strptime((text or "").strip()[:10], "%m/%d/%Y").date())
    except ValueError:
        return None


def _normalize(board: dict, job: dict, scopes: set[str]) -> dict:
    item = make_job(ats=ATS, key=board["key"], ext_id=job["JvId"], company=(job.get("Company") or "").strip(),
                    title=job["JobTitle"], location=job.get("Location") or "", country="US", url=job.get("URL") or "",
                    posted_at=_posted(job.get("AccquisitionDate")), precision="day",
                    description=job.get("Description") or "")
    return {**item, "scopes": sorted(scopes)}


def _search(term: str, user: str, token: str) -> list:
    payload = get_json(SEARCH.format(user=quote(user), q=quote(term), n=RESULTS, days=DAYS),
                       headers={"Authorization": f"Bearer {token}"})
    jobs = require_list(payload, "Jobs")
    if any(not isinstance(j, dict) or "JvId" not in j or "JobTitle" not in j for j in jobs):
        raise SchemaDrift("a Jobs entry has no JvId / JobTitle")
    return jobs


def _write_report(today, counts: dict) -> None:
    path = report_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"at": day_iso(today), "states": counts}, indent=1) + "\n")


def fetch(board: dict, ctx: Ctx) -> FetchResult:
    from .. import jobbanks
    found = jobbanks.key()
    if found is None:
        return FetchResult([], None, error="no CareerOneStop key (ask CareerOneStop for one through the NLx Research Hub)")
    token, user = found
    seen: dict[str, dict] = {}
    found_by: dict[str, set[str]] = {}
    try:
        for term in terms(ctx.search_terms):
            for job in _search(term, user, token):
                key = str(job["JvId"])
                seen.setdefault(key, job)
                found_by.setdefault(key, set()).add(f"term:{term}")
    except HttpError as err:
        return FetchResult([], err.status, error=str(err))
    except SchemaDrift as err:
        return schema_failure(err)
    counts: dict[str, Counter] = {}
    items, bad = [], 0
    for key, job in seen.items():
        tally = counts.setdefault(state_of(job.get("Location")) or "?", Counter())
        tally["found"] += 1
        if ctx.known_elsewhere(job.get("Company") or "", job.get("JobTitle") or ""):
            tally["same_as_board"] += 1
            continue
        try:
            fp = raw_fp(job, FP_SALT)
            uid = f"{ATS}:{board['key']}:{key}"
            items.append(fast_item(ATS, board["key"], key, job.get("JobTitle", ""), fp)
                         if ctx.unchanged(uid, fp) else {**_normalize(board, job, found_by[key]), "raw_fp": fp})
        except (KeyError, TypeError, ValueError, AttributeError):
            bad += 1
    _write_report(ctx.today, {st: dict(c) for st, c in sorted(counts.items())})
    return FetchResult(items, 200, n_raw=len(seen), n_parse_err=bad)
