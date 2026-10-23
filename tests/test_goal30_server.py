"""GOAL30 server side: §1 a question in words no rule knows is mapped once by Claude (an injected runner here, never a
real call) and cached forever, the user's saved answer to the same question in other words is found by its ideas, legal and
personal facts are never guessed, the start date setting; §2 whose job a saved answer was written for and an answer
naming another company; §3 / §4 Location for this job only and a ✎ / chip kept where the user chose; §0 a field inside a
job or school entry is never answered from the bank."""
import json
from pathlib import Path
from unittest import mock

from job_hunt_agent import config, db
from job_hunt_agent.autofill import api, bank, experience, generate, keys, learn, meaning, origin

from tests.test_goal23_autofill import AutofillCase
from tests.test_goal8_flow import PID

CASES = json.loads((Path(__file__).resolve().parent / "fixtures" / "autofill" / "goal30" / "meaning_cases.json")
                   .read_text())["cases"]


class Runner:
    """Stands in for `claude -p`: answers with `reply` (or raises), counting calls."""
    def __init__(self, reply=None, fail=False):
        self.reply, self.fail, self.calls, self.prompts = reply, fail, 0, []

    def __call__(self, prompt, pid):
        self.calls += 1
        self.prompts.append(prompt)
        if self.fail:
            raise ValueError("Draft failed: usage limit reached")
        return json.dumps(self.reply)


def one(got: dict, label: str) -> dict:
    return got[label[:20]]


class MeaningTest(AutofillCase):
    def setUp(self):
        super().setUp()
        meaning._resting.clear()

    def item(self, n, label, options=()):
        return {"id": str(n), "label": label, "qnorm": keys.norm(label), "options": list(options)}

    def test_one_call_maps_the_page_and_every_answer_is_cached_none_too(self):
        run = Runner([{"id": "1", "field": "employed"}, {"id": "2", "field": "saved:0"}, {"id": "3", "field": "none"}])
        items = [self.item(1, "Are you in a job right now?"), self.item(2, "What drew you to us?"),
                 self.item(3, "Favourite colour?")]
        got = meaning.ask(self.conn, PID, items, ["why do you want to work here"], run)
        self.assertEqual(got, {"1": "employed", "2": "q:why do you want to work here"})
        self.assertEqual(run.calls, 1)
        self.assertIn("never answer it", run.prompts[0])
        again = meaning.ask(self.conn, PID, items, ["why do you want to work here"], Runner(fail=True))
        self.assertEqual(again, got)                                 # all three from the cache, no call
        self.assertEqual(meaning.cached(self.conn, PID, keys.norm("Favourite colour?")), "none")

    def test_a_failed_call_maps_and_caches_nothing(self):
        items = [self.item(1, "Are you in a job right now?")]
        self.assertEqual(meaning.ask(self.conn, PID, items, [], Runner(fail=True)), {})
        self.assertIsNone(meaning.cached(self.conn, PID, items[0]["qnorm"]))

    def test_after_a_failed_call_claude_rests_ten_minutes_then_is_asked_again(self):
        """A usage limit or a lost login: the next Autofill does not wait on another call that will fail."""
        items = [self.item(1, "Are you in a job right now?")]
        meaning.ask(self.conn, PID, items, [], Runner(fail=True))
        run = Runner([{"id": "1", "field": "employed"}])
        self.assertEqual(meaning.ask(self.conn, PID, items, [], run), {})
        self.assertEqual(run.calls, 0)
        with mock.patch.object(meaning.time, "monotonic", return_value=meaning.time.monotonic() + meaning.REST_S + 1):
            self.assertEqual(meaning.ask(self.conn, PID, items, [], run), {"1": "employed"})
        self.assertEqual(run.calls, 1)

    def test_a_setting_or_a_made_up_key_or_a_missing_saved_question_is_none(self):
        run = Runner([{"id": "1", "field": "start_weeks"}, {"id": "2", "field": "made_up"}, {"id": "3", "field": "saved:9"}])
        got = meaning.ask(self.conn, PID, [self.item(1, "Weeks?"), self.item(2, "Thing?"), self.item(3, "Other?")], [], run)
        self.assertEqual(got, {})

    def test_resolve_fills_a_mapped_question_for_review_and_says_claude_read_it(self):
        label = "How should we ring you?"
        run = Runner([{"id": label[:20], "field": "phone"}])
        got = one(self.resolve(self.field(label), ask_claude=True, runner=run), label)
        self.assertEqual((got["status"], got["value"], got["review"]), ("fill", "913-555-0100", True))
        self.assertIn("Claude read this question as", got["why"])

    def test_without_ask_claude_no_call_is_made(self):
        label = "How should we ring you?"
        run = Runner([{"id": label[:20], "field": "phone"}])
        got = one(self.resolve(self.field(label, required=True), runner=run), label)
        self.assertEqual((got["status"], run.calls), ("needs", 0))

    def test_the_canary_person_never_calls_claude(self):
        seen = {}
        with mock.patch.object(bank, "resolve", lambda conn, pid, fields, ctx: seen.update(ctx) or []), \
                mock.patch.object(api, "job_address", lambda *a, **k: {}), \
                mock.patch.object(api.sponsor, "badge_for", lambda *a: None), \
                mock.patch.object(api.sponsor, "says_yes", lambda *a: False), mock.patch.object(bank, "load", lambda *a: {}):
            api._resolve(self.conn, "canary", {"fields": []})
            self.assertFalse(seen["ask_claude"])
            api._resolve(self.conn, PID, {"fields": []})
            self.assertTrue(seen["ask_claude"])

    def test_his_saved_question_in_other_words_is_found_by_its_ideas(self):
        own = {"are you currently employed": {}, "do you need sponsorship now": {}}
        self.assertEqual(learn.similar("current employment status", own), "are you currently employed")
        self.assertIsNone(learn.similar("are you currently unemployed", {"are you currently employed": {}}))


class MeaningTableTest(AutofillCase):
    def test_the_server_never_maps_a_wording_meant_for_none(self):
        """The 150+ real wordings: where the honest answer is Needs you, job-hunt-agent's own rules stay out of it (the
        extension's classifier is checked against the whole table in test_goal30_meaning)."""
        self.assertGreaterEqual(len(CASES), 150)
        for label, kind, want, _src in CASES:
            if want is None and kind != "file":
                with self.subTest(label):
                    found = keys.classify(keys.norm(label))
                    self.assertTrue(found is None or found.key == "account_password", found and found.key)


class NoGuessTest(AutofillCase):
    def test_legal_and_personal_keys_are_no_guess(self):
        for key in ("work_auth", "sponsor_future", "salary", "start_date", "notice", "employed", "hcp"):
            with self.subTest(key):
                self.assertTrue(keys.BY_KEY[key].no_guess)

    def test_a_personal_question_he_never_answered_needs_him(self):
        label = "Are you currently employed?"
        got = one(self.resolve(self.field(label, "select", options=["Yes", "No"])), label)
        self.assertEqual(got["status"], "needs")


class StartWeeksTest(AutofillCase):
    def test_a_start_date_is_his_weeks_setting_2_by_default(self):
        bank.save(self.conn, PID, "start_date", "")
        label = "What is your desired start date?"
        got = one(self.resolve(self.field(label, "date")), label)
        self.assertEqual((got["status"], got["value"], got["weeks"]), ("fill", "2 weeks", 2))

    def test_the_setting_is_0_to_26_weeks(self):
        self.assertEqual(bank.save(self.conn, PID, "start_weeks", "3 weeks")["answer"], "3")
        self.assertEqual(bank.weeks(bank.load(self.conn, PID)), 3)
        for bad in ("27", "soon", "-1"):
            with self.subTest(bad), self.assertRaises(ValueError):
                bank.save(self.conn, PID, "start_weeks", bad)

    def test_a_saved_wait_in_his_words_is_kept(self):
        bank.save(self.conn, PID, "start_date", "2 weeks after offer acceptance")
        got = one(self.resolve(self.field("When can you start?")), "When can you start?")
        self.assertEqual(got["value"], "2 weeks after offer acceptance")


class RepeaterTest(AutofillCase):
    def test_a_field_inside_a_job_or_school_entry_is_never_answered_from_the_bank(self):
        for group in ("work", "edu"):
            with self.subTest(group):
                self.assertEqual(one(self.resolve({**self.field("Location"), "group": group}), "Location")["status"], "skip")
        self.assertEqual(one(self.resolve(self.field("Location")), "Location")["status"], "fill")

    def test_a_school_saved_with_its_short_name_still_gets_the_resumes_place(self):
        base = json.loads((config.PROFILES / PID / "baseline.json").read_text())
        (config.PROFILES / PID / "baseline.json").write_text(json.dumps(
            {**base, "education": [{"school": "Lakeview State University, Springfield, IL, USA"}]}))
        got = experience._schools(PID, [{"school": "Lakeview State University (UCM)", "location": ""}])
        self.assertEqual((got[0]["school"], got[0]["location"]), ("Lakeview State University (UCM)", "Springfield, IL"))


class OriginTest(AutofillCase):
    def learned(self, key, company, uid):
        with db.tx(self.conn):
            self.conn.execute("INSERT INTO autofill_learned(profile, scope, key, question, answer, how, company, job_uid, at) "
                              "VALUES (?,?,?,?,?,?,?,?,?)", (PID, "global", key, "Why us?", "Globex is great.", "hand",
                                                             company, uid, "2026-10-01T10:00:00Z"))

    def test_an_answer_written_for_another_job_says_so(self):
        self.learned("q:why us", "Globex", "J9")
        view = origin.of(self.conn, PID, "q:why us", self.uids[0])
        self.assertEqual((view["company"], view["at"]), ("Globex", "2026-10-01"))
        self.assertEqual(origin.label_of({"company": "Globex", "title": "ML Engineer"}), "Written for Globex, ML Engineer")
        self.assertIsNone(origin.of(self.conn, PID, "q:why us", "J9"))           # written for this very job

    def test_an_answer_naming_another_company_is_flagged_whole_words_only(self):
        with db.tx(self.conn):                          # a company the user saved an answer for (applications are the other source)
            for company in ("Globex", "Initech"):
                self.conn.execute("INSERT INTO autofill_snippets(profile, company, question, qnorm, text, at) VALUES "
                                  "(?,?,?,?,?,?)", (PID, company, "Why us?", "why us", "Text.", "2026-10-01T10:00:00Z"))
        self.assertEqual(origin.names_other(self.conn, PID, "I loved my time talking to Globex.", "Acme"), "Globex")
        self.assertIsNone(origin.names_other(self.conn, PID, "I loved Globex.", "Globex"))
        self.assertIsNone(origin.names_other(self.conn, PID, "Globexy tools", "Acme"))


class FilledLookupTest(AutofillCase):
    def test_a_filled_open_question_says_whose_job_and_which_company_it_names(self):
        text = "I loved building retrieval at Globex."
        with db.tx(self.conn):
            self.conn.execute("INSERT INTO autofill_snippets(profile, job_uid, company, question, qnorm, text, at) VALUES "
                              "(?,?,?,?,?,?,?)", (PID, "J9", "Globex", "Why us?", "why us", text, "2026-10-01T10:00:00Z"))
        got = generate.lookup(PID, {"uid": self.uids[0], "company": "Acme", "filled": {"What draws you to this role?": text}},
                              self.conn)["filled"]["What draws you to this role?"]
        self.assertEqual((got["origin"]["company"], got["names"], got["label"]), ("Globex", "Globex", "Written for Globex"))


class KeepTest(AutofillCase):
    def test_for_this_job_only_fills_that_job_and_no_other(self):
        uid, other = self.uids[0], self.uids[1]
        label = "Why do you want this role?"
        api.keep(self.conn, PID, {"uid": uid, "label": label, "value": "Because.", "scope": "job"})
        mine = one(self.resolve(self.field(label), job_key=uid), label)
        theirs = one(self.resolve(self.field(label), job_key=other), label)
        self.assertEqual((mine["status"], mine["value"]), ("fill", "Because."))
        self.assertNotEqual(theirs.get("value"), "Because.")

    def test_everywhere_saves_a_known_field_to_its_key(self):
        out = api.keep(self.conn, PID, {"label": "How did you hear about us?", "value": "LinkedIn", "scope": "everywhere"})
        self.assertEqual(out["target"], "how_heard")
        self.assertEqual(bank.load(self.conn, PID)["how_heard"]["answer"], "LinkedIn")

    def test_a_bad_scope_a_company_scope_without_a_company_and_no_job_are_refused(self):
        for args in ({"scope": "planet"}, {"scope": "company"}, {"scope": "job"}):
            with self.subTest(args), self.assertRaises(ValueError):
                api.keep(self.conn, PID, {"label": "Q?", "value": "A", **args})


class LocationChoiceTest(AutofillCase):
    def test_where_you_live_is_kept_for_this_job_only(self):
        uid = self.uids[0]
        out = api.choose_location(self.conn, PID, {"uid": uid, "choice": "home"})
        self.assertEqual(out["location"]["choice"], "home")
        self.assertEqual(meaning.job_answers(self.conn, PID, uid)[meaning.JOB_LOCATION], "home")
        self.assertEqual(meaning.job_answers(self.conn, PID, self.uids[1]), {})

    def test_a_choice_needs_a_job_and_is_resume_or_home(self):
        for args in ({"choice": "home"}, {"uid": "x", "choice": "mars"}):
            with self.subTest(args), self.assertRaises(ValueError):
                api.choose_location(self.conn, PID, args)
