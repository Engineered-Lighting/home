"""Cryptographic verifier for reviewed legacy-identity projection bundles.

This module is deliberately independent from the online Core API.  It accepts
canonical JSON bytes, a freshly re-read set of minimized source records, and
deployment-pinned key/build policy.  Successful verification returns only the
typed semantic projections and content-free commitments that a later atomic
PostgreSQL finalizer may consume.

It does not connect to PostgreSQL, mutate semantic tables, claim a cutover, or
create parent facts.  In particular, a database login is not treated as proof
that the Ed25519 signature or keyed commitments were checked: callers must run
this verifier before a finalizer entrypoint can ever be enabled.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import hmac
import json
import re
import unicodedata
from typing import Any, Callable, Mapping, Sequence
import uuid

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


MAX_CANONICAL_BYTES = 4_194_304
MAX_ITEMS = 10_000
HEX64 = re.compile(r"^[0-9a-f]{64}$")
HEX128 = re.compile(r"^[0-9a-f]{128}$")
POLICY_TOKEN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")

RUN_KEYS = frozenset(
    {
        "run_id",
        "operator_request_id",
        "contract_version",
        "source_schema_version",
        "source_projection_contract_version",
        "importer_version",
        "canonicalization_version",
        "projection_version",
        "shadow_rule_version",
        "commitment_algorithm",
        "commitment_key_fingerprint",
        "commitment_key_epoch",
        "source_item_count",
        "decision_count",
        "logical_source_manifest_commitment",
        "projection_manifest_commitment",
        "source_projection_contract_digest",
        "review_receipt_commitment",
        "policy_version",
        "policy_digest",
        "shadow_authorization_id",
        "release_manifest_digest",
        "migration_tool_bundle_digest",
        "core_oci_manifest_digest",
        "core_schema_digest",
        "core_capability_digest",
        "signature_algorithm",
        "signing_key_fingerprint",
        "review_signature",
        "expires_at",
    }
)
SOURCE_KEYS = frozenset(
    {
        "source_item_id",
        "ordinal",
        "source_table_kind",
        "row_key_commitment",
        "allowed_projection_commitment",
    }
)
DECISION_KEYS = frozenset(
    {
        "decision_id",
        "source_item_id",
        "ordinal",
        "decision_kind",
        "disposition",
        "candidate_commitment",
        "canonical_apply_decision_id",
        "canonical_apply_disposition",
        "decision_commitment",
    }
)
PROJECTION_KEYS = frozenset(
    {
        "decision_id",
        "decision_kind",
        "candidate_commitment",
        "projection_table_kind",
        "projection_ref_commitment",
        "projection_commitment",
        "receipt_id",
        "receipt_commitment",
        "record",
    }
)
SOURCE_RECORD_KEYS = frozenset(
    {"source_item_id", "source_table_kind", "row_key", "allowed_projection"}
)

SOURCE_TABLE_KINDS = frozenset(
    {"schema_meta", "identities", "identity_aliases", "enrollments", "relationships"}
)
DECISION_KINDS = frozenset(
    {
        "person",
        "privacy_directive",
        "person_status",
        "alias",
        "recognition_binding",
        "legacy_role_candidate",
        "legacy_relationship_candidate",
        "explicit_omission",
    }
)
APPLY_KINDS = DECISION_KINDS - {"explicit_omission"}
DISPOSITIONS = frozenset(
    {"apply", "privacy_suppressed", "out_of_scope_by_rule", "coalesced_duplicate"}
)
PROJECTION_TABLES = {
    "person": "identity.people",
    "privacy_directive": "identity.privacy_directives",
    "person_status": "identity.people",
    "alias": "identity.aliases",
    "recognition_binding": "identity.external_recognition_bindings",
    "legacy_role_candidate": "identity.legacy_role_labels",
    "legacy_relationship_candidate": "identity.legacy_relationship_candidates",
}


class VerificationError(RuntimeError):
    """Fail-closed verifier error that never contains private payload content."""


@dataclass(frozen=True, slots=True)
class VerificationPolicy:
    review_public_key: Ed25519PublicKey
    review_key_fingerprint: str
    commitment_key: bytes
    commitment_key_epoch: int
    commitment_key_fingerprint: str
    policy_version: str
    policy_digest: str
    source_projection_contract_digest: str
    release_manifest_digest: str
    migration_tool_bundle_digest: str
    core_oci_manifest_digest: str
    core_schema_digest: str
    core_capability_digest: str
    shadow_authorization_id: uuid.UUID
    review_key_purpose: str = "identity_migration_review"
    review_key_revoked: bool = False
    now: Callable[[], datetime] = lambda: datetime.now(UTC)

    def __post_init__(self) -> None:
        if self.review_key_purpose != "identity_migration_review":
            raise ValueError("review public key has the wrong purpose")
        if self.review_key_revoked:
            raise ValueError("review public key is revoked")
        if not self.commitment_key or len(self.commitment_key) < 32:
            raise ValueError("commitment key must contain at least 256 bits")
        if self.commitment_key_epoch <= 0:
            raise ValueError("commitment key epoch must be positive")
        for value in (
            self.review_key_fingerprint,
            self.commitment_key_fingerprint,
            self.policy_digest,
            self.source_projection_contract_digest,
            self.release_manifest_digest,
            self.migration_tool_bundle_digest,
            self.core_oci_manifest_digest,
            self.core_schema_digest,
            self.core_capability_digest,
        ):
            if not HEX64.fullmatch(value):
                raise ValueError("policy digest or fingerprint is not canonical")
        if not POLICY_TOKEN.fullmatch(self.policy_version):
            raise ValueError("policy version is not canonical")
        raw_public = self.review_public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        if not hmac.compare_digest(
            hashlib.sha256(raw_public).hexdigest(), self.review_key_fingerprint
        ):
            raise ValueError("review public key fingerprint mismatch")
        if not hmac.compare_digest(
            hashlib.sha256(self.commitment_key).hexdigest(),
            self.commitment_key_fingerprint,
        ):
            raise ValueError("commitment key fingerprint mismatch")


@dataclass(frozen=True, slots=True)
class VerifiedProjectionBundle:
    run_id: uuid.UUID
    review_receipt_commitment: str
    projection_manifest_commitment: str
    _projections_canonical: bytes
    verified_at: datetime

    @property
    def projections(self) -> tuple[Mapping[str, Any], ...]:
        parsed = parse_canonical_json(
            self._projections_canonical, maximum=MAX_CANONICAL_BYTES
        )
        if not isinstance(parsed, list):  # constructor-owned invariant
            raise AssertionError("verified projection storage is malformed")
        return tuple(parsed)

    def finalizer_document(self) -> dict[str, Any]:
        """Return the private, bounded input for a future atomic DB finalizer.

        This result intentionally contains no raw legacy source record and no
        caller-supplied authority or cutover flag.
        """

        return {
            "contract_version": "reviewed-identity-semantic-finalizer-input-v1",
            "run_id": str(self.run_id),
            "review_receipt_commitment": self.review_receipt_commitment,
            "projection_manifest_commitment": self.projection_manifest_commitment,
            "projections": [dict(item) for item in self.projections],
        }


def _pairs_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise VerificationError("canonical document contains duplicate keys")
        result[key] = value
    return result


def _reject_float(_value: str) -> Any:
    raise VerificationError("canonical document contains a non-integer number")


def _reject_constant(_value: str) -> Any:
    raise VerificationError("canonical document contains a non-finite number")


def _validate_json_value(value: Any, *, depth: int = 0) -> None:
    if depth > 32:
        raise VerificationError("canonical document nesting is too deep")
    if value is None or type(value) in {bool, int}:
        return
    if isinstance(value, str):
        if unicodedata.normalize("NFC", value) != value:
            raise VerificationError("canonical document contains non-NFC text")
        if any(unicodedata.category(character).startswith("C") for character in value):
            raise VerificationError("canonical document contains control text")
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item, depth=depth + 1)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise VerificationError("canonical document contains a non-text key")
            _validate_json_value(key, depth=depth + 1)
            _validate_json_value(item, depth=depth + 1)
        return
    raise VerificationError("canonical document contains an unsupported value")


def canonical_bytes(value: Any) -> bytes:
    _validate_json_value(value)
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as error:
        raise VerificationError("canonical document could not be encoded") from error


def parse_canonical_json(raw: bytes, *, maximum: int = MAX_CANONICAL_BYTES) -> Any:
    if not raw or len(raw) > maximum:
        raise VerificationError("canonical document size is invalid")
    try:
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_pairs_without_duplicates,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
    except VerificationError:
        raise
    except (UnicodeError, json.JSONDecodeError) as error:
        raise VerificationError("canonical document is invalid") from error
    if not hmac.compare_digest(canonical_bytes(value), raw):
        raise VerificationError("document bytes are not canonical")
    return value


def keyed_commitment(key: bytes, domain: str, value: Any) -> str:
    domain_bytes = domain.encode("ascii", errors="strict")
    payload = canonical_bytes(value)
    material = (
        b"home-agent-identity\x00"
        + len(domain_bytes).to_bytes(2, "big")
        + domain_bytes
        + len(payload).to_bytes(8, "big")
        + payload
    )
    return hmac.new(key, material, hashlib.sha256).hexdigest()


def _exact_object(value: Any, keys: frozenset[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise VerificationError(f"{label} shape is invalid")
    return value


def _canonical_uuid(value: Any, label: str, *, require_v7: bool = False) -> uuid.UUID:
    if not isinstance(value, str):
        raise VerificationError(f"{label} UUID is invalid")
    try:
        parsed = uuid.UUID(value)
    except ValueError as error:
        raise VerificationError(f"{label} UUID is invalid") from error
    if str(parsed) != value or (require_v7 and parsed.version != 7):
        raise VerificationError(f"{label} UUID is not canonical")
    return parsed


def _canonical_time(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z", value
    ):
        raise VerificationError(f"{label} timestamp is not canonical")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    except ValueError as error:
        raise VerificationError(f"{label} timestamp is invalid") from error
    return parsed


def _hex64(value: Any, label: str) -> str:
    if not isinstance(value, str) or not HEX64.fullmatch(value):
        raise VerificationError(f"{label} digest is invalid")
    return value


def _bounded_text(value: Any, label: str, maximum: int, *, optional: bool = False):
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise VerificationError(f"{label} text is invalid")
    if value != value.strip():
        raise VerificationError(f"{label} text is not normalized")
    return value


def _require_exact_keys(record: Mapping[str, Any], keys: set[str], label: str) -> None:
    if set(record) != keys:
        raise VerificationError(f"{label} record shape is invalid")


def _validate_projection_record(kind: str, decision_id: str, value: Any) -> None:
    if not isinstance(value, dict):
        raise VerificationError("projection record is invalid")
    if kind == "person":
        _require_exact_keys(
            value,
            {
                "person_id",
                "display_name",
                "pronouns",
                "privacy_scope",
                "legacy_source_ref",
                "legacy_source_version",
                "legacy_source_sha256",
            },
            kind,
        )
        _canonical_uuid(value["person_id"], "person")
        _bounded_text(value["display_name"], "display name", 255)
        _bounded_text(value["pronouns"], "pronouns", 64, optional=True)
        if value["privacy_scope"] != "private":
            raise VerificationError("migrated person privacy scope must be private")
        _bounded_text(value["legacy_source_ref"], "person source", 255)
        if (
            type(value["legacy_source_version"]) is not int
            or value["legacy_source_version"] < 0
        ):
            raise VerificationError("person source version is invalid")
        _hex64(value["legacy_source_sha256"], "person source")
        return
    id_fields = {
        "privacy_directive": "directive_id",
        "alias": "alias_id",
        "recognition_binding": "binding_id",
        "legacy_role_candidate": "label_id",
        "legacy_relationship_candidate": "candidate_id",
    }
    if kind in id_fields:
        if value.get(id_fields[kind]) != decision_id:
            raise VerificationError("projection row ID is not the reviewed decision ID")
    if kind == "privacy_directive":
        _require_exact_keys(
            value,
            {
                "directive_id",
                "person_id",
                "directive",
                "enabled",
                "expires_at",
                "source_ref",
                "source_version",
                "source_snapshot_sha256",
            },
            kind,
        )
        _canonical_uuid(value["person_id"], "privacy subject")
        if (
            value["directive"]
            not in {
                "do_not_track",
                "ignored",
                "silent",
                "private",
                "auto_expire",
            }
            or value["enabled"] is not True
        ):
            raise VerificationError("privacy directive is invalid")
        if value["directive"] == "auto_expire":
            _canonical_time(value["expires_at"], "auto expiry")
        elif value["expires_at"] is not None:
            raise VerificationError("non-expiring privacy directive has an expiry")
        _validate_source_fields(value)
        return
    if kind == "person_status":
        _require_exact_keys(
            value,
            {
                "person_id",
                "status",
                "source_ref",
                "source_version",
                "source_snapshot_sha256",
            },
            kind,
        )
        _canonical_uuid(value["person_id"], "status subject")
        if value["status"] != "archived":
            raise VerificationError("migrated person status is invalid")
        _validate_source_fields(value)
        return
    if kind == "alias":
        _require_exact_keys(
            value,
            {
                "alias_id",
                "person_id",
                "alias",
                "normalized_alias",
                "alias_kind",
                "source_ref",
                "source_version",
                "source_snapshot_sha256",
            },
            kind,
        )
        _canonical_uuid(value["person_id"], "alias subject")
        alias = _bounded_text(value["alias"], "alias", 255)
        normalized = " ".join(unicodedata.normalize("NFKC", alias).split()).casefold()
        if (
            not normalized
            or normalized != value["normalized_alias"]
            or any(unicodedata.category(char).startswith("C") for char in normalized)
        ):
            raise VerificationError("alias normalization is invalid")
        if value["alias_kind"] not in {"name", "nickname"}:
            raise VerificationError("alias kind is invalid")
        _validate_source_fields(value)
        return
    if kind == "recognition_binding":
        _require_exact_keys(
            value,
            {
                "binding_id",
                "person_id",
                "external_system",
                "external_id",
                "status",
                "source_ref",
                "source_version",
                "source_snapshot_sha256",
            },
            kind,
        )
        _canonical_uuid(value["person_id"], "recognition subject")
        if value["external_system"] != "frigate" or value["status"] not in {
            "active",
            "retired",
        }:
            raise VerificationError("recognition binding is invalid")
        _bounded_text(value["external_id"], "recognition ID", 255)
        _validate_source_fields(value)
        return
    if kind == "legacy_role_candidate":
        _require_exact_keys(
            value,
            {
                "label_id",
                "person_id",
                "role_label",
                "perspective",
                "source_ref",
                "source_version",
                "source_snapshot_sha256",
            },
            kind,
        )
        _canonical_uuid(value["person_id"], "role subject")
        _bounded_text(value["role_label"], "role label", 64)
        if value["perspective"] != "unknown":
            raise VerificationError("legacy role perspective is authoritative")
        _validate_source_fields(value, allow_null_version=True)
        return
    if kind == "legacy_relationship_candidate":
        _require_exact_keys(
            value,
            {
                "candidate_id",
                "from_person_id",
                "to_person_id",
                "relationship_label",
                "relationship_status",
                "perspective",
                "authoritative",
                "source_ref",
                "source_snapshot_sha256",
            },
            kind,
        )
        source = _canonical_uuid(value["from_person_id"], "relationship source")
        target = _canonical_uuid(value["to_person_id"], "relationship target")
        if source == target:
            raise VerificationError("legacy relationship is self-referential")
        _bounded_text(value["relationship_label"], "relationship label", 64)
        if value["relationship_status"] not in {"active", "ended", "paused"}:
            raise VerificationError("legacy relationship status is invalid")
        if value["perspective"] != "unknown" or value["authoritative"] is not False:
            raise VerificationError("legacy relationship candidate claims authority")
        _bounded_text(value["source_ref"], "relationship source", 255)
        _hex64(value["source_snapshot_sha256"], "relationship source")
        return
    raise VerificationError("projection decision kind is unsupported")


def _validate_source_fields(
    value: Mapping[str, Any], *, allow_null_version: bool = False
) -> None:
    _bounded_text(value["source_ref"], "projection source", 255)
    version = value["source_version"]
    if allow_null_version and version is None:
        pass
    elif type(version) is not int or version < 0:
        raise VerificationError("projection source version is invalid")
    _hex64(value["source_snapshot_sha256"], "projection source")


def _constant_equal(actual: Any, expected: str, label: str) -> None:
    if not isinstance(actual, str) or not hmac.compare_digest(actual, expected):
        raise VerificationError(f"{label} does not match deployment policy")


def verify_projection_bundle(
    raw_bundle: bytes,
    *,
    source_records: Sequence[Mapping[str, Any]],
    policy: VerificationPolicy,
) -> VerifiedProjectionBundle:
    bundle = parse_canonical_json(raw_bundle)
    _exact_object(
        bundle,
        frozenset({"contract_version", "manifest", "projections"}),
        "bundle",
    )
    if bundle["contract_version"] != "reviewed-identity-projection-bundle-v1":
        raise VerificationError("projection bundle contract is unsupported")
    manifest = _exact_object(
        bundle["manifest"],
        frozenset({"run", "source_items", "decisions"}),
        "manifest",
    )
    run = _exact_object(manifest["run"], RUN_KEYS, "run")
    sources = manifest["source_items"]
    decisions = manifest["decisions"]
    projections = bundle["projections"]
    if not all(isinstance(value, list) for value in (sources, decisions, projections)):
        raise VerificationError("manifest collections are invalid")
    if not (1 <= len(sources) <= MAX_ITEMS and 1 <= len(decisions) <= MAX_ITEMS):
        raise VerificationError("manifest collection count is invalid")
    if len(projections) > MAX_ITEMS or len(source_records) != len(sources):
        raise VerificationError("projection or source record count is invalid")
    private_sources = list(source_records)
    if len(canonical_bytes(private_sources)) > MAX_CANONICAL_BYTES:
        raise VerificationError("private source record set is oversized")
    run_id = _canonical_uuid(run["run_id"], "run", require_v7=True)
    _canonical_uuid(run["operator_request_id"], "operator request", require_v7=True)
    if run["contract_version"] != "reviewed-identity-migration-run-v1":
        raise VerificationError("reviewed run contract is unsupported")
    fixed = {
        "source_schema_version": 1,
        "source_projection_contract_version": "legacy-identity-source-projection-v1",
        "importer_version": "legacy-identity-importer-v1",
        "canonicalization_version": "identity-canonicalization-v1",
        "projection_version": "semantic-people-projection-v1",
        "shadow_rule_version": "record-only-envelope-worker-gate-v3",
        "commitment_algorithm": "hmac-sha256-v1",
        "signature_algorithm": "ed25519",
    }
    if any(run.get(key) != value for key, value in fixed.items()):
        raise VerificationError("reviewed run version contract is invalid")
    if type(run["source_item_count"]) is not int or run["source_item_count"] != len(
        sources
    ):
        raise VerificationError("source item count is invalid")
    if type(run["decision_count"]) is not int or run["decision_count"] != len(
        decisions
    ):
        raise VerificationError("decision count is invalid")
    expiry = _canonical_time(run["expires_at"], "review expiry")
    verified_at = policy.now()
    if verified_at.tzinfo is None:
        raise ValueError("verification policy clock must return an aware timestamp")
    verified_at = verified_at.astimezone(UTC)
    if expiry <= verified_at:
        raise VerificationError("reviewed projection bundle is expired")
    _constant_equal(run["policy_version"], policy.policy_version, "policy version")
    for key, expected in (
        ("policy_digest", policy.policy_digest),
        ("source_projection_contract_digest", policy.source_projection_contract_digest),
        ("release_manifest_digest", policy.release_manifest_digest),
        ("migration_tool_bundle_digest", policy.migration_tool_bundle_digest),
        ("core_oci_manifest_digest", policy.core_oci_manifest_digest),
        ("core_schema_digest", policy.core_schema_digest),
        ("core_capability_digest", policy.core_capability_digest),
        ("signing_key_fingerprint", policy.review_key_fingerprint),
        ("commitment_key_fingerprint", policy.commitment_key_fingerprint),
    ):
        _constant_equal(run[key], expected, key)
    if run["commitment_key_epoch"] != policy.commitment_key_epoch:
        raise VerificationError("commitment key epoch does not match deployment policy")
    if _canonical_uuid(run["shadow_authorization_id"], "shadow authorization") != (
        policy.shadow_authorization_id
    ):
        raise VerificationError("shadow authorization does not match deployment policy")

    source_by_id: dict[str, Mapping[str, Any]] = {}
    for expected_ordinal, item_value in enumerate(sources):
        item = _exact_object(item_value, SOURCE_KEYS, "source item")
        item_id = str(
            _canonical_uuid(item["source_item_id"], "source item", require_v7=True)
        )
        if item["ordinal"] != expected_ordinal or item_id in source_by_id:
            raise VerificationError("source item ordinals or IDs are invalid")
        if item["source_table_kind"] not in SOURCE_TABLE_KINDS:
            raise VerificationError("source table kind is invalid")
        _hex64(item["row_key_commitment"], "source row key")
        _hex64(item["allowed_projection_commitment"], "allowed projection")
        source_by_id[item_id] = item

    private_source_by_id: dict[str, Mapping[str, Any]] = {}
    for value in private_sources:
        item = _exact_object(dict(value), SOURCE_RECORD_KEYS, "private source record")
        item_id = str(
            _canonical_uuid(
                item["source_item_id"], "private source item", require_v7=True
            )
        )
        if item_id in private_source_by_id or item_id not in source_by_id:
            raise VerificationError("private source record identity is invalid")
        stored = source_by_id[item_id]
        if item["source_table_kind"] != stored["source_table_kind"]:
            raise VerificationError("private source record kind drifted")
        expected_row = keyed_commitment(
            policy.commitment_key,
            "identity-source-row-key-v1",
            {
                "run_id": str(run_id),
                "source_item_id": item_id,
                "source_table_kind": item["source_table_kind"],
                "row_key": item["row_key"],
            },
        )
        expected_allowed = keyed_commitment(
            policy.commitment_key,
            "identity-allowed-projection-v1",
            {
                "run_id": str(run_id),
                "source_item_id": item_id,
                "source_table_kind": item["source_table_kind"],
                "allowed_projection": item["allowed_projection"],
            },
        )
        if not hmac.compare_digest(stored["row_key_commitment"], expected_row) or not (
            hmac.compare_digest(
                stored["allowed_projection_commitment"], expected_allowed
            )
        ):
            raise VerificationError("private source record commitment mismatch")
        private_source_by_id[item_id] = item

    decision_by_id: dict[str, Mapping[str, Any]] = {}
    apply_decisions: dict[str, Mapping[str, Any]] = {}
    for expected_ordinal, decision_value in enumerate(decisions):
        decision = _exact_object(decision_value, DECISION_KEYS, "decision")
        decision_id = str(
            _canonical_uuid(decision["decision_id"], "decision", require_v7=True)
        )
        source_id = str(
            _canonical_uuid(
                decision["source_item_id"], "decision source", require_v7=True
            )
        )
        if (
            decision["ordinal"] != expected_ordinal
            or decision_id in decision_by_id
            or source_id not in source_by_id
            or decision["decision_kind"] not in DECISION_KINDS
            or decision["disposition"] not in DISPOSITIONS
        ):
            raise VerificationError("decision identity or classification is invalid")
        expected_decision_commitment = keyed_commitment(
            policy.commitment_key,
            "identity-decision-v1",
            {
                key: value
                for key, value in decision.items()
                if key != "decision_commitment"
            },
        )
        if not hmac.compare_digest(
            _hex64(decision["decision_commitment"], "decision"),
            expected_decision_commitment,
        ):
            raise VerificationError("decision commitment mismatch")
        disposition = decision["disposition"]
        kind = decision["decision_kind"]
        candidate = decision["candidate_commitment"]
        if disposition == "apply":
            if kind not in APPLY_KINDS or candidate is None:
                raise VerificationError("apply decision shape is invalid")
            _hex64(candidate, "candidate")
            if (
                decision["canonical_apply_decision_id"] is not None
                or decision["canonical_apply_disposition"] is not None
            ):
                raise VerificationError("apply decision has a coalescing target")
            apply_decisions[decision_id] = decision
        elif disposition == "coalesced_duplicate":
            if kind not in APPLY_KINDS or candidate is None:
                raise VerificationError("coalesced decision shape is invalid")
            _hex64(candidate, "candidate")
            canonical_id = str(
                _canonical_uuid(
                    decision["canonical_apply_decision_id"],
                    "canonical apply decision",
                    require_v7=True,
                )
            )
            if (
                canonical_id == decision_id
                or decision["canonical_apply_disposition"] != "apply"
            ):
                raise VerificationError("coalesced decision target is invalid")
        else:
            if (
                kind != "explicit_omission"
                or candidate is not None
                or decision["canonical_apply_decision_id"] is not None
                or decision["canonical_apply_disposition"] is not None
            ):
                raise VerificationError("omission decision shape is invalid")
        decision_by_id[decision_id] = decision
    for decision in decision_by_id.values():
        if decision["disposition"] != "coalesced_duplicate":
            continue
        canonical = decision_by_id.get(decision["canonical_apply_decision_id"])
        if (
            canonical is None
            or canonical["disposition"] != "apply"
            or canonical["decision_kind"] != decision["decision_kind"]
            or not hmac.compare_digest(
                canonical["candidate_commitment"], decision["candidate_commitment"]
            )
        ):
            raise VerificationError(
                "coalesced decision does not match its canonical apply"
            )

    projection_by_decision: dict[str, Mapping[str, Any]] = {}
    verified_projections: list[Mapping[str, Any]] = []
    for value in projections:
        projection = _exact_object(value, PROJECTION_KEYS, "projection")
        decision_id = str(
            _canonical_uuid(
                projection["decision_id"], "projection decision", require_v7=True
            )
        )
        receipt_id = str(
            _canonical_uuid(
                projection["receipt_id"], "projection receipt", require_v7=True
            )
        )
        decision = apply_decisions.get(decision_id)
        if decision is None or decision_id in projection_by_decision:
            raise VerificationError("projection does not map to one apply decision")
        kind = decision["decision_kind"]
        if (
            projection["decision_kind"] != kind
            or projection["projection_table_kind"] != PROJECTION_TABLES[kind]
            or not hmac.compare_digest(
                projection["candidate_commitment"], decision["candidate_commitment"]
            )
        ):
            raise VerificationError("projection classification does not match decision")
        _validate_projection_record(kind, decision_id, projection["record"])
        expected_candidate = keyed_commitment(
            policy.commitment_key,
            "identity-candidate-v1",
            {
                "run_id": str(run_id),
                "decision_id": decision_id,
                "decision_kind": kind,
                "record": projection["record"],
            },
        )
        expected_projection = keyed_commitment(
            policy.commitment_key,
            "identity-semantic-projection-v1",
            {
                "run_id": str(run_id),
                "decision_id": decision_id,
                "decision_kind": kind,
                "projection_table_kind": projection["projection_table_kind"],
                "record": projection["record"],
            },
        )
        expected_ref = keyed_commitment(
            policy.commitment_key,
            "identity-projection-reference-v1",
            {
                "run_id": str(run_id),
                "decision_id": decision_id,
                "projection_table_kind": projection["projection_table_kind"],
                "record_identity": _record_identity(kind, projection["record"]),
            },
        )
        expected_receipt = keyed_commitment(
            policy.commitment_key,
            "identity-projection-receipt-v1",
            {
                "run_id": str(run_id),
                "decision_id": decision_id,
                "receipt_id": receipt_id,
                "candidate_commitment": expected_candidate,
                "projection_ref_commitment": expected_ref,
                "projection_commitment": expected_projection,
            },
        )
        for actual, expected, label in (
            (projection["candidate_commitment"], expected_candidate, "candidate"),
            (projection["projection_commitment"], expected_projection, "projection"),
            (
                projection["projection_ref_commitment"],
                expected_ref,
                "projection reference",
            ),
            (projection["receipt_commitment"], expected_receipt, "projection receipt"),
        ):
            _hex64(actual, label)
            if not hmac.compare_digest(actual, expected):
                raise VerificationError(f"{label} commitment mismatch")
        projection_by_decision[decision_id] = projection
        verified_projections.append(projection)
    if set(projection_by_decision) != set(apply_decisions):
        raise VerificationError("projection set is incomplete")
    if list(projection_by_decision) != list(apply_decisions):
        raise VerificationError("projection set is not in reviewed decision order")

    decisions_by_source: dict[str, list[Mapping[str, Any]]] = {
        source_id: [] for source_id in source_by_id
    }
    for decision in decision_by_id.values():
        decisions_by_source[decision["source_item_id"]].append(decision)
    for source_id, source_decisions in decisions_by_source.items():
        allowed_decisions: list[dict[str, Any]] = []
        for decision in source_decisions:
            disposition = decision["disposition"]
            record = None
            if disposition == "apply":
                record = projection_by_decision[decision["decision_id"]]["record"]
            elif disposition == "coalesced_duplicate":
                record = projection_by_decision[
                    decision["canonical_apply_decision_id"]
                ]["record"]
            allowed_decisions.append(
                {
                    "decision_kind": decision["decision_kind"],
                    "disposition": disposition,
                    "record": record,
                }
            )
        expected_allowed_projection = {"decisions": allowed_decisions}
        if canonical_bytes(
            private_source_by_id[source_id]["allowed_projection"]
        ) != canonical_bytes(expected_allowed_projection):
            raise VerificationError(
                "private source allowed projection does not match reviewed decisions"
            )

    expected_source_root = keyed_commitment(
        policy.commitment_key,
        "identity-source-manifest-v1",
        sources,
    )
    expected_projection_root = keyed_commitment(
        policy.commitment_key,
        "identity-projection-manifest-v1",
        verified_projections,
    )
    if not hmac.compare_digest(
        run["logical_source_manifest_commitment"], expected_source_root
    ) or not hmac.compare_digest(
        run["projection_manifest_commitment"], expected_projection_root
    ):
        raise VerificationError("manifest root commitment mismatch")
    unsigned_run = {
        key: value for key, value in run.items() if key != "review_signature"
    }
    review_commitment_run = {
        key: value
        for key, value in unsigned_run.items()
        if key != "review_receipt_commitment"
    }
    expected_review_receipt = keyed_commitment(
        policy.commitment_key,
        "identity-review-receipt-v1",
        {
            "run": review_commitment_run,
            "source_items": sources,
            "decisions": decisions,
            "projections": verified_projections,
        },
    )
    if not hmac.compare_digest(
        run["review_receipt_commitment"], expected_review_receipt
    ):
        raise VerificationError("review receipt commitment mismatch")
    signature_hex = run["review_signature"]
    if not isinstance(signature_hex, str) or not HEX128.fullmatch(signature_hex):
        raise VerificationError("review signature is invalid")
    signed_document = canonical_bytes(
        {
            "domain": "reviewed-identity-migration-review-v1",
            "run": unsigned_run,
            "source_items": sources,
            "decisions": decisions,
            "projections": verified_projections,
        }
    )
    try:
        policy.review_public_key.verify(bytes.fromhex(signature_hex), signed_document)
    except (InvalidSignature, ValueError) as error:
        raise VerificationError("review signature verification failed") from error
    return VerifiedProjectionBundle(
        run_id=run_id,
        review_receipt_commitment=run["review_receipt_commitment"],
        projection_manifest_commitment=run["projection_manifest_commitment"],
        _projections_canonical=canonical_bytes(verified_projections),
        verified_at=verified_at,
    )


def _record_identity(kind: str, record: Mapping[str, Any]) -> Mapping[str, Any]:
    if kind in {"person", "person_status"}:
        return {"person_id": record["person_id"]}
    if kind == "privacy_directive":
        return {"directive_id": record["directive_id"]}
    if kind == "alias":
        return {"alias_id": record["alias_id"]}
    if kind == "recognition_binding":
        return {"binding_id": record["binding_id"]}
    if kind == "legacy_role_candidate":
        return {"label_id": record["label_id"]}
    if kind == "legacy_relationship_candidate":
        return {"candidate_id": record["candidate_id"]}
    raise VerificationError("projection record identity is unsupported")
