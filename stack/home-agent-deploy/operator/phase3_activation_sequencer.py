#!/usr/bin/env python3
"""Root-controlled E5m source admission and grant-permit sequencer.

This installs no migration execution path.  It admits only the exact
hosted-tested source pack and can arm the grant service only after the existing
E5j preflight reports no blockers.  Merely writing the permit changes no
database, service, rollout, or Home Assistant state.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
from typing import Any, Mapping, Sequence


CONTRACT = "phase3-activation-sequencer-e5m-v1"
SOURCE_RECEIPT_CONTRACT = "phase3-source-acceptance-e5j-v1"
GRANT_PERMIT_CONTRACT = "phase3-grant-permit-e5m-v1"
TARGET_REVISION = "0021_parent_status_e5h"
WEB_BOUNDARY_RUN_ID = "30387665230"
CONFIG_ROOT = Path("/srv/home-agent/config")
ENVIRONMENT_PATH = CONFIG_ROOT / "home-agent.env"
SOURCE_RECEIPT_PATH = CONFIG_ROOT / "phase3-source-acceptance-e5j.json"
GRANT_PERMIT_PATH = CONFIG_ROOT / "phase3-grant-permit-e5m.txt"
SOURCE_ROOT = Path(__file__).resolve().parents[3]
SOURCE_PLAN = Path(__file__).with_name("phase3_activation_source_plan.py")
PREFLIGHT = Path(__file__).with_name("phase3_activation_preflight.py")
APPLY_GRANTS = SOURCE_ROOT / "stack/home-agent-deploy/apply-grants.sh"
COMPOSE = SOURCE_ROOT / "stack/home-agent-compose.yml"
COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
RUN_ID = re.compile(r"^[1-9][0-9]{5,19}$")
MAX_JSON_BYTES = 64 * 1024
GRANT_PERMIT_VALUE = (
    f"{GRANT_PERMIT_CONTRACT}:"
    "0017_authenticated_binding_e5c:0021_parent_status_e5h"
)
ACTIVATION_SEQUENCE = (
    "validate_e5j_preflight",
    "stop_core_roles",
    "backup_gate",
    "migrate_0013_finalizer",
    "provision_e4_roles",
    "migrate_0015_current_authority",
    "provision_binding_kernel",
    "run_identity_finalizer",
    "freeze_legacy_semantic_writers",
    "run_semantic_cutover",
    "migrate_0017_authenticated_binding",
    "apply_permitted_grants",
    "start_revision_0017_private_core",
    "confirm_authenticated_principal_binding",
    "stop_revision_0017_private_core",
    "migrate_0018_parent_authority",
    "provision_parent_relationship_kernel",
    "migrate_0021_parent_status",
    "apply_permitted_grants",
    "start_revision_0021_private_core",
    "verify_health_and_receipts",
)


class SequencerError(RuntimeError):
    """The activation boundary could not establish trusted state."""


def _require_root_linux() -> None:
    if sys.platform != "linux" or os.geteuid() != 0:
        raise SequencerError("activation sequencing requires root on Linux")


def _run_json(command: Sequence[str], *, timeout: int = 120) -> Mapping[str, Any]:
    try:
        result = subprocess.run(
            list(command),
            cwd=SOURCE_ROOT,
            check=False,
            capture_output=True,
            text=False,
            timeout=timeout,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise SequencerError("activation evidence command failed") from error
    if result.returncode not in {0, 3} or not result.stdout:
        raise SequencerError("activation evidence command failed")
    if len(result.stdout) > MAX_JSON_BYTES or b"\0" in result.stdout:
        raise SequencerError("activation evidence output is invalid")
    try:
        value = json.loads(result.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SequencerError("activation evidence output is invalid") from error
    if not isinstance(value, Mapping):
        raise SequencerError("activation evidence output is invalid")
    return value


def source_plan() -> Mapping[str, Any]:
    return _run_json([sys.executable, str(SOURCE_PLAN)])


def preflight() -> Mapping[str, Any]:
    return _run_json([sys.executable, str(PREFLIGHT)])


def _safe_config_root() -> None:
    try:
        details = CONFIG_ROOT.lstat()
    except FileNotFoundError as error:
        raise SequencerError("activation config directory is missing") from error
    if (
        not stat.S_ISDIR(details.st_mode)
        or details.st_uid != 0
        or details.st_gid != 0
        or stat.S_IMODE(details.st_mode) & 0o022
    ):
        raise SequencerError("activation config directory is unsafe")


def _validate_existing_target(path: Path) -> None:
    if path.parent != CONFIG_ROOT:
        raise SequencerError("activation target is outside the config directory")
    try:
        details = path.lstat()
    except FileNotFoundError:
        return
    if (
        not stat.S_ISREG(details.st_mode)
        or details.st_uid != 0
        or details.st_gid != 0
        or stat.S_IMODE(details.st_mode) != 0o600
        or details.st_nlink != 1
    ):
        raise SequencerError("existing activation artifact is unsafe")


def _atomic_write(path: Path, raw: bytes) -> None:
    _safe_config_root()
    _validate_existing_target(path)
    if not raw or len(raw) > MAX_JSON_BYTES or b"\0" in raw:
        raise SequencerError("activation artifact is invalid")
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
        directory = os.open(CONFIG_ROOT, os.O_RDONLY)
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


def _protected_text(path: Path) -> str | None:
    try:
        details = path.lstat()
    except FileNotFoundError:
        return None
    if (
        not stat.S_ISREG(details.st_mode)
        or details.st_uid != 0
        or details.st_gid != 0
        or stat.S_IMODE(details.st_mode) != 0o600
        or details.st_nlink != 1
    ):
        raise SequencerError("activation artifact is unsafe")
    raw = path.read_bytes()
    if not raw or len(raw) > MAX_JSON_BYTES or b"\0" in raw:
        raise SequencerError("activation artifact is invalid")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SequencerError("activation artifact is invalid") from error


def grant_contract_installed() -> bool:
    try:
        grants = APPLY_GRANTS.read_text(encoding="utf-8")
        compose = COMPOSE.read_text(encoding="utf-8")
    except OSError:
        return False
    required_grants = (
        'HOME_AGENT_PHASE3_GRANT_PERMIT_FILE',
        '"/run/phase3-activation/permit"',
        GRANT_PERMIT_VALUE,
        "phase3 grant activation permit is not mounted",
    )
    required_compose = (
        "grant-phase3-activation:",
        "phase3_grant_permit_phase3_activation",
        "HOME_AGENT_PHASE3_GRANT_PERMIT_FILE: /run/phase3-activation/permit",
    )
    return all(value in grants for value in required_grants) and all(
        value in compose for value in required_compose
    )


def build_source_receipt(plan: Mapping[str, Any]) -> dict[str, str]:
    commit = plan.get("current_commit")
    run_id = plan.get("accepted_postgres_run_id")
    if (
        plan.get("source_pack_matches_hosted_acceptance") is not True
        or plan.get("fixed_migration_entrypoints_installed") is not True
        or not isinstance(commit, str)
        or COMMIT_SHA.fullmatch(commit) is None
        or not isinstance(run_id, str)
        or RUN_ID.fullmatch(run_id) is None
        or not grant_contract_installed()
    ):
        raise SequencerError("hosted activation source is not admitted")
    return {
        "contract": SOURCE_RECEIPT_CONTRACT,
        "target_revision": TARGET_REVISION,
        "source_commit": commit,
        "postgres_authority_run_id": run_id,
        "postgres_authority_status": "success",
        "web_boundary_run_id": WEB_BOUNDARY_RUN_ID,
        "web_boundary_status": "success",
        "activation_contract_status": "installed",
    }


def admit_source() -> None:
    receipt = build_source_receipt(source_plan())
    raw = (
        json.dumps(receipt, separators=(",", ":"), sort_keys=True).encode("utf-8")
        + b"\n"
    )
    _atomic_write(SOURCE_RECEIPT_PATH, raw)


def _permit_is_armed() -> bool:
    value = _protected_text(GRANT_PERMIT_PATH)
    return value == GRANT_PERMIT_VALUE + "\n"


def arm_grants() -> None:
    report = preflight()
    if (
        report.get("contract") != "phase3-activation-preflight-e5j-v1"
        or report.get("preflight_passed") is not True
        or report.get("blockers") != []
        or report.get("authoritative") is not False
        or report.get("enables_writes") is not False
        or report.get("runs_migrations") is not False
        or report.get("changes_rollout_mode") is not False
    ):
        raise SequencerError("phase3 evidence preflight is not complete")
    _atomic_write(GRANT_PERMIT_PATH, (GRANT_PERMIT_VALUE + "\n").encode("ascii"))


def disarm_grants() -> None:
    _safe_config_root()
    value = _protected_text(GRANT_PERMIT_PATH)
    if value is None:
        return
    if value != GRANT_PERMIT_VALUE + "\n":
        raise SequencerError("grant permit is not the reviewed artifact")
    GRANT_PERMIT_PATH.unlink()
    directory = os.open(CONFIG_ROOT, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def status() -> dict[str, Any]:
    plan = source_plan()
    report = preflight()
    source_admitted = report.get("source_acceptance") == "valid"
    return {
        "contract": CONTRACT,
        "source_pack_matches_hosted_acceptance": (
            plan.get("source_pack_matches_hosted_acceptance") is True
        ),
        "fixed_migration_entrypoints_installed": (
            plan.get("fixed_migration_entrypoints_installed") is True
        ),
        "grant_activation_contract_installed": grant_contract_installed(),
        "source_admitted": source_admitted,
        "grant_permit_armed": _permit_is_armed(),
        "preflight_passed": report.get("preflight_passed") is True,
        "preflight_blockers": list(report.get("blockers", [])),
        "activation_sequence": list(ACTIVATION_SEQUENCE),
        "migration_executor_enabled": False,
        "authoritative": False,
        "enables_writes": False,
        "runs_migrations": False,
        "changes_rollout_mode": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="phase3-activation-sequencer")
    parser.add_argument(
        "command",
        choices=("status", "admit-source", "arm-grants", "disarm-grants"),
    )
    args = parser.parse_args()
    try:
        _require_root_linux()
        if args.command == "admit-source":
            admit_source()
        elif args.command == "arm-grants":
            arm_grants()
        elif args.command == "disarm-grants":
            disarm_grants()
        result = status()
    except SequencerError:
        print(
            "phase3 activation sequencer could not establish trusted state",
            file=sys.stderr,
        )
        return 78
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    if args.command == "status":
        return 0 if result["preflight_passed"] else 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
