"""GOAL27 (job-hunt-agent's side of the rebuilt Autofill): the structured profile built once before a page is touched; the
extension's classifier names a field's key and job-hunt-agent keeps its guards (someone else's email, a legal question);
short custom questions answered from the resume (never a legal / EEO one, a choice only in its own words); the cover
letter on request (text and a PDF named like the resume); the match % and missing keywords of any job page; Save job
from any page; one log row per field (never a value) and the Health page's per-field view."""
import json
import re
import shutil
import subprocess
from unittest import mock

from job_hunt_agent import db
from job_hunt_agent.autofill import api, assist, bank, health, pdftext

from tests.test_goal23_autofill import AutofillCase
from tests.test_goal25_health import NOW
from tests.test_goal8_flow import PID


class ProfileFirstTest(AutofillCase):
    def test_one_structured_profile_with_every_key_resolved(self):
        bank.save(self.conn, PID, "gender", "Male")
        got = api.get(self.conn, PID, "/api/autofill/profile", {"uid": self.uids[0]})
        self.assertEqual((got["contact"]["first_name"], got["contact"]["email"]), ("Tess", "tess@example.com"))
        self.assertEqual(got["links"]["linkedin"], "https://linkedin.com/in/tess")
        self.assertEqual(got["eeo"], {"gender": "Male"})                       # only what the user saved
        self.assertEqual(got["answers"]["race"]["status"], "needs")
        self.assertIn("race", got["missing"])
        self.assertNotIn("middle_name", got["missing"])                        # may stay empty: not missing
        self.assertTrue(got["jobs"] and got["schools"])
        self.assertTrue(all(not j["description"] or j["description"].startswith("• ") for j in got["jobs"]))
        self.assertEqual(got["counts"]["jobs"], len(got["jobs"]))

    def test_the_extensions_key_is_used_with_the_old_guards(self):
        fields = [{"id": "a", "label": "Your sex", "key": "gender", "kind": "select"},
                  {"id": "b", "label": "Referrer's email", "key": "email", "kind": "text"},
                  {"id": "c", "label": "In which country are you authorized to work?", "key": "country", "kind": "text"},
                  {"id": "d", "label": "Pick one", "key": "sponsor_auto", "kind": "text"}]
        bank.save(self.conn, PID, "gender", "Male")
        got = {a["id"]: a for a in bank.resolve(self.conn, PID, fields, {})}
        self.assertEqual((got["a"]["key"], got["a"]["value"]), ("gender", "Male"))
        self.assertNotEqual(got["b"]["key"], "email")                          # someone else's details
        self.assertNotEqual(got["c"]["key"], "country")                        # legal wording: a legal key only
        self.assertIsNone(got["d"]["key"])                                     # a setting is never a page field


class SuggestTest(AutofillCase):
    def test_short_questions_from_the_resume_never_legal_and_choices_in_their_own_words(self):
        seen = {}

        def runner(prompt, pid):
            listed = json.loads(re.search(r"Questions:\n(.*)\n\nJob:", prompt, re.S).group(1))
            seen["ids"] = [q["id"] for q in listed]
            return json.dumps([{"id": "years", "answer": "4"}, {"id": "k8s", "answer": "yes"}, {"id": "lang", "answer": "Rust"}])
        qs = [{"id": "years", "label": "How many years of Python experience do you have?", "kind": "text"},
              {"id": "k8s", "label": "Have you used Kubernetes?", "kind": "select", "options": ["Yes", "No"]},
              {"id": "lang", "label": "Favorite language", "kind": "select", "options": ["Python", "Go"]},
              {"id": "auth", "label": "Are you legally authorized to work in the US?", "kind": "select", "options": ["Yes", "No"]},
              {"id": "eeo", "label": "Gender", "kind": "select", "options": ["Male", "Female"]},
              {"id": "why", "label": "Why do you want to work here?", "kind": "textarea"}]
        assist._CACHE.clear()
        with mock.patch.object(assist, "_run", lambda prompt, pid, model, r=None: runner(prompt, pid)):
            got = {a["id"]: a for a in assist.suggest(self.conn, PID, {"uid": self.uids[0], "questions": qs})["answers"]}
        self.assertEqual(seen["ids"], ["years", "k8s", "lang"])                # legal, EEO and long ones never sent
        self.assertEqual((got["years"]["answer"], got["years"]["status"]), ("4", "review"))
        self.assertEqual(got["k8s"]["answer"], "Yes")                          # the form's own words
        self.assertEqual((got["lang"]["answer"], got["lang"]["status"]), ("", "none"))   # not one of its choices
        self.assertEqual(got["auth"]["answer"], "")
        with mock.patch.object(assist, "_run", side_effect=AssertionError("asked twice")):
            again = {a["id"]: a for a in assist.suggest(self.conn, PID, {"uid": self.uids[0], "questions": qs[:1]})["answers"]}
        self.assertEqual(again["years"]["answer"], "4")                         # a refilled page costs no second call

    def test_a_page_without_its_job_is_refused(self):
        with self.assertRaises(ValueError):
            assist.suggest(self.conn, PID, {"questions": [{"id": "x", "label": "Years?"}]})


class CoverLetterTest(AutofillCase):
    def test_on_request_text_kept_with_the_job_and_a_pdf_named_like_the_resume(self):
        letter = "Dear Hiring Team,\n\nI build ML systems — in Python.\n\nSincerely,\nTess Tester"
        got = assist.cover(self.conn, PID, {"uid": self.uids[0]}, runner=lambda prompt, pid: letter)
        self.assertNotIn("—", got["text"])
        self.assertTrue(got["file_name"].endswith(".pdf") and "Cover_Letter" in got["file_name"], got["file_name"])
        pdf = api.get(self.conn, PID, "/api/autofill/cover.pdf", {"uid": self.uids[0]})
        self.assertTrue(pdf.data.startswith(b"%PDF-1.4"))
        self.assertEqual(pdf.name, got["file_name"])
        with self.assertRaises(KeyError):
            api.get(self.conn, PID, "/api/autofill/cover.pdf", {"uid": self.uids[1]})

    def test_the_pdf_holds_the_text(self):
        data = pdftext.render("Dear team,\n\nI build things • in Python (and more).\n" + "word " * 700)
        out = subprocess.run(["pdftotext", "-", "-"], input=data, capture_output=True) if shutil.which("pdftotext") else None
        if out is not None:
            text = out.stdout.decode()
            self.assertIn("I build things • in Python (and more).", text)
            self.assertGreaterEqual(text.count("word"), 700)
        self.assertIn(b"/Count 2", data)                                      # a long letter: two pages


class ScoreAndSaveTest(AutofillCase):
    JD = ("We are hiring an ML Engineer. Requirements: Python, PyTorch, Kubernetes, AWS, Spark and SQL. You will build "
          "training pipelines, deploy models with Docker and Kubernetes, and run A/B tests. Experience with Airflow is a plus. "
          "Strong communication skills.")

    def test_any_page_gets_a_match_and_its_missing_keywords(self):
        got = assist.score(self.conn, PID, {"text": self.JD})
        self.assertTrue(0 <= got["pct"] <= 100)
        self.assertEqual(got["resume"], "ML Engineer")
        self.assertIn("Kubernetes", got["matched"] + got["missing"])
        with self.assertRaises(ValueError):
            assist.score(self.conn, PID, {"text": "Apply now."})

    def test_save_job_from_any_page_once(self):
        args = {"url": "https://careers.example.com/jobs/42", "title": "ML Engineer", "company": "Example Co", "text": self.JD}
        got = assist.save_job(self.conn, PID, args)
        self.assertFalse(got["already"])
        row = self.conn.execute("SELECT status, company, role_title, jd_text FROM applications WHERE id = ?", (got["app"],)).fetchone()
        self.assertEqual((row["status"], row["company"], row["role_title"]), ("saved", "Example Co", "ML Engineer"))
        self.assertIn("Kubernetes", row["jd_text"])
        self.assertTrue(assist.save_job(self.conn, PID, args)["already"])
        with self.assertRaises(ValueError):
            assist.save_job(self.conn, PID, {"url": "about:blank"})


class FieldLogTest(AutofillCase):
    def test_one_row_per_field_without_values_and_the_health_view(self):
        fields = [{"label": "Email", "kind": "text", "key": "email", "status": "filled"},
                  {"label": "Gender", "kind": "select", "key": "gender", "status": "needs", "why": "no saved answer"},
                  {"label": "Phone", "kind": "text", "key": "phone", "status": "failed", "why": "the page did not keep the text"},
                  {"label": "x", "status": "bogus"}]
        api.post(self.conn, PID, "/api/autofill/report", {"board": "lever", "step": "apply", "filled": 1, "fields": fields,
                                                           "version": "1.3.0", "needs": []}, NOW)
        rows = [dict(r) for r in self.conn.execute("SELECT label, key, status, why, version FROM autofill_fields")]
        self.assertEqual([r["status"] for r in rows], ["filled", "needs", "failed"])
        self.assertEqual(rows[0]["version"], "1.3.0")
        view = health.report(self.conn, PID, NOW)["fields"]
        self.assertEqual(view["boards"][0], {"board": "lever", "filled": 1, "review": 0, "needs": 1, "failed": 1, "kept": 0, "skipped": 0})
        self.assertEqual(view["worst"][0]["label"], "Phone")                  # a failure first, with why
        self.assertEqual(view["worst"][0]["why"], "the page did not keep the text")

    def test_old_rows_go(self):
        api.post(self.conn, PID, "/api/autofill/report", {"board": "lever", "fields": [{"label": "Email", "status": "filled"}],
                                                           "needs": []}, NOW.replace(month=8))
        api.post(self.conn, PID, "/api/autofill/report", {"board": "lever", "fields": [{"label": "Email", "status": "filled"}],
                                                           "needs": []}, NOW)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM autofill_fields").fetchone()[0], 1)


class SchemaTest(AutofillCase):
    def test_schema_32_has_the_field_log(self):
        self.assertGreaterEqual(db.SCHEMA_VERSION, 32)
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(autofill_fields)")}
        self.assertEqual(cols, {"id", "profile", "board", "step", "label", "kind", "key", "status", "why", "version", "at"})
