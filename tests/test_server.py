"""Web UI server + api against a throwaway DB: LAN token, Host allowlist, X-Jobhunt header, tabs, triage,
mark applied, resume path safety, background work in the server."""
import http.client
import json
import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from job_hunt_agent import api, config, db, ops, server, store, tracker
from job_hunt_agent.ats.common import FetchResult, make_job

NOW = datetime.now(timezone.utc).replace(microsecond=0)


def seed_job(conn) -> str:
    item = make_job(ats="lever", key="acme", ext_id="1", company="Acme", title="Machine Learning Engineer",
                    location="Austin, TX", url="https://jobs.lever.co/acme/1", posted_at=db.iso(NOW),
                    description="Requirements:\nPython, PyTorch, Kubernetes and AWS.")
    db.sync_boards(conn, [{"board": "lever:acme", "ats": "lever", "key": "acme", "company": "Acme"}])
    run = db.start_run(conn, "test", NOW)
    store.write_board(conn, db.board(conn, "lever:acme"), FetchResult([item], 200, n_raw=1), "ok", run, NOW,
                      ["fam:ml_engineer"], 5)
    return item["uid"]


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.patch = mock.patch.object(config, "DB_PATH", Path(cls.tmp.name) / "t.db")
        cls.patch.start()
        conn = db.connect()
        cls.uid = seed_job(conn)
        conn.close()
        cls.srv = server.make_server(lan=False, port=0)
        cls.srv.token = "s3cret"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.patch.stop()
        cls.tmp.cleanup()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.srv.server_port, timeout=10)
        base = {"Host": "localhost", "Cookie": f"{server.COOKIE}=s3cret"}
        conn.request(method, path, json.dumps(body) if body is not None else None, {**base, **(headers or {})})
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp, data

    def post(self, path, body):
        return self.request("POST", path, body, {"X-Jobhunt": "1", "Content-Type": "application/json"})

    def test_token_flow(self):
        self.assertEqual(self.request("GET", "/", headers={"Cookie": ""})[0].status, 401)
        resp, _ = self.request("GET", "/?token=s3cret", headers={"Cookie": ""})
        self.assertEqual(resp.status, 303)
        self.assertIn("HttpOnly", resp.getheader("Set-Cookie"))
        self.assertIn("SameSite=Lax", resp.getheader("Set-Cookie"))      # a link clicked in mail/chat works (M8 #2)
        self.assertEqual(self.request("GET", "/?token=s%C3%A9cret", headers={"Cookie": ""})[0].status, 401)
        self.assertEqual(self.request("GET", "/favicon.ico")[0].status, 204)
        resp, html = self.request("GET", "/")
        self.assertEqual(resp.status, 200)
        self.assertIn(b"Job Hunt Agent", html)
        self.assertIn("default-src 'self'", resp.getheader("Content-Security-Policy"))

    def test_pwa_manifest_and_icons_need_no_cookie_but_the_app_does(self):
        for path in ("/manifest.webmanifest", "/icon.svg", "/icon-192.png", "/icon-512.png", "/apple-touch-icon.png"):
            self.assertEqual(self.request("GET", path, headers={"Cookie": ""})[0].status, 200, path)
        manifest = json.loads(self.request("GET", "/manifest.webmanifest", headers={"Cookie": ""})[1])
        self.assertEqual((manifest["display"], manifest["start_url"]), ("standalone", "/"))
        for path in ("/", "/js/app.js", "/sw.js", "/api/jobs?profile=alex&tab=recommended"):
            self.assertEqual(self.request("GET", path, headers={"Cookie": ""})[0].status, 401, path)
        worker = self.request("GET", "/sw.js")[1].decode()
        self.assertIn('url.pathname.startsWith("/api/")', worker)          # job data is never cached
        self.assertIn("url.search", worker)                                # nor ?token= links

    def test_static_files_revalidate_with_an_etag_and_json_is_never_stored(self):
        resp, body = self.request("GET", "/app.css")
        tag = resp.getheader("ETag")
        self.assertTrue(tag and body)
        self.assertEqual(resp.getheader("Cache-Control"), "no-cache")
        resp, body = self.request("GET", "/app.css", headers={"If-None-Match": tag})
        self.assertEqual((resp.status, body), (304, b""))
        self.assertEqual(self.request("GET", "/api/profiles")[0].getheader("Cache-Control"), "no-store")

    def test_json_and_files_are_gzipped_only_when_the_browser_asks(self):
        import gzip
        resp, body = self.request("GET", "/app.css", headers={"Accept-Encoding": "gzip, br"})
        self.assertEqual(resp.getheader("Content-Encoding"), "gzip")
        plain = self.request("GET", "/app.css")[1]
        self.assertEqual(gzip.decompress(body), plain)
        self.assertLess(len(body), len(plain))
        resp, body = self.request("GET", "/api/jobs?profile=alex&tab=recommended", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(resp.getheader("Content-Encoding"), "gzip")
        self.assertIn("jobs", json.loads(gzip.decompress(body)))
        self.assertIsNone(self.request("GET", "/api/profiles", headers={"Accept-Encoding": "gzip"})[0]
                          .getheader("Content-Encoding"))                       # under GZIP_MIN

    def test_foreign_host_and_missing_header_are_refused(self):
        self.assertEqual(self.request("GET", "/api/profiles", headers={"Host": "evil.example"})[0].status, 403)
        self.assertEqual(self.request("POST", "/api/triage", {"uid": self.uid, "state": "saved"})[0].status, 403)

    def test_tabs_triage_and_mark_applied(self):
        resp, data = self.request("GET", "/api/jobs?profile=alex&tab=fresh")
        self.assertEqual(resp.status, 200)
        self.assertEqual([c["uid"] for c in json.loads(data)["jobs"]], [self.uid])
        self.assertEqual(self.post("/api/triage", {"profile": "alex", "uid": self.uid, "state": "saved"})[0].status,
                         200)
        saved = json.loads(self.request("GET", "/api/jobs?profile=alex&tab=saved")[1])["jobs"]
        self.assertEqual(saved[0]["triage"], "saved")
        self.assertEqual(self.post("/api/applied", {"profile": "alex", "uid": self.uid})[0].status, 200)
        apps = json.loads(self.request("GET", "/api/jobs?profile=alex&tab=applied")[1])["applications"]
        self.assertEqual((apps[0]["job_uid"], apps[0]["status"]), (self.uid, "applied"))
        self.assertIn("address", apps[0])                  # the Archived line shows a short address
        detail = json.loads(self.request("GET", f"/api/job?profile=alex&uid={self.uid}")[1])
        self.assertIn("PyTorch", detail["jd"])
        self.assertEqual(detail["application"]["status"], "applied")

    def test_detail_shows_tracker_contacts_from_earlier_applications_at_the_company(self):
        conn = db.connect()
        tracker.ensure_profile(conn, "alex")
        with db.tx(conn):
            conn.executemany(
                "INSERT INTO applications(profile, import_key, company, role_title, applied_on, status, target_name, "
                "email_address) VALUES ('alex', ?, ?, ?, ?, 'applied', ?, ?)",
                [("t1", "Acme Inc.", "Data Scientist", "2026-03-05", "Pat Recruiter", "pat@acme.example"),
                 ("t2", "Acme", "ML Engineer", "2026-04-01", None, None),
                 ("t3", "Other Co", "ML Engineer", "2026-04-02", "Sam", None)])
        detail = api.job_detail(conn, "alex", self.uid)
        conn.close()
        earlier = detail["earlier"]
        self.assertEqual([e["role_title"] for e in earlier], ["ML Engineer", "Data Scientist"])   # newest first
        self.assertEqual(earlier[1]["contacts"], {"target_name": "Pat Recruiter", "email_address": "pat@acme.example"})
        self.assertEqual(earlier[0]["contacts"], {})

    def test_profile_without_baseline_shows_no_coverage_and_unknown_uids_are_refused(self):
        conn = db.connect()
        with db.tx(conn):
            conn.execute("INSERT OR REPLACE INTO fits(profile, uid, content_hash, lexicon_hash, baseline_hash, "
                         "dealbreaker_hash, jd_state, terms, computed_at) VALUES ('nobase', ?, 'c', 'l', '', 'd', 'ok', "
                         "'[[\"Python\", 1.0, 0, 1]]', 'now')", (self.uid,))
        with mock.patch.object(api.config, "PROFILES", Path(self.tmp.name) / "profiles"):
            detail = api.job_detail(conn, "nobase", self.uid)
        self.assertNotIn("coverage", detail)                            # card says "Fit not scored" (M8 #4)
        with self.assertRaisesRegex(KeyError, "nope"):
            api.triage(conn, "alex", "lever:acme:nope", "saved", NOW)  # M8 #7
        conn.close()

    def test_followup_can_be_marked_done(self):
        conn = db.connect()
        tracker.ensure_profile(conn, "alex")
        applied = (datetime.now().date() - timedelta(days=4)).isoformat()
        with db.tx(conn):
            app_id = conn.execute("INSERT INTO applications(profile, import_key, company, role_title, applied_on, "
                                  "status) VALUES ('alex', 'fu1', 'Followco', 'ML', ?, 'applied')", (applied,)).lastrowid
        conn.close()
        due = json.loads(self.request("GET", "/api/jobs?profile=alex&tab=followups")[1])["applications"]
        self.assertIn(app_id, [a["id"] for a in due])
        self.assertEqual(self.post("/api/followup", {"profile": "alex", "app": app_id, "n": 1})[0].status, 200)
        self.assertEqual(self.post("/api/followup", {"profile": "alex", "app": app_id, "n": 3})[0].status, 400)
        # application ids belong to one profile (verifier M8 round 3)
        self.assertEqual(self.post("/api/followup", {"profile": "jordan", "app": app_id, "n": 2})[0].status, 404)
        self.assertEqual(self.post("/api/status", {"profile": "jordan", "app": app_id, "status": "rejected"})[0]
                         .status, 404)
        self.assertEqual(self.post("/api/status", {"profile": "alex", "app": app_id, "status": "screen"})[0]
                         .status, 200)
        conn = db.connect()
        row = conn.execute("SELECT follow_up_1, edited FROM applications WHERE id=?", (app_id,)).fetchone()
        conn.close()
        self.assertTrue(row["follow_up_1"])
        self.assertIn("follow_up_1", row["edited"])                      # export writes it to the tracker

    def test_partner_profile_is_listed(self):
        ids = [p["id"] for p in json.loads(self.request("GET", "/api/profiles")[1])]
        self.assertIn("alex", ids)

    def test_bad_input_is_a_clean_error(self):
        self.assertEqual(self.request("GET", "/api/jobs?profile=nobody")[0].status, 400)
        self.assertEqual(self.request("GET", "/api/jobs?profile=alex&tab=nope")[0].status, 400)
        self.assertEqual(self.request("GET", "/api/nope")[0].status, 404)

    def test_negative_content_length_is_rejected(self):
        resp, _ = self.request("POST", "/api/triage?profile=alex", None,
                               {"X-Jobhunt": "1", "Content-Length": "-1"})
        self.assertEqual(resp.status, 400)                              # no blocking read (verifier M8 round 2)


class DefaultProfileTest(unittest.TestCase):
    """No profile name in code: a fresh clone with only profiles/me works (verifier M8 round 3)."""

    def test_default_profile_and_profiles_route_without_a_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "me").mkdir()
            (Path(tmp) / "me" / "profile.yaml").write_text("name: Me\nfamilies: [ml_engineer]\n")
            with mock.patch.object(config, "PROFILES", Path(tmp)), \
                    mock.patch.object(config, "settings", return_value={}):
                self.assertEqual(config.default_profile(), "me")
                self.assertEqual([p["id"] for p in server.route(None, "GET", "/api/profiles", {}, NOW)], ["me"])
            with mock.patch.object(config, "PROFILES", Path(tmp)), \
                    mock.patch.object(config, "settings", return_value={"default_profile": "nobody"}):
                self.assertEqual(config.default_profile(), "me")              # a missing name falls back
            (Path(tmp) / "ann").mkdir()                                 # sorts first, but has no resume yet
            (Path(tmp) / "ann" / "profile.yaml").write_text("name: Ann\nfamilies: [ml_engineer]\n")
            (Path(tmp) / "me" / "baseline.json").write_text("{}")
            with mock.patch.object(config, "PROFILES", Path(tmp)), \
                    mock.patch.object(config, "settings", return_value={}):
                self.assertEqual(config.default_profile(), "me")
            with mock.patch.object(config, "PROFILES", Path(tmp) / "none"):
                self.assertIsNone(config.default_profile())


class HostAndLaunchdTest(unittest.TestCase):
    def test_static_assets_and_lab_pages_are_served_and_traversal_is_refused(self):
        self.assertEqual(server.static_asset("/app.css")[1], "text/css; charset=utf-8")
        for bad in ("/../pyproject.toml", "/lab/../server.py", "/.hidden.css", "/server.py", "/a%2F..%2Fx.css",
                    "/lab/zz", "/nope.css"):
            self.assertIsNone(server.static_asset(bad), bad)
        with tempfile.TemporaryDirectory() as tmp:
            page = Path(tmp) / "static" / "lab" / "q"
            page.mkdir(parents=True)
            (page / "index.html").write_text("<p>lab</p>")
            with mock.patch.object(server.resources, "files", return_value=Path(tmp)):
                self.assertEqual(server.static_asset("/lab/q"), (b"<p>lab</p>", "text/html; charset=utf-8"))

    def test_host_allowlist(self):
        self.assertTrue(server.host_allowed("localhost:8765"))
        self.assertTrue(server.host_allowed("192.168.1.20:8765"))
        self.assertTrue(server.host_allowed("[::1]:8765"))
        self.assertFalse(server.host_allowed("attacker.example:8765"))
        self.assertFalse(server.host_allowed("[abc"))                       # malformed, not a crash
        with mock.patch.object(server, "local_names", return_value=frozenset({"example-macbook-pro.local"})):
            self.assertTrue(server.host_allowed("Example-MacBook-Pro.local:8765"))     # Bonjour name (M8 #1)

    def test_tailscale_names_of_this_mac_are_allowed(self):
        names = frozenset({"example-macbook-pro", "example-macbook-pro.local"})
        with mock.patch.object(server, "local_names", return_value=names):
            self.assertTrue(server.host_allowed("example-macbook-pro:8765"))            # MagicDNS short name
            self.assertTrue(server.host_allowed("example-macbook-pro.tail1a2b3.ts.net:8765"))
            self.assertTrue(server.host_allowed("100.101.102.103:8765"))                    # Tailscale IP
            self.assertFalse(server.host_allowed("someone-else.tail1a2b3.ts.net:8765"))
            self.assertFalse(server.host_allowed("example-macbook-pro.attacker.example:8765"))

    def test_lan_urls_include_bonjour_name_and_ip(self):
        with mock.patch.object(server, "_scutil_local_name", return_value="Example-MacBook-Pro"), \
                mock.patch.object(server, "lan_ip", return_value="192.168.1.249"):
            server.local_names.cache_clear()
            urls = server.lan_urls(8765, "t0k")
            server.local_names.cache_clear()
        self.assertEqual(urls, ["http://Example-MacBook-Pro.local:8765/?token=t0k",
                                "http://192.168.1.249:8765/?token=t0k"])

    def test_background_work_runs_in_the_server(self):
        self.assertEqual(ops.status(), {})
        self.assertEqual(ops.schedule_note(), "")
        self.assertIn("running", ops.fetch_state())


if __name__ == "__main__":
    unittest.main()
