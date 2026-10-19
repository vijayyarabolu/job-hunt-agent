"""GOAL25 §1/§3 (user 2026-10-14 17:06): Autofill learns what the person fills by hand. An ordinary answer is kept
for that question (and the same question asked in other words) on every board; sponsorship / work authorization /
legal / EEO only for that company; long free text only as a snippet with the application; secrets never. Undo puts
back what was there. The Answers page gets Learned, company overrides, snippets, fill stats, typo-proof choices and
the education list (more than one degree, start dates)."""
import json
from unittest import mock

from job_hunt_agent import db
from job_hunt_agent.autofill import api, bank, choices, fillstats, learn, sponsor

from tests.test_goal23_autofill import AutofillCase
from tests.test_goal8_flow import PID


class LearnCase(AutofillCase):
    def teach(self, *items, board="greenhouse", company="Acme Robotics", step="", uid="J1", hand=None):
        args = {"items": list(items), "board": board, "company": company, "step": step, "uid": uid}
        if hand is not None:
            args["hand"] = hand
        return api.post(self.conn, PID, "/api/autofill/learn", args, None)["learned"]

    def item(self, label, value, kind="text", options=(), how="hand", hints=()):
        return {"label": label, "value": value, "kind": kind, "options": list(options), "how": how, "hints": list(hints)}

    def one(self, field, **ctx):
        return list(self.resolve(field, **ctx).values())[0]

    def answer(self, key):
        return bank.load(self.conn, PID).get(key, {}).get("answer")


class LearnGlobalTest(LearnCase):
    def test_a_hand_answer_fills_the_same_question_on_another_board(self):
        got = self.teach(self.item("Are you comfortable working in a fast-paced startup environment?", "Yes",
                                   kind="radio", options=["Yes", "No"]))
        self.assertEqual([(g["scope"], g["answer"]) for g in got], [("global", "Yes")])
        later = self.one(self.field("Are you comfortable working in a fast paced startup environment",
                                    kind="select", options=["Yes", "No"]), board="lever", company="Other Co")
        self.assertEqual((later["value"], later["status"]), ("Yes", "fill"))

    def test_a_built_in_answer_is_updated_and_undo_puts_the_old_one_back(self):
        bank.save(self.conn, PID, "notice", "1 month")
        got = self.teach(self.item("What is your notice period?", "2 weeks"))
        self.assertEqual((got[0]["label"], got[0]["answer"]), ("Notice period", "2 weeks"))
        self.assertEqual(self.answer("notice"), "2 weeks")
        api.post(self.conn, PID, "/api/autofill/learn/undo", {"id": got[0]["id"]}, None)
        self.assertEqual(self.answer("notice"), "1 month")
        self.assertEqual(learn.learned_list(self.conn, PID), [])

    def test_undo_of_a_new_question_removes_it(self):
        got = self.teach(self.item("Preferred team size", "5-10 people"))
        self.assertEqual(self.answer("q:preferred team size"), "5-10 people")
        learn.undo(self.conn, PID, got[0]["id"])
        self.assertIsNone(self.answer("q:preferred team size"))

    def test_the_same_answer_again_is_not_a_new_lesson(self):
        bank.save(self.conn, PID, "notice", "2 weeks")
        self.assertEqual(self.teach(self.item("Notice period", "2 weeks")), [])

    def test_the_board_wording_of_a_known_answer_is_kept_without_a_note(self):
        bank.save(self.conn, PID, "relocate", "Yes")
        got = self.teach(self.item("Are you willing to relocate?", "Yes, I am willing to relocate", kind="select",
                                   options=["Yes, I am willing to relocate", "No"]), board="workday")
        self.assertEqual(got, [])
        self.assertEqual(bank.load(self.conn, PID)["relocate"]["options"]["workday"], "Yes, I am willing to relocate")

    def test_a_learned_question_closes_its_needs_you(self):
        api.post(self.conn, PID, "/api/autofill/report",
                 {"board": "lever", "company": "X", "needs": [{"label": "Preferred team size", "kind": "text"}]},
                 db.utcnow())
        self.teach(self.item("Preferred team size", "Small"))
        self.assertEqual(api.get(self.conn, PID, "/api/autofill/count", {})["open"], 0)

    def test_opposite_questions_are_never_the_same_question(self):
        for a, b in (("are you currently employed", "are you currently unemployed"),
                     ("are you available to work weekdays", "are you available to work weekends"),
                     ("are you willing to relocate", "are you unwilling to relocate"),
                     ("can you work night shifts", "can you not work night shifts")):
            with self.subTest(a=a):
                self.assertIsNone(learn.similar(b, {a}))
        self.assertEqual(learn.similar("what is your notice period", {"notice period"}), "notice period")

    def test_unemployed_never_overwrites_employed(self):
        self.teach(self.item("Are you currently employed?", "No", kind="radio", options=["Yes", "No"]))
        self.teach(self.item("Are you currently unemployed?", "Yes", kind="radio", options=["Yes", "No"]))
        employed = self.one(self.field("Are you currently employed?", kind="radio", options=["Yes", "No"]), board="lever")
        self.assertEqual(employed["value"], "No")

    def test_a_similar_own_question_never_takes_over_a_built_in_answer(self):
        self.teach(self.item("Do you have a referral code for our first name field?", "ZZ", kind="text"))
        got = self.one(self.field("First name"), board="lever")
        self.assertNotEqual(got["value"], "ZZ")

    def test_hand_counts_are_kept_per_step(self):
        self.teach(self.item("Preferred team size", "Small"), hand=3, step="Questions")
        self.assertEqual(self.conn.execute("SELECT hand FROM autofill_hand").fetchone()["hand"], 3)


class LearnLimitsTest(LearnCase):
    def test_secrets_are_never_kept(self):
        got = self.teach(self.item("Social Security Number", "123-45-6789"), self.item("Bank routing number", "011000015"),
                         self.item("Password", "hunter22"), self.item("Date of birth", "01/02/1990"),
                         self.item("Driver's License Number", "K1234"), self.item("Your answer", "x", hints=["ssn"]))
        self.assertEqual(got, [])
        stored = json.dumps([dict(r) for r in self.conn.execute("SELECT * FROM autofill_answers")])
        for secret in ("123-45-6789", "011000015", "hunter22", "01/02/1990", "K1234"):
            self.assertNotIn(secret, stored)

    def test_search_and_filter_boxes_are_not_learned(self):
        got = self.teach(self.item("Search jobs", "machine learning"), self.item("Filter by location", "Remote"),
                         self.item("Keywords", "pytorch"), self.item("Find", "x", kind="search"))
        self.assertEqual(got, [])

    def test_address_job_and_account_answers_are_not_learned(self):
        got = self.teach(self.item("Street address", "1 Other St"), self.item("City", "Austin"),
                         self.item("How did you hear about us?", "LinkedIn"),
                         self.item("Have you previously worked for this company?", "Yes"))
        self.assertEqual(got, [])
        self.assertEqual(self.teach(self.item("Preferred team size", "Small"), step="auth"), [])
        self.assertEqual(self.teach(self.item("Preferred team size", "Small"), step="Experience"), [])

    def test_years_with_one_skill_never_change_the_overall_years(self):
        bank.save(self.conn, PID, "years_exp", "4")
        got = self.teach(self.item("How many years of experience do you have with LangChain?", "2"))
        self.assertEqual((got[0]["scope"], self.answer("years_exp")), ("global", "4"))
        same = self.one(self.field("How many years of experience do you have with LangChain?"), company="Other")
        self.assertEqual(same["value"], "2")
        self.assertEqual(self.one(self.field("Years of experience"), company="Other")["value"], "4")

    def test_a_start_date_typed_as_a_date_keeps_the_saved_wait(self):
        bank.save(self.conn, PID, "start_date", "2 weeks after offer acceptance")
        self.assertEqual(self.teach(self.item("Earliest start date", "11/02/2026")), [])
        self.assertEqual(self.answer("start_date"), "2 weeks after offer acceptance")


    def test_a_preferred_question_is_not_the_referral_question(self):
        from job_hunt_agent.autofill import keys
        self.assertIsNone(keys.classify(keys.norm("Preferred team size")))       # GOAL23 read "preferred" as "referred"
        self.assertEqual(keys.classify(keys.norm("Were you referred by an employee?")).key, "referred")


class CompanyOnlyTest(LearnCase):
    def test_sponsorship_changed_by_hand_is_that_company_only(self):
        bank.save(self.conn, PID, "sponsor_future", "Yes")
        got = self.teach(self.item("Will you now or in the future require sponsorship?", "No", kind="radio",
                                   options=["Yes", "No"], how="override"), company="Acme Robotics")
        self.assertEqual((got[0]["scope"], got[0]["company"]), ("company", "Acme Robotics"))
        self.assertEqual(self.answer("sponsor_future"), "Yes")          # the global answer stays
        q = self.field("Will you now or in the future require visa sponsorship?", kind="radio", options=["Yes", "No"])
        self.assertEqual(self.one(q, company="Acme Robotics Inc.")["value"], "No")
        self.assertEqual(self.one(q, company="Other Co")["value"], "Yes")
        listed = learn.overrides_list(self.conn, PID)
        self.assertEqual([(o["company"], o["key"], o["answer"]) for o in listed],
                         [("Acme Robotics", "sponsor_future", "No")])

    def test_a_company_answer_equal_to_the_usual_one_is_not_an_override(self):
        bank.save(self.conn, PID, "sponsor_future", "No")
        got = self.teach(self.item("Will you now or in the future require sponsorship?", "No", kind="radio",
                                   options=["Yes", "No"], how="hand"), company="Shield AI")
        self.assertEqual(got, [])
        self.assertEqual(learn.overrides_list(self.conn, PID), [])

    def test_no_instead_of_the_automatic_yes_at_a_sponsoring_company_is_kept_for_it(self):
        bank.save(self.conn, PID, "sponsor_future", "No")                    # GOAL24 fills Yes at a sponsor
        with mock.patch.object(sponsor, "badge_for", lambda *args: {"kind": "sponsors"}):
            got = self.teach(self.item("Will you now or in the future require sponsorship?", "No", kind="radio",
                                       options=["Yes", "No"], how="override"), company="Acme Robotics")
        self.assertEqual([g["scope"] for g in got], ["company"])
        self.assertEqual([(o["company"], o["answer"]) for o in learn.overrides_list(self.conn, PID)], [("Acme Robotics", "No")])

    def test_eeo_work_auth_and_certify_questions_are_company_only(self):
        got = self.teach(self.item("Gender", "Male", kind="select", options=["Male", "Female"]),
                         self.item("Are you legally authorized to work in the United States?", "Yes"),
                         self.item("I certify that the information provided is accurate", "Yes", kind="checkbox"),
                         company="Acme Robotics")
        self.assertEqual([g["scope"] for g in got], ["company"] * 3)
        self.assertEqual((self.answer("gender"), self.answer("work_auth")), ("", ""))
        self.assertEqual(self.resolve(self.field("Gender", kind="select", options=["Male", "Female"]),
                                      company="Acme Robotics")["Gender"]["value"], "Male")
        self.assertEqual(self.resolve(self.field("Gender", kind="select", options=["Male", "Female"]),
                                      company="Other")["Gender"]["status"], "needs")

    def test_a_company_answer_can_be_undone_edited_and_removed(self):
        got = self.teach(self.item("Gender", "Male"), company="Acme Robotics")
        learn.undo(self.conn, PID, got[0]["id"])
        self.assertEqual(learn.overrides_list(self.conn, PID), [])
        self.teach(self.item("Gender", "Male"), company="Acme Robotics")
        row = learn.overrides_list(self.conn, PID)[0]
        api.post(self.conn, PID, "/api/autofill/override", {"company": row["company_key"], "key": "gender",
                                                             "answer": "Female"}, None)
        self.assertEqual(learn.overrides_list(self.conn, PID)[0]["answer"], "Female")
        api.post(self.conn, PID, "/api/autofill/override", {"company": row["company_key"], "key": "gender", "answer": ""},
                 None)
        self.assertEqual(learn.overrides_list(self.conn, PID), [])

    def test_without_a_company_a_legal_answer_is_not_learned(self):
        self.assertEqual(self.teach(self.item("Gender", "Male"), company=""), [])


class SnippetTest(LearnCase):
    LONG = "I have built retrieval systems for three years. " * 8

    def test_long_text_is_a_snippet_never_filled_by_itself(self):
        got = self.teach(self.item("Why do you want to work at Acme Robotics?", self.LONG.strip(), kind="textarea"))
        self.assertEqual(got[0]["scope"], "snippet")
        self.assertFalse(any(k.startswith("q:why") for k in bank.load(self.conn, PID)))
        later = self.one(self.field("Why do you want to work at Initech?", kind="textarea"), company="Initech")
        self.assertEqual((later["status"], later["value"]), ("needs", ""))   # offered in the panel (Needs you), never filled
        offered = api.get(self.conn, PID, "/api/autofill/snippets", {"q": "Why do you want to work at Initech?"})
        self.assertEqual(offered["snippets"][0]["text"], self.LONG.strip())

    def test_a_cover_letter_is_a_snippet(self):
        for n, label in enumerate(("Cover Letter", "Cover letter (optional)")):
            with self.subTest(label):
                got = self.teach(self.item(label, f"{n} {self.LONG.strip()}", kind="textarea"), uid=f"J{n}")
                self.assertEqual([g["scope"] for g in got], ["snippet"])
        saved = bank.load(self.conn, PID)                # never a usual (auto-filled) answer
        self.assertEqual([k for k, v in saved.items() if "cover" in k and v.get("answer")], [])

    def test_a_short_why_us_answer_is_a_snippet_too_and_undo_removes_it(self):
        got = self.teach(self.item("Why are you interested in this role?", "The team ships fast.", kind="textarea"))
        self.assertEqual(got[0]["scope"], "snippet")
        learn.undo(self.conn, PID, got[0]["id"])
        self.assertEqual(learn.snippets(self.conn, PID), [])


class ChoicesTest(LearnCase):
    def test_a_typo_in_a_choice_answer_is_fixed_once_and_can_be_undone(self):
        bank.save(self.conn, PID, "orientation", "Hetrosexual")
        page = api.get(self.conn, PID, "/api/autofill/answers", {})
        self.assertEqual(page["fixed"], [{"key": "orientation", "label": "Sexual orientation", "was": "Hetrosexual",
                                          "now": "Heterosexual / Straight"}])
        self.assertEqual(self.answer("orientation"), "Heterosexual / Straight")
        row = next(r for r in page["learned"] if r["key"] == "orientation")
        learn.undo(self.conn, PID, row["id"])
        self.assertEqual(self.answer("orientation"), "Hetrosexual")
        again = api.get(self.conn, PID, "/api/autofill/answers", {})           # the user's words stay: not fixed again
        self.assertEqual((again["fixed"], self.answer("orientation")), ([], "Hetrosexual"))

    def test_choice_answers_get_common_and_learned_board_options(self):
        self.teach(self.item("Gender", "Man", kind="select", options=["Man", "Woman", "Decline"]),
                   company="Acme Robotics", board="ashby")
        rows = {r["key"]: r for r in api.get(self.conn, PID, "/api/autofill/answers", {})["bank"]}
        self.assertEqual(rows["gender"]["choices"][:4], ["Male", "Female", "Non-binary", choices.DECLINE])
        self.assertIn("Man", rows["gender"]["choices"])
        self.assertEqual(rows["work_auth"]["choices"], ["Yes", "No"])
        self.assertEqual(rows["salary"]["choices"], [])

    def test_a_far_answer_is_left_alone(self):
        bank.save(self.conn, PID, "race", "Asian (Not Hispanic or Latino)")
        self.assertEqual(choices.fix_typos(self.conn, PID, bank.load(self.conn, PID)), [])


class EducationAndStatsTest(LearnCase):
    def test_more_than_one_degree_and_the_first_is_the_highest(self):
        two = [{"school": "Lakeview State University", "degree": "Master's Degree", "field": "Computer Science",
                "start": "08/2023", "end": "05/2025"},
               {"school": "Riverside Institute of Technology", "degree": "Bachelor's Degree", "field": "Electronics", "start": "08/2016",
                "end": "05/2020"}]
        bank.save(self.conn, PID, "education_list", json.dumps(two))
        got = bank.history(self.conn, PID)["schools"]
        self.assertEqual([(s["school"], s["start"]) for s in got],
                         [("Lakeview State University", "08/2023"), ("Riverside Institute of Technology", "08/2016")])
        self.assertEqual((self.answer("school"), self.answer("degree"), self.answer("grad_date")),
                         ("Lakeview State University", "Master's Degree", "05/2025"))
        with self.assertRaises(ValueError):
            bank.save(self.conn, PID, "education_list", json.dumps([{"school": "X", "start": "2016"}]))

    def test_stats_count_auto_and_hand_once_per_step(self):
        for filled in (10, 12):                          # Autofill clicked twice on one step: 12 counts once
            api.post(self.conn, PID, "/api/autofill/report", {"uid": "J1", "board": "greenhouse", "step": "Apply",
                                                              "filled": filled, "needs": []}, db.utcnow())
        self.teach(self.item("Preferred team size", "Small"), hand=3, step="Apply")
        got = fillstats.summary(self.conn, PID)
        self.assertEqual(got["overall"], {"auto": 12, "hand": 3, "pct": 80})
        self.assertEqual((got["apps"][0]["auto"], got["apps"][0]["hand"], got["apps"][0]["pct"]), (12, 3, 80))
        self.assertEqual(len(got["weeks"]), 1)
