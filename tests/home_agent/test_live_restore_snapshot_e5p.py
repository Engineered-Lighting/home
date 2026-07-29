from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_local_restore_stages_encrypted_repository_under_exclusive_lock() -> None:
    script = read("stack/home-agent-deploy/operator/isolated_restore_drill.sh")
    local_block = script.split(
        'if [[ "$HOME_AGENT_BACKUP_TOPOLOGY" == local ]]; then', 2
    )[-1].split("else", 1)[0]

    expected = (
        'production_postgres_container=home-agent-postgres-1',
        'exec 8>>"$HOME_AGENT_PGBACKREST_LOCK_FILE"',
        "flock -n -x 8",
        'grep -Fxq "$HOME_AGENT_PGBACKREST_LOCAL_REPO_ROOT|/repository"',
        'grep -Fxq "$production_data|/var/lib/postgresql/data"',
        'grep -Fxq "$HOME_AGENT_PGBACKREST_LOCK_FILE|/run/home-agent-locks/repository.lock"',
        'repo_local="$workspace/stage/pgbackrest-repository"',
        "cp --archive --one-file-system --reflink=auto",
        'flock -u 8',
        'exec 8>&-',
    )
    for value in expected:
        assert value in local_block

    assert local_block.index("flock -n -x 8") < local_block.index("cp --archive")
    assert local_block.index("cp --archive") < local_block.index("flock -u 8")
    assert (
        "an unreviewed running container mounts protected Home Agent storage"
        in local_block
    )
    assert (
        "requires PostgreSQL and every repository writer to be stopped"
        not in local_block
    )


def test_live_snapshot_is_part_of_the_hosted_and_source_acceptance_boundaries() -> None:
    workflow = read(".github/workflows/home-agent-e1-postgres.yml")
    runner = read("tools/run-home-agent-e1-postgres-gate.py")
    dockerfile = read(
        "stack/services/home-agent-core/Dockerfile.postgres-test"
    )
    source_plan = read(
        "stack/home-agent-deploy/operator/phase3_activation_source_plan.py"
    )

    assert "E5n/E5o/E5p/E5q PostgreSQL gate" in workflow
    assert "E5n/E5o/E5p/E5q authority gate" in workflow
    assert "test_live_restore_snapshot_e5p.py" in workflow
    assert "test_live_restore_snapshot_e5p.py" in runner
    assert (
        "COPY docs/HOME-AGENT-RUNBOOK.md /workspace/docs/HOME-AGENT-RUNBOOK.md"
        in dockerfile
    )
    assert (
        '"stack/home-agent-deploy/operator/isolated_restore_drill.sh",'
        in source_plan
    )


def test_operator_documentation_explains_online_production_boundary() -> None:
    drill = read("stack/home-agent-deploy/operator/RESTORE-DRILL.md")
    runbook = read("docs/HOME-AGENT-RUNBOOK.md")

    for documentation in (drill, runbook):
        normalized = " ".join(documentation.split())
        assert "PostgreSQL may remain online" in normalized
        assert "exclusive repository lock" in normalized
    assert "scheduled path remains disabled" in " ".join(drill.split())
