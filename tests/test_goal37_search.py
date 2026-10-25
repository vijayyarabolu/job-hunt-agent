"""GOAL37 §1 (user 2026-10-24: "I typed in Amazon but it's showing something else, and at Recommended it should show new
jobs"): a search shows what the words NAME first. A company's search (Amazon, AWS for Amazon, a typo of it) lists the
rows that name it, and only counts the rows whose JD alone mentions it ("Amazon Bedrock"), one click away (`wide`).
Any other search (a JD skill like "LangGraph", a title like "data scientist") still lists every row, the ones that
name it first. ⌘K on a job list puts the open jobs first, the company's own jobs first, and says so when there is
none. No live DB, no network."""
import unittest
from datetime import timedelta

from job_hunt_agent import api, config, db, feedcache, feedfind, filters, searchapi, store, textsearch, tracker, trackapi
from job_hunt_agent.api import FEED_STATES
from job_hunt_agent.ats.common import FetchResult, make_job
from tests.test_goal35_search import PID, _Db

R = textsearch.Record
T = textsearch
NOW = db.utcnow().replace(microsecond=0)
BOARDS = [{"board": "lever:acme", "ats": "lever", "key": "acme", "company": "Acme", "segment": "startup"},
          {"board": "workday:amazon", "ats": "workday", "key": "amazon", "company": "Amazon", "segment": "big"},
          {"board": "greenhouse:globex", "ats": "greenhouse", "key": "globex", "company": "Globex", "segment": "startup"}]
JOBS = (("lever:acme", "AWS Machine Learning Engineer"), ("lever:acme", "Machine Learning Engineer"),
        ("workday:amazon", "Applied AI Engineer"), ("workday:amazon", "Machine Learning Engineer II"))
APPS = (("Amazon", "Applied Scientist LLM", "Seattle, WA", "2026-10-22", "applied", "LLM work", ""),
        ("Amazon", "Data Engineer, WW Ops", "Seattle, WA", "2026-10-13", "applied", "Redshift", ""),
        ("JPMorgan Chase", "Software Engineer III, ML Pipelines (Python, AWS)", "Plano, TX", "2026-10-22", "applied",
         "AWS and Python", ""),
        ("deCircle", "Founding AI Engineer", "New York, NY", "", "saved", "Agents on Amazon Bedrock with LangGraph", ""),
        ("USAA", "Data Scientist Senior", "San Antonio, TX", "", "saved", "Amazon SageMaker models", ""),
        ("Mercy", "LangGraph AI Engineer", "St. Louis, MO", "2026-10-20", "applied", "Agents", ""),
        ("Acme", "Machine Learning Engineer", "Austin, TX", "2026-10-01", "rejected", "Python", "Referred by an Amazon PM"))


def ids(found, positions) -> list:
    return [found.index.records[i].id for i in positions]


class TierTest(unittest.TestCase):
    RECS = [R(1, {"company": "deCircle", "title": "Founding AI Engineer", "jd": "Built on Amazon Bedrock"}),
            R(2, {"company": "JPMorgan Chase", "title": "Software Engineer III, ML Pipelines (Python, AWS)"}),
            R(3, {"company": "Amazon", "title": "Applied Scientist LLM"}),
            R(4, {"company": "AWS", "title": "Solutions Architect"}),
            R(5, {"company": "Acme", "title": "Amazon Marketplace Analyst"}),
            R(6, {"company": "Acme", "title": "Analyst", "notes": "Amazon referral"}),
            R(7, {"company": "Amazonia Foods", "title": "Data Engineer"})]

    def test_the_company_itself_beats_an_alias_beats_a_part_beats_the_title_beats_a_note_beats_the_jd(self):
        f = textsearch.search(self.RECS, "amazon")
        tiers = {f.index.records[i].id: f.tier(i) for i in f.positions}
        self.assertEqual(tiers, {1: T.LONG_ONLY, 2: T.LONG_ONLY, 3: T.COMPANY, 4: T.COMPANY_ALIAS, 5: T.NAMED,
                                 6: T.SHORT_FIELD, 7: T.COMPANY_PART})
        self.assertEqual(ids(f, f.ranked()), [3, 4, 7, 5, 6, 1, 2])
        self.assertTrue(f.names_company())
        self.assertEqual(ids(f, f.strong()), [3, 4, 5, 6, 7])        # not the JD's Bedrock, not a title's AWS

    def test_an_alias_names_the_company_too(self):
        f = textsearch.search(self.RECS, "aws")
        tiers = {f.index.records[i].id: f.tier(i) for i in f.positions}
        self.assertEqual((tiers[4], tiers[3], tiers[2]), (T.COMPANY, T.COMPANY_ALIAS, T.NAMED))   # "AWS" typed: a title too
        self.assertEqual(ids(f, f.strong())[:3], [2, 3, 4])

    def test_a_typo_in_the_company_is_still_the_company(self):
        f = textsearch.search([R(1, {"company": "Amazon", "title": "SDE"}), R(2, {"company": "X", "title": "Y", "jd": "amazon"})],
                              "amazn")
        self.assertEqual(f.tier(0), T.COMPANY)
        self.assertEqual(ids(f, f.strong()), [1])

    def test_a_jd_skill_named_by_one_title_still_lists_every_jd_that_asks_for_it(self):
        recs = [R(1, {"company": "Box", "title": "ML Engineer", "jd": "LangGraph agents"}),
                R(2, {"company": "Mercy", "title": "LangGraph AI Engineer"}),
                R(3, {"company": "Nike", "title": "Data Engineer", "jd": "Spark"})]
        f = textsearch.search(recs, "langgraph")
        self.assertFalse(f.names_company())
        self.assertEqual(ids(f, f.strong()), [1, 2])
        self.assertEqual(ids(f, f.ranked()), [2, 1])                  # the title that names it first

    def test_a_company_word_prefix_does_not_hide_the_jds(self):
        # "meta" starts "Metals": a part of a name, not the company named, so the JD matches stay
        f = textsearch.search([R(1, {"company": "Nox Metals", "title": "ML"}), R(2, {"company": "Box", "title": "SDE", "jd": "metadata"})],
                              "meta")
        self.assertEqual(f.tier(0), T.COMPANY_PART)
        self.assertFalse(f.names_company())
        self.assertEqual(ids(f, f.strong()), [1, 2])

    def test_meta_itself_ranks_above_metals(self):
        f = textsearch.search([R(1, {"company": "Nox Metals", "title": "ML"}), R(2, {"company": "Meta", "title": "SWE"})], "meta")
        self.assertEqual(ids(f, f.ranked()), [2, 1])

    def test_a_date_only_search_keeps_every_row(self):
        f = textsearch.search([R(1, {"company": "A"}, ("2026-10-22",)), R(2, {"company": "B"}, ("2026-10-22",))], "10/22")
        self.assertEqual(ids(f, f.strong()), [1, 2])
        self.assertFalse(f.names_company())

    def test_a_multi_word_alias_typed_whole_is_the_company(self):
        f = textsearch.search([R(1, {"company": "Amazon"}), R(2, {"company": "Box", "jd": "Amazon Web Services"})],
                              "amazon web services")
        self.assertTrue(f.names_company())
        self.assertEqual(ids(f, f.strong()), [1])

    def test_the_tokens_matched_are_the_same_as_before(self):
        index = textsearch.Index.build(self.RECS)
        for word in ("amazon", "aws", "amazn", "ai", "meta", "zzzz"):
            with self.subTest(word=word):
                want = index.wants(word)
                self.assertEqual(index.tokens(word), list(want.tokens))
                self.assertLessEqual(want.own | want.alias, set(want.tokens))


def seed_apps(conn, pid: str = PID) -> None:
    tracker.ensure_profile(conn, pid)
    sql = ("INSERT INTO applications(profile, company, role_title, location, applied_on, status, jd_text, notes) "
           "VALUES (?, ?, ?, ?, ?, ?, ?, ?)")
    with db.tx(conn):
        for r in APPS:
            conn.execute(sql, (pid, *r))


def seed_feed(conn) -> None:
    db.sync_boards(conn, BOARDS)
    run = db.start_run(conn, "test", NOW)
    scopes = ["fam:ml_engineer", "fam:ai_engineer", "fam:data_engineer"]
    for board in BOARDS:
        items = [make_job(ats=board["ats"], key=board["key"], ext_id=str(i), company=board["company"], title=title,
                          location="Seattle, WA", workplace="remote", url=f"http://127.0.0.1:9/{board['key']}/{i}",
                          posted_at=db.iso(NOW - timedelta(hours=i + 1)),
                          description="Requirements:\n- 3+ years of Python and PyTorch.\n" + "We ship ML. " * 8)
                 for i, (b, title) in enumerate(JOBS) if b == board["board"]]
        store.write_board(conn, db.board(conn, board["board"]), FetchResult(items, 200, n_raw=len(items)), "ok", run,
                          NOW, scopes, 5)
    conn.commit()


class _Seeded(_Db):
    def seed(self):
        seed_apps(self.conn)

    def seed_feed(self):
        seed_feed(self.conn)


class TrackerTest(_Seeded):
    def rows(self, q, **kw):
        return trackapi.rows(self.conn, PID, q=q, limit=trackapi.MAX_PAGE, **kw)

    def test_a_company_search_shows_the_rows_that_name_it_and_counts_the_jd_only_ones(self):
        self.seed()
        out = self.rows("Amazon")
        self.assertEqual([r["company"] for r in out["rows"]], ["Amazon", "Amazon", "Acme"])   # Acme: "Amazon" in its notes
        self.assertEqual((out["found"]["total"], out["found"]["jd_only"], out["found"]["wide"]), (3, 3, False))
        self.assertEqual(sum(out["found"]["counts"].values()), 3)

    def test_wide_lists_the_jd_only_rows_too_after_the_named_ones_in_each_status(self):
        self.seed()
        out = self.rows("Amazon", wide=True)
        self.assertEqual((out["found"]["total"], out["found"]["jd_only"], out["found"]["wide"]), (6, 3, True))
        applied = [r["company"] for r in out["rows"] if r["status"] == "applied"]
        self.assertEqual(applied, ["Amazon", "Amazon", "JPMorgan Chase"])          # named first, then the AWS title
        order = [trackapi.UI_STATUSES.index(r["status"]) for r in out["rows"]]
        self.assertEqual(order, sorted(order))                                     # still grouped by status (GOAL35)

    def test_a_skill_search_still_lists_every_row_with_the_named_one_first(self):
        self.seed()
        out = self.rows("LangGraph")
        self.assertEqual(out["found"]["jd_only"], 0)
        self.assertEqual(sorted(r["company"] for r in out["rows"]), ["Mercy", "deCircle"])

    def test_inside_a_status_the_row_that_names_it_comes_before_a_newer_jd_only_row(self):
        self.seed()
        with db.tx(self.conn):
            self.conn.execute("INSERT INTO applications(profile, company, role_title, applied_on, status, jd_text) "
                              "VALUES (?, 'Box', 'ML Engineer', '2026-10-23', 'applied', 'LangGraph agents')", (PID,))
        applied = [r["company"] for r in self.rows("LangGraph")["rows"] if r["status"] == "applied"]
        self.assertEqual(applied, ["Mercy", "Box"])

    def test_no_search_keeps_the_newest_first_order(self):
        self.seed()
        out = trackapi.rows(self.conn, PID, limit=trackapi.MAX_PAGE)
        self.assertIsNone(out["found"])
        self.assertEqual([r["company"] for r in out["rows"][:2]], ["JPMorgan Chase", "Amazon"])   # 10-22, newest id first


class FeedAndPaletteTest(_Seeded):
    def test_the_job_list_search_lists_the_companys_jobs_not_aws_titles(self):
        self.seed_feed()
        out = api.jobs(self.conn, PID, "recommended", NOW, filters.parse({"q": "amazon"}))
        self.assertEqual(sorted({j["company"] for j in out["jobs"]}), ["Amazon"])
        aws = api.jobs(self.conn, PID, "recommended", NOW, filters.parse({"q": "aws"}))
        self.assertIn("AWS Machine Learning Engineer", [j["title"] for j in aws["jobs"]])     # typed: the title says it

    def test_feedfind_keeps_the_tiers_of_what_it_found(self):
        self.seed_feed()
        kept = feedcache.ranked(self.conn, config.profile(PID), NOW, False, FEED_STATES)
        hits = feedfind.find(self.conn, PID, kept, "amazon")
        self.assertEqual(set(hits.tiers.values()), {textsearch.COMPANY})
        self.assertEqual(len(hits.uids), 2)

    def test_command_k_lists_the_companys_own_applications_first(self):
        self.seed()
        out = searchapi.everything(self.conn, PID, "amazon", NOW)
        self.assertEqual([r["company"] for r in out["applications"]["rows"]], ["Amazon", "Amazon", "Acme"])
        self.assertEqual(out["applications"]["total"], 3)

    def test_command_k_puts_the_companys_jobs_first_and_names_no_gap(self):
        self.seed()
        self.seed_feed()
        out = searchapi.everything(self.conn, PID, "aws", NOW)
        self.assertEqual([j["company"] for j in out["jobs"]][:2], ["Amazon", "Amazon"])         # Amazon before Acme's AWS title
        self.assertEqual(out["jobs"][-1]["title"], "AWS Machine Learning Engineer")
        self.assertIsNone(out["no_jobs_at"])

    def test_command_k_says_when_the_company_has_no_listed_job(self):
        self.seed()
        self.seed_feed()
        out = searchapi.everything(self.conn, PID, "globex", NOW)
        self.assertEqual((out["jobs"], out["no_jobs_at"]), ([], "Globex"))
        self.assertIn("Globex", [c["company"] for c in out["companies"]])
        mine = searchapi.everything(self.conn, PID, "usaa", NOW)                # no board: the user's row names it
        self.assertEqual(mine["no_jobs_at"], "USAA")
        self.assertIsNone(searchapi.everything(self.conn, PID, "langgraph", NOW)["no_jobs_at"])   # not a company


if __name__ == "__main__":
    unittest.main()
