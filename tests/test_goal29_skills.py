"""GOAL29 §4: Workday's Skills box, seen live on 2026-10-19 (IRC): picks were saved with their first letters cut off
("hain", "ve AI", "Smith", "loudWatch") because the next skill was typed before Workday cleared the box from the last
one, and that tenant saves free text on Enter. The fix (content/fill.js, boards/workday.js): wait for an empty, focused
box; type; check the box reads the skill exactly (clear and retype up to 2 times); only then press Enter; check the new
pick reads the skill, else remove it and say so in the panel notes. Driven by tests/ui/goal29_skills.mjs in headless
Chrome against the Workday simulator in slow-clear / free-text / wipe-once / mangle modes. Skipped without node or
Chrome (JH_CHROME names another Chromium)."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHROME = os.environ.get("JH_CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
RUNNER = Path(__file__).parent / "ui" / "goal29_skills.mjs"
FIXTURES = Path(__file__).parent / "fixtures" / "autofill"
RUN_TIMEOUT_S = 240


@unittest.skipUnless(shutil.which("node") and Path(CHROME).exists(), "needs node and Chrome")
class WorkdaySkillsTypingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as tmp:
            out = subprocess.run(["node", str(RUNNER), CHROME, str(Path(tmp) / "chrome"), str(ROOT / "extension"), str(FIXTURES)],
                                 capture_output=True, text=True, timeout=RUN_TIMEOUT_S)
        lines = [ln for ln in out.stdout.splitlines() if ln.startswith("{")]
        cls.checks = json.loads(lines[-1])["checks"] if lines else [{"name": "runner", "ok": False, "detail": out.stderr[-3000:]}]

    def test_every_check(self):
        self.assertTrue(self.checks)
        for c in self.checks:
            with self.subTest(c["name"]):
                self.assertTrue(c["ok"], c["detail"])


class VersionTest(unittest.TestCase):
    def test_the_extension_version_is_bumped(self):
        version = json.loads((ROOT / "extension" / "manifest.json").read_text())["version"]
        self.assertGreaterEqual(tuple(map(int, version.split("."))), (1, 4, 0))
