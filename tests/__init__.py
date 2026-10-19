"""Test package. Tests run against a throwaway app home holding the two fictional sample profiles (never
~/.job-hunt-agent), with no Anthropic key and no notifications. No test may reach the internet: every urlopen to a
host other than this machine fails like an unreachable network. The fake Google and the fake ATS servers run on
127.0.0.1, so they still answer; a test that mocks urlopen replaces this guard for its duration."""
import atexit
import os
import shutil
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

_HOME = Path(tempfile.mkdtemp(prefix="jha-tests-"))
os.environ["JOB_HUNT_AGENT_HOME"] = str(_HOME)           # before job_hunt_agent.config is imported
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("JOB_HUNT_AGENT_DEMO", None)
os.environ["JOB_HUNT_AGENT_NO_NOTIFY"] = "1"
atexit.register(shutil.rmtree, _HOME, True)

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
_real_urlopen = urllib.request.urlopen


def _local_only(req, *args, **kwargs):
    url = req.full_url if isinstance(req, urllib.request.Request) else str(req)
    if (urlsplit(url).hostname or "") not in LOCAL_HOSTS:
        raise urllib.error.URLError(f"network disabled in tests: {urlsplit(url).hostname}")
    return _real_urlopen(req, *args, **kwargs)


urllib.request.urlopen = _local_only


def _sample_profiles() -> None:
    from job_hunt_agent import sample
    sample.write(_HOME / "profiles")


_sample_profiles()


def _no_background_google() -> None:
    """Mark applied saves a JD Doc in a background thread; in tests it is off unless a test opts in
    (jd_doc.BACKGROUND)."""
    from job_hunt_agent import jd_doc
    jd_doc.BACKGROUND = False


_no_background_google()


def _no_live_resume_service() -> None:
    """Unpatched resume-service calls go to the discard port and come back Offline; a test that wants a service
    patches rgclient._base / request with its own fake. Tests of the built-in tailor patch _base to ""."""
    from job_hunt_agent import rgclient
    rgclient._base = lambda: "http://127.0.0.1:9"


_no_live_resume_service()
