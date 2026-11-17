"""Current-receipt and covariance fences for projecting the existing motion forecast."""
import copy
import json
import subprocess
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import forecast_spatial as F
import motion
from contracts import FrameRef, PlaneRef, TrackEvidence, TrackingSnapshot

P = ((0.04, 0.005, 0.01, -0.002), (0.005, 0.09, 0.003, 0.015),
     (0.01, 0.003, 0.16, 0.006), (-0.002, 0.015, 0.006, 0.25))


def fixture():
    rgb = PlaneRef(7, 20.0, 30.0, 640, 360)
    frame = FrameRef("run-1", 2, 7, "camera-session", "rgb", rgb, None, None, "calibration-sha",
                     "registered-quarter-turn", "scan-1", 100.0, 100.2, 1000.0)
    state = motion.MotionState(
        service_run_id="run-1", camera_epoch=2, frame_id=7, track_id="track-1", association_revision=3,
        transform_id=frame.transform_id, scan_revision="scan-1", depth_calibration_sha256="calibration-sha",
        observed_mono=100.0, available_mono=100.25, valid_until_mono=101.4,
        xy_m=(1.0, 2.0), velocity_mps=(1.0, 0.0), covariance=P,
        sample_count=3, history_span_s=0.4, accel_sigma_mps2=0.1,
        registration_error_m=0.05, timing_error_s=0.1, source_obs_ids=("observation-current",))
    track = TrackEvidence("track-1", "confirmed", state.xy_m, 0.3, "stereo", state.source_obs_ids,
                          "confident", 3, 99.0, MappingProxyType({}), motion=state)
    snapshot = TrackingSnapshot("run-1", 2, 55, frame, "fresh", None, "present", (track,))
    forecast = motion.forecast(state, 100.4, frame=frame, track_id=track.track_id, association_revision=3)
    assert forecast is not None
    return forecast, snapshot, track


def with_state(forecast, snapshot, track, state):
    track = replace(track, motion=state)
    return replace(forecast, state=state), replace(snapshot, tracks=(track,)), track


class SpatialForecastTest(unittest.TestCase):
    def test_projection_preserves_full_source_payload_and_has_no_authority(self):
        forecast, snapshot, track = fixture()
        packet, reason = F.qualify(forecast, snapshot, track, 100.5)
        self.assertIsNone(reason)
        self.assertEqual(packet["schema"], F.SCHEMA)
        self.assertEqual(packet["state"]["observed_mono"], snapshot.frame.capture_mono)
        self.assertEqual(packet["state"]["covariance"], forecast.state.covariance)
        self.assertEqual(packet["issued_mono"], forecast.issued_mono)
        self.assertEqual(packet["valid_until_mono"], forecast.state.valid_until_mono)
        self.assertEqual(packet["qualified_at_mono"], 100.5)
        self.assertAlmostEqual(packet["remaining_freshness_s"], 0.9)
        for original, projected in zip(forecast.samples, packet["samples"]):
            self.assertEqual(projected["at_mono"], original.at_mono)
            self.assertEqual(projected["xy_m"], original.xy_m)
            self.assertEqual(projected["covariance_m2"], original.covariance_m2)
            self.assertEqual(projected["sigma_m"], original.sigma_m)
        self.assertTrue(packet["shadow_only"])
        self.assertTrue(packet["virtual_only"])
        for name in ("physical", "walkable", "beam", "delay", "aim", "occupancy"):
            self.assertIs(packet[name + "_qualified"], False)
        json.dumps(packet, allow_nan=False)

    def test_every_binding_and_current_snapshot_claim_must_match(self):
        for field, value in (("service_run_id", "old-run"), ("camera_epoch", 1), ("frame_id", 6),
                             ("transform_id", "old-transform"), ("scan_revision", "old-scan"),
                             ("depth_calibration_sha256", "old-calibration"), ("scan_revision", None)):
            with self.subTest(field=field):
                forecast, snapshot, track = fixture()
                changed = replace(snapshot, frame=replace(snapshot.frame, **{field: value}))
                self.assertEqual(F.qualify(forecast, changed, track, 100.5), (None, "binding_mismatch"))
        forecast, snapshot, track = fixture()
        for changed in (replace(snapshot, service_run_id="other"), replace(snapshot, camera_epoch=3),
                        replace(snapshot, tracks=()), replace(snapshot, tracks=(track, track))):
            self.assertEqual(F.qualify(forecast, changed, track, 100.5), (None, "binding_mismatch"))
        changed = replace(track, association_revision=4)
        self.assertEqual(F.qualify(forecast, replace(snapshot, tracks=(changed,)), changed, 100.5),
                         (None, "binding_mismatch"))

    def test_current_observations_and_current_motion_are_required(self):
        forecast, snapshot, track = fixture()
        for ids in ((), ("older-observation",), ("observation-current", "extra"),
                    ("observation-current", "observation-current")):
            changed = replace(track, current_obs_ids=ids)
            self.assertEqual(F.qualify(forecast, replace(snapshot, tracks=(changed,)), changed, 100.5),
                             (None, "observations_mismatch"))
        changed = replace(track, motion=None)
        self.assertEqual(F.qualify(forecast, replace(snapshot, tracks=(changed,)), changed, 100.5),
                         (None, "motion_unavailable"))

    def test_crossing_and_coasting_abstain_even_before_revision_changes(self):
        forecast, snapshot, track = fixture()
        for fields in ({"association_status": "ambiguous"}, {"state": "coasting"}, {"state": "tentative"}):
            changed = replace(track, **fields)
            self.assertEqual(F.qualify(forecast, replace(snapshot, tracks=(changed,)), changed, 100.5),
                             (None, "track_ineligible"))
        for status in ("stale", "degraded", "starting"):
            self.assertEqual(F.qualify(forecast, replace(snapshot, status=status), track, 100.5),
                             (None, "snapshot_unavailable"))

    def test_full_cross_covariance_is_checked_even_when_every_two_axis_pair_is_psd(self):
        forecast, snapshot, track = fixture()
        # Each 2x2 principal minor is nonnegative. The complete [position, velocity] matrix is indefinite.
        cross = ((1.0, 0.0, 1.0, 1.0), (0.0, 1.0, 1.0, -1.0),
                 (1.0, 1.0, 1.0, 0.0), (1.0, -1.0, 0.0, 1.0))
        for covariance in (cross, None):
            state = copy.copy(forecast.state)
            object.__setattr__(state, "covariance", covariance)
            changed = with_state(forecast, snapshot, track, state)
            self.assertEqual(F.qualify(*changed, 100.5), (None, "state_covariance_invalid"))

    def test_capture_expiry_is_never_renewed_and_timing_order_is_causal(self):
        forecast, snapshot, track = fixture()
        expiry = forecast.valid_until_mono
        self.assertIsNotNone(F.qualify(forecast, snapshot, track, expiry - 1e-9)[0])
        for now in (expiry, expiry + 0.001, 100.3, float("nan"), True):
            self.assertEqual(F.qualify(forecast, snapshot, track, now), (None, "timing_invalid"))
        self.assertEqual(F.qualify(replace(forecast, valid_until_mono=expiry + 0.1), snapshot, track, 100.5),
                         (None, "timing_invalid"))
        self.assertEqual(F.qualify(replace(forecast, issued_mono=100.1), snapshot, track, 100.5),
                         (None, "timing_invalid"))
        changed_frame = replace(snapshot.frame, capture_mono=99.9)
        self.assertEqual(F.qualify(forecast, replace(snapshot, frame=changed_frame), track, 100.5),
                         (None, "timing_invalid"))
        state = replace(forecast.state, valid_until_mono=101.5)
        changed = with_state(replace(forecast, valid_until_mono=101.5), snapshot, track, state)
        self.assertEqual(F.qualify(*changed, 100.5), (None, "timing_invalid"), "timing bound already consumes capture lease")

    def test_samples_keep_unique_order_fixed_issued_horizons_and_consistent_uncertainty(self):
        forecast, snapshot, track = fixture()
        first = forecast.samples[0]
        for samples in ((), (first, first), tuple(reversed(forecast.samples)),
                        (replace(first, horizon_s=2.1, at_mono=forecast.issued_mono + 2.1),),
                        (replace(first, at_mono=first.at_mono + 0.1),),
                        (replace(first, xy_m=(float("nan"), 0)),)):
            self.assertIsNone(F.qualify(replace(forecast, samples=samples), snapshot, track, 100.5)[0])
        for changed in (replace(first, covariance_m2=((1, 2), (2, 1))),
                        replace(first, covariance_m2=None), replace(first, sigma_m=first.sigma_m + 0.1)):
            self.assertEqual(F.qualify(replace(forecast, samples=(changed,)), snapshot, track, 100.5),
                             (None, "sample_covariance_invalid"))

    def test_existing_room_coordinates_are_not_rotated_and_facing_never_becomes_velocity(self):
        forecast, snapshot, track = fixture()
        # Registration was a quarter turn before the tracker produced these room-floor coordinates.
        packet, reason = F.qualify(forecast, snapshot, track, 100.5)
        self.assertIsNone(reason)
        self.assertAlmostEqual(packet["samples"][0]["xy_m"][0], 1.6)
        self.assertEqual(packet["samples"][0]["xy_m"][1], 2.0)
        from contracts import BodyFacing, Orientation3D
        north = Orientation3D((0, 1, 0), (0, 0, 1), 0, 0, 25, 100, (0, 1, 2, 3, 4), "stereo_face_axis")
        facing = replace(track, body_facing=BodyFacing(0, 20, 100, 1), head_orientation=north)
        other, reason = F.qualify(forecast, replace(snapshot, tracks=(facing,)), facing, 100.5)
        self.assertIsNone(reason)
        self.assertEqual(packet["samples"], other["samples"], "north-facing person still travels east")

    def test_arrival_age_is_already_in_samples_and_later_qualification_does_not_add_it_again(self):
        forecast, snapshot, track = fixture()
        early, _ = F.qualify(forecast, snapshot, track, 100.4)
        later, _ = F.qualify(forecast, snapshot, track, 100.8)
        self.assertEqual(early["samples"], later["samples"])
        self.assertEqual(early["samples"][0]["at_mono"], 100.4 + 0.2)
        self.assertAlmostEqual(early["samples"][0]["xy_m"][0], 1.0 + 0.4 + 0.2)
        self.assertEqual(early["valid_until_mono"], later["valid_until_mono"])
        self.assertAlmostEqual(early["remaining_freshness_s"] - later["remaining_freshness_s"], 0.4)

    def test_minimum_history_and_missing_bindings_abstain(self):
        forecast, snapshot, track = fixture()
        state = replace(forecast.state, sample_count=2, history_span_s=0.2)
        self.assertEqual(F.qualify(*with_state(forecast, snapshot, track, state), 100.5),
                         (None, "history_unqualified"))
        self.assertEqual(F.qualify(None, snapshot, track, 100.5), (None, "forecast_unavailable"))
        self.assertEqual(F.qualify(forecast, replace(snapshot, frame=None), track, 100.5),
                         (None, "snapshot_unavailable"))

    def test_malformed_large_sample_and_observation_counts_are_refused(self):
        forecast, snapshot, track = fixture()
        samples = tuple(replace(forecast.samples[0], horizon_s=h, at_mono=forecast.issued_mono + h)
                        for h in (0.1, 0.2, 0.3, 0.4, 0.5))
        self.assertEqual(F.qualify(replace(forecast, samples=samples), snapshot, track, 100.5),
                         (None, "samples_invalid"))
        for count, accepted in ((64, True), (65, False)):
            ids = tuple("obs-%d" % index for index in range(count))
            state = replace(forecast.state, source_obs_ids=ids)
            changed = with_state(forecast, snapshot, replace(track, current_obs_ids=ids), state)
            result, reason = F.qualify(*changed, 100.5)
            self.assertEqual(result is not None, accepted)
            if not accepted:
                self.assertEqual(reason, "observations_mismatch")
        ids = ("x" * 129,)
        state = replace(forecast.state, source_obs_ids=ids)
        changed = with_state(forecast, snapshot, replace(track, current_obs_ids=ids), state)
        self.assertEqual(F.qualify(*changed, 100.5), (None, "observations_mismatch"))

    def test_long_identifiers_and_incomplete_old_frame_records_do_not_gain_binding(self):
        forecast, snapshot, track = fixture()
        for changes in ({"camera_session": None}, {"camera_session": "x" * 129}, {"rgb": None}):
            with self.subTest(changes=changes):
                incomplete = replace(snapshot, frame=replace(snapshot.frame, **changes))
                self.assertEqual(F.qualify(forecast, incomplete, track, 100.5), (None, "binding_mismatch"))
        state = replace(forecast.state, service_run_id="x" * 129)
        changed = with_state(forecast, snapshot, track, state)
        current = replace(changed[1], service_run_id=state.service_run_id,
                          frame=replace(snapshot.frame, service_run_id=state.service_run_id))
        self.assertEqual(F.qualify(changed[0], current, changed[2], 100.5), (None, "binding_mismatch"))

    def test_projection_is_standard_library_only(self):
        script = """
import importlib.abc, sys
sys.path.insert(0, sys.argv[1])
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname.split('.')[0] in ('numpy', 'cv2', 'torch', 'onnxruntime'):
            raise AssertionError('unexpected inference dependency')
sys.meta_path.insert(0, Block())
import forecast_spatial
"""
        result = subprocess.run([sys.executable, "-B", "-c", script, str(HERE)], capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))


if __name__ == "__main__":
    unittest.main()
