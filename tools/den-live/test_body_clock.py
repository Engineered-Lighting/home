import http
import math
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from body_clock import AGE_SCHEMA, capture_bounds, lease_clock
from source import AllowedCamera, CameraSource, Mailbox


class CaptureClockTests(unittest.TestCase):
    def setUp(self):
        self.key = ["camera-run", 1, 17]
        self.meta = dict(session="camera-run", rgb_seq=1, depth_seq=17)
        self.receipt = dict(schema=AGE_SCHEMA, evidence_id="synthetic", frame_key=self.key.copy(), rgb_age_upper_s=0.05,
                            depth_age_upper_s=0.09, separation_upper_s=0.04)

    def bounds(self, **changes):
        bundle = SimpleNamespace(**{"session": "camera-run", "rgb_seq": 1,
                                    "request_mono": 100.0, "receipt_arrival_mono": 100.6,
                                    "capture_age_receipt": self.receipt, **changes})
        return capture_bounds(bundle, self.meta, frame_key=self.key)

    def test_uses_get_start_and_oldest_possible_exposure(self):
        bounds = self.bounds()
        self.assertEqual(bounds.earliest_mono, math.nextafter(100.0 - 0.09, -math.inf))
        self.assertEqual(bounds.separation_s, 0.04)
        self.assertEqual(bounds.evidence_id, "synthetic")
        self.receipt["rgb_age_upper_s"] = 0.11
        self.assertEqual(self.bounds(receipt_arrival_mono=101.0).earliest_mono,
                         math.nextafter(100.0 - 0.11, -math.inf))

    def test_stale_source_age_is_not_refreshed_by_the_get(self):
        self.receipt["depth_age_upper_s"] = 1.5
        self.assertLess(self.bounds().earliest_mono + 1.0, 100.0)

    def test_absent_proof_and_inconsistent_brackets_refuse(self):
        self.assertIsNone(self.bounds(capture_age_receipt=None))
        self.assertIsNone(self.bounds(request_mono=None))
        self.assertIsNone(self.bounds(request_mono=101.0))
        self.assertIsNone(self.bounds(receipt_arrival_mono=math.nan))
        self.assertIsNone(self.bounds(receipt_arrival_mono=None, arrival_mono=100.6))
        self.assertIsNone(self.bounds(request_mono=True))

    def test_receipt_must_name_exact_rgb_depth_and_camera_run(self):
        for key in (["other-run", 1, 17], ["camera-run", 2, 17], ["camera-run", 1, 18],
                    ["camera-run", True, 17]):
            with self.subTest(key=key):
                self.receipt["frame_key"] = key
                self.assertIsNone(self.bounds())
        self.receipt["frame_key"] = self.key
        self.assertIsNone(self.bounds(session="new-camera-run"))
        self.meta["depth_seq"] += 1
        self.assertIsNone(self.bounds())

    def test_all_three_bounds_required_and_nonnegative(self):
        for field in ("rgb_age_upper_s", "depth_age_upper_s", "separation_upper_s"):
            old = self.receipt.pop(field)
            self.assertIsNone(self.bounds())
            for bad in (-1, math.inf, math.nan, False, "0.02", 10 ** 400):
                with self.subTest(field=field, bad=bad):
                    self.receipt[field] = bad
                    self.assertIsNone(self.bounds())
            self.receipt[field] = old
        self.receipt["schema"] = "bundle_packaging_time_not_sensor_exposure"
        self.assertIsNone(self.bounds())

    def test_out_of_range_subtraction_refuses_without_throwing(self):
        self.receipt["rgb_age_upper_s"] = 10 ** 308
        self.assertIsNone(self.bounds(request_mono=-(10 ** 308)))

    def test_requires_source_evidence_identity(self):
        for bad in ("", None, 8, "x" * 129):
            self.receipt["evidence_id"] = bad
            self.assertIsNone(self.bounds())

    def test_lease_uses_suspend_inclusive_clock_when_available(self):
        with patch("body_clock.time.CLOCK_BOOTTIME", 7, create=True), \
                patch("body_clock.time.clock_gettime", return_value=109, create=True) as read:
            self.assertEqual(lease_clock(), 109)
            read.assert_called_once_with(7)

    def test_camera_request_bracket_uses_body_clock_and_keeps_marker_clock(self):
        class BundleError(Exception):
            pass

        packet = {**self.meta, "t_host": 12.0}
        take = SimpleNamespace(clock=lambda: 50.0, http=http,
                               check=SimpleNamespace(BundleError=BundleError, read_bundle=lambda _: (packet, None)))
        camera = AllowedCamera(SimpleNamespace(host="fixture", port=1234))
        mailbox = Mailbox()
        source = CameraSource(take, camera, mailbox)

        def request():
            source.halt.set()
            return 200, b"bundle", self.receipt

        with patch.object(camera, "get_bundle", request), patch("source.lease_clock", side_effect=(100.0, 100.6)):
            source.run()
        bundle = mailbox.take(0)
        self.assertEqual(bundle.arrival_mono, 50.0)
        self.assertEqual(bundle.receipt_arrival_mono, 100.6)
        self.assertEqual(bundle.request_mono, 100.0)
        self.assertEqual(capture_bounds(bundle, self.meta, frame_key=self.key).earliest_mono,
                         math.nextafter(100.0 - .09, -math.inf))
