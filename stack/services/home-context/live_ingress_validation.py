"""Closed commissioning and causal checks for the opt-in live ingress profile.

This module reads no files, cameras or services. Original receipt lookup is an
explicit trusted composition dependency; publication lookup belongs to the journal.
"""
from __future__ import annotations

import json
import math
import re
from decimal import Decimal, InvalidOperation

from journal import JournalError, canonical, digest, finite, identifier


SCOPE_KEYS = {"site_id", "room_id", "session_id", "domain_epoch"}
PARTS = {"work", "body", "head", "hand_left", "hand_right", "protection", "body_forward", "head_forward"}
HANDOFF_DIGEST = "8345e855397d86c65a2197daa7592dcf4f73dc48fa00659bd588093b297d7d0e"
CLOCK_KEYS = {"mapping_id", "mapping_digest", "source_epoch", "original_unit", "epoch_origin_s", "max_uncertainty_s"}
REGISTRATION_KEYS = {"registration_ref", "registration_digest", "geometry_revision", "geometry_digest", "mount_digest",
                     "calibration_digest", "coverage_refs", "frame", "unit", "transform_chain_refs"}
RECEIPT_KEYS = {"store_id", "receipt_id", "digest", "component_id", "observed_at", "available_at", "clock_mapping_id"}
RECEIPT_RECORD_KEYS = {"ref", "capture_epoch", "capture_seq", "observation_root_times", "sensor_root_bindings"}
SOURCE_KEYS = {"producer_id", "producer_epoch", "capture_epoch", "initial_generation", "sensor_roots", "capture_clock",
               "control_clock", "method_refs", "model_refs", "association_method_refs", "registration", "receipt_store_ids",
               "max_age_s", "part_policies", "receipt_component_ids", "primitive_ids", "protection_policy_refs"}


def _reject(code="invalid_live_input"):
    raise JournalError(code, "input does not match its commissioned live source policy")


def _exact(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        _reject()


def _id(value):
    identifier(value, "registered_identifier")


def _sha(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        _reject()


def _ref(value):
    _exact(value, {"id", "digest"})
    _id(value["id"])
    _sha(value["digest"])


def _integer(value, minimum=0, maximum=9007199254740991):
    if type(value) is not int or not minimum <= value <= maximum:
        _reject()


def _nonnegative(value, *, positive=False):
    if not finite(value) or value < 0 or positive and value == 0:
        _reject()


def _array(value, limit, *, nonempty=False):
    if not isinstance(value, list) or len(value) > limit or nonempty and not value:
        _reject()
    if len({canonical(item) for item in value}) != len(value):
        _reject()


def _refs(value, *, nonempty=False):
    _array(value, 64, nonempty=nonempty)
    for item in value:
        _ref(item)


def _clock_policy(policy):
    _exact(policy, CLOCK_KEYS)
    _id(policy["mapping_id"])
    _sha(policy["mapping_digest"])
    _id(policy["source_epoch"])
    if policy["original_unit"] not in {"unix_ns", "unix_s", "device_ns", "monotonic_ns"}:
        _reject("invalid_clock")
    _nonnegative(policy["epoch_origin_s"])
    _nonnegative(policy["max_uncertainty_s"])
    if policy["max_uncertainty_s"] > 1:
        _reject("invalid_clock")
    if policy["original_unit"] in {"unix_ns", "unix_s"} and policy["epoch_origin_s"] != 0:
        _reject("invalid_clock")


def validate_clock(clock, policy, observed_at):
    """Convert the original clock once, retaining exact integer arithmetic to UTC."""
    _exact(clock, {"status", "mapping_id", "mapping_digest", "source_epoch", "original_unit", "original_value", "uncertainty_s"})
    if clock["status"] != "commissioned" or any(clock[key] != policy[key] for key in ("mapping_id", "mapping_digest", "source_epoch", "original_unit")):
        _reject("invalid_clock")
    _nonnegative(observed_at)
    _nonnegative(clock["uncertainty_s"])
    if clock["uncertainty_s"] > policy["max_uncertainty_s"]:
        _reject("invalid_clock")
    raw = clock["original_value"]
    unit = clock["original_unit"]
    if not isinstance(raw, str) or len(raw) > 48 or not re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", raw):
        _reject("invalid_clock")
    if unit in {"unix_ns", "device_ns", "monotonic_ns"} and "." in raw:
        _reject("invalid_clock")
    try:
        raw_time = Decimal(raw)
        scale = Decimal(1000000000) if unit.endswith("ns") else Decimal(1)
        converted = raw_time / scale + Decimal(str(policy["epoch_origin_s"]))
        # The wire is an epoch-second JSON number; its one binary rounding is
        # allowed, but a second arrival-time substitution or arbitrary epsilon is not.
        if not converted.is_finite() or float(converted) != observed_at:
            _reject("invalid_clock")
    except (InvalidOperation, OverflowError, ValueError):
        _reject("invalid_clock")
    return observed_at


def _uncertainty(policy, *, directional, origin=None):
    if directional:
        _exact(policy, {"model", "angular_error_rad", "age", "applicability_ref"})
        if policy["model"] != "bounded_angle-1":
            _reject()
        _nonnegative(policy["angular_error_rad"])
        if policy["angular_error_rad"] > math.pi:
            _reject()
        keys = {"max_age_s", "max_angular_speed_rad_s"}
    else:
        _exact(policy, {"model", "bound_at_observation_m", "components_m", "age", "applicability_ref"})
        if policy["model"] != "conservative_additive_radius-1":
            _reject()
        _exact(policy["components_m"], {"capture_association", "depth", "registration", "estimator"})
        for value in policy["components_m"].values():
            _nonnegative(value)
        _nonnegative(policy["bound_at_observation_m"])
        total = sum(Decimal(str(value)) for value in policy["components_m"].values())
        if abs(Decimal(str(policy["bound_at_observation_m"])) - total) > Decimal("0.000000000001"):
            _reject("invalid_uncertainty")
        keys = {"max_age_s", "max_speed_m_s", "max_accel_m_s2"}
    _ref(policy["applicability_ref"])
    _exact(policy["age"], keys | ({"origin_at"} if origin is not None else set()))
    for key in keys:
        _nonnegative(policy["age"][key], positive=key == "max_age_s")
    if origin is not None and policy["age"]["origin_at"] != origin:
        _reject("invalid_clock")


def _registration(registration):
    _exact(registration, REGISTRATION_KEYS)
    for key in {"registration_ref", "geometry_revision"}:
        _id(registration[key])
    for key in {"registration_digest", "geometry_digest", "mount_digest", "calibration_digest"}:
        _sha(registration[key])
    if registration["frame"] != "z_up_metric_floor0" or registration["unit"] != "m":
        _reject()
    _array(registration["coverage_refs"], 128, nonempty=True)
    for item in registration["coverage_refs"]:
        _id(item)
    _refs(registration["transform_chain_refs"], nonempty=True)


def validate_config(config, schema_registry):
    """Reject unknown configuration or pins before the journal opens a database."""
    try:
        _exact(config, {"schema", "version", "manifest_ref", "manifest"})
        subset_enabled = config.get("schema") == "home-live-ingress-config-13" and type(config.get("version")) is int and config["version"] == 13
        pc_enabled = subset_enabled or config.get("schema") == "home-live-ingress-config-12" and type(config.get("version")) is int and config["version"] == 12
        manual_enabled = config.get("schema") == "home-live-ingress-config-11" and type(config.get("version")) is int and config["version"] == 11
        room_clarification_enabled = manual_enabled or config.get("schema") == "home-live-ingress-config-10" and type(config.get("version")) is int and config["version"] == 10
        room_enabled = pc_enabled or room_clarification_enabled or config.get("schema") == "home-live-ingress-config-9" and type(config.get("version")) is int and config["version"] == 9
        conservative_enabled = room_enabled or config.get("schema") == "home-live-ingress-config-7" and type(config.get("version")) is int and config["version"] == 7
        lifecycle_enabled = conservative_enabled or config.get("schema") == "home-live-ingress-config-6" and type(config.get("version")) is int and config["version"] == 6
        stable_enabled = lifecycle_enabled or config.get("schema") == "home-live-ingress-config-5" and type(config.get("version")) is int and config["version"] == 5
        delivery_enabled = stable_enabled or config.get("schema") == "home-live-ingress-config-4" and type(config.get("version")) is int and config["version"] == 4
        scene_enabled = delivery_enabled or config.get("schema") == "home-live-ingress-config-3" and type(config.get("version")) is int and config["version"] == 3
        worker_enabled = scene_enabled or config.get("schema") == "home-live-ingress-config-2" and type(config.get("version")) is int and config["version"] == 2
        if not worker_enabled and (config["schema"] != "home-live-ingress-config-1" or type(config["version"]) is not int or config["version"] != 1):
            _reject()
        _ref(config["manifest_ref"])
        manifest = config["manifest"]
        _exact(manifest, {"schema", "version", "identity", "handoff_digest", "interface_digest", "sources", "limits"}
               | ({"worker_interface_digest", "worker"} if worker_enabled else set())
               | ({"scene_interface_digest"} if scene_enabled else set())
               | ({"delivery_interface_digest"} if delivery_enabled else set())
               | ({"lifecycle_interface_digest"} if lifecycle_enabled else set())
               | ({"conservative_delivery_interface_digest", "service_clock_policy"} if conservative_enabled else set())
               | ({"room_interface_digest"} if room_enabled else set())
               | ({"room_clarification_interface_digest"} if room_clarification_enabled else set())
               | ({"room_manual_interface_digest"} if manual_enabled else set())
               | ({"pc_support_interface_digest"} if pc_enabled else set())
               | ({"room_subset_interface_digest"} if subset_enabled else set()))
        version = 13 if subset_enabled else 12 if pc_enabled else 11 if manual_enabled else 10 if room_clarification_enabled else 9 if room_enabled else 7 if conservative_enabled else 6 if lifecycle_enabled else 5 if stable_enabled else 4 if delivery_enabled else 3 if scene_enabled else 2 if worker_enabled else 1
        if (manifest["schema"] != "home-live-ingress-manifest-" + str(version) or type(manifest["version"]) is not int or manifest["version"] != version
                or manifest["handoff_digest"] != HANDOFF_DIGEST or manifest["interface_digest"] != schema_registry.interface_digest
                or config["manifest_ref"]["digest"] != digest(manifest)):
            _reject()
        _exact(manifest["identity"], SCOPE_KEYS)
        for item in manifest["identity"].values():
            _id(item)
        _exact(manifest["limits"], {"max_original_roots", "max_heads", "max_receipt_bindings"})
        for key, limit in (("max_original_roots", 1024), ("max_heads", 128), ("max_receipt_bindings", 2048)):
            _integer(manifest["limits"][key], 1, limit)
        if conservative_enabled:
            policy = manifest["service_clock_policy"]
            _exact(policy,{"schema","version","qualification_ref","valid_until","uncertainty_s","max_anchor_divergence_s","max_round_trip_s","max_mapping_age_s"})
            if policy["schema"] != "home-live-service-clock-policy-1" or type(policy["version"]) is not int or policy["version"] != 1:
                _reject()
            _ref(policy["qualification_ref"])
            for key, upper in (("uncertainty_s",1),("max_anchor_divergence_s",1),("max_round_trip_s",1),("max_mapping_age_s",5)):
                _nonnegative(policy[key],positive=key!="uncertainty_s")
                if policy[key]>upper:
                    _reject()
            _nonnegative(policy["valid_until"],positive=True)
        sources = manifest["sources"]
        if not isinstance(sources, dict) or not 1 <= len(sources) <= 16:
            _reject()
        for source_id, source in sources.items():
            _id(source_id)
            _exact(source, SOURCE_KEYS)
            for key in ("producer_id", "producer_epoch", "capture_epoch"):
                _id(source[key])
            _integer(source["initial_generation"], 1)
            for key, maximum in (("sensor_roots", 128), ("receipt_store_ids", 16), ("receipt_component_ids", 64)):
                _array(source[key], maximum, nonempty=True)
                for item in source[key]:
                    _id(item)
            _clock_policy(source["capture_clock"])
            _clock_policy(source["control_clock"])
            if source["capture_clock"]["source_epoch"] != source["capture_epoch"]:
                _reject("invalid_clock")
            for key in ("method_refs", "model_refs", "association_method_refs"):
                _refs(source[key], nonempty=key != "model_refs")
            _nonnegative(source["max_age_s"], positive=True)
            if source["max_age_s"] > 3600:
                _reject()
            if source["registration"] is not None:
                _registration(source["registration"])
            _exact(source["part_policies"], PARTS)
            for name, policy in source["part_policies"].items():
                if policy is not None:
                    if source["registration"] is None:
                        _reject()
                    _uncertainty(policy, directional=name.endswith("_forward"))
                    if policy["age"]["max_age_s"] > source["max_age_s"]:
                        _reject()
            _exact(source["primitive_ids"], {"body", "protection"})
            for name, maximum in (("body", 12), ("protection", 4)):
                _array(source["primitive_ids"][name], maximum, nonempty=source["part_policies"][name] is not None)
                for primitive_id in source["primitive_ids"][name]:
                    _id(primitive_id)
            _refs(source["protection_policy_refs"], nonempty=source["part_policies"]["protection"] is not None)
        if worker_enabled:
            if subset_enabled:
                from live_lifecycle import validate_lifecycle_policy
                from room_subset_worker import validate_room_subset_worker_policy
                validate_lifecycle_policy(manifest["worker"]["lifecycle_policy"], sources)
                validate_room_subset_worker_policy(manifest, schema_registry)
            elif manual_enabled:
                from live_lifecycle import validate_lifecycle_policy
                from room_manual import validate_manual_worker_policy
                validate_lifecycle_policy(manifest["worker"]["lifecycle_policy"], sources)
                validate_manual_worker_policy(manifest, schema_registry)
            elif room_clarification_enabled:
                from live_lifecycle import validate_lifecycle_policy
                from room_clarification import validate_room_clarification_worker_policy
                validate_lifecycle_policy(manifest["worker"]["lifecycle_policy"], sources)
                validate_room_clarification_worker_policy(manifest, schema_registry)
            elif pc_enabled:
                from live_lifecycle import validate_lifecycle_policy
                from pc_support_worker import validate_pc_worker_policy
                validate_lifecycle_policy(manifest["worker"]["lifecycle_policy"], sources)
                validate_pc_worker_policy(manifest, schema_registry)
            elif room_enabled:
                from live_lifecycle import validate_lifecycle_policy
                from room_delivery import validate_room_worker_policy
                validate_lifecycle_policy(manifest["worker"]["lifecycle_policy"], sources)
                validate_room_worker_policy(manifest, schema_registry)
            elif conservative_enabled:
                from live_lifecycle import validate_lifecycle_policy
                from live_delivery import validate_conservative_delivery_worker_policy
                validate_lifecycle_policy(manifest["worker"]["lifecycle_policy"], sources)
                validate_conservative_delivery_worker_policy(manifest, schema_registry)
            elif lifecycle_enabled:
                from live_lifecycle import validate_lifecycle_policy
                from live_delivery import validate_lifecycle_delivery_worker_policy
                if not schema_registry.lifecycle_interface_digest or manifest["lifecycle_interface_digest"] != schema_registry.lifecycle_interface_digest:
                    _reject()
                validate_lifecycle_policy(manifest["worker"]["lifecycle_policy"], sources)
                validate_lifecycle_delivery_worker_policy(manifest, schema_registry)
            elif stable_enabled:
                from live_delivery import validate_stable_delivery_worker_policy
                validate_stable_delivery_worker_policy(manifest, schema_registry)
            elif delivery_enabled:
                from live_delivery import validate_delivery_worker_policy
                validate_delivery_worker_policy(manifest, schema_registry)
            elif scene_enabled:
                from live_scene import validate_scene_worker_policy
                validate_scene_worker_policy(manifest, schema_registry)
            else:
                from live_worker import validate_worker_policy
                validate_worker_policy(manifest, schema_registry)
        return json.loads(canonical(config))
    except (JournalError, TypeError, KeyError, ValueError, OverflowError):
        raise ValueError("invalid commissioned live ingress configuration") from None


def _person(person, scope):
    _exact(person, SCOPE_KEYS | {"track_epoch", "pid"})
    if any(person[key] != scope[key] for key in SCOPE_KEYS):
        _reject("scope_mismatch")
    _id(person["track_epoch"])
    if type(person["pid"]) is int:
        _integer(person["pid"], 1)
    else:
        _id(person["pid"])


def _association(payload, source):
    association = payload["association"]
    if association["method_ref"] not in source["association_method_refs"]:
        _reject("forbidden")
    _integer(association["generation"], 1)
    for kind in ("person", "subject"):
        selected = payload[kind + "_ref"]
        candidates = association["candidate_" + kind + "_refs"]
        status = association[kind + "_status"]
        _array(candidates, 16)
        if status == "qualified":
            if selected is None or candidates:
                _reject("invalid_association")
        elif status == "unresolved":
            if selected is not None or not candidates:
                _reject("invalid_association")
        elif status == "not_applicable":
            if selected is not None or candidates:
                _reject("invalid_association")
        else:
            _reject("invalid_association")
        for item in ([selected] if selected is not None else []) + candidates:
            if kind == "person":
                _person(item, payload)
            else:
                _exact(item, {"subject_id", "subject_epoch"})
                _id(item["subject_id"])
                _id(item["subject_epoch"])
    if association["subject_status"] == "qualified" and association["person_status"] != "qualified":
        _reject("invalid_association")


def _receipt(ref, source, resolver, *, pc_proof_components=()):
    _exact(ref, RECEIPT_KEYS)
    if (ref["store_id"] not in source["receipt_store_ids"] or ref["component_id"] not in source["receipt_component_ids"]
            or ref["clock_mapping_id"] != source["capture_clock"]["mapping_id"]):
        _reject("forbidden")
    for key in ("store_id", "receipt_id", "component_id", "clock_mapping_id"):
        _id(ref[key])
    _sha(ref["digest"])
    if not finite(ref["observed_at"]) or not finite(ref["available_at"]) or not 0 <= ref["observed_at"] <= ref["available_at"]:
        _reject("invalid_clock")
    try:
        record = resolver(ref["store_id"], ref["receipt_id"])
    except Exception:
        _reject("receipt_unavailable")
    extra = {"pc_input_original"} if ref["component_id"] in pc_proof_components else set()
    _exact(record, RECEIPT_RECORD_KEYS | extra)
    if record["ref"] != ref or record["capture_epoch"] != source["capture_epoch"]:
        _reject("receipt_mismatch")
    _integer(record["capture_seq"], 0)
    return record


def _shape(shape):
    kind = shape["kind"]
    if kind in {"opaque_primitives", "protected_primitives"}:
        names = []
        for item in shape["items"]:
            _shape(item)
            names.append(item["part_id"])
        if len(names) != len(set(names)):
            _reject("invalid_geometry")
        return
    if kind == "direction":
        vector = shape["unit_vector"]
        if len(vector) != 3 or not all(finite(value) for value in vector) or abs(sum(value * value for value in vector) - 1) > 1e-6:
            _reject("invalid_geometry")
        return
    for key in ("center_m", "half_extents_m") if kind == "aabb" else ("a_m", "b_m"):
        vector = shape[key]
        if len(vector) != 3 or not all(finite(value) for value in vector):
            _reject("invalid_geometry")
    if kind == "aabb" and not all(value > 0 for value in shape["half_extents_m"]):
        _reject("invalid_geometry")
    if kind == "capsule":
        _nonnegative(shape["radius_m"], positive=True)


def validate_capture_lineage(payload, source, receipt_resolver, *, has_parents=False, pc_proof_components=()):
    """Protocol-neutral original capture clocks, roots and fixed-store receipts."""
    head = payload["head"]
    observed, available, valid = (payload[key] for key in ("observed_at", "available_at", "valid_until"))
    if not all(finite(value) for value in (observed, available, valid)) or not 0 <= observed <= available < valid or valid - observed > source["max_age_s"]:
        _reject("invalid_clock")
    validate_clock(payload["clock"], source["capture_clock"], observed)
    roots, root_times, root_bindings = payload["observation_roots"], payload["observation_root_times"], payload["sensor_root_bindings"]
    _array(roots, 128, nonempty=True)
    _array(payload["sensor_roots"], 128, nonempty=True)
    if not isinstance(root_times, dict) or not isinstance(root_bindings, dict) or set(root_times) != set(roots) or set(root_bindings) != set(roots):
        _reject("invalid_root_bindings")
    union = set()
    for root in roots:
        _id(root)
        if not finite(root_times[root]) or not 0 <= root_times[root] <= observed:
            _reject("invalid_clock")
        _array(root_bindings[root], 128, nonempty=True)
        if not set(root_bindings[root]) <= set(source["sensor_roots"]):
            _reject("forbidden")
        union.update(root_bindings[root])
    if max(root_times.values()) != observed or union != set(payload["sensor_roots"]):
        _reject("invalid_root_bindings")
    resolved = []
    receipt_roots, receipt_bindings = {}, {}
    latest_sequence = -1
    for ref in payload["original_receipt_refs"]:
        record = _receipt(ref, source, receipt_resolver, pc_proof_components=pc_proof_components)
        if ref["available_at"] > available or ref["observed_at"] > observed or record["capture_seq"] > head["position"]["capture_seq"]:
            _reject("invalid_clock")
        # The explicit PC proof owns its source sequence at the unchanged HA
        # instant. A separately resolved current work parent can contribute a
        # newer full-ancestry time. The PC proof and complete parent lineage are
        # independently validated by the same admission transaction below.
        if ref["observed_at"] == observed or (has_parents
                and ref["component_id"] in pc_proof_components and "pc_input_original" in record
                and record["capture_seq"] == head["position"]["capture_seq"]):
            latest_sequence = max(latest_sequence, record["capture_seq"])
        times, bindings = record["observation_root_times"], record["sensor_root_bindings"]
        if not isinstance(times, dict) or not times or not isinstance(bindings, dict) or set(times) != set(bindings):
            _reject("receipt_mismatch")
        for root, time in times.items():
            _id(root)
            _array(bindings[root], 128, nonempty=True)
            for physical in bindings[root]:
                _id(physical)
            if not finite(time) or not 0 <= time <= ref["observed_at"] or root not in root_times or time != root_times[root] or set(bindings[root]) != set(root_bindings[root]):
                _reject("receipt_mismatch")
            if root in receipt_roots and (receipt_roots[root] != time or set(receipt_bindings[root]) != set(bindings[root])):
                _reject("receipt_mismatch")
            receipt_roots[root], receipt_bindings[root] = time, bindings[root]
        if max(times.values()) != ref["observed_at"]:
            _reject("receipt_mismatch")
        resolved.append(record)
    if latest_sequence != head["position"]["capture_seq"]:
        _reject("receipt_mismatch")
    # Publication parents may contribute their own roots; journal resolution adds
    # those roots before demanding complete ancestry. With no parents, all roots
    # must be proved here by original receipts rather than invented identifiers.
    if not has_parents and set(receipt_roots) != set(roots):
        _reject("receipt_mismatch")
    return resolved


def validate_capture(payload, source, receipt_resolver, *, pc_proof_components=()):
    """Check one schema-validated capture against immutable commissioned policy.

    Returns resolved original receipts. The journal separately resolves parent
    publications and persists roots, capture positions, heads and source barriers.
    """
    if payload["source_id"] != payload["head"]["key"]["source_id"]:
        _reject("scope_mismatch")
    _association(payload, source)
    method = payload["method_ref"]
    if method not in source["method_refs"]:
        _reject("forbidden")
    head = payload["head"]
    key = head["key"]
    predicate = "geometry_bundle" if payload["schema"] == "home-live-geometry-1" else payload["kind"]
    if (key["payload_type"] != payload["schema"] or key["person_ref"] != payload["person_ref"]
            or key["subject_ref"] != payload["subject_ref"] or key["predicate"] != predicate or key["method_ref"] != method
            or head["position"]["capture_epoch"] != source["capture_epoch"]):
        _reject("invalid_head")
    resolved = validate_capture_lineage(payload, source, receipt_resolver, has_parents=bool(payload["parent_refs"]), pc_proof_components=pc_proof_components)
    observed, valid, root_times = payload["observed_at"], payload["valid_until"], payload["observation_root_times"]
    if payload["schema"] == "home-live-fact-1":
        if payload["model_ref"] is not None and payload["model_ref"] not in source["model_refs"]:
            _reject("forbidden")
        if payload["probability"] is not None:
            _reject("uncalibrated_probability")
        return resolved
    if source["registration"] is None or payload["registration"] != source["registration"]:
        _reject("registration_mismatch")
    known_supports = {canonical(ref) for ref in payload["original_receipt_refs"] + payload["parent_refs"]}
    for name, part in payload["parts"].items():
        if not 0 <= part["observed_at"] <= observed or not part["observed_at"] < part["valid_until"] <= valid:
            _reject("invalid_clock")
        if part["observed_at"] not in root_times.values() or any(canonical(ref) not in known_supports for ref in part["support_refs"]):
            _reject("unsupported_geometry")
        if any("observed_at" in ref and ref["observed_at"] > part["observed_at"] for ref in part["support_refs"]):
            _reject("invalid_clock")
        if part["status"] != "supplied":
            continue
        if not part["support_refs"] or part["method_ref"] not in source["method_refs"]:
            _reject("unsupported_geometry")
        policy = source["part_policies"][name]
        if policy is None:
            _reject("missing_measurement_bound")
        uncertainty = part["uncertainty"]
        _uncertainty(uncertainty, directional=name.endswith("_forward"), origin=part["observed_at"])
        normalized = json.loads(canonical(uncertainty))
        del normalized["age"]["origin_at"]
        if normalized != policy or part["valid_until"] - part["observed_at"] > uncertainty["age"]["max_age_s"]:
            _reject("invalid_uncertainty")
        _shape(part["shape"])
        if name in {"body", "protection"}:
            if {item["part_id"] for item in part["shape"]["items"]} != set(source["primitive_ids"][name]):
                _reject("incomplete_geometry")
            if name == "protection" and part["shape"]["policy_ref"] not in source["protection_policy_refs"]:
                _reject("forbidden")
    return resolved
