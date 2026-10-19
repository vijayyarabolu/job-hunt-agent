"""Email -> plain text, for both readers: a Gmail API `format=full` payload and an RFC 822 message (IMAP).

text/plain is preferred; an HTML-only email is reduced to its visible text (scripts, styles and tags dropped,
entities decoded). Attachments are skipped.
"""
import base64
import email
import email.policy
import html
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

_DROP = re.compile(r"(?is)<(script|style|head)\b.*?</\1>")
_BREAK = re.compile(r"(?i)<\s*(br|/p|/div|/tr|/li|/h[1-6])\b[^>]*>")
_TAG = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"[ \t\r\f\v]+")
_LINES = re.compile(r"\n\s*\n+")


def html_text(markup: str) -> str:
    text = _BREAK.sub("\n", _DROP.sub(" ", markup or ""))
    text = html.unescape(_TAG.sub(" ", text)).replace(" ", " ")
    return _LINES.sub("\n\n", _SPACES.sub(" ", text)).strip()


def _b64(data: str) -> str:
    raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    return raw.decode("utf-8", "replace")


def _walk_gmail(part: dict, plain: list, rich: list) -> None:
    mime = (part.get("mimeType") or "").lower()
    if part.get("filename"):
        return                                                # an attachment
    data = (part.get("body") or {}).get("data")
    if data and mime == "text/plain":
        plain.append(_b64(data))
    elif data and mime == "text/html":
        rich.append(_b64(data))
    for child in part.get("parts") or []:
        _walk_gmail(child, plain, rich)


def gmail_text(payload: dict) -> str:
    plain, rich = [], []
    _walk_gmail(payload or {}, plain, rich)
    if any(p.strip() for p in plain):
        return "\n".join(plain).strip()
    return "\n".join(html_text(r) for r in rich).strip()


def header(headers: list[dict], name: str) -> str:
    wanted = name.lower()
    return next((h.get("value") or "" for h in headers or [] if (h.get("name") or "").lower() == wanted), "")


def rfc822_text(raw: bytes) -> tuple[email.message.EmailMessage, str]:
    """The parsed message and its text."""
    message = email.message_from_bytes(raw, policy=email.policy.default)
    plain, rich = [], []
    for part in message.walk():
        if part.is_multipart() or part.get_filename():
            continue
        kind = part.get_content_type()
        if kind not in ("text/plain", "text/html"):
            continue
        try:
            content = part.get_content()
        except (LookupError, ValueError):                     # an unknown charset: best effort
            content = (part.get_payload(decode=True) or b"").decode("utf-8", "replace")
        (plain if kind == "text/plain" else rich).append(content)
    text = "\n".join(plain) if any(p.strip() for p in plain) else "\n".join(html_text(r) for r in rich)
    return message, text.strip()


def when(value: str | None, fallback: datetime | None = None) -> datetime:
    """A Date header as UTC; the fallback (the server's own time) when it is missing or unreadable."""
    try:
        moment = parsedate_to_datetime(value) if value else None
    except (TypeError, ValueError):
        moment = None
    if moment is None:
        return fallback or datetime.now(timezone.utc)
    return (moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
