"""after Fix with note / Re-import the resume panel shows the new file at once. The panel carries resumegen's
"what changed" line, how many edits Undo my edit can take back (None when resumegen has no Undo: the button stays
hidden) and when the files were made (file URLs carry it, so no viewer keeps the old PDF); Undo my edit and Cancel
are actions."""
import unittest

from job_hunt_agent import rgjobs
from tests.test_goal8_flow import JD, PID, Goal8Case

EDITED = "2026-10-12T21:40:00Z"


class ResumePanelAfterEditTest(Goal8Case):
    def edited(self, **extra) -> int:
        return self.rg.add(PID, "Acme", "MLE", JD, status="ready", files_version=EDITED, undo_count=1,
                           what_changed="Your Doc edit: Location on the top line: Phoenix, AZ", **extra)

    def test_the_panel_carries_what_changed_undo_and_the_files_time(self):
        panel = rgjobs.detail(PID, self.edited())
        self.assertEqual(panel["what_changed"], "Your Doc edit: Location on the top line: Phoenix, AZ")
        self.assertEqual(panel["undo_count"], 1)
        self.assertEqual(panel["files_version"], EDITED)

    def test_an_older_resumegen_hides_undo(self):
        rg_id = self.rg.add(PID, "Acme", "MLE", JD, status="ready", times={"made": "x", "updated": "2026-10-12T20:00:00Z"})
        panel = rgjobs.detail(PID, rg_id)
        self.assertIsNone(panel["undo_count"])
        self.assertIsNone(panel["what_changed"])
        self.assertEqual(panel["files_version"], "2026-10-12T20:00:00Z")    # still cache-busted by its updated time

    def test_approved_before_the_edit_is_flagged_for_approve_again(self):
        rg_id = self.edited(approved_stale=True, drive_file_id="F1")
        self.assertTrue(rgjobs.detail(PID, rg_id)["approved_stale"])

    def test_undo_my_edit_and_cancel_are_sent_to_resumegen(self):
        rg_id = self.edited()
        self.assertIn("undo_edit", rgjobs.ACTIONS)
        out = rgjobs.action(PID, rg_id, "undo_edit")
        self.assertEqual(self.rg.actions[-1], (rg_id, "undo_edit", {}))
        self.assertEqual(out["panel"]["id"], rg_id)                      # the panel is fetched again after it
        rgjobs.action(PID, rg_id, "cancel")
        self.assertEqual(self.rg.actions[-1], (rg_id, "cancel", {}))

    def test_someone_elses_resume_cannot_be_undone(self):
        rg_id = self.rg.add("jordan", "Acme", "MLE", JD, status="ready", undo_count=1)
        with self.assertRaises(KeyError):
            rgjobs.action(PID, rg_id, "undo_edit")


if __name__ == "__main__":
    unittest.main()
