"""career boards for the big data / AI filers job-hunt-agent does not fetch (no network: probes, robots-listed
Workday tenants, careers pages and the live check are stand-ins). Which employers are looked at, how a board is
found on the company's own careers page, which guessed boards are refused, what lands in companies.yaml and what an
unreadable careers page is reported as."""
import tempfile
from pathlib import Path
from unittest import mock

import yaml

from job_hunt_agent import config, db, discover, discover_seed, discover_verify
from job_hunt_agent.discover_verify import Verdict
from job_hunt_agent.h1b import boards, names

from tests.test_goal24_h1b import H1bCase

PAGE = """<html><a href="https://www.linkedin.com/company/acme">LinkedIn</a>
<a href="https://boards.greenhouse.io/acmerobotics">Open roles</a></html>"""
AVATURE = '<html><a href="https://acme.avature.net/careers/SearchJobs">Search jobs</a></html>'


def live(us60=4, name=None):
    return lambda ats, key, now, names: Verdict(9, us60, "2026-10-10", name or names(key), "list")


class BoardsCase(H1bCase):
    def setUp(self):
        super().setUp()
        self.cfg = tempfile.TemporaryDirectory()
        Path(self.cfg.name, "companies.yaml").write_text(yaml.safe_dump(
            {"companies": [{"name": "Acme", "ats": "lever", "token": "acme"}]}))
        self.more = [mock.patch.object(config, "CONFIG", Path(self.cfg.name)),
                     mock.patch.object(discover, "_discover_one", lambda *a, **k: []),
                     mock.patch.object(discover_seed, "tenant_sites", lambda tenant: []),
                     mock.patch.object(discover_verify, "record", lambda *a, **k: None)]
        for p in self.more:
            p.start()
        self.now = db.utcnow()

    def tearDown(self):
        for p in reversed(self.more):
            p.stop()
        self.cfg.cleanup()
        super().tearDown()

    def target(self, name):
        return next(t for t in boards.targets(self.conn, 50, 10) if t["name"].startswith(name))


class TargetsTest(BoardsCase):
    def test_unfetched_data_filers_biggest_first_then_cap_exempt(self):
        got = [t["name"] for t in boards.targets(self.conn, 50, 10)]
        self.assertEqual(got[0], "Acme Robotics, Inc.")             # 3 data / AI filings, no board for it yet
        self.assertIn("State University of Testing", got)
        self.assertNotIn("Globex LLC", got)                          # no data / AI filing
        with mock.patch.object(boards, "fetched_keys", lambda conn: {names.key("Acme Robotics")}):
            self.assertNotIn("Acme Robotics, Inc.", [t["name"] for t in boards.targets(self.conn, 50, 10)])
        self.assertEqual(boards.display_name("GOLDMAN SACHS & CO. LLC"), "Goldman Sachs")


class FindTest(BoardsCase):
    def test_a_board_linked_from_the_companys_own_careers_page(self):
        with mock.patch.object(boards, "get_text", lambda url: PAGE), \
                mock.patch.object(discover_verify, "check", live()):
            got = boards.find(self.conn, self.target("Acme"), self.now)
        self.assertEqual((got["how"], got["board"]["ats"], got["board"]["token"]),
                         ("careers page", "greenhouse", "acmerobotics"))
        added = boards.add([got["board"]])
        self.assertEqual(added, [{"name": "Acme Robotics", "ats": "greenhouse", "token": "acmerobotics"}])
        saved = yaml.safe_load(Path(self.cfg.name, "companies.yaml").read_text())["companies"]
        self.assertEqual(len(saved), 2)
        self.assertEqual(boards.add([got["board"]]), [])            # already there: not twice

    def test_aggregators_are_never_candidates(self):
        self.assertEqual(boards.links(PAGE, "https://acme.example/careers"), ["https://boards.greenhouse.io/acmerobotics"])

    def test_a_board_with_nothing_open_in_the_us_is_not_added(self):
        with mock.patch.object(boards, "get_text", lambda url: PAGE), \
                mock.patch.object(discover_verify, "check", live(us60=0)):
            got = boards.find(self.conn, self.target("Acme"), self.now)
        self.assertIsNone(got["board"])
        self.assertEqual(got["site"], "board with nothing open in the US")

    def test_a_guessed_workday_tenant_must_carry_the_companys_name(self):
        with mock.patch.object(discover_seed, "tenant_sites", lambda tenant: [f"{tenant}/wd5/External"]), \
                mock.patch.object(boards, "get_text", mock.Mock(side_effect=OSError("offline"))):
            with mock.patch.object(discover_verify, "check", live(name="Acme Plumbing Supply")):
                self.assertIsNone(boards.find(self.conn, self.target("Acme"), self.now)["board"])
            with mock.patch.object(discover_verify, "check", live(name="Acme Robotics")):
                got = boards.find(self.conn, self.target("Acme"), self.now)
        self.assertEqual((got["how"], got["board"]["wd"], got["board"]["site"]), ("workday tenant", "wd5", "External"))

    def test_an_unreadable_careers_page_is_named(self):
        with mock.patch.object(boards, "get_text", lambda url: AVATURE):
            got = boards.find(self.conn, self.target("Acme"), self.now)
        self.assertEqual((got["board"], got["site"]), (None, "Avature"))
