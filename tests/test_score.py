"""Worth score v5 (decision 002): worked examples, hand-ranked pairwise gate, chip invariants, push gating,
section-aware ATS coverage and the applied-before matcher."""
import itertools
import json
import unittest
from datetime import date, datetime, timedelta, timezone

from job_hunt_agent import applied, fits, lexicon, score
from job_hunt_agent.resume import ats

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
CTX = score.Ctx(now=NOW, profile_id="alex", years=4, known=frozenset({"Python"}), has_baseline=True)


def iso(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def fit_row(required_hits=7, required=8, preferred_hits=1, preferred=2, state="ok", extra=(), dealbreaker=None):
    terms = [[f"R{i}", 1.0, int(i < required_hits), 1] for i in range(required)]
    terms += [[f"P{i}", 0.5, int(i < preferred_hits), 0] for i in range(preferred)]
    return {"jd_state": state, "terms": json.dumps(terms + list(extra)), "dealbreaker": dealbreaker}


def job(**over) -> dict:
    """Example A of decision 002: Lever Senior MLE, San Francisco, 5 h old, $190-240k, named team, 5+ years."""
    row = {"uid": "lever:acme:1", "ats": "lever", "company": "Acme", "title": "Senior Machine Learning Engineer",
           "location": "San Francisco, CA", "is_us": 1, "req_id": None, "has_team": 1, "salary_min": 190000,
           "salary_max": 240000, "min_years": 5, "is_talent_pool": 0, "precision": "exact",
           "posted_at": iso(5), "first_posted_at": iso(5), "first_seen": iso(4), "seed": 0, "bumped_at": None,
           "chain_origin": None, "chain_n": 0, "repost_count": 0, "closed_at": None, "stale": 0}
    return {**row, **over}


class ChipTest(unittest.TestCase):
    """Release review: the fit chip shows its denominator; a tailored-not-sent job is flagged, not a cold lead."""

    def test_fit_chip_shows_term_count(self):
        result = score.final(job(), fit_row(), CTX)
        self.assertTrue(any(c.startswith("Fit ") and " of 10 skills" in c for c in result.chips), result.chips)

    def test_tailored_job_gets_pipeline_chip_first(self):
        ctx = score.Ctx(now=NOW, profile_id="alex", years=4, known=frozenset({"Python"}), has_baseline=True,
                        pipeline={"lever:acme:1": "tailored"})
        result = score.final(job(), fit_row(), ctx)
        self.assertEqual((result.chips[0], result.pipeline), ("Tailored · apply now", "tailored"))

    def test_stale_workday_job_is_marked_unverified(self):
        self.assertIn("Unverified 7d+", score.final(job(stale=1), fit_row(), CTX).chips)
        self.assertNotIn("Unverified 7d+", score.final(job(), fit_row(), CTX).chips)

    def test_no_pipeline_chip_for_untouched_job(self):
        result = score.final(job(), fit_row(), CTX)
        self.assertIsNone(result.pipeline)
        self.assertNotIn("Tailored · apply now", result.chips)


class RankingQualityTest(unittest.TestCase):
    """years filter, evidence shrinkage, unscored jobs, contract roles, staffing posts, sponsorship label."""
    CTX6 = score.Ctx(now=NOW, profile_id="alex", years=4, known=frozenset({"Python"}), has_baseline=True,
                     max_years=6, sponsorship_label=True)

    def test_jd_asking_more_than_max_years_is_too_senior_and_never_pushes(self):
        for years in (7, 8, 12):                              # Wells Fargo 7+, NVIDIA 8+, Cerebras 12+
            result = score.final(job(min_years=years), fit_row(), self.CTX6)
            self.assertEqual(result.state, "too_senior")
            self.assertFalse(result.push)
            self.assertIn(f"Too senior · {years}+ yrs", result.chips)

    def test_jd_asking_exactly_max_years_is_penalized_not_hidden(self):
        at_max = score.final(job(min_years=6), fit_row(), self.CTX6)
        below = score.final(job(min_years=5), fit_row(), self.CTX6)
        self.assertEqual(at_max.state, "ok")
        self.assertLess(at_max.worth, below.worth)
        self.assertIn("6+ yrs", at_max.chips)

    def test_five_of_five_skills_ranks_below_twenty_of_twenty(self):
        few = score.final(job(), fit_row(required_hits=5, required=5, preferred_hits=0, preferred=0), self.CTX6)
        many = score.final(job(), fit_row(required_hits=20, required=20, preferred_hits=0, preferred=0), self.CTX6)
        self.assertLess(few.parts["F"], many.parts["F"])
        self.assertIn("Low evidence", few.chips)
        self.assertNotIn("Low evidence", many.chips)

    def test_no_skills_listed_is_neutral(self):
        self.assertAlmostEqual(score._fit({"jd_state": "ok", "terms": "[]"}, self.CTX6)["F"], 0.6)   # the prior

    def test_pending_fit_shows_no_number(self):
        result = score.final(job(), None, self.CTX6)
        self.assertEqual((result.scored, result.band, result.state), (False, "Scoring", "pending"))

    def test_contract_role_is_labelled_but_not_penalized(self):
        full = score.final(job(employment_type="full_time"), fit_row(), self.CTX6)
        contract = score.final(job(employment_type="contract"), fit_row(), self.CTX6)
        c2h = score.final(job(employment_type="contract_to_hire"), fit_row(), self.CTX6)
        self.assertEqual(full.worth, contract.worth)
        self.assertEqual(full.worth, c2h.worth)
        self.assertIn("Contract", contract.chips)
        self.assertIn("Contract-to-hire", c2h.chips)
        self.assertNotIn("Full-time", full.chips)

    def test_staffing_harvest_post_is_penalized(self):
        normal = score.final(job(staffing=1), fit_row(), self.CTX6)
        harvest = score.final(job(staffing=2), fit_row(), self.CTX6)
        self.assertEqual(normal.mults.get("Staffing post?"), None)
        self.assertEqual(harvest.mults["Staffing post?"], 0.5)
        self.assertLess(harvest.worth, normal.worth)

    def test_sponsorship_label_only_when_the_profile_asks(self):
        self.assertIn("No sponsorship", score.final(job(sponsorship="no"), fit_row(), self.CTX6).chips)
        self.assertNotIn("No sponsorship", score.final(job(sponsorship="no"), fit_row(), CTX).chips)
        self.assertEqual(score.final(job(sponsorship="no"), fit_row(), self.CTX6).worth,
                         score.final(job(), fit_row(), self.CTX6).worth)


class WorkedExamplesTest(unittest.TestCase):
    def test_example_a_scores_92_and_pushes(self):
        result = score.final(job(), fit_row(), CTX)
        self.assertEqual(result.worth, 92)
        self.assertAlmostEqual(result.parts["F"], 0.775, places=3)      # F = (hw + 1.8) / (tw + 3)
        self.assertEqual((result.parts["Q"], result.parts["S"]), (1.0, 1.0))
        self.assertTrue(result.push)
        self.assertEqual(result.band, "Apply today")
        self.assertEqual(result.chips[0], "Posted 5h ago")

    def test_example_a_prime_repost_chain_scores_53_and_never_pushes(self):
        row = job(chain_origin=iso(31 * 24), chain_n=1, repost_count=1)
        result = score.final(row, fit_row(), CTX)
        self.assertEqual(result.worth, 53)
        self.assertFalse(result.push)
        self.assertEqual(result.chips[0], "Reposted ×1 · first seen 31d ago")

    def test_example_b_old_java_workday_repost_collapses(self):
        row = job(uid="workday:acme/wd1/ext:JR1", ats="workday", title="Machine Learning Engineer", precision="day",
                  posted_at=iso(45), first_posted_at=iso(45), req_id="JR1", has_team=0, salary_min=None,
                  salary_max=None, min_years=8, chain_origin=iso(75 * 24), chain_n=1)
        java = [["Java", 1.0, 0, 1]]
        result = score.final(row, fit_row(required_hits=5, required=10, preferred_hits=0, preferred=0, extra=java),
                             CTX)
        self.assertEqual(result.worth, 11)          # GOAL2 A.3 fit prior: F .523 (was .333 -> 9)
        self.assertEqual(set(result.mults), {"Reposted ×1", "Open 75d", "Needs Java"})
        self.assertEqual(result.band, "Low")


class RankingGateTest(unittest.TestCase):
    """Hand-ranked fixtures (best first). Pairwise agreement must stay >= 0.9 (decision 002 CI gate)."""

    HAND_RANKED = [     # a person's judgment of "which would I rather apply to first", best first
        ("fresh, strong fit, salary, US", job(), fit_row()),
        ("fresh, strong fit, no salary", job(salary_min=None, salary_max=None), fit_row()),
        ("1 day old, strong fit", job(first_posted_at=iso(30), posted_at=iso(30)), fit_row()),
        ("fresh, middling fit", job(), fit_row(required_hits=4)),
        ("fresh, strong fit, 10+ years", job(min_years=10), fit_row(required_hits=6)),
        ("3 days old, strong fit", job(first_posted_at=iso(72), posted_at=iso(72)), fit_row()),
        ("fresh, weak fit", job(), fit_row(required_hits=1, preferred_hits=0)),
        ("repost chain 31d", job(chain_origin=iso(744), chain_n=1), fit_row()),
        ("fresh, needs Java and Scala", job(), fit_row(extra=[["Java", 1.0, 0, 1], ["Scala", 1.0, 0, 1]])),
        ("fresh talent pool", job(is_talent_pool=1), fit_row()),
        ("70 days old, weak fit", job(first_posted_at=iso(70 * 24), posted_at=iso(70 * 24)),
         fit_row(required_hits=2)),
        ("fresh non-US", job(is_us=0, location="Toronto, ON"), fit_row()),
    ]

    def test_pairwise_agreement(self):
        worths = [score.final(row, fit, CTX).worth for _, row, fit in self.HAND_RANKED]
        pairs = list(itertools.combinations(range(len(worths)), 2))
        agree = sum(worths[i] >= worths[j] for i, j in pairs) / len(pairs)
        self.assertGreaterEqual(agree, 0.9, list(zip([n for n, _, _ in self.HAND_RANKED], worths)))


class Goal3StateTest(unittest.TestCase):
    """clearance titles are filtered even before the fit exists; a non-US job never reaches a list."""
    CTX_DB = score.Ctx(now=NOW, profile_id="alex", years=4, known=frozenset({"Python"}), has_baseline=True,
                       dealbreakers=(r"\bpolygraph\b", r"\bTS\s*/\s*SCI\b"))

    def test_clearance_title_is_filtered_with_or_without_a_fit(self):
        title = "AI Engineer Associate - TS/SCI with Polygraph"
        for fit in (None, fit_row()):
            result = score.final(job(title=title), fit, self.CTX_DB)
            self.assertEqual((result.state, result.dealbreaker), ("filtered", r"\bpolygraph\b"))
        self.assertEqual(score.final(job(), fit_row(), self.CTX_DB).state, "ok")

    def test_non_us_job_gets_its_own_hidden_state(self):
        row = job(location="Remote - Poland; Remote - Bulgaria", is_us=0, title="Generative AI Developer")
        self.assertEqual(score.final(row, fit_row(), self.CTX_DB).state, "non_us")
        clearance_abroad = job(is_us=0, title="ML Engineer - TS/SCI")
        self.assertEqual(score.final(clearance_abroad, fit_row(), self.CTX_DB).state, "filtered")

    def test_senior_title_without_years_stays_visible(self):
        # GOAL8 §4 (user decision 2026-10-07) replaced GOAL3's "Senior title reads as 5+ years"
        row = job(title="Senior Machine Learning Engineer", min_years=None)
        junior = score.Ctx(now=NOW, profile_id="jordan", years=2, known=frozenset(), has_baseline=True, max_years=4)
        result = score.final(row, fit_row(), junior)
        self.assertEqual(result.state, "ok")
        self.assertIn("Senior title", result.chips)               # still a seniority penalty for a 2-year profile
        mid = score.Ctx(now=NOW, profile_id="alex", years=4, known=frozenset(), has_baseline=True, max_years=6)
        self.assertEqual(score.final(row, fit_row(), mid).state, "ok")
        stated = job(title="Sr. AI/Data Science Engineer", min_years=2)          # Medtronic: years stated and fit
        self.assertEqual(score.final(stated, fit_row(), junior).state, "ok")

    def test_unknown_us_stays_visible_with_a_penalty(self):
        result = score.final(job(location="Remote", is_us=None), fit_row(), self.CTX_DB)
        self.assertEqual(result.state, "ok")
        self.assertIn("US unclear", result.chips)


class ChipsAndGatesTest(unittest.TestCase):
    def test_every_penalty_has_a_chip(self):
        row = job(is_talent_pool=1, is_us=None, chain_origin=iso(800), chain_n=2, min_years=9)
        result = score.final(row, fit_row(extra=[["C++", 1.0, 0, 1]]), CTX)
        for name in result.mults:
            self.assertIn(name, result.chips)
        self.assertIn("9+ yrs", result.chips)

    def test_at_most_one_unknown_data_chip(self):
        result = score.final(job(), fit_row(required=2, preferred=0, required_hits=2, state="thin"), CTX)
        unknown = {"Scoring…", "No JD", "JD unreadable", "Thin JD", "Fit not scored", "Date unknown"}
        self.assertEqual(len(unknown & set(result.chips)), 1)

    def test_dealbreaker_filters_and_missing_fit_is_pending(self):
        self.assertEqual(score.final(job(), fit_row(dealbreaker="clearance"), CTX).state, "filtered")
        pending = score.final(job(), None, CTX)
        self.assertEqual((pending.state, pending.push), ("pending", False))
        self.assertIn("Scoring…", pending.chips)

    def test_seed_rows_get_no_freshness_and_no_push(self):
        result = score.final(job(seed=1), fit_row(), CTX)
        self.assertEqual((result.parts["R"], result.push, result.chips[0]), (0.0, False, "Backlog (first run)"))

    def test_thin_jd_pushes_only_when_fully_covered_and_fresh(self):
        full = fit_row(required=3, required_hits=3, preferred=0, state="thin")
        self.assertTrue(score.final(job(), full, CTX).push)
        partial = fit_row(required=3, required_hits=2, preferred=0, state="thin")
        self.assertFalse(score.final(job(), partial, CTX).push)

    def test_profile_without_baseline_uses_degraded_push(self):
        ctx = score.Ctx(now=NOW, profile_id="partner", years=None, known=frozenset(), has_baseline=False)
        result = score.final(job(), fit_row(), ctx)
        self.assertIn("Fit not scored", result.chips)
        self.assertEqual(result.parts["F"], 0.5)
        self.assertEqual(result.parts["S"], 0.8)

    def test_same_role_application_flags_and_mutes(self):
        ref = applied.AppliedRef("acme", applied.normalize_title("Sr. Machine Learning Engineer"),
                                 date(2026, 8, 14), None)
        ctx = score.Ctx(now=NOW, profile_id="alex", years=4, known=frozenset(), has_baseline=True, applied=(ref,))
        result = score.final(job(), fit_row(), ctx)
        self.assertIn("Applied Aug 14", result.chips)
        self.assertFalse(result.push)
        self.assertTrue(result.muted)                 # the digest skips it too (verifier M8 round 2)
        self.assertEqual(result.worth, 92)            # a flag, never a score change

    def test_role_far_above_profile_years_never_pushes(self):
        result = score.final(job(min_years=8), fit_row(), CTX)
        self.assertLess(result.parts["S"], score.MIN_PUSH_SENIORITY)
        self.assertFalse(result.push)
        high = score.final(job(min_years=8, salary_min=250000, salary_max=300000), fit_row(required_hits=8), CTX)
        self.assertNotEqual(high.band, "Apply today")                # never labelled "apply today" either

    def test_remote_us_counts_as_specific_but_bare_country_does_not(self):
        self.assertTrue(score.specific_us({"is_us": 1, "location": "Remote - US"}))
        self.assertFalse(score.specific_us({"is_us": 1, "location": "United States"}))
        self.assertFalse(score.specific_us({"is_us": 1, "location": "7 Locations"}))


class CoverageTest(unittest.TestCase):
    JD = ("About Acme\nAcme runs Kubernetes and Snowflake.\nWhat you'll do\nBuild RAG pipelines with LangChain "
          "and Python.\nRequirements:\n3+ years with PyTorch\nExperience with Java and Ray is a plus.\n"
          "Nice to have\nSpark, Airflow\nBenefits\nWe pay for AWS certifications.")

    def test_sections_weight_terms(self):
        terms = {t.name: t for t in ats.extract(self.JD)}
        self.assertNotIn("Kubernetes", terms)                 # company intro
        self.assertNotIn("AWS", terms)                        # benefits
        self.assertEqual((terms["PyTorch"].weight, terms["PyTorch"].need), (1.0, True))
        self.assertEqual(terms["Ray"].weight, 0.5)            # inline 'a plus'
        self.assertEqual(terms["Airflow"].weight, 0.5)        # 'Nice to have' section

    def test_ignored_language_counts_only_when_known(self):
        terms = ats.extract("Requirements:\nJava and Python and PyTorch")
        self.assertEqual(ats.coverage(terms, {"Python", "PyTorch"}).pct, 1.0)
        self.assertEqual(ats.coverage(terms, {"Python", "PyTorch"}, {"Java"}).pct, 2 / 3)

    def test_fit_compute_never_raises(self):
        inp = fits.ProfileInputs("x", ("ml_engineer",), True, frozenset({"Python"}), "b", "l", ("(",), "d")
        with self.assertLogs("job_hunt_agent.fits", "WARNING"):
            self.assertEqual(fits.compute("ML Engineer", b"not zlib", inp)["jd_state"], "unreadable")


class ShortNameTest(unittest.TestCase):
    """'R' and 'Go' count only as items of a list, so 'R&D', 'Go-to-market' and 'Go/No-Go' never match."""

    def test_short_names_match_in_lists_and_skill_lines(self):
        for text in ("Python, R, SQL", "Python or R.", "R or Matlab", "(R, Python)", "Programming: Python, SQL, R",
                     "Python\nR\nSQL"):
            self.assertIn("R", lexicon.find(text), text)
        for text in ("Go, Python or Java", "experience in Golang", "Languages: Rust, Go", "Go\n"):
            self.assertIn("Go", lexicon.find(text), text)
        for text in ("Experience with R and Python", "proficient in R", "like R or Matlab", "SQL, Python, or R to extract"):
            self.assertIn("R", lexicon.find(text), text)
        for text in ("Strong Python and Go — scale", "C++ or Go is a plus", "entire Go, Python, and Typescript stack"):
            self.assertIn("Go", lexicon.find(text), text)

    def test_short_names_ignore_prose(self):
        for text in ("Our R&D team", "Go-to-market strategy", "Go/No-Go decisions", "Let's go build",
                     "Plan R for the year", "We go, you follow"):
            found = lexicon.find(text)
            self.assertNotIn("R", found, text)
            self.assertNotIn("Go", found, text)

    def test_generic_words_count_only_as_skills_in_lists(self):
        prose = ("We drive cost optimization and search engine optimization. Write regression tests. Video streaming "
                 "and lambda expressions. X-ray imaging.")
        found = lexicon.find(prose)
        for name in ("Optimization", "Regression Models", "Streaming", "Lambda", "Ray"):
            self.assertNotIn(name, found, name)
        found = lexicon.find("Tools: Spark, Ray, Dask. Methods: Statistics, Optimization, Causal Inference. "
                             "AWS: S3, Lambda, ECS. Logistic regression.")
        for name in ("Ray", "Optimization", "Lambda", "Regression Models"):
            self.assertIn(name, found, name)

    def test_common_jd_phrasings(self):
        found = lexicon.find("Statistical techniques. API development. Productionization of models. "
                             "Experiment design. Econometrics, MATLAB and optimization models.")
        for name in ("Statistics", "REST APIs", "Model Deployment", "Experimentation", "Econometrics", "MATLAB",
                     "Optimization"):
            self.assertIn(name, found)

    def test_resume_terms_reads_headline_summary_skills_and_bullets(self):
        resume = {"headline": "ML Engineer", "summary": "Builds with PyTorch.",
                  "skills": [{"category": "Programming", "items": ["Python", "R"]}],
                  "experience": [{"bullets": ["Deployed Kubernetes services for teams."]}]}
        self.assertTrue({"PyTorch", "Python", "R", "Kubernetes"} <= ats.resume_terms(resume))


class AppliedMatchTest(unittest.TestCase):
    def test_company_variants(self):
        for a, b in [("JP Morgan", "JPMorgan Chase & Co."), ("Meta", "Meta Platforms, Inc."),
                     ("Capital One", "capitalone")]:
            with self.subTest(a=a):
                self.assertTrue(applied.same_company(applied.company_key(a), applied.company_key(b)))
        for a, b in [("Ro", "Ross Stores"), ("Accenture", "Accenture Federal Services"), ("Cohere", "Cohere Health"),
                     ("Samsara", "Samara"), ("Meta", "Metabase"), ("Intuit", "Intuitive Surgical"), ("Ramp", "Rampart")]:
            with self.subTest(a=a, b=b):
                self.assertFalse(applied.same_company(applied.company_key(a), applied.company_key(b)))
        self.assertTrue(applied.same_company(applied.company_key("Abnormal AI"),
                                             applied.company_key("Abnormal Security")))

    def test_ampersand_and_the_word_and_are_the_same_company(self):
        # Jordan's typed "Robots & Pencils" row did not match the feed's "Robots and Pencils" posting
        self.assertEqual(applied.company_key("Robots & Pencils"), applied.company_key("Robots and Pencils"))
        self.assertEqual(applied.company_key("Johnson & Johnson"), applied.company_key("Johnson and Johnson"))
        from job_hunt_agent.gsync_cycle import similar_company
        self.assertTrue(similar_company("Robots & Pencils", "Robots and Pencils"))

    def test_title_match_needs_both_jaccard_and_ratio(self):
        t = applied.normalize_title
        self.assertTrue(applied.same_title(t("Sr. ML Engineer"), t("Senior Machine Learning Engineer")))
        self.assertFalse(applied.same_title(t("Machine Learning Engineer, Ads"), t("Data Engineer, Payments")))


if __name__ == "__main__":
    unittest.main()
