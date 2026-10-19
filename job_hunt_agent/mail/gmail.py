"""Gmail API reader (read-only): GET users/me/profile, messages (list) and messages/<id> (format=full). Nothing
else is ever called: no send, modify, label, trash or delete endpoint exists in this module.
"""
from datetime import datetime, timezone

from ..google import GoogleError, NeedsConsent, api, auth as gauth, base
from . import Mail, MailError
from .parse import gmail_text, header, when
from .signin import token_path

PAGE = 100
MAX_MESSAGES = 1000                 # one poll reads the OLDEST 1000 new ones at most; the next continues after them
MAX_PAGES = 40                      # ids listed per poll: 40 x 500 (listing is cheap; fetching is not)
SKIP = "-in:spam -in:sent -in:drafts -in:chats"     # Trash IS read: people delete rejections at once


class GmailReader:
    kind = "gmail"

    def __init__(self, pid: str):
        self.pid = pid

    def _get(self, path: str, params: dict | None = None) -> dict:
        return api.call("GET", f"{base('gmail')}/users/me/{path}", params=params,
                        token=gauth.token_for(token_path(self.pid))).json()

    def account(self) -> str | None:
        return self._get("profile").get("emailAddress")

    def ids_since(self, after: datetime) -> list[str]:
        ids, page = [], None
        query = f"after:{int(after.timestamp())} {SKIP}"
        for _ in range(MAX_PAGES):
            params = {"q": query, "maxResults": PAGE, "includeSpamTrash": "true",
                      **({"pageToken": page} if page else {})}
            answer = self._get("messages", params)
            ids += [m["id"] for m in answer.get("messages") or []]
            page = answer.get("nextPageToken")
            if not page:
                break
        return ids                                                 # newest first, as Gmail lists them

    def message(self, msg_id: str) -> Mail:
        data = self._get(f"messages/{msg_id}", {"format": "full"})
        payload = data.get("payload") or {}
        headers = payload.get("headers") or []
        internal = data.get("internalDate")
        moment = datetime.fromtimestamp(int(internal) / 1000, timezone.utc) if internal else None
        return Mail(msg_id=data.get("id") or msg_id, sender=header(headers, "From"),
                    subject=header(headers, "Subject"), sent_at=moment or when(header(headers, "Date")),
                    text=gmail_text(payload))

    def since(self, after: datetime, known: set[str] = frozenset()) -> list[Mail]:
        """Messages received after `after`, oldest first; ids in `known` are not fetched again."""
        try:
            new = [i for i in self.ids_since(after) if i not in known][-MAX_MESSAGES:]   # the oldest new ones
            mails = [self.message(i) for i in new]
        except NeedsConsent as err:
            raise MailError(f"Gmail sign-in lapsed for {self.pid}: connect Gmail again") from err
        except GoogleError as err:
            raise MailError(f"Gmail read failed for {self.pid}: {err}") from err
        return sorted((m for m in mails if m.sent_at > after), key=lambda m: m.sent_at)
