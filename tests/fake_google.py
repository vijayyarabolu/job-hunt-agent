"""A fake Google on 127.0.0.1 for tests (decision 006): OAuth (consent redirect + token endpoint with PKCE check),
Drive v3 (files create/list/get/update/delete/export, multipart upload incl. DOCX -> Google Doc conversion,
permissions), Sheets v4 (spreadsheets get/batchUpdate, values batchGet/batchUpdate/append) and Gmail v1 read-only
(users/me/profile, messages list with an after:<epoch> query, messages/<id>; GOAL12: any other method is refused).

    fake = FakeGoogle().start()     # then JOB_HUNT_AGENT_GOOGLE_BASE=fake.base (fake.env() patches it)
    fake.fail("POST", r":batchUpdate", 429, times=1)    # inject faults; fake.requests logs every call
"""
import base64
import hashlib
import json
import os
import re
import secrets
import threading
from datetime import datetime, timedelta, timezone
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock
from urllib.parse import parse_qs, unquote, urlencode, urlsplit

from tests import fake_sheets
from tests.fake_sheets import Spreadsheet

FOLDER, GDOC = "application/vnd.google-apps.folder", "application/vnd.google-apps.document"
GSHEET = "application/vnd.google-apps.spreadsheet"
DEFAULT_PDF = b"%PDF-1.4\n% fake export\n"
OWNER_EMAIL = "owner@example.com"
_APP_PROP = re.compile(r"appProperties has \{ key='([^']*)' and value='([^']*)' \}")


class FakeGoogle:
    def __init__(self):
        self.files: dict[str, dict] = {}
        self.sheets: dict[str, Spreadsheet] = {}
        self.requests: list[tuple[str, str, dict]] = []
        self.faults: list[dict] = []
        self.codes: dict[str, str] = {}
        self.refresh_tokens: set[str] = set()
        self.access: set[str] = set()
        self.who: dict[str, str] = {}               # token -> the Google account it belongs to
        self.next_email = OWNER_EMAIL               # who the next consent signs in as
        self.owner = OWNER_EMAIL                    # the account of the request being served (files' owner)
        self.refresh_error: str | None = None
        self.export_pdf = DEFAULT_PDF
        self.export_by_name: dict[str, bytes] = {}
        self.permissions: list[dict] = []
        self.mails: dict[str, dict] = {}            # Gmail message id -> the API's message JSON
        self.gmail_account = "jane.doe@gmail.com"
        self.code_scopes: dict[str, str] = {}
        self.clock = lambda: datetime.now(timezone.utc)
        self.lock = threading.RLock()
        self._ids = 0
        self.server = None
        self.base = ""

    # ---- lifecycle ----
    def start(self) -> "FakeGoogle":
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(self))
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        return self

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def env(self):
        return mock.patch.dict(os.environ, {"JOB_HUNT_AGENT_GOOGLE_BASE": self.base})

    def fail(self, method: str, path: str, status: int, times: int = 1, reason: str = "",
             headers: dict | None = None, delay_s: float = 0.0) -> None:
        self.faults.append({"method": method, "path": re.compile(path), "status": status, "times": times,
                            "reason": reason, "headers": headers or {}, "delay": delay_s})

    def grant(self, email: str = "", scope: str | None = None) -> dict:
        """A ready token, as if consent had happened (tests that are not about login use it)."""
        refresh, access = secrets.token_hex(8), secrets.token_hex(8)
        self.refresh_tokens.add(refresh)
        self.access.add(access)
        self.who[refresh] = self.who[access] = email or OWNER_EMAIL
        return {"access_token": access, "refresh_token": refresh, "expires_at": 4e9,
                "scope": scope or " ".join(("https://www.googleapis.com/auth/drive.file",
                                            "https://www.googleapis.com/auth/spreadsheets")),
                "refresh_obtained_at": self.clock().strftime("%Y-%m-%dT%H:%M:%SZ"), "lapsed": False}

    def count(self, method: str, pattern: str) -> int:
        return sum(1 for m, p, _ in self.requests if m == method and re.search(pattern, p))

    # ---- model ----
    def new_id(self, prefix: str = "f") -> str:
        with self.lock:
            self._ids += 1
            return f"{prefix}{self._ids}"

    def now(self) -> str:
        return self.clock().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    def create(self, meta: dict, content: bytes | None = None, media_type: str | None = None) -> dict:
        mime = meta.get("mimeType") or media_type or "application/octet-stream"
        fid = self.new_id("s" if mime == GSHEET else "f")
        record = {"id": fid, "name": meta.get("name", "untitled"), "mimeType": mime, "parents": meta.get("parents", []),
                  "appProperties": meta.get("appProperties", {}), "trashed": False, "modifiedTime": self.now(),
                  "owners": [{"emailAddress": self.owner}],
                  "webViewLink": f"https://drive.google.com/file/d/{fid}/view", "content": content}
        if content is not None and mime not in (GDOC, GSHEET):
            record["md5Checksum"] = hashlib.md5(content).hexdigest()
        if mime == GSHEET:
            self.sheets[fid] = Spreadsheet(fid, record["name"])
            record["webViewLink"] = f"https://docs.google.com/spreadsheets/d/{fid}/edit"
        if mime == FOLDER:
            record["webViewLink"] = f"https://drive.google.com/drive/folders/{fid}"
        self.files[fid] = record
        fake_sheets.CHIP_NAMES[record["webViewLink"]] = record["name"]
        fake_sheets.CHIP_OWNERS[record["webViewLink"]] = self.owner
        return record

    def touch(self, fid: str) -> None:
        if fid in self.files:
            self.files[fid]["modifiedTime"] = self.now()

    def existing(self, sid: str, tabs: dict[str, list[list]]) -> Spreadsheet:
        """A spreadsheet the user already has (never created by the app), tabs given as rows of values;
        a value is a str/number or a cell dict {'v', 'formula', 'link', 'fmt'}."""
        book = Spreadsheet(sid, "Application Tracker")
        book.sheets = []
        for title, rows in tabs.items():
            book.add_sheet({"title": title})
            for r, row in enumerate(rows):
                for c, value in enumerate(row):
                    if value not in (None, ""):
                        book.cell(title, r, c).update(value if isinstance(value, dict) else {"v": value})
        self.sheets[sid] = book
        return book

    def spreadsheet(self) -> Spreadsheet:
        return next(iter(self.sheets.values()))

    def phone_edit(self, title: str, row: int, header: str, value: str) -> None:
        """Play a phone edit: set the shown text of one cell (row 0 = header) and bump modifiedTime."""
        with self.lock:
            book = self.spreadsheet()
            col = book.row_values(title)[0].index(header)
            book.cell(title, row, col).update({"v": value, "formula": None, "link": None, "runs": None})
            self.touch(book.sid)

    def listing(self, q: str) -> list[dict]:
        props = _APP_PROP.findall(q)
        rest = _APP_PROP.sub("", q)
        name = re.search(r"name = '((?:[^'\\]|\\.)*)'", rest)
        parent = re.search(r"'([^']+)' in parents", rest)
        mime = re.search(r"mimeType = '([^']+)'", rest)
        out = []
        for f in self.files.values():
            if ("trashed = false" in rest and f["trashed"]) or any(f["appProperties"].get(k) != v for k, v in props):
                continue
            if f.get("owners") and f["owners"][0]["emailAddress"] != self.owner:
                continue                                # a search sees only the caller's own files
            if name and f["name"] != name.group(1).replace("\\'", "'"):
                continue
            if (parent and parent.group(1) not in f["parents"]) or (mime and f["mimeType"] != mime.group(1)):
                continue
            out.append(public(f))
        return out


def public(record: dict) -> dict:
    return {k: v for k, v in record.items() if k != "content"}


def later(fake: FakeGoogle, seconds: float) -> None:
    """Move the fake's clock forward (the modifiedTime of later writes)."""
    start = fake.clock()
    fake.clock = lambda: start + timedelta(seconds=seconds)


def _handler(fake: FakeGoogle):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _send(self, status: int, body: bytes = b"", ctype: str = "application/json", headers=None):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, payload) -> None:
            self._send(status, json.dumps(payload).encode())

        def _error(self, status: int, reason: str = "", headers=None) -> None:
            body = {"error": {"code": status, "message": f"fake {status}", "status": reason,
                              "errors": [{"reason": reason}] if reason else []}}
            self._send(status, json.dumps(body).encode(), headers=headers)

        def _go(self, method: str) -> None:
            parts = urlsplit(self.path)
            query = {k: v if len(v) > 1 else v[0] for k, v in parse_qs(parts.query).items()}
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            with fake.lock:
                fake.requests.append((method, parts.path, query))
                fault = next((f for f in fake.faults if f["times"] and f["method"] == method
                              and f["path"].search(parts.path)), None)
                if fault:
                    fault["times"] -= 1
            if fault:
                if fault["delay"]:
                    threading.Event().wait(fault["delay"])
                return self._error(fault["status"], fault["reason"], fault["headers"])
            if parts.path == "/token":
                return self._token({k: v[0] for k, v in parse_qs(body.decode()).items()})
            if parts.path == "/o/oauth2/v2/auth":
                return self._consent(query)
            if parts.path == "/revoke":
                fake.refresh_tokens.discard(parse_qs(body.decode()).get("token", [""])[0])
                return self._json(200, {})
            bearer = self.headers.get("Authorization", "")[7:]
            if bearer not in fake.access:
                return self._error(401, "authError")
            try:
                with fake.lock:
                    fake.owner = fake_sheets.CALLER = fake.who.get(bearer, OWNER_EMAIL)
                    return _route(fake, self, method, parts.path, query, body)
            except KeyError as err:
                return self._error(404, f"notFound {err}")
            except ValueError as err:                       # e.g. a write past the grid's last column
                return self._error(400, f"badRequest {err}")

        def do_GET(self):  # noqa: N802
            self._go("GET")

        def do_POST(self):  # noqa: N802
            self._go("POST")

        def do_PATCH(self):  # noqa: N802
            self._go("PATCH")

        def do_DELETE(self):  # noqa: N802
            self._go("DELETE")

        def _consent(self, query: dict) -> None:
            code = secrets.token_hex(6)
            fake.codes[code] = query["code_challenge"]
            fake.code_scopes[code] = query.get("scope") or ""
            target = f"{query['redirect_uri']}?{urlencode({'code': code, 'state': query['state']})}"
            self._send(302, b"", "text/plain", {"Location": target})

        def _token(self, form: dict) -> None:
            if form["grant_type"] == "authorization_code":
                challenge = fake.codes.pop(form.get("code"), None)
                digest = base64.urlsafe_b64encode(hashlib.sha256(form.get("code_verifier", "").encode()).digest())
                if challenge is None or digest.rstrip(b"=").decode() != challenge:
                    return self._json(400, {"error": "invalid_grant", "error_description": "bad code or verifier"})
                token = fake.grant(fake.next_email, fake.code_scopes.pop(form.get("code"), None) or None)
                return self._json(200, {"access_token": token["access_token"], "expires_in": 3599,
                                        "refresh_token": token["refresh_token"], "scope": token["scope"]})
            if fake.refresh_error or form.get("refresh_token") not in fake.refresh_tokens:
                return self._json(400, {"error": fake.refresh_error or "invalid_grant"})
            access = secrets.token_hex(8)
            fake.access.add(access)
            fake.who[access] = fake.who.get(form.get("refresh_token"), OWNER_EMAIL)
            return self._json(200, {"access_token": access, "expires_in": 3599})

    return Handler


def _multipart(content_type: str, body: bytes) -> tuple[dict, bytes, str]:
    message = BytesParser().parsebytes(f"Content-Type: {content_type}\r\n\r\n".encode() + body)
    meta_part, media_part = message.get_payload()
    return json.loads(meta_part.get_payload(decode=True)), media_part.get_payload(decode=True), \
        media_part.get_content_type()


def _gmail(fake: FakeGoogle, h, method: str, rest: str, query: dict):
    """Gmail v1, read-only: profile, list (q 'after:<epoch> ...'), get. Anything else is a 405 (and logged)."""
    if method != "GET":
        return h._error(405, "readOnlyFake")
    if rest == "profile":
        return h._json(200, {"emailAddress": fake.gmail_account})
    if rest == "messages":
        after = re.search(r"after:(\d+)", query.get("q", ""))
        floor = int(after.group(1)) * 1000 if after else 0
        ids = [m for m, msg in sorted(fake.mails.items(), key=lambda kv: -int(kv[1]["internalDate"]))
               if int(msg["internalDate"]) > floor]
        start = int(query.get("pageToken") or 0)
        size = int(query.get("maxResults") or 100)
        page = {"messages": [{"id": i} for i in ids[start:start + size]]}
        if start + size < len(ids):
            page["nextPageToken"] = str(start + size)
        return h._json(200, page)
    found = re.match(r"^messages/([^/]+)$", rest)
    if found and found.group(1) in fake.mails:
        return h._json(200, fake.mails[found.group(1)])
    return h._error(404, "notFound")


def _route(fake: FakeGoogle, h, method: str, path: str, query: dict, body: bytes):
    if path.startswith("/gmail/v1/users/me/"):
        return _gmail(fake, h, method, path[len("/gmail/v1/users/me/"):], query)
    if path == "/upload/drive/v3/files" and method == "POST":
        meta, media, media_type = _multipart(h.headers["Content-Type"], body)
        return h._json(200, public(fake.create(meta, media, media_type)))
    match = re.match(r"^/upload/drive/v3/files/([^/]+)$", path)
    if match and method == "PATCH":                       # new content for an existing file (in place)
        meta, media, _ = _multipart(h.headers["Content-Type"], body)
        record = fake.files[match.group(1)]
        record.update({k: v for k, v in meta.items() if k == "name"}, content=media)
        fake.touch(record["id"])
        return h._json(200, public(record))
    if path == "/drive/v3/about":
        return h._json(200, {"user": {"emailAddress": fake.owner}})
    if path == "/drive/v3/files" and method == "POST":
        return h._json(200, public(fake.create(json.loads(body or b"{}"))))
    if path == "/drive/v3/files" and method == "GET":
        return h._json(200, {"files": fake.listing(query.get("q", ""))})
    match = re.match(r"^/drive/v3/files/([^/]+)(/export|/permissions)?$", path)
    if match:
        return _file(fake, h, method, match.group(1), match.group(2), query, body)
    match = re.match(r"^/v4/spreadsheets/([^/:]+)(.*)$", path)
    if match:
        return _sheets(fake, h, method, match.group(1), unquote(match.group(2)), query, body)
    raise KeyError(path)


def _file(fake: FakeGoogle, h, method: str, fid: str, sub: str | None, query: dict, body: bytes):
    record = fake.files[fid]
    if sub == "/export":
        if record["mimeType"] != GDOC:
            return h._error(400, "fileNotExportable")
        return h._send(200, fake.export_by_name.get(record["name"], fake.export_pdf), "application/pdf")
    if sub == "/permissions":
        fake.permissions.append({"file": fid, **json.loads(body)})
        return h._json(200, {"id": fake.new_id("p")})
    if method == "GET" and query.get("alt") == "media":
        return h._send(200, record.get("content") or b"", record["mimeType"])
    if method == "DELETE":
        del fake.files[fid]
        return h._send(204)
    if method == "PATCH":
        meta = json.loads(body or b"{}")
        removed = (query.get("removeParents") or "").split(",")
        added = [p for p in (query.get("addParents") or "").split(",") if p]
        record.update({k: v for k, v in meta.items() if k in ("name", "trashed")})
        record["parents"] = [p for p in record["parents"] if p not in removed] + added
        fake.touch(fid)
    return h._json(200, public(record))


def _sheets(fake: FakeGoogle, h, method: str, sid: str, rest: str, query: dict, body: bytes):
    book = fake.sheets[sid]
    if rest == "" and method == "GET":
        return h._json(200, book.grid_data(query["ranges"]) if "ranges" in query else book.to_json())
    payload = json.loads(body or b"{}")
    if rest == ":batchUpdate":
        replies = book.batch_update(payload["requests"])
        fake.touch(sid)
        return h._json(200, {"spreadsheetId": sid, "replies": replies})
    if rest == "/values:batchGet":
        ranges = query.get("ranges", [])
        return h._json(200, {"valueRanges": book.batch_get([ranges] if isinstance(ranges, str) else ranges,
                                                           query.get("valueRenderOption", "FORMATTED_VALUE"))})
    if rest == "/values:batchUpdate":
        for item in payload["data"]:
            book.values_update(item["range"], item["values"])
        fake.touch(sid)
        return h._json(200, {"spreadsheetId": sid})
    match = re.match(r"^/values/(.+):append$", rest)
    if match:
        book.values_update(match.group(1), payload["values"], append=True)
        fake.touch(sid)
        return h._json(200, {"spreadsheetId": sid})
    raise KeyError(rest)
