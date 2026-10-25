"""GOAL13 against a throwaway DB and the fake resumegen of the GOAL8 tests: the Resumes tab's Queue / Ready / Done
today (order, positions, needs-you first, applied and passed jobs out, outside jobs in), Cancel and Move to top,
"Added to queue (#n)", closed postings (§2c), today's progress (§2d), a Yes approving the ready resume (§2a),
Settings for the goal / Apply-in-hand / answers (§2b, §2g)."""
import json
import os
from datetime import date, timedelta
from unittest import mock

from job_hunt_agent import api, applyback, config, db, history, outside, outside_jobs, profile_settings, resume_upload, rgjobs, rqueue, server, trackapi
from tests.test_goal8_flow import JD, NOW, PID, FakeResumegen, Goal8Case, seed_job

TODAY = rqueue.today(NOW)
TODAY_DATE = date.fromisoformat(TODAY)


class ResumesTabCase(Goal8Case):
    def setUp(self):
        super().setUp()
        self.ids = mock.patch.object(config, "profile_ids", lambda: [PID])
        self.ids.start()

    def tearDown(self):
        self.ids.stop()
        super().tearDown()

    def add(self, uid, status, **extra):
        return self.rg.add(PID, "Acme", "Machine Learning Engineer", JD, source_job_id=uid, status=status, **extra)

    def board(self, view):
        return rqueue.board(self.conn, PID, view, NOW)

    def applied(self, uid, day=TODAY):
        app = trackapi.create(self.conn, PID, {"job_uid": uid, "status": "saved"})["id"]
        with db.tx(self.conn):
            self.conn.execute("UPDATE applications SET status = 'applied', applied_on = ? WHERE id = ?", (day, app))
        return app


class QueueTest(ResumesTabCase):
    def test_running_first_then_the_order_they_will_run_then_waiting(self):
        more = [seed_job(self.conn, str(n)) for n in range(4, 7)]
        a = self.add(self.uids[0], "queued")
        b = self.add(self.uids[1], "queued", priority=1)                 # Move to top: runs next
        c = self.add(self.uids[2], "generating", started_at="2026-10-08T16:00:00Z")
        d = self.add(more[0], "waiting")
        e = self.add(more[1], "queued")
        self.add(more[2], "cancelled")
        got = self.board("queue")
        self.assertEqual([j["rq"]["rg"] for j in got["jobs"]], [c, b, a, e, d])
        self.assertEqual([j["rq"]["pos"] for j in got["jobs"]], [None, 1, 2, 3, 4])
        self.assertEqual([j["rq"]["state"] for j in got["jobs"]], ["generating", "queued", "queued", "queued", "waiting"])
        self.assertEqual(got["counts"], {"queue": 5, "ready": 0, "done": 0, "needs": 0})
        self.assertEqual(got["jobs"][1]["title"], "Machine Learning Engineer")      # the feed card itself
        self.assertIn("match", got["jobs"][1])

    def test_cancel_and_move_to_top_go_to_resumegen(self):
        rg_id = self.add(self.uids[0], "queued")
        rgjobs.action(PID, rg_id, "top")
        rgjobs.action(PID, rg_id, "cancel")
        self.assertEqual([(a[0], a[1]) for a in self.rg.actions], [(rg_id, "top"), (rg_id, "cancel")])
        with self.assertRaises(ValueError):
            rgjobs.action(PID, rg_id, "delete")

    def test_generate_says_each_jobs_place_in_the_queue(self):
        self.add(self.uids[0], "queued")
        out = rgjobs.generate(self.conn, PID, self.uids[1:])
        self.assertEqual(out["positions"], {self.uids[1]: 2, self.uids[2]: 3})

    def test_an_outside_job_waits_in_the_queue_with_its_tracker_row(self):
        rg_id = self.rg.add(PID, "Recruiter Co", "AI Engineer", JD, status="queued", jd_url="https://ex.com/p")
        rgjobs.statuses(self.conn, PID)                                    # adopts it
        row = outside.saved_rows(self.conn, PID)[0]["id"]
        card = self.board("queue")["jobs"][0]
        # GOAL36 §5.1: its card is its job record's, an inside card with the "outside job" tag
        self.assertEqual((card["uid"], card["outside_job"], card["company"], card["apply_url"], card["rq"]["rg"]),
                         (outside_jobs.uid_of(PID, row), True, "Recruiter Co", "https://ex.com/p", rg_id))
        self.assertIn(outside_jobs.uid_of(PID, row), rgjobs.statuses(self.conn, PID)["jobs"])    # its resume chip


class ReadyTest(ResumesTabCase):
    def test_needs_you_on_top_then_newest_ready_first(self):         # newest at the top
        more = [seed_job(self.conn, str(n)) for n in range(4, 6)]
        new = self.add(self.uids[0], "ready", finished_at="2026-10-08T15:00:00Z")
        old = self.add(self.uids[1], "ready", finished_at="2026-10-08T09:00:00Z", drive_file_id="F")
        needs = self.add(self.uids[2], "needs_you", finished_at="2026-10-08T16:00:00Z", error="page 2")
        failed = self.add(more[0], "failed", finished_at="2026-10-08T10:00:00Z")
        got = self.board("ready")
        self.assertEqual([j["rq"]["rg"] for j in got["jobs"]], [needs, failed, new, old])
        self.assertEqual([j["rq"]["state"] for j in got["jobs"]], ["needs", "needs", "ready", "ready"])
        self.assertTrue(got["jobs"][3]["rq"]["approved"])
        self.assertEqual(got["counts"]["needs"], 2)
        self.assertEqual(got["jobs"][0]["rq"]["why"], "page 2")
        self.assertNotIn(more[1], [j["uid"] for j in got["jobs"]])          # no resume: not here

    def test_applied_and_passed_jobs_leave_ready(self):
        for uid in self.uids:
            self.add(uid, "ready", finished_at="2026-10-08T09:00:00Z")
        self.applied(self.uids[0], day="2026-10-01")
        api.triage(self.conn, PID, self.uids[1], "dismissed", NOW)
        got = self.board("ready")
        self.assertEqual([j["uid"] for j in got["jobs"]], [self.uids[2]])
        self.assertEqual(got["counts"]["done"], 0)                       # applied on another day

    def test_a_closed_posting_drops_to_the_bottom_with_its_badge(self):
        self.add(self.uids[0], "ready", finished_at="2026-10-08T09:00:00Z")
        self.add(self.uids[1], "ready", finished_at="2026-10-08T10:00:00Z")
        with db.tx(self.conn):
            self.conn.execute("UPDATE jobs SET closed_at = ? WHERE uid = ?", (db.iso(NOW), self.uids[0]))
        got = self.board("ready")["jobs"]
        self.assertEqual([(j["uid"], j["rq"]["closed"]) for j in got], [(self.uids[1], False), (self.uids[0], True)])


class DoneTodayTest(ResumesTabCase):
    def test_applied_today_from_this_list_with_the_days_progress(self):
        for uid in self.uids:
            self.add(uid, "ready", finished_at="2026-10-08T09:00:00Z")
        app = self.applied(self.uids[0])
        trackapi.create(self.conn, PID, {"company": "Elsewhere", "role_title": "DS", "status": "saved"})
        got = self.board("done")
        self.assertEqual([(j["uid"], j["rq"]["app"]) for j in got["jobs"]], [(self.uids[0], app)])
        self.assertEqual((got["applied_today"], got["goal"]), (1, profile_settings.DEFAULT_GOAL))
        self.assertEqual(got["counts"], {"queue": 0, "ready": 2, "done": 1, "needs": 0})
        polled = rgjobs.statuses(self.conn, PID, now=NOW)
        self.assertEqual((polled["counts"]["ready"], polled["applied_today"]), (2, 1))

    def test_offline_still_says_todays_progress(self):
        self.rg.stop()
        got = self.board("ready")
        self.assertEqual((got["online"], got["jobs"], got["applied_today"]), (False, [], 0))
        self.assertEqual(rgjobs.statuses(self.conn, PID, now=NOW)["goal"], profile_settings.DEFAULT_GOAL)
        self.rg = FakeResumegen()                                           # tearDown stops it again

    def test_a_bad_view_is_refused(self):
        with self.assertRaises(ValueError):
            self.board("everything")


class UndoToReadyTest(ResumesTabCase):
    """Done today → Undo goes back through the edits after the applied mark (verifier: one Undo only reverted the
    sheet fields filled after Yes)."""
    def test_undo_reverts_the_later_edits_and_the_applied_mark(self):
        app = trackapi.create(self.conn, PID, {"job_uid": self.uids[0], "status": "saved"})["id"]
        with history.action("Alex"):
            trackapi.update(self.conn, PID, app, {"status": "applied"}, TODAY_DATE)
        with history.action("Alex"):
            trackapi.update(self.conn, PID, app, {"notes": "referral from Sam"}, TODAY_DATE)
        out = trackapi.undo_to_ready(self.conn, PID, app, "Alex", TODAY_DATE)
        self.assertEqual((out["row"]["status"], out["row"]["notes"] or ""), ("saved", ""))

    def test_without_an_applied_mark_in_its_history_only_the_last_edit_goes(self):
        app = self.applied(self.uids[0])                                   # marked outside the edit history
        with history.action("Alex"):
            trackapi.update(self.conn, PID, app, {"notes": "first"}, TODAY_DATE)
        with history.action("Alex"):
            trackapi.update(self.conn, PID, app, {"notes": "second"}, TODAY_DATE)
        out = trackapi.undo_to_ready(self.conn, PID, app, "Alex", TODAY_DATE)
        self.assertEqual((out["row"]["status"], out["row"]["notes"]), ("applied", "first"))


class ClosedPostingTest(ResumesTabCase):
    def test_a_queued_generation_for_a_closed_posting_is_cancelled(self):
        gone = self.add(self.uids[0], "queued")
        kept = self.add(self.uids[1], "queued")
        running = self.add(self.uids[2], "generating")
        with db.tx(self.conn):
            self.conn.executemany("UPDATE jobs SET closed_at = ? WHERE uid = ?",
                                  [(db.iso(NOW), self.uids[0]), (db.iso(NOW), self.uids[2])])
        got = self.board("queue")
        self.assertEqual(self.rg.actions, [(gone, "cancel", {})])         # a running one is left to finish
        self.assertEqual([j["rq"]["rg"] for j in got["jobs"]], [running, kept])
        self.assertEqual(got["cancelled_closed"], 1)
        self.rg.jobs[gone]["status"] = "queued"                            # (the fake does not cancel by itself)
        polled = rgjobs.statuses(self.conn, PID, now=NOW)                  # the poll sweeps too, chips agree
        self.assertNotIn(self.uids[0], polled["jobs"])


class YesApprovesTest(ResumesTabCase):
    @mock.patch.object(resume_upload, "upload")
    def test_yes_approves_the_ready_resume_then_attaches_it(self, upload):
        rg_id = self.add(self.uids[0], "ready", finished_at="2026-10-08T09:00:00Z")
        entry = applyback.remember(self.conn, PID, NOW, uid=self.uids[0])
        out = applyback.answer(self.conn, PID, entry, "yes", NOW + timedelta(minutes=5))
        self.assertEqual(self.rg.actions[0][:2], (rg_id, "approve"))
        self.assertEqual(out["attached"]["kind"], "tailored")
        self.assertEqual(trackapi.get(self.conn, PID, out["app"])["status"], "applied")
        upload.assert_not_called()                                         # the tailored file is already in Drive

    def test_an_approved_resume_is_not_approved_twice(self):
        self.add(self.uids[0], "ready", drive_file_id="F1", drive_link="https://d/F1", drive_name="T.pdf")
        self.assertIsNone(rgjobs.approve_ready(self.conn, PID, self.uids[0]))
        self.assertIsNone(rgjobs.approve_ready(self.conn, PID, self.uids[1]))   # no resume at all
        self.assertEqual(self.rg.actions, [])


class ApplySettingsTest(ResumesTabCase):
    def test_goal_toggle_and_answers_are_saved_per_person(self):
        got = profile_settings.apply_get(PID)
        self.assertEqual((got["daily_goal"], got["apply_in_hand"], got["answers"]["years"]), (15, True, "4 years"))
        out = profile_settings.set_apply(PID, {"goal": 8, "in_hand": False,
                                               "answers": {"salary": " $150k  base ", "linkedin": "https://li/x"}})
        self.assertEqual((out["daily_goal"], out["apply_in_hand"]), (8, False))
        self.assertEqual((out["answers"]["salary"], out["answers"]["linkedin"]), ("$150k base", "https://li/x"))
        path = config.PROFILES / PID / "answers.json"
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(path.read_text())["salary"], "$150k base")
        self.assertIn("daily_goal: 8\n", (config.PROFILES / PID / "profile.yaml").read_text())
        self.assertEqual(rqueue.progress(self.conn, PID, NOW)["goal"], 8)

    def test_bad_settings_change_nothing(self):
        for bad in ({"goal": 101}, {"goal": "x"}, {"in_hand": "yes"}, {"answers": {"ssn": "1"}},
                    {"answers": {"salary": "x" * 301}}, {"goal": 5, "answers": {"salary": 3}}):
            with self.assertRaises(ValueError):
                profile_settings.set_apply(PID, bad)
        self.assertEqual(profile_settings.apply_get(PID)["daily_goal"], 15)
        self.assertFalse((config.PROFILES / PID / "answers.json").exists())

    def test_profile_yaml_fills_the_answers_left_empty(self):
        with mock.patch.object(config, "profile", lambda pid: {"years": 3, "linkedin": "https://li/s",
                                                               "github": "https://gh/s", "needs_sponsorship": True}):
            got = profile_settings.answers(PID)
        self.assertEqual((got["linkedin"], got["github"], got["years"], got["work_auth"], got["start"]),
                         ("https://li/s", "https://gh/s", "3 years", "Will require visa sponsorship: Yes", ""))


    def test_a_cleared_answer_stays_cleared(self):
        self.assertEqual(profile_settings.answers(PID)["years"], "4 years")          # from profile.yaml
        profile_settings.set_apply(PID, {"answers": {"years": ""}})
        self.assertEqual(profile_settings.answers(PID)["years"], "")

    def test_an_answers_file_that_is_not_an_object_is_ignored(self):
        (config.PROFILES / PID / "answers.json").write_text("[]")
        self.assertEqual(profile_settings.answers(PID)["years"], "4 years")
        self.assertEqual(profile_settings.set_apply(PID, {"answers": {"start": "2 weeks"}})["answers"]["start"], "2 weeks")

class RouteTest(ResumesTabCase):
    def test_the_jobs_route_serves_the_resumes_tab_and_settings_add_the_goal13_parts(self):
        self.add(self.uids[0], "ready")
        got = server.route(self.conn, "GET", "/api/jobs", {"profile": PID, "tab": "resumes", "rq": "ready"}, NOW)
        self.assertEqual((got["tab"], got["rq"], len(got["jobs"])), ("resumes", "ready", 1))
        settings = server.route(self.conn, "GET", "/api/settings", {"profile": PID}, NOW)
        self.assertIn("answers", settings)
        self.assertIn("max_required_years", settings)
        out = server.route(self.conn, "POST", "/api/settings/apply", {"profile": PID, "goal": 3}, NOW)
        self.assertEqual(out["daily_goal"], 3)
