"""GOAL5 §4 (decision 011) adapters: Eightfold, Phenom, iCIMS (Jibe) and Workable, on synthetic payloads shaped like
the live responses checked on 2026-10-06. Search adapters query per term and never count misses; Workable is a full
board list."""
import unittest
from datetime import date
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from job_hunt_agent import config, fetch, store
from job_hunt_agent.ats import Ctx, detect, eightfold, icims, phenom, workable
from job_hunt_agent.ats.common import SCHEMA_ERROR
from job_hunt_agent.net import HttpError

ML = lambda title: "machine learning" in title.lower() or "data scientist" in title.lower()   # noqa: E731
CTX = Ctx(matches=ML, search_terms=("machine learning",), today=date(2026, 10, 6))


def board(ats: str, key: str, company: str = "Acme") -> dict:
    return {"board": f"{ats}:{key}", "ats": ats, "key": key, "company": company}


def pcsx_position(pid: int, name: str) -> dict:
    return {"id": pid, "displayJobId": f"R{pid}", "name": name, "locations": ["United States, Washington, Redmond"],
            "standardizedLocations": ["Redmond, WA, US"], "postedTs": 1791248905, "department": "AI",
            "workLocationOption": "onsite", "positionUrl": f"/careers/job/{pid}"}


class EightfoldTest(unittest.TestCase):
    KEY = "apply.careers.example.com/example.com/pcsx"

    def test_pages_until_count_then_details_only_matched_titles(self):
        page1 = {"data": {"count": 12, "positions": [pcsx_position(i, "ML Engineer") for i in range(1, 10)]
                          + [pcsx_position(10, "Machine Learning Engineer")]}}
        page2 = {"data": {"count": 12, "positions": [pcsx_position(11, "Accountant"),
                                                     pcsx_position(12, "Senior Machine Learning Scientist")]}}
        detail = {"data": {"jobDescription": "<p>Build <b>LLM</b> systems.</p>"}}
        calls = []

        def get(url):
            calls.append(url)
            return detail if "position_details" in url else (page1 if "start=0" in url else page2)
        with mock.patch.object(eightfold, "get_json", side_effect=get):
            result = eightfold.fetch(board("eightfold", self.KEY), CTX)
        searches = [parse_qs(urlsplit(u).query) for u in calls if "/search" in u]
        self.assertEqual([q["start"] for q in searches], [["0"], ["10"]])
        self.assertEqual(searches[0]["location"], ["United States"])
        self.assertEqual(searches[0]["domain"], ["example.com"])
        self.assertEqual(sum("position_details" in u for u in calls), 2)        # the two machine-learning titles
        self.assertEqual(result.n_raw, 12)
        job = next(i for i in result.items if i["ext_id"] == "12")
        self.assertEqual(job["description"], "Build LLM systems.")
        self.assertEqual(job["url"], "https://apply.careers.example.com/careers/job/12?domain=example.com")
        self.assertEqual(job["posted_at"], "2026-10-06T01:08:25Z")
        self.assertTrue(job["is_us"])
        self.assertEqual(job["scopes"], ["term:machine learning"])

    def test_v2_uses_t_create_and_the_job_endpoint(self):
        listing = {"count": 1, "positions": [{"id": 7, "name": "Machine Learning Scientist", "location": "Remote, United States",
                                              "locations": ["Remote, United States"], "t_create": 1790553600,
                                              "canonicalPositionUrl": "https://jobs.example.net/careers/job/7"}]}
        with mock.patch.object(eightfold, "get_json", side_effect=[listing, {"job_description": "Spark"}]) as get:
            result = eightfold.fetch(board("eightfold", "jobs.example.net/example.com/v2"), CTX)
        self.assertIn("/api/apply/v2/jobs/7?domain=example.com", get.call_args_list[1].args[0])
        self.assertEqual(result.items[0]["posted_at"], "2026-09-28T00:00:00Z")
        self.assertEqual(result.items[0]["url"], "https://jobs.example.net/careers/job/7")

    def test_a_renamed_list_is_schema_drift(self):
        with mock.patch.object(eightfold, "get_json", return_value={"data": {"jobs": []}}):
            result = eightfold.fetch(board("eightfold", self.KEY), CTX)
        self.assertTrue(result.error.startswith(SCHEMA_ERROR))

    def test_an_unchanged_match_still_without_its_jd_gets_it_now(self):
        # a detail refused once (Microsoft 429s) was never asked again while the listing stayed the same
        listing = {"data": {"count": 2, "positions": [pcsx_position(1, "ML Engineer"), pcsx_position(2, "ML Engineer")]}}
        detail = {"data": {"jobDescription": "<p>Ship models.</p>"}}
        ctx = Ctx(search_terms=("machine learning",), unchanged=lambda uid, fp: True,
                  needs_detail=lambda uid: uid.endswith(":1"))
        with mock.patch.object(eightfold, "get_json", side_effect=lambda u: detail if "position_details" in u else listing) as get:
            result = eightfold.fetch(board("eightfold", self.KEY), ctx)
        self.assertEqual(sum("position_details" in c.args[0] for c in get.call_args_list), 1)
        by_id = {i["ext_id"]: i for i in result.items}
        self.assertEqual(by_id["1"]["description"], "Ship models.")
        self.assertTrue(by_id["2"]["fast"])

    def test_a_429_after_some_terms_keeps_what_was_collected(self):
        listing = {"data": {"count": 1, "positions": [pcsx_position(1, "ML Engineer")]}}
        ctx = Ctx(matches=lambda t: False, search_terms=("machine learning", "llm"))
        with mock.patch.object(eightfold, "get_json", side_effect=[listing, HttpError("u", 429, "Too Many")]):
            result = eightfold.fetch(board("eightfold", self.KEY), ctx)
        self.assertEqual((result.http_status, result.error, result.truncated), (200, None, True))
        self.assertEqual([i["ext_id"] for i in result.items], ["1"])
        self.assertIn("429", result.notes[0])

    def test_a_429_before_anything_is_a_failure(self):
        with mock.patch.object(eightfold, "get_json", side_effect=HttpError("u", 429, "Too Many")):
            result = eightfold.fetch(board("eightfold", self.KEY), CTX)
        self.assertEqual(result.http_status, 429)

    def test_a_malformed_posting_is_a_parse_error_not_a_lost_board(self):
        listing = {"data": {"count": "2", "positions": [pcsx_position(1, "ML Engineer"), {"name": "no id"}, "junk"]}}
        with mock.patch.object(eightfold, "get_json", return_value=listing):
            result = eightfold.fetch(board("eightfold", self.KEY), Ctx(matches=lambda t: False, search_terms=("x",)))
        self.assertEqual(([i["ext_id"] for i in result.items], result.n_parse_err, result.error), (["1"], 2, None))

    def test_a_malformed_detail_keeps_the_posting(self):
        listing = {"data": {"count": 1, "positions": [pcsx_position(1, "Machine Learning Engineer")]}}
        with mock.patch.object(eightfold, "get_json", side_effect=[listing, {"data": ["not", "a", "dict"]}]):
            result = eightfold.fetch(board("eightfold", self.KEY), CTX)
        self.assertEqual((len(result.items), result.items[0]["description"]), (1, ""))
        self.assertTrue(result.notes)

    def test_a_millisecond_timestamp_does_not_crash(self):
        pos = {**pcsx_position(1, "ML Engineer"), "postedTs": 1791248905000000}
        with mock.patch.object(eightfold, "get_json", return_value={"data": {"count": 1, "positions": [pos]}}):
            result = eightfold.fetch(board("eightfold", self.KEY), Ctx(matches=lambda t: False, search_terms=("x",)))
        self.assertEqual((len(result.items), result.items[0]["posted_at"]), (1, None))

    def test_a_failed_detail_is_a_note_not_a_failure(self):
        listing = {"data": {"count": 1, "positions": [pcsx_position(1, "Machine Learning Engineer")]}}
        with mock.patch.object(eightfold, "get_json", side_effect=[listing, HttpError("u", 500, "boom")]):
            result = eightfold.fetch(board("eightfold", self.KEY), CTX)
        self.assertEqual((len(result.items), result.items[0]["description"]), (1, ""))
        self.assertEqual(len(result.notes), 1)


def phenom_job(job_id: str, title: str, country: str) -> dict:
    return {"jobId": job_id, "jobSeqNo": f"SEQ{job_id}", "reqId": job_id, "title": title,
            "postedDate": "2026-09-14T20:03:36.000+0000", "location": f"Chicago, Illinois, {country}",
            "country": country, "applyUrl": f"https://ual-pro.taleo.net/careersection/1/jobdetail.ftl?job={job_id}"}


class PhenomTest(unittest.TestCase):
    KEY = "careers.example.com/EXUS/page16/us"

    def test_keyword_search_drops_non_us_and_keeps_the_apply_link(self):
        listing = {"refineSearch": {"totalHits": 2, "data": {"jobs": [
            phenom_job("W1", "Machine Learning Engineering Manager", "United States"),
            phenom_job("G2", "Data Scientist - Machine Learning", "India")]}}}
        detail = {"jobDetail": {"data": {"job": {"description": "<p>PyTorch</p>"}}}}
        with mock.patch.object(phenom, "post_json", side_effect=[listing, detail]) as post:
            result = phenom.fetch(board("phenom", self.KEY), CTX)
        sent = post.call_args_list[0].args[1]
        self.assertEqual((sent["keywords"], sent["refNum"], sent["pageId"], sent["country"]),
                         ("machine learning", "EXUS", "page16", "us"))
        self.assertEqual([i["ext_id"] for i in result.items], ["W1"])            # India dropped before any detail
        self.assertEqual(post.call_count, 2)
        job = result.items[0]
        self.assertEqual(job["description"], "PyTorch")
        self.assertTrue(job["apply_url"].startswith("https://ual-pro.taleo.net/"))
        self.assertEqual(job["url"], "https://careers.example.com/us/en/job/W1")
        self.assertEqual(job["posted_at"], "2026-09-14T20:03:36Z")

    def test_missing_refine_search_is_schema_drift(self):
        with mock.patch.object(phenom, "post_json", return_value={"other": {}}):
            self.assertTrue(phenom.fetch(board("phenom", self.KEY), CTX).error.startswith(SCHEMA_ERROR))


class IcimsTest(unittest.TestCase):
    def test_one_us_query_per_term_with_descriptions_in_the_list(self):
        payload = {"totalCount": 1, "jobs": [{"data": {
            "slug": "477916", "req_id": "477916", "title": "Data Scientist", "posted_date": "2026-10-06T00:00:00+0000",
            "full_location": "Purchase, New York", "country_code": "US", "description": "<p>SQL</p>",
            "qualifications": "<ul><li>Python</li></ul>", "apply_url": "https://careers-x.icims.com/jobs/477916/login"}}]}
        with mock.patch.object(icims, "get_json", return_value=payload) as get:
            result = icims.fetch(board("icims", "www.example-jobs.com"), CTX)
        query = parse_qs(urlsplit(get.call_args.args[0]).query)
        self.assertEqual((query["keywords"], query["country"]), (["machine learning"], ["United States"]))
        self.assertEqual(get.call_count, 1)
        job = result.items[0]
        self.assertIn("SQL", job["description"])
        self.assertIn("Python", job["description"])
        self.assertEqual(job["url"], "https://www.example-jobs.com/jobs/477916")
        self.assertTrue(job["is_us"])


class WorkableTest(unittest.TestCase):
    PAYLOAD = {"name": "Acme", "jobs": [{
        "shortcode": "AB12", "title": "ML Engineer", "published_on": "2026-07-30", "telecommuting": True,
        "url": "https://apply.workable.com/j/AB12", "application_url": "https://apply.workable.com/j/AB12/apply",
        "locations": [{"city": "Austin", "region": "Texas", "country": "United States", "countryCode": "US",
                       "hidden": False}], "description": "<p>LLMs</p>", "employment_type": "Full-time"}]}

    def test_whole_board_in_one_call_day_precision(self):
        with mock.patch.object(workable, "get_json", return_value=self.PAYLOAD) as get:
            result = workable.fetch(board("workable", "acme"), CTX)
        self.assertEqual(get.call_args.args[0], "https://apply.workable.com/api/v1/widget/accounts/acme?details=true")
        job = result.items[0]
        self.assertEqual((job["posted_at"], job["precision"], job["workplace"]), ("2026-07-30T00:00:00Z", "day", "remote"))
        self.assertEqual((job["location"], job["country"], job["is_us"]), ("Austin, Texas, United States", "US", True))

    def test_the_account_slug_is_quoted_into_one_path_segment(self):
        with mock.patch.object(workable, "get_json", return_value={"jobs": []}) as get:
            workable.fetch(board("workable", "../x"), CTX)
        self.assertEqual(get.call_args.args[0], "https://apply.workable.com/api/v1/widget/accounts/..%2Fx?details=true")

    def test_unchanged_postings_are_fast(self):
        ctx = Ctx(unchanged=lambda uid, fp: True)
        with mock.patch.object(workable, "get_json", return_value=self.PAYLOAD):
            self.assertTrue(workable.fetch(board("workable", "acme"), ctx).items[0]["fast"])


class RegistryTest(unittest.TestCase):
    def test_config_builds_multi_part_keys_and_refuses_missing_parts(self):
        got = config.validate_company({"name": "Microsoft", "ats": "eightfold", "token": "apply.careers.microsoft.com",
                                       "domain": "microsoft.com", "api": "pcsx"})
        self.assertEqual(got["board"], "eightfold:apply.careers.microsoft.com/microsoft.com/pcsx")
        got = config.validate_company({"name": "United", "ats": "phenom", "token": "careers.united.com",
                                       "ref": "UAIUADUS", "page": "page16", "site": "us"})
        self.assertEqual(got["key"], "careers.united.com/UAIUADUS/page16/us")
        with self.assertRaises(config.ConfigError):
            config.validate_company({"name": "X", "ats": "eightfold", "token": "x.example", "api": "pcsx"})

    def test_hosts_and_robots_urls(self):
        ef = board("eightfold", "apply.careers.microsoft.com/microsoft.com/pcsx")
        self.assertEqual(fetch.host_of(ef), "apply.careers.microsoft.com")
        self.assertEqual(fetch.robots_urls(ef), ["https://apply.careers.microsoft.com/api/pcsx/search"])
        self.assertEqual(fetch.robots_urls(board("eightfold", "jobs.n.net/n.com/v2")),
                         ["https://jobs.n.net/api/apply/v2/jobs"])
        self.assertEqual(fetch.robots_urls(board("icims", "www.pepsicojobs.com")), ["https://www.pepsicojobs.com/api/jobs"])
        self.assertEqual(fetch.host_of(board("workable", "acme")), "apply.workable.com")

    def test_search_boards_never_count_misses_but_workable_does(self):
        self.assertTrue({"eightfold", "phenom", "icims"} <= store.SEARCH_ATS)
        self.assertNotIn("workable", store.SEARCH_ATS)

    def test_workable_careers_urls_are_detected(self):
        self.assertEqual(detect("https://apply.workable.com/huggingface/"), {"ats": "workable", "token": "huggingface"})
        self.assertIsNone(detect("https://apply.workable.com/j/F4C096B22E"))


if __name__ == "__main__":
    unittest.main()
