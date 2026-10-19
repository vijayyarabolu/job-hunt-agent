"""GOAL21 area 3 server side: the head's "N duplicates" opens a list of just the older copies (each says which newer
job it repeats: × removes it), and "new matches" counts the last 24 hours (it said "0 new matches today" at 2 AM next
to 54 jobs under a day old)."""
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

import docx

from job_hunt_agent import api, config, db, filters, fits, store
from job_hunt_agent.ats.common import FetchResult, make_job
from tests.test_goal8_flow import PROFILE

NOW = db.utcnow().replace(microsecond=0)
LINE = "- 3+ years building ML systems in Python and PyTorch on AWS."


def _job(i: int, title: str, minutes: int, req: str | None = "auto", location: str = "Austin, TX", edit: str = "") -> dict:
    return make_job(ats="lever", key="acme", ext_id=str(i), company="Acme", title=title, location=location,
                    url=f"http://127.0.0.1:9/acme/{i}", req_id=f"R-{100 + i}" if req == "auto" else req,
                    posted_at=db.iso(NOW - timedelta(minutes=minutes)),
                    description=f"About the role ({title}).{edit}\nRequirements:\n{LINE}\n" + "We ship ML products. " * 8)


class Goal21ListTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        resumes = root / "profiles" / "tester" / "resumes"
        resumes.mkdir(parents=True)
        (root / "profiles" / "tester" / "profile.yaml").write_text(PROFILE)
        doc = docx.Document()
        doc.add_paragraph("Tess Tester. ML Engineer. Python, PyTorch, Kubernetes, AWS. 2021 - 2025.")
        doc.save(resumes / "Tess_ML.docx")
        self.patches = [mock.patch.object(config, "DB_PATH", root / "t.db"), mock.patch.object(config, "PROFILES", root / "profiles")]
        for p in self.patches:
            p.start()
        self.conn = db.connect()
        db.sync_boards(self.conn, [{"board": "lever:acme", "ats": "lever", "key": "acme", "company": "Acme"}])
        run = db.start_run(self.conn, "test", NOW)
        items = [_job(1, "Machine Learning Engineer", 30), _job(2, "Applied ML Engineer", 60 * 20),
                 _job(3, "Search Machine Learning Engineer", 90, req=None),            # the newer copy
                 _job(4, "Search Machine Learning Engineer", 60 * 26, req=None, edit=" Reposted."),   # the older copy, 26 h old
                 _job(5, "Ranking Machine Learning Engineer", 60 * 30)]
        store.write_board(self.conn, db.board(self.conn, "lever:acme"), FetchResult(items, 200, n_raw=len(items)), "ok", run, NOW,
                          ["fam:ml_engineer"], 5)
        self.uid = {i["ext_id"]: i["uid"] for i in items}
        fits.refresh(self.conn, config.profile("tester"), NOW)

    def tearDown(self):
        self.conn.close()
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def listed(self, **args):
        return api.jobs(self.conn, "tester", "recommended", NOW, filters.parse(args))

    def test_the_list_counts_and_marks_the_duplicate(self):
        out = self.listed()
        self.assertEqual(out["dupes"], 1)
        cards = {c["uid"]: c for c in out["jobs"]}
        self.assertEqual(cards[self.uid["4"]]["dup_of"]["uid"], self.uid["3"])
        self.assertNotIn("dup_of", cards[self.uid["3"]])

    def test_duplicates_only_lists_just_the_older_copies_still_marked(self):
        out = self.listed(dupes="1")
        self.assertEqual([c["uid"] for c in out["jobs"]], [self.uid["4"]])
        self.assertEqual(out["jobs"][0]["dup_of"]["uid"], self.uid["3"])
        self.assertEqual(out["dupes"], 1)
        self.assertEqual(out["total"], 1)

    def test_the_filter_reads_dupes_from_the_address(self):
        self.assertTrue(filters.parse({"dupes": "1"}).dupes_only)
        self.assertFalse(filters.parse({}).dupes_only)

    def test_new_matches_are_the_last_24_hours_not_since_midnight(self):
        today = self.listed()["today"]
        self.assertEqual(today["total"], 3)                     # 30 min, 20 h and 90 min old; not 26 h or 30 h
        self.assertEqual(today["since"], db.iso(NOW - timedelta(hours=24)))


if __name__ == "__main__":
    unittest.main()
