"""Isolated storage contract for governed linking; no runtime creation or grants.

These declarations are not part of legacy bootstrap metadata. A reviewed frozen
migration and atomic authority kernels must create/use them. Constraints do not
prove authority, cross-row freshness, privacy eligibility or monotonic updates.
"""
from sqlalchemy import (BigInteger, CheckConstraint, Column, DateTime,
                        ForeignKeyConstraint, MetaData, String, Table,
                        UniqueConstraint)
from sqlalchemy.dialects.postgresql import UUID

from .shared_identity_schema import shared_issuers

shared_link_challenge_metadata = MetaData()
_references = MetaData()
_principals = Table("principals", _references, Column("principal_id", UUID),
                    Column("person_id", UUID), schema="identity")
_bindings = Table("ha_user_bindings", _references, Column("binding_id", UUID), schema="identity")
# The next frozen migration must add this exact UNIQUE key to the existing proof
# table before creating the child FK. Do not mutate the frozen 0032 declaration;
# registration_revision was added separately in 0033 and legacy rows may be NULL.
REQUIRED_PROOF_SCOPE_UNIQUE_COLUMNS = ("proof_id", "issuer_id", "subject",
                                     "challenge_commitment", "session_commitment", "registration_revision")
_proofs = Table("shared_auth_proofs", _references, Column("proof_id", UUID),
                Column("issuer_id", String(64)), Column("subject", String(64)),
                Column("challenge_commitment", String(64)), Column("session_commitment", String(64)),
                Column("registration_revision", BigInteger), schema="identity")


def uid(name, **kw):
    return Column(name, UUID(as_uuid=True), **kw)


def stamp(name, **kw):
    return Column(name, DateTime(timezone=True), **kw)


def digest(name):
    return CheckConstraint(f"{name} ~ '^[0-9a-f]{{64}}$'", name=f"link_{name}_digest")


# No FK to live identity: cancellation/generation history survives unlink and
# identity erasure. The governed writer derives the owner commitment, never UI.
shared_owner_generations = Table(
    "shared_owner_generations", shared_link_challenge_metadata,
    Column("owner_commitment", String(64), primary_key=True),
    Column("authorization_generation", BigInteger, nullable=False),
    Column("revision", BigInteger, nullable=False),
    Column("state", String(16), nullable=False),
    stamp("updated_at", nullable=False), Column("ledger_record_digest", String(64)),
    CheckConstraint("authorization_generation > 0 AND revision > 0 AND state IN ('active','blocked')",
                    name="link_generation_state"),
    CheckConstraint("ledger_record_digest IS NULL OR ledger_record_digest ~ '^[0-9a-f]{64}$'",
                    name="link_generation_ledger"),
    digest("owner_commitment"), schema="privacy")

shared_link_ceremonies = Table(
    "shared_link_ceremonies", shared_link_challenge_metadata,
    uid("ceremony_id", primary_key=True),
    Column("owner_commitment", String(64), nullable=False),
    uid("principal_id", nullable=False), uid("person_id", nullable=False),
    uid("legacy_binding_id", nullable=False),
    Column("initiating_session_commitment", String(64), nullable=False),
    Column("request_commitment", String(64), nullable=False),
    Column("purpose", String(32), nullable=False),
    Column("authorization_generation", BigInteger, nullable=False),
    Column("revision", BigInteger, nullable=False),
    Column("state", String(16), nullable=False),
    stamp("created_at", nullable=False), stamp("expires_at", nullable=False), stamp("ended_at"),
    ForeignKeyConstraint(["owner_commitment"], [shared_owner_generations.c.owner_commitment]),
    ForeignKeyConstraint(["principal_id", "person_id"], [_principals.c.principal_id, _principals.c.person_id]),
    ForeignKeyConstraint(["legacy_binding_id"], [_bindings.c.binding_id]),
    UniqueConstraint("owner_commitment", "request_commitment", name="link_ceremony_request_once"),
    UniqueConstraint("ceremony_id", "authorization_generation", name="link_ceremony_generation_identity"),
    CheckConstraint("purpose = 'link_echo_victoria' AND authorization_generation > 0 AND revision > 0",
                    name="link_ceremony_purpose_generation"),
    CheckConstraint("expires_at > created_at AND expires_at <= created_at + interval '5 minutes'",
                    name="link_ceremony_window"),
    CheckConstraint("(state = 'pending' AND ended_at IS NULL) OR "
                    "(state = 'cancelled' AND ended_at IS NOT NULL AND ended_at >= created_at) OR "
                    "(state = 'consumed' AND ended_at IS NOT NULL AND ended_at >= created_at AND ended_at < expires_at)",
                    name="link_ceremony_lifecycle"),
    digest("owner_commitment"), digest("initiating_session_commitment"), digest("request_commitment"),
    schema="identity")

shared_link_challenges = Table(
    "shared_link_challenges", shared_link_challenge_metadata,
    uid("challenge_id", primary_key=True), uid("ceremony_id", nullable=False),
    Column("authorization_generation", BigInteger, nullable=False),
    Column("issuer_id", String(64), nullable=False),
    Column("registration_revision", BigInteger, nullable=False),
    Column("session_commitment", String(64), nullable=False),
    Column("challenge_commitment", String(64), nullable=False, unique=True),
    stamp("created_at", nullable=False), stamp("expires_at", nullable=False),
    uid("consumed_proof_id", unique=True), Column("consumed_subject", String(64)), stamp("consumed_at"),
    ForeignKeyConstraint(["ceremony_id", "authorization_generation"],
                         [shared_link_ceremonies.c.ceremony_id, shared_link_ceremonies.c.authorization_generation]),
    ForeignKeyConstraint(["issuer_id"], [shared_issuers.c.issuer_id]),
    ForeignKeyConstraint(["consumed_proof_id", "issuer_id", "consumed_subject",
                          "challenge_commitment", "session_commitment", "registration_revision"],
                         [_proofs.c[name] for name in REQUIRED_PROOF_SCOPE_UNIQUE_COLUMNS]),
    UniqueConstraint("ceremony_id", "issuer_id", name="link_one_challenge_per_issuer"),
    CheckConstraint("issuer_id IN ('home-assistant:echo','home-assistant:victoria') "
                    "AND authorization_generation > 0 AND registration_revision > 0",
                    name="link_challenge_issuer_revision"),
    CheckConstraint("expires_at > created_at AND expires_at <= created_at + interval '5 minutes'",
                    name="link_challenge_window"),
    CheckConstraint("(consumed_proof_id IS NULL AND consumed_subject IS NULL AND consumed_at IS NULL) OR "
                    "(consumed_proof_id IS NOT NULL AND consumed_subject IS NOT NULL AND consumed_subject <> '' "
                    "AND consumed_subject = btrim(consumed_subject) AND consumed_at IS NOT NULL "
                    "AND consumed_at >= created_at AND consumed_at < expires_at)",
                    name="link_challenge_consumption"),
    digest("session_commitment"), digest("challenge_commitment"), schema="identity")

SHARED_LINK_CHALLENGE_TABLES = (shared_owner_generations, shared_link_ceremonies, shared_link_challenges)
