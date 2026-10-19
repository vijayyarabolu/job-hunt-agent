"""RFC 9309 matching in the robots gate: the longest matching rule wins (Allow on a tie), `*` and `$` patterns, groups
for our agent before `*`, an empty Disallow allows everything, /robots.txt is always allowed. The stdlib parser applied
the first matching line instead, so `Disallow: /` before `Allow: /api/pcsx` refused an allowed URL."""
import unittest

from job_hunt_agent import robots

EIGHTFOLD = """User-agent: *
Disallow: /
Allow: /$
Allow: /careers
Allow: /api/pcsx
"""


def ok(text: str, url: str) -> bool:
    return robots.parser_for(text).can_fetch(robots.AGENT, url)


class LongestMatchTest(unittest.TestCase):
    def test_a_longer_allow_beats_an_earlier_disallow(self):
        self.assertTrue(ok(EIGHTFOLD, "https://h.example/api/pcsx/search?domain=x.com&start=0"))
        self.assertFalse(ok(EIGHTFOLD, "https://h.example/api/other"))

    def test_a_longer_disallow_beats_an_earlier_allow(self):
        text = "User-agent: *\nAllow: /\nDisallow: /private\n"
        self.assertFalse(ok(text, "https://h.example/private/x"))
        self.assertTrue(ok(text, "https://h.example/public"))

    def test_dollar_anchors_the_end(self):
        self.assertTrue(ok(EIGHTFOLD, "https://h.example/"))
        self.assertFalse(ok(EIGHTFOLD, "https://h.example/x"))

    def test_star_matches_any_run(self):
        text = "User-agent: *\nDisallow: /*?q=\nDisallow: /*.pdf$\n"
        self.assertFalse(ok(text, "https://h.example/search?q=ml"))
        self.assertFalse(ok(text, "https://h.example/a/b.pdf"))
        self.assertTrue(ok(text, "https://h.example/a/b.pdf?x=1"))

    def test_tie_goes_to_allow(self):
        self.assertTrue(ok("User-agent: *\nDisallow: /a\nAllow: /a\n", "https://h.example/a"))


class GroupTest(unittest.TestCase):
    def test_our_group_wins_over_star_and_groups_merge(self):
        text = ("User-agent: *\nDisallow: /\n\nUser-agent: Job-Hunt-Agent\nDisallow: /x\n\n"
                "User-agent: other\nUser-agent: job-hunt-agent\nDisallow: /y\n")
        self.assertTrue(ok(text, "https://h.example/z"))
        self.assertFalse(ok(text, "https://h.example/x"))
        self.assertFalse(ok(text, "https://h.example/y"))

    def test_no_rules_and_empty_disallow_allow_everything(self):
        self.assertTrue(ok("", "https://h.example/a"))
        self.assertTrue(ok("User-agent: *\nDisallow:\n", "https://h.example/a"))

    def test_robots_txt_itself_and_comments(self):
        text = "# hi\nUser-agent: * # all\nDisallow: / # nothing\n"
        self.assertFalse(ok(text, "https://h.example/a"))
        self.assertTrue(ok(text, "https://h.example/robots.txt"))

    def test_percent_encoding_is_compared_decoded(self):
        self.assertFalse(ok("User-agent: *\nDisallow: /a%3cd\n", "https://h.example/a%3Cd"))
        self.assertFalse(ok("User-agent: *\nDisallow: /caf%C3%A9\n", "https://h.example/café"))


class HostileInputTest(unittest.TestCase):
    """Review findings 2026-10-06: a BOM, a throttled robots.txt, wildcard blow-up, encoded wildcards."""

    def test_a_byte_order_mark_does_not_lose_the_first_group(self):
        self.assertFalse(ok("\ufeffUser-agent: *\nDisallow: /", "https://h.example/a"))

    def test_many_wildcards_match_in_linear_time(self):
        import time
        text = "User-agent: *\nDisallow: /" + "*a" * 25 + "b\n"
        started = time.monotonic()
        self.assertTrue(ok(text, "https://h.example/" + "a" * 60))
        self.assertLess(time.monotonic() - started, 0.5)

    def test_an_encoded_star_or_dollar_is_a_literal(self):
        self.assertTrue(ok("User-agent: *\nDisallow: /a%2Ab\n", "https://h.example/aXXb"))
        self.assertFalse(ok("User-agent: *\nDisallow: /a%2Ab\n", "https://h.example/a*b"))
        self.assertTrue(ok("User-agent: *\nDisallow: /x%24\n", "https://h.example/x"))

    def test_a_throttled_robots_txt_is_unreachable_not_allow_all(self):
        from unittest import mock
        from job_hunt_agent.net import HttpError
        robots.clear()
        try:
            for status in (408, 429):
                with mock.patch("job_hunt_agent.net.request", side_effect=HttpError("u", status, "slow down")):
                    self.assertIsNone(robots.allowed(f"https://t{status}.example/x"))
        finally:
            robots.clear()


class CrawlDelayTest(unittest.TestCase):
    def test_our_groups_delay_is_read(self):
        text = "User-agent: *\nAllow: /\ncrawl-delay: 5\n"
        self.assertEqual(robots.parser_for(text).crawl_delay(robots.AGENT), 5.0)
        self.assertIsNone(robots.parser_for("User-agent: *\nAllow: /\n").crawl_delay(robots.AGENT))
        self.assertIsNone(robots.parser_for("User-agent: *\nCrawl-delay: soon\n").crawl_delay(robots.AGENT))
        self.assertIsNone(robots.parser_for("User-agent: *\nCrawl-delay: inf\n").crawl_delay(robots.AGENT))
        self.assertEqual(robots.parser_for("User-agent: *\nCrawl-delay: 86400\n").crawl_delay(robots.AGENT),
                         robots.MAX_CRAWL_DELAY_S)

    def test_net_waits_at_least_the_crawl_delay(self):
        from job_hunt_agent import net
        robots.clear()
        try:
            with robots._lock:
                robots._cache["slow.example"] = (robots.parser_for("User-agent: *\nCrawl-delay: 5\n"), 1e18, 200, "")
            self.assertEqual(net.gap_for("slow.example"), 5.0)
            self.assertEqual(net.gap_for("api.lever.co"), 1.0)
            self.assertEqual(net.gap_for("other.example"), net.MIN_INTERVAL_S)
            net.pace("paced.example", 1.0)
            self.assertEqual(net.gap_for("paced.example"), 1.0)
            self.assertEqual(net.gap_for("slow.example"), 5.0)             # never lower than robots asks
        finally:
            robots.clear()


if __name__ == "__main__":
    unittest.main()


class CaselessHostTest(unittest.TestCase):
    """Workday serves a career site under any spelling (/ThermoFisherCareers/ = /thermofishercareers/), so its
    robots rules are matched without case there; RFC 9309's case-sensitive match stays the rule everywhere else."""
    TEXT = "User-agent: *\nDisallow: /ThermoFisherCareers/\n"

    def gate(self, host: str, path: str) -> bool:
        from unittest import mock
        with mock.patch.object(robots, "_entry", return_value=robots.parser_for(self.TEXT)):
            return robots.allowed(f"https://{host}{path}")

    def test_a_workday_site_disallowed_in_another_case_is_refused(self):
        self.assertFalse(self.gate("thermofisher.wd5.myworkdayjobs.com", "/thermofishercareers/"))
        self.assertTrue(self.gate("thermofisher.wd5.myworkdayjobs.com", "/wday/cxs/thermofisher/x/jobs"))
        self.assertFalse(self.gate("wd5.myworkdaysite.com", "/thermofishercareers/"))

    def test_other_hosts_keep_the_case_sensitive_match(self):
        self.assertTrue(self.gate("jobs.example.com", "/thermofishercareers/"))
        self.assertFalse(self.gate("jobs.example.com", "/ThermoFisherCareers/"))
