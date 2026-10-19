"""GOAL23 (user 2026-10-14): the Autofill extension's side of job-hunt-agent, against a throwaway DB, a synthetic profile and
the fake resumegen. The answer bank (seeded only from what the person gave; legal / EEO answers never defaulted),
"Needs you" (grouped, answered once, then filled everywhere), which job a page is and which resume it gets, §7's
address per resume city (street only, the tracker line "7480 Birdwood Ave, McLean, VA 22102"), Applied through the
Mark-applied code, the token header + CORS for the extension's own id, the Draft call and the extension zip."""
import http.client
import io
import json
import threading
import unittest
import zipfile
from pathlib import Path
from datetime import timedelta
from unittest import mock

from job_hunt_agent import applyback, config, db, server, trackapi
from job_hunt_agent.autofill import address, api, bank, draft, keys, match, package, questions, resume

from tests.test_goal8_flow import JD, NOW, PID, FakeResumegen, Goal8Case, seed_job

PROFILE = """name: Tess Tester
email: tess@example.com
phone: 913-555-0100
location: Austin, TX
linkedin: https://linkedin.com/in/tess
github: https://github.com/tess
families: [ml_engineer]
years: 4
needs_sponsorship: true
past_employers: ["Globex"]
resumes:
  - {id: ml, label: ML Engineer, file: Tess_ML.docx, families: [ml_engineer]}
"""
BASELINE = {"summary": "ML engineer.", "skills": [{"category": "ML", "items": ["Python", "PyTorch"]}],
            "experience": [{"company": "Globex, USA.", "title": "ML Engineer", "dates": "Jul 2025 – Present",
                            "bullets": ["Built models."]},
                           {"company": "Initech, India", "title": "Data Analyst", "dates": "Jan 2021 - Jul 2023",
                            "bullets": ["Made reports."]}],
            "education": [{"degree": "Master of Science in Computer Science",
                           "school": "Lakeview State University, Springfield, IL, USA",
                           "dates": "Aug 2023 – May 2025"}]}
RECENT = db.iso(NOW - timedelta(days=1))
CANDIDATES = [{"street": "1781 Chain Bridge Rd", "zip": "22102", "name": "The Ashby", "own_zip": True},
              {"street": "7480 Birdwood Ave", "zip": "22102", "name": None, "own_zip": True}]


class AutofillCase(Goal8Case):
    def setUp(self):
        super().setUp()
        folder = config.PROFILES / PID
        (folder / "profile.yaml").write_text(PROFILE)
        (folder / "answers.json").write_text(json.dumps({"work_auth": "Will require visa sponsorship: Yes",
                                                         "years": "4 years"}))
        (folder / "baseline.json").write_text(json.dumps(BASELINE))

    def field(self, label, kind="text", required=False, hints=(), options=()):
        return {"id": label[:20], "label": label, "kind": kind, "required": required, "hints": list(hints),
                "options": list(options)}

    def resolve(self, *fields, **ctx):
        return {a["id"]: a for a in bank.resolve(self.conn, PID, list(fields), ctx)}

    def cache(self, city_state="McLean, VA", found=None):
        with db.tx(self.conn):
            self.conn.execute("INSERT OR REPLACE INTO osm_places(city_state, candidates, fetched_at) VALUES (?,?,?)",
                              (city_state, json.dumps(CANDIDATES if found is None else found), RECENT))


class BankSeedTest(AutofillCase):
    def test_the_bank_starts_with_what_the_person_gave_and_nothing_else(self):
        got = {k: v["answer"] for k, v in bank.load(self.conn, PID).items()}
        self.assertEqual((got["first_name"], got["last_name"], got["email"], got["phone"]),
                         ("Tess", "Tester", "tess@example.com", "913-555-0100"))
        self.assertEqual((got["city"], got["state"], got["country"]),
                         ("Austin", "Texas", "United States of America"))
        self.assertEqual((got["linkedin"], got["years_exp"], got["sponsor_now"], got["sponsor_future"]),
                         ("https://linkedin.com/in/tess", "4", "Yes", "Yes"))
        self.assertEqual((got["degree"], got["field_of_study"], got["school"], got["grad_date"]),
                         ("Master's Degree", "Computer Science", "Lakeview State University", "05/2025"))
        for empty in ("address1", "zip", "work_auth", "gender", "race", "veteran", "disability", "signature_name",
                      "salary", "terms", "age18"):
            self.assertEqual(got[empty], "", empty)
        jobs = json.loads(got["history"])
        self.assertEqual(jobs[0], {"company": "Globex", "title": "ML Engineer", "location": "USA", "start": "07/2025",
                                   "end": "", "current": True})
        self.assertEqual((jobs[1]["start"], jobs[1]["end"], jobs[1]["current"]), ("01/2021", "07/2023", False))

    def test_sponsorship_is_left_empty_when_the_two_saved_answers_disagree(self):
        (config.PROFILES / PID / "answers.json").write_text(json.dumps({"work_auth": "Will require visa sponsorship: NO"}))
        got = bank.load(self.conn, PID)
        self.assertEqual((got["sponsor_now"]["answer"], got["sponsor_future"]["answer"]), ("", ""))

    def test_a_cleared_answer_is_not_seeded_again(self):
        bank.save(self.conn, PID, "email", "")
        self.assertEqual(bank.load(self.conn, PID)["email"]["answer"], "")

    def test_saving_checks_keys_dates_and_strips_apartment_numbers(self):
        with self.assertRaisesRegex(ValueError, "unknown answer"):
            bank.save(self.conn, PID, "favourite_colour", "blue")
        with self.assertRaisesRegex(ValueError, "MM/YYYY"):
            bank.save(self.conn, PID, "history", json.dumps([{"company": "X", "start": "2021"}]))
        self.assertEqual(bank.save(self.conn, PID, "address1", "12 Elm Street, Apt 4B")["answer"], "12 Elm St")
        self.assertEqual(bank.save(self.conn, PID, "address2", "Unit 9")["answer"], "")


class SkillsTest(unittest.TestCase):
    """GOAL23 §2 (user 14:24): the tailored resume's Skills section, one skill each."""

    def test_skills_split_on_commas_bars_and_labels(self):
        self.assertEqual(draft.skills_of("Languages: Python | PySpark, SQL; Cloud: AWS (S3, Glue), C++, CI/CD"),
                         ["Python", "PySpark", "SQL", "AWS", "S3", "Glue", "C++", "CI/CD"])
        self.assertEqual(draft.skills_of("Retrieval Augmented Generation (RAG), Hugging Face"),
                         ["Retrieval Augmented Generation (RAG)", "Hugging Face"])

    def test_the_tailored_section_is_used_without_duplicates(self):
        diff = {"skills": [{"items": {"base": "Java", "new": "Python, python, PyTorch"}},
                           {"items": {"base": "", "new": "Cloud: AWS (S3, Glue)"}}]}
        self.assertEqual(draft._from_diff(diff)["skills"], ["Python", "PyTorch", "AWS", "S3", "Glue"])

    def test_an_unreadable_tailored_resume_gives_no_skills_not_the_base_ones(self):
        with mock.patch.object(draft.rgclient, "request", side_effect=draft.rgclient.Offline("down")), \
                mock.patch.object(draft, "_base_skills") as base:
            self.assertEqual(draft.skills_for(PID, {"rg_id": 7, "kind": "approved"}), [])
        base.assert_not_called()

    def test_without_a_tailored_resume_the_uploaded_base_file_gives_the_skills(self):
        parsed = {"skills": [{"category": "Data", "items": ["Spark", "dbt", "spark"]}]}
        with mock.patch.object(draft, "_base_docx", return_value=Path("Base_Data.docx")) as where, \
                mock.patch.object(draft.baseline, "parse", return_value=parsed):
            self.assertEqual(draft.skills_for(PID, {"rg_id": None, "kind": "base", "resume_id": "de"}),
                             ["Spark", "dbt"])
        where.assert_called_once_with(PID, "de")


class ClassifyTest(AutofillCase):
    CASES = [("First Name", (), "first_name"), ("Legal Last Name*", (), "last_name"), ("Email Address", (), "email"),
             ("Phone Device Type", (), "phone_type"), ("Country Phone Code", (), "phone_code"),
             ("Phone Number", (), "phone"), ("Address Line 1", (), "address1"), ("Postal Code", (), "zip"),
             ("City", (), "city"), ("State", (), "state"), ("", ("legalname--firstname",), "first_name"),
             ("Are you legally authorized to work in the United States?", (), "work_auth"),
             ("Will you now or in the future require sponsorship for employment visa status (e.g., H-1B)?", (),
              "sponsor_future"),
             ("Do you require visa sponsorship?", (), "sponsor_now"),
             ("How Did You Hear About Us?", (), "how_heard"), ("Have you previously worked for Acme?", (), "prev_worked"),
             ("LinkedIn Profile", (), "linkedin"), ("Desired salary", (), "salary"),
             ("Are you at least 18 years of age?", (), "age18"),
             ("I have read and agree to the terms and conditions", (), "terms"), ("Gender", (), "gender"),
             ("Please select your race / ethnicity", (), "race"), ("Veteran Status", (), "veteran"),
             ("Disability Status", (), "disability"), ("Highest degree earned", (), "degree"),
             ("Are you willing to relocate?", (), "relocate"), ("Password", (), "account_password")]

    def test_common_questions_map_to_their_bank_key(self):
        for label, hints, want in self.CASES:
            with self.subTest(label):
                found = keys.classify(label, hints)
                self.assertEqual(found.key if found else None, want)

    def test_someone_elses_contact_details_and_legal_wording_are_not_his_contact_answers(self):
        for label in ("Referrer email", "Emergency contact phone", "Recruiter phone", "Reference name",
                      "Name of the employee who referred you"):
            with self.subTest(label):
                found = keys.classify(label)
                self.assertNotIn(found.key if found else None, ("email", "phone", "full_name", "first_name", "last_name"))
        found = keys.classify("In which country are you legally authorized to work?")
        self.assertTrue(found is None or found.legal, found and found.key)

    def test_unknown_questions_have_no_key_and_company_names_normalize(self):
        self.assertIsNone(keys.classify("What is your favourite sorting algorithm?"))
        self.assertEqual(keys.norm("Have you worked at ACME before? *", "Acme"), "have you worked at company before")


class ResolveTest(AutofillCase):
    def test_legal_and_eeo_questions_are_never_answered_without_a_saved_answer(self):
        got = self.resolve(self.field("Are you legally authorized to work in the US?", "radio", True),
                           self.field("Gender", "select"), self.field("Name", required=True),
                           section="self_identify")
        self.assertEqual({k: (v["status"], v["value"]) for k, v in got.items()},
                         {"Are you legally auth": ("needs", ""), "Gender": ("needs", ""), "Name": ("needs", "")})
        self.assertEqual(got["Name"]["key"], "signature_name")
        bank.save(self.conn, PID, "gender", "Female")
        self.assertEqual(self.resolve(self.field("Gender", "select"))["Gender"]["value"], "Female")

    def test_a_bare_name_under_a_signature_heading_is_the_signature_on_any_board(self):
        name = {**self.field("Name", required=True), "section": "Voluntary Self-Identification of Disability"}
        date_f = {**self.field("Date", required=True), "section": "Voluntary Self-Identification of Disability"}
        got = self.resolve(name, date_f, board="icims")
        self.assertEqual([(got[k]["key"], got[k]["status"], got[k]["value"]) for k in ("Name", "Date")],
                         [("signature_name", "needs", ""), ("signature_date", "needs", "")])
        plain = self.resolve({**self.field("Name"), "section": "Personal Information"}, board="icims")
        self.assertEqual(plain["Name"]["key"], "full_name")

    def test_derived_answers_follow_the_job(self):
        f = (self.field("Have you previously worked for this company?", "radio"),
             self.field("How did you hear about us?"), self.field("Confirm email"), self.field("Today's Date"))
        here = self.resolve(*f, company="Globex Corporation", source_url="https://www.linkedin.com/jobs/view/1")
        self.assertEqual(here["Have you previously w"[:20]]["value"], "Yes")
        self.assertEqual(here["How did you hear abou"[:20]]["value"], "Job Board")   # found on LinkedIn: a job board
        self.assertEqual(here["Confirm email"]["value"], "tess@example.com")
        self.assertEqual(here["Today's Date"]["status"], "needs")      # a signature's date: only once the user signs
        bank.save(self.conn, PID, "signature_name", "Tess Tester")
        self.assertRegex(self.resolve(f[3])["Today's Date"]["value"], r"^\d{2}/\d{2}/\d{4}$")
        other = self.resolve(*f[:2], company="Acme")
        self.assertEqual((other["Have you previously w"[:20]]["value"], other["How did you hear abou"[:20]]["value"]),
                         ("No", "Company Website"))

    def test_unknown_questions_ask_only_when_required_or_a_choice(self):
        got = self.resolve(self.field("Favourite colour"), self.field("Pet's name", required=True),
                           self.field("T-shirt size", "select"), self.field("Middle Name"), self.field("Password"))
        self.assertEqual([got[k]["status"] for k in ("Favourite colour", "Pet's name", "T-shirt size", "Middle Name",
                                                    "Password")], ["skip", "needs", "needs", "skip", "skip"])


class NeedsYouTest(AutofillCase):
    def ask(self, text, company, options=(), key=None, board="greenhouse"):
        return questions.record(self.conn, PID, [{"label": text, "kind": "select" if options else "text",
                                                  "options": list(options), "key": key}], board, company)

    def test_the_same_question_at_two_companies_is_one_entry_answered_once(self):
        self.ask("Have you ever worked at Acme as a contractor?", "Acme", ["Yes", "No"])
        self.assertEqual(self.ask("Have you ever worked at Initech as a contractor?", "Initech", ["Yes", "No"]), 1)
        entry = questions.open_list(self.conn, PID)[0]
        self.assertEqual((entry["seen"], entry["companies"], entry["options"]),
                         (2, ["Acme", "Initech"], ["Yes", "No"]))
        self.assertEqual(questions.answer(self.conn, PID, entry["id"], "no")["open"], 0)
        label = "Have you ever worked at Hooli as a contractor?"
        got = self.resolve(self.field(label, "radio"), company="Hooli", board="greenhouse")[label[:20]]
        self.assertEqual((got["status"], got["value"], got["option"]), ("fill", "No", "No"))

    def test_a_known_key_is_answered_into_the_bank_and_reopens_if_asked_again(self):
        self.ask("What is your notice period?", "Acme", key="notice")
        entry = questions.open_list(self.conn, PID)[0]
        questions.answer(self.conn, PID, entry["id"], "2 weeks")
        self.assertEqual(bank.load(self.conn, PID)["notice"]["answer"], "2 weeks")
        self.assertEqual(self.ask("What is your notice period?", "Acme", key="notice"), 1)   # did not fit: open again

    def test_address_fields_and_a_cover_letter_are_never_questions(self):      # user 2026-10-14
        for label, key in (("Postal Code", "zip"), ("Locality", None), ("Administrative Area", None), ("StateSelect", None),
                           ("What city are you located in?", None), ("Cover Letter", None)):
            with self.subTest(label):
                self.assertEqual(self.ask(label, "Acme", key=key), 0)

    def test_dismissed_stays_dismissed_and_empty_answers_are_refused(self):
        self.ask("Anything else?", "Acme")
        qid = questions.open_list(self.conn, PID)[0]["id"]
        with self.assertRaises(ValueError):
            questions.answer(self.conn, PID, qid, "  ")
        questions.dismiss(self.conn, PID, qid)
        self.assertEqual(self.ask("Anything else?", "Acme"), 0)


class AddressTest(AutofillCase):
    def test_the_tracker_line_is_street_city_state_zip(self):
        self.assertEqual(address.line("7480 Birdwood Avenue #3", "McLean", "Virginia", "22102-1234"),
                         "7480 Birdwood Ave, McLean, VA 22102")
        self.assertEqual(address.usps("1781 chain bridge road apt 4B"), "1781 Chain Bridge Rd")
        self.assertEqual(address.usps("100 north main street suite 200"), "100 N Main St")
        self.assertEqual(address.strip_unit("12 Elm St Bldg C Unit 5"), "12 Elm St")
        for street, want in (("16300 Space Center Boulevard", "16300 Space Center Blvd"),      # a word, not a unit
                             ("1200 Ste Genevieve Avenue", "1200 Ste Genevieve Ave"), ("500 Unit Rd", "500 Unit Rd"),
                             ("12 Old Court Road", "12 Old Court Rd"), ("8 Stone Ridge Road #3", "8 Stone Ridge Rd"),
                             ("9 Park Avenue South Apt 2", "9 Park Ave S"), ("44 Oak Lane Unit A", "44 Oak Ln")):
            with self.subTest(street):
                self.assertEqual(address.usps(street), want)

    def test_remote_or_a_base_resume_uses_the_home_address_once_it_is_saved(self):
        self.assertEqual(address.for_job(self.conn, PID, None)["kind"], "none")
        bank.save(self.conn, PID, "address1", "123 Main Street")
        bank.save(self.conn, PID, "zip", "78701")
        for rg in (None, {"workplace": "remote", "location": None},
                   {"workplace": "hybrid", "location": "Austin, TX"}):
            got = address.for_job(self.conn, PID, rg)
            self.assertEqual((got["kind"], got["line"]), ("home", "123 Main St, Austin, TX 78701"))

    def test_a_new_city_is_offered_a_suggestion_then_saved_and_reused_without_network(self):
        rg = {"workplace": "onsite", "location": "McLean, VA"}
        with mock.patch.object(address, "start_lookup") as look:
            self.assertEqual(address.for_job(self.conn, PID, rg)["kind"], "looking")
            look.assert_called_once_with("McLean, VA")
        self.cache()
        offer = address.for_job(self.conn, PID, rg)
        self.assertEqual((offer["kind"], offer["street"], offer["n"]), ("suggest", "1781 Chain Bridge Rd", 2))
        self.assertEqual(address.another(self.conn, PID, "McLean, VA")["street"], "7480 Birdwood Ave")
        used = address.use(self.conn, PID, "McLean, VA")
        self.assertEqual((used["kind"], used["line"]), ("saved", "7480 Birdwood Ave, McLean, VA 22102"))
        with mock.patch.object(address, "start_lookup", side_effect=AssertionError("no network")):
            self.assertEqual(address.for_job(self.conn, PID, rg)["line"], "7480 Birdwood Ave, McLean, VA 22102")
        edited = address.use(self.conn, PID, "mclean, va", "55 Oak Lane Apt 2", "22101")
        self.assertEqual(edited["line"], "55 Oak Ln, McLean, VA 22101")
        self.assertEqual([a["city_state"] for a in address.listing(self.conn, PID)], ["McLean, VA"])
        with self.assertRaisesRegex(ValueError, "5 digits"):
            address.use(self.conn, PID, "McLean, VA", "55 Oak Ln", "221")

    def test_osm_results_keep_real_house_numbers_and_never_a_unit(self):
        answers = {"search": [{"lat": "38.93", "lon": "-77.17", "address": {"postcode": "22101"}}],
                   "interpreter": {"elements": [
                       {"tags": {"addr:housenumber": "1781", "addr:street": "Chain Bridge Road",
                                 "addr:postcode": "22102", "addr:unit": "4B"}},
                       {"tags": {"addr:housenumber": "12A", "addr:street": "Elm Street"}},
                       {"tags": {"addr:housenumber": "Building C", "addr:street": "Nowhere Rd"}},
                       {"tags": {"addr:housenumber": "1781", "addr:street": "Chain Bridge Road"}}]}}
        calls = []

        def fake(url):
            calls.append(url)
            return answers["search" if "nominatim" in url else "interpreter"]
        got = address.fetch_candidates("McLean, VA", fake)
        self.assertEqual([(c["street"], c["zip"]) for c in got],
                         [("1781 Chain Bridge Rd", "22102"), ("12A Elm St", "22101")])
        self.assertEqual(len(calls), 3)                     # Nominatim's apartments (none), the city, Overpass
        self.assertIn("amenity=apartments", calls[0])
        self.assertIn("city=McLean", calls[1])

    def test_an_empty_lookup_can_be_tried_again(self):
        with db.tx(self.conn):
            self.conn.execute("INSERT INTO osm_places(city_state, candidates, fetched_at) VALUES ('Chantilly, VA', '[]', ?)",
                              (db.iso(db.utcnow()),))
        self.assertEqual(address.offer(self.conn, PID, "Chantilly, VA", fetch=False)["kind"], "failed")
        with mock.patch.object(address, "start_lookup") as start:
            again = address.another(self.conn, PID, "Chantilly, VA")
        start.assert_called_once_with("Chantilly, VA")
        self.assertEqual(again["kind"], "looking")
        self.assertIsNone(self.conn.execute("SELECT 1 FROM osm_places WHERE city_state = 'Chantilly, VA'").fetchone())

    def test_a_busy_overpass_server_falls_back_to_the_next(self):
        calls = []

        def fake(url):
            calls.append(url)
            if "nominatim" in url:
                return [{"lat": "38.93", "lon": "-77.17", "address": {"postcode": "22101"}}]
            if url.startswith(address.OVERPASS[0]):
                raise address.net.HttpError(url, 504, "Gateway Timeout")
            if url.startswith(address.OVERPASS[1]):
                return {"elements": [], "remark": "runtime error: Query timed out"}
            return {"elements": [{"tags": {"addr:housenumber": "7480", "addr:street": "Birdwood Avenue"}}]}
        got = address.fetch_candidates("McLean, VA", fake)
        self.assertEqual([c["street"] for c in got], ["7480 Birdwood Ave"])
        self.assertEqual([u.split("?")[0] for u in calls[2:]], list(address.OVERPASS))

    def test_nominatim_apartment_buildings_come_first_with_one_request(self):
        calls = []

        def fake(url):
            calls.append(url)
            return [{"type": "apartments", "name": "Kula Kai Apartments",
                     "address": {"house_number": "808", "road": "Comet Drive", "postcode": "94404", "city": "Foster City"}},
                    {"type": "apartments", "address": {"road": "Clipper Lane", "postcode": "94404", "city": "Foster City"}},
                    {"type": "apartments", "address": {"house_number": "9", "road": "Next Town Rd", "postcode": "94403",
                                                       "city": "San Mateo"}},
                    {"type": "swimming_pool", "address": {"house_number": "1", "road": "Pool Rd", "postcode": "94404",
                                                          "city": "Foster City"}}]
        got = address.fetch_candidates("Foster City, CA", fake)
        self.assertEqual([(c["street"], c["zip"]) for c in got], [("808 Comet Dr", "94404")])
        self.assertEqual(len(calls), 1)

    def test_address_fields_fill_the_city_at_once_then_the_whole_saved_address(self):
        fields = [self.field("Address Line 1"), self.field("City"), self.field("State", "select"),
                  self.field("Postal Code"), self.field("Address Line 2")]
        early = self.resolve(*fields, address=address.answers_for({"kind": "looking", "city_state": "McLean, VA"}))
        self.assertEqual({k: (v["status"], v["value"]) for k, v in early.items()},      # never a question, never a hold
                         {"Address Line 1": ("skip", ""), "City": ("fill", "McLean"), "State": ("fill", "Virginia"),
                          "Postal Code": ("skip", ""), "Address Line 2": ("skip", "")})
        self.cache()
        address.use(self.conn, PID, "McLean, VA")
        values = address.answers_for(address.saved(self.conn, PID, "McLean, VA"))
        got = self.resolve(*fields, address=values)
        self.assertEqual([got[k]["value"] for k in ("Address Line 1", "City", "State", "Postal Code", "Address Line 2")],
                         ["1781 Chain Bridge Rd", "McLean", "Virginia", "22102", ""])
        self.assertEqual(got["Address Line 2"]["status"], "skip")


class MatchAndResumeTest(AutofillCase):
    def test_a_feed_job_is_found_from_its_posting_and_apply_pages(self):
        lever = "6f78b170-9600-4b2d-9682-feead160100d"
        uid = seed_job(self.conn, lever)
        for url in (f"https://jobs.lever.co/acme/{lever}", f"https://jobs.lever.co/acme/{lever}/apply"):
            got = match.find(self.conn, PID, url, jobs=[])
            self.assertEqual((got["job"]["uid"], got["job"]["how"]), (uid, "url"))

    def test_an_outside_job_from_a_pasted_jd_is_matched_by_its_link(self):
        rg = self.rg.add(PID, "Hooli", "ML Engineer", JD, status="ready", times={"made": RECENT},
                         jd_url="https://hooli.wd5.myworkdayjobs.com/en-US/ext/job/X/ML_R123")
        got = match.find(self.conn, PID, "https://hooli.wd5.myworkdayjobs.com/ext/job/X/ML_R123/apply/applyManually")
        self.assertEqual((got["job"]["source"], got["job"]["company"], got["job"]["how"]), ("outside", "Hooli", "resume"))
        linked = self.conn.execute("SELECT rg_id FROM rg_links WHERE app_id = ?", (got["job"]["app"],)).fetchone()[0]
        self.assertEqual(linked, rg)

    def test_an_unknown_page_offers_recent_resumes_and_a_pick_is_remembered_for_every_step(self):
        self.rg.add(PID, "Hooli", "ML Engineer", JD, jd_url="https://www.linkedin.com/jobs/view/42", status="ready",
                    times={"made": RECENT})
        self.rg.add(PID, "Old", "Role", JD, source_job_id=self.uids[1], status="ready",
                    times={"made": db.iso(NOW - timedelta(days=20))})
        page = "https://hooli.wd1.myworkdayjobs.com/en-US/Careers/job/Remote/ML-Engineer_JR9/apply"
        got = match.find(self.conn, PID, page)
        self.assertIsNone(got["job"])
        self.assertEqual([r["company"] for r in got["recent"]], ["Hooli"])
        match.remember(self.conn, PID, page, app_id=got["recent"][0]["app"])
        again = match.find(self.conn, PID, page.replace("/apply", "/apply/applyManually"))
        self.assertEqual((again["job"]["company"], again["job"]["how"]), ("Hooli", "picked"))

    def test_the_resume_is_the_approved_one_else_ready_with_a_warning_else_the_base(self):
        base = resume.choose(self.conn, PID, self.uids[0], None)
        self.assertEqual((base["kind"], base["file_name"]), ("base", "Tess_ML.pdf"))   # the plain name: no company
        self.assertIn("No tailored resume", base["warning"])
        ready = self.rg.add(PID, "Acme", "MLE", JD, source_job_id=self.uids[0], status="ready",
                            result={"base": {"id": "ml", "label": "ML Engineer"}})
        got = resume.choose(self.conn, PID, self.uids[0], None)
        self.assertEqual((got["kind"], got["rg_id"]), ("ready", ready))
        self.assertIn("Not approved", got["warning"])
        self.rg.jobs[ready]["drive_file_id"] = "F1"
        got = resume.choose(self.conn, PID, self.uids[0], None)
        self.assertEqual((got["kind"], got["warning"], got["file_name"]), ("approved", None, "Tess_ML.pdf"))
        self.assertEqual(resume.file(self.conn, PID, self.uids[0], None)[:2], (b"%PDF-1.4 tailored", "application/pdf"))
        self.rg.stop()
        offline = resume.choose(self.conn, PID, self.uids[0], None)
        self.assertEqual(offline["kind"], "base")
        self.assertIn("offline", offline["warning"])
        self.assertEqual(resume.file(self.conn, PID, self.uids[0], None)[0], b"%PDF-1.4 base")
        self.rg = FakeResumegen()


class UrlKeyTest(unittest.TestCase):
    """A remembered pick is for that job's page only: the query's job / company ids are part of the key."""

    def test_two_jobs_on_one_careers_page_never_share_a_pick(self):
        k = match.url_key
        self.assertNotEqual(k("https://acme.com/careers/job?gh_jid=111"), k("https://acme.com/careers/job?gh_jid=222"))
        self.assertNotEqual(k("https://career4.successfactors.com/career?company=Acme&career_job_req_id=5"),
                            k("https://career4.successfactors.com/career?company=Globex&career_job_req_id=5"))
        self.assertEqual(k("https://acme.com/careers/job?gh_jid=111&utm_source=linkedin"),
                         k("https://www.acme.com/careers/job/?gh_jid=111"))
        self.assertEqual(k("https://x.wd5.myworkdayjobs.com/en-US/ext/job/Remote/ML_R1/apply/applyManually"),
                         k("https://x.wd5.myworkdayjobs.com/ext/job/Remote/ML_R1"))


class AppliedTest(AutofillCase):
    def fake_confirm(self, conn, pid, now, uid=None, app_id=None, resume_id=None):
        app = trackapi.create(conn, pid, {"job_uid": uid, "status": "applied"})["id"]
        return {"app": app, "attached": {"kind": "tailored"}, "ask": []}

    def test_applied_uses_mark_applied_once_and_writes_the_address_used(self):
        self.rg.add(PID, "Acme", "MLE", JD, source_job_id=self.uids[0], status="ready", workplace="onsite",
                    location="McLean, VA")
        self.cache()
        address.use(self.conn, PID, "McLean, VA")
        with mock.patch.object(applyback, "confirm", side_effect=self.fake_confirm) as confirm:
            out = api.applied(self.conn, PID, {"uid": self.uids[0]}, NOW)
            again = api.applied(self.conn, PID, {"uid": self.uids[0]}, NOW)
        self.assertEqual(confirm.call_count, 1)
        self.assertTrue(again["already"])
        self.assertEqual(out["address"], "1781 Chain Bridge Rd, McLean, VA 22102")
        self.assertEqual(trackapi._own(self.conn, PID, out["app"])["address"], "1781 Chain Bridge Rd, McLean, VA 22102")

    def test_with_no_tailored_resume_the_row_records_the_base_file_that_was_uploaded(self):
        base = {"kind": "base", "rg_id": None, "resume_id": "data_engineer"}
        with mock.patch.object(applyback, "confirm", side_effect=self.fake_confirm) as confirm, \
                mock.patch.object(api.resume, "choose", return_value=base):
            api.applied(self.conn, PID, {"uid": self.uids[1]}, NOW)
        self.assertEqual(confirm.call_args.kwargs.get("resume_id"), "data_engineer")

    def test_report_keeps_counts_and_questions_never_values(self):
        out = api.report(self.conn, PID, {"uid": self.uids[0], "board": "greenhouse", "company": "Acme",
                                          "filled": 18, "skipped": 1,
                                          "needs": [{"label": "Pet's name", "kind": "text"}]}, NOW)
        self.assertEqual(out["open"], 1)
        row = dict(self.conn.execute("SELECT * FROM autofill_log").fetchone())
        self.assertEqual((row["filled"], row["needs"], row["skipped"], row["board"]), (18, 1, 1, "greenhouse"))
        self.assertFalse({"value", "answer", "text"} & set(row))


class DraftTest(AutofillCase):
    def test_draft_sends_jd_and_resume_strips_dashes_and_keeps_it(self):
        seen = {}

        def runner(prompt, pid):
            seen["prompt"] = prompt
            return '"I build ML systems — in Python. I would bring that to Acme."'
        out = draft.draft(self.conn, PID, self.uids[0], None, "Why do you want to work here?", runner=runner)
        self.assertEqual(out["text"], "I build ML systems, in Python. I would bring that to Acme.")
        self.assertIn("Kubernetes", seen["prompt"])
        self.assertIn("Built models.", seen["prompt"])
        self.assertIn("Use ONLY facts", seen["prompt"])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM autofill_drafts").fetchone()[0], 1)
        with self.assertRaisesRegex(ValueError, "not matched"):
            draft.draft(self.conn, PID, None, None, "Why?", runner=runner)


class ServerTest(AutofillCase):
    def setUp(self):
        super().setUp()
        self.srv = server.make_server(lan=False, port=0)
        self.srv.token = "lan-tok"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.origin = f"chrome-extension://{package.own_id()}"

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        super().tearDown()

    def call(self, method, path, body=None, **headers):
        conn = http.client.HTTPConnection("127.0.0.1", self.srv.server_port, timeout=10)
        conn.request(method, path, json.dumps(body) if body is not None else None,
                     {"Host": "localhost", "X-Jobhunt": "1", "Content-Type": "application/json", **headers})
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, dict(resp.getheaders()), data

    def test_the_extension_token_header_opens_autofill_paths_only(self):
        tok = {"X-Jobhunt-Token": "lan-tok", "Origin": self.origin}
        status, headers, data = self.call("GET", f"/api/autofill/ping?profile={PID}", **tok)
        self.assertEqual((status, json.loads(data)["profile"]), (200, PID))
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), self.origin)
        self.assertEqual(self.call("GET", f"/api/autofill/ping?profile={PID}", **{"X-Jobhunt-Token": "wrong"})[0], 401)
        self.assertEqual(self.call("GET", f"/api/jobs?profile={PID}", **tok)[0], 401)
        other = self.call("GET", f"/api/autofill/ping?profile={PID}", **{**tok, "Origin": "https://evil.example"})
        self.assertNotIn("Access-Control-Allow-Origin", other[1])

    def test_preflight_answers_only_the_extension(self):
        ok = self.call("OPTIONS", "/api/autofill/resolve", Origin=self.origin)
        self.assertEqual((ok[0], ok[1].get("Access-Control-Allow-Origin")), (204, self.origin))
        self.assertIn("X-Jobhunt-Token", ok[1].get("Access-Control-Allow-Headers"))
        self.assertEqual(self.call("OPTIONS", "/api/autofill/resolve", Origin="https://evil.example")[0], 403)
        self.assertEqual(self.call("OPTIONS", "/api/jobs", Origin=self.origin)[0], 403)

    def test_a_snapshot_may_be_larger_than_other_posts_and_the_resume_comes_as_a_pdf(self):
        big = "<form>" + "<input name=x>" * 20000 + "</form>"
        status, _, data = self.call("POST", "/api/autofill/snapshot",
                                    {"profile": PID, "url": "https://x.example/a", "html": big},
                                    **{"X-Jobhunt-Token": "lan-tok"})
        self.assertEqual(status, 200, data)
        status, headers, data = self.call("GET", f"/api/autofill/resume?profile={PID}&uid={self.uids[0]}",
                                          **{"X-Jobhunt-Token": "lan-tok"})
        self.assertEqual((status, data, headers["Content-Type"]), (200, b"%PDF-1.4 base", "application/pdf"))
        self.assertIn("Tess_ML.pdf", headers["Content-Disposition"])


class PackageTest(AutofillCase):
    def test_the_zip_is_the_extension_folder_and_its_id_is_fixed(self):
        names = zipfile.ZipFile(io.BytesIO(package.zip_bytes())).namelist()
        self.assertIn("job-hunt-agent-autofill/manifest.json", names)
        self.assertIn("job-hunt-agent-autofill/content/core.js", names)
        self.assertFalse([n for n in names if "/test/" in n])
        self.assertRegex(package.own_id(), r"^[a-p]{32}$")
        self.assertIn(f"chrome-extension://{package.own_id()}", package.allowed_origins())
