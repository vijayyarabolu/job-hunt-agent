"""GOAL4 §1/§3 (decision 007): keyword extraction (longest mention wins, plurals, counts, sections), per-resume
keyword % / matched / missing / best resume, the profile's resume list with the baseline.json drop-in fallback, and
Mark applied recording the resume used in the person's naming style."""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import docx

from job_hunt_agent import api, config, db, fits, keywords, lexicon, resumes, store
from job_hunt_agent.ats.common import FetchResult, make_job
from job_hunt_agent.resume import ats
from tests.test_server import seed_job


class ExtractionTest(unittest.TestCase):
    def test_aliases_normalize_and_the_longest_mention_wins(self):
        self.assertEqual(lexicon.find("Experience with AWS SageMaker"), {"SageMaker": 1})
        self.assertEqual(set(lexicon.find("LLMs, APIs and SDKs")), {"Large Language Models (LLM)", "API", "SDK"})
        self.assertEqual(lexicon.find("Spark SQL"), {"Apache Spark": 1})
        self.assertEqual(lexicon.find("Azure Data Factory"), {"Azure Data Factory": 1})
        self.assertEqual(lexicon.find("REST APIs"), {"REST APIs": 1})

    def test_products_are_not_merged(self):
        self.assertEqual(set(lexicon.find("Tableau or Looker")), {"Tableau", "Looker"})
        self.assertEqual(set(lexicon.find("Prometheus and Grafana")), {"Prometheus", "Grafana"})

    def test_soft_skills_and_generic_words_are_never_keywords(self):
        self.assertEqual(lexicon.find("Excellent communication, leadership, teamwork and problem-solving skills; "
                                      "run experiments, own observability."), {})

    def test_counts_sections_and_first_position(self):
        jd = "Requirements:\nPython and SQL. Python daily.\nNice to have:\nKubernetes, Python"
        terms = {t.name: t for t in ats.extract(jd)}
        self.assertEqual((terms["Python"].count, terms["Python"].weight, terms["Python"].need), (3, 1.0, True))
        self.assertEqual((terms["Kubernetes"].weight, terms["Kubernetes"].need), (0.5, False))
        self.assertLess(terms["Python"].first, terms["SQL"].first)
        self.assertGreater(terms["Python"].importance, terms["SQL"].importance)
        self.assertGreater(terms["SQL"].importance, terms["Kubernetes"].importance)

    def test_skipped_sections_do_not_count(self):
        jd = "Requirements:\nPython\nBenefits:\nFree Snowflake credits"
        self.assertEqual([t.name for t in ats.extract(jd)], ["Python"])

    def test_sections_after_an_about_heading_resume_on_common_headings(self):
        # Wayve, 2026-10-06: an emoji 'About our Engineering Teams' heading hid the whole JD (curly apostrophes,
        # 'day-to-day', 'You should apply if:', 'It would be great if you also have:')
        jd = ("\U0001f6e0\ufe0f About our Engineering Teams\nWe drive cars.\n\U0001f9e0 Your day-to-day\nRun Spark jobs.\n"
              "\U0001f9e9 What you\u2019ll be working on:\nDashboards in Tableau\n\U0001f64c You should apply if:\n"
              " - You know Python and SQL\nIt would be great if you also have:\n - Airflow")
        got = {t.name: t.weight for t in ats.extract(jd)}
        self.assertEqual(got, {"Apache Spark": 1.0, "Tableau": 1.0, "Python": 1.0, "SQL": 1.0, "Airflow": 0.5})

    def test_a_company_blurb_skip_that_swallows_the_whole_jd_is_undone(self):
        # Zocdoc / Turing (verifier round 2): 'About ...' first, then no heading the extractor recognizes
        jd = ("About Zocdoc\nYou call. You wait. You book in seconds.\nYou\u2019ll be successful in this role if you\u2026\n"
              "- Have deep expertise in SQL and strong proficiency in Python or R.\nBenefits:\nFree Snowflake credits")
        self.assertEqual({t.name for t in ats.extract(jd)}, {"SQL", "Python", "R"})

    def test_a_company_blurb_is_still_skipped_when_the_role_has_counted_lines(self):
        jd = "About Acme\nWe run Kubernetes at scale.\nRequirements:\nPython"
        self.assertEqual([t.name for t in ats.extract(jd)], ["Python"])

    def test_more_about_headings_are_role_sections(self):
        # Signify / HelloFresh: 'More about you', "S'more about the team" hold the requirements
        jd = "About Signify\nWe make lights.\nMore about you\nPython\nS'more about the team\nSQL"
        self.assertEqual({t.name for t in ats.extract(jd)}, {"Python", "SQL"})


class KeywordScoresTest(unittest.TestCase):
    R = (resumes.Resume("ai", "AI Engineer", None, ("ai_engineer",), frozenset(), ""),
         resumes.Resume("de", "Data Engineer", None, ("data_engineer",), frozenset(), ""))

    def pack(self) -> str:
        terms = [ats.Term("Python", 1.0, True, 3, 5), ats.Term("Airflow", 1.0, True, 2, 30),
                 ats.Term("RAG", 1.0, True, 1, 20), ats.Term("Spark", 0.5, False, 1, 60)]
        return json.dumps(keywords.pack(terms, (frozenset({"Python", "RAG"}), frozenset({"Python", "Airflow", "Spark"}))))

    def test_pct_is_matched_over_extracted_and_missing_by_importance(self):
        ai, de = keywords.score_all(self.pack(), self.R)
        self.assertEqual((ai.pct, ai.matched, ai.missing), (50, ("Python", "RAG"), ("Airflow", "Spark")))
        self.assertEqual((de.pct, de.matched, de.missing), (75, ("Python", "Airflow", "Spark"), ("RAG",)))
        self.assertEqual(keywords.best([ai, de]).id, "de")

    def test_ties_go_to_the_title_mapped_resume_then_weighted(self):
        a = keywords.ResumeScore("a", "A", 50, 0.4, (), (), 2)
        b = keywords.ResumeScore("b", "B", 50, 0.6, (), (), 2)
        self.assertEqual(keywords.best([a, b]).id, "b")
        self.assertEqual(keywords.best([a, b], title_id="a").id, "a")      # never "B scores higher" on equal %

    def test_display_ignore_and_weighted_mode_knobs(self):
        with mock.patch.object(lexicon, "settings", return_value={"display_ignore": ["Spark"], "pct_mode": "count",
                                                                  "min_corpus_jds": 3}):
            self.assertEqual(keywords.score_all(self.pack(), self.R)[0].n_total, 3)
        with mock.patch.object(lexicon, "settings", return_value={"display_ignore": [], "pct_mode": "weighted",
                                                                  "min_corpus_jds": 3}):
            ai = keywords.score_all(self.pack(), self.R)[0]
            self.assertEqual(ai.pct, round(100 * ai.weighted))

    def test_old_four_field_rows_still_read(self):
        (ai,) = keywords.score_all(json.dumps([["Python", 1.0, 1, 1], ["SQL", 1.0, 0, 1]]), self.R[:1])
        self.assertEqual(ai.pct, 50)


def _write_docx(path: Path, text: str) -> None:
    document = docx.Document()
    for line in text.splitlines():
        document.add_paragraph(line)
    document.save(path)


class ProfileResumesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.patch = mock.patch.object(config, "PROFILES", self.root)
        self.patch.start()
        (self.root / "p" / "resumes").mkdir(parents=True)

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_no_resume_list_uses_baseline_json_as_main(self):
        (self.root / "p" / "baseline.json").write_text(json.dumps(
            {"headline": "AI Engineer", "summary": "Builds RAG with Python.", "skills": [], "experience": []}))
        (main,) = resumes.of_profile({"id": "p", "headline_default": "AI Engineer", "families": ["ai_engineer"]})
        self.assertEqual((main.id, main.label, main.file), ("main", "AI Engineer", None))
        self.assertTrue({"Python", "Retrieval Augmented Generation (RAG)"} <= main.terms)

    def test_drop_in_files_and_a_resumes_list_need_no_code(self):
        _write_docx(self.root / "p" / "resumes" / "A.docx", "Skills: Python, PyTorch")
        _write_docx(self.root / "p" / "resumes" / "B.docx", "Skills: SQL, Airflow")
        prof = {"id": "p", "resumes": [{"id": "ml", "label": "ML Engineer", "file": "A.docx", "families": ["ml_engineer"]},
                                       {"id": "de", "label": "Data Engineer", "file": "B.docx",
                                        "families": ["data_engineer"]},
                                       {"id": "gone", "file": "missing.docx"}]}
        loaded = resumes.of_profile(prof)
        self.assertEqual([r.id for r in loaded], ["ml", "de"])                 # an unreadable file is skipped
        self.assertEqual(loaded[1].terms, frozenset({"SQL", "Airflow"}))
        self.assertEqual(resumes.for_families(loaded, ["data_engineer"]).id, "de")
        inp = fits.profile_inputs({**prof, "families": ["ml_engineer"], "dealbreakers": []})
        self.assertEqual(len(inp.resume_terms), 2)

    def test_bad_lists_are_refused(self):
        for bad in ({"id": "x"}, [{"id": "x"}], [{"id": "x", "file": "../a.docx"}],
                    [{"id": "x", "file": "a.docx"}, {"id": "x", "file": "b.docx"}]):
            with self.assertRaises(ValueError, msg=bad):
                resumes.validate(bad)

    def test_file_name_in_each_persons_style(self):
        ml = resumes.Resume("ml", "ML Engineer", "Alex_Rivera_ML_Engineer_Resume.docx", (), frozenset(), "")
        alex = {"id": "alex", "name": "Alex Rivera", "resume_file_pattern": "{name}_{title}_Resume({company}).pdf"}
        jordan = {"name": "Jordan Lee", "headline_default": "AI Engineer",          # no id: no base file to find
                 "resume_file_pattern": "{name}_{title}({company}).pdf"}
        self.assertEqual(resumes.file_name(alex, ml, "Acme / Labs"), "Alex_Rivera_ML_Engineer_Resume(Acme   Labs).pdf")
        self.assertEqual(resumes.file_name(jordan, None, "Loop"), "Jordan_Lee_AI_Engineer(Loop).pdf")


class RefreshStopTest(unittest.TestCase):
    """A fetch run stops rebuilding fits at its CPU ceiling; rows left over stay stale for the next run."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = mock.patch.object(config, "DB_PATH", Path(self.tmp.name) / "t.db")
        self.patch.start()
        self.conn = db.connect()
        items = [make_job(ats="lever", key="acme", ext_id=str(i), company="Acme", title="Machine Learning Engineer",
                          location="Austin, TX", url=f"https://jobs.lever.co/acme/{i}", posted_at=db.iso(db.utcnow()),
                          description="Requirements:\nPython, PyTorch, Kubernetes and AWS.") for i in range(3)]
        db.sync_boards(self.conn, [{"board": "lever:acme", "ats": "lever", "key": "acme", "company": "Acme"}])
        run = db.start_run(self.conn, "test", db.utcnow())
        store.write_board(self.conn, db.board(self.conn, "lever:acme"), FetchResult(items, 200, n_raw=3), "ok", run,
                          db.utcnow(), ["fam:ml_engineer"], 5)

    def tearDown(self):
        self.conn.close()
        self.patch.stop()
        self.tmp.cleanup()

    def test_stop_ends_the_rebuild_between_chunks(self):
        profile = config.profile("alex")
        with mock.patch.object(fits, "REFRESH_CHUNK", 1):
            written = fits.refresh(self.conn, profile, db.utcnow(), stop=lambda: True)
        self.assertEqual((written, fits.pending(self.conn, profile)), (1, 2))
        self.assertEqual(fits.refresh(self.conn, profile, db.utcnow()), 2)

    def test_pending_is_remembered_until_the_fits_or_jobs_move(self):
        profile = config.profile("alex")
        self.assertEqual(fits.pending(self.conn, profile), 3)
        with mock.patch.object(fits, "_count_stale", side_effect=AssertionError("recounted")):
            self.assertEqual(fits.pending(self.conn, profile), 3)          # nothing moved: the kept count
        with mock.patch.object(fits, "REFRESH_CHUNK", 1):
            fits.refresh(self.conn, profile, db.utcnow(), stop=lambda: True)
        self.assertEqual(fits.pending(self.conn, profile), 2)              # a fit landed: counted again
        self.conn.execute("UPDATE boards SET active = 0")
        self.assertEqual(fits.pending(self.conn, profile), 0)              # a board switched off: counted again


class MarkAppliedResumeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = mock.patch.object(config, "DB_PATH", Path(self.tmp.name) / "t.db")
        self.patch.start()
        self.conn = db.connect()
        self.uid = seed_job(self.conn)
        fits.refresh(self.conn, config.profile("alex"), db.utcnow())

    def tearDown(self):
        self.conn.close()
        self.patch.stop()
        self.tmp.cleanup()

    def test_records_the_best_resume_and_prefills_the_resume_cell(self):
        app_id = api.mark_applied(self.conn, "alex", self.uid, datetime.now(timezone.utc))
        row = self.conn.execute("SELECT resume, resume_used, match_pct FROM applications WHERE id = ?",
                                (app_id,)).fetchone()
        label = next(r.label for r in fits.profile_inputs(config.profile("alex")).resumes if r.id == row["resume_used"])
        self.assertEqual(row["resume"], f"Alex_Rivera_{label.replace(' ', '_')}_Resume(Acme).pdf")   # base name + company
        self.assertIsNotNone(row["match_pct"])

    def test_marked_with_a_file_leaves_the_resume_cell_to_the_upload(self):
        from job_hunt_agent import applydialog
        app_id = applydialog.mark(self.conn, "alex", datetime.now(timezone.utc), uid=self.uid,
                                  resume_id="data_engineer", with_file=True)
        row = self.conn.execute("SELECT resume, resume_used, resume_missing FROM applications WHERE id = ?",
                                (app_id,)).fetchone()
        self.assertEqual((row["resume"] or "", row["resume_used"], row["resume_missing"]), ("", "data_engineer", 0))

    def test_a_picked_resume_is_used_and_a_typed_resume_is_kept(self):
        app_id = api.mark_applied(self.conn, "alex", self.uid, datetime.now(timezone.utc), "data_engineer")
        row = self.conn.execute("SELECT resume, resume_used FROM applications WHERE id = ?", (app_id,)).fetchone()
        self.assertEqual((row["resume_used"], row["resume"]),     # the name follows the ML role (user 2026-10-15)
                         ("data_engineer", "Alex_Rivera_ML_Engineer_Resume(Acme).pdf"))
        with db.tx(self.conn):
            self.conn.execute("UPDATE applications SET resume = 'my_own.pdf' WHERE id = ?", (app_id,))
        api.mark_applied(self.conn, "alex", self.uid, datetime.now(timezone.utc))
        self.assertEqual(self.conn.execute("SELECT resume FROM applications WHERE id = ?", (app_id,)).fetchone()[0],
                         "my_own.pdf")

    def test_card_shows_the_best_and_detail_shows_all_four(self):
        detail = api.job_detail(self.conn, "alex", self.uid, datetime.now(timezone.utc))
        view = detail["card"]["resumes"]
        self.assertEqual([r["id"] for r in view["all"]], ["ai_engineer", "ml_engineer", "data_scientist",
                                                           "data_engineer"])
        self.assertEqual(view["best"]["pct"], max(r["pct"] for r in view["all"]))
        self.assertIn("matched", view["all"][0])

    def test_a_jd_without_tracked_keywords_says_so_instead_of_showing_nothing(self):
        with self.conn:
            self.conn.execute("UPDATE fits SET terms = '[]', jd_state = 'thin' WHERE uid = ?", (self.uid,))
        view = api.job_detail(self.conn, "alex", self.uid, datetime.now(timezone.utc))["card"]["resumes"]
        self.assertTrue(view["empty"])
        self.assertEqual(view["short"], "no tracked skill keywords in this JD: no Lever estimate")
        self.assertEqual((view["how"], view["all"], view["best"]), ("/how/ats?vendor=lever", [], None))


if __name__ == "__main__":
    unittest.main()
