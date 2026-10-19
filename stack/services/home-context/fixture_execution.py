"""Pure room target -> calibrated stationary A4 compiler, offline draft only.

No bus, codec, grant signer, ID allocator, mixer, sequencer or state restoration
lives here. A fresh immutable assessment is a required input, not a conclusion
of aiming at a point. The historical virtual delivery modules are untouched.
"""
from __future__ import annotations

import copy
import math
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP

from fixture_profile import (CAP_FIELDS, MAX_U64, FixtureProfileRefusal, describe_profile,
                             canonical, digest, exact, identifier, integer, number, sha256,
                             validate_pins, validate_profile, vector)

TARGET_SCHEMA = "home-fixture-calibrated-target-draft-1"
DRAFT_SCHEMA = "home-fixture-compiled-target-draft-1"
CHECK_NAMES = frozenset({"work_coverage", "all_people_protection", "room_collision", "dark_motion_path"})
LINEAGE_FIELDS = frozenset({"source_cut_sha256", "protection_sha256", "authority_sha256",
                            "scene_id", "episode_id", "example_id", "correction_ref"})
DRAFT_FIELDS = frozenset({"schema", "version", "status", "physical_eligible", "profile_sha256",
                          "kind", "motion_id", "revision_id", "predecessor_id", "scope", "pins",
                          "lineage", "context", "target_room_m", "issued_home_us", "support_expiry_home_us",
                          "original_caps", "motion_timeout_us", "fixture_angles_deg", "target_centideg",
                          "speed_deg_s", "quantization_error_rad", "start_centideg", "assessment_sha256",
                          "motion_origin_revision_id", "phase_policy", "draft_sha256"})


class FixtureExecutionRefusal(FixtureProfileRefusal):
    pass


def reject(code, detail=""):
    raise FixtureExecutionRefusal(code, detail)


def quantize_target_centideg(axis, fixture_degrees):
    """Round half away from zero after explicit zero/direction/transmission.

    Decimal string inputs support exact independent tie vectors. This function
    does not validate a profile or grant movement; compile_target does both.
    """
    try:
        angle = Decimal(str(fixture_degrees))
        ratio = axis["units_per_fixture_degree"]
        raw = (Decimal(axis["zero_centideg"]) + Decimal(axis["direction"]) * angle
               * Decimal(ratio["numerator"]) / Decimal(ratio["denominator"]))
        if not raw.is_finite():
            reject("nonfinite_target")
        result = int(raw.to_integral_value(rounding=ROUND_HALF_UP))
    except (KeyError, TypeError, ValueError, ArithmeticError):
        reject("invalid_quantization_input")
    if not -(1 << 31) <= result < (1 << 31):
        reject("target_overflow")
    return result


def _require_fictional(p, now_home_us):
    description = describe_profile(p, now_home_us=now_home_us)
    if not description["simulation_available"]:
        reject("profile_unavailable", ",".join(description["refusals"]))


def compile_target(profile, target_room_m, speed_fixture_deg_s, *, now_home_us):
    """Calibrate a candidate target; never establish collision/delivery authority."""
    p = validate_profile(profile)
    _require_fictional(p, now_home_us)
    target = vector(target_room_m, 3, "target_room_m")
    speeds = vector(speed_fixture_deg_s, 2, "speed_fixture_deg_s")
    g = p["geometry"]
    delta = [target[i] - g["optical_pivot_m"][i] for i in range(3)]
    distance = math.hypot(*delta)
    if not math.isfinite(distance) or distance <= g["position_uncertainty_m"] or distance <= 1e-12:
        reject("target_intersects_optical_origin")
    basis = g["fixture_to_room_basis"]
    local = [sum(basis[j * 3 + i] * delta[j] for j in range(3)) for i in range(3)]
    horizontal = math.hypot(local[0], local[1])
    # At the optical axis pan is not observable from a point. Refuse rather than
    # secretly selecting zero or borrowing an old physical initial position.
    if horizontal <= 1e-12 * distance:
        reject("pan_direction_ambiguous")
    angles = [math.degrees(math.atan2(local[1], local[0])),
              math.degrees(math.atan2(horizontal, -local[2]))]
    targets, motor_speeds, errors = [], [], []
    for axis, angle, speed in zip(p["axes"], angles, speeds):
        if speed <= 0:
            reject("nonpositive_speed")
        if not axis["fixture_min_deg"] <= angle <= axis["fixture_max_deg"]:
            reject("fixture_angle_out_of_range", axis["name"])
        q = quantize_target_centideg(axis, angle)
        if not axis["minimum_centideg"] <= q <= axis["maximum_centideg"]:
            reject("motor_target_out_of_range", axis["name"])
        ratio = axis["units_per_fixture_degree"]
        # Wire speed is motor degrees/s, so the explicitly calibrated motor
        # centidegrees/fixture-degree conversion includes the divisor100.
        converted = (Decimal(str(speed)) * Decimal(ratio["numerator"])
                     / Decimal(ratio["denominator"]) / Decimal(100))
        cap = min(axis["max_speed_deg_s"], int(converted.to_integral_value(rounding=ROUND_FLOOR)))
        if cap <= 0:
            reject("speed_below_wire_resolution", axis["name"])
        reconstructed = ((q - axis["zero_centideg"]) * axis["direction"]
                         * ratio["denominator"] / ratio["numerator"])
        errors.append(abs(math.radians(reconstructed - angle)))
        targets.append(q)
        motor_speeds.append(cap)
    error = sum(errors)  # Sum is a conservative two-axis angular bound.
    if error > p["bounds"]["max_quantization_error_rad"] + 1e-15:
        reject("quantization_exceeds_bound")
    return {"schema": TARGET_SCHEMA, "version": 1, "physical_eligible": False,
            "profile_sha256": digest(p), "target_room_m": copy.deepcopy(target_room_m),
            "fixture_angles_deg": angles, "target_centideg": targets,
            "speed_deg_s": motor_speeds, "quantization_error_rad": error,
            "range_m": distance}


def _lineage(value):
    exact(value, LINEAGE_FIELDS, "lineage")
    for key in ("source_cut_sha256", "protection_sha256", "authority_sha256"):
        sha256(value[key], key)
    for key in ("scene_id", "episode_id", "example_id"):
        identifier(value[key], key)
    if value["correction_ref"] is not None:
        exact(value["correction_ref"], {"intent_id", "body_sha256"}, "correction_ref")
        identifier(value["correction_ref"]["intent_id"], "intent_id")
        sha256(value["correction_ref"]["body_sha256"], "body_sha256")


def _caps(value):
    exact(value, CAP_FIELDS, "original_caps")
    for key in CAP_FIELDS:
        integer(value[key], 1, MAX_U64, key)


def _context(value):
    exact(value, {"boot_generation", "session_id", "ownership_generation"}, "context")
    for key in value:
        integer(value[key], 1, MAX_U64, key)


def _scope(value, fixture_id):
    exact(value, {"home_id", "room_id", "fixture_id"}, "scope")
    for key in value:
        identifier(value[key], key)
    if value["fixture_id"] != fixture_id:
        reject("fixture_scope_mismatch")


def _observation(p, r, now_home_us):
    o = r["current_axes"]
    exact(o, {"status", "stationary", "target_centideg", "sampled_home_us", "available_home_us",
              "boot_generation", "session_id", "profile_sha256", "calibration_sha256"}, "current_axes")
    if o["status"] != "fictional" or o["stationary"] is not True:
        reject("current_stationary_axes_unavailable")
    if type(o["target_centideg"]) is not list or len(o["target_centideg"]) != 2:
        reject("invalid_current_axes")
    for axis, angle in zip(p["axes"], o["target_centideg"]):
        integer(angle, axis["minimum_centideg"], axis["maximum_centideg"], "observed_centideg")
    for key in ("sampled_home_us", "available_home_us"):
        integer(o[key], 0, MAX_U64, key)
    for key in ("boot_generation", "session_id"):
        integer(o[key], 1, MAX_U64, key)
    if not o["sampled_home_us"] <= o["available_home_us"] <= r["issued_home_us"] <= now_home_us:
        reject("invalid_observation_order")
    if now_home_us - o["sampled_home_us"] > p["bounds"]["max_observation_age_us"]:
        reject("stale_current_axes")
    if any(o[key] != r["context"][key] for key in ("boot_generation", "session_id")):
        reject("observation_context_mismatch")
    if o["profile_sha256"] != digest(p) or o["calibration_sha256"] != p["pins"]["calibration"]:
        reject("observation_profile_mismatch")
    return o


def _assessment(p, r, target, now_home_us, start):
    a = r["assessment"]
    exact(a, {"status", "assessment_id", "method", "profile_sha256", "pins", "lineage",
              "target_room_m", "target_centideg", "start_centideg", "current_axes_sha256",
              "motion_id", "checks", "valid_from_home_us", "valid_until_home_us",
              "original_caps", "target_uncertainty_m", "total_aim_error_bound_rad"}, "assessment")
    identifier(a["assessment_id"], "assessment_id")
    if a["status"] != "fictional" or a["method"] != "fictional-room-and-path-assessment-1":
        reject("assessment_not_fictional")
    for key, expected in (("profile_sha256", digest(p)), ("pins", r["pins"]),
                          ("lineage", r["lineage"]), ("target_room_m", r["target_room_m"]),
                          ("target_centideg", target["target_centideg"]), ("start_centideg", start),
                          ("motion_id", r["motion_id"]), ("original_caps", r["original_caps"]),
                          ("current_axes_sha256", digest(r["current_axes"]) if r["current_axes"] is not None else None)):
        if canonical(a[key]) != canonical(expected):
            reject("assessment_binding_mismatch", key)
    exact(a["checks"], CHECK_NAMES, "assessment.checks")
    if any(value is not True for value in a["checks"].values()):
        reject("unsupported_geometry_or_path")
    integer(a["valid_from_home_us"], 0, MAX_U64, "valid_from_home_us")
    integer(a["valid_until_home_us"], 1, MAX_U64, "valid_until_home_us")
    if not a["valid_from_home_us"] <= r["issued_home_us"] <= now_home_us < a["valid_until_home_us"]:
        reject("assessment_not_current")
    number(a["target_uncertainty_m"], "target_uncertainty_m", 0)
    combined = p["geometry"]["position_uncertainty_m"] + a["target_uncertainty_m"]
    if combined >= target["range_m"]:
        reject("unbounded_aim_uncertainty")
    required = (math.asin(combined / target["range_m"])
                + p["geometry"]["orientation_uncertainty_rad"]
                + p["bounds"]["tracking_error_rad"] + target["quantization_error_rad"])
    number(a["total_aim_error_bound_rad"], "total_aim_error_bound_rad", 0, math.pi)
    if a["total_aim_error_bound_rad"] + 1e-15 < required:
        reject("assessment_understates_uncertainty")
    # An assessed uncertainty envelope must remain inside commissioned travel;
    # merely rounding its centre to an in-range command is insufficient.
    for axis, angle, q in zip(p["axes"], target["fixture_angles_deg"], target["target_centideg"]):
        angular_margin_deg = math.degrees(a["total_aim_error_bound_rad"])
        ratio = axis["units_per_fixture_degree"]
        motor_margin = math.ceil(angular_margin_deg * ratio["numerator"] / ratio["denominator"])
        if (angle - angular_margin_deg < axis["fixture_min_deg"]
                or angle + angular_margin_deg > axis["fixture_max_deg"]
                or q - motor_margin < axis["minimum_centideg"]
                or q + motor_margin > axis["maximum_centideg"]):
            reject("uncertainty_exceeds_mechanical_limits", axis["name"])
    return a


def _a4_minimum_arrival_us(p, start, target):
    """Consistency lower bound, not a simulated MCU or qualified upper bound.

    From a stationary sample, even an ideal motor under the explicit speed
    cap/stored acceleration cannot arrive and stop sooner than this. Real
    response/tracking/launch bounds still come from the separately pinned model.
    """
    needed = 0
    for axis, initial, final, speed in zip(p["axes"], start, target["target_centideg"], target["speed_deg_s"]):
        distance = Decimal(abs(final - initial))
        velocity = Decimal(speed * 100)
        acceleration = Decimal(axis["stored_acceleration_centideg_s2"])
        if distance <= velocity * velocity / acceleration:
            seconds = 2 * (distance / acceleration).sqrt()
        else:
            seconds = distance / velocity + velocity / acceleration
        needed = max(needed, int((seconds * 1_000_000).to_integral_value(rounding=ROUND_CEILING)))
    return needed


def _previous(previous, p, r, now_home_us, target):
    if type(previous) is not dict or set(previous) != DRAFT_FIELDS or previous.get("schema") != DRAFT_SCHEMA or type(previous.get("version")) is not int or previous.get("version") != 1 or previous.get("status") != "simulator_candidate" or previous.get("physical_eligible") is not False:
        reject("previous_draft_unavailable")
    body = {key: value for key, value in previous.items() if key != "draft_sha256"}
    if previous.get("draft_sha256") != digest(body):
        reject("previous_draft_changed")
    for key in ("motion_id", "revision_id", "motion_origin_revision_id", "motion_timeout_us"):
        integer(previous[key], 1, MAX_U64, key)
    integer(previous["support_expiry_home_us"], 0, MAX_U64, "previous.support_expiry_home_us")
    _lineage(previous["lineage"])
    for key, expected in (("profile_sha256", digest(p)), ("pins", r["pins"]),
                          ("scope", r["scope"]), ("context", r["context"])):
        if canonical(previous.get(key)) != canonical(expected):
            reject("previous_context_mismatch", key)
    if r["predecessor_id"] != previous["revision_id"] or r["revision_id"] <= previous["revision_id"]:
        reject("revision_order_mismatch")
    if r["kind"] == "MOVE":
        if r["motion_id"] == previous["motion_id"]:
            reject("move_reuses_motion_id")
        return
    if now_home_us >= previous["support_expiry_home_us"]:
        reject("expired_motion_cannot_resume")
    for key in ("scene_id", "episode_id", "example_id"):
        if previous["lineage"][key] != r["lineage"][key]:
            reject("retained_scene_mismatch", key)
    for key, expected in (("motion_id", r["motion_id"]), ("target_room_m", r["target_room_m"]),
                          ("target_centideg", target["target_centideg"]),
                          ("speed_deg_s", target["speed_deg_s"]),
                          ("motion_timeout_us", r["motion_timeout_us"])):
        if canonical(previous[key]) != canonical(expected):
            reject("retained_motion_changed", key)


def compile_stationary(profile, request, *, now_home_us, previous=None, mode="simulation"):
    """Compile a detached immutable candidate under an explicitly fictional cut.

    The caller reserves IDs, independently admits authority, durably dispatches
    and handles exact retries. This pure function cannot supply those guarantees.
    ``physical`` always refuses in this source slice, including qualified data.
    """
    if mode != "simulation":
        reject("physical_admission_not_implemented")
    p = validate_profile(profile)
    integer(now_home_us, 0, MAX_U64, "now_home_us")
    _require_fictional(p, now_home_us)
    r = request
    exact(r, {"kind", "motion_id", "revision_id", "predecessor_id", "scope", "pins", "lineage",
              "context", "target_room_m", "speed_fixture_deg_s", "issued_home_us",
              "support_expiry_home_us", "original_caps", "motion_timeout_us", "current_axes",
              "assessment"}, "request")
    if r["kind"] not in ("MOVE", "LEVELS", "CONTINUE"):
        reject("unsupported_revision_kind")
    for key in ("motion_id", "revision_id"):
        integer(r[key], 1, MAX_U64, key)
    integer(r["predecessor_id"], 0, MAX_U64, "predecessor_id")
    for key in ("issued_home_us", "support_expiry_home_us"):
        integer(r[key], 0, MAX_U64, key)
    if not r["issued_home_us"] <= now_home_us < r["support_expiry_home_us"]:
        reject("support_not_current")
    validate_pins(r["pins"])
    if r["pins"] != p["pins"]:
        reject("profile_pins_mismatch")
    _scope(r["scope"], p["fixture_id"])
    _lineage(r["lineage"])
    _context(r["context"])
    _caps(r["original_caps"])
    b = p["bounds"]
    integer(r["motion_timeout_us"], 1, b["max_motion_us"], "motion_timeout_us")
    if b["arrival_bound_us"] + b["launch_skew_us"] > r["motion_timeout_us"]:
        reject("motion_timeout_understates_model")
    target = compile_target(p, r["target_room_m"], r["speed_fixture_deg_s"], now_home_us=now_home_us)
    if previous is not None:
        _previous(previous, p, r, now_home_us, target)
    elif r["kind"] != "MOVE" or r["predecessor_id"] != 0:
        reject("previous_draft_required")
    if r["kind"] == "MOVE":
        observation = _observation(p, r, now_home_us)
        start = observation["target_centideg"]
        if b["arrival_bound_us"] < _a4_minimum_arrival_us(p, start, target):
            reject("arrival_bound_below_a4_minimum")
    else:
        if r["current_axes"] is not None:
            reject("retained_motion_has_new_start")
        start = previous["start_centideg"]
    assessment = _assessment(p, r, target, now_home_us, start)
    ceiling = min(*r["original_caps"].values(), assessment["valid_until_home_us"],
                  *(entry["valid_until_home_us"] for entry in p["evidence"].values()),
                  r["issued_home_us"] + b["max_support_us"], MAX_U64)
    if r["support_expiry_home_us"] > ceiling:
        reject("support_exceeds_original_bounds")
    reserve = b["protective_reserve_us"] + b["clock_uncertainty_us"]
    if r["kind"] == "MOVE":
        reserve += sum(b[key] for key in ("darkness_decay_us", "driver_commit_us", "launch_skew_us",
                                          "arrival_bound_us", "settle_window_us"))
    if r["support_expiry_home_us"] - now_home_us <= reserve:
        reject("insufficient_remaining_service_lease")
    out = {"schema": DRAFT_SCHEMA, "version": 1, "status": "simulator_candidate",
           "physical_eligible": False, "profile_sha256": digest(p), **copy.deepcopy({
               key: r[key] for key in ("kind", "motion_id", "revision_id", "predecessor_id", "scope",
                                      "pins", "lineage", "context", "target_room_m", "issued_home_us",
                                      "support_expiry_home_us", "original_caps", "motion_timeout_us")}),
           "fixture_angles_deg": target["fixture_angles_deg"],
           "target_centideg": target["target_centideg"], "speed_deg_s": target["speed_deg_s"],
           "quantization_error_rad": target["quantization_error_rad"],
           "start_centideg": copy.deepcopy(start), "assessment_sha256": digest(assessment),
           "motion_origin_revision_id": r["revision_id"] if r["kind"] == "MOVE" else previous["motion_origin_revision_id"],
           "phase_policy": "dark_retarget" if r["kind"] == "MOVE" else "retain_motion"}
    out["draft_sha256"] = digest(out)
    return out
