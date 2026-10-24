"""Atomic writes (atomic.py): every write has its own temporary file beside the target, so writers of the same file
never race on one shared name (CI: two saves of one tailored resume, the second replace() found no data/tailor/1.tmp)."""
import json
import stat
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from job_hunt_agent import atomic, config, secrets_store, tailor
from job_hunt_agent.google import auth

THREADS = 8
ROUNDS = 25


def hammer(work) -> list[BaseException]:
    """Runs work(n, i) from THREADS threads at once, ROUNDS times each; the exceptions they raised."""
    errors, start = [], threading.Barrier(THREADS)

    def run(n):
        start.wait()
        for i in range(ROUNDS):
            try:
                work(n, i)
            except BaseException as err:                # noqa: BLE001 - collected for the assertion
                errors.append(err)

    threads = [threading.Thread(target=run, args=(n,)) for n in range(THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return errors


class AtomicTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def leftovers(self, folder: Path) -> list[str]:
        return sorted(p.name for p in folder.iterdir() if p.name.endswith(".tmp"))

    def test_concurrent_writes_of_one_file_all_land_and_leave_no_temp_file(self):
        target = self.dir / "sub" / "x.json"
        self.assertEqual(hammer(lambda n, i: atomic.write_text(target, json.dumps({"n": n, "i": i}))), [])
        self.assertIn(json.loads(target.read_text())["n"], range(THREADS))      # one whole write, never a mix
        self.assertEqual(self.leftovers(target.parent), [])

    def test_mode_is_set_and_a_failed_write_keeps_the_old_file(self):
        target = self.dir / "secret.yaml"
        atomic.write_text(target, "old", 0o600)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        with self.assertRaises(RuntimeError), atomic.replacing(target) as part:
            part.write_text("half")
            raise RuntimeError("disk full")
        self.assertEqual(target.read_text(), "old")
        self.assertEqual(self.leftovers(self.dir), [])

    def test_the_same_tailored_job_saved_from_several_threads(self):
        with mock.patch.object(config, "DATA", self.dir / "data"):
            errors = hammer(lambda n, i: tailor._save({"id": 1, "status": "ready", "by": n, "round": i}))
            self.assertEqual(errors, [])
            self.assertEqual(tailor._load(1)["id"], 1)
            self.assertEqual(self.leftovers(tailor.folder()), [])

    def test_secrets_and_google_tokens_saved_from_several_threads(self):
        target = self.dir / "config" / "secrets.yaml"
        token = self.dir / "data" / "token.json"
        self.assertEqual(hammer(lambda n, i: secrets_store._write_yaml(target, {"k": f"{n}-{i}"})), [])
        self.assertEqual(hammer(lambda n, i: auth.save({"access_token": f"{n}-{i}"}, path=token)), [])
        for path in (target, token):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600, path)
            self.assertEqual(self.leftovers(path.parent), [])
        self.assertTrue(json.loads(token.read_text())["access_token"])

    def test_no_module_writes_a_fixed_temporary_name(self):
        root = Path(__file__).resolve().parent.parent / "job_hunt_agent"
        fixed = [f"{p.relative_to(root)}:{n}" for p in root.rglob("*.py")
                 for n, line in enumerate(p.read_text().splitlines(), 1)
                 if 'with_suffix(".tmp")' in line or '+ ".part")' in line or 'with_suffix(".part")' in line]
        self.assertEqual(fixed, [])


if __name__ == "__main__":
    unittest.main()
