"""Additive, initially inaccessible cross-home identity storage.

Only the reviewed migration may create SHARED_IDENTITY_TABLES. These declarations
never join app.schema.metadata (0001 creates that metadata). External reference
placeholders belong to another MetaData and MUST NOT be created. Storage checks
are not authentication: future kernels must verify fresh issuer proof provenance,
the exact legacy binding/owner correspondence, current authority, privacy and
erasure fences, and atomically consume proofs and advance revocation generation.
No runtime writer or grant is provided by this module.
"""
from sqlalchemy import (BigInteger, CheckConstraint, Column, DateTime,
                        ForeignKeyConstraint, Index, MetaData, String, Table,
                        UniqueConstraint)
from sqlalchemy.dialects.postgresql import UUID

shared_identity_metadata = MetaData()
_external_metadata = MetaData()
_principals = Table("principals", _external_metadata,
                    Column("principal_id", UUID), Column("person_id", UUID), schema="identity")
_bindings = Table("ha_user_bindings", _external_metadata,
                  Column("binding_id", UUID), schema="identity")


def uid(name, **kw):
    return Column(name, UUID(as_uuid=True), **kw)


def stamp(name, **kw):
    return Column(name, DateTime(timezone=True), **kw)


def digest(name):
    return CheckConstraint(f"{name} ~ '^[0-9a-f]{{64}}$'", name=f"shared_{name}_digest")


shared_issuers = Table(
    "shared_issuers", shared_identity_metadata,
    Column("issuer_id", String(64), primary_key=True),
    Column("site_id", String(16), nullable=False, unique=True),
    Column("registration_revision", BigInteger, nullable=False),
    Column("state", String(16), nullable=False),
    CheckConstraint("(issuer_id, site_id) IN "
                    "(('home-assistant:echo','echo'),('home-assistant:victoria','victoria'))",
                    name="shared_fixed_issuer_site"),
    CheckConstraint("registration_revision > 0 AND state IN ('active','revoked')",
                    name="shared_issuer_lifecycle"),
    UniqueConstraint("issuer_id", "site_id", name="shared_issuer_site_identity"),
    schema="identity")

shared_auth_proofs = Table(
    "shared_auth_proofs", shared_identity_metadata,
    uid("proof_id", primary_key=True),
    Column("issuer_id", String(64), nullable=False),
    Column("subject", String(64), nullable=False),
    Column("session_commitment", String(64), nullable=False),
    Column("challenge_commitment", String(64), nullable=False, unique=True),
    stamp("authenticated_at", nullable=False), stamp("issued_at", nullable=False),
    stamp("expires_at", nullable=False), stamp("consumed_at"),
    ForeignKeyConstraint(["issuer_id"], [shared_issuers.c.issuer_id]),
    UniqueConstraint("proof_id", "issuer_id", "subject", name="shared_proof_identity"),
    CheckConstraint("subject <> '' AND subject = btrim(subject)", name="shared_proof_subject"),
    CheckConstraint("authenticated_at <= issued_at AND issued_at < expires_at "
                    "AND expires_at <= authenticated_at + interval '5 minutes' "
                    "AND (consumed_at IS NULL OR (consumed_at >= issued_at AND consumed_at < expires_at))",
                    name="shared_proof_window"),
    digest("session_commitment"), digest("challenge_commitment"), schema="identity")

shared_link_proposals = Table(
    "shared_link_proposals", shared_identity_metadata,
    uid("proposal_id", primary_key=True), uid("legacy_binding_id", nullable=False),
    uid("principal_id", nullable=False), uid("person_id", nullable=False),
    uid("confirmed_by_principal_id", nullable=False),
    uid("echo_proof_id", nullable=False, unique=True),
    Column("echo_issuer", String(64), nullable=False), Column("echo_subject", String(64), nullable=False),
    uid("victoria_proof_id", nullable=False, unique=True),
    Column("victoria_issuer", String(64), nullable=False), Column("victoria_subject", String(64), nullable=False),
    Column("proposal_digest", String(64), nullable=False),
    Column("expected_generation", BigInteger, nullable=False),
    stamp("created_at", nullable=False), stamp("expires_at", nullable=False),
    ForeignKeyConstraint(["legacy_binding_id"], [_bindings.c.binding_id]),
    ForeignKeyConstraint(["principal_id", "person_id"], [_principals.c.principal_id, _principals.c.person_id]),
    ForeignKeyConstraint(["echo_proof_id", "echo_issuer", "echo_subject"],
                         [shared_auth_proofs.c.proof_id, shared_auth_proofs.c.issuer_id, shared_auth_proofs.c.subject]),
    ForeignKeyConstraint(["victoria_proof_id", "victoria_issuer", "victoria_subject"],
                         [shared_auth_proofs.c.proof_id, shared_auth_proofs.c.issuer_id, shared_auth_proofs.c.subject]),
    CheckConstraint("echo_issuer = 'home-assistant:echo' AND victoria_issuer = 'home-assistant:victoria' "
                    "AND echo_proof_id <> victoria_proof_id", name="shared_two_issuer_proofs"),
    CheckConstraint("confirmed_by_principal_id = principal_id", name="shared_owner_self_confirmation"),
    CheckConstraint("expected_generation >= 0 AND expires_at > created_at "
                    "AND expires_at <= created_at + interval '5 minutes'", name="shared_proposal_window"),
    UniqueConstraint("proposal_id", "principal_id", "person_id", "proposal_digest",
                     name="shared_proposal_owner_digest"),
    digest("proposal_digest"), schema="identity")

shared_link_receipts = Table(
    "shared_link_receipts", shared_identity_metadata,
    uid("receipt_id", primary_key=True), uid("proposal_id", nullable=False, unique=True),
    uid("principal_id", nullable=False), uid("person_id", nullable=False),
    Column("proposal_digest", String(64), nullable=False),
    Column("confirmation_commitment", String(64), nullable=False, unique=True),
    stamp("confirmed_at", nullable=False),
    ForeignKeyConstraint(["proposal_id", "principal_id", "person_id", "proposal_digest"],
                         [shared_link_proposals.c.proposal_id, shared_link_proposals.c.principal_id,
                          shared_link_proposals.c.person_id, shared_link_proposals.c.proposal_digest]),
    UniqueConstraint("receipt_id", "principal_id", "person_id", name="shared_receipt_owner"),
    digest("confirmation_commitment"), schema="identity")

shared_owner_links = Table(
    "shared_owner_links", shared_identity_metadata,
    uid("link_id", primary_key=True), uid("receipt_id", nullable=False, unique=True),
    uid("principal_id", nullable=False), uid("person_id", nullable=False),
    Column("revision", BigInteger, nullable=False),
    Column("authorization_generation", BigInteger, nullable=False),
    stamp("created_at", nullable=False), stamp("revoked_at"),
    ForeignKeyConstraint(["receipt_id", "principal_id", "person_id"],
                         [shared_link_receipts.c.receipt_id, shared_link_receipts.c.principal_id,
                          shared_link_receipts.c.person_id]),
    CheckConstraint("revision > 0 AND authorization_generation > 0 "
                    "AND (revoked_at IS NULL OR revoked_at >= created_at)", name="shared_link_lifecycle"),
    schema="identity")
Index("uq_shared_active_owner", shared_owner_links.c.person_id, unique=True,
      postgresql_where=shared_owner_links.c.revoked_at.is_(None))

shared_subject_bindings = Table(
    "shared_subject_bindings", shared_identity_metadata,
    uid("binding_id", primary_key=True), uid("link_id", nullable=False),
    Column("issuer_id", String(64), nullable=False), Column("subject", String(64), nullable=False),
    stamp("created_at", nullable=False), stamp("revoked_at"),
    ForeignKeyConstraint(["link_id"], [shared_owner_links.c.link_id]),
    ForeignKeyConstraint(["issuer_id"], [shared_issuers.c.issuer_id]),
    UniqueConstraint("link_id", "issuer_id", name="shared_link_one_subject_per_issuer"),
    CheckConstraint("subject <> '' AND subject = btrim(subject) "
                    "AND (revoked_at IS NULL OR revoked_at >= created_at)", name="shared_subject_lifecycle"),
    schema="identity")
Index("uq_shared_active_issuer_subject", shared_subject_bindings.c.issuer_id,
      shared_subject_bindings.c.subject, unique=True,
      postgresql_where=shared_subject_bindings.c.revoked_at.is_(None))

shared_sources = Table(
    "shared_sources", shared_identity_metadata,
    Column("site_id", String(16), primary_key=True),
    Column("source_id", String(128), primary_key=True),
    Column("capability", String(32), primary_key=True),
    Column("issuer_id", String(64), nullable=False),
    Column("registration_revision", BigInteger, nullable=False),
    Column("state", String(16), nullable=False),
    ForeignKeyConstraint(["issuer_id", "site_id"], [shared_issuers.c.issuer_id, shared_issuers.c.site_id]),
    CheckConstraint("source_id ~ '^[a-z0-9][a-z0-9._:-]{0,127}$' "
                    "AND capability IN ('memory.read','personal_memory.write','lighting.execute')",
                    name="shared_exact_source_capability"),
    CheckConstraint("registration_revision > 0 AND state IN ('active','revoked')",
                    name="shared_source_lifecycle"), schema="identity")

shared_source_grants = Table(
    "shared_source_grants", shared_identity_metadata,
    uid("grant_id", primary_key=True), uid("link_id", nullable=False),
    Column("site_id", String(16), nullable=False), Column("source_id", String(128), nullable=False),
    Column("capability", String(32), nullable=False),
    Column("source_revision", BigInteger, nullable=False),
    Column("authorization_generation", BigInteger, nullable=False),
    Column("revision", BigInteger, nullable=False),
    Column("approval_commitment", String(64), nullable=False, unique=True),
    stamp("created_at", nullable=False), stamp("expires_at", nullable=False), stamp("revoked_at"),
    ForeignKeyConstraint(["link_id"], [shared_owner_links.c.link_id]),
    ForeignKeyConstraint(["site_id", "source_id", "capability"],
                         [shared_sources.c.site_id, shared_sources.c.source_id, shared_sources.c.capability]),
    CheckConstraint("source_revision > 0 AND authorization_generation > 0 AND revision > 0 "
                    "AND expires_at > created_at AND (revoked_at IS NULL OR revoked_at >= created_at)",
                    name="shared_grant_lifecycle"),
    digest("approval_commitment"), schema="identity")
Index("uq_shared_active_source_grant", shared_source_grants.c.link_id,
      shared_source_grants.c.site_id, shared_source_grants.c.source_id,
      shared_source_grants.c.capability, unique=True,
      postgresql_where=shared_source_grants.c.revoked_at.is_(None))

# Deliberately no FKs: unlink/erasure replay survives removal of live identity
# rows. Commitments are computed by future governed kernels, never public hashes
# treated as proof. Ledger attachment and replay remain separate required gates.
shared_identity_revocations = Table(
    "shared_identity_revocations", shared_identity_metadata,
    uid("revocation_id", primary_key=True),
    Column("owner_commitment", String(64), nullable=False),
    Column("scope_commitment", String(64), nullable=False),
    Column("kind", String(32), nullable=False),
    Column("authorization_generation", BigInteger, nullable=False),
    stamp("revoked_at", nullable=False),
    Column("ledger_record_digest", String(64)),
    CheckConstraint("kind IN ('owner_unlink','grant_revoke','person_erasure') "
                    "AND authorization_generation > 0", name="shared_revocation_kind_generation"),
    CheckConstraint("ledger_record_digest IS NULL OR ledger_record_digest ~ '^[0-9a-f]{64}$'",
                    name="shared_revocation_ledger_digest"),
    UniqueConstraint("owner_commitment", "scope_commitment", "authorization_generation",
                     name="shared_revocation_once"),
    digest("owner_commitment"), digest("scope_commitment"), schema="privacy")

SHARED_IDENTITY_TABLES = (
    shared_issuers, shared_auth_proofs, shared_link_proposals, shared_link_receipts,
    shared_owner_links, shared_subject_bindings, shared_sources,
    shared_source_grants, shared_identity_revocations,
)
