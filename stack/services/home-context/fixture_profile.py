"""Pure validation of the additive hardware compiler profile.

This is a Home adapter profile, not the PCB's binary codec or a qualification
issuer. A valid document may still describe wholly unavailable hardware. Only
explicit fictional profiles can feed the offline compiler in this revision.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math

PROFILE_SCHEMA = "home-fixture-commissioning-profile-1"
MODEL_SCHEMA = "home-fixture-execution-model-1"
PIN_NAMES = frozenset({"fixture", "firmware", "protocol", "profile", "calibration",
                       "contract", "model", "registration", "collision", "equipment"})
EVIDENCE_GROUPS = frozenset({"geometry", "motor_protocol", "mechanics", "a4_model",
                             "lighting", "thermal", "darkness", "protective", "clock"})
CAP_FIELDS = ("evidence_not_after_home_us", "protection_not_after_home_us",
              "authority_not_after_home_us", "clock_qualification_not_after_home_us")
MAX_U64 = (1 << 64) - 1


class FixtureProfileRefusal(ValueError):
    def __init__(self, code, detail=""):
        self.code = code
        super().__init__(code + (": " + detail if detail else ""))


def refuse(code, detail=""):
    raise FixtureProfileRefusal(code, detail)


def exact(value, keys, path):
    if type(value) is not dict or set(value) != set(keys):
        refuse("invalid_fields", path)


def integer(value, low, high, path):
    if type(value) is not int or not low <= value <= high:
        refuse("invalid_integer", path)
    return value


def number(value, path, low=-math.inf, high=math.inf):
    try:
        valid = type(value) in (int, float) and math.isfinite(value) and low <= value <= high
    except (OverflowError, ValueError):
        valid = False
    if not valid:
        refuse("invalid_number", path)
    return value


def identifier(value, path):
    if type(value) is not str or not 1 <= len(value) <= 128 or not value.isascii() or any(
            ord(c) < 33 or ord(c) > 126 for c in value):
        refuse("invalid_identifier", path)
    return value


def sha256(value, path):
    if type(value) is not str or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        refuse("invalid_digest", path)
    return value


def canonical(value):
    """Canonical local JSON identity; never a substitute for canonical wire bytes."""
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False,
                          separators=(",", ":")).encode("ascii")
    except (ValueError, TypeError, OverflowError, RecursionError):
        refuse("invalid_json")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def vector(value, count, path):
    if type(value) is not list or len(value) != count:
        refuse("invalid_vector", path)
    return [number(x, path) for x in value]


def validate_pins(pins):
    exact(pins, PIN_NAMES, "pins")
    for name in PIN_NAMES:
        sha256(pins[name], "pins." + name)


def _validate_geometry(g):
    exact(g, {"room_frame", "mount_position_m", "optical_pivot_m", "fixture_to_room_basis",
              "position_uncertainty_m", "orientation_uncertainty_rad"}, "geometry")
    if g["room_frame"] != "z_up_metric_floor0":
        refuse("unsupported_frame")
    vector(g["mount_position_m"], 3, "mount_position_m")
    vector(g["optical_pivot_m"], 3, "optical_pivot_m")
    basis = vector(g["fixture_to_room_basis"], 9, "fixture_to_room_basis")
    rows = [basis[i:i + 3] for i in (0, 3, 6)]
    for i in range(3):
        for j in range(3):
            if abs(sum(a * b for a, b in zip(rows[i], rows[j])) - (1 if i == j else 0)) > 1e-9:
                refuse("invalid_basis", "basis must be orthonormal")
    determinant = (basis[0] * (basis[4] * basis[8] - basis[5] * basis[7])
                   - basis[1] * (basis[3] * basis[8] - basis[5] * basis[6])
                   + basis[2] * (basis[3] * basis[7] - basis[4] * basis[6]))
    if abs(determinant - 1) > 1e-9:
        refuse("invalid_basis", "basis must be right handed")
    number(g["position_uncertainty_m"], "position_uncertainty_m", 0, 100)
    number(g["orientation_uncertainty_rad"], "orientation_uncertainty_rad", 0, math.pi)


def _validate_axis(axis, name):
    exact(axis, {"name", "request_id", "response_id", "protocol", "target_unit", "speed_unit",
                 "zero_centideg", "direction", "units_per_fixture_degree", "minimum_centideg",
                 "maximum_centideg", "fixture_min_deg", "fixture_max_deg", "max_speed_deg_s",
                 "stored_acceleration_centideg_s2"}, "axis")
    if axis["name"] != name or axis["protocol"] != "rmd-a4-degrees-v1" or axis["target_unit"] != "centidegree" or axis["speed_unit"] != "degree_per_second":
        refuse("unsupported_axis_model")
    integer(axis["request_id"], 1, 0x7ff, "request_id")
    integer(axis["response_id"], 1, 0x7ff, "response_id")
    for key in ("zero_centideg", "minimum_centideg", "maximum_centideg"):
        integer(axis[key], -(1 << 31), (1 << 31) - 1, key)
    if axis["minimum_centideg"] >= axis["maximum_centideg"] or not axis["minimum_centideg"] <= axis["zero_centideg"] <= axis["maximum_centideg"]:
        refuse("invalid_axis_limits")
    if type(axis["direction"]) is not int or axis["direction"] not in (-1, 1):
        refuse("invalid_axis_direction")
    ratio = axis["units_per_fixture_degree"]
    exact(ratio, {"numerator", "denominator"}, "units_per_fixture_degree")
    for key in ratio:
        integer(ratio[key], 1, (1 << 31) - 1, key)
    number(axis["fixture_min_deg"], "fixture_min_deg", -360, 360)
    number(axis["fixture_max_deg"], "fixture_max_deg", -360, 360)
    if axis["fixture_min_deg"] >= axis["fixture_max_deg"]:
        refuse("invalid_axis_limits")
    integer(axis["max_speed_deg_s"], 1, 0xffff, "max_speed_deg_s")
    integer(axis["stored_acceleration_centideg_s2"], 1, (1 << 32) - 1, "stored_acceleration_centideg_s2")


def _validate_bounds(bounds):
    durations = {"max_motion_us", "arrival_bound_us", "launch_skew_us", "driver_commit_us",
                 "darkness_decay_us", "settle_window_us", "protective_reserve_us",
                 "max_observation_age_us", "clock_uncertainty_us", "max_support_us"}
    exact(bounds, durations | {"tracking_error_rad", "max_quantization_error_rad"}, "bounds")
    for key in durations:
        integer(bounds[key], 1, MAX_U64, key)
    if bounds["max_support_us"] > 3_000_000 or bounds["arrival_bound_us"] > bounds["max_motion_us"]:
        refuse("invalid_timing_bounds")
    number(bounds["tracking_error_rad"], "tracking_error_rad", 0, math.pi)
    number(bounds["max_quantization_error_rad"], "max_quantization_error_rad", 0, math.pi)


def validate_profile(profile):
    """Validate and detach a versioned document; this grants no hardware authority."""
    exact(profile, {"schema", "version", "model_schema", "profile_id", "fixture_id", "status",
                    "production_permission", "pins", "evidence", "geometry", "axes", "bounds"}, "profile")
    if profile["schema"] != PROFILE_SCHEMA or type(profile["version"]) is not int or profile["version"] != 1 or profile["model_schema"] != MODEL_SCHEMA:
        refuse("unsupported_profile_version")
    identifier(profile["profile_id"], "profile_id")
    identifier(profile["fixture_id"], "fixture_id")
    if profile["status"] not in ("unqualified", "fictional", "qualified") or type(profile["production_permission"]) is not bool:
        refuse("invalid_profile_status")
    if profile["status"] != "qualified" and profile["production_permission"]:
        refuse("fictional_or_unknown_production_permission")
    validate_pins(profile["pins"])
    exact(profile["evidence"], EVIDENCE_GROUPS, "evidence")
    for key, entry in profile["evidence"].items():
        exact(entry, {"status", "receipt_sha256", "valid_from_home_us", "valid_until_home_us"}, "evidence." + key)
        if entry["status"] not in ("unknown", "source_known", "installed_observed", "qualified", "fictional"):
            refuse("invalid_evidence_status")
        if entry["status"] == "unknown":
            if entry["receipt_sha256"] is not None or entry["valid_from_home_us"] is not None or entry["valid_until_home_us"] is not None:
                refuse("unknown_evidence_has_authority")
        else:
            sha256(entry["receipt_sha256"], "receipt_sha256")
            integer(entry["valid_from_home_us"], 0, MAX_U64, "valid_from_home_us")
            integer(entry["valid_until_home_us"], 1, MAX_U64, "valid_until_home_us")
            if entry["valid_from_home_us"] >= entry["valid_until_home_us"]:
                refuse("invalid_evidence_interval")
            if entry["status"] == "fictional" and profile["status"] != "fictional":
                refuse("fictional_evidence_in_physical_profile")
    if profile["geometry"] is not None:
        _validate_geometry(profile["geometry"])
    if profile["axes"] is not None:
        if type(profile["axes"]) is not list or len(profile["axes"]) != 2:
            refuse("invalid_axes")
        for name, axis in zip(("pan", "tilt"), profile["axes"]):
            _validate_axis(axis, name)
        ids = [a[k] for a in profile["axes"] for k in ("request_id", "response_id")]
        if len(set(ids)) != 4:
            refuse("ambiguous_axis_identity")
    if profile["bounds"] is not None:
        _validate_bounds(profile["bounds"])
    if len(canonical(profile)) > 32_768:
        refuse("profile_too_large")
    return copy.deepcopy(profile)


def describe_profile(profile, *, now_home_us):
    p = validate_profile(profile)
    integer(now_home_us, 0, MAX_U64, "now_home_us")
    missing = [name + "_unavailable" for name in ("geometry", "axes", "bounds") if p[name] is None]
    for name, entry in p["evidence"].items():
        if entry["status"] != "fictional" or not entry["valid_from_home_us"] <= now_home_us < entry["valid_until_home_us"]:
            missing.append(name + "_not_current_fictional_evidence")
    if p["status"] != "fictional":
        missing.append("fictional_profile_required")
    return {"profile_sha256": digest(p), "fixture_id": p["fixture_id"],
            "simulation_available": not missing, "physical_eligible": False,
            "physical_refusal": "physical_admission_not_implemented",
            "refusals": sorted(missing)}
