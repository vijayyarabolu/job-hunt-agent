"""Signals (US, salary, years, team, talent pool, dealbreakers) and text helpers."""
import unittest

from job_hunt_agent import signals, text

DEALBREAKERS = [r"security clearance", r"\bTS/SCI\b"]


class SignalsTest(unittest.TestCase):
    def test_us_detection(self):
        self.assertTrue(signals.is_us("San Francisco, CA"))
        self.assertTrue(signals.is_us("US, CA, Santa Clara"))
        self.assertTrue(signals.is_us("Remote - US"))
        self.assertTrue(signals.is_us("anything", "United States"))
        self.assertFalse(signals.is_us("London, United Kingdom"))
        self.assertFalse(signals.is_us("Bengaluru, India"))
        self.assertFalse(signals.is_us("x", "SG"))
        self.assertIsNone(signals.is_us("Remote"))
        # verifier M1: country codes that look like US state codes
        self.assertFalse(signals.is_us("Toronto, ON, CA"))
        self.assertFalse(signals.is_us("Munich, DE"))
        self.assertFalse(signals.is_us("New Delhi, IN"))
        self.assertTrue(signals.is_us("Indianapolis, IN"))
        self.assertTrue(signals.is_us("Remote (U.S.)"))
        self.assertTrue(signals.is_us("US, CA, Santa Clara; Germany, Munich"))
        self.assertFalse(signals.is_us("Germany, Munich; UK, Remote"))

    def test_plain_abroad_shapes_seen_in_the_feed(self):
        # 1,244 open matched jobs had no verdict and showed in both feeds; these shapes are clearly abroad
        for place in ("Shanghai_Tianshan", "Ramos Arizpe, MX", "Alajuela, CR", "Diegem, BE, 1831", "SG, 048583",
                      "Remote - MX", "Latin America", "Wrocław", "Penang", "Stationsplein 32, 3511 ED Utrecht",
                      "Belo Horizonte, MG"):
            with self.subTest(place=place):
                self.assertIs(signals.is_us(place), False)
        # a US state code is never read as a country, and bare towns that are also US towns stay unknown
        for place, verdict in (("Denver, CO", True), ("Boise, ID", True), ("Mobile, AL", True), ("Ames, IA", True),
                               ("Cairo", None), ("Milan", None), ("Remote (world)", None),
                               ("Dallas-Fort Worth - JP", True), ("San Jose, CR", False)):
            with self.subTest(place=place):
                self.assertIs(signals.is_us(place), verdict)

    def test_a_german_or_indian_region_before_de_or_in_is_the_country(self):
        # SuccessFactors writes 'city, region, country'; "DE" read as Delaware kept Bayer's Leverkusen US
        for place in ("Leverkusen, Nordrhein-Westfalen, DE", "Weimar, Thüringen, DE", "Munich, BY, DE",
                      "Berlin, Berlin, DE", "Muenchen, München, DE", "Hyderabad, Telangana, IN", "Noida, UP, IN",
                      "Gurugram, HR, IN", "Pune, Maharashtra, IN", "Whitefield, Bengaluru, IN"):
            with self.subTest(place=place):
                self.assertIs(signals.is_us(place), False)
        for place in ("123 Main St, Newark, DE", "Wilmington, Wilmington, DE", "1 Monument Cir, Indianapolis, IN",
                      "Gary, Gary, IN", "Dover, DE", "Fort Wayne, IN"):
            with self.subTest(place=place):
                self.assertIs(signals.is_us(place), True)

    def test_the_second_pass_tail(self):
        # GOAL17 verifier second pass: ~60 foreign places, 1-2 cards each, still read as "maybe US"
        for place in ("Rehovot,ISR", "Remote, GBR", "Capetown", "Trento", "Semarang", "Singapore",
                      "Wiesloch/Walldorf", "Haute Garonne (31)", "Beit HaEmek", "Wien, Wienerbergstraße 11",
                      "Gelang Patah, Johor", "Taleigao - Goa", "Toulouse Area", "Petaling Jaya", "Weinheim",
                      "Flex - Thane", "TUN - ARIANA", "Gütersloh", "Mönchengladbach - Santander-Platz", "東京都中央区",
                      "Ramat Gan", "Herzliya", "Wiesbaden, Hesse", "Ditzingen", "Groningen", "Yokohama", "Zug", "Basel",
                      "Quezon City", "Kronberg, Campus Kronberg 1", "DE Bundesweit", "Carlow, Carlow, IRE",
                      "OFNA BANCO PATIO STA FE CDMX", "Marseille Area", "Amman, JO, 11118",
                      "01010, SAN BERNARDINO, Distrito Capital", "Belo Horizonte, MG; Curitiba, PR; Recife, PE; São Paulo, SP"):
            with self.subTest(place=place):
                self.assertIs(signals.is_us(place), False)
        for place in ("Toronto, ON | Hybrid", "Warsaw - Poland; Digital", "Montreal | Télécommande", "Leverkusen",
                      "London, United Kingdom - Remote / Hybrid", "Gijon", "San Salvador", "Sao Jose dos Campos",
                      "Naples, Napoli", "Ulm Area", "PL_Lodz_Ogrodowa", "Ådalsvej 99 Hørsholm"):
            with self.subTest(place=place):                    # a work-mode segment names no place
                self.assertIs(signals.is_us(place), False)
        self.assertIsNone(signals.is_us("Remote / Hybrid"))
        self.assertIs(signals.is_us("Remote | North America or Europe", "US"), True)   # Remote may include the US
        self.assertIs(signals.is_us("Austin, TX | Hybrid"), True)
        self.assertIs(signals.is_us("San Juan, PR"), True)
        for place in ("KATO SCHOLARI 01", "Canadian Head Office (It)", "EU (Remote)",
                      "Argentina, AR; Belize, BZ; Bolivia, BO; Brazil, BR; Chile, CL", "Merida, City 32"):
            with self.subTest(place=place):                    # after the live resignal: the open tail
                self.assertIs(signals.is_us(place), False)
        for place in ("San Diego", "Detroit", "Nashville", "Plano North", "Raleigh", "Jacksonville", "Santa Monica",
                      "Culver City", "Redwood City", "San Mateo", "Boca Raton", "McLean", "Herndon", "Pleasanton",
                      "Lehi", "NIH-Bethesda", "Long Beach Office 3800", "CA-Beverly Hills - 9601 Wilshire",
                      "Hybrid/Fort Lauderdale - Full-Time", "Philadelphia - FMC Tower", "Phoenix Campus (Main)",
                      "Charlotte"):
            with self.subTest(place=place):                    # US cities without a state
                self.assertIs(signals.is_us(place), True)
        for place in ("Hyderabad - Phoenix Equinox Tower 2", "Hanover House, Charlotte Street - MANCHESTER"):
            with self.subTest(place=place):                    # a street or tower, not the US city
                self.assertIs(signals.is_us(place), False)
        self.assertIsNot(signals.is_us("Macedonia/8210 Macedonia Commons Boulevard/110629"), False)   # Macedonia, OH
        self.assertIsNot(signals.is_us("Deepwater Proteus - GOA"), False)   # Gulf of America rig, not Goa
        self.assertIsNot(signals.is_us("New Ulm Medical Center"), False)    # New Ulm, MN
        self.assertIs(signals.is_us("Durham, NC, New York, NY, Los Angeles, CA, Toronto, CAN"), True)
        for place in ("Lima", "Leeds", "Jena", "Gothenburg", "Karlsruhe", "Belen", "York", "Charlotte"):
            with self.subTest(place=place):                    # US towns too (Lima OH, Leeds AL, Jena LA …)
                self.assertIsNot(signals.is_us(place), False)

    def test_the_unknown_tail_the_verifier_found(self):
        # these showed as "maybe US" in both feeds; Puerto Rico is a US territory
        for place in ("Billund", "Bratislava", "Ljubljana", "Canary Wharf, 1 Churchill Place", "Puteaux", "Oberkochen",
                      "Immenstaad am Bodensee", "Farringdon", "Welwyn", "Hilversum", "Lucknow", "Netanya", "Nicosia",
                      "Lausanne; London"):
            with self.subTest(place=place):
                self.assertIs(signals.is_us(place), False)
        for place in ("San Juan, Puerto Rico, Puerto Rico", "Puerto Rico"):
            with self.subTest(place=place):
                self.assertIs(signals.is_us(place), True)
        for place in ("Athens", "Cairo", "Milan", "Vienna", "Markham", "2 Locations", "Remote"):
            with self.subTest(place=place):                    # Athens GA, Cairo IL, Milan TN, Vienna VA, Markham IL
                self.assertIsNone(signals.is_us(place))

    def test_salary_formats(self):
        self.assertEqual(signals.parse_salary("$120,000 - $150,000"), (120000.0, 150000.0, "USD"))
        self.assertEqual(signals.parse_salary("$257K – $335K • Offers Equity"), (257000.0, 335000.0, "USD"))
        self.assertEqual(signals.parse_salary("148,000 USD - 235,750 USD"), (148000.0, 235750.0, "USD"))
        self.assertEqual(signals.parse_salary("401k match and $50 stipend"), (None, None, None))
        self.assertEqual(signals.parse_salary("Signing bonus $50,000. Salary $180,000 - $220,000"),
                         (180000.0, 220000.0, "USD"))
        self.assertEqual(signals.parse_salary("$52 - $64 per hour"), (None, None, None))

    def test_min_years(self):
        # GOAL3 §2 changed the rule from the smallest to the largest required number: both are requirements here
        self.assertEqual(signals.min_years("5+ years of experience with Python; 3 years experience in ML"), 5)
        self.assertEqual(signals.min_years("3-5 years of professional experience"), 3)
        self.assertIsNone(signals.min_years("Experience with PyTorch"))

    def test_team_and_talent_pool(self):
        self.assertTrue(signals.has_named_team("You will report to the Head of Applied ML."))
        self.assertTrue(signals.has_named_team("", team="Search Ranking"))
        self.assertFalse(signals.has_named_team("We build things.", team="Engineering"))
        self.assertTrue(signals.is_talent_pool("ML Engineer - Talent Pool", ""))
        self.assertTrue(signals.is_talent_pool("ML Engineer", "Submit a general application for future opportunities"))
        self.assertFalse(signals.is_talent_pool("ML Engineer", "Build models."))

    def test_dealbreakers_respect_negation(self):
        self.assertEqual(signals.dealbreaker("Active TS/SCI required.", DEALBREAKERS), r"\bTS/SCI\b")
        self.assertEqual(signals.dealbreaker("Must hold a security clearance.", DEALBREAKERS), "security clearance")
        self.assertIsNone(signals.dealbreaker("No security clearance required.", DEALBREAKERS))
        self.assertIsNone(signals.dealbreaker("This role does not require a security clearance.", DEALBREAKERS))


class TextTest(unittest.TestCase):
    def test_html_to_text_handles_double_escaping(self):
        raw = "&lt;div&gt;&lt;p&gt;Build &amp;amp; ship&lt;/p&gt;&lt;ul&gt;&lt;li&gt;RAG&lt;/li&gt;&lt;/ul&gt;&lt;/div&gt;"
        self.assertEqual(text.html_to_text(raw, double_escaped=True), "Build & ship\n\n- RAG")

    def test_norm_and_boilerplate(self):
        self.assertEqual(text.norm("  Sr. ML Engineer (C++/Python)!"), "sr ml engineer c++ python")
        body = "Build RAG systems.\nWe are an equal opportunity employer and value diversity."
        self.assertEqual(text.strip_boilerplate(body), "Build RAG systems.")
        mixed = "Build RAG.\nWe believe in pay transparency.\nMust hold a security clearance."
        self.assertEqual(text.strip_boilerplate(mixed), "Build RAG.\nMust hold a security clearance.")


class ForwardNegationTest(unittest.TestCase):
    """Verifier M9: "clearance is not required" after the match is a negation too; other sentences are not."""

    def test_not_required_after_the_match(self):
        from job_hunt_agent import config
        pats = list(config.DEFAULT_DEALBREAKERS)
        for text_ in ("Security clearance is not required for this role.", "Security clearance isn't needed.",
                      "Top Secret clearance is optional.", "Security clearance (not required).",
                      "Security clearance (preferred, not required)."):
            self.assertIsNone(signals.dealbreaker(text_, pats), text_)
        for text_ in ("Active security clearance required.", "An active secret clearance is required, not preferred.",
                      "Requires polygraph. Relocation is not required.",
                      "This position requires an active Secret clearance (not required to be TS).",
                      "Active Secret clearance (TS not required).",
                      "TS/SCI clearance (not required at start; must be obtainable)."):
            self.assertIsNotNone(signals.dealbreaker(text_, pats), text_)


class DefaultDealbreakerTest(unittest.TestCase):
    """GOAL §5: dealbreakers default to security clearance and TS/SCI for every profile (release review C3)."""

    def _profile(self, extra: str = "") -> dict:
        import tempfile
        from pathlib import Path
        from unittest import mock
        from job_hunt_agent import config
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "p").mkdir()
            (Path(tmp) / "p" / "profile.yaml").write_text("name: P\nfamilies: [data_scientist]\n" + extra)
            with mock.patch.object(config, "PROFILES", Path(tmp)):
                return config.profile("p")

    def test_defaults_apply_merge_and_can_be_turned_off(self):
        from job_hunt_agent import config
        self.assertTrue(set(config.DEFAULT_DEALBREAKERS) <= set(self._profile()["dealbreakers"]))
        merged = self._profile("dealbreakers: ['\\bon-call\\b']\n")["dealbreakers"]
        self.assertIn("\\bon-call\\b", merged)
        self.assertTrue(set(config.DEFAULT_DEALBREAKERS) <= set(merged))
        self.assertEqual(self._profile("dealbreakers_default: false\n")["dealbreakers"], [])

    def test_default_patterns_catch_live_wordings(self):
        from job_hunt_agent import config
        patterns = list(config.DEFAULT_DEALBREAKERS)
        for text in ("Requires an Active Top Secret clearance", "TS / SCI with polygraph", "Top-Secret eligibility",
                     "must hold a security clearance", "Active Secret clearance required"):
            self.assertIsNotNone(signals.dealbreaker(text, patterns), text)
        for text in ("We keep your data secret", "No clearance required", "clearance sale on GPUs"):
            self.assertIsNone(signals.dealbreaker(text, patterns), text)


class SponsorshipTest(unittest.TestCase):
    def test_labels(self):
        cases = [
            ("Requirements:\n- US Citizen (Public Trust Eligible)\n- Python", "citizen"),        # verifier 6, D2
            ("• U.S. Citizenship required", "citizen"),
            ("Must be a U.S. citizen to be considered.", "citizen"),
            ("We are unable to sponsor visas for this role.", "no"),
            ("Candidates must be authorized to work without visa sponsorship.", "no"),
            ("Visa sponsorship is available.", "yes"),
            ("We consider applicants regardless of citizenship status or any other protected basis.", None),
            ("Open to US citizens and permanent residents and visa holders alike.", None),
            (None, None),
        ]
        for body, expected in cases:
            with self.subTest(body=body):
                self.assertEqual(signals.sponsorship(body), expected)


# leaks seen in the live DB on 2026-10-05 (trimmed real JD excerpts)
CAPITAL_ONE_DE4 = """Basic Qualifications:

- Bachelor's Degree or higher in Computer Science or a related quantitative field (Statistics, Economics)

- At least 4 years of experience in application development (Internship experience does not apply)

- At least 2 years of experience in distributed data

- At least 2 years of experience with SQL

- At least 1 year of experience in data modeling and designing end-to-end data solutions using both relational and non-relational database systems

Preferred Qualifications:

- 7+ years of experience in application development with demonstrated proficiency in Python, SQL, Scala, or Java

- 4+ years of hands-on experience designing, deploying and operating data workloads in at least one public cloud
"""
CATERPILLAR_SR_DS = """BASIC QUALIFICATIONS:

- Desire BS or higher degree in a quantitative discipline such as data science, data analytics, computer science, engineering, statistics, mathematics, finance, or other related degree and 6+ years of professional experience utilizing quantitative analysis/ data management (data design, data quality, metadata, governance, etc.) is required, a Master’s degree and 1-3 years of relevant experience. OR equivalent working experience

- Proficiency in BI tools, Python and SQL, GA4, BigQuery.

- Extensive experience (typically 3 years+) with advanced data analysis and statistical methods such as regression, hypothesis testing, ANOVA, statistical process control, etc.
"""
SSC_GENAI = """Qualifications:
- 2–3 years experience building GenAI / LLM-based applications
- 1–3 years hands-on experience with prompt engineering
"""


class Goal3YearsTest(unittest.TestCase):
    def test_largest_required_number_wins_over_a_one_year_tool_line(self):
        self.assertEqual(signals.min_years(CAPITAL_ONE_DE4), 4)      # was 1 ('At least 1 year of ... data modeling')
        self.assertEqual(signals.min_years(SSC_GENAI), 2)

    def test_degree_ladder_takes_the_masters_rung(self):
        # Caterpillar "SR Data Scientist": BS + 6 years, or a Master's + 1-3 years -> the MS rung (1); the separate
        # "typically 3 years+" requirement is the largest -> 3 (was 1). Both profiles hold an MS.
        self.assertEqual(signals.min_years(CATERPILLAR_SR_DS), 3)
        self.assertEqual(signals.min_years("- PhD plus 5+ years of research experience, or a Master’s degree plus "
                                           "10+ years of research experience in Machine Learning"), 10)
        self.assertEqual(signals.min_years("- Bachelor’s degree in CS and 8+ years of relevant experience; or a "
                                           "Master’s degree and 6+ years; or, in lieu of a degree, 12+ years of "
                                           "directly related experience."), 6)
        self.assertEqual(signals.min_years("- 5 years of experience as a data engineer with a Master's degree in STEM "
                                           "OR 7 years of experience as a data engineer with a bachelor's degree"), 5)

    def test_ladder_alternatives_across_lines_and_degree_substitutes(self):
        hatch = ("- Bachelor’s degree plus 3 years of recent specialized experience; or\n"
                 "- Associate’s degree plus 7 years of recent specialized experience; or\n"
                 "- 11 years of recent specialized experience.")
        self.assertEqual(signals.min_years(hatch), 3)
        stripe = ("Master’s degree in Statistics, plus 5 years of related work experience in data science.\n"
                  "In the alternative, the employer will accept a Bachelor’s degree in Statistics, plus 7 years "
                  "of related work experience in data science.")
        self.assertEqual(signals.min_years(stripe), 5)
        accenture = ("Bachelor's degree or equivalent (minimum 12 years) work experience. (If Associate’s Degree, "
                     "must have minimum 6 years work experience)\nMinimum of 3 years of experience as a machine "
                     "learning engineer")
        self.assertEqual(signals.min_years(accenture), 3)
        atlassian = "A BE in Computer Science or equivalent with 6+ years of experience building ML systems"
        self.assertEqual(signals.min_years(atlassian), 6)          # the years come on top of the degree
        self.assertEqual(signals.min_years("BS in CS or an equivalent degree and 5+ years of industry experience"), 5)

    def test_same_rung_requirements_add_up_and_decimals_floor(self):
        self.assertEqual(signals.min_years("Bachelor's degree and 5+ years of experience, including 2+ years of "
                                           "experience with Python or Go"), 5)
        self.assertEqual(signals.min_years("Minimum 7.5 year(s) of experience is required"), 7)
        self.assertEqual(signals.min_years("- 8+ years of professional software engineering experience, plus 1+ "
                                           "years of dedicated focus building production-grade LLM systems"), 8)


class Goal3YearsCoverageTest(unittest.TestCase):
    """Years the parser missed in the 2026-10-05 top-25 audit (min_years None on Senior roles)."""

    def test_number_words_and_marked_years_without_the_word_experience(self):
        us_bank = "Basic Qualifications:\nTwo to three years of relevant experience"
        self.assertEqual(signals.min_years(us_bank), 2)
        self.assertEqual(signals.min_years("five (5) years of experience in ML"), 5)
        self.assertEqual(signals.min_years("- 3+ years in data analytics, ideally in retail or consumer products."), 3)
        self.assertEqual(signals.min_years("- 8+ years in Business Systems Analysis or Automation roles"), 8)
        self.assertIsNone(signals.min_years("Applicants must be 18+ years of age."))
        self.assertIsNone(signals.min_years("We have served customers for over 95 years."))

    def test_an_upper_bound_is_not_a_minimum(self):
        # GOAL8 §4 audit: RA Capital "Up to three years of experience" was read as 3+ and hid the job at cap 2
        self.assertIsNone(signals.min_years("Up to three years of experience in software engineering, data "
                                            "engineering, or a related role."))
        for text in ("Less than 2 years of experience with Python", "0-2 years of professional experience",
                     "no more than 3 years of industry experience", "0 to 3 years of experience in ML",
                     "fewer than 5 years of experience building models"):
            with self.subTest(text):
                self.assertIsNone(signals.min_years(text))
        self.assertEqual(signals.min_years("- 3+ years of experience in ML\n- up to 5 years in a startup is a plus"), 3)
        self.assertEqual(signals.min_years("1-3 years of experience in data science"), 1)

    def test_jd_seeking_a_staff_hire_without_years_reads_as_staff(self):
        shield = ("Shield AI is seeking engineers across air, maritime, and ground domains. We are seeking a Staff "
                  "Engineer to develop these capabilities from algorithms and production code.")
        self.assertEqual(signals.min_years(shield), signals.STAFF_ASSUMED_YEARS)
        self.assertEqual(signals.min_years("We are seeking a Staff Engineer.\n- 5+ years of experience in C++"), 5)
        self.assertIsNone(signals.min_years("You will partner with staff across the company."))

    def test_requirement_bullets_under_skipped_headings_count_last(self):
        wayve = ("About Wayve\nWayve builds embodied AI.\n\U0001f64c You should apply if:\n"
                 " - You have 3+ years of experience in a Data Science role.\n"
                 "It would be great if you also have:\n - 6+ years of experience with autonomous driving data.\n"
                 "What’s in it for you:\n - Technology stipend - $4,000 every 2 years after your start.")
        self.assertEqual(signals.min_years(wayve), 3)


class Goal3LocationTest(unittest.TestCase):
    def test_remote_abroad_beats_a_us_country_field(self):
        # SS&C "Generative AI Developer": Workday reported country "United States of America"
        self.assertFalse(signals.is_us("Remote - Poland; Remote - Bulgaria", "United States of America"))
        self.assertTrue(signals.is_us("2 Locations", "United States of America"))
        self.assertTrue(signals.is_us("Manchester", "United States"))       # ambiguous town: the country field wins

    def test_remote_country_names_are_not_us(self):
        for country in ("Bulgaria", "Romania", "Portugal", "Philippines", "Costa Rica", "Serbia", "Ukraine", "Vietnam",
                        "Egypt", "Nigeria", "Kenya", "South Africa", "Chile", "Peru", "Uruguay", "Greece", "Turkey",
                        "Czech Republic", "Hungary", "Austria", "Belgium", "Finland", "Norway", "Estonia", "Lithuania",
                        "Latvia", "Croatia", "Slovakia", "Slovenia", "Malaysia", "Thailand", "Indonesia", "Pakistan",
                        "Bangladesh", "Sri Lanka", "New Zealand", "Saudi Arabia", "UAE", "Qatar"):
            with self.subTest(country=country):
                self.assertFalse(signals.is_us(f"Remote - {country}"))
        self.assertFalse(signals.is_us("San Jose, Costa Rica"))
        self.assertFalse(signals.is_us("Edmonton, AB, CA"))
        self.assertFalse(signals.is_us("IND - TN - CHENNAI"))
        self.assertFalse(signals.is_us("Stuttgart, DE"))

    def test_us_places_with_foreign_names_stay_us(self):
        for place in ("New Mexico", "Albuquerque, New Mexico", "Warsaw, IN", "Dublin, CA", "London, Kentucky",
                      "Vancouver, WA", "Melbourne, FL", "Lebanon, PA", "Geneva, IL", "Ontario, CA",
                      "United States & Canada", "Remote - US or Canada", "Berlin, Germany or Palo Alto, CA",
                      "US - Remote", "Princeton - NJ - US", "Plano, Texas", "CT - Hartford"):
            with self.subTest(place=place):
                self.assertTrue(signals.is_us(place))
        self.assertIsNone(signals.is_us("Non-US Remote"))
        self.assertIsNone(signals.is_us("Athens"))                          # Greece or Georgia: unknown


class Goal3DealbreakerTest(unittest.TestCase):
    PROFILE_PATTERNS = [r"\bsecurity clearance\b", r"\bTS/SCI\b", r"\b(top secret|secret) clearance\b",
                        r"\bpolygraph\b", r"\bTS\s*/\s*SCI\b", r"\b(?:secret|ts|top[ -]?secret)[ -]cleared\b",
                        r"\b(?:government|federal|dod|doe)\s+(?:security\s+)?clearance\b"]

    def test_clearance_title_is_a_dealbreaker(self):
        from job_hunt_agent import config
        for patterns in (self.PROFILE_PATTERNS, list(config.DEFAULT_DEALBREAKERS)):
            self.assertIsNotNone(signals.dealbreaker("AI Engineer Associate - TS/SCI with Polygraph", patterns))
        self.assertIsNotNone(signals.dealbreaker("Data Scientist/Application Developer (Secret cleared)",
                                                 self.PROFILE_PATTERNS))
        self.assertIsNotNone(signals.dealbreaker("Must be eligible to obtain a government clearance",
                                                 self.PROFILE_PATTERNS))

    def test_negations_still_hold(self):
        for text_ in ("Clearance is not required for this role.", "No security clearance required.",
                      "Government Clearance Required? No  Job Posting End Date", "Polygraph is not required."):
            self.assertIsNone(signals.dealbreaker(text_, self.PROFILE_PATTERNS), text_)

    def test_both_profiles_carry_the_polygraph_pattern(self):
        from job_hunt_agent import config
        for pid in config.profile_ids():
            with self.subTest(profile=pid):
                self.assertIn(r"\bpolygraph\b", config.profile(pid)["dealbreakers"])


if __name__ == "__main__":
    unittest.main()
