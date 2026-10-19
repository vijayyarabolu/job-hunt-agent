"""the feed answers fast. AppliedIndex finds the same companies as same_company() pair by pair (exact /
core keys by dict, the fuzzy check only for names whose lengths can pass), on real-looking names."""
import itertools
import random
import unittest

from job_hunt_agent import applied

NAMES = ["JPMorgan Chase & Co.", "JPMorgan", "Meta Platforms, Inc.", "Meta", "Accenture", "Accenture Federal Services",
         "Cohere", "Cohere Health", "Capital One", "Capitol One", "Capital One Bank", "Wells Fargo", "Wells Fargo & Co",
         "Amazon", "Amazon Web Services", "Amazn", "Google", "Gooogle", "Alphabet", "H2O.ai", "H2O", "USAA", "Target",
         "Target Corporation", "The Nuclear Company", "Nuclear Co", "Microsoft", "Microsoft Corp", "Mircosoft",
         "Salesforce", "SalesForce.com", "Comulate", "KUBRA", "Kubra Data Transfer", "LangChain", "Lang Chain",
         "Epicor Software", "Epicor", "Qventus", "Q-ventus", "Allstate", "All State", "", "A", "AB"]


class AppliedIndexTest(unittest.TestCase):
    def test_index_matches_pairwise_same_company(self):
        rnd = random.Random(7)
        extra = ["".join(rnd.choice("abcdefgh ") for _ in range(rnd.randint(3, 14))) for _ in range(150)]
        names = NAMES + extra
        refs = tuple(applied.AppliedRef(applied.company_key(n), "t", None, None) for n in names)
        index = applied.AppliedIndex(refs)
        for name in names + ["JP Morgan Chase", "Meta Platform", "capital one", "Wells Fargo Bank"]:
            key = applied.company_key(name)
            want = sorted(id(r) for r in refs if applied.same_company(key, r.company_key))
            got = sorted(id(r) for r in index.at_company(name))
            self.assertEqual(got, want, name)

    def test_empty_names_never_match(self):
        index = applied.AppliedIndex((applied.AppliedRef("", "t", None, None),))
        self.assertEqual(index.at_company(""), [])
        self.assertEqual(list(itertools.chain(index.at_company("Acme"))), [])


class FeedCacheTest(unittest.TestCase):
    """The kept ranking gives the same feed as a fresh one, and a change it depends on drops it."""

    def test_cached_feed_equals_fresh_and_feedback_invalidates(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest import mock

        from job_hunt_agent import api, config, db, feedback, feedcache
        from tests.test_server import seed_job
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(config, "DB_PATH", Path(tmp) / "t.db"):
            conn = db.connect()
            uid = seed_job(conn)
            now = db.utcnow()
            feedcache.clear()
            first = api.jobs(conn, "alex", "recommended", now)
            with mock.patch.object(feedcache.ranking, "rank", side_effect=AssertionError("not kept")):
                again = api.jobs(conn, "alex", "recommended", now)
            self.assertEqual(json.dumps(first, sort_keys=True, default=str), json.dumps(again, sort_keys=True, default=str))
            feedback.record(conn, "alex", uid, "like", None, now, ("ml_engineer",))
            with mock.patch.object(feedcache, "_revalidate") as later:     # a like moves it: ranked again behind
                api.jobs(conn, "alex", "recommended", now)
            later.assert_called_once()
            feedcache._rerank(*later.call_args.args)
            [entry] = feedcache._ENTRIES.values()
            self.assertEqual(entry.fingerprint, feedcache.fingerprint(conn, "alex"))
            conn.close()
            feedcache.clear()

    def test_an_old_ranking_is_served_at_once_and_ranked_again_in_the_background(self):
        import tempfile
        import time
        from dataclasses import replace
        from pathlib import Path
        from unittest import mock

        from job_hunt_agent import api, config, db, feedcache
        from tests.test_server import seed_job
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(config, "DB_PATH", Path(tmp) / "t.db"):
            conn = db.connect()
            seed_job(conn)
            now = db.utcnow()
            feedcache.clear()
            api.jobs(conn, "alex", "recommended", now)
            [(key, entry)] = feedcache._ENTRIES.items()
            feedcache._ENTRIES[key] = replace(entry, at=time.monotonic() - feedcache.TTL_S - 1)   # aged out, unchanged
            with mock.patch.object(feedcache.ranking, "rank", side_effect=AssertionError("ranked in the request")), \
                    mock.patch.object(feedcache, "_revalidate") as later:
                api.jobs(conn, "alex", "recommended", now)
            later.assert_called_once()
            feedcache._rerank(*later.call_args.args)                       # what the background thread runs
            self.assertLess(time.monotonic() - feedcache._ENTRIES[key].at, feedcache.TTL_S)
            feedcache._ENTRIES[key] = replace(feedcache._ENTRIES[key], at=time.monotonic() - feedcache.TTL_S - 1)
            with mock.patch.object(feedcache.ranking, "rank", side_effect=RuntimeError("boom")), \
                    self.assertLogs("job_hunt_agent.feedcache", "ERROR"):
                feedcache._rerank(*later.call_args.args)                   # a failed re-rank keeps the old answer...
            self.assertLess(time.monotonic() - feedcache._ENTRIES[key].at, feedcache.TTL_S)   # ...without retrying at once
            conn.close()
            feedcache.clear()

    def test_during_a_run_the_kept_answer_is_served_and_the_warm_up_waits_for_a_fresh_one(self):
        import tempfile
        from pathlib import Path
        from unittest import mock

        from job_hunt_agent import api, config, db, feedcache
        from tests.test_server import seed_job
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(config, "DB_PATH", Path(tmp) / "t.db"):
            conn = db.connect()
            seed_job(conn)
            now = db.utcnow()
            feedcache.clear()
            api.jobs(conn, "alex", "recommended", now)
            [(key, kept)] = feedcache._ENTRIES.items()
            db.start_run(conn, "test", now)                       # a fetch run starts: the data moved
            with mock.patch.object(feedcache.ranking, "rank", side_effect=AssertionError("ranked in the request")), \
                    mock.patch.object(feedcache, "_revalidate") as later:
                api.jobs(conn, "alex", "recommended", now)
            later.assert_called_once()
            self.assertIs(feedcache._ENTRIES[key], kept)
            with mock.patch.object(feedcache.ranking, "rank", wraps=feedcache.ranking.rank) as rank:
                api.jobs(conn, "alex", "recommended", now, wait=True)   # the watcher's warm-up after a run
            self.assertEqual(rank.call_count, 1)
            self.assertIsNot(feedcache._ENTRIES[key], kept)
            conn.close()
            feedcache.clear()

    def test_kept_rankings_share_one_copy_of_each_jobs_text(self):
        import tempfile
        from pathlib import Path
        from unittest import mock

        from job_hunt_agent import config, db, ranking
        from tests.test_server import seed_job
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(config, "DB_PATH", Path(tmp) / "t.db"):
            conn = db.connect()
            seed_job(conn)
            now = db.utcnow()
            states = ("ok", "pending", "filtered", "too_senior", "non_us")
            [(mine, _)] = ranking.rank(conn, config.profile("alex"), now, states=states)
            [(again, _)] = ranking.rank(conn, config.profile("alex"), now, states=states)   # e.g. the other person's
            conn.close()
        self.assertEqual(mine["url"], again["url"])
        self.assertIs(mine["url"], again["url"])              # one string in memory, not one per ranking
        self.assertIs(mine["title"], again["title"])

    def test_one_ranking_at_a_time_and_waiters_take_the_fresh_answer(self):
        import tempfile
        import threading
        import time
        from pathlib import Path
        from unittest import mock

        from job_hunt_agent import config, db, feedcache
        from tests.test_server import seed_job
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(config, "DB_PATH", Path(tmp) / "t.db"):
            conn = db.connect()
            seed_job(conn)
            conn.close()
            feedcache.clear()
            running, most, calls = [0], [0], []
            real = feedcache.ranking.rank

            def slow_rank(*args, **kwargs):
                running[0] += 1
                most[0] = max(most[0], running[0])
                calls.append(1)
                time.sleep(0.2)                            # long enough for every thread to arrive meanwhile
                running[0] -= 1
                return real(*args, **kwargs)

            def ask(pid):
                c = db.connect()
                feedcache.ranked(c, config.profile(pid), db.utcnow(), False, ("ok", "pending"))
                c.close()
            with mock.patch.object(feedcache.ranking, "rank", side_effect=slow_rank):
                threads = [threading.Thread(target=ask, args=(pid,)) for pid in ("alex", "alex", "alex", "jordan")]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join()
            self.assertEqual(most[0], 1)                   # never two rankings in memory at once
            self.assertEqual(len(calls), 2)                # alex ranked once for three tabs, jordan once
            feedcache.clear()


from tests.test_tracker_ui import TrackerCase  # noqa: E402


class CompactTrackerTest(TrackerCase):
    def test_compact_rows_cut_long_text_and_say_which_fields(self):
        from job_hunt_agent import server, trackapi
        long_jd = ("Senior ML role. " * 200).strip()
        row = self.add(company="Acme", role_title="ML Engineer", jd=long_jd, notes="short", status="applied")
        out = server.route(self.conn, "GET", "/api/tracker", {"profile": "alex", "compact": "1"}, None)
        got = next(r for r in out["rows"] if r["id"] == row["id"])
        self.assertEqual(len(got["jd"]), trackapi.CLIP_CHARS)
        self.assertTrue(got["jd"].endswith("…"))
        self.assertIn("jd", got["clipped"])
        self.assertEqual(got["notes"], "short")
        full = server.route(self.conn, "GET", "/api/tracker", {"profile": "alex"}, None)
        self.assertEqual(next(r for r in full["rows"] if r["id"] == row["id"])["jd"], long_jd)
        self.assertEqual(trackapi.get(self.conn, "alex", row["id"])["jd"], long_jd)
