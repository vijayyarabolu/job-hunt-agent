"""GOAL22 (user 2026-10-13 11:08): Generate resume never fails on a location. Target's street address now gives
"Brooklyn Park, MN"; a job whose city can't be worked out, or that names several, is not sent but comes back in
`needs_place` for the picker; the pick (a city, Remote, or typed text) is kept for the job."""
from job_hunt_agent import db, rgjobs

from tests.test_goal8_flow import PID, Goal8Case

TARGET = "7000 Target Pkwy N,NCD-0375 Brooklyn Park,MN 55445"


class PlacesCase(Goal8Case):
    def place(self, uid: str, location: str, workplace: str | None = "onsite"):
        with db.tx(self.conn):
            self.conn.execute("UPDATE jobs SET location = ?, workplace = ? WHERE uid = ?", (location, workplace, uid))


class GenerateNeverFailsOnALocation(PlacesCase):
    def test_targets_street_address_is_sent_as_its_city(self):
        self.place(self.uids[0], TARGET)
        out = rgjobs.generate(self.conn, PID, self.uids[:1])
        self.assertEqual((out["queued"], out["needs_place"]), (1, []))
        self.assertEqual(self.rg.posts[-1][0]["location"], "Brooklyn Park, MN")

    def test_several_cities_come_back_to_pick_and_nothing_is_sent_for_them(self):
        self.place(self.uids[0], "Birmingham, AL, USA; Tarrant, AL 35217, USA")
        out = rgjobs.generate(self.conn, PID, self.uids[:2])
        self.assertEqual(out["queued"], 1)                                    # the other job still goes
        self.assertEqual([j["source_job_id"] for j in self.rg.posts[-1]], [self.uids[1]])
        ask = out["needs_place"][0]
        self.assertEqual((ask["uid"], ask["choices"], ask["home"]),
                         (self.uids[0], ["Birmingham, AL", "Tarrant, AL"], ""))          # no location in the profile: no city
        self.assertEqual((ask["company"], ask["location"]), ("Acme", "Birmingham, AL, USA; Tarrant, AL 35217, USA"))

    def test_a_place_that_cannot_be_read_asks_too(self):
        self.place(self.uids[0], "Aurora St Lukes Medical Center - 2900 W Oklahoma Ave")
        out = rgjobs.generate(self.conn, PID, self.uids[:1])
        self.assertEqual((out["queued"], len(out["needs_place"]), self.rg.posts), (0, 1, []))

    def test_the_pick_is_sent_and_kept_for_the_job(self):
        self.place(self.uids[0], "Birmingham, AL, USA; Tarrant, AL 35217, USA")
        out = rgjobs.generate(self.conn, PID, self.uids[:1], {self.uids[0]: "Tarrant, AL"})
        self.assertEqual((out["queued"], out["needs_place"]), (1, []))
        self.assertEqual((self.rg.posts[-1][0]["workplace"], self.rg.posts[-1][0]["location"]), ("onsite", "Tarrant, AL"))
        self.assertEqual(rgjobs.picked(self.conn, PID, self.uids[0]), "Tarrant, AL")
        self.rg.jobs.clear()                                                  # done: Generate again asks nothing
        again = rgjobs.generate(self.conn, PID, self.uids[:1])
        self.assertEqual((again["queued"], self.rg.posts[-1][0]["location"]), (1, "Tarrant, AL"))

    def test_remote_sends_the_persons_own_city(self):
        self.place(self.uids[0], "Toronto, ON")
        rgjobs.generate(self.conn, PID, self.uids[:1], {self.uids[0]: "remote"})
        self.assertEqual((self.rg.posts[-1][0]["workplace"], self.rg.posts[-1][0]["location"]), ("remote", None))
        self.assertEqual(rgjobs.picked(self.conn, PID, self.uids[0]), "remote")

    def test_typed_text_is_read_like_a_location_and_bad_text_is_refused(self):
        self.place(self.uids[0], "Toronto, ON")
        rgjobs.generate(self.conn, PID, self.uids[:1], {self.uids[0]: "brooklyn park mn"})
        self.assertEqual(self.rg.posts[-1][0]["location"], "Brooklyn Park, MN")
        with self.assertRaisesRegex(ValueError, "Brooklyn Park, MN"):
            rgjobs.generate(self.conn, PID, self.uids[1:2], {self.uids[1]: "somewhere nice"})
        with self.assertRaises(ValueError):
            rgjobs.generate(self.conn, PID, self.uids[1:2], ["Austin, TX"])

    def test_a_pick_only_counts_for_the_jobs_sent(self):
        rgjobs.generate(self.conn, PID, self.uids[:1], {self.uids[2]: "Tarrant, AL"})
        self.assertIsNone(rgjobs.picked(self.conn, PID, self.uids[2]))

    def test_a_pick_for_a_job_already_in_the_queue_is_not_kept(self):
        self.rg.add(PID, "Acme", "MLE", "jd", source_job_id=self.uids[0], status="needs_place")
        out = rgjobs.generate(self.conn, PID, self.uids[:1], {self.uids[0]: "Tarrant, AL"})
        self.assertEqual((out["queued"], out["skipped"][0]["why"]), (0, "already in the queue"))
        self.assertIsNone(rgjobs.picked(self.conn, PID, self.uids[0]))

    def test_the_other_person_is_asked_on_their_own(self):
        self.place(self.uids[0], "Toronto, ON")
        rgjobs.remember(self.conn, "someone_else", self.uids[0], "Austin, TX")
        self.assertEqual(len(rgjobs.generate(self.conn, PID, self.uids[:1])["needs_place"]), 1)


class CleanPick(PlacesCase):
    def test_shapes(self):
        for typed, want in (("Brooklyn Park, MN", "Brooklyn Park, MN"), ("Brooklyn Park,MN", "Brooklyn Park, MN"),
                            ("  austin   tx ", "Austin, TX"), ("Remote", "remote"), ("Lees Summit, Missouri", "Lees Summit, MO")):
            with self.subTest(typed=typed):
                self.assertEqual(rgjobs.clean_pick(typed), want)
        for bad in ("", "Toronto, ON", "x"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    rgjobs.clean_pick(bad)


if __name__ == "__main__":
    import unittest
    unittest.main()
