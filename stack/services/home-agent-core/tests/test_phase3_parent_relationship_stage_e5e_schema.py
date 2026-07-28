from __future__ import annotations

import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = (
    ROOT / "alembic/versions/0019_parent_relationship_stage_e5e.py"
)
MIGRATION = MIGRATION_PATH.read_text(encoding="utf-8")
ROLE_CEREMONY = (
    ROOT.parents[1]
    / "home-agent-deploy/provision-parent-relationship-kernel-role.sh"
).read_text(encoding="utf-8")
GRANTS = (
    ROOT.parents[1] / "home-agent-deploy/apply-grants.sh"
).read_text(encoding="utf-8")
API = (ROOT / "app/api.py").read_text(encoding="utf-8")
STORE = (ROOT / "app/store.py").read_text(encoding="utf-8")


def test_e5e_is_a_linear_staging_only_revision() -> None:
    assert 'revision: str = "0019_parent_stage_e5e"' in MIGRATION
    assert 'down_revision: str | None = "0018_parent_relationship_e5d"' in (
        MIGRATION
    )
    assert "Production remains pinned to revision 0006a" in MIGRATION
    assert "It creates no fact" in " ".join(MIGRATION.split())
    assert "private BFF route, and UI remain disabled" in MIGRATION
    assert "INSERT INTO knowledge.fact_versions" not in MIGRATION
    assert "INSERT INTO identity.confirmation_artifacts" not in MIGRATION
    assert "INSERT INTO knowledge.memory_transactions" not in MIGRATION
    assert "INSERT INTO operations.parent_relationship_authority_receipts" not in (
        MIGRATION
    )
    assert "stage_authenticated_parent_relationship_e5e" not in API
    assert "stage_authenticated_parent_relationship_e5e" not in STORE


def test_e5e_function_is_first_statement_serializable_and_fenced() -> None:
    assert "pg_current_xact_id_if_assigned() IS NOT NULL" in MIGRATION
    assert "transaction_isolation') <> 'serializable'" in MIGRATION
    assert "transaction_read_only') <> 'off'" in MIGRATION
    assert "pg_is_in_recovery()" in MIGRATION
    assert "statement_timeout', '15s'" in MIGRATION
    assert "lock_timeout', '5s'" in MIGRATION
    assert "transaction_timeout', '30s'" in MIGRATION

    body = MIGRATION.split('FUNCTION_BODY = rf"""', 1)[1].split('"""', 1)[0]
    fence = body.index("privacy.lock_identity_semantic_write_fence()")
    promotion = body.index(
        "FROM operations.semantic_authority_promotions AS promotion"
    )
    binding = body.index("FROM identity.ha_user_bindings AS binding")
    assert fence < promotion < binding
    assert (
        "operations.evaluate_current_identity_semantic_authority("
        in body
    )


def test_e5e_derives_child_and_exact_parent_set_server_side() -> None:
    assert "binding.ha_user_id = authenticated_ha_user_id" in MIGRATION
    assert "binding.revoked_at IS NULL" in MIGRATION
    assert "principal.person_id = binding.person_id" in MIGRATION
    assert "candidate.parent_person_id" in MIGRATION
    assert "identity.legacy_role_labels AS label" in MIGRATION
    assert "label.role_label = 'parent'" in MIGRATION
    assert "label.perspective = 'unknown'" in MIGRATION
    assert "lineage.run_id = e5e_promotion.run_id" in MIGRATION
    assert "lineage.projection_id = label.label_id" in MIGRATION
    assert "projection_subject.subject_role = 'primary'" in MIGRATION
    assert "IF e5e_count <> 2" in MIGRATION

    signature = MIGRATION.split(
        "CREATE FUNCTION identity."
        "stage_authenticated_parent_relationship_e5e(",
        1,
    )[1].split(")", 1)[0]
    assert "parent_person_id" not in signature
    assert "child_person_id" not in signature
    assert "legacy_label_id" not in signature


def test_e5e_privacy_ambiguity_and_existing_fact_checks_fail_closed() -> None:
    for check in (
        "privacy.identity_person_is_blocked(",
        "privacy.identity_principal_is_blocked(",
        "identity.privacy_directives AS directive",
        "identity.edge_privacy_user_blocks AS edge_block",
        "parent_relationship_e5e_binding_invalid",
        "parent_relationship_e5e_candidate_set_invalid",
        "parent_relationship_e5e_parent_fact_exists",
    ):
        assert check in MIGRATION
    assert "directive.directive IN (" in MIGRATION
    assert "AND directive.enabled" in MIGRATION
    assert "pg_catalog.lower(e5e_parent_labels[1])" in MIGRATION
    assert "predicate = 'parent_of'" in MIGRATION
    assert "pg_catalog.upper_inf(fact.system_range)" in MIGRATION
    assert "fact.resolution = 'accepted'" in MIGRATION


def test_e5e_stages_exactly_two_digest_bound_edges_atomically() -> None:
    for write in (
        "INSERT INTO identity.parent_relationship_requests",
        "INSERT INTO identity.parent_relationship_proposals",
        "INSERT INTO identity.parent_relationship_proposal_edges",
    ):
        assert MIGRATION.count(write) == 1
    assert "FOR e5e_index IN 1..2 LOOP" in MIGRATION
    assert "parent-relationship-authority-v2" in MIGRATION
    assert "parent-candidate-set-v2" in MIGRATION
    assert (
        "cb991031ceed6e7e1ecf3532118ee2ad24f71bf18602b9e4ef2ace50035ed50e"
        in MIGRATION
    )
    assert "required_authority" in MIGRATION
    assert "'explicit_related_party'" in MIGRATION
    assert "'explicit_authority'" in MIGRATION
    assert "'not_applicable'" in MIGRATION
    assert "'accepted', 'private'" in MIGRATION
    assert "e5e_existing.expires_at <= pg_catalog.clock_timestamp()" in MIGRATION
    assert "parent_relationship_e5e_replay_drift" in MIGRATION


def test_e5e_uses_a_table_blind_caller_and_dedicated_no_login_kernel() -> None:
    assert "session_user <> '{CALLER_ROLE}'" in MIGRATION
    assert "current_user <> '{KERNEL_ROLE}'" in MIGRATION
    assert 'CALLER_ROLE = "home_agent_binding_committer"' in MIGRATION
    assert 'KERNEL_ROLE = "home_agent_parent_relationship_kernel"' in MIGRATION
    assert "SECURITY DEFINER" in MIGRATION
    assert "SET search_path = pg_catalog" in MIGRATION
    assert "SET row_security = on" in MIGRATION
    assert (
        "ALTER FUNCTION {FUNCTION} OWNER TO {KERNEL_ROLE}" in MIGRATION
    )
    assert "GRANT EXECUTE ON FUNCTION {FUNCTION} TO {CALLER_ROLE}" in MIGRATION
    assert "home_agent_api" in MIGRATION
    assert "home_agent_binding_operator" in MIGRATION

    assert (
        "CREATE ROLE home_agent_parent_relationship_kernel NOLOGIN"
        in ROLE_CEREMONY
    )
    for attribute in (
        "NOLOGIN",
        "NOSUPERUSER",
        "NOCREATEDB",
        "NOCREATEROLE",
        "NOREPLICATION",
        "NOINHERIT",
        "NOBYPASSRLS",
        "CONNECTION LIMIT 0",
    ):
        assert attribute in ROLE_CEREMONY
    assert "PASSWORD NULL" in ROLE_CEREMONY
    assert "pg_catalog.pg_terminate_backend" in ROLE_CEREMONY
    assert "pg_catalog.pg_default_acl" in ROLE_CEREMONY
    assert "pg_catalog.pg_parameter_acl" in ROLE_CEREMONY
    assert "requires revision 0018" in ROLE_CEREMONY


def test_embedded_function_body_digest_matches_source() -> None:
    namespace: dict[str, object] = {}
    compiled = compile(MIGRATION, str(MIGRATION_PATH), "exec")

    class _Alembic:
        op = object()

    import sys
    import types

    previous = sys.modules.get("alembic")
    module = types.ModuleType("alembic")
    module.op = _Alembic.op
    try:
        sys.modules["alembic"] = module
        exec(compiled, namespace)
    finally:
        if previous is None:
            sys.modules.pop("alembic", None)
        else:
            sys.modules["alembic"] = previous

    body = namespace["FUNCTION_BODY"]
    assert isinstance(body, str)
    assert namespace["FUNCTION_BODY_SHA256"] == hashlib.sha256(
        body.encode("utf-8")
    ).hexdigest()


def test_grant_replay_quarantines_then_restores_only_exact_stage_execute() -> None:
    assert (
        GRANTS.index("DO $parent_relationship_e5e_function_quarantine$")
        < GRANTS.index(
            "version_num = '0019_parent_stage_e5e'"
        )
        < GRANTS.index("DO $parent_relationship_e5e_active_acl$")
    )
    quarantine = GRANTS.split(
        "DO $parent_relationship_e5e_function_quarantine$", 1
    )[1].split("$parent_relationship_e5e_function_quarantine$;", 1)[0]
    assert "stage_authenticated_parent_relationship_e5e" in quarantine
    assert "SELECT role_row.rolname FROM pg_catalog.pg_roles" in quarantine
    assert "UNION ALL SELECT 'PUBLIC'" in quarantine
    assert "REVOKE ALL PRIVILEGES ON FUNCTION %s FROM %s CASCADE" in quarantine

    activation = GRANTS.split(
        "\\if :activate_parent_relationship_stage_e5e", 1
    )[1].split("\\endif", 1)[0]
    assert "GRANT USAGE ON SCHEMA identity TO home_agent_binding_committer" in (
        activation
    )
    assert "SET ROLE home_agent_parent_relationship_kernel" in activation
    assert "stage_authenticated_parent_relationship_e5e" in activation
    assert "TO home_agent_binding_committer" in activation
    assert "has_function_privilege" in activation
    assert "pg_catalog.pg_auth_members" in activation
    assert "pg_catalog.pg_attribute" in activation
    assert "parent relationship E5e active ACL contract mismatch" in activation

    table_quarantine = GRANTS.split(
        "DO $parent_relationship_e5d_early_quarantine$", 1
    )[1].split("$parent_relationship_e5d_early_quarantine$;", 1)[0]
    assert "current_revision = '0019_parent_stage_e5e'" in (
        table_quarantine
    )
    assert "home_agent_parent_relationship_kernel" in table_quarantine
    assert "operations.parent_relationship_authority_receipts" in (
        table_quarantine
    )


def test_grant_replay_projects_e5e_out_of_pinned_predecessor_catalogs() -> None:
    e3 = GRANTS.split("DO $identity_finalizer_e3_acl$", 1)[1].split(
        "$identity_finalizer_e3_acl$;", 1
    )[0]
    assert "identity finalizer E3 reviewed E5e overlay mismatch" in e3
    assert "parent_relationship_stage_e5e_r08_select" in e3
    assert "parent_relationship_stage_e5e_r09_select" in e3
    assert "ANY (e5e_e3_policy_names)" in e3
    assert "expected_e3_catalog_sha256" in e3

    e4 = GRANTS.split("DO $identity_cutover_e4_acl$", 1)[1].split(
        "$identity_cutover_e4_acl$;", 1
    )[0]
    assert "identity cutover E4 reviewed E5e overlay mismatch" in e4
    assert "parent_relationship_stage_e5e_r07_select" in e4
    assert "attribute_acl.grantee = parent_relationship_kernel_oid" in e4
    assert "e5e_promotion_column_acl_count <> 0" in e4
    for promotion_column in (
        "promotion_id",
        "authority_scope",
        "run_id",
        "finalization_id",
        "policy_digest",
        "committed_at",
    ):
        assert f"'{promotion_column}'" in e4
    assert "expected_e4_catalog_sha256" in e4

    activation = GRANTS.split(
        "DO $parent_relationship_e5e_active_acl$", 1
    )[1].split("$parent_relationship_e5e_active_acl$;", 1)[0]
    assert (
        "ce75886310acf9f9790be2d4d98ae3ac8e61f9dd0af67756ce2a1e3f3c8defa1"
        in activation
    )
    assert "pg_catalog.convert_to(prosrc, 'UTF8')" in activation

    terminal_grants = GRANTS.split(
        "\\if :activate_parent_relationship_stage_e5e", 1
    )[1].split("DO $parent_relationship_e5e_active_acl$", 1)[0]
    for dependency in (
        "identity.ha_user_bindings",
        "identity.principals",
        "identity.people",
        "identity.privacy_directives",
        "identity.edge_privacy_user_blocks",
        "identity.legacy_role_labels",
        "knowledge.fact_versions",
        "operations.semantic_authority_promotions",
        "operations.reviewed_identity_migration_projection_lineage",
        "operations.reviewed_identity_migration_projection_subjects",
        "identity.parent_relationship_requests",
        "identity.parent_relationship_proposals",
        "identity.parent_relationship_proposal_edges",
    ):
        assert dependency in terminal_grants
