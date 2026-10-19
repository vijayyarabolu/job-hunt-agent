"""GOAL4 §4 addition: tracker "Upload resume (PDF/DOCX)" -> a new Drive file in job-hunt-agent/Resumes/<Profile>/, linked in
the real Sheet's Resume cell (existing text and links kept), shared with the person when configured, never deleting
or overwriting an earlier upload."""
import io
import zipfile
from unittest import mock

from job_hunt_agent import resume_upload, server, trackapi
from job_hunt_agent.google import FOLDER
from tests.test_gsync import TAB, SyncCase

PDF_BYTES = b"%PDF-1.7\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def docx_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<w:document/>")
    return buf.getvalue()


class CheckFileTest(SyncCase):
    def test_pdf_and_docx_are_accepted_by_their_bytes(self):
        self.assertEqual(resume_upload.check_file("cv.PDF", PDF_BYTES)[0], ".pdf")
        self.assertEqual(resume_upload.check_file("cv.docx", docx_bytes())[0], ".docx")

    def test_anything_else_is_refused_with_a_plain_reason(self):
        cases = [("cv.txt", b"hello", "only PDF or DOCX"), ("cv.pdf", b"", "empty"),
                 ("cv.pdf", b"PK\x03\x04 not a pdf", "not a PDF"), ("cv.docx", PDF_BYTES, "not a Word"),
                 ("cv.pdf", b"%PDF-" + b"x" * resume_upload.MAX_BYTES, "over 10 MB")]
        for name, data, reason in cases:
            with self.subTest(name=name, reason=reason), self.assertRaisesRegex(ValueError, reason):
                resume_upload.check_file(name, data)


class NamingTest(SyncCase):
    def test_the_persons_pattern_with_the_role_title_and_versions(self):
        app = {"role_title": "Senior ML Engineer, Platform (GenAI)", "company": "Acme, Inc."}
        profile = {"id": "jane", "name": "Jane Doe", "resume_file_pattern": "{name}_{title}_Resume({company}).pdf"}
        self.assertEqual(resume_upload.file_name(profile, app, ".pdf", 1),
                         "Jane_Doe_Senior_ML_Engineer_Platform_GenAI_Resume(Acme, Inc.).pdf")
        self.assertEqual(resume_upload.file_name(profile, app, ".docx", 3),
                         "Jane_Doe_Senior_ML_Engineer_Platform_GenAI_Resume(Acme, Inc.)_v3.docx")
        jordan = {"id": "s", "name": "Jordan Lee", "resume_file_pattern": "{name}_{title}({company}).pdf"}
        self.assertEqual(resume_upload.file_name(jordan, {"role_title": "AI Engineer", "company": "Beta/Co"}, ".pdf", 1),
                         "Jordan_Lee_AI_Engineer(Beta Co).pdf")

    def test_a_bad_pattern_is_a_plain_error(self):
        with self.assertRaisesRegex(ValueError, "use only"):
            resume_upload.file_name({"id": "j", "resume_file_pattern": "{name}_{role}.pdf"}, {}, ".pdf", 1)


class MergedCellTest(SyncCase):
    """The upload is a Drive file chip (user, 2026-10-06 evening: like the older rows); see test_goal6_jdformat."""
    def chips(self, cell):
        return [(r["startIndex"], r["chip"]["richLinkProperties"]["uri"]) for r in cell["chipRuns"] if "chip" in r]

    def test_an_empty_cell_becomes_the_files_chip(self):
        cell = resume_upload.merged_cell({}, "a.pdf", "https://drive.google.com/file/d/a/view")
        self.assertEqual(cell["userEnteredValue"], {"stringValue": "@"})
        self.assertEqual(self.chips(cell), [(0, "https://drive.google.com/file/d/a/view")])

    def test_existing_text_and_its_cell_link_stay(self):
        cell = resume_upload.merged_cell({"formattedValue": "old.pdf", "hyperlink": "https://old"}, "a.pdf",
                                         "https://drive.google.com/file/d/a/view")
        self.assertEqual(cell["userEnteredValue"], {"stringValue": "old.pdf @"})
        self.assertEqual(cell["textFormatRuns"][0], {"startIndex": 0, "format": {"link": {"uri": "https://old"}}})
        self.assertEqual(self.chips(cell), [(8, "https://drive.google.com/file/d/a/view")])

    def test_indexes_count_utf16_units(self):
        cell = resume_upload.merged_cell({"formattedValue": "cv 📄"}, "a.pdf", "https://drive.google.com/file/d/a/view")
        self.assertEqual(self.chips(cell), [(6, "https://drive.google.com/file/d/a/view")])      # 📄 is 2 UTF-16 units
        self.assertEqual(resume_upload.shown_text({"formattedValue": "cv 📄"}, "a.pdf"), "cv 📄 a.pdf")


class UploadTest(SyncCase):
    def setUp(self):
        super().setUp()
        self.link()

    def resume_cell(self, company: str) -> dict:
        return self.book.cell(TAB, self.row_of(company), self.column("Resume"))

    def chips(self, company: str) -> list[str]:
        return [r["chip"]["richLinkProperties"]["uri"] for r in self.resume_cell(company).get("chips") or []
                if "chip" in r]

    def drive_files(self, mime=None) -> list[dict]:
        return [f for f in self.fake.files.values() if mime is None or f["mimeType"] == mime]

    def test_upload_goes_to_the_profile_folder_and_links_the_resume_cell(self):
        got = resume_upload.upload(self.conn, "jane", self.ids["Acme"], "my cv.pdf", PDF_BYTES)
        folders = {f["name"]: f for f in self.drive_files(FOLDER)}
        self.assertEqual(set(folders), {"job-hunt-agent", "Resumes", "Jane"})
        self.assertEqual(folders["Resumes"]["parents"], [folders["job-hunt-agent"]["id"]])
        self.assertEqual(folders["Jane"]["parents"], [folders["Resumes"]["id"]])
        pdf = next(f for f in self.drive_files("application/pdf"))
        self.assertEqual(pdf["name"], "Jane_Doe_ML_Engineer_Resume(Acme).pdf")
        self.assertEqual(pdf["parents"], [folders["Jane"]["id"]])
        self.assertEqual(pdf["content"], PDF_BYTES)
        cell = self.resume_cell("Acme")
        self.assertEqual(cell["v"], "acme.pdf Jane_Doe_ML_Engineer_Resume(Acme).pdf")       # old text kept
        self.assertEqual(self.chips("Acme"), [got["link"]])                             # the file as a chip
        self.assertEqual(got["sheet"], f"{TAB} row 2, column E")
        self.assertEqual(self.app("Acme")["resume"], cell["v"])                         # the app has it at once
        first, second = self.sync(), self.sync()
        self.assertEqual([r.get(k, 0) for r in (first, second) for k in ("pushed", "pulled")], [0, 0, 0, 0])
        self.assertEqual(trackapi.get(self.conn, "jane", self.ids["Acme"])["uploads"][0]["link"], got["link"])

    def test_a_re_upload_is_a_new_version_and_nothing_is_deleted_or_overwritten(self):
        first = resume_upload.upload(self.conn, "jane", self.ids["Gamma"], "a.pdf", PDF_BYTES)
        second = resume_upload.upload(self.conn, "jane", self.ids["Gamma"], "b.docx", docx_bytes())
        self.assertEqual((first["version"], second["version"]), (1, 2))
        self.assertEqual(second["name"], "Jane_Doe_AI_Engineer_Resume(Gamma)_v2.docx")
        files = [f for f in self.drive_files() if f["mimeType"] != FOLDER]
        self.assertEqual(len(files), 2)
        self.assertFalse(any(f["trashed"] for f in files))
        self.assertEqual(self.fake.count("DELETE", r"/drive/"), 0)
        self.assertEqual(self.fake.count("PATCH", r"/drive/"), 0)
        cell = self.resume_cell("Gamma")
        self.assertEqual(cell["v"], f"{first['name']} {second['name']}")
        self.assertEqual(self.chips("Gamma"), [first["link"], second["link"]])

    def test_an_existing_link_in_the_cell_is_kept(self):
        self.edit("Acme", "Resume", {"v": "acme.pdf", "link": "https://example.com/acme.pdf"})
        got = resume_upload.upload(self.conn, "jane", self.ids["Acme"], "cv.pdf", PDF_BYTES)
        runs = self.resume_cell("Acme")["runs"]
        self.assertEqual(runs[0]["format"]["link"]["uri"], "https://example.com/acme.pdf")
        self.assertEqual(self.chips("Acme"), [got["link"]])

    def test_the_folder_is_shared_once_with_the_configured_person(self):
        self.add_profile("jane", "name: Jane Doe\nfamilies: [ml_engineer]\ndrive_share_with: jane@example.com\n")
        resume_upload.upload(self.conn, "jane", self.ids["Acme"], "cv.pdf", PDF_BYTES)
        resume_upload.upload(self.conn, "jane", self.ids["Beta"], "cv.pdf", PDF_BYTES)
        folder = next(f for f in self.drive_files(FOLDER) if f["name"] == "Jane")
        self.assertEqual(self.fake.permissions, [{"file": folder["id"], "type": "user", "role": "writer",
                                                  "emailAddress": "jane@example.com"}])

    def test_folders_are_created_once(self):
        for company in ("Acme", "Beta"):
            resume_upload.upload(self.conn, "jane", self.ids[company], "cv.pdf", PDF_BYTES)
        self.assertEqual(len(self.drive_files(FOLDER)), 3)

    def test_a_row_not_on_the_sheet_yet_gets_the_link_when_it_is_written(self):
        row = trackapi.create(self.conn, "jane", {"company": "Delta", "role_title": "ML Engineer", "status": "saved"})
        got = resume_upload.upload(self.conn, "jane", row["id"], "cv.pdf", PDF_BYTES)
        self.assertIn("not on the Sheet yet", got["sheet"])
        trackapi.update(self.conn, "jane", row["id"], {"status": "applied"})
        self.sync()
        self.assertEqual((self.resume_cell("Delta")["v"], self.chips("Delta")), (got["name"], [got["link"]]))

    def test_a_refused_file_never_reaches_drive(self):
        with self.assertRaisesRegex(ValueError, "not a PDF"):
            resume_upload.upload(self.conn, "jane", self.ids["Acme"], "cv.pdf", b"nope")
        self.assertEqual(self.fake.count("POST", r"/drive/v3|/upload/"), 0)

    def test_another_profiles_row_is_not_found(self):
        self.add_profile("sam", "name: Sam\nfamilies: [ml_engineer]\n")
        with self.assertRaises(KeyError):
            resume_upload.upload(self.conn, "sam", self.ids["Acme"], "cv.pdf", PDF_BYTES)

    def test_the_server_route_takes_the_raw_file_body(self):
        got = server.route(self.conn, "POST", server.UPLOAD_PATH,
                           {"profile": "jane", "app": str(self.ids["Beta"]), "name": "cv.pdf", "data": PDF_BYTES}, None)
        self.assertEqual(got["name"], "Jane_Doe_Data_Scientist_Resume(Beta).pdf")
        with self.assertRaisesRegex(ValueError, "file itself"):
            server.route(self.conn, "POST", server.UPLOAD_PATH, {"profile": "jane", "app": "1"}, None)

    def test_a_formula_cell_is_left_alone_and_the_file_still_uploads(self):
        self.edit("Acme", "Resume", {"v": None, "formula": '=HYPERLINK("https://x/cv.pdf","cv")'})
        got = resume_upload.upload(self.conn, "jane", self.ids["Acme"], "cv.pdf", PDF_BYTES)
        self.assertIn("formula", got["sheet"])
        self.assertEqual(self.resume_cell("Acme")["formula"], '=HYPERLINK("https://x/cv.pdf","cv")')
        self.assertEqual(len(self.drive_files("application/pdf")), 1)

    def sort_once_after(self, name: str, raced: list):
        real = getattr(resume_upload, name)

        def then_sort(*args):
            out = real(*args)
            if not raced:                                   # someone sorts right after this call, once
                raced.append(True)
                with self.fake.lock:
                    grid = self.sheet["grid"]
                    grid[1], grid[2] = grid[2], grid[1]
            return out
        return mock.patch.object(resume_upload, name, then_sort)

    def test_a_sort_between_read_and_write_is_undone_and_the_link_lands_on_the_right_row(self):
        with self.sort_once_after("_read_cell", []):
            got = resume_upload.upload(self.conn, "jane", self.ids["Beta"], "cv.pdf", PDF_BYTES)
        self.assertEqual(self.chips("Beta"), [got["link"]])
        self.assertEqual(self.resume_cell("Beta")["v"], got["name"])
        self.assertEqual(self.resume_cell("Acme")["v"], "acme.pdf")              # the row it hit got its text back
        self.assertEqual(self.chips("Acme"), [])

    def test_a_sort_right_after_the_write_needs_no_undo(self):
        with self.sort_once_after("_write", []):
            got = resume_upload.upload(self.conn, "jane", self.ids["Beta"], "cv.pdf", PDF_BYTES)
        self.assertEqual(self.chips("Beta"), [got["link"]])
        self.assertEqual(self.resume_cell("Acme")["v"], "acme.pdf")
        self.assertEqual(got["sheet"], f"{TAB} row 2, column E")                 # where the row is now
