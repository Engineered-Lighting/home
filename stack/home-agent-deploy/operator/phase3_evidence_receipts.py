#!/usr/bin/env python3
"""Root-only E5j writers for evidence the operator actually verified.

The restore writer is called only by the guarded isolated restore drill after
its database, schema, shutdown, dump, and page-checksum checks pass. The
erasure writer runs the existing read-only Core restore gate against the live
database and binds that result to the current independent ledger head and the
latest restore receipt.

Neither command changes rollout mode, runs a migration, or grants authority.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
from typing import Any, Mapping, Sequence


SOURCE_REVISION = "0006a_worker_lease_arbitration"
RESTORE_CONTRACT = "phase3-restore-drill-receipt-e5j-v1"
ERASURE_CONTRACT = "phase3-erasure-current-receipt-e5j-v1"
CONFIG_ROOT = Path("/srv/home-agent/config")
ENVIRONMENT_PATH = CONFIG_ROOT / "home-agent.env"
RESTORE_RECEIPT_PATH = CONFIG_ROOT / "phase3-restore-drill-e5j.json"
ERASURE_RECEIPT_PATH = CONFIG_ROOT / "phase3-erasure-current-e5j.json"
LEDGER_HEAD_PATH = Path("/srv/home-agent/erasure-ledger/ledger.head.json")
SOURCE_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_PATH = SOURCE_ROOT / "stack/home-agent-compose.yml"
BACKUP_LABEL = re.compile(r"^[0-9]{8}-[0-9]{6}F$")
SYSTEM_IDENTIFIER = re.compile(r"^[1-9][0-9]{9,19}$")
HEAD_HASH = re.compile(r"^[0-9a-f]{64}$")
MAX_JSON_BYTES = 64 * 1024


class ReceiptError(RuntimeError):
    """Trusted evidence could not be established."""


def _exact_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ReceiptError("evidence JSON contains a duplicate key")
        value[key] = item
    return value


def _decode_json(raw: bytes) -> Any:
    if not raw or len(raw) > MAX_JSON_BYTES or b"\0" in raw:
        raise ReceiptError("evidence JSON has an invalid size")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_exact_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReceiptError("evidence JSON is invalid") from error


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ReceiptError("receipt time must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def build_restore_receipt(
    *,
    backup_label: str,
    schema_revision: str,
    database_system_identifier: str,
    completed_at: datetime,
) -> dict[str, Any]:
    if BACKUP_LABEL.fullmatch(backup_label) is None:
        raise ReceiptError("backup label is invalid")
    if schema_revision != SOURCE_REVISION:
        raise ReceiptError("restore schema revision is not the activation source")
    if SYSTEM_IDENTIFIER.fullmatch(database_system_identifier) is None:
        raise ReceiptError("database system identifier is invalid")
    return {
        "contract": RESTORE_CONTRACT,
        "backup_label": backup_label,
        "schema_revision": schema_revision,
        "database_system_identifier": database_system_identifier,
        "completed_at": _utc_text(completed_at),
        "restore_status": "passed",
    }


def parse_ledger_head(value: Any) -> dict[str, Any]:
    if (
        not isinstance(value, Mapping)
        or set(value) != {"version", "epoch", "head_hash"}
        or value.get("version") != 1
        or isinstance(value.get("epoch"), bool)
        or not isinstance(value.get("epoch"), int)
        or value["epoch"] < 0
        or not isinstance(value.get("head_hash"), str)
        or HEAD_HASH.fullmatch(value["head_hash"]) is None
        or (value["epoch"] == 0 and value["head_hash"] != "0" * 64)
    ):
        raise ReceiptError("independent erasure ledger head is invalid")
    return dict(value)


def validate_restore_receipt(value: Any) -> dict[str, Any]:
    if (
        not isinstance(value, Mapping)
        or set(value)
        != {
            "contract",
            "backup_label",
            "schema_revision",
            "database_system_identifier",
            "completed_at",
            "restore_status",
        }
        or value.get("contract") != RESTORE_CONTRACT
        or not isinstance(value.get("backup_label"), str)
        or BACKUP_LABEL.fullmatch(value["backup_label"]) is None
        or value.get("schema_revision") != SOURCE_REVISION
        or not isinstance(value.get("database_system_identifier"), str)
        or SYSTEM_IDENTIFIER.fullmatch(value["database_system_identifier"]) is None
        or not isinstance(value.get("completed_at"), str)
        or value.get("restore_status") != "passed"
    ):
        raise ReceiptError("restore receipt is invalid")
    try:
        timestamp = datetime.fromisoformat(
            value["completed_at"].replace("Z", "+00:00")
        )
    except ValueError as error:
        raise ReceiptError("restore receipt time is invalid") from error
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ReceiptError("restore receipt time is invalid")
    return dict(value)


def build_erasure_current_receipt(
    *,
    restore_receipt: Any,
    verification: Any,
    ledger_head: Any,
    verified_at: datetime,
) -> dict[str, Any]:
    restore = validate_restore_receipt(restore_receipt)
    head = parse_ledger_head(ledger_head)
    if (
        not isinstance(verification, Mapping)
        or set(verification)
        != {"status", "current", "ledger_epoch", "database_epoch"}
        or verification.get("status") != "current"
        or verification.get("current") is not True
        or isinstance(verification.get("ledger_epoch"), bool)
        or not isinstance(verification.get("ledger_epoch"), int)
        or isinstance(verification.get("database_epoch"), bool)
        or not isinstance(verification.get("database_epoch"), int)
        or verification["ledger_epoch"] != verification["database_epoch"]
        or verification["ledger_epoch"] != head["epoch"]
    ):
        raise ReceiptError("database erasure gate is not current")
    return {
        "contract": ERASURE_CONTRACT,
        "backup_label": restore["backup_label"],
        "schema_revision": restore["schema_revision"],
        "ledger_epoch": head["epoch"],
        "ledger_head_hash": head["head_hash"],
        "verified_at": _utc_text(verified_at),
        "gate_status": "current",
    }


def _safe_evidence_file(
    path: Path,
    *,
    expected_uid: int,
    expected_gid: int,
) -> Any:
    try:
        details = path.lstat()
    except FileNotFoundError as error:
        raise ReceiptError("required operator evidence is missing") from error
    if (
        not stat.S_ISREG(details.st_mode)
        or details.st_uid != expected_uid
        or details.st_gid != expected_gid
        or stat.S_IMODE(details.st_mode) != 0o600
        or details.st_nlink != 1
    ):
        raise ReceiptError("operator evidence has unsafe ownership or mode")
    return _decode_json(path.read_bytes())


def _require_root_linux() -> None:
    if sys.platform != "linux" or os.geteuid() != 0:
        raise ReceiptError("receipt ceremony requires the root operator on Linux")


def _require_safe_parent(path: Path) -> None:
    if path.parent != CONFIG_ROOT:
        raise ReceiptError("receipt target is outside the reviewed config directory")
    try:
        details = CONFIG_ROOT.lstat()
    except FileNotFoundError as error:
        raise ReceiptError("reviewed config directory is missing") from error
    if (
        not stat.S_ISDIR(details.st_mode)
        or details.st_uid != 0
        or details.st_gid != 0
        or stat.S_IMODE(details.st_mode) & 0o022
    ):
        raise ReceiptError("reviewed config directory has unsafe ownership or mode")
    try:
        existing = path.lstat()
    except FileNotFoundError:
        return
    if (
        not stat.S_ISREG(existing.st_mode)
        or existing.st_uid != 0
        or existing.st_gid != 0
        or stat.S_IMODE(existing.st_mode) != 0o600
        or existing.st_nlink != 1
    ):
        raise ReceiptError("existing receipt has unsafe ownership or mode")


def atomic_write_root_receipt(path: Path, value: Mapping[str, Any]) -> None:
    _require_safe_parent(path)
    raw = (
        json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")
        + b"\n"
    )
    if len(raw) > MAX_JSON_BYTES:
        raise ReceiptError("receipt is oversized")
    temporary = path.with_name(f".{path.name}.new.{secrets.token_hex(12)}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = -1
    try:
        descriptor = os.open(temporary, flags, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.chown(temporary, 0, 0)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        directory_descriptor = os.open(CONFIG_ROOT, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _run(command: Sequence[str], *, timeout: int = 120) -> bytes:
    try:
        result = subprocess.run(
            list(command),
            check=True,
            capture_output=True,
            timeout=timeout,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ReceiptError("erasure-current verification command failed") from error
    return result.stdout


def _verify_erasure_current() -> Any:
    if not COMPOSE_PATH.is_file() or COMPOSE_PATH.is_symlink():
        raise ReceiptError("reviewed Compose file is unavailable")
    return _decode_json(
        _run(
            [
                "docker",
                "compose",
                "--project-name",
                "home-agent",
                "--env-file",
                str(ENVIRONMENT_PATH),
                "--file",
                str(COMPOSE_PATH),
                "--profile",
                "operator",
                "run",
                "--rm",
                "--no-deps",
                "--pull",
                "never",
                "restore-replay",
                "restore-verify",
            ]
        )
    )


def write_restore(args: argparse.Namespace) -> None:
    receipt = build_restore_receipt(
        backup_label=args.backup_label,
        schema_revision=args.schema_revision,
        database_system_identifier=args.database_system_identifier,
        completed_at=datetime.now(UTC),
    )
    atomic_write_root_receipt(RESTORE_RECEIPT_PATH, receipt)


def write_erasure_current() -> None:
    restore_receipt = _safe_evidence_file(
        RESTORE_RECEIPT_PATH,
        expected_uid=0,
        expected_gid=0,
    )
    ledger_head = _safe_evidence_file(
        LEDGER_HEAD_PATH,
        expected_uid=10001,
        expected_gid=10001,
    )
    verification = _verify_erasure_current()
    receipt = build_erasure_current_receipt(
        restore_receipt=restore_receipt,
        verification=verification,
        ledger_head=ledger_head,
        verified_at=datetime.now(UTC),
    )
    atomic_write_root_receipt(ERASURE_RECEIPT_PATH, receipt)


def main() -> int:
    parser = argparse.ArgumentParser(prog="phase3-evidence-receipts")
    subparsers = parser.add_subparsers(dest="command", required=True)
    restore = subparsers.add_parser("restore")
    restore.add_argument("--backup-label", required=True)
    restore.add_argument("--schema-revision", required=True)
    restore.add_argument("--database-system-identifier", required=True)
    subparsers.add_parser("erasure-current")
    args = parser.parse_args()
    try:
        _require_root_linux()
        if args.command == "restore":
            write_restore(args)
        else:
            write_erasure_current()
    except ReceiptError:
        print("phase3 evidence receipt could not establish trusted state", file=sys.stderr)
        return 78
    print(
        json.dumps(
            {
                "contract": "phase3-evidence-receipt-writer-e5j-v1",
                "evidence": args.command,
                "status": "written",
                "authoritative": False,
                "enables_writes": False,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
