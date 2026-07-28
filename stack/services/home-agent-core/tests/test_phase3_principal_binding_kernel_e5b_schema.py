from __future__ import annotations

import ast
import hashlib
import importlib.util
import re
from pathlib import Path

from app import schema


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = (
    ROOT / "alembic/versions/0016_principal_binding_kernel_e5b.py"
)
MIGRATION = MIGRATION_PATH.read_text(encoding="utf-8")
E5A_MIGRATION = (
    ROOT / "alembic/versions/0015_identity_current_authority_e5.py"
).read_text(encoding="utf-8")
E2_MIGRATION = (
    ROOT / "alembic/versions/0012_identity_person_erasure_tombstone.py"
).read_text(encoding="utf-8")
RUNTIME_TEST = (
    ROOT
    / "tests/test_phase3_principal_binding_kernel_e5b_runtime_postgres.py"
)
MIGRATION_SPEC = importlib.util.spec_from_file_location(
    "principal_binding_kernel_e5b_migration", MIGRATION_PATH
)
assert MIGRATION_SPEC is not None and MIGRATION_SPEC.loader is not None
MIGRATION_MODULE = importlib.util.module_from_spec(MIGRATION_SPEC)
MIGRATION_SPEC.loader.exec_module(MIGRATION_MODULE)


def _literal(name: str) -> str:
    return _source_literal(MIGRATION, name)


def _source_literal(source: str, name: str) -> str:
    tree = ast.parse(source)
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


def test_e5b_is_dormant_database_only_groundwork() -> None:
    assert 'revision: str = "0016_principal_binding_e5b"' in MIGRATION
    assert len(MIGRATION_MODULE.revision) <= 32
    assert 'down_revision: str | None = "0015_current_authority_e5a"' in MIGRATION
    assert "Production remains pinned to revision 0006a" in MIGRATION
    assert "adds no API, BFF, UI, Compose command, OAuth adapter" in MIGRATION
    assert "E5c" in MIGRATION
    assert "separate E5c\nactivation boundary" in MIGRATION
    assert (
        "operations.principal_binding_authority_receipts"
        not in schema.metadata.tables
    )
    assert "authority_receipt_id" not in schema.ha_user_bindings.c


def test_exact_function_signature_role_and_transaction_boundary() -> None:
    body = _literal("FUNCTION_BODY")
    assert MIGRATION_MODULE.FUNCTION == (
        "identity.commit_authenticated_principal_binding_e5b("
        "uuid,character varying,character varying,uuid,uuid,uuid,uuid,uuid)"
    )
    assert (
        "identity.commit_authenticated_principal_binding_e5b(\n"
        "          target_proposal_id uuid,\n"
        "          authenticated_ha_user_id varchar(64),\n"
        "          target_proposal_digest varchar(64),\n"
        "          confirmation_nonce uuid,\n"
        "          new_authority_receipt_id uuid,\n"
        "          new_principal_id uuid,\n"
        "          new_confirmation_artifact_id uuid,\n"
        "          new_binding_id uuid"
    ) in MIGRATION
    assert "RETURNS timestamptz" in MIGRATION
    assert "SECURITY DEFINER" in MIGRATION
    assert "SET search_path = pg_catalog, pg_temp" in MIGRATION
    assert "session_user <> 'home_agent_binding_committer'" in body
    assert "current_user <> 'home_agent_identity_binding_kernel'" in body
    assert (
        "session_user, 'home_agent_identity_binding_kernel', 'SET'" in body
    )
    assert "transaction_isolation') <> 'serializable'" in body
    assert "transaction_read_only') <> 'off'" in body
    assert "pg_catalog.pg_is_in_recovery()" in body
    assert "pg_catalog.pg_current_xact_id_if_assigned() IS NOT NULL" in body
    assert body.index(
        "pg_catalog.pg_current_xact_id_if_assigned() IS NOT NULL"
    ) < body.index(
        "operations.evaluate_current_identity_semantic_authority("
    )
    assert "pg_catalog.set_config('statement_timeout', '15s', true)" in body
    assert "pg_catalog.set_config('lock_timeout', '5s', true)" in body
    assert (
        "'idle_in_transaction_session_timeout', '15s', true" in body
    )
    assert "pg_catalog.set_config('transaction_timeout', '30s', true)" in body
    assert body.index(
        "privacy.lock_identity_semantic_write_fence()"
    ) < body.index(
        "FROM identity.principal_binding_proposals AS proposal"
    )
    assert body.index(
        "privacy.lock_identity_semantic_write_fence()"
    ) < body.index(
        "FROM operations.semantic_authority_promotions AS promotion"
    )
    assert (
        "proposal.proposal_id = target_proposal_id" in body
    )


def test_semantic_fence_precedes_resolution_and_e5a_precedes_graph_reads() -> None:
    body = _literal("FUNCTION_BODY")
    fence = body.index("privacy.lock_identity_semantic_write_fence()")
    proposal = body.index(
        "FROM identity.principal_binding_proposals AS proposal"
    )
    promotion = body.index(
        "FROM operations.semantic_authority_promotions AS promotion"
    )
    authority = body.index(
        "operations.evaluate_current_identity_semantic_authority("
    )
    assert body.index("first application-data operation") < fence
    assert fence < proposal < promotion < authority
    for marker in (
        "FROM identity.principal_binding_requests",
        "FROM identity.people",
        "FROM operations.reviewed_identity_migration_projection_lineage",
        "FROM identity.privacy_directives",
        "FROM identity.edge_privacy_user_blocks",
        "FROM identity.ha_user_bindings",
        "FROM identity.confirmation_artifacts",
    ):
        assert authority < body.index(marker)
    assert (
        "e5b_authority_result IS DISTINCT FROM "
        "'current_database_authority'" in body
    )
    assert "principal_binding_e5b_authority_not_current" in body
    assert "e5b_operation_time >= e5b_proposal.expires_at" in body
    assert "USING ERRCODE = '55000'" in body
    assert "e5b_authority_result" not in body.split(
        "RAISE EXCEPTION 'principal_binding_e5b_authority_not_current'", 1
    )[1].split(";", 1)[0]


def test_caller_and_dependency_authority_contracts_are_revalidated() -> None:
    preconditions = MIGRATION.split(
        "DO $e5b_preconditions$", 1
    )[1].split("$e5b_preconditions$", 1)[0]
    for marker in (
        "caller_role.rolcanlogin",
        "NOT caller_role.rolinherit",
        "NOT caller_role.rolsuper",
        "NOT caller_role.rolbypassrls",
        "caller_role.rolconnlimit = 8",
        "caller_role.rolconfig = ARRAY[",
        "'statement_timeout=15s'",
        "'transaction_timeout=30s'",
        "WHERE roleid = caller_oid",
        "OR member = caller_oid",
        "WHERE setrole = caller_oid",
        "principal_binding_e5b_caller_role_invalid",
    ):
        assert marker in preconditions
    assert "caller_role.rolconfig @>" not in preconditions
    assert "FROM pg_catalog.pg_authid" in preconditions
    assert "OR rolpassword IS NOT NULL" in preconditions
    assert "rolconfig IS NOT NULL" not in preconditions
    assert preconditions.count("'pg_catalog.bool'::regtype") == 2
    assert "'pg_catalog.boolean'::regtype" not in preconditions

    assert MIGRATION_MODULE.AUTHORITY_FUNCTION_BODY_SHA256 == hashlib.sha256(
        _source_literal(E5A_MIGRATION, "FUNCTION_BODY").encode("utf-8")
    ).hexdigest()
    assert MIGRATION_MODULE.FENCE_LOCK_BODY_SHA256 == _source_literal(
        E5A_MIGRATION, "FENCE_LOCK_BODY_SHA256"
    )
    assert MIGRATION_MODULE.FENCE_TRIGGER_BODY_SHA256 == _source_literal(
        E5A_MIGRATION, "FENCE_TRIGGER_BODY_SHA256"
    )
    assert MIGRATION_MODULE.PERSON_BLOCK_BODY_SHA256 == _source_literal(
        E5A_MIGRATION, "PERSON_BLOCK_BODY_SHA256"
    )
    principal_block = re.search(
        r"CREATE FUNCTION privacy\.identity_principal_is_blocked\(.*?"
        r"AS \$function\$(.*?)\$function\$;",
        E2_MIGRATION,
        re.DOTALL,
    )
    assert principal_block is not None
    assert MIGRATION_MODULE.PRINCIPAL_BLOCK_BODY_SHA256 == hashlib.sha256(
        principal_block.group(1).encode("utf-8")
    ).hexdigest()
    for marker in (
        "function_row.proowner = authority_kernel_oid",
        "function_row.prosecdef",
        "function_row.provolatile = 'v'",
        "'pg_catalog.varchar'::regtype",
        "{AUTHORITY_FUNCTION_BODY_SHA256}",
        "acl.grantee = caller_oid",
        "acl.grantee = authority_kernel_oid",
        "acl.grantor = authority_kernel_oid",
        "principal_binding_e5b_authority_dependency_invalid",
        "fence_lock_oid",
        "fence_trigger_oid",
        "person_block_oid",
        "principal_block_oid",
        "{FENCE_LOCK_BODY_SHA256}",
        "{FENCE_TRIGGER_BODY_SHA256}",
        "{PERSON_BLOCK_BODY_SHA256}",
        "{PRINCIPAL_BLOCK_BODY_SHA256}",
        "installed.proowner = expected.function_owner",
        "installed.prosecdef",
        "installed.prorettype = expected.return_type",
        "installed.prosrc, 'UTF8'",
        "principal_binding_e5b_privacy_dependency_invalid",
    ):
        assert marker in preconditions
    assert "home_agent_identity_erasure_kernel" in (
        MIGRATION_MODULE.ALL_REVOKED_ROLES
    )
    assert MIGRATION_MODULE.KERNEL_ROLE in MIGRATION_MODULE.ALL_REVOKED_ROLES
    assert (
        MIGRATION_MODULE.KERNEL_ROLE
        not in MIGRATION_MODULE.FUNCTION_REVOKED_ROLES
    )
    assert set(MIGRATION_MODULE.FUNCTION_REVOKED_ROLES) == (
        set(MIGRATION_MODULE.ALL_REVOKED_ROLES)
        - {MIGRATION_MODULE.KERNEL_ROLE}
    )


def test_receipt_has_exact_immutable_normalized_authority_shape() -> None:
    create = MIGRATION.split(
        "CREATE TABLE {RECEIPT} (", 1
    )[1].split(
        "ALTER TABLE identity.ha_user_bindings", 1
    )[0]
    expected = {
        "receipt_id",
        "binding_id",
        "proposal_id",
        "operator_request_id",
        "request_id",
        "ha_user_id",
        "person_id",
        "person_snapshot_digest",
        "proposal_digest",
        "stage_receipt_digest",
        "proposal_staged_at",
        "proposal_expires_at",
        "principal_id",
        "principal_kind",
        "confirmation_artifact_id",
        "confirmation_purpose",
        "confirmation_nonce_sha256",
        "confirmation_issued_at",
        "confirmation_consumed_at",
        "confirmation_expires_at",
        "promotion_id",
        "promotion_run_id",
        "promotion_finalization_id",
        "promotion_policy_digest",
        "promotion_committed_at",
        "projection_lineage_id",
        "projection_decision_kind",
        "projection_table_kind",
        "projection_id",
        "projection_subject_role",
        "proposal_contract_version",
        "policy_version",
        "authority_result",
        "database_transaction_id",
        "evaluated_at",
    }
    assert set(MIGRATION_MODULE.RECEIPT_COLUMNS) == expected
    assert len(MIGRATION_MODULE.RECEIPT_COLUMNS) == len(expected) == 35
    for column in expected:
        assert re.search(
            rf"(?m)^\s{{10}}{re.escape(column)}\s+", create
        )
    assert "json" not in create.lower()
    assert "bytea" not in create.lower()
    for fixed in (
        "principal_kind = 'ha_user'",
        "'ha_user_person_binding.confirm'",
        "projection_decision_kind = 'person'",
        "projection_table_kind = 'identity.people'",
        "projection_id = person_id",
        "projection_subject_role = 'primary'",
        "'principal-binding-proposal-v1'",
        "policy_version = 'home-agent-mvp-v1'",
        "authority_result = 'current_database_authority'",
        "database_transaction_id > 0",
        "promotion_committed_at <= proposal_staged_at",
        "proposal_staged_at <= evaluated_at",
        "evaluated_at < proposal_expires_at",
        "confirmation_issued_at = evaluated_at",
        "confirmation_consumed_at = evaluated_at",
        "evaluated_at + interval '5 minutes'",
    ):
        assert fixed in create
    assert create.count("~ '^[0-9a-f]{{64}}$'") == 5
    for name in (
        "uq_principal_binding_authority_receipts_binding",
        "uq_principal_binding_authority_receipts_proposal",
        "uq_principal_binding_authority_receipts_principal",
        "uq_principal_binding_authority_receipts_confirmation",
    ):
        assert name in create
    assert (
        MIGRATION_MODULE.RECEIPT_BINDING_CONSTRAINT
        == "principal_binding_receipts_e5b_binding_graph"
    )
    assert "CONSTRAINT {RECEIPT_BINDING_CONSTRAINT} UNIQUE" in create


def test_exact_foreign_keys_and_support_uniques_close_the_graph() -> None:
    for marker in (
        "principal_binding_proposals_e5b_exact_authority",
        "confirmation_artifacts_e5b_exact_authority",
        "semantic_authority_promotions_e5b_exact_authority",
        "identity_projection_lineage_e5b_exact_person",
        "uq_confirmation_artifacts_binding_nonce_e5b",
        "fk_principal_binding_authority_receipts_request",
        "fk_principal_binding_authority_receipts_proposal",
        "fk_principal_binding_authority_receipts_principal",
        "fk_principal_binding_authority_receipts_confirmation",
        "fk_principal_binding_authority_receipts_promotion",
        "fk_principal_binding_authority_receipts_lineage",
        "fk_principal_binding_authority_receipts_subject",
        "authority_receipt_id uuid NOT NULL",
        "uq_ha_user_bindings_authority_receipt_id",
        "fk_ha_user_bindings_e5b_authority_receipt",
    ):
        assert marker in MIGRATION
    assert (
        "principal_id, person_id, principal_kind)\n"
        "            REFERENCES identity.principals\n"
        "              (principal_id, person_id, kind)" in MIGRATION
    )
    assert (
        "projection_lineage_id, person_id, projection_subject_role"
        in MIGRATION
    )
    assert (
        "authority_receipt_id, binding_id, proposal_id, ha_user_id,\n"
        "            principal_id, person_id, source_artifact_id, confirmed_at"
        in MIGRATION
    )


def test_confirmation_nonce_is_globally_single_use_across_all_purposes() -> None:
    preconditions = MIGRATION.split(
        "DO $e5b_preconditions$", 1
    )[1].split("$e5b_preconditions$", 1)[0]
    assert (
        "GROUP BY artifact.client_nonce_sha256"
        in preconditions
    )
    assert "HAVING pg_catalog.count(*) > 1" in preconditions
    assert (
        "principal_binding_e5b_existing_nonce_reuse_requires_review"
        in preconditions
    )

    index = MIGRATION.split(
        "CREATE UNIQUE INDEX {GLOBAL_NONCE_INDEX}", 1
    )[1].split(";", 1)[0]
    assert (
        "ON identity.confirmation_artifacts (client_nonce_sha256)"
        in index
    )
    assert "WHERE" not in index

    body = _literal("FUNCTION_BODY")
    collision = body.split(
        "FROM identity.confirmation_artifacts AS artifact", 2
    )[2].split(") THEN", 1)[0]
    assert (
        "artifact.client_nonce_sha256 = e5b_confirmation_nonce_sha256"
        in collision
    )
    assert "artifact.purpose" not in collision

    downgrade = MIGRATION.split("def downgrade()", 1)[1]
    refusal = downgrade.split(
        "DO $e5b_downgrade$", 1
    )[1].split("$e5b_downgrade$", 1)[0]
    assert "FROM identity.confirmation_artifacts" in refusal
    assert "WHERE purpose = 'ha_user_person_binding.confirm'" in refusal
    assert "DROP INDEX identity.{GLOBAL_NONCE_INDEX}" in downgrade


def test_proposal_digests_are_recomputed_with_canonical_byte_contract() -> None:
    body = _literal("FUNCTION_BODY")
    for material in (
        "e5b_person_material",
        "e5b_stage_material",
        "e5b_proposal_material",
    ):
        assert f"pg_catalog.convert_to({material}, 'UTF8')" in body
    assert (
        "Python canonical_json(sort_keys=True,separators=(',',':'),"
        in body
    )
    assert "ensure_ascii=False" in body
    assert body.count("pg_catalog.sha256(") >= 4
    assert (
        "'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"'"
        in body
    )
    person_keys = (
        "contract",
        "display_name",
        "legacy_source_sha256",
        "person_id",
        "privacy_scope",
        "status",
        "status_source_sha256",
    )
    stage_keys = (
        "contract",
        "ha_user_id",
        "operator_request_id",
        "person_id",
        "person_snapshot_digest",
        "policy_digest",
        "policy_version",
        "request_id",
        "review_code",
        "reviewed_display_label",
    )
    proposal_keys = (
        "contract",
        "expires_at",
        "ha_user_id",
        "operator_request_id",
        "person_id",
        "person_snapshot_digest",
        "policy_digest",
        "policy_version",
        "request_id",
        "review_code",
        "reviewed_display_label",
        "stage_receipt_digest",
        "staged_at",
    )
    sections = {
        person_keys: body.split("e5b_person_material :=", 1)[1].split(
            "e5b_expected_person_snapshot_digest :=", 1
        )[0],
        stage_keys: body.split("e5b_stage_material :=", 1)[1].split(
            "e5b_expected_stage_receipt_digest :=", 1
        )[0],
        proposal_keys: body.split("e5b_proposal_material :=", 1)[1].split(
            "e5b_expected_proposal_digest :=", 1
        )[0],
    }
    for keys, section in sections.items():
        positions = [section.index(f'"{key}"') for key in keys]
        assert positions == sorted(positions)


def test_current_privacy_lineage_and_collision_gates_fail_closed() -> None:
    body = _literal("FUNCTION_BODY")
    for marker in (
        "lineage.run_id = e5b_promotion.run_id",
        "lineage.decision_kind = 'person'",
        "lineage.projection_table_kind = 'identity.people'",
        "lineage.projection_id = e5b_proposal.person_id",
        "subject.subject_role = 'primary'",
        "e5b_projection_count <> 1",
        "e5b_person.status <> 'active'",
        "privacy.identity_person_is_blocked(e5b_person.person_id)",
        "'auto_expire', 'do_not_track', 'ignored', 'silent'",
        "edge_block.ha_user_id = authenticated_ha_user_id",
        "edge_block.person_id = e5b_person.person_id",
        "binding.revoked_at IS NULL",
        "ORDER BY binding.binding_id",
        "artifact.client_nonce_sha256 =",
    ):
        assert marker in body
    assert "FOR UPDATE OF proposal" in body
    assert "FOR UPDATE OF request" in body
    assert "FOR UPDATE OF person" not in body
    assert "PostgreSQL row locks" in body
    assert "E5a already holds the global semantic fence" in body


def test_commit_writes_only_the_exact_binding_graph() -> None:
    body = _literal("FUNCTION_BODY")
    inserted = set(
        re.findall(r"(?im)^\s*INSERT INTO\s+([a-z_]+\.[a-z_]+)", body)
    )
    updated = set(
        re.findall(r"(?im)^\s*UPDATE\s+([a-z_]+\.[a-z_]+)", body)
    )
    assert inserted == {
        "identity.principals",
        "identity.confirmation_artifacts",
        "privacy.artifact_registry",
        "operations.principal_binding_authority_receipts",
        "identity.ha_user_bindings",
    }
    assert updated == {
        "identity.principal_binding_proposals",
        "identity.principal_binding_requests",
    }
    assert "DELETE FROM" not in body
    assert "TRUNCATE" not in body
    assert "EXECUTE " not in body
    assert body.index(
        "INSERT INTO operations.principal_binding_authority_receipts"
    ) < body.index("INSERT INTO identity.ha_user_bindings")
    for constraint in (
        "validate_principal_binding_graph_principal_binding_requests",
        "validate_principal_binding_graph_principal_binding_proposals",
        "validate_principal_binding_graph_ha_user_bindings",
        "validate_principal_binding_graph_confirmation_artifacts",
    ):
        assert body.count(constraint) == 2
    assert "IMMEDIATE;" in body
    assert "DEFERRED;" in body


def test_exact_replay_is_bound_to_every_input_and_committed_graph() -> None:
    body = _literal("FUNCTION_BODY")
    replay = body.index("IF e5b_proposal.state = 'consumed' THEN")
    replay_return = body.index("RETURN e5b_receipt.evaluated_at;")
    fresh_insert = body.index("INSERT INTO identity.principals")
    assert replay < replay_return < fresh_insert
    replay_section = body[replay:replay_return]
    for marker in (
        "e5b_receipt.ha_user_id",
        "e5b_receipt.proposal_digest",
        "e5b_receipt.confirmation_nonce_sha256",
        "e5b_receipt.promotion_id",
        "e5b_receipt.promotion_run_id",
        "e5b_receipt.projection_lineage_id",
        "e5b_receipt.receipt_id",
        "new_authority_receipt_id",
        "new_principal_id",
        "new_confirmation_artifact_id",
        "new_binding_id",
        "FROM identity.principals AS principal",
        "principal.person_id = e5b_receipt.person_id",
        "principal.kind = 'ha_user'",
        "principal.status = 'active'",
        "binding.authority_receipt_id",
        "privacy.artifact_registry",
    ):
        assert marker in replay_section
    assert (
        "e5b_receipt.receipt_id\n"
        "          IS DISTINCT FROM new_authority_receipt_id"
        in replay_section
    )
    assert (
        "e5b_receipt.principal_id\n"
        "          IS DISTINCT FROM new_principal_id"
        in replay_section
    )
    assert (
        "e5b_receipt.confirmation_artifact_id\n"
        "          IS DISTINCT FROM new_confirmation_artifact_id"
        in replay_section
    )
    assert (
        "e5b_receipt.binding_id\n"
        "          IS DISTINCT FROM new_binding_id"
        in replay_section
    )


def test_rls_column_acl_and_function_ownership_are_least_authority() -> None:
    assert (
        MIGRATION_MODULE.KERNEL_ROLE
        == "home_agent_identity_binding_kernel"
    )
    assert "NOT rolcanlogin" not in MIGRATION
    for name in (
        "principal_binding_e5b_select",
        "principal_binding_e5b_insert",
        "principal_binding_e5b_update",
        "principal_binding_e5b_suppression",
        "principal_binding_authority_receipts_owner_select",
        "principal_binding_authority_receipts_e5b_select",
        "principal_binding_authority_receipts_e5b_insert",
    ):
        assert name in MIGRATION
    expected_command_policies = sum(
        len(commands)
        for commands in MIGRATION_MODULE.SHARED_POLICY_TABLES.values()
    )
    assert expected_command_policies == 16
    assert len(MIGRATION_MODULE.SUPPRESSION_EXPRESSIONS) == 6
    assert ") <> 22 THEN" in MIGRATION
    assert ") <> 23 THEN" not in MIGRATION
    assert "GRANT SELECT, INSERT ON TABLE {RECEIPT}" not in MIGRATION
    assert 'GRANT SELECT ({", ".join(RECEIPT_COLUMNS)})' in MIGRATION
    assert 'GRANT INSERT ({", ".join(RECEIPT_COLUMNS)})' in MIGRATION
    assert (
        "pg_catalog.has_table_privilege(\n"
        "               '{KERNEL_ROLE}', '{RECEIPT}', 'SELECT'"
        in MIGRATION
    )
    assert "GRANT UPDATE" not in MIGRATION.split(
        "ON identity.people TO {KERNEL_ROLE}", 1
    )[0].rsplit("GRANT SELECT", 1)[-1]
    assert "GRANT CREATE ON SCHEMA identity TO {KERNEL_ROLE}" in MIGRATION
    assert "REVOKE CREATE ON SCHEMA identity FROM {KERNEL_ROLE}" in MIGRATION
    assert "GRANT EXECUTE ON FUNCTION {FUNCTION} TO {CALLER_ROLE}" in MIGRATION


def test_semantic_fence_extensions_have_exact_narrow_trigger_shape() -> None:
    for constant, trigger, table in (
        (
            "PEOPLE_FENCE_TRIGGER",
            "people_principal_binding_e5b_fence",
            "identity.people",
        ),
        (
            "DIRECTIVES_FENCE_TRIGGER",
            "privacy_directives_principal_binding_e5b_fence",
            "identity.privacy_directives",
        ),
        (
            "EDGE_BLOCK_FENCE_TRIGGER",
            "edge_privacy_user_blocks_principal_binding_e5b_fence",
            "identity.edge_privacy_user_blocks",
        ),
    ):
        assert getattr(MIGRATION_MODULE, constant) == trigger
        assert f"CREATE TRIGGER {{{constant}}}" in MIGRATION
        assert trigger in MIGRATION
        assert f"ON {table}" in MIGRATION
    assert MIGRATION.count("privacy.fence_identity_tombstone_write();") == 3
    assert "BEFORE UPDATE OF" in MIGRATION
    assert "BEFORE INSERT OR UPDATE OF" in MIGRATION
    assert "DELETE" not in MIGRATION.split(
        "def _install_fence_extensions()", 1
    )[1].split("def _install_function()", 1)[0]


def test_function_body_and_downgrade_are_pinned_and_fail_closed() -> None:
    body = _literal("FUNCTION_BODY")
    assert hashlib.sha256(body.encode("utf-8")).hexdigest() == (
        MIGRATION_MODULE.FUNCTION_BODY_SHA256
    )
    assert (
        "FUNCTION_BODY_SHA256 = hashlib.sha256(\n"
        '    FUNCTION_BODY.encode("utf-8")\n'
        ").hexdigest()" in MIGRATION
    )
    assert "pg_catalog.convert_to(prosrc, 'UTF8')" in MIGRATION
    downgrade = MIGRATION.split("def downgrade() -> None:", 1)[1]
    assert "refusing to remove populated E5b principal-binding authority" in (
        downgrade
    )
    assert "USING ERRCODE = '2BP01'" in downgrade
    assert downgrade.index("IF EXISTS (SELECT 1 FROM {RECEIPT})") < (
        downgrade.index("REVOKE ALL ON FUNCTION")
    )
    assert "DROP FUNCTION" in downgrade
    assert "DROP ROLE" not in downgrade
    assert "DROP TABLE {RECEIPT}" in downgrade
    assert "DROP COLUMN authority_receipt_id" in downgrade
    assert "REVOKE SELECT ({\", \".join(RECEIPT_COLUMNS)})," in downgrade


def test_runtime_contract_is_parameter_hidden_and_exercises_success() -> None:
    runtime = RUNTIME_TEST.read_text(encoding="utf-8")
    assert runtime.count("create_async_engine(") == runtime.count(
        "hide_parameters=True"
    )
    assert "test_e5b_success_creates_one_exact_graph_and_replays" in runtime
    assert "commit_authenticated_principal_binding_e5b" in runtime
    assert "current_database_authority" in runtime
