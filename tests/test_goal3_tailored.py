"""resumegen GOAL3 §4 / §5 / §6 in job-hunt-agent: the match follows a ready tailored resume (stored, that job only, base kept
beside it, base again when the resume is gone, stored numbers kept while resumegen is offline), and the side panel's
Looks good / Fix with note / Restore / Regenerate with current resume, Outdated badge and made time."""
import io
import unittest
from unittest import mock

import docx

from job_hunt_agent import api, config, feedcache, filters, fits, rgjobs, tailored
from tests.test_goal8_flow import JD, NOW, PID, Goal8Case, seed_job

DOCX = tailored.DOCX_TYPE
WIDE_JD = ("Requirements:\n" + "Build ML systems in Python and PyTorch on Kubernetes and AWS. "
           "Pipelines in Spark, Airflow, Snowflake, dbt and Kafka; models served with Docker and MLflow. " * 3)


def resume_docx(text: str) -> bytes:
    doc = docx.Document()
    doc.add_paragraph(text)
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


TAILORED = resume_docx("Tess Tester. ML Engineer. Python, PyTorch, Kubernetes, AWS, Spark, Airflow, Snowflake, dbt, "
                       "Kafka, Docker, MLflow. 2021 - 2025.")


class TailoredScoreCase(Goal8Case):
    def setUp(self):
        super().setUp()
        self.wide = seed_job(self.conn, "7", title="Machine Learning Engineer, Data", description=WIDE_JD)
        fits.refresh(self.conn, config.profile(PID), NOW)
        feedcache.clear()

    def tearDown(self):
        feedcache.clear()
        super().tearDown()

    def ready(self, uid: str, made: str = "2026-10-07T21:30:00Z", data: bytes = TAILORED, ctype: str = DOCX,
              **extra) -> int:
        rg_id = self.rg.add(PID, "Acme", "MLE", WIDE_JD, source_job_id=uid, status="ready",
                            times={"made": made, "updated": None}, result={"base": {"id": "ml"}}, **extra)
        self.rg.files[(rg_id, "docx")] = (data, ctype)
        return rg_id

    def poll(self) -> dict:
        return rgjobs.statuses(self.conn, PID, lambda jobs: api.sync_tailored(self.conn, PID, jobs, NOW))

    def cards(self, **args) -> dict:
        feedcache.clear()
        with mock.patch.object(config, "profile_ids", lambda: [PID]):
            got = api.jobs(self.conn, PID, "recommended", NOW, filters.parse(args))
        return {c["uid"]: c for c in got["jobs"]}


class TailoredScoreTest(TailoredScoreCase):
    def setUp(self):
        super().setUp()
        self.ids = mock.patch.object(config, "profile_ids", lambda: [PID])
        self.ids.start()

    def tearDown(self):
        self.ids.stop()
        super().tearDown()

    def test_a_ready_resume_raises_that_jobs_match_only_and_keeps_the_base_beside_it(self):
        before = self.cards()
        self.ready(self.wide)
        got = self.poll()
        after = self.cards()
        card = after[self.wide]
        self.assertGreater(card["match"], before[self.wide]["match"])
        self.assertEqual(card["match_base"], before[self.wide]["match"])
        self.assertEqual(card["tailored"]["pct"], card["match"])
        self.assertEqual(got["scores"][self.wide]["pct"], card["match"])
        others = [u for u in before if u != self.wide]            # same-title jobs collapse into one row
        self.assertTrue(others)
        for uid in others:                                        # other jobs: same number, no tailored fields
            self.assertEqual(after[uid]["match"], before[uid]["match"])
            self.assertNotIn("match_base", after[uid])

    def test_the_same_scorer_with_the_base_text_gives_the_base_number(self):
        base = (config.PROFILES / PID / "resumes" / "Tess_ML.docx").read_bytes()
        self.ready(self.wide, data=base)
        self.poll()
        card = self.cards()[self.wide]
        self.assertEqual(card["match"], card["match_base"])

    def test_stored_once_not_fetched_on_every_poll_and_rescored_when_the_resume_changes(self):
        rg_id = self.ready(self.wide)
        self.poll()
        self.poll()
        self.assertEqual(self.rg.file_gets, [(rg_id, "docx")])
        self.rg.jobs[rg_id]["times"] = {"made": "2026-10-07T21:30:00Z", "updated": "2026-10-08T09:00:00Z"}
        self.poll()                                              # accepted / re-imported: a new stamp
        self.assertEqual(len(self.rg.file_gets), 2)

    def test_regenerating_falls_back_to_the_base_until_the_new_resume_is_ready(self):
        rg_id = self.ready(self.wide)
        self.poll()
        self.rg.jobs[rg_id]["status"] = "queued"
        self.poll()
        self.assertNotIn("tailored", self.cards()[self.wide])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM tailored_scores").fetchone()[0], 0)

    def test_resumegen_offline_keeps_the_stored_scores(self):
        self.ready(self.wide)
        self.poll()
        self.rg.down = True
        got = self.poll()
        self.assertFalse(got["online"])
        self.assertIn(self.wide, got["scores"])
        self.assertIn("tailored", self.cards()[self.wide])

    def test_an_unreadable_file_keeps_the_base_score_and_is_not_fetched_again(self):
        self.ready(self.wide, data=b"%PDF-1.4", ctype="application/pdf")
        self.poll()
        self.poll()
        self.assertEqual(len(self.rg.file_gets), 1)
        self.assertNotIn("tailored", self.cards()[self.wide])

    def test_a_missing_file_keeps_the_base_score_and_is_not_fetched_again(self):
        # resumegen answered 404 for job 38's .docx; the KeyError failed the whole sync on every poll
        rg_id = self.ready(self.wide)
        self.rg.files[(rg_id, "docx")] = None
        with self.assertNoLogs("job_hunt_agent.api", level="ERROR"):
            self.poll()
        self.poll()
        self.assertEqual(len(self.rg.file_gets), 1)
        self.assertNotIn("tailored", self.cards()[self.wide])

    def test_a_poll_during_a_sync_does_not_score_the_same_resumes_again(self):
        # the laptop and the phone polled at once and every new resume was downloaded twice
        self.ready(self.wide)
        real, inner = tailored.rgclient.request, []

        def request(method, path, **kw):
            if "/file/" in path and not inner:
                inner.append(self.poll())                          # the other device's poll, mid-sync
            return real(method, path, **kw)
        with mock.patch.object(tailored.rgclient, "request", side_effect=request):
            self.poll()
        self.assertEqual(len(self.rg.file_gets), 1)
        self.assertIn("tailored", self.cards()[self.wide])

    def test_best_match_and_the_floor_use_the_tailored_number_for_that_job(self):
        before = self.cards(sort="match")
        self.ready(self.wide)
        self.poll()
        tailored_pct = self.cards()[self.wide]["match"]
        floor = before[self.wide]["match"] + 1
        self.assertLessEqual(floor, tailored_pct)
        self.assertIn(self.wide, self.cards(min_match=str(floor)))
        self.assertEqual(list(before)[-1], self.wide)            # base 65%: last under Best match
        ordered = list(self.cards(sort="match").values())
        self.assertEqual([c["match"] for c in ordered], sorted((c["match"] for c in ordered), reverse=True))

    def test_the_job_panel_card_shows_the_tailored_number(self):
        self.ready(self.wide)
        self.poll()
        detail = api.job_detail(self.conn, PID, self.wide, NOW)
        self.assertEqual(detail["card"]["match"], detail["card"]["tailored"]["pct"])


class PanelActionsTest(TailoredScoreCase):
    def test_needs_you_looks_good_and_fix_with_note(self):
        rg_id = self.rg.add(PID, "Acme", "MLE", JD, status="needs_you",
                            reasons=[{"area": "Length", "text": "page 2 has 3 lines"}])
        self.assertEqual(rgjobs.detail(PID, rg_id)["reasons"][0]["text"], "page 2 has 3 lines")
        rgjobs.action(PID, rg_id, "accept")
        self.assertEqual(self.rg.actions[-1], (rg_id, "accept", {}))
        with self.assertRaisesRegex(ValueError, "write what to fix"):
            rgjobs.action(PID, rg_id, "fix", "  ")
        rgjobs.action(PID, rg_id, "fix", "only make the summary shorter")
        self.assertEqual(self.rg.actions[-1], (rg_id, "fix", {"note": "only make the summary shorter"}))

    def test_restore_unarchives_through_resumegen(self):
        rg_id = self.ready(self.wide, archived_at="2026-10-07T22:00:00.123Z")
        self.assertTrue(self.poll()["jobs"][self.wide]["archived"])
        out = rgjobs.action(PID, rg_id, "restore")
        self.assertEqual(self.rg.actions[-1], (None, "unarchive", {"ids": [rg_id]}))
        self.assertIsNone(out["panel"]["archived_at"])

    def test_outdated_badge_made_time_and_regenerate_with_current_resume(self):
        rg_id = self.ready(self.wide, badge="Outdated – made from your old resume",
                           outdated=["made from your old resume"])
        chip = self.poll()["jobs"][self.wide]
        self.assertEqual((chip["badge"], chip["made"]), ("Outdated – made from your old resume", "2026-10-07T21:30:00Z"))
        panel = rgjobs.detail(PID, rg_id)
        self.assertEqual(panel["outdated"], ["made from your old resume"])
        self.assertEqual(panel["times"]["made"], "2026-10-07T21:30:00Z")
        rgjobs.action(PID, rg_id, "regenerate", None, current=True)
        self.assertEqual(self.rg.actions[-1], (rg_id, "regenerate", {"current": True}))


if __name__ == "__main__":
    unittest.main()
