"""Offline contract tests for the 5 ATS adapters (recorded live payloads) and careers-URL detection."""
import json
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from job_hunt_agent.ats import Ctx, ashby, detect, greenhouse, lever, smartrecruiters, workday
from job_hunt_agent.net import HttpError, Response

FIXTURES = Path(__file__).parent / "fixtures"
REQUIRED = {"uid", "title", "company", "location", "url", "apply_url", "posted_at", "precision", "description"}
CTX = Ctx(matches=lambda title: True, search_terms=("machine learning engineer",), today=date(2026, 10, 2))


def load(name: str):
    return json.loads((FIXTURES / name).read_text())


def cond(payload, etag='W/"e1"'):
    """A get_json_cond answer: (200 response with validators, parsed payload)."""
    return Response(200, b"", etag, None), payload


class AdapterContractTest(unittest.TestCase):
    def assert_jobs(self, result, ats: str, precision: str):
        self.assertIsNone(result.error)
        self.assertEqual(result.n_parse_err, 0)
        self.assertTrue(result.items)
        for job in result.items:
            self.assertTrue(REQUIRED <= job.keys())
            self.assertTrue(job["uid"].startswith(f"{ats}:"))
            self.assertEqual(job["precision"], precision)
            self.assertRegex(job["posted_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
            self.assertTrue(job["apply_url"].startswith("https://"))

    def _greenhouse(self, payload, ctx=CTX):
        listing = {"jobs": [{k: v for k, v in j.items() if k != "content"} for j in payload["jobs"]]}
        details = {str(j["id"]): j for j in payload["jobs"]}
        with mock.patch.object(greenhouse, "get_json_cond", return_value=cond(listing)), \
                mock.patch.object(greenhouse, "get_json",
                                  side_effect=lambda url: details[url.rsplit("/", 1)[1]]) as detail:
            return greenhouse.fetch({"key": "anthropic", "company": "Anthropic"}, ctx), detail

    def test_greenhouse_unescapes_double_escaped_html(self):
        result, _ = self._greenhouse(load("greenhouse/anthropic.json"))
        self.assert_jobs(result, "greenhouse", "exact")
        self.assertNotIn("&lt;", result.items[0]["description"])
        self.assertNotIn("<div", result.items[0]["description"])
        self.assertEqual(result.etag, 'W/"e1"')

    def test_greenhouse_lists_without_content_and_details_only_new_matches(self):
        payload = load("greenhouse/anthropic.json")
        ctx = Ctx(matches=lambda t: t == payload["jobs"][0]["title"], needs_detail=lambda uid: True)
        result, detail = self._greenhouse(payload, ctx)
        self.assertEqual(detail.call_count, sum(j["title"] == payload["jobs"][0]["title"] for j in payload["jobs"]))
        self.assertEqual(result.items[-1]["description"] if len(result.items) > 1 else "", "")

    def test_greenhouse_caps_details_per_run(self):
        payload = load("greenhouse/anthropic.json")
        payload = {"jobs": [dict(payload["jobs"][0], id=1000 + i) for i in range(greenhouse.MAX_DETAILS + 5)]}
        _, detail = self._greenhouse(payload)
        self.assertEqual(detail.call_count, greenhouse.MAX_DETAILS)

    def test_unchanged_postings_become_fast_items(self):
        payload = load("greenhouse/anthropic.json")
        ctx = Ctx(unchanged=lambda uid, fp: True, needs_detail=lambda uid: False)
        result, detail = self._greenhouse(payload, ctx)
        detail.assert_not_called()
        self.assertTrue(all(i.get("fast") for i in result.items))
        self.assertIsInstance(result.items[0]["raw_fp"], int)

    def test_an_unchanged_match_still_without_its_jd_gets_it_now(self):
        # past the first run's MAX_DETAILS, an unchanged posting was a fast item forever, so its JD never came
        # (SpaceX: 62 matched jobs without a JD for 3 days)
        payload = load("greenhouse/anthropic.json")
        first = payload["jobs"][0]
        ctx = Ctx(matches=lambda t: t == first["title"], unchanged=lambda uid, fp: True,
                  needs_detail=lambda uid: uid.endswith(f":{first['id']}"))
        result, detail = self._greenhouse(payload, ctx)
        self.assertEqual(detail.call_count, 1)
        got = next(i for i in result.items if i["ext_id"] == str(first["id"]))
        self.assertFalse(got.get("fast"))
        self.assertTrue(got["description"])
        self.assertTrue(all(i.get("fast") for i in result.items if i["ext_id"] != str(first["id"])))

    def test_304_is_not_modified_not_an_error(self):
        for module in (greenhouse, lever, ashby):
            with mock.patch.object(module, "get_json_cond", return_value=(Response(304, b"", 'W/"e1"'), None)):
                result = module.fetch({"key": "x", "company": "X", "etag": 'W/"e1"'}, CTX)
            self.assertTrue(result.not_modified)
            self.assertIsNone(result.error)
            self.assertEqual(result.http_status, 304)

    def test_greenhouse_falls_back_to_updated_precision(self):
        payload = load("greenhouse/anthropic.json")
        payload["jobs"][0]["first_published"] = None
        result, _ = self._greenhouse(payload)
        self.assertEqual(result.items[0]["precision"], "updated")

    def test_lever_uses_created_at_and_salary_range(self):
        with mock.patch.object(lever, "get_json_cond", return_value=cond(load("lever/palantir.json"))):
            result = lever.fetch({"key": "palantir", "company": "Palantir"}, CTX)
        self.assert_jobs(result, "lever", "exact")

    def test_ashby_skips_unlisted_postings(self):
        payload = load("ashby/openai.json")
        payload["jobs"][0]["isListed"] = False
        with mock.patch.object(ashby, "get_json_cond", return_value=cond(payload)):
            result = ashby.fetch({"key": "openai", "company": "OpenAI"}, CTX)
        self.assert_jobs(result, "ashby", "exact")
        self.assertEqual(len(result.items), len(payload["jobs"]) - 1)

    def test_smartrecruiters_fetches_detail_for_matched_titles(self):
        responses = [load("smartrecruiters/list.json")] + [load("smartrecruiters/detail.json")] * 3
        with mock.patch.object(smartrecruiters, "get_json", side_effect=responses):
            result = smartrecruiters.fetch({"key": "ServiceNow", "company": "ServiceNow"}, CTX)
        self.assert_jobs(result, "smartrecruiters", "exact")
        self.assertGreater(len(result.items[0]["description"]), 200)

    def test_workday_an_unchanged_fresh_match_without_its_jd_gets_it_now(self):
        # a detail refused once (429) was not asked again the same day while the list entry stayed the same
        listing = {"total": 2, "jobPostings": [
            {"title": "Machine Learning Engineer", "postedOn": "Posted Today", "externalPath": f"/job/x/ML_R000{i}",
             "locationsText": "Austin, TX"} for i in (1, 2)]}
        wanted = "R0001"
        ctx = Ctx(unchanged=lambda uid, fp: True, needs_detail=lambda uid: uid.endswith(f":{wanted}"))
        with mock.patch.object(workday, "post_json", return_value=listing), \
             mock.patch.object(workday, "get_json", return_value=load("workday/detail.json")) as detail:
            result = workday.fetch({"key": "nvidia/wd5/NVIDIAExternalCareerSite", "company": "NVIDIA"}, ctx)
        self.assertEqual(detail.call_count, 1)
        self.assertEqual([i["ext_id"] for i in result.items if not i.get("fast")], [wanted])

    def test_workday_lists_then_details_only_fresh_matches(self):
        listing = load("workday/list.json")
        with mock.patch.object(workday, "post_json", return_value=listing), \
             mock.patch.object(workday, "get_json", return_value=load("workday/detail.json")) as detail:
            result = workday.fetch({"key": "nvidia/wd5/NVIDIAExternalCareerSite", "company": "NVIDIA"}, CTX)
        self.assert_jobs(result, "workday", "day")
        fresh = [p for p in listing["jobPostings"] if (workday.parse_posted_on(p["postedOn"])[0] or 99) <= 2]
        self.assertEqual(detail.call_count, len(fresh))

    def test_http_error_becomes_failed_result(self):
        with mock.patch.object(lever, "get_json_cond", side_effect=HttpError("u", 404, "Not Found")):
            result = lever.fetch({"key": "nope", "company": "Nope"}, CTX)
        self.assertEqual(result.http_status, 404)
        self.assertEqual(result.items, [])


class NewPassesTest(unittest.TestCase):
    """Decision 005: Workday newest-first list pass and the Amazon adapter (recorded shapes, no network)."""

    def page(self, ages, total=100):
        return {"total": total, "jobPostings": [
            {"title": f"Role {i}", "postedOn": f"Posted {a} Days Ago" if a > 1 else "Posted Today",
             "externalPath": f"/job/x/Role_R{i:04d}", "locationsText": "Austin, TX"} for i, a in ages]}

    def test_workday_list_pass_stops_at_a_page_older_than_the_cutoff(self):
        pages = [self.page([(i, 0) for i in range(20)]), self.page([(20 + i, 5) for i in range(20)]),
                 self.page([(40 + i, 9) for i in range(20)])]
        board = {"key": "acme/wd5/Ext", "company": "Acme", "task": "list", "last_ok_at": "2026-10-01T10:00:00Z"}
        with mock.patch.object(workday, "post_json", side_effect=pages) as posted, \
                mock.patch.object(workday, "get_json", return_value={}):
            result = workday.fetch(board, Ctx(matches=lambda t: False, today=date(2026, 10, 2)))
        self.assertEqual(posted.call_count, 2)                      # page 2 is all older than 2 days: stop
        self.assertEqual((len(result.items), result.total), (40, 100))
        self.assertTrue(all("scopes" not in i for i in result.items))

    def test_workday_list_pass_never_pages_past_the_first_total(self):
        first = self.page([(i, 0) for i in range(20)], total=25)
        second = self.page([(20 + i, 0) for i in range(5)], total=0)
        board = {"key": "acme/wd5/Ext", "company": "Acme", "task": "list"}
        with mock.patch.object(workday, "post_json", side_effect=[first, second, first]) as posted:
            workday.fetch(board, Ctx(matches=lambda t: False, today=date(2026, 10, 2)))
        self.assertEqual(posted.call_count, 2)

    def test_amazon_search_is_day_precision_and_us(self):
        from job_hunt_agent.ats import amazon
        payload = {"jobs": [{"id_icims": "10568249", "title": "Machine Learning Engineer",
                             "normalized_location": "Seattle, Washington, USA", "posted_date": "October  2, 2026",
                             "job_path": "/en/jobs/10568249/x", "description": "Build models.",
                             "url_next_step": "https://account.amazon.jobs/jobs/10568249/apply"}]}
        with mock.patch.object(amazon, "get_json", return_value=payload):
            result = amazon.fetch({"key": "amazon", "company": "Amazon"}, CTX)
        item = result.items[0]
        self.assertEqual((item["uid"], item["posted_at"], item["precision"], item["is_us"]),
                         ("amazon:amazon:10568249", "2026-10-02T00:00:00Z", "day", True))
        self.assertEqual(item["scopes"], ["term:machine learning engineer"])

    def test_amazon_qualifications_keep_their_headings_so_preferred_terms_weigh_half(self):
        from job_hunt_agent.ats import amazon
        from job_hunt_agent.resume import ats
        payload = {"jobs": [{"id_icims": "1", "title": "Applied Scientist", "normalized_location": "Seattle, WA, USA",
                             "posted_date": "October  2, 2026", "job_path": "/en/jobs/1/x", "description": "Build.",
                             "basic_qualifications": "- PyTorch", "preferred_qualifications": "- Kubernetes"}]}
        with mock.patch.object(amazon, "get_json", return_value=payload):
            text = amazon.fetch({"key": "amazon", "company": "Amazon"}, CTX).items[0]["description"]
        self.assertIn("Basic Qualifications\n- PyTorch", text)
        self.assertEqual({t.name: t.weight for t in ats.extract(text)}, {"PyTorch": 1.0, "Kubernetes": 0.5})

    def test_amazon_line_breaks_are_text_not_tags(self):
        # all 340 open Amazon JDs showed literal "<br/>" in the job detail, Copy JD and resumegen
        from job_hunt_agent.ats import amazon
        from job_hunt_agent.resume import ats
        payload = {"jobs": [{"id_icims": "1", "title": "Applied Scientist", "normalized_location": "Seattle, WA, USA",
                             "posted_date": "October  2, 2026", "job_path": "/en/jobs/1/x",
                             "description": "Build robots. <br/>Ship models &amp; tools.",
                             "basic_qualifications": "- 3+ years of Python<br/>- PyTorch",
                             "preferred_qualifications": "- Docker<br/>- Kubernetes"}]}
        with mock.patch.object(amazon, "get_json", return_value=payload):
            text = amazon.fetch({"key": "amazon", "company": "Amazon"}, CTX).items[0]["description"]
        self.assertNotIn("<br", text)
        self.assertIn("Ship models & tools.", text)
        self.assertIn("Basic Qualifications\n- 3+ years of Python", text)
        self.assertEqual({t.name: t.weight for t in ats.extract(text)},
                         {"Python": 1.0, "PyTorch": 1.0, "Docker": 0.5, "Kubernetes": 0.5})


class WorkdayParsingTest(unittest.TestCase):
    def test_posted_on_buckets(self):
        self.assertEqual(workday.parse_posted_on("Posted Today"), (0, False))
        self.assertEqual(workday.parse_posted_on("Posted Yesterday"), (1, False))
        self.assertEqual(workday.parse_posted_on("Posted 3 Days Ago"), (3, False))
        self.assertEqual(workday.parse_posted_on("Posted 30+ Days Ago"), (30, True))
        self.assertEqual(workday.parse_posted_on(""), (None, False))

    def test_ext_id_prefers_req_suffix(self):
        self.assertEqual(workday.ext_id({"externalPath": "/job/US-CA-Santa-Clara/ML-Engineer_JR2024455"}), "JR2024455")
        self.assertEqual(workday.ext_id({"externalPath": "/job/x/Data-Scientist_R-12345"}), "R-12345")

    def test_location_falls_back_to_bullets_then_path(self):
        self.assertEqual(workday.list_location({"locationsText": "Santa Clara, CA"}), "Santa Clara, CA")
        self.assertEqual(workday.list_location({"locationsText": "", "bulletFields": ["R00347102", "Ebene"]}),
                         "Ebene")
        self.assertEqual(workday.list_location({"bulletFields": ["JR2024455"],
                                                "externalPath": "/job/Bengaluru/AI-ML-Engineer_JR2024455"}),
                         "Bengaluru")
        self.assertEqual(workday.list_location({}), "")


class DetectTest(unittest.TestCase):
    def test_detects_every_supported_careers_url(self):
        cases = {
            "https://boards.greenhouse.io/anthropic": {"ats": "greenhouse", "token": "anthropic"},
            "https://job-boards.greenhouse.io/databricks/jobs/123": {"ats": "greenhouse", "token": "databricks"},
            "https://boards.greenhouse.io/embed/job_board?for=figma": {"ats": "greenhouse", "token": "figma"},
            "https://jobs.lever.co/palantir": {"ats": "lever", "token": "palantir"},
            "https://jobs.ashbyhq.com/openai/abc": {"ats": "ashby", "token": "openai"},
            "https://jobs.smartrecruiters.com/ServiceNow": {"ats": "smartrecruiters", "token": "ServiceNow"},
            "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite": {
                "ats": "workday", "token": "nvidia", "wd": "wd5", "site": "NVIDIAExternalCareerSite"},
            "https://cvshealth.wd1.myworkdayjobs.com/en-us/CVS_Health_Careers/job/x": {
                "ats": "workday", "token": "cvshealth", "wd": "wd1", "site": "CVS_Health_Careers"},
            "https://wd5.myworkdaysite.com/en-US/recruiting/acme/External": {
                "ats": "workday", "token": "acme", "wd": "wd5", "site": "External"},
            "https://jobs.lever.co/Palantir/abc": {"ats": "lever", "token": "palantir"},
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(detect(url), expected)

    def test_rejects_reserved_paths(self):
        self.assertIsNone(detect("https://jobs.ashbyhq.com/api/non-user-graphql"))

    def test_rejects_unknown_hosts(self):
        self.assertIsNone(detect("https://www.linkedin.com/jobs/view/1"))
        self.assertIsNone(detect("https://evil.example.com/boards.greenhouse.io/x"))


if __name__ == "__main__":
    unittest.main()


class WorkplaceTest(unittest.TestCase):
    def test_workplace_values_are_normalized(self):
        from job_hunt_agent.ats.common import _workplace
        self.assertEqual([_workplace(v) for v in ("On-site", "ONSITE", "Hybrid (travel-required)", "Remote", "", None)],
                         ["onsite", "onsite", "hybrid", "remote", None, None])

    def test_v5_migration_normalizes_stored_rows_like_the_adapters(self):
        import tempfile
        from pathlib import Path
        from job_hunt_agent import db
        from job_hunt_agent.ats.common import _workplace
        raw = ["On-site", "on_site", "Hybrid (travel-required)", " Remote - US", "onsite", "Flexible"]
        with tempfile.TemporaryDirectory() as tmp:
            conn = db.connect(Path(tmp) / "t.db")
            db.sync_boards(conn, [{"board": "lever:acme", "ats": "lever", "key": "acme", "company": "Acme"}])
            with db.tx(conn):
                for i, value in enumerate(raw):
                    conn.execute("INSERT INTO jobs(uid, board, ext_id, company, title, workplace, precision, "
                                 "content_hash, repost_key, first_seen, last_seen) VALUES (?, 'lever:acme', ?, "
                                 "'Acme', 'T', ?, 'unknown', 'h', 'k', '2026-10-01T00:00:00Z', "
                                 "'2026-10-01T00:00:00Z')", (f"u{i}", str(i), value))
                conn.execute(db.MIGRATIONS[5][0])
            stored = [r[0] for r in conn.execute("SELECT workplace FROM jobs ORDER BY uid")]
            conn.close()
        self.assertEqual(stored[:5], [_workplace(v) for v in raw[:5]])
        self.assertEqual(stored[5], "Flexible")                         # unknown wording is left as it was


class SchemaDriftTest(unittest.TestCase):
    """A renamed list key is drift, never an empty board (release review C1)."""

    def test_renamed_keys_are_schema_errors_and_empty_lists_are_real(self):
        from job_hunt_agent.ats import ashby, greenhouse, lever
        from job_hunt_agent.ats.common import SCHEMA_ERROR
        board = {"board": "x:acme", "key": "acme", "company": "Acme"}
        cases = [(greenhouse, {"postings": []}, {"jobs": []}), (ashby, {"items": []}, {"jobs": []}),
                 (lever, {"data": []}, [])]
        for module, drifted, empty in cases:
            with mock.patch.object(module, "get_json_cond", return_value=cond(drifted)):
                self.assertTrue(module.fetch(board, CTX).error.startswith(SCHEMA_ERROR), module.__name__)
            with mock.patch.object(module, "get_json_cond", return_value=cond(empty)):
                result = module.fetch(board, CTX)
            self.assertEqual((result.error, result.items), (None, []), module.__name__)
