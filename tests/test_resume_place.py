"""the resume's location follows the job (remote -> the person's own city; hybrid / on-site -> the job's
first US city as "City, ST"). Locations below are real shapes from the jobs table."""
import unittest

from job_hunt_agent import resume_place as rp


class Workplace(unittest.TestCase):
    def test_remote_keeps_the_persons_city(self):
        self.assertEqual(rp.resolve("remote", "Seattle, WA", ""), {
            "workplace": "remote", "location": None, "why": "remote (job): the person's own city", "pick": False,
            "choices": []})
        self.assertIsNone(rp.resolve(None, "Remote (US/Canada)", "")["location"])
        self.assertEqual(rp.resolve(None, "", "This role is fully remote within the US.")["workplace"], "remote")

    def test_hybrid_and_onsite_take_the_jobs_city(self):
        self.assertEqual(rp.resolve("hybrid", "San Francisco, CA; New York, NY", "")["location"], "San Francisco, CA")
        self.assertEqual(rp.resolve("onsite", "US, MA, Wilmington", "")["location"], "Wilmington, MA")
        self.assertEqual(rp.resolve(None, "Los Angeles, California", "We work in office 3 days a week.")["workplace"],
                         "hybrid")
        self.assertEqual(rp.resolve(None, "Austin, TX", "This is an on-site role.")["workplace"], "onsite")

    def test_unstated_is_onsite(self):
        got = rp.resolve(None, "US, MA, Wilmington", "Build AI tools for our engineers.")
        self.assertEqual((got["workplace"], got["location"]), ("onsite", "Wilmington, MA"))
        self.assertIn("not stated", got["why"])

    def test_several_locations_take_the_first_us_one(self):
        self.assertEqual(rp.city_state("London, UK; Boston, MA"), "Boston, MA")
        self.assertEqual(rp.city_state("West Milwaukee +1 more", "Location: West Milwaukee, WI or Kenosha, WI"),
                         "West Milwaukee, WI")

    def test_no_usable_us_city_keeps_the_persons_city(self):
        for place in ("Toronto, ON", "London, UK", "Bengaluru, India", "United States", "US", ""):
            with self.subTest(place=place):
                self.assertIsNone(rp.resolve("onsite", place, "")["location"])


class CityState(unittest.TestCase):
    def test_real_location_shapes(self):
        cases = {"US, MA, Wilmington": "Wilmington, MA", "Dayton, OH": "Dayton, OH", "Plano, TX, US": "Plano, TX",
                 "Greenville, South Carolina": "Greenville, SC", "Seattle, Washington, United States": "Seattle, WA",
                 "New York": "New York, NY", "New York City": "New York, NY", "GREENVILLE, SC": "Greenville, SC",
                 "San Francisco HQ Office": "San Francisco, CA", "United States - New York": "New York, NY",
                 "Jersey City New Jersey United States": "Jersey City, NJ", "Austin, TX 78701": "Austin, TX",
                 "San Francisco Bay Area, California USA": "San Francisco, CA", "Washington, DC": "Washington, DC",
                 "New York, New York, USA": "New York, NY", "Annapolis Junction, Maryland": "Annapolis Junction, MD"}
        for place, want in cases.items():
            with self.subTest(place=place):
                self.assertEqual(rp.city_state(place), want)


class MoreShapes(unittest.TestCase):
    """GOAL11 §9 (user, 2026-10-08 12:46): McLane's Taleo "United States-Texas-Temple" printed Austin, TX."""
    def test_country_state_city_and_other_ats_shapes(self):
        cases = {"United States-Texas-Temple": "Temple, TX", "US-TX-Temple": "Temple, TX",
                 "Temple, Texas, United States": "Temple, TX", "USA - Texas - Temple": "Temple, TX",
                 "TX - Temple": "Temple, TX", "USA-Ohio-Columbus": "Columbus, OH", "NY - Brooklyn": "Brooklyn, NY",
                 "Texas - Fort Worth": "Fort Worth, TX", "MA-Boston, UNAVAILABLE, United States": "Boston, MA",
                 "  Atlanta-GA": "Atlanta, GA", "USA - Georgia - McDonough": "McDonough, GA",
                 "Client Site - USA - CA - Woodland Hills - 6150 Canoga Ave": "Woodland Hills, CA",
                 "USA - Wilmington IL 60481": "Wilmington, IL", "Lincoln NE": "Lincoln, NE",
                 "Fort Collins CO": "Fort Collins, CO", "Austin TX ": "Austin, TX",
                 "Washington, District Of Columbia": "Washington, DC", "In-Person NYC": "New York, NY",
                 "Dallas-Fort Worth": "Dallas, TX", "Winston-Salem, NC": "Winston-Salem, NC",
                 "United States-Texas-Corpus Christi": "Corpus Christi, TX",
                 "USA, California, Santa Cruz": "Santa Cruz, CA", "US-CA Los Angeles": "Los Angeles, CA",
                 "PA - Philadelphia (19103)": "Philadelphia, PA", "9835 Eagle Dr.\nMont Belvieu, TX 77523": "Mont Belvieu, TX",
                 "901 Yorkchester, Houston 77090": "Houston, TX",
                 "United States-New York-New York": "New York, NY", "2458 - Cedar Park, TX": "Cedar Park, TX",
                 "Store 192- Columbia, MD": "Columbia, MD", "Washington D.C.; Torrance, CA": "Washington, DC"}
        for place, want in cases.items():
            with self.subTest(place=place):
                self.assertEqual(rp.city_state(place), want)

    def test_no_city_stays_none(self):
        for place in ("TBD, United States", "Any", "Global Remote", "UNAVAILABLE, UNAVAILABLE, US", "Texas", "US-TX",
                      "Berlin, BE, DE, 10117", "Rajpura, PB, IN, 140401"):     # DE / IN are Germany / India here
            with self.subTest(place=place):
                self.assertIsNone(rp.city_state(place))

    def test_mclane_hybrid_job_takes_its_city(self):
        self.assertEqual(rp.resolve("hybrid", "United States-Texas-Temple", "")["location"], "Temple, TX")


class FromTheJdText(unittest.TestCase):
    """a pasted / manual JD (no job-hunt-agent job) gives its own workplace and location."""
    ICF = ("Data Engineer\nLocation: Reston, VA; Alabama Remote Office (AL99); Charlotte, NC\n\nAbout the role ...\n"
           "Job Location: Remote, however, strong preference for candidates who live in the Washington DC Metro Area. "
           "There will be occasional onsite meetings on the client site in Washington, DC.")

    def test_the_posting_location_line_wins_over_loose_words(self):
        self.assertEqual(rp.location_in_jd(self.ICF).split(";")[0], "Reston, VA")
        self.assertEqual(rp.resolve(None, None, self.ICF)["workplace"], "remote", "not the 'onsite meetings' line")

    def test_a_hybrid_jd_with_a_location_line(self):
        jd = "Senior Data Engineer\nWork Location: Pittsburgh, PA (Hybrid, 3 days in office)\nWe build ..."
        self.assertEqual(rp.resolve(None, None, jd), {"workplace": "hybrid", "location": "Pittsburgh, PA",
                                                      "why": "hybrid (location): the job's city Pittsburgh, PA",
                                                  "pick": False, "choices": ["Pittsburgh, PA"]})

    def test_a_city_line_in_the_header(self):
        jd = "Machine Learning Engineer\nAcme Corp\nAustin, TX · On-site\n\nAbout Acme, Inc. in New York ..."
        self.assertEqual(rp.resolve(None, None, jd)["location"], "Austin, TX")

    def test_nothing_stated_keeps_the_persons_city(self):
        self.assertIsNone(rp.resolve(None, None, "We build ML systems. You know Python.")["location"])


if __name__ == "__main__":
    unittest.main()


class StreetAddresses(unittest.TestCase):
    """GOAL22 (user 2026-10-13 11:08): Target's street address failed Generate. Real location strings from the jobs
    table; every answer is a "City, ST" resumegen accepts, or None."""
    def test_addresses_give_the_city(self):
        cases = {"7000 Target Pkwy N,NCD-0375 Brooklyn Park,MN 55445": "Brooklyn Park, MN",
                 "1000 Nicollet Mall, Minneapolis,MN 55403-2542": "Minneapolis, MN",
                 "43 New Scotland Avenue Albany, NY 12208": "Albany, NY",
                 "1200 Westwood Dr, Hamilton, MT 59840, USA": "Hamilton, MT",
                 "1530 FM 973 Taylor, TX, USA": "Taylor, TX",
                 "Main Campus, Columbus, OH": "Columbus, OH",
                 "Streeterville, Chicago, IL": "Chicago, IL",
                 "Richmond University Medical Center (Staten Island, NY)": "Staten Island, NY",
                 "Main Campus (Washington, DC)": "Washington, DC",
                 "US > Arizona > Phoenix": "Phoenix, AZ",
                 "North America/USA/Tennessee/Lexington, TN": "Lexington, TN",
                 "Main Campus - Pavilion A - 777 Bannock Street, Denver, CO 80204": "Denver, CO",
                 "Omni Homestead Resort, 1766 Homestead Drive, Hot Springs, Virginia, United States of America":
                     "Hot Springs, VA",
                 "Shea Medical Center - 9003 E Shea Blvd Scottsdale, AZ 85260": "Scottsdale, AZ",
                 "Santa Fe Springs - Clark St., CA, USA": "Santa Fe Springs, CA",
                 "Bourbonnais - Hybrid, IL, USA": "Bourbonnais, IL",
                 "MO-Kansas City": "Kansas City, MO", "NY - New York": "New York, NY", "DC - Washington": "Washington, DC",
                 "Store 3067-Miracle Mile Shops-WHBM-Las Vegas, NV 89109": "Las Vegas, NV",
                 "Hoboken University Hosiptal, Hoboken, NJ, US": "Hoboken, NJ"}
        for place, want in cases.items():
            with self.subTest(place=place):
                self.assertEqual(rp.city_state(place), want)

    def test_never_a_value_resumegen_rejects(self):
        for place in ("Aurora St Lukes Medical Center - 2900 W Oklahoma Ave", "Remote Opportunity - VA, NC, SC",
                      "Sonesta Resort Hilton Head Island, SC", "TN Columbia - 156017", "IN TN CHENNAI Home Office"):
            with self.subTest(place=place):
                got = rp.city_state(place)
                self.assertTrue(got is None or rp.LOCATION.match(got), got)


class PickTheCity(unittest.TestCase):
    """Generate asks when the job names several cities or a place that isn't a US "City, ST"."""
    def test_one_clear_city_needs_no_pick(self):
        got = rp.resolve("onsite", "7000 Target Pkwy N,NCD-0375 Brooklyn Park,MN 55445", "")
        self.assertEqual((got["location"], got["pick"], got["choices"]), ("Brooklyn Park, MN", False, ["Brooklyn Park, MN"]))

    def test_several_cities_ask(self):
        got = rp.resolve("hybrid", "Birmingham, AL, USA; Tarrant, AL 35217, USA; Fultondale, AL, USA", "")
        self.assertTrue(got["pick"])
        self.assertEqual(got["choices"], ["Birmingham, AL", "Tarrant, AL", "Fultondale, AL"])
        self.assertTrue(rp.resolve("onsite", "West Milwaukee +1 more", "")["pick"])

    def test_a_place_that_cannot_be_worked_out_asks(self):
        for place in ("Toronto, ON", "Sonesta Resort Hilton Head Island, SC", "Aurora St Lukes Medical Center - 2900 W Oklahoma Ave"):
            with self.subTest(place=place):
                got = rp.resolve("onsite", place, "")
                self.assertEqual((got["location"], got["pick"]), (None, True))

    def test_words_no_city_has_ask_instead_of_guessing(self):
        # these went out as "Store, WA", "Work from Home, OH", "Karnataka, IN", "week in Whitwell, TN"
        for place in ("OH-Ohio-Work from Home", "Location Varies, KY", "Customer Site, CA", "Corporate, TN",
                      "Hybrid (Bengaluru, Karnataka, IN)", "Hybrid- 3 days/week in Whitwell, TN",
                      "100% Remote - Covering all of Indiana"):
            with self.subTest(place=place):
                got = rp.resolve("onsite", place, "")
                self.assertEqual((got["location"], got["pick"]), (None, True))

    def test_a_street_named_after_a_state_is_not_the_state(self):
        self.assertEqual(rp.city_state("Store 21720, 1151 Columbus Ave, Washington Court House, Ohio, United States of "
                                       "America"), "Washington Court House, OH")
        self.assertEqual(rp.city_state("IN-INDIANAPOLIS, 220 VIRGINIA AVE"), "Indianapolis, IN")
        for place, want in (("Center Point, AL", "Center Point, AL"), ("Homestead, FL", "Homestead, FL"),
                            ("Plant City, FL", "Plant City, FL"), ("Mountain Home, AR", "Mountain Home, AR"),
                            ("Lake In The Hills, IL", "Lake In The Hills, IL")):
            self.assertEqual(rp.city_state(place), want)

    def test_no_place_or_remote_never_asks(self):
        for stored, place in (("onsite", "United States"), ("onsite", ""), ("onsite", "TBD, United States"),
                              ("remote", "Seattle, WA; Austin, TX"), (None, "Remote (US/Canada)")):
            with self.subTest(place=place):
                self.assertFalse(rp.resolve(stored, place, "")["pick"])
