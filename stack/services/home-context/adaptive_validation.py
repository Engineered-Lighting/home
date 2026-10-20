"""Closed validation and admission for the isolated adaptive synthetic profile.

Service eligibility omits consumer receipt until authenticated delivery. Worker
proofs preserve original receipts and immutable publication/component bodies.
"""
from __future__ import annotations

import copy
import json
import math
import re
import sys
import uuid

from journal import ID, JournalError, bounded_json, canonical, digest, finite, identifier


SERVICE_PROFILE = "adaptive-book-synthetic-1"
CLOCK_PROFILE = "relative-session-seconds-1"
_MISSING = object()
SCOPE_KEYS = ("site_id", "room_id", "session_id", "domain_epoch")
REVISION_FIELDS = {"home-preference-snapshot-1": "preference_set_id",
                   "home-goal-declaration-1": "declaration_id",
                   "home-experience-goal-1": "goal_id"}
MAXIMUM_REVISION_ROWS = 96
_DIGEST = re.compile(r"[a-f0-9]{64}\Z")


def _identifier(value):
    return isinstance(value, str) and ID.fullmatch(value) is not None


def _error(code):
    raise JournalError(code, code.replace("_", " "))


def create_clock_profile(*, session_id, origin_utc_s=None, utc_source_clocks=_MISSING):
    """Construct an immutable, digest-bound relative clock configuration."""
    clocks = [] if utc_source_clocks is _MISSING else utc_source_clocks
    if (not _identifier(session_id)
            or origin_utc_s is not None and (not finite(origin_utc_s) or origin_utc_s < 0)
            or not isinstance(clocks, list) or len(clocks) > 8
            or any(not _identifier(value) for value in clocks)
            or len(set(clocks)) != len(clocks)
            or origin_utc_s is None and clocks):
        _error("adaptive_clock_profile")
    profile = {"id": "clock:" + session_id, "kind": CLOCK_PROFILE,
               "session_id": session_id, "maximum_t": 120,
               "origin_utc_s": origin_utc_s, "utc_source_clocks": sorted(clocks)}
    return {**profile, "digest": digest(profile)}


def assert_clock_profile(profile):
    keys = {"id", "kind", "session_id", "maximum_t", "origin_utc_s", "utc_source_clocks", "digest"}
    if not isinstance(profile, dict) or set(profile) != keys:
        _error("adaptive_clock_profile")
    expected = create_clock_profile(session_id=profile["session_id"],
                                    origin_utc_s=profile["origin_utc_s"],
                                    utc_source_clocks=profile["utc_source_clocks"])
    if canonical(expected) != canonical(profile):
        _error("adaptive_clock_profile")
    return profile


def _relative_time(value, profile):
    if not finite(value) or not 0 <= value <= profile["maximum_t"]:
        _error("adaptive_clock_range")
    return value


def assert_relative_time(value, profile):
    assert_clock_profile(profile)
    return _relative_time(value, profile)


def normalize_time(*, value, units, source_clock_id, profile):
    """Convert only through an explicit configured source/origin; never infer units."""
    assert_clock_profile(profile)
    if not finite(value) or not _identifier(source_clock_id):
        _error("adaptive_clock_conversion")
    if units == CLOCK_PROFILE:
        if source_clock_id != profile["id"]:
            _error("adaptive_clock_source")
        relative, conversion = _relative_time(value, profile), "identity"
    else:
        if (units != "utc_epoch_seconds" or profile["origin_utc_s"] is None
                or source_clock_id not in profile["utc_source_clocks"]):
            _error("adaptive_clock_source")
        # Match Math.round rather than Python's ties-to-even round.
        scaled = (float(value) - float(profile["origin_utc_s"])) * 1e8
        if not math.isfinite(scaled):
            _error("adaptive_clock_range")
        relative = _relative_time(math.floor(scaled + .5) / 1e8, profile)
        conversion = "explicit-utc-origin-1"
    return {"value": relative, "provenance": {"units": units,
            "source_clock_id": source_clock_id, "original_value": value,
            "profile_digest": profile["digest"], "conversion": conversion}}


def _publication_times(publication, profile):
    assert_clock_profile(profile)
    payload = publication.get("payload") if isinstance(publication, dict) else None
    if (not isinstance(payload, dict) or payload.get("clock_profile_digest") != profile["digest"]
            or payload.get("session_id") != profile["session_id"]):
        _error("adaptive_clock_binding")
    effective = payload.get("effective_at")
    if effective is None:
        effective = payload.get("observed_at", _MISSING)
    times = (effective, payload.get("available_at", _MISSING), publication.get("committed_at", _MISSING))
    if any(value is _MISSING for value in times):
        _error("adaptive_clock_missing")
    for value in times:
        _relative_time(value, profile)
    return times


def server_eligible_at(publication, profile):
    """Server-head eligibility; this never invents a consumer receipt."""
    return max(_publication_times(publication, profile))


def eligible_at(row, profile):
    """Worker eligibility from all four original causal clocks."""
    publication = row.get("publication") if isinstance(row, dict) else None
    times = _publication_times(publication, profile)
    received = row.get("received_at", _MISSING)
    if received is _MISSING:
        _error("adaptive_clock_missing")
    _relative_time(received, profile)
    if received < times[2]:
        _error("adaptive_original_receipt_clock")
    return max(*times, received)


def validate_command_clocks(payload, profile, now):
    """Validate issued command metadata and return its exact envelope clocks.

    Future availability/effect is admitted as pending; issued_at must already
    exist at admission. This helper neither changes nor manufactures timestamps.
    """
    assert_clock_profile(profile)
    _relative_time(now, profile)
    if (not isinstance(payload, dict) or payload.get("clock_profile_digest") != profile["digest"]
            or payload.get("session_id") != profile["session_id"]):
        _error("adaptive_clock_binding")
    names = ("issued_at", "effective_at", "available_at", "valid_until")
    if any(key not in payload for key in names):
        _error("adaptive_clock_missing")
    for key in names:
        _relative_time(payload[key], profile)
    if payload["issued_at"] > now or payload["issued_at"] > payload["available_at"]:
        _error("adaptive_issued_clock")
    if payload["valid_until"] <= max(payload["effective_at"], payload["available_at"]):
        _error("adaptive_clock_interval")
    return {"observed_at": payload["issued_at"], "available_at": payload["available_at"],
            "valid_until": payload["valid_until"]}


def _integer(value, minimum=0, maximum=9007199254740991):
    return type(value) is int and minimum <= value <= maximum


def _scope(value):
    return (isinstance(value, dict) and set(value) == set(SCOPE_KEYS)
            and all(_identifier(value[key]) for key in SCOPE_KEYS))


def _same_scope(identity, value):
    return isinstance(value, dict) and all(identity[key] == value.get(key) for key in SCOPE_KEYS)


def _subject(value):
    return (isinstance(value, dict) and set(value) == {"kind", "id"}
            and value["kind"] in ("session", "household", "room") and _identifier(value["id"]))


def validate_subject_authorization(authorization, identity, *, configured_worker_id=None, allow_read_namespace=False):
    """Validate configured grant data supplied by the authenticated adapter.

    This is not authentication. The caller must obtain this configuration from
    its grant/principal registry, never from a publication's payload.
    """
    if (not _scope(identity) or not isinstance(authorization, dict)
            or set(authorization) != {"owner_id", "producer_id", "scope", "subject_rights"}
            or not _identifier(authorization["owner_id"]) or not _identifier(authorization["producer_id"])
            or not _scope(authorization["scope"]) or not _same_scope(identity, authorization["scope"])
            or not isinstance(authorization["subject_rights"], list)
            or len(authorization["subject_rights"]) > 16):
        _error("adaptive_revision_owner")
    seen = set()
    for rule in authorization["subject_rights"]:
        required = {"subject", "chain_ids", "rights"}
        if (not isinstance(rule, dict) or not required <= set(rule)
                or set(rule) - required - {"derived_goal_namespace"}
                or not _subject(rule["subject"]) or not isinstance(rule["chain_ids"], list)
                or len(rule["chain_ids"]) > MAXIMUM_REVISION_ROWS
                or any(not _identifier(chain) for chain in rule["chain_ids"])
                or len(set(rule["chain_ids"])) != len(rule["chain_ids"])
                or not isinstance(rule["rights"], list) or not rule["rights"]
                or any(right not in ("read", "write", "derive") for right in rule["rights"])
                or len(set(rule["rights"])) != len(rule["rights"])):
            _error("adaptive_revision_subject")
        namespace = rule.get("derived_goal_namespace")
        own_namespace = "goal:" + authorization["producer_id"] + ":"
        read_namespace = "goal:" + configured_worker_id + ":" if configured_worker_id else own_namespace
        read_valid = ("derive" not in rule["rights"] and "read" in rule["rights"]
                      and (namespace == read_namespace or allow_read_namespace and isinstance(namespace, str)
                           and namespace.startswith("goal:") and namespace.endswith(":") and _identifier(namespace[5:-1])))
        if namespace is not None and (not _identifier(namespace) or namespace != own_namespace and not read_valid):
            _error("adaptive_revision_subject")
        rule_key = canonical([rule["subject"], sorted(rule["chain_ids"]), namespace])
        if rule_key in seen:
            _error("adaptive_revision_subject")
        seen.add(rule_key)
    return authorization


def subject_chain_allowed(payload, authorization, identity, *, right, configured_worker_id=None):
    """Check read/write/derive for one exact owner/subject/chain binding."""
    validate_subject_authorization(authorization, identity, configured_worker_id=configured_worker_id)
    field = REVISION_FIELDS.get(payload.get("schema")) if isinstance(payload, dict) else None
    if (right not in ("read", "write", "derive") or field is None
            or not _same_scope(identity, payload) or payload.get("owner_id") != authorization["owner_id"]
            or not _subject(payload.get("subject")) or not _identifier(payload.get(field))):
        return False
    if right == "derive" and payload["schema"] != "home-experience-goal-1":
        return False
    if right == "write" and payload["schema"] == "home-experience-goal-1":
        return False
    for rule in authorization["subject_rights"]:
        if canonical(rule["subject"]) != canonical(payload["subject"]) or right not in rule["rights"]:
            continue
        if payload[field] in rule["chain_ids"]:
            return True
        namespace = rule.get("derived_goal_namespace")
        if (namespace and payload["schema"] == "home-experience-goal-1" and right in ("derive", "read")
                and namespace == "goal:" + (authorization["producer_id"] if right == "derive" else configured_worker_id or authorization["producer_id"]) + ":"
                and payload[field].startswith(namespace) and len(payload[field]) > len(namespace)):
            return True
    return False


def filter_subject_publications(publications, authorization, identity):
    """Pure page/snapshot/lookup filter; a read grant never implies geometry."""
    validate_subject_authorization(authorization, identity)
    if not isinstance(publications, list) or len(publications) > 2048:
        _error("adaptive_revision_budget")
    return [copy.deepcopy(publication) for publication in publications
            if isinstance(publication, dict)
            and subject_chain_allowed(publication.get("payload"), authorization, identity, right="read")]


def revision_chain_key(payload):
    field = REVISION_FIELDS.get(payload.get("schema")) if isinstance(payload, dict) else None
    if field is None:
        _error("adaptive_revision_metadata")
    return canonical([payload["schema"], *(payload[key] for key in SCOPE_KEYS),
                      payload["owner_id"], payload["subject"], payload[field]])


class RevisionStore:
    """Bounded immutable revision CAS, separate admitted and eligible heads.

    The service uses three original clocks. Set consumer_receipts=True only for
    worker/recovery parity, where each row must carry an actual original receipt.
    """

    def __init__(self, *, identity, clock_profile, maximum_rows=96, consumer_receipts=False, configured_worker_id=None):
        assert_clock_profile(clock_profile)
        if (not _scope(identity) or identity["session_id"] != clock_profile["session_id"]
                or not _integer(maximum_rows, 1, MAXIMUM_REVISION_ROWS) or type(consumer_receipts) is not bool
                or configured_worker_id is not None and not _identifier(configured_worker_id)):
            _error("adaptive_revision_configuration")
        self._identity = copy.deepcopy(identity)
        self._clock = copy.deepcopy(clock_profile)
        self._maximum_rows = maximum_rows
        self._consumer_receipts = consumer_receipts
        self._configured_worker_id = configured_worker_id
        self._rows = []

    def _eligibility(self, row):
        return (eligible_at(row, self._clock) if self._consumer_receipts
                else server_eligible_at(row["publication"], self._clock))

    def _check(self, row, authorization):
        publication = row.get("publication") if isinstance(row, dict) else None
        payload = publication.get("payload") if isinstance(publication, dict) else None
        field = REVISION_FIELDS.get(payload.get("schema")) if isinstance(payload, dict) else None
        if (field is None or not _same_scope(self._identity, payload) or payload.get("version") != 1
                or not _identifier(payload.get("id")) or publication.get("id") != payload["id"]
                or publication.get("payload_type") != payload["schema"] or publication.get("payload_version") != "1.0"
                or publication.get("payload_digest") != digest(payload)
                or not _identifier(publication.get("producer_id")) or not _identifier(publication.get("journal_epoch"))
                or not _integer(publication.get("cursor"), 1) or not _identifier(payload.get("owner_id"))
                or not _identifier(payload.get(field)) or not _integer(payload.get("revision"), 0, 95)
                or not _subject(payload.get("subject")) or payload.get("status") not in ("active", "suspended", "ended")):
            _error("adaptive_revision_metadata")
        validate_subject_authorization(authorization, self._identity, configured_worker_id=self._configured_worker_id)
        if authorization["owner_id"] != payload["owner_id"] or authorization["producer_id"] != publication["producer_id"]:
            _error("adaptive_revision_owner")
        right = "derive" if payload["schema"] == "home-experience-goal-1" else "write"
        if not subject_chain_allowed(payload, authorization, self._identity, right=right, configured_worker_id=self._configured_worker_id):
            _error("adaptive_revision_subject")
        self._eligibility(row)
        validate_command_clocks(payload, self._clock, publication["committed_at"])
        predecessor = payload.get("expected_predecessor", _MISSING)
        if predecessor is not None and (not isinstance(predecessor, dict)
                or set(predecessor) != {"revision", "digest"} or not _integer(predecessor["revision"], 0, 95)
                or not isinstance(predecessor["digest"], str) or not _DIGEST.fullmatch(predecessor["digest"])):
            _error("adaptive_revision_predecessor")
        return publication, payload, revision_chain_key(payload)

    def admit(self, row, authorization):
        publication, payload, chain = self._check(row, authorization)
        prior = next((r for r in self._rows if r["publication"]["producer_id"] == publication["producer_id"]
                      and r["publication"]["id"] == publication["id"]), None)
        if prior is not None:
            if canonical(prior["publication"]) != canonical(publication):
                _error("adaptive_revision_event_conflict")
            return {"accepted": False, "retry": True, "row": copy.deepcopy(prior)}
        if (any(r["publication"]["cursor"] == publication["cursor"] for r in self._rows)
                or self._rows and self._rows[0]["publication"]["journal_epoch"] != publication["journal_epoch"]):
            _error("adaptive_revision_journal")
        head = next((r for r in reversed(self._rows) if revision_chain_key(r["publication"]["payload"]) == chain), None)
        expected = ({"revision": head["publication"]["payload"]["revision"],
                     "digest": head["publication"]["payload_digest"]} if head else None)
        if (payload["revision"] != (head["publication"]["payload"]["revision"] + 1 if head else 0)
                or canonical(payload["expected_predecessor"]) != canonical(expected)):
            _error("adaptive_revision_cas")
        if len(self._rows) >= self._maximum_rows:
            _error("adaptive_revision_budget")
        self._rows.append(copy.deepcopy(row))
        return {"accepted": True, "retry": False, "row": copy.deepcopy(row)}

    def materialize(self, t):
        assert_relative_time(t, self._clock)
        heads = {}
        for row in self._rows:
            if self._eligibility(row) > t:
                continue
            payload = row["publication"]["payload"]
            chain = revision_chain_key(payload)
            if chain not in heads or payload["revision"] > heads[chain]["publication"]["payload"]["revision"]:
                heads[chain] = row
        result = []
        for chain, row in sorted(heads.items()):
            payload = row["publication"]["payload"]
            disposition = ("ended" if payload["status"] == "ended" else "expired"
                           if payload["valid_until"] <= t else payload["status"])
            result.append({"key": chain, "row": copy.deepcopy(row), "eligible_at": self._eligibility(row),
                           "disposition": disposition})
        return result

    def snapshot(self):
        return copy.deepcopy({"identity": self._identity, "clock_profile_digest": self._clock["digest"],
                              "maximum_rows": self._maximum_rows, "rows": self._rows})

    def restore(self, snapshot, resolve_authorization):
        if (not isinstance(snapshot, dict) or set(snapshot) != {"identity", "clock_profile_digest", "maximum_rows", "rows"}
                or canonical(snapshot["identity"]) != canonical(self._identity)
                or snapshot["clock_profile_digest"] != self._clock["digest"]
                or snapshot["maximum_rows"] != self._maximum_rows or not isinstance(snapshot["rows"], list)
                or len(snapshot["rows"]) > self._maximum_rows or not callable(resolve_authorization)):
            _error("adaptive_revision_restore")
        trial = RevisionStore(identity=self._identity, clock_profile=self._clock,
                              maximum_rows=self._maximum_rows, consumer_receipts=self._consumer_receipts, configured_worker_id=self._configured_worker_id)
        for row in snapshot["rows"]:
            if trial.admit(row, resolve_authorization(copy.deepcopy(row)))["retry"]:
                _error("adaptive_revision_restore")
        self._rows = trial._rows

    def preflight(self, rows, resolve_authorization):
        """Return a prospective complete state; no live state changes on failure."""
        if not isinstance(rows, list) or len(rows) > self._maximum_rows or not callable(resolve_authorization):
            _error("adaptive_revision_budget")
        trial = RevisionStore(identity=self._identity, clock_profile=self._clock,
                              maximum_rows=self._maximum_rows, consumer_receipts=self._consumer_receipts, configured_worker_id=self._configured_worker_id)
        trial.restore(self.snapshot(), resolve_authorization)
        results = [trial.admit(row, resolve_authorization(copy.deepcopy(row))) for row in rows]
        return {"snapshot": trial.snapshot(), "results": results}


def _person_ref(value, identity):
    return (isinstance(value, dict) and set(value) == {*SCOPE_KEYS, "track_epoch", "pid"}
            and _same_scope(identity, value) and _identifier(value["track_epoch"]) and _integer(value["pid"], 1))


def _object_ref(value, identity):
    return (isinstance(value, dict) and set(value) == {*SCOPE_KEYS, "object_epoch", "oid"}
            and _same_scope(identity, value) and _identifier(value["object_epoch"]) and _identifier(value["oid"]))


def validate_materials(materials, identity):
    keys = {"object_ref", "source_id", "registration_ref", "registry_digest", "registration_generation",
            "uncertainty_correlation_ref", "transform_origin", "transform_origin_m", "estimate_qualification"}
    if not isinstance(materials, list) or len(materials) > 4:
        _error("adaptive_material_configuration")
    objects = set()
    for material in materials:
        if (not isinstance(material, dict) or set(material) != keys or not _object_ref(material["object_ref"], identity)
                or any(not _identifier(material[key]) for key in ("source_id", "registration_ref", "uncertainty_correlation_ref"))
                or not isinstance(material["registry_digest"], str) or not _DIGEST.fullmatch(material["registry_digest"])
                or not _integer(material["registration_generation"])
                or material["transform_origin"] not in ("current_two_page_midpoint", "declared_static_protocol_origin")
                or material["estimate_qualification"] not in ("scripted_exact_static_protocol_pose", "scripted_deliberately_noisy_protocol_pose")):
            _error("adaptive_material_configuration")
        origin = material["transform_origin_m"]
        if (material["transform_origin"] == "current_two_page_midpoint" and origin is not None
                or material["transform_origin"] == "declared_static_protocol_origin"
                and (not isinstance(origin, list) or len(origin) != 3 or any(not finite(value) for value in origin))):
            _error("adaptive_material_configuration")
        key = canonical(material["object_ref"])
        if key in objects:
            _error("adaptive_material_configuration")
        objects.add(key)
    return materials


def validate_configuration(value, registry=None):
    """Verify the full trusted recipe and both immutable material catalogues."""
    keys = {"identity", "clock_profile", "materials", "config_digest", "config", "registered_surfaces"}
    recipe_keys = {"registry", "profile", "clock_profile_digest", "interface_digest", "registered_catalogue_digest", "materials_digest", "profile_mode"}
    if not isinstance(value, dict) or set(value) != keys or not _scope(value["identity"]):
        _error("adaptive_configuration")
    bounded_json(value, 64 * 1024)
    assert_clock_profile(value["clock_profile"])
    validate_materials(value["materials"], value["identity"])
    config = value["config"]
    if (not isinstance(config, dict) or set(config) != recipe_keys or config["profile_mode"] != SERVICE_PROFILE
            or not isinstance(config["registry"], dict) or not isinstance(config["profile"], dict)
            or config["clock_profile_digest"] != value["clock_profile"]["digest"]
            or config["materials_digest"] != digest(value["materials"])
            or config["registered_catalogue_digest"] != digest(value["registered_surfaces"])
            or value["config_digest"] != digest(config)):
        _error("adaptive_configuration_digest")
    if registry is None:
        from schema_registry import SchemaRegistry
        registry = SchemaRegistry((), profile=SERVICE_PROFILE)
    room_registry = config["registry"]
    if (config["interface_digest"] != registry.interface_digest
            or room_registry.get("site_id") != value["identity"]["site_id"] or room_registry.get("room_id") != value["identity"]["room_id"]
            or room_registry.get("digest") != digest({key: body for key, body in room_registry.items() if key != "digest"})
            or any(material["registry_digest"] != room_registry["digest"] for material in value["materials"])):
        _error("adaptive_configuration_binding")
    surfaces = value["registered_surfaces"]
    if not isinstance(surfaces, list) or len(surfaces) > 2 or len({surface.get("registration_ref") for surface in surfaces}) != len(surfaces):
        _error("adaptive_registered_catalogue")
    for surface in surfaces:
        if (not validate_adaptive(surface, registry)["ok"] or surface["schema"] != "home-registered-surface-1"
                or not _same_scope(surface, value["identity"]) or surface["clock_profile_digest"] != value["clock_profile"]["digest"]
                or surface["registry_digest"] != room_registry["digest"]):
            _error("adaptive_registered_catalogue")
    return value


def validate_authority(authority, identity):
    """Check the complete canonical room/source/registration/follow CAS map."""
    if (not _scope(identity) or not isinstance(authority, dict)
            or set(authority) != {"room", "sources", "registrations", "follows"}
            or not isinstance(authority["room"], dict)
            or set(authority["room"]) != {"motion_generation", "motion_allowed"}
            or not _integer(authority["room"]["motion_generation"])
            or type(authority["room"]["motion_allowed"]) is not bool):
        _error("adaptive_authority_shape")
    for field in ("sources", "registrations", "follows"):
        entries = authority[field]
        if not isinstance(entries, list) or len(entries) > 4:
            _error("adaptive_authority_shape")
        keys = []
        for entry in entries:
            if field == "sources":
                good = (isinstance(entry, dict) and set(entry) == {"source_id", "revocation_generation", "revoked"}
                        and _identifier(entry["source_id"]) and _integer(entry["revocation_generation"])
                        and type(entry["revoked"]) is bool)
                key = entry.get("source_id") if isinstance(entry, dict) else None
            elif field == "registrations":
                good = (isinstance(entry, dict) and set(entry) == {"registration_ref", "registration_generation", "registry_digest", "revoked"}
                        and _identifier(entry["registration_ref"]) and _integer(entry["registration_generation"])
                        and isinstance(entry["registry_digest"], str) and _DIGEST.fullmatch(entry["registry_digest"])
                        and type(entry["revoked"]) is bool)
                key = entry.get("registration_ref") if isinstance(entry, dict) else None
            else:
                good = (isinstance(entry, dict) and set(entry) == {"person_ref", "object_ref", "association_generation", "source_id",
                                                               "registration_ref", "follow_generation", "follow_allowed"}
                        and _person_ref(entry["person_ref"], identity) and _object_ref(entry["object_ref"], identity)
                        and _integer(entry["association_generation"]) and _identifier(entry["source_id"])
                        and _identifier(entry["registration_ref"]) and _integer(entry["follow_generation"])
                        and type(entry["follow_allowed"]) is bool)
                key = canonical([entry["person_ref"], entry["object_ref"], entry["association_generation"],
                                 entry["source_id"], entry["registration_ref"]]) if good else None
            if not good:
                _error("adaptive_authority_shape")
            keys.append(key)
        if keys != sorted(set(keys)):
            _error("adaptive_authority_order")
    sources = {row["source_id"] for row in authority["sources"]}
    registrations = {row["registration_ref"] for row in authority["registrations"]}
    if any(row["source_id"] not in sources or row["registration_ref"] not in registrations for row in authority["follows"]):
        _error("adaptive_authority_binding")
    return authority


def assert_authority_cas(expected, current, identity):
    """Compare every applicable fence, including allowed flags, without mutation."""
    validate_authority(expected, identity)
    validate_authority(current, identity)
    if canonical(expected) != canonical(current):
        raise JournalError("adaptive_authority_changed", "adaptive authority changed", 409)
    return copy.deepcopy(current)


def following_allowed(authority, identity, *, person_ref, object_ref, association_generation, source_id, registration_ref):
    """Combine exact scoped following permission with genuine hard fences."""
    validate_authority(authority, identity)
    if (not _person_ref(person_ref, identity) or not _object_ref(object_ref, identity)
            or not _integer(association_generation) or not _identifier(source_id) or not _identifier(registration_ref)):
        _error("adaptive_authority_binding")
    match = next((row for row in authority["follows"] if canonical(row["person_ref"]) == canonical(person_ref)
                  and canonical(row["object_ref"]) == canonical(object_ref)
                  and row["association_generation"] == association_generation and row["source_id"] == source_id
                  and row["registration_ref"] == registration_ref), None)
    source = next((row for row in authority["sources"] if row["source_id"] == source_id), None)
    registration = next((row for row in authority["registrations"] if row["registration_ref"] == registration_ref), None)
    return bool(authority["room"]["motion_allowed"] and match and match["follow_allowed"]
                and source and not source["revoked"] and registration and not registration["revoked"])


def validate_adaptive(value, registry=None):
    """Pinned closed public contract validation, with explicit relative clocks."""
    from schema_registry import ADAPTIVE_NEW_VERSIONS, SchemaRegistry
    if registry is None:
        registry = SchemaRegistry((), profile=SERVICE_PROFILE)
    errors = []
    try:
        registry.validate(value)
    except (JournalError, TypeError, ValueError, UnicodeError):
        return {"ok": False, "errors": ["schema"]}
    if value["schema"] not in ADAPTIVE_NEW_VERSIONS and value["schema"] != "home-context-snapshot-3":
        from dynamic_validation import validate_dynamic
        errors.extend(validate_dynamic(value, registry)["errors"])
    clock_fields = {"observed_at", "available_at", "valid_until", "issued_at", "effective_at", "committed_at",
                    "received_at", "as_of_t", "last_t", "next_semantic_t", "next_control_t", "from_t", "t",
                    "expires_at", "decided_at", "eligible_at", "support_valid_until", "environment_valid_until",
                    "settle_until", "started_at", "last_negative_at", "last_positive_at", "last_observed_at",
                    "support_until", "negative_support_until", "conflict_observed_at", "first_use", "last_use",
                    "last_progress_at", "negative_progress_t"}
    if value["schema"] not in ADAPTIVE_NEW_VERSIONS and value["schema"] != "home-context-snapshot-3":
        def bounded_times(item):
            if isinstance(item, dict):
                for key, child in item.items():
                    if key in clock_fields and child is not None and (not finite(child) or not 0 <= child <= 120):
                        errors.append("relative_clock_range")
                    bounded_times(child)
            elif isinstance(item, list):
                for child in item:
                    bounded_times(child)
        bounded_times(value)
        return {"ok": not errors, "errors": errors}

    def visit(item):
        if isinstance(item, dict):
            for key, item_value in item.items():
                if key in clock_fields and item_value is not None and (not finite(item_value) or not 0 <= item_value <= 120):
                    errors.append("relative_clock_range")
            if (item.get("clock_profile_digest") and value.get("clock_profile_digest")
                    and item["clock_profile_digest"] != value["clock_profile_digest"]):
                errors.append("clock_profile_binding")
            if all(key in item for key in SCOPE_KEYS) and not _same_scope(value, item):
                errors.append("nested_scope")
            if "person_ref" in item and ((item.get("kind") == "person" and item["person_ref"] is None)
                                        or (item.get("kind") == "room" and item["person_ref"] is not None)):
                errors.append("beneficiary_kind")
            if (item.get("kind") == "object" and "registration_ref" in item
                    and (item.get("object_ref") is None or item["registration_ref"] is not None)
                    or item.get("kind") == "registered_region" and "object_ref" in item
                    and (item["object_ref"] is not None or item.get("registration_ref") is None)):
                errors.append("selector_kind")
            if "min_lux" in item and (item["min_lux"] > item.get("desired_lux", item["min_lux"])
                                      or item.get("desired_lux", item["min_lux"]) > item["max_lux"]):
                errors.append("band_order")
            if "lower_lux" in item and (item["lower_lux"] > item["nominal_lux"] or item["nominal_lux"] > item["upper_lux"]):
                errors.append("environment_order")
            if "effective_at" in item and "valid_until" in item and item["valid_until"] <= max(item["effective_at"], item["available_at"]):
                errors.append("ttl")
            if ("issued_at" in item and "available_at" in item and item["issued_at"] > item["available_at"]
                    or "issued_at" in item and "valid_until" in item and item["valid_until"] <= item["issued_at"]):
                errors.append("issued_clock")
            if "revision" in item and "expected_predecessor" in item:
                predecessor = item["expected_predecessor"]
                if ((item["revision"] == 0 and predecessor is not None)
                        or item["revision"] > 0 and (predecessor is None or predecessor["revision"] != item["revision"] - 1)):
                    errors.append("revision_predecessor")
            if item.get("role") == "protected" and "aim_anchor_m" in item and item["aim_anchor_m"] is not None:
                errors.append("protected_anchor")
            if (item.get("role") == "protected" and any(o["kind"] != "exposure_ceiling" for o in item.get("objectives", []))
                    or item.get("role") == "benefit" and any(o["kind"] != "illuminance_band" for o in item.get("objectives", []))):
                errors.append("objective_role")
            if (item.get("role") == "protected" and item.get("target")
                    and (item["target"]["kind"] != "registered_region" or item.get("beneficiary", {}).get("kind") != "room"
                         or item.get("association_generation") is not None)):
                errors.append("protected_subject")
            if "patches" in item and item.get("role") == "protected" and (len(item["patches"]) != 1 or item["patches"][0]["role"] != "protected"):
                errors.append("protected_patch")
            if ("patches" in item and item.get("role") == "benefit"
                    and (len(item["patches"]) != 2 or {p["role"] for p in item["patches"]} != {"left", "right"})):
                errors.append("two_pages_required")
            if "centre_m" in item:
                u, v, normal = item["u"], item["v"], item["normal"]
                dot = lambda a, b: sum(x * y for x, y in zip(a, b))
                cross = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]]
                if (any(abs(dot(a, a) - 1) > 1e-6 for a in (u, v, normal)) or abs(dot(u, v)) > 1e-6
                        or any(abs(a - b) > 1e-6 for a, b in zip(cross, normal))):
                    errors.append("patch_basis")
                points = item["vertices_uv_m"]
                if abs(sum(a[0] * points[(i + 1) % len(points)][1] - a[1] * points[(i + 1) % len(points)][0]
                           for i, a in enumerate(points))) < 1e-8:
                    errors.append("patch_area")
            if (item.get("mode") == "surface_epoch_uniform" and "geometry_ref" in item
                    and (item["geometry_ref"] is not None or item["compatibility_digest"] is not None)
                    or item.get("mode") == "captured_pose" and "geometry_ref" in item
                    and (item["geometry_ref"] is None or item["compatibility_digest"] is None)):
                errors.append("environment_binding")
            if "payload" in item and "payload_type" in item and "cursor" in item:
                publication_errors = validate_adaptive(item["payload"], registry)["errors"]
                if item["payload_digest"] != digest(item["payload"]):
                    publication_errors.append("payload_digest")
                if (item["payload_type"] != item["payload"]["schema"]
                        or item["payload_version"] != str(registry.payload_versions.get(item["payload_type"])) + ".0"):
                    publication_errors.append("payload_type_version")
                if item["id"] != item["payload"]["id"]:
                    publication_errors.append("publication_id")
                if item["payload"].get("issued_at", 0) > item["committed_at"]:
                    publication_errors.append("issued_after_commit")
                errors.extend("nested_publication:" + error for error in publication_errors)
            if "from_t" in item and "t" in item and item["from_t"] > item["t"]:
                errors.append("interval_order")
            if item.get("schema") in {"home-dynamic-geometry-snapshot-2", "home-control-input-cut-2", "home-context-replay-5"}:
                if digest({key: child for key, child in item.items() if key != "digest"}) != item["digest"]:
                    errors.append("digest_mismatch")
            if "authority" in item:
                authority = item["authority"]
                for family in ("sources", "registrations", "follows"):
                    keys = ([e["source_id"] for e in authority[family]] if family == "sources" else
                            [e["registration_ref"] for e in authority[family]] if family == "registrations" else
                            [canonical([e["person_ref"], e["object_ref"], e["association_generation"], e["source_id"], e["registration_ref"]])
                             for e in authority[family]])
                    if keys != sorted(set(keys)):
                        errors.append("authority_keys:" + family)
            if item.get("schema") == "home-lighting-request-4" and item["lease"]:
                lease = item["lease"]
                if (lease["person_ref"] != item["beneficiary"]["person_ref"] or lease["object_ref"] != item["target"]["object_ref"]
                        or lease["association_generation"] != item["association_generation"]):
                    errors.append("lease_subject")
            if item.get("schema") == "home-lighting-control-decision-2":
                if (any(item[key]["t"] != item["t"] for key in ("allocation", "execution"))
                        or item["geometry_snapshot"]["as_of_t"] != item["t"]
                        or any(item[key]["control_seq"] != item["control_seq"] for key in ("allocation", "execution"))):
                    errors.append("control_binding")
            if item.get("schema") == "home-lighting-execution-2":
                if (len(set(item["fixture_ids"])) != 4 or len(set(item["target_ids"])) != len(item["target_ids"])
                        or any(state[8] >= len(item["target_ids"]) for states in [*[row["states"] for row in item["samples"]], item["boundary_state"]] for state in states)):
                    errors.append("execution_dictionary_binding")
                feedback = item["feedback"]
                if (any(feedback[key] != item[key] for key in ("control_seq", "from_t", "t"))
                        or feedback["execution_digest"] != digest({key: child for key, child in item.items() if key != "feedback"})
                        or feedback["sample_digest"] != digest(item["samples"])):
                    errors.append("feedback_execution_binding")
                if (any(sample["t"] <= item["from_t"] or sample["t"] > item["t"] for sample in item["samples"])
                        or any(a["t"] >= b["t"] for a, b in zip(item["samples"], item["samples"][1:]))):
                    errors.append("execution_sample_order")
            if item.get("schema") == "home-lighting-outcome-feedback-1":
                if any(exposure["from_t"] != item["from_t"] or exposure["t"] != item["t"] for exposure in item["exposures"]):
                    errors.append("feedback_interval")
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)
    visit(value)
    name = value["schema"]
    if "revision" in value:
        predecessor = value["expected_predecessor"]
        if ((value["revision"] == 0 and predecessor is not None)
                or value["revision"] > 0 and (predecessor is None or predecessor["revision"] != value["revision"] - 1)):
            errors.append("revision_predecessor")
    if name in {"home-dynamic-geometry-snapshot-2", "home-control-input-cut-2", "home-context-replay-5"}:
        if digest({key: item for key, item in value.items() if key != "digest"}) != value["digest"]:
            errors.append("digest_mismatch")
    if name == "home-lighting-request-4":
        if (value["role"] == "protected" and (value["lease"] is not None or value["follow_mode"] != "registered_static")
                or value["role"] != "protected" and (value["lease"] is None or value["target"]["kind"] != "object"
                                                     or value["beneficiary"]["kind"] != "person")):
            errors.append("lease_role")
    if name == "home-lighting-environment-1" and value["observed_at"] > value["available_at"]:
        errors.append("observation_clock")
    if name == "home-context-replay-5":
        protocol = value["reference_protocol"]
        if ((protocol["mode"] == "ordinary") != protocol["ordinary_acceptance_eligible"]):
            errors.append("reference_diagnostic_admission")
    return {"ok": not errors, "errors": errors}


from dynamic_validation import DynamicProfile, GEOMETRY_PRIORITY, component_id, object_key


ADAPTIVE_INPUTS = frozenset({"home-fact-batch-2", "home-geometry-3", "home-intent-event-3",
                           "home-evidence-request-2", "home-evidence-reply-2", "home-preference-snapshot-1",
                           "home-goal-declaration-1", "home-registered-surface-1", "home-lighting-environment-1",
                           "home-lighting-capability-1", "home-experience-goal-1", "home-lighting-control-decision-2"})
ADAPTIVE_OUTPUTS = frozenset({"home-engagement-state-3", "home-experience-goal-1",
                            "home-lighting-policy-decision-3", "home-lighting-control-decision-2"})
ADAPTIVE_FAMILIES = frozenset({"home-registered-surface-1", "home-lighting-environment-1", "home-lighting-capability-1"})
ADAPTIVE_QUOTAS = {"home-geometry-3": 600, "home-fact-batch-2": 240,
                   "home-lighting-control-decision-2": 600, "home-engagement-state-3": 120,
                   "home-lighting-policy-decision-3": 120, "home-experience-goal-1": 32,
                   "home-preference-snapshot-1": 16, "home-goal-declaration-1": 32,
                   "home-intent-event-3": 32, "home-lighting-environment-1": 120,
                   "home-lighting-capability-1": 4, "home-registered-surface-1": 4,
                   "home-source-health-1": 16, "home-context-revocation-1": 8,
                   "home-evidence-request-2": 8, "home-evidence-reply-2": 8}


def _normalized(publication):
    return {key: publication[key] for key in ("journal_epoch", "cursor", "id", "producer_id", "committed_at",
                                             "payload_type", "payload_version", "payload_digest", "payload")}


def _publication_ref(publication):
    return {key: publication[key] for key in ("journal_epoch", "cursor", "payload_digest")}


class AdaptiveProfile(DynamicProfile):
    """Isolated adaptive journal dialect; the preserved dynamic profile is unchanged."""

    def __init__(self, journal):
        super().__init__(journal)
        self.identity = copy.deepcopy(journal.adaptive_config["identity"])
        self.clock_profile = copy.deepcopy(journal.adaptive_config["clock_profile"])
        self.materials = copy.deepcopy(validate_materials(journal.adaptive_config["materials"], self.identity))
        self.config_digest = journal.adaptive_config["config_digest"]
        workers = {grant.producer_id for grant in journal.grants if "worker_commit" in grant.operations
                   and (self.identity["site_id"], self.identity["room_id"]) in grant.scopes}
        if len(workers) != 1:
            raise ValueError("adaptive profile requires one configured worker producer")
        self._worker_producer = next(iter(workers))
        for grant in journal.grants:
            for stored in grant.adaptive_subjects:
                binding = json.loads(stored)
                if _same_scope(self.identity, binding["scope"]):
                    validate_subject_authorization({**binding, "producer_id": grant.producer_id}, self.identity,
                                                   configured_worker_id=self._worker_producer)
                    if any(rule.get("derived_goal_namespace") is not None and "read" in rule["rights"]
                           and rule["derived_goal_namespace"] != "goal:" + self._worker_producer + ":" for rule in binding["subject_rights"]):
                        raise ValueError("adaptive goal read namespace must bind the configured room worker")
        assert_relative_time(journal.clock(), self.clock_profile)
        sources = sorted({source for grant in journal.grants for source in grant.source_ids})
        registrations = {}
        for grant in journal.grants:
            for ref, source, _, registry_digest, _ in grant.registration_bindings:
                value = {"registration_ref": ref, "registration_generation": 0, "registry_digest": registry_digest, "revoked": False}
                if ref in registrations and registrations[ref][0] != source or ref in registrations and registrations[ref][1] != value:
                    raise ValueError("adaptive registration bindings conflict")
                registrations[ref] = (source, value)
        if len(sources) > 4 or len(registrations) > 4 or any(source not in sources for source, _ in registrations.values()):
            raise ValueError("adaptive source/registration authority exceeds its bound")
        self._registrations = registrations
        if any(material["registration_ref"] not in registrations
               or registrations[material["registration_ref"]][0] != material["source_id"]
               or registrations[material["registration_ref"]][1]["registry_digest"] != material["registry_digest"]
               for material in self.materials):
            raise ValueError("adaptive material lacks its exact configured registration")
        for surface in journal.adaptive_config["registered_surfaces"]:
            binding = registrations.get(surface["registration_ref"])
            if (not binding or binding[0] != surface["source_id"]
                    or binding[1]["registry_digest"] != surface["registry_digest"]):
                raise ValueError("adaptive protected catalogue lacks its exact configured registration")
        initial = {"room": {"motion_generation": 0, "motion_allowed": True},
                   "sources": [{"source_id": source, "revocation_generation": 0, "revoked": False} for source in sources],
                   "registrations": [registrations[ref][1] for ref in sorted(registrations)], "follows": []}
        self.j._set_meta("adaptive-authority", canonical(initial))

    def validate_payload(self, value):
        result = validate_adaptive(value, self.j.schemas)
        if not result["ok"]:
            raise JournalError("invalid_payload", "invalid adaptive contract", errors=result["errors"])
        if all(key in value for key in SCOPE_KEYS) and not _same_scope(self.identity, value):
            raise JournalError("scope_mismatch", "adaptive session identity changed")
        if "clock_profile_digest" in value and value["clock_profile_digest"] != self.clock_profile["digest"]:
            raise JournalError("clock_profile_mismatch", "adaptive clock profile changed")
        return value

    def _scope_check(self, site, room):
        if site != self.identity["site_id"] or room != self.identity["room_id"]:
            raise JournalError("scope_mismatch", "adaptive room identity changed")

    def _authorization(self, payload, grant, right):
        for stored in grant.adaptive_subjects:
            binding = json.loads(stored)
            auth = {**binding, "producer_id": grant.producer_id}
            if subject_chain_allowed(payload, auth, self.identity, right=right, configured_worker_id=self._worker_producer):
                return auth
        raise JournalError("forbidden", "owner, subject or chain is outside the authenticated adaptive grant", 403)

    def _intent_subject(self, payload, grant, right):
        if not _same_scope(self.identity, payload):
            raise JournalError("scope_mismatch", "intent session identity changed")
        for stored in grant.adaptive_subjects:
            binding = json.loads(stored)
            if _same_scope(self.identity, binding["scope"]) and any(
                    rule["subject"] == payload["subject"] and right in rule["rights"] for rule in binding["subject_rights"]):
                return True
        raise JournalError("forbidden", "intent subject is outside the authenticated adaptive grant", 403)

    def _read_allowed(self, publication, grant):
        payload = publication["payload"]
        try:
            if payload["schema"] in REVISION_FIELDS:
                self._authorization(payload, grant, "read")
            elif payload["schema"] == "home-intent-event-3":
                self._intent_subject(payload, grant, "read")
        except JournalError as error:
            if error.code == "forbidden":
                return False
            raise
        return True

    def _original_authorization(self, row):
        p = row["publication"]
        right = "derive" if p["payload_type"] == "home-experience-goal-1" else "write"
        for grant in self.j.grants:
            if grant.producer_id != p["producer_id"]:
                continue
            try:
                return self._authorization(p["payload"], grant, right)
            except JournalError as error:
                if error.code != "forbidden":
                    raise
        raise JournalError("forbidden", "original revision no longer has its configured writer", 403)

    def _revision_store(self):
        store = RevisionStore(identity=self.identity, clock_profile=self.clock_profile, configured_worker_id=self._worker_producer)
        for row in self.j.db.execute("SELECT body FROM publications WHERE payload_type IN ('home-preference-snapshot-1','home-goal-declaration-1','home-experience-goal-1') ORDER BY cursor"):
            item = {"publication": _normalized(json.loads(row[0]))}
            store.admit(item, self._original_authorization(item))
        return store

    def _registration_for_geometry(self, payload):
        matches = sorted({material["registration_ref"] for item in payload["objects"] for material in self.materials
                          if material["object_ref"]["oid"] == item["object_ref"]["oid"] and _same_scope(material["object_ref"], item["object_ref"])
                          and material["source_id"] == payload["capture"]["source_id"]
                          and material["registry_digest"] == payload["registry_digest"]})
        if len(matches) != 1:
            raise JournalError("registration_mismatch", "geometry requires one exact configured registration")
        return matches[0]

    def _follow_key(self, entry):
        return canonical([entry["person_ref"], entry["object_ref"], entry["association_generation"],
                          entry["source_id"], entry["registration_ref"]])

    def authority(self, site, room):
        self._scope_check(site, room)
        current = json.loads(self.j._meta("adaptive-authority"))
        follows = {}
        for row in self._qualified_components(site, room, self.j.clock()):
            if row["payload_type"] != "home-geometry-3":
                continue
            payload, component = row["publication"]["payload"], row["component"]
            if component["person_ref"] is None:
                continue
            entry = {"person_ref": component["person_ref"], "object_ref": component["object_ref"],
                     "association_generation": component["association_generation"], "source_id": payload["capture"]["source_id"],
                     "registration_ref": self._registration_for_geometry(payload), "follow_generation": 0, "follow_allowed": True}
            key = self._follow_key(entry)
            saved = self.j._meta("adaptive-follow:" + key)
            if saved:
                entry.update(json.loads(saved))
            follows[key] = entry
        current["follows"] = [follows[key] for key in sorted(follows)]
        validate_authority(current, self.identity)
        return current

    def _heads(self, event):
        if event["payload_type"] != "home-geometry-3":
            return
        payload = event["payload"]
        ref = self._registration_for_geometry(payload)
        authority = json.loads(self.j._meta("adaptive-authority"))
        registration = next(r for r in authority["registrations"] if r["registration_ref"] == ref)
        if payload["registration_generation"] < registration["registration_generation"]:
            raise JournalError("registration_regression", "obsolete geometry registration", 409)

    def _refresh_heads(self, site, room):
        self._scope_check(site, room)
        authority = json.loads(self.j._meta("adaptive-authority"))
        for row in self._qualified_components(site, room, self.j.clock()):
            if row["payload_type"] == "home-geometry-3":
                body = row["publication"]["payload"]
                ref = self._registration_for_geometry(body)
                registration = next(r for r in authority["registrations"] if r["registration_ref"] == ref)
                registration["registration_generation"] = max(registration["registration_generation"], body["registration_generation"])
        self.j._set_meta("adaptive-authority", canonical(authority))

    def _storage_budget(self, reserved=False):
        count, size = self.j.db.execute("SELECT count(*),coalesce(sum(length(CAST(body AS BLOB))),0) FROM publications").fetchone()
        pages = self.j.db.execute("PRAGMA page_count").fetchone()[0] * self.j.db.execute("PRAGMA page_size").fetchone()[0]
        if (count > min(self.j.limits.max_publications, 2048 if reserved else 1928)
                or size > min(self.j.limits.max_publication_bytes, (12 if reserved else 11) * 1024 * 1024)
                or pages > min(self.j.limits.max_db_bytes, (48 if reserved else 47) * 1024 * 1024)):
            raise JournalError("journal_full", "adaptive reservation exhausted; no evidence was evicted", 507)

    def source(self, event):
        if event["payload_type"] == "home-registered-surface-1":
            return event["payload"]["source_id"]
        return DynamicProfile.source(event)

    def append(self, event, *, context_seq=None, status="accepted", grant=None):
        j, db = self.j, self.j.db
        retry = j._receipt(event)
        if retry:
            return retry
        kind = event["payload_type"]
        if kind not in ADAPTIVE_QUOTAS:
            raise JournalError("unsupported_schema", "payload is outside the adaptive publication allowlist")
        if db.execute("SELECT count(*) FROM publications WHERE payload_type=?", (kind,)).fetchone()[0] >= ADAPTIVE_QUOTAS[kind]:
            raise JournalError("journal_full", "adaptive per-type reservation exhausted", 507)
        if kind in {"home-intent-event-3", "home-goal-declaration-1"} and db.execute(
                "SELECT count(*) FROM publications WHERE payload_type IN ('home-intent-event-3','home-goal-declaration-1')").fetchone()[0] >= 32:
            raise JournalError("journal_full", "combined instruction/declaration reservation exhausted", 507)
        if kind in {"home-geometry-3", "home-fact-batch-2"}:
            self._roots(event)
        source = self.source(event)
        if j._revoked(event["site_id"], event["room_id"], source, event["revision"]) and kind != "home-context-revocation-1":
            raise JournalError("revoked", "source or revision is revoked", 409)
        if db.execute("SELECT count(*) FROM receipts").fetchone()[0] >= j.limits.max_receipts:
            raise JournalError("journal_full", "durable identity budget exhausted", 507)
        cursor, committed = int(j._meta("cursor")) + 1, j.clock()
        publication = {"schema": "home-context-publication-1", "id": event["id"], "producer_id": event["producer_id"],
                       "stream_epoch": j._meta("epoch"), "journal_epoch": j._meta("epoch"), "cursor": cursor,
                       "context_seq": cursor if context_seq is None else context_seq, "committed_at": committed,
                       "payload_type": kind, "payload_version": event["payload_version"],
                       "payload": event["payload"], "payload_digest": digest(event["payload"])}
        if kind in REVISION_FIELDS:
            if grant is None:
                raise JournalError("forbidden", "revision admission requires its authenticated grant", 403)
            authorization = self._authorization(event["payload"], grant, "derive" if kind == "home-experience-goal-1" else "write")
            self._revision_store().admit({"publication": _normalized(publication)}, authorization)
        body = bounded_json(publication, j.limits.max_body_bytes)
        db.execute("INSERT INTO publications VALUES(?,?,?,?,?,?,?)", (cursor, event["site_id"], event["room_id"], source, event["revision"], kind, body))
        receipt = {"schema": "home-context-receipt-1", "id": event["id"], "producer_id": event["producer_id"],
                   "stream_epoch": j._meta("epoch"), "cursor": cursor, "committed_at": committed,
                   "payload_digest": publication["payload_digest"], "status": status}
        stored = j._tombstone(event) if kind in ADAPTIVE_OUTPUTS else event
        db.execute("INSERT INTO receipts(producer_id,source_epoch,event_id,digest,receipt,event) VALUES(?,?,?,?,?,?)",
                   (event["producer_id"], event["stream_epoch"], event["id"], digest(event), canonical(receipt), canonical(stored)))
        key = canonical([event["site_id"], event["room_id"], source, kind, event["payload"].get("id", "room")])
        db.execute("INSERT INTO state VALUES(?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET revision=excluded.revision,valid_until=excluded.valid_until,body=excluded.body",
                   (key, event["site_id"], event["room_id"], source, event["revision"], event["valid_until"], body))
        j._set_meta("cursor", cursor)
        self._heads(event)
        self._refresh_heads(event["site_id"], event["room_id"])
        self._storage_budget(kind in {"home-context-revocation-1", "home-evidence-reply-2", "home-source-health-1", "home-evidence-request-2"})
        return receipt

    def _event_for(self, payload, grant=None, *, producer_id=None, valid_until=None):
        t = payload.get("issued_at", payload.get("observed_at", payload.get("available_at", payload.get("t", self.j.clock()))))
        version = self.j.schemas.payload_versions[payload["schema"]]
        return {"schema": "home-context-event-1", "schema_version": "1.0", "id": payload["id"],
                "producer_id": producer_id or grant.producer_id, "stream_epoch": self.j._meta("epoch"), **self.identity,
                "observed_at": t, "available_at": payload.get("available_at", t),
                "valid_until": min(120, valid_until if valid_until is not None else payload.get("valid_until", t + 3)),
                "payload_type": payload["schema"], "payload_version": str(version) + ".0", "revision": str(version) + ".0", "payload": payload}

    def append_event(self, event, grant):
        self.j._event(event)
        name, payload = event["payload_type"], event["payload"]
        grant.require("event", event["site_id"], event["room_id"], payload_type=name, revision=event["revision"], producer_id=event["producer_id"])
        if name in ADAPTIVE_OUTPUTS:
            raise JournalError("worker_commit_required", "shared adaptive outputs require one atomic worker commit")
        if name not in {"home-fact-batch-2", "home-geometry-3", "home-source-health-1", *ADAPTIVE_FAMILIES}:
            raise JournalError("typed_route_required", "adaptive command requires its typed route")
        if name in {"home-fact-batch-2", "home-geometry-3"}:
            if any(event[k] != payload[k] for k in ("observed_at", "available_at", "valid_until")):
                raise JournalError("invalid_clock", "source envelope clocks differ from original body")
            records = payload["facts"] if name == "home-fact-batch-2" else [payload]
            if any(record["capture"]["source_id"] not in grant.source_ids for record in records):
                raise JournalError("forbidden", "capture source outside its explicit grant", 403)
            if name == "home-geometry-3":
                bounded_json(payload, 4096)
                if not any(binding[1] == payload["capture"]["source_id"] and binding[3] == payload["registry_digest"] for binding in grant.registration_bindings):
                    raise JournalError("forbidden", "geometry registry outside its explicit grant", 403)
        elif name in ADAPTIVE_FAMILIES:
            if name == "home-lighting-environment-1":
                if payload["source_id"] not in grant.source_ids or any(event[k] != payload[k] for k in ("observed_at", "available_at", "valid_until")):
                    raise JournalError("forbidden", "environment source or original clocks outside grant", 403)
            else:
                clocks = validate_command_clocks(payload, self.clock_profile, self.j.clock())
                if any(event[k] != clocks[k] for k in clocks):
                    raise JournalError("invalid_clock", "command envelope clocks differ from issued metadata")
            if name == "home-registered-surface-1" and (payload["source_id"] not in grant.source_ids or not any(
                    binding[0] == payload["registration_ref"] and binding[1] == payload["source_id"]
                    and binding[3] == payload["registry_digest"] for binding in grant.registration_bindings)):
                raise JournalError("forbidden", "registered geometry requires its distinct commissioned grant", 403)
            if name == "home-registered-surface-1":
                expected = next((surface for surface in self.j.adaptive_config["registered_surfaces"] if surface["registration_ref"] == payload["registration_ref"]), None)
                if not expected or any(payload[key] != expected[key] for key in ("source_id", "region_id", "region_epoch", "registration_generation", "registry_digest", "patches", "transform_origin_m", "uncertainty", "uncertainty_correlation_ref", "qualification")):
                    raise JournalError("registration_mismatch", "registered geometry differs from the configuration-bound catalogue", 409)
            elif name == "home-lighting-capability-1":
                profile = self.j.adaptive_config["config"]["profile"]
                if (payload["profile_ref"] != profile.get("profile_ref") or payload["profile_digest"] != digest(profile)
                        or payload["model_ref"] != profile.get("profile_ref") or payload["model_digest"] != digest(profile)):
                    raise JournalError("capability_mismatch", "capability differs from the configured synthetic model", 409)
        return self.j._transaction(lambda: self.append(event, grant=grant))

    def declaration(self, payload, grant, *, family):
        self.validate_payload(payload)
        if family not in {"home-preference-snapshot-1", "home-goal-declaration-1"} or payload["schema"] != family:
            raise JournalError("unsupported_schema", "exact adaptive declaration family required")
        grant.require("event", payload["site_id"], payload["room_id"], payload_type=family, revision="1.0")
        self._authorization(payload, grant, "write")
        validate_command_clocks(payload, self.clock_profile, self.j.clock())
        event = self._event_for(payload, grant); self.j._event(event)
        return self.j._transaction(lambda: self.append(event, grant=grant))

    def intent(self, payload, grant):
        self.validate_payload(payload)
        if payload["schema"] != "home-intent-event-3":
            raise JournalError("unsupported_schema", "intent-3 required")
        grant.require("intent", payload["site_id"], payload["room_id"], payload_type=payload["schema"], revision="3.0")
        if grant.expected_actor_ref is None or grant.expected_actor_ref != payload["actor_ref"]:
            raise JournalError("forbidden", "intent actor differs from authenticated binding", 403)
        self._intent_subject(payload, grant, "write")
        validate_command_clocks(payload, self.clock_profile, self.j.clock())
        event = self._event_for(payload, grant); self.j._event(event)
        return self.j._transaction(lambda: self.append(event, grant=grant))

    def _visible(self, grant, site, room, after, through, *, inputs_only=False):
        names = sorted(grant.payload_types & ADAPTIVE_INPUTS if inputs_only else grant.payload_types)
        revisions = sorted(grant.revisions)
        if not names or not revisions:
            return []
        rows = self.j.db.execute("SELECT * FROM publications WHERE site_id=? AND room_id=? AND cursor>? AND cursor<=? AND payload_type IN (%s) AND revision IN (%s) ORDER BY cursor" %
                                 (",".join("?" for _ in names), ",".join("?" for _ in revisions)),
                                 (site, room, after, through, *names, *revisions)).fetchall()
        return [row for row in rows if self._read_allowed(json.loads(row["body"]), grant)]

    def page(self, grant, site, room, *, after=0, stream_epoch=None, limit=128, consumer_id=None):
        grant.require("read", site, room); self._scope_check(site, room)
        if type(limit) is not int or not 1 <= limit <= 128:
            raise JournalError("invalid_query", "limit must be 1..128")
        def read():
            self._expire_requests(site, room); self._refresh_heads(site, room)
            tail = self.j._epoch_cursor(stream_epoch, after)
            rows = self._visible(grant, site, room, after, tail)
            selected, used = [], 2048
            for row in rows[:limit]:
                size = len(row["body"].encode("utf-8"))
                if used + size > self.j.limits.max_response_bytes:
                    break
                selected.append(json.loads(row["body"])); used += size
            more = len(selected) < len(rows)
            next_cursor = selected[-1]["cursor"] if more and selected else after if more else tail
            self._delivered_publications(grant, consumer_id or grant.principal_id, selected)
            self.j._delivered(grant, consumer_id or grant.principal_id, site, room, next_cursor)
            return {"schema": "home-context-page-1", "stream_epoch": self.j._meta("epoch"), "after": after,
                    "items": selected, "next_cursor": next_cursor, "authority": self.authority(site, room)}
        return self.j._transaction(read)

    def _ref(self, ref, grant, scope, through, *, allow_revoked=False):
        publication = super()._ref(ref, grant, scope, through, allow_revoked=allow_revoked)
        if not self._read_allowed(publication, grant):
            raise JournalError("forbidden", "referenced original is outside configured subject read rights", 403)
        return publication

    def input_cut(self, grant, site, room, *, consumer_id, after, t, limit=128, cut_id=None):
        grant.require("read", site, room)
        grant.require("worker_commit", site, room)
        if consumer_id != grant.expected_worker_id:
            raise JournalError("forbidden", "cut consumer must be the configured worker", 403)
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 128 or not finite(t) or not 0 <= t <= self.j.clock():
            raise JournalError("invalid_query", "bounded causal cut parameters required")
        def read():
            self._refresh_heads(site, room)
            if cut_id:
                row = self.j.db.execute("SELECT * FROM dynamic_cuts WHERE cut_id=? AND principal_id=? AND consumer_id=? AND site_id=? AND room_id=?",
                                        (cut_id, grant.principal_id, consumer_id, site, room)).fetchone()
                if not row:
                    raise JournalError("unknown_cut", "cut is outside the granted worker scope", 404)
                if t != row["as_of_t"] or not row["after_cursor"] <= after <= row["delivered_cursor"]:
                    raise JournalError("incomplete_cut", "cut continuation cannot skip receipts or change time", 409)
                frozen, authority = row["cut_cursor"], json.loads(row["authority"])
                identifier(cut_id, "cut_id")
            else:
                self._expire_requests(site, room)
                frozen = self.j._epoch_cursor(None, after)
                if self.j.db.execute("SELECT count(*) FROM dynamic_cuts").fetchone()[0] >= 1024:
                    raise JournalError("cut_budget", "synthetic fence budget exhausted", 429)
                authority, new_id = self.authority(site, room), "cut:" + str(uuid.uuid4())
                self.j.db.execute("INSERT INTO dynamic_cuts VALUES(?,?,?,?,?,?,?,?,?,?)",
                                  (new_id, grant.principal_id, consumer_id, site, room, after, frozen, t, canonical(authority), after))
                cut_id_local = new_id
            actual_id = cut_id if cut_id else cut_id_local
            rows = self._visible(grant, site, room, after, frozen, inputs_only=True)
            committed_outputs = self._committed_output_heads(grant, site, room, frozen, t)
            publications, used = [], 4096 + len(canonical(committed_outputs).encode("utf-8"))
            for row in rows[:limit]:
                size = len(row["body"].encode("utf-8"))
                if used + size > self.j.limits.max_response_bytes:
                    break
                publications.append(json.loads(row["body"])); used += size
            complete = len(publications) == len(rows)
            next_cursor = frozen if complete else publications[-1]["cursor"] if publications else after
            self.j.db.execute("UPDATE dynamic_cuts SET delivered_cursor=max(delivered_cursor,?) WHERE cut_id=?", (next_cursor, actual_id))
            self._delivered_publications(grant, consumer_id, publications)
            self.j._delivered(grant, consumer_id, site, room, next_cursor)
            self._delivered_publications(grant, consumer_id, committed_outputs)
            self._storage_budget()
            result = {"journal_epoch": self.j._meta("epoch"), "cut_id": actual_id, "cut_cursor": frozen,
                    "as_of_t": t, "authority": authority, "complete": complete,
                    "publications": publications, "committed_outputs": committed_outputs, "next_cursor": next_cursor}
            bounded_json(result, self.j.limits.max_response_bytes)
            return result
        return self.j._transaction(read)

    def checkpoint(self, worker_id, grant, site, room):
        grant.require("read", site, room); grant.require("worker_commit", site, room)
        if worker_id != grant.expected_worker_id:
            raise JournalError("forbidden", "worker identity differs from grant", 403)
        def read():
            self._refresh_heads(site, room)
            row = self.j.db.execute("SELECT * FROM workers WHERE worker_id=? AND site_id=? AND room_id=?", (worker_id, site, room)).fetchone()
            checkpoint = json.loads(row["checkpoint"]) if row else None
            return {"schema": "home-context-worker-checkpoint-response-4", "journal_epoch": self.j._meta("epoch"),
                    "worker_id": worker_id, "input_cursor": row["input_cursor"] if row else 0,
                    "commit_seq": row["context_seq"] if row else 0, "semantic_seq": checkpoint["semantic_seq"] if checkpoint else -1,
                    "control_seq": checkpoint["control_seq"] if checkpoint else 0, "checkpoint": checkpoint, "authority": self.authority(site, room)}
        return self.j._transaction(read)

    def _validate_cut(self, commit, grant, previous):
        cut, cp = commit["input_cut"], commit["checkpoint"]
        row = self.j.db.execute("SELECT * FROM dynamic_cuts WHERE cut_id=? AND principal_id=? AND consumer_id=? AND site_id=? AND room_id=?",
                                (cut["cut_id"], grant.principal_id, grant.expected_worker_id, commit["site_id"], commit["room_id"])).fetchone()
        if (not row or not cut["complete"] or row["delivered_cursor"] != row["cut_cursor"]
                or cut["input_cursor"] != row["cut_cursor"] or cut["as_of_t"] != row["as_of_t"]
                or row["after_cursor"] != previous or cut["authority"] != json.loads(row["authority"])):
            raise JournalError("incomplete_cut", "commit requires its complete original receipt fence", 409)
        if cut["authority"] != commit["expected_authority"] or cut["authority"] != self.authority(commit["site_id"], commit["room_id"]):
            raise JournalError("authority_conflict", "authority changed after the cut", 409)
        known, known_rows = {}, {}
        old_worker = self.j.db.execute("SELECT checkpoint FROM workers WHERE worker_id=? AND site_id=? AND room_id=?", (grant.expected_worker_id, commit["site_id"], commit["room_id"])).fetchone()
        if old_worker:
            prior_checkpoint = json.loads(old_worker[0])
            known = {r["ref"]["cursor"]: r for r in prior_checkpoint["receipts"]}
            known_rows = {r["publication"]["cursor"]: r for k in ("pending_inputs", "geometry_inputs", "retained_inputs") for r in prior_checkpoint[k]}
        receipts = {}
        for claimed in cut["receipts"]:
            cursor = claimed["ref"]["cursor"]
            if cursor in receipts:
                raise JournalError("invalid_input_receipt", "receipt cursor repeated", 409)
            p = self._ref(claimed["ref"], grant, commit, row["cut_cursor"], allow_revoked=True)
            payload = p["payload"]
            delivered = self.j.db.execute("SELECT served_at FROM dynamic_deliveries WHERE principal_id=? AND consumer_id=? AND cursor=?",
                                          (grant.principal_id, grant.expected_worker_id, cursor)).fetchone()
            if p["payload_type"] not in ADAPTIVE_INPUTS or not delivered or not max(p["committed_at"], delivered[0]) <= claimed["received_at"] <= self.j.clock():
                raise JournalError("invalid_input_receipt", "receipt is not a causal original worker input", 409)
            eligible = self._eligible(p, claimed["received_at"])
            if claimed["eligible_at"] != eligible or cursor in known and claimed["received_at"] != known[cursor]["received_at"]:
                raise JournalError("invalid_input_receipt", "original input receipt time cannot change", 409)
            receipts[cursor] = claimed
        required = {r["cursor"] for r in self._visible(grant, commit["site_id"], commit["room_id"], previous, row["cut_cursor"], inputs_only=True)}
        if not required <= set(receipts):
            raise JournalError("incomplete_cut", "a visible input receipt is missing from the cut", 409)
        eligible_refs, buffered_refs = cut["eligible_refs"], cut["buffered_refs"]
        eligible_cursors, buffered_cursors = [r["cursor"] for r in eligible_refs], [r["cursor"] for r in buffered_refs]
        if len(set(eligible_cursors + buffered_cursors)) != len(eligible_cursors + buffered_cursors) or set(eligible_cursors + buffered_cursors) != set(receipts):
            raise JournalError("invalid_input_receipt", "eligible and buffered references must partition receipts", 409)
        for cursor in sorted(receipts):
            # A journal cursor orders durable delivery, not physical or receipt
            # clocks. Complete delivery is checked above; independently buffer
            # each future input without suppressing an eligible later input.
            now_eligible = receipts[cursor]["eligible_at"] <= cut["as_of_t"]
            if (cursor in eligible_cursors) != now_eligible:
                raise JournalError("receipt_order", "input eligibility differs from its original causal clocks", 409)
        for reference in eligible_refs + buffered_refs:
            if reference["payload_digest"] != receipts[reference["cursor"]]["ref"]["payload_digest"] or reference["journal_epoch"] != cut["journal_epoch"]:
                raise JournalError("invalid_reference", "cut reference differs from its receipt", 409)
        retained = cp["pending_inputs"] + cp["geometry_inputs"] + cp["retained_inputs"]
        body_cursors = set()
        for item in retained:
            saved = item["publication"]
            if saved["cursor"] in body_cursors:
                raise JournalError("invalid_reference", "checkpoint body cursor repeated", 409)
            body_cursors.add(saved["cursor"])
            ref = {"journal_epoch": saved["journal_epoch"], "cursor": saved["cursor"], "payload_digest": saved["payload_digest"]}
            original = self._ref(ref, grant, commit, row["cut_cursor"], allow_revoked=True)
            fields = ("journal_epoch", "cursor", "id", "producer_id", "committed_at", "payload_type", "payload_version", "payload_digest", "payload")
            if saved != {k: original[k] for k in fields} or saved["cursor"] not in receipts or item["received_at"] != receipts[saved["cursor"]]["received_at"]:
                raise JournalError("invalid_reference", "checkpoint body or original receipt was changed", 409)
            if "component_ids" in item:
                payload = original["payload"]
                ids = item["component_ids"]
                allowed = {component_id(c, payload["schema"] == "home-geometry-3") for c in
                           (payload["objects"] if payload["schema"] == "home-geometry-3" else payload["facts"] if payload["schema"] == "home-fact-batch-2" else [])}
                if len(ids) != len(set(ids)) or not ids or ids != sorted(ids) or not set(ids) <= allowed:
                    raise JournalError("invalid_reference", "checkpoint component selector is not in its original publication", 409)
                if saved["cursor"] not in known_rows:
                    issued = self.j.db.execute("SELECT served_at FROM dynamic_snapshot_masks WHERE principal_id=? AND consumer_id=? AND cursor=? AND component_ids=?",
                                               (grant.principal_id, grant.expected_worker_id, saved["cursor"], canonical(ids))).fetchone()
                    if not issued or issued[0] > item["received_at"]:
                        raise JournalError("invalid_reference", "first component mask requires its authentic delivered snapshot selection", 409)
            if saved["cursor"] in known_rows and item.get("component_ids") != known_rows[saved["cursor"]].get("component_ids"):
                raise JournalError("invalid_reference", "retained component selection cannot change", 409)
        checkpoint_receipts = set()
        for claimed in cp["receipts"]:
            if claimed["ref"]["cursor"] in checkpoint_receipts:
                raise JournalError("invalid_input_receipt", "checkpoint receipt cursor repeated", 409)
            checkpoint_receipts.add(claimed["ref"]["cursor"])
            if claimed["ref"]["cursor"] not in receipts or claimed != receipts[claimed["ref"]["cursor"]]:
                raise JournalError("invalid_input_receipt", "checkpoint receipt differs from its original cut", 409)
        pending_cursors = {item["publication"]["cursor"] for item in cp["pending_inputs"]}
        if body_cursors != checkpoint_receipts:
            raise JournalError("invalid_input_receipt", "retained bodies and original checkpoint receipts must match exactly", 409)
        for cursor in buffered_cursors:
            original = self._ref(receipts[cursor]["ref"], grant, commit, row["cut_cursor"], allow_revoked=True)
            discarded_by_revocation = (not cut["authority"]["room"]["motion_allowed"]
                                       and original["payload_type"] in {"home-geometry-3", "home-fact-batch-2"})
            if not discarded_by_revocation and (cursor not in pending_cursors or cursor not in checkpoint_receipts):
                raise JournalError("invalid_input_receipt", "buffered input requires its immutable pending body and original receipt", 409)
        source_facts, source_intents = {}, {}
        for item in retained:
            payload = item["publication"]["payload"]
            if payload["schema"] == "home-fact-batch-2":
                for fact in payload["facts"]:
                    if item.get("component_ids") is None or fact["id"] in item["component_ids"]:
                        source_facts.setdefault(fact["id"], set()).add(digest(fact))
            elif payload["schema"] == "home-intent-event-3":
                source_intents.setdefault(payload["id"], set()).add(digest(payload))
        for values, sources in ((cp["facts"], source_facts), (cp["intents"], source_intents)):
            if len({v["id"] for v in values}) != len(values) or any(digest(v) not in sources.get(v["id"], set()) for v in values):
                raise JournalError("invalid_reference", "materialized checkpoint inputs must be selected original components", 409)
        return receipts

    def _eligible(self, publication, received_at=None):
        payload = publication["payload"]
        value = max(payload.get("effective_at", payload.get("observed_at", payload.get("t", payload.get("available_at", publication["committed_at"])))),
                    payload.get("available_at", publication["committed_at"]), publication["committed_at"])
        if received_at is not None:
            if not finite(received_at) or not publication["committed_at"] <= received_at <= 120:
                raise JournalError("invalid_input_receipt", "original receipt clock is invalid", 409)
            value = max(value, received_at)
        return value

    def _family_key(self, publication):
        payload = publication["payload"]
        return digest([payload["schema"], *[payload[key] for key in SCOPE_KEYS], publication["producer_id"],
                       *([payload["registration_ref"]] if payload["schema"] == "home-registered-surface-1" else [])])

    def _family_heads(self, site, room, t, *, eligible=None):
        heads = {}
        for row in self.j.db.execute("SELECT * FROM publications WHERE site_id=? AND room_id=? AND payload_type IN ('home-registered-surface-1','home-lighting-environment-1','home-lighting-capability-1') ORDER BY cursor", (site, room)):
            publication = json.loads(row["body"])
            if self._eligible(publication) > t or eligible is not None and row["cursor"] not in eligible:
                continue
            key = self._family_key(publication)
            rank = (publication["payload"]["effective_at"], publication["payload"]["available_at"], publication["payload_digest"])
            if key not in heads or rank > heads[key][0]:
                heads[key] = (rank, row, publication)
        return {key: (row, publication) for key, (_, row, publication) in heads.items()}

    def _committed_output_heads(self, grant, site, room, through, t):
        heads = {}
        for row in self.j.db.execute("SELECT body FROM publications WHERE site_id=? AND room_id=? AND cursor<=? AND payload_type IN ('home-lighting-policy-decision-3','home-lighting-control-decision-2','home-experience-goal-1') ORDER BY cursor", (site, room, through)):
            publication = json.loads(row[0]); body = publication["payload"]
            if publication["producer_id"] != grant.producer_id or publication["payload_type"] not in grant.payload_types or not self._read_allowed(publication, grant) or self._eligible(publication) > t:
                continue
            key = revision_chain_key(body) if body["schema"] == "home-experience-goal-1" else body["schema"]
            heads[key] = _normalized(publication)
        if len(heads) > 10:
            raise JournalError("output_delivery_budget", "committed current output heads exceed the ten-original bound", 507)
        return sorted(heads.values(), key=lambda publication: publication["cursor"])

    def _component(self, reference, commit, grant, *, historical=False):
        """Resolve an unchanged component from an authenticated original publication."""
        publication_ref = reference["publication_ref"]
        if publication_ref is None:
            if reference == {"component_id": "config:" + self.config_digest, "component_digest": self.config_digest, "publication_ref": None}:
                return copy.deepcopy(self.j.adaptive_config["config"])
            candidates = [body for body in commit["outputs"]
                          if body.get("id") == reference["component_id"] and digest(body) == reference["component_digest"]]
            if not candidates:
                policy = next((body for body in commit["outputs"] if body["schema"] == "home-lighting-policy-decision-3"), None)
                candidates = [body for body in (policy["requests"] if policy else [])
                              if body["id"] == reference["component_id"] and digest(body) == reference["component_digest"]]
            if not candidates:
                rows = [row for row in commit["checkpoint"]["revisions"]["rows"]
                        if row["publication"]["payload_type"] == "home-experience-goal-1"
                        and row["publication"]["id"] == reference["component_id"]
                        and row["publication"]["payload_digest"] == reference["component_digest"]]
                for row in rows:
                    publication = self._ref(_publication_ref(row["publication"]), grant, commit, commit["input_cut"]["input_cursor"], allow_revoked=historical)
                    if (publication["cursor"] in {ref["cursor"] for ref in commit["input_cut"]["eligible_refs"]}
                            and _normalized(publication) == row["publication"] and self._eligible(publication, row["received_at"]) <= commit["input_cut"]["as_of_t"]):
                        candidates.append(publication["payload"])
            if not candidates:
                raise JournalError("invalid_reference", "unpublished component is not a staged goal or checkpointed request", 409)
            return candidates[0]
        publication = self._ref(publication_ref, grant, commit, commit["input_cut"]["input_cursor"], allow_revoked=historical)
        output_proofs = {row["publication"]["cursor"]: row for row in commit["checkpoint"]["committed_output_inputs"]}
        proof = output_proofs.get(publication["cursor"])
        if publication["cursor"] not in {r["cursor"] for r in commit["input_cut"]["eligible_refs"]} and not (
                proof and self._eligible(publication, proof["received_at"]) <= commit["input_cut"]["as_of_t"]):
            raise JournalError("invalid_reference", "component requires its eligible original receipt", 409)
        payload = publication["payload"]
        components = (payload["objects"] if payload["schema"] == "home-geometry-3" else
                      payload["facts"] if payload["schema"] == "home-fact-batch-2" else
                      [payload, *payload["requests"]] if payload["schema"] == "home-lighting-policy-decision-3" else
                      [payload, payload["execution"]["feedback"]] if payload["schema"] == "home-lighting-control-decision-2" else [payload])
        matches = [body for body in components
                   if (body["object_ref"]["oid"] if payload["schema"] == "home-geometry-3" else body["id"]) == reference["component_id"]
                   and digest(body) == reference["component_digest"]]
        if len(matches) != 1 or publication_ref.get("component_id", reference["component_id"]) != reference["component_id"]:
            raise JournalError("invalid_reference", "component digest or original selector differs", 409)
        return matches[0]

    def _fences(self, authority, person, obj, association, source_id, registration_ref):
        source = next((row for row in authority["sources"] if row["source_id"] == source_id), None)
        registration = next((row for row in authority["registrations"] if row["registration_ref"] == registration_ref), None)
        follow = next((row for row in authority["follows"] if row["person_ref"] == person and row["object_ref"] == obj
                       and row["association_generation"] == association and row["source_id"] == source_id
                       and row["registration_ref"] == registration_ref), None)
        if not source or not registration or not follow or not following_allowed(
                authority, self.identity, person_ref=person, object_ref=obj, association_generation=association,
                source_id=source_id, registration_ref=registration_ref):
            raise JournalError("invalid_reference", "scoped following authority is unavailable", 409)
        return {"room_motion_generation": authority["room"]["motion_generation"], "source_id": source_id,
                "source_revocation_generation": source["revocation_generation"], "registration_ref": registration_ref,
                "registration_generation": registration["registration_generation"], "follow_generation": follow["follow_generation"]}

    @staticmethod
    def _registered_authority(surface, authority):
        source = next((row for row in authority["sources"] if row["source_id"] == surface["source_id"]), None)
        registration = next((row for row in authority["registrations"]
                             if row["registration_ref"] == surface["registration_ref"]), None)
        return (authority["room"]["motion_allowed"] and source is not None and not source["revoked"]
                and registration is not None and not registration["revoked"]
                and registration["registration_generation"] == surface["registration_generation"]
                and registration["registry_digest"] == surface["registry_digest"])

    def _geometry(self, snapshot, cut, commit, grant, receipts):
        super()._geometry(snapshot, cut, commit, grant, receipts)
        eligible = {reference["cursor"] for reference in cut["eligible_refs"]}
        heads = self._family_heads(commit["site_id"], commit["room_id"], cut["as_of_t"], eligible=eligible)
        if len({head["registration_ref"] for head in snapshot["surface_heads"]}) != len(snapshot["surface_heads"]):
            raise JournalError("invalid_reference", "surface head repeated", 409)
        for head in snapshot["surface_heads"]:
            if head["publication_ref"] is None:
                if head["status"] == "fresh" or head["surface"] is not None:
                    raise JournalError("invalid_reference", "usable registered surface requires its original", 409)
                continue
            publication = self._ref(head["publication_ref"], grant, commit, cut["input_cursor"], allow_revoked=head["status"] != "fresh")
            payload = publication["payload"]
            candidates = [value for row, value in heads.values() if row["payload_type"] == "home-registered-surface-1"
                          and value["payload"]["registration_ref"] == head["registration_ref"]]
            if (payload["schema"] != "home-registered-surface-1" or len(candidates) != 1
                    or _publication_ref(publication) != _publication_ref(candidates[0]) or head["surface"] != payload):
                raise JournalError("invalid_reference", "surface differs from its current eligible original", 409)
            if head["status"] == "fresh" and payload["valid_until"] <= cut["as_of_t"]:
                raise JournalError("stale_geometry", "registered surface expired", 409)
            if head["status"] == "fresh" and not self._registered_authority(payload, cut["authority"]):
                raise JournalError("invalid_reference", "registered surface lacks current source/registration authority", 409)

    def _target(self, target, output, commit, grant):
        cut, checkpoint = commit["input_cut"], commit["checkpoint"]
        publication = self._ref(target["geometry_ref"], grant, commit, cut["input_cursor"])
        if publication["cursor"] not in {ref["cursor"] for ref in cut["eligible_refs"]}:
            raise JournalError("invalid_reference", "target requires eligible original geometry", 409)
        policy = checkpoint["pipeline"]["policy"] if checkpoint["pipeline"] else None
        requests = [request for request in (policy["requests"] if policy else []) if request["id"] == target["request_id"]]
        if len(requests) != 1:
            raise JournalError("invalid_reference", "target has no checkpointed request", 409)
        request = requests[0]
        if any(request[key] != target[key] for key in ("goal_ref", "beneficiary", "target", "association_generation", "role", "objectives")) or request["valid_until"] <= cut["as_of_t"]:
            raise JournalError("invalid_reference", "target differs from its live consumer request", 409)
        self._component(request["goal_ref"], commit, grant)
        for reference in request["preference_refs"] + request["capability_refs"] + request["instruction_refs"] + target["environment_refs"]:
            self._component(reference, commit, grant)
        payload = publication["payload"]
        if target["role"] == "protected":
            if (payload["schema"] != "home-registered-surface-1" or target["target"]["registration_ref"] != payload["registration_ref"]
                    or target["registration_ref"] != payload["registration_ref"] or target["source_id"] != payload["source_id"]
                    or payload["source_id"] != self._registrations[payload["registration_ref"]][0]
                    or any(target[key] != payload[key] for key in ("patches", "uncertainty", "transform_origin_m", "uncertainty_correlation_ref"))
                    or target["status"] != "registered" or target["fences"] is not None or target["aim_anchor_m"] is not None):
                raise JournalError("invalid_reference", "protected target differs from its registered original", 409)
            heads = [head for head in output["geometry_snapshot"]["surface_heads"] if head["registration_ref"] == payload["registration_ref"]]
            if (len(heads) != 1 or heads[0]["status"] != "fresh" or heads[0]["surface"] != payload
                    or heads[0]["publication_ref"] != target["geometry_ref"]
                    or not self._registered_authority(payload, checkpoint["authority"])):
                raise JournalError("invalid_reference", "protected target lacks current registered authority", 409)
            return
        obj = target["target"]["object_ref"]; person = target["beneficiary"]["person_ref"]
        if payload["schema"] != "home-geometry-3":
            raise JournalError("invalid_reference", "benefit target requires original object geometry", 409)
        originals = [item for item in payload["objects"] if item["object_ref"] == obj]
        if len(originals) != 1 or target["geometry_ref"].get("component_id") != obj["oid"]:
            raise JournalError("invalid_reference", "benefit target selector changed", 409)
        original = originals[0]
        if (original["state"] != "open" or original["person_ref"] != person
                or original["association_generation"] != target["association_generation"]
                or any(target[key] != original[key] for key in ("patches", "uncertainty"))):
            raise JournalError("invalid_reference", "target body differs from original open geometry", 409)
        material = next((row for row in self.materials if row["object_ref"] == obj), None)
        if (not material or target["source_id"] != material["source_id"] or payload["capture"]["source_id"] != material["source_id"]
                or target["registration_ref"] != material["registration_ref"] or payload["registry_digest"] != material["registry_digest"]
                or target["uncertainty_correlation_ref"] != material["uncertainty_correlation_ref"]):
            raise JournalError("invalid_reference", "target material metadata differs from trusted configuration", 409)
        midpoint = [sum(patch["centre_m"][i] for patch in original["patches"]) / 2 for i in range(3)]
        origin = material["transform_origin_m"] if material["transform_origin"] == "declared_static_protocol_origin" else midpoint
        if target["transform_origin_m"] != origin or target["aim_anchor_m"] != midpoint or origin != midpoint:
            raise JournalError("invalid_reference", "target origin or aim differs from normative material binding", 409)
        fences = self._fences(checkpoint["authority"], person, obj, target["association_generation"], target["source_id"], target["registration_ref"])
        lease = request["lease"]
        if target["fences"] != fences or not lease or lease["fences"] != fences or lease["valid_until"] <= cut["as_of_t"]:
            raise JournalError("invalid_reference", "target lease has stale scoped authority", 409)
        heads = [head for head in output["geometry_snapshot"]["object_heads"] if head["object_ref"] == obj]
        if (len(heads) != 1 or not heads[0]["estimate"] or heads[0]["estimate"]["state"] != "open"
                or heads[0]["estimate"]["person_ref"] != person or heads[0]["estimate"]["association_generation"] != target["association_generation"]
                or heads[0]["status"] == "rejected"):
            raise JournalError("invalid_reference", "current geometry fences the requested attribution", 409)
        if target["status"] == "resolved":
            if request["follow_mode"] != "runtime" or heads[0]["status"] != "fresh" or heads[0]["estimate_ref"] != target["geometry_ref"]:
                raise JournalError("invalid_reference", "runtime target does not use current fresh geometry", 409)
        else:
            intents = checkpoint["pipeline"]["intents"]
            matches = [intent for intent in intents if intent["kind"] == "keep_here" and intent["person_ref"] == person
                       and intent["object_ref"] == obj and lease["id"] == "declared-lease:" + intent["id"]]
            outcomes = checkpoint["pipeline"]["intent_outcomes"]
            if request["follow_mode"] != "declared_frozen" or len(matches) != 1:
                raise JournalError("invalid_reference", "frozen target lacks its exact keep-here declaration", 409)
            intent = matches[0]; outcome = next((row for row in outcomes if row["intent_id"] == intent["id"]), None)
            if (not outcome or outcome["status"] != "applied" or outcome["intent_digest"] != digest(intent)
                    or lease["valid_until"] > min(intent["valid_until"], outcome["decided_at"] + 30, 120)):
                raise JournalError("invalid_reference", "frozen target lacks active terminal application", 409)
            previous_row = self.j.db.execute("SELECT checkpoint FROM workers WHERE worker_id=? AND site_id=? AND room_id=?",
                                            (grant.expected_worker_id, commit["site_id"], commit["room_id"])).fetchone()
            previous = json.loads(previous_row[0]) if previous_row else None
            frozen = previous["pipeline"]["frozen_heads"] if previous and previous["pipeline"] else []
            saved = [head for head in frozen if head["object_ref"] == obj and head["capture_intent_id"] == intent["id"]
                     and head["estimate_ref"] == target["geometry_ref"] and head["estimate"] == original]
            if not saved and (heads[0]["status"] != "fresh" or heads[0]["estimate_ref"] != target["geometry_ref"]):
                raise JournalError("invalid_reference", "initial freeze requires the exact current fresh original", 409)
            stored = [head for head in checkpoint["pipeline"]["frozen_heads"] if head["object_ref"] == obj]
            if (len(stored) != 1 or stored[0]["capture_intent_id"] != intent["id"]
                    or stored[0]["estimate_ref"] != target["geometry_ref"] or stored[0]["estimate"] != original):
                raise JournalError("invalid_reference", "frozen original is absent from the checkpoint", 409)

    def _frozen_head_proofs(self, commit, grant, old):
        pipeline = commit["checkpoint"]["pipeline"]
        if not pipeline:
            return
        heads = pipeline["frozen_heads"]
        if len({canonical(head["object_ref"]) for head in heads}) != len(heads):
            raise JournalError("invalid_reference", "frozen object capture repeated", 409)
        previous = old["pipeline"]["frozen_heads"] if old and old["pipeline"] else []
        prior_ids = set(old["pipeline"]["intent_ids"]) if old and old["pipeline"] else set()
        current_ids = set(pipeline["intent_ids"])
        if (len(current_ids) != len(pipeline["intent_ids"]) or not prior_ids <= current_ids
                or current_ids - prior_ids != {head["capture_intent_id"] for head in heads} - prior_ids):
            raise JournalError("invalid_reference", "immutable capture history differs from the captured heads", 409)
        control = next((body for body in commit["outputs"] if body["schema"] == "home-lighting-control-decision-2"), None)
        for head in heads:
            intents = [body for body in pipeline["intents"] if body["id"] == head["capture_intent_id"] and body["kind"] == "keep_here"]
            results = [row for row in pipeline["intent_outcomes"] if row["intent_id"] == head["capture_intent_id"]]
            if (len(intents) != 1 or len(results) != 1 or results[0]["status"] != "applied"
                    or results[0]["intent_digest"] != digest(intents[0]) or not head["estimate_ref"] or not head["estimate"]
                    or not _same_scope(self.identity, intents[0]) or intents[0]["object_ref"] != head["object_ref"]
                    or intents[0]["person_ref"] != head["estimate"]["person_ref"]):
                raise JournalError("invalid_reference", "frozen capture lacks its exact first applied keep-here instruction", 409)
            publication = self._ref(head["estimate_ref"], grant, commit, commit["input_cut"]["input_cursor"], allow_revoked=True)
            body = publication["payload"]
            estimates = [row for row in body.get("objects", []) if row["object_ref"] == head["object_ref"]]
            if (body["schema"] != "home-geometry-3" or len(estimates) != 1 or estimates[0] != head["estimate"]
                    or estimates[0]["state"] != "open" or head["estimate_ref"].get("component_id") != head["object_ref"]["oid"]
                    or any(head[key] != body[key] for key in ("capture", "observed_at", "available_at", "valid_until"))):
                raise JournalError("invalid_reference", "frozen capture differs from its immutable original geometry", 409)
            prior = [row for row in previous if row["capture_intent_id"] == head["capture_intent_id"] and row["object_ref"] == head["object_ref"]]
            if prior:
                if len(prior) != 1 or head != prior[0]:
                    raise JournalError("invalid_reference", "a continuing instruction cannot rewrite its frozen capture", 409)
            else:
                current = [row for row in control["geometry_snapshot"]["object_heads"] if row["object_ref"] == head["object_ref"]] if control else []
                captured = {key: value for key, value in head.items() if key != "capture_intent_id"}
                if (head["capture_intent_id"] in prior_ids or len(current) != 1 or current[0]["status"] != "fresh" or captured != current[0]
                        or results[0]["decided_at"] > commit["input_cut"]["as_of_t"]):
                    raise JournalError("invalid_reference", "new keep-here capture requires its own current fresh original", 409)

    def _intent_ledger(self, commit, old):
        checkpoint = commit["checkpoint"]; pipeline = checkpoint["pipeline"]
        ledger = pipeline["intent_outcomes"] if pipeline else []
        prior = {row["intent_id"]: row for row in old["pipeline"]["intent_outcomes"]} if old and old["pipeline"] else {}
        current = {row["intent_id"]: row for row in ledger}
        if len(current) != len(ledger) or any(current.get(key) != value for key, value in prior.items()):
            raise JournalError("intent_result_conflict", "first terminal admission is immutable", 409)
        policy = next((body for body in commit["outputs"] if body["schema"] == "home-lighting-policy-decision-3"), None)
        if not policy:
            if current != prior:
                raise JournalError("invalid_intent_result", "only a semantic boundary may admit an instruction", 409)
            if (pipeline["policy"] if pipeline else None) != (old["pipeline"]["policy"] if old and old["pipeline"] else None):
                raise JournalError("invalid_reference", "non-semantic commit changed its committed policy", 409)
            return current
        results = {row["intent_id"]: row for row in policy["intent_results"]}
        if len(results) != len(policy["intent_results"]) or results != current or not pipeline or pipeline["policy"] != policy:
            raise JournalError("invalid_intent_result", "policy and checkpoint first-admission ledgers differ", 409)
        retained = checkpoint["retained_inputs"] + checkpoint["pending_inputs"] + checkpoint["geometry_inputs"]
        eligible = {ref["cursor"] for ref in commit["input_cut"]["eligible_refs"]}
        for key, outcome in current.items():
            bodies = [row for row in retained if row["publication"]["payload_type"] == "home-intent-event-3"
                      and row["publication"]["payload"]["id"] == key]
            if len(bodies) != 1 or digest(bodies[0]["publication"]["payload"]) != outcome["intent_digest"]:
                raise JournalError("invalid_intent_result", "terminal outcome lacks its unchanged original instruction", 409)
            item = bodies[0]; intent = item["publication"]["payload"]
            if outcome["decided_at"] < self._eligible(item["publication"], item["received_at"]):
                raise JournalError("invalid_intent_result", "instruction was decided before original eligibility", 409)
            if key not in prior:
                if (item["publication"]["cursor"] not in eligible or outcome["decided_at"] != policy["t"]
                        or outcome["decision_ref"] != policy["id"] or policy["t"] % 1 != 0
                        or (intent["valid_until"] <= policy["t"]) != (outcome["status"] == "expired")):
                    raise JournalError("invalid_intent_result", "first outcome must bind the eligible semantic boundary", 409)
        return current

    def _post_authority(self, commit, outcomes):
        before = commit["input_cut"]["authority"]; after = copy.deepcopy(before)
        if commit["kind"] not in {"semantic", "combined"}:
            return after
        checkpoint = commit["checkpoint"]
        bodies = {row["publication"]["payload"]["id"]: row["publication"]["payload"]
                  for family in ("pending_inputs", "geometry_inputs", "retained_inputs") for row in checkpoint[family]
                  if row["publication"]["payload_type"] == "home-intent-event-3"}
        # Terminal declarations remain immutable; their active effects may end.
        for follow in after["follows"]:
            bindings = [binding for binding in checkpoint["intent_authority_bindings"]
                        if all(binding[key] == follow[key] for key in ("person_ref", "object_ref", "association_generation", "source_id", "registration_ref"))]
            active = [binding for binding in bindings if outcomes[binding["intent_id"]]["status"] == "applied"
                      and bodies[binding["intent_id"]]["effective_at"] <= commit["input_cut"]["as_of_t"] < bodies[binding["intent_id"]]["valid_until"]]
            if active and follow["follow_allowed"]:
                follow["follow_generation"] += 1; follow["follow_allowed"] = False
            elif (not active and not follow["follow_allowed"]
                  and any(follow["follow_generation"] == binding["baseline_follow_generation"] + 1 for binding in bindings)):
                follow["follow_generation"] += 1; follow["follow_allowed"] = True
        return after

    def _intent_authority_bindings(self, commit, old, outcomes):
        checkpoint = commit["checkpoint"]; bindings = checkpoint["intent_authority_bindings"]
        by_id = {binding["intent_id"]: binding for binding in bindings}
        previous = {binding["intent_id"]: binding for binding in old["intent_authority_bindings"]} if old else {}
        if len(bindings) != len(by_id) or any(by_id.get(key) != value for key, value in previous.items()):
            raise JournalError("intent_authority_conflict", "first scoped authority binding is immutable", 409)
        bodies = {row["publication"]["payload"]["id"]: row["publication"]["payload"]
                  for family in ("pending_inputs", "geometry_inputs", "retained_inputs") for row in checkpoint[family]
                  if row["publication"]["payload_type"] == "home-intent-event-3"}
        expected_ids = {key for key, outcome in outcomes.items() if outcome["status"] == "applied" and bodies[key]["kind"] == "dont_follow"}
        if set(by_id) != expected_ids:
            raise JournalError("intent_authority_conflict", "only each applied follow-stop instruction needs its first binding", 409)
        engagement = next((body for body in commit["outputs"] if body["schema"] == "home-engagement-state-3"), None)
        for key, binding in by_id.items():
            intent = bodies[key]; outcome = outcomes[key]
            if (binding["person_ref"] != intent["person_ref"] or binding["object_ref"] != intent["object_ref"]
                    or binding["applied_at"] != outcome["decided_at"]):
                raise JournalError("intent_authority_conflict", "binding changed the first applied attribution", 409)
            if key in previous:
                continue
            follows = [follow for follow in commit["input_cut"]["authority"]["follows"]
                       if all(follow[field] == binding[field] for field in ("person_ref", "object_ref", "association_generation", "source_id", "registration_ref"))]
            relations = [relation for relation in engagement["relations"] if relation["person_ref"] == binding["person_ref"]
                         and relation["object_ref"] == binding["object_ref"] and relation["association_generation"] == binding["association_generation"]
                         and not relation["closed"] and relation["status"] == "engaged"] if engagement else []
            if len(follows) != 1 or len(relations) != 1:
                raise JournalError("intent_authority_conflict", "first follow binding requires its exact engaged current tuple", 409)
            follow = follows[0]
            authority = commit["input_cut"]["authority"]
            source = next(row for row in authority["sources"] if row["source_id"] == follow["source_id"])
            registration = next(row for row in authority["registrations"] if row["registration_ref"] == follow["registration_ref"])
            if not authority["room"]["motion_allowed"] or source["revoked"] or registration["revoked"]:
                raise JournalError("intent_authority_conflict", "first instruction cannot acquire externally closed authority", 409)
            owners = [prior for prior in previous.values() if all(prior[field] == binding[field] for field in ("person_ref", "object_ref", "association_generation", "source_id", "registration_ref"))
                      and prior["baseline_follow_generation"] + 1 == follow["follow_generation"]]
            baseline = follow["follow_generation"] if follow["follow_allowed"] else owners[0]["baseline_follow_generation"] if owners else None
            if baseline is None or binding["baseline_follow_generation"] != baseline:
                raise JournalError("intent_authority_conflict", "closed permission is not owned by the admitted stop generation", 409)

    @staticmethod
    def _exposure_environment(target, context):
        """Bind the prior installed environment without duplicating photometry."""
        references = {}
        for patch in target["patches"]:
            matches = []
            for row in context["environment_heads"]:
                body = row["body"]
                if (max(body["effective_at"], body["available_at"]) > context["installed_at"]
                        or body["valid_until"] <= context["installed_at"]):
                    continue
                for contribution in body["contributions"]:
                    binding = contribution["binding"]
                    if (binding["target"] == target["target"] and binding["surface_id"] == patch["surface_id"]
                            and binding["surface_epoch"] == patch["surface_epoch"]):
                        if binding["mode"] != "surface_epoch_uniform":
                            return target["environment_refs"], None
                        matches.append(contribution)
                        references[canonical(row["ref"])] = row
            channels = {row["channel"] for row in matches}
            if not matches or len(channels) != 1 or "total_measured_illumination" in channels and len(matches) != 1:
                return target["environment_refs"], None
        rows = list(references.values())
        return [row["ref"] for row in rows], min(row["body"]["valid_until"] for row in rows)

    def _feedback_exposures(self, output, commit, grant, old):
        """Feedback describes the preceding installed interval, never new demand."""
        execution = output["execution"]; feedback = execution["feedback"]
        context = old["pipeline"]["exposure_context"] if old and old["pipeline"] else None
        targets = context["targets"] if context else []
        if len(feedback["exposures"]) != len(targets) or context and context["installed_at"] > execution["from_t"]:
            raise JournalError("invalid_reference", "feedback differs from the prior installed exposure context", 409)
        for target, exposure in zip(targets, feedback["exposures"]):
            requests = [request for request in context["requests"] if request["id"] == target["request_id"]]
            bounds = [row for row in context["support_bounds"] if row["target_id"] == target["id"]]
            if len(requests) != 1 or len(bounds) != 1:
                raise JournalError("invalid_reference", "prior exposure has no unique original request/support", 409)
            request, bound = requests[0], bounds[0]
            reference = exposure["request_ref"]
            if (reference["component_id"] != request["id"] or reference["component_digest"] != digest(request)
                    or exposure["goal_ref"] != request["goal_ref"] or exposure["preference_refs"] != request["preference_refs"]
                    or exposure["support_valid_until"] != bound["valid_until"]):
                raise JournalError("invalid_reference", "past exposure changed its original demand or support", 409)
            if reference["publication_ref"] is None:
                if exposure["status"] != "unavailable" or exposure["reason"] != "original_committed_request_not_delivered":
                    raise JournalError("invalid_reference", "unpublished past request cannot claim qualified exposure", 409)
            elif self._component(reference, commit, grant, historical=True) != request:
                raise JournalError("invalid_reference", "past request differs from its delivered original policy", 409)
            self._component(exposure["goal_ref"], commit, grant, historical=True)
            for reference in exposure["preference_refs"]:
                self._component(reference, commit, grant, historical=True)
            environment_refs, environment_until = self._exposure_environment(target, context)
            if exposure["environment_refs"] != environment_refs or exposure["environment_valid_until"] != environment_until:
                raise JournalError("invalid_reference", "past exposure changed its installed environment", 409)
            for reference in environment_refs:
                self._component(reference, commit, grant, historical=True)
            if (min(request["valid_until"], bound["valid_until"]) <= execution["t"]
                    or environment_until is None or environment_until <= execution["t"]):
                if exposure["status"] not in {"unavailable", "compute_refused"}:
                    raise JournalError("invalid_reference", "expired or unavailable original support cannot claim exposure", 409)

    def _checkpoint_references(self, commit, grant, old):
        checkpoint = commit["checkpoint"]; cut = commit["input_cut"]
        store = RevisionStore(identity=self.identity, clock_profile=self.clock_profile, maximum_rows=96, consumer_receipts=True, configured_worker_id=self._worker_producer)
        store.restore(checkpoint["revisions"], self._original_authorization)
        prior_rows = old["revisions"]["rows"] if old else []
        if checkpoint["revisions"]["rows"][:len(prior_rows)] != prior_rows:
            raise JournalError("revision_conflict", "checkpoint cannot rewrite admitted revision originals or receipts", 409)
        receipt_map = {row["ref"]["cursor"]: row for row in cut["receipts"]}
        prior_outputs = {row["publication"]["cursor"]: row for row in old["committed_output_inputs"]} if old else {}
        outputs_seen = set()
        for row in checkpoint["committed_output_inputs"]:
            publication = row["publication"]; cursor = publication["cursor"]
            original = self._ref(_publication_ref(publication), grant, commit, cut["input_cursor"], allow_revoked=True)
            delivered = self.j.db.execute("SELECT served_at FROM dynamic_deliveries WHERE principal_id=? AND consumer_id=? AND cursor=?", (grant.principal_id, grant.expected_worker_id, cursor)).fetchone()
            if (cursor in outputs_seen or publication["payload_type"] != "home-lighting-policy-decision-3" or publication["producer_id"] != grant.producer_id
                    or publication != _normalized(original) or not delivered or not max(publication["committed_at"], delivered[0]) <= row["received_at"] <= self.j.clock()
                    or cursor in prior_outputs and prior_outputs[cursor] != row):
                raise JournalError("invalid_reference", "committed policy proof lacks unchanged authenticated original delivery", 409)
            outputs_seen.add(cursor)
        for row in checkpoint["revisions"]["rows"]:
            original = self._ref(_publication_ref(row["publication"]), grant, commit, cut["input_cursor"], allow_revoked=True)
            receipt = receipt_map.get(original["cursor"])
            if row["publication"] != _normalized(original) or not receipt or row["received_at"] != receipt["received_at"]:
                raise JournalError("invalid_reference", "revision row lacks its unchanged original receipt", 409)
        outputs = [body for body in commit["outputs"] if body["schema"] == "home-experience-goal-1"]
        if len({body["goal_id"] for body in checkpoint["goal_heads"]}) != len(checkpoint["goal_heads"]):
            raise JournalError("revision_conflict", "derived goal head repeated", 409)
        old_count = old["goal_revision_count"] if old else 0
        if checkpoint["goal_revision_count"] != old_count + len(outputs):
            raise JournalError("revision_conflict", "goal revision count differs from admitted outputs", 409)
        durable = [row["publication"]["payload"] for row in store.snapshot()["rows"] if row["publication"]["payload_type"] == "home-experience-goal-1"]
        old_heads = old["goal_heads"] if old else []
        for goal in checkpoint["goal_heads"]:
            if goal not in outputs + durable + old_heads:
                raise JournalError("invalid_reference", "goal head is neither staged nor previously committed", 409)
        pipeline = checkpoint["pipeline"]
        if pipeline and checkpoint["reservations"] != pipeline["reservations"]:
            raise JournalError("invalid_reference", "worker and pipeline reservations differ", 409)
        for reservation in checkpoint["reservations"]:
            requests = pipeline["policy"]["requests"] if pipeline and pipeline["policy"] else []
            leases = [request["lease"] for request in requests if request["lease"] and digest(request["lease"]) == reservation["lease_digest"]]
            if (len(leases) != 1 or reservation["fences"] != leases[0]["fences"]
                    or reservation["expires_at"] > leases[0]["valid_until"] or reservation["issued_at"] > cut["as_of_t"]):
                raise JournalError("invalid_reference", "reservation lacks its bounded exact lease", 409)
        exposure = pipeline["exposure_context"] if pipeline else None
        if exposure:
            prior_exposure = old["pipeline"]["exposure_context"] if old and old["pipeline"] else None
            known_requests = (pipeline["policy"]["requests"] if pipeline["policy"] else []) + (prior_exposure["requests"] if prior_exposure else [])
            if (exposure["installed_at"] > cut["as_of_t"] or len({row["id"] for row in exposure["requests"]}) != len(exposure["requests"])
                    or len({row["id"] for row in exposure["targets"]}) != len(exposure["targets"])
                    or len({row["target_id"] for row in exposure["support_bounds"]}) != len(exposure["targets"])
                    or any(request not in known_requests for request in exposure["requests"])):
                raise JournalError("invalid_reference", "exposure context changed its installed demand", 409)
            for target in exposure["targets"]:
                requests = [request for request in exposure["requests"] if request["id"] == target["request_id"]]
                bounds = [bound for bound in exposure["support_bounds"] if bound["target_id"] == target["id"]]
                if (len(requests) != 1 or len(bounds) != 1 or bounds[0]["valid_until"] > requests[0]["valid_until"]
                        or any(target[key] != requests[0][key] for key in ("goal_ref", "beneficiary", "target", "association_generation", "role", "objectives"))):
                    raise JournalError("invalid_reference", "past exposure target changed its installed request/support", 409)
                publication = self._ref(target["geometry_ref"], grant, commit, cut["input_cursor"], allow_revoked=True)
                payload = publication["payload"]
                matches = ([item for item in payload["objects"] if item["object_ref"] == target["target"]["object_ref"]]
                           if payload["schema"] == "home-geometry-3" else [payload] if payload["schema"] == "home-registered-surface-1" else [])
                if len(matches) != 1 or any(target[key] != matches[0][key] for key in ("patches", "uncertainty")):
                    raise JournalError("invalid_reference", "past exposure geometry differs from its immutable original", 409)
                original = matches[0]
                if target["role"] == "benefit":
                    material = next((row for row in self.materials if row["object_ref"] == target["target"]["object_ref"]), None)
                    midpoint = [sum(patch["centre_m"][i] for patch in original["patches"]) / 2 for i in range(3)]
                    if (payload["schema"] != "home-geometry-3" or original["state"] != "open" or not material
                            or target["beneficiary"]["person_ref"] != original["person_ref"]
                            or target["association_generation"] != original["association_generation"]
                            or target["source_id"] != material["source_id"] or target["registration_ref"] != material["registration_ref"]
                            or target["uncertainty_correlation_ref"] != material["uncertainty_correlation_ref"]
                            or target["transform_origin_m"] != midpoint or target["aim_anchor_m"] != midpoint
                            or material["transform_origin"] == "declared_static_protocol_origin" and midpoint != material["transform_origin_m"]):
                        raise JournalError("invalid_reference", "past benefit exposure changed its original attribution/material", 409)
                elif (payload["schema"] != "home-registered-surface-1" or target["target"]["registration_ref"] != original["registration_ref"]
                        or target["registration_ref"] != original["registration_ref"] or target["source_id"] != original["source_id"]
                        or target["aim_anchor_m"] is not None
                        or any(target[key] != original[key] for key in ("transform_origin_m", "uncertainty_correlation_ref"))):
                    raise JournalError("invalid_reference", "past protected exposure changed its registered material", 409)
            for row in exposure["environment_heads"]:
                if (self._component(row["ref"], commit, grant, historical=True) != row["body"]
                        or max(row["body"]["effective_at"], row["body"]["available_at"]) > exposure["installed_at"]):
                    raise JournalError("invalid_reference", "past exposure environment differs from original", 409)
        acknowledged = checkpoint["acknowledged_feedback"]
        if acknowledged:
            publication = self._ref(acknowledged["publication_ref"], grant, commit, cut["input_cursor"], allow_revoked=True)
            frontier = acknowledged["frontier"]; receipt = receipt_map.get(publication["cursor"])
            saved = self.j.db.execute("SELECT receipt FROM receipts WHERE producer_id=? AND source_epoch=? AND event_id=?",
                                      (grant.producer_id, commit["journal_epoch"], "worker-commit/" + frontier["commit_id"])).fetchone()
            committed = json.loads(saved[0]) if saved else None
            if (publication["payload_type"] != "home-lighting-control-decision-2" or not receipt
                    or receipt["eligible_at"] > cut["as_of_t"] or acknowledged["received_at"] != receipt["received_at"]
                    or publication["payload"]["control_seq"] != frontier["control_seq"]
                    or digest(publication["payload"]["execution"]["feedback"]) != frontier["component_digest"]
                    or not committed or publication["cursor"] not in {r["cursor"] for r in committed["receipts"]}):
                raise JournalError("invalid_reference", "feedback frontier lacks its later eligible committed original", 409)
        control = next((body for body in commit["outputs"] if body["schema"] == "home-lighting-control-decision-2"), None)
        if control:
            self._feedback_exposures(control, commit, grant, old)
            expected = {"commit_id": commit["id"], "control_seq": checkpoint["control_seq"],
                        "component_digest": digest(control["execution"]["feedback"])}
            if checkpoint["pending_feedback"] != expected:
                raise JournalError("invalid_reference", "feedback frontier differs from the atomic output", 409)
        elif checkpoint["pending_feedback"] != (old["pending_feedback"] if old else None):
            raise JournalError("invalid_reference", "non-control commit changed its feedback frontier", 409)

    def worker_commit(self, commit, grant):
        self.validate_payload(commit)
        if commit["schema"] != "home-context-worker-commit-4":
            raise JournalError("invalid_commit", "adaptive commit-4 required")
        grant.require("worker_commit", commit["site_id"], commit["room_id"])
        bounded_json(commit, self.j.limits.max_worker_commit_bytes)
        checkpoint, cut = commit["checkpoint"], commit["input_cut"]
        if any(not _same_scope(commit, body) for body in (checkpoint, cut)) or any(body["runtime_epoch"] != commit["runtime_epoch"] or body["journal_epoch"] != commit["journal_epoch"] for body in (checkpoint, cut)):
            raise JournalError("scope_mismatch", "cut/checkpoint runtime differs from commit")
        def apply():
            marker = "worker-commit/" + commit["id"]
            retry = self.j.db.execute("SELECT digest,receipt FROM receipts WHERE producer_id=? AND source_epoch=? AND event_id=?", (grant.producer_id, commit["journal_epoch"], marker)).fetchone()
            if retry:
                if retry["digest"] != digest(commit):
                    raise JournalError("identity_conflict", "commit retry changed its original body", 409)
                return json.loads(retry["receipt"])
            self._refresh_heads(commit["site_id"], commit["room_id"])
            if (commit["journal_epoch"] != self.j.stream_epoch or checkpoint["interface_digest"] != self.j.schemas.interface_digest
                    or checkpoint["config_digest"] != self.config_digest):
                raise JournalError("reset_required", "journal/interface/trusted configuration changed", 409)
            row = self.j.db.execute("SELECT * FROM workers WHERE worker_id=? AND site_id=? AND room_id=?", (grant.expected_worker_id, commit["site_id"], commit["room_id"])).fetchone()
            previous, seq = (row["input_cursor"], row["context_seq"]) if row else (0, 0)
            old = json.loads(row["checkpoint"]) if row else None
            if commit["expected_input_cursor"] != previous or commit["expected_commit_seq"] != seq:
                raise JournalError("checkpoint_conflict", "worker checkpoint advanced concurrently", 409)
            semantic, control = (old["semantic_seq"], old["control_seq"]) if old else (-1, 0)
            delta = {"bootstrap": (0, 0), "semantic": (1, 0), "control": (0, 1), "combined": (1, 1)}[commit["kind"]]
            if (checkpoint["commit_seq"] != seq + 1 or checkpoint["semantic_seq"] != semantic + delta[0]
                    or checkpoint["control_seq"] != control + delta[1] or checkpoint["input_cursor"] != cut["input_cursor"]
                    or checkpoint["last_t"] != cut["as_of_t"]):
                raise JournalError("invalid_commit", "checkpoint counters differ from commit kind")
            if old and (not _same_scope(old, commit) or checkpoint["runtime_epoch"] != old["runtime_epoch"] or checkpoint["last_t"] < old["last_t"]):
                raise JournalError("checkpoint_conflict", "worker scope/runtime/clock changed", 409)
            if commit["kind"] == "bootstrap" and (row or cut["as_of_t"] != 0):
                raise JournalError("invalid_commit", "bootstrap requires a fresh zero-clock worker")
            claimed = self._validate_cut(commit, grant, previous)
            counts = {name: sum(body["schema"] == name for body in commit["outputs"]) for name in ADAPTIVE_OUTPUTS}
            if (counts["home-engagement-state-3"] != delta[0] or counts["home-lighting-policy-decision-3"] != delta[0]
                    or counts["home-lighting-control-decision-2"] != delta[1] or counts["home-experience-goal-1"] and not delta[0]):
                raise JournalError("invalid_commit", "adaptive output set differs from commit kind")
            outcomes = self._intent_ledger(commit, old)
            self._frozen_head_proofs(commit, grant, old)
            self._intent_authority_bindings(commit, old, outcomes)
            post_authority = self._post_authority(commit, outcomes)
            if checkpoint["authority"] != post_authority:
                raise JournalError("authority_conflict", "post-semantic map contains an unauthorized authority delta", 409)
            self._checkpoint_references(commit, grant, old)
            output_receipts = []
            for output in commit["outputs"]:
                if output["schema"] != "home-experience-goal-1" and (output["t"] != cut["as_of_t"] or output["semantic_seq"] != checkpoint["semantic_seq"]):
                    raise JournalError("invalid_commit", "output cutoff/semantic sequence differs")
                grant.require("worker_commit", commit["site_id"], commit["room_id"], payload_type=output["schema"], revision=str(self.j.schemas.payload_versions[output["schema"]]) + ".0")
                if output["schema"] == "home-experience-goal-1":
                    self._authorization(output, grant, "derive")
                    if output["issued_at"] != cut["as_of_t"] or output["effective_at"] > cut["as_of_t"] or output["available_at"] > cut["as_of_t"]:
                        raise JournalError("invalid_commit", "derived goal must be issued at its semantic cutoff")
                elif output["schema"] == "home-lighting-control-decision-2":
                    bounded_json(output, 32 * 1024)
                    if output["control_seq"] != checkpoint["control_seq"] or output["input_cut_digest"] != cut["digest"]:
                        raise JournalError("invalid_commit", "control differs from cut/sequence")
                    prior_row = self.j.db.execute("SELECT body FROM publications WHERE site_id=? AND room_id=? AND payload_type='home-lighting-control-decision-2' ORDER BY cursor DESC LIMIT 1", (commit["site_id"], commit["room_id"])).fetchone()
                    prior_t = json.loads(prior_row[0])["payload"]["t"] if prior_row else 0
                    execution = output["execution"]
                    if execution["fixture_ids"] != [fixture["id"] for fixture in self.j.adaptive_config["config"]["registry"]["fixtures"]]:
                        raise JournalError("invalid_reference", "execution fixture dictionary differs from configured registry order", 409)
                    if (abs(execution["from_t"] - prior_t) > 1e-9 or abs(output["t"] - prior_t - .2) > 1e-9
                            or len(execution["samples"]) != 10 or any(abs(sample["t"] - prior_t - (i + 1) * .02) > 1e-9 for i, sample in enumerate(execution["samples"]))):
                        raise JournalError("invalid_commit", "control requires ten immutable past20ms samples on the5Hz grid", 409)
                    profile = self.j.adaptive_config["config"]["profile"]
                    if execution["feedback"]["model_ref"] != profile["profile_ref"] or execution["feedback"]["model_digest"] != digest(profile):
                        raise JournalError("invalid_reference", "outcome feedback model differs from trusted configuration", 409)
                    self._geometry(output["geometry_snapshot"], cut, commit, grant, claimed)
                    for reference in output["consumed_refs"]:
                        self._component(reference, commit, grant, historical=True)
                    for target in output["allocation"]["targets"]:
                        self._target(target, output, commit, grant)
                elif output["schema"] == "home-lighting-policy-decision-3":
                    for reference in output["consumed_refs"]:
                        self._component(reference, commit, grant, historical=True)
                event = self._event_for(output, grant); self.j._event(event)
                if self.j._receipt(event):
                    raise JournalError("output_identity_reused", "new commit reused an output publication identity", 409)
                output_receipts.append(self.append(event, context_seq=seq + 1, grant=grant))
                if output["schema"] == "home-lighting-policy-decision-3":
                    for outcome in output["intent_results"]:
                        rows = self.j.db.execute("SELECT producer_id,source_epoch,event_id,event,application_result FROM receipts WHERE event_id=?", (outcome["intent_id"],)).fetchall()
                        matches = [item for item in rows if json.loads(item["event"]).get("payload_type") == "home-intent-event-3" and _same_scope(json.loads(item["event"]), commit)]
                        if len(matches) != 1:
                            raise JournalError("invalid_intent_result", "outcome requires one scoped durable instruction", 409)
                        saved = matches[0]
                        application = {"schema": "home-intent-application-result-3", "version": 3,
                                       "id": "intent-result:" + digest([commit[key] for key in SCOPE_KEYS] + [outcome["intent_id"]]),
                                       **self.identity, "clock_profile_digest": self.clock_profile["digest"], **outcome,
                                       "available_at": outcome["decided_at"], "truth_role": "runtime_application_result"}
                        self.validate_payload(application)
                        if saved["application_result"] and json.loads(saved["application_result"]) != application:
                            raise JournalError("intent_result_conflict", "first durable application result cannot change", 409)
                        self.j.db.execute("UPDATE receipts SET application_result=? WHERE producer_id=? AND source_epoch=? AND event_id=?", (canonical(application), saved["producer_id"], saved["source_epoch"], saved["event_id"]))
            for follow in post_authority["follows"]:
                self.j._set_meta("adaptive-follow:" + self._follow_key(follow), canonical({key: follow[key] for key in ("follow_generation", "follow_allowed")}))
            self.j.db.execute("INSERT INTO workers VALUES(?,?,?,?,?,?) ON CONFLICT(worker_id,site_id,room_id) DO UPDATE SET input_cursor=excluded.input_cursor,context_seq=excluded.context_seq,checkpoint=excluded.checkpoint", (grant.expected_worker_id, commit["site_id"], commit["room_id"], checkpoint["input_cursor"], seq + 1, canonical(checkpoint)))
            result = {"schema": "home-context-worker-commit-receipt-4", "id": commit["id"], "worker_id": grant.expected_worker_id,
                      "journal_epoch": self.j.stream_epoch, "input_cursor": checkpoint["input_cursor"], "commit_seq": seq + 1,
                      "semantic_seq": checkpoint["semantic_seq"], "control_seq": checkpoint["control_seq"], "cursor": int(self.j._meta("cursor")), "receipts": output_receipts}
            self.j.db.execute("INSERT INTO receipts(producer_id,source_epoch,event_id,digest,receipt,event) VALUES(?,?,?,?,?,?)", (grant.producer_id, commit["journal_epoch"], marker, digest(commit), canonical(result), canonical({"schema": "home-context-worker-commit-tombstone-4", **self.identity})))
            self._storage_budget()
            return result
        return self.j._transaction(apply)

    def lookup_commit(self, commit_id, grant, site, room):
        grant.require("read", site, room); grant.require("worker_commit", site, room); self._scope_check(site, room)
        with self.j.lock:
            row = self.j.db.execute("SELECT receipt FROM receipts WHERE producer_id=? AND source_epoch=? AND event_id=?", (grant.producer_id, self.j.stream_epoch, "worker-commit/" + commit_id)).fetchone()
            if not row:
                raise JournalError("not_found", "commit is outside the configured worker journal", 404)
            receipt = json.loads(row[0]); publications = []
            for output in receipt["receipts"]:
                original = self.j.db.execute("SELECT body FROM publications WHERE cursor=?", (output["cursor"],)).fetchone()
                publication = json.loads(original[0])
                if publication["payload_type"] not in grant.payload_types or not self._read_allowed(publication, grant):
                    raise JournalError("forbidden", "commit original is outside worker read rights", 403)
                publications.append(_normalized(publication))
            return {"schema": "home-context-adaptive-commit-response-1", "receipt": receipt, "publications": publications}

    def _snapshot_dependencies(self, items, grant, through, now):
        queue = list(items.values()); visited = set(); revision_cursors = set()
        while queue:
            publication = queue.pop(); payload = publication["payload"]
            if publication["cursor"] in visited:
                continue
            visited.add(publication["cursor"])
            if payload["schema"] not in REVISION_FIELDS:
                continue
            revision_cursors.add(publication["cursor"])
            references = payload.get("preference_refs", []) + ([payload["declaration_ref"]] if payload.get("declaration_ref") else [])
            candidates = []
            for reference in references:
                ref = reference["publication_ref"]
                if ref is None or ref["journal_epoch"] != self.j.stream_epoch or ref["cursor"] > through:
                    continue
                original = self.j.db.execute("SELECT body FROM publications WHERE cursor=?", (ref["cursor"],)).fetchone()
                if original:
                    body = json.loads(original[0])
                    if (body["payload_digest"] != ref["payload_digest"] or body["payload"]["id"] != reference["component_id"]
                            or body["payload_digest"] != reference["component_digest"]):
                        raise JournalError("invalid_reference", "snapshot dependency differs from its original component", 409)
                    candidates.append(body)
            predecessor = payload["expected_predecessor"]
            if predecessor:
                for row in self.j.db.execute("SELECT body FROM publications WHERE cursor<=? AND payload_type=?", (through, payload["schema"])):
                    body = json.loads(row[0]); previous = body["payload"]
                    if (revision_chain_key(previous) == revision_chain_key(payload) and previous["revision"] == predecessor["revision"]
                            and body["payload_digest"] == predecessor["digest"]):
                        candidates.append(body)
            for original in candidates:
                if (original["payload_type"] not in REVISION_FIELDS or original["payload_type"] not in grant.payload_types
                        or original["payload_version"] not in grant.revisions or not self._read_allowed(original, grant)
                        or not _same_scope(original["payload"], self.identity) or self._eligible(original) > now):
                    continue
                if original["cursor"] not in items:
                    items[original["cursor"]] = _normalized(original); queue.append(original)
            if len(revision_cursors) > 96:
                raise JournalError("snapshot_budget", "revision dependency closure exceeds 96 originals", 507)

    def snapshot(self, grant, site, room, consumer_id=None):
        grant.require("read", site, room); self._scope_check(site, room)
        def read():
            now = assert_relative_time(self.j.clock(), self.clock_profile)
            visible = self._visible(grant, site, room, 0, int(self.j._meta("cursor")), inputs_only=True)
            for row in visible:
                publication = json.loads(row["body"])
                if (not self.j._revoked(site, room, row["source_id"], row["revision"])
                        and self._eligible(publication) > now):
                    raise JournalError("snapshot_pending_inputs", "snapshot has pending originals; retry after eligibility or use cut/checkpoint recovery", 409)
            self._expire_requests(site, room); self._refresh_heads(site, room)
            tail = int(self.j._meta("cursor"))
            items, components, revision_heads, family_heads, masks = {}, [], [], [], {}
            for record in self._qualified_components(site, room, now):
                if (record["payload_type"] not in grant.payload_types or record["revision"] not in grant.revisions
                        or self.j._revoked(site, room, record["source_id"], record["revision"])):
                    continue
                publication = record["publication"]
                items[publication["cursor"]] = _normalized(publication)
                components.append(record["head"])
                masks.setdefault(publication["cursor"], set()).add(component_id(record["component"], record["payload_type"] == "home-geometry-3"))
            for head in self._revision_store().materialize(now):
                publication = head["row"]["publication"]
                payload = publication["payload"]
                if (payload["schema"] not in grant.payload_types or publication["payload_version"] not in grant.revisions
                        or not self._read_allowed(publication, grant)):
                    continue
                items[publication["cursor"]] = publication
                revision_heads.append({"publication_ref": _publication_ref(publication), "payload_type": payload["schema"],
                                       "owner_id": payload["owner_id"], "subject": payload["subject"],
                                       "chain_id": payload[REVISION_FIELDS[payload["schema"]]], "revision": payload["revision"],
                                       "eligible_at": head["eligible_at"], "valid_until": payload["valid_until"], "disposition": head["disposition"]})
            for key, (row, publication) in sorted(self._family_heads(site, room, now).items()):
                if (row["payload_type"] not in grant.payload_types or row["revision"] not in grant.revisions
                        or self.j._revoked(site, room, row["source_id"], row["revision"])):
                    continue
                items[row["cursor"]] = _normalized(publication)
                family_heads.append({"publication_ref": _publication_ref(publication), "payload_type": row["payload_type"],
                                     "producer_id": publication["producer_id"], "chain_id": key, "eligible_at": self._eligible(publication),
                                     "valid_until": publication["payload"]["valid_until"],
                                     "disposition": "expired" if publication["payload"]["valid_until"] <= now else "active"})
            other_heads = {}
            for row in self._visible(grant, site, room, 0, tail):
                publication = json.loads(row["body"]); payload = publication["payload"]
                if row["payload_type"] in REVISION_FIELDS or row["payload_type"] in ADAPTIVE_FAMILIES or row["payload_type"] in {"home-fact-batch-2", "home-geometry-3"}:
                    continue
                if row["payload_type"] not in self.j.schemas.publication_families or self._eligible(publication) > now:
                    continue
                if self.j._revoked(site, room, row["source_id"], row["revision"]):
                    continue
                if row["payload_type"] in {"home-intent-event-3", "home-evidence-request-2", "home-evidence-reply-2"}:
                    key = canonical([row["payload_type"], publication["producer_id"], payload.get("request_id", payload["id"])])
                else:
                    key = canonical([row["payload_type"], publication["producer_id"]])
                other_heads[key] = publication
            for publication in other_heads.values():
                items[publication["cursor"]] = _normalized(publication)
            self._snapshot_dependencies(items, grant, tail, now)
            result = {"schema": "home-context-snapshot-3", "version": 3, **self.identity,
                      "clock_profile_digest": self.clock_profile["digest"], "stream_epoch": self.j.stream_epoch,
                      "cursor": tail, "as_of_t": now, "authority": self.authority(site, room),
                      "items": [items[key] for key in sorted(items)],
                      "component_heads": sorted(components, key=lambda h: (h["publication_ref"]["cursor"], canonical(h))),
                      "revision_heads": revision_heads, "family_heads": family_heads}
            self.validate_payload(result); bounded_json(result, self.j.limits.max_response_bytes)
            consumer = consumer_id or grant.principal_id; identifier(consumer, "consumer_id")
            for cursor, ids in masks.items():
                self.j.db.execute("INSERT OR IGNORE INTO dynamic_snapshot_masks VALUES(?,?,?,?,?)",
                                  (grant.principal_id, consumer, cursor, canonical(sorted(ids)), now))
            if self.j.db.execute("SELECT count(*) FROM dynamic_snapshot_masks").fetchone()[0] > 2048:
                raise JournalError("snapshot_budget", "snapshot selector proof budget exhausted", 507)
            self._storage_budget()
            self._delivered_publications(grant, consumer, list(items.values()))
            self.j._delivered(grant, consumer, site, room, tail)
            return result
        return self.j._transaction(read)

    def _cached_estimate(self, request):
        self._refresh_heads(request["site_id"], request["room_id"])
        authority = self.authority(request["site_id"], request["room_id"])
        rows = self._qualified_components(request["site_id"], request["room_id"], self.j.clock())
        # Select the current lineage before compatibility: a changed epoch,
        # person, association, closed state or expiry must fence older support.
        candidates = [(row, row["head"]) for row in rows if row["payload_type"] == "home-geometry-3"]
        candidates = [(row, head) for row, head in candidates if _same_scope(head["object_ref"], request)
                      and head["object_ref"]["oid"] == request["object_ref"]["oid"]]
        if not candidates:
            return None
        row, head = max(candidates, key=lambda r: (r[1]["observed_at"], GEOMETRY_PRIORITY[r[0]["component"]["state"]], r[0]["publication"]["payload_digest"]))
        if self.j._revoked(row["site_id"], row["room_id"], row["source_id"], row["revision"]):
            return None
        p = row["publication"]; g = p["payload"]; obj = row["component"]
        correction = None
        for original in self.j.db.execute("SELECT body FROM publications WHERE site_id=? AND room_id=? AND payload_type='home-intent-event-3'", (request["site_id"], request["room_id"])).fetchall():
            declared = json.loads(original[0]); i = declared["payload"]
            if (i["kind"] == "association_correction" and _same_scope(i, request) and i["object_ref"]["oid"] == request["object_ref"]["oid"]
                    and self._eligible(declared) <= self.j.clock()):
                application = self.j.db.execute("SELECT application_result FROM receipts WHERE event_id=? AND producer_id=? AND source_epoch=?",
                                                (i["id"], declared["producer_id"], self.j.stream_epoch)).fetchone()
                if not application or not application[0] or json.loads(application[0])["status"] != "applied":
                    continue
                correction = max(correction if correction is not None else -1, i["available_at"])
        registration_ref = self._registration_for_geometry(g)
        registration = next((r for r in authority["registrations"] if r["registration_ref"] == registration_ref), None)
        if (not registration or registration["revoked"] or g["registration_generation"] != registration["registration_generation"] or head["object_ref"] != request["object_ref"]
                or head["person_ref"] != request["person_ref"] or obj["state"] != "open"
                or correction is not None and g["observed_at"] <= correction
                or not 0 <= self.j.clock() - g["observed_at"] <= .5
                or self.j.clock() >= min(g["valid_until"], g["observed_at"] + 1)):
            return None
        facts = []
        for fact_row in (r for r in rows if r["payload_type"] == "home-fact-batch-2"):
            fact_head = fact_row["head"]
            if (not _same_scope(fact_head["object_ref"], request) or fact_head["object_ref"]["oid"] != request["object_ref"]["oid"]
                    or fact_head["observed_at"] < g["observed_at"]
                    or self.j._revoked(fact_row["site_id"], fact_row["room_id"], fact_row["source_id"], fact_row["revision"])):
                continue
            if (fact_head["object_ref"] != request["object_ref"] or fact_head["person_ref"] != request["person_ref"]
                    or fact_head["association_generation"] != head["association_generation"]):
                return None
            facts.append(fact_row["component"])
        if any(f["kind"] in {"closed", "source_gap", "negative_use"} and f["value"] or f["kind"] == "use" and not f["value"] for f in facts):
            return None
        for kind in {f["kind"] for f in facts}:
            if len({f["value"] for f in facts if f["kind"] == kind}) > 1:
                return None
        if obj["uncertainty"]["translation_radius_m"] <= request["precision_m"] and obj["uncertainty"]["rotation_radius_rad"] <= request["orientation_rad"]:
            return dict(head["publication_ref"])
        return None

    def lookup_intent(self, event_id, grant, site, room):
        grant.require("read", site, room, payload_type="home-intent-event-3", revision="3.0")
        with self.j.lock:
            rows = self.j.db.execute("SELECT event,receipt,application_result FROM receipts WHERE event_id=?", (event_id,)).fetchall()
            matches = [row for row in rows if json.loads(row["event"]).get("payload_type") == "home-intent-event-3"
                       and _same_scope(self.identity, json.loads(row["event"]))]
            if len(matches) != 1:
                raise JournalError("not_found", "intent not found in granted scope", 404)
            row = matches[0]; payload = json.loads(row["event"])["payload"]
            self._intent_subject(payload, grant, "read")
            return {"intent": payload, "receipt": json.loads(row["receipt"]),
                    "application_result": json.loads(row["application_result"]) if row["application_result"] else None}

    def revoke(self, event, grant):
        self.j._event(event)
        payload = event["payload"]
        if payload["schema"] != "home-context-revocation-1":
            raise JournalError("unsupported_schema", "explicit source revocation required")
        grant.require("revoke", event["site_id"], event["room_id"], payload_type=event["payload_type"],
                      revision=event["revision"], producer_id=event["producer_id"])
        def apply():
            retry = self.j._receipt(event)
            if retry:
                return retry
            receipt = self.append(event)
            self.j.db.execute("INSERT OR IGNORE INTO revocations VALUES(?,?,?,?)",
                              (event["site_id"], event["room_id"], payload["source_id"], payload["revision"]))
            authority = json.loads(self.j._meta("adaptive-authority"))
            if payload["source_id"] == "*":
                authority["room"]["motion_generation"] += 1
                authority["room"]["motion_allowed"] = False
            for source in authority["sources"]:
                if payload["source_id"] in ("*", source["source_id"]):
                    source["revocation_generation"] += 1; source["revoked"] = True
            for registration in authority["registrations"]:
                if payload["source_id"] in ("*", self._registrations[registration["registration_ref"]][0]):
                    registration["registration_generation"] += 1; registration["revoked"] = True
            self.j._set_meta("adaptive-authority", canonical(authority))
            self._storage_budget(True)
            return receipt
        return self.j._transaction(apply)


if __name__ == "__main__":
    try:
        values = json.load(sys.stdin)
        result = [validate_adaptive(value) for value in values] if isinstance(values, list) else validate_adaptive(values)
        print(json.dumps(result, separators=(",", ":")))
    except (ValueError, TypeError, KeyError, JournalError):
        print('{"ok":false,"errors":["invalid_json"]}')
        sys.exit(1)
