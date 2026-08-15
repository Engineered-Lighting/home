#!/usr/bin/env python3
"""One-time, restart-safe provisioning of Phase 3 signing credentials.

All private key material is generated in memory and is passed directly to
``systemd-creds`` over stdin.  Only host-bound encrypted blobs are ever placed
on disk.  The command accepts no paths, keys, digests, or policy values from
the caller; every non-secret input is derived from the hosted-accepted source,
the exact installed Core image, and the current durable shadow authorization.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
from typing import Any, Callable, Mapping, Sequence
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import migrate_legacy_identity as migration
from reviewed_identity_payload import VerificationPolicy, canonical_bytes
import phase3_identity_signing_ceremony as signing


CONTRACT = "phase3-identity-credential-provisioner-e5ae-v1"
STATE_CONTRACT = "phase3-identity-credential-state-e5ae-v1"
RECEIPT_CONTRACT = "phase3-identity-credential-receipt-e5ae-v1"
KEY_SOURCE_CONTRACT = "phase3-signing-key-source-e5ae-v1"
MATERIAL_CONTRACT = "phase3-signing-material-e5ae-v1"
SOURCE_ACCEPTANCE_CONTRACT = "phase3-source-acceptance-e5j-v1"
# Phase 3 is admitted from a detached, clean activation checkout.  The live
# development checkout may contain unrelated operator work and is never a
# trusted signing source.
SOURCE_ROOT = Path("/opt/home/home-agent-integration-test")
OPERATOR_ROOT = SOURCE_ROOT / "stack/home-agent-deploy/operator"
CORE_ROOT = SOURCE_ROOT / "stack/services/home-agent-core"
POLICY_PATH = SOURCE_ROOT / "stack/home-agent-deploy/policy/home-agent-mvp-v1.json"
ENVIRONMENT_PATH = Path("/srv/home-agent/config/home-agent.env")
SOURCE_ACCEPTANCE_PATH = Path(
    "/srv/home-agent/config/phase3-source-acceptance-e5j.json"
)
KEY_SOURCE_PATH = Path("/srv/home-agent/config/phase3-signing-key-source-e5ae.json")
STATE_PATH = Path(
    "/srv/home-agent/config/phase3-identity-credential-state-e5ae.json"
)
RECEIPT_PATH = Path(
    "/srv/home-agent/config/phase3-identity-signing-credentials-e5ae.json"
)
CREDENTIAL_ROOT = Path("/etc/credstore.encrypted")
INSTALL_ROOT = Path("/usr/local/libexec/home-agent-identity-signing")
INSTALL_MANIFEST = INSTALL_ROOT / "installed.sha256"
CORE_CONTAINER = "home-agent-core-api-1"
CORE_IMAGE = "engineered-lighting/home-agent-core:local"
SYSTEMD_CREDS = Path("/usr/bin/systemd-creds")
MAX_JSON = 256 * 1024
HEX64 = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
ALLOWED_KEY_SOURCES = frozenset({"host", "tpm2", "host+tpm2"})
TOOL_FILES = (
    "migrate_legacy_identity.py",
    "reviewed_identity_payload.py",
    "reviewed_identity_packet_compiler.py",
    "phase3_identity_signing_ceremony.py",
    "phase3_identity_credential_provisioner.py",
    "phase3_writer_freeze_evidence.py",
    "phase3_writer_freeze_ceremony.py",
    "phase3_privacy_cutover_evidence.py",
    "phase3_privacy_cutover_ceremony.py",
    "phase3_semantic_cutover_packet.py",
    "phase3_semantic_cutover_ceremony.py",
)
CREDENTIALS = (
    ("policy.json", "home-agent-identity-policy.cred"),
    ("commitment.key", "home-agent-identity-commitment.cred"),
    ("review.key", "home-agent-identity-review.cred"),
    ("finalization.key", "home-agent-identity-finalization.cred"),
    (
        "writer-freeze-policy.json",
        "home-agent-identity-writer-freeze-policy.cred",
    ),
    ("writer-freeze.key", "home-agent-identity-writer-freeze.cred"),
    (
        "privacy-probe-policy.json",
        "home-agent-identity-privacy-probe-policy.cred",
    ),
    ("privacy-probe.key", "home-agent-identity-privacy-probe.cred"),
    (
        "semantic-cutover-policy.json",
        "home-agent-identity-semantic-cutover-policy.cred",
    ),
    ("semantic-cutover.key", "home-agent-identity-semantic-cutover.cred"),
)
CORE_SCHEMA_PATHS = (
    "alembic.ini",
    "alembic/env.py",
    "alembic/script.py.mako",
    "app/schema.py",
)


class CredentialProvisioningError(RuntimeError):
    """A content-free credential-provisioning failure."""


@dataclass(frozen=True, slots=True)
class CredentialBundle:
    plaintext: Mapping[str, bytes]
    plaintext_sha256: Mapping[str, str]
    receipt: Mapping[str, Any]


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_digest(value: Any) -> str:
    return _sha256(canonical_bytes(value))


def _private_raw(key: Ed25519PrivateKey) -> bytes:
    return key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _public_raw(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def core_schema_digest(root: Path) -> str:
    paths = list(CORE_SCHEMA_PATHS)
    try:
        paths.extend(
            str(path.relative_to(root)).replace("\\", "/")
            for path in sorted((root / "alembic/versions").glob("*.py"))
        )
        manifest = []
        for relative in paths:
            raw = (root / relative).read_bytes()
            if not raw or b"\0" in raw:
                raise CredentialProvisioningError("Core schema source is invalid")
            manifest.append({"path": relative, "sha256": _sha256(raw)})
    except OSError as error:
        raise CredentialProvisioningError(
            "Core schema source is unavailable"
        ) from error
    if len(paths) != len(set(paths)) or not any(
        value.startswith("alembic/versions/") for value in paths
    ):
        raise CredentialProvisioningError("Core schema source is incomplete")
    return _canonical_digest({"contract": "core-schema-bundle-v1", "files": manifest})


def compile_bundle(
    *,
    material: Mapping[str, Any],
    source_commit: str,
    source_pack_digest: str,
    migration_tool_bundle_digest: str,
    core_oci_manifest_digest: str,
    expected_policy_version: str,
    expected_policy_digest: str,
    expected_source_projection_digest: str,
    expected_core_schema_digest: str,
    expected_core_capability_digest: str,
    operation_id: uuid.UUID,
    key_factory: Callable[[], Ed25519PrivateKey] = Ed25519PrivateKey.generate,
    random_bytes: Callable[[int], bytes] = secrets.token_bytes,
) -> CredentialBundle:
    expected_keys = {
        "contract",
        "schema_revision",
        "shadow_authorization_id",
        "shadow_rule_version",
        "policy_version",
        "policy_digest",
        "source_projection_contract_digest",
        "core_schema_digest",
        "core_capability_digest",
        "authorization_status",
        "authoritative",
        "read_only",
    }
    if set(material) != expected_keys or material.get("contract") != MATERIAL_CONTRACT:
        raise CredentialProvisioningError("Core signing material is invalid")
    if (
        material.get("schema_revision") != "0006a_worker_lease_arbitration"
        or material.get("shadow_rule_version")
        != "record-only-envelope-worker-gate-v3"
        or material.get("policy_version") != expected_policy_version
        or material.get("policy_digest") != expected_policy_digest
        or material.get("source_projection_contract_digest")
        != expected_source_projection_digest
        or material.get("core_schema_digest") != expected_core_schema_digest
        or material.get("core_capability_digest")
        != expected_core_capability_digest
        or material.get("authorization_status") != "authorized"
        or material.get("authoritative") is not False
        or material.get("read_only") is not True
    ):
        raise CredentialProvisioningError("Core signing material does not match")
    digests = (
        source_pack_digest,
        migration_tool_bundle_digest,
        core_oci_manifest_digest,
        expected_policy_digest,
        expected_source_projection_digest,
        expected_core_schema_digest,
        expected_core_capability_digest,
    )
    if COMMIT.fullmatch(source_commit) is None or any(
        HEX64.fullmatch(str(value)) is None for value in digests
    ):
        raise CredentialProvisioningError("credential source digest is invalid")
    try:
        shadow_id = uuid.UUID(str(material["shadow_authorization_id"]))
    except (ValueError, TypeError, AttributeError) as error:
        raise CredentialProvisioningError(
            "shadow authorization identifier is invalid"
        ) from error
    if shadow_id.version not in {4, 7} or operation_id.version != 7:
        raise CredentialProvisioningError("credential identifier is invalid")

    purpose_names = ("review", "finalization", "writer", "privacy", "semantic")
    purpose_keys = {name: key_factory() for name in purpose_names}
    public_raw = {name: _public_raw(key) for name, key in purpose_keys.items()}
    fingerprints = {name: _sha256(raw) for name, raw in public_raw.items()}
    if len(set(public_raw.values())) != len(public_raw):
        raise CredentialProvisioningError("purpose signing keys are not distinct")
    commitment = random_bytes(32)
    if len(commitment) != 32:
        raise CredentialProvisioningError("commitment key generation failed")
    commitment_fingerprint = _sha256(commitment)
    policy = VerificationPolicy(
        review_public_key=purpose_keys["review"].public_key(),
        review_key_fingerprint=fingerprints["review"],
        finalization_public_key=purpose_keys["finalization"].public_key(),
        finalization_key_fingerprint=fingerprints["finalization"],
        commitment_key=commitment,
        commitment_key_epoch=1,
        commitment_key_fingerprint=commitment_fingerprint,
        policy_version=expected_policy_version,
        policy_digest=expected_policy_digest,
        source_projection_contract_digest=expected_source_projection_digest,
        release_manifest_digest=source_pack_digest,
        migration_tool_bundle_digest=migration_tool_bundle_digest,
        core_oci_manifest_digest=core_oci_manifest_digest,
        core_schema_digest=expected_core_schema_digest,
        core_capability_digest=expected_core_capability_digest,
        shadow_authorization_id=shadow_id,
    )
    policy_raw = canonical_bytes(signing.policy_manifest(policy))

    def purpose_policy(contract: str, name: str) -> bytes:
        return canonical_bytes(
            {
                "contract": contract,
                "public_key_hex": public_raw[name].hex(),
                "key_fingerprint": fingerprints[name],
            }
        )

    plaintext = {
        "policy.json": policy_raw,
        "commitment.key": commitment,
        "review.key": _private_raw(purpose_keys["review"]),
        "finalization.key": _private_raw(purpose_keys["finalization"]),
        "writer-freeze-policy.json": purpose_policy(
            "phase3-writer-freeze-signing-policy-e5z-v1", "writer"
        ),
        "writer-freeze.key": _private_raw(purpose_keys["writer"]),
        "privacy-probe-policy.json": purpose_policy(
            "phase3-privacy-probe-signing-policy-e5aa-v1", "privacy"
        ),
        "privacy-probe.key": _private_raw(purpose_keys["privacy"]),
        "semantic-cutover-policy.json": purpose_policy(
            "phase3-semantic-cutover-signing-policy-e5ab-v1", "semantic"
        ),
        "semantic-cutover.key": _private_raw(purpose_keys["semantic"]),
    }
    if set(plaintext) != {name for name, _target in CREDENTIALS}:
        raise CredentialProvisioningError("credential purpose set is incomplete")
    hashes = {name: _sha256(value) for name, value in plaintext.items()}
    receipt = {
        "contract": RECEIPT_CONTRACT,
        "operation_id": str(operation_id),
        "source_commit": source_commit,
        "release_manifest_digest": source_pack_digest,
        "migration_tool_bundle_digest": migration_tool_bundle_digest,
        "core_oci_manifest_digest": core_oci_manifest_digest,
        "core_schema_digest": expected_core_schema_digest,
        "core_capability_digest": expected_core_capability_digest,
        "source_projection_contract_digest": expected_source_projection_digest,
        "policy_version": expected_policy_version,
        "policy_digest": expected_policy_digest,
        "shadow_authorization_id": str(shadow_id),
        "review_key_fingerprint": fingerprints["review"],
        "finalization_key_fingerprint": fingerprints["finalization"],
        "writer_freeze_key_fingerprint": fingerprints["writer"],
        "privacy_probe_key_fingerprint": fingerprints["privacy"],
        "semantic_cutover_key_fingerprint": fingerprints["semantic"],
        "commitment_key_fingerprint": commitment_fingerprint,
        "commitment_key_epoch": 1,
        "credential_count": len(CREDENTIALS),
        "status": "provisioned",
    }
    return CredentialBundle(plaintext=plaintext, plaintext_sha256=hashes, receipt=receipt)


def _run(
    command: Sequence[str],
    *,
    input_bytes: bytes | None = None,
    accepted: frozenset[int] = frozenset({0}),
    timeout: int = 120,
    allow_binary: bool = False,
) -> bytes:
    try:
        result = subprocess.run(
            list(command),
            check=False,
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise CredentialProvisioningError("credential subprocess failed") from error
    if (
        result.returncode not in accepted
        or len(result.stdout) > MAX_JSON
        or (not allow_binary and b"\0" in result.stdout)
    ):
        raise CredentialProvisioningError("credential subprocess failed")
    return result.stdout


def _json(raw: bytes, *, maximum: int = MAX_JSON) -> Mapping[str, Any]:
    if not raw or len(raw) > maximum or b"\0" in raw:
        raise CredentialProvisioningError("credential JSON is invalid")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CredentialProvisioningError("credential JSON is invalid") from error
    if not isinstance(value, Mapping):
        raise CredentialProvisioningError("credential JSON is invalid")
    return value


def _safe_file(path: Path, *, mode: int = 0o600) -> bytes:
    try:
        details = path.lstat()
        raw = path.read_bytes()
    except OSError as error:
        raise CredentialProvisioningError("credential input is unavailable") from error
    if (
        not stat.S_ISREG(details.st_mode)
        or details.st_uid != 0
        or details.st_gid != 0
        or stat.S_IMODE(details.st_mode) != mode
        or details.st_nlink != 1
        or not raw
        or len(raw) > MAX_JSON
        or b"\0" in raw
    ):
        raise CredentialProvisioningError("credential input is unsafe")
    return raw


def _environment() -> Mapping[str, str]:
    raw = _safe_file(ENVIRONMENT_PATH)
    selected: dict[str, str] = {}
    expected = {"HOME_AGENT_POLICY_DIGEST"}
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise CredentialProvisioningError("deployment policy is invalid") from error
    for line in text.splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in expected:
            if key in selected or not value:
                raise CredentialProvisioningError("deployment policy is invalid")
            selected[key] = value
    if set(selected) != expected:
        raise CredentialProvisioningError("deployment policy is incomplete")
    return selected


def _installed_bundle_digest() -> str:
    raw = _safe_file(INSTALL_MANIFEST, mode=0o644)
    lines = raw.decode("ascii").splitlines()
    expected = [f"{name}" for name in TOOL_FILES]
    names: list[str] = []
    for line in lines:
        if len(line) < 67 or line[64:66] != "  ":
            raise CredentialProvisioningError("installed tool manifest is invalid")
        digest, name = line[:64], line[66:]
        if HEX64.fullmatch(digest) is None or name.startswith(('/', '.')) or '/' in name:
            raise CredentialProvisioningError("installed tool manifest is invalid")
        names.append(name)
        try:
            installed = (INSTALL_ROOT / name).read_bytes()
            source = (OPERATOR_ROOT / name).read_bytes()
        except OSError as error:
            raise CredentialProvisioningError(
                "installed tool bundle is incomplete"
            ) from error
        if _sha256(installed) != digest or not secrets.compare_digest(installed, source):
            raise CredentialProvisioningError("installed tool bundle does not match")
    if names != expected:
        raise CredentialProvisioningError("installed tool manifest is incomplete")
    return _sha256(raw)


def _source_report() -> Mapping[str, Any]:
    report = _json(
        _run(
            [sys.executable, str(OPERATOR_ROOT / "phase3_activation_source_plan.py")],
            accepted=frozenset({3}),
        )
    )
    if (
        report.get("source_pack_matches_hosted_acceptance") is not True
        or report.get("source_acceptance_receipt_issuable") is not True
        or report.get("blockers") != []
        or COMMIT.fullmatch(str(report.get("current_commit", ""))) is None
        or HEX64.fullmatch(str(report.get("source_pack_digest", ""))) is None
    ):
        raise CredentialProvisioningError("hosted accepted source is unavailable")
    acceptance = _json(_safe_file(SOURCE_ACCEPTANCE_PATH))
    if (
        acceptance.get("contract") != SOURCE_ACCEPTANCE_CONTRACT
        or acceptance.get("source_commit") != report["current_commit"]
        or acceptance.get("postgres_authority_run_id")
        != report.get("accepted_postgres_run_id")
        or acceptance.get("web_boundary_run_id") != report.get("accepted_web_run_id")
        or acceptance.get("postgres_authority_status") != "success"
        or acceptance.get("web_boundary_status") != "success"
        or acceptance.get("activation_contract_status") != "installed"
    ):
        raise CredentialProvisioningError("source acceptance receipt is invalid")
    return report


def _image_digest(expected_source_commit: str) -> str:
    if COMMIT.fullmatch(expected_source_commit) is None:
        raise CredentialProvisioningError("accepted source commit is invalid")
    container = _run(
        ["docker", "inspect", "--format={{.Image}}", CORE_CONTAINER]
    ).decode("ascii").strip()
    image = _run(
        ["docker", "image", "inspect", "--format={{.Id}}", CORE_IMAGE]
    ).decode("ascii").strip()
    for name in (
        "home-agent-core-api-1",
        "home-agent-core-ingest-1",
        "home-agent-core-worker-1",
    ):
        value = _run(["docker", "inspect", "--format={{.Image}}", name]).decode(
            "ascii"
        ).strip()
        if value != image:
            raise CredentialProvisioningError("Core images are not identical")
    if container != image or not image.startswith("sha256:"):
        raise CredentialProvisioningError("installed Core image is invalid")
    revision = _run(
        [
            "docker",
            "image",
            "inspect",
            "--format={{index .Config.Labels "
            '"org.opencontainers.image.revision"}}',
            CORE_IMAGE,
        ]
    ).decode("ascii").strip()
    if revision != expected_source_commit:
        raise CredentialProvisioningError("installed Core source revision differs")
    digest = image.removeprefix("sha256:")
    if HEX64.fullmatch(digest) is None:
        raise CredentialProvisioningError("installed Core image is invalid")
    return digest


def _key_source() -> str:
    value = _json(_safe_file(KEY_SOURCE_PATH))
    if set(value) != {"contract", "with_key"} or value.get("contract") != KEY_SOURCE_CONTRACT:
        raise CredentialProvisioningError("signing key source is invalid")
    with_key = value.get("with_key")
    if with_key not in ALLOWED_KEY_SOURCES:
        raise CredentialProvisioningError("signing key source is unsupported")
    return str(with_key)


def _prepare_key_source(with_key: str) -> None:
    if "tpm2" in with_key:
        _run([str(SYSTEMD_CREDS), "has-tpm2"], timeout=30)
    if "host" in with_key:
        _run([str(SYSTEMD_CREDS), "setup"], timeout=30)


def _uuid7() -> uuid.UUID:
    timestamp_ms = int(datetime.now(UTC).timestamp() * 1000)
    random_bits = secrets.randbits(74)
    value = timestamp_ms << 80
    value |= 0x7 << 76
    value |= ((random_bits >> 62) & 0xFFF) << 64
    value |= 0b10 << 62
    value |= random_bits & ((1 << 62) - 1)
    return uuid.UUID(int=value)


def _atomic_json(path: Path, value: Mapping[str, Any], *, replace: bool) -> None:
    raw = canonical_bytes(value) + b"\n"
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
        if not replace and (path.exists() or path.is_symlink()):
            raise CredentialProvisioningError("credential receipt already exists")
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
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


def _encrypt(name: str, raw: bytes, with_key: str) -> bytes:
    encrypted = _run(
        [
            str(SYSTEMD_CREDS),
            "encrypt",
            f"--name={name}",
            f"--with-key={with_key}",
            "-",
            "-",
        ],
        input_bytes=raw,
        allow_binary=True,
    )
    if not encrypted or len(encrypted) > 64 * 1024:
        raise CredentialProvisioningError("credential encryption failed")
    return encrypted


def _decrypt(path: Path, name: str) -> bytes:
    return _run(
        [str(SYSTEMD_CREDS), "decrypt", f"--name={name}", str(path), "-"],
        timeout=30,
        allow_binary=True,
    )


def _credential_paths() -> Mapping[str, Path]:
    return {name: CREDENTIAL_ROOT / target for name, target in CREDENTIALS}


def _validate_roots() -> None:
    if sys.platform != "linux" or os.geteuid() != 0:
        raise CredentialProvisioningError("credential provisioning requires root Linux")
    for path, allowed_modes in (
        (CREDENTIAL_ROOT, frozenset({0o700})),
        (STATE_PATH.parent, frozenset({0o700, 0o750})),
    ):
        details = path.lstat()
        if (
            not stat.S_ISDIR(details.st_mode)
            or details.st_uid != 0
            or details.st_gid != 0
            or stat.S_IMODE(details.st_mode) not in allowed_modes
        ):
            raise CredentialProvisioningError("credential directory is unsafe")
    details = SYSTEMD_CREDS.lstat()
    if (
        not stat.S_ISREG(details.st_mode)
        or details.st_uid != 0
        or stat.S_IMODE(details.st_mode) & 0o022
    ):
        raise CredentialProvisioningError("system credential tool is unsafe")


def _write_staged(path: Path, raw: bytes) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.chown(path, 0, 0)
    os.chmod(path, 0o600)


def _validate_state(value: Mapping[str, Any]) -> None:
    keys = {
        "contract",
        "operation_id",
        "phase",
        "staging_directory",
        "with_key",
        "plaintext_sha256",
        "receipt",
    }
    if (
        set(value) != keys
        or value.get("contract") != STATE_CONTRACT
        or value.get("phase") not in {"preparing", "publishing", "complete"}
        or value.get("with_key") not in ALLOWED_KEY_SOURCES
        or not isinstance(value.get("plaintext_sha256"), Mapping)
        or set(value["plaintext_sha256"]) != {name for name, _ in CREDENTIALS}
        or any(HEX64.fullmatch(str(item)) is None for item in value["plaintext_sha256"].values())
        or not isinstance(value.get("receipt"), Mapping)
    ):
        raise CredentialProvisioningError("credential state is invalid")
    operation = uuid.UUID(str(value["operation_id"]))
    if operation.version != 7 or str(operation) != value["operation_id"]:
        raise CredentialProvisioningError("credential state is invalid")
    expected_stage = f".home-agent-phase3-e5ae-{operation}"
    if value.get("staging_directory") != expected_stage:
        raise CredentialProvisioningError("credential state is invalid")
    _validate_receipt(value["receipt"])
    if (
        value["receipt"]["operation_id"] != value["operation_id"]
        or value["receipt"]["key_source"] != value["with_key"]
    ):
        raise CredentialProvisioningError("credential state is invalid")


def _validate_receipt(value: Mapping[str, Any]) -> None:
    keys = {
        "contract",
        "operation_id",
        "source_commit",
        "release_manifest_digest",
        "migration_tool_bundle_digest",
        "core_oci_manifest_digest",
        "core_schema_digest",
        "core_capability_digest",
        "source_projection_contract_digest",
        "policy_version",
        "policy_digest",
        "shadow_authorization_id",
        "review_key_fingerprint",
        "finalization_key_fingerprint",
        "writer_freeze_key_fingerprint",
        "privacy_probe_key_fingerprint",
        "semantic_cutover_key_fingerprint",
        "commitment_key_fingerprint",
        "commitment_key_epoch",
        "credential_count",
        "status",
        "key_source",
    }
    digest_keys = {
        key
        for key in keys
        if key.endswith("_digest") or key.endswith("_fingerprint")
    }
    fingerprint_keys = {key for key in keys if key.endswith("_fingerprint")}
    if (
        set(value) != keys
        or value.get("contract") != RECEIPT_CONTRACT
        or COMMIT.fullmatch(str(value.get("source_commit", ""))) is None
        or any(HEX64.fullmatch(str(value.get(key, ""))) is None for key in digest_keys)
        or len({value[key] for key in fingerprint_keys}) != len(fingerprint_keys)
        or not isinstance(value.get("policy_version"), str)
        or not 1 <= len(value["policy_version"]) <= 128
        or value.get("commitment_key_epoch") != 1
        or value.get("credential_count") != len(CREDENTIALS)
        or value.get("status") != "provisioned"
        or value.get("key_source") not in ALLOWED_KEY_SOURCES
    ):
        raise CredentialProvisioningError("credential receipt is invalid")
    try:
        operation = uuid.UUID(str(value["operation_id"]))
        shadow = uuid.UUID(str(value["shadow_authorization_id"]))
    except (ValueError, TypeError, AttributeError) as error:
        raise CredentialProvisioningError("credential receipt is invalid") from error
    if (
        operation.version != 7
        or str(operation) != value["operation_id"]
        or shadow.version not in {4, 7}
        or str(shadow) != value["shadow_authorization_id"]
    ):
        raise CredentialProvisioningError("credential receipt is invalid")


def _cleanup_preparing(state: Mapping[str, Any]) -> None:
    paths = _credential_paths()
    if any(path.exists() or path.is_symlink() for path in paths.values()):
        raise CredentialProvisioningError("partial credential publication exists")
    staging = CREDENTIAL_ROOT / str(state["staging_directory"])
    if staging.exists() or staging.is_symlink():
        details = staging.lstat()
        if not stat.S_ISDIR(details.st_mode) or details.st_uid != 0:
            raise CredentialProvisioningError("credential staging is unsafe")
        for name, target in CREDENTIALS:
            candidate = staging / target
            if candidate.exists() or candidate.is_symlink():
                candidate.unlink()
        try:
            staging.rmdir()
        except OSError as error:
            raise CredentialProvisioningError("credential staging is not empty") from error
    STATE_PATH.unlink()


def _resume_publication(state: Mapping[str, Any]) -> Mapping[str, Any]:
    _validate_state(state)
    staging = CREDENTIAL_ROOT / str(state["staging_directory"])
    paths = _credential_paths()
    if state["phase"] == "preparing":
        _cleanup_preparing(state)
        raise CredentialProvisioningError("incomplete credential preparation removed")
    for name, target_name in CREDENTIALS:
        published = paths[name]
        staged = staging / target_name
        candidate = published if published.exists() else staged
        if candidate.is_symlink() or not candidate.exists():
            raise CredentialProvisioningError("credential publication is incomplete")
        details = candidate.lstat()
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_uid != 0
            or details.st_gid != 0
            or stat.S_IMODE(details.st_mode) != 0o600
            or details.st_nlink not in {1, 2}
        ):
            raise CredentialProvisioningError("encrypted credential is unsafe")
        if _sha256(_decrypt(candidate, name)) != state["plaintext_sha256"][name]:
            raise CredentialProvisioningError("encrypted credential verification failed")
        if not published.exists():
            os.link(staged, published)
            os.chown(published, 0, 0)
            os.chmod(published, 0o600)
            directory = os.open(CREDENTIAL_ROOT, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    state = dict(state)
    state["phase"] = "complete"
    _atomic_json(STATE_PATH, state, replace=True)
    if RECEIPT_PATH.exists() or RECEIPT_PATH.is_symlink():
        existing = _json(_safe_file(RECEIPT_PATH))
        if existing != state["receipt"]:
            raise CredentialProvisioningError("credential receipt does not match")
    else:
        _atomic_json(RECEIPT_PATH, state["receipt"], replace=False)
    if staging.exists():
        for _name, target_name in CREDENTIALS:
            candidate = staging / target_name
            if candidate.exists():
                candidate.unlink()
        staging.rmdir()
    return {**state["receipt"], "provisioning_status": "resumed"}


def execute() -> Mapping[str, Any]:
    _validate_roots()
    if STATE_PATH.exists() or STATE_PATH.is_symlink():
        existing_state = _json(_safe_file(STATE_PATH))
        _validate_state(existing_state)
        if existing_state["phase"] == "preparing":
            _cleanup_preparing(existing_state)
        else:
            return _resume_publication(existing_state)
    if RECEIPT_PATH.exists() or RECEIPT_PATH.is_symlink() or any(
        path.exists() or path.is_symlink() for path in _credential_paths().values()
    ):
        raise CredentialProvisioningError("untracked signing credentials exist")

    source = _source_report()
    environment = _environment()
    policy_raw = POLICY_PATH.read_bytes()
    policy = _json(policy_raw)
    policy_version = policy.get("policy_version")
    if (
        not isinstance(policy_version, str)
        or not 1 <= len(policy_version) <= 128
        or _sha256(policy_raw) != environment["HOME_AGENT_POLICY_DIGEST"]
    ):
        raise CredentialProvisioningError("deployment policy digest does not match")
    material = _json(
        _run(
            [
                "docker",
                "exec",
                CORE_CONTAINER,
                "/app/docker-entrypoint.sh",
                "phase3-signing-material",
            ],
            timeout=180,
        )
    )
    bundle = compile_bundle(
        material=material,
        source_commit=str(source["current_commit"]),
        source_pack_digest=str(source["source_pack_digest"]),
        migration_tool_bundle_digest=_installed_bundle_digest(),
        core_oci_manifest_digest=_image_digest(str(source["current_commit"])),
        expected_policy_version=policy_version,
        expected_policy_digest=environment["HOME_AGENT_POLICY_DIGEST"],
        expected_source_projection_digest=_canonical_digest(
            migration.SOURCE_PROJECTION_CONTRACT
        ),
        expected_core_schema_digest=core_schema_digest(CORE_ROOT),
        expected_core_capability_digest=_canonical_digest(
            migration.EXPECTED_CAPABILITY_CONTRACT
        ),
        operation_id=_uuid7(),
    )
    with_key = _key_source()
    _prepare_key_source(with_key)
    receipt = {**bundle.receipt, "key_source": with_key}
    operation = receipt["operation_id"]
    staging_name = f".home-agent-phase3-e5ae-{operation}"
    staging = CREDENTIAL_ROOT / staging_name
    staging.mkdir(mode=0o700)
    os.chown(staging, 0, 0)
    state: dict[str, Any] = {
        "contract": STATE_CONTRACT,
        "operation_id": operation,
        "phase": "preparing",
        "staging_directory": staging_name,
        "with_key": with_key,
        "plaintext_sha256": dict(bundle.plaintext_sha256),
        "receipt": receipt,
    }
    _atomic_json(STATE_PATH, state, replace=False)
    try:
        for name, target_name in CREDENTIALS:
            encrypted = _encrypt(name, bundle.plaintext[name], with_key)
            target = staging / target_name
            _write_staged(target, encrypted)
            if _sha256(_decrypt(target, name)) != bundle.plaintext_sha256[name]:
                raise CredentialProvisioningError(
                    "encrypted credential verification failed"
                )
        directory = os.open(staging, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        state["phase"] = "publishing"
        _atomic_json(STATE_PATH, state, replace=True)
    except Exception:
        # No target is published before the phase becomes ``publishing``.
        # The next invocation can safely discard an incomplete staging set.
        raise
    result = _resume_publication(state)
    return {**result, "provisioning_status": "completed"}


def main() -> int:
    if len(sys.argv) != 1:
        print("identity credential provisioner accepts no arguments", file=sys.stderr)
        return 64
    try:
        result = execute()
    except (CredentialProvisioningError, OSError, ValueError):
        print("identity credential provisioner failed closed", file=sys.stderr)
        return 78
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
