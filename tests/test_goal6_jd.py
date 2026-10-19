"""GOAL6 §1 + §3: the Mark applied dialog backend (resume picked, "resume missing" until a file is uploaded), the JD
kept as text (feed / the cell's own text / the live posting; "posting gone"), the JD cell of new and link-only rows
the app made (the role title linked to the posting, the text in jd_text; never over typed text), the backfill report,
and linkify-jd (only link runs written: no cell text ever changes)."""
from datetime import datetime, timezone
from unittest import mock

from job_hunt_agent import applydialog, db, gsync_jd, gsync_linkify, jdbackfill, jdsnap, trackapi
from job_hunt_agent.gsync_values import to_cell
from tests.test_gsync import SyncCase, TAB
from tests.test_tracker_ui import TrackerCase

POSTING = "https://boards.greenhouse.io/acme/jobs/42"
JD_TEXT = "About the role\n\nBuild ML systems with Python and PyTorch. " * 8


class CellFormatTest(TrackerCase):
    APP = {"job_url": POSTING, "jd_text": JD_TEXT * 10}

    def test_a_title_is_one_clipped_line_linked_to_the_posting_with_the_jd_as_a_note(self):
        cell = gsync_jd.jd_cell(to_cell("ML Engineer", True, header="JD"), self.APP)
        self.assertEqual(cell["textFormatRuns"], [{"startIndex": 0, "format": {"link": {"uri": POSTING}}}])
        self.assertEqual(cell["userEnteredFormat"], {"wrapStrategy": "CLIP"})
        self.assertTrue(cell["note"].startswith("About the role") and cell["note"].endswith(gsync_jd.NOTE_TAIL))
        self.assertEqual(len(cell["note"]), gsync_jd.NOTE_CHARS)

    def test_a_cell_with_its_own_link_and_rows_without_a_posting_are_written_as_they_are(self):
        own = to_cell(f"{POSTING}/other", True, header="JD")
        self.assertEqual(gsync_jd.jd_cell(own, self.APP), own)
        title = to_cell("ML Engineer", True, header="JD")
        self.assertEqual(gsync_jd.jd_cell(title, {"jd_text": JD_TEXT}), title)

    def test_link_only(self):
        self.assertEqual(jdsnap.link_only(f"  {POSTING} "), POSTING)
        self.assertIsNone(jdsnap.link_only(f"{POSTING}\n\nAbout the role"))
        self.assertIsNone(jdsnap.link_only("About the role"))


class SnapshotTest(TrackerCase):
    def test_typed_jd_text_is_kept_as_is_and_the_cell_is_never_rewritten(self):
        row = self.add(jd=JD_TEXT.strip(), job_url=POSTING)
        snap = row["jd_snapshot"]
        self.assertEqual((snap["state"], snap["source"], snap["url"]), ("saved", "sheet", POSTING))
        self.assertEqual(trackapi.get(self.conn, "alex", row["id"])["jd"], JD_TEXT.strip())

    def test_a_link_only_row_reads_the_live_posting_and_gets_the_title_and_the_text(self):
        posting = {"ats": "greenhouse", "description": JD_TEXT}
        with mock.patch.object(jdsnap.jobfetch, "fetch_posting", return_value=posting):
            row = self.add(job_url=POSTING)                      # jd defaults to the link
        self.assertEqual((row["jd_snapshot"]["state"], row["jd_snapshot"]["source"]), ("saved", "fetch"))
        self.assertEqual((row["jd"], row["job_url"]), ("ML Engineer", POSTING))
        self.assertIn("PyTorch", self.conn.execute("SELECT jd_text FROM applications WHERE id = ?",
                                                   (row["id"],)).fetchone()[0])

    def test_an_untitled_row_shows_the_jd_arrow(self):
        posting = {"ats": "greenhouse", "description": JD_TEXT}
        with mock.patch.object(jdsnap.jobfetch, "fetch_posting", return_value=posting):
            row = self.add(job_url=POSTING)
        with db.tx(self.conn):
            self.conn.execute("UPDATE applications SET role_title = '', jd = ? WHERE id = ?", (POSTING, row["id"]))
        self.assertTrue(jdsnap.fill_cell(self.conn, row["id"]))
        self.assertEqual(trackapi.get(self.conn, "alex", row["id"])["jd"], jdsnap.NO_TITLE)

    def test_an_imported_link_only_row_is_left_as_it_is(self):
        row = self.add(job_url=POSTING)
        with db.tx(self.conn):
            self.conn.execute("UPDATE applications SET source = 'import', jd = ?, jd_text = NULL WHERE id = ?",
                              (POSTING, row["id"]))
        self.assertFalse(jdsnap.fill_cell(self.conn, row["id"]))

    def test_a_closed_posting_is_marked_gone_and_the_cell_keeps_its_link(self):
        closed = ValueError("This posting is closed: Greenhouse answers 404 for it.")
        with mock.patch.object(jdsnap.jobfetch, "fetch_posting", side_effect=closed):
            row = self.add(job_url=POSTING)
        self.assertEqual(row["jd_snapshot"]["state"], "gone")
        self.assertEqual(row["jd"], POSTING)

    def test_text_saved_earlier_survives_the_posting_going_away(self):
        with mock.patch.object(jdsnap.jobfetch, "fetch_posting", return_value={"ats": "lever", "description": JD_TEXT}):
            row = self.add(job_url=POSTING)
        with mock.patch.object(jdsnap.jobfetch, "fetch_posting", side_effect=ValueError("This posting is closed")):
            snap = jdsnap.snapshot(self.conn, row["id"], fetch=True)
        self.assertEqual((snap["state"], snap["text"].strip()), ("saved", JD_TEXT.strip()))

    def test_a_row_the_person_blanked_in_the_sheet_is_not_filled(self):
        posting = {"ats": "greenhouse", "description": JD_TEXT}
        with mock.patch.object(jdsnap.jobfetch, "fetch_posting", return_value=posting):
            row = self.add(job_url=POSTING)
        with db.tx(self.conn):                                   # a duplicate the person emptied in the Sheet
            self.conn.execute("UPDATE applications SET company = '', role_title = '', jd = '' WHERE id = ?",
                              (row["id"],))
        self.assertFalse(jdsnap.fill_cell(self.conn, row["id"]))
        self.assertEqual(trackapi.get(self.conn, "alex", row["id"])["jd"], "")

    def test_backfill_counts_and_lists(self):
        with mock.patch.object(jdsnap.jobfetch, "fetch_posting", side_effect=ValueError("closed")):
            self.add(job_url=POSTING, company="Gone Co")
        self.add(jd=JD_TEXT, company="Typed Co")
        with db.tx(self.conn):
            self.conn.execute("DELETE FROM jd_snapshots")
        posting = {"ats": "greenhouse", "description": JD_TEXT}
        with mock.patch.object(jdsnap.jobfetch, "fetch_posting", return_value=posting):
            result = jdbackfill.run(self.conn, ["alex"])
        counts = result["counts"]["alex"]
        self.assertEqual((counts.get("saved:fetch"), counts.get("saved:sheet")), (1, 1))
        self.assertEqual(len(result["filled"]), 1)
        self.assertEqual([r["company"] for r in result["no_url"]], ["Typed Co"])
        report = jdbackfill.report_md(result, "2026-10-06 12:00")
        self.assertIn("| alex | 0 | 1 | 1 | 0 | 0 | 0 | 2 | 1 |", report)


class ApplyDialogTest(TrackerCase):
    def test_options_list_every_resume_with_one_preselected(self):
        info = applydialog.options(self.conn, "alex", datetime.now(timezone.utc), uid=self.uid)
        ids = [r["id"] for r in info["resumes"]]
        self.assertTrue(ids)
        self.assertIn(info["best"], ids)

    def test_without_a_file_the_row_shows_resume_missing_until_an_upload(self):
        row = self.add(status="saved")
        resume = applydialog.options(self.conn, "alex", datetime.now(timezone.utc), app_id=row["id"])["best"]
        app_id = applydialog.mark(self.conn, "alex", datetime.now(timezone.utc), app_id=row["id"], resume_id=resume)
        out = trackapi.get(self.conn, "alex", app_id)
        self.assertEqual((out["status"], out["resume_missing"], out["has_upload"]), ("applied", True, False))
        self.assertEqual(out["resume_used"], resume)

    def test_marked_with_a_file_has_no_chip(self):
        app_id = applydialog.mark(self.conn, "alex", datetime.now(timezone.utc), uid=self.uid, with_file=True)
        self.assertFalse(trackapi.get(self.conn, "alex", app_id)["resume_missing"])


class LinkifyTest(SyncCase):
    def setUp(self):
        super().setUp()
        self.link()
        self.edit("Acme", "JD", f"See {POSTING} for details")              # a link written as plain text
        self.edit("Beta", "JD", f"{POSTING}\n\nAbout the role")
        cell = self.book.cell(TAB, self.row_of("Beta"), self.column("JD"))
        cell["runs"] = [{"startIndex": 0, "format": {"link": {"uri": POSTING}}}]   # already linked
        cell["link"] = POSTING

    def test_only_links_are_written_and_no_text_changes(self):
        before = self.person_cells()
        entry = gsync_linkify.plan(self.conn, "jane")[0]
        self.assertEqual(entry["counts"], {"fix": 1, "linked": 1, "no_url": 1})
        report = gsync_linkify.apply(self.conn, "jane")[TAB]
        self.assertEqual((report["linked"], report["not_linked"]), (1, []))
        self.assertEqual(self.person_cells(), before)                         # every cell's text as it was
        acme = self.book.cell(TAB, self.row_of("Acme"), self.column("JD"))
        self.assertEqual(acme["link"], POSTING)
        self.assertEqual(gsync_linkify.plan(self.conn, "jane")[0]["fix"], [])  # nothing left to do

    def test_the_link_styling_sheets_adds_is_still_a_linked_cell(self):
        style = {"foregroundColor": {"blue": 0.8}, "foregroundColorStyle": {"rgbColor": {"blue": 0.8}}, "underline": True}
        linked = {"formattedValue": f"{POSTING}\n\nAbout", "textFormatRuns": [
            {"format": {**style, "link": {"uri": POSTING}}}, {"startIndex": len(POSTING), "format": {}}]}
        bold = {"formattedValue": f"{POSTING}\n\nAbout", "textFormatRuns": [{"format": {"bold": True}}]}
        self.assertEqual(gsync_linkify.classify(linked), "linked")
        self.assertEqual(gsync_linkify.classify(bold), "rich")

    def test_a_link_sheets_stored_normalised_is_still_linked(self):
        text = "Benefits: https://www.Example.com/Perks.Pursuant to law"
        cell = {"formattedValue": text, "textFormatRuns": [
            {"format": {}}, {"startIndex": 10, "format": {"link": {"uri": "https://www.example.com/perks.pursuant"}}}]}
        self.assertEqual(gsync_linkify.classify(cell), "linked")

    def test_a_zero_width_space_ends_a_link(self):
        from job_hunt_agent.gsync_values import urls
        self.assertEqual([u for _, _, u in urls("see https://tinyurl.com/RA-request​ now")],
                         ["https://tinyurl.com/RA-request"])
