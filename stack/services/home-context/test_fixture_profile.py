"""Public authored fixtures and independent profile refusal controls."""
import copy
import math
import unittest

from fixture_profile import (CAP_FIELDS, EVIDENCE_GROUPS, MODEL_SCHEMA, PIN_NAMES,
                             PROFILE_SCHEMA, FixtureProfileRefusal, describe_profile,
                             digest, validate_profile)


def fictional_profile():
    pins = {name: ("%02x" % (i + 1)) * 32 for i, name in enumerate(sorted(PIN_NAMES))}
    # The six wire identity pins match PCB's public fictional example, never a unit.
    pins.update({name: ("%02x" % (i + 1)) * 32 for i, name in enumerate(
        ("fixture", "firmware", "protocol", "profile", "calibration", "contract"))})
    axes = []
    for index, name in enumerate(("pan", "tilt")):
        axes.append({"name": name, "request_id": 0x141 + index, "response_id": 0x241 + index,
                     "protocol": "rmd-a4-degrees-v1", "target_unit": "centidegree",
                     "speed_unit": "degree_per_second", "zero_centideg": 0, "direction": 1,
                     "units_per_fixture_degree": {"numerator": 100, "denominator": 1},
                     "minimum_centideg": -17000, "maximum_centideg": 17000,
                     "fixture_min_deg": -170, "fixture_max_deg": 170,
                     "max_speed_deg_s": 30, "stored_acceleration_centideg_s2": 10000})
    return {"schema": PROFILE_SCHEMA, "version": 1, "model_schema": MODEL_SCHEMA,
            "profile_id": "fictional-c12-a4-1", "fixture_id": "fictional-fixture-1",
            "status": "fictional", "production_permission": False, "pins": pins,
            "evidence": {name: {"status": "fictional", "receipt_sha256": "aa" * 32,
                                "valid_from_home_us": 0, "valid_until_home_us": 10_000_000}
                         for name in EVIDENCE_GROUPS},
            "geometry": {"room_frame": "z_up_metric_floor0", "mount_position_m": [8, 8, 8],
                         "optical_pivot_m": [0, 0, 2],
                         "fixture_to_room_basis": [1, 0, 0, 0, 1, 0, 0, 0, 1],
                         "position_uncertainty_m": 0.001, "orientation_uncertainty_rad": 0.001},
            "axes": axes,
            "bounds": {"max_motion_us": 2_000_000, "arrival_bound_us": 1_800_000,
                       "launch_skew_us": 100_000, "driver_commit_us": 100_000,
                       "darkness_decay_us": 100_000, "settle_window_us": 200_000,
                       "protective_reserve_us": 200_000, "max_observation_age_us": 250_000,
                       "clock_uncertainty_us": 10_000, "max_support_us": 3_000_000,
                       "tracking_error_rad": 0.002, "max_quantization_error_rad": 0.0002}}


class ProfileTests(unittest.TestCase):
    def test_fictional_is_available_but_never_physical(self):
        d = describe_profile(fictional_profile(), now_home_us=1_000_000)
        self.assertTrue(d["simulation_available"])
        self.assertFalse(d["physical_eligible"])

    def test_unknown_shipping_control_is_valid_but_unavailable(self):
        p = fictional_profile()
        p.update(status="unqualified", geometry=None, axes=None, bounds=None)
        p["evidence"] = {name: {"status": "unknown", "receipt_sha256": None,
                                "valid_from_home_us": None, "valid_until_home_us": None}
                         for name in EVIDENCE_GROUPS}
        self.assertEqual(validate_profile(p), p)
        self.assertFalse(describe_profile(p, now_home_us=1)["simulation_available"])

    def test_profile_validation_detaches_input(self):
        p = fictional_profile()
        v = validate_profile(p)
        v["axes"][0]["max_speed_deg_s"] = 1
        self.assertEqual(p["axes"][0]["max_speed_deg_s"], 30)

    def test_exact_schema_and_bounded_fields(self):
        for key, value in (("version", True), ("version", 2), ("schema", "config7"),
                           ("production_permission", True), ("fixture_id", "with space")):
            with self.subTest(key=key, value=value):
                p = fictional_profile()
                p[key] = value
                with self.assertRaises(FixtureProfileRefusal):
                    validate_profile(p)
        p = fictional_profile()
        p["display_transform"] = [1] * 16
        with self.assertRaises(FixtureProfileRefusal):
            validate_profile(p)

    def test_basis_reflection_scaling_nonfinite_and_degeneracy_refuse(self):
        for basis in ([1, 0, 0, 0, 1, 0, 0, 0, -1], [2, 0, 0, 0, 1, 0, 0, 0, 1],
                      [math.nan, 0, 0, 0, 1, 0, 0, 0, 1], [0] * 9):
            with self.subTest(basis=basis):
                p = fictional_profile()
                p["geometry"]["fixture_to_room_basis"] = basis
                with self.assertRaises(FixtureProfileRefusal):
                    validate_profile(p)

    def test_expiry_is_exclusive_and_future_evidence_not_available(self):
        p = fictional_profile()
        self.assertFalse(describe_profile(p, now_home_us=10_000_000)["simulation_available"])
        p["evidence"]["geometry"]["valid_from_home_us"] = 2_000_000
        self.assertFalse(describe_profile(p, now_home_us=1_000_000)["simulation_available"])

    def test_missing_bound_never_inherits_a_source_default(self):
        p = fictional_profile()
        del p["bounds"]["darkness_decay_us"]
        with self.assertRaises(FixtureProfileRefusal):
            validate_profile(p)

    def test_motor_ids_units_acceleration_and_ratios_are_explicit(self):
        cases = [("stored_acceleration_centideg_s2", 0), ("target_unit", "unknown"),
                 ("speed_unit", "rpm"), ("direction", True), ("request_id", 0),
                 ("units_per_fixture_degree", {"numerator": 100, "denominator": 0})]
        for key, value in cases:
            p = fictional_profile()
            p["axes"][0][key] = value
            with self.subTest(key=key), self.assertRaises(FixtureProfileRefusal):
                validate_profile(p)
        p = fictional_profile()
        p["axes"][1]["request_id"] = p["axes"][0]["request_id"]
        with self.assertRaises(FixtureProfileRefusal):
            validate_profile(p)

    def test_source_or_installed_status_does_not_qualify_simulation(self):
        for status in ("source_known", "installed_observed", "qualified", "unknown"):
            p = fictional_profile()
            p["evidence"]["mechanics"]["status"] = status
            if status == "unknown":
                p["evidence"]["mechanics"].update(receipt_sha256=None, valid_from_home_us=None,
                                                  valid_until_home_us=None)
            with self.subTest(status=status):
                self.assertFalse(describe_profile(p, now_home_us=1)["simulation_available"])


if __name__ == "__main__":
    unittest.main()
