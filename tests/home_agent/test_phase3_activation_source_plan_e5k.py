from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[2]
PLAN = ROOT / "stack/home-agent-deploy/operator/phase3_activation_source_plan.py"


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "home_agent_phase3_activation_source_plan_e5k",
        PLAN,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tree() -> bytes:
    return (
        b"100644 blob " + b"a" * 40 + b"\tstack/a.py\0"
        b"100755 blob " + b"b" * 40 + b"\tstack/b.sh\0"
    )


def test_exact_hosted_source_pack_can_be_verified_but_never_activated() -> None:
    module = _module()
    entries, digest = module.parse_tree(_tree())
    report = module.evaluate(
        head_commit="c" * 40,
        clean=True,
        accepted_is_ancestor=True,
        source_diff_clean=True,
        tree_entries=entries,
        source_pack_digest=digest,
    )

    assert entries == 2
    assert report["contract"] == "phase3-activation-source-plan-e5k-v1"
    assert report["accepted_commit"] == "772f9c0edcb9d8f1bcbde447533671c9a57bd2b5"
    assert report["accepted_postgres_run_id"] == "33045552910"
    assert report["accepted_web_run_id"] == "31906584262"
    assert report["source_pack_matches_hosted_acceptance"] is True
    assert report["fixed_migration_entrypoints_installed"] is True
    assert report["activation_grant_contract_installed"] is True
    assert report["identity_finalizer_executor_installed"] is True
    assert report["identity_cutover_executor_installed"] is True
    assert report["activation_executor_installed"] is True
    assert report["authoritative_split_phase_activation_runner_installed"] is True
    assert report["source_acceptance_receipt_issuable"] is True
    assert report["reviewed_identity_packet_compiler_installed"] is True
    assert (
        report["reviewed_identity_distinct_purpose_signing_ceremony_installed"] is True
    )
    assert report["identity_signing_credential_provisioner_installed"] is True
    assert report["identity_writer_freeze_evidence_writer_installed"] is True
    assert report["identity_privacy_cutover_evidence_writer_installed"] is True
    assert report["identity_privacy_cutover_observer_installed"] is True
    assert report["identity_semantic_cutover_packet_compiler_installed"] is True
    assert report["authoritative"] is False
    assert report["enables_writes"] is False
    assert report["runs_migrations"] is False
    assert report["changes_rollout_mode"] is False
    assert report["blockers"] == []


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ({"clean": False}, "hosted_accepted_source_pack_not_established"),
        (
            {"accepted_is_ancestor": False},
            "hosted_accepted_source_pack_not_established",
        ),
        ({"source_diff_clean": False}, "hosted_accepted_source_pack_not_established"),
        (
            {"head_commit": "not-a-commit"},
            "hosted_accepted_source_pack_not_established",
        ),
        ({"tree_entries": 0}, "hosted_accepted_source_pack_not_established"),
        (
            {"source_pack_digest": "not-a-digest"},
            "hosted_accepted_source_pack_not_established",
        ),
    ],
)
def test_any_source_uncertainty_fails_closed(values, expected: str) -> None:
    module = _module()
    arguments = {
        "head_commit": "c" * 40,
        "clean": True,
        "accepted_is_ancestor": True,
        "source_diff_clean": True,
        "tree_entries": 2,
        "source_pack_digest": "d" * 64,
    }
    arguments.update(values)

    report = module.evaluate(**arguments)

    assert report["source_pack_matches_hosted_acceptance"] is False
    assert report["fixed_migration_entrypoints_installed"] is False
    assert report["source_pack_digest"] is None
    assert report["source_pack_entries"] is None
    assert report["blockers"][0] == expected
    assert report["source_acceptance_receipt_issuable"] is False


def test_tree_parser_rejects_links_bad_modes_duplicates_and_disorder() -> None:
    module = _module()
    invalid = (
        b"",
        b"120000 blob " + b"a" * 40 + b"\tstack/link\0",
        b"100644 tree " + b"a" * 40 + b"\tstack/a\0",
        b"100644 blob " + b"a" * 39 + b"\tstack/a\0",
        b"100644 blob " + b"a" * 40 + b"\t../escape\0",
        (
            b"100644 blob " + b"a" * 40 + b"\tstack/b\0"
            b"100644 blob " + b"b" * 40 + b"\tstack/a\0"
        ),
        (
            b"100644 blob " + b"a" * 40 + b"\tstack/a\0"
            b"100644 blob " + b"b" * 40 + b"\tstack/a\0"
        ),
    )
    for tree in invalid:
        with pytest.raises(module.SourcePlanError):
            module.parse_tree(tree)


def test_live_verifier_is_git_only_read_only_and_has_no_receipt_writer() -> None:
    source = PLAN.read_text(encoding="utf-8")
    module = _module()

    assert "shell=False" in source
    for value in (
        '"git", "rev-parse", "HEAD"',
        '"git", "status", "--porcelain", "--untracked-files=all"',
        '"git", "merge-base", "--is-ancestor"',
        '"diff",',
        '"ls-tree"',
        '"-r"',
        '"-z"',
    ):
        assert value in source
    live_source = source.split("def live_report()", 1)[1]
    for forbidden in (
        '["docker"',
        "alembic",
        "psql",
        "pg_dump",
        "restore-replay",
        "rollout-authorize",
        "atomic_write",
        "SOURCE_ACCEPTANCE_PATH",
        "phase3-source-acceptance-e5",
    ):
        assert forbidden not in live_source
    assert len(module.ACTIVATION_PATHS) >= 25
    assert len(set(module.ACTIVATION_PATHS)) == len(module.ACTIVATION_PATHS)


def test_e5k_is_carried_by_the_filtered_hosted_gate() -> None:
    runner = (ROOT / "tools/run-home-agent-e1-postgres-gate.py").read_text(
        encoding="utf-8"
    )
    workflow = (ROOT / ".github/workflows/home-agent-e1-postgres.yml").read_text(
        encoding="utf-8"
    )

    assert "stack/home-agent-deploy/operator/phase3_activation_source_plan.py" in (
        runner
    )
    assert "stack/home-agent-deploy/operator/phase3_activation_runner.py" in runner
    assert "tests/home_agent/test_phase3_activation_source_plan_e5k.py" in runner
    assert "tests/home_agent/test_phase3_activation_runner_e5ad.py" in runner
    assert "stack/home-agent-deploy/operator/migrate_legacy_identity.py" in runner
    assert "phase3_activation_source_plan.py" in workflow
    assert "phase3_activation_runner.py" in workflow
    assert "test_phase3_activation_source_plan_e5k.py" in workflow
    assert "test_phase3_activation_runner_e5ad.py" in workflow
    assert "operator/migrate_legacy_identity.py" in workflow
    assert "stack/home-agent-deploy/operator/migrate_legacy_identity.py" in (
        _module().ACTIVATION_PATHS
    )
    assert (
        "E5j/E5k/E5l/E5m/E5n/E5o/E5p/E5q/E5r/E5s/E5t/E5u/E5v/E5w/E5x PostgreSQL gate"
        in workflow
    )
    assert (
        "E5j/E5k/E5l/E5m/E5n/E5o/E5p/E5q/E5r/E5s/E5t/E5u/E5v/E5w/E5x authority gate"
        in workflow
    )
