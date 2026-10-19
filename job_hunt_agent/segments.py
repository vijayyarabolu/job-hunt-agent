"""Company segment for B3's big-vs-startup share (decision 005 skeptic objection 6). The written rule, first match wins:

1. public_sector - the name or website says government or public education: a .gov/.mil/.edu (or .gov.xx/.ac.xx)
   domain, or words like university, college, school district, public schools, county of / '... County', city of,
   state of, department of, commonwealth of, transit authority, government of, ministry of, municipal, township,
   national laboratory ('Federal' alone is not enough: Accenture Federal Services is a contractor).
2. big - an S&P 500 constituent; a YC company at stage 'Public' or with team_size >= 1000; or >= 300 open postings
   on its board (Workday: >= 1000 total, its count is capped at 2000).
3. startup - a YC company (any stage below the 'big' bar), or a board on a startup ATS (Ashby, Greenhouse, Lever)
   with < 150 open postings that matched nothing above and is not a staffing/health/finance-services
   'other' name.
4. other - staffing and recruiting agencies, hospitals and health systems, credit unions, churches, law firms, and
   Workday tenants below the big bar (Workday is an enterprise ATS: a mid-size employer, rarely a startup).
5. unknown - everything left (e.g. a 150-299 posting Greenhouse board not on any list); reported as its own bucket.

The rule is deterministic and uses only data we hold (lists in config/lists/, board size, ATS, name, domain).
"""
import re

from . import seedlists

SEGMENTS = ("big", "startup", "public_sector", "other", "unknown")
BIG_POSTINGS = 300
BIG_WORKDAY_TOTAL = 1000
BIG_TEAM = 1000
STARTUP_MAX_POSTINGS = 150
STARTUP_ATS = {"ashby", "greenhouse", "lever"}
_PUBLIC_DOMAIN = re.compile(r"\.(gov|mil|edu)(\.[a-z]{2})?$|\.(gov|ac)\.[a-z]{2}$|\.k12\.[a-z]{2}\.us$")
_PUBLIC_NAME = re.compile(r"(?i)\b(university|universidad|college|school district|public schools|county of|"
                          r"county government|city of|state of|department of|commonwealth of|transit authority|"
                          r"national laborator(y|ies)|government of|ministry of|municipal|township)\b"
                          r"|\bcounty$")     # not 'Accenture Federal Services' (a contractor)
_OTHER_NAME = re.compile(r"(?i)\b(staffing|recruit(ing|ment|ers)|talent solutions|hospital|health system|"
                         r"medical center|healthcare system|clinic|credit union|church|ministries|law firm|"
                         r"attorneys|llp)\b")


def segment(name: str | None, website: str | None, ats: str, open_postings: int | None) -> str:
    """One of SEGMENTS, by the rule in this module's docstring."""
    site = seedlists.domain(website) or ""
    if _PUBLIC_DOMAIN.search(site) or _PUBLIC_NAME.search(name or ""):
        return "public_sector"
    yc = seedlists.yc_row(name, website)
    size = open_postings or 0
    team = int(yc["team_size"]) if yc and str(yc.get("team_size") or "").isdigit() else 0
    if (seedlists.in_sp500(name) or (yc and (yc.get("stage") == "Public" or team >= BIG_TEAM))
            or (ats == "workday" and size >= BIG_WORKDAY_TOTAL) or (ats != "workday" and size >= BIG_POSTINGS)):
        return "big"
    if yc:
        return "startup"
    if _OTHER_NAME.search(name or "") or ats == "workday":
        return "other"
    if ats in STARTUP_ATS and size < STARTUP_MAX_POSTINGS:
        return "startup"
    return "unknown"
