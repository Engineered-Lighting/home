"""Add owner-only Phase 3 reviewed identity-migration authority records.

Revision ID: 0007_phase3_identity_authority
Revises: 0006a_worker_lease_arbitration
Create Date: 2026-07-12

This revision is schema groundwork only. It deliberately installs no runtime
writer, finalizer function, readiness gate, or parent-confirmation authority.
The future finalizer must accept the complete confirmed payload set in one
SERIALIZABLE transaction, create every semantic projection and exact receipt,
and append the candidate finalization atomically. It must independently prove
dense zero-based source/decision ordinals, exact run counts and commitments,
all six passed privacy categories, signature/build bindings, and the exact
shadow predecessor. It must also reject collisions with or adoption of every
pre-0007 legacy-source projection.

Private source values never enter these tables. ``*_commitment`` columns are
domain-separated keyed HMAC commitments; public build/policy artifacts use
``*_digest``. Enrollment/recognition source rows are manifest items and require
an explicit apply, suppression, rule exclusion, or coalescing decision. The
fixed source-projection contract binds schema-v1 tables outside the selected
identity domain (including preferences, notes, and change logs) without
persisting their content or paths.

The legacy writer evidence strengths in this revision are point-in-time only.
Neither ``observed_stopped`` nor ``operator_attested`` means physically frozen
or enforced offline, so finalization and cutover rows remain explicitly
candidate/unverified and can never satisfy authoritative readiness.

The runtime migration pin intentionally remains revision 0006a on this branch.
Do not run the normal ``alembic upgrade head``/migrate deployment profile with
this groundwork release: Core must fail closed on revision 0007 until the later
atomic finalizer and authoritative-readiness release updates the runtime pin.
"""

from __future__ import annotations

from typing import Sequence

from alembic import op
from sqlalchemy.schema import CreateTable

from app import schema


revision: str = "0007_phase3_identity_authority"
down_revision: str | None = "0006a_worker_lease_arbitration"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


PHASE3_IDENTITY_TABLES = (
    schema.reviewed_identity_migration_runs,
    schema.reviewed_identity_migration_source_items,
    schema.reviewed_identity_migration_decisions,
    schema.reviewed_identity_migration_item_receipts,
    schema.reviewed_identity_migration_finalizations,
    schema.legacy_identity_writer_evidence,
    schema.privacy_cutover_check_receipts,
    schema.semantic_authority_cutovers,
    schema.reviewed_identity_migration_erasure_impacts,
)

RUNTIME_AND_OPERATOR_ROLES = (
    "home_agent_api",
    "home_agent_binding_operator",
    "home_agent_ingest",
    "home_agent_worker",
    "home_agent_erasure",
    "home_agent_rollout",
    "home_agent_backup",
)

TABLE_COMMENTS = {
    "reviewed_identity_migration_runs": (
        "Owner-only signed review header. Aggregate counts are never sufficient; "
        "source items and decisions must exist before a future atomic finalizer. "
        "Pre-0007 semantic rows remain unadopted and unreviewed."
    ),
    "reviewed_identity_migration_source_items": (
        "Content-free, zero-based source-row manifest. Commitments are keyed and "
        "duplicate allowed-projection commitments are valid across distinct row keys."
    ),
    "reviewed_identity_migration_decisions": (
        "Content-free projection or explicit-omission decision manifest. Every "
        "enrollment row requires an explicit decision; silent omission is invalid."
    ),
    "reviewed_identity_migration_item_receipts": (
        "Candidate exact-projection receipts. A future finalizer must insert all "
        "projections and receipts in its single SERIALIZABLE transaction."
    ),
    "reviewed_identity_migration_finalizations": (
        "Candidate-only finalization record. Revision 0007 cannot verify density, "
        "completeness, transaction atomicity, or authority."
    ),
    "legacy_identity_writer_evidence": (
        "Signed point-in-time writer observation bound to one installation, semantic "
        "generation, projection commitment, and build; never a freeze assertion."
    ),
    "privacy_cutover_check_receipts": (
        "Fixed-category candidate privacy checks. A future cutover kernel must require "
        "all six categories passed with residual_code none."
    ),
    "semantic_authority_cutovers": (
        "Candidate-unenforced cutover attestation. It is non-authoritative until a "
        "future migration adds and verifies enforced-offline writer evidence."
    ),
    "reviewed_identity_migration_erasure_impacts": (
        "Run-level erasure suspension after linkable leaf commitments are removed. "
        "Presence must invalidate future readiness; no retained leaf FK is allowed."
    ),
}


def _qualified(table) -> str:
    return f'"{table.schema}"."{table.name}"'


def upgrade() -> None:
    for table in PHASE3_IDENTITY_TABLES:
        op.execute(CreateTable(table, if_not_exists=True))

    for table in PHASE3_IDENTITY_TABLES:
        qualified = _qualified(table)
        comment = TABLE_COMMENTS[table.name].replace("'", "''")
        op.execute(f"COMMENT ON TABLE {qualified} IS '{comment}'")
        op.execute(f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {qualified} FORCE ROW LEVEL SECURITY")
        select_policy = f"{table.name}_owner_select"
        insert_policy = f"{table.name}_owner_insert"
        op.execute(f"DROP POLICY IF EXISTS {select_policy} ON {qualified}")
        op.execute(f"DROP POLICY IF EXISTS {insert_policy} ON {qualified}")
        op.execute(
            f"CREATE POLICY {select_policy} ON {qualified} FOR SELECT "
            "TO home_agent_owner "
            "USING (session_user = 'home_agent_owner')"
        )
        op.execute(
            f"CREATE POLICY {insert_policy} ON {qualified} FOR INSERT "
            "TO home_agent_owner "
            "WITH CHECK (session_user = 'home_agent_owner')"
        )
        roles = ", ".join(RUNTIME_AND_OPERATOR_ROLES)
        op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {qualified} FROM PUBLIC, {roles}")
        op.execute(f"GRANT SELECT, INSERT ON TABLE {qualified} TO home_agent_owner")


def downgrade() -> None:
    predicates = " OR ".join(
        f"EXISTS (SELECT 1 FROM {_qualified(table)})"
        for table in PHASE3_IDENTITY_TABLES
    )
    op.execute(
        f"""
        DO $$
        BEGIN
          IF {predicates} THEN
            RAISE EXCEPTION
              'refusing to drop nonempty Phase 3 identity authority evidence'
              USING ERRCODE = '2BP01';
          END IF;
        END
        $$
        """
    )
    for table in reversed(PHASE3_IDENTITY_TABLES):
        op.drop_table(table.name, schema=table.schema)
