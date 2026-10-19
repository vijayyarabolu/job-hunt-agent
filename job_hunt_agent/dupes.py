"""Duplicates: the same posting twice for one person, found by itself so one click removes the extra.

Two feed jobs, or two tracker rows, are the same when they share the company + a near title (case, punctuation,
"Sr." = "Senior", "ML" = "Machine Learning"; the level stays: "Senior Data Engineer" is not "Data Engineer") + the
first place, or the same apply / posting URL, or (feed jobs) the same company's posting id on the same board system, or
(tracker rows) the same feed job, unless applied more than REAPPLY_DAYS apart (a re-application). A requisition id is no key: companies share one across teams (DoorDash P-113069 is
three different roles), so two feed jobs with different ones are never the same by title. A URL shared by more than
URL_SHARE_MAX items is a careers page, not a posting, so it pairs nothing. Groups chain (A~B, B~C: one group). The newest of each group is kept; every other one is a duplicate of it. ranking.collapse already folds copies
of one req with the same JD into one card ("+2 locations"); this catches the rest (a repost with an edited JD, a row
typed twice into the Sheet)."""
import re
from datetime import date
from urllib.parse import parse_qsl, urlencode, urlsplit

from . import applied

_WORD = re.compile(r"[a-z0-9]+")
_TRACKING = re.compile(r"^(utm_|ref$|source$|src$|lever-source|gh_src$)")
URL_SHARE_MAX = 3
REAPPLY_DAYS = 30
_SAME = {"sr": "senior", "jr": "junior", "ml": "machine learning", "mle": "machine learning engineer",
         "swe": "software engineer", "ds": "data scientist", "genai": "generative ai"}
_FILLER = {"the", "a", "of", "and"}


def _title(title: str | None) -> str:
    words = _WORD.findall((title or "").lower())
    return " ".join(_SAME.get(w, w) for w in words if w not in _FILLER)


def _place(location: str | None) -> str:
    return " ".join(_WORD.findall(re.split(r"[;|]", location or "")[0].lower()))


def _url(url: str | None) -> str:
    """The posting's address without tracking parameters or #part (a query can be the posting: ?gh_jid=8144196)."""
    parts = urlsplit((url or "").strip().lower())
    if not parts.netloc:
        return ""
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if not _TRACKING.match(k)])
    return f"{parts.netloc}{parts.path.rstrip('/')}" + (f"?{query}" if query else "")


def _groups(items: list, keys_of, newest) -> dict:
    """{id of a duplicate: id of the newest item it duplicates}; items sharing any key are one group."""
    parent: dict = {}

    def root(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    first: dict = {}
    keyed = [(ident, keys_of(item)) for ident, item in items]
    shared: dict = {}
    for _, keys in keyed:
        for key in keys:
            shared[key] = shared.get(key, 0) + 1
    for ident, _ in items:
        parent[ident] = ident
    for ident, keys in keyed:
        for key in keys:
            if key[0] == "u" and shared[key] > URL_SHARE_MAX:
                continue
            if key in first:
                parent[root(ident)] = root(first[key])
            else:
                first[key] = ident
    members: dict = {}
    for ident, item in items:
        members.setdefault(root(ident), []).append((ident, item))
    out = {}
    for group in members.values():
        if len(group) < 2:
            continue
        keep = max(group, key=lambda pair: newest(pair[1]))[0]
        out.update({ident: keep for ident, _ in group if ident != keep})
    return out


def _job_keys(row: dict) -> list[tuple]:
    company = applied.company_key(row.get("company"))
    req = str(row.get("req_id") or "").strip().lower()
    keys = [("t", company, _title(row.get("title")), _place(row.get("location")), req)]
    keys += [("u", url) for url in {_url(row.get("apply_url")), _url(row.get("url"))} - {""}]
    if row.get("board") and row.get("ext_id"):                      # posting ids are per company on most boards
        keys.append(("a", str(row["board"]).split(":")[0], company, str(row["ext_id"])))
    return keys


def jobs(rows: list[dict]) -> dict[str, str]:
    """Feed jobs (one person's list): {uid of a duplicate: uid of the newest copy}."""
    return _groups([(r["uid"], r) for r in rows], _job_keys,
                   lambda r: (r.get("first_posted_at") or r.get("posted_at") or "", r.get("first_seen") or "", r["uid"]))


def _app_keys(row: dict) -> list[tuple]:
    company, title = applied.company_key(row.get("company")), _title(row.get("role_title"))
    keys = [("t", company, title, _place(row.get("address") or row.get("location")))] if company and title else []
    keys += [("u", url) for url in {_url(row.get("job_url")), _url(row.get("apply_url"))} - {""}]
    return keys + ([("j", row["job_uid"])] if row.get("job_uid") else [])


def _day(row: dict):
    """When the row was applied, else when it was last changed (a row saved to apply later)."""
    try:
        return date.fromisoformat(str(row.get("applied_on") or row.get("updated_at") or "")[:10])
    except ValueError:
        return None


def _rounds(group: list[dict]) -> list[list[dict]]:
    """One group of the same role split into application rounds: days more than REAPPLY_DAYS apart start a new
    round (a re-application, or a repost saved months later); rows without any date join the latest round."""
    dated = sorted((r for r in group if _day(r)), key=_day)
    rounds: list[list[dict]] = []
    for row in dated:
        if not rounds or (_day(row) - _day(rounds[-1][-1])).days > REAPPLY_DAYS:
            rounds = [*rounds, []]
        rounds[-1].append(row)
    undated = [r for r in group if not _day(r)]
    return [*rounds[:-1], [*rounds[-1], *undated]] if rounds else [undated]


def _app_newest(row: dict) -> tuple:
    return row.get("applied_on") or "", row.get("updated_at") or "", row["id"]


def apps(conn, pid: str) -> dict[int, int]:
    """The person's tracker rows: {id of a duplicate row: id of the newest of its round} (a re-application months
    later is its own round, and a row typed twice in an older round is still found)."""
    rows = [dict(r) for r in conn.execute(
        "SELECT id, company, role_title, address, location, job_url, apply_url, job_uid, applied_on, updated_at "
        "FROM applications WHERE profile = ?", (pid,))]
    by_id = {r["id"]: r for r in rows}
    groups: dict[int, list[dict]] = {}
    for dup, keep in _groups([(r["id"], r) for r in rows], _app_keys, _app_newest).items():
        groups.setdefault(keep, [by_id[keep]]).append(by_id[dup])
    out = {}
    for group in groups.values():
        for round_ in _rounds(group):
            if len(round_) > 1:
                keep = max(round_, key=_app_newest)["id"]
                out.update({r["id"]: keep for r in round_ if r["id"] != keep})
    return out


def label(company: str | None, title: str | None) -> str:
    return f"{company or '(company)'} · {title or '(role)'}"
