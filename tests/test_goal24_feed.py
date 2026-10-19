"""GOAL24 §3/§4/§6 on a throwaway DB with the synthetic filings: each job's badge on the feed, the Recommended
order (sponsors first inside the same day / match band, No sponsorship last, nothing hidden), the switch's counts and
filter, "Load more" paging without a cap, the tracker row's badge, callbacks by badge in Stats, and the extension's
future-sponsorship answer (Yes at Sponsors / Cap-exempt while the Answers-page setting is On)."""
from types import SimpleNamespace

from job_hunt_agent import api, db, filters, stats, tracker
from job_hunt_agent.autofill import api as afapi, bank, sponsor
from job_hunt_agent.h1b import feed, label

from tests.test_goal8_flow import NOW, PID
from tests.test_goal24_h1b import H1bCase

FUTURE_Q = {"id": "f1", "label": "Will you now or in the future require visa sponsorship?", "kind": "radio",
            "options": ["Yes", "No"], "required": True}
NOW_Q = {"id": "f2", "label": "Do you require sponsorship to work in the US?", "kind": "radio", "options": ["Yes", "No"],
         "required": True}


def badge(kind):
    return label.Badge(kind, kind, "why", "filings")


def pair(uid, posted):
    return ({"uid": uid, "first_posted_at": posted, "precision": "exact" if posted else None, "first_seen": posted},
            SimpleNamespace())


class OrderTest(H1bCase):
    def test_sponsors_lead_their_band_and_no_sponsorship_goes_last(self):
        pairs = [pair(u, None) for u in ("unknown90", "maybe90", "spons90", "no95", "unknown70", "spons70")]
        pcts = {"unknown90": 92, "maybe90": 91, "spons90": 90, "no95": 95, "unknown70": 75, "spons70": 72}
        found = {"maybe90": badge("maybe"), "spons90": badge("sponsors"), "no95": badge("no"), "spons70": badge("cap_exempt")}
        got = [r["uid"] for r, _ in feed.order(pairs, "match", pcts, found)]
        # 90s band: sponsors, unknown, maybe; 70s band: sponsors, unknown; the 95 % No-sponsorship job last
        self.assertEqual(got, ["spons90", "unknown90", "maybe90", "spons70", "unknown70", "no95"])

    def test_a_fresh_job_is_never_pushed_below_an_older_sponsor(self):
        today, older = "2026-10-14T15:00:00Z", "2026-10-10T15:00:00Z"
        pairs = [pair("fresh_unknown", today), pair("old_sponsor", older)]
        got = [r["uid"] for r, _ in feed.order(pairs, "newest", {}, {"old_sponsor": badge("sponsors")})]
        self.assertEqual(got, ["fresh_unknown", "old_sponsor"])

    def test_switch_counts_and_filter(self):
        pairs = [pair(u, None) for u in ("a", "b", "c", "d", "e")]
        found = {"a": badge("sponsors"), "b": badge("cap_exempt"), "c": badge("maybe"), "d": badge("no")}
        self.assertEqual(feed.counts(pairs, found), {"friendly": 3, "no": 1, "all": 5})
        self.assertEqual([r["uid"] for r, _ in feed.only(pairs, found, "friendly")], ["a", "b", "c"])
        self.assertEqual([r["uid"] for r, _ in feed.only(pairs, found, "no")], ["d"])
        self.assertEqual(len(feed.only(pairs, found, None)), 5)                # All: Unknown too


class FeedApiTest(H1bCase):
    def setUp(self):
        super().setUp()
        with db.tx(self.conn):
            for uid, company, said in zip(self.uids, ("Acme Robotics", "Nobody Filed Inc", "Initech"), (None, None, "no")):
                self.conn.execute("UPDATE jobs SET company = ?, sponsorship = ? WHERE uid = ?", (company, said, uid))
        label.forget()

    def test_cards_carry_badges_and_the_switch_filters(self):
        out = api.jobs(self.conn, PID, "recommended", NOW, filters.parse({}))
        kinds = {c["company"]: (c["sponsor"] or {}).get("kind") for c in out["jobs"]}
        self.assertEqual(kinds, {"Acme Robotics": "sponsors", "Nobody Filed Inc": None, "Initech": "no"})
        self.assertEqual(out["jobs"][-1]["company"], "Initech")                # No sponsorship: shown, ranked last
        self.assertEqual(out["spons"], {"friendly": 1, "no": 1, "all": 3})
        friendly = api.jobs(self.conn, PID, "recommended", NOW, filters.parse({"spons": "friendly"}))
        self.assertEqual([c["company"] for c in friendly["jobs"]], ["Acme Robotics"])
        self.assertEqual(friendly["spons"]["all"], 3)                          # counts stay those of the whole list
        paged = api.jobs(self.conn, PID, "recommended", NOW, filters.parse({"offset": "2"}))
        self.assertEqual((paged["offset"], len(paged["jobs"]), paged["total"]), (2, 1, 3))   # Load more, no cap

    def test_tracker_rows_and_stats_by_badge(self):
        tracker.ensure_profile(self.conn, PID)
        with db.tx(self.conn):
            for company, status in (("Acme Robotics", "interview"), ("Acme Robotics", "applied"), ("Initech", "applied")):
                self.conn.execute("INSERT INTO applications(profile, company, role_title, applied_on, status) "
                                  "VALUES (?, ?, 'ML Engineer', '2026-10-01', ?)", (PID, company, status))
        apps = [dict(r) for r in self.conn.execute("SELECT id, company, NULL AS job_sponsorship FROM applications")]
        views = feed.app_views(self.conn, PID, apps)
        self.assertEqual(sorted((v or {}).get("kind") for v in views.values()), ["maybe", "sponsors", "sponsors"])
        rows = {r["key"]: (r["n"], r["responses"]) for r in stats.report(self.conn, PID)["by_sponsor"]}
        self.assertEqual(rows["sponsors"], (2, 1))
        self.assertEqual(rows["maybe"], (1, 0))


class AutofillSponsorTest(H1bCase):
    def setUp(self):
        super().setUp()
        with db.tx(self.conn):
            self.conn.execute("UPDATE jobs SET company = 'Acme Robotics' WHERE uid = ?", (self.uids[0],))
            self.conn.execute("UPDATE jobs SET company = 'Initech' WHERE uid = ?", (self.uids[1],))
        bank.save(self.conn, PID, "sponsor_future", "No")
        bank.save(self.conn, PID, "sponsor_now", "No")
        label.forget()

    def answers(self, uid):
        got = afapi._resolve(self.conn, PID, {"uid": uid, "fields": [FUTURE_Q, NOW_Q], "board": "greenhouse"})
        return [(a["key"], a["value"], a["source"]) for a in got["answers"]]

    def test_yes_to_the_future_question_at_a_sponsor_only(self):
        self.assertEqual(self.answers(self.uids[0]), [("sponsor_future", "Yes", "sponsor"), ("sponsor_now", "No", "bank")])
        self.assertEqual(self.answers(self.uids[1]), [("sponsor_future", "No", "bank"), ("sponsor_now", "No", "bank")])

    def test_the_setting_off_keeps_the_saved_answer(self):
        bank.save(self.conn, PID, "sponsor_auto", "off")
        self.assertEqual(self.answers(self.uids[0])[0], ("sponsor_future", "No", "bank"))
        with self.assertRaises(ValueError):
            bank.save(self.conn, PID, "sponsor_auto", "maybe")

    def test_an_older_bank_gets_the_setting_on(self):
        with db.tx(self.conn):
            self.conn.execute("DELETE FROM autofill_answers WHERE key = 'sponsor_auto'")
        self.assertEqual(bank.load(self.conn, PID)["sponsor_auto"]["answer"], "On")
        self.assertIn("sponsor_auto", [r["key"] for r in bank.listing(self.conn, PID) if r["category"] == "legal"])

    def test_the_panel_shows_the_badge_and_the_answer_before_filling(self):
        got = afapi.match_page(self.conn, PID, {"uid": self.uids[0]})
        self.assertEqual(got["sponsor"]["badge"]["kind"], "sponsors")
        self.assertEqual((got["sponsor"]["future"], got["sponsor"]["setting"]), ("Yes", True))
        other = afapi.match_page(self.conn, PID, {"uid": self.uids[1]})
        self.assertEqual((other["sponsor"]["badge"]["text"], other["sponsor"]["future"]), ("Sponsored before", "No"))
        self.assertEqual(sponsor.panel(None, {})["why"], "not saved yet: it will ask you")
