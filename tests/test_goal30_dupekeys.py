"""GOAL30-app §1 #5: the Recommended list rebuilt every job's duplicate keys (company, title, place, URLs, posting
id) on every call, ~25% of the answer for 4,400 jobs. They depend only on those fields, so they are kept."""
import unittest

from job_hunt_agent import dupes

ROW = {"uid": "gh:acme:1", "company": "Acme, Inc.", "req_id": " R-1 ", "title": "Sr. ML Engineer",
       "location": "Austin, TX; Remote", "apply_url": "https://Boards.example.com/acme/1?utm_source=x",
       "url": "https://boards.example.com/acme/1", "board": "greenhouse:acme", "ext_id": 1}


class DupeKeysTest(unittest.TestCase):
    def test_keys_are_as_before_and_built_once(self):
        dupes._keys_of.cache_clear()
        keys = dupes._job_keys(ROW)
        self.assertEqual(set(keys), {("t", "acme", "senior machine learning engineer", "austin tx", "r-1"),
                                     ("u", "boards.example.com/acme/1"), ("a", "greenhouse", "acme", "1")})
        self.assertEqual(dupes._job_keys(dict(ROW)), keys)
        self.assertEqual(dupes._keys_of.cache_info().hits, 1)

    def test_a_changed_field_gives_new_keys(self):
        moved = dupes._job_keys({**ROW, "location": "Denver, CO"})
        self.assertIn(("t", "acme", "senior machine learning engineer", "denver co", "r-1"), moved)

    def test_two_copies_of_a_posting_still_group(self):
        copy = {**ROW, "uid": "gh:acme:2", "ext_id": 2, "first_posted_at": "2026-10-20T10:00:00Z"}
        self.assertEqual(dupes.jobs([{**ROW, "first_posted_at": "2026-10-19T10:00:00Z"}, copy]), {"gh:acme:1": "gh:acme:2"})


if __name__ == "__main__":
    unittest.main()
