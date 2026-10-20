"""Health contracts and actual local HTTP/TLS; never contact either home."""
from dataclasses import replace, asdict
import json
from pathlib import Path
import socket
import ssl
import threading
import time
import unittest
from unittest.mock import patch

from home_shared.health import ClockEvidence, HostClockEvidence, HealthProbe, HealthProbeConfig, StatsTimingEvidence


FIXTURES = Path(__file__).parent / "fixtures" / "gateway_client"


class Clocks:
    mono = 100.0
    wall = 1000.0
    def advance(self, seconds):
        self.mono += seconds
        self.wall += seconds


def config(**changes):
    value = HealthProbeConfig("localhost", 8971, FIXTURES / "ca.pem", "frigate-secret", "camera-secret",
                              "victoria-frigate", "hailo", 20, 2, "commissioned-1", "jetson", "boot-1",
                              "victoria-pi", "pi-boot-1", "echo-engine", "la-boot-1")
    return replace(value, **changes)


def camera():
    return {"healthy": True, "session": "capture-1", "bundle_age_seconds": .1,
            "encoder_age_seconds": .02, "error": None}


def stats(wall=995):
    return {"service": {"version": "0.18-0", "last_updated": int(wall), "uptime": 200},
            "cameras": {"den_oak": {"camera_fps": 10, "process_fps": 10, "skipped_fps": 0,
                                     "detection_enabled": True, "pid": 1, "capture_pid": 2, "ffmpeg_pid": 3}},
            "detectors": {"hailo": {"inference_speed": 11, "detection_start": 0, "pid": 4}}}


class ClockProvider:
    def __init__(self, mono, wall=time.time):
        self.mono = mono
        self.wall = wall
        self.changes = {}
        self.enabled = True
    def read(self):
        if not self.enabled:
            return None
        now = self.mono()
        return replace(ClockEvidence("jetson", "boot-1", "victoria-pi", "pi-boot-1", "echo-engine", "la-boot-1",
                                     "commissioned-1", "receipt-1",
                                     now, self.wall(), now + 5, .1, .2, .3), **self.changes)


class FakeProbe(HealthProbe):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.responses = {True: camera(), False: stats()}
        self.requests = []
        self.before_get = None
    def _get(self, camera, deadline):
        self.requests.append(camera)
        if self.before_get:
            self.before_get()
        return self.responses[camera]


class SyntheticTimingAuthority:
    """Test-only authority; never a production timing measurement implementation."""
    def __init__(self, mono):
        self.mono = mono
        self.enabled = True
        self.changes = {}
    def read_for(self, digest):
        if not self.enabled:
            return None
        now = self.mono()
        return replace(StatsTimingEvidence("victoria-frigate", "0.18-0", "victoria-pi", "pi-boot-1",
                                            "jetson", "boot-1", "commissioned-1", digest,
                                            "synthetic-timing-1", now, now + 5, 2),
                       **self.changes)


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.clocks = Clocks()
        self.provider = ClockProvider(lambda: self.clocks.mono, lambda: self.clocks.wall)
        self.timing = SyntheticTimingAuthority(lambda: self.clocks.mono)
        self.probe = FakeProbe(config(), clock_provider=self.provider, clock=lambda: self.clocks.wall,
                               stats_timing_provider=self.timing,
                               monotonic=lambda: self.clocks.mono)
        self.addCleanup(self.probe.stop)

    def collect(self):
        self.assertTrue(self.probe.poll())
        self.probe._worker.join(1)
        self.assertFalse(self.probe._worker.is_alive())
        return self.probe.read()

    def test_positive_health_has_separate_uncertainty_and_stable_continuity(self):
        first = self.collect()
        self.assertEqual(first.camera_session, "capture-1")
        self.assertEqual(first.cross_host_uncertainty, .3)
        self.assertEqual(first.checked_mono, 100)
        self.assertTrue(first.frigate_instance.startswith("probe:"))
        self.clocks.advance(1)
        second = self.collect()
        self.assertEqual(first.frigate_instance, second.frigate_instance)
        self.assertEqual(self.probe.requests, [True, False, True, False])

    def use_host_clock(self, **changes):
        legacy_reader = self.provider.read
        def read():
            value = asdict(legacy_reader())
            value["camera_host_uncertainty_seconds"] = value.pop("camera_source_uncertainty_seconds")
            value.update(version=2, **changes)
            return HostClockEvidence(**value)
        self.provider.read = read

    def test_host_clock_v2_preserves_boot_and_charges_only_la_as_cross_host(self):
        self.use_host_clock()
        sample = self.collect()
        self.assertEqual(sample.clock_evidence_version, 2)
        self.assertEqual(sample.camera_boot_id, "boot-1")
        self.assertEqual(sample.clock_validation_id, "receipt-1")
        self.assertEqual(sample.camera_host_uncertainty_seconds, .1)
        self.assertEqual(sample.cross_host_uncertainty, .3)

    def test_host_clock_wrong_boot_or_claimed_version_fails_before_probe(self):
        self.use_host_clock(camera_boot_id="wrong")
        self.assertIsNone(self.collect())
        self.assertEqual(self.probe.requests, [])
        self.provider.read = lambda: HostClockEvidence("jetson", "boot-1", "victoria-pi", "pi-boot-1",
            "echo-engine", "la-boot-1", "commissioned-1", "receipt", 101, 1001, 106, .1, .2, .3, version=1)
        self.clocks.advance(1)
        self.assertIsNone(self.collect())
        self.assertEqual(self.probe.requests, [])

    def test_host_clock_version_change_rotates_continuity(self):
        old = self.collect()
        self.use_host_clock()
        self.clocks.advance(1)
        new = self.collect()
        self.assertNotEqual(old.frigate_instance, new.frigate_instance)
        self.assertEqual(new.clock_evidence_version, 2)

    def test_new_authority_version_cannot_reuse_previous_cached_health(self):
        self.provider.current_evidence = lambda: (self.provider.read(), None)
        self.assertIsNotNone(self.collect())
        self.use_host_clock()
        self.assertIsNone(self.probe.read())

    def test_larger_host_clock_bound_revokes_cached_v2_sample(self):
        self.use_host_clock()
        self.provider.current_evidence = lambda: (self.provider.read(), None)
        self.assertIsNotNone(self.collect())
        old = self.provider.read
        self.provider.read = lambda: replace(old(), camera_host_uncertainty_seconds=.2)
        self.assertIsNone(self.probe.read())

    def test_camera_cache_expires_before_three_seconds(self):
        self.assertIsNotNone(self.collect())
        self.clocks.advance(2.9)
        self.assertIsNone(self.probe.read())

    def test_frigate_cached_measurement_age_never_renews(self):
        self.assertIsNotNone(self.collect())
        for _ in range(11):
            self.clocks.advance(1)
            self.assertIsNotNone(self.collect())
        self.clocks.advance(1)
        self.assertIsNone(self.collect())  # Packaging remains 995; collection-lag budget also expires.
        self.assertEqual(self.probe.last_error, "health_probe_failed")

    def test_expired_source_fails_even_with_current_probe_receipt(self):
        self.probe.responses[False]["service"]["last_updated"] = 970
        self.assertIsNone(self.collect())

    def test_pi_uncertainty_bounds_source_future_timestamp(self):
        self.probe.responses[False]["service"]["last_updated"] = 1001
        self.assertIsNone(self.collect())

    def test_no_clock_evidence_prevents_any_network_probe(self):
        self.provider.enabled = False
        self.assertIsNone(self.collect())
        self.assertEqual(self.probe.requests, [])

    def test_clock_scope_expiry_and_combined_budget_rejected(self):
        invalids = [{"camera_host_identity": "other"}, {"camera_boot_id": "old-boot"},
                    {"pi_host_identity": "other"}, {"pi_boot_id": "old-boot"},
                    {"la_host_identity": "other"}, {"la_boot_id": "old-boot"},
                    {"policy_revision": "other"}, {"valid_until_mono": 100},
                    {"checked_mono": 94}, {"la_uncertainty_seconds": .95},
                    {"pi_uncertainty_seconds": .95}, {"la_uncertainty_seconds": float("nan")}]
        for changes in invalids:
            with self.subTest(changes=changes):
                self.provider.changes = changes
                self.assertIsNone(self.collect())
                self.clocks.advance(1)

    def test_clock_receipt_expiry_cannot_be_replaced_on_read(self):
        self.provider.changes = {"valid_until_mono": 101}
        self.assertIsNotNone(self.collect())
        self.clocks.advance(1)
        self.provider.changes = {"valid_until_mono": 200}
        self.assertIsNone(self.probe.read())

    def test_first_probe_rejects_wall_clock_change_after_clock_receipt(self):
        def jump():
            self.clocks.wall -= 1
        self.probe.before_get = jump
        self.assertIsNone(self.collect())

    def test_clock_provider_may_finish_after_worker_start(self):
        original = self.provider.read
        def later():
            self.clocks.advance(.01)
            return original()
        self.provider.read = later
        self.assertIsNotNone(self.collect())

    def test_wall_jump_invalidates_cache_and_pending_fresh_probe(self):
        self.assertIsNotNone(self.collect())
        self.clocks.wall -= 1
        self.assertIsNone(self.probe.read())
        self.clocks.wall += 1
        self.clocks.advance(1)
        self.assertIsNotNone(self.collect())
        self.clocks.advance(1)
        self.clocks.wall -= 1
        self.assertIsNone(self.collect())

    def test_missing_processing_or_detector_never_passes_from_pids(self):
        self.probe.responses[False]["cameras"]["den_oak"]["process_fps"] = 0
        self.assertIsNone(self.collect())
        self.clocks.advance(1)
        self.probe.responses[False] = stats()
        self.probe.responses[False]["detectors"] = {}
        self.assertIsNone(self.collect())

    def test_normal_ongoing_detection_is_relative_to_source_snapshot(self):
        self.probe.responses[False]["detectors"]["hailo"]["detection_start"] = 995.5
        self.assertIsNotNone(self.collect())  # Not five seconds of hung inference.

    def test_session_pid_backward_stats_uptime_and_failure_rotate_continuity(self):
        previous = self.collect().frigate_instance
        changes = [lambda: self.probe.responses[True].update(session="capture-2"),
                   lambda: self.probe.responses[False]["cameras"]["den_oak"].update(capture_pid=9),
                   lambda: self.probe.responses[False]["service"].update(last_updated=994),
                   lambda: self.probe.responses[False]["service"].update(uptime=1)]
        for change in changes:
            change()
            self.clocks.advance(1)
            current = self.collect().frigate_instance
            self.assertNotEqual(previous, current)
            previous = current
        self.provider.enabled = False
        self.clocks.advance(1)
        self.assertIsNone(self.collect())
        self.provider.enabled = True
        self.clocks.advance(1)
        self.assertNotEqual(previous, self.collect().frigate_instance)

    def test_negative_or_stale_camera_ages_and_error_rejected(self):
        for change in ({"encoder_age_seconds": -1}, {"bundle_age_seconds": 3.1},
                       {"bundle_age_seconds": float("inf")}, {"healthy": 1}, {"error": "disconnected"}):
            self.probe.responses[True] = dict(camera(), **change)
            self.assertIsNone(self.collect())
            self.clocks.advance(1)

    def test_deadline_keeps_single_worker_and_rejects_late_completion(self):
        entered, release = threading.Event(), threading.Event()
        def block():
            entered.set()
            release.wait(2)
        self.probe.before_get = block
        self.probe.poll()
        try:
            self.assertTrue(entered.wait(1))
            self.clocks.advance(6)
            before = time.monotonic()
            self.assertIsNone(self.probe.read())
            self.assertFalse(self.probe.poll())
            self.assertLess(time.monotonic() - before, .1)
            self.assertEqual(self.probe.last_error, "health_probe_deadline")
        finally:
            release.set()
            self.probe._worker.join(1)
        self.assertIsNone(self.probe.read())

    def test_stop_is_nonblocking_and_prevents_late_publication(self):
        entered, release = threading.Event(), threading.Event()
        def block():
            entered.set()
            release.wait(2)
        self.probe.before_get = block
        self.probe.poll()
        self.assertTrue(entered.wait(1))
        before = time.monotonic()
        self.probe.stop()
        self.assertLess(time.monotonic() - before, .1)
        release.set()
        self.probe._worker.join(1)
        self.assertIsNone(self.probe.read())
        self.assertFalse(self.probe.poll())

    def test_policy_bounds_and_credentials_are_explicit(self):
        for changes in ({"max_stats_age_seconds": 31}, {"max_stats_age_seconds": True},
                        {"source_instance": ""}, {"camera_bearer": "bad\r\nheader"},
                        {"frigate_hostname": "https://other/"}):
            with self.assertRaises(ValueError): config(**changes)
        self.assertNotIn("secret", repr(config()))

    def test_worker_start_failure_closes_cache_without_escaping(self):
        self.assertIsNotNone(self.collect())
        self.clocks.advance(1)
        with patch("home_shared.health.threading.Thread.start", side_effect=RuntimeError("thread limit")):
            self.assertFalse(self.probe.poll())
        self.assertIsNone(self.probe.read())
        self.assertEqual(self.probe.last_error, "health_worker_unavailable")

    def test_probe_restart_creates_new_observer_continuity(self):
        first = self.collect().frigate_instance
        another = FakeProbe(config(), clock_provider=self.provider, clock=lambda: self.clocks.wall,
                            stats_timing_provider=self.timing,
                            monotonic=lambda: self.clocks.mono)
        self.addCleanup(another.stop)
        another.poll()
        another._worker.join(1)
        self.assertNotEqual(first, another.read().frigate_instance)

    def test_stats_without_independent_timing_evidence_remain_unknown(self):
        self.timing.enabled = False
        self.assertIsNone(self.collect())

    def test_stats_timing_scope_digest_expiry_and_collection_bound(self):
        for changes in ({"source_instance": "other"}, {"source_version": "other"},
                        {"pi_boot_id": "previous-boot"}, {"pi_host_identity": "other"},
                        {"camera_boot_id": "previous-boot"}, {"camera_host_identity": "other"},
                        {"policy_revision": "other"}, {"snapshot_digest": "wrong"},
                        {"valid_until_mono": 100}, {"checked_mono": 200}, {"checked_mono": 0},
                        {"valid_until_mono": float("nan")}, {"max_collection_lag_seconds": 3}):
            with self.subTest(changes=changes):
                self.timing.changes = changes
                self.assertIsNone(self.collect())
                self.clocks.advance(1)

    def test_stats_timing_receipt_cannot_be_renewed_by_cache_read(self):
        self.timing.changes = {"valid_until_mono": 101}
        self.assertIsNotNone(self.collect())
        self.clocks.advance(1)
        self.timing.changes = {"valid_until_mono": 200}
        self.assertIsNone(self.probe.read())

    def test_collection_lag_prevents_false_detector_stall_diagnosis(self):
        self.probe.responses[False]["detectors"]["hailo"]["detection_start"] = 991
        self.assertIsNotNone(self.collect())  # Earliest camera/detector sample can be 992.


class LocalJson:
    def __init__(self, value, *, tls=False, status=200, extra=""):
        self.value, self.status, self.extra = value, status, extra
        self.requests = []
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.listener.settimeout(2)
        self.port = self.listener.getsockname()[1]
        self.tls = None
        if tls:
            self.tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            self.tls.load_cert_chain(str(FIXTURES / "server.pem"), str(FIXTURES / "server-test-only.key"))
        self.thread = threading.Thread(target=self.run, daemon=True)
    def __enter__(self):
        self.thread.start()
        return self
    def __exit__(self, *_):
        self.listener.close()
        self.thread.join(3)
        if self.thread.is_alive():
            raise AssertionError("health fixture worker failed to stop")
    def run(self):
        try:
            raw, _ = self.listener.accept()
            raw.settimeout(2)
            with raw:
                stream = self.tls.wrap_socket(raw, server_side=True) if self.tls else raw
                with stream:
                    data = b""
                    while b"\r\n\r\n" not in data:
                        chunk = stream.recv(4096)
                        if not chunk:
                            return
                        data += chunk
                    self.requests.append(data)
                    body = self.value if isinstance(self.value, bytes) else json.dumps(self.value).encode()
                    stream.sendall((f"HTTP/1.1 {self.status} Test\r\nContent-Type: application/json\r\n"
                                    f"Content-Length: {len(body)}\r\n{self.extra}\r\n").encode() + body)
        except (OSError, ssl.SSLError):
            pass  # Refused TLS and deliberate client cancellation are expected.


class HealthSocketTests(unittest.TestCase):
    def probe(self, camera_server, frigate_server, **changes):
        def connect(address, timeout):
            ports = {("127.0.0.1", 8090): camera_server.port, ("localhost", 8971): frigate_server.port}
            return socket.create_connection(("127.0.0.1", ports[address]), timeout)
        probe = HealthProbe(config(**changes), clock_provider=ClockProvider(time.monotonic),
                            stats_timing_provider=SyntheticTimingAuthority(time.monotonic), _connect=connect)
        self.addCleanup(probe.stop)
        return probe

    def collect(self, probe):
        probe.poll()
        probe._worker.join(3)
        self.assertFalse(probe._worker.is_alive())
        return probe.read()

    def test_real_camera_http_and_verified_frigate_tls_fixed_paths(self):
        with LocalJson(camera()) as cam, LocalJson(stats(time.time() - 2), tls=True) as frigate:
            probe = self.probe(cam, frigate)
            sample = self.collect(probe)
            self.assertIsNotNone(sample)
            self.assertIn(b"GET /health HTTP/1.1", cam.requests[0])
            self.assertIn(b"Authorization: Bearer camera-secret", cam.requests[0])
            self.assertIn(b"GET /api/stats HTTP/1.1", frigate.requests[0])
            self.assertIn(b"Authorization: Bearer frigate-secret", frigate.requests[0])
            self.assertIn(b"X-Cache-Bypass: 1", frigate.requests[0])
            self.assertFalse(probe._tls.hostname_checks_common_name)
            self.assertIsNone(probe._tls.keylog_filename)

    def test_untrusted_frigate_certificate_never_delivers_health(self):
        with LocalJson(camera()) as cam, LocalJson(stats(time.time() - 2), tls=True) as frigate:
            probe = self.probe(cam, frigate, ca_file=FIXTURES / "client.pem")
            self.assertIsNone(self.collect(probe))
            self.assertEqual(frigate.requests, [])

    def test_redirect_not_followed_or_treated_as_health(self):
        with LocalJson(camera()) as cam, LocalJson({}, tls=True, status=302, extra="Location: https://other/\r\n") as frigate:
            probe = self.probe(cam, frigate)
            self.assertIsNone(self.collect(probe))
            self.assertEqual(len(frigate.requests), 1)

    def test_duplicate_fields_and_oversized_framing_fail_closed(self):
        for body, extra in [(b'{"service":{},"service":{}}', ""),
                            (b'{}', "Content-Length: 999999\r\n"),
                            (b'{"value":NaN}', "")]:
            with self.subTest(body=body, extra=extra), LocalJson(camera()) as cam, \
                    LocalJson(body, tls=True, extra=extra) as frigate:
                probe = self.probe(cam, frigate)
                self.assertIsNone(self.collect(probe))
