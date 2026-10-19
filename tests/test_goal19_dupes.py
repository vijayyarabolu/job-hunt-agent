"""GOAL19 §3 duplicates: the same posting twice for one person (company + near title + place, the same posting URL or
the same board posting id; tracker rows also by their feed job), the newest kept; what is not a duplicate (another
level, another place, another requisition id, a careers page shared by many); a removed duplicate hides only itself."""
import unittest

from job_hunt_agent import db, dupes, removal, trackapi
from tests.test_goal19_grid import DbCase
from tests.test_goal19_reasons import seed_more


def job(uid, title="Machine Learning Engineer", location="Austin, TX", posted="2026-10-10T10:00:00Z", **extra):
    return {"uid": uid, "company": "Acme", "title": title, "location": location, "first_posted_at": posted,
            "url": f"https://jobs.example.com/{uid}", "apply_url": None, **extra}


class FeedDuplicatesTest(unittest.TestCase):
    def test_same_company_near_title_and_place_keeps_the_newest(self):
        rows = [job("a"), job("b", title="Machine-Learning Engineer", posted="2026-10-11T10:00:00Z"),
                job("c", title="ML Engineer", posted="2026-10-09T10:00:00Z")]
        self.assertEqual(dupes.jobs(rows), {"a": "b", "c": "b"})

    def test_another_level_place_or_requisition_is_another_job(self):
        self.assertEqual(dupes.jobs([job("a"), job("b", title="Senior Machine Learning Engineer")]), {})
        self.assertEqual(dupes.jobs([job("a"), job("b", location="Seattle, WA")]), {})
        self.assertEqual(dupes.jobs([job("a", req_id="P-1"), job("b", req_id="P-2")]), {})

    def test_a_shared_requisition_id_alone_is_not_a_duplicate(self):
        rows = [job("a", title="Software Engineer, MDX", req_id="P-113069"),
                job("b", title="Software Engineer, Gen AI", req_id="P-113069")]
        self.assertEqual(dupes.jobs(rows), {})

    def test_the_same_posting_url_or_board_posting_id(self):
        same_url = [job("a", title="AI Engineer", apply_url="https://acme.com/jobs/77?utm_source=x"),
                    job("b", title="Applied AI Engineer", apply_url="https://acme.com/jobs/77", posted="2026-10-11T00:00:00Z")]
        self.assertEqual(dupes.jobs(same_url), {"a": "b"})
        same_id = [job("a", title="AI Engineer", board="greenhouse:acme", ext_id="9"),
                   job("b", title="AI Eng", board="greenhouse:acme-eu", ext_id="9", posted="2026-10-11T00:00:00Z")]
        self.assertEqual(dupes.jobs(same_id), {"a": "b"})

    def test_the_same_posting_id_at_two_companies_is_two_jobs(self):
        rows = [job("a", title="Data Engineer", board="bamboohr:acme", ext_id="45"),
                {**job("b", title="ML Engineer", location="Remote", board="bamboohr:zeta", ext_id="45"), "company": "Zeta Labs"}]
        self.assertEqual(dupes.jobs(rows), {})

    def test_a_careers_page_shared_by_many_pairs_nothing(self):
        rows = [job(str(i), title=f"Role {i}", apply_url="https://acme.com/careers") for i in range(5)]
        self.assertEqual(dupes.jobs(rows), {})
        query = [job(str(i), title=f"Role {i}", apply_url=f"https://stripe.com/jobs/search?gh_jid={i}") for i in range(2)]
        self.assertEqual(dupes.jobs(query), {})                       # the query is the posting: two postings

    def test_groups_chain(self):
        rows = [job("a", apply_url="https://acme.com/jobs/1"),
                job("b", title="AI Engineer", apply_url="https://acme.com/jobs/1", posted="2026-10-11T00:00:00Z"),
                job("c", title="AI Engineer", posted="2026-10-12T00:00:00Z")]
        self.assertEqual(dupes.jobs(rows), {"a": "c", "b": "c"})


class TrackerDuplicatesTest(DbCase):
    def test_a_row_typed_twice_is_a_duplicate_of_the_newer(self):
        older = trackapi.create(self.conn, "alex", {"company": "Chewy", "role_title": "Machine Learning Engineer II",
                                                     "date_applied": "09/10/2026"})["id"]
        newer = trackapi.create(self.conn, "alex", {"company": "Chewy ", "role_title": "Machine Learning Engineer II",
                                                     "date_applied": "09/18/2026"})["id"]
        trackapi.create(self.conn, "jordan", {"company": "Chewy", "role_title": "Machine Learning Engineer II"})
        self.assertEqual(dupes.apps(self.conn, "alex"), {older: newer})
        rows = {r["id"]: r for r in trackapi.rows(self.conn, "alex")["rows"]}
        self.assertEqual(rows[older]["dup_of"]["id"], newer)
        self.assertIsNone(rows[newer]["dup_of"])
        self.assertEqual(dupes.apps(self.conn, "jordan"), {})          # one person's rows only


    def test_a_new_application_months_later_is_not_a_duplicate(self):
        trackapi.create(self.conn, "alex", {"company": "Chewy", "role_title": "MLE II", "date_applied": "12/18/2025"})
        trackapi.create(self.conn, "alex", {"company": "Chewy", "role_title": "MLE II", "date_applied": "05/13/2026"})
        self.assertEqual(dupes.apps(self.conn, "alex"), {})

    def test_a_row_typed_twice_before_a_re_application_is_still_found(self):
        ids = [trackapi.create(self.conn, "alex", {"company": "Chewy", "role_title": "MLE II", "date_applied": d})["id"]
               for d in ("12/10/2025", "12/12/2025", "05/13/2026")]
        self.assertEqual(dupes.apps(self.conn, "alex"), {ids[0]: ids[1]})

    def test_a_repost_saved_months_later_is_not_a_duplicate_of_the_old_application(self):
        trackapi.create(self.conn, "alex", {"company": "Chewy", "role_title": "MLE II", "date_applied": "01/05/2026"})
        trackapi.create(self.conn, "alex", {"company": "Chewy", "role_title": "MLE II", "status": "saved"})
        self.assertEqual(dupes.apps(self.conn, "alex"), {})


class RemoveDuplicateTest(DbCase):
    def test_paid_lookups_and_mail_stay_and_come_back_linked(self):
        ids = {}
        for table in removal.UNLINK:
            cols = [c for c in self.conn.execute(f"PRAGMA table_info({table})") if c[3] and not c[5] and c[4] is None]
            row = {c[1]: ("recorded" if c[1] == "state" else 1 if c[2].upper().startswith("INT") else "x") for c in cols}
            row["app_id"] = self.a
            with db.tx(self.conn):
                cur = self.conn.execute(f"INSERT INTO {table}({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                                        list(row.values()))
            ids[table] = cur.lastrowid
        out = removal.remove_app(self.conn, "alex", self.a)
        link = lambda t: self.conn.execute(f"SELECT app_id FROM {t} WHERE id = ?", (ids[t],)).fetchone()
        self.assertEqual([link(t)[0] for t in removal.UNLINK], [None, None])     # kept, unlinked
        removal.undo(self.conn, "alex", out["removed"])
        self.assertEqual([link(t)[0] for t in removal.UNLINK], [self.a, self.a])

    def test_a_removed_duplicate_hides_only_itself(self):
        kept = seed_more(self.conn, "k1", "Acme")
        gone = seed_more(self.conn, "k2", "Acme")
        removal.remove_job(self.conn, "alex", gone, "duplicate")
        self.assertEqual(removal.hidden(self.conn, "alex"), ({gone}, set()))
        row = self.conn.execute("SELECT uid, company, title, location FROM jobs WHERE uid = ?", (kept,)).fetchone()
        self.assertEqual(len(removal.keep(self.conn, "alex", [dict(row)])), 1)
        removal.remove_job(self.conn, "alex", kept, "unwanted")     # not wanted: its reposts stay hidden too
        self.assertEqual(len(removal.hidden(self.conn, "alex")[1]), 1)


if __name__ == "__main__":
    unittest.main()
