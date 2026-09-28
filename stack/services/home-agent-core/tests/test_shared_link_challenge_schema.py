"""Offline DDL contract checks, not database or authorization acceptance."""
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app import schema
from app import shared_identity_schema as identity
from app import shared_link_challenge_schema as link


def checks(table):
    return " ".join(str(c.sqltext) for c in table.constraints if isinstance(c, CheckConstraint))


def unique(table):
    return {tuple(column.name for column in c.columns) for c in table.constraints if isinstance(c, UniqueConstraint)}


def test_challenge_storage_is_not_implicitly_created_by_existing_metadata():
    names = set(link.shared_link_challenge_metadata.tables)
    assert len(names) == 3
    assert not names & set(schema.metadata.tables)
    assert not names & set(identity.shared_identity_metadata.tables)
    assert len(identity.SHARED_IDENTITY_TABLES) == 9
    for table in link.SHARED_LINK_CHALLENGE_TABLES:
        assert f"CREATE TABLE {table.fullname}" in str(CreateTable(table).compile(dialect=postgresql.dialect()))
        assert all(c.server_default is None for c in table.columns)
        assert all(fk.ondelete != "CASCADE" for fk in table.foreign_keys)


def test_generation_anchor_survives_live_identity_removal():
    generation = link.shared_owner_generations
    assert not generation.foreign_keys
    assert "authorization_generation > 0" in checks(generation)
    assert "'blocked'" in checks(generation)
    # Parent generations are immutable snapshots, not FKs to the mutable current
    # counter: advancing the counter must invalidate, not be blocked by, parents.
    assert all(fk.target_fullname != "privacy.shared_owner_generations.authorization_generation"
               for fk in link.shared_link_ceremonies.foreign_keys)


def test_parent_retains_existing_account_anchor_and_request_identity():
    parent = link.shared_link_ceremonies
    targets = {fk.target_fullname for fk in parent.foreign_keys}
    assert {"identity.principals.principal_id", "identity.principals.person_id",
            "identity.ha_user_bindings.binding_id"} <= targets
    assert ("owner_commitment", "request_commitment") in unique(parent)
    assert "purpose = 'link_echo_victoria'" in checks(parent)
    assert "state = 'pending' AND ended_at IS NULL" in checks(parent)
    assert "ended_at < expires_at" in checks(parent)


def test_two_issuer_children_require_distinct_commitments_and_exact_parent_generation():
    child = link.shared_link_challenges
    assert ("ceremony_id", "issuer_id") in unique(child)
    assert ("challenge_commitment",) in unique(child)
    assert "'home-assistant:echo','home-assistant:victoria'" in checks(child)
    fks = {tuple(c.name for c in fk.columns) for fk in child.constraints if isinstance(fk, ForeignKeyConstraint)}
    assert ("ceremony_id", "authorization_generation") in fks
    assert "registration_revision > 0" in checks(child)


def test_consumption_cannot_reference_another_issuer_or_half_written_receipt():
    child = link.shared_link_challenges
    fks = {tuple(c.name for c in fk.columns) for fk in child.constraints if isinstance(fk, ForeignKeyConstraint)}
    assert ("consumed_proof_id", "issuer_id", "consumed_subject", "challenge_commitment",
            "session_commitment", "registration_revision") in fks
    assert link.REQUIRED_PROOF_SCOPE_UNIQUE_COLUMNS == ("proof_id", "issuer_id", "subject",
        "challenge_commitment", "session_commitment", "registration_revision")
    assert ("consumed_proof_id",) in unique(child)
    assert "consumed_proof_id IS NULL AND consumed_subject IS NULL AND consumed_at IS NULL" in checks(child)
    assert "consumed_subject IS NOT NULL" in checks(child)
    assert "consumed_at < expires_at" in checks(child)


def test_parent_and_child_windows_are_bounded_without_fabricated_timestamps():
    for table in (link.shared_link_ceremonies, link.shared_link_challenges):
        assert "expires_at <= created_at + interval '5 minutes'" in checks(table)
        assert not table.c.created_at.nullable
        assert not table.c.expires_at.nullable
