"""GOAL34: Download PDF (the cover letter) and Download resume in the Autofill panel, the Dice finding (its drop zone
ignores a file Autofill puts in it: the panel says so beside Download PDF) and the Dice file card's label. The browser
part is tests/ui/goal34_download.mjs on GOAL23's fake person and fixtures; the names are checked for both profiles'
naming styles (synthetic profile copies, never the real profiles folder)."""
import base64
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from job_hunt_agent import applyback, config, db, resume_upload
from job_hunt_agent.autofill import api, assist, resume
from tests import test_goal23_extension as g23

RUNNER = Path(__file__).parent / "ui" / "goal34_download.mjs"
EXT = Path(__file__).resolve().parent.parent / "extension"


@unittest.skipUnless(g23.shutil.which("node") and Path(g23.CHROME).exists(), "needs node and Google Chrome")
class DownloadButtonsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        real_run = subprocess.run
        runs = []

        def keep(*a, **k):                                # the runner's stdout also carries the downloaded files
            out = real_run(*a, **k)
            runs.append(out)
            return out

        # like both real profiles, the fake person has a base resume PDF (the cover letter's name is built on it)
        cls.base_dir = tempfile.TemporaryDirectory()
        base_pdf = Path(cls.base_dir.name) / "Zz_Testcase_AI_Engineer_Resume.pdf"
        base_pdf.write_bytes(b"%PDF-1.4\n% ZZ_TEST base resume\n")
        cls.base_patch = mock.patch.object(applyback, "base_pdf", lambda pid, rid: base_pdf)
        cls.base_patch.start()
        with mock.patch.object(g23, "RUNNER", RUNNER), mock.patch.object(g23.subprocess, "run", keep):
            g23.ExtensionWorkdayTest.setUpClass.__func__(cls)
        lines = [ln for ln in runs[-1].stdout.splitlines() if ln.startswith("{")] if runs else []
        cls.files = {k: base64.b64decode(v) for k, v in (json.loads(lines[-1]).get("files") or {}).items()} if lines else {}
        conn = db.connect()                               # GOAL23's patches still point at the throwaway DB
        cls.server_cover = api.get(conn, g23.PID, "/api/autofill/cover.pdf", {"uid": cls.uid}).data
        conn.close()

    @classmethod
    def tearDownClass(cls):
        g23.ExtensionWorkdayTest.tearDownClass.__func__(cls)
        cls.base_patch.stop()
        cls.base_dir.cleanup()

    def group(self, names):
        by_name = {c["name"]: c for c in self.checks}
        self.assertNotIn("runner", by_name, by_name.get("runner", {}).get("detail"))
        for name in names:
            with self.subTest(name):
                self.assertIn(name, by_name, "check did not run")
                self.assertTrue(by_name[name]["ok"], by_name[name]["detail"])

    def test_buttons_render_on_every_board(self):
        boards = ["Greenhouse", "Ashby", "Lever", "Dice"]
        self.group(["Workday: the panel shows the matched job", "Workday: Download PDF renders with Paste and Upload",
                    "Workday: Download resume renders"]
                   + [f"{b}: {what}" for b in boards for what in ("the panel shows the job", "Download PDF renders",
                                                                  "Download resume renders", "every cover letter button has its icon")])

    def test_download_pdf_is_upload_as_pdf_saved(self):
        self.group(["Greenhouse: Upload as PDF fills the cover letter box", "Download PDF saves the cover letter under its upload name",
                    "Download PDF gives the same bytes as Upload as PDF"])
        self.assertEqual(self.files.get("cover"), self.server_cover)
        self.assertTrue(self.files["cover"].startswith(b"%PDF-"))

    def test_download_resume_is_the_tailored_pdf(self):
        self.group(["Download resume saves the tailored PDF under the base resume's name", "the panel says where the resume went"])
        self.assertEqual(self.files.get("resume"), self.pdf)

    def test_dice_says_why_and_offers_download(self):
        self.group(["Dice: Upload as PDF is not offered", "Dice: the panel says why, with Download PDF right there",
                    "Dice: Download PDF saves the cover letter", "Dice: nothing clicked Next or the drop zone"])

    def test_no_cover_button_is_silent(self):
        self.group(["Lever: Upload as PDF with no cover letter box says so", "Dice: Paste with no cover letter box says so"])

    def test_dice_file_label_is_the_card_heading(self):
        self.group(["Dice: the cover letter file box is named by its heading", "Dice: the Skipped row reads Cover letter, not the whole card"])

    def test_no_page_error(self):
        self.group(["the extension reaches job-hunt-agent", "no page error"])


PROFILE = """name: {name}
families: [ai_engineer]
resumes:
  - {{id: ai_engineer, label: AI Engineer, file: {file}, families: [ai_engineer]}}
"""


class FileNamesTest(unittest.TestCase):
    """Both downloads keep the names Autofill uploads under: each person's own base resume name, the cover letter
    with Cover_Letter in place of Resume (synthetic copies of the two profiles' resumes: lines)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        for pid, name, file in (("alex", "Alex Rivera", "Alex_Rivera_AI_Engineer_Resume.docx"),
                                ("jordan", "Jordan Lee", "Jordan_Lee_AI_Engineer_Resume.docx")):
            (root / pid).mkdir()
            (root / pid / "profile.yaml").write_text(PROFILE.format(name=name, file=file))
        patch = mock.patch.object(config, "PROFILES", root)
        patch.start()
        self.addCleanup(patch.stop)

    def names(self, pid):
        profile = {**config.profile(pid), "id": pid}
        upload = resume_upload.upload_name(profile, {"resume_used": "ai_engineer", "role_title": "AI Engineer"}, ".pdf")
        with mock.patch.object(resume, "pick", lambda *a, **k: {"file_name": upload}):
            return upload, assist.cover_name(None, pid, "uid-zz", None)

    def test_alex(self):
        self.assertEqual(self.names("alex"), ("Alex_Rivera_AI_Engineer_Resume.pdf", "Alex_Rivera_AI_Engineer_Cover_Letter.pdf"))

    def test_jordan_uses_her_own_names(self):
        self.assertEqual(self.names("jordan"), ("Jordan_Lee_AI_Engineer_Resume.pdf", "Jordan_Lee_AI_Engineer_Cover_Letter.pdf"))


class ExtensionSourceTest(unittest.TestCase):
    def test_version_is_1_6_4(self):
        self.assertEqual(json.loads((EXT / "manifest.json").read_text())["version"], "1.7.0")   # GOAL36 §5: the scan, Link to a job, Generate reasons

    def test_no_new_permission(self):                     # a blob link, not chrome.downloads: Chrome asks nothing new
        self.assertEqual(json.loads((EXT / "manifest.json").read_text())["permissions"], ["storage"])

    def test_new_ui_text_has_no_dashes(self):
        text = (EXT / "content" / "panel.js").read_text() + (EXT / "content" / "main.js").read_text()
        for line in ("Download PDF", "Download resume", "doesn't accept uploads from Autofill: use Download PDF and drop it in",
                     "No cover letter upload found on this page: use Download PDF and drop it in",
                     "No cover letter box found on this page: use Copy, or Download PDF and drop it in"):
            self.assertIn(line, text)
            self.assertNotRegex(line, "[-–—]")


if __name__ == "__main__":
    unittest.main()
