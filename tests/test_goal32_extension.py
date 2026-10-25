"""GOAL32 §3: after the extension is reloaded, an old tab's content script throws "Uncaught (in promise) Error: Extension
context invalidated" (content/core.js). Every chrome.* call now fails quietly and the panel says "Extension updated:
refresh this page" once. Driven by tests/ui/goal32_stale.mjs (the real core.js in a VM with a stub chrome)."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXT = ROOT / "extension"
RUNNER = Path(__file__).parent / "ui" / "goal32_stale.mjs"
STALE = "Extension updated: refresh this page"
RUN_TIMEOUT_S = 60


@unittest.skipUnless(shutil.which("node"), "needs node")
class StaleContextTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        run = subprocess.run(["node", str(RUNNER), str(EXT / "content" / "core.js")], capture_output=True, text=True,
                             timeout=RUN_TIMEOUT_S, check=False)
        if run.returncode:
            raise AssertionError(run.stderr)
        cls.out = json.loads(run.stdout.strip().splitlines()[-1])

    def test_calls_work_while_the_extension_is_there(self):
        self.assertEqual(self.out["aliveSend"], {"ok": True, "data": "ping"})
        self.assertEqual(self.out["aliveStore"], {"base": "http://127.0.0.1:8765"})
        self.assertTrue(self.out["aliveSave"])

    def test_a_stale_tab_never_throws(self):
        for key in ("deadSend", "deadApi", "deadStore", "deadSave"):
            self.assertNotIn("threw", self.out[key], key)
        self.assertEqual(self.out["deadSend"]["value"], {"ok": False, "error": STALE})
        self.assertEqual(self.out["deadStore"]["value"], {})
        self.assertTrue(self.out["rejectionPrevented"])           # a stray chrome.* promise stays off the console

    def test_the_panel_says_so_once(self):
        self.assertEqual(self.out["notices"], [{"stale": STALE}])

    def test_no_content_script_calls_chrome_storage_or_messages_directly(self):
        for path in (EXT / "content").rglob("*.js"):
            if path.name == "core.js":
                continue
            text = path.read_text()
            for call in ("chrome.storage.local.get(", "chrome.storage.local.set(", "chrome.runtime.sendMessage(",
                         "chrome.runtime.getManifest("):
                self.assertNotIn(call, text, f"{path.name}: {call} (use JH.store / JH.save / JH.send / JH.version)")

    def test_the_version_is_1_6_5(self):
        self.assertEqual(json.loads((EXT / "manifest.json").read_text())["version"], "1.7.0")   # GOAL36 §5: the scan, Link to a job, Generate reasons
        self.assertEqual(self.out["version"], "1.7.0")   # GOAL36 §5: the scan, Link to a job, Generate reasons


if __name__ == "__main__":
    unittest.main()
