"""GOAL33 §1 / §3d: a resume pasted in resumegen always reaches Tailored resumes, even for a role the user applied to
before; outside cards show the job's place (never the user's address) and a match from the pasted JD."""
from job_hunt_agent import appliedview, db, outside, rgjobs, trackapi
from tests.test_goal13_resumes import TODAY, ResumesTabCase
from tests.test_goal8_flow import JD, NOW, PID

HOME = "11 Example Street, Austin, TX 66223"
EARLIER = "2026-10-11"


class PastedAgainTest(ResumesTabCase):
    def applied_row(self, company="Amazon", role="Applied Scientist"):
        app = trackapi.create(self.conn, PID, {"company": company, "role_title": role, "status": "saved"})["id"]
        with db.tx(self.conn):
            self.conn.execute("UPDATE applications SET status = 'applied', applied_on = ?, address = ? WHERE id = ?",
                              (EARLIER, HOME, app))
            self.conn.execute("INSERT INTO application_events(app_id, at, from_status, to_status, source) "
                              "VALUES (?, ?, 'saved', 'applied', 'ui')", (app, EARLIER + "T14:41:02Z"))
        return app

    def test_a_jd_pasted_for_a_role_applied_to_earlier_shows_in_ready(self):
        old = self.applied_row()
        rg_id = self.rg.add(PID, "Amazon", "Applied Scientist", JD, status="ready", created_at=db.iso(NOW))
        rgjobs.statuses(self.conn, PID)
        ready = self.board("ready")
        self.assertEqual([j["rq"]["rg"] for j in ready["jobs"]], [rg_id])
        self.assertNotEqual(ready["jobs"][0]["outside"], old)                 # its own Saved row
        self.assertEqual(ready["counts"]["ready"], 1)

    def test_a_link_made_before_the_fix_heals_on_the_next_look(self):
        old = self.applied_row()
        rg_id = self.rg.add(PID, "Amazon", "Applied Scientist", JD, status="ready", created_at=db.iso(NOW))
        with db.tx(self.conn):                                               # what the live DB holds for job 331
            self.conn.execute("INSERT INTO rg_links(rg_id, profile, app_id, created_at) VALUES (?, ?, ?, ?)",
                              (rg_id, PID, old, db.iso(NOW)))
        rgjobs.statuses(self.conn, PID)
        self.assertNotEqual(outside.links(self.conn, PID)[rg_id], old)
        self.assertEqual([j["rq"]["rg"] for j in self.board("ready")["jobs"]], [rg_id])
        self.assertEqual(self.conn.execute("SELECT status FROM applications WHERE id = ?", (old,)).fetchone()[0],
                         "applied")                                          # the user's applied row is untouched

    def test_a_resume_made_before_he_applied_still_counts_as_applied(self):
        rg_id = self.rg.add(PID, "Recruiter Co", "AI Engineer", JD, status="ready", created_at="2026-10-01T10:00:00Z")
        rgjobs.statuses(self.conn, PID)
        app = outside.links(self.conn, PID)[rg_id]
        with db.tx(self.conn):
            self.conn.execute("UPDATE applications SET status = 'applied', applied_on = ? WHERE id = ?", (TODAY, app))
            self.conn.execute("INSERT INTO application_events(app_id, at, from_status, to_status, source) "
                              "VALUES (?, ?, 'saved', 'applied', 'ui')", (app, db.iso(NOW)))
        rgjobs.statuses(self.conn, PID)
        self.assertEqual(outside.links(self.conn, PID)[rg_id], app)
        self.assertEqual([j["rq"]["rg"] for j in self.board("done")["jobs"]], [rg_id])

    def test_a_resume_made_the_day_he_applied_with_only_the_date_known_stays_linked(self):
        """GOAL33 review: a row with an applied day but no applied event (a Sheet edit) was compared as text: any
        resume made that day looked "later" than the bare date and got a second Saved row."""
        rg_id = self.rg.add(PID, "Sheet Co", "AI Engineer", JD, status="ready", created_at=f"{TODAY}T15:00:00Z")
        rgjobs.statuses(self.conn, PID)
        app = outside.links(self.conn, PID)[rg_id]
        with db.tx(self.conn):
            self.conn.execute("UPDATE applications SET status = 'applied', applied_on = ? WHERE id = ?", (TODAY, app))
        rgjobs.statuses(self.conn, PID)
        self.assertEqual(outside.links(self.conn, PID)[rg_id], app)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM applications WHERE company = 'Sheet Co'").fetchone()[0], 1)

    def test_a_saved_row_for_the_same_role_is_still_reused(self):
        saved = trackapi.create(self.conn, PID, {"company": "Acme AI", "role_title": "ML Engineer", "status": "saved"})["id"]
        rg_id = self.rg.add(PID, "Acme AI", "ML Engineer", JD, status="ready", created_at=db.iso(NOW))
        rgjobs.statuses(self.conn, PID)
        self.assertEqual(outside.links(self.conn, PID)[rg_id], saved)


class OutsideCardTest(ResumesTabCase):
    def test_the_card_shows_the_jobs_place_never_his_address(self):
        rg_id = self.rg.add(PID, "Indeed", "Machine Learning Engineer", JD, status="ready",
                            created_at=db.iso(NOW), location="Austin, TX", workplace="hybrid")
        rgjobs.statuses(self.conn, PID)
        app = outside.links(self.conn, PID)[rg_id]
        with db.tx(self.conn):                              # Autofill keeps the address it typed (GOAL23 §7)
            self.conn.execute("UPDATE applications SET address = ? WHERE id = ?", (HOME, app))
        self.assertEqual(self.board("ready")["jobs"][0]["location"], "Austin, TX")
        with db.tx(self.conn):
            self.conn.execute("UPDATE applications SET status = 'applied', applied_on = ? WHERE id = ?", (TODAY, app))
        applied = appliedview.board(self.conn, PID, "all", None, NOW)["jobs"]
        self.assertEqual([c["location"] for c in applied if c.get("outside") == app], ["Austin, TX"])

    def test_no_place_known_shows_none_not_the_address(self):
        rg_id = self.rg.add(PID, "Recruiter Co", "AI Engineer", JD, status="ready", created_at=db.iso(NOW))
        rgjobs.statuses(self.conn, PID)
        app = outside.links(self.conn, PID)[rg_id]
        with db.tx(self.conn):
            self.conn.execute("UPDATE applications SET address = ? WHERE id = ?", (HOME, app))
        self.assertIsNone(self.board("ready")["jobs"][0]["location"])

    def test_the_match_comes_from_the_pasted_jd(self):
        self.rg.add(PID, "Recruiter Co", "Machine Learning Engineer", JD, status="ready", created_at=db.iso(NOW))
        rgjobs.statuses(self.conn, PID)
        card = self.board("ready")["jobs"][0]
        self.assertIsInstance(card["match"], int)
        self.assertEqual([p["key"] for p in card["parts"]][:2], ["experience", "skills"])
        self.assertNotIn("no_jd", card)

    def test_a_row_without_jd_text_says_so(self):
        rg_id = self.rg.add(PID, "Recruiter Co", "AI Engineer", "", status="ready", created_at=db.iso(NOW))
        rgjobs.statuses(self.conn, PID)
        card = self.board("ready")["jobs"][0]
        self.assertEqual((card["rq"]["rg"], card["match"], card.get("no_jd")), (rg_id, None, True))


class AppliedScoringTest(ResumesTabCase):
    def test_applied_never_scores_in_the_request_and_the_background_fills_it_in(self):
        import time
        from job_hunt_agent import jdsnap
        app = trackapi.create(self.conn, PID, {"company": "Recruiter Co", "role_title": "Machine Learning Engineer",
                                               "status": "saved"})["id"]
        jdsnap.keep(self.conn, app, None, JD, "pasted")
        with db.tx(self.conn):
            self.conn.execute("UPDATE applications SET status = 'applied', applied_on = ? WHERE id = ?", (TODAY, app))
        first = [c for c in appliedview.board(self.conn, PID, "all", None, NOW)["jobs"] if c.get("outside") == app][0]
        self.assertEqual((first["match"], first.get("scoring")), (None, True))
        for _ in range(100):                                          # the filler thread scores it
            if self.conn.execute("SELECT 1 FROM outside_fits WHERE app_id = ?", (app,)).fetchone():
                break
            time.sleep(0.05)
        later = [c for c in appliedview.board(self.conn, PID, "all", None, NOW)["jobs"] if c.get("outside") == app][0]
        self.assertIsInstance(later["match"], int)
        self.assertNotIn("scoring", later)
