from __future__ import annotations

import ast
import hashlib
import importlib.util
import re
from pathlib import Path

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app import schema


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = (
    ROOT / "alembic/versions/0014_identity_semantic_cutover_e4.py"
)
MIGRATION = MIGRATION_PATH.read_text(encoding="utf-8")
RUNTIME_TEST = (
    ROOT
    / "tests/test_phase3_identity_semantic_cutover_e4_runtime_postgres.py"
).read_text(encoding="utf-8")
FREEZE = "enforced_legacy_identity_writer_freezes"
ADMISSION = "reviewed_identity_cutover_admissions"
PROMOTION = "semantic_authority_promotions"
MIGRATION_SPEC = importlib.util.spec_from_file_location(
    "identity_semantic_cutover_e4_migration", MIGRATION_PATH
)
assert MIGRATION_SPEC is not None and MIGRATION_SPEC.loader is not None
MIGRATION_MODULE = importlib.util.module_from_spec(MIGRATION_SPEC)
MIGRATION_SPEC.loader.exec_module(MIGRATION_MODULE)
E4_TABLES = {
    FREEZE: MIGRATION_MODULE.E4_FREEZE_TABLE,
    ADMISSION: MIGRATION_MODULE.E4_ADMISSION_TABLE,
    PROMOTION: MIGRATION_MODULE.E4_PROMOTION_TABLE,
}


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
    return E4_TABLES[name]


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


def test_e4_is_append_only_dormant_database_groundwork() -> None:
    assert 'revision: str = "0014_identity_cutover_e4"' in MIGRATION
    assert 'down_revision: str | None = "0013_identity_finalizer_e3"' in MIGRATION
    assert "Production remains pinned to revision 0006a" in MIGRATION
    assert "No API, BFF, UI, Compose command, activation helper" in MIGRATION
    assert "principal binding, parent fact, memory, place, initiative, action" in (
        MIGRATION
    )
    assert "authoritative_at_commit`` is deliberately historical" in MIGRATION
    for name in (FREEZE, ADMISSION, PROMOTION):
        assert f"operations.{name}" not in schema.metadata.tables
    assert "E4_METADATA = MetaData(" in MIGRATION
    assert "They are never\n# passed to ``CreateTable``." in MIGRATION


def test_strong_writer_freeze_is_exact_signed_and_runtime_append_only() -> None:
    table = _table(FREEZE)
    assert set(table.c.keys()) == {
        "freeze_id",
        "run_id",
        "writer_evidence_id",
        "contract_version",
        "enforcement_status",
        "write_probe_result",
        "semantic_write_status",
        "legacy_context_cutoff_status",
        "recognition_mode",
        "source_installation_id",
        "semantic_generation",
        "source_projection_commitment",
        "e3_source_manifest_commitment",
        "e3_projection_manifest_commitment",
        "e3_commitment_key_epoch",
        "writer_evidence_commitment",
        "trigger_set_commitment",
        "blocked_probe_commitment",
        "release_manifest_digest",
        "freeze_kernel_build_digest",
        "policy_digest",
        "freeze_commitment",
        "signature_algorithm",
        "signing_key_fingerprint",
        "freeze_signature",
        "enforced_at",
        "verified_at",
        "recorded_at",
    }
    assert (
        ("run_id", "writer_evidence_id"),
        (
            "operations.legacy_identity_writer_evidence.run_id",
            "operations.legacy_identity_writer_evidence.evidence_id",
        ),
    ) in _foreign_keys(FREEZE)
    assert ("run_id",) in _unique_columns(FREEZE)
    assert ("writer_evidence_id",) in _unique_columns(FREEZE)
    checks = _checks(FREEZE)
    for marker in (
        "enforced-legacy-identity-writer-freeze-v1",
        "enforced_offline",
        "write_probe_result = 'blocked'",
        "semantic_write_status = 'frozen'",
        "enforced_cutoff",
        "nonauthoritative_only",
        "enforced_at <= verified_at",
        "verified_at <= recorded_at",
        "e3_commitment_key_epoch > 0",
    ):
        assert marker in checks
    freeze_policy = MIGRATION.split(
        "def _create_tables_and_policies()", 1
    )[1].split("def _install_function()", 1)[0]
    assert "enforced_writer_freezes_owner_insert" in freeze_policy
    assert "FOR UPDATE" not in freeze_policy.split(
        "ON {FREEZE}", 1
    )[0]
    assert "GRANT SELECT, INSERT ON TABLE {FREEZE} TO home_agent_owner" in (
        freeze_policy
    )


def test_cutover_admission_is_content_free_short_lived_and_one_time() -> None:
    table = _table(ADMISSION)
    forbidden = {
        "display_name",
        "alias",
        "external_id",
        "source_ref",
        "payload",
        "content",
        "ha_user_id",
        "principal_id",
        "person_id",
        "coordinates",
        "utterance",
    }
    assert not forbidden & set(table.c.keys())
    ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
    assert "JSON" not in ddl
    assert "BYTEA" not in ddl
    assert ("run_id",) in _unique_columns(ADMISSION)
    assert ("promotion_id",) in _unique_columns(ADMISSION)
    assert ("candidate_cutover_id",) in _unique_columns(ADMISSION)
    assert ("writer_freeze_id",) in _unique_columns(ADMISSION)
    checks = _checks(ADMISSION)
    assert "reviewed-identity-cutover-admission-v1" in checks
    assert "offline_verified" in checks
    assert "document_octets BETWEEN 2 AND 1048576" in checks
    assert "semantic_generation > 0" in checks
    assert "e3_commitment_key_epoch > 0" in checks
    assert "erasure_ledger_epoch >= 0" in checks
    assert "erasure_ledger_verification = 'head_matched'" in checks
    assert "expires_at <= admitted_at + interval '15 minutes'" in checks
    assert "consumed_at >= admitted_at" in checks
    for column in (
        "source_installation_id",
        "semantic_generation",
        "e3_source_manifest_commitment",
        "e3_projection_manifest_commitment",
        "e3_commitment_key_epoch",
        "erasure_ledger_epoch",
        "erasure_ledger_head_hash",
        "erasure_ledger_verification",
    ):
        assert column in table.c


def test_promotion_is_one_content_free_historical_authority_receipt() -> None:
    table = _table(PROMOTION)
    assert list(table.primary_key.columns.keys()) == ["promotion_id"]
    for unique in (
        ("authority_scope",),
        ("run_id",),
        ("finalization_id",),
        ("candidate_cutover_id",),
        ("writer_freeze_id",),
        ("admission_id",),
        ("promotion_commitment",),
    ):
        assert unique in _unique_columns(PROMOTION)
    checks = _checks(PROMOTION)
    assert "authority_scope = 'identity_semantics'" in checks
    assert "reviewed-identity-semantic-cutover-v1" in checks
    assert "historical_authoritative_commit" in checks
    assert "authoritative_at_commit = true" in checks
    assert "database_transaction_id > 0" in checks
    assert "cutover_signature ~ '^[0-9a-f]{128}$'" in checks
    for column in (
        "source_installation_id",
        "semantic_generation",
        "e3_source_manifest_commitment",
        "e3_projection_manifest_commitment",
        "e3_commitment_key_epoch",
        "erasure_ledger_epoch",
        "erasure_ledger_head_hash",
        "erasure_ledger_verification",
    ):
        assert column in table.c
    assert "Current authority must additionally prove no later erasure impact" in (
        MIGRATION
    )


def test_cutover_function_has_fixed_byte_security_and_transaction_boundary() -> None:
    body = _literal("FUNCTION_BODY")
    assert "SECURITY DEFINER" in MIGRATION
    assert "SET search_path = pg_catalog, pg_temp" in MIGRATION
    assert (
        "operations.commit_reviewed_identity_cutover(bytea,uuid)" in MIGRATION
    )
    assert "session_user <> 'home_agent_identity_cutover'" in body
    assert "current_user <> 'home_agent_identity_cutover_kernel'" in body
    assert "transaction_isolation') <> 'serializable'" in body
    assert "transaction_read_only') <> 'off'" in body
    assert "pg_catalog.pg_is_in_recovery()" in body
    assert "submitted_cutover_document bytea" in MIGRATION
    assert "target_admission_id uuid" in MIGRATION
    assert "submitted_cutover_document IS NULL" in body
    assert "target_admission_id IS NULL" in body
    assert "pg_catalog.octet_length(submitted_cutover_document) > 1048576" in body
    assert "pg_catalog.sha256(submitted_cutover_document)" in body
    assert body.index("identity_cutover_input_invalid") < body.index(
        "privacy.lock_identity_semantic_write_fence()"
    )
    assert body.index("privacy.lock_identity_semantic_write_fence()") < body.index(
        "pg_catalog.pg_advisory_xact_lock"
    )
    assert body.index("pg_catalog.pg_advisory_xact_lock") < body.index(
        "FROM operations.reviewed_identity_cutover_admissions"
    )
    assert "EXECUTE " not in body
    assert (
        "WHERE candidate_admission.admission_id = target_admission_id" in body
    )


def test_e4_uses_a_separate_expired_nondelegable_role_pair() -> None:
    assert (
        'FINALIZER_ROLE = "home_agent_identity_cutover"' in MIGRATION
    )
    assert (
        'KERNEL_ROLE = "home_agent_identity_cutover_kernel"' in MIGRATION
    )
    for marker in (
        "login_role.rolcanlogin",
        "NOT login_role.rolinherit",
        "NOT login_role.rolbypassrls",
        "login_role.rolconnlimit = 1",
        "timestamptz '1970-01-01 00:00:00+00'",
        "NOT kernel_role.rolcanlogin",
        "kernel_role.rolconnlimit = 0",
        "identity_cutover_e4_pregrant_authority_invalid",
    ):
        assert marker in MIGRATION
    assert (
        "GRANT EXECUTE ON FUNCTION {FUNCTION} TO {FINALIZER_ROLE}" in MIGRATION
    )
    assert (
        "GRANT EXECUTE ON FUNCTION {FUNCTION} TO "
        "home_agent_identity_finalizer"
    ) not in MIGRATION


def test_function_writes_only_promotion_and_admission_consumption() -> None:
    body = _literal("FUNCTION_BODY")
    inserted = set(
        re.findall(r"(?im)^\s*INSERT INTO\s+([a-z_]+\.[a-z_]+)", body)
    )
    updated = set(
        re.findall(r"(?im)^\s*UPDATE\s+([a-z_]+\.[a-z_]+)", body)
    )
    assert inserted == {"operations.semantic_authority_promotions"}
    assert updated == {"operations.reviewed_identity_cutover_admissions"}
    for forbidden in (
        "identity.principals",
        "identity.ha_user_bindings",
        "knowledge.fact_versions",
        "knowledge.memory_transactions",
        "knowledge.places",
        "engagement.initiatives",
        "operations.rollout_authorizations",
        "operations.privacy_cutover_check_receipts",
        "operations.semantic_authority_cutovers",
    ):
        assert f"INSERT INTO {forbidden}" not in body
        assert f"UPDATE {forbidden}" not in body


def test_function_requires_exact_six_privacy_categories_and_strong_freeze() -> None:
    body = _literal("FUNCTION_BODY")
    for category in (
        "ingress",
        "retrieval",
        "prompt",
        "initiative",
        "export",
        "edge_block",
    ):
        assert f"('{category}'::text" in body
    assert "e4_privacy_count <> 6 OR e4_privacy_category_count <> 6" in body
    assert "privacy_receipt.check_result = 'passed'" in body
    assert "privacy_receipt.residual_code = 'none'" in body
    for marker in (
        "enforced_offline",
        "write_probe_result <> 'blocked'",
        "semantic_write_status <> 'frozen'",
        "enforced_cutoff",
        "nonauthoritative_only",
        "e4_writer_evidence.integrity_result <> 'passed'",
        "e4_writer_evidence.checkpoint_result <> 'complete'",
        "e4_writer_evidence.journal_result <> 'clean'",
    ):
        assert marker in body
    assert (
        "e4_writer_freeze.enforced_at < e3_finalization.finalized_at" in body
    )
    assert (
        "e4_writer_freeze.verified_at < e3_finalization.finalized_at" in body
    )
    assert (
        "e4_writer_freeze.e3_source_manifest_commitment" in body
        and "e4_admission.e3_source_manifest_commitment" in body
    )
    assert (
        "e4_writer_freeze.e3_projection_manifest_commitment" in body
        and "e4_admission.e3_projection_manifest_commitment" in body
    )
    assert (
        "e4_writer_freeze.e3_commitment_key_epoch" in body
        and "e4_admission.e3_commitment_key_epoch" in body
    )


def test_erasure_and_subject_blocks_invalidate_commit_and_exact_replay() -> None:
    body = _literal("FUNCTION_BODY")
    assert "identity_cutover_erasure_impact_present" in body
    assert "identity_cutover_subject_blocked" in body
    replay_return = body.index(
        "IF e4_exact_replay THEN\n    RETURN e4_promotion.promotion_id;"
    )
    for marker in (
        "identity_cutover_e3_run_invalid",
        "identity_cutover_writer_freeze_invalid",
        "identity_cutover_writer_evidence_invalid",
        "identity_cutover_erasure_ledger_head_invalid",
        "identity_cutover_privacy_set_invalid",
        "identity_cutover_erasure_impact_present",
        "identity_cutover_subject_blocked",
    ):
        assert body.index(marker) < replay_return
    assert body.index(
        "INSERT INTO operations.semantic_authority_promotions"
    ) > replay_return
    assert body.index(
        "UPDATE operations.reviewed_identity_cutover_admissions "
        "AS target_admission"
    ) > replay_return


def test_e3_and_erasure_ledger_are_exactly_bound_and_ordered() -> None:
    body = _literal("FUNCTION_BODY")
    for marker in (
        "operations.reviewed_identity_migration_runs",
        "e3_run.logical_source_manifest_commitment",
        "e3_run.projection_manifest_commitment",
        "e3_run.commitment_key_epoch",
        "e4_admission.source_installation_id",
        "e4_admission.semantic_generation",
        "operations.erasure_ledger_state AS current_ledger",
        "FOR SHARE OF current_ledger",
        "e4_ledger_state.recorded_epoch",
        "e4_ledger_state.recorded_head_hash",
        "e4_admission.erasure_ledger_epoch",
        "e4_admission.erasure_ledger_head_hash",
    ):
        assert marker in body
    assert (
        '"operations.reviewed_identity_migration_runs",'
        in MIGRATION.split("READ_EVIDENCE_TABLES =", 1)[1]
    )
    assert (
        "GRANT SELECT, UPDATE (updated_at) ON TABLE\n"
        "          operations.erasure_ledger_state TO {KERNEL_ROLE}"
    ) in MIGRATION
    assert (
        "recorded_epoch',\n               'UPDATE'"
        in MIGRATION
    )


def test_function_is_unambiguous_canonical_and_runtime_append_only() -> None:
    body = _literal("FUNCTION_BODY")
    declarations = body.split("BEGIN", 1)[0]
    local_names = re.findall(r"(?m)^\s{2}([a-z][a-z0-9_]*)\s", declarations)
    assert local_names
    assert all(name.startswith(("e3_", "e4_")) for name in local_names)
    assert (
        "UPDATE operations.reviewed_identity_cutover_admissions "
        "AS target_admission"
    ) in body
    assert (
        "WHERE target_admission.admission_id = e4_admission.admission_id"
        in body
    )
    policy_sql = MIGRATION.split(
        "def _create_tables_and_policies()", 1
    )[1].split("def _install_function()", 1)[0]
    assert "FOR UPDATE TO home_agent_owner" not in policy_sql
    assert "FOR DELETE TO home_agent_owner" not in policy_sql
    assert "GRANT UPDATE ON TABLE {FREEZE}" not in policy_sql
    assert "GRANT UPDATE ON TABLE {PROMOTION}" not in policy_sql
    assert "ALTER TABLE {qualified} FORCE ROW LEVEL SECURITY" in policy_sql
    assert (
        "submitted_cutover_document IS DISTINCT FROM pg_catalog.convert_to("
        in body
    )
    assert "e4_document::text, 'UTF8'" in body
    assert "identity_cutover_document_not_canonical" in body
    assert RUNTIME_TEST.count("create_async_engine(") == RUNTIME_TEST.count(
        "hide_parameters=True"
    )


def test_verifier_and_installed_function_body_are_pinned() -> None:
    body = _literal("FUNCTION_BODY")
    verifier_manifest = _literal("VERIFIER_BUNDLE_MANIFEST")
    verifier_digest = _literal("VERIFIER_BUNDLE_DIGEST")
    assert hashlib.sha256(verifier_manifest.encode("utf-8")).hexdigest() == (
        verifier_digest
    )
    assert verifier_digest in body
    assert (
        "FUNCTION_BODY_SHA256 = hashlib.sha256("
        'FUNCTION_BODY.encode("utf-8")'
    ) in MIGRATION
    assert "function_row.prosrc" in MIGRATION
    assert "identity_cutover_e4_function_contract_invalid" in MIGRATION


def test_rls_acl_ownership_and_downgrade_are_fail_closed() -> None:
    for marker in (
        "ENABLE ROW LEVEL SECURITY",
        "FORCE ROW LEVEL SECURITY",
        "identity_cutover_e4_select",
        "identity_cutover_e4_insert",
        "identity_cutover_e4_update",
        "GRANT EXECUTE ON FUNCTION {FUNCTION} TO {FINALIZER_ROLE}",
        "GRANT INSERT ON TABLE {PROMOTION} TO {KERNEL_ROLE}",
        "GRANT SELECT ON TABLE {PROMOTION} TO home_agent_owner",
        "operations.reviewed_identity_migration_projection_subjects",
        "privacy.lock_identity_semantic_write_fence()",
        "privacy.identity_person_is_blocked(uuid)",
        "pg_catalog.has_column_privilege",
        "table_row.relowner <> owner_oid",
        "function_row.proowner = kernel_oid",
        "NOT trigger_row.tgisinternal",
        "rule_row.ev_class IN ({tables})",
    ):
        assert marker in MIGRATION
    assert "GRANT INSERT ON TABLE {PROMOTION} TO home_agent_owner" not in MIGRATION
    downgrade = MIGRATION.split("def downgrade() -> None:", 1)[1]
    assert "refusing to remove E4 semantic-cutover evidence" in downgrade
    assert "USING ERRCODE = '2BP01'" in downgrade
    assert "DROP FUNCTION operations.commit_reviewed_identity_cutover" in downgrade
    assert "GRANT USAGE ON SCHEMA operations TO {KERNEL_ROLE}" in downgrade
    assert (
        "REVOKE CREATE, USAGE ON SCHEMA operations FROM {KERNEL_ROLE}"
        in downgrade
    )
    for table_constant in ("PROMOTION", "ADMISSION", "FREEZE"):
        assert f"SELECT 1 FROM {{{table_constant}}}" in downgrade
