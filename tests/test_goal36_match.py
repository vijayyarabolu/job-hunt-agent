"""GOAL36 §1 / §3 on a throwaway DB: jobs come in by the person's master resume, not by title. A title that is clearly
another field is denied (and counted per day and reason); any other title whose JD names the masters' skills is
scored against the person's resume and kept at 40 % or more, whatever it is called; a title family keeps a job as
before; the JD gate and Staff titles rank lower instead of hiding; the hard rules (not US, too many years, past
employer, citizens only, no OPT, C2C / 1099 only, no sponsorship ever) still hide. "New" can mean 1, 3 or 7 days. W2
contract roles through staffing firms are kept and tagged. No network."""
import unittest
from datetime import timedelta

from job_hunt_agent import api, config, db, filters, fits, fresh, profile_settings, ranking, resignal, resumematch, score, titles
from job_hunt_agent import workterms

from tests.test_goal8_flow import NOW, PID, Goal8Case, seed_job

PAD = "\nAbout us\n" + "We build software that people rely on every day, with care for quality and clear writing. " * 4
FIT_JD = ("Requirements:\n- Build ranking services in Python and PyTorch.\n- Deploy them on AWS with Kubernetes.\n"
          "- Write clear tests." + PAD)
LOW_JD = ("Requirements:\n- Java, Spring and Kafka services; Scala and Go.\n- React front ends, Terraform, Snowflake and "
          "Spark pipelines.\n- Python, AWS and Kubernetes a plus." + PAD)
FEW_SKILLS_JD = "Requirements:\n- Excel and PowerPoint reports for the finance team.\n- Python a plus." + PAD


class DenyListTest(unittest.TestCase):
    CASES = {"Registered Nurse": "healthcare", "Truck Driver CDL": "driver", "Sales Engineer": "sales",
             "Senior Accountant": "accounting", "Warehouse Associate": "warehouse", "Technical Recruiter": "recruiting",
             "Engineering Manager, ML": "management", "Director of AI": "management", "Machine Learning Intern": "intern",
             "Mechanical Engineer": "hardware", "Customer Support Specialist": "admin_support",
             "Healthcare Data Scientist": None, "Accounting Systems Engineer": None, "Applied Scientist": None,
             "Software Engineer, Search Relevance": None, "Analytics Engineer, LLM": None, "Staff ML Engineer": None,
             "Member of Technical Staff": None, "ML Engineer, Hardware Acceleration": None,
             # the copy's 14 days: a word that only starts like a denied one is not denied
             "Applied Scientist II, International and Emerging Stores Payments": None,
             "Data Scientist Senior - Internal Audit": None, "Applied AI Engineer, Internal Systems": None,
             "Salesforce Developer, Sr. System Development Engineer , Frontier AI Tools": None,
             "ML Engineer, Cooperative Robotics": None, "Summer Interns, Data Science": "intern",
             "Co-op, Machine Learning": "intern", "Inside Sales Representative": "sales"}

    def test_each_title(self):
        for title, reason in self.CASES.items():
            with self.subTest(title=title):
                self.assertEqual(titles.denied(title), reason)

    def test_what_is_read_at_fetch(self):
        self.assertTrue(titles.worth_reading("Software Engineer, Search Relevance"))
        self.assertTrue(titles.worth_reading("Staff ML Engineer"))
        self.assertFalse(titles.worth_reading("Registered Nurse"))
        self.assertFalse(titles.worth_reading("Financial Analyst"))            # not denied, but no tech word: list-only
        self.assertTrue(titles.senior_title("Staff ML Engineer"))
        self.assertFalse(titles.senior_title("Member of Technical Staff"))


class StoreTest(Goal8Case):
    def setUp(self):
        super().setUp()
        resumematch.forget()

    def families(self, uid: str) -> tuple[str, bool]:
        row = self.conn.execute("SELECT families, description IS NOT NULL FROM jobs WHERE uid = ?", (uid,)).fetchone()
        return row[0], bool(row[1])

    def test_a_non_family_title_with_the_masters_skills_keeps_its_jd(self):
        uid = seed_job(self.conn, "fit", title="Software Engineer, Search Relevance", description=FIT_JD)
        self.assertEqual(self.families(uid), (",resume,", True))

    def test_a_jd_with_too_few_master_skills_is_not_kept(self):
        uid = seed_job(self.conn, "few", title="Business Intelligence Developer", description=FEW_SKILLS_JD)
        self.assertEqual(self.families(uid), (",", False))

    def test_a_denied_title_is_counted_per_day_and_reason(self):
        seed_job(self.conn, "rn1", title="Registered Nurse", description=FIT_JD)
        seed_job(self.conn, "rn2", title="ICU Nurse", description=FIT_JD)
        seed_job(self.conn, "dr", title="Truck Driver CDL", description=FIT_JD)
        got = {r["reason"]: (r["n"], r["example"]) for r in self.conn.execute("SELECT * FROM title_denials")}
        self.assertEqual(got, {"healthcare": (2, "Registered Nurse"), "driver": (1, "Truck Driver CDL")})
        uid = self.conn.execute("SELECT uid FROM jobs WHERE title = 'ICU Nurse'").fetchone()[0]
        self.assertEqual(self.families(uid), (",", False))

    def test_resignal_gives_stored_jobs_the_mark(self):
        uid = seed_job(self.conn, "fit", title="Software Engineer, Search Relevance", description=FIT_JD)
        with db.tx(self.conn):
            self.conn.execute("UPDATE jobs SET families = ',' WHERE uid = ?", (uid,))
        resignal.run(self.conn)
        self.assertEqual(self.families(uid)[0], ",resume,")


class FeedTest(Goal8Case):
    """The three ordinary ML jobs of Goal8Case plus one job per rule."""

    def setUp(self):
        super().setUp()
        resumematch.forget()
        self.fit = seed_job(self.conn, "fit", title="Software Engineer, Search Relevance", description=FIT_JD)
        self.low = seed_job(self.conn, "low", title="Platform Engineer", description=LOW_JD)
        self.staff = seed_job(self.conn, "staff", title="Staff Software Engineer, Ranking", description=FIT_JD)
        self.ever = seed_job(self.conn, "ever", title="Backend Engineer, Ranking", description=FIT_JD +
                             "\nApplicants must be able to work without the need for current or future sponsorship.")
        self.fam_low = seed_job(self.conn, "famlow", title="Machine Learning Engineer, Payments", description=LOW_JD)
        self.refresh()

    def refresh(self):
        fits.refresh(self.conn, config.profile(PID), NOW)

    def ranked(self) -> dict:
        rows = ranking.rank(self.conn, config.profile(PID), NOW, states=api.FEED_STATES + ("non_us",))
        return {r["uid"]: s for r, s in rows}

    def feed(self) -> dict:
        return api.jobs(self.conn, PID, "recommended", NOW, filters.parse({}))

    def test_a_job_the_resume_fits_comes_in_whatever_its_title(self):
        s = self.ranked()[self.fit]
        self.assertEqual((s.state, s.vouched), ("ok", False))
        self.assertGreaterEqual(s.resume_pct, resumematch.MIN_PCT)
        self.assertIn(f"Resume match {s.resume_pct}%", s.chips)
        self.assertIn(self.fit, {c["uid"] for c in self.feed()["jobs"]})

    def test_under_40_percent_is_hidden_and_counted(self):
        s = self.ranked()[self.low]
        self.assertLess(s.resume_pct, resumematch.MIN_PCT)
        self.assertEqual(s.state, score.LOW_MATCH)
        out = self.feed()
        self.assertNotIn(self.low, {c["uid"] for c in out["jobs"]})
        self.assertEqual(out["low_match"], 1)

    def test_a_title_family_still_keeps_a_job_with_a_low_resume_match(self):
        s = self.ranked()[self.fam_low]
        self.assertEqual((s.state, s.vouched), ("ok", True))

    def test_a_staff_title_ranks_lower_instead_of_hiding(self):
        ranked = self.ranked()
        self.assertEqual(ranked[self.staff].state, "ok")
        self.assertIn(score.SENIOR_TITLE, ranked[self.staff].mults)
        self.assertLess(ranked[self.staff].worth, ranked[self.fit].worth)

    def test_no_sponsorship_ever_is_a_hard_rule(self):
        s = self.ranked()[self.ever]
        self.assertEqual((s.state, s.dealbreaker), ("filtered", "work terms: no sponsorship ever"))
        self.assertEqual(self.feed()["not_w2"], 1)

    def test_the_jd_gate_ranks_lower_instead_of_hiding(self):
        gated = seed_job(self.conn, "ds", title="Data Scientist, Ranking", description=FIT_JD)
        path = config.PROFILES / PID / "profile.yaml"
        path.write_text(path.read_text().replace("families: [ml_engineer]", "families: [ml_engineer, data_scientist]")
                        + "jd_gate: {families: [data_scientist], generic_titles: true, min_terms: 2}\n")
        self.refresh()
        s = self.ranked()[gated]
        self.assertEqual((s.state, s.vouched), ("ok", False))             # the resume fits, so it stays
        self.assertIn(score.LITTLE_GENAI, s.mults)

    def test_the_hard_rules_still_hide(self):
        far = seed_job(self.conn, "far", title="Software Engineer, Search", description=FIT_JD)
        old = seed_job(self.conn, "old", title="Software Engineer, Ranking API",
                       description=FIT_JD.replace("Requirements:\n", "Requirements:\n- 8+ years of experience.\n"))
        with db.tx(self.conn):
            self.conn.execute("UPDATE jobs SET is_us = 0 WHERE uid = ?", (far,))
        self.refresh()
        ranked = self.ranked()
        self.assertEqual(ranked[far].state, "non_us")
        self.assertEqual(ranked[old].state, "too_senior")


class NewWindowTest(Goal8Case):
    def test_one_three_or_seven_days(self):
        self.assertEqual(profile_settings.apply_get(PID)["new_within_days"], 1)
        self.assertEqual(fresh.window(config.profile(PID)), timedelta(days=1))
        profile_settings.set_apply(PID, {"new_days": 3})
        self.assertEqual(profile_settings.apply_get(PID)["new_within_days"], 3)
        self.assertEqual(fresh.window(config.profile(PID)), timedelta(days=3))
        with self.assertRaises(ValueError):
            profile_settings.set_apply(PID, {"new_days": 5})

    def test_a_three_day_old_job_is_new_only_with_the_longer_window(self):
        row = {"first_posted_at": db.iso(NOW - timedelta(days=2)), "precision": "exact", "first_seen": db.iso(NOW)}
        self.assertFalse(fresh.is_fresh(row, NOW))
        self.assertTrue(fresh.is_fresh(row, NOW, timedelta(days=3)))
        self.assertTrue(fresh.is_fresh(row, NOW, timedelta(days=7)))


class W2Test(Goal8Case):
    def test_a_w2_contract_through_a_staffing_firm_is_kept_and_tagged(self):
        uid = seed_job(self.conn, "tek", title="Machine Learning Engineer", description=FIT_JD + "\n6 month W2 contract.")
        with db.tx(self.conn):
            self.conn.execute("UPDATE jobs SET company = 'TEKsystems' WHERE uid = ?", (uid,))
        fits.refresh(self.conn, config.profile(PID), NOW)
        cards = {c["uid"]: c for c in api.jobs(self.conn, PID, "recommended", NOW, filters.parse({}))["jobs"]}
        self.assertEqual(cards[uid]["work"], {"w2": True, "contract": True, "vague": False, "via": None})

    def test_the_counts(self):
        rows = [{"employment_type": "full_time", "company": "Acme"}, {"employment_type": "contract", "company": "Acme"},
                {"employment_type": None, "company": "Insight Global"}, {"employment_type": None, "company": "Acme"},
                {"employment_type": None, "company": "Acme"}]
        got = resignal.w2_counts(rows, ["w2", "w2", "w2", None, "c2c_only"])
        self.assertEqual(got, {"w2_full_time": 1, "w2_contract": 2, "not_said": 1, "dropped": {"C2C only": 1}})

    def test_w2_only_is_no_longer_a_harvesting_signal(self):
        from job_hunt_agent import signals
        self.assertEqual(signals.staffing_signals("W2 only. Per hour on W2."), 0)
        self.assertEqual(workterms.read("This is a W2 only contract."), ("w2",))


if __name__ == "__main__":
    unittest.main()
