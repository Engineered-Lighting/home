"""Local socket-pair HTTP fixtures; never contact an installed camera."""
import base64
from contextlib import contextmanager
from dataclasses import replace
import io
import json
import socket
import threading
import time
import unittest

from home_shared.adapter import Work
from home_shared.camera_client import CameraBusy, CameraClient, CameraDeadline, CameraUnavailable
from home_shared.gateway import MAX_IMAGE_BYTES, jpeg_dimensions
from test_gateway import header_fixture


def work_fixture():
    return Work("request", "victoria", "den_oak", "den", "adapter", 1, 1,
                1005, 5, 1020, 0, "arrival")


def metadata_fixture(**overrides):
    value = {"schema": 1, "session": "capture", "sequence": 7, "width": 640,
             "height": 360, "quality": 85, "source_clock": "depthai_host_monotonic",
             "monotonic_clock_scope": "python_monotonic_on_camera_host",
             "timestamp_semantics": "commissioned_source_frame_timestamp_mapping",
             "observed_at_unix_ns": 1005_000_000_000, "observed_monotonic_seconds": 5,
             "uncertainty_seconds": .1, "validation_id": "fixture-map"}
    value.update(overrides)
    return base64.urlsafe_b64encode(json.dumps(value).encode()).decode()


def response_fixture(*, jpeg=None, status=200, headers=None):
    jpeg = header_fixture() if jpeg is None else jpeg
    values = [("Content-Type", "image/jpeg"), ("Content-Length", str(len(jpeg))),
              ("X-Victoria-Frame-Metadata", metadata_fixture())] if headers is None else headers
    return (f"HTTP/1.1 {status} Fixture\r\n" + "".join(f"{k}: {v}\r\n" for k, v in values)
            + "\r\n").encode() + jpeg


@contextmanager
def peer(response, *, clock=lambda: 1005.2, monotonic=lambda: 5.2, decode=jpeg_dimensions,
         before_send=None):
    """Exercise real HTTP framing on a local socket, with only a fake connector."""
    client_socket, server_socket = socket.socketpair()
    calls, requests = [], []
    server_socket.settimeout(2)

    def connect(address, *, timeout):
        calls.append((address, timeout))
        return client_socket

    def serve():
        try:
            with server_socket, server_socket.makefile("rb") as reader:
                lines = []
                while True:
                    line = reader.readline()
                    lines.append(line)
                    if line in (b"", b"\r\n"):
                        break
                requests.append(b"".join(lines))
                if before_send is not None:
                    before_send()
                if callable(response):
                    response(server_socket)
                else:
                    server_socket.sendall(response)
        except OSError:
            pass  # deadline tests deliberately shut down the peer

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    client = CameraClient("fixture-secret", _connect=connect, _decode=decode,
                          clock=clock, monotonic=monotonic)
    try:
        yield client, calls, requests
    finally:
        client_socket.close()
        server_socket.close()
        thread.join(timeout=2)
        if thread.is_alive():
            raise AssertionError("fixture peer did not stop")


class CameraClientTests(unittest.TestCase):
    def fetch(self, client, work=None, **overrides):
        options = {"expected_capture_session": "capture", "cross_host_uncertainty": .2}
        options.update(overrides)
        return client.fetch(work or work_fixture(), **options)

    def test_fixed_authenticated_endpoint_preserves_source_time(self):
        with peer(response_fixture()) as (client, calls, requests):
            frame = self.fetch(client)
            self.assertEqual(frame.capture_session, "capture")
            self.assertEqual(frame.frame_id, "7")
            self.assertEqual((frame.observed_at, frame.observed_mono), (1005, 5))
            self.assertEqual(frame.uncertainty_seconds, .1)  # no double charging
            self.assertEqual(calls[0][0], ("127.0.0.1", 8090))
            self.assertLessEqual(calls[0][1], 15)
            self.assertIn(b"GET /frame.jpg HTTP/1.1\r\n", requests[0])
            self.assertIn(b"Authorization: Bearer fixture-secret\r\n", requests[0])

    def test_default_codec_accepts_a_real_bounded_jpeg(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow required for production codec acceptance")
        from home_shared.gateway import verify_snapshot_image
        output = io.BytesIO()
        Image.new("RGB", (640, 360), (40, 50, 60)).save(output, format="JPEG", quality=85)
        with peer(response_fixture(jpeg=output.getvalue()), decode=verify_snapshot_image) as (client, _, __):
            self.assertEqual(self.fetch(client).jpeg, output.getvalue())

    def test_default_codec_rejects_marker_only_image(self):
        from home_shared.gateway import verify_snapshot_image
        with peer(response_fixture(), decode=verify_snapshot_image) as (client, _, __):
            with self.assertRaises(CameraUnavailable):
                self.fetch(client)

    def test_redirect_and_error_never_follow_or_retry(self):
        for status in (301, 302, 401, 503):
            with self.subTest(status=status), peer(response_fixture(status=status)) as (client, calls, _):
                with self.assertRaises(CameraUnavailable):
                    self.fetch(client)
                self.assertEqual(len(calls), 1)

    def test_rejects_ambiguous_encoding_and_unbounded_headers(self):
        good = [("Content-Type", "image/jpeg"), ("Content-Length", str(len(header_fixture()))),
                ("X-Victoria-Frame-Metadata", metadata_fixture())]
        cases = [good + [("Content-Length", "10")], good + [("Content-Encoding", "gzip")],
                 good + [("Transfer-Encoding", "chunked")],
                 [(k, "text/html" if k == "Content-Type" else v) for k, v in good],
                 [(k, str(MAX_IMAGE_BYTES + 1) if k == "Content-Length" else v) for k, v in good],
                 [(k, "x" * 8193 if k == "X-Victoria-Frame-Metadata" else v) for k, v in good],
                 [(k, "-1" if k == "Content-Length" else v) for k, v in good]]
        for headers in cases:
            with self.subTest(headers=[k for k, _ in headers]), peer(response_fixture(headers=headers)) as (client, _, __):
                with self.assertRaises(CameraUnavailable):
                    self.fetch(client)

    def test_rejects_short_body_and_invalid_metadata(self):
        for payload in (response_fixture()[:-4], response_fixture().replace(metadata_fixture().encode(), b"bad")):
            with self.subTest(size=len(payload)), peer(payload) as (client, _, __):
                with self.assertRaises(CameraUnavailable):
                    self.fetch(client)

    def test_session_freshness_and_uncertainty_fail_closed(self):
        for options, clock in (({"expected_capture_session": "restarted"}, 1005.2),
                               ({}, 1008), ({"cross_host_uncertainty": 1}, 1005.2)):
            with self.subTest(options=options, clock=clock), peer(response_fixture(), clock=lambda: clock) as (client, _, __):
                with self.assertRaises(CameraUnavailable):
                    self.fetch(client, **options)

    def test_wrong_site_expired_or_future_work_never_connects(self):
        calls = []
        client = CameraClient("fixture", clock=lambda: 1005, monotonic=lambda: 5,
                              _connect=lambda *a, **kw: calls.append(a))
        for work in (replace(work_fixture(), site_id="echo"),
                     replace(work_fixture(), scheduled_mono=-10, scheduled_at=990, request_deadline=1005),
                     replace(work_fixture(), scheduled_mono=6)):
            with self.subTest(work=work), self.assertRaises((ValueError, CameraDeadline)):
                self.fetch(client, work)
        self.assertEqual(calls, [])

    def test_header_injection_and_secret_bearing_errors_are_rejected(self):
        for token in ("", "secret\r\nX-Test: value", "x" * 4097, "private\N{SNOWMAN}"):
            with self.assertRaises(ValueError) as raised:
                CameraClient(token)
            self.assertNotIn("private", str(raised.exception))
        def failed_connection(*args, **kwargs):
            raise OSError("fixture-secret peer detail")
        client = CameraClient("fixture-secret", clock=lambda: 1005, monotonic=lambda: 5, _connect=failed_connection)
        with self.assertRaises(CameraUnavailable) as raised:
            self.fetch(client)
        self.assertNotIn("fixture-secret", str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)

    def test_concurrent_fetch_is_rejected_while_first_is_active(self):
        entered, release = threading.Event(), threading.Event()
        results = []
        def pause():
            entered.set()
            release.wait(timeout=2)
        with peer(response_fixture(), before_send=pause) as (client, calls, _):
            first = threading.Thread(target=lambda: results.append(self.fetch(client)))
            first.start()
            try:
                self.assertTrue(entered.wait(timeout=1))
                with self.assertRaises(CameraBusy):
                    self.fetch(client)
            finally:
                release.set()
                first.join(timeout=2)
            self.assertEqual(len(calls), 1)
            self.assertEqual(len(results), 1)

    def test_deadline_interrupts_incomplete_http_headers(self):
        entered, release = threading.Event(), threading.Event()
        def pause():
            entered.set()
            release.wait(timeout=2)
        start = time.monotonic()
        wall = time.time()
        work = replace(work_fixture(), scheduled_mono=start - 14.75,
                       scheduled_at=wall - 14.75, request_deadline=wall + .25)
        with peer(response_fixture(), before_send=pause, clock=time.time, monotonic=time.monotonic) as (client, _, __):
            try:
                with self.assertRaises(CameraUnavailable):
                    self.fetch(client, work)
                self.assertLess(time.monotonic() - start, 1.5)
            finally:
                release.set()
            self.assertTrue(entered.is_set())

    def test_timeout_retains_permit_until_decoder_exits(self):
        entered, release, exited = threading.Event(), threading.Event(), threading.Event()
        def stuck_decode(_jpeg):
            entered.set()
            release.wait(timeout=2)
            exited.set()
        start, wall = time.monotonic(), time.time()
        work = replace(work_fixture(), scheduled_mono=start - 14.75,
                       scheduled_at=wall - 14.75, request_deadline=wall + .25)
        with peer(response_fixture(), decode=stuck_decode, clock=time.time, monotonic=time.monotonic) as (client, calls, _):
            try:
                with self.assertRaises(CameraDeadline):
                    self.fetch(client, work)
                fresh = replace(work_fixture(), scheduled_mono=time.monotonic(), scheduled_at=time.time())
                fresh = replace(fresh, request_deadline=fresh.scheduled_at + 15)
                with self.assertRaises(CameraBusy):
                    self.fetch(client, fresh)
                self.assertEqual(len(calls), 1)
            finally:
                release.set()
            self.assertTrue(entered.is_set())
            self.assertTrue(exited.wait(timeout=1))

    def test_absolute_deadline_interrupts_drip_fed_headers_and_body(self):
        for phase in ("headers", "body"):
            exited = threading.Event()
            def drip(connection):
                try:
                    if phase == "headers":
                        connection.sendall(b"HTTP/1.1 200 Fixture\r\nX-Slow: ")
                    else:
                        connection.sendall(response_fixture().split(b"\r\n\r\n")[0] + b"\r\n\r\n")
                    for _ in range(60):
                        connection.sendall(b"x")
                        time.sleep(.02)  # each read arrives before its socket timeout
                finally:
                    exited.set()
            start, wall = time.monotonic(), time.time()
            work = replace(work_fixture(), scheduled_mono=start - 14.75,
                           scheduled_at=wall - 14.75, request_deadline=wall + .25)
            with self.subTest(phase=phase), peer(drip, clock=time.time, monotonic=time.monotonic) as (client, _, __):
                with self.assertRaises(CameraUnavailable):
                    self.fetch(client, work)
                self.assertLess(time.monotonic() - start, 1)
                self.assertTrue(exited.wait(timeout=1))  # shutdown reaches the trickling peer
                self.assertTrue(client._inflight.acquire(timeout=1))
                client._inflight.release()


if __name__ == "__main__":
    unittest.main()
