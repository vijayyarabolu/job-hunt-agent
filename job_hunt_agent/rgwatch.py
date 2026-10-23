"""GOAL33 §1: resumegen's news reaches the page in about a second.

Every resume state change in resumegen writes an event (queued, generating, checking, needs you, ready, accepted,
archived...). While at least one job-hunt-agent tab is open, one thread here follows resumegen's event stream and tells the
tabs (live.HUB, per person); the page then asks /api/resumes once. Before this the page only polled: 4 to 30 s while
a resume was being made and 60 s otherwise, so a resume pasted in resumegen took up to a minute to show here.
resumegen offline: a quiet retry with backoff, never a log line per try.
"""
import json
import threading
import urllib.error
import urllib.request

from . import live, rgclient

IDLE_S = 2                  # no tab open: look again this often
READ_TIMEOUT_S = 40         # resumegen sends a keepalive every 15 s; silence past this means the stream is dead
BACKOFF_S = (0.5, 2, 5, 15, 30, 60)


def _stream(stop: threading.Event, hub: live.Hub) -> bool:
    """Follow the stream until it ends; True when it sent anything (a keepalive counts), so a stream that closes at
    once (resumegen restarting) is retried with backoff, not every 0.5 s."""
    req = urllib.request.Request(f"{rgclient.address()}/api/events?since=latest",
                                 headers={"Authorization": f"Bearer {rgclient._token()}"})
    sent = False
    with urllib.request.urlopen(req, timeout=READ_TIMEOUT_S) as resp:
        hub.bump_rg(None)                                # whatever moved while not following: every page asks once
        for raw in resp:
            sent = True
            if stop.is_set() or not hub.streams:
                return True                              # every tab closed: let resumegen's stream thread go
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            try:
                event = json.loads(line[5:])
            except ValueError:
                continue
            hub.bump_rg(event.get("profile"))
    return sent


def watch(stop: threading.Event, hub: live.Hub | None = None) -> None:
    hub = hub or live.HUB
    fails = 0
    while not stop.is_set():
        if not hub.streams:
            stop.wait(IDLE_S)
            continue
        try:
            fails = 0 if _stream(stop, hub) else fails + 1
        except (rgclient.Offline, urllib.error.URLError, OSError, ValueError):
            fails += 1                                   # offline or restarting: the page's own poll still runs
        stop.wait(BACKOFF_S[min(fails, len(BACKOFF_S) - 1)])
