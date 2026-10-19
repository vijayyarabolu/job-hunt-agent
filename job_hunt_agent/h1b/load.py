"""the public DOL disclosure files (H-1B LCAs and PERM green-card cases, the last 8 quarters) into
h1b_employers / h1b_months, rolled up per company into h1b_companies.

Only certified H-1B / H-1B1 LCAs (withdrawn after certification too: the company still filed) and certified PERM
cases count. Per employer (one per FEIN, else per name) and month: all filings, data / AI ones (names.is_data_role),
their wage levels, worksite states and titles. Never a worker's data; of the contact email only its domain. The
whole set is rebuilt on every load (one short write transaction after the files are read), so a load is idempotent.
"""
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .. import config
from ..db import iso, tx, utcnow
from . import names, xlsx

QUARTERS = 8
RECENT_MONTHS = 12
TOP_STATES, TOP_TITLES, TOP_EMPLOYERS = 5, 5, 5
MAX_DBA_SHARE = 3                 # a trade name on more employers' filings than this is a franchise brand
LCA_COLS = ("CASE_NUMBER", "CASE_STATUS", "DECISION_DATE", "VISA_CLASS", "JOB_TITLE", "SOC_CODE", "EMPLOYER_NAME",
            "TRADE_NAME_DBA", "EMPLOYER_FEIN", "NAICS_CODE", "EMPLOYER_STATE", "EMPLOYER_POC_EMAIL", "WORKSITE_STATE",
            "PW_WAGE_LEVEL")
PERM_COLS = ("CASE_NUMBER", "CASE_STATUS", "DECISION_DATE", "JOB_TITLE", "PWD_SOC_CODE", "PW_SOC_CODE",
             "EMP_BUSINESS_NAME", "EMPLOYER_NAME", "EMP_TRADE_NAME", "EMP_FEIN", "EMPLOYER_FEIN", "EMP_NAICS",
             "NAICS_CODE", "EMP_STATE", "EMPLOYER_STATE_PROVINCE", "EMP_POC_EMAIL", "EMP_CONTACT_EMAIL",
             "PRIMARY_WORKSITE_STATE", "WORKSITE_STATE")
LCA_FILED = frozenset({"certified", "certified - withdrawn", "certified-withdrawn"})
PERM_FILED = frozenset({"certified", "certified-expired", "certified - expired"})
FREE_MAIL = frozenset({"gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "aol.com", "icloud.com", "live.com",
                       "msn.com", "protonmail.com", "comcast.net", "ymail.com", "rediffmail.com"})
_FILE = re.compile(r"(LCA|PERM)_Dis[a-z]*_Data_(?:New_Form_)?FY(\d{4})(?:_Q(\d))?\.xlsx$", re.I)
WAGE_LEVELS = ("I", "II", "III", "IV")


def data_dir() -> Path:
    return config.DATA / "h1b"


def parse_name(name: str) -> tuple[str, int, int] | None:
    """("lca" | "perm", fiscal year, last quarter covered) of a DOL file name; a yearly file covers Q4."""
    m = _FILE.search(name)
    if not m:
        return None
    return m.group(1).lower(), int(m.group(2)), int(m.group(3) or 4)


def _qindex(fy: int, q: int) -> int:
    return fy * 4 + q - 1


def quarter_start(index: int) -> date:
    """First day of a fiscal quarter (FY Q1 starts in October of the year before)."""
    fy, q = divmod(index, 4)
    month = 10 + 3 * q
    return date(fy - 1 + (month - 1) // 12, (month - 1) % 12 + 1, 1)


def wanted(names_or_urls: list[str]) -> tuple[list[str], date | None]:
    """The files that cover the last QUARTERS quarters of the newest LCA data, and the window's first day. A file is
    taken when its last quarter is in the window (the current year's file is cumulative from Q1; a yearly PERM file
    covers its whole year), rows outside the window are dropped when read."""
    parsed = [(u, parse_name(u.rsplit("/", 1)[-1])) for u in names_or_urls]
    lca = [_qindex(p[1], p[2]) for _, p in parsed if p and p[0] == "lca"]
    if not lca:
        return [], None
    start = max(lca) - QUARTERS + 1
    keep = [u for u, p in parsed if p and _qindex(p[1], p[2]) >= start]
    return keep, quarter_start(start)


@dataclass
class _Emp:
    names: Counter = field(default_factory=Counter)
    dbas: Counter = field(default_factory=Counter)
    naics: Counter = field(default_factory=Counter)
    states: Counter = field(default_factory=Counter)
    domains: Counter = field(default_factory=Counter)


@dataclass
class _Month:
    n_all: int = 0
    n_data: int = 0
    levels: Counter = field(default_factory=Counter)
    states: Counter = field(default_factory=Counter)
    titles: Counter = field(default_factory=Counter)


@dataclass
class Batch:
    """Filings read from the files, before they are written."""
    employers: dict = field(default_factory=lambda: defaultdict(_Emp))
    months: dict = field(default_factory=lambda: defaultdict(_Month))
    seen: set = field(default_factory=set)
    files: list = field(default_factory=list)
    last_lca: date | None = None


def _ident(fein: str | None, name: str) -> str:
    digits = re.sub(r"\D", "", fein or "")
    return f"fein:{digits}" if len(digits) == 9 and digits.strip("0") else f"name:{names.key(name)}"


def _domain(email: str | None) -> str | None:
    found = (email or "").strip().lower().rpartition("@")[2]
    return found if "." in found and found not in FREE_MAIL else None


def _title(text: str | None) -> str:
    return " ".join((text or "").split())[:80].title()


def _first(row: dict, *cols: str) -> str | None:
    return next((row[c] for c in cols if row.get(c)), None)


def _add(batch: Batch, program: str, row: dict, name: str, when: date, soc: str | None) -> None:
    ident = _ident(_first(row, "EMPLOYER_FEIN", "EMP_FEIN"), name)
    emp = batch.employers[ident]
    emp.names[" ".join(name.split())] += 1
    dba = _first(row, "TRADE_NAME_DBA", "EMP_TRADE_NAME")
    if dba and dba.strip().upper() not in ("N/A", "NA", "NONE"):
        emp.dbas[" ".join(dba.split())] += 1
    naics = _first(row, "NAICS_CODE", "EMP_NAICS")
    if naics:
        emp.naics[naics.split(".")[0]] += 1
    head = _first(row, "EMPLOYER_STATE", "EMP_STATE", "EMPLOYER_STATE_PROVINCE")
    if head:
        emp.states[head.strip().upper()[:20]] += 1
    domain = _domain(_first(row, "EMPLOYER_POC_EMAIL", "EMP_POC_EMAIL", "EMP_CONTACT_EMAIL"))
    if domain:
        emp.domains[domain] += 1
    month = batch.months[(ident, program, when.strftime("%Y-%m"))]
    month.n_all += 1
    worksite = _first(row, "WORKSITE_STATE", "PRIMARY_WORKSITE_STATE")
    if worksite:
        month.states[worksite.strip().upper()[:20]] += 1
    if names.is_data_role(soc, row.get("JOB_TITLE")):
        month.n_data += 1
        month.titles[_title(row.get("JOB_TITLE"))] += 1
        level = (row.get("PW_WAGE_LEVEL") or "").strip().upper()
        if level in WAGE_LEVELS:
            month.levels[level] += 1


def read_file(batch: Batch, path: Path, since: date) -> dict:
    """Add one DOL file's filings to the batch; {name, program, rows, first_date, last_date, bytes}."""
    kind = parse_name(path.name)
    if kind is None:
        raise ValueError(f"not a DOL disclosure file: {path.name}")
    program = kind[0]
    cols, filed = (LCA_COLS, LCA_FILED) if program == "lca" else (PERM_COLS, PERM_FILED)
    kept, first, last = 0, None, None
    for row in xlsx.rows(path, cols):
        case = (row.get("CASE_NUMBER") or "").strip()
        if not case or case in batch.seen or (row.get("CASE_STATUS") or "").strip().lower() not in filed:
            continue
        if program == "lca" and not (row.get("VISA_CLASS") or "").strip().upper().startswith("H-1B"):
            continue
        when = xlsx.to_date(row.get("DECISION_DATE"))
        name = _first(row, "EMPLOYER_NAME", "EMP_BUSINESS_NAME")
        if when is None or when < since or not name or not names.key(name):
            continue
        batch.seen.add(case)
        _add(batch, program, row, name, when, _first(row, "SOC_CODE", "PWD_SOC_CODE", "PW_SOC_CODE"))
        kept += 1
        first, last = min(first or when, when), max(last or when, when)
    if program == "lca" and last:
        batch.last_lca = max(batch.last_lca or last, last)
    info = {"name": path.name, "program": program, "rows": kept, "first_date": first and first.isoformat(),
            "last_date": last and last.isoformat(), "bytes": path.stat().st_size}
    batch.files.append(info)
    return info


def _cap_reason(name: str | None, dba: str | None, naics: str | None) -> str | None:
    return names.cap_exempt(name, naics) or (names.cap_exempt(dba, naics) if dba else None)


def _employer_row(ident: str, emp: _Emp) -> tuple:
    name = emp.names.most_common(1)[0][0]
    dba = emp.dbas.most_common(1)[0][0] if emp.dbas else None
    naics = emp.naics.most_common(1)[0][0] if emp.naics else None
    reason = _cap_reason(name, dba, naics)
    return (ident, name, dba, names.key(name), naics, emp.states.most_common(1)[0][0] if emp.states else None,
            emp.domains.most_common(1)[0][0] if emp.domains else None, reason)


def write(conn, batch: Batch, urls: dict[str, str] | None = None) -> dict:
    """Replace every h1b_* row with this batch, then roll up the companies. {employers, months, companies, ref}."""
    now = iso(utcnow())
    with tx(conn):
        for table in ("h1b_months", "h1b_companies", "h1b_employers", "h1b_files"):
            conn.execute(f"DELETE FROM {table}")
        ids = {}
        for ident, emp in batch.employers.items():
            cur = conn.execute("INSERT INTO h1b_employers(ident, name, dba, key, naics, state, domain, cap_exempt) "
                               "VALUES (?,?,?,?,?,?,?,?)", _employer_row(ident, emp))
            ids[ident] = cur.lastrowid
        conn.executemany(
            "INSERT INTO h1b_months(employer_id, program, month, n_all, n_data, levels, states, titles) "
            "VALUES (?,?,?,?,?,?,?,?)",
            [(ids[ident], program, month, m.n_all, m.n_data, json.dumps(dict(m.levels)),
              json.dumps(dict(m.states)), json.dumps(dict(m.titles.most_common(TOP_TITLES))))
             for (ident, program, month), m in batch.months.items()])
        for f in batch.files:
            conn.execute("INSERT OR REPLACE INTO h1b_files(name, program, url, bytes, rows, first_date, last_date, "
                         "imported_at) VALUES (?,?,?,?,?,?,?,?)",
                         (f["name"], f["program"], (urls or {}).get(f["name"]), f["bytes"], f["rows"],
                          f["first_date"], f["last_date"], now))
        ref = (batch.last_lca or date.today()).isoformat()
        conn.execute("INSERT OR REPLACE INTO h1b_meta(key, value) VALUES ('ref_date', ?), ('built_at', ?)",
                     (ref, now))
        n_companies = rollup(conn, ref)
    return {"employers": len(ids), "months": len(batch.months), "companies": n_companies, "ref": ref}


def months_back(ref: str, n: int) -> str:
    """'YYYY-MM' n months before the month of ref (an ISO date)."""
    year, month = int(ref[:4]), int(ref[5:7]) - n
    while month < 1:
        year, month = year - 1, month + 12
    return f"{year:04d}-{month:02d}"


def status(recent_all: int, recent_data: int, prior_all: int, perm_all: int, cap: str | None) -> str | None:
    """sponsors: >= 1 data / AI filing in the last 12 months, or >= 3 of any role; cap_exempt: a university /
    research employer that files; maybe: only older filings, a few unrelated ones, or green cards only."""
    if cap and (recent_all or prior_all or perm_all):
        return "cap_exempt"
    if recent_data >= 1 or recent_all >= 3:
        return "sponsors"
    if recent_all or prior_all or perm_all:
        return "maybe"
    return None


def _merge_json(target: Counter, text: str) -> None:
    for k, v in json.loads(text or "{}").items():
        target[k] += v


def _empty_acc() -> dict:
    return {"ra": 0, "rd": 0, "pa": 0, "pd": 0, "ma": 0, "md": 0, "lv": Counter(), "st": Counter(), "ti": Counter(),
            "last": None}


def _per_employer(conn, ref: str) -> dict[int, dict]:
    """employer id -> its LCAs in the last 12 months (ra / rd: all / data) and the 12 before (pa / pd), its PERM
    cases (ma / md), wage levels, worksite states and titles of its recent data roles, its last filing month."""
    recent_from, prior_from = months_back(ref, RECENT_MONTHS - 1), months_back(ref, 2 * RECENT_MONTHS - 1)
    per_emp: dict[int, dict] = defaultdict(_empty_acc)
    for m in conn.execute("SELECT * FROM h1b_months"):
        acc = per_emp[m["employer_id"]]
        if m["program"] == "perm":
            acc["ma"] += m["n_all"]
            acc["md"] += m["n_data"]
            continue
        recent = m["month"] >= recent_from
        if not recent and m["month"] < prior_from:
            continue
        acc["ra" if recent else "pa"] += m["n_all"]
        acc["rd" if recent else "pd"] += m["n_data"]
        acc["last"] = max(acc["last"] or m["month"], m["month"])
        _merge_json(acc["st"], m["states"])
        if recent:
            _merge_json(acc["lv"], m["levels"])
            _merge_json(acc["ti"], m["titles"])
    return per_emp


def _company_row(k: str, ids: list[int], emps: dict, per_emp: dict) -> tuple:
    tot = _empty_acc()
    for i in ids:
        acc = per_emp.get(i) or _empty_acc()
        for f in ("ra", "rd", "pa", "pd", "ma", "md"):
            tot[f] += acc[f]
        for f in ("lv", "st", "ti"):
            tot[f].update(acc[f])
        tot["last"] = max(tot["last"] or "", acc["last"] or "") or None
    size = lambda i: (per_emp[i]["ra"] + per_emp[i]["pa"] + per_emp[i]["ma"]) if i in per_emp else 0   # noqa: E731
    ranked = sorted(ids, key=lambda i: -size(i))
    # cap-exempt only when the cap-exempt employers file most of the key's cases: "The Citadel" (a college) must
    # not make Citadel (the hedge fund) cap-exempt
    cap_size = sum(size(i) for i in ids if emps[i]["cap_exempt"])
    cap = next((emps[i]["cap_exempt"] for i in ranked if emps[i]["cap_exempt"]), None) \
        if cap_size and 2 * cap_size >= sum(size(i) for i in ids) else None
    domain = next((emps[i]["domain"] for i in ranked if emps[i]["domain"]), None)
    listed = [[emps[i]["name"], per_emp[i]["ra"] if i in per_emp else 0] for i in ranked[:TOP_EMPLOYERS]]
    return (k, emps[ranked[0]]["name"], json.dumps(listed), status(tot["ra"], tot["rd"], tot["pa"], tot["ma"], cap),
            tot["ra"], tot["rd"], tot["pa"], tot["pd"], tot["ma"], tot["md"],
            tot["lv"].most_common(1)[0][0] if tot["lv"] else None,
            json.dumps([s for s, _ in tot["st"].most_common(TOP_STATES)]),
            json.dumps([t for t, _ in tot["ti"].most_common(TOP_TITLES)]), tot["last"], cap, domain)


def _groups(conn, emps: dict) -> dict[str, list[int]]:
    """company key -> employer ids. Keys are worked out again from the stored names (an alias added later applies
    without reading the files). A trade name (dba) adds its own key only when it names a company and at most
    MAX_DBA_SHARE employers use it: a franchise brand on many franchisees' filings (McDonald's) is not the brand."""
    groups: dict[str, list[int]] = defaultdict(list)
    dba_users: dict[str, set] = defaultdict(set)
    for r in emps.values():
        own = names.key(r["name"])
        if own != r["key"]:
            conn.execute("UPDATE h1b_employers SET key = ? WHERE id = ?", (own, r["id"]))
        cap = _cap_reason(r["name"], r["dba"], r["naics"])          # the rules may have changed since the load
        if cap != r["cap_exempt"]:
            conn.execute("UPDATE h1b_employers SET cap_exempt = ? WHERE id = ?", (cap, r["id"]))
            emps[r["id"]] = {**dict(r), "cap_exempt": cap}
        if own:
            groups[own].append(r["id"])
        if names.dba_counts(r["name"], r["dba"]) and names.key(r["dba"]) not in ("", own):
            dba_users[names.key(r["dba"])].add(r["id"])
    for k, ids in dba_users.items():
        if len(ids) <= MAX_DBA_SHARE:
            groups[k].extend(ids)
    return groups


def rerollup(conn) -> int:
    """Companies again from the stored employers (after a change to the name or cap-exempt rules or the aliases)."""
    ref = conn.execute("SELECT value FROM h1b_meta WHERE key = 'ref_date'").fetchone()
    if ref is None:
        raise FileNotFoundError("no filings loaded yet (job-hunt-agent h1b load)")
    with tx(conn):
        n = rollup(conn, ref[0])
        conn.execute("INSERT OR REPLACE INTO h1b_meta(key, value) VALUES ('built_at', ?)", (iso(utcnow()),))
    return n


def rollup(conn, ref: str) -> int:
    """h1b_companies from h1b_employers + h1b_months: every employer counts under its name's key (and its dba's)."""
    conn.execute("DELETE FROM h1b_companies")
    emps = {r["id"]: r for r in conn.execute("SELECT * FROM h1b_employers")}
    groups = _groups(conn, emps)
    per_emp = _per_employer(conn, ref)
    rows = [_company_row(k, ids, emps, per_emp) for k, ids in groups.items()]
    conn.executemany("INSERT INTO h1b_companies(key, name, employers, status, recent_all, recent_data, prior_all, "
                     "prior_data, perm_all, perm_data, level, states, titles, last_month, cap_exempt, domain) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    return len(rows)


def load_dir(conn, folder: Path | None = None, urls: dict[str, str] | None = None, log=print) -> dict:
    """Read every wanted DOL file in data/h1b (the last 8 quarters) and replace the h1b tables with them."""
    folder = folder or data_dir()
    files = sorted(p.name for p in folder.glob("*.xlsx"))
    picked, since = wanted(files)
    if not picked:
        raise FileNotFoundError(f"no DOL LCA files in {folder}")
    batch = Batch()
    # the newest files first: a case decided again later is counted once, at its latest decision
    for name in sorted(picked, key=lambda n: parse_name(n)[1:], reverse=True):
        info = read_file(batch, folder / name, since)
        log(f"  {name}: {info['rows']:,} filings ({info['first_date']} .. {info['last_date']})")
    result = write(conn, batch, urls)
    return {**result, "since": since.isoformat(), "files": batch.files}
