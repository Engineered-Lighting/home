from __future__ import annotations

from pathlib import Path

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint

from app import schema


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT / "alembic/versions/0018_parent_relationship_authority_e5d.py"
).read_text(encoding="utf-8")
GRANTS = (
    ROOT.parents[1] / "home-agent-deploy/apply-grants.sh"
).read_text(encoding="utf-8")

TABLES = (
    schema.parent_relationship_requests,
    schema.parent_relationship_proposals,
    schema.parent_relationship_proposal_edges,
    schema.parent_relationship_authority_receipts,
    schema.parent_relationship_authority_receipt_edges,
)


def _checks(table) -> str:
    return " ".join(
        str(item.sqltext)
        for item in table.constraints
        if isinstance(item, CheckConstraint)
    )


def _unique_columns(table) -> set[tuple[str, ...]]:
    return {
        tuple(column.name for column in item.columns)
        for item in table.constraints
        if isinstance(item, UniqueConstraint)
    }


def _foreign_keys(table) -> set[tuple[tuple[str, ...], tuple[str, ...]]]:
    return {
        (
            tuple(column.name for column in item.columns),
            tuple(element.target_fullname for element in item.elements),
        )
        for item in table.constraints
        if isinstance(item, ForeignKeyConstraint)
    }


def test_e5d_foundation_is_a_linear_owner_only_revision() -> None:
    assert 'revision: str = "0018_parent_relationship_e5d"' in MIGRATION
    assert 'down_revision: str | None = "0017_authenticated_binding_e5c"' in (
        MIGRATION
    )
    assert "Production remains pinned to revision 0006a" in MIGRATION
    assert "creates no fact" in MIGRATION
    assert "grants no runtime access" in MIGRATION
    assert "FORCE ROW LEVEL SECURITY" in MIGRATION
    assert "session_user = 'home_agent_owner'" in MIGRATION
    assert "refusing to drop nonempty E5d parent authority evidence" in MIGRATION

    runtime_grants = tuple(
        line.strip()
        for line in MIGRATION.splitlines()
        if line.strip().startswith("GRANT ")
    )
    assert runtime_grants == ()
    assert "TO home_agent_owner" in MIGRATION
    for role in (
        "home_agent_api",
        "home_agent_binding_operator",
        "home_agent_binding_committer",
        "home_agent_worker",
        "home_agent_erasure",
    ):
        assert f'"{role}"' in MIGRATION


def test_request_is_bound_to_the_confirmed_principal_person_and_binding() -> None:
    request = schema.parent_relationship_requests
    assert set(request.c.keys()) == {
        "request_id",
        "principal_id",
        "child_person_id",
        "binding_id",
        "state",
        "requested_at",
        "expires_at",
        "staged_at",
        "closed_at",
    }
    checks = _checks(request)
    assert "substring(request_id::text from 15 for 1) = '7'" in checks
    for state in ("pending", "staged", "consumed", "cancelled", "expired"):
        assert state in checks
    assert "expires_at > requested_at" in checks
    assert (
        "request_id",
        "principal_id",
        "child_person_id",
    ) in _unique_columns(request)
    foreign_keys = _foreign_keys(request)
    assert any(
        remote == ("identity.principals.principal_id",)
        for _local, remote in foreign_keys
    )
    assert any(
        remote == ("identity.people.person_id",)
        for _local, remote in foreign_keys
    )
    assert any(
        remote == ("identity.ha_user_bindings.binding_id",)
        for _local, remote in foreign_keys
    )
    indexes = {index.name: index for index in request.indexes}
    assert {
        "uq_parent_relationship_requests_active_principal",
        "uq_parent_relationship_requests_active_child",
    } <= set(indexes)
    assert all(index.unique for index in indexes.values())


def test_proposal_is_exactly_two_edges_private_and_fifteen_minutes() -> None:
    proposal = schema.parent_relationship_proposals
    checks = _checks(proposal)
    assert "contract_version = 'parent-relationship-authority-v2'" in checks
    assert "candidate_count = 2" in checks
    assert "interval '15 minutes'" in checks
    assert "confirmation_artifact_id IS NULL" in checks
    assert "memory_transaction_id IS NULL" in checks
    assert "confirmation_artifact_id IS NOT NULL" in checks
    assert "memory_transaction_id IS NOT NULL" in checks
    assert {
        ("request_id",),
        ("proposal_digest",),
        ("confirmation_artifact_id",),
        ("memory_transaction_id",),
        ("proposal_id", "child_person_id"),
    } <= _unique_columns(proposal)

    request_subject_fk = next(
        (local, remote)
        for local, remote in _foreign_keys(proposal)
        if local == ("request_id", "principal_id", "child_person_id")
    )
    assert request_subject_fk[1] == (
        "identity.parent_relationship_requests.request_id",
        "identity.parent_relationship_requests.principal_id",
        "identity.parent_relationship_requests.child_person_id",
    )


def test_edges_reuse_people_and_legacy_labels_without_storing_names() -> None:
    edge = schema.parent_relationship_proposal_edges
    columns = set(edge.c.keys())
    assert {
        "proposal_edge_id",
        "proposal_id",
        "ordinal",
        "parent_person_id",
        "child_person_id",
        "legacy_label_id",
        "review_code",
        "person_snapshot_digest",
        "role_snapshot_digest",
        "edge_commitment",
        "predicate",
        "legacy_role_label",
        "legacy_perspective",
        "legacy_authoritative",
        "required_authority",
        "required_support",
        "required_contradiction",
        "required_freshness",
        "required_coverage",
        "required_resolution",
        "privacy_scope",
    } == columns
    assert not columns & {
        "display_name",
        "parent_name",
        "child_name",
        "exact_text",
        "utterance",
        "latitude",
        "longitude",
    }
    checks = _checks(edge)
    for invariant in (
        "ordinal IN (0,1)",
        "parent_person_id <> child_person_id",
        "predicate = 'parent_of'",
        "legacy_role_label = 'parent'",
        "legacy_perspective = 'unknown'",
        "legacy_authoritative = false",
        "required_authority = 'explicit_related_party'",
        "required_support = 'explicit_authority'",
        "required_contradiction = 'none'",
        "required_freshness = 'not_applicable'",
        "required_coverage = 'not_applicable'",
        "required_resolution = 'accepted'",
        "privacy_scope = 'private'",
    ):
        assert invariant in checks
    assert {
        ("proposal_id", "ordinal"),
        ("proposal_id", "parent_person_id"),
        ("proposal_id", "legacy_label_id"),
        ("proposal_id", "review_code"),
    } <= _unique_columns(edge)
    foreign_keys = _foreign_keys(edge)
    assert any(
        remote == ("identity.people.person_id",)
        for _local, remote in foreign_keys
    )
    assert any(
        remote == ("identity.legacy_role_labels.label_id",)
        for _local, remote in foreign_keys
    )


def test_receipts_are_normalized_content_minimized_and_fact_linked() -> None:
    receipt = schema.parent_relationship_authority_receipts
    receipt_edge = schema.parent_relationship_authority_receipt_edges
    receipt_checks = _checks(receipt)
    assert "contract_version = 'parent-relationship-authority-v2'" in receipt_checks
    assert "edge_count = 2" in receipt_checks
    assert "authority_result = 'committed'" in receipt_checks
    assert "database_transaction_id > 0" in receipt_checks
    assert {
        ("proposal_id",),
        ("request_id",),
        ("confirmation_artifact_id",),
        ("memory_transaction_id",),
        ("receipt_commitment",),
    } <= _unique_columns(receipt)

    assert set(receipt_edge.c.keys()) == {
        "receipt_edge_id",
        "receipt_id",
        "ordinal",
        "proposal_edge_id",
        "fact_version_id",
        "confirmation_support_id",
        "legacy_support_id",
    }
    assert "ordinal IN (0,1)" in _checks(receipt_edge)
    assert "confirmation_support_id <> legacy_support_id" in _checks(receipt_edge)
    assert ("receipt_id", "ordinal") in _unique_columns(receipt_edge)
    foreign_keys = _foreign_keys(receipt_edge)
    for target in (
        "identity.parent_relationship_proposal_edges.proposal_edge_id",
        "knowledge.fact_versions.fact_version_id",
        "knowledge.fact_support.support_id",
    ):
        assert any(target in remote for _local, remote in foreign_keys)


def test_foundation_has_no_runtime_mount_or_writer() -> None:
    assert all(
        f"{table.schema}.{table.name}" in schema.metadata.tables for table in TABLES
    )
    runtime_sources = (
        ROOT / "app/api.py",
        ROOT / "app/store.py",
    )
    for path in runtime_sources:
        source = path.read_text(encoding="utf-8")
        assert "parent_relationship_requests" not in source
        assert "parent_relationship_proposals" not in source
        assert "parent_relationship_authority_receipts" not in source


def test_grant_replay_keeps_every_e5d_relation_owner_only() -> None:
    api_grants = GRANTS.split(
        "DO $identity_api_operations_grants$", 1
    )[1].split("$identity_api_operations_grants$;", 1)[0]
    worker_grants = GRANTS.split(
        "DO $identity_worker_operations_grants$", 1
    )[1].split("$identity_worker_operations_grants$;", 1)[0]
    for section in (api_grants, worker_grants):
        assert "parent_relationship_authority_receipts" in section
        assert "parent_relationship_authority_receipt_edges" in section
        assert section.count("relation_row.oid IS DISTINCT FROM") >= 3

    quarantine = GRANTS.split(
        "DO $parent_relationship_e5d_early_quarantine$", 1
    )[1].split("$parent_relationship_e5d_early_quarantine$;", 1)[0]
    for table in TABLES:
        assert f"'{table.schema}.{table.name}'" in quarantine
    assert "role_row.rolname <> 'home_agent_owner'" in quarantine
    assert "UNION ALL SELECT 'PUBLIC'" in quarantine
    assert "REVOKE ALL PRIVILEGES ON TABLE %s FROM %s CASCADE" in quarantine
    assert "REFERENCES (%1$s)" in quarantine
    assert "GRANT" not in quarantine
