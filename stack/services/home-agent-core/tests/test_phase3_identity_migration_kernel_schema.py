from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "alembic" / "versions"
MIGRATION = (MIGRATIONS / "0008_identity_migration_kernel.py").read_text(
    encoding="utf-8"
)
SCHEMA = (ROOT / "app" / "schema.py").read_text(encoding="utf-8")


def _revision_literal(path: Path, name: str) -> str | None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for statement in tree.body:
        if not isinstance(statement, ast.AnnAssign):
            continue
        if not isinstance(statement.target, ast.Name) or statement.target.id != name:
            continue
        if isinstance(statement.value, ast.Constant):
            return statement.value.value
    return None


def test_every_alembic_revision_fits_the_deployed_version_column() -> None:
    revisions: set[str] = set()
    for path in sorted(MIGRATIONS.glob("[0-9][0-9][0-9][0-9]_*.py")):
        revision = _revision_literal(path, "revision")
        assert revision is not None, path.name
        assert len(revision) <= 32, (path.name, revision, len(revision))
        assert revision not in revisions
        revisions.add(revision)
        predecessor = _revision_literal(path, "down_revision")
        if predecessor is not None:
            assert len(predecessor) <= 32, (path.name, predecessor, len(predecessor))


def test_manifest_kernel_is_explicitly_non_deployable_and_manifest_only() -> None:
    assert 'revision: str = "0008_identity_migration_kernel"' in MIGRATION
    assert 'down_revision: str | None = "0007_phase3_identity_authority"' in MIGRATION
    assert "external_payload_verifier_not_implemented" in MIGRATION
    assert "'finalization_enabled', false" in MIGRATION
    assert "CREATE OR REPLACE FUNCTION\n        operations.finalize" not in MIGRATION
    assert "INSERT INTO identity." not in MIGRATION
    assert "INSERT INTO knowledge." not in MIGRATION
    assert "INSERT INTO privacy." not in MIGRATION
    assert "INSERT INTO operations.outbox" not in MIGRATION
    assert (
        "INSERT INTO operations.reviewed_identity_migration_item_receipts"
        not in MIGRATION
    )
    assert (
        "INSERT INTO operations.reviewed_identity_migration_finalizations"
        not in MIGRATION
    )


def test_exact_roles_own_and_call_the_two_fixed_functions() -> None:
    assert "rolname = '{KERNEL_ROLE}'" in MIGRATION
    assert "rolcanlogin = false" in MIGRATION
    assert MIGRATION.count("rolinherit = false") == 2
    assert "rolsuper = false" in MIGRATION
    assert "rolbypassrls = false" in MIGRATION
    assert MIGRATION.count("rolcreatedb = false") == 2
    assert MIGRATION.count("rolcreaterole = false") == 2
    assert MIGRATION.count("rolreplication = false") == 2
    assert "rolconnlimit = 0" in MIGRATION
    assert "rolname = '{CALLER_ROLE}'" in MIGRATION
    assert "rolcanlogin = true" in MIGRATION
    assert "rolconnlimit = 1" in MIGRATION
    assert MIGRATION.count("pg_catalog.pg_auth_members") >= 4
    assert "kernel_membership.member = kernel_role.oid" in MIGRATION
    assert "caller_membership.member = caller_role.oid" in MIGRATION
    assert "caller_role.rolvaliduntil IS NOT NULL" in MIGRATION
    assert "caller_role.rolvaliduntil <=" in MIGRATION
    assert MIGRATION.count("pg_catalog.pg_shdepend") >= 4
    assert "kernel_ownership.deptype = 'o'" in MIGRATION
    assert "caller_ownership.deptype = 'o'" in MIGRATION
    assert "owner_role.rolname = 'home_agent_owner'" in MIGRATION
    assert "owner_membership.admin_option = false" in MIGRATION
    assert "owner_membership.inherit_option = false" in MIGRATION
    assert "owner_membership.set_option = true" in MIGRATION
    assert MIGRATION.count("SECURITY DEFINER") == 3
    assert MIGRATION.count("SET search_path = pg_catalog, pg_temp") == 3
    signatures = (
        "operations.reviewed_identity_migration_capabilities()",
        "operations.register_reviewed_identity_migration(jsonb)",
    )
    for signature in signatures:
        assert f'"{signature}"' in MIGRATION
    assert 'f"ALTER FUNCTION {signature} OWNER TO {KERNEL_ROLE}"' in MIGRATION
    assert 'f"GRANT EXECUTE ON FUNCTION {signature} TO {CALLER_ROLE}"' in MIGRATION
    assert "IF session_user <> '{CALLER_ROLE}'" in MIGRATION
    assert "OR current_user <> '{KERNEL_ROLE}'" in MIGRATION
    assert "identity_migration_role_required" in MIGRATION
    assert "identity_migration_serializable_required" in MIGRATION
    assert "identity_migration_role_activation_invalid" in MIGRATION
    assert "identity_migration_stale_transaction" in MIGRATION
    assert "pg_catalog.set_config('lock_timeout', '5s', true)" in MIGRATION
    assert "pg_catalog.pg_advisory_xact_lock({ADVISORY_LOCK_ID})" in MIGRATION
    assert "identity_migration_ownership_contract_invalid" in MIGRATION
    assert "_assert_installed_ownership()" in MIGRATION
    assert "operations.bump_reviewed_identity_migration_replay_guard()" in MIGRATION
    assert "identity_migration_replay_guard_contract_invalid" in MIGRATION
    assert "GRANT EXECUTE ON FUNCTION {REPLAY_GUARD_TRIGGER_SIGNATURE}" not in MIGRATION


def test_kernel_rls_and_grants_are_narrow_and_direct_caller_access_is_zero() -> None:
    assert 'select_policy = f"{name}_manifest_kernel_select"' in MIGRATION
    assert 'insert_policy = f"{name}_manifest_kernel_insert"' in MIGRATION
    for table in (
        "operations.reviewed_identity_migration_runs",
        "operations.reviewed_identity_migration_source_items",
        "operations.reviewed_identity_migration_decisions",
    ):
        assert f'"{table}"' in MIGRATION
    assert "\"session_user = 'home_agent_identity_migration' \"" in MIGRATION
    assert "AND current_user = 'home_agent_identity_kernel'" in MIGRATION
    assert "AND NOT pg_catalog.pg_has_role(" in MIGRATION
    assert (
        "GRANT SELECT, INSERT ON TABLE "
        '"\n        "operations.reviewed_identity_migration_runs'
    ) in MIGRATION
    assert (
        "GRANT SELECT (authorization_id, from_mode, to_mode, rule_version" in MIGRATION
    )
    assert "REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA {schema_name}" in MIGRATION
    assert "FROM PUBLIC, {runtime_roles}" in MIGRATION
    assert "ENABLE ROW LEVEL SECURITY" in MIGRATION
    assert "FORCE ROW LEVEL SECURITY" in MIGRATION
    assert "reviewed_identity_migration_runs_replay_guard_update" in MIGRATION
    assert "reviewed_identity_migration_runs_replay_guard_trigger_select" in MIGRATION
    assert "pg_catalog.pg_trigger_depth() = 1" in MIGRATION
    assert "GRANT UPDATE (expires_at) ON TABLE" in MIGRATION
    assert "FROM {KERNEL_ROLE}, {CALLER_ROLE}" in MIGRATION
    assert 'f"TO {KERNEL_ROLE}"' in MIGRATION
    assert 'f"GRANT SELECT' not in MIGRATION
    assert "GRANT INSERT" not in MIGRATION
    for forbidden in (
        "operations.reviewed_identity_migration_item_receipts",
        "operations.reviewed_identity_migration_finalizations",
        "operations.legacy_identity_writer_evidence",
        "operations.privacy_cutover_check_receipts",
        "operations.semantic_authority_cutovers",
        "operations.reviewed_identity_migration_erasure_impacts",
    ):
        assert forbidden in MIGRATION


def test_registration_is_bounded_dense_and_validates_the_full_replay_set() -> None:
    for marker in (
        "pg_catalog.octet_length(manifest::text) > 4194304",
        "manifest_canonical_bytes_max",
        "run_header_canonical_bytes_max",
        "NOT BETWEEN 1 AND 10000",
        "minimum_ordinal <> 0",
        "maximum_ordinal <> target_run.source_item_count - 1",
        "maximum_ordinal <> target_run.decision_count - 1",
        "distinct_ordinals <> target_run.source_item_count",
        "distinct_ordinals <> target_run.decision_count",
        "GROUP BY decision_item.source_item_id,",
        "decision_item.decision_kind",
        "decision_item.decision_kind <> 'privacy_directive'",
        "privacy_item.decision_kind = 'privacy_directive'",
        "privacy_item.candidate_commitment",
        "pg_catalog.bool_or(",
        "omission_item.decision_kind = 'explicit_omission'",
        "WITH decision_set AS MATERIALIZED",
        "WITH source_set AS MATERIALIZED",
        "identity_migration_manifest_invalid",
        "identity_migration_manifest_conflict",
        "identity_migration_predecessor_invalid",
        "identity_migration_review_expired",
        "identity_migration_erasure_affected",
        "SET expires_at = replay_guard.expires_at",
        "RETURNING replay_guard.* INTO existing_run",
        "existing_run_found := FOUND",
        "reviewed_identity_migration_erasure_replay_guard",
        "SET expires_at = expires_at",
    ):
        assert marker in MIGRATION
    assert "existing_run.operator_request_id" in MIGRATION
    assert "existing_run.review_signature" in MIGRATION
    assert "existing_run.expires_at" in MIGRATION
    assert "stored_source.allowed_projection_commitment" in MIGRATION
    assert "stored_decision.canonical_apply_decision_id" in MIGRATION
    assert "stored_decision.decision_commitment" in MIGRATION
    assert "RETURN target_run.run_id" in MIGRATION
    replay_return = MIGRATION.index("RETURN target_run.run_id")
    expiry_gate = MIGRATION.index("identity_migration_review_expired")
    assert replay_return < expiry_gate
    assert "exact full-set replay remains valid after expiry" in MIGRATION
    assert "pg_catalog.clock_timestamp()" in MIGRATION
    assert 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"' in MIGRATION
    header_conflict = MIGRATION.index("existing_run.operator_request_id")
    erasure_block = MIGRATION.index("IF erasure_affected THEN")
    child_compare = MIGRATION.index("stored_source.allowed_projection_commitment")
    assert header_conflict < erasure_block < child_compare
    registration_guard = MIGRATION.index("SET expires_at = replay_guard.expires_at")
    impact_guard = MIGRATION.index("SET expires_at = expires_at")
    assert registration_guard < header_conflict
    assert MIGRATION.count("{PER_RUN_ADVISORY_NAMESPACE}") >= 2
    assert impact_guard > registration_guard


def test_one_run_per_shadow_predecessor_and_downgrade_is_fail_closed() -> None:
    assert "uq_identity_migration_shadow_authorization_once" in MIGRATION
    assert "CREATE UNIQUE INDEX IF NOT EXISTS" not in MIGRATION
    assert "identity_migration_shadow_index_name_occupied" in MIGRATION
    assert "identity_migration_shadow_index_invalid" in MIGRATION
    assert "identity_migration_shadow_index_missing" in MIGRATION
    assert "index_contract.indisvalid = true" in MIGRATION
    assert "index_contract.indisunique = true" in MIGRATION
    assert "pg_catalog.pg_get_userbyid(index_class.relowner)" in MIGRATION
    assert '"DROP INDEX "' in MIGRATION
    assert '"DROP INDEX IF EXISTS "' not in MIGRATION
    assert "shadow_authorization_id =" in MIGRATION
    assert "identity_migration_kernel_downgrade_blocked" in MIGRATION
    assert "USING ERRCODE = '2BP01'" in MIGRATION
    assert "FROM operations.reviewed_identity_migration_runs" in MIGRATION
    assert "uq_identity_migration_shadow_authorization_once" not in SCHEMA
