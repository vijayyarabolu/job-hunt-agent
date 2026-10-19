"""IMAP: the fallback when Google will not grant gmail.readonly to the unverified app, and the only way
for a job-application address that is not Gmail / Google Workspace. The app password lives in the macOS Keychain
(service "job-hunt-agent-gmail", account = the address), never in the repo or the DB. Read-only: the mailbox is opened with
EXAMINE (readonly=True) and bodies are fetched with BODY.PEEK, so not even the read flag changes.

Google's IMAP: "[Gmail]/All Mail", Gmail's own search and message id (X-GM-MSGID in hex), so links work as with the
API. Any other server: INBOX, SEARCH SINCE <day>, an id made from the Message-ID header, and no web link.
"""
import hashlib
import imaplib
import re
import subprocess
from datetime import datetime

from . import Mail, MailError
from .address import imap_host
from .parse import rfc822_text, when

GOOGLE_HOST = "imap.gmail.com"
SERVICE = "job-hunt-agent-gmail"
GMAIL_BOX = '"[Gmail]/All Mail"'
ADDRESS_KEY = "mail.{}.imap"
HOST_KEY = "mail.{}.imap_host"
MAX_MESSAGES = 1000
_MSGID = re.compile(rb"X-GM-MSGID (\d+)")
_SAFE_SECRET = re.compile(r"^[!#-\[\]-~]{4,128}$")     # printable, no space, quote or backslash
KEYCHAIN_TIMEOUT_S = 30


def password(address: str) -> str | None:
    """The app password from the Keychain, or None."""
    try:
        out = subprocess.run(["security", "find-generic-password", "-s", SERVICE, "-a", address, "-w"],
                             capture_output=True, text=True, timeout=KEYCHAIN_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return (out.stdout.strip() or None) if out.returncode == 0 else None


def store_password(address: str, secret: str | None = None) -> bool:
    """Into the Keychain. With no secret, macOS asks on the terminal; a secret from the app goes through stdin
    (`security -i`), never on a command line."""
    if secret is None:
        out = subprocess.run(["security", "add-generic-password", "-U", "-s", SERVICE, "-a", address, "-w"],
                             timeout=120)
        return out.returncode == 0
    secret = secret.replace(" ", "")                      # Google shows app passwords in groups of four
    if not _SAFE_SECRET.match(secret) or '"' in address or "\\" in address:
        raise ValueError("that app password has characters job-hunt-agent cannot store; paste it without spaces")
    command = f'add-generic-password -U -s {SERVICE} -a "{address}" -w "{secret}"\n'
    out = subprocess.run(["security", "-i"], input=command, capture_output=True, text=True,
                         timeout=KEYCHAIN_TIMEOUT_S)
    return out.returncode == 0 and password(address) == secret


def forget(address: str) -> None:
    subprocess.run(["security", "delete-generic-password", "-s", SERVICE, "-a", address], capture_output=True,
                   timeout=KEYCHAIN_TIMEOUT_S)


class ImapReader:
    kind = "imap"

    def __init__(self, address: str, host: str | None = None, connect=imaplib.IMAP4_SSL):
        self.address, self.host, self._connect = address, host or imap_host(address), connect
        self.google = self.host == GOOGLE_HOST

    def account(self) -> str:
        return self.address

    def check(self) -> None:
        """Sign in once and open the mailbox read-only (Settings' "Use IMAP"); MailError when that fails."""
        secret = password(self.address)
        if not secret:
            raise MailError(f"no app password for {self.address} in the Keychain")
        try:
            with self._connect(self.host) as box:
                box.login(self.address, secret)
                box.select(GMAIL_BOX if self.google else "INBOX", readonly=True)
        except (imaplib.IMAP4.error, OSError) as err:
            raise MailError(f"{self.host} refused {self.address}: {err}") from err

    def since(self, after: datetime, known: set[str] = frozenset()) -> list[Mail]:
        secret = password(self.address)
        if not secret:
            raise MailError(f"no app password for {self.address} in the Keychain: Settings -> Job-application email")
        try:
            with self._connect(self.host) as box:
                box.login(self.address, secret)
                box.select(GMAIL_BOX if self.google else "INBOX", readonly=True)      # EXAMINE: nothing can change
                if self.google:
                    _, data = box.uid("SEARCH", "X-GM-RAW", f'"after:{int(after.timestamp())} -in:sent -in:drafts"')
                else:
                    _, data = box.uid("SEARCH", "SINCE", after.astimezone().strftime("%d-%b-%Y"))
                mails = self._new(box, (data[0] or b"").split(), known)
        except (imaplib.IMAP4.error, OSError) as err:
            raise MailError(f"IMAP read failed for {self.address}: {err}") from err
        return sorted((m for m in mails if m.sent_at > after), key=lambda m: m.sent_at)

    def _new(self, box, uids: list[bytes], known: set[str]) -> list[Mail]:
        """Oldest first, up to MAX_MESSAGES not read before (the next poll continues after them; an id is only known
        once the message is fetched, so the ones read before are skipped on the way)."""
        mails = []
        for uid in uids:
            mail = self._one(box, uid)
            if mail and mail.msg_id not in known:
                mails.append(mail)
                if len(mails) >= MAX_MESSAGES:
                    break
        return mails

    def _one(self, box, uid: bytes) -> Mail | None:
        _, parts = box.uid("FETCH", uid, "(X-GM-MSGID BODY.PEEK[])" if self.google else "(BODY.PEEK[])")
        head = next((p for p in parts if isinstance(p, tuple)), None)
        if head is None:
            return None
        message, text = rfc822_text(head[1])
        found = _MSGID.search(head[0]) if self.google else None
        if found:
            msg_id = format(int(found.group(1)), "x")
        else:
            key = str(message.get("Message-ID") or f"{self.host}:{uid.decode()}")
            msg_id = "imap-" + hashlib.sha1(key.encode()).hexdigest()[:16]
        return Mail(msg_id=msg_id, sender=str(message.get("From") or ""), subject=str(message.get("Subject") or ""),
                    sent_at=when(message.get("Date")), text=text, gmail=bool(found))
