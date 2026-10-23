"""a person's Google side runs on their own Google account. Against the fake Google, which knows which
account each token belongs to (files record their owner)."""
import os
import stat
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

from job_hunt_agent import db, google_move, google_page, jd_doc, resume_upload, rgclient
from job_hunt_agent.google import accounts, auth
from tests.google_case import PROFILE_YAML
from tests.test_gsync import TAB, SyncCase
from tests.test_jd_doc import POSTING, RAW_JD
from tests.test_resume_upload import PDF_BYTES

JANE = "jane@example.com"
OWNER = "owner@example.com"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def consent_answer(url: str) -> str:
    """What the browser's address bar shows after consent (Google's redirect target)."""
    try:
        urllib.request.build_opener(_NoRedirect).open(url)
    except urllib.error.HTTPError as err:
        return err.headers["Location"]
    raise AssertionError("no redirect from the consent page")


class OwnAccountCase(SyncCase):
    def setUp(self):
        super().setUp()
        self.add_profile("jane", PROFILE_YAML + f"drive_share_with: {JANE}\n")
        self.link()

    def connect_jane(self) -> dict:
        self.fake.next_email = JANE
        url = accounts.start("jane", 8765)
        self.assertIn("login_hint=jane%40example.com", url)
        return accounts.finish("jane", consent_answer(url))

    def owners(self, name_part: str) -> set[str]:
        return {f["owners"][0]["emailAddress"] for f in self.fake.files.values() if name_part in f["name"]}


class SignIn(OwnAccountCase):
    def test_the_person_signs_in_with_their_own_address_and_the_token_is_private(self):
        self.assertFalse(accounts.connected("jane"))
        self.assertEqual(self.connect_jane(), {"email": JANE})
        path = auth.token_path("jane")
        self.assertEqual(path.name, "google_token.jane.json")
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertTrue(accounts.connected("jane"))
        self.assertNotEqual(auth.load()["refresh_token"], auth.load("jane")["refresh_token"], "two tokens")

    def test_another_google_account_is_refused(self):
        self.fake.next_email = "someone@else.com"
        url = accounts.start("jane", 8765)
        with self.assertRaisesRegex(ValueError, "signed in as someone@else.com"):
            accounts.finish("jane", consent_answer(url))
        self.assertFalse(accounts.connected("jane"))

    def test_a_stale_or_garbled_answer_is_refused(self):
        answer = consent_answer(accounts.start("jane", 8765))
        accounts.start("jane", 8765)                                   # a newer Connect replaces the waiting one
        with self.assertRaisesRegex(ValueError, "another sign-in"):
            accounts.finish("jane", answer)
        with self.assertRaisesRegex(ValueError, "whole address"):
            accounts.finish("jane", "hello")

    def test_disconnect_revokes_and_the_owner_takes_over(self):
        self.connect_jane()
        self.assertTrue(accounts.disconnect("jane"))
        self.assertFalse(auth.token_path("jane").exists())
        with accounts.as_profile("jane") as account:
            self.assertIsNone(account)

    def test_the_page_connects_and_its_links_carry_a_tag(self):
        self.assertIn("Connect jane@example.com", google_page.render("jane"))
        with self.assertRaises(PermissionError):
            google_page.disconnect("jane", "forged")
        self.fake.next_email = JANE
        url = google_page.connect_url("jane", google_page._tag("connect", "jane"), 8765)
        with mock.patch.object(google_page.google_move, "soon") as move:
            page = google_page.finish("jane", google_page._tag("finish", "jane"), consent_answer(url))
        self.assertIn("Connected as jane@example.com", page)
        move.assert_called_once_with("jane")


class FilesLandInTheirDrive(OwnAccountCase):
    def test_before_sign_in_the_owner_makes_files_and_shares_the_folder_without_mail(self):
        resume_upload.upload(self.conn, "jane", self.ids["Acme"], "cv.pdf", PDF_BYTES)
        self.assertEqual(self.owners("Jane_Doe_ML_Engineer"), {OWNER})
        self.assertEqual([p["emailAddress"] for p in self.fake.permissions], [JANE])
        shares = [q for _, path, q in self.fake.requests if path.endswith("/permissions")]
        self.assertEqual(shares[0]["sendNotificationEmail"], "false")

    def test_after_sign_in_uploads_and_jd_docs_are_owned_by_the_person(self):
        self.connect_jane()
        resume_upload.upload(self.conn, "jane", self.ids["Acme"], "cv.pdf", PDF_BYTES)
        with db.tx(self.conn):
            self.conn.execute("UPDATE applications SET jd_text = ?, job_url = ? WHERE id = ?",
                              (RAW_JD, POSTING, self.ids["Acme"]))
        jd_doc.save(self.conn, self.ids["Acme"])
        self.assertEqual(self.owners("Jane_Doe_ML_Engineer"), {JANE})
        self.assertEqual(self.owners(" - JD"), {JANE})
        self.assertEqual(self.fake.permissions, [], "her own folder: nothing to share")

    def test_an_approved_resume_in_her_drive_is_chipped_in_her_row_at_once(self):
        # Arrange: a file in her own Drive (the owner's token cannot see it, so cannot chip it)
        self.connect_jane()
        mine = resume_upload.upload(self.conn, "jane", self.ids["Acme"], "cv.pdf", PDF_BYTES)
        # Act: the Approve path links that file to another row, replacing its resume
        made = resume_upload.link_existing(self.conn, "jane", self.ids["Gamma"], mine["name"],
                                           mine["link"].split("/d/")[1].split("/")[0], mine["link"], replace=True)
        # Assert: written now, on her token (not "follows on the next sync")
        self.assertNotIn("next sync", made["sheet"])
        cell = self.book.cell(TAB, self.row_of("Gamma"), self.column("Resume"))
        self.assertIn(mine["link"], [r["chip"]["richLinkProperties"]["uri"] for r in cell.get("chips") or [] if "chip" in r])

    def test_a_refused_token_falls_back_to_the_owners(self):
        self.connect_jane()
        self.fake.fail("GET", r"/v4/spreadsheets/", 403, times=1)
        self.assertIn(TAB, self.sync_report())

    def sync_report(self) -> dict:
        from job_hunt_agent import gsync_cycle
        return gsync_cycle.sync_profile(self.conn, "jane")

    def test_a_lapsed_person_token_falls_back(self):
        self.connect_jane()
        auth.save({**auth.load("jane"), "lapsed": True}, "jane")
        with accounts.as_profile("jane") as account:
            self.assertIsNone(account)


class MoveExistingFiles(OwnAccountCase):
    def setUp(self):
        super().setUp()
        self.upload = resume_upload.upload(self.conn, "jane", self.ids["Acme"], "cv.pdf", PDF_BYTES)
        with db.tx(self.conn):
            self.conn.execute("UPDATE applications SET jd_text = ?, job_url = ? WHERE id = ?",
                              (RAW_JD, POSTING, self.ids["Gamma"]))
        self.jd = jd_doc.save(self.conn, self.ids["Gamma"])
        jd_doc.link_rows(self.conn, "jane")
        self.connect_jane()
        self.rg = []
        self.move_patches = [mock.patch.object(rgclient, "request", self._resumegen),
                             mock.patch.object(google_move, "PACE_S", 0)]
        for p in self.move_patches:                 # not self.patches: GoogleCase keeps its config patches there
            p.start()

    def tearDown(self):
        for p in self.move_patches:
            p.stop()
        super().tearDown()

    def _resumegen(self, method, path, body=None, **kw):
        self.rg.append((method, path, body))
        return {"jobs": []} if method == "GET" else {"ok": True, "pdfs": 0, "docs": 0}

    def test_files_are_re_made_in_her_drive_links_follow_and_old_copies_are_parked(self):
        got = google_move.run(self.conn, "jane")
        self.assertEqual((got["files"], got["jd_docs"]), (1, 1))
        pdfs = [f for f in self.fake.files.values() if f["name"] == self.upload["name"]]
        self.assertEqual(sorted(f["owners"][0]["emailAddress"] for f in pdfs), [JANE, OWNER], "nothing deleted")
        mine = next(f for f in pdfs if f["owners"][0]["emailAddress"] == JANE)
        old = next(f for f in pdfs if f["owners"][0]["emailAddress"] == OWNER)
        self.assertEqual(mine["content"], PDF_BYTES, "same bytes, same name")
        row = self.conn.execute("SELECT file_id, link FROM resume_uploads").fetchone()
        self.assertEqual((row["file_id"], row["link"]), (mine["id"], mine["webViewLink"]))
        self.assertEqual(self.fake.files[old["parents"][0]]["name"], "Old - Jane (moved)")
        self.assertIn({"file": old["parents"][0], "type": "user", "role": "writer", "emailAddress": JANE},
                      self.fake.permissions, "old links still open for her")
        resume = self.book.cell(TAB, self.row_of("Acme"), self.column("Resume"))
        self.assertEqual([c["chip"]["richLinkProperties"]["uri"] for c in resume["chips"] if "chip" in c],
                         [mine["webViewLink"]])
        new_jd = jd_doc.get(self.conn, self.ids["Gamma"])
        self.assertNotEqual(new_jd["file_id"], self.jd["file_id"])
        self.assertEqual(self.fake.files[new_jd["file_id"]]["owners"][0]["emailAddress"], JANE)
        self.assertEqual(self.book.cell(TAB, self.row_of("Gamma"), self.column("JD"))["link"], new_jd["link"])
        self.assertEqual(self.rg[-1][1], "/api/google/relink")
        self.assertEqual(google_move.last("jane")["state"], "done")
        again = google_move.run(self.conn, "jane")
        self.assertEqual((again["files"], again["jd_docs"], again["parked"]), (0, 0, 0), "resumable, nothing twice")

    def _stopped_mid_jd_doc(self) -> None:
        """What a restart left: state "running", one JD Doc's owner copy forgotten but its link kept."""
        google_move._note(self.conn, "jane", "running", "0 resumes and Docs copied; writing your JD Docs")
        with db.tx(self.conn):
            self.conn.execute("UPDATE jd_docs SET file_id = NULL, sha = NULL WHERE app_id = ?", (self.ids["Gamma"],))

    def test_a_move_a_restart_stopped_resumes_when_the_server_starts(self):
        self._stopped_mid_jd_doc()
        self.assertEqual(google_move.resume_stopped(["jane", "nobody"]), ["jane"])
        for worker in [t for t in threading.enumerate() if t.name == "google-move-jane"]:
            worker.join(30)
        self.assertEqual(google_move.last("jane")["state"], "done", google_move.last("jane"))
        new_jd = jd_doc.get(self.conn, self.ids["Gamma"])
        self.assertEqual(self.fake.files[new_jd["file_id"]]["owners"][0]["emailAddress"], JANE)
        moved = self.conn.execute("SELECT old_id FROM google_moves WHERE kind = 'jd_doc'").fetchall()
        self.assertEqual([r["old_id"] for r in moved], [self.jd["file_id"]], "found again by its kept link")
        self.assertEqual(google_move.resume_stopped(["jane"]), [], "a finished move is not resumed")

    def test_the_account_page_says_stopped_resuming(self):
        google_move._note(self.conn, "jane", "running", google_move.RESUMING)
        self.assertIn("Stopped by a restart, resuming", google_page.render("jane"))

    def test_a_hand_run_holding_the_lock_is_never_doubled(self):
        self._stopped_mid_jd_doc()
        with google_move._held("jane") as free:
            self.assertTrue(free)
            self.assertEqual(google_move.resume_stopped(["jane"]), [])
            self.assertIn("skipped", google_move.run(self.conn, "jane"))
        self.assertEqual(google_move.run(self.conn, "jane")["jd_docs"], 1)


class SwapLinks(unittest.TestCase):
    def test_only_moved_files_links_change(self):
        old, other = "https://drive.google.com/file/d/OLDFILE1234/view", "https://example.com/d/OLDFILE1234"
        cell = {"textFormatRuns": [{"format": {"link": {"uri": old}}},
                                   {"startIndex": 5, "format": {"link": {"uri": other}}}]}
        new = "https://drive.google.com/file/d/NEWFILE1234/view"
        swap = google_move.swapped_cell(cell, {"OLDFILE1234": new})
        self.assertEqual(swap[1], "textFormatRuns")
        self.assertEqual([r["format"]["link"]["uri"] for r in swap[0]["textFormatRuns"]], [new, other])
        self.assertIsNone(google_move.swapped_cell({"textFormatRuns": [{"format": {"link": {"uri": other}}}]},
                                                   {"OLDFILE1234": new}))


if __name__ == "__main__":
    unittest.main()
