"""Add the dormant E5d parent-relationship authority foundation.

Revision ID: 0018_parent_relationship_e5d
Revises: 0017_authenticated_binding_e5c
Create Date: 2026-07-27

This revision creates owner-only persistence for one exact, private,
two-parent proposal and its future normalized authority receipt. It creates no fact,
stages no live proposal, grants no runtime access, and mounts no API.

The future commit kernel must run at SERIALIZABLE isolation and atomically:
revalidate current identity authority, the active self-binding, both current
People rows, both legacy role labels, privacy/retrieval/erasure state, the
empty open-parent-fact snapshot, policy and proposal digests; mint and consume
one fresh authenticated confirmation artifact; create one committed memory
transaction, two explicit parent_of fact versions, both confirmation and
legacy-context support for each fact, both normalized receipt edges, and the
single authority receipt; and consume the proposal/request. Any mismatch must
roll back the complete transaction.

Production remains pinned to revision 0006a and record_only. Do not activate a
runtime writer until the erasure kernel, split credentials, authenticated
adapter, private BFF route, UI, replay suite, and hosted PostgreSQL gate cover
these tables.
"""

from __future__ import annotations

from typing import Sequence

from alembic import op

from app import schema


revision: str = "0018_parent_relationship_e5d"
down_revision: str | None = "0017_authenticated_binding_e5c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


FOUNDATION_TABLES = (
    schema.parent_relationship_requests,
    schema.parent_relationship_proposals,
    schema.parent_relationship_proposal_edges,
    schema.parent_relationship_authority_receipts,
    schema.parent_relationship_authority_receipt_edges,
)

NON_OWNER_ROLES = (
    "PUBLIC",
    "home_agent_api",
    "home_agent_binding_operator",
    "home_agent_binding_committer",
    "home_agent_identity_binding_kernel",
    "home_agent_ingest",
    "home_agent_worker",
    "home_agent_erasure",
    "home_agent_rollout",
    "home_agent_backup",
    "home_agent_identity_migration",
    "home_agent_identity_kernel",
    "home_agent_identity_finalizer",
    "home_agent_identity_finalizer_kernel",
    "home_agent_identity_cutover",
    "home_agent_identity_cutover_kernel",
    "home_agent_identity_authority_kernel",
    "home_agent_identity_erasure_kernel",
)

TABLE_COMMENTS = {
    "parent_relationship_requests": (
        "Private principal-bound request for one atomic two-parent cutover. "
        "E5d foundation rows are owner-only and cannot authorize facts."
    ),
    "parent_relationship_proposals": (
        "Digest-bound 15-minute proposal header. Exactly two normalized edges "
        "must be present and later consumed together or not at all."
    ),
    "parent_relationship_proposal_edges": (
        "Private references to existing People rows and reviewed legacy parent "
        "labels; labels remain context and are never themselves authority."
    ),
    "parent_relationship_authority_receipts": (
        "Content-minimized receipt header reserved for a future SERIALIZABLE "
        "kernel that creates exactly two explicit parent_of facts atomically."
    ),
    "parent_relationship_authority_receipt_edges": (
        "Normalized receipt-to-fact/support linkage reserved for exactly two "
        "future committed edges; no names, utterances, or locations are stored."
    ),
}


def _qualified(table) -> str:
    return f'"{table.schema}"."{table.name}"'


def _owner_policy(table, operation: str) -> str:
    return f"{table.name}_e5d_owner_{operation.casefold()}"


def upgrade() -> None:
    bind = op.get_bind()
    for table in FOUNDATION_TABLES:
        table.create(bind=bind, checkfirst=True)

    revoked_roles = ", ".join(NON_OWNER_ROLES)
    for table in FOUNDATION_TABLES:
        qualified = _qualified(table)
        comment = TABLE_COMMENTS[table.name].replace("'", "''")
        op.execute(f"COMMENT ON TABLE {qualified} IS '{comment}'")
        op.execute(f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {qualified} FORCE ROW LEVEL SECURITY")
        for operation in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            policy = _owner_policy(table, operation)
            op.execute(f"DROP POLICY IF EXISTS {policy} ON {qualified}")
            if operation == "SELECT":
                clause = "USING (session_user = 'home_agent_owner')"
            elif operation == "INSERT":
                clause = "WITH CHECK (session_user = 'home_agent_owner')"
            elif operation == "UPDATE":
                clause = (
                    "USING (session_user = 'home_agent_owner') "
                    "WITH CHECK (session_user = 'home_agent_owner')"
                )
            else:
                clause = "USING (session_user = 'home_agent_owner')"
            op.execute(
                f"CREATE POLICY {policy} ON {qualified} FOR {operation} "
                f"TO home_agent_owner {clause}"
            )
        op.execute(
            f"REVOKE ALL PRIVILEGES ON TABLE {qualified} FROM {revoked_roles}"
        )
        op.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {qualified} "
            "TO home_agent_owner"
        )


def downgrade() -> None:
    predicates = " OR ".join(
        f"EXISTS (SELECT 1 FROM {_qualified(table)})"
        for table in FOUNDATION_TABLES
    )
    op.execute(
        f"""
        DO $e5d_nonempty$
        BEGIN
          IF {predicates} THEN
            RAISE EXCEPTION
              'refusing to drop nonempty E5d parent authority evidence'
              USING ERRCODE = '2BP01';
          END IF;
        END
        $e5d_nonempty$;
        """
    )
    for table in reversed(FOUNDATION_TABLES):
        table.drop(bind=op.get_bind(), checkfirst=False)
