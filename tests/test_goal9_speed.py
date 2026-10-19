"""GOAL9 verifier finding: a filter pick took 115-373 ms to land, mostly in /api/jobs. The speed-ups must not change
an answer: the timestamp fast path parses like strptime (and still refuses other forms), the JD key cache re-keys an
edited JD, and a memoized card equals a freshly built one."""
import threading
import time
import unittest
from datetime import datetime, timezone
from unittest import mock

from job_hunt_agent import api, config, db, feedcache, filters, fresh, ranking
from tests.test_feed_order import NOW, FeedOrderApiTest
from tests.test_ranking import CollapseTest


class ParseFastPathTest(unittest.TestCase):
    def test_the_stored_form_parses_like_strptime(self):
        for ts in ("2026-10-07T12:34:56Z", "2024-02-29T00:00:00Z", "1999-12-31T23:59:59Z"):
            with self.subTest(ts):
                want = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                self.assertEqual(fresh.parse(ts), want)
                self.assertEqual(fresh.parse(ts).tzinfo, timezone.utc)

    def test_other_forms_are_still_refused(self):
        for ts in ("2026-10-07 12:34:56Z", "2026-10-07T12:34:56+00:00", "2026-13-07T12:34:56Z", "garbage-garbage-gaZ"):
            with self.subTest(ts), self.assertRaises(ValueError):
                fresh.parse(ts)

    def test_empty_is_none(self):
        self.assertIsNone(fresh.parse(None))
        self.assertIsNone(fresh.parse(""))


class JdKeyCacheTest(CollapseTest):
    def test_collapse_is_the_same_with_a_warm_cache(self):
        ranking._jd_key_cache.clear()
        cold = ranking.collapse(self.conn, self.ranked)
        warm = ranking.collapse(self.conn, self.ranked)
        self.assertEqual(cold, warm)

    def test_an_edited_jd_is_read_again(self):
        a = ranking._jd_word_set("lever:acme:1", ranking.zlib.compress(b"Requirements: Python and AWS."))
        b = ranking._jd_word_set("lever:acme:1", ranking.zlib.compress(b"Requirements: Rust and GCP."))
        self.assertNotEqual(a, b)
        self.assertEqual(a, ranking._jd_word_set("lever:acme:1", ranking.zlib.compress(b"Requirements: Python and AWS.")))


class CardMemoTest(FeedOrderApiTest):
    def test_a_memoized_feed_answer_equals_a_fresh_one(self):
        prof = {**config.profile("alex"), "feed_min_match": 0}
        with mock.patch.object(config, "profile", lambda pid: prof):
            feedcache._ENTRIES.clear()
            first = api.jobs(self.conn, "alex", "recommended", NOW, filters.parse({}))
            again = api.jobs(self.conn, "alex", "recommended", NOW, filters.parse({}))
            by_match = api.jobs(self.conn, "alex", "recommended", NOW, filters.parse({"sort": "match"}))
        self.assertEqual(first, again)
        self.assertEqual(sorted(first["jobs"], key=lambda c: c["uid"]), sorted(by_match["jobs"], key=lambda c: c["uid"]))
        self.assertTrue(first["jobs"][0]["parts"])


class RerankKeepsTheMemoTest(FeedOrderApiTest):
    def rerank(self, profile):
        key = feedcache.key_of(profile, False, ("ok",))
        feedcache._ENTRIES[key] = feedcache.Entry(feedcache.fingerprint(self.conn, profile["id"]), 0.0, [],
                                                  views={"x": "kept"}, profile_sig=feedcache._sig(profile))
        feedcache._BUSY.add(key)
        feedcache._rerank(key, profile, False, ("ok",))      # opens its own connection to the same test DB
        return feedcache._ENTRIES.pop(key)

    def test_nothing_moved_keeps_the_memo(self):
        self.assertEqual(self.rerank(config.profile("alex")).views, {"x": "kept"})

    def test_a_changed_profile_starts_a_new_memo(self):
        prof = config.profile("alex")
        key = feedcache.key_of(prof, False, ("ok",))
        feedcache._ENTRIES[key] = feedcache.Entry(feedcache.fingerprint(self.conn, prof["id"]), 0.0, [],
                                                  views={"x": "kept"}, profile_sig="another profile")
        feedcache._rerank(key, prof, False, ("ok",))
        self.assertEqual(feedcache._ENTRIES.pop(key).views, {})



class IndustriesOnlyWhenNeededTest(FeedOrderApiTest):
    """the cold first page read every pool job's industry (~4k JDs, ~40 % of it) for the 300 cards shown;
    the match % (a floor or Best match) is the only use of the rest."""
    def industry_calls(self, **query):
        prof = {**config.profile("alex"), "feed_min_match": query.pop("floor", 0)}
        api._INDUSTRY.clear()
        feedcache._ENTRIES.clear()
        with mock.patch.object(config, "profile", lambda pid: prof), mock.patch.object(api, "MAX_CARDS", 1), \
                mock.patch.object(api.match, "industry_of", wraps=api.match.industry_of) as seen:
            data = api.jobs(self.conn, "alex", "recommended", NOW, filters.parse(query))
        return seen.call_count, data

    def test_newest_first_reads_only_the_cards_shown(self):
        calls, data = self.industry_calls()
        self.assertEqual((calls, len(data["jobs"]), data["total"]), (1, 1, 3))

    def test_a_floor_or_best_match_reads_every_job(self):
        self.assertEqual(self.industry_calls(sort="match")[0], 3)
        self.assertEqual(self.industry_calls(floor=10)[0], 3)


    def test_the_warm_up_and_a_page_never_read_the_same_jd_twice(self):
        # a Best match asked during the warm-up's industry fill read every JD a second time (9.4 s)
        rows = [{"uid": u} for u in (self.new_thin, self.mid_rich, self.old_rich)]
        api._INDUSTRY.clear()
        slow = lambda *a: time.sleep(0.05) or None
        with mock.patch.object(api.match, "industry_of", side_effect=slow) as seen, \
                mock.patch.object(api.ranking, "SQL_BATCH", 1):
            other = threading.Thread(target=api._industries, args=(db.connect(config.DB_PATH), rows))
            other.start()
            api._industries(self.conn, rows)
            other.join()
        self.assertEqual(seen.call_count, 3)


if __name__ == "__main__":
    unittest.main()
