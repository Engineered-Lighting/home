"""Add the dormant identity-erasure schema and kernel-role foundation.

Revision ID: 0011_identity_erasure_e1
Revises: 0010_identity_erasure_source
Create Date: 2026-07-12

This revision is schema foundation only.  It adds no erasure function, no
worker authority, and no direct privilege for an online role.  Existing
descriptor-scoped erasure requests remain valid and unchanged; a manual
whole-person identity erasure is represented only when the request has one
separately confirmed, normalized ``person_erasure_scopes`` row.

The dormant person-level retrieval-block marker is source-bound to that exact
manual scope and its normalized operation. Auto-expiry is intentionally not an
E1 authority source; it requires a separately reviewed hardening migration. A
completion receipt is source-bound to one run/operation impact and cannot exist
without the operation's mandatory block marker. Retrieval
does not consult this marker until a later, separately gated slice. All tables
contain pseudonymous identifiers, categorical state, counts, timestamps, and
keyed commitments only.
"""

from __future__ import annotations

from typing import Sequence

from alembic import op


revision: str = "0011_identity_erasure_e1"
down_revision: str | None = "0010_identity_erasure_source"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


KERNEL_ROLE = "home_agent_identity_erasure_kernel"

PERSON_SCOPE = "privacy.person_erasure_scopes"
SUBJECT_BLOCK = "privacy.subject_retrieval_blocks"
ERASURE_RECEIPT = (
    "operations.reviewed_identity_migration_erasure_receipts"
)
OPERATION = "operations.reviewed_identity_migration_erasure_operations"
IMPACT = "operations.reviewed_identity_migration_erasure_impacts"

FOUNDATION_TABLES = (PERSON_SCOPE, SUBJECT_BLOCK, ERASURE_RECEIPT)
RUNTIME_AND_OPERATOR_ROLES = (
    "home_agent_api",
    "home_agent_binding_operator",
    "home_agent_ingest",
    "home_agent_worker",
    "home_agent_erasure",
    "home_agent_rollout",
    "home_agent_backup",
    "home_agent_identity_migration",
    "home_agent_identity_kernel",
    "home_agent_identity_finalizer",
    "home_agent_identity_finalizer_kernel",
    KERNEL_ROLE,
)

SUPPORT_CONSTRAINTS = (
    (
        "identity.principals",
        "identity_erasure_principal_kind_authority",
        "UNIQUE (principal_id, person_id, kind)",
    ),
    (
        "identity.ha_user_bindings",
        "identity_erasure_ha_binding_authority",
        "UNIQUE (binding_id, principal_id, person_id, confirmed_at, "
        "identity_erasure_binding_valid_until)",
    ),
    (
        "identity.confirmation_artifacts",
        "identity_erasure_confirmation_authority",
        "UNIQUE (artifact_id, principal_id, purpose, proposal_digest, "
        "consumed_at, expires_at)",
    ),
    (
        "privacy.erasure_requests",
        "identity_erasure_request_authority",
        "UNIQUE (erasure_request_id, principal_id, "
        "identity_person_scope_commitment)",
    ),
    (
        OPERATION,
        "identity_erasure_operation_request_source",
        "UNIQUE (operation_id, erasure_request_id)",
    ),
    (
        IMPACT,
        "identity_erasure_impact_receipt_source",
        "UNIQUE (impact_id, run_id, operation_id, impact_code, "
        "removed_leaf_commitment_count, unlinked_projection_count)",
    ),
)

REQUEST_SCOPE_CHECK = "identity_person_erasure_scope_commitment_shape"
REQUEST_SCOPE_COLUMN = "identity_person_scope_commitment"
BINDING_VALID_COLUMN = "identity_erasure_binding_valid_until"

RELIED_SUPPORT_CONSTRAINTS = (
    ("identity.principals", "principal_person_identity", "UNIQUE (principal_id, person_id)"),
    ("identity.principals", "fk_principals_person_id_people", "FOREIGN KEY (person_id) REFERENCES identity.people(person_id)"),
    ("identity.principals", "ck_principals_principal_kind", "CHECK (kind::text = ANY (ARRAY['ha_user'::character varying, 'voice_unknown'::character varying, 'service'::character varying]::text[]))"),
    ("identity.ha_user_bindings", "uq_ha_user_bindings_proposal_id", "UNIQUE (proposal_id)"),
    ("identity.ha_user_bindings", "fk_ha_user_bindings_proposal_id_principal_binding_proposals", "FOREIGN KEY (proposal_id) REFERENCES identity.principal_binding_proposals(proposal_id) ON DELETE CASCADE"),
    ("identity.ha_user_bindings", "fk_ha_user_bindings_principal_id_principals", "FOREIGN KEY (principal_id) REFERENCES identity.principals(principal_id)"),
    ("identity.ha_user_bindings", "fk_ha_user_bindings_person_id_people", "FOREIGN KEY (person_id) REFERENCES identity.people(person_id)"),
    ("identity.ha_user_bindings", "fk_ha_user_bindings_confirmed_by_principal_id_principals", "FOREIGN KEY (confirmed_by_principal_id) REFERENCES identity.principals(principal_id)"),
    ("identity.ha_user_bindings", "fk_ha_user_bindings_source_artifact_id_confirmation_artifacts", "FOREIGN KEY (source_artifact_id) REFERENCES identity.confirmation_artifacts(artifact_id)"),
    ("identity.ha_user_bindings", "ha_binding_principal_person", "FOREIGN KEY (principal_id, person_id) REFERENCES identity.principals(principal_id, person_id)"),
    ("identity.ha_user_bindings", "ck_ha_user_bindings_ha_binding_self_confirmation", "CHECK (confirmed_by_principal_id = principal_id)"),
    ("identity.ha_user_bindings", "ck_ha_user_bindings_ha_binding_active_artifact", "CHECK (revoked_at IS NOT NULL OR source_artifact_id IS NOT NULL)"),
    ("identity.confirmation_artifacts", "fk_confirmation_artifacts_principal_id_principals", "FOREIGN KEY (principal_id) REFERENCES identity.principals(principal_id)"),
    ("identity.confirmation_artifacts", "confirmation_nonce_once", "UNIQUE (principal_id, client_nonce_sha256)"),
    ("identity.confirmation_artifacts", "ck_confirmation_artifacts_confirmation_digests", "CHECK (proposal_digest::text ~ '^[0-9a-f]{64}$'::text AND client_nonce_sha256::text ~ '^[0-9a-f]{64}$'::text)"),
    ("identity.confirmation_artifacts", "ck_confirmation_artifacts_confirmation_consumed_in_window", "CHECK (consumed_at >= issued_at AND consumed_at <= expires_at)"),
    ("privacy.erasure_requests", "fk_erasure_requests_principal_id_principals", "FOREIGN KEY (principal_id) REFERENCES identity.principals(principal_id)"),
    ("privacy.erasure_requests", "ck_erasure_requests_erasure_state", "CHECK (state::text = ANY (ARRAY['preview'::character varying, 'blocked'::character varying, 'running'::character varying, 'ledger_pending'::character varying, 'complete'::character varying, 'partial'::character varying, 'failed'::character varying]::text[]))"),
)

RELIED_SUPPORT_POLICIES = (
    ("identity.confirmation_artifacts", "confirmation_artifacts_trusted_maintenance", "*", "((SESSION_USER = 'home_agent_owner'::name) OR ((CURRENT_USER = 'home_agent_owner'::name) AND (SESSION_USER = ANY (ARRAY['home_agent_worker'::name, 'home_agent_erasure'::name]))))"),
    ("identity.confirmation_artifacts", "identity_confirmation_artifacts_principal", "*", "(principal_id = (NULLIF(current_setting('app.principal_id'::text, true), ''::text))::uuid)"),
    ("identity.ha_user_bindings", "ha_user_bindings_ingest_read", "r", "(SESSION_USER = 'home_agent_ingest'::name)"),
    ("identity.ha_user_bindings", "ha_user_bindings_subject_or_operator", "*", "(((ha_user_id)::text = NULLIF(current_setting('app.ha_user_id'::text, true), ''::text)) OR (principal_id = (NULLIF(current_setting('app.principal_id'::text, true), ''::text))::uuid) OR (SESSION_USER = ANY (ARRAY['home_agent_binding_operator'::name, 'home_agent_owner'::name])) OR ((CURRENT_USER = 'home_agent_owner'::name) AND (SESSION_USER = ANY (ARRAY['home_agent_worker'::name, 'home_agent_erasure'::name]))))"),
    ("privacy.erasure_requests", "privacy_erasure_requests_principal", "*", "(principal_id = (NULLIF(current_setting('app.principal_id'::text, true), ''::text))::uuid)"),
)


def _require_dormant_kernel_role() -> None:
    op.execute(
        f"""
        DO $role_contract$
        BEGIN
          IF NOT EXISTS (
            SELECT 1
              FROM pg_catalog.pg_roles AS kernel
             WHERE kernel.rolname = '{KERNEL_ROLE}'
               AND kernel.rolcanlogin = false
               AND kernel.rolinherit = false
               AND kernel.rolsuper = false
               AND kernel.rolcreatedb = false
               AND kernel.rolcreaterole = false
               AND kernel.rolreplication = false
               AND kernel.rolbypassrls = false
               AND kernel.rolconnlimit = 0
               AND NOT EXISTS (
                 SELECT 1
                   FROM pg_catalog.pg_auth_members AS parent_membership
                  WHERE parent_membership.member = kernel.oid
               )
               AND (
                 SELECT pg_catalog.count(*)
                   FROM pg_catalog.pg_auth_members AS child_membership
                  WHERE child_membership.roleid = kernel.oid
               ) = 1
               AND EXISTS (
                 SELECT 1
                   FROM pg_catalog.pg_auth_members AS owner_membership
                   JOIN pg_catalog.pg_roles AS owner_role
                     ON owner_role.oid = owner_membership.member
                  WHERE owner_membership.roleid = kernel.oid
                    AND owner_role.rolname = 'home_agent_owner'
                    AND owner_membership.admin_option = false
                    AND owner_membership.inherit_option = false
                    AND owner_membership.set_option = true
               )
               AND NOT EXISTS (
                 SELECT 1
                   FROM pg_catalog.pg_shdepend AS owned_object
                  WHERE owned_object.refobjid = kernel.oid
                    AND owned_object.deptype = 'o'
               )
          ) THEN
            RAISE EXCEPTION 'identity_erasure_kernel_role_invalid'
              USING ERRCODE = '42704';
          END IF;
        END
        $role_contract$
        """
    )


def _admit_exact_predecessor() -> None:
    target_names = ",".join(f"'{table}'" for table in FOUNDATION_TABLES)
    support_names = ",".join(
        f"'{constraint}'"
        for constraint in (
            *(item[1] for item in SUPPORT_CONSTRAINTS),
            REQUEST_SCOPE_CHECK,
        )
    )
    support_definition_values = ",".join(
        "(" + ",".join(
            (
                f"'{table}'::regclass",
                "'" + name.replace("'", "''") + "'",
                "'" + definition.replace("'", "''") + "'",
            )
        ) + ")"
        for table, name, definition in RELIED_SUPPORT_CONSTRAINTS
    )
    support_policy_values = ",".join(
        "(" + ",".join(
            (
                f"'{table}'::regclass",
                "'" + name.replace("'", "''") + "'",
                "'" + command + "'::\"char\"",
                "'" + expression.replace("'", "''") + "'",
            )
        ) + ")"
        for table, name, command, expression in RELIED_SUPPORT_POLICIES
    )
    op.execute(
        f"""
        DO $admission$
        DECLARE
          owner_oid oid;
        BEGIN
          SELECT oid INTO STRICT owner_oid
            FROM pg_catalog.pg_roles
           WHERE rolname = 'home_agent_owner';

          IF EXISTS (
            SELECT 1
              FROM pg_catalog.unnest(
                     ARRAY[{target_names}]::text[]
                   ) AS target(target_name)
             WHERE pg_catalog.to_regclass(target_name) IS NOT NULL
          ) THEN
            RAISE EXCEPTION 'identity_erasure_e1_name_occupied'
              USING ERRCODE = '55000';
          END IF;

          IF EXISTS (
            SELECT 1
              FROM pg_catalog.pg_constraint
             WHERE conname = ANY (ARRAY[{support_names}]::text[])
          ) THEN
            RAISE EXCEPTION 'identity_erasure_e1_constraint_occupied'
              USING ERRCODE = '55000';
          END IF;

          IF EXISTS (
            SELECT 1
              FROM pg_catalog.pg_attribute
             WHERE (
               (attrelid = 'privacy.erasure_requests'::regclass
                AND attname = '{REQUEST_SCOPE_COLUMN}')
               OR (attrelid = 'identity.ha_user_bindings'::regclass
                   AND attname = '{BINDING_VALID_COLUMN}')
             )
               AND attnum > 0
               AND NOT attisdropped
          ) THEN
            RAISE EXCEPTION 'identity_erasure_e1_column_occupied'
              USING ERRCODE = '55000';
          END IF;

          IF NOT EXISTS (
            SELECT 1
              FROM pg_catalog.pg_class AS operation_table
             WHERE operation_table.oid = '{OPERATION}'::regclass
               AND operation_table.relkind = 'r'
               AND operation_table.relpersistence = 'p'
               AND operation_table.relowner = owner_oid
               AND operation_table.relrowsecurity
               AND operation_table.relforcerowsecurity
          ) OR NOT EXISTS (
            SELECT 1
              FROM pg_catalog.pg_class AS impact_table
             WHERE impact_table.oid = '{IMPACT}'::regclass
               AND impact_table.relkind = 'r'
               AND impact_table.relpersistence = 'p'
               AND impact_table.relowner = owner_oid
               AND impact_table.relrowsecurity
               AND impact_table.relforcerowsecurity
          ) THEN
            RAISE EXCEPTION 'identity_erasure_e1_predecessor_invalid'
              USING ERRCODE = '55000';
          END IF;

          IF EXISTS (
            SELECT 1
              FROM pg_catalog.unnest(
                     ARRAY[
                       'identity.principals',
                       'identity.ha_user_bindings',
                       'identity.confirmation_artifacts',
                       'privacy.erasure_requests'
                     ]::text[]
                   ) AS support(support_name)
             WHERE NOT EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_class AS support_table
                WHERE support_table.oid = support_name::regclass
                  AND support_table.relkind = 'r'
                  AND support_table.relpersistence = 'p'
                  AND support_table.relowner = owner_oid
             )
          ) OR NOT EXISTS (
            SELECT 1
              FROM pg_catalog.pg_class AS confirmation_table
             WHERE confirmation_table.oid =
                   'identity.confirmation_artifacts'::regclass
               AND confirmation_table.relrowsecurity
               AND confirmation_table.relforcerowsecurity
          ) OR NOT EXISTS (
            SELECT 1
              FROM pg_catalog.pg_class AS binding_table
             WHERE binding_table.oid = 'identity.ha_user_bindings'::regclass
               AND binding_table.relrowsecurity
               AND binding_table.relforcerowsecurity
          ) OR NOT EXISTS (
            SELECT 1
              FROM pg_catalog.pg_class AS request_table
             WHERE request_table.oid = 'privacy.erasure_requests'::regclass
               AND request_table.relrowsecurity
          ) THEN
            RAISE EXCEPTION 'identity_erasure_e1_support_boundary_invalid'
              USING ERRCODE = '55000';
          END IF;

          IF EXISTS (
            SELECT 1 FROM (VALUES
              ('identity.principals'::regclass, 'principal_person_identity'),
              ('identity.principals'::regclass,
               'fk_principals_person_id_people'),
              ('identity.principals'::regclass,
               'ck_principals_principal_kind'),
              ('identity.ha_user_bindings'::regclass,
               'uq_ha_user_bindings_proposal_id'),
              ('identity.ha_user_bindings'::regclass,
               'fk_ha_user_bindings_proposal_id_principal_binding_proposals'),
              ('identity.ha_user_bindings'::regclass,
               'fk_ha_user_bindings_principal_id_principals'),
              ('identity.ha_user_bindings'::regclass,
               'fk_ha_user_bindings_person_id_people'),
              ('identity.ha_user_bindings'::regclass,
               'fk_ha_user_bindings_confirmed_by_principal_id_principals'),
              ('identity.ha_user_bindings'::regclass,
               'fk_ha_user_bindings_source_artifact_id_confirmation_artifacts'),
              ('identity.ha_user_bindings'::regclass,
               'ha_binding_principal_person'),
              ('identity.ha_user_bindings'::regclass,
               'ck_ha_user_bindings_ha_binding_self_confirmation'),
              ('identity.ha_user_bindings'::regclass,
               'ck_ha_user_bindings_ha_binding_active_artifact'),
              ('identity.confirmation_artifacts'::regclass,
               'fk_confirmation_artifacts_principal_id_principals'),
              ('identity.confirmation_artifacts'::regclass,
               'confirmation_nonce_once'),
              ('identity.confirmation_artifacts'::regclass,
               'ck_confirmation_artifacts_confirmation_digests'),
              ('identity.confirmation_artifacts'::regclass,
               'ck_confirmation_artifacts_confirmation_consumed_in_window'),
              ('privacy.erasure_requests'::regclass,
               'fk_erasure_requests_principal_id_principals'),
              ('privacy.erasure_requests'::regclass,
               'ck_erasure_requests_erasure_state')
            ) AS required(required_table, required_name)
             WHERE NOT EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_constraint AS support_constraint
                WHERE support_constraint.conname = required_name
                  AND support_constraint.conrelid = required_table
                  AND support_constraint.convalidated
                  AND support_constraint.contype IN ('f','u','c')
             )
          ) OR NOT EXISTS (
            SELECT 1
              FROM pg_catalog.pg_constraint AS principal_kind
             WHERE principal_kind.conrelid = 'identity.principals'::regclass
               AND principal_kind.conname = 'ck_principals_principal_kind'
               AND pg_catalog.pg_get_expr(
                     principal_kind.conbin, principal_kind.conrelid
                   ) LIKE '%ha_user%voice_unknown%service%'
          ) OR NOT EXISTS (
            SELECT 1
              FROM pg_catalog.pg_constraint AS self_confirmation
             WHERE self_confirmation.conrelid =
                   'identity.ha_user_bindings'::regclass
               AND self_confirmation.conname =
                   'ck_ha_user_bindings_ha_binding_self_confirmation'
               AND pg_catalog.pg_get_expr(
                     self_confirmation.conbin, self_confirmation.conrelid
                   ) = '(confirmed_by_principal_id = principal_id)'
          ) OR NOT EXISTS (
            SELECT 1
              FROM pg_catalog.pg_constraint AS confirmation_time
             WHERE confirmation_time.conrelid =
                   'identity.confirmation_artifacts'::regclass
               AND confirmation_time.conname =
                   'ck_confirmation_artifacts_confirmation_consumed_in_window'
               AND pg_catalog.pg_get_expr(
                     confirmation_time.conbin, confirmation_time.conrelid
                   ) LIKE '%consumed_at%issued_at%expires_at%'
          ) THEN
            RAISE EXCEPTION 'identity_erasure_e1_support_constraints_invalid'
              USING ERRCODE = '55000';
          END IF;

          IF EXISTS (
            SELECT 1 FROM (VALUES {support_definition_values})
              AS expected(expected_table, expected_name, expected_definition)
             WHERE NOT EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_constraint AS support_constraint
                WHERE support_constraint.conrelid = expected_table
                  AND support_constraint.conname = expected_name
                  AND support_constraint.convalidated
                  AND pg_catalog.pg_get_constraintdef(
                        support_constraint.oid, true
                      ) = expected_definition
             )
          ) THEN
            RAISE EXCEPTION 'identity_erasure_e1_support_definition_invalid'
              USING ERRCODE = '55000';
          END IF;

          IF (
            SELECT pg_catalog.array_agg(
                     policy.polname::text ORDER BY policy.polname
                   )
              FROM pg_catalog.pg_policy AS policy
             WHERE policy.polrelid =
                   'identity.confirmation_artifacts'::regclass
          ) IS DISTINCT FROM ARRAY[
            'confirmation_artifacts_trusted_maintenance',
            'identity_confirmation_artifacts_principal'
          ]::text[] OR (
            SELECT pg_catalog.array_agg(
                     policy.polname::text ORDER BY policy.polname
                   )
              FROM pg_catalog.pg_policy AS policy
             WHERE policy.polrelid = 'identity.ha_user_bindings'::regclass
          ) IS DISTINCT FROM ARRAY[
            'ha_user_bindings_ingest_read',
            'ha_user_bindings_subject_or_operator'
          ]::text[] OR (
            SELECT pg_catalog.array_agg(
                     policy.polname::text ORDER BY policy.polname
                   )
              FROM pg_catalog.pg_policy AS policy
             WHERE policy.polrelid = 'privacy.erasure_requests'::regclass
          ) IS DISTINCT FROM ARRAY[
            'privacy_erasure_requests_principal'
          ]::text[] THEN
            RAISE EXCEPTION 'identity_erasure_e1_support_policies_invalid'
              USING ERRCODE = '55000';
          END IF;

          IF EXISTS (
            SELECT 1 FROM (VALUES {support_policy_values})
              AS expected(
                expected_table, expected_name, expected_command,
                expected_expression
              )
             WHERE NOT EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_policy AS policy
                WHERE policy.polrelid = expected_table
                  AND policy.polname = expected_name
                  AND policy.polcmd = expected_command
                  AND policy.polpermissive
                  AND policy.polroles = ARRAY[0]::oid[]
                  AND pg_catalog.pg_get_expr(
                        policy.polqual, policy.polrelid
                      ) = expected_expression
                  AND (
                    (expected_command = 'r' AND policy.polwithcheck IS NULL)
                    OR (expected_command <> 'r' AND pg_catalog.pg_get_expr(
                          policy.polwithcheck, policy.polrelid
                        ) = expected_expression)
                  )
             )
          ) THEN
            RAISE EXCEPTION 'identity_erasure_e1_support_policy_shape_invalid'
              USING ERRCODE = '55000';
          END IF;

          IF EXISTS (
            SELECT 1
              FROM pg_catalog.unnest(ARRAY[
                'home_agent_api','home_agent_binding_operator',
                'home_agent_ingest','home_agent_worker','home_agent_erasure',
                'home_agent_rollout','home_agent_backup'
              ]::text[]) AS runtime(role_name)
             WHERE pg_catalog.has_table_privilege(
                     role_name, 'identity.principals',
                     'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'
                   )
                OR pg_catalog.has_table_privilege(
                     role_name, 'identity.ha_user_bindings',
                     'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'
                   )
                OR pg_catalog.has_any_column_privilege(
                     role_name, 'identity.principals',
                     'INSERT,UPDATE,REFERENCES'
                   )
                OR pg_catalog.has_any_column_privilege(
                     role_name, 'identity.ha_user_bindings',
                     'INSERT,UPDATE,REFERENCES'
                   )
          ) OR EXISTS (
            SELECT 1
              FROM pg_catalog.unnest(ARRAY[
                'home_agent_binding_operator','home_agent_ingest',
                'home_agent_worker','home_agent_erasure','home_agent_rollout',
                'home_agent_backup'
              ]::text[]) AS runtime(role_name)
             WHERE pg_catalog.has_table_privilege(
                     role_name, 'identity.confirmation_artifacts',
                     'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'
                   )
                OR pg_catalog.has_any_column_privilege(
                     role_name, 'identity.confirmation_artifacts',
                     'INSERT,UPDATE,REFERENCES'
                   )
          ) OR pg_catalog.has_table_privilege(
            'home_agent_api', 'identity.confirmation_artifacts', 'INSERT'
          ) OR (
            SELECT coalesce(
                     pg_catalog.array_agg(attribute.attname::text
                       ORDER BY attribute.attnum)
                       FILTER (
                         WHERE pg_catalog.has_column_privilege(
                           'home_agent_api',
                           'identity.confirmation_artifacts',
                           attribute.attname, 'INSERT'
                         )
                       ),
                     ARRAY[]::text[]
                   )
              FROM pg_catalog.pg_attribute AS attribute
             WHERE attribute.attrelid =
                   'identity.confirmation_artifacts'::regclass
               AND attribute.attnum > 0
               AND NOT attribute.attisdropped
          ) IS DISTINCT FROM ARRAY[
            'artifact_id', 'principal_id', 'purpose', 'proposal_digest',
            'client_nonce_sha256', 'issued_at', 'expires_at', 'consumed_at'
          ]::text[] OR pg_catalog.has_table_privilege(
            'home_agent_api', 'identity.confirmation_artifacts',
            'UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'
          ) OR pg_catalog.has_any_column_privilege(
            'home_agent_api', 'identity.confirmation_artifacts',
            'UPDATE,REFERENCES'
          ) THEN
            RAISE EXCEPTION 'identity_erasure_e1_support_acl_invalid'
              USING ERRCODE = '55000';
          END IF;

          IF EXISTS (
            SELECT 1
              FROM (VALUES
                ('identity.principal_binding_requests'::regclass,
                 'validate_principal_binding_graph_principal_binding_requests'),
                ('identity.principal_binding_proposals'::regclass,
                 'validate_principal_binding_graph_principal_binding_proposals'),
                ('identity.ha_user_bindings'::regclass,
                 'validate_principal_binding_graph_ha_user_bindings'),
                ('identity.confirmation_artifacts'::regclass,
                 'validate_principal_binding_graph_confirmation_artifacts')
              ) AS expected(expected_table, expected_name)
             WHERE NOT EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_trigger AS graph_trigger
                WHERE graph_trigger.tgrelid = expected.expected_table
                  AND graph_trigger.tgname = expected.expected_name
                  AND graph_trigger.tgfoid = pg_catalog.to_regprocedure(
                        'identity.validate_principal_binding_graph_trigger()'
                      )
                  AND graph_trigger.tgenabled = 'O'
                  AND graph_trigger.tgdeferrable
                  AND graph_trigger.tginitdeferred
                  AND graph_trigger.tgconstraint <> 0
                  AND graph_trigger.tgtype = 29
                  AND graph_trigger.tgnargs = 0
                  AND NOT graph_trigger.tgisinternal
             )
          ) OR (
            SELECT pg_catalog.count(*)
              FROM pg_catalog.pg_trigger AS graph_trigger
             WHERE graph_trigger.tgfoid = pg_catalog.to_regprocedure(
                     'identity.validate_principal_binding_graph_trigger()'
                   )
               AND NOT graph_trigger.tgisinternal
          ) <> 4 OR NOT EXISTS (
            SELECT 1
              FROM pg_catalog.pg_proc AS graph_function
              JOIN pg_catalog.pg_roles AS graph_owner
                ON graph_owner.oid = graph_function.proowner
              JOIN pg_catalog.pg_language AS graph_language
                ON graph_language.oid = graph_function.prolang
             WHERE graph_function.oid = pg_catalog.to_regprocedure(
                   'identity.validate_principal_binding_graph(uuid,uuid)'
                 )
               AND graph_owner.rolname = 'home_agent_owner'
               AND graph_language.lanname = 'plpgsql'
               AND graph_function.prosecdef
               AND graph_function.proconfig =
                   ARRAY['search_path=pg_catalog']::text[]
               AND pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
                     graph_function.prosrc, 'UTF8')), 'hex') =
                   '8d969b350e9048d54de9341a9b2f08182572796ab11c9377b9e558214afede2f'
               AND graph_function.proacl IS NOT NULL
               AND NOT EXISTS (
                 SELECT 1 FROM pg_catalog.aclexplode(graph_function.proacl) acl
                  WHERE acl.grantee <> graph_function.proowner
                    AND acl.privilege_type = 'EXECUTE'
               )
          ) OR NOT EXISTS (
            SELECT 1
              FROM pg_catalog.pg_proc AS trigger_function
              JOIN pg_catalog.pg_roles AS trigger_owner
                ON trigger_owner.oid = trigger_function.proowner
              JOIN pg_catalog.pg_language AS trigger_language
                ON trigger_language.oid = trigger_function.prolang
             WHERE trigger_function.oid = pg_catalog.to_regprocedure(
                   'identity.validate_principal_binding_graph_trigger()'
                 )
               AND trigger_owner.rolname = 'home_agent_owner'
               AND trigger_language.lanname = 'plpgsql'
               AND trigger_function.prosecdef
               AND trigger_function.proconfig =
                   ARRAY['search_path=pg_catalog']::text[]
               AND pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
                     trigger_function.prosrc, 'UTF8')), 'hex') =
                   '4e71f303603b5f91349718b551a0857b417b560fd0852d944584ee023da96d23'
               AND trigger_function.proacl IS NOT NULL
               AND NOT EXISTS (
                 SELECT 1 FROM pg_catalog.aclexplode(trigger_function.proacl) acl
                  WHERE acl.grantee <> trigger_function.proowner
                    AND acl.privilege_type = 'EXECUTE'
               )
          ) THEN
            RAISE EXCEPTION 'identity_erasure_e1_binding_graph_invalid'
              USING ERRCODE = '55000';
          END IF;

          -- E1 establishes mandatory blocking and receipt invariants before
          -- the first identity-erasure operation.  It never guesses how to
          -- retrofit an operation that predates those invariants.
          IF EXISTS (SELECT 1 FROM {OPERATION})
             OR EXISTS (SELECT 1 FROM {IMPACT}) THEN
            RAISE EXCEPTION 'identity_erasure_e1_preexisting_operation'
              USING ERRCODE = '55000';
          END IF;
        END
        $admission$
        """
    )


def _validate_exact_0010_table_shapes() -> None:
    # Build PostgreSQL-canonical expected relations and compare catalogs. This
    # catches renamed, added, dropped, retyped, nullable, weakened, NOT VALID,
    # or otherwise rewritten 0010 columns/constraints before E1 adds FKs.
    non_owner_roles = ", ".join(RUNTIME_AND_OPERATOR_ROLES)
    op.execute(
        f"""
        DO $shape_namespace$
        BEGIN
          IF pg_catalog.to_regnamespace('e1_validation') IS NOT NULL THEN
            RAISE EXCEPTION 'identity_erasure_e1_validation_schema_occupied'
              USING ERRCODE = '55000';
          END IF;
        END
        $shape_namespace$;
        CREATE SCHEMA e1_validation AUTHORIZATION home_agent_owner;

        CREATE TABLE e1_validation.e1_expected_erasure_operation (
          operation_id uuid
            CONSTRAINT pk_reviewed_identity_migration_erasure_operations
            PRIMARY KEY,
          source_kind varchar(32) NOT NULL,
          erasure_request_id uuid,
          auto_expiry_schedule_id uuid,
          operation_commitment varchar(64) NOT NULL,
          recorded_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
          CONSTRAINT fk_identity_erasure_operation_request
            FOREIGN KEY (erasure_request_id)
            REFERENCES privacy.erasure_requests(erasure_request_id),
          CONSTRAINT fk_identity_erasure_operation_auto_expiry
            FOREIGN KEY (auto_expiry_schedule_id)
            REFERENCES privacy.auto_expiry_schedules(schedule_id),
          CONSTRAINT identity_erasure_operation_request
            UNIQUE (erasure_request_id),
          CONSTRAINT identity_erasure_operation_auto_expiry
            UNIQUE (auto_expiry_schedule_id),
          CONSTRAINT identity_erasure_operation_commitment
            UNIQUE (operation_commitment),
          CONSTRAINT ck_reviewed_identity_migration_erasure_operations_exact_source
            CHECK (
              (source_kind = 'erasure_request'
               AND erasure_request_id IS NOT NULL
               AND auto_expiry_schedule_id IS NULL)
              OR (source_kind = 'auto_expiry_schedule'
                  AND erasure_request_id IS NULL
                  AND auto_expiry_schedule_id IS NOT NULL)
            ),
          CONSTRAINT ck_reviewed_identity_migration_erasure_operations_uuidv7_ids
            CHECK (
              substring(operation_id::text from 15 for 1) = '7'
              AND substring(operation_id::text from 20 for 1)
                  IN ('8','9','a','b')
              AND (erasure_request_id IS NULL OR (
                substring(erasure_request_id::text from 15 for 1) = '7'
                AND substring(erasure_request_id::text from 20 for 1)
                    IN ('8','9','a','b')
              ))
              AND (auto_expiry_schedule_id IS NULL OR (
                substring(auto_expiry_schedule_id::text from 15 for 1) = '7'
                AND substring(auto_expiry_schedule_id::text from 20 for 1)
                    IN ('8','9','a','b')
              ))
            ),
          CONSTRAINT ck_reviewed_identity_migration_erasure_operations_commi_fe29
            CHECK (operation_commitment ~ '^[0-9a-f]{{64}}$')
        );

        CREATE TABLE e1_validation.e1_expected_erasure_impact (
          impact_id uuid
            CONSTRAINT pk_reviewed_identity_migration_erasure_impacts
            PRIMARY KEY,
          run_id uuid NOT NULL,
          operation_id uuid NOT NULL,
          impact_code varchar(32) NOT NULL,
          readiness_suspension varchar(16) NOT NULL,
          removed_leaf_commitment_count integer NOT NULL,
          unlinked_projection_count integer NOT NULL,
          impact_commitment varchar(64) NOT NULL,
          recorded_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
          CONSTRAINT fk_identity_migration_erasure_impact_run
            FOREIGN KEY (run_id)
            REFERENCES operations.reviewed_identity_migration_runs(run_id),
          CONSTRAINT fk_identity_migration_erasure_operation
            FOREIGN KEY (operation_id)
            REFERENCES {OPERATION}(operation_id),
          CONSTRAINT identity_erasure_impact_operation
            UNIQUE (run_id, operation_id),
          CONSTRAINT uq_reviewed_identity_migration_erasure_impacts_impact_c_6f7b
            UNIQUE (impact_commitment),
          CONSTRAINT ck_reviewed_identity_migration_erasure_impacts_uuidv7_ids
            CHECK (
              substring(impact_id::text from 15 for 1) = '7'
              AND substring(impact_id::text from 20 for 1) IN ('8','9','a','b')
              AND substring(operation_id::text from 15 for 1) = '7'
              AND substring(operation_id::text from 20 for 1)
                  IN ('8','9','a','b')
            ),
          CONSTRAINT ck_reviewed_identity_migration_erasure_impacts_impact_shape
            CHECK (
              impact_code IN (
                'leaf_commitments_removed','linkage_unavailable'
              ) AND readiness_suspension = 'required'
            ),
          CONSTRAINT ck_reviewed_identity_migration_erasure_impacts_impact_caps
            CHECK (
              removed_leaf_commitment_count BETWEEN 0 AND 30000
              AND unlinked_projection_count BETWEEN 0 AND 10000
            ),
          CONSTRAINT ck_reviewed_identity_migration_erasure_impacts_impact_c_4c72
            CHECK (
              (impact_code = 'leaf_commitments_removed'
               AND removed_leaf_commitment_count > 0
               AND unlinked_projection_count = 0)
              OR (impact_code = 'linkage_unavailable'
                  AND unlinked_projection_count > 0)
            ),
          CONSTRAINT ck_reviewed_identity_migration_erasure_impacts_commitment_shape
            CHECK (impact_commitment ~ '^[0-9a-f]{{64}}$')
        );

        ALTER TABLE e1_validation.e1_expected_erasure_operation
          ENABLE ROW LEVEL SECURITY;
        ALTER TABLE e1_validation.e1_expected_erasure_operation
          FORCE ROW LEVEL SECURITY;
        CREATE POLICY
          reviewed_identity_migration_erasure_operations_owner_select
          ON e1_validation.e1_expected_erasure_operation FOR SELECT
          TO home_agent_owner
          USING (session_user = 'home_agent_owner');
        CREATE POLICY
          reviewed_identity_migration_erasure_operations_owner_insert
          ON e1_validation.e1_expected_erasure_operation FOR INSERT
          TO home_agent_owner
          WITH CHECK (session_user = 'home_agent_owner');

        ALTER TABLE e1_validation.e1_expected_erasure_impact
          ENABLE ROW LEVEL SECURITY;
        ALTER TABLE e1_validation.e1_expected_erasure_impact
          FORCE ROW LEVEL SECURITY;
        CREATE POLICY
          reviewed_identity_migration_erasure_impacts_owner_select
          ON e1_validation.e1_expected_erasure_impact FOR SELECT
          TO home_agent_owner
          USING (session_user = 'home_agent_owner');
        CREATE POLICY
          reviewed_identity_migration_erasure_impacts_owner_insert
          ON e1_validation.e1_expected_erasure_impact FOR INSERT
          TO home_agent_owner
          WITH CHECK (session_user = 'home_agent_owner');
        CREATE POLICY
          identity_migration_erasure_impacts_manifest_kernel_select
          ON e1_validation.e1_expected_erasure_impact FOR SELECT
          TO home_agent_identity_kernel
          USING (
            session_user = 'home_agent_identity_migration'
            AND current_user = 'home_agent_identity_kernel'
            AND NOT pg_catalog.pg_has_role(
              session_user, 'home_agent_identity_kernel', 'SET'
            )
          );

        REVOKE ALL PRIVILEGES ON TABLE
          e1_validation.e1_expected_erasure_operation
          FROM PUBLIC, {non_owner_roles};
        GRANT SELECT, INSERT ON TABLE
          e1_validation.e1_expected_erasure_operation
          TO home_agent_owner;
        REVOKE ALL PRIVILEGES ON TABLE e1_validation.e1_expected_erasure_impact
          FROM PUBLIC, {non_owner_roles};
        GRANT SELECT, INSERT ON TABLE e1_validation.e1_expected_erasure_impact
          TO home_agent_owner;
        GRANT SELECT ON TABLE e1_validation.e1_expected_erasure_impact
          TO home_agent_identity_kernel;

        DO $shape_contract$
        DECLARE
          actual_columns text[];
          expected_columns text[];
          actual_constraints text[];
          expected_constraints text[];
          actual_definitions text[];
          expected_definitions text[];
          actual_defaults text[];
          expected_defaults text[];
          actual_policies text[];
          expected_policies text[];
          actual_acl text[];
          expected_acl text[];
          pair record;
        BEGIN
          FOR pair IN
            SELECT * FROM (VALUES
              ('{OPERATION}'::regclass,
                'e1_validation.e1_expected_erasure_operation'::regclass),
              ('{IMPACT}'::regclass,
                'e1_validation.e1_expected_erasure_impact'::regclass)
            ) AS comparison(actual_table, expected_table)
          LOOP
            SELECT pg_catalog.array_agg(
                     attribute.attname || '|' ||
                     pg_catalog.format_type(
                       attribute.atttypid, attribute.atttypmod
                     ) || '|' || attribute.attnotnull::text
                     ORDER BY attribute.attname
                   )
              INTO actual_columns
              FROM pg_catalog.pg_attribute AS attribute
             WHERE attribute.attrelid = pair.actual_table
               AND attribute.attnum > 0
               AND NOT attribute.attisdropped;
            SELECT pg_catalog.array_agg(
                     attribute.attname || '|' ||
                     pg_catalog.format_type(
                       attribute.atttypid, attribute.atttypmod
                     ) || '|' || attribute.attnotnull::text
                     ORDER BY attribute.attname
                   )
              INTO expected_columns
              FROM pg_catalog.pg_attribute AS attribute
             WHERE attribute.attrelid = pair.expected_table
               AND attribute.attnum > 0
               AND NOT attribute.attisdropped;

            SELECT pg_catalog.array_agg(
                     constraint_row.conname ORDER BY constraint_row.conname
                   ),
                   pg_catalog.array_agg(
                     pg_catalog.pg_get_constraintdef(
                       constraint_row.oid, true
                     ) ORDER BY pg_catalog.pg_get_constraintdef(
                       constraint_row.oid, true
                     )
                   )
              INTO actual_constraints, actual_definitions
              FROM pg_catalog.pg_constraint AS constraint_row
             WHERE constraint_row.conrelid = pair.actual_table;
            SELECT pg_catalog.array_agg(
                     constraint_row.conname ORDER BY constraint_row.conname
                   ),
                   pg_catalog.array_agg(
                     pg_catalog.pg_get_constraintdef(
                       constraint_row.oid, true
                     ) ORDER BY pg_catalog.pg_get_constraintdef(
                       constraint_row.oid, true
                     )
                   )
              INTO expected_constraints, expected_definitions
              FROM pg_catalog.pg_constraint AS constraint_row
             WHERE constraint_row.conrelid = pair.expected_table;

            SELECT pg_catalog.array_agg(
                     attribute.attname || '|' ||
                     pg_catalog.pg_get_expr(
                       default_row.adbin, default_row.adrelid
                     ) ORDER BY attribute.attname
                   )
              INTO actual_defaults
              FROM pg_catalog.pg_attrdef AS default_row
              JOIN pg_catalog.pg_attribute AS attribute
                ON attribute.attrelid = default_row.adrelid
               AND attribute.attnum = default_row.adnum
             WHERE default_row.adrelid = pair.actual_table;
            SELECT pg_catalog.array_agg(
                     attribute.attname || '|' ||
                     pg_catalog.pg_get_expr(
                       default_row.adbin, default_row.adrelid
                     ) ORDER BY attribute.attname
                   )
              INTO expected_defaults
              FROM pg_catalog.pg_attrdef AS default_row
              JOIN pg_catalog.pg_attribute AS attribute
                ON attribute.attrelid = default_row.adrelid
               AND attribute.attnum = default_row.adnum
             WHERE default_row.adrelid = pair.expected_table;

            SELECT pg_catalog.array_agg(
                     policy.polname || ':' || policy.polcmd::text || ':' ||
                     policy.polpermissive::text || ':' ||
                     policy.polroles::text || ':' ||
                     COALESCE(policy.polqual::text, '') || ':' ||
                     COALESCE(policy.polwithcheck::text, '')
                     ORDER BY policy.polname
                   )
              INTO actual_policies
              FROM pg_catalog.pg_policy AS policy
             WHERE policy.polrelid = pair.actual_table;
            SELECT pg_catalog.array_agg(
                     policy.polname || ':' || policy.polcmd::text || ':' ||
                     policy.polpermissive::text || ':' ||
                     policy.polroles::text || ':' ||
                     COALESCE(policy.polqual::text, '') || ':' ||
                     COALESCE(policy.polwithcheck::text, '')
                     ORDER BY policy.polname
                   )
              INTO expected_policies
              FROM pg_catalog.pg_policy AS policy
             WHERE policy.polrelid = pair.expected_table;

            SELECT pg_catalog.array_agg(
                     COALESCE(grantee.rolname, 'PUBLIC') || ':' ||
                     grantor.rolname || ':' || acl.privilege_type || ':' ||
                     acl.is_grantable::text
                     ORDER BY COALESCE(grantee.rolname, 'PUBLIC'),
                              grantor.rolname, acl.privilege_type,
                              acl.is_grantable
                   )
              INTO actual_acl
              FROM pg_catalog.pg_class AS table_row
              CROSS JOIN LATERAL pg_catalog.aclexplode(table_row.relacl) AS acl
              LEFT JOIN pg_catalog.pg_roles AS grantee
                ON grantee.oid = acl.grantee
              JOIN pg_catalog.pg_roles AS grantor
                ON grantor.oid = acl.grantor
             WHERE table_row.oid = pair.actual_table;
            SELECT pg_catalog.array_agg(
                     COALESCE(grantee.rolname, 'PUBLIC') || ':' ||
                     grantor.rolname || ':' || acl.privilege_type || ':' ||
                     acl.is_grantable::text
                     ORDER BY COALESCE(grantee.rolname, 'PUBLIC'),
                              grantor.rolname, acl.privilege_type,
                              acl.is_grantable
                   )
              INTO expected_acl
              FROM pg_catalog.pg_class AS table_row
              CROSS JOIN LATERAL pg_catalog.aclexplode(table_row.relacl) AS acl
              LEFT JOIN pg_catalog.pg_roles AS grantee
                ON grantee.oid = acl.grantee
              JOIN pg_catalog.pg_roles AS grantor
                ON grantor.oid = acl.grantor
             WHERE table_row.oid = pair.expected_table;

            IF actual_columns IS DISTINCT FROM expected_columns
               OR actual_constraints IS DISTINCT FROM expected_constraints
               OR actual_definitions IS DISTINCT FROM expected_definitions
               OR actual_defaults IS DISTINCT FROM expected_defaults
               OR actual_policies IS DISTINCT FROM expected_policies
               OR actual_acl IS DISTINCT FROM expected_acl THEN
              RAISE EXCEPTION 'identity_erasure_e1_predecessor_shape_invalid'
                USING ERRCODE = '55000';
            END IF;
          END LOOP;
        END
        $shape_contract$;

        DROP SCHEMA e1_validation CASCADE;
        """
    )


def _validate_exact_replay_guard() -> None:
    op.execute(
        f"""
        DO $replay_guard_contract$
        DECLARE
          guard_oid oid;
          kernel_oid oid;
        BEGIN
          guard_oid := pg_catalog.to_regprocedure(
            'operations.bump_reviewed_identity_migration_replay_guard()'
          );
          SELECT oid INTO STRICT kernel_oid
            FROM pg_catalog.pg_roles
           WHERE rolname = 'home_agent_identity_kernel';

          IF guard_oid IS NULL OR NOT EXISTS (
            SELECT 1
              FROM pg_catalog.pg_proc AS guard_function
              JOIN pg_catalog.pg_namespace AS function_namespace
                ON function_namespace.oid = guard_function.pronamespace
              JOIN pg_catalog.pg_language AS function_language
                ON function_language.oid = guard_function.prolang
             WHERE guard_function.oid = guard_oid
               AND function_namespace.nspname = 'operations'
               AND guard_function.proname =
                   'bump_reviewed_identity_migration_replay_guard'
               AND guard_function.pronargs = 0
               AND guard_function.prorettype = 'pg_catalog.trigger'::regtype
               AND function_language.lanname = 'plpgsql'
               AND guard_function.proowner = kernel_oid
               AND guard_function.prosecdef
               AND guard_function.proconfig =
                   ARRAY['search_path=pg_catalog, pg_temp']::text[]
               AND guard_function.proacl IS NOT NULL
               AND NOT EXISTS (
                 SELECT 1
                   FROM pg_catalog.aclexplode(guard_function.proacl) AS acl
                  WHERE acl.privilege_type = 'EXECUTE'
               )
               AND pg_catalog.encode(
                     pg_catalog.sha256(
                       pg_catalog.convert_to(guard_function.prosrc, 'UTF8')
                     ),
                     'hex'
                   ) =
                   '627bb84f83baa6183144de5d94ddcc4f0da56dc02e64c544b4b679b5ed3c316b'
          ) OR (
            SELECT pg_catalog.count(*)
              FROM pg_catalog.pg_proc AS candidate
              JOIN pg_catalog.pg_namespace AS candidate_namespace
                ON candidate_namespace.oid = candidate.pronamespace
             WHERE candidate_namespace.nspname = 'operations'
               AND candidate.proname =
                   'bump_reviewed_identity_migration_replay_guard'
          ) <> 1 OR EXISTS (
            SELECT 1
              FROM pg_catalog.pg_trigger AS operation_trigger
             WHERE operation_trigger.tgrelid = '{OPERATION}'::regclass
               AND NOT operation_trigger.tgisinternal
          ) OR (
            SELECT pg_catalog.count(*)
              FROM pg_catalog.pg_trigger AS impact_trigger
             WHERE impact_trigger.tgrelid = '{IMPACT}'::regclass
               AND NOT impact_trigger.tgisinternal
          ) <> 1 OR NOT EXISTS (
            SELECT 1
              FROM pg_catalog.pg_trigger AS guard_trigger
             WHERE guard_trigger.tgrelid = '{IMPACT}'::regclass
               AND guard_trigger.tgname =
                   'reviewed_identity_migration_erasure_replay_guard'
               AND guard_trigger.tgfoid = guard_oid
               AND guard_trigger.tgenabled = 'O'
               AND NOT guard_trigger.tgisinternal
               AND guard_trigger.tgtype = 7
               AND guard_trigger.tgnargs = 0
          ) THEN
            RAISE EXCEPTION 'identity_erasure_e1_replay_guard_invalid'
              USING ERRCODE = '55000';
          END IF;
        END
        $replay_guard_contract$
        """
    )


def _quarantine_kernel_role() -> None:
    schemas = (
        "public, ingest, identity, knowledge, engagement, privacy, operations, media"
    )
    op.execute(
        f"REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA {schemas} "
        f"FROM {KERNEL_ROLE}"
    )
    op.execute(
        f"""
        DO $column_acl$
        DECLARE
          target_table record;
        BEGIN
          FOR target_table IN
            SELECT table_namespace.nspname,
                   candidate_table.relname,
                   pg_catalog.string_agg(
                     pg_catalog.quote_ident(attribute.attname), ', '
                     ORDER BY attribute.attnum
                   ) AS column_list
              FROM pg_catalog.pg_class AS candidate_table
              JOIN pg_catalog.pg_namespace AS table_namespace
                ON table_namespace.oid = candidate_table.relnamespace
              JOIN pg_catalog.pg_attribute AS attribute
                ON attribute.attrelid = candidate_table.oid
               AND attribute.attnum > 0
               AND NOT attribute.attisdropped
             WHERE table_namespace.nspname IN (
               'public','ingest','identity','knowledge','engagement',
               'privacy','operations','media'
             )
               AND candidate_table.relkind IN ('r','p','v','m','f')
             GROUP BY table_namespace.nspname, candidate_table.relname
          LOOP
            EXECUTE pg_catalog.format(
              'REVOKE SELECT (%1$s), INSERT (%1$s), UPDATE (%1$s), '
              'REFERENCES (%1$s) ON TABLE %2$I.%3$I FROM {KERNEL_ROLE}',
              target_table.column_list,
              target_table.nspname,
              target_table.relname
            );
          END LOOP;
        END
        $column_acl$
        """
    )
    op.execute(
        f"REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA {schemas} "
        f"FROM {KERNEL_ROLE}"
    )
    op.execute(
        "REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA "
        f"{schemas} "
        f"FROM {KERNEL_ROLE}"
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
               'privacy','operations','media'
             )
               AND candidate_type.typisdefined
               AND candidate_type.typrelid = 0
               AND candidate_type.typelem = 0
          LOOP
            EXECUTE pg_catalog.format(
              'REVOKE USAGE ON TYPE %I.%I FROM {KERNEL_ROLE}',
              type_entry.nspname,
              type_entry.typname
            );
          END LOOP;
        END
        $type_acl$
        """
    )
    op.execute(
        f"REVOKE USAGE, CREATE ON SCHEMA {schemas} FROM {KERNEL_ROLE}"
    )
    for object_kind in ("TABLES", "SEQUENCES", "FUNCTIONS", "TYPES"):
        op.execute(
            "ALTER DEFAULT PRIVILEGES FOR ROLE home_agent_owner IN SCHEMA "
            f"{schemas} REVOKE ALL PRIVILEGES ON {object_kind} "
            f"FROM {KERNEL_ROLE}"
        )
    op.execute(
        f"""
        DO $database_acl$
        BEGIN
          EXECUTE pg_catalog.format(
            'REVOKE ALL PRIVILEGES ON DATABASE %I FROM {KERNEL_ROLE}',
            pg_catalog.current_database()
          );
        END
        $database_acl$
        """
    )


def _install_support_constraints() -> None:
    op.execute(
        "ALTER TABLE privacy.erasure_requests "
        f"ADD COLUMN {REQUEST_SCOPE_COLUMN} varchar(64)"
    )
    op.execute(
        "ALTER TABLE privacy.erasure_requests "
        f"ADD CONSTRAINT {REQUEST_SCOPE_CHECK} CHECK ("
        "identity_person_scope_commitment IS NULL OR ("
        "identity_person_scope_commitment ~ '^[0-9a-f]{64}$' AND "
        "scope = '{\"kind\":\"identity_person\"}'::jsonb))"
    )
    op.execute(
        "ALTER TABLE identity.ha_user_bindings "
        f"ADD COLUMN {BINDING_VALID_COLUMN} timestamptz "
        "GENERATED ALWAYS AS ("
        "COALESCE(revoked_at, 'infinity'::timestamptz)) STORED"
    )
    for table, name, definition in SUPPORT_CONSTRAINTS:
        op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {name} {definition}")


def _install_foundation_tables() -> None:
    op.execute(
        f"""
        CREATE TABLE {PERSON_SCOPE} (
          scope_id uuid PRIMARY KEY,
          erasure_request_id uuid NOT NULL,
          person_id uuid NOT NULL,
          authorizing_principal_id uuid NOT NULL,
          authority_kind varchar(32) NOT NULL,
          ha_binding_id uuid NOT NULL,
          binding_confirmed_at timestamptz NOT NULL,
          binding_valid_until timestamptz NOT NULL,
          confirmation_artifact_id uuid NOT NULL,
          confirmation_purpose varchar(128) NOT NULL,
          confirmation_consumed_at timestamptz NOT NULL,
          confirmation_expires_at timestamptz NOT NULL,
          scope_kind varchar(32) NOT NULL,
          scope_commitment varchar(64) NOT NULL,
          recorded_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
          CONSTRAINT fk_person_erasure_scope_request_authority
            FOREIGN KEY (
              erasure_request_id,
              authorizing_principal_id,
              scope_commitment
            )
            REFERENCES privacy.erasure_requests(
              erasure_request_id,
              principal_id,
              identity_person_scope_commitment
            ),
          CONSTRAINT fk_person_erasure_scope_principal_person
            FOREIGN KEY (
              authorizing_principal_id, person_id, authority_kind
            ) REFERENCES identity.principals(principal_id, person_id, kind),
          CONSTRAINT fk_person_erasure_scope_ha_binding
            FOREIGN KEY (
              ha_binding_id,
              authorizing_principal_id,
              person_id,
              binding_confirmed_at,
              binding_valid_until
            ) REFERENCES identity.ha_user_bindings(
              binding_id,
              principal_id,
              person_id,
              confirmed_at,
              identity_erasure_binding_valid_until
            ) ON UPDATE CASCADE,
          CONSTRAINT fk_person_erasure_scope_confirmation
            FOREIGN KEY (
              confirmation_artifact_id,
              authorizing_principal_id,
              confirmation_purpose,
              scope_commitment,
              confirmation_consumed_at,
              confirmation_expires_at
            ) REFERENCES identity.confirmation_artifacts(
              artifact_id,
              principal_id,
              purpose,
              proposal_digest,
              consumed_at,
              expires_at
            ),
          CONSTRAINT identity_erasure_scope_request_person
            UNIQUE (erasure_request_id, person_id),
          CONSTRAINT identity_erasure_scope_request
            UNIQUE (erasure_request_id),
          CONSTRAINT identity_erasure_scope_confirmation
            UNIQUE (confirmation_artifact_id),
          CONSTRAINT identity_erasure_scope_commitment
            UNIQUE (scope_commitment),
          CONSTRAINT ck_person_erasure_scope_kind
            CHECK (
              scope_kind = 'identity_person'
              AND authority_kind = 'ha_user'
              AND confirmation_purpose = 'identity_person_erasure'
            ),
          CONSTRAINT ck_person_erasure_scope_uuidv7
            CHECK (
              substring(scope_id::text from 15 for 1) = '7'
              AND substring(scope_id::text from 20 for 1) IN ('8','9','a','b')
              AND substring(erasure_request_id::text from 15 for 1) = '7'
              AND substring(erasure_request_id::text from 20 for 1)
                  IN ('8','9','a','b')
              AND substring(confirmation_artifact_id::text from 15 for 1) = '7'
              AND substring(confirmation_artifact_id::text from 20 for 1)
                  IN ('8','9','a','b')
            ),
          CONSTRAINT ck_person_erasure_scope_commitment
            CHECK (scope_commitment ~ '^[0-9a-f]{{64}}$'),
          CONSTRAINT ck_person_erasure_scope_confirmation_time
            CHECK (
              confirmation_consumed_at <= recorded_at
              AND recorded_at <= confirmation_expires_at
              AND confirmation_consumed_at >= binding_confirmed_at
              AND recorded_at <= binding_valid_until
            )
        );

        CREATE TABLE {SUBJECT_BLOCK} (
          block_id uuid PRIMARY KEY,
          operation_id uuid NOT NULL,
          person_id uuid NOT NULL,
          source_kind varchar(32) NOT NULL,
          erasure_request_id uuid NOT NULL,
          block_code varchar(32) NOT NULL,
          block_commitment varchar(64) NOT NULL,
          created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
          CONSTRAINT fk_subject_retrieval_block_operation
            FOREIGN KEY (operation_id)
            REFERENCES {OPERATION}(operation_id),
          CONSTRAINT fk_subject_retrieval_block_request_operation
            FOREIGN KEY (operation_id, erasure_request_id)
            REFERENCES {OPERATION}(operation_id, erasure_request_id),
          CONSTRAINT fk_subject_retrieval_block_request_person
            FOREIGN KEY (erasure_request_id, person_id)
            REFERENCES {PERSON_SCOPE}(erasure_request_id, person_id),
          CONSTRAINT ck_subject_retrieval_block_exact_source
            CHECK (
              source_kind = 'erasure_request'
            ),
          CONSTRAINT identity_erasure_block_operation
            UNIQUE (operation_id),
          CONSTRAINT identity_erasure_block_commitment
            UNIQUE (block_commitment),
          CONSTRAINT ck_subject_retrieval_block_code
            CHECK (block_code = 'identity_person_erasure'),
          CONSTRAINT ck_subject_retrieval_block_uuidv7
            CHECK (
              substring(block_id::text from 15 for 1) = '7'
              AND substring(block_id::text from 20 for 1) IN ('8','9','a','b')
              AND substring(operation_id::text from 15 for 1) = '7'
              AND substring(operation_id::text from 20 for 1) IN ('8','9','a','b')
            ),
          CONSTRAINT ck_subject_retrieval_block_commitment
            CHECK (block_commitment ~ '^[0-9a-f]{{64}}$')
        );

        CREATE TABLE {ERASURE_RECEIPT} (
          receipt_id uuid PRIMARY KEY,
          impact_id uuid NOT NULL,
          run_id uuid NOT NULL,
          operation_id uuid NOT NULL,
          impact_code varchar(32) NOT NULL,
          removed_leaf_commitment_count integer NOT NULL,
          unlinked_projection_count integer NOT NULL,
          result_code varchar(32) NOT NULL,
          completion_txid bigint NOT NULL,
          receipt_commitment varchar(64) NOT NULL,
          completed_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
          CONSTRAINT fk_identity_erasure_receipt_exact_impact
            FOREIGN KEY (
              impact_id,
              run_id,
              operation_id,
              impact_code,
              removed_leaf_commitment_count,
              unlinked_projection_count
            ) REFERENCES {IMPACT}(
              impact_id,
              run_id,
              operation_id,
              impact_code,
              removed_leaf_commitment_count,
              unlinked_projection_count
            ),
          CONSTRAINT fk_identity_erasure_receipt_retrieval_block
            FOREIGN KEY (operation_id)
            REFERENCES {SUBJECT_BLOCK}(operation_id),
          CONSTRAINT identity_erasure_receipt_operation_run
            UNIQUE (run_id, operation_id),
          CONSTRAINT identity_erasure_receipt_impact
            UNIQUE (impact_id),
          CONSTRAINT identity_erasure_receipt_commitment
            UNIQUE (receipt_commitment),
          CONSTRAINT ck_identity_erasure_receipt_result
            CHECK (
              (impact_code = 'leaf_commitments_removed'
               AND result_code = 'migration_run_erased'
               AND removed_leaf_commitment_count > 0
               AND unlinked_projection_count = 0)
              OR (impact_code = 'linkage_unavailable'
                  AND result_code = 'linkage_unavailable'
                  AND removed_leaf_commitment_count = 0
                  AND unlinked_projection_count > 0)
            ),
          CONSTRAINT ck_identity_erasure_receipt_counts
            CHECK (
              removed_leaf_commitment_count BETWEEN 0 AND 30000
              AND unlinked_projection_count BETWEEN 0 AND 10000
            ),
          CONSTRAINT ck_identity_erasure_receipt_txid
            CHECK (completion_txid > 0),
          CONSTRAINT ck_identity_erasure_receipt_uuidv7
            CHECK (
              substring(receipt_id::text from 15 for 1) = '7'
              AND substring(receipt_id::text from 20 for 1) IN ('8','9','a','b')
              AND substring(impact_id::text from 15 for 1) = '7'
              AND substring(impact_id::text from 20 for 1) IN ('8','9','a','b')
              AND substring(operation_id::text from 15 for 1) = '7'
              AND substring(operation_id::text from 20 for 1) IN ('8','9','a','b')
            ),
          CONSTRAINT ck_identity_erasure_receipt_commitment
            CHECK (receipt_commitment ~ '^[0-9a-f]{{64}}$')
        );
        """
    )

    comments = {
        PERSON_SCOPE: (
            "Confirmed, self-authorized whole-person identity-erasure scope; "
            "descriptor erasure requests intentionally have no row here."
        ),
        SUBJECT_BLOCK: (
            "Dormant mandatory person-level retrieval-block marker linked to "
            "one exact confirmed manual erasure operation source."
        ),
        ERASURE_RECEIPT: (
            "Idempotent content-free completion receipt for one exact identity-"
            "migration erasure impact and its required retrieval block."
        ),
    }
    for table, comment in comments.items():
        op.execute(f"COMMENT ON TABLE {table} IS '{comment}'")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        base = table.split(".", 1)[1]
        op.execute(
            f"CREATE POLICY {base}_owner_select ON {table} FOR SELECT "
            "TO home_agent_owner USING (session_user = 'home_agent_owner')"
        )
        op.execute(
            f"CREATE POLICY {base}_owner_insert ON {table} FOR INSERT "
            "TO home_agent_owner WITH CHECK (session_user = 'home_agent_owner')"
        )
        roles = ", ".join(RUNTIME_AND_OPERATOR_ROLES)
        op.execute(
            f"REVOKE ALL PRIVILEGES ON TABLE {table} FROM PUBLIC, {roles}"
        )
        op.execute(f"GRANT SELECT, INSERT ON TABLE {table} TO home_agent_owner")


def _narrow_erasure_request_acl() -> None:
    op.execute(
        """
        DO $erasure_request_acl_reset$
        DECLARE
          all_columns text;
          target_role text;
          grantee_sql text;
        BEGIN
          SELECT pg_catalog.string_agg(
                   pg_catalog.quote_ident(attribute.attname), ', '
                   ORDER BY attribute.attnum
                 )
            INTO STRICT all_columns
            FROM pg_catalog.pg_attribute AS attribute
           WHERE attribute.attrelid = 'privacy.erasure_requests'::regclass
             AND attribute.attnum > 0
             AND NOT attribute.attisdropped;
          FOR target_role IN
            SELECT role_row.rolname FROM pg_catalog.pg_roles AS role_row
             WHERE role_row.rolname <> 'home_agent_owner'
            UNION ALL SELECT 'PUBLIC'
          LOOP
            grantee_sql := CASE WHEN target_role = 'PUBLIC'
              THEN 'PUBLIC' ELSE pg_catalog.quote_ident(target_role) END;
            EXECUTE pg_catalog.format(
              'REVOKE ALL PRIVILEGES ON TABLE privacy.erasure_requests FROM %s',
              grantee_sql
            );
            EXECUTE pg_catalog.format(
              'REVOKE SELECT (%1$s), INSERT (%1$s), UPDATE (%1$s), '
              'REFERENCES (%1$s) ON TABLE privacy.erasure_requests FROM %2$s',
              all_columns, grantee_sql
            );
          END LOOP;
        END
        $erasure_request_acl_reset$
        """
    )
    op.execute(
        "GRANT SELECT (erasure_request_id, principal_id, scope, state, "
        "policy_digest, created_at, completed_at) ON TABLE "
        "privacy.erasure_requests TO home_agent_api, home_agent_worker, "
        "home_agent_erasure"
    )
    op.execute(
        "GRANT INSERT (erasure_request_id, principal_id, scope, state, "
        "policy_digest) ON TABLE privacy.erasure_requests TO home_agent_api"
    )
    op.execute(
        "GRANT INSERT (erasure_request_id, principal_id, scope, state, "
        "policy_digest, completed_at) ON TABLE privacy.erasure_requests "
        "TO home_agent_erasure"
    )
    op.execute(
        "GRANT UPDATE (state, completed_at) ON TABLE privacy.erasure_requests "
        "TO home_agent_api, home_agent_worker, home_agent_erasure"
    )


def _narrow_identity_support_acl() -> None:
    # Table-level SELECT automatically widens to columns added later. Reset
    # both table and attribute ACLs before restoring the predecessor's exact
    # HA-binding projection, keeping E1's validity linkage owner-only.
    op.execute(
        """
        DO $identity_erasure_support_acl$
        DECLARE
          all_principal_columns text;
          all_binding_columns text;
          all_confirmation_columns text;
          target_role text;
          grantee_sql text;
        BEGIN
          SELECT pg_catalog.string_agg(
                   pg_catalog.quote_ident(attribute.attname), ', '
                   ORDER BY attribute.attnum
                 )
            INTO STRICT all_principal_columns
            FROM pg_catalog.pg_attribute AS attribute
           WHERE attribute.attrelid = 'identity.principals'::regclass
             AND attribute.attnum > 0
             AND NOT attribute.attisdropped;

          SELECT pg_catalog.string_agg(
                   pg_catalog.quote_ident(attribute.attname), ', '
                   ORDER BY attribute.attnum
                 )
            INTO STRICT all_binding_columns
            FROM pg_catalog.pg_attribute AS attribute
           WHERE attribute.attrelid = 'identity.ha_user_bindings'::regclass
             AND attribute.attnum > 0
             AND NOT attribute.attisdropped;

          SELECT pg_catalog.string_agg(
                   pg_catalog.quote_ident(attribute.attname), ', '
                   ORDER BY attribute.attnum
                 )
            INTO STRICT all_confirmation_columns
            FROM pg_catalog.pg_attribute AS attribute
           WHERE attribute.attrelid =
                 'identity.confirmation_artifacts'::regclass
             AND attribute.attnum > 0
             AND NOT attribute.attisdropped;

          FOR target_role IN
            SELECT role_row.rolname FROM pg_catalog.pg_roles AS role_row
             WHERE role_row.rolname <> 'home_agent_owner'
            UNION ALL SELECT 'PUBLIC'
          LOOP
            grantee_sql := CASE WHEN target_role = 'PUBLIC'
              THEN 'PUBLIC' ELSE pg_catalog.quote_ident(target_role) END;
            EXECUTE pg_catalog.format(
              'REVOKE ALL PRIVILEGES ON TABLE identity.principals, '
              'identity.ha_user_bindings, identity.confirmation_artifacts '
              'FROM %s',
              grantee_sql
            );
            EXECUTE pg_catalog.format(
              'REVOKE SELECT (%1$s), INSERT (%1$s), UPDATE (%1$s), '
              'REFERENCES (%1$s) ON TABLE identity.principals FROM %2$s',
              all_principal_columns, grantee_sql
            );
            EXECUTE pg_catalog.format(
              'REVOKE SELECT (%1$s), INSERT (%1$s), UPDATE (%1$s), '
              'REFERENCES (%1$s) ON TABLE identity.ha_user_bindings FROM %2$s',
              all_binding_columns, grantee_sql
            );
            EXECUTE pg_catalog.format(
              'REVOKE SELECT (%1$s), INSERT (%1$s), UPDATE (%1$s), '
              'REFERENCES (%1$s) '
              'ON TABLE identity.confirmation_artifacts FROM %2$s',
              all_confirmation_columns, grantee_sql
            );
          END LOOP;
        END
        $identity_erasure_support_acl$
        """
    )
    op.execute(
        "GRANT SELECT ON TABLE identity.principals TO home_agent_api, "
        "home_agent_binding_operator, home_agent_erasure"
    )
    op.execute(
        "GRANT SELECT (binding_id, proposal_id, ha_user_id, principal_id, "
        "person_id, confirmed_by_principal_id, confirmed_at, revoked_at, "
        "source_artifact_id) ON TABLE identity.ha_user_bindings TO "
        "home_agent_api, home_agent_binding_operator, home_agent_ingest, "
        "home_agent_erasure"
    )
    op.execute(
        "GRANT SELECT ON TABLE identity.confirmation_artifacts TO home_agent_api"
    )
    op.execute(
        "GRANT INSERT (artifact_id, principal_id, purpose, proposal_digest, "
        "client_nonce_sha256, issued_at, expires_at, consumed_at) ON TABLE "
        "identity.confirmation_artifacts TO home_agent_api"
    )


def _assert_installed_contract() -> None:
    target_names = ",".join(f"'{table}'" for table in FOUNDATION_TABLES)
    role_names = ",".join(f"'{role}'" for role in RUNTIME_AND_OPERATOR_ROLES)
    op.execute(
        f"""
        DO $installed_contract$
        DECLARE
          owner_oid oid;
          kernel_oid oid;
        BEGIN
          SELECT oid INTO STRICT owner_oid FROM pg_catalog.pg_roles
           WHERE rolname = 'home_agent_owner';
          SELECT oid INTO STRICT kernel_oid FROM pg_catalog.pg_roles
           WHERE rolname = '{KERNEL_ROLE}';
          IF EXISTS (
            SELECT 1
              FROM pg_catalog.unnest(
                     ARRAY[{target_names}]::text[]
                   ) AS target(target_name)
              JOIN pg_catalog.pg_class AS table_row
                ON table_row.oid = target_name::regclass
             WHERE table_row.relkind <> 'r'
                OR table_row.relpersistence <> 'p'
                OR NOT table_row.relrowsecurity
                OR NOT table_row.relforcerowsecurity
                OR pg_catalog.pg_get_userbyid(table_row.relowner)
                   <> 'home_agent_owner'
          ) OR EXISTS (
            SELECT 1
              FROM pg_catalog.unnest(
                     ARRAY[{target_names}]::text[]
                   ) AS target(target_name)
              JOIN pg_catalog.pg_class AS target_table
                ON target_table.oid = target_name::regclass
             CROSS JOIN LATERAL pg_catalog.aclexplode(
               coalesce(
                 target_table.relacl,
                 pg_catalog.acldefault('r', target_table.relowner)
               )
             ) AS table_acl
             WHERE table_acl.grantee <> owner_oid
          ) OR EXISTS (
            SELECT 1
              FROM pg_catalog.unnest(
                     ARRAY[{target_names}]::text[]
                   ) AS target(target_name)
              JOIN pg_catalog.pg_attribute AS attribute
                ON attribute.attrelid = target_name::regclass
               AND attribute.attnum > 0
               AND NOT attribute.attisdropped
             CROSS JOIN LATERAL pg_catalog.aclexplode(attribute.attacl)
               AS column_acl
             WHERE column_acl.grantee <> owner_oid
          ) OR EXISTS (
            SELECT 1
              FROM pg_catalog.unnest(
                     ARRAY[{target_names}]::text[]
                   ) AS target(target_name)
             WHERE NOT pg_catalog.has_table_privilege(
               'home_agent_owner', target_name, 'SELECT,INSERT'
             )
          ) OR pg_catalog.has_column_privilege(
            'home_agent_api', 'privacy.erasure_requests',
            'identity_person_scope_commitment', 'SELECT'
          ) OR pg_catalog.has_column_privilege(
            'home_agent_api', 'privacy.erasure_requests',
            'identity_person_scope_commitment', 'INSERT'
          ) OR pg_catalog.has_column_privilege(
            'home_agent_api', 'privacy.erasure_requests',
            'identity_person_scope_commitment', 'UPDATE'
          ) OR EXISTS (
            SELECT 1
              FROM pg_catalog.unnest(
                     ARRAY[{role_names}]::text[]
                   ) AS role(role_name)
             WHERE pg_catalog.has_column_privilege(
               role_name, 'identity.ha_user_bindings',
               '{BINDING_VALID_COLUMN}', 'SELECT,INSERT,UPDATE,REFERENCES'
             )
          ) OR EXISTS (
            SELECT 1
              FROM pg_catalog.unnest(
                     ARRAY[{role_names}]::text[]
                   ) AS role(role_name)
             WHERE role_name <> 'home_agent_api'
               AND (
                 pg_catalog.has_table_privilege(
                   role_name, 'identity.confirmation_artifacts',
                   'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'
                 )
                 OR pg_catalog.has_any_column_privilege(
                   role_name, 'identity.confirmation_artifacts',
                   'INSERT,UPDATE,REFERENCES'
                 )
               )
          ) OR EXISTS (
            SELECT 1
              FROM (VALUES
                ('privacy.erasure_requests'::regclass),
                ('identity.ha_user_bindings'::regclass)
              ) AS protected_table(table_oid)
              JOIN pg_catalog.pg_class AS table_row
                ON table_row.oid = protected_table.table_oid
             CROSS JOIN LATERAL pg_catalog.aclexplode(
               coalesce(
                 table_row.relacl,
                 pg_catalog.acldefault('r', table_row.relowner)
               )
             ) AS table_acl
             WHERE table_acl.grantee <> owner_oid
          ) OR EXISTS (
            SELECT 1
              FROM (VALUES
                ('privacy.erasure_requests'::regclass,
                 '{REQUEST_SCOPE_COLUMN}'),
                ('identity.ha_user_bindings'::regclass,
                 '{BINDING_VALID_COLUMN}')
              ) AS protected_column(table_oid, column_name)
              JOIN pg_catalog.pg_attribute AS attribute
                ON attribute.attrelid = protected_column.table_oid
               AND attribute.attname = protected_column.column_name
               AND attribute.attnum > 0
               AND NOT attribute.attisdropped
             CROSS JOIN LATERAL pg_catalog.aclexplode(attribute.attacl)
               AS column_acl
             WHERE column_acl.grantee <> owner_oid
          ) OR EXISTS (
            SELECT 1
              FROM pg_catalog.pg_database AS database_row
             CROSS JOIN LATERAL pg_catalog.aclexplode(database_row.datacl) acl
             WHERE database_row.datname = pg_catalog.current_database()
               AND acl.grantee = kernel_oid
          ) OR EXISTS (
            SELECT 1
              FROM pg_catalog.pg_namespace AS namespace_row
             CROSS JOIN LATERAL pg_catalog.aclexplode(namespace_row.nspacl) acl
             WHERE namespace_row.nspname IN (
               'public','ingest','identity','knowledge','engagement',
               'privacy','operations','media'
             )
               AND acl.grantee = kernel_oid
          ) OR EXISTS (
            SELECT 1
              FROM pg_catalog.pg_class AS class_row
              JOIN pg_catalog.pg_namespace AS namespace_row
                ON namespace_row.oid = class_row.relnamespace
             CROSS JOIN LATERAL pg_catalog.aclexplode(class_row.relacl) acl
             WHERE namespace_row.nspname IN (
               'public','ingest','identity','knowledge','engagement',
               'privacy','operations','media'
             )
               AND acl.grantee = kernel_oid
          ) OR EXISTS (
            SELECT 1
              FROM pg_catalog.pg_attribute AS attribute
              JOIN pg_catalog.pg_class AS class_row
                ON class_row.oid = attribute.attrelid
              JOIN pg_catalog.pg_namespace AS namespace_row
                ON namespace_row.oid = class_row.relnamespace
             CROSS JOIN LATERAL pg_catalog.aclexplode(attribute.attacl) acl
             WHERE namespace_row.nspname IN (
               'public','ingest','identity','knowledge','engagement',
               'privacy','operations','media'
             )
               AND acl.grantee = kernel_oid
          ) OR EXISTS (
            SELECT 1
              FROM pg_catalog.pg_proc AS function_row
              JOIN pg_catalog.pg_namespace AS namespace_row
                ON namespace_row.oid = function_row.pronamespace
             CROSS JOIN LATERAL pg_catalog.aclexplode(function_row.proacl) acl
             WHERE namespace_row.nspname IN (
               'public','ingest','identity','knowledge','engagement',
               'privacy','operations','media'
             )
               AND acl.grantee = kernel_oid
          ) OR EXISTS (
            SELECT 1
              FROM pg_catalog.pg_type AS type_row
              JOIN pg_catalog.pg_namespace AS namespace_row
                ON namespace_row.oid = type_row.typnamespace
             CROSS JOIN LATERAL pg_catalog.aclexplode(type_row.typacl) acl
             WHERE namespace_row.nspname IN (
               'public','ingest','identity','knowledge','engagement',
               'privacy','operations','media'
             )
               AND acl.grantee = kernel_oid
          ) OR EXISTS (
            SELECT 1
              FROM pg_catalog.pg_default_acl AS default_acl
             CROSS JOIN LATERAL pg_catalog.aclexplode(default_acl.defaclacl) acl
             WHERE default_acl.defaclrole = owner_oid
               AND acl.grantee = kernel_oid
          ) THEN
            RAISE EXCEPTION 'identity_erasure_e1_contract_invalid'
              USING ERRCODE = '42501';
          END IF;
        END
        $installed_contract$
        """
    )


def upgrade() -> None:
    _require_dormant_kernel_role()
    _admit_exact_predecessor()
    _validate_exact_0010_table_shapes()
    _validate_exact_replay_guard()
    _quarantine_kernel_role()
    _install_support_constraints()
    _install_foundation_tables()
    _narrow_erasure_request_acl()
    _narrow_identity_support_acl()
    _assert_installed_contract()


def downgrade() -> None:
    evidence = " OR ".join(
        (
            *(f"EXISTS (SELECT 1 FROM {table})" for table in FOUNDATION_TABLES),
            "EXISTS (SELECT 1 FROM privacy.erasure_requests "
            "WHERE identity_person_scope_commitment IS NOT NULL)",
        )
    )
    op.execute(
        f"""
        DO $downgrade$
        BEGIN
          IF {evidence} THEN
            RAISE EXCEPTION 'identity_erasure_e1_downgrade_blocked'
              USING ERRCODE = '2BP01';
          END IF;
        END
        $downgrade$
        """
    )
    for table in reversed(FOUNDATION_TABLES):
        schema_name, table_name = table.split(".", 1)
        op.drop_table(table_name, schema=schema_name)
    for table, name, _ in reversed(SUPPORT_CONSTRAINTS):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {name}")
    op.execute(
        "ALTER TABLE privacy.erasure_requests "
        f"DROP CONSTRAINT {REQUEST_SCOPE_CHECK}"
    )
    op.drop_column(
        "erasure_requests",
        "identity_person_scope_commitment",
        schema="privacy",
    )
    op.drop_column(
        "ha_user_bindings",
        BINDING_VALID_COLUMN,
        schema="identity",
    )
