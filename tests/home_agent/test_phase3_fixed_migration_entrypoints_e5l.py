from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = ROOT / "stack/services/home-agent-core/docker-entrypoint.sh"
COMPOSE = ROOT / "stack/home-agent-compose.yml"
WORKFLOW = ROOT / ".github/workflows/home-agent-e1-postgres.yml"
RUNNER = ROOT / "tools/run-home-agent-e1-postgres-gate.py"

FIXED_STAGES = {
    "phase3-migrate-finalizer": "0013_identity_finalizer_e3",
    "phase3-migrate-current-authority": "0015_current_authority_e5a",
    "phase3-migrate-authenticated-binding": "0017_authenticated_binding_e5c",
    "phase3-migrate-parent-authority": "0018_parent_relationship_e5d",
    "phase3-migrate-parent-status": "0021_parent_status_e5h",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_e5l_installs_only_the_five_reviewed_phase3_stops() -> None:
    source = _read(ENTRYPOINT)

    for role, revision in FIXED_STAGES.items():
        assert f'="{revision}"' in source
        assert f"  {role})" in source
    # Five executable case arms plus the one deny-only startup guard pattern.
    assert source.count("  phase3-migrate-") == len(FIXED_STAGES) + 1
    assert "alembic upgrade head" not in source
    assert 'alembic upgrade "$migration_target"' in source
    assert 'python -m app.migration_guard "$migration_target"' in source


def test_e5l_does_not_widen_the_normal_migration_target() -> None:
    source = _read(ENTRYPOINT)

    assert 'DEPLOYABLE_MIGRATION_REVISION="0006a_worker_lease_arbitration"' in source
    assert 'target="${HOME_AGENT_EXPECTED_DB_REVISION:-}"' in source
    assert 'if [ "$target" != "$DEPLOYABLE_MIGRATION_REVISION" ]' in source
    assert 'run_phase3_migration "$PHASE3_FINALIZER_REVISION"' in source
    assert 'run_phase3_migration "$PHASE3_PARENT_STATUS_REVISION"' in source


def test_e5l_rejects_startup_migration_and_extra_arguments() -> None:
    source = _read(ENTRYPOINT)

    assert 'if [ "${HOME_AGENT_RUN_MIGRATIONS:-0}" = "1" ]; then' in source
    assert source.count(
        'echo "phase3 migration cannot use automatic startup migration"'
    ) == 2
    for label in (
        "finalizer",
        "current-authority",
        "authenticated-binding",
        "parent-authority",
        "parent-status",
    ):
        assert f'echo "phase3 {label} migration accepts no arguments"' in source


def test_e5l_has_no_compose_activation_surface() -> None:
    compose = _read(COMPOSE)

    for role in FIXED_STAGES:
        assert role not in compose
    assert "phase3-schema-activation" not in compose
    assert "phase3-migration-permit" not in compose


def test_e5l_is_carried_by_the_hosted_gate() -> None:
    workflow = _read(WORKFLOW)
    runner = _read(RUNNER)

    assert "test_phase3_fixed_migration_entrypoints_e5l.py" in workflow
    assert "test_phase3_fixed_migration_entrypoints_e5l.py" in runner
    assert "E5j/E5k/E5l/E5m/E5n/E5o/E5p/E5q/E5r PostgreSQL gate" in workflow
    assert "E5j/E5k/E5l/E5m/E5n/E5o/E5p/E5q/E5r authority gate" in workflow
