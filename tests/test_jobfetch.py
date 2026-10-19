"""jobfetch: posting URL -> uid the fetcher stores, and one-request auto-fill from the ATS's public data (offline)."""
import json
import sqlite3
import unittest
from pathlib import Path
from unittest import mock

from job_hunt_agent import jobfetch
from job_hunt_agent.net import ROBOTS_BLOCKED, HttpError

FIXTURES = Path(__file__).parent / "fixtures" / "jobfetch"

# (company, url, uid) copied from data/job_hunt_agent.db rows written by the fetcher
REAL_ROWS = {
    "greenhouse": [
        ("Cloudflare", "https://boards.greenhouse.io/cloudflare/jobs/8171942?gh_jid=8171942",
         "greenhouse:cloudflare:8171942"),
        ("BillionToOne", "https://job-boards.greenhouse.io/billiontoone/jobs/4707243005",
         "greenhouse:billiontoone:4707243005"),
    ],
    "lever": [
        ("Shield AI", "https://jobs.lever.co/shieldai/06a65b0d-2490-420a-8240-15987beec16f",
         "lever:shieldai:06a65b0d-2490-420a-8240-15987beec16f"),
        ("Zoox", "https://jobs.lever.co/zoox/edd3a8ae-bc78-49ac-a536-2033d7c02dd6",
         "lever:zoox:edd3a8ae-bc78-49ac-a536-2033d7c02dd6"),
    ],
    "ashby": [
        ("Tabs", "https://jobs.ashbyhq.com/tabs/fabadd5c-4b15-4da3-9d58-c022261a27db",
         "ashby:tabs:fabadd5c-4b15-4da3-9d58-c022261a27db"),
        ("Helion", "https://jobs.ashbyhq.com/helion/1e2ff97c-9a78-4f90-99f5-89cfcc9e9eba",
         "ashby:helion:1e2ff97c-9a78-4f90-99f5-89cfcc9e9eba"),
    ],
    "workday": [
        ("US01 VALEO", "https://valeo.wd3.myworkdayjobs.com/Valeo_jobs/job/Cairo/"
         "Senior-Salesforce-Developer--Valeo-Service_REQ2026081849", "workday:valeo/wd3/Valeo_jobs:REQ2026081849"),
        ("Bank of America", "https://ghr.wd1.myworkdayjobs.com/Lateral-US/job/Charlotte/"
         "Principal-Engineer---RAG-Database---Embeddings-Architect_26020184", "workday:ghr/wd1/Lateral-US:26020184"),
    ],
    "smartrecruiters": [
        ("ServiceNow", "https://jobs.smartrecruiters.com/ServiceNow/744000147349629",
         "smartrecruiters:ServiceNow:744000147349629"),
        ("ServiceNow", "https://jobs.smartrecruiters.com/ServiceNow/744000150599979",
         "smartrecruiters:ServiceNow:744000150599979"),
    ],
}

GH_URL = "https://job-boards.greenhouse.io/formationbio/jobs/6623947"
LEVER_URL = "https://jobs.lever.co/mashgin/da733114-d2b5-4abf-92e3-c034ce970a86/apply"
ASHBY_URL = "https://jobs.ashbyhq.com/mento/b583bb71-2f09-4ecb-a0a8-f15ab648da79/application"
WD_URL = ("https://o9solutions.wd5.myworkdayjobs.com/en-US/o9SolutionsExternal/job/Bangalore-Karnataka-India/"
          "Senior-Data-Scientist---Demand-Forecasting_JR102128/apply")
SR_URL = "https://jobs.smartrecruiters.com/ServiceNow/744000094537795-enterprise-account-executive"


def _json(name: str):
    return json.loads((FIXTURES / name).read_text())


def _uid(url: str, resolve=None) -> str | None:
    ref = jobfetch.parse_url(url, resolve)
    return ref["uid"] if ref else None


class ParseRealRowsTest(unittest.TestCase):
    def _check(self, ats: str) -> None:
        for company, url, uid in REAL_ROWS[ats]:
            with self.subTest(company=company):
                self.assertEqual(_uid(url), uid)
                self.assertEqual(jobfetch.parse_url(url)["ats"], ats)

    def test_greenhouse_rows(self):
        self._check("greenhouse")

    def test_lever_rows(self):
        self._check("lever")

    def test_ashby_rows(self):
        self._check("ashby")

    def test_workday_rows(self):
        self._check("workday")

    def test_smartrecruiters_rows(self):
        self._check("smartrecruiters")


class ParseShapesTest(unittest.TestCase):
    def test_greenhouse_shapes(self):
        cases = {
            "https://boards.greenhouse.io/acme/jobs/123#app": "greenhouse:acme:123",
            "https://job-boards.greenhouse.io/acme/jobs/123?gh_src=x": "greenhouse:acme:123",
            "https://job-boards.eu.greenhouse.io/jetbrains/jobs/4932739101": "greenhouse:jetbrains:4932739101",
            "https://boards.greenhouse.io/embed/job_app?for=gemini&token=8089812&gh_jid=8089812":
                "greenhouse:gemini:8089812",
            "https://job-boards.greenhouse.io/embed/job_app?for=acme&token=55": "greenhouse:acme:55",
            "https://boards.greenhouse.io/acme?gh_jid=77": "greenhouse:acme:77",
            "job-boards.greenhouse.io/acme/jobs/123": "greenhouse:acme:123",
            # a company site that names its board in the query (real DB row)
            "https://coreweave.com/careers/job?4714302006&board=coreweave&gh_jid=4714302006":
                "greenhouse:coreweave:4714302006",
        }
        for url, uid in cases.items():
            with self.subTest(url=url):
                self.assertEqual(_uid(url), uid)

    def test_greenhouse_company_site_without_token_is_none(self):
        self.assertIsNone(_uid("https://www.revmed.com/careers-list/?gh_jid=7720354003"))
        self.assertIsNone(_uid("https://example.com/careers?gh_jid=not-a-number&board=acme"))

    def test_greenhouse_company_site_with_resolver(self):
        calls = []

        def resolve(ats, hint, posting_id):
            calls.append((ats, hint, posting_id))
            return "revolutionmedicines"
        self.assertEqual(_uid("https://www.revmed.com/careers-list/?gh_jid=7720354003", resolve),
                         "greenhouse:revolutionmedicines:7720354003")
        self.assertEqual(calls, [("greenhouse", "www.revmed.com", "7720354003")])

    def test_lever_shapes(self):
        uuid = "06a65b0d-2490-420a-8240-15987beec16f"
        for url in (f"https://jobs.lever.co/ShieldAI/{uuid}", f"https://jobs.lever.co/shieldai/{uuid}/apply",
                    f"https://jobs.lever.co/shieldai/{uuid.upper()}?lever-source=x"):
            with self.subTest(url=url):
                self.assertEqual(_uid(url), f"lever:shieldai:{uuid}")
        eu = jobfetch.parse_url(f"https://jobs.eu.lever.co/acme/{uuid}")
        self.assertEqual((eu["uid"], eu["api"]), (f"lever:acme:{uuid}", "https://api.eu.lever.co"))

    def test_ashby_shapes(self):
        uuid = "fabadd5c-4b15-4da3-9d58-c022261a27db"
        self.assertEqual(_uid(f"https://jobs.ashbyhq.com/tabs/{uuid}/application?utm_source=x"),
                         f"ashby:tabs:{uuid}")
        self.assertEqual(_uid(f"https://jobs.ashbyhq.com/OpenAI/{uuid}"), f"ashby:OpenAI:{uuid}")
        self.assertIsNone(_uid(f"https://www.tabs.com/careers?ashby_jid={uuid}"))
        self.assertEqual(_uid(f"https://www.tabs.com/careers?ashby_jid={uuid}", lambda a, h, i: "tabs"),
                         f"ashby:tabs:{uuid}")

    def test_workday_shapes(self):
        base = "https://travelers.wd5.myworkdayjobs.com"
        path = "/job/CT---Hartford/Product-Management-Development-Program--PMDP--Intern_R-52317"
        uid = "workday:travelers/wd5/External:R-52317"
        for url in (f"{base}/External{path}", f"{base}/en-US/External{path}", f"{base}/External{path}/apply",
                    f"{base}/en-US/External{path}/apply/applyManually?source=x"):
            with self.subTest(url=url):
                ref = jobfetch.parse_url(url)
                self.assertEqual(ref["uid"], uid)
                self.assertEqual(ref["job_path"], path)

    def test_workday_myworkdaysite(self):
        url = ("https://wd1.myworkdaysite.com/recruiting/wf/WellsFargoJobs/job/SAN-FRANCISCO-CA/"
               "Senior-Data-Engineer_R-550968")
        self.assertEqual(_uid(url), "workday:wf/wd1/WellsFargoJobs:R-550968")
        url = ("https://wd1.myworkdaysite.com/en-US/recruiting/fmr/FidelityCareers/job/Salt-Lake-City-UT/"
               "Data-Engineer--Analytics---Performance-Insights_2135554")
        self.assertEqual(_uid(url), "workday:fmr/wd1/FidelityCareers:2135554")

    def test_workday_suffix_the_fetcher_pattern_misses(self):
        # the fetcher stores bulletFields[0] (the req id) there; Workday adds '-1' to a repeated slug (real DB rows)
        url = ("https://ms.wd5.myworkdayjobs.com/External/job/New-York-New-York-United-States-of-America/"
               "Generative-AI---Machine-Learning-Engineer_PT-JR041494-1")
        self.assertEqual(_uid(url), "workday:ms/wd5/External:PT-JR041494")
        url = ("https://ms.wd5.myworkdayjobs.com/External/job/New-York-New-York-United-States-of-America/"
               "Sr-AI-Engineer_PT-JR041902")
        self.assertEqual(_uid(url), "workday:ms/wd5/External:PT-JR041902")
        self.assertEqual(jobfetch.workday_ext_id("/job/Chicago/Senior-Specialist--HRIS_"),
                         "/job/Chicago/Senior-Specialist--HRIS_")

    def test_workday_key_spelling_from_resolver(self):
        url = ("https://cat.wd5.myworkdayjobs.com/CaterpillarCareers/job/Chennai-Tamil-Nadu/"
               "Senior-Software-Engineer---Data-Engineering_R0000386238")
        self.assertEqual(_uid(url), "workday:cat/wd5/CaterpillarCareers:R0000386238")
        self.assertEqual(_uid(url, lambda ats, hint, i: "cat/wd5/caterpillarcareers"),
                         "workday:cat/wd5/caterpillarcareers:R0000386238")

    def test_smartrecruiters_shapes(self):
        for url in ("https://jobs.smartrecruiters.com/ServiceNow/744000147349629-senior-engineer",
                    "https://jobs.smartrecruiters.com/ServiceNow/744000147349629?trid=x",
                    "https://careers.smartrecruiters.com/ServiceNow/744000147349629"):
            with self.subTest(url=url):
                self.assertEqual(_uid(url), "smartrecruiters:ServiceNow:744000147349629")

    def test_other_urls_are_none(self):
        for url in ("", "   ", "not a url", "https://www.linkedin.com/jobs/view/4012345678",
                    "https://www.indeed.com/viewjob?jk=abc", "https://job-boards.greenhouse.io/acme",
                    "https://boards.greenhouse.io/embed/job_board?for=acme", "https://jobs.lever.co/acme",
                    "https://jobs.lever.co/acme/not-a-uuid", "https://jobs.ashbyhq.com/acme",
                    "https://travelers.wd5.myworkdayjobs.com/External", "https://jobs.smartrecruiters.com/ServiceNow",
                    "https://jobs.smartrecruiters.com/ServiceNow/senior-engineer",
                    "https://example.myworkdayjobs.com.evil.com/External/job/X/Y_R1"):
            with self.subTest(url=url):
                self.assertIsNone(jobfetch.parse_url(url))
                self.assertIsNone(jobfetch.fetch_posting(url))


class DbResolverTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript("""
            CREATE TABLE jobs (board TEXT, ext_id TEXT, url TEXT);
            CREATE TABLE boards (board TEXT, ats TEXT, key TEXT);
            INSERT INTO jobs VALUES ('greenhouse:revolutionmedicines', '7720354003',
                                     'https://www.revmed.com/careers-list/?gh_jid=7720354003');
            INSERT INTO jobs VALUES ('greenhouse:stripe', '111', 'https://stripe.com/jobs/search?gh_jid=111');
            INSERT INTO jobs VALUES ('greenhouse:stripe', '112', 'https://stripe.com/jobs/search?gh_jid=112');
            INSERT INTO jobs VALUES ('greenhouse:a', '1', 'https://shared.example.com/?gh_jid=1');
            INSERT INTO jobs VALUES ('greenhouse:b', '2', 'https://shared.example.com/?gh_jid=2');
            INSERT INTO jobs VALUES ('ashby:tabs', 'fabadd5c-4b15-4da3-9d58-c022261a27db', 'x');
            INSERT INTO boards VALUES ('workday:cat/wd5/caterpillarcareers', 'workday', 'cat/wd5/caterpillarcareers');
        """)
        self.resolve = jobfetch.db_resolver(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_known_posting_id(self):
        self.assertEqual(_uid("https://www.revmed.com/careers-list/?gh_jid=7720354003", self.resolve),
                         "greenhouse:revolutionmedicines:7720354003")
        self.assertEqual(_uid("https://www.tabs.com/careers?ashby_jid=fabadd5c-4b15-4da3-9d58-c022261a27db",
                              self.resolve), "ashby:tabs:fabadd5c-4b15-4da3-9d58-c022261a27db")

    def test_new_posting_on_a_known_career_host(self):
        self.assertEqual(_uid("https://stripe.com/jobs/search?gh_jid=999", self.resolve), "greenhouse:stripe:999")

    def test_ambiguous_or_unknown_host_is_none(self):
        self.assertIsNone(_uid("https://shared.example.com/?gh_jid=3", self.resolve))
        self.assertIsNone(_uid("https://unknown.example.com/?gh_jid=3", self.resolve))

    def test_workday_stored_spelling(self):
        url = "https://cat.wd5.myworkdayjobs.com/CaterpillarCareers/job/Chennai/Senior-Engineer_R0000386238"
        self.assertEqual(_uid(url, self.resolve), "workday:cat/wd5/caterpillarcareers:R0000386238")
        url = "https://valeo.wd3.myworkdayjobs.com/Valeo_jobs/job/Cairo/Dev_REQ1"
        self.assertEqual(_uid(url, self.resolve), "workday:valeo/wd3/Valeo_jobs:REQ1")


class FetchPostingTest(unittest.TestCase):
    def _fetch_json(self, url: str, payload):
        with mock.patch.object(jobfetch, "get_json", return_value=payload) as get_json:
            job = jobfetch.fetch_posting(url)
        return job, [c.args[0] for c in get_json.call_args_list]

    def test_greenhouse_fields(self):
        job, calls = self._fetch_json(GH_URL, _json("greenhouse_job.json"))
        self.assertEqual(calls, ["https://boards-api.greenhouse.io/v1/boards/formationbio/jobs/6623947"])
        self.assertEqual(set(job), set(jobfetch.FIELDS))
        self.assertEqual(job["uid"], "greenhouse:formationbio:6623947")
        self.assertEqual(job["company"], "Formation Bio")
        self.assertEqual(job["title"], "Senior Data Scientist")
        self.assertEqual(job["location"], "New York, NY; Boston, MA")
        self.assertEqual(job["posted_at"], "2025-02-12T21:05:12Z")
        self.assertEqual(job["url"], GH_URL)
        self.assertTrue(job["description"].startswith("About Formation Bio\n\nFormation Bio is"))
        self.assertNotIn("<", job["description"])
        self.assertNotIn("&lt;", job["description"])

    def test_lever_fields(self):
        job, calls = self._fetch_json(LEVER_URL, _json("lever_posting.json"))
        self.assertEqual(calls, ["https://api.lever.co/v0/postings/mashgin/da733114-d2b5-4abf-92e3-c034ce970a86"])
        self.assertEqual(job["uid"], "lever:mashgin:da733114-d2b5-4abf-92e3-c034ce970a86")
        self.assertEqual(job["company"], "Mashgin")
        self.assertEqual(job["title"], "Senior Software Engineer, Machine Learning and Artificial Intelligence")
        self.assertEqual((job["location"], job["workplace"]), ("Palo Alto, CA", "onsite"))
        self.assertEqual((job["salary_min"], job["salary_max"], job["salary_ccy"]), (200000.0, 300000.0, "USD"))
        self.assertEqual(job["employment_type"], "full_time")
        self.assertEqual(job["apply_url"], "https://jobs.lever.co/mashgin/da733114-d2b5-4abf-92e3-c034ce970a86/apply")
        self.assertEqual(job["posted_at"], "2023-01-02T16:58:28Z")
        self.assertIn("Mashgin powers", job["description"])

    def test_ashby_fields(self):
        job, calls = self._fetch_json(ASHBY_URL, _json("ashby_board.json"))
        self.assertEqual(calls, ["https://api.ashbyhq.com/posting-api/job-board/mento?includeCompensation=true"])
        self.assertEqual(job["uid"], "ashby:mento:b583bb71-2f09-4ecb-a0a8-f15ab648da79")
        self.assertEqual(job["title"], "Data Scientist Coach (Director+)")
        self.assertEqual((job["location"], job["workplace"]), ("Remote - US", "remote"))
        self.assertEqual(job["employment_type"], "contract")
        self.assertEqual(job["posted_at"], "2024-12-09T15:28:05Z")
        self.assertTrue(job["apply_url"].endswith("/application"))
        self.assertTrue(job["description"].startswith("ABOUT THE ROLE"))

    def test_ashby_id_not_listed(self):
        url = "https://jobs.ashbyhq.com/mento/00000000-0000-4000-8000-000000000000"
        with mock.patch.object(jobfetch, "get_json", return_value=_json("ashby_board.json")):
            with self.assertRaisesRegex(ValueError, "no longer listed"):
                jobfetch.fetch_posting(url)

    def test_ashby_unexpected_shape(self):
        with mock.patch.object(jobfetch, "get_json", return_value={"postings": []}):
            with self.assertRaisesRegex(ValueError, "unexpected format"):
                jobfetch.fetch_posting(ASHBY_URL)

    def test_workday_fields(self):
        job, calls = self._fetch_json(WD_URL, _json("workday_job.json"))
        self.assertEqual(calls, ["https://o9solutions.wd5.myworkdayjobs.com/wday/cxs/o9solutions/o9SolutionsExternal"
                                 "/job/Bangalore-Karnataka-India/Senior-Data-Scientist---Demand-Forecasting_JR102128"])
        self.assertEqual(job["uid"], "workday:o9solutions/wd5/o9SolutionsExternal:JR102128")
        self.assertEqual(job["company"], "o9 Solutions Management India Private Limited")
        self.assertEqual(job["title"], "Senior/Lead Data Scientist - Demand Forecasting")
        self.assertEqual(job["location"], "Bangalore, Karnataka, India")
        self.assertEqual(job["employment_type"], "full_time")
        self.assertEqual(job["posted_at"], "2026-09-05T00:00:00Z")
        self.assertEqual(job["url"], "https://o9solutions.wd5.myworkdayjobs.com/o9SolutionsExternal/job/"
                                     "Bangalore-Karnataka-India/Senior-Data-Scientist---Demand-Forecasting_JR102128")
        self.assertTrue(job["description"].startswith("Transforming the Future of Enterprise Planning"))

    def test_workday_exact_uid_from_the_requisition_id(self):
        payload = _json("workday_job.json")
        info = {**payload["jobPostingInfo"], "jobReqId": "PT-JR041494"}
        url = "https://ms.wd5.myworkdayjobs.com/External/job/New-York/Generative-AI-Engineer_PT-JR041494-1"
        job, _ = self._fetch_json(url, {**payload, "jobPostingInfo": info})
        self.assertEqual(job["uid"], "workday:ms/wd5/External:PT-JR041494")

    def test_workday_without_posting_info_is_closed(self):
        with mock.patch.object(jobfetch, "get_json", return_value={"error": "not found"}):
            with self.assertRaisesRegex(ValueError, "closed"):
                jobfetch.fetch_posting(WD_URL)

    def test_company_falls_back_to_the_board_key(self):
        payload = {k: v for k, v in _json("greenhouse_job.json").items() if k != "company_name"}
        job, _ = self._fetch_json("https://job-boards.greenhouse.io/formation-bio/jobs/6623947", payload)
        self.assertEqual(job["company"], "Formation Bio")
        payload = {k: v for k, v in _json("workday_job.json").items() if k != "hiringOrganization"}
        job, _ = self._fetch_json(WD_URL, payload)
        self.assertEqual(job["company"], "O9Solutions")

    def test_closed_posting_raises(self):
        for url in (GH_URL, LEVER_URL, WD_URL):
            for status in (404, 410):
                with self.subTest(url=url, status=status):
                    err = HttpError(url, status, "Not Found")
                    with mock.patch.object(jobfetch, "get_json", side_effect=err):
                        with self.assertRaisesRegex(ValueError, f"closed.*{status}"):
                            jobfetch.fetch_posting(url)

    def test_unreachable_raises(self):
        for err in (HttpError(GH_URL, None, "timed out"), HttpError(GH_URL, 503, "unavailable")):
            with self.subTest(status=err.status):
                with mock.patch.object(jobfetch, "get_json", side_effect=err):
                    with self.assertRaisesRegex(ValueError, "Could not reach Greenhouse"):
                        jobfetch.fetch_posting(GH_URL)

    def test_malformed_payload_raises(self):
        with mock.patch.object(jobfetch, "get_json", return_value={"id": 1}):
            with self.assertRaisesRegex(ValueError, "unexpected format"):
                jobfetch.fetch_posting(LEVER_URL)


class SmartRecruitersTest(unittest.TestCase):
    PAGE = (FIXTURES / "smartrecruiters_page.html").read_text()

    def test_page_fields(self):
        with mock.patch.object(jobfetch, "get_text", return_value=self.PAGE) as get_text, \
                mock.patch.object(jobfetch, "get_json") as get_json:
            job = jobfetch.fetch_posting(SR_URL)
        get_text.assert_called_once_with("https://jobs.smartrecruiters.com/ServiceNow/744000094537795")
        get_json.assert_not_called()                     # api.smartrecruiters.com: robots.txt Disallow: /
        self.assertEqual(job["uid"], "smartrecruiters:ServiceNow:744000094537795")
        self.assertEqual(job["company"], "ServiceNow")
        self.assertEqual(job["title"], "Enterprise Account Executive - Manufacturing(Process)")
        self.assertEqual((job["location"], job["workplace"]), ("Tokyo, Japan", "onsite"))
        self.assertEqual(job["employment_type"], "full_time")
        self.assertEqual(job["posted_at"], "2025-11-20T04:43:31Z")
        self.assertTrue(job["description"].startswith("Company Description\n\nIt all started"))
        self.assertIn("Job Description", job["description"])
        self.assertNotIn("not this", job["description"] + job["title"])

    def test_robots_blocked_returns_ids_only(self):
        err = HttpError(SR_URL, ROBOTS_BLOCKED, "robots.txt disallows this URL", robots=True)
        with mock.patch.object(jobfetch, "get_text", side_effect=err):
            job = jobfetch.fetch_posting(SR_URL)
        self.assertEqual(job, {"uid": "smartrecruiters:ServiceNow:744000094537795", "ats": "smartrecruiters",
                               "key": "ServiceNow", "ext_id": "744000094537795",
                               "url": "https://jobs.smartrecruiters.com/ServiceNow/744000094537795",
                               "blocked": "robots"})

    def test_page_without_job_posting_is_closed(self):
        page = "<html><head><title>Jobs at ServiceNow</title></head><body><h1>Open roles</h1></body></html>"
        with mock.patch.object(jobfetch, "get_text", return_value=page):
            with self.assertRaisesRegex(ValueError, "closed"):
                jobfetch.fetch_posting(SR_URL)

    def test_page_not_found_is_closed(self):
        with mock.patch.object(jobfetch, "get_text", side_effect=HttpError(SR_URL, 404, "Not Found")):
            with self.assertRaisesRegex(ValueError, "closed"):
                jobfetch.fetch_posting(SR_URL)


if __name__ == "__main__":
    unittest.main()
