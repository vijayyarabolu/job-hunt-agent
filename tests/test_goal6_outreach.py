"""Draft outreach per contact (a ready-to-paste Claude prompt + a plain template that works without Claude),
sent marks with dates (the sheet's Email Sent / LinkedIn Request cells), and follow-ups +3/+6 days after the outreach.
Nothing is ever sent by the app."""
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from job_hunt_agent import contacts, outreach, server, trackapi
from tests.test_tracker_ui import TrackerCase

JD = ("About the role\n\nJoin our Machine Learning team.\n\nRequirements:\n"
      "- 3+ years building machine learning systems in Python and PyTorch\n"
      "- Experience deploying models with Kubernetes and AWS\n- Strong SQL and data pipelines\n\n"
      "Nice to have:\n- LLM fine-tuning\n")
APPLIED = date(2026, 10, 1)


class OutreachTest(TrackerCase):
    def setUp(self):
        super().setUp()
        row = self.add(company="Acme", role_title="ML Engineer", jd=JD, status="applied", date_applied="10/01/2026")
        contacts.save(self.conn, row["id"], [{"name": "Jane Doe", "role": "Head of Machine Learning",
                                              "email": "jane.doe@acme.ai", "source": "jd"}])
        self.row = trackapi.get(self.conn, "alex", row["id"], APPLIED)
        self.contact = self.row["contacts"][0]

    def test_prompt_has_the_contact_job_requirements_resume_and_the_ask(self):
        out = outreach.draft(self.conn, "alex", self.row["id"], self.contact["id"])
        prompt = out["prompt"]
        for text in ("Jane Doe", "Head of Machine Learning", "Acme", "ML Engineer", "hiring manager", "under 120 words",
                     "subject line", "under 300 characters", "no flattery", "Machine Learning team"):
            self.assertIn(text, prompt)
        self.assertTrue(out["requirements"])
        self.assertTrue(any("PyTorch" in r for r in out["requirements"]))
        self.assertIn(out["resume"]["label"], prompt)
        self.assertTrue(2 <= len(out["achievements"]) <= 3)
        self.assertNotIn("phone", prompt.lower())

    def test_plain_template_fits_the_limits(self):
        template = outreach.draft(self.conn, "alex", self.row["id"], self.contact["id"])["template"]
        self.assertTrue(template["subject"])
        self.assertLess(len(template["email"].split()), 120)
        self.assertLessEqual(len(template["linkedin"]), 300)
        self.assertIn("Hi Jane", template["email"])
        self.assertIn("ML Engineer", template["linkedin"])

    def test_achievements_are_result_bullets_not_skill_lists(self):
        lines = ["MLOps & Cloud: AWS (SageMaker, Lambda), Docker, Kubernetes, MLflow, Datadog, CI/CD, Monitoring",
                 "Languages & Data: Python, SQL, FastAPI, Flask, Airflow, PySpark",
                 "Built PyTorch training pipelines on Kubernetes and AWS that cut model training time by 40%.",
                 "Deployed Python services with SQL feature stores serving 2M predictions a day.",
                 "Mentored three engineers on code review practices across the platform team."]
        wins = outreach.achievements(lines, JD)
        self.assertEqual(wins[:2], [lines[2], lines[3]])
        self.assertFalse(any(w.startswith(("MLOps", "Languages")) for w in wins))

    def test_an_unknown_contact_is_refused(self):
        with self.assertRaises(KeyError):
            outreach.draft(self.conn, "alex", self.row["id"], 999999)

    def test_follow_ups_count_from_the_outreach_when_it_was_sent(self):
        contacts.toggle(self.conn, self.contact["id"], "email_sent", True, today=date(2026, 10, 5))
        row = trackapi.get(self.conn, "alex", self.row["id"], date(2026, 10, 6))
        self.assertEqual(row["email_sent"], "10/05/2026")                   # the sheet's Email Sent cell
        self.assertEqual((row["fu1"]["due"], row["fu2"]["due"]), ("2026-10-08", "2026-10-11"))
        self.assertEqual(row["fu1"]["after"], "outreach 10/05/2026")

    def test_follow_ups_without_outreach_count_from_the_applied_date(self):
        row = trackapi.get(self.conn, "alex", self.row["id"], date(2026, 10, 2))
        self.assertEqual((row["fu1"]["due"], row["fu1"]["after"]), ("2026-10-04", "applied"))

    def test_route_and_nothing_is_sent(self):
        out = server.route(self.conn, "GET", "/api/outreach", {"profile": "alex", "app": str(self.row["id"]),
                           "contact": str(self.contact["id"])}, datetime.now(timezone.utc))
        self.assertIn("prompt", out)
        source = Path(outreach.__file__).read_text()
        for word in ("smtplib", "urlopen", "sendmail", "net.request"):
            self.assertNotIn(word, source)


class BlankNameTest(unittest.TestCase):
    def test_a_contact_whose_name_is_only_spaces_gets_hi_there(self):
        out = outreach._template({"name": "Jane Doe"}, {"name": "   "}, {"company": "Acme", "role_title": "MLE"},
                                 None, [], [])
        self.assertIn("Hi there", out["email"])
