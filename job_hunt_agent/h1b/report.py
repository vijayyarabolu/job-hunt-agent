"""GOAL24 §1/§2: how well the filing employers match job-hunt-agent's companies, and the label of every open US job.
`job-hunt-agent h1b report` prints it; PROGRESS24 quotes it."""
from collections import Counter

from .. import config
from . import label, names

TOP_UNMATCHED = 25


def _open_us(conn) -> list:
    return conn.execute("SELECT j.company, j.sponsorship, j.families, count(*) AS n FROM jobs j "
                        "JOIN boards b ON b.board = j.board WHERE j.closed_at IS NULL AND j.is_us = 1 "
                        "AND b.active = 1 GROUP BY j.company, j.sponsorship, j.families").fetchall()


def _in_families(families: str | None, wanted: set[str]) -> bool:
    return any(f",{f}," in (families or "") for f in wanted)


def build(conn) -> dict:
    """{files, ref, companies_by_status, jobs: {kind: n}, match: {...}, unmatched: [(name, data, all)], ...}."""
    label.forget()
    idx = label.index(conn)
    wanted = {f for p in config.profiles() for f in p.get("families", ())}
    jobs, jobs_fam = Counter(), Counter()
    seen, matched, seen_fam, matched_fam = set(), set(), set(), set()
    for r in _open_us(conn):
        b = label.badge(dict(r), idx)
        kind = b.kind if b else "unknown"
        jobs[kind] += r["n"]
        k = names.key(r["company"])
        seen.add(k)
        hit = k in idx.companies
        if hit:
            matched.add(k)
        if _in_families(r["families"], wanted):
            jobs_fam[kind] += r["n"]
            seen_fam.add(k)
            if hit:
                matched_fam.add(k)
    statuses = Counter(c["status"] for c in idx.companies.values())
    data_filers = {k: c for k, c in idx.companies.items() if c["recent_data"] > 0}
    unmatched = sorted((c for k, c in data_filers.items() if k not in seen), key=lambda c: -c["recent_data"])
    n_unmatched = len(unmatched)
    unmatched = list({c["name"]: c for c in reversed(unmatched)}.values())[::-1]   # a dba group repeats its lead
    files = [dict(r) for r in conn.execute("SELECT name, rows, first_date, last_date FROM h1b_files ORDER BY name")]
    return {"files": files, "ref": idx.ref, "companies_by_status": dict(statuses),
            "jobs": dict(jobs), "jobs_in_families": dict(jobs_fam),
            "match": {"companies": len(seen), "matched": len(matched), "family_companies": len(seen_fam),
                      "family_matched": len(matched_fam), "data_filers": len(data_filers),
                      "data_filers_fetched": len(data_filers) - n_unmatched},
            "unmatched": [(c["name"], c["recent_data"], c["recent_all"]) for c in unmatched[:TOP_UNMATCHED]],
            "learned": len(idx.learned)}


def _pct(a: int, b: int) -> str:
    return f"{100 * a / b:.1f}%" if b else "n/a"


def text(rep: dict) -> str:
    m = rep["match"]
    lines = [f"Filing data through {rep['ref']}:"]
    lines += [f"  {f['name']}: {f['rows']:,} filings ({f['first_date']} .. {f['last_date']})" for f in rep["files"]]
    lines.append("Companies in the filings: " + ", ".join(f"{k} {v:,}" for k, v in sorted(rep["companies_by_status"].items())))
    lines.append(f"Match: {m['matched']:,} of {m['companies']:,} companies with open US jobs ({_pct(m['matched'], m['companies'])}); "
                 f"in your role families {m['family_matched']:,} of {m['family_companies']:,} "
                 f"({_pct(m['family_matched'], m['family_companies'])})")
    lines.append(f"Employers with data / AI filings in the last 12 months: {m['data_filers']:,}; job-hunt-agent fetches "
                 f"{m['data_filers_fetched']:,} of them ({_pct(m['data_filers_fetched'], m['data_filers'])})")
    for title, counts in (("Open US jobs", rep["jobs"]), ("Open US jobs in your role families", rep["jobs_in_families"])):
        total = sum(counts.values())
        lines.append(f"{title} ({total:,}): " + ", ".join(f"{k} {v:,} ({_pct(v, total)})" for k, v in
                                                            sorted(counts.items(), key=lambda kv: -kv[1])))
    lines.append(f"Learned auto-rejects: {rep['learned']}")
    lines.append("Biggest data / AI filers job-hunt-agent does not fetch yet:")
    lines += [f"  {n}: {d} data / AI filings ({a} in all) in 12 months" for n, d, a in rep["unmatched"]]
    return "\n".join(lines)
