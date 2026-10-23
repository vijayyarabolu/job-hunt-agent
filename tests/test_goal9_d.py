"""GOAL9 area D in a real browser (tests/ui/goal9_d.mjs): the Saved / Applied / Interviewing / Archived / Tracker /
Follow-ups / Stats tabs and the Tracker editing. A throwaway DB (1,200 applied rows, a few saved ones, a due
follow-up, a rejected row, jordan's vendors), the real server, headless Chrome over the DevTools protocol, no network,
resumegen unreachable. Every browser check must pass (the catch-all), plus the named groups."""
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from job_hunt_agent import config, db, rgclient, server, trackapi, vendors
from tests.test_goal7_ui import CHROME, TRACKER_ROWS

RUNNER = Path(__file__).parent / "ui" / "goal9_d.mjs"
TOKEN = "ui9d-test-token"
RUN_TIMEOUT_S = 240
LONG_COMPANY = "Company With An Extremely Long Name That Goes On And On And On Incorporated"


def seed(conn) -> None:
    today = date.today()
    for i in range(TRACKER_ROWS):
        trackapi.create(conn, "alex", {"company": f"Company {i}", "role_title": "ML Engineer", "status": "applied",
                                        "date_applied": "2026-09-01", "notes": f"note {i}"})
    for company in ("Saved Co", LONG_COMPANY, "Third Saved"):
        trackapi.create(conn, "alex", {"company": company, "role_title": "Data Scientist", "status": "saved"})
    trackapi.create(conn, "alex", {"company": "Due Co", "role_title": "AI Engineer", "status": "applied",
                                    "date_applied": (today - timedelta(days=3)).strftime("%m/%d/%Y")})
    trackapi.create(conn, "alex", {"company": "Gone Co", "role_title": "ML Engineer", "status": "rejected",
                                    "date_applied": "2026-08-01"})
    trackapi.create(conn, "jordan", {"company": "Jordan Co", "role_title": "Data Engineer", "status": "applied",
                                    "date_applied": "2026-09-02"})
    vendors.save(conn, "jordan", [{"company": "TEK Systems"}, {"company": "Apex"}])
    conn.commit()


@unittest.skipUnless(shutil.which("node") and Path(CHROME).exists(), "needs node and Google Chrome")
class Goal9DBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.patches = [mock.patch.object(config, "DB_PATH", Path(cls.tmp.name) / "ui.db"),
                       mock.patch.object(rgclient, "_base", lambda: "http://127.0.0.1:9")]
        for p in cls.patches:
            p.start()
        conn = db.connect()
        seed(conn)
        conn.close()
        cls.srv = server.make_server(lan=False, port=0)
        cls.srv.token = TOKEN
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        out = subprocess.run(["node", str(RUNNER), f"http://localhost:{cls.srv.server_port}", TOKEN, CHROME,
                              str(Path(cls.tmp.name) / "chrome")], capture_output=True, text=True, timeout=RUN_TIMEOUT_S)
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

    def test_every_browser_check_passed(self):
        self.assert_group([c["name"] for c in self.checks])

    def test_move_to_paints_counts_and_rolls_back(self):
        # on desktop the status is a grid cell (type, Enter picks; ⌘Z undoes); phone cards keep Move to…
        self.assert_group(["the status cell lists the statuses as you type",
                           "a status change takes the row out of the Saved filter and counts it at once",
                           "the status counts follow", "the total follows", "the table was not reloaded",
                           "the server has the move", "⌘Z brings the row back where it was", "and the counts and total with it",
                           "the server undid it", "a refused change is put back with a message",
                           "on Interviewing a row moved to Rejected leaves at once", "⌘Z puts it back on Interviewing",
                           "Move to… on a card paints at once"])

    def test_inline_edit_and_editor_update_without_reload(self):
        self.assert_group(["typing edits the cell", "Enter shows the new value at once", "the grid keeps the focus",
                           "no reload for a cell edit", "the corner says Saved", "⌘Z restores the cell", "and the server",
                           "a refused cell edit is put back", "Save paints the row in the table",
                           "Save paints the Follow-ups badge", "no reload for an editor save", "the editor keeps the focus on Save",
                           "Esc closes the editor and focus returns to the grid", "right-click offers Mark applied…",
                           "Mark applied opens its dialog", "Mark applied without a file takes the row out of Saved at once",
                           "a vendor cell shows the new value at once and keeps the focus", "the next edit starts from the saved value"])

    def test_show_columns_is_a_live_multi_choice_menu(self):
        self.assert_group(["a tick hides the column at once and the menu stays open", "the table keeps its scroll position",
                           "the anchor still says it is open", "a second tick applies too, menu still open",
                           "Esc closes it from a checkbox and focus goes back to Columns", "an outside click closes it",
                           "a second click on Columns closes it", "Reset shows every column again"])

    def test_focus_follow_ups_phone_and_labels(self):
        self.assert_group(["typing in the search keeps the focus across the reload", "a searched Tracker shows no stray null", "the chip keeps the focus after the reload",
                           "scrolling 1200 rows stays smooth", "Mark sent today updates the list at once",
                           "No takes it off the list and the badge", "nothing in a card runs past 390 px (long company name included)",
                           "every control on the phone is 44 px tall", "the follow-up links are 44 px tall on the phone",
                           "a long press shows the label on the phone", "a keyboard focus shows the label",
                           "hover label: Sort, filter, hide", "hover label: Drag to resize the column",
                           "labels: tracker table", "labels: Show columns menu", "labels: the row editor", "labels: Follow-ups",
                           "labels: phone cards", "no page errors"])


if __name__ == "__main__":
    unittest.main()
