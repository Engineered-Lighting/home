"""Motion evidence and causal replay checks; no model, camera, host or network."""
import dataclasses
import json
import math
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import motion
from contracts import FrameRef, PlaneRef, TrackEvidence, TrackingSnapshot


P = ((0.04, 0.005, 0.003, 0.0),
     (0.005, 0.09, 0.0, 0.004),
     (0.003, 0.0, 0.16, 0.01),
     (0.0, 0.004, 0.01, 0.25))


def frame(at=100.0, frame_id=10, **changes):
    value = FrameRef("run", 2, frame_id, "camera", "rgb", PlaneRef(frame_id, 1, at, 640, 360), None, None,
                     "c" * 64, "transform", "scan", at, at + 0.05, 1_800_000_000 + at)
    return dataclasses.replace(value, **changes)


def state(**changes):
    values = dict(service_run_id="run", camera_epoch=2, frame_id=10, track_id="person", association_revision=3,
                  transform_id="transform", scan_revision="scan", depth_calibration_sha256="c" * 64,
                  observed_mono=100.0, available_mono=100.1, valid_until_mono=101.4,
                  xy_m=(1.0, 2.0), velocity_mps=(1.0, 0.0), covariance=P,
                  sample_count=3, history_span_s=0.5, accel_sigma_mps2=0.6,
                  registration_error_m=0.1, timing_error_s=0.1, source_obs_ids=("obs10",))
    return motion.MotionState(**(values | changes))


def receive(receipts, at, frame_id, *, velocity=(1.0, 0.0), raw=None, **changes):
    raw = raw or SimpleNamespace(x=[1.0, 2.0, *velocity], P=[list(row) for row in P], last_seen=at)
    values = dict(frame=frame(at, frame_id), track_id="person", association_revision=3,
                  association_status="confident", state="confirmed", current_obs_ids=(f"obs{frame_id}",),
                  available_mono=at + 0.1, accel_sigma_mps2=0.6, registration_error_m=0.1, timing_error_s=0.05)
    return receipts.update(raw, **(values | changes))


class Validation(unittest.TestCase):
    def test_full_filter_state_is_an_immutable_copy(self):
        covariance = [list(row) for row in P]
        velocity = [1, 2]
        value = state(covariance=covariance, velocity_mps=velocity)
        covariance[0][0] = 999
        velocity[0] = 999
        self.assertEqual(value.covariance, P)
        self.assertEqual(value.velocity_mps, (1, 2))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            value.sample_count = 0

    def test_missing_nonfinite_asymmetric_and_indefinite_covariance_rejected(self):
        indefinite = [[1.0 if i == j else 0.0 for j in range(4)] for i in range(4)]
        indefinite[0][1] = indefinite[1][0] = 2
        asymmetric = [list(row) for row in P]
        asymmetric[0][1] += 0.01
        nan = [list(row) for row in P]
        nan[0][0] = math.nan
        for covariance in (None, (), [[1, 0], [0, 1]], indefinite, asymmetric, nan):
            with self.subTest(covariance=covariance), self.assertRaises(ValueError):
                state(covariance=covariance)

    def test_semidefinite_zero_axes_are_supported_without_inventing_noise(self):
        zero = tuple((0, 0, 0, 0) for _ in range(4))
        value = state(covariance=zero, velocity_mps=(0, 0), accel_sigma_mps2=0,
                      registration_error_m=0, timing_error_s=0)
        self.assertEqual(motion.forecast(value, 100.2).samples[0].covariance_m2, ((0, 0), (0, 0)))
        invalid = [list(row) for row in zero]
        invalid[0][1] = invalid[1][0] = 0.01
        with self.assertRaises(ValueError):
            state(covariance=invalid)

    def test_psd_validation_preserves_small_correlated_variances(self):
        covariance = ((1e-12, 1e-6, 0, 0), (1e-6, 1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1))
        self.assertEqual(state(covariance=covariance).covariance, covariance)

    def test_invalid_bindings_times_and_counter_types_rejected(self):
        for changes in (dict(service_run_id=""), dict(scan_revision=None), dict(depth_calibration_sha256=None),
                        dict(camera_epoch=True), dict(association_revision=-1), dict(frame_id=1.5),
                        dict(observed_mono=100.2), dict(available_mono=101.5), dict(valid_until_mono=102.0),
                        dict(velocity_mps=(math.inf, 0)), dict(timing_error_s=-1), dict(sample_count=1),
                        dict(source_obs_ids=()), dict(basis="language_model")):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                state(**changes)


class Forecasting(unittest.TestCase):
    def test_horizons_include_capture_to_available_age_and_do_not_renew_expiry(self):
        value = state()
        result = motion.forecast(value, 100.2, frame=frame(), track_id="person", association_revision=3)
        self.assertEqual(tuple(s.horizon_s for s in result.samples), (0.2, 0.5, 1.0, 2.0))
        self.assertAlmostEqual(result.samples[0].xy_m[0], 1.4)
        self.assertAlmostEqual(result.samples[-1].xy_m[0], 3.2)
        self.assertEqual(result.valid_until_mono, value.valid_until_mono)
        self.assertEqual(motion.forecast(value, 101.0).valid_until_mono, value.valid_until_mono)
        self.assertIsNone(motion.forecast(value, value.valid_until_mono))

    def test_full_cross_covariance_survives_and_error_envelope_only_adds(self):
        value = state(accel_sigma_mps2=0, registration_error_m=0, timing_error_s=0)
        sample = motion.forecast(value, 100.1, horizons_s=(0.5,)).samples[0]
        # dt=.6: known analytical projection of the correlated input fixture.
        self.assertAlmostEqual(sample.covariance_m2[0][0], 0.1012)
        self.assertAlmostEqual(sample.covariance_m2[0][1], 0.0086)
        self.assertAlmostEqual(sample.covariance_m2[1][1], 0.1848)
        for changes in (dict(registration_error_m=0.2), dict(timing_error_s=0.2), dict(accel_sigma_mps2=1.0)):
            larger = motion.forecast(dataclasses.replace(value, **changes), 100.1, horizons_s=(0.5,)).samples[0]
            self.assertGreater(larger.covariance_m2[0][0], sample.covariance_m2[0][0])
            self.assertGreater(larger.covariance_m2[1][1], sample.covariance_m2[1][1])
            self.assertGreater(larger.sigma_m, sample.sigma_m)

    def test_propagated_envelopes_remain_finite_psd_and_grow_for_this_cv_fixture(self):
        samples = motion.forecast(state(), 100.2).samples
        for sample in samples:
            a, b, d = sample.covariance_m2[0][0], sample.covariance_m2[0][1], sample.covariance_m2[1][1]
            self.assertTrue(all(math.isfinite(x) for row in sample.covariance_m2 for x in row))
            self.assertGreaterEqual(a * d - b * b, 0)
            self.assertGreaterEqual(sample.sigma_m, math.sqrt(max(a, d)))
        self.assertEqual(sorted(s.sigma_m for s in samples), [s.sigma_m for s in samples])

    def test_causal_cutoff_cannot_use_future_observations_or_receipts(self):
        value = state()
        for now, cutoff in ((99.0, None), (100.05, None), (100.2, 100.05), (100.2, 100.3), (100.2, math.nan)):
            self.assertIsNone(motion.forecast(value, now, cutoff_mono=cutoff))
        self.assertIsNotNone(motion.forecast(value, 100.2, cutoff_mono=100.1))

    def test_epoch_person_association_calibration_and_frame_fences(self):
        value = state()
        for changes in (dict(service_run_id="new"), dict(camera_epoch=3), dict(frame_id=11),
                        dict(transform_id="new"), dict(scan_revision="new"), dict(depth_calibration_sha256="d" * 64)):
            self.assertIsNone(motion.forecast(value, 100.2, frame=frame(**changes)))
        self.assertIsNone(motion.forecast(value, 100.2, track_id="other"))
        self.assertIsNone(motion.forecast(value, 100.2, association_revision=4))

    def test_bad_or_unbounded_forecast_requests_abstain(self):
        for horizons in ((), (-0.1,), (2.01,), (math.inf,), (True,), (1.0, 0.5), (0.5, 0.5)):
            self.assertIsNone(motion.forecast(state(), 100.2, horizons_s=horizons))
        self.assertIsNone(motion.forecast(None, 100.2))
        self.assertIsNone(motion.forecast(state(sample_count=2), 100.2))
        self.assertIsNone(motion.forecast(state(history_span_s=0.399), 100.2))


class ObservationReceipts(unittest.TestCase):
    def test_three_distinct_observations_must_span_four_tenths(self):
        receipts = motion.Receipts()
        first = receive(receipts, 100, 1)
        second = receive(receipts, 100.1, 2)
        third = receive(receipts, 100.2, 3)
        fourth = receive(receipts, 100.4, 4)
        self.assertEqual([x.sample_count for x in (first, second, third, fourth)], [1, 2, 3, 4])
        self.assertFalse(third.lead_qualified)
        self.assertTrue(fourth.lead_qualified)

    def test_latest_tracker_stop_and_turn_replace_previous_velocity(self):
        receipts = motion.Receipts()
        receive(receipts, 100, 1)
        receive(receipts, 100.2, 2)
        stopped = receive(receipts, 100.4, 3, velocity=(0, 0))
        self.assertEqual(motion.forecast(stopped, 100.5).samples[-1].xy_m, stopped.xy_m)
        turned = receive(receipts, 100.6, 4, velocity=(0, -1))
        result = motion.forecast(turned, 100.7).samples[-1]
        self.assertEqual(result.xy_m[0], turned.xy_m[0])
        self.assertLess(result.xy_m[1], turned.xy_m[1])

    def test_ambiguity_and_coasting_break_history_even_before_revision_changes(self):
        for invalid in (dict(association_status="ambiguous"), dict(state="coasting"), dict(current_obs_ids=()),
                        dict(timing_error_s=None)):
            receipts = motion.Receipts()
            receive(receipts, 100, 1)
            receive(receipts, 100.2, 2)
            self.assertIsNone(receive(receipts, 100.4, 3, **invalid))
            reset = receive(receipts, 100.6, 4)
            self.assertEqual(reset.sample_count, 1)

    def test_new_epoch_run_association_and_registration_start_new_history(self):
        for changes in (dict(camera_epoch=3), dict(service_run_id="new"), dict(transform_id="new"),
                        dict(scan_revision="new"), dict(depth_calibration_sha256="d" * 64)):
            receipts = motion.Receipts()
            receive(receipts, 100, 1)
            receive(receipts, 100.2, 2)
            reset = receive(receipts, 100.4, 3, frame=frame(100.4, 3, **changes))
            self.assertEqual(reset.sample_count, 1)
        receipts = motion.Receipts()
        receive(receipts, 100, 1)
        receive(receipts, 100.2, 2)
        self.assertEqual(receive(receipts, 100.4, 3, association_revision=4).sample_count, 1)

    def test_out_of_order_frames_never_publish_and_duplicates_do_not_count(self):
        receipts = motion.Receipts()
        receive(receipts, 100, 1)
        receive(receipts, 100.2, 2)
        self.assertIsNone(receive(receipts, 100.1, 3))
        self.assertIsNone(receive(receipts, 100.3, 1))
        self.assertIsNone(receive(receipts, 100.4, 3, current_obs_ids=("obs2",)))
        self.assertEqual(receive(receipts, 100.6, 4).sample_count, 1)

    def test_missing_private_filter_covariance_or_stale_measurement_is_unavailable(self):
        for raw in (SimpleNamespace(x=[1, 2, 1, 0], last_seen=100),
                    SimpleNamespace(x=[1, 2, 1, 0], P=P, last_seen=99),
                    SimpleNamespace(x=[1, 2, math.nan, 0], P=P, last_seen=100)):
            self.assertIsNone(receive(motion.Receipts(), 100, 1, raw=raw))

    def test_long_gap_restarts_and_receipt_storage_is_bounded(self):
        receipts = motion.Receipts()
        receive(receipts, 100, 1)
        self.assertEqual(receive(receipts, 102, 2).sample_count, 1)
        for i in range(200):
            receive(receipts, 103, 1, track_id=f"person{i}")
        self.assertEqual(len(receipts.history), 128)


class SourceTiming(unittest.TestCase):
    @staticmethod
    def frame():
        return frame(depth=PlaneRef(20, 1, 100, 640, 360))

    @staticmethod
    def bundle():
        # Different receipt/tracker clock origins. Only elapsed receipt time is bridged.
        return SimpleNamespace(session="camera", rgb_seq=10, arrival_mono=100.1,
                               request_mono=1099.9, receipt_arrival_mono=1100.1,
                               capture_age_receipt={"schema": "den-body-pair-age-1", "frame_key": ["camera", 10, 20],
                                                    "evidence_id": "age10", "rgb_age_upper_s": 0.3,
                                                    "depth_age_upper_s": 0.31, "separation_upper_s": 0.02})

    def test_attested_interval_covers_clock_bias_without_adding_transit_twice(self):
        meta = dict(session="camera", rgb_seq=10, depth_seq=20)
        self.assertAlmostEqual(motion.timing_bound(self.frame(), self.bundle(), meta), 0.41)
        # An arbitrarily different absolute receipt clock origin changes no elapsed age.
        bundle = self.bundle()
        bundle.request_mono += 10000
        bundle.receipt_arrival_mono += 10000
        self.assertAlmostEqual(motion.timing_bound(self.frame(), bundle, meta), 0.41)

    def test_missing_or_wrong_exact_source_receipt_abstains(self):
        meta = dict(session="camera", rgb_seq=10, depth_seq=20)
        bundle = self.bundle()
        bundle.capture_age_receipt["frame_key"][-1] = 21
        self.assertIsNone(motion.timing_bound(self.frame(), bundle, meta))
        bundle.capture_age_receipt = None
        self.assertIsNone(motion.timing_bound(self.frame(), bundle, meta))

    def test_receipt_must_also_name_the_published_frame_planes(self):
        meta = dict(session="camera", rgb_seq=10, depth_seq=20)
        for changes in (dict(camera_session="other"), dict(rgb=PlaneRef(11, 1, 100, 640, 360)),
                        dict(depth=PlaneRef(21, 1, 100, 640, 360)), dict(depth=None)):
            self.assertIsNone(motion.timing_bound(dataclasses.replace(self.frame(), **changes), self.bundle(), meta))


class LosslessProjection(unittest.TestCase):
    def test_optional_motion_survives_json_replay_with_full_precision_and_lineage(self):
        import person_state
        value = state(covariance=tuple(tuple(x / 100 for x in row) for row in P))
        track = TrackEvidence("person", "confirmed", value.xy_m, 0.1, "stereo", ("obs10",), "confident", 3, 99,
                              {}, motion=value)
        snapshot = TrackingSnapshot("run", 2, 1, frame(), "fresh", None, "present", (track,))
        record = json.loads(json.dumps(person_state.serialise(snapshot), allow_nan=False))
        restored = motion.MotionState(**record["tracks"][0]["motion"])
        self.assertEqual(restored, value)
        self.assertEqual(motion.forecast(restored, 100.2, frame=record["frame"]), motion.forecast(value, 100.2))
        changed = record["frame"] | {"camera_epoch": 3}
        self.assertIsNone(motion.forecast(restored, 100.2, frame=changed))
        self.assertNotIn("motion", person_state.track_record(dataclasses.replace(track, motion=None)))


if __name__ == "__main__":
    unittest.main()
