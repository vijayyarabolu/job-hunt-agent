"""stdlib web UI server (GOAL §8, decision 001 §7).

Binds 127.0.0.1 by default; `--lan` binds 0.0.0.0 and then requires the LAN token (env JOB_HUNT_AGENT_TOKEN or
data/lan_token): open http://<Bonjour name>.local:8765/?token=<token> (or the IP link) once; an HttpOnly SameSite=Lax
cookie keeps you in. Host header allowlist (localhost, this Mac's host and Bonjour names, IP literals) against DNS
rebinding; POSTs need X-Jobhunt: 1, which a cross-site page cannot send, so Lax is enough against CSRF.
A daemon thread runs fetch.run() every fetch_interval_min (flock prevents overlap with a `fetch` from cron); others
send the morning digest at digest_hour, sync the Google Sheet and run the daily email check (ops.py).
"""
import gzip
import hashlib
import hmac
import html
import ipaddress
import json
import logging
import os
import re
import secrets
import socket
import socketserver
import sqlite3
import subprocess
import sys
import threading
import time
from functools import lru_cache
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from urllib.parse import parse_qs, quote, urlsplit

from . import (api, appliedview, applyback, applydialog, applyq, config, contacts, db, export_full, fetch, filters,
               google_move, google_page, gridapi, history, jd_doc, jdcopy, jobbanks, live, logrotate, notify, ops, outreach,
               profile_admin, profile_settings, removal, resume_swap, resume_upload, rgclient, rgjobs, rgwatch, rqueue,
               searchapi, setup_status, sheet_look, stats, stats_html, trackapi, vendor_howpage, vendors)
from .autofill import api as autofill, health as autofill_health, package as autofill_pkg
from .finder import service as finder
from .google import GoogleError, accounts
from .mail import panel as mail_panel, signin as mail_signin

log = logging.getLogger(__name__)
MAIL_ACT = "/api/mail/act"              # every button of the "Updates from email" panel
MAX_BODY = 64 * 1024
MAX_AUTOFILL_BODY = 4 * 1024 * 1024     # a page snapshot (the form's HTML) from the extension
TOKEN_HEADER = "X-Jobhunt-Token"        # the extension sends the LAN token here (it has no cookie)
VERSION_HEADER = "X-Jobhunt-Version"    # the extension's version (job-hunt-agent says when a newer one is ready)
CORS_HEADERS = "Content-Type, X-Jobhunt, X-Jobhunt-Token, X-Jobhunt-Version"
HOW_PATH = "/how/ats"                   # the vendor model page (decision 010)
GOOGLE_PAGES = "/google/"                # account, connect, finish, callback, disconnect
JD_PAGE_PATH = "/jd"                     # the Full JD page (?profile=&app= or &uid=)
OPEN_RG_PATH = "/open/resumegen"   # the side menu's "Open resumegen ↗"
EVENTS_PATH = "/api/events"         # Server-Sent Events (live.py)
UPLOAD_PATH = "/api/tracker/resume"     # raw file body (PDF/DOCX), args in the query string
SETUP_RESUME = "/api/setup/resume"      # a profile's base resume (.docx), raw body like UPLOAD_PATH
COOKIE = "jh_token"
TAILNET_SUFFIX = ".ts.net"      # Tailscale MagicDNS: <machine>.<tailnet>.ts.net
JS = "text/javascript; charset=utf-8"
DIGEST_HOUR = 8               # local time; settings.yaml digest_hour overrides
DIGEST_WINDOW_H = 4           # a Mac asleep at 08:00 still gets the digest until noon
DIGEST_TICK_S = 60
STATIC = {"/": ("index.html", "text/html; charset=utf-8")}   # the rest (/app.css, /js/*.js, fonts) via static_asset
ASSET_TYPES = {".css": "text/css; charset=utf-8", ".js": JS, ".html": "text/html; charset=utf-8", ".woff2": "font/woff2",
               ".svg": "image/svg+xml", ".png": "image/png", ".webmanifest": "application/manifest+json"}
ASSET_PATH = re.compile(r"^/(?:[a-z0-9][a-z0-9_-]*/)*[a-z0-9][a-z0-9_-]*\.[a-z0-9]+$")   # no "..", no dotfiles
# the PWA manifest and its icons carry no data; browsers fetch them without cookies (install, iOS home screen)
PUBLIC_PATHS = frozenset({"/manifest.webmanifest", "/icon.svg", "/icon-192.png", "/icon-512.png", "/icon-maskable-512.png",
                          "/apple-touch-icon.png"})
CLIENT_GONE = (BrokenPipeError, ConnectionResetError)    # the browser dropped the connection (tab switch, close)
GZIP_MIN = 1400                             # smaller answers are not worth compressing
GZIP_LEVEL = 6
GZIP_TYPES = ("application/json", "text/", "image/svg+xml", "application/manifest+json")
_GZIPPED: dict[str, bytes] = {}             # static file ETag -> its gzip body (files only change on a deploy)
ASSET_CACHE = "no-cache"                    # keep, but ask every time (ETag -> 304): a new deploy shows at once
FONT_CACHE = "public, max-age=31536000, immutable"
LAB_PAGE = re.compile(r"^/lab/[a-z]$")                 # design mockups at /lab/a, /lab/b, /lab/c
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
SECURITY_HEADERS = {"Content-Security-Policy": "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'",
                    "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "Cache-Control": "no-store"}


def static_asset(path: str) -> tuple[bytes, str] | None:
    """A file under job-hunt-agent/static (css, js, fonts, icons, lab pages) by its URL path; None when there is none."""
    if LAB_PAGE.match(path):
        path += "/index.html"
    if not ASSET_PATH.match(path):
        return None
    ctype = ASSET_TYPES.get(path[path.rindex("."):])
    found = resources.files("job_hunt_agent").joinpath("static", *path.strip("/").split("/"))
    if ctype is None or not found.is_file():
        return None
    return found.read_bytes(), ctype


def lan_token() -> str:
    """JOB_HUNT_AGENT_TOKEN, else data/lan_token (created once, mode 0600; never in git or the plist)."""
    if os.environ.get("JOB_HUNT_AGENT_TOKEN"):
        return os.environ["JOB_HUNT_AGENT_TOKEN"]
    path = config.DATA / "lan_token"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_urlsafe(24) + "\n")
        path.chmod(0o600)
    return path.read_text().strip()


def _scutil_local_name() -> str | None:
    """macOS Bonjour name (System Settings > General > Sharing > Local hostname), e.g. 'Alexs-MacBook-Pro'."""
    try:
        out = subprocess.run(["scutil", "--get", "LocalHostName"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


@lru_cache(maxsize=1)
def local_names() -> frozenset[str]:
    host = socket.gethostname().lower()
    short = host.split(".")[0]
    names = {"localhost", host, short, f"{short}.local"}
    bonjour = _scutil_local_name()
    if bonjour:
        names |= {bonjour.lower(), f"{bonjour.lower()}.local"}
    return frozenset(names)


def lan_ip() -> str | None:
    """The address other machines on the network reach this Mac at (no packet is sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))                      # TEST-NET-1: routing lookup only
            return probe.getsockname()[0]
    except OSError:
        return None


def lan_urls(port: int, token: str) -> list[str]:
    bonjour = _scutil_local_name() or socket.gethostname().split(".")[0]
    urls = [f"http://{bonjour}.local:{port}/?token={token}"]
    ip = lan_ip()
    if ip and not ip.startswith("127."):
        urls.append(f"http://{ip}:{port}/?token={token}")
    return urls


def host_allowed(host_header: str | None) -> bool:
    host = (host_header or "").strip()
    if host.startswith("[") and "]" not in host:
        return False                                                # malformed IPv6 literal (verifier M8 round 2)
    host = host[1:host.index("]")] if host.startswith("[") else host.rsplit(":", 1)[0]
    host = host.lower()
    if host in local_names():
        return True
    if host.endswith(TAILNET_SUFFIX) and host.split(".", 1)[0] in local_names():
        return True                                                 # this Mac's Tailscale MagicDNS name
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _same(given: str, token: str) -> bool:
    return hmac.compare_digest(given.encode("utf-8"), token.encode("utf-8"))


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 64     # a page load opens ~8 parallel connections; the default backlog of 5 overflows (reset)
    token: str | None = None

    def server_bind(self):
        # HTTPServer.server_bind does a reverse-DNS getfqdn() that takes ~35 s on macOS.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], CLIENT_GONE):          # the tab closed or moved on mid-answer: not an error
            return
        super().handle_error(request, client_address)


class Handler(BaseHTTPRequestHandler):
    server: Server

    def log_message(self, fmt, *args):          # quiet access log; failures are logged in _dispatch
        pass

    def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        extra = dict(extra or {})
        if len(body) >= GZIP_MIN and ctype.startswith(GZIP_TYPES) and "gzip" in self.headers.get("Accept-Encoding", ""):
            tag = extra.get("ETag")
            packed = _GZIPPED.get(tag) if tag else None
            if packed is None:
                packed = gzip.compress(body, GZIP_LEVEL, mtime=0)
                if tag:
                    _GZIPPED[tag] = packed
            body = packed
            extra.update({"Content-Encoding": "gzip", "Vary": "Accept-Encoding"})
        self.send_response(status)
        headers = {**SECURITY_HEADERS, "Content-Type": ctype, "Content-Length": str(len(body)), **self._cors(), **extra}
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _cors(self) -> dict:
        """the Autofill extension (its own fixed id only) may read /api/autofill/* answers."""
        origin = self.headers.get("Origin") or ""
        if not urlsplit(self.path).path.startswith(autofill.PREFIX) or origin not in autofill_pkg.allowed_origins():
            return {}
        return {"Access-Control-Allow-Origin": origin, "Vary": "Origin"}

    def do_OPTIONS(self):
        """The extension's preflight (custom headers); everything else gets no CORS answer."""
        cors = self._cors()
        if not cors or not host_allowed(self.headers.get("Host")):
            return self._send(403, b"", "text/plain")
        self._send(204, b"", "text/plain", {"Access-Control-Allow-Methods": "GET, POST",
                                            "Access-Control-Allow-Headers": CORS_HEADERS,
                                            "Access-Control-Max-Age": "600"})

    def _asset(self, body: bytes, ctype: str) -> None:
        """a static file the browser may keep and revalidate (304 when unchanged); fonts never change."""
        tag = f'"{hashlib.sha1(body).hexdigest()[:16]}"'
        cache = FONT_CACHE if ctype == ASSET_TYPES[".woff2"] else ASSET_CACHE
        headers = {"ETag": tag, "Cache-Control": cache}
        if self.headers.get("If-None-Match") == tag:
            return self._send(304, b"", ctype, headers)
        self._send(200, body, ctype, headers)

    def _json(self, status: int, payload) -> None:
        self._send(status, json.dumps(payload, default=str).encode(), "application/json")

    def _authorized(self) -> bool:
        if not host_allowed(self.headers.get("Host")):
            self._json(403, {"error": "host not allowed"})
            return False
        parts = urlsplit(self.path)
        if self.server.token is None or parts.path in PUBLIC_PATHS:
            return True
        if parts.path in (accounts.CALLBACK_PATH, accounts.LEGACY_CALLBACK) and accounts.answers_pending(parts.query):
            return True                                     # Google's redirect: the pending state is the secret
        jar = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        if COOKIE in jar and _same(jar[COOKIE].value, self.server.token):
            return True
        header = self.headers.get(TOKEN_HEADER) or ""
        if parts.path.startswith(autofill.PREFIX) and header and _same(header, self.server.token):
            return True                                             # the Autofill extension
        given = parse_qs(urlsplit(self.path).query).get("token", [""])[0]
        if given and _same(given, self.server.token):
            cookie = f"{COOKIE}={self.server.token}; HttpOnly; SameSite=Lax; Path=/; Max-Age=31536000"
            self._send(303, b"", "text/plain", {"Location": "/", "Set-Cookie": cookie})
            return False
        self._send(401, b"Open this page once with ?token=<LAN token> (printed by `job-hunt-agent serve --lan`).",
                   "text/plain; charset=utf-8")
        return False

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > (MAX_AUTOFILL_BODY if urlsplit(self.path).path.startswith(autofill.PREFIX) else MAX_BODY):
            raise ValueError("body too large")
        if length < 0:
            raise ValueError("bad Content-Length")
        data = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(data, dict):
            raise ValueError("JSON object expected")
        return data

    def do_GET(self):
        if not self._authorized():
            return
        parts = urlsplit(self.path)
        if parts.path == "/favicon.ico":
            return self._send(204, b"", "image/x-icon")
        if parts.path == accounts.CALLBACK_PATH and "state=" in parts.query and accounts.answers_pending(parts.query):
            return self._google_page("/google/callback", parts.query)
        if parts.path in STATIC:
            name, ctype = STATIC[parts.path]
            return self._asset(resources.files("job_hunt_agent").joinpath("static", name).read_bytes(), ctype)
        asset = static_asset(parts.path)
        if asset is not None:
            return self._asset(*asset)
        if parts.path == OPEN_RG_PATH:
            pid = parse_qs(parts.query).get("profile", [""])[0] or None
            return self._send(303, b"", "text/plain", {"Location": rgclient.browser_url(self.headers.get("Host"), pid)})
        if parts.path == EVENTS_PATH:
            return self._events(parse_qs(parts.query).get("profile", [""])[0] or config.default_profile())
        if parts.path.startswith(GOOGLE_PAGES):                  # a person's own Google account
            return self._google_page(parts.path, parts.query)
        if parts.path == JD_PAGE_PATH:
            return self._jd_page({k: v[0] for k, v in parse_qs(parts.query).items()})
        if parts.path == HOW_PATH:                               # "how this is computed"
            vendor = parse_qs(parts.query).get("vendor", [""])[0]
            return self._send(200, vendor_howpage.render(vendor).encode(), "text/html; charset=utf-8")
        self._dispatch("GET", parts.path, {k: v[0] for k, v in parse_qs(parts.query).items()})

    def _google_page(self, path: str, query: str) -> None:
        args = {k: v[0] for k, v in parse_qs(query).items()}
        pid, tag, html_type = args.get("profile") or "", args.get("t"), "text/html; charset=utf-8"
        try:
            if path == "/google/callback":
                body = google_page.callback(query)
            elif path == "/google/connect":
                target = google_page.connect_url(pid, tag, self.server.server_port, args.get("kind"),
                                                 args.get("address"))
                return self._send(303, b"", "text/plain", {"Location": target})
            elif path == "/google/finish":
                body = google_page.finish(pid, tag, args.get("url") or "")
            elif path == "/google/disconnect":
                body = google_page.disconnect(pid, tag)
            elif path == "/google/account":
                body = google_page.render(pid or config.default_profile())
            else:
                raise KeyError(path)
            self._send(200, body.encode(), html_type)
        except KeyError:
            self._send(404, b"<p>Not found.</p>", html_type)
        except (ValueError, PermissionError, config.ConfigError) as err:
            self._send(400, f"<p>{html.escape(str(err))}</p><p><a href=\"/\">Back</a></p>".encode(), html_type)
        except CLIENT_GONE:
            return
        except Exception:                                       # never leak internals to the browser
            log.exception("Google account page failed: %s", path)
            self._send(500, b"<p>The Google account page failed (see the server log).</p>", html_type)

    def _jd_page(self, args: dict) -> None:
        """the Full JD page, server-rendered (phone over Tailscale too)."""
        pid = args.get("profile") or config.default_profile()
        conn = db.connect()
        try:
            if pid not in config.profile_ids():
                raise KeyError(pid)
            app = int(args["app"]) if (args.get("app") or "").isdigit() else None
            if app is None and not args.get("uid"):
                raise KeyError("app or uid")
            body = jd_doc.page(conn, pid, app, args.get("uid"))
            self._send(200, body.encode(), "text/html; charset=utf-8")
        except KeyError:
            self._send(404, b"<p>No such job or tracker row.</p>", "text/html; charset=utf-8")
        except CLIENT_GONE:
            return
        except Exception:                                       # never leak internals to the browser
            log.exception("JD page failed: %s", args)
            self._send(500, b"<p>The JD page failed (see the server log).</p>", "text/html; charset=utf-8")
        finally:
            conn.close()

    def _events(self, pid: str) -> None:
        """One tab's event stream; ends when the tab goes away or the server stops."""
        if pid not in config.profile_ids():
            return self._json(400, {"error": f"unknown profile {pid!r}"})
        hub = live.HUB                                          # leave the hub this stream joined
        if not hub.join():
            return self._json(503, {"error": "too many open live streams"})
        self.close_connection = True
        try:
            self.send_response(200)
            for key, value in {**SECURITY_HEADERS, "Content-Type": "text/event-stream"}.items():
                self.send_header(key, value)
            self.end_headers()
            live.stream(self._write_event, pid)
        except OSError:                                         # the tab closed: BrokenPipe / ConnectionReset
            pass
        finally:
            hub.leave()

    def _write_event(self, data: bytes) -> None:
        self.wfile.write(data)
        self.wfile.flush()

    def do_POST(self):
        if not self._authorized():
            return
        if self.headers.get("X-Jobhunt") != "1":
            return self._json(403, {"error": "missing X-Jobhunt header"})
        parts = urlsplit(self.path)
        try:
            body = self._upload(parts.query) if parts.path in (UPLOAD_PATH, SETUP_RESUME) else self._body()
        except ValueError as err:
            return self._json(400, {"error": str(err)})
        self._dispatch("POST", parts.path, body)

    def _upload(self, query: str) -> dict:
        """A resume file sent as the raw body; profile, app and the file's own name come in the query string."""
        length = int(self.headers.get("Content-Length") or 0)
        if length > resume_upload.MAX_BYTES:
            raise ValueError(f"the file is over {resume_upload.MAX_BYTES // (1024 * 1024)} MB")
        if length <= 0:
            raise ValueError("the file is empty")
        return {**{k: v[0] for k, v in parse_qs(query).items()}, "data": self.rfile.read(length)}

    def _dispatch(self, method: str, path: str, args: dict) -> None:
        if method == "GET" and path == autofill.PREFIX + "setup":   # the token the extension needs
            return self._json(200, {"token": self.server.token, "extension_id": autofill_pkg.own_id()})
        if path.startswith(autofill.PREFIX) and (self.headers.get("Origin") or "").startswith("chrome-extension://"):
            args = {**args, "_ext": self.headers.get(VERSION_HEADER) or ""}
        if path.startswith("/api/setup"):
            args = {**args, "_port": self.server.server_port}
        conn, now = db.connect(), db.utcnow()
        try:
            result = route(conn, method, path, args, now)
            if isinstance(result, autofill.File):                # a resume PDF / the extension zip
                ascii_name = result.name.encode("ascii", "ignore").decode() or "file"
                how = "inline" if result.ctype == "application/pdf" else "attachment"
                return self._send(200, result.data, result.ctype,
                                  {"Cache-Control": "private, no-store",
                                   "Content-Disposition": f'{how}; filename="{ascii_name}"; '
                                                          f"filename*=UTF-8''{quote(result.name)}"})
            if isinstance(result, Download):
                return self._send(200, result.path.read_bytes(), result.ctype,
                                  {"Content-Disposition": f'attachment; filename="{result.path.name}"'})
            if isinstance(result, Raw):                         # a resume PDF / page image from resumegen
                headers = {"Cache-Control": "private, no-store",
                           "Content-Security-Policy": "default-src 'none'; frame-ancestors 'self'"}
                if result.name:                                 # the base resume's name, not "file.pdf"
                    ascii_name = result.name.encode("ascii", "ignore").decode() or "resume.pdf"
                    headers["Content-Disposition"] = (f'inline; filename="{ascii_name}"; '
                                                      f"filename*=UTF-8''{quote(result.name)}")
                return self._send(200, result.data, result.ctype, headers)
            self._json(200, result)
        except rgclient.Offline as err:                         # job-hunt-agent keeps working without it
            self._json(503, {"error": str(err), "offline": True})
        except KeyError as err:
            self._json(404, {"error": f"not found: {err}"})
        except (ValueError, config.ConfigError) as err:
            self._json(400, {"error": str(err)})
        except PermissionError as err:
            self._json(403, {"error": str(err)})
        except CLIENT_GONE:
            return
        except Exception:                                       # never leak internals to the browser
            log.exception("request failed: %s %s", method, path)
            self._json(500, {"error": "internal error (see server log)"})
        finally:
            conn.close()


class Download:
    """A file answer (tracker exports): sent as an attachment instead of JSON."""
    def __init__(self, path, ctype: str):
        self.path, self.ctype = path, ctype


class Raw:
    """Bytes shown inline (a resume PDF or page image passed through from resumegen)."""
    def __init__(self, data: bytes, ctype: str, name: str | None = None):
        self.data, self.ctype, self.name = data, ctype, name


def _goal8_get(conn, pid: str, path: str, args: dict, now):
    """GOAL8 reads: resume chips + side panel (resumegen), "Did you apply?" entries, Settings."""
    if path == "/api/resumes":
        return rgjobs.statuses(conn, pid, lambda jobs: api.sync_tailored(conn, pid, jobs, now), now)
    if path == "/api/resumes/job":
        return rgjobs.detail(pid, int(args["id"]))
    if path == "/api/resumes/file" or path.startswith("/api/resumes/file/"):   # .../file/<Name>.pdf: Chrome saves by URL
        return Raw(*rgjobs.file(pid, int(args["id"]), args.get("which", "pdf"), download=args.get("dl") == "1"))
    if path == "/api/pending":
        return applyback.due(conn, pid, now, boot=args.get("boot") == "1", peek=args.get("peek") == "1")
    if path == "/api/settings":
        return {**profile_settings.get(pid), **profile_settings.apply_get(pid),      # GOAL13: goal, in hand, answers
                "jobbanks": jobbanks.status()}                                       # GOAL35 §4.2
    return None


def _approved_to_row(conn, pid: str, args: dict) -> str | None:
    """an approved resume for a job already applied to becomes its row's resume (the old one replaced);
    the Approve itself has succeeded, so a failure here is only reported."""
    try:
        return resume_swap.after_approve(conn, pid, int(args["id"]))
    except (ValueError, KeyError, GoogleError, rgclient.Offline) as err:
        log.warning("%s: approved resume %s not linked to its row: %s", pid, args.get("id"), err)
        return f"not linked to the tracker row: {err}"


def _goal8_post(conn, pid: str, path: str, args: dict, now):
    """GOAL8 writes; None when the path is not one of them."""
    if path == "/api/resumes/generate":
        return rgjobs.generate(conn, pid, args.get("uids"), args.get("places"))
    if path == "/api/resumes/action":
        out = rgjobs.action(pid, int(args["id"]), str(args.get("action", "")), args.get("note"),
                            args.get("current") is True)
        return {**out, "row_note": _approved_to_row(conn, pid, args)} if args.get("action") == "approve" else out
    if path == "/api/pending/answer":
        return applyback.answer(conn, pid, int(args["id"]), str(args.get("answer", "")), now)
    if path == "/api/outside/applied":                         # §3: Mark applied on an outside job
        return applyback.confirm(conn, pid, now, app_id=int(args["app"]))
    if path == "/api/settings/years":
        return profile_settings.set_years_cap(pid, args.get("cap"))
    if path == "/api/settings/min_match":                     # resumegen GOAL3 §8
        return profile_settings.set_min_match(pid, args.get("min_match"))
    if path == "/api/settings/past_employers":                 # GOAL16 §0
        return profile_settings.set_past_employers(pid, args.get("names"))
    if path == "/api/settings/apply":                          # GOAL13 §2b, §2d, §2g
        return profile_settings.set_apply(pid, args)
    if path == "/api/settings/jobbanks":                       # GOAL35 §4.2: app-wide
        return jobbanks.set_on(args.get("on"))
    return None


def _tracker_get(conn, pid: str, path: str, args: dict):
    """GOAL3 tracker reads; None when the path is not one of them."""
    if path == "/api/tracker":
        out = trackapi.rows(conn, pid, args.get("status") or None, args.get("q"), int(args.get("limit") or
                            trackapi.PAGE), int(args.get("offset") or 0), sheet=int(args.get("sheet") or 0) or None,
                            clip=args.get("compact") == "1", wide=args.get("wide") == "1")
        return {**out, "look": (sheet_look.get(conn, pid) or {}).get("tabs")}       # GOAL19 §1: the Sheet's look
    if path == "/api/tracker/row":
        return trackapi.get(conn, pid, int(args["app"]))
    if path == "/api/tracker/sync":                            # "saved ✓ / synced ✓"
        return trackapi.sync_state(conn, pid, int(args["app"]))
    if path == "/api/tracker/followups":
        return trackapi.followups(conn, pid, count_only=args.get("count") == "1")
    if path == "/api/tracker/stats":
        report = stats.report(conn, pid)
        return {**report, "html": stats_html.render(report, phone=args.get("phone") == "1")}
    if path == "/api/tracker/lookup":
        return trackapi.lookup(conn, args.get("url", ""))
    if path == "/api/tracker/export":
        return Download(export_full.export(conn, pid, args.get("kind", "original")), XLSX_TYPE)
    if path == "/api/vendors":
        return {"vendors": vendors.listing(conn, pid)}
    if path == "/api/contacts/panel":                          # free, no paid call, no network
        return finder.panel(conn, pid, _app_id(args), args.get("domain") or None, uid=args.get("uid") or None)
    if path == "/api/contacts/hunter_account":                 # free; searches / verifications left
        return finder.hunter_account(pid)
    if path == "/api/outreach":                                # text only; the person sends it
        return outreach.draft(conn, pid, int(args["app"]), int(args["contact"]))
    return None


def _app_id(args: dict) -> int | None:
    """The tracker row a contacts call is about, or None for a feed job keyed by its uid."""
    return int(args["app"]) if args.get("app") else None


def _tracker_post(conn, pid: str, path: str, args: dict):
    """GOAL3 tracker writes; None when the path is not one of them."""
    if path == "/api/outreach/claude":                         # one Claude call on the person's click
        return outreach.claude_draft(conn, pid, int(args["app"]), int(args["contact"]))
    if path == "/api/tracker/update":
        changes = args.get("changes") or {}
        if isinstance(changes, dict):
            trackapi.not_stale_resume(conn, pid, int(args["app"]), changes)          # GOAL18 §1
        return trackapi.update(conn, pid, int(args["app"]), changes)
    if path == "/api/tracker/add":
        return trackapi.create(conn, pid, args.get("data") or {})
    if path == "/api/tracker/update_many":                     # paste, fill, clear, undo / redo
        return gridapi.update_many(conn, pid, args.get("edits"), guard=not args.get("undo"))
    if path == "/api/tracker/insert":
        return gridapi.insert_blank(conn, pid, str(args.get("status") or "applied"))
    if path == "/api/remove":                                  # × Remove (a job, or a tracker row)
        why = str(args.get("why") or "unwanted")
        if args.get("app"):
            return removal.remove_app(conn, pid, int(args["app"]), why)
        out = removal.remove_job(conn, pid, str(args["uid"]), why)
        return {**out, "resumes": rqueue.archive_resumes(conn, pid, out["uid"], out["removed"])}    # GOAL20
    if path == "/api/remove/undo":
        out = removal.undo(conn, pid, int(args["id"]))
        rg, requeue = out.pop("rg"), out.pop("rg_requeue")
        return {**out, "resumes": rqueue.restore_resumes(rg, requeue)}
    if path == "/api/tracker/contacts":
        app_id = int(args["app"])
        trackapi.get(conn, pid, app_id)                            # the profile's own row, or KeyError
        contacts.save(conn, app_id, args.get("contacts"))
        return trackapi.get(conn, pid, app_id)
    if path == "/api/tracker/contact_flag":
        row = conn.execute("SELECT app_id FROM contacts WHERE id = ?", (int(args["contact"]),)).fetchone()
        if row is None:
            raise KeyError(args["contact"])
        trackapi.get(conn, pid, row["app_id"])
        contacts.toggle(conn, int(args["contact"]), args.get("flag", ""), bool(args.get("on")))
        return trackapi.get(conn, pid, row["app_id"])
    if path == "/api/tracker/followup":
        return trackapi.followup_done(conn, pid, int(args["app"]), int(args["n"]))
    if path == "/api/tracker/undo":                            # the person's last edit
        if args.get("ready"):                                  # Done today → back in Ready
            return trackapi.undo_to_ready(conn, pid, int(args["app"]), person(pid))
        return trackapi.undo(conn, pid, int(args["app"]) if args.get("app") else None, person(pid),
                             field=str(args["field"]) if args.get("field") else None)
    if path == "/api/vendors":
        return {"vendors": vendors.save(conn, pid, args.get("vendors"), clear=bool(args.get("clear")))}
    if path == "/api/contacts/lookup":                         # one source, on the person's click
        return finder.lookup(conn, pid, _app_id(args), str(args.get("provider", "")), str(args.get("domain", "")),
                             uid=args.get("uid") or None)
    if path == "/api/contacts/add":                            # a feed job (uid) gets its saved row only now
        return finder.add(conn, pid, _app_id(args), args.get("contact"), uid=args.get("uid") or None)
    if path == UPLOAD_PATH:
        if not isinstance(args.get("data"), bytes):
            raise ValueError("send the file itself as the request body")
        return resume_upload.upload(conn, pid, int(args["app"]), args.get("name", ""), args["data"],
                                    replace=args.get("replace") == "1")           # Replace ▾ → Upload
    if path == "/api/resume/act":                              # × / Replace ▾ on a row's resume
        return resume_swap.act(conn, pid, args)
    return None


def setup_route(method: str, path: str, args: dict):
    """Settings > Setup and Profiles: work before any profile exists."""
    pid = args.get("profile") if args.get("profile") in config.profile_ids() else config.default_profile()
    if method == "GET" and path == "/api/setup":
        return {"items": setup_status.items(pid, args.get("_port")), "demo": config.demo_mode(),
                "home": str(config.ROOT), "profiles": api.profiles(), "families": profile_admin.families()}
    if method != "POST":
        raise KeyError(path)
    if config.demo_mode() and path != "/api/setup/test":
        raise PermissionError("demo mode does not save settings: run `job-hunt-agent serve` for the real app")
    if path == "/api/setup/test":
        return setup_status.test(str(args.get("item") or ""), pid)
    if path == "/api/setup/key":
        return setup_status.save_key(str(args.get("name") or ""), args.get("value"))
    if path == "/api/setup/resume_service":
        return setup_status.save_tailor_url(args.get("url"))
    if path == "/api/setup/google_client":
        return setup_status.save_google_client(args.get("client_id"), args.get("client_secret"), args.get("json"))
    if path == "/api/setup/google_connect":
        if not accounts.main_status()["configured"]:
            raise ValueError("save your Google OAuth client first (the steps above)")
        return {"url": accounts.start(accounts.MAIN, int(args.get("_port") or 0))}
    if path == "/api/setup/google_disconnect":
        return {"disconnected": accounts.disconnect(accounts.MAIN)}
    if path == "/api/setup/profile":
        return profile_admin.create(args)
    if path == SETUP_RESUME:
        return profile_admin.upload_resume(str(args.get("profile") or ""), str(args.get("name") or ""),
                                           args.get("data") or b"")
    raise KeyError(path)


def route(conn, method: str, path: str, args: dict, now):
    if method == "GET" and path == "/api/profiles":                 # answers before any profile is chosen
        return api.profiles()
    if path.startswith("/api/setup"):
        return setup_route(method, path, args)
    pid = args.get("profile") or config.default_profile()
    if pid not in config.profile_ids():
        raise ValueError(f"unknown profile {pid!r}")
    if path.startswith(applyq.PREFIX):                            # Apply on laptop
        return applyq.route(conn, pid, method, path, args, now)
    if path.startswith(autofill.PREFIX):                          # the Autofill extension + Answers page
        if "_ext" in args:                                          # which extension version the user uses
            autofill_health.seen(conn, pid, args["_ext"])
        if method == "GET":
            return autofill.get(conn, pid, path, args)
        with history.action(person(pid)):
            return autofill.post(conn, pid, path, args, now)
    if method == "GET":
        if path == "/api/jobs" and args.get("tab") == "resumes":    # Queue / Ready / Done today
            return rqueue.board(conn, pid, args.get("rq") or "ready", now)
        if path == "/api/jobs" and args.get("tab") == "applied" and args.get("view") == "cards":   # Applied as cards
            return appliedview.board(conn, pid, args.get("seg") or "all", args.get("q"), now)
        if path == "/api/jobs":
            return api.jobs(conn, pid, args.get("tab", "recommended"), now, filters.parse(args))
        if path == "/api/job":
            return api.job_detail(conn, pid, args["uid"], now)
        if path == "/api/jd_text":                                 # Copy JD / Copy JD + keywords
            return jdcopy.jd_text(conn, pid, args["uid"], keywords=args.get("keywords") == "1",
                                  resume=args.get("resume") or None)
        if path == "/api/health":
            return api.health_summary(conn, now)
        if path == "/api/search":                                  # GOAL35 §5.2: the ⌘K box, every area
            return searchapi.everything(conn, pid, args.get("q") or "", now)
        if path == "/api/apply_options":                           # GOAL6 §1: the Mark applied dialog
            return applydialog.options(conn, pid, now, args.get("uid") or None,
                                       int(args["app"]) if args.get("app") else None)
        found = _tracker_get(conn, pid, path, args)
        if found is None:
            found = _goal8_get(conn, pid, path, args, now)
        if found is not None:
            return found
        if path == "/api/google":                                  # Sheet link + token banner
            return api.google_summary(conn, pid)
    if method == "POST":
        with history.action(person(pid)):                     # every change credited to the person
            if path == MAIL_ACT:
                return mail_panel.act(conn, pid, args, person(pid))
            return _post(conn, pid, path, args, now)
    raise KeyError(path)


def person(pid: str) -> str:
    """The person's name for the edit history (their profile name, else the profile id)."""
    try:
        return str(config.profile(pid).get("name") or pid)
    except config.ConfigError:
        return pid


def _post(conn, pid: str, path: str, args: dict, now):
    """Every write the web UI makes."""
    if path == "/api/offer":                                       # GOAL29 §2: Claude's three areas, once per JD
        detail = api.job_detail(conn, pid, args["uid"], now)
        return {"offer": api.offer_panel(conn, pid, args["uid"], detail.get("card"), now, run_llm=True)}
    if path == "/api/triage":
        api.triage(conn, pid, args["uid"], args["state"], now)
        return {"ok": True, "resumes": rqueue.follow_triage(conn, pid, args["uid"], args["state"])}   # GOAL20
    if path == "/api/apply_click":                                 # also remembered for "Did you apply?"
        if args.get("app"):
            return {"pending": applyback.remember(conn, pid, now, app_id=int(args["app"]))}
        return {"app": api.apply_click(conn, pid, args["uid"], now),
                "pending": applyback.remember(conn, pid, now, uid=args["uid"])}
    if path == "/api/applied":                                     # from the Mark applied dialog
        return {"app": applydialog.mark(conn, pid, now, args.get("uid") or None,
                                        int(args["app"]) if args.get("app") else None,
                                        args.get("resume") or None, bool(args.get("with_file")))}
    if path == "/api/status":
        api.set_status(conn, pid, int(args["app"]), args["status"])
        return {"ok": True}
    if path == "/api/followup":
        api.followup_done(conn, pid, int(args["app"]), int(args["n"]), now)
        return {"ok": True}
    if path == "/api/feedback":                                # Like / Not interested + reason
        return api.feedback_action(conn, pid, args["uid"], args.get("kind", ""), args.get("reason"), now,
                                   args.get("note"))
    found = _tracker_post(conn, pid, path, args)
    if found is None:
        found = _goal8_post(conn, pid, path, args, now)
    if found is not None:
        return found
    raise KeyError(path)


def _fetch_loop(stop: threading.Event) -> None:
    interval = int(config.settings()["fetch_interval_min"]) * 60
    while not stop.is_set():
        try:
            fetch.run(trigger="server")
        except Exception:                                       # the loop must survive any single failed run
            log.exception("server fetch loop: run failed")
        if stop.wait(interval):
            return


def _digest_loop(stop: threading.Event) -> None:
    """the morning digest from the always-on server instead of an OS scheduler."""
    hour = int(config.settings().get("digest_hour", DIGEST_HOUR))
    while not stop.wait(DIGEST_TICK_S):
        try:
            now = db.utcnow()
            if notify.digest_due(now, hour, DIGEST_WINDOW_H):
                conn = db.connect()
                try:
                    notify.digest(conn, now)
                finally:
                    conn.close()
        except Exception:                                       # a failed digest must not kill the thread
            log.exception("digest loop: digest failed")


WARM_RETRY_S = (5, 15, 60)                  # GOAL33 §3: a warm-up that met a locked DB tries again
CHECKPOINT_S = 300


def _warm(retry_s: tuple = WARM_RETRY_S, first: bool = False) -> None:
    for wait in (*retry_s, None):
        try:
            api.warm(db.utcnow(), first=first)
            return
        except sqlite3.OperationalError as err:              # locked by a long write elsewhere: wait, try again
            if wait is None:
                log.exception("feed warm-up failed")
                return
            log.warning("feed warm-up: %s; trying again in %s s", err, wait)
            time.sleep(wait)
        except Exception:                                   # a cold cache only makes the first load slower
            log.exception("feed warm-up failed")
            return


def _warm_after_change() -> None:
    """live.watch's on_change: one try, no waiting on a lock (that would hold the run and edit news back from the
    tabs); the next change warms again and a page computes what it needs anyway."""
    _warm(retry_s=())


def _checkpoints(stop: threading.Event) -> None:
    """GOAL33 §3b: the WAL is copied back every few minutes (PASSIVE: never waits on a reader), so it stays small."""
    while not stop.wait(CHECKPOINT_S):
        try:
            conn = db.connect()
            try:
                db.checkpoint(conn)
            finally:
                conn.close()
        except sqlite3.Error as err:
            log.warning("WAL checkpoint skipped: %s", err)


def make_server(lan: bool = False, port: int | None = None) -> Server:
    port = int(config.settings()["server_port"]) if port is None else port
    server = Server(("0.0.0.0" if lan else "127.0.0.1", port), Handler)
    server.token = lan_token() if lan else None
    return server


def serve(lan: bool = False, port: int | None = None, fetch_loop: bool = True, sync: bool = True) -> None:
    server = make_server(lan, port)
    stop = threading.Event()
    if fetch_loop:
        threading.Thread(target=_fetch_loop, args=(stop,), daemon=True, name="fetch-loop").start()
    if sync:                                       # a second (design lab) server must not sync the sheets too
        from .google import runner                                 # Sheet sync 10 s after a change,
        threading.Thread(target=runner.loop, args=(stop,), daemon=True, name="google-sync").start()   # pull / 10 min
        from .mail import poll                                     # job emails every 30 min + Cold daily
        mail_signin.redirect_port = server.server_port
        threading.Thread(target=poll.loop, args=(stop,), daemon=True, name="mail").start()
        threading.Thread(target=google_move.resume_stopped, args=(config.profile_ids(),), daemon=True,  # GOAL15 §2
                         name="google-move-resume").start()
        threading.Thread(target=logrotate.loop, args=(stop,), daemon=True, name="log-rotate").start()   # GOAL33 §3
    threading.Thread(target=_digest_loop, args=(stop,), daemon=True, name="digest").start()        # 08:00 digest
    if fetch_loop:                                 # the daily email check and the monthly H-1B refresh
        threading.Thread(target=ops.loop, args=(stop,), daemon=True, name="daily").start()
    threading.Thread(target=live.watch, args=(stop,), kwargs={"on_change": _warm_after_change}, daemon=True,   # GOAL7 SSE + the
                     name="live-watch").start()                                        # kept feed refreshed
    threading.Thread(target=rgwatch.watch, args=(stop,), daemon=True, name="rg-watch").start()   # GOAL33 §1
    threading.Thread(target=_checkpoints, args=(stop,), daemon=True, name="wal-checkpoint").start()   # GOAL33 §3b
    threading.Thread(target=_warm, kwargs={"first": True}, daemon=True, name="warm-feed").start()
    where = lan_urls(server.server_port, server.token) if lan else [f"http://localhost:{server.server_port}/"]
    print("job-hunt-agent UI on " + "\n   or ".join(where), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
