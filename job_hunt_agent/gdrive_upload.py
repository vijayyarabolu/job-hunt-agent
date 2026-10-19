"""Drive side of the tracker's resume upload (GOAL4 §4 addition): the folders job-hunt-agent/Resumes/<Profile>/, the
person's share, and one new file per upload. Scope drive.file: the app sees only folders and files it created.

Never deletes, trashes, renames or overwrites a Drive file: a re-upload is always a new file (decision 008).
a person with their own Google token gets the folder in their own Drive (cache keys per account).
"""
import hashlib
from datetime import datetime, timezone

from . import config
from .google import FOLDER, GoogleError, api, auth, base

ROOT_PATH = ("job-hunt-agent", "Resumes")
FILE_FIELDS = "id,name,mimeType,webViewLink,md5Checksum,size"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _quote(text: str) -> str:
    return text.replace("\\", "\\\\").replace("'", "\\'")


def _alive(file_id: str) -> bool:
    try:
        found = api.get(f"{base('drive')}/files/{file_id}", {"fields": "id,trashed"})
    except GoogleError as err:
        if err.status == 404:
            return False
        raise
    return not found.get("trashed")


def _find(name: str, parent: str) -> dict | None:
    q = f"name = '{_quote(name)}' and mimeType = '{FOLDER}' and '{parent}' in parents and trashed = false"
    found = api.get(f"{base('drive')}/files", {"q": q, "fields": "files(id,webViewLink)", "spaces": "drive"})
    return (found.get("files") or [None])[0]


def _scoped(key: str) -> str:
    """Cache keys per Google account: a person's own Drive has its own folders."""
    account = auth.current()
    return f"{account}|{key}" if account else key


def _cached(conn, key: str) -> dict | None:
    row = conn.execute("SELECT file_id, link FROM gfiles WHERE key = ?", (_scoped(key),)).fetchone()
    return {"id": row["file_id"], "webViewLink": row["link"]} if row else None


def _remember(conn, key: str, record: dict) -> None:
    conn.execute("INSERT INTO gfiles(key, file_id, link, at) VALUES (?,?,?,?) ON CONFLICT(key) DO UPDATE SET "
                 "file_id = excluded.file_id, link = excluded.link, at = excluded.at",
                 (_scoped(key), record["id"], record.get("webViewLink"), _now()))
    conn.commit()


def folder(conn, parts: tuple[str, ...]) -> dict:
    """The folder at My Drive/<parts...>, found or created level by level (cached in gfiles, re-checked)."""
    parent, record = "root", {}
    for depth in range(1, len(parts) + 1):
        key = "folder:" + "/".join(parts[:depth])
        record = _cached(conn, key)
        if record is None or not _alive(record["id"]):
            record = _find(parts[depth - 1], parent) or api.post(
                f"{base('drive')}/files", {"name": parts[depth - 1], "mimeType": FOLDER, "parents": [parent]},
                {"fields": "id,webViewLink"})
            _remember(conn, key, record)
        parent = record["id"]
    return record


def share(conn, folder_id: str, email: str) -> bool:
    """Give `email` editor access to the folder (its files inherit it), once, without an e-mail. True
    when newly shared."""
    key = f"share:{folder_id}:{email.lower()}"
    if _cached(conn, key):
        return False
    made = api.post(f"{base('drive')}/files/{folder_id}/permissions",
                    {"type": "user", "role": "writer", "emailAddress": email},
                    {"sendNotificationEmail": "false", "fields": "id"})
    _remember(conn, key, {"id": made.get("id", ""), "webViewLink": None})
    return True


def profile_folder(conn, pid: str) -> dict:
    """job-hunt-agent/Resumes/<Profile>/ in the Drive of whoever's token is in use; on the owner's Drive it is shared with
    the person (profile drive_share_with), so their files never ask for access."""
    made = folder(conn, (*ROOT_PATH, pid.title()))
    share_with = config.profile(pid).get("drive_share_with")
    shared = bool(share_with) and auth.current() != pid and share(conn, made["id"], share_with)
    return {**made, "shared_with": None if auth.current() == pid else share_with, "newly_shared": shared}


def upload(folder_id: str, name: str, data: bytes, mime: str, props: dict) -> dict:
    """A NEW file in the folder (never an update of an existing one); the stored bytes are checked by MD5."""
    meta = {"name": name, "parents": [folder_id], "mimeType": mime,
            "appProperties": {k: str(v) for k, v in props.items()}}
    body, ctype = api.multipart(meta, data, mime)
    made = api.call("POST", f"{base('upload')}/files", params={"uploadType": "multipart", "fields": FILE_FIELDS},
                    data=body, ctype=ctype).json()
    if made.get("md5Checksum") and made["md5Checksum"] != hashlib.md5(data).hexdigest():
        raise GoogleError(f"Drive stored {name} with a different checksum: upload it again")
    return made
