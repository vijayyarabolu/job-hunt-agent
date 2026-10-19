"""Find contacts. Free signals first (people named in the JD, the company's team page), LinkedIn people-search
links and an "Open in Apollo" link (no scraping, no API), Apollo organization enrich + top people only on a click
(per-person key, daily credit cap, cached so the same lookup never spends twice), Hunter off until a key exists, an
email-pattern guess labelled "guessed, unverified", never a phone number, and Add contact -> the sheet's cells."""
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from job_hunt_agent import fetch, server, trackapi
from job_hunt_agent.finder import apollo, http, ledger, rank, service, signals
from job_hunt_agent.google import state as gstate
from tests.test_tracker_ui import TrackerCase, TODAY

KEY = "test-key-not-real-123"
JD = ("About the role\n\nJoin our Machine Learning team in Austin. You will report to the Head of Machine Learning.\n"
      "Questions? Contact our recruiter, Jane Doe (jane.doe@acme.ai). Learn more at https://www.acme.ai/about.\n"
      "Accommodations: careers@acme.ai. Acme is an Equal Opportunity Employer.")
ORG = {"organization": {"id": "org1", "name": "Acme", "primary_domain": "acme.ai", "estimated_num_employees": 120,
                        "phone": "+1 555 0100", "industry": "software"}}
PEOPLE = {"people": [
    {"id": "p1", "first_name": "Sam", "last_name": "Lee", "title": "Head of Machine Learning",
     "linkedin_url": "https://www.linkedin.com/in/samlee", "city": "Austin", "state": "Texas",
     "phone_numbers": [{"raw_number": "+1 555 0101"}], "email": "email_not_unlocked@domain.com"},
    {"id": "p2", "first_name": "Ana", "last_name": "Ruiz", "title": "Chief Executive Officer",
     "sanitized_phone": "+15550102", "email_status": "verified"}]}


def fake_apollo(calls: list, org=ORG, people=PEOPLE):
    def call(provider, method, url, headers, params=None, body=None):
        calls.append(url)
        assert headers["x-api-key"] == KEY
        if url.endswith("/organizations/enrich"):
            return org
        if url.endswith("/mixed_people/organization_top_people"):
            return people
        raise AssertionError(url)
    return call


class SignalsTest(TrackerCase):
    def test_people_named_in_the_jd(self):
        found = signals.jd_people(JD, "Acme")
        jane = next(p for p in found if p["name"] == "Jane Doe")
        self.assertEqual((jane["email"], jane["source"]), ("jane.doe@acme.ai", "jd"))
        self.assertIn("recruit", jane["title"].lower())
        self.assertTrue(any(p["title"] == "Head of Machine Learning" and not p["name"] for p in found))
        inbox = next(p for p in found if p["email"] == "careers@acme.ai")
        self.assertEqual(inbox["name"], "")                                      # a team inbox, not a person
        self.assertFalse(any("Equal" in p["name"] for p in found))

    def test_functional_inboxes_and_the_company_are_not_people(self):
        text = ("Need help? Disability_Accommodation@acme.com, candidate.accommodations@hrhelp.acme.com, "
                "talent.acquisition@acme.com, fraud.report@acme.com, careers.technicalsupport@acme.com. "
                "Hiring Manager: Acme Entertainment Co. Recruiter: Matthew Brady. Contact erika.santucci@acme.com.")
        found = signals.jd_people(text, "Acme Entertainment Co.")
        self.assertEqual(sorted(p["name"] for p in found if p["name"]), ["Erika Santucci", "Matthew Brady"])
        self.assertEqual(sum(1 for p in found if p["title"].startswith("team inbox")), 5)

    def test_company_domain_from_the_jd_else_a_labelled_guess(self):
        self.assertEqual(signals.domain_guess("Acme", [JD], ["https://boards.greenhouse.io/acme/jobs/1"]),
                         ("acme.ai", "link in the JD"))
        self.assertEqual(signals.domain_guess("Globex Corp", ["no links"], []),
                         ("globex.com", signals.GUESSED_DOMAIN))

    def test_linkedin_people_search_links_only(self):
        links = signals.linkedin_searches("Acme", "ml_engineer", "Machine Learning")
        self.assertEqual(len(links), 4)
        for link in links:
            self.assertTrue(link["url"].startswith("https://www.linkedin.com/search/results/people/?keywords=Acme"))
        self.assertIn("talent acquisition", " ".join(link["label"] for link in links))

    def test_open_in_apollo_link(self):
        url = signals.apollo_web_url("acme.ai")
        self.assertTrue(url.startswith("https://app.apollo.io/#/people?"))
        self.assertIn("acme.ai", url)
        self.assertIn("recruiter", url)

    def test_email_guess(self):
        self.assertEqual(signals.guess_email("Jane Doe", "acme.ai"), "jane.doe@acme.ai")
        self.assertEqual(signals.guess_email("Jane Q. Doe", "acme.ai", "{f}{last}"), "jdoe@acme.ai")
        self.assertIsNone(signals.guess_email("Jane", "acme.ai"))

    def test_team_page_people(self):
        html = ("<html><body><h3>Jane Doe</h3><p>Chief Technology Officer</p><h3>Bob Smith</h3><p>Office Manager</p>"
                "<div><span>Raj Patel</span><span>VP, Data &amp; AI</span></div></body></html>")
        found = {(p["name"], p["title"]) for p in signals.team_page_people(html)}
        self.assertEqual(found, {("Jane Doe", "Chief Technology Officer"), ("Raj Patel", "VP, Data & AI")})


class RankTest(TrackerCase):
    def test_role_why_and_order(self):
        job = {"role_title": "ML Engineer", "location": "Austin, TX", "family": "ml_engineer"}
        people = [{"name": "Ana Ruiz", "title": "Chief Executive Officer", "source": "apollo"},
                  {"name": "Sam Lee", "title": "Head of Machine Learning", "source": "apollo", "city": "Austin"},
                  {"name": "Jane Doe", "title": "Recruiter", "source": "jd"}]
        ranked = rank.rank(people, job)
        self.assertEqual([p["name"] for p in ranked][:2], ["Jane Doe", "Sam Lee"])
        sam = next(p for p in ranked if p["name"] == "Sam Lee")
        self.assertEqual(sam["role"], "hiring manager")
        self.assertTrue(any("same team" in w for w in sam["why"]))
        self.assertTrue(any("Austin" in w for w in sam["why"]))
        self.assertIn(sam["confidence"], ("high", "medium", "low"))

    def test_a_non_tech_director_is_not_a_likely_hiring_manager(self):
        self.assertEqual(rank.likely_role("Director of Partnerships"), "other")
        self.assertEqual(rank.likely_role("Director, Data Engineering"), "hiring manager")
        self.assertEqual(rank.likely_role("VP of Engineering"), "leader")


class ApolloLookupTest(TrackerCase):
    def setUp(self):
        super().setUp()
        self.row = self.add(company="Acme", role_title="ML Engineer", jd=JD, status="applied")
        self.keys = mock.patch.object(service.keys, "key", side_effect=lambda provider, pid: KEY
                                      if provider == "apollo" else None)
        self.keys.start()

    def tearDown(self):
        self.keys.stop()
        super().tearDown()

    def lookup(self, calls, **kw):
        with mock.patch.object(http, "call", side_effect=fake_apollo(calls, **kw)):
            return service.lookup(self.conn, "alex", self.row["id"], "apollo", "acme.ai")

    def test_panel_is_free_and_shows_the_cost_before(self):
        with mock.patch.object(http, "call", side_effect=AssertionError("no paid call")):
            panel = service.panel(self.conn, "alex", self.row["id"])
        self.assertEqual(panel["domain"], {"value": "acme.ai", "source": "link in the JD"})
        self.assertTrue(any(s["name"] == "Jane Doe" for s in panel["suggestions"]))
        self.assertEqual(panel["providers"]["apollo"]["estimate"]["credits"], 2)
        self.assertEqual(panel["providers"]["apollo"]["left_today"], ledger.DAILY_CAP)
        self.assertFalse(panel["providers"]["hunter"]["configured"])
        self.assertEqual(len(panel["links"]["linkedin"]), 4)
        jane = next(s for s in panel["suggestions"] if s["name"] == "Jane Doe")
        self.assertEqual(jane["email_status"], "named in the JD")

    def test_an_empty_top_people_answer_is_explained_now_and_on_every_reopen(self):
        out = self.lookup([], people={"people": [], "organization": {"top_people_total": 0}})
        self.assertIn("lists no top people", out["note"])
        with mock.patch.object(http, "call", side_effect=AssertionError("no paid call")):
            again = service.panel(self.conn, "alex", self.row["id"])
        self.assertIn("lists no top people", again["providers"]["apollo"]["estimate"]["detail"])
        self.assertEqual(again["providers"]["apollo"]["estimate"]["credits"], 0)

    def test_lookup_ranks_people_never_keeps_phones_and_is_cached(self):
        calls = []
        out = self.lookup(calls)
        self.assertEqual(len(calls), 2)
        self.assertEqual(out["spent"], 2)
        names = [s["name"] for s in out["panel"]["suggestions"]]
        self.assertIn("Sam Lee", names)
        sam = next(s for s in out["panel"]["suggestions"] if s["name"] == "Sam Lee")
        self.assertEqual((sam["source"], sam["linkedin"]), ("apollo", "https://www.linkedin.com/in/samlee"))
        self.assertEqual(sam["email_guess"], "sam.lee@acme.ai")
        self.assertEqual(sam["email_guess_status"], signals.GUESS_LABEL)
        stored = json.dumps([dict(r) for r in self.conn.execute("SELECT * FROM contact_lookups")])
        self.assertNotIn("555", stored)                                            # no phone numbers, ever
        self.assertNotIn(KEY, stored)                                              # the key is never stored
        self.assertNotIn("555", json.dumps(out))
        again = []
        out2 = self.lookup(again)
        self.assertEqual((again, out2["spent"]), ([], 0))                          # same lookup never spends twice
        self.assertEqual(out2["panel"]["providers"]["apollo"]["used_today"], 2)

    def test_a_large_company_skips_top_people(self):
        calls = []
        big = {"organization": {**ORG["organization"], "estimated_num_employees": 9000}}
        out = self.lookup(calls, org=big)
        self.assertEqual((len(calls), out["spent"]), (1, 1))
        self.assertIn("9000", out["note"])

    def test_the_daily_cap_stops_before_any_call(self):
        calls = []
        with mock.patch.object(ledger, "DAILY_CAP", 1):
            with self.assertRaisesRegex(ValueError, "daily"):
                self.lookup(calls)
        self.assertEqual(calls, [])

    def test_no_key_no_call(self):
        with mock.patch.object(service.keys, "key", return_value=None), \
                mock.patch.object(http, "call", side_effect=AssertionError("no call")):
            with self.assertRaisesRegex(ValueError, "Apollo key"):
                service.lookup(self.conn, "alex", self.row["id"], "apollo", "acme.ai")

    def test_a_bad_domain_is_refused(self):
        with self.assertRaisesRegex(ValueError, "domain"):
            service.lookup(self.conn, "alex", self.row["id"], "apollo", "not a domain/../x")

    def test_person_cleaning_drops_phones_and_locked_emails(self):
        person = apollo.person(PEOPLE["people"][0])
        self.assertNotIn("phone_numbers", person)
        self.assertIsNone(person["email"])
        self.assertEqual(person["name"], "Sam Lee")


class AddContactTest(TrackerCase):
    def test_add_contact_saves_with_source_and_marks_the_sheet(self):
        row = self.add(company="Acme", status="applied")
        with mock.patch.object(gstate, "mark_dirty", wraps=gstate.mark_dirty) as dirty:
            out = service.add(self.conn, "alex", row["id"], {"name": "Jane Doe", "role": "Recruiter",
                              "email": "jane.doe@acme.ai", "linkedin": "", "source": "jd", "notes": "named in JD"})
        self.assertTrue(dirty.called)
        self.assertEqual(out["target_name"], "Jane Doe")
        self.assertEqual(out["email_address"], "jane.doe@acme.ai")
        self.assertEqual(out["contacts"][0]["source"], "jd")
        out = service.add(self.conn, "alex", row["id"], {"name": "Sam Lee", "role": "Head of ML", "source": "apollo"})
        self.assertEqual(out["target_name"], "Jane Doe, Sam Lee")
        with self.assertRaisesRegex(ValueError, "source"):
            service.add(self.conn, "alex", row["id"], {"name": "X Y", "source": "scraped"})


class CreditSafetyTest(TrackerCase):
    def test_the_scheduled_fetch_never_reaches_a_paid_provider(self):
        source = Path(fetch.__file__).read_text()
        self.assertNotIn("finder", source)
        self.assertNotIn("apollo", source.lower())
        self.assertNotIn("hunter", source.lower())
        import subprocess
        import sys
        probe = ("import sys; import job_hunt_agent.fetch; "
                 "print(sorted(m for m in sys.modules if m.startswith('job_hunt_agent.finder')))")
        out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                             cwd=Path(__file__).parents[1], check=True)
        self.assertEqual(out.stdout.strip(), "[]")       # nothing fetch imports, directly or not, can reach Hunter/Apollo

    def test_hunter_is_off_without_a_key(self):
        row = self.add(company="Acme", status="applied")
        with mock.patch.object(service.keys, "key", return_value=None):
            with self.assertRaisesRegex(ValueError, "Hunter key"):
                service.lookup(self.conn, "alex", row["id"], "hunter", "acme.ai")

    def test_routes(self):
        row = self.add(company="Acme", role_title="ML Engineer", jd=JD, status="applied")
        now = datetime.now(timezone.utc)
        with mock.patch.object(service.keys, "key", return_value=None):
            panel = server.route(self.conn, "GET", "/api/contacts/panel", {"profile": "alex", "app": str(row["id"])},
                                 now)
        self.assertIn("suggestions", panel)
        out = server.route(self.conn, "POST", "/api/contacts/add", {"profile": "alex", "app": row["id"],
                           "contact": {"name": "Jane Doe", "source": "jd"}}, now)
        self.assertEqual(out["target_name"], "Jane Doe")
        self.assertEqual(trackapi.get(self.conn, "alex", row["id"], TODAY)["contacts"][0]["name"], "Jane Doe")


class ReviewFixesTest(TrackerCase):
    """Code review 2026-10-06: private hosts are not company domains; two clicks never spend twice."""
    def test_private_and_local_hosts_are_refused(self):
        for bad in ("printer.local", "intranet.corp", "x.internal", "box.home.arpa"):
            with self.assertRaisesRegex(ValueError, "not a company domain"):
                service.clean_domain(bad)
        self.assertEqual(service.clean_domain("https://www.Acme.com/careers"), "acme.com")

    def test_lookups_run_one_at_a_time(self):
        self.assertFalse(service._LOOKUP_LOCK.locked())
        seen = []
        with mock.patch.dict(service.LOOKUPS, {"team_page": lambda *a: (seen.append(service._LOOKUP_LOCK.locked()),
                                                                       (0.0, None))[1]}):
            row = self.add(job_url="https://boards.greenhouse.io/acme/jobs/1")
            service.lookup(self.conn, "alex", row["id"], "team_page", "acme.com")
        self.assertEqual(seen, [True])


class TopPeopleLayoutTest(TrackerCase):
    """organization_top_people is undocumented: people are found under any list key, and the layout is kept."""
    def test_people_under_another_key_one_level_down(self):
        got = {"result": {"people_list": [{"first_name": "Ana", "last_name": "Ruiz", "title": "CTO",
                                           "phone_numbers": [{"raw_number": "+1 555"}]}]}, "pagination": {"page": 1}}
        with mock.patch.object(apollo.http, "call", return_value=got):
            people, layout = apollo.top_people(KEY, "org1")
        self.assertEqual([(p["name"], p["title"]) for p in people], [("Ana Ruiz", "CTO")])
        self.assertNotIn("phone_numbers", people[0])
        self.assertEqual(layout, {"result": {"people_list": "list[1]"}, "pagination": {"page": 1}})

    def test_an_empty_answer_keeps_its_layout(self):
        with mock.patch.object(apollo.http, "call", return_value={"breadcrumbs": [], "people": []}):
            self.assertEqual(apollo.top_people(KEY, "org1"), ([], {"breadcrumbs": "list[0]", "people": "list[0]"}))
