"""GOAL16 Tier A adapters on responses recorded live on 2026-10-08 (trimmed): Gem (Bilt), Rippling (Anthony & Sylvan
Pools) and BambooHR (Day Translations). Each board is one list request (a full list: an absent job is a miss); a
title-matched new posting costs one detail request where the list has no description (Rippling, BambooHR)."""
import json
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

from job_hunt_agent import discover_cc, discover_more, fetch, net, registry, store, tiers
from job_hunt_agent.ats import (ADAPTERS, Ctx, adp, bamboohr, cornerstone, dayforce, detect, gem, ibm, jazzhr, paylocity,
                         rippling)
from job_hunt_agent.ats.common import SCHEMA_ERROR, raw_fp
from job_hunt_agent.net import HttpError

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 8, 20, 0, tzinfo=timezone.utc)
WORDS = ("ai ", "machine learning", "data", "localization", "inventory", "events manager", "treasury")
MATCH = lambda title: any(w in f"{title.lower()} " for w in WORDS)  # noqa: E731
CTX = Ctx(matches=MATCH, today=date(2026, 10, 8))


def load(path: str):
    return json.loads((FIXTURES / path).read_text())


def text(path: str) -> str:
    return (FIXTURES / path).read_text()


def board(ats: str, key: str, company: str = "Acme") -> dict:
    return {"board": f"{ats}:{key}", "ats": ats, "key": key, "company": company}


class GemTest(unittest.TestCase):
    def _fetch(self, ctx=CTX):
        calls = []

        def get(url, **_):
            calls.append(url)
            return load("gem/list.json")
        with mock.patch.object(gem, "get_json", side_effect=get):
            return gem.fetch(board("gem", "bilt", "Bilt"), ctx), calls

    def test_one_request_reads_every_posting_with_its_description(self):
        result, calls = self._fetch()
        self.assertEqual(calls, ["https://api.gem.com/job_board/v0/bilt/job_posts/"])
        self.assertEqual((result.n_raw, len(result.items), result.truncated), (4, 4, False))
        job = next(i for i in result.items if i["title"].startswith("Forward Deployed"))
        self.assertTrue(job["url"].startswith("https://jobs.gem.com/bilt/"))
        self.assertTrue(job["is_us"])
        self.assertEqual(job["precision"], "exact")
        self.assertTrue(job["posted_at"].startswith("20"))
        self.assertGreater(len(job["description"]), 200)

    def test_an_unchanged_posting_stays_on_the_fast_path(self):
        first = load("gem/list.json")[0]
        ctx = Ctx(matches=MATCH, unchanged=lambda uid, fp: fp == raw_fp(first))
        result, _ = self._fetch(ctx)
        self.assertTrue(next(i for i in result.items if i["ext_id"] == str(first["id"])).get("fast"))

    def test_a_non_list_answer_is_schema_drift(self):
        with mock.patch.object(gem, "get_json", return_value={"error": "x"}):
            self.assertTrue(gem.fetch(board("gem", "bilt"), CTX).error.startswith(SCHEMA_ERROR))


class RipplingTest(unittest.TestCase):
    KEY = "anthony-sylvan-pools"

    def _fetch(self, ctx=CTX):
        calls = []

        def get(url, **_):
            calls.append(url)
            return load("rippling/detail.json") if url.count("/jobs/") else load("rippling/list.json")
        with mock.patch.object(rippling, "get_json", side_effect=get):
            return rippling.fetch(board("rippling", self.KEY, "Anthony & Sylvan Pools"), ctx), calls

    def test_the_list_then_one_detail_for_the_matched_posting(self):
        result, calls = self._fetch()
        self.assertEqual(calls[0], "https://ats.rippling.com/api/v1/board/anthony-sylvan-pools/jobs")
        self.assertEqual(len(calls), 2)                                   # only 'AI Solutions Developer' matched
        self.assertEqual((result.n_raw, len(result.items)), (6, 6))
        job = next(i for i in result.items if i["title"] == "AI Solutions Developer")
        self.assertEqual(job["location"], "Warminster, PA")
        self.assertTrue(job["is_us"])
        self.assertEqual(job["posted_at"], "2026-07-31T22:31:52Z")
        self.assertEqual(job["employment_type"], "full_time")
        self.assertIn("Anthony & Sylvan", job["description"])
        other = next(i for i in result.items if i["title"] == "Construction Manager")
        self.assertEqual((other["posted_at"], other["description"]), (None, ""))

    def test_an_unchanged_board_costs_one_request(self):
        _, calls = self._fetch(Ctx(matches=MATCH, unchanged=lambda uid, fp: True))
        self.assertEqual(len(calls), 1)

    def test_discovery_reads_country_codes_and_the_company_name(self):
        def get(url, **_):
            return load("rippling/detail.json") if url.count("/jobs/") else load("rippling/v2.json")
        with mock.patch.object(rippling, "get_json", side_effect=get):
            found = rippling.survey(self.KEY)
        self.assertEqual(found["name"], "Anthony & Sylvan Pools")
        self.assertEqual(found["n_jobs"], 43)
        self.assertEqual(found["rows"][0][2], "US")


class BambooHRTest(unittest.TestCase):
    KEY = "daytranslations"

    def _fetch(self):
        calls = []

        def get(url, redirects=True, **_):
            calls.append((url, redirects))
            return load("bamboohr/detail.json") if url.endswith("/detail") else load("bamboohr/list.json")
        with mock.patch.object(bamboohr, "get_json", side_effect=get):
            return bamboohr.fetch(board("bamboohr", self.KEY, "Day Translations"), CTX), calls

    def test_list_then_detail_for_the_matched_us_posting_only(self):
        result, calls = self._fetch()
        self.assertEqual(calls[0], ("https://daytranslations.bamboohr.com/careers/list", False))
        details = [u for u, _ in calls if u.endswith("/detail")]
        self.assertEqual(details, ["https://daytranslations.bamboohr.com/careers/105/detail"])   # not the Argentina one
        job = next(i for i in result.items if i["ext_id"] == "105")
        self.assertEqual(job["location"], "Tampa, Florida, United States")
        self.assertTrue(job["is_us"])
        self.assertEqual((job["posted_at"], job["precision"]), ("2026-08-06T00:00:00Z", "day"))
        self.assertEqual(job["url"], "https://daytranslations.bamboohr.com/careers/105")
        self.assertGreater(len(job["description"]), 100)
        abroad = next(i for i in result.items if i["ext_id"] == "52")
        self.assertFalse(abroad["is_us"])

    def test_a_redirect_off_the_board_is_a_dead_board(self):
        def moved(url, redirects=True, **_):
            raise HttpError(url, 302, "Found")
        with mock.patch.object(bamboohr, "get_json", side_effect=moved):
            result = bamboohr.fetch(board("bamboohr", "gone"), CTX)
        self.assertEqual(result.http_status, 404)


class ADPTest(unittest.TestCase):
    KEY = "7d58836c-11dd-4415-9de0-63b918b88652/19000101_000001"

    def _fetch(self):
        calls = []

        def get(url, **_):
            calls.append(url)
            return load("adp/detail.json") if "/job-requisitions/" in url else load("adp/list.json")
        with mock.patch.object(adp, "get_json", side_effect=get):
            return adp.fetch(board("adp", self.KEY, "2Life Communities"), CTX), calls

    def test_pages_then_details_for_matched_requisitions(self):
        result, calls = self._fetch()
        self.assertIn("cid=7d58836c-11dd-4415-9de0-63b918b88652&ccId=19000101_000001", calls[0])
        self.assertIn("$top=20&$skip=1", calls[0])
        self.assertEqual(len(calls), 2)                                   # one list page + 'Senior Events Manager'
        self.assertEqual((result.n_raw, result.truncated), (5, False))
        job = next(i for i in result.items if i["title"] == "Maintenance Mechanic III")
        self.assertEqual(job["location"], "Newton, MA, US; Waltham, MA, US")
        self.assertTrue(job["is_us"])
        self.assertEqual(job["posted_at"], "2026-10-08T22:31:00Z")
        self.assertIsNone(job["salary_min"])                              # $33.65/h is never read as a salary
        self.assertIn("jobId=9201849983032_1", job["url"])

    def test_a_full_page_asks_for_the_next(self):
        page = load("adp/list.json")
        full = {**page, "jobRequisitions": [dict(page["jobRequisitions"][0], itemID=f"x{i}") for i in range(20)],
                "meta": {"totalNumber": 25}}
        rest = {**page, "jobRequisitions": [dict(page["jobRequisitions"][0], itemID=f"y{i}") for i in range(5)],
                "meta": {"totalNumber": 25}}
        with mock.patch.object(adp, "get_json", side_effect=[full, rest]) as got:
            listed, total, truncated = adp.listing(self.KEY)
        self.assertEqual((len(listed), total, truncated), (25, 25, False))
        self.assertIn("$skip=21", got.call_args_list[1].args[0])


class PaylocityTest(unittest.TestCase):
    KEY = "3005d498-4c76-4bc1-a1ba-e4527dfae067"

    def test_board_page_data_then_json_ld_for_the_matched_job(self):
        calls = []

        def get(url, **_):
            calls.append(url)
            return text("paylocity/job.html") if "/Details/" in url else text("paylocity/board.html")
        with mock.patch.object(paylocity, "get_text", side_effect=get):
            result = paylocity.fetch(board("paylocity", self.KEY, "Bitzer"), CTX)
        self.assertEqual(calls[0], f"https://recruiting.paylocity.com/Recruiting/Jobs/All/{self.KEY}")
        self.assertEqual(len(calls), 2)                              # the inventory counter only
        self.assertEqual(result.n_raw, 5)
        job = next(i for i in result.items if i["title"].startswith("Inventory Cycle Counter"))
        self.assertEqual(job["location"], "Flowery Branch, GA, USA")
        self.assertTrue(job["is_us"])
        self.assertIn("cycle counts", job["description"])
        self.assertEqual(job["url"], "https://recruiting.paylocity.com/Recruiting/Jobs/Details/4543799")

    def test_a_page_without_page_data_is_schema_drift(self):
        with mock.patch.object(paylocity, "get_text", return_value="<html>moved</html>"):
            self.assertTrue(paylocity.fetch(board("paylocity", self.KEY), CTX).error.startswith(SCHEMA_ERROR))


class DayforceTest(unittest.TestCase):
    def test_csrf_pair_then_search_with_full_descriptions(self):
        posts = []

        def post(url, body, headers=None):
            posts.append((url, body, headers))
            return load("dayforce/search.json")
        with mock.patch.object(dayforce, "get_json_with_cookies",
                               return_value=({"csrfToken": "tok"}, ("__Host-next-auth.csrf-token=abc", "a=b"))), \
                mock.patch.object(dayforce, "post_json", side_effect=post):
            result = dayforce.fetch(board("dayforce", "gbank/CANDIDATEPORTAL", "GBank"), CTX)
        url, body, headers = posts[0]
        self.assertEqual(url, "https://jobs.dayforcehcm.com/api/geo/gbank/jobposting/search")
        self.assertEqual(body, {"clientNamespace": "gbank", "jobBoardCode": "CANDIDATEPORTAL", "cultureCode": "en-US",
                                "paginationStart": 0})
        self.assertEqual(headers, {"X-CSRF-TOKEN": "tok", "Cookie": "__Host-next-auth.csrf-token=abc; a=b"})
        self.assertEqual(len(posts), 1)                                 # 6 of maxCount 6: one page
        job = next(i for i in result.items if i["title"] == "Treasury Management Coordinator")
        self.assertTrue(job["is_us"])
        self.assertEqual(job["url"], "https://jobs.dayforcehcm.com/en-US/gbank/CANDIDATEPORTAL/jobs/121")
        self.assertIn("Treasury Management", job["description"])
        self.assertEqual(job["posted_at"], "2026-10-01T07:00:00Z")


class CornerstoneTest(unittest.TestCase):
    def test_page_token_then_search(self):
        posts = []

        def post(url, body, headers=None):
            posts.append((url, body, headers))
            return load("cornerstone/search.json")
        with mock.patch.object(cornerstone, "get_text", return_value=text("cornerstone/home.html")) as page, \
                mock.patch.object(cornerstone, "post_json", side_effect=post):
            result = cornerstone.fetch(board("cornerstone", "bistatedev/4", "Bi-State Development"), CTX)
        self.assertEqual(page.call_args.args[0], "https://bistatedev.csod.com/ux/ats/careersite/4/home?c=bistatedev")
        url, body, headers = posts[0]
        self.assertEqual(url, "https://us.api.csod.com/rec-job-search/external/jobs")
        self.assertEqual((body["careerSiteId"], body["pageNumber"]), (4, 1))
        self.assertEqual(headers, {"Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.e30.fixture"})
        job = next(i for i in result.items if i["title"] == "Intern - AI Agent and Analytics")
        self.assertEqual((job["posted_at"], job["precision"]), ("2026-09-30T00:00:00Z", "day"))
        self.assertEqual(job["location"], "St. Louis, MO, US")
        self.assertTrue(job["is_us"])
        self.assertTrue(job["url"].endswith("/home/requisition/" + job["ext_id"] + "?c=bistatedev"))

    def test_a_page_without_a_token_is_schema_drift(self):
        with mock.patch.object(cornerstone, "get_text", return_value="<html></html>"):
            result = cornerstone.fetch(board("cornerstone", "bistatedev/4"), CTX)
        self.assertTrue(result.error.startswith(SCHEMA_ERROR))


class JazzHRTest(unittest.TestCase):
    def test_apply_page_then_json_ld_for_matched_jobs(self):
        calls = []

        def get(url, **_):
            calls.append(url)
            return text("jazzhr/job.html") if url.count("/apply/") else text("jazzhr/apply.html")
        with mock.patch.object(jazzhr, "get_text", side_effect=get):
            result = jazzhr.fetch(board("jazzhr", "focuskpi", "FocusKPI"), CTX)
        self.assertEqual(calls[0], "https://focuskpi.applytojob.com/apply")
        self.assertEqual(result.n_raw, 4)
        self.assertEqual(len(calls), 3)                            # Data Engineer + Data Scientist pages
        job = next(i for i in result.items if i["ext_id"] == "FwtCZHBZHk")
        self.assertEqual(job["url"], "https://focuskpi.applytojob.com/apply/FwtCZHBZHk/Data-Engineer")
        self.assertEqual((job["posted_at"], job["precision"]), ("2026-10-07T00:00:00Z", "day"))
        self.assertTrue(job["is_us"])
        self.assertEqual(job["employment_type"], "contract")
        other = next(i for i in result.items if i["title"] == "Accounts Payable Specialist")
        self.assertEqual((other["location"], other["posted_at"]), ("Mountian View, CA", None))

    def test_the_global_feed_keeps_tenants_with_matched_titles(self):
        feed = ('<urlset><url><loc>https://focuskpi.applytojob.com/apply/FwtCZHBZHk/Data-Engineer?source=GS</loc></url>'
                '<url><loc>https://diner.applytojob.com/apply/AbCdEf12/Line-Cook?source=GS</loc></url></urlset>')
        pages = iter([feed, "<urlset></urlset>"])
        with mock.patch.object(jazzhr, "get_text", side_effect=lambda url, **_: next(pages)):
            found = jazzhr.feed_tenants(lambda title: "Data" in title)
        self.assertEqual(found, {"focuskpi": ["Data Engineer"]})

    def test_survey_names_the_board_from_its_page_title(self):
        with mock.patch.object(jazzhr, "get_text", return_value=text("jazzhr/apply.html")):
            self.assertEqual(jazzhr.survey("focuskpi")["name"], "FocusKPI Inc.")


class IBMTest(unittest.TestCase):
    def test_one_us_filtered_search_per_term_and_no_misses(self):
        posts = []

        def post(url, body, headers=None):
            posts.append(body)
            return load("ibm/search.json")
        with mock.patch.object(ibm, "post_json", side_effect=post):
            result = ibm.fetch(board("ibm", "ibm", "IBM"), Ctx(matches=MATCH, search_terms=("machine learning", "llm")))
        self.assertEqual(len(posts), 2)
        self.assertEqual(posts[0]["query"]["bool"]["filter"], [{"term": {"field_keyword_05": "United States"}}])
        self.assertEqual(result.n_raw, 4)                                     # both terms found the same 4
        job = result.items[0]
        self.assertTrue(job["url"].startswith("https://careers.ibm.com/careers/JobDetail?jobId="))
        self.assertTrue(job["is_us"])
        self.assertEqual(job["precision"], "day")
        self.assertEqual(job["scopes"], ["term:llm", "term:machine learning"])
        self.assertIn("ibm", store.TERM_SEARCH_ATS)


class WiringTest(unittest.TestCase):
    def test_adapters_hosts_and_list_urls(self):
        for ats in ("gem", "rippling", "bamboohr"):
            self.assertIn(ats, ADAPTERS)
            self.assertIn(ats, registry.FETCHABLE)
        self.assertEqual(fetch.host_of(board("bamboohr", "acme")), "acme.bamboohr.com")
        self.assertEqual(fetch.host_of(board("rippling", "acme")), "ats.rippling.com")
        self.assertEqual(fetch.robots_urls(board("gem", "acme")), ["https://api.gem.com/job_board/v0/acme/job_posts/"])
        self.assertIn("bamboohr", tiers.UNDATED_ATS)
        self.assertEqual(fetch.host_of(board("cornerstone", "bistatedev/4")), "bistatedev.csod.com")
        self.assertEqual(net.bucket_key("us.api.csod.com"), "csod.com")
        self.assertEqual(fetch.robots_urls(board("dayforce", "gbank/CANDIDATEPORTAL")),
                         ["https://jobs.dayforcehcm.com/api/geo/gbank/jobposting/search"])
        self.assertEqual(fetch.robots_urls(board("cornerstone", "bistatedev/4")),
                         ["https://bistatedev.csod.com/ux/ats/careersite/4/home"])

    def test_every_bamboohr_tenant_shares_one_rate_bucket(self):
        self.assertEqual(net.bucket_key("acme.bamboohr.com"), "bamboohr.com")
        self.assertEqual(net.bucket_key("other.bamboohr.com"), "bamboohr.com")
        self.assertEqual(net.bucket_key("ats.rippling.com"), "ats.rippling.com")

    def test_detect_and_crawl_keys(self):
        self.assertEqual(detect("https://acme.bamboohr.com/careers/12"), {"ats": "bamboohr", "token": "acme"})
        self.assertIsNone(detect("https://www.bamboohr.com/careers"))
        self.assertEqual(detect("https://ats.rippling.com/acme-inc/jobs/1f"), {"ats": "rippling", "token": "acme-inc"})
        self.assertEqual(detect("https://jobs.gem.com/bilt/am9i"), {"ats": "gem", "token": "bilt"})
        self.assertEqual(discover_cc.token_from_url("bamboohr", "https://Acme.bamboohr.com/careers"), "acme")
        self.assertIsNone(discover_cc.token_from_url("bamboohr", "https://acme.bamboohr.com/login.php"))
        self.assertIsNone(discover_cc.token_from_url("gem", "https://jobs.gem.com/api/x"))
        adp_url = ("https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?"
                   "cid=7d58836c-11dd-4415-9de0-63b918b88652&ccId=19000101_000001&lang=en_US")
        self.assertEqual(discover_cc.token_from_url("adp", adp_url),
                         "7d58836c-11dd-4415-9de0-63b918b88652/19000101_000001")
        self.assertEqual(discover_cc.token_from_url("dayforce", "https://jobs.dayforcehcm.com/en-US/dcrusa/join-us"),
                         "dcrusa/join-us")
        self.assertEqual(discover_cc.token_from_url(
            "cornerstone", "https://bistatedev.csod.com/ux/ats/careersite/4/home?c=bistatedev"), "bistatedev/4")
        self.assertIsNone(discover_cc.token_from_url("cornerstone", "https://www.csod.com/ux/ats/careersite/1/home"))
        self.assertIsNone(discover_cc.token_from_url("paylocity", "https://recruiting.paylocity.com/Recruiting/Jobs/"
                                                                  "Details/4543799"))


class DiscoveryTest(unittest.TestCase):
    def test_an_open_undated_us_posting_makes_the_board_live(self):
        rows = [(None, "Tampa, Florida, United States", "United States", "Data Analyst"),
                (None, "Remote Worldwide, Argentina", "Argentina", "PM")]
        self.assertEqual(discover_more.us_live(rows, NOW), (1, "2026-10-08T20:00:00Z"))
        self.assertEqual(discover_more.us_live(rows[1:], NOW), (0, None))

    def test_dated_postings_must_be_recent(self):
        old = [("2026-01-02T00:00:00Z", "Austin, TX", "US", "x")]
        self.assertEqual(discover_more.us_live(old, NOW)[0], 0)
        fresh_row = [("2026-10-01T00:00:00Z", "Austin, TX", "US", "x")]
        self.assertEqual(discover_more.us_live(fresh_row, NOW)[0], 1)

    def test_check_names_from_the_board_then_the_seed_list(self):
        survey = {"n_jobs": 2, "name": None, "rows": [(None, "Austin, TX", "US", "Data Engineer")]}
        with mock.patch.dict(discover_more.SURVEYS, {"bamboohr": lambda key: survey}):
            verdict = discover_more.check("bamboohr", "acme", NOW, lambda key: "Acme Robotics, Inc.")
        self.assertEqual((verdict.n_jobs, verdict.us60, verdict.name, verdict.name_src), (2, 1, "Acme Robotics", "seed"))

    def test_seed_rows_are_validated_before_use(self):
        text = "name,slug,url\nAcme,Acme,https://acme.bamboohr.com\nBad,../etc,x\nEvil,a b,x\n"
        self.assertEqual(discover_more.parse_seed("bamboohr", text), {"acme": "Acme"})


if __name__ == "__main__":
    unittest.main()
