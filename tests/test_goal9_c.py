"""GOAL9 area C: the job detail pane. Backend: Find contacts keyed by a feed job's uid without a tracker row (panel /
lookup / add; the row is created as 'saved' only when a contact is saved, never on open, and a saved row never
reaches the Sheet). Browser: tests/ui/goal9_c.mjs in headless Chrome against a throwaway DB with Apollo / Hunter
mocked in-process (no network, no credits): the ⋯ menu closing rules, Find contacts one click from the pane and the
phone sheet, the cost asked before any paid request, hover labels on every control, every action optimistic."""
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import docx

from job_hunt_agent import config, db, fits, gsync, resume_upload, rgclient, server, store, trackapi
from job_hunt_agent.ats.common import FetchResult, make_job
from job_hunt_agent.finder import http, hunter, ledger, service
from tests.test_goal6_contacts import JD, KEY, PEOPLE, fake_apollo
from tests.test_goal7_hunter import ACCOUNT, emails
from tests.test_goal7_ui import CHROME
from tests.test_goal8_flow import PID as TESTER, PROFILE, TOKEN as RG_TOKEN, FakeResumegen
from tests.test_tracker_ui import TrackerCase

NOW = datetime.now(timezone.utc).replace(microsecond=0)
RUNNER = Path(__file__).parent / "ui" / "goal9_c.mjs"
SHOTS = Path(__file__).parents[1] / "docs" / "evidence" / "v9" / "c"
TOKEN = "ui9c-test-token"
RUN_TIMEOUT_S = 300
PID = "alex"
JOBS = (("Machine Learning Engineer", "- 3+ years of experience building ML systems in Python and PyTorch."),
        ("Senior Machine Learning Engineer", "- Build ML systems in Python, PyTorch and Kubernetes on AWS."),
        ("AI Engineer", "- 2+ years of experience with Python, LLMs and RAG systems in production."),
        ("ML Platform Engineer", "- Kubernetes, MLflow, Python and AWS for model serving at scale."),
        ("Applied AI Engineer", "- Python, LLM evaluation and RAG pipelines on AWS for production systems."))
no_call = mock.patch.object(http, "call", side_effect=AssertionError("no paid call"))


def seed_job(conn, ext_id: str = "1", title: str = "Machine Learning Engineer", description: str = JD) -> str:
    db.sync_boards(conn, [{"board": "lever:acme", "ats": "lever", "key": "acme", "company": "Acme"}])
    item = make_job(ats="lever", key="acme", ext_id=ext_id, company="Acme", title=title, location="Austin, TX",
                    url=f"https://jobs.lever.co/acme/{ext_id}", posted_at=db.iso(NOW), description=description)
    run = db.start_run(conn, "test", NOW)
    store.write_board(conn, db.board(conn, "lever:acme"), FetchResult([item], 200, n_raw=1), "ok", run, NOW,
                      ["fam:ml_engineer"], 5)
    return item["uid"]


class UidPanelTest(TrackerCase):
    """A feed job without a tracker row: the panel is free and creates nothing."""
    def setUp(self):
        super().setUp()
        self.conn.execute("DELETE FROM jobs")
        self.job = seed_job(self.conn)
        self.keys = mock.patch.object(service.keys, "key", side_effect=lambda p, pid: KEY if p == "apollo" else None)
        self.keys.start()

    def tearDown(self):
        self.keys.stop()
        super().tearDown()

    def rows(self) -> int:
        return self.conn.execute("SELECT count(*) FROM applications WHERE profile = ?", (PID,)).fetchone()[0]

    def test_panel_by_uid_is_free_and_creates_no_row(self):
        with no_call:
            panel = service.panel(self.conn, PID, None, uid=self.job)
        self.assertEqual((panel["app"], panel["uid"], panel["company"]), (None, self.job, "Acme"))
        self.assertEqual(panel["domain"], {"value": "acme.ai", "source": "link in the JD"})
        self.assertTrue(any(s["name"] == "Jane Doe" for s in panel["suggestions"]))     # named in the JD
        self.assertEqual(panel["providers"]["apollo"]["estimate"]["credits"], 0)        # the cost, before any click
        self.assertIn("0 credits (documented)", panel["providers"]["apollo"]["estimate"]["detail"])   # GOAL28 §5
        self.assertEqual(len(panel["links"]["linkedin"]), 4)
        self.assertEqual(panel["contacts"], [])
        self.assertEqual(self.rows(), 0)

    def test_an_unknown_uid_is_refused(self):
        with self.assertRaises(KeyError):
            service.panel(self.conn, PID, None, uid="lever:nope/1")
        with self.assertRaisesRegex(ValueError, "app or uid"):
            service.panel(self.conn, PID, None)

    def test_lookup_by_uid_spends_through_the_ledger_and_creates_no_row(self):
        calls = []
        with mock.patch.object(http, "call", side_effect=fake_apollo(calls)):
            out = service.lookup(self.conn, PID, None, "apollo", "acme.ai", uid=self.job)
        self.assertEqual(out["spent"], 0)                               # GOAL28: the documented search, 0 credits
        self.assertEqual(len(calls), 2)                                 # the manager and the peer (the JD names the recruiter)
        self.assertTrue(any(s["source"] == "apollo" for s in out["panel"]["suggestions"]))
        self.assertEqual(self.rows(), 0)
        self.assertEqual(ledger.usage(self.conn, PID)["used_today"], 0)
        with no_call:                                                   # the next open shows it free, from the cache
            again = service.panel(self.conn, PID, None, uid=self.job)
        self.assertEqual(again["providers"]["apollo"]["estimate"]["credits"], 0)
        self.assertEqual(again["domain"]["value"], "acme.ai")

    def test_hunter_lookup_by_uid(self):
        with mock.patch.object(service.keys, "key", return_value=KEY), \
                mock.patch.object(service.hunter.http, "call", return_value=emails(3)), \
                mock.patch.object(service.hunter, "account", return_value=None):
            out = service.lookup(self.conn, PID, None, "hunter", "acme.ai", uid=self.job)
        self.assertEqual(out["spent"], 1)
        self.assertEqual(len([s for s in out["panel"]["suggestions"] if s["source"] == "hunter"]), 3)
        self.assertEqual(self.rows(), 0)

    def test_add_by_uid_creates_a_saved_row_only_then_and_keeps_it_off_the_sheet(self):
        with no_call:
            row = service.add(self.conn, PID, None, {"name": "Jane Doe", "role": "Recruiter", "source": "jd"},
                              uid=self.job)
        self.assertEqual((row["status"], row["job_uid"], row["company"], row["source"]), ("saved", self.job, "Acme", "feed"))
        self.assertEqual(row["contacts"][0]["name"], "Jane Doe")
        self.assertEqual(row["target_name"], "Jane Doe")
        self.assertEqual(self.rows(), 1)
        events = [dict(r) for r in self.conn.execute("SELECT * FROM application_events WHERE app_id = ?", (row["id"],))]
        self.assertEqual([(e["to_status"], e["source"]) for e in events], [("saved", "ui")])
        self.assertEqual(gsync.eligible_new(self.conn, PID, {row["id"]: row}), [])        # saved: not a Sheet row
        self.assertIsNone(self.conn.execute("SELECT applied_on FROM applications WHERE id = ?", (row["id"],)).fetchone()[0])
        with no_call:                                                   # the second contact joins the same row
            again = service.add(self.conn, PID, None, {"name": "Sam Lee", "source": "manual"}, uid=self.job)
        self.assertEqual((again["id"], again["target_name"]), (row["id"], "Jane Doe, Sam Lee"))
        self.assertEqual(self.rows(), 1)
        with no_call:                                                   # the panel now carries the row
            panel = service.panel(self.conn, PID, None, uid=self.job)
        self.assertEqual((panel["app"], len(panel["contacts"])), (row["id"], 2))

    def test_a_uid_whose_row_exists_uses_the_row(self):
        row = self.add(job_uid=self.job, status="applied")
        with no_call:
            panel = service.panel(self.conn, PID, None, uid=self.job)
        self.assertEqual(panel["app"], row["id"])
        self.assertEqual(self.rows(), 1)

    def test_routes_take_a_uid(self):
        args = {"profile": PID, "uid": self.job}
        self.enterContext(mock.patch.object(config, "profile_ids", return_value=[PID]))   # no profiles dir in CI
        with no_call:
            panel = server.route(self.conn, "GET", "/api/contacts/panel", args, NOW)
        self.assertEqual((panel["app"], panel["uid"]), (None, self.job))
        self.assertEqual(self.rows(), 0)
        calls = []
        with mock.patch.object(http, "call", side_effect=fake_apollo(calls)):
            out = server.route(self.conn, "POST", "/api/contacts/lookup", {**args, "provider": "apollo", "domain": "acme.ai"}, NOW)
        self.assertEqual((out["spent"], self.rows()), (0, 0))
        with no_call:
            row = server.route(self.conn, "POST", "/api/contacts/add", {**args, "contact": {"name": "Jane Doe", "source": "jd"}}, NOW)
        self.assertEqual((row["status"], self.rows()), ("saved", 1))
        self.assertEqual(trackapi.get(self.conn, PID, row["id"])["contacts"][0]["name"], "Jane Doe")


def seed_feed(conn) -> None:
    """Five Acme jobs (the goal8 titles) whose JD names a recruiter and links acme.ai, so the free signals have
    something to show and Apollo / Hunter have a domain to be asked about."""
    db.sync_boards(conn, [{"board": "lever:acme", "ats": "lever", "key": "acme", "company": "Acme"}])
    run = db.start_run(conn, "test", NOW)
    items = [make_job(ats="lever", key="acme", ext_id=str(i), company="Acme", title=title, location="Austin, TX",
                      url=f"http://127.0.0.1:9/acme/{i}", posted_at=db.iso(NOW - timedelta(minutes=10 * (i + 1))),
                      description=f"{JD}\nRequirements:\n{line}\n" + "We ship ML products. " * 8)
             for i, (title, line) in enumerate(JOBS)]
    store.write_board(conn, db.board(conn, "lever:acme"), FetchResult(items, 200, n_raw=len(items)), "ok", run, NOW,
                      ["fam:ml_engineer", "fam:ai_engineer"], 5)
    fits.refresh(conn, config.profile(TESTER), NOW)         # scored: the pane shows a worth and the resume picker
    conn.commit()


def fake_providers(calls: list):
    """Apollo (goal6's fake: a manager, no peer, so Hunter is still asked) and Hunter (goal7's answers) in one
    http.call double; every call is recorded."""
    apollo = fake_apollo([], people={"people": [p for p in PEOPLE["people"] if "Engineer" not in p["title"]]})

    def call(provider, method, url, headers, params=None, body=None):
        calls.append((provider, url.rsplit("/", 1)[-1]))
        if provider == "apollo":
            return apollo(provider, method, url, headers, params, body)
        if url.endswith("/account"):
            return ACCOUNT
        if url.endswith("/domain-search"):
            return emails(3)
        raise AssertionError(url)
    return call


@unittest.skipUnless(shutil.which("node") and Path(CHROME).exists(), "needs node and Google Chrome")
class Goal9cBrowserTest(unittest.TestCase):
    """The detail pane in headless Chrome: tests/ui/goal9_c.mjs drives it, every browser check must pass."""
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        resumes = root / "profiles" / TESTER / "resumes"
        resumes.mkdir(parents=True)
        (root / "profiles" / TESTER / "profile.yaml").write_text(PROFILE.replace("families: [ml_engineer]",
                                                                                 "families: [ml_engineer, ai_engineer]"))
        (resumes / "Tess_ML.pdf").write_bytes(b"%PDF-1.4 base")
        doc = docx.Document()
        doc.add_paragraph("Tess Tester. ML Engineer. Python, PyTorch, Kubernetes, AWS. 2021 - 2025.")
        doc.save(resumes / "Tess_ML.docx")
        cls.rg = FakeResumegen()
        cls.calls: list = []
        hunter.ACCOUNT_CACHE.clear()
        made = {"name": "Tess_Tester_ML_Resume(Acme).pdf", "link": "https://drive.example/x", "sheet": "not on the Sheet yet"}
        cls.patches = [mock.patch.object(config, "DB_PATH", root / "ui.db"),
                       mock.patch.object(config, "PROFILES", root / "profiles"),
                       mock.patch.object(rgclient, "_base", lambda: cls.rg.url),
                       mock.patch.object(rgclient, "_token", lambda: RG_TOKEN),
                       mock.patch.object(resume_upload, "upload", return_value=made),
                       mock.patch.object(resume_upload, "link_existing", return_value=made),
                       mock.patch.object(service.keys, "key", return_value=KEY),      # both providers "configured"
                       mock.patch.object(http, "call", side_effect=fake_providers(cls.calls))]
        for p in cls.patches:
            p.start()
        conn = db.connect()
        seed_feed(conn)
        conn.close()
        cls.srv = server.make_server(lan=False, port=0)
        cls.srv.token = TOKEN
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        out = subprocess.run(["node", str(RUNNER), f"http://localhost:{cls.srv.server_port}", TOKEN, CHROME,
                              str(root / "chrome"), cls.rg.url, str(SHOTS) if os.environ.get("JH_SHOTS") else ""],
                             capture_output=True, text=True,
                             timeout=RUN_TIMEOUT_S)
        lines = [ln for ln in out.stdout.splitlines() if ln.startswith("{")]
        cls.checks = json.loads(lines[-1])["checks"] if lines else [{"name": "runner", "ok": False, "detail": out.stderr}]
        conn = db.connect()
        cls.rows = [dict(r) for r in conn.execute("SELECT id, job_uid, status, applied_on, source FROM applications "
                                                  "WHERE profile = ? ORDER BY id", (TESTER,))]
        cls.on_sheet = conn.execute("SELECT count(*) FROM gsync_rows").fetchone()[0]
        cls.eligible = gsync.eligible_new(conn, TESTER, {r["id"]: r for r in cls.rows})
        conn.close()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        for p in reversed(cls.patches):
            p.stop()
        cls.rg.stop()
        cls.tmp.cleanup()

    def assert_group(self, names):
        by_name = {c["name"]: c for c in self.checks}
        runner = by_name.get("runner")
        self.assertIsNone(runner, runner and runner["detail"])
        for name in names:
            with self.subTest(name):
                self.assertIn(name, by_name, "check did not run")
                self.assertTrue(by_name[name]["ok"], by_name[name]["detail"])

    def test_every_browser_check_passed(self):
        self.assert_group([c["name"] for c in self.checks])

    def test_the_more_menu_closes_on_pick_outside_esc_scroll_and_another_menu(self):
        self.assert_group(["⋯ closes on pick (Copy JD)", "⋯ closes on an outside click", "⋯ closes on Esc",
                           "⋯ closes when the pane scrolls", "⋯ closes when another menu opens",
                           "a second click on ⋯ closes it", "⋯ closes after Open the posting ↗ (new tab, back)",
                           "⋯ closes after Add a job by link and the panel opens",
                           "⋯ closes after Tracker row and the editor opens", "phone: ⋯ closes on pick",
                           "phone: ⋯ closes on an outside tap", "phone: ⋯ closes when the sheet scrolls"])

    def test_find_contacts_is_one_click_free_first_and_asks_before_spending(self):
        self.assert_group(["Find contacts is one click from the pane", "opening it sends no lookup",
                           "the free signals render", "Apollo and Hunter show their cost before any click",
                           "no tracker row was made by opening it", "Apollo asks first with the cost",
                           "nothing was sent before the answer", "Cancel sends nothing",
                           "OK sends exactly one lookup and shows Apollo's people", "still no tracker row after a lookup",
                           "Hunter asks first with the cost", "the tracker row is created as saved, not applied",
                           "the pane shows the new row without a reload", "phone: Find contacts is one tap away in the sheet",
                           "phone: opening it sends no lookup", "phone: Apollo asks first with the cost",
                           "phone: Cancel sends nothing"])
        paid = [c for c in self.calls if c[1] in ("api_search", "domain-search")]
        self.assertEqual(paid, [("apollo", "api_search"), ("apollo", "api_search"), ("hunter", "domain-search")])
        self.assertNotIn(KEY, json.dumps(self.checks))                          # a key never reaches the page

    def test_the_saved_row_from_a_contact_never_reaches_the_sheet(self):
        # job 0's row comes from Add to contacts; job 1's from Mark applied + Undo; job 2's from Apply ↗ (all saved)
        contact_row = [r for r in self.rows if r["job_uid"] == "lever:acme:0"]
        self.assertEqual(len(contact_row), 1, self.rows)
        self.assertEqual((contact_row[0]["status"], contact_row[0]["source"], contact_row[0]["applied_on"]),
                         ("saved", "feed", None))
        self.assertTrue(all(r["status"] == "saved" for r in self.rows), self.rows)
        self.assertEqual(self.on_sheet, 0)
        self.assertEqual(self.eligible, [])

    def test_every_pane_action_is_optimistic_and_fast(self):
        self.assert_group([c["name"] for c in self.checks if "first visible change" in c["name"]]
                          + ["Save marks the row at once", "a refused Like rolls back with a toast",
                             "the resume picker stays visible once the resume layer has loaded",
                             "Mark applied marks the row and the pane at once",
                             "the pane's Your application block appears without a reload", "Undo puts the pane back",
                             "Not interested drops the row and asks why", "Undo brings the row back",
                             "Restore drops it from Archived with a toast", "Apply ↗ remembers the pending apply",
                             "phone: Esc closes the sheet and focus returns to the list",
                             "phone: a swipe down closes the sheet"])

    def test_every_control_has_a_hover_label(self):
        self.assert_group([c["name"] for c in self.checks if c["name"].startswith(("labels:", "hover tip on"))]
                          + ["keyboard focus shows the tip", "phone: a long press shows the tip and release hides it"])


if __name__ == "__main__":
    unittest.main()
