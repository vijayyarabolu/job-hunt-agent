"""GOAL6 §2/§3 against the fake Google: an inline JD/Address edit reaches the Sheet on the next cycle (no hourly
wait), JD links are real links (textFormatRuns), edits made on either side land in the row's history, undo puts the
old value back on both sides, the row says "synced" once the Sheet has every value, and a job typed in the Sheet
first and then marked applied in the app stays one row (twins merged)."""
from datetime import datetime

from job_hunt_agent import contacts, db, gsync, gsync_cycle, history, trackapi, tracker
from job_hunt_agent.google import state
from job_hunt_agent.gsync_values import link_runs, urls
from tests.test_gsync import SyncCase, TAB
from tests.test_server import seed_job

POSTING = "https://jobs.example.com/acme/123"


class LinkRunsTest(SyncCase):
    def test_every_url_becomes_a_link_and_trailing_punctuation_stays_text(self):
        text = f"{POSTING}\n\nApply at https://acme.example/apply. Thanks"
        self.assertEqual([u for _, _, u in urls(text)], [POSTING, "https://acme.example/apply"])
        runs = link_runs(text)
        self.assertEqual(runs[0], {"startIndex": 0, "format": {"link": {"uri": POSTING}}})
        self.assertEqual(runs[1], {"startIndex": len(POSTING), "format": {}})
        self.assertEqual(runs[2]["format"]["link"]["uri"], "https://acme.example/apply")
        self.assertEqual(link_runs("no link here"), [])

    def test_run_indexes_count_utf16_units(self):
        text = "😀 see https://x.example/a"                       # the emoji is 2 UTF-16 units
        self.assertEqual(link_runs(text)[1]["startIndex"], len("😀 see ") + 1)


class InlineEditSyncTest(SyncCase):
    def setUp(self):
        super().setUp()
        self.link()
        self.sync()
        with db.tx(self.conn):                                   # the scheduled JD read just ran
            state.put(self.conn, f"gsync.jane.{TAB}.full_at", db.iso(db.utcnow()))

    def jd_cell(self, company: str) -> dict:
        return self.book.cell(TAB, self.row_of(company), self.column("JD"))

    def test_jd_edited_in_the_app_reaches_the_sheet_on_the_next_cycle_as_a_link(self):
        app_id = self.ids["Acme"]
        with history.action("Jane Doe"):                          # pasted "URL + JD": the cell keeps one line
            trackapi.update(self.conn, "jane", app_id, {"jd": f"{POSTING}\n\nAbout the role"})
        self.assertEqual(trackapi.sync_state(self.conn, "jane", app_id)["pending"], ["JD"])
        report = self.sync()
        self.assertTrue(report["full_read"])                      # the pending JD edit pulled the column in
        cell, app = self.jd_cell("Acme"), self.app("Acme")
        self.assertEqual((cell["v"], cell["link"], cell["wrap"]), (app["role_title"], POSTING, "CLIP"))
        self.assertEqual(app["jd_text"], "About the role")
        self.assertEqual(trackapi.sync_state(self.conn, "jane", app_id)["state"], "synced")

    def test_edits_on_both_sides_are_in_the_history_and_undo_restores_both(self):
        app_id = self.ids["Gamma"]
        with history.action("Jane Doe"):
            trackapi.update(self.conn, "jane", app_id, {"role_title": "Staff AI Engineer"})
        self.sync()
        self.assertEqual(self.grid()[self.row_of("Gamma")][1], "Staff AI Engineer")
        self.edit("Gamma", "Target Name", "Bo Chen")             # typed on the phone
        self.sync()
        rows = history.for_app(self.conn, app_id)
        self.assertEqual([(r["field"], r["who"], r["source"]) for r in rows][:2],
                         [("target_name", "Google Sheet", "sheet"), ("role_title", "Jane Doe", "app")])
        self.assertEqual((rows[1]["old_value"], rows[1]["new_value"]), ("AI Engineer", "Staff AI Engineer"))
        out = trackapi.undo(self.conn, "jane", app_id, "Jane Doe")
        self.assertEqual(out["row"]["role_title"], "AI Engineer")
        self.sync()
        self.assertEqual(self.grid()[self.row_of("Gamma")][1], "AI Engineer")
        self.assertEqual(history.for_app(self.conn, app_id)[0]["source"], "undo")
        with self.assertRaisesRegex(ValueError, "nothing to undo"):
            trackapi.undo(self.conn, "jane", app_id, "Jane Doe")

    def test_undo_of_a_status_change(self):
        app_id = self.ids["Gamma"]
        with history.action("Jane Doe"):
            trackapi.update(self.conn, "jane", app_id, {"status": "interview"})
        self.assertEqual(trackapi.undo(self.conn, "jane", None, "Jane Doe")["row"]["status"], "applied")


class ContactCellsTest(SyncCase):
    def test_a_contact_cell_typed_in_the_sheet_reaches_the_apps_contact(self):
        self.link()
        contacts.save(self.conn, self.ids["Gamma"], [{"name": "Zed Ray", "role": "Recruiter", "email": "zed@g.com"}])
        self.sync()                                               # the app's contact is in the Sheet
        self.edit("Gamma", "Target Role", "Head of Data")
        self.edit("Gamma", "Email Sent", "10/05/2026")
        self.sync()
        person = contacts.for_apps(self.conn, [self.ids["Gamma"]])[self.ids["Gamma"]][0]
        self.assertEqual((person["name"], person["role"], person["email_sent"]),
                         ("Zed Ray", "Head of Data", "10/05/2026"))


class TwinsTest(SyncCase):
    def setUp(self):
        super().setUp()
        self.link()

    def test_a_job_typed_in_the_sheet_first_then_marked_applied_in_the_app_stays_one_row(self):
        today = datetime.now().strftime("%m/%d/%Y")
        with self.fake.lock:
            for col, value in enumerate(["Torc Robotics", "Senior, ML Engineer - Tracking", today]):
                self.book.cell(TAB, 4, col).update({"v": value})
        self.sync()                                               # her row becomes application #typed (keyed)
        typed = self.conn.execute("SELECT id FROM applications WHERE source = 'sheet'").fetchone()["id"]
        made = trackapi.create(self.conn, "jane", {"company": "Torc Robotics", "role_title":
                                                   "Senior, ML Engineer - Tracking", "job_url": POSTING,
                                                   "status": "saved"})
        tracker.set_status(self.conn, made["id"], "applied")
        rows_before = len([r for r in self.grid() if any(r)])
        report = self.sync()
        self.assertEqual(report.get("appended", 0), 0)
        self.assertEqual(len([r for r in self.grid() if any(r)]), rows_before)
        self.assertIsNone(self.conn.execute("SELECT 1 FROM applications WHERE id = ?", (made["id"],)).fetchone())
        kept = dict(self.conn.execute("SELECT * FROM applications WHERE id = ?", (typed,)).fetchone())
        self.assertEqual((kept["job_url"], kept["company"]), (POSTING, "Torc Robotics"))
        self.assertEqual(self.log("merged")[0]["app_id"], typed)

    def test_a_feed_job_with_a_job_id_merges_into_the_typed_row(self):
        today = datetime.now().strftime("%m/%d/%Y")
        with self.fake.lock:
            for col, value in enumerate(["Allstate", "AI Software Engineer", today]):
                self.book.cell(TAB, 4, col).update({"v": value})
        self.sync()
        typed = self.conn.execute("SELECT id FROM applications WHERE source = 'sheet'").fetchone()["id"]
        made = trackapi.create(self.conn, "jane", {"company": "Allstate", "role_title": "AI Software Engineer",
                                                   "status": "saved"})
        with db.tx(self.conn):                                    # a feed job: the row carries the posting's id
            self.conn.execute("UPDATE applications SET job_uid = 'workday:acme/1' WHERE id = ?", (made["id"],))
        tracker.set_status(self.conn, made["id"], "applied")
        self.assertEqual(self.sync().get("appended", 0), 0)       # no UNIQUE(profile, job_uid) crash
        kept = self.conn.execute("SELECT job_uid FROM applications WHERE id = ?", (typed,)).fetchone()
        self.assertEqual(kept["job_uid"], "workday:acme/1")

    def test_a_sheet_row_reused_for_another_job_drops_the_old_posting(self):
        uid = seed_job(self.conn)                                 # the Acme posting
        made = trackapi.create(self.conn, "jane", {"company": "Acme", "role_title": "Machine Learning Engineer",
                                                   "status": "applied"})
        with db.tx(self.conn):
            self.conn.execute("UPDATE applications SET job_uid = ?, job_url = 'https://jobs.lever.co/acme/1' "
                              "WHERE id = ?", (uid, made["id"]))
        self.sync()                                               # appended with its key
        index = next(i for i, r in enumerate(self.grid()) if f"jh:{made['id']}" in [str(v) for v in r])
        with self.fake.lock:                                      # the user types another job into that row
            self.book.cell(TAB, index, 0).update({"v": "Globex", "formula": None})
            self.book.cell(TAB, index, 1).update({"v": "Data Analyst", "formula": None})
        self.sync()
        row = dict(self.conn.execute("SELECT * FROM applications WHERE id = ?", (made["id"],)).fetchone())
        self.assertEqual((row["company"], row["job_uid"], row["job_url"]), ("Globex", None, None))
        self.assertEqual(self.log("detached")[0]["app_id"], made["id"])

    def test_a_renamed_company_keeps_its_posting(self):
        uid = seed_job(self.conn)
        made = trackapi.create(self.conn, "jane", {"company": "Acme", "status": "applied", "role_title": "MLE"})
        with db.tx(self.conn):
            self.conn.execute("UPDATE applications SET job_uid = ? WHERE id = ?", (uid, made["id"]))
        self.sync()
        index = next(i for i, r in enumerate(self.grid()) if f"jh:{made['id']}" in [str(v) for v in r])
        with self.fake.lock:
            self.book.cell(TAB, index, 0).update({"v": "Acme Inc.", "formula": None})
        self.sync()
        self.assertEqual(self.conn.execute("SELECT job_uid FROM applications WHERE id = ?",
                                           (made["id"],)).fetchone()[0], uid)

    def test_different_roles_at_one_company_are_not_merged(self):
        today = datetime.now().strftime("%m/%d/%Y")
        with self.fake.lock:
            for col, value in enumerate(["Solace", "Data Engineer", today]):
                self.book.cell(TAB, 4, col).update({"v": value})
        self.sync()
        made = trackapi.create(self.conn, "jane", {"company": "Solace", "role_title": "Data Scientist"})
        self.assertEqual(self.sync().get("appended", 0), 1)
        self.assertIsNotNone(self.conn.execute("SELECT 1 FROM applications WHERE id = ?", (made["id"],)).fetchone())

    def test_the_jd_read_runs_every_few_minutes(self):
        self.assertLessEqual(gsync.FULL_READ_S, 180)              # + a 2 min poll: a Sheet JD edit lands < 5 min


class ReviewFixesTest(SyncCase):
    """Code review 2026-10-06: one word is not the same role; undated rows are not twins; Undo never reverts what
    job-hunt-agent did on its own; a merge keeps both rows' contacts."""
    def test_role_and_date_rules(self):
        from job_hunt_agent.gsync_twins import _near, same_role
        self.assertFalse(same_role("Engineer", "Senior Software Engineer"))
        self.assertTrue(same_role("ML Engineer", "Senior ML Engineer"))
        self.assertFalse(_near({"applied_on": None}, {"applied_on": "2026-10-06"}))

    def test_undo_skips_what_the_app_did_on_its_own(self):
        app_id = self.ids["Gamma"]
        with history.action("Jane Doe"):
            trackapi.update(self.conn, "jane", app_id, {"role_title": "Staff AI Engineer"})
        with history.action(history.CLI):
            tracker.edit_field(self.conn, self.ids["Acme"], "jd", "Machine Learning Engineer")
        out = trackapi.undo(self.conn, "jane", None, "Jane Doe")
        self.assertEqual(out["row"]["id"], app_id)

    def test_a_merge_keeps_the_apps_contacts_after_the_sheet_rows(self):
        self.link()
        today = datetime.now().strftime("%m/%d/%Y")
        with self.fake.lock:
            for col, value in enumerate(["Torc Robotics", "Senior ML Engineer", today]):
                self.book.cell(TAB, 4, col).update({"v": value})
        self.sync()
        typed = self.conn.execute("SELECT id FROM applications WHERE source = 'sheet'").fetchone()["id"]
        contacts.save(self.conn, typed, [{"name": "Ann Lee", "role": "Recruiter"}])
        made = trackapi.create(self.conn, "jane", {"company": "Torc Robotics", "role_title": "Senior ML Engineer",
                                                   "status": "saved"})
        contacts.save(self.conn, made["id"], [{"name": "Bo Chen", "role": "Head of ML"}])
        tracker.set_status(self.conn, made["id"], "applied")
        self.sync()
        names = [c["name"] for c in contacts.for_apps(self.conn, [typed])[typed]]
        self.assertEqual(names, ["Ann Lee", "Bo Chen"])

    def test_a_merge_moves_every_child_row_so_the_delete_never_breaks_a_foreign_key(self):
        """the app twin's JD Doc, pending apply and resumegen link made the delete fail."""
        self.link()
        today = datetime.now().strftime("%m/%d/%Y")
        with self.fake.lock:
            for col, value in enumerate(["Torc Robotics", "Senior ML Engineer", today]):
                self.book.cell(TAB, 4, col).update({"v": value})
        self.sync()
        typed = self.conn.execute("SELECT id FROM applications WHERE source = 'sheet'").fetchone()["id"]
        made = trackapi.create(self.conn, "jane", {"company": "Torc Robotics", "role_title": "Senior ML Engineer",
                                                   "status": "saved"})["id"]
        now = datetime.now().isoformat()
        with db.tx(self.conn):
            self.conn.execute("INSERT INTO jd_docs (app_id, profile, state, link, saved_at) "
                              "VALUES (?, 'jane', 'saved', 'https://docs/x', ?)", (made, now))
            self.conn.execute("INSERT INTO pending_applies (profile, app_id, opened_at) VALUES ('jane', ?, ?)",
                              (made, now))
            self.conn.execute("INSERT INTO rg_links (rg_id, profile, app_id, created_at) VALUES (7, 'jane', ?, ?)",
                              (made, now))
        tracker.set_status(self.conn, made, "applied")
        self.sync()
        self.assertIsNone(self.conn.execute("SELECT 1 FROM applications WHERE id = ?", (made,)).fetchone())
        for table in ("jd_docs", "pending_applies", "rg_links"):
            self.assertEqual(self.conn.execute(f"SELECT app_id FROM {table}").fetchone()["app_id"], typed, table)
        self.assertEqual(self.conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_a_row_typed_in_the_sheet_keeps_its_jd_as_text(self):
        self.link()
        jd = "About the role\n\n" + "Build ML systems with Python. " * 20
        with self.fake.lock:
            for col, value in enumerate(["Comcast", "Data Engineer 2", "10/06/2026", jd]):
                self.book.cell(TAB, 4, col).update({"v": value})
        gsync_cycle.sync_profile(self.conn, "jane", full=True)
        typed = self.conn.execute("SELECT id FROM applications WHERE source = 'sheet'").fetchone()["id"]
        snap = self.conn.execute("SELECT state, source, text FROM jd_snapshots WHERE app_id = ?", (typed,)).fetchone()
        self.assertEqual((snap["state"], snap["source"]), ("saved", "sheet"))
        self.assertTrue(snap["text"].startswith("About the role"))
