"""Pinned tracker integration for spatial forecasts, using generated floor observations.

These tests exercise state copying, causal binding and invalidation. The stop and
turn fixtures deliberately retain the Kalman filter's inertia. They establish no
physical trajectory accuracy or calibrated probability for a forecast envelope.
"""
import dataclasses
import math
import unittest

import fixture_rgb
import forecast_spatial
import motion
from contracts import FrameRef, Orientation3D, PlaneRef, TrackEvidence, TrackingSnapshot


class Replay:
    def __init__(self, tools, params):
        self.params = params
        self.core = tools.records.tracker_core.TrackerCore(params)
        self.receipts = motion.Receipts()
        self.number = 0

    def step(self, positions, *, heading=(0.0, 5.0)):
        self.number += 1
        at = 100.0 + self.number * 0.2
        frame = FrameRef("generated-run", 1, self.number, "generated-camera", "rgb",
                         PlaneRef(self.number, at, at, 640, 360), None,
                         PlaneRef(self.number, at, at, 640, 400), "c" * 64, "registered-room@1", "scan-1",
                         at, at + 0.01, 1_800_000_000.0 + at)
        detections = [{"id": "obs-%d-%s" % (self.number, name), "xy": list(xy),
                       "R": [[0.0025, 0.0003], [0.0003, 0.004]],
                       "obs_ids": ["obs-%d-%s" % (self.number, name)], "heading": heading}
                      for name, xy in positions.items()]
        raw, tracks = {}, []
        available = at + 0.03
        for value, status in self.core.step(at, detections):
            if status == "ended":
                continue
            name = "e1-trk-%d" % value.id
            association = "ambiguous" if value.ambiguous else "confident"
            observations = tuple(sorted(value.obs_ids))
            # Keep the revision constant so one unsafe frame itself must interrupt
            # qualification, even before Perceiver's committed revision changes.
            state = self.receipts.update(
                value, frame=frame, track_id=name, association_revision=1,
                association_status=association, state=status, current_obs_ids=observations,
                available_mono=available, accel_sigma_mps2=self.params["accel_sigma"],
                registration_error_m=0.05, timing_error_s=0.02)
            track = TrackEvidence(name, status, tuple(value.x[:2]), math.sqrt(max(value.P[0][0], value.P[1][1])),
                                  "stereo", observations, association, 1, value.since_ts, {}, motion=state)
            raw[name] = value
            tracks.append(track)
        snapshot = TrackingSnapshot(frame.service_run_id, frame.camera_epoch, self.number, frame,
                                    "fresh", None, "present" if positions else "unknown", tuple(tracks))
        return snapshot, raw, available

    def walking(self, count=20):
        for index in range(count):
            current = self.step({"a": (index * 0.12, 1.0)})
        return current


def forecast(snapshot, track, now):
    return motion.forecast(track.motion, now, frame=snapshot.frame, track_id=track.track_id,
                           association_revision=track.association_revision)


class SpatialTracking(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scene = fixture_rgb.Scene()
        cls.addClassCleanup(cls.scene.close)
        cls.params = cls.scene.perceiver().track_method.tracker

    def replay(self):
        return Replay(self.scene.tools, self.params)

    def test_actual_filter_state_and_full_cross_covariance_survive_projection(self):
        replay = self.replay()
        snapshot, raw, now = replay.walking()
        track, = snapshot.tracks
        value = raw[track.track_id]
        state = track.motion
        self.assertTrue(state.lead_qualified)
        self.assertEqual(state.xy_m, tuple(value.x[:2]))
        self.assertEqual(state.velocity_mps, tuple(value.x[2:]))
        self.assertEqual(state.covariance, tuple(tuple(row) for row in value.P))
        self.assertEqual(state.source_obs_ids, tuple(value.obs_ids))
        self.assertTrue(any(state.covariance[i][j] != 0 for i in range(2) for j in range(2, 4)))

        # Isolate F P F^T algebra using actual filter P, without making an
        # assertion about physical error terms or their pending qualification.
        algebra = dataclasses.replace(state, accel_sigma_mps2=0.0, registration_error_m=0.0, timing_error_s=0.0)
        sample = motion.forecast(algebra, now, horizons_s=(0.5,)).samples[0]
        dt = sample.at_mono - state.observed_mono
        for i in range(2):
            self.assertAlmostEqual(sample.xy_m[i], state.xy_m[i] + dt * state.velocity_mps[i])
            for j in range(2):
                expected = (state.covariance[i][j]
                            + dt * (state.covariance[i + 2][j] + state.covariance[i][j + 2])
                            + dt ** 2 * state.covariance[i + 2][j + 2])
                self.assertAlmostEqual(sample.covariance_m2[i][j], expected)

        packet, reason = forecast_spatial.qualify(forecast(snapshot, track, now), snapshot, track, now)
        self.assertIsNone(reason)
        self.assertEqual(packet["state"]["covariance"], state.covariance)
        copied = state.covariance
        replay.step({"a": (2.4, 1.0)})
        self.assertEqual(state.covariance, copied)

    def test_stop_exposes_filter_inertia_then_converges_without_an_instant_stop_claim(self):
        replay = self.replay()
        snapshot, _, now = replay.walking()
        before, = snapshot.tracks
        self.assertGreater(before.motion.velocity_mps[0], 0.3)
        snapshot, _, now = replay.step({"a": (2.28, 1.0)})
        first_stop, = snapshot.tracks
        self.assertEqual(first_stop.track_id, before.track_id)
        self.assertGreater(first_stop.motion.velocity_mps[0], 0)
        self.assertLess(first_stop.motion.velocity_mps[0], before.motion.velocity_mps[0])
        first_forecast = forecast(snapshot, first_stop, now)
        self.assertGreater(first_forecast.samples[-1].xy_m[0], first_stop.motion.xy_m[0])

        for _ in range(15):
            snapshot, _, now = replay.step({"a": (2.28, 1.0)})
        settled, = snapshot.tracks
        self.assertLess(abs(settled.motion.velocity_mps[0]), 0.1 * first_stop.motion.velocity_mps[0])
        settled_forecast = forecast(snapshot, settled, now)
        self.assertLess(abs(settled_forecast.samples[-1].xy_m[0] - settled.motion.xy_m[0]),
                        first_forecast.samples[-1].xy_m[0] - first_stop.motion.xy_m[0])

    def test_u_turn_uses_updated_filter_velocity_with_a_transition(self):
        replay = self.replay()
        snapshot, _, _ = replay.walking()
        before, = snapshot.tracks
        snapshot, _, now = replay.step({"a": (2.16, 1.0)})
        turning, = snapshot.tracks
        self.assertEqual(turning.track_id, before.track_id)
        self.assertGreater(turning.motion.velocity_mps[0], -before.motion.velocity_mps[0])
        self.assertNotAlmostEqual(turning.motion.velocity_mps[0], -0.6, places=3)
        for index in range(1, 11):
            snapshot, _, now = replay.step({"a": (2.16 - index * 0.12, 1.0)})
        reversed_track, = snapshot.tracks
        self.assertLess(reversed_track.motion.velocity_mps[0], -0.3)
        self.assertLess(forecast(snapshot, reversed_track, now).samples[-1].xy_m[0], reversed_track.motion.xy_m[0])

    def test_sideways_head_direction_cannot_replace_travel_velocity(self):
        replay = self.replay()
        snapshot, _, now = replay.walking()
        track, = snapshot.tracks
        original = forecast(snapshot, track, now)
        for facing in ((0.0, 1.0, 0.0), (-1.0, 0.0, 0.0)):
            yaw = math.degrees(math.atan2(facing[0], facing[1])) % 360
            head = Orientation3D(facing, (0.0, 0.0, 1.0), yaw, 0.0, 25.0, snapshot.frame.capture_mono,
                                 (0, 1, 2, 3, 4), "rgb_coco5_pnp_model")
            turned = dataclasses.replace(track, head_orientation=head)
            current = dataclasses.replace(snapshot, tracks=(turned,))
            actual = forecast(current, turned, now)
            self.assertEqual(actual, original)
            packet, reason = forecast_spatial.qualify(actual, current, turned, now)
            self.assertIsNone(reason)
            self.assertEqual(packet["samples"], dataclasses.asdict(original)["samples"])

    def test_current_frame_and_same_path_with_wrong_calibration_refuse_old_forecasts(self):
        replay = self.replay()
        before, _, now = replay.walking()
        track, = before.tracks
        old = forecast(before, track, now)
        current, _, current_now = replay.step({"a": (2.4, 1.0)})
        current_track, = current.tracks
        self.assertLess(current_now, old.valid_until_mono)
        self.assertIsNone(forecast_spatial.qualify(old, current, current_track, current_now)[0])
        for changes in (dict(depth_calibration_sha256="d" * 64), dict(transform_id="registered-room@2"),
                        dict(scan_revision="scan-2"), dict(service_run_id="other-run"), dict(camera_epoch=2)):
            changed_frame = dataclasses.replace(before.frame, **changes)
            changed = dataclasses.replace(before, frame=changed_frame,
                                          service_run_id=changed_frame.service_run_id,
                                          camera_epoch=changed_frame.camera_epoch)
            self.assertIsNone(motion.forecast(track.motion, now, frame=changed_frame,
                                             track_id=track.track_id, association_revision=track.association_revision))
            self.assertIsNone(forecast_spatial.qualify(old, changed, track, now)[0])

    def test_crossing_and_occlusion_interrupt_history_before_revision_changes(self):
        replay = self.replay()
        for _ in range(20):
            snapshot, _, now = replay.step({"a": (-3.0, 1.0), "b": (3.0, 1.0)})
        self.assertTrue(all(track.motion.lead_qualified for track in snapshot.tracks))
        previous = {track.track_id: forecast(snapshot, track, now) for track in snapshot.tracks}
        crossing = None
        for index in range(1, 21):
            snapshot, _, now = replay.step({"a": (-3.0 + index * 0.15, 1.0),
                                             "b": (3.0 - index * 0.15, 1.0)})
            if any(track.association_status == "ambiguous" for track in snapshot.tracks):
                crossing = snapshot
                break
            previous = {track.track_id: forecast(snapshot, track, now) for track in snapshot.tracks}
        self.assertIsNotNone(crossing)
        for track in crossing.tracks:
            self.assertEqual(track.association_revision, 1)
            self.assertEqual(track.association_status, "ambiguous")
            self.assertIsNone(track.motion)
            self.assertIsNone(forecast_spatial.qualify(previous[track.track_id], crossing, track, now)[0])

        for _ in range(4):
            snapshot, _, now = replay.step({})
            self.assertTrue(all(track.motion is None for track in snapshot.tracks))
        self.assertTrue(any(track.state == "coasting" for track in snapshot.tracks))
        reset = None
        for _ in range(20):
            snapshot, _, now = replay.step({"a": (-3.0, 1.0), "b": (3.0, 1.0)})
            reset = next((track for track in snapshot.tracks if track.motion is not None), None)
            if reset is not None:
                break
        self.assertIsNotNone(reset)
        self.assertEqual(reset.motion.sample_count, 1)
        self.assertFalse(reset.motion.lead_qualified)
        self.assertIsNone(forecast(snapshot, reset, now))


if __name__ == "__main__":
    unittest.main()
