"""GOAL28 §5: outreach for three kinds of person (recruiter / AI/ML manager / peer engineer). Messages are drafted only,
from that person's own resume (a fact bank's Part 1 when the profile has fact_bank.md, else the current resume): a LinkedIn note <= 300 characters
(greeting, "I applied for <role> at <company>", one JD hook, one proof line, one ask by kind) and a cold email < 120
words with a subject; no flattery, no em dashes. Hunter runs only for the kinds still missing, filtered by department,
behind the daily cap and the cache."""
import json
import re
import unittest
from unittest import mock

from job_hunt_agent import config, contacts, outreach, trackapi
from job_hunt_agent.finder import hunter, http, kinds, ledger, service
from tests.test_goal6_contacts import KEY
from tests.test_tracker_ui import TrackerCase

JD = ("About the role\n\nJoin our Applied AI team.\n\nRequirements:\n"
      "- 3+ years building RAG systems and LLM agents in Python with LangGraph\n"
      "- Experience shipping on AWS with evaluation and guardrails\n\nNice to have:\n- MCP servers\n")
ROW = {"company": "Acme Robotics", "role_title": "AI Engineer", "date_applied": "10/15/2026"}
FLATTERY = re.compile(r"(?i)\b(impressed|admire|amazing|incredible|passionate|thrilled|excited|love your)\b")


class KindTest(unittest.TestCase):
    def test_titles(self):
        cases = {"Technical Recruiter": "recruiter", "Talent Acquisition Partner": "recruiter",
                 "Engineering Manager, Machine Learning": "manager", "Head of AI": "manager",
                 "Director of Applied Science": "manager", "Senior Machine Learning Engineer": "peer",
                 "Applied Scientist": "peer", "AI Engineer": "peer", "Chief Executive Officer": None,
                 "Account Executive": None, "Software Engineer": None}
        for title, kind in cases.items():
            self.assertEqual(kinds.kind_of(title), kind, title)

    def test_hunter_departments_follow_the_missing_kinds(self):
        self.assertEqual(kinds.departments(["recruiter"]), "hr")
        self.assertEqual(kinds.departments(["manager", "peer"]), "it,management,research")


class MessageTest(unittest.TestCase):
    def check(self, kind: str, out: dict, proof_source: str):
        note, mail = out["note"], out["email"]
        self.assertLessEqual(len(note), 300, note)
        self.assertLess(len(mail.split()), 120, mail)
        self.assertTrue(out["subject"])
        self.assertIn("I applied for AI Engineer at Acme Robotics.", note)
        self.assertIn(kinds.ASKS[kind], note)
        self.assertIn(kinds.ASKS[kind], mail)
        self.assertNotIn("—", note + mail + out["subject"])
        self.assertNotIn("–", note + mail)
        self.assertIsNone(FLATTERY.search(note + mail))
        proof = out["proof"]
        self.assertIn(proof["text"], proof_source)                                       # a real line of THEIR resume
        self.assertIn(proof["text"].split(",")[0].split(" ", 1)[1][:30], note)           # the proof line, verbatim
        self.assertTrue(proof["hook"])                                                    # it names a JD skill...
        self.assertIn(f"The role asks for {proof['hook'][0]}", note)                      # ...which is the hook

    def test_alex_proofs_come_from_his_fact_bank_part_one(self):
        bank = (config.PROFILES / "alex" / "fact_bank.md").read_text()
        part1, part2 = bank.split("## Part 2", 1)
        for kind in kinds.KINDS:
            out = outreach.compose("alex", ROW, JD, kind)
            self.check(kind, out, part1)
            self.assertTrue(out["note"].startswith("Hi [first name],"))       # nobody found yet: the person fills it
        self.assertTrue(all(text in part1 for _, text in outreach.proof_lines("alex")))

    def test_jordan_proofs_come_from_her_current_resume(self):
        resume = json.dumps(json.loads((config.PROFILES / "jordan" / "baseline.json").read_text()), ensure_ascii=False)
        self.assertFalse((config.PROFILES / "jordan" / "fact_bank.md").exists())
        for kind in kinds.KINDS:
            out = outreach.compose("jordan", ROW, JD, kind, {"name": "Jane Doe", "role": "Recruiter"})
            self.check(kind, out, resume)
            self.assertTrue(out["note"].startswith("Hi Jane,"))
            self.assertTrue(out["note"].endswith("Jordan"))

    def test_the_prompt_for_a_claude_chat_has_the_same_rules(self):
        out = outreach.compose("alex", ROW, JD, "manager", {"name": "Sam Lee", "role": "Head of AI"})
        for text in ("under 300 characters", "under 120 words", "subject line", "no flattery", "no em dashes",
                     kinds.ASKS["manager"], "Sam Lee", "Head of AI", "use only these facts and numbers",
                     "I applied for <role> at <company>", "Applied AI team"):
            self.assertIn(text, out["prompt"])
        self.assertIn(out["proof"]["text"], out["prompt"])

    def test_nothing_is_sent(self):
        source = open(outreach.__file__).read() + open(kinds.__file__).read()
        for word in ("smtplib", "urlopen", "sendmail", "net.request", "http.call"):
            self.assertNotIn(word, source)


class DraftForContactTest(TrackerCase):
    def test_a_saved_contacts_kind_picks_the_ask_and_is_shown(self):
        row = self.add(company="Acme Robotics", role_title="AI Engineer", jd=JD, status="applied")
        contacts.save(self.conn, row["id"], [{"name": "Kim Park", "role": "Staff Engineer", "kind": "peer",
                                              "source": "apollo", "found_at": "2026-10-18"},
                                             {"name": "Ana Ruiz", "role": "CTO", "source": "team_page"}])
        cs = trackapi.get(self.conn, "alex", row["id"])["contacts"]
        kim = outreach.draft(self.conn, "alex", row["id"], cs[0]["id"])
        self.assertEqual((kim["kind"], kim["contact"]["found_at"], kim["contact"]["source"]),
                         ("peer", "2026-10-18", "apollo"))
        self.assertIn(kinds.ASKS["peer"], kim["template"]["linkedin"])
        ana = outreach.draft(self.conn, "alex", row["id"], cs[1]["id"])
        self.assertEqual(ana["kind"], "manager")                               # a company leader: asked as a manager


class HunterForMissingKindsTest(TrackerCase):
    """Hunter runs last and only for the kinds still missing, filtered by department; cap and cache as before."""
    def setUp(self):
        super().setUp()
        self.row = self.add(company="Acme", role_title="ML Engineer", status="applied",
                            jd="Requirements:\n- Python and RAG.\nQuestions? Our recruiter, Jane Doe, can help.")
        self.keys = mock.patch.object(service.keys, "key", side_effect=lambda provider, pid: KEY)
        self.keys.start()
        self.account = mock.patch.object(hunter, "account", return_value={})
        self.account.start()

    def tearDown(self):
        self.account.stop()
        self.keys.stop()
        super().tearDown()

    def test_departments_cover_only_the_missing_kinds_and_the_answer_is_cached(self):
        calls = []

        def call(provider, method, url, headers, params=None, body=None):
            calls.append(params)
            return {"data": {"pattern": "{first}.{last}", "emails": [
                {"value": "sam.lee@acme.ai", "first_name": "Sam", "last_name": "Lee",
                 "position": "Engineering Manager, Machine Learning", "verification": {"status": "valid"}}]}}
        panel = service.panel(self.conn, "alex", self.row["id"], "acme.ai")
        self.assertEqual(panel["providers"]["hunter"]["estimate"]["credits"], 1)
        with mock.patch.object(http, "call", side_effect=call):
            out = service.lookup(self.conn, "alex", self.row["id"], "hunter", "acme.ai")
        self.assertEqual(calls[0]["department"], "it,management,research")         # the recruiter is in the JD
        self.assertEqual(out["spent"], 1)
        cards = {c["kind"]: c for c in out["panel"]["kinds"]}
        self.assertEqual(cards["manager"]["people"][0]["email"], "sam.lee@acme.ai")
        self.assertEqual(out["panel"]["missing"], ["peer"])
        self.assertEqual(out["panel"]["providers"]["hunter"]["estimate"]["departments"], "it,research")
        with mock.patch.object(http, "call", side_effect=AssertionError("no call")):
            again = service.panel(self.conn, "alex", self.row["id"], "acme.ai")
        self.assertEqual({c["kind"]: len(c["people"]) for c in again["kinds"]}, {"recruiter": 1, "manager": 1, "peer": 0})

    def test_the_daily_cap_stops_hunter_before_any_call(self):
        with mock.patch.object(ledger, "DAILY_CAP", 0), \
                mock.patch.object(http, "call", side_effect=AssertionError("no call")):
            with self.assertRaisesRegex(ValueError, "daily"):
                service.lookup(self.conn, "alex", self.row["id"], "hunter", "acme.ai")

    def test_nothing_missing_means_no_hunter_call(self):
        with mock.patch.object(service, "_missing", return_value=[]), \
                mock.patch.object(http, "call", side_effect=AssertionError("no call")):
            out = service.lookup(self.conn, "alex", self.row["id"], "hunter", "acme.ai")
        self.assertEqual(out["spent"], 0)
        self.assertIn("Hunter not needed", out["note"])


if __name__ == "__main__":
    unittest.main()
