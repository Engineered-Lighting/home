"""Let the owner add a person, with the privacy closure written in the same act.

Revision ID: 0027_owner_person_e5n
Revises: 0026_third_party_e5m
Create Date: 2026-08-28

The legacy per-item People import was retired, and `store.create_person` was
left orphaned rather than re-exposed. Reading what it did explains why: it
INSERTed a row into identity.people and stopped. A person could exist with no
provenance anyone could audit and no privacy state decided, and the decision
about how the system may treat them was deferred to whoever wrote the next row.

This kernel refuses that shape. Creating a person and establishing their privacy
closure are one transaction or neither:

* exactly one status, and it is a literal -- a caller cannot create somebody
  already erased, or in a state no code expects;
* provenance is an owner attestation artifact, NOT a legacy source ref. An
  owner-created person must never be able to masquerade as a reviewed import,
  because the reviewed-import path had a verifier this one does not;
* an initial directive is optional, but if it is `auto_expire` its schedule is
  written in the same transaction. An auto-expiring person with no expiry is a
  person who never expires, which is the opposite of what was asked for;
* `ignored` and `do_not_track` are accepted, and the erasure interlock is
  re-checked, so a person cannot be created into a state the suppression
  policies would already hide.

It creates no principal and no binding. Being known to the household is not the
same as having an account, and conflating them is how a person ends up with
authority nobody granted.
"""

from __future__ import annotations

from typing import Sequence

from alembic import op


revision: str = "0027_owner_person_e5n"
down_revision: str = "0026_third_party_e5m"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

CALLER_ROLE = "home_agent_binding_committer"
KERNEL_ROLE = "home_agent_identity_finalizer_kernel"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE FUNCTION identity.create_owner_attested_person_e5n(
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
             OR current_user <> '{KERNEL_ROLE}'
             OR pg_catalog.pg_has_role(
                  session_user, '{KERNEL_ROLE}', 'SET'
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
             OR pg_catalog.btrim(target_display_name) = ''
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
            new_person_id, pg_catalog.btrim(target_display_name),
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
        $function$;
        """
    )

    # The finalizer kernel can write people and directives but holds nothing at
    # all on privacy.artifact_registry -- checked at column level, because
    # has_table_privilege cannot see column grants and reports the same false
    # for roles that do hold them. Provenance is mandatory, so this widening is
    # deliberate, and scoped to exactly the columns the kernel writes rather
    # than a blanket table privilege.
    op.execute(
        f"""
        GRANT INSERT (
          artifact_id, artifact_kind, store, external_ref, content_sha256,
          owner_principal_id, retention_class, status, created_at
        ) ON privacy.artifact_registry TO {KERNEL_ROLE};
        """
    )

    op.execute(
        "ALTER FUNCTION identity.create_owner_attested_person_e5n("
        "uuid, text, text, text, text, text, timestamptz, text, "
        f"uuid, uuid, uuid) OWNER TO {KERNEL_ROLE};"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION identity.create_owner_attested_person_e5n("
        "uuid, text, text, text, text, text, timestamptz, text, "
        "uuid, uuid, uuid) FROM PUBLIC;"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION identity.create_owner_attested_person_e5n("
        "uuid, text, text, text, text, text, timestamptz, text, "
        f"uuid, uuid, uuid) TO {CALLER_ROLE};"
    )


def downgrade() -> None:
    op.execute(
        "DROP FUNCTION IF EXISTS identity.create_owner_attested_person_e5n("
        "uuid, text, text, text, text, text, timestamptz, text, "
        "uuid, uuid, uuid);"
    )
    # The widening must not outlive the feature that justified it.
    op.execute(
        f"""
        REVOKE INSERT (
          artifact_id, artifact_kind, store, external_ref, content_sha256,
          owner_principal_id, retention_class, status, created_at
        ) ON privacy.artifact_registry FROM {KERNEL_ROLE};
        """
    )
