#!/usr/bin/env python3
"""Compile the six mandatory privacy-cutover receipts offline.

The input is a canonical, content-free observation from deterministic live
probes. Exactly three fixed assertions must pass for each required category.
A fourth purpose-pinned Ed25519 key attests the complete observation, and keyed
commitments bind every resulting database receipt plus the six-receipt set.

No names, person identifiers, source entity IDs, coordinates, utterances,
tokens, URLs, or evidence content are accepted. The packet is non-authoritative
and this module has no filesystem, network, database, or model client.
"""

from __future__ import annotations

from dataclasses import dataclass
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


CONTRACT = "phase3-privacy-cutover-evidence-e5aa-v1"
OBSERVATION_CONTRACT = "phase3-privacy-cutover-observation-e5aa-v1"
ATTESTATION_DOMAIN = "identity-privacy-cutover-observation-v1"
RECEIPT_DOMAIN = "identity-privacy-cutover-receipt-v1"
SET_DOMAIN = "identity-privacy-cutover-receipt-set-v1"
MAX_BYTES = 1024 * 1024
CATEGORY_ASSERTIONS = {
    "ingress": (
        "edge_policy_current",
        "blocked_source_rows_absent",
        "raw_identity_ingest_absent",
    ),
    "retrieval": (
        "subject_blocks_enforced",
        "private_scope_idor_denied",
        "legacy_retrieval_absent",
    ),
    "prompt": (
        "silent_private_prompt_filters_enforced",
        "legacy_context_absent",
        "external_sensitive_routing_disabled",
    ),
    "initiative": (
        "location_initiatives_default_off",
        "private_surface_only",
        "privacy_suppression_current",
    ),
    "export": (
        "raw_gps_offhost_absent",
        "semantic_exports_principal_scoped",
        "legacy_export_route_gone",
    ),
    "edge_block": (
        "do_not_track_pre_ingress",
        "blocked_spool_payloads_purged",
        "privacy_policy_fail_closed",
    ),
}
CATEGORY_ORDER = tuple(CATEGORY_ASSERTIONS)
OBSERVATION_KEYS = frozenset(
    {
        "contract",
        "run_id",
        "finalization_id",
        "freeze_id",
        "policy_digest",
        "release_manifest_digest",
        "probe_bundle_digest",
        "checked_at",
        "categories",
    }
)
CATEGORY_KEYS = frozenset(
    {"check_category", "check_result", "residual_code", "assertions", "probe_digest"}
)
PACKET_KEYS = frozenset(
    {
        "contract",
        "authoritative",
        "run_id",
        "finalization_id",
        "freeze_id",
        "privacy_observation",
        "privacy_observation_sha256",
        "attestation_algorithm",
        "attestation_key_fingerprint",
        "privacy_observation_signature",
        "receipts",
        "privacy_check_set_commitment",
    }
)
RECEIPT_KEYS = frozenset(
    {
        "check_id",
        "run_id",
        "finalization_id",
        "check_category",
        "check_result",
        "residual_code",
        "check_commitment",
        "receipt_commitment",
        "policy_digest",
        "checked_at",
    }
)


class PrivacyCutoverEvidenceError(RuntimeError):
    """A content-free privacy-cutover evidence failure."""


@dataclass(frozen=True, slots=True)
class PrivacyProbePolicy:
    public_key: Ed25519PublicKey
    key_fingerprint: str
    key_purpose: str = "identity_privacy_cutover_probe"
    key_revoked: bool = False

    def __post_init__(self) -> None:
        if self.key_purpose != "identity_privacy_cutover_probe":
            raise ValueError("privacy probe public key has the wrong purpose")
        if self.key_revoked:
            raise ValueError("privacy probe public key is revoked")
        raw = self.public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        if not hmac.compare_digest(
            hashlib.sha256(raw).hexdigest(), self.key_fingerprint
        ):
            raise ValueError("privacy probe public key fingerprint mismatch")


def _exact(value: Any, keys: frozenset[str], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise PrivacyCutoverEvidenceError(f"{label} shape is invalid")
    return dict(value)


def _hex64(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise PrivacyCutoverEvidenceError(f"{label} digest is invalid")
    return value


def _uuid(value: Any, label: str) -> str:
    try:
        parsed = uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError) as error:
        raise PrivacyCutoverEvidenceError(f"{label} identifier is invalid") from error
    if str(parsed) != value or parsed.version != 7:
        raise PrivacyCutoverEvidenceError(f"{label} identifier is invalid")
    return str(parsed)


def _time(value: Any, label: str):
    from datetime import UTC, datetime

    if not isinstance(value, str) or not value.endswith("Z"):
        raise PrivacyCutoverEvidenceError(f"{label} timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise PrivacyCutoverEvidenceError(f"{label} timestamp is invalid") from error
    if (
        parsed.tzinfo is None
        or parsed.astimezone(UTC) != parsed
        or parsed.strftime("%Y-%m-%dT%H:%M:%S.%fZ") != value
    ):
        raise PrivacyCutoverEvidenceError(f"{label} timestamp is noncanonical")
    return parsed


def _parse_observation(raw: bytes) -> dict[str, Any]:
    if type(raw) is not bytes or not raw or len(raw) > MAX_BYTES:
        raise PrivacyCutoverEvidenceError("privacy observation is invalid")
    try:
        value = parse_canonical_json(raw, maximum=MAX_BYTES)
    except VerificationError as error:
        raise PrivacyCutoverEvidenceError("privacy observation is invalid") from error
    observation = _exact(value, OBSERVATION_KEYS, "privacy observation")
    if observation["contract"] != OBSERVATION_CONTRACT:
        raise PrivacyCutoverEvidenceError("privacy observation is unsupported")
    _uuid(observation["run_id"], "privacy run")
    _uuid(observation["finalization_id"], "privacy finalization")
    _uuid(observation["freeze_id"], "writer freeze")
    for key in ("policy_digest", "release_manifest_digest", "probe_bundle_digest"):
        _hex64(observation[key], key)
    _time(observation["checked_at"], "privacy check")
    categories = observation["categories"]
    if not isinstance(categories, list) or len(categories) != len(CATEGORY_ORDER):
        raise PrivacyCutoverEvidenceError("privacy observation category set is invalid")
    for expected_category, raw_category in zip(CATEGORY_ORDER, categories, strict=True):
        category = _exact(raw_category, CATEGORY_KEYS, "privacy category")
        if (
            category["check_category"] != expected_category
            or category["check_result"] != "passed"
            or category["residual_code"] != "none"
        ):
            raise PrivacyCutoverEvidenceError("privacy category did not pass")
        assertions = category["assertions"]
        expected_assertions = CATEGORY_ASSERTIONS[expected_category]
        if (
            not isinstance(assertions, Mapping)
            or set(assertions) != set(expected_assertions)
            or any(assertions[name] is not True for name in expected_assertions)
        ):
            raise PrivacyCutoverEvidenceError("privacy assertions are incomplete")
        _hex64(category["probe_digest"], "privacy probe")
    return observation


def _raw_public(key: Ed25519PublicKey) -> bytes:
    return key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def _require_key(
    private_key: Ed25519PrivateKey,
    probe_policy: PrivacyProbePolicy,
    identity_policy: VerificationPolicy,
    writer_policy: WriterFreezePolicy,
) -> None:
    if not isinstance(private_key, Ed25519PrivateKey) or not hmac.compare_digest(
        _raw_public(private_key.public_key()), _raw_public(probe_policy.public_key)
    ):
        raise PrivacyCutoverEvidenceError("privacy probe private key does not match")
    probe_raw = _raw_public(probe_policy.public_key)
    other_keys = (
        identity_policy.review_public_key,
        identity_policy.finalization_public_key,
        writer_policy.public_key,
    )
    if any(hmac.compare_digest(probe_raw, _raw_public(key)) for key in other_keys):
        raise PrivacyCutoverEvidenceError(
            "privacy probe signing purpose is not distinct"
        )


def _id(factory: Callable[[], uuid.UUID], label: str) -> str:
    value = factory()
    if not isinstance(value, uuid.UUID) or value.version != 7:
        raise PrivacyCutoverEvidenceError(f"{label} generator did not produce UUIDv7")
    return str(value)


def compile_privacy_cutover_evidence(
    raw_observation: bytes,
    raw_writer_freeze_evidence: bytes,
    *,
    identity_policy: VerificationPolicy,
    writer_freeze_policy: WriterFreezePolicy,
    privacy_probe_policy: PrivacyProbePolicy,
    privacy_probe_private_key: Ed25519PrivateKey,
    id_factory: Callable[[], uuid.UUID] = uuid7,
) -> bytes:
    """Compile six committed receipts from a complete live-probe observation."""

    _require_key(
        privacy_probe_private_key,
        privacy_probe_policy,
        identity_policy,
        writer_freeze_policy,
    )
    observation = _parse_observation(raw_observation)
    try:
        freeze_packet = verify_writer_freeze_evidence(
            raw_writer_freeze_evidence,
            identity_policy=identity_policy,
            writer_freeze_policy=writer_freeze_policy,
        )
    except WriterFreezeEvidenceError as error:
        raise PrivacyCutoverEvidenceError(
            "writer-freeze evidence is invalid"
        ) from error
    freeze = freeze_packet["enforced_writer_freeze"]
    if (
        observation["run_id"] != freeze_packet["run_id"]
        or observation["finalization_id"] != freeze_packet["finalization_id"]
        or observation["freeze_id"] != freeze["freeze_id"]
        or observation["policy_digest"] != identity_policy.policy_digest
        or observation["release_manifest_digest"] != freeze["release_manifest_digest"]
        or _time(observation["checked_at"], "privacy check")
        < _time(freeze["verified_at"], "writer freeze verification")
    ):
        raise PrivacyCutoverEvidenceError("privacy observation lineage is invalid")
    observation_sha = hashlib.sha256(raw_observation).hexdigest()
    observation_signature = privacy_probe_private_key.sign(
        canonical_bytes(
            {
                "domain": ATTESTATION_DOMAIN,
                "privacy_observation_sha256": observation_sha,
                "observation": observation,
            }
        )
    ).hex()
    receipts = []
    for category in observation["categories"]:
        receipt = {
            "check_id": _id(id_factory, "privacy check"),
            "run_id": observation["run_id"],
            "finalization_id": observation["finalization_id"],
            "check_category": category["check_category"],
            "check_result": "passed",
            "residual_code": "none",
            "check_commitment": keyed_commitment(
                identity_policy.commitment_key,
                "identity-privacy-cutover-check-v1",
                {
                    "run_id": observation["run_id"],
                    "finalization_id": observation["finalization_id"],
                    "freeze_id": observation["freeze_id"],
                    "checked_at": observation["checked_at"],
                    "category": category,
                    "privacy_observation_sha256": observation_sha,
                },
            ),
            "receipt_commitment": "",
            "policy_digest": observation["policy_digest"],
            "checked_at": observation["checked_at"],
        }
        receipt["receipt_commitment"] = keyed_commitment(
            identity_policy.commitment_key,
            RECEIPT_DOMAIN,
            {
                key: value
                for key, value in receipt.items()
                if key != "receipt_commitment"
            },
        )
        receipts.append(receipt)
    result = {
        "contract": CONTRACT,
        "authoritative": False,
        "run_id": observation["run_id"],
        "finalization_id": observation["finalization_id"],
        "freeze_id": observation["freeze_id"],
        "privacy_observation": observation,
        "privacy_observation_sha256": observation_sha,
        "attestation_algorithm": "ed25519",
        "attestation_key_fingerprint": privacy_probe_policy.key_fingerprint,
        "privacy_observation_signature": observation_signature,
        "receipts": receipts,
        "privacy_check_set_commitment": keyed_commitment(
            identity_policy.commitment_key, SET_DOMAIN, receipts
        ),
    }
    raw_result = canonical_bytes(result)
    verify_privacy_cutover_evidence(
        raw_result,
        identity_policy=identity_policy,
        privacy_probe_policy=privacy_probe_policy,
    )
    return raw_result


def verify_privacy_cutover_evidence(
    raw: bytes,
    *,
    identity_policy: VerificationPolicy,
    privacy_probe_policy: PrivacyProbePolicy,
) -> Mapping[str, Any]:
    if type(raw) is not bytes or not raw or len(raw) > MAX_BYTES:
        raise PrivacyCutoverEvidenceError("privacy evidence packet is invalid")
    try:
        value = parse_canonical_json(raw, maximum=MAX_BYTES)
    except VerificationError as error:
        raise PrivacyCutoverEvidenceError(
            "privacy evidence packet is invalid"
        ) from error
    packet = _exact(value, PACKET_KEYS, "privacy evidence packet")
    if packet["contract"] != CONTRACT or packet["authoritative"] is not False:
        raise PrivacyCutoverEvidenceError("privacy evidence contract is invalid")
    observation_raw = canonical_bytes(packet["privacy_observation"])
    observation = _parse_observation(observation_raw)
    observation_sha = hashlib.sha256(observation_raw).hexdigest()
    if (
        packet["run_id"] != observation["run_id"]
        or packet["finalization_id"] != observation["finalization_id"]
        or packet["freeze_id"] != observation["freeze_id"]
        or packet["privacy_observation_sha256"] != observation_sha
        or packet["attestation_algorithm"] != "ed25519"
        or packet["attestation_key_fingerprint"] != privacy_probe_policy.key_fingerprint
    ):
        raise PrivacyCutoverEvidenceError("privacy observation attestation drifted")
    signature = packet["privacy_observation_signature"]
    if not isinstance(signature, str) or len(signature) != 128:
        raise PrivacyCutoverEvidenceError("privacy observation signature is invalid")
    try:
        privacy_probe_policy.public_key.verify(
            bytes.fromhex(signature),
            canonical_bytes(
                {
                    "domain": ATTESTATION_DOMAIN,
                    "privacy_observation_sha256": observation_sha,
                    "observation": observation,
                }
            ),
        )
    except (InvalidSignature, ValueError) as error:
        raise PrivacyCutoverEvidenceError(
            "privacy observation signature failed"
        ) from error
    receipts = packet["receipts"]
    if not isinstance(receipts, list) or len(receipts) != len(CATEGORY_ORDER):
        raise PrivacyCutoverEvidenceError("privacy receipt set is incomplete")
    seen_ids: set[str] = set()
    for category, raw_receipt, category_observation in zip(
        CATEGORY_ORDER, receipts, observation["categories"], strict=True
    ):
        receipt = _exact(raw_receipt, RECEIPT_KEYS, "privacy receipt")
        check_id = _uuid(receipt["check_id"], "privacy check")
        if check_id in seen_ids:
            raise PrivacyCutoverEvidenceError("privacy check identifier is duplicated")
        seen_ids.add(check_id)
        if (
            receipt["run_id"] != observation["run_id"]
            or receipt["finalization_id"] != observation["finalization_id"]
            or receipt["check_category"] != category
            or receipt["check_result"] != "passed"
            or receipt["residual_code"] != "none"
            or receipt["policy_digest"] != identity_policy.policy_digest
            or receipt["checked_at"] != observation["checked_at"]
        ):
            raise PrivacyCutoverEvidenceError("privacy receipt lineage is invalid")
        expected_check = keyed_commitment(
            identity_policy.commitment_key,
            "identity-privacy-cutover-check-v1",
            {
                "run_id": observation["run_id"],
                "finalization_id": observation["finalization_id"],
                "freeze_id": observation["freeze_id"],
                "checked_at": observation["checked_at"],
                "category": category_observation,
                "privacy_observation_sha256": observation_sha,
            },
        )
        if not hmac.compare_digest(receipt["check_commitment"], expected_check):
            raise PrivacyCutoverEvidenceError("privacy check commitment mismatch")
        expected_receipt = keyed_commitment(
            identity_policy.commitment_key,
            RECEIPT_DOMAIN,
            {
                key: value
                for key, value in receipt.items()
                if key != "receipt_commitment"
            },
        )
        if not hmac.compare_digest(receipt["receipt_commitment"], expected_receipt):
            raise PrivacyCutoverEvidenceError("privacy receipt commitment mismatch")
    expected_set = keyed_commitment(
        identity_policy.commitment_key, SET_DOMAIN, receipts
    )
    if not hmac.compare_digest(packet["privacy_check_set_commitment"], expected_set):
        raise PrivacyCutoverEvidenceError("privacy receipt set commitment mismatch")
    return packet
