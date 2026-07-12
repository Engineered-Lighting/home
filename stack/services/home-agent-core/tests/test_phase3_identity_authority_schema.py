from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app import schema
from app.db import Database


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "alembic/versions/0007_phase3_identity_authority_foundation.py"
GRANTS = ROOT.parents[1] / "home-agent-deploy/apply-grants.sh"

TABLE_NAMES = (
    "reviewed_identity_migration_runs",
    "reviewed_identity_migration_source_items",
    "reviewed_identity_migration_decisions",
    "reviewed_identity_migration_item_receipts",
    "reviewed_identity_migration_finalizations",
    "legacy_identity_writer_evidence",
    "privacy_cutover_check_receipts",
    "semantic_authority_cutovers",
    "reviewed_identity_migration_erasure_impacts",
)
RUNTIME_ROLES = (
    "home_agent_api",
    "home_agent_binding_operator",
    "home_agent_ingest",
    "home_agent_worker",
    "home_agent_erasure",
    "home_agent_rollout",
    "home_agent_backup",
)


def _table(name: str):
    return schema.metadata.tables[f"operations.{name}"]


def _checks(name: str) -> str:
    return " ".join(
        str(item.sqltext)
        for item in _table(name).constraints
        if isinstance(item, CheckConstraint)
    )


def _unique_columns(name: str) -> set[tuple[str, ...]]:
    return {
        tuple(column.name for column in item.columns)
        for item in _table(name).constraints
        if isinstance(item, UniqueConstraint)
    }


def _foreign_keys(name: str) -> set[tuple[tuple[str, ...], tuple[str, ...]]]:
    return {
        (
            tuple(column.name for column in item.columns),
            tuple(element.target_fullname for element in item.elements),
        )
        for item in _table(name).constraints
        if isinstance(item, ForeignKeyConstraint)
    }


def test_phase3_identity_authority_tables_are_content_free_and_offline_compilable() -> (
    None
):
    for name in TABLE_NAMES:
        table = _table(name)
        ddl = str(
            CreateTable(table, if_not_exists=True).compile(dialect=postgresql.dialect())
        )
        assert f"CREATE TABLE IF NOT EXISTS operations.{name}" in ddl
        assert "JSON" not in ddl
        assert "BYTEA" not in ddl
        assert not {
            "display_name",
            "source_path",
            "source_ref",
            "payload",
            "content",
            "alias",
            "person_id",
            "principal_id",
            "ha_user_id",
            "entity_id",
            "latitude",
            "longitude",
            "lat",
            "lon",
            "external_id",
            "external_identifier",
            "frigate_person_name",
            "before_json",
            "after_json",
            "target_id",
        } & set(table.c.keys())


def test_run_contract_binds_predecessor_builds_versions_expiry_and_signatures() -> None:
    run = _table("reviewed_identity_migration_runs")
    assert {
        "source_projection_contract_digest",
        "commitment_key_fingerprint",
        "commitment_key_epoch",
        "shadow_authorization_id",
        "release_manifest_digest",
        "migration_tool_bundle_digest",
        "core_oci_manifest_digest",
        "core_schema_digest",
        "core_capability_digest",
        "signing_key_fingerprint",
        "review_signature",
        "expires_at",
    } <= set(run.c.keys())
    checks = _checks("reviewed_identity_migration_runs")
    for expected in (
        "source_schema_version = 1",
        "legacy-identity-source-projection-v1",
        "record-only-envelope-worker-gate-v3",
        "hmac-sha256-v1",
        "commitment_key_epoch > 0",
        "source_item_count BETWEEN 1 AND 10000",
        "decision_count BETWEEN 1 AND 10000",
        "signature_algorithm = 'ed25519'",
        "review_signature ~ '^[0-9a-f]{128}$'",
        "interval '24 hours'",
    ):
        assert expected in checks


def test_source_manifest_and_decisions_are_dense_bounded_and_same_run_bound() -> None:
    source_checks = _checks("reviewed_identity_migration_source_items")
    assert "ordinal BETWEEN 0 AND 9999" in source_checks
    for source_kind in (
        "schema_meta",
        "identities",
        "identity_aliases",
        "enrollments",
        "relationships",
    ):
        assert source_kind in source_checks
    source_uniques = _unique_columns("reviewed_identity_migration_source_items")
    assert ("run_id", "ordinal") in source_uniques
    assert ("run_id", "row_key_commitment") in source_uniques
    assert ("run_id", "allowed_projection_commitment") not in source_uniques

    decision_checks = _checks("reviewed_identity_migration_decisions")
    for disposition in (
        "apply",
        "privacy_suppressed",
        "out_of_scope_by_rule",
        "coalesced_duplicate",
    ):
        assert disposition in decision_checks
    assert "explicit_omission" in decision_checks
    assert "canonical_apply_disposition = 'apply'" in decision_checks
    decision_fks = _foreign_keys("reviewed_identity_migration_decisions")
    assert any(
        local == ("run_id", "source_item_id")
        and all(
            "reviewed_identity_migration_source_items" in target for target in remote
        )
        for local, remote in decision_fks
    )
    assert any(
        local
        == (
            "run_id",
            "canonical_apply_decision_id",
            "decision_kind",
            "canonical_apply_disposition",
            "candidate_commitment",
        )
        for local, _remote in decision_fks
    )


def test_receipts_are_exact_apply_only_and_projection_kind_checked_in_sql() -> None:
    receipt = _table("reviewed_identity_migration_item_receipts")
    checks = _checks("reviewed_identity_migration_item_receipts")
    assert "decision_disposition = 'apply'" in checks
    assert "inserted_exact" in checks and "replayed_exact" in checks
    for projection in (
        "identity.people",
        "identity.aliases",
        "identity.external_recognition_bindings",
        "identity.privacy_directives",
        "identity.legacy_role_labels",
        "identity.legacy_relationship_candidates",
    ):
        assert projection in checks
    assert "projection_ref_commitment" in receipt.c
    assert "projection_target_id" not in receipt.c
    assert (
        "run_id",
        "decision_kind",
        "projection_ref_commitment",
    ) in _unique_columns("reviewed_identity_migration_item_receipts")
    receipt_fks = _foreign_keys("reviewed_identity_migration_item_receipts")
    assert any(
        local
        == (
            "run_id",
            "decision_id",
            "decision_kind",
            "decision_disposition",
            "candidate_commitment",
        )
        for local, _remote in receipt_fks
    )


def test_candidate_finalization_writer_privacy_cutover_and_erasure_are_honest() -> None:
    finalization_checks = _checks("reviewed_identity_migration_finalizations")
    assert "verification_status = 'candidate_unverified'" in finalization_checks
    assert "authoritative = false" in finalization_checks
    for commitment in (
        "decision_manifest_commitment",
        "receipt_set_commitment",
        "finalization_signature",
    ):
        assert commitment in _table("reviewed_identity_migration_finalizations").c

    evidence = _table("legacy_identity_writer_evidence")
    assert {
        "source_installation_id",
        "semantic_generation",
        "source_projection_commitment",
        "freeze_kernel_build_digest",
        "evidence_signature",
    } <= set(evidence.c.keys())
    evidence_checks = _checks("legacy_identity_writer_evidence")
    assert "observed_stopped" in evidence_checks
    assert "operator_attested" in evidence_checks
    assert "enforced_offline" not in evidence_checks
    assert "physically_frozen" not in evidence_checks

    privacy_checks = _checks("privacy_cutover_check_receipts")
    for category in (
        "ingress",
        "retrieval",
        "prompt",
        "initiative",
        "export",
        "edge_block",
    ):
        assert category in privacy_checks
    assert "check_result IN ('passed','blocked')" in privacy_checks
    assert "check_result = 'passed' AND residual_code = 'none'" in privacy_checks

    cutover_checks = _checks("semantic_authority_cutovers")
    assert "candidate_unenforced" in cutover_checks
    assert "authoritative = false" in cutover_checks
    cutover_fks = _foreign_keys("semantic_authority_cutovers")
    assert any(local == ("run_id", "finalization_id") for local, _ in cutover_fks)
    assert any(local == ("run_id", "writer_evidence_id") for local, _ in cutover_fks)
    for category in (
        "ingress",
        "retrieval",
        "prompt",
        "initiative",
        "export",
        "edge_block",
    ):
        assert (
            (
                "run_id",
                f"{category}_check_id",
                f"{category}_check_category",
                "required_privacy_check_result",
                "required_privacy_residual_code",
            ),
            (
                "operations.privacy_cutover_check_receipts.run_id",
                "operations.privacy_cutover_check_receipts.check_id",
                "operations.privacy_cutover_check_receipts.check_category",
                "operations.privacy_cutover_check_receipts.check_result",
                "operations.privacy_cutover_check_receipts.residual_code",
            ),
        ) in cutover_fks

    erasure_fks = _foreign_keys("reviewed_identity_migration_erasure_impacts")
    assert any(
        local == ("erasure_request_id",)
        and remote == ("privacy.erasure_requests.erasure_request_id",)
        for local, remote in erasure_fks
    )
    assert all("decision" not in " ".join(remote) for _local, remote in erasure_fks)
    assert all("receipt" not in " ".join(remote) for _local, remote in erasure_fks)
    erasure_checks = _checks("reviewed_identity_migration_erasure_impacts")
    assert "readiness_suspension = 'required'" in erasure_checks
    assert "removed_leaf_commitment_count > 0" in erasure_checks


def test_migration_is_owner_only_force_rls_and_refuses_evidence_downgrade() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'revision: str = "0007_phase3_identity_authority"' in source
    assert 'down_revision: str | None = "0006_worker_maintenance_health"' in source
    assert "finalizer function" in source
    assert "pre-0007 legacy-source projection" in source
    assert "ENABLE ROW LEVEL SECURITY" in source
    assert "FORCE ROW LEVEL SECURITY" in source
    assert "TO home_agent_owner" in source
    assert "GRANT SELECT, INSERT ON TABLE" in source
    assert "TO home_agent_owner" in source
    assert "GRANT SELECT, INSERT ON TABLE {qualified} TO home_agent_api" not in source
    for role in RUNTIME_ROLES:
        assert f'"{role}"' in source
    for name in TABLE_NAMES:
        assert name in source
    assert "enforced-offline" in source  # only in the explicit non-authority comment
    assert "parent_confirmation" not in source
    assert "parent_proposal" not in source
    assert "refusing to drop nonempty Phase 3 identity authority evidence" in source
    assert "USING ERRCODE = '2BP01'" in source


def test_apply_grants_revoke_runs_after_broad_and_default_api_grants() -> None:
    source = GRANTS.read_text(encoding="utf-8")
    marker = "Revision 0007 is an owner-only schema foundation"
    assert marker in source
    revoke = source.split(marker, 1)[1]
    assert source.index(marker) > source.index("ALTER DEFAULT PRIVILEGES")
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO home_agent_api" in source
    assert "REVOKE ALL PRIVILEGES ON TABLE" in revoke
    for name in TABLE_NAMES:
        assert f"operations.{name}" in revoke
    for role in RUNTIME_ROLES:
        assert role in revoke
    assert "GRANT" not in revoke


@pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL is required for post-grant PostgreSQL assertions",
)
@pytest.mark.asyncio
async def test_post_grant_tables_are_force_rls_and_runtime_roles_have_zero_acl() -> (
    None
):
    database = Database(os.environ["TEST_DATABASE_URL"])
    try:
        async with database.transaction() as connection:
            for name in TABLE_NAMES:
                qualified = f"operations.{name}"
                rls = (
                    await connection.execute(
                        text(
                            "SELECT relrowsecurity, relforcerowsecurity "
                            "FROM pg_class WHERE oid = to_regclass(:qualified)"
                        ),
                        {"qualified": qualified},
                    )
                ).one()
                assert tuple(rls) == (True, True)
                policies = (
                    await connection.execute(
                        text(
                            "SELECT cmd, roles FROM pg_policies "
                            "WHERE schemaname = 'operations' AND tablename = :name "
                            "ORDER BY cmd"
                        ),
                        {"name": name},
                    )
                ).all()
                assert {row.cmd for row in policies} == {"INSERT", "SELECT"}
                assert all(row.roles == ["home_agent_owner"] for row in policies)
                for role in RUNTIME_ROLES:
                    privileges = (
                        await connection.execute(
                            text(
                                "SELECT "
                                "has_table_privilege(:role, :qualified, 'SELECT'), "
                                "has_table_privilege(:role, :qualified, 'INSERT'), "
                                "has_table_privilege(:role, :qualified, 'UPDATE'), "
                                "has_table_privilege(:role, :qualified, 'DELETE'), "
                                "has_table_privilege(:role, :qualified, 'TRUNCATE')"
                            ),
                            {"role": role, "qualified": qualified},
                        )
                    ).one()
                    assert tuple(privileges) == (False, False, False, False, False)
    finally:
        await database.close()
