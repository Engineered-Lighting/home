"""Typed minimum admission checks matching the shared Home context contract."""
import re
from journal import JournalError, bounded_json, check_person_ref, finite, identifier


SCOPE = {"site_id", "room_id", "session_id", "domain_epoch"}
BOOL_KINDS = {"surface_manipulation", "hand_interaction", "object_interaction", "working_position", "orientation",
              "negative_engagement", "pc_input", "proximity", "pass_by", "object_visible", "device_active", "conflict", "qualified_association"}
ACTIVITIES = {"computer_work", "gaming", "desk_hobby", "reading", "phone_use", "listening", "resting", "sleeping_likely", "cleaning", "transition", "other"}
MODEL_CUES = {"held_object": {"book", "phone", "keyboard", "mouse", "unknown", "none"},
              "book_state": {"open", "closed", "unknown"}, "posture": {"sitting", "standing", "lying", "unknown"},
              "activity_hypothesis": ACTIVITIES | {"unknown"}}


def metadata(value):
    if "model" in value:
        model = value["model"]
        fields(model, (), {"model_id", "model_sha256", "config_id", "config_digest", "calibration_id", "source_run_id"})
        for key, item in model.items():
            if key in {"model_sha256", "config_digest"}:
                if not isinstance(item, str) or not re.fullmatch(r"[a-f0-9]{64}", item):
                    raise JournalError("invalid_payload", "model metadata digest required")
            else:
                identifier(item, key)
    if "legacy_lineage" in value:
        lineage = value["legacy_lineage"]
        fields(lineage, (), {"dialect", "arm_id", "generation", "capture_epoch", "rule", "map", "pairing_skew_s"})
        for key in ("dialect", "arm_id", "capture_epoch", "rule"):
            if key in lineage:
                identifier(lineage[key], key)
        if "generation" in lineage and (type(lineage["generation"]) is not int or lineage["generation"] < 0):
            raise JournalError("invalid_payload", "nonnegative source generation required")
        if "pairing_skew_s" in lineage and (not finite(lineage["pairing_skew_s"]) or lineage["pairing_skew_s"] < 0):
            raise JournalError("invalid_payload", "nonnegative pairing skew required")
        if "map" in lineage:
            fields(lineage["map"], {"map_id", "revision", "model_sha256"})
            for key in ("map_id", "revision"):
                identifier(lineage["map"][key], key)
            if not isinstance(lineage["map"]["model_sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", lineage["map"]["model_sha256"]):
                raise JournalError("invalid_payload", "source map digest required")


def fact_value(kind, status, value):
    if kind not in BOOL_KINDS | {"person_presence", "reading_material", "activity_mode", "activity_proposal", "model_cues"}:
        raise JournalError("invalid_payload", "fact kind requires a registered versioned mapping")
    if status in {"missing", "stale", "unobservable"}:
        valid = value is None
    elif kind in BOOL_KINDS:
        valid = type(value) is bool
    elif kind == "person_presence":
        valid = isinstance(value, dict) and set(value) == {"track_status"} and value["track_status"] in {"tracked", "coasting", "not_observed"}
    elif kind == "reading_material":
        valid = isinstance(value, dict) and set(value) == {"state", "relation"} and value["state"] in {"open", "closed", "unknown"} and value["relation"] in {"person_using_material", "visible_unused", "unknown"}
    elif kind in {"activity_mode", "activity_proposal"}:
        valid = isinstance(value, str) and value in ACTIVITIES
    else:
        valid = isinstance(value, dict) and set(value) <= set(MODEL_CUES) and all(isinstance(v, str) and v in MODEL_CUES[k] for k, v in value.items())
    if not valid:
        raise JournalError("invalid_payload", "closed semantic fact value required")


def fields(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= set(value) or set(value) - set(required) - set(optional):
        raise JournalError("invalid_payload", "exact typed payload fields required")


def refs(value, field, allow_empty=True):
    if not isinstance(value, list) or len(value) > 128 or any(not isinstance(v, str) for v in value) or len(set(value)) != len(value) or not allow_empty and not value:
        raise JournalError("invalid_evidence_refs", "bounded unique evidence references required")
    for item in value:
        identifier(item, field)


def typed_version(value, schema):
    if value.get("schema") != schema or type(value.get("version")) is not int or value["version"] != 1:
        raise JournalError("unsupported_schema", "typed payload version 1 required")


def fact(value, *, admission=True):
    required = {"schema", "version", "id", "producer_event_id", *SCOPE, "person_ref", "layer", "kind", "status", "value",
                "observed_at", "available_at", "valid_until", "source_id", "sensor_roots", "observation_roots",
                "parent_evidence_ids", "clock", "qualification", "probability"}
    optional = {"surface_id", "region_id", "association", "spatial", "attribution_refs", "model", "calibration",
                "observation_window", "legacy_lineage", "sensor_root_bindings", "observation_root_times"}
    fields(value, required, optional)
    metadata(value)
    typed_version(value, "fact-1")
    for key in SCOPE | {"id", "producer_event_id", "source_id", "kind"}:
        identifier(value[key], key)
    for key in ("surface_id", "region_id"):
        if key in value:
            identifier(value[key], key)
    if value["person_ref"] is not None:
        check_person_ref(value["person_ref"], value)
    elif not isinstance(value.get("association"), dict) or value["association"].get("status") != "unresolved":
        raise JournalError("invalid_person_ref", "unresolved fact requires explicit association status")
    if value["layer"] not in {"observation", "interpretation"} or value["status"] not in {"supplied", "missing", "stale", "unobservable", "conflict"}:
        raise JournalError("invalid_payload", "unknown fact layer/status")
    fact_value(value["kind"], value["status"], value["value"])
    if value["qualification"] not in {"experimental_shadow", "qualified_shadow"}:
        raise JournalError("invalid_payload", "unknown qualification")
    if not all(finite(value[k]) for k in ("observed_at", "available_at", "valid_until")) or not 0 <= value["observed_at"] <= value["available_at"] or value["valid_until"] <= value["observed_at"]:
        raise JournalError("invalid_clock", "causal fact epoch-second interval required")
    for key in ("sensor_roots", "observation_roots", "parent_evidence_ids"):
        refs(value[key], key, allow_empty=value["status"] != "supplied" or key == "parent_evidence_ids")
    times = value.get("observation_root_times")
    if len(value["observation_roots"]) > 1 and times is None:
        raise JournalError("root_times_required", "multi-root facts require original per-root observation times")
    if times is not None and (not isinstance(times, dict) or set(times) != set(value["observation_roots"]) or any(not finite(t) or not 0 <= t <= value["observed_at"] for t in times.values()) or times and max(times.values()) != value["observed_at"]):
        raise JournalError("invalid_root_times", "exact causal original root times required")
    window = value.get("observation_window")
    if window is not None:
        fields(window, {"start", "end"})
        if not all(finite(window[k]) for k in window) or not 0 <= window["start"] <= window["end"] == value["observed_at"] or times is not None and any(not window["start"] <= t <= window["end"] for t in times.values()):
            raise JournalError("invalid_root_times", "root observation times must fit the recorded window")
    fields(value["clock"], {"status", "mapping_id", "uncertainty_s", "original_unit", "original_value"})
    clock = value["clock"]
    identifier(clock["mapping_id"], "mapping_id")
    if (clock["status"] not in {"commissioned", "unverified"} or admission and clock["status"] != "commissioned"
            or not finite(clock["uncertainty_s"]) or clock["uncertainty_s"] < 0
            or clock["original_unit"] not in {"utc_epoch_s", "utc_epoch_ms", "utc_epoch_ns", "device_relative_s", "session_offset_s"}
            or not isinstance(clock["original_value"], str)):
        raise JournalError("invalid_clock", "explicit commissioned clock mapping required for admission")
    if value["probability"] is not None:
        cal = value.get("calibration")
        if not finite(value["probability"]) or not 0 <= value["probability"] <= 1 or not isinstance(cal, dict) or cal.get("status") != "fitted":
            raise JournalError("invalid_probability", "fitted probability calibration required")
        for key in ("calibrator_id", "calibration_set_id"):
            identifier(cal.get(key), key)
    if "spatial" in value:
        spatial = value["spatial"]
        fields(spatial, {"geometry_revision", "registration_ref", "coverage_refs", "association_ref", "registration_verified", "coverage_status", "association_status"}, {"registry_digest"})
        for key in ("geometry_revision", "registration_ref", "association_ref"):
            identifier(spatial[key], key)
        refs(spatial["coverage_refs"], "coverage_refs")
        if spatial["registration_verified"] is not True or spatial["coverage_status"] != "observed" or spatial["association_status"] != "qualified":
            raise JournalError("unqualified_geometry", "verified geometry, coverage and association required")
    if "attribution_refs" in value:
        refs(value["attribution_refs"], "attribution_refs")
    if value["kind"] == "pc_input" and value["value"] is True and not value.get("attribution_refs"):
        raise JournalError("unattributed_device", "person-attributed PC activity requires attribution refs")
    bounded_json(value, 65536)


def source_state(value):
    schema = value.get("schema")
    base = {"schema", "version", "id", *SCOPE, "source_id", "status", "observed_at", "available_at", "valid_until"}
    if schema == "home-source-health-1":
        fields(value, base | {"reason"})
        if value["status"] not in {"functional", "unavailable", "failed", "revoked"}:
            raise JournalError("invalid_payload", "unknown source health status")
        identifier(value["reason"], "reason")
    elif schema == "home-coverage-1":
        fields(value, base | {"geometry_revision", "registration_ref", "surface_ids", "evidence_refs"})
        if value["status"] not in {"observed", "unobservable"}:
            raise JournalError("invalid_payload", "unknown coverage status")
        for key in ("geometry_revision", "registration_ref"):
            identifier(value[key], key)
        refs(value["surface_ids"], "surface_ids")
        refs(value["evidence_refs"], "evidence_refs", allow_empty=value["status"] != "observed")
    else:
        raise JournalError("unsupported_schema", "known source health or coverage schema required")
    typed_version(value, schema)
    for key in SCOPE | {"id", "source_id"}:
        identifier(value[key], key)
    if not all(finite(value[k]) for k in ("observed_at", "available_at", "valid_until")) or not 0 <= value["observed_at"] <= value["available_at"] or value["valid_until"] <= value["observed_at"]:
        raise JournalError("invalid_clock", "causal source status interval required")


def intent(value):
    fields(value, {"schema", "version", "id", *SCOPE, "actor_ref", "kind", "value", "set_at", "available_at", "valid_until", "origin", "truth_role"},
           {"reply_to", "person_ref", "surface_id", "region_id", "purpose", "zone_id"})
    typed_version(value, "home-intent-event-1")
    if value["kind"] not in {"declared_surface_use", "too_bright", "wrong_region", "wrong_fixture", "dont_follow", "brightness", "cct", "region", "fixture"}:
        raise JournalError("invalid_payload", "unknown intent kind")
    for key in ("id", "actor_ref"):
        identifier(value[key], key)
    for key in ("reply_to", "surface_id", "region_id", "zone_id"):
        if key in value:
            identifier(value[key], key)
    if value.get("person_ref") is not None:
        check_person_ref(value["person_ref"], value)
    if value["kind"] == "dont_follow" and value.get("person_ref") is None:
        raise JournalError("invalid_person_ref", "dont_follow needs a full person reference")
    if value["kind"] == "declared_surface_use" and not {"surface_id", "region_id"} <= set(value):
        raise JournalError("invalid_payload", "declared surface use needs explicit surface and region")
    if not all(finite(value[k]) for k in ("set_at", "available_at", "valid_until")) or not 0 <= value["set_at"] <= value["available_at"] or value["valid_until"] <= value["set_at"]:
        raise JournalError("invalid_clock", "causal intent interval required")
