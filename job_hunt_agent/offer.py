"""GOAL29 §2: the Offer check, a separate panel on the job page. It never changes Match % or Priority; every check is
logged (offer_checks) so the next goal can decide whether it should.

Seven areas, each graded A to F with a word and one line of evidence (from the JD or our own data):
- free, from data job-hunt-agent has: Level fit (score.seniority), Location and remote (the job's place against the
  person's city and their saved Relocate answer), Sponsorship (the H-1B badge, only for a person who needs it),
  Hiring health (the company's roles opened vs closed on our boards in HEALTH_DAYS);
- Claude, one call per JD (cached by content_hash, never repeated), only for jobs with Match >= LLM_MIN_MATCH and
  while today's spend is under DAILY_BUDGET_USD: Growth, Team signals, Mission and product. With an Anthropic key saved
  (Settings > Setup) the call goes through the API (claude_api; its cost estimated from the tokens), else it runs
  headless on the computer's own Claude Code login (Haiku; its cost is Claude's own total_cost_usd).
The Pay row is gone (no pay range was ever set in a profile)."""
import json
import re
import subprocess
from datetime import datetime, timedelta, timezone

from . import claude_api, config, db, match, profile_settings, score
from .mail import claude as mail_claude

GRADES = "ABCDF"
WORDS = {"A": "Strong", "B": "Good", "C": "Fair", "D": "Weak", "F": "Poor", None: "Unknown"}
HEALTH_DAYS = 60
LLM_MIN_MATCH = 70
DAILY_BUDGET_USD = 1.0
HAIKU = "claude-haiku-5-5"
API_USD_PER_MTOK = (2.0, 10.0)       # input / output, claude_api.DEFAULT_MODEL's price (claude.com/pricing, October 2026)
LLM_TIMEOUT_S = 120
JD_MAX = 9000
PAUSED = "Offer check paused: daily budget reached"
LLM_AREAS = (("growth", "Growth"), ("team", "Team signals"), ("mission", "Mission and product"))
SHORT = {"Level fit": "level fit", "Location and remote": "location", "Sponsorship": "sponsorship",
         "Hiring health": "hiring health", "Growth": "growth", "Team signals": "team signals",
         "Mission and product": "mission"}
LLM_SCHEMA = {"type": "object", "additionalProperties": False, "required": [k for k, _ in LLM_AREAS],
              "properties": {k: {"type": "object", "additionalProperties": False, "required": ["grade", "quote"],
                                 "properties": {"grade": {"type": "string", "enum": [*GRADES, "?"]},
                                                "quote": {"type": "string"}}} for k, _ in LLM_AREAS}}
LLM_PROMPT = """You read one job posting for an applicant and grade three areas, A (best) to F (worst), or "?" when the
posting does not say. For each, quote the ONE short phrase from the posting (copied word for word, at most 20 words)
that the grade rests on, or "" with "?".

- growth: what this role would teach the applicant beyond what the user already does (new scale, new kinds of models,
  ownership, mentors). The applicant: {background}
- team: team signals: team size, who the user would work with, how mature their AI work is (shipped products, ML platform,
  research group) versus a first experiment.
- mission: the mission and the product: a clear product people use and a reason the work matters.

Be strict: a posting that only says "fast-paced" or "innovative" earns no better than C. JSON only.

Job: {role} at {company}
Posting:
{jd}"""


def _area(name: str, grade: str | None, evidence: str) -> dict:
    return {"area": name, "grade": grade, "word": WORDS[grade], "evidence": evidence}


def level(row: dict, profile: dict) -> dict:
    s, chips = score.seniority(row, profile.get("years"), profile.get("max_required_years"))
    grade = "A" if s >= 0.95 else "B" if s >= 0.75 else "C" if s >= 0.5 else "D" if s >= 0.3 else "F"
    asks = f"asks {row['min_years']}+ years" if row.get("min_years") else "years not stated"
    why = f"{row.get('title')}: {asks}; you have {profile.get('years')}"
    return _area("Level fit", grade, why + (f" ({', '.join(chips)})" if chips else ""))


def _state(place: str | None) -> str:
    m = re.search(r",\s*([A-Z]{2})\b", place or "")
    return m.group(1) if m else ""


def location(row: dict, profile: dict, relocate: str | None) -> dict:
    where, home = match.workplace_of(row), profile.get("location") or ""
    place = row.get("location") or "place not stated"
    if row.get("is_us") == 0:
        return _area("Location and remote", "F", f"Outside the US: {place}")
    if where == "remote":
        return _area("Location and remote", "A", f"Remote ({place})")
    city, state = home.split(",")[0].strip().lower(), _state(home)
    near = bool(state and _state(place) == state) or bool(city and city in place.lower())
    kind = {"hybrid": "Hybrid", "onsite": "On site"}.get(where or "", "Office")
    if near:
        return _area("Location and remote", "A", f"{kind} in {place}, near {home}")
    if where is None:
        return _area("Location and remote", "C", f"{place}: remote or office not stated")
    moves = str(relocate or "").strip().lower().startswith("y")
    return _area("Location and remote", "B" if moves else "D",
                 f"{kind} in {place}; you said you {'would' if moves else 'would not'} relocate")


def sponsorship(profile: dict, badge: dict | None) -> dict:
    if not profile.get("needs_sponsorship"):
        return _area("Sponsorship", "A", "You do not need sponsorship")
    if not badge:
        return _area("Sponsorship", None, "No H-1B filings or JD wording found for this company")
    grade = {"sponsors": "A", "cap_exempt": "A", "maybe": "C", "no": "F"}.get(badge.get("kind"))
    return _area("Sponsorship", grade, badge.get("why") or badge.get("text") or "")


def hiring_health(conn, company: str, now: datetime) -> dict:
    since = db.iso(now - timedelta(days=HEALTH_DAYS))
    opened, closed = conn.execute("SELECT sum(first_seen >= ? AND seed = 0), sum(closed_at >= ?) FROM jobs "
                                  "WHERE company = ?", (since, since, company)).fetchone()
    opened, closed = opened or 0, closed or 0
    if not opened and not closed:
        return _area("Hiring health", None, f"No roles opened or closed on our boards in {HEALTH_DAYS} days")
    ratio = opened / max(closed, 1)
    grade = "A" if ratio >= 1.2 else "B" if ratio >= 0.8 else "C" if ratio >= 0.5 else "D" if opened else "F"
    return _area("Hiring health", grade, f"Opened {opened} and closed {closed} roles on our boards in {HEALTH_DAYS} days")


# ---------- the Claude areas ----------

def _day(now: datetime) -> str:
    return now.astimezone().strftime("%Y-%m-%d")


def spent_today(conn, now: datetime) -> float:
    return float(conn.execute("SELECT coalesce(sum(cost_usd), 0) FROM offer_llm WHERE day = ?", (_day(now),)).fetchone()[0])


def cached(conn, content_hash: str) -> list | None:
    row = conn.execute("SELECT areas FROM offer_llm WHERE content_hash = ?", (content_hash,)).fetchone()
    return json.loads(row[0]) if row else None


def _squash(text: str) -> str:
    return re.sub(r"\W+", " ", text or "").strip().lower()


def _llm_areas(raw: dict | None, jd: str) -> list:
    """Three areas from Claude's answer; a quote not found in the JD drops its grade (never a made-up line)."""
    out = []
    for key, name in LLM_AREAS:
        got = (raw or {}).get(key) or {}
        grade, quote = str(got.get("grade") or "?"), str(got.get("quote") or "").strip()
        if grade not in GRADES or not _squash(quote) or _squash(quote) not in _squash(jd):
            out.append(_area(name, None, "The posting does not say"))
        else:
            out.append(_area(name, grade, f"“{quote}”"))
    return out


def _run_api(prompt: str) -> tuple:
    try:
        answer, usage = claude_api.ask(prompt, schema=LLM_SCHEMA, max_tokens=2000, effort="low")
    except claude_api.ClaudeError as err:
        raise ValueError(str(err)) from None
    if not isinstance(answer, dict):
        raise ValueError("no answer came back")
    cost = (usage["input_tokens"] * API_USD_PER_MTOK[0] + usage["output_tokens"] * API_USD_PER_MTOK[1]) / 1e6
    return answer, cost


def run_claude(prompt: str) -> tuple:
    """(answer dict, cost in USD) of one Claude call: the API with a saved key, else headless Haiku on the Claude Code
    login; ValueError on failure."""
    if claude_api.configured():
        return _run_api(prompt)
    cmd = [mail_claude.claude_bin(), "-p", "--output-format", "json", "--json-schema", json.dumps(LLM_SCHEMA),
           "--model", HAIKU, "--tools", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
           "--setting-sources", "", "--disable-slash-commands", "--no-session-persistence", "--max-turns", "4"]
    try:
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=LLM_TIMEOUT_S,
                              env=mail_claude.env_for(""))
        raw = json.loads((proc.stdout or "").strip() or "{}")
    except (OSError, subprocess.TimeoutExpired, ValueError) as err:
        raise ValueError(f"Claude did not answer ({type(err).__name__})") from None
    answer = raw.get("structured_output")
    if answer is None:
        try:
            answer = json.loads(raw.get("result") or "")
        except (TypeError, ValueError):
            answer = None
    if raw.get("is_error") or not isinstance(answer, dict):
        noise = f"{raw.get('result') or ''} {proc.stderr or ''}"
        raise ValueError("Claude usage limit" if mail_claude.LIMIT.search(noise) else
                         "Not logged in to Claude" if mail_claude.LOGIN.search(noise) else "no answer came back")
    return answer, float(raw.get("total_cost_usd") or 0)


def _background(profile: dict) -> str:
    return (f"{profile.get('years')} years as {profile.get('headline_default') or 'an engineer'}; "
            f"industries {', '.join(profile.get('industries') or []) or 'not listed'}")


def llm(conn, row: dict, profile: dict, now: datetime, runner=None) -> dict:
    """{"areas": [...], "state": "cached" | "new" | "paused" | "error", "cost": n}. Never called twice for one JD."""
    hit = cached(conn, row["content_hash"])
    if hit is not None:
        return {"areas": hit, "state": "cached", "cost": 0.0}
    if spent_today(conn, now) >= DAILY_BUDGET_USD:
        return {"areas": [], "state": "paused", "note": PAUSED, "cost": 0.0}
    prompt = LLM_PROMPT.format(background=_background(profile), role=row.get("title"), company=row.get("company"),
                               jd=row["jd"][:JD_MAX])
    try:
        raw, cost = (runner or run_claude)(prompt)
    except ValueError as err:
        return {"areas": [], "state": "error", "note": f"Offer check: {err}", "cost": 0.0}
    areas = _llm_areas(raw, row["jd"])
    with db.tx(conn):
        conn.execute("INSERT OR IGNORE INTO offer_llm(content_hash, areas, raw, model, cost_usd, day, at) "
                     "VALUES (?,?,?,?,?,?,?)",
                     (row["content_hash"], json.dumps(areas), json.dumps(raw), HAIKU, cost, _day(now), db.iso(now)))
    return {"areas": areas, "state": "new", "cost": cost}


# ---------- the whole check ----------

def _and(words: list) -> str:
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def overall(areas: list) -> tuple:
    """(grade, line): "Offer check: B, good level fit and team signals, unclear growth"."""
    graded = [GRADES.index(a["grade"]) for a in areas if a["grade"]]
    if not graded:
        return None, "Offer check: not enough data yet"
    grade = GRADES[round(sum(graded) / len(graded))]
    good = [SHORT[a["area"]] for a in areas if a["grade"] in ("A", "B")]
    weak = [SHORT[a["area"]] for a in areas if a["grade"] in ("D", "F")]
    unclear = [SHORT[a["area"]] for a in areas if not a["grade"] and a["area"] in dict(LLM_AREAS).values()]
    bits = [f"good {_and(good)}" if good else "", f"weak {_and(weak)}" if weak else "",
            f"unclear {_and(unclear)}" if unclear else ""]
    said = ", ".join(b for b in bits if b)
    return grade, f"Offer check: {grade}, {said}" if said else f"Offer check: {grade}"


def _job(conn, uid: str) -> dict:
    row = conn.execute("SELECT uid, company, title, location, workplace, is_us, salary_min, salary_max, salary_ccy, "
                       "min_years, content_hash, description FROM jobs WHERE uid = ?", (uid,)).fetchone()
    if row is None:
        raise KeyError(uid)
    out = dict(row)
    out["jd"] = db.unpack(out.pop("description"))
    return out


def _llm_state(conn, row: dict, match_pct: int | None, now: datetime) -> tuple:
    if cached(conn, row["content_hash"]) is not None:
        return "cached", None
    if match_pct is None or match_pct < LLM_MIN_MATCH:
        return "skipped", f"Checked only for jobs with Match {LLM_MIN_MATCH}% or more"
    if spent_today(conn, now) >= DAILY_BUDGET_USD:
        return "paused", PAUSED
    return "ready", "Checking growth, team and mission with Claude"


def check(conn, pid: str, uid: str, match_pct: int | None, badge: dict | None = None, now: datetime | None = None,
          run_llm: bool = False, runner=None) -> dict:
    """The panel: {"grade", "line", "areas": [...], "llm": state, "note"}. run_llm: call Claude now when the job
    qualifies and nothing is cached (the panel asks for it after it opens; the free areas never wait for it)."""
    now = now or datetime.now(timezone.utc)
    profile, row = config.profile(pid), _job(conn, uid)
    relocate = profile_settings.answers(pid, profile).get("relocate")
    areas = [level(row, profile), location(row, profile, relocate), sponsorship(profile, badge),
             hiring_health(conn, row["company"], now)]
    state, note = _llm_state(conn, row, match_pct, now)
    extra = cached(conn, row["content_hash"]) or []
    if state == "ready" and run_llm:
        got = llm(conn, row, profile, now, runner)
        state, note, extra = got["state"], got.get("note"), got["areas"]
    areas += extra or [_area(name, None, note or "") for _, name in LLM_AREAS]
    grade, line = overall(areas)
    _log(conn, pid, row, grade, line, areas, now)
    return {"grade": grade, "line": line, "areas": areas, "llm": state, "note": note}


def _log(conn, pid: str, row: dict, grade, line: str, areas: list, now: datetime) -> None:
    with db.tx(conn):
        conn.execute("INSERT INTO offer_checks(profile, uid, content_hash, grade, line, areas, at) VALUES (?,?,?,?,?,?,?) "
                     "ON CONFLICT(profile, uid) DO UPDATE SET content_hash = excluded.content_hash, "
                     "grade = excluded.grade, line = excluded.line, areas = excluded.areas, at = excluded.at",
                     (pid, row["uid"], row["content_hash"], grade, line, json.dumps(areas), db.iso(now)))
