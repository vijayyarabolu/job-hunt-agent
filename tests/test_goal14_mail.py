"""the nightly read. Python pre-filter (alerts, newsletters, codes skipped and logged), Claude reads the rest
in full in chunks of 15 on the person's own account with a minimal call, a status moves only when Claude is sure and
exactly one application matches (rules: cross-check), a usage limit leaves the rest for the next run, the morning
line, and still nothing but sender / subject / date / snippet / link / result / Claude's reason is stored."""
import json
import subprocess
from datetime import datetime, timedelta, timezone
from unittest import mock

from job_hunt_agent import api, config, db
from job_hunt_agent.google import state
from job_hunt_agent.mail import Mail, claude, match, nightly, panel, poll, prefilter
from tests.mail_fakes import claude_says, items, rules_claude
from tests.test_mail_rules import SENT, MailCase

AEP_BODY = ("Dear Jane,\n\nThank you for your interest in the R19850 Data Scientist position. After careful review, "
            "you have not been chosen to move forward in the selection process.\n\nAEP Talent Acquisition\n\n"
            "Unsubscribe from these emails | Privacy Policy")
SOFT_REJECT = ("Hi Jane,\n\nThanks for applying to the Machine Learning Engineer role at Acme Robotics. The team "
               "has reviewed your profile and the role will not be a fit for us right now. We will keep your "
               "resume on file.\n\nAcme Robotics")


def mail(i: int, sender: str, subject: str, body: str, minutes: int = 0) -> Mail:
    return Mail(f"g{i}", sender, subject, SENT + timedelta(minutes=minutes), body)


class OnSentDay(datetime):
    """The panel's "today" is the test emails' day, whatever day the suite runs."""

    @classmethod
    def now(cls, tz=None):
        return SENT.astimezone(tz)


class Goal14Case(MailCase):
    def status(self, company: str, title: str) -> str:
        return self.app(self.ids[(company, title)])["status"]

    def row(self, msg_id: str) -> dict:
        return dict(self.conn.execute("SELECT * FROM mail_messages WHERE msg_id = ?", (msg_id,)).fetchone())

    def read(self, mails, runner):
        return nightly.read_mail(self.conn, "jane", mails, SENT + timedelta(hours=1), runner)


class PrefilterTest(Goal14Case):
    def test_alerts_newsletters_promos_and_codes_are_skipped_and_logged(self):
        skipped = [mail(1, "LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>", "30+ new jobs for ML Engineer",
                        "Acme Robotics is hiring"),
                   mail(2, "Indeed <alert@indeed.com>", "Data Scientist jobs near you", "Apply now"),
                   mail(3, "Stripe <newsletter@stripe.com>", "Stripe Sessions recap", "Our new products"),
                   mail(4, "Workday <noreply@myworkday.com>", "Your verification code", "Code: 123456"),
                   mail(5, "Shop <orders@shop.example>", "Your order shipped", "Arrives Thursday")]
        fake = rules_claude()
        report = self.read(skipped, fake)
        self.assertEqual((report["skipped"], report["asked"], fake.prompts), (5, 0, []))
        log = (config.DATA / "logs" / "mail_skipped.log").read_text()
        for m in skipped:
            self.assertIn(m.subject, log)
        self.assertIn("one-time code", log)
        self.assertIn("job alert / newsletter / promotion", log)
        self.assertEqual({self.row(m.msg_id)["sender"] for m in skipped}, {None})       # nothing kept

    def test_a_job_board_is_skipped_only_for_its_alerts(self):
        apps = match.candidates(self.conn, "jane")
        relay = ("Indeed Apply <indeedapply@indeed.com>", "Your application has been submitted: Data Engineer")
        alert = ("Indeed <alert@indeed.com>", "New Data Engineer jobs for you")
        self.assertTrue(prefilter.decide(*relay, match.find(*relay, "", apps))[0])
        self.assertFalse(prefilter.decide(*alert, match.find(*alert, "", apps))[0])

    def test_ats_company_and_job_subjects_are_kept(self):
        apps = match.candidates(self.conn, "jane")
        for sender, subject in (("AEP <AEP@myworkday.com>", "AEP Thanks You!"),
                                ("Acme Robotics <hello@acmerobotics.com>", "A note from Acme Robotics"),
                                ("Dana <dana@unknown.example>", "Next steps on your application"),
                                ("Karat <scheduling@karat.io>", "Schedule your Karat interview")):
            found = match.find(sender, subject, "", apps)
            self.assertTrue(prefilter.decide(sender, subject, found)[0], subject)


class ClaudeCallTest(Goal14Case):
    def test_full_trimmed_bodies_with_the_candidate_applications_in_chunks_of_15(self):
        mails = [mail(i, "Acme Robotics <no-reply@us.greenhouse-mail.io>", f"Acme Robotics update {i}",
                      SOFT_REJECT + "\n\nOn Mon, Oct 6, 2026 at 9:00 AM Jane wrote:\n> quoted text", i)
                 for i in range(20)]
        fake = rules_claude()
        self.read(mails, fake)
        self.assertEqual([len(items(p)) for p in fake.prompts], [15, 5])
        sent = items(fake.prompts[0])[0]
        self.assertEqual(sent["text"], SOFT_REJECT.strip())                    # whole body, quoted thread trimmed
        self.assertEqual(sent["applications"], [{"id": self.ids[("Acme Robotics", "Machine Learning Engineer")],
                                                 "company": "Acme Robotics", "title": "Machine Learning Engineer",
                                                 "req_id": None, "applied_on": "2026-09-20"}])
        self.assertEqual((sent["from"], sent["date"]), (mails[0].sender, "2026-10-08"))
        self.assertLessEqual(len(claude.trim("x" * 20000)), claude.BODY_MAX)
        self.assertNotIn("Unsubscribe", claude.trim(AEP_BODY))

    def test_a_minimal_call_on_the_persons_own_account(self):
        answer = {"structured_output": {"results": []}, "usage": {"input_tokens": 600, "output_tokens": 9},
                  "total_cost_usd": 0.002}
        done = subprocess.CompletedProcess([], 0, json.dumps(answer), "")
        with mock.patch.object(subprocess, "run", return_value=done) as run, \
                mock.patch.object(config, "settings", return_value={"claude_config_dirs": {"jordan": "~/.claude-jordan"}}):
            out, kind, usage = claude.run_claude("prompt", "jordan")
        cmd, env = run.call_args.args[0], run.call_args.kwargs["env"]
        self.assertEqual((out, kind, usage["input_tokens"]), ({"results": []}, "ok", 600))
        self.assertEqual(cmd[cmd.index("--model") + 1], "claude-sonnet-5-5")
        self.assertEqual(cmd[cmd.index("--tools") + 1], "")
        self.assertEqual(cmd[cmd.index("--setting-sources") + 1], "")
        self.assertIn("--system-prompt", cmd)                                  # not Claude Code's long default
        self.assertIn("--disable-slash-commands", cmd)
        self.assertTrue(env["CLAUDE_CONFIG_DIR"].endswith("/.claude-jordan"))
        self.assertNotIn("CLAUDECODE", env)

    def test_a_limit_or_a_lapsed_sign_in_is_told_apart(self):
        for text, kind in (("You've hit your usage limit · resets 1am", "limit"), ("Not logged in · Please run /login",
                                                                                   "login"), ("boom", "error")):
            done = subprocess.CompletedProcess([], 1, json.dumps({"is_error": True, "result": text}), "")
            with mock.patch.object(subprocess, "run", return_value=done):
                self.assertEqual(claude.run_claude("p", "jane")[:2], (None, kind))


class ApplyTest(Goal14Case):
    def test_claude_catches_what_the_keywords_miss_when_sure_and_single(self):
        soft = mail(1, "Acme Robotics <no-reply@us.greenhouse-mail.io>", "Your application to Acme Robotics",
                    SOFT_REJECT)
        report = self.read([soft], claude_says({"Acme": {"kind": "rejected", "reason": 'Said "will not be a fit"'}}))
        self.assertEqual(report["outcomes"], [("applied", "rejected")])
        self.assertEqual(self.status("Acme Robotics", "Machine Learning Engineer"), "rejected")
        row = self.row("g1")
        self.assertEqual((row["method"], row["confidence"], row["rule_kind"]), ("claude", "high", "received"))
        self.assertEqual(row["claude_reason"], 'Said "will not be a fit"')

    def test_not_sure_or_several_applications_wait_for_the_person(self):
        unsure = mail(1, "Acme Robotics <no-reply@us.greenhouse-mail.io>", "Acme Robotics update", SOFT_REJECT)
        capone = mail(2, "Capital One <capitalone@myworkday.com>", "An update from Capital One",
                      "Unfortunately we will not be moving forward with your application.", 1)
        fake = claude_says({"Acme": {"kind": "rejected", "confidence": "medium"},
                            "Capital One": {"kind": "rejected", "application": None}})
        self.read([unsure, capone], fake)
        self.assertEqual((self.row("g1")["state"], self.row("g1")["reason"]), ("needs", "Claude is not sure (medium)"))
        self.assertEqual((self.row("g2")["state"], self.row("g2")["reason"]), ("needs", "several applications match"))
        self.assertEqual(self.status("Acme Robotics", "Machine Learning Engineer"), "applied")

    def test_a_rules_disagreement_on_a_status_waits_and_shows_both_answers(self):
        flat = mail(1, "Ramp <no-reply@hire.lever.co>", "Ramp - Applied AI Engineer",
                    "Unfortunately, we have decided to move forward with other candidates.")
        self.read([flat], claude_says({"Ramp": {"kind": "received", "reason": "Just a thank-you"}}))
        row = self.row("g1")
        self.assertEqual((row["state"], row["reason"]), ("needs", "the rules say rejected, Claude says received"))
        self.assertEqual(self.status("Ramp", "Applied AI Engineer"), "applied")
        html = panel.panel(self.conn, "jane")
        self.assertIn("Claude: <b>Received</b> (high): Just a thank-you", html)
        self.assertIn("Rules: Rejected", html)

    def test_the_aep_rejection_lands_on_the_one_application_by_tenant_and_req_id(self):
        db.sync_boards(self.conn, [{"board": "workday:aep/wd1/AEPCareerSite", "ats": "workday", "key": "aep",
                                    "company": "AEP"}])
        with db.tx(self.conn):
            self.conn.execute("INSERT INTO jobs(uid, board, ext_id, company, title, precision, content_hash, "
                              "repost_key, first_seen, last_seen, req_id) VALUES ('wd:aep1', "
                              "'workday:aep/wd1/AEPCareerSite', 'R19850', 'Ohio Power Company', 'Data Scientist', "
                              "'unknown', 'h', 'k', '2026-09-01T00:00:00Z', '2026-09-01T00:00:00Z', 'R19850')")
            app = self.conn.execute("INSERT INTO applications(profile, job_uid, company, role_title, status, "
                                    "applied_on, source) VALUES ('jane', 'wd:aep1', 'Ohio Power Company', "
                                    "'Data Scientist', 'applied', '2026-10-08', 'feed')").lastrowid
        fake = claude_says({"AEP": {"kind": "rejected"}})
        self.read([mail(3, "AEP <AEP@myworkday.com>", "AEP Thanks You!", AEP_BODY)], fake)
        self.assertEqual([a["id"] for a in items(fake.prompts[0])[0]["applications"]], [app])
        self.assertEqual(self.app(app)["status"], "rejected")

    def test_a_reply_revives_a_cold_row_only_when_claude_is_sure(self):
        app = self.ids[("Globex", "Data Engineer II")]
        with db.tx(self.conn):
            self.conn.execute("UPDATE applications SET status = 'ghosted' WHERE id = ?", (app,))
        note = "Thank you for applying. We have received your application for Data Engineer II."
        self.read([mail(1, "Globex Careers <careers@globex.icims.com>", "Globex: application received", note)],
                  claude_says({"Globex": {"kind": "received", "confidence": "medium"}}))
        self.assertEqual(self.app(app)["status"], "ghosted")
        self.read([mail(2, "Globex Careers <careers@globex.icims.com>", "Globex: application received", note, 1)],
                  claude_says({"Globex": {"kind": "received"}}))
        self.assertEqual(self.app(app)["status"], "applied")

    def test_a_confirmation_with_an_if_selected_sentence_stays_applied(self):
        dwt = mail(6, "Pinnacle Bank <notification@jobvite.com>", "Your application for Machine Learning Engineer",
                   "We have received your application and are currently reviewing it. If you are selected to move "
                   "forward, a member of our team will contact you to schedule an interview.")
        self.read([dwt], claude_says({"Machine": {"kind": "received"}}))
        self.assertEqual((self.row("g6")["state"], self.row("g6")["kind"]), ("recorded", "received"))
        self.assertEqual(self.status("Pinnacle Bank", "Machine Learning Engineer"), "applied")


class RunTest(Goal14Case):
    class Reader:
        kind = "gmail"

        def __init__(self, mails):
            self.mails = mails

        def since(self, after, known=frozenset()):
            return sorted((m for m in self.mails if m.sent_at > after and m.msg_id not in known),
                          key=lambda m: m.sent_at)

    def test_a_usage_limit_leaves_the_rest_for_the_next_run_and_never_falls_back_to_keywords(self):
        mails = [mail(i, "Acme Robotics <no-reply@us.greenhouse-mail.io>", f"Acme Robotics update {i}",
                      "Unfortunately, we have decided to move forward with other candidates.", i) for i in range(20)]
        reader = self.Reader(mails)
        limited = rules_claude(stop_after=1)
        first = nightly.run_person(self.conn, "jane", SENT + timedelta(hours=1), reader, limited, notify_person=False)
        self.assertEqual((first["asked"], first["waiting"]), (20, 5))
        self.assertIn("5 wait for the next run (Claude usage limit)", first["line"])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM mail_messages").fetchone()[0], 15)
        self.assertEqual(state.get(self.conn, poll.KEY.format("jane", "error")), "Claude usage limit")
        second = nightly.run_person(self.conn, "jane", SENT + timedelta(days=1), reader, rules_claude(),
                                    notify_person=False)
        self.assertEqual((second["read"], second["waiting"]), (5, 0))
        self.assertEqual(self.conn.execute("SELECT count(*) FROM mail_messages").fetchone()[0], 20)
        self.assertIsNone(state.get(self.conn, poll.KEY.format("jane", "error")))

    def test_the_morning_line_reaches_the_header_and_the_notification(self):
        mails = [mail(1, "Ramp <no-reply@hire.lever.co>", "Ramp - Applied AI Engineer",
                      "Unfortunately, we have decided to move forward with other candidates."),
                 mail(2, "Stripe Recruiting <recruiting@stripe.com>", "Stripe: schedule your interviews",
                      "We'd like to invite you to interview. Pick times here: https://app.goodtime.io/x", 1),
                 mail(3, "Capital One <capitalone@myworkday.com>", "An update from Capital One",
                      "Unfortunately we will not be moving forward with your application.", 2)]
        now = SENT + timedelta(hours=1)
        with mock.patch.object(nightly.notify, "send") as send:
            done = nightly.run_person(self.conn, "jane", now, self.Reader(mails), rules_claude())
        line = "Overnight email: 1 rejected, 1 interview invite, 1 needs you"
        self.assertEqual(done["line"], line)
        send.assert_called_once_with("job-hunt-agent", line, "jane")
        with mock.patch.object(nightly.db, "utcnow", return_value=now):
            self.assertEqual(nightly.overnight_line(self.conn, "jane"), line)
        with mock.patch("job_hunt_agent.google.runner.banner", return_value={"message": None}), \
                mock.patch.object(api.gsync, "sheet_url", return_value=None), \
                mock.patch.object(nightly.db, "utcnow", return_value=now):
            self.assertEqual(api.google_summary(self.conn, "jane")["message"], line)

    def test_one_run_per_person_at_a_time(self):
        with db.tx(self.conn):
            state.put(self.conn, poll.KEY.format("jane", "running"), db.iso(SENT))
        busy = nightly.run_person(self.conn, "jane", SENT + timedelta(minutes=5), self.Reader([]), rules_claude())
        self.assertEqual(busy, {"skipped": "a check is already running"})
        stale = nightly.run_person(self.conn, "jane", SENT + timedelta(minutes=20), self.Reader([]), rules_claude(),
                                   notify_person=False)
        self.assertEqual(stale["read"], 0)                                    # a crashed run's mark expires
        self.assertIsNone(state.get(self.conn, poll.KEY.format("jane", "running")))

    def test_nothing_new_is_said_quietly(self):
        with mock.patch.object(nightly.notify, "send") as send:
            done = nightly.run_person(self.conn, "jane", SENT, self.Reader([]), rules_claude())
        self.assertEqual(done["line"], "Overnight email: no updates")
        send.assert_not_called()

    def test_the_body_never_reaches_the_db(self):
        secret = "Your interview code is KITE-7781 and the salary band is 180-210k."
        body = f"We'd like to invite you to an onsite interview. {'filler ' * 80}{secret}"
        nightly.run_person(self.conn, "jane", SENT + timedelta(hours=1),
                           self.Reader([mail(1, "Stripe Recruiting <recruiting@stripe.com>", "Stripe: onsite", body)]),
                           rules_claude(), notify_person=False)
        dump = json.dumps([dict(r) for r in self.conn.execute("SELECT * FROM mail_messages")])
        self.assertNotIn("KITE-7781", dump)
        self.assertLessEqual(len(self.row("g1")["snippet"]), 300)


class MatchToTest(Goal14Case):
    def test_an_ambiguous_email_is_matched_in_one_click(self):
        capone = mail(1, "Capital One <capitalone@myworkday.com>", "Thanks for applying to Capital One",
                      "Thank you for applying. We have received your application.")
        self.read([capone], claude_says({"Capital": {"kind": "received", "application": None}}))
        row = self.row("g1")
        self.assertEqual((row["state"], row["app_id"]), ("recorded", None))
        with mock.patch.object(panel, "datetime", OnSentDay):
            html = panel.panel(self.conn, "jane")
        self.assertIn('placeholder="Match to…"', html)
        self.assertIn('data-mail="match"', html)
        target = self.ids[("Capital One", "Senior Data Scientist")]
        got = panel.act(self.conn, "jane", {"act": "match", "id": str(row["id"]),
                                            "app": f"Capital One · Senior Data Scientist #{target}"}, "Jane")
        self.assertEqual(got["msg"], "Matched")
        self.assertEqual(self.row("g1")["app_id"], target)
        self.assertEqual(self.app(target)["status"], "applied")              # matching never changes a status
