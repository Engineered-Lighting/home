#!/usr/bin/env python3
"""Create the private, separately signed Phase 3 semantic-cutover packet."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

import phase3_identity_signing_ceremony as identity_ceremony
import phase3_privacy_cutover_ceremony as privacy_ceremony
from phase3_semantic_cutover_packet import (
    SemanticCutoverPacketError,
    SemanticCutoverPolicy,
    compile_semantic_cutover_packet,
    verify_semantic_cutover_packet,
)
import phase3_writer_freeze_ceremony as writer_ceremony
from reviewed_identity_payload import (
    VerificationError,
    canonical_bytes,
    parse_canonical_json,
)


CONTRACT = "phase3-semantic-cutover-ceremony-e5ab-v1"
SEMANTIC_POLICY_CONTRACT = "phase3-semantic-cutover-signing-policy-e5ab-v1"
UNIT_NAME = "home-agent-identity-semantic-cutover-packet.service"
CREDENTIAL_NAMES = frozenset(
    {
        "policy.json",
        "commitment.key",
        "writer-freeze-policy.json",
        "privacy-probe-policy.json",
        "semantic-cutover-policy.json",
        "semantic-cutover.key",
    }
)
PRIVATE_ROOT = identity_ceremony.PRIVATE_ROOT
WRITER_EVIDENCE_PATH = writer_ceremony.EVIDENCE_PATH
PRIVACY_EVIDENCE_PATH = privacy_ceremony.EVIDENCE_PATH
ERASURE_RECEIPT_PATH = Path("/srv/home-agent/config/phase3-erasure-current-e5j.json")
PACKET_PATH = PRIVATE_ROOT / "semantic-cutover-packet-e5ab.json"
RECEIPT_PATH = PRIVATE_ROOT / "semantic-cutover-packet-receipt-e5ab.json"
MAX_BYTES = 4 * 1024 * 1024


class SemanticCutoverCeremonyError(RuntimeError):
    """A content-free semantic-cutover ceremony failure."""


def _read(path: Path, maximum: int = MAX_BYTES) -> bytes:
    try:
        return identity_ceremony._read_root_file(
            path, maximum=maximum, modes=frozenset({0o400, 0o440, 0o600})
        )
    except identity_ceremony.SigningCeremonyError as error:
        raise SemanticCutoverCeremonyError(
            "semantic-cutover ceremony input is unavailable"
        ) from error


def _canonical_json(raw: bytes) -> Mapping[str, Any]:
    content = raw[:-1] if raw.endswith(b"\n") else raw
    try:
        value = parse_canonical_json(content, maximum=MAX_BYTES)
    except VerificationError as error:
        raise SemanticCutoverCeremonyError(
            "semantic-cutover ceremony JSON is invalid"
        ) from error
    if not isinstance(value, Mapping):
        raise SemanticCutoverCeremonyError("semantic-cutover ceremony JSON is invalid")
    return value


def _credential_directory() -> Path:
    path = Path(os.environ.get("CREDENTIALS_DIRECTORY", ""))
    if path != Path("/run/credentials") / UNIT_NAME:
        raise SemanticCutoverCeremonyError(
            "semantic-cutover credential directory is invalid"
        )
    try:
        names = frozenset(item.name for item in path.iterdir())
    except OSError as error:
        raise SemanticCutoverCeremonyError(
            "semantic-cutover credentials are unavailable"
        ) from error
    if names != CREDENTIAL_NAMES:
        raise SemanticCutoverCeremonyError(
            "semantic-cutover credential separation failed"
        )
    return path


def _semantic_policy(directory: Path) -> SemanticCutoverPolicy:
    value = _canonical_json(_read(directory / "semantic-cutover-policy.json", 16384))
    if (
        set(value) != {"contract", "public_key_hex", "key_fingerprint"}
        or value["contract"] != SEMANTIC_POLICY_CONTRACT
    ):
        raise SemanticCutoverCeremonyError("semantic-cutover signing policy is invalid")
    try:
        raw = bytes.fromhex(value["public_key_hex"])
        if len(raw) != 32:
            raise ValueError("invalid public key")
        return SemanticCutoverPolicy(
            public_key=Ed25519PublicKey.from_public_bytes(raw),
            key_fingerprint=value["key_fingerprint"],
        )
    except (TypeError, ValueError) as error:
        raise SemanticCutoverCeremonyError(
            "semantic-cutover signing policy is invalid"
        ) from error


def _private_key(directory: Path) -> Ed25519PrivateKey:
    raw = _read(directory / "semantic-cutover.key", 32)
    if len(raw) != 32:
        raise SemanticCutoverCeremonyError("semantic-cutover signing key is invalid")
    try:
        return Ed25519PrivateKey.from_private_bytes(raw)
    except ValueError as error:
        raise SemanticCutoverCeremonyError(
            "semantic-cutover signing key is invalid"
        ) from error


def _receipt(raw: bytes, packet: Mapping[str, Any]) -> Mapping[str, Any]:
    document = packet["cutover_document"]
    candidate = packet["semantic_authority_candidate"]
    return {
        "contract": "phase3-semantic-cutover-packet-receipt-e5ab-v1",
        "status": "verified_unadmitted",
        "run_id": document["run_id"],
        "admission_id": document["admission_id"],
        "promotion_id": document["promotion_id"],
        "candidate_cutover_id": candidate["cutover_id"],
        "writer_freeze_id": document["writer_freeze_id"],
        "privacy_check_set_commitment": document["privacy_check_set_commitment"],
        "cutover_document_sha256": packet["cutover_document_sha256"],
        "cutover_packet_sha256": hashlib.sha256(raw).hexdigest(),
    }


def execute() -> Mapping[str, Any]:
    identity_ceremony._require_root_linux()
    identity_ceremony._safe_private_root()
    directory = _credential_directory()
    identity_policy = identity_ceremony._load_policy(directory)
    writer_policy = writer_ceremony._writer_policy(directory)
    privacy_policy = privacy_ceremony._privacy_policy(directory)
    semantic_policy = _semantic_policy(directory)
    descriptor = identity_ceremony._acquire_lock()
    try:
        try:
            existing = _read(PACKET_PATH)
        except SemanticCutoverCeremonyError:
            if PACKET_PATH.exists():
                raise
        else:
            raw = existing[:-1] if existing.endswith(b"\n") else existing
            try:
                verified = verify_semantic_cutover_packet(
                    raw,
                    identity_policy=identity_policy,
                    writer_freeze_policy=writer_policy,
                    privacy_probe_policy=privacy_policy,
                    semantic_cutover_policy=semantic_policy,
                )
            except SemanticCutoverPacketError as error:
                raise SemanticCutoverCeremonyError(
                    "existing semantic-cutover packet is invalid"
                ) from error
            receipt = _receipt(raw, verified)
            identity_ceremony._atomic_exact(
                RECEIPT_PATH, canonical_bytes(receipt) + b"\n", replace=False
            )
            return {**receipt, "ceremony_status": "resumed"}

        writer_evidence = _read(WRITER_EVIDENCE_PATH)
        privacy_evidence = _read(PRIVACY_EVIDENCE_PATH)
        erasure_receipt = _canonical_json(_read(ERASURE_RECEIPT_PATH, 16384))
        if writer_evidence.endswith(b"\n"):
            writer_evidence = writer_evidence[:-1]
        if privacy_evidence.endswith(b"\n"):
            privacy_evidence = privacy_evidence[:-1]
        try:
            raw = compile_semantic_cutover_packet(
                writer_evidence,
                privacy_evidence,
                erasure_receipt,
                identity_policy=identity_policy,
                writer_freeze_policy=writer_policy,
                privacy_probe_policy=privacy_policy,
                semantic_cutover_policy=semantic_policy,
                semantic_cutover_private_key=_private_key(directory),
                now=datetime.now(UTC),
            )
            verified = verify_semantic_cutover_packet(
                raw,
                identity_policy=identity_policy,
                writer_freeze_policy=writer_policy,
                privacy_probe_policy=privacy_policy,
                semantic_cutover_policy=semantic_policy,
            )
        except SemanticCutoverPacketError as error:
            raise SemanticCutoverCeremonyError(
                "semantic-cutover packet compilation failed"
            ) from error
        receipt = _receipt(raw, verified)
        identity_ceremony._atomic_exact(PACKET_PATH, raw + b"\n", replace=False)
        identity_ceremony._atomic_exact(
            RECEIPT_PATH, canonical_bytes(receipt) + b"\n", replace=False
        )
        return {**receipt, "ceremony_status": "completed"}
    finally:
        os.close(descriptor)


def main() -> int:
    if len(sys.argv) != 1:
        print("semantic-cutover ceremony accepts no arguments", file=sys.stderr)
        return 64
    try:
        result = execute()
    except (
        SemanticCutoverCeremonyError,
        SemanticCutoverPacketError,
        identity_ceremony.SigningCeremonyError,
    ):
        print("semantic-cutover ceremony failed closed", file=sys.stderr)
        return 78
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
