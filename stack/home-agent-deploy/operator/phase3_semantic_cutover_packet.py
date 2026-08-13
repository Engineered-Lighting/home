#!/usr/bin/env python3
"""Compile the separately signed Phase 3 semantic-cutover packet offline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import hmac
from typing import Any, Callable, Mapping
import uuid

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from phase3_privacy_cutover_evidence import (
    CATEGORY_ORDER,
    PrivacyCutoverEvidenceError,
    PrivacyProbePolicy,
    verify_privacy_cutover_evidence,
)
from phase3_writer_freeze_evidence import (
    WriterFreezeEvidenceError,
    WriterFreezePolicy,
    verify_writer_freeze_evidence,
)
from reviewed_identity_packet_compiler import uuid7
from reviewed_identity_payload import (
    VerificationError,
    VerificationPolicy,
    canonical_bytes,
    keyed_commitment,
    parse_canonical_json,
)


CONTRACT = "phase3-semantic-cutover-packet-e5ab-v1"
DOCUMENT_CONTRACT = "reviewed-identity-semantic-cutover-v1"
CANDIDATE_CONTRACT = "semantic-authority-cutover-candidate-v1"
SIGNING_DOMAIN = "reviewed-identity-semantic-cutover-v1"
VERIFIER_BUNDLE_DIGEST = (
    "eae0fd391a19a50a89fab9ee7665760499d7f154bfaf518abe56b8eed2090aa0"
)
ERASURE_CONTRACT = "phase3-erasure-current-receipt-e5j-v1"
ERASURE_SOURCE_REVISION = "0006a_worker_lease_arbitration"
MAX_BYTES = 4 * 1024 * 1024
MAX_EVIDENCE_AGE = timedelta(minutes=5)
DOCUMENT_KEYS = frozenset(
    {
        "admission_id",
        "authority_scope",
        "candidate_cutover_id",
        "contract_version",
        "core_capability_digest",
        "core_schema_digest",
        "cutover_signature",
        "e3_commitment_key_epoch",
        "e3_projection_manifest_commitment",
        "e3_source_manifest_commitment",
        "erasure_ledger_epoch",
        "erasure_ledger_head_hash",
        "erasure_ledger_verification",
        "finalization_id",
        "freeze_commitment",
        "policy_digest",
        "privacy_check_set_commitment",
        "promotion_commitment",
        "promotion_id",
        "release_manifest_digest",
        "run_id",
        "semantic_generation",
        "signature_algorithm",
        "signing_key_fingerprint",
        "source_installation_id",
        "verifier_bundle_digest",
        "writer_freeze_id",
    }
)
CANDIDATE_KEYS = frozenset(
    {
        "cutover_id",
        "run_id",
        "finalization_id",
        "writer_evidence_id",
        "contract_version",
        "authority_status",
        "authoritative",
        "ingress_check_id",
        "ingress_check_category",
        "retrieval_check_id",
        "retrieval_check_category",
        "prompt_check_id",
        "prompt_check_category",
        "initiative_check_id",
        "initiative_check_category",
        "export_check_id",
        "export_check_category",
        "edge_block_check_id",
        "edge_block_check_category",
        "required_privacy_check_result",
        "required_privacy_residual_code",
        "privacy_check_set_commitment",
        "cutover_commitment",
        "policy_digest",
        "signature_algorithm",
        "signing_key_fingerprint",
        "cutover_signature",
        "attested_at",
    }
)
PACKET_KEYS = frozenset(
    {
        "contract",
        "authoritative",
        "writer_freeze_evidence",
        "privacy_cutover_evidence",
        "erasure_current_receipt",
        "semantic_authority_candidate",
        "cutover_document",
        "cutover_document_sha256",
    }
)
ERASURE_KEYS = frozenset(
    {
        "contract",
        "backup_label",
        "schema_revision",
        "ledger_epoch",
        "ledger_head_hash",
        "verified_at",
        "gate_status",
    }
)


class SemanticCutoverPacketError(RuntimeError):
    """A content-free semantic-cutover packet failure."""


@dataclass(frozen=True, slots=True)
class SemanticCutoverPolicy:
    public_key: Ed25519PublicKey
    key_fingerprint: str
    key_purpose: str = "identity_semantic_cutover"
    key_revoked: bool = False

    def __post_init__(self) -> None:
        if self.key_purpose != "identity_semantic_cutover":
            raise ValueError("semantic-cutover public key has the wrong purpose")
        if self.key_revoked:
            raise ValueError("semantic-cutover public key is revoked")
        raw = _raw_public(self.public_key)
        if not hmac.compare_digest(
            hashlib.sha256(raw).hexdigest(), self.key_fingerprint
        ):
            raise ValueError("semantic-cutover public key fingerprint mismatch")


def _raw_public(key: Ed25519PublicKey) -> bytes:
    return key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def _exact(value: Any, keys: frozenset[str], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise SemanticCutoverPacketError(f"{label} shape is invalid")
    return dict(value)


def _uuid(value: Any, label: str) -> str:
    try:
        parsed = uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError) as error:
        raise SemanticCutoverPacketError(f"{label} identifier is invalid") from error
    if str(parsed) != value or parsed.version != 7:
        raise SemanticCutoverPacketError(f"{label} identifier is invalid")
    return str(parsed)


def _id(factory: Callable[[], uuid.UUID], label: str) -> str:
    value = factory()
    if not isinstance(value, uuid.UUID) or value.version != 7:
        raise SemanticCutoverPacketError(f"{label} generator did not produce UUIDv7")
    return str(value)


def _hex64(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise SemanticCutoverPacketError(f"{label} digest is invalid")
    return value


def _time(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise SemanticCutoverPacketError(f"{label} timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise SemanticCutoverPacketError(f"{label} timestamp is invalid") from error
    if (
        parsed.tzinfo is None
        or parsed.astimezone(UTC) != parsed
        or parsed.strftime("%Y-%m-%dT%H:%M:%S.%fZ") != value
    ):
        raise SemanticCutoverPacketError(f"{label} timestamp is noncanonical")
    return parsed


def _erasure_receipt(value: Mapping[str, Any]) -> dict[str, Any]:
    receipt = _exact(value, ERASURE_KEYS, "erasure-current receipt")
    if (
        receipt["contract"] != ERASURE_CONTRACT
        or receipt["schema_revision"] != ERASURE_SOURCE_REVISION
        or receipt["gate_status"] != "current"
        or type(receipt["ledger_epoch"]) is not int
        or receipt["ledger_epoch"] < 0
        or not isinstance(receipt["backup_label"], str)
    ):
        raise SemanticCutoverPacketError("erasure-current receipt is invalid")
    _hex64(receipt["ledger_head_hash"], "erasure ledger head")
    if receipt["ledger_epoch"] == 0 and receipt["ledger_head_hash"] != "0" * 64:
        raise SemanticCutoverPacketError("erasure-current receipt is invalid")
    _time(receipt["verified_at"], "erasure verification")
    return receipt


def _require_key(
    private_key: Ed25519PrivateKey,
    cutover_policy: SemanticCutoverPolicy,
    identity_policy: VerificationPolicy,
    writer_policy: WriterFreezePolicy,
    privacy_policy: PrivacyProbePolicy,
) -> None:
    if not isinstance(private_key, Ed25519PrivateKey) or not hmac.compare_digest(
        _raw_public(private_key.public_key()), _raw_public(cutover_policy.public_key)
    ):
        raise SemanticCutoverPacketError("semantic-cutover private key does not match")
    raw = _raw_public(cutover_policy.public_key)
    if any(
        hmac.compare_digest(raw, _raw_public(other))
        for other in (
            identity_policy.review_public_key,
            identity_policy.finalization_public_key,
            writer_policy.public_key,
            privacy_policy.public_key,
        )
    ):
        raise SemanticCutoverPacketError(
            "semantic-cutover signing purpose is not distinct"
        )


def compile_semantic_cutover_packet(
    raw_writer_freeze_evidence: bytes,
    raw_privacy_cutover_evidence: bytes,
    erasure_current_receipt: Mapping[str, Any],
    *,
    identity_policy: VerificationPolicy,
    writer_freeze_policy: WriterFreezePolicy,
    privacy_probe_policy: PrivacyProbePolicy,
    semantic_cutover_policy: SemanticCutoverPolicy,
    semantic_cutover_private_key: Ed25519PrivateKey,
    now: datetime,
    id_factory: Callable[[], uuid.UUID] = uuid7,
) -> bytes:
    """Aggregate and sign evidence without admitting or committing authority."""

    if now.tzinfo is None:
        raise SemanticCutoverPacketError("semantic-cutover clock must be aware")
    now = now.astimezone(UTC)
    _require_key(
        semantic_cutover_private_key,
        semantic_cutover_policy,
        identity_policy,
        writer_freeze_policy,
        privacy_probe_policy,
    )
    try:
        freeze_packet = verify_writer_freeze_evidence(
            raw_writer_freeze_evidence,
            identity_policy=identity_policy,
            writer_freeze_policy=writer_freeze_policy,
        )
        privacy_packet = verify_privacy_cutover_evidence(
            raw_privacy_cutover_evidence,
            identity_policy=identity_policy,
            privacy_probe_policy=privacy_probe_policy,
        )
    except (WriterFreezeEvidenceError, PrivacyCutoverEvidenceError) as error:
        raise SemanticCutoverPacketError(
            "semantic-cutover evidence is invalid"
        ) from error
    erasure = _erasure_receipt(erasure_current_receipt)
    freeze = freeze_packet["enforced_writer_freeze"]
    evidence = freeze_packet["writer_evidence"]
    if (
        privacy_packet["run_id"] != freeze_packet["run_id"]
        or privacy_packet["finalization_id"] != freeze_packet["finalization_id"]
        or privacy_packet["freeze_id"] != freeze["freeze_id"]
        or _time(privacy_packet["privacy_observation"]["checked_at"], "privacy check")
        > now
        or now
        - _time(privacy_packet["privacy_observation"]["checked_at"], "privacy check")
        > MAX_EVIDENCE_AGE
        or _time(erasure["verified_at"], "erasure verification") > now
        or now - _time(erasure["verified_at"], "erasure verification")
        > MAX_EVIDENCE_AGE
    ):
        raise SemanticCutoverPacketError(
            "semantic-cutover evidence is stale or mismatched"
        )
    admission_id = _id(id_factory, "cutover admission")
    promotion_id = _id(id_factory, "authority promotion")
    candidate_id = _id(id_factory, "authority candidate")
    receipts = {item["check_category"]: item for item in privacy_packet["receipts"]}
    if tuple(receipts) != CATEGORY_ORDER:
        raise SemanticCutoverPacketError("privacy receipt order is invalid")
    promotion_material = {
        "admission_id": admission_id,
        "promotion_id": promotion_id,
        "candidate_cutover_id": candidate_id,
        "run_id": freeze_packet["run_id"],
        "finalization_id": freeze_packet["finalization_id"],
        "writer_freeze_id": freeze["freeze_id"],
        "writer_evidence_commitment": evidence["evidence_commitment"],
        "freeze_commitment": freeze["freeze_commitment"],
        "privacy_check_set_commitment": privacy_packet["privacy_check_set_commitment"],
        "erasure_ledger_epoch": erasure["ledger_epoch"],
        "erasure_ledger_head_hash": erasure["ledger_head_hash"],
        "policy_digest": identity_policy.policy_digest,
    }
    promotion_commitment = keyed_commitment(
        identity_policy.commitment_key,
        "identity-semantic-authority-promotion-v1",
        promotion_material,
    )
    candidate = {
        "cutover_id": candidate_id,
        "run_id": freeze_packet["run_id"],
        "finalization_id": freeze_packet["finalization_id"],
        "writer_evidence_id": evidence["evidence_id"],
        "contract_version": CANDIDATE_CONTRACT,
        "authority_status": "candidate_unenforced",
        "authoritative": False,
        **{
            f"{category}_check_id": receipts[category]["check_id"]
            for category in CATEGORY_ORDER
        },
        **{f"{category}_check_category": category for category in CATEGORY_ORDER},
        "required_privacy_check_result": "passed",
        "required_privacy_residual_code": "none",
        "privacy_check_set_commitment": privacy_packet["privacy_check_set_commitment"],
        "cutover_commitment": "",
        "policy_digest": identity_policy.policy_digest,
        "signature_algorithm": "ed25519",
        "signing_key_fingerprint": semantic_cutover_policy.key_fingerprint,
        "cutover_signature": "",
        "attested_at": now.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
    }
    candidate["cutover_commitment"] = keyed_commitment(
        identity_policy.commitment_key,
        "identity-semantic-authority-candidate-v1",
        {
            key: value
            for key, value in candidate.items()
            if key not in {"cutover_commitment", "cutover_signature"}
        },
    )
    document = {
        "contract_version": DOCUMENT_CONTRACT,
        "admission_id": admission_id,
        "promotion_id": promotion_id,
        "run_id": freeze_packet["run_id"],
        "finalization_id": freeze_packet["finalization_id"],
        "candidate_cutover_id": candidate_id,
        "writer_freeze_id": freeze["freeze_id"],
        "authority_scope": "identity_semantics",
        "promotion_commitment": promotion_commitment,
        "privacy_check_set_commitment": privacy_packet["privacy_check_set_commitment"],
        "freeze_commitment": freeze["freeze_commitment"],
        "source_installation_id": freeze["source_installation_id"],
        "semantic_generation": str(freeze["semantic_generation"]),
        "e3_source_manifest_commitment": freeze["e3_source_manifest_commitment"],
        "e3_projection_manifest_commitment": freeze[
            "e3_projection_manifest_commitment"
        ],
        "e3_commitment_key_epoch": str(freeze["e3_commitment_key_epoch"]),
        "erasure_ledger_epoch": str(erasure["ledger_epoch"]),
        "erasure_ledger_head_hash": erasure["ledger_head_hash"],
        "erasure_ledger_verification": "head_matched",
        "release_manifest_digest": freeze["release_manifest_digest"],
        "core_schema_digest": identity_policy.core_schema_digest,
        "core_capability_digest": identity_policy.core_capability_digest,
        "policy_digest": identity_policy.policy_digest,
        "signing_key_fingerprint": semantic_cutover_policy.key_fingerprint,
        "verifier_bundle_digest": VERIFIER_BUNDLE_DIGEST,
        "signature_algorithm": "ed25519",
        "cutover_signature": "",
    }
    signature = semantic_cutover_private_key.sign(
        canonical_bytes(
            {
                "domain": SIGNING_DOMAIN,
                "document": {
                    key: value
                    for key, value in document.items()
                    if key != "cutover_signature"
                },
            }
        )
    ).hex()
    document["cutover_signature"] = signature
    candidate["cutover_signature"] = signature
    result = {
        "contract": CONTRACT,
        "authoritative": False,
        "writer_freeze_evidence": freeze_packet,
        "privacy_cutover_evidence": privacy_packet,
        "erasure_current_receipt": erasure,
        "semantic_authority_candidate": candidate,
        "cutover_document": document,
        "cutover_document_sha256": hashlib.sha256(
            canonical_bytes(document)
        ).hexdigest(),
    }
    raw_result = canonical_bytes(result)
    verify_semantic_cutover_packet(
        raw_result,
        identity_policy=identity_policy,
        writer_freeze_policy=writer_freeze_policy,
        privacy_probe_policy=privacy_probe_policy,
        semantic_cutover_policy=semantic_cutover_policy,
    )
    return raw_result


def verify_semantic_cutover_packet(
    raw: bytes,
    *,
    identity_policy: VerificationPolicy,
    writer_freeze_policy: WriterFreezePolicy,
    privacy_probe_policy: PrivacyProbePolicy,
    semantic_cutover_policy: SemanticCutoverPolicy,
) -> Mapping[str, Any]:
    if type(raw) is not bytes or not raw or len(raw) > MAX_BYTES:
        raise SemanticCutoverPacketError("semantic-cutover packet is invalid")
    try:
        value = parse_canonical_json(raw, maximum=MAX_BYTES)
    except VerificationError as error:
        raise SemanticCutoverPacketError(
            "semantic-cutover packet is invalid"
        ) from error
    packet = _exact(value, PACKET_KEYS, "semantic-cutover packet")
    if packet["contract"] != CONTRACT or packet["authoritative"] is not False:
        raise SemanticCutoverPacketError("semantic-cutover packet contract is invalid")
    try:
        freeze_packet = verify_writer_freeze_evidence(
            canonical_bytes(packet["writer_freeze_evidence"]),
            identity_policy=identity_policy,
            writer_freeze_policy=writer_freeze_policy,
        )
        privacy_packet = verify_privacy_cutover_evidence(
            canonical_bytes(packet["privacy_cutover_evidence"]),
            identity_policy=identity_policy,
            privacy_probe_policy=privacy_probe_policy,
        )
    except (WriterFreezeEvidenceError, PrivacyCutoverEvidenceError) as error:
        raise SemanticCutoverPacketError(
            "semantic-cutover evidence is invalid"
        ) from error
    erasure = _erasure_receipt(packet["erasure_current_receipt"])
    candidate = _exact(
        packet["semantic_authority_candidate"], CANDIDATE_KEYS, "authority candidate"
    )
    document = _exact(packet["cutover_document"], DOCUMENT_KEYS, "cutover document")
    if not all(isinstance(item, str) for item in document.values()):
        raise SemanticCutoverPacketError("cutover document is not string-only")
    document_raw = canonical_bytes(document)
    if packet["cutover_document_sha256"] != hashlib.sha256(document_raw).hexdigest():
        raise SemanticCutoverPacketError("cutover document commitment mismatch")
    freeze = freeze_packet["enforced_writer_freeze"]
    evidence = freeze_packet["writer_evidence"]
    receipts = privacy_packet["receipts"]
    expected_document = {
        "contract_version": DOCUMENT_CONTRACT,
        "admission_id": document["admission_id"],
        "promotion_id": document["promotion_id"],
        "run_id": freeze_packet["run_id"],
        "finalization_id": freeze_packet["finalization_id"],
        "candidate_cutover_id": candidate["cutover_id"],
        "writer_freeze_id": freeze["freeze_id"],
        "authority_scope": "identity_semantics",
        "promotion_commitment": document["promotion_commitment"],
        "privacy_check_set_commitment": privacy_packet["privacy_check_set_commitment"],
        "freeze_commitment": freeze["freeze_commitment"],
        "source_installation_id": freeze["source_installation_id"],
        "semantic_generation": str(freeze["semantic_generation"]),
        "e3_source_manifest_commitment": freeze["e3_source_manifest_commitment"],
        "e3_projection_manifest_commitment": freeze[
            "e3_projection_manifest_commitment"
        ],
        "e3_commitment_key_epoch": str(freeze["e3_commitment_key_epoch"]),
        "erasure_ledger_epoch": str(erasure["ledger_epoch"]),
        "erasure_ledger_head_hash": erasure["ledger_head_hash"],
        "erasure_ledger_verification": "head_matched",
        "release_manifest_digest": freeze["release_manifest_digest"],
        "core_schema_digest": identity_policy.core_schema_digest,
        "core_capability_digest": identity_policy.core_capability_digest,
        "policy_digest": identity_policy.policy_digest,
        "signing_key_fingerprint": semantic_cutover_policy.key_fingerprint,
        "verifier_bundle_digest": VERIFIER_BUNDLE_DIGEST,
        "signature_algorithm": "ed25519",
        "cutover_signature": document["cutover_signature"],
    }
    for key in ("admission_id", "promotion_id", "candidate_cutover_id"):
        _uuid(expected_document[key], key)
    if document != expected_document:
        raise SemanticCutoverPacketError("cutover document lineage is invalid")
    promotion_material = {
        "admission_id": document["admission_id"],
        "promotion_id": document["promotion_id"],
        "candidate_cutover_id": document["candidate_cutover_id"],
        "run_id": document["run_id"],
        "finalization_id": document["finalization_id"],
        "writer_freeze_id": freeze["freeze_id"],
        "writer_evidence_commitment": evidence["evidence_commitment"],
        "freeze_commitment": freeze["freeze_commitment"],
        "privacy_check_set_commitment": privacy_packet["privacy_check_set_commitment"],
        "erasure_ledger_epoch": erasure["ledger_epoch"],
        "erasure_ledger_head_hash": erasure["ledger_head_hash"],
        "policy_digest": identity_policy.policy_digest,
    }
    if not hmac.compare_digest(
        document["promotion_commitment"],
        keyed_commitment(
            identity_policy.commitment_key,
            "identity-semantic-authority-promotion-v1",
            promotion_material,
        ),
    ):
        raise SemanticCutoverPacketError("promotion commitment mismatch")
    expected_candidate = {
        "cutover_id": document["candidate_cutover_id"],
        "run_id": document["run_id"],
        "finalization_id": document["finalization_id"],
        "writer_evidence_id": evidence["evidence_id"],
        "contract_version": CANDIDATE_CONTRACT,
        "authority_status": "candidate_unenforced",
        "authoritative": False,
        **{
            f"{category}_check_id": receipts[index]["check_id"]
            for index, category in enumerate(CATEGORY_ORDER)
        },
        **{f"{category}_check_category": category for category in CATEGORY_ORDER},
        "required_privacy_check_result": "passed",
        "required_privacy_residual_code": "none",
        "privacy_check_set_commitment": privacy_packet["privacy_check_set_commitment"],
        "cutover_commitment": candidate["cutover_commitment"],
        "policy_digest": identity_policy.policy_digest,
        "signature_algorithm": "ed25519",
        "signing_key_fingerprint": semantic_cutover_policy.key_fingerprint,
        "cutover_signature": document["cutover_signature"],
        "attested_at": candidate["attested_at"],
    }
    if candidate != expected_candidate:
        raise SemanticCutoverPacketError("authority candidate lineage is invalid")
    _time(candidate["attested_at"], "candidate attestation")
    candidate_unsigned = {
        key: value
        for key, value in candidate.items()
        if key not in {"cutover_commitment", "cutover_signature"}
    }
    if not hmac.compare_digest(
        candidate["cutover_commitment"],
        keyed_commitment(
            identity_policy.commitment_key,
            "identity-semantic-authority-candidate-v1",
            candidate_unsigned,
        ),
    ):
        raise SemanticCutoverPacketError("authority candidate commitment mismatch")
    signature = document["cutover_signature"]
    if len(signature) != 128:
        raise SemanticCutoverPacketError("semantic-cutover signature is invalid")
    try:
        semantic_cutover_policy.public_key.verify(
            bytes.fromhex(signature),
            canonical_bytes(
                {
                    "domain": SIGNING_DOMAIN,
                    "document": {
                        key: value
                        for key, value in document.items()
                        if key != "cutover_signature"
                    },
                }
            ),
        )
    except (InvalidSignature, ValueError) as error:
        raise SemanticCutoverPacketError("semantic-cutover signature failed") from error
    return packet
