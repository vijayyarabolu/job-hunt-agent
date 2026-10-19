"""GOAL3 §3 tracker: mark applied from a card, manual add (ATS link and any link), several contacts per row,
follow-ups due, stats, vendors and the full export, through the same functions and routes the web UI uses."""
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from job_hunt_agent import api, config, contacts, db, export_full, server, stats, trackapi, vendors
from job_hunt_agent.ats.common import FetchResult, make_job
from tests.test_server import NOW, seed_job
from job_hunt_agent import store

TODAY = date(2026, 10, 5)


class TrackerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = mock.patch.object(config, "DB_PATH", Path(self.tmp.name) / "t.db")
        self.patch.start()
        self.conn = db.connect()
        self.uid = seed_job(self.conn)

    def tearDown(self):
        self.conn.close()
        self.patch.stop()
        self.tmp.cleanup()

    def add(self, **fields) -> dict:
        data = {"company": "Globex", "role_title": "ML Engineer", **fields}
        return trackapi.create(self.conn, "alex", data, TODAY)


class MarkAppliedTest(TrackerCase):
    def test_mark_applied_on_a_card_creates_a_prefilled_stamped_row(self):
        app_id = api.mark_applied(self.conn, "alex", self.uid, datetime.now(timezone.utc))
        row = trackapi.get(self.conn, "alex", app_id)
        self.assertEqual((row["company"], row["role_title"], row["status"], row["source"]),
                         ("Acme", "Machine Learning Engineer", "applied", "feed"))
        self.assertEqual(row["applied_on"], date.today().isoformat())
        self.assertEqual(row["date_applied"], date.today().strftime("%m/%d/%Y"))
        # GOAL6 JD format (2026-10-06 evening): the JD cell is the role title (linked to the posting), the text in jd_text
        self.assertEqual((row["job_url"], row["location"]), ("https://jobs.lever.co/acme/1", "Austin, TX"))
        self.assertEqual(row["jd"], "Machine Learning Engineer")
        self.assertIn("PyTorch", self.conn.execute("SELECT jd_text FROM applications WHERE id = ?", (app_id,)).fetchone()[0])
        self.assertEqual((row["jd_snapshot"]["state"], row["jd_snapshot"]["source"]), ("saved", "feed"))
        self.assertEqual(row["family"], "ml_engineer")
        self.assertEqual(row["fu1"]["due"], (date.today() + timedelta(days=3)).isoformat())
        again = api.mark_applied(self.conn, "alex", self.uid, datetime.now(timezone.utc))
        self.assertEqual(again, app_id)                                     # one row per posting


class ManualAddTest(TrackerCase):
    def test_any_link_is_saved_with_what_was_typed(self):
        url = "https://careers.example.com/jobs/42"
        found = trackapi.lookup(self.conn, url)
        self.assertEqual((found["supported"], found["fields"]["job_url"]), (False, url))
        row = self.add(job_url=url, location="Remote", notes="referral from Sam")
        self.assertEqual((row["source"], row["status"], row["applied_on"], row["job_url"], row["jd"]),
                         ("manual", "applied", "2026-10-05", url, url))
        self.assertIsNone(row["job_uid"])

    def _seed_greenhouse(self) -> str:
        item = make_job(ats="greenhouse", key="initech", ext_id="4242", company="Initech", title="AI Engineer",
                        location="Dallas, TX", url="https://job-boards.greenhouse.io/initech/jobs/4242",
                        posted_at=db.iso(NOW), description="Python and LLMs.", salary=(140000, 170000, "USD"))
        db.sync_boards(self.conn, [{"board": "lever:acme", "ats": "lever", "key": "acme", "company": "Acme"},
                                   {"board": "greenhouse:initech", "ats": "greenhouse", "key": "initech",
                                    "company": "Initech"}])
        run = db.start_run(self.conn, "test", NOW)
        store.write_board(self.conn, db.board(self.conn, "greenhouse:initech"), FetchResult([item], 200, n_raw=1),
                          "ok", run, NOW, ["fam:ai_engineer"], 5)
        return item["uid"]

    def test_greenhouse_link_of_a_fetched_job_fills_everything_and_links_the_posting(self):
        uid = self._seed_greenhouse()
        with mock.patch("job_hunt_agent.jobfetch.fetch_posting") as fetched:              # no network: the DB has it
            found = trackapi.lookup(self.conn, "https://job-boards.greenhouse.io/initech/jobs/4242")
        fetched.assert_not_called()
        self.assertEqual((found["from"], found["job_uid"]), ("feed", uid))
        self.assertEqual((found["fields"]["company"], found["fields"]["salary"]), ("Initech", "$140k to $170k"))
        row = trackapi.create(self.conn, "alex", {"job_uid": found["job_uid"], "status": "saved"}, TODAY)
        self.assertEqual((row["company"], row["role_title"], row["status"], row["applied_on"], row["location"]),
                         ("Initech", "AI Engineer", "saved", None, "Dallas, TX"))
        with self.assertRaisesRegex(ValueError, "already in the tracker"):
            trackapi.create(self.conn, "alex", {"job_uid": uid}, TODAY)

    def test_greenhouse_link_not_fetched_yet_is_read_from_the_ats(self):
        url = "https://boards.greenhouse.io/globex/jobs/777"
        posting = {"uid": "greenhouse:globex:777", "ats": "greenhouse", "company": "Globex", "title": "AI Engineer",
                   "url": url, "apply_url": url, "location": "New York, NY", "workplace": "hybrid",
                   "salary_min": 150000, "salary_max": 190000, "employment_type": "full_time"}
        with mock.patch("job_hunt_agent.jobfetch.fetch_posting", return_value=posting) as fetched:
            found = trackapi.lookup(self.conn, url)
        fetched.assert_called_once()
        self.assertEqual(found["job_uid"], "greenhouse:globex:777")
        self.assertEqual(found["fields"]["salary"], "$150k to $190k")
        row = trackapi.create(self.conn, "alex", {**found["fields"], "job_uid": found["job_uid"]}, TODAY)
        self.assertEqual((row["company"], row["workplace"], row["employment_label"]), ("Globex", "hybrid", "Full-time"))

    def test_closed_posting_still_lets_the_user_type_it(self):
        with mock.patch("job_hunt_agent.jobfetch.fetch_posting", side_effect=ValueError("posting closed (404)")):
            found = trackapi.lookup(self.conn, "https://boards.greenhouse.io/globex/jobs/1")
        self.assertIn("closed", found["error"])

    def test_validation(self):
        with self.assertRaisesRegex(ValueError, "company and role"):
            trackapi.create(self.conn, "alex", {"company": "X"}, TODAY)
        with self.assertRaisesRegex(ValueError, "workplace"):
            self.add(workplace="moon")
        with self.assertRaisesRegex(ValueError, "paste a job link"):
            trackapi.lookup(self.conn, "javascript:alert(1)")


class EditAndContactsTest(TrackerCase):
    def test_edits_status_date_and_extras(self):
        row = self.add()
        row = trackapi.update(self.conn, "alex", row["id"], {"date_applied": "2026-09-30", "status": "screen",
                                                              "team_name": "Payments", "salary": "$160k"})
        self.assertEqual((row["applied_on"], row["date_applied"], row["status"]), ("2026-09-30", "09/30/2026", "screen"))
        self.assertEqual((row["team_name"], row["salary"]), ("Payments", "$160k"))
        with self.assertRaisesRegex(ValueError, "not editable"):
            trackapi.update(self.conn, "alex", row["id"], {"profile": "jordan"})
        with self.assertRaises(KeyError):
            trackapi.update(self.conn, "jordan", row["id"], {"notes": "x"})        # another person's row

    def test_packed_cells_split_into_contacts(self):
        got = contacts.split_legacy({"target_name": "Dror Ayalon, Brian Bovino", "target_role": "Head of AI, Recruiter",
                                     "email_address": "brian.b@x.com", "linkedin_request": "Sent",
                                     "email_sent": "N/A, Yes"})
        self.assertEqual([(c["name"], c["role"], c["email"], c["linkedin_sent"], c["email_sent"]) for c in got],
                         [("Dror Ayalon", "Head of AI", "", "Sent", "N/A"),
                          ("Brian Bovino", "Recruiter", "brian.b@x.com", "Sent", "Yes")])
        one = contacts.split_legacy({"target_name": "Kim Lee", "target_role": "Director, Data Services"})
        self.assertEqual(one[0]["role"], "Director, Data Services")         # a role's own comma is not a split
        self.assertEqual(contacts.split_legacy({}), [])

    def test_several_contacts_with_their_own_sent_and_replied_marks(self):
        row = self.add()
        row = server.route(self.conn, "POST", "/api/tracker/contacts", {
            "profile": "alex", "app": row["id"], "contacts": [
                {"name": "Ann Recruiter", "role": "Recruiter", "email": "ann@globex.com"},
                {"name": "Bo Manager", "role": "Hiring Manager", "linkedin": "https://linkedin.com/in/bo"}]}, None)
        self.assertEqual([c["name"] for c in row["contacts"]], ["Ann Recruiter", "Bo Manager"])
        self.assertEqual(row["target_name"], "Ann Recruiter, Bo Manager")          # the tracker cells follow
        bo = row["contacts"][1]
        row = server.route(self.conn, "POST", "/api/tracker/contact_flag",
                           {"profile": "alex", "contact": bo["id"], "flag": "replied", "on": True}, None)
        self.assertEqual(row["contacts"][1]["replied"], date.today().strftime("%m/%d/%Y"))
        self.assertEqual(row["contacts"][0]["replied"], "")
        with self.assertRaisesRegex(ValueError, "email"):
            contacts.save(self.conn, row["id"], [{"name": "x", "email": "not-an-email"}])


class FollowupsAndStatsTest(TrackerCase):
    def test_followups_due_overdue_and_done(self):
        overdue = self.add(company="Old Co", date_applied="2026-09-28")                # FU1 due 10/01, FU2 10/04
        today = self.add(company="Today Co", date_applied="2026-10-02")               # FU1 due 10/05
        self.add(company="New Co", date_applied="2026-10-04")                         # FU1 due 10/07
        due = trackapi.followups(self.conn, "alex", TODAY)
        self.assertEqual([r["company"] for r in due["overdue"]], ["Old Co"])
        self.assertEqual([r["company"] for r in due["today"]], ["Today Co"])
        self.assertEqual([r["company"] for r in due["upcoming"]], ["New Co"])
        self.assertEqual(due["due_count"], 2)
        self.assertEqual(trackapi.followups(self.conn, "alex", TODAY, count_only=True), {"due_count": 2})  # the badge
        done = trackapi.followup_done(self.conn, "alex", today["id"], 1, TODAY)
        self.assertEqual((done["follow_up_1"], done["fu1"]["state"]), ("10/05/2026", "done"))
        self.assertNotIn("Today Co", [r["company"] for r in trackapi.followups(self.conn, "alex", TODAY)["today"]])
        self.assertEqual(overdue["fu2"]["state"], "overdue")

    def test_followups_leave_out_the_jd_text(self):
        """Jordan's 135 overdue rows carried ~5 KB of JD each (991 KB); the Follow-ups list never shows it."""
        self.add(company="Old Co", date_applied="2026-09-28", jd="Build models. " * 300)
        row = trackapi.followups(self.conn, "alex", TODAY)["overdue"][0]
        self.assertNotIn("jd", row)
        self.assertEqual((row["company"], row["fu1"]["state"]), ("Old Co", "overdue"))

    def test_stats_rates_by_family_and_median_days_to_response(self):
        a = self.add(company="A", role_title="ML Engineer", date_applied="2026-09-01")
        self.add(company="B", role_title="Data Scientist", date_applied="2026-09-02")
        self.add(company="C", role_title="Data Scientist", date_applied="2026-09-03")
        trackapi.update(self.conn, "alex", a["id"], {"status": "screen"})
        rep = stats.report(self.conn, "alex", TODAY)
        self.assertEqual((rep["applications"], rep["responses"], rep["rate"]), (3, 1, 0.333))
        fam = {r["key"]: (r["n"], r["responses"]) for r in rep["by_family"]}
        self.assertEqual(fam, {"ml_engineer": (1, 1), "data_scientist": (2, 0)})
        self.assertEqual(rep["median_days_to_response"], (date.today() - date(2026, 9, 1)).days)
        self.assertEqual(sum(w["n"] for w in rep["weekly"]), 3)
        self.assertEqual({r["key"] for r in rep["by_size"]}, {"unknown"})

    def test_stats_date_a_typed_row_by_its_posting_without_reading_every_job(self):
        # the Stats tab took 4-7 s live: it read all ~600k stored jobs to date the imported rows
        self.conn.execute("UPDATE jobs SET first_posted_at = ? WHERE uid = ?", ("2026-09-01T10:00:00Z", self.uid))
        self.add(company="Acme Inc.", role_title="Machine Learning Engineer", date_applied="2026-09-03")
        seen = []
        self.conn.set_trace_callback(seen.append)
        try:
            rep = stats.report(self.conn, "alex", TODAY)
        finally:
            self.conn.set_trace_callback(None)
        self.assertEqual({r["key"]: r["n"] for r in rep["by_days_posted"]}.get("2 to 3 days"), 1)
        whole = [s for s in seen if " FROM jobs " in s and "DISTINCT" not in s
                 and "company IN" not in s and "uid" not in s]
        self.assertEqual(whole, [], "a query read every job instead of the rows' companies")


class VendorsAndExportTest(TrackerCase):
    def test_vendors_adopted_once_then_edited_lists_survive_reimport(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(config, "PROFILES", Path(tmp)):
            (Path(tmp) / "jordan").mkdir()
            (Path(tmp) / "jordan" / "profile.yaml").write_text("name: S\nfamilies: [ml_engineer]\n")
            (Path(tmp) / "jordan" / "vendors.json").write_text('[{"company": "TEK", "email": "a@tek.example"}]')
            self.assertEqual(vendors.adopt_json(self.conn), 1)
            self.assertEqual(vendors.adopt_json(self.conn), 0)
            self.assertEqual(vendors.listing(self.conn, "jordan")[0]["company"], "TEK")
            vendors.save(self.conn, "jordan", [{"company": "TEK", "notes": "called 10/05"}, {"company": "Randstad"}])
            self.assertFalse(vendors.from_import(self.conn, "jordan", [{"company": "Old"}]))
            self.assertEqual([v["company"] for v in vendors.listing(self.conn, "jordan")], ["TEK", "Randstad"])

    def test_full_export_has_every_row_contacts_and_vendors(self):
        row = self.add()
        contacts.save(self.conn, row["id"], [{"name": "Ann"}, {"name": "Bo"}])
        api.mark_applied(self.conn, "alex", self.uid, datetime.now(timezone.utc))
        out = export_full.export(self.conn, "alex", "full", Path(self.tmp.name) / "full.xlsx")
        self.assertEqual(export_full.summary(out), {"Applications": 2, "Contacts": 2, "Vendors": 0})

    def test_export_route_downloads_a_workbook(self):
        self.add()
        with mock.patch.object(config, "DATA", Path(self.tmp.name)):
            got = server.route(self.conn, "GET", "/api/tracker/export", {"profile": "alex", "kind": "full"}, None)
        self.assertIsInstance(got, server.Download)
        self.assertTrue(got.path.read_bytes().startswith(b"PK"))


if __name__ == "__main__":
    unittest.main()


class ReviewFixesTest(TrackerCase):
    """Code review of GOAL3: lossless contact edits, safe toggles, guarded vendor wipes / formulas, atomic edits,
    and an upgrade raced by a second process."""

    def test_ticking_a_legacy_contact_rewrites_only_that_cell(self):
        row = self.add(target_name="Jane Doe", email_address="jane@x.com (no reply), call 555-1212")
        jane = row["contacts"][0]
        contacts.toggle(self.conn, jane["id"], "email_sent", True, TODAY)
        row = trackapi.get(self.conn, "alex", row["id"], TODAY)
        self.assertEqual(row["email_address"], "jane@x.com (no reply), call 555-1212")   # untouched original text
        self.assertEqual(row["target_name"], "Jane Doe")
        self.assertEqual(row["email_sent"], "10/05/2026")

    def test_unticking_a_contacts_only_mark_keeps_the_contact(self):
        row = self.add()
        saved = contacts.save(self.conn, row["id"], [{"linkedin_sent": "10/01/2026"}, {"name": "Bo"}])
        got = contacts.toggle(self.conn, saved[0]["id"], "linkedin_sent", False, TODAY)
        self.assertEqual(got["linkedin_sent"], "")
        self.assertEqual([c["name"] for c in contacts.for_apps(self.conn, [row["id"]])[row["id"]]], ["", "Bo"])

    def test_emptying_the_vendor_list_needs_clear(self):
        vendors.save(self.conn, "jordan", [{"company": "TEK"}])
        with self.assertRaisesRegex(ValueError, "clear"):
            vendors.save(self.conn, "jordan", [])
        self.assertEqual(len(vendors.listing(self.conn, "jordan")), 1)
        self.assertEqual(vendors.save(self.conn, "jordan", [], clear=True), [])

    def test_vendor_and_row_cells_that_look_like_formulas_stay_text(self):
        import openpyxl
        from job_hunt_agent import tracker_map
        cell = tracker_map._guarded(openpyxl.Workbook().active.cell(row=1, column=1, value='=HYPERLINK("x")'))
        self.assertTrue(cell.quotePrefix)
        self.assertEqual(cell.data_type, "s")

    def test_bad_date_rejects_the_whole_edit(self):
        row = self.add(notes="before")
        with self.assertRaisesRegex(ValueError, "date"):
            trackapi.update(self.conn, "alex", row["id"], {"notes": "after", "date_applied": "someday"})
        self.assertEqual(trackapi.get(self.conn, "alex", row["id"])["notes"], "before")

    def test_packed_cells_of_a_row_with_app_contacts_are_edited_through_the_contacts(self):
        # the cell is editable inline; the typed names spread over the contacts, the cell keeps the text
        row = self.add()
        contacts.save(self.conn, row["id"], [{"name": "Ann", "email": "ann@acme.example",
                                              "linkedin": "https://l.example/ann"}])
        out = trackapi.update(self.conn, "alex", row["id"], {"target_name": "Ann Lee and Bo Chen"})
        self.assertEqual(out["target_name"], "Ann Lee and Bo Chen")
        self.assertEqual([c["name"] for c in out["contacts"]], ["Ann Lee", "Bo Chen"])
        self.assertEqual(out["contacts"][0]["linkedin"], "https://l.example/ann")       # other fields kept

    def test_upgrade_raced_by_another_process_is_a_no_op(self):
        self.assertFalse(db._upgrade(self.conn, 10))                # migration 11's ALTERs meet a v12 DB
        self.assertFalse(self.conn.in_transaction)
        self.assertEqual(self.conn.execute("PRAGMA user_version").fetchone()[0], db.SCHEMA_VERSION)
