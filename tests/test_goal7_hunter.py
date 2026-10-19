"""Hunter live. Domain Search costs 1 credit per 1-10 emails returned (help.hunter.io article 1970956), 0
when nothing comes back; the free /v2/account answer (searches / verifications left) is shown in the panel, read on
demand and kept a few minutes; a key never leaves the request header."""
import time
from unittest import mock

from job_hunt_agent import server
from job_hunt_agent.finder import hunter, service
from tests.test_tracker_ui import TrackerCase

KEY = "test-hunter-key-not-real"
ACCOUNT = {"data": {"email": "someone@example.com", "plan_name": "Free", "plan_level": 0, "reset_date": "2026-10-18",
                    "requests": {"credits": {"used": 1, "available": 50.0},
                                 "searches": {"used": 1, "available": 50},
                                 "verifications": {"used": 0, "available": 100}}}}


def emails(n: int) -> dict:
    return {"data": {"pattern": "{first}.{last}", "emails": [
        {"value": f"p{i}@acme.ai", "first_name": f"P{i}", "last_name": "Lee", "position": "Recruiter",
         "confidence": 90, "verification": {"status": "valid"}} for i in range(n)]}}


class DomainSearchCreditsTest(TrackerCase):
    def test_one_credit_per_ten_emails_and_none_for_an_empty_answer(self):
        for found, credits in ((0, 0), (1, 1), (7, 1), (10, 1)):
            with mock.patch.object(hunter.http, "call", return_value=emails(found)) as call:
                result, used = hunter.domain_search(KEY, "acme.ai")
            self.assertEqual((len(result["people"]), used), (found, credits))
            self.assertEqual(call.call_args.args[4]["limit"], hunter.DOMAIN_LIMIT)
        self.assertLessEqual(hunter.DOMAIN_LIMIT, 10)                       # one search never spends 2 credits

    def test_the_panel_estimate_is_one_credit(self):
        row = self.add(company="Acme", status="applied")
        with mock.patch.object(service.keys, "key", return_value=KEY), \
                mock.patch.object(service.hunter, "account", return_value=None):
            est = service.panel(self.conn, "alex", row["id"], "acme.ai")["providers"]["hunter"]["estimate"]
        self.assertEqual(est["credits"], 1)
        self.assertIn("1 credit", est["detail"])

    def test_a_live_lookup_records_one_credit_and_ranks_hunter_contacts(self):
        row = self.add(company="Acme", role_title="ML Engineer", status="applied")
        with mock.patch.object(service.keys, "key", return_value=KEY), \
                mock.patch.object(hunter.http, "call", return_value=emails(3)):
            out = service.lookup(self.conn, "alex", row["id"], "hunter", "acme.ai")
        self.assertEqual(out["spent"], 1)
        people = [s for s in out["panel"]["suggestions"] if s["source"] == "hunter"]
        self.assertEqual(len(people), 3)
        self.assertEqual(people[0]["email_status"], "valid")


class AccountTest(TrackerCase):
    def setUp(self):
        super().setUp()
        hunter.ACCOUNT_CACHE.clear()

    def test_account_is_parsed_without_the_email_and_kept_for_a_while(self):
        with mock.patch.object(hunter.http, "call", return_value=ACCOUNT) as call:
            first = hunter.account("alex", KEY)
            again = hunter.account("alex", KEY)
        self.assertEqual(call.call_count, 1)
        self.assertEqual(first, again)
        self.assertEqual(first["searches_left"], 49)
        self.assertEqual(first["verifications_left"], 100)
        self.assertEqual((first["plan"], first["reset_date"]), ("Free", "2026-10-18"))
        self.assertNotIn("someone@example.com", repr(first))
        self.assertNotIn(KEY, repr(first))
        hunter.ACCOUNT_CACHE["alex"] = (time.monotonic() - hunter.ACCOUNT_TTL_S - 1, first)
        with mock.patch.object(hunter.http, "call", return_value=ACCOUNT) as call:
            hunter.account("alex", KEY)
        self.assertEqual(call.call_count, 1)

    def test_a_failed_account_read_is_an_explained_none_not_an_error(self):
        err = hunter.http.ProviderError("hunter", 401, "No user found for the API key supplied")
        with mock.patch.object(hunter.http, "call", side_effect=err):
            self.assertEqual(hunter.account("alex", KEY), {"error": "Hunter: HTTP 401: No user found for the API "
                                                                     "key supplied"})

    def test_the_route_answers_with_the_account_or_says_the_key_is_missing(self):
        with mock.patch.object(service.keys, "key", return_value=None):
            out = server.route(self.conn, "GET", "/api/contacts/hunter_account", {"profile": "alex"}, None)
        self.assertFalse(out["configured"])
        with mock.patch.object(service.keys, "key", return_value=KEY), \
                mock.patch.object(hunter.http, "call", return_value=ACCOUNT):
            out = server.route(self.conn, "GET", "/api/contacts/hunter_account", {"profile": "alex"}, None)
        self.assertEqual((out["configured"], out["account"]["searches_left"]), (True, 49))
