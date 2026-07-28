from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT / "alembic/versions/0021_parent_relationship_status_e5h.py"
).read_text(encoding="utf-8")
GRANTS = (ROOT.parents[1] / "home-agent-deploy/apply-grants.sh").read_text(
    encoding="utf-8"
)


def test_e5h_is_additive_recovery_without_production_activation() -> None:
    assert 'revision: str = "0021_parent_status_e5h"' in MIGRATION
    assert 'down_revision: str | None = "0020_parent_commit_e5f"' in MIGRATION
    assert "Production remains pinned to revision 0006a" in MIGRATION
    assert "location_memory" not in MIGRATION
    assert "travel_greetings" not in MIGRATION


def test_e5h_is_table_blind_serializable_and_identity_derived() -> None:
    signature = MIGRATION.split(
        "CREATE FUNCTION identity." "recover_authenticated_parent_relationship_e5h(",
        1,
    )[1].split(")", 1)[0]
    assert "authenticated_ha_user_id character varying" in signature
    for forbidden in (
        "principal_id",
        "person_id",
        "proposal_id",
        "parent_id",
    ):
        assert forbidden not in signature
    body = MIGRATION.split('FUNCTION_BODY = rf"""', 1)[1].split(
        '"""\n\nFUNCTION_BODY_SHA256',
        1,
    )[0]
    assert "pg_current_xact_id_if_assigned() IS NOT NULL" in body
    assert "transaction_isolation') <> 'serializable'" in body
    assert body.index("privacy.lock_identity_semantic_write_fence()") < body.index(
        "FROM operations.semantic_authority_promotions"
    )
    assert "evaluate_current_identity_semantic_authority" in body
    assert "binding.ha_user_id = authenticated_ha_user_id" in body


def test_e5h_recovers_ready_confirmed_and_expires_stale_atomically() -> None:
    assert "'ready_for_confirmation'::varchar" in MIGRATION
    assert "'confirmed'::varchar" in MIGRATION
    assert MIGRATION.count("'not_started'::varchar") == 2
    assert "SET state = 'expired'" in MIGRATION
    assert "SET state = 'expired',\n             closed_at" in MIGRATION
    assert "GET DIAGNOSTICS e5h_affected = ROW_COUNT" in MIGRATION
    assert "parent_relationship_e5h_expiry_race" in MIGRATION
    assert "parent_relationship_e5h_unreceipted_facts" in MIGRATION
    assert "e5h_count <> 2" in MIGRATION


def test_e5h_revalidates_privacy_and_normalized_receipt_graph() -> None:
    for contract in (
        "privacy.identity_person_is_blocked",
        "privacy.identity_principal_is_blocked",
        "identity.edge_privacy_user_blocks",
        "identity.privacy_directives",
        "operations.parent_relationship_authority_receipts",
        "operations.parent_relationship_authority_receipt_edges",
        "knowledge.fact_versions",
        "knowledge.fact_support",
        "'authenticated_confirmation'",
        "'identity_migration'",
    ):
        assert contract in MIGRATION
    for prohibited in (
        "associated_with",
        "owns",
        "resides_at",
        "present_at",
    ):
        assert prohibited not in MIGRATION.casefold()


def test_e5h_grant_replay_quarantines_then_exactly_admits_status() -> None:
    quarantine = GRANTS.index("DO $parent_relationship_e5h_function_quarantine$")
    activation = GRANTS.index("AS activate_parent_relationship_status_e5h")
    assert quarantine < activation
    assert (
        "version_num = '0021_parent_status_e5h'"
        in (GRANTS[activation - 120 : activation])
    )
    tail = GRANTS[activation:]
    assert "identity.recover_authenticated_parent_relationship_e5h(varchar)" in tail
    assert "function_acl_count <> 3" in tail
    assert "parent relationship E5h active ACL contract mismatch" in tail
