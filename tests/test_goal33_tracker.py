"""GOAL33 §3 (serve.log, 10-21): Mark applied from Autofill and the page's "Did you apply?" Yes for the same job at the
same moment: the second one failed with "UNIQUE constraint failed: applications.profile, applications.job_uid" because
track_job looked for the row outside its write transaction. Now both get the one row."""
from unittest import mock

from job_hunt_agent import db, history, tracker, trackapi
from tests.test_goal8_flow import PID, Goal8Case


class TrackJobRaceTest(Goal8Case):
    def test_a_row_made_by_another_request_meanwhile_is_reused(self):
        uid = self.uids[0]
        other = db.connect()
        real = trackapi.job_facts

        def facts_while_another_saves(conn, job_uid):
            found = real(conn, job_uid)
            with mock.patch.object(trackapi, "job_facts", real):
                tracker.track_job(other, PID, job_uid)      # the other request wins the race
            return found
        with mock.patch.object(trackapi, "job_facts", facts_while_another_saves):
            app = tracker.track_job(self.conn, PID, uid)
        other.close()
        rows = self.conn.execute("SELECT id FROM applications WHERE profile = ? AND job_uid = ?", (PID, uid)).fetchall()
        self.assertEqual([r["id"] for r in rows], [app])
        events = self.conn.execute("SELECT count(*) FROM application_events WHERE app_id = ?", (app,)).fetchone()[0]
        self.assertEqual(events, 1)


class FollowupUndoTest(Goal8Case):
    """GOAL33 review: the "Follow-up marked sent" toast's Undo reverted the row's last edit, whatever it was; it now
    names the field and is refused when the row's last change was something else."""
    def setUp(self):
        super().setUp()
        self.app = tracker.track_job(self.conn, PID, self.uids[0])

    def test_undo_reverts_the_follow_up(self):
        with history.action("Alex"):                        # one request, as the server makes it
            trackapi.followup_done(self.conn, PID, self.app, 1)
        out = trackapi.undo(self.conn, PID, self.app, field="follow_up_1")
        self.assertEqual([u["field"] for u in out["undone"]], ["follow_up_1"])

    def test_a_later_edit_is_never_undone_in_its_place(self):
        with history.action("Alex"):
            trackapi.followup_done(self.conn, PID, self.app, 1)
        with history.action("Alex"):
            trackapi.update(self.conn, PID, self.app, {"notes": "called them"})
        with self.assertRaisesRegex(ValueError, "changed again"):
            trackapi.undo(self.conn, PID, self.app, field="follow_up_1")
        notes = self.conn.execute("SELECT notes FROM applications WHERE id = ?", (self.app,)).fetchone()[0]
        self.assertEqual(notes, "called them")
