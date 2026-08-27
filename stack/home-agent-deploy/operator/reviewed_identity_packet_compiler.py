#!/usr/bin/env python3
"""Compile a complete reviewed legacy-People projection packet offline.

The compiler consumes only the minimized query-only source inventory and the
already reviewed migration plan.  Every selected SQLite row becomes one source
item.  Rows that are intentionally excluded receive an explicit omission
decision; none can disappear through an aggregate count.  The output remains
non-authoritative and cannot connect to PostgreSQL or Home Assistant.

Review and finalization private keys are deliberately absent.  This module
emits exact review signing bytes, verifies a supplied review signature against
the deployment-pinned public key, and hands the resulting canonical bundle to
``reviewed_identity_payload`` for independent verification and compilation of
the distinct finalization signing payload.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import hashlib
import secrets
import time
from typing import Any, Callable, Mapping, Sequence
import uuid

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from migrate_legacy_identity import (
    ALIAS_ENDPOINT_TEMPLATE,
    PERSON_ENDPOINT,
    PERSON_STATUS_ENDPOINT_TEMPLATE,
    PRIVACY_DIRECTIVE_ENDPOINT_TEMPLATE,
    RECOGNITION_BINDING_ENDPOINT_TEMPLATE,
    RELATIONSHIP_CANDIDATE_ENDPOINT,
    ROLE_ENDPOINT,
    ApplyOperation,
    LegacySourceInventoryRow,
    MigrationError,
    MigrationPlan,
    normalized_alias as _normalized_alias,
    prepare_apply,
)
from reviewed_identity_payload import (
    PROJECTION_TABLES,
    VerificationError,
    VerificationPolicy,
    canonical_bytes,
    keyed_commitment,
    parse_canonical_json,
    verify_projection_bundle,
)


CONTRACT = "reviewed-identity-packet-compiler-e5x-v1"
REVIEW_DOMAIN = "reviewed-identity-migration-review-v1"
MAX_ITEMS = 10_000
KIND_ORDER = {
    "person": 0,
    "privacy_directive": 1,
    "person_status": 2,
    "alias": 3,
    "recognition_binding": 4,
    "legacy_role_candidate": 5,
    "legacy_relationship_candidate": 6,
}


class PacketCompilerError(RuntimeError):
    """A content-free reviewed-packet compiler failure."""


def uuid7() -> uuid.UUID:
    """Generate an RFC 9562 UUIDv7 without importing the Core runtime."""

    timestamp_ms = time.time_ns() // 1_000_000
    if timestamp_ms < 0 or timestamp_ms >= 1 << 48:
        raise PacketCompilerError("system clock cannot produce UUIDv7")
    random_bits = secrets.randbits(74)
    value = timestamp_ms << 80
    value |= 0x7 << 76
    value |= ((random_bits >> 62) & 0xFFF) << 64
    value |= 0b10 << 62
    value |= random_bits & ((1 << 62) - 1)
    return uuid.UUID(int=value)


def _require_uuid7(value: uuid.UUID, label: str) -> str:
    if not isinstance(value, uuid.UUID) or value.version != 7:
        raise PacketCompilerError(f"{label} generator did not produce UUIDv7")
    return str(value)


def _hex64(value: str, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise PacketCompilerError(f"{label} digest is invalid")
    return value


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None:
        raise PacketCompilerError("packet compiler clock must be timezone-aware")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _capabilities() -> frozenset[tuple[str, str]]:
    return frozenset(
        {
            ("post", PERSON_ENDPOINT),
            ("post", ROLE_ENDPOINT),
            ("post", ALIAS_ENDPOINT_TEMPLATE),
            ("post", RECOGNITION_BINDING_ENDPOINT_TEMPLATE),
            ("post", PRIVACY_DIRECTIVE_ENDPOINT_TEMPLATE),
            ("post", PERSON_STATUS_ENDPOINT_TEMPLATE),
            ("post", RELATIONSHIP_CANDIDATE_ENDPOINT),
            ("field", "PersonCreate.legacy_source_sha256"),
            ("idempotency", "exact-projection-v1"),
        }
    )


def _projection_record(operation: ApplyOperation, decision_id: str) -> dict[str, Any]:
    payload = dict(operation.payload)
    if operation.kind == "person":
        return payload
    if operation.kind == "privacy_directive":
        return {
            "directive_id": decision_id,
            "person_id": operation.person_id,
            **payload,
        }
    if operation.kind == "person_status":
        return {"person_id": operation.person_id, **payload}
    if operation.kind == "alias":
        return {
            "alias_id": decision_id,
            "person_id": operation.person_id,
            "alias": payload["alias"],
            "normalized_alias": _normalized_alias(payload["alias"]),
            "alias_kind": payload["alias_kind"],
            "source_ref": payload["source_ref"],
            "source_version": payload["source_version"],
            "source_snapshot_sha256": payload["source_snapshot_sha256"],
        }
    if operation.kind == "recognition_binding":
        return {
            "binding_id": decision_id,
            "person_id": operation.person_id,
            **payload,
        }
    if operation.kind == "legacy_role_candidate":
        return {
            "label_id": decision_id,
            "person_id": payload["person_id"],
            "role_label": payload["role_label"],
            "perspective": "unknown",
            "source_ref": payload["source_ref"],
            "source_version": payload["source_version"],
            "source_snapshot_sha256": payload["source_snapshot_sha256"],
        }
    if operation.kind == "legacy_relationship_candidate":
        return {
            "candidate_id": decision_id,
            "from_person_id": payload["from_person_id"],
            "to_person_id": payload["to_person_id"],
            "relationship_label": payload["relationship_label"],
            "relationship_status": payload["relationship_status"],
            "perspective": "unknown",
            "authoritative": False,
            "source_ref": payload["source_ref"],
            "source_snapshot_sha256": payload["source_snapshot_sha256"],
        }
    raise PacketCompilerError("packet compiler received an unsupported operation")


def _record_identity(kind: str, record: Mapping[str, Any]) -> dict[str, str]:
    if kind in {"person", "person_status"}:
        return {"person_id": str(record["person_id"])}
    field = {
        "privacy_directive": "directive_id",
        "alias": "alias_id",
        "recognition_binding": "binding_id",
        "legacy_role_candidate": "label_id",
        "legacy_relationship_candidate": "candidate_id",
    }[kind]
    return {field: str(record[field])}


def _operation_key(operation: ApplyOperation) -> tuple[Any, ...]:
    review = operation.review
    if operation.kind == "alias":
        return (
            "identity_aliases",
            operation.person_id,
            review["alias"],
            review["alias_kind"],
        )
    if operation.kind == "recognition_binding":
        return (
            "enrollments",
            operation.person_id,
            review["external_id"],
            review["status"],
        )
    if operation.kind == "legacy_relationship_candidate":
        return (
            "relationships",
            review["from_person_id"],
            review["to_person_id"],
            review["relationship_label"],
            review["relationship_status"],
        )
    return ("identities", operation.person_id)


def _source_key(row: LegacySourceInventoryRow) -> tuple[Any, ...]:
    value = row.values
    if row.source_table_kind == "identity_aliases":
        return (
            "identity_aliases",
            value["identity_uuid"],
            value["alias"],
            value["alias_kind"],
        )
    if row.source_table_kind == "enrollments":
        return (
            "enrollments",
            value["identity_uuid"],
            value["frigate_person_name"],
            "retired" if value["retired_at"] is not None else "active",
        )
    if row.source_table_kind == "relationships":
        return (
            "relationships",
            value["from_uuid"],
            value["to_uuid"],
            value["rel_type"],
            value["status"],
        )
    if row.source_table_kind == "identities":
        return ("identities", value["uuid"])
    return ("schema_meta", value["key"])


def _sort_operations(operation: ApplyOperation) -> tuple[Any, ...]:
    payload = operation.payload
    return (
        KIND_ORDER[operation.kind],
        str(payload.get("directive", "")),
        str(payload.get("role_label", "")),
        str(payload.get("source_ref", "")),
    )


@dataclass(frozen=True, slots=True)
class UnsignedReviewedIdentityPacket:
    """Immutable private compiler result awaiting the review signature."""

    contract: str
    run_id: uuid.UUID
    private_review_sha256: str
    _unsigned_run_canonical: bytes = field(repr=False)
    _source_items_canonical: bytes = field(repr=False)
    _decisions_canonical: bytes = field(repr=False)
    _projections_canonical: bytes = field(repr=False)
    _source_records_canonical: bytes = field(repr=False)
    _review_signing_payload: bytes = field(repr=False)
    _review_public_key: Ed25519PublicKey = field(repr=False)

    @property
    def review_signing_payload(self) -> bytes:
        return bytes(self._review_signing_payload)

    @property
    def source_records(self) -> list[Mapping[str, Any]]:
        value = parse_canonical_json(self._source_records_canonical)
        if not isinstance(value, list):
            raise AssertionError("compiler source records are malformed")
        return value

    def private_state(self) -> dict[str, Any]:
        """Return canonicalizable private state for an fsynced ceremony."""

        return {
            "contract": CONTRACT,
            "run_id": str(self.run_id),
            "private_review_sha256": self.private_review_sha256,
            "unsigned_run": parse_canonical_json(self._unsigned_run_canonical),
            "source_items": parse_canonical_json(self._source_items_canonical),
            "decisions": parse_canonical_json(self._decisions_canonical),
            "projections": parse_canonical_json(self._projections_canonical),
            "source_records": parse_canonical_json(self._source_records_canonical),
            "review_signing_payload_sha256": hashlib.sha256(
                self._review_signing_payload
            ).hexdigest(),
        }

    def assemble_reviewed_bundle(self, signature_hex: str) -> bytes:
        if (
            not isinstance(signature_hex, str)
            or len(signature_hex) != 128
            or any(character not in "0123456789abcdef" for character in signature_hex)
        ):
            raise PacketCompilerError("review signature is invalid")
        try:
            self._review_public_key.verify(
                bytes.fromhex(signature_hex), self._review_signing_payload
            )
        except (InvalidSignature, ValueError) as error:
            raise PacketCompilerError("review signature verification failed") from error
        run = parse_canonical_json(self._unsigned_run_canonical)
        sources = parse_canonical_json(self._source_items_canonical)
        decisions = parse_canonical_json(self._decisions_canonical)
        projections = parse_canonical_json(self._projections_canonical)
        if not isinstance(run, dict):
            raise AssertionError("compiler run is malformed")
        run["review_signature"] = signature_hex
        return canonical_bytes(
            {
                "contract_version": "reviewed-identity-projection-bundle-v1",
                "manifest": {
                    "run": run,
                    "source_items": sources,
                    "decisions": decisions,
                },
                "projections": projections,
            }
        )


def restore_unsigned_packet(
    value: Mapping[str, Any],
    *,
    review_public_key: Ed25519PublicKey,
) -> UnsignedReviewedIdentityPacket:
    """Restore exact compiler state without regenerating any identifier."""

    expected = {
        "contract",
        "run_id",
        "private_review_sha256",
        "unsigned_run",
        "source_items",
        "decisions",
        "projections",
        "source_records",
        "review_signing_payload_sha256",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise PacketCompilerError("private signing state shape is invalid")
    if value["contract"] != CONTRACT:
        raise PacketCompilerError("private signing state contract is invalid")
    try:
        run_id = uuid.UUID(str(value["run_id"]))
    except (ValueError, TypeError, AttributeError) as error:
        raise PacketCompilerError("private signing state run is invalid") from error
    _require_uuid7(run_id, "restored run")
    private_review_sha256 = _hex64(value["private_review_sha256"], "private review")
    unsigned_run = value["unsigned_run"]
    source_items = value["source_items"]
    decisions = value["decisions"]
    projections = value["projections"]
    source_records = value["source_records"]
    if (
        not isinstance(unsigned_run, Mapping)
        or unsigned_run.get("run_id") != str(run_id)
        or not all(
            isinstance(item, list)
            for item in (source_items, decisions, projections, source_records)
        )
    ):
        raise PacketCompilerError("private signing state content is invalid")
    if not source_records or any(
        not isinstance(record, Mapping)
        or not isinstance(record.get("row_key"), Mapping)
        or record["row_key"].get("private_review_sha256") != private_review_sha256
        for record in source_records
    ):
        raise PacketCompilerError("private signing state review binding is invalid")
    review_payload = canonical_bytes(
        {
            "domain": REVIEW_DOMAIN,
            "run": unsigned_run,
            "source_items": source_items,
            "decisions": decisions,
            "projections": projections,
        }
    )
    expected_digest = _hex64(
        value["review_signing_payload_sha256"], "review signing payload"
    )
    if hashlib.sha256(review_payload).hexdigest() != expected_digest:
        raise PacketCompilerError("private signing state commitment mismatch")
    return UnsignedReviewedIdentityPacket(
        contract=CONTRACT,
        run_id=run_id,
        private_review_sha256=private_review_sha256,
        _unsigned_run_canonical=canonical_bytes(unsigned_run),
        _source_items_canonical=canonical_bytes(source_items),
        _decisions_canonical=canonical_bytes(decisions),
        _projections_canonical=canonical_bytes(projections),
        _source_records_canonical=canonical_bytes(source_records),
        _review_signing_payload=review_payload,
        _review_public_key=review_public_key,
    )


def compile_unsigned_packet(
    plan: MigrationPlan,
    inventory: Sequence[LegacySourceInventoryRow],
    *,
    private_review_sha256: str,
    policy: VerificationPolicy,
    id_factory: Callable[[], uuid.UUID] = uuid7,
    now: datetime | None = None,
) -> UnsignedReviewedIdentityPacket:
    """Compile exact review signing bytes without possessing a signing key."""

    _hex64(private_review_sha256, "private review")
    if not inventory or len(inventory) > MAX_ITEMS:
        raise PacketCompilerError("legacy source inventory count is invalid")
    raw_timestamp = now or policy.now()
    if raw_timestamp.tzinfo is None:
        raise PacketCompilerError("packet compiler clock must be timezone-aware")
    timestamp = raw_timestamp.astimezone(UTC)
    run_id = _require_uuid7(id_factory(), "run")
    operator_request_id = _require_uuid7(id_factory(), "operator request")
    try:
        preparation = prepare_apply(plan, _capabilities())
    except MigrationError as error:
        raise PacketCompilerError(
            "reviewed semantic projection could not compile"
        ) from error

    operation_groups: dict[tuple[Any, ...], list[ApplyOperation]] = {}
    for operation in preparation.operations:
        operation_groups.setdefault(_operation_key(operation), []).append(operation)
    for operations in operation_groups.values():
        operations.sort(key=_sort_operations)

    source_items: list[dict[str, Any]] = []
    source_records: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    projections: list[dict[str, Any]] = []
    consumed_operations = 0
    for source_ordinal, row in enumerate(inventory):
        source_item_id = _require_uuid7(id_factory(), "source item")
        source_row_sha256 = hashlib.sha256(canonical_bytes(row.values)).hexdigest()
        row_key = {
            **dict(row.row_key),
            "private_review_sha256": private_review_sha256,
            "source_row_sha256": source_row_sha256,
        }
        operations = operation_groups.pop(_source_key(row), [])
        if row.source_table_kind == "schema_meta":
            omission = "out_of_scope_by_rule"
        elif not operations:
            omission = (
                "out_of_scope_by_rule"
                if row.source_table_kind == "identity_aliases"
                and row.values.get("alias_kind") == "frigate_name"
                else "privacy_suppressed"
            )
        else:
            omission = None
        allowed: list[dict[str, Any]] = []
        if omission is not None:
            decision_id = _require_uuid7(id_factory(), "decision")
            decision = {
                "decision_id": decision_id,
                "source_item_id": source_item_id,
                "ordinal": len(decisions),
                "decision_kind": "explicit_omission",
                "disposition": omission,
                "candidate_commitment": None,
                "canonical_apply_decision_id": None,
                "canonical_apply_disposition": None,
                "decision_commitment": "",
            }
            decision["decision_commitment"] = keyed_commitment(
                policy.commitment_key,
                "identity-decision-v1",
                {
                    key: value
                    for key, value in decision.items()
                    if key != "decision_commitment"
                },
            )
            decisions.append(decision)
            allowed.append(
                {
                    "decision_kind": "explicit_omission",
                    "disposition": omission,
                    "record": None,
                }
            )
        else:
            for operation in operations:
                consumed_operations += 1
                decision_id = _require_uuid7(id_factory(), "decision")
                receipt_id = _require_uuid7(id_factory(), "receipt")
                record = _projection_record(operation, decision_id)
                kind = operation.kind
                candidate = keyed_commitment(
                    policy.commitment_key,
                    "identity-candidate-v1",
                    {
                        "run_id": run_id,
                        "decision_id": decision_id,
                        "decision_kind": kind,
                        "record": record,
                    },
                )
                decision = {
                    "decision_id": decision_id,
                    "source_item_id": source_item_id,
                    "ordinal": len(decisions),
                    "decision_kind": kind,
                    "disposition": "apply",
                    "candidate_commitment": candidate,
                    "canonical_apply_decision_id": None,
                    "canonical_apply_disposition": None,
                    "decision_commitment": "",
                }
                decision["decision_commitment"] = keyed_commitment(
                    policy.commitment_key,
                    "identity-decision-v1",
                    {
                        key: value
                        for key, value in decision.items()
                        if key != "decision_commitment"
                    },
                )
                table = PROJECTION_TABLES[kind]
                projection_commitment = keyed_commitment(
                    policy.commitment_key,
                    "identity-semantic-projection-v1",
                    {
                        "run_id": run_id,
                        "decision_id": decision_id,
                        "decision_kind": kind,
                        "projection_table_kind": table,
                        "record": record,
                    },
                )
                projection_ref = keyed_commitment(
                    policy.commitment_key,
                    "identity-projection-reference-v1",
                    {
                        "run_id": run_id,
                        "decision_id": decision_id,
                        "projection_table_kind": table,
                        "record_identity": _record_identity(kind, record),
                    },
                )
                receipt = keyed_commitment(
                    policy.commitment_key,
                    "identity-projection-receipt-v1",
                    {
                        "run_id": run_id,
                        "decision_id": decision_id,
                        "receipt_id": receipt_id,
                        "candidate_commitment": candidate,
                        "projection_ref_commitment": projection_ref,
                        "projection_commitment": projection_commitment,
                    },
                )
                decisions.append(decision)
                projections.append(
                    {
                        "decision_id": decision_id,
                        "decision_kind": kind,
                        "candidate_commitment": candidate,
                        "projection_table_kind": table,
                        "projection_ref_commitment": projection_ref,
                        "projection_commitment": projection_commitment,
                        "receipt_id": receipt_id,
                        "receipt_commitment": receipt,
                        "record": record,
                    }
                )
                allowed.append(
                    {
                        "decision_kind": kind,
                        "disposition": "apply",
                        "record": record,
                    }
                )
        allowed_projection = {"decisions": allowed}
        source_records.append(
            {
                "source_item_id": source_item_id,
                "source_table_kind": row.source_table_kind,
                "row_key": row_key,
                "allowed_projection": allowed_projection,
            }
        )
        source_items.append(
            {
                "source_item_id": source_item_id,
                "ordinal": source_ordinal,
                "source_table_kind": row.source_table_kind,
                "row_key_commitment": keyed_commitment(
                    policy.commitment_key,
                    "identity-source-row-key-v1",
                    {
                        "run_id": run_id,
                        "source_item_id": source_item_id,
                        "source_table_kind": row.source_table_kind,
                        "row_key": row_key,
                    },
                ),
                "allowed_projection_commitment": keyed_commitment(
                    policy.commitment_key,
                    "identity-allowed-projection-v1",
                    {
                        "run_id": run_id,
                        "source_item_id": source_item_id,
                        "source_table_kind": row.source_table_kind,
                        "allowed_projection": allowed_projection,
                    },
                ),
            }
        )
    if operation_groups or consumed_operations != len(preparation.operations):
        raise PacketCompilerError(
            "semantic projection did not cover the source inventory"
        )
    if not decisions or len(decisions) > MAX_ITEMS or len(projections) > MAX_ITEMS:
        raise PacketCompilerError("reviewed packet item count is invalid")

    run = {
        "run_id": run_id,
        "operator_request_id": operator_request_id,
        "contract_version": "reviewed-identity-migration-run-v1",
        "source_schema_version": plan.schema_version,
        "source_projection_contract_version": "legacy-identity-source-projection-v1",
        "importer_version": "legacy-identity-importer-v1",
        "canonicalization_version": "identity-canonicalization-v1",
        "projection_version": "semantic-people-projection-v1",
        "shadow_rule_version": "record-only-envelope-worker-gate-v3",
        "commitment_algorithm": "hmac-sha256-v1",
        "commitment_key_fingerprint": policy.commitment_key_fingerprint,
        "commitment_key_epoch": policy.commitment_key_epoch,
        "source_item_count": len(source_items),
        "decision_count": len(decisions),
        "logical_source_manifest_commitment": keyed_commitment(
            policy.commitment_key, "identity-source-manifest-v1", source_items
        ),
        "projection_manifest_commitment": keyed_commitment(
            policy.commitment_key, "identity-projection-manifest-v1", projections
        ),
        "source_projection_contract_digest": policy.source_projection_contract_digest,
        "review_receipt_commitment": "",
        "policy_version": policy.policy_version,
        "policy_digest": policy.policy_digest,
        "shadow_authorization_id": str(policy.shadow_authorization_id),
        "release_manifest_digest": policy.release_manifest_digest,
        "migration_tool_bundle_digest": policy.migration_tool_bundle_digest,
        "core_oci_manifest_digest": policy.core_oci_manifest_digest,
        "core_schema_digest": policy.core_schema_digest,
        "core_capability_digest": policy.core_capability_digest,
        "signature_algorithm": "ed25519",
        "signing_key_fingerprint": policy.review_key_fingerprint,
        "review_signature": "",
        "expires_at": _utc_text(timestamp + timedelta(minutes=10)),
    }
    receipt_run = {
        key: value
        for key, value in run.items()
        if key not in {"review_signature", "review_receipt_commitment"}
    }
    run["review_receipt_commitment"] = keyed_commitment(
        policy.commitment_key,
        "identity-review-receipt-v1",
        {
            "run": receipt_run,
            "source_items": source_items,
            "decisions": decisions,
            "projections": projections,
        },
    )
    unsigned_run = {
        key: value for key, value in run.items() if key != "review_signature"
    }
    review_payload = canonical_bytes(
        {
            "domain": REVIEW_DOMAIN,
            "run": unsigned_run,
            "source_items": source_items,
            "decisions": decisions,
            "projections": projections,
        }
    )
    return UnsignedReviewedIdentityPacket(
        contract=CONTRACT,
        run_id=uuid.UUID(run_id),
        private_review_sha256=private_review_sha256,
        _unsigned_run_canonical=canonical_bytes(unsigned_run),
        _source_items_canonical=canonical_bytes(source_items),
        _decisions_canonical=canonical_bytes(decisions),
        _projections_canonical=canonical_bytes(projections),
        _source_records_canonical=canonical_bytes(source_records),
        _review_signing_payload=review_payload,
        _review_public_key=policy.review_public_key,
    )


def verify_assembled_packet(
    packet: UnsignedReviewedIdentityPacket,
    signature_hex: str,
    *,
    policy: VerificationPolicy,
):
    """Assemble and independently verify the signed review bundle."""

    raw = packet.assemble_reviewed_bundle(signature_hex)
    try:
        return verify_projection_bundle(
            raw,
            source_records=packet.source_records,
            policy=policy,
        )
    except VerificationError as error:
        raise PacketCompilerError(
            "assembled review packet failed verification"
        ) from error
