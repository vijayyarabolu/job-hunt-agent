"""Text contrast, readable with red-green colour blindness: every button and input on the job-hunt-agent site, the Autofill
panel and the extension's options page reads at 4.5:1 or better (3:1 for an icon-only button) in every state
(normal, hover, focus, pressed, disabled), light and dark; a job site's CSS cannot restyle the panel's buttons.
Runs tests/ui/contrast.mjs in headless Chrome against a throwaway job-hunt-agent (temp DB, test people, fake resumegen).
Skipped without node / Chrome."""
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from job_hunt_agent import config, db, rgclient, server
from tests.test_goal7_ui import CHROME
from tests.test_goal8_flow import PROFILE, TOKEN as RG_TOKEN, FakeResumegen
from tests.test_goal13_ui import PEOPLE, seed

RUNNER = Path(__file__).parent / "ui" / "contrast.mjs"
EXT = Path(__file__).parent.parent / "extension"
FIX = Path(__file__).parent / "fixtures" / "autofill"
TOKEN = "contrast-test-token"
RUN_TIMEOUT_S = 900
SHOWN = 40
# a job site at its worst: every element, every button state and the panel's host restyled, its colour names taken
HOSTILE = """<!doctype html><html><head><meta charset="utf-8"><title>Apply</title><style>
:root, [data-jh-panel] { --blue: #fff; --bg: #fff; --ink: #fff; --soft: #fff; --line: #fff; --red: #fff; --red-bg: #fff;
  --orange: #fff; --grey: #fff; --quiet: #fff; color-scheme: dark; }
*, *::before, *::after { color: #fff !important; background: #fff !important; border-color: #fff !important; }
button, button:hover, button:focus, button:active, button:disabled, a:hover { color: #fff !important; background: #fff !important;
  opacity: .3 !important; filter: invert(1) !important; }
div { opacity: .5 !important; }
[data-jh-panel] { all: unset !important; display: block !important; filter: invert(1) !important; opacity: .3 !important;
  mix-blend-mode: difference !important; color: #fff !important; }
</style></head><body><h1>Apply: AI Engineer</h1><form>
<label for="fn">First name</label><input id="fn" name="first_name">
<label for="em">Email</label><input id="em" name="email" type="email">
<label for="ph">Phone</label><input id="ph" name="phone">
<button type="button">Next</button></form></body></html>"""


@unittest.skipUnless(shutil.which("node") and Path(CHROME).exists(), "needs node and Google Chrome")
class ContrastTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        for pid, name in PEOPLE.items():
            (root / "profiles" / pid / "resumes").mkdir(parents=True)
            (root / "profiles" / pid / "profile.yaml").write_text(
                PROFILE.replace("Tess Tester", name).replace("families: [ml_engineer]", "families: [ml_engineer, ai_engineer]"))
        hostile = root / "hostile.html"
        hostile.write_text(HOSTILE)
        cls.rg = FakeResumegen()
        cls.patches = [mock.patch.object(config, "DB_PATH", root / "c.db"), mock.patch.object(config, "PROFILES", root / "profiles"),
                       mock.patch.object(rgclient, "_base", lambda: cls.rg.url), mock.patch.object(rgclient, "_token", lambda: RG_TOKEN)]
        for p in cls.patches:
            p.start()
        conn = db.connect()
        seed(conn)
        conn.close()
        cls.srv = server.make_server(lan=False, port=0)
        cls.srv.token = TOKEN
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        out = subprocess.run(["node", str(RUNNER), f"http://localhost:{cls.srv.server_port}", TOKEN, CHROME, str(root / "chrome"),
                              str(EXT), str(FIX), str(hostile)], capture_output=True, text=True, timeout=RUN_TIMEOUT_S)
        lines = [ln for ln in out.stdout.splitlines() if ln.startswith("{")]
        got = json.loads(lines[-1]) if lines else {"checks": [{"name": "runner", "ok": False, "detail": out.stderr[-3000:]}], "failures": []}
        cls.checks, cls.failures = got["checks"], got["failures"]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        for p in reversed(cls.patches):
            p.stop()
        cls.rg.stop()
        cls.tmp.cleanup()

    def test_every_view_was_checked(self):
        for c in self.checks:
            with self.subTest(c["name"]):
                self.assertTrue(c["ok"], c["detail"])
        names = " ".join(c["name"] for c in self.checks)
        for part in ("tab recommended", "a job's panel", "Not interested: the reason box (empty)", "Settings",
                     "extension options page", "panel on the Ashby page", "panel error box on a page that restyles everything"):
            self.assertIn(part, names)

    def test_every_control_reads_at_aa_in_every_state(self):
        lines = [f"{f['ratio']}:1 (needs {f['need']}) {f['scheme']} {f['state']}: {f['what']} [{f['fg']} on {f['bg']}] ({f['view']})"
                 for f in self.failures]
        self.assertEqual(lines[:SHOWN], [], f"{len(lines)} low-contrast controls")
