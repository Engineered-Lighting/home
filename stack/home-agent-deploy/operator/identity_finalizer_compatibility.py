"""Compile a dormant erasure-compatibility intent for identity finalization.

This module is deliberately offline and non-deployable.  It authenticates the
reviewed identity bundle and its separate finalization envelope, compares every
verified projection subject with a canonical E2 tombstone snapshot, and emits a
content-minimized intent only when the two sets are disjoint.

The result cannot prove that the supplied tombstone snapshot is complete or
current.  The input represents only ledger-attached E2 rows and cannot represent
an active block whose ledger fields have not yet been attached.  It cannot lock
PostgreSQL state and it cannot authorize, stage, or commit a semantic write.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import hmac
import re
from typing import Any, Mapping, Sequence
import uuid

from reviewed_identity_payload import (
    VerificationError,
    VerificationPolicy,
    canonical_bytes,
    keyed_commitment,
    parse_canonical_json,
    verify_finalization_envelope,
    verify_projection_bundle,
)


E2_CONTRACT_REVISION = "0012_identity_erasure_e2"
SNAPSHOT_CONTRACT_VERSION = "identity-erasure-e2-tombstone-snapshot-v1"
INTENT_CONTRACT_VERSION = "identity-finalizer-erasure-compatibility-v1"

_HEX64 = re.compile(r"[0-9a-f]{64}")
_SNAPSHOT_KEYS = frozenset(
    {
        "contract_version",
        "e2_contract_revision",
        "policy_digest",
        "snapshot_id",
        "observed_at",
        "expires_at",
        "ledger_epoch_high_watermark",
        "rows",
        "snapshot_commitment",
    }
)
_TOMBSTONE_KEYS = frozenset(
    {
        "person_id",
        "block_id",
        "block_commitment",
        "ledger_epoch",
        "ledger_outbox_id",
        "ledger_record_hash",
        "ledger_record_digest",
    }
)
_LINEAGE_KEYS = frozenset(
    {
        "lineage_id",
        "run_id",
        "receipt_id",
        "decision_id",
        "decision_kind",
        "projection_table_kind",
        "projection_id",
        "projection_ref_commitment",
        "subjects",
        "lineage_commitment",
    }
)
_PROJECTION_KINDS = frozenset(
    {
        "person",
        "privacy_directive",
        "person_status",
        "alias",
        "recognition_binding",
        "legacy_role_candidate",
        "legacy_relationship_candidate",
    }
)


@dataclass(frozen=True, slots=True)
class IdentityFinalizerCompatibilityIntent:
    """Content-minimized, permanently write-disabled compatibility result."""

    run_id: uuid.UUID
    finalization_id: uuid.UUID
    finalization_commitment: str
    policy_digest: str
    affected_person_count: int
    affected_person_set_digest: str
    tombstone_count: int
    tombstone_set_digest: str
    snapshot_commitment: str
    ledger_epoch_high_watermark: int
    compatibility_intent_commitment: str
    contract_version: str = field(default=INTENT_CONTRACT_VERSION, init=False)
    e2_contract_revision: str = field(default=E2_CONTRACT_REVISION, init=False)
    compatibility_status: str = field(default="coverage_unproven", init=False)
    observed_snapshot_disjoint: bool = field(default=True, init=False)
    deployment_status: str = field(default="non_deployable", init=False)
    capability_status: str = field(default="capability_disabled", init=False)
    authoritative: bool = field(default=False, init=False)
    commit_ready: bool = field(default=False, init=False)
    enables_writes: bool = field(default=False, init=False)
    atomic_commit_enforced: bool = field(default=False, init=False)
    snapshot_completeness_proven: bool = field(default=False, init=False)
    database_state_current: bool = field(default=False, init=False)
    transaction_lock_enforced: bool = field(default=False, init=False)

    @property
    def audit_payload(self) -> Mapping[str, Any]:
        """Return only pseudonymous identifiers, counts, and commitments."""

        return {
            "contract_version": self.contract_version,
            "e2_contract_revision": self.e2_contract_revision,
            "compatibility_status": self.compatibility_status,
            "observed_snapshot_disjoint": self.observed_snapshot_disjoint,
            "deployment_status": self.deployment_status,
            "capability_status": self.capability_status,
            "authoritative": self.authoritative,
            "commit_ready": self.commit_ready,
            "enables_writes": self.enables_writes,
            "atomic_commit_enforced": self.atomic_commit_enforced,
            "snapshot_completeness_proven": self.snapshot_completeness_proven,
            "database_state_current": self.database_state_current,
            "transaction_lock_enforced": self.transaction_lock_enforced,
            "run_id": str(self.run_id),
            "finalization_id": str(self.finalization_id),
            "finalization_commitment": self.finalization_commitment,
            "policy_digest": self.policy_digest,
            "affected_person_count": self.affected_person_count,
            "affected_person_set_digest": self.affected_person_set_digest,
            "tombstone_count": self.tombstone_count,
            "tombstone_set_digest": self.tombstone_set_digest,
            "snapshot_commitment": self.snapshot_commitment,
            "ledger_epoch_high_watermark": self.ledger_epoch_high_watermark,
            "compatibility_intent_commitment": (self.compatibility_intent_commitment),
        }

    def canonical_audit_payload(self) -> bytes:
        return canonical_bytes(self.audit_payload)


def _canonical_uuid(value: Any, label: str, *, require_v7: bool = False) -> str:
    if not isinstance(value, str):
        raise VerificationError(f"{label} UUID is invalid")
    try:
        parsed = uuid.UUID(value)
    except ValueError as error:
        raise VerificationError(f"{label} UUID is invalid") from error
    if str(parsed) != value or (require_v7 and parsed.version != 7):
        raise VerificationError(f"{label} UUID is not canonical")
    return value


def _canonical_person_id(value: Any) -> str:
    return _canonical_uuid(value, "E2 tombstone person")


def _hex64(value: Any, label: str) -> str:
    if not isinstance(value, str) or _HEX64.fullmatch(value) is None:
        raise VerificationError(f"{label} commitment is invalid")
    return value


def _canonical_time(value: Any, label: str) -> datetime:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z", value) is None
    ):
        raise VerificationError(f"{label} timestamp is not canonical")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    except ValueError as error:
        raise VerificationError(f"{label} timestamp is invalid") from error


def _parse_tombstone_snapshot(
    raw_snapshot: bytes, *, policy: VerificationPolicy
) -> tuple[
    tuple[Mapping[str, Any], ...],
    frozenset[str],
    str,
    int,
    Mapping[str, Any],
]:
    value = parse_canonical_json(raw_snapshot)
    if not isinstance(value, dict) or set(value) != _SNAPSHOT_KEYS:
        raise VerificationError("E2 tombstone snapshot shape is invalid")
    if value["contract_version"] != SNAPSHOT_CONTRACT_VERSION:
        raise VerificationError("E2 tombstone snapshot contract is unsupported")
    if value["e2_contract_revision"] != E2_CONTRACT_REVISION:
        raise VerificationError("E2 tombstone snapshot revision is unsupported")
    supplied_policy = _hex64(value["policy_digest"], "E2 snapshot policy")
    if not hmac.compare_digest(supplied_policy, policy.policy_digest):
        raise VerificationError("E2 tombstone snapshot policy does not match")
    _canonical_uuid(value["snapshot_id"], "E2 tombstone snapshot", require_v7=True)
    observed_at = _canonical_time(value["observed_at"], "E2 snapshot observation")
    expires_at = _canonical_time(value["expires_at"], "E2 snapshot expiry")
    now = policy.now()
    if now.tzinfo is None:
        raise ValueError("verification policy clock must return an aware timestamp")
    now = now.astimezone(UTC)
    if (
        expires_at <= observed_at
        or expires_at - observed_at > timedelta(minutes=5)
        or observed_at > now
        or expires_at <= now
    ):
        raise VerificationError("E2 tombstone snapshot is not current")
    high_watermark = value["ledger_epoch_high_watermark"]
    if type(high_watermark) is not int or high_watermark < 0:
        raise VerificationError("E2 tombstone ledger watermark is invalid")

    raw_rows = value["rows"]
    if not isinstance(raw_rows, list):
        raise VerificationError("E2 tombstone snapshot rows are invalid")

    rows: list[Mapping[str, Any]] = []
    people: set[str] = set()
    block_commitments: set[str] = set()
    ledger_hashes: set[str] = set()
    ledger_digests: set[str] = set()
    block_ids: set[str] = set()
    ledger_epochs: set[int] = set()
    ledger_outbox_ids: set[str] = set()
    prior_person_id: str | None = None
    for raw_row in raw_rows:
        if not isinstance(raw_row, dict) or set(raw_row) != _TOMBSTONE_KEYS:
            raise VerificationError("E2 tombstone row shape is invalid")
        person_id = _canonical_person_id(raw_row["person_id"])
        row = {
            "person_id": person_id,
            "block_id": _canonical_uuid(
                raw_row["block_id"], "E2 retrieval block", require_v7=True
            ),
            "block_commitment": _hex64(
                raw_row["block_commitment"], "E2 retrieval block"
            ),
            "ledger_epoch": raw_row["ledger_epoch"],
            "ledger_outbox_id": _canonical_uuid(
                raw_row["ledger_outbox_id"],
                "E2 ledger outbox",
                require_v7=True,
            ),
            "ledger_record_hash": _hex64(
                raw_row["ledger_record_hash"], "E2 ledger record hash"
            ),
            "ledger_record_digest": _hex64(
                raw_row["ledger_record_digest"], "E2 ledger record digest"
            ),
        }
        if type(row["ledger_epoch"]) is not int or row["ledger_epoch"] <= 0:
            raise VerificationError("E2 tombstone ledger epoch is invalid")
        if prior_person_id is not None and person_id <= prior_person_id:
            raise VerificationError("E2 tombstone rows are not a canonical set")
        if (
            person_id in people
            or row["block_id"] in block_ids
            or row["block_commitment"] in block_commitments
            or row["ledger_epoch"] in ledger_epochs
            or row["ledger_outbox_id"] in ledger_outbox_ids
            or row["ledger_record_hash"] in ledger_hashes
            or row["ledger_record_digest"] in ledger_digests
        ):
            raise VerificationError("E2 tombstone snapshot contains duplicates")
        prior_person_id = person_id
        people.add(person_id)
        block_ids.add(row["block_id"])
        block_commitments.add(row["block_commitment"])
        ledger_epochs.add(row["ledger_epoch"])
        ledger_outbox_ids.add(row["ledger_outbox_id"])
        ledger_hashes.add(row["ledger_record_hash"])
        ledger_digests.add(row["ledger_record_digest"])
        rows.append(row)
    expected_high_watermark = max(ledger_epochs, default=0)
    if high_watermark != expected_high_watermark:
        raise VerificationError("E2 tombstone ledger watermark drifted")
    snapshot_core = {key: value[key] for key in value if key != "snapshot_commitment"}
    expected_snapshot_commitment = keyed_commitment(
        policy.commitment_key,
        "identity-erasure-e2-tombstone-snapshot-v1",
        snapshot_core,
    )
    snapshot_commitment = _hex64(value["snapshot_commitment"], "E2 tombstone snapshot")
    if not hmac.compare_digest(snapshot_commitment, expected_snapshot_commitment):
        raise VerificationError("E2 tombstone snapshot commitment mismatch")
    return (
        tuple(rows),
        frozenset(people),
        snapshot_commitment,
        high_watermark,
        snapshot_core,
    )


def _affected_person_ids(document: Mapping[str, Any]) -> tuple[str, ...]:
    """Extract and cross-check every subject from verified projection lineage."""

    projections = document.get("projections")
    if not isinstance(projections, list):
        raise VerificationError("verified finalizer projection set is invalid")
    affected: set[str] = set()
    run_id = _canonical_uuid(document.get("run_id"), "verified finalizer run")
    for projection in projections:
        if not isinstance(projection, dict):
            raise VerificationError("verified finalizer projection is invalid")
        kind = projection.get("decision_kind")
        record = projection.get("record")
        lineage = projection.get("lineage")
        if (
            kind not in _PROJECTION_KINDS
            or not isinstance(record, dict)
            or not isinstance(lineage, dict)
            or set(lineage) != _LINEAGE_KEYS
        ):
            raise VerificationError("verified finalizer projection is invalid")
        subjects = lineage.get("subjects")
        if not isinstance(subjects, list):
            raise VerificationError("verified finalizer lineage subjects are invalid")

        if kind == "legacy_relationship_candidate":
            expected = (
                ("from", _canonical_person_id(record.get("from_person_id"))),
                ("to", _canonical_person_id(record.get("to_person_id"))),
            )
        else:
            expected = (("primary", _canonical_person_id(record.get("person_id"))),)
        actual: list[tuple[str, str]] = []
        for subject in subjects:
            if not isinstance(subject, dict) or set(subject) != {
                "person_id",
                "subject_role",
            }:
                raise VerificationError("verified finalizer lineage subject is invalid")
            role = subject["subject_role"]
            if not isinstance(role, str):
                raise VerificationError("verified finalizer lineage subject is invalid")
            actual.append((role, _canonical_person_id(subject["person_id"])))
        if tuple(actual) != expected:
            raise VerificationError("verified finalizer lineage subjects drifted")
        projection_id_field = {
            "person": "person_id",
            "person_status": "person_id",
            "privacy_directive": "directive_id",
            "alias": "alias_id",
            "recognition_binding": "binding_id",
            "legacy_role_candidate": "label_id",
            "legacy_relationship_candidate": "candidate_id",
        }[kind]
        expected_projection_id = _canonical_uuid(
            record.get(projection_id_field), "verified finalizer projection"
        )
        for field_name in ("receipt_id", "decision_id"):
            _canonical_uuid(
                projection.get(field_name), f"verified finalizer {field_name}"
            )
        if (
            lineage["run_id"] != run_id
            or lineage["lineage_id"] != projection.get("receipt_id")
            or lineage["receipt_id"] != projection.get("receipt_id")
            or lineage["decision_id"] != projection.get("decision_id")
            or lineage["decision_kind"] != kind
            or lineage["projection_table_kind"]
            != projection.get("projection_table_kind")
            or lineage["projection_id"] != expected_projection_id
            or lineage["projection_ref_commitment"]
            != projection.get("projection_ref_commitment")
        ):
            raise VerificationError("verified finalizer lineage tuple drifted")
        affected.update(person_id for _, person_id in actual)
    return tuple(sorted(affected))


def _reject_erasure_overlap(
    affected_people: Sequence[str], blocked_people: frozenset[str]
) -> None:
    if not set(affected_people).isdisjoint(blocked_people):
        raise VerificationError("identity_erasure_blocked")


def compile_identity_finalizer_compatibility(
    raw_bundle: bytes,
    raw_envelope: bytes,
    raw_tombstone_snapshot: bytes,
    *,
    source_records: Sequence[Mapping[str, Any]],
    policy: VerificationPolicy,
) -> IdentityFinalizerCompatibilityIntent:
    """Authenticate inputs and compile a permanently non-deployable intent.

    First admission uses only the live verification path: expired review
    documents and elapsed auto-expiry directives fail closed.  A caller cannot
    substitute an already-constructed ``VerifiedFinalizerDocument``.
    """

    bundle = verify_projection_bundle(
        raw_bundle,
        source_records=source_records,
        policy=policy,
    )
    finalizer = verify_finalization_envelope(bundle, raw_envelope, policy=policy)
    document = finalizer.document
    affected_people = _affected_person_ids(document)
    (
        tombstone_rows,
        blocked_people,
        snapshot_commitment,
        ledger_high_watermark,
        snapshot_core,
    ) = _parse_tombstone_snapshot(raw_tombstone_snapshot, policy=policy)
    _reject_erasure_overlap(affected_people, blocked_people)

    affected_digest = keyed_commitment(
        policy.commitment_key,
        "identity-finalizer-affected-person-set-v1",
        list(affected_people),
    )
    tombstone_digest = keyed_commitment(
        policy.commitment_key,
        "identity-finalizer-e2-tombstone-set-v1",
        snapshot_core,
    )
    finalization_commitment = _hex64(
        document.get("finalization_commitment"), "identity finalization"
    )
    intent_core = {
        "contract_version": INTENT_CONTRACT_VERSION,
        "e2_contract_revision": E2_CONTRACT_REVISION,
        "compatibility_status": "coverage_unproven",
        "observed_snapshot_disjoint": True,
        "deployment_status": "non_deployable",
        "capability_status": "capability_disabled",
        "authoritative": False,
        "commit_ready": False,
        "enables_writes": False,
        "atomic_commit_enforced": False,
        "snapshot_completeness_proven": False,
        "database_state_current": False,
        "transaction_lock_enforced": False,
        "run_id": str(finalizer.run_id),
        "finalization_id": str(finalizer.finalization_id),
        "finalization_commitment": finalization_commitment,
        "policy_digest": policy.policy_digest,
        "affected_person_count": len(affected_people),
        "affected_person_set_digest": affected_digest,
        "tombstone_count": len(tombstone_rows),
        "tombstone_set_digest": tombstone_digest,
        "snapshot_commitment": snapshot_commitment,
        "ledger_epoch_high_watermark": ledger_high_watermark,
    }
    intent_commitment = keyed_commitment(
        policy.commitment_key,
        "identity-finalizer-erasure-compatibility-intent-v1",
        intent_core,
    )
    return IdentityFinalizerCompatibilityIntent(
        run_id=finalizer.run_id,
        finalization_id=finalizer.finalization_id,
        finalization_commitment=finalization_commitment,
        policy_digest=policy.policy_digest,
        affected_person_count=len(affected_people),
        affected_person_set_digest=affected_digest,
        tombstone_count=len(tombstone_rows),
        tombstone_set_digest=tombstone_digest,
        snapshot_commitment=snapshot_commitment,
        ledger_epoch_high_watermark=ledger_high_watermark,
        compatibility_intent_commitment=intent_commitment,
    )
