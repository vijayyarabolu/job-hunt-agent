"""GOAL30-app §1 #4: profile.yaml and settings.yaml were parsed again on every call: 29 times for one Recommended
list (the H-1B badge's first name), twice per job page, ~8 ms each. A file is now parsed once per change (its
mtime and size); every caller gets its own copy, so nothing one caller changes reaches the next."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from job_hunt_agent import config


class YamlKeptTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "profile.yaml"
        self.path.write_text("name: Test Person\nfamilies: [ml_engineer]\nanswers: {years: 4}\n")
        config._YAML_KEPT.clear()

    def tearDown(self):
        self.dir.cleanup()

    def _bump(self, text: str) -> None:
        stat = self.path.stat()
        self.path.write_text(text)
        os.utime(self.path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))

    def test_a_file_is_parsed_once_until_it_changes(self):
        real = config.yaml.safe_load
        with mock.patch.object(config.yaml, "safe_load", side_effect=real) as parse:
            first = config._read_yaml(self.path, {})
            again = config._read_yaml(self.path, {})
            self.assertEqual(parse.call_count, 1)
            self._bump("name: Other Person\nfamilies: [ml_engineer]\n")
            changed = config._read_yaml(self.path, {})
            self.assertEqual(parse.call_count, 2)
        self.assertEqual(first, again)
        self.assertEqual(changed["name"], "Other Person")

    def test_each_caller_gets_its_own_copy(self):
        mine = config._read_yaml(self.path, {})
        mine["answers"]["years"] = 99
        mine["name"] = "changed"
        fresh = config._read_yaml(self.path, {})
        self.assertEqual(fresh["answers"]["years"], 4)
        self.assertEqual(fresh["name"], "Test Person")

    def test_missing_empty_and_bad_files_behave_as_before(self):
        self.assertEqual(config._read_yaml(Path(self.dir.name) / "nope.yaml", {"d": 1}), {"d": 1})
        self._bump("")
        self.assertEqual(config._read_yaml(self.path, []), [])
        self._bump("name: [unclosed\n")
        with self.assertRaises(config.ConfigError):
            config._read_yaml(self.path, {})


if __name__ == "__main__":
    unittest.main()
