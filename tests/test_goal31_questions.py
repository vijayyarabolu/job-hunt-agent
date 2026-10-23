"""GOAL31 §2: on 1.5.0 Workday dropdowns were read while the last question's list was still on screen, so Needs you
holds EEO questions with another question's choices ("What is your gender?" offering races, "Veteran Status"
offering Female / Male). A chip there would save a race as the user's gender. Such a question is closed (asked again, it
comes back with its own choices) and never recorded again; Self Identify's Language dropdown is never an EEO question."""
import json

from job_hunt_agent.autofill import keys, questions
from tests.test_goal23_autofill import PID, AutofillCase

RACES = ["Select One", "American Indian or Alaska Native (Not Hispanic or Latino) (United States of America)",
         "Asian (Not Hispanic or Latino) (United States of America)", "White (Not Hispanic or Latino) (United States of America)"]
GENDERS = ["Decline to State", "Female", "Male"]


class ForeignChoicesTest(AutofillCase):
    def ask(self, text, key, options):
        questions.record(self.conn, PID, [{"label": text, "kind": "select", "options": options, "key": key}], "workday", "NVIDIA")

    def put(self, text, key, options):                   # an old row, as 1.5.0 left it
        self.conn.execute("INSERT INTO autofill_questions(profile, qnorm, key, text, kind, options, boards, companies, first_at, last_at) "
                          "VALUES (?,?,?,?,?,?,?,?,?,?)", (PID, keys.norm(text, "NVIDIA"), key, text, "select", json.dumps(options), '["workday"]',
                                                         '["NVIDIA"]', "2026-10-20T22:08:06Z", "2026-10-20T22:08:06Z"))
        self.conn.commit()

    def texts(self):
        return [q["text"] for q in questions.open_list(self.conn, PID)]

    def test_old_questions_with_another_questions_choices_are_closed(self):
        self.put("What is your gender?", "gender", RACES)
        self.put("Do you identify as one of the following protected veterans?", "veteran", GENDERS)
        self.put("Language", "disability", ["English", "Spanish"])
        self.put("Please select your race", "race", RACES)                     # its own choices: stays
        self.put("Are you an armed services veteran or veteran with disability?", "disability",
                 ["Veteran", "Veteran with a Disability", "None of the above"])  # its own choices: stays
        self.assertEqual(questions.tidy(self.conn, PID), 3)
        self.assertEqual(sorted(self.texts()), ["Are you an armed services veteran or veteran with disability?", "Please select your race"])

    def test_they_are_not_recorded_again(self):
        self.ask("What is your gender?", "gender", RACES)
        self.ask("Language", "disability", ["English", "Spanish"])
        self.assertEqual(self.texts(), [])

    def test_asked_again_with_its_own_choices_it_comes_back(self):
        self.put("What is your gender?", "gender", RACES)
        questions.tidy(self.conn, PID)
        self.ask("What is your gender?", "gender", GENDERS)
        got = questions.open_list(self.conn, PID)
        self.assertEqual([(q["text"], q["options"]) for q in got], [("What is your gender?", GENDERS)])
