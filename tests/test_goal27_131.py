"""1.3.1 (Prompts/AUTOFILL-BUGS.md, a real Capital One Workday test): a past job's Location is the employer's
country (never the target job's city the tailored copy carries); a choice is matched by meaning ("I am not a
protected veteran" -> No, Male -> Man, "Decline to self-identify", "(Not Hispanic or Latino)" wordings) and only then
by Claude, from the form's own list; Claude's pick outside the list is dropped."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from job_hunt_agent.autofill import assist, canary, experience

from tests.test_goal23_autofill import AutofillCase
from tests.test_goal8_flow import PID

PICK = Path(__file__).resolve().parent.parent / "extension" / "content" / "pick.js"


class CountryTest(unittest.TestCase):
    def test_a_place_becomes_its_country(self):
        cases = {"McLean, VA": "United States", "USA": "United States", "U.S.A.": "United States", "Austin TX": "United States",
                 "Boston, Massachusetts": "United States", "New York": "United States", "India": "India",
                 "Hyderabad, Telangana": "India", "Bengaluru": "India", "Toronto, Canada": "Canada", "": "", "Remote": ""}
        for where, want in cases.items():
            with self.subTest(where):
                self.assertEqual(experience.country(where), want)

    def test_past_jobs_get_the_employers_country_not_the_target_jobs_city(self):
        tailored = [{"company": "Globex Properties, McLean, VA", "title": "Data Scientist", "dates": "Jan 2024 – Present", "bullets": ["a"]},
                    {"company": "Deloitte, McLean, VA", "title": "Data Analyst", "dates": "Jan 2022 – Dec 2023", "bullets": []},
                    {"company": "International Business Machines, McLean, VA", "title": "Software Engineer", "dates": "Jun 2018 – Dec 2021", "bullets": []}]
        base = [{"company": "Globex Properties", "location": "USA"}, {"company": "Deloitte Corporation", "location": "Boston, MA"},
                {"company": "International Business Machines", "location": "India"}]
        rows = experience.job_rows(tailored, base)
        self.assertEqual([(r["company"], r["location"]) for r in rows],
                         [("Globex Properties", "United States"), ("Deloitte", "United States"), ("International Business Machines", "India")])

    def test_without_his_list_the_tailored_place_still_becomes_a_country(self):
        rows = experience.job_rows([{"company": "Globex Corp, Austin TX", "title": "ML", "dates": "2023 – Present", "bullets": []}])
        self.assertEqual((rows[0]["company"], rows[0]["location"]), ("Globex Corp", "United States"))


class ChooseTest(AutofillCase):
    def test_claude_picks_only_from_the_forms_own_list(self):
        def fake(prompt, pid):
            return json.dumps([{"id": "gid", "pick": "Cisgender man"}, {"id": "vet", "pick": "No, never"}])
        items = [{"id": "gid", "question": "Gender identity", "answer": "Male", "options": ["Cisgender man", "Cisgender woman"]},
                 {"id": "vet", "question": "Veteran?", "answer": "I am not a protected veteran", "options": ["Yes", "No"]},
                 {"id": "x", "question": "No options", "answer": "Yes", "options": []}]
        with mock.patch.object(assist, "_run", lambda prompt, pid, model, r=None: fake(prompt, pid)):
            got = {p["id"]: p for p in assist.choose(self.conn, PID, {"items": items})["picks"]}
        self.assertEqual((got["gid"]["pick"], got["gid"]["status"]), ("Cisgender man", "review"))
        self.assertEqual((got["vet"]["pick"], got["vet"]["status"]), ("", "none"))        # not one of its options: dropped
        self.assertNotIn("x", got)                                                      # nothing to choose from: never sent


class RealSitesTest(AutofillCase):
    def test_one_page_per_ats_and_a_per_field_table(self):
        def drive(home, pages, chrome, fields=None):
            fields.extend([{"board": "greenhouse", "label": "Email", "key": "email", "status": "filled", "why": ""},
                           {"board": "greenhouse", "label": "Gender", "key": "gender", "status": "failed", "why": "the page did not keep it"}])
            return [{**p, "fields": 2, "filled": 1} for p in pages]
        with mock.patch.object(canary, "_drive", drive), \
                mock.patch.object(canary, "live_pages", lambda conn, boards=canary.LIVE_BOARDS: [{"name": "greenhouse-live", "board": "greenhouse",
                                                                                                "url": "https://job-boards.greenhouse.io/x/jobs/1", "source": "live"}]):
            got = canary.realsites(self.conn, urls=["https://careers.example.com/apply/9"])
        self.assertEqual([p["board"] for p in got["pages"]], ["greenhouse", "url"])
        self.assertIn("| greenhouse | https://job-boards.greenhouse.io/x/jobs/1 | 1 | 0 | 0 | 1 | Gender: the page did not keep it |", got["markdown"])
        self.assertIn("workday", canary.REAL_BOARDS)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM autofill_canary").fetchone()[0], 0)   # nothing recorded


@unittest.skipUnless(shutil.which("node"), "needs node")
class MeaningTest(unittest.TestCase):
    def best(self, options, answer):
        out = subprocess.run(["node", "-e", f"const p = require({json.dumps(str(PICK))}); const r = p.best({json.dumps(options)}, [{json.dumps(answer)}]); "
                              "console.log(JSON.stringify(r ? r.text : null))"], capture_output=True, text=True, timeout=30, check=True)
        return json.loads(out.stdout)

    def test_choices_by_meaning(self):
        yn = ["Yes", "No", "Decline to self-identify"]
        self.assertEqual(self.best(yn, "I am not a protected veteran"), "No")
        self.assertEqual(self.best(["Yes", "No"], "I am a protected veteran"), "Yes")
        self.assertEqual(self.best(yn, "I don't wish to answer"), "Decline to self-identify")
        self.assertEqual(self.best(["Man", "Woman", "Decline to self-identify"], "Male"), "Man")
        race = ["American Indian or Alaska Native (Not Hispanic or Latino) (United States of America)",
                "Asian (Not Hispanic or Latino) (United States of America)",
                "Two or More Races (Not Hispanic or Latino) (United States of America)"]
        self.assertEqual(self.best(race, "Asian"), race[1])
        self.assertEqual(self.best(race, "Two or more races"), race[2])
        self.assertEqual(self.best(["Yes", "No", "I do not want to answer"], "No, I do not have a disability"), "No")
        self.assertIsNone(self.best(["Cisgender man", "Cisgender woman"], "Male"))     # Claude's turn, never a guess here


if __name__ == "__main__":
    unittest.main()
