"""the Resumes tab's day groups (daygroups.stamp) and the time each view groups by (rqueue.made_at)."""
import unittest
from datetime import datetime, timedelta, timezone

from job_hunt_agent import daygroups, rqueue

NOW = datetime(2026, 10, 12, 19, 0, tzinfo=timezone.utc)              # Mon Oct 12, 2:00 PM in Chicago


def card(name: str, at: str | None) -> dict:
    return {"uid": name, "rq": {"made_at": at}}


def utc(days: int = 0, hours: float = 0) -> str:
    return (NOW - timedelta(days=days, hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


class StampTest(unittest.TestCase):
    def test_days_newest_first_numbered_with_time_and_old_ones_last(self):
        cards = [card("old", utc(days=45)), card("a", utc(hours=1)), card("y", utc(days=1)), card("b", utc(hours=4)),
                 card("fri", utc(days=3)), card("none", None)]
        got = daygroups.stamp(cards, NOW)
        self.assertEqual([(c["uid"], c["rq"]["day"], c["rq"]["n"]) for c in got],
                         [("a", "Today · Mon Oct 12 (2)", 1), ("b", "Today · Mon Oct 12 (2)", 2),
                          ("y", "Yesterday · Sun Oct 11 (1)", 1), ("fri", "Fri Oct 9 (1)", 1),
                          ("old", "More than 30 days (2)", 1), ("none", "More than 30 days (2)", 2)])
        self.assertEqual(got[0]["rq"]["time"], "1:00 PM", "Chicago time")
        self.assertEqual(got[-1]["rq"]["time"], "")

    def test_chicago_midnight_decides_the_day(self):
        late = datetime(2026, 10, 12, 4, 30, tzinfo=timezone.utc)      # 11:30 PM Sun in Chicago
        got = daygroups.stamp([card("x", "2026-10-12T04:00:00Z")], late)
        self.assertEqual((got[0]["rq"]["day"], got[0]["rq"]["time"]), ("Today · Sun Oct 11 (1)", "11:00 PM"))

    def test_the_order_inside_a_day_is_kept(self):
        cards = [card("needs", utc(hours=5)), card("new", utc(hours=1)), card("closed", utc(hours=0.5))]
        self.assertEqual([c["uid"] for c in daygroups.stamp(cards, NOW)], ["needs", "new", "closed"])

    def test_the_queue_keeps_its_run_order(self):
        cards = [card("q1", utc(days=1)), card("q2", utc(hours=1)), card("q3", utc(hours=2))]
        got = daygroups.stamp(cards, NOW, keep_order=True)
        self.assertEqual([(c["uid"], c["rq"]["n"]) for c in got], [("q1", 1), ("q2", 1), ("q3", 2)])
        self.assertEqual(got[1]["rq"]["day"], "Today · Mon Oct 12 (2)")

    def test_unreadable_times_are_old(self):
        self.assertIsNone(daygroups.local("yesterday"))
        self.assertIsNone(daygroups.local("2026-10-12T10:00:00"), "no zone: not guessed")


class MadeAtTest(unittest.TestCase):
    JOB = {"created_at": "q", "finished_at": "f", "times": {"queued": "Q", "made": "M", "approved": "A"}}

    def test_each_view_groups_by_its_own_time(self):
        self.assertEqual(rqueue.made_at(self.JOB, "queue"), "Q")
        self.assertEqual(rqueue.made_at(self.JOB, "ready"), "f")
        self.assertEqual(rqueue.made_at(self.JOB, "done"), "A")
        self.assertEqual(rqueue.made_at({"finished_at": "f"}, "done"), "f")
        self.assertEqual(rqueue.made_at({"created_at": "q"}, "queue"), "q")


if __name__ == "__main__":
    unittest.main()
