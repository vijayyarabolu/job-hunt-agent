"""net.py against a local HTTP server: retry on 429/5xx, no retry on 404, rate spacing per host, 304s, gzip,
host backoff after a run of 403s."""
import gzip
import json
import socketserver
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from job_hunt_agent import net


class _Handler(BaseHTTPRequestHandler):
    hits: dict[str, int] = {}

    def do_GET(self):
        count = self.hits[self.path] = self.hits.get(self.path, 0) + 1
        if self.path == "/flaky" and count < 3:
            self.send_response(429)
            self.send_header("Retry-After", "0")
            self.end_headers()
            return
        if self.path == "/missing":
            self.send_response(404)
            self.end_headers()
            return
        if self.path == "/cond":
            if self.headers.get("If-None-Match") == 'W/"v1"':
                self.send_response(304)
                self.send_header("ETag", 'W/"v1"')
                self.end_headers()
                return
            self._json({"fresh": True}, etag='W/"v1"')
            return
        if self.path == "/gz":
            raw = gzip.compress(json.dumps({"zipped": True}).encode())
            self.send_response(200)
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        if self.path.startswith("/ban"):
            self.send_response(403)
            self.end_headers()
            return
        body = json.dumps({"ok": True, "ua": self.headers.get("User-Agent")}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload, etag=None):
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        if etag:
            self.send_header("ETag", etag)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class _FastServer(ThreadingHTTPServer):
    def server_bind(self):
        # HTTPServer.server_bind does a reverse-DNS getfqdn() that takes ~35 s on macOS.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


class NetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = _FastServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        _Handler.hits.clear()
        gate = mock.patch.object(net, "_gate")          # robots gate tested separately (no network here)
        gate.start()
        self.addCleanup(gate.stop)

    def test_sends_real_user_agent(self):
        self.assertIn("job-hunt-agent", net.get_json(f"{self.base}/ok")["ua"])

    def test_retries_429_then_succeeds(self):
        with mock.patch.object(net.time, "sleep"):
            self.assertEqual(net.get_json(f"{self.base}/flaky")["ok"], True)
        self.assertEqual(_Handler.hits["/flaky"], 3)

    def test_404_is_not_retried(self):
        with self.assertRaises(net.HttpError) as caught:
            net.get_json(f"{self.base}/missing")
        self.assertEqual(caught.exception.status, 404)
        self.assertFalse(caught.exception.is_transient)
        self.assertEqual(_Handler.hits["/missing"], 1)

    def test_workday_tenants_share_one_data_center_bucket(self):
        self.assertEqual(net.bucket_key("nvidia.wd5.myworkdayjobs.com"), "wd5.myworkdayjobs.com")
        self.assertEqual(net.bucket_key("adobe.wd5.myworkdayjobs.com"), "wd5.myworkdayjobs.com")
        self.assertEqual(net.bucket_key("api.lever.co"), "api.lever.co")

    def test_requests_to_one_host_are_spaced(self):
        started = time.monotonic()
        for _ in range(4):
            net.get_json(f"{self.base}/ok")
        self.assertGreaterEqual(time.monotonic() - started, 3 * net.MIN_INTERVAL_S * 0.95)

    def test_304_is_a_response_not_a_failure(self):
        net.reset_stats()
        first, payload = net.get_json_cond(f"{self.base}/cond")
        self.assertEqual((first.status, payload, first.etag), (200, {"fresh": True}, 'W/"v1"'))
        again, payload = net.get_json_cond(f"{self.base}/cond", etag=first.etag)
        self.assertEqual((again.status, payload, again.etag), (304, None, 'W/"v1"'))
        self.assertEqual((net.stats()["n_req"], net.stats()["n_304"]), (2, 1))

    def test_gzip_bodies_are_decoded(self):
        self.assertEqual(net.get_json(f"{self.base}/gz"), {"zipped": True})

    def test_contact_comes_from_local_config_not_code(self):
        self.assertNotIn("@", net.AGENT)

    def test_lever_gets_its_crawl_delay(self):
        self.assertEqual(net.gap_for("api.lever.co"), 1.0)
        self.assertEqual(net.gap_for("boards-api.greenhouse.io"), net.MIN_INTERVAL_S)


class GateTest(unittest.TestCase):
    def test_disallowed_url_is_refused_before_any_request(self):
        from job_hunt_agent import robots
        with mock.patch.object(robots, "allowed", return_value=False), \
                mock.patch.object(net.urllib.request, "urlopen") as opened:
            with self.assertRaises(net.HttpError) as caught:
                net.get_json("https://api.smartrecruiters.com/v1/companies/x/postings")
        opened.assert_not_called()
        self.assertEqual((caught.exception.status, caught.exception.robots), (net.ROBOTS_BLOCKED, True))
        self.assertFalse(caught.exception.is_transient)

    def test_unreadable_robots_is_transient(self):
        from job_hunt_agent import robots
        with mock.patch.object(robots, "allowed", return_value=None):
            with self.assertRaises(net.HttpError) as caught:
                net.get_json("https://example.com/x")
        self.assertTrue(caught.exception.is_transient)


class BackoffTest(unittest.TestCase):
    def setUp(self):
        net._backoff.clear()
        net._refusals.clear()
        gate = mock.patch.object(net, "_gate")
        gate.start()
        self.addCleanup(gate.stop)

    def tearDown(self):
        net._backoff.clear()
        net._refusals.clear()

    def test_a_run_of_403s_pauses_the_host_and_later_calls_fail_fast(self):
        for _ in range(net.REFUSALS_TO_BACKOFF):
            net._note_status("boards-api.greenhouse.io", 403)
        self.assertIsNotNone(net.backoff_until("boards-api.greenhouse.io"))
        with mock.patch.object(net.urllib.request, "urlopen") as opened:
            with self.assertRaises(net.HttpError) as caught:
                net.get_json("https://boards-api.greenhouse.io/v1/boards/x/jobs")
        opened.assert_not_called()
        self.assertTrue(caught.exception.backoff)
        self.assertTrue(caught.exception.is_transient)

    def test_one_403_does_not_pause_and_success_resets_the_count(self):
        net._note_status("api.lever.co", 403)
        net._note_status("api.lever.co", 200)
        net._note_status("api.lever.co", 403)
        net._note_status("api.lever.co", 403)
        self.assertIsNone(net.backoff_until("api.lever.co"))

    def test_workday_403s_are_per_tenant_not_a_ban(self):
        for tenant in ("a", "b", "c", "d"):
            net._note_status(f"{tenant}.wd1.myworkdayjobs.com", 403)
        self.assertIsNone(net.backoff_until("x.wd1.myworkdayjobs.com"))

    def test_tenant_403s_on_a_shared_vendor_bucket_are_not_a_ban(self):
        for tenant in ("a", "b", "c", "d"):
            net._note_status(f"{tenant}.bamboohr.com", 403)
        self.assertIsNone(net.backoff_until("x.bamboohr.com"))
        for _ in range(net.REFUSALS_TO_BACKOFF):
            net._note_status("e.bamboohr.com", 429)
        self.assertIsNotNone(net.backoff_until("x.bamboohr.com"))     # a vendor-wide 429 run pauses them all

    def test_a_403_robots_txt_never_counts_as_a_refusal(self):
        def refuse(req, timeout):
            raise net.urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)
        with mock.patch.object(net.urllib.request, "urlopen", side_effect=refuse):
            for _ in range(net.REFUSALS_TO_BACKOFF + 1):
                with self.assertRaises(net.HttpError):
                    net.request("https://api.gem.com/robots.txt", gate=False)
        self.assertIsNone(net.backoff_until("api.gem.com"))

    def test_backoff_survives_a_restart_through_state(self):
        for _ in range(net.REFUSALS_TO_BACKOFF):
            net._note_status("acme.wd5.myworkdayjobs.com", 429)
        saved = net.backoff_state()
        net._backoff.clear()
        net.load_backoff(saved)
        self.assertIsNotNone(net.backoff_until("other.wd5.myworkdayjobs.com"))   # whole data center paused
        self.assertEqual(saved["wd5.myworkdayjobs.com"]["strikes"], 1)


if __name__ == "__main__":
    unittest.main()
