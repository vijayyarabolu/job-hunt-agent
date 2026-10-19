"""Browser tests never write to ~/Downloads: the shared harness (tests/ui/cdp.mjs) sends Chrome's downloads to the
test's temp dir and deletes them on close. The user found Tess_ML*.pdf copies in their real Downloads folder."""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.test_goal7_ui import CHROME

RUNNER = Path(__file__).parent / "ui" / "downloads.mjs"
REAL_DOWNLOADS = Path.home() / "Downloads"
RUN_TIMEOUT_S = 60


def _names(folder: Path) -> set[str]:
    return {p.name for p in folder.iterdir()} if folder.is_dir() else set()


@unittest.skipUnless(shutil.which("node") and Path(CHROME).exists(), "needs node and Google Chrome")
class DownloadsStayInTempTest(unittest.TestCase):
    def test_a_download_lands_in_the_temp_folder_and_is_deleted(self):
        before = _names(REAL_DOWNLOADS)
        with tempfile.TemporaryDirectory() as tmp:
            out = subprocess.run(["node", str(RUNNER), CHROME, str(Path(tmp) / "chrome")],
                                 capture_output=True, text=True, timeout=RUN_TIMEOUT_S)
            lines = [ln for ln in out.stdout.splitlines() if ln.startswith("{")]
            self.assertTrue(lines, out.stderr)
            got = json.loads(lines[-1])
            self.assertTrue(got["downloads"].startswith(tmp), got["downloads"])
        for c in got["checks"]:
            with self.subTest(c["name"]):
                self.assertTrue(c["ok"], c["detail"])
        new = {n for n in _names(REAL_DOWNLOADS) - before if n.startswith("ZZ_TEST")}
        self.assertFalse(new, f"the test's download reached ~/Downloads: {new}")


if __name__ == "__main__":
    unittest.main()
