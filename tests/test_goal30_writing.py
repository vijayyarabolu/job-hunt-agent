"""GOAL30-app §1 #12 to #16: the user's UI rules: no em or en dashes and no dashes as punctuation, ranges written "5 to 10".
Every string the page's own scripts, CSS and HTML show is checked (comments aside); answers.js is GOAL30-ext's.
Server-made text is checked where it was fixed (Stats, the email panel, pay ranges, the Google page)."""
import inspect
import re
import unittest
from pathlib import Path

from job_hunt_agent import match, score, stats, stats_html

STATIC = Path(__file__).resolve().parent.parent / "job_hunt_agent" / "static"
DASHES = re.compile("[–—]")
OWNED_ELSEWHERE = {"answers.js"}          # GOAL30-ext's Answers page


def _code(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return "\n".join(re.sub(r"(^|\s)//.*$", "", line) for line in text.splitlines())


class NoDashesTest(unittest.TestCase):
    def test_the_page_shows_no_em_or_en_dash(self):
        found = []
        for path in [*sorted((STATIC / "js").glob("*.js")), *sorted(STATIC.glob("*.css")), STATIC / "index.html"]:
            if path.name in OWNED_ELSEWHERE:
                continue
            body = _code(path.read_text()) if path.suffix == ".js" else re.sub(r"/\*.*?\*/", "", path.read_text(), flags=re.S)
            found += [f"{path.name}: {line.strip()[:90]}" for line in body.splitlines() if DASHES.search(line)]
        self.assertEqual(found, [])

    def test_ranges_and_empty_values_are_words(self):
        self.assertEqual(match.salary_label(150000, 190000), "$150k to $190k")
        self.assertEqual(stats_html._pct(None), "none yet")
        self.assertTrue(all(not DASHES.search(label) for _, label in stats.POSTED_BUCKETS))
        chip = inspect.getsource(score)
        self.assertIn('k to ${hi / 1000:.0f}k"', chip)                # the feed's pay chip: "$150k to $190k"
        self.assertFalse(DASHES.search(chip.split('chips.append((f"$', 1)[1].split("\n", 1)[0]))



class AppliedSegmentsTest(unittest.TestCase):
    def test_the_segment_labels_fit_one_row(self):
        # "Rejected or cold" pushed every count under its label and hid "All 553" (GOAL30-app second pass); the
        # labels with their counts fit the list pane's one row up to the 30 letters of All/Waiting/Interviewing/Rejected
        segs = (STATIC / "js" / "applied.js").read_text().split("const SEGS = ", 1)[1].split(";", 1)[0]
        labels = re.findall(r'\["\w+", "([^"]+)", "[^"]+"\]', segs)
        self.assertEqual(len(labels), 4)
        self.assertLessEqual(sum(map(len, labels)), 30, labels)
        self.assertIn("Cold", segs)                     # the hover says what the last one holds


if __name__ == "__main__":
    unittest.main()
