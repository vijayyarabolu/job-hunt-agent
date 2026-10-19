"""Crawl discovery after harvest: live checks (faked net), probe rows, registry selection/dedupe, segments, seed
robots parsing, robots.txt rules. No network."""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from job_hunt_agent import (config, db, discover_run, discover_seed, discover_verify as dv, registry, robots, segments,
                     seedlists)
from job_hunt_agent import net
from job_hunt_agent.net import HttpError

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)


class NamesTest(unittest.TestCase):
    def test_clean_name(self):
        self.assertEqual(dv.clean_name("2100 NVIDIA USA"), "NVIDIA")
        self.assertEqual(dv.clean_name("Wolters Kluwer United States Inc."), "Wolters Kluwer")
        self.assertEqual(dv.clean_name("OpenAI Jobs"), "OpenAI")
        self.assertIsNone(dv.clean_name("1234"))

    def test_us_recent_counts_only_us_postings_in_60_days(self):
        rows = [("2026-09-30T10:00:00Z", "Austin, TX", None), ("2026-07-01T10:00:00Z", "Austin, TX", None),
                ("2026-09-30T10:00:00Z", "Berlin, Germany", None), ("2026-09-29T00:00:00Z", None, "US")]
        self.assertEqual(dv.us_recent(rows, NOW), (2, "2026-09-30T10:00:00Z"))

    def test_us_facet_found_in_nested_groups(self):
        facets = [{"facetParameter": "locationMainGroup", "values": [
            {"facetParameter": "locationHierarchy1", "descriptor": "Locations", "values": [
                {"descriptor": "Canada", "id": "ca"}, {"descriptor": "United States of America", "id": "us1"}]}]}]
        self.assertEqual(dv._us_facet(facets), ("locationHierarchy1", "us1"))
        self.assertIsNone(dv._us_facet([{"facetParameter": "x", "values": [{"descriptor": "Canada", "id": "c"}]}]))

    def test_workday_rows_skip_capped_dates(self):
        postings = [{"postedOn": "Posted Today"}, {"postedOn": "Posted 30+ Days Ago"}, {"postedOn": "Posted 3 Days Ago"}]
        self.assertEqual(len(dv.wd_us_rows(postings, NOW, True)), 2)


class ChecksTest(unittest.TestCase):
    def test_greenhouse_check_uses_company_name_and_us_dates(self):
        payload = {"jobs": [{"company_name": "Acme Inc.", "first_published": "2026-09-30T10:00:00-04:00",
                             "location": {"name": "New York, NY"}, "absolute_url": "https://acme.com/careers?gh_jid=1"},
                            {"first_published": "2026-09-30T10:00:00Z", "location": {"name": "London, UK"}}]}
        with mock.patch.object(dv, "get_json", return_value=payload):
            verdict = dv.check_greenhouse("acme", NOW)
        self.assertEqual((verdict.n_jobs, verdict.us60, verdict.name, verdict.website),
                         (2, 1, "Acme", "https://acme.com/careers?gh_jid=1"))

    def test_ashby_reads_identity_from_the_hosted_page_only_when_us_live(self):
        payload = {"jobs": [{"isListed": True, "publishedAt": "2026-09-30T10:00:00Z", "location": "Remote - US",
                             "address": {"postalAddress": {"addressCountry": "United States"}}}]}
        page = '<title>Acme Jobs</title> "publicWebsite":"https://acme.ai/"'
        with mock.patch.object(dv, "get_json", return_value=payload), \
                mock.patch.object(dv, "get_text", return_value=page) as fetched:
            verdict = dv.check_ashby("acme", NOW)
        fetched.assert_called_once_with("https://jobs.ashbyhq.com/acme")
        self.assertEqual((verdict.name, verdict.website, verdict.name_src), ("Acme", "https://acme.ai/", "page"))

    def test_workday_robots_disallowed_site_is_never_admitted(self):
        with mock.patch.object(dv.robots, "allowed", return_value=False), \
                mock.patch.object(dv, "post_json") as posted:
            verdict = dv.check_workday("nvidia/wd5/Research", NOW)
        posted.assert_not_called()
        self.assertEqual((verdict.n_jobs, verdict.name_src), (0, "robots"))

    def test_workday_names_from_list_then_hiring_org_then_slug(self):
        first = {"total": 50, "jobPostings": [{"postedOn": "Posted Today", "externalPath": "/job/x_R1",
                                               "locationsText": "Austin, TX"}], "facets": []}
        with mock.patch.object(dv.robots, "allowed", return_value=True), \
                mock.patch.object(dv, "post_json", return_value=first), \
                mock.patch.object(dv, "get_json", return_value={"hiringOrganization": {"name": "77 Acme Corp USA"}}):
            listed = dv.check_workday("acme/wd1/External", NOW, lambda t: "Acme Corporation")
            org = dv.check_workday("acme/wd1/External", NOW)
        self.assertEqual((listed.name, listed.name_src), ("Acme Corporation", "list"))
        self.assertEqual((org.name, org.name_src, org.us60), ("Acme", "hiring_org", 1))

    def test_transient_errors_are_not_misses(self):
        with mock.patch.object(dv, "check", side_effect=HttpError("u", 429, "slow down")):
            verdict, error = dv._one("greenhouse", "acme", NOW, lambda t: None)
        self.assertIsNone(verdict)
        self.assertIn("429", error)
        with mock.patch.object(dv, "check", side_effect=HttpError("u", 404, "gone")):
            self.assertEqual(dv._one("greenhouse", "acme", NOW, lambda t: None), (dv.Verdict(0), None))


class ProbeRowsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.db")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_record_then_error_keeps_the_verdict_and_schedules_a_retry(self):
        dv.record(self.conn, "greenhouse", "acme", "cc", NOW, dv.Verdict(5, 2, "2026-09-30T00:00:00Z", "Acme", "ats"))
        dv.record(self.conn, "greenhouse", "acme", "cc", NOW, None, "429 slow down")
        row = self.conn.execute("SELECT * FROM discover_probes").fetchone()
        self.assertEqual((row["hit"], row["us60"], row["board_name"], row["error"]), (1, 2, "Acme", "429 slow down"))
        self.assertEqual(dv.todo(self.conn, {"greenhouse": ["acme", "beta"]}, NOW), [("greenhouse", "beta")])

    def test_todo_rechecks_after_ttl_or_on_refresh(self):
        dv.record(self.conn, "lever", "acme", "cc", NOW, dv.Verdict(1))
        self.assertEqual(dv.todo(self.conn, {"lever": ["acme"]}, NOW), [])
        self.assertEqual(dv.todo(self.conn, {"lever": ["acme"]}, NOW, refresh=True), [("lever", "acme")])
        self.assertEqual(dv.todo(self.conn, {"lever": ["acme"]}, NOW + dv.VERIFY_TTL * 2), [("lever", "acme")])

    def test_interleave_round_robins_atss(self):
        pairs = [("a", "1"), ("a", "2"), ("a", "3"), ("b", "1")]
        self.assertEqual(discover_run.interleave(pairs), [("a", "1"), ("b", "1"), ("a", "2"), ("a", "3")])


def row(ats, slug, name, us60=3, n_jobs=10, website=None, hit=1, newest="2026-09-30T00:00:00Z"):
    return {"ats": ats, "slug": slug, "board_name": name, "us60": us60, "n_jobs": n_jobs, "website": website,
            "hit": hit, "newest_us": newest, "source": "cc", "name_src": "ats"}


class RegistrySelectTest(unittest.TestCase):
    def test_admits_only_us_live_and_skips_curated_boards(self):
        rows = [row("greenhouse", "acme", "Acme"), row("greenhouse", "dead", "Dead", us60=0),
                row("ashby", "beta", "Beta", hit=0), row("greenhouse", "anthropic", "Anthropic")]
        kept, funnel = registry.select(rows, [{"name": "Anthropic", "ats": "greenhouse", "token": "anthropic"}])
        self.assertEqual([r["slug"] for r in kept], ["acme"])
        self.assertEqual((funnel["us_live"], funnel["already_curated"], funnel["admitted"]), (2, 1, 1))

    def test_one_workday_site_per_tenant_and_curated_tenant_wins(self):
        rows = [row("workday", "acme/wd1/External", "Acme", n_jobs=50), row("workday", "acme/wd1/Internal", "Acme 2",
                n_jobs=500), row("workday", "nvidia/wd5/Other", "NVIDIA2")]
        curated = [{"name": "NVIDIA", "ats": "workday", "token": "nvidia", "wd": "wd5", "site": "Ext"}]
        kept, funnel = registry.select(rows, curated)
        self.assertEqual([r["slug"] for r in kept], ["acme/wd1/Internal"])
        self.assertEqual(funnel["workday_extra_sites"], 1)

    def test_dedupe_same_company_across_atss_keeps_the_busier_board(self):
        rows = [row("greenhouse", "acme", "Acme, Inc.", us60=2), row("ashby", "acme", "Acme", us60=9, website="https://acme.org/"),
                row("lever", "acme-x", "Acme", website="https://acme.org"),
                row("ashby", "acme2", "Acme", website="https://other-acme.com", us60=1)]
        kept, funnel = registry.select(rows, [])
        self.assertEqual(sorted(r["ats"] + ":" + r["slug"] for r in kept), ["ashby:acme", "ashby:acme2"])
        self.assertEqual(funnel["dupe_same_company"], 2)

    def test_a_listed_board_keeps_its_place_when_a_busier_namesake_arrives(self):
        rows = [row("ashby", "glide", "Glide", us60=2), row("rippling", "glide-co", "Glide", us60=40)]
        kept, funnel = registry.select(rows, [], {"ashby:glide"})
        self.assertEqual([r["ats"] + ":" + r["slug"] for r in kept], ["ashby:glide"])
        self.assertEqual(funnel["dupe_same_company"], 1)

    def test_boards_merges_files_with_curated_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "companies.discovered.yaml").write_text(
                "companies:\n- {name: Acme, ats: greenhouse, token: acme, segment: startup}\n"
                "- {name: Dup, ats: lever, token: dup}\n- {name: X, ats: unknownats, token: x}\n")
            with mock.patch.object(config, "CONFIG", Path(tmp)), \
                    mock.patch.object(config, "companies_raw",
                                      return_value=[{"name": "Dup", "ats": "lever", "token": "dup"}]):
                boards = registry.boards()
        self.assertEqual([(b["board"], b["source"]) for b in boards],
                         [("lever:dup", "curated"), ("greenhouse:acme", "cc")])
        self.assertEqual(boards[1]["segment"], "startup")


class SegmentTest(unittest.TestCase):
    def setUp(self):
        sp = mock.patch.object(seedlists, "sp500", return_value={"generalmotors": "General Motors"})
        yc = mock.patch.object(seedlists, "yc_companies", return_value=(
            {"stripe": {"team_size": "7000", "stage": "Growth"}, "tinyco": {"team_size": "12", "stage": "Early"}}, {}))
        sp.start()
        yc.start()
        self.addCleanup(sp.stop)
        self.addCleanup(yc.stop)

    def test_rule_order(self):
        self.assertEqual(segments.segment("Ohio State University", None, "workday", 3000), "public_sector")
        self.assertEqual(segments.segment("Agency", "https://dmv.ca.gov", "greenhouse", 3), "public_sector")
        self.assertEqual(segments.segment("General Motors", None, "workday", 50), "big")
        self.assertEqual(segments.segment("Stripe", None, "greenhouse", 40), "big")
        self.assertEqual(segments.segment("Somebank", None, "greenhouse", 400), "big")
        self.assertEqual(segments.segment("TinyCo", None, "ashby", 1), "startup")
        self.assertEqual(segments.segment("Acme Staffing", None, "greenhouse", 10), "other")
        self.assertEqual(segments.segment("Midco", None, "workday", 200), "other")
        self.assertEqual(segments.segment("Newco", None, "ashby", 12), "startup")
        self.assertEqual(segments.segment("Midsize", None, "greenhouse", 200), "unknown")

    def test_compact_names(self):
        self.assertEqual(seedlists.compact("The Walt Disney Company"), "waltdisney")
        self.assertEqual(seedlists.compact("3M"), "3m")


class SeedAndRobotsTest(unittest.TestCase):
    def test_sites_from_workday_robots(self):
        text = ("Sitemap: https://gm.wd5.myworkdayjobs.com/Careers_GM/siteMap.xml\nUser-agent: *\n"
                "Allow: /Careers_GM/\nAllow: /Careers_TEI/\nDisallow: /Research/\nDisallow: /refreshFacet/\n")
        self.assertEqual(discover_seed.sites_from_robots(text), ["Careers_GM", "Careers_TEI"])

    def test_tenant_sites_skips_data_centers_without_the_tenant(self):
        def fake(url, **kwargs):
            if ".wd5." in url:
                return net.Response(200, b"User-agent: *\nAllow: /Ext/\n")
            raise HttpError(url, 422, "no tenant")
        with mock.patch.object(discover_seed, "request", side_effect=fake):
            self.assertEqual(discover_seed.tenant_sites("acme"), ["acme/wd5/Ext"])

    def test_robots_rules_and_unreadable_robots(self):
        robots.clear()
        ok = net.Response(200, b"User-agent: *\nDisallow: /Research/\n")
        with mock.patch.object(net, "request", return_value=ok) as fetched:
            self.assertFalse(robots.allowed("https://n.wd5.myworkdayjobs.com/Research/"))
            self.assertTrue(robots.allowed("https://n.wd5.myworkdayjobs.com/External/"))
        self.assertEqual(fetched.call_count, 1)                      # cached per host
        self.assertFalse(fetched.call_args.kwargs["gate"])          # robots.txt itself bypasses the gate
        with mock.patch.object(net, "request", side_effect=HttpError("u", 503, "down")):
            self.assertIsNone(robots.allowed("https://other.example/x"))     # 5xx: disallow for now, retry later
        for status in (401, 404):
            with mock.patch.object(net, "request", side_effect=HttpError("u", status, "none")):
                self.assertTrue(robots.allowed(f"https://h{status}.example/x"))   # RFC 9309: 4xx = allowed
        self.assertTrue(robots.allowed("https://data.commoncrawl.org/cc-index/x"))
        robots.clear()

    def test_robots_cache_round_trips_through_the_db(self):
        robots.clear()
        with tempfile.TemporaryDirectory() as tmp:
            conn = db.connect(Path(tmp) / "t.db")
            with mock.patch.object(net, "request", return_value=net.Response(200, b"User-agent: *\nDisallow: /x/\n")):
                robots.allowed("https://a.example/y")
            self.assertEqual(robots.save(conn), 1)
            robots.clear()
            robots.load(conn)
            with mock.patch.object(net, "request") as fetched:
                self.assertFalse(robots.allowed("https://a.example/x/1"))
            fetched.assert_not_called()
            conn.close()
        robots.clear()


if __name__ == "__main__":
    unittest.main()
