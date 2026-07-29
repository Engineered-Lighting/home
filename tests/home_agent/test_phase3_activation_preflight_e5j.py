from __future__ import annotations

from datetime import UTC, datetime, timedelta
import importlib.util
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[2]
PREFLIGHT = ROOT / "stack/home-agent-deploy/operator/phase3_activation_preflight.py"


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "home_agent_phase3_activation_preflight_e5j",
        PREFLIGHT,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


NOW = datetime(2026, 7, 28, 19, 0, tzinfo=UTC)
BACKUP_LABEL = "20260728-102702F"


def _environment(*, topology: str = "local") -> dict[str, str]:
    return {
        "HOME_AGENT_EXPECTED_DB_REVISION": "0006a_worker_lease_arbitration",
        "HOME_AGENT_ROLLOUT_MODE": "record_only",
        "HOME_AGENT_BACKUP_TOPOLOGY": topology,
    }


def _core_probe(*, ready: bool) -> dict[str, object]:
    relevant = 500 if ready else 203
    remaining = 0 if ready else 297
    blockers = [] if ready else ["qualifying_redacted_envelope_threshold_not_met"]
    return {
        "phase2": {
            "contract": "phase2-record-only-gate-v3",
            "rule_version": "record-only-envelope-worker-gate-v3",
            "ready_to_advance": ready,
            "relevant_envelopes": relevant,
            "relevant_envelopes_required": 500,
            "relevant_envelopes_remaining": remaining,
            "blockers": blockers,
        },
        "phase3": {
            "contract": "phase3-readiness-diagnostic-v0",
            "schema_revision": "0006a_worker_lease_arbitration",
            "authoritative": False,
            "enables_writes": False,
            "ready_to_advance": False,
        },
    }


def _backup_info(*, healthy: bool = True) -> list[dict[str, object]]:
    return [
        {
            "status": {"code": 0 if healthy else 1, "message": "ok"},
            "cipher": "aes-256-cbc",
            "backup": [
                {
                    "label": BACKUP_LABEL,
                    "type": "full",
                    "error": False,
                    "timestamp": {"start": 1785234422, "stop": 1785234423},
                }
            ],
        }
    ]


def _health(*, unhealthy: str | None = None) -> dict[str, str]:
    module = _module()
    return {
        name: "unhealthy" if name == unhealthy else required_state
        for name, required_state in module.REQUIRED_CONTAINER_STATES.items()
    }


def _restore_receipt(
    *,
    completed_at: datetime = NOW - timedelta(days=1),
    label: str = BACKUP_LABEL,
) -> dict[str, str]:
    return {
        "contract": "phase3-restore-drill-receipt-e5j-v1",
        "backup_label": label,
        "schema_revision": "0006a_worker_lease_arbitration",
        "database_system_identifier": "7522608291310326247",
        "completed_at": completed_at.isoformat().replace("+00:00", "Z"),
        "restore_status": "passed",
    }


def _ledger_head(*, epoch: int = 7, head_hash: str = "b" * 64) -> dict[str, object]:
    return {"version": 1, "epoch": epoch, "head_hash": head_hash}


def _erasure_receipt(
    *,
    verified_at: datetime = NOW - timedelta(hours=1),
    label: str = BACKUP_LABEL,
    epoch: int = 7,
    head_hash: str = "b" * 64,
) -> dict[str, object]:
    return {
        "contract": "phase3-erasure-current-receipt-e5j-v1",
        "backup_label": label,
        "schema_revision": "0006a_worker_lease_arbitration",
        "ledger_epoch": epoch,
        "ledger_head_hash": head_hash,
        "verified_at": verified_at.isoformat().replace("+00:00", "Z"),
        "gate_status": "current",
    }


def _source_acceptance() -> dict[str, str]:
    return {
        "contract": "phase3-source-acceptance-e5j-v1",
        "target_revision": "0021_parent_status_e5h",
        "source_commit": "a" * 40,
        "postgres_authority_run_id": "30388823143",
        "postgres_authority_status": "success",
        "web_boundary_run_id": "30387665230",
        "web_boundary_status": "success",
        "activation_contract_status": "installed",
    }


def _off_host_receipt() -> dict[str, str]:
    return {
        "contract": "phase3-off-host-backup-receipt-e5j-v1",
        "backup_label": BACKUP_LABEL,
        "copied_at": (NOW - timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        "destination_class": "operator_controlled_off_host",
        "verification_status": "checksum_verified",
    }


def _evaluate(module, **values):
    arguments = {
        "environment": _environment(),
        "core_probe": _core_probe(ready=False),
        "backup_info": _backup_info(),
        "container_health": _health(),
        "restore_receipt": None,
        "erasure_receipt": None,
        "ledger_head": _ledger_head(),
        "off_host_receipt": None,
        "source_acceptance": None,
        "source_commit": "a" * 40,
        "source_tree_clean": True,
        "now": NOW,
    }
    arguments.update(values)
    return module.evaluate(**arguments)


def test_live_shape_reports_only_canonical_e5j_blockers() -> None:
    module = _module()

    report = _evaluate(module)

    assert report["contract"] == "phase3-activation-preflight-e5j-v1"
    assert report["phase2"] == {
        "valid": True,
        "ready": False,
        "qualifying_events": 203,
        "required_events": 500,
        "remaining_events": 297,
        "blockers": ["qualifying_redacted_envelope_threshold_not_met"],
    }
    assert report["blockers"] == [
        "record_only_evidence_not_ready",
        "off_host_backup_receipt_missing",
        "current_backup_restore_receipt_missing",
        "current_erasure_gate_receipt_missing",
        "reviewed_activation_source_not_admitted",
    ]
    assert report["preflight_passed"] is False
    assert report["authoritative"] is False
    assert report["enables_writes"] is False
    assert report["changes_rollout_mode"] is False
    assert report["runs_migrations"] is False


def test_complete_packet_can_pass_preflight_but_never_grants_authority() -> None:
    module = _module()

    report = _evaluate(
        module,
        core_probe=_core_probe(ready=True),
        restore_receipt=_restore_receipt(),
        erasure_receipt=_erasure_receipt(),
        off_host_receipt=_off_host_receipt(),
        source_acceptance=_source_acceptance(),
    )

    assert report["blockers"] == []
    assert report["preflight_passed"] is True
    assert report["authoritative"] is False
    assert report["enables_writes"] is False
    assert report["changes_rollout_mode"] is False
    assert report["runs_migrations"] is False


def test_invalid_core_backup_health_and_container_state_fail_closed() -> None:
    module = _module()
    probe = _core_probe(ready=True)
    probe["phase2"]["contract"] = "attacker-contract"

    report = _evaluate(
        module,
        core_probe=probe,
        backup_info=_backup_info(healthy=False),
        container_health=_health(unhealthy="home-agent-core-api-1"),
        restore_receipt=_restore_receipt(),
        erasure_receipt=_erasure_receipt(),
        off_host_receipt=_off_host_receipt(),
        source_acceptance=_source_acceptance(),
    )

    assert report["blockers"] == [
        "core_admission_diagnostic_invalid",
        "required_container_not_ready",
        "encrypted_backup_repository_not_healthy",
        "off_host_backup_receipt_missing",
        "current_backup_restore_receipt_missing",
        "current_erasure_gate_receipt_missing",
    ]
    assert report["phase2"]["qualifying_events"] is None
    assert report["backup"]["latest_full_backup_label"] is None


def test_restore_receipt_must_match_latest_backup_and_remain_current() -> None:
    module = _module()
    for receipt in (
        _restore_receipt(label="20260727-102059F"),
        _restore_receipt(completed_at=NOW - timedelta(days=36)),
        {**_restore_receipt(), "unexpected": "field"},
    ):
        report = _evaluate(
            module,
            core_probe=_core_probe(ready=True),
            restore_receipt=receipt,
            erasure_receipt=_erasure_receipt(),
            off_host_receipt=_off_host_receipt(),
            source_acceptance=_source_acceptance(),
        )
        assert report["preflight_passed"] is False
        assert "current_backup_restore_receipt_missing" in report["blockers"]
        assert "current_erasure_gate_receipt_missing" in report["blockers"]


def test_erasure_receipt_is_separate_and_bound_to_restore_and_current_head() -> None:
    module = _module()
    for receipt, head in (
        (_erasure_receipt(label="20260727-102059F"), _ledger_head()),
        (_erasure_receipt(epoch=6), _ledger_head()),
        (_erasure_receipt(head_hash="c" * 64), _ledger_head()),
        (
            _erasure_receipt(verified_at=NOW - timedelta(days=36)),
            _ledger_head(),
        ),
        ({**_erasure_receipt(), "restore_status": "passed"}, _ledger_head()),
    ):
        report = _evaluate(
            module,
            core_probe=_core_probe(ready=True),
            restore_receipt=_restore_receipt(),
            erasure_receipt=receipt,
            ledger_head=head,
            off_host_receipt=_off_host_receipt(),
            source_acceptance=_source_acceptance(),
        )
        assert report["preflight_passed"] is False
        assert "current_erasure_gate_receipt_missing" in report["blockers"]


def test_source_acceptance_is_exact_and_cannot_claim_activation_by_itself() -> None:
    module = _module()
    for receipt in (
        {**_source_acceptance(), "postgres_authority_status": "queued"},
        {**_source_acceptance(), "activation_contract_status": "pending"},
        {**_source_acceptance(), "source_commit": "not-a-commit"},
        {**_source_acceptance(), "private_note": "forbidden"},
    ):
        report = _evaluate(
            module,
            core_probe=_core_probe(ready=True),
            restore_receipt=_restore_receipt(),
            erasure_receipt=_erasure_receipt(),
            off_host_receipt=_off_host_receipt(),
            source_acceptance=receipt,
        )
        assert report["preflight_passed"] is False
        assert report["authoritative"] is False
        assert report["enables_writes"] is False
        assert "reviewed_activation_source_not_admitted" in report["blockers"]


def test_source_receipt_must_match_clean_checked_out_commit() -> None:
    module = _module()
    for commit, clean in (("b" * 40, True), ("a" * 40, False)):
        report = _evaluate(
            module,
            core_probe=_core_probe(ready=True),
            restore_receipt=_restore_receipt(),
            erasure_receipt=_erasure_receipt(),
            off_host_receipt=_off_host_receipt(),
            source_acceptance=_source_acceptance(),
            source_commit=commit,
            source_tree_clean=clean,
        )
        assert report["preflight_passed"] is False
        assert "reviewed_activation_source_not_admitted" in report["blockers"]


def test_operator_probe_contains_no_mutating_container_or_database_command() -> None:
    source = PREFLIGHT.read_text(encoding="utf-8")
    module = _module()

    assert "shell=False" in source
    assert "docker compose" not in source
    assert 'docker", "exec' in source
    assert 'docker", "inspect' in source
    for forbidden in (
        "alembic upgrade",
        "rollout-authorize",
        "docker restart",
        "docker stop",
        "docker rm",
        "docker cp",
        "psql",
        "pg_dump",
        "execute_services",
    ):
        assert forbidden not in module.CORE_PROBE
    assert "/v1/operator-rollout/phase2-readiness" in module.CORE_PROBE
    assert "/v1/operator-rollout/phase3-readiness" in module.CORE_PROBE
    assert "print(operator)" not in module.CORE_PROBE
    assert "print(bootstrap)" not in module.CORE_PROBE


def test_e5j_is_carried_by_the_filtered_hosted_gate() -> None:
    runner = (ROOT / "tools/run-home-agent-e1-postgres-gate.py").read_text(
        encoding="utf-8"
    )
    workflow = (ROOT / ".github/workflows/home-agent-e1-postgres.yml").read_text(
        encoding="utf-8"
    )

    assert "stack/home-agent-deploy/operator/phase3_activation_preflight.py" in runner
    assert "stack/home-agent-deploy/operator/phase3_evidence_receipts.py" in runner
    assert "tests/home_agent/test_phase3_activation_preflight_e5j.py" in runner
    assert "tests/home_agent/test_phase3_evidence_receipts_e5j.py" in runner
    assert "test_phase3_activation_preflight_e5j.py" in workflow
    assert "test_phase3_evidence_receipts_e5j.py" in workflow
    assert "E5j/E5k/E5l/E5m/E5n/E5o/E5p/E5q/E5r/E5s/E5t/E5u PostgreSQL gate" in workflow
    assert "E5j/E5k/E5l/E5m/E5n/E5o/E5p/E5q/E5r/E5s/E5t/E5u authority gate" in workflow


def test_environment_parser_selects_no_secret_or_arbitrary_shell_value() -> None:
    module = _module()
    values = module.selected_environment(
        """
        HOME_AGENT_EXPECTED_DB_REVISION=0006a_worker_lease_arbitration
        HOME_AGENT_ROLLOUT_MODE=record_only
        HOME_AGENT_BACKUP_TOPOLOGY=local
        HOME_AGENT_OPERATOR_TOKEN=private-canary
        """
    )

    assert values == _environment()
    assert "private-canary" not in repr(values)
