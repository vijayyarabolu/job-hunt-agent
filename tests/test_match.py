"""Match breakdown: industry table, experience / skills / industry / title parts and their "why" lines,
the overall %, labels, "Why you're a good fit" and "Gaps" bullets. Pure functions only."""
import json
import unittest
from datetime import datetime, timedelta, timezone

from job_hunt_agent import match, score

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
FAMILIES = ("ai_engineer", "genai_engineer", "ml_engineer", "dl_engineer", "data_scientist", "data_engineer")
WHO = match.Who(years=4, max_years=6, families=FAMILIES, industries=("real estate", "financial services", "SaaS"),
                known=frozenset(), has_baseline=True, sponsorship_label=True)


def iso(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def terms(hits: int, total: int, missing_name: str = "BigQuery") -> str:
    packed = [[f"Skill{i}", 1.0, 1, 1] for i in range(hits)]
    packed += [[missing_name if i == 0 else f"Gap{i}", 1.0, 0, 1] for i in range(total - hits)]
    return json.dumps(packed)


def row(**over) -> dict:
    base = {"uid": "lever:acme:1", "company": "Acme", "title": "Machine Learning Engineer", "location": "Austin, TX",
            "families": ",ml_engineer,", "min_years": 3, "salary_min": 150000, "salary_max": 190000, "has_team": 1,
            "precision": "exact", "posted_at": iso(3), "first_posted_at": iso(3), "first_seen": iso(3), "seed": 0,
            "bumped_at": None, "chain_origin": None, "chain_n": 0, "repost_count": 0, "closed_at": None, "stale": 0,
            "fit_terms": terms(9, 10), "jd_state": "ok", "workplace": None, "sponsorship": None}
    return {**base, **over}


def scored(**over) -> score.Score:
    base = dict(state="ok", worth=80, band="Apply today", parts={}, mults={}, chips=(), push=False)
    return score.Score(**{**base, **over})


class IndustryTest(unittest.TestCase):
    def test_company_name_and_jd_keywords_pick_the_industry(self):
        jd = "We serve asset management and custody clients across capital markets."
        self.assertEqual(match.industry_of("Deloitte Services", jd), "financial services")

    def test_benefits_lines_and_known_employers(self):
        jd = "We offer medical, dental and life insurance.\nBuild data pipelines for our customers."
        self.assertIsNone(match.industry_of("Acme", jd))                              # perks are not an industry
        self.assertEqual(match.industry_of("Wells Fargo", "Build CRM integrations on our cloud platform."),
                         "financial services")
        self.assertEqual(match.industry_of("Allstate", "Protect patients' clinical data."), "insurance")

    def test_keywords_count_in_any_case(self):
        # a substring check in lower case now skips industries with no keyword (cold feed 18 s -> ~7 s)
        self.assertEqual(match.industry_of("Acme", "Our FinTech platform serves BANKS. Banking APIs."),
                         "financial services")
        self.assertEqual(match.industry_of("ACME INSURANCE", "Build data pipelines."), "insurance")

    def test_thin_or_tied_evidence_is_unknown(self):
        self.assertIsNone(match.industry_of("Acme", "Build ML systems with Python."))
        self.assertIsNone(match.industry_of("Acme", "healthcare; retail"))         # 2 vs 2: a tie

    def test_part_same_related_unknown_other(self):
        self.assertEqual(match.industry_part("financial services", WHO).pct, 100)
        related = match.industry_part("market data", WHO)
        self.assertEqual((related.pct, related.why), (70, "Market data: close to your financial services background"))
        self.assertEqual(match.industry_part(None, WHO).pct, match.INDUSTRY_PCT["unknown"])
        other = match.industry_part("healthcare", WHO)
        self.assertEqual((other.pct, other.why), (35, "Healthcare: a new domain for you"))

    def test_profile_industries_match_case_insensitively(self):
        who = match.Who(years=2, industries=("Software Services", "IT services"))
        self.assertEqual(match.industry_part("software services", who).pct, 100)
        self.assertEqual(match.industry_part("SaaS", who).pct, 70)


class ExperienceTest(unittest.TestCase):
    def test_years_gap_scale(self):
        cases = {None: 85, 3: 100, 4: 100, 5: 85, 6: 65, 7: 45}
        for min_years, pct in cases.items():
            self.assertEqual(match.experience_part(min_years, WHO).pct, pct, min_years)

    def test_why_lines(self):
        self.assertEqual(match.experience_part(3, WHO).why, "You have 4 yrs; JD asks 3+")
        self.assertEqual(match.experience_part(5, WHO).why, "JD asks 5+ yrs; you have 4")
        self.assertIn("too senior", match.experience_part(8, WHO).why)

    def test_unknown_profile_years_is_left_out(self):
        self.assertIsNone(match.experience_part(3, match.Who(years=None)).pct)


class SkillsTest(unittest.TestCase):
    def test_counts_hits_and_names_missing(self):
        part = match.skills_part(terms(9, 10), "ok", WHO)
        self.assertEqual(part.why, "Your resume has 9/10 JD keywords; missing: BigQuery")
        self.assertEqual((part.pct, part.missing), (90, ("BigQuery",)))

    def test_known_skills_do_not_count_in_the_keyword_pct(self):
        """Decision 007: the keyword % reads the resume only (known_skills still help the ranking fit)."""
        who = match.Who(years=4, known=frozenset({"BigQuery"}))
        self.assertEqual(match.skills_part(terms(9, 10), "ok", who).pct, 90)

    def test_best_of_several_resumes_and_title_mapping(self):
        ai = match._OneResume("ai_engineer", "AI Engineer", ("ai_engineer",))
        ml = match._OneResume("ml_engineer", "ML Engineer", ("ml_engineer",))
        packed = json.dumps([["Python", 1.0, 3, 1], ["PyTorch", 1.0, 2, 1], ["RAG", 1.0, 1, 1], ["Spark", 1.0, 0, 1]])
        who = match.Who(years=4, families=FAMILIES, resumes=(ai, ml))
        scores, top, title = match.resume_scores({"fit_terms": packed, "families": ",ai_engineer,"}, who)
        self.assertEqual([(s.id, s.pct) for s in scores], [("ai_engineer", 50), ("ml_engineer", 50)])
        self.assertEqual((top.id, title), ("ai_engineer", "ai_engineer"))       # tie -> the title-mapped resume
        part = match.skills_part(packed, "ok", who, ",ml_engineer,")
        self.assertEqual((part.pct, part.why), (50, "ML Engineer resume has 2/4 JD keywords; missing: RAG, Spark"))

    def test_no_baseline_pending_or_no_jd_have_no_number(self):
        self.assertIsNone(match.skills_part(terms(1, 2), "ok", match.Who(years=4, has_baseline=False)).pct)
        self.assertIsNone(match.skills_part(None, None, WHO).pct)
        self.assertIsNone(match.skills_part("[]", "none", WHO).pct)

    def test_thin_jd_is_marked(self):
        self.assertTrue(match.skills_part(terms(2, 3), "thin", WHO).why.endswith("(short JD)"))


class TitleTest(unittest.TestCase):
    def test_levels(self):
        cases = {"Senior ML Engineer": "senior", "Machine Learning Engineer II": "mid", "Data Engineer I": "junior",
                 "AI / ML Engineer": "mid", "Staff ML Engineer": "lead", "Member of Technical Staff, AI": "mid",
                 "ML Engineer III": "senior", "Associate Data Scientist": "junior"}
        for title, level in cases.items():
            self.assertEqual(match.level_of(title), level, title)

    def test_family_and_seniority(self):
        self.assertEqual(match.title_part(row(), WHO).pct, 100)
        self.assertEqual(match.title_part(row(), WHO).why, "ML Engineer track; mid-level title fits 4 yrs")
        lead = match.title_part(row(title="Lead ML Engineer"), WHO)
        self.assertEqual(lead.pct, 65)
        self.assertIn("is above", lead.why)
        self.assertEqual(match.title_part(row(families=",data_engineer,", title="Data Engineer"), WHO).pct, 85)

    def test_junior_profile_sees_senior_as_far_above(self):
        who = match.Who(years=1, families=FAMILIES)
        self.assertEqual(match.title_part(row(title="Senior ML Engineer"), who).pct, match.LEVEL_FAR)


class BreakdownTest(unittest.TestCase):
    def test_overall_is_weighted_mean_over_known_parts(self):
        out = match.breakdown(row(), WHO, "financial services")
        self.assertEqual([p.pct for p in out["parts"]], [100, 90, 100, 100])
        self.assertEqual(out["pct"], round(.25 * 100 + .40 * 90 + .15 * 100 + .20 * 100))

    def test_missing_skills_part_renormalizes(self):
        out = match.breakdown(row(fit_terms=None), WHO, None)
        expected = (.25 * 100 + .15 * 60 + .20 * 100) / .60
        self.assertEqual(out["pct"], round(expected))


class LabelsTest(unittest.TestCase):
    def test_fresh_job_is_early_with_age_salary_and_level(self):
        lab = match.labels(row(), scored(), NOW, WHO)
        self.assertEqual((lab["posted"], lab["early"], lab["salary"]), ("3 h 0 min ago", True, "$150k to 190k"))
        self.assertEqual(lab["seniority"], "Mid-level · 3+ yrs")

    def test_repost_is_never_early(self):
        self.assertFalse(match.labels(row(repost_count=1, precision="unknown"), scored(), NOW, WHO)["early"])
        self.assertFalse(match.labels(row(chain_origin=iso(400), chain_n=1), scored(), NOW, WHO)["early"])

    def test_old_job_shows_days(self):
        self.assertEqual(match.labels(row(first_posted_at=iso(80)), scored(), NOW, WHO)["posted"], "3 d 8 h ago")

    def test_remote_from_location_and_applied_before_and_too_senior(self):
        lab = match.labels(row(location="Remote - US"), scored(state="too_senior", chips=("Applied Mar 05",)), NOW, WHO)
        self.assertEqual((lab["workplace"], lab["applied_before"], lab["too_senior"]), ("remote", "Applied Mar 05", True))

    def test_sponsorship_only_when_profile_asks_and_jd_says(self):
        self.assertEqual(match.labels(row(sponsorship="no"), scored(), NOW, WHO)["sponsorship"], "No sponsorship")
        self.assertIsNone(match.labels(row(sponsorship=None), scored(), NOW, WHO)["sponsorship"])
        quiet = match.Who(years=4, sponsorship_label=False)
        self.assertIsNone(match.labels(row(sponsorship="no"), scored(), NOW, quiet)["sponsorship"])


class BulletsTest(unittest.TestCase):
    def test_good_fit_has_three_strongest_bullets(self):
        parts = match.breakdown(row(), WHO, "financial services")["parts"]
        bullets = match.good_fit(row(), scored(), parts, NOW, WHO)
        self.assertEqual(len(bullets), match.MAX_BULLETS)
        self.assertTrue(bullets[0].startswith("You cover 9 of 10 listed skills"))
        self.assertEqual(bullets[1], "Your 4 years meet the 3+ the JD asks")

    def test_gaps_lead_with_missing_skills_then_years_then_penalties(self):
        r = row(min_years=6, fit_terms=terms(5, 10))
        parts = match.breakdown(r, WHO, "healthcare")["parts"]
        out = match.gaps(r, scored(mults={"Reposted ×2": 0.72}), parts)
        self.assertEqual(out[0], "Missing BigQuery, Gap1, Gap2 (+2 more)")
        self.assertEqual(out[1], "JD asks 6+ yrs; you have 4")
        self.assertEqual(len(out), match.MAX_GAPS)

    def test_boosts_are_not_gaps(self):
        parts = match.breakdown(row(fit_terms=terms(10, 10)), WHO, "financial services")["parts"]
        self.assertEqual(match.gaps(row(), scored(mults={"Your feedback: liked company": 1.1}), parts), [])


if __name__ == "__main__":
    unittest.main()
