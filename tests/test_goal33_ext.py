"""GOAL33 §5 (found by the end-to-end run on copies): Save and Continue pressed while Autofill is still finishing a
step. The next step used to be skipped (its auto-fill was dropped while the last one ran) and the first step's
fields were logged under the next step's name. GOAL23's fake person and fixtures; driven by tests/ui/goal33_ext.mjs."""
import unittest
from pathlib import Path
from unittest import mock

from job_hunt_agent import db
from tests import test_goal23_extension as g23

RUNNER = Path(__file__).parent / "ui" / "goal33_ext.mjs"


@unittest.skipUnless(g23.shutil.which("node") and Path(g23.CHROME).exists(), "needs node and Google Chrome")
class NextWhileFillingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with mock.patch.object(g23, "RUNNER", RUNNER):
            g23.ExtensionWorkdayTest.setUpClass.__func__(cls)
        conn = db.connect(Path(cls.tmp.name) / "ext.db")
        cls.steps = {r["label"]: r["step"] for r in conn.execute("SELECT label, step FROM autofill_fields ORDER BY id")}
        conn.close()

    @classmethod
    def tearDownClass(cls):
        g23.ExtensionWorkdayTest.tearDownClass.__func__(cls)

    def check(self, name):
        by_name = {c["name"]: c for c in self.checks}
        self.assertNotIn("runner", by_name, by_name.get("runner", {}).get("detail"))
        self.assertIn(name, by_name, "check did not run")
        self.assertTrue(by_name[name]["ok"], by_name[name]["detail"])

    def test_the_next_step_is_still_filled(self):
        self.check("My Information starts filling")
        self.check("My Experience is still filled on its own")

    def test_the_first_steps_fields_are_logged_under_it(self):
        self.assertEqual(self.steps.get("First Name"), "My Information", self.steps)
