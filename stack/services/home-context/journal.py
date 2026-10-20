"""Bounded, durable local shadow journal. No device or model integrations."""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, replace
from pathlib import Path


PREFIX = "/api/home-context/v1"
DYNAMIC_PROFILE = "dynamic-book-synthetic-1"
MAP_REHEARSAL_PROFILE = "home-map-rehearsal-synthetic-1"
ADAPTIVE_PROFILE = "adaptive-book-synthetic-1"
FEEDBACK_PROFILE = "adaptive-book-feedback-synthetic-1"
REFERENCE_PROFILE = "adaptive-book-feedback-reference-synthetic-1"
G2_PROFILE = "adaptive-book-feedback-reference-geometry-hold-synthetic-2"
LIVE_PROFILE = "home-live-shadow-1"
G2_TYPED_OPERATIONS = {"geometry_constraint_source": "home-preference-geometry-constraint-source-1",
                       "feedback_assessment": "home-preference-learning-assessment-3",
                       "write_authority": "home-preference-write-authority-2",
                       "write_frontier": "home-preference-write-frontier-2",
                       "support_qualification": "home-preference-support-qualification-2"}
FEEDBACK_PROFILES = frozenset({FEEDBACK_PROFILE, REFERENCE_PROFILE, G2_PROFILE})
TERMINALS = {"supported", "abstained", "unavailable", "denied", "expired", "cancelled"}
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}\Z")
EVENT_KEYS = {"schema", "schema_version", "id", "producer_id", "stream_epoch", "site_id", "room_id",
              "session_id", "domain_epoch", "observed_at", "available_at", "valid_until", "payload_type",
              "payload_version", "revision", "payload"}
FORBIDDEN_KEYS = {"jpeg_base64", "image_base64", "embedding", "embeddings", "identity_hypothesis", "person_name"}


class JournalError(Exception):
    def __init__(self, code, message, status=400, **details):
        super().__init__(message)
        self.code, self.message, self.status, self.details = code, message, status, details

    def response(self):
        return {"schema": "home-context-error-1", "error": {"code": self.code, "message": self.message, **self.details}}


def finite(value):
    return type(value) is int and abs(value) <= 9007199254740991 or type(value) is float and math.isfinite(value)


def identifier(value, field):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise JournalError("invalid_payload", f"{field} must be an identifier")
    return value


def _number(value):
    """ECMAScript JSON number form for the bounded numbers used by the contract."""
    if not finite(value):
        raise JournalError("invalid_payload", "finite JSON numbers required")
    if value == 0:
        return "0"
    if type(value) is int:
        if abs(value) > 9007199254740991:
            raise JournalError("invalid_payload", "JSON integers must be exactly representable")
        return str(value)
    raw = repr(value).lower()
    if "e" not in raw:
        return raw[:-2] if raw.endswith(".0") else raw
    mantissa, exponent = raw.split("e")
    exponent = int(exponent)
    sign = "-" if mantissa.startswith("-") else ""
    digits = mantissa.lstrip("-").replace(".", "")
    digits = digits.rstrip("0") or "0"
    point = 1 + exponent
    if -6 <= exponent < 21:
        if point <= 0:
            return sign + "0." + "0" * -point + digits
        if point >= len(digits):
            return sign + digits + "0" * (point - len(digits))
        return sign + digits[:point] + "." + digits[point:]
    mantissa = digits[0] + ("." + digits[1:] if len(digits) > 1 else "")
    return sign + mantissa + "e" + ("+" if exponent >= 0 else "-") + str(abs(exponent))


def canonical(value, depth=0):
    if depth > 24:
        raise JournalError("invalid_payload", "JSON nesting exceeds limit")
    if value is None:
        return "null"
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) in (int, float):
        return _number(value)
    if isinstance(value, str):
        try:
            return json.dumps(value, ensure_ascii=False).encode("utf-8").decode("utf-8")
        except UnicodeError:
            raise JournalError("invalid_payload", "invalid Unicode") from None
    if isinstance(value, list):
        return "[" + ",".join(canonical(v, depth + 1) for v in value) + "]"
    if isinstance(value, dict) and all(isinstance(k, str) for k in value):
        # JS canonicalJson sorts UTF-16 code units, including supplementary keys.
        keys = sorted(value, key=lambda k: k.encode("utf-16-be", errors="surrogatepass"))
        return "{" + ",".join(canonical(k) + ":" + canonical(value[k], depth + 1) for k in keys) + "}"
    raise JournalError("invalid_payload", "plain JSON required")


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def bounded_json(value, limit):
    encoded = canonical(value)
    if len(encoded.encode("utf-8")) > limit:
        raise JournalError("payload_too_large", "payload exceeds configured bound", 413)
    return encoded


def _no_private_fields(value, depth=0):
    if depth > 24:
        raise JournalError("invalid_payload", "JSON nesting exceeds limit")
    if isinstance(value, dict):
        if FORBIDDEN_KEYS.intersection(value):
            raise JournalError("forbidden_field", "private model fields are not journal inputs")
        for item in value.values():
            _no_private_fields(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _no_private_fields(item, depth + 1)


def check_person_ref(person, scope):
    keys = {"site_id", "room_id", "session_id", "domain_epoch", "track_epoch", "pid"}
    if not isinstance(person, dict) or set(person) != keys:
        raise JournalError("invalid_person_ref", "full scoped person_ref required")
    for key in keys - {"pid"}:
        identifier(person[key], key)
    if not (type(person["pid"]) is int and 1 <= person["pid"] <= 9007199254740991 or isinstance(person["pid"], str) and ID.fullmatch(person["pid"])):
        raise JournalError("invalid_person_ref", "pid must be a positive integer or opaque identifier")
    if any(person[k] != scope[k] for k in ("site_id", "room_id", "session_id", "domain_epoch")):
        raise JournalError("scope_mismatch", "person_ref belongs to another scope")


@dataclass(frozen=True)
class Limits:
    max_publications: int = 4096
    max_receipts: int = 262144
    max_state_items: int = 128
    max_body_bytes: int = 65536
    max_worker_commit_bytes: int = 262144
    max_manual_derived_event_bytes: int = 262144
    max_manual_worker_commit_bytes: int = 1048576
    max_response_bytes: int = 1048576
    max_db_bytes: int = 67108864
    max_publication_bytes: int = 16777216
    max_state_bytes: int = 524288
    retention_seconds: int = 86400
    max_workers: int = 32
    max_consumers: int = 128
    max_active_requests: int = 64

    def __post_init__(self):
        for name, value in self.__dict__.items():
            if type(value) is not int or value <= 0:
                raise ValueError(f"positive integer limit required: {name}")
        if self.max_db_bytes < 65536:
            raise ValueError("database budget must be at least 64 KiB")


@dataclass(frozen=True)
class Grant:
    principal_id: str
    producer_id: str
    token_sha256: str
    scopes: frozenset
    operations: frozenset
    payload_types: frozenset
    revisions: frozenset
    expected_actor_ref: str | None = None
    source_ids: frozenset = frozenset()
    sensor_roots: frozenset = frozenset()
    schema_pins: frozenset = frozenset()
    clock_mappings: tuple = ()
    registration_bindings: tuple = ()
    expected_worker_id: str | None = None
    adaptive_subjects: tuple = ()
    producer_epoch: str | None = None
    expected_consumer_id: str | None = None
    live_bindings: tuple = ()

    @classmethod
    def from_dict(cls, value, *, profile="legacy"):
        keys = {"principal_id", "producer_id", "token_sha256", "scopes", "operations", "payload_types", "revisions", "schema_pins"}
        extra = {"producer_epoch", "expected_consumer_id"} if profile in {*FEEDBACK_PROFILES, LIVE_PROFILE} else set()
        if profile == LIVE_PROFILE:
            extra.add("live_bindings")
        if not isinstance(value, dict) or not keys <= set(value) or set(value) - keys - {"expected_actor_ref", "expected_worker_id", "source_ids", "sensor_roots", "clock_mappings", "registration_bindings", "adaptive_subjects"} - extra:
            raise ValueError("explicit grant fields required")
        identifier(value["principal_id"], "principal_id")
        identifier(value["producer_id"], "producer_id")
        if not re.fullmatch(r"[a-f0-9]{64}", value["token_sha256"]):
            raise ValueError("token_sha256 required, never a default credential")
        scopes = []
        if not isinstance(value["scopes"], list) or not 1 <= len(value["scopes"]) <= 32:
            raise ValueError("bounded explicit scopes required")
        for scope in value["scopes"]:
            if not isinstance(scope, dict) or set(scope) != {"site_id", "room_id"}:
                raise ValueError("site_id and room_id grant scope required")
            scopes.append((identifier(scope["site_id"], "site_id"), identifier(scope["room_id"], "room_id")))
        for field in ("operations", "payload_types", "revisions"):
            if not isinstance(value[field], list) or len(value[field]) > 64 or len(set(value[field])) != len(value[field]):
                raise ValueError("bounded explicit grant allowlists required")
            for item in value[field]:
                identifier(item, field)
        allowed_operations = {"read", "event", "intent", "feedback", "evidence_request", "evidence_reply", "worker_commit", "revoke"}
        if profile == G2_PROFILE:
            allowed_operations |= set(G2_TYPED_OPERATIONS)
        if not set(value["operations"]) <= allowed_operations:
            raise ValueError("unknown grant operation")
        for operation in set(value["operations"]) & set(G2_TYPED_OPERATIONS):
            if G2_TYPED_OPERATIONS[operation] not in value["payload_types"] or "read" not in value["operations"]:
                raise ValueError("G2 typed issuer requires its narrow family and independent read rights")
        actor = value.get("expected_actor_ref")
        if actor is not None:
            identifier(actor, "expected_actor_ref")
        worker = value.get("expected_worker_id")
        if worker is not None:
            identifier(worker, "expected_worker_id")
        if "worker_commit" in value["operations"] and worker is None:
            raise ValueError("worker_commit grant requires explicit expected_worker_id")
        sources = value.get("source_ids", [])
        if not isinstance(sources, list) or len(sources) > 64:
            raise ValueError("bounded source_ids allowlist required")
        for source in sources:
            identifier(source, "source_ids")
        sensors = value.get("sensor_roots", [])
        if not isinstance(sensors, list) or len(sensors) > 128:
            raise ValueError("bounded sensor_roots allowlist required")
        for sensor in sensors:
            identifier(sensor, "sensor_root")
        pins = value["schema_pins"]
        if not isinstance(pins, dict) or len(pins) > 64 or any(not isinstance(v, str) or not re.fullmatch(r"[a-f0-9]{64}", v) for v in pins.values()):
            raise ValueError("explicit schema digest pins required")
        mappings = value.get("clock_mappings", {})
        if not isinstance(mappings, dict) or len(mappings) > 64:
            raise ValueError("bounded explicit clock mappings required")
        clocks = []
        for mapping_id, mapping in mappings.items():
            identifier(mapping_id, "mapping_id")
            if not isinstance(mapping, dict) or not {"source_id", "original_unit", "max_uncertainty_s"} <= set(mapping) or set(mapping) - {"source_id", "original_unit", "max_uncertainty_s", "epoch_origin_s", "source_epoch"}:
                raise ValueError("exact commissioned clock mapping fields required")
            source = identifier(mapping["source_id"], "source_id")
            unit, uncertainty = mapping["original_unit"], mapping["max_uncertainty_s"]
            if unit not in {"utc_epoch_s", "utc_epoch_ms", "utc_epoch_ns", "device_relative_s", "session_offset_s"} or not finite(uncertainty) or not 0 <= uncertainty <= 1:
                raise ValueError("explicit clock unit and bounded uncertainty required")
            origin, epoch = mapping.get("epoch_origin_s"), mapping.get("source_epoch")
            if unit in {"device_relative_s", "session_offset_s"}:
                if not finite(origin) or origin < 0:
                    raise ValueError("relative clock requires commissioned epoch origin")
                identifier(epoch, "source_epoch")
            elif origin is not None or epoch is not None:
                raise ValueError("absolute clock must not contain relative origin")
            clocks.append((mapping_id, source, unit, uncertainty, origin, epoch))
        registrations = value.get("registration_bindings", {})
        if not isinstance(registrations, dict) or len(registrations) > 64:
            raise ValueError("bounded commissioned registration bindings required")
        registration_records = []
        for registration_ref, registration in registrations.items():
            identifier(registration_ref, "registration_ref")
            if not isinstance(registration, dict) or set(registration) != {"source_id", "geometry_revision", "registry_digest", "coverage_refs"}:
                raise ValueError("exact commissioned registration fields required")
            source, revision = identifier(registration["source_id"], "source_id"), identifier(registration["geometry_revision"], "geometry_revision")
            registry_digest, coverage = registration["registry_digest"], registration["coverage_refs"]
            if not isinstance(registry_digest, str) or not re.fullmatch(r"[a-f0-9]{64}", registry_digest) or not isinstance(coverage, list) or not 1 <= len(coverage) <= 128 or len(set(coverage)) != len(coverage):
                raise ValueError("pinned registry digest and nonempty bounded coverage required")
            for ref in coverage:
                identifier(ref, "coverage_ref")
            registration_records.append((registration_ref, source, revision, registry_digest, tuple(coverage)))
        subjects = value.get("adaptive_subjects", [])
        if not isinstance(subjects, list) or len(subjects) > 16:
            raise ValueError("bounded explicit adaptive subject bindings required")
        subject_records = []
        for binding in subjects:
            if not isinstance(binding, dict) or set(binding) != {"owner_id", "scope", "subject_rights"}:
                raise ValueError("exact adaptive owner/scope/subject binding required")
            from adaptive_validation import validate_subject_authorization
            authorization = {**binding, "producer_id": value["producer_id"]}
            try:
                validate_subject_authorization(authorization, binding["scope"], allow_read_namespace=True)
            except JournalError as error:
                raise ValueError("invalid configured adaptive subject authorization") from error
            if (binding["scope"]["site_id"], binding["scope"]["room_id"]) not in scopes:
                raise ValueError("adaptive subject binding is outside grant scope")
            subject_records.append(canonical(binding))
        if len(set(subject_records)) != len(subject_records):
            raise ValueError("duplicate adaptive subject binding")
        epoch = consumer = None
        if profile in FEEDBACK_PROFILES or profile == LIVE_PROFILE and "worker_commit" in value["operations"]:
            epoch = identifier(value.get("producer_epoch"), "producer_epoch")
            consumer = identifier(value.get("expected_consumer_id"), "expected_consumer_id")
            if worker is not None and "worker_commit" in value["operations"] and consumer != worker:
                raise ValueError("E worker consumer must match its configured worker identity")
        live_bindings = []
        if profile == LIVE_PROFILE:
            bindings = value.get("live_bindings")
            if not isinstance(bindings, list) or not 1 <= len(bindings) <= 16:
                raise ValueError("explicit bounded live source bindings required")
            for binding in bindings:
                if (not isinstance(binding, dict) or set(binding) != {"manifest_ref", "source_id", "capabilities"}
                        or not isinstance(binding["manifest_ref"], dict) or set(binding["manifest_ref"]) != {"id", "digest"}
                        or not isinstance(binding["manifest_ref"]["digest"], str)
                        or not re.fullmatch(r"[a-f0-9]{64}", binding["manifest_ref"]["digest"])
                        or not isinstance(binding["capabilities"], list) or not binding["capabilities"]
                        or len(set(binding["capabilities"])) != len(binding["capabilities"])
                        or not set(binding["capabilities"]) <= {"capture", "control", "read", "revoke", "worker_commit"}):
                    raise ValueError("exact live manifest/source/capability binding required")
                identifier(binding["source_id"], "source_id")
                identifier(binding["manifest_ref"]["id"], "manifest_id")
                live_bindings.append(canonical(binding))
            if len(set(live_bindings)) != len(live_bindings):
                raise ValueError("duplicate live source binding")
        return cls(value["principal_id"], value["producer_id"], value["token_sha256"], frozenset(scopes),
                   frozenset(value["operations"]), frozenset(value["payload_types"]), frozenset(value["revisions"]), actor, frozenset(sources), frozenset(sensors), frozenset(pins.items()), tuple(clocks), tuple(registration_records), worker, tuple(subject_records), epoch, consumer, tuple(live_bindings))

    def require(self, operation, site_id, room_id, *, payload_type=None, revision=None, producer_id=None):
        if (operation not in self.operations or (site_id, room_id) not in self.scopes
                or payload_type is not None and payload_type not in self.payload_types
                or revision is not None and revision not in self.revisions
                or producer_id is not None and producer_id != self.producer_id):
            raise JournalError("forbidden", "operation is outside the configured grant", 403)


class Journal:
    def __init__(self, path, *, grants=(), limits=None, clock=time.time, evidence_router=None, profile="legacy", adaptive_config=None, adaptive_feedback_config=None, adaptive_feedback_reference_config=None, adaptive_feedback_geometry_hold_2_config=None, map_rehearsal_config=None, map_rehearsal_reopen=False, live_config=None, live_receipt_resolver=None, live_wall_clock=None, live_monotonic=None):
        self._recorder = None
        self._recorder_failed = False
        self._recorded_publications = None
        self._recorded_control_operations = None
        if profile not in {"legacy", DYNAMIC_PROFILE, MAP_REHEARSAL_PROFILE, ADAPTIVE_PROFILE, LIVE_PROFILE, *FEEDBACK_PROFILES}:
            raise ValueError("unknown journal profile")
        if profile != LIVE_PROFILE and (live_config is not None or live_receipt_resolver is not None):
            raise ValueError("live configuration requires its isolated profile")
        if profile == MAP_REHEARSAL_PROFILE:
            if (not isinstance(map_rehearsal_config, dict) or set(map_rehearsal_config) != {"identity", "binding_digest", "config_digest", "registration_generation"}
                    or clock is time.time or not isinstance(map_rehearsal_config["registration_generation"], int)):
                raise ValueError("map rehearsal requires immutable binding and explicit virtual clock")
            if Path(path).exists() and not map_rehearsal_reopen:
                raise ValueError("map rehearsal requires a fresh journal unless exact reopen is requested")
            self.map_rehearsal_config = map_rehearsal_config
        elif map_rehearsal_config is not None or map_rehearsal_reopen:
            raise ValueError("map rehearsal configuration requires its isolated profile")
        self.profile, self.dynamic = profile, None
        self.path, self.limits, self.clock = Path(path), limits or Limits(), clock
        # The synthetic profile never opens or migrates an existing ledger.
        if profile in {DYNAMIC_PROFILE, ADAPTIVE_PROFILE, *FEEDBACK_PROFILES}:
            if self.path.exists() and self.path.stat().st_size:
                raise ValueError(("dynamic" if profile == DYNAMIC_PROFILE else "adaptive") + " synthetic profile requires a fresh journal")
            self.limits = replace(self.limits,
                                  max_publications=min(self.limits.max_publications, 2048),
                                  max_publication_bytes=min(self.limits.max_publication_bytes, 12 * 1024 * 1024),
                                  max_db_bytes=min(self.limits.max_db_bytes, 48 * 1024 * 1024),
                                  max_active_requests=1)
        if profile == ADAPTIVE_PROFILE:
            from adaptive_validation import validate_configuration, SCOPE_KEYS
            if (not isinstance(adaptive_config, dict) or set(adaptive_config) != {"identity", "clock_profile", "materials", "config_digest", "config", "registered_surfaces"}
                    or not isinstance(adaptive_config["identity"], dict) or set(adaptive_config["identity"]) != set(SCOPE_KEYS)):
                raise ValueError("exact adaptive identity, clock, materials and configuration digest required")
            try:
                for name in SCOPE_KEYS:
                    identifier(adaptive_config["identity"][name], name)
                validate_configuration(adaptive_config)
            except JournalError as error:
                raise ValueError("invalid adaptive configuration") from error
            if adaptive_config["identity"]["session_id"] != adaptive_config["clock_profile"]["session_id"] or clock is time.time:
                raise ValueError("adaptive relative session requires an explicit matching clock")
            if not isinstance(adaptive_config["config_digest"], str) or not re.fullmatch(r"[a-f0-9]{64}", adaptive_config["config_digest"]):
                raise ValueError("adaptive trusted configuration digest required")
            self.adaptive_config = json.loads(canonical(adaptive_config))
        elif adaptive_config is not None:
            raise ValueError("adaptive configuration requires its isolated profile")
        if profile == FEEDBACK_PROFILE:
            from adaptive_feedback_validation import validate_configuration
            if adaptive_config is not None or clock is time.time:
                raise ValueError("feedback profile requires its native configuration and explicit session clock")
            try:
                validate_configuration(adaptive_feedback_config)
            except (JournalError, TypeError, KeyError) as error:
                raise ValueError("invalid feedback configuration") from error
            self.adaptive_feedback_config = json.loads(canonical(adaptive_feedback_config))
        elif adaptive_feedback_config is not None:
            raise ValueError("feedback configuration requires its isolated profile")
        if profile == REFERENCE_PROFILE:
            from adaptive_feedback_reference_validation import validate_configuration
            if adaptive_config is not None or adaptive_feedback_config is not None or clock is time.time:
                raise ValueError("reference feedback requires its separate native configuration and session clock")
            try:
                validate_configuration(adaptive_feedback_reference_config)
            except (JournalError, TypeError, KeyError) as error:
                raise ValueError("invalid reference feedback configuration") from error
            self.adaptive_feedback_reference_config = json.loads(canonical(adaptive_feedback_reference_config))
            # Private shared semantic helpers use this bound configuration;
            # external admission never aliases the historical profile.
            self.adaptive_feedback_config = self.adaptive_feedback_reference_config
        elif adaptive_feedback_reference_config is not None:
            raise ValueError("reference configuration requires its isolated profile")
        if profile == G2_PROFILE:
            from adaptive_feedback_geometry_hold_2_validation import validate_configuration
            if adaptive_config is not None or adaptive_feedback_config is not None or adaptive_feedback_reference_config is not None or clock is time.time:
                raise ValueError("G2 requires its own configuration and explicit local session clock")
            try:
                validate_configuration(adaptive_feedback_geometry_hold_2_config)
            except (JournalError, TypeError, KeyError) as error:
                raise ValueError("invalid G2 service configuration") from error
            self.adaptive_feedback_geometry_hold_2_config = json.loads(canonical(adaptive_feedback_geometry_hold_2_config))
            self.adaptive_feedback_config = self.adaptive_feedback_geometry_hold_2_config
        elif adaptive_feedback_geometry_hold_2_config is not None:
            raise ValueError("G2 configuration requires its separate profile")
        self.grants = tuple(Grant.from_dict(g, profile=profile) if isinstance(g, dict) else g for g in grants)
        if profile not in {*FEEDBACK_PROFILES, LIVE_PROFILE} and any(g.producer_epoch is not None or g.expected_consumer_id is not None for g in self.grants):
            raise ValueError("feedback grant fields require the isolated feedback profile")
        if len(self.grants) > 128 or len({g.token_sha256 for g in self.grants}) != len(self.grants):
            raise ValueError("bounded, unique configured grants required")
        # Tokens may rotate for one authority, but a room cannot have two reducers.
        worker_authorities = {}
        for grant in self.grants:
            if "worker_commit" not in grant.operations:
                continue
            authority = (grant.principal_id, grant.producer_id, grant.expected_worker_id)
            for scope in grant.scopes:
                if scope in worker_authorities and worker_authorities[scope] != authority:
                    raise ValueError("one configured worker authority per site and room required")
                worker_authorities[scope] = authority
        # A router is registered by composition code, never by request URL/module.
        # None is the shipped local-shadow default: no acquisition capability.
        self.evidence_router = evidence_router
        from schema_registry import SchemaRegistry
        self.schemas = SchemaRegistry(self.grants, profile=profile)
        if profile == LIVE_PROFILE:
            from live_ingress_validation import validate_config
            from live_ingress import validate_grants
            self.live_config = validate_config(live_config, self.schemas)
            validate_grants(self.grants, self.live_config)
            self.live_service_clock = None
            if self.live_config["version"] in {7, 9, 10, 11, 12, 13}:
                from live_service_clock import QualifiedServiceClock
                import time as clock_time
                import uuid as clock_uuid
                self.live_service_clock = QualifiedServiceClock(self.live_config["manifest"]["service_clock_policy"],
                    anchor_epoch="service-clock:"+clock_uuid.uuid4().hex,
                    wall=live_wall_clock or clock_time.time,monotonic=live_monotonic or clock_time.monotonic)
                def qualified_now():
                    sample=self.live_service_clock.sample()
                    if not sample["qualified"]:
                        raise JournalError("clock_unqualified",sample["reason"],503)
                    return sample["sampled_wall_at"]
                self.clock=qualified_now
            if not callable(live_receipt_resolver):
                raise ValueError("explicit owner-controlled original-receipt resolver required")
            self.live_receipt_resolver = live_receipt_resolver
            if self.path.exists() and self.path.stat().st_size:
                # Read-only preflight: never initialize or migrate a legacy ledger.
                existing = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)
                try:
                    marker = existing.execute("SELECT value FROM meta WHERE key='profile'").fetchone()
                    binding = existing.execute("SELECT value FROM meta WHERE key='live-config'").fetchone()
                    if not marker or marker[0] != LIVE_PROFILE or not binding or binding[0] != digest(self.live_config):
                        raise ValueError("live reopen requires the exact profile and immutable configuration")
                except sqlite3.Error as error:
                    raise ValueError("live profile refuses unmarked or incompatible existing journals") from error
                finally:
                    existing.close()
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, timeout=2)
        self.db.row_factory = sqlite3.Row
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone():
            marked = self.db.execute("SELECT value FROM meta WHERE key='profile'").fetchone()
            if marked and marked[0] != profile:
                self.db.close()
                raise ValueError("journal profile mismatch; no migration performed")
            if profile == MAP_REHEARSAL_PROFILE and map_rehearsal_reopen:
                bound = self.db.execute("SELECT value FROM meta WHERE key='map-rehearsal-config'").fetchone()
                if not marked or marked[0] != profile or not bound or bound[0] != canonical(map_rehearsal_config):
                    self.db.close()
                    raise ValueError("map rehearsal reopen configuration mismatch; no migration performed")
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA cache_size=-2048")
        self.db.execute("PRAGMA temp_store=MEMORY")
        page_size = self.db.execute("PRAGMA page_size").fetchone()[0]
        page_count = self.db.execute("PRAGMA page_count").fetchone()[0]
        if page_count * page_size > self.limits.max_db_bytes:
            self.db.close()
            raise ValueError("existing journal exceeds configured disk budget")
        self.db.execute(f"PRAGMA max_page_count={self.limits.max_db_bytes // page_size}")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS publications(cursor INTEGER PRIMARY KEY,site_id TEXT NOT NULL,room_id TEXT NOT NULL,
                source_id TEXT NOT NULL,revision TEXT NOT NULL,payload_type TEXT NOT NULL,body TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS publications_scope ON publications(site_id,room_id,cursor);
            CREATE TABLE IF NOT EXISTS receipts(producer_id TEXT NOT NULL,source_epoch TEXT NOT NULL,event_id TEXT NOT NULL,
                digest TEXT NOT NULL,receipt TEXT NOT NULL,event TEXT NOT NULL,application_result TEXT,
                PRIMARY KEY(producer_id,source_epoch,event_id));
            CREATE INDEX IF NOT EXISTS receipts_cursor ON receipts(json_extract(receipt,'$.cursor'))
                WHERE json_extract(receipt,'$.schema')='home-context-receipt-1';
            CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY,site_id TEXT NOT NULL,room_id TEXT NOT NULL,
                source_id TEXT NOT NULL,revision TEXT NOT NULL,valid_until REAL NOT NULL,body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS requests(request_id TEXT PRIMARY KEY,active_key TEXT NOT NULL,site_id TEXT NOT NULL,
                room_id TEXT NOT NULL,deadline REAL NOT NULL,terminal TEXT,body TEXT NOT NULL,receipt TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS requests_active ON requests(active_key,terminal,deadline);
            CREATE TABLE IF NOT EXISTS workers(worker_id TEXT NOT NULL,site_id TEXT NOT NULL,room_id TEXT NOT NULL,
                input_cursor INTEGER NOT NULL,context_seq INTEGER NOT NULL,checkpoint TEXT NOT NULL,
                PRIMARY KEY(worker_id,site_id,room_id));
            CREATE TABLE IF NOT EXISTS consumers(principal_id TEXT NOT NULL,consumer_id TEXT NOT NULL,site_id TEXT NOT NULL,
                room_id TEXT NOT NULL,delivered INTEGER NOT NULL,PRIMARY KEY(principal_id,consumer_id,site_id,room_id));
            CREATE TABLE IF NOT EXISTS revocations(site_id TEXT NOT NULL,room_id TEXT NOT NULL,source_id TEXT NOT NULL,
                revision TEXT NOT NULL,PRIMARY KEY(site_id,room_id,source_id,revision));
            CREATE TABLE IF NOT EXISTS original_roots(scope TEXT NOT NULL,observation_root TEXT NOT NULL,sensor_digest TEXT NOT NULL,observed_at REAL,
                PRIMARY KEY(scope,observation_root));
        """)
        if "application_result" not in {row[1] for row in self.db.execute("PRAGMA table_info(receipts)")}:
            self.db.execute("ALTER TABLE receipts ADD COLUMN application_result TEXT")
        if "observed_at" not in {row[1] for row in self.db.execute("PRAGMA table_info(original_roots)")}:
            self.db.execute("ALTER TABLE original_roots ADD COLUMN observed_at REAL")
        for key, value in (("epoch", str(uuid.uuid4())), ("cursor", "0"), ("retained_after", "0")):
            self.db.execute("INSERT OR IGNORE INTO meta VALUES(?,?)", (key, value))
        if profile == LIVE_PROFILE:
            from live_ingress import LiveIngressProfile
            try:
                self.dynamic = LiveIngressProfile(self)
            except Exception:
                self.db.close()
                raise
        elif profile == MAP_REHEARSAL_PROFILE:
            self._set_meta("profile", profile)
            from map_rehearsal_validation import MapRehearsalProfile
            self.dynamic = MapRehearsalProfile(self)
        elif profile == DYNAMIC_PROFILE:
            self._set_meta("profile", profile)
            from dynamic_validation import DynamicProfile
            self.dynamic = DynamicProfile(self)
        elif profile == ADAPTIVE_PROFILE:
            self._set_meta("profile", profile)
            from adaptive_validation import AdaptiveProfile
            try:
                self.dynamic = AdaptiveProfile(self)
            except Exception:
                self.db.close()
                raise
        elif profile == FEEDBACK_PROFILE:
            self._set_meta("profile", profile)
            from adaptive_feedback_validation import AdaptiveFeedbackProfile
            try:
                self.dynamic = AdaptiveFeedbackProfile(self)
            except Exception:
                self.db.close()
                raise
        elif profile == REFERENCE_PROFILE:
            self._set_meta("profile", profile)
            from adaptive_feedback_reference_validation import AdaptiveFeedbackReferenceProfile
            try:
                self.dynamic = AdaptiveFeedbackReferenceProfile(self)
            except Exception:
                self.db.close()
                raise
        elif profile == G2_PROFILE:
            self._set_meta("profile", profile)
            from adaptive_feedback_geometry_hold_2_validation import AdaptiveFeedbackGeometryHold2Profile
            try:
                self.dynamic = AdaptiveFeedbackGeometryHold2Profile(self)
            except Exception:
                self.db.close()
                raise

    @property
    def stream_epoch(self):
        with self.lock:
            return self._meta("epoch")

    def close(self):
        with self.lock:
            self.db.close()

    def authenticate(self, bearer):
        if not isinstance(bearer, str) or not 1 <= len(bearer) <= 4096:
            raise JournalError("unauthorized", "explicit bearer grant required", 401)
        supplied = hashlib.sha256(bearer.encode()).hexdigest()
        matches = [g for g in self.grants if hmac.compare_digest(g.token_sha256, supplied)]
        if len(matches) != 1:
            raise JournalError("unauthorized", "explicit bearer grant required", 401)
        return matches[0]

    def _meta(self, key, default=None):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def _set_meta(self, key, value):
        self.db.execute("INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))

    def attach_recorder(self, recorder):
        """Trusted composition capability; no route may select an archive writer."""
        if self.profile != LIVE_PROFILE or not callable(recorder):
            raise ValueError("live recorder capability required")
        with self.lock:
            if self._recorder is not None or self._recorder_failed or int(self._meta("cursor")) != 0:
                raise ValueError("recorder must attach exactly once before source admission")
            self._recorder = recorder

    def _observe_publication(self, event, receipt, publication):
        if self._recorder is not None:
            if self._recorded_publications is None:
                raise JournalError("recorder_unavailable", "publication requires recorded transaction", 503)
            self._recorded_publications.append((event, receipt, publication))

    def _observe_control_operation(self, operation, receipt):
        """Private successor capability: original authority joins the same SQL tx."""
        if self._recorder is None or self._recorded_control_operations is None:
            raise JournalError("recorder_unavailable", "control authority requires recorded transaction", 503)
        self._recorded_control_operations.append({"operation": operation, "receipt": receipt})

    def _transaction(self, operation):
        with self.lock:
            if self._recorder_failed:
                raise JournalError("recorder_unavailable", "session evidence is incomplete; new admission stopped", 503)
            self._recorded_publications = []
            self._recorded_control_operations = []
            prepared = False
            tx_id = None
            try:
                self.db.execute("BEGIN IMMEDIATE")
                result = operation()
                bounded_json(result, self.limits.max_response_bytes)
                if self._recorder is not None and (self._recorded_publications or self._recorded_control_operations):
                    tx_id = str(uuid.uuid4())
                    originals = {}
                    for event, _, _ in self._recorded_publications:
                        for ref in event["payload"].get("original_receipt_refs", []):
                            key = (ref["store_id"], ref["receipt_id"])
                            if key not in originals:
                                originals[key] = self.live_receipt_resolver(*key)
                    try:
                        preparation = {
                            "tx_id": tx_id,
                            "publications": [p for _, _, p in self._recorded_publications],
                            "events": [e for e, _, _ in self._recorded_publications],
                            "receipts": [r for _, r, _ in self._recorded_publications],
                            "original_receipts": list(originals.values())}
                        if self._recorded_control_operations:
                            preparation["control_operations"] = self._recorded_control_operations
                        self._recorder({"kind": "journal_prepare", "data": preparation})
                        prepared = True
                    except BaseException:
                        self._recorder_failed = True
                        raise JournalError("recorder_unavailable", "cannot durably prepare session evidence", 503) from None
                self.db.execute("COMMIT")
                if prepared:
                    try:
                        self._recorder({"kind": "journal_commit", "data": {"tx_id": tx_id, "result": result}})
                    except BaseException:
                        self._recorder_failed = True
                        raise JournalError("recorder_unavailable", "journal committed but archive confirmation failed; session incomplete", 503) from None
                return result
            except sqlite3.Error as error:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                if (getattr(error, "sqlite_errorcode", 0) & 255) in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
                    raise JournalError("journal_busy", "durable journal is temporarily locked; retry the exact request", 503) from error
                raise JournalError("journal_unavailable", "durable journal admission failed", 507) from error
            except BaseException:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                    if prepared:
                        try:
                            self._recorder({"kind": "journal_abort", "data": {"tx_id": tx_id}})
                        except BaseException:
                            self._recorder_failed = True
                raise
            finally:
                self._recorded_publications = None
                self._recorded_control_operations = None

    def _scope(self, value):
        for field in ("site_id", "room_id", "session_id", "domain_epoch"):
            identifier(value.get(field), field)
        if value.get("person_ref") is not None:
            check_person_ref(value["person_ref"], value)

    def _event(self, event, *, max_bytes=None):
        if not isinstance(event, dict) or set(event) != EVENT_KEYS:
            raise JournalError("invalid_envelope", "exact home-context-event-1 envelope required")
        if event["schema"] != "home-context-event-1" or event["schema_version"] != "1.0":
            raise JournalError("unsupported_schema", "unsupported event schema/version")
        self._scope(event)
        for key in ("id", "producer_id", "stream_epoch", "payload_type", "payload_version", "revision"):
            identifier(event[key], key)
        if not all(finite(event[k]) for k in ("observed_at", "available_at", "valid_until")):
            raise JournalError("invalid_clock", "epoch seconds required")
        dynamic_source = (self.profile in {DYNAMIC_PROFILE, MAP_REHEARSAL_PROFILE} and event["payload_type"] in {"home-geometry-3", "home-fact-batch-2"}
                          or self.profile in {ADAPTIVE_PROFILE, *FEEDBACK_PROFILES})
        if dynamic_source:
            # Synthetic source admission may precede interpretation availability.
            # The worker and qualified-head materializer buffer the original body.
            causal = (0 <= event["observed_at"] <= self.clock()
                      and event["observed_at"] <= event["available_at"] <= 120
                      and event["available_at"] < event["valid_until"])
            if self.profile in {ADAPTIVE_PROFILE, *FEEDBACK_PROFILES} and "valid_until" not in event["payload"]:
                causal = (0 <= event["observed_at"] <= self.clock()
                          and event["observed_at"] <= event["available_at"] <= event["valid_until"] <= 120)
            if self.profile in {ADAPTIVE_PROFILE, *FEEDBACK_PROFILES}:
                causal = causal and event["valid_until"] <= 120
        else:
            causal = (0 <= event["observed_at"] <= event["available_at"] <= self.clock()
                      and event["valid_until"] >= event["observed_at"])
        if not causal:
            raise JournalError("invalid_clock", "causal epoch-second interval required")
        payload = event["payload"]
        self.schemas.validate(payload)
        if self.profile in {DYNAMIC_PROFILE, MAP_REHEARSAL_PROFILE, ADAPTIVE_PROFILE, *FEEDBACK_PROFILES, LIVE_PROFILE} and event["payload_type"] in self.schemas.payload_versions:
            version = str(self.schemas.payload_versions[event["payload_type"]]) + ".0"
        else:
            version = str(payload.get("schema_version", "1.0" if type(payload.get("version")) is int and payload["version"] == 1 else "unsupported")) if isinstance(payload, dict) else None
        if not isinstance(payload, dict) or payload.get("schema") != event["payload_type"] or version != event["payload_version"]:
            raise JournalError("invalid_payload", "typed versioned payload must match envelope")
        for key in ("site_id", "room_id", "session_id", "domain_epoch"):
            if key in payload and payload[key] != event[key]:
                raise JournalError("scope_mismatch", "payload belongs to another scope")
        if payload.get("person_ref") is not None:
            check_person_ref(payload["person_ref"], event)
        _no_private_fields(payload)
        if self.dynamic and event["payload_type"] in self.schemas.payload_versions:
            self.dynamic.validate_payload(payload)
        if event["payload_type"] == "home-lighting-shadow-decision-1" and payload.get("actuated") is not False:
            raise JournalError("shadow_only", "shadow decision must explicitly have actuated false")
        if event["payload_type"] == "fact-1":
            from validation import fact
            fact(payload)
            if any(payload[k] != event[k] for k in ("observed_at", "available_at", "valid_until")):
                raise JournalError("invalid_clock", "fact clocks must equal source envelope clocks")
        if event["payload_type"] in {"home-source-health-1", "home-coverage-1"}:
            from validation import source_state
            source_state(payload)
            if any(payload[k] != event[k] for k in ("observed_at", "available_at", "valid_until")):
                raise JournalError("invalid_clock", "source status clocks must equal envelope clocks")
        if event["payload_type"] == "home-geometry-1" and any(payload[k] != event[k] for k in ("observed_at", "available_at", "valid_until")):
            raise JournalError("invalid_clock", "geometry clocks must equal source envelope clocks")
        if event["payload_type"] == "home-engagement-state-1":
            if payload["t"] != event["observed_at"] or payload["valid_until"] != event["valid_until"] or payload["valid_until"] <= payload["t"]:
                raise JournalError("invalid_clock", "engagement state clocks must match its publication envelope")
            for person in payload["persons"]:
                check_person_ref(person["person_ref"], payload)
        if event["payload_type"] == "home-relation-hypothesis-1":
            if any(payload[k] != event[k] for k in ("observed_at", "available_at", "valid_until")) or payload["valid_until"] <= payload["observed_at"]:
                raise JournalError("invalid_clock", "hypothesis clocks must match its original producer envelope")
            probability, calibration = payload["probability"], payload.get("calibration")
            if probability is not None and (not isinstance(calibration, dict) or calibration.get("status") != "fitted"):
                raise JournalError("invalid_probability", "hypothesis probability requires fitted calibration")
            if probability is not None:
                for key in ("calibrator_id", "calibration_set_id"):
                    identifier(calibration.get(key), key)
        bounded_json(event, self.limits.max_body_bytes if max_bytes is None else max_bytes)
        return event

    def worker_commit_body_limit(self):
        if self.profile == LIVE_PROFILE and getattr(getattr(self, 'dynamic', None), 'worker', None) is not None:
            return self.dynamic.worker.commit_body_limit()
        return self.limits.max_worker_commit_bytes

    def _source(self, event):
        return event["producer_id"] if event["payload_type"] == "home-context-revocation-1" else event["payload"].get("source_id", event["producer_id"])

    def _revoked(self, site, room, source, revision):
        return self.db.execute("SELECT 1 FROM revocations WHERE site_id=? AND room_id=? AND source_id IN (?, '*') AND revision IN (?, '*')",
                               (site, room, source, revision)).fetchone() is not None

    def _receipt(self, event):
        row = self.db.execute("SELECT * FROM receipts WHERE producer_id=? AND source_epoch=? AND event_id=?",
                              (event["producer_id"], event["stream_epoch"], event["id"])).fetchone()
        if row:
            if row["digest"] != digest(event):
                raise JournalError("identity_conflict", "event identity was already committed with another payload", 409)
            return json.loads(row["receipt"])
        return None

    def _append(self, event, *, context_seq=None, status="accepted"):
        if self.dynamic:
            return self.dynamic.append(event, context_seq=context_seq, status=status)
        retry = self._receipt(event)
        if retry:
            return retry
        self._prune()
        if event["payload_type"] == "fact-1":
            self._bind_original_roots(event)
        source = identifier(self._source(event), "source_id")
        if self._revoked(event["site_id"], event["room_id"], source, event["revision"]) and event["payload_type"] != "home-context-revocation-1":
            raise JournalError("revoked", "source or revision is revoked", 409)
        if self.db.execute("SELECT count(*) FROM receipts").fetchone()[0] >= self.limits.max_receipts:
            raise JournalError("journal_full", "durable identity budget exhausted; explicit journal rotation required", 507)
        cursor = int(self._meta("cursor")) + 1
        committed = self.clock()
        payload_digest = digest(event["payload"])
        publication = {"schema": "home-context-publication-1", "id": event["id"], "producer_id": event["producer_id"],
                       "stream_epoch": self._meta("epoch"), "cursor": cursor,
                       "context_seq": cursor if context_seq is None else context_seq, "committed_at": committed,
                       "payload_type": event["payload_type"], "payload_version": event["payload_version"],
                       "payload": event["payload"], "payload_digest": payload_digest}
        body = bounded_json(publication, self.limits.max_body_bytes)
        self.db.execute("INSERT INTO publications VALUES(?,?,?,?,?,?,?)", (cursor, event["site_id"], event["room_id"], source,
                                                                         event["revision"], event["payload_type"], body))
        receipt = {"schema": "home-context-receipt-1", "id": event["id"], "producer_id": event["producer_id"],
                   "stream_epoch": self._meta("epoch"), "cursor": cursor, "committed_at": committed,
                   "payload_digest": payload_digest, "status": status}
        stored_event = self._tombstone(event) if event["payload_type"] in {"home-context-1", "home-lighting-shadow-decision-1", "home-engagement-state-1"} else event
        self.db.execute("INSERT INTO receipts(producer_id,source_epoch,event_id,digest,receipt,event) VALUES(?,?,?,?,?,?)", (event["producer_id"], event["stream_epoch"], event["id"],
                                                                   digest(event), canonical(receipt), canonical(stored_event)))
        # Commands remain journal evidence; the snapshot has latest producer/subject/type state.
        subject = event["payload"].get("region_id", event["payload"].get("surface_id", "room"))
        person = canonical(event["payload"].get("person_ref"))
        key = canonical([event["site_id"], event["room_id"], source, event["payload_type"], event["payload"].get("kind"), subject, person])
        if event["valid_until"] > committed:
            self.db.execute("DELETE FROM state WHERE valid_until<=?", (committed,))
            exists = self.db.execute("SELECT 1 FROM state WHERE key=?", (key,)).fetchone()
            if not exists and self.db.execute("SELECT count(*) FROM state").fetchone()[0] >= self.limits.max_state_items:
                raise JournalError("journal_full", "materialized state budget exhausted", 507)
            self.db.execute("INSERT INTO state VALUES(?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET revision=excluded.revision,valid_until=excluded.valid_until,body=excluded.body",
                            (key, event["site_id"], event["room_id"], source, event["revision"], event["valid_until"], body))
            if self.db.execute("SELECT coalesce(sum(length(CAST(body AS BLOB))),0) FROM state").fetchone()[0] > self.limits.max_state_bytes:
                raise JournalError("journal_full", "materialized state byte budget exhausted", 507)
        self._set_meta("cursor", cursor)
        floor = max(int(self._meta("retained_after")), cursor - self.limits.max_publications)
        self.db.execute("DELETE FROM publications WHERE cursor<=?", (floor,))
        self._set_meta("retained_after", floor)
        self._prune()
        return receipt

    @staticmethod
    def _tombstone(event):
        return {"schema": "home-context-event-tombstone-1", **{k: event[k] for k in ("id", "site_id", "room_id", "session_id", "domain_epoch", "observed_at", "available_at", "valid_until", "payload_type", "revision")},
                "producer_id": event["producer_id"], "payload": {"schema": event["payload_type"], "source_id": event["payload"].get("source_id", event["producer_id"])}}

    def _prune(self):
        if self.dynamic:
            return  # An admitted synthetic scenario never prunes evidence.
        cutoff = self.clock() - self.limits.retention_seconds
        old = self.db.execute("SELECT max(cursor) FROM publications WHERE json_extract(body,'$.committed_at')<?", (cutoff,)).fetchone()[0]
        floor = max(int(self._meta("retained_after")), old or 0)
        self.db.execute("DELETE FROM publications WHERE cursor<=?", (floor,))
        size = self.db.execute("SELECT coalesce(sum(length(CAST(body AS BLOB))),0) FROM publications").fetchone()[0]
        if size > self.limits.max_publication_bytes:
            for row in self.db.execute("SELECT cursor,length(CAST(body AS BLOB)) FROM publications ORDER BY cursor").fetchall():
                if size <= self.limits.max_publication_bytes:
                    break
                floor = max(floor, row[0]); size -= row[1]
            self.db.execute("DELETE FROM publications WHERE cursor<=?", (floor,))
        self._set_meta("retained_after", floor)
        # Hash/receipt identity remains durable after large source bodies expire.
        rows = self.db.execute("SELECT producer_id,source_epoch,event_id,event FROM receipts WHERE json_extract(event,'$.schema')='home-context-event-1' AND (json_extract(event,'$.payload_type')='fact-1' AND json_extract(event,'$.valid_until')<=? OR json_extract(receipt,'$.committed_at')<?) LIMIT 128", (self.clock(), cutoff)).fetchall()
        for row in rows:
            event = json.loads(row["event"])
            self.db.execute("UPDATE receipts SET event=? WHERE producer_id=? AND source_epoch=? AND event_id=?", (canonical(self._tombstone(event)), row["producer_id"], row["source_epoch"], row["event_id"]))
        self.db.execute("DELETE FROM requests WHERE terminal IS NOT NULL AND deadline<?", (cutoff,))

    def _bind_original_roots(self, event):
        payload = event["payload"]
        sensors, observations = payload["sensor_roots"], payload["observation_roots"]
        bindings = payload.get("sensor_root_bindings")
        if bindings is None:
            if len(sensors) > 1:
                raise JournalError("root_bindings_required", "multi-sensor facts need per-observation original sensor bindings")
            bindings = {root: list(sensors) for root in observations}
        if not isinstance(bindings, dict) or set(bindings) != set(observations):
            raise JournalError("invalid_root_bindings", "every observation root needs an exact sensor binding")
        union = set()
        scope = canonical([event[k] for k in ("site_id", "room_id", "session_id", "domain_epoch")])
        for root, physical in bindings.items():
            if not isinstance(physical, list) or not physical or any(not isinstance(p, str) for p in physical) or len(set(physical)) != len(physical) or not set(physical) <= set(sensors):
                raise JournalError("invalid_root_bindings", "original physical sensor bindings required")
            union.update(physical)
            identity = digest(sorted(physical))
            observed = payload.get("observation_root_times", {}).get(root, payload["observed_at"])
            prior = self.db.execute("SELECT sensor_digest,observed_at FROM original_roots WHERE scope=? AND observation_root=?", (scope, root)).fetchone()
            if prior and prior[0] != identity:
                raise JournalError("root_conflict", "original observation root cannot change physical ancestry", 409)
            if prior and prior[1] != observed:
                raise JournalError("root_time_conflict", "original observation root cannot change source observation time", 409)
            self.db.execute("INSERT OR IGNORE INTO original_roots VALUES(?,?,?,?)", (scope, root, identity, observed))
        if union != set(sensors):
            raise JournalError("invalid_root_bindings", "sensor roots must equal the explicit original binding union")

    def append_event(self, event, grant):
        if self.dynamic:
            return self.dynamic.append_event(event, grant)
        self._event(event)
        grant.require("event", event["site_id"], event["room_id"], payload_type=event["payload_type"], revision=event["revision"], producer_id=event["producer_id"])
        if event["payload_type"] in {"fact-1", "home-source-health-1", "home-coverage-1"} and event["payload"].get("source_id") not in grant.source_ids:
            raise JournalError("forbidden", "fact source_id is outside the configured producer grant", 403)
        if event["payload_type"] == "fact-1" and not set(event["payload"]["sensor_roots"]) <= grant.sensor_roots:
            raise JournalError("forbidden", "fact physical sensor roots are outside the configured producer grant", 403)
        if event["payload_type"] in {"fact-1", "home-geometry-1"}:
            self._grant_clock(event, grant)
            self._grant_registration(event, grant)
        if event["payload_type"] in {"home-engagement-state-1", "home-lighting-shadow-decision-1"}:
            raise JournalError("worker_commit_required", "shared worker output requires an atomic worker commit")
        # Typed commands must use their routing methods for lifecycle/actor checks.
        if event["payload_type"] in {"home-evidence-request-1", "home-evidence-reply-1", "home-intent-event-1", "home-context-consumer-feedback-1", "home-context-revocation-1"}:
            raise JournalError("typed_route_required", "typed command requires its dedicated route")
        return self._transaction(lambda: self._append(event))

    def _grant_clock(self, event, grant):
        from producer_adapters import clock_seconds
        clock = event["payload"].get("clock", {})
        matches = [m for m in grant.clock_mappings if m[0] == clock.get("mapping_id")]
        if len(matches) != 1:
            raise JournalError("forbidden", "clock mapping is outside the commissioned producer grant", 403)
        _, source, unit, uncertainty, origin, epoch = matches[0]
        if clock.get("status") != "commissioned" or source != self._source(event) or unit != clock.get("original_unit") or not finite(clock.get("uncertainty_s")) or clock["uncertainty_s"] > uncertainty:
            raise JournalError("forbidden", "clock source, unit or uncertainty does not match its grant", 403)
        mapping = {"status": "commissioned", "mapping_id": clock["mapping_id"], "uncertainty_s": clock["uncertainty_s"]}
        if origin is not None:
            mapping.update(epoch_origin_s=origin, source_epoch=epoch)
        converted, _ = clock_seconds(clock.get("original_value"), unit, mapping)
        observed = event["payload"].get("observed_at")
        if not finite(observed) or abs(converted - observed) > 0.000001:
            raise JournalError("invalid_clock", "original capture clock must map to declared observation time")

    def _grant_registration(self, event, grant):
        spatial = event["payload"] if event["payload_type"] == "home-geometry-1" else event["payload"].get("spatial")
        if spatial is None:
            return
        matches = [r for r in grant.registration_bindings if r[0] == spatial.get("registration_ref")]
        if len(matches) != 1:
            raise JournalError("forbidden", "spatial registration is outside the commissioned producer grant", 403)
        _, source, revision, registry_digest, coverage = matches[0]
        supplied = spatial.get("coverage_refs")
        if (source != self._source(event) or revision != spatial.get("geometry_revision") or registry_digest != spatial.get("registry_digest")
                or not isinstance(supplied, list) or not supplied or not set(supplied) <= set(coverage)):
            raise JournalError("forbidden", "spatial source, registry or coverage does not match its commissioned grant", 403)

    def _delivered(self, grant, consumer, site, room, cursor):
        identifier(consumer, "consumer_id")
        row = self.db.execute("SELECT delivered FROM consumers WHERE principal_id=? AND consumer_id=? AND site_id=? AND room_id=?",
                              (grant.principal_id, consumer, site, room)).fetchone()
        if row is None and self.db.execute("SELECT count(*) FROM consumers").fetchone()[0] >= self.limits.max_consumers:
            raise JournalError("journal_full", "consumer budget exhausted", 507)
        self.db.execute("INSERT INTO consumers VALUES(?,?,?,?,?) ON CONFLICT(principal_id,consumer_id,site_id,room_id) DO UPDATE SET delivered=max(delivered,excluded.delivered)",
                        (grant.principal_id, consumer, site, room, cursor))

    def _epoch_cursor(self, epoch, after):
        floor, tail = int(self._meta("retained_after")), int(self._meta("cursor"))
        if epoch is not None and epoch != self._meta("epoch") or type(after) is not int or after < floor or after > tail:
            raise JournalError("reset_required", "cursor is outside the retained journal", 409,
                               stream_epoch=self._meta("epoch"), retained_after=floor, cursor=tail)
        return tail

    def snapshot(self, grant, site, room, consumer_id=None):
        if self.dynamic:
            return self.dynamic.snapshot(grant, site, room, consumer_id)
        grant.require("read", site, room)
        def read():
            self._prune()
            self._expire_requests(site, room)
            cursor = int(self._meta("cursor"))
            rows = self.db.execute("SELECT body,revision FROM state WHERE site_id=? AND room_id=? AND valid_until>?", (site, room, self.clock())).fetchall()
            rows = [r for r in rows if r["revision"] in grant.revisions and json.loads(r["body"])["payload_type"] in grant.payload_types]
            items = sorted((json.loads(r[0]) for r in rows), key=lambda p: p["cursor"])
            self._delivered(grant, consumer_id or grant.principal_id, site, room, cursor)
            return {"schema": "home-context-snapshot-1", "stream_epoch": self._meta("epoch"), "cursor": cursor, "items": items, "revocation_generation": self.revocation_generation(site, room)}
        return self._transaction(read)

    def page(self, grant, site, room, *, after=0, stream_epoch=None, limit=128, consumer_id=None):
        if self.dynamic:
            return self.dynamic.page(grant, site, room, after=after, stream_epoch=stream_epoch, limit=limit, consumer_id=consumer_id)
        grant.require("read", site, room)
        if type(limit) is not int or not 1 <= limit <= 128:
            raise JournalError("invalid_query", "limit must be 1..128")
        def read():
            self._prune()
            self._expire_requests(site, room)
            tail = self._epoch_cursor(stream_epoch, after)
            allowed_types, allowed_revisions = sorted(grant.payload_types), sorted(grant.revisions)
            if not allowed_types or not allowed_revisions:
                self._delivered(grant, consumer_id or grant.principal_id, site, room, tail)
                return {"schema": "home-context-page-1", "stream_epoch": self._meta("epoch"), "after": after, "items": [], "next_cursor": tail, "revocation_generation": self.revocation_generation(site, room)}
            query = "SELECT body FROM publications WHERE site_id=? AND room_id=? AND cursor>? AND payload_type IN (%s) AND revision IN (%s) ORDER BY cursor LIMIT ?" % (','.join('?' for _ in allowed_types), ','.join('?' for _ in allowed_revisions))
            rows = self.db.execute(query, (site, room, after, *allowed_types, *allowed_revisions, limit))
            items, used, byte_limited = [], 2048, False
            for row in rows:
                size = len(row[0].encode("utf-8"))
                if used + size > self.limits.max_response_bytes:
                    byte_limited = True
                    break
                items.append(json.loads(row[0])); used += size
            next_cursor = items[-1]["cursor"] if items and (len(items) == limit or byte_limited) else tail
            self._delivered(grant, consumer_id or grant.principal_id, site, room, next_cursor)
            return {"schema": "home-context-page-1", "stream_epoch": self._meta("epoch"), "after": after, "items": items, "next_cursor": next_cursor, "revocation_generation": self.revocation_generation(site, room)}
        return self._transaction(read)

    def _command_event(self, payload, grant, *, event_id=None, scope=None, observed=None, available=None, valid=None):
        successor = (isinstance(payload, dict) and payload.get("schema") == "home-live-intent-event-2"
                     and type(payload.get("version")) is int and payload["version"] == 2
                     and getattr(self, "live_config", None) is not None and self.live_config.get("version") >= 6)
        declaration = (isinstance(payload, dict) and payload.get("schema") == "home-live-intent-event-3"
                       and type(payload.get("version")) is int and payload["version"] == 3
                       and getattr(self, "live_config", None) is not None and self.live_config.get("version") in {10, 11})
        manual = (isinstance(payload, dict) and payload.get("schema") == "home-live-intent-event-4"
                  and type(payload.get("version")) is int and payload["version"] == 4
                  and getattr(self, "live_config", None) is not None and self.live_config.get("version") == 11)
        if not successor and not declaration and not manual and (not isinstance(payload, dict) or type(payload.get("version")) is not int or payload["version"] != 1):
            raise JournalError("unsupported_schema", "explicit typed command version required")
        scope = payload if scope is None else scope
        self._scope(scope)
        now = self.clock()
        observed = payload.get("set_at", now) if observed is None else observed
        available = payload.get("available_at", now) if available is None else available
        valid = payload.get("valid_until", payload.get("deadline", now)) if valid is None else valid
        event = {"schema": "home-context-event-1", "schema_version": "1.0", "id": event_id or payload.get("id"),
                 "producer_id": grant.producer_id, "stream_epoch": self.stream_epoch,
                 **{k: scope[k] for k in ("site_id", "room_id", "session_id", "domain_epoch")},
                 "observed_at": observed, "available_at": available, "valid_until": valid,
                 "payload_type": payload["schema"], "payload_version": "4.0" if manual else "3.0" if declaration else "2.0" if successor else "1.0", "revision": "4.0" if manual else "3.0" if declaration else "2.0" if successor else "1.0", "payload": payload}
        return self._event(event)

    def _command_retry(self, payload, grant, event_id):
        row = self.db.execute("SELECT * FROM receipts WHERE producer_id=? AND source_epoch=? AND event_id=?", (grant.producer_id, self.stream_epoch, event_id)).fetchone()
        if row:
            saved_receipt = json.loads(row["receipt"])
            if saved_receipt.get("payload_digest") != digest(payload):
                raise JournalError("identity_conflict", "command identity already has another payload", 409)
            return json.loads(row["receipt"])
        return None

    def request_evidence(self, payload, grant):
        if self.dynamic:
            return self.dynamic.request_evidence(payload, grant)
        if not isinstance(payload, dict) or payload.get("schema") != "home-evidence-request-1":
            raise JournalError("unsupported_schema", "home-evidence-request-1 required")
        from validation import fields, typed_version
        fields(payload, {"schema", "version", "request_id", "correlation_id", "consumer_id", "site_id", "room_id", "session_id", "domain_epoch",
                         "surface_id", "region_id", "geometry_revision", "existing_evidence_refs", "distinction", "permitted_precision", "max_observation_age_s", "deadline", "budget"}, {"person_ref"})
        typed_version(payload, "home-evidence-request-1")
        self._scope(payload)
        for key in ("request_id", "correlation_id", "consumer_id", "surface_id", "region_id", "geometry_revision", "distinction", "permitted_precision"):
            identifier(payload.get(key), key)
        if payload["distinction"] not in {"manipulation_vs_proximity", "material_use_vs_visible", "person_association", "attributed_pc_vs_active", "continued_engagement"} or payload["permitted_precision"] not in {"registered_surface", "registered_region"}:
            raise JournalError("invalid_payload", "unknown evidence distinction or precision")
        grant.require("evidence_request", payload["site_id"], payload["room_id"], payload_type=payload["schema"], revision="1.0")
        if not finite(payload.get("deadline")):
            raise JournalError("invalid_clock", "finite request deadline required")
        if not finite(payload.get("max_observation_age_s")) or not 0 <= payload["max_observation_age_s"] <= 10:
            raise JournalError("invalid_payload", "bounded observation age required")
        budget = payload.get("budget")
        if (not isinstance(budget, dict) or set(budget) != {"frames", "model_calls", "wall_s"}
                or type(budget["frames"]) is not int or not 0 <= budget["frames"] <= 4
                or type(budget["model_calls"]) is not int or not 0 <= budget["model_calls"] <= 1
                or not finite(budget["wall_s"]) or not 0 <= budget["wall_s"] <= 3):
            raise JournalError("invalid_payload", "bounded evidence budget required")
        self._refs(payload.get("existing_evidence_refs"), allow_empty=True)
        def accept():
            retry = self._command_retry(payload, grant, payload["request_id"])
            if retry:
                return retry
            if payload["deadline"] <= self.clock():
                raise JournalError("expired", "request deadline has elapsed", 409)
            active = canonical([payload[k] for k in ("site_id", "room_id", "session_id", "domain_epoch")] +
                               [payload.get("person_ref"), payload["region_id"], payload["distinction"]])
            if self.db.execute("SELECT 1 FROM requests WHERE active_key=? AND terminal IS NULL AND deadline>?", (active, self.clock())).fetchone():
                raise JournalError("request_active", "one active request per scoped person-region-distinction", 409)
            if self.db.execute("SELECT count(*) FROM requests WHERE terminal IS NULL AND deadline>?", (self.clock(),)).fetchone()[0] >= self.limits.max_active_requests:
                raise JournalError("request_budget", "active evidence request budget exhausted", 429)
            if self.db.execute("SELECT 1 FROM requests WHERE request_id=?", (payload["request_id"],)).fetchone():
                raise JournalError("identity_conflict", "request id already belongs to another producer", 409)
            event = self._command_event(payload, grant, event_id=payload["request_id"])
            receipt = self._append(event, status="pending")
            self.db.execute("INSERT INTO requests VALUES(?,?,?,?,?,NULL,?,?)", (payload["request_id"], active, payload["site_id"], payload["room_id"], payload["deadline"], canonical(payload), canonical(receipt)))
            if self.evidence_router is None:
                self._lifecycle_reply(payload, "unavailable", "source_not_provisioned")
            return receipt
        return self._transaction(accept)

    @staticmethod
    def _refs(refs, *, allow_empty):
        if not isinstance(refs, list) or len(refs) > 128 or any(not isinstance(ref, str) for ref in refs) or not allow_empty and not refs or len(set(refs)) != len(refs):
            raise JournalError("invalid_evidence_refs", "bounded unique evidence references required")
        for ref in refs:
            identifier(ref, "evidence_ref")

    def reply_evidence(self, payload, grant):
        if self.dynamic:
            return self.dynamic.reply_evidence(payload, grant)
        if not isinstance(payload, dict) or payload.get("schema") != "home-evidence-reply-1" or payload.get("terminal_status") not in TERMINALS:
            raise JournalError("invalid_payload", "typed terminal evidence reply required")
        from validation import fields, typed_version
        fields(payload, {"schema", "version", "request_id", "correlation_id", "site_id", "room_id", "session_id", "domain_epoch", "terminal_status", "available_at", "reason", "evidence_refs", "alternatives"})
        typed_version(payload, "home-evidence-reply-1")
        self._scope(payload)
        for key in ("request_id", "correlation_id", "reason"):
            identifier(payload.get(key), key)
        if not finite(payload.get("available_at")) or payload["available_at"] > self.clock():
            raise JournalError("invalid_clock", "causal reply availability required")
        self._refs(payload.get("evidence_refs"), allow_empty=payload["terminal_status"] != "supported")
        if not isinstance(payload.get("alternatives"), list) or len(payload["alternatives"]) > 128 or any(not isinstance(v, str) or len(v) > 256 for v in payload["alternatives"]):
            raise JournalError("invalid_payload", "bounded alternatives required")
        def accept():
            row = self.db.execute("SELECT * FROM requests WHERE request_id=?", (payload["request_id"],)).fetchone()
            if not row:
                raise JournalError("unknown_request", "evidence request does not exist", 404)
            request = json.loads(row["body"])
            if any(payload[k] != request[k] for k in ("site_id", "room_id", "session_id", "domain_epoch")):
                raise JournalError("scope_mismatch", "reply belongs to another request scope")
            grant.require("evidence_reply", row["site_id"], row["room_id"], payload_type=payload["schema"], revision="1.0")
            if payload["correlation_id"] != request["correlation_id"]:
                raise JournalError("scope_mismatch", "reply correlation does not match request")
            if row["terminal"] is not None:
                saved = json.loads(row["terminal"])
                if canonical(saved["payload"]) != canonical(payload):
                    raise JournalError("terminal_conflict", "request already has another terminal reply", 409)
                return saved["receipt"]
            if self.clock() >= row["deadline"] and payload["terminal_status"] != "expired":
                raise JournalError("expired", "late request can terminate only as expired", 409)
            if payload["terminal_status"] == "supported":
                for ref in payload["evidence_refs"]:
                    evidence = self.db.execute("SELECT event FROM receipts WHERE event_id=?", (ref,)).fetchall()
                    valid = []
                    for item in evidence:
                        e = json.loads(item[0])
                        if (e.get("schema") == "home-context-event-1" and e.get("payload_type") == "fact-1" and e["payload"].get("status") == "supplied"
                                and (request.get("person_ref") is None or e["payload"].get("person_ref") == request["person_ref"])
                                and e["site_id"] == row["site_id"] and e["room_id"] == row["room_id"]
                                and e["session_id"] == request["session_id"] and e["domain_epoch"] == request["domain_epoch"]
                                and e["available_at"] <= payload["available_at"] < e["valid_until"]
                                and 0 <= payload["available_at"] - e["observed_at"] <= request["max_observation_age_s"]
                                and not self._revoked(e["site_id"], e["room_id"], self._source(e), e["revision"])):
                            valid.append(e)
                    if len(valid) != 1:
                        raise JournalError("unsupported_evidence", "supported reply needs uniquely scoped current fact references", 409)
            event = self._command_event(payload, grant, event_id=self._terminal_id(payload["request_id"]), scope=request,
                                        observed=payload["available_at"], valid=max(payload["available_at"], row["deadline"]))
            receipt = self._append(event, status=payload["terminal_status"])
            self.db.execute("UPDATE requests SET terminal=? WHERE request_id=?", (canonical({"payload": payload, "receipt": receipt}), payload["request_id"]))
            return receipt
        return self._transaction(accept)

    def intent(self, payload, grant):
        if self.dynamic:
            return self.dynamic.intent(payload, grant)
        if not isinstance(payload, dict) or payload.get("schema") != "home-intent-event-1":
            raise JournalError("unsupported_schema", "home-intent-event-1 required")
        from validation import intent
        intent(payload)
        self._scope(payload)
        grant.require("intent", payload["site_id"], payload["room_id"], payload_type=payload["schema"], revision="1.0")
        if (grant.expected_actor_ref is None or payload["actor_ref"] != grant.expected_actor_ref
                or payload.get("origin") != "human_declaration" or payload.get("truth_role") != "runtime_intent"):
            raise JournalError("forbidden", "intent actor and human declaration must match configured grant", 403)
        if "value" not in payload or not finite(payload.get("set_at")) or payload["set_at"] > self.clock():
            raise JournalError("invalid_payload", "typed intent value and causal set_at required")
        def accept():
            return self._command_retry(payload, grant, payload["id"]) or self._append(self._command_event(payload, grant))
        return self._transaction(accept)

    def feedback(self, payload, grant):
        if self.profile == LIVE_PROFILE:
            return self.dynamic.unavailable(payload, grant)
        if not isinstance(payload, dict) or payload.get("schema") != "home-context-consumer-feedback-1" or payload.get("truth_role") != "runtime_feedback":
            raise JournalError("invalid_payload", "typed runtime feedback required")
        self.schemas.validate(payload)
        self._scope(payload)
        identifier(payload.get("id"), "id")
        identifier(payload.get("consumer_id"), "consumer_id")
        identifier(payload.get("decision_ref"), "decision_ref")
        grant.require("feedback", payload["site_id"], payload["room_id"], payload_type=payload["schema"], revision="1.0")
        self._refs(payload.get("evidence_refs"), allow_empty=True)
        if not isinstance(payload.get("input_results"), list) or len(payload["input_results"]) > 128 or not isinstance(payload.get("unresolved"), list) or len(payload["unresolved"]) > 128:
            raise JournalError("invalid_payload", "bounded feedback results required")
        for item in payload["input_results"]:
            if not isinstance(item, dict) or set(item) != {"id", "disposition", "reason"}:
                raise JournalError("invalid_payload", "typed input result required")
            identifier(item["id"], "id")
            if item["disposition"] not in {"accepted", "rejected"} or not isinstance(item["reason"], str) or len(item["reason"]) > 1024:
                raise JournalError("invalid_payload", "typed feedback disposition and bounded reason required")
        def accept():
            retry = self._command_retry(payload, grant, payload["id"])
            if retry:
                return retry
            event = self._command_event(payload, grant, observed=payload.get("available_at"))
            return self._append(event)
        return self._transaction(accept)

    def lookup_request(self, request_id, grant, site, room):
        if self.dynamic:
            return self.dynamic.lookup_request(request_id, grant, site, room)
        grant.require("read", site, room, payload_type="home-evidence-request-1", revision="1.0")
        def read():
            self._expire_requests(site, room)
            row = self.db.execute("SELECT * FROM requests WHERE request_id=? AND site_id=? AND room_id=?", (request_id, site, room)).fetchone()
            if not row:
                raise JournalError("not_found", "request not found in granted scope", 404)
            terminal = json.loads(row["terminal"]) if row["terminal"] else None
            if terminal:
                grant.require("read", site, room, payload_type="home-evidence-reply-1", revision="1.0")
            return {"request": json.loads(row["body"]), "receipt": terminal["receipt"] if terminal else json.loads(row["receipt"]),
                    "terminal_reply": terminal["payload"] if terminal else None}
        return self._transaction(read)

    def _lifecycle_reply(self, request, status, reason):
        now = self.clock()
        payload = {"schema": "home-evidence-reply-1", "version": 1, "request_id": request["request_id"],
                   "correlation_id": request["correlation_id"], **{k: request[k] for k in ("site_id", "room_id", "session_id", "domain_epoch")}, "terminal_status": status, "available_at": now,
                   "reason": reason, "evidence_refs": [], "alternatives": []}
        event = {"schema": "home-context-event-1", "schema_version": "1.0", "id": self._terminal_id(request["request_id"]),
                 "producer_id": "home-context-journal", "stream_epoch": self._meta("epoch"),
                 **{k: request[k] for k in ("site_id", "room_id", "session_id", "domain_epoch")},
                 "observed_at": now, "available_at": now, "valid_until": max(now, request["deadline"]),
                 "payload_type": payload["schema"], "payload_version": "1.0", "revision": "1.0", "payload": payload}
        receipt = self._append(event, status=status)
        self.db.execute("UPDATE requests SET terminal=? WHERE request_id=? AND terminal IS NULL",
                        (canonical({"payload": payload, "receipt": receipt}), request["request_id"]))

    def _expire_requests(self, site, room):
        rows = self.db.execute("SELECT body FROM requests WHERE site_id=? AND room_id=? AND terminal IS NULL AND deadline<=? LIMIT ?",
                               (site, room, self.clock(), self.limits.max_active_requests)).fetchall()
        for row in rows:
            self._lifecycle_reply(json.loads(row[0]), "expired", "deadline_elapsed")

    @staticmethod
    def _terminal_id(request_id):
        return "evidence-reply:" + hashlib.sha256(request_id.encode("utf-8")).hexdigest()

    def lookup_intent(self, event_id, grant, site, room):
        if self.dynamic:
            return self.dynamic.lookup_intent(event_id, grant, site, room)
        grant.require("read", site, room, payload_type="home-intent-event-1", revision="1.0")
        with self.lock:
            rows = self.db.execute("SELECT event,receipt,application_result FROM receipts WHERE event_id=?", (event_id,)).fetchall()
            matches = []
            for row in rows:
                event = json.loads(row["event"])
                if event["site_id"] == site and event["room_id"] == room and event.get("payload_type") == "home-intent-event-1":
                    if event.get("schema") == "home-context-event-tombstone-1":
                        raise JournalError("artifact_expired", "original intent body is outside its retention window", 410)
                    result = {"event": event["payload"], "receipt": json.loads(row["receipt"])}
                    if row["application_result"] is not None:
                        result["application_result"] = json.loads(row["application_result"])
                    matches.append(result)
            if len(matches) != 1:
                raise JournalError("not_found", "unique intent not found in granted scope", 404)
            return matches[0]

    def revocation_generation(self, site, room):
        return int(self._meta("revocation:" + canonical([site, room]), "0"))

    def _store_intent_application_results(self, commit, grant):
        """Bind terminal results to prior, uniquely scoped consumed declarations."""
        for output in commit["publications"]:
            if output["payload_type"] != "home-lighting-shadow-decision-1":
                continue
            decision = output["payload"]
            for reported in decision["intent_results"]:
                if reported["decision_ref"] != decision["id"]:
                    raise JournalError("invalid_application_result", "intent result must identify its enclosing decision", 409)
                rows = self.db.execute("SELECT * FROM receipts WHERE event_id=? AND json_extract(event,'$.payload_type')='home-intent-event-1'", (reported["id"],)).fetchall()
                matches = []
                for row in rows:
                    original, receipt = json.loads(row["event"]), json.loads(row["receipt"])
                    if all(original[k] == output[k] for k in ("site_id", "room_id", "session_id", "domain_epoch")):
                        matches.append((row, original, receipt))
                if len(matches) != 1:
                    raise JournalError("invalid_application_result", "intent result needs one prior declaration in the full decision scope", 409)
                row, original, receipt = matches[0]
                if (original["payload_type"] not in grant.payload_types or original["revision"] not in grant.revisions
                        or not 0 < receipt["cursor"] <= commit["input_cursor"]
                        or original["available_at"] > decision["t"] or receipt["committed_at"] > decision["t"]):
                    raise JournalError("invalid_application_result", "intent result must refer to prior worker-consumed available input", 409)
                result = {"schema": "home-intent-application-result-1", "version": 1, **reported}
                if row["application_result"] is not None:
                    prior = json.loads(row["application_result"])
                    if any(prior[k] != result[k] for k in ("id", "status", "reason")):
                        raise JournalError("application_result_conflict", "intent already has another terminal application outcome", 409)
                    continue  # Preserve the first decision reference and original ingress receipt.
                self.db.execute("UPDATE receipts SET application_result=? WHERE producer_id=? AND source_epoch=? AND event_id=?",
                                (canonical(result), row["producer_id"], row["source_epoch"], row["event_id"]))

    def revoke(self, event, grant):
        if self.dynamic:
            return self.dynamic.revoke(event, grant)
        self._event(event)
        payload = event["payload"]
        if payload["schema"] != "home-context-revocation-1":
            raise JournalError("unsupported_schema", "typed revocation required")
        grant.require("revoke", event["site_id"], event["room_id"], payload_type=event["payload_type"], revision=event["revision"], producer_id=event["producer_id"])
        source, revision = payload.get("source_id"), payload.get("revision")
        if source != "*":
            identifier(source, "source_id")
        if revision != "*":
            identifier(revision, "revision")
        identifier(payload.get("reason"), "reason")
        def commit():
            retry = self._receipt(event)
            if retry:
                return retry
            receipt = self._append(event)
            self.db.execute("INSERT OR IGNORE INTO revocations VALUES(?,?,?,?)", (event["site_id"], event["room_id"], source, revision))
            self.db.execute("DELETE FROM state WHERE site_id=? AND room_id=? AND (?='*' OR source_id=?) AND (?='*' OR revision=?)",
                            (event["site_id"], event["room_id"], source, source, revision, revision))
            # A derived shadow context may depend on a revoked source. Withhold
            # it until a worker reconciles rather than publishing stale authority.
            self.db.execute("DELETE FROM state WHERE site_id=? AND room_id=? AND json_extract(body,'$.payload_type') IN ('home-context-1','home-lighting-shadow-decision-1','home-engagement-state-1')",
                            (event["site_id"], event["room_id"]))
            self._set_meta("revocation:" + canonical([event["site_id"], event["room_id"]]), self.revocation_generation(event["site_id"], event["room_id"]) + 1)
            return receipt
        return self._transaction(commit)

    def checkpoint(self, worker_id, grant, site, room):
        if self.dynamic:
            return self.dynamic.checkpoint(worker_id, grant, site, room)
        grant.require("read", site, room)
        grant.require("worker_commit", site, room)
        identifier(worker_id, "worker_id")
        if worker_id != grant.expected_worker_id:
            raise JournalError("forbidden", "worker identity does not match configured principal", 403)
        with self.lock:
            row = self.db.execute("SELECT * FROM workers WHERE worker_id=? AND site_id=? AND room_id=?", (worker_id, site, room)).fetchone()
            return {"schema": "home-context-worker-checkpoint-1", "stream_epoch": self._meta("epoch"), "worker_id": worker_id,
                    "input_cursor": row["input_cursor"] if row else 0, "context_seq": row["context_seq"] if row else 0,
                    "checkpoint": json.loads(row["checkpoint"]) if row else None,
                    "revocation_generation": self.revocation_generation(site, room)}

    def worker_commit(self, commit, grant):
        if self.dynamic:
            return self.dynamic.worker_commit(commit, grant)
        required = {"schema", "schema_version", "id", "worker_id", "producer_id", "stream_epoch", "site_id", "room_id",
                    "expected_input_cursor", "expected_context_seq", "input_cursor", "input_receipts", "checkpoint", "publications"}
        if not isinstance(commit, dict) or set(commit) != required or commit["schema"] != "home-context-worker-commit-1" or commit["schema_version"] != "1.0":
            raise JournalError("invalid_commit", "exact typed worker commit required")
        for key in ("id", "worker_id", "producer_id", "stream_epoch", "site_id", "room_id"):
            identifier(commit[key], key)
        grant.require("worker_commit", commit["site_id"], commit["room_id"], producer_id=commit["producer_id"])
        if commit["worker_id"] != grant.expected_worker_id:
            raise JournalError("forbidden", "worker identity does not match configured principal", 403)
        if not isinstance(commit["publications"], list) or not 1 <= len(commit["publications"]) <= 16:
            raise JournalError("invalid_commit", "1..16 bounded output publications required")
        checkpoint = commit["checkpoint"]
        if not isinstance(checkpoint, dict) or not {"config_digest", "domain_epoch", "revocation_generation"} <= set(checkpoint):
            raise JournalError("invalid_commit", "checkpoint configuration and epochs required")
        if not re.fullmatch(r"[a-f0-9]{64}", str(checkpoint["config_digest"])):
            raise JournalError("invalid_commit", "checkpoint config digest required")
        identifier(checkpoint["domain_epoch"], "domain_epoch")
        for key in ("expected_input_cursor", "expected_context_seq", "input_cursor"):
            if type(commit[key]) is not int or commit[key] < 0:
                raise JournalError("invalid_commit", "integer checkpoint cursors required")
        if not isinstance(commit["input_receipts"], list) or len(commit["input_receipts"]) > 128:
            raise JournalError("invalid_commit", "bounded decision input receipt metadata required")
        seen_cursors = set()
        for receipt in commit["input_receipts"]:
            if not isinstance(receipt, dict) or set(receipt) != {"cursor", "id", "payload_digest", "received_at"}:
                raise JournalError("invalid_commit", "exact decision input receipt fields required")
            identifier(receipt["id"], "id")
            if type(receipt["cursor"]) is not int or not 1 <= receipt["cursor"] <= commit["input_cursor"] or receipt["cursor"] in seen_cursors:
                raise JournalError("invalid_commit", "unique consumed receipt cursors required")
            if not isinstance(receipt["payload_digest"], str) or not re.fullmatch(r"[a-f0-9]{64}", receipt["payload_digest"]) or not finite(receipt["received_at"]):
                raise JournalError("invalid_commit", "finite receipt time and payload digest required")
            seen_cursors.add(receipt["cursor"])
        for event in commit["publications"]:
            self._event(event)
            if (event["producer_id"] != grant.producer_id or event["site_id"] != commit["site_id"]
                    or event["room_id"] != commit["room_id"] or event["domain_epoch"] != checkpoint["domain_epoch"]):
                raise JournalError("scope_mismatch", "worker output belongs to another scope")
            grant.require("worker_commit", event["site_id"], event["room_id"], payload_type=event["payload_type"], revision=event["revision"])
            if event["payload_type"] not in {"home-context-1", "home-lighting-shadow-decision-1", "home-context-consumer-feedback-1", "home-engagement-state-1"}:
                raise JournalError("invalid_commit", "worker output type is not an accepted shadow publication")
            if event["payload_type"] == "home-lighting-shadow-decision-1" and event["payload"].get("input_receipts") != commit["input_receipts"]:
                raise JournalError("invalid_commit", "decision receipt metadata must equal its atomic commit")
        bounded_json(commit, self.limits.max_worker_commit_bytes)
        def apply():
            # Commit retry identity lives in the same durable receipt budget.
            marker = {"producer_id": grant.producer_id, "stream_epoch": commit["stream_epoch"], "id": "worker-commit/" + commit["id"]}
            row = self.db.execute("SELECT * FROM receipts WHERE producer_id=? AND source_epoch=? AND event_id=?", (marker["producer_id"], marker["stream_epoch"], marker["id"])).fetchone()
            if row:
                if row["digest"] != digest(commit):
                    raise JournalError("identity_conflict", "worker commit identity changed", 409)
                return json.loads(row["receipt"])
            worker = self.db.execute("SELECT * FROM workers WHERE worker_id=? AND site_id=? AND room_id=?", (commit["worker_id"], commit["site_id"], commit["room_id"])).fetchone()
            previous, seq = (worker["input_cursor"], worker["context_seq"]) if worker else (0, 0)
            if commit["expected_input_cursor"] != previous:
                raise JournalError("checkpoint_conflict", "worker checkpoint advanced concurrently", 409)
            if commit["expected_context_seq"] != seq:
                raise JournalError("checkpoint_conflict", "worker decision sequence advanced concurrently", 409)
            if not isinstance(checkpoint.get("state"), dict) or type(checkpoint["state"].get("seq")) is not int or checkpoint["state"]["seq"] != seq + 1:
                raise JournalError("invalid_commit", "worker checkpoint state must match the next decision sequence")
            if any(event["payload_type"] in {"home-lighting-shadow-decision-1", "home-engagement-state-1"} and event["payload"].get("seq") != seq + 1 for event in commit["publications"]):
                raise JournalError("invalid_commit", "shadow decision must match the next decision sequence")
            if commit["stream_epoch"] != self._meta("epoch"):
                raise JournalError("reset_required", "worker journal epoch changed", 409)
            delivered = self.db.execute("SELECT delivered FROM consumers WHERE principal_id=? AND consumer_id=? AND site_id=? AND room_id=?",
                                        (grant.principal_id, commit["worker_id"], commit["site_id"], commit["room_id"])).fetchone()
            if not previous <= commit["input_cursor"] <= int(self._meta("cursor")) or delivered is None or commit["input_cursor"] > delivered[0]:
                raise JournalError("unseen_cursor", "worker cannot consume undelivered input", 409)
            for claimed in commit["input_receipts"]:
                saved = self.db.execute("SELECT receipt,event FROM receipts WHERE json_extract(receipt,'$.schema')='home-context-receipt-1' AND json_extract(receipt,'$.cursor')=?", (claimed["cursor"],)).fetchone()
                if saved is None:
                    raise JournalError("invalid_input_receipt", "decision receipt does not identify durable journal input", 409)
                receipt, original = json.loads(saved["receipt"]), json.loads(saved["event"])
                if (claimed["id"] != receipt["id"] or claimed["payload_digest"] != receipt["payload_digest"]
                        or original["site_id"] != commit["site_id"] or original["room_id"] != commit["room_id"]
                        or original["payload_type"] not in grant.payload_types or original["revision"] not in grant.revisions
                        or not receipt["committed_at"] <= claimed["received_at"] <= self.clock() + 0.05):
                    raise JournalError("invalid_input_receipt", "decision receipt metadata does not match delivered scoped input", 409)
            if type(checkpoint["revocation_generation"]) is not int or checkpoint["revocation_generation"] != self.revocation_generation(commit["site_id"], commit["room_id"]):
                raise JournalError("revocation_conflict", "worker must reconcile the current revocation generation", 409)
            if worker is None and self.db.execute("SELECT count(*) FROM workers").fetchone()[0] >= self.limits.max_workers:
                raise JournalError("journal_full", "worker budget exhausted", 507)
            # All output and checkpoint writes share this transaction.
            for event in commit["publications"]:
                if self._receipt(event) is not None:
                    raise JournalError("output_identity_reused", "a new worker commit cannot reuse a previously committed output identity", 409)
            self._store_intent_application_results(commit, grant)
            receipts = [self._append(event, context_seq=seq + 1) for event in commit["publications"]]
            self.db.execute("INSERT INTO workers VALUES(?,?,?,?,?,?) ON CONFLICT(worker_id,site_id,room_id) DO UPDATE SET input_cursor=excluded.input_cursor,context_seq=excluded.context_seq,checkpoint=excluded.checkpoint",
                            (commit["worker_id"], commit["site_id"], commit["room_id"], commit["input_cursor"], seq + 1, canonical(checkpoint)))
            result = {"schema": "home-context-worker-commit-receipt-1", "id": commit["id"], "worker_id": commit["worker_id"],
                      "stream_epoch": self._meta("epoch"), "input_cursor": commit["input_cursor"], "context_seq": seq + 1,
                      "cursor": int(self._meta("cursor")), "receipts": receipts}
            if self.db.execute("SELECT count(*) FROM receipts").fetchone()[0] >= self.limits.max_receipts:
                raise JournalError("journal_full", "durable commit identity budget exhausted", 507)
            self.db.execute("INSERT INTO receipts(producer_id,source_epoch,event_id,digest,receipt,event) VALUES(?,?,?,?,?,?)", (grant.producer_id, commit["stream_epoch"], marker["id"], digest(commit), canonical(result), canonical({"schema": "home-context-worker-commit-tombstone-1", "site_id": commit["site_id"], "room_id": commit["room_id"]})))
            return result
        return self._transaction(apply)
