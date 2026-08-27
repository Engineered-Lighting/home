#!/usr/bin/env python3
"""Sign one physically observed legacy-writer freeze into private evidence."""

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
from phase3_writer_freeze_evidence import (
    WriterFreezeEvidenceError,
    WriterFreezePolicy,
    compile_writer_freeze_evidence,
    verify_writer_freeze_evidence,
)
from reviewed_identity_payload import (
    VerificationError,
    canonical_bytes,
    parse_canonical_json,
)


CONTRACT = "phase3-writer-freeze-signing-ceremony-e5z-v1"
WRITER_POLICY_CONTRACT = "phase3-writer-freeze-signing-policy-e5z-v1"
UNIT_NAME = "home-agent-identity-writer-freeze.service"
CREDENTIAL_NAMES = frozenset(
    {
        "policy.json",
        "commitment.key",
        "writer-freeze-policy.json",
        "writer-freeze.key",
    }
)
PRIVATE_ROOT = identity_ceremony.PRIVATE_ROOT
REVIEW_PATH = identity_ceremony.REVIEW_PATH
STATE_PATH = identity_ceremony.STATE_PATH
OBSERVATION_PATH = PRIVATE_ROOT / "writer-freeze-observation-e5z.json"
EVIDENCE_PATH = PRIVATE_ROOT / "writer-freeze-evidence-e5z.json"
RECEIPT_PATH = PRIVATE_ROOT / "writer-freeze-evidence-receipt-e5z.json"
MAX_BYTES = 4 * 1024 * 1024


class WriterFreezeCeremonyError(RuntimeError):
    """A content-free writer-freeze signing failure."""


def _read(path: Path, maximum: int = MAX_BYTES) -> bytes:
    try:
        return identity_ceremony._read_root_file(
            path, maximum=maximum, modes=frozenset({0o400, 0o440, 0o600})
        )
    except identity_ceremony.SigningCeremonyError as error:
        raise WriterFreezeCeremonyError(
            "writer-freeze ceremony input is unavailable"
        ) from error


def _json(raw: bytes, *, canonical: bool) -> Mapping[str, Any]:
    content = raw[:-1] if raw.endswith(b"\n") else raw
    if canonical:
        try:
            value = parse_canonical_json(content, maximum=MAX_BYTES)
        except VerificationError as error:
            raise WriterFreezeCeremonyError(
                "writer-freeze ceremony JSON is invalid"
            ) from error
    else:

        def reject_duplicates(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate key")
                result[key] = value
            return result

        try:
            value = json.loads(content, object_pairs_hook=reject_duplicates)
        except (ValueError, UnicodeError) as error:
            raise WriterFreezeCeremonyError(
                "writer-freeze ceremony JSON is invalid"
            ) from error
    if not isinstance(value, Mapping):
        raise WriterFreezeCeremonyError("writer-freeze ceremony JSON is invalid")
    return value


def _credential_directory() -> Path:
    path = Path(os.environ.get("CREDENTIALS_DIRECTORY", ""))
    if path != Path("/run/credentials") / UNIT_NAME:
        raise WriterFreezeCeremonyError("writer-freeze credential directory is invalid")
    try:
        names = frozenset(item.name for item in path.iterdir())
    except OSError as error:
        raise WriterFreezeCeremonyError(
            "writer-freeze credentials are unavailable"
        ) from error
    if names != CREDENTIAL_NAMES:
        raise WriterFreezeCeremonyError("writer-freeze credential separation failed")
    return path


def _writer_policy(directory: Path) -> WriterFreezePolicy:
    value = _json(
        _read(directory / "writer-freeze-policy.json", 16 * 1024), canonical=True
    )
    if (
        set(value) != {"contract", "public_key_hex", "key_fingerprint"}
        or value["contract"] != WRITER_POLICY_CONTRACT
    ):
        raise WriterFreezeCeremonyError("writer-freeze signing policy is invalid")
    try:
        raw = bytes.fromhex(value["public_key_hex"])
        if len(raw) != 32:
            raise ValueError("invalid public key")
        return WriterFreezePolicy(
            public_key=Ed25519PublicKey.from_public_bytes(raw),
            key_fingerprint=value["key_fingerprint"],
        )
    except (TypeError, ValueError) as error:
        raise WriterFreezeCeremonyError(
            "writer-freeze signing policy is invalid"
        ) from error


def _private_key(directory: Path) -> Ed25519PrivateKey:
    raw = _read(directory / "writer-freeze.key", 32)
    if len(raw) != 32:
        raise WriterFreezeCeremonyError("writer-freeze signing key is invalid")
    try:
        return Ed25519PrivateKey.from_private_bytes(raw)
    except ValueError as error:
        raise WriterFreezeCeremonyError(
            "writer-freeze signing key is invalid"
        ) from error


def _receipt(raw: bytes, value: Mapping[str, Any]) -> Mapping[str, Any]:
    evidence = value["writer_evidence"]
    freeze = value["enforced_writer_freeze"]
    return {
        "contract": "phase3-writer-freeze-evidence-receipt-e5z-v1",
        "status": "verified",
        "run_id": value["run_id"],
        "evidence_id": evidence["evidence_id"],
        "freeze_id": freeze["freeze_id"],
        "evidence_commitment": evidence["evidence_commitment"],
        "freeze_commitment": freeze["freeze_commitment"],
        "evidence_packet_sha256": hashlib.sha256(raw).hexdigest(),
    }


def _expected_source_plan(private_review: Mapping[str, Any]) -> str:
    """Derive the projection the evidence must match from the reviewed snapshot.

    The private review's own ``source_plan_sha256`` is a review-time
    self-report that no signing verb re-derives, so a loader change between
    review and freeze silently invalidates it. Binding the review to the
    snapshot and then projecting that snapshot keeps the chain
    review -> snapshot -> plan -> physical observation closed against bytes
    every party can recompute.
    """

    body = private_review.get("private_review")
    reviewed_snapshot = (
        body.get("sqlite_snapshot_sha256") if isinstance(body, Mapping) else None
    )
    if reviewed_snapshot != identity_ceremony._snapshot_sha256():
        raise WriterFreezeCeremonyError("private People review snapshot drifted")
    try:
        return identity_ceremony.load_plan(identity_ceremony.SOURCE_PATH).digest
    except identity_ceremony.MigrationError as error:
        raise WriterFreezeCeremonyError(
            "private identity snapshot could not be projected"
        ) from error


def execute() -> Mapping[str, Any]:
    identity_ceremony._require_root_linux()
    identity_ceremony._safe_private_root()
    directory = _credential_directory()
    policy = identity_ceremony._load_policy(directory)
    writer_policy = _writer_policy(directory)
    descriptor = identity_ceremony._acquire_lock()
    try:
        try:
            existing = _read(EVIDENCE_PATH)
        except WriterFreezeCeremonyError:
            if EVIDENCE_PATH.exists():
                raise
        else:
            raw = existing[:-1] if existing.endswith(b"\n") else existing
            try:
                verified = verify_writer_freeze_evidence(
                    raw,
                    identity_policy=policy,
                    writer_freeze_policy=writer_policy,
                )
            except WriterFreezeEvidenceError as error:
                raise WriterFreezeCeremonyError(
                    "existing writer-freeze evidence is invalid"
                ) from error
            receipt = _receipt(raw, verified)
            identity_ceremony._atomic_exact(
                RECEIPT_PATH, canonical_bytes(receipt) + b"\n", replace=False
            )
            return {**receipt, "ceremony_status": "resumed"}

        state = identity_ceremony._load_state(policy)
        if state["phase"] != "finalized":
            raise WriterFreezeCeremonyError("identity signing is not finalized")
        identity_ceremony.verify_finalized_state(state, policy)
        packet = identity_ceremony._packet_from_state(state, policy)
        private_review = _json(_read(REVIEW_PATH), canonical=False)
        observation_raw = _read(OBSERVATION_PATH)
        observation = (
            observation_raw[:-1] if observation_raw.endswith(b"\n") else observation_raw
        )
        expected_plan_digest = _expected_source_plan(private_review)
        try:
            raw = compile_writer_freeze_evidence(
                packet,
                state["review_signature"],
                canonical_bytes(state["finalization_envelope"]),
                private_review,
                observation,
                expected_source_plan_sha256=expected_plan_digest,
                identity_policy=policy,
                writer_freeze_policy=writer_policy,
                writer_freeze_private_key=_private_key(directory),
            )
        except WriterFreezeEvidenceError as error:
            raise WriterFreezeCeremonyError(
                "writer-freeze evidence compilation failed"
            ) from error
        verified = verify_writer_freeze_evidence(
            raw,
            identity_policy=policy,
            writer_freeze_policy=writer_policy,
        )
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
        print("writer-freeze ceremony accepts no arguments", file=sys.stderr)
        return 64
    try:
        result = execute()
    except (
        WriterFreezeCeremonyError,
        identity_ceremony.SigningCeremonyError,
        WriterFreezeEvidenceError,
    ):
        print("writer-freeze ceremony failed closed", file=sys.stderr)
        return 78
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
