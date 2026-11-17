"""Offline tests for the exact-bundle body age receipt."""

from datetime import timedelta
import io
import json
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

import build_body_clock_release as builder


ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "tools/fixtures/installed-oak-capture-20261111.py"


class BodyClockReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = BASE.read_bytes()
        cls.generated = builder.build(cls.base)
        cls.module = types.ModuleType("_test_body_clock_release")
        sys.modules[cls.module.__name__] = cls.module
        cls.addClassCleanup(sys.modules.pop, cls.module.__name__)
        exec(compile(cls.generated, "<body-clock-release>", "exec"), cls.module.__dict__)

    def setUp(self):
        m = self.module
        self.mono_ns = 10_000_000_000
        self.boot_delta_ns = 0
        self.sdk_offset_ns = 2_000_000_000
        self.correction = dict(m.DEPTH_CORRECTION, applied=True)
        self.profile = m.BodyClockProfile(
            schema="den-body-clock-profile-1", evidence_id="synthetic", device_id="unit",
            depthai_version="3.10.0", calibration_sha256=self.correction["calibration_sha256"],
            fps=30, clock_drift_ppm=100, receiver_drift_ppm=100,
            rgb={"before_stamp_s": .012, "after_stamp_s": .003},
            depth={"before_stamp_s": .015, "after_stamp_s": .004})
        self.state = m.BodyClockState(15, body_clock=self.profile, _point=self.point)
        self.bind()

    def point(self):
        before = self.mono_ns
        self.mono_ns += 1000
        return self.module.HostPoint(before, self.mono_ns + self.boot_delta_ns, 1_000_000_000, self.mono_ns)

    def sdk_now(self):
        return timedelta(microseconds=(self.mono_ns + self.sdk_offset_ns) // 1000)

    def sample(self, source_now):
        return self.real_sample_clock_pair(source_now, monotonic_ns=self.mono, wall_ns=lambda: 1_000_000_000)

    def mono(self):
        self.mono_ns += 1000
        return self.mono_ns

    def bind(self, **overrides):
        settings = {"sdk": "3.10.0", "device_id": "unit", "fps": 30, "correction": self.correction}
        self.state.bind_body_clock(**(settings | overrides))

    def publish(self, sequence, *, depth_seq=None, frame_delay_ns=30_000_000, rgb_exposure=.008, stereo_exposure=.009):
        self.mono_ns += 200_000_000
        rgb_stamp = self.mono_ns + self.sdk_offset_ns - frame_delay_ns
        depth_stamp = rgb_stamp - 9_000_000
        rgb = {"sequence": sequence, "host_monotonic_timestamp_seconds": rgb_stamp / 1e9,
               "exposure_duration_seconds": rgb_exposure}
        depth = {"sequence": sequence + 20 if depth_seq is None else depth_seq,
                 "host_monotonic_timestamp_seconds": depth_stamp / 1e9}
        mono = dict(depth, exposure_duration_seconds=stereo_exposure)
        meta = {"session": self.state.session, "frames": {"rgb": rgb, "depth": depth, "left": mono, "right": mono}}
        bundle = bytes([sequence]) * 24
        self.real_sample_clock_pair = self.module.sample_clock_pair
        with mock.patch.object(self.module, "sample_clock_pair", self.sample):
            self.state.publish_bundle(bundle, rgb, b"rgb", self.mono_ns / 1e9,
                                      metadata=meta, source_now=self.sdk_now)
        return bundle

    def test_build_preserves_pinned_source_and_camera_pipeline(self):
        self.assertEqual(builder.build(self.base), self.generated)
        self.assertNotEqual(self.base, self.generated)
        with self.assertRaises(ValueError):
            builder.build(self.base + b" ")
        self.assertIn(b"state.jpeg_response()", self.generated)
        self.assertIn(b"rgb_video.link(encoder.input)", self.generated)
        self.assertIn(b"headers['X-Den-Body-Capture-Age'] = age", self.generated)

    def test_requires_two_consistent_samples_and_exact_response_bundle(self):
        first = self.publish(1)
        self.assertIsNone(self.state.body_capture_age(first))
        second = self.publish(2)
        self.assertIsNone(self.state.body_capture_age(first))
        self.assertIsNone(self.state.body_capture_age(bytes(bytearray(second))))
        self.mono_ns += 20_000_000
        receipt = json.loads(self.state.body_capture_age(second))
        self.assertEqual(receipt["schema"], "den-body-pair-age-1")
        self.assertEqual(receipt["frame_key"], [self.state.session, 2, 22])
        self.assertEqual(receipt["evidence_id"], "synthetic")
        self.assertGreaterEqual(receipt["rgb_age_upper_s"], .062)
        self.assertGreaterEqual(receipt["depth_age_upper_s"], .074)
        self.assertGreaterEqual(receipt["separation_upper_s"], .027)
        self.assertLess(receipt["depth_age_upper_s"], .075)

    def test_authenticated_http_path_returns_the_pair_receipt_and_original_body(self):
        self.publish(1)
        bundle = self.publish(2)

        class CaptureServer:
            def __init__(self, _address, handler):
                self.handler = handler

            def serve_forever(self):
                pass

        with mock.patch.object(self.module, "ThreadingHTTPServer", CaptureServer):
            server = self.module.serve(self.state, "127.0.0.1", 8090, "test-token")

        def get(path, authorization):
            handler = server.handler.__new__(server.handler)
            handler.path = path
            handler.headers = {"Authorization": authorization}
            handler.connection = types.SimpleNamespace(settimeout=lambda _: None)
            handler.wfile = io.BytesIO()
            output = {}
            handler.send_response = lambda status: output.update(status=status)
            handler.send_error = handler.send_response
            handler.send_header = lambda name, value: output.update({name: value})
            handler.end_headers = lambda: None
            with mock.patch.object(self.state, "response", return_value=(200, "application/octet-stream", bundle)):
                handler.do_GET()
            return output, handler.wfile.getvalue()

        headers, data = get("/frame.npz", "Bearer test-token")
        receipt = json.loads(headers["X-Den-Body-Capture-Age"])
        self.assertEqual(receipt["frame_key"], [self.state.session, 2, 22])
        self.assertEqual(data, bundle)
        headers, _ = get("/calibration", "Bearer test-token")
        self.assertNotIn("X-Den-Body-Capture-Age", headers)
        headers, _ = get("/frame.npz", "")
        self.assertEqual(headers, {"status": 401})

    def test_clock_discontinuity_and_suspend_clear_age(self):
        self.publish(1)
        current = self.publish(2)
        self.assertIsNotNone(self.state.body_capture_age(current))
        self.boot_delta_ns += 5_000_000
        self.assertIsNone(self.state.body_capture_age(current))
        self.assertIsNone(self.state.body_capture_age(self.publish(3)))
        self.assertIsNone(self.state.body_capture_age(self.publish(4)))
        self.assertIsNotNone(self.state.body_capture_age(self.publish(5)))
        self.sdk_offset_ns += 30_000_000
        self.assertIsNone(self.state.body_capture_age(self.publish(6)))
        self.assertIsNotNone(self.state.body_capture_age(self.publish(7)))

    def test_requires_matching_profile_and_current_depth_sequence(self):
        self.bind(device_id="other")
        self.publish(1)
        self.assertIsNone(self.state.body_capture_age(self.publish(2)))
        self.bind()
        self.publish(3)
        self.assertIsNone(self.state.body_capture_age(self.publish(4, depth_seq=22)))
        current = self.publish(5)
        self.assertIsNotNone(self.state.body_capture_age(current))
        self.mono_ns += 1_000_000_000
        self.assertIsNone(self.state.body_capture_age(current))

    def test_capture_before_verified_no_suspend_interval_is_ineligible(self):
        self.publish(1)
        old = self.publish(2, frame_delay_ns=260_000_000)
        self.assertIsNone(self.state.body_capture_age(old))

    def test_exposure_metadata_can_disprove_commissioned_bounds(self):
        self.publish(1)
        self.assertIsNone(self.state.body_capture_age(self.publish(2, rgb_exposure=.016)))
        self.assertIsNone(self.state.body_capture_age(self.publish(3, rgb_exposure=None)))
        self.assertIsNone(self.state.body_capture_age(self.publish(4, stereo_exposure=.020)))
        self.assertIsNotNone(self.state.body_capture_age(self.publish(5, stereo_exposure=None)))

    def test_missing_commissioned_plane_bound_is_refused(self):
        from dataclasses import replace
        with self.assertRaises(ValueError):
            replace(self.profile, rgb={"before_stamp_s": 0, "after_stamp_s": 0})
        with self.assertRaises(ValueError):
            replace(self.profile, depth={"before_stamp_s": .01, "after_stamp_s": float("nan")})


if __name__ == "__main__":
    unittest.main()
