"""GOAL19 §1 in headless Chrome (tests/ui/goal19_ui.mjs): the Tracker grid against the Sheets checklist, for both
people, on a throwaway DB: frozen header / columns, row numbers, fixed rows, 1,200 rows virtualized, selection and
every key, typing / Enter / F2 / double-click editing, Esc, clipboard TSV both ways (a real Google Sheets copy with a
quoted multi-line cell), Delete, fill handle, undo / redo, dropdown chips, typed dates, long text, links, the Drive
file chip, sort / filter / hide, ⌘F, right-click insert / delete with Undo, Saved, conflicts, resize remembered,
day groups, the Sheet's colors; and the phone's card list. Downloads (none expected) go to the temp dir."""
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from job_hunt_agent import config, db, rgclient, server, trackapi
from job_hunt_agent.google import state
from tests.test_goal7_ui import CHROME

RUNNER = Path(__file__).parent / "ui" / "goal19_ui.mjs"
TOKEN = "ui19-test-token"
RUN_TIMEOUT_S = 420
ALEX_ROWS = 1200
JORDAN_ROWS = 40
LOOK = {"header_bg": "#626e7a", "header_fg": "#ffffff", "band": ["#ffffff", "#f6f8f9"], "widths": {"Company Name": 164},
        "hidden": [], "rules": [{"headers": ["JD", "Resume"], "when": "not_blank", "value": None, "bg": "#b7e1cd",
                                 "fg": None, "bold": False}], "frozen_rows": 1, "frozen_cols": 0}


def seed(conn) -> dict:
    """Alex: 1,200 rows (Company 0 … 1199), Jordan: 40; a conflict, an uploaded resume, the Sheet's look."""
    ids = {}
    for i in range(ALEX_ROWS):
        made = trackapi.create(conn, "alex", {
            "company": f"Company {i}", "role_title": "ML Engineer" if i % 2 else "AI Engineer", "status": "applied",
            "date_applied": f"09/{1 + i % 28:02d}/2026", "notes": f"note {i}", "job_url": f"https://jobs.example.com/{i}",
            "status_ar": ("Cold", "Rejected", "Interview")[i % 3] if i % 5 == 0 else None})
        ids[i] = made["id"]
    for i in range(JORDAN_ROWS):
        trackapi.create(conn, "jordan", {"company": f"Firm {i}", "role_title": "Data Engineer", "status": "applied",
                                        "date_applied": "09/15/2026", "address": f"City {i}"})
    now = db.iso(db.utcnow())
    with db.tx(conn):
        conn.execute("INSERT INTO gsync_log(at, profile, tab, app_id, kind, column_name, sheet_value, app_value, winner) "
                     "VALUES (?,?,?,?,?,?,?,?,?)", (now, "alex", "Job_Application_Tracker", ids[2], "conflict",
                                                    "Target Name", "Sheet Ann", "App Ann", "sheet"))
        conn.execute("UPDATE applications SET target_name = 'Sheet Ann' WHERE id = ?", (ids[2],))
        conn.execute("UPDATE applications SET applied_on = '2026-10-11', date_applied = '10/11/2026' WHERE id IN (?, ?)",
                     (ids[2], ids[3]))                                  # on the first screen
        conn.execute("INSERT INTO resume_uploads(profile, app_id, version, name, mime, bytes, md5, file_id, link, "
                     "uploaded_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     ("alex", ids[3], 1, "Resume(Company 3).pdf", "application/pdf", 10, "x", "f1",
                      "https://drive.google.com/file/d/f1/view", now))
        conn.execute("UPDATE applications SET resume = 'Resume(Company 3).pdf' WHERE id = ?", (ids[3],))
        state.put(conn, "look.alex", json.dumps({"at": now, "tabs": {"Job_Application_Tracker": LOOK}}))
    return ids


@unittest.skipUnless(shutil.which("node") and Path(CHROME).exists(), "needs node and Google Chrome")
class Goal19GridBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.patches = [mock.patch.object(config, "DB_PATH", root / "ui.db"),
                       mock.patch.object(rgclient, "_base", lambda: "http://127.0.0.1:9")]
        for p in cls.patches:
            p.start()
        conn = db.connect()
        cls.ids = seed(conn)
        conn.close()
        cls.srv = server.make_server(lan=False, port=0)
        cls.srv.token = TOKEN
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        out = subprocess.run(["node", str(RUNNER), f"http://localhost:{cls.srv.server_port}", TOKEN, CHROME,
                              str(root / "chrome"), json.dumps({"rows": ALEX_ROWS, "jordan": JORDAN_ROWS})],
                             capture_output=True, text=True, timeout=RUN_TIMEOUT_S)
        lines = [ln for ln in out.stdout.splitlines() if ln.startswith("{")]
        cls.checks = json.loads(lines[-1])["checks"] if lines else [{"name": "runner", "ok": False,
                                                                     "detail": out.stderr[-3000:]}]

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

    def test_every_browser_check_passed(self):
        self.assert_group([c["name"] for c in self.checks])

    def test_grid_layout(self):
        self.assert_group(["the grid opens under 500 ms", "only the rows near the view are drawn",
                           "the header row stays put", "Company and Role stay put", "row numbers on the left",
                           "every row has the same height", "scrolling to the end draws the last rows",
                           "the Sheet's header color", "the Sheet's green JD / Resume cells",
                           "the Resume cell shows the Drive file chip", "a conflict is flagged on its cell"])

    def test_selection_and_keys(self):
        self.assert_group(["a click selects the cell", "↓ moves down", "→ moves right", "↑ moves up", "← moves left", "Tab moves right",
                           "Shift+Tab moves left", "Shift+Enter moves up", "Home goes to the first column",
                           "End goes to the last column", "⌘↓ jumps to the last row", "⌘↑ jumps to the first row", "⌘→ jumps to the last column", "⌘← jumps to the first column",
                           "Shift+click selects a range", "Shift+arrows extend the range", "a drag selects a range",
                           "a header click selects the column", "⌘A selects everything"])

    def test_editing(self):
        self.assert_group(["typing replaces the cell", "Enter saves and moves down", "the server has the typed value",
                           "Enter edits keeping the text", "Tab saves and moves right", "F2 edits",
                           "double-click edits", "Esc cancels", "Saved shows in the corner",
                           "the status list filters as you type", "Enter picks from the list",
                           "the server has the picked status", "a typed 10/12 becomes a date", "today becomes a date",
                           "the date picker is there", "long text opens a larger editor", "Shift+Enter is a new line",
                           "the long text is saved with its line break", "⌘-click opens a link"])

    def test_clipboard_fill_undo(self):
        self.assert_group(["⌘C copies the range as TSV", "a Google Sheets copy pastes into the cells",
                           "a quoted multi-line cell pastes as one cell", "one value pastes into the whole range",
                           "Delete clears the range", "⌘Z undoes the clear", "⌘⇧Z redoes it", "⌘X cuts",
                           "the fill handle extends dates", "⌘Z undoes the fill", "the fill handle copies a plain value", "the copied value is saved", "a real ⌘V pastes"])

    def test_menus(self):
        self.assert_group(["Sort A → Z", "Sort Z → A", "the sort is shown and clearable", "Filter by values",
                           "Filter by condition", "the filter is shown and clearable", "Hide column",
                           "⌘F finds", "Enter goes to the next match", "Shift+Enter goes to the previous match", "Esc closes find",
                           "right-click → Insert row below", "⌘Z removes the inserted row", "right-click → Insert row above",
                           "⌘Z removes the row inserted above",
                           "right-click → Delete row asks first", "the row is deleted", "Undo puts the row back",
                           "Copy link to the job", "Open JD", "Open resume", "a resized column is remembered",
                           "Group by day shows day headers"])

    def test_both_people_and_phone(self):
        self.assert_group(["Jordan: the grid shows her columns", "Jordan: a cell edit is saved",
                           "phone: the card list, no grid", "phone: a tap opens the row editor", "no page errors"])


if __name__ == "__main__":
    unittest.main()
