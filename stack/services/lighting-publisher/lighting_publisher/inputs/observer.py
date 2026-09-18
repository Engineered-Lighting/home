"""The observer adapter: a polled HTTP reading of typed presence.

The observer runs on the AI box and answers with its current typed reading of
each camera. The publisher polls it every ``OBSERVER_POLL_S`` seconds and uses
it for two things only: to corroborate Frigate (a credible person is a Frigate
person the fresh observer agrees with) and to tell the TV machine whether a
person track is in the living room.

Transport rules, enforced here and not by convention:

- the URL must be ``http`` or ``https`` with a host: nothing else is opened;
- proxies are refused (an empty ``ProxyHandler``), so no environment variable
  can redirect the poll off the LAN;
- redirects are refused (a handler that raises), so the observer cannot move
  the publisher to another host;
- one short timeout, one response, a size cap, and no cookies or auth headers.

The reply is parsed tolerantly: the observer's schema is owned elsewhere, so
this reads presence from whichever of the documented shapes is present and
records ``None`` (no reading) rather than guessing. Freshness is the age of
the last successful poll, measured by the caller's clock; a reading with no
usable camera at all is still a successful poll, because the observer
answering "nobody" is evidence.
"""
from __future__ import annotations

import datetime as dt
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Mapping

DEFAULT_OBSERVER_URL = "http://192.168.0.100:8767"
DEFAULT_STATE_PATH = "/api/state"
DEFAULT_TIMEOUT_S = 3.0
MAX_BYTES = 4 * 1024 * 1024
"""The observer's ``/api/state`` is its whole state, and it is large: measured
at 717 KiB on 2026-09-18, of which 558 KiB is the base64 ``pose_image`` of each
worker's last frame. At the old 256 KiB cap every single poll failed, which
would have left the observer permanently stale, the publisher permanently
unhealthy and every belief ``unknown`` for the whole shadow week.

The observer serves no narrower endpoint and honours no query parameter that
trims the body, so the cap is raised to fit it with headroom rather than the
body being trimmed. The cost is about 150 KB/s and one 717 KiB JSON parse every
five seconds. The images are decoded and dropped in the same breath: nothing
but counts survives into ``ObserverReading``, and the publisher neither logs
nor forwards a payload. A narrow presence endpoint on the observer is the right
fix and is an observer change, which needs the owner's window because it
restarts live inference."""
USER_AGENT = "lighting-publisher/0.1"

PRESENCE_BOOL_KEYS = ("person_present", "present", "occupied", "has_person", "person")
PRESENCE_COUNT_KEYS = ("person_count", "people_count", "persons", "people", "tracks", "count")
CAMERA_CONTAINER_KEYS = ("cameras", "by_camera", "camera_state")

LATEST_KEY = "latest"
DETECTOR_WORKERS = ("objects", "rtmw", "jepa")
"""The workers whose people lists are read, most trusted first.

``objects`` is the YOLOX full-scene scan and carries ``total_people``; ``rtmw``
is the pose detector; ``jepa`` carries V-JEPA's tracks. All three look at the
frame themselves.

``cameras.<name>.inference_gate.occupancy`` is deliberately NOT read, although
it is the one field in the payload that says "occupied" in words. Its own
``reason`` on this house is "person detected by Frigate": it is derived from
Frigate, and the estimator asks the observer to CORROBORATE Frigate. Reading it
would make that corroboration agree with Frigate by construction, so a stuck
Frigate zone would read as confirmed by vision and the night guard would rest
on one sensor while appearing to rest on two."""


class ObserverError(RuntimeError):
    """A poll failed. The message names the failure class, never a payload."""


class RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """Any redirect is a failure: the observer's address is configuration."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        raise urllib.error.HTTPError(req.full_url, code, "redirect refused", headers, fp)


class UrllibTransport:
    """The only thing in the publisher that opens an outbound connection."""

    def __init__(self, timeout_s: float = DEFAULT_TIMEOUT_S, max_bytes: int = MAX_BYTES) -> None:
        self.timeout_s = float(timeout_s)
        self.max_bytes = int(max_bytes)
        # Built by hand rather than with ``build_opener``: that helper also
        # installs the file, ftp and data handlers and a ProxyHandler that
        # reads ``http_proxy`` from the environment. This opener knows http
        # and https only, and an empty ProxyHandler registers no proxy at
        # all, so no environment variable can move the poll off the LAN.
        self._opener = urllib.request.OpenerDirector()
        for handler in (urllib.request.ProxyHandler({}),
                        urllib.request.HTTPHandler(),
                        urllib.request.HTTPSHandler(),
                        urllib.request.HTTPDefaultErrorHandler(),
                        urllib.request.HTTPErrorProcessor(),
                        RefuseRedirects()):
            self._opener.add_handler(handler)

    def fetch(self, url: str) -> bytes:
        """GET ``url`` and return at most ``max_bytes`` of body."""
        request = urllib.request.Request(url, method="GET", headers={
            "Accept": "application/json", "User-Agent": USER_AGENT})
        try:
            with self._opener.open(request, timeout=self.timeout_s) as response:
                status = getattr(response, "status", None)
                if status is not None and int(status) != 200:
                    raise ObserverError(f"observer returned status {int(status)}")
                return response.read(self.max_bytes)
        except ObserverError:
            raise
        except urllib.error.HTTPError as exc:
            raise ObserverError(f"observer http error {exc.code}") from None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise ObserverError(f"observer unreachable ({type(exc).__name__})") from None


@dataclass(frozen=True)
class ObserverCamera:
    """The observer's typed reading for one camera."""

    present: bool | None = None
    people: int | None = None

    @property
    def has_reading(self) -> bool:
        return self.present is not None


@dataclass(frozen=True)
class ObserverReading:
    """One successful poll."""

    at: dt.datetime
    cameras: Mapping[str, ObserverCamera] = field(default_factory=dict)
    cameras_read: int = 0

    def present(self, camera: str) -> bool | None:
        found = self.cameras.get(camera)
        return None if found is None else found.present

    def track_in(self, camera: str) -> bool:
        """True only on an affirmative reading: no reading is not a track."""
        return self.present(camera) is True

    def as_dict(self) -> dict:
        return {"at": self.at.isoformat(timespec="seconds"), "cameras_read": self.cameras_read,
                "cameras": sorted(self.cameras)}


def _coerce_camera(value: object) -> ObserverCamera:
    """Read one camera's entry in whichever documented shape it arrives."""
    if isinstance(value, bool):
        return ObserverCamera(present=value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return ObserverCamera(present=value > 0, people=int(value))
    if isinstance(value, list):
        return ObserverCamera(present=len(value) > 0, people=len(value))
    if not isinstance(value, dict):
        return ObserverCamera()
    for key in PRESENCE_BOOL_KEYS:
        found = value.get(key)
        if isinstance(found, bool):
            return ObserverCamera(present=found, people=None)
    for key in PRESENCE_COUNT_KEYS:
        found = value.get(key)
        if isinstance(found, bool):
            continue
        if isinstance(found, (int, float)):
            return ObserverCamera(present=found > 0, people=int(found))
        if isinstance(found, list):
            return ObserverCamera(present=len(found) > 0, people=len(found))
    return ObserverCamera()


def _camera_from_latest(workers: object) -> ObserverCamera:
    """Read one camera from ``latest.<camera>``: {worker: {..., people: [...]}}.

    The first worker in ``DETECTOR_WORKERS`` with a valid result and a people
    list wins. ``valid`` false is a worker that did not produce a result this
    window, which is not evidence that nobody is there, so it is passed over
    rather than read as empty.
    """
    if not isinstance(workers, dict):
        return ObserverCamera()
    for name in DETECTOR_WORKERS:
        body = workers.get(name)
        if not isinstance(body, dict) or body.get("valid") is False:
            continue
        total = body.get("total_people")
        if isinstance(total, bool):
            total = None
        if isinstance(total, (int, float)):
            return ObserverCamera(present=total > 0, people=int(total))
        people = body.get("people")
        if isinstance(people, list):
            return ObserverCamera(present=len(people) > 0, people=len(people))
    return ObserverCamera()


def parse_reading(data: object, now: dt.datetime) -> ObserverReading:
    """Build a reading from the decoded body; unknown shapes read as empty.

    The observer this publisher runs against puts its per-camera detector
    results under ``latest``, and its ``cameras`` map carries stream health and
    an inference gate rather than presence, so ``latest`` is read first. The
    documented flat shapes are still accepted underneath, because they are what
    the unit tests and any other observer would send.
    """
    payload = data
    if isinstance(payload, dict) and not any(
            k in payload for k in CAMERA_CONTAINER_KEYS + (LATEST_KEY,)):
        inner = payload.get("state")
        if isinstance(inner, dict):
            payload = inner
    cameras: dict[str, ObserverCamera] = {}
    if isinstance(payload, dict):
        latest = payload.get(LATEST_KEY)
        if isinstance(latest, dict):
            for name, workers in latest.items():
                if isinstance(name, str):
                    reading = _camera_from_latest(workers)
                    if reading.has_reading:
                        cameras[name] = reading
        container = None
        for key in CAMERA_CONTAINER_KEYS:
            candidate = payload.get(key)
            if isinstance(candidate, dict):
                container = candidate
                break
        if container is not None:
            for name, value in container.items():
                if isinstance(name, str) and name not in cameras:
                    cameras[name] = _coerce_camera(value)
    read = sum(1 for camera in cameras.values() if camera.has_reading)
    return ObserverReading(at=now, cameras=cameras, cameras_read=read)


class ObserverClient:
    """Polls the observer and keeps the last reading and the last failure."""

    def __init__(self, url: str = DEFAULT_OBSERVER_URL, state_path: str = DEFAULT_STATE_PATH,
                 timeout_s: float = DEFAULT_TIMEOUT_S, transport: object | None = None) -> None:
        self.url = self._validate(url, state_path)
        self.transport = transport if transport is not None else UrllibTransport(timeout_s)
        self.last: ObserverReading | None = None
        self.last_error: str | None = None
        self.polls = 0
        self.failures = 0

    @staticmethod
    def _validate(url: str, state_path: str) -> str:
        """Refuse anything that is not an http(s) URL with a host."""
        parsed = urllib.parse.urlsplit((url or "").strip())
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("OBSERVER_URL must be an http(s) URL with a host")
        path = parsed.path.rstrip("/")
        if not path:
            path = state_path
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))

    def poll(self, now: dt.datetime) -> ObserverReading:
        """One poll. Raises ``ObserverError``; the caller decides what that means."""
        self.polls += 1
        try:
            body = self.transport.fetch(self.url)
        except ObserverError as exc:
            self.failures += 1
            self.last_error = str(exc)
            raise
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            self.failures += 1
            self.last_error = "observer sent a body that is not JSON"
            raise ObserverError(self.last_error) from None
        reading = parse_reading(data, now)
        self.last = reading
        self.last_error = None
        return reading

    def as_dict(self) -> dict:
        """Counters for ``/healthz``; the URL is configuration, not a secret."""
        return {"url": self.url, "polls": self.polls, "failures": self.failures,
                "last_error": self.last_error,
                "last": self.last.as_dict() if self.last is not None else None}
