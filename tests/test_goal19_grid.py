"""GOAL19 §1 / §3 server side: many cells in one write with the values they replaced (undo / redo), a blank row
that waits for a name before the Sheet gets it, conflicts flagged per cell, × Remove of a row (snapshot, Undo, the
Sheet row deleted after the Undo toast and never taken in again) and of a feed job (hidden, also when fetched again
under a new id), the Sheet's look; and the sync round trip against the fake Google Sheet."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from job_hunt_agent import api, config, db, gridapi, gsync, gsync_cycle, removal, sheet_look, trackapi, tracker
from job_hunt_agent.google import state
from tests.test_goal19_reasons import seed_more
from tests.test_gsync import TAB, SyncCase
from tests.test_server import seed_job

NOW = datetime(2026, 10, 12, 21, 0, tzinfo=timezone.utc)


class DbCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = mock.patch.object(config, "DB_PATH", Path(self.tmp.name) / "t.db")
        self.patch.start()
        self.conn = db.connect()
        self.a = trackapi.create(self.conn, "alex", {"company": "Acme", "role_title": "ML Engineer",
                                                      "date_applied": "10/01/2026", "notes": "one"})["id"]
        self.b = trackapi.create(self.conn, "alex", {"company": "Beta", "role_title": "AI Engineer"})["id"]

    def tearDown(self):
        self.conn.close()
        self.patch.stop()
        self.tmp.cleanup()

    def row(self, app_id):
        return trackapi.get(self.conn, "alex", app_id)


class UpdateManyTest(DbCase):
    def test_many_cells_one_request_with_what_they_replaced(self):
        out = gridapi.update_many(self.conn, "alex", [
            {"app": self.a, "changes": {"notes": "two", "date_applied": "10/12/2026"}},
            {"app": self.b, "changes": {"target_name": "Ann"}}])
        self.assertEqual(out["errors"], [])
        self.assertEqual({(b["app"], b["field"], b["value"]) for b in out["before"]},
                         {(self.a, "notes", "one"), (self.a, "date_applied", "10/01/2026"), (self.b, "target_name", None)})
        self.assertEqual([r["id"] for r in out["rows"]], [self.a, self.b])
        self.assertEqual(self.row(self.a)["applied_on"], "2026-10-12")
        self.assertEqual(self.row(self.b)["target_name"], "Ann")

    def test_a_refused_row_is_reported_and_the_others_are_written(self):
        out = gridapi.update_many(self.conn, "alex", [{"app": self.a, "changes": {"date_applied": "someday"}},
                                                       {"app": self.b, "changes": {"notes": "ok"}},
                                                       {"app": 99999, "changes": {"notes": "x"}}])
        self.assertEqual([e["app"] for e in out["errors"]], [self.a, 99999])
        self.assertEqual(self.row(self.a)["date_applied"], "10/01/2026")
        self.assertEqual(self.row(self.b)["notes"], "ok")

    def test_another_persons_row_is_not_touched(self):
        out = gridapi.update_many(self.conn, "jordan", [{"app": self.a, "changes": {"notes": "hers"}}])
        self.assertEqual(len(out["errors"]), 1)
        self.assertEqual(self.row(self.a)["notes"], "one")

    def test_bad_requests(self):
        for edits in ([], "x", [{"app": self.a}], [{"app": self.a, "changes": "x"}]):
            with self.subTest(edits=edits), self.assertRaises(ValueError):
                gridapi.update_many(self.conn, "alex", edits)
        with mock.patch.object(gridapi, "MAX_EDITS", 1), self.assertRaises(ValueError):
            gridapi.update_many(self.conn, "alex", [{"app": self.a, "changes": {"notes": "a", "salary": "b"}}])

    def test_undo_writes_the_before_values_back(self):
        out = gridapi.update_many(self.conn, "alex", [{"app": self.a, "changes": {"notes": "two"}}])
        back = [{"app": b["app"], "changes": {b["field"]: b["value"]}} for b in out["before"]]
        gridapi.update_many(self.conn, "alex", back, guard=False)
        self.assertEqual(self.row(self.a)["notes"], "one")


class InsertTest(DbCase):
    def test_a_blank_row_is_made_in_the_tab_status_and_is_not_for_the_sheet_yet(self):
        row = gridapi.insert_blank(self.conn, "alex", "screen")
        self.assertEqual((row["status"], row["company"], row["role_title"]), ("screen", None, None))
        eligible = lambda: [a["id"] for a in gsync.eligible_new(self.conn, "alex", gsync.apps_of(self.conn, "alex"))]
        self.assertNotIn(row["id"], eligible())
        gridapi.update_many(self.conn, "alex", [{"app": row["id"], "changes": {"company": "Gamma"}}])
        self.assertIn(row["id"], eligible())
        with self.assertRaises(ValueError):
            gridapi.insert_blank(self.conn, "alex", "nope")


class ConflictFlagTest(DbCase):
    def test_the_newest_conflict_per_cell_of_the_last_week(self):
        def log(at, sheet_value, winner):
            self.conn.execute("INSERT INTO gsync_log(at, profile, tab, app_id, kind, column_name, sheet_value, "
                              "app_value, winner) VALUES (?,?,?,?,?,?,?,?,?)",
                              (db.iso(at), "alex", TAB, self.a, "conflict", "Target Name", sheet_value, "App", winner))
        now = db.utcnow()
        log(now - timedelta(days=9), "old", "sheet")
        log(now - timedelta(hours=2), "S1", "app")
        log(now - timedelta(hours=1), "S2", "sheet")
        self.conn.commit()
        flags = gridapi.conflicts(self.conn, "alex", [self.a, self.b])
        self.assertEqual(list(flags), [self.a])
        self.assertEqual({k: flags[self.a]["Target Name"][k] for k in ("winner", "sheet", "app")},
                         {"winner": "sheet", "sheet": "S2", "app": "App"})
        rows = trackapi.rows(self.conn, "alex", clip=True)["rows"]
        self.assertEqual({r["id"]: bool(r["conflicts"]) for r in rows}, {self.a: True, self.b: False})
        self.assertEqual(gridapi.conflicts(self.conn, "jordan", [self.a]), {})


class RemoveTest(DbCase):
    def test_a_row_is_deleted_whole_and_undo_puts_it_back_exactly(self):
        gridapi.update_many(self.conn, "alex", [{"app": self.a, "changes": {"target_name": "Ann"}}])
        before = self.row(self.a)
        out = removal.remove_app(self.conn, "alex", self.a, "duplicate")
        with self.assertRaises(KeyError):
            self.row(self.a)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM edit_history WHERE app_id = ?", (self.a,)).fetchone()[0], 0)
        self.assertNotIn(self.a, [r["id"] for r in trackapi.rows(self.conn, "alex")["rows"]])
        removal.undo(self.conn, "alex", out["removed"])
        after = self.row(self.a)
        self.assertEqual({k: after[k] for k in ("id", "company", "target_name", "notes")},
                         {k: before[k] for k in ("id", "company", "target_name", "notes")})
        self.assertEqual(len(after["history"]), len(before["history"]))
        with self.assertRaises(KeyError):
            removal.undo(self.conn, "alex", out["removed"])                       # once

    def test_only_the_persons_own_rows(self):
        with self.assertRaises(KeyError):
            removal.remove_app(self.conn, "jordan", self.a)
        with self.assertRaises(ValueError):
            removal.remove_app(self.conn, "alex", self.a, "because")

    def test_a_removed_job_is_hidden_from_the_feed_also_under_a_new_id(self):
        uid = seed_job(self.conn)
        shown = lambda tab: [j["uid"] for j in api.jobs(self.conn, "alex", tab, NOW, wait=True)["jobs"]]
        self.assertIn(uid, shown("recommended"))
        out = removal.remove_job(self.conn, "alex", uid)
        for tab in ("recommended", "archived", "saved"):
            self.assertNotIn(uid, shown(tab))
        again = seed_more(self.conn, "acme2", "Acme")                              # the same posting under a new id
        self.assertEqual(removal.keep(self.conn, "alex", [{"uid": again, "company": "Acme",
                                                            "title": "Machine Learning Engineer",
                                                            "location": "Austin, TX"}]), [])
        self.assertNotIn(again, shown("recommended"))                             # hidden by its job key
        self.assertEqual(removal.hidden(self.conn, "jordan"), (set(), set()))       # hers is not hidden
        removal.undo(self.conn, "alex", out["removed"])
        self.assertTrue({uid, again} & set(shown("recommended")))                 # back (one card: the same posting)

    def test_removing_a_row_of_a_feed_job_hides_the_job_and_undo_shows_it_again(self):
        uid = seed_job(self.conn)
        app_id = tracker.track_job(self.conn, "alex", uid)
        out = removal.remove_app(self.conn, "alex", app_id)
        self.assertIn(uid, removal.hidden(self.conn, "alex")[0])
        removal.undo(self.conn, "alex", out["removed"])
        self.assertNotIn(uid, removal.hidden(self.conn, "alex")[0])
        self.assertEqual(self.row(app_id)["job_uid"], uid)

    def test_job_key(self):
        self.assertEqual(removal.job_key("Acme, Inc.", "ML Engineer (Remote)", "Austin, TX; Remote"),
                         "acme|ml engineer remote|austin tx")


class LookTest(DbCase):
    SHEET = {"properties": {"title": TAB, "gridProperties": {"frozenRowCount": 1}},
             "bandedRanges": [{"rowProperties": {"firstBandColor": {"red": 1, "green": 1, "blue": 1},
                                                 "secondBandColor": {"red": 0.9647059, "green": 0.972549,
                                                                     "blue": 0.9764706}}}],
             "conditionalFormats": [
                 {"ranges": [{"startColumnIndex": 1, "endColumnIndex": 3}],
                  "booleanRule": {"condition": {"type": "NOT_BLANK"}, "format": {
                      "backgroundColor": {"red": 0.7176471, "green": 0.88235295, "blue": 0.8039216}}}},
                 {"ranges": [{"startColumnIndex": 0}], "booleanRule": {"condition": {"type": "CUSTOM_FORMULA"},
                                                                       "format": {}}}],
             "data": [{"columnMetadata": [{"pixelSize": 164}, {"pixelSize": 300}, {"pixelSize": 142},
                                          {"hiddenByUser": True, "pixelSize": 100}],
                       "rowData": [{"values": [
                           {"formattedValue": "Company Name", "effectiveFormat": {
                               "backgroundColor": {"red": 0.38431373, "green": 0.43137255, "blue": 0.47843137},
                               "textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1}}}},
                           {"formattedValue": "JD"}, {"formattedValue": "Resume"}, {"formattedValue": "job-hunt-agent id"}]}]}]}

    def test_parse_the_header_row(self):
        got = sheet_look.parse(self.SHEET)
        self.assertEqual((got["header_bg"], got["header_fg"]), ("#626e7a", "#ffffff"))
        self.assertEqual(got["band"], ["#ffffff", "#f6f8f9"])
        self.assertEqual(got["widths"], {"Company Name": 164, "JD": 300, "Resume": 142, "job-hunt-agent id": 100})
        self.assertEqual(got["hidden"], ["job-hunt-agent id"])
        self.assertEqual(got["rules"], [{"headers": ["JD", "Resume"], "when": "not_blank", "value": None,
                                         "bg": "#b7e1cd", "fg": None, "bold": False}])
        self.assertEqual(sheet_look.parse({}), {"header_bg": None, "header_fg": None, "band": [], "widths": {},
                                                "hidden": [], "rules": [], "frozen_rows": 0, "frozen_cols": 0})

    def test_cached_never_waited_for(self):
        self.assertIsNone(sheet_look.get(self.conn, "alex"))                       # not linked: nothing started
        with db.tx(self.conn):
            state.put(self.conn, "look.alex", json.dumps({"at": db.iso(db.utcnow()), "tabs": {TAB: {"band": []}}}))
        with mock.patch.object(sheet_look.threading, "Thread") as thread:
            self.assertEqual(sheet_look.get(self.conn, "alex")["tabs"], {TAB: {"band": []}})
            thread.assert_not_called()


class SheetRoundTripTest(SyncCase):
    """The grid's writes reach the Sheet; a delete takes the Sheet row away after the Undo toast and it never comes
    back as a typed row; an Undo after that puts it back at the end; a blank inserted row waits for a name."""

    def setUp(self):
        super().setUp()
        self.link()

    def test_cells_written_together_reach_the_sheet(self):
        gridapi.update_many(self.conn, "jane", [{"app": self.ids["Acme"], "changes": {"target_name": "Zed"}},
                                                {"app": self.ids["Gamma"], "changes": {"target_role": "CTO"}}])
        self.sync()
        self.assertEqual(self.grid()[self.row_of("Acme")][self.column("Target Name")], "Zed")
        self.assertEqual(self.grid()[self.row_of("Gamma")][self.column("Target Role")], "CTO")

    def test_a_sheet_edit_made_meanwhile_wins_when_newer_and_is_flagged(self):
        now = datetime.now(timezone.utc)
        with db.tx(self.conn):
            state.put(self.conn, f"gsync.jane.{TAB}.read_at", db.iso(now - timedelta(minutes=10)))
        gridapi.update_many(self.conn, "jane", [{"app": self.ids["Acme"], "changes": {"target_name": "App Value"}}])
        with db.tx(self.conn):
            self.conn.execute("UPDATE applications SET field_at = ? WHERE id = ?",
                              (json.dumps({"target_name": db.iso(now - timedelta(minutes=8))}), self.ids["Acme"]))
        self.edit("Acme", "Target Name", "Sheet Value")
        self.assertEqual(self.sync()["conflicts"], 1)
        (row,) = gridapi.view_rows(self.conn, "jane", [self.ids["Acme"]])
        self.assertEqual(row["target_name"], "Sheet Value")
        self.assertEqual(row["conflicts"]["Target Name"]["winner"], "sheet")

    def test_delete_waits_for_the_undo_toast_then_the_sheet_row_goes(self):
        out = removal.remove_app(self.conn, "jane", self.ids["Beta"])
        self.assertTrue(out["sheet"])
        report = self.sync()                                        # inside the Undo window: the row stays, not re-read
        self.assertEqual((report.get("deleted"), report.get("new_from_sheet", 0)), (0, 0))
        self.assertIn("Beta", [r[0] for r in self.grid() if r])
        with mock.patch.object(removal, "SHEET_GRACE_S", 0):
            self.assertEqual(self.sync()["deleted"], 1)
        self.assertNotIn("Beta", [r[0] for r in self.grid() if r])
        self.assertEqual([r[0] for r in self.grid()[1:4] if r], ["Acme", "Gamma"])
        report = self.sync()
        self.assertEqual((report.get("deleted"), report.get("new_from_sheet", 0), report["missing"]), (0, 0, 0))
        self.assertEqual(self.conn.execute("SELECT count(*) FROM applications WHERE company = 'Beta'").fetchone()[0], 0)
        removal.undo(self.conn, "jane", out["removed"])            # Undo after the Sheet row went: added again
        self.assertEqual(self.sync()["appended"], 1)
        self.assertIn("Beta", [r[0] for r in self.grid() if r])

    def test_undo_inside_the_toast_leaves_the_sheet_alone(self):
        out = removal.remove_app(self.conn, "jane", self.ids["Gamma"])
        removal.undo(self.conn, "jane", out["removed"])
        with mock.patch.object(removal, "SHEET_GRACE_S", 0):
            report = self.sync()
        self.assertEqual((report.get("deleted"), report.get("appended", 0), report["missing"]), (0, 0, 0))
        self.assertEqual(self.grid()[self.row_of("Gamma")][14], f"jh:{self.ids['Gamma']}")

    def test_an_undo_while_the_sheet_row_is_being_deleted_adds_it_again(self):
        out = removal.remove_app(self.conn, "jane", self.ids["Beta"])
        real = gsync.batch

        def undo_then_batch(sid, requests):                       # the Undo lands after the claim, before the delete
            if any("deleteDimension" in r for r in requests):
                removal.undo(self.conn, "jane", out["removed"])
            return real(sid, requests)
        with mock.patch.object(removal, "SHEET_GRACE_S", 0), mock.patch.object(gsync, "batch", undo_then_batch):
            self.assertEqual(self.sync()["deleted"], 1)
        self.assertNotIn("Beta", [r[0] for r in self.grid() if r])
        self.assertEqual(self.sync()["appended"], 1)                 # not lost: back in the Sheet
        self.assertIn("Beta", [r[0] for r in self.grid() if r])

    def test_an_undo_and_a_failed_delete_leave_exactly_one_sheet_row(self):
        real = gsync.batch
        for deleted_first in (False, True):                      # the failure before / after the row went
            with self.subTest(deleted_first=deleted_first):
                out = removal.remove_app(self.conn, "jane", self.ids["Beta"])

                def undo_then_fail(sid, requests):
                    if any("deleteDimension" in r for r in requests):
                        removal.undo(self.conn, "jane", out["removed"])
                        if deleted_first:
                            real(sid, requests)
                        raise RuntimeError("quota")
                    return real(sid, requests)
                with mock.patch.object(removal, "SHEET_GRACE_S", 0), mock.patch.object(gsync, "batch", undo_then_fail), \
                        self.assertRaises(RuntimeError):
                    self.sync()
                self.sync()
                self.sync()
                self.assertEqual([r[0] for r in self.grid() if r].count("Beta"), 1)

    def test_a_refused_sheet_delete_is_tried_again(self):
        removal.remove_app(self.conn, "jane", self.ids["Beta"])
        with mock.patch.object(removal, "SHEET_GRACE_S", 0):
            with mock.patch.object(gsync, "batch", side_effect=RuntimeError("quota")), self.assertRaises(RuntimeError):
                gsync_cycle.delete_rows(self.conn, next(t for t in gsync.targets(self.conn, "jane") if t.title == TAB))
            self.assertEqual(removal.sheet_deletes(self.conn, "jane", TAB), [self.ids["Beta"]])
            self.assertEqual(self.sync()["deleted"], 1)
        self.assertNotIn("Beta", [r[0] for r in self.grid() if r])

    def test_a_blank_row_reaches_the_sheet_once_it_has_a_name(self):
        row = gridapi.insert_blank(self.conn, "jane")
        self.assertEqual(self.sync().get("appended", 0), 0)
        gridapi.update_many(self.conn, "jane", [{"app": row["id"], "changes": {"company": "Delta", "role_title": "MLE"}}])
        self.assertEqual(self.sync()["appended"], 1)
        self.assertIn("Delta", [r[0] for r in self.grid() if r])
        self.assertEqual(gsync_cycle.sync_profile(self.conn, "jane")[TAB].get("appended", 0), 0)


if __name__ == "__main__":
    unittest.main()
