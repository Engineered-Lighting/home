"""Step 3's frame source: the den camera's newest bundle, about 5 a second (plan L1). Standard library only.

One thread polls the camera's newest bundle with the camera's token, every poll_s. A bundle it has seen before (same
session, RGB sequence and host time, as take.py dedupes) is skipped. Each new one goes into a one-slot mailbox: if the
loop hasn't taken the last one yet, the older is replaced and counted as dropped, so the loop always works on the
newest frame and lag can't build up. It keeps the bundle's bytes, never writes them anywhere.

The link (KeptLink): the camera's /frame-slim.npz (den-camera-slim-1) on one connection kept open, asked with
If-None-Match. It's the newest frame without the left and right images, which nothing here reads, so every reader
(den-take's check, den-observe's takes, body.py) reads it as it reads /frame.npz. A pull costs one round trip plus the
bytes, with no new TCP connection and no slow start each time. A 304 means the camera's frame is still the one we
have: nothing new and no error, so it asks again recheck_s later. A camera without the slim path (404) gets
den-take's own /frame.npz pull, a new connection each time as before, and is asked for the slim path again
SLIM_RETRY_S later. A slim answer that doesn't declare den-camera-slim-1 is refused.

Status, for the stream: ok; offline (nothing new for stale_s, or the camera doesn't answer); refused (401 or 403:
the token). After an error it waits longer each time, up to max_wait_s, and tries again; it never stops on its own.

The camera's paths (plan step 4): den-live asks the camera for /frame-slim.npz, /frame.npz and /calibration only,
never /frame.jpg or anything else. The camera serves one JPEG at a time and refuses a second, and the Lab's previews
and its Qwen ring need it; the two bundles are prebuilt latest bundles with request slots of their own. AllowedCamera
holds den-take's client and the link to those paths, refusing any other before a request is made. Every den-live tool
that reads the camera gets its client from camera_client(): the sofa check, step 2's take, the live loop and the
service. Only the live loop's source pulls through the link; takes keep the full /frame.npz.
"""
import http.client
import json
import threading
import time
from collections import deque
from dataclasses import dataclass
from functools import partial

from body_clock import AGE_HEADER, lease_clock

SLIM_PATH = "/frame-slim.npz"
SLIM_SCHEMA = "den-camera-slim-1"
SCHEMA_HEADER = "X-Den-Bundle-Schema"
ALLOWED_PATHS = ("/frame.npz", "/calibration", SLIM_PATH)
SLIM_RETRY_S = 60.0   # a camera without the slim path is asked for it again this long after its 404
RECHECK_S = 0.05      # after a 304, ask again this long after the last ask began: about a round trip on the link


class PathRefused(Exception):
    pass


class SchemaRefused(http.client.HTTPException):
    """The camera's slim path answered with a bundle that doesn't declare den-camera-slim-1."""


class AllowedCamera:
    """den-take's Camera, held to ALLOWED_PATHS: any other path is refused here, before a connection is opened. It has
    no health(): den-take's /health check is a path this client doesn't ask for."""

    def __init__(self, camera, *, read_body_bundle=None, link=None):
        self._camera = camera
        self._read_body_bundle = read_body_bundle
        self.link = link
        self.host, self.port = camera.host, camera.port

    def __repr__(self):
        return "AllowedCamera(%s:%s)" % (self.host, self.port)

    def get(self, path, timeout=5.0):
        if type(path) is not str or path not in ALLOWED_PATHS:
            raise PathRefused("den-live asks the camera for %s only" % " and ".join(ALLOWED_PATHS))
        return self._camera.get(path, timeout=timeout)

    def get_bundle(self, timeout=5.0):
        """Read the latest bundle (status 304: still the one we have) and its optional same-response exposure
        receipt: through the kept link when there is one, else /frame.npz on a new connection."""
        if self.link is not None:
            return self.link.get_bundle(timeout)
        return self._get_full(timeout)

    def _get_full(self, timeout):
        if self._read_body_bundle is None:
            status, body = self.get("/frame.npz", timeout)
            return status, body, None
        return self._read_body_bundle(timeout)

    def close(self):
        if self.link is not None:
            self.link.close()


def _age_receipt(response):
    values = [value for key, value in response.getheaders() if key.casefold() == AGE_HEADER.casefold()]
    try:
        return json.loads(values[0]) if len(values) == 1 and len(values[0]) <= 2048 else None
    except (ValueError, RecursionError):
        return None


def _get_body_bundle(camera, max_body, token, timeout):
    connection = http.client.HTTPConnection(camera.host, camera.port, timeout=timeout)
    try:
        headers = {"Authorization": "Bearer " + token} if token else {}
        connection.request("GET", "/frame.npz", headers=headers)
        response = connection.getresponse()
        body = response.read(max_body + 1)
        if len(body) > max_body:
            raise http.client.HTTPException("response too large")
        if response.length:
            raise http.client.IncompleteRead(body, response.length)
        return response.status, body, _age_receipt(response)
    finally:
        connection.close()


class KeptLink:
    """The camera's slim bundle on one connection kept open (the module's docstring, "The link"). One thread uses it:
    the source's. get_bundle(timeout) -> (status, body, receipt); full(timeout) is the /frame.npz pull it falls back
    on. counts: connections opened, 200s, 304s and fallbacks, for the benchmark and the tests."""

    # The camera closed an idle connection first: these, on a reused connection, are asked once more on a new one.
    CLOSED = (http.client.RemoteDisconnected, ConnectionResetError, ConnectionAbortedError, BrokenPipeError)

    def __init__(self, host, port, token, max_body, full, *, body_timing=False, clock=time.monotonic,
                 retry_s=SLIM_RETRY_S):
        self.host, self.port, self.max_body, self.full = host, port, max_body, full
        self._headers = {"Authorization": "Bearer " + token} if token else {}
        self.body_timing, self.clock, self.retry_s = body_timing, clock, retry_s
        self._connection = self._etag = self._slim_after = None
        self.mode = "slim"   # "full" while the camera hasn't the slim path
        self.counts = {"connections": 0, "slim": 0, "not_modified": 0, "full": 0}

    def close(self):
        connection, self._connection = self._connection, None
        if connection is not None:
            connection.close()

    def get_bundle(self, timeout=5.0):
        if self._slim_after is not None and self.clock() < self._slim_after:
            self.counts["full"] += 1
            return self.full(timeout)
        status, body, response = self._ask(timeout)
        if status == 404:   # a camera release without the slim path: the full bundle, as before
            self.close()
            self._etag, self._slim_after, self.mode = None, self.clock() + self.retry_s, "full"
            self.counts["full"] += 1
            return self.full(timeout)
        self._slim_after, self.mode = None, "slim"
        if status == 304:
            self.counts["not_modified"] += 1
            return 304, b"", None
        if status != 200:
            self._etag = None
            return status, body, None
        if response.getheader(SCHEMA_HEADER) != SLIM_SCHEMA:
            self.close()
            self._etag = None
            raise SchemaRefused("the camera's %s isn't %s" % (SLIM_PATH, SLIM_SCHEMA))
        self._etag = response.getheader("ETag")
        self.counts["slim"] += 1
        return 200, body, _age_receipt(response) if self.body_timing else None

    def _ask(self, timeout):
        headers = dict(self._headers, **({"If-None-Match": self._etag} if self._etag else {}))
        for attempt in (0, 1):
            reused = self._connection is not None
            if not reused:
                self._connection = http.client.HTTPConnection(self.host, self.port, timeout=timeout)
                self.counts["connections"] += 1
            connection = self._connection
            try:
                if connection.sock is not None:
                    connection.sock.settimeout(timeout)
                connection.request("GET", SLIM_PATH, headers=headers)
                response = connection.getresponse()
                body = response.read(self.max_body + 1)
            except self.CLOSED:
                self.close()
                if reused and attempt == 0:
                    continue
                raise
            except BaseException:
                self.close()
                raise
            if len(body) > self.max_body:
                self.close()
                raise http.client.HTTPException("response too large")
            if response.length:   # the connection closed part way through the body
                self.close()
                raise http.client.IncompleteRead(body, response.length)
            if response.will_close:
                self.close()
            return response.status, body, response


def camera_client(take, url, token, *, body_timing=False):
    """den-take's client held to the den-live paths, with the kept slim link for the live source's pulls and
    optional frame-age receipts."""
    camera = take.Camera(url, token)
    read_body_bundle = partial(_get_body_bundle, camera, take.MAX_BODY, token) if body_timing else None
    allowed = AllowedCamera(camera, read_body_bundle=read_body_bundle)
    allowed.link = KeptLink(camera.host, camera.port, token, take.MAX_BODY, allowed._get_full, body_timing=body_timing)
    return allowed


@dataclass(frozen=True)
class Bundle:
    data: bytes
    session: str
    rgb_seq: int
    t_host: float          # the Jetson's host monotonic clock at the RGB frame
    arrival_unix: float    # this PC's wall clock when the bytes arrived
    arrival_mono: float    # this PC's monotonic clock then
    fetch_s: float | None = None
    parse_s: float | None = None
    request_mono: float | None = None
    capture_age_receipt: dict | None = None
    receipt_arrival_mono: float | None = None


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
    def __init__(self, take, camera, mailbox, *, poll_s=0.2, stale_s=5.0, max_wait_s=5.0, recheck_s=RECHECK_S):
        super().__init__(daemon=True, name="den-live-source")
        self.take, self.mailbox = take, mailbox
        self.camera = camera if isinstance(camera, AllowedCamera) else AllowedCamera(camera)
        self.poll_s, self.stale_s, self.max_wait_s, self.recheck_s = poll_s, stale_s, max_wait_s, recheck_s
        self.halt = threading.Event()
        self.status, self.why = "starting", None
        self.polls = self.errors = 0
        self.sessions = []
        self.camera_session = None
        self._seen = deque(maxlen=64)

    def state(self):
        link = self.camera.link
        return {"status": self.status, "why": self.why, "session": self.camera_session,
                "link": link.mode if link is not None else "full"}

    def run(self):
        try:
            self._run()
        finally:
            self.camera.close()   # the kept connection, if any

    def _run(self):
        clock = self.take.clock
        last_new, wait = clock(), 0.0
        while not self.halt.is_set():
            started = clock()
            self.polls += 1
            request_mono = lease_clock()
            try:
                status, body, age = self.camera.get_bundle()
                why = None
            except SchemaRefused:
                status, body, age, why = None, b"", None, "the camera's slim bundle isn't %s" % SLIM_SCHEMA
            except (OSError, self.take.http.client.HTTPException) as exc:
                status, body, age, why = None, b"", None, "the camera did not answer (%s)" % type(exc).__name__
            receipt_arrival = lease_clock()
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
                        self.camera_session = meta["session"]
                        self.status, self.why = "ok", None
                        self.mailbox.put(Bundle(body, meta["session"], meta["rgb_seq"], meta["t_host"], arrival_unix,
                                                arrival_mono, fetch_s=arrival_mono - started, parse_s=clock() - arrival_mono,
                                                request_mono=request_mono, capture_age_receipt=age,
                                                receipt_arrival_mono=receipt_arrival))
                        fresh, last_new = True, arrival_mono
            elif status == 304:   # the camera's frame is still the one we have: nothing new, and no error
                wait = 0.0
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
                if status not in (200, 304):   # a growing wait after an error, never past max_wait_s
                    wait = min(self.max_wait_s, wait * 2 if wait else self.poll_s)
            delay = (wait or (self.recheck_s if status == 304 else self.poll_s)) - (clock() - started)
            if delay > 0:
                self.halt.wait(delay)
