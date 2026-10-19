"""GOAL10 adapters on responses recorded live on 2026-10-08 (trimmed): Oracle Recruiting Cloud (JPMorgan Chase),
classic iCIMS (Stifel), Jobvite (Nutanix, MedVet's custom template), Taleo (HDR's REST section, Weyerhaeuser's legacy
section) and SuccessFactors (EY). Search adapters query per term and never count misses; iCIMS classic and Jobvite
are full lists whose matched postings cost one detail page."""
import json
import unittest
from datetime import date
from pathlib import Path
from unittest import mock
from urllib.parse import unquote

from datetime import datetime, timezone

from job_hunt_agent import discover_cc, discover_enterprise, fetch, registry, signals, store
from job_hunt_agent.ats import (ADAPTERS, Ctx, board_key, detect, fulllist, icims_classic, jobvite, jsonld,
                         oracle_recruiting, successfactors, taleo)
from job_hunt_agent.ats.common import SCHEMA_ERROR, SchemaDrift, raw_fp
from job_hunt_agent.net import HttpError

FIXTURES = Path(__file__).parent / "fixtures"
ML = lambda title: any(w in title.lower() for w in ("machine learning", "mlops", "ai ", "artificial"))  # noqa: E731
CTX = Ctx(matches=ML, search_terms=("machine learning",), today=date(2026, 10, 8))


def text(path: str) -> str:
    return (FIXTURES / path).read_text()


def board(ats: str, key: str, company: str = "Acme") -> dict:
    return {"board": f"{ats}:{key}", "ats": ats, "key": key, "company": company}


class OracleRecruitingTest(unittest.TestCase):
    KEY = "jpmc.fa.oraclecloud.com/CX_1001"

    def _fetch(self, ctx=CTX):
        listing = json.loads(text("oracle_recruiting/list.json"))
        detail = json.loads(text("oracle_recruiting/detail.json"))
        calls = []

        def get(url):
            calls.append(unquote(url))
            return detail if "JobRequisitionDetails" in url else listing
        with mock.patch.object(oracle_recruiting, "get_json", side_effect=get):
            return oracle_recruiting.fetch(board("oracle_recruiting", self.KEY, "JPMorgan Chase"), ctx), calls

    def test_newest_first_phrase_search_drops_non_us_and_details_only_matches(self):
        result, calls = self._fetch()
        self.assertIn("siteNumber=CX_1001", calls[0])
        self.assertIn('keyword="machine learning"', calls[0])
        self.assertIn("sortBy=POSTING_DATES_DESC", calls[0])
        self.assertEqual((result.n_raw, len(result.items)), (5, 4))                 # the Bengaluru req is dropped
        self.assertEqual(sum("JobRequisitionDetails" in u for u in calls), 2)        # AI Research lead + MLOps
        job = next(i for i in result.items if i["ext_id"] == "210789358")
        self.assertEqual(job["url"], "https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/"
                                     "job/210789358")
        self.assertEqual((job["posted_at"], job["precision"]), ("2026-10-08T00:00:00Z", "day"))
        self.assertTrue(job["is_us"])
        self.assertIn("JPMorganChase AI Research", job["description"])
        self.assertEqual((job["salary_min"], job["salary_max"]), (133000.0, 205000.0))   # the flex field, with cents
        self.assertEqual(job["scopes"], ["term:machine learning"])

    def test_a_known_jd_costs_no_detail(self):
        _, calls = self._fetch(Ctx(matches=ML, search_terms=("machine learning",), needs_detail=lambda uid: False))
        self.assertFalse([u for u in calls if "JobRequisitionDetails" in u])

    def test_a_missing_requisition_list_is_schema_drift(self):
        with mock.patch.object(oracle_recruiting, "get_json", return_value={"items": [{"jobs": []}]}):
            result = oracle_recruiting.fetch(board("oracle_recruiting", self.KEY), CTX)
        self.assertTrue(result.error.startswith(SCHEMA_ERROR))

    def test_a_secondary_us_location_keeps_a_foreign_primary(self):
        posting = {"PrimaryLocation": "Bengaluru, India", "PrimaryLocationCountry": "IN",
                   "secondaryLocations": [{"Name": "Plano, TX, United States", "CountryCode": "US"}]}
        self.assertTrue(oracle_recruiting.is_us(posting))
        self.assertFalse(oracle_recruiting.is_us({**posting, "secondaryLocations": []}))

    def test_finder_syntax_is_stripped_from_the_keyword(self):
        self.assertIn('keyword="a b  c"', unquote(oracle_recruiting.list_url("h", "S", 'a,b;"c"')))


class IcimsClassicTest(unittest.TestCase):
    HOST = "careers-stifel.icims.com"

    def _fetch(self, job_page=None):
        sitemap, job = text("icims_classic/sitemap.xml"), job_page or text("icims_classic/job.html")
        calls = []

        def get(url):
            calls.append(url)
            if url.endswith("/sitemap.xml"):
                return sitemap
            if isinstance(job, Exception):
                raise job
            return job
        with mock.patch.object(icims_classic, "get_text", side_effect=get):
            return icims_classic.fetch(board("icims_classic", self.HOST, "Stifel"), CTX), calls

    def test_the_sitemap_is_the_full_list_and_matches_read_their_json_ld(self):
        result, calls = self._fetch()
        self.assertEqual((result.n_raw, len(result.items), result.truncated), (6, 6, False))
        self.assertEqual(calls[1], f"https://{self.HOST}/jobs/10024/sr.-ai-cloud-platform-engineer/job?in_iframe=1")
        job = next(i for i in result.items if i["ext_id"] == "10024")
        self.assertEqual(job["title"], "Sr. AI Cloud Platform Engineer")
        self.assertEqual(job["location"], "Saint Louis, MO, US")
        self.assertTrue(job["is_us"])
        self.assertEqual((job["posted_at"], job["precision"]), ("2026-10-05T04:00:00Z", "exact"))
        self.assertGreater(len(job["description"]), 1000)
        other = next(i for i in result.items if i["ext_id"] == "9776")
        self.assertEqual((other["title"], other["precision"]), ("Research Associate Iii - Biotechnology", "unknown"))

    def test_a_failed_detail_marks_the_posting_so_the_next_run_asks_again(self):
        result, _ = self._fetch(HttpError("u", 503, "busy"))
        job = next(i for i in result.items if i["ext_id"] == "10024")
        entry = next(e for e in icims_classic.entries(self.HOST, text("icims_classic/sitemap.xml"))
                     if e["id"] == "10024")
        self.assertEqual(job["raw_fp"], raw_fp(entry, fulllist.PENDING))
        self.assertNotEqual(job["raw_fp"], raw_fp(entry))
        self.assertIn("detail 10024", result.notes[0])

    def test_other_hosts_in_a_sitemap_are_ignored_and_a_non_sitemap_is_drift(self):
        xml = "<urlset><url><loc>https://evil.example/jobs/1/x/job</loc></url></urlset>"
        self.assertEqual(icims_classic.entries(self.HOST, xml), [])
        with mock.patch.object(icims_classic, "get_text", return_value="<html>login</html>"):
            result = icims_classic.fetch(board("icims_classic", self.HOST), CTX)
        self.assertTrue(result.error.startswith(SCHEMA_ERROR))


class JobviteTest(unittest.TestCase):
    def test_rows_from_the_standard_and_a_custom_template(self):
        rows = jobvite.rows("nutanix", text("jobvite/jobs.html"))
        self.assertEqual(rows[0], {"id": "oUQKAfwO", "title": "Finance Manager", "location": "San Jose, California"})
        custom = jobvite.rows("medvet", text("jobvite/search_custom.html"))
        self.assertEqual([r["location"] for r in custom], ["United States", "Chicago, Illinois", "Worthington, Ohio"])

    def test_detail_page(self):
        found = jobvite.parse_detail(text("jobvite/job.html"))
        self.assertEqual((found["title"], found["team"], found["location"], found["req_id"]),
                         ("Manager, Engineering", "Engineering", "San Jose, California", "32344"))
        self.assertIn("Hungry, Humble, Honest", found["description"])

    def test_a_show_more_board_is_read_through_the_paged_search(self):
        first = text("jobvite/jobs.html").replace("</table>", '</table><a href="/medvet/search?c=Vets&p=0">More</a>')
        pages = {"/medvet/jobs": first, "/medvet/search?q=&p=0": text("jobvite/search_custom.html")}
        with mock.patch.object(jobvite, "get_text", side_effect=lambda u: pages[u.split(".com", 1)[1]]):
            rows, truncated = jobvite.listing("medvet")
        self.assertEqual((len(rows), truncated), (3, False))       # fewer rows than a page: the last page

    def test_full_list_fetch_skips_details_for_rows_placed_abroad(self):
        ctx = Ctx(matches=lambda t: "Financ" in t)
        pages = {"/nutanix/jobs": text("jobvite/jobs.html")}
        with mock.patch.object(jobvite, "get_text",
                               side_effect=lambda u: pages.get(u.split(".com", 1)[1], text("jobvite/job.html"))) as get:
            result = jobvite.fetch(board("jobvite", "nutanix", "Nutanix"), ctx)
        self.assertEqual(len(result.items), 6)
        self.assertEqual(get.call_count, 2)          # the list + Finance Manager (San Jose); Bangalore/Mexico skipped
        self.assertEqual(result.items[0]["apply_url"], "https://jobs.jobvite.com/nutanix/job/oUQKAfwO/apply")

    def test_a_slug_that_left_jobvite_is_gone(self):
        with mock.patch.object(jobvite, "get_text", return_value="<html>ADP Workforce Now</html>"):
            result = jobvite.fetch(board("jobvite", "conair"), CTX)
        self.assertEqual(result.http_status, 404)


class TaleoTest(unittest.TestCase):
    def setUp(self):
        taleo._modes.clear()

    def test_rest_section_search_and_detail(self):
        listing, posts = json.loads(text("taleo/search.json")), []

        def post(url, body, headers=None):
            posts.append((url, body, headers))
            return listing
        pages = {"jobsearch.ftl": text("taleo/jobsearch_rest.html"), "jobdetail.ftl": text("taleo/jobdetail.html")}
        with mock.patch.object(taleo, "post_json", side_effect=post), \
                mock.patch.object(taleo, "get_text", side_effect=lambda u: pages[u.split("/")[-1].split("?")[0]]):
            result = taleo.fetch(board("taleo", "hdr.taleo.net/ex", "HDR"),
                                 Ctx(matches=lambda t: "Control Systems" in t, search_terms=("data",)))
        url, body, headers = posts[0]
        self.assertIn("portal=101430233", url)
        self.assertEqual((body["fieldData"]["fields"]["KEYWORD"], headers), ("data", taleo.HEADERS))
        job = next(i for i in result.items if i["title"] == "Senior Control Systems Engineer")
        self.assertEqual((job["location"], job["posted_at"], job["req_id"]),
                         ("United States-Ohio-Columbus", "2026-06-15T00:00:00Z", "193686"))
        self.assertIn("At HDR, our employee-owners", job["description"])
        self.assertEqual(job["url"], "https://hdr.taleo.net/careersection/ex/jobdetail.ftl?job=193686&lang=en")

    def test_a_list_without_a_day_column_takes_the_day_from_the_job_page(self):
        # GOAL17 extra row: UTSW / Weyerhaeuser lists carry no day, their jobdetail.ftl does ('Sep 9, 2026, 7:43:23 PM')
        listing = json.loads(text("taleo/search.json"))
        for row in listing["requisitionList"]:
            row["column"] = [c for c in row["column"] if not (isinstance(c, str) and taleo._DAY.match(c.strip()))]
        pages = {"jobsearch.ftl": text("taleo/jobsearch_rest.html"), "jobdetail.ftl": text("taleo/jobdetail.html")}
        with mock.patch.object(taleo, "post_json", return_value=listing), \
                mock.patch.object(taleo, "get_text", side_effect=lambda u: pages[u.split("/")[-1].split("?")[0]]):
            result = taleo.fetch(board("taleo", "hdr.taleo.net/ex", "HDR"),
                                 Ctx(matches=lambda t: "Control Systems" in t, search_terms=("data",)))
        job = next(i for i in result.items if i["title"] == "Senior Control Systems Engineer")
        other = next(i for i in result.items if "Control Systems" not in i["title"])
        self.assertEqual((job["posted_at"], job["precision"]), ("2026-06-15T00:00:00Z", "day"))
        self.assertIn("At HDR, our employee-owners", job["description"])
        self.assertEqual((other["posted_at"], other["precision"]), (None, "unknown"))   # no page read: first seen

    def test_the_job_page_day_is_the_posting_not_the_unposting_day(self):
        parts = ["!*!<p>JD</p>", "Sep 16, 2026, 11\\:33\\:48 AM", "Oct 16, 2026, 11\\:59\\:00 PM"]
        page = '<input id="initialHistory" value="' + "!|!".join(parts) + '">'
        self.assertEqual(taleo.detail(page), {"description": "JD", "posted_at": "2026-09-16T00:00:00Z"})
        self.assertEqual(taleo.detail(page.replace("Sep 16, 2026, 11\\:33\\:48 AM", "x").replace("Oct 16", "y"))
                         ["posted_at"], None)

    def test_legacy_section_rows_carry_their_own_ids_places_and_days(self):
        rows = taleo.legacy_rows(text("taleo/jobsearch_legacy.html"))
        self.assertEqual(len({r["jobId"] for r in rows}), len(rows))
        superintendent = next(r for r in rows if r["contestNo"] == "01024992")
        self.assertEqual((superintendent["jobId"], taleo.title(superintendent), taleo.places(superintendent),
                          taleo.posted_day(superintendent)),
                         ("467445", "Electrical Superintendent", ["USA-LA-Dodson"], "2026-10-06T00:00:00Z"))
        self.assertFalse(taleo.is_us(next(r for r in rows if taleo.places(r) == ["CAN-BC-Delta"])))

    def test_a_legacy_title_with_a_comma_is_not_its_place(self):
        parts = ["9", "Supervisor, Maintenance", "Add to My Job Cart", "Oct 7, 2026",
                 "Submission for the position: Supervisor, Maintenance - (Job Number: 77)"]
        page = f'<input id="initialHistory" value="{"!|!".join(parts)}">'
        row = taleo.legacy_rows(page)[0]
        self.assertEqual((row["jobId"], taleo.places(row), taleo.posted_day(row)), ("9", [], "2026-10-07T00:00:00Z"))

    def test_legacy_mode_searches_with_the_keyword_parameter(self):
        urls = []

        def get(url):
            urls.append(url)
            return text("taleo/jobsearch_legacy.html")
        with mock.patch.object(taleo, "get_text", side_effect=get):
            result = taleo.fetch(board("taleo", "weyerhaeuser.taleo.net/10000", "Weyerhaeuser"),
                                 Ctx(matches=lambda t: False, search_terms=("engineer",)))
        self.assertEqual(urls[1], "https://weyerhaeuser.taleo.net/careersection/10000/jobsearch.ftl?lang=en"
                                  "&keyword=engineer")
        self.assertEqual(len(result.items), 23)                 # 25 rows; Delta BC and Grande Prairie AB dropped

    def test_a_closed_or_moved_section_is_gone_not_drift(self):
        for page in ("<title>Career Section Unavailable</title>", "<html>our new careers site</html>"):
            taleo._modes.clear()
            with mock.patch.object(taleo, "get_text", return_value=page):
                result = taleo.fetch(board("taleo", "x.taleo.net/2"), CTX)
            self.assertEqual(result.http_status, 404)


class SuccessFactorsTest(unittest.TestCase):
    def test_search_rows_and_detail_microdata(self):
        rows = successfactors.rows(text("successfactors/search.html"))
        self.assertTrue(rows[0]["path"].startswith("/ey/job/"))
        self.assertTrue(rows[0]["path"].endswith("/1445446833/"))
        self.assertFalse(successfactors.is_us(rows[0]))                  # 'Bengaluru, KA, IN, 560001'
        found = successfactors.parse_detail(text("successfactors/job.html"))
        self.assertEqual((found["posted"], found["country"], found["organization"]),
                         ("2026-10-08T00:00:00Z", "IN", "EY"))
        self.assertTrue(found["description"].startswith("Requisition Id: 1750608"))

    def test_a_search_with_no_results_is_empty_not_drift(self):
        page = ('<h1 class="keyword-title">Career Opportunities for "llm".</h1><div id="noresults" class="alert">'
                '<label>There are no jobs that match your criteria.</label></div>')       # Novo Nordisk, 2026-10-08
        self.assertEqual(successfactors.rows(page), [])
        with self.assertRaises(SchemaDrift):
            successfactors.rows("<html>a maintenance page</html>")

    def test_the_tile_layout_reads_like_the_table(self):
        rows = successfactors.rows(text("successfactors/search_tiles.html"))          # L3Harris, recorded 2026-10-08
        self.assertEqual(rows[0], {"id": "1438091900", "title": "Associate, Inventory Management",
                                   "path": "/job/Anaheim-Associate%2C-Inventory-Management-CA-92805/1438091900/",
                                   "location": "Anaheim, CA, US, 92805", "date": None})
        self.assertTrue(all(successfactors.is_us(r) for r in rows))

    def test_fetch_keeps_us_rows_and_builds_site_urls(self):
        us_row = text("successfactors/search.html").replace("Bengaluru, KA, IN, 560001", "Hoboken, NJ, US, 07030", 1)
        with mock.patch.object(successfactors, "get_text", return_value=us_row) as get:
            result = successfactors.fetch(board("successfactors", "careers.ey.com/ey", "EY"),
                                          Ctx(matches=lambda t: False, search_terms=("machine learning",)))
        self.assertEqual(get.call_args_list[0].args[0],
                         "https://careers.ey.com/ey/search/?q=%22machine+learning%22&startrow=0")
        self.assertEqual([i["ext_id"] for i in result.items], ["1445446833"])
        self.assertTrue(result.items[0]["url"].startswith("https://careers.ey.com/ey/job/"))


class WiringTest(unittest.TestCase):
    def test_every_new_system_is_registered_with_hosts_robots_urls_and_search_semantics(self):
        keys = {"oracle_recruiting": "jpmc.fa.oraclecloud.com/CX_1001", "taleo": "hdr.taleo.net/ex",
                "successfactors": "careers.ey.com/ey", "icims_classic": "careers-stifel.icims.com",
                "jobvite": "nutanix"}
        for ats, key in keys.items():
            self.assertIn(ats, ADAPTERS)
            self.assertIn(ats, registry.FETCHABLE)
            urls = fetch.robots_urls(board(ats, key))
            self.assertTrue(urls[0].startswith("https://") and "{" not in urls[0], urls)
        self.assertEqual(fetch.host_of(board("successfactors", "careers.ey.com/ey")), "careers.ey.com")
        self.assertEqual(fetch.host_of(board("jobvite", "nutanix")), "jobs.jobvite.com")
        self.assertTrue({"oracle_recruiting", "taleo", "successfactors"} <= store.SEARCH_ATS)
        self.assertFalse({"icims_classic", "jobvite"} & store.SEARCH_ATS)          # full lists: misses count

    def test_careers_urls_and_registry_entries_round_trip(self):
        found = detect("https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/1")
        self.assertEqual(board_key(found), "jpmc.fa.oraclecloud.com/CX_1001")
        self.assertEqual(detect("https://hdr.taleo.net/careersection/ex/jobdetail.ftl?job=1")["section"], "ex")
        self.assertEqual(detect("https://careers-stifel.icims.com/jobs/1/x/job")["ats"], "icims_classic")
        self.assertIsNone(detect("https://www.icims.com/about"))
        self.assertEqual(detect("https://jobs.jobvite.com/careers/nutanix/job/x")["token"], "nutanix")
        row = {"ats": "taleo", "slug": "hdr.taleo.net/ex", "board_name": "HDR", "website": None, "n_jobs": 50,
               "us60": 3, "source": "cc", "name_src": "page"}
        entry = registry._entry(row)
        self.assertEqual((entry["token"], entry["section"], board_key(entry)), ("hdr.taleo.net", "ex", row["slug"]))

    def test_salary_with_cents(self):
        self.assertEqual(signals.parse_salary("New York,NY $133,000.00-$205,000.00")[:2], (133000.0, 205000.0))


class JsonLdTest(unittest.TestCase):
    def test_placeholder_address_parts_are_left_out(self):
        posting = {"jobLocation": [{"address": {"addressLocality": "Boston", "addressRegion": "UNAVAILABLE",
                                                "addressCountry": "US"}}]}               # Arthur D. Little, 2026-10-08
        self.assertEqual(jsonld.location(posting), ("Boston, US", "US"))


class DiscoveryTest(unittest.TestCase):
    NOW = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)

    def _jobvite(self, page: str):
        with mock.patch.object(jobvite, "get_text", return_value=page), \
                mock.patch.object(discover_enterprise, "get_text", return_value=page):
            return discover_enterprise.CHECKS["jobvite"]("nutanix", self.NOW)

    def test_a_board_counts_only_with_a_us_data_ai_title_open(self):
        plain = text("jobvite/jobs.html")
        self.assertEqual(self._jobvite(plain).us60, 0)                  # finance roles only: not admitted
        ml = plain.replace(">Finance Manager<", ">Machine Learning Engineer<")
        verdict = self._jobvite(ml)
        self.assertEqual((verdict.n_jobs, verdict.us60, verdict.name), (6, 1, "Nutanix"))   # title "Careers": slug name
        abroad = plain.replace(">Financial Analyst<", ">Data Scientist<")       # Bangalore row
        self.assertEqual(self._jobvite(abroad).us60, 0)

    def test_an_unreadable_page_is_no_board_not_a_retry(self):
        with mock.patch.object(jobvite, "get_text", return_value='<div class="jv-wrapper">landing page</div>'):
            self.assertEqual(discover_enterprise.CHECKS["jobvite"]("amba", self.NOW).n_jobs, 0)

    def test_site_titles_become_employer_names(self):
        for title, name in [("Verisk Verisk Candidate Experience page", "Verisk"), ("Job Search | HDR", "HDR"),
                            ("Mortenson External Candidate Experience page", "Mortenson"), ("UNAVAILABLE", None),
                            ("First Solar (US) Candidate Experience page", "First Solar")]:
            self.assertEqual(discover_enterprise.page_name(f"<title>{title}</title>"), name)
        self.assertEqual(discover_enterprise.slug_name("icims_classic", "careers-stifel.icims.com"), "Stifel")

    def test_common_crawl_urls_give_strict_keys(self):
        cases = [("oracle_recruiting", "https://hdjq.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/9",
                  "hdjq.fa.us2.oraclecloud.com/CX_1"),
                 ("oracle_recruiting", "https://evil.example/hcmUI/CandidateExperience/en/sites/CX_1", None),
                 ("taleo", "https://hdr.taleo.net/careersection/ex/jobdetail.ftl?job=1", "hdr.taleo.net/ex"),
                 ("icims_classic", "https://careers-stifel.icims.com/jobs/1/x/job", "careers-stifel.icims.com"),
                 ("jobvite", "https://jobs.jobvite.com/careers/nutanix/job/o1", "nutanix"),
                 ("jobvite", "https://jobs.jobvite.com/robots.txt", None)]
        for ats, url, key in cases:
            self.assertEqual(discover_cc.token_from_url(ats, url), key, url)

    def _taleo(self, rows_by_term: dict):
        with mock.patch.object(discover_enterprise.taleo, "search_rows",
                               side_effect=lambda host, section, term: rows_by_term.get(term, [])), \
                mock.patch.object(discover_enterprise, "get_text", return_value="<title>Job Search | Acme</title>"):
            return discover_enterprise.CHECKS["taleo"]("acme.taleo.net/ex", self.NOW)

    def test_a_taleo_section_without_dates_is_read_by_open_titles(self):
        def row(job_id, title, place):
            return {"jobId": job_id, "column": [title, json.dumps([place])], "linkedColumn": 0, "locationsColumns": [1]}
        newest = [row(1, "Welder", "USA-TX-Houston"), row(2, "Payroll Clerk", "USA-OH-Dayton")]
        verdict = self._taleo({None: newest, "data scientist": [row(3, "Data Scientist II", "USA-TX-Houston")]})
        self.assertEqual((verdict.us60, verdict.newest_us, verdict.name), (1, "2026-10-08T12:00:00Z", "Acme"))
        abroad = self._taleo({None: newest, "data scientist": [row(3, "Data Scientist II", "United Kingdom-London")]})
        self.assertEqual(abroad.us60, 0)
        self.assertEqual(self._taleo({None: newest}).us60, 0)                 # no data / AI title: not admitted

    def test_oracle_test_tenants_are_not_boards(self):
        url = "https://efds-test.fa.em5.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_8001"
        self.assertIsNone(discover_cc.token_from_url("oracle_recruiting", url))
        self.assertEqual(discover_enterprise.CHECKS["oracle_recruiting"]("ibqbjb-dev1.fa.ocs.oraclecloud.com/CX",
                                                                       self.NOW).us60, 0)

    def test_one_oracle_site_per_tenant(self):
        def row(slug, n):
            return {"ats": "oracle_recruiting", "slug": slug, "hit": 1, "us60": 5, "n_jobs": n, "newest_us": None,
                    "board_name": "Acme", "website": None}
        kept, funnel = registry.select([row("a.fa.us2.oraclecloud.com/CX", 90),
                                        row("a.fa.us2.oraclecloud.com/CX_1", 120)], [])
        self.assertEqual([r["slug"] for r in kept], ["a.fa.us2.oraclecloud.com/CX_1"])
        self.assertEqual(funnel["oracle_recruiting_extra_sites"], 1)

    def test_the_site_picked_for_a_tenant_does_not_depend_on_row_order(self):
        def row(site, name_src):
            return {"ats": "oracle_recruiting", "slug": f"edel.fa.us2.oraclecloud.com/{site}", "hit": 1, "us60": 121,
                    "n_jobs": 1005, "newest_us": None, "website": None, "name_src": name_src,
                    "board_name": "Fortinet" if name_src == "list" else "Edel"}
        rows = [row("CX", "slug"), row("CX_1", "slug"), row("CX_2001", "list")]
        for ordered in (rows, rows[::-1], rows[1:] + rows[:1]):
            kept, _ = registry.select(ordered, [])
            self.assertEqual([(r["slug"], r["board_name"]) for r in kept],
                             [("edel.fa.us2.oraclecloud.com/CX_2001", "Fortinet")])
        bigger = {**row("CX", "page"), "n_jobs": 1100}                 # more jobs wins; the pinned name moves to it
        self.assertEqual([(r["slug"], r["board_name"]) for r in registry.select(rows + [bigger], [])[0]],
                         [("edel.fa.us2.oraclecloud.com/CX", "Fortinet")])
        unnamed = [row("CX", "page"), row("CX_1", "page")]
        for ordered in (unnamed, unnamed[::-1]):
            self.assertEqual(registry.select(ordered, [])[0][0]["slug"], "edel.fa.us2.oraclecloud.com/CX")
            kept, _ = registry.select(ordered, [], {"oracle_recruiting:edel.fa.us2.oraclecloud.com/CX_1"})
            self.assertEqual(kept[0]["slug"], "edel.fa.us2.oraclecloud.com/CX_1")         # the listed site stays


if __name__ == "__main__":
    unittest.main()
