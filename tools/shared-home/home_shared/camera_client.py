"""Authenticated same-host preview transport; no caller-selected URLs.

Construct only in the Victoria Jetson adapter process, alongside the camera
owner. A bearer authenticates the reader; the fixed loopback listener and its
service ownership are part of the commissioned host boundary. This client does
not attest clock calibration, camera health or the current occupancy generation.
The adapter must revalidate those when binding the returned frame.
"""
from __future__ import annotations

import http.client
import queue
import re
import socket
import threading
import time
from typing import Callable

from .adapter import Frame, Work, finite, validate_frame
from .gateway import MAX_IMAGE_BYTES, verify_snapshot_image
from .protocol import camera_frame


class CameraUnavailable(RuntimeError):
    """No usable preview was obtained; messages never include credentials."""


class CameraBusy(CameraUnavailable):
    pass


class CameraDeadline(CameraUnavailable):
    pass


class CameraClient:
    """One bounded fetch, without redirects, proxies, retries or image caching.

    The caller's immutable work deadline covers connection, headers, body and
    decoding. A timed-out worker retains the single-fetch permit until it exits;
    subsequent calls cannot accumulate workers. Socket shutdown interrupts a
    blocked response. Private injected dependencies are for local tests only.
    """

    def __init__(self, token: str, *, monotonic: Callable[[], float] = time.monotonic,
                 clock: Callable[[], float] = time.time,
                 _connect: Callable = socket.create_connection,
                 _decode: Callable[[bytes], None] = verify_snapshot_image):
        if (not isinstance(token, str) or not 1 <= len(token) <= 4096
                or re.fullmatch(r"[A-Za-z0-9._~+/=-]+", token) is None):
            raise ValueError("camera bearer must be a bounded HTTP token")
        self._token = token
        self._monotonic, self._clock = monotonic, clock
        self._connect, self._decode = _connect, _decode
        self._inflight = threading.Lock()

    def fetch(self, work: Work, *, expected_capture_session: str,
              cross_host_uncertainty: float) -> Frame:
        if ((work.site_id, work.camera_id, work.room_id) != ("victoria", "den_oak", "den")
                or not isinstance(expected_capture_session, str)
                or not 1 <= len(expected_capture_session) <= 128
                or not finite(cross_host_uncertainty) or not 0 <= cross_host_uncertainty <= 1
                or not all(finite(v) for v in (work.scheduled_at, work.scheduled_mono, work.request_deadline))
                or work.request_deadline != work.scheduled_at + 15):
            raise ValueError("registered camera work and valid timing required")

        def remaining() -> float:
            mono, wall = self._monotonic(), self._clock()
            if (not finite(mono) or not finite(wall)
                    or mono < work.scheduled_mono or wall < work.scheduled_at):
                raise CameraDeadline("camera request clock is invalid")
            budget = min(work.scheduled_mono + 15 - mono, work.request_deadline - wall)
            if budget <= 0:
                raise CameraDeadline("camera request deadline elapsed")
            return budget

        remaining()
        if not self._inflight.acquire(blocking=False):
            raise CameraBusy("camera fetch still in progress")
        result: queue.Queue = queue.Queue(maxsize=1)
        cancelled = threading.Event()
        socket_guard = threading.Lock()
        current_socket: list[socket.socket] = []

        def worker() -> None:
            outcome: Frame | CameraUnavailable
            try:
                with self._connect(("127.0.0.1", 8090), timeout=remaining()) as connection:
                    with socket_guard:
                        if cancelled.is_set():
                            raise CameraDeadline("camera request deadline elapsed")
                        current_socket.append(connection)
                    connection.settimeout(remaining())
                    request = ("GET /frame.jpg HTTP/1.1\r\nHost: 127.0.0.1:8090\r\n"
                               "Accept: image/jpeg\r\nConnection: close\r\n"
                               f"Authorization: Bearer {self._token}\r\n\r\n")
                    connection.sendall(request.encode("ascii"))
                    with http.client.HTTPResponse(connection) as response:
                        response.begin()
                        remaining()
                        if response.status != 200:
                            raise CameraUnavailable("camera preview unavailable")
                        headers = response.headers
                        for name in ("Content-Length", "Content-Type", "X-Victoria-Frame-Metadata"):
                            if len(headers.get_all(name, [])) != 1:
                                raise CameraUnavailable("camera response headers invalid")
                        if (headers.get("Content-Type", "").lower() != "image/jpeg"
                                or headers.get_all("Transfer-Encoding")
                                or len(headers.get_all("Content-Encoding", [])) > 1
                                or headers.get("Content-Encoding", "identity").lower() != "identity"):
                            raise CameraUnavailable("camera response encoding invalid")
                        length = headers["Content-Length"]
                        metadata = headers["X-Victoria-Frame-Metadata"]
                        if (not re.fullmatch(r"[0-9]{1,6}", length)
                                or not 4 <= int(length) <= MAX_IMAGE_BYTES or len(metadata) > 8192):
                            raise CameraUnavailable("camera response exceeds bounds")
                        connection.settimeout(remaining())
                        jpeg = response.read(int(length))
                        remaining()
                        if len(jpeg) != int(length):
                            raise CameraUnavailable("camera response incomplete")
                        frame = camera_frame(jpeg, metadata)
                        if frame.capture_session != expected_capture_session:
                            raise CameraUnavailable("camera capture session changed")
                        self._decode(jpeg)
                        remaining()
                        validate_frame(frame, work, now_mono=self._monotonic(), now_unix=self._clock(),
                                       cross_host_uncertainty=cross_host_uncertainty)
                        outcome = frame
            except CameraUnavailable as exc:
                outcome = exc
            except Exception:
                # HTTP/parser/codec exceptions can contain peer data. Keep them
                # out of logs, tracebacks and user-visible transport failures.
                outcome = CameraUnavailable("camera preview transport or validation failed")
            finally:
                with socket_guard:
                    current_socket.clear()
                self._inflight.release()
            result.put(outcome)

        thread = threading.Thread(target=worker, name="victoria-preview-fetch", daemon=True)
        try:
            thread.start()
        except Exception:
            self._inflight.release()
            raise CameraUnavailable("camera fetch could not start") from None
        try:
            try:
                outcome = result.get(timeout=remaining())
            except queue.Empty:
                raise CameraDeadline("camera request deadline elapsed") from None
            remaining()
            if isinstance(outcome, CameraUnavailable):
                raise outcome from None
            try:
                validate_frame(outcome, work, now_mono=self._monotonic(), now_unix=self._clock(),
                               cross_host_uncertainty=cross_host_uncertainty)
            except ValueError:
                raise CameraUnavailable("camera preview expired before delivery") from None
            return outcome
        finally:
            cancelled.set()
            with socket_guard:
                for connection in current_socket:
                    try:
                        connection.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
