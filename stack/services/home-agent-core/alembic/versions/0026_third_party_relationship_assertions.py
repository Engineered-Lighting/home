"""Let the owner assert a relationship between two other household members.

Revision ID: 0026_third_party_e5m
Revises: 0025_owner_partner_caller_e5l
Create Date: 2026-08-28

E5k could only record a relationship the account holder is one end of, because
it derived the subject from the binding. The household graph is mostly not like
that: Holly is a parent of Ben, Felipe and Ashley are partners of each other,
and none of those people has an account.

## Who may speak for whom

The owner may. That is a real answer, not an evasion, but it is a WEAKER claim
than E5k's -- there the attester is at least one end of the edge and is
asserting about their own life. Here they are asserting about two other people,
neither of whom has consented.

The record must not flatten that difference, so the receipt gains
``assertion_scope``: 'self' when the attester is an endpoint, 'third_party' when
they are not. Both remain 'authorized_administrator' on the authority axis --
that axis says who had the standing, not how close they stood -- and
assertion_scope says the rest. A later review that decides third-party
assertions need something stronger can find every one of them with a WHERE
clause rather than by re-deriving the graph.

## What does not change

Both people must be active, unerased and undirected; the two facts are still
written together or not at all; the perspective principal is still the
attester's, so these facts are that person's belief about the household rather
than an impersonal truth.
"""

from __future__ import annotations

from typing import Sequence

from alembic import op


revision = "0026_third_party_e5m"
down_revision = "0025_owner_partner_caller_e5l"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

CALLER_ROLE = "home_agent_binding_committer"
KERNEL_ROLE = "home_agent_partner_relationship_kernel"


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE operations.partner_relationship_authority_receipts
          ADD COLUMN assertion_scope varchar(16) NOT NULL DEFAULT 'self',
          ADD COLUMN predicate varchar(128) NOT NULL DEFAULT 'partner_of';
        ALTER TABLE operations.partner_relationship_authority_receipts
          ALTER COLUMN assertion_scope DROP DEFAULT,
          ALTER COLUMN predicate DROP DEFAULT;
        ALTER TABLE operations.partner_relationship_authority_receipts
          ADD CONSTRAINT partner_receipt_scope CHECK (
            assertion_scope IN ('self', 'third_party')
          );
        -- The scope must agree with the row rather than being a free label:
        -- a 'self' receipt whose subject is not the attester's own person
        -- would misdescribe what happened.
        ALTER TABLE operations.partner_relationship_authority_receipts
          ADD CONSTRAINT partner_receipt_predicate CHECK (
            predicate IN ('partner_of', 'parent_of')
          );
        """
    )

    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION
          identity.commit_owner_partner_relationship_e5k(
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
            new_attestation_artifact_id uuid,
            target_subject_person_id uuid DEFAULT NULL,
            target_predicate text DEFAULT 'partner_of'
          ) RETURNS uuid
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $function$
        DECLARE
          e5m_binding record;
          e5m_subject uuid;
          e5m_scope text;
          e5m_symmetric boolean;
          e5m_operation_time timestamptz;
          e5m_existing uuid;
          e5m_index integer;
          e5m_subjects uuid[];
          e5m_objects uuid[];
          e5m_fact_ids uuid[];
          e5m_fact_version_ids uuid[];
          e5m_support_ids uuid[];
        BEGIN
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

          IF target_predicate NOT IN ('partner_of', 'parent_of') THEN
            RAISE EXCEPTION 'owner_partner_e5m_predicate_invalid'
              USING ERRCODE = '22023';
          END IF;
          -- partner_of is symmetric and is stored both ways. parent_of is not:
          -- writing the inverse would assert that a child is a parent.
          e5m_symmetric := target_predicate = 'partner_of';

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

          SELECT receipt.receipt_id
            INTO e5m_existing
            FROM operations.partner_relationship_authority_receipts AS receipt
           WHERE receipt.ceremony_id = target_ceremony_id
             AND receipt.document_digest = target_document_digest
             AND receipt.authority_result = 'committed';
          IF e5m_existing IS NOT NULL THEN
            RETURN e5m_existing;
          END IF;

          PERFORM privacy.lock_identity_semantic_write_fence();

          SELECT binding.principal_id, binding.person_id
            INTO e5m_binding
            FROM identity.ha_user_bindings AS binding
           WHERE binding.ha_user_id = target_ha_user_id
             AND binding.revoked_at IS NULL;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'owner_partner_e5k_binding_missing'
              USING ERRCODE = '42501';
          END IF;

          -- Omitted subject means the attester is one end, which is E5k's
          -- original contract and stays the default.
          e5m_subject := coalesce(target_subject_person_id,
                                  e5m_binding.person_id);
          e5m_scope := CASE
            WHEN e5m_subject = e5m_binding.person_id THEN 'self'
            ELSE 'third_party'
          END;

          IF e5m_subject = target_partner_person_id THEN
            RAISE EXCEPTION 'owner_partner_e5k_reflexive'
              USING ERRCODE = '22023';
          END IF;

          -- Both endpoints, not just the far one: a third-party assertion has
          -- two people who are not the attester.
          IF (
            SELECT count(*) FROM identity.people AS person
             WHERE person.person_id IN (e5m_subject, target_partner_person_id)
               AND person.status = 'active'
          ) <> 2 THEN
            RAISE EXCEPTION 'owner_partner_e5k_partner_unavailable'
              USING ERRCODE = '42501';
          END IF;

          IF privacy.identity_person_is_blocked(e5m_subject)
             OR privacy.identity_person_is_blocked(target_partner_person_id)
             OR EXISTS (
               SELECT 1 FROM identity.privacy_directives AS directive
                WHERE directive.person_id IN (
                        e5m_subject, target_partner_person_id
                      )
                  AND directive.enabled
             ) THEN
            RAISE EXCEPTION 'owner_partner_e5k_privacy_blocked'
              USING ERRCODE = '42501';
          END IF;

          IF EXISTS (
            SELECT 1 FROM knowledge.fact_versions AS fact
             WHERE fact.predicate = target_predicate
               AND upper_inf(fact.system_range)
               AND fact.resolution = 'accepted'
               AND fact.perspective_principal_id = e5m_binding.principal_id
               AND (
                 (fact.subject_id = e5m_subject
                  AND (fact.object ->> 'person_id')::uuid
                        = target_partner_person_id)
                 OR (e5m_symmetric
                     AND fact.subject_id = target_partner_person_id
                     AND (fact.object ->> 'person_id')::uuid = e5m_subject)
               )
          ) THEN
            RAISE EXCEPTION 'owner_partner_e5k_already_recorded'
              USING ERRCODE = '23505';
          END IF;

          e5m_operation_time := pg_catalog.clock_timestamp();

          INSERT INTO privacy.artifact_registry (
            artifact_id, artifact_kind, store, external_ref, content_sha256,
            owner_principal_id, retention_class, status, created_at
          ) VALUES (
            new_attestation_artifact_id, 'owner_attestation',
            'postgresql', NULL, target_document_digest,
            e5m_binding.principal_id, 'governed_history', 'active',
            e5m_operation_time
          );

          INSERT INTO knowledge.memory_transactions (
            transaction_id, principal_id, visit_id, kind, state,
            exact_text_ciphertext, exact_text_nonce, exact_text_sha256,
            candidate, preview, verifier_results, policy_version,
            policy_digest, confirmation_digest, confirmed_at,
            created_at, updated_at
          ) VALUES (
            new_memory_transaction_id, e5m_binding.principal_id, NULL,
            'owner_partner_attestation', 'committed',
            NULL, NULL, NULL,
            pg_catalog.jsonb_build_object(
              'contract', 'owner-partner-attestation-v1',
              'predicate', target_predicate,
              'assertion_scope', e5m_scope,
              'edge_count', CASE WHEN e5m_symmetric THEN 2 ELSE 1 END,
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
                'rule_version', 'e5m-v1'
              )
            ),
            'home-agent-mvp-v1', target_document_digest,
            target_document_digest, e5m_operation_time,
            e5m_operation_time, e5m_operation_time
          );

          e5m_subjects := ARRAY[e5m_subject, target_partner_person_id];
          e5m_objects := ARRAY[target_partner_person_id, e5m_subject];
          e5m_fact_ids := ARRAY[new_fact_id_self, new_fact_id_partner];
          e5m_fact_version_ids :=
            ARRAY[new_fact_version_id_self, new_fact_version_id_partner];
          e5m_support_ids :=
            ARRAY[new_support_id_self, new_support_id_partner];

          FOR e5m_index IN 1..(CASE WHEN e5m_symmetric THEN 2 ELSE 1 END) LOOP
            INSERT INTO knowledge.fact_versions (
              fact_version_id, fact_id, version, subject_type, subject_id,
              predicate, object, perspective_principal_id, valid_range,
              system_range, authority, support, contradiction, freshness,
              coverage, resolution, privacy_scope, memory_transaction_id,
              committed_at
            ) VALUES (
              e5m_fact_version_ids[e5m_index],
              e5m_fact_ids[e5m_index], 1, 'person',
              e5m_subjects[e5m_index], target_predicate,
              pg_catalog.jsonb_build_object(
                'person_id', e5m_objects[e5m_index]::text
              ),
              e5m_binding.principal_id,
              pg_catalog.tstzrange(e5m_operation_time, NULL, '[)'),
              pg_catalog.tstzrange(e5m_operation_time, NULL, '[)'),
              'authorized_administrator', 'explicit_authority', 'none',
              'not_applicable', 'not_applicable', 'accepted', 'private',
              new_memory_transaction_id, e5m_operation_time
            );

            INSERT INTO knowledge.fact_support (
              support_id, fact_version_id, artifact_id,
              root_observation_id, dependency_domain, support_role, created_at
            ) VALUES (
              e5m_support_ids[e5m_index],
              e5m_fact_version_ids[e5m_index],
              new_attestation_artifact_id, NULL,
              'owner_attestation', 'attestation',
              e5m_operation_time
            );
          END LOOP;

          INSERT INTO operations.partner_relationship_authority_receipts (
            receipt_id, ceremony_id, principal_id, subject_person_id,
            partner_person_id, contract_version, edge_count,
            authority_result, document_digest, memory_transaction_id,
            assertion_scope, predicate, attested_at
          ) VALUES (
            new_receipt_id, target_ceremony_id, e5m_binding.principal_id,
            e5m_subject, target_partner_person_id,
            'owner-partner-attestation-v1',
            CASE WHEN e5m_symmetric THEN 2 ELSE 1 END,
            'committed', target_document_digest, new_memory_transaction_id,
            e5m_scope, target_predicate, e5m_operation_time
          );

          INSERT INTO operations.partner_relationship_authority_receipt_edges (
            receipt_edge_id, receipt_id, ordinal, fact_version_id
          )
          SELECT edge.receipt_edge_id, new_receipt_id, edge.ordinal,
                 edge.fact_version_id
            FROM (VALUES
              (new_receipt_edge_id_0, 0, new_fact_version_id_self),
              (new_receipt_edge_id_1, 1, new_fact_version_id_partner)
            ) AS edge(receipt_edge_id, ordinal, fact_version_id)
           WHERE e5m_symmetric OR edge.ordinal = 0;

          RETURN new_receipt_id;
        END
        $function$;
        """
    )

    # edge_count is no longer always 2: a parent_of assertion writes one edge.
    op.execute(
        """
        ALTER TABLE operations.partner_relationship_authority_receipts
          DROP CONSTRAINT partner_receipt_contract;
        ALTER TABLE operations.partner_relationship_authority_receipts
          ADD CONSTRAINT partner_receipt_contract CHECK (
            contract_version = 'owner-partner-attestation-v1'
            AND authority_result = 'committed'
            AND (
              (predicate = 'partner_of' AND edge_count = 2)
              OR (predicate = 'parent_of' AND edge_count = 1)
            )
          );
        """
    )

    op.execute(
        f"GRANT EXECUTE ON FUNCTION "
        f"identity.commit_owner_partner_relationship_e5k("
        f"uuid, text, uuid, text, uuid, uuid, uuid, uuid, uuid, uuid, uuid, "
        f"uuid, uuid, uuid, uuid, uuid, text) TO {CALLER_ROLE};"
    )


def downgrade() -> None:
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "identity.commit_owner_partner_relationship_e5k("
        "uuid, text, uuid, text, uuid, uuid, uuid, uuid, uuid, uuid, uuid, "
        "uuid, uuid, uuid, uuid, uuid, text);"
    )
    op.execute(
        """
        ALTER TABLE operations.partner_relationship_authority_receipts
          DROP CONSTRAINT partner_receipt_predicate,
          DROP CONSTRAINT partner_receipt_scope,
          DROP COLUMN predicate,
          DROP COLUMN assertion_scope;
        """
    )
