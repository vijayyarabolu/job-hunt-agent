"""GOAL15 §1, server side: "Updates from email" folds to one summary line, Cold is one grouped line (names only on
Show, inline when 10 or fewer), the paging value is bounded, and one Undo all reverts every sweep of the day."""
import unittest
from datetime import date, timedelta

from job_hunt_agent import db
from job_hunt_agent.mail import panel, update
from tests.test_mail_rules import MailCase


class SummaryTest(unittest.TestCase):
    def test_the_folded_line_names_each_count(self):
        counts = {"rejected": 1, "screen": 0, "interview": 0, "offer": 1, "received": 5, "cold": 366}
        self.assertEqual(panel.summary_line(counts), "Email today: 1 rejected · 1 offer · 5 received · 366 went Cold")
        self.assertEqual(panel.summary_line(dict.fromkeys(counts, 0)), "Email today: nothing yet")

    def test_the_view_value_is_parsed_and_bounded(self):
        self.assertEqual(panel._view(""), ("", 20, 0))
        self.assertEqual(panel._view("cold|40|20"), ("cold", 40, 20))
        self.assertEqual(panel._view("bogus|99999999|-3"), ("", panel.MAX_SHOWN, 0))
        self.assertEqual(panel._view("received|5"), ("received", 20, 0), "never fewer than one page")


class ColdGroupTest(MailCase):
    def setUp(self):
        super().setUp()
        with db.tx(self.conn):          # MailCase's rows say 2026-09-20: from 2026-10-19 on they went Cold here too
            self.conn.execute("UPDATE applications SET applied_on = ?", (date.today().isoformat(),))

    def add_rows(self, n: int, applied_on: str) -> None:
        with db.tx(self.conn):
            for i in range(n):
                self.conn.execute("INSERT INTO applications(profile, company, role_title, status, applied_on, source) "
                                  "VALUES ('jane', ?, 'ML Engineer', 'applied', ?, 'feed')", (f"ZZ_TEST {i:02d}", applied_on))

    def sweep(self, n: int) -> None:
        self.add_rows(n, (date.today() - timedelta(days=60)).isoformat())
        update.cold_sweep(self.conn, "jane", date.today())

    def test_a_big_sweep_is_one_line_until_show(self):
        self.sweep(12)
        html = panel.panel(self.conn, "jane")
        self.assertIn("<details class=\"mail\">", html, "folded by default")
        self.assertIn("Email today: 12 went Cold", html)
        self.assertIn("<b>12 applications went Cold</b> (no reply in 30 days)", html)
        self.assertNotIn("cold-names", html)
        shown = panel.act(self.conn, "jane", {"act": "view", "value": "|20|20"}, "Jane")["html"]
        self.assertEqual(shown.count("<li>ZZ_TEST"), 12)
        self.assertIn("<details class=\"mail\" open>", shown, "pressed inside: stays open")

    def test_a_small_sweep_lists_its_names_at_once(self):
        self.sweep(3)
        html = panel.panel(self.conn, "jane")
        self.assertEqual(html.count("<li>ZZ_TEST"), 3)
        self.assertNotIn('>Show<', html)

    def test_undo_all_reverts_every_sweep_of_the_day(self):
        self.sweep(4)
        self.add_rows(2, (date.today() - timedelta(days=59)).isoformat())
        update.cold_sweep(self.conn, "jane", date.today() + timedelta(days=1))
        batches = ",".join(s["batch"] for s in update.sweeps(self.conn, "jane"))
        self.assertEqual(len(batches.split(",")), 2)
        cold = self.conn.execute("SELECT COUNT(*) FROM applications WHERE status = 'ghosted'").fetchone()[0]
        self.assertGreaterEqual(cold, 6)                  # the 6 test rows, plus fixture rows old enough by now
        out = panel.act(self.conn, "jane", {"act": "cold-undo", "id": batches}, "Jane")
        self.assertEqual(out["msg"], f"{cold} applications are back to Applied")
        left = self.conn.execute("SELECT COUNT(*) FROM applications WHERE status = 'ghosted'").fetchone()[0]
        self.assertEqual(left, 0)


if __name__ == "__main__":
    unittest.main()
