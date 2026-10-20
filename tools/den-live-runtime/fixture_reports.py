"""Bounded original execution reports; no report is physical-light proof.

The module carries original bytes and first receipt clocks. It neither predicts
device state nor turns a transport/driver acknowledgement into an outcome.
"""
from __future__ import annotations

import hashlib
import json


class FixtureRuntimeRefusal(ValueError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def require(condition, reason):
    if not condition:
        raise FixtureRuntimeRefusal(reason)


def canonical_bytes(value):
    """Private storage encoding, also compatible with canonical grant JSON.

    This is not a second wire codec. Production callers supply the agreed
    contract codec and admitted-grant validator to the dispatcher.
    """
    def check(item, depth=0):
        require(depth <= 12, "json_depth_bound")
        if item is None or type(item) in (str, bool):
            return
        if type(item) is int:
            require(-(1 << 63) <= item < 1 << 64, "json_integer_bound")
            return
        if type(item) is list:
            require(len(item) <= 256, "json_array_bound")
            for child in item:
                check(child, depth + 1)
            return
        if type(item) is dict:
            require(len(item) <= 128 and all(type(key) is str for key in item), "json_object_bound")
            for child in item.values():
                check(child, depth + 1)
            return
        raise FixtureRuntimeRefusal("json_noncanonical_value")
    check(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")


def parse_original(raw, *, max_bytes=65536):
    require(type(raw) is bytes and 0 < len(raw) <= max_bytes, "original_body_size_bound")
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "original_duplicate_key")
            result[key] = value
        return result
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(FixtureRuntimeRefusal("original_nonfinite")))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise FixtureRuntimeRefusal("original_json_invalid") from exc
    canonical_bytes(value)
    return value


def decimal_u64(value):
    require(type(value) is str and value.isascii() and value.isdecimal()
            and (value == "0" or not value.startswith("0")), "canonical_u64_required")
    result = int(value)
    require(result < 1 << 64, "u64_bound")
    return result


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def digest(value):
    require(type(value) is str and len(value) == 64
            and all(char in "0123456789abcdef" for char in value), "digest_required")
    return value


SCOPE_FIELDS = {"fixture_id", "device_id", "boot_generation", "session_id", "context_digest",
                "ownership_generation", "source_generation", "authority_generation", "domain_generations", "pins"}


def validate_scope(scope):
    require(type(scope) is dict and set(scope) == SCOPE_FIELDS, "fixed_scope_fields_required")
    require(type(scope["fixture_id"]) is str and 0 < len(scope["fixture_id"]) <= 128, "fixture_identity_required")
    require(type(scope["device_id"]) is str and len(scope["device_id"]) == 32
            and all(char in "0123456789abcdef" for char in scope["device_id"]), "device_identity_required")
    for name in ("boot_generation", "session_id", "ownership_generation", "source_generation", "authority_generation"):
        require(decimal_u64(scope[name]) > 0, "nonzero_scope_generation_required")
    digest(scope["context_digest"])
    require(type(scope["domain_generations"]) is list and len(scope["domain_generations"]) == 8
            and all(type(value) is int and 0 <= value < 1 << 32 for value in scope["domain_generations"]),
            "domain_generation_scope_required")
    require(type(scope["pins"]) is dict and 0 < len(scope["pins"]) <= 16, "fixed_pins_required")
    for key, value in scope["pins"].items():
        require(type(key) is str and 0 < len(key) <= 64, "pin_name_required")
        digest(value)
    require(len(canonical_bytes(scope)) <= 4096, "scope_size_bound")
    return scope


REPORT_CATEGORIES = {"transport", "admission", "axis_state", "settling", "led_registers", "protective", "unknown"}
NATIVE_FIELDS = {"schema","version","event","result","phase","reported_device_us","sampled_device_us","received_device_us",
                 "revision_id","revision_sha256","session_context_sha256","motion_id","state_generation","effect_generation",
                 "axis","value","frame","evidence_source","physical_pass"}
NATIVE_PHASES = frozenset("inhibited idle darkening decaying launch_pan launch_tilt moving settling illuminating lit protecting unconfirmed fault travel".split())
NATIVE_RESULTS = frozenset("ok duplicate changed_duplicate capability_denied invalid_profile context_mismatch no_session invalid_clock clock_rollback invalid_plan stale_revision predecessor_mismatch cas_mismatch ownership invalid_frame mixer_mismatch power_limit unsupported expired reserve_exceeded busy stale_angles not_stationary thermal_missing thermal_trip thermal_stale thermal_latched driver_rejected driver_uncertain can_rejected can_uncertain motion_timeout observation_rejected stop_unconfirmed event_overflow travel revoked manual_lock invalid_manual_duration".split())
NATIVE_EVENTS = frozenset("session_opened admitted duplicate refused lighting_requested lighting_verified can_submitted controller_completed motor_reply axis_observed settling settled phase_changed withdrawn ambient_retained manual_expired thermal_derated thermal_fault protective_submitted unconfirmed fault clock_mapped".split())


def validate_native_event(body, scope, admitted=None):
    """Validate the pinned native event shape, not just a claimed category.

    This is report parsing, not a duplicate firmware state machine. Real current
    journal admission must also supply its independently admitted original plan.
    """
    validate_scope(scope)
    require(type(body) is dict and set(body) == NATIVE_FIELDS, "native_report_fields_invalid")
    require(body["schema"] == "home-fixture-execution-report-1" and type(body["version"]) is int and body["version"] == 1
            and body["evidence_source"] == "simulated" and body["physical_pass"] is False, "native_report_provenance_invalid")
    require(body["event"] in NATIVE_EVENTS and body["result"] in NATIVE_RESULTS and body["phase"] in NATIVE_PHASES,
            "native_report_enum_invalid")
    require(digest(body["session_context_sha256"]) == scope["context_digest"], "native_report_context_mismatch")
    for name in ("reported_device_us","revision_id","motion_id","state_generation","effect_generation"):
        decimal_u64(body[name])
    for name in ("sampled_device_us","received_device_us"):
        if body[name] is not None:
            require(decimal_u64(body[name]) <= decimal_u64(body["reported_device_us"]), "native_report_clock_order")
    if body["sampled_device_us"] is not None and body["received_device_us"] is not None:
        require(decimal_u64(body["sampled_device_us"]) <= decimal_u64(body["received_device_us"]), "native_report_sample_receipt_order")
    if body["revision_sha256"] is not None:
        digest(body["revision_sha256"])
    require((decimal_u64(body["revision_id"]) == 0) == (body["revision_sha256"] is None), "native_report_revision_digest_missing")
    require(type(body["axis"]) is int and body["axis"] in (0,1,255), "native_report_axis_invalid")
    require(type(body["value"]) is int and -(1<<31) <= body["value"] < 1<<31, "native_report_value_invalid")
    require(type(body["frame"]) is list and len(body["frame"]) == 24 and all(type(value) is int and 0 <= value <= 4096 for value in body["frame"]),
            "native_report_frame_invalid")
    if admitted is not None:
        require(type(admitted) is dict and type(admitted.get("plan")) is dict, "original_admitted_report_binding_required")
        plan = admitted["plan"]
        require(body["revision_id"] == plan["revision_id"] and body["revision_sha256"] == admitted["wire_sha256"]
                and body["motion_id"] == plan["motion_id"] and plan["context_digest"] == scope["context_digest"],
                "native_report_original_admission_mismatch")
        grant = admitted.get("grant_body")
        require(type(grant) is dict, "original_admitted_grant_required")
        for name in ("fixture_id","device_id","context_digest","pins","ownership_generation","source_generation",
                     "authority_generation","domain_generations"):
            require(canonical_bytes(scope[name]) == canonical_bytes(grant.get(name)), "native_report_scope_admission_mismatch")
    event = body["event"]
    if body["result"] not in {"ok","duplicate"} or event in {"refused","fault","unconfirmed","thermal_fault"}:
        category = "unknown"
    else:
        category = {"admitted":"admission","duplicate":"admission","axis_observed":"axis_state",
                    "settling":"settling","settled":"settling","lighting_verified":"led_registers",
                    "can_submitted":"transport","controller_completed":"transport","motor_reply":"transport",
                    "withdrawn":"protective","protective_submitted":"protective"}.get(event,"unknown")
    if event == "axis_observed" and body["result"] == "ok":
        require(body["axis"] in (0,1) and body["sampled_device_us"] is not None and body["received_device_us"] is not None,
                "native_axis_sample_missing")
    return category


def retain_report(report_id, raw, *, scope, first_received_home_us, category=None, sampled_device_us=None, admitted=None):
    """Create a local causal envelope from a trusted simulated adapter channel.

    The original raw body is preserved exactly (including whitespace). Category
    is a channel type, not permission to claim the contained values were true.
    This first implementation has no real-device ingress.
    """
    validate_scope(scope)
    require(type(report_id) is str and 0 < len(report_id) <= 128, "report_identity_required")
    decimal_u64(first_received_home_us)
    body = parse_original(raw, max_bytes=16384)
    derived = validate_native_event(body, scope, admitted=admitted)
    require(category is None or category == derived, "report_category_mismatch")
    require(sampled_device_us is None or sampled_device_us == body["sampled_device_us"], "report_sample_clock_mismatch")
    category, sampled_device_us = derived, body["sampled_device_us"]
    return {"schema": "home-fixture-execution-report-envelope-1", "version": 1, "report_id": report_id,
            "scope": parse_original(canonical_bytes(scope)), "evidence_source": "simulated",
            "category": category, "sampled_device_us": sampled_device_us,
            "first_received_home_us": first_received_home_us, "original_hex": raw.hex(),
            "original_sha256": sha256(raw), "physical_truth": "unqualified"}


def report_view(report):
    """Truthful status for consumers; endpoints and PWM are never achieved light."""
    require(type(report) is dict and report.get("schema") == "home-fixture-execution-report-envelope-1" and report.get("version") == 1
            and report.get("evidence_source") == "simulated" and report.get("physical_truth") == "unqualified",
            "report_provenance_unsupported")
    validate_scope(report["scope"])
    body = parse_original(bytes.fromhex(report["original_hex"]), max_bytes=16384)
    require(sha256(bytes.fromhex(report["original_hex"])) == report["original_sha256"], "report_original_digest_mismatch")
    category = validate_native_event(body, report["scope"])
    require(category == report["category"] and report["sampled_device_us"] == body["sampled_device_us"],
            "report_semantic_projection_mismatch")
    return {"report_id": report["report_id"], "kind": category, "evidence_source": "simulated",
            "status": "simulated_" + category, "original": body,
            "first_received_home_us": report["first_received_home_us"],
            "sampled_device_us": report["sampled_device_us"], "measured_light": False,
            "physical_pose_confirmed": False, "physical_permission": False}
