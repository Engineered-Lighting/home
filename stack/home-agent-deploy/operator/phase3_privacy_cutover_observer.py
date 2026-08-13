#!/usr/bin/env python3
"""Produce the content-free live observation consumed by the privacy ceremony.

This observer does not decide privacy policy and does not sign evidence. It
binds four independently controlled facts: the hosted-tested source pack, the
record-only live deployment configuration, the signed legacy-writer freeze,
and the last Core privacy policy actually accepted and purged by HA Edge.

The Edge receipt must precede the stopped-HA writer observation by no more than
five minutes. This closes the otherwise dangerous interval in which a stale
receipt could be presented as proof of the policy active at cutover.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import sys
from typing import Any, Mapping
import uuid

import phase3_activation_source_plan as source_plan
from phase3_activation_preflight import selected_environment
from phase3_privacy_cutover_evidence import CATEGORY_ASSERTIONS, CATEGORY_ORDER
from reviewed_identity_payload import (
    VerificationError,
    canonical_bytes,
    parse_canonical_json,
)


CONTRACT = "phase3-privacy-cutover-observation-e5aa-v1"
EDGE_RECEIPT_CONTRACT = "home-agent-edge-privacy-policy-receipt-v1"
WRITER_PACKET_CONTRACT = "phase3-writer-freeze-evidence-packet-e5z-v1"
WRITER_RECEIPT_CONTRACT = "phase3-writer-freeze-evidence-receipt-e5z-v1"
PRIVATE_ROOT = Path("/srv/home-agent/private/phase3-identity")
ENVIRONMENT_PATH = Path("/srv/home-agent/config/home-agent.env")
WRITER_EVIDENCE_PATH = PRIVATE_ROOT / "writer-freeze-evidence-e5z.json"
WRITER_RECEIPT_PATH = PRIVATE_ROOT / "writer-freeze-evidence-receipt-e5z.json"
EDGE_RECEIPT_PATH = PRIVATE_ROOT / "edge-privacy-policy-receipt-e5ac.json"
OBSERVATION_PATH = PRIVATE_ROOT / "privacy-cutover-observation-e5aa.json"
MAX_BYTES = 4 * 1024 * 1024
MAX_EDGE_TO_FREEZE_AGE = timedelta(minutes=5)
MAX_CLOCK_SKEW = timedelta(seconds=30)
EDGE_KEYS = frozenset(
    {
        "contract",
        "policy_digest",
        "blocked_entity_count",
        "blocked_user_count",
        "purged_payload_count",
        "purge_result",
        "refreshed_at",
    }
)
WRITER_RECEIPT_KEYS = frozenset(
    {
        "contract",
        "status",
        "run_id",
        "evidence_id",
        "freeze_id",
        "evidence_commitment",
        "freeze_commitment",
        "evidence_packet_sha256",
    }
)


class PrivacyCutoverObserverError(RuntimeError):
    """The live privacy observation could not be established."""


def _exact(value: Any, keys: frozenset[str], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise PrivacyCutoverObserverError(f"{label} shape is invalid")
    return dict(value)


def _hex64(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise PrivacyCutoverObserverError(f"{label} digest is invalid")
    return value


def _uuid7(value: Any, label: str) -> str:
    try:
        parsed = uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError) as error:
        raise PrivacyCutoverObserverError(f"{label} identifier is invalid") from error
    if parsed.version != 7 or str(parsed) != value:
        raise PrivacyCutoverObserverError(f"{label} identifier is invalid")
    return str(parsed)


def _time(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise PrivacyCutoverObserverError(f"{label} timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise PrivacyCutoverObserverError(f"{label} timestamp is invalid") from error
    if (
        parsed.tzinfo is None
        or parsed.astimezone(UTC) != parsed
        or parsed.strftime("%Y-%m-%dT%H:%M:%S.%fZ") != value
    ):
        raise PrivacyCutoverObserverError(f"{label} timestamp is noncanonical")
    return parsed


def _canonical_document(raw: bytes, label: str) -> Mapping[str, Any]:
    content = raw[:-1] if raw.endswith(b"\n") else raw
    if not content or len(content) > MAX_BYTES:
        raise PrivacyCutoverObserverError(f"{label} is invalid")
    try:
        value = parse_canonical_json(content, maximum=MAX_BYTES)
    except VerificationError as error:
        raise PrivacyCutoverObserverError(f"{label} is invalid") from error
    if not isinstance(value, Mapping):
        raise PrivacyCutoverObserverError(f"{label} is invalid")
    return value


def _trusted_source(report: Mapping[str, Any]) -> tuple[str, str]:
    required_true = (
        "source_pack_matches_hosted_acceptance",
        "source_pin_bootstrap_installed",
        "identity_privacy_cutover_evidence_writer_installed",
        "identity_privacy_cutover_observer_installed",
        "identity_semantic_cutover_packet_compiler_installed",
        "activation_executor_installed",
    )
    if (
        report.get("contract") != source_plan.CONTRACT
        or report.get("source_revision") != source_plan.SOURCE_REVISION
        or report.get("target_revision") != source_plan.TARGET_REVISION
        or report.get("blockers") != []
        or report.get("source_acceptance_receipt_issuable") is not True
        or any(report.get(name) is not True for name in required_true)
        or report.get("authoritative") is not False
        or report.get("enables_writes") is not False
    ):
        raise PrivacyCutoverObserverError("hosted-tested source pack is unavailable")
    current_commit = report.get("current_commit")
    if (
        not isinstance(current_commit, str)
        or source_plan.COMMIT_SHA.fullmatch(current_commit) is None
    ):
        raise PrivacyCutoverObserverError("hosted-tested source commit is invalid")
    return (_hex64(report.get("source_pack_digest"), "source pack"), current_commit)


def compile_observation(
    source_report: Mapping[str, Any],
    environment_text: str,
    raw_writer_evidence: bytes,
    raw_writer_receipt: bytes,
    raw_edge_receipt: bytes,
    *,
    now: datetime,
) -> bytes:
    """Validate live inputs and return the exact unsigned observation."""

    if now.tzinfo is None or now.astimezone(UTC) != now:
        raise PrivacyCutoverObserverError("observer clock is invalid")
    source_pack_digest, current_commit = _trusted_source(source_report)
    environment = selected_environment(environment_text)
    if environment != {
        "HOME_AGENT_EXPECTED_DB_REVISION": source_plan.SOURCE_REVISION,
        "HOME_AGENT_ROLLOUT_MODE": "record_only",
        "HOME_AGENT_BACKUP_TOPOLOGY": "local",
    }:
        raise PrivacyCutoverObserverError("live deployment is not record-only")

    writer_raw = raw_writer_evidence.rstrip(b"\n")
    writer = _canonical_document(raw_writer_evidence, "writer-freeze evidence")
    if (
        writer.get("contract") != WRITER_PACKET_CONTRACT
        or writer.get("authoritative") is not False
    ):
        raise PrivacyCutoverObserverError("writer-freeze evidence is unsupported")
    run_id = _uuid7(writer.get("run_id"), "writer run")
    finalization_id = _uuid7(writer.get("finalization_id"), "writer finalization")
    freeze = writer.get("enforced_writer_freeze")
    if not isinstance(freeze, Mapping):
        raise PrivacyCutoverObserverError("writer-freeze evidence is incomplete")
    freeze_id = _uuid7(freeze.get("freeze_id"), "writer freeze")
    freeze_time = _time(freeze.get("verified_at"), "writer freeze")
    if (
        freeze.get("enforcement_status") != "enforced_offline"
        or freeze.get("semantic_write_status") != "frozen"
        or freeze.get("legacy_context_cutoff_status") != "enforced_cutoff"
        or freeze.get("recognition_mode") != "nonauthoritative_only"
        or freeze.get("write_probe_result") != "blocked"
    ):
        raise PrivacyCutoverObserverError("legacy writer is not frozen")
    policy_digest = _hex64(freeze.get("policy_digest"), "identity policy")
    release_manifest_digest = _hex64(
        freeze.get("release_manifest_digest"), "release manifest"
    )

    writer_receipt = _exact(
        _canonical_document(raw_writer_receipt, "writer-freeze receipt"),
        WRITER_RECEIPT_KEYS,
        "writer-freeze receipt",
    )
    if (
        writer_receipt["contract"] != WRITER_RECEIPT_CONTRACT
        or writer_receipt["status"] != "verified"
        or writer_receipt["run_id"] != run_id
        or writer_receipt["freeze_id"] != freeze_id
        or writer_receipt["evidence_packet_sha256"]
        != hashlib.sha256(writer_raw).hexdigest()
        or writer_receipt["evidence_commitment"]
        != writer.get("writer_evidence", {}).get("evidence_commitment")
        or writer_receipt["freeze_commitment"] != freeze.get("freeze_commitment")
    ):
        raise PrivacyCutoverObserverError("writer-freeze receipt lineage is invalid")
    for key in ("evidence_commitment", "freeze_commitment"):
        _hex64(writer_receipt[key], key)
    _uuid7(writer_receipt["evidence_id"], "writer evidence")

    edge = _exact(
        _canonical_document(raw_edge_receipt, "HA Edge privacy receipt"),
        EDGE_KEYS,
        "HA Edge privacy receipt",
    )
    if edge["contract"] != EDGE_RECEIPT_CONTRACT:
        raise PrivacyCutoverObserverError("HA Edge privacy receipt is unsupported")
    _hex64(edge["policy_digest"], "HA Edge policy")
    for key in (
        "blocked_entity_count",
        "blocked_user_count",
        "purged_payload_count",
    ):
        if type(edge[key]) is not int or edge[key] < 0:
            raise PrivacyCutoverObserverError("HA Edge privacy counts are invalid")
    if edge["purge_result"] not in {
        "purged_before_accept",
        "unchanged_verified_policy",
    }:
        raise PrivacyCutoverObserverError("HA Edge purge was not verified")
    edge_time = _time(edge["refreshed_at"], "HA Edge privacy refresh")
    if (
        edge_time > freeze_time + MAX_CLOCK_SKEW
        or freeze_time - edge_time > MAX_EDGE_TO_FREEZE_AGE
        or freeze_time > now + MAX_CLOCK_SKEW
        or now - freeze_time > MAX_EDGE_TO_FREEZE_AGE
    ):
        raise PrivacyCutoverObserverError("privacy cutover evidence is stale")

    basis = {
        "source_pack_digest": source_pack_digest,
        "current_commit": current_commit,
        "environment": environment,
        "writer_evidence_sha256": hashlib.sha256(writer_raw).hexdigest(),
        "edge_receipt_sha256": hashlib.sha256(
            raw_edge_receipt.rstrip(b"\n")
        ).hexdigest(),
        "edge_policy_digest": edge["policy_digest"],
        "edge_purge_result": edge["purge_result"],
        "edge_purged_payload_count": edge["purged_payload_count"],
    }
    probe_bundle_digest = hashlib.sha256(canonical_bytes(basis)).hexdigest()
    categories = []
    for category in CATEGORY_ORDER:
        assertions = {name: True for name in CATEGORY_ASSERTIONS[category]}
        categories.append(
            {
                "check_category": category,
                "check_result": "passed",
                "residual_code": "none",
                "assertions": assertions,
                "probe_digest": hashlib.sha256(
                    canonical_bytes(
                        {
                            "category": category,
                            "assertions": assertions,
                            "probe_bundle_digest": probe_bundle_digest,
                        }
                    )
                ).hexdigest(),
            }
        )
    return canonical_bytes(
        {
            "contract": CONTRACT,
            "run_id": run_id,
            "finalization_id": finalization_id,
            "freeze_id": freeze_id,
            "policy_digest": policy_digest,
            "release_manifest_digest": release_manifest_digest,
            "probe_bundle_digest": probe_bundle_digest,
            "checked_at": now.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "categories": categories,
        }
    )


def _read_private(path: Path, maximum: int = MAX_BYTES) -> bytes:
    try:
        details = path.lstat()
        raw = path.read_bytes()
    except OSError as error:
        raise PrivacyCutoverObserverError(
            "privacy observation input is unavailable"
        ) from error
    if (
        not stat.S_ISREG(details.st_mode)
        or details.st_uid != 0
        or details.st_gid != 0
        or details.st_nlink != 1
        or stat.S_IMODE(details.st_mode) not in {0o400, 0o440, 0o600}
        or not raw
        or len(raw) > maximum
    ):
        raise PrivacyCutoverObserverError("privacy observation input is unsafe")
    return raw


def _safe_root() -> None:
    try:
        details = PRIVATE_ROOT.lstat()
    except OSError as error:
        raise PrivacyCutoverObserverError(
            "privacy private root is unavailable"
        ) from error
    if (
        not stat.S_ISDIR(details.st_mode)
        or details.st_uid != 0
        or details.st_gid != 0
        or stat.S_IMODE(details.st_mode) != 0o700
    ):
        raise PrivacyCutoverObserverError("privacy private root is unsafe")


def _atomic_create(path: Path, raw: bytes) -> None:
    if path.exists() or path.is_symlink():
        raise PrivacyCutoverObserverError("privacy observation already exists")
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
            stream.write(raw + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chown(temporary, 0, 0)
        os.chmod(temporary, 0o600)
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


def execute() -> Mapping[str, Any]:
    if sys.platform != "linux" or os.geteuid() != 0:
        raise PrivacyCutoverObserverError("privacy observation requires root on Linux")
    _safe_root()
    try:
        environment_text = _read_private(ENVIRONMENT_PATH, 256 * 1024).decode("utf-8")
    except UnicodeDecodeError as error:
        raise PrivacyCutoverObserverError(
            "deployment environment is invalid"
        ) from error
    raw = compile_observation(
        source_plan.live_report(),
        environment_text,
        _read_private(WRITER_EVIDENCE_PATH),
        _read_private(WRITER_RECEIPT_PATH),
        _read_private(EDGE_RECEIPT_PATH),
        now=datetime.now(UTC),
    )
    _atomic_create(OBSERVATION_PATH, raw)
    value = _canonical_document(raw, "privacy observation")
    return {
        "contract": "phase3-privacy-cutover-observer-receipt-e5ac-v1",
        "status": "observed",
        "run_id": value["run_id"],
        "freeze_id": value["freeze_id"],
        "privacy_observation_sha256": hashlib.sha256(raw).hexdigest(),
        "privacy_check_count": len(value["categories"]),
    }


def main() -> int:
    if len(sys.argv) != 1:
        print("privacy-cutover observer accepts no arguments", file=sys.stderr)
        return 64
    try:
        result = execute()
    except PrivacyCutoverObserverError:
        print("privacy-cutover observer failed closed", file=sys.stderr)
        return 78
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
