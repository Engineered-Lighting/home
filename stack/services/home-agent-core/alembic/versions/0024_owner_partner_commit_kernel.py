"""Add the dormant owner-attested partner-relationship commit kernel.

Revision ID: 0024_owner_partner_e5k
Revises: 0023_partner_vocabulary_e5j
Create Date: 2026-08-28

Creates one SECURITY DEFINER kernel that atomically records a partnership the
OWNER asserts, and nothing else. It creates no fact on its own, mounts no API,
grants nothing to a runtime role, and cannot be reached until a later reviewed
change provisions its caller.

## Why owner-attested is a different contract

parent_of was committed by E5f with authority 'explicit_related_party': a second
party confirmed it in a browser. Most people in a household have no account and
cannot confirm anything, so a partnership asserted by the owner alone is a
weaker claim and must be recorded as one. fact_versions already carries an
authority axis for exactly this: 'authorized_administrator'. Stamping an owner
assertion as 'explicit_related_party' would forge a confirmation that never
happened, so the value is a literal here, never a parameter.

## Symmetry

A partnership is symmetric and is stored as two directed facts, matching the
legacy store and matching how E5f writes two facts for two parents. Both are
written in the same transaction or neither is: a half-recorded partnership would
satisfy uq_active_partner_relationship while being false in one direction.

## What it deliberately does not do

The subject must be the bound account holder. A partnership between two other
people is a third-party assertion, which needs its own review of who may speak
for whom, and is not this revision.
"""

from __future__ import annotations

from typing import Sequence

from alembic import op


revision: str = "0024_owner_partner_e5k"
down_revision: str = "0023_partner_vocabulary_e5j"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

CALLER_ROLE = "home_agent_binding_committer"
KERNEL_ROLE = "home_agent_partner_relationship_kernel"


def upgrade() -> None:
    op.execute(
        f"""
        DO $bootstrap$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_authid WHERE rolname = '{KERNEL_ROLE}'
          ) THEN
            -- Dormant exactly as the other kernels are: it cannot log in, and
            -- VALID UNTIL the epoch means even acquiring a password would not
            -- help. A ceremony activates it briefly and revokes it again.
            CREATE ROLE {KERNEL_ROLE} NOLOGIN VALID UNTIL '1970-01-01';
          END IF;
        END
        $bootstrap$;
        """
    )

    # Owner-attested receipts are a separate ledger from E5f's. They record a
    # weaker claim (nobody confirmed it but the owner) and must never be
    # mistaken for one, so they do not share a table with the confirmed ones.
    op.execute(
        """
        CREATE TABLE operations.partner_relationship_authority_receipts (
          receipt_id uuid PRIMARY KEY,
          ceremony_id uuid NOT NULL UNIQUE,
          principal_id uuid NOT NULL
            REFERENCES identity.principals (principal_id),
          subject_person_id uuid NOT NULL
            REFERENCES identity.people (person_id),
          partner_person_id uuid NOT NULL
            REFERENCES identity.people (person_id),
          contract_version varchar(64) NOT NULL,
          edge_count integer NOT NULL,
          authority_result varchar(32) NOT NULL,
          document_digest varchar(64) NOT NULL,
          memory_transaction_id uuid NOT NULL
            REFERENCES knowledge.memory_transactions (transaction_id),
          attested_at timestamptz NOT NULL,
          CONSTRAINT partner_receipt_contract CHECK (
            contract_version = 'owner-partner-attestation-v1'
            AND edge_count = 2
            AND authority_result = 'committed'
          ),
          CONSTRAINT partner_receipt_digest_shape CHECK (
            document_digest ~ '^[0-9a-f]{64}$'
          ),
          CONSTRAINT partner_receipt_not_reflexive CHECK (
            subject_person_id <> partner_person_id
          )
        );

        CREATE TABLE operations.partner_relationship_authority_receipt_edges (
          receipt_edge_id uuid PRIMARY KEY,
          receipt_id uuid NOT NULL
            REFERENCES operations.partner_relationship_authority_receipts
              (receipt_id),
          ordinal integer NOT NULL,
          fact_version_id uuid NOT NULL UNIQUE
            REFERENCES knowledge.fact_versions (fact_version_id),
          CONSTRAINT partner_edge_ordinal CHECK (ordinal IN (0, 1)),
          CONSTRAINT partner_edge_unique_ordinal UNIQUE (receipt_id, ordinal)
        );

        ALTER TABLE operations.partner_relationship_authority_receipts
          ENABLE ROW LEVEL SECURITY;
        ALTER TABLE operations.partner_relationship_authority_receipts
          FORCE ROW LEVEL SECURITY;
        ALTER TABLE operations.partner_relationship_authority_receipt_edges
          ENABLE ROW LEVEL SECURITY;
        ALTER TABLE operations.partner_relationship_authority_receipt_edges
          FORCE ROW LEVEL SECURITY;

        CREATE POLICY partner_receipts_owner_only
          ON operations.partner_relationship_authority_receipts
          FOR ALL
          USING (session_user = 'home_agent_owner')
          WITH CHECK (session_user = 'home_agent_owner');
        CREATE POLICY partner_receipt_edges_owner_only
          ON operations.partner_relationship_authority_receipt_edges
          FOR ALL
          USING (session_user = 'home_agent_owner')
          WITH CHECK (session_user = 'home_agent_owner');
        """
    )

    op.execute(
        f"""
        CREATE FUNCTION identity.commit_owner_partner_relationship_e5k(
          target_ceremony_id uuid,
          target_ha_user_id text,
          target_partner_person_id uuid,
          target_document_digest text,
          new_memory_transaction_id uuid,
          new_fact_id_self uuid,
          new_fact_id_partner uuid,
          new_fact_version_id_self uuid,
          new_fact_version_id_partner uuid,
          new_support_id_self uuid,
          new_support_id_partner uuid,
          new_receipt_id uuid,
          new_receipt_edge_id_0 uuid,
          new_receipt_edge_id_1 uuid,
          new_attestation_artifact_id uuid
        ) RETURNS uuid
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $function$
        DECLARE
          e5k_binding record;
          e5k_operation_time timestamptz;
          e5k_existing uuid;
          e5k_affected integer;
          e5k_fact_version_ids uuid[];
          e5k_fact_ids uuid[];
          e5k_support_ids uuid[];
          e5k_subjects uuid[];
          e5k_objects uuid[];
          e5k_index integer;
        BEGIN
          -- The caller must be the committer reached through its own
          -- credential, and must NOT be able to SET ROLE into this kernel:
          -- a caller who can become the kernel could bypass every check below.
          IF session_user <> '{CALLER_ROLE}'
             OR current_user <> '{KERNEL_ROLE}'
             OR pg_catalog.pg_has_role(
                  session_user, '{KERNEL_ROLE}', 'SET'
                ) THEN
            RAISE EXCEPTION 'owner_partner_e5k_role_invalid'
              USING ERRCODE = '42501';
          END IF;

          IF pg_catalog.current_setting('transaction_isolation')
               <> 'serializable'
             OR pg_catalog.current_setting('transaction_read_only') <> 'off'
             OR pg_catalog.pg_is_in_recovery()
             OR pg_catalog.pg_current_xact_id_if_assigned() IS NOT NULL THEN
            RAISE EXCEPTION 'owner_partner_e5k_transaction_invalid'
              USING ERRCODE = '25000';
          END IF;

          -- Every identifier is supplied by the caller and must be distinct;
          -- a repeated UUID would silently collapse two rows into one.
          IF (
            SELECT count(DISTINCT id) FROM unnest(ARRAY[
              target_ceremony_id, new_memory_transaction_id,
              new_fact_id_self, new_fact_id_partner,
              new_fact_version_id_self, new_fact_version_id_partner,
              new_support_id_self, new_support_id_partner,
              new_receipt_id, new_receipt_edge_id_0, new_receipt_edge_id_1,
              new_attestation_artifact_id
            ]) AS id
          ) <> 12 THEN
            RAISE EXCEPTION 'owner_partner_e5k_identifiers_invalid'
              USING ERRCODE = '22023';
          END IF;

          IF target_document_digest !~ '^[0-9a-f]{{64}}$' THEN
            RAISE EXCEPTION 'owner_partner_e5k_digest_invalid'
              USING ERRCODE = '22023';
          END IF;

          -- Replay must be an exact-match proof, never a repair. A ceremony
          -- that already committed returns its receipt unchanged; it never
          -- writes a second time and never patches a divergent row.
          SELECT receipt.receipt_id
            INTO e5k_existing
            FROM operations.partner_relationship_authority_receipts AS receipt
           WHERE receipt.ceremony_id = target_ceremony_id
             AND receipt.document_digest = target_document_digest
             AND receipt.authority_result = 'committed';
          IF e5k_existing IS NOT NULL THEN
            RETURN e5k_existing;
          END IF;

          -- First application-data operation, as in E5f: the fence orders this
          -- against every other semantic writer for the whole transaction.
          PERFORM privacy.lock_identity_semantic_write_fence();

          SELECT binding.principal_id, binding.person_id
            INTO e5k_binding
            FROM identity.ha_user_bindings AS binding
           WHERE binding.ha_user_id = target_ha_user_id
             AND binding.revoked_at IS NULL;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'owner_partner_e5k_binding_missing'
              USING ERRCODE = '42501';
          END IF;

          -- The owner asserts about themselves. A partnership between two other
          -- people is a third-party claim and is out of scope here.
          IF e5k_binding.person_id = target_partner_person_id THEN
            RAISE EXCEPTION 'owner_partner_e5k_reflexive'
              USING ERRCODE = '22023';
          END IF;

          IF NOT EXISTS (
            SELECT 1 FROM identity.people AS person
             WHERE person.person_id = target_partner_person_id
               AND person.status = 'active'
          ) THEN
            RAISE EXCEPTION 'owner_partner_e5k_partner_unavailable'
              USING ERRCODE = '42501';
          END IF;

          -- Erasure is an interlock, checked before any write and after the
          -- fence, so a concurrent erasure cannot slip in behind it.
          IF privacy.identity_person_is_blocked(e5k_binding.person_id)
             OR privacy.identity_person_is_blocked(target_partner_person_id)
             OR EXISTS (
               SELECT 1 FROM identity.privacy_directives AS directive
                WHERE directive.person_id IN (
                        e5k_binding.person_id, target_partner_person_id
                      )
                  AND directive.enabled
             ) THEN
            RAISE EXCEPTION 'owner_partner_e5k_privacy_blocked'
              USING ERRCODE = '42501';
          END IF;

          -- Refuse rather than duplicate. uq_active_partner_relationship would
          -- catch one direction, but failing here names the reason.
          IF EXISTS (
            SELECT 1 FROM knowledge.fact_versions AS fact
             WHERE fact.predicate = 'partner_of'
               AND upper_inf(fact.system_range)
               AND fact.resolution = 'accepted'
               AND fact.perspective_principal_id = e5k_binding.principal_id
               AND (
                 (fact.subject_id = e5k_binding.person_id
                  AND (fact.object ->> 'person_id')::uuid
                        = target_partner_person_id)
                 OR (fact.subject_id = target_partner_person_id
                     AND (fact.object ->> 'person_id')::uuid
                           = e5k_binding.person_id)
               )
          ) THEN
            RAISE EXCEPTION 'owner_partner_e5k_already_recorded'
              USING ERRCODE = '23505';
          END IF;

          e5k_operation_time := pg_catalog.clock_timestamp();

          -- The attestation IS the artifact. fact_support.artifact_id is NOT
          -- NULL, and an unprovenanced support row would leave an owner-
          -- asserted fact with no root to point at.
          INSERT INTO privacy.artifact_registry (
            artifact_id, artifact_kind, store, external_ref, content_sha256,
            owner_principal_id, retention_class, status, created_at
          ) VALUES (
            new_attestation_artifact_id, 'owner_attestation',
            'postgresql', NULL, target_document_digest,
            e5k_binding.principal_id, 'governed_history', 'active',
            e5k_operation_time
          );

          INSERT INTO knowledge.memory_transactions (
            transaction_id, principal_id, visit_id, kind, state,
            exact_text_ciphertext, exact_text_nonce, exact_text_sha256,
            candidate, preview, verifier_results, policy_version,
            policy_digest, confirmation_digest, confirmed_at,
            created_at, updated_at
          ) VALUES (
            new_memory_transaction_id, e5k_binding.principal_id, NULL,
            'owner_partner_attestation', 'committed',
            NULL, NULL, NULL,
            pg_catalog.jsonb_build_object(
              'contract', 'owner-partner-attestation-v1',
              'edge_count', 2,
              'document_digest', target_document_digest
            ),
            pg_catalog.jsonb_build_object(
              'contract', 'owner-partner-preview-v1',
              'partner_person_id', target_partner_person_id::text
            ),
            pg_catalog.jsonb_build_array(
              pg_catalog.jsonb_build_object(
                'result', 'passed',
                'rule', 'authorized_administrator_attestation',
                'rule_version', 'e5k-v1'
              )
            ),
            'home-agent-mvp-v1', target_document_digest,
            target_document_digest, e5k_operation_time,
            e5k_operation_time, e5k_operation_time
          );

          -- Both directions, or neither. A half-recorded partnership would
          -- satisfy the uniqueness index while being false one way round.
          e5k_fact_ids := ARRAY[new_fact_id_self, new_fact_id_partner];
          e5k_fact_version_ids :=
            ARRAY[new_fact_version_id_self, new_fact_version_id_partner];
          e5k_support_ids := ARRAY[new_support_id_self, new_support_id_partner];
          e5k_subjects :=
            ARRAY[e5k_binding.person_id, target_partner_person_id];
          e5k_objects :=
            ARRAY[target_partner_person_id, e5k_binding.person_id];

          FOR e5k_index IN 1..2 LOOP
            INSERT INTO knowledge.fact_versions (
              fact_version_id, fact_id, version, subject_type, subject_id,
              predicate, object, perspective_principal_id, valid_range,
              system_range, authority, support, contradiction, freshness,
              coverage, resolution, privacy_scope, memory_transaction_id,
              committed_at
            ) VALUES (
              e5k_fact_version_ids[e5k_index],
              e5k_fact_ids[e5k_index], 1, 'person',
              e5k_subjects[e5k_index], 'partner_of',
              pg_catalog.jsonb_build_object(
                'person_id', e5k_objects[e5k_index]::text
              ),
              e5k_binding.principal_id,
              pg_catalog.tstzrange(e5k_operation_time, NULL, '[)'),
              pg_catalog.tstzrange(e5k_operation_time, NULL, '[)'),
              -- Literal, never a parameter: the owner asserted this and no
              -- second party confirmed it. Recording it as
              -- 'explicit_related_party' would forge a confirmation.
              'authorized_administrator', 'explicit_authority', 'none',
              'not_applicable', 'not_applicable', 'accepted', 'private',
              new_memory_transaction_id, e5k_operation_time
            );

            INSERT INTO knowledge.fact_support (
              support_id, fact_version_id, artifact_id,
              root_observation_id, dependency_domain, support_role, created_at
            ) VALUES (
              e5k_support_ids[e5k_index],
              e5k_fact_version_ids[e5k_index],
              new_attestation_artifact_id, NULL,
              'owner_attestation', 'attestation',
              e5k_operation_time
            );
          END LOOP;

          INSERT INTO operations.partner_relationship_authority_receipts (
            receipt_id, ceremony_id, principal_id, subject_person_id,
            partner_person_id, contract_version, edge_count,
            authority_result, document_digest, memory_transaction_id,
            attested_at
          ) VALUES (
            new_receipt_id, target_ceremony_id, e5k_binding.principal_id,
            e5k_binding.person_id, target_partner_person_id,
            'owner-partner-attestation-v1', 2,
            'committed', target_document_digest, new_memory_transaction_id,
            e5k_operation_time
          );

          INSERT INTO operations.partner_relationship_authority_receipt_edges (
            receipt_edge_id, receipt_id, ordinal, fact_version_id
          ) VALUES
            (new_receipt_edge_id_0, new_receipt_id, 0,
             new_fact_version_id_self),
            (new_receipt_edge_id_1, new_receipt_id, 1,
             new_fact_version_id_partner);

          GET DIAGNOSTICS e5k_affected = ROW_COUNT;
          IF e5k_affected <> 2 THEN
            RAISE EXCEPTION 'owner_partner_e5k_edges_invalid'
              USING ERRCODE = '25000';
          END IF;

          RETURN new_receipt_id;
        END
        $function$;
        """
    )

    op.execute(
        f"ALTER FUNCTION identity.commit_owner_partner_relationship_e5k("
        f"uuid, text, uuid, text, uuid, uuid, uuid, uuid, uuid, uuid, uuid, "
        f"uuid, uuid, uuid) OWNER TO {KERNEL_ROLE};"
    )
    # Deliberately no GRANT EXECUTE. The kernel is unreachable until a later
    # reviewed change provisions the caller, exactly as E5d left E5f dormant.


def downgrade() -> None:
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "identity.commit_owner_partner_relationship_e5k("
        "uuid, text, uuid, text, uuid, uuid, uuid, uuid, uuid, uuid, uuid, "
        "uuid, uuid, uuid);"
    )
    op.execute(
        "DROP TABLE IF EXISTS "
        "operations.partner_relationship_authority_receipt_edges;"
    )
    op.execute(
        "DROP TABLE IF EXISTS "
        "operations.partner_relationship_authority_receipts;"
    )
