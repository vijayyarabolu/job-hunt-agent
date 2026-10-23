"""GOAL30 §1: 150+ real label wordings (the user's and Jordan's per-field log, the Answers page's waiting questions, the
spec's own variants, the nightly check's Ashby page) each find the field they mean, or honestly none: the extension's
classifier (extension/content/classify.js, in node) first, job-hunt-agent's own rules (keys.classify) when it has no key,
as on a real page. The GOAL29 code got 145 of the first 173 right."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

from job_hunt_agent.autofill import keys

ROOT = Path(__file__).resolve().parent.parent
CLASSIFY = ROOT / "extension" / "content" / "classify.js"
CASES = json.loads((ROOT / "tests" / "fixtures" / "autofill" / "goal30" / "meaning_cases.json").read_text())["cases"]


def extension_keys(cases) -> list:
    script = f"const c = require({json.dumps(str(CLASSIFY))}); const xs = JSON.parse(require('fs').readFileSync(0, 'utf8'));" \
             "console.log(JSON.stringify(xs.map(([l, k]) => c.field({ label: l, kind: k }).key || null)))"
    out = subprocess.run(["node", "-e", script], input=json.dumps(cases), capture_output=True, text=True, timeout=60,
                         check=True)
    return json.loads(out.stdout)


@unittest.skipUnless(shutil.which("node"), "needs node")
class MeaningTableTest(unittest.TestCase):
    def test_every_wording_finds_the_field_it_means(self):
        self.assertGreaterEqual(len(CASES), 150)
        for (label, kind, want, src), ext in zip(CASES, extension_keys(CASES)):
            found = keys.classify(keys.norm(label))
            server = found.key if found and found.key != "account_password" and kind != "file" else None
            with self.subTest(label, kind=kind, src=src):
                self.assertEqual(ext or server, want)


def node_classify(body: str, data):
    script = f"const c = require({json.dumps(str(CLASSIFY))}); const xs = JSON.parse(require('fs').readFileSync(0, 'utf8'));" \
             f"console.log(JSON.stringify({body}))"
    out = subprocess.run(["node", "-e", script], input=json.dumps(data), capture_output=True, text=True, timeout=60,
                         check=True)
    return json.loads(out.stdout)


@unittest.skipUnless(shutil.which("node"), "needs node")
class EntryNumberTest(unittest.TestCase):
    """§0: which job or school a field inside a repeater belongs to. "Work Experience 2" is the second job; a heading
    ending in a year is not an entry number; entries with no number go in page order (the second Company box is the
    second job), never all onto the first."""

    def entries(self, rows):
        fields = [{"label": label, "section": section, "kind": "text"} for label, section in rows]
        return node_classify("c.entries(xs.map((f) => c.field(f)))", fields)

    def test_numbered_headings_name_the_entry(self):
        self.assertEqual(self.entries([("Job Title", "Work Experience 2"), ("Company", "Work Experience 2"),
                                       ("Job Title", "Work Experience 1")]), [1, 1, 0])

    def test_unnumbered_entries_go_in_page_order(self):
        self.assertEqual(self.entries([("Job Title", "Work Experience"), ("Company", "Work Experience"),
                                       ("Job Title", "Work Experience"), ("Company", "Work Experience")]), [0, 0, 1, 1])

    def test_a_year_in_the_heading_is_not_an_entry_number(self):
        self.assertEqual(self.entries([("School", "Education 2012"), ("Degree", "Education 2012")]), [0, 0])


if __name__ == "__main__":
    unittest.main()
