"""GOAL27 §2: the field classifier (extension/content/classify.js, run in node). Every case of the corpus (labels,
names, autocomplete tokens, placeholders, sections and choices as the major ATSs write them) maps to its canonical
field; questions it cannot be sure of stay unknown (never a guess); sensitive fields are recognised."""
import json
import shutil
import subprocess
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLASSIFY = ROOT / "extension" / "content" / "classify.js"
CASES = Path(__file__).parent / "fixtures" / "autofill" / "goal27" / "classify_cases.json"
BOARDS = Path(__file__).parent / "fixtures" / "autofill" / "boards"
PAGES = Path(__file__).parent / "ui" / "goal27_classify_pages.mjs"
CHROME = os.environ.get("JH_CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
# recorded pages: the fields that matter, by their label as the page shows it
RECORDED = {
    "greenhouse.html": {"First Name": "first_name", "Last Name": "last_name", "Email": "email", "Country": "phone_code",
                        "Phone": "phone", "Location (City)": "location", "Are you 18 years or older?": "age18",
                        "Are you legally authorized to work in the United States?": "work_auth", "Gender": "gender",
                        "Are you Hispanic or Latinx?": "hispanic", "Protected Veteran Status": "veteran",
                        "Disability Status": "disability", "Have you worked at DoorDash?": "prev_worked"},
    "lever.html": {"Full name✱": "full_name", "Email✱": "email", "Phone": "phone", "Current company": "current_company",
                   "LinkedIn URL": "linkedin", "GitHub URL": "github", "Gender": "gender", "Race": "race",
                   "Will you require sponsorship for employment now or in the future?✱": "sponsor_future",
                   "If yes, what type of sponsorship?": None},
    "ashby.html": {"Legal Name": "full_name", "Email": "email", "Phone": "phone", "Resume": "resume",
                   "LinkedIn Profile": "linkedin"},
    "workable.html": {"First name": "first_name", "Last name": "last_name", "Expected salary": "salary",
                      "How did you hear about us?": "how_heard"},
    "bamboohr.html": {"Address": "address1", "City": "city", "ZIP": "zip", "Date Available": "start_date",
                      "Desired Pay": "salary", "Will you now or in the future require employment visa sponsorship?": "sponsor_future"},
    "jazzhr.html": {"First Name": "first_name", "State/Province": "state", "Postal": "zip", "Resume": "resume"},
}


def run(fields: list[dict]) -> list[dict]:
    script = f"const c = require({json.dumps(str(CLASSIFY))}); const fs = require('fs');" \
             "const xs = JSON.parse(fs.readFileSync(0, 'utf8')); console.log(JSON.stringify(xs.map((f) => c.field(f))))"
    out = subprocess.run(["node", "-e", script], input=json.dumps(fields), capture_output=True, text=True, timeout=30,
                         check=True)
    return json.loads(out.stdout)


@unittest.skipUnless(shutil.which("node"), "needs node")
class ClassifyTest(unittest.TestCase):
    def test_corpus(self):
        cases = json.loads(CASES.read_text())
        got = run([c["field"] for c in cases])
        self.assertGreaterEqual(len(cases), 140)
        for case, result in zip(cases, got):
            with self.subTest(case["field"].get("label") or case["field"].get("name")):
                self.assertEqual(result.get("key"), case["expect"], result)

    def test_repeater_index_from_name(self):
        got = run([{"label": "Title", "name": "jobs[2][title]", "kind": "text"},
                   {"label": "School", "name": "education-1--school", "kind": "text", "section": "Education"}])
        self.assertEqual((got[0]["key"], got[0]["index"]), ("work.title", 2))
        self.assertEqual((got[1]["key"], got[1]["index"]), ("edu.school", 1))

    def test_sensitive_never_a_contact_field(self):
        got = run([{"label": "Date of birth", "kind": "text", "autocomplete": "bday"},
                   {"label": "SSN (last 4)", "kind": "text"}, {"label": "Passport number", "kind": "text"}])
        self.assertEqual([g["key"] for g in got], ["sensitive"] * 3)

    def test_two_keys_alike_is_unknown(self):
        got = run([{"label": "Website", "placeholder": "", "kind": "text", "name": "linkedin_or_github"}])
        self.assertIsNone(got[0]["key"])


@unittest.skipUnless(shutil.which("node") and Path(CHROME).exists(), "needs node and Chrome")
class RecordedPagesTest(unittest.TestCase):
    def test_recorded_apply_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = subprocess.run(["node", str(PAGES), CHROME, str(Path(tmp) / "chrome"), str(ROOT / "extension"),
                                  *[str(BOARDS / name) for name in RECORDED]], capture_output=True, text=True, timeout=180)
        got = json.loads(out.stdout.strip().splitlines()[-1])
        for page, want in RECORDED.items():
            keys = {}
            for label, key in got[page]:
                keys.setdefault(label, key)
            for label, key in want.items():
                with self.subTest(page=page, label=label):
                    self.assertIn(label, keys)
                    self.assertEqual(keys[label], key)


if __name__ == "__main__":
    unittest.main()
