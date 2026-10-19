"""the full JD as a Google Doc, one per application, linked from the row's JD cell; the app's Full JD page.
Against the fake Google (Drive converts the HTML; the Sheet is the person's existing one)."""
import unittest
from unittest import mock

from job_hunt_agent import db, gsync_cycle, gsync_jd, jd_doc, jdsnap
from job_hunt_agent import jd_doc_html as html
from job_hunt_agent.google import GDOC
from job_hunt_agent.gsync_values import to_cell
from tests.test_gsync import TAB, SyncCase

POSTING = "https://boards.greenhouse.io/acme/jobs/1"
RAW_JD = ("About Acme\nAcme builds tools for engineers. " * 5 + "\n\nWhat you'll do:\n- Build ML tooling\n"
          "• Ship models to production\n\nREQUIREMENTS\n- 4 years of Python\n\nEqual Opportunity Employer\n"
          "Acme is an equal opportunity employer and values diversity.")
DOC = "https://docs.google.com/document/d/doc1/edit"


class TextToHtml(unittest.TestCase):
    def test_headings_are_bold_bullets_are_bullets_and_text_is_escaped(self):
        kinds = html.blocks(RAW_JD)
        self.assertIn(("h", "What you'll do:"), kinds)
        self.assertIn(("h", "REQUIREMENTS"), kinds)
        self.assertIn(("li", "Build ML tooling"), kinds)
        self.assertIn(("li", "Ship models to production"), kinds)
        body = html.body_html(RAW_JD + "\n<script>x</script> & co")
        self.assertIn("<p><b>What you&#x27;ll do:</b></p><ul><li>Build ML tooling</li><li>Ship models", body)
        self.assertIn("&lt;script&gt;x&lt;/script&gt; &amp; co", body)
        self.assertIn("Equal Opportunity Employer", body, "the JD as posted: no boilerplate stripped")

    def test_the_raw_posting_wins_over_the_cleaned_copies(self):
        job = {"description": db.pack(RAW_JD)}
        self.assertEqual(html.source_text(None, {"jd_text": "cleaned"}, job), (RAW_JD, "feed"))
        pairs = html.header({"company": "Acme", "role_title": "ML Engineer", "date_applied": "10/08/2026",
                             "job_url": POSTING}, {"req_id": "R1", "salary_min": 150000, "salary_max": 180000,
                                                   "salary_ccy": "USD", "first_posted_at": "2026-10-01T00:00:00Z"})
        self.assertEqual(dict(pairs), {"Company": "Acme", "Role title": "ML Engineer", "Salary": "150,000 - 180,000 USD",
                                       "Req ID": "R1", "Posting URL": POSTING, "Posted": "2026-10-01",
                                       "Date applied": "10/08/2026"})
        self.assertEqual(jd_doc.doc_name(pairs), "Acme - ML Engineer - JD")


class FirstLineLink(unittest.TestCase):
    def test_the_title_cell_the_app_wrote_links_to_the_doc_instead_of_the_posting(self):
        runs, why = jd_doc.linked_runs("ML Engineer", [{"format": {"link": {"uri": POSTING}}}], DOC, (POSTING,))
        self.assertEqual((runs, why), ([{"startIndex": 0, "format": {"link": {"uri": DOC}}}], "linked"))

    def test_an_older_multi_line_cell_keeps_its_text_format_and_links(self):
        text = "Senior ML Engineer 🚀\nWe build things.\nApply: https://acme.com/jobs/1"
        bold = [{"format": {"bold": True}}, {"startIndex": 21, "format": {}},
                {"startIndex": 46, "format": {"link": {"uri": "https://acme.com/jobs/1"}}}]
        runs, why = jd_doc.linked_runs(text, bold, DOC)
        self.assertEqual(why, "linked")
        self.assertEqual(runs[0], {"startIndex": 0, "format": {"bold": True, "link": {"uri": DOC}}})
        self.assertEqual(runs[1], {"startIndex": 21, "format": {}}, "the link ends with the line (UTF-16: 🚀 is 2)")
        self.assertEqual(runs[-1]["format"]["link"]["uri"], "https://acme.com/jobs/1", "the person's link stays")
        self.assertEqual(jd_doc.linked_runs(text, runs, DOC), (None, "already linked"))

    def test_a_first_line_that_is_a_link_is_skipped(self):
        text = "https://acme.com/jobs/1\n\nML Engineer at Acme"
        runs, _ = jd_doc.linked_runs(text, [], DOC)
        self.assertEqual(runs, [{"startIndex": 0, "format": {}}, {"startIndex": 25, "format": {"link": {"uri": DOC}}}])
        self.assertEqual(jd_doc.linked_runs("   ", [], DOC), (None, "the JD cell is empty"))

    def test_the_sync_writes_the_doc_link_on_the_title_cell(self):
        cell = gsync_jd.jd_cell(to_cell("ML Engineer", True, header="JD"), {"job_url": POSTING}, DOC)
        self.assertEqual(cell["textFormatRuns"], [{"startIndex": 0, "format": {"link": {"uri": DOC}}}])
        typed = to_cell("See https://acme.com/jobs/1", True, header="JD")
        self.assertEqual(gsync_jd.jd_cell(typed, {"job_url": POSTING}, DOC), typed, "a cell with its own link: as is")


class DocInDrive(SyncCase):
    def setUp(self):
        super().setUp()
        self.link()
        self.app_id = self.ids["Acme"]
        with db.tx(self.conn):
            self.conn.execute("UPDATE applications SET jd_text = ?, job_url = ? WHERE id = ?",
                              (RAW_JD, POSTING, self.app_id))

    def docs(self) -> list[dict]:
        return [f for f in self.fake.files.values() if f["mimeType"] == GDOC]

    def test_one_doc_per_application_updated_in_place(self):
        first = jd_doc.save(self.conn, self.app_id)
        self.assertEqual(first["action"], "created")
        [doc] = self.docs()
        self.assertEqual(doc["name"], "Acme - ML Engineer - JD")
        self.assertEqual(doc["appProperties"], {"job_hunt_agent_jd": str(self.app_id), "profile": "jane"})
        self.assertEqual(self.fake.files[doc["parents"][0]]["name"], "Jane")
        self.assertIn(b"<li>Build ML tooling</li>", doc["content"])
        self.assertIn(b"<b>Company:</b> Acme", doc["content"])
        writes = self.fake.count("POST", r"/upload/drive/v3/files")
        self.assertEqual(jd_doc.save(self.conn, self.app_id)["action"], "unchanged")
        self.assertEqual(self.fake.count("POST", r"/upload/drive/v3/files"), writes, "unchanged: no request")
        with db.tx(self.conn):
            self.conn.execute("UPDATE applications SET jd_text = ? WHERE id = ?", (RAW_JD + "\nNew line.", self.app_id))
        again = jd_doc.save(self.conn, self.app_id)
        self.assertEqual((again["action"], again["file_id"]), ("updated", doc["id"]))
        self.assertEqual(len(self.docs()), 1)
        self.assertIn(b"New line.", self.docs()[0]["content"])

    def test_a_lost_record_finds_the_doc_it_made(self):
        jd_doc.save(self.conn, self.app_id)
        with db.tx(self.conn):
            self.conn.execute("DELETE FROM jd_docs")
        self.assertEqual(jd_doc.save(self.conn, self.app_id)["action"], "updated")
        self.assertEqual(len(self.docs()), 1)

    def test_no_jd_text_fetches_once_then_says_posting_gone(self):
        gamma = self.ids["Gamma"]
        with db.tx(self.conn):
            self.conn.execute("UPDATE applications SET job_url = ? WHERE id = ?", (POSTING, gamma))
        with mock.patch.object(jdsnap, "fetch_text", return_value=("gone", None, "HTTP 404")) as fetch:
            first = jd_doc.save(self.conn, gamma, fetch=True)
            second = jd_doc.save(self.conn, gamma, fetch=True)
            page = jd_doc.page(self.conn, "jane", gamma)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual((first["state"], second["action"]), ("gone", "gone"))
        self.assertIn(html.GONE, second["detail"])
        self.assertEqual(self.docs(), [])
        self.assertIn(html.GONE, page)

    def test_a_row_merged_away_during_the_backfill_is_skipped(self):
        real, gone = jd_doc.save, self.ids["Gamma"]

        def save(conn, app_id, **kw):
            if app_id == gone:                     # a sync folded it into its Sheet twin mid-run
                raise KeyError(app_id)
            return real(conn, app_id, **kw)
        with mock.patch.object(jd_doc, "save", save):
            got = jd_doc.backfill(self.conn, ["jane"], pace_s=0, fetch=False, link=False, say=lambda _: None)
        self.assertEqual(got["jane"]["docs"]["merged away"], 1)
        self.assertEqual(got["jane"]["docs"]["created"], 1)

    def test_a_busy_database_is_waited_out(self):
        real, calls = jd_doc.save, []

        def save(conn, app_id, **kw):
            calls.append(app_id)
            if len(calls) == 1:
                raise jd_doc.sqlite3.OperationalError("database is locked")
            return real(conn, app_id, **kw)
        with mock.patch.object(jd_doc, "save", save), mock.patch.object(jd_doc, "BUSY_WAIT_S", 0):
            got = jd_doc.backfill(self.conn, ["jane"], pace_s=0, fetch=False, link=False, say=lambda _: None)
        self.assertEqual(calls[:2], [calls[0], calls[0]], "the same row again")
        self.assertNotIn("failed", got["jane"]["docs"])

    def test_the_jd_cell_links_to_the_doc_and_keeps_its_text(self):
        done = jd_doc.save(self.conn, self.app_id)
        self.assertEqual(jd_doc.link_rows(self.conn, "jane"), {"linked": 1})
        cell = self.book.cell(TAB, self.row_of("Acme"), self.column("JD"))
        self.assertEqual((cell["v"], cell["link"]), ("JD one", done["link"]))
        self.assertEqual(jd_doc.link_rows(self.conn, "jane"), {"already linked": 1})
        self.assertEqual(self.sync().get("pulled", 0), 0, "a link is not a value change")

    def test_the_full_jd_page(self):
        jd_doc.save(self.conn, self.app_id)
        page = jd_doc.page(self.conn, "jane", self.app_id)
        self.assertIn('<button id="copy"', page)
        self.assertIn("<li>Ship models to production</li>", page)
        self.assertIn(jd_doc.link_of(self.conn, self.app_id), page)
        with self.assertRaises(KeyError):
            jd_doc.page(self.conn, "someone", self.app_id)

    def test_a_row_typed_into_the_sheet_gets_its_doc_on_the_next_sync(self):
        # Arrange: a row typed into the Sheet with its JD pasted (Jordan's #1381 had none)
        with self.fake.lock:
            for col, value in enumerate(["Delta", "ML Engineer"]):
                self.book.cell(TAB, 4, col).update({"v": value})
            self.book.cell(TAB, 4, self.column("JD")).update({"v": RAW_JD})
        # Act
        with mock.patch.object(jd_doc, "soon", side_effect=jd_doc.save_and_link) as soon:
            gsync_cycle.sync_profile(self.conn, "jane", full=True)
            gsync_cycle.sync_profile(self.conn, "jane", full=True)
        # Assert: every row without a Doc got one once (Gamma: no JD anywhere, so "gone"), none twice
        delta = self.conn.execute("SELECT id FROM applications WHERE company = 'Delta'").fetchone()["id"]
        started = [c.args[0] for c in soon.call_args_list]
        self.assertIn(delta, started)
        self.assertEqual(len(started), len(set(started)), "a row is not retried every cycle")
        self.assertEqual(jd_doc.get(self.conn, delta)["name"], "Delta - ML Engineer - JD")
        self.assertEqual(jd_doc.get(self.conn, self.ids["Gamma"])["state"], "gone")

    def test_a_gone_row_whose_jd_arrives_later_gets_its_doc(self):
        gamma = self.ids["Gamma"]
        jd_doc.save(self.conn, gamma)
        self.assertNotIn(gamma, jd_doc.missing(self.conn, "jane"))
        with db.tx(self.conn):
            self.conn.execute("UPDATE applications SET jd_text = ?, updated_at = '2099-01-01T00:00:00Z' WHERE id = ?",
                              (RAW_JD, gamma))
        self.assertIn(gamma, jd_doc.missing(self.conn, "jane"))
        jd_doc.save(self.conn, gamma)
        self.assertEqual(jd_doc.get(self.conn, gamma)["state"], "saved")

    def test_mark_applied_saves_in_the_background_only_with_real_google(self):
        self.assertIsNone(jd_doc.soon(self.app_id), "off in tests unless a test opts in")
        with mock.patch.object(jd_doc, "BACKGROUND", True):
            jd_doc.soon(self.app_id).join(30)
        self.assertEqual(len(self.docs()), 1)
        self.assertEqual(jd_doc.get(self.conn, self.app_id)["state"], "saved")


if __name__ == "__main__":
    unittest.main()
