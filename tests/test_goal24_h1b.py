"""GOAL24 §1/§2: the public H-1B / PERM filings (synthetic DOL files in a temp dir, a throwaway DB): name matching,
data / AI roles, cap-exempt rules, which files make the last 8 quarters, the load (certified H-1B only, each case
once, franchise trade names ignored), the company statuses, the per-job badge (the JD's words win, learned
auto-rejects, a big filer's quick rejection overruled) and the monthly refresh plan."""
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

import openpyxl

from job_hunt_agent import config, db, signals, tracker
from job_hunt_agent.h1b import label, load, names, refresh, xlsx

from tests.test_goal8_flow import PID, Goal8Case

LCA_HEAD = ["CASE_NUMBER", "CASE_STATUS", "RECEIVED_DATE", "DECISION_DATE", "VISA_CLASS", "JOB_TITLE", "SOC_CODE",
            "EMPLOYER_NAME", "TRADE_NAME_DBA", "EMPLOYER_FEIN", "NAICS_CODE", "EMPLOYER_STATE", "EMPLOYER_POC_EMAIL",
            "WORKSITE_STATE", "PW_WAGE_LEVEL", "EXTRA_COLUMN"]
PERM_HEAD = ["CASE_NUMBER", "CASE_STATUS", "DECISION_DATE", "EMP_BUSINESS_NAME", "EMP_FEIN", "EMP_NAICS", "JOB_TITLE",
             "PWD_SOC_CODE", "PRIMARY_WORKSITE_STATE", "EMP_POC_EMAIL"]
PROFILE = """name: Tess Tester
families: [ml_engineer]
years: 4
needs_sponsorship: true
"""
RECENT, OLD = date(2026, 3, 2), date(2025, 1, 15)


def serial(day: date) -> int:
    return (day - xlsx.EXCEL_EPOCH).days


def lca(case, name, when, title="Data Scientist", soc="15-2051.00", status="Certified", visa="H-1B", fein="",
        dba="", naics="541511", level="II", state="TX", email="hr@acme.example"):
    return [case, status, serial(when), serial(when), visa, title, soc, name, dba, fein, naics, "TX", email, state,
            level, "ignored"]


def write_book(path: Path, head: list, rows: list, blank_tail: int = 3) -> None:
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(head)
    for r in rows:
        sheet.append(r)
    for _ in range(blank_tail):                    # DOL's files end with formatted, empty rows
        sheet.append([None] * len(head))
    book.save(path)


LCA_ROWS = [lca("I-1", "Acme Robotics, Inc.", RECENT, fein="11-1111111", level="II"),
            lca("I-2", "ACME ROBOTICS INC", RECENT, title="ML Engineer", soc="15-1252.00", fein="11-1111111",
                level="III", state="CA"),
            lca("I-3", "Acme Robotics, Inc.", RECENT, fein="11-1111111", level="II"),
            lca("I-4", "Globex LLC", RECENT, title="Accountant", soc="13-2011.00", fein="22-2222222"),
            lca("I-5", "Globex LLC", RECENT, title="Accountant", soc="13-2011.00", fein="22-2222222"),
            lca("I-6", "Globex LLC", RECENT, title="Accountant", soc="13-2011.00", fein="22-2222222"),
            lca("I-7", "Initech", OLD, fein="33-3333333"),
            lca("I-8", "Oldco", RECENT, title="Chef", soc="35-1011.00", fein="44-4444444"),
            lca("I-9", "State University of Testing", RECENT, naics="611310", fein="55-5555555"),
            lca("I-10", "Denied Corp", RECENT, status="Denied", fein="66-6666666"),
            lca("I-11", "Aussie Co", RECENT, visa="E-3 Australian", fein="77-7777777"),
            lca("I-12", "Ancient Co", date(2024, 2, 1), fein="88-8888888"),
            *(lca(f"I-1{3 + i}", f"Franchise {n} LLC", RECENT, dba="Burger Barn", fein=f"91-000000{i}", title="Cook",
                  soc="35-2014.00") for i, n in enumerate(("One", "Two", "Three", "Four"))),
            lca("I-17", "Holdco 9 LLC", RECENT, dba="Brightdata", fein="92-0000001")]


class NamesTest(unittest.TestCase):
    def test_a_filing_name_and_a_board_name_get_one_key(self):
        same = [("Amazon.com Services LLC", "Amazon"), ("Meta Platforms, Inc.", "Meta"), ("Google LLC", "Google"),
                ("Capital One, National Association", "Capital One"), ("Wells Fargo Bank, N.A.", "Wells Fargo"),
                ("Visa U.S.A. Inc.", "Visa"), ("Amazon Web Services, Inc.", "Amazon"),
                ("Bank of America, N.A.", "Bank of America"), ("Anduril Industries", "Anduril"),
                ("XYZ Holdings LLC dba Acme", "XYZ Holdings"), ("Data Bricks", "Databricks")]
        for filing, board in same:
            with self.subTest(filing):
                self.assertEqual(names.key(filing), names.key(board))
        apart = [("Tesla Laboratories", "Tesla"), ("Cohere Health", "Cohere"), ("Bank of America", "Bank"),
                 ("Accenture Federal Services", "Accenture"), ("First National Bank of America", "Bank of America")]
        for a, b in apart:
            with self.subTest(a):
                self.assertNotEqual(names.key(a), names.key(b))

    def test_a_dba_names_a_company_unless_it_is_filler(self):
        self.assertEqual(names.split_dba("XYZ Holdings, LLC d/b/a Acme Robotics"), ("XYZ Holdings, LLC", "Acme Robotics"))
        for junk in ("N/A", "Not applicable", "n / a", "Same", "1199 Coleman Ave", "(an Abbott Laboratories company)", "-"):
            with self.subTest(junk):
                self.assertFalse(names.real_dba(junk))
        self.assertTrue(names.real_dba("Fidelity Investments"))

    def test_data_roles_by_soc_or_title(self):
        self.assertTrue(names.is_data_role("15-2051.00", "Analyst III"))
        self.assertTrue(names.is_data_role("15-1252.00", "Machine Learning Engineer"))
        self.assertTrue(names.is_data_role("15-1252.00", "Senior Data Engineer"))
        self.assertTrue(names.is_data_role("15-1252.00", "AI Engineer"))
        self.assertFalse(names.is_data_role("15-1252.00", "Software Developer"))
        self.assertFalse(names.is_data_role("29-1141.00", "Registered Nurse"))
        self.assertFalse(names.is_data_role("47-2111.00", "Maintenance Electrician"))

    def test_cap_exempt_rules(self):
        self.assertEqual(names.cap_exempt("The Leland Stanford, Jr University", "611310"), "university")
        self.assertEqual(names.cap_exempt("Board of Regents of the State", "611310"), "university")
        self.assertIsNone(names.cap_exempt("College Board", "611710"))
        self.assertEqual(names.cap_exempt("Lawrence Livermore National Security, LLC", "541715"), "government lab")
        self.assertEqual(names.cap_exempt("The Broad Institute, Inc.", "541715"), "research")
        self.assertIsNone(names.cap_exempt("Broad Institute Consulting", "541511"))
        self.assertEqual(names.cap_exempt("University Hospitals Health System", "622110"), "university")
        self.assertEqual(names.cap_exempt("Children's Hospital of Philadelphia", "622110"), "academic hospital")
        self.assertIsNone(names.cap_exempt("Acme Hospital Staffing", "622110"))


class FilesTest(unittest.TestCase):
    def test_the_window_is_the_last_8_quarters_of_the_newest_lca_file(self):
        base = "https://www.dol.gov/x/"
        found = [base + n for n in ("LCA_Disclosure_Data_FY2024_Q3.xlsx", "LCA_Disclosure_Data_FY2024_Q4.xlsx",
                                    "LCA_Disclosure_Data_FY2025_Q1.xlsx", "LCA_Disclosure_Data_FY2025_Q4.xlsx",
                                    "LCA_Disclosure_Data_FY2026_Q3.xlsx", "PERM_Disclosure_Data_FY2023_Q4.xlsx",
                                    "PERM_Disclosure_Data_FY2024_Q4.xlsx", "PERM_Disclosure_Data_New_Form_FY2024_Q4.xlsx",
                                    "PERM_Disclosure_Data_FY2026_Q3.xlsx", "H-2A_Disclosure_Data_FY2026_Q3.xlsx")]
        picked, since = load.wanted(found)
        self.assertEqual(since, date(2024, 7, 1))           # FY2024 Q4 = Jul-Sep 2024
        self.assertEqual(sorted(u.rsplit("/", 1)[-1] for u in picked),
                         ["LCA_Disclosure_Data_FY2024_Q4.xlsx", "LCA_Disclosure_Data_FY2025_Q1.xlsx",
                          "LCA_Disclosure_Data_FY2025_Q4.xlsx", "LCA_Disclosure_Data_FY2026_Q3.xlsx",
                          "PERM_Disclosure_Data_FY2024_Q4.xlsx", "PERM_Disclosure_Data_FY2026_Q3.xlsx",
                          "PERM_Disclosure_Data_New_Form_FY2024_Q4.xlsx"])

    def test_refresh_plan_downloads_new_files_and_drops_old_ones(self):
        found = ["https://d/LCA_Disclosure_Data_FY2026_Q4.xlsx", "https://d/LCA_Disclosure_Data_FY2025_Q1.xlsx"]
        have = {"LCA_Disclosure_Data_FY2025_Q1.xlsx", "LCA_Disclosure_Data_FY2024_Q1.xlsx", "notes.xlsx"}
        todo = refresh.plan(found, have, have)
        self.assertEqual(todo["new"], ["https://d/LCA_Disclosure_Data_FY2026_Q4.xlsx"])
        self.assertEqual(todo["remove"], ["LCA_Disclosure_Data_FY2024_Q1.xlsx"])      # never a file it did not name
        self.assertTrue(todo["load"])
        same = {"LCA_Disclosure_Data_FY2026_Q4.xlsx", "LCA_Disclosure_Data_FY2025_Q1.xlsx"}
        self.assertFalse(refresh.plan(found, same, same)["load"])                    # nothing new: nothing to do

    def test_excel_dates_and_text_dates(self):
        self.assertEqual(xlsx.to_date("45838"), date(2025, 6, 30))
        self.assertEqual(xlsx.to_date("2025-06-30"), date(2025, 6, 30))
        self.assertEqual(xlsx.to_date("6/30/2025"), date(2025, 6, 30))
        self.assertIsNone(xlsx.to_date("soon"))


class H1bCase(Goal8Case):
    """A temp DB with the synthetic filings loaded; the test person needs sponsorship."""

    def setUp(self):
        super().setUp()
        (config.PROFILES / PID / "profile.yaml").write_text(PROFILE)
        self.files = tempfile.TemporaryDirectory()
        folder = Path(self.files.name)
        write_book(folder / "LCA_Disclosure_Data_FY2026_Q3.xlsx", LCA_HEAD, LCA_ROWS)
        write_book(folder / "LCA_Disclosure_Data_FY2025_Q2.xlsx", LCA_HEAD,
                   [lca("I-1", "Acme Robotics, Inc.", RECENT, fein="11-1111111"),        # the same case again
                    lca("I-7b", "Initech", OLD, title="Data Analyst", fein="33-3333333")])
        write_book(folder / "PERM_Disclosure_Data_FY2026_Q3.xlsx", PERM_HEAD,
                   [["G-1", "Certified", serial(RECENT), "Greencard Only Inc", "99-9999999", "541511", "Data Scientist",
                     "15-2051", "WA", "hr@greencard.example"],
                    ["G-2", "Certified", serial(RECENT), "Acme Robotics, Inc.", "11-1111111", "541511",
                     "Engineer", "17-2071", "TX", "hr@acme.example"]])
        self.result = load.load_dir(self.conn, folder, log=lambda _m: None)
        label.forget()

    def tearDown(self):
        self.files.cleanup()
        label.forget()
        super().tearDown()

    def badge(self, company, sponsorship=None, dealbreaker=None):
        return label.badge({"company": company, "sponsorship": sponsorship}, label.index(self.conn), dealbreaker)

    def reject(self, company, applied_on, rejected_on):
        tracker.ensure_profile(self.conn, PID)
        with db.tx(self.conn):
            app = self.conn.execute("INSERT INTO applications(profile, company, role_title, applied_on, status) "
                                    "VALUES (?, ?, 'ML Engineer', ?, 'rejected')", (PID, company, applied_on)).lastrowid
            self.conn.execute("INSERT INTO mail_messages(profile, msg_id, sent_at, kind, method, state, app_id, read_at) "
                              "VALUES (?, ?, ?, 'rejected', 'rule', 'applied', ?, ?)",
                              (PID, f"m{app}", f"{rejected_on}T15:00:00Z", app, f"{rejected_on}T16:00:00Z"))
        label.forget()


class LoadTest(H1bCase):
    def company(self, name):
        return self.conn.execute("SELECT * FROM h1b_companies WHERE key = ?", (names.key(name),)).fetchone()

    def test_statuses_from_the_filings(self):
        self.assertEqual(self.result["ref"], "2026-03-02")
        acme = self.company("Acme Robotics")
        self.assertEqual((acme["status"], acme["recent_all"], acme["recent_data"]), ("sponsors", 3, 3))  # I-1 once
        self.assertEqual((acme["perm_all"], acme["level"]), (1, "II"))
        self.assertEqual(json.loads(acme["states"])[:2], ["TX", "CA"])
        self.assertEqual(acme["domain"], "acme.example")
        self.assertEqual(self.company("Globex")["status"], "sponsors")          # 3 of any role
        initech = self.company("Initech")
        self.assertEqual((initech["status"], initech["recent_all"], initech["prior_all"]), ("maybe", 0, 2))
        self.assertEqual(self.company("Oldco")["status"], "maybe")              # 1 unrelated filing
        self.assertEqual(self.company("State University of Testing")["status"], "cap_exempt")
        self.assertEqual(self.company("Greencard Only")["status"], "maybe")     # green cards only
        for nobody in ("Denied Corp", "Aussie Co", "Ancient Co"):               # denied, E-3, before the window
            self.assertIsNone(self.company(nobody), nobody)

    def test_a_franchise_brand_is_not_credited_but_a_real_trade_name_is(self):
        self.assertIsNone(self.company("Burger Barn"))                          # 4 franchisees file under it
        self.assertEqual(self.company("Brightdata")["status"], "sponsors")      # its one data filing
        self.assertEqual(self.company("Holdco 9")["status"], "sponsors")

    def test_loading_again_gives_the_same_tables(self):
        before = tuple(self.conn.execute("SELECT count(*), sum(recent_all) FROM h1b_companies").fetchone())
        again = load.load_dir(self.conn, Path(self.files.name), log=lambda _m: None)
        self.assertEqual(before, tuple(self.conn.execute("SELECT count(*), sum(recent_all) FROM h1b_companies").fetchone()))
        self.assertEqual(again["employers"], self.result["employers"])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM h1b_files").fetchone()[0], 3)

    def test_no_worker_data_is_kept(self):
        dump = "\n".join(str(tuple(r)) for t in ("h1b_employers", "h1b_months", "h1b_companies")
                         for r in self.conn.execute(f"SELECT * FROM {t}"))
        self.assertNotIn("hr@", dump)                                          # only the email's domain


class BadgeTest(H1bCase):
    def test_badges_from_the_filings(self):
        b = self.badge("Acme Robotics")
        self.assertEqual((b.kind, b.text, b.source), ("sponsors", "Sponsors · 3 in 12 mo", "filings"))
        self.assertIn("3 H-1B filings in the last 12 months (3 data / AI)", b.why)
        self.assertIn("typical wage level II", b.why)
        self.assertEqual(self.badge("State University of Testing").text, "Cap-exempt")
        self.assertEqual(self.badge("Initech").text, "Sponsored before")
        self.assertIsNone(self.badge("Nobody Filed Inc"))                       # Unknown: no badge
        self.assertEqual([label.tier(self.badge(c)) for c in ("Acme Robotics", "Nobody", "Initech")], [0, 1, 2])

    def test_the_jd_wins_over_the_history(self):
        self.assertEqual(self.badge("Acme Robotics", "no").text, "No sponsorship")
        self.assertEqual(self.badge("Acme Robotics", "citizen").text, "Citizens only")
        self.assertEqual(self.badge("Acme Robotics", None, r"\bTS\s*/\s*SCI\b").text, "Citizens only")
        yes = self.badge("Nobody Filed Inc", "yes")
        self.assertEqual((yes.kind, yes.text), ("sponsors", "Sponsors"))
        self.assertEqual(label.tier(self.badge("Acme Robotics", "no")), 3)
        self.assertEqual(label.group(self.badge("Acme Robotics", "no")), "no")
        self.assertEqual(label.group(self.badge("Initech")), "friendly")
        self.assertEqual(label.group(None), "unknown")

    def test_a_quick_rejection_teaches_no_sponsorship(self):
        self.reject("Initech", "2026-10-08", "2026-10-10")
        b = self.badge("Initech")
        self.assertEqual((b.kind, b.text, b.source), ("no", "No sponsorship (learned)", "learned"))
        self.assertIn("Tess applied 2026-10-08 and was rejected 2 days later", b.why)
        self.reject("Globex", "2026-10-01", "2026-10-09")                      # 8 days: not learned
        self.assertEqual(self.badge("Globex").kind, "sponsors")

    def test_a_big_filers_quick_rejection_is_about_the_role(self):
        with db.tx(self.conn):
            self.conn.execute("UPDATE h1b_companies SET recent_data = 40 WHERE key = ?", (names.key("Acme Robotics"),))
        self.reject("Acme Robotics", "2026-10-08", "2026-10-09")
        b = self.badge("Acme Robotics")
        self.assertEqual(b.kind, "sponsors")
        self.assertEqual(b.evidence["learned"]["days"], 1)                      # still shown as evidence


class QuoteTest(unittest.TestCase):
    def test_the_sentence_that_decided(self):
        jd = "We build robots. Unfortunately, we are unable to sponsor visas at this time. Apply now!"
        self.assertEqual(signals.sponsorship_quote(jd), "Unfortunately, we are unable to sponsor visas at this time.")
        self.assertEqual(signals.sponsorship_quote("Visa sponsorship is available for this role\nPerks"),
                         "Visa sponsorship is available for this role")
        self.assertIsNone(signals.sponsorship_quote("We build robots."))


if __name__ == "__main__":
    unittest.main()
