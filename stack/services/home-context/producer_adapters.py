"""Explicit local source adapters; no source acquisition and no guessed bindings."""
from decimal import Decimal, InvalidOperation
from copy import deepcopy

from journal import JournalError, bounded_json, check_person_ref, finite, identifier
from validation import fact, fields, refs


def clock_seconds(original, unit, mapping):
    """Normalize only a declared unit and commissioned mapping, never magnitude."""
    if not isinstance(mapping, dict) or mapping.get("status") != "commissioned":
        raise JournalError("adapter_clock_unverified", "commissioned clock mapping required")
    identifier(mapping.get("mapping_id"), "mapping_id")
    uncertainty = mapping.get("uncertainty_s")
    if not finite(uncertainty) or not 0 <= uncertainty <= 1:
        raise JournalError("adapter_clock_unverified", "bounded mapped clock uncertainty required")
    if type(original) is bool or not isinstance(original, (int, float, str)):
        raise JournalError("adapter_clock_invalid", "explicit original timestamp required")
    try:
        raw = Decimal(str(original))
    except InvalidOperation:
        raise JournalError("adapter_clock_invalid", "finite timestamp required") from None
    if not raw.is_finite():
        raise JournalError("adapter_clock_invalid", "finite timestamp required")
    if unit == "utc_epoch_s":
        converted = raw
    elif unit == "utc_epoch_ms":
        converted = raw / 1000
    elif unit == "utc_epoch_ns":
        converted = raw / 1000000000
    elif unit in {"device_relative_s", "session_offset_s"}:
        origin = mapping.get("epoch_origin_s")
        if not finite(origin) or not isinstance(mapping.get("source_epoch"), str):
            raise JournalError("adapter_clock_unverified", "explicit epoch origin and source epoch required")
        converted = Decimal(str(origin)) + raw
    else:
        raise JournalError("adapter_clock_invalid", "unsupported original clock unit")
    seconds = float(converted)
    if not finite(seconds) or seconds < 0:
        raise JournalError("adapter_clock_invalid", "valid Unix epoch seconds required")
    clock = {"status": "commissioned", "mapping_id": mapping["mapping_id"], "uncertainty_s": uncertainty,
             "original_unit": unit, "original_value": str(original)}
    return seconds, clock


def map_roots(roots, bindings):
    """Unknown event/media roots fail; a new independent root is never invented."""
    if not isinstance(roots, list) or not roots or len(roots) > 128 or not isinstance(bindings, dict):
        raise JournalError("adapter_roots_unbound", "explicit source root bindings required")
    sensors, observations = set(), set()
    for root in roots:
        if not isinstance(root, str) or root not in bindings:
            raise JournalError("adapter_roots_unbound", "original root has no registered binding")
        binding = bindings[root]
        if not isinstance(binding, dict) or not {"sensor_root", "observation_root"} <= set(binding) or set(binding) - {"sensor_root", "observation_root", "observed_at"}:
            raise JournalError("adapter_roots_unbound", "exact root binding required")
        sensors.add(identifier(binding["sensor_root"], "sensor_root"))
        observations.add(identifier(binding["observation_root"], "observation_root"))
    return sorted(sensors), sorted(observations)


def scoped_person(scope, tracker_binding=None, *, instance_ref=None):
    """instance_ref is intentionally never used as a persistent person id."""
    if tracker_binding is None:
        return None, {"status": "unresolved", "alternatives": []}
    if not isinstance(tracker_binding, dict) or tracker_binding.get("status") != "qualified":
        raise JournalError("adapter_association_unqualified", "qualified tracker association required")
    person = tracker_binding.get("person_ref")
    check_person_ref(person, scope)
    identifier(tracker_binding.get("association_ref"), "association_ref")
    # Passing an observation-local reference alone is not a tracker binding.
    return dict(person), {"status": "qualified", "alternatives": []}


def adapt_observation(value, *, dialect, binding, kind, fact_value, tracker_binding=None, layer="observation"):
    """Lossless lineage wrapper around an explicitly selected, registered dialect.

    Callers choose a closed fact kind/value mapping. Geometry words are not
    mapped to Apartment target IDs here, and historical scene projections are
    rejected. A binding is supplied by commissioned local composition code.
    """
    supported = {"lab-perception-output-1", "lab-ha-observation-1", "qwen-observation-1", "vjepa-observation-1", "room-facts-event-1"}
    if dialect not in supported or not isinstance(value, dict) or not isinstance(binding, dict) or binding.get("dialect") != dialect:
        raise JournalError("adapter_dialect_unknown", "explicit registered producer dialect required")
    required = {"site_id", "room_id", "session_id", "domain_epoch", "source_id", "producer_event_id", "fact_id", "clock", "root_bindings"}
    if not required <= set(binding):
        raise JournalError("adapter_binding_missing", "full scope, clock and original root binding required")
    scope = {key: identifier(binding[key], key) for key in ("site_id", "room_id", "session_id", "domain_epoch")}
    if value.get("site_id") != scope["site_id"] or any(key in value and value[key] != scope[key] for key in scope):
        raise JournalError("adapter_scope_mismatch", "source belongs to another scope")
    if dialect in {"qwen-observation-1", "vjepa-observation-1"}:
        if value.get("schema") != dialect or value.get("status") != "valid":
            raise JournalError("adapter_invalid_result", "valid bound model receipt required")
        if value.get("first_received_at") != value.get("available_at"):
            raise JournalError("adapter_clock_invalid", "original first receipt required")
        layer = "interpretation"
    elif value.get("status", "completed") != "completed":
        raise JournalError("adapter_invalid_result", "completed producer observation required")
    if dialect == "lab-perception-output-1":
        result = value.get("result")
        if not isinstance(result, dict) or result.get("schema_version") != 1 or result.get("coordinate_space") != "source_pixels" or result.get("frame_id") != value.get("frame_id"):
            raise JournalError("adapter_frame_unbound", "detector result needs exact source frame binding")
    if dialect == "room-facts-event-1" and (not isinstance(value.get("map"), dict) or not {"map_id", "revision", "model_sha256"} <= set(value["map"])):
        raise JournalError("adapter_geometry_unbound", "room facts original map binding required")
    unit = binding.get("time_unit", "utc_epoch_s")
    observed, clock = clock_seconds(value.get("observed_at"), unit, binding["clock"])
    available, _ = clock_seconds(value.get("available_at"), unit, binding["clock"])
    expiry, _ = clock_seconds(value.get("valid_until"), unit, binding["clock"])
    roots = value.get("evidence_roots")
    # Detector/media ancestry must be explicitly bound by the producer. The
    # legacy event-id fallback in persisted_evidence is not independent proof.
    if roots is None:
        roots = binding.get("original_roots")
    sensors, observations = map_roots(roots, binding["root_bindings"])
    root_times = {}
    for root in roots:
        original = binding["root_bindings"][root]
        if "observed_at" in original:
            root_time, _ = clock_seconds(original["observed_at"], unit, binding["clock"])
        elif len(observations) == 1:
            root_time = observed
        else:
            raise JournalError("adapter_roots_unbound", "clip roots need original per-frame clock receipts")
        root_times[original["observation_root"]] = root_time
    person, association = scoped_person(scope, tracker_binding)
    output = {"schema": "fact-1", "version": 1, "id": identifier(binding["fact_id"], "fact_id"),
              "producer_event_id": identifier(binding["producer_event_id"], "producer_event_id"), **scope,
              "person_ref": person, "layer": layer, "kind": identifier(kind, "kind"), "status": "supplied", "value": fact_value,
              "observed_at": observed, "available_at": available, "valid_until": expiry,
              "source_id": identifier(binding["source_id"], "source_id"), "sensor_roots": sensors, "observation_roots": observations,
              "parent_evidence_ids": list(binding.get("parent_evidence_ids", [])), "clock": clock,
              "qualification": "experimental_shadow", "probability": None, "association": association}
    output["observation_root_times"] = root_times
    output["sensor_root_bindings"] = {binding["root_bindings"][root]["observation_root"]: [binding["root_bindings"][root]["sensor_root"]] for root in roots}
    if finite(value.get("window_start")):
        start, _ = clock_seconds(value["window_start"], unit, binding["clock"])
        output["observation_window"] = {"start": start, "end": observed}
    lineage = {key: value[key] for key in ("arm_id", "generation", "capture_epoch") if key in value and value[key] is not None}
    if dialect == "room-facts-event-1":
        lineage["map"] = {k: value["map"][k] for k in ("map_id", "revision", "model_sha256")}
        lineage.update({key: value[key] for key in ("rule", "pairing_skew_s") if key in value and value[key] is not None})
    if lineage:
        output["legacy_lineage"] = {"dialect": dialect, **lineage}
    fact(output)
    return output


def to_geometry_sidecar(value, registered):
    """Owner-authorized single-sensor projection of already commissioned geometry.

    This function copies metric geometry supplied by registered composition code.
    It performs no pixel/depth transform, tracker assignment or target inference.
    Semantic facts remain free of this coordinate object.
    """
    fact(value)
    if len(value["sensor_roots"]) != 1 or value["person_ref"] is None:
        raise JournalError("adapter_geometry_unbound", "single physical sensor and qualified scoped person required")
    fields(registered, {"id", "frame", "units", "geometry_revision", "registration_ref", "registry_digest", "coverage_refs", "association_ref", "authorized_geometry"})
    if registered["frame"] != "z_up_metric_floor0" or registered["units"] != "m" or not isinstance(registered["authorized_geometry"], dict):
        raise JournalError("adapter_geometry_unbound", "explicit registered metric geometry required")
    spatial = value.get("spatial")
    if not isinstance(spatial, dict) or any(spatial.get(k) != registered[k] for k in ("geometry_revision", "registration_ref", "registry_digest", "association_ref", "coverage_refs")):
        raise JournalError("adapter_geometry_unbound", "fact geometry lineage must match the commissioned registration")
    refs(registered["coverage_refs"], "coverage_refs", allow_empty=False)
    for key in ("id", "geometry_revision", "registration_ref", "association_ref"):
        identifier(registered[key], key)
    import re
    if not isinstance(registered["registry_digest"], str) or not re.fullmatch(r"[a-f0-9]{64}", registered["registry_digest"]):
        raise JournalError("adapter_geometry_unbound", "explicit pinned geometry registry digest required")
    output = {"schema": "home-geometry-1", "version": 1, **{k: value[k] for k in ("site_id", "room_id", "session_id", "domain_epoch", "person_ref", "observed_at", "available_at", "valid_until", "clock")}, **registered}
    bounded_json(output, 65536)
    return deepcopy(output)
