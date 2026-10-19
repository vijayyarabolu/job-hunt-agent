"""every Claude call job-hunt-agent makes uses claude-sonnet-5-5 with --effort medium (the nightly mail read and the
rules' fallback classifier alike)."""
import subprocess
import unittest
from unittest import mock

from job_hunt_agent.mail import claude, llm


def _flag(cmd: list, name: str) -> str | None:
    return cmd[cmd.index(name) + 1] if name in cmd else None


class ModelsTest(unittest.TestCase):
    def _cmd(self, call) -> list:
        seen = []

        def fake(cmd, **_kw):
            seen.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")
        with mock.patch.object(subprocess, "run", fake):
            call()
        return seen[0]

    def test_rules_fallback_classifier_uses_sonnet_medium(self):
        cmd = self._cmd(lambda: llm.run_claude("x"))
        self.assertEqual((_flag(cmd, "--model"), _flag(cmd, "--effort")), ("claude-sonnet-5-5", "medium"))

    def test_nightly_mail_read_uses_sonnet_medium(self):
        cmd = self._cmd(lambda: claude.run_claude("x", "alex"))
        self.assertEqual((_flag(cmd, "--model"), _flag(cmd, "--effort")), ("claude-sonnet-5-5", "medium"))


if __name__ == "__main__":
    unittest.main()
