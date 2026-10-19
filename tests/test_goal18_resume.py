"""an upload / Replace leaves the Sheet's Resume cell a Drive file chip. A stale page that sends the
replaced file's name back is refused (row 392), resume_repair finds a bare file name and makes it the chip again, and
a chip the person's own token cannot see (400 "Error fetching chip data: Permission denied") is written with the
owner's token."""
import unittest

from job_hunt_agent import resume_repair, resume_upload, server, db, trackapi
from job_hunt_agent.google import GoogleError, accounts
from tests.test_goal15_resume import ResumeSwapCase
from tests.test_resume_upload import PDF_BYTES


class StaleWriteTest(ResumeSwapCase):
    def replaced(self) -> tuple[dict, dict]:
        old = resume_upload.upload(self.conn, "jane", self.ids["Gamma"], "a.pdf", PDF_BYTES)
        new = resume_upload.upload(self.conn, "jane", self.ids["Gamma"], "b.pdf", PDF_BYTES, replace=True)
        return old, new

    def page(self, text: str) -> dict:
        return server.route(self.conn, "POST", "/api/tracker/update",
                            {"profile": "jane", "app": self.ids["Gamma"], "changes": {"resume": text}}, db.utcnow())

    def test_the_replaced_files_name_sent_back_is_refused_and_the_chip_stays(self):
        old, new = self.replaced()
        with self.assertRaisesRegex(ValueError, "replaced by .*reload"):
            self.page(old["name"])
        self.sync()
        self.assertEqual((self.cell("Gamma")["v"], self.chips("Gamma")), (new["name"], [new["link"]]))

    def test_the_server_route_says_why(self):
        old, _ = self.replaced()
        with self.assertRaisesRegex(ValueError, "replaced by"):
            server.route(self.conn, "POST", "/api/tracker/update",
                         {"profile": "jane", "app": self.ids["Gamma"], "changes": {"resume": old["name"]}},
                         db.utcnow())

    def test_other_text_and_the_current_name_are_still_saved(self):
        _, new = self.replaced()
        self.page(f"{new['name']} (sent by email)")
        self.page("typed by hand")
        self.assertEqual(self.app("Gamma")["resume"], "typed by hand")

    def test_the_apps_own_writes_are_not_checked(self):
        old, _ = self.replaced()                     # Undo's fallback puts the old text back (verifier D2)
        trackapi.update(self.conn, "jane", self.ids["Gamma"], {"resume": old["name"]})
        self.assertEqual(self.app("Gamma")["resume"], old["name"])

    def test_another_persons_row_says_nothing(self):
        old, _ = self.replaced()
        with self.assertRaises(KeyError):
            trackapi.not_stale_resume(self.conn, "sri", self.ids["Gamma"], {"resume": old["name"]})

    def test_a_row_without_uploads_takes_any_text(self):
        trackapi.update(self.conn, "jane", self.ids["Acme"], {"resume": "Jane_Doe_Resume.pdf"})
        self.assertEqual(self.app("Acme")["resume"], "Jane_Doe_Resume.pdf")


class RepairTest(ResumeSwapCase):
    def test_a_bare_name_is_found_and_made_the_current_files_chip(self):
        old = resume_upload.upload(self.conn, "jane", self.ids["Gamma"], "a.pdf", PDF_BYTES)
        new = resume_upload.upload(self.conn, "jane", self.ids["Gamma"], "b.pdf", PDF_BYTES, replace=True)
        self.edit("Gamma", "Resume", {"v": old["name"], "formula": None, "chips": [], "runs": []})   # row 392
        found = resume_repair.repair(self.conn, "jane")
        self.assertEqual([(r["app"], r["verdict"], r["to"], r["result"]) for r in found],
                         [(self.ids["Gamma"], "bare", new["name"], "dry run")])
        self.assertEqual(self.chips("Gamma"), [], "a dry run writes nothing")
        resume_repair.repair(self.conn, "jane", apply=True)
        self.assertEqual((self.cell("Gamma")["v"], self.chips("Gamma")), (new["name"], [new["link"]]))
        self.assertEqual(resume_repair.repair(self.conn, "jane"), [], "nothing left")
        self.sync()
        self.assertEqual(self.chips("Gamma"), [new["link"]], "the next sync keeps it")

    def test_chips_and_typed_cells_are_left_alone(self):
        up = resume_upload.upload(self.conn, "jane", self.ids["Gamma"], "a.pdf", PDF_BYTES)
        self.sync()
        self.assertEqual([r["verdict"] for r in resume_repair.scan(self.conn, "jane")], ["chip"])
        self.edit("Gamma", "Resume", {"v": "Resume.pdf(Gamma)", "formula": None, "chips": [], "runs": []})
        self.assertEqual([r["verdict"] for r in resume_repair.scan(self.conn, "jane")], ["other"])
        self.assertEqual(resume_repair.repair(self.conn, "jane", apply=True), [])
        self.assertEqual(self.cell("Gamma")["v"], "Resume.pdf(Gamma)", "typed text untouched")
        self.assertTrue(up["link"])


class VerdictTest(unittest.TestCase):
    CUR = [("A(Amazon)_v2.pdf", "f2")]
    OLD = [("A(Amazon).pdf", "f1")]

    def test_verdicts(self):
        self.assertEqual(resume_repair.verdict("A(Amazon)_v2.pdf", {"f2"}, self.CUR, self.OLD), "chip")
        self.assertEqual(resume_repair.verdict("A(Amazon).pdf", set(), self.CUR, self.OLD), "bare")
        self.assertEqual(resume_repair.verdict("A(Amazon)_v2.pdf", set(), self.CUR, self.OLD), "bare")
        self.assertEqual(resume_repair.verdict("my note", set(), self.CUR, self.OLD), "other")
        self.assertEqual(resume_repair.verdict("A(Amazon).pdf", set(), [], self.OLD), "other")

    def test_file_ids_from_chips_links_and_hyperlinks(self):
        cell = {"chipRuns": [{"chip": {"richLinkProperties": {"uri": "https://drive.google.com/file/d/abcdefghijk1/view"}}}],
                "textFormatRuns": [{"format": {"link": {"uri": "https://drive.google.com/open?id=abcdefghijk2"}}}],
                "hyperlink": "https://example.com/x"}
        self.assertEqual(resume_repair.cell_files(cell), {"abcdefghijk1", "abcdefghijk2"})


class RefusedTest(unittest.TestCase):
    def test_a_chip_the_token_cannot_see_falls_back_to_the_owner(self):
        chip = GoogleError("400 INVALID_ARGUMENT Invalid requests[0].updateCells: Error fetching chip data: "
                           "Permission denied.", 400)
        self.assertTrue(accounts.refused(chip))
        self.assertTrue(accounts.refused(GoogleError("no", 403)))
        self.assertFalse(accounts.refused(GoogleError("Invalid range", 400)))
        self.assertFalse(accounts.refused(GoogleError("boom", 500)))


if __name__ == "__main__":
    unittest.main()
