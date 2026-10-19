"""GOAL9 area A (top bar, tabs, filters) in headless Chrome (tests/ui/goal9_a.mjs) against a throwaway DB and a
synthetic profile: every filter menu applies on one real click / tap within 150 ms, the list equals the server's
answer, the chip shows the choice, focus stays on the chip, single menus close on pick, More stays open with live
ticks, Esc / outside click / scroll close without undoing, the phone sort segment, the drift banner dismiss, tabs,
the profile menu, the theme, ⌘K, and the §6 hover labels (every control labelled, hover, keyboard focus, long press).
The seed covers every menu: three families, three states, three workplaces, pay from none to $220k, ages from
10 minutes to 5 days, a too-senior job, a contract job, a startup board and a big-company board."""
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

import docx

from job_hunt_agent import config, db, health, server, store
from job_hunt_agent.ats.common import FetchResult, make_job
from tests.test_goal7_ui import CHROME

NOW = db.utcnow().replace(microsecond=0)
RUNNER = Path(__file__).parent / "ui" / "goal9_a.mjs"
TOKEN = "ui9a-test-token"
RUN_TIMEOUT_S = 240
PID = "tester"
PROFILE = """name: Tess Tester
families: [ml_engineer, ai_engineer, data_engineer]
years: 4
max_required_years: 4
feed_min_match: 0
resumes:
  - {id: ml, label: ML Engineer, file: Tess_ML.docx, families: [ml_engineer, ai_engineer, data_engineer]}
"""
DRIFT = ["lever: salary share 20% (was 60%)"]
BOARDS = [{"board": "lever:acme", "ats": "lever", "key": "acme", "company": "Acme", "segment": "startup"},
          {"board": "workday:amazon", "ats": "workday", "key": "amazon", "company": "Amazon", "segment": "big"}]
# board, title, location, workplace, (min, max) pay, hours ago, the JD's years line, employment
JOBS = (("lever:acme", "Machine Learning Engineer", "Austin, TX", "remote", (150000, 190000), 2,
         "3+ years of experience building ML systems in Python and PyTorch on Kubernetes.", None),
        ("lever:acme", "Senior Machine Learning Engineer", "Austin, TX", "onsite", (200000, 240000), 120,
         "6+ years of experience with Python, PyTorch and Kubernetes on AWS.", None),
        ("lever:acme", "AI Engineer", "New York, NY", "hybrid", None, 30,
         "2+ years of experience with Python, LLMs and RAG systems in production on AWS.", None),
        ("lever:acme", "Data Engineer", "Seattle, WA", "remote", (100000, 130000), 0.2,
         "3+ years with Spark, Airflow, Python and AWS pipelines.", "contract"),
        ("lever:acme", "ML Platform Engineer", "Austin, TX", "onsite", (120000, 140000), 48,
         "Kubernetes, MLflow, Python and AWS for model serving at scale.", None),
        ("workday:amazon", "Applied AI Engineer", "Seattle, WA", "onsite", (180000, 220000), 1,
         "2+ years with Python, LLM evaluation and RAG pipelines on AWS.", None))


def seed(conn) -> None:
    db.sync_boards(conn, BOARDS)
    run = db.start_run(conn, "test", NOW)
    scopes = ["fam:ml_engineer", "fam:ai_engineer", "fam:data_engineer"]
    for board in BOARDS:
        ats, key = board["ats"], board["key"]
        items = [make_job(ats=ats, key=key, ext_id=str(i), company=board["company"], title=title, location=loc,
                          workplace=wp, salary=(*pay, "USD") if pay else None, employment=emp,
                          url=f"http://127.0.0.1:9/{key}/{i}", posted_at=db.iso(NOW - timedelta(hours=age)),
                          description="About the role.\nRequirements:\n- " + line + "\n" + "We ship ML products. " * 8)
                 for i, (b, title, loc, wp, pay, age, line, emp) in enumerate(JOBS) if b == board["board"]]
        store.write_board(conn, db.board(conn, board["board"]), FetchResult(items, 200, n_raw=len(items)), "ok", run,
                          NOW, scopes, 5)
    conn.commit()


@unittest.skipUnless(shutil.which("node") and Path(CHROME).exists(), "needs node and Google Chrome")
class Goal9ABrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        resumes = root / "profiles" / PID / "resumes"
        resumes.mkdir(parents=True)
        (root / "profiles" / PID / "profile.yaml").write_text(PROFILE)
        (resumes / "Tess_ML.pdf").write_bytes(b"%PDF-1.4 base")
        doc = docx.Document()
        doc.add_paragraph("Tess Tester. ML Engineer. Python, PyTorch, Kubernetes, AWS, LLMs, RAG. 2021 - 2025.")
        doc.save(resumes / "Tess_ML.docx")
        cls.patches = [mock.patch.object(config, "DB_PATH", root / "ui.db"),
                       mock.patch.object(config, "PROFILES", root / "profiles"),
                       mock.patch.object(health, "drift", return_value=list(DRIFT))]
        for p in cls.patches:
            p.start()
        conn = db.connect()
        seed(conn)
        conn.close()
        cls.srv = server.make_server(lan=False, port=0)
        cls.srv.token = TOKEN
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        out = subprocess.run(["node", str(RUNNER), f"http://localhost:{cls.srv.server_port}", TOKEN, CHROME,
                              str(root / "chrome")], capture_output=True, text=True, timeout=RUN_TIMEOUT_S)
        lines = [ln for ln in out.stdout.splitlines() if ln.startswith("{")]
        cls.checks = json.loads(lines[-1])["checks"] if lines else [{"name": "runner", "ok": False, "detail": out.stderr}]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
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

    def named(self, *parts):
        return [c["name"] for c in self.checks if all(p in c["name"] for p in parts)]

    def test_every_browser_check_passed(self):
        self.assert_group([c["name"] for c in self.checks])

    def test_every_menu_applies_on_one_click_desktop_and_phone(self):
        for menu in ("Role", "Location", "Workplace", "Match", "Posted pay", "Posted 24h", "Best match", "Newest", "More"):
            for tag in ("", "phone "):
                names = [n for n in self.named(f"{tag}{menu}:", "one click applies") if n.startswith("phone ") == bool(tag)]
                self.assertTrue(names, f"no one-click check for {tag}{menu}")
                self.assert_group(names)
        self.assert_group(["feed loads", "Hide applied: the list matches the server",
                           "Hide applied: the menu stays open with the tick", "phone: the sort segment is in the strip"])

    def test_menus_close_and_keep_the_choice(self):
        self.assert_group(self.named("closes on pick") + self.named("Esc closes without undoing")
                          + self.named("outside click closes without undoing") + self.named("scroll that moves the chip closes")
                          + self.named("a list scroll leaves it open")
                          + self.named("opening another menu closes the first") + self.named("stays open with the tick")
                          + self.named("a second tick updates live") + self.named("clicking the chip again closes it")
                          + self.named("it reopens with the ticks"))

    def test_chips_counts_focus_and_layout(self):
        self.assert_group(self.named("the chip shows the choice") + self.named("the list matches the server")
                          + self.named("the head counts it") + self.named("never shows a stale count")
                          + self.named("focus stays") + self.named("× removes it") + self.named("Clear all empties it")
                          + ["no chip hides behind the sort segment"])

    def test_tabs_profile_theme_palette_and_banner(self):
        self.assert_group(["tabs follow the number keys", "arrow keys move between tabs", "the profile menu opens",
                           "Esc closes the profile menu", "the theme toggles on one click", "⌘K opens the palette",
                           "⌘K jumps to a tab", "the drift notice shows in the status", "Hide until it changes hides it at once",
                           "the dismissal is remembered", "it comes back when the drift changes",
                           "every top tab is in view at 1440 px", "every top tab is in view at 1280 px",
                           "every top tab is in view at 1024 px", "every top tab is in view at 800 px",
                           "the profile button keeps its name when the name is hidden",
                           "phone: the More tab menu opens", "phone: a tap outside closes the More tab menu",
                           "phone: Esc closes the More tab menu", "phone: picking a view in the section strip switches"])

    def test_hover_labels(self):
        self.assert_group(self.named("has a hover label") + ["hovering a chip shows its label",
                          "hovering Best match shows its label", "keyboard focus on a chip shows its label",
                          "phone: a long press shows the label", "phone: lifting the finger hides it",
                          "no page errors (desktop)", "no page errors"])


if __name__ == "__main__":
    unittest.main()
