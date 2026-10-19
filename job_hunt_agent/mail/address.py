"""The job-application address each person types in Settings (10:15 update): is it Gmail, Google
Workspace (a company domain whose mail Google hosts: MX at google.com) or something else (IMAP only), and is the
account that signed in the same mailbox (Gmail ignores dots and +tags; googlemail.com is gmail.com)?"""
import re
import subprocess

_ADDRESS = re.compile(r"^[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
GMAIL_DOMAINS = ("gmail.com", "googlemail.com")
GOOGLE_MX = ("google.com.", "googlemail.com.", "google.com", "googlemail.com")
IMAP_HOSTS = {"gmail": "imap.gmail.com", "google": "imap.gmail.com", "outlook.com": "outlook.office365.com",
              "hotmail.com": "outlook.office365.com", "live.com": "outlook.office365.com",
              "msn.com": "outlook.office365.com", "yahoo.com": "imap.mail.yahoo.com", "icloud.com": "imap.mail.me.com",
              "me.com": "imap.mail.me.com", "aol.com": "imap.aol.com"}
MX_TIMEOUT_S = 5


def valid(address: str | None) -> str:
    """The address trimmed, or ValueError."""
    text = (address or "").strip()
    if not _ADDRESS.match(text):
        raise ValueError(f"{text or 'that'} is not an email address")
    return text


def domain(address: str) -> str:
    return address.rsplit("@", 1)[-1].lower()


def mailbox(address: str) -> str:
    """The canonical mailbox: lower case; for Gmail without dots and +tags."""
    local, _, host = address.strip().lower().rpartition("@")
    if host in GMAIL_DOMAINS:
        return local.split("+", 1)[0].replace(".", "") + "@gmail.com"
    return f"{local}@{host}"


def same_mailbox(a: str | None, b: str | None) -> bool:
    return bool(a and b) and mailbox(a) == mailbox(b)


def mx_hosts(host: str) -> list[str]:
    try:
        out = subprocess.run(["dig", "+short", "MX", host], capture_output=True, text=True, timeout=MX_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [line.split()[-1].lower() for line in out.stdout.splitlines() if line.strip()]


def provider(address: str, lookup=mx_hosts) -> str:
    """'gmail', 'google' (Workspace), 'other', or 'unknown' (no answer from DNS: Google sign-in may still work)."""
    host = domain(address)
    if host in GMAIL_DOMAINS:
        return "gmail"
    found = lookup(host)
    if not found:
        return "unknown"
    return "google" if any(h.endswith(GOOGLE_MX) for h in found) else "other"


def imap_host(address: str, kind: str | None = None) -> str:
    """The IMAP server to try for this address (Gmail / Workspace: Google's; well-known hosts; else imap.<domain>)."""
    host = domain(address)
    return IMAP_HOSTS.get(kind or "") or IMAP_HOSTS.get(host) or f"imap.{host}"
