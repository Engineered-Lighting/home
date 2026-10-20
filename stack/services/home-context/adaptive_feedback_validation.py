"""Closed admission for the separately versioned feedback synthetic profile.

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


SERVICE_PROFILE = "adaptive-book-feedback-synthetic-1"
CLOCK_PROFILE = "relative-session-seconds-1"
_MISSING = object()
_FRESH_STORED_PRIOR = object()
_PHASE_VIEWS_FALLBACK = object()
SCOPE_KEYS = ("site_id", "room_id", "session_id", "domain_epoch")
REVISION_FIELDS = {"home-preference-snapshot-2": "preference_set_id",
                   "home-experience-context-declaration-1": "context_set_id",
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
        if (field is None or not _same_scope(self._identity, payload) or payload.get("version") != (2 if payload.get("schema") == "home-preference-snapshot-2" else 1)
                or not _identifier(payload.get("id")) or publication.get("id") != payload["id"]
                or publication.get("payload_type") != payload["schema"] or publication.get("payload_version") != str(payload.get("version")) + ".0"
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
                      and r["publication"]["producer_epoch"] == publication["producer_epoch"]
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
        if (not validate_feedback(surface, registry)["ok"] or surface["schema"] != "home-registered-surface-1"
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


def validate_feedback(value, registry=None):
    """Pinned closed public contract validation, with explicit relative clocks."""
    from schema_registry import ADAPTIVE_NEW_VERSIONS, SchemaRegistry
    if registry is None:
        registry = SchemaRegistry((), profile=SERVICE_PROFILE)
    errors = []
    try:
        registry.validate(value)
    except (JournalError, TypeError, ValueError, UnicodeError):
        return {"ok": False, "errors": ["schema"]}
    if value["schema"] not in ADAPTIVE_NEW_VERSIONS and value["schema"] != "home-context-snapshot-5":
        from dynamic_validation import validate_dynamic
        errors.extend(validate_dynamic(value, registry)["errors"])
    clock_fields = {"observed_at", "available_at", "valid_until", "issued_at", "effective_at", "committed_at",
                    "received_at", "as_of_t", "last_t", "last_installed_t", "next_semantic_t", "next_control_t", "from_t", "t",
                    "expires_at", "decided_at", "eligible_at", "support_valid_until", "environment_valid_until",
                    "settle_until", "started_at", "last_negative_at", "last_positive_at", "last_observed_at",
                    "support_until", "negative_support_until", "conflict_observed_at", "first_use", "last_use",
                    "last_progress_at", "negative_progress_t"}
    if value["schema"] not in ADAPTIVE_NEW_VERSIONS and value["schema"] != "home-context-snapshot-5":
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
                publication_errors = validate_feedback(item["payload"], registry)["errors"]
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
            if item.get("schema") in {"home-dynamic-geometry-snapshot-3", "home-control-input-cut-3", "home-context-replay-8"}:
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
            if item.get("schema") == "home-lighting-control-decision-3":
                if (any(item[key]["t"] != item["t"] for key in ("allocation", "execution"))
                        or item["geometry_snapshot"]["as_of_t"] != item["t"]
                        or any(item[key]["control_seq"] != item["control_seq"] for key in ("allocation", "execution"))):
                    errors.append("control_binding")
            if item.get("schema") == "home-lighting-execution-3":
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
    if name in {"home-dynamic-geometry-snapshot-3", "home-control-input-cut-3", "home-context-replay-8"}:
        if digest({key: item for key, item in value.items() if key != "digest"}) != value["digest"]:
            errors.append("digest_mismatch")
    if name == "home-lighting-request-4":
        if (value["role"] == "protected" and (value["lease"] is not None or value["follow_mode"] != "registered_static")
                or value["role"] != "protected" and (value["lease"] is None or value["target"]["kind"] != "object"
                                                     or value["beneficiary"]["kind"] != "person")):
            errors.append("lease_role")
    if name == "home-lighting-environment-1" and value["observed_at"] > value["available_at"]:
        errors.append("observation_clock")
    if name == "home-context-replay-8":
        protocol = value["reference_protocol"]
        if ((protocol["mode"] == "ordinary") != protocol["ordinary_acceptance_eligible"]):
            errors.append("reference_diagnostic_admission")
    return {"ok": not errors, "errors": errors}


from dynamic_validation import DynamicProfile, GEOMETRY_PRIORITY, component_id, object_key
SCOPE = SCOPE_KEYS
same_scope = _same_scope


ADAPTIVE_INPUTS = frozenset({"home-fact-batch-3", "home-geometry-4", "home-intent-event-3",
                           "home-evidence-request-3", "home-evidence-reply-3", "home-preference-snapshot-2",
                           "home-goal-declaration-1", "home-registered-surface-1", "home-lighting-environment-1",
                           "home-lighting-capability-1", "home-experience-goal-1", "home-lighting-control-decision-3"})
ADAPTIVE_OUTPUTS = frozenset({"home-engagement-state-4", "home-experience-goal-1",
                            "home-lighting-policy-decision-3", "home-lighting-control-decision-3"})
ADAPTIVE_FAMILIES = frozenset({"home-registered-surface-1", "home-lighting-environment-1", "home-lighting-capability-1"})
ADAPTIVE_QUOTAS = {"home-geometry-4": 600, "home-fact-batch-3": 240,
                   "home-lighting-control-decision-3": 600, "home-engagement-state-4": 120,
                   "home-lighting-policy-decision-3": 120, "home-experience-goal-1": 32,
                   "home-preference-snapshot-2": 16, "home-goal-declaration-1": 32,
                   "home-intent-event-3": 32, "home-lighting-environment-1": 120,
                   "home-lighting-capability-1": 4, "home-registered-surface-1": 4,
                   "home-source-health-1": 16, "home-context-revocation-1": 8,
                   "home-evidence-request-3": 8, "home-evidence-reply-3": 8}


def _normalized(publication):
    return copy.deepcopy(publication)


def _publication_ref(publication):
    return {key: publication[key] for key in ("journal_epoch", "cursor", "payload_digest")}


def original_family_clocks(payload, committed_at):
    """Authored decision/policy clocks; never substitute them for captures."""
    family=payload.get("schema")
    if family=="home-experience-classifier-disposition-1":
        decision,available=payload.get("normalized",{}).get("as_of"),payload.get("normalized",{}).get("available_at")
    elif family=="home-preference-selector-memory-policy-1":
        decision,available=payload.get("normalized",{}).get("effective_at"),payload.get("normalized",{}).get("available_at")
    else:
        decision=next((payload[k] for k in ("effective_at","observed_at","t","issued_at","decided_at","available_at") if k in payload),None)
        available=next((payload[k] for k in ("available_at","t","decided_at","effective_at","observed_at","issued_at") if k in payload),None)
    if not finite(decision) or not finite(available):
        raise JournalError("invalid_input_receipt","original family has no authored eligibility clocks",409)
    return decision,available


def _utf16_sort_key(value):
    """Match JavaScript's canonical-string comparison, including astral IDs."""
    return value.encode("utf-16-be",errors="surrogatepass")


class AdaptiveFeedbackProfile(DynamicProfile):
    """Isolated adaptive journal dialect; the preserved dynamic profile is unchanged."""

    def __init__(self, journal):
        super().__init__(journal)
        self.identity = copy.deepcopy(journal.adaptive_feedback_config["identity"])
        self.clock_profile = copy.deepcopy(journal.adaptive_feedback_config["clock_profile"])
        self.materials = copy.deepcopy(validate_materials(journal.adaptive_feedback_config["materials"], self.identity))
        self.config_digest = journal.adaptive_feedback_config["config_digest"]
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
        for surface in journal.adaptive_feedback_config["registered_surfaces"]:
            binding = registrations.get(surface["registration_ref"])
            if (not binding or binding[0] != surface["source_id"]
                    or binding[1]["registry_digest"] != surface["registry_digest"]):
                raise ValueError("adaptive protected catalogue lacks its exact configured registration")
        initial = {"room": {"motion_generation": 0, "motion_allowed": True},
                   "sources": [{"source_id": source, "revocation_generation": 0, "revoked": False} for source in sources],
                   "registrations": [registrations[ref][1] for ref in sorted(registrations)], "follows": [],
                   "producers": [{"producer_id": p["producer_id"], "producer_epoch": p["producer_epoch"], "revocation_generation": 0, "revoked": False} for p in sorted(journal.adaptive_feedback_config["producer_epochs"], key=lambda p: canonical([p["producer_id"],p["producer_epoch"]]))]}
        self.j._set_meta("adaptive-authority", canonical(initial))

    def validate_payload(self, value):
        result = validate_feedback(value, self.j.schemas)
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
        for row in self.j.db.execute("SELECT body FROM publications WHERE payload_type IN ('home-preference-snapshot-2','home-experience-context-declaration-1','home-goal-declaration-1','home-experience-goal-1') ORDER BY cursor"):
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
            if row["payload_type"] != "home-geometry-4":
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
        if event["payload_type"] != "home-geometry-4":
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
            if row["payload_type"] == "home-geometry-4":
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
        if kind in {"home-geometry-4", "home-fact-batch-3"}:
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
        self._storage_budget(kind in {"home-context-revocation-1", "home-evidence-reply-3", "home-source-health-1", "home-evidence-request-3"})
        return receipt

    def _event_for(self, payload, grant=None, *, producer_id=None, valid_until=None):
        t = payload.get("issued_at", payload.get("observed_at", payload.get("available_at", payload.get("t", self.j.clock()))))
        available=payload.get("available_at",t)
        if payload["schema"] in {"home-experience-classifier-disposition-1","home-preference-selector-memory-policy-1"}:
            t,available=original_family_clocks(payload,self.j.clock())
        version = self.j.schemas.payload_versions[payload["schema"]]
        return {"schema": "home-context-event-1", "schema_version": "1.0", "id": payload["id"],
                "producer_id": producer_id or grant.producer_id, "stream_epoch": self.j._meta("epoch"), **self.identity,
                "observed_at": t, "available_at": available,
                "valid_until": min(120, valid_until if valid_until is not None else payload.get("valid_until", t + 3)),
                "payload_type": payload["schema"], "payload_version": str(version) + ".0", "revision": str(version) + ".0", "payload": payload}

    def append_event(self, event, grant):
        self.j._event(event)
        name, payload = event["payload_type"], event["payload"]
        grant.require("event", event["site_id"], event["room_id"], payload_type=name, revision=event["revision"], producer_id=event["producer_id"])
        if name in ADAPTIVE_OUTPUTS:
            raise JournalError("worker_commit_required", "shared adaptive outputs require one atomic worker commit")
        if name not in {"home-fact-batch-3", "home-geometry-4", "home-source-health-1", *ADAPTIVE_FAMILIES}:
            raise JournalError("typed_route_required", "adaptive command requires its typed route")
        if name in {"home-fact-batch-3", "home-geometry-4"}:
            if any(event[k] != payload[k] for k in ("observed_at", "available_at", "valid_until")):
                raise JournalError("invalid_clock", "source envelope clocks differ from original body")
            records = payload["facts"] if name == "home-fact-batch-3" else [payload]
            if any(record["capture"]["source_id"] not in grant.source_ids for record in records):
                raise JournalError("forbidden", "capture source outside its explicit grant", 403)
            if name == "home-geometry-4":
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
                expected = next((surface for surface in self.j.adaptive_feedback_config["registered_surfaces"] if surface["registration_ref"] == payload["registration_ref"]), None)
                if not expected or any(payload[key] != expected[key] for key in ("source_id", "region_id", "region_epoch", "registration_generation", "registry_digest", "patches", "transform_origin_m", "uncertainty", "uncertainty_correlation_ref", "qualification")):
                    raise JournalError("registration_mismatch", "registered geometry differs from the configuration-bound catalogue", 409)
            elif name == "home-lighting-capability-1":
                profile = self.j.adaptive_feedback_config["config"]["profile"]
                if (payload["profile_ref"] != profile.get("profile_ref") or payload["profile_digest"] != digest(profile)
                        or payload["model_ref"] != profile.get("profile_ref") or payload["model_digest"] != digest(profile)):
                    raise JournalError("capability_mismatch", "capability differs from the configured synthetic model", 409)
        return self.j._transaction(lambda: self.append(event, grant=grant))

    def declaration(self, payload, grant, *, family):
        self.validate_payload(payload)
        if family not in {"home-preference-snapshot-2", "home-goal-declaration-1"} or payload["schema"] != family:
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
            self._delivered_publications(grant, self._consumer(grant,consumer_id), selected)
            self.j._delivered(grant, self._consumer(grant,consumer_id), site, room, next_cursor)
            return {"schema": "home-context-page-1", "stream_epoch": self.j._meta("epoch"), "after": after,
                    "items": selected, "next_cursor": next_cursor, "authority": self.authority(site, room)}
        return self.j._transaction(read)

    def _ref(self, ref, grant, scope, through, *, allow_revoked=False):
        publication = self._base_ref(ref, grant, scope, through, allow_revoked=allow_revoked)
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
                if self._cut_count() >= 1024:
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
            result = {"schema": self.checkpoint_response_schema, "journal_epoch": self.j._meta("epoch"),
                    "worker_id": worker_id, "input_cursor": row["input_cursor"] if row else 0,
                    "commit_seq": row["context_seq"] if row else 0, "semantic_seq": checkpoint["semantic_seq"] if checkpoint else -1,
                    "control_seq": checkpoint["control_seq"] if checkpoint else 0, "checkpoint": checkpoint, "authority": self.authority(site, room)}
            if self.checkpoint_response_version is not None:
                result["version"] = self.checkpoint_response_version
                self.validate_payload(result)
            return result
        return self.j._transaction(read)

    def _stored_checkpoint_view(self, checkpoint, grant):
        return checkpoint

    def _validate_cut(self, commit, grant, previous, *, _authoritative_prior=_FRESH_STORED_PRIOR):
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
        if _authoritative_prior is _FRESH_STORED_PRIOR:
            old_worker = self.j.db.execute("SELECT checkpoint FROM workers WHERE worker_id=? AND site_id=? AND room_id=?", (grant.expected_worker_id, commit["site_id"], commit["room_id"])).fetchone()
            prior_checkpoint = self._stored_checkpoint_view(json.loads(old_worker[0]), grant) if old_worker else None
        else:
            prior_checkpoint = _authoritative_prior
        if prior_checkpoint is not None:
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
            fields = tuple(original)
            if saved != {k: original[k] for k in fields} or saved["cursor"] not in receipts or item["received_at"] != receipts[saved["cursor"]]["received_at"]:
                raise JournalError("invalid_reference", "checkpoint body or original receipt was changed", 409)
            if "component_ids" in item:
                payload = original["payload"]
                ids = item["component_ids"]
                allowed = {component_id(c, payload["schema"] == "home-geometry-4") for c in
                           (payload["objects"] if payload["schema"] == "home-geometry-4" else payload["facts"] if payload["schema"] == "home-fact-batch-3" else [])}
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
                                       and original["payload_type"] in {"home-geometry-4", "home-fact-batch-3"})
            if not discarded_by_revocation and (cursor not in pending_cursors or cursor not in checkpoint_receipts):
                raise JournalError("invalid_input_receipt", "buffered input requires its immutable pending body and original receipt", 409)
        source_facts, source_intents = {}, {}
        for item in retained:
            payload = item["publication"]["payload"]
            if payload["schema"] == "home-fact-batch-3":
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
        value = max(*original_family_clocks(payload,publication["committed_at"]),publication["committed_at"])
        if received_at is not None:
            if not finite(received_at) or not publication["committed_at"] <= received_at <= 120:
                raise JournalError("invalid_input_receipt", "original receipt clock is invalid", 409)
            value = max(value, received_at)
        return value

    def _family_key(self, publication):
        payload = publication["payload"]
        identity=[payload["schema"], *[payload[key] for key in SCOPE_KEYS], publication["producer_id"]]
        if payload["schema"]=="home-lighting-environment-1":
            identity.extend([publication["producer_epoch"],payload["source_id"]])
        elif payload["schema"]=="home-registered-surface-1":
            identity.append(payload["registration_ref"])
        return digest(identity)

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
        for row in self.j.db.execute("SELECT body FROM publications WHERE site_id=? AND room_id=? AND cursor<=? AND payload_type IN ('home-lighting-policy-decision-3','home-lighting-settled-exposure-1','home-lighting-control-decision-3','home-experience-goal-1') ORDER BY cursor", (site, room, through)):
            publication = json.loads(row[0]); body = publication["payload"]
            if publication["producer_id"] != grant.producer_id or publication["producer_epoch"] != grant.producer_epoch or publication["payload_type"] not in grant.payload_types or not self._read_allowed(publication, grant) or self._eligible(publication) > t:
                continue
            key = revision_chain_key(body) if body["schema"] == "home-experience-goal-1" else body["schema"]
            heads[key] = _normalized(publication)
        if len(heads) > 8:
            raise JournalError("output_delivery_budget", "committed current output heads exceed the eight-original bound", 507)
        return sorted(heads.values(), key=lambda publication: publication["cursor"])

    def _component(self, reference, commit, grant, *, historical=False):
        """Resolve an unchanged component from an authenticated original publication."""
        publication_ref = reference["publication_ref"]
        if publication_ref is None:
            if reference == {"component_id": "config:" + self.config_digest, "component_digest": self.config_digest, "publication_ref": None}:
                return copy.deepcopy(self.j.adaptive_feedback_config["config"])
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
                proof and self._eligible(publication, proof["received_at"]) <= commit["input_cut"]["as_of_t"] or any(
                row["source_ref"]["publication_ref"]["cursor"]==publication["cursor"]
                and row["eligible_at"]<=commit["input_cut"]["as_of_t"] for row in commit["checkpoint"]["dependency_receipts"])):
            raise JournalError("invalid_reference", "component requires its eligible original receipt", 409)
        payload = publication["payload"]
        components = (payload["objects"] if payload["schema"] == "home-geometry-4" else
                      payload["facts"] if payload["schema"] == "home-fact-batch-3" else
                      [payload, *payload["requests"]] if payload["schema"] == "home-lighting-policy-decision-3" else
                      [payload] if payload["schema"] == "home-lighting-control-decision-3" else [payload])
        matches = [body for body in components
                   if (body["object_ref"]["oid"] if payload["schema"] == "home-geometry-4" else body["id"]) == reference["component_id"]
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
        self._object_geometry(snapshot, cut, commit, grant, receipts)
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
        if payload["schema"] != "home-geometry-4":
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
        control = next((body for body in commit["outputs"] if body["schema"] == "home-lighting-control-decision-3"), None)
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
            if (body["schema"] != "home-geometry-4" or len(estimates) != 1 or estimates[0] != head["estimate"]
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
        if commit["kind"] != "semantic":
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
        engagement = next((body for body in commit["outputs"] if body["schema"] == "home-engagement-state-4"), None)
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
            if (cursor in outputs_seen or publication["payload_type"] not in {"home-lighting-policy-decision-3","home-lighting-settled-exposure-1"}
                    or publication["producer_id"] != grant.producer_id or publication["producer_epoch"]!=grant.producer_epoch
                    or publication != _normalized(original) or not delivered or not max(publication["committed_at"], delivered[0]) <= row["received_at"] <= self.j.clock()
                    or cursor in prior_outputs and prior_outputs[cursor] != row):
                raise JournalError("invalid_reference", "committed output proof lacks unchanged authenticated original delivery", 409)
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
            installed = old["pipeline"] if old and old["pipeline"] else None
            requests = ((installed["policy"]["requests"] + (installed["exposure_context"]["requests"] if installed["exposure_context"] else []))
                        if checkpoint["boundary_phase"] == "control_pending" and installed
                        else pipeline["policy"]["requests"] if pipeline and pipeline["policy"] else [])
            requests = list({digest(request): request for request in requests}.values())
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
                           if payload["schema"] == "home-geometry-4" else [payload] if payload["schema"] == "home-registered-surface-1" else [])
                if len(matches) != 1 or any(target[key] != matches[0][key] for key in ("patches", "uncertainty")):
                    raise JournalError("invalid_reference", "past exposure geometry differs from its immutable original", 409)
                original = matches[0]
                if target["role"] == "benefit":
                    material = next((row for row in self.materials if row["object_ref"] == target["target"]["object_ref"]), None)
                    midpoint = [sum(patch["centre_m"][i] for patch in original["patches"]) / 2 for i in range(3)]
                    if (payload["schema"] != "home-geometry-4" or original["state"] != "open" or not material
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
        self._installed_frontier(commit, grant, old)
        self._feedback_context_proofs(commit, grant, old)

    def _phase_transition(self, commit, old):
        cp = commit["checkpoint"]; kind = commit["kind"]
        validate_checkpoint_frontier(cp)
        if kind not in {"bootstrap", "semantic", "control"}:
            raise JournalError("invalid_commit", "semantic publication and control installation require separate transactions", 409)
        if old is None:
            if kind != "bootstrap" or cp["boundary_phase"] != "bootstrap":
                raise JournalError("invalid_commit", "worker must begin with its fresh bootstrap", 409)
            return
        validate_checkpoint_frontier(old)
        if cp["runtime_duration_s"] != old["runtime_duration_s"]:
            raise JournalError("reset_required", "worker duration cannot change after bootstrap", 409)
        if old["boundary_phase"] == "bootstrap":
            if kind != "semantic" or cp["boundary_phase"] != "ready" or cp["last_t"] != 0:
                raise JournalError("invalid_commit", "bootstrap requires semantic0 genesis before control", 409)
            return
        if old["last_t"] == old["runtime_duration_s"]:
            raise JournalError("invalid_commit", "completed worker duration permits only its exact retries", 409)
        if kind == "semantic":
            if (old["boundary_phase"] != "ready" or cp["boundary_phase"] != "control_pending"
                    or cp["last_t"] != old["next_semantic_t"] or cp["last_t"] != old["next_control_t"]
                    or cp["last_installed_t"] != old["last_installed_t"]):
                raise JournalError("invalid_commit", "semantic publication cannot skip an installed or pending control", 409)
            before, after = old["pipeline"], cp["pipeline"]
            installed_fields = ("t", "commands", "fixtures", "exposure_context", "reservations", "frozen_heads", "intent_ids")
            if (any(after[key] != before[key] for key in installed_fields)
                    or after["feedback_exposure"]["held_context"] != before["feedback_exposure"]["held_context"]
                    or cp["reservations"] != old["reservations"] or cp["pending_installed"] != old["pending_installed"]):
                raise JournalError("invalid_reference", "semantic publication changed the prior installed state", 409)
            for field,refkey,limit in (("acknowledged_boundaries","original_ref",6),("completed_intervals","control_original_ref",5)):
                previous={canonical(row[refkey]):row for row in before["feedback_exposure"][field]}
                following=after["feedback_exposure"][field]
                if any(canonical(row[refkey]) in previous and row != previous[canonical(row[refkey])] for row in following):
                    raise JournalError("invalid_reference", "semantic history cannot rewrite a prior original or FIRST", 409)
                merged={**previous,**{canonical(row[refkey]):row for row in following}}
                rank=(lambda row:row["control_seq"]) if field=="acknowledged_boundaries" else (lambda row:row["interval"]["t"])
                if following != sorted(merged.values(),key=rank)[-limit:]:
                    raise JournalError("invalid_reference", "semantic history can only retain originals or append its bounded window", 409)
            # History is separately joined to genuine already-admitted originals
            # and unchanged FIRST; appending it does not install another interval.
        elif kind == "control":
            if (cp["boundary_phase"] != "ready" or cp["last_t"] != old["next_control_t"]
                    or old["boundary_phase"] == "control_pending" and cp["last_t"] != old["last_t"]
                    or old["boundary_phase"] == "ready" and cp["last_t"] == old["next_semantic_t"]
                       and cp["last_t"] < cp["runtime_duration_s"]):
                raise JournalError("invalid_commit", "control must follow its due genuine semantic publication", 409)
        else:
            raise JournalError("invalid_commit", "bootstrap cannot replace an existing worker", 409)

    def _prepare_phase_checked_views(self, commit, old, grant, *, invocation):
        """Historical default; only a trusted fixture subclass may opt in."""
        return None

    def _consume_phase_checked_views(self, prepared, commit, old, grant, *, invocation):
        raise JournalError("invalid_phase_views", "uncommissioned private phase views", 409)

    def worker_commit(self, commit, grant, *, request_canonical_json=None):
        request_text=canonical(commit) if request_canonical_json is None else request_canonical_json
        if not isinstance(request_text,str) or request_text!=canonical(commit):
            raise JournalError("noncanonical_commit","E commit requires exact canonical UTF8 wire bytes",400)
        bounded_json(commit,self.j.limits.max_worker_commit_bytes)
        request_digest=hashlib.sha256(request_text.encode("utf-8")).hexdigest()
        first_received=self.j.clock()
        if commit["schema"] != self.commit_schema:
            raise JournalError("invalid_commit", self.commit_schema + " required")
        grant.require("worker_commit", commit["site_id"], commit["room_id"])
        bounded_json(commit, self.j.limits.max_worker_commit_bytes)
        checkpoint, cut = commit["checkpoint"], commit["input_cut"]
        if any(not _same_scope(commit, body) for body in (checkpoint, cut)) or any(body["runtime_epoch"] != commit["runtime_epoch"] or body["journal_epoch"] != commit["journal_epoch"] for body in (checkpoint, cut)):
            raise JournalError("scope_mismatch", "cut/checkpoint runtime differs from commit")
        def apply():
            marker = "worker-commit/" + commit["id"]
            retry = self.j.db.execute("SELECT digest,receipt FROM receipts WHERE producer_id=? AND source_epoch=? AND event_id=?", (grant.producer_id, grant.producer_epoch, marker)).fetchone()
            if retry:
                if retry["digest"] != request_digest:
                    raise JournalError("identity_conflict", "commit retry changed its original body", 409)
                return json.loads(retry["receipt"])
            self.validate_payload(commit)
            self._grant_current(grant)
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
            invocation = object()
            prepared_views = self._prepare_phase_checked_views(commit, old, grant, invocation=invocation)
            if prepared_views is None:
                self._phase_transition_wire(commit, old, grant)
            delta = {"bootstrap": (0, 0), "semantic": (1, 0), "control": (0, 1)}[commit["kind"]]
            if (checkpoint["commit_seq"] != seq + 1 or checkpoint["semantic_seq"] != semantic + delta[0]
                    or checkpoint["control_seq"] != control + delta[1] or checkpoint["input_cursor"] != cut["input_cursor"]
                    or checkpoint["last_t"] != cut["as_of_t"]):
                raise JournalError("invalid_commit", "checkpoint counters differ from commit kind")
            if old and (not _same_scope(old, commit) or checkpoint["runtime_epoch"] != old["runtime_epoch"] or checkpoint["last_t"] < old["last_t"]):
                raise JournalError("checkpoint_conflict", "worker scope/runtime/clock changed", 409)
            if old and checkpoint["static_bindings_digest"]!=old["static_bindings_digest"]:
                raise JournalError("reset_required","worker static authorization/publisher bindings changed",409)
            if commit["kind"] == "bootstrap" and (row or cut["as_of_t"] != 0):
                raise JournalError("invalid_commit", "bootstrap requires a fresh zero-clock worker")
            views = (self._consume_phase_checked_views(prepared_views, commit, old, grant, invocation=invocation)
                     if prepared_views is not None else _PHASE_VIEWS_FALLBACK)
            if views is _PHASE_VIEWS_FALLBACK:
                proof_checkpoint=self._hydrate_checkpoint_outputs(checkpoint,grant,cut)
                proof_old=self._hydrate_checkpoint_outputs(old,grant) if old else None
            else:
                proof_checkpoint, proof_old = views
            proof_commit={**commit,"checkpoint":proof_checkpoint}
            claimed = (self._validate_cut(proof_commit, grant, previous)
                       if views is _PHASE_VIEWS_FALLBACK else
                       self._validate_cut(proof_commit, grant, previous, _authoritative_prior=proof_old))
            counts = {name: sum(body["schema"] == name for body in commit["outputs"]) for name in ADAPTIVE_OUTPUTS}
            if (counts["home-engagement-state-4"] != delta[0] or counts["home-lighting-policy-decision-3"] != delta[0]
                    or counts["home-lighting-control-decision-3"] != delta[1] or counts["home-experience-goal-1"] and not delta[0]
                    or counts["home-lighting-settled-exposure-1"] != delta[0]
                    or counts["home-lighting-installed-boundary-1"] != int(bool(delta[0] and checkpoint["semantic_seq"] == 0))
                    or counts["home-experience-feedback-application-1"] and not delta[0]):
                raise JournalError("invalid_commit", "adaptive output set differs from commit kind")
            outcomes = self._intent_ledger(proof_commit, proof_old)
            self._intent_application_outputs(proof_commit,proof_old,outcomes)
            self._frozen_head_proofs(proof_commit, grant, proof_old)
            self._intent_authority_bindings(proof_commit, proof_old, outcomes)
            post_authority = self._post_authority(proof_commit, outcomes)
            if checkpoint["authority"] != post_authority:
                raise JournalError("authority_conflict", "post-semantic map contains an unauthorized authority delta", 409)
            self._checkpoint_references(proof_commit, grant, proof_old)
            if commit["kind"] == "control":
                self._current_policy_original(proof_commit, grant)
            self._feedback_terminals(proof_commit,grant,proof_old)
            self._exposure_outputs(proof_commit,grant,proof_old)
            output_receipts = []
            for output in commit["outputs"]:
                if output["schema"] not in {"home-experience-goal-1","home-experience-feedback-application-1","home-intent-application-result-3"} and (output["t"] != cut["as_of_t"] or output["schema"]!="home-lighting-installed-boundary-1" and output["semantic_seq"] != checkpoint["semantic_seq"]):
                    raise JournalError("invalid_commit", "output cutoff/semantic sequence differs")
                grant.require("worker_commit", commit["site_id"], commit["room_id"], payload_type=output["schema"], revision=str(self.j.schemas.payload_versions[output["schema"]]) + ".0")
                if output["schema"] == "home-experience-goal-1":
                    self._authorization(output, grant, "derive")
                    if output["issued_at"] != cut["as_of_t"] or output["effective_at"] > cut["as_of_t"] or output["available_at"] > cut["as_of_t"]:
                        raise JournalError("invalid_commit", "derived goal must be issued at its semantic cutoff")
                elif output["schema"] == "home-lighting-control-decision-3":
                    bounded_json(output, 32 * 1024)
                    if output["control_seq"] != checkpoint["control_seq"] or output["input_cut_digest"] != cut["digest"]:
                        raise JournalError("invalid_commit", "control differs from cut/sequence")
                    prior_row = self.j.db.execute("SELECT body FROM publications WHERE site_id=? AND room_id=? AND payload_type='home-lighting-control-decision-3' ORDER BY cursor DESC LIMIT 1", (commit["site_id"], commit["room_id"])).fetchone()
                    prior_t = json.loads(prior_row[0])["payload"]["t"] if prior_row else 0
                    execution = output["execution"]
                    if execution["fixture_ids"] != [fixture["id"] for fixture in self.j.adaptive_feedback_config["config"]["registry"]["fixtures"]]:
                        raise JournalError("invalid_reference", "execution fixture dictionary differs from configured registry order", 409)
                    if (abs(execution["from_t"] - prior_t) > 1e-9 or abs(output["t"] - prior_t - .2) > 1e-9
                            or len(execution["samples"]) != 10 or any(abs(sample["t"] - prior_t - (i + 1) * .02) > 1e-9 for i, sample in enumerate(execution["samples"]))):
                        raise JournalError("invalid_commit", "control requires ten immutable past20ms samples on the5Hz grid", 409)
                    self._geometry(output["geometry_snapshot"], cut, proof_commit, grant, claimed)
                    for reference in output["consumed_refs"]:
                        self._component(reference, proof_commit, grant, historical=True)
                    for target in output["allocation"]["targets"]:
                        self._target(target, output, proof_commit, grant)
                elif output["schema"] == "home-lighting-policy-decision-3":
                    for reference in output["consumed_refs"]:
                        self._component(reference, proof_commit, grant, historical=True)
                event = self._event_for(output, grant); self.j._event(event)
                if self._event_retry(event,grant):
                    raise JournalError("output_identity_reused", "new commit reused an output publication identity", 409)
                output_receipts.append(self.append(event, context_seq=seq + 1, grant=grant))
                if output["schema"] == "home-lighting-policy-decision-3":
                    for outcome in output["intent_results"]:
                        rows = self.j.db.execute("SELECT producer_id,source_epoch,event_id,event,application_result FROM receipts WHERE event_id=?", (outcome["intent_id"],)).fetchall()
                        matches = [item for item in rows if json.loads(item["event"]).get("payload_type") == "home-intent-event-3" and _same_scope(json.loads(item["event"]), commit)]
                        if len(matches) != 1:
                            raise JournalError("invalid_intent_result", "outcome requires one scoped durable instruction", 409)
                        saved = matches[0]
                        application = self._intent_application_body(commit,outcome)
                        self.validate_payload(application)
                        if saved["application_result"] and json.loads(saved["application_result"]) != application:
                            raise JournalError("intent_result_conflict", "first durable application result cannot change", 409)
                        self.j.db.execute("UPDATE receipts SET application_result=? WHERE producer_id=? AND source_epoch=? AND event_id=?", (canonical(application), saved["producer_id"], saved["source_epoch"], saved["event_id"]))
            for follow in post_authority["follows"]:
                self.j._set_meta("adaptive-follow:" + self._follow_key(follow), canonical({key: follow[key] for key in ("follow_generation", "follow_allowed")}))
            self.j.db.execute("INSERT INTO workers VALUES(?,?,?,?,?,?) ON CONFLICT(worker_id,site_id,room_id) DO UPDATE SET input_cursor=excluded.input_cursor,context_seq=excluded.context_seq,checkpoint=excluded.checkpoint", (grant.expected_worker_id, commit["site_id"], commit["room_id"], checkpoint["input_cursor"], seq + 1, canonical(checkpoint)))
            result = {"schema": "home-context-worker-commit-receipt-5", "version":5,"producer_epoch":grant.producer_epoch,"raw_request_digest":request_digest,"first_received_at":first_received, "id": commit["id"], "worker_id": grant.expected_worker_id,
                      "journal_epoch": self.j.stream_epoch, "input_cursor": checkpoint["input_cursor"], "commit_seq": seq + 1,
                      "semantic_seq": checkpoint["semantic_seq"], "control_seq": checkpoint["control_seq"], "cursor": int(self.j._meta("cursor")), "receipts": output_receipts}
            self.j.db.execute("INSERT INTO receipts(producer_id,source_epoch,event_id,digest,receipt,event) VALUES(?,?,?,?,?,?)", (grant.producer_id, grant.producer_epoch, marker, request_digest, canonical(result), canonical({"schema": "home-context-worker-commit-tombstone-5", **self.identity})))
            self.j.db.execute("INSERT INTO feedback_worker_commit_originals VALUES(?,?,?,?,?)",(grant.producer_id,grant.producer_epoch,commit["id"],request_digest,zlib.compress(request_text.encode("utf-8"),9)))
            self.validate_payload(result)
            self._storage_budget()
            return result
        return self.j._transaction(apply)

    def lookup_commit(self, commit_id, grant, site, room):
        grant.require("read", site, room); grant.require("worker_commit", site, room); self._scope_check(site, room)
        self._consumer(grant,grant.expected_worker_id)
        def read():
            row = self.j.db.execute("SELECT receipt FROM receipts WHERE producer_id=? AND source_epoch=? AND event_id=?", (grant.producer_id, grant.producer_epoch, "worker-commit/" + commit_id)).fetchone()
            if not row:
                raise JournalError("not_found", "commit is outside the configured worker journal", 404)
            receipt = json.loads(row[0]); publications = []
            for output in receipt["receipts"]:
                original = self.j.db.execute("SELECT body FROM publications WHERE cursor=?", (output["cursor"],)).fetchone()
                publication = json.loads(original[0])
                if publication["payload_type"] not in grant.payload_types or not self._read_allowed(publication, grant):
                    raise JournalError("forbidden", "commit original is outside worker read rights", 403)
                publications.append(_normalized(publication))
            # Returning complete authenticated originals is a real delivery;
            # the earlier acknowledgement alone is not a consumer receipt.
            self._delivered_publications(grant,grant.expected_consumer_id,publications)
            result={"schema": "home-context-feedback-commit-response-1", "version":1,"receipt": receipt, "publications": publications}
            self.validate_payload(result);self._storage_budget()
            return result
        return self.j._transaction(read)

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
        self._consumer(grant, consumer_id)
        return self.j._transaction(lambda: self._snapshot_read(grant, site, room, consumer_id))

    def _snapshot_read(self, grant, site, room, consumer_id):
        """Build/deliver one snapshot inside the caller's existing transaction."""
        now = assert_relative_time(self.j.clock(), self.clock_profile)
        visible = self._visible(grant, site, room, 0, int(self.j._meta("cursor")), inputs_only=True)
        pending = {}
        for row in visible:
            publication = json.loads(row["body"])
            if (not self.j._revoked(site, room, row["source_id"], row["revision"])
                    and self._eligible(publication) > now):
                if publication["payload_type"] in {"home-preference-snapshot-2","home-experience-context-declaration-1","home-experience-actor-binding-1"}:
                    # Future declarations are unchanged reference-only originals.
                    # Retain them through the cursor fence without promoting a head.
                    pending[publication["cursor"]]=_normalized(publication)
                else:
                    raise JournalError("snapshot_pending_inputs", "snapshot has pending originals; retry after eligibility or use cut/checkpoint recovery", 409)
        self._expire_requests(site, room); self._refresh_heads(site, room)
        tail = int(self.j._meta("cursor"))
        items, components, revision_heads, family_heads, masks = pending, [], [], [], {}
        for record in self._qualified_components(site, room, now):
            if (record["payload_type"] not in grant.payload_types or record["revision"] not in grant.revisions
                    or self.j._revoked(site, room, record["source_id"], record["revision"])):
                continue
            publication = record["publication"]
            items[publication["cursor"]] = _normalized(publication)
            components.append(record["head"])
            masks.setdefault(publication["cursor"], set()).add(component_id(record["component"], record["payload_type"] == "home-geometry-4"))
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
            if row["payload_type"] in REVISION_FIELDS or row["payload_type"] in ADAPTIVE_FAMILIES or row["payload_type"] in {"home-fact-batch-3", "home-geometry-4"}:
                continue
            if row["payload_type"] not in self.j.schemas.publication_families or self._eligible(publication) > now:
                continue
            family=row["payload_type"]
            scope=[payload[k] for k in SCOPE_KEYS]
            publisher=[publication["producer_id"],publication["producer_epoch"]]
            if family=="home-experience-actor-binding-1":
                key=canonical([family,*scope,*publisher,payload["actor_ref"],payload["person_ref"],payload["feedback_origin"],payload["id"]])
                rank=(*original_family_clocks(payload,publication["committed_at"]),publication["payload_digest"])
            elif family in {"home-preference-write-authority-1","home-preference-write-frontier-1"}:
                key=canonical([family,*scope,payload["owner_id"],payload["subject"],payload["preference_set_id"]])
                rank=(payload.get("sampled_at",payload["effective_at"]),payload["available_at"],publication["committed_at"],publication["cursor"])
            elif family in {"home-intent-event-3","home-evidence-request-3","home-evidence-reply-3","home-acquisition-status-2",
                            "home-experience-classifier-disposition-1","home-experience-feedback-1","home-experience-feedback-application-1",
                            "home-intent-application-result-3","home-preference-write-1","home-preference-write-application-1"}:
                key=canonical([family,*scope,*publisher,payload["id"]])
                rank=(publication["cursor"],)
            else:
                key=canonical([family,*scope,*publisher])
                rank=(publication["cursor"],)
            if key not in other_heads or rank>other_heads[key][0]:
                other_heads[key]=(rank,publication)
        for _,publication in other_heads.values():
            items[publication["cursor"]] = _normalized(publication)
        self._snapshot_dependencies(items, grant, tail, now)
        result = {"schema": "home-context-snapshot-5", "version": 5, **self.identity,
                  "clock_profile_digest": self.clock_profile["digest"], "stream_epoch": self.j.stream_epoch,
                  "cursor": tail, "as_of_t": now, "authority": self.authority(site, room),
                  "items": [items[key] for key in sorted(items)],
                  "component_heads": sorted(components, key=lambda h: (h["publication_ref"]["cursor"], canonical(h))),
                  "revision_heads": revision_heads, "family_heads": family_heads,
                  "write_heads":self._snapshot_write_heads(grant,site,room,now,items)}
        refs=self._preference_snapshot_refs(now,items,grant)
        result.update(refs)
        result["items"]=[items[k] for k in sorted(items)]
        self.validate_payload(result); bounded_json(result, self.j.limits.max_response_bytes)
        consumer = self._consumer(grant, consumer_id); identifier(consumer, "consumer_id")
        for cursor, ids in masks.items():
            self.j.db.execute("INSERT OR IGNORE INTO dynamic_snapshot_masks VALUES(?,?,?,?,?)",
                              (grant.principal_id, consumer, cursor, canonical(sorted(ids)), now))
        if self.j.db.execute("SELECT count(*) FROM dynamic_snapshot_masks").fetchone()[0] > 2048:
            raise JournalError("snapshot_budget", "snapshot selector proof budget exhausted", 507)
        self._storage_budget()
        self._delivered_publications(grant, consumer, list(items.values()))
        self.j._delivered(grant, consumer, site, room, tail)
        self._storage_budget()
        return result

    def _cached_estimate(self, request):
        self._refresh_heads(request["site_id"], request["room_id"])
        authority = self.authority(request["site_id"], request["room_id"])
        if not authority["room"]["motion_allowed"]:
            return None
        rows = self._qualified_components(request["site_id"], request["room_id"], self.j.clock())
        # Select the current lineage before compatibility: a changed epoch,
        # person, association, closed state or expiry must fence older support.
        candidates = [(row, row["head"]) for row in rows if row["payload_type"] == "home-geometry-4"]
        candidates = [(row, head) for row, head in candidates if _same_scope(head["object_ref"], request)
                      and head["object_ref"]["oid"] == request["object_ref"]["oid"]]
        if not candidates:
            return None
        row, head = max(candidates, key=lambda r: (r[1]["observed_at"], GEOMETRY_PRIORITY[r[0]["component"]["state"]], r[0]["publication"]["payload_digest"]))
        if self.j._revoked(row["site_id"], row["room_id"], row["source_id"], row["revision"]) or not self._producer_allowed(row["publication"]["producer_id"],row["publication"]["producer_epoch"]):
            return None
        p = row["publication"]; g = p["payload"]; obj = row["component"]
        correction = None
        for original in self.j.db.execute("SELECT body FROM publications WHERE site_id=? AND room_id=? AND payload_type='home-intent-event-3'", (request["site_id"], request["room_id"])).fetchall():
            declared = json.loads(original[0]); i = declared["payload"]
            if (i["kind"] == "association_correction" and _same_scope(i, request) and i["object_ref"]["oid"] == request["object_ref"]["oid"]
                    and self._eligible(declared) <= self.j.clock()):
                application = self.j.db.execute("SELECT application_result FROM receipts WHERE event_id=? AND producer_id=? AND source_epoch=?",
                                                (i["id"], declared["producer_id"], declared["producer_epoch"])).fetchone()
                if not application or not application[0] or json.loads(application[0])["status"] != "applied":
                    continue
                correction = max(correction if correction is not None else -1, i["available_at"])
        registration_ref = self._registration_for_geometry(g)
        registration = next((r for r in authority["registrations"] if r["registration_ref"] == registration_ref), None)
        source = next((r for r in authority["sources"] if r["source_id"] == row["source_id"]), None)
        if (not source or source["revoked"] or not registration or registration["revoked"] or g["registration_generation"] != registration["registration_generation"] or head["object_ref"] != request["object_ref"]
                or head["person_ref"] != request["person_ref"] or obj["state"] != "open"
                or correction is not None and g["observed_at"] <= correction
                or not 0 <= self.j.clock() - g["observed_at"] <= .5
                or self.j.clock() >= min(g["valid_until"], g["observed_at"] + 1)):
            return None
        facts = []
        for fact_row in (r for r in rows if r["payload_type"] == "home-fact-batch-3"):
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



    def _roots(self, event):
        payload = event["payload"]
        records = payload["facts"] if payload["schema"] == "home-fact-batch-3" else [payload]
        scope = canonical([event[k] for k in SCOPE])
        for record in records:
            cap = record["capture"]
            root = canonical([cap["source_id"], cap["capture_epoch"], cap["capture_seq"]])
            ancestry = digest([cap["source_id"], cap["capture_epoch"]])
            row = self.j.db.execute("SELECT sensor_digest,observed_at FROM original_roots WHERE scope=? AND observation_root=?", (scope, root)).fetchone()
            if row and (row[0] != ancestry or row[1] != cap["observed_at"]):
                raise JournalError("root_time_conflict", "original capture identity cannot change its observation time", 409)
            if not row:
                total = self.j.db.execute("SELECT count(*) FROM original_roots WHERE scope=? AND sensor_digest=?", (scope, ancestry)).fetchone()[0]
                if total >= 600:
                    raise JournalError("root_budget", "synthetic original capture budget exhausted", 507)
                self.j.db.execute("INSERT INTO original_roots VALUES(?,?,?,?)", (scope, root, ancestry, cap["observed_at"]))
    def _qualified_components(self, site, room, t, *, eligible=None, masks=None):
        """Derive bounded causal heads from original publications, including fences.

        Publication arrival is not observation order. Future availability cannot
        retire an eligible predecessor, and expiry cannot resurrect older data.
        """
        records = []
        for row in self.j.db.execute("SELECT * FROM publications WHERE site_id=? AND room_id=? AND payload_type IN ('home-geometry-4','home-fact-batch-3')", (site, room)).fetchall():
            if eligible is not None and row["cursor"] not in eligible:
                continue
            publication = json.loads(row["body"]); body = publication["payload"]
            geometric = body["schema"] == "home-geometry-4"
            for item in body["objects"] if geometric else body["facts"]:
                clocks = body if geometric else item
                if max(publication["committed_at"], body["available_at"], clocks["observed_at"], clocks["available_at"]) > t:
                    continue
                mask = (masks or {}).get(publication["cursor"])
                if mask is not None and component_id(item, geometric) not in mask:
                    continue
                cap = clocks["capture"]
                lineage = canonical([cap["source_id"], publication["producer_id"], publication["producer_epoch"], item["object_ref"], body["schema"]])
                head = {"payload_type": body["schema"], "person_ref": item["person_ref"], "object_ref": item["object_ref"],
                        "association_generation": item["association_generation"], "kind": "geometry" if geometric else item["kind"],
                        "capture": cap, **{k: clocks[k] for k in ("observed_at", "available_at", "valid_until")},
                        "publication_ref": {"journal_epoch": publication["journal_epoch"], "cursor": publication["cursor"],
                                            "payload_digest": publication["payload_digest"], "component_id": item["object_ref"]["oid"] if geometric else item["id"]}}
                records.append({"lineage": lineage, "head": head, "publication": publication,
                                "component": item, "source_id": cap["source_id"], "revision": row["revision"],
                                "payload_type": body["schema"], "site_id": site, "room_id": room})
        # Same-observed negative geometry wins conservatively; stable digest
        # breaks remaining ties without interpreting receipt order as truth.
        records.sort(key=lambda r: (r["head"]["observed_at"], GEOMETRY_PRIORITY[r["component"]["state"]] if r["head"]["kind"] == "geometry" else 0, r["publication"]["payload_digest"]))
        families = {}
        for record in records:
            lineage, head = record["lineage"], record["head"]
            key = (lineage, head["payload_type"])
            previous = families.get(key, [])
            if previous and previous[0]["head"]["observed_at"] < head["observed_at"]:
                previous = []
            if head["kind"] == "geometry":
                families[key] = [record]
            else:
                # Every interpretation of the newest original observation is
                # retained, even when one child's own expiry has elapsed.
                families[key] = previous + [record]
        return [r for family in families.values() for r in family]
    def _delivered_publications(self, grant, consumer, publications):
        # This is server delivery time, never a fabricated consumer receipt.
        for publication in publications:
            self.j.db.execute("INSERT OR IGNORE INTO dynamic_deliveries VALUES(?,?,?,?)",
                              (grant.principal_id, consumer, publication["cursor"], self.j.clock()))
    def _base_ref(self, ref, grant, scope, through, *, allow_revoked=False):
        if ref["journal_epoch"] != self.j._meta("epoch") or ref["cursor"] > through:
            raise JournalError("invalid_reference", "reference is outside the fixed journal cut", 409)
        row = self.j.db.execute("SELECT * FROM publications WHERE cursor=?", (ref["cursor"],)).fetchone()
        if not row or row["site_id"] != scope["site_id"] or row["room_id"] != scope["room_id"]:
            raise JournalError("invalid_reference", "reference is not durable in the granted room", 409)
        p = json.loads(row["body"])
        if p["payload_type"] not in grant.payload_types or row["revision"] not in grant.revisions or p["payload_digest"] != ref["payload_digest"] or not same_scope(p["payload"], scope):
            raise JournalError("invalid_reference", "reference binding or digest mismatch", 409)
        if not allow_revoked and self.j._revoked(row["site_id"], row["room_id"], row["source_id"], row["revision"]):
            raise JournalError("revoked", "referenced source is revoked", 409)
        component = ref.get("component_id")
        if component and p["payload_type"] == "home-geometry-4" and component not in {o["object_ref"]["oid"] for o in p["payload"]["objects"]}:
            raise JournalError("invalid_reference", "geometry component does not exist", 409)
        return p
    def _object_geometry(self, snapshot, cut, commit, grant, receipts):
        self.validate_payload(snapshot)
        if (snapshot["digest"] != cut["geometry_snapshot_digest"] or snapshot["as_of_t"] != cut["as_of_t"]
                or snapshot["input_cursor"] != cut["input_cursor"] or snapshot["authority"] != cut["authority"]):
            raise JournalError("invalid_reference", "control geometry snapshot differs from its atomic cut", 409)
        eligible = {r["cursor"] for r in cut["eligible_refs"]}
        cp = commit["checkpoint"]
        masks = {r["publication"]["cursor"]: r["component_ids"] for k in ("pending_inputs", "geometry_inputs", "retained_inputs") for r in cp[k] if "component_ids" in r}
        current = {}
        for candidate in self._qualified_components(commit["site_id"], commit["room_id"], cut["as_of_t"], eligible=eligible, masks=masks):
            if candidate["payload_type"] != "home-geometry-4":
                continue
            key = object_key(candidate["head"]["object_ref"])
            rank = lambda r: (r["head"]["observed_at"], GEOMETRY_PRIORITY[r["component"]["state"]], r["publication"]["payload_digest"])
            if key not in current or rank(candidate) > rank(current[key]):
                current[key] = candidate
        for head in snapshot["object_heads"]:
            if head["estimate_ref"] is None:
                if head["status"] in {"fresh", "held"}:
                    raise JournalError("invalid_reference", "usable head requires an immutable estimate", 409)
                continue
            p = self._ref(head["estimate_ref"], grant, commit, cut["input_cursor"], allow_revoked=head["status"] not in {"fresh", "held"})
            payload = p["payload"]
            if payload["schema"] != "home-geometry-4" or p["cursor"] not in eligible or p["cursor"] not in receipts:
                raise JournalError("invalid_reference", "geometry must have an eligible original receipt", 409)
            selected = current.get(object_key(head["object_ref"]))
            if not selected or head["estimate_ref"] != selected["head"]["publication_ref"]:
                raise JournalError("stale_geometry", "snapshot did not select its current eligible component lineage", 409)
            matches = [o for o in payload["objects"] if o["object_ref"] == head["object_ref"]]
            if len(matches) != 1 or head["estimate"] != matches[0] or head["capture"] != payload["capture"] or snapshot["registry_digest"] != payload["registry_digest"]:
                raise JournalError("invalid_reference", "geometry body is not the immutable selected estimate", 409)
            for field in ("observed_at", "available_at", "valid_until"):
                if head[field] != payload[field]:
                    raise JournalError("invalid_reference", "geometry timing was changed", 409)
            if head["status"] in {"fresh", "held"}:
                age = cut["as_of_t"] - payload["observed_at"]
                if not 0 <= age <= 0.5 + 1e-9 or cut["as_of_t"] >= min(payload["valid_until"], payload["observed_at"] + 1):
                    raise JournalError("stale_geometry", "usable estimate exceeds freshness or TTL", 409)
    def _expire_requests(self, site, room):
        for row in self.j.db.execute("SELECT body FROM requests WHERE site_id=? AND room_id=? AND terminal IS NULL AND deadline<=?", (site, room, self.j.clock())).fetchall():
            self._terminal(json.loads(row[0]), "expired", "deadline_elapsed")
    def lookup_request(self, request_id, grant, site, room):
        grant.require("read", site, room, payload_type="home-evidence-request-3", revision="3.0")
        def read():
            self._expire_requests(site, room)
            row = self.j.db.execute("SELECT * FROM requests WHERE request_id=? AND site_id=? AND room_id=?", (request_id, site, room)).fetchone()
            if not row:
                raise JournalError("not_found", "request not found", 404)
            terminal = json.loads(row["terminal"]) if row["terminal"] else None
            if terminal:
                grant.require("read", site, room, payload_type="home-evidence-reply-3", revision="3.0")
            return {"request": json.loads(row["body"]), "receipt": terminal["receipt"] if terminal else json.loads(row["receipt"]), "terminal_reply": terminal["payload"] if terminal else None}
        return self.j._transaction(read)

# Interface-neutral durable origin primitives.
"""Private Stage E preparation; no profile, wire or route admission is installed.

These helpers preserve an authenticated origin and its first result under an
explicit caller-owned SQLite transaction.  The generated E schema validator and
the actual service grant checks must precede public admission.  This module does
not infer a producer epoch from a journal epoch or authenticate caller JSON.
"""

import hashlib
import json
import math
import sqlite3
import zlib

from journal import JournalError, canonical, identifier


MAXIMUM_ORIGINS = 2048
MAXIMUM_REQUEST_BYTES = 256 * 1024
MAXIMUM_RECEIPT_BYTES = 1024 * 1024
AUTHENTICATION_KEYS = frozenset({"principal_id", "producer_id", "producer_epoch"})
ORIGIN_KEYS = frozenset({"producer_id", "producer_epoch", "event_id"})


def _error(code, *, status=400):
    raise JournalError(code, code, status)


def snapshot_json(value, *, maximum_bytes=MAXIMUM_REQUEST_BYTES):
    """Copy only inert built-in JSON, bounded before canonical hashing."""
    if type(maximum_bytes) is not int or not 1 <= maximum_bytes <= MAXIMUM_RECEIPT_BYTES:
        _error("feedback_json_configuration")
    active = set()
    nodes = 0

    def visit(item, depth):
        nonlocal nodes
        nodes += 1
        if nodes > 20000 or depth > 16:
            _error("feedback_json_bound")
        kind = type(item)
        if item is None or kind is bool:
            return item
        if kind is int:
            if abs(item) > 9007199254740991:
                _error("feedback_json_number")
            return item
        if kind is float:
            if not math.isfinite(item):
                _error("feedback_json_number")
            return item
        if kind is str:
            try:
                item.encode("utf-8")
            except UnicodeError:
                _error("feedback_json_unicode")
            return item
        if kind not in (list, dict) or id(item) in active:
            _error("feedback_json_type")
        active.add(id(item))
        try:
            if kind is list:
                return [visit(child, depth + 1) for child in item]
            if any(type(key) is not str for key in item):
                _error("feedback_json_key")
            return {visit(key, depth + 1): visit(child, depth + 1) for key, child in item.items()}
        finally:
            active.remove(id(item))

    result = visit(value, 0)
    if len(canonical(result).encode("utf-8")) > maximum_bytes:
        _error("feedback_json_bound")
    return result


def complete_request_digest(request):
    """Digest the complete canonical raw value, never a normalized projection."""
    value = snapshot_json(request)
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _authentication(authentication):
    if type(authentication) is not dict or set(authentication) != AUTHENTICATION_KEYS:
        _error("feedback_authenticated_origin")
    return {key: identifier(authentication[key], key) for key in sorted(AUTHENTICATION_KEYS)}


def authenticated_origin(authentication, event_id):
    """Use only the service's trusted grant projection, not a payload principal."""
    auth = _authentication(authentication)
    return {"producer_id": auth["producer_id"], "producer_epoch": auth["producer_epoch"],
            "event_id": identifier(event_id, "event_id")}


def validate_producer_epochs(records):
    """Retain exact historical principal/publisher/epoch tuples without aliases."""
    if type(records) is not list or not 1 <= len(records) <= 8:
        _error("feedback_producer_configuration")
    result, pairs = [], set()
    for record in records:
        auth = _authentication(record)
        pair = (auth["producer_id"], auth["producer_epoch"])
        if pair in pairs:
            _error("feedback_producer_configuration")
        pairs.add(pair)
        result.append(auth)
    return sorted(result, key=lambda row: (row["producer_id"], row["producer_epoch"], row["principal_id"]))


class PrivateOriginLedger:
    """A disconnected durable first-receipt primitive, not an E wire validator.

    Public code must supply its normative receipt validator and enforce profile,
    grant, schema, quotas and database limits within the same transaction.  This
    helper deliberately performs no commit, mutable authority check or clock read.
    """

    def __init__(self, connection, *, maximum_origins=MAXIMUM_ORIGINS):
        if not isinstance(connection, sqlite3.Connection) or type(maximum_origins) is not int or not 1 <= maximum_origins <= MAXIMUM_ORIGINS:
            _error("feedback_origin_configuration")
        self.connection = connection
        self.maximum_origins = maximum_origins
        connection.execute("""CREATE TABLE IF NOT EXISTS adaptive_feedback_origins (
            producer_id TEXT NOT NULL, producer_epoch TEXT NOT NULL,
            event_id TEXT NOT NULL, principal_id TEXT NOT NULL,
            request_digest TEXT NOT NULL, request_json TEXT NOT NULL,
            first_receipt_json TEXT NOT NULL, first_receipt_digest TEXT NOT NULL,
            PRIMARY KEY(producer_id, producer_epoch, event_id))""")

    def _encode_request_storage(self, canonical_text):
        """Default physical representation remains the original plain text."""
        return canonical_text

    def _decode_request_storage(self, stored_value, expected_request_digest):
        """Default readers retain their existing plain-text parsing behavior."""
        return stored_value

    def lookup(self, authentication, event_id):
        auth = _authentication(authentication)
        origin = authenticated_origin(auth, event_id)
        row = self.connection.execute(
            "SELECT principal_id,request_digest,request_json,first_receipt_json,first_receipt_digest "
            "FROM adaptive_feedback_origins WHERE producer_id=? AND producer_epoch=? AND event_id=?",
            tuple(origin[key] for key in ("producer_id", "producer_epoch", "event_id"))).fetchone()
        if row is None:
            return None
        if row[0] != auth["principal_id"]:
            _error("feedback_origin_owner", status=403)
        try:
            request = snapshot_json(json.loads(self._decode_request_storage(row[2], row[1])))
            receipt = snapshot_json(json.loads(row[3]), maximum_bytes=MAXIMUM_RECEIPT_BYTES)
        except (TypeError, ValueError, JournalError):
            _error("feedback_origin_corrupt", status=409)
        receipt_digest = hashlib.sha256(canonical(receipt).encode("utf-8")).hexdigest()
        if complete_request_digest(request) != row[1] or receipt_digest != row[4]:
            _error("feedback_origin_corrupt", status=409)
        return {"origin": origin, "request_digest": row[1], "request": request,
                "first_receipt": receipt, "first_receipt_digest": receipt_digest}

    def retry(self, authentication, event_id, request):
        """Resolve before mutable CAS/revocation/expiry; None is unresolved."""
        raw = snapshot_json(request)
        prior = self.lookup(authentication, event_id)
        if prior is not None and prior["request_digest"] != complete_request_digest(raw):
            _error("feedback_origin_conflict", status=409)
        return prior

    def remember(self, authentication, event_id, request, first_receipt, *, validate_receipt):
        raw = snapshot_json(request)
        prior = self.retry(authentication, event_id, raw)
        if prior is not None:
            return prior
        if not self.connection.in_transaction:
            _error("feedback_origin_transaction_required")
        if not callable(validate_receipt):
            _error("feedback_receipt_validator_required")
        receipt = snapshot_json(first_receipt, maximum_bytes=MAXIMUM_RECEIPT_BYTES)
        validator_input = snapshot_json(receipt, maximum_bytes=MAXIMUM_RECEIPT_BYTES)
        if validate_receipt(validator_input) is not True or canonical(validator_input) != canonical(receipt):
            _error("feedback_receipt_validation")
        count = self.connection.execute("SELECT COUNT(*) FROM adaptive_feedback_origins").fetchone()[0]
        if count >= self.maximum_origins:
            _error("feedback_origin_budget", status=409)
        auth = _authentication(authentication)
        origin = authenticated_origin(auth, event_id)
        request_digest = complete_request_digest(raw)
        receipt_digest = hashlib.sha256(canonical(receipt).encode("utf-8")).hexdigest()
        self.connection.execute(
            "INSERT INTO adaptive_feedback_origins VALUES(?,?,?,?,?,?,?,?)",
            (origin["producer_id"], origin["producer_epoch"], origin["event_id"], auth["principal_id"],
             request_digest, self._encode_request_storage(canonical(raw)), canonical(receipt), receipt_digest))
        return {"origin": origin, "request_digest": request_digest, "request": raw,
                "first_receipt": receipt, "first_receipt_digest": receipt_digest}


def validate_checkpoint_frontier(checkpoint):
    """Closed CP7 schedule; semantic publication never installs mechanics."""
    cp = checkpoint
    duration = cp["runtime_duration_s"]
    if type(duration) is not int or not 1 <= duration <= 120:
        raise JournalError("invalid_commit", "worker duration must be a bounded integer", 409)
    if cp["semantic_seq"] == -1:
        if (cp["boundary_phase"] != "bootstrap" or cp["commit_seq"] != 1 or cp["control_seq"] != 0
                or cp["last_t"] != 0 or cp["last_installed_t"] is not None or cp["next_semantic_t"] != 0
                or cp["next_control_t"] != .2 or cp["reducer"]["last_t"] is not None or cp["pipeline"] is not None
                or cp["pending_installed"] is not None or cp["acknowledged_installed"] is not None
                or cp["goal_heads"] or cp["goal_revision_count"] or cp["intents"] or cp["feedback_terminals"]
                or cp["intent_authority_bindings"] or cp["reservations"]):
            raise JournalError("invalid_commit", "bootstrap frontier is not a fresh zero-clock state", 409)
        return
    pipeline = cp["pipeline"]
    if (cp["boundary_phase"] == "bootstrap" or cp["commit_seq"] != cp["control_seq"] + cp["semantic_seq"] + 2
            or not 0 <= cp["last_t"] <= duration or cp["reducer"]["last_t"] != cp["semantic_seq"]
            or not pipeline or pipeline["t"] != cp["last_installed_t"] or not pipeline["policy"]
            or pipeline["policy"]["semantic_seq"] != cp["semantic_seq"]
            or cp["next_semantic_t"] != min(duration, cp["semantic_seq"] + 1)
            or cp["next_control_t"] != min(duration, round((cp["control_seq"] + 1) * .2, 9))
            or not cp["pending_installed"] or cp["pending_installed"]["control_seq"] != cp["control_seq"]):
        raise JournalError("invalid_commit", "checkpoint schedule or installed frontier differs", 409)
    if cp["boundary_phase"] == "ready":
        semantic_at = int(cp["last_t"] + 1e-8)
        if (cp["last_t"] != round(cp["control_seq"] * .2, 9) or cp["last_installed_t"] != cp["last_t"]
                or cp["semantic_seq"] != (duration - 1 if cp["last_t"] == duration else semantic_at)):
            raise JournalError("invalid_commit", "ready checkpoint does not name its actual installed boundary", 409)
    elif cp["boundary_phase"] == "control_pending":
        n = cp["last_t"]
        if (n % 1 or not 0 < n < duration or cp["semantic_seq"] != n or cp["control_seq"] != 5 * n - 1
                or cp["last_installed_t"] != round(n - .2, 9) or cp["next_control_t"] != n):
            raise JournalError("invalid_commit", "pending control must follow the integer semantic publication", 409)
    else:
        raise JournalError("invalid_commit", "unknown checkpoint boundary phase", 409)


# Native E semantic validation.  A is consulted only for byte-identical shared
# payloads and pure source invariants; it never handles an E route/checkpoint.
def validate_feedback(value, registry=None):
    from schema_registry import SchemaRegistry
    from adaptive_validation import validate_adaptive
    if registry is None:
        registry = SchemaRegistry((), profile=SERVICE_PROFILE)
    try:
        registry.validate(value)
    except (JournalError, TypeError, ValueError, UnicodeError):
        return {"ok": False, "errors": ["schema"]}
    errors = []
    clock_fields = {"observed_at", "available_at", "valid_until", "issued_at", "effective_at", "committed_at",
                    "received_at", "as_of_t", "last_t", "last_installed_t", "next_semantic_t", "next_control_t", "from_t", "t",
                    "expires_at", "decided_at", "eligible_at", "settle_until", "started_at", "reported_at",
                    "rule_valid_until", "sampled_at", "until", "not_before", "deadline", "feedback_received_at",
                    "feedback_eligible_at", "first_received_at", "support_until", "negative_support_until",
                    "conflict_observed_at", "last_positive_at", "last_negative_at", "negative_progress_t",
                    "last_progress_at", "original_support_valid_until", "environment_valid_until"}
    source_versions = {"home-fact-batch-3": ("home-fact-batch-2", 2), "home-geometry-4": ("home-geometry-3", 3),
                       "home-engagement-state-4": ("home-engagement-state-3", 3),
                       "home-evidence-request-3": ("home-evidence-request-2", 2),
                       "home-evidence-reply-3": ("home-evidence-reply-2", 2),
                       "home-acquisition-status-2": ("home-acquisition-status-1", 1)}
    shared_registry = registry._feedback_shared_adaptive_registry
    def visit(v):
        if isinstance(v, list):
            for x in v:
                visit(x)
            return
        if not isinstance(v, dict):
            return
        # The enclosing pinned schema already validates this closed selected
        # component descriptor. Its schema/version name an original child;
        # they do not make these five metadata fields a complete payload.
        # Actual selector/identity/digest authority remains in original lookup.
        if set(v) == {"selector", "id", "schema", "version", "digest"}:
            return
        for k, x in v.items():
            if k in clock_fields and x is not None and (not finite(x) or not 0 <= x <= 120):
                errors.append("relative_clock_range")
        if v.get("clock_profile_digest") and value.get("clock_profile_digest") and v["clock_profile_digest"] != value["clock_profile_digest"]:
            errors.append("clock_profile_binding")
        if all(k in v for k in SCOPE_KEYS) and all(k in value for k in SCOPE_KEYS) and not _same_scope(v, value):
            errors.append("scope_binding")
        name = v.get("schema")
        if name == "home-context-publication-2":
            if (v["stream_epoch"] != v["journal_epoch"] or v["payload_type"] != v["payload"]["schema"]
                    or v["payload_version"] != str(v["payload"]["version"]) + ".0"
                    or v["id"] != v["payload"]["id"] or v["payload_digest"] != digest(v["payload"])):
                errors.append("publication2_payload_binding")
            if v["payload"].get("issued_at", v["payload"].get("reported_at", 0)) > v["committed_at"]:
                errors.append("publication2_original_clock")
        if name in {"home-context-worker-checkpoint-7", "home-context-replay-8"} and v["interface_digest"] != registry.interface_digest:
            errors.append("feedback_interface_pin")
        if name == "home-context-worker-checkpoint-7":
            try:
                validate_checkpoint_frontier(v)
            except JournalError:
                errors.append("feedback_checkpoint_frontier")
            inline=[*v["pending_inputs"],*v["geometry_inputs"],*v["retained_inputs"],*v["committed_output_inputs"]]
            cursors={row["publication"]["cursor"] for row in inline}
            dependencies={row["source_ref"]["publication_ref"]["cursor"]:row for row in v["dependency_receipts"]}
            if (len(cursors)!=len(inline) or len(inline)+len(v["referenced_outputs"])>96
                    or len(dependencies)!=len(v["dependency_receipts"])):errors.append("checkpoint_original_union")
            if any(row["source_ref"]["component"] is not None for row in v["dependency_receipts"]):
                errors.append("dependency_receipt_requires_parent")
            for descriptor in v["referenced_outputs"]:
                ref=descriptor["source_ref"];parent=ref["publication_ref"]
                inputs=ref["payload_type"] in {"home-lighting-control-decision-3","home-lighting-installed-boundary-1"}
                first=[row for row in v["receipts"] if row["ref"]["cursor"]==parent["cursor"]]
                version=registry.payload_versions.get(ref["payload_type"])
                if (parent["cursor"] in cursors or ref["component"] is not None
                        or ref["payload_type"] not in {"home-lighting-control-decision-3","home-lighting-installed-boundary-1","home-lighting-policy-decision-3","home-lighting-settled-exposure-1"}
                        or ref["payload_version"]!=str(version)+".0" or parent["journal_epoch"]!=v["journal_epoch"]
                        or parent["cursor"]>v["input_cursor"] or descriptor["received_at"]>v["last_t"]
                        or descriptor["eligible_at"]>v["last_t"] or descriptor["eligible_at"]<descriptor["received_at"]
                        or descriptor["digest"]!=digest({k:x for k,x in descriptor.items() if k!="digest"})
                        or dependencies.get(parent["cursor"])!=descriptor):errors.append("checkpoint_referenced_original")
                expected={"ref":parent,"received_at":descriptor["received_at"],"eligible_at":descriptor["eligible_at"]}
                if (inputs and first!=[expected]) or (not inputs and first):errors.append("checkpoint_referenced_receipt")
                cursors.add(parent["cursor"])
            if cursors!=set(dependencies):errors.append("checkpoint_dependency_union")
        if set(v) == {"consumer_id", "received_at", "eligible_at", "digest"}:
            if v["digest"] != digest({k:x for k,x in v.items() if k != "digest"}) or v["eligible_at"] < v["received_at"]:
                errors.append("original_receipt_digest_clock")
        if name == "home-context-preference-write-origin-proof-1":
            try:
                wire=v["request_canonical_json"];body=json.loads(wire);lookup=v["origin_lookup"];p=lookup["command"];first=lookup["first_receipt"]
                origin={"producer_id":p["producer_id"],"producer_epoch":p["producer_epoch"],"event_id":p["id"]}
                if (wire!=canonical(body) or len(wire.encode("utf-8"))>65536 or digest(body)!=lookup["request_digest"]
                        or p["payload_type"]!="home-preference-write-1" or body!=p["payload"] or body["producer_epoch"]!=p["producer_epoch"]
                        or lookup["journal_epoch"]!=v["journal_epoch"] or lookup["resolved_at"]!=v["resolved_at"]
                        or p["journal_epoch"]!=v["journal_epoch"] or p["committed_at"]>v["resolved_at"]
                        or lookup["origin"]!=origin or first["origin"]!=origin or first["request_digest"]!=lookup["request_digest"]
                        or first["publication_ref"]!=_publication_ref(p) or first["committed_at"]!=p["committed_at"]
                        or lookup["application"] is not None and (lookup["application"]["payload"]["origin"]!=origin
                            or lookup["application"]["payload"]["raw_request_digest"]!=lookup["request_digest"])):
                    errors.append("preference_origin_proof_binding")
            except (ValueError,KeyError,TypeError,UnicodeError):
                errors.append("preference_origin_proof_encoding")
        if name == "home-context-worker-commit-7":
            cp,cut=v["checkpoint"],v["input_cut"]
            if v["kind"] == "combined":
                errors.append("commit_requires_separate_phases")
            if (not cut["complete"] or any(x["runtime_epoch"]!=v["runtime_epoch"] for x in (cp,cut))
                    or any(x["journal_epoch"]!=v["journal_epoch"] for x in (cp,cut)) or cp["last_t"]!=cut["as_of_t"]
                    or cp["input_cursor"]!=cut["input_cursor"] or v["expected_input_cursor"]>cp["input_cursor"]
                    or cp["commit_seq"]!=v["expected_commit_seq"]+1 or v["expected_authority"]!=cut["authority"]):
                errors.append("commit_internal_frontier")
            if any(cp["authority"][k]!=cut["authority"][k] for k in ("room","sources","registrations","producers")):
                errors.append("commit_authority_fence")
            fence=lambda f:canonical([f[k] for k in ("person_ref","object_ref","association_generation","source_id","registration_ref")])
            pre,post=cut["authority"]["follows"],cp["authority"]["follows"]
            if len(pre)!=len(post):errors.append("commit_follow_set")
            pipeline=cp["pipeline"] or {};intents=pipeline.get("intents",[]);outcomes=pipeline.get("intent_outcomes",[])
            for f in pre:
                next_f=next((x for x in post if fence(x)==fence(f)),None)
                if next_f is None:errors.append("commit_follow_set");continue
                if next_f==f:continue
                owned=[]
                for b in cp["intent_authority_bindings"]:
                    if fence(b)!=fence(f):continue
                    i=next((x for x in intents if x["id"]==b["intent_id"] and x["kind"]=="dont_follow"
                            and x["effective_at"]<=b["applied_at"]<x["valid_until"]
                            and x["person_ref"]==b["person_ref"] and x["object_ref"]==b["object_ref"]),None)
                    if i and any(x["intent_id"]==b["intent_id"] and x["status"]=="applied" and x["decided_at"]==b["applied_at"] for x in outcomes):owned.append((b,i))
                active=[b for b,i in owned if i["valid_until"]>cp["last_t"]]
                closing=f["follow_allowed"] and not next_f["follow_allowed"] and next_f["follow_generation"]==f["follow_generation"]+1 and any(b["baseline_follow_generation"]==f["follow_generation"] for b in active)
                opening=not f["follow_allowed"] and next_f["follow_allowed"] and next_f["follow_generation"]==f["follow_generation"]+1 and not active and any(b["baseline_follow_generation"]+1==f["follow_generation"] for b,i in owned)
                if not closing and not opening:errors.append("commit_follow_unproven")
        if name == "home-context-worker-output-proof-3":
            c,r,p = v["commit"],v["receipt"],v["publication"]
            wire = v["request_canonical_json"]
            if (wire != canonical(c) or len(wire.encode("utf-8"))>262144 or hashlib.sha256(wire.encode("utf-8")).hexdigest() != r["raw_request_digest"]
                    or r["id"] != c["id"] or any(r[k] != c["checkpoint"][k] for k in ("commit_seq","semantic_seq","control_seq","input_cursor"))
                    or r["commit_seq"] != c["expected_commit_seq"]+1 or r["input_cursor"] != c["input_cut"]["input_cursor"]
                    or any(x["journal_epoch"] != v["journal_epoch"] for x in (c,c["input_cut"],c["checkpoint"],r,p))
                    or p["stream_epoch"] != v["journal_epoch"] or p["producer_epoch"] != r["producer_epoch"] or p["producer_id"]!=r["worker_id"]
                    or p["context_seq"] != r["commit_seq"] or not c["input_cut"]["as_of_t"] <= r["first_received_at"] <= p["committed_at"] <= v["resolved_at"]):
                errors.append("worker_output_proof_join")
            if (any(c["runtime_epoch"]!=x["runtime_epoch"] for x in (c["checkpoint"],c["input_cut"])) or not c["input_cut"]["complete"]
                    or c["expected_authority"]!=c["input_cut"]["authority"] or c["checkpoint"]["last_t"]!=c["input_cut"]["as_of_t"]
                    or c["checkpoint"]["input_cursor"]!=c["input_cut"]["input_cursor"] or c["expected_input_cursor"]>c["input_cut"]["input_cursor"]):
                errors.append("worker_output_proof_frontier")
            outputs={(x["schema"],x["id"]):x for x in c["outputs"]}
            rows=r["receipts"];cursors=[x["cursor"] for x in rows]
            if (len(outputs)!=len(c["outputs"]) or len({x["id"] for x in rows})!=len(rows) or len(rows)!=len(outputs) or cursors!=sorted(set(cursors))
                    or r["cursor"]!=max([r["input_cursor"],*cursors])):
                errors.append("worker_output_proof_outputs")
            for index,row in enumerate(rows):
                matches=[x for x in c["outputs"] if x["id"]==row["id"]]
                if (len(matches)!=1 or index>=len(c["outputs"]) or c["outputs"][index]["id"]!=row["id"] or row["payload_digest"]!=digest(matches[0]) or row["producer_id"]!=p["producer_id"]
                        or row["producer_epoch"]!=p["producer_epoch"] or row["stream_epoch"]!=p["stream_epoch"]
                        or row["cursor"]<=c["input_cut"]["input_cursor"] or not r["first_received_at"]<=row["committed_at"]<=v["resolved_at"]):
                    errors.append("worker_output_proof_receipt")
            joined=[x for x in rows if x["cursor"]==p["cursor"]]
            if (outputs.get((p["payload_type"],p["id"]))!=p["payload"] or len(joined)!=1
                    or any(joined[0][k]!=p[k] for k in ("id","producer_id","producer_epoch","stream_epoch","committed_at","payload_digest"))):
                errors.append("worker_output_proof_publication")
            for key in ("consumer_receipt","worker_receipt"):
                if v[key] is not None and not p["committed_at"]<=v[key]["received_at"]<=v[key]["eligible_at"]<=v["resolved_at"]:
                    errors.append("worker_output_proof_delivery")
        if name == "home-context-snapshot-5":
            def write_head_key(h):
                return canonical([h["payload_type"],h["owner_id"],h["subject"],h["preference_set_id"]])
            def write_parent_rank(p):
                effective,available=original_family_clocks(p["payload"],p["committed_at"])
                return (p["payload"].get("sampled_at",effective),available,p["committed_at"],p["cursor"])
            keys=[write_head_key(h) for h in v["write_heads"]]
            expected={}
            for publication in v["items"]:
                if publication["payload_type"] not in {"home-preference-write-authority-1","home-preference-write-frontier-1"}:continue
                body=publication["payload"]
                try:clocks=original_family_clocks(body,publication["committed_at"])
                except JournalError:
                    errors.append("write_head_original_clock");continue
                if max(*clocks,publication["committed_at"])>v["as_of_t"]:continue
                key=write_head_key({"payload_type":publication["payload_type"],**body})
                if key not in expected or write_parent_rank(publication)>write_parent_rank(expected[key]):expected[key]=publication
            if keys!=sorted(set(keys),key=_utf16_sort_key):errors.append("write_head_identity_order")
            if len(keys)!=len(expected) or any(key not in expected for key in keys):errors.append("write_head_coverage")
            for head in v["write_heads"]:
                p=next((p for p in v["items"] if p["cursor"]==head["source_ref"]["publication_ref"]["cursor"]),None)
                body=p["payload"] if p else None
                current=expected.get(write_head_key(head))
                if (p is None or current is None or p["cursor"]!=current["cursor"]
                        or p["payload_type"] not in {"home-preference-write-authority-1","home-preference-write-frontier-1"}
                        or head["payload_type"]!=p["payload_type"] or p["id"]!=body["id"] or p["payload_digest"]!=digest(body) or not _same_scope(v,body)
                        or any(head[k]!=body[k] for k in ("owner_id","subject","preference_set_id","valid_until"))
                        or head["source_ref"]!={"publication_ref":_publication_ref(p),"publication_id":p["id"],"producer_id":p["producer_id"],
                                              "producer_epoch":p["producer_epoch"],"payload_type":p["payload_type"],"payload_version":p["payload_version"],"component":None}):
                    errors.append("write_head_original_binding");continue
                try:clocks=original_family_clocks(body,p["committed_at"])
                except JournalError:
                    errors.append("write_head_original_clock");continue
                original_eligible=max(*clocks,p["committed_at"])
                publisher=next((x for x in v["authority"]["producers"] if x["producer_id"]==p["producer_id"] and x["producer_epoch"]==p["producer_epoch"]),None)
                disposition=("unavailable" if publisher is None or publisher["revoked"] else "revoked" if body.get("status")=="revoked"
                             else "expired" if body["valid_until"]<=v["as_of_t"] else "active")
                if head["eligible_at"]!=original_eligible or original_eligible>v["as_of_t"] or head["disposition"]!=disposition:
                    errors.append("write_head_disposition")
        if name == "home-context-feedback-read-cut-2":
            snapshot=v["snapshot"]
            if (v["digest"]!=digest({k:x for k,x in v.items() if k!="digest"})
                    or snapshot["stream_epoch"]!=v["journal_epoch"] or snapshot["cursor"]!=v["input_cursor"]
                    or snapshot["as_of_t"]!=v["as_of_t"] or snapshot["authority"]!=v["authority"]
                    or len(v["receipts"])!=len(snapshot["items"])
                    or len({p["cursor"] for p in snapshot["items"]})!=len(snapshot["items"])):
                errors.append("feedback_read_cut_binding")
            for index,row in enumerate(v["receipts"]):
                if index>=len(snapshot["items"]):
                    errors.append("feedback_read_cut_original_receipt");continue
                p=snapshot["items"][index];first=row["consumer_receipt"];body=p["payload"]
                try:observed,available=original_family_clocks(body,p["committed_at"])
                except JournalError:
                    errors.append("feedback_read_cut_original_receipt");continue
                if (p["cursor"]>v["input_cursor"] or p["journal_epoch"]!=v["journal_epoch"]
                        or row["publication_ref"]!={"journal_epoch":p["journal_epoch"],"cursor":p["cursor"],"payload_digest":p["payload_digest"]}
                        or first["consumer_id"]!=v["consumer_id"] or not p["committed_at"]<=first["received_at"]<=v["as_of_t"]
                        or observed is None or available is None
                        or first["eligible_at"]!=max(first["received_at"],p["committed_at"],observed,available)
                        or first["digest"]!=digest({k:first[k] for k in ("consumer_id","received_at","eligible_at")})):
                    errors.append("feedback_read_cut_original_receipt")
        if v.get("normalized") is not None:
            normalized = v["normalized"]
            if normalized.get("id") != v["id"] or normalized.get("scope") and not _same_scope(v, normalized["scope"]):
                errors.append("typed_normalized_identity")
        if "authority" in v:
            try:
                validate_authority(v["authority"], {k:value[k] for k in SCOPE_KEYS} if all(k in value for k in SCOPE_KEYS) else {k:v[k] for k in SCOPE_KEYS})
            except JournalError:
                errors.append("authority")
        if v.get("min") is not None and v.get("max") is not None and (v["min"] > v["max"] or "desired" in v and not v["min"] <= v["desired"] <= v["max"]):
            errors.append("lux_order")
        if "effective_at" in v and "valid_until" in v and v["valid_until"] <= max(v["effective_at"], v.get("available_at", v["effective_at"])):
            errors.append("ttl")
        if "issued_at" in v and ("available_at" in v and v["issued_at"] > v["available_at"] or "valid_until" in v and v["issued_at"] >= v["valid_until"]):
            errors.append("issued_clock")
        if name in {"home-control-input-cut-3", "home-dynamic-geometry-snapshot-3", "home-context-replay-8"} and digest({k:x for k,x in v.items() if k != "digest"}) != v["digest"]:
            errors.append("content_digest")
        if name in source_versions:
            old = copy.deepcopy(v); old["schema"], old["version"] = source_versions[name]; old.pop("clock_profile_digest", None)
            errors.extend(validate_adaptive(old, shared_registry)["errors"])
        elif name and name in registry.schemas:
            from schema_registry import ADAPTIVE_VERSIONS
            if name in ADAPTIVE_VERSIONS and name not in source_versions:
                errors.extend(validate_adaptive(v, shared_registry)["errors"])
        if name == "home-preference-snapshot-2":
            tags = v["condition_tags"]
            if tags is not None and (not tags or tags != sorted(set(tags))) or (tags is None) != (v["context_set_id"] is None):
                errors.append("conditional_identity")
            if v["rule_valid_until"] < v["valid_until"]:
                errors.append("conditional_lifetime")
            pred = v["expected_predecessor"]
            if (v["revision"] == 0 and pred is not None or v["revision"] > 0 and (pred is None or pred["revision"] != v["revision"]-1)):
                errors.append("revision_predecessor")
            band = v["qualities"]["illuminance_band"]
            if band is not None and not band["min_lux"] <= band["desired_lux"] <= band["max_lux"]:
                errors.append("band_order")
        if name == "home-experience-context-declaration-1":
            if (not v["tags"] or any(child["normalized"]["tags"] != v["tags"] for child in v["context_components"].values())):
                errors.append("context_original_tags")
        if name in {"home-experience-producer-revocation-1", "home-feedback-authority-revocation-1"}:
            if v["generation"] != v["expected_generation"] + 1 or v["issued_at"] != v["effective_at"] or v["issued_at"] != v["available_at"]:
                errors.append("revocation_generation_clock")
            if name == "home-feedback-authority-revocation-1" and (v["kind"] == "room_motion") != (v["source_id"] is None):
                errors.append("authority_revocation_source")
        if name == "home-lighting-two-sided-sensitivity-1":
            cases = [v["decrease_case"], v["increase_case"]]
            if (cases[0]["id"] == cases[1]["id"] or cases[0]["direction"] != "decrease" or cases[1]["direction"] != "increase"
                    or cases[0]["delta_lux"] != -25 or cases[1]["delta_lux"] != 25):
                errors.append("two_sided_direction_binding")
            gain = v["response_gain"]
            if v["status"] == "qualified" and (not v["whole_affine_range_proved"] or gain is None or gain["lower"] <= 0 or gain["lower"] > gain["upper"]
                    or v["tracking_error_upper_lux"] is None or v["tracking_error_upper_lux"] > 5
                    or any(c["status"] != "modeled_supported" or c["delivery_attribution"] is None
                           or any(r["status"] != "modeled_supported" or not r["all_cell_range_met"] or not r["mechanics_admitted"]
                                  or r["tracking_error_lux"] is None or r["tracking_error_lux"] > 5 for r in c["slice_results"]) for c in cases)):
                errors.append("two_sided_qualification")
            if any(c["status"] == "unavailable" and c["delivery_attribution"] is not None for c in cases):
                errors.append("unavailable_direction_attribution")
        if name == "home-preference-learning-assessment-1":
            cert = v["two_sided_delivery"]
            if (v["t"] != cert["t"] or v["exposure_ref"] != cert["common_basis"]["exposure_ref"]
                    or v["context_ref"] != cert["common_basis"]["context_ref"] or v["status"] == "qualified" and cert["status"] != "qualified"):
                errors.append("assessment_certificate_binding")
        if v.get("registry_digest") and "fixtures" in v and "commands" in v and v.get("digest") and v.get("clock_profile_digest"):
            if digest({k:x for k,x in v.items() if k != "digest"}) != v["digest"]:
                errors.append("installed_state_digest")
        if "fixtures" in v and "commands" in v and finite(v.get("t")):
            for f in v["fixtures"]:
                command = next((c for c in v["commands"] if c["fixture_id"] == f["id"]), None)
                if command is None:
                    errors.append("mechanics_command_missing"); continue
                reducing = f["fade"] is not None and f["fade"]["to_level"] == 0 and f["fade"]["from_level"] >= f["fade"]["to_level"] and f["fade"]["duration_s"] <= .2 + 1e-8
                same = command["target_id"] is not None and f["target_id"] == command["target_id"]
                if (command["target_id"] is None and command["level"] != 0 or command["target_id"] is not None and command["level"] == 0
                        or f["level"] > 0 and not reducing and (not same or f["phase"] in {"idle","dark","braking"})
                        or f["phase"] == "lit" and (not same or f["motion"] is not None or v["t"] < f["settle_until"]-1e-8)
                        or f["motion"] is None and (any(f[a][x] != 0 for a in ("pan","tilt") for x in ("velocity","acceleration")) or f["phase"] in {"slewing","braking"})
                        or f["motion"] is not None and f["phase"] != ("braking" if f["motion"]["kind"] == "brake" else "slewing")):
                    errors.append("mechanics_lifecycle")
        if name == "home-lighting-control-decision-3":
            if (any(v[k]["t"] != v["t"] for k in ("allocation","execution","installed_state","interval_proof"))
                    or v["geometry_snapshot"]["as_of_t"] != v["t"] or any(v[k]["control_seq"] != v["control_seq"] for k in ("allocation","execution","installed_state"))
                    or v["interval_proof"]["from_t"] != v["execution"]["from_t"]
                    or v["interval_proof"]["execution_sample_digest"] != digest(v["execution"]["samples"])):
                errors.append("control3_binding")
        if name == "home-lighting-execution-3":
            if (len(set(v["fixture_ids"])) != 4 or len(set(v["target_ids"])) != len(v["target_ids"])
                    or any(row[8] >= len(v["target_ids"]) for rows in [*[s["states"] for s in v["samples"]],v["boundary_state"]] for row in rows)):
                errors.append("execution_dictionary")
            if abs(v["t"]-v["from_t"]-.2)>1e-8 or len(v["samples"]) != 10 or any(abs(s["t"]-(v["from_t"]+(i+1)*.02))>1e-8 for i,s in enumerate(v["samples"])):
                errors.append("execution_sample_order")
        if name == "home-lighting-settled-exposure-1":
            if (v["t"] < 2 and (v["status"] != "unavailable" or v["from_t"] is not None or v["until"] is not None or v["exposures"])
                    or v["t"] >= 2 and (v["from_t"] is None or v["until"] is None or abs(v["from_t"]-(v["t"]-1.2))>1e-8 or abs(v["until"]-(v["t"]-.2))>1e-8)):
                errors.append("fixed_exposure_window")
        if name == "home-lighting-settled-exposure-component-1" and v["status"] == "qualified":
            if (v["qualification_basis"] is None or len(v["installation_bases"]) != 5 or len(v["control_refs"]) != 5
                    or len(v["boundary_refs"]) != 6 or len(v["interval_digests"]) != 5 or v["actual_sample_count"] != 50
                    or v["assessed_lux"] is None or abs(v["until"]-v["from_t"]-1)>1e-8
                    or v["assessed_lux"]["min"] < v["requested_lux"]["min"] or v["assessed_lux"]["max"] > v["requested_lux"]["max"]):
                errors.append("qualified_exposure_completeness")
        for x in v.values():
            visit(x)
    visit(value)
    return {"ok": not errors, "errors": errors}


_base_validate_authority = validate_authority
def validate_authority(authority, identity):
    if not isinstance(authority, dict) or set(authority) != {"room","sources","registrations","follows","producers"}:
        _error("feedback_authority_shape")
    _base_validate_authority({k:v for k,v in authority.items() if k != "producers"}, identity)
    rows = authority["producers"]
    if not isinstance(rows, list) or len(rows)>8:
        _error("feedback_producer_authority")
    keys=[]
    for row in rows:
        if (not isinstance(row,dict) or set(row)!={"producer_id","producer_epoch","revocation_generation","revoked"}
                or not _identifier(row["producer_id"]) or not _identifier(row["producer_epoch"])
                or not _integer(row["revocation_generation"]) or type(row["revoked"]) is not bool):
            _error("feedback_producer_authority")
        keys.append(canonical([row["producer_id"],row["producer_epoch"]]))
    if keys != sorted(set(keys)):
        _error("feedback_producer_authority")
    return authority


def validate_configuration(value, registry=None, *, profile_mode=SERVICE_PROFILE):
    required={"identity","clock_profile","materials","registered_surfaces","config","config_digest",
              "producer_epochs","actor_bindings","classifier_policy","write_authorities"}
    if not isinstance(value,dict) or set(value)!=required or not _scope(value["identity"]):
        _error("feedback_configuration")
    bounded_json(value,256*1024)
    assert_clock_profile(value["clock_profile"])
    validate_materials(value["materials"],value["identity"])
    if value["identity"]["session_id"] != value["clock_profile"]["session_id"]:
        _error("feedback_configuration")
    producers=validate_producer_epochs(value["producer_epochs"])
    if canonical(producers) != canonical(value["producer_epochs"]):
        _error("feedback_producer_configuration")
    config=value["config"]
    recipe={"registry","profile","clock_profile_digest","interface_digest","registered_catalogue_digest","materials_digest","profile_mode","feedback_config_digest"}
    if not isinstance(config,dict) or set(config)!=recipe or config["profile_mode"]!=profile_mode:
        _error("feedback_configuration")
    feedback={k:value[k] for k in ("producer_epochs","actor_bindings","classifier_policy","write_authorities")}
    if (config["feedback_config_digest"]!=digest(feedback) or config["materials_digest"]!=digest(value["materials"])
            or config["registered_catalogue_digest"]!=digest(value["registered_surfaces"])
            or config["clock_profile_digest"]!=value["clock_profile"]["digest"] or value["config_digest"]!=digest(config)):
        _error("feedback_configuration_digest")
    if registry is None:
        from schema_registry import SchemaRegistry
        registry=SchemaRegistry((),profile=SERVICE_PROFILE)
    room=config["registry"]
    if (config["interface_digest"]!=registry.interface_digest or not isinstance(room,dict)
            or room.get("site_id")!=value["identity"]["site_id"] or room.get("room_id")!=value["identity"]["room_id"]
            or room.get("digest")!=digest({k:x for k,x in room.items() if k!="digest"})
            or any(m["registry_digest"]!=room["digest"] for m in value["materials"])):
        _error("feedback_configuration_binding")
    families={"registered_surfaces":("home-registered-surface-1",2),"actor_bindings":("home-experience-actor-binding-1",4),
              "write_authorities":("home-preference-write-authority-1",2)}
    for field,(family,bound) in families.items():
        if not isinstance(value[field],list) or len(value[field])>bound or len({x.get("id") for x in value[field]})!=len(value[field]):
            _error("feedback_configuration_binding")
        for x in value[field]:
            if (x.get("schema")!=family or not _same_scope(value["identity"],x) or x.get("clock_profile_digest")!=value["clock_profile"]["digest"]
                    or not validate_feedback(x,registry)["ok"]):
                _error("feedback_configuration_binding")
    policy=value["classifier_policy"]
    if (not isinstance(policy,dict) or policy.get("schema")!="home-experience-classifier-policy-1"
            or not _same_scope(value["identity"],policy) or policy.get("clock_profile_digest")!=value["clock_profile"]["digest"]
            or not validate_feedback(policy,registry)["ok"]):
        _error("feedback_configuration_binding")
    pairs={(p["principal_id"],p["producer_id"],p["producer_epoch"]) for p in producers}
    for binding in value["actor_bindings"]:
        if (binding["principal_id"],binding["feedback_origin"]["producer_id"],binding["feedback_origin"]["producer_epoch"]) not in pairs:
            _error("feedback_actor_configuration")
        if binding["binding_digest"]!=digest({k:x for k,x in binding.items() if k!="binding_digest"}):
            _error("feedback_actor_configuration")
    for authority in value["write_authorities"]:
        writer=authority["writer_origin"];normalized=authority["authority_component"]["normalized"]
        if ((authority["writer_principal_id"],writer["producer_id"],writer["producer_epoch"]) not in pairs
                or normalized["writer_origin"]!=writer or normalized["feedback_origin"]!=authority["feedback_origin"]
                or not any(binding["feedback_origin"]==authority["feedback_origin"]
                           and binding["actor_ref"]==authority["actor_ref"] and binding["person_ref"]==authority["person_ref"]
                           and any(all(rule[k]==authority[k] for k in ("owner_id","subject","preference_set_id","context_set_id","target"))
                                   and rule["beneficiary"]=={"kind":"person","person_ref":authority["person_ref"]}
                                   for rule in binding["allowed_bindings"])
                           for binding in value["actor_bindings"])):
            _error("feedback_authority_configuration")
    return value



# The publication reservation is a closed authored matrix, not a guarantee that
# simultaneously maximal payloads fit the aggregate byte/database budgets.
ADAPTIVE_QUOTAS = {"home-geometry-4":600,"home-fact-batch-3":120,"home-lighting-control-decision-3":600,
 "home-engagement-state-4":120,"home-lighting-policy-decision-3":120,"home-lighting-settled-exposure-1":120,
 "home-experience-goal-1":32,"home-lighting-environment-1":16,"home-preference-snapshot-2":8,
 "home-experience-context-declaration-1":8,"home-intent-event-3":8,"home-goal-declaration-1":8,"home-intent-application-result-3":8,
 "home-experience-feedback-1":16,"home-experience-feedback-application-1":16,"home-experience-actor-binding-1":4,
 "home-experience-classifier-policy-1":1,"home-preference-write-authority-1":2,"home-preference-write-frontier-1":32,
 "home-preference-learning-assessment-1":16,"home-experience-classifier-disposition-1":16,
 "home-preference-learning-policy-1":1,"home-preference-selector-memory-policy-1":1,
 "home-preference-write-1":6,"home-preference-write-application-1":6,"home-lighting-capability-1":2,
 "home-registered-surface-1":2,"home-experience-producer-revocation-1":2,"home-feedback-authority-revocation-1":2,
 "home-lighting-installed-boundary-1":1,"home-evidence-request-3":16,"home-evidence-reply-3":16,"home-acquisition-status-2":8}
ADAPTIVE_INPUTS = frozenset(ADAPTIVE_QUOTAS)-{"home-engagement-state-4","home-lighting-policy-decision-3","home-lighting-settled-exposure-1"}
ADAPTIVE_OUTPUTS = frozenset({"home-engagement-state-4","home-lighting-policy-decision-3","home-lighting-control-decision-3",
                             "home-experience-goal-1","home-lighting-installed-boundary-1","home-lighting-settled-exposure-1","home-experience-feedback-application-1","home-intent-application-result-3"})
ADAPTIVE_FAMILIES = frozenset({"home-registered-surface-1","home-lighting-environment-1","home-lighting-capability-1"})
_FeedbackCore = AdaptiveFeedbackProfile


def preference_unchanged_projection(payload):
    """Exact administrative deletion recipe, preserving every hard constraint."""
    result=copy.deepcopy(payload)
    for key in ("id","revision","expected_predecessor","issued_at","effective_at","available_at","valid_until",
                "condition_tags","context_set_id","rule_valid_until","write_origin"):
        result.pop(key,None)
    result["qualities"]["illuminance_band"].pop("desired_lux",None)
    return result


class AdaptiveFeedbackProfile(_FeedbackCore):
    """Opt-in E public authority, with original epoch and consumer proofs."""

    commit_schema = "home-context-worker-commit-7"
    output_proof_schema = "home-context-worker-output-proof-3"
    output_proof_version = 3
    checkpoint_response_schema = "home-context-worker-checkpoint-response-5"
    checkpoint_response_version = None

    def _phase_transition_wire(self, commit, old, grant):
        return self._phase_transition(commit, old)

    def __init__(self,journal):
        configured=validate_producer_epochs(journal.adaptive_feedback_config["producer_epochs"])
        for g in journal.grants:
            if ({"principal_id":g.principal_id,"producer_id":g.producer_id,"producer_epoch":g.producer_epoch} not in configured
                    or not g.expected_consumer_id):
                raise ValueError("E grant requires its exact configured producer epoch and consumer")
            if "worker_commit" in g.operations and g.expected_worker_id!=g.producer_id:
                raise ValueError("E worker identity must equal its authenticated publisher")
        super().__init__(journal)
        self.origins=PrivateOriginLedger(journal.db)
        self.j.db.execute("CREATE TABLE feedback_worker_commit_originals(producer_id TEXT NOT NULL,producer_epoch TEXT NOT NULL,id TEXT NOT NULL,request_digest TEXT NOT NULL,request_blob BLOB NOT NULL,PRIMARY KEY(producer_id,producer_epoch,id))")
        self.j.db.execute("CREATE TABLE feedback_write_results(producer_id TEXT NOT NULL,producer_epoch TEXT NOT NULL,event_id TEXT NOT NULL,application_cursor INTEGER NOT NULL,successor_cursor INTEGER,PRIMARY KEY(producer_id,producer_epoch,event_id))")
        self.j.db.execute("CREATE TABLE feedback_write_originals(principal_id TEXT NOT NULL,producer_id TEXT NOT NULL,producer_epoch TEXT NOT NULL,event_id TEXT NOT NULL,request_digest TEXT NOT NULL,request_blob BLOB NOT NULL,PRIMARY KEY(principal_id,producer_id,producer_epoch,event_id))")
        self.j.db.execute("CREATE TABLE feedback_worker_first_receipts(principal_id TEXT NOT NULL,consumer_id TEXT NOT NULL,cursor INTEGER NOT NULL,received_at REAL NOT NULL,eligible_at REAL NOT NULL,PRIMARY KEY(principal_id,consumer_id,cursor))")
        self.j.db.execute("CREATE TABLE feedback_consumer_receipts(principal_id TEXT NOT NULL,consumer_id TEXT NOT NULL,cursor INTEGER NOT NULL,received_at REAL NOT NULL,eligible_at REAL NOT NULL,digest TEXT NOT NULL,PRIMARY KEY(principal_id,consumer_id,cursor))")
        self.j.db.execute("CREATE TABLE feedback_read_cuts(cut_id TEXT PRIMARY KEY,principal_id TEXT NOT NULL,consumer_id TEXT NOT NULL,site_id TEXT NOT NULL,room_id TEXT NOT NULL,as_of_t REAL NOT NULL,input_cursor INTEGER NOT NULL,authority_digest TEXT NOT NULL,body_blob BLOB NOT NULL,body_digest TEXT NOT NULL,UNIQUE(principal_id,consumer_id,site_id,room_id,as_of_t,input_cursor,authority_digest))")
        self.j.db.execute("CREATE INDEX feedback_receipts_cursor ON receipts(json_extract(receipt,'$.cursor')) WHERE json_extract(receipt,'$.schema')='home-context-receipt-2'")

    def _auth(self,grant):
        return {"principal_id":grant.principal_id,"producer_id":grant.producer_id,"producer_epoch":grant.producer_epoch}

    def _admitted_reference_output(self,publication,grant):
        """A configured publisher alone is not an atomic worker admission."""
        rows=self.j.db.execute("SELECT receipt FROM receipts WHERE producer_id=? AND source_epoch=? AND event_id LIKE 'worker-commit/%' AND json_extract(receipt,'$.commit_seq')=?",
                               (grant.producer_id,grant.producer_epoch,publication["context_seq"])).fetchall()
        if len(rows)!=1:raise JournalError("invalid_reference","referenced output lacks one genuine worker commit",409)
        receipt=json.loads(rows[0][0]);matches=[r for r in receipt["receipts"] if r["cursor"]==publication["cursor"]]
        stored=self.j.db.execute("SELECT request_digest FROM feedback_worker_commit_originals WHERE producer_id=? AND producer_epoch=? AND id=?",
                                 (grant.producer_id,grant.producer_epoch,receipt["id"])).fetchone()
        if (not stored or stored[0]!=receipt["raw_request_digest"] or receipt["worker_id"]!=grant.expected_worker_id
                or receipt["producer_epoch"]!=grant.producer_epoch or receipt["journal_epoch"]!=publication["journal_epoch"]
                or receipt["commit_seq"]!=publication["context_seq"] or len(matches)!=1
                or any(matches[0][k]!=publication[k] for k in ("id","producer_id","producer_epoch","stream_epoch","cursor","committed_at","payload_digest"))):
            raise JournalError("invalid_reference","referenced parent is not its exact admitted worker output",409)

    def _hydrate_checkpoint_outputs(self,checkpoint,grant,cut=None):
        """Proof-only view; never serialize or validate its duplicate ref union."""
        self.validate_payload(checkpoint)
        return self._expand_referenced_outputs(checkpoint, grant, cut)

    def _expand_referenced_outputs(self,checkpoint,grant,cut=None):
        """Private semantic expansion after the profile validates its wire."""
        view=copy.deepcopy(checkpoint)
        claims={r["ref"]["cursor"]:r for r in cut["receipts"]} if cut else {}
        for descriptor in checkpoint["referenced_outputs"]:
            ref=descriptor["source_ref"]
            publication,body=self._source_component(ref,grant,through=checkpoint["input_cursor"],historical=True)
            if (ref["component"] is not None or publication["producer_id"]!=self._worker_producer
                    or publication["producer_id"]!=grant.producer_id or publication["producer_epoch"]!=grant.producer_epoch
                    or publication["payload_type"] not in {"home-lighting-control-decision-3","home-lighting-installed-boundary-1","home-lighting-policy-decision-3","home-lighting-settled-exposure-1"}
                    or descriptor["eligible_at"]!=self._eligible(publication,descriptor["received_at"])):
                raise JournalError("invalid_reference","reference recovery differs from configured whole output or original eligibility",409)
            self._admitted_reference_output(publication,grant)
            delivered=self.j.db.execute("SELECT received_at,eligible_at,digest FROM feedback_consumer_receipts WHERE principal_id=? AND consumer_id=? AND cursor=?",
                                         (grant.principal_id,grant.expected_consumer_id,publication["cursor"])).fetchone()
            prior=self.j.db.execute("SELECT received_at,eligible_at FROM feedback_worker_first_receipts WHERE principal_id=? AND consumer_id=? AND cursor=?",
                                     (grant.principal_id,grant.expected_consumer_id,publication["cursor"])).fetchone()
            first={"consumer_id":grant.expected_consumer_id,"received_at":delivered[0],"eligible_at":delivered[1]} if delivered else None
            claimed=claims.get(publication["cursor"])
            input_family=publication["payload_type"] in {"home-lighting-control-decision-3","home-lighting-installed-boundary-1"}
            if (not delivered or delivered[2]!=digest(first) or delivered[1]!=self._eligible(publication,delivered[0])
                    or not max(publication["committed_at"],delivered[0])<=descriptor["received_at"]<=checkpoint["last_t"]
                    or prior and tuple(prior)!=(descriptor["received_at"],descriptor["eligible_at"])
                    or not prior and (cut is None or input_family and (not claimed
                        or claimed!={"ref":ref["publication_ref"],"received_at":descriptor["received_at"],"eligible_at":descriptor["eligible_at"]}))):
                raise JournalError("invalid_input_receipt","reference recovery lacks unchanged authentic worker FIRST",409)
            row={"publication":publication,"received_at":descriptor["received_at"]}
            view["retained_inputs" if input_family else "committed_output_inputs"].append(row)
        # Individual original validations above are safe. The encoded union
        # alone goes through validate_feedback; this private hydrated view
        # intentionally contains both descriptors and their resolved bodies.
        return view

    def _authorization(self,payload,grant,right):
        if payload.get("schema") not in {"home-preference-write-1","home-preference-write-authority-1","home-preference-write-frontier-1"}:return super()._authorization(payload,grant,right)
        for stored in grant.adaptive_subjects:
            binding=json.loads(stored)
            if (right in {"write","read"} and _same_scope(self.identity,payload) and binding["scope"]==self.identity
                    and binding["owner_id"]==payload["owner_id"] and any(rule["subject"]==payload["subject"]
                    and right in rule["rights"] and payload["preference_set_id"] in rule["chain_ids"] for rule in binding["subject_rights"])):
                return {**binding,"producer_id":grant.producer_id}
        raise JournalError("forbidden","write command is outside exact authenticated owner/subject/chain",403)

    def _read_allowed(self,publication,grant):
        if publication["payload_type"] in {"home-preference-write-authority-1","home-preference-write-frontier-1"}:
            try:self._authorization(publication["payload"],grant,"read")
            except JournalError as error:
                if error.code=="forbidden":return False
                raise
        return super()._read_allowed(publication,grant)

    def _producer_allowed(self,producer,epoch):
        authority=json.loads(self.j._meta("adaptive-authority"))
        row=next((x for x in authority["producers"] if x["producer_id"]==producer and x["producer_epoch"]==epoch),None)
        return row is not None and not row["revoked"]

    def _grant_current(self,grant):
        if not self._producer_allowed(grant.producer_id,grant.producer_epoch):
            raise JournalError("producer_revoked","configured producer epoch is revoked",409)

    def _consumer(self,grant,consumer):
        if consumer!=grant.expected_consumer_id:
            raise JournalError("forbidden","consumer differs from its authenticated E grant",403)
        return consumer

    def _cut_count(self):
        return sum(self.j.db.execute("SELECT count(*) FROM "+name).fetchone()[0]
                   for name in ("dynamic_cuts","feedback_read_cuts"))

    def _retained_read_cut(self, row, grant):
        """Lookup preserves the original pair and first receipts, including clocks."""
        try:
            decoder=zlib.decompressobj()
            raw=decoder.decompress(row["body_blob"],self.j.limits.max_response_bytes+1)
            if (len(raw)>self.j.limits.max_response_bytes or decoder.unconsumed_tail
                    or not decoder.eof or decoder.unused_data):
                raise ValueError("bounded complete stream required")
            text=raw.decode("utf-8");result=json.loads(text)
            if canonical(result)!=text or digest(result)!=row["body_digest"]:
                raise ValueError("retained bytes changed")
            self.validate_payload(result)
            for publication in result["snapshot"]["items"]:
                original=self._ref(_publication_ref(publication),grant,self.identity,result["input_cursor"],allow_revoked=True)
                if original!=publication:
                    raise ValueError("snapshot parent changed original bytes")
            for row_receipt in result["receipts"]:
                first=self.j.db.execute("SELECT received_at,eligible_at,digest FROM feedback_consumer_receipts WHERE principal_id=? AND consumer_id=? AND cursor=?",
                                        (grant.principal_id,result["consumer_id"],row_receipt["publication_ref"]["cursor"])).fetchone()
                expected={"consumer_id":result["consumer_id"],"received_at":first[0],"eligible_at":first[1],"digest":first[2]} if first else None
                if expected!=row_receipt["consumer_receipt"]:
                    raise ValueError("retained first delivery changed")
        except (ValueError,UnicodeError,zlib.error,RecursionError):
            raise JournalError("corrupt_original","retained read cut failed exact bounded recovery",409) from None
        return result

    def read_cut(self, grant, site, room, *, consumer_id, cut_id=None):
        grant.require("read",site,room);self._scope_check(site,room);self._consumer(grant,consumer_id)
        if cut_id is not None:identifier(cut_id,"cut_id")
        def read():
            if cut_id is not None:
                row=self.j.db.execute("SELECT * FROM feedback_read_cuts WHERE cut_id=? AND principal_id=? AND consumer_id=? AND site_id=? AND room_id=?",
                                      (cut_id,grant.principal_id,consumer_id,site,room)).fetchone()
                if row is None:raise JournalError("unknown_cut","retained read cut is outside authenticated consumer scope",404)
                return self._retained_read_cut(row,grant)
            self._grant_current(grant)
            snapshot=self._snapshot_read(grant,site,room,consumer_id)
            authority_digest=digest(snapshot["authority"])
            previous=self.j.db.execute("SELECT * FROM feedback_read_cuts WHERE principal_id=? AND consumer_id=? AND site_id=? AND room_id=? AND as_of_t=? AND input_cursor=? AND authority_digest=?",
                                       (grant.principal_id,consumer_id,site,room,snapshot["as_of_t"],snapshot["cursor"],authority_digest)).fetchone()
            if previous is not None:return self._retained_read_cut(previous,grant)
            if self._cut_count()>=1024:raise JournalError("cut_budget","shared synthetic fence budget exhausted",429)
            receipts=[]
            for publication in snapshot["items"]:
                first=self.j.db.execute("SELECT received_at,eligible_at,digest FROM feedback_consumer_receipts WHERE principal_id=? AND consumer_id=? AND cursor=?",
                                        (grant.principal_id,consumer_id,publication["cursor"])).fetchone()
                if first is None:raise JournalError("invalid_input_receipt","snapshot original lacks actual consumer delivery",409)
                receipts.append({"publication_ref":_publication_ref(publication),"consumer_receipt":{
                    "consumer_id":consumer_id,"received_at":first[0],"eligible_at":first[1],"digest":first[2]}})
            result={"schema":"home-context-feedback-read-cut-2","version":2,**self.identity,
                    "clock_profile_digest":self.clock_profile["digest"],"journal_epoch":self.j.stream_epoch,
                    "consumer_id":consumer_id,"as_of_t":snapshot["as_of_t"],"cut_id":"read-cut:"+str(uuid.uuid4()),
                    "input_cursor":snapshot["cursor"],"authority":snapshot["authority"],"complete":True,
                    "snapshot":snapshot,"receipts":receipts}
            result["digest"]=digest(result)
            self.validate_payload(result);text=bounded_json(result,self.j.limits.max_response_bytes)
            self.j.db.execute("INSERT INTO feedback_read_cuts VALUES(?,?,?,?,?,?,?,?,?,?)",
                              (result["cut_id"],grant.principal_id,consumer_id,site,room,result["as_of_t"],result["input_cursor"],
                               authority_digest,zlib.compress(text.encode("utf-8"),9),digest(result)))
            self._storage_budget()
            return result
        return self.j._transaction(read)

    def _receipt_valid(self,value):
        node=self.j.schemas.schemas["home-context-worker-commit-receipt-5"][0]["properties"]["receipts"]["items"]
        return self.j.schemas._valid(value,node,node,self.j.schemas.schemas["home-context-worker-commit-receipt-5"][1])

    def _event_retry(self,event,grant,raw_request=None):
        prior=self.origins.retry(self._auth(grant),event["id"],event if raw_request is None else raw_request)
        return prior["first_receipt"] if prior else None

    def append(self,event,*,context_seq=None,status="accepted",grant=None,raw_request=None):
        if grant is None:
            raise JournalError("forbidden","E original publication requires an authenticated configured issuer",403)
        raw=event if raw_request is None else raw_request
        retry=self._event_retry(event,grant,raw)
        if retry is not None:
            return retry
        self._grant_current(grant)
        j,db=self.j,self.j.db
        kind=event["payload_type"]
        if kind not in ADAPTIVE_QUOTAS or kind not in j.schemas.publication_families:
            raise JournalError("unsupported_schema","original family is outside the closed E reservation")
        if db.execute("SELECT count(*) FROM publications WHERE payload_type=?",(kind,)).fetchone()[0]>=ADAPTIVE_QUOTAS[kind]:
            raise JournalError("journal_full","E family reservation exhausted",507)
        if kind in {"home-intent-event-3","home-goal-declaration-1","home-intent-application-result-3"} and db.execute("SELECT count(*) FROM publications WHERE payload_type IN ('home-intent-event-3','home-goal-declaration-1','home-intent-application-result-3')").fetchone()[0]>=8:
            raise JournalError("journal_full","shared E intent/declaration reservation exhausted",507)
        if kind in {"home-geometry-4","home-fact-batch-3"}:
            self._roots(event)
        source=self.source(event)
        if j._revoked(event["site_id"],event["room_id"],source,event["revision"]):
            raise JournalError("revoked","source is revoked",409)
        cursor,now=int(j._meta("cursor"))+1,j.clock()
        publication={"schema":"home-context-publication-2","id":event["id"],"producer_id":grant.producer_id,
                     "producer_epoch":grant.producer_epoch,"stream_epoch":j.stream_epoch,"journal_epoch":j.stream_epoch,
                     "cursor":cursor,"context_seq":cursor if context_seq is None else context_seq,"committed_at":now,
                     "payload_type":kind,"payload_version":event["payload_version"],"payload_digest":digest(event["payload"]),"payload":copy.deepcopy(event["payload"])}
        self.validate_payload(publication)
        if kind in REVISION_FIELDS:
            auth=self._authorization(event["payload"],grant,"derive" if kind=="home-experience-goal-1" else "write")
            self._revision_store().admit({"publication":publication},auth)
        body=bounded_json(publication,j.limits.max_body_bytes)
        db.execute("INSERT INTO publications VALUES(?,?,?,?,?,?,?)",(cursor,event["site_id"],event["room_id"],source,event["revision"],kind,body))
        receipt={"schema":"home-context-receipt-2","id":event["id"],"producer_id":grant.producer_id,"producer_epoch":grant.producer_epoch,
                 "stream_epoch":j.stream_epoch,"cursor":cursor,"committed_at":now,"payload_digest":publication["payload_digest"],"status":"accepted"}
        if db.execute("SELECT count(*) FROM receipts").fetchone()[0]>=j.limits.max_receipts:
            raise JournalError("journal_full","durable E identity budget exhausted",507)
        db.execute("INSERT INTO receipts(producer_id,source_epoch,event_id,digest,receipt,event) VALUES(?,?,?,?,?,?)",
                   (grant.producer_id,grant.producer_epoch,event["id"],digest(raw),canonical(receipt),canonical(event)))
        self.origins.remember(self._auth(grant),event["id"],raw,receipt,validate_receipt=self._receipt_valid)
        key=canonical([event["site_id"],event["room_id"],source,kind,event["payload"].get("id","room")])
        db.execute("INSERT INTO state VALUES(?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET revision=excluded.revision,valid_until=excluded.valid_until,body=excluded.body",
                   (key,event["site_id"],event["room_id"],source,event["revision"],event["valid_until"],body))
        j._set_meta("cursor",cursor);self._heads(event);self._refresh_heads(event["site_id"],event["room_id"]);self._storage_budget()
        return receipt

    def _storage_budget(self,reserved=False):
        count,size=self.j.db.execute("SELECT count(*),coalesce(sum(length(CAST(body AS BLOB))),0) FROM publications").fetchone()
        allocated=self.j.db.execute("PRAGMA page_count").fetchone()[0]*self.j.db.execute("PRAGMA page_size").fetchone()[0]
        if (count>min(self.j.limits.max_publications,2048) or size>min(self.j.limits.max_publication_bytes,11*1024*1024)
                or allocated>min(self.j.limits.max_db_bytes,47*1024*1024)):
            raise JournalError("journal_full","E aggregate capacity exhausted; no original was evicted",507)

    def _original_authorization(self,row):
        p=row["publication"]
        for g in self.j.grants:
            if g.producer_id==p["producer_id"] and g.producer_epoch==p["producer_epoch"]:
                try:
                    return self._authorization(p["payload"],g,"derive" if p["payload_type"]=="home-experience-goal-1" else "write")
                except JournalError as e:
                    if e.code!="forbidden":raise
        raise JournalError("forbidden","original revision lacks its historical authenticated writer",403)

    def _ref(self,ref,grant,scope,through,*,allow_revoked=False):
        p=super()._ref(ref,grant,scope,through,allow_revoked=allow_revoked)
        self.validate_payload(p)
        if not allow_revoked and not self._producer_allowed(p["producer_id"],p["producer_epoch"]):
            raise JournalError("producer_revoked","referenced epoch is revoked",409)
        return p

    def _source_ref(self,p,component=None):
        return {"publication_ref":_publication_ref(p),"publication_id":p["id"],"producer_id":p["producer_id"],
                "producer_epoch":p["producer_epoch"],"payload_type":p["payload_type"],"payload_version":p["payload_version"],"component":component}

    def _raw_component(self,p,component):
        if component is None:return p["payload"]
        body=p["payload"];selector=component["selector"]
        groups={"table_setting_attribution":("setting_components","table"),"model_setting_attribution":("setting_components","model"),
                "table_context":("context_components","table"),"model_context":("context_components","model"),
                "table_policy":("policy_components","table"),"acknowledged_model_policy":("policy_components","model"),"selector_memory_policy":("policy_components","selector"),
                "model_write_authority":(None,"authority_component"),"model_current_frontier":(None,"frontier_component")}
        candidates=[]
        if selector in groups:
            parent,key=groups[selector];item=body.get(parent,{}).get(key) if parent else body.get(key)
            if item is not None:candidates=[item]
        elif selector=="delivery_attribution":
            certificate=body.get("two_sided_delivery",{})
            candidates=[certificate[k]["delivery_attribution"] for k in ("decrease_case","increase_case")
                        if certificate.get(k,{}).get("delivery_attribution") is not None]
        elif selector=="execution_feedback":candidates=body.get("interval_exposures",body.get("exposures",[]))
        elif selector=="classifier_disposition" and body.get("schema")=="home-experience-classifier-disposition-1":candidates=[body]
        elif selector in {"policy_request","policy_request_lease"}:
            candidates=body.get("requests",[]) if selector=="policy_request" else [r["lease"] for r in body.get("requests",[]) if r["lease"]]
        elif selector=="worker_goal_revision" and body.get("schema")=="home-experience-goal-1":candidates=[body]
        matches=[x for x in candidates if x.get("id")==component["id"] and x.get("schema")==component["schema"] and x.get("version")==component["version"] and digest(x)==component["digest"]]
        if len(matches)!=1:raise JournalError("invalid_reference","closed component is not exact in its original parent",409)
        return matches[0]

    def _source_component(self,ref,grant,*,through=None,historical=True):
        p=self._ref(ref["publication_ref"],grant,self.identity,int(self.j._meta("cursor")) if through is None else through,allow_revoked=historical)
        if {k:ref[k] for k in ref if k!="component"} != {k:self._source_ref(p)[k] for k in ref if k!="component"}:
            raise JournalError("invalid_reference","source reference changed original publisher, epoch or identity",409)
        return p,self._raw_component(p,ref["component"])

    def _delivered_publications(self,grant,consumer,publications):
        self._consumer(grant,consumer)
        for p in publications:
            self.j.db.execute("INSERT OR IGNORE INTO dynamic_deliveries VALUES(?,?,?,?)",(grant.principal_id,consumer,p["cursor"],self.j.clock()))
            received=self.j.clock();eligible=self._eligible(p,received)
            row={"consumer_id":consumer,"received_at":received,"eligible_at":eligible}
            self.j.db.execute("INSERT OR IGNORE INTO feedback_consumer_receipts VALUES(?,?,?,?,?,?)",(grant.principal_id,consumer,p["cursor"],received,eligible,digest(row)))
        if self.j.db.execute("SELECT count(*) FROM feedback_consumer_receipts").fetchone()[0]>16384:
            raise JournalError("receipt_budget","E delivery ledger exhausted",507)

    def lookup_original(self,grant,site,room,*,cursor,consumer_id):
        grant.require("read",site,room);self._scope_check(site,room);self._consumer(grant,consumer_id)
        def read():
            row=self.j.db.execute("SELECT body FROM publications WHERE cursor=? AND site_id=? AND room_id=?",(cursor,site,room)).fetchone()
            p=json.loads(row[0]) if row else None
            if p is not None:
                self._ref(_publication_ref(p),grant,self.identity,int(self.j._meta("cursor")),allow_revoked=True)
            first=self.j.db.execute("SELECT received_at,eligible_at,digest FROM feedback_consumer_receipts WHERE principal_id=? AND consumer_id=? AND cursor=?",(grant.principal_id,consumer_id,cursor)).fetchone()
            result={"schema":"home-context-original-lookup-1","version":1,**self.identity,"clock_profile_digest":self.clock_profile["digest"],
                    "journal_epoch":self.j.stream_epoch,"resolved_at":self.j.clock(),"publication":p,
                    "receipt":{ "consumer_id":consumer_id,"received_at":first[0],"eligible_at":first[1],"digest":first[2]} if first else None}
            self.validate_payload(result);return result
        return self.j._transaction(read)

    def _commit_original(self,producer,epoch,commit_id):
        """Recover actual canonical wire with a decompressed 256 KiB ceiling."""
        row=self.j.db.execute("SELECT request_digest,request_blob FROM feedback_worker_commit_originals WHERE producer_id=? AND producer_epoch=? AND id=?",(producer,epoch,commit_id)).fetchone()
        if row is None:raise JournalError("not_found","immutable worker request is unavailable",404)
        try:
            decoder=zlib.decompressobj()
            raw=decoder.decompress(row["request_blob"],self.j.limits.max_worker_commit_bytes+1)
            if (len(raw)>self.j.limits.max_worker_commit_bytes or decoder.unconsumed_tail
                    or not decoder.eof or decoder.unused_data):
                raise ValueError("bounded complete zlib stream required")
            text=raw.decode("utf-8")
            commit=json.loads(text)
            if canonical(commit)!=text or hashlib.sha256(raw).hexdigest()!=row["request_digest"]:
                raise ValueError("original wire digest changed")
            self.validate_payload(commit)
        except (ValueError,UnicodeError,zlib.error,RecursionError,JournalError):
            raise JournalError("corrupt_original","immutable worker request failed exact bounded recovery",409) from None
        return commit,text,row["request_digest"]

    def lookup_output_proof(self,grant,site,room,*,cursor,consumer_id):
        grant.require("read",site,room);self._scope_check(site,room);self._consumer(grant,consumer_id)
        def read():
            row=self.j.db.execute("SELECT body FROM publications WHERE cursor=? AND site_id=? AND room_id=?",(cursor,site,room)).fetchone()
            if row is None:raise JournalError("not_found","worker output is unavailable",404)
            publication=json.loads(row[0])
            self._ref(_publication_ref(publication),grant,self.identity,int(self.j._meta("cursor")),allow_revoked=True)
            worker_grants=[g for g in self.j.grants if "worker_commit" in g.operations
                           and g.producer_id==publication["producer_id"] and g.producer_epoch==publication["producer_epoch"]]
            if publication["producer_id"]!=self._worker_producer or not worker_grants:
                raise JournalError("not_found","publication is not an admitted worker output",404)
            matches=[]
            for saved in self.j.db.execute("SELECT receipt FROM receipts WHERE producer_id=? AND source_epoch=? AND event_id LIKE 'worker-commit/%'",(publication["producer_id"],publication["producer_epoch"])):
                receipt=json.loads(saved[0])
                if any(output["cursor"]==cursor for output in receipt["receipts"]):matches.append(receipt)
            if len(matches)!=1:raise JournalError("corrupt_original","worker output lacks one exact atomic receipt",409)
            receipt=matches[0]
            commit,text,raw_digest=self._commit_original(publication["producer_id"],publication["producer_epoch"],receipt["id"])
            output_matches=[output for output in commit["outputs"] if output.get("id")==publication["id"]
                            and output.get("schema")==publication["payload_type"]]
            if (receipt["raw_request_digest"]!=raw_digest or receipt["producer_epoch"]!=publication["producer_epoch"]
                    or len(output_matches)!=1 or output_matches[0]!=publication["payload"]
                    or not any(r["cursor"]==cursor and r["payload_digest"]==publication["payload_digest"]
                               and r["producer_epoch"]==publication["producer_epoch"] for r in receipt["receipts"])):
                raise JournalError("corrupt_original","output, commit and receipt no longer join exactly",409)
            first=self.j.db.execute("SELECT received_at,eligible_at,digest FROM feedback_consumer_receipts WHERE principal_id=? AND consumer_id=? AND cursor=?",(grant.principal_id,consumer_id,cursor)).fetchone()
            worker=worker_grants[0]
            processed=self.j.db.execute("SELECT received_at,eligible_at FROM feedback_worker_first_receipts WHERE principal_id=? AND consumer_id=? AND cursor=?",(worker.principal_id,worker.expected_consumer_id,cursor)).fetchone()
            service_receipt={"consumer_id":consumer_id,"received_at":first[0],"eligible_at":first[1],"digest":first[2]} if first else None
            worker_receipt=None
            if processed:
                clock_row={"consumer_id":worker.expected_consumer_id,"received_at":processed[0],"eligible_at":processed[1]}
                worker_receipt={**clock_row,"digest":digest(clock_row)}
            result={"schema":self.output_proof_schema,"version":self.output_proof_version,**self.identity,
                    "clock_profile_digest":self.clock_profile["digest"],"journal_epoch":self.j.stream_epoch,
                    "resolved_at":self.j.clock(),"commit":commit,"request_canonical_json":text,"receipt":receipt,
                    "publication":publication,"consumer_receipt":service_receipt,"worker_receipt":worker_receipt}
            self.validate_payload(result);bounded_json(result,1024*1024);return result
        return self.j._transaction(read)

    def append_event(self,event,grant):
        if event.get("payload_type") in {"home-fact-batch-3","home-geometry-4",*ADAPTIVE_FAMILIES}:
            return super().append_event(event,grant)
        def apply():
            retry=self._event_retry(event,grant)
            if retry:return retry
            self.j._event(event);name=event["payload_type"]
            grant.require("event",event["site_id"],event["room_id"],payload_type=name,revision=event["revision"],producer_id=event["producer_id"])
            if name in ADAPTIVE_OUTPUTS or name in {"home-preference-write-1","home-preference-write-application-1","home-experience-feedback-1","home-experience-producer-revocation-1","home-feedback-authority-revocation-1"}:
                raise JournalError("typed_route_required","E output/command requires its atomic typed route")
            if name in {"home-experience-actor-binding-1","home-experience-classifier-policy-1","home-preference-write-authority-1"}:
                configured=([self.j.adaptive_feedback_config["classifier_policy"]] if name=="home-experience-classifier-policy-1" else self.j.adaptive_feedback_config["actor_bindings" if name=="home-experience-actor-binding-1" else "write_authorities"])
                if event["payload"] not in configured:raise JournalError("forbidden","issuer original differs from immutable trusted configuration",403)
            if name in {"home-fact-batch-3","home-geometry-4",*ADAPTIVE_FAMILIES}:
                # Reuse the E-native source capture and commissioning guards.
                return super().append_event(event,grant)
            return self.append(event,grant=grant)
        return self.j._transaction(apply)

    def declaration(self,payload,grant,*,family):
        if family not in REVISION_FIELDS or family=="home-experience-goal-1":raise JournalError("unsupported_schema","typed E declaration required")
        event=self._event_for(payload,grant)
        def apply():
            retry=self._event_retry(event,grant,payload)
            if retry:return retry
            self.validate_payload(payload)
            if payload["schema"]!=family:raise JournalError("unsupported_schema","native typed declaration family differs")
            self.j._event(event);grant.require("event",payload["site_id"],payload["room_id"],payload_type=family,revision=str(payload["version"])+".0")
            self._authorization(payload,grant,"write")
            if payload.get("actor_ref",grant.expected_actor_ref)!=grant.expected_actor_ref:raise JournalError("forbidden","declaration actor differs from grant",403)
            return self.append(event,grant=grant,raw_request=payload)
        return self.j._transaction(apply)

    def intent(self,payload,grant):
        event=self._event_for(payload,grant)
        def apply():
            retry=self._event_retry(event,grant,payload)
            if retry:return retry
            self.validate_payload(payload);self.j._event(event)
            grant.require("intent",payload["site_id"],payload["room_id"],payload_type="home-intent-event-3",revision="3.0")
            self._intent_subject(payload,grant,"write")
            if payload["actor_ref"]!=grant.expected_actor_ref:raise JournalError("forbidden","intent actor differs from grant",403)
            return self.append(event,grant=grant,raw_request=payload)
        return self.j._transaction(apply)

    def feedback(self,payload,grant):
        event=self._event_for(payload,grant)
        def apply():
            retry=self._event_retry(event,grant,payload)
            if retry:return retry
            self.validate_payload(payload);bounded_json(payload,4096);self.j._event(event)
            grant.require("feedback",payload["site_id"],payload["room_id"],payload_type=payload["schema"],revision="1.0")
            self._grant_current(grant)
            if payload["schema"]!="home-experience-feedback-1" or payload["actor_ref"]!=grant.expected_actor_ref:
                raise JournalError("forbidden","feedback actor is not its authenticated submitter",403)
            self._intent_subject(payload,grant,"write")
            original,binding=self._source_component(payload["actor_binding_ref"],grant,historical=False)
            if (binding not in self.j.adaptive_feedback_config["actor_bindings"] or original["payload_type"]!="home-experience-actor-binding-1"
                    or binding["principal_id"]!=grant.principal_id or binding["feedback_origin"]!={"producer_id":grant.producer_id,"producer_epoch":grant.producer_epoch}
                    or binding["actor_ref"]!=payload["actor_ref"] or binding["person_ref"]!=payload["person_ref"]
                    or max(original["committed_at"],binding["effective_at"],binding["available_at"])>payload["reported_at"]
                    or payload["reported_at"]>=binding["valid_until"]):
                raise JournalError("forbidden","binding cannot retroactively authenticate feedback",403)
            if not any(all(rule[k]==payload[k] for k in ("subject","beneficiary","target","association_generation"))
                       and rule["preference_set_id"]==payload["preference_refs"][0]["preference_set_id"] for rule in binding["allowed_bindings"]):
                raise JournalError("forbidden","feedback tuple is outside its immutable actor binding",403)
            gp,goal=self._source_component(payload["goal_ref"]["source_ref"],grant,historical=True)
            pref_ref=payload["preference_refs"][0];pp,pref=self._source_component(pref_ref["source_ref"],grant,historical=True)
            ep,exposure=self._source_component(payload["exposure_ref"],grant,historical=True)
            if (goal.get("schema")!="home-experience-goal-1" or goal["goal_id"]!=payload["goal_ref"]["goal_id"]
                    or goal["revision"]!=payload["goal_ref"]["revision"] or digest(goal)!=payload["goal_ref"]["digest"]
                    or pref.get("schema")!="home-preference-snapshot-2" or pref["preference_set_id"]!=pref_ref["preference_set_id"]
                    or pref["revision"]!=pref_ref["revision"] or digest(pref)!=pref_ref["digest"]
                    or exposure.get("schema")!="home-lighting-settled-exposure-component-1"
                    or exposure["goal_ref"]!=payload["goal_ref"] or exposure["preference_ref"]!=pref_ref
                    or exposure["person_ref"]!=payload["person_ref"] or exposure["target"]!=payload["target"]
                    or any(goal[k]!=payload[k] for k in ("subject","beneficiary","target","association_generation"))
                    or max(gp["committed_at"],pp["committed_at"],ep["committed_at"],exposure["available_at"])>payload["reported_at"]):
                raise JournalError("invalid_reference","feedback OLD basis differs from exact original goal/preference/exposure",409)
            return self.append(event,grant=grant,raw_request=payload)
        return self.j._transaction(apply)

    def _revocation(self,payload,grant,*,producer):
        event=self._event_for(payload,grant)
        def apply():
            retry=self._event_retry(event,grant,payload)
            if retry:return retry
            self.validate_payload(payload);self.j._event(event)
            grant.require("revoke",payload["site_id"],payload["room_id"],payload_type=payload["schema"],revision="1.0")
            if payload["actor_ref"]!=grant.expected_actor_ref or any(payload[k]!=self.j.clock() for k in ("issued_at","effective_at","available_at")):
                raise JournalError("forbidden","revocation requires configured issuer and immediate original clocks",403)
            self._grant_current(grant)
            authority=json.loads(self.j._meta("adaptive-authority"))
            if producer:
                row=next((x for x in authority["producers"] if x["producer_id"]==payload["target_producer_id"] and x["producer_epoch"]==payload["target_producer_epoch"]),None)
                if row is None:raise JournalError("invalid_authority","producer epoch is not configured",409)
                field="revocation_generation";flag="revoked";desired=payload["revoked"]
            elif payload["kind"]=="room_motion":
                if payload["source_id"] is not None:raise JournalError("invalid_authority","room revocation cannot select a source",409)
                row=authority["room"];field="motion_generation";flag="motion_allowed";desired=not payload["closed"]
            else:
                row=next((x for x in authority["sources"] if x["source_id"]==payload["source_id"]),None)
                if row is None or payload["source_id"] not in grant.source_ids:raise JournalError("forbidden","source revocation is outside configured authority",403)
                field="revocation_generation";flag="revoked";desired=payload["closed"]
            if payload["expected_generation"]!=row[field] or payload["generation"]!=row[field]+1:
                raise JournalError("authority_conflict","revocation CAS generation changed",409)
            receipt=self.append(event,grant=grant,raw_request=payload)
            row[field]=payload["generation"];row[flag]=desired
            self.j._set_meta("adaptive-authority",canonical(authority));self._storage_budget();return receipt
        return self.j._transaction(apply)

    def producer_revoke(self,payload,grant):
        if payload.get("schema")!="home-experience-producer-revocation-1":raise JournalError("unsupported_schema","producer revocation1 required")
        return self._revocation(payload,grant,producer=True)

    def authority_revoke(self,payload,grant):
        if payload.get("schema")!="home-feedback-authority-revocation-1":raise JournalError("unsupported_schema","feedback authority revocation1 required")
        return self._revocation(payload,grant,producer=False)

    def preference_write(self,payload,grant,*,request_canonical_json=None):
        request_text=canonical(payload) if request_canonical_json is None else request_canonical_json
        if not isinstance(request_text,str) or request_text!=canonical(payload):
            raise JournalError("noncanonical_write","E write requires exact canonical UTF8 bytes",400)
        bounded_json(payload,min(65536,self.j.limits.max_body_bytes))
        def apply():
            prior=self.origins.retry(self._auth(grant),payload.get("id"),payload)
            if prior:return self.lookup_origin(grant,payload["site_id"],payload["room_id"],event_id=payload["id"])
            self.validate_payload(payload);now=self.j.clock()
            grant.require("event",payload["site_id"],payload["room_id"],payload_type=payload["schema"],revision="1.0")
            self._authorization(payload,grant,"write")
            if payload["schema"]!="home-preference-write-1" or payload["producer_epoch"]!=grant.producer_epoch or payload["actor_ref"]!=grant.expected_actor_ref:
                raise JournalError("forbidden","write actor/origin differs from authenticated writer",403)
            if payload["issued_at"]>now or max(payload["not_before"],payload["available_at"])>now:
                raise JournalError("not_eligible","write is not currently eligible",409)
            self._grant_current(grant)
            issuer=self._service_issuer("home-preference-write-application-1")
            pp,predecessor=self._source_component(payload["expected_predecessor"]["source_ref"],grant,historical=True)
            if predecessor.get("schema")!="home-preference-snapshot-2" or any(predecessor[k]!=payload[k] for k in ("owner_id","subject","target","preference_set_id")) or predecessor["beneficiary"]!={"kind":"person","person_ref":payload["person_ref"]}:
                raise JournalError("invalid_reference","write predecessor tuple differs from command",409)
            if payload["expected_predecessor"]!={"preference_set_id":predecessor["preference_set_id"],"revision":predecessor["revision"],"digest":digest(predecessor),"source_ref":self._source_ref(pp)}:
                raise JournalError("invalid_reference","write predecessor is not an exact raw revision",409)
            cp,context=self._source_component(payload["context_ref"]["source_ref"],grant,historical=False)
            ap,authority=self._source_component(payload["authority_ref"],grant,historical=False)
            fp,frontier=self._source_component(payload["frontier_ref"],grant,historical=False)
            lp,policy=self._source_component(payload["policy_ref"],grant,historical=False)
            if (context.get("schema")!="home-experience-context-declaration-1" or authority.get("schema")!="home-preference-write-authority-1"
                    or frontier.get("schema")!="home-preference-write-frontier-1"
                    or policy.get("schema") not in {"home-preference-table-policy-1","home-preference-acknowledged-model-policy-1","home-preference-selector-memory-policy-1"}
                    or lp["payload_type"]!="home-preference-learning-policy-1" or payload["policy_ref"]["component"] is None):
                raise JournalError("invalid_reference","write requires exact context, authority, frontier and typed policy originals",409)
            selector=payload["policy_ref"]["component"]["selector"]
            role={"table_policy":"home-preference-table-policy-1","acknowledged_model_policy":"home-preference-acknowledged-model-policy-1","selector_memory_policy":"home-preference-selector-memory-policy-1"}
            selected_setting=((payload["learning_basis"]["setting_attribution_ref"]["component"] or {}).get("selector") if payload["learning_basis"] else None)
            expected=({"table_setting_attribution":"table_policy","model_setting_attribution":"acknowledged_model_policy"}.get(selected_setting) if payload["operation"]=="learn" else None)
            if (role.get(selector)!=policy["schema"] or expected is not None and selector!=expected
                    or payload["operation"]=="learn" and payload["learning_basis"] is not None and expected is None
                    or payload["operation"] in {"default","recall","human_undo"} and selector not in {"acknowledged_model_policy","selector_memory_policy"}
                    or any(policy["normalized"][k]!=lp["payload"][k] for k in ("effective_at","available_at","valid_until"))):
                raise JournalError("invalid_reference","selected typed policy has the wrong operation role or original clocks",409)
            for original in (cp,ap,fp,lp):
                if self._eligible(original)>now:raise JournalError("not_eligible","write dependency is future available",409)
            reason=None;admitted_context=None;admitted_authority=None
            store=self._revision_store();chain=revision_chain_key(predecessor)
            head=next((r["publication"] for r in reversed(store.snapshot()["rows"]) if revision_chain_key(r["publication"]["payload"])==chain),None)
            current_context=next((r["row"]["publication"] for r in store.materialize(now) if r["row"]["publication"]["payload_type"]=="home-experience-context-declaration-1"
                                  and all(r["row"]["publication"]["payload"][k]==context[k] for k in ("owner_id","subject","context_set_id"))),None)
            current_authority=self._write_family_head("home-preference-write-authority-1",payload,now)
            current_frontier=self._write_family_head("home-preference-write-frontier-1",payload,now)
            expected_context={"context_set_id":context["context_set_id"],"revision":context["revision"],"digest":digest(context),"source_ref":self._source_ref(cp)}
            if payload["deadline"]<=now:reason="command_expired"
            elif head!=pp or predecessor["status"]!="active" or predecessor["valid_until"]<=now:reason="predecessor_conflict"
            elif payload["unchanged_projection_digest"]!=digest(preference_unchanged_projection(predecessor)):reason="projection_mismatch"
            elif not predecessor["qualities"]["illuminance_band"]["min_lux"]<=payload["desired_lux"]<=predecessor["qualities"]["illuminance_band"]["max_lux"]:reason="desired_outside_band"
            elif current_context!=cp or payload["context_ref"]!=expected_context:reason="context_changed"
            elif context["status"]!="active" or context["valid_until"]<=now:reason="context_unavailable"
            elif any(context[k]!=payload[k] for k in ("owner_id","subject","target","preference_set_id")) or context["beneficiary"]!=predecessor["beneficiary"]:reason="context_changed"
            elif (current_authority!=ap or authority not in self.j.adaptive_feedback_config["write_authorities"]
                  or authority["status"]!="active" or authority["valid_until"]<=now or authority["rule_valid_until"]<=now
                  or authority["writer_principal_id"]!=grant.principal_id
                  or authority["writer_origin"]!={"producer_id":grant.producer_id,"producer_epoch":grant.producer_epoch}
                  or any(authority[k]!=payload[k] for k in ("actor_ref","person_ref","owner_id","subject","target","preference_set_id"))
                  or payload["operation"] not in authority["allowed_operations"]):reason="authority_revoked"
            elif (current_frontier!=fp or frontier["valid_until"]<=now or frontier["head_ref"]!=payload["expected_predecessor"] or frontier["head_projection_digest"]!=payload["unchanged_projection_digest"]
                  or frontier["context_ref"]!=payload["context_ref"] or frontier["authority_ref"]!=payload["authority_ref"]
                  or any(frontier[k]!=payload[k] for k in ("owner_id","subject","preference_set_id"))):reason="predecessor_conflict"
            elif payload["context_set_id"] is not None and (payload["context_set_id"]!=context["context_set_id"] or payload["condition_tags"]!=context["tags"]):reason="context_changed"
            elif not now<payload["rule_valid_until"]<=min(context["valid_until"],authority["valid_until"],authority["rule_valid_until"]):reason="memory_expired"
            else:reason=self._write_learning_reason(payload,grant,predecessor,context,policy,authority,now,policy_parent=lp)
            reservation=self.j.db.execute("SELECT event FROM receipts WHERE producer_id=? AND source_epoch=? AND json_extract(event,'$.payload_type')='home-preference-write-1' AND json_extract(event,'$.payload.reservation_id')=?",(grant.producer_id,grant.producer_epoch,payload["reservation_id"])).fetchone()
            if reservation:reason="reservation_conflict"
            event=self._event_for(payload,grant);command_receipt=self.append(event,grant=grant,raw_request=payload)
            self.j.db.execute("INSERT INTO feedback_write_originals VALUES(?,?,?,?,?,?)",(grant.principal_id,grant.producer_id,grant.producer_epoch,payload["id"],hashlib.sha256(request_text.encode("utf-8")).hexdigest(),zlib.compress(request_text.encode("utf-8"),9)))
            command=json.loads(self.j.db.execute("SELECT body FROM publications WHERE cursor=?",(command_receipt["cursor"],)).fetchone()[0])
            successor=None
            if reason is None:
                successor=copy.deepcopy(predecessor)
                successor.update(id="preference-write:"+digest([grant.producer_id,grant.producer_epoch,payload["id"]]),revision=predecessor["revision"]+1,
                                 expected_predecessor={"revision":predecessor["revision"],"digest":digest(predecessor)},issued_at=now,effective_at=now,available_at=now,
                                 valid_until=payload["rule_valid_until"],condition_tags=payload["condition_tags"],context_set_id=payload["context_set_id"],rule_valid_until=payload["rule_valid_until"],
                                 write_origin=authenticated_origin(self._auth(grant),payload["id"]))
                successor["qualities"]["illuminance_band"]["desired_lux"]=payload["desired_lux"]
                if preference_unchanged_projection(successor)!=preference_unchanged_projection(predecessor):raise JournalError("projection_mismatch","successor changed a preserved field",409)
                self.validate_payload(successor)
                successor_receipt=self.append(self._event_for(successor,grant),grant=grant)
                successor=json.loads(self.j.db.execute("SELECT body FROM publications WHERE cursor=?",(successor_receipt["cursor"],)).fetchone()[0])
                admitted_context=payload["context_ref"];admitted_authority=payload["authority_ref"]
            app={"schema":"home-preference-write-application-1","version":1,"id":"write-result:"+digest(authenticated_origin(self._auth(grant),payload["id"])),
                 **self.identity,"clock_profile_digest":self.clock_profile["digest"],"origin":authenticated_origin(self._auth(grant),payload["id"]),
                 "raw_request_digest":digest(payload),"command_ref":self._source_ref(command),"expected_predecessor":payload["expected_predecessor"],
                 "successor_ref":{"preference_set_id":successor["payload"]["preference_set_id"],"revision":successor["payload"]["revision"],"digest":successor["payload_digest"],"source_ref":self._source_ref(successor)} if successor else None,"admitted_context_ref":admitted_context,"admitted_authority_ref":admitted_authority,
                 "status":"applied" if reason is None else "expired" if reason=="command_expired" else "rejected","reason":reason or "exact_predecessor_admitted",
                 "decided_at":now,"available_at":now}
            self.validate_payload(app);app_receipt=self.append(self._event_for(app,issuer),grant=issuer)
            self.j.db.execute("INSERT INTO feedback_write_results VALUES(?,?,?,?,?)",(grant.producer_id,grant.producer_epoch,payload["id"],app_receipt["cursor"],successor["cursor"] if successor else None))
            self._storage_budget();return self.lookup_origin(grant,payload["site_id"],payload["room_id"],event_id=payload["id"])
        return self.j._transaction(apply)

    def _service_issuer(self,family,*,current=True):
        candidates=[g for g in self.j.grants if "event" in g.operations and family in g.payload_types
                    and (self.identity["site_id"],self.identity["room_id"]) in g.scopes]
        unique={(g.principal_id,g.producer_id,g.producer_epoch):g for g in candidates}
        if len(unique)!=1:raise JournalError("issuer_unavailable","exact dedicated service issuer is missing or ambiguous",409)
        issuer=next(iter(unique.values()))
        if current:self._grant_current(issuer)
        return issuer

    def _write_family_head(self,family,payload,t):
        heads=[]
        for row in self.j.db.execute("SELECT body FROM publications WHERE payload_type=?",(family,)):
            p=json.loads(row[0]);body=p["payload"]
            if (_same_scope(body,self.identity) and all(body[k]==payload[k] for k in ("owner_id","subject","preference_set_id"))
                    and self._eligible(p)<=t):
                heads.append(p)
        # Choose before TTL/status/epoch disposition. Those predicates can
        # refuse the selected head, never fall back to an older permission.
        return max(heads,key=lambda p:(p["payload"].get("sampled_at",p["payload"]["effective_at"]),p["payload"]["available_at"],p["committed_at"],p["cursor"]),default=None)

    def _write_learning_reason(self,payload,grant,predecessor,context,policy,authority,now,*,policy_parent):
        # Candidate bytes are an authenticated learner commitment. They are
        # verified by the source-pinned learner/replay, never inferred here.
        limits=policy["normalized"];parent=policy_parent["payload"]
        lifetime=parent["policy_components"]["selector"]["normalized"] if limits["kind"]=="table_policy" else limits
        first=self.j.db.execute("SELECT received_at,eligible_at FROM feedback_consumer_receipts WHERE principal_id=? AND consumer_id=? AND cursor=?",
                                (grant.principal_id,grant.expected_consumer_id,policy_parent["cursor"])).fetchone()
        if first is None or first[1]!=self._eligible(policy_parent,first[0]) or first[1]>now:return "learning_basis_unavailable"
        if limits.get("valid_until",0)<=now or self._eligible(policy_parent,first[0])>now:return "memory_expired"
        if payload["deadline"]>min(120,payload["available_at"]+limits["proposal_ttl_s"],limits["valid_until"]):
            raise JournalError("invalid_reference","write submission deadline exceeds its selected original policy",409)
        reservations=lifetime.get("reservations",[])
        if payload["operation"]=="human_undo":
            if payload["reservation_id"]!=limits.get("undo_reservation_id") or payload["undo_effect_ref"] is None:return "reservation_conflict"
            _,effect=self._source_component(payload["undo_effect_ref"],grant,historical=True)
            if effect.get("schema")!="home-preference-write-application-1" or effect["status"]!="applied" or effect["successor_ref"] is None:return "undo_effect_unavailable"
            _,effect_pref=self._source_component(effect["successor_ref"]["source_ref"],grant,historical=True)
            if effect_pref!=predecessor:return "undo_effect_changed"
            _,effect_command=self._source_component(effect["command_ref"],grant,historical=True)
            if effect_command["policy_ref"]["publication_ref"]!=payload["policy_ref"]["publication_ref"]:return "undo_effect_changed"
            # A selected undo reservation must be the same arm's admitted
            # learning ancestry, independently of a candidate's opaque ID.
            ancestor=predecessor;learn_role=None
            for _ in range(16):
                origin=ancestor.get("write_origin")
                if origin is None:break
                row=self.j.db.execute("SELECT event FROM receipts WHERE producer_id=? AND source_epoch=? AND event_id=?",(origin["producer_id"],origin["producer_epoch"],origin["event_id"])).fetchone()
                if row is None:return "undo_effect_unavailable"
                prior=json.loads(row[0])["payload"]
                if prior.get("schema")!="home-preference-write-1":return "undo_effect_unavailable"
                if prior["operation"]=="learn":
                    learn_role=prior["policy_ref"]["component"]["selector"];break
                _,ancestor=self._source_component(prior["expected_predecessor"]["source_ref"],grant,historical=True)
            required="acknowledged_model_policy" if learn_role=="acknowledged_model_policy" else "selector_memory_policy" if learn_role=="table_policy" else effect_command["policy_ref"]["component"]["selector"]
            if payload["policy_ref"]["component"]["selector"]!=required:return "undo_effect_changed"
            _,before=self._source_component(effect["expected_predecessor"]["source_ref"],grant,historical=True)
            return None if payload["desired_lux"]==before["qualities"]["illuminance_band"]["desired_lux"] else "undo_effect_changed"
        if not any(r["id"]==payload["reservation_id"] and r["operation"]==payload["operation"] and r["tags"]==context["tags"] for r in reservations):return "reservation_conflict"
        if payload["desired_lux"]==predecessor["qualities"]["illuminance_band"]["desired_lux"] and payload["condition_tags"]==predecessor["condition_tags"]:return "no_effect"
        delta=payload["desired_lux"]-predecessor["qualities"]["illuminance_band"]["desired_lux"]
        if abs(delta)>min(25,limits.get("max_delta_lux",0)):return "administrative_step_exceeded"
        if payload["operation"]!="learn":return None
        basis=payload["learning_basis"]
        if basis is None:return "learning_basis_unavailable"
        original={}
        for key,ref in basis.items():
            source=ref.get("source_ref",ref)
            p,body=self._source_component(source,grant,historical=False)
            delivered=self.j.db.execute("SELECT received_at,eligible_at FROM feedback_consumer_receipts WHERE principal_id=? AND consumer_id=? AND cursor=?",(grant.principal_id,grant.expected_consumer_id,p["cursor"])).fetchone()
            if not delivered or delivered[1]>now:return "learning_basis_unavailable"
            original[key]=(p,body)
        old=original["old_preference_ref"][1];feedback=original["feedback_ref"][1];app=original["application_ref"][1]
        exposure=original["exposure_ref"][1];classification=original["classification_ref"][1]
        if not self._feedback_learning_origin_supported(original["feedback_ref"][0],feedback,app,authority,grant):
            return "learning_basis_unavailable"
        setting=original["setting_attribution_ref"][1].get("normalized",{})
        delivery=original["delivery_attribution_ref"][1].get("normalized",{})
        normalized=self._learning_normalized_basis(exposure,old,basis["old_preference_ref"]["source_ref"],grant)
        expected_setting=normalized["table_setting_digest"] if setting.get("kind")=="table_setting_attribution" else normalized["model_setting_digest"]
        certificate=original["delivery_attribution_ref"][0]["payload"].get("two_sided_delivery",{})
        if (certificate.get("status")!="qualified" or certificate.get("whole_affine_range_proved") is not True
                or certificate.get("decrease_case",{}).get("delta_lux")!=-25
                or certificate.get("increase_case",{}).get("delta_lux")!=25
                or certificate.get("common_basis",{}).get("exposure_ref")!=basis["exposure_ref"]
                or certificate.get("common_basis",{}).get("preference_ref")!=basis["old_preference_ref"]
                or original["delivery_attribution_ref"][0]["cursor"]>=original["feedback_ref"][0]["cursor"]
                or original["delivery_attribution_ref"][0]["committed_at"]>feedback["reported_at"]):
            return "learning_basis_unavailable"
        for direction in ("decrease_case","increase_case"):
            case=certificate[direction]
            attribution=case.get("delivery_attribution",{}).get("normalized",{})
            if case.get("status")!="modeled_supported" or not attribution.get("feasible_variation") or attribution.get("constraint_limited") or attribution.get("ambient_dominates"):
                return "learning_basis_unavailable"
        if (old!=predecessor or app.get("schema")!="home-experience-feedback-application-1" or app["status"]!="attributed"
                or app["feedback_ref"]!=basis["feedback_ref"] or feedback.get("schema")!="home-experience-feedback-1"
                or feedback["exposure_ref"]!=basis["exposure_ref"] or exposure.get("status")!="qualified"
                or classification.get("schema")!="home-experience-classifier-disposition-1"
                or classification["normalized"]["feedback_source_ref"]!=basis["feedback_ref"]
                or classification["normalized"]["status"]!="inferred_preference_candidate"
                or setting.get("status")!="qualified" or delivery.get("status")!="qualified"
                or setting.get("exposure_ref")!=basis["exposure_ref"] or delivery.get("exposure_ref")!=basis["exposure_ref"]
                or setting.get("exposure_body_digest")!=normalized["exposure_digest"] or delivery.get("exposure_body_digest")!=normalized["exposure_digest"]
                or setting.get("setting_digest")!=expected_setting or delivery.get("setting_digest")!=normalized["model_setting_digest"]
                or setting.get("normalized_row_body_digest")!=normalized["preference_digest"]
                or setting.get("control_refs")!=normalized["control_refs"] or delivery.get("control_refs")!=normalized["control_refs"]
                or any(x.get("from")!=exposure["from_t"] or x.get("until")!=exposure["until"] for x in (setting,delivery))
                or setting.get("kind") not in {"table_setting_attribution","model_setting_attribution"}
                or setting.get("kind")=="model_setting_attribution" and setting.get("immutable_projection_digest")!=normalized["projection_digest"]
                or setting.get("preference_source")!=basis["old_preference_ref"]["source_ref"]
                or setting.get("preference_ref")!={"set_id":predecessor["preference_set_id"],"revision":predecessor["revision"],"digest":digest(predecessor)}
                or not delivery.get("feasible_variation") or delivery.get("constraint_limited") or delivery.get("ambient_dominates")):
            return "learning_basis_unavailable"
        classified=classification["normalized"]["as_of"]
        disposition=classification["normalized"]
        if (not self._classifier_policy_basis_supported(disposition,grant,classified)
                or not self._classifier_limits_supported(feedback,exposure,classified)):
            return "learning_basis_unavailable"
        proposal=disposition.get("proposal") or {}
        if (disposition["feedback_digest"]!=digest(feedback) or disposition["feedback_identity"]!={"id":feedback["id"],"actor_ref":feedback["actor_ref"],"origin":app["origin"]}
                or disposition["dimension"]!="illumination_lux" or proposal.get("operation")!="directional"
                or proposal.get("expected_predecessor")!={"set_id":old["preference_set_id"],"revision":old["revision"],"digest":digest(old)}
                or proposal.get("direction")!=("higher" if feedback["kind"]=="too_dim" else "lower")
                or not max(feedback["reported_at"],feedback["available_at"],feedback["effective_at"],app["decided_at"])<=classified<feedback["valid_until"]
                or any(original[k][0]["committed_at"]>classified for k in ("feedback_ref","application_ref","exposure_ref","setting_attribution_ref","delivery_attribution_ref"))
                or classified>now or classified-feedback["reported_at"]>limits.get("max_label_age_s",0)
                or classified-exposure["until"]>limits.get("max_exposure_age_s",0)):
            return "learning_basis_unavailable"
        if (feedback["kind"]=="too_dim" and delta<=0) or (feedback["kind"]=="too_bright" and delta>=0):return "learning_direction_conflict"
        if abs(delta)>limits.get("step_lux",0):return "learning_step_exceeded"
        # An admitted ordinal has an authored belief lifetime, independent of
        # its short ingress TTL. A later write cannot restart that lifetime.
        horizon=min(120,feedback["reported_at"]+lifetime.get("belief_duration_s",0),limits.get("valid_until",0),lifetime["valid_until"],
                    old["rule_valid_until"],authority["rule_valid_until"])
        if now>=horizon or payload["rule_valid_until"]>horizon:
            return "memory_expired"
        return None

    def _feedback_learning_origin_supported(self,publication,feedback,application,authority,grant):
        """A retained label belongs to this authority's exact authenticated origin."""
        origin={"producer_id":publication["producer_id"],"producer_epoch":publication["producer_epoch"]}
        if (authority["feedback_origin"]!=origin or application.get("origin")!={**origin,"event_id":feedback["id"]}
                or any(feedback[k]!=authority[k] for k in ("actor_ref","person_ref","subject","target"))):return False
        parent,binding=self._source_component(feedback["actor_binding_ref"],grant,historical=False)
        return (parent["payload_type"]=="home-experience-actor-binding-1" and binding in self.j.adaptive_feedback_config["actor_bindings"]
                and binding["feedback_origin"]==origin and binding["actor_ref"]==feedback["actor_ref"] and binding["person_ref"]==feedback["person_ref"]
                and max(parent["committed_at"],binding["effective_at"],binding["available_at"])<=feedback["reported_at"]<binding["valid_until"]
                and any(all(rule[k]==feedback[k] for k in ("subject","beneficiary","target","association_generation"))
                        and all(rule[k]==authority[k] for k in ("owner_id","preference_set_id","context_set_id"))
                        for rule in binding["allowed_bindings"]))

    def _classifier_limits_supported(self,feedback,exposure,classified):
        """Preserve the authored classifier limits at its original decision."""
        policy=self.j.adaptive_feedback_config["classifier_policy"]
        return (feedback["dimension"] in policy["allowed_dimensions"]
                and feedback["available_at"]<=classified<=feedback["available_at"]+policy["max_feedback_age_s"]
                and exposure["from_t"]<exposure["until"]<=feedback["reported_at"]
                and exposure["until"]-exposure["from_t"]<=policy["max_exposure_span_s"]
                and feedback["reported_at"]-exposure["until"]<=policy["max_exposure_age_s"]
                and (exposure["assessment_kind"]!="modeled" or policy["allow_modeled_candidates"]))

    def _classifier_policy_basis_supported(self,disposition,grant,classified):
        policy=self.j.adaptive_feedback_config["classifier_policy"]
        normalized={"kind":"classifier_policy","scope":self.identity,"id":policy["id"],"version":policy["policy_version"],
                    **{k:policy[k] for k in ("allowed_dimensions","allowed_context_keys","illumination_lux_bounds",
                                           "max_feedback_age_s","max_exposure_age_s","max_exposure_span_s",
                                           "allow_modeled_candidates","effective_at","available_at","valid_until")}}
        if (disposition["policy_digest"]!=digest(policy) or disposition["policy_normalized_digest"]!=digest(normalized)
                or not max(policy["effective_at"],policy["available_at"])<=classified<policy["valid_until"]):
            return False
        candidates=[row for row in disposition["basis_refs"] if row["source"]["payload_type"]=="home-experience-classifier-policy-1"]
        if len(candidates)!=1:return False
        basis=candidates[0]
        if basis["source"]["component"] is not None:return False
        original,body=self._source_component(basis["source"],grant,historical=False)
        first=self.j.db.execute("SELECT received_at,eligible_at FROM feedback_consumer_receipts WHERE principal_id=? AND consumer_id=? AND cursor=?",
                                (grant.principal_id,grant.expected_consumer_id,original["cursor"])).fetchone()
        return (body==policy and first is not None and first[1]<=classified
                and basis=={"source":self._source_ref(original),"body_digest":digest(normalized),
                            "clock_profile_digest":self.clock_profile["digest"],"committed_at":original["committed_at"],"received_at":first[0]})

    def _learning_normalized_basis(self,exposure,preference,preference_source,grant):
        """Derive the pinned raw-to-private projections; no learner math."""
        controls=[]
        for ref in exposure["control_refs"]:
            p,_=self._source_component(ref,grant,historical=False)
            matches=[x for x in p["payload"].get("interval_exposures",[]) if x["person_ref"]==exposure["person_ref"] and x["target"]==exposure["target"]]
            if len(matches)!=1:raise JournalError("invalid_reference","normalized exposure requires one exact target child per original control",409)
            child=matches[0]
            controls.append(self._source_ref(p,{"selector":"execution_feedback","id":child["id"],"schema":child["schema"],"version":child["version"],"digest":digest(child)}))
        body={"kind":"exposure","scope":self.identity,"id":exposure["id"],"person_ref":exposure["person_ref"],"target":exposure["target"],
              "goal_ref":{k:exposure["goal_ref"][k] for k in ("goal_id","revision","digest")},
              "preference_ref":{"set_id":exposure["preference_ref"]["preference_set_id"],"revision":exposure["preference_ref"]["revision"],"digest":exposure["preference_ref"]["digest"]},
              "from":exposure["from_t"],"until":exposure["until"],"status":"settled" if exposure["status"]=="qualified" else "unavailable",
              "assessment_kind":exposure["assessment_kind"],"requested_lux":exposure["requested_lux"],"assessed_lux":exposure["assessed_lux"],"control_refs":controls,"available_at":exposure["available_at"]}
        row={"kind":"preference_snapshot","scope":self.identity,"id":preference["id"],"set_id":preference["preference_set_id"],"revision":preference["revision"],
             "preference_subject":preference["subject"],**{k:preference[k] for k in ("effective_at","available_at","valid_until")}}
        band=preference["qualities"]["illuminance_band"];projection=digest(preference_unchanged_projection(preference))
        setting={"dimension":"illumination_lux","min":band["min_lux"],"desired":band["desired_lux"],"max":band["max_lux"],"owner_id":preference["owner_id"],
                 "person_ref":preference["beneficiary"]["person_ref"],"target":preference["target"],"original_source":preference_source,"normalized_row_body_digest":digest(row),
                 "immutable_projection_digest":projection,"condition_tags":preference["condition_tags"],"rule_valid_until":preference["rule_valid_until"]}
        table={k:setting[k] for k in ("dimension","min","desired","max","owner_id","original_source","normalized_row_body_digest")}
        return {"exposure_digest":digest(body),"preference_digest":digest(row),"model_setting_digest":digest(setting),"table_setting_digest":digest(table),
                "control_refs":controls,"projection_digest":projection}

    def _intent_application_outputs(self,commit,old,outcomes):
        previous={r["intent_id"] for r in old["pipeline"]["intent_outcomes"]} if old and old["pipeline"] else set()
        expected=[]
        for key,outcome in outcomes.items():
            if key in previous:continue
            expected.append(self._intent_application_body(commit,outcome))
        actual=[o for o in commit["outputs"] if o["schema"]=="home-intent-application-result-3"]
        if sorted(actual,key=canonical)!=sorted(expected,key=canonical):
            raise JournalError("invalid_intent_result","every first terminal intent requires its exact atomic application original",409)

    def _intent_application_body(self,commit,outcome):
        matches=[]
        for row in self.j.db.execute("SELECT body FROM publications WHERE payload_type='home-intent-event-3'"):
            p=json.loads(row[0])
            if p["id"]==outcome["intent_id"] and p["payload_digest"]==outcome["intent_digest"] and _same_scope(p["payload"],commit):matches.append(p)
        if len(matches)!=1:raise JournalError("invalid_intent_result","first terminal needs one exact scoped original instruction",409)
        p=matches[0];origin={"producer_id":p["producer_id"],"producer_epoch":p["producer_epoch"],"event_id":p["id"]}
        return {"schema":"home-intent-application-result-3","version":3,"id":"intent-application:"+digest({"scope":self.identity,"origin":origin}),
                **self.identity,"clock_profile_digest":self.clock_profile["digest"],**outcome,"available_at":outcome["decided_at"],"truth_role":"runtime_application_result"}

    def lookup_origin(self,grant,site,room,*,event_id):
        self._scope_check(site,room)
        grant.require("event",site,room,payload_type="home-preference-write-1",revision="1.0")
        saved=self.origins.lookup(self._auth(grant),event_id)
        if saved is None:raise JournalError("not_found","write origin is unresolved",404)
        command_row=self.j.db.execute("SELECT body FROM publications WHERE cursor=?",(saved["first_receipt"]["cursor"],)).fetchone()
        command=json.loads(command_row[0]) if command_row else None
        if command is None or command["payload_type"]!="home-preference-write-1":raise JournalError("not_found","origin is not a preference write",404)
        self._authorization(command["payload"],grant,"write")
        result_row=self.j.db.execute("SELECT application_cursor,successor_cursor FROM feedback_write_results WHERE producer_id=? AND producer_epoch=? AND event_id=?",(grant.producer_id,grant.producer_epoch,event_id)).fetchone()
        def original(cursor):
            found=self.j.db.execute("SELECT body FROM publications WHERE cursor=?",(cursor,)).fetchone() if cursor is not None else None
            return json.loads(found[0]) if found else None
        application=original(result_row[0]) if result_row else None;successor=original(result_row[1]) if result_row else None
        result={"schema":"home-context-origin-lookup-1","version":1,**self.identity,"clock_profile_digest":self.clock_profile["digest"],"journal_epoch":self.j.stream_epoch,
                "resolved_at":self.j.clock(),"origin":saved["origin"],"request_digest":saved["request_digest"],"command":command,"application":application,"successor":successor,
                "first_receipt":{"origin":saved["origin"],"request_digest":saved["request_digest"],"publication_ref":_publication_ref(command),"committed_at":saved["first_receipt"]["committed_at"]}}
        self.validate_payload(result);return result

    def lookup_write_origin_proof(self,grant,site,room,*,event_id):
        def read():
            lookup=self.lookup_origin(grant,site,room,event_id=event_id)
            row=self.j.db.execute("SELECT request_digest,request_blob FROM feedback_write_originals WHERE principal_id=? AND producer_id=? AND producer_epoch=? AND event_id=?",(grant.principal_id,grant.producer_id,grant.producer_epoch,event_id)).fetchone()
            if not row:raise JournalError("not_found","exact accepted write wire is unavailable",404)
            try:
                decoder=zlib.decompressobj();raw=decoder.decompress(row["request_blob"],min(65536,self.j.limits.max_body_bytes)+1)
                if len(raw)>min(65536,self.j.limits.max_body_bytes) or decoder.unconsumed_tail or not decoder.eof or decoder.unused_data:
                    raise ValueError("bounded complete zlib stream required")
                text=raw.decode("utf-8");payload=json.loads(text)
                if (canonical(payload)!=text or hashlib.sha256(raw).hexdigest()!=row["request_digest"]
                        or row["request_digest"]!=lookup["request_digest"] or payload!=lookup["command"]["payload"]):
                    raise ValueError("exact accepted write wire changed")
            except (ValueError,UnicodeError,zlib.error,RecursionError,JournalError):
                raise JournalError("corrupt_original","write wire failed exact bounded recovery",409) from None
            result={"schema":"home-context-preference-write-origin-proof-1","version":1,**self.identity,
                    "clock_profile_digest":self.clock_profile["digest"],"journal_epoch":self.j.stream_epoch,"resolved_at":lookup["resolved_at"],
                    "request_canonical_json":text,"origin_lookup":lookup}
            self.validate_payload(result);return result
        return self.j._transaction(read)

    def _preference_snapshot_refs(self,t,items,grant):
        heads=self._revision_store().materialize(t)
        contexts=[x for x in heads if x["row"]["publication"]["payload_type"]=="home-experience-context-declaration-1"]
        applicable=[];frontiers=[];context_refs=[]
        for h in heads:
            p=h["row"]["publication"];body=p["payload"]
            if p["payload_type"] not in grant.payload_types or not self._read_allowed(p,grant):continue
            if body["schema"]=="home-experience-context-declaration-1":context_refs.append(self._source_ref(p))
            if body["schema"]!="home-preference-snapshot-2":continue
            frontiers.append(self._source_ref(p))
            if h["disposition"]!="active" or body["rule_valid_until"]<=t:continue
            tags=body["condition_tags"]
            matching=[x for x in contexts if x["row"]["publication"]["payload"]["context_set_id"]==body["context_set_id"]
                      and all(x["row"]["publication"]["payload"][k]==body[k] for k in ("owner_id","subject","preference_set_id"))]
            if tags is not None and (len(matching)!=1 or matching[0]["disposition"]!="active" or not set(tags)<=set(matching[0]["row"]["publication"]["payload"]["tags"])):continue
            applicable.append(self._source_ref(p))
        if any(len(x)>8 for x in (applicable,frontiers,context_refs)):raise JournalError("snapshot_budget","E head references exceed reservation",507)
        return {"applicable_preference_refs":applicable,"global_preference_frontier_refs":frontiers,"context_head_refs":context_refs}

    def _snapshot_write_heads(self,grant,site,room,t,items):
        """Expose exact selected chain heads, never an older usable fallback."""
        candidates={}
        for row in self._visible(grant,site,room,0,int(self.j._meta("cursor"))):
            publication=json.loads(row["body"]);body=publication["payload"]
            if body["schema"] not in {"home-preference-write-authority-1","home-preference-write-frontier-1"} or self._eligible(publication)>t:
                continue
            key=canonical([body["schema"],body["owner_id"],body["subject"],body["preference_set_id"]])
            candidates[key]=body
        if len(candidates)>8:raise JournalError("snapshot_budget","write-head witness exceeds eight selected heads",507)
        result=[];authority=self.authority(site,room)
        for key,body in sorted(candidates.items(),key=lambda item:_utf16_sort_key(item[0])):
            publication=self._write_family_head(body["schema"],body,t)
            if publication is None or not self._read_allowed(publication,grant):continue
            selected=publication["payload"]
            publisher=next((x for x in authority["producers"] if x["producer_id"]==publication["producer_id"] and x["producer_epoch"]==publication["producer_epoch"]),None)
            disposition=("unavailable" if publisher is None or publisher["revoked"] else "revoked" if selected.get("status")=="revoked"
                         else "expired" if selected["valid_until"]<=t else "active")
            items[publication["cursor"]]=_normalized(publication)
            result.append({"payload_type":selected["schema"],"owner_id":selected["owner_id"],"subject":selected["subject"],
                           "preference_set_id":selected["preference_set_id"],"source_ref":self._source_ref(publication),
                           "eligible_at":self._eligible(publication),"valid_until":selected["valid_until"],"disposition":disposition})
        return result

    def _installed_frontier(self,commit,grant,old):
        cp=commit["checkpoint"];cut=commit["input_cut"]
        ack=cp["acknowledged_installed"]
        if ack is not None:
            p,body=self._source_component(ack["source_ref"],grant,through=cut["input_cursor"],historical=True)
            f=ack["frontier"]
            row=self.j.db.execute("SELECT receipt FROM receipts WHERE producer_id=? AND source_epoch=? AND event_id=?",(grant.producer_id,grant.producer_epoch,"worker-commit/"+f["commit_id"])).fetchone()
            receipt=json.loads(row[0]) if row else None
            delivered=self.j.db.execute("SELECT served_at FROM dynamic_deliveries WHERE principal_id=? AND consumer_id=? AND cursor=?",(grant.principal_id,grant.expected_consumer_id,p["cursor"])).fetchone()
            if (p["producer_id"]!=grant.producer_id or p["producer_epoch"]!=grant.producer_epoch or p["payload_type"]!=f["payload_type"]
                    or p["payload_digest"]!=f["payload_digest"] or body["installed_state"]["control_seq"]!=f["control_seq"]
                    or not receipt or p["cursor"] not in {r["cursor"] for r in receipt["receipts"]}
                    or not delivered or not max(delivered[0],p["committed_at"])<=ack["received_at"]<=self.j.clock()
                    or ack["eligible_at"]!=self._eligible(p,ack["received_at"]) or ack["eligible_at"]>cut["as_of_t"]):
                raise JournalError("invalid_reference","installed acknowledgement is not its exact later delivered worker original",409)
            if old and old["acknowledged_installed"] and old["acknowledged_installed"]["source_ref"]==ack["source_ref"] and old["acknowledged_installed"]!=ack:
                raise JournalError("invalid_reference","installed original receipt cannot refresh",409)
        output=next((x for x in commit["outputs"] if x["schema"] in {"home-lighting-control-decision-3","home-lighting-installed-boundary-1"}),None)
        expected=({"commit_id":commit["id"],"control_seq":cp["control_seq"],"payload_type":output["schema"],"payload_digest":digest(output)} if output else old["pending_installed"] if old else None)
        if cp["pending_installed"]!=expected:raise JournalError("invalid_reference","installed frontier differs from atomic full output",409)
        if output is None:return
        state=output["installed_state"];pipeline=cp["pipeline"]
        expected_state={"t":output["t"],"control_seq":cp["control_seq"],"registry_digest":self.j.adaptive_feedback_config["config"]["registry"]["digest"],
                        "clock_profile_digest":self.clock_profile["digest"],"commands":pipeline["commands"] if pipeline else [],"fixtures":pipeline["fixtures"] if pipeline else []}
        expected_state["digest"]=digest(expected_state)
        if state!=expected_state:raise JournalError("invalid_reference","installed mechanics are not exact accepted POST checkpoint state",409)
        if output["schema"]=="home-lighting-installed-boundary-1":
            if cp["semantic_seq"]!=0 or cp["control_seq"]!=0 or output["t"]!=0 or output["input_cut_digest"]!=cut["digest"]:
                raise JournalError("invalid_reference","installed genesis is exclusively semantic0",409)
        else:
            if ack is None:raise JournalError("invalid_reference","control interval requires its actually acknowledged starting original",409)
            p,prior=self._source_component(ack["source_ref"],grant,through=cut["input_cursor"],historical=True)
            proof=output["interval_proof"]
            if (proof["start_original_ref"]!=_publication_ref(p) or proof["start_installed_digest"]!=prior["installed_state"]["digest"]
                    or prior["installed_state"]["t"]!=proof["from_t"] or prior["installed_state"]["control_seq"]!=cp["control_seq"]-1
                    or proof["execution_sample_digest"]!=digest(output["execution"]["samples"])):
                raise JournalError("invalid_reference","past interval changed its exact previous installed original",409)

    def _current_policy_original(self,commit,grant):
        """A policy draft or ACK cannot grant even an unqualified control."""
        cp=commit["checkpoint"];policy=cp["pipeline"]["policy"]
        rows=[row for row in cp["committed_output_inputs"] if row["publication"]["payload_type"]=="home-lighting-policy-decision-3"
              and row["publication"]["payload"]==policy]
        if len(rows)!=1:
            raise JournalError("invalid_reference","control requires its genuinely delivered current policy",409)
        item=rows[0];publication=item["publication"];reference=self._source_ref(publication)
        p,body=self._received_original(reference,commit,grant,commit["input_cut"]["as_of_t"])
        if (p!=publication or body!=policy or p["producer_id"]!=grant.producer_id or p["producer_epoch"]!=grant.producer_epoch
                or policy["semantic_seq"]!=cp["semantic_seq"] or item["received_at"]>cp["last_t"]):
            raise JournalError("invalid_reference","current policy changed its own original identity or FIRST",409)
        self._admitted_reference_output(p,grant)

    def _current_revision_head(self,rows,body,at):
        """Choose the causal scoped chain head before status/TTL/revocation."""
        chain=revision_chain_key(body)
        candidates=[row for row in rows if _same_scope(row['publication']['payload'],self.identity)
                    and revision_chain_key(row['publication']['payload'])==chain
                    and self._eligible(row['publication'],row['received_at'])<=at]
        if not candidates:return None
        revision=max(row['publication']['payload']['revision'] for row in candidates)
        heads=[row for row in candidates if row['publication']['payload']['revision']==revision]
        if len({row['publication']['payload_digest'] for row in heads})!=1:return None
        return min(heads,key=lambda row:row['publication']['cursor'])['publication']

    def _current_request_heads(self,request,commit,grant):
        """A sealed request proves old bodies, not current demand permission.

        Only current targets/new installation use this guard. OLD qualification
        and interval proof resolution remains historical-original validation.
        """
        at=commit['input_cut']['as_of_t']
        rows=self._current_revision_rows(commit,grant,('home-experience-goal-1','home-preference-snapshot-2','home-experience-context-declaration-1'))
        for reference,family in [(request['goal_ref'],'home-experience-goal-1'),
                                 *[(ref,'home-preference-snapshot-2') for ref in request['preference_refs']]]:
            body=self._component(reference,commit,grant,historical=True)
            originals=[row['publication'] for row in rows if row['publication']['payload_type']==family
                       and row['publication']['id']==reference['component_id']
                       and row['publication']['payload_digest']==reference['component_digest']
                       and row['publication']['payload']==body
                       and self._eligible(row['publication'],row['received_at'])<=at]
            parent=reference['publication_ref']
            if parent is None:
                # The semantic draft ref remains null in the sealed policy;
                # only its genuinely delivered own original can support control.
                originals=[p for p in originals if family=='home-experience-goal-1'
                           and p['producer_id']==grant.producer_id and p['producer_epoch']==grant.producer_epoch
                           and body['owner_id']==grant.producer_id]
            else:
                originals=[p for p in originals if all(p[key]==parent[key] for key in ('journal_epoch','cursor','payload_digest'))]
            head=self._current_revision_head(rows,body,at) if body.get('schema')==family else None
            if (not originals or head is None or head['payload_digest']!=reference['component_digest']
                    or not self._producer_allowed(head['producer_id'],head['producer_epoch'])
                    or head['payload']['status']!='active' or head['payload']['valid_until']<=at):
                raise JournalError('invalid_reference','request does not name an active current scoped goal/preference head',409)
            if family=='home-preference-snapshot-2':
                preference=head['payload']
                if preference['rule_valid_until']<=at:
                    raise JournalError('invalid_reference','request preference current head rule expired',409)
                if preference['condition_tags'] is not None:
                    context=self._current_revision_head(rows,{**preference,'schema':'home-experience-context-declaration-1'},at)
                    c=context['payload'] if context else None
                    policy=commit['checkpoint']['pipeline']['policy']
                    anchored=context is not None and any(ref['component_id']==context['id']
                        and ref['component_digest']==context['payload_digest'] and ref['publication_ref'] is not None
                        and all(ref['publication_ref'][key]==context[key] for key in ('journal_epoch','cursor','payload_digest'))
                        for ref in policy['consumed_refs'])
                    if (c is None or not anchored or not self._producer_allowed(context['producer_id'],context['producer_epoch'])
                            or c['status']!='active' or c['valid_until']<=at
                            or c['preference_set_id']!=preference['preference_set_id'] or c['tags']!=preference['condition_tags']
                            or any(c[key]!=request[key] for key in ('beneficiary','target','association_generation'))):
                        raise JournalError('invalid_reference','request conditional preference lacks its exact applicable current context head',409)

    def _target(self,target,output,commit,grant):
        policy=commit['checkpoint']['pipeline']['policy'] if commit['checkpoint']['pipeline'] else None
        requests=[request for request in policy['requests'] if request['id']==target['request_id']] if policy else []
        if len(requests)!=1:raise JournalError('invalid_reference','target has no checkpointed request',409)
        self._current_request_heads(requests[0],commit,grant)
        return super()._target(target,output,commit,grant)

    def _feedback_terminals(self,commit,grant,old):
        cp=commit["checkpoint"];cut=commit["input_cut"]
        originals=cp["dependency_receipts"];seen=set()
        previous={canonical(x["source_ref"]):x for x in old["dependency_receipts"]} if old else {}
        claims={x["ref"]["cursor"]:x for x in cut["receipts"]}
        for item in originals:
            key=canonical(item["source_ref"])
            if key in seen or item["digest"]!=digest({k:x for k,x in item.items() if k!="digest"}):
                raise JournalError("invalid_reference","dependency receipt duplicate or content changed",409)
            seen.add(key);p,_=self._source_component(item["source_ref"],grant,through=cut["input_cursor"],historical=True)
            served=self.j.db.execute("SELECT served_at FROM dynamic_deliveries WHERE principal_id=? AND consumer_id=? AND cursor=?",(grant.principal_id,grant.expected_consumer_id,p["cursor"])).fetchone()
            prior=self.j.db.execute("SELECT received_at,eligible_at FROM feedback_worker_first_receipts WHERE principal_id=? AND consumer_id=? AND cursor=?",(grant.principal_id,grant.expected_consumer_id,p["cursor"])).fetchone()
            expected=claims.get(p["cursor"])
            if (not served or not max(served[0],p["committed_at"])<=item["received_at"]<=self.j.clock()
                    or item["eligible_at"]!=self._eligible(p,item["received_at"])
                    or expected and (expected["received_at"]!=item["received_at"] or expected["eligible_at"]!=item["eligible_at"])
                    or prior and tuple(prior)!=(item["received_at"],item["eligible_at"])
                    or key in previous and previous[key]!=item):
                raise JournalError("invalid_input_receipt","dependency lacks its unchanged authenticated first consumer receipt",409)
            self.j.db.execute("INSERT OR IGNORE INTO feedback_worker_first_receipts VALUES(?,?,?,?,?)",(grant.principal_id,grant.expected_consumer_id,p["cursor"],item["received_at"],item["eligible_at"]))
        for claimed in cut["receipts"]:
            prior=self.j.db.execute("SELECT received_at,eligible_at FROM feedback_worker_first_receipts WHERE principal_id=? AND consumer_id=? AND cursor=?",(grant.principal_id,grant.expected_consumer_id,claimed["ref"]["cursor"])).fetchone()
            if prior and tuple(prior)!=(claimed["received_at"],claimed["eligible_at"]):raise JournalError("invalid_input_receipt","worker original receipt cannot refresh",409)
            self.j.db.execute("INSERT OR IGNORE INTO feedback_worker_first_receipts VALUES(?,?,?,?,?)",(grant.principal_id,grant.expected_consumer_id,claimed["ref"]["cursor"],claimed["received_at"],claimed["eligible_at"]))
        terminals=cp["feedback_terminals"];oldrows=old["feedback_terminals"] if old else []
        if terminals[:len(oldrows)]!=oldrows:raise JournalError("feedback_terminal_conflict","first feedback terminal cannot change or disappear",409)
        applications=[x for x in commit["outputs"] if x["schema"]=="home-experience-feedback-application-1"]
        if len(applications)!=len(terminals)-len(oldrows):raise JournalError("invalid_commit","new terminal rows and atomic application outputs must match",409)
        keys=set()
        for row in terminals:
            key=canonical(row["origin"])
            if key in keys:raise JournalError("feedback_terminal_conflict","feedback origin repeats",409)
            keys.add(key)
            p,feedback=self._source_component(row["feedback_ref"],grant,through=cut["input_cursor"],historical=True)
            if (p["payload_type"]!="home-experience-feedback-1" or row["origin"]!={"producer_id":p["producer_id"],"producer_epoch":p["producer_epoch"],"event_id":p["id"]}
                    or row["feedback_digest"]!=p["payload_digest"]):raise JournalError("invalid_reference","terminal changed its complete feedback origin",409)
            if row in oldrows:continue
            claim=claims.get(p["cursor"])
            policy=next((x for x in commit["outputs"] if x["schema"]=="home-lighting-policy-decision-3"),None)
            proof={"journal_epoch":commit["journal_epoch"],"cut_id":cut["cut_id"],"cut_cursor":cut["input_cursor"],"as_of_t":cut["as_of_t"],"digest":cut["digest"]}
            if (not claim or claim["eligible_at"]>cut["as_of_t"] or row["feedback_received_at"]!=claim["received_at"]
                    or row["feedback_eligible_at"]!=claim["eligible_at"] or row["decided_at"]!=cut["as_of_t"]
                    or row["input_cut_ref"]!=proof or not policy or row["decision_ref"]!=policy["id"]):
                raise JournalError("invalid_reference","terminal requires its original eligible processing receipt and semantic decision",409)
            app=next((x for x in applications if x["origin"]==row["origin"]),None)
            if (app is None or app["id"]!=row["application_id"] or digest(app)!=row["application_digest"]
                    or any(app[k]!=row[k] for k in row if k not in {"application_id","application_digest"})
                    or app["available_at"]!=row["decided_at"]):raise JournalError("invalid_reference","application differs from first immutable terminal ledger",409)
            if row["status"]=="expired" and feedback["valid_until"]>cut["as_of_t"] or row["status"]!="expired" and feedback["valid_until"]<=cut["as_of_t"]:
                raise JournalError("invalid_reference","terminal expiry differs from original feedback",409)
            if row["status"]=="attributed":
                ep,exposure=self._received_original(feedback["exposure_ref"],commit,grant,feedback["reported_at"])
                bp,binding=self._received_original(feedback["actor_binding_ref"],commit,grant,feedback["reported_at"])
                _,preference=self._received_original(feedback["preference_refs"][0]["source_ref"],commit,grant,exposure["from_t"])
                policy=self.j.adaptive_feedback_config["classifier_policy"]
                q=exposure.get("qualification_basis") or {};f=q.get("authority_fences") or {};a=cut["authority"]
                source=next((x for x in a["sources"] if x["source_id"]==f.get("source_id")),None)
                registration=next((x for x in a["registrations"] if x["registration_ref"]==f.get("registration_ref")),None)
                follow=next((x for x in a["follows"] if x["person_ref"]==feedback["person_ref"] and x["object_ref"]==feedback["target"]["object_ref"]
                             and x["association_generation"]==feedback["association_generation"] and x["source_id"]==f.get("source_id") and x["registration_ref"]==f.get("registration_ref")),None)
                producers={(x["producer_id"],x["producer_epoch"]):x for x in a["producers"]}
                # Actor binding describes the authenticated epoch, not an event.
                permitted=binding in self.j.adaptive_feedback_config["actor_bindings"] and binding["feedback_origin"]=={k:p[k] for k in ("producer_id","producer_epoch")}
                if (not self._producer_allowed(p["producer_id"],p["producer_epoch"]) or not self._producer_allowed(bp["producer_id"],bp["producer_epoch"])
                        or ep["producer_id"]!=grant.producer_id or ep["producer_epoch"]!=grant.producer_epoch or exposure["status"]!="qualified"
                        or exposure["until"]>feedback["reported_at"] or exposure["goal_ref"]!=feedback["goal_ref"] or exposure["preference_ref"]!=feedback["preference_refs"][0]
                        or exposure["person_ref"]!=feedback["person_ref"] or exposure["target"]!=feedback["target"]
                        or not permitted or binding["actor_ref"]!=feedback["actor_ref"] or binding["person_ref"]!=feedback["person_ref"]
                        or not binding["effective_at"]<=feedback["reported_at"]<binding["valid_until"] or binding["valid_until"]<=cut["as_of_t"]
                        or digest(preference)!=feedback["preference_refs"][0]["digest"] or preference["beneficiary"]!=feedback["beneficiary"] or preference["target"]!=feedback["target"]
                        or not any(x["owner_id"]==preference["owner_id"] and x["preference_set_id"]==preference["preference_set_id"] and x["subject"]==feedback["subject"] and x["beneficiary"]==feedback["beneficiary"] and x["target"]==feedback["target"] for x in binding["allowed_bindings"])
                        or feedback["dimension"] not in policy["allowed_dimensions"] or cut["as_of_t"]-feedback["reported_at"]>policy["max_feedback_age_s"]
                        or feedback["reported_at"]-exposure["until"]>policy["max_exposure_age_s"] or exposure["until"]-exposure["from_t"]>policy["max_exposure_span_s"]
                        or not a["room"]["motion_allowed"] or a["room"]["motion_generation"]!=f.get("room_motion_generation")
                        or source is None or source["revoked"] or source["revocation_generation"]!=f.get("source_revocation_generation")
                        or registration is None or registration["revoked"] or registration["registration_generation"]!=f.get("registration_generation")
                        or follow is None or not follow["follow_allowed"] or follow["follow_generation"]!=f.get("follow_generation")
                        or any((x["producer_id"],x["producer_epoch"]) not in producers or producers[(x["producer_id"],x["producer_epoch"])]["revoked"]
                               or producers[(x["producer_id"],x["producer_epoch"])]["revocation_generation"]!=x["revocation_generation"] for x in q.get("producer_fences",[]))):
                    raise JournalError("invalid_reference","attribution cannot substitute an unavailable OLD exposure or revoked epoch",409)

    def _exposure_outputs(self,commit,grant,old):
        for report in (x for x in commit["outputs"] if x["schema"]=="home-lighting-settled-exposure-1"):
            if report["t"]!=commit["input_cut"]["as_of_t"]:raise JournalError("invalid_reference","exposure report cutoff differs",409)
            for e in report["exposures"]:
                controls=[];boundaries=[]
                for ref in e["control_refs"]:
                    p,c=self._source_component(ref,grant,through=commit["input_cut"]["input_cursor"],historical=True)
                    if (p["payload_type"]!="home-lighting-control-decision-3" or p["producer_id"]!=grant.producer_id or p["producer_epoch"]!=grant.producer_epoch):
                        raise JournalError("invalid_reference","exposure interval is not an authenticated worker control",409)
                    controls.append(c)
                for ref in e["boundary_refs"]:
                    p,b=self._source_component(ref,grant,through=commit["input_cut"]["input_cursor"],historical=True)
                    if p["producer_id"]!=grant.producer_id or p["producer_epoch"]!=grant.producer_epoch or p["payload_type"] not in {"home-lighting-control-decision-3","home-lighting-installed-boundary-1"}:
                        raise JournalError("invalid_reference","exposure boundary is not a retained installed original",409)
                    boundaries.append((p,b))
                if len(controls)==5 and (any(abs(c["t"]-(report["t"]-1+i*.2))>1e-8 for i,c in enumerate(controls))
                        or e["interval_digests"]!=[digest(c["interval_proof"]) for c in controls]):
                    raise JournalError("invalid_reference","exposure changed its fixed five complete controls",409)
                if e["status"]!="qualified":continue
                receipts={canonical(x["source_ref"]):x for x in commit["checkpoint"]["dependency_receipts"]}
                if any(canonical(ref) not in receipts for ref in e["control_refs"]+e["boundary_refs"]):
                    raise JournalError("invalid_reference","qualified exposure lacks retained original first receipts",409)
                lower=[];upper=[]
                for i,c in enumerate(controls):
                    p,b=boundaries[i];state=b["installed_state"]
                    if (c["interval_proof"]["start_original_ref"]!=_publication_ref(p) or c["interval_proof"]["start_installed_digest"]!=state["digest"]
                            or abs(state["t"]-c["execution"]["from_t"])>1e-8):raise JournalError("invalid_reference","interval did not start at its exact POST original",409)
                    matches=[a for a in c["interval_proof"]["assessments"] if a["qualification_basis_digest"]==digest(e["qualification_basis"])]
                    if (len(matches)!=1 or not matches[0]["settled"] or matches[0]["status"]!="modeled_in_band"
                            or matches[0]["assessed_lux"] is None or matches[0]["original_support_valid_until"]<=c["t"]
                            or matches[0]["environment_valid_until"] is None or matches[0]["environment_valid_until"]<=c["t"]
                            or matches[0]["installation_basis_digest"]!=digest(e["installation_bases"][i])):
                        raise JournalError("invalid_reference","qualified report cannot promote unavailable or changed interval assessment",409)
                    for f in state["fixtures"]:
                        if (f["motion"] is not None or f["fade"] is not None or state["t"]<f["settle_until"]
                                or any(f[a][k]!=0 for a in ("pan","tilt") for k in ("velocity","acceleration"))):
                            raise JournalError("invalid_reference","settled exposure began with active mechanics",409)
                    for sample in c["execution"]["samples"]:
                        for f,tuple_value in zip(state["fixtures"],sample["states"]):
                            target=c["execution"]["target_ids"][tuple_value[8]] if tuple_value[8]>=0 else None
                            if (any(abs(x-y)>1e-9 for x,y in zip(tuple_value[:7],[f["pan"]["position"],f["tilt"]["position"],0,0,0,0,f["level"]]))
                                    or target!=f["target_id"]):raise JournalError("invalid_reference","settled report omitted movement or fade within actual samples",409)
                    lower.append(matches[0]["assessed_lux"]["min"]);upper.append(matches[0]["assessed_lux"]["max"])
                if e["assessed_lux"]!={"min":min(lower),"max":max(upper)}:raise JournalError("invalid_reference","summary extrema differ from complete prior interval proofs",409)

    def _received_original(self,ref,commit,grant,at):
        p,body=self._source_component(ref,grant,through=commit["input_cut"]["input_cursor"],historical=True)
        rows=[r for r in commit["checkpoint"]["dependency_receipts"] if r["source_ref"]["publication_ref"]["cursor"]==p["cursor"]]
        if not rows or any(r["eligible_at"]>at for r in rows):
            raise JournalError("invalid_reference","exposure basis requires its original eligible consumer receipt",409)
        return p,body

    def _qualification_context_original(self,rows,preference,target,at):
        """Select causal chain heads before compatibility or disposition."""
        chains={}
        for row in rows:
            p=row["publication"];body=p["payload"]
            if (p["payload_type"]!="home-experience-context-declaration-1" or not _same_scope(body,self.identity)
                    or body["owner_id"]!=preference["owner_id"] or body["subject"]!=preference["subject"]
                    or self._eligible(p,row["received_at"])>at):continue
            key=canonical([*[body[k] for k in SCOPE_KEYS],body["owner_id"],body["subject"],body["context_set_id"]])
            old=chains.get(key,[])
            if not old or body["revision"]>old[0]["payload"]["revision"]:chains[key]=[p]
            elif body["revision"]==old[0]["payload"]["revision"]:chains[key]=old+[p]
        selected=[]
        for heads in chains.values():
            if len({p["payload_digest"] for p in heads})!=1:return None
            p=min(heads,key=lambda original:original["cursor"]);body=p["payload"]
            if (body["status"]!="active" or body["valid_until"]<=at
                    or body["preference_set_id"]!=preference["preference_set_id"]
                    or any(body[k]!=target[k] for k in ("beneficiary","target","association_generation"))):continue
            if preference["condition_tags"] is not None and (body["context_set_id"]!=preference["context_set_id"] or body["tags"]!=preference["condition_tags"]):continue
            selected.append(p)
        return selected[0] if len(selected)==1 else None

    def _qualification_context_rows(self,commit,grant):
        """Use complete authentic delivery history, not a claimed row subset."""
        return self._current_revision_rows(commit,grant,('home-experience-context-declaration-1',))

    def _current_revision_rows(self,commit,grant,families):
        """Read genuine through-cut originals with their immutable worker FIRST."""
        allowed=tuple(family for family in families if family in grant.payload_types)
        if not allowed:return []
        claims={row["ref"]["cursor"]:row for row in commit["input_cut"]["receipts"]}
        result=[]
        placeholders=','.join('?' for _ in allowed)
        for row in self.j.db.execute("SELECT body,revision FROM publications WHERE site_id=? AND room_id=? AND payload_type IN ("+placeholders+") AND cursor<=? ORDER BY cursor",
                                     (commit["site_id"],commit["room_id"],*allowed,commit["input_cut"]["input_cursor"])):
            p=json.loads(row[0])
            if row[1] not in grant.revisions or not self._read_allowed(p,grant):continue
            prior=self.j.db.execute("SELECT received_at,eligible_at FROM feedback_worker_first_receipts WHERE principal_id=? AND consumer_id=? AND cursor=?",
                                     (grant.principal_id,grant.expected_consumer_id,p["cursor"])).fetchone()
            claimed=claims.get(p["cursor"])
            if prior and claimed and tuple(prior)!=(claimed["received_at"],claimed["eligible_at"]):
                raise JournalError("invalid_input_receipt","qualification context cannot refresh its original worker receipt",409)
            received=prior[0] if prior else claimed["received_at"] if claimed else None
            if received is not None:result.append({"publication":p,"received_at":received})
        return result

    def _feedback_context_proofs(self,commit,grant,old):
        pipeline=commit["checkpoint"]["pipeline"]
        if pipeline is None:return
        feedback=pipeline["feedback_exposure"];held=feedback["held_context"]
        oldfeedback=old["pipeline"]["feedback_exposure"] if old and old["pipeline"] else None
        for field,refkey in (("acknowledged_boundaries","original_ref"),("completed_intervals","control_original_ref")):
            rows=feedback[field];seen=set()
            prior={canonical(r[refkey]):r for r in oldfeedback[field]} if oldfeedback else {}
            for row in rows:
                key=canonical(row[refkey])
                if key in seen:raise JournalError("invalid_reference","exposure history original repeats",409)
                seen.add(key);p,body=self._received_original(row[refkey],commit,grant,commit["input_cut"]["as_of_t"])
                first=next(r for r in commit["checkpoint"]["dependency_receipts"] if r["source_ref"]["publication_ref"]["cursor"]==p["cursor"])
                if (p["producer_id"]!=grant.producer_id or p["producer_epoch"]!=grant.producer_epoch or row["first_received_at"]!=first["received_at"]
                        or key in prior and prior[key]!=row):raise JournalError("invalid_reference","exposure history changed its authenticated first receipt",409)
                if field=="completed_intervals":
                    # The checkpoint keeps the authenticated full start source;
                    # the published interval retains its compact parent ref.
                    interval=copy.deepcopy(row["interval"])
                    start,_=self._received_original(interval["start_original_ref"],commit,grant,commit["input_cut"]["as_of_t"])
                    interval["start_original_ref"]=_publication_ref(start)
                    if body["schema"]!="home-lighting-control-decision-3" or interval!=body["interval_proof"]:
                        raise JournalError("invalid_reference","completed interval differs from its admitted control original",409)
                elif (body["schema"] not in {"home-lighting-control-decision-3","home-lighting-installed-boundary-1"}
                      or any(row[k]!=body["installed_state"][k] for k in ("t","control_seq")) or row["installed_state_digest"]!=body["installed_state"]["digest"]):
                    raise JournalError("invalid_reference","acknowledged history differs from exact installed original",409)
        priorheld=oldfeedback["held_context"] if oldfeedback else None
        control=next((o for o in commit["outputs"] if o["schema"]=="home-lighting-control-decision-3"),None)
        if control:
            ack=commit["checkpoint"]["acknowledged_installed"]
            expected=[]
            for qrow in priorheld["qualification_rows"] if priorheld else []:
                staged=next((r for r in priorheld["installation_rows"] if r["target_id"]==qrow["target_id"]),None)
                if staged is None:raise JournalError("invalid_reference","prior qualification lacks exact staged installation",409)
                basis={**staged["basis"],"schema":"home-lighting-exposure-installation-basis-2","installed_original_ref":ack["source_ref"]}
                expected.append((qrow["target_id"],qrow["digest"],digest(basis),basis))
            actual=control["interval_proof"]["assessments"]
            if len(actual)!=len(expected) or len({r["target_id"] for r in actual})!=len(actual):
                raise JournalError("invalid_reference","interval must retain every OLD qualification row",409)
            for target_id,qhash,ihash,basis in expected:
                row=next((r for r in actual if r["target_id"]==target_id),None)
                if (row is None or row["qualification_basis_digest"]!=qhash or row["installation_basis_digest"]!=ihash
                        or row["original_support_valid_until"]!=basis["original_support_valid_until"]):
                    raise JournalError("invalid_reference","interval attribution changed its OLD installation basis",409)
                if row["status"] in {"modeled_in_band","modeled_out_of_band"}:
                    _,start=self._received_original(ack["source_ref"],commit,grant,control["t"])
                    fixtures=start["installed_state"]["fixtures"]
                    if (not row["settled"] or row["assessed_lux"] is None or row["original_support_valid_until"]<=control["t"]
                            or row["environment_valid_until"] is None or row["environment_valid_until"]<=control["t"]
                            or any(f["motion"] is not None or f["fade"] is not None or control["execution"]["from_t"]<f["settle_until"]
                                   or any(f[a][k]!=0 for a in ("pan","tilt") for k in ("velocity","acceleration")) for f in fixtures)):
                        raise JournalError("invalid_reference","modeled settled interval began without stationary qualified support",409)
                    for sample in control["execution"]["samples"]:
                        for f,state in zip(fixtures,sample["states"]):
                            target=control["execution"]["target_ids"][state[8]] if state[8]>=0 else None
                            if target!=f["target_id"] or any(abs(x-y)>1e-9 for x,y in zip(state[:7],[f["pan"]["position"],f["tilt"]["position"],0,0,0,0,f["level"]])):
                                raise JournalError("invalid_reference","settled interval omitted actual mechanics changes",409)
        if held is None:return
        base={k:v for k,v in held.items() if k not in {"qualification_rows","installation_rows"}}
        if base!=pipeline["exposure_context"]:raise JournalError("invalid_reference","feedback held context differs from installed context",409)
        if held==priorheld:return
        output=next((o for o in commit["outputs"] if o["schema"] in {"home-lighting-control-decision-3","home-lighting-installed-boundary-1"}),None)
        if output is None:raise JournalError("invalid_reference","semantic processing cannot replace installed held exposure",409)
        state=output["installed_state"];t=state["t"]
        if held["installed_at"]!=t:raise JournalError("invalid_reference","held exposure installation clock differs",409)
        qrows=held["qualification_rows"];irows=held["installation_rows"]
        if (len({r["target_id"] for r in qrows})!=len(qrows) or {r["target_id"] for r in qrows}!={r["target_id"] for r in irows} or len(irows)!=len(qrows)):
            raise JournalError("invalid_reference","qualification/installation row identities differ",409)
        context_rows=self._qualification_context_rows(commit,grant) if qrows else []
        for qr in qrows:
            q=qr["basis"];ir=next(r for r in irows if r["target_id"]==qr["target_id"]);i=ir["basis"]
            target=next((x for x in held["targets"] if x["id"]==qr["target_id"]),None)
            request=next((x for x in held["requests"] if target and x["id"]==target["request_id"]),None)
            bound=next((x for x in held["support_bounds"] if x["target_id"]==qr["target_id"]),None)
            if bound is None or bound["valid_until"]<=max(t,commit["checkpoint"]["last_t"]):
                raise JournalError("invalid_reference","new qualified held support must extend beyond installation",409)
            if (not target or target["role"]!="benefit" or not request or request["lease"] is None or not bound
                    or qr["digest"]!=digest(q) or ir["digest"]!=digest(i) or q["target_id"]!=target["id"]
                    or any(q[k]!=target[k] for k in ("beneficiary","target","association_generation"))
                    or q["authority_fences"]!=target["fences"] or len(request["preference_refs"])!=1):
                raise JournalError("invalid_reference","qualification row changed its installed benefit target",409)
            _,goal=self._received_original(q["goal_ref"],commit,grant,t)
            _,pref=self._received_original(q["preference_ref"],commit,grant,t)
            _,context=self._received_original(q["context_ref"],commit,grant,t)
            selected_context=self._qualification_context_original(context_rows,pref,target,t)
            if (goal!=self._component(request["goal_ref"],commit,grant,historical=True) or pref!=self._component(request["preference_refs"][0],commit,grant,historical=True)
                    or goal["schema"]!="home-experience-goal-1" or pref["schema"]!="home-preference-snapshot-2"
                    or selected_context is None or selected_context["payload"]!=context or q["context_ref"]!=self._source_ref(selected_context)):
                raise JournalError("invalid_reference","qualification did not bind exact OLD goal/preference/context originals",409)
            self._current_request_heads(request,commit,grant)
            band=request["objectives"][0]
            if q["requested_lux"]!={"min":band["min_lux"],"desired":band["desired_lux"],"max":band["max_lux"]} or q["coverage_fraction"]!=band["coverage_fraction"]:
                raise JournalError("invalid_reference","qualification band changed its original request",409)
            if len(q["capability_refs"])!=len(request["capability_refs"]):raise JournalError("invalid_reference","qualification capabilities differ",409)
            for ref,wanted in zip(q["capability_refs"],request["capability_refs"]):
                _,cap=self._received_original(ref,commit,grant,t)
                if cap!=self._component(wanted,commit,grant,historical=True) or q["model"]!={k:cap[k] for k in ("profile_ref","profile_digest","model_ref","model_digest")}:
                    raise JournalError("invalid_reference","qualification synthetic model differs from original capability",409)
            if len(q["instruction_effects"])!=len(request["instruction_refs"]):raise JournalError("invalid_reference","qualification instruction set differs",409)
            for effect,wanted in zip(q["instruction_effects"],request["instruction_refs"]):
                _,instruction=self._received_original(effect["instruction_ref"],commit,grant,t)
                ap,application=self._received_original(effect["application_ref"],commit,grant,t)
                if (instruction!=self._component(wanted,commit,grant,historical=True) or application["schema"]!="home-intent-application-result-3"
                        or ap["producer_id"]!=grant.producer_id or ap["producer_epoch"]!=grant.producer_epoch
                        or application["status"]!="applied" or application["intent_id"]!=instruction["id"] or application["intent_digest"]!=digest(instruction)
                        or application["decided_at"]>t or effect["kind"]!=instruction["kind"] or effect["value"]!=instruction["value"]):
                    raise JournalError("invalid_reference","qualification instruction lacks exact first APPLIED application original",409)
            protected=[x for x in held["targets"] if x["role"]=="protected"]
            if len(q["protected_constraints"])!=len(protected):raise JournalError("invalid_reference","qualification protected receiver set differs",409)
            for constraint,receiver in zip(q["protected_constraints"],protected):
                protected_request=next((r for r in held["requests"] if r["id"]==receiver["request_id"]),None)
                _,protected_goal=self._received_original(constraint["goal_ref"],commit,grant,t)
                sp,surface=self._received_original(constraint["geometry_ref"],commit,grant,t)
                source=next((s for s in commit["checkpoint"]["authority"]["sources"] if s["source_id"]==receiver["source_id"]),None)
                registration=next((r for r in commit["checkpoint"]["authority"]["registrations"] if r["registration_ref"]==receiver["registration_ref"]),None)
                if (not protected_request or surface["schema"]!="home-registered-surface-1" or not source or source["revoked"] or not registration or registration["revoked"]
                        or protected_goal!=self._component(receiver["goal_ref"],commit,grant,historical=True)
                        or _publication_ref(sp)!=receiver["geometry_ref"] or constraint["target"]!=receiver["target"]
                        or constraint["exposure_ceiling_lux"]!=receiver["objectives"][0]["max_lux"]
                        or any(constraint[k]!=receiver[k] for k in ("uncertainty_correlation_ref","source_id","registration_ref"))
                        or constraint["registration_generation"]!=registration["registration_generation"]
                        or constraint["source_revocation_generation"]!=source["revocation_generation"]
                        or constraint["room_motion_generation"]!=commit["checkpoint"]["authority"]["room"]["motion_generation"]
                        or len(constraint["preference_refs"])!=len(protected_request["preference_refs"])):
                    raise JournalError("invalid_reference","qualification protected receiver changed commissioned original or authority",409)
                self._current_request_heads(protected_request,commit,grant)
                for ref,wanted in zip(constraint["preference_refs"],protected_request["preference_refs"]):
                    _,body=self._received_original(ref,commit,grant,t)
                    if body!=self._component(wanted,commit,grant,historical=True):raise JournalError("invalid_reference","protected preference original differs",409)
            pp,policy=self._received_original(i["policy_ref"],commit,grant,t)
            rp,actual_request=self._received_original(i["request_ref"],commit,grant,t)
            lp,lease=self._received_original(i["lease_ref"],commit,grant,t)
            if (pp["producer_id"]!=grant.producer_id or pp["producer_epoch"]!=grant.producer_epoch or pp!=rp or pp!=lp
                    or policy["schema"]!="home-lighting-policy-decision-3" or request not in policy["requests"] or actual_request!=request or lease!=request["lease"]
                    or i["qualification_basis_digest"]!=qr["digest"] or i["installed_state_digest"]!=state["digest"] or i["input_cut_digest"]!=output["input_cut_digest"]
                    or i["installed_at"]!=t or i["control_seq"]!=state["control_seq"] or i["follow_mode"]!=request["follow_mode"]
                    or i["request_valid_until"]!=request["valid_until"] or i["lease_valid_until"]!=lease["valid_until"] or i["original_support_valid_until"]!=bound["valid_until"]):
                raise JournalError("invalid_reference","installation basis changed its original policy/request/lease or accepted state",409)
            gp,geometry=self._received_original(i["geometry"]["original_ref"],commit,grant,t)
            estimates=[e for e in geometry.get("objects",[]) if e["object_ref"]==target["target"]["object_ref"]]
            if (geometry["schema"]!="home-geometry-4" or i["geometry"]["original_ref"]["component"] is not None or len(estimates)!=1
                    or {k:v for k,v in target["geometry_ref"].items() if k!="component_id"}!=_publication_ref(gp)
                    or target["geometry_ref"].get("component_id",target["target"]["object_ref"]["oid"])!=target["target"]["object_ref"]["oid"]
                    or i["geometry"]["geometry_digest"]!=digest(estimates[0])
                    or any(i["geometry"][k]!=geometry[k] for k in ("capture","observed_at","available_at","valid_until","registry_digest","registration_generation"))
                    or any(i["geometry"][k]!=target[k] for k in ("uncertainty_correlation_ref","transform_origin_m"))):
                raise JournalError("invalid_reference","installation geometry changed original capture/components or clocks",409)
            if len(i["environment_originals"])!=len(held["environment_heads"]):raise JournalError("invalid_reference","installation environment differs",409)
            for ref,head in zip(i["environment_originals"],held["environment_heads"]):
                _,environment=self._received_original(ref["original_ref"],commit,grant,t)
                if environment!=head["body"] or any(ref[k]!=environment[k] for k in ("source_id","observed_at","effective_at","available_at","valid_until")):
                    raise JournalError("invalid_reference","installation environment changed original body or clocks",409)
            refs=[q["goal_ref"],q["preference_ref"],q["context_ref"],*q["capability_refs"],i["policy_ref"],i["geometry"]["original_ref"],
                  *[x["original_ref"] for x in i["environment_originals"]],
                  *[r for e in q["instruction_effects"] for r in (e["instruction_ref"],e["application_ref"])],
                  *[r for p in q["protected_constraints"] for r in (p["goal_ref"],*p["preference_refs"],p["geometry_ref"])]]
            keys={canonical([r["producer_id"],r["producer_epoch"]]) for r in refs}
            fences=[p for p in commit["checkpoint"]["authority"]["producers"] if canonical([p["producer_id"],p["producer_epoch"]]) in keys]
            if q["producer_fences"]!=fences or len(fences)!=len(keys) or any(p["revoked"] for p in fences):
                raise JournalError("invalid_reference","qualification producer epoch fences differ from installed authority",409)

    def request_evidence(self,payload,grant):
        self.validate_payload(payload)
        if payload["schema"] != "home-evidence-request-3" or not 0 < payload["deadline"]-payload["available_at"] <= 3:
            raise JournalError("invalid_payload","cached request3 requires a deadline within three seconds")
        grant.require("evidence_request",payload["site_id"],payload["room_id"],payload_type=payload["schema"],revision="3.0")
        event=self._event_for(payload,grant,valid_until=payload["deadline"]);self.j._event(event)
        def apply():
            retry=self._event_retry(event,grant,payload)
            if retry is not None:return retry
            self._grant_current(grant)
            self._expire_requests(payload["site_id"],payload["room_id"])
            if max(payload["available_at"],self.j.clock()) >= payload["deadline"]:
                raise JournalError("expired","request deadline elapsed",409)
            if payload["available_at"] > self.j.clock():raise JournalError("not_eligible","request is future available",409)
            if self.j.db.execute("SELECT count(*) FROM requests WHERE terminal IS NULL").fetchone()[0]>=1:
                raise JournalError("request_budget","one pending synthetic acquisition is permitted",429)
            if self.j.db.execute("SELECT 1 FROM requests WHERE request_id=?",(payload["id"],)).fetchone():
                raise JournalError("identity_conflict","request id is already used",409)
            # Issuer availability is preflighted before admitting an original.
            self._service_issuer("home-evidence-reply-3")
            acquisition_issuer=self._service_issuer("home-acquisition-status-2") if self.j.evidence_router=="synthetic_stub" else None
            receipt=self.append(event,grant=grant,raw_request=payload)
            self.j.db.execute("INSERT INTO requests VALUES(?,?,?,?,?,NULL,?,?)",(payload["id"],object_key(payload["object_ref"]),payload["site_id"],payload["room_id"],payload["deadline"],canonical(payload),canonical(receipt)))
            if acquisition_issuer:
                acquisition={"schema":"home-acquisition-status-2","version":2,"id":"acquisition:"+payload["id"],
                             **self.identity,"clock_profile_digest":self.clock_profile["digest"],"request_id":payload["id"],
                             "available_at":self.j.clock(),"deadline":payload["deadline"],"status":"pending","reason":"synthetic_stub_pending","actual_frames":0,"actual_model_calls":0}
                self.append(self._event_for(acquisition,acquisition_issuer,valid_until=payload["deadline"]),grant=acquisition_issuer)
            else:
                ref=self._cached_estimate(payload)
                self._terminal(payload,"supported" if ref else "unavailable","cached_geometry" if ref else "no_compatible_cached_geometry",ref)
            return receipt
        return self.j._transaction(apply)

    def _terminal(self,request,status,reason,ref=None):
        row=self.j.db.execute("SELECT terminal FROM requests WHERE request_id=?",(request["id"],)).fetchone()
        if row and row[0]:return json.loads(row[0])["receipt"]
        issuer=self._service_issuer("home-evidence-reply-3")
        reply={"schema":"home-evidence-reply-3","version":3,"id":"reply:"+request["id"],**self.identity,
               "clock_profile_digest":self.clock_profile["digest"],"request_id":request["id"],"available_at":self.j.clock(),
               "status":status,"reason":reason,"estimate_ref":ref,"actual_frames":0,"actual_model_calls":0}
        self.validate_payload(reply)
        receipt=self.append(self._event_for(reply,issuer,valid_until=min(120,max(self.j.clock()+1,request["deadline"]))),grant=issuer)
        self.j.db.execute("UPDATE requests SET terminal=? WHERE request_id=? AND terminal IS NULL",(canonical({"payload":reply,"receipt":receipt}),request["id"]))
        return receipt

    def reply_evidence(self,payload,grant):
        self.validate_payload(payload)
        if payload["schema"] != "home-evidence-reply-3":raise JournalError("unsupported_schema","reply3 required")
        grant.require("evidence_reply",payload["site_id"],payload["room_id"],payload_type=payload["schema"],revision="3.0")
        if (grant.principal_id,grant.producer_id,grant.producer_epoch)!=(lambda g:(g.principal_id,g.producer_id,g.producer_epoch))(self._service_issuer("home-evidence-reply-3",current=False)):
            raise JournalError("forbidden","reply must use its dedicated authenticated issuer",403)
        def apply():
            row=self.j.db.execute("SELECT * FROM requests WHERE request_id=?",(payload["request_id"],)).fetchone()
            if not row or not _same_scope(json.loads(row["body"]),payload):raise JournalError("unknown_request","reply is outside request scope",404)
            if row["terminal"]:
                terminal=json.loads(row["terminal"])
                if terminal["payload"]!=payload:raise JournalError("terminal_conflict","first request terminal is immutable",409)
                return terminal["receipt"]
            self._grant_current(grant);request=json.loads(row["body"]);now=self.j.clock()
            if payload["available_at"]!=now:raise JournalError("invalid_clock","terminal reply must use actual admission time",409)
            if (now>=request["deadline"])!=(payload["status"]=="expired"):
                raise JournalError("expired","terminal expiry differs from request deadline",409)
            if payload["status"]=="supported":
                ref=self._cached_estimate(request)
                if ref is None or ref!=payload["estimate_ref"]:raise JournalError("unsupported_evidence","support requires exact current qualified cached geometry",409)
            elif payload["estimate_ref"] is not None:raise JournalError("unsupported_evidence","non-supported terminal cannot assert geometry",409)
            receipt=self.append(self._event_for(payload,grant,valid_until=min(120,max(now+1,request["deadline"]))),grant=grant,raw_request=payload)
            self.j.db.execute("UPDATE requests SET terminal=? WHERE request_id=?",(canonical({"payload":payload,"receipt":receipt}),payload["request_id"]))
            return receipt
        return self.j._transaction(apply)

