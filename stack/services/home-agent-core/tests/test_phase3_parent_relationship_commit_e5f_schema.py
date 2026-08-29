from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "alembic/versions/0020_parent_relationship_commit_e5f.py"
).read_text(encoding="utf-8")
GRANTS = (
    ROOT.parents[1] / "home-agent-deploy/apply-grants.sh"
).read_text(encoding="utf-8")


def test_e5f_is_additive_and_does_not_activate_a_surface() -> None:
    assert 'revision: str = "0020_parent_commit_e5f"' in MIGRATION
    assert 'down_revision: str | None = "0019_parent_stage_e5e"' in MIGRATION
    assert "No API, BFF route, UI, or production activation" in MIGRATION
    assert "Production remains pinned to revision 0006a" in " ".join(
        MIGRATION.split()
    )


def test_e5f_caller_cannot_choose_semantic_identities() -> None:
    signature = MIGRATION.split(
        "CREATE FUNCTION identity."
        "commit_authenticated_parent_relationship_e5f(",
        1,
    )[1].split(")", 1)[0]
    assert "parent_person_id" not in signature
    assert "child_person_id" not in signature
    assert "principal_id" not in signature
    assert "binding_id" not in signature
    assert "authenticated_ha_user_id character varying" in signature
    assert "target_proposal_id uuid" in signature
    assert "target_proposal_digest character varying" in signature


def test_e5f_fences_and_revalidates_before_writing() -> None:
    body = MIGRATION.split('FUNCTION_BODY = rf"""', 1)[1].split(
        '"""\n\nFUNCTION_BODY_SHA256',
        1,
    )[0]
    fence = body.index("privacy.lock_identity_semantic_write_fence()")
    proposal = body.index("FROM identity.parent_relationship_proposals")
    first_insert = body.index("INSERT INTO identity.confirmation_artifacts")
    assert fence < proposal < first_insert
    assert "pg_current_xact_id_if_assigned() IS NOT NULL" in body
    assert "transaction_isolation') <> 'serializable'" in body
    assert "evaluate_current_identity_semantic_authority" in body
    assert "privacy.identity_person_is_blocked" in body
    assert "privacy.identity_principal_is_blocked" in body
    assert "legacy_role_candidate" in body
    assert "projection_subject.subject_role = 'primary'" in body


def test_e5f_commits_the_exact_atomic_graph() -> None:
    for relation in (
        "identity.confirmation_artifacts",
        "privacy.artifact_registry",
        "knowledge.memory_transactions",
        "knowledge.fact_versions",
        "knowledge.fact_support",
        "operations.parent_relationship_authority_receipts",
        "operations.parent_relationship_authority_receipt_edges",
    ):
        assert f"INSERT INTO {relation}" in MIGRATION
    assert MIGRATION.count("FOR e5f_index IN 1..2 LOOP") == 3
    assert "'explicit_related_party'" in MIGRATION
    assert "'explicit_authority'" in MIGRATION
    assert "'authenticated_confirmation', 'confirmation'" in MIGRATION
    assert "'identity_migration', 'legacy_context'" in MIGRATION
    assert "'parent_relationship_confirmation', 'committed'" in MIGRATION
    assert "exact_text_ciphertext" in MIGRATION
    assert "NULL, NULL, NULL" in MIGRATION
    assert "WHEN serialization_failure THEN" in MIGRATION
    assert "WHEN integrity_constraint_violation THEN" in MIGRATION


def test_e5f_encodes_no_ownership_residence_or_presence_implication() -> None:
    lowered = MIGRATION.casefold()
    for prohibited in (
        "associated_with",
        "owns",
        "ownership",
        "resides_at",
        "residence",
        "present_at",
        "presence",
    ):
        assert prohibited not in lowered
    assert "predicate = 'parent_of'" in MIGRATION
    assert "'person_id', e5f_binding.child_person_id::text" in MIGRATION


def test_e5f_is_security_definer_and_table_blind() -> None:
    assert "SECURITY DEFINER" in MIGRATION
    assert "SET search_path = pg_catalog" in MIGRATION
    assert "SET row_security = on" in MIGRATION
    assert "ALTER FUNCTION {FUNCTION} OWNER TO {KERNEL_ROLE}" in MIGRATION
    assert "GRANT EXECUTE ON FUNCTION {FUNCTION} TO {CALLER_ROLE}" in MIGRATION
    assert (
        "GRANT EXECUTE ON FUNCTION {FUNCTION} TO home_agent_api"
        not in MIGRATION
    )
    assert "pg_has_role(\n          session_user, '{KERNEL_ROLE}', 'SET'" in (
        MIGRATION
    )


def test_grant_replay_quarantines_then_exactly_admits_e5f() -> None:
    quarantine = GRANTS.index(
        "DO $parent_relationship_e5f_function_quarantine$"
    )
    activation = GRANTS.index(
        "AS activate_parent_relationship_commit_e5f"
    )
    assert quarantine < activation
    # The gate is keyed on the revisions at which E5f's grants must be live.
    # Asserted by membership rather than by the exact one-line spelling: the
    # list grew past 0021 and a formatting pin would fail for no reason.
    gate = GRANTS[activation - 900 : activation]
    assert "version_num IN (" in gate
    for revision in (
        "0020_parent_commit_e5f",
        "0021_parent_status_e5h",
        "0028_owner_partner_access_e5o",
    ):
        assert f"'{revision}'" in gate
    assert (
        "GRANT EXECUTE ON FUNCTION\n"
        "  identity.commit_authenticated_parent_relationship_e5f("
        in GRANTS[activation:]
    )
    assert (
        "TO home_agent_binding_committer"
        in GRANTS[activation:]
    )
    assert (
        "TO home_agent_parent_relationship_kernel"
        in GRANTS[activation:]
    )
    assert (
        "GRANT SELECT (\n"
        "  fact_id, version, subject_type, valid_range, authority, support,\n"
        "  contradiction, freshness, coverage, privacy_scope,\n"
        "  memory_transaction_id, committed_at\n"
        ") ON knowledge.fact_versions"
        in GRANTS[activation:]
    )
    assert (
        "parent relationship E5f active ACL contract mismatch"
        in GRANTS[activation:]
    )


def test_e5f_replay_can_read_only_its_additional_fact_columns() -> None:
    columns = (
        "fact_id, version, subject_type, valid_range, authority, support,\n"
        "          contradiction, freshness, coverage, privacy_scope,\n"
        "          memory_transaction_id, committed_at"
    )
    assert f"GRANT SELECT (\n          {columns}" in MIGRATION
    assert f"REVOKE SELECT (\n          {columns}" in MIGRATION
    assert "GRANT SELECT ON knowledge.fact_versions" not in MIGRATION
