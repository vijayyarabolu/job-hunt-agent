"""GOAL7 §2 live updates: one watcher thread reads the DB every few seconds (a finished fetch run, a tracker edit) and
wakes the Server-Sent Events streams; each open tab holds one stream with a heartbeat, a stream ends cleanly when the
tab goes away or the server stops, and the number of streams is capped."""
import http.client
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from job_hunt_agent import config, db, live, server


def add_run(conn, finished: bool = True) -> int:
    now = db.utcnow()
    run = db.start_run(conn, "test", now)
    if finished:
        db.finish_run(conn, run, now, [])
    return run


class HubTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = mock.patch.object(config, "DB_PATH", Path(self.tmp.name) / "t.db")
        self.patch.start()
        self.conn = db.connect()
        self.hub = live.Hub()

    def tearDown(self):
        self.conn.close()
        self.patch.stop()
        self.tmp.cleanup()

    def test_a_finished_run_changes_the_state_and_an_unfinished_one_does_not(self):
        self.hub.poll(self.conn)
        first = self.hub.version
        add_run(self.conn, finished=False)
        self.hub.poll(self.conn)
        self.assertEqual(self.hub.version, first)
        run = add_run(self.conn)
        self.hub.poll(self.conn)
        self.assertEqual(self.hub.version, first + 1)
        self.assertEqual(self.hub.event("alex")["run"], run)

    def test_wait_wakes_on_a_change_and_times_out_otherwise(self):
        self.hub.poll(self.conn)
        seen = self.hub.version
        started = time.monotonic()
        self.assertEqual(self.hub.wait(seen, 0.05), seen)
        self.assertGreaterEqual(time.monotonic() - started, 0.04)
        threading.Timer(0.05, lambda: (add_run(db.connect()), self.hub.poll(db.connect()))).start()
        self.assertEqual(self.hub.wait(seen, 5), seen + 1)

    def test_stop_wakes_every_waiting_stream(self):
        threading.Timer(0.05, self.hub.stop).start()
        started = time.monotonic()
        self.hub.wait(self.hub.version, 5)
        self.assertLess(time.monotonic() - started, 2)
        self.assertTrue(self.hub.stopped)

    def test_streams_are_capped(self):
        held = [self.hub.join() for _ in range(live.MAX_STREAMS)]
        self.assertTrue(all(held))
        self.assertFalse(self.hub.join())
        self.hub.leave()
        self.assertTrue(self.hub.join())


class StreamTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.patch = mock.patch.object(config, "DB_PATH", Path(cls.tmp.name) / "t.db")
        cls.patch.start()
        db.connect().close()
        cls.srv = server.make_server(lan=False, port=0)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.patch.stop()
        cls.tmp.cleanup()

    def test_the_stream_says_hello_then_sends_a_change_and_heartbeats(self):
        hub = live.Hub()
        with mock.patch.object(live, "HUB", hub), mock.patch.object(live, "HEARTBEAT_S", 0.2):
            conn = http.client.HTTPConnection("127.0.0.1", self.srv.server_port, timeout=5)
            conn.request("GET", "/api/events?profile=alex", headers={"Host": "localhost"})
            resp = conn.getresponse()
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.getheader("Content-Type"), "text/event-stream")
            self.assertIn(b"event: hello", resp.fp.readline() + resp.fp.readline())
            add_run(db.connect())
            hub.poll(db.connect())
            lines = b"".join(resp.fp.readline() for _ in range(6))
            self.assertIn(b"event: change", lines)
            self.assertIn(b": ping", b"".join(resp.fp.readline() for _ in range(4)))
            resp.close()                                            # the tab goes away
            conn.close()
            deadline = time.monotonic() + 3
            while hub.streams and time.monotonic() < deadline:     # the next heartbeat finds the closed socket
                time.sleep(0.05)
            self.assertEqual(hub.streams, 0)

    def test_an_unknown_profile_is_refused(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.srv.server_port, timeout=5)
        conn.request("GET", "/api/events?profile=nobody", headers={"Host": "localhost"})
        self.assertEqual(conn.getresponse().status, 400)
        conn.close()


class WatchTest(unittest.TestCase):
    def test_on_change_runs_after_a_change_but_not_at_startup(self):
        tmp = tempfile.TemporaryDirectory()
        with tmp, mock.patch.object(config, "DB_PATH", Path(tmp.name) / "t.db"), mock.patch.object(live, "POLL_S", 0.02):
            db.connect().close()
            hub, stop, calls = live.Hub(), threading.Event(), []
            thread = threading.Thread(target=live.watch, args=(stop, hub), kwargs={"on_change": lambda: calls.append(1)})
            thread.start()
            time.sleep(0.15)
            self.assertEqual(calls, [])
            add_run(db.connect())
            deadline = time.monotonic() + 3
            while not calls and time.monotonic() < deadline:
                time.sleep(0.02)
            stop.set()
            thread.join(3)
            self.assertEqual(calls, [1])
            self.assertTrue(hub.stopped)

    def test_the_kept_feed_is_refreshed_before_the_tabs_are_told(self):
        tmp = tempfile.TemporaryDirectory()
        with tmp, mock.patch.object(config, "DB_PATH", Path(tmp.name) / "t.db"), mock.patch.object(live, "POLL_S", 0.02):
            db.connect().close()
            hub, stop, seen = live.Hub(), threading.Event(), []
            thread = threading.Thread(target=live.watch, args=(stop, hub),
                                      kwargs={"on_change": lambda: seen.append(hub.version)})
            thread.start()
            time.sleep(0.15)
            before = hub.version
            add_run(db.connect())
            deadline = time.monotonic() + 3
            while hub.version == before and time.monotonic() < deadline:
                time.sleep(0.02)
            stop.set()
            thread.join(3)
            self.assertEqual(seen, [before])               # warmed while the tabs still had the old version
            self.assertEqual(hub.version, before + 1)
