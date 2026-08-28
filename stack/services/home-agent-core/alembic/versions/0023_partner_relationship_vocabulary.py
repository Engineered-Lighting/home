"""Admit partner_of into the relationship vocabulary, with its own invariants.

Revision ID: 0023_partner_vocabulary_e5j
Revises: 0022_fact_suppression_e5i
Create Date: 2026-08-28

A predicate is not a value, it is a contract. knowledge.fact_versions.predicate
is varchar(128) with no CHECK, so the storage layer would accept anything;
everything that makes a predicate safe lives elsewhere, and has to be added
deliberately.

parent_of carries uq_active_parent_relationship. Without an equivalent, a second
predicate gets no uniqueness enforcement at all -- the same pair could be
asserted twice and both rows would be 'accepted' and current.

partner_of differs from parent_of in one way that matters: it is symmetric.
The legacy store models that as two directed rows (Ashley->Felipe and
Felipe->Ashley), and the fact model follows it, exactly as parent_of uses two
facts for two parents. The uniqueness index therefore has the same shape --
it makes each DIRECTED edge unique -- and the symmetric pair is a transactional
invariant for the commit kernel, not a schema one.

This revision adds no ability to WRITE a partner_of fact. It only ensures that
when a kernel is able to, the fact cannot be duplicated and cannot name a person
as their own partner. Ordering is deliberate: 0022 closes the object-side
erasure hole first, so partner_of can never exist while erased people are
visible through it.
"""

from __future__ import annotations

from typing import Sequence

from alembic import op


revision: str = "0023_partner_vocabulary_e5j"
down_revision: str | None = "0022_fact_suppression_e5i"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # Mirrors uq_active_parent_relationship. Unique per directed edge, scoped to
    # currently-believed accepted facts, so a retracted edge does not block a
    # later re-assertion.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_active_partner_relationship
            ON knowledge.fact_versions (
              subject_id,
              ((object ->> 'person_id')),
              perspective_principal_id
            )
         WHERE predicate = 'partner_of'
           AND upper_inf(system_range)
           AND resolution = 'accepted';
        """
    )
    # Scoped to partner_of so no other predicate's meaning is constrained.
    # Nobody is their own partner, and a self-edge would also defeat the
    # symmetry invariant the kernel will rely on.
    op.execute(
        """
        ALTER TABLE knowledge.fact_versions
          ADD CONSTRAINT partner_relationship_is_not_reflexive
          CHECK (
            predicate <> 'partner_of'
            OR (object ->> 'person_id') IS DISTINCT FROM subject_id::text
          );
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE knowledge.fact_versions "
        "DROP CONSTRAINT partner_relationship_is_not_reflexive;"
    )
    op.execute("DROP INDEX knowledge.uq_active_partner_relationship;")
