#!/usr/bin/env python3
"""Sign and persist exactly six live privacy-cutover receipts."""

from __future__ import annotations

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
from phase3_privacy_cutover_evidence import (
    PrivacyCutoverEvidenceError,
    PrivacyProbePolicy,
    compile_privacy_cutover_evidence,
    verify_privacy_cutover_evidence,
)
import phase3_writer_freeze_ceremony as writer_ceremony
from reviewed_identity_payload import (
    VerificationError,
    canonical_bytes,
    parse_canonical_json,
)


CONTRACT = "phase3-privacy-cutover-ceremony-e5aa-v1"
PRIVACY_POLICY_CONTRACT = "phase3-privacy-probe-signing-policy-e5aa-v1"
UNIT_NAME = "home-agent-identity-privacy-evidence.service"
CREDENTIAL_NAMES = frozenset(
    {
        "policy.json",
        "commitment.key",
        "writer-freeze-policy.json",
        "privacy-probe-policy.json",
        "privacy-probe.key",
    }
)
PRIVATE_ROOT = identity_ceremony.PRIVATE_ROOT
OBSERVATION_PATH = PRIVATE_ROOT / "privacy-cutover-observation-e5aa.json"
WRITER_EVIDENCE_PATH = writer_ceremony.EVIDENCE_PATH
EVIDENCE_PATH = PRIVATE_ROOT / "privacy-cutover-evidence-e5aa.json"
RECEIPT_PATH = PRIVATE_ROOT / "privacy-cutover-evidence-receipt-e5aa.json"
MAX_BYTES = 4 * 1024 * 1024


class PrivacyCutoverCeremonyError(RuntimeError):
    """A content-free privacy-cutover signing failure."""


def _read(path: Path, maximum: int = MAX_BYTES) -> bytes:
    try:
        return identity_ceremony._read_root_file(
            path, maximum=maximum, modes=frozenset({0o400, 0o440, 0o600})
        )
    except identity_ceremony.SigningCeremonyError as error:
        raise PrivacyCutoverCeremonyError(
            "privacy-cutover ceremony input is unavailable"
        ) from error


def _canonical_json(raw: bytes) -> Mapping[str, Any]:
    content = raw[:-1] if raw.endswith(b"\n") else raw
    try:
        value = parse_canonical_json(content, maximum=MAX_BYTES)
    except VerificationError as error:
        raise PrivacyCutoverCeremonyError(
            "privacy-cutover ceremony JSON is invalid"
        ) from error
    if not isinstance(value, Mapping):
        raise PrivacyCutoverCeremonyError("privacy-cutover ceremony JSON is invalid")
    return value


def _credential_directory() -> Path:
    path = Path(os.environ.get("CREDENTIALS_DIRECTORY", ""))
    if path != Path("/run/credentials") / UNIT_NAME:
        raise PrivacyCutoverCeremonyError("privacy credential directory is invalid")
    try:
        names = frozenset(item.name for item in path.iterdir())
    except OSError as error:
        raise PrivacyCutoverCeremonyError(
            "privacy credentials are unavailable"
        ) from error
    if names != CREDENTIAL_NAMES:
        raise PrivacyCutoverCeremonyError("privacy credential separation failed")
    return path


def _privacy_policy(directory: Path) -> PrivacyProbePolicy:
    value = _canonical_json(_read(directory / "privacy-probe-policy.json", 16 * 1024))
    if (
        set(value) != {"contract", "public_key_hex", "key_fingerprint"}
        or value["contract"] != PRIVACY_POLICY_CONTRACT
    ):
        raise PrivacyCutoverCeremonyError("privacy signing policy is invalid")
    try:
        raw = bytes.fromhex(value["public_key_hex"])
        if len(raw) != 32:
            raise ValueError("invalid public key")
        return PrivacyProbePolicy(
            public_key=Ed25519PublicKey.from_public_bytes(raw),
            key_fingerprint=value["key_fingerprint"],
        )
    except (TypeError, ValueError) as error:
        raise PrivacyCutoverCeremonyError(
            "privacy signing policy is invalid"
        ) from error


def _private_key(directory: Path) -> Ed25519PrivateKey:
    raw = _read(directory / "privacy-probe.key", 32)
    if len(raw) != 32:
        raise PrivacyCutoverCeremonyError("privacy signing key is invalid")
    try:
        return Ed25519PrivateKey.from_private_bytes(raw)
    except ValueError as error:
        raise PrivacyCutoverCeremonyError("privacy signing key is invalid") from error


def _receipt(raw: bytes, packet: Mapping[str, Any]) -> Mapping[str, Any]:
    return {
        "contract": "phase3-privacy-cutover-evidence-receipt-e5aa-v1",
        "status": "verified",
        "run_id": packet["run_id"],
        "finalization_id": packet["finalization_id"],
        "freeze_id": packet["freeze_id"],
        "privacy_check_count": len(packet["receipts"]),
        "privacy_check_set_commitment": packet["privacy_check_set_commitment"],
        "evidence_packet_sha256": hashlib.sha256(raw).hexdigest(),
    }


def execute() -> Mapping[str, Any]:
    identity_ceremony._require_root_linux()
    identity_ceremony._safe_private_root()
    directory = _credential_directory()
    identity_policy = identity_ceremony._load_policy(directory)
    writer_policy = writer_ceremony._writer_policy(directory)
    privacy_policy = _privacy_policy(directory)
    descriptor = identity_ceremony._acquire_lock()
    try:
        try:
            existing = _read(EVIDENCE_PATH)
        except PrivacyCutoverCeremonyError:
            if EVIDENCE_PATH.exists():
                raise
        else:
            raw = existing[:-1] if existing.endswith(b"\n") else existing
            try:
                verified = verify_privacy_cutover_evidence(
                    raw,
                    identity_policy=identity_policy,
                    privacy_probe_policy=privacy_policy,
                )
            except PrivacyCutoverEvidenceError as error:
                raise PrivacyCutoverCeremonyError(
                    "existing privacy evidence is invalid"
                ) from error
            receipt = _receipt(raw, verified)
            identity_ceremony._atomic_exact(
                RECEIPT_PATH, canonical_bytes(receipt) + b"\n", replace=False
            )
            return {**receipt, "ceremony_status": "resumed"}

        observation = _read(OBSERVATION_PATH)
        if observation.endswith(b"\n"):
            observation = observation[:-1]
        writer_evidence = _read(WRITER_EVIDENCE_PATH)
        if writer_evidence.endswith(b"\n"):
            writer_evidence = writer_evidence[:-1]
        try:
            raw = compile_privacy_cutover_evidence(
                observation,
                writer_evidence,
                identity_policy=identity_policy,
                writer_freeze_policy=writer_policy,
                privacy_probe_policy=privacy_policy,
                privacy_probe_private_key=_private_key(directory),
            )
            verified = verify_privacy_cutover_evidence(
                raw,
                identity_policy=identity_policy,
                privacy_probe_policy=privacy_policy,
            )
        except PrivacyCutoverEvidenceError as error:
            raise PrivacyCutoverCeremonyError(
                "privacy evidence compilation failed"
            ) from error
        receipt = _receipt(raw, verified)
        identity_ceremony._atomic_exact(EVIDENCE_PATH, raw + b"\n", replace=False)
        identity_ceremony._atomic_exact(
            RECEIPT_PATH, canonical_bytes(receipt) + b"\n", replace=False
        )
        return {**receipt, "ceremony_status": "completed"}
    finally:
        os.close(descriptor)


def main() -> int:
    if len(sys.argv) != 1:
        print("privacy-cutover ceremony accepts no arguments", file=sys.stderr)
        return 64
    try:
        result = execute()
    except (
        PrivacyCutoverCeremonyError,
        PrivacyCutoverEvidenceError,
        identity_ceremony.SigningCeremonyError,
    ):
        print("privacy-cutover ceremony failed closed", file=sys.stderr)
        return 78
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
