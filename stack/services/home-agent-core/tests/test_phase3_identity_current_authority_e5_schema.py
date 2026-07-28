from __future__ import annotations

import ast
import hashlib
import importlib.util
import re
from pathlib import Path

from app import schema


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = (
    ROOT / "alembic/versions/0015_identity_current_authority_e5.py"
)
MIGRATION = MIGRATION_PATH.read_text(encoding="utf-8")
MIGRATION_SPEC = importlib.util.spec_from_file_location(
    "identity_current_authority_e5_migration", MIGRATION_PATH
)
assert MIGRATION_SPEC is not None and MIGRATION_SPEC.loader is not None
MIGRATION_MODULE = importlib.util.module_from_spec(MIGRATION_SPEC)
MIGRATION_SPEC.loader.exec_module(MIGRATION_MODULE)


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


def test_e5a_is_dormant_database_only_groundwork() -> None:
    assert 'revision: str = "0015_current_authority_e5a"' in MIGRATION
    assert 'down_revision: str | None = "0014_identity_cutover_e4"' in MIGRATION
    assert "Production remains pinned to revision 0006a" in MIGRATION
    assert "adds no API, BFF, UI, Compose command, activation" in MIGRATION
    assert "principal binding, parent fact, memory, place, initiative" in MIGRATION
    assert "database erasure overlay" in MIGRATION
    assert "not a fresh check of the encrypted external" in MIGRATION
    assert "current privacy directives" in MIGRATION
    assert "legacy writer" in MIGRATION
    assert (
        "operations.evaluate_current_identity_semantic_authority"
        not in schema.metadata.tables
    )
    assert "CREATE TABLE" not in MIGRATION


def test_function_has_one_content_free_input_and_closed_result_contract() -> None:
    body = _literal("FUNCTION_BODY")
    assert (
        "operations.evaluate_current_identity_semantic_authority(uuid)"
        in MIGRATION
    )
    assert "target_promotion_id uuid" in MIGRATION
    assert "RETURNS varchar(32)" in MIGRATION
    assert "SECURITY DEFINER" in MIGRATION
    assert "SET search_path = pg_catalog, pg_temp" in MIGRATION
    assert tuple(MIGRATION_MODULE.RESULTS) == (
        "current_database_authority",
        "promotion_missing",
        "promotion_invalid",
        "ledger_state_missing",
        "ledger_regressed",
        "ledger_diverged",
        "ledger_continuity_invalid",
        "erasure_impact_present",
        "subject_retrieval_blocked",
    )
    assert all(len(result) <= 32 for result in MIGRATION_MODULE.RESULTS)
    for result in MIGRATION_MODULE.RESULTS:
        assert f"'{result}'" in body
    forbidden = (
        "display_name",
        "alias",
        "external_id",
        "ha_user_id",
        "principal_id",
        "coordinates",
        "utterance",
        "exact_text",
        "payload",
        "jsonb",
    )
    assert not any(marker in body.lower() for marker in forbidden)
    assert "RAISE EXCEPTION 'identity_authority_status_role_invalid'" in body
    assert "RAISE EXCEPTION 'identity_authority_status_transaction_invalid'" in body
    assert "RAISE EXCEPTION 'identity_authority_status_input_invalid'" in body


def test_role_transaction_and_input_guards_precede_application_data() -> None:
    body = _literal("FUNCTION_BODY")
    assert "session_user NOT IN (" in body
    assert "'home_agent_binding_operator'" in body
    assert "'home_agent_binding_committer'" in body
    assert "current_user <> 'home_agent_identity_authority_kernel'" in body
    assert (
        "session_user, 'home_agent_identity_authority_kernel', 'SET'" in body
    )
    assert "transaction_isolation') <> 'serializable'" in body
    assert "transaction_read_only') <> 'off'" in body
    assert "pg_catalog.pg_is_in_recovery()" in body
    assert "target_promotion_id IS NULL" in body
    assert "substring(target_promotion_id::text, 15, 1) <> '7'" in body
    assert "pg_catalog.set_config('statement_timeout', '15s', true)" in body
    assert "pg_catalog.set_config('lock_timeout', '5s', true)" in body
    assert (
        "'idle_in_transaction_session_timeout', '15s', true"
        in body
    )
    assert "pg_catalog.set_config('transaction_timeout', '30s', true)" in body
    fence = body.index("privacy.lock_identity_semantic_write_fence()")
    assert body.index("identity_authority_status_input_invalid") < fence
    assert body.index("First application-data MVCC operation") < fence
    assert body.index("'transaction_timeout', '30s', true") < fence
    assert fence < body.index(
        "FROM operations.semantic_authority_promotions AS stored_promotion"
    )


def test_impact_and_subject_races_use_the_two_existing_ordering_protocols() -> None:
    body = _literal("FUNCTION_BODY")
    fence = body.index("privacy.lock_identity_semantic_write_fence()")
    promotion = body.index(
        "FROM operations.semantic_authority_promotions AS stored_promotion"
    )
    advisory = body.index("pg_catalog.pg_advisory_xact_lock")
    run_update = body.index(
        "UPDATE operations.reviewed_identity_migration_runs AS locked_run"
    )
    impact = body.index(
        "FROM operations.reviewed_identity_migration_erasure_impacts"
    )
    subject = body.index(
        "FROM operations.reviewed_identity_migration_projection_subjects"
    )
    assert fence < promotion < advisory < run_update < impact < subject
    assert "72102026" in body
    assert "pg_catalog.hashtext(e5_promotion.run_id::text)" in body
    assert "SET expires_at = locked_run.expires_at" in body
    assert "e5_affected_rows <> 1" in body
    assert "reviewed_identity_migration_erasure_replay_guard" in MIGRATION
    assert "subject_retrieval_blocks_identity_write_fence" in MIGRATION
    assert "trigger_row.tgqual IS NULL" in MIGRATION
    assert "trigger_row.tgfoid = impact_guard_oid" in MIGRATION
    assert "trigger_row.tgfoid = fence_trigger_oid" in MIGRATION
    assert MIGRATION_MODULE.IMPACT_GUARD_BODY_SHA256 in MIGRATION
    assert MIGRATION_MODULE.FENCE_LOCK_BODY_SHA256 in MIGRATION
    assert MIGRATION_MODULE.FENCE_TRIGGER_BODY_SHA256 in MIGRATION
    assert MIGRATION_MODULE.PERSON_BLOCK_BODY_SHA256 in MIGRATION
    assert "CREATE TRIGGER" not in MIGRATION
    assert body.count("FOR SHARE") == 1
    assert "FOR SHARE OF current_ledger" in body
    assert (
        "PostgreSQL requires UPDATE\n"
        "  -- authority for row locks"
    ) in body


def test_historical_promotion_graph_is_fully_revalidated() -> None:
    body = _literal("FUNCTION_BODY")
    for relation in (
        "operations.semantic_authority_promotions",
        "operations.reviewed_identity_cutover_admissions",
        "operations.reviewed_identity_migration_runs",
        "operations.reviewed_identity_migration_finalizations",
        "operations.reviewed_identity_finalizer_admissions",
        "operations.semantic_authority_cutovers",
        "operations.enforced_legacy_identity_writer_freezes",
        "operations.legacy_identity_writer_evidence",
        "operations.privacy_cutover_check_receipts",
    ):
        assert relation in body
    for marker in (
        "reviewed-identity-semantic-cutover-v1",
        "historical_authoritative_commit",
        "reviewed-identity-cutover-admission-v1",
        MIGRATION_MODULE.E4_VERIFIER_BUNDLE_DIGEST,
        "offline_verified",
        "reviewed-identity-migration-run-v1",
        "reviewed-identity-migration-finalization-v1",
        "candidate_unverified",
        "semantic-authority-cutover-candidate-v1",
        "candidate_unenforced",
        "enforced-legacy-identity-writer-freeze-v1",
        "enforced_offline",
        "write_probe_result <> 'blocked'",
        "semantic_write_status <> 'frozen'",
        "enforced_cutoff",
        "nonauthoritative_only",
        "integrity_result <> 'passed'",
        "checkpoint_result <> 'complete'",
        "journal_result <> 'clean'",
    ):
        assert marker in body
    assert "e5_admission.consumed_at" in body
    assert "e5_promotion.committed_at" in body
    assert "e5_finalizer_admission.consumed_at" in body
    assert "e5_finalization.finalized_at" in body
    for category in (
        "ingress",
        "retrieval",
        "prompt",
        "initiative",
        "export",
        "edge_block",
    ):
        assert f"('{category}'::text" in body
    assert "e5_privacy_count <> 6 OR e5_privacy_category_count <> 6" in body
    assert "stored_privacy_receipt.check_result = 'passed'" in body
    assert "stored_privacy_receipt.residual_code = 'none'" in body


def test_ledger_currentness_is_monotone_contiguous_and_db_local() -> None:
    body = _literal("FUNCTION_BODY")
    ledger = body.index(
        "FROM operations.erasure_ledger_state AS current_ledger"
    )
    impact = body.index(
        "FROM operations.reviewed_identity_migration_erasure_impacts"
    )
    assert ledger < impact
    assert "FOR SHARE OF current_ledger" in body
    assert (
        "e5_ledger_state.recorded_epoch < "
        "e5_promotion.erasure_ledger_epoch" in body
    )
    assert (
        "e5_ledger_state.recorded_epoch = "
        "e5_promotion.erasure_ledger_epoch" in body
    )
    assert (
        "e5_ledger_state.recorded_head_hash\n"
        "         IS DISTINCT FROM e5_promotion.erasure_ledger_head_hash"
        in body
    )
    assert "FROM operations.erasure_replay_receipts AS starting_receipt" in body
    assert "FROM operations.erasure_replay_receipts AS later_receipt" in body
    assert "FROM operations.erasure_replay_receipts AS final_receipt" in body
    assert (
        "e5_ledger_state.recorded_epoch\n"
        "           - e5_promotion.erasure_ledger_epoch"
        in body
    )
    assert "repeat('0', 64)" in body
    precedence = [
        body.index("RETURN 'ledger_state_missing'"),
        body.index("RETURN 'ledger_regressed'"),
        body.index("RETURN 'ledger_diverged'"),
        body.index("RETURN 'ledger_continuity_invalid'"),
        body.index("RETURN 'erasure_impact_present'"),
        body.index("RETURN 'subject_retrieval_blocked'"),
        body.index("RETURN 'current_database_authority'"),
    ]
    assert precedence == sorted(precedence)


def test_function_has_no_durable_result_or_semantic_write() -> None:
    body = _literal("FUNCTION_BODY")
    inserted = re.findall(
        r"(?im)^\s*INSERT INTO\s+([a-z_]+\.[a-z_]+)", body
    )
    deleted = re.findall(
        r"(?im)^\s*DELETE FROM\s+([a-z_]+\.[a-z_]+)", body
    )
    updated = re.findall(
        r"(?im)^\s*UPDATE\s+([a-z_]+\.[a-z_]+)", body
    )
    assert inserted == []
    assert deleted == []
    assert updated == ["operations.reviewed_identity_migration_runs"]
    for forbidden in (
        "identity.principals",
        "identity.ha_user_bindings",
        "identity.privacy_directives",
        "knowledge.fact_versions",
        "knowledge.memory_transactions",
        "knowledge.places",
        "engagement.initiatives",
    ):
        assert forbidden not in body


def test_rls_grants_and_function_body_are_exactly_scoped() -> None:
    assert (
        'CALLER_ROLE = "home_agent_binding_operator"' in MIGRATION
    )
    assert (
        'KERNEL_ROLE = "home_agent_identity_authority_kernel"' in MIGRATION
    )
    assert "NOT kernel_role.rolcanlogin" in MIGRATION
    assert "NOT kernel_role.rolinherit" in MIGRATION
    assert "NOT kernel_role.rolbypassrls" in MIGRATION
    assert "caller_role.rolconnlimit = 8" in MIGRATION
    assert "caller_role.rolvaliduntil IS NULL" in MIGRATION
    assert "'statement_timeout=15s'" in MIGRATION
    assert "'lock_timeout=5s'" in MIGRATION
    assert "'idle_in_transaction_session_timeout=15s'" in MIGRATION
    assert "'transaction_timeout=30s'" in MIGRATION
    assert "kernel_role.rolconnlimit = 0" in MIGRATION
    assert "AND NOT admin_option" in MIGRATION
    assert "AND NOT inherit_option" in MIGRATION
    assert "AND set_option" in MIGRATION
    assert "setdatabase <> 0" in MIGRATION
    assert "pg_catalog.pg_type AS object_row" in MIGRATION
    assert "pg_catalog.pg_parameter_acl AS object_row" in MIGRATION
    assert "CREATE POLICY {SELECT_POLICY}" in MIGRATION
    assert "CREATE POLICY {RUN_LOCK_POLICY}" in MIGRATION
    assert "GRANT UPDATE (expires_at) ON TABLE {RUN}" in MIGRATION
    assert "GRANT UPDATE (updated_at) ON TABLE {LEDGER_STATE}" in MIGRATION
    assert "GRANT INSERT" not in MIGRATION
    assert "GRANT DELETE" not in MIGRATION
    assert "GRANT EXECUTE ON FUNCTION {FUNCTION} TO {CALLER_ROLE}" in MIGRATION
    assert (
        "GRANT EXECUTE ON FUNCTION {FUNCTION} TO home_agent_api"
        not in MIGRATION
    )
    body = _literal("FUNCTION_BODY")
    assert hashlib.sha256(body.encode("utf-8")).hexdigest() == (
        MIGRATION_MODULE.FUNCTION_BODY_SHA256
    )
    assert "function_row.prosrc" in MIGRATION
    assert "identity_authority_e5_function_invalid" in MIGRATION


def test_downgrade_removes_only_e5_overlay_and_preserves_e4_rows() -> None:
    downgrade = MIGRATION.split("def downgrade() -> None:", 1)[1]
    assert "E5 owns no durable rows" in downgrade
    assert "historical E4" in downgrade
    assert (
        "DROP FUNCTION\n"
        "          operations.evaluate_current_identity_semantic_authority(uuid)\n"
        "          RESTRICT" in downgrade
    )
    assert "DROP POLICY {RUN_LOCK_POLICY} ON {RUN}" in downgrade
    assert "DROP POLICY {SELECT_POLICY} ON {table}" in downgrade
    assert "DROP TABLE" not in downgrade
    assert "DELETE FROM" not in downgrade
    assert "UPDATE operations.semantic_authority_promotions" not in downgrade
    assert "DROP ROLE" not in downgrade
    assert "FROM {KERNEL_ROLE}, {CALLER_ROLE}" in downgrade
