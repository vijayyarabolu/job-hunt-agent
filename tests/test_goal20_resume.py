"""GOAL20 (user 22:21): the resume panel carries what a running Re-import is doing right now (resumegen's step:
"reading your Doc", "building the PDF", "checking"), so the busy button can say it; none once it has finished."""
from job_hunt_agent import rgjobs
from tests.test_goal8_flow import JD, PID, Goal8Case


class ReimportStepTest(Goal8Case):
    def test_the_panel_carries_the_running_step(self):
        rg_id = self.rg.add(PID, "Acme", "MLE", JD, status="checking",
                            step={"step": "building the PDF", "since": 1.0, "at": 2.0})
        self.assertEqual(rgjobs.detail(PID, rg_id)["step"], "building the PDF")

    def test_no_step_when_nothing_runs(self):
        rg_id = self.rg.add(PID, "Acme", "MLE", JD, status="ready")
        self.assertIsNone(rgjobs.detail(PID, rg_id)["step"])


class ListingTest(Goal8Case):
    def test_the_app_asks_for_every_resume_in_the_lean_form(self):
        """resumegen's plain list stops at the newest 200 (an older approved resume vanished from job_hunt_agent after
        a few days at ~50 a day); lean=1 lists all of them with the small result fields only."""
        from unittest import mock
        from job_hunt_agent import rgclient
        with mock.patch.object(rgclient, "request", return_value={"jobs": [{"id": 1, "profile": PID}]}) as req:
            self.assertEqual([j["id"] for j in rgjobs.listing(PID)], [1])
        self.assertIn("lean=1", req.call_args.args[1])


class ArchivedLeavesResumesTest(Goal8Case):
    def test_an_archived_resume_is_not_in_queue_or_ready(self):
        """GOAL20 (user 23:37): a resume archived (or deleted) in resumegen leaves job-hunt-agent's Resumes lists at once;
        live, Jordan's CLEAR / Genesys / Premier resumes archived at 04:18 still sat in job-hunt-agent's Ready."""
        from job_hunt_agent import rqueue
        from tests.test_goal8_flow import NOW
        kept = self.rg.add(PID, "Acme", "MLE", JD, source_job_id=self.uids[0], status="ready", finished_at="2026-10-12T20:00:00Z")
        self.rg.add(PID, "Acme", "MLE 2", JD, source_job_id=self.uids[1], status="ready", finished_at="2026-10-12T20:00:00Z",
                    archived_at="2026-10-13T04:18:09Z")
        self.rg.add(PID, "Acme", "MLE 3", JD, source_job_id=self.uids[2], status="queued", archived_at="2026-10-13T04:18:09Z")
        self.assertEqual([j["rq"]["rg"] for j in rqueue.board(self.conn, PID, "ready", NOW)["jobs"]], [kept])
        self.assertEqual(rqueue.board(self.conn, PID, "queue", NOW)["jobs"], [])


class RemoveArchivesTheResumeTest(Goal8Case):
    """GOAL20 (user 23:37): × Remove on a job in job-hunt-agent archives its resumes in resumegen (one source of truth), and
    Undo puts them back; resumegen offline: the job is still removed here."""
    def setUp(self):
        super().setUp()
        import threading
        from job_hunt_agent import server
        self.srv = server.make_server(lan=False, port=0)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        super().tearDown()

    def post(self, path, body):
        import http.client
        import json
        conn = http.client.HTTPConnection("127.0.0.1", self.srv.server_port, timeout=10)
        conn.request("POST", path, json.dumps({"profile": PID, **body}),
                     {"Host": "localhost", "X-Jobhunt": "1", "Content-Type": "application/json"})
        resp = conn.getresponse()
        out = json.loads(resp.read() or b"{}")
        conn.close()
        return resp.status, out

    def test_remove_archives_its_resumes_and_undo_restores_them(self):
        rg_id = self.rg.add(PID, "Acme", "MLE", JD, source_job_id=self.uids[0], status="ready")
        other = self.rg.add(PID, "Acme", "MLE", JD, source_job_id=self.uids[1], status="ready")
        status, out = self.post("/api/remove", {"uid": self.uids[0]})
        self.assertEqual(status, 200, out)
        self.assertEqual(out.get("resumes"), [rg_id])
        self.assertIsNotNone(self.rg.jobs[rg_id].get("archived_at"))
        self.assertIsNone(self.rg.jobs[other].get("archived_at"))
        status, _ = self.post("/api/remove/undo", {"id": out["removed"]})
        self.assertEqual(status, 200)
        self.assertIn((None, "unarchive", {"ids": [rg_id]}), self.rg.actions)

    def test_undo_puts_a_cancelled_queued_resume_back_in_the_queue(self):
        """Remove cancels a queued resume before archiving it; Undo queues it again (it was never made)."""
        rg_id = self.rg.add(PID, "Acme", "MLE", JD, source_job_id=self.uids[0], status="queued")
        status, out = self.post("/api/remove", {"uid": self.uids[0]})
        self.assertEqual((status, self.rg.jobs[rg_id]["status"]), (200, "cancelled"))
        status, back = self.post("/api/remove/undo", {"id": out["removed"]})
        self.assertEqual(status, 200, back)
        self.assertEqual((self.rg.jobs[rg_id]["status"], self.rg.jobs[rg_id].get("archived_at")), ("queued", None))
        self.assertNotIn("rg", back)

    def test_resumegen_offline_still_removes_the_job(self):
        self.rg.down = True
        status, out = self.post("/api/remove", {"uid": self.uids[0]})
        self.assertEqual(status, 200, out)
        self.assertEqual(out.get("resumes"), [])


class NotInterestedArchivesTheResumeTest(RemoveArchivesTheResumeTest):
    """GOAL20 (user 23:37 "one source of truth, both directions"): Not interested hid a job's resume from job_hunt_agent's
    Ready while resumegen kept it in its Ready. Now it is archived there too, and the job coming back (Undo, Restore,
    Save) restores it; a queued one is queued again."""
    def test_not_interested_archives_and_restore_brings_it_back(self):
        ready = self.rg.add(PID, "Acme", "MLE", JD, source_job_id=self.uids[0], status="ready")
        queued = self.rg.add(PID, "Acme", "MLE", JD, source_job_id=self.uids[0], status="queued")
        other = self.rg.add(PID, "Acme", "MLE", JD, source_job_id=self.uids[1], status="ready")
        status, out = self.post("/api/triage", {"uid": self.uids[0], "state": "dismissed"})
        self.assertEqual((status, sorted(out.get("resumes"))), (200, [ready, queued]))
        self.assertTrue(self.rg.jobs[ready].get("archived_at") and self.rg.jobs[queued].get("archived_at"))
        self.assertIsNone(self.rg.jobs[other].get("archived_at"))
        status, back = self.post("/api/triage", {"uid": self.uids[0], "state": "restored"})
        self.assertEqual((status, sorted(back.get("resumes"))), (200, [ready, queued]))
        self.assertEqual([self.rg.jobs[i].get("archived_at") for i in (ready, queued)], [None, None])
        self.assertEqual(self.rg.jobs[queued]["status"], "queued")
        status, again = self.post("/api/triage", {"uid": self.uids[0], "state": "saved"})
        self.assertEqual(again.get("resumes"), [])                       # nothing left to restore

    def test_a_job_without_resumes_and_resumegen_offline_still_triage(self):
        status, out = self.post("/api/triage", {"uid": self.uids[1], "state": "dismissed"})
        self.assertEqual((status, out.get("resumes")), (200, []))
        self.rg.down = True
        status, out = self.post("/api/triage", {"uid": self.uids[2], "state": "dismissed"})
        self.assertEqual((status, out.get("resumes")), (200, []))


class RestoreWhileOfflineTest(RemoveArchivesTheResumeTest):
    def test_a_restore_while_resumegen_is_offline_is_tried_again(self):
        """GOAL20 verifier: the archived ids were forgotten before resumegen was asked; offline, they stayed archived."""
        ready = self.rg.add(PID, "Acme", "MLE", JD, source_job_id=self.uids[0], status="ready")
        self.post("/api/triage", {"uid": self.uids[0], "state": "dismissed"})
        self.rg.down = True
        status, out = self.post("/api/triage", {"uid": self.uids[0], "state": "restored"})
        self.assertEqual((status, out.get("resumes")), (200, []))
        self.rg.down = False
        status, out = self.post("/api/triage", {"uid": self.uids[0], "state": "saved"})
        self.assertEqual((out.get("resumes"), self.rg.jobs[ready].get("archived_at")), ([ready], None))
