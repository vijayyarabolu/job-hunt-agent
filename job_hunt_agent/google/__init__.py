"""Google Sheets over stdlib urllib (decision 006; GOAL4 §4, decision 008). No Google client libraries.

Base URLs are the real Google endpoints unless JOB_HUNT_AGENT_GOOGLE_BASE points at a loopback fake (tests/fake_google.py);
a non-loopback override is refused so a stray environment variable can never send the token elsewhere.
"""
import os
from urllib.parse import urlsplit

# edit the person's existing sheets; drive.file (files this app creates only) for the uploaded resumes
SCOPES = ("https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive.file")
REAL = {"auth": "https://accounts.google.com/o/oauth2/v2/auth", "token": "https://oauth2.googleapis.com/token",
        "revoke": "https://oauth2.googleapis.com/revoke",
        "drive": "https://www.googleapis.com/drive/v3", "upload": "https://www.googleapis.com/upload/drive/v3",
        "sheets": "https://sheets.googleapis.com/v4", "gmail": "https://gmail.googleapis.com/gmail/v1"}
FAKE_PATHS = {"auth": "/o/oauth2/v2/auth", "token": "/token", "revoke": "/revoke", "drive": "/drive/v3",
              "upload": "/upload/drive/v3", "sheets": "/v4", "gmail": "/gmail/v1"}
LOOPBACK = ("127.0.0.1", "localhost")
FOLDER = "application/vnd.google-apps.folder"
GDOC = "application/vnd.google-apps.document"
GSHEET = "application/vnd.google-apps.spreadsheet"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PDF = "application/pdf"


class GoogleError(Exception):
    """A Google call failed for good. status is None for transport errors and timeouts."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class NotConfigured(GoogleError):
    pass


class NeedsConsent(GoogleError):
    """The refresh token lapsed (Testing-mode tokens last 7 days) or was revoked: the user connects again."""

    def __init__(self, message: str = ""):
        super().__init__(message or "Google access lapsed or was revoked: connect again in Settings > Setup")


def base(name: str) -> str:
    override = os.environ.get("JOB_HUNT_AGENT_GOOGLE_BASE")
    if not override:
        return REAL[name]
    parts = urlsplit(override)
    if parts.scheme != "http" or parts.hostname not in LOOPBACK:
        raise NotConfigured(f"JOB_HUNT_AGENT_GOOGLE_BASE must be a loopback http URL (a test fake), not {override!r}")
    return override.rstrip("/") + FAKE_PATHS[name]
