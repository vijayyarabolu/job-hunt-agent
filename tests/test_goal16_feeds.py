"""GOAL16 one-request feeds on responses recorded live on 2026-10-08 (trimmed): Recruitee (ABRIO GmbH), Breezy HR
(DrHouse), Pinpoint (ISG), HiBob (Qogita) and JobScore (JobScore). Each board is one list request (a full list: an
absent job is a miss); Breezy alone has no description in its list, so a title-matched US posting costs one page."""
import json
import threading
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from job_hunt_agent import discover_cc, discover_more, discover_verify, fetch, net, registry, tiers
from job_hunt_agent.ats import ADAPTERS, Ctx, breezy, detect, hibob, jobscore, pinpoint, recruitee
from job_hunt_agent.ats.common import SCHEMA_ERROR, raw_fp

FIXTURES = Path(__file__).parent / "fixtures"


def load(path: str):
    return json.loads((FIXTURES / path).read_text())


def ctx_for(*words: str, unchanged=None) -> Ctx:
    matches = lambda title: any(w in title.lower() for w in words)  # noqa: E731
    return Ctx(matches=matches, today=date(2026, 10, 8), **({"unchanged": unchanged} if unchanged else {}))


def board(ats: str, key: str, company: str = "Acme") -> dict:
    return {"board": f"{ats}:{key}", "ats": ats, "key": key, "company": company}


def run(module, ats: str, key: str, answers: dict, ctx: Ctx, getter: str = "get_json"):
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs.get("headers")))
        return answers[url]
    with mock.patch.object(module, getter, side_effect=get):
        return module.fetch(board(ats, key), ctx), calls


class RecruiteeTest(unittest.TestCase):
    URL = "https://abriogmbh.recruitee.com/api/offers/"

    def test_one_request_reads_every_offer_with_its_description(self):
        result, calls = run(recruitee, "recruitee", "abriogmbh", {self.URL: load("recruitee/offers.json")},
                            ctx_for("ux/ui"))
        self.assertEqual([c[0] for c in calls], [self.URL])
        self.assertEqual((result.n_raw, len(result.items)), (3, 3))
        job = next(i for i in result.items if "UX/UI" in i["title"])
        self.assertEqual((job["country"], job["is_us"], job["workplace"]), ("DE", False, "hybrid"))
        self.assertEqual((job["posted_at"], job["precision"]), ("2026-09-30T08:41:36Z", "exact"))
        self.assertEqual(job["url"], "https://abriogmbh.recruitee.com/o/werkstudent-uxui-design-mwd-1")
        self.assertIn("Frankfurt", job["location"])
        self.assertGreater(len(job["description"]), 200)

    def test_an_unchanged_offer_stays_on_the_fast_path(self):
        first = load("recruitee/offers.json")["offers"][0]
        ctx = ctx_for("ux/ui", unchanged=lambda uid, fp: fp == raw_fp(first))
        result, _ = run(recruitee, "recruitee", "abriogmbh", {self.URL: load("recruitee/offers.json")}, ctx)
        self.assertTrue(next(i for i in result.items if i["ext_id"] == str(first["id"])).get("fast"))

    def test_a_renamed_list_key_is_schema_drift(self):
        result, _ = run(recruitee, "recruitee", "abriogmbh", {self.URL: {"jobs": []}}, ctx_for("x"))
        self.assertTrue(result.error.startswith(SCHEMA_ERROR))


class BreezyTest(unittest.TestCase):
    URL = "https://drhouse-inc.breezy.hr/json"

    def test_list_then_json_ld_for_the_matched_us_position(self):
        listed = load("breezy/list.json")
        page = (FIXTURES / "breezy/job.html").read_text()
        with mock.patch.object(breezy, "get_json", return_value=listed) as get_json, \
                mock.patch.object(breezy, "get_text", return_value=page) as get_text:
            ctx = ctx_for("physician", unchanged=lambda uid, fp: uid.endswith(listed[1]["id"]))
            result = breezy.fetch(board("breezy", "drhouse-inc", "DrHouse"), ctx)
        get_json.assert_called_once_with(self.URL)
        self.assertEqual([c.args[0] for c in get_text.call_args_list], [listed[0]["url"]])
        job = next(i for i in result.items if i["ext_id"] == listed[0]["id"])
        self.assertEqual((job["country"], job["is_us"]), ("US", True))
        self.assertIn("Minneapolis, MN", job["location"])
        self.assertIn("Saint Paul, MN", job["location"])
        self.assertEqual((job["posted_at"], job["precision"]), ("2026-09-22T13:43:23Z", "exact"))
        self.assertGreater(len(job["description"]), 200)

    def test_survey_names_the_company(self):
        with mock.patch.object(breezy, "get_json", return_value=load("breezy/list.json")):
            found = breezy.survey("drhouse-inc")
        self.assertEqual((found["n_jobs"], found["name"]), (2, "DrHouse, Inc."))
        self.assertEqual(found["rows"][0][2], "US")


class PinpointTest(unittest.TestCase):
    URL = "https://isginc.pinpointhq.com/postings.json"

    def test_one_request_reads_every_posting_undated_with_pay(self):
        result, calls = run(pinpoint, "pinpoint", "isginc", {self.URL: load("pinpoint/postings.json")},
                            ctx_for("surface water"))
        self.assertEqual([c[0] for c in calls], [self.URL])
        self.assertEqual(result.n_raw, 3)
        job = next(i for i in result.items if "Surface Water" in i["title"])
        self.assertTrue(job["is_us"])
        self.assertEqual((job["posted_at"], job["precision"], job["workplace"]), (None, "unknown", "onsite"))
        self.assertEqual(job["location"], "Bloomington, MN")
        self.assertTrue(job["url"].startswith("https://isginc.pinpointhq.com/en/postings/"))
        self.assertGreater(len(job["description"]), 200)
        civil = next(i for i in result.items if i["location"] == "Mankato, MN")
        self.assertEqual((civil["salary_min"], civil["salary_max"]), (75000.0, 150000.0))

    def test_pinpoint_boards_use_first_seen_freshness(self):
        self.assertIn("pinpoint", tiers.UNDATED_ATS)


class HiBobTest(unittest.TestCase):
    URL = "https://qogita.careers.hibob.com/api/job-ad"

    def test_one_request_with_the_board_referer(self):
        result, calls = run(hibob, "hibob", "qogita", {self.URL: load("hibob/job_ad.json")}, ctx_for("data"))
        self.assertEqual(calls, [(self.URL, {"Referer": "https://qogita.careers.hibob.com/jobs"})])
        job = next(i for i in result.items if i["title"] == "Data Engineer")
        self.assertEqual((job["country"], job["is_us"]), ("Netherlands", False))
        self.assertEqual((job["posted_at"], job["precision"]), ("2026-09-02T06:35:14Z", "exact"))
        self.assertTrue(job["url"].startswith("https://qogita.careers.hibob.com/jobs/"))
        self.assertGreater(len(job["description"]), 200)

    def test_a_us_country_name_is_us(self):
        self.assertEqual(hibob.location({"site": "New York", "country": "United States"}),
                         ("New York, United States", "United States"))


class JobScoreTest(unittest.TestCase):
    URL = "https://careers.jobscore.com/jobs/jobscore/feed.json?sort=date&limit=500"

    def test_one_feed_request_with_full_bodies(self):
        result, calls = run(jobscore, "jobscore", "jobscore", {self.URL: load("jobscore/feed.json")},
                            ctx_for("engineer"))
        self.assertEqual([c[0] for c in calls], [self.URL])
        job = result.items[0]
        self.assertEqual((job["country"], job["is_us"], job["workplace"]), ("BR", False, "remote"))
        self.assertEqual((job["posted_at"], job["precision"]), ("2026-01-20T18:35:42Z", "exact"))
        self.assertTrue(job["url"].startswith("https://careers.jobscore.com/careers/jobscore/jobs/"))
        self.assertFalse(result.truncated)

    def test_jobscore_never_runs_on_the_30_minute_tier(self):
        self.assertEqual((tiers.tier_of("jobscore", 1), tiers.tier_of("jobscore", 3)), (2, 3))
        self.assertEqual(tiers.tier_of("greenhouse", 1), 1)

    def test_survey_names_the_company(self):
        with mock.patch.object(jobscore, "get_json", return_value=load("jobscore/feed.json")):
            self.assertEqual(jobscore.survey("jobscore")["name"], "JobScore")


class FeedWiringTest(unittest.TestCase):
    SYSTEMS = {"recruitee": ("abriogmbh", "abriogmbh.recruitee.com"), "breezy": ("drhouse-inc", "drhouse-inc.breezy.hr"),
               "pinpoint": ("isginc", "isginc.pinpointhq.com"), "hibob": ("qogita", "qogita.careers.hibob.com"),
               "jobscore": ("jobscore", "careers.jobscore.com")}

    def test_adapters_hosts_registry_and_discovery(self):
        for ats, (key, host) in self.SYSTEMS.items():
            self.assertIn(ats, ADAPTERS)
            self.assertIn(ats, registry.FETCHABLE)
            self.assertIn(ats, registry.GOAL16_ATS)
            self.assertIn(ats, discover_more.SURVEYS)
            self.assertIn(ats, discover_cc.PREFIXES)
            self.assertEqual(fetch.host_of(board(ats, key)), host)
            self.assertIn(ats, fetch.LIST_URLS)

    def test_tenants_share_one_vendor_bucket(self):
        self.assertEqual(net.bucket_key("a.breezy.hr"), net.bucket_key("b.breezy.hr"))
        self.assertEqual(net.bucket_key("a.careers.hibob.com"), net.bucket_key("b.careers.hibob.com"))

    def test_detect(self):
        cases = {"https://abriogmbh.recruitee.com/o/werkstudent": ("recruitee", "abriogmbh"),
                 "https://drhouse-inc.breezy.hr/p/280aaa0fbc5701-physician": ("breezy", "drhouse-inc"),
                 "https://isginc.pinpointhq.com/en/postings/8b37": ("pinpoint", "isginc"),
                 "https://qogita.careers.hibob.com/jobs": ("hibob", "qogita"),
                 "https://careers.jobscore.com/careers/jobscore/jobs/senior-x": ("jobscore", "jobscore")}
        for url, (ats, token) in cases.items():
            found = detect(url)
            self.assertEqual((found["ats"], found["token"]), (ats, token), url)
        for url in ("https://www.breezy.hr/pricing", "https://app.recruitee.com/", "https://careers.jobscore.com/"):
            self.assertIsNone(detect(url), url)

    def test_crawl_keys_follow_detect(self):
        self.assertEqual(discover_more.key_from_url("https://isginc.pinpointhq.com/postings.json"),
                         ("pinpoint", "isginc"))
        self.assertEqual(discover_more.key_from_url("https://careers.jobscore.com/careers/acme-co/jobs/x-1"),
                         ("jobscore", "acme-co"))
        self.assertTrue(discover_more.KEY_PATTERNS["jobscore"].match("jobscore"))
        self.assertNotIn("hibob", discover_more.SEED_FILES)


class VerifyLanesTest(unittest.TestCase):
    def test_a_stalled_vendor_never_holds_another_vendors_checks(self):
        released, fast_done, waits = threading.Event(), [], []

        def one(ats, token, now, names):
            if ats == "cornerstone":
                waits.append(released.wait(2))         # a 10 s Crawl-delay vendor, every thread of its own busy
                return discover_verify.Verdict(0), None
            fast_done.append(token)
            if len(fast_done) == 5:
                released.set()
            return discover_verify.Verdict(1, 1), None
        pairs = [("cornerstone", f"t{n}/1") for n in range(4)] + [("gem", f"g{n}") for n in range(5)]
        with mock.patch.object(discover_verify, "_one", side_effect=one), \
                mock.patch.object(discover_verify, "record"):
            counts = discover_verify.verify_all(None, pairs, "seed16", None, workers=2)
        self.assertEqual(waits, [True] * 4, "gem waited behind cornerstone")
        self.assertEqual((counts["gem"]["us_live"], counts["cornerstone"]["checked"]), (5, 4))


if __name__ == "__main__":
    unittest.main()
