"""Independent opt-in PC-input proof and clause checks; no I/O or dwell.

The journal owns authentication, source generations, current-head resolution and
clock admission. This module consumes its resolved originals/current parents and
checks the explicit successor policy. A policy pin is a commissioning input, not
an independently measured qualification receipt. Legacy facts gain no authority.
"""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from functools import wraps
import re

from journal import JournalError, canonical, digest, finite


SCOPE = ("site_id", "room_id", "session_id", "domain_epoch")
BINDING_KEYS = set("source_id method_ref association_method_ref qualification_ref input_rule_ref device_id workstation_id subject_ref input_sensor_root input_clock_epoch ha_source_id lastactive_entity geometry_source_id geometry_method_ref proof_component_id max_input_age_s".split())
RULE_KEYS = set("schema version id site_id device_id ha_source_id entities derived".split())
CLOCK_KEYS = set("schema version id site_id device_id input_clock_epoch input_rule_ref method_ref assessment_ref valid_from available_at valid_until offset_s uncertainty_s".split())
PROOF_KEYS = set("schema version id scope source_id source_generation method_ref association_method_ref qualification_ref input_rule_ref device_id workstation_id person_ref subject_ref input_clock_epoch input_root input_at input_valid_until association_ref observed_at available_at ha_event".split())
REF_KEYS = set("stream_epoch cursor producer_id id payload_digest component_id".split())
RECORD_KEYS = set("ref capture_epoch capture_seq observation_root_times sensor_root_bindings pc_input_original".split())
INSTANT = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})\Z")
ENTITY = re.compile(r"[a-z_]+\.[a-z0-9_]{1,100}\Z")
UNUSABLE = {None, "unknown", "unavailable"}


class PCSupportRefusal(ValueError):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _guard(function):
    @wraps(function)
    def checked(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except PCSupportRefusal:
            raise
        except (KeyError, TypeError, ValueError, OverflowError, RecursionError, JournalError):
            raise PCSupportRefusal("malformed_pc_support_material") from None
    return checked


def _need(condition, reason):
    if not condition:
        raise PCSupportRefusal(reason)


def _exact(value, keys, reason):
    _need(isinstance(value, dict) and set(value) == set(keys), reason)


def _id(value):
    _need(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}", value),
          "invalid_pc_identifier")


def _number(value, *, nonnegative=True):
    _need(finite(value) and (not nonnegative or value >= 0), "invalid_pc_number")
    return value


def _pin(value):
    _exact(value, {"id", "digest"}, "invalid_pc_pin")
    _id(value["id"])
    _need(isinstance(value["digest"], str) and re.fullmatch(r"[a-f0-9]{64}", value["digest"]), "invalid_pc_pin")


def _resolve(ref, bodies):
    _pin(ref)
    rows = [body for body in bodies if body["id"] == ref["id"]]
    _need(len(rows) == 1 and digest(rows[0]) == ref["digest"], "pc_policy_body_reference_mismatch")
    return rows[0]


def _entity(value):
    _need(isinstance(value, str) and ENTITY.fullmatch(value) and not value.startswith("derived."), "raw_pc_entity_required")


def _rule(body):
    _exact(body, RULE_KEYS, "closed_pc_rule_required")
    _need(body["schema"] == "home-pc-input-rule-1" and type(body["version"]) is int and body["version"] == 1, "pc_rule_version")
    for k in ("id", "site_id", "device_id", "ha_source_id"):
        _id(body[k])
    entities, derived = body["entities"], body["derived"]
    _need(isinstance(entities, list) and len(entities) == 2 and len(set(entities)) == 2, "two_raw_pc_entities_required")
    for entity in entities:
        _entity(entity)
    _exact(derived, {"computer_active", "computer_input_recency"}, "pc_derived_rules_required")
    a, r = derived["computer_active"], derived["computer_input_recency"]
    common = {"session_entity", "last_active_entity", "unlocked_values"}
    _exact(a, common | {"max_idle_seconds"}, "pc_active_v1_rule_required")
    _exact(r, common | {"rule"}, "pc_recency_v3_rule_required")
    _need(r["rule"] == "computer_active_v3" and all(a[k] == r[k] for k in common), "same_pc_raw_rule_inputs_required")
    _need({a["session_entity"], a["last_active_entity"]} == set(entities), "pc_rule_entity_binding")
    values = a["unlocked_values"]
    _need(isinstance(values, list) and 1 <= len(values) <= 4 and len(set(values)) == len(values)
          and all(isinstance(v, str) and 1 <= len(v) <= 32 and v not in UNUSABLE for v in values), "pc_unlocked_values_required")
    _need(30 <= _number(a["max_idle_seconds"]) <= 3600, "bounded_pc_idle_rule_required")


def _clock(body):
    _exact(body, CLOCK_KEYS, "closed_pc_clock_qualification_required")
    _need(body["schema"] == "home-pc-input-clock-qualification-1" and type(body["version"]) is int and body["version"] == 1, "pc_clock_version")
    for key in ("id", "site_id", "device_id", "input_clock_epoch"):
        _id(body[key])
    for key in ("input_rule_ref", "method_ref", "assessment_ref"):
        _pin(body[key])
    _need(_number(body["valid_from"]) <= _number(body["available_at"]) < _number(body["valid_until"]), "pc_clock_interval")
    _number(body["offset_s"], nonnegative=False)
    _number(body["uncertainty_s"])


@_guard
def validate_pc_support_policy(policy, sources):
    """Validate fixed bodies against commissioned sources; return a copy."""
    _exact(policy, {"schema", "version", "bindings", "input_rules", "clock_qualifications"}, "closed_pc_support_policy_required")
    _need(policy["schema"] == "home-live-pc-support-policy-1" and type(policy["version"]) is int and policy["version"] == 1,
          "pc_support_policy_version")
    for key in ("bindings", "input_rules", "clock_qualifications"):
        _need(isinstance(policy[key], list) and 1 <= len(policy[key]) <= 16, "bounded_pc_policy_required")
    _need(isinstance(sources, dict), "commissioned_pc_sources_required")
    for key, check in (("input_rules", _rule), ("clock_qualifications", _clock)):
        for body in policy[key]:
            check(body)
        _need(len({b["id"] for b in policy[key]}) == len(policy[key]), "duplicate_pc_policy_body")
    seen, epochs = set(), {}
    for b in policy["bindings"]:
        _exact(b, BINDING_KEYS, "closed_pc_binding_required")
        for key in BINDING_KEYS - {"method_ref", "association_method_ref", "qualification_ref", "input_rule_ref", "subject_ref", "geometry_method_ref", "max_input_age_s"}:
            _id(b[key])
        for key in ("method_ref", "association_method_ref", "qualification_ref", "input_rule_ref", "geometry_method_ref"):
            _pin(b[key])
        _exact(b["subject_ref"], {"subject_id", "subject_epoch"}, "pc_subject_epoch_required")
        for v in b["subject_ref"].values():
            _id(v)
        _entity(b["lastactive_entity"])
        _need(0 < _number(b["max_input_age_s"]) <= 15, "pc_input_age_outside_v3_horizon")
        source, geometry = sources.get(b["source_id"]), sources.get(b["geometry_source_id"])
        _need(source is not None and geometry is not None
              and b["method_ref"] in source["method_refs"]
              and b["association_method_ref"] in source["association_method_refs"]
              and b["association_method_ref"] in geometry["association_method_refs"]
              and b["geometry_method_ref"] in geometry["method_refs"]
              and b["proof_component_id"] in source["receipt_component_ids"]
              and b["input_sensor_root"] in source["sensor_roots"], "pc_binding_not_commissioned")
        key = canonical([b["source_id"], b["method_ref"]])
        _need(key not in seen, "duplicate_pc_support_binding")
        seen.add(key)
        rule = _resolve(b["input_rule_ref"], policy["input_rules"])
        clock = _resolve(b["qualification_ref"], policy["clock_qualifications"])
        _need(all(rule[k] == b[k] for k in ("device_id", "ha_source_id"))
              and rule["derived"]["computer_active"]["last_active_entity"] == b["lastactive_entity"]
              and all(clock[k] == b[k] for k in ("device_id", "input_clock_epoch", "input_rule_ref", "method_ref"))
              and clock["site_id"] == rule["site_id"], "pc_rule_clock_binding_mismatch")
        epoch = (b["device_id"], b["input_clock_epoch"])
        mapping = (clock["offset_s"], clock["uncertainty_s"])
        _need(epoch not in epochs or epochs[epoch] == mapping, "pc_mapping_change_requires_new_epoch")
        epochs[epoch] = mapping
    _need(len(canonical(policy).encode()) <= 128 * 1024, "pc_policy_budget_exhausted")
    return deepcopy(policy)


def _instant(value):
    """Exact ISO microseconds, preserving offset before the one wire rounding."""
    _need(isinstance(value, str) and INSTANT.fullmatch(value), "offset_qualified_pc_lastactive_required")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
        delta = parsed.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
        result = Decimal(delta.days * 86400 + delta.seconds) + Decimal(delta.microseconds) / Decimal(1000000)
    except (ValueError, OverflowError):
        raise PCSupportRefusal("offset_qualified_pc_lastactive_required") from None
    _need(result >= 0, "nonnegative_pc_input_time_required")
    return result


def _derived(rule, facts, observed):
    """Independent reconstruction of incumbent computer_active_v1/v3 records."""
    a = rule["derived"]["computer_active"]
    session, last = facts[a["session_entity"]]["state"], facts[a["last_active_entity"]]["state"]
    stamp = None
    try:
        stamp = float(_instant(last))
    except PCSupportRefusal:
        pass
    idle = None if stamp is None else observed - stamp
    active = (None if session in UNUSABLE or idle is None or idle < -5 else
              "on" if session in a["unlocked_values"] and idle <= a["max_idle_seconds"] else "off")
    recency = (None if session in UNUSABLE or session.lower() in UNUSABLE or idle is None or idle < -5 else
               "locked" if session not in a["unlocked_values"] else
               next((word for bound, word in ((15, "under_15s"), (60, "15_to_60s"), (300, "1_to_5_min")) if idle <= bound), "over_5_min"))
    inputs = [a["session_entity"], a["last_active_entity"]]
    return {
        "derived.computer_active": {"state": active, "status": "unknown" if active is None else "known",
            "provenance": {"inputs": inputs, "rule": "computer_active_v1", "max_idle_seconds": a["max_idle_seconds"]}},
        "derived.computer_input_recency": {"state": recency, "status": "unknown" if recency is None else "known",
            "provenance": {"inputs": inputs, "rule": "computer_active_v3"}}}


def _binding(fact, policy):
    matches = [b for b in policy["bindings"] if b["source_id"] == fact.get("source_id") and b["method_ref"] == fact.get("method_ref")]
    _need(len(matches) == 1, "pc_fact_not_policy_bound")
    return matches[0]


def _proof(fact, original, policy, *, now=None):
    _need(fact.get("schema") == "home-live-fact-1" and fact.get("kind") == "pc_input_interaction"
          and fact.get("status") == "supplied" and fact.get("value") == "attributed_recent"
          and fact.get("reason") == "qualified_pc_input_attribution", "attributed_pc_fact_required")
    b = _binding(fact, policy)
    rule, clock = _resolve(b["input_rule_ref"], policy["input_rules"]), _resolve(b["qualification_ref"], policy["clock_qualifications"])
    _exact(original, PROOF_KEYS, "complete_pc_original_required")
    _need(len(canonical(original).encode()) <= 128 * 1024, "pc_original_budget_exhausted")
    _need(original["schema"] == "home-original-pc-input-1" and type(original["version"]) is int and original["version"] == 1,
          "pc_original_version")
    _id(original["id"])
    scope = {k: fact[k] for k in SCOPE}
    _need(original["scope"] == scope and rule["site_id"] == clock["site_id"] == scope["site_id"], "pc_original_scope_mismatch")
    for k in ("source_id", "source_generation", "method_ref", "person_ref", "subject_ref"):
        _need(original[k] == fact[k], "pc_original_fact_binding_mismatch")
    for k in ("association_method_ref", "qualification_ref", "input_rule_ref", "device_id", "workstation_id", "subject_ref", "input_clock_epoch"):
        _need(original[k] == b[k], "pc_original_policy_binding_mismatch")
    person, association = fact.get("person_ref"), fact.get("association") or {}
    _need(isinstance(person, dict) and set(person) == {*SCOPE, "pid", "track_epoch"}
          and all(person[k] == scope[k] for k in SCOPE) and fact.get("subject_ref") == b["subject_ref"]
          and association.get("person_status") == association.get("subject_status") == "qualified"
          and association.get("method_ref") == b["association_method_ref"]
          and association.get("candidate_person_refs") == association.get("candidate_subject_refs") == [], "qualified_pc_person_work_required")
    _id(person["track_epoch"])
    _need(type(person["pid"]) is int and person["pid"] >= 1 or isinstance(person["pid"], str), "pc_anonymous_person_required")
    if isinstance(person["pid"], str):
        _id(person["pid"])
    h_event = original["ha_event"]
    _need(isinstance(h_event, dict) and h_event.get("kind") == "ha.observation"
          and type(h_event.get("id")) is int and 1 <= h_event["id"] <= 9007199254740991
          and h_event.get("session_id") == scope["session_id"] and isinstance(h_event.get("payload"), dict), "original_pc_ha_event_required")
    ha = h_event["payload"]
    _need(ha.get("site_id") == scope["site_id"] and ha.get("session_id") == scope["session_id"]
          and (ha.get("quality") or {}).get("source_id") == b["ha_source_id"]
          and isinstance(ha.get("snapshot_key"), str) and ha.get("evidence_roots") == [ha["snapshot_key"]]
          and isinstance(ha.get("ha"), dict), "pc_ha_scope_or_root_mismatch")
    observed, available, valid = (_number(ha.get(k)) for k in ("observed_at", "available_at", "valid_until"))
    _need(observed <= available < valid and 0 <= _number(ha.get("acquisition_duration_s")) <= 2,
          "pc_ha_original_clock_mismatch")
    _need(ha.get("snapshot_digest") == digest({k: ha[k] for k in ("observed_at", "ha", "acquisition_duration_s")}), "pc_ha_original_digest_mismatch")
    facts = ha["ha"]
    for entity in rule["entities"]:
        value = facts.get(entity)
        _need(isinstance(value, dict) and value.get("status") == "known" and isinstance(value.get("state"), str)
              and 1 <= len(value["state"]) <= 128 and value["state"] not in UNUSABLE, "known_pc_raw_states_required")
    derived = _derived(rule, facts, observed)
    _need(all(facts.get(k) == v for k, v in derived.items())
          and derived["derived.computer_active"]["state"] == "on"
          and derived["derived.computer_input_recency"]["state"] == "under_15s", "pc_raw_derived_rule_mismatch")
    raw_time = _instant(facts[b["lastactive_entity"]]["state"])
    input_at = float(raw_time + Decimal(str(clock["offset_s"])))
    uncertainty = clock["uncertainty_s"]
    root = "pc-input:" + digest([b["device_id"], b["input_clock_epoch"], input_at])[:40]
    until = min(input_at + b["max_input_age_s"] - uncertainty, clock["valid_until"])
    _need(original["input_at"] == input_at and original["input_root"] == root
          and original["input_valid_until"] == until, "pc_original_input_clock_mismatch")
    _need(clock["valid_from"] <= input_at - uncertainty <= input_at + uncertainty <= observed <= available < clock["valid_until"]
          and clock["available_at"] <= original["available_at"], "pc_input_clock_not_applicable")
    _need(original["observed_at"] == observed <= fact["observed_at"]
          and available <= original["available_at"] <= fact["available_at"]
          and original["observed_at"] <= original["available_at"] < until
          and fact["valid_until"] <= min(until, valid), "pc_fact_original_horizon_mismatch")
    _need(original["association_ref"] in fact.get("parent_refs", [])
          and original["association_ref"].get("component_id") == "parts.work", "pc_work_parent_reference_required")
    times, bindings = fact.get("observation_root_times", {}), fact.get("sensor_root_bindings", {})
    _need(times.get(root) == input_at and bindings.get(root) == [b["input_sensor_root"]]
          and all(times.get(r) == observed and b["input_sensor_root"] in bindings.get(r, []) for r in ha["evidence_roots"]),
          "pc_original_input_root_binding_mismatch")
    if any(isinstance(v, dict) and v.get("origin") == "same_camera_visual" for v in facts.values()):
        _need(all(set(bindings.get(r, [])) - {b["input_sensor_root"]} for r in ha["evidence_roots"]),
              "pc_mixed_ha_root_correlation_required")
    if now is not None:
        _need(fact["available_at"] <= _number(now) < min(fact["valid_until"], until, clock["valid_until"]), "pc_support_expired_or_not_available")
    return {"binding": b, "rule": rule, "clock": clock, "input_at": input_at,
            "input_root": root, "input_valid_until": until, "ha": ha}


@_guard
def validate_pc_input_fact(payload, source, policy, records, parent_publications, *, now):
    """Check resolved private proof and current work parent; does not resolve I/O.

    parent_publications must already have passed the journal's exact grant,
    epoch, current-head, component and source-generation checks in its transaction.
    This function cannot turn arbitrary dictionaries into current publications.
    """
    _need(isinstance(records, (list, tuple)) and len(records) <= 64
          and isinstance(parent_publications, (list, tuple)) and len(parent_publications) <= 64, "bounded_pc_resolved_material_required")
    b = _binding(payload, policy)
    matching = [r for r in records if isinstance(r, dict) and isinstance(r.get("ref"), dict)
                and r["ref"].get("component_id") == b["proof_component_id"]]
    _need(len(matching) == 1, "one_fixed_pc_original_required")
    record = matching[0]
    _exact(record, RECORD_KEYS, "closed_pc_receipt_record_required")
    original, ref = record["pc_input_original"], record["ref"]
    _need(ref in payload["original_receipt_refs"] and ref["store_id"] in source["receipt_store_ids"]
          and ref["component_id"] in source["receipt_component_ids"]
          and ref["clock_mapping_id"] == source["capture_clock"]["mapping_id"]
          and record["capture_epoch"] == source["capture_epoch"]
          and record["capture_seq"] == payload["head"]["position"]["capture_seq"], "pc_original_receipt_binding_mismatch")
    checked = _proof(payload, original, policy, now=now)
    _need(ref["receipt_id"] == original["id"] and ref["digest"] == digest(original)
          and ref["observed_at"] == original["observed_at"] and ref["available_at"] == original["available_at"], "pc_original_receipt_digest_mismatch")
    needed_times = {checked["input_root"]: checked["input_at"],
                    **{root: checked["ha"]["observed_at"] for root in checked["ha"]["evidence_roots"]}}
    _need(record["observation_root_times"] == needed_times
          and record["sensor_root_bindings"] == {r: payload["sensor_root_bindings"][r] for r in needed_times}, "pc_receipt_original_roots_mismatch")
    work_ref = original["association_ref"]
    _exact(work_ref, REF_KEYS, "exact_pc_work_publication_reference_required")
    parents = [p for p in parent_publications if isinstance(p, dict)
               and all(p.get(k) == work_ref[k] for k in REF_KEYS - {"component_id"})]
    _need(len(parents) == 1, "current_pc_work_publication_required")
    publication = parents[0]
    parent, work = publication.get("payload", {}), publication.get("payload", {}).get("parts", {}).get("work", {})
    _need(publication.get("schema") == "home-context-publication-1"
          and publication.get("payload_type") == "home-live-geometry-1"
          and publication["payload_digest"] == digest(parent)
          and parent.get("source_id") == b["geometry_source_id"] and parent.get("method_ref") == b["geometry_method_ref"]
          and parent.get("status") == work.get("status") == "supplied"
          and parent.get("manifest_ref") == payload["manifest_ref"]
          and all(parent.get(k) == payload[k] for k in (*SCOPE, "person_ref", "subject_ref"))
          and parent.get("association", {}).get("person_status") == parent.get("association", {}).get("subject_status") == "qualified"
          and parent.get("association", {}).get("method_ref") == b["association_method_ref"], "pc_work_association_mismatch")
    _need(publication["committed_at"] <= original["available_at"]
          and parent["available_at"] <= original["available_at"]
          and payload["valid_until"] <= min(parent["valid_until"], work["valid_until"],
              payload["observed_at"] + source["max_age_s"])
          and now + payload["clock"]["uncertainty_s"] < min(payload["valid_until"], parent["valid_until"], work["valid_until"]),
          "pc_work_original_lease_mismatch")
    return deepcopy({"input_at": checked["input_at"], "input_root": checked["input_root"],
                     "input_valid_until": checked["input_valid_until"], "original": original})


@_guard
def pc_support_clause(fact, original, policy, *, now=None):
    """Return one input-only progression clause for an already admitted fact."""
    if fact.get("kind") != "pc_input_interaction" or fact.get("status") != "supplied" or fact.get("value") != "attributed_recent":
        return None
    checked = _proof(fact, original, policy, now=now)
    _need(set(fact["observation_roots"]) == set(fact["observation_root_times"]) == set(fact["sensor_root_bindings"])
          and max(fact["observation_root_times"].values()) == fact["observed_at"], "pc_clause_complete_ancestry_required")
    return {"id": "clause:" + digest({"kind": "pc_input_interaction", "ids": [fact["id"]]})[:24],
        "kind": "pc_input_interaction", "fact_ids": [fact["id"]], "source_ids": [fact["source_id"]],
        "sensor_roots": sorted(set(fact["sensor_roots"])), "observation_roots": sorted(fact["observation_roots"]),
        "progression_roots": [checked["input_root"]], "parent_evidence_ids": sorted({r["id"] for r in fact["parent_refs"]}),
        "observed_at": checked["input_at"], "valid_until": min(fact["valid_until"], checked["input_valid_until"]),
        "qualification": "qualified_pc_input", "progression_start_at": checked["input_at"]}
