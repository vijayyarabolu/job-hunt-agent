"""GOAL36 §5.1 on a throwaway DB: an outside job (a JD pasted in the Resume app) gets a real job record, so its card
and detail are an inside job's (match ring, years asked from the JD, place from the JD) with a small "outside job"
tag; it never shows in the feed; Mark applied and the tracker work on the same row; the Resumes tab keys its resume by
the record. ZZ_TEST data, no network."""
import json

from job_hunt_agent import api, db, filters, jdsnap, outside_jobs, rqueue, tracker

from tests.test_goal8_flow import NOW, PID, Goal8Case

JD = ("ZZ_TEST Prairie Robotics\nLocation: Lawrence, KS\n\nRequirements:\n- 3+ years of experience.\n"
      + "- Build ML systems in Python and PyTorch, deploy on Kubernetes and AWS.\n" * 3)


class OutsideCase(Goal8Case):
    def setUp(self):
        super().setUp()
        tracker.ensure_profile(self.conn, PID)
        with db.tx(self.conn):
            cur = self.conn.execute("INSERT INTO applications(profile, company, role_title, job_url, status, source, "
                                    "updated_at) VALUES (?, 'ZZ_TEST Prairie Robotics', 'Machine Learning Engineer', "
                                    "'https://example.com/zz-test-job', 'saved', 'manual', ?)", (PID, db.iso(NOW)))
        self.app_id = cur.lastrowid
        jdsnap.keep(self.conn, self.app_id, "https://example.com/zz-test-job", JD, "pasted")
        self.uid = outside_jobs.uid_of(PID, self.app_id)

    def app(self) -> dict:
        return dict(self.conn.execute("SELECT * FROM applications WHERE id = ?", (self.app_id,)).fetchone())


class RecordTest(OutsideCase):
    def test_the_row_gets_a_job_record_with_years_and_place_from_the_jd(self):
        self.assertEqual(outside_jobs.backfill(self.conn, PID, NOW), 1)
        job = dict(self.conn.execute("SELECT * FROM jobs WHERE uid = ?", (self.uid,)).fetchone())
        self.assertEqual((job["board"], job["company"], job["title"]), (f"outside:{PID}", "ZZ_TEST Prairie Robotics",
                                                                      "Machine Learning Engineer"))
        self.assertEqual((job["min_years"], job["location"], job["is_us"]), (3, "Lawrence, KS", 1))
        self.assertIn(",ml_engineer,", job["families"])         # its own board is review-only: never in a feed
        self.assertEqual(self.app()["job_uid"], self.uid)
        fit = self.conn.execute("SELECT jd_state FROM fits WHERE profile = ? AND uid = ?", (PID, self.uid)).fetchone()
        self.assertEqual(fit["jd_state"], "ok")
        self.assertEqual(outside_jobs.backfill(self.conn, PID, NOW), 0)          # again: nothing new, no copy
        self.assertEqual(self.conn.execute("SELECT count(*) FROM jobs WHERE board = ?", (f"outside:{PID}",))
                         .fetchone()[0], 1)

    def test_a_sheet_or_feed_row_is_left_alone(self):
        with db.tx(self.conn):
            self.conn.execute("UPDATE applications SET source = 'import' WHERE id = ?", (self.app_id,))
        self.assertEqual(outside_jobs.backfill(self.conn, PID, NOW), 0)
        self.assertIsNone(self.app()["job_uid"])


class SameAsInsideTest(OutsideCase):
    def setUp(self):
        super().setUp()
        outside_jobs.backfill(self.conn, PID, NOW)

    def test_the_card_is_an_inside_card_with_the_outside_tag(self):
        card = api.cards_for(self.conn, PID, [self.uid], NOW)[self.uid]
        self.assertTrue(card["outside_job"])
        self.assertNotIn("outside", card)                      # the old small card's key: it opened the row editor
        self.assertIsNotNone(card["match"])
        self.assertEqual(card["asks_years"], 3)
        inside = api.cards_for(self.conn, PID, self.uids[:1], NOW)[self.uids[0]]
        self.assertFalse(inside["outside_job"])
        self.assertLessEqual(set(inside) - {"closed"}, set(card))

    def test_the_detail_page_opens_like_an_inside_job(self):
        detail = api.job_detail(self.conn, PID, self.uid, NOW)
        self.assertEqual(detail["card"]["uid"], self.uid)
        self.assertTrue(detail["card"]["outside_job"])
        self.assertIn("Lawrence, KS", detail["card"]["location"])
        self.assertNotIn("outside matching", json.dumps(detail))         # its ATS is "this ATS", not "outside"

    def test_it_never_shows_in_the_feed(self):
        for tab in ("recommended", "all"):
            out = api.jobs(self.conn, PID, tab, NOW, filters.parse({}))
            self.assertNotIn(self.uid, {c["uid"] for c in out["jobs"]}, tab)

    def test_mark_applied_works_on_the_same_tracker_row(self):
        app_id = api.mark_applied(self.conn, PID, self.uid, NOW)
        self.assertEqual(app_id, self.app_id)
        self.assertEqual(self.app()["status"], "applied")

    def test_the_resumes_tab_keys_its_resume_by_the_record(self):
        with db.tx(self.conn):
            self.conn.execute("INSERT INTO rg_links(rg_id, profile, app_id, created_at) VALUES (901, ?, ?, ?)",
                              (PID, self.app_id, db.iso(NOW)))
        got = rqueue._entries(self.conn, PID, [{"id": 901, "status": "ready", "profile": PID}])
        self.assertEqual(list(got), [self.uid])
