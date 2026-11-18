"""Semantic projection uses actual immutable hints and never changes geometry."""
import copy
import dataclasses
import json
import math
import unittest

import intent
import motion
import test_intent
import walking_intent


def hint(**changes):
    value = test_intent.wire()
    value.update(changes)
    return intent.from_wire(value, now_mono=100.3)


def snapshot():
    value, track = test_intent.current()
    value["frame"].update(frame_id=33, arrival_mono=100.21)
    value["tracks"] = [track]
    return value


def measured(**changes):
    values = dict(service_run_id="run", camera_epoch=1, frame_id=33, track_id="person",
                  association_revision=2, transform_id="transform", scan_revision="scan",
                  depth_calibration_sha256="a" * 64, observed_mono=100.2, available_mono=100.21,
                  valid_until_mono=101.7, xy_m=(0, 0), velocity_mps=(0.5, 0),
                  covariance=((0.01, 0, 0, 0), (0, 0.01, 0, 0), (0, 0, 0.01, 0), (0, 0, 0, 0.01)),
                  sample_count=4, history_span_s=0.6, white_accel_sigma_mps32=0,
                  registration_error_m=0, timing_error_s=0, source_obs_ids=("observation",))
    return motion.MotionState(**(values | changes))


class WalkingIntentTest(unittest.TestCase):
    def test_real_hint_keeps_independent_raw_scores_and_private_evidence_out(self):
        value = hint()
        result = walking_intent.project(value, 100.3, snapshot())
        self.assertEqual((result["disposition"], result["reason"]),
                         ("ignored", "awaiting_held_out_admission"))
        self.assertFalse(result["selected"])
        self.assertFalse(result["consumed"])
        self.assertEqual(result["clock"], "producer_monotonic")
        self.assertEqual(result["observed_at"], 100)
        self.assertEqual(result["produced_at"], 100.1)
        self.assertEqual(result["expires_at"], 102)
        self.assertEqual(result["dependencies"], dict(value.dependencies))
        self.assertEqual(result["model_revision"], value.model_revision)
        self.assertEqual(result["snapshot_manifest_digest"], value.snapshot_manifest_digest)
        self.assertEqual(result["hypotheses"], [dict(intent=choice, raw_score=float(index),
            calibrated_probability=False) for index, choice in enumerate(intent.CHOICES)])
        encoded = json.dumps(result, allow_nan=False)
        for private in ("private", "blob_ref", "source_frame", "track_bbox", "roi_xyxy", "http", "text"):
            self.assertNotIn(private, encoded)
        self.assertLess(len(encoded), 3000)

    def test_none_scores_are_five_unknown_raw_scores(self):
        result = walking_intent.project(hint(raw_option_scores=None), 100.3, snapshot())
        self.assertEqual(len(result["hypotheses"]), 5)
        self.assertTrue(all(item["raw_score"] is None and item["calibrated_probability"] is False
                            for item in result["hypotheses"]))

    def test_absent_and_malformed_input_are_bounded_diagnostics(self):
        self.assertEqual(walking_intent.project(None, 100.3, snapshot())["disposition"], "absent")
        for value in ({"url": "https://private.invalid/clip"}, object(), "private raw text"):
            result = walking_intent.project(value, 100.3, snapshot())
            self.assertEqual(result["reason"], "malformed_intent_hint")
            self.assertIsNone(result["request_id"])
            self.assertEqual(result["hypotheses"], [])
            self.assertNotIn("private", json.dumps(result))
        for changes in (dict(request_id="x" * 257), dict(clip_id="https://private.invalid/clip"),
                        dict(raw_option_scores={key: math.nan for key in intent.CHOICES}),
                        dict(raw_option_scores={key: 10 ** 1000 for key in intent.CHOICES}),
                        dict(raw_option_scores={"continue": 1}), dict(authority="lighting_owner")):
            result = walking_intent.project(dataclasses.replace(hint(), **changes), 100.3, snapshot())
            self.assertEqual(result["reason"], "malformed_intent_hint")
            self.assertIsNone(result["clip_id"])

    def test_original_expiry_future_result_and_control_cutoff(self):
        value = hint()
        for at in (102, 200):
            result = walking_intent.project(value, at, snapshot())
            self.assertEqual((result["disposition"], result["reason"]), ("expired", "capture_lease_expired"))
            self.assertEqual(result["expires_at"], 102)
        future = dataclasses.replace(value, produced_at=100.4)
        self.assertEqual(walking_intent.project(future, 100.3, snapshot())["reason"], "future_intent_hint")
        self.assertEqual(walking_intent.project(value, 100.3, snapshot(), control_cutoff=100.1)["reason"], "newer_control")
        self.assertEqual(walking_intent.project(value, 100.3, snapshot(), control_cutoff=100)["reason"], "awaiting_held_out_admission")
        for cutoff in (True, math.nan, 101, 10 ** 1000):
            self.assertEqual(walking_intent.project(value, 100.3, snapshot(), control_cutoff=cutoff)["reason"], "invalid_control_cutoff")
        for now in (True, math.nan, -1, 10 ** 1000):
            self.assertEqual(walking_intent.project(value, now, snapshot())["reason"], "invalid_clock")

    def test_current_full_frame_and_person_binding_are_checked(self):
        for key, changed in (("service_run_id", "other-run"), ("camera_epoch", 2), ("frame_id", -1),
                             ("frame_id", True), ("frame_id", 31), ("transform_id", "changed"),
                             ("scan_revision", "changed"), ("depth_calibration_sha256", "b" * 64),
                             ("capture_mono", 101), ("arrival_mono", 101), ("camera_id", "other-camera")):
            value = snapshot()
            value["frame"][key] = changed
            self.assertEqual(walking_intent.project(hint(), 100.3, value)["reason"], "current_binding_changed", key)
        for key, changed in (("association_revision", 3), ("association_status", "ambiguous"),
                             ("state", "coasting"), ("current_obs_ids", []), ("track_id", "other-person")):
            value = snapshot()
            value["tracks"][0][key] = changed
            self.assertEqual(walking_intent.project(hint(), 100.3, value)["reason"], "current_binding_changed", key)
        for key, changed in (("status", "stale"), ("presence", "empty"), ("service_run_id", "other-run"),
                             ("camera_epoch", 2), ("tracks", [])):
            value = snapshot()
            value[key] = changed
            self.assertEqual(walking_intent.project(hint(), 100.3, value)["reason"], "current_binding_changed", key)
        duplicate = snapshot()
        duplicate["tracks"] *= 2
        self.assertEqual(walking_intent.project(hint(), 100.3, duplicate)["reason"], "current_binding_changed")

    def test_malformed_clip_source_frame_keys_do_not_pass_instance_check(self):
        for key, changed in (("frame_id", -1), ("frame_id", True), ("service_run_id", "other-run"),
                             ("camera_epoch", 2), ("depth_calibration_sha256", "b" * 64)):
            raw = test_intent.wire()
            raw["evidence"]["frames"][0]["source_frame"][key] = changed
            value = dataclasses.replace(hint(), evidence=raw["evidence"])
            self.assertEqual(walking_intent.project(value, 100.3, snapshot())["reason"], "malformed_intent_hint", key)

    def test_newer_current_measured_stop_and_turn_supersede_the_hint(self):
        value = snapshot()
        stop = measured(velocity_mps=(0.05, 0.05))
        result = walking_intent.project(hint(), 100.3, value, motion_state=stop, plan_reason="motion_changed")
        self.assertEqual(result["reason"], "newer_measured_stop")
        self.assertFalse(result["selected"])
        turn = walking_intent.project(hint(), 100.3, value, motion_state=measured(), plan_reason="motion_changed")
        self.assertEqual(turn["reason"], "newer_measured_turn")
        boundary = walking_intent.project(hint(), 100.3, value, motion_state=measured(velocity_mps=(0.1, 0)))
        self.assertEqual(boundary["reason"], "awaiting_held_out_admission")

    def test_stale_unbound_or_not_newer_motion_cannot_claim_an_override(self):
        for state in (measured(frame_id=32), measured(association_revision=3), measured(track_id="other-person"),
                      measured(source_obs_ids=("other-observation",)), measured(available_mono=100.4),
                      measured(sample_count=1, history_span_s=0)):
            result = walking_intent.project(hint(), 100.3, snapshot(), motion_state=state, plan_reason="motion_changed")
            self.assertEqual(result["reason"], "awaiting_held_out_admission")
        value = snapshot()
        value["frame"].update(capture_mono=100, arrival_mono=100.01)
        same = measured(observed_mono=100, available_mono=100.01, valid_until_mono=101.5)
        self.assertEqual(walking_intent.project(hint(), 100.3, value, motion_state=same,
                                              plan_reason="motion_changed")["reason"], "awaiting_held_out_admission")

    def test_projection_leaves_inputs_and_geometry_forecast_unchanged(self):
        state, current = measured(), snapshot()
        before = copy.deepcopy(current)
        forecast = motion.forecast(state, 100.3)
        for value in (None, {}, hint(), dataclasses.replace(hint(), expires_at=101)):
            walking_intent.project(value, 100.3, current, motion_state=state)
        self.assertEqual(current, before)
        self.assertEqual(motion.forecast(state, 100.3), forecast)


if __name__ == "__main__":
    unittest.main()
