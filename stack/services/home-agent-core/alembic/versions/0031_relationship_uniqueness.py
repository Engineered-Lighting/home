"""Give every relationship predicate the uniqueness guard two of them had.

0023 and 0026 each installed a partial unique index scoped to a single
predicate -- ``uq_active_partner_relationship`` for ``partner_of`` and
``uq_active_parent_relationship`` for ``parent_of`` -- so an active
relationship of that kind between two people could only be recorded once.

0030 widened the vocabulary to seven predicates but left those two indexes
alone, because each names its predicate in a ``WHERE`` clause rather than
deriving it. The five new predicates therefore have no guard at all: recording
the same friendship twice writes two active rows, and nothing rejects the
second. That is the same oversight as the receipt CHECKs 0030 missed -- a
predicate-specific object that has to be revisited whenever the vocabulary
moves.

The replacement puts ``predicate`` in the index key instead of in the
predicate-specific ``WHERE``, so it enforces per-predicate uniqueness for every
member of the vocabulary and cannot go stale the next time one is added. It
subsumes both originals exactly, so they are dropped rather than left beside it.

Revision ID: 0031_relationship_uniqueness_e5r
Revises: 0030_relationship_vocabulary_e5q
"""

from __future__ import annotations

from alembic import op

revision: str = "0031_relationship_uniqueness_e5r"
down_revision: str | None = "0030_relationship_vocabulary_e5q"
branch_labels: str | None = None
depends_on: str | None = None

VOCABULARY = (
    "colleague_of",
    "friend_of",
    "neighbor_of",
    "parent_of",
    "partner_of",
    "roommate_of",
    "sibling_of",
)

INDEX = "uq_active_relationship"


def upgrade() -> None:
    # Keyed on predicate rather than scoped to one, so the guard covers every
    # predicate the vocabulary admits today and any it admits later.
    #
    # The predicate list is written out rather than joined from VOCABULARY at
    # runtime: a list built inside the function is invisible to anything reading
    # these migrations statically, and reading them is how a predicate-scoped
    # index gets caught before it reaches a database.
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_active_relationship
            ON knowledge.fact_versions (
              subject_id,
              predicate,
              ((object ->> 'person_id')),
              perspective_principal_id
            )
         WHERE predicate IN (
                 'colleague_of', 'friend_of', 'neighbor_of', 'parent_of',
                 'partner_of', 'roommate_of', 'sibling_of'
               )
           AND upper_inf(system_range)
           AND resolution = 'accepted';
        """
    )

    # Dropped only after the replacement exists, so no window passes without a
    # uniqueness guard on the two predicates that already had one. Written out
    # rather than looped: a statement built from a loop variable cannot be read
    # statically, and the test that catches predicate-scoped indexes reads these
    # migrations rather than executing them.
    op.execute("DROP INDEX IF EXISTS knowledge.uq_active_partner_relationship;")
    op.execute("DROP INDEX IF EXISTS knowledge.uq_active_parent_relationship;")


def downgrade() -> None:
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_active_partner_relationship
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
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_active_parent_relationship
            ON knowledge.fact_versions (
              subject_id,
              ((object ->> 'person_id')),
              perspective_principal_id
            )
         WHERE predicate = 'parent_of'
           AND upper_inf(system_range)
           AND resolution = 'accepted';
        """
    )
    op.execute("DROP INDEX IF EXISTS knowledge.uq_active_relationship;")
