from __future__ import annotations

from datetime import UTC, datetime
import importlib.util
from pathlib import Path
import sys
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[2]
WRITER = (
    ROOT / "stack/home-agent-deploy/operator/off_host_backup_writer.py"
)
SOURCE_PLAN = (
    ROOT
    / "stack/home-agent-deploy/operator/phase3_activation_source_plan.py"
)
RUNNER = ROOT / "tools/run-home-agent-e1-postgres-gate.py"
WORKFLOW = ROOT / ".github/workflows/home-agent-e1-postgres.yml"
DESTINATION_EXAMPLE = (
    ROOT
    / "stack/home-agent-deploy/off-host-backup-destination.e5o.example.json"
)


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "home_agent_off_host_backup_writer_e5o",
        WRITER,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _destination() -> dict[str, str]:
    return {
        "contract": "phase3-off-host-backup-destination-e5o-v1",
        "destination_class": "operator_controlled_off_host",
        "remote": "home-agent-offhost",
        "prefix": "HomeAgent/pgBackRest",
    }


def test_destination_is_exact_and_cannot_redirect_the_writer() -> None:
    module = _module()
    assert module.validate_destination(_destination()) == _destination()
    assert DESTINATION_EXAMPLE.read_text(encoding="utf-8").strip() == (
        '{"contract":"phase3-off-host-backup-destination-e5o-v1",'
        '"destination_class":"operator_controlled_off_host",'
        '"prefix":"HomeAgent/pgBackRest","remote":"home-agent-offhost"}'
    )
    for key, value in (
        ("remote", "attacker"),
        ("prefix", "../escape"),
        ("destination_class", "same_host"),
        ("contract", "future"),
    ):
        changed = _destination()
        changed[key] = value
        with pytest.raises(module.OffHostBackupError):
            module.validate_destination(changed)


def test_receipt_is_compatible_with_the_existing_e5j_preflight() -> None:
    module = _module()
    receipt = module.build_receipt(
        backup_label="20260729-102836F",
        copied_at=datetime(2026, 7, 29, 17, 0, tzinfo=UTC),
    )
    assert receipt == {
        "contract": "phase3-off-host-backup-receipt-e5j-v1",
        "backup_label": "20260729-102836F",
        "copied_at": "2026-07-29T17:00:00Z",
        "destination_class": "operator_controlled_off_host",
        "verification_status": "checksum_verified",
    }


def test_writer_copies_only_the_encrypted_repository_and_verifies_remote_bytes() -> None:
    source = WRITER.read_text(encoding="utf-8")
    assert 'REPOSITORY = DURABLE_ROOT / "pgbackrest-repository"' in source
    assert "runtime.sqlite" not in source.split('"""', 2)[2]
    assert "erasure-ledger" not in source
    assert "postgres/data" not in source
    assert "pgbackrest_repository_guard.py" in source
    assert "fcntl.LOCK_EX" in source
    assert '"--one-file-system"' in source
    assert '"rclone",' in source
    assert '"copyto",' in source
    assert '"cat",' in source
    assert "hashlib.sha256()" in source
    assert "_atomic_write_receipt(receipt)" in source
    assert source.index("upload_and_verify(") < source.index(
        "_atomic_write_receipt(receipt)"
    )


def test_writer_is_root_only_argument_free_and_never_uses_a_shell() -> None:
    source = WRITER.read_text(encoding="utf-8")
    assert "os.geteuid() != 0" in source
    assert "len(sys.argv) != 1" in source
    assert source.count("shell=False") >= 2
    for forbidden in (
        "shell=True",
        "os.system",
        "eval(",
        "HOME_ASSISTANT",
        "postgres_owner_password",
        "repo1-cipher-pass",
    ):
        assert forbidden not in source


def test_source_plan_tracks_the_writer_and_removes_only_its_missing_boundary() -> None:
    source = SOURCE_PLAN.read_text(encoding="utf-8")
    assert "stack/home-agent-deploy/operator/off_host_backup_writer.py" in source
    assert "off_host_backup_writer_installed" in source
    assert "off_host_backup_writer_not_installed" not in source
    assert "activation_migration_executor_not_installed" not in source
    assert "identity_authority_admission_writer_not_installed" not in source
    assert "identity_disposable_role_activation_not_installed" in source


def test_e5o_is_carried_by_the_hosted_gate() -> None:
    runner = RUNNER.read_text(encoding="utf-8")
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "off_host_backup_writer.py" in runner
    assert "test_off_host_backup_writer_e5o.py" in runner
    assert "test_off_host_backup_writer_e5o.py" in workflow
    assert "E5o" in workflow
