"""company names exactly as the company writes them. The bad names below are the real ones the user listed
(and others the check found on 2026-10-08); names_fix.apply runs on a throwaway DB and lists folder."""
import csv
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

from job_hunt_agent import applied, company_names, config, db, history, names_fix, registry, seedlists, tracker
from job_hunt_agent.discover_verify import clean_name

NO_JDS = company_names.Evidence(Counter(), Counter())


class TidyAndSuspicious(unittest.TestCase):
    def test_legal_suffixes_go_but_names_stay_whole(self):
        cases = {"Chevron Corporation": "Chevron", "Blackstone Inc.": "Blackstone", "MONTICELLOAM, LLC": "MONTICELLOAM",
                 "Illumina, Inc.": "Illumina", "Graham Capital Management, L.P.": "Graham Capital Management",
                 "Guidewire Software, Inc. (US)": "Guidewire Software", "CLEAR - Corporate": "CLEAR",
                 "Bank of America": "Bank of America", "Brown Brothers Harriman & Co.": "Brown Brothers Harriman",
                 "Eli Lilly and Company": "Eli Lilly and Company", "impact.com": "impact.com", "goodr": "goodr"}
        for name, want in cases.items():
            with self.subTest(name=name):
                self.assertEqual(company_names.tidy(name), want)

    def test_the_users_bad_names_are_all_flagged(self):
        cases = {("sense", "6sense"): "lost leading digits", ("am", "affinipay1"): "cut from the slug",
                 ("paid", "hashtagpaid"): "cut from the slug", ("solidcore]", "solidcore"): "stray punctuation",
                 ("UNAVAILABLE", "usacareers-bridgenext.icims.com"): "junk",
                 ("GAP INTERNATIONAL", "gapinternational"): "all caps",
                 ("MONTICELLOAM, LLC", "monticelloam"): "legal suffix",
                 ("Chevron Corporation", "chevron/wd5/University"): "legal suffix",
                 ("House Buyers of", "housebuyersofamerica"): "cut off"}
        for (name, key), want in cases.items():
            with self.subTest(name=name):
                self.assertEqual(company_names.suspicious(name, key), want)
        self.assertEqual(company_names.suspicious("US01 VALEO", "valeo/wd3/Valeo_jobs", "workday"), "legal entity code")
        for name in ("Booz Allen Hamilton_United States", "Semtech_Corporation", "Synechron, Inc._USA Company"):
            self.assertEqual(company_names.suspicious(name, "x/wd1/X", "workday"), "underscore", name)

    def test_real_brands_are_left_alone(self):
        for name, key in [("OPSWAT", "opswat"), ("thatgamecompany", "thatgamecompany"), ("d-Matrix", "d-matrix"),
                          ("impact.com", "impact"), ("goodr", "goodr"), ("A24", "a24"), ("3M", "3m/wd1/Search"),
                          ("NVIDIA", "nvidia/wd5/NVIDIAExternalCareerSite")]:
            with self.subTest(name=name):
                self.assertIsNone(company_names.suspicious(name, key))
                self.assertEqual(company_names.propose(name, key, NO_JDS)[0], name)
        self.assertEqual(company_names.propose("Hex", "hex", NO_JDS)[0], "Hex", "a short slug-like name is looked at, kept")


class Evidence(unittest.TestCase):
    def test_here_at_and_is_seeking_name_the_company(self):
        # two ADP boards kept their tenant id although their JDs name the employer
        key = "244a96b2-61cf-4805-839d-558c7cbe4e3c/19000101_000001"
        for text, want in (("Here at DigiFlight we deliver mission software.", "DigiFlight"),
                           ("Science Systems and Applications, Inc. (SSAI) is seeking a Data Scientist.",
                            "Science Systems and Applications")):
            with self.subTest(want=want):
                ev = company_names.jd_evidence("244A96B2 61Cf 4805 839D 558C7Cbe4E3C", [text])
                self.assertEqual(company_names.propose("244A96B2 61Cf 4805 839D 558C7Cbe4E3C", key, ev, ats="adp")[0],
                                 want)
        ev = company_names.jd_evidence("x", ["We are seeking a Data Scientist.\nOur client is seeking an engineer."])
        self.assertEqual(ev.claims, Counter())                     # "We" / "Our client" are not names

    def test_the_jd_names_the_company(self):
        texts = ["About 6sense\n6sense is a B2B platform.", "At 8am, we build payments software for lawyers."]
        self.assertEqual(company_names.propose("sense", "6sense", company_names.jd_evidence("sense", texts))[0],
                         "6sense")
        self.assertEqual(company_names.propose("am", "affinipay1", company_names.jd_evidence("am", texts))[0], "8am")

    def test_styling_needs_real_uses_not_urls_headings_or_plain_words(self):
        jds = ["Apply at https://purestorage.com/careers or jobs@purestorage.com. Pure Storage builds flash.",
               "ABOUT RAMP\nRamp is a finance platform.", "Modal is great. Our modal dialogs are modal.",
               "insitro is a drug discovery company. At insitro, we combine ML and biology."]
        self.assertEqual(company_names.propose("Pure Storage", "purestorage",
                                               company_names.jd_evidence("Pure Storage", jds))[0], "Pure Storage")
        self.assertEqual(company_names.propose("Ramp", "ramp", company_names.jd_evidence("Ramp", jds))[0], "Ramp")
        self.assertEqual(company_names.propose("Modal", "modal", company_names.jd_evidence("Modal", jds))[0], "Modal")
        self.assertEqual(company_names.propose("Insitro", "insitro", company_names.jd_evidence("Insitro", jds))[0],
                         "insitro")

    def test_never_all_caps_unless_the_company_writes_it(self):
        self.assertEqual(company_names.propose("GAP INTERNATIONAL", "gapinternational", NO_JDS, "Gap International")[0],
                         "Gap International")
        self.assertEqual(company_names.propose("RUE GILT GROUPE", "ruelala", NO_JDS)[0], "Rue Gilt Groupe")
        jd = company_names.jd_evidence("WHOOP", ["At WHOOP, we build wearables. WHOOP members love it."])
        self.assertEqual(company_names.propose("Whoop", "whoop", jd)[0], "WHOOP")

    def test_acronyms_stay_when_caps_are_lowered(self):
        # the plan had "IKG USA" -> "Ikg Usa", "CS&L CPAS PA" -> "CS&L Cpas PA" (the docstring promised USA stays)
        for name, want in {"WEG TRANSFORMERS USA": "Weg Transformers USA", "CS&L CPAS PA": "CS&L CPAS PA",
                           "EURO USA": "Euro USA", "NYC BAGEL DELI": "NYC Bagel Deli",
                           "GREAT WESTERN DINING": "Great Western Dining", "AHP - NEVADA": "AHP - Nevada",
                           "IKG USA": "IKG USA", "THE NEW DAY PET INN": "The New Day Pet Inn",
                           "ALL OUT AIR": "All Out Air", "MSO PRO SERVICES": "MSO Pro Services",
                           "IFF (CDFI)": "IFF (CDFI)", "AAAG-NEVADA": "Aaag-Nevada"}.items():
            with self.subTest(name=name):
                self.assertEqual(company_names.propose(name, "x", NO_JDS)[0], want)


class Pins(unittest.TestCase):
    BRANDS = {"Zone & Co", "Rothschild & Co"}               # the company's own name ends in "& Co"

    def test_pinned_names_keep_no_legal_suffix(self):
        # GOAL10's enterprise pins showed "PG&E Corporation", "AAR Corp", "Paramount Skydance Corporation" …
        path = config.SHIPPED / "lists" / "board_names.csv"
        with open(path, newline="") as handle:
            bad = [r["name"] for r in csv.DictReader(handle)
                   if company_names.tidy(r["name"]) != r["name"] and r["name"] not in self.BRANDS]
        self.assertEqual(bad, [])


class IdNames(unittest.TestCase):
    """~2,950 ADP / Paylocity boards from GOAL16's discovery were named by their tenant GUID, and their jobs
    showed "6Ea91Ea2 804F 462B 9Fd7 63453B2902Ab" as the company in the feed."""
    ID = "6Ea91Ea2 804F 462B 9Fd7 63453B2902Ab"
    KEY = "6ea91ea2-804f-462b-9fd7-63453b2902ab"

    def test_an_id_is_not_a_name(self):
        for name in (self.ID, "454C7D2D B7A3 4771 8631 C441B6D82034", "000c0490-cc55-4888-9ce3-84bc24843c60"):
            with self.subTest(name=name):
                self.assertEqual(company_names.suspicious(name, self.KEY, "paylocity"), company_names.ID_NAME)
        self.assertIsNone(company_names.suspicious("Interclypse", self.KEY, "paylocity"))

    def test_the_jd_or_the_ats_names_it(self):
        jds = ["Interclypse is seeking Data Scientists. At Interclypse, we grow engineers.",
               "Join Interclypse and build data products."]
        got = company_names.propose(self.ID, self.KEY, company_names.jd_evidence(self.ID, jds), ats="paylocity")
        self.assertEqual(got[0], "Interclypse")
        self.assertEqual(company_names.propose(self.ID, self.KEY, NO_JDS, "Interclypse, Inc.", "paylocity")[0],
                         "Interclypse")
        adp = company_names.jd_evidence("x", ["WHO WE ARE\n\nAt Greenleaf Trust N.A., our teams are dedicated."])
        self.assertEqual(company_names.propose("454C7D2D B7A3 4771 8631 C441B6D82034", "454c7d2d-b7a3/1", adp,
                                               ats="adp")[0], "Greenleaf Trust N.A.")
        # a Paylocity page title in capitals is still the employer's name
        self.assertEqual(company_names.propose(self.ID, self.KEY, NO_JDS, "CHRIST PANOS FOOD", "paylocity")[0],
                         "Christ Panos Food")

    def test_no_evidence_asks_for_a_name_and_never_recases_the_id(self):
        name, why = company_names.propose(self.ID, self.KEY, NO_JDS, ats="paylocity")
        self.assertEqual(name, self.ID)
        self.assertTrue(why.startswith("needs a name"), why)


class DiscoveryName(unittest.TestCase):
    def test_clean_name_keeps_leading_digits_and_brackets(self):
        cases = {"6sense": "6sense", "8am": "8am", "#paid": "#paid", "[solidcore]": "[solidcore]",
                 "2100 NVIDIA USA": "NVIDIA", "Bank of America": "Bank of America", "OpenAI Jobs": "OpenAI",
                 "House Buyers of America": "House Buyers of America", "Wolters Kluwer United States Inc.":
                 "Wolters Kluwer"}
        for raw, want in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(clean_name(raw), want)


class ApplyOnAThrowawayDb(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "config" / "lists").mkdir(parents=True)
        (root / "config" / "lists" / "board_names.csv").write_text("ats,key,name\n")
        self.patches = [mock.patch.object(config, "DB_PATH", root / "t.db"),
                        mock.patch.object(config, "CONFIG", root / "config")]
        for p in self.patches:
            p.start()
        seedlists._board_names.cache_clear()
        applied._renamed.cache_clear()
        self.conn = db.connect()
        self.boards = [{"name": "sense", "ats": "greenhouse", "token": "6sense"},
                       {"name": "Acme", "ats": "greenhouse", "token": "acme"}]
        with mock.patch.object(registry, "boards", lambda: [registry._board(b, "cc") for b in self.boards]):
            db.sync_boards(self.conn, registry.boards())
        for n, board in enumerate(("greenhouse:6sense", "greenhouse:6sense", "greenhouse:acme")):
            self.conn.execute("INSERT INTO jobs(uid, board, ext_id, company, title, precision, first_seen, last_seen, "
                              "content_hash, repost_key) VALUES (?,?,?,?,?,?,?,?,'h','k')", (f"{board}:{n}", board, str(n), board.endswith("acme")
                                                           and "Acme" or "sense", "ML Engineer", "exact",
                                                           "2026-10-01T00:00:00Z", "2026-10-08T00:00:00Z"))
        self.conn.execute("INSERT INTO profiles(id, name) VALUES ('p', 'P')")
        self.apps = [self._app("greenhouse:6sense:0", "sense"), self._app("greenhouse:6sense:1", "6Sense Inc")]

    def _app(self, uid: str, company: str) -> int:
        cur = self.conn.execute("INSERT INTO applications(profile, company, role_title, source, job_uid, status) "
                                "VALUES ('p', ?, 'ML Engineer', 'feed', ?, 'applied')", (company, uid))
        return cur.lastrowid

    def tearDown(self):
        self.conn.close()
        for p in reversed(self.patches):
            p.stop()
        seedlists._board_names.cache_clear()
        applied._renamed.cache_clear()
        self.tmp.cleanup()

    def test_apply_pins_renames_and_keeps_typed_rows(self):
        changes = [{"board": "greenhouse:6sense", "ats": "greenhouse", "key": "6sense", "old": "sense",
                    "new": "6sense", "why": "lost leading digits: ATS says '6sense'"}]
        with mock.patch.object(registry, "boards", lambda: [registry._board(b, "cc") for b in self.boards]):
            result = names_fix.apply(self.conn, changes)
        self.assertEqual((result["boards"], result["jobs"]), (1, 2))
        pins = list(csv.DictReader(open(Path(self.tmp.name) / "config" / "lists" / "board_names.csv")))
        self.assertEqual(pins, [{"ats": "greenhouse", "key": "6sense", "name": "6sense"}])
        companies = {r[0] for r in self.conn.execute("SELECT company FROM jobs WHERE board = 'greenhouse:6sense'")}
        self.assertEqual(companies, {"6sense"})
        rows = dict(self.conn.execute("SELECT id, company FROM applications").fetchall())
        self.assertEqual(rows[self.apps[0]], "6sense", "the app wrote 'sense': it follows the board")
        self.assertEqual(rows[self.apps[1]], "6Sense Inc", "typed by a person: left alone")
        self.assertEqual(applied.company_key("sense"), applied.company_key("6sense"), "old rows still match")

    def test_a_row_a_person_edited_is_left_alone(self):
        self.conn.execute("UPDATE applications SET company = 'Sense' WHERE id = ?", (self.apps[0],))
        with history.action("Alex"):                     # a person typed the board's old name back in
            tracker.edit_field(self.conn, self.apps[0], "company", "sense")
        changes = [{"board": "greenhouse:6sense", "ats": "greenhouse", "key": "6sense", "old": "sense",
                    "new": "6sense", "why": "pinned"}]
        with mock.patch.object(registry, "boards", lambda: [registry._board(b, "cc") for b in self.boards]):
            names_fix.apply(self.conn, changes)
        self.assertEqual(self.conn.execute("SELECT company FROM applications WHERE id = ?",
                                           (self.apps[0],)).fetchone()[0], "sense")

    def test_jobs_a_fetch_wrote_during_a_rename_are_found_and_follow_the_board(self):
        # a fetch running while Booz Allen was renamed wrote the old name on 378 jobs
        self.conn.execute("UPDATE jobs SET company = 'Acme_United States' WHERE board = 'greenhouse:acme'")
        self.assertEqual(names_fix.drifted(self.conn),
                         [{"board": "greenhouse:acme", "board_name": "Acme", "job_name": "Acme_United States", "jobs": 1}])
        with mock.patch.object(registry, "boards", lambda: [registry._board(b, "cc") for b in self.boards]):
            result = names_fix.apply(self.conn, [])
        self.assertEqual(result["drifted_jobs"], 1)
        self.assertEqual(names_fix.drifted(self.conn), [])

    def test_an_id_named_board_with_matched_jobs_takes_its_name_after_a_fetch(self):
        # GOAL16's Paylocity / ADP boards named by a tenant GUID showed "31Dd0607 5Fe1 …" cards
        guid, quiet = "31dd0607-5fe1-43cc-a916-35084f250a54", "d887949e-3f67-43b9-9f6b-3371b3f9450e"
        self.boards += [{"name": guid, "ats": "paylocity", "token": guid},
                        {"name": quiet, "ats": "paylocity", "token": quiet}]
        rows = [(f"paylocity:{guid}", "1", ",data_scientist,"), (f"paylocity:{quiet}", "2", ",")]
        page = "<title>Tanaq Technical Services LLC - Job Opportunities</title>"
        with mock.patch.object(registry, "boards", lambda: [registry._board(b, "cc") for b in self.boards]), \
                mock.patch.object(names_fix, "get_text", return_value=page) as get:
            db.sync_boards(self.conn, registry.boards())
            for board, ext, families in rows:
                self.conn.execute("INSERT INTO jobs(uid, board, ext_id, company, title, families, precision, first_seen, "
                                  "last_seen, content_hash, repost_key) VALUES (?,?,?,?,'Data Scientist',?,'exact',"
                                  "'2026-10-09T05:00:00Z','2026-10-09T05:00:00Z','h','k')",
                                  (f"{board}:{ext}", board, ext, company_names.tidy(board.split(":")[1]), families))
            result = names_fix.learn(self.conn)
            db.sync_boards(self.conn, registry.boards())                 # the next run's registry sync keeps it
        self.assertEqual(result, {"boards": 1, "jobs": 1})
        self.assertEqual(get.call_count, 1, "a board with no matched job is not looked up")
        names = dict(self.conn.execute("SELECT board, company FROM boards WHERE ats = 'paylocity'").fetchall())
        self.assertEqual(names[f"paylocity:{guid}"], "Tanaq Technical Services")
        self.assertEqual(self.conn.execute("SELECT company FROM jobs WHERE board = ?",
                                           (f"paylocity:{guid}",)).fetchone()[0], "Tanaq Technical Services")
        learned = list(csv.DictReader(open(Path(self.tmp.name) / "board_names.learned.csv")))
        self.assertEqual(learned, [{"ats": "paylocity", "key": guid, "name": "Tanaq Technical Services"}])
        self.assertEqual((Path(self.tmp.name) / "config" / "lists" / "board_names.csv").read_text(), "ats,key,name\n",
                         "the tracked pins are left to people")


if __name__ == "__main__":
    unittest.main()
