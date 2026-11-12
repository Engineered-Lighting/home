"""Frigate's occupancy for the den, read from the den home's Home Assistant: the first half of the ask's gate.

Standard library. Frigate, on the den home's low-power Pi, notices someone first; only then does den-live ask Qwen
(the owner, 2026-11-12: "once occupancy is detected, Qwen is then called upon").

- GET <ha_url>/api/states/<entity> every POLL_S, with the den-live user's long-lived token (a bearer token).
- state() is "on" while the last read said on, and for HOLD_S after it last did. That's den_lighting's own 2 minutes of
  vacancy: Frigate can lose a reader sitting still, and the ask also needs someone seated, so standing up or leaving
  still stops the asks at once.
- It fails closed: no good read for STALE_S, or a sensor reading anything but on/off (unavailable, say), is "unknown",
  which asks nothing. A read that fails outright keeps the last word until STALE_S.
- Never logs the token, the URL or a reply's text: why is a short sentence of its own.
"""
import json
import ssl
import threading
import time
import urllib.error
import urllib.request

POLL_S = 0.5
STALE_S = 10.0
HOLD_S = 120.0
TIMEOUT_S = 3.0


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: urllib would carry the bearer token to whatever host it names."""

    def redirect_request(self, *args, **kwargs):
        return None


def fetcher(url, token, entity, *, cafile=None, timeout_s=TIMEOUT_S):
    """A fetch() that returns Home Assistant's state word for entity, or raises."""
    handlers = [NoRedirect()]
    if url.startswith("https:"):
        handlers.append(urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=cafile)))
    opener = urllib.request.build_opener(*handlers)
    target = url.rstrip("/") + "/api/states/" + entity

    def fetch():
        request = urllib.request.Request(target, headers={"Authorization": "Bearer " + token, "Accept": "application/json"})
        with opener.open(request, timeout=timeout_s) as response:
            return json.loads(response.read(65536).decode("utf-8"))["state"]
    return fetch


class Occupancy(threading.Thread):
    def __init__(self, fetch, *, poll_s=POLL_S, stale_s=STALE_S, hold_s=HOLD_S, clock=time.monotonic):
        super().__init__(daemon=True, name="den-live-occupancy")
        self.fetch, self.poll_s, self.stale_s, self.hold_s, self.clock = fetch, poll_s, stale_s, hold_s, clock
        self.halt = threading.Event()
        self.lock = threading.Lock()
        self.last_ok = self.last_on = None
        self.raw = None
        self.problem = "not read yet"
        self.counts = {"reads": 0, "failed": 0}

    def poll_once(self):
        try:
            word = self.fetch()
        except urllib.error.HTTPError as exc:
            self._failed("Home Assistant said %d" % exc.code)
            return
        except Exception:   # any other failure, http.client's included: never the thread's end, and never its text
            self._failed("Home Assistant didn't answer")
            return
        now = self.clock()
        with self.lock:
            self.counts["reads"] += 1
            if word in ("on", "off"):
                self.raw, self.last_ok, self.problem = word, now, None
                if word == "on":
                    self.last_on = now
            else:
                self.raw, self.problem = None, "Frigate's sensor reads %s" % ("unavailable" if word == "unavailable" else "something else")

    def _failed(self, why):
        with self.lock:
            self.counts["failed"] += 1
            self.problem = why

    def run(self):
        while not self.halt.is_set():
            try:
                self.poll_once()
            except Exception:   # poll_once catches its own; this is a belt over the braces
                self._failed("the occupancy read failed")
            self.halt.wait(self.poll_s)

    def state(self):
        """(word, why): on, off or unknown, and a short sentence for the health line when it isn't on."""
        now = self.clock()
        with self.lock:
            if self.last_ok is None or now - self.last_ok > self.stale_s or self.raw is None:
                return "unknown", "Frigate unread: %s" % (self.problem or "no reply")
            if self.raw == "on" or (self.last_on is not None and now - self.last_on <= self.hold_s):
                return "on", None
            return "off", "Frigate sees nobody"
