"""GOAL12 against the fake Google: each person's own read-only Gmail sign-in (on the Mac, or a pasted answer from a
phone), polling only new mail since the start day, nothing ever written to Gmail, the panel over HTTP, and the new
status reaching the person's Sheet (and leaving it again on Undo)."""
import base64
import json
import os
import stat
import threading
import urllib.error
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from unittest import mock

from job_hunt_agent import db, google_page, server
from job_hunt_agent.google import accounts, auth
from job_hunt_agent.mail import GMAIL_SCOPE, Mail, nightly, poll, signin, update
from tests.google_case import GoogleCase
from tests.mail_fakes import rules_claude
from tests.test_google_auth import browser
from tests.test_gsync import SyncCase

NOW = datetime(2026, 10, 8, 18, 0, tzinfo=timezone.utc)
GMAIL_ADDRESS = "jane.doe@gmail.com"                 # the fake Gmail account (fake_google.gmail_account)
REJECT = ("Acme Robotics <no-reply@us.greenhouse-mail.io>", "Your application to Acme Robotics",
          "Hi Jane,\n\nThank you for applying to Acme Robotics. Unfortunately, we have decided to move forward with "
          "other candidates for the Machine Learning Engineer role.\n\nAcme Robotics")


def gmail_message(msg_id: str, sender: str, subject: str, body: str, when: datetime) -> dict:
    data = base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
    return {"id": msg_id, "threadId": msg_id, "internalDate": str(int(when.timestamp() * 1000)),
            "payload": {"mimeType": "text/plain", "headers": [{"name": "From", "value": sender},
                                                               {"name": "Subject", "value": subject}],
                        "body": {"data": data}}}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def redirect_target(url: str) -> str:
    """Where a URL redirects to, without following it (a phone cannot load 127.0.0.1: its address is pasted)."""
    try:
        urllib.request.build_opener(_NoRedirect).open(url, timeout=10)
    except urllib.error.HTTPError as err:
        err.close()
        return err.headers["Location"]
    raise AssertionError(f"{url} did not redirect")


def status_of(conn, app_id: int) -> str:
    return conn.execute("SELECT status FROM applications WHERE id = ?", (app_id,)).fetchone()[0]


def add_acme(conn) -> int:
    with db.tx(conn):
        cur = conn.execute("INSERT INTO applications(profile, company, role_title, status, applied_on, source) "
                           "VALUES ('jane', 'Acme Robotics', 'Machine Learning Engineer', 'applied', '2026-10-01', "
                           "'feed')")
    return cur.lastrowid


class SigninTest(GoogleCase):
    def test_login_on_the_mac_asks_gmail_readonly_alone_and_keeps_its_own_token(self):
        sheets_token = auth.token_path().read_text()
        signin.login(self.conn, "jane", GMAIL_ADDRESS, open_browser=browser)
        query = [q for m, p, q in self.fake.requests if p == "/o/oauth2/v2/auth"][-1]
        self.assertEqual(query["scope"], GMAIL_SCOPE)
        path = signin.token_path("jane")
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(json.loads(path.read_text())["scope"], GMAIL_SCOPE)
        self.assertEqual(auth.token_path().read_text(), sheets_token)          # the Sheets sign-in is untouched
        self.assertEqual(signin.status(self.conn, "jane"), {"connected": True, "lapsed": False,
                                                            "account": "jane.doe@gmail.com"})

    def test_a_pasted_answer_finishes_the_sign_in_once(self):
        answer = redirect_target(signin.begin(self.conn, "jane", GMAIL_ADDRESS))
        self.assertTrue(answer.startswith(f"http://127.0.0.1:{signin.redirect_port}/?"))  # GOAL11's
        self.assertEqual(signin.finish(self.conn, f"  {answer}  "), "jane")
        self.assertTrue(signin.status(self.conn, "jane")["connected"])
        with self.assertRaisesRegex(ValueError, "already used"):
            signin.finish(self.conn, answer)

    def test_an_old_or_forged_answer_is_refused(self):
        answer = redirect_target(signin.begin(self.conn, "jane", GMAIL_ADDRESS))
        with mock.patch.object(accounts.time, "time", return_value=time.time() + 16 * 60):
            with self.assertRaisesRegex(ValueError, "older than 15 minutes"):
                signin.finish(self.conn, answer)
        with self.assertRaisesRegex(ValueError, "unknown"):
            signin.finish(self.conn, "http://127.0.0.1:8765/google/callback?code=x&state=forged")
        self.assertFalse(signin.token_path("jane").exists())


class PollTest(GoogleCase):
    def setUp(self):
        super().setUp()
        auth.save(self.fake.grant(scope=GMAIL_SCOPE), path=signin.token_path("jane"))
        self.app_id = add_acme(self.conn)
        self.fake.mails = {
            "m0": gmail_message("m0", *REJECT, poll.start() - timedelta(hours=2)),        # before the start day
            "m1": gmail_message("m1", *REJECT, datetime(2026, 10, 8, 15, 0, tzinfo=timezone.utc)),
            "m2": gmail_message("m2", "Shop <orders@shop.example>", "Your order shipped", "Arrives Thursday.",
                                datetime(2026, 10, 8, 16, 0, tzinfo=timezone.utc))}

    def test_only_new_mail_since_the_start_day_and_nothing_is_written_to_gmail(self):
        first = poll.poll_profile(self.conn, "jane", NOW, runner=rules_claude())
        self.assertEqual((first["read"], first["states"].count("applied"), first["states"].count("ignored")), (2, 1, 1))
        self.assertEqual(status_of(self.conn, self.app_id), "rejected")
        self.assertIsNone(self.conn.execute("SELECT 1 FROM mail_messages WHERE msg_id = 'm0'").fetchone())
        again = poll.poll_profile(self.conn, "jane", NOW + timedelta(minutes=30), runner=rules_claude())
        self.assertEqual(again["read"], 0)
        gmail_calls = [(m, p) for m, p, _ in self.fake.requests if p.startswith("/gmail/")]
        self.assertTrue(gmail_calls)
        self.assertEqual({m for m, _ in gmail_calls}, {"GET"})                    # read-only, every call
        self.assertTrue(all(p.startswith(("/gmail/v1/users/me/messages", "/gmail/v1/users/me/profile"))
                            for _, p in gmail_calls))

    def test_redo_reads_again_what_changed_nothing_and_keeps_what_moved_a_status(self):
        poll.poll_profile(self.conn, "jane", NOW, runner=rules_claude())                  # m1 applied, m2 ignored
        self.assertEqual(poll.redo(self.conn, "jane"), 1)
        self.assertEqual([r[0] for r in self.conn.execute("SELECT msg_id FROM mail_messages")], ["m1"])
        again = poll.poll_profile(self.conn, "jane", NOW + timedelta(minutes=1), runner=rules_claude())
        self.assertEqual((again["read"], again["states"]), (1, ["ignored"]))               # m2 only; m1 stays known
        self.assertEqual(status_of(self.conn, self.app_id), "rejected")

    def test_redo_keeps_what_waits_in_needs_you(self):
        update.record(self.conn, "jane", Mail("n9", "Acme <jobs@acme.example>", "Next steps", NOW, "Acme Robotics"),
                      "screen", "llm", None, "needs", app_id=self.app_id, to_status="screen", now=NOW)
        poll.redo(self.conn, "jane")
        self.assertEqual(self.conn.execute("SELECT state FROM mail_messages WHERE msg_id = 'n9'").fetchone()[0], "needs")

    def test_trash_is_read_too_but_never_spam_or_sent(self):
        poll.poll_profile(self.conn, "jane", NOW, runner=rules_claude())
        lists = [q for m, p, q in self.fake.requests if p == "/gmail/v1/users/me/messages"]
        self.assertTrue(lists)
        for query in lists:                                    # a rejection deleted before the check still counts
            self.assertEqual(query.get("includeSpamTrash"), "true")
            for skipped in ("-in:spam", "-in:sent", "-in:drafts", "-in:chats"):
                self.assertIn(skipped, query["q"])

    def test_the_body_is_never_stored(self):
        poll.poll_profile(self.conn, "jane", NOW, runner=rules_claude())
        row = dict(self.conn.execute("SELECT * FROM mail_messages WHERE msg_id = 'm1'").fetchone())
        self.assertLessEqual(len(row["snippet"]), 300)
        self.assertEqual(row["link"], "https://mail.google.com/mail/u/0/#all/m1")
        dump = json.dumps([dict(r) for r in self.conn.execute("SELECT * FROM mail_messages")])
        self.assertNotIn("Arrives Thursday", dump)                                 # a non-job email: nothing kept
        self.assertNotIn("Your order shipped", dump)

    def test_a_lapsed_sign_in_waits_without_failing(self):
        self.fake.refresh_tokens.clear()
        auth.save({**auth.load(path=signin.token_path("jane")), "expires_at": 0}, path=signin.token_path("jane"))
        result = poll.poll_profile(self.conn, "jane", NOW, runner=rules_claude())
        self.assertIn("lapsed", result["error"])
        self.assertEqual(status_of(self.conn, self.app_id), "applied")
        self.assertTrue(signin.status(self.conn, "jane")["lapsed"])

    def test_nobody_connected_is_skipped_and_cold_runs_once_a_day(self):
        signin.token_path("jane").unlink()
        self.assertEqual(poll.poll_profile(self.conn, "jane", NOW), {"skipped": "mail not connected"})
        report = nightly.run(self.conn, NOW)                  # the nightly job; the server thread is Cold only
        self.assertEqual(report["jane"]["mail"], {"skipped": "mail not connected"})
        self.assertEqual(poll.run(self.conn, NOW + timedelta(minutes=1))["jane"], {"cold": {"cold": "done today"}})


class ServerCase(GoogleCase):
    """The app server on a free port with one applied Acme row; act() presses an email button."""

    def setUp(self):
        super().setUp()
        self.httpd = server.make_server(port=0)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"
        self.app_id = add_acme(self.conn)

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        super().tearDown()

    def act(self, **body) -> dict:
        req = urllib.request.Request(f"{self.base}/api/mail/act", method="POST", data=json.dumps(
            {"profile": "jane", **body}).encode(), headers={"Content-Type": "application/json", "X-Jobhunt": "1"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())

    def connect_url(self, address: str = GMAIL_ADDRESS, tag: str | None = None) -> str:
        query = {"profile": "jane", "t": tag or google_page.tag("connect-gmail", "jane"), "kind": "gmail",
                 "address": address}
        return f"{self.base}/google/connect?{urllib.parse.urlencode(query)}"


class PanelTest(ServerCase):
    def test_needs_you_is_escaped_and_confirm_then_undo_work(self):
        evil = Mail("x1", "Acme <jobs@acme.example>", "<script>alert(1)</script> next steps",
                    datetime(2026, 10, 8, 15, 0, tzinfo=timezone.utc), "Thanks for applying to Acme Robotics.")
        update.record(self.conn, "jane", evil, "rejected", "llm", None, "needs", app_id=self.app_id,
                      to_status="rejected", reason="Claude classified it", now=NOW)
        html = self.act(act="view")["html"]
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertNotIn("<script>", html)
        self.assertIn("not connected yet", html)
        row_id = self.conn.execute("SELECT id FROM mail_messages WHERE msg_id = 'x1'").fetchone()[0]
        answer = self.act(act="confirm", id=str(row_id), status="rejected", app="")
        self.assertIn('data-mail="undo"', answer["html"])
        self.assertEqual(status_of(self.conn, self.app_id), "rejected")
        self.act(act="undo", id=str(row_id))
        self.assertEqual(status_of(self.conn, self.app_id), "applied")
        self.assertEqual(self.act(act="cold-days", value="45")["msg"], "Saved")
        self.assertIn('value="45"', self.act(act="view", view="settings")["html"])

    def test_connect_goes_through_goal11s_sign_in_and_its_answer_page(self):
        target = redirect_target(self.connect_url())
        self.assertIn("gmail.readonly", target)
        self.assertNotIn("spreadsheets", target)
        answer = redirect_target(target)                               # Google -> / on this Mac
        self.assertEqual(urllib.parse.urlsplit(answer).path, "/")
        page = urllib.request.urlopen(self.base + answer[answer.index("/?"):], timeout=10).read()
        self.assertIn(b"Job-application email connected", page)
        self.assertTrue(signin.status(self.conn, "jane")["connected"])
        self.assertFalse(auth.token_path("jane").exists())                # GOAL11's own token stays as it was

    def test_a_connect_link_from_elsewhere_is_refused(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(self.connect_url(tag="forged"), timeout=10)
        self.assertEqual(caught.exception.code, 400)
        caught.exception.close()


class SheetTest(SyncCase):
    def setUp(self):
        super().setUp()
        self.link()

    def test_an_email_status_reaches_the_sheet_and_undo_takes_it_back(self):
        mail = Mail("s1", "Gamma <no-reply@hire.lever.co>", "Gamma: your application",
                    datetime(2026, 10, 8, 15, 0, tzinfo=timezone.utc),
                    "Thank you for applying for the AI Engineer role. Unfortunately, we will not be moving forward "
                    "with your application.")
        poll.process(self.conn, "jane", [mail], NOW, lambda p: None)
        before = self.grid()
        self.sync()
        after = self.grid()
        changed = [(r, c) for r, row in enumerate(after) for c, v in enumerate(row) if before[r][c] != v]
        self.assertEqual(changed, [(self.row_of("Gamma"), self.column("Status(A/R)"))])
        self.assertEqual(after[self.row_of("Gamma")][self.column("Status(A/R)")], "Rejected")
        row_id = self.conn.execute("SELECT id FROM mail_messages WHERE msg_id = 's1'").fetchone()[0]
        update.undo(self.conn, "jane", row_id, "Jane Doe")
        self.sync()
        self.assertIn(self.grid()[self.row_of("Gamma")][self.column("Status(A/R)")], ("", None))

    def test_cold_shows_as_cold_and_a_typed_status_is_left_alone(self):
        result = update.cold_sweep(self.conn, "jane", date(2026, 10, 8))
        self.assertGreaterEqual(result["cold"], 1)                                 # Acme, applied 09/12/2025
        self.sync()
        self.assertEqual(self.grid()[self.row_of("Acme")][self.column("Status(A/R)")], "Cold")
        self.assertEqual(self.grid()[self.row_of("Beta")][self.column("Status(A/R)")], "Rejected")
        self.assertEqual(status_of(self.conn, self.ids["Beta"]), "rejected")


class FakeBox:
    """An IMAP4_SSL stand-in that records every command."""
    RAW = (b"From: Acme Robotics <no-reply@greenhouse.io>\r\nSubject: Update\r\nDate: Thu, 08 Oct 2026 10:00:00 -0500"
           b"\r\nContent-Type: text/plain\r\n\r\nWe will not be moving forward with your application.\r\n")

    def __init__(self, host):
        self.calls = []
        FakeBox.last = self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self, user, secret):
        self.calls.append(("login", user))

    def select(self, mailbox, readonly=False):
        self.calls.append(("select", mailbox, readonly))

    def uid(self, command, *args):
        self.calls.append(("uid", command, *args))
        if command == "SEARCH":
            return "OK", [b"7"]
        return "OK", [(b"7 (X-GM-MSGID 1800000000000000001 BODY[] {170}", self.RAW), b")"]


class ImapTest(GoogleCase):
    def test_imap_opens_read_only_and_peeks(self):
        from job_hunt_agent.mail import imap
        with mock.patch.object(imap, "password", return_value="app-pass"):
            mails = imap.ImapReader("jane@gmail.com", connect=FakeBox).since(poll.start())
        self.assertEqual([m.subject for m in mails], ["Update"])
        self.assertEqual(mails[0].msg_id, format(1800000000000000001, "x"))
        calls = FakeBox.last.calls
        self.assertIn(("select", imap.GMAIL_BOX, True), calls)                     # EXAMINE: read-only
        fetches = [c for c in calls if c[:2] == ("uid", "FETCH")]
        self.assertTrue(fetches and all("BODY.PEEK[]" in c[3] for c in fetches))  # the read flag never changes
        self.assertFalse([c for c in calls if c[0] == "uid" and c[1] in ("STORE", "COPY", "MOVE", "EXPUNGE")])

    def test_no_keychain_password_is_a_clear_error(self):
        from job_hunt_agent.mail import MailError, imap
        with mock.patch.object(imap, "password", return_value=None):
            with self.assertRaisesRegex(MailError, "Keychain"):
                imap.ImapReader("jane@gmail.com", connect=FakeBox).since(poll.start())
