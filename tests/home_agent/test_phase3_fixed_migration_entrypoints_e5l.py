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
    # Reviewed addition: the owner-attested People work ends at 0027, and a
    # migration with no stop that reaches it is undeployable -- `migrate`
    # refuses any target but the baseline.
    "phase3-migrate-owner-person": "0027_owner_person_e5n",
    # Reviewed addition: 0028 makes the owner-attested partner kernel
    # executable at all -- it had the wrong owner, a duplicate overload, no
    # privileges and no row policy. Both owner-attested routes pin to it, so
    # without a stop that reaches it the image cannot deploy the revision they
    # require.
    "phase3-migrate-owner-partner-access": "0028_owner_partner_access_e5o",
    # Reviewed addition: 0029 gives owner-attested person creation a kernel
    # role of its own. Both owner-attested routes pin to it, so without a
    # stop that reaches it the image cannot deploy the revision they need.
    "phase3-migrate-owner-person-role": "0029_owner_person_role_e5p",
    # Reviewed addition: 0030 widens the relationship vocabulary, so it needs
    # its own fixed stage -- the generic migrator only deploys 0006a.
    "phase3-migrate-relationship-vocabulary": "0030_relationship_vocabulary_e5q",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_e5l_installs_only_the_reviewed_phase3_stops() -> None:
    source = _read(ENTRYPOINT)

    for role, revision in FIXED_STAGES.items():
        assert f'="{revision}"' in source
        assert f"  {role})" in source
    # One executable case arm per reviewed stop, plus the single deny-only
    # startup guard pattern.
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
    assert (
        source.count('echo "phase3 migration cannot use automatic startup migration"')
        == 2
    )
    for label in (
        role.removeprefix("phase3-migrate-") for role in FIXED_STAGES
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
    assert (
        "E5j/E5k/E5l/E5m/E5n/E5o/E5p/E5q/E5r/E5s/E5t/E5u/E5v/E5w/E5x PostgreSQL gate"
        in workflow
    )
    assert (
        "E5j/E5k/E5l/E5m/E5n/E5o/E5p/E5q/E5r/E5s/E5t/E5u/E5v/E5w/E5x authority gate"
        in workflow
    )
