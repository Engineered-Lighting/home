#!/usr/bin/env python3
"""Compile and verify physical legacy-writer freeze evidence offline.

This boundary accepts only an already verified identity packet lineage plus a
canonical, content-free observation produced while Home Assistant is stopped.
It binds the observed SQLite trigger fence and its external witness to the
exact E3 source/projection commitments. A third, purpose-pinned Ed25519 key
signs the point-in-time writer evidence and the stronger enforced-freeze row.

The output matches the dormant PostgreSQL E3/E4 relations but remains a
non-authoritative artifact. This module has no filesystem, network, database,
or Home Assistant client.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
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

from reviewed_identity_packet_compiler import (
    PacketCompilerError,
    UnsignedReviewedIdentityPacket,
    uuid7,
)
from reviewed_identity_payload import (
    VerificationError,
    VerificationPolicy,
    canonical_bytes,
    keyed_commitment,
    parse_canonical_json,
    verify_expired_finalization_replay,
)


CONTRACT = "phase3-writer-freeze-evidence-packet-e5z-v1"
OBSERVATION_CONTRACT = "legacy-identity-physical-freeze-observation-v1"
PRIVATE_REVIEW_CONTRACT = "phase3-reviewed-people-private-review-e5x-v1"
EVIDENCE_DOMAIN = "legacy-identity-writer-evidence-v1"
FREEZE_DOMAIN = "enforced-legacy-identity-writer-freeze-v1"
MAX_BYTES = 1024 * 1024
PACKET_KEYS = frozenset(
    {
        "contract",
        "authoritative",
        "run_id",
        "finalization_id",
        "private_review_sha256",
        "physical_observation_sha256",
        "writer_evidence",
        "enforced_writer_freeze",
    }
)
OBSERVATION_KEYS = frozenset(
    {
        "contract",
        "source_installation_id",
        "semantic_generation",
        "source_plan_sha256",
        "database_sha256",
        "schema_digest",
        "trigger_digest",
        "cutover_witness_sha256",
        "home_assistant_state",
        "process_lock_result",
        "write_probe_result",
        "integrity_result",
        "checkpoint_result",
        "journal_result",
        "legacy_context_cutoff_status",
        "recognition_mode",
        "freeze_kernel_build_digest",
        "observed_at",
    }
)
EVIDENCE_KEYS = frozenset(
    {
        "evidence_id",
        "run_id",
        "source_installation_id",
        "semantic_generation",
        "source_projection_commitment",
        "evidence_strength",
        "integrity_result",
        "checkpoint_result",
        "journal_result",
        "legacy_context_cutoff_status",
        "release_manifest_digest",
        "freeze_kernel_build_digest",
        "evidence_commitment",
        "signature_algorithm",
        "signing_key_fingerprint",
        "evidence_signature",
        "observed_at",
    }
)
FREEZE_KEYS = frozenset(
    {
        "freeze_id",
        "run_id",
        "writer_evidence_id",
        "contract_version",
        "enforcement_status",
        "write_probe_result",
        "semantic_write_status",
        "legacy_context_cutoff_status",
        "recognition_mode",
        "source_installation_id",
        "semantic_generation",
        "source_projection_commitment",
        "e3_source_manifest_commitment",
        "e3_projection_manifest_commitment",
        "e3_commitment_key_epoch",
        "writer_evidence_commitment",
        "trigger_set_commitment",
        "blocked_probe_commitment",
        "release_manifest_digest",
        "freeze_kernel_build_digest",
        "policy_digest",
        "freeze_commitment",
        "signature_algorithm",
        "signing_key_fingerprint",
        "freeze_signature",
        "enforced_at",
        "verified_at",
    }
)


class WriterFreezeEvidenceError(RuntimeError):
    """A content-free physical writer-freeze evidence failure."""


@dataclass(frozen=True, slots=True)
class WriterFreezePolicy:
    public_key: Ed25519PublicKey
    key_fingerprint: str
    key_purpose: str = "legacy_identity_writer_freeze"
    key_revoked: bool = False

    def __post_init__(self) -> None:
        if self.key_purpose != "legacy_identity_writer_freeze":
            raise ValueError("writer-freeze public key has the wrong purpose")
        if self.key_revoked:
            raise ValueError("writer-freeze public key is revoked")
        raw = self.public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        if not hmac.compare_digest(
            hashlib.sha256(raw).hexdigest(), self.key_fingerprint
        ):
            raise ValueError("writer-freeze public key fingerprint mismatch")


def _exact(value: Any, keys: frozenset[str], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise WriterFreezeEvidenceError(f"{label} shape is invalid")
    return dict(value)


def _hex64(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise WriterFreezeEvidenceError(f"{label} digest is invalid")
    return value


def _uuid(value: Any, label: str, *, version7: bool = True) -> str:
    try:
        parsed = uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError) as error:
        raise WriterFreezeEvidenceError(f"{label} identifier is invalid") from error
    if str(parsed) != value or (version7 and parsed.version != 7):
        raise WriterFreezeEvidenceError(f"{label} identifier is invalid")
    return str(parsed)


def _time(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise WriterFreezeEvidenceError(f"{label} timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise WriterFreezeEvidenceError(f"{label} timestamp is invalid") from error
    if parsed.tzinfo is None or parsed.astimezone(UTC) != parsed:
        raise WriterFreezeEvidenceError(f"{label} timestamp is invalid")
    canonical = parsed.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    if canonical != value:
        raise WriterFreezeEvidenceError(f"{label} timestamp is noncanonical")
    return parsed


def _id(factory: Callable[[], uuid.UUID], label: str) -> str:
    value = factory()
    if not isinstance(value, uuid.UUID) or value.version != 7:
        raise WriterFreezeEvidenceError(f"{label} generator did not produce UUIDv7")
    return str(value)


def _parse_observation(raw: bytes) -> dict[str, Any]:
    if type(raw) is not bytes or not raw or len(raw) > MAX_BYTES:
        raise WriterFreezeEvidenceError("physical freeze observation is invalid")
    try:
        value = parse_canonical_json(raw, maximum=MAX_BYTES)
    except VerificationError as error:
        raise WriterFreezeEvidenceError(
            "physical freeze observation is invalid"
        ) from error
    observation = _exact(value, OBSERVATION_KEYS, "physical freeze observation")
    if observation["contract"] != OBSERVATION_CONTRACT:
        raise WriterFreezeEvidenceError("physical freeze observation is unsupported")
    _uuid(observation["source_installation_id"], "source installation")
    if observation["semantic_generation"] != 2:
        raise WriterFreezeEvidenceError("semantic fence generation is unsupported")
    for key in (
        "source_plan_sha256",
        "database_sha256",
        "cutover_witness_sha256",
        "freeze_kernel_build_digest",
    ):
        _hex64(observation[key], key)
    for key in ("schema_digest", "trigger_digest"):
        value = observation[key]
        if not isinstance(value, str) or not value.startswith("sha256:"):
            raise WriterFreezeEvidenceError("physical fence digest is invalid")
        _hex64(value.removeprefix("sha256:"), key)
    expected = {
        "home_assistant_state": "stopped",
        "process_lock_result": "exclusive",
        "write_probe_result": "blocked",
        "integrity_result": "passed",
        "checkpoint_result": "complete",
        "journal_result": "clean",
        "legacy_context_cutoff_status": "enforced_cutoff",
        "recognition_mode": "nonauthoritative_only",
    }
    if any(observation[key] != value for key, value in expected.items()):
        raise WriterFreezeEvidenceError("physical freeze observation is insufficient")
    _time(observation["observed_at"], "physical observation")
    return observation


def _private_review(
    artifact: Mapping[str, Any], packet: UnsignedReviewedIdentityPacket
) -> Mapping[str, Any]:
    value = _exact(
        artifact,
        frozenset({"contract", "private_review", "private_review_sha256"}),
        "private People review",
    )
    if value["contract"] != PRIVATE_REVIEW_CONTRACT:
        raise WriterFreezeEvidenceError("private People review is unsupported")
    body = value["private_review"]
    digest = _hex64(value["private_review_sha256"], "private People review")
    if (
        not isinstance(body, Mapping)
        or hashlib.sha256(canonical_bytes(body)).hexdigest() != digest
    ):
        raise WriterFreezeEvidenceError("private People review commitment mismatch")
    if not hmac.compare_digest(digest, packet.private_review_sha256):
        raise WriterFreezeEvidenceError("private People review lineage mismatch")
    return body


def _require_key(
    private_key: Ed25519PrivateKey,
    policy: WriterFreezePolicy,
    identity_policy: VerificationPolicy,
) -> None:
    if not isinstance(private_key, Ed25519PrivateKey):
        raise WriterFreezeEvidenceError("writer-freeze private key is invalid")
    raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    expected = policy.public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    if not hmac.compare_digest(raw, expected):
        raise WriterFreezeEvidenceError("writer-freeze private key does not match")
    for other in (
        identity_policy.review_public_key,
        identity_policy.finalization_public_key,
    ):
        if hmac.compare_digest(
            raw,
            other.public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            ),
        ):
            raise WriterFreezeEvidenceError(
                "writer-freeze signing purpose is not distinct"
            )


def _signing_payload(domain: str, row: Mapping[str, Any]) -> bytes:
    return canonical_bytes({"domain": domain, "row": row})


def compile_writer_freeze_evidence(
    packet: UnsignedReviewedIdentityPacket,
    review_signature: str,
    raw_finalization_envelope: bytes,
    private_review_artifact: Mapping[str, Any],
    raw_physical_observation: bytes,
    *,
    identity_policy: VerificationPolicy,
    writer_freeze_policy: WriterFreezePolicy,
    writer_freeze_private_key: Ed25519PrivateKey,
    id_factory: Callable[[], uuid.UUID] = uuid7,
) -> bytes:
    """Compile signed E3/E4 evidence rows without writing any authority store."""

    _require_key(writer_freeze_private_key, writer_freeze_policy, identity_policy)
    try:
        raw_bundle = packet.assemble_reviewed_bundle(review_signature)
        finalizer = verify_expired_finalization_replay(
            raw_bundle,
            raw_finalization_envelope,
            source_records=packet.source_records,
            policy=identity_policy,
        )
    except (PacketCompilerError, VerificationError) as error:
        raise WriterFreezeEvidenceError(
            "identity finalization lineage is invalid"
        ) from error
    document = finalizer.document
    bundle = parse_canonical_json(raw_bundle, maximum=MAX_BYTES)
    run = bundle["manifest"]["run"]
    review_body = _private_review(private_review_artifact, packet)
    observation = _parse_observation(raw_physical_observation)
    if observation["source_plan_sha256"] != review_body.get("source_plan_sha256"):
        raise WriterFreezeEvidenceError("physical source projection drifted")
    observed_at = observation["observed_at"]
    run_id = _uuid(document["run_id"], "reviewed run")
    finalization_id = _uuid(document["finalization_id"], "finalization")
    if run_id != str(packet.run_id) or finalization_id != run_id:
        raise WriterFreezeEvidenceError("identity finalization lineage drifted")

    evidence = {
        "evidence_id": _id(id_factory, "writer evidence"),
        "run_id": run_id,
        "source_installation_id": observation["source_installation_id"],
        "semantic_generation": observation["semantic_generation"],
        "source_projection_commitment": document["projection_manifest_commitment"],
        "evidence_strength": "observed_stopped",
        "integrity_result": "passed",
        "checkpoint_result": "complete",
        "journal_result": "clean",
        "legacy_context_cutoff_status": "observed_cutoff",
        "release_manifest_digest": run["release_manifest_digest"],
        "freeze_kernel_build_digest": observation["freeze_kernel_build_digest"],
        "evidence_commitment": "",
        "signature_algorithm": "ed25519",
        "signing_key_fingerprint": writer_freeze_policy.key_fingerprint,
        "evidence_signature": "",
        "observed_at": observed_at,
    }
    evidence_unsigned = {
        key: value
        for key, value in evidence.items()
        if key not in {"evidence_commitment", "evidence_signature"}
    }
    evidence["evidence_commitment"] = keyed_commitment(
        identity_policy.commitment_key,
        EVIDENCE_DOMAIN,
        {
            "physical_observation_sha256": hashlib.sha256(
                raw_physical_observation
            ).hexdigest(),
            "row": evidence_unsigned,
        },
    )
    evidence["evidence_signature"] = writer_freeze_private_key.sign(
        _signing_payload(
            EVIDENCE_DOMAIN,
            {
                **evidence_unsigned,
                "evidence_commitment": evidence["evidence_commitment"],
            },
        )
    ).hex()

    freeze = {
        "freeze_id": _id(id_factory, "enforced freeze"),
        "run_id": run_id,
        "writer_evidence_id": evidence["evidence_id"],
        "contract_version": "enforced-legacy-identity-writer-freeze-v1",
        "enforcement_status": "enforced_offline",
        "write_probe_result": "blocked",
        "semantic_write_status": "frozen",
        "legacy_context_cutoff_status": "enforced_cutoff",
        "recognition_mode": "nonauthoritative_only",
        "source_installation_id": observation["source_installation_id"],
        "semantic_generation": observation["semantic_generation"],
        "source_projection_commitment": document["projection_manifest_commitment"],
        "e3_source_manifest_commitment": document["source_manifest_commitment"],
        "e3_projection_manifest_commitment": document["projection_manifest_commitment"],
        "e3_commitment_key_epoch": run["commitment_key_epoch"],
        "writer_evidence_commitment": evidence["evidence_commitment"],
        "trigger_set_commitment": keyed_commitment(
            identity_policy.commitment_key,
            "legacy-identity-trigger-set-v1",
            {
                "schema_digest": observation["schema_digest"],
                "trigger_digest": observation["trigger_digest"],
                "cutover_witness_sha256": observation["cutover_witness_sha256"],
                "database_sha256": observation["database_sha256"],
            },
        ),
        "blocked_probe_commitment": keyed_commitment(
            identity_policy.commitment_key,
            "legacy-identity-blocked-probe-v1",
            {
                "home_assistant_state": observation["home_assistant_state"],
                "process_lock_result": observation["process_lock_result"],
                "write_probe_result": observation["write_probe_result"],
                "checkpoint_result": observation["checkpoint_result"],
                "journal_result": observation["journal_result"],
            },
        ),
        "release_manifest_digest": run["release_manifest_digest"],
        "freeze_kernel_build_digest": observation["freeze_kernel_build_digest"],
        "policy_digest": run["policy_digest"],
        "freeze_commitment": "",
        "signature_algorithm": "ed25519",
        "signing_key_fingerprint": writer_freeze_policy.key_fingerprint,
        "freeze_signature": "",
        "enforced_at": observed_at,
        "verified_at": observed_at,
    }
    freeze_unsigned = {
        key: value
        for key, value in freeze.items()
        if key not in {"freeze_commitment", "freeze_signature"}
    }
    freeze["freeze_commitment"] = keyed_commitment(
        identity_policy.commitment_key, FREEZE_DOMAIN, freeze_unsigned
    )
    freeze["freeze_signature"] = writer_freeze_private_key.sign(
        _signing_payload(
            FREEZE_DOMAIN,
            {
                **freeze_unsigned,
                "freeze_commitment": freeze["freeze_commitment"],
            },
        )
    ).hex()
    result = {
        "contract": CONTRACT,
        "authoritative": False,
        "run_id": run_id,
        "finalization_id": finalization_id,
        "private_review_sha256": packet.private_review_sha256,
        "physical_observation_sha256": hashlib.sha256(
            raw_physical_observation
        ).hexdigest(),
        "writer_evidence": evidence,
        "enforced_writer_freeze": freeze,
    }
    raw_result = canonical_bytes(result)
    verify_writer_freeze_evidence(
        raw_result,
        identity_policy=identity_policy,
        writer_freeze_policy=writer_freeze_policy,
    )
    return raw_result


def verify_writer_freeze_evidence(
    raw: bytes,
    *,
    identity_policy: VerificationPolicy,
    writer_freeze_policy: WriterFreezePolicy,
) -> Mapping[str, Any]:
    """Verify every cross-row commitment and both purpose-pinned signatures."""

    if type(raw) is not bytes or not raw or len(raw) > MAX_BYTES:
        raise WriterFreezeEvidenceError("writer-freeze packet is invalid")
    try:
        value = parse_canonical_json(raw, maximum=MAX_BYTES)
    except VerificationError as error:
        raise WriterFreezeEvidenceError("writer-freeze packet is invalid") from error
    packet = _exact(value, PACKET_KEYS, "writer-freeze packet")
    if packet["contract"] != CONTRACT or packet["authoritative"] is not False:
        raise WriterFreezeEvidenceError("writer-freeze packet contract is invalid")
    run_id = _uuid(packet["run_id"], "reviewed run")
    if _uuid(packet["finalization_id"], "finalization") != run_id:
        raise WriterFreezeEvidenceError("writer-freeze finalization is invalid")
    _hex64(packet["private_review_sha256"], "private review")
    _hex64(packet["physical_observation_sha256"], "physical observation")
    evidence = _exact(packet["writer_evidence"], EVIDENCE_KEYS, "writer evidence")
    freeze = _exact(
        packet["enforced_writer_freeze"], FREEZE_KEYS, "enforced writer freeze"
    )
    if evidence["run_id"] != run_id or freeze["run_id"] != run_id:
        raise WriterFreezeEvidenceError("writer-freeze run lineage is invalid")
    _uuid(evidence["evidence_id"], "writer evidence")
    _uuid(freeze["freeze_id"], "enforced freeze")
    _uuid(evidence["source_installation_id"], "source installation")
    if (
        freeze["writer_evidence_id"] != evidence["evidence_id"]
        or freeze["source_installation_id"] != evidence["source_installation_id"]
        or freeze["semantic_generation"] != evidence["semantic_generation"]
        or freeze["source_projection_commitment"]
        != evidence["source_projection_commitment"]
        or freeze["e3_projection_manifest_commitment"]
        != evidence["source_projection_commitment"]
        or freeze["writer_evidence_commitment"] != evidence["evidence_commitment"]
    ):
        raise WriterFreezeEvidenceError("writer-freeze cross-row lineage is invalid")
    expected_evidence = keyed_commitment(
        identity_policy.commitment_key,
        EVIDENCE_DOMAIN,
        {
            "physical_observation_sha256": packet["physical_observation_sha256"],
            "row": {
                key: value
                for key, value in evidence.items()
                if key not in {"evidence_commitment", "evidence_signature"}
            },
        },
    )
    if not hmac.compare_digest(evidence["evidence_commitment"], expected_evidence):
        raise WriterFreezeEvidenceError("writer evidence commitment mismatch")
    freeze_unsigned = {
        key: value
        for key, value in freeze.items()
        if key not in {"freeze_commitment", "freeze_signature"}
    }
    expected_freeze = keyed_commitment(
        identity_policy.commitment_key, FREEZE_DOMAIN, freeze_unsigned
    )
    if not hmac.compare_digest(freeze["freeze_commitment"], expected_freeze):
        raise WriterFreezeEvidenceError("enforced freeze commitment mismatch")
    for row, signature_field, domain, label in (
        (evidence, "evidence_signature", EVIDENCE_DOMAIN, "writer evidence"),
        (freeze, "freeze_signature", FREEZE_DOMAIN, "enforced freeze"),
    ):
        if (
            row["signature_algorithm"] != "ed25519"
            or row["signing_key_fingerprint"] != writer_freeze_policy.key_fingerprint
        ):
            raise WriterFreezeEvidenceError(f"{label} signing policy mismatch")
        signature = row[signature_field]
        if not isinstance(signature, str) or len(signature) != 128:
            raise WriterFreezeEvidenceError(f"{label} signature is invalid")
        signed_row = {
            key: value for key, value in row.items() if key != signature_field
        }
        try:
            writer_freeze_policy.public_key.verify(
                bytes.fromhex(signature), _signing_payload(domain, signed_row)
            )
        except (InvalidSignature, ValueError) as error:
            raise WriterFreezeEvidenceError(f"{label} signature failed") from error
    if evidence["evidence_strength"] != "observed_stopped" or (
        evidence["integrity_result"],
        evidence["checkpoint_result"],
        evidence["journal_result"],
        evidence["legacy_context_cutoff_status"],
    ) != ("passed", "complete", "clean", "observed_cutoff"):
        raise WriterFreezeEvidenceError("writer evidence is insufficient")
    if (
        freeze["contract_version"] != "enforced-legacy-identity-writer-freeze-v1"
        or freeze["enforcement_status"] != "enforced_offline"
        or freeze["write_probe_result"] != "blocked"
        or freeze["semantic_write_status"] != "frozen"
        or freeze["legacy_context_cutoff_status"] != "enforced_cutoff"
        or freeze["recognition_mode"] != "nonauthoritative_only"
        or freeze["e3_commitment_key_epoch"] != identity_policy.commitment_key_epoch
        or freeze["policy_digest"] != identity_policy.policy_digest
    ):
        raise WriterFreezeEvidenceError("enforced writer freeze is insufficient")
    observed = _time(evidence["observed_at"], "writer evidence")
    enforced = _time(freeze["enforced_at"], "enforced freeze")
    verified = _time(freeze["verified_at"], "freeze verification")
    if observed > enforced or enforced > verified:
        raise WriterFreezeEvidenceError("writer-freeze timeline is invalid")
    return packet
