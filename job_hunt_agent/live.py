"""GOAL7 §2 live updates over Server-Sent Events (stdlib).

One watcher thread reads two numbers from the DB every POLL_S (the last finished fetch run, the last tracker edit per
person: a Sheet pull, the other tab, an undo) and wakes the streams when either moves. Each open tab holds one stream
(GET /api/events): a hello with the current numbers, then a "change" event when something moved, else a heartbeat
comment every HEARTBEAT_S so proxies keep it open and a closed tab is noticed. The page decides what to reload. The
number of streams is capped so they can never take every server thread.
"""
import json
import logging
import threading

from . import db

log = logging.getLogger(__name__)
POLL_S = 10
HEARTBEAT_S = 25
MAX_STREAMS = 12                    # 2 people x a few tabs and devices; the server has daemon threads per request
RETRY_MS = 5000                     # the browser's EventSource reconnect delay


class Hub:
    def __init__(self):
        self._cond = threading.Condition()
        self.version = 0
        self.state: dict = {"run": None, "edits": {}}
        self.streams = 0
        self.stopped = False

    @staticmethod
    def read(conn) -> dict:
        run = conn.execute("SELECT max(id) FROM runs WHERE finished_at IS NOT NULL").fetchone()[0]
        edits = {r[0]: r[1] for r in conn.execute("SELECT profile, max(id) FROM edit_history GROUP BY profile")}
        return {"run": run, "edits": edits}

    def poll(self, conn) -> None:
        self.publish(self.read(conn))

    def publish(self, now: dict) -> bool:
        """Wake the streams if `now` differs from what they were last told; True if it did."""
        with self._cond:
            if now == self.state:
                return False
            self.state = now
            self.version += 1
            self._cond.notify_all()
            return True

    def moved(self, now: dict) -> bool:
        with self._cond:
            return now != self.state

    def event(self, pid: str) -> dict:
        """What one person's page needs to know: the last finished fetch run and their last tracker edit."""
        with self._cond:
            return {"run": self.state["run"], "edit": self.state["edits"].get(pid), "v": self.version}

    def wait(self, seen: int, timeout: float) -> int:
        """Block until the version moves past `seen`, the hub stops, or the timeout; the version now."""
        with self._cond:
            self._cond.wait_for(lambda: self.version != seen or self.stopped, timeout)
            return self.version

    def join(self) -> bool:
        with self._cond:
            if self.streams >= MAX_STREAMS or self.stopped:
                return False
            self.streams += 1
            return True

    def leave(self) -> None:
        with self._cond:
            self.streams -= 1

    def stop(self) -> None:
        with self._cond:
            self.stopped = True
            self._cond.notify_all()


HUB = Hub()


def watch(stop: threading.Event, hub: Hub | None = None, on_change=None) -> None:
    """The server's watcher thread: one small DB read every POLL_S, whatever the number of open tabs; on_change()
    runs here (not in a request) after something moved, before the tabs are told, except at startup."""
    hub = hub or HUB
    started = False
    while not stop.is_set():
        try:
            conn = db.connect()
            try:
                now = hub.read(conn)
            finally:
                conn.close()
            if started and on_change and hub.moved(now):
                on_change()                                 # the kept feed is fresh before the tabs ask for it
            hub.publish(now)
            started = True
        except Exception:                                   # a locked or busy DB just waits for the next tick
            log.exception("live watcher: poll failed")
        if stop.wait(POLL_S):
            break
    hub.stop()


def message(name: str, data: dict) -> bytes:
    return f"event: {name}\ndata: {json.dumps(data)}\n\n".encode()


def stream(write, pid: str, hub: Hub | None = None) -> None:
    """Write one tab's events until the tab goes away (the write fails) or the server stops."""
    hub = hub or HUB
    write(f"retry: {RETRY_MS}\n".encode() + message("hello", hub.event(pid)))
    seen = hub.version
    while not hub.stopped:
        now = hub.wait(seen, HEARTBEAT_S)
        if hub.stopped:
            return
        write(message("change", hub.event(pid)) if now != seen else b": ping\n\n")
        seen = now
