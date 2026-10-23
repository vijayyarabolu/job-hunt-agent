"""The Autofill canary (`job-hunt-agent autofill canary`; schedule `nightly` with cron to run it after the fetch is quiet).

The real extension in headless Chrome fills, as a fake person on a throwaway job-hunt-agent (temp database + temp profile;
the real one is only read for which live pages to try and written with the results):
- every board fixture in tests/fixtures/autofill/canary.json (saved public apply pages, each served at its own
  board's address so the right adapter runs),
- every regression fixture: each page snapshot the user sent and each page an error / unknown layout reported becomes
  data/autofill/fixtures/<name>.html (every typed value, tick and pick removed), expected to fill at least as many
  fields as its first run (raised when it fills more),
- a few live public apply pages (Greenhouse, Lever, Ashby, Workable, BambooHR: the newest open job of each), never
  submitted (scripts/autofill/canary.mjs blocks every request that is not a GET).
A page filling clearly less than its last good run is a notice in job-hunt-agent the next morning (health.record_canary).
"""
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
import zlib
from pathlib import Path

from .. import config, db
from . import bank, health

CHROME = os.environ.get("JH_CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")   # JH_CHROME: another Chromium
NODE_DIRS = ("/opt/homebrew/bin", "/usr/local/bin")          # a scheduler's PATH may have neither
REPO = Path(__file__).resolve().parents[2]                    # the code's own checkout (JOB_HUNT_AGENT_HOME may differ)
RUNNER = REPO / "scripts" / "autofill" / "canary.mjs"
EXTENSION = REPO / "extension"
FIXTURES = REPO / "tests" / "fixtures" / "autofill"
BOARD_MANIFEST = FIXTURES / "canary.json"
LIVE_BOARDS = ("greenhouse", "lever", "ashby", "workable", "bamboohr")
MAX_REGRESSION = 40                                          # the newest snapshots / error pages
RUN_TIMEOUT_S = 3600
QUIET_WAIT_S = 1800                                          # waits this long for a running fetch to finish
QUIET_POLL_S = 30
PID = "canary"

FAKE_PROFILE = """name: Casey Canary
email: canary@example.com
phone: 555-0100
location: Foster City, CA
linkedin: https://www.linkedin.com/in/casey-canary-example
github: https://github.com/casey-canary-example
families: [ml_engineer]
years: 4
needs_sponsorship: false
resumes:
  - {id: ml, label: ML Engineer, file: Casey_ML.docx, families: [ml_engineer]}
"""
FAKE_BASELINE = {"summary": "ML engineer.", "skills": [{"category": "ML", "items": ["Python", "PyTorch"]}],
                 "experience": [{"company": "Example Corp", "title": "ML Engineer", "dates": "Jul 2023 – Present",
                                 "bullets": ["Built models."]}],
                 "education": [{"degree": "Master of Science in Computer Science", "school": "Example University",
                                "dates": "Aug 2021 – May 2023"}]}

FAKE_ANSWERS = {"work_auth": "Yes", "sponsor_now": "No", "sponsor_future": "No", "age18": "Yes", "address1": "11 Example Street",
                "city": "Foster City", "state": "California", "zip": "94404", "country": "United States", "salary": "150000",
                "start_date": "2 weeks after offer acceptance", "notice": "2 weeks", "relocate": "Yes", "onsite": "Yes",
                "travel": "Yes", "how_heard": "LinkedIn", "prev_worked": "No", "related_employee": "No", "referred": "No",
                "criminal": "No", "noncompete": "No", "background_check": "Yes", "drug_test": "Yes", "clearance": "No",
                "gender": "Decline to self-identify", "hispanic": "Decline to self-identify", "race": "Decline to self-identify",
                "veteran": "I don't wish to answer", "disability": "I do not want to answer",
                "sms_consent": "No"}                                  # GOAL30 §0c: Ashby's texting consent radio is checked too

_INPUT = re.compile(r"<input\b[^>]*>", re.I)
_TYPE = re.compile(r"""\btype\s*=\s*["']?([a-z-]+)""", re.I)
_VALUE = re.compile(r"""\svalue\s*=\s*("[^"]*"|'[^']*'|[^\s>]*)""", re.I)
_CHECKED = re.compile(r"""\schecked(\s*=\s*("[^"]*"|'[^']*'|[^\s>]*))?""", re.I)
_SELECTED = re.compile(r"""(<option\b[^>]*?)\sselected(\s*=\s*("[^"]*"|'[^']*'|[^\s>]*))?""", re.I)
_TEXTAREA = re.compile(r"(<textarea\b[^>]*>)[\s\S]*?(</textarea>)", re.I)
KEEP_VALUE = {"radio", "checkbox", "hidden", "submit", "button", "image", "reset"}


def blank(html: str) -> str:
    """A form with nothing of the user's in it: no typed value, no tick, no pick (a radio / checkbox keeps its option value)."""
    def one(m: re.Match) -> str:
        tag = m.group(0)
        kind = (_TYPE.search(tag).group(1) if _TYPE.search(tag) else "text").lower()
        return _CHECKED.sub("", tag) if kind in KEEP_VALUE else _VALUE.sub("", tag)
    return _TEXTAREA.sub(r"\1\2", _SELECTED.sub(r"\1", _INPUT.sub(one, html)))


def _read(path: Path) -> list[dict]:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return []


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".part")
    partial.write_text(json.dumps(rows, indent=1))
    partial.replace(path)


def regression_dir() -> Path:
    return config.DATA / "autofill" / "fixtures"


def export(conn) -> list[dict]:
    """New snapshots and reported error pages -> regression fixtures (the newest MAX_REGRESSION are kept)."""
    folder = regression_dir()
    manifest = _read(folder / "manifest.json")
    have = {m["name"] for m in manifest}
    rows = [("snapshot", r) for r in conn.execute("SELECT id, url, board, '' AS step, html, at FROM autofill_snapshots")]
    rows += [("error", r) for r in conn.execute("SELECT id, url, board, step, html, at FROM autofill_errors WHERE html IS NOT NULL")]
    seen_pages = {(m["board"], m["url"].split("?")[0], m.get("step", "")) for m in manifest}
    for kind, r in sorted(rows, key=lambda x: x[1]["at"], reverse=True):
        name, page = f"{kind}-{r['id']}", (r["board"] or "generic", str(r["url"]).split("?")[0], r["step"] or "")
        if name in have or page in seen_pages or not str(r["url"]).startswith("http"):
            continue
        seen_pages.add(page)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{name}.html").write_text(blank(zlib.decompress(r["html"]).decode("utf-8", "replace")))
        manifest.append({"name": name, "board": page[0], "url": r["url"], "step": page[2], "file": f"{name}.html",
                         "source": "regression", "expected": None, "at": r["at"]})
    manifest = sorted(manifest, key=lambda m: m["at"], reverse=True)
    for gone in manifest[MAX_REGRESSION:]:
        (folder / gone["file"]).unlink(missing_ok=True)
    manifest = manifest[:MAX_REGRESSION]
    if manifest or (folder / "manifest.json").exists():
        _write(folder / "manifest.json", manifest)
    return [{**m, "file": str(folder / m["file"])} for m in manifest]


def board_pages() -> list[dict]:
    return [{**m, "source": "fixture", "file": str(FIXTURES / m["file"]) if m.get("file") else ""} for m in _read(BOARD_MANIFEST)]


# 1.3.1 (AUTOFILL-BUGS #7): every ATS job-hunt-agent fetches, for the real-site check on this computer (`job-hunt-agent autofill realsites`)
REAL_BOARDS = ("workday", "greenhouse", "lever", "ashby", "icims", "icims_classic", "smartrecruiters", "taleo",
               "oracle_recruiting", "successfactors", "jobvite", "bamboohr", "workable", "jazzhr", "breezy", "paylocity",
               "adp", "dayforce", "rippling", "eightfold", "phenom", "recruitee", "pinpoint", "hibob", "gem", "jobscore",
               "cornerstone")


def live_pages(conn, boards=LIVE_BOARDS) -> list[dict]:
    """The newest open job of each board with a public apply page (another posting each night)."""
    out = []
    for ats in boards:
        row = conn.execute("SELECT coalesce(apply_url, url) AS u FROM jobs WHERE uid >= ? AND uid < ? AND closed_at IS NULL "
                           "AND coalesce(apply_url, url) LIKE 'https://%' ORDER BY first_seen DESC LIMIT 1",
                           (ats + ":", ats + ";")).fetchone()
        if row:
            url = row["u"]
            if ats == "ashby" and not url.rstrip("/").endswith("/application"):     # the form is its own tab
                url = url.rstrip("/") + "/application"
            out.append({"name": f"{ats}-live", "board": ats, "url": url, "source": "live", "expected": None})
    return out


def _fake_person(home: Path) -> None:
    prof = home / "profiles" / PID
    (prof / "resumes").mkdir(parents=True)
    (prof / "profile.yaml").write_text(FAKE_PROFILE)
    (prof / "baseline.json").write_text(json.dumps(FAKE_BASELINE))
    (prof / "answers.json").write_text(json.dumps({"work_auth": "Yes", "sponsor_now": "No", "sponsor_future": "No"}))


def _node() -> str:
    found = shutil.which("node") or next((str(Path(d) / "node") for d in NODE_DIRS if (Path(d) / "node").exists()), None)
    if not found:
        raise RuntimeError("node is not installed (the canary drives Chrome with it)")
    return found


def _drive(home: Path, pages: list[dict], chrome: str, fields: list | None = None) -> list[dict]:
    """The pages in headless Chrome against a throwaway job-hunt-agent (temp database + the fake person). `fields`: filled
    with that run's per-field log (board, label, key, status, why) before the throwaway database goes."""
    from .. import server
    _fake_person(home)
    (home / "pages.json").write_text(json.dumps(pages))
    saved = (config.DB_PATH, config.PROFILES)
    config.DB_PATH, config.PROFILES = home / "canary.db", home / "profiles"
    srv = None
    try:
        conn = db.connect()                                  # made before the server's threads share it
        try:
            for key, value in FAKE_ANSWERS.items():
                bank.save(conn, PID, key, value)
        finally:
            conn.close()
        srv = server.make_server(lan=False, port=0)
        srv.token = secrets.token_hex(16)
        threading.Thread(target=srv.serve_forever, daemon=True, name="canary-serve").start()
        try:
            out = subprocess.run([_node(), str(RUNNER), f"http://127.0.0.1:{srv.server_port}", srv.token, chrome,
                                  str(home / "chrome"), str(EXTENSION), str(FIXTURES), str(home / "pages.json")],
                                 capture_output=True, text=True, timeout=RUN_TIMEOUT_S, check=False)
        except subprocess.TimeoutExpired:                    # a hung browser is a result too (Health shows it)
            return [{"name": "runner", "board": "?", "source": "runner",
                     "error": f"the check did not finish in {RUN_TIMEOUT_S // 60} minutes"}]
    finally:
        if srv:
            srv.shutdown()
            srv.server_close()
        if fields is not None and (home / "canary.db").exists():
            conn = db.connect()
            try:
                fields.extend(dict(r) for r in conn.execute("SELECT board, label, key, status, why FROM autofill_fields ORDER BY id"))
            finally:
                conn.close()
        config.DB_PATH, config.PROFILES = saved
    lines = [ln for ln in out.stdout.splitlines() if ln.startswith("{")]
    if not lines:
        return [{"name": "runner", "board": "?", "source": "runner", "error": (out.stderr or "no output")[-500:]}]
    return json.loads(lines[-1])["pages"]


def _ratchet(results: list[dict]) -> None:
    """A regression fixture expects at least its first run's fills, raised when it fills more."""
    path = regression_dir() / "manifest.json"
    manifest = _read(path)
    if not manifest:
        return
    good = {r["name"]: r["filled"] for r in results
            if r.get("source") == "regression" and not r.get("error") and not r.get("skip") and r.get("fields")}
    _write(path, [{**m, "expected": max(m.get("expected") or 0, good[m["name"]])} if m["name"] in good else m for m in manifest])


def run(conn, *, live: bool = True, chrome: str = CHROME) -> dict:
    """One canary run: fixtures (board + regression) and, unless live=False, the live pages. Results are recorded."""
    run_at = db.iso(db.utcnow())
    pages = board_pages() + export(conn) + (live_pages(conn) if live else [])
    with tempfile.TemporaryDirectory(prefix="jh-canary-") as tmp:
        results = _drive(Path(tmp), pages, chrome)
    drops = health.record_canary(conn, run_at, results)
    _ratchet(results)
    return {"run_at": run_at, "pages": results, "drops": drops}


def realsites(conn, *, chrome: str = CHROME, urls=()) -> dict:
    """1.3.1 (AUTOFILL-BUGS #7): one real public apply page per ATS (the newest open posting job-hunt-agent has, or `urls`),
    filled once as the canary's fake person: nothing but reads leave the browser (never Submit, never an account
    made), and the result is per field. Nothing is recorded in job-hunt-agent's database. Returns {pages, fields, markdown}."""
    pages = live_pages(conn, REAL_BOARDS) + [{"name": f"url-{i + 1}", "board": "url", "url": u, "source": "live", "expected": None}
                                             for i, u in enumerate(urls)]
    fields: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="jh-realsites-") as tmp:
        results = _drive(Path(tmp), pages, chrome, fields)
    lines = ["| Site | Page | Filled | Review | Needs you | Failed | Problems |", "|---|---|---|---|---|---|---|"]
    for page in results:
        mine = [f for f in fields if f["board"] == page.get("board") or page.get("board") == "url"]
        n = {k: sum(1 for f in mine if f["status"] == k) for k in ("filled", "review", "needs", "failed")}
        bad = "; ".join(f"{f['label'][:40]}: {f['why']}" for f in mine if f["status"] == "failed")[:300]
        lines.append(f"| {page.get('board')} | {str(page.get('url', ''))[:60]} | {n['filled']} | {n['review']} | {n['needs']} | "
                     f"{n['failed']} | {page.get('error') or page.get('skip') or bad or '–'} |")
    return {"pages": results, "fields": fields, "markdown": "\n".join(lines)}


def wait_quiet(fetching=None, sleep=time.sleep) -> bool:
    """Waits (at most QUIET_WAIT_S) while a fetch runs; True when it is quiet."""
    from .. import ops
    fetching = fetching or (lambda: bool(ops.fetch_state().get("running")))
    for _ in range(QUIET_WAIT_S // QUIET_POLL_S):
        if not fetching():
            return True
        sleep(QUIET_POLL_S)
    return not fetching()
