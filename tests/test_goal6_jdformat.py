"""GOAL6 JD format (user, 2026-10-06 evening; replaces the "URL + JD" cell): the JD cell is one clipped line, the role
title linked to the posting, with the first ~2,000 characters as a hover note; the full text lives in a hidden
"JD Text" column at the far right (synced both ways); rows the app wrote in the old "URL + JD" style are converted and
their height reset; the person's own older rows stay as they are; uploaded resumes are Drive file chips."""
from unittest import mock

from job_hunt_agent import gsync, gsync_cycle, gsync_jd, gsync_oneline, jdsnap, resume_upload, trackapi, tracker
from tests.fake_sheets import wrapped_writes
from tests.test_gsync import SyncCase, TAB
from tests.test_resume_upload import PDF_BYTES

POSTING = "https://boards.greenhouse.io/initech/jobs/7"
JD_TEXT = "About the role\n\nBuild ML systems with Python and PyTorch. " * 60
DRIVE = "https://drive.google.com/file/d/{}/view?usp=drivesdk"


class JdFormatTest(SyncCase):
    def setUp(self):
        super().setUp()
        self.link()

    def row_index(self, app_id: int) -> int:
        return next(i for i, r in enumerate(self.grid()) if f"jh:{app_id}" in [str(v) for v in r])

    def jd_cells(self, app_id: int) -> tuple[dict, dict]:
        index = self.row_index(app_id)
        return (self.book.cell(TAB, index, self.column("JD")),
                self.book.cell(TAB, index, self.column(gsync.JD_TEXT_HEADER)))

    def test_the_hidden_jd_text_column_goes_at_the_far_right_once(self):
        gsync_jd.add_columns(self.conn, "jane")
        gsync_jd.add_columns(self.conn, "jane")
        headers = self.grid()[0]
        self.assertEqual(headers.count(gsync.JD_TEXT_HEADER), 1)
        at = headers.index(gsync.JD_TEXT_HEADER)
        self.assertEqual(at, max(i for i, h in enumerate(headers) if h))
        self.assertIn(at, self.sheet["hidden"])

    def test_a_new_app_row_is_a_title_link_with_the_text_hidden_and_a_note(self):
        gsync_jd.add_columns(self.conn, "jane")
        posting = {"ats": "greenhouse", "description": JD_TEXT}
        with mock.patch.object(jdsnap.jobfetch, "fetch_posting", return_value=posting):
            made = trackapi.create(self.conn, "jane", {"company": "Initech", "role_title": "ML Engineer",
                                                       "job_url": POSTING, "status": "applied"})
        app = dict(self.conn.execute("SELECT jd, jd_text FROM applications WHERE id = ?", (made["id"],)).fetchone())
        self.assertEqual(app["jd"], "ML Engineer")
        self.assertTrue(app["jd_text"].startswith("About the role"))
        gsync_cycle.sync_profile(self.conn, "jane", full=True)
        jd, text = self.jd_cells(made["id"])
        self.assertEqual((jd["v"], jd["link"], jd["wrap"]), ("ML Engineer", POSTING, "CLIP"))
        self.assertTrue(jd["note"].startswith("About the role"))
        self.assertLessEqual(len(jd["note"]), gsync_jd.NOTE_CHARS)
        self.assertEqual(text["v"], app["jd_text"])
        self.assertEqual(self.sheet["heights"][self.row_index(made["id"])], gsync_jd.ROW_PX)

    def test_a_url_plus_jd_row_is_converted_and_the_persons_rows_stay(self):
        gamma = self.ids["Gamma"]
        tracker.edit_field(self.conn, gamma, "jd", f"{POSTING}\n\n{JD_TEXT.strip()}")    # the old approved format
        tracker.edit_field(self.conn, self.ids["Beta"], "jd", POSTING)                      # her link-only row
        gsync_cycle.sync_profile(self.conn, "jane", full=True)
        before = {r[0]: r[3] for r in self.grid()[1:] if r and r[0] in ("Acme", "Beta")}
        report = gsync_jd.apply(self.conn, "jane")
        self.assertEqual(report["converted"], [gamma])
        jd, text = self.jd_cells(gamma)
        self.assertEqual((jd["v"], jd["link"], jd["wrap"]), ("AI Engineer", POSTING, "CLIP"))
        self.assertEqual(text["v"], JD_TEXT.strip())
        self.assertEqual(self.sheet["heights"][self.row_index(gamma)], gsync_jd.ROW_PX)
        self.assertEqual({r[0]: r[3] for r in self.grid()[1:] if r and r[0] in ("Acme", "Beta")}, before)
        self.assertEqual(self.conn.execute("SELECT job_url FROM applications WHERE id = ?", (gamma,)).fetchone()[0],
                         POSTING)
        self.assertEqual(gsync_jd.apply(self.conn, "jane")["converted"], [])          # nothing left to do

    def new_row(self) -> int:
        gsync_jd.add_columns(self.conn, "jane")
        with mock.patch.object(jdsnap.jobfetch, "fetch_posting", return_value={"ats": "greenhouse",
                                                                              "description": JD_TEXT}):
            made = trackapi.create(self.conn, "jane", {"company": "Initech", "role_title": "ML Engineer",
                                                       "job_url": POSTING, "status": "applied"})
        gsync_cycle.sync_profile(self.conn, "jane", full=True)
        return made["id"]

    def test_the_sync_is_quiet_after_the_new_format_is_written(self):
        app_id = self.new_row()
        report = gsync_cycle.sync_profile(self.conn, "jane", full=True)[TAB]
        self.assertEqual((report.get("pushed", 0), report.get("pulled", 0)), (0, 0))
        self.assertEqual(self.jd_cells(app_id)[0]["v"], "ML Engineer")

    def test_an_app_change_of_the_jd_text_refreshes_the_note(self):
        app_id = self.new_row()
        tracker.set_jd_text(self.conn, app_id, "Shorter JD")
        gsync_cycle.sync_profile(self.conn, "jane", full=True)
        jd, text = self.jd_cells(app_id)
        self.assertEqual((text["v"], jd["note"], jd["v"], jd["link"]), ("Shorter JD", "Shorter JD", "ML Engineer",
                                                                        POSTING))

    def test_jd_text_typed_in_the_sheet_reaches_the_app(self):
        gsync_jd.add_columns(self.conn, "jane")
        gsync_cycle.sync_profile(self.conn, "jane", full=True)
        with self.fake.lock:
            self.book.cell(TAB, self.row_of("Acme"), self.column(gsync.JD_TEXT_HEADER)).update(
                {"v": "Typed JD text", "formula": None})
        gsync_cycle.sync_profile(self.conn, "jane", full=True)
        self.assertEqual(self.app("Acme")["jd_text"], "Typed JD text")


class ResumeChipTest(SyncCase):
    def test_a_new_row_with_an_upload_shows_the_file_as_a_chip(self):
        self.link()
        made = trackapi.create(self.conn, "jane", {"company": "Delta", "role_title": "ML Engineer", "status": "saved"})
        got = resume_upload.upload(self.conn, "jane", made["id"], "cv.pdf", PDF_BYTES)
        trackapi.update(self.conn, "jane", made["id"], {"status": "applied"})
        self.sync()
        cell = self.book.cell(TAB, self.row_of("Delta"), self.column("Resume"))
        self.assertEqual((cell["v"], cell["wrap"]), (got["name"], "CLIP"))
        self.assertEqual([r["chip"]["richLinkProperties"]["uri"] for r in cell["chips"] if "chip" in r], [got["link"]])
        report = self.sync()
        self.assertEqual((report.get("pushed", 0), report.get("pulled", 0)), (0, 0))


    def test_an_upload_is_a_drive_chip_in_an_empty_cell(self):
        cell = resume_upload.merged_cell({}, "a.pdf", DRIVE.format("A"))
        self.assertEqual(cell["userEnteredValue"], {"stringValue": "@"})
        self.assertEqual(cell["chipRuns"], [{"startIndex": 0, "chip": {"richLinkProperties": {"uri": DRIVE.format("A")}}}])
        self.assertEqual(cell["userEnteredFormat"], {"wrapStrategy": "CLIP"})

    def test_text_and_earlier_chips_are_kept(self):
        typed = resume_upload.merged_cell({"userEnteredValue": {"stringValue": "old.pdf"}}, "b.pdf", DRIVE.format("B"))
        self.assertEqual(typed["userEnteredValue"]["stringValue"], "old.pdf @")
        self.assertEqual(typed["chipRuns"], [{"startIndex": 8, "chip": {"richLinkProperties": {"uri": DRIVE.format("B")}}}])
        chip = {"userEnteredValue": {"stringValue": "a.pdf"}, "formattedValue": "a.pdf",
                "chipRuns": [{"chip": {"richLinkProperties": {"uri": DRIVE.format("A")}}}, {"startIndex": 5}]}
        both = resume_upload.merged_cell(chip, "b.pdf", DRIVE.format("B"))
        self.assertEqual(both["userEnteredValue"]["stringValue"], "@ @")
        self.assertEqual([r["startIndex"] for r in both["chipRuns"] if "chip" in r], [0, 2])

    def test_a_linked_upload_written_before_becomes_a_chip(self):
        linked = {"userEnteredValue": {"stringValue": "x.pdf\ny.docx"},
                  "textFormatRuns": [{"format": {}}, {"startIndex": 6, "format": {"link": {"uri": DRIVE.format("Y")}}}]}
        cell = gsync_jd.chip_cell(linked)
        self.assertEqual(cell["userEnteredValue"]["stringValue"], "x.pdf @")                 # one line
        self.assertEqual(cell["chipRuns"], [{"startIndex": 6, "chip": {"richLinkProperties": {"uri": DRIVE.format("Y")}}}])

    def test_an_upload_linked_before_chips_becomes_a_chip_on_one_line(self):
        self.link()
        got = resume_upload.upload(self.conn, "jane", self.ids["Acme"], "cv.pdf", PDF_BYTES)
        index, col = self.row_of("Acme"), self.column("Resume")
        with self.fake.lock:                                      # the cell as the app wrote it before chips
            self.book.cell(TAB, index, col).update({"v": f"acme.pdf\n{got['name']}", "chips": None, "link": got["link"],
                                                    "runs": [{"startIndex": 0, "format": {}},
                                                             {"startIndex": 9, "format": {"link": {"uri": got["link"]}}}]})
        self.sync()
        self.assertEqual(gsync_jd.chip_resumes(self.conn, "jane"), [self.ids["Acme"]])
        cell = self.book.cell(TAB, index, col)
        self.assertEqual((cell["v"], cell["wrap"]), (f"acme.pdf {got['name']}", "CLIP"))
        self.assertEqual(self.app("Acme")["resume"], cell["v"])
        report = self.sync()
        self.assertEqual((report.get("pushed", 0), report.get("pulled", 0)), (0, 0))


class OneLineWritesTest(SyncCase):
    """User, 2026-10-06 evening: every cell the app writes in either sheet is one clipped line (no row grows)."""
    def test_multi_line_app_values_reach_the_sheet_as_one_clipped_line_and_the_sync_stays_quiet(self):
        self.link()
        self.sync()
        trackapi.update(self.conn, "jane", self.ids["Gamma"], {"role_title": "Staff\nAI Engineer",
                                                                "resume": "a.pdf\nb.pdf", "notes": "x"})
        made = trackapi.create(self.conn, "jane", {"company": "Delta", "role_title": "ML Engineer",
                                                   "resume": "cv.pdf\ncover.pdf", "status": "applied"})
        report = gsync_cycle.sync_profile(self.conn, "jane", full=True)[TAB]
        self.assertEqual((report["pushed"], report["appended"]), (2, 1))
        for company in ("Gamma", "Delta"):
            row = self.grid()[self.row_of(company)]
            self.assertFalse([v for v in row if isinstance(v, str) and "\n" in v], company)
        self.assertEqual(self.grid()[self.row_of("Gamma")][1], "Staff AI Engineer")
        self.assertEqual(wrapped_writes(self.book), [])
        self.assertEqual(self.app("Gamma")["role_title"], "Staff\nAI Engineer")      # the app keeps its lines
        self.assertEqual(self.sheet["heights"][self.row_of("Delta")], gsync_jd.ROW_PX)
        again = self.sync()
        self.assertEqual((again.get("pushed", 0), again.get("pulled", 0)), (0, 0))
        self.assertIsNotNone(made)

    def test_the_guard_catches_a_wrapped_write(self):
        self.link()
        with self.fake.lock:                                      # what the app wrote before 2026-10-06 evening
            self.book.writes.append((TAB, 3, 4, {"userEnteredValue": {"stringValue": "URL\n\nJD"}}, "userEnteredValue"))
        self.assertEqual(len(wrapped_writes(self.book)), 1)
        self.book.writes.clear()


class RowCheckTest(SyncCase):
    """doctor's sheet check: rows taller than normal, and rows the app wrote that are not clipped."""
    def setUp(self):
        super().setUp()
        self.link()
        made = trackapi.create(self.conn, "jane", {"company": "Delta", "role_title": "ML Engineer", "status": "applied"})
        self.sync()
        self.index = next(i for i, r in enumerate(self.grid()) if f"jh:{made['id']}" in [str(v) for v in r])

    def test_rows_the_app_writes_now_pass_and_the_persons_own_wrapping_is_not_flagged(self):
        self.edit("Acme", "JD", {"v": "About\nthe role", "formula": None, "wrap": "WRAP"})   # her own row
        self.assertEqual(gsync_oneline.check(self.conn, "jane")[TAB], {"tall": [], "may_grow": [], "app_rows": 1})
        self.assertTrue(gsync_oneline.doctor_line(self.conn).startswith("sheet rows: ok"))

    def test_a_tall_row_and_an_unclipped_app_row_are_flagged_and_the_fix_clears_them(self):
        with self.fake.lock:                                      # written before every write was one clipped line
            self.book.cell(TAB, self.index, 1)["wrap"] = "WRAP"
            self.sheet["heights"][self.index] = 120
            self.sheet["heights"][self.row_of("Acme")] = 60       # a row the person made tall
        found = gsync_oneline.check(self.conn, "jane")[TAB]
        self.assertEqual((found["tall"], found["may_grow"]), (sorted([self.index + 1, self.row_of("Acme") + 1]),
                                                              [self.index + 1]))
        self.assertIn("PROBLEM", gsync_oneline.doctor_line(self.conn))
        self.assertEqual(gsync_oneline.fix_app_rows(self.conn, "jane"), {TAB: 1})
        found = gsync_oneline.check(self.conn, "jane")[TAB]
        self.assertEqual((found["tall"], found["may_grow"]), ([self.row_of("Acme") + 1], []))  # hers: only flagged
        self.assertEqual(self.book.cell(TAB, self.index, 1)["wrap"], "CLIP")
