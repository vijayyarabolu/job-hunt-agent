"""the test run must never reach the live resumegen on this Mac (port 8770). Its log showed job-hunt-agent test
profiles ("tester", "other") asking it for jobs; a write there would queue a real generation."""
import unittest

from job_hunt_agent import rgclient


class LiveResumegenGuardTest(unittest.TestCase):
    def test_an_unpatched_call_never_reaches_port_8770(self):
        self.assertNotIn(":8770", rgclient._base())

    def test_an_unpatched_call_is_offline(self):
        with self.assertRaises(rgclient.Offline):
            rgclient.request("GET", "/api/jobs?profile=tester")


if __name__ == "__main__":
    unittest.main()
