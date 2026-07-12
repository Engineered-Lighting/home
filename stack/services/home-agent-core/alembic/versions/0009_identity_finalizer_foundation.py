"""Add dormant, normalized lineage for a future identity finalizer.

Revision ID: 0009_identity_finalizer_base
Revises: 0008_identity_migration_kernel
Create Date: 2026-07-12

This revision is schema foundation only. It installs no callable finalizer,
does not project semantic rows, and cannot make a migration or cutover
authoritative. The production runtime pin intentionally remains revision
0006; normal deployment must continue to fail closed at this revision.
The foundation's ``finalization_enabled`` state is therefore ``false``.

The two lineage tables normalize one reviewed apply receipt to one typed
projection target and then identify the one or two people affected by that
target. They contain identifiers, categorical roles, and keyed commitments
only. They contain no names, aliases, source refs, external identifiers,
utterances, coordinates, snapshots, or relationship facts. In particular,
``from`` and ``to`` are lineage roles for a legacy-candidate projection, not
semantic assertions.

The future callable kernel must populate the semantic projection, receipt,
lineage, affected-person rows, privacy side effects, and non-authoritative
finalization in one SERIALIZABLE transaction. Revision 0009 deliberately
grants neither finalizer role any table privilege.
"""

from __future__ import annotations

from typing import Sequence

from alembic import op
from sqlalchemy.schema import CreateTable

from app import schema


revision: str = "0009_identity_finalizer_base"
down_revision: str | None = "0008_identity_migration_kernel"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


FINALIZER_ROLE = "home_agent_identity_finalizer"
FINALIZER_KERNEL_ROLE = "home_agent_identity_finalizer_kernel"

RUNTIME_AND_OPERATOR_ROLES = (
    "home_agent_api",
    "home_agent_binding_operator",
    "home_agent_ingest",
    "home_agent_worker",
    "home_agent_erasure",
    "home_agent_rollout",
    "home_agent_backup",
    "home_agent_identity_kernel",
    "home_agent_identity_migration",
    FINALIZER_ROLE,
    FINALIZER_KERNEL_ROLE,
)

FOUNDATION_TABLES = (
    schema.reviewed_identity_migration_projection_lineage,
    schema.reviewed_identity_migration_projection_subjects,
)

UNSAFE_PREEXISTING_EVIDENCE_TABLES = (
    "operations.reviewed_identity_migration_item_receipts",
    "operations.reviewed_identity_migration_finalizations",
    "operations.legacy_identity_writer_evidence",
    "operations.privacy_cutover_check_receipts",
    "operations.semantic_authority_cutovers",
)

RECEIPT_LINEAGE_CONSTRAINT = (
    "uq_reviewed_identity_migration_item_receipts_lineage_identity"
)

TABLE_COMMENTS = {
    "reviewed_identity_migration_projection_lineage": (
        "Dormant, non-authoritative exact receipt-to-projection lineage. "
        "Projection IDs are typed target identifiers; commitments are keyed. "
        "No private projection content is stored here."
    ),
    "reviewed_identity_migration_projection_subjects": (
        "Dormant, non-authoritative affected-person lineage. Roles primary, "
        "from, and to describe projection participation only and never assert "
        "a semantic relationship or parent fact."
    ),
}


def _qualified(table) -> str:
    return f'"{table.schema}"."{table.name}"'


def _require_dormant_roles() -> None:
    op.execute(
        f"""
        DO $roles$
        BEGIN
          IF NOT EXISTS (
            SELECT 1
              FROM pg_catalog.pg_roles AS finalizer_role
             WHERE finalizer_role.rolname = '{FINALIZER_ROLE}'
               AND finalizer_role.rolcanlogin = true
               AND finalizer_role.rolinherit = false
               AND finalizer_role.rolsuper = false
               AND finalizer_role.rolcreatedb = false
               AND finalizer_role.rolcreaterole = false
               AND finalizer_role.rolreplication = false
               AND finalizer_role.rolbypassrls = false
               AND finalizer_role.rolconnlimit = 1
               AND finalizer_role.rolvaliduntil IS NOT NULL
               AND finalizer_role.rolvaliduntil <=
                   pg_catalog.transaction_timestamp()
               AND NOT EXISTS (
                 SELECT 1
                   FROM pg_catalog.pg_auth_members AS membership
                  WHERE membership.member = finalizer_role.oid
                     OR membership.roleid = finalizer_role.oid
               )
               AND NOT EXISTS (
                 SELECT 1
                   FROM pg_catalog.pg_shdepend AS owned_object
                  WHERE owned_object.refobjid = finalizer_role.oid
                    AND owned_object.deptype = 'o'
               )
          ) OR NOT EXISTS (
            SELECT 1
              FROM pg_catalog.pg_roles AS kernel_role
             WHERE kernel_role.rolname = '{FINALIZER_KERNEL_ROLE}'
               AND kernel_role.rolcanlogin = false
               AND kernel_role.rolinherit = false
               AND kernel_role.rolsuper = false
               AND kernel_role.rolcreatedb = false
               AND kernel_role.rolcreaterole = false
               AND kernel_role.rolreplication = false
               AND kernel_role.rolbypassrls = false
               AND kernel_role.rolconnlimit = 0
               AND NOT EXISTS (
                 SELECT 1
                   FROM pg_catalog.pg_auth_members AS membership
                  WHERE membership.member = kernel_role.oid
               )
               AND (
                 SELECT pg_catalog.count(*)
                   FROM pg_catalog.pg_auth_members AS membership
                  WHERE membership.roleid = kernel_role.oid
               ) = 1
               AND EXISTS (
                 SELECT 1
                   FROM pg_catalog.pg_auth_members AS membership
                   JOIN pg_catalog.pg_roles AS owner_role
                     ON owner_role.oid = membership.member
                  WHERE membership.roleid = kernel_role.oid
                    AND owner_role.rolname = 'home_agent_owner'
                    AND membership.admin_option = false
                    AND membership.inherit_option = false
                    AND membership.set_option = true
               )
               AND NOT EXISTS (
                 SELECT 1
                   FROM pg_catalog.pg_shdepend AS owned_object
                  WHERE owned_object.refobjid = kernel_role.oid
                    AND owned_object.deptype = 'o'
               )
          ) THEN
            RAISE EXCEPTION 'identity_finalizer_dormant_roles_invalid'
              USING ERRCODE = '42704';
          END IF;
        END
        $roles$
        """
    )


def _refuse_unsafe_preexisting_evidence() -> None:
    evidence = " OR ".join(
        f"EXISTS (SELECT 1 FROM {table})"
        for table in UNSAFE_PREEXISTING_EVIDENCE_TABLES
    )
    op.execute(
        f"""
        DO $foundation_admission$
        BEGIN
          IF {evidence} THEN
            RAISE EXCEPTION 'identity_finalizer_preexisting_evidence_unsafe'
              USING ERRCODE = '55000';
          END IF;
        END
        $foundation_admission$
        """
    )


def _prepare_foundation_objects() -> None:
    # Revision 0001 intentionally builds from current metadata. A fresh
    # 0001->head installation therefore reaches 0009 with these two exact,
    # empty tables and the exact receipt unique already present. Remove only
    # that all-or-nothing empty bootstrap shape, then recreate it below under
    # the 0009 policy/ACL contract. Existing installations at 0008 have none.
    op.execute(
        f"""
        DO $foundation_objects$
        DECLARE
          lineage_table regclass;
          subjects_table regclass;
          receipt_constraint oid;
          bootstrap_rows bigint;
        BEGIN
          lineage_table := pg_catalog.to_regclass(
            'operations.reviewed_identity_migration_projection_lineage'
          );
          subjects_table := pg_catalog.to_regclass(
            'operations.reviewed_identity_migration_projection_subjects'
          );
          IF (lineage_table IS NULL) <> (subjects_table IS NULL) THEN
            RAISE EXCEPTION 'identity_finalizer_foundation_name_occupied'
              USING ERRCODE = '55000';
          END IF;
          IF lineage_table IS NOT NULL THEN
            EXECUTE 'SELECT pg_catalog.count(*) FROM '
                    || lineage_table::text
              INTO bootstrap_rows;
            IF bootstrap_rows <> 0 THEN
              RAISE EXCEPTION 'identity_finalizer_foundation_name_occupied'
                USING ERRCODE = '55000';
            END IF;
            EXECUTE 'SELECT pg_catalog.count(*) FROM '
                    || subjects_table::text
              INTO bootstrap_rows;
            IF bootstrap_rows <> 0 THEN
              RAISE EXCEPTION 'identity_finalizer_foundation_name_occupied'
                USING ERRCODE = '55000';
            END IF;
            DROP TABLE operations.reviewed_identity_migration_projection_subjects;
            DROP TABLE operations.reviewed_identity_migration_projection_lineage;
          END IF;

          IF (
            SELECT pg_catalog.count(*)
              FROM pg_catalog.pg_constraint AS named_constraint
             WHERE named_constraint.conname = '{RECEIPT_LINEAGE_CONSTRAINT}'
          ) > 1 THEN
            RAISE EXCEPTION 'identity_finalizer_receipt_constraint_occupied'
              USING ERRCODE = '55000';
          END IF;
          SELECT exact_constraint.oid
            INTO receipt_constraint
            FROM pg_catalog.pg_constraint AS exact_constraint
           WHERE exact_constraint.conname = '{RECEIPT_LINEAGE_CONSTRAINT}';
          IF receipt_constraint IS NOT NULL THEN
            IF NOT EXISTS (
              SELECT 1
                FROM pg_catalog.pg_constraint AS exact_constraint
               WHERE exact_constraint.oid = receipt_constraint
                 AND exact_constraint.contype = 'u'
                 AND exact_constraint.conrelid =
                     'operations.reviewed_identity_migration_item_receipts'::regclass
                 AND pg_catalog.pg_get_constraintdef(
                       exact_constraint.oid, true
                     ) = 'UNIQUE (run_id, receipt_id, decision_id, '
                         'decision_kind, projection_table_kind, '
                         'projection_ref_commitment)'
            ) THEN
              RAISE EXCEPTION 'identity_finalizer_receipt_constraint_occupied'
                USING ERRCODE = '55000';
            END IF;
            ALTER TABLE operations.reviewed_identity_migration_item_receipts
              DROP CONSTRAINT {RECEIPT_LINEAGE_CONSTRAINT};
          END IF;
        END
        $foundation_objects$
        """
    )


def _quarantine_dormant_roles() -> None:
    roles = f"{FINALIZER_ROLE}, {FINALIZER_KERNEL_ROLE}"
    schemas = "public, ingest, identity, knowledge, engagement, privacy, operations"
    op.execute(f"REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA {schemas} FROM {roles}")
    op.execute(
        f"REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA {schemas} FROM {roles}"
    )
    op.execute(
        "REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA "
        f"ingest, identity, knowledge, engagement, privacy, operations FROM {roles}"
    )
    op.execute(
        f"""
        DO $type_acl$
        DECLARE
          type_entry record;
        BEGIN
          FOR type_entry IN
            SELECT type_namespace.nspname, candidate_type.typname
              FROM pg_catalog.pg_type AS candidate_type
              JOIN pg_catalog.pg_namespace AS type_namespace
                ON type_namespace.oid = candidate_type.typnamespace
             WHERE type_namespace.nspname IN (
               'public','ingest','identity','knowledge','engagement',
               'privacy','operations'
             )
               AND candidate_type.typisdefined
               AND candidate_type.typrelid = 0
               AND candidate_type.typelem = 0
          LOOP
            EXECUTE pg_catalog.format(
              'REVOKE USAGE ON TYPE %I.%I FROM {roles}',
              type_entry.nspname,
              type_entry.typname
            );
          END LOOP;
        END
        $type_acl$
        """
    )
    op.execute(f"REVOKE USAGE, CREATE ON SCHEMA {schemas} FROM {roles}")
    op.execute(
        f"""
        DO $database_acl$
        BEGIN
          EXECUTE pg_catalog.format(
            'REVOKE CREATE, TEMPORARY ON DATABASE %I FROM {FINALIZER_ROLE}',
            pg_catalog.current_database()
          );
          EXECUTE pg_catalog.format(
            'REVOKE ALL PRIVILEGES ON DATABASE %I FROM {FINALIZER_KERNEL_ROLE}',
            pg_catalog.current_database()
          );
        END
        $database_acl$
        """
    )


def _install_receipt_identity() -> None:
    op.create_unique_constraint(
        op.f(RECEIPT_LINEAGE_CONSTRAINT),
        "reviewed_identity_migration_item_receipts",
        [
            "run_id",
            "receipt_id",
            "decision_id",
            "decision_kind",
            "projection_table_kind",
            "projection_ref_commitment",
        ],
        schema="operations",
    )


def _install_foundation_tables() -> None:
    for table in FOUNDATION_TABLES:
        op.execute(CreateTable(table))
        qualified = _qualified(table)
        comment = TABLE_COMMENTS[table.name].replace("'", "''")
        op.execute(f"COMMENT ON TABLE {qualified} IS '{comment}'")
        op.execute(f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {qualified} FORCE ROW LEVEL SECURITY")

        select_policy = f"{table.name}_owner_select"
        insert_policy = f"{table.name}_owner_insert"
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
        op.execute(
            f"REVOKE ALL PRIVILEGES ON TABLE {qualified} " f"FROM PUBLIC, {roles}"
        )
        op.execute(f"GRANT SELECT, INSERT ON TABLE {qualified} TO home_agent_owner")


def upgrade() -> None:
    _require_dormant_roles()
    _refuse_unsafe_preexisting_evidence()
    _prepare_foundation_objects()
    _quarantine_dormant_roles()
    _install_receipt_identity()
    _install_foundation_tables()


def downgrade() -> None:
    # Refusing downgrade after any lineage or finalization evidence prevents a
    # schema rollback from silently destroying the only normalized erasure map.
    evidence = " OR ".join(
        [
            *(
                f"EXISTS (SELECT 1 FROM {_qualified(table)})"
                for table in FOUNDATION_TABLES
            ),
            *(
                f"EXISTS (SELECT 1 FROM {table})"
                for table in UNSAFE_PREEXISTING_EVIDENCE_TABLES
            ),
        ]
    )
    op.execute(
        f"""
        DO $downgrade$
        BEGIN
          IF {evidence} THEN
            RAISE EXCEPTION 'identity_finalizer_foundation_downgrade_blocked'
              USING ERRCODE = '2BP01';
          END IF;
        END
        $downgrade$
        """
    )
    for table in reversed(FOUNDATION_TABLES):
        op.drop_table(table.name, schema=table.schema)
    op.drop_constraint(
        op.f(RECEIPT_LINEAGE_CONSTRAINT),
        "reviewed_identity_migration_item_receipts",
        schema="operations",
        type_="unique",
    )
