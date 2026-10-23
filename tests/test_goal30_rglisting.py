"""GOAL30-app §0: "Resume app is offline" while resumegen was running. One slow answer (a Mac short of memory) past
the 4 s timeout flipped the page to offline with Queue 0 / Ready 0 / Done today 0, hiding 2 ready and 17 done today.
Now the list call waits longer, askers waiting on a call in flight share its answer, and for the page's lists
(keep=True) one failed call answers the last good list and only two failed tries in a row mean offline. Autofill,
approving and archiving still ask strictly: they act on resumegen, so an old list would pick a resume it cannot get."""
import threading
import time
import unittest
from unittest import mock

from job_hunt_agent import rgclient, rgjobs

PID = "alex"
GOOD = {"jobs": [{"id": 1, "profile": PID}, {"id": 2, "profile": PID}]}


class ListingToleranceTest(unittest.TestCase):
    def setUp(self):
        rgjobs._LISTED.clear()

    def test_one_failed_call_answers_the_last_good_list(self):
        with mock.patch.object(rgclient, "request", side_effect=[GOOD, rgclient.Offline("Resume app is offline")]):
            self.assertEqual([j["id"] for j in rgjobs.listing(PID, keep=True)], [1, 2])
            self.assertEqual([j["id"] for j in rgjobs.listing(PID, keep=True)], [1, 2])

    def test_two_failed_calls_in_a_row_are_offline(self):
        down = rgclient.Offline("Resume app is offline")
        with mock.patch.object(rgclient, "request", side_effect=[GOOD, down, down]):
            rgjobs.listing(PID, keep=True)
            rgjobs.listing(PID, keep=True)
            with self.assertRaises(rgclient.Offline):
                rgjobs.listing(PID, keep=True)

    def test_a_good_answer_resets_the_count(self):
        down = rgclient.Offline("Resume app is offline")
        with mock.patch.object(rgclient, "request", side_effect=[GOOD, down, GOOD, down, GOOD]):
            for _ in range(5):
                self.assertEqual(len(rgjobs.listing(PID, keep=True)), 2)

    def test_with_nothing_kept_it_tries_twice_before_offline(self):
        down = rgclient.Offline("Resume app is offline")
        with mock.patch.object(rgclient, "request", side_effect=[down, GOOD]) as req:
            self.assertEqual(len(rgjobs.listing(PID, keep=True)), 2)
        self.assertEqual(req.call_count, 2)
        rgjobs._LISTED.clear()
        with mock.patch.object(rgclient, "request", side_effect=[down, down]), self.assertRaises(rgclient.Offline):
            rgjobs.listing(PID, keep=True)

    def test_askers_waiting_on_a_call_share_its_answer(self):
        # the first asker's call started before the others asked, so they share the next one, not that one
        calls = []

        def slow(*args, **kwargs):
            calls.append(args)
            time.sleep(0.3)
            return GOOD
        with mock.patch.object(rgclient, "request", side_effect=slow):
            threads = [threading.Thread(target=rgjobs.listing, args=(PID, True)) for _ in range(5)]
            for t in threads:
                t.start()
                time.sleep(0.02)
            for t in threads:
                t.join()
        self.assertEqual(len(calls), 2)

    def test_a_call_that_started_before_the_ask_is_never_its_answer(self):
        # review: a resume queued while a poll was in flight, then a strict ask, got the poll's older list
        newer = {"jobs": [{"id": 3, "profile": PID}]}
        answers = iter([GOOD, newer])

        def slow(*args, **kwargs):
            time.sleep(0.3)
            return next(answers)
        with mock.patch.object(rgclient, "request", side_effect=slow):
            poll = threading.Thread(target=rgjobs.listing, args=(PID, True))
            poll.start()
            time.sleep(0.1)                                   # the poll is in flight; now the change and the ask
            self.assertEqual([j["id"] for j in rgjobs.listing(PID)], [3])
            poll.join()

    def test_waiters_share_a_failure_instead_of_queueing_their_own(self):
        # review: with resumegen hanging, each waiter used to try twice for 10 s, one after another
        calls, failed = [], []

        def down(*args, **kwargs):
            calls.append(args)
            time.sleep(0.1)
            raise rgclient.Offline("Resume app is offline")

        def ask():
            try:
                rgjobs.listing(PID, keep=True)
            except rgclient.Offline:
                failed.append(1)
        with mock.patch.object(rgclient, "request", side_effect=down):
            threads = [threading.Thread(target=ask) for _ in range(6)]
            for t in threads:
                t.start()
                time.sleep(0.01)
            for t in threads:
                t.join()
        self.assertEqual(len(failed), 6)
        self.assertLessEqual(len(calls), 4)                   # two rounds of two tries, not six

    def test_an_old_last_good_list_is_not_shown_as_current(self):
        down = rgclient.Offline("Resume app is offline")
        clock = [1000.0]
        with mock.patch.object(rgclient, "request", side_effect=[GOOD, down]), \
                mock.patch.object(rgjobs.time, "monotonic", lambda: clock[0]):
            rgjobs.listing(PID, keep=True)
            clock[0] += rgjobs.LAST_GOOD_MAX_S + 1
            with self.assertRaises(rgclient.Offline):
                rgjobs.listing(PID, keep=True)

    def test_a_later_ask_never_reuses_an_older_answer(self):
        newer = {"jobs": [{"id": 3, "profile": PID}]}
        with mock.patch.object(rgclient, "request", side_effect=[GOOD, newer]):
            rgjobs.listing(PID, keep=True)
            self.assertEqual([j["id"] for j in rgjobs.listing(PID, keep=True)], [3])

    def test_the_list_waits_longer_than_a_button_press(self):
        with mock.patch.object(rgclient, "request", return_value=GOOD) as req:
            rgjobs.listing(PID, keep=True)
        self.assertGreater(req.call_args.kwargs["timeout"], rgclient.TIMEOUT_S)

    def test_a_strict_ask_never_answers_an_old_list(self):
        down = rgclient.Offline("Resume app is offline")
        with mock.patch.object(rgclient, "request", side_effect=[GOOD, down]):
            rgjobs.listing(PID, keep=True)
            with self.assertRaises(rgclient.Offline):
                rgjobs.listing(PID)

    def test_people_are_kept_apart(self):
        down = rgclient.Offline("Resume app is offline")
        with mock.patch.object(rgclient, "request", side_effect=[GOOD, down, down]):
            rgjobs.listing(PID, keep=True)
            with self.assertRaises(rgclient.Offline):
                rgjobs.listing("jordan", keep=True)


if __name__ == "__main__":
    unittest.main()
