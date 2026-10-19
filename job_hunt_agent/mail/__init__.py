"""read each person's job emails (read-only) and move their applications' status.

Read-only by construction: the Gmail sign-in asks for `gmail.readonly` alone (its own token per person, separate from
the Sheets / Drive token), the only Gmail calls are GET list / get / profile, and the IMAP fallback opens the mailbox
with EXAMINE (read-only) and fetches with BODY.PEEK. Bodies are read in memory to classify and match, never stored:
mail_messages keeps sender, subject, date, message id, a <= 300-character snippet, the classification and the link.
"""
from dataclasses import dataclass
from datetime import datetime

GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
SCOPES = (GMAIL_SCOPE,)
START_DAY = "2026-10-08"            # user, GOAL12: "do it for today" - no look-back before this local midnight
WEB = "https://mail.google.com/"     # the only mail links shown (built here from the message id)
LINK = WEB + "mail/u/0/#all/{}"
SNIPPET_MAX = 300
KINDS = ("rejected", "screen", "interview", "offer", "received", "other")
STATUS_OF = {"rejected": "rejected", "screen": "screen", "interview": "interview", "offer": "offer"}


class MailError(Exception):
    """Reading mail failed (network, quota, a revoked sign-in); the poll reports it and tries again later."""


@dataclass(frozen=True)
class Mail:
    """One email as read: `text` is the plain body, held in memory only."""
    msg_id: str
    sender: str                     # 'Name <addr@host>' as sent
    subject: str
    sent_at: datetime               # UTC
    text: str
    gmail: bool = True              # a Gmail message id (has a web link); False: another IMAP server

    @property
    def snippet(self) -> str:
        flat = " ".join(self.text.split())
        return flat if len(flat) <= SNIPPET_MAX else flat[:SNIPPET_MAX - 1].rstrip() + "…"

    @property
    def link(self) -> str | None:
        return LINK.format(self.msg_id) if self.gmail else None
