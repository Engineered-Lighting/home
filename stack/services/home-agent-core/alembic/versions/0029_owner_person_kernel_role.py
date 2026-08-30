"""Give owner-attested person creation a kernel role of its own.

Revision ID: 0029_owner_person_role_e5p
Revises: 0028_owner_partner_access_e5o
Create Date: 2026-08-29

Adding a person has never worked. The kernel reads identity.ha_user_bindings to
authenticate its caller, and the role it runs as holds no privilege on that
table -- deliberately. 0027 gave the function to
home_agent_identity_finalizer_kernel, an existing dormant role belonging to the
E3 finalizer, instead of minting one of its own. The E3 contract forbids that
role from reaching identity.ha_user_bindings by name:

    tests/home_agent/test_identity_finalizer_e3_deployment_contract.py
      for forbidden_write in ("identity.principals", "identity.ha_user_bindings",
                              "identity.confirmation_artifacts", ...):
          assert forbidden_write not in admission

So the kernel wanted an access its own role is contractually denied. Granting it
would have widened a reviewed security boundary to suit one caller; this gives
the kernel a role instead.

THE BODY IS REPLACED, NOT JUST REASSIGNED. The function names its own role as a
literal inside its opening guard:

    IF session_user <> 'home_agent_binding_committer'
       OR current_user <> '<the kernel role>'
       OR pg_catalog.pg_has_role(session_user, '<the kernel role>', 'SET') THEN
      RAISE EXCEPTION 'owner_person_e5n_role_invalid';

Moving ownership alone would set current_user to the new role while the guard
still demanded the old one, and every call would raise. That is exactly the
fault 0028 repaired for the partner kernel, in reverse. The body below is
0027's, byte-identical except for those two role literals, re-emitted with
CREATE OR REPLACE on an unchanged signature -- which preserves the ACL.

0027's widening of the E3 role is also undone here: it granted that role INSERT
on privacy.artifact_registry solely so this kernel could write its attestation.
With the function moved, the reason is gone and so is the grant, and the E3
ownership contract can go back to expecting exactly one owned object.
"""

from __future__ import annotations

from typing import Sequence

from alembic import op


revision: str = "0029_owner_person_role_e5p"
down_revision: str | None = "0028_owner_partner_access_e5o"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

NEW_KERNEL_ROLE = "home_agent_owner_person_kernel"
OLD_KERNEL_ROLE = "home_agent_identity_finalizer_kernel"
CALLER_ROLE = "home_agent_binding_committer"

SIGNATURE = (
    "uuid, text, text, text, text, text, timestamptz, text, "
    "uuid, uuid, uuid"
)

# The same triple the kernel enforces internally, so the row gate never becomes
# the weaker of the two checks.
KERNEL_PREDICATE = (
    f"session_user = '{CALLER_ROLE}' "
    f"AND current_user = '{NEW_KERNEL_ROLE}' "
    f"AND NOT pg_catalog.pg_has_role(session_user, '{NEW_KERNEL_ROLE}', 'SET')"
)

# 0027's artifact columns, revoked from the E3 role and granted to this one in
# apply-grants.sh, where kernel grants live.
ARTIFACT_COLUMNS = (
    "artifact_id, artifact_kind, store, external_ref, content_sha256, "
    "owner_principal_id, retention_class, status, created_at"
)

BODY = f"""\
CREATE OR REPLACE FUNCTION identity.create_owner_attested_person_e5n(
          target_ceremony_id uuid,
          target_ha_user_id text,
          target_display_name text,
          target_pronouns text,
          target_privacy_scope text,
          target_directive text,
          target_directive_expires_at timestamptz,
          target_document_digest text,
          new_person_id uuid,
          new_attestation_artifact_id uuid,
          new_directive_id uuid
        ) RETURNS uuid
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $function$
        DECLARE
          e5n_binding record;
          e5n_operation_time timestamptz;
          e5n_existing uuid;
        BEGIN
          IF session_user <> '{CALLER_ROLE}'
             OR current_user <> '{NEW_KERNEL_ROLE}'
             OR pg_catalog.pg_has_role(
                  session_user, '{NEW_KERNEL_ROLE}', 'SET'
                ) THEN
            RAISE EXCEPTION 'owner_person_e5n_role_invalid'
              USING ERRCODE = '42501';
          END IF;

          IF pg_catalog.current_setting('transaction_isolation')
               <> 'serializable'
             OR pg_catalog.current_setting('transaction_read_only') <> 'off'
             OR pg_catalog.pg_is_in_recovery()
             OR pg_catalog.pg_current_xact_id_if_assigned() IS NOT NULL THEN
            RAISE EXCEPTION 'owner_person_e5n_transaction_invalid'
              USING ERRCODE = '25000';
          END IF;

          IF target_document_digest !~ '^[0-9a-f]{{64}}$'
             OR new_person_id = new_attestation_artifact_id THEN
            RAISE EXCEPTION 'owner_person_e5n_identifiers_invalid'
              USING ERRCODE = '22023';
          END IF;

          -- A display name is how a human will recognise this person in a
          -- confirmation later. Blank or whitespace-only is not a name.
          IF target_display_name IS NULL
             OR pg_catalog.btrim(target_display_name, E' \\t\\n\\r\\f\\v') = ''
             OR pg_catalog.length(target_display_name) > 255 THEN
            RAISE EXCEPTION 'owner_person_e5n_display_name_invalid'
              USING ERRCODE = '22023';
          END IF;

          IF target_privacy_scope NOT IN ('private', 'household') THEN
            RAISE EXCEPTION 'owner_person_e5n_privacy_scope_invalid'
              USING ERRCODE = '22023';
          END IF;

          IF target_directive IS NOT NULL
             AND target_directive NOT IN (
               'do_not_track', 'ignored', 'silent', 'private', 'auto_expire'
             ) THEN
            RAISE EXCEPTION 'owner_person_e5n_directive_invalid'
              USING ERRCODE = '22023';
          END IF;

          -- An auto-expiring person with no expiry never expires, which is the
          -- opposite of what was asked for. Refuse rather than record a
          -- directive that cannot do its job.
          IF target_directive = 'auto_expire'
             AND target_directive_expires_at IS NULL THEN
            RAISE EXCEPTION 'owner_person_e5n_expiry_missing'
              USING ERRCODE = '22023';
          END IF;
          IF target_directive <> 'auto_expire'
             AND target_directive_expires_at IS NOT NULL THEN
            RAISE EXCEPTION 'owner_person_e5n_expiry_unexpected'
              USING ERRCODE = '22023';
          END IF;

          SELECT person.person_id
            INTO e5n_existing
            FROM identity.people AS person
           WHERE person.person_id = new_person_id;
          IF e5n_existing IS NOT NULL THEN
            -- Replay is a proof, not a repair: the row is returned untouched.
            RETURN e5n_existing;
          END IF;

          PERFORM privacy.lock_identity_semantic_write_fence();

          SELECT binding.principal_id, binding.person_id
            INTO e5n_binding
            FROM identity.ha_user_bindings AS binding
           WHERE binding.ha_user_id = target_ha_user_id
             AND binding.revoked_at IS NULL;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'owner_person_e5n_binding_missing'
              USING ERRCODE = '42501';
          END IF;

          IF privacy.identity_person_is_blocked(e5n_binding.person_id) THEN
            RAISE EXCEPTION 'owner_person_e5n_attester_blocked'
              USING ERRCODE = '42501';
          END IF;

          e5n_operation_time := pg_catalog.clock_timestamp();

          INSERT INTO privacy.artifact_registry (
            artifact_id, artifact_kind, store, external_ref, content_sha256,
            owner_principal_id, retention_class, status, created_at
          ) VALUES (
            new_attestation_artifact_id, 'owner_person_attestation',
            'postgresql', NULL, target_document_digest,
            e5n_binding.principal_id, 'governed_history', 'active',
            e5n_operation_time
          );

          -- status is a literal: nobody may be created already erased, or in a
          -- state no code expects. The legacy_source_* columns stay NULL --
          -- an owner-created person must never look like a reviewed import.
          INSERT INTO identity.people (
            person_id, display_name, pronouns, status, privacy_scope,
            created_at, updated_at
          ) VALUES (
            new_person_id, pg_catalog.btrim(target_display_name, E' \\t\\n\\r\\f\\v'),
            target_pronouns, 'active', target_privacy_scope,
            e5n_operation_time, e5n_operation_time
          );

          IF target_directive IS NOT NULL THEN
            INSERT INTO identity.privacy_directives (
              directive_id, person_id, directive, enabled, expires_at,
              source_artifact_id, created_at
            ) VALUES (
              new_directive_id, new_person_id, target_directive, true,
              target_directive_expires_at, new_attestation_artifact_id,
              e5n_operation_time
            );
          END IF;

          RETURN new_person_id;
        END
        $function$;"""


def upgrade() -> None:
    op.execute(
        f"""
        DO $bootstrap$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_authid
             WHERE rolname = '{NEW_KERNEL_ROLE}'
          ) THEN
            -- Dormant exactly as the sibling kernels are: it cannot log in,
            -- holds no connection, inherits nothing, and VALID UNTIL the epoch
            -- means even acquiring a password would not help.
            CREATE ROLE {NEW_KERNEL_ROLE}
              NOLOGIN NOINHERIT CONNECTION LIMIT 0 VALID UNTIL '1970-01-01';
            -- The owner must be able to assume it to reassign ownership and to
            -- issue the kernel's grants; the other kernel roles carry the same
            -- single membership.
            GRANT {NEW_KERNEL_ROLE} TO {{owner}}
              WITH ADMIN FALSE, INHERIT FALSE, SET TRUE;
          END IF;
        END
        $bootstrap$;
        """.replace("{owner}", "CURRENT_USER")
    )

    # Replace the body first: it names the role in its own guard, so moving
    # ownership without this raises owner_person_e5n_role_invalid on every call.
    op.execute(BODY)

    op.execute(
        f"ALTER FUNCTION identity.create_owner_attested_person_e5n("
        f"{SIGNATURE}) OWNER TO {NEW_KERNEL_ROLE};"
    )
    # ALTER ... OWNER TO rewrites the grantor in surviving aclitems, so the
    # committer's EXECUTE follows the function. Restated so the migration is
    # self-contained.
    op.execute(
        f"REVOKE ALL ON FUNCTION identity.create_owner_attested_person_e5n("
        f"{SIGNATURE}) FROM PUBLIC;"
    )
    op.execute(
        f"GRANT EXECUTE ON FUNCTION identity.create_owner_attested_person_e5n("
        f"{SIGNATURE}) TO {CALLER_ROLE};"
    )

    # 0028 gave the row gate to the old role. Retire those and issue the new
    # ones, including the ha_user_bindings policy the kernel never had -- the
    # half a grant alone could never have fixed, since that table forces RLS.
    op.execute(
        "DROP POLICY IF EXISTS owner_person_e5o_kernel_insert "
        "ON privacy.artifact_registry;"
    )
    op.execute(
        "DROP POLICY IF EXISTS owner_person_e5o_kernel_select "
        "ON privacy.artifact_registry;"
    )
    op.execute(
        f"CREATE POLICY artifact_registry_e5p_kernel_select "
        f"ON privacy.artifact_registry FOR SELECT TO {NEW_KERNEL_ROLE} "
        f"USING ({KERNEL_PREDICATE});"
    )
    op.execute(
        f"CREATE POLICY artifact_registry_e5p_kernel_insert "
        f"ON privacy.artifact_registry FOR INSERT TO {NEW_KERNEL_ROLE} "
        f"WITH CHECK ({KERNEL_PREDICATE});"
    )
    op.execute(
        f"CREATE POLICY ha_user_bindings_e5p_kernel_select "
        f"ON identity.ha_user_bindings FOR SELECT TO {NEW_KERNEL_ROLE} "
        f"USING ({KERNEL_PREDICATE});"
    )

    # 0027 widened the E3 role solely so this kernel could write its
    # attestation. The function has moved, so the reason has gone.
    op.execute(
        f"REVOKE INSERT ({ARTIFACT_COLUMNS}) ON privacy.artifact_registry "
        f"FROM {OLD_KERNEL_ROLE};"
    )


def downgrade() -> None:
    op.execute(
        f"GRANT INSERT ({ARTIFACT_COLUMNS}) ON privacy.artifact_registry "
        f"TO {OLD_KERNEL_ROLE};"
    )
    op.execute(
        "DROP POLICY IF EXISTS ha_user_bindings_e5p_kernel_select "
        "ON identity.ha_user_bindings;"
    )
    op.execute(
        "DROP POLICY IF EXISTS artifact_registry_e5p_kernel_insert "
        "ON privacy.artifact_registry;"
    )
    op.execute(
        "DROP POLICY IF EXISTS artifact_registry_e5p_kernel_select "
        "ON privacy.artifact_registry;"
    )
    # The body is not restored: it would name a role this downgrade is about to
    # take the function away from. Ownership returns, and 0028's policies with
    # it, which is the state 0028 left behind.
    op.execute(
        f"ALTER FUNCTION identity.create_owner_attested_person_e5n("
        f"{SIGNATURE}) OWNER TO {OLD_KERNEL_ROLE};"
    )
