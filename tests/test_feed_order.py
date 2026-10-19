"""GOAL4 §0 feed order: newest first by the exact posted time (Workday day precision -> our first_seen), the
feed_min_match floor, the Newest | Best match toggle, the 1h / 6h / 24h / 3d chips and the exact age on cards."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from job_hunt_agent import api, config, db, filters, fits, fresh, match, store
from job_hunt_agent.ats.common import FetchResult, make_job

NOW = datetime(2026, 10, 5, 18, 0, tzinfo=timezone.utc)


def iso(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


class FeedMomentTest(unittest.TestCase):
    def test_exact_uses_the_ats_posted_time(self):
        row = {"precision": "exact", "first_posted_at": iso(3), "first_seen": iso(1)}
        self.assertEqual(fresh.feed_moment(row), (fresh.parse(iso(3)), True))

    def test_day_precision_uses_our_first_seen(self):
        posted_day = iso(5)[:10] + "T00:00:00Z"
        row = {"precision": "day", "first_posted_at": posted_day, "first_seen": iso(5)}
        self.assertEqual(fresh.feed_moment(row), (fresh.parse(iso(5)), True))

    def test_day_precision_found_late_falls_back_to_the_posted_day(self):
        row = {"precision": "day", "first_posted_at": "2026-09-20T00:00:00Z", "first_seen": iso(2)}
        moment, exact = fresh.feed_moment(row)
        self.assertEqual(moment.date().isoformat(), "2026-09-20")
        self.assertFalse(exact)

    def test_updated_unknown_use_first_seen_and_backlog_has_none(self):
        self.assertEqual(fresh.feed_moment({"precision": "updated", "first_seen": iso(7)})[0], fresh.parse(iso(7)))
        self.assertEqual(fresh.feed_moment({"precision": "exact", "first_posted_at": iso(1), "seed": 1}), (None, False))

    def test_an_undated_job_says_first_seen(self):
        # GOAL17 extra row: Jobvite / some Taleo jobs have no posting date; their age is ours, not the company's
        self.assertEqual(fresh.age_word({"precision": "unknown", "first_seen": iso(2)}), "First seen")
        self.assertEqual(fresh.age_word({"precision": "updated", "first_seen": iso(2)}), "First seen")
        self.assertEqual(fresh.age_word({"precision": "exact", "first_posted_at": iso(3)}), "Posted")
        self.assertEqual(fresh.age_word({"precision": "day", "first_posted_at": "2026-10-05T00:00:00Z"}), "Posted")

    def test_a_repost_chain_is_as_old_as_its_origin(self):
        row = {"precision": "exact", "first_posted_at": iso(1), "chain_origin": iso(100)}
        self.assertEqual(fresh.feed_moment(row)[0], fresh.parse(iso(100)))


class ExactAgeTest(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(match.exact_age(32 / 60), "32 min ago")
        self.assertEqual(match.exact_age(0.001), "1 min ago")
        self.assertEqual(match.exact_age(5 + 12 / 60), "5 h 12 min ago")
        self.assertEqual(match.exact_age(50), "2 d 2 h ago")
        self.assertEqual(match.exact_age(200), "8 d ago")
        self.assertEqual(match.exact_age(30, exact=False), "~1 d 6 h ago")
        self.assertIsNone(match.exact_age(None))


class ParseSortAndChipsTest(unittest.TestCase):
    def test_default_sort_is_newest_and_toggle_parses(self):
        self.assertEqual(filters.parse({}).sort, "newest")
        self.assertEqual(filters.parse({"sort": "match"}).sort, "match")
        with self.assertRaises(ValueError):
            filters.parse({"sort": "random"})

    def test_quick_chips(self):
        self.assertEqual([filters.parse({"posted": p}).posted_hours for p in ("1h", "6h", "24h", "3d")],
                         [1, 6, 24, 72])

    def test_posted_window_uses_the_feed_moment(self):
        posted_day = iso(30)[:10] + "T00:00:00Z"
        row = {"precision": "day", "first_posted_at": posted_day, "first_seen": iso(0.5), "seed": 0}
        self.assertTrue(filters.keep(filters.Filters(posted_hours=1), row, "ok", None, False, 80, NOW))


def _seed(conn, ext_id: str, title: str, hours_ago: float, desc: str) -> str:
    item = make_job(ats="lever", key="acme", ext_id=ext_id, company="Acme", title=title, location="Austin, TX",
                    url=f"https://jobs.lever.co/acme/{ext_id}", posted_at=iso(hours_ago), description=desc)
    run = db.start_run(conn, "test", NOW)
    store.write_board(conn, db.board(conn, "lever:acme"), FetchResult([item], 200, n_raw=1), "ok", run, NOW,
                      ["fam:ml_engineer"], 5)
    return item["uid"]


class FeedCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = mock.patch.object(config, "DB_PATH", Path(self.tmp.name) / "t.db")
        self.patch.start()
        self.conn = db.connect()
        db.sync_boards(self.conn, [{"board": "lever:acme", "ats": "lever", "key": "acme", "company": "Acme"}])
        rich = "Requirements:\n" + "Python, PyTorch, TensorFlow, LLM, RAG, LangChain, AWS, SQL, Docker, Kubernetes."
        self.old_rich = _seed(self.conn, "1", "Machine Learning Engineer", 20, rich)
        self.new_thin = _seed(self.conn, "2", "Machine Learning Engineer II", 2, "Requirements:\nJAX, CatBoost, Kubeflow, CrewAI, DSPy, vLLM, CUDA, Looker, Grafana, Prometheus.")
        self.mid_rich = _seed(self.conn, "3", "ML Engineer", 6, rich + " Spark.")
        fits.refresh(self.conn, config.profile("alex"), NOW)

    def tearDown(self):
        self.conn.close()
        self.patch.stop()
        self.tmp.cleanup()

    def feed(self, floor: int, **args) -> list[str]:
        prof = {**config.profile("alex"), "feed_min_match": floor}
        with mock.patch.object(config, "profile", lambda pid: prof):
            data = api.jobs(self.conn, "alex", "recommended", NOW, filters.parse(args))
        return [c["uid"] for c in data["jobs"]], data


class FeedOrderApiTest(FeedCase):
    def test_newest_first_by_default_with_exact_ages(self):
        uids, data = self.feed(0)
        self.assertEqual(uids, [self.new_thin, self.mid_rich, self.old_rich])
        self.assertEqual(data["sort"], "newest")
        self.assertEqual(data["jobs"][0]["labels"]["posted"], "2 h 0 min ago")
        self.assertEqual(data["jobs"][0]["posted_ts"], iso(2))
        self.assertEqual(data["jobs"][0]["posted_label"], "Posted")

    def test_floor_hides_weak_matches(self):
        uids, data = self.feed(60)
        self.assertNotIn(self.new_thin, uids)
        self.assertEqual(data["min_match_floor"], 60)

    def test_no_floor_set_shows_every_match(self):
        """resumegen GOAL3 §8: with no feed_min_match in the profile, Recommended shows jobs under 60% too."""
        prof = {k: v for k, v in config.profile("alex").items() if k != "feed_min_match"}
        with mock.patch.object(config, "profile", lambda pid: prof):
            data = api.jobs(self.conn, "alex", "recommended", NOW, filters.parse({}))
        self.assertIn(self.new_thin, [c["uid"] for c in data["jobs"]])
        self.assertEqual(data["min_match_floor"], 0)

    def test_best_match_toggle(self):
        uids, _ = self.feed(0, sort="match")
        self.assertEqual(uids[-1], self.new_thin)

    def test_quick_chip_last_6h(self):
        uids, _ = self.feed(0, posted="6h")
        self.assertEqual(set(uids), {self.new_thin, self.mid_rich})


class JobBoardFilterTest(FeedCase):
    """a "Job board" filter with live counts per board, independent of Posted, paged past 300."""
    def setUp(self):
        super().setUp()
        db.sync_boards(self.conn, [{"board": "lever:acme", "ats": "lever", "key": "acme", "company": "Acme"},
                                   {"board": "workday:globex", "ats": "workday", "key": "globex", "company": "Globex"},
                                   {"board": "ashby:zed", "ats": "ashby", "key": "zed", "company": "Zed"}])
        item = make_job(ats="workday", key="globex", ext_id="9", company="Globex", title="Machine Learning Engineer",
                        location="Austin, TX", url="https://globex.wd1.myworkdayjobs.com/x/9", posted_at=iso(40),
                        description="Requirements:\nPython, PyTorch, AWS.")
        run = db.start_run(self.conn, "test", NOW)
        store.write_board(self.conn, db.board(self.conn, "workday:globex"), FetchResult([item], 200, n_raw=1), "ok",
                          run, NOW, ["fam:ml_engineer"], 5)
        self.workday = item["uid"]
        fits.refresh(self.conn, config.profile("alex"), NOW)

    def counts(self, data) -> dict:
        return {b["id"]: b["n"] for b in data["boards"]}

    def test_a_board_alone_or_with_posted_and_counts_follow_the_other_filters(self):
        uids, data = self.feed(0, board="workday")
        self.assertEqual(uids, [self.workday])
        self.assertEqual(self.counts(data), {"ashby": 0, "lever": 3, "workday": 1}, "0-count boards are listed too")
        self.assertEqual([b["label"] for b in data["boards"]], ["Ashby", "Lever", "Workday"])
        uids, data = self.feed(0, board="workday", posted="6h")
        self.assertEqual((uids, self.counts(data)["lever"]), ([], 2))
        uids, _ = self.feed(0, board="workday,lever")             # Posted "Any": no window at all
        self.assertEqual(len(uids), 4)
        uids, _ = self.feed(0, board="workday,lever", posted="30d")
        self.assertNotIn(self.workday, uids, "its board's first run: backlog, no posted time")
        self.assertEqual(len(uids), 3)

    def test_with_a_board_picked_the_list_pages_past_the_cap(self):
        with mock.patch.object(api, "MAX_CARDS", 2):
            first, data = self.feed(0, board="lever")
            rest, _ = self.feed(0, board="lever", offset="2")
            capped, all_boards = self.feed(0, offset="2")
        self.assertEqual((len(first), data["total"], len(rest)), (2, 3, 1))
        self.assertFalse(set(first) & set(rest))
        self.assertEqual((len(capped), all_boards["total"]), (2, 4), "no board: newest 300 only, as before")


if __name__ == "__main__":
    unittest.main()
