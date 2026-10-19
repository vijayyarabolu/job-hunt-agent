"""GOAL25 §4 (user 2026-10-14 17:06): the nightly canary. Every page snapshot and every reported error page becomes a
regression fixture with nothing of the user's in it and an expected result; the newest open job of each board is a live page;
the canary waits for a running fetch, and runs from `autofill nightly`. The last
test drives the real extension on two saved fixtures (skipped without node / Chrome)."""
import shutil
import sqlite3
import tempfile
import unittest
import zlib
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from job_hunt_agent import config, db, store
from job_hunt_agent.ats.common import FetchResult, make_job
from job_hunt_agent.autofill import canary, health

from tests.test_goal23_autofill import AutofillCase
from tests.test_goal23_extension import CHROME

FORM = ('<form><input name="first" value="Zz Person"><input type="hidden" name="t" value="keep">'
        '<input type="radio" name="sp" value="No" checked><input type="checkbox" name="c" value="1" checked="checked">'
        '<select name="g"><option value="a" selected>A</option><option value="b">B</option></select>'
        '<textarea name="why">Because I like it</textarea></form>')


class BlankTest(unittest.TestCase):
    def test_a_fixture_keeps_the_form_but_nothing_typed_ticked_or_picked(self):
        out = canary.blank(FORM)
        self.assertNotIn("Zz Person", out)
        self.assertNotIn("Because I like it", out)
        self.assertNotIn("checked", out)
        self.assertNotIn("selected", out)
        self.assertIn('value="keep"', out)                 # hidden values and option values stay
        self.assertIn('value="No"', out)
        self.assertIn('<option value="a">A</option>', out)


class CanaryDataTest(AutofillCase):
    def setUp(self):
        super().setUp()
        self.data = Path(self.tmp.name) / "data"
        self.patches.append(mock.patch.object(config, "DATA", self.data))
        self.patches[-1].start()

    def snapshot(self, url, at, board="generic"):
        with self.conn:
            self.conn.execute("INSERT INTO autofill_snapshots(profile, url, board, html, at) VALUES (?,?,?,?,?)",
                              ("zztest", url, board, zlib.compress(FORM.encode()), at))

    def error(self, url, at, step="questions1"):
        health.record_error(self.conn, "zztest", {"board": "workday", "step": step, "kind": "error", "message": "boom",
                                                  "url": url, "html": FORM}, datetime.fromisoformat(at.replace("Z", "+00:00")))

    def test_snapshots_and_error_pages_become_blank_regression_fixtures_once(self):
        self.snapshot("https://zz.example.com/apply", "2026-10-14T09:00:00Z")
        self.error("https://zz.wd5.myworkdayjobs.com/x/apply?step=1", "2026-10-14T08:00:00Z")
        self.error("https://zz.wd5.myworkdayjobs.com/x/apply?step=2", "2026-10-14T10:00:00Z")   # the same page again
        got = canary.export(self.conn)
        self.assertEqual([(g["name"], g["board"], g["source"], g["expected"]) for g in got],
                         [("error-2", "workday", "regression", None), ("snapshot-1", "generic", "regression", None)])
        text = Path(got[1]["file"]).read_text()
        self.assertNotIn("Zz Person", text)
        self.assertEqual(len(canary.export(self.conn)), 2)                    # nothing new: nothing added
        self.assertEqual(sorted(p.name for p in canary.regression_dir().iterdir()),
                         ["error-2.html", "manifest.json", "snapshot-1.html"])

    def test_the_first_good_run_sets_the_expected_fills_and_a_better_one_raises_it(self):
        self.snapshot("https://zz.example.com/apply", "2026-10-14T09:00:00Z")
        canary.export(self.conn)
        canary._ratchet([{"name": "snapshot-1", "source": "regression", "fields": 10, "filled": 6}])
        self.assertEqual(canary.export(self.conn)[0]["expected"], 6)
        canary._ratchet([{"name": "snapshot-1", "source": "regression", "fields": 10, "filled": 4}])
        self.assertEqual(canary.export(self.conn)[0]["expected"], 6)          # a drop never lowers it
        canary._ratchet([{"name": "snapshot-1", "source": "regression", "fields": 10, "filled": 8}])
        self.assertEqual(canary.export(self.conn)[0]["expected"], 8)

    def test_only_the_newest_pages_are_kept(self):
        for i in range(canary.MAX_REGRESSION + 3):
            self.snapshot(f"https://zz.example.com/apply/{i}", f"2026-10-{1 + i // 24:02d}T{i % 24:02d}:00:00Z")
        got = canary.export(self.conn)
        self.assertEqual(len(got), canary.MAX_REGRESSION)
        self.assertEqual(len(list(canary.regression_dir().glob("*.html"))), canary.MAX_REGRESSION)

    def test_the_live_pages_are_the_newest_open_job_of_each_board(self):
        for ext_id, url in (("a1", "https://jobs.lever.co/zzcanary/a1"), ("a2", "https://jobs.lever.co/zzcanary/a2")):
            item = make_job(ats="lever", key="zzcanary", ext_id=ext_id, company="ZZ Canary Co", title="ML Engineer",
                            location="Foster City, CA", url=url, posted_at=db.iso(db.utcnow()), description="ML.")
            db.sync_boards(self.conn, [{"board": "lever:zzcanary", "ats": "lever", "key": "zzcanary", "company": "ZZ Canary Co"}])
            run = db.start_run(self.conn, "test", db.utcnow())
            store.write_board(self.conn, db.board(self.conn, "lever:zzcanary"), FetchResult([item], 200, n_raw=1), "ok", run,
                              db.utcnow(), [], 5)
        got = canary.live_pages(self.conn)
        self.assertEqual([(g["name"], g["board"], g["source"]) for g in got], [("lever-live", "lever", "live")])
        self.assertTrue(got[0]["url"].startswith("https://jobs.lever.co/zzcanary/"))

    def test_the_board_fixtures_exist_and_each_has_an_expected_fill(self):
        pages = canary.board_pages()
        self.assertEqual({p["board"] for p in pages} >= {"greenhouse", "lever", "ashby", "workable", "bamboohr", "workday"}, True)
        for p in pages:
            self.assertTrue(p["sim"] == "workday" if not p["file"] else Path(p["file"]).exists(), p["name"])
            self.assertGreater(p["expected"], 0, p["name"])


class NightlyTest(unittest.TestCase):
    def test_it_waits_while_a_fetch_runs(self):
        states, naps = iter([True, True, False]), []
        self.assertTrue(canary.wait_quiet(lambda: next(states), naps.append))
        self.assertEqual(naps, [canary.QUIET_POLL_S] * 2)


@unittest.skipUnless(shutil.which("node") and Path(CHROME).exists(), "needs node and Google Chrome")
class CanaryBrowserTest(AutofillCase):
    def test_the_extension_fills_saved_fixtures_and_a_drop_is_a_notice(self):
        data = Path(self.tmp.name) / "data"
        pages = [p for p in canary.board_pages() if p["name"] in ("jazzhr-fixture", "workday-sim")]
        pages[0] = {**pages[0], "expected": 99}                             # more than it can fill: a drop
        with mock.patch.object(config, "DATA", data), mock.patch.object(canary, "board_pages", lambda: pages):
            got = canary.run(self.conn, live=False)
        by = {p["name"]: p for p in got["pages"]}
        self.assertGreaterEqual(by["workday-sim"]["filled"], pages[1]["expected"], by)
        self.assertGreater(by["jazzhr-fixture"]["filled"], 0, by)
        self.assertEqual(len(got["drops"]), 1, got["drops"])
        self.assertIn("jazzhr", health.notices(self.conn)[0]["text"])
        self.assertEqual((config.DB_PATH.name, config.PROFILES.name), ("t.db", "profiles"))   # put back after the run
