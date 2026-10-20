"""Exact-frame wire validation; no device or clock authority is simulated as live."""
import base64
import hashlib
import json
import unittest

from home_shared.protocol import camera_frame


class CausalProtocolTests(unittest.TestCase):
    jpeg = b"fixture image bytes; real decode is enforced by camera/gateway"

    def receipt(self, **changes):
        value = dict(schema=3, session="capture", sequence=7, timing_generation=2,
                     width=640, height=360, quality=85, observed_monotonic_seconds=5.1,
                     observed_at_unix_ns=1005100000000, uncertainty_seconds=.1 + .01 + .000001 + .2,
                     validation_id="exact-frame-receipt", exposure_duration_seconds=.01,
                     source_clock="depthai_device_monotonic", monotonic_clock_scope="python_monotonic_on_camera_host",
                     timestamp_semantics="causal_exact_frame_exposure_end", challenge_nonce="a" * 32,
                     challenge_device_ns=100000, frame_device_ns=200000,
                     host_send_monotonic_ns=5000000000, host_receive_monotonic_ns=5200000000,
                     timestamp_quantization_ns=1000, preview_sha256="b" * 64,
                     jpeg_sha256=hashlib.sha256(self.jpeg).hexdigest(),
                     host_boot_id="12345678-1234-1234-1234-123456789abc",
                     host_clock_validation_id="host-clock", host_clock_uncertainty_seconds=.2)
        value.update(changes)
        return value

    def parse(self, value, jpeg=None):
        return camera_frame(self.jpeg if jpeg is None else jpeg,
                            base64.urlsafe_b64encode(json.dumps(value).encode()).decode())

    def test_exact_frame_midpoint_error_and_identity_preserved(self):
        frame = self.parse(self.receipt())
        self.assertEqual(frame.frame_id, "c2-s7")
        self.assertEqual(frame.timing_generation, 2)
        self.assertEqual(frame.timing_evidence_version, 2)
        self.assertEqual(frame.camera_boot_id, self.receipt()["host_boot_id"])
        self.assertEqual(frame.host_clock_validation_id, "host-clock")
        self.assertEqual(frame.camera_host_uncertainty_seconds, .2)
        self.assertEqual(frame.observed_mono, 5.1)
        self.assertEqual(frame.observed_at, 1005.1)
        self.assertEqual(frame.jpeg, self.jpeg)
        self.assertAlmostEqual(frame.uncertainty_seconds, .310001)

    def test_jpeg_substitution_or_oversize_fails(self):
        with self.assertRaises(ValueError):
            self.parse(self.receipt(), b"other")
        for image in (b"", b"a" * (256 * 1024 + 1)):
            with self.subTest(size=len(image)), self.assertRaises(ValueError):
                self.parse(self.receipt(jpeg_sha256=hashlib.sha256(image).hexdigest()), image)

    def test_causal_order_and_derived_values_cannot_be_weakened(self):
        cases = ({"frame_device_ns": 101000}, {"frame_device_ns": 99999},
                 {"host_receive_monotonic_ns": 4999999999}, {"timestamp_quantization_ns": 0},
                 {"uncertainty_seconds": .3}, {"uncertainty_seconds": 1.01},
                 {"observed_monotonic_seconds": 5.2}, {"exposure_duration_seconds": 0},
                 {"host_clock_uncertainty_seconds": float("nan")}, {"sequence": True},
                 {"host_clock_uncertainty_seconds": 10**400},
                 {"frame_device_ns": 2**63}, {"schema": True},
                 {"host_boot_id": "unknown"}, {"source_clock": "depthai_host_monotonic"})
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.parse(self.receipt(**changes))

    def test_supported_timestamp_resolution_edges(self):
        for quantization in (1, 999, 1000, 1000000, 1000001):
            value = self.receipt(timestamp_quantization_ns=quantization,
                                 frame_device_ns=2000000,
                                 uncertainty_seconds=.1 + .01 + quantization / 1e9 + .2)
            with self.subTest(quantization=quantization):
                if 1000 <= quantization <= 1000000:
                    self.parse(value)
                else:
                    with self.assertRaises(ValueError):
                        self.parse(value)

    def test_missing_extra_and_duplicate_fields_fail(self):
        for key in self.receipt():
            value = self.receipt()
            del value[key]
            with self.subTest(missing=key), self.assertRaises(ValueError):
                self.parse(value)
        with self.assertRaises(ValueError):
            self.parse(self.receipt(extra=True))
        text = json.dumps(self.receipt())[:-1] + ', "sequence": 8}'
        with self.assertRaises(ValueError):
            camera_frame(self.jpeg, base64.urlsafe_b64encode(text.encode()).decode())


if __name__ == "__main__":
    unittest.main()
