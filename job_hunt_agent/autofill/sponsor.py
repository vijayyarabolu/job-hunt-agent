"""the job's H-1B sponsorship badge for the Autofill panel, and the future-sponsorship answer it fills.

Answers page › Legal: "At companies marked Sponsors or Cap-exempt, answer the future-sponsorship question: Yes"
(sponsor_auto, default On). At such a company the "now or in the future" question gets Yes; everywhere else, and
always for "do you need sponsorship now", the person's saved answer is used unchanged."""
from .. import config
from ..h1b import feed, label

SETTING = "sponsor_auto"
DEFAULT = "On"
SAYS_YES = ("sponsors", "cap_exempt")
_ON = ("on", "yes", "true", "1")


def is_on(bank: dict) -> bool:
    """The setting (a bank row; a bank from before GOAL24 without it counts as the default, On)."""
    entry = bank.get(SETTING)
    value = str((entry or {}).get("answer") or "").strip().lower() if entry else DEFAULT.lower()
    return value in _ON


def badge_for(conn, pid: str, uid: str | None, app_id: int | None) -> dict | None:
    """The card view of the job's badge (feed job: its JD's word + its company; tracker row: its company)."""
    if pid not in config.profile_ids() or not config.profile(pid).get("needs_sponsorship"):
        return None
    row = None
    if uid:
        row = conn.execute("SELECT company, sponsorship FROM jobs WHERE uid = ?", (uid,)).fetchone()
    elif app_id:
        row = conn.execute("SELECT a.company, j.sponsorship FROM applications a LEFT JOIN jobs j ON j.uid = a.job_uid "
                           "WHERE a.profile = ? AND a.id = ?", (pid, app_id)).fetchone()
    if row is None:
        return None
    return feed.view(label.badge(dict(row), label.index(conn)))


def says_yes(view: dict | None, bank: dict) -> bool:
    return bool(view and view["kind"] in SAYS_YES and is_on(bank))


def panel(view: dict | None, bank: dict) -> dict:
    """What the panel shows before filling: the badge and which future-sponsorship answer it will use."""
    saved = str((bank.get("sponsor_future") or {}).get("answer") or "").strip()
    if says_yes(view, bank):
        answer, why = "Yes", "this company sponsors, Answers › Legal setting"
    elif saved:
        answer, why = saved, "your saved answer"
    else:
        answer, why = "", "not saved yet: it will ask you"
    return {"badge": view, "future": answer, "why": why, "setting": is_on(bank)}
