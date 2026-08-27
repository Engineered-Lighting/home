#!/usr/bin/env python3
"""Run the distinct-purpose Phase 3 identity signing ceremony.

The ceremony has four fixed phases: ``stage``, ``review``, ``finalize``, and
``supersede-expired``.  It never accepts a content path or credential path.  A
root-only launcher gives each phase a systemd credential directory containing
the public policy and commitment key plus, for a signing phase, exactly one
purpose-specific Ed25519 private key.  The review key is unavailable to
finalization and vice versa.  ``supersede-expired`` holds no signing key at
all: it can only retire an expired, entirely unsigned staged packet by
archiving it next to an append-only, content-free supersession receipt so a
later ``stage`` can mint a fresh packet with a fresh review window.

Private, resumable state is atomically fsynced below
``/srv/home-agent/private/phase3-identity``.  The only public output is a
content-free status receipt.  This module has no network or database writer.
"""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import sys
from typing import Any, Mapping
import uuid

try:  # Imported only by the Linux-hosted filesystem entry point.
    import fcntl
except ImportError:  # pragma: no cover - pure state functions are tested on Windows.
    fcntl = None

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from migrate_legacy_identity import (
    MigrationError,
    load_plan,
    load_source_inventory,
)
from reviewed_identity_packet_compiler import (
    PacketCompilerError,
    UnsignedReviewedIdentityPacket,
    _utc_text,
    compile_unsigned_packet,
    restore_unsigned_packet,
    verify_assembled_packet,
)
from reviewed_identity_payload import (
    VerificationError,
    VerificationPolicy,
    _canonical_time,
    canonical_bytes,
    parse_canonical_json,
    verify_expired_finalization_replay,
    verify_finalization_envelope,
)


CONTRACT = "phase3-identity-signing-ceremony-e5y-v1"
STATE_CONTRACT = "phase3-identity-signing-state-e5y-v1"
POLICY_CONTRACT = "phase3-identity-signing-policy-e5y-v1"
RECEIPT_CONTRACT = "phase3-identity-signing-receipt-e5y-v1"
PRIVATE_ROOT = Path("/srv/home-agent/private/phase3-identity")
SOURCE_PATH = PRIVATE_ROOT / "identity.db"
REVIEW_PATH = PRIVATE_ROOT / "people-review-e5x.json"
STATE_PATH = PRIVATE_ROOT / "identity-signing-state-e5y.json"
DOCUMENT_PATH = PRIVATE_ROOT / "identity-finalizer-document-e5y.json"
RECEIPT_PATH = PRIVATE_ROOT / "identity-signing-receipt-e5y.json"
LOCK_PATH = PRIVATE_ROOT / ".identity-signing-e5y.lock"
EDGE_RECEIPT_PATH = PRIVATE_ROOT / "edge-privacy-policy-receipt-e5ac.json"
WRITER_OBSERVATION_PATH = PRIVATE_ROOT / "writer-freeze-observation-e5z.json"
PRIVACY_OBSERVATION_PATH = PRIVATE_ROOT / "privacy-cutover-observation-e5aa.json"
CUTOVER_PACKET_PATH = PRIVATE_ROOT / "semantic-cutover-packet-e5ab.json"
CUTOVER_RECEIPT_PATH = PRIVATE_ROOT / "semantic-cutover-packet-receipt-e5ab.json"
SUPERSESSION_CONTRACT = "phase3-identity-packet-supersession-receipt-e5aj-v1"
SUPERSESSION_REASON = "review_window_expired_unsigned"
SUPERSESSION_ARCHIVE_PREFIX = "identity-signing-state-e5y.superseded-"
SUPERSESSION_RECEIPT_PREFIX = "identity-signing-supersession-"
SUPERSESSION_RECEIPT_SUFFIX = "-e5aj.json"
REVIEW_WINDOW_MINIMUM_SECONDS = 60
ACTIVATION_PRIVATE_ROOT = Path("/srv/home-agent/private/phase3-activation")
RUNNER_JOURNAL_PATH = ACTIVATION_PRIVATE_ROOT / "runner-state-e5ad.json"
RUNNER_COMPLETION_PATH = ACTIVATION_PRIVATE_ROOT / "runner-completion-e5ad.json"
RUNNER_JOURNAL_CONTRACT = "phase3-authoritative-split-activation-runner-e5ad-v1"
RUNNER_COMPLETED_PREFIX = (
    "admit_source",
    "validate_pre_authorization_prerequisites",
    "authorize_shadow",
    "provision_signing_credentials",
)
RUNNER_AWAIT_STEP = "await_reviewed_people_packet"
RUNNER_PAUSE_CODES = frozenset(
    {"awaiting_private_people_review", "awaiting_private_people_packet", "none"}
)
RUNNER_LOCK_PATH = Path("/srv/home-agent/locks/phase3-runner.lock")
ENVIRONMENT_PATH = Path("/srv/home-agent/config/home-agent.env")
EXPECTED_DB_REVISION = "0006a_worker_lease_arbitration"
EXPECTED_ROLLOUT_MODE = "record_only"
CREDENTIAL_RECEIPT_PATH = Path(
    "/srv/home-agent/config/phase3-identity-signing-credentials-e5ae.json"
)
CREDENTIAL_RECEIPT_CONTRACT = "phase3-identity-credential-receipt-e5ae-v1"
CREDENTIAL_RECEIPT_COUNT = 10
MAX_PRIVATE_BYTES = 4 * 1024 * 1024
MAX_POLICY_BYTES = 32 * 1024
MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024 * 1024
PHASES = frozenset({"stage", "review", "finalize", "supersede-expired"})
UNIT_NAMES = {
    "stage": "home-agent-identity-packet-stage.service",
    "review": "home-agent-identity-packet-review.service",
    "finalize": "home-agent-identity-packet-finalize.service",
    "supersede-expired": "home-agent-identity-packet-supersede.service",
}
PHASE_CREDENTIALS = {
    "stage": frozenset({"policy.json", "commitment.key"}),
    "review": frozenset({"policy.json", "commitment.key", "review.key"}),
    "finalize": frozenset({"policy.json", "commitment.key", "finalization.key"}),
    "supersede-expired": frozenset({"policy.json", "commitment.key"}),
}
SUPERSESSION_ABSENT_PATHS = (
    DOCUMENT_PATH,
    RECEIPT_PATH,
    EDGE_RECEIPT_PATH,
    WRITER_OBSERVATION_PATH,
    PRIVACY_OBSERVATION_PATH,
    CUTOVER_PACKET_PATH,
    CUTOVER_RECEIPT_PATH,
    RUNNER_COMPLETION_PATH,
)
POLICY_KEYS = frozenset(
    {
        "contract",
        "review_public_key_hex",
        "review_key_fingerprint",
        "finalization_public_key_hex",
        "finalization_key_fingerprint",
        "commitment_key_epoch",
        "commitment_key_fingerprint",
        "policy_version",
        "policy_digest",
        "source_projection_contract_digest",
        "release_manifest_digest",
        "migration_tool_bundle_digest",
        "core_oci_manifest_digest",
        "core_schema_digest",
        "core_capability_digest",
        "shadow_authorization_id",
    }
)
STATE_KEYS = frozenset(
    {
        "contract",
        "ceremony_policy_sha256",
        "phase",
        "unsigned_packet",
        "review_signature",
        "review_approval_sha256",
        "finalization_envelope",
        "finalization_approval_sha256",
        "finalizer_document_sha256",
    }
)


class SigningCeremonyError(RuntimeError):
    """A content-free signing ceremony failure."""


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _hex(value: Any, length: int, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != length
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise SigningCeremonyError(f"{label} is invalid")
    return value


def _public_raw(public_key: Ed25519PublicKey) -> bytes:
    return public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def policy_manifest(policy: VerificationPolicy) -> Mapping[str, Any]:
    """Return the public, canonical policy identity bound into ceremony state."""

    return {
        "contract": POLICY_CONTRACT,
        "review_public_key_hex": _public_raw(policy.review_public_key).hex(),
        "review_key_fingerprint": policy.review_key_fingerprint,
        "finalization_public_key_hex": _public_raw(
            policy.finalization_public_key
        ).hex(),
        "finalization_key_fingerprint": policy.finalization_key_fingerprint,
        "commitment_key_epoch": policy.commitment_key_epoch,
        "commitment_key_fingerprint": policy.commitment_key_fingerprint,
        "policy_version": policy.policy_version,
        "policy_digest": policy.policy_digest,
        "source_projection_contract_digest": (policy.source_projection_contract_digest),
        "release_manifest_digest": policy.release_manifest_digest,
        "migration_tool_bundle_digest": policy.migration_tool_bundle_digest,
        "core_oci_manifest_digest": policy.core_oci_manifest_digest,
        "core_schema_digest": policy.core_schema_digest,
        "core_capability_digest": policy.core_capability_digest,
        "shadow_authorization_id": str(policy.shadow_authorization_id),
    }


def ceremony_policy_sha256(policy: VerificationPolicy) -> str:
    return _sha256(canonical_bytes(policy_manifest(policy)))


def _validate_state(value: Mapping[str, Any], policy: VerificationPolicy) -> str:
    if not isinstance(value, Mapping) or set(value) != STATE_KEYS:
        raise SigningCeremonyError("private signing state shape is invalid")
    if value["contract"] != STATE_CONTRACT:
        raise SigningCeremonyError("private signing state contract is invalid")
    if value["ceremony_policy_sha256"] != ceremony_policy_sha256(policy):
        raise SigningCeremonyError("private signing state policy drifted")
    phase = value["phase"]
    if phase not in {"staged", "review_signed", "finalized"}:
        raise SigningCeremonyError("private signing state phase is invalid")
    review_signature = value["review_signature"]
    review_approval = value["review_approval_sha256"]
    finalization_envelope = value["finalization_envelope"]
    finalization_approval = value["finalization_approval_sha256"]
    document_digest = value["finalizer_document_sha256"]
    if phase == "staged":
        if any(
            item is not None
            for item in (
                review_signature,
                review_approval,
                finalization_envelope,
                finalization_approval,
                document_digest,
            )
        ):
            raise SigningCeremonyError("staged signing state is inconsistent")
    elif phase == "review_signed":
        _hex(review_signature, 128, "review signature")
        _hex(review_approval, 64, "review approval")
        if any(
            item is not None
            for item in (
                finalization_envelope,
                finalization_approval,
                document_digest,
            )
        ):
            raise SigningCeremonyError("reviewed signing state is inconsistent")
    else:
        _hex(review_signature, 128, "review signature")
        _hex(review_approval, 64, "review approval")
        _hex(finalization_approval, 64, "finalization approval")
        _hex(document_digest, 64, "finalizer document")
        if not isinstance(finalization_envelope, Mapping):
            raise SigningCeremonyError("finalized signing state is inconsistent")
    try:
        restore_unsigned_packet(
            value["unsigned_packet"],
            review_public_key=policy.review_public_key,
        )
    except PacketCompilerError as error:
        raise SigningCeremonyError("private signing state packet is invalid") from error
    return phase


def stage_state(
    packet: UnsignedReviewedIdentityPacket,
    *,
    policy: VerificationPolicy,
) -> Mapping[str, Any]:
    """Create immutable, resumable state without possessing either signing key."""

    value = {
        "contract": STATE_CONTRACT,
        "ceremony_policy_sha256": ceremony_policy_sha256(policy),
        "phase": "staged",
        "unsigned_packet": packet.private_state(),
        "review_signature": None,
        "review_approval_sha256": None,
        "finalization_envelope": None,
        "finalization_approval_sha256": None,
        "finalizer_document_sha256": None,
    }
    _validate_state(value, policy)
    return value


def _packet_from_state(
    state: Mapping[str, Any], policy: VerificationPolicy
) -> UnsignedReviewedIdentityPacket:
    _validate_state(state, policy)
    try:
        return restore_unsigned_packet(
            state["unsigned_packet"],
            review_public_key=policy.review_public_key,
        )
    except PacketCompilerError as error:  # already content-free
        raise SigningCeremonyError("private signing state packet is invalid") from error


def review_approval_token(state: Mapping[str, Any], policy: VerificationPolicy) -> str:
    packet = _packet_from_state(state, policy)
    return f"REVIEW {_sha256(packet.review_signing_payload)}"


def _require_private_key(
    private_key: Ed25519PrivateKey,
    expected_public: Ed25519PublicKey,
    label: str,
) -> None:
    if not isinstance(private_key, Ed25519PrivateKey) or not secrets.compare_digest(
        _public_raw(private_key.public_key()), _public_raw(expected_public)
    ):
        raise SigningCeremonyError(f"{label} private key does not match policy")


def approve_review(
    state: Mapping[str, Any],
    *,
    policy: VerificationPolicy,
    review_private_key: Ed25519PrivateKey,
    approval: str,
) -> Mapping[str, Any]:
    """Apply one explicit review approval using only the review-purpose key."""

    if _validate_state(state, policy) != "staged":
        raise SigningCeremonyError("review approval is not valid in this phase")
    packet = _packet_from_state(state, policy)
    digest = _sha256(packet.review_signing_payload)
    if not secrets.compare_digest(approval, f"REVIEW {digest}"):
        raise SigningCeremonyError("review approval did not match")
    _require_private_key(review_private_key, policy.review_public_key, "review")
    signature = review_private_key.sign(packet.review_signing_payload).hex()
    try:
        verify_assembled_packet(packet, signature, policy=policy)
    except PacketCompilerError as error:
        raise SigningCeremonyError("review signature did not verify") from error
    result = dict(state)
    result.update(
        {
            "phase": "review_signed",
            "review_signature": signature,
            "review_approval_sha256": digest,
        }
    )
    _validate_state(result, policy)
    return result


def finalization_approval_token(
    state: Mapping[str, Any],
    policy: VerificationPolicy,
    *,
    now: datetime,
) -> str:
    if _validate_state(state, policy) != "review_signed":
        raise SigningCeremonyError("finalization approval is not valid in this phase")
    packet = _packet_from_state(state, policy)
    try:
        verified = verify_assembled_packet(
            packet, state["review_signature"], policy=policy
        )
        payload = verified.finalization_signing_payload(now=now)
    except (PacketCompilerError, VerificationError) as error:
        raise SigningCeremonyError(
            "reviewed packet is not finalization-eligible"
        ) from error
    return f"FINALIZE {_sha256(payload)}"


def approve_finalization(
    state: Mapping[str, Any],
    *,
    policy: VerificationPolicy,
    finalization_private_key: Ed25519PrivateKey,
    approval: str,
    now: datetime,
) -> tuple[Mapping[str, Any], bytes]:
    """Apply a separate finalization approval and return verified DB-bound bytes."""

    if _validate_state(state, policy) != "review_signed":
        raise SigningCeremonyError("finalization approval is not valid in this phase")
    packet = _packet_from_state(state, policy)
    try:
        verified = verify_assembled_packet(
            packet, state["review_signature"], policy=policy
        )
        payload = verified.finalization_signing_payload(now=now)
    except (PacketCompilerError, VerificationError) as error:
        raise SigningCeremonyError(
            "reviewed packet is not finalization-eligible"
        ) from error
    digest = _sha256(payload)
    if not secrets.compare_digest(approval, f"FINALIZE {digest}"):
        raise SigningCeremonyError("finalization approval did not match")
    _require_private_key(
        finalization_private_key,
        policy.finalization_public_key,
        "finalization",
    )
    proposal = verified.finalization_proposal
    envelope = {
        "contract_version": "reviewed-identity-finalization-envelope-v1",
        "finalization_id": str(verified.run_id),
        "finalization_commitment": proposal["finalization_commitment"],
        "signature_algorithm": "ed25519",
        "signing_key_fingerprint": policy.finalization_key_fingerprint,
        "signature_scope": "reviewed-identity-migration-finalization-v1",
        "finalization_signature": finalization_private_key.sign(payload).hex(),
    }
    raw_envelope = canonical_bytes(envelope)
    try:
        finalizer = verify_finalization_envelope(verified, raw_envelope, policy=policy)
    except VerificationError as error:
        raise SigningCeremonyError("finalization signature did not verify") from error
    document = finalizer.canonical_document()
    result = dict(state)
    result.update(
        {
            "phase": "finalized",
            "finalization_envelope": envelope,
            "finalization_approval_sha256": digest,
            "finalizer_document_sha256": _sha256(document),
        }
    )
    _validate_state(result, policy)
    return result, document


def verify_finalized_state(
    state: Mapping[str, Any], policy: VerificationPolicy
) -> bytes:
    """Reconstruct an exact finalized result, including after review expiry."""

    if _validate_state(state, policy) != "finalized":
        raise SigningCeremonyError("signing state is not finalized")
    packet = _packet_from_state(state, policy)
    raw_bundle = packet.assemble_reviewed_bundle(state["review_signature"])
    raw_envelope = canonical_bytes(state["finalization_envelope"])
    try:
        finalizer = verify_expired_finalization_replay(
            raw_bundle,
            raw_envelope,
            source_records=packet.source_records,
            policy=policy,
        )
    except (PacketCompilerError, VerificationError) as error:
        raise SigningCeremonyError("finalized signing state did not verify") from error
    document = finalizer.canonical_document()
    if not secrets.compare_digest(
        _sha256(document), state["finalizer_document_sha256"]
    ):
        raise SigningCeremonyError("finalized signing state commitment mismatch")
    return document


def content_free_receipt(
    state: Mapping[str, Any], policy: VerificationPolicy
) -> Mapping[str, Any]:
    document = verify_finalized_state(state, policy)
    packet = _packet_from_state(state, policy)
    raw_bundle = packet.assemble_reviewed_bundle(state["review_signature"])
    raw_envelope = canonical_bytes(state["finalization_envelope"])
    return {
        "contract": RECEIPT_CONTRACT,
        "status": "finalized",
        "run_id": str(packet.run_id),
        "ceremony_policy_sha256": state["ceremony_policy_sha256"],
        "review_key_fingerprint": policy.review_key_fingerprint,
        "finalization_key_fingerprint": policy.finalization_key_fingerprint,
        "review_payload_sha256": state["review_approval_sha256"],
        "finalization_payload_sha256": state["finalization_approval_sha256"],
        "reviewed_bundle_sha256": _sha256(raw_bundle),
        "finalization_envelope_sha256": _sha256(raw_envelope),
        "finalizer_document_sha256": _sha256(document),
    }


def state_expires_at(state: Mapping[str, Any]) -> datetime:
    """Return the staged packet's canonical review-window expiry instant."""

    packet = state.get("unsigned_packet") if isinstance(state, Mapping) else None
    run = packet.get("unsigned_run") if isinstance(packet, Mapping) else None
    value = run.get("expires_at") if isinstance(run, Mapping) else None
    try:
        return _canonical_time(value, "review window")
    except VerificationError as error:
        raise SigningCeremonyError("staged review window is invalid") from error


def supersession_archive_name(run_id: str) -> str:
    return f"{SUPERSESSION_ARCHIVE_PREFIX}{run_id}.json"


def supersession_receipt_name(run_id: str) -> str:
    return f"{SUPERSESSION_RECEIPT_PREFIX}{run_id}{SUPERSESSION_RECEIPT_SUFFIX}"


def supersession_receipt_static(
    state: Mapping[str, Any], state_raw: bytes
) -> Mapping[str, Any]:
    """Deterministic receipt fields, reproducible on every resumed attempt."""

    packet = state["unsigned_packet"]
    run_id = str(packet["run_id"])
    return {
        "contract": SUPERSESSION_CONTRACT,
        "status": "superseded",
        "reason_code": SUPERSESSION_REASON,
        "run_id": run_id,
        "review_payload_sha256": packet["review_signing_payload_sha256"],
        "expires_at": packet["unsigned_run"]["expires_at"],
        "private_review_sha256": packet["private_review_sha256"],
        "ceremony_policy_sha256": state["ceremony_policy_sha256"],
        "superseded_state_sha256": _sha256(state_raw),
        "archived_state_name": supersession_archive_name(run_id),
    }


def build_supersession_receipt(
    state: Mapping[str, Any], state_raw: bytes, *, now: datetime
) -> Mapping[str, Any]:
    return {
        **supersession_receipt_static(state, state_raw),
        "created_at": _utc_text(now),
    }


def validate_supersession_receipt(
    receipt: Mapping[str, Any],
    *,
    state: Mapping[str, Any],
    state_raw: bytes,
) -> None:
    """Accept only a byte-consistent prior receipt; never rewrite one."""

    expected = supersession_receipt_static(state, state_raw)
    if (
        not isinstance(receipt, Mapping)
        or set(receipt) != set(expected) | {"created_at"}
        or any(receipt.get(key) != value for key, value in expected.items())
    ):
        raise SigningCeremonyError("supersession receipt is inconsistent")
    try:
        _canonical_time(receipt["created_at"], "supersession receipt")
    except VerificationError as error:
        raise SigningCeremonyError("supersession receipt is inconsistent") from error


def _require_root_linux() -> None:
    if sys.platform != "linux" or os.geteuid() != 0:
        raise SigningCeremonyError("identity signing requires root on Linux")


def _safe_private_root() -> None:
    try:
        details = PRIVATE_ROOT.lstat()
    except FileNotFoundError as error:
        raise SigningCeremonyError("private identity directory is missing") from error
    if (
        not stat.S_ISDIR(details.st_mode)
        or details.st_uid != 0
        or details.st_gid != 0
        or stat.S_IMODE(details.st_mode) != 0o700
    ):
        raise SigningCeremonyError("private identity directory is unsafe")


def _read_root_file(path: Path, *, maximum: int, modes: frozenset[int]) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        details = os.fstat(descriptor)
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_uid != 0
            or details.st_gid != 0
            or stat.S_IMODE(details.st_mode) not in modes
            or details.st_nlink != 1
            or details.st_size <= 0
            or details.st_size > maximum
        ):
            raise SigningCeremonyError("private ceremony file is unsafe")
        raw = os.read(descriptor, maximum + 1)
    except OSError as error:
        raise SigningCeremonyError("private ceremony file is unavailable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not raw or len(raw) > maximum or b"\0" in raw:
        raise SigningCeremonyError("private ceremony file is invalid")
    return raw


def _atomic_exact(path: Path, raw: bytes, *, replace: bool) -> None:
    if not raw or len(raw) > MAX_PRIVATE_BYTES or b"\0" in raw:
        raise SigningCeremonyError("private ceremony artifact is invalid")
    if path.exists() and not replace:
        existing = _read_root_file(
            path, maximum=MAX_PRIVATE_BYTES, modes=frozenset({0o600})
        )
        if secrets.compare_digest(existing, raw):
            return
        raise SigningCeremonyError("private ceremony artifact already differs")
    temporary = path.with_name(f".{path.name}.new.{secrets.token_hex(12)}")
    descriptor = -1
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.chown(temporary, 0, 0)
        os.chmod(temporary, 0o600)
        if not replace and path.exists():
            existing = _read_root_file(
                path, maximum=MAX_PRIVATE_BYTES, modes=frozenset({0o600})
            )
            if not secrets.compare_digest(existing, raw):
                raise SigningCeremonyError("private ceremony artifact already differs")
            return
        os.replace(temporary, path)
        directory = os.open(PRIVATE_ROOT, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _parse_private_json(raw: bytes) -> Mapping[str, Any]:
    canonical = raw[:-1] if raw.endswith(b"\n") else raw
    try:
        value = parse_canonical_json(canonical, maximum=MAX_PRIVATE_BYTES)
    except VerificationError as error:
        raise SigningCeremonyError("private ceremony JSON is invalid") from error
    if not isinstance(value, Mapping):
        raise SigningCeremonyError("private ceremony JSON is invalid")
    return value


def _load_state(policy: VerificationPolicy) -> Mapping[str, Any]:
    raw = _read_root_file(
        STATE_PATH, maximum=MAX_PRIVATE_BYTES, modes=frozenset({0o600})
    )
    state = _parse_private_json(raw)
    _validate_state(state, policy)
    return state


def _write_state(state: Mapping[str, Any]) -> None:
    _atomic_exact(STATE_PATH, canonical_bytes(state) + b"\n", replace=True)


def _credential_directory(phase: str) -> Path:
    raw = os.environ.get("CREDENTIALS_DIRECTORY", "")
    path = Path(raw)
    expected = Path("/run/credentials") / UNIT_NAMES[phase]
    if path != expected:
        raise SigningCeremonyError("systemd credential directory is invalid")
    try:
        names = frozenset(item.name for item in path.iterdir())
    except OSError as error:
        raise SigningCeremonyError("systemd credentials are unavailable") from error
    if names != PHASE_CREDENTIALS[phase]:
        raise SigningCeremonyError("systemd credential purpose separation failed")
    return path


def _load_policy(directory: Path) -> VerificationPolicy:
    raw_policy = _read_root_file(
        directory / "policy.json",
        maximum=MAX_POLICY_BYTES,
        modes=frozenset({0o400, 0o440}),
    )
    try:
        value = parse_canonical_json(raw_policy, maximum=MAX_POLICY_BYTES)
    except VerificationError as error:
        raise SigningCeremonyError("identity signing policy is invalid") from error
    if not isinstance(value, Mapping) or set(value) != POLICY_KEYS:
        raise SigningCeremonyError("identity signing policy is invalid")
    if value["contract"] != POLICY_CONTRACT:
        raise SigningCeremonyError("identity signing policy is unsupported")
    commitment_key = _read_root_file(
        directory / "commitment.key",
        maximum=64,
        modes=frozenset({0o400, 0o440}),
    )
    try:
        review_public = Ed25519PublicKey.from_public_bytes(
            bytes.fromhex(_hex(value["review_public_key_hex"], 64, "review public key"))
        )
        finalization_public = Ed25519PublicKey.from_public_bytes(
            bytes.fromhex(
                _hex(
                    value["finalization_public_key_hex"],
                    64,
                    "finalization public key",
                )
            )
        )
        shadow_id = uuid.UUID(str(value["shadow_authorization_id"]))
        policy = VerificationPolicy(
            review_public_key=review_public,
            review_key_fingerprint=value["review_key_fingerprint"],
            finalization_public_key=finalization_public,
            finalization_key_fingerprint=value["finalization_key_fingerprint"],
            commitment_key=commitment_key,
            commitment_key_epoch=value["commitment_key_epoch"],
            commitment_key_fingerprint=value["commitment_key_fingerprint"],
            policy_version=value["policy_version"],
            policy_digest=value["policy_digest"],
            source_projection_contract_digest=value[
                "source_projection_contract_digest"
            ],
            release_manifest_digest=value["release_manifest_digest"],
            migration_tool_bundle_digest=value["migration_tool_bundle_digest"],
            core_oci_manifest_digest=value["core_oci_manifest_digest"],
            core_schema_digest=value["core_schema_digest"],
            core_capability_digest=value["core_capability_digest"],
            shadow_authorization_id=shadow_id,
            now=lambda: datetime.now(UTC),
        )
    except (TypeError, ValueError, AttributeError) as error:
        raise SigningCeremonyError("identity signing policy is invalid") from error
    if policy_manifest(policy) != value:
        raise SigningCeremonyError("identity signing policy is noncanonical")
    return policy


def _load_private_key(path: Path) -> Ed25519PrivateKey:
    raw = _read_root_file(path, maximum=32, modes=frozenset({0o400, 0o440}))
    if len(raw) != 32:
        raise SigningCeremonyError("purpose signing key is invalid")
    try:
        return Ed25519PrivateKey.from_private_bytes(raw)
    except ValueError as error:
        raise SigningCeremonyError("purpose signing key is invalid") from error


def _snapshot_sha256() -> str:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(SOURCE_PATH, flags)
        details = os.fstat(descriptor)
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_uid != 0
            or details.st_gid != 0
            or stat.S_IMODE(details.st_mode) != 0o600
            or details.st_nlink != 1
            or details.st_size <= 0
            or details.st_size > MAX_SNAPSHOT_BYTES
        ):
            raise SigningCeremonyError("private identity snapshot is unsafe")
        digest = hashlib.sha256()
        while block := os.read(descriptor, 1024 * 1024):
            digest.update(block)
        return digest.hexdigest()
    except OSError as error:
        raise SigningCeremonyError(
            "private identity snapshot is unavailable"
        ) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _review_sha256() -> str:
    review = _parse_private_json(
        _read_root_file(
            REVIEW_PATH, maximum=MAX_PRIVATE_BYTES, modes=frozenset({0o600})
        )
    )
    if set(review) != {"contract", "private_review", "private_review_sha256"}:
        raise SigningCeremonyError("private People review is invalid")
    body = review["private_review"]
    digest = _hex(review["private_review_sha256"], 64, "private review digest")
    if not isinstance(body, Mapping) or _sha256(canonical_bytes(body)) != digest:
        raise SigningCeremonyError("private People review commitment mismatch")
    if body.get("sqlite_snapshot_sha256") != _snapshot_sha256():
        raise SigningCeremonyError("private People review snapshot drifted")
    # The review reports the projection it was written against; re-derive it so a
    # loader change after the review cannot leave a stale plan in the chain.
    try:
        plan_digest = load_plan(SOURCE_PATH).digest
    except MigrationError as error:
        raise SigningCeremonyError(
            "private identity snapshot could not be projected"
        ) from error
    if body.get("source_plan_sha256") != plan_digest:
        raise SigningCeremonyError("private People review plan drifted")
    if (
        body.get("slice_readiness", {}).get("ready_for_private_content_review")
        is not True
    ):
        raise SigningCeremonyError("private People review is not eligible")
    return digest


def _acquire_lock() -> int:
    if fcntl is None:  # constructor-level guard for non-Linux imports
        raise SigningCeremonyError("identity signing lock requires Linux")
    descriptor = os.open(
        LOCK_PATH,
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    details = os.fstat(descriptor)
    if (
        not stat.S_ISREG(details.st_mode)
        or details.st_uid != 0
        or details.st_gid != 0
        or stat.S_IMODE(details.st_mode) != 0o600
        or details.st_nlink != 1
    ):
        os.close(descriptor)
        raise SigningCeremonyError("identity signing lock is unsafe")
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        os.close(descriptor)
        raise SigningCeremonyError(
            "identity signing ceremony is already active"
        ) from error
    return descriptor


def _require_absent(path: Path) -> None:
    try:
        os.lstat(path)
    except FileNotFoundError:
        return
    except OSError as error:
        raise SigningCeremonyError("signing chain artifact is unavailable") from error
    raise SigningCeremonyError("signing chain artifact already exists")


def _acquire_runner_lock() -> int:
    if fcntl is None:  # constructor-level guard for non-Linux imports
        raise SigningCeremonyError("identity signing lock requires Linux")
    try:
        descriptor = os.open(
            RUNNER_LOCK_PATH, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        )
    except OSError as error:
        raise SigningCeremonyError("activation runner lock is unavailable") from error
    details = os.fstat(descriptor)
    if (
        not stat.S_ISREG(details.st_mode)
        or details.st_uid != 0
        or details.st_gid != 0
        or stat.S_IMODE(details.st_mode) != 0o600
        or details.st_nlink != 1
    ):
        os.close(descriptor)
        raise SigningCeremonyError("activation runner lock is unsafe")
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        os.close(descriptor)
        raise SigningCeremonyError("activation runner is active") from error
    return descriptor


def _runner_journal_for_supersession() -> None:
    journal = _parse_private_json(
        _read_root_file(
            RUNNER_JOURNAL_PATH, maximum=MAX_PRIVATE_BYTES, modes=frozenset({0o600})
        )
    )
    if (
        journal.get("contract") != RUNNER_JOURNAL_CONTRACT
        or journal.get("completed_steps") != list(RUNNER_COMPLETED_PREFIX)
        or journal.get("next_step") != RUNNER_AWAIT_STEP
        or journal.get("status") not in {"active", "paused", "running"}
        or journal.get("pause_code") not in RUNNER_PAUSE_CODES
    ):
        raise SigningCeremonyError("activation boundary refuses supersession")


def _environment_for_supersession() -> None:
    try:
        details = os.lstat(ENVIRONMENT_PATH)
    except OSError as error:
        raise SigningCeremonyError("activation environment is unavailable") from error
    if (
        not stat.S_ISREG(details.st_mode)
        or details.st_uid != 0
        or details.st_gid != 0
        or details.st_nlink != 1
        or stat.S_IMODE(details.st_mode) & 0o022
    ):
        raise SigningCeremonyError("activation environment is unsafe")
    try:
        text = ENVIRONMENT_PATH.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise SigningCeremonyError("activation environment is unavailable") from error
    expected = {
        "HOME_AGENT_EXPECTED_DB_REVISION": EXPECTED_DB_REVISION,
        "HOME_AGENT_ROLLOUT_MODE": EXPECTED_ROLLOUT_MODE,
    }
    counts = dict.fromkeys(expected, 0)
    for line in text.splitlines():
        name, separator, value = line.partition("=")
        if separator and name in expected:
            counts[name] += 1
            if value != expected[name]:
                raise SigningCeremonyError(
                    "activation environment refuses supersession"
                )
    if any(count != 1 for count in counts.values()):
        raise SigningCeremonyError("activation environment refuses supersession")


def _credential_receipt_for_supersession(policy: VerificationPolicy) -> None:
    receipt = _parse_private_json(
        _read_root_file(
            CREDENTIAL_RECEIPT_PATH,
            maximum=MAX_PRIVATE_BYTES,
            modes=frozenset({0o400, 0o440, 0o600}),
        )
    )
    manifest = policy_manifest(policy)
    bound_keys = (
        "review_key_fingerprint",
        "finalization_key_fingerprint",
        "commitment_key_fingerprint",
        "commitment_key_epoch",
        "policy_version",
        "policy_digest",
        "source_projection_contract_digest",
        "release_manifest_digest",
        "migration_tool_bundle_digest",
        "core_oci_manifest_digest",
        "core_schema_digest",
        "core_capability_digest",
        "shadow_authorization_id",
    )
    if (
        receipt.get("contract") != CREDENTIAL_RECEIPT_CONTRACT
        or receipt.get("status") != "provisioned"
        or receipt.get("credential_count") != CREDENTIAL_RECEIPT_COUNT
        or receipt.get("key_source") not in {"host", "tpm2", "host+tpm2"}
        or any(receipt.get(key) != manifest[key] for key in bound_keys)
    ):
        raise SigningCeremonyError("identity credential receipt refuses supersession")


def _superseded_pairs(
    policy: VerificationPolicy,
) -> list[tuple[str, Mapping[str, Any]]]:
    try:
        names = sorted(item.name for item in PRIVATE_ROOT.iterdir())
    except OSError as error:
        raise SigningCeremonyError(
            "private identity directory is unavailable"
        ) from error
    archives: dict[str, str] = {}
    receipts: dict[str, str] = {}
    for name in names:
        if name.startswith("."):
            continue
        if name.startswith(SUPERSESSION_ARCHIVE_PREFIX) and name.endswith(".json"):
            archives[name[len(SUPERSESSION_ARCHIVE_PREFIX) : -len(".json")]] = name
        elif name.startswith(SUPERSESSION_RECEIPT_PREFIX) and name.endswith(
            SUPERSESSION_RECEIPT_SUFFIX
        ):
            receipts[
                name[len(SUPERSESSION_RECEIPT_PREFIX) : -len(SUPERSESSION_RECEIPT_SUFFIX)]
            ] = name
    if not archives or set(archives) != set(receipts):
        raise SigningCeremonyError("supersession lineage is incomplete")
    pairs: list[tuple[str, Mapping[str, Any]]] = []
    for run_id in sorted(archives):
        archive_raw = _read_root_file(
            PRIVATE_ROOT / archives[run_id],
            maximum=MAX_PRIVATE_BYTES,
            modes=frozenset({0o600}),
        )
        archived_state = _parse_private_json(archive_raw)
        if (
            _validate_state(archived_state, policy) != "staged"
            or str(archived_state["unsigned_packet"]["run_id"]) != run_id
        ):
            raise SigningCeremonyError("supersession lineage is inconsistent")
        receipt = _parse_private_json(
            _read_root_file(
                PRIVATE_ROOT / receipts[run_id],
                maximum=MAX_PRIVATE_BYTES,
                modes=frozenset({0o600}),
            )
        )
        validate_supersession_receipt(
            receipt, state=archived_state, state_raw=archive_raw
        )
        pairs.append((run_id, receipt))
    return pairs


def _run_supersede(policy: VerificationPolicy) -> Mapping[str, Any]:
    runner_lock = _acquire_runner_lock()
    try:
        try:
            os.lstat(STATE_PATH)
        except FileNotFoundError:
            pairs = _superseded_pairs(policy)
            run_id, receipt = pairs[-1]
            return {
                "contract": CONTRACT,
                "phase": "superseded",
                "run_id": run_id,
                "review_payload_sha256": receipt["review_payload_sha256"],
                "superseded_packet_count": len(pairs),
                "status": "already_superseded",
            }
        except OSError as error:
            raise SigningCeremonyError(
                "private ceremony file is unavailable"
            ) from error
        raw = _read_root_file(
            STATE_PATH, maximum=MAX_PRIVATE_BYTES, modes=frozenset({0o600})
        )
        state = _parse_private_json(raw)
        if _validate_state(state, policy) != "staged":
            raise SigningCeremonyError("supersession is not valid in this phase")
        for path in SUPERSESSION_ABSENT_PATHS:
            _require_absent(path)
        _runner_journal_for_supersession()
        _environment_for_supersession()
        _credential_receipt_for_supersession(policy)
        packet = _packet_from_state(state, policy)
        if packet.private_review_sha256 != _review_sha256():
            raise SigningCeremonyError("private People review drifted")
        now = policy.now()
        if state_expires_at(state) > now:
            raise SigningCeremonyError("staged review window is still live")
        run_id = str(packet.run_id)
        receipt_path = PRIVATE_ROOT / supersession_receipt_name(run_id)
        archive_path = PRIVATE_ROOT / supersession_archive_name(run_id)
        try:
            os.lstat(receipt_path)
        except FileNotFoundError:
            fresh = build_supersession_receipt(state, raw, now=now)
            _atomic_exact(
                receipt_path, canonical_bytes(fresh) + b"\n", replace=False
            )
        except OSError as error:
            raise SigningCeremonyError(
                "private ceremony file is unavailable"
            ) from error
        else:
            existing = _parse_private_json(
                _read_root_file(
                    receipt_path,
                    maximum=MAX_PRIVATE_BYTES,
                    modes=frozenset({0o600}),
                )
            )
            validate_supersession_receipt(existing, state=state, state_raw=raw)
        try:
            os.lstat(archive_path)
        except FileNotFoundError:
            pass
        except OSError as error:
            raise SigningCeremonyError(
                "private ceremony file is unavailable"
            ) from error
        else:
            existing_archive = _read_root_file(
                archive_path, maximum=MAX_PRIVATE_BYTES, modes=frozenset({0o600})
            )
            if not secrets.compare_digest(existing_archive, raw):
                raise SigningCeremonyError("supersession archive already differs")
        try:
            os.replace(STATE_PATH, archive_path)
            directory = os.open(PRIVATE_ROOT, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError as error:
            raise SigningCeremonyError("supersession archive failed") from error
        return {
            "contract": CONTRACT,
            "phase": "superseded",
            "run_id": run_id,
            "review_payload_sha256": state["unsigned_packet"][
                "review_signing_payload_sha256"
            ],
            "status": "superseded_review_window_expired",
        }
    finally:
        os.close(runner_lock)


def _run_stage(policy: VerificationPolicy) -> Mapping[str, Any]:
    try:
        state = _load_state(policy)
    except SigningCeremonyError:
        if STATE_PATH.exists():
            raise
    else:
        result = {
            "contract": CONTRACT,
            "phase": state["phase"],
            "run_id": state["unsigned_packet"]["run_id"],
            "status": "resumed",
        }
        if state["phase"] == "staged":
            if state_expires_at(state) <= policy.now():
                raise SigningCeremonyError("staged review window has expired")
            result["expires_at"] = state["unsigned_packet"]["unsigned_run"][
                "expires_at"
            ]
        return result
    review_digest = _review_sha256()
    try:
        plan = load_plan(SOURCE_PATH)
        inventory = load_source_inventory(SOURCE_PATH)
        packet = compile_unsigned_packet(
            plan,
            inventory,
            private_review_sha256=review_digest,
            policy=policy,
        )
    except (MigrationError, PacketCompilerError) as error:
        raise SigningCeremonyError("identity packet compilation failed") from error
    state = stage_state(packet, policy=policy)
    _atomic_exact(STATE_PATH, canonical_bytes(state) + b"\n", replace=False)
    return {
        "contract": CONTRACT,
        "phase": "staged",
        "run_id": str(packet.run_id),
        "review_payload_sha256": _sha256(packet.review_signing_payload),
        "expires_at": state["unsigned_packet"]["unsigned_run"]["expires_at"],
        "status": "awaiting_private_review_approval",
    }


def _private_approval(prompt: str) -> str:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise SigningCeremonyError("signing approval requires a private TTY")
    print(prompt)
    return input("Type the exact approval line: ").strip()


def _run_review(policy: VerificationPolicy, directory: Path) -> Mapping[str, Any]:
    state = _load_state(policy)
    if state["phase"] != "staged":
        raise SigningCeremonyError("review ceremony is not valid in this phase")
    packet = _packet_from_state(state, policy)
    if packet.private_review_sha256 != _review_sha256():
        raise SigningCeremonyError("private People review drifted")
    remaining = int((state_expires_at(state) - policy.now()).total_seconds())
    if remaining < REVIEW_WINDOW_MINIMUM_SECONDS:
        raise SigningCeremonyError("staged review window has expired")
    token = review_approval_token(state, policy)
    approval = _private_approval(
        "Review the private People packet, then approve only this exact digest:\n"
        + token
        + f"\nReview window remaining: {remaining}s"
    )
    updated = approve_review(
        state,
        policy=policy,
        review_private_key=_load_private_key(directory / "review.key"),
        approval=approval,
    )
    _write_state(updated)
    return {
        "contract": CONTRACT,
        "phase": "review_signed",
        "run_id": updated["unsigned_packet"]["run_id"],
        "review_payload_sha256": updated["review_approval_sha256"],
        "status": "awaiting_distinct_finalization_approval",
    }


def _write_final_artifacts(
    state: Mapping[str, Any], document: bytes, policy: VerificationPolicy
) -> Mapping[str, Any]:
    receipt = content_free_receipt(state, policy)
    _atomic_exact(DOCUMENT_PATH, document + b"\n", replace=False)
    _write_state(state)
    _atomic_exact(RECEIPT_PATH, canonical_bytes(receipt) + b"\n", replace=False)
    return receipt


def _run_finalize(policy: VerificationPolicy, directory: Path) -> Mapping[str, Any]:
    state = _load_state(policy)
    if state["phase"] == "finalized":
        document = verify_finalized_state(state, policy)
        receipt = _write_final_artifacts(state, document, policy)
        return {**receipt, "ceremony_status": "resumed"}
    if state["phase"] != "review_signed":
        raise SigningCeremonyError("finalization ceremony is not valid in this phase")
    packet = _packet_from_state(state, policy)
    if packet.private_review_sha256 != _review_sha256():
        raise SigningCeremonyError("private People review drifted")
    now = datetime.now(UTC)
    token = finalization_approval_token(state, policy, now=now)
    approval = _private_approval(
        "Finalize only the independently reviewed migration digest below:\n" + token
    )
    updated, document = approve_finalization(
        state,
        policy=policy,
        finalization_private_key=_load_private_key(directory / "finalization.key"),
        approval=approval,
        now=datetime.now(UTC),
    )
    receipt = _write_final_artifacts(updated, document, policy)
    return {**receipt, "ceremony_status": "completed"}


def run(phase: str) -> Mapping[str, Any]:
    _require_root_linux()
    _safe_private_root()
    directory = _credential_directory(phase)
    policy = _load_policy(directory)
    descriptor = _acquire_lock()
    try:
        if phase == "stage":
            return _run_stage(policy)
        if phase == "review":
            return _run_review(policy, directory)
        if phase == "finalize":
            return _run_finalize(policy, directory)
        if phase == "supersede-expired":
            return _run_supersede(policy)
        raise SigningCeremonyError("identity signing phase is unsupported")
    finally:
        os.close(descriptor)


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in PHASES:
        print("identity signing requires one fixed phase", file=sys.stderr)
        return 64
    try:
        result = run(sys.argv[1])
    except (SigningCeremonyError, OSError):
        print("identity signing ceremony failed closed", file=sys.stderr)
        return 78
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
