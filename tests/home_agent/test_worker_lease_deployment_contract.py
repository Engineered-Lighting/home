from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REVISION = "0006a_worker_lease_arbitration"


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_normal_migration_is_pinned_before_dormant_phase3_revisions() -> None:
    entrypoint = read("stack/services/home-agent-core/docker-entrypoint.sh")
    compose = read("stack/home-agent-compose.yml")
    phase3 = read(
        "stack/services/home-agent-core/alembic/versions/"
        "0007_phase3_identity_authority_foundation.py"
    )

    assert f'DEPLOYABLE_MIGRATION_REVISION="{REVISION}"' in entrypoint
    assert 'target="${HOME_AGENT_EXPECTED_DB_REVISION:-}"' in entrypoint
    assert 'if [ "$target" != "$DEPLOYABLE_MIGRATION_REVISION" ]' in entrypoint
    assert 'alembic upgrade "$migration_target"' in entrypoint
    assert 'python -m app.migration_guard "$migration_target"' in entrypoint
    assert "alembic upgrade head" not in entrypoint
    assert "alembic upgrade 0007" not in entrypoint
    assert "alembic upgrade 0012" not in entrypoint

    migrate = compose.split("  migrate:\n", 1)[1].split("\n  grant-runtime:", 1)[0]
    assert "HOME_AGENT_EXPECTED_DB_REVISION:" in migrate
    assert (
        "${HOME_AGENT_EXPECTED_DB_REVISION:?set deployable database revision}"
        in migrate
    )
    assert f'down_revision: str | None = "{REVISION}"' in phase3

    readme = read("stack/services/home-agent-core/README.md")
    assert f"HOME_AGENT_EXPECTED_DB_REVISION={REVISION}" in readme
    assert "heartbeat timestamp in the future is intentionally" in readme
    assert "restart PostgreSQL so its postmaster-start fence changes" in readme


def test_runtime_and_operator_revision_pins_match_the_deployable_migration() -> None:
    assert f'readiness_migration: ReadinessMigration = "{REVISION}"' in read(
        "stack/services/home-agent-core/app/config.py"
    )
    assert f"HOME_AGENT_EXPECTED_DB_REVISION={REVISION}" in read(
        "stack/home-agent.env.example"
    )
    assert f'"{REVISION}.py"' in read("tools/run-home-agent-e1-postgres-gate.py")
