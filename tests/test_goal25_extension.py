"""GOAL25 (user 2026-10-14 17:06) with the real extension in headless Chrome against a real job-hunt-agent server (throwaway
DB, the fake person, fake resumegen): answers the user fills by hand on a real Greenhouse page are learned and fill the same
questions on a real Lever page and a Workday step; sponsorship changed by hand is that company's only; long text is a
snippet offered in the panel; Undo; nothing is lost when the tab closes; the docked panel (job, resume, address,
sponsorship slot, profile, grouped fields, edit from the panel, collapse / float per site, narrow windows, the page's
own buttons never covered); a forced adapter error is reported and the page still fills; job-hunt-agent asleep; the update
notice; color-blind simulation screenshots. Driven by tests/ui/goal25_extension.mjs. Skipped without node or Chrome."""
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import unittest
import zlib
from pathlib import Path
from unittest import mock

from job_hunt_agent import config, db, rgclient, server, store
from job_hunt_agent.ats.common import FetchResult, make_job
from job_hunt_agent.autofill import address, bank, draft, package
from tests import test_goal8_flow as g8
from tests.test_goal23_extension import BASELINE, CHROME, DIFF, PROFILE

ROOT = Path(__file__).resolve().parent.parent
RUNNER = Path(__file__).parent / "ui" / "goal25_extension.mjs"
FIXTURES = Path(__file__).parent / "fixtures" / "autofill"
SHOTS = ROOT / "Prompts" / "ux25"
TOKEN = "ext25-test-token"
PID = "zztest"
RUN_TIMEOUT_S = 600
NEWER = "1.2.2"
JOBS = (("greenhouse", "doordashusa", "3623888", "DoorDash", "Shift Lead", "https://job-boards.greenhouse.io/doordashusa/jobs/3623888"),
        ("lever", "shieldai", "2394368a-67ff-441c-83c3-e998446327e7", "Shield AI", "Applications Engineer",
         "https://jobs.lever.co/shieldai/2394368a-67ff-441c-83c3-e998446327e7"),
        ("workday", "zztest/wd5/External", "JR0001", "ZZ Test Corp", "AI Engineer",
         "https://zztest.wd5.myworkdayjobs.com/en-US/External/job/Foster-City-CA/AI-Engineer_JR0001"))


def _add_job(conn, fake, ats, key, ext_id, company, title, url):
    item = make_job(ats=ats, key=key, ext_id=ext_id, company=company, title=title, location="Foster City, CA", url=url,
                    posted_at=db.iso(db.utcnow()), description=f"{title}. Python.")
    board = f"{ats}:{key}"
    db.sync_boards(conn, [{"board": board, "ats": ats, "key": key, "company": company}])
    run = db.start_run(conn, "test", db.utcnow())
    store.write_board(conn, db.board(conn, board), FetchResult([item], 200, n_raw=1), "ok", run, db.utcnow(),
                      ["fam:ai_engineer"], 5)
    rg = fake.add(PID, company, title, "JD", source_job_id=item["uid"], status="ready", location="Foster City, CA",
                  workplace="onsite", result={"base": {"id": "ai", "label": "AI Engineer"}, "diff": DIFF},
                  drive_file_id="F1", times={"made": db.iso(db.utcnow()), "approved": db.iso(db.utcnow())})
    fake.files[(rg, "pdf")] = (b"%PDF-1.4\n% ZZ_TEST fake resume\n" + b"0" * 900, "application/pdf")
    return item["uid"]


@unittest.skipUnless(shutil.which("node") and Path(CHROME).exists(), "needs node and Google Chrome")
class Goal25ExtensionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls.tmp.name)
        prof = tmp / "profiles" / PID
        (prof / "resumes").mkdir(parents=True)
        (prof / "profile.yaml").write_text(PROFILE)
        (prof / "baseline.json").write_text(json.dumps(BASELINE))
        (prof / "answers.json").write_text("{}")
        cls.fake = g8.FakeResumegen()
        cls.patches = [mock.patch.object(config, "DB_PATH", tmp / "ext.db"), mock.patch.object(config, "PROFILES", tmp / "profiles"),
                       mock.patch.object(rgclient, "_base", lambda: cls.fake.url), mock.patch.object(rgclient, "_token", lambda: g8.TOKEN),
                       mock.patch.object(draft, "run", lambda prompt, pid: "Draft."),
                       mock.patch.object(package, "version", lambda: NEWER)]                  # job-hunt-agent has a newer extension
        for p in cls.patches:
            p.start()
        conn = db.connect()
        cls.uids = {job[0]: _add_job(conn, cls.fake, *job) for job in JOBS}
        bank.ensure(conn, PID)
        for key, value in {"work_auth": "Yes", "sponsor_now": "No", "sponsor_future": "No", "age18": "Yes",
                           "address1": "11 Test Street", "zip": "78701"}.items():
            bank.save(conn, PID, key, value)
        conn.execute("INSERT INTO osm_places(city_state, candidates, fetched_at) VALUES (?,?,?)",
                     ("Foster City, CA", json.dumps([{"street": "808 Comet Dr", "zip": "94404", "name": "Kula Kai", "own_zip": True}]),
                      db.iso(db.utcnow())))
        conn.commit()
        address.use(conn, PID, "Foster City, CA")
        conn.close()
        cls.srv = server.make_server(lan=False, port=0)
        cls.srv.token = TOKEN
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{cls.srv.server_port}"
        SHOTS.mkdir(parents=True, exist_ok=True)
        out = subprocess.run(["node", str(RUNNER), base, TOKEN, CHROME, str(tmp / "chrome"), str(ROOT / "extension"), str(FIXTURES),
                              str(SHOTS)], capture_output=True, text=True, timeout=RUN_TIMEOUT_S,
                             env={**os.environ, "JH_NEWER": NEWER})
        cls.stderr = out.stderr
        lines = [ln for ln in out.stdout.splitlines() if ln.startswith("{")]
        cls.checks = json.loads(lines[-1])["checks"] if lines else [{"name": "runner", "ok": False, "detail": out.stderr[-3000:]}]
        cls.db_bytes = (tmp / "ext.db").read_bytes()
        conn = db.connect()
        cls.answers = {r["key"]: r["answer"] for r in conn.execute("SELECT key, answer FROM autofill_answers WHERE profile = ?", (PID,))}
        cls.overrides = [dict(r) for r in conn.execute("SELECT company_name, key, answer FROM autofill_overrides")]
        cls.errors = [dict(r) for r in conn.execute("SELECT board, kind, message, html FROM autofill_errors")]
        cls.snippets = [dict(r) for r in conn.execute("SELECT question, text, company FROM autofill_snippets")]
        conn.close()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.fake.stop()
        for p in reversed(cls.patches):
            p.stop()
        cls.tmp.cleanup()

    def assert_group(self, names):
        by_name = {c["name"]: c for c in self.checks}
        runner = by_name.get("runner")
        self.assertIsNone(runner, runner and runner["detail"])
        for name in names:
            with self.subTest(name):
                self.assertIn(name, by_name, "check did not run")
                self.assertTrue(by_name[name]["ok"], by_name[name]["detail"])

    def test_learned_on_greenhouse_fills_lever_and_workday(self):
        self.assert_group(["Greenhouse: the panel docks with the job, resume, address, sponsorship slot and profile",
                           "Greenhouse: Autofill fills what it knows", "Greenhouse: the field list is grouped with icons and words",
                           "a hand-typed answer shows under Yours", "an edit in the panel fills the page field",
                           "leaving the page says Saved for next time", "the company-only answer says so",
                           "the long answer is kept with the application",
                           "Lever: the notice period learned on Greenhouse fills", "Lever: the team size picked in the panel fills",
                           "Lever: sponsorship stays the usual answer at another company",
                           "Lever: the long answer is offered, not filled", "Lever: Use a saved answer fills the box",
                           "Workday: the learned answer fills a Workday question", "Undo in the panel removes what was learned",
                           "a closed tab loses nothing"])
        self.assertEqual(self.answers.get("q:preferred team size"), "6-20")
        self.assertEqual(self.answers.get("sponsor_future"), "No")                          # the global answer stayed
        self.assertIn({"company_name": "DoorDash", "key": "sponsor_future", "answer": "Yes"}, self.overrides)
        self.assertEqual([s["company"] for s in self.snippets], ["DoorDash"])
        self.assertEqual(self.answers.get("notice"), "3 weeks")                               # typed, tab closed, still saved

    def test_secrets_never_reach_the_app(self):
        self.assert_group(["the SSN typed by hand is not learned"])
        self.assertNotIn(b"123-45-6789", self.db_bytes)
        for err in self.errors:                         # the error snapshot keeps the form, never a typed value
            html = zlib.decompress(err["html"]) if err["html"] else b""
            self.assertNotIn(b"123-45-6789", html)
            self.assertNotIn(b"ZZ-DOB-1990", html)

    def test_only_job_applications_teach_it(self):
        self.assert_group(["a form that is not a job application is not learned"])
        self.assertNotIn(b"ZZ Newsletter Name", self.db_bytes)

    def test_the_docked_panel(self):
        self.assert_group(["docked on the right, 340 px, the page moved over", "the page's own buttons are not under the panel",
                           "Workday's fixed Next bar stays clear", "collapse gives a slim tab and gives the page its width back",
                           "the tab opens it again", "float mode is a draggable box", "float is remembered for this site",
                           "a narrow window opens it collapsed", "the panel shows the extension version",
                           "a newer extension in job-hunt-agent shows the update notice", "clicking a row scrolls to and highlights the field"])

    def test_production_guards(self):
        self.assert_group(["a forced adapter error does not break the page", "the error is reported to job-hunt-agent with the page",
                           "one filler per field: a second filler waits its turn out",
                           "job-hunt-agent asleep: the panel says so before filling"])
        forced = [e for e in self.errors if "forced adapter error" in e["message"]]
        self.assertTrue(forced and forced[0]["board"] == "greenhouse" and forced[0]["html"], self.errors)

    def test_color_blind_safe(self):
        self.assert_group(["every status has an icon and a word", "simulation screenshots saved"])
        for name in ("panel-normal.png", "panel-deuteranopia.png", "panel-protanopia.png"):
            self.assertGreater((SHOTS / name).stat().st_size, 10_000, name)
        self.assertEqual(color_blind_clashes(), [])


# ---- the panel's status colors under color-vision deficiency (Machado, Oliveira & Fernandes 2009, severity 1.0) -----
MACHADO = {"protanopia": ((0.152286, 1.052583, -0.204868), (0.114503, 0.786281, 0.099216), (-0.003882, -0.048116, 1.051998)),
           "deuteranopia": ((0.367322, 0.860646, -0.227968), (0.280085, 0.672501, 0.047413), (-0.011820, 0.042940, 0.968881))}
STATUS_VARS = (("needs", "--orange"), ("filled", "--blue"), ("yours", "--grey"), ("bad", "--red"))
MIN_DELTA_E = 20


def _lin(c):
    c /= 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _lab(rgb):
    r, g, b = rgb
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883
    f = lambda t: t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116
    return 116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z))


def _simulate(hexcolor, matrix):
    rgb = [_lin(int(hexcolor[i:i + 2], 16)) for i in (1, 3, 5)]
    return [max(0.0, min(1.0, sum(m * c for m, c in zip(row, rgb)))) for row in matrix]


def color_blind_clashes() -> list[str]:
    """Pairs of panel status colors (text, light and dark theme) closer than ΔE 20 for a protan or deutan eye."""
    css = (ROOT / "extension" / "content" / "panel.js").read_text()
    light, dark = css.split("@media (prefers-color-scheme: dark)", 1)
    clashes = []
    for theme, text in (("light", light), ("dark", dark)):
        colors = {s: re.search(rf"{v}: (#[0-9a-f]{{6}})", text).group(1) for s, v in STATUS_VARS}
        for eye, matrix in MACHADO.items():
            seen = {s: _lab(_simulate(c, matrix)) for s, c in colors.items()}
            names = list(seen)
            for i, a in enumerate(names):
                for b in names[i + 1:]:
                    delta = sum((p - q) ** 2 for p, q in zip(seen[a], seen[b])) ** 0.5
                    if delta < MIN_DELTA_E:
                        clashes.append(f"{theme} {eye}: {a} vs {b} ΔE {delta:.1f}")
    return clashes


class ColorMathTest(unittest.TestCase):
    def test_the_simulation_tells_red_and_green_apart_badly(self):
        red, green = _lab(_simulate("#d62728", MACHADO["deuteranopia"])), _lab(_simulate("#2ca02c", MACHADO["deuteranopia"]))
        self.assertLess(sum((p - q) ** 2 for p, q in zip(red, green)) ** 0.5, 30)      # the reason for the rule

    def test_the_panel_colors_stay_apart(self):
        self.assertEqual(color_blind_clashes(), [])
