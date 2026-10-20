"""Pure, simulation-only JOINT compiler for the pinned canonical C12 adapter.

The trusted consumer installs the original STATUS3/CAP2 decoder callbacks and
supplies its immutable current observer reference. Neither a typed reference nor
this detached draft creates authority. Home still owns clocks, currentness/CAS,
ID reservation, grants, dispatch and exact retries. No provider imports, codec,
scheduler, I/O or calibration application to command bytes lives here.
"""
from __future__ import annotations

import copy
import dataclasses
import hashlib
import math
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP

from fixture_execution import (CHECK_NAMES, FixtureExecutionRefusal, _a4_minimum_arrival_us,
                               _caps, _context, _lineage, _require_fictional, _scope)
from fixture_profile import (MAX_U64, canonical, digest, exact, identifier, integer,
                             number, sha256, validate_profile, vector)

PROFILE_SCHEMA = "home-fixture-joint-execution-profile-1"
TARGET_SCHEMA = "home-fixture-joint-target-draft-1"
DRAFT_SCHEMA = "home-fixture-joint-compiled-draft-1"
ASSESSMENT_METHOD = "fictional-joint-room-and-path-assessment-1"
RETAINED_REQUEST_SCHEMA = "home-fixture-retained-angle-request-2"
RETAINED_DRAFT_SCHEMA = "home-fixture-joint-compiled-draft-2"
RETAINED_ASSESSMENT_METHOD = "fictional-retained-angle-and-path-assessment-1"
ABI = "c12-canonical-capture-1"
COORDINATES = "joint-centidegrees-and-joint-degrees-per-second-to-canonical-arbiter-once"
PROVIDER_FIELDS = frozenset({"manifest_sha256", "source_sha256", "executable_sha256",
                              "configuration_sha256"})
WIRE_PINS = ("fixture", "firmware", "protocol", "profile", "calibration", "contract")
CURRENT_FIELDS = frozenset({"binding_sha256", "snapshot_sha256", "capabilities_sha256",
    "boot_generation", "session_id", "ownership_generation", "context_digest", "state_generation",
    "revision_id", "domain_generations", "captured_device_us", "device_now_lower_us",
    "device_now_upper_us", "maximum_status_age_us", "evidence_source"})
REQUEST_FIELDS = frozenset({"kind", "motion_id", "revision_id", "predecessor_id", "scope", "pins",
    "lineage", "context", "target_room_m", "speed_fixture_deg_s", "issued_home_us",
    "support_expiry_home_us", "original_caps", "motion_timeout_us", "assessment"})
DRAFT_FIELDS = frozenset({"schema", "version", "status", "physical_eligible", "authority_created",
    "coordinate_contract", "profile_sha256", "provider_pins", "kind", "motion_id", "revision_id",
    "predecessor_id", "scope", "pins", "lineage", "context", "target_room_m", "issued_home_us",
    "support_expiry_home_us", "original_caps", "motion_timeout_us", "fixture_angles_deg",
    "target_centideg", "speed_deg_s", "quantization_error_rad", "start_centideg",
    "assessment_sha256", "source_reference", "motion_origin_revision_id",
    "motion_origin_support_expiry_home_us", "motion_deadline_device_us", "motion_started_device_us",
    "motion_start_deadline_device_us", "phase_policy", "draft_sha256"})
RETAINED_REQUEST_FIELDS = (REQUEST_FIELDS - {"target_room_m"}) | {
    "schema", "version", "fixture_angles_deg", "retained_aim_ref"}
RETAINED_DRAFT_FIELDS = (DRAFT_FIELDS - {"target_room_m"}) | {"retained_aim_ref"}


def _retained_aim_ref(value):
    exact(value, {"room_commit_ref", "input_cut_digest", "motion_trace_sha256", "fixture_basis_sha256"}, "retained_aim_ref")
    commit = value["room_commit_ref"]
    exact(commit, {"configuration_digest", "manifest_digest", "worker_version", "room_output_schema",
                  "commit_id", "commit_body_sha256", "decision_sha256", "room_output_revision_id", "room_output_sha256"},
          "retained_room_commit_ref")
    if commit["worker_version"] != "home-live-context-worker-12.0.0" or commit["room_output_schema"] != "home-live-room-output-1":
        reject("retained_room_profile_required")
    for key in ("configuration_digest", "manifest_digest", "commit_body_sha256", "decision_sha256", "room_output_sha256"):
        sha256(commit[key], key)
    for key in ("commit_id", "room_output_revision_id"):
        identifier(commit[key], key)
    for key in ("input_cut_digest", "motion_trace_sha256", "fixture_basis_sha256"):
        sha256(value[key], key)


def retained_native_reference_digest(reference):
    """Original STATUS/CAP identity; only derived current-clock bounds advance."""
    return digest({k: v for k, v in reference.items() if k not in {'device_now_lower_us', 'device_now_upper_us'}})


def reject(code, detail=""):
    raise FixtureExecutionRefusal(code, detail)


def _uint_text(value, path):
    if type(value) is not str or not value.isascii() or not value.isdecimal() or str(int(value)) != value:
        reject("invalid_original_unsigned", path)
    return integer(int(value), 0, MAX_U64, path)


def _immutable_record(value, fields, path):
    # Installed canonical classes are frozen dataclasses; never accept a request
    # JSON object in place of the fixed observer port. This is not authentication.
    if not dataclasses.is_dataclass(value) or isinstance(value, type) or not value.__dataclass_params__.frozen:
        reject("trusted_immutable_reference_required", path)
    try:
        body = {key: getattr(value, key) for key in fields}
    except AttributeError as exc:
        raise FixtureExecutionRefusal("trusted_reference_fields_missing", path) from exc
    return body


def quantize_joint_target_centideg(motor, fixture_degrees):
    """Nearest representable JOINT centidegree, ties away from zero."""
    try:
        n, d = motor["joint_per_motor_num"], motor["joint_per_motor_den"]
        integer(n, 1, (1 << 32) - 1, "joint_per_motor_num")
        integer(d, 1, (1 << 32) - 1, "joint_per_motor_den")
        angle = Decimal(str(fixture_degrees))
        if not angle.is_finite():
            reject("nonfinite_joint_target")
        step = n // math.gcd(n, d)
        q = int((angle * 100 / step).to_integral_value(rounding=ROUND_HALF_UP)) * step
        return integer(q, -(1 << 31), (1 << 31) - 1, "joint_target")
    except (KeyError, TypeError, ArithmeticError) as exc:
        raise FixtureExecutionRefusal("invalid_joint_quantization_input") from exc


def validate_joint_profile(value, *, canonical_config, provider_pins):
    exact(value, {"schema", "version", "motor_profile", "canonical", "spotlight"}, "joint_profile")
    if value["schema"] != PROFILE_SCHEMA or type(value["version"]) is not int or value["version"] != 1:
        reject("joint_profile_version")
    p = validate_profile(value["motor_profile"])
    exact(provider_pins, PROVIDER_FIELDS, "provider_pins")
    for key in PROVIDER_FIELDS:
        sha256(provider_pins[key], key)
    exact(value["canonical"], PROVIDER_FIELDS | {"abi", "coordinate_contract"}, "canonical")
    if (value["canonical"]["abi"] != ABI or value["canonical"]["coordinate_contract"] != COORDINATES
            or any(value["canonical"][key] != provider_pins[key] for key in PROVIDER_FIELDS)):
        reject("canonical_provider_binding_mismatch")
    if (type(canonical_config) is not dict or canonical_config.get("schema") != "c12-canonical-capture-config-1"
            or type(canonical_config.get("version")) is not int or canonical_config["version"] != 1
            or digest(canonical_config) != provider_pins["configuration_sha256"]
            or canonical_config.get("coordinate_contract") != COORDINATES
            or canonical_config.get("evidence_source") != "simulated"
            or canonical_config.get("physical_permission") is not False
            or canonical_config.get("physical_truth") is not False):
        reject("canonical_configuration_mismatch")
    expected_spot = canonical_config.get("spotlight")
    exact(value["spotlight"], {"domain", "channels", "test_emitters", "input", "semantic_CCT_mapping",
                                "real_emitter_qualification"}, "spotlight")
    if (value["spotlight"] != expected_spot or value["spotlight"]["domain"] != 7
            or value["spotlight"]["channels"] != [21, 22, 23]
            or value["spotlight"]["input"] != "raw_logical_duty_only"
            or value["spotlight"]["semantic_CCT_mapping"] is not None
            or value["spotlight"]["real_emitter_qualification"] is not False):
        reject("spotlight_mapping_unavailable")
    motors = canonical_config.get("motors")
    if type(motors) is not list or len(motors) != 2 or p["axes"] is None:
        reject("canonical_motor_binding_missing")
    for index, (a, m) in enumerate(zip(p["axes"], motors)):
        exact(m, {"axis", "name", "request_id", "response_id", "direction", "joint_per_motor_num",
                  "joint_per_motor_den", "motor_zero_centideg"}, "motor")
        integer(m["axis"], index, index, "motor.axis")
        integer(m["joint_per_motor_num"], 1, (1 << 32) - 1, "joint_per_motor_num")
        integer(m["joint_per_motor_den"], 1, (1 << 32) - 1, "joint_per_motor_den")
        integer(m["direction"], -1, 1, "direction")
        if m["direction"] == 0:
            reject("invalid_motor_direction")
        integer(m["motor_zero_centideg"], -(1 << 31), (1 << 31) - 1, "motor_zero_centideg")
        ratio = a["units_per_fixture_degree"]
        if (a["name"].upper() != m["name"] or a["request_id"] != m["request_id"]
                or a["response_id"] != m["response_id"] or a["direction"] != m["direction"]
                or a["zero_centideg"] != m["motor_zero_centideg"]
                or ratio["numerator"] * m["joint_per_motor_num"]
                   != 100 * m["joint_per_motor_den"] * ratio["denominator"]):
            reject("motor_mapping_disagrees_with_canonical", str(index))
    return copy.deepcopy(value)


class JointFixtureCompiler:
    def __init__(self, profile, *, canonical_config, provider_pins, binding,
                 decode_status, decode_capabilities):
        self.profile = validate_joint_profile(profile, canonical_config=canonical_config,
                                              provider_pins=provider_pins)
        self.p = self.profile["motor_profile"]
        self.motors = copy.deepcopy(canonical_config["motors"])
        self.stationary_speed_centideg_s = integer(canonical_config.get("stationary_speed_centideg_s"),
                                                   0, (1 << 31) - 1, "stationary_speed_centideg_s")
        self.provider_pins = copy.deepcopy(provider_pins)
        if not callable(decode_status) or not callable(decode_capabilities):
            reject("fixed_original_decoders_required")
        _immutable_record(binding, {"binding_sha256", "boot_generation", "session_id",
                                   "ownership_generation", "context_digest"}, "binding")
        sha256(binding.binding_sha256, "binding_sha256")
        if type(binding.context_digest) is not bytes or len(binding.context_digest) != 32:
            reject("binding_context_digest")
        self.binding = binding
        self.decode_status = decode_status
        self.decode_capabilities = decode_capabilities

    def _raw_validation(self, targets, speeds):
        """Validation-only inverse. Returned values never become command fields."""
        raw_targets, raw_speeds = [], []
        for a, m, joint, speed in zip(self.p["axes"], self.motors, targets, speeds):
            n, d = m["joint_per_motor_num"], m["joint_per_motor_den"]
            if joint * d % n or speed * d % n:
                reject("joint_not_exactly_representable")
            raw = m["motor_zero_centideg"] + m["direction"] * joint * d // n
            motor_speed = speed * d // n
            integer(raw, a["minimum_centideg"], a["maximum_centideg"], "validation.raw_target")
            integer(motor_speed, 1, min(65535, a["max_speed_deg_s"]), "validation.raw_speed")
            raw_targets.append(raw)
            raw_speeds.append(motor_speed)
        return raw_targets, raw_speeds

    def compile_target_joint(self, target_room_m, speed_fixture_deg_s, *, now_home_us):
        integer(now_home_us, 0, MAX_U64, "now_home_us")
        _require_fictional(self.p, now_home_us)
        target, speeds = vector(target_room_m, 3, "target_room_m"), vector(speed_fixture_deg_s, 2, "speed_fixture_deg_s")
        g = self.p["geometry"]
        delta = [target[i] - g["optical_pivot_m"][i] for i in range(3)]
        distance = math.hypot(*delta)
        if not math.isfinite(distance) or distance <= max(g["position_uncertainty_m"], 1e-12):
            reject("target_intersects_optical_origin")
        basis = g["fixture_to_room_basis"]
        local = [sum(basis[j * 3 + i] * delta[j] for j in range(3)) for i in range(3)]
        horizontal = math.hypot(*local[:2])
        if horizontal <= 1e-12 * distance:
            reject("pan_direction_ambiguous")
        angles = [math.degrees(math.atan2(local[1], local[0])), math.degrees(math.atan2(horizontal, -local[2]))]
        targets, joint_speeds, errors = [], [], []
        for a, m, angle, speed in zip(self.p["axes"], self.motors, angles, speeds):
            if speed <= 0 or not a["fixture_min_deg"] <= angle <= a["fixture_max_deg"]:
                reject("fixture_target_or_speed_out_of_range", a["name"])
            n, d = m["joint_per_motor_num"], m["joint_per_motor_den"]
            step = n // math.gcd(n, d)
            q = quantize_joint_target_centideg(m, angle)
            # Integer JOINT degrees/s must also map exactly to integer motor dps.
            raw_cap_joint = Decimal(a["max_speed_deg_s"]) * n / d
            cap = min(Decimal(str(speed)), raw_cap_joint, Decimal(65535))
            joint_speed = int((cap / step).to_integral_value(rounding=ROUND_FLOOR)) * step
            if joint_speed <= 0:
                reject("speed_below_joint_resolution")
            if not a["fixture_min_deg"] <= q / 100 <= a["fixture_max_deg"]:
                reject("quantized_joint_target_out_of_range")
            targets.append(q)
            joint_speeds.append(joint_speed)
            errors.append(abs(math.radians(q / 100 - angle)))
        self._raw_validation(targets, joint_speeds)
        error = sum(errors)
        if error > self.p["bounds"]["max_quantization_error_rad"] + 1e-15:
            reject("quantization_exceeds_bound")
        return {"schema": TARGET_SCHEMA, "version": 1, "physical_eligible": False,
                "coordinate_contract": COORDINATES, "profile_sha256": digest(self.profile),
                "target_room_m": copy.deepcopy(target_room_m), "fixture_angles_deg": angles,
                "target_centideg": targets, "speed_deg_s": joint_speeds,
                "quantization_error_rad": error, "range_m": distance}

    def _current(self, ref, raw_status, raw_capabilities):
        body = _immutable_record(ref, CURRENT_FIELDS, "current_ref")
        for key in ("binding_sha256", "snapshot_sha256", "capabilities_sha256", "context_digest"):
            sha256(body[key], key)
        for key in ("boot_generation", "session_id", "ownership_generation"):
            integer(body[key], 1, MAX_U64, key)
            if body[key] != getattr(self.binding, key):
                reject("current_binding_generation_mismatch", key)
        for key in ("state_generation", "revision_id", "captured_device_us", "device_now_lower_us", "device_now_upper_us"):
            integer(body[key], 0, MAX_U64, key)
        integer(body["maximum_status_age_us"], 1, 5_000_000, "maximum_status_age_us")
        if (body["binding_sha256"] != self.binding.binding_sha256
                or body["context_digest"] != self.binding.context_digest.hex()
                or body["evidence_source"] != "simulated"):
            reject("current_binding_or_source_mismatch")
        if type(body["domain_generations"]) is not tuple or len(body["domain_generations"]) != 8:
            reject("current_domain_generations")
        for value in body["domain_generations"]:
            integer(value, 0, (1 << 32) - 1, "domain_generation")
        low, high, capture = body["device_now_lower_us"], body["device_now_upper_us"], body["captured_device_us"]
        b = self.p["bounds"]
        if (low > high or high - low > 2 * b["clock_uncertainty_us"] or capture > high
                or high - capture >= min(body["maximum_status_age_us"], b["max_observation_age_us"])):
            reject("current_clock_or_status_stale")
        if type(raw_status) is not bytes or len(raw_status) != 998 or type(raw_capabilities) is not bytes or len(raw_capabilities) != 404:
            reject("original_status3_cap2_bytes_required")
        if (hashlib.sha256(raw_status).hexdigest() != body["snapshot_sha256"]
                or hashlib.sha256(raw_capabilities).hexdigest() != body["capabilities_sha256"]):
            reject("current_original_digest_mismatch")
        try:
            status = self.decode_status(raw_status, binding=self.binding, captured_device_us=capture)
            cap = self.decode_capabilities(raw_capabilities, {key: self.p["pins"][key] for key in WIRE_PINS})
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            raise FixtureExecutionRefusal("original_decoder_refused") from exc
        if (status.get("schema") != "c12-runtime-decoded-snapshot-1" or status.get("document_version") != 3
                or cap.get("schema") != "c12-runtime-capabilities-1" or cap.get("document_version") != 2
                or status.get("original_document_hex") != raw_status.hex()
                or status.get("original_document_sha256") != body["snapshot_sha256"]
                or status.get("captured_device_us") != str(capture)):
            reject("original_decoded_binding_mismatch")
        for key in ("boot_generation", "session_id", "ownership_generation", "state_generation", "revision_id"):
            if status.get(key) != str(body[key]):
                reject("current_original_generation_mismatch", key)
        expected_pins = {key: self.p["pins"][key] for key in WIRE_PINS}
        if (status.get("context_digest") != body["context_digest"] or status.get("pins") != expected_pins
                or cap.get("pins") != expected_pins
                or tuple(d["generation"] for d in status.get("domains", [])) != body["domain_generations"]
                or status.get("session_open") is not True or status.get("thermal_latched") is not False
                or status.get("simulated") is not True or status.get("physical_permission") is not False
                or cap.get("physical_permission") is not False):
            reject("current_original_context_refusal")
        integer(cap.get("qualified_mask"), 0, 255, "qualified_mask")
        # Canonical source bits: spotlight=2,motion=4,thermal=8,darkness=16,
        # protective=32,V4.2 allowlist=128. These are fictional model flags only.
        if cap["qualified_mask"] & 0xBE != 0xBE or cap.get("protocol") != 1:
            reject("canonical_task_profile_unavailable")
        driver = _uint_text(cap.get("max_driver_commit_us"), "max_driver_commit_us")
        if not driver or driver > b["driver_commit_us"]:
            reject("profile_understates_canonical_driver_bound")
        if type(cap.get("axes")) is not list or len(cap["axes"]) != 2:
            reject("capability_axes_missing")
        for a, c in zip(self.p["axes"], cap["axes"]):
            if (c["request_id"] != a["request_id"] or c["response_id"] != a["response_id"]
                    or c["minimum_centideg"] > c["maximum_centideg"]):
                reject("capability_motor_binding_mismatch")
        return status, cap, body

    def _assessment(self, r, target, start, reference, now_home_us):
        a = r["assessment"]
        exact(a, {"status", "assessment_id", "method", "profile_sha256", "pins", "lineage",
                  "target_room_m", "target_centideg", "start_centideg", "current_reference_sha256",
                  "motion_id", "checks", "valid_from_home_us", "valid_until_home_us", "original_caps",
                  "target_uncertainty_m", "total_aim_error_bound_rad"}, "joint_assessment")
        identifier(a["assessment_id"], "assessment_id")
        if a["status"] != "fictional" or a["method"] != ASSESSMENT_METHOD:
            reject("joint_assessment_not_fictional")
        expected = {"profile_sha256": digest(self.profile), "pins": r["pins"], "lineage": r["lineage"],
                    "target_room_m": r["target_room_m"], "target_centideg": target["target_centideg"],
                    "start_centideg": start, "current_reference_sha256": digest(reference),
                    "motion_id": r["motion_id"], "original_caps": r["original_caps"]}
        for key, value in expected.items():
            if canonical(a[key]) != canonical(value):
                reject("joint_assessment_binding_mismatch", key)
        exact(a["checks"], CHECK_NAMES, "checks")
        if any(value is not True for value in a["checks"].values()):
            reject("unsupported_geometry_or_path")
        integer(a["valid_from_home_us"], 0, MAX_U64, "valid_from_home_us")
        integer(a["valid_until_home_us"], 1, MAX_U64, "valid_until_home_us")
        if not a["valid_from_home_us"] <= r["issued_home_us"] <= now_home_us < a["valid_until_home_us"]:
            reject("assessment_not_current")
        number(a["target_uncertainty_m"], "target_uncertainty_m", 0)
        combined = self.p["geometry"]["position_uncertainty_m"] + a["target_uncertainty_m"]
        if combined >= target["range_m"]:
            reject("unbounded_aim_uncertainty")
        required = (math.asin(combined / target["range_m"]) + self.p["geometry"]["orientation_uncertainty_rad"]
                    + self.p["bounds"]["tracking_error_rad"] + target["quantization_error_rad"])
        number(a["total_aim_error_bound_rad"], "total_aim_error_bound_rad", 0, math.pi)
        if a["total_aim_error_bound_rad"] + 1e-15 < required:
            reject("assessment_understates_uncertainty")
        return a

    def compile_stationary_joint(self, request, *, now_home_us, current_ref, raw_status_v3,
                                 raw_capabilities_v2, previous=None):
        return self._compile_stationary_joint(request, now_home_us=now_home_us, current_ref=current_ref,
            raw_status_v3=raw_status_v3, raw_capabilities_v2=raw_capabilities_v2, previous=previous, retained=False)

    def compile_retained_target_joint(self, fixture_angles_deg, speed_fixture_deg_s, *, now_home_us):
        """Quantize an original committed endpoint; never reconstruct a room point."""
        integer(now_home_us, 0, MAX_U64, "now_home_us")
        _require_fictional(self.p, now_home_us)
        angles = vector(fixture_angles_deg, 2, "fixture_angles_deg")
        speeds = vector(speed_fixture_deg_s, 2, "speed_fixture_deg_s")
        targets, joint_speeds, errors = [], [], []
        for a, m, angle, speed in zip(self.p["axes"], self.motors, angles, speeds):
            if speed <= 0 or not a["fixture_min_deg"] <= angle <= a["fixture_max_deg"]:
                reject("fixture_target_or_speed_out_of_range", a["name"])
            n, d = m["joint_per_motor_num"], m["joint_per_motor_den"]
            step = n // math.gcd(n, d)
            q = quantize_joint_target_centideg(m, angle)
            cap = min(Decimal(str(speed)), Decimal(a["max_speed_deg_s"]) * n / d, Decimal(65535))
            joint_speed = int((cap / step).to_integral_value(rounding=ROUND_FLOOR)) * step
            if joint_speed <= 0:
                reject("speed_below_joint_resolution")
            if not a["fixture_min_deg"] <= q / 100 <= a["fixture_max_deg"]:
                reject("quantized_joint_target_out_of_range")
            targets.append(q); joint_speeds.append(joint_speed)
            errors.append(abs(math.radians(q / 100 - angle)))
        self._raw_validation(targets, joint_speeds)
        error = sum(errors)
        if error > self.p["bounds"]["max_quantization_error_rad"] + 1e-15:
            reject("quantization_exceeds_bound")
        return {"schema": "home-fixture-retained-angle-target-draft-1", "version": 1,
                "physical_eligible": False, "coordinate_contract": COORDINATES,
                "profile_sha256": digest(self.profile), "fixture_angles_deg": angles,
                "target_centideg": targets, "speed_deg_s": joint_speeds, "quantization_error_rad": error}

    def _retained_assessment(self, r, target, start, reference, now_home_us):
        a = r["assessment"]
        exact(a, {"status", "assessment_id", "method", "profile_sha256", "pins", "lineage",
                  "fixture_angles_deg", "retained_aim_ref", "target_centideg", "start_centideg", "current_reference_sha256",
                  "motion_id", "checks", "valid_from_home_us", "valid_until_home_us", "original_caps",
                  "minimum_work_range_m", "target_uncertainty_m", "total_aim_error_bound_rad"}, "retained_joint_assessment")
        identifier(a["assessment_id"], "assessment_id")
        if a["status"] != "fictional" or a["method"] != RETAINED_ASSESSMENT_METHOD:
            reject("retained_joint_assessment_not_fictional")
        expected = {"profile_sha256": digest(self.profile), "pins": r["pins"], "lineage": r["lineage"],
                    "fixture_angles_deg": r["fixture_angles_deg"], "retained_aim_ref": r["retained_aim_ref"],
                    "target_centideg": target["target_centideg"], "start_centideg": start,
                    "current_reference_sha256": retained_native_reference_digest(reference),
                    "motion_id": r["motion_id"], "original_caps": r["original_caps"]}
        for key, value in expected.items():
            if canonical(a[key]) != canonical(value): reject("retained_joint_assessment_binding_mismatch", key)
        exact(a["checks"], CHECK_NAMES, "checks")
        if any(value is not True for value in a["checks"].values()): reject("unsupported_geometry_or_path")
        integer(a["valid_from_home_us"], 0, MAX_U64, "valid_from_home_us")
        integer(a["valid_until_home_us"], 1, MAX_U64, "valid_until_home_us")
        if not a["valid_from_home_us"] <= r["issued_home_us"] <= now_home_us < a["valid_until_home_us"]:
            reject("assessment_not_current")
        number(a["minimum_work_range_m"], "minimum_work_range_m", 0)
        number(a["target_uncertainty_m"], "target_uncertainty_m", 0)
        combined = self.p["geometry"]["position_uncertainty_m"] + a["target_uncertainty_m"]
        if combined >= a["minimum_work_range_m"]: reject("unbounded_aim_uncertainty")
        required = (math.asin(combined / a["minimum_work_range_m"]) + self.p["geometry"]["orientation_uncertainty_rad"]
                    + self.p["bounds"]["tracking_error_rad"] + target["quantization_error_rad"])
        number(a["total_aim_error_bound_rad"], "total_aim_error_bound_rad", 0, math.pi)
        if a["total_aim_error_bound_rad"] + 1e-15 < required: reject("assessment_understates_uncertainty")
        return a

    def compile_retained_stationary_joint(self, request, *, now_home_us, current_ref, raw_status_v3,
                                         raw_capabilities_v2, previous=None):
        return self._compile_stationary_joint(request, now_home_us=now_home_us, current_ref=current_ref,
            raw_status_v3=raw_status_v3, raw_capabilities_v2=raw_capabilities_v2, previous=previous, retained=True)

    def _compile_stationary_joint(self, request, *, now_home_us, current_ref, raw_status_v3,
                                  raw_capabilities_v2, previous, retained):
        integer(now_home_us, 0, MAX_U64, "now_home_us")
        _require_fictional(self.p, now_home_us)
        r = copy.deepcopy(request)
        exact(r, RETAINED_REQUEST_FIELDS if retained else REQUEST_FIELDS, "joint_request")
        if retained:
            if r["schema"] != RETAINED_REQUEST_SCHEMA or type(r["version"]) is not int or r["version"] != 2:
                reject("retained_joint_request_version")
            _retained_aim_ref(r["retained_aim_ref"])
        if r["kind"] not in ("MOVE", "LEVELS", "CONTINUE"):
            reject("unsupported_revision_kind")
        for key in ("motion_id", "revision_id"):
            integer(r[key], 1, MAX_U64, key)
        integer(r["predecessor_id"], 0, MAX_U64, "predecessor_id")
        for key in ("issued_home_us", "support_expiry_home_us"):
            integer(r[key], 0, MAX_U64, key)
        if not r["issued_home_us"] <= now_home_us < r["support_expiry_home_us"]:
            reject("support_not_current")
        if r["pins"] != self.p["pins"]:
            reject("profile_pins_mismatch")
        _scope(r["scope"], self.p["fixture_id"])
        _lineage(r["lineage"])
        _context(r["context"])
        _caps(r["original_caps"])
        status, cap, reference = self._current(current_ref, raw_status_v3, raw_capabilities_v2)
        if any(r["context"][key] != reference[key] for key in r["context"]):
            reject("request_context_mismatch")
        if r["predecessor_id"] != reference["revision_id"] or r["revision_id"] <= reference["revision_id"]:
            reject("actual_predecessor_required")
        b = self.p["bounds"]
        integer(r["motion_timeout_us"], 1, min(b["max_motion_us"], _uint_text(cap["max_motion_us"], "max_motion_us")), "motion_timeout_us")
        if b["arrival_bound_us"] + b["launch_skew_us"] > r["motion_timeout_us"]:
            reject("motion_timeout_understates_model")
        target = (self.compile_retained_target_joint(r["fixture_angles_deg"], r["speed_fixture_deg_s"], now_home_us=now_home_us)
                  if retained else self.compile_target_joint(r["target_room_m"], r["speed_fixture_deg_s"], now_home_us=now_home_us))
        for a, c, angle, q, speed in zip(self.p["axes"], cap["axes"], target["fixture_angles_deg"], target["target_centideg"], target["speed_deg_s"]):
            integer(q, c["minimum_centideg"], c["maximum_centideg"], "capability.joint_target")
            integer(speed, 1, c["max_speed_deg_s"], "capability.joint_speed")
        prior = None
        if previous is not None:
            exact(previous, RETAINED_DRAFT_FIELDS if retained else DRAFT_FIELDS, "previous_joint_draft")
            if (previous["schema"] != (RETAINED_DRAFT_SCHEMA if retained else DRAFT_SCHEMA)
                    or type(previous["version"]) is not int or previous["version"] != (2 if retained else 1)
                    or previous["status"] != "simulator_candidate" or previous["physical_eligible"] is not False
                    or previous["authority_created"] is not False or previous["coordinate_contract"] != COORDINATES
                    or previous["draft_sha256"] != digest({k: v for k, v in previous.items() if k != "draft_sha256"})):
                reject("previous_joint_draft_changed")
            for key, value in (("profile_sha256", digest(self.profile)), ("scope", r["scope"]),
                               ("context", r["context"]), ("pins", r["pins"]), ("provider_pins", self.provider_pins)):
                if canonical(previous[key]) != canonical(value):
                    reject("previous_context_mismatch", key)
            for key in ("motion_id", "revision_id", "motion_origin_revision_id"):
                integer(previous[key], 1, MAX_U64, "previous." + key)
            for key in ("issued_home_us", "support_expiry_home_us", "motion_origin_support_expiry_home_us"):
                integer(previous[key], 0, MAX_U64, "previous." + key)
            if not previous["issued_home_us"] <= r["issued_home_us"]:
                reject("previous_revision_time_reversed")
            if not previous["revision_id"] <= reference["revision_id"]:
                reject("previous_task_newer_than_device")
            prior = previous
        actual_motion = _uint_text(status["motion_id"], "motion_id")
        if r["kind"] == "MOVE":
            # Pinned native submit/validateMove: every active-task phase is
            # Busy, Travel rejects all plans, and a previously launched motor
            # requires a completed protective stop. STATUS3 does not expose
            # task_active/motor_ever_sent, so use the conservative visible
            # invariant: Idle and either confirmed stop or no launch times.
            # A last nonfatal refusal/result is historical, not a task flag.
            if status.get("phase") != "idle":
                reject("current_move_phase_unavailable")
            prior_started = _uint_text(status["motion_started_us"], "motion_started_us")
            prior_deadline = _uint_text(status["motion_deadline_us"], "motion_deadline_us")
            no_launch = prior_started == 0 and prior_deadline == 0
            if status.get("stop_confirmed") is not True and not no_launch:
                reject("current_move_stop_unconfirmed")
            if r["motion_id"] == actual_motion or (prior is not None and r["motion_id"] == prior["motion_id"]):
                reject("move_reuses_motion_id")
            axes = status.get("axis_observations")
            if type(axes) is not list or len(axes) != 2:
                reject("current_stationary_axes_unavailable")
            start = []
            for i, (a, o, c) in enumerate(zip(self.p["axes"], axes, cap["axes"])):
                if (o.get("axis") != i or o.get("opcode") != 0x92 or o.get("matched") is not True
                        or o.get("stationary") is not True):
                    reject("current_stationary_axes_unavailable")
                sampled = _uint_text(o["sampled_device_us"], "sampled_device_us")
                received = _uint_text(o["received_device_us"], "received_device_us")
                if not sampled <= received <= reference["captured_device_us"] or reference["device_now_upper_us"] - sampled >= b["max_observation_age_us"]:
                    reject("stale_axis_original")
                integer(o.get("speed_centideg_s"), -(1 << 31), (1 << 31) - 1, "joint_observed_speed")
                if abs(o["speed_centideg_s"]) > self.stationary_speed_centideg_s:
                    reject("current_stationary_speed_mismatch")
                # Before the first motor launch the native engine accepts
                # fresh feedback without a task generation; cancellation can
                # leave a nonzero admitted motion ID with no launch at all.
                permitted_generations = {status["motion_id"]}
                if no_launch and status.get("stop_confirmed") is not True:
                    permitted_generations.add("0")
                if o["motion_generation"] not in permitted_generations:
                    reject("axis_motion_generation_mismatch")
                integer(o["angle_centideg"], c["minimum_centideg"], c["maximum_centideg"], "joint_start")
                start.append(o["angle_centideg"])
            raw_start, _ = self._raw_validation(start, target["speed_deg_s"])
            raw_target, raw_speed = self._raw_validation(target["target_centideg"], target["speed_deg_s"])
            if b["arrival_bound_us"] < _a4_minimum_arrival_us(self.p, raw_start, {"target_centideg": raw_target, "speed_deg_s": raw_speed}):
                reject("arrival_bound_below_a4_minimum")
            origin_revision, origin_support = r["revision_id"], r["support_expiry_home_us"]
            motion_deadline = None  # Actual device deadline first appears in later STATUS.
            motion_started = None
            motion_start_deadline = None
        else:
            if prior is None:
                reject("previous_task_required")
            if now_home_us >= prior["support_expiry_home_us"]:
                reject("expired_motion_cannot_resume")
            for key in ("scene_id", "episode_id", "example_id"):
                if prior["lineage"][key] != r["lineage"][key]:
                    reject("retained_scene_mismatch")
            semantic = status["current_semantic_target"]
            for key, expected in (("motion_id", r["motion_id"]),
                                  ("fixture_angles_deg", r["fixture_angles_deg"]) if retained else ("target_room_m", r["target_room_m"]),
                                  ("target_centideg", target["target_centideg"]), ("speed_deg_s", target["speed_deg_s"]),
                                  ("motion_timeout_us", r["motion_timeout_us"])):
                if canonical(prior[key]) != canonical(expected):
                    reject("retained_motion_changed", key)
            if retained and any(prior["retained_aim_ref"][k] != r["retained_aim_ref"][k]
                                for k in ("motion_trace_sha256", "fixture_basis_sha256")):
                reject("retained_motion_changed", "retained_aim_ref")
            if (actual_motion != r["motion_id"] or semantic["target_centideg"] != target["target_centideg"]
                    or semantic["speed_deg_s"] != target["speed_deg_s"]
                    or semantic["motion_timeout_us"] != str(r["motion_timeout_us"])):
                reject("actual_retained_motion_changed")
            deadline = _uint_text(status["motion_deadline_us"], "motion_deadline_us")
            started = _uint_text(status["motion_started_us"], "motion_started_us")
            start_deadline = _uint_text(semantic["start_deadline_device_us"], "start_deadline_device_us")
            phase = status.get("phase")
            if phase not in ("darkening", "decaying", "launch_pan", "launch_tilt", "moving", "settling", "illuminating", "lit"):
                reject("retained_motion_phase_unavailable")
            if phase in ("illuminating", "lit") and status.get("mechanical_settled") is not True:
                reject("retained_settlement_unavailable")
            if phase in ("moving", "settling") and (not started or reference["device_now_upper_us"] >= deadline):
                reject("expired_device_motion_cannot_resume")
            if deadline and (not started or started >= deadline or started > reference["device_now_lower_us"]):
                reject("retained_motion_original_times_invalid")
            if phase in ("illuminating", "lit") and not deadline:
                reject("retained_motion_original_times_invalid")
            if prior["motion_deadline_device_us"] is not None and deadline != prior["motion_deadline_device_us"]:
                reject("retained_device_motion_deadline_changed")
            if prior["motion_started_device_us"] is not None and started != prior["motion_started_device_us"]:
                reject("retained_device_motion_start_changed")
            if prior["motion_start_deadline_device_us"] is not None and start_deadline != prior["motion_start_deadline_device_us"]:
                reject("retained_device_start_deadline_changed")
            actual_support = _uint_text(status["support_expiry_home_us"], "support_expiry_home_us")
            if actual_support <= now_home_us:
                reject("actual_motion_support_expired")
            if actual_support != prior["support_expiry_home_us"]:
                reject("actual_retained_support_changed")
            start = copy.deepcopy(prior["start_centideg"])
            origin_revision = prior["motion_origin_revision_id"]
            origin_support = prior["motion_origin_support_expiry_home_us"]
            motion_deadline = deadline or None
            motion_started = started or None
            motion_start_deadline = start_deadline
        assessment = (self._retained_assessment(r, target, start, reference, now_home_us) if retained
                      else self._assessment(r, target, start, reference, now_home_us))
        margin = math.degrees(assessment["total_aim_error_bound_rad"])
        raw_targets, _ = self._raw_validation(target["target_centideg"], target["speed_deg_s"])
        for a, m, c, angle, q, raw in zip(self.p["axes"], self.motors, cap["axes"], target["fixture_angles_deg"], target["target_centideg"], raw_targets):
            raw_margin = math.ceil(margin * 100 * m["joint_per_motor_den"] / m["joint_per_motor_num"])
            joint_margin = math.ceil(margin * 100)
            if (angle - margin < a["fixture_min_deg"] or angle + margin > a["fixture_max_deg"]
                    or raw - raw_margin < a["minimum_centideg"] or raw + raw_margin > a["maximum_centideg"]
                    or q - joint_margin < c["minimum_centideg"] or q + joint_margin > c["maximum_centideg"]):
                reject("uncertainty_exceeds_mechanical_limits")
        ceiling = min(*r["original_caps"].values(), assessment["valid_until_home_us"],
                      *(e["valid_until_home_us"] for e in self.p["evidence"].values()),
                      r["issued_home_us"] + b["max_support_us"], MAX_U64)
        if r["support_expiry_home_us"] > ceiling:
            reject("support_exceeds_original_bounds")
        reserve = b["protective_reserve_us"] + b["clock_uncertainty_us"]
        if r["kind"] == "MOVE":
            reserve += sum(b[key] for key in ("darkness_decay_us", "driver_commit_us", "launch_skew_us", "arrival_bound_us", "settle_window_us"))
        if r["support_expiry_home_us"] - now_home_us <= reserve:
            reject("insufficient_remaining_service_lease")
        out = {"schema": RETAINED_DRAFT_SCHEMA if retained else DRAFT_SCHEMA, "version": 2 if retained else 1,
               "status": "simulator_candidate", "physical_eligible": False,
               "authority_created": False, "coordinate_contract": COORDINATES, "profile_sha256": digest(self.profile),
               "provider_pins": copy.deepcopy(self.provider_pins),
               **{k: r[k] for k in ("kind", "motion_id", "revision_id", "predecessor_id", "scope", "pins", "lineage", "context",
                   "issued_home_us", "support_expiry_home_us", "original_caps", "motion_timeout_us")},
               **({"retained_aim_ref": copy.deepcopy(r["retained_aim_ref"])} if retained else {"target_room_m": r["target_room_m"]}),
               "fixture_angles_deg": target["fixture_angles_deg"], "target_centideg": target["target_centideg"],
               "speed_deg_s": target["speed_deg_s"], "quantization_error_rad": target["quantization_error_rad"],
               "start_centideg": start, "assessment_sha256": digest(assessment), "source_reference": reference,
               "motion_origin_revision_id": origin_revision, "motion_origin_support_expiry_home_us": origin_support,
               "motion_deadline_device_us": motion_deadline, "motion_started_device_us": motion_started,
               "motion_start_deadline_device_us": motion_start_deadline,
               "phase_policy": "dark_retarget" if r["kind"] == "MOVE" else "retain_motion"}
        out["draft_sha256"] = digest(out)
        return copy.deepcopy(out)

    def validate_raw_spotlight(self, duties, *, current_ref, raw_status_v3, raw_capabilities_v2):
        """Explicit test duties only. No semantic CCT/spectrum/photometry claim."""
        status, cap, reference = self._current(current_ref, raw_status_v3, raw_capabilities_v2)
        if type(duties) is not list or len(duties) != 3:
            reject("explicit_raw_spotlight_duties_required")
        frame = copy.deepcopy(status["requested"])
        if type(frame) is not list or len(frame) != 24:
            reject("current_lighting_frame_unavailable")
        frame[21:24] = duties
        if len(cap["maximum_duty"]) != 24 or len(cap["full_channel_milliwatt"]) != 24:
            reject("lighting_capability_incomplete")
        for i, duty in enumerate(frame):
            integer(duty, 0, min(4096, cap["maximum_duty"][i]), "raw_duty")
            integer(cap["full_channel_milliwatt"][i], 0, (1 << 32) - 1, "channel_power")
            if duty and cap["full_channel_milliwatt"][i] == 0:
                reject("active_channel_power_unknown")
        average = sum(d * p for d, p in zip(frame, cap["full_channel_milliwatt"]))
        instant = 0
        for zone in range(7):
            indices = range(zone * 3, zone * 3 + 3)
            if sum(frame[i] for i in indices) > 4096:
                reject("ambient_pwm_overlap")
            instant += max((cap["full_channel_milliwatt"][i] for i in indices if frame[i]), default=0)
        instant += sum(cap["full_channel_milliwatt"][i] for i in range(21, 24) if frame[i])
        if ((average + 4095) // 4096 > cap["max_average_lighting_milliwatt"]
                or instant > cap["max_instant_lighting_milliwatt"]):
            reject("raw_lighting_power_limit")
        return {"schema": "home-fixture-raw-spotlight-test-1", "version": 1, "physical_eligible": False,
                "authority_created": False, "input": "raw_logical_duty_only", "duties": copy.deepcopy(duties),
                "semantic_CCT_mapping": None, "real_emitter_qualification": False,
                "profile_sha256": digest(self.profile), "current_reference_sha256": digest(reference)}

    def compile_semantic_spotlight(self, *_args, **_kwargs):
        reject("semantic_spotlight_cct_mapping_unavailable")
