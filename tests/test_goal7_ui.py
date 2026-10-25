"""GOAL7 §5 UI tests in a real browser: keyboard shortcuts, optimistic actions with Undo (and rollback), filter chips.
A throwaway DB with three jobs, the real server, headless Chrome driven by tests/ui/goal7_ui.mjs (no packages).
Skipped when node or Chrome is not installed."""
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from job_hunt_agent import config, db, rgclient, server, store, trackapi
from job_hunt_agent.ats.common import FetchResult, make_job

NOW = datetime.now(timezone.utc).replace(microsecond=0)
CHROME = os.environ.get("JH_CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
RUNNER = Path(__file__).parent / "ui" / "goal7_ui.mjs"
TOKEN = "ui-test-token"
COMPANIES = ("Acme", "Globex", "Initech")
TRACKER_ROWS = 1200                 # the table stays smooth with 1,000+ rows
RUN_TIMEOUT_S = 180


def seed(conn) -> None:
    boards = [{"board": f"lever:{c.lower()}", "ats": "lever", "key": c.lower(), "company": c} for c in COMPANIES]
    db.sync_boards(conn, boards)
    run = db.start_run(conn, "test", NOW)
    for i, company in enumerate(COMPANIES):
        item = make_job(ats="lever", key=company.lower(), ext_id=str(i), company=company, title="Machine Learning Engineer",
                        location="Austin, TX", url=f"https://jobs.lever.co/{company.lower()}/{i}",
                        posted_at=db.iso(NOW - timedelta(minutes=10 * (i + 1))),
                        description="Requirements:\nPython, PyTorch, Kubernetes and AWS.")
        store.write_board(conn, db.board(conn, f"lever:{company.lower()}"), FetchResult([item], 200, n_raw=1), "ok", run,
                          NOW, ["fam:ml_engineer"], 5)
    for i in range(TRACKER_ROWS):
        trackapi.create(conn, "alex", {"company": f"Company {i}", "role_title": "ML Engineer", "status": "applied",
                                        "date_applied": "2026-09-01", "notes": f"note {i}"})
    conn.commit()


@unittest.skipUnless(shutil.which("node") and Path(CHROME).exists(), "needs node and Google Chrome")
class BrowserUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.patch = mock.patch.object(config, "DB_PATH", Path(cls.tmp.name) / "ui.db")
        cls.patch.start()
        cls.offline = mock.patch.object(rgclient, "_base", lambda: "http://127.0.0.1:9")   # no resumegen here
        cls.offline.start()
        conn = db.connect()
        seed(conn)
        conn.close()
        cls.srv = server.make_server(lan=False, port=0)
        cls.srv.token = TOKEN
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        base = f"http://localhost:{cls.srv.server_port}"
        out = subprocess.run(["node", str(RUNNER), base, TOKEN, CHROME, str(Path(cls.tmp.name) / "chrome")],
                             capture_output=True, text=True, timeout=RUN_TIMEOUT_S)
        lines = [ln for ln in out.stdout.splitlines() if ln.startswith("{")]
        cls.checks = json.loads(lines[-1])["checks"] if lines else [{"name": "runner", "ok": False, "detail": out.stderr}]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.offline.stop()
        cls.patch.stop()
        cls.tmp.cleanup()

    def assert_group(self, names):
        by_name = {c["name"]: c for c in self.checks}
        runner = by_name.get("runner")
        self.assertIsNone(runner, runner and runner["detail"])
        for name in names:
            with self.subTest(name):
                self.assertIn(name, by_name, "check did not run")
                self.assertTrue(by_name[name]["ok"], by_name[name]["detail"])

    def test_keyboard_shortcuts_and_palette(self):
        self.assert_group(["feed loads", "the first Tab goes to the skip link", "j selects the next job", "k selects the previous job", "↓ moves like j",
                           "? opens the keyboard sheet", "Esc closes it", "2 switches to Saved",
                           "1 switches back to Recommended", "⌘K opens the palette", "palette jumps to Tracker",
                           "Enter opens the job in the detail pane"])

    def test_optimistic_actions_undo_and_rollback(self):
        self.assert_group(["s marks the row Saved at once", "the server has it saved", "the toast offers Undo",
                           "Undo un-saves it on the server", "Undo puts the row back", "x removes the row at once",
                           "the toast asks why", "Undo for not interested", "the row is back",
                           "the server forgot the pass", "a refused save rolls back with a message"])

    def test_filter_chips(self):
        self.assert_group(["a filter chip opens its popover", "the choice shows as an active chip",
                           "it is not stored for the next open", "× removes the chip",
                           "the toast offers Undo for the filter", "Undo brings the filter back",
                           "Posted is one control", "More lists every extra filter", "a More choice becomes an active chip"])

    def test_tracker_table(self):
        self.assert_group(["5 opens the tracker table", "only the rows near the view are drawn",
                           "scrolling draws the rows further down", "scrolling 1000+ rows stays smooth",
                           "the header stays put", "the first column stays put", "Move to… changes the status at once",
                           "the server has the new status", "Undo moves it back", "a column can be hidden",
                           "hidden columns are remembered", "a column can be resized and the width is remembered",
                           "a click edits the cell in place", "Enter saves the cell", "Undo restores the cell",
                           "no page errors"])


STATIC = Path(__file__).parents[1] / "job_hunt_agent" / "static"
JS_BUDGET = 120_000 + 1_000         # JS ≤ 121 KB, CSS ≤ 40 KB uncompressed, ≤ 2 font files
# +1,000 at startup: the first-run Setup route, the "#settings/setup" return from Google and the product name in
# app.js / core.js / feed.js, on top of GOAL35's address state (urlstate.js, find.js)
CSS_BUDGET = 40_000
FONT_FILES = 2
# GOAL8 (user decision 2026-10-07 "Lazy-load new parts"): the budget above holds for what loads at startup (app.js and
# its static imports + sw.js, app.css); modules imported on first use (stats, GOAL8's rg/back) and rg.css may add to it
# user decision 2026-10-08 "keep the 185 KB lazy JavaScript cap" (GOAL8 said ~145 KB; GOAL9 and resumegen GOAL3 grew the
# lazy modules, then comments were trimmed to fit): new JS must fit inside it; startup cap unchanged
# GOAL19 (user 2026-10-12 "raise JS_TOTAL_BUDGET only by what the grid needs"): +58,200 for the Sheets-like Tracker
# grid's five lazy modules (grid, gridcss, gridedit, gridops, gridmenu = 58,135 B), fetched only on the Tracker tab
# GOAL20 (user 23:37 "do everything properly"): +4,000 for the audit fixes (answers editor, busy states, archive sync),
# all in lazy modules; the startup cap is unchanged
# GOAL21 (user 23:35 "make the app feel premium"): +11,000 for the Settings page (settings.js, lazy) and the side menu /
# status button / phone sections in app.js; +2,000 for the job list (24 h line, duplicates link, labelled job actions,
# the posted-pay chip); +1,000 for the resume panel's names / needs-you count and the Tracker's Status column (lazy
# rg.js, rq.js, grid.js), +1,000 for edges.js (lazy: the phone's edge fade on sideways-scrolling rows); the startup
# cap is unchanged; +1,000 for the after-test fixes (the Applied? line cleared on Mark applied, Duplicates only ends
# with its list, the Tracker's stray null); +1,000 for the verifier fixes (Already applied, the phone's follow-ups
# badge, empty-list words, the Stats skeleton); +1,000 for the grid fitting the screen and spoken names on "×" buttons; +1,000 for the verifier's last fixes
# (no key hints on a phone, one primary per screen); GOAL22 (user 2026-10-13 "Generate resume never fails on a
# location"): +2,500 for the city picker in rg.js (lazy)
# GOAL23 §4/§6 (GOAL23.md "new Answers page", "Get the Autofill extension"): +11,800 for answers.js (lazy, only on
# the Answers tab) and the Settings › Autofill extension section (lazy settings.js); the startup cap is unchanged
# GOAL24 §3-§6 (H-1B badges on the feed, job panel, Tracker and phone, the Recommended switch, Load
# more, the Answers-page setting): +5,000 (sponsor.js 3.4 KB at startup, paid for by trimmed comments; the rest in lazy
# grid / tracker / answers); the startup cap is unchanged
# GOAL25 §1/§3/§4/§5 (the Answers page's Learned / company answers / saved long answers / Stats /
# choice lists, Settings › Autofill › Health, Apply on laptop): +21,000, all lazy (afmore.js 5.8 KB and answers.js on
# the Answers tab, afhealth.js 5.5 KB on Settings and after the first paint, applyq.js 7.4 KB after the first paint);
# the startup cap is unchanged (the queue hooks in through the lazy Answers badge, not app.js / detail.js)
# Settings > Setup and Profiles (setup.js, lazy, about 10 KB) and the outreach "Draft with Claude" box: +11,000
# Autofill Health's per-field table in the lazy afhealth.js: +1,000; the startup cap is unchanged
# Applied as cards (applied.js, lazy, only on the Applied tab, with its own few styles): +4,500
# the three who-to-contact cards in finder.js and the contact's kind in rowedit.js (both lazy): +2,500
JS_TOTAL_BUDGET = 185_000 + 58_200 + 4_000 + 11_000 + 2_000 + 1_000 + 1_000 + 1_000 + 1_000 + 1_000 + 1_000 + 2_500 + 11_800 \
    + 5_000 + 21_000 + 11_000 + 1_000 + 4_500 + 2_500 + 6_400 + 1_000 + 4_200 + 1_400 + 2_500 + 1_500 + 20_000 + 1_500 + 1_000 \
    + 1_500 + 1_000
# the Offer check panel and the job page's Application questions box (offer.js, appq.js, both lazy): +6,400
# the Resumes tab's empty-view words and the poll's backoff in the lazy rq.js / rg.js: +1,000
# each list and page keeping its last data, placeholders and Could not load + Retry: +4,200 (tracker.js, 19 KB, now
# loads on the first Tracker visit)
# core.js's postedText ("Posted today, found 9:03 AM") and the card / job pane using it: +1,400
# live.js's resume-service push, the ring's words, the reload rule, the open job kept for a reload and Saved counting
# its outside jobs: +2,500
# the empty views saying what fills them, Clear filters, the follow-up Undo, key 0 and the status shapes: +1,500
# urlstate.js (every page's filters, sort, tab, search, person and job in the address), find.js (the page's twin of
# the shared search + highlighting), the feed / Answers search boxes, the ⌘K box's server search, the Tracker's
# found-by-status line and empty states: +20,000
# the W2 / vague repost / "via" tags, the not-W2 footer count and Settings > Job sources: +1,500
# Settings > New jobs (lazy settings.js): +1,000
# the Tracker's "N more where only the JD mentions it: show" toggle, ⌘K's job-list order and its "No open X jobs"
# line, and the grid's column sort / filters kept in the address (lazy tracker.js, palette.js, gridmenu.js): +1,500
# the public build's startup +1,000 (see JS_BUDGET) counts here too: +1,000
LAZY_CSS_BUDGET = 8_000 + 1_000 + 1_300         # GOAL25 §1/§3: answers.css (Learned, company answers, Stats bars)
# +1,300 for offer.css (Offer check grades, Application questions) and rg.css's "Mirrored from JD" tags
# ds.css holds the tokens and shared parts (buttons, chips, tags, menus, dialogs, toasts) and is byte-identical
# in resumegen, so the two apps look like one product. It loads at startup with app.css: both count against one cap,
# raised from 40,000 (app.css alone) by the 12,000 GOAL21 adds (the shell grid, Settings page, status popover), +1,000
# for the phone's edge fade on sideways-scrolling rows (W15), +1,000 for the phone bar's focus ring kept on screen;
# mail.css (1.2 KB) now loads at startup too, so the email box never shows unstyled: +1,500, and it leaves the lazy cap
# GOAL24: +1,500 for the H-1B badges, the Recommended switch and the colour-blind-safe tokens
# contrast for colour-blind users: +1,000 so every control reads at 4.5:1 in every state
STARTUP_CSS = ("app.css", "ds.css", "mail.css")
# Settings > Setup's status panel and state pills: +1,500
# the placeholders, Could not load + Retry, the tab fade-in and the filters keeping their room: +500
CSS_BUDGET_GOAL21 = CSS_BUDGET + 12_000 + 1_000 + 1_000 + 1_500 + 1_500 + 1_000 + 1_500 + 500
_IMPORT = re.compile(r'^import [^;]*? from "\./([a-z0-9]+\.js)";', re.M)


def startup_modules() -> set[str]:
    """app.js and every module it imports statically (dynamic import() is not followed)."""
    seen, todo = set(), ["app.js"]
    while todo:
        name = todo.pop()
        if name not in seen:
            seen.add(name)
            todo += _IMPORT.findall((STATIC / "js" / name).read_text())
    return seen


class BudgetTest(unittest.TestCase):
    def test_the_ui_stays_inside_the_size_budget(self):
        startup = sum((STATIC / "js" / n).stat().st_size for n in startup_modules()) + (STATIC / "sw.js").stat().st_size
        total = sum(p.stat().st_size for p in [*(STATIC / "js").glob("*.js"), STATIC / "sw.js"])
        self.assertLessEqual(startup, JS_BUDGET)
        self.assertLessEqual(total, JS_TOTAL_BUDGET)
        self.assertLessEqual(sum((STATIC / n).stat().st_size for n in STARTUP_CSS), CSS_BUDGET_GOAL21)
        lazy_css = sum(p.stat().st_size for p in STATIC.glob("*.css") if p.name not in STARTUP_CSS)
        self.assertLessEqual(lazy_css, LAZY_CSS_BUDGET)
        self.assertNotIn("rg.js", startup_modules())
        self.assertNotIn("stats.js", startup_modules())
        self.assertLessEqual(len(list((STATIC / "fonts").glob("*.woff2"))), FONT_FILES)
        page = (STATIC / "index.html").read_text()
        self.assertIn('rel="preload" href="/fonts/schibsted-grotesk.woff2"', page)
        self.assertRegex((STATIC / "app.css").read_text(), r"font-display: (swap|optional)")   # GOAL31: optional, no late reflow


@unittest.skipUnless(shutil.which("node"), "needs node")
class ModuleSyntaxTest(unittest.TestCase):
    def test_every_ui_module_parses_as_an_es_module(self):
        js_dir = STATIC / "js"
        with tempfile.TemporaryDirectory() as tmp:
            for src in sorted(js_dir.glob("*.js")):
                copy = Path(tmp) / f"{src.stem}.mjs"            # .mjs: node parses it as a module, like the browser
                copy.write_text(src.read_text())
                out = subprocess.run(["node", "--check", str(copy)], capture_output=True, text=True)
                with self.subTest(src.name):
                    self.assertEqual(out.returncode, 0, out.stderr)


if __name__ == "__main__":
    unittest.main()
