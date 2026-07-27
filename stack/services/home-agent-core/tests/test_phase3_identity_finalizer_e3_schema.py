from __future__ import annotations

import ast
import hashlib
import re
from pathlib import Path

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app import schema


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = ROOT.parents[2]
MIGRATION_PATH = ROOT / "alembic/versions/0013_identity_finalizer_kernel.py"
GRANTS_PATH = REPOSITORY_ROOT / "stack/home-agent-deploy/apply-grants.sh"
MIGRATION = MIGRATION_PATH.read_text(encoding="utf-8")
GRANTS = GRANTS_PATH.read_text(encoding="utf-8")
ADMISSION_TABLE = "reviewed_identity_finalizer_admissions"


def _literal(name: str) -> str:
    tree = ast.parse(MIGRATION)
    for statement in tree.body:
        if not isinstance(statement, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == name
            for target in statement.targets
        ):
            continue
        assert isinstance(statement.value, ast.Constant)
        assert isinstance(statement.value.value, str)
        return statement.value.value
    raise AssertionError(f"{name} is not a string literal")


def _table(name: str):
    return schema.metadata.tables[f"operations.{name}"]


def _checks(name: str) -> str:
    return " ".join(
        str(constraint.sqltext)
        for constraint in _table(name).constraints
        if isinstance(constraint, CheckConstraint)
    )


def _unique_columns(name: str) -> set[tuple[str, ...]]:
    return {
        tuple(column.name for column in constraint.columns)
        for constraint in _table(name).constraints
        if isinstance(constraint, UniqueConstraint)
    }


def _foreign_keys(name: str) -> set[tuple[tuple[str, ...], tuple[str, ...]]]:
    return {
        (
            tuple(column.name for column in constraint.columns),
            tuple(element.target_fullname for element in constraint.elements),
        )
        for constraint in _table(name).constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }


def test_e3_revision_is_dormant_database_only_and_content_minimized() -> None:
    assert 'revision: str = "0013_identity_finalizer_e3"' in MIGRATION
    assert 'down_revision: str | None = "0012_identity_erasure_e2"' in MIGRATION
    assert "Production remains pinned to revision 0006a" in MIGRATION
    assert "No API, BFF, UI, Compose command, activation helper" in MIGRATION
    assert "finalizer login remains expired" in MIGRATION

    table = _table(ADMISSION_TABLE)
    assert set(table.c.keys()) == {
        "admission_id",
        "run_id",
        "finalization_id",
        "contract_version",
        "verification_status",
        "document_sha256",
        "document_octets",
        "finalization_commitment",
        "decision_manifest_commitment",
        "receipt_set_commitment",
        "lineage_set_commitment",
        "privacy_closure_set_commitment",
        "auto_expiry_effect_set_commitment",
        "review_receipt_commitment",
        "release_manifest_digest",
        "migration_tool_bundle_digest",
        "core_oci_manifest_digest",
        "core_schema_digest",
        "core_capability_digest",
        "policy_digest",
        "review_signing_key_fingerprint",
        "finalization_signing_key_fingerprint",
        "verifier_bundle_digest",
        "admitted_at",
        "expires_at",
        "consumed_at",
    }
    ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
    assert "JSON" not in ddl
    assert "BYTEA" not in ddl
    assert not {
        "display_name",
        "alias",
        "external_id",
        "source_ref",
        "payload",
        "content",
        "review_signature",
        "finalization_signature",
        "ha_user_id",
        "principal_id",
        "person_id",
    } & set(table.c.keys())


def test_admission_is_exact_one_time_uuidv7_run_binding() -> None:
    table = _table(ADMISSION_TABLE)
    assert list(table.primary_key.columns.keys()) == ["admission_id"]
    assert ("run_id",) in _unique_columns(ADMISSION_TABLE)
    assert ("finalization_id",) in _unique_columns(ADMISSION_TABLE)
    assert ("document_sha256",) in _unique_columns(ADMISSION_TABLE)
    assert ("finalization_commitment",) in _unique_columns(ADMISSION_TABLE)
    assert (
        ("run_id",),
        ("operations.reviewed_identity_migration_runs.run_id",),
    ) in _foreign_keys(ADMISSION_TABLE)

    checks = _checks(ADMISSION_TABLE)
    assert "finalization_id = run_id" in checks
    assert "reviewed-identity-finalizer-admission-v1" in checks
    assert "offline_verified" in checks
    assert "document_octets BETWEEN 2 AND 4194304" in checks
    assert "expires_at <= admitted_at + interval '15 minutes'" in checks
    assert "consumed_at >= admitted_at" in checks
    for column in (
        "document_sha256",
        "finalization_commitment",
        "decision_manifest_commitment",
        "receipt_set_commitment",
        "lineage_set_commitment",
        "privacy_closure_set_commitment",
        "auto_expiry_effect_set_commitment",
        "review_receipt_commitment",
        "release_manifest_digest",
        "migration_tool_bundle_digest",
        "core_oci_manifest_digest",
        "core_schema_digest",
        "core_capability_digest",
        "policy_digest",
        "review_signing_key_fingerprint",
        "finalization_signing_key_fingerprint",
        "verifier_bundle_digest",
    ):
        assert column in checks


def test_pre_e3_bootstrap_manifest_uses_compiled_postgresql_names() -> None:
    table = _table(ADMISSION_TABLE)
    ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
    compiled_names = set(re.findall(r"\bCONSTRAINT ([a-z0-9_]+)", ddl))

    assert compiled_names
    for constraint_name in compiled_names:
        assert f"'{constraint_name}|" in GRANTS
    assert (
        "uq_reviewed_identity_finalizer_admissions_finalization__1d9a"
        in compiled_names
    )
    assert (
        "uq_reviewed_identity_finalizer_admissions_finalization_commitment"
        not in GRANTS
    )


def test_bootstrap_accepts_only_the_exact_empty_metadata_shape() -> None:
    assert "identity_finalizer_admission_preexisting_evidence" in MIGRATION
    assert "identity_finalizer_admission_bootstrap_shape_invalid" in MIGRATION
    assert "actual_columns IS DISTINCT FROM expected_columns" in MIGRATION
    assert "table_row.relowner = owner_oid" in MIGRATION
    assert "table_row.relrowsecurity" in MIGRATION
    assert "actual_row_security IS DISTINCT FROM false" in MIGRATION
    assert "table_row.relforcerowsecurity" in MIGRATION
    assert "actual_force_row_security IS DISTINCT FROM false" in MIGRATION
    assert "table_row.relhastriggers" in MIGRATION
    assert "actual_has_triggers IS DISTINCT FROM true" in MIGRATION
    assert "actual_trigger_count <> 2" in MIGRATION
    assert "actual_insert_ri_trigger_count <> 1" in MIGRATION
    assert "actual_update_ri_trigger_count <> 1" in MIGRATION
    assert 'pg_catalog."RI_FKey_check_ins"()' in MIGRATION
    assert 'pg_catalog."RI_FKey_check_upd"()' in MIGRATION
    assert "expected_constraints constant text[]" in MIGRATION
    assert "attribute.attidentity::text" in MIGRATION
    assert "attribute.attgenerated::text" in MIGRATION
    assert "constraint_row.contype::text" in MIGRATION
    assert "policy.polcmd::text" in MIGRATION
    assert "pk_reviewed_identity_finalizer_admissions|p|true|false|false" in MIGRATION
    assert (
        "uq_reviewed_identity_finalizer_admissions_run_id|u|true|false|false"
        in MIGRATION
    )
    assert re.search(
        r"constraint_row\.contype = 'c'\s+"
        r"AND constraint_row\.connoinherit",
        MIGRATION,
    )
    assert "constraint_row.contype IN ('p','u')" in MIGRATION
    assert "constraint_row.contype = 'f'" in MIGRATION
    assert "expected_indexes constant text[]" in MIGRATION
    assert "'class relkind=%s persistence=%s owner_match=%s '" in MIGRATION
    assert "'ri_triggers total=%s insert_exact=%s update_exact=%s'" in MIGRATION
    assert "actual_column_mismatch_positions integer[]" in MIGRATION
    assert "actual_constraint_mismatch_positions integer[]" in MIGRATION
    assert "actual_index_mismatch_positions integer[]" in MIGRATION
    assert "actual_columns_sha256 text" in MIGRATION
    assert "actual_owner_privileges text[]" in MIGRATION
    assert "actual_unexpected_acl_count bigint" in MIGRATION
    assert "'columns count=%s actual_sha256=%s expected_sha256=%s '" in MIGRATION
    assert "'user_triggers=%s rewrites=%s security_labels=%s '" in MIGRATION
    assert "'publications=%s'" in MIGRATION
    bootstrap = MIGRATION.split("def _prepare_admission_table()", 1)[1].split(
        "def _install_shared_write_fence()", 1
    )[0]
    assert "pg_catalog.pg_get_constraintdef(" not in bootstrap
    assert "pg_catalog.pg_get_indexdef(" not in bootstrap
    assert "actual_constraint_definitions" not in bootstrap
    assert "actual_index_definitions" not in bootstrap
    assert "actual_acl text[]" not in bootstrap
    assert MIGRATION.index("identity_finalizer_admission_preexisting_evidence") < (
        MIGRATION.index("DROP TABLE {ADMISSION}")
    )
    assert MIGRATION.index("DROP TABLE {ADMISSION}") < MIGRATION.index(
        "op.execute(CreateTable(schema.reviewed_identity_finalizer_admissions))"
    )


def test_role_admission_allows_only_the_reviewed_role_wide_settings() -> None:
    assert "'log_parameter_max_length_on_error=0'" in MIGRATION
    assert "pg_catalog.pg_db_role_setting" in MIGRATION
    assert "AND setdatabase <> 0" in MIGRATION


def test_sql_conditional_expression_is_not_schema_qualified() -> None:
    assert "pg_catalog.coalesce" not in MIGRATION


def test_evidence_policy_identifiers_cannot_truncate_or_collide() -> None:
    select_policy = _literal("EVIDENCE_SELECT_POLICY")
    insert_policy = _literal("EVIDENCE_INSERT_POLICY")
    assert select_policy != insert_policy
    assert len(select_policy.encode("utf-8")) <= 63
    assert len(insert_policy.encode("utf-8")) <= 63
    assert "_e3_" in select_policy
    assert "_e3_" in insert_policy
    install = MIGRATION.split("def _install_evidence_policies()", 1)[1].split(
        "def _install_function_and_acl()", 1
    )[0]
    assert "table.replace(" not in install
    assert "EVIDENCE_SELECT_POLICY" in install
    assert "EVIDENCE_INSERT_POLICY" in install
    downgrade = MIGRATION.split("def downgrade()", 1)[1]
    assert "EVIDENCE_SELECT_POLICY" in downgrade
    assert "EVIDENCE_INSERT_POLICY" in downgrade


def test_kernel_schema_privileges_cover_function_compilation_only() -> None:
    install = MIGRATION.split("def _install_function_and_acl()", 1)[1].split(
        "def _validate_dormant_role()", 1
    )[0]
    usage_and_create = (
        "GRANT USAGE, CREATE ON SCHEMA operations TO {KERNEL_ROLE};"
    )
    set_role = "SET LOCAL ROLE {KERNEL_ROLE};"
    reset_role = "RESET ROLE;"
    revoke_create = "REVOKE CREATE ON SCHEMA operations FROM {KERNEL_ROLE};"
    persistent_usage = (
        "GRANT USAGE ON SCHEMA operations TO {FINALIZER_ROLE}, {KERNEL_ROLE};"
    )

    grant_position = install.index(usage_and_create)
    set_role_position = install.index(set_role, grant_position)
    create_position = install.index(
        "CREATE FUNCTION operations.finalize_reviewed_identity_migration(",
        set_role_position,
    )
    reset_position = install.index(reset_role, create_position)
    revoke_position = install.index(revoke_create, reset_position)
    persistent_usage_position = install.index(persistent_usage, revoke_position)

    assert (
        grant_position
        < set_role_position
        < create_position
        < reset_position
        < revoke_position
        < persistent_usage_position
    )
    assert "REVOKE USAGE ON SCHEMA operations FROM {KERNEL_ROLE}" not in install


def test_finalizer_function_has_a_fixed_security_and_byte_boundary() -> None:
    body = _literal("FUNCTION_BODY")
    assert "SECURITY DEFINER" in MIGRATION
    assert "SET search_path = pg_catalog, pg_temp" in MIGRATION
    assert "session_user <> 'home_agent_identity_finalizer'" in body
    assert "current_user <> 'home_agent_identity_finalizer_kernel'" in body
    assert "transaction_isolation') <> 'serializable'" in body
    assert "transaction_read_only') <> 'off'" in body
    assert "pg_catalog.pg_is_in_recovery()" in body
    assert "pg_catalog.octet_length(finalizer_document)" in body
    assert "pg_catalog.sha256(finalizer_document)" in body
    assert body.index("pg_catalog.sha256(finalizer_document)") < body.index(
        "pg_catalog.convert_from(finalizer_document, 'UTF8')::jsonb"
    )
    assert "EXECUTE " not in body
    assert "pg_catalog.clock_timestamp()" in body
    assert "pg_catalog.transaction_timestamp()" in body
    assert "pg_catalog.pg_advisory_xact_lock" in body


def test_grant_replay_pins_the_exact_finalizer_body_digest() -> None:
    body_digest = hashlib.sha256(_literal("FUNCTION_BODY").encode("utf-8")).hexdigest()
    assert body_digest in GRANTS
    assert "pg_catalog.pg_get_functiondef" in GRANTS or "function_row.prosrc" in GRANTS
    assert "pg_catalog.sha256" in GRANTS
    assert "identity finalizer E3 function contract mismatch" in GRANTS


def test_only_the_candidate_projection_surface_can_be_written() -> None:
    body = _literal("FUNCTION_BODY")
    inserted = set(
        re.findall(
            r"(?im)^\s*INSERT INTO\s+([a-z_]+\.[a-z_]+)",
            body,
        )
    )
    assert inserted == {
        "identity.people",
        "identity.aliases",
        "identity.external_recognition_bindings",
        "identity.privacy_directives",
        "identity.legacy_role_labels",
        "identity.legacy_relationship_candidates",
        "privacy.auto_expiry_schedules",
        "operations.outbox",
        "operations.reviewed_identity_migration_item_receipts",
        "operations.reviewed_identity_migration_projection_lineage",
        "operations.reviewed_identity_migration_projection_subjects",
        "operations.reviewed_identity_migration_finalizations",
    }
    updated = set(
        re.findall(
            r"(?im)^\s*UPDATE\s+([a-z_]+\.[a-z_]+)",
            body,
        )
    )
    assert updated == {
        "identity.people",
        "operations.reviewed_identity_finalizer_admissions",
    }
    for forbidden in (
        "identity.principals",
        "identity.ha_user_bindings",
        "identity.confirmation_artifacts",
        "identity.source_entity_bindings",
        "knowledge.fact_versions",
        "knowledge.memory_transactions",
        "knowledge.places",
        "engagement.initiatives",
        "operations.rollout_authorizations",
        "operations.legacy_identity_writer_evidence",
        "operations.privacy_cutover_check_receipts",
        "operations.semantic_authority_cutovers",
    ):
        assert f"INSERT INTO {forbidden}" not in body
        assert f"UPDATE {forbidden}" not in body


def test_document_review_and_finalization_attestations_bind_to_live_evidence() -> None:
    body = _literal("FUNCTION_BODY")
    verifier_bundle_digest = _literal("VERIFIER_BUNDLE_DIGEST")
    assert re.fullmatch(r"[0-9a-f]{64}", verifier_bundle_digest)
    assert verifier_bundle_digest in body
    assert "admission.verifier_bundle_digest" in body
    assert "document -> 'review_attestation' ->> 'review_signature'" in body
    assert "migration_run.review_signature" in body
    assert (
        "document -> 'review_attestation' ->> 'review_signature'\n"
        "           IS DISTINCT FROM"
    ) in body
    assert "migration_run.signing_key_fingerprint" in body
    assert "admission.review_signing_key_fingerprint" in body
    assert "admission.finalization_signing_key_fingerprint" in body
    assert "reviewed-identity-migration-review-v1" in body
    assert "reviewed-identity-migration-finalization-v1" in body
    assert "finalization_signature" in body


def test_required_json_nulls_cannot_bypass_exact_semantic_validation() -> None:
    body = _literal("FUNCTION_BODY")

    for typed_gate in (
        "required_string(key_name)",
        "required_count(key_name)",
        "required_attestation(attestation, key_name)",
        "required_projection_string(key_name)",
        "required_lineage_string(key_name)",
        "required_person_string(key_name)",
        "required_status_string(key_name)",
        "required_alias_string(key_name)",
        "required_recognition_string(key_name)",
        "required_privacy_string(key_name)",
        "required_role_string(key_name)",
        "required_relationship_string(key_name)",
        "required_closure_string(key_name)",
        "required_closure_directive_string(key_name)",
        "required_effect_string(key_name)",
    ):
        assert typed_gate in body
    assert body.count("IS DISTINCT FROM 'string'") >= 15
    assert (
        "IS DISTINCT FROM\n           admission.finalization_commitment" in body
    )
    assert (
        "IS DISTINCT FROM\n           admission.review_receipt_commitment" in body
    )
    assert "IS DISTINCT FROM\n           migration_run.review_signature" in body
    assert "target_projection_id := (target_lineage ->> 'projection_id')::uuid" in body
    assert body.index("required_lineage_string(key_name)") < body.index(
        "target_projection_id := (target_lineage ->> 'projection_id')::uuid"
    )


def test_privacy_closure_and_auto_expiry_are_exact_bidirectional_sets() -> None:
    body = _literal("FUNCTION_BODY")
    for marker in (
        "identity_finalizer_privacy_cardinality_invalid",
        "identity_finalizer_privacy_closure_invalid",
        "identity_finalizer_privacy_closure_set_invalid",
        "identity_finalizer_privacy_closure_shape_invalid",
        "identity_finalizer_privacy_closure_exact_set_invalid",
        "identity_finalizer_auto_expiry_set_invalid",
        "identity_finalizer_auto_expiry_shape_invalid",
        "identity_finalizer_auto_expiry_exact_set_invalid",
        "schedule_id' <> target_directive_id::text",
        "p ->> 'receipt_id' = target_effect ->> 'outbox_id'",
        "privacy.person.auto_expire",
    ):
        assert marker in body
    assert body.count("target_closure -> 'directives'") >= 4
    assert "SELECT pg_catalog.count(DISTINCT effect ->> 'schedule_id')" in body
    assert "SELECT pg_catalog.count(DISTINCT effect ->> 'outbox_id')" in body
    assert "SELECT pg_catalog.count(DISTINCT effect ->> 'directive_id')" in body
    assert "SELECT pg_catalog.count(DISTINCT effect ->> 'effect_commitment')" in body


def test_zero_apply_reviewed_run_is_a_valid_content_free_finalization() -> None:
    body = _literal("FUNCTION_BODY")
    finalization_checks = _checks("reviewed_identity_migration_finalizations")

    assert "apply_decision_count BETWEEN 0 AND decision_count" in finalization_checks
    assert "receipt_count = apply_decision_count" in finalization_checks
    assert "projection_count <> apply_decision_count" in body
    assert "(projection_count > 0 AND person_count = 0)" in body
    assert (
        "candidate_unverified', false, source_count, decision_count,\n"
        "    apply_decision_count, apply_decision_count"
    ) in body
    assert "IF projection_count = 0" not in body
    assert "apply_decision_count = 0" not in body


def test_exact_replay_is_read_only_complete_and_refuses_drift() -> None:
    body = _literal("FUNCTION_BODY")
    replay = body.split("IF exact_replay THEN", 1)[1].split(
        "FOR target_projection IN", 1
    )[0]
    assert "INSERT INTO" not in replay
    assert "UPDATE " not in replay
    for marker in (
        "identity_finalizer_consumed_admission_drift",
        "identity_finalizer_consumed_lineage_drift",
        "identity_finalizer_consumed_person_drift",
        "identity_finalizer_consumed_status_drift",
        "identity_finalizer_consumed_alias_drift",
        "identity_finalizer_consumed_recognition_drift",
        "identity_finalizer_consumed_privacy_drift",
        "identity_finalizer_consumed_role_drift",
        "identity_finalizer_consumed_relationship_drift",
        "identity_finalizer_consumed_effect_set_drift",
        "identity_finalizer_consumed_effect_drift",
        "admission.consumed_at IS DISTINCT FROM finalized_marker",
        "receipt.database_transaction_id = current_txid",
        "receipt.applied_at = finalized_marker",
        "lineage.linked_at = finalized_marker",
    ):
        assert marker in body


def test_shared_write_fence_orders_every_tombstone_and_finalization() -> None:
    body = _literal("FUNCTION_BODY")
    assert body.index("privacy.lock_identity_semantic_write_fence()") < body.index(
        "reviewed_identity_finalizer_admissions AS candidate"
    )
    assert "CREATE TABLE {FENCE_TABLE}" in MIGRATION
    assert "fence_key = 'global' AND fence_generation = 1" in MIGRATION
    assert "UPDATE privacy.identity_semantic_write_fence" in MIGRATION
    assert "CREATE TRIGGER subject_retrieval_blocks_identity_write_fence" in MIGRATION
    assert "BEFORE INSERT OR UPDATE OF person_id" in MIGRATION
    assert "privacy.subject_retrieval_blocks" in MIGRATION
    assert "PERFORM privacy.lock_identity_semantic_write_fence();" in MIGRATION
    assert "GRANT EXECUTE ON FUNCTION {FENCE_FUNCTION} TO {KERNEL_ROLE}" in MIGRATION


def test_roles_rls_and_acl_remain_dormant_and_non_delegable() -> None:
    for marker in (
        "login_role.rolcanlogin",
        "NOT login_role.rolinherit",
        "NOT login_role.rolsuper",
        "NOT login_role.rolbypassrls",
        "login_role.rolconnlimit = 1",
        "login_role.rolvaliduntil =",
        "timestamptz '1970-01-01 00:00:00+00'",
        "NOT kernel_role.rolcanlogin",
        "kernel_role.rolconnlimit = 0",
        "ENABLE ROW LEVEL SECURITY",
        "FORCE ROW LEVEL SECURITY",
        "reviewed_identity_finalizer_admissions_e3_kernel_select",
        "reviewed_identity_finalizer_admissions_e3_kernel_update",
        "NOT pg_catalog.pg_has_role(",
        "GRANT EXECUTE ON FUNCTION {FUNCTION} TO {FINALIZER_ROLE}",
    ):
        assert marker in MIGRATION
    assert "GRANT EXECUTE ON FUNCTION {FUNCTION} TO {KERNEL_ROLE}" not in MIGRATION
    assert "GRANT SELECT, INSERT ON TABLE identity.people TO {KERNEL_ROLE}" in MIGRATION
    assert (
        "status, status_source_ref, status_source_version,\n"
        "          status_source_sha256, updated_at\n"
        "        ) ON TABLE identity.people TO {KERNEL_ROLE}"
    ) in MIGRATION
    assert "display_name" not in MIGRATION.split(
        "GRANT UPDATE (", 1
    )[1].split(") ON TABLE identity.people", 1)[0]
    for prohibited in (
        "identity.principals",
        "identity.ha_user_bindings",
        "knowledge.fact_versions",
        "knowledge.memory_transactions",
        "knowledge.places",
        "engagement.initiatives",
    ):
        grant_lines = "\n".join(
            line
            for line in MIGRATION.splitlines()
            if "GRANT " in line or prohibited in line
        )
        assert f"GRANT {prohibited}" not in grant_lines


def test_downgrade_refuses_evidence_and_restores_the_exact_0012_acl() -> None:
    downgrade = MIGRATION.split("def downgrade() -> None:", 1)[1]
    assert "refusing to remove used E3 identity finalizer" in downgrade
    assert "USING ERRCODE = '2BP01'" in downgrade
    assert "reviewed_identity_finalizer_admissions" in downgrade
    assert "reviewed_identity_migration_finalizations" in downgrade
    assert "REVOKE ALL ON FUNCTION {FUNCTION}" in downgrade
    assert "REVOKE USAGE" in downgrade
    assert "{KERNEL_ROLE}" in downgrade
    assert _literal("KERNEL_ROLE") == "home_agent_identity_finalizer_kernel"
    for target in (
        "identity.people",
        "identity.aliases",
        "identity.external_recognition_bindings",
        "identity.privacy_directives",
        "identity.legacy_role_labels",
        "identity.legacy_relationship_candidates",
        "privacy.auto_expiry_schedules",
        "operations.outbox",
        "operations.reviewed_identity_migration_item_receipts",
        "operations.reviewed_identity_migration_projection_lineage",
        "operations.reviewed_identity_migration_projection_subjects",
    ):
        assert target in downgrade
    assert "DROP FUNCTION operations.finalize_reviewed_identity_migration" in downgrade
    assert "DROP TABLE {FENCE_TABLE}" in downgrade
    assert "op.drop_table(" in downgrade
