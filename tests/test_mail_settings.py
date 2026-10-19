"""GOAL12 §1 (10:15 update): Settings -> "Job-application email": the typed address is the one Google signs in (or the
token is dropped), a non-Google address is pointed to IMAP, Disconnect / interval / Cold days, IMAP from the app with
the password in the Keychain only, a non-Google IMAP server, and every control with its hover label."""
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from unittest import mock

from job_hunt_agent import db, profile_settings
from job_hunt_agent.google import auth, state
from job_hunt_agent.mail import GMAIL_SCOPE, Mail, MailError, address as addr, imap, nightly, panel, poll, signin, update
from tests.google_case import GoogleCase
from tests.mail_fakes import rules_claude
from tests.test_mail_gmail import FakeBox, ServerCase, redirect_target

NOW = datetime(2026, 10, 8, 18, 0, tzinfo=timezone.utc)
INTERACTIVE = ("a", "button", "select", "input", "summary")


class Untitled(HTMLParser):
    """Collects every interactive element (hidden inputs aside) that has no title."""

    def __init__(self):
        super().__init__()
        self.missing: list[str] = []

    def handle_starttag(self, tag, attrs):
        found = dict(attrs)
        if tag in INTERACTIVE and found.get("type") != "hidden" and not found.get("title"):
            self.missing.append(self.get_starttag_text())


def untitled(html: str) -> list[str]:
    parser = Untitled()
    parser.feed(html)
    return parser.missing


class AddressTest(GoogleCase):
    def test_gmail_ignores_dots_tags_and_googlemail(self):
        self.assertTrue(addr.same_mailbox("Jane.Doe+jobs@GMail.com", "janedoe@googlemail.com"))
        self.assertFalse(addr.same_mailbox("jane@gmail.com", "jane@example.com"))
        self.assertFalse(addr.same_mailbox(None, "jane@gmail.com"))

    def test_provider_from_the_mx_records(self):
        self.assertEqual(addr.provider("a@gmail.com", lambda h: []), "gmail")
        self.assertEqual(addr.provider("a@corp.example", lambda h: ["aspmx.l.google.com."]), "google")
        self.assertEqual(addr.provider("a@corp.example", lambda h: ["mx.corp.example."]), "other")
        self.assertEqual(addr.provider("a@corp.example", lambda h: []), "unknown")
        self.assertEqual(addr.imap_host("a@outlook.com"), "outlook.office365.com")
        self.assertEqual(addr.imap_host("a@corp.example", "google"), imap.GOOGLE_HOST)
        self.assertEqual(addr.imap_host("a@corp.example"), "imap.corp.example")

    def test_not_an_address_is_refused(self):
        for bad in ("", "jane", "jane@", "<jane@gmail.com>"):
            with self.assertRaisesRegex(ValueError, "not an email address"):
                addr.valid(bad)


class AddressSigninTest(GoogleCase):
    def test_the_typed_address_is_the_login_hint_and_is_kept(self):
        answer = redirect_target(signin.begin(self.conn, "jane", "Jane.Doe@gmail.com"))
        query = [q for m, p, q in self.fake.requests if p == "/o/oauth2/v2/auth"][-1]
        self.assertEqual(query["login_hint"], "Jane.Doe@gmail.com")
        signin.finish(self.conn, answer)
        self.assertEqual(signin.address_of(self.conn, "jane"), "Jane.Doe@gmail.com")
        self.assertEqual(signin.status(self.conn, "jane")["account"], "jane.doe@gmail.com")

    def test_signing_in_as_another_account_keeps_nothing(self):
        answer = redirect_target(signin.begin(self.conn, "jane", "someone.else@gmail.com"))
        with self.assertRaisesRegex(ValueError, "you signed in as jane.doe@gmail.com, not someone.else@gmail.com"):
            signin.finish(self.conn, answer)
        self.assertFalse(signin.token_path("jane").exists())
        self.assertFalse(signin.status(self.conn, "jane")["connected"])
        self.assertIsNone(signin.address_of(self.conn, "jane"))

    def test_a_non_google_address_is_pointed_to_imap(self):
        with mock.patch.object(addr, "provider", return_value="other"):
            with self.assertRaisesRegex(ValueError, "not a Gmail or Google Workspace address.*IMAP"):
                signin.begin(self.conn, "jane", "jane@corp.example")
        self.assertFalse([p for m, p, q in self.fake.requests if p == "/o/oauth2/v2/auth"])

    def test_disconnect_drops_the_token_and_reading_stops(self):
        auth.save(self.fake.grant(scope=GMAIL_SCOPE), path=signin.token_path("jane"))
        self.assertIsNotNone(poll.reader_for(self.conn, "jane"))
        signin.disconnect(self.conn, "jane")
        self.assertFalse(signin.token_path("jane").exists())
        self.assertIsNone(poll.reader_for(self.conn, "jane"))


class SettingsViewTest(ServerCase):
    def test_both_views_label_every_control(self):
        auth.save(self.fake.grant(scope=GMAIL_SCOPE), path=signin.token_path("jane"))
        mail = Mail("t1", "Acme <jobs@acme.example>", "Next steps", NOW - timedelta(hours=1), "Acme Robotics")
        update.record(self.conn, "jane", mail, "screen", "rule", None, "needs", app_id=self.app_id,
                      to_status="screen", reason="two applications", now=NOW)
        for view in ("panel", "settings"):
            self.assertEqual(untitled(self.act(act="view", view=view)["html"]), [], view)
        settings = self.act(act="view", view="settings")["html"]
        for words in ('action="/google/connect"', 'name="address"', 'data-mail="disconnect"', 'data-mail="check"',
                      'data-mail="imap"', 'data-mail="daily-at"', 'data-mail="cold-days"', "daily at 00:15"):
            self.assertIn(words, settings)

    def test_a_refused_identity_check_says_why_instead_of_failing(self):
        answer = redirect_target(signin.begin(self.conn, "jane", "jane.doe@gmail.com"))
        self.fake.fail("GET", r"/gmail/v1/users/me/profile$", 403, reason="accessNotConfigured")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.act(act="finish", value=answer, view="settings")
        body = caught.exception.read().decode()
        caught.exception.close()
        self.assertEqual(caught.exception.code, 400)                         # a message, not "internal error"
        self.assertIn("could not read which Google account signed in: 403", body)
        self.assertIn("Press Connect again", body)
        self.assertFalse(signin.status(self.conn, "jane")["connected"])

    def test_the_daily_time_is_saved_per_person(self):
        self.assertEqual(self.act(act="daily-at", value="06:30", view="settings")["msg"], "Saved")
        self.assertEqual(profile_settings.get("jane")[profile_settings.MAIL_AT_KEY], "06:30")
        self.assertIn('value="06:30"', self.act(act="view", view="settings")["html"])
        for bad in ("25:00", "6:30", "", "noon"):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.act(act="daily-at", value=bad, view="settings")
            caught.exception.close()
        self.assertEqual(profile_settings.get("jane")[profile_settings.MAIL_AT_KEY], "06:30")
        self.assertIn('mail_daily_at: "06:30"', (self.root / "profiles" / "jane" / "profile.yaml").read_text())

    def test_the_nightly_run_waits_for_the_persons_time_and_catches_up_after_sleep(self):
        with mock.patch.object(profile_settings, "get", return_value={profile_settings.MAIL_AT_KEY: "00:15"}):
            local = datetime(2026, 10, 9, 0, 10).astimezone()                  # 00:10: not yet
            self.assertTrue(nightly.due(self.conn, "jane", local))              # never ran: due
            with db.tx(self.conn):
                state.put(self.conn, poll.KEY.format("jane", "nightly_at"), db.iso(local - timedelta(hours=1)))
            self.assertFalse(nightly.due(self.conn, "jane", local))             # ran after yesterday's 00:15
            self.assertTrue(nightly.due(self.conn, "jane", local + timedelta(minutes=10)))
            self.assertTrue(nightly.due(self.conn, "jane", local + timedelta(hours=8)))   # asleep at 00:15: on wake
            with db.tx(self.conn):
                state.put(self.conn, poll.KEY.format("jane", "nightly_at"), db.iso(local + timedelta(minutes=6)))
            self.assertFalse(nightly.due(self.conn, "jane", local + timedelta(hours=8)))


    def test_connect_with_an_address_and_a_non_gmail_address_says_so(self):
        target = redirect_target(self.connect_url("jane.doe@gmail.com"))
        self.assertEqual(urllib.parse.parse_qs(urllib.parse.urlsplit(target).query)["login_hint"],
                         ["jane.doe@gmail.com"])
        with mock.patch.object(addr, "provider", return_value="other"):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(self.connect_url("j@corp.example"), timeout=10)
        body = caught.exception.read().decode()
        caught.exception.close()
        self.assertIn("not a Gmail or Google Workspace address", body)

    def test_imap_from_the_app_keeps_the_password_in_the_keychain_only(self):
        with mock.patch.object(imap, "store_password", return_value=True) as store, \
                mock.patch.object(imap.ImapReader, "check") as check, \
                mock.patch.object(addr, "provider", return_value="other"):
            answer = self.act(act="imap", value="jane@corp.example", secret="abcd-efgh", host="", view="settings")
        store.assert_called_once_with("jane@corp.example", "abcd-efgh")
        check.assert_called_once()
        self.assertIn("over IMAP", answer["msg"])
        self.assertEqual(state.get(self.conn, imap.HOST_KEY.format("jane")), "imap.corp.example")
        dump = "\n".join(str(tuple(r)) for r in self.conn.execute("SELECT * FROM google_state"))
        self.assertNotIn("abcd-efgh", dump)                                   # never in the database
        self.assertIn("IMAP jane@corp.example", answer["html"])
        with mock.patch.object(imap, "forget") as forget:
            self.assertIn("Disconnected", self.act(act="disconnect", view="settings")["msg"])
        forget.assert_called_once_with("jane@corp.example")
        self.assertIsNone(poll.reader_for(self.conn, "jane"))

    def test_a_refused_imap_password_is_removed_again(self):
        with mock.patch.object(imap, "store_password", return_value=True), \
                mock.patch.object(imap.ImapReader, "check", side_effect=MailError("imap.corp.example refused")), \
                mock.patch.object(imap, "forget") as forget, mock.patch.object(addr, "provider", return_value="other"):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.act(act="imap", value="jane@corp.example", secret="wrong-pass", view="settings")
        caught.exception.close()
        forget.assert_called_once_with("jane@corp.example")
        self.assertIsNone(state.get(self.conn, imap.ADDRESS_KEY.format("jane")))


class PlainBox(FakeBox):
    """A non-Google IMAP server: no X-GM-* extensions."""
    RAW = (b"From: Acme Robotics <no-reply@greenhouse.io>\r\nSubject: Update\r\nMessage-ID: <abc@acme>\r\n"
           b"Date: Thu, 08 Oct 2026 10:00:00 -0500\r\nContent-Type: text/plain\r\n\r\nWe will not be moving forward.\r\n")

    def uid(self, command, *args):
        self.calls.append(("uid", command, *args))
        if command == "SEARCH":
            return "OK", [b"3"]
        return "OK", [(b"3 (BODY[] {170}", self.RAW), b")"]


class PlainImapTest(GoogleCase):
    def test_another_server_reads_the_inbox_since_the_day_without_a_link(self):
        with mock.patch.object(imap, "password", return_value="app-pass"):
            mails = imap.ImapReader("jane@corp.example", "imap.corp.example", connect=PlainBox).since(poll.start())
        calls = PlainBox.last.calls
        self.assertIn(("select", "INBOX", True), calls)
        self.assertIn(("uid", "SEARCH", "SINCE", "08-Oct-2026"), calls)
        self.assertFalse([c for c in calls if any("X-GM" in str(x) for x in c)])
        self.assertEqual(len(mails), 1)
        self.assertTrue(mails[0].msg_id.startswith("imap-"))
        self.assertFalse(mails[0].gmail)
        self.assertIsNone(mails[0].link)

    def test_the_panel_shows_no_link_for_such_mail(self):
        self.assertEqual(panel._link({"subject": "Update", "link": None}), "<b>Update</b>")


class EmailNoteTest(GoogleCase):
    def test_a_status_set_by_email_shows_the_email_on_the_row(self):
        from job_hunt_agent import trackapi
        from tests.test_mail_gmail import REJECT, add_acme
        app_id = add_acme(self.conn)
        sender, subject, body = REJECT
        mail = Mail("n1", sender, subject, datetime(2026, 10, 8, 15, 0, tzinfo=timezone.utc), body)
        self.assertEqual(poll.process(self.conn, "jane", [mail], NOW, lambda p: None), {"applied": 1})
        event = trackapi.get(self.conn, "jane", app_id)["events"][-1]
        self.assertEqual((event["to_status"], event["source"]), ("rejected", "email"))
        self.assertEqual(event["note"], "Email 2026-10-08: Your application to Acme Robotics")
        self.assertEqual(event["link"], "https://mail.google.com/mail/u/0/#all/n1")
        self.assertNotIn("snapshot", event)


class BacklogTest(GoogleCase):
    """More new mail than one poll reads: the OLDEST go first, the next poll reads the rest (nothing skipped)."""

    def test_gmail_reads_the_oldest_first_and_the_next_poll_the_rest(self):
        from job_hunt_agent.mail import gmail
        from tests.test_mail_gmail import gmail_message
        auth.save(self.fake.grant(scope=GMAIL_SCOPE), path=signin.token_path("jane"))
        self.fake.mails = {f"b{i}": gmail_message(f"b{i}", "Shop <o@shop.example>", f"Order {i}", "Shipped.",
                                                  datetime(2026, 10, 8, 14, i, tzinfo=timezone.utc))
                           for i in range(5)}
        with mock.patch.object(gmail, "MAX_MESSAGES", 3), mock.patch.object(gmail, "PAGE", 2):
            first = poll.poll_profile(self.conn, "jane", NOW, runner=rules_claude())
            second = poll.poll_profile(self.conn, "jane", NOW + timedelta(minutes=30), runner=rules_claude())
        self.assertEqual((first["read"], second["read"]), (3, 2))
        read = {r[0] for r in self.conn.execute("SELECT msg_id FROM mail_messages")}
        self.assertEqual(read, {f"b{i}" for i in range(5)})

    def test_imap_takes_the_oldest_new_ones_first_skipping_ones_read_before(self):
        class Many(PlainBox):
            def uid(self, command, *args):
                self.calls.append(("uid", command, *args))
                if command == "SEARCH":
                    return "OK", [b"3 4 5 6"]
                raw = PlainBox.RAW.replace(b"<abc@acme>", b"<m" + args[0] + b"@acme>")
                return "OK", [(b"x (BODY[] {170}", raw), b")"]
        reader = imap.ImapReader("jane@corp.example", "imap.corp.example", connect=Many)
        with mock.patch.object(imap, "password", return_value="app-pass"), mock.patch.object(imap, "MAX_MESSAGES", 2):
            everything = reader.since(poll.start())
            first_id = everything[0].msg_id
            mails = reader.since(poll.start(), known={first_id})
        fetched = [c[2] for c in Many.last.calls if c[:2] == ("uid", "FETCH")]
        self.assertEqual(fetched, [b"3", b"4", b"5"])       # 3 was read before: skipped on the way, 4 and 5 are new
        self.assertEqual(len(mails), 2)
        self.assertNotIn(first_id, {m.msg_id for m in mails})


class ColdNoteTest(GoogleCase):
    def test_the_cold_rule_is_named_on_the_row_not_email(self):
        from datetime import date
        from job_hunt_agent import trackapi
        from tests.test_mail_gmail import add_acme
        app_id = add_acme(self.conn)
        self.assertEqual(update.cold_sweep(self.conn, "jane", date(2026, 11, 15))["cold"], 1)
        event = trackapi.get(self.conn, "jane", app_id)["events"][-1]
        self.assertEqual((event["to_status"], event["note"]), ("ghosted", "Cold rule: no reply in time"))
