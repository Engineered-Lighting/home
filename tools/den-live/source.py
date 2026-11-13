"""Step 3's frame source: the den camera's newest bundle, about 5 a second (plan L1). Standard library only.

One thread polls the camera's /frame.npz with den-take's own client and token, every poll_s. A bundle it has seen
before (same session, RGB sequence and host time, as take.py dedupes) is skipped. Each new one goes into a one-slot
mailbox: if the loop hasn't taken the last one yet, the older is replaced and counted as dropped, so the loop always
works on the newest frame and lag can't build up. It keeps the bundle's bytes, never writes them anywhere.

Status, for the stream: ok; offline (nothing new for stale_s, or the camera doesn't answer); refused (401 or 403:
the token). After an error it waits longer each time, up to max_wait_s, and tries again; it never stops on its own.

The camera's paths (plan step 4): den-live asks the camera for /frame.npz and /calibration only, never /frame.jpg or
anything else. The camera serves one JPEG at a time and refuses a second, and the Lab's previews and its Qwen ring need
it; /frame.npz is a prebuilt latest bundle with request slots of its own. AllowedCamera holds den-take's client to
those two paths, refusing any other before a request is made. Every den-live tool that reads the camera gets its client
from camera_client(): the sofa check, step 2's take, the live loop and the service.
"""
import threading
import time
from collections import deque
from dataclasses import dataclass

ALLOWED_PATHS = ("/frame.npz", "/calibration")


class PathRefused(Exception):
    pass


class AllowedCamera:
    """den-take's Camera, held to ALLOWED_PATHS: any other path is refused here, before a connection is opened. It has
    no health(): den-take's /health check is a path this client doesn't ask for."""

    def __init__(self, camera):
        self._camera = camera
        self.host, self.port = camera.host, camera.port

    def __repr__(self):
        return "AllowedCamera(%s:%s)" % (self.host, self.port)

    def get(self, path, timeout=5.0):
        if type(path) is not str or path not in ALLOWED_PATHS:
            raise PathRefused("den-live asks the camera for %s only" % " and ".join(ALLOWED_PATHS))
        return self._camera.get(path, timeout=timeout)


def camera_client(take, url, token):
    """den-take's GET client for the camera at url, with the token, held to ALLOWED_PATHS."""
    return AllowedCamera(take.Camera(url, token))


@dataclass(frozen=True)
class Bundle:
    data: bytes
    session: str
    rgb_seq: int
    t_host: float          # the Jetson's host monotonic clock at the RGB frame
    arrival_unix: float    # this PC's wall clock when the bytes arrived
    arrival_mono: float    # this PC's monotonic clock then


class Mailbox:
    """The newest item not yet taken. Putting over one still waiting replaces it and counts a drop."""

    def __init__(self):
        self._cond = threading.Condition()
        self._item = None
        self.received = self.dropped = 0

    def put(self, item):
        with self._cond:
            if self._item is not None:
                self.dropped += 1
            self._item = item
            self.received += 1
            self._cond.notify_all()

    def take(self, timeout):
        with self._cond:
            self._cond.wait_for(lambda: self._item is not None, timeout)
            item, self._item = self._item, None
            return item


class CameraSource(threading.Thread):
    def __init__(self, take, camera, mailbox, *, poll_s=0.2, stale_s=5.0, max_wait_s=5.0):
        super().__init__(daemon=True, name="den-live-source")
        self.take, self.mailbox = take, mailbox
        self.camera = camera if isinstance(camera, AllowedCamera) else AllowedCamera(camera)
        self.poll_s, self.stale_s, self.max_wait_s = poll_s, stale_s, max_wait_s
        self.halt = threading.Event()
        self.status, self.why = "starting", None
        self.polls = self.errors = 0
        self.sessions = []
        self._seen = deque(maxlen=64)

    def state(self):
        return {"status": self.status, "why": self.why}

    def run(self):
        clock = self.take.clock
        last_new, wait = clock(), 0.0
        while not self.halt.is_set():
            started = clock()
            self.polls += 1
            try:
                status, body = self.camera.get("/frame.npz")
                why = None
            except (OSError, self.take.http.client.HTTPException) as exc:
                status, body, why = None, b"", "the camera did not answer (%s)" % type(exc).__name__
            arrival_mono, arrival_unix = clock(), time.time()
            fresh = False
            if status == 200:
                try:
                    meta = self.take.check.read_bundle(body)[0]
                except self.take.check.BundleError:
                    self.errors += 1
                    why = "a bundle didn't read"
                else:
                    key = (meta["session"], meta["rgb_seq"], meta["t_host"])
                    if key not in self._seen:
                        self._seen.append(key)
                        if not self.sessions or self.sessions[-1] != meta["session"]:
                            self.sessions.append(meta["session"])
                        self.mailbox.put(Bundle(body, meta["session"], meta["rgb_seq"], meta["t_host"], arrival_unix, arrival_mono))
                        fresh, last_new = True, arrival_mono
            elif status in (401, 403):
                self.errors += 1
                self.status, self.why = "refused", "the camera refused the token"
            elif status is not None:
                self.errors += 1
                why = "the camera answered %s" % status
            else:
                self.errors += 1
            if fresh:
                self.status, self.why, wait = "ok", None, 0.0
            else:
                if status not in (401, 403) and arrival_mono - last_new > self.stale_s:
                    self.status, self.why = "offline", why or "no new frame for %d s" % round(arrival_mono - last_new)
                if status != 200:   # a growing wait after an error, never past max_wait_s
                    wait = min(self.max_wait_s, wait * 2 if wait else self.poll_s)
            delay = (wait or self.poll_s) - (clock() - started)
            if delay > 0:
                self.halt.wait(delay)
