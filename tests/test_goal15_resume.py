"""× removes a row's resume (the Drive file kept, moved to Resumes/<Profile>/Replaced/, Undo), Replace ▾
uploads or links a ready resumegen version in its place (one chip, never two), Generate again queues it, and an
Approve for an applied job replaces that row's resume. Against the fake Google (Sheet + Drive) and a fake resumegen."""
from unittest import mock

from job_hunt_agent import db, gdrive_upload, resume_swap, resume_upload, rgclient, rgjobs, server, trackapi
from job_hunt_agent.google import FOLDER
from tests.test_gsync import TAB, SyncCase
from tests.test_resume_upload import PDF_BYTES

UID = "lever:gamma:1"
APPROVED = "Jane_Doe_AI_Engineer_Resume(Gamma).pdf"


class ResumeSwapCase(SyncCase):
    def setUp(self):
        super().setUp()
        self.link()
        with db.tx(self.conn):
            self.conn.execute("UPDATE applications SET job_uid = ? WHERE id = ?", (UID, self.ids["Gamma"]))
        self.rg_jobs: dict[int, dict] = {}
        self.rg_calls: list[tuple] = []
        self.rg_patch = mock.patch.object(rgclient, "request", self._resumegen)
        self.rg_patch.start()

    def tearDown(self):
        self.rg_patch.stop()
        super().tearDown()

    # ---- a fake resumegen: its jobs; Approve uploads the PDF to Drive like the real one ----
    def _resumegen(self, method, path, body=None, **kw):
        self.rg_calls.append((method, path, body))
        if method == "GET" and path.startswith("/api/jobs?"):
            return {"jobs": list(self.rg_jobs.values())}
        if method == "GET" and path.startswith("/api/jobs/"):
            return {"job": self.rg_jobs[int(path.rsplit("/", 1)[-1])]}
        if method == "POST" and path.endswith("/approve"):
            job = self.rg_jobs[int(path.split("/")[3])]
            folder = gdrive_upload.profile_folder(self.conn, "jane")
            made = gdrive_upload.upload(folder["id"], APPROVED, PDF_BYTES, "application/pdf", {"rg": job["id"]})
            job.update(drive_file_id=made["id"], drive_link=made["webViewLink"], drive_name=made["name"])
            return {"ok": True}
        if method == "POST" and path == "/api/jobs":
            return {"ids": [99]}
        return {"ok": True}

    def ready_job(self, rg_id: int = 7) -> dict:
        self.rg_jobs[rg_id] = {"id": rg_id, "profile": "jane", "status": "ready", "source_job_id": UID,
                               "company": "Gamma", "role": "AI Engineer", "finished_at": "2026-10-08T17:00:00Z"}
        return self.rg_jobs[rg_id]

    # ---- reading the result ----
    def cell(self, company: str) -> dict:
        return self.book.cell(TAB, self.row_of(company), self.column("Resume"))

    def chips(self, company: str) -> list[str]:
        return [r["chip"]["richLinkProperties"]["uri"] for r in self.cell(company).get("chips") or [] if "chip" in r]

    def parent(self, link: str) -> dict:
        file = next(f for f in self.fake.files.values() if f.get("webViewLink") == link)
        return self.fake.files[file["parents"][0]]

    def current(self, company: str) -> list[str]:
        return [f["name"] for f in resume_swap.current(self.conn, self.ids[company])]


class RemoveTest(ResumeSwapCase):
    def test_x_clears_the_row_keeps_the_file_in_replaced_and_undo_brings_it_back(self):
        up = resume_upload.upload(self.conn, "jane", self.ids["Acme"], "cv.pdf", PDF_BYTES)
        before = self.cell("Acme")["v"]
        got = resume_swap.act(self.conn, "jane", {"act": "remove", "app": self.ids["Acme"]})
        self.assertEqual((got["row"]["has_resume"], got["row"]["has_upload"], got["row"]["resume"]), (False, False, None))
        self.assertEqual((self.cell("Acme").get("v") or "", self.chips("Acme")), ("", []), "written at once")
        self.assertEqual(self.current("Acme"), [])
        replaced = self.parent(up["link"])
        self.assertEqual(replaced["name"], "Replaced")
        self.assertEqual(self.fake.files[replaced["parents"][0]]["name"], "Jane", "in the person's Resumes folder")
        self.assertEqual(self.fake.count("DELETE", r"/drive/"), 0, "nothing deleted")
        self.sync()
        self.assertEqual((self.cell("Acme").get("v") or "", self.chips("Acme")), ("", []))
        back = resume_swap.act(self.conn, "jane", {"act": "undo", "app": self.ids["Acme"], "id": got["undo"]})
        self.assertTrue(back["row"]["has_upload"])
        self.assertEqual(self.parent(up["link"])["name"], "Jane")
        self.sync()
        self.assertEqual((self.cell("Acme")["v"], self.chips("Acme")), (before, [up["link"]]))
        with self.assertRaisesRegex(ValueError, "nothing to undo"):
            resume_swap.undo(self.conn, "jane", got["undo"])

    def test_a_sheet_only_resume_keeps_its_link_through_x_and_undo(self):
        drive = "https://drive.google.com/file/d/SHEETONLY123/view"
        self.edit("Acme", "Resume", {"v": "acme.pdf", "formula": None, "link": drive})
        self.sync()
        before = {k: self.cell("Acme").get(k) for k in ("v", "link", "runs", "chips")}
        got = resume_swap.remove(self.conn, "jane", self.ids["Acme"])
        self.assertEqual((self.cell("Acme").get("v") or "", self.chips("Acme")), ("", []))
        resume_swap.undo(self.conn, "jane", got["undo"])
        after = {k: self.cell("Acme").get(k) for k in ("v", "link", "runs", "chips")}
        self.assertIn(drive, repr(after), after)
        self.assertEqual(after["v"], before["v"])                         # the whole text still links the file
        self.assertEqual([r["format"]["link"]["uri"] for r in after["runs"] or [] if r["format"]] or [after["link"]],
                         [before["link"]])
        self.assertEqual(trackapi.get(self.conn, "jane", self.ids["Acme"])["resume"], "acme.pdf")

    def test_an_undo_after_a_later_change_is_refused(self):
        resume_upload.upload(self.conn, "jane", self.ids["Gamma"], "a.pdf", PDF_BYTES)
        first = resume_upload.upload(self.conn, "jane", self.ids["Gamma"], "b.pdf", PDF_BYTES, replace=True)
        resume_upload.upload(self.conn, "jane", self.ids["Gamma"], "c.pdf", PDF_BYTES, replace=True)
        with self.assertRaisesRegex(ValueError, "changed since"):
            resume_swap.undo(self.conn, "jane", first["undo"])
        self.assertEqual(len(resume_swap.current(self.conn, self.ids["Gamma"])), 1, "never two current files")

    def test_a_row_with_no_resume_has_nothing_to_remove(self):
        with self.assertRaisesRegex(ValueError, "no resume"):
            resume_swap.remove(self.conn, "jane", self.ids["Gamma"])

    def test_another_persons_undo_is_refused(self):
        resume_upload.upload(self.conn, "jane", self.ids["Acme"], "cv.pdf", PDF_BYTES)
        got = resume_swap.remove(self.conn, "jane", self.ids["Acme"])
        with self.assertRaisesRegex(ValueError, "nothing to undo"):
            resume_swap.undo(self.conn, "sri", got["undo"])


class ReplaceTest(ResumeSwapCase):
    def test_replace_upload_leaves_one_chip_and_undo_restores_the_old_one(self):
        old = resume_upload.upload(self.conn, "jane", self.ids["Gamma"], "a.pdf", PDF_BYTES)
        new = resume_upload.upload(self.conn, "jane", self.ids["Gamma"], "b.pdf", PDF_BYTES, replace=True)
        self.assertEqual(self.current("Gamma"), [new["name"]])
        self.assertEqual(self.parent(old["link"])["name"], "Replaced")
        self.assertEqual(new["row"]["resume"], new["name"])
        self.sync()
        self.assertEqual((self.cell("Gamma")["v"], self.chips("Gamma")), (new["name"], [new["link"]]), "one chip")
        resume_swap.undo(self.conn, "jane", new["undo"])
        self.assertEqual(self.current("Gamma"), [old["name"]])
        self.assertEqual(self.parent(new["link"])["name"], "Replaced")
        self.sync()
        self.assertEqual((self.cell("Gamma")["v"], self.chips("Gamma")), (old["name"], [old["link"]]))

    def test_the_server_route_takes_replace(self):
        resume_upload.upload(self.conn, "jane", self.ids["Gamma"], "a.pdf", PDF_BYTES)
        got = server.route(self.conn, "POST", "/api/tracker/resume",
                           {"profile": "jane", "app": str(self.ids["Gamma"]), "name": "b.pdf", "replace": "1",
                            "data": PDF_BYTES}, db.utcnow())
        self.assertEqual(self.current("Gamma"), [got["name"]])

    def test_a_ready_version_is_approved_to_drive_and_linked(self):
        old = resume_upload.upload(self.conn, "jane", self.ids["Gamma"], "a.pdf", PDF_BYTES)
        self.ready_job(7)
        html = resume_swap.act(self.conn, "jane", {"act": "menu", "app": self.ids["Gamma"]})["html"]
        for part in ('value="7"', "#7 · ready · 2026-10-08 17:00", 'data-r="upload"', 'data-r="generate"'):
            self.assertIn(part, html)
        got = resume_swap.act(self.conn, "jane", {"act": "use", "app": self.ids["Gamma"], "id": "7"})
        self.assertIn(("POST", "/api/jobs/7/approve", {}), self.rg_calls)
        self.assertEqual(self.current("Gamma"), [APPROVED])
        self.assertEqual(self.chips("Gamma"), [self.rg_jobs[7]["drive_link"]], "same name, the new file's chip")
        self.sync()
        self.assertEqual(self.chips("Gamma"), [self.rg_jobs[7]["drive_link"]])
        self.assertIn("on this row", resume_swap.menu(self.conn, "jane", self.ids["Gamma"]))
        resume_swap.undo(self.conn, "jane", got["undo"])
        self.assertEqual(self.current("Gamma"), [old["name"]])

    def test_another_jobs_version_is_refused(self):
        self.ready_job(7)["source_job_id"] = "lever:other:2"
        with self.assertRaisesRegex(ValueError, "not one of this job's"):
            resume_swap.use(self.conn, "jane", self.ids["Gamma"], 7)

    def test_generate_again_queues_the_rows_job(self):
        with mock.patch.object(rgjobs, "_item", lambda conn, pid, uid: {"source_job_id": uid}):
            got = resume_swap.act(self.conn, "jane", {"act": "generate", "app": self.ids["Gamma"]})
        self.assertIn("Resumes → Ready", got["msg"])
        self.assertIn(("POST", "/api/jobs", {"jobs": [{"source_job_id": UID}]}), self.rg_calls)


class ApproveTest(ResumeSwapCase):
    def approve(self, rg_id: int) -> dict:
        return server.route(self.conn, "POST", "/api/resumes/action",
                            {"profile": "jane", "id": rg_id, "action": "approve"}, db.utcnow())

    def test_approving_for_an_applied_row_replaces_its_resume(self):
        old = resume_upload.upload(self.conn, "jane", self.ids["Gamma"], "a.pdf", PDF_BYTES)
        self.ready_job(8)
        self.assertIn(f"Linked {APPROVED}", self.approve(8)["row_note"])
        self.assertEqual(self.current("Gamma"), [APPROVED])
        self.assertEqual(self.parent(old["link"])["name"], "Replaced")
        self.sync()
        self.assertEqual(len(self.chips("Gamma")), 1, "never a second chip")

    def test_the_row_typed_for_the_same_posting_gets_it_when_the_jobs_own_row_is_only_saved(self):
        # Arrange: Gamma (the job's own row) only saved; Acme typed into the Sheet, applied, same posting URL
        # (Jordan's #1380 / #1381)
        with db.tx(self.conn):
            self.conn.execute("UPDATE applications SET status = 'saved' WHERE id = ?", (self.ids["Gamma"],))
            self.conn.execute("UPDATE applications SET status = 'applied', job_uid = NULL WHERE id = ?",
                              (self.ids["Acme"],))
            self.conn.execute("INSERT OR REPLACE INTO jd_snapshots(app_id, url, state) VALUES (?, ?, 'saved')",
                              (self.ids["Acme"], "https://jobs.example/gamma/1"))
        self.ready_job(8)["jd_url"] = "https://jobs.example/gamma/1"
        # Act
        note = self.approve(8)["row_note"]
        # Assert
        self.assertIn(f"Linked {APPROVED}", note)
        self.assertEqual(self.current("Acme"), [APPROVED])
        self.assertEqual(self.current("Gamma"), [], "the saved row is left alone")

    def test_a_job_not_applied_to_is_left_alone(self):
        with db.tx(self.conn):
            self.conn.execute("UPDATE applications SET status = 'saved' WHERE id = ?", (self.ids["Gamma"],))
        self.ready_job(8)
        self.assertIsNone(self.approve(8)["row_note"])
        self.assertEqual(self.current("Gamma"), [])


class FlagsTest(ResumeSwapCase):
    def test_a_row_has_a_resume_by_its_text_or_a_current_file(self):
        self.assertTrue(trackapi.get(self.conn, "jane", self.ids["Acme"])["has_resume"], "text from the Sheet counts")
        self.assertFalse(trackapi.get(self.conn, "jane", self.ids["Gamma"])["has_resume"])
