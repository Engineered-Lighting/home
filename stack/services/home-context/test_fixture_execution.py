"""Independent direction/quantization, causal binding and refusal controls."""
import copy
import math
import unittest

from fixture_execution import (CHECK_NAMES, FixtureExecutionRefusal, compile_stationary,
                               compile_target, quantize_target_centideg)
from fixture_profile import CAP_FIELDS, FixtureProfileRefusal, digest
from test_fixture_profile import fictional_profile


def fictional_request(profile=None, *, target_room_m=None):
    """Explicit authored request for offline integration; not a real room receipt."""
    p = fictional_profile() if profile is None else profile
    # Independently chosen 45-degree tilt and zero-degree pan from the optical
    # pivot [0,0,2]. The mount is deliberately different and irrelevant to aim.
    target = [1, 0, 1] if target_room_m is None else list(target_room_m)
    r = {"kind": "MOVE", "motion_id": 10, "revision_id": 1, "predecessor_id": 0,
         "scope": {"home_id": "fictional-home", "room_id": "fictional-den",
                   "fixture_id": p["fixture_id"]}, "pins": copy.deepcopy(p["pins"]),
         "lineage": {"source_cut_sha256": "07" * 32, "protection_sha256": "08" * 32,
                     "authority_sha256": "09" * 32, "scene_id": "scene-fictional-1",
                     "episode_id": "episode-fictional-1", "example_id": "example-fictional-1",
                     "correction_ref": None},
         "context": {"boot_generation": 1, "session_id": 2, "ownership_generation": 4},
         "target_room_m": target, "speed_fixture_deg_s": [30, 30],
         "issued_home_us": 1_000_000, "support_expiry_home_us": 3_900_000,
         "original_caps": {key: 4_000_000 for key in CAP_FIELDS},
         "motion_timeout_us": 2_000_000,
         "current_axes": {"status": "fictional", "stationary": True, "target_centideg": [0, 0],
                          "sampled_home_us": 900_000, "available_home_us": 950_000,
                          "boot_generation": 1, "session_id": 2,
                          "profile_sha256": digest(p), "calibration_sha256": p["pins"]["calibration"]},
         "assessment": None}
    return reassess(p, r)


def reassess(profile, request, *, start=None):
    """Authored assessment builder; checks are assertions by the fixture author.

    There is intentionally no collision/coverage computation in this helper.
    H1 must label these assertions fictional and retain the exact original cut.
    """
    r = copy.deepcopy(request)
    q = compile_target(profile, r["target_room_m"], r["speed_fixture_deg_s"],
                       now_home_us=r["issued_home_us"])
    if start is None:
        start = r["current_axes"]["target_centideg"] if r["current_axes"] is not None else [0, 0]
    r["assessment"] = {"status": "fictional", "assessment_id": "authored-assessment-1",
                       "method": "fictional-room-and-path-assessment-1", "profile_sha256": digest(profile),
                       "pins": copy.deepcopy(r["pins"]), "lineage": copy.deepcopy(r["lineage"]),
                       "target_room_m": copy.deepcopy(r["target_room_m"]),
                       "target_centideg": q["target_centideg"], "start_centideg": list(start),
                       "current_axes_sha256": digest(r["current_axes"]) if r["current_axes"] is not None else None,
                       "motion_id": r["motion_id"], "checks": {key: True for key in CHECK_NAMES},
                       "valid_from_home_us": r["issued_home_us"], "valid_until_home_us": 4_000_000,
                       "original_caps": copy.deepcopy(r["original_caps"]), "target_uncertainty_m": 0.001,
                       "total_aim_error_bound_rad": 0.02}
    return r


def retained_request(profile, previous, kind="CONTINUE"):
    r = fictional_request(profile)
    r.update(kind=kind, revision_id=previous["revision_id"] + 1,
             predecessor_id=previous["revision_id"], motion_id=previous["motion_id"],
             issued_home_us=1_100_000, current_axes=None)
    r["lineage"]["source_cut_sha256"] = "ab" * 32
    return reassess(profile, r, start=previous["start_centideg"])


class TargetTests(unittest.TestCase):
    def test_independent_known_directions_and_downward_tilt(self):
        cases = [([1, 0, 1], [0, 4500]), ([0, 1, 1], [9000, 4500]),
                 ([0, -1, 1], [-9000, 4500]), ([1, 0, 2], [0, 9000]),
                 ([1, 0, 3], [0, 13500])]
        for target, expected in cases:
            with self.subTest(target=target):
                out = compile_target(fictional_profile(), target, [20, 20], now_home_us=1_000_000)
                self.assertEqual(out["target_centideg"], expected)
                self.assertFalse(out["physical_eligible"])

    def test_basis_inverse_mount_and_optical_pivot_are_distinct(self):
        p = fictional_profile()
        # Fixture +X points room +Y: rotate+90 around room Z.
        p["geometry"]["fixture_to_room_basis"] = [0, -1, 0, 1, 0, 0, 0, 0, 1]
        q = compile_target(p, [0, 1, 1], [20, 20], now_home_us=1_000_000)
        self.assertEqual(q["target_centideg"], [0, 4500])
        p["geometry"]["mount_position_m"] = [-70, 90, 35]
        self.assertEqual(compile_target(p, [0, 1, 1], [20, 20], now_home_us=1_000_000)["target_centideg"], [0, 4500])
        p["geometry"]["optical_pivot_m"] = [0, 0, 1]
        self.assertEqual(compile_target(p, [0, 1, 1], [20, 20], now_home_us=1_000_000)["target_centideg"], [0, 9000])

    def test_literal_half_away_from_zero_zero_and_transmission(self):
        axis = fictional_profile()["axes"][0]
        for angle, expected in (("0.005", 1), ("-0.005", -1), ("0.0049999", 0),
                                ("-0.015", -2), ("1.235", 124)):
            with self.subTest(angle=angle):
                self.assertEqual(quantize_target_centideg(axis, angle), expected)
        axis.update(zero_centideg=123, direction=-1,
                    units_per_fixture_degree={"numerator": 300, "denominator": 2})
        self.assertEqual(quantize_target_centideg(axis, "2.5"), -252)

    def test_direction_ratio_and_speed_conversion_are_explicit(self):
        p = fictional_profile()
        p["axes"][1].update(direction=-1, zero_centideg=100,
                              units_per_fixture_degree={"numerator": 200, "denominator": 1})
        q = compile_target(p, [1, 0, 1], [29.999, 12.999], now_home_us=1_000_000)
        self.assertEqual(q["target_centideg"], [0, -8900])
        self.assertEqual(q["speed_deg_s"], [29, 25])  # floor, never round cap upward
        self.assertEqual(compile_target(p, [1, 0, 1], [90, 90], now_home_us=1_000_000)["speed_deg_s"], [30, 30])

    def test_ambiguous_pan_origin_limits_and_speed_refuse(self):
        cases = [([0, 0, 1], [20, 20]), ([0, 0, 2], [20, 20]), ([-1, 0, 1], [20, 20]),
                 ([1, 0, 1], [0.999, 20]), ([1, 0, 1], [0, 20]), ([math.inf, 0, 1], [20, 20])]
        for target, speed in cases:
            with self.subTest(target=target, speed=speed), self.assertRaises(FixtureProfileRefusal):
                compile_target(fictional_profile(), target, speed, now_home_us=1_000_000)

    def test_quantization_error_and_overflow_refuse(self):
        p = fictional_profile()
        p["axes"][1]["units_per_fixture_degree"] = {"numerator": 1, "denominator": 1}
        with self.assertRaisesRegex(FixtureExecutionRefusal, "quantization_exceeds_bound"):
            compile_target(p, [1, 0, 0.9], [2000, 2000], now_home_us=1_000_000)
        axis = fictional_profile()["axes"][0]
        with self.assertRaisesRegex(FixtureExecutionRefusal, "target_overflow"):
            quantize_target_centideg(axis, "30000000")


class StationaryTests(unittest.TestCase):
    def setUp(self):
        self.p = fictional_profile()
        self.r = fictional_request(self.p)

    def compile(self, request=None, **kwargs):
        return compile_stationary(self.p, self.r if request is None else request,
                                  now_home_us=kwargs.pop("now_home_us", 1_000_000), **kwargs)

    def test_move_is_detached_candidate_preserving_all_lineage(self):
        d = self.compile()
        self.assertEqual(d["target_centideg"], [0, 4500])
        self.assertEqual(d["lineage"], self.r["lineage"])
        self.assertEqual(d["phase_policy"], "dark_retarget")
        self.assertFalse(d["physical_eligible"])
        self.assertEqual(d["draft_sha256"], digest({k: v for k, v in d.items() if k != "draft_sha256"}))
        d["lineage"]["scene_id"] = "mutated"
        self.assertEqual(self.r["lineage"]["scene_id"], "scene-fictional-1")

    def test_physical_mode_refuses_even_with_source_claims(self):
        with self.assertRaisesRegex(FixtureExecutionRefusal, "physical_admission_not_implemented"):
            self.compile(mode="physical")

    def test_brightness_and_continue_keep_exact_motion_and_origin(self):
        previous = self.compile()
        for kind in ("LEVELS", "CONTINUE"):
            r = retained_request(self.p, previous, kind)
            r["lineage"]["correction_ref"] = {"intent_id": "brightness-2", "body_sha256": "ac" * 32}
            r = reassess(self.p, r, start=previous["start_centideg"])
            d = self.compile(r, now_home_us=1_100_000, previous=previous)
            for key in ("motion_id", "motion_origin_revision_id", "target_centideg", "speed_deg_s", "motion_timeout_us", "start_centideg"):
                self.assertEqual(d[key], previous[key])
            self.assertEqual(d["phase_policy"], "retain_motion")
            self.assertEqual(d["lineage"]["example_id"], previous["lineage"]["example_id"])
            self.assertEqual(d["lineage"]["correction_ref"]["intent_id"], "brightness-2")
            self.assertEqual(previous["lineage"]["correction_ref"], None)

    def test_continue_cannot_extend_fixed_motion_timeout_or_change_target(self):
        previous = self.compile()
        changes = [("motion_timeout_us", 1_900_000), ("motion_id", 11),
                   ("target_room_m", [1.1, 0, 1]), ("speed_fixture_deg_s", [21, 20])]
        for key, value in changes:
            r = retained_request(self.p, previous)
            r[key] = value
            r = reassess(self.p, r)
            with self.subTest(key=key), self.assertRaisesRegex(FixtureExecutionRefusal, "retained_motion_changed"):
                self.compile(r, now_home_us=1_100_000, previous=previous)

    def test_new_move_requires_new_motion_identity_and_fresh_current_pose(self):
        previous = self.compile()
        r = fictional_request(self.p, target_room_m=[math.cos(math.radians(10)) * math.sin(math.radians(20)),
                                                   math.sin(math.radians(10)) * math.sin(math.radians(20)),
                                                   2 - math.cos(math.radians(20))])
        r.update(revision_id=2, predecessor_id=1)
        with self.assertRaisesRegex(FixtureExecutionRefusal, "move_reuses_motion_id"):
            self.compile(r, previous=previous)
        r["motion_id"] = 11
        r = reassess(self.p, r)
        d = self.compile(r, previous=previous)
        self.assertEqual(d["motion_origin_revision_id"], 2)
        self.assertEqual(d["phase_policy"], "dark_retarget")
        r["current_axes"] = None
        with self.assertRaises(FixtureProfileRefusal):
            self.compile(r, previous=previous)

    def test_original_expiry_is_exclusive_never_restarted_from_receipt(self):
        for now in (3_900_000, 4_000_000):
            with self.subTest(now=now), self.assertRaisesRegex(FixtureExecutionRefusal, "support_not_current"):
                self.compile(now_home_us=now)
        r = copy.deepcopy(self.r)
        r["support_expiry_home_us"] = 4_000_001
        with self.assertRaisesRegex(FixtureExecutionRefusal, "support_exceeds_original_bounds"):
            self.compile(r)

    def test_late_move_cannot_illuminate_without_remaining_completion_reserves(self):
        r = copy.deepcopy(self.r)
        r["current_axes"].update(sampled_home_us=2_500_000, available_home_us=2_500_000)
        r["issued_home_us"] = 2_500_000
        r = reassess(self.p, r)
        with self.assertRaisesRegex(FixtureExecutionRefusal, "insufficient_remaining_service_lease"):
            self.compile(r, now_home_us=2_500_000)

    def test_arrival_bound_cannot_beat_speed_and_stored_acceleration(self):
        r = copy.deepcopy(self.r)
        r["speed_fixture_deg_s"] = [20, 20]
        r = reassess(self.p, r)
        with self.assertRaisesRegex(FixtureExecutionRefusal, "arrival_bound_below_a4_minimum"):
            self.compile(r)
        p = fictional_profile()
        p["axes"][1]["stored_acceleration_centideg_s2"] = 100
        r = fictional_request(p)
        with self.assertRaisesRegex(FixtureExecutionRefusal, "arrival_bound_below_a4_minimum"):
            compile_stationary(p, r, now_home_us=1_000_000)

    def test_uncertainty_envelope_cannot_cross_mechanical_limits(self):
        p = fictional_profile()
        p["axes"][1]["maximum_centideg"] = 4500
        r = fictional_request(p)
        with self.assertRaisesRegex(FixtureExecutionRefusal, "uncertainty_exceeds_mechanical_limits"):
            compile_stationary(p, r, now_home_us=1_000_000)

    def test_profile_registration_collision_model_and_equipment_pins_exact(self):
        for key in self.r["pins"]:
            r = copy.deepcopy(self.r)
            r["pins"][key] = "fe" * 32
            with self.subTest(key=key), self.assertRaisesRegex(FixtureExecutionRefusal, "profile_pins_mismatch"):
                self.compile(r)

    def test_assessment_cannot_borrow_another_cut_scene_pose_or_cap(self):
        fields = {"lineage": {**self.r["assessment"]["lineage"], "source_cut_sha256": "ca" * 32},
                  "target_room_m": [2, 0, 1], "target_centideg": [0, 4400], "start_centideg": [1, 0],
                  "motion_id": 11, "current_axes_sha256": "cb" * 32,
                  "original_caps": {**self.r["original_caps"], CAP_FIELDS[0]: 4_000_001}}
        for key, value in fields.items():
            r = copy.deepcopy(self.r)
            r["assessment"][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(FixtureExecutionRefusal, "assessment_binding_mismatch"):
                self.compile(r)

    def test_missing_any_protection_or_room_or_path_check_refuses(self):
        for key in CHECK_NAMES:
            for value in (False, 1, "true"):
                r = copy.deepcopy(self.r)
                r["assessment"]["checks"][key] = value
                with self.subTest(key=key, value=value), self.assertRaisesRegex(FixtureExecutionRefusal, "unsupported_geometry_or_path"):
                    self.compile(r)

    def test_observed_sample_age_and_clock_order_are_independent_of_receipt(self):
        for patch in ({"sampled_home_us": 749_999, "available_home_us": 999_999},
                      {"sampled_home_us": 1_000_001, "available_home_us": 1_000_001},
                      {"sampled_home_us": 990_000, "available_home_us": 980_000},
                      {"boot_generation": 2}, {"boot_generation": True}, {"session_id": 3}, {"stationary": False},
                      {"profile_sha256": "ff" * 32}, {"calibration_sha256": "ee" * 32}):
            r = copy.deepcopy(self.r)
            r["current_axes"].update(patch)
            r = reassess(self.p, r)
            with self.subTest(patch=patch), self.assertRaises(FixtureProfileRefusal):
                self.compile(r)

    def test_assessment_must_enclose_position_orientation_tracking_and_quantization(self):
        r = copy.deepcopy(self.r)
        r["assessment"]["total_aim_error_bound_rad"] = 0.001
        with self.assertRaisesRegex(FixtureExecutionRefusal, "assessment_understates_uncertainty"):
            self.compile(r)
        r["assessment"]["target_uncertainty_m"] = 2
        with self.assertRaisesRegex(FixtureExecutionRefusal, "unbounded_aim_uncertainty"):
            self.compile(r)

    def test_numeric_lookalikes_cannot_change_exact_assessment_bytes(self):
        r = copy.deepcopy(self.r)
        r["assessment"]["target_centideg"] = [0.0, 4500.0]
        with self.assertRaisesRegex(FixtureExecutionRefusal, "assessment_binding_mismatch"):
            self.compile(r)

    def test_truncated_or_extra_previous_draft_refuses_cleanly(self):
        previous = self.compile()
        r = retained_request(self.p, previous)
        for patch in ({"unrecognized": 1},):
            b = {**previous, **patch}
            b["draft_sha256"] = digest({k: v for k, v in b.items() if k != "draft_sha256"})
            with self.assertRaisesRegex(FixtureExecutionRefusal, "previous_draft_unavailable"):
                self.compile(r, previous=b, now_home_us=1_100_000)
        del previous["motion_origin_revision_id"]
        with self.assertRaisesRegex(FixtureExecutionRefusal, "previous_draft_unavailable"):
            self.compile(r, previous=previous, now_home_us=1_100_000)
    def test_continuation_cannot_resume_expired_or_reset_motion(self):
        previous = self.compile()
        r = retained_request(self.p, previous)
        r.update(issued_home_us=3_900_000, support_expiry_home_us=4_000_000)
        r = reassess(self.p, r)
        with self.assertRaisesRegex(FixtureExecutionRefusal, "expired_motion_cannot_resume"):
            self.compile(r, now_home_us=3_900_000, previous=previous)
        r = retained_request(self.p, previous)
        r["context"]["boot_generation"] = 2
        r = reassess(self.p, r)
        with self.assertRaisesRegex(FixtureExecutionRefusal, "previous_context_mismatch"):
            self.compile(r, now_home_us=1_100_000, previous=previous)

    def test_changed_previous_reused_revision_predecessor_or_scene_refuse(self):
        previous = self.compile()
        bad = copy.deepcopy(previous)
        bad["target_centideg"][0] = 200
        r = retained_request(self.p, previous)
        with self.assertRaisesRegex(FixtureExecutionRefusal, "previous_draft_changed"):
            self.compile(r, now_home_us=1_100_000, previous=bad)
        for patch in ({"revision_id": 1}, {"predecessor_id": 0}):
            b = {**r, **patch}
            with self.subTest(patch=patch), self.assertRaisesRegex(FixtureExecutionRefusal, "revision_order_mismatch"):
                self.compile(b, now_home_us=1_100_000, previous=previous)
        r["lineage"]["scene_id"] = "other-scene"
        r = reassess(self.p, r)
        with self.assertRaisesRegex(FixtureExecutionRefusal, "retained_scene_mismatch"):
            self.compile(r, now_home_us=1_100_000, previous=previous)


if __name__ == "__main__":
    unittest.main()
