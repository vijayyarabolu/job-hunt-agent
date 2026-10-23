"""GOAL29 §5, job-hunt-agent's side of Generate: what goes to resumegen's answer queue (the question, the field's limit, the
JD, company and role, the tailored resume's resumegen id, the variant), the cache lookup, and Save to Answers (kept
as a long answer for the job, offered again). resumegen itself is mocked here; its side is in resumegen's tests."""
import unittest
from unittest import mock

from job_hunt_agent.autofill import api, generate, learn

from tests.test_goal23_autofill import AutofillCase
from tests.test_goal8_flow import PID

QUESTION = "Why do you want to work at Acme? (required)"


class GenerateTest(AutofillCase):
    def call(self, reply):
        seen = []

        def fake(method, path, body=None, **_):
            seen.append((method, path, body))
            return reply
        return seen, mock.patch.object(generate.rgclient, "request", side_effect=fake)

    def test_generate_sends_the_question_limit_jd_and_job_and_returns_the_queued_answer(self):
        seen, patch = self.call({"answer": {"id": 5, "state": "writing", "label": "Writing with Claude…"}})
        with patch, mock.patch.object(generate.resume, "choose", return_value={"rg_id": 41, "kind": "approved"}):
            out = api.post(self.conn, PID, "/api/autofill/answer/generate",
                           {"uid": self.uids[0], "question": "  Why   Acme? ", "max_chars": "1500"}, None)
        self.assertEqual(out["answer"]["label"], "Writing with Claude…")
        method, path, body = seen[0]
        self.assertEqual((method, path), ("POST", "/api/answers"))
        self.assertEqual((body["profile"], body["job_key"], body["question"], body["max_chars"], body["rg_job_id"]),
                         (PID, self.uids[0], "Why Acme?", 1500, 41))
        self.assertEqual((body["company"], body["role"], body["variant"]), ("Acme", "Machine Learning Engineer", ""))
        self.assertIn("Kubernetes", body["jd_text"])

    def test_a_tracker_row_is_keyed_by_its_id_and_a_bad_limit_is_dropped(self):
        with mock.patch.object(generate.draft, "_jd", return_value=("Initech", "Data Engineer", "Python. " * 40)):
            seen, patch = self.call({"answer": {"id": 6}})
            with patch, mock.patch.object(generate.resume, "choose", return_value={"rg_id": None}):
                generate.generate(self.conn, PID, {"app": "12", "question": QUESTION, "max_chars": "lots",
                                                   "variant": "shorter", "previous": "An older answer."})
        body = seen[0][2]
        self.assertEqual((body["job_key"], body["max_chars"], body["company"], body["variant"], body["previous"]),
                         ("app:12", None, "Initech", "shorter", "An older answer."))

    def test_nothing_is_sent_without_a_job_a_question_a_known_variant_or_a_jd(self):
        seen, patch = self.call({})
        with patch:
            for args, why in (({"question": QUESTION}, "not matched"), ({"uid": self.uids[0]}, "no question"),
                              ({"uid": self.uids[0], "question": QUESTION, "variant": "longer"}, "variant")):
                with self.assertRaisesRegex(ValueError, why):
                    generate.generate(self.conn, PID, args)
            with mock.patch.object(generate.draft, "_jd", return_value=("Acme", "ML", "short")):
                with self.assertRaisesRegex(ValueError, "no description"):
                    generate.generate(self.conn, PID, {"uid": self.uids[0], "question": QUESTION})
        self.assertEqual(seen, [])

    def test_status_and_lookup_ask_resumegen_and_never_generate(self):
        seen, patch = self.call({"answer": {"id": 9, "state": "ready"}, "answers": {"Why Acme?": {"id": 9}}})
        with patch:
            self.assertEqual(api.get(self.conn, PID, "/api/autofill/answer/status", {"id": "9"})["answer"]["id"], 9)
            got = api.post(self.conn, PID, "/api/autofill/answer/lookup",
                           {"uid": self.uids[0], "questions": ["Why Acme?", "  ", "Tell us about a project"]}, None)
            self.assertEqual(generate.lookup(PID, {"uid": self.uids[0], "questions": []}), {"answers": {}, "filled": {}})   # GOAL30 §2: and whose job filled ones were for
            with self.assertRaisesRegex(ValueError, "id"):
                generate.status({"id": "x"})
        self.assertEqual(got["answers"], {"Why Acme?": {"id": 9}})
        self.assertEqual([(m, p) for m, p, _ in seen], [("GET", "/api/answers/9"), ("POST", "/api/answers/lookup")])
        self.assertEqual(seen[1][2]["questions"], ["Why Acme?", "Tell us about a project"])

    def test_save_to_answers_keeps_it_with_the_job_and_offers_it_again(self):
        args = {"uid": self.uids[0], "question": QUESTION, "text": "I build ML systems in Python and want to at Acme."}
        out = api.post(self.conn, PID, "/api/autofill/answer/save", args, None)
        self.assertEqual((out["scope"], out["company"]), ("snippet", "Acme"))
        row = dict(self.conn.execute("SELECT * FROM autofill_snippets").fetchone())
        self.assertEqual((row["job_uid"], row["company"], row["text"]), (self.uids[0], "Acme", args["text"]))
        self.assertEqual(self.conn.execute("SELECT how FROM autofill_learned").fetchone()[0], "generated")
        self.assertEqual(generate.save(self.conn, PID, args), {"saved": "unchanged"})
        self.assertEqual(learn.snippets(self.conn, PID, "Why do you want to work at Initech?")[0]["text"], args["text"])
        with self.assertRaisesRegex(ValueError, "no answer"):
            generate.save(self.conn, PID, {**args, "text": " "})


if __name__ == "__main__":
    unittest.main()
