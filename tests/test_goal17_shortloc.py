"""221 open matched jobs carry only a country as their location ("United States", "US", "USA"); shortLoc()
stripped the country and left nothing, so the row showed "Microsoft · " with a dangling separator."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

PROBE = Path(__file__).parent / "ui" / "shortloc.mjs"


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class ShortLocTest(unittest.TestCase):
    def short(self, *places: str) -> dict:
        out = subprocess.run(["node", str(PROBE), json.dumps(places)], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_a_country_alone_is_shown_not_dropped(self):
        got = self.short("US", "United States", "USA", "United States of America")
        self.assertEqual(got, {"US": "US", "United States": "United States", "USA": "USA",
                               "United States of America": "United States of America"})

    def test_cities_keep_their_short_form(self):
        got = self.short("Austin, TX", "US, MA, Wilmington", "San Francisco; New York, NY", "Austin, Texas, United States")
        self.assertEqual(got, {"Austin, TX": "Austin, TX", "US, MA, Wilmington": "Wilmington, MA",
                               "San Francisco; New York, NY": "San Francisco +1 more",
                               "Austin, Texas, United States": "Austin, TX"})


if __name__ == "__main__":
    unittest.main()
