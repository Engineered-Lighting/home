"""Storage-contract tests; these do not claim PostgreSQL kernel acceptance."""
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from app import schema
from app import shared_identity_schema as shared


def checks(table):
    return " ".join(str(c.sqltext) for c in table.constraints if isinstance(c, CheckConstraint))


def unique_columns(table):
    return {tuple(c.name for c in constraint.columns) for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint)}


def test_isolated_metadata_cannot_pollute_greenfield_legacy_schema():
    tables = shared.SHARED_IDENTITY_TABLES
    assert len(tables) == 9
    assert set(shared.shared_identity_metadata.tables) == {t.fullname for t in tables}
    assert not set(shared.shared_identity_metadata.tables) & set(schema.metadata.tables)
    assert all(t.metadata is shared.shared_identity_metadata for t in tables)


def test_all_ddl_compiles_and_new_references_are_topologically_ordered():
    seen = set()
    for table in shared.SHARED_IDENTITY_TABLES:
        ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
        assert f"CREATE TABLE {table.fullname}" in ddl
        for fk in table.foreign_keys:
            if fk.column.table.metadata is shared.shared_identity_metadata:
                assert fk.column.table.fullname in seen
        for index in table.indexes:
            assert "CREATE UNIQUE INDEX" in str(CreateIndex(index).compile(dialect=postgresql.dialect()))
        seen.add(table.fullname)


def test_fixed_issuer_mapping_and_qualified_subject_collision_key():
    assert "('home-assistant:echo','echo')" in checks(shared.shared_issuers)
    assert "('home-assistant:victoria','victoria')" in checks(shared.shared_issuers)
    index = next(iter(shared.shared_subject_bindings.indexes))
    assert index.unique
    assert tuple(c.name for c in index.columns) == ("issuer_id", "subject")
    assert str(index.dialect_options["postgresql"]["where"]).endswith("revoked_at IS NULL")
    assert ("link_id", "issuer_id") in unique_columns(shared.shared_subject_bindings)


def test_proofs_have_bounded_freshness_and_exact_consumption_window():
    value = checks(shared.shared_auth_proofs)
    assert "authenticated_at <= issued_at" in value
    assert "expires_at <= authenticated_at + interval '5 minutes'" in value
    assert "consumed_at >= issued_at AND consumed_at < expires_at" in value
    assert ("challenge_commitment",) in unique_columns(shared.shared_auth_proofs)
    proposal = shared.shared_link_proposals
    assert ("echo_proof_id",) in unique_columns(proposal)
    assert ("victoria_proof_id",) in unique_columns(proposal)
    assert "echo_proof_id <> victoria_proof_id" in checks(proposal)
    fk_keys = {tuple(c.name for c in cst.columns) for cst in proposal.constraints
               if isinstance(cst, ForeignKeyConstraint)}
    assert ("echo_proof_id", "echo_issuer", "echo_subject") in fk_keys
    assert ("victoria_proof_id", "victoria_issuer", "victoria_subject") in fk_keys


def test_existing_owner_anchors_and_confirmation_lineage_are_retained():
    proposal = shared.shared_link_proposals
    targets = {fk.target_fullname for fk in proposal.foreign_keys}
    assert "identity.ha_user_bindings.binding_id" in targets
    assert "identity.principals.principal_id" in targets
    assert "identity.principals.person_id" in targets
    assert "confirmed_by_principal_id = principal_id" in checks(proposal)
    assert ("proposal_id",) in unique_columns(shared.shared_link_receipts)
    assert ("receipt_id",) in unique_columns(shared.shared_owner_links)
    assert ("receipt_id", "principal_id", "person_id") in unique_columns(shared.shared_link_receipts)


def test_grants_name_exact_registered_source_capability():
    value = checks(shared.shared_sources)
    assert "source_id ~ '^[a-z0-9][a-z0-9._:-]{0,127}$'" in value
    assert "capability IN ('memory.read','personal_memory.write','lighting.execute')" in value
    targets = {fk.target_fullname for fk in shared.shared_source_grants.foreign_keys}
    assert {"identity.shared_sources.site_id", "identity.shared_sources.source_id",
            "identity.shared_sources.capability"} <= targets
    assert "source_revision > 0" in checks(shared.shared_source_grants)
    assert "authorization_generation > 0" in checks(shared.shared_source_grants)


def test_tombstones_survive_identity_row_deletion_and_require_generation():
    tombstones = shared.shared_identity_revocations
    assert not tombstones.foreign_keys
    assert "authorization_generation > 0" in checks(tombstones)
    assert "person_erasure" in checks(tombstones)
    assert ("owner_commitment", "scope_commitment", "authorization_generation") in unique_columns(tombstones)
    assert all(fk.ondelete != "CASCADE" for table in shared.SHARED_IDENTITY_TABLES for fk in table.foreign_keys)


def test_no_authority_defaults_or_implicit_proof_timestamps():
    for table in shared.SHARED_IDENTITY_TABLES:
        assert all(c.server_default is None for c in table.columns)
        assert all(not c.nullable for c in table.primary_key.columns)
    for name in ("authenticated_at", "issued_at", "expires_at"):
        assert not shared.shared_auth_proofs.c[name].nullable
