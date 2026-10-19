"""ranking.collapse: one req posted in several locations is one entry (release review, multi-location duplicates)."""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from job_hunt_agent import db, ranking, store
from job_hunt_agent.ats.common import FetchResult, make_job

NOW = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)
JD = "Requirements:\nPython, PyTorch and AWS. Build detection models."


class CollapseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.db")
        jobs = [("1", "Senior AI Security Engineer", "Seattle, WA", JD),
                ("2", "Senior AI Security Engineer", "Dallas, TX", JD),
                ("3", "Senior AI Security Engineer", "Denver, CO", JD + " Different team, different work."),
                ("4", "Data Scientist", "Seattle, WA", JD),
                ("5", "Data Scientist", "Remote", ""),
                ("6", "Data Scientist", "Austin, TX", "")]
        items = [make_job(ats="lever", key="acme", ext_id=ext, company="Acme", title=title, location=loc,
                          url=f"https://jobs.lever.co/acme/{ext}", posted_at=db.iso(NOW), description=jd)
                 for ext, title, loc, jd in jobs]
        db.sync_boards(self.conn, [{"board": "lever:acme", "ats": "lever", "key": "acme", "company": "Acme"}])
        run = db.start_run(self.conn, "test", NOW)
        store.write_board(self.conn, db.board(self.conn, "lever:acme"), FetchResult(items, 200, n_raw=len(items)),
                          "ok", run, NOW, ["fam:ml_engineer"], 5)
        self.ranked = [({"uid": i["uid"], "company": i["company"], "title": i["title"], "location": i["location"]},
                        SimpleNamespace(worth=90 - n)) for n, i in enumerate(items)]

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_same_req_in_two_locations_is_one_entry(self):
        out = ranking.collapse(self.conn, self.ranked)
        self.assertEqual([r["location"] for r, _ in out], ["Seattle, WA", "Denver, CO", "Seattle, WA"])
        self.assertEqual([loc for _, loc in out[0][0]["also"]], ["Dallas, TX"])     # best-ranked copy leads

    def test_a_different_jd_is_never_merged(self):
        out = ranking.collapse(self.conn, self.ranked)
        self.assertEqual([len(r["also"]) for r, _ in out], [1, 0, 2])

    def test_no_jd_copies_in_other_places_join_the_same_title(self):
        # same company + same title in several locations is one card; with no JD text to compare, only
        # a different place says it is a copy
        out = ranking.collapse(self.conn, self.ranked)
        self.assertEqual([loc for _, loc in out[2][0]["also"]], ["Remote", "Austin, TX"])

    def test_a_near_identical_jd_on_another_board_is_the_same_req(self):
        items = [make_job(ats="greenhouse", key="acmeco", ext_id="g1", company="Acme", title="Senior AI Security Engineer",
                          location="Seattle, WA", url="https://boards.greenhouse.io/acmeco/jobs/g1",
                          posted_at=db.iso(NOW), description=JD),
                 make_job(ats="greenhouse", key="acmeco", ext_id="g2", company="Acme", title="Senior AI Security Engineer",
                          location="Seattle, WA", url="https://boards.greenhouse.io/acmeco/jobs/g2",
                          posted_at=db.iso(NOW), description="")]
        db.sync_boards(self.conn, [{"board": "greenhouse:acmeco", "ats": "greenhouse", "key": "acmeco", "company": "Acme"}])
        run = db.start_run(self.conn, "test", NOW)
        store.write_board(self.conn, db.board(self.conn, "greenhouse:acmeco"), FetchResult(items, 200, n_raw=2), "ok",
                          run, NOW, ["fam:ml_engineer"], 5)
        extra = [({"uid": i["uid"], "company": "Acme", "title": i["title"], "location": i["location"]},
                  SimpleNamespace(worth=10)) for i in items]
        out = ranking.collapse(self.conn, self.ranked + extra)
        self.assertEqual(len(out[0][0]["also"]), 2)            # Dallas + the other board's Seattle copy
        self.assertEqual(len(out), 3)                          # the no-JD Seattle copy joins a lead in another place

    def test_a_location_line_does_not_make_another_req(self):
        long_jd = " ".join(f"skill{n}" for n in range(60))
        self.assertTrue(ranking.same_req(set((long_jd + " Seattle WA").split()), set((long_jd + " Dallas TX").split()),
                                         "Seattle, WA", "Dallas, TX"))
        self.assertFalse(ranking.same_req(set("python pytorch".split()), set("sales quota".split()), "A", "B"))
        self.assertTrue(ranking.same_req(set(), {"x"}, "Seattle, WA", "Remote"))
        self.assertFalse(ranking.same_req(set(), {"x"}, "Seattle, WA", "Seattle, WA"))

    def test_the_number_of_cards_does_not_depend_on_the_sort(self):
        # Alex's feed said "300 of 3993" under Newest and "300 of 4004" under Best match. Booz Allen's "Data
        # Engineer": three copies with no JD, two in McLean and one in Fort Belvoir; a no-JD copy pairs with any
        # other place, so whichever came first decided whether the two McLean copies shared a card
        def row(uid, loc, worth):
            return ({"uid": uid, "company": "Booz", "title": "Data Engineer", "location": loc}, SimpleNamespace(worth=worth))
        a, b, c = row("x:a", "McLean, VA", 3), row("x:b", "Fort Belvoir, VA", 2), row("x:c", "McLean, VA", 1)
        counts = {len(ranking.collapse(self.conn, order)) for order in ([a, b, c], [b, a, c], [c, b, a])}
        self.assertEqual(len(counts), 1, counts)
        out = ranking.collapse(self.conn, [b, a, c])
        self.assertEqual(out[0][0]["uid"], "x:b")                        # the first in the given order still leads

    def test_input_rows_are_not_mutated(self):
        ranking.collapse(self.conn, self.ranked)
        self.assertNotIn("also", self.ranked[0][0])


if __name__ == "__main__":
    unittest.main()
