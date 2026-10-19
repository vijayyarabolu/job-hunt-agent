"""Which application an email is about.

Company first: each of the person's submitted applications gives its company key (the tracker's name and the feed
job's board name, applied.company_key, so GOAL11's renames apply) and the email is searched for it in the sender
(display name, address, domain: 3 points), the subject (2) and the body (1). Subject and body hits must start with a
capital (or digit) where they stand, so "ramp up" is not Ramp. A body that names three or more applied-to companies
and does not come from an ATS is a digest (job alerts, newsletters): its body hits are dropped. The best-scoring
company wins; a tie is ambiguous.

Then the role: one open application at that company is the match. Several: a req id that appears in the email picks
one; else the title whose words best appear in it (at least 75% of them, and strictly ahead of the next). Still
unsure -> ambiguous, and the email waits in "Needs you" with every candidate.
"""
import re
from dataclasses import dataclass
from email.utils import parseaddr

from ..applied import APPLIED_STATES, company_key, normalize_title
from ..text import norm

ATS_DOMAINS = ("greenhouse.io", "greenhouse-mail.io", "lever.co", "myworkday.com", "myworkdayjobs.com",
               "workday.com", "ashbyhq.com", "icims.com", "smartrecruiters.com", "jobvite.com", "taleo.net",
               "successfactors.com", "successfactors.eu", "sapsf.com", "oraclecloud.com", "taleo.com", "workable.com",
               "workablemail.com", "breezy.hr", "applytojob.com", "jazzhr.com", "recruitee.com", "bamboohr.com",
               "paylocity.com", "ultipro.com", "ukg.com", "dayforcehcm.com", "rippling.com", "gem.com",
               "eightfold.ai", "phenompeople.com", "avature.net", "teamtailor.com", "pinpointhq.com", "dover.com",
               "wellfound.com", "hirevue.com", "codesignal.com", "hackerrank.com", "karat.io", "goodtime.io",
               "modernloop.io", "calendly.com")
SENDER, SUBJECT, BODY = 3, 2, 1
DIGEST_COMPANIES = 3
TITLE_SHARE = 0.75
MIN_KEY = 3                         # a company key shorter than this (compact) is never searched for
OPEN_FIRST = ("applied", "screen", "interview", "offer", "ghosted")
_NO_NAME = re.compile(r"(?i)^(?:no-?reply|do-?not-?reply|notifications?|notify|careers?|jobs?|talent|recruiting|"
                      r"recruitment|hr|hiring|mail|info|alerts?|system|workday|support)[\w.+-]*$")   # no company
_TENANT = re.compile(r"^[a-z]{3,}$")
_GENERIC_SENDER = re.compile(r"(?i)\b(?:no-?reply|do-?not-?reply|notifications?|careers?|jobs?|talent|recruiting|"
                             r"recruitment|recruiter|hiring|team|hr|people|acquisition|via|the|at|workday|greenhouse|"
                             r"lever|ashby|icims|from)\b")


@dataclass(frozen=True)
class App:
    id: int
    company: str
    role_title: str
    status: str
    applied_on: str | None
    req_id: str | None
    keys: tuple[str, ...]


@dataclass(frozen=True)
class Match:
    key: str | None                 # the company key found (None: no company)
    company: str | None             # its name as the tracker writes it, or a guess from the sender
    apps: tuple[App, ...]           # every candidate application at that company
    app: App | None                 # the one it is about, when sure
    score: int
    from_ats: bool

    @property
    def unique(self) -> bool:
        return self.app is not None


def board_alias(board: str | None) -> str | None:
    """The job board's tenant, the name the company's ATS mail often signs with: "workday:aep/wd1/AEPCareerSite" is
    "aep" while the tracker says "Ohio Power Company" (GOAL11 §3 names); letters only, so "talkdesk2" or an Oracle
    host is no name."""
    tenant = (board or "").partition(":")[2].split("/", 1)[0].lower()
    return tenant if _TENANT.match(tenant) else None


def candidates(conn, pid: str) -> list[App]:
    marks = ",".join("?" * len(APPLIED_STATES))
    rows = conn.execute(
        f"SELECT a.id, a.company, a.role_title, a.status, a.applied_on, j.req_id, j.company AS board, "
        f"j.board AS board_id FROM applications a LEFT JOIN jobs j ON j.uid = a.job_uid "
        f"WHERE a.profile = ? AND a.status IN ({marks})", (pid, *APPLIED_STATES)).fetchall()
    out = []
    for r in rows:
        names = (company_key(r["company"]), company_key(r["board"]), board_alias(r["board_id"]) or "")
        keys = tuple(dict.fromkeys(k for k in names if len(k.replace(" ", "")) >= MIN_KEY))
        if keys:
            out.append(App(r["id"], r["company"] or r["board"] or "", r["role_title"] or "", r["status"],
                           r["applied_on"], r["req_id"], keys))
    return out


def sender_parts(sender: str) -> tuple[str, str, str]:
    """(display name, local part, domain) of a From header."""
    display, address = parseaddr(sender or "")
    local, _, domain = address.lower().rpartition("@")
    return display, local, domain


def is_ats(domain: str) -> bool:
    return any(domain == d or domain.endswith("." + d) for d in ATS_DOMAINS)


def _flat(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _key_rx(key: str) -> re.Pattern:
    words = [re.escape(w) for w in key.split()]
    return re.compile(r"(?<![A-Za-z0-9])" + r"[\s\-.&,']*".join(words) + r"(?![A-Za-z0-9])", re.I)


def _capital_hit(rx: re.Pattern, text: str) -> bool:
    return any(m.group(0)[:1].isupper() or m.group(0)[:1].isdigit() for m in rx.finditer(text or ""))


def _own_domain(domain: str) -> str:
    """The part of the sender's domain that can name the company: none of an ATS's own name ("AEP@myworkday.com" is
    AEP, never the company Workday), only a subdomain in front of it ("acme.wd5.myworkday.com")."""
    ats = next((d for d in ATS_DOMAINS if domain == d or domain.endswith("." + d)), None)
    return domain[:-len(ats)].strip(".") if ats else domain.rsplit(".", 1)[0]


def _scores(sender: str, subject: str, text: str, keys: set[str]) -> dict[str, int]:
    display, local, domain = sender_parts(sender)
    flat_sender = f"{_flat(display)} {_flat(local)} {_flat(_own_domain(domain))}"
    display_norm = norm(display)
    out = {}
    for key in keys:
        rx, flat, score = _key_rx(key), key.replace(" ", ""), 0
        if re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", display_norm) or (
                len(flat) >= 4 and flat in flat_sender):
            score += SENDER
        if _capital_hit(rx, subject):
            score += SUBJECT
        if _capital_hit(rx, text):
            score += BODY
        if score:
            out[key] = score
    return out


def _share(app: App, words: set[str]) -> float:
    title = normalize_title(app.role_title).split()
    return sum(1 for w in title if w in words) / len(title) if title else 0.0


def _req_hit(app: App, whole: str) -> bool:
    req = app.req_id or ""
    return (len(req) >= 4 and any(c.isdigit() for c in req)
            and re.search(rf"(?<![A-Za-z0-9]){re.escape(req)}(?![A-Za-z0-9])", whole) is not None)


def _by_req(apps: list[App], subject: str, text: str) -> App | None:
    """No company name found, but the email quotes one application's requisition number ("R19850")."""
    hit = [a for a in apps if _req_hit(a, f"{subject}\n{text}")]
    return hit[0] if len(hit) == 1 else None


def _pick_role(apps: list[App], subject: str, text: str) -> App | None:
    if len(apps) == 1:
        return apps[0]
    whole = f"{subject}\n{text}"
    with_req = [a for a in apps if _req_hit(a, whole)]
    if len(with_req) == 1:
        return with_req[0]
    words = set(normalize_title(whole).split())
    ranked = sorted(((_share(a, words), a) for a in apps), key=lambda pair: -pair[0])
    best, second = ranked[0][0], ranked[1][0]
    return ranked[0][1] if best >= TITLE_SHARE and best > second else None


def guess_company(sender: str) -> str | None:
    """A company name from the sender when no application matched: the display name minus ATS / team words, else
    the domain's name."""
    display, _, domain = sender_parts(sender)
    left = " ".join(_GENERIC_SENDER.sub(" ", display).split()).strip(" -|,@")
    if left:
        return left
    local = parseaddr(sender or "")[1].rpartition("@")[0]
    if is_ats(domain) and local and not _NO_NAME.match(local):        # "AEP@myworkday.com": the mailbox is the company
        return local if any(c.isupper() for c in local) else local.capitalize()
    if domain and not is_ats(domain):
        parts = domain.split(".")
        return parts[-2].capitalize() if len(parts) >= 2 else None
    return None


def find(sender: str, subject: str, text: str, apps: list[App]) -> Match:
    _, _, domain = sender_parts(sender)
    from_ats = is_ats(domain)
    by_key: dict[str, list[App]] = {}
    for app in apps:
        for key in app.keys:
            by_key.setdefault(key, []).append(app)
    scores = _scores(sender, subject, text, set(by_key))
    if not from_ats and sum(1 for s in scores.values() if s == BODY) >= DIGEST_COMPANIES:
        scores = {k: s for k, s in scores.items() if s != BODY}
    if not scores:
        by_req = _by_req(apps, subject, text)
        if by_req:
            return Match(by_req.keys[0], by_req.company, (by_req,), by_req, BODY, from_ats)
        return Match(None, guess_company(sender), (), None, 0, from_ats)
    best = {a: max(scores.get(k, 0) for k in a.keys) for a in apps}       # an app's tracker or board name
    top = max(best.values())
    pool = [a for a, s in best.items() if s == top]
    shared = set.intersection(*({k for k in a.keys if scores.get(k) == top} for a in pool))
    key = min(shared) if shared else next(k for k in pool[0].keys if scores.get(k) == top)
    if not shared:                                                        # two companies tie: ask
        return Match(key, pool[0].company, tuple(pool), None, top, from_ats)
    open_apps = [a for a in pool if a.status in OPEN_FIRST] or pool
    return Match(key, pool[0].company, tuple(pool), _pick_role(open_apps, subject, text), top, from_ats)
