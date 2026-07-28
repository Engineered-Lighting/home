"""Add authenticated E5h parent-confirmation status recovery.

Revision ID: 0021_parent_status_e5h
Revises: 0020_parent_commit_e5f
Create Date: 2026-07-28

The table-blind binding committer can recover one unexpired private preview,
observe the normalized confirmed result, or atomically close an expired
preview. Browser storage is never an authority. Partial, contradictory, or
privacy-blocked graphs fail closed.

Production remains pinned to revision 0006a and record_only.
"""

from __future__ import annotations

import hashlib
from typing import Sequence

from alembic import op


revision: str = "0021_parent_status_e5h"
down_revision: str | None = "0020_parent_commit_e5f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


CALLER_ROLE = "home_agent_binding_committer"
KERNEL_ROLE = "home_agent_parent_relationship_kernel"
FUNCTION = (
    "identity.recover_authenticated_parent_relationship_e5h(" "character varying)"
)

ALL_RUNTIME_ROLES = (
    "PUBLIC",
    "home_agent_api",
    "home_agent_binding_operator",
    "home_agent_binding_committer",
    "home_agent_identity_binding_kernel",
    "home_agent_parent_relationship_kernel",
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


FUNCTION_BODY = rf"""
DECLARE
  e5h_authority_result varchar(32);
  e5h_promotion record;
  e5h_binding record;
  e5h_proposal identity.parent_relationship_proposals%ROWTYPE;
  e5h_request identity.parent_relationship_requests%ROWTYPE;
  e5h_parent_labels text[];
  e5h_review_codes text[];
  e5h_now timestamptz;
  e5h_count bigint;
  e5h_affected integer;
  e5h_confirmed_at timestamptz;
BEGIN
  IF session_user <> '{CALLER_ROLE}'
     OR current_user <> '{KERNEL_ROLE}'
     OR pg_catalog.pg_has_role(
          session_user, '{KERNEL_ROLE}', 'SET'
        ) THEN
    RAISE EXCEPTION 'parent_relationship_e5h_role_invalid'
      USING ERRCODE = '42501';
  END IF;

  IF pg_catalog.current_setting('transaction_isolation') <> 'serializable'
     OR pg_catalog.current_setting('transaction_read_only') <> 'off'
     OR pg_catalog.pg_is_in_recovery()
     OR pg_catalog.pg_current_xact_id_if_assigned() IS NOT NULL THEN
    RAISE EXCEPTION 'parent_relationship_e5h_transaction_invalid'
      USING ERRCODE = '25000';
  END IF;

  IF authenticated_ha_user_id IS NULL
     OR authenticated_ha_user_id <> pg_catalog.btrim(authenticated_ha_user_id)
     OR pg_catalog.length(authenticated_ha_user_id) NOT BETWEEN 1 AND 64 THEN
    RAISE EXCEPTION 'parent_relationship_e5h_input_invalid'
      USING ERRCODE = '22023';
  END IF;

  PERFORM pg_catalog.set_config('statement_timeout', '15s', true);
  PERFORM pg_catalog.set_config('lock_timeout', '5s', true);
  PERFORM pg_catalog.set_config(
    'idle_in_transaction_session_timeout', '15s', true
  );
  PERFORM pg_catalog.set_config('transaction_timeout', '30s', true);

  -- The recovery kernel may close an expired preview, so it participates in
  -- the same global semantic write order as stage and commit.
  PERFORM privacy.lock_identity_semantic_write_fence();

  SELECT promotion.promotion_id,
         promotion.policy_digest,
         promotion.committed_at
    INTO e5h_promotion
    FROM operations.semantic_authority_promotions AS promotion
   WHERE promotion.authority_scope = 'identity_semantics';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'parent_relationship_e5h_authority_unavailable'
      USING ERRCODE = '55000';
  END IF;

  SELECT operations.evaluate_current_identity_semantic_authority(
           e5h_promotion.promotion_id
         )
    INTO e5h_authority_result;
  IF e5h_authority_result IS DISTINCT FROM
       'current_database_authority' THEN
    RAISE EXCEPTION 'parent_relationship_e5h_authority_not_current'
      USING ERRCODE = '55000';
  END IF;

  SELECT binding.binding_id,
         binding.principal_id,
         binding.person_id AS child_person_id,
         principal.kind AS principal_kind,
         principal.status AS principal_status,
         child.display_name AS child_display_label,
         child.status AS child_status
    INTO e5h_binding
    FROM identity.ha_user_bindings AS binding
    JOIN identity.principals AS principal
      ON principal.principal_id = binding.principal_id
     AND principal.person_id = binding.person_id
    JOIN identity.people AS child
      ON child.person_id = binding.person_id
   WHERE binding.ha_user_id = authenticated_ha_user_id
     AND binding.revoked_at IS NULL;
  IF NOT FOUND
     OR e5h_binding.principal_kind <> 'ha_user'
     OR e5h_binding.principal_status <> 'active'
     OR e5h_binding.child_status <> 'active'
     OR privacy.identity_person_is_blocked(
          e5h_binding.child_person_id
        )
     OR privacy.identity_principal_is_blocked(
          e5h_binding.principal_id
        )
     OR EXISTS (
       SELECT 1
         FROM identity.privacy_directives AS directive
        WHERE directive.person_id = e5h_binding.child_person_id
          AND directive.enabled
          AND directive.directive IN (
            'auto_expire', 'do_not_track', 'ignored', 'silent'
          )
     )
     OR EXISTS (
       SELECT 1
         FROM identity.edge_privacy_user_blocks AS edge_block
        WHERE edge_block.ha_user_id = authenticated_ha_user_id
           OR edge_block.person_id = e5h_binding.child_person_id
     ) THEN
    RAISE EXCEPTION 'parent_relationship_e5h_binding_invalid'
      USING ERRCODE = '42501';
  END IF;

  SELECT pg_catalog.count(*)
    INTO e5h_count
    FROM identity.parent_relationship_proposals AS proposal
   WHERE proposal.principal_id = e5h_binding.principal_id
     AND proposal.child_person_id = e5h_binding.child_person_id
     AND proposal.state IN ('ready', 'consumed');
  IF e5h_count > 1 THEN
    RAISE EXCEPTION 'parent_relationship_e5h_graph_invalid'
      USING ERRCODE = '23514';
  END IF;

  IF e5h_count = 0 THEN
    SELECT pg_catalog.count(*)
      INTO e5h_count
      FROM knowledge.fact_versions AS fact
     WHERE fact.predicate = 'parent_of'
       AND fact.object ->> 'person_id' =
           e5h_binding.child_person_id::text
       AND fact.perspective_principal_id =
           e5h_binding.principal_id
       AND pg_catalog.upper_inf(fact.system_range)
       AND fact.resolution = 'accepted';
    IF e5h_count <> 0 THEN
      RAISE EXCEPTION 'parent_relationship_e5h_unreceipted_facts'
        USING ERRCODE = '23514';
    END IF;
    RETURN QUERY SELECT
      'not_started'::varchar,
      NULL::uuid, NULL::varchar, NULL::timestamptz,
      NULL::varchar, NULL::varchar, NULL::varchar,
      NULL::varchar, NULL::varchar, NULL::timestamptz, 0;
    RETURN;
  END IF;

  SELECT proposal.*
    INTO STRICT e5h_proposal
    FROM identity.parent_relationship_proposals AS proposal
   WHERE proposal.principal_id = e5h_binding.principal_id
     AND proposal.child_person_id = e5h_binding.child_person_id
     AND proposal.state IN ('ready', 'consumed');

  SELECT request.*
    INTO e5h_request
    FROM identity.parent_relationship_requests AS request
   WHERE request.request_id = e5h_proposal.request_id
     AND request.principal_id = e5h_binding.principal_id
     AND request.child_person_id = e5h_binding.child_person_id
     AND request.binding_id = e5h_binding.binding_id;
  IF NOT FOUND
     OR e5h_proposal.contract_version <>
        'parent-relationship-authority-v2'
     OR e5h_proposal.candidate_count <> 2
     OR e5h_proposal.policy_version <> 'home-agent-mvp-v1'
     OR e5h_proposal.policy_digest IS DISTINCT FROM
        e5h_promotion.policy_digest
     OR e5h_request.requested_at IS DISTINCT FROM
        e5h_proposal.staged_at
     OR e5h_request.staged_at IS DISTINCT FROM
        e5h_proposal.staged_at
     OR e5h_request.expires_at IS DISTINCT FROM
        e5h_proposal.expires_at THEN
    RAISE EXCEPTION 'parent_relationship_e5h_graph_invalid'
      USING ERRCODE = '23514';
  END IF;

  IF e5h_proposal.state = 'ready' THEN
    e5h_now := pg_catalog.clock_timestamp();
    IF e5h_proposal.expires_at <= e5h_now THEN
      UPDATE identity.parent_relationship_proposals AS proposal
         SET state = 'expired'
       WHERE proposal.proposal_id = e5h_proposal.proposal_id
         AND proposal.state = 'ready'
         AND proposal.expires_at <= e5h_now;
      GET DIAGNOSTICS e5h_affected = ROW_COUNT;
      IF e5h_affected <> 1 THEN
        RAISE EXCEPTION 'parent_relationship_e5h_expiry_race'
          USING ERRCODE = '40001';
      END IF;
      UPDATE identity.parent_relationship_requests AS request
         SET state = 'expired',
             closed_at = pg_catalog.greatest(
               e5h_now, e5h_request.expires_at
             )
       WHERE request.request_id = e5h_request.request_id
         AND request.state = 'staged'
         AND request.closed_at IS NULL;
      GET DIAGNOSTICS e5h_affected = ROW_COUNT;
      IF e5h_affected <> 1 THEN
        RAISE EXCEPTION 'parent_relationship_e5h_expiry_race'
          USING ERRCODE = '40001';
      END IF;
      RETURN QUERY SELECT
        'not_started'::varchar,
        NULL::uuid, NULL::varchar, NULL::timestamptz,
        NULL::varchar, NULL::varchar, NULL::varchar,
        NULL::varchar, NULL::varchar, NULL::timestamptz, 0;
      RETURN;
    END IF;

    SELECT pg_catalog.array_agg(
             parent.display_name ORDER BY edge.ordinal
           ),
           pg_catalog.array_agg(
             edge.review_code ORDER BY edge.ordinal
           ),
           pg_catalog.count(*)
      INTO e5h_parent_labels, e5h_review_codes, e5h_count
      FROM identity.parent_relationship_proposal_edges AS edge
      JOIN identity.people AS parent
        ON parent.person_id = edge.parent_person_id
      JOIN identity.legacy_role_labels AS legacy
        ON legacy.label_id = edge.legacy_label_id
       AND legacy.person_id = edge.parent_person_id
     WHERE edge.proposal_id = e5h_proposal.proposal_id
       AND edge.child_person_id = e5h_binding.child_person_id
       AND edge.ordinal IN (0, 1)
       AND edge.predicate = 'parent_of'
       AND edge.legacy_role_label = 'parent'
       AND edge.legacy_perspective = 'unknown'
       AND NOT edge.legacy_authoritative
       AND edge.required_authority = 'explicit_related_party'
       AND edge.required_support = 'explicit_authority'
       AND edge.required_contradiction = 'none'
       AND edge.required_freshness = 'not_applicable'
       AND edge.required_coverage = 'not_applicable'
       AND edge.required_resolution = 'accepted'
       AND edge.privacy_scope = 'private'
       AND legacy.role_label = 'parent'
       AND legacy.perspective = 'unknown'
       AND parent.status = 'active'
       AND NOT privacy.identity_person_is_blocked(parent.person_id)
       AND NOT EXISTS (
         SELECT 1
           FROM identity.privacy_directives AS directive
          WHERE directive.person_id = parent.person_id
            AND directive.enabled
            AND directive.directive IN (
              'auto_expire', 'do_not_track', 'ignored', 'silent'
            )
       )
       AND NOT EXISTS (
         SELECT 1
           FROM identity.edge_privacy_user_blocks AS edge_block
          WHERE edge_block.ha_user_id = authenticated_ha_user_id
             OR edge_block.person_id = parent.person_id
       );
    IF e5h_request.state <> 'staged'
       OR e5h_request.closed_at IS NOT NULL
       OR e5h_proposal.consumed_at IS NOT NULL
       OR e5h_proposal.confirmation_artifact_id IS NOT NULL
       OR e5h_proposal.memory_transaction_id IS NOT NULL
       OR e5h_count <> 2
       OR e5h_parent_labels[1] IS NULL
       OR e5h_parent_labels[2] IS NULL
       OR pg_catalog.lower(e5h_parent_labels[1]) =
          pg_catalog.lower(e5h_parent_labels[2])
       OR EXISTS (
         SELECT 1
           FROM knowledge.fact_versions AS fact
          WHERE fact.predicate = 'parent_of'
            AND fact.object ->> 'person_id' =
                e5h_binding.child_person_id::text
            AND fact.perspective_principal_id =
                e5h_binding.principal_id
            AND pg_catalog.upper_inf(fact.system_range)
            AND fact.resolution = 'accepted'
       ) THEN
      RAISE EXCEPTION 'parent_relationship_e5h_graph_invalid'
        USING ERRCODE = '23514';
    END IF;
    RETURN QUERY SELECT
      'ready_for_confirmation'::varchar,
      e5h_proposal.proposal_id,
      e5h_proposal.proposal_digest,
      e5h_proposal.expires_at,
      e5h_binding.child_display_label::varchar,
      e5h_parent_labels[1]::varchar,
      e5h_review_codes[1]::varchar,
      e5h_parent_labels[2]::varchar,
      e5h_review_codes[2]::varchar,
      NULL::timestamptz,
      0;
    RETURN;
  END IF;

  SELECT receipt.committed_at,
         pg_catalog.count(*)
    INTO e5h_confirmed_at, e5h_count
    FROM operations.parent_relationship_authority_receipts AS receipt
    JOIN operations.parent_relationship_authority_receipt_edges
         AS receipt_edge
      ON receipt_edge.receipt_id = receipt.receipt_id
    JOIN identity.parent_relationship_proposal_edges AS proposal_edge
      ON proposal_edge.proposal_edge_id = receipt_edge.proposal_edge_id
     AND proposal_edge.proposal_id = e5h_proposal.proposal_id
     AND proposal_edge.ordinal = receipt_edge.ordinal
    JOIN knowledge.fact_versions AS fact
      ON fact.fact_version_id = receipt_edge.fact_version_id
     AND fact.subject_id = proposal_edge.parent_person_id
    JOIN knowledge.fact_support AS confirmation_support
      ON confirmation_support.support_id =
         receipt_edge.confirmation_support_id
     AND confirmation_support.fact_version_id = fact.fact_version_id
    JOIN knowledge.fact_support AS legacy_support
      ON legacy_support.support_id = receipt_edge.legacy_support_id
     AND legacy_support.fact_version_id = fact.fact_version_id
   WHERE receipt.proposal_id = e5h_proposal.proposal_id
     AND receipt.request_id = e5h_request.request_id
     AND receipt.principal_id = e5h_binding.principal_id
     AND receipt.child_person_id = e5h_binding.child_person_id
     AND receipt.binding_id = e5h_binding.binding_id
     AND receipt.confirmation_artifact_id =
         e5h_proposal.confirmation_artifact_id
     AND receipt.memory_transaction_id =
         e5h_proposal.memory_transaction_id
     AND receipt.contract_version = 'parent-relationship-authority-v2'
     AND receipt.edge_count = 2
     AND receipt.proposal_digest = e5h_proposal.proposal_digest
     AND receipt.policy_version = 'home-agent-mvp-v1'
     AND receipt.policy_digest = e5h_promotion.policy_digest
     AND receipt.authority_result = 'committed'
     AND receipt.committed_at = e5h_proposal.consumed_at
     AND receipt_edge.ordinal IN (0, 1)
     AND fact.subject_type = 'person'
     AND fact.predicate = 'parent_of'
     AND fact.object = pg_catalog.jsonb_build_object(
           'person_id', e5h_binding.child_person_id::text
         )
     AND fact.perspective_principal_id = e5h_binding.principal_id
     AND pg_catalog.upper_inf(fact.system_range)
     AND fact.authority = 'explicit_related_party'
     AND fact.support = 'explicit_authority'
     AND fact.contradiction = 'none'
     AND fact.freshness = 'not_applicable'
     AND fact.coverage = 'not_applicable'
     AND fact.resolution = 'accepted'
     AND fact.privacy_scope = 'private'
     AND fact.memory_transaction_id =
         e5h_proposal.memory_transaction_id
     AND confirmation_support.artifact_id =
         e5h_proposal.confirmation_artifact_id
     AND confirmation_support.dependency_domain =
         'authenticated_confirmation'
     AND confirmation_support.support_role = 'confirmation'
     AND legacy_support.artifact_id = proposal_edge.legacy_label_id
     AND legacy_support.dependency_domain = 'identity_migration'
     AND legacy_support.support_role = 'legacy_context'
   GROUP BY receipt.committed_at;
  IF e5h_proposal.state <> 'consumed'
     OR e5h_request.state <> 'consumed'
     OR e5h_request.closed_at IS DISTINCT FROM
        e5h_proposal.consumed_at
     OR e5h_proposal.consumed_at IS NULL
     OR e5h_count <> 2
     OR e5h_confirmed_at IS DISTINCT FROM
        e5h_proposal.consumed_at THEN
    RAISE EXCEPTION 'parent_relationship_e5h_graph_invalid'
      USING ERRCODE = '23514';
  END IF;
  RETURN QUERY SELECT
    'confirmed'::varchar,
    NULL::uuid, NULL::varchar, NULL::timestamptz,
    NULL::varchar, NULL::varchar, NULL::varchar,
    NULL::varchar, NULL::varchar, e5h_confirmed_at, 2;
END;
"""

FUNCTION_BODY_SHA256 = hashlib.sha256(FUNCTION_BODY.encode("utf-8")).hexdigest()


def _validate_preconditions() -> None:
    op.execute(
        f"""
        DO $parent_relationship_e5h_preconditions$
        DECLARE
          kernel_oid oid;
        BEGIN
          SELECT oid INTO STRICT kernel_oid
            FROM pg_catalog.pg_roles
           WHERE rolname = '{KERNEL_ROLE}';
          IF pg_catalog.to_regprocedure(
               'identity.stage_authenticated_parent_relationship_e5e('
               'character varying,uuid,uuid,uuid,uuid,uuid,'
               'character varying,character varying)'
             ) IS NULL
             OR pg_catalog.to_regprocedure(
               'identity.commit_authenticated_parent_relationship_e5f('
               'character varying,uuid,character varying,uuid,'
               'uuid,uuid,uuid,uuid,uuid,uuid,uuid,uuid,'
               'uuid,uuid,uuid,uuid,uuid)'
             ) IS NULL
             OR pg_catalog.to_regprocedure('{FUNCTION}') IS NOT NULL
             OR pg_catalog.pg_has_role(
                  '{CALLER_ROLE}', '{KERNEL_ROLE}', 'SET'
                ) THEN
            RAISE EXCEPTION 'parent_relationship_e5h_kernel_invalid'
              USING ERRCODE = '42501';
          END IF;
        END
        $parent_relationship_e5h_preconditions$;
        """
    )


def _install_function() -> None:
    revoked = ", ".join(ALL_RUNTIME_ROLES)
    op.execute(
        f"""
        CREATE FUNCTION identity.recover_authenticated_parent_relationship_e5h(
          authenticated_ha_user_id character varying
        )
        RETURNS TABLE (
          state varchar,
          proposal_id uuid,
          proposal_digest varchar,
          expires_at timestamptz,
          child_display_label varchar,
          parent_0_display_label varchar,
          parent_0_review_code varchar,
          parent_1_display_label varchar,
          parent_1_review_code varchar,
          confirmed_at timestamptz,
          fact_count integer
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        SET row_security = on
        AS $parent_relationship_status_e5h${FUNCTION_BODY}$parent_relationship_status_e5h$;

        ALTER FUNCTION {FUNCTION} OWNER TO {KERNEL_ROLE};
        REVOKE ALL PRIVILEGES ON FUNCTION {FUNCTION} FROM {revoked};
        GRANT EXECUTE ON FUNCTION {FUNCTION} TO {CALLER_ROLE};
        """
    )


def _validate_installation() -> None:
    op.execute(
        f"""
        DO $parent_relationship_e5h_validation$
        DECLARE
          function_oid oid;
          kernel_oid oid;
          observed_body_digest text;
        BEGIN
          function_oid := pg_catalog.to_regprocedure('{FUNCTION}');
          SELECT oid INTO STRICT kernel_oid
            FROM pg_catalog.pg_roles
           WHERE rolname = '{KERNEL_ROLE}';
          SELECT pg_catalog.encode(
                   pg_catalog.sha256(
                     pg_catalog.convert_to(prosrc, 'UTF8')
                   ),
                   'hex'
                 )
            INTO STRICT observed_body_digest
            FROM pg_catalog.pg_proc
           WHERE oid = function_oid;
          IF function_oid IS NULL
             OR NOT EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_proc
                WHERE oid = function_oid
                  AND proowner = kernel_oid
                  AND prosecdef
                  AND provolatile = 'v'
                  AND proparallel = 'u'
                  AND proconfig @> ARRAY[
                    'search_path=pg_catalog',
                    'row_security=on'
                  ]::text[]
             )
             OR observed_body_digest <> '{FUNCTION_BODY_SHA256}'
             OR NOT pg_catalog.has_function_privilege(
                  '{CALLER_ROLE}', function_oid, 'EXECUTE'
                )
             OR pg_catalog.has_function_privilege(
                  'home_agent_api', function_oid, 'EXECUTE'
                )
             OR pg_catalog.has_function_privilege(
                  'home_agent_binding_operator',
                  function_oid, 'EXECUTE'
                )
             OR EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_proc AS function_row
                 CROSS JOIN LATERAL pg_catalog.aclexplode(
                   function_row.proacl
                 ) AS privilege_row
                WHERE function_row.oid = function_oid
                  AND privilege_row.grantee = 0
                  AND privilege_row.privilege_type = 'EXECUTE'
             ) THEN
            RAISE EXCEPTION
              'parent_relationship_e5h_installation_invalid'
              USING ERRCODE = '42501';
          END IF;
        END
        $parent_relationship_e5h_validation$;
        """
    )


def upgrade() -> None:
    _validate_preconditions()
    _install_function()
    _validate_installation()


def downgrade() -> None:
    op.execute(f"DROP FUNCTION {FUNCTION}")
