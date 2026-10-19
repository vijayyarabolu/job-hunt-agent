"""Stop asking what it can know (user 2026-10-14): address fields are never a question (any wording: Paylocity's
Postal Code / Administrative Area / Locality / StateSelect, "what city are you located in"), the address comes from
the tracker row, else the best OpenStreetMap address for the resume's city picked and saved at once (no confirm
click), the old address items leave Needs you, a cover letter is never asked, and saving answers / the address is
quick (database now, the Google Sheet with the next background sync)."""
import threading
import time
from unittest import mock

from job_hunt_agent import api as jhapi, db
from job_hunt_agent.autofill import address, api, bank, keys, questions
from job_hunt_agent.google import state as gstate
from tests.test_goal23_autofill import AutofillCase
from tests.test_goal8_flow import NOW, PID, seed_job

ONSITE = {"workplace": "onsite", "location": "McLean, VA"}
FAST_S = 0.2
PAYLOCITY = (("Street Address", "address1"), ("Locality", "city"), ("Administrative Area", "state"),
             ("StateSelect", "state"), ("Postal Code", "zip"), ("Country", "country"))
OTHER_WORDS = (("What city are you located in?", "city"), ("Town", "city"), ("Province", "state"), ("Region", "state"),
               ("State / Province / Region", "state"), ("ZIP code", "zip"), ("City", "city"))


class AddressWordsTest(AutofillCase):
    def test_every_address_wording_is_an_address_key(self):
        for label, key in PAYLOCITY + OTHER_WORDS:
            with self.subTest(label):
                self.assertEqual(keys.classify(label).key, key)

    def test_paylocity_fields_fill_from_the_jobs_address_and_none_is_a_question(self):
        self.cache()
        values = address.answers_for(address.use(self.conn, PID, "McLean, VA"))
        fields = [self.field(label, "select" if label == "StateSelect" else "text", required=True) for label, _ in PAYLOCITY]
        got = self.resolve(*fields, address=values)
        self.assertEqual({k: (v["status"], v["value"]) for k, v in got.items()},
                         {"Street Address": ("fill", "1781 Chain Bridge Rd"), "Locality": ("fill", "McLean"),
                          "Administrative Area": ("fill", "Virginia"), "StateSelect": ("fill", "Virginia"),
                          "Postal Code": ("fill", "22102"), "Country": ("fill", bank.US_COUNTRY)})

    def test_no_city_known_gives_only_the_country(self):
        self.assertEqual(address.answers_for({"kind": "none", "city_state": ""}), {"address2": "", "country": bank.US_COUNTRY})

    def test_a_cover_letter_is_left_empty_and_never_asked(self):
        got = self.resolve(self.field("Cover Letter", "textarea", required=True))
        self.assertEqual(got["Cover Letter"]["status"], "skip")


class AddressOrderTest(AutofillCase):
    def setUp(self):
        super().setUp()
        self.uid = seed_job(self.conn, "zz-ask-less-1")
        self.app = jhapi.mark_applied(self.conn, PID, self.uid, NOW)     # its tracker row (Address: the job's city)
        self.tailored = mock.patch.object(api.resume, "tailored_job", return_value=ONSITE)
        self.tailored.start()
        self.addCleanup(self.tailored.stop)

    def row_address(self):
        return self.conn.execute("SELECT address FROM applications WHERE id = ?", (self.app,)).fetchone()["address"]

    def test_the_tracker_rows_full_address_comes_first(self):
        with db.tx(self.conn):
            self.conn.execute("UPDATE applications SET address = '55 Oak Ln, McLean, VA 22101' WHERE id = ?", (self.app,))
        got = api.job_address(self.conn, PID, self.uid, None, jobs=[])
        self.assertEqual((got["kind"], got["line"]), ("tracker", "55 Oak Ln, McLean, VA 22101"))

    def test_without_one_the_best_osm_address_is_picked_and_saved_to_the_tracker_without_a_click(self):
        self.cache()
        got = api.job_address(self.conn, PID, self.uid, None, jobs=[])
        self.assertEqual((got["kind"], got["line"], got.get("auto")), ("saved", "1781 Chain Bridge Rd, McLean, VA 22102", True))
        self.assertEqual(self.row_address(), "1781 Chain Bridge Rd, McLean, VA 22102")            # silently, in the row
        self.assertEqual(address.saved(self.conn, PID, "McLean, VA")["source"], "osm")
        again = api.job_address(self.conn, PID, self.uid, None, jobs=[])                           # the row decides now
        self.assertEqual(again["kind"], "tracker")

    def test_a_fill_waits_for_the_citys_first_lookup_then_fills(self):
        def lookup(city_state):
            address._LOOKUPS[city_state] = "looking"

            def done():
                time.sleep(0.4)
                conn = db.connect()
                with db.tx(conn):
                    conn.execute("INSERT OR REPLACE INTO osm_places(city_state, candidates, fetched_at) VALUES (?,?,?)",
                                 (city_state, '[{"street": "1781 Chain Bridge Rd", "zip": "22102"}]', db.iso(db.utcnow())))
                conn.close()
                address._LOOKUPS.pop(city_state, None)
            threading.Thread(target=done, daemon=True).start()
        with mock.patch.object(address, "start_lookup", side_effect=lookup):
            got = api.post(self.conn, PID, "/api/autofill/resolve", {"uid": self.uid, "fields": [
                self.field("Locality"), self.field("Postal Code")]}, NOW)
        self.assertEqual([(a["status"], a["value"]) for a in got["answers"]], [("fill", "McLean"), ("fill", "22102")])

    def test_change_in_the_panel_saves_for_the_city_and_this_jobs_row(self):
        self.cache()
        got = api.post(self.conn, PID, "/api/autofill/address/use", {"uid": self.uid, "city_state": "McLean, VA",
                                                                     "street": "7480 Birdwood Ave", "zip": "22102"}, NOW)
        self.assertEqual(got["line"], "7480 Birdwood Ave, McLean, VA 22102")
        self.assertEqual(self.row_address(), "7480 Birdwood Ave, McLean, VA 22102")


class NeedsYouCleanupTest(AutofillCase):
    def test_old_address_and_cover_letter_items_leave_needs_you(self):
        now = db.iso(db.utcnow())
        with db.tx(self.conn):
            for text in ("Locality", "Postal Code", "Administrative Area", "Cover Letter", "Why do you want this job?",
                         "CountryUnited States", "StateFL", "StateSelect a state", "Address Line 1",     # glued to the pick
                         "What city are you currently located in? (MANDATORY)", "Race or Ethnicity"):
                self.conn.execute("INSERT INTO autofill_questions(profile, qnorm, key, text, kind, options, boards, companies, "
                                  "first_at, last_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                                  (PID, keys.norm(text), None, text, "text", "[]", "[]", "[]", now, now))
        api.get(self.conn, PID, "/api/autofill/answers", {})               # opening the Answers page tidies it
        self.assertEqual(sorted(q["text"] for q in questions.open_list(self.conn, PID)), ["Race or Ethnicity", "Why do you want this job?"])


class QuickSaveTest(AutofillCase):
    """Saving returns at once: the database now; the Sheet only with the next background sync (no network here)."""

    def test_saving_answers_and_the_address_takes_under_200_ms_without_the_network(self):
        uid = seed_job(self.conn, "zz-ask-less-2")
        jhapi.mark_applied(self.conn, PID, uid, NOW)
        self.cache()
        calls = [("/api/autofill/answer", {"key": "notice", "answer": "2 weeks"}),
                 ("/api/autofill/address/use", {"uid": uid, "city_state": "McLean, VA", "street": "7480 Birdwood Ave",
                                                "zip": "22102"}),
                 ("/api/autofill/learn", {"items": [{"label": "Preferred team size", "value": "6-20", "kind": "text"}],
                                          "board": "greenhouse", "company": "Acme", "uid": uid, "hand": 1})]
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("no network while saving")):
            for path, args in calls:
                with self.subTest(path):
                    start = time.perf_counter()
                    api.post(self.conn, PID, path, args, NOW)
                    self.assertLess(time.perf_counter() - start, FAST_S)
        self.assertIsNotNone(gstate.dirty(self.conn)[0])                # the Sheet is told by the background sync
