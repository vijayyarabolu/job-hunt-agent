"""ATS adapter registry and careers-URL detection."""
import re
from urllib.parse import parse_qs, urlsplit

from . import (adp, amazon, ashby, bamboohr, breezy, careeronestop, cornerstone, dayforce, eightfold, gem, greenhouse, hibob, ibm,
               icims, icims_classic, jazzhr, jobscore, jobvite, lever, oracle_recruiting, paylocity, phenom, pinpoint,
               recruitee, rippling, smartrecruiters, successfactors, taleo, workable, workday)
from .common import Ctx, FetchResult

ADAPTERS = {
    "greenhouse": greenhouse.fetch,
    "lever": lever.fetch,
    "ashby": ashby.fetch,
    "smartrecruiters": smartrecruiters.fetch,
    "workday": workday.fetch,
    "amazon": amazon.fetch,          # GOAL2 B (decision 005)
    "eightfold": eightfold.fetch,    # GOAL5 §4 (decision 011)
    "phenom": phenom.fetch,
    "icims": icims.fetch,            # iCIMS career sites on the Jibe front end only
    "workable": workable.fetch,
    "oracle_recruiting": oracle_recruiting.fetch,   # the enterprise systems
    "taleo": taleo.fetch,
    "successfactors": successfactors.fetch,
    "icims_classic": icims_classic.fetch,
    "jobvite": jobvite.fetch,
    "bamboohr": bamboohr.fetch,      # GOAL16 Tier A: SMB / startup systems with one-request public lists
    "rippling": rippling.fetch,
    "gem": gem.fetch,
    "recruitee": recruitee.fetch,
    "breezy": breezy.fetch,
    "pinpoint": pinpoint.fetch,
    "hibob": hibob.fetch,
    "jobscore": jobscore.fetch,
    "adp": adp.fetch,                # GOAL16 Tier B: payroll-suite / mid-market systems
    "paylocity": paylocity.fetch,
    "dayforce": dayforce.fetch,
    "cornerstone": cornerstone.fetch,
    "jazzhr": jazzhr.fetch,
    "ibm": ibm.fetch,                # GOAL16 Tier C: a big employer's own career search
    "careeronestop": careeronestop.fetch,   # GOAL35 §4.2: state job banks (off until a key is approved)
}
KEY_FIELDS = {"workday": ("wd", "site"), "eightfold": ("domain", "api"), "phenom": ("ref", "page", "site"),
              "oracle_recruiting": ("site",), "taleo": ("section",),
              "adp": ("ccid",), "dayforce": ("site",), "cornerstone": ("site",)}             # GOAL16

_WORKDAY_HOST = re.compile(r"^(?P<tenant>[\w-]+)\.(?P<wd>wd\d+)\.myworkdayjobs\.com$")
_WORKDAY_SITE_HOST = re.compile(r"^(?P<wd>wd\d+)\.myworkdaysite\.com$")     # wd5.myworkdaysite.com/recruiting/t/s
_LOCALE = re.compile(r"^[a-z]{2}-[a-z]{2}$", re.I)      # en-US / en-us (a bare 'us' may be a real site name)
_RESERVED = {"api", "embed"}
_ORACLE_SITE = re.compile(r"/hcmUI/CandidateExperience/[^/]+/sites/([A-Za-z0-9_]+)", re.I)
_ICIMS_PORTAL = re.compile(r"^[a-z0-9-]+\.icims\.com$")
_ICIMS_VENDOR = {"www.icims.com", "careers.icims.com", "developer-community.icims.com", "community.icims.com"}
_TALEO_SECTION = re.compile(r"^/careersection/([A-Za-z0-9_.-]+)/")
_JOBVITE_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$", re.I)
_SLUG16 = re.compile(r"^[a-z0-9][a-z0-9_-]{0,99}$", re.I)
_BAMBOO_HOST = re.compile(r"^([a-z0-9][a-z0-9-]{0,62})\.bamboohr\.com$")
_BAMBOO_VENDOR = {"www", "api", "app", "marketplace", "partners", "help", "status", "go", "learn"}
_GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_ADP_CCID = re.compile(r"^[0-9A-Za-z_]{1,40}$")
_DAYFORCE_PART = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_DAYFORCE_RESERVED = {"api", "_next", "static", "favicon.ico", "robots.txt", "sitemap.xml"}
_CSOD_HOST = re.compile(r"^([a-z0-9][a-z0-9-]{0,62})\.csod\.com$")
_CSOD_VENDOR = {"www", "api", "us", "eu", "help", "status", "support"}
_JAZZ_HOST = re.compile(r"^([a-z0-9][a-z0-9-]{0,62})\.applytojob\.com$")
_JAZZ_VENDOR = {"www", "app", "api", "help"}
_FEED_HOSTS = {"recruitee": re.compile(r"^([a-z0-9][a-z0-9-]{0,62})\.recruitee\.com$"),      # GOAL16 one-request feeds
               "breezy": re.compile(r"^([a-z0-9][a-z0-9-]{0,62})\.breezy\.hr$"),
               "pinpoint": re.compile(r"^([a-z0-9][a-z0-9-]{0,62})\.pinpointhq\.com$"),
               "hibob": re.compile(r"^([a-z0-9][a-z0-9-]{0,62})\.careers\.hibob\.com$")}
_FEED_VENDOR = {"www", "app", "api", "help", "support", "status", "blog", "careers", "developers", "docs", "marketing"}
_JOBSCORE_CODE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def board_key(entry: dict) -> str:
    """companies.yaml entry -> board key: {token}, or token plus KEY_FIELDS joined by '/' (Workday {tenant}/{wdN}/{site},
    Eightfold {host}/{domain}/{api}, Phenom {host}/{refNum}/{pageId}/{site})."""
    return "/".join([entry["token"], *(str(entry[f]) for f in KEY_FIELDS.get(entry["ats"], ()))])


def detect(url: str) -> dict | None:
    """Careers URL -> {'ats', 'token', 'wd'?, 'site'?}. Only allowlisted ATS hosts are accepted."""
    parts = urlsplit(url if "://" in url else f"https://{url}")
    host = (parts.hostname or "").lower()
    segments = [s for s in parts.path.split("/") if s]
    if host in {"boards.greenhouse.io", "job-boards.greenhouse.io"}:
        token = parse_qs(parts.query).get("for", [None])[0] or (segments[0] if segments else None)
        if token == "embed":
            token = None
        return {"ats": "greenhouse", "token": token} if token else None
    if host == "jobs.lever.co" and segments and segments[0].lower() not in _RESERVED:
        return {"ats": "lever", "token": segments[0].lower()}
    if host == "jobs.ashbyhq.com" and segments and segments[0].lower() not in _RESERVED:
        return {"ats": "ashby", "token": segments[0]}
    if host in {"jobs.smartrecruiters.com", "careers.smartrecruiters.com"} and segments:
        return {"ats": "smartrecruiters", "token": segments[0]}
    if host == "apply.workable.com" and segments and segments[0].lower() not in _RESERVED | {"j"}:
        return {"ats": "workable", "token": segments[0].lower()}
    enterprise = (_detect_enterprise(host, parts.path, segments) or _detect_goal16(host, segments)
                  or _detect_payroll(host, segments, parse_qs(parts.query)))
    if enterprise:
        return enterprise
    rest = [s for s in segments if not _LOCALE.match(s)]
    found = _WORKDAY_HOST.match(host)
    if found and rest:
        return {"ats": "workday", "token": found["tenant"], "wd": found["wd"], "site": rest[0]}
    found = _WORKDAY_SITE_HOST.match(host)
    if found and len(rest) >= 3 and rest[0] == "recruiting":
        return {"ats": "workday", "token": rest[1], "wd": found["wd"], "site": rest[2]}
    return None


def _detect_enterprise(host: str, path: str, segments: list[str]) -> dict | None:
    """GOAL10 systems: Oracle Recruiting Cloud, Taleo, classic iCIMS, Jobvite (SuccessFactors sites use company
    domains, so they cannot be told from the URL)."""
    oracle = _ORACLE_SITE.search(path) if host.endswith(".oraclecloud.com") else None
    if oracle:
        return {"ats": "oracle_recruiting", "token": host, "site": oracle.group(1)}
    taleo = _TALEO_SECTION.match(path) if host.endswith(".taleo.net") and ".tbe." not in host else None
    if taleo and taleo.group(1) not in {"rest", "iam", "theme"}:
        return {"ats": "taleo", "token": host, "section": taleo.group(1)}
    if _ICIMS_PORTAL.match(host) and host not in _ICIMS_VENDOR:
        return {"ats": "icims_classic", "token": host}
    if host == "jobs.jobvite.com":
        rest = segments[1:] if segments and segments[0].lower() == "careers" else segments
        if rest and _JOBVITE_SLUG.match(rest[0]) and rest[0].lower() not in _RESERVED | {"careers"}:
            return {"ats": "jobvite", "token": rest[0].lower()}
    return None


def _detect_goal16(host: str, segments: list[str]) -> dict | None:
    """GOAL16 systems: BambooHR (`<co>.bamboohr.com/careers`), Rippling (`ats.rippling.com/<slug>`), Gem
    (`jobs.gem.com/<vanity>`)."""
    bamboo = _BAMBOO_HOST.match(host)
    if bamboo and bamboo.group(1) not in _BAMBOO_VENDOR and segments[:1] in (["careers"], ["jobs"], []):
        return {"ats": "bamboohr", "token": bamboo.group(1)}
    if host == "ats.rippling.com" and segments and _SLUG16.match(segments[0]) and segments[0] not in _RESERVED:
        return {"ats": "rippling", "token": segments[0]}
    if host == "jobs.gem.com" and segments and _SLUG16.match(segments[0]) and segments[0] not in _RESERVED:
        return {"ats": "gem", "token": segments[0]}
    jazz = _JAZZ_HOST.match(host)
    if jazz and jazz.group(1) not in _JAZZ_VENDOR and segments[:1] in (["apply"], []):
        return {"ats": "jazzhr", "token": jazz.group(1)}
    return _detect_feed(host, segments)


def _detect_feed(host: str, segments: list[str]) -> dict | None:
    """Recruitee / Breezy / Pinpoint / HiBob (`<co>.<vendor host>`) and JobScore (`careers.jobscore.com/careers/<co>`)."""
    for ats, pattern in _FEED_HOSTS.items():
        found = pattern.match(host)
        if found:
            return {"ats": ats, "token": found.group(1)} if found.group(1) not in _FEED_VENDOR else None
    if host == "careers.jobscore.com" and len(segments) >= 2 and segments[0] in ("careers", "jobs") \
            and _JOBSCORE_CODE.match(segments[1]):
        return {"ats": "jobscore", "token": segments[1]}
    return None


def _detect_payroll(host: str, segments: list[str], query: dict) -> dict | None:
    """GOAL16 Tier B: ADP Workforce Now (recruitment.html?cid=&ccId=), Paylocity (/Recruiting/Jobs/All/<guid>),
    Dayforce (jobs.dayforcehcm.com/<culture>/<tenant>/<site>), Cornerstone (<t>.csod.com/ux/ats/careersite/<id>)."""
    if host == "workforcenow.adp.com" and segments[-1:] == ["recruitment.html"]:
        cid, ccid = (query.get("cid") or [""])[0], (query.get("ccId") or query.get("ccid") or [""])[0]
        return {"ats": "adp", "token": cid.lower(), "ccid": ccid} if _GUID.match(cid) and _ADP_CCID.match(ccid) else None
    if host == "recruiting.paylocity.com" and len(segments) >= 4 and [s.lower() for s in segments[:3]] == [
            "recruiting", "jobs", "all"] and _GUID.match(segments[3]):
        return {"ats": "paylocity", "token": segments[3].lower()}
    if host == "jobs.dayforcehcm.com":
        rest = segments[1:] if segments and _LOCALE.match(segments[0]) else segments
        if len(rest) >= 2 and all(_DAYFORCE_PART.match(s) for s in rest[:2]) and rest[0] not in _DAYFORCE_RESERVED:
            return {"ats": "dayforce", "token": rest[0].lower(), "site": rest[1]}
        return None
    csod = _CSOD_HOST.match(host)
    if csod and csod.group(1) not in _CSOD_VENDOR and segments[:3] == ["ux", "ats", "careersite"] \
            and len(segments) >= 4 and segments[3].isdigit():
        return {"ats": "cornerstone", "token": csod.group(1), "site": segments[3]}
    return None


__all__ = ["ADAPTERS", "KEY_FIELDS", "Ctx", "FetchResult", "board_key", "detect"]
