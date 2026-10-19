"""GOAL12 rules on fixture emails (real-style rejection / assessment / interview / offer / confirmation texts and
look-alikes that are not job replies): classification, which application, what happens to the status, and that a
non-job email keeps nothing it said. Each fixture runs against a fresh tracker."""
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from job_hunt_agent import config, db, history, tracker, trackapi
from job_hunt_agent.mail import Mail, classify, llm, match, parse, poll, update

FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "mail" / "emails.json").read_text())
SENT = datetime(2026, 10, 8, 15, 0, tzinfo=timezone.utc)
TRACKER = [("Acme Robotics", "Machine Learning Engineer", None), ("Ramp", "Applied AI Engineer", None),
           ("Northwind Health", "Senior Data Scientist", "R-20431"),
           ("Northwind Health", "Machine Learning Engineer", "R-20500"), ("Globex", "Data Engineer II", None),
           ("Lumen Labs", "AI Engineer", None), ("Tidewater AI", "ML Platform Engineer", None),
           ("Pinnacle Bank", "Machine Learning Engineer", None), ("Orbital Systems", "Data Scientist", None),
           ("Capital One", "Senior Data Scientist", None), ("Capital One", "Machine Learning Engineer, Card", None),
           ("Delta Freight", "Data Analyst", None), ("Juniper Grid", "Data Engineer", None),
           ("Arbor Logic", "Machine Learning Engineer", None), ("Brightline", "Data Engineer", None),
           ("Quantia", "Applied Scientist", None), ("Fernway", "Data Scientist", None),
           ("Vantage Point", "Machine Learning Engineer", None), ("Helix Bio", "Bioinformatics Data Scientist", None),
           ("Stripe", "Data Engineer", None), ("Cobalt Data", "ML Engineer", None),
           ("Notion", "Data Scientist, Growth", None), ("Northstar Analytics", "Data Scientist", None),
           ("Workday", "Machine Learning Engineer III", None),        # applied to Workday the company (an ATS too)
           ("Silverline", "Analytics Engineer", None), ("Meridian AI", "ML Engineer", None)]


class MailCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for name in ("config", "data", "profiles", "applications"):
            (self.root / name).mkdir()
        (self.root / "profiles" / "jane").mkdir()
        (self.root / "profiles" / "jane" / "profile.yaml").write_text("name: Jane Doe\nfamilies: [ml_engineer]\n")
        self.patches = [mock.patch.object(config, "CONFIG", self.root / "config"),
                        mock.patch.object(config, "DATA", self.root / "data"),
                        mock.patch.object(config, "PROFILES", self.root / "profiles"),
                        mock.patch.object(config, "APPLICATIONS", self.root / "applications"),
                        mock.patch.object(config, "DB_PATH", self.root / "data" / "t.db"),
                        mock.patch.dict("os.environ", {"JOB_HUNT_AGENT_NO_NOTIFY": "1"})]
        for p in self.patches:
            p.start()
        self.conn = None
        self.fresh()

    def fresh(self, name: str = "t.db"):
        if self.conn:
            self.conn.close()
        self.conn = db.connect(self.root / "data" / name)
        self.conn.execute("INSERT INTO profiles(id, name) VALUES ('jane', 'Jane Doe')")
        db.sync_boards(self.conn, [{"board": "workday:nw", "ats": "workday", "key": "nw", "company": "Northwind"}])
        self.ids = {}
        for company, title, req in TRACKER:
            self.ids[(company, title)] = self.add_app(company, title, req=req)

    def tearDown(self):
        self.conn.close()
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def add_app(self, company, title, status="applied", applied_on="2026-09-20", req=None) -> int:
        uid = None
        with db.tx(self.conn):
            if req:
                uid = f"wd:{req}"
                self.conn.execute("INSERT INTO jobs(uid, board, ext_id, company, title, precision, content_hash, "
                                  "repost_key, first_seen, last_seen, req_id) VALUES (?, 'workday:nw', ?, ?, ?, "
                                  "'unknown', 'h', 'k', '2026-09-01T00:00:00Z', '2026-09-01T00:00:00Z', ?)",
                                  (uid, req, company, title, req))
            cur = self.conn.execute("INSERT INTO applications(profile, job_uid, company, role_title, status, "
                                    "applied_on, date_applied, source) VALUES ('jane', ?, ?, ?, ?, ?, ?, 'feed')",
                                    (uid, company, title, status, applied_on, applied_on))
            self.conn.execute("INSERT INTO application_events(app_id, at, from_status, to_status, source) "
                              "VALUES (?, '2026-09-20T12:00:00Z', NULL, ?, 'import')", (cur.lastrowid, status))
        return cur.lastrowid

    def app(self, app_id: int) -> dict:
        return dict(self.conn.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone())

    def mail(self, case: dict, msg_id: str = "m1", sent: datetime = SENT) -> Mail:
        return Mail(msg_id, case["from"], case["subject"], sent, case["body"])

    def run_one(self, mail: Mail, llm_answer: str | None = None) -> dict:
        calls = []

        def runner(prompt):
            calls.append(prompt)
            return {"results": [{"i": 0, "kind": llm_answer}]} if llm_answer else None
        poll.process(self.conn, "jane", [mail], SENT + timedelta(minutes=5), runner)
        row = self.conn.execute("SELECT * FROM mail_messages WHERE msg_id = ?", (mail.msg_id,)).fetchone()
        return {**dict(row), "llm_calls": len(calls)}


class FixtureTest(MailCase):
    def _company_rows(self, company):
        return [i for (c, _), i in self.ids.items() if c == company]

    def _check(self, case: dict) -> list[str]:
        self.fresh(f"{case['id']}.db")
        wrong = []
        if case.get("start"):
            for app_id in self._company_rows(case["company"]):
                self.conn.execute("UPDATE applications SET status = ? WHERE id = ?", (case["start"], app_id))
        if case.get("hand_after"):
            for app_id in self._company_rows(case["company"]):
                self.conn.execute("INSERT INTO application_events(app_id, at, from_status, to_status, source) "
                                  "VALUES (?, '2026-10-08T16:00:00Z', 'applied', ?, 'ui')", (app_id, case["start"]))
        self.conn.commit()
        row = self.run_one(self.mail(case), case.get("llm"))
        if row["state"] != case["outcome"]:
            wrong.append(f"outcome {row['state']} != {case['outcome']} ({row['reason']})")
        if row["kind"] != case["kind"]:
            wrong.append(f"kind {row['kind']} != {case['kind']}")
        if case.get("company") and row["app_id"]:
            app = self.app(row["app_id"])
            if app["company"] != case["company"]:
                wrong.append(f"company {app['company']} != {case['company']}")
            if case.get("title") and app["role_title"] != case["title"]:
                wrong.append(f"title {app['role_title']} != {case['title']}")
            if row["state"] == "applied" and app["status"] != row["to_status"]:
                wrong.append(f"status {app['status']} != {row['to_status']}")
        elif case.get("company") and case["outcome"] != "ignored":
            wrong.append("no application linked")
        if row["state"] == "ignored" and any(row[k] for k in ("sender", "subject", "snippet", "link")):
            wrong.append("a non-job email kept what it said")
        if bool(row["llm_calls"]) != bool(case.get("llm")):
            wrong.append(f"Claude calls {row['llm_calls']}")
        return wrong

    def test_every_fixture_email_is_handled_right(self):
        self.assertGreaterEqual(len(FIXTURES), 30)
        misses = {case["id"]: self._check(case) for case in FIXTURES}
        misses = {k: v for k, v in misses.items() if v}
        right = len(FIXTURES) - len(misses)
        print(f"\nmail fixtures: {right}/{len(FIXTURES)} right ({100 * right / len(FIXTURES):.0f}%)")
        self.assertEqual(misses, {})

    def test_the_real_emails_are_all_right_and_the_whole_set_at_least_95_percent(self):      # GOAL14 §1c
        real = [c for c in FIXTURES if c.get("real")]
        self.assertGreaterEqual(len(real), 8)                       # Alex's first check, anonymised
        self.assertGreaterEqual(len(FIXTURES) - len(real), 40)      # typical Workday / Greenhouse / Lever / ... mail
        wrong = {c["id"]: w for c in FIXTURES if (w := self._check(c))}
        self.assertEqual({k: v for k, v in wrong.items() if any(c["id"] == k and c.get("real") for c in real)}, {})
        self.assertGreaterEqual(1 - len(wrong) / len(FIXTURES), 0.95, wrong)

    def test_the_kinds_cover_every_status(self):
        kinds = {c["kind"] for c in FIXTURES}
        self.assertTrue({"rejected", "screen", "interview", "offer", "received", "other"} <= kinds)


class RuleTest(unittest.TestCase):
    def test_job_context_is_required(self):
        self.assertEqual(classify.classify("Payment", "Unfortunately your card was declined.").kind, "other")
        self.assertEqual(classify.classify("Hi", "Unfortunately we will not be moving forward with your "
                                                 "application.").kind, "rejected")

    def test_disagreement_goes_to_claude(self):
        verdict = classify.classify("Offer?", "We regret to inform you ... we are pleased to offer you the role")
        self.assertIsNone(verdict.kind)
        self.assertIn(verdict.guess, ("rejected", "offer"))

    def test_received_is_outranked_by_any_status(self):
        verdict = classify.classify("Thanks for applying", "Thank you for applying. Please complete the online "
                                                          "assessment for this role.")
        self.assertEqual(verdict.kind, "screen")

    def test_transitions_only_move_forward(self):
        self.assertEqual(update.transition("applied", "interview")[0], update.MOVE)
        self.assertEqual(update.transition("interview", "screen")[0], update.SAME)
        self.assertEqual(update.transition("offer", "rejected")[0], update.CONFLICT)
        self.assertEqual(update.transition("interview", "rejected")[0], update.MOVE)
        self.assertEqual(update.transition("rejected", "interview")[0], update.CONFLICT)
        self.assertEqual(update.transition("ghosted", "screen")[0], update.MOVE)
        self.assertEqual(update.transition("ghosted", "applied")[0], update.MOVE)
        self.assertEqual(update.transition("applied", "applied")[0], update.SAME)


class MatchTest(unittest.TestCase):
    APPS = [match.App(1, "Ramp", "Applied AI Engineer", "applied", "2026-09-01", None, ("ramp",)),
            match.App(2, "Notion", "Data Scientist", "applied", "2026-09-01", None, ("notion",)),
            match.App(3, "Stripe", "Data Engineer", "applied", "2026-09-01", None, ("stripe",)),
            match.App(4, "Scale AI", "ML Engineer", "applied", "2026-09-01", None, ("scale",))]

    def test_lowercase_words_are_not_companies(self):
        found = match.find("Weekly <a@news.dev>", "How to ramp up", "the notion of scale", self.APPS)
        self.assertIsNone(found.key)

    def test_a_digest_naming_three_companies_matches_none(self):
        found = match.find("Alerts <alerts@jobs.example>", "New jobs", "Ramp\nNotion\nStripe\nApply now", self.APPS)
        self.assertIsNone(found.key)

    def test_an_ats_digest_still_counts_its_sender(self):
        found = match.find("Stripe <no-reply@greenhouse.io>", "Your application", "Ramp Notion Stripe", self.APPS)
        self.assertEqual(found.app.id, 3)

    def test_company_guess_from_the_sender(self):
        self.assertEqual(match.guess_company("Zephyr Motors Talent <no-reply@greenhouse.io>"), "Zephyr Motors")
        self.assertEqual(match.guess_company("<jobs@kestrel.com>"), "Kestrel")
        self.assertEqual(match.guess_company("AEP@myworkday.com"), "AEP")                 # the ATS mailbox names it
        self.assertEqual(match.guess_company("kestrelpower@myworkday.com"), "Kestrelpower")
        self.assertIsNone(match.guess_company("no-reply@myworkday.com"))

    def test_the_board_tenant_is_a_company_name(self):
        self.assertEqual(match.board_alias("workday:aep/wd1/AEPCareerSite"), "aep")      # AEP = "Ohio Power Company"
        self.assertEqual(match.board_alias("greenhouse:acmerobotics"), "acmerobotics")
        self.assertIsNone(match.board_alias("oracle_recruiting:ibwujb.fa.ocs.oraclecloud.com/CX_1001"))
        self.assertIsNone(match.board_alias("greenhouse:talkdesk2"))
        self.assertIsNone(match.board_alias(None))
        ohio = match.App(6, "Ohio Power Company", "Data Scientist", "applied", "2026-10-08", "R19850",
                         ("ohio power company", "aep"))
        found = match.find("AEP@myworkday.com", "Thank you for applying to AEP!", "Dear Jane", [*self.APPS, ohio])
        self.assertEqual(found.app.id, 6)

    def test_an_ats_name_never_matches_that_company(self):
        apps = [*self.APPS, match.App(5, "Workday", "ML Engineer III", "applied", "2026-09-01", None, ("workday",))]
        self.assertIsNone(match.find("AEP@myworkday.com", "Thank you for applying to AEP!", "Dear Jane", apps).key)


class ParseTest(unittest.TestCase):
    def test_gmail_payload_prefers_plain_text(self):
        import base64

        def enc(text):
            return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")
        payload = {"mimeType": "multipart/alternative", "parts": [
            {"mimeType": "text/plain", "body": {"data": enc("Plain body")}},
            {"mimeType": "text/html", "body": {"data": enc("<p>Html body</p>")}},
            {"mimeType": "application/pdf", "filename": "offer.pdf", "body": {"attachmentId": "x"}}]}
        self.assertEqual(parse.gmail_text(payload), "Plain body")
        html_only = {"mimeType": "text/html", "body": {"data": enc("<style>p{}</style><p>Hi&nbsp;Jane</p><br>Bye")}}
        self.assertEqual(parse.gmail_text(html_only), "Hi Jane\n\nBye")

    def test_rfc822_message(self):
        raw = (b"From: Acme <a@acme.com>\r\nSubject: Hello\r\nDate: Thu, 08 Oct 2026 10:00:00 -0500\r\n"
               b"Content-Type: text/plain; charset=utf-8\r\n\r\nThanks for applying.\r\n")
        message, text = parse.rfc822_text(raw)
        self.assertEqual((message["Subject"], text), ("Hello", "Thanks for applying."))
        self.assertEqual(parse.when(message["Date"]), datetime(2026, 10, 8, 15, 0, tzinfo=timezone.utc))

    def test_snippet_is_at_most_300_characters(self):
        mail = Mail("x", "a@b.c", "s", SENT, "word " * 200)
        self.assertLessEqual(len(mail.snippet), 300)


class ActionTest(MailCase):
    CASE = next(c for c in FIXTURES if c["id"] == "rej-greenhouse")

    def test_undo_puts_the_old_status_back_and_tracker_undo_leaves_email_changes_alone(self):
        row = self.run_one(self.mail(self.CASE))
        app_id = row["app_id"]
        self.assertEqual(self.app(app_id)["status"], "rejected")
        with self.assertRaisesRegex(ValueError, "nothing to undo"):
            trackapi.undo(self.conn, "jane", app_id)               # not the person's own edit
        update.undo(self.conn, "jane", row["id"], "Jane Doe")
        self.assertEqual(self.app(app_id)["status"], "applied")
        state = self.conn.execute("SELECT state FROM mail_messages WHERE id = ?", (row["id"],)).fetchone()[0]
        self.assertEqual(state, "undone")
        undone = self.conn.execute("SELECT count(*) FROM edit_history WHERE batch = ? AND undone_by IS NOT NULL",
                                   (row["batch"],)).fetchone()[0]
        self.assertEqual(undone, 1)

    def test_undo_refuses_when_the_person_changed_the_status_since(self):
        row = self.run_one(self.mail(self.CASE))
        tracker.set_status(self.conn, row["app_id"], "interview", source="ui")
        with self.assertRaisesRegex(ValueError, "changed since"):
            update.undo(self.conn, "jane", row["id"], "Jane Doe")
        self.assertEqual(self.app(row["app_id"])["status"], "interview")

    def test_only_the_status_changes(self):
        app_id = self.ids[("Acme Robotics", "Machine Learning Engineer")]
        before = self.app(app_id)
        self.run_one(self.mail(self.CASE))
        after = self.app(app_id)
        changed = {k for k in before if before[k] != after[k]}
        self.assertEqual(changed, {"status", "updated_at", "field_at"})

    def test_confirm_with_another_application_and_dismiss(self):
        untitled = next(c for c in FIXTURES if c["id"] == "rej-capone-untitled")
        row = self.run_one(self.mail(untitled))
        self.assertEqual(row["state"], "needs")
        other = self.ids[("Capital One", "Machine Learning Engineer, Card")]
        done = update.confirm(self.conn, "jane", row["id"], "Jane Doe", app_id=other)
        self.assertEqual((done["state"], self.app(other)["status"]), ("confirmed", "rejected"))
        self.assertEqual(self.app(self.ids[("Capital One", "Senior Data Scientist")])["status"], "applied")
        update.undo(self.conn, "jane", row["id"], "Jane Doe")
        self.assertEqual(self.app(other)["status"], "applied")
        alert = self.run_one(self.mail(next(c for c in FIXTURES if c["id"] == "rec-unknown"), "m2"))
        self.assertEqual(update.dismiss(self.conn, "jane", alert["id"])["state"], "dismissed")
        with self.assertRaisesRegex(ValueError, "already handled"):
            update.dismiss(self.conn, "jane", alert["id"])

    def test_emails_read_during_a_persons_request_keep_their_own_batch_and_author(self):
        cases = [next(c for c in FIXTURES if c["id"] == i) for i in ("rej-greenhouse", "rej-lever")]
        with history.action("Jane Doe"):                                  # the server wraps every POST like this
            poll.process(self.conn, "jane", [self.mail(c, f"b{i}") for i, c in enumerate(cases)],
                         SENT + timedelta(minutes=5), lambda p: None)
        rows = self.conn.execute("SELECT batch FROM mail_messages WHERE state = 'applied'").fetchall()
        self.assertEqual(len({r[0] for r in rows}), 2)
        who = {r[0] for r in self.conn.execute("SELECT who FROM edit_history WHERE field = 'status'")}
        self.assertEqual(who, {history.EMAIL})

    def test_a_status_set_by_hand_after_the_email_is_never_overwritten(self):
        app_id = self.ids[("Acme Robotics", "Machine Learning Engineer")]
        with mock.patch.object(db, "utcnow", return_value=SENT + timedelta(hours=1)):
            tracker.set_status(self.conn, app_id, "interview", source="ui")
        row = self.run_one(self.mail(self.CASE))
        self.assertEqual((row["state"], self.app(app_id)["status"]), ("recorded", "interview"))
        self.assertIn("by hand", row["reason"])


class ColdTest(MailCase):
    TODAY = date(2026, 10, 8)

    def test_old_silent_rows_go_cold_in_one_undoable_batch(self):
        old = self.add_app("Old Co", "Data Engineer", applied_on="2026-08-01")
        recent = self.add_app("New Co", "Data Engineer", applied_on="2026-09-30")
        touched = self.add_app("Touched Co", "Data Engineer", applied_on="2026-08-01")
        self.conn.execute("INSERT INTO application_events(app_id, at, from_status, to_status, source) "
                          "VALUES (?, '2026-10-01T00:00:00Z', 'screen', 'applied', 'ui')", (touched,))
        self.conn.commit()
        result = update.cold_sweep(self.conn, "jane", self.TODAY)
        self.assertEqual(self.app(old)["status"], "ghosted")
        self.assertEqual(self.app(recent)["status"], "applied")
        self.assertEqual(self.app(touched)["status"], "applied")
        due = {r["id"] for r in tracker.followups_due(self.conn, "jane")}
        self.assertNotIn(old, due)
        batches = {r[0] for r in self.conn.execute("SELECT DISTINCT batch FROM edit_history WHERE who = ?",
                                                   (history.COLD,))}
        self.assertEqual(batches, {result["batch"]})
        self.assertEqual(update.undo_cold(self.conn, "jane", result["batch"], "Jane Doe"), result["cold"])
        self.assertEqual(self.app(old)["status"], "applied")
        self.assertEqual(update.cold_sweep(self.conn, "jane", self.TODAY)["cold"], 0)   # an undone row waits again

    def test_a_reply_revives_a_cold_row(self):
        app_id = self.ids[("Stripe", "Data Engineer")]
        self.conn.execute("UPDATE applications SET status = 'ghosted' WHERE id = ?", (app_id,))
        self.conn.commit()
        case = next(c for c in FIXTURES if c["id"] == "int-goodtime")
        row = self.run_one(self.mail(case))
        self.assertEqual((row["state"], self.app(app_id)["status"]), ("applied", "interview"))

    def test_the_cold_days_are_a_setting(self):
        from job_hunt_agent import profile_settings
        self.assertEqual(update.cold_days("jane"), 30)
        profile_settings.set_cold_days("jane", 45)
        self.assertEqual(update.cold_days("jane"), 45)
        for bad in ("0", "400", "x"):
            with self.assertRaises(ValueError):
                profile_settings.set_cold_days("jane", bad)
        old = self.add_app("Forty Co", "Data Engineer", applied_on="2026-08-30")      # 39 days: not yet at 45
        self.assertNotIn(old, update.cold_due(self.conn, "jane", self.TODAY))


class ClaudeCapTest(MailCase):
    def test_at_most_50_calls_a_day(self):
        calls = []
        today = date(2026, 10, 8)
        answers = llm.classify_batch(self.conn, [("s", "t")] * 45, today,
                                     lambda p: calls.append(p) or {"results": [{"i": 0, "kind": "rejected"}]})
        self.assertEqual(len(calls), 3)                                 # 20 + 20 + 5
        self.assertEqual(answers[0], "rejected")
        self.conn.execute("UPDATE google_state SET value = '50' WHERE key = ?", (llm.COUNT_KEY.format(today),))
        self.conn.commit()
        self.assertEqual(llm.classify_batch(self.conn, [("s", "t")], today, lambda p: calls.append(p)), [None])
        self.assertEqual(len(calls), 3)


if __name__ == "__main__":
    unittest.main()
