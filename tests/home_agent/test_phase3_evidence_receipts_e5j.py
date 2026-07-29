from __future__ import annotations

from datetime import UTC, datetime
import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[2]
WRITER = ROOT / "stack/home-agent-deploy/operator/phase3_evidence_receipts.py"
DRILL = ROOT / "stack/home-agent-deploy/operator/isolated_restore_drill.sh"
PREFLIGHT = ROOT / "stack/home-agent-deploy/operator/phase3_activation_preflight.py"
NOW = datetime(2026, 7, 28, 20, 0, tzinfo=UTC)


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "home_agent_phase3_evidence_receipts_e5j",
        WRITER,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _restore(module) -> dict[str, object]:
    return module.build_restore_receipt(
        backup_label="20260728-102702F",
        schema_revision="0006a_worker_lease_arbitration",
        database_system_identifier="7522608291310326247",
        completed_at=NOW,
    )


def _head() -> dict[str, object]:
    return {"version": 1, "epoch": 7, "head_hash": "b" * 64}


def _verification() -> dict[str, object]:
    return {
        "status": "current",
        "current": True,
        "ledger_epoch": 7,
        "database_epoch": 7,
    }


def test_restore_receipt_can_only_report_the_guarded_drill_contract() -> None:
    module = _module()

    assert _restore(module) == {
        "contract": "phase3-restore-drill-receipt-e5j-v1",
        "backup_label": "20260728-102702F",
        "schema_revision": "0006a_worker_lease_arbitration",
        "database_system_identifier": "7522608291310326247",
        "completed_at": "2026-07-28T20:00:00Z",
        "restore_status": "passed",
    }
    for values in (
        {
            "backup_label": "latest",
            "schema_revision": "0006a_worker_lease_arbitration",
            "database_system_identifier": "7522608291310326247",
        },
        {
            "backup_label": "20260728-102702F",
            "schema_revision": "0021_parent_status_e5h",
            "database_system_identifier": "7522608291310326247",
        },
        {
            "backup_label": "20260728-102702F",
            "schema_revision": "0006a_worker_lease_arbitration",
            "database_system_identifier": "not-a-system-id",
        },
    ):
        with pytest.raises(module.ReceiptError):
            module.build_restore_receipt(completed_at=NOW, **values)


def test_erasure_current_receipt_requires_exact_live_gate_and_ledger_head() -> None:
    module = _module()
    receipt = module.build_erasure_current_receipt(
        restore_receipt=_restore(module),
        verification=_verification(),
        ledger_head=_head(),
        verified_at=NOW,
    )

    assert receipt == {
        "contract": "phase3-erasure-current-receipt-e5j-v1",
        "backup_label": "20260728-102702F",
        "schema_revision": "0006a_worker_lease_arbitration",
        "ledger_epoch": 7,
        "ledger_head_hash": "b" * 64,
        "verified_at": "2026-07-28T20:00:00Z",
        "gate_status": "current",
    }

    invalid = [
        ({**_verification(), "current": False}, _head()),
        ({**_verification(), "status": "erasure_replay_required"}, _head()),
        ({**_verification(), "database_epoch": 6}, _head()),
        ({**_verification(), "ledger_epoch": 6}, _head()),
        ({**_verification(), "unexpected": "field"}, _head()),
        (_verification(), {**_head(), "head_hash": "c" * 63}),
        (_verification(), {**_head(), "epoch": 8}),
    ]
    for verification, head in invalid:
        with pytest.raises(module.ReceiptError):
            module.build_erasure_current_receipt(
                restore_receipt=_restore(module),
                verification=verification,
                ledger_head=head,
                verified_at=NOW,
            )


def test_writer_has_fixed_root_owned_atomic_receipt_boundaries() -> None:
    source = WRITER.read_text(encoding="utf-8")

    for value in (
        'CONFIG_ROOT = Path("/srv/home-agent/config")',
        'RESTORE_RECEIPT_PATH = CONFIG_ROOT / "phase3-restore-drill-e5j.json"',
        'ERASURE_RECEIPT_PATH = CONFIG_ROOT / "phase3-erasure-current-e5j.json"',
        "details.st_uid != expected_uid",
        "details.st_gid != expected_gid",
        "stat.S_IMODE(details.st_mode) != 0o600",
        "details.st_nlink != 1",
        "os.O_EXCL",
        "os.O_NOFOLLOW",
        "os.fsync(stream.fileno())",
        "os.replace(temporary, path)",
        "os.fsync(directory_descriptor)",
    ):
        assert value in source
    assert "--restore-status" not in source
    assert "--gate-status" not in source
    assert "shell=False" in source


def test_erasure_writer_runs_only_the_read_only_existing_gate() -> None:
    source = WRITER.read_text(encoding="utf-8")
    compose = (ROOT / "stack/home-agent-compose.yml").read_text(encoding="utf-8")
    command = source.split("def _verify_erasure_current()", 1)[1].split(
        "def write_restore", 1
    )[0]
    restore_service = compose.split("  restore-replay:", 1)[1].split(
        "\n  rollout-authorize:", 1
    )[0]

    for value in (
        '"docker"',
        '"compose"',
        '"--pull"',
        '"never"',
        '"--no-deps"',
        '"restore-replay"',
        '"restore-verify"',
    ):
        assert value in command
    for forbidden in (
        "restore-replay\", \"restore-replay",
        "alembic",
        "psql",
        "rollout-authorize",
        "docker stop",
        "docker restart",
        "docker build",
    ):
        assert forbidden not in command
    assert "environment:\n      <<: *core-environment" in restore_service
    assert "HOME_AGENT_POLICY_DIGEST" in compose.split(
        "x-core-environment:", 1
    )[1].split("\nx-core-service:", 1)[0]


def test_restore_drill_writes_only_after_database_and_page_checks_pass() -> None:
    source = DRILL.read_text(encoding="utf-8")
    checksum = source.index('info "checking every restored page checksum offline"')
    success = source.index("drill_succeeded=1")
    writer = source.index('python3 "$receipt_writer" restore')

    assert checksum < success < writer
    assert '--database-system-identifier "$restored_system_id"' in source
    assert "erasure_ledger_replay_status" not in source
    assert 'require_regular_nonsymlink "$receipt_writer"' in source


def test_preflight_rejects_the_old_conflated_e5i_contract() -> None:
    module = _module()
    preflight_source = PREFLIGHT.read_text(encoding="utf-8")

    assert module.RESTORE_CONTRACT in preflight_source
    assert module.ERASURE_CONTRACT in preflight_source
    assert "erasure_ledger_replay_status" not in preflight_source
    assert "phase3-restore-drill-receipt-e5i-v1" not in preflight_source
