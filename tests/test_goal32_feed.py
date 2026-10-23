"""GOAL32: one role posted once per city is one card (Speechify's "Software Engineer, AI Data - <city>" x 740), led by
the best-located copy, and Not interested / applied on it covers every copy; genuinely different roles stay apart;
a Workday "posted today" card says when it was found, not "19 min"."""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from job_hunt_agent import api, clones, config, db, feedcache, fresh, ranking, store
from job_hunt_agent.ats.common import FetchResult, make_job

NOW = datetime(2026, 10, 21, 17, tzinfo=timezone.utc)
JD = " ".join(f"skill{n}" for n in range(80)) + " Build data pipelines for AI voice models."
ROLE = "Software Engineer, AI Data"
CITIES = ("Mountain View, CA, USA", "Menlo Park, CA, USA", "Seattle, WA, USA", "Aarhus, Denmark")


class SplitTest(unittest.TestCase):
    def test_a_trailing_place_is_cut_off_the_title(self):
        for title, base, place in [
                (f"{ROLE} - Mountain View, CA, USA", ROLE, "Mountain View, CA, USA"),
                (f"{ROLE} - Winston-Salem, NC, USA", ROLE, "Winston-Salem, NC, USA"),
                ("Senior Data Engineer (Seattle)", "Senior Data Engineer", "Seattle"),
                ("Data Engineer - Remote - US", "Data Engineer", "Remote, US"),
                ("AI Agent Engineer | Remote", "AI Agent Engineer", "Remote"),
                ("AI/ML Engineer, Berlin", "AI/ML Engineer", "Berlin"),
                ("Marketing Data Analytics Engineer (US)", "Marketing Data Analytics Engineer", "US")]:
            self.assertEqual(clones.split(title, "Remote"), (base, place), title)

    def test_teams_levels_and_title_words_stay(self):
        for title in (ROLE, "Software Engineer - Payments", "Software Engineer II", "Engineer (SWE-2)",
                      "Software Engineer, AI Billing", "Data Scientist - US Department of State", "Full-Stack Engineer",
                      "Agentic Engineer - Security, US Amazon Dedicated Cloud Security"):
            self.assertEqual(clones.split(title, "Remote")[0], title, title)

    def test_the_level_is_part_of_the_key(self):
        self.assertNotEqual(clones.title_key("Software Engineer I - Austin, TX"), clones.title_key("Software Engineer II - Austin, TX"))
        self.assertNotEqual(clones.title_key("Senior Data Engineer"), clones.title_key("Data Engineer"))
        self.assertEqual(clones.title_key("Sr. ML Engineer - Remote"), clones.title_key("Senior Machine Learning Engineer"))

    def test_his_places_come_first_then_remote_then_the_us(self):
        prefs = clones.preferences({"location": "Round Rock, TX"})
        self.assertEqual(prefs, ("round rock", "austin", "tx"))
        rank = [clones.place_rank({"title": f"{ROLE} - {c}", "location": "Remote"}, prefs) for c in
                ("Round Rock, TX, USA", "Austin, TX, USA", "Dallas, TX, USA", "Remote", "Denver, CO, USA", "Aarhus, Denmark")]
        self.assertEqual(rank, sorted(rank))
        self.assertEqual(len(set(rank)), len(rank))


class CityClonesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.db")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _jobs(self, jobs):
        """(ext, title, location, jd, req) -> ranked rows, best-ranked first."""
        items = [make_job(ats="greenhouse", key="speechify", ext_id=ext, company="Speechify", title=title, location=loc,
                          url=f"https://job-boards.greenhouse.io/speechify/jobs/{ext}", posted_at=db.iso(NOW),
                          description=jd, req_id=req) for ext, title, loc, jd, req in jobs]
        db.sync_boards(self.conn, [{"board": "greenhouse:speechify", "ats": "greenhouse", "key": "speechify",
                                    "company": "Speechify"}])
        run = db.start_run(self.conn, "test", NOW)
        store.write_board(self.conn, db.board(self.conn, "greenhouse:speechify"), FetchResult(items, 200, n_raw=len(items)),
                          "ok", run, NOW, ["fam:ml_engineer"], 5)
        return [({"uid": i["uid"], "company": i["company"], "title": i["title"], "location": i["location"],
                  "req_id": i.get("req_id")}, SimpleNamespace(worth=90 - n)) for n, i in enumerate(items)]

    def test_one_role_in_many_cities_is_one_card_with_its_other_places(self):
        ranked = self._jobs([(str(n), f"{ROLE} - {c}", "Remote", "", "133") for n, c in enumerate(CITIES)])
        out = ranking.collapse(self.conn, ranked, clones.preferences({"location": "Bellevue, WA"}))
        self.assertEqual(len(out), 1)
        lead = out[0][0]
        self.assertEqual(lead["title"], f"{ROLE} - Seattle, WA, USA")               # the user's metro beats the better rank
        self.assertEqual([place for _, place in lead["also"]],
                         ["Mountain View, CA, USA", "Menlo Park, CA, USA", "Aarhus, Denmark"])

    def test_copies_with_the_same_jd_join_and_a_different_jd_stays_apart(self):
        ranked = self._jobs([("1", f"{ROLE} - Mountain View, CA, USA", "Remote", JD, "133"),
                             ("2", f"{ROLE} - Menlo Park, CA, USA", "Remote", JD + " Menlo Park", "133"),
                             ("3", f"{ROLE} - Austin, TX, USA", "Remote", "Sales quota territory accounts " * 20, "140")])
        out = ranking.collapse(self.conn, ranked)
        self.assertEqual([len(r["also"]) for r, _ in out], [1, 0])

    def test_levels_and_teams_are_never_merged(self):
        ranked = self._jobs([("1", "Software Engineer I - Austin, TX, USA", "Remote", "", ""),
                             ("2", "Software Engineer II - Dallas, TX, USA", "Remote", "", ""),
                             ("3", "Software Engineer - Payments - Austin, TX", "Remote", "", ""),
                             ("4", "Software Engineer - Search - Dallas, TX", "Remote", "", "")])
        self.assertEqual(len(ranking.collapse(self.conn, ranked)), 4)

    def test_a_filtered_out_lead_hands_the_card_to_the_next_copy(self):
        ranked = self._jobs([(str(n), f"{ROLE} - {c}", "Remote", "", "133") for n, c in enumerate(CITIES)])
        group_of = ranking.groups(self.conn, [r for r, _ in ranked])
        shown = ranking.fold([pair for pair in ranked if "Seattle" not in pair[0]["title"]], group_of,
                             clones.preferences({"location": "Bellevue, WA"}))
        self.assertEqual(len(shown), 1)
        self.assertEqual(len(shown[0][0]["also"]), 2)


class CoversEveryCopyTest(unittest.TestCase):
    GROUP = {"a": "a", "b": "a", "c": "a", "x": "x", "y": "x"}

    def test_not_interested_on_one_copy_covers_the_role(self):
        self.assertEqual(api._spread({"b": "dismissed"}, self.GROUP), {"a": "dismissed", "b": "dismissed", "c": "dismissed"})

    def test_a_copy_keeps_its_own_state_and_not_interested_wins(self):
        spread = api._spread({"a": "saved", "c": "dismissed", "y": "restored"}, self.GROUP)
        self.assertEqual(spread, {"a": "saved", "b": "dismissed", "c": "dismissed", "x": "restored", "y": "restored"})

    def test_applying_to_one_city_marks_the_role_applied(self):
        app = {"id": 7, "status": "applied"}
        self.assertEqual(api._spread({"c": app}, self.GROUP), {"a": app, "b": app, "c": app})

    def test_a_click_writes_every_copy_of_the_role(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "t.db"
        conn = db.connect(path)
        self.addCleanup(conn.close)
        conn.execute("INSERT INTO boards(board, ats, key, company) VALUES ('greenhouse:s', 'greenhouse', 's', 'S')")
        for ext in ("1", "2", "3"):
            conn.execute("INSERT INTO jobs(uid, board, ext_id, company, title, precision, content_hash, repost_key, "
                         "first_seen, last_seen) VALUES (?, 'greenhouse:s', ?, 'S', 'X', 'exact', '', '', ?, ?)",
                         (f"g:{ext}", ext, db.iso(NOW), db.iso(NOW)))
        entry = feedcache.Entry((), 0.0, [], groups={"g:1": "g:1", "g:2": "g:1"})
        with mock.patch.object(config, "DB_PATH", path), \
                mock.patch.dict(feedcache._ENTRIES, {(str(path), "alex", False, (), None, None, ()): entry}, clear=True):
            self.assertEqual(feedcache.copies("alex", "g:2"), ["g:1", "g:2"])
            self.assertEqual(feedcache.copies("alex", "g:3"), ["g:3"])
            api.triage(conn, "alex", "g:2", "dismissed", NOW)
        got = {r["uid"] for r in conn.execute("SELECT uid FROM triage_events WHERE profile = 'alex'")}
        self.assertEqual(got, {"g:1", "g:2"})


class FoundNotPostedTest(unittest.TestCase):
    def row(self, precision, posted, seen):
        return {"precision": precision, "first_posted_at": posted, "first_seen": seen, "seed": 0}

    def test_workday_posted_today_is_found_time_not_an_age(self):
        self.assertEqual(fresh.found_kind(self.row("day", "2026-10-21", "2026-10-21T16:41:00Z")), "day")

    def test_an_exact_post_time_is_an_age(self):
        self.assertIsNone(fresh.found_kind(self.row("exact", "2026-10-21T15:00:00Z", "2026-10-21T16:41:00Z")))

    def test_no_posted_date_says_so(self):
        self.assertEqual(fresh.found_kind(self.row("unknown", None, "2026-10-21T16:41:00Z")), "unknown")
        self.assertEqual(fresh.found_kind(self.row("updated", "2026-10-20T10:00:00Z", "2026-10-21T16:41:00Z")), "unknown")

    def test_a_late_found_day_post_is_the_day_itself(self):
        self.assertIsNone(fresh.found_kind(self.row("day", "2026-10-10", "2026-10-21T16:41:00Z")))     # "~11 d ago"


if __name__ == "__main__":
    unittest.main()
