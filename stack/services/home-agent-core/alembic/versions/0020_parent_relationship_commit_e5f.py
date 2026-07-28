"""Install the authenticated, atomic E5f parent relationship commit kernel.

Revision ID: 0020_parent_commit_e5f
Revises: 0019_parent_stage_e5e
Create Date: 2026-07-28

This revision consumes one unexpired E5e two-parent preview and commits both
explicit parent_of facts, their provenance, the authenticated confirmation,
one memory transaction, and one normalized authority receipt in the same
SERIALIZABLE transaction. The caller supplies only opaque identifiers and the
authenticated HA user; every semantic identity is re-derived in PostgreSQL.

No API, BFF route, UI, or production activation is included. Production
remains pinned to revision 0006a and record_only.
"""

from __future__ import annotations

import hashlib
from typing import Sequence

from alembic import op


revision: str = "0020_parent_commit_e5f"
down_revision: str | None = "0019_parent_stage_e5e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


CALLER_ROLE = "home_agent_binding_committer"
KERNEL_ROLE = "home_agent_parent_relationship_kernel"
STAGE_FUNCTION = (
    "identity.stage_authenticated_parent_relationship_e5e("
    "character varying,uuid,uuid,uuid,uuid,uuid,"
    "character varying,character varying)"
)
FUNCTION = (
    "identity.commit_authenticated_parent_relationship_e5f("
    "character varying,uuid,character varying,uuid,"
    "uuid,uuid,uuid,uuid,uuid,uuid,uuid,uuid,"
    "uuid,uuid,uuid,uuid,uuid)"
)
POLICY_PREFIX = "parent_relationship_commit_e5f"
EMPTY_OPEN_PARENT_FACT_SET_DIGEST = (
    "cb991031ceed6e7e1ecf3532118ee2ad24f71bf18602b9e4ef2ace50035ed50e"
)

SELECT_TABLES = (
    "identity.confirmation_artifacts",
    "privacy.artifact_registry",
    "knowledge.memory_transactions",
    "knowledge.fact_support",
    "operations.parent_relationship_authority_receipts",
    "operations.parent_relationship_authority_receipt_edges",
)
INSERT_TABLES = (
    "identity.confirmation_artifacts",
    "privacy.artifact_registry",
    "knowledge.memory_transactions",
    "knowledge.fact_versions",
    "knowledge.fact_support",
    "operations.parent_relationship_authority_receipts",
    "operations.parent_relationship_authority_receipt_edges",
)
UPDATE_TABLES = (
    "identity.parent_relationship_requests",
    "identity.parent_relationship_proposals",
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
  e5f_authority_result varchar(32);
  e5f_promotion record;
  e5f_binding record;
  e5f_proposal identity.parent_relationship_proposals%ROWTYPE;
  e5f_request identity.parent_relationship_requests%ROWTYPE;
  e5f_receipt operations.parent_relationship_authority_receipts%ROWTYPE;
  e5f_edges identity.parent_relationship_proposal_edges[];
  e5f_fact_ids uuid[];
  e5f_fact_version_ids uuid[];
  e5f_confirmation_support_ids uuid[];
  e5f_legacy_support_ids uuid[];
  e5f_receipt_edge_ids uuid[];
  e5f_all_new_ids uuid[];
  e5f_person_snapshot_digest text;
  e5f_role_snapshot_digest text;
  e5f_edge_commitment text;
  e5f_edge_commitments text[] := ARRAY[]::text[];
  e5f_candidate_set_commitment text;
  e5f_expected_proposal_digest text;
  e5f_confirmation_nonce_sha256 text;
  e5f_confirmation_digest text;
  e5f_receipt_commitment text;
  e5f_operation_time timestamptz;
  e5f_database_transaction_id bigint;
  e5f_index integer;
  e5f_count bigint;
  e5f_affected_rows integer;
BEGIN
  IF session_user <> '{CALLER_ROLE}'
     OR current_user <> '{KERNEL_ROLE}'
     OR pg_catalog.pg_has_role(
          session_user, '{KERNEL_ROLE}', 'SET'
        ) THEN
    RAISE EXCEPTION 'parent_relationship_e5f_role_invalid'
      USING ERRCODE = '42501';
  END IF;

  IF pg_catalog.current_setting('transaction_isolation') <> 'serializable'
     OR pg_catalog.current_setting('transaction_read_only') <> 'off'
     OR pg_catalog.pg_is_in_recovery()
     OR pg_catalog.pg_current_xact_id_if_assigned() IS NOT NULL THEN
    RAISE EXCEPTION 'parent_relationship_e5f_transaction_invalid'
      USING ERRCODE = '25000';
  END IF;

  e5f_all_new_ids := ARRAY[
    new_confirmation_artifact_id,
    new_memory_transaction_id,
    new_authority_receipt_id,
    new_fact_id_0,
    new_fact_version_id_0,
    new_confirmation_support_id_0,
    new_legacy_support_id_0,
    new_receipt_edge_id_0,
    new_fact_id_1,
    new_fact_version_id_1,
    new_confirmation_support_id_1,
    new_legacy_support_id_1,
    new_receipt_edge_id_1
  ]::uuid[];
  IF authenticated_ha_user_id IS NULL
     OR authenticated_ha_user_id <> pg_catalog.btrim(authenticated_ha_user_id)
     OR pg_catalog.length(authenticated_ha_user_id) NOT BETWEEN 1 AND 64
     OR target_proposal_id IS NULL
     OR target_proposal_digest IS NULL
     OR target_proposal_digest !~ '^[0-9a-f]{{64}}$'
     OR confirmation_nonce IS NULL
     OR pg_catalog.substring(target_proposal_id::text, 15, 1) <> '7'
     OR pg_catalog.substring(target_proposal_id::text, 20, 1)
        NOT IN ('8','9','a','b')
     OR pg_catalog.substring(confirmation_nonce::text, 15, 1) <> '4'
     OR pg_catalog.substring(confirmation_nonce::text, 20, 1)
        NOT IN ('8','9','a','b')
     OR pg_catalog.array_position(e5f_all_new_ids, NULL) IS NOT NULL
     OR (
       SELECT pg_catalog.count(DISTINCT supplied_id)
         FROM pg_catalog.unnest(
           e5f_all_new_ids || ARRAY[target_proposal_id]::uuid[]
         ) AS supplied(supplied_id)
     ) <> 14
     OR EXISTS (
       SELECT 1
         FROM pg_catalog.unnest(e5f_all_new_ids) AS supplied(supplied_id)
        WHERE pg_catalog.substring(supplied_id::text, 15, 1) <> '7'
           OR pg_catalog.substring(supplied_id::text, 20, 1)
              NOT IN ('8','9','a','b')
     ) THEN
    RAISE EXCEPTION 'parent_relationship_e5f_input_invalid'
      USING ERRCODE = '22023';
  END IF;

  PERFORM pg_catalog.set_config('statement_timeout', '15s', true);
  PERFORM pg_catalog.set_config('lock_timeout', '5s', true);
  PERFORM pg_catalog.set_config(
    'idle_in_transaction_session_timeout', '15s', true
  );
  PERFORM pg_catalog.set_config('transaction_timeout', '30s', true);

  e5f_confirmation_nonce_sha256 := pg_catalog.encode(
    pg_catalog.sha256(pg_catalog.uuid_send(confirmation_nonce)), 'hex'
  );
  e5f_fact_ids := ARRAY[new_fact_id_0, new_fact_id_1]::uuid[];
  e5f_fact_version_ids :=
    ARRAY[new_fact_version_id_0, new_fact_version_id_1]::uuid[];
  e5f_confirmation_support_ids := ARRAY[
    new_confirmation_support_id_0, new_confirmation_support_id_1
  ]::uuid[];
  e5f_legacy_support_ids := ARRAY[
    new_legacy_support_id_0, new_legacy_support_id_1
  ]::uuid[];
  e5f_receipt_edge_ids :=
    ARRAY[new_receipt_edge_id_0, new_receipt_edge_id_1]::uuid[];

  -- This is the first application-data operation. E5a reacquires the same
  -- fence and retains the authority locks until the outer transaction ends.
  PERFORM privacy.lock_identity_semantic_write_fence();

  SELECT proposal.*
    INTO e5f_proposal
    FROM identity.parent_relationship_proposals AS proposal
   WHERE proposal.proposal_id = target_proposal_id
     AND proposal.proposal_digest = target_proposal_digest
   FOR UPDATE OF proposal;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'parent_relationship_e5f_proposal_unavailable'
      USING ERRCODE = 'P0002';
  END IF;

  SELECT request.*
    INTO e5f_request
    FROM identity.parent_relationship_requests AS request
   WHERE request.request_id = e5f_proposal.request_id
     AND request.principal_id = e5f_proposal.principal_id
     AND request.child_person_id = e5f_proposal.child_person_id
   FOR UPDATE OF request;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'parent_relationship_e5f_graph_invalid'
      USING ERRCODE = '23514';
  END IF;

  SELECT promotion.promotion_id,
         promotion.run_id,
         promotion.finalization_id,
         promotion.policy_digest,
         promotion.committed_at
    INTO e5f_promotion
    FROM operations.semantic_authority_promotions AS promotion
   WHERE promotion.authority_scope = 'identity_semantics';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'parent_relationship_e5f_authority_unavailable'
      USING ERRCODE = '55000';
  END IF;

  SELECT operations.evaluate_current_identity_semantic_authority(
           e5f_promotion.promotion_id
         )
    INTO e5f_authority_result;
  IF e5f_authority_result IS DISTINCT FROM
       'current_database_authority' THEN
    RAISE EXCEPTION 'parent_relationship_e5f_authority_not_current'
      USING ERRCODE = '55000';
  END IF;

  SELECT binding.binding_id,
         binding.principal_id,
         binding.person_id AS child_person_id,
         principal.kind AS principal_kind,
         principal.status AS principal_status,
         child.display_name AS child_display_label,
         child.status AS child_status
    INTO e5f_binding
    FROM identity.ha_user_bindings AS binding
    JOIN identity.principals AS principal
      ON principal.principal_id = binding.principal_id
     AND principal.person_id = binding.person_id
    JOIN identity.people AS child
      ON child.person_id = binding.person_id
   WHERE binding.ha_user_id = authenticated_ha_user_id
     AND binding.revoked_at IS NULL;
  IF NOT FOUND
     OR e5f_binding.binding_id IS DISTINCT FROM e5f_request.binding_id
     OR e5f_binding.principal_id IS DISTINCT FROM
        e5f_proposal.principal_id
     OR e5f_binding.child_person_id IS DISTINCT FROM
        e5f_proposal.child_person_id
     OR e5f_binding.principal_kind <> 'ha_user'
     OR e5f_binding.principal_status <> 'active'
     OR e5f_binding.child_status <> 'active'
     OR privacy.identity_person_is_blocked(
          e5f_binding.child_person_id
        )
     OR privacy.identity_principal_is_blocked(
          e5f_binding.principal_id
        )
     OR EXISTS (
       SELECT 1
         FROM identity.privacy_directives AS directive
        WHERE directive.person_id = e5f_binding.child_person_id
          AND directive.enabled
          AND directive.directive IN (
            'auto_expire', 'do_not_track', 'ignored', 'silent'
          )
     )
     OR EXISTS (
       SELECT 1
         FROM identity.edge_privacy_user_blocks AS edge_block
        WHERE edge_block.ha_user_id = authenticated_ha_user_id
           OR edge_block.person_id = e5f_binding.child_person_id
     ) THEN
    RAISE EXCEPTION 'parent_relationship_e5f_binding_invalid'
      USING ERRCODE = '42501';
  END IF;

  SELECT pg_catalog.array_agg(edge ORDER BY edge.ordinal),
         pg_catalog.count(*)
    INTO e5f_edges, e5f_count
    FROM identity.parent_relationship_proposal_edges AS edge
   WHERE edge.proposal_id = e5f_proposal.proposal_id;
  IF e5f_count <> 2
     OR (e5f_edges[1]).ordinal <> 0
     OR (e5f_edges[2]).ordinal <> 1
     OR (e5f_edges[1]).parent_person_id =
        (e5f_edges[2]).parent_person_id
     OR (e5f_edges[1]).legacy_label_id =
        (e5f_edges[2]).legacy_label_id THEN
    RAISE EXCEPTION 'parent_relationship_e5f_graph_invalid'
      USING ERRCODE = '23514';
  END IF;

  FOR e5f_index IN 1..2 LOOP
    SELECT pg_catalog.encode(
             pg_catalog.sha256(
               pg_catalog.convert_to(
                 pg_catalog.jsonb_build_object(
                   'legacy_source_sha256',
                   parent.legacy_source_sha256,
                   'person_id', parent.person_id::text,
                   'privacy_scope', parent.privacy_scope,
                   'status', parent.status,
                   'status_source_sha256',
                   parent.status_source_sha256
                 )::text,
                 'UTF8'
               )
             ),
             'hex'
           ),
           pg_catalog.encode(
             pg_catalog.sha256(
               pg_catalog.convert_to(
                 pg_catalog.jsonb_build_object(
                   'label_id', label.label_id::text,
                   'person_id', label.person_id::text,
                   'perspective', label.perspective,
                   'role_label', label.role_label,
                   'source_snapshot_sha256',
                   label.source_snapshot_sha256
                 )::text,
                 'UTF8'
               )
             ),
             'hex'
           ),
           pg_catalog.count(*)
      INTO e5f_person_snapshot_digest,
           e5f_role_snapshot_digest,
           e5f_count
      FROM identity.people AS parent
      JOIN identity.legacy_role_labels AS label
        ON label.label_id =
           (e5f_edges[e5f_index]).legacy_label_id
       AND label.person_id = parent.person_id
      JOIN operations.reviewed_identity_migration_projection_lineage
           AS lineage
        ON lineage.run_id = e5f_promotion.run_id
       AND lineage.decision_kind = 'legacy_role_candidate'
       AND lineage.projection_table_kind =
           'identity.legacy_role_labels'
       AND lineage.projection_id = label.label_id
      JOIN operations.reviewed_identity_migration_projection_subjects
           AS projection_subject
        ON projection_subject.lineage_id = lineage.lineage_id
       AND projection_subject.person_id = parent.person_id
       AND projection_subject.subject_role = 'primary'
     WHERE parent.person_id =
           (e5f_edges[e5f_index]).parent_person_id
       AND parent.status = 'active'
       AND label.role_label = 'parent'
       AND label.perspective = 'unknown'
       AND parent.person_id <> e5f_binding.child_person_id
       AND NOT privacy.identity_person_is_blocked(parent.person_id)
       AND NOT EXISTS (
         SELECT 1
           FROM identity.privacy_directives AS directive
          WHERE directive.person_id = parent.person_id
            AND directive.enabled
       )
       AND NOT EXISTS (
         SELECT 1
           FROM identity.edge_privacy_user_blocks AS edge_block
          WHERE edge_block.ha_user_id = authenticated_ha_user_id
             OR edge_block.person_id = parent.person_id
       )
     GROUP BY
       parent.person_id,
       parent.legacy_source_sha256,
       parent.privacy_scope,
       parent.status,
       parent.status_source_sha256,
       label.label_id,
       label.person_id,
       label.perspective,
       label.role_label,
       label.source_snapshot_sha256;
    IF NOT FOUND
       OR e5f_count <> 1
       OR (e5f_edges[e5f_index]).child_person_id IS DISTINCT FROM
          e5f_binding.child_person_id
       OR (e5f_edges[e5f_index]).predicate <> 'parent_of'
       OR (e5f_edges[e5f_index]).legacy_role_label <> 'parent'
       OR (e5f_edges[e5f_index]).legacy_perspective <> 'unknown'
       OR (e5f_edges[e5f_index]).legacy_authoritative
       OR (e5f_edges[e5f_index]).required_authority <>
          'explicit_related_party'
       OR (e5f_edges[e5f_index]).required_support <>
          'explicit_authority'
       OR (e5f_edges[e5f_index]).required_contradiction <> 'none'
       OR (e5f_edges[e5f_index]).required_freshness <>
          'not_applicable'
       OR (e5f_edges[e5f_index]).required_coverage <>
          'not_applicable'
       OR (e5f_edges[e5f_index]).required_resolution <> 'accepted'
       OR (e5f_edges[e5f_index]).privacy_scope <> 'private'
       OR (e5f_edges[e5f_index]).person_snapshot_digest
          IS DISTINCT FROM e5f_person_snapshot_digest
       OR (e5f_edges[e5f_index]).role_snapshot_digest
          IS DISTINCT FROM e5f_role_snapshot_digest THEN
      RAISE EXCEPTION 'parent_relationship_e5f_graph_invalid'
        USING ERRCODE = '23514';
    END IF;

    e5f_edge_commitment := pg_catalog.encode(
      pg_catalog.sha256(
        pg_catalog.convert_to(
          pg_catalog.jsonb_build_object(
            'child_person_id',
            e5f_binding.child_person_id::text,
            'contract', 'parent-relationship-authority-v2',
            'legacy_label_id',
            (e5f_edges[e5f_index]).legacy_label_id::text,
            'ordinal', e5f_index - 1,
            'parent_person_id',
            (e5f_edges[e5f_index]).parent_person_id::text,
            'person_snapshot_digest',
            e5f_person_snapshot_digest,
            'predicate', 'parent_of',
            'proposal_edge_id',
            (e5f_edges[e5f_index]).proposal_edge_id::text,
            'review_code',
            (e5f_edges[e5f_index]).review_code,
            'role_snapshot_digest',
            e5f_role_snapshot_digest
          )::text,
          'UTF8'
        )
      ),
      'hex'
    );
    IF (e5f_edges[e5f_index]).edge_commitment IS DISTINCT FROM
         e5f_edge_commitment THEN
      RAISE EXCEPTION 'parent_relationship_e5f_graph_invalid'
        USING ERRCODE = '23514';
    END IF;
    e5f_edge_commitments := pg_catalog.array_append(
      e5f_edge_commitments, e5f_edge_commitment
    );
  END LOOP;

  e5f_candidate_set_commitment := pg_catalog.encode(
    pg_catalog.sha256(
      pg_catalog.convert_to(
        pg_catalog.jsonb_build_object(
          'contract', 'parent-candidate-set-v2',
          'edge_commitments', e5f_edge_commitments
        )::text,
        'UTF8'
      )
    ),
    'hex'
  );
  e5f_expected_proposal_digest := pg_catalog.encode(
    pg_catalog.sha256(
      pg_catalog.convert_to(
        pg_catalog.jsonb_build_object(
          'candidate_count', 2,
          'candidate_set_commitment',
          e5f_candidate_set_commitment,
          'child_person_id',
          e5f_binding.child_person_id::text,
          'contract', 'parent-relationship-authority-v2',
          'expires_at',
          pg_catalog.to_char(
            e5f_proposal.expires_at AT TIME ZONE 'UTC',
            'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
          ),
          'open_parent_fact_set_digest',
          '{EMPTY_OPEN_PARENT_FACT_SET_DIGEST}',
          'operator_request_id',
          e5f_proposal.operator_request_id::text,
          'policy_digest', e5f_promotion.policy_digest,
          'policy_version', 'home-agent-mvp-v1',
          'principal_id', e5f_binding.principal_id::text,
          'proposal_id', e5f_proposal.proposal_id::text,
          'request_id', e5f_request.request_id::text,
          'staged_at',
          pg_catalog.to_char(
            e5f_proposal.staged_at AT TIME ZONE 'UTC',
            'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
          )
        )::text,
        'UTF8'
      )
    ),
    'hex'
  );
  IF e5f_proposal.contract_version <>
       'parent-relationship-authority-v2'
     OR e5f_proposal.candidate_count <> 2
     OR e5f_proposal.candidate_set_commitment IS DISTINCT FROM
        e5f_candidate_set_commitment
     OR e5f_proposal.open_parent_fact_set_digest <>
        '{EMPTY_OPEN_PARENT_FACT_SET_DIGEST}'
     OR e5f_proposal.proposal_digest IS DISTINCT FROM
        e5f_expected_proposal_digest
     OR target_proposal_digest IS DISTINCT FROM
        e5f_expected_proposal_digest
     OR e5f_proposal.policy_version <> 'home-agent-mvp-v1'
     OR e5f_proposal.policy_digest IS DISTINCT FROM
        e5f_promotion.policy_digest
     OR e5f_request.requested_at IS DISTINCT FROM
        e5f_proposal.staged_at
     OR e5f_request.staged_at IS DISTINCT FROM
        e5f_proposal.staged_at
     OR e5f_request.expires_at IS DISTINCT FROM
        e5f_proposal.expires_at
     OR NOT pg_catalog.isfinite(e5f_proposal.staged_at)
     OR NOT pg_catalog.isfinite(e5f_proposal.expires_at)
     OR e5f_proposal.staged_at < e5f_promotion.committed_at
     OR e5f_proposal.expires_at <= e5f_proposal.staged_at THEN
    RAISE EXCEPTION 'parent_relationship_e5f_graph_invalid'
      USING ERRCODE = '23514';
  END IF;

  e5f_confirmation_digest := pg_catalog.encode(
    pg_catalog.sha256(
      pg_catalog.convert_to(
        pg_catalog.jsonb_build_object(
          'client_nonce_sha256', e5f_confirmation_nonce_sha256,
          'contract', 'parent-relationship-confirmation-v1',
          'proposal_digest', e5f_proposal.proposal_digest
        )::text,
        'UTF8'
      )
    ),
    'hex'
  );

  IF e5f_proposal.state = 'consumed' THEN
    SELECT receipt.*
      INTO e5f_receipt
      FROM operations.parent_relationship_authority_receipts AS receipt
     WHERE receipt.proposal_id = e5f_proposal.proposal_id;
    e5f_receipt_commitment := pg_catalog.encode(
      pg_catalog.sha256(
        pg_catalog.convert_to(
          pg_catalog.jsonb_build_object(
            'binding_id', e5f_binding.binding_id::text,
            'child_person_id', e5f_binding.child_person_id::text,
            'confirmation_artifact_id',
            new_confirmation_artifact_id::text,
            'contract', 'parent-relationship-authority-v2',
            'database_transaction_id',
            e5f_receipt.database_transaction_id,
            'fact_version_ids',
            ARRAY[
              new_fact_version_id_0::text,
              new_fact_version_id_1::text
            ],
            'memory_transaction_id',
            new_memory_transaction_id::text,
            'policy_digest', e5f_promotion.policy_digest,
            'principal_id', e5f_binding.principal_id::text,
            'proposal_digest', e5f_proposal.proposal_digest,
            'receipt_id', new_authority_receipt_id::text
          )::text,
          'UTF8'
        )
      ),
      'hex'
    );
    IF e5f_request.state <> 'consumed'
       OR e5f_proposal.consumed_at IS NULL
       OR e5f_request.closed_at IS DISTINCT FROM
          e5f_proposal.consumed_at
       OR e5f_receipt.receipt_id IS DISTINCT FROM
          new_authority_receipt_id
       OR e5f_receipt.request_id IS DISTINCT FROM
          e5f_request.request_id
       OR e5f_receipt.principal_id IS DISTINCT FROM
          e5f_binding.principal_id
       OR e5f_receipt.child_person_id IS DISTINCT FROM
          e5f_binding.child_person_id
       OR e5f_receipt.binding_id IS DISTINCT FROM
          e5f_binding.binding_id
       OR e5f_receipt.confirmation_artifact_id IS DISTINCT FROM
          new_confirmation_artifact_id
       OR e5f_receipt.memory_transaction_id IS DISTINCT FROM
          new_memory_transaction_id
       OR e5f_receipt.contract_version <>
          'parent-relationship-authority-v2'
       OR e5f_receipt.edge_count <> 2
       OR e5f_receipt.proposal_digest IS DISTINCT FROM
          e5f_proposal.proposal_digest
       OR e5f_receipt.policy_version <> 'home-agent-mvp-v1'
       OR e5f_receipt.policy_digest IS DISTINCT FROM
          e5f_promotion.policy_digest
       OR e5f_receipt.authority_result <> 'committed'
       OR e5f_receipt.database_transaction_id <= 0
       OR e5f_receipt.receipt_commitment IS DISTINCT FROM
          e5f_receipt_commitment
       OR e5f_receipt.committed_at IS DISTINCT FROM
          e5f_proposal.consumed_at
       OR e5f_proposal.confirmation_artifact_id IS DISTINCT FROM
          new_confirmation_artifact_id
       OR e5f_proposal.memory_transaction_id IS DISTINCT FROM
          new_memory_transaction_id
       OR NOT EXISTS (
         SELECT 1
           FROM identity.confirmation_artifacts AS artifact
          WHERE artifact.artifact_id = new_confirmation_artifact_id
            AND artifact.principal_id = e5f_binding.principal_id
            AND artifact.purpose = 'parent_relationship.confirm'
            AND artifact.proposal_digest =
                e5f_proposal.proposal_digest
            AND artifact.client_nonce_sha256 =
                e5f_confirmation_nonce_sha256
            AND artifact.issued_at = e5f_receipt.committed_at
            AND artifact.consumed_at = e5f_receipt.committed_at
            AND artifact.expires_at =
                e5f_receipt.committed_at + interval '5 minutes'
       )
       OR NOT EXISTS (
         SELECT 1
           FROM privacy.artifact_registry AS registry
          WHERE registry.artifact_id = new_confirmation_artifact_id
            AND registry.artifact_kind =
                'authenticated_confirmation'
            AND registry.store = 'postgresql'
            AND registry.external_ref IS NULL
            AND registry.content_sha256 =
                e5f_proposal.proposal_digest
            AND registry.owner_principal_id =
                e5f_binding.principal_id
            AND registry.retention_class = 'governed_history'
            AND registry.status = 'active'
       )
       OR NOT EXISTS (
         SELECT 1
           FROM knowledge.memory_transactions AS memory
          WHERE memory.transaction_id = new_memory_transaction_id
            AND memory.principal_id = e5f_binding.principal_id
            AND memory.visit_id IS NULL
            AND memory.kind = 'parent_relationship_confirmation'
            AND memory.state = 'committed'
            AND memory.exact_text_ciphertext IS NULL
            AND memory.exact_text_nonce IS NULL
            AND memory.exact_text_sha256 IS NULL
            AND memory.policy_version = 'home-agent-mvp-v1'
            AND memory.policy_digest = e5f_promotion.policy_digest
            AND memory.confirmation_digest =
                e5f_confirmation_digest
            AND memory.confirmed_at = e5f_receipt.committed_at
            AND memory.candidate = pg_catalog.jsonb_build_object(
                  'contract', 'parent-relationship-authority-v2',
                  'edge_count', 2,
                  'proposal_digest', e5f_proposal.proposal_digest
                )
            AND memory.preview = pg_catalog.jsonb_build_object(
                  'candidate_set_commitment',
                  e5f_proposal.candidate_set_commitment,
                  'contract', 'parent-relationship-preview-v1'
                )
            AND memory.verifier_results =
                pg_catalog.jsonb_build_array(
                  pg_catalog.jsonb_build_object(
                    'result', 'passed',
                    'rule',
                    'authenticated_related_party_confirmation',
                    'rule_version', 'e5f-v1'
                  )
                )
       )
       OR (
         SELECT pg_catalog.count(*)
           FROM operations.parent_relationship_authority_receipt_edges
                AS receipt_edge
           JOIN knowledge.fact_versions AS fact
             ON fact.fact_version_id = receipt_edge.fact_version_id
           JOIN knowledge.fact_support AS confirmation_support
             ON confirmation_support.support_id =
                receipt_edge.confirmation_support_id
           JOIN knowledge.fact_support AS legacy_support
             ON legacy_support.support_id =
                receipt_edge.legacy_support_id
          WHERE receipt_edge.receipt_id = e5f_receipt.receipt_id
            AND receipt_edge.receipt_edge_id =
                e5f_receipt_edge_ids[receipt_edge.ordinal + 1]
            AND receipt_edge.proposal_edge_id =
                (e5f_edges[receipt_edge.ordinal + 1]).proposal_edge_id
            AND fact.fact_version_id =
                e5f_fact_version_ids[receipt_edge.ordinal + 1]
            AND fact.fact_id =
                e5f_fact_ids[receipt_edge.ordinal + 1]
            AND fact.version = 1
            AND fact.subject_type = 'person'
            AND fact.subject_id =
                (e5f_edges[receipt_edge.ordinal + 1]).parent_person_id
            AND fact.predicate = 'parent_of'
            AND fact.object = pg_catalog.jsonb_build_object(
                  'person_id', e5f_binding.child_person_id::text
                )
            AND fact.perspective_principal_id =
                e5f_binding.principal_id
            AND pg_catalog.lower(fact.valid_range) =
                e5f_receipt.committed_at
            AND pg_catalog.lower_inc(fact.valid_range)
            AND pg_catalog.upper_inf(fact.valid_range)
            AND pg_catalog.lower(fact.system_range) =
                e5f_receipt.committed_at
            AND pg_catalog.lower_inc(fact.system_range)
            AND pg_catalog.upper_inf(fact.system_range)
            AND fact.authority = 'explicit_related_party'
            AND fact.support = 'explicit_authority'
            AND fact.contradiction = 'none'
            AND fact.freshness = 'not_applicable'
            AND fact.coverage = 'not_applicable'
            AND fact.resolution = 'accepted'
            AND fact.privacy_scope = 'private'
            AND fact.memory_transaction_id =
                new_memory_transaction_id
            AND fact.committed_at = e5f_receipt.committed_at
            AND confirmation_support.support_id =
                e5f_confirmation_support_ids[
                  receipt_edge.ordinal + 1
                ]
            AND confirmation_support.fact_version_id =
                fact.fact_version_id
            AND confirmation_support.artifact_id =
                new_confirmation_artifact_id
            AND confirmation_support.dependency_domain =
                'authenticated_confirmation'
            AND confirmation_support.support_role = 'confirmation'
            AND confirmation_support.root_observation_id IS NULL
            AND confirmation_support.created_at =
                e5f_receipt.committed_at
            AND legacy_support.support_id =
                e5f_legacy_support_ids[receipt_edge.ordinal + 1]
            AND legacy_support.fact_version_id =
                fact.fact_version_id
            AND legacy_support.artifact_id =
                (e5f_edges[
                  receipt_edge.ordinal + 1
                ]).legacy_label_id
            AND legacy_support.dependency_domain =
                'identity_migration'
            AND legacy_support.support_role = 'legacy_context'
            AND legacy_support.root_observation_id IS NULL
            AND legacy_support.created_at = e5f_receipt.committed_at
       ) <> 2 THEN
      RAISE EXCEPTION 'parent_relationship_e5f_replay_drift'
        USING ERRCODE = '23514';
    END IF;
    RETURN QUERY SELECT
      e5f_receipt.receipt_id,
      e5f_receipt.committed_at;
    RETURN;
  END IF;

  IF e5f_proposal.state <> 'ready'
     OR e5f_request.state <> 'staged'
     OR e5f_proposal.consumed_at IS NOT NULL
     OR e5f_proposal.confirmation_artifact_id IS NOT NULL
     OR e5f_proposal.memory_transaction_id IS NOT NULL
     OR e5f_request.closed_at IS NOT NULL
     OR e5f_proposal.expires_at <= pg_catalog.clock_timestamp()
     OR EXISTS (
       SELECT 1
         FROM knowledge.fact_versions AS fact
        WHERE fact.predicate = 'parent_of'
          AND fact.object ->> 'person_id' =
              e5f_binding.child_person_id::text
          AND fact.perspective_principal_id =
              e5f_binding.principal_id
          AND pg_catalog.upper_inf(fact.system_range)
          AND fact.resolution = 'accepted'
     )
     OR EXISTS (
       SELECT 1
         FROM identity.confirmation_artifacts AS artifact
        WHERE artifact.client_nonce_sha256 =
              e5f_confirmation_nonce_sha256
     ) THEN
    RAISE EXCEPTION 'parent_relationship_e5f_graph_invalid'
      USING ERRCODE = '23514';
  END IF;

  e5f_operation_time := pg_catalog.clock_timestamp();
  e5f_database_transaction_id := pg_catalog.txid_current();
  IF e5f_operation_time >= e5f_proposal.expires_at
     OR NOT pg_catalog.isfinite(e5f_operation_time)
     OR e5f_database_transaction_id <= 0 THEN
    RAISE EXCEPTION 'parent_relationship_e5f_graph_invalid'
      USING ERRCODE = '23514';
  END IF;

  e5f_receipt_commitment := pg_catalog.encode(
    pg_catalog.sha256(
      pg_catalog.convert_to(
        pg_catalog.jsonb_build_object(
          'binding_id', e5f_binding.binding_id::text,
          'child_person_id', e5f_binding.child_person_id::text,
          'confirmation_artifact_id',
          new_confirmation_artifact_id::text,
          'contract', 'parent-relationship-authority-v2',
          'database_transaction_id', e5f_database_transaction_id,
          'fact_version_ids',
          ARRAY[
            new_fact_version_id_0::text,
            new_fact_version_id_1::text
          ],
          'memory_transaction_id',
          new_memory_transaction_id::text,
          'policy_digest', e5f_promotion.policy_digest,
          'principal_id', e5f_binding.principal_id::text,
          'proposal_digest', e5f_proposal.proposal_digest,
          'receipt_id', new_authority_receipt_id::text
        )::text,
        'UTF8'
      )
    ),
    'hex'
  );

  BEGIN
    INSERT INTO identity.confirmation_artifacts (
      artifact_id, principal_id, purpose, proposal_digest,
      client_nonce_sha256, issued_at, expires_at, consumed_at
    ) VALUES (
      new_confirmation_artifact_id, e5f_binding.principal_id,
      'parent_relationship.confirm', e5f_proposal.proposal_digest,
      e5f_confirmation_nonce_sha256, e5f_operation_time,
      e5f_operation_time + interval '5 minutes', e5f_operation_time
    );

    INSERT INTO privacy.artifact_registry (
      artifact_id, artifact_kind, store, external_ref, content_sha256,
      owner_principal_id, retention_class, status, created_at
    ) VALUES (
      new_confirmation_artifact_id, 'authenticated_confirmation',
      'postgresql', NULL, e5f_proposal.proposal_digest,
      e5f_binding.principal_id, 'governed_history', 'active',
      e5f_operation_time
    );

    INSERT INTO knowledge.memory_transactions (
      transaction_id, principal_id, visit_id, kind, state,
      exact_text_ciphertext, exact_text_nonce, exact_text_sha256,
      candidate, preview, verifier_results, policy_version,
      policy_digest, confirmation_digest, confirmed_at,
      created_at, updated_at
    ) VALUES (
      new_memory_transaction_id, e5f_binding.principal_id, NULL,
      'parent_relationship_confirmation', 'committed',
      NULL, NULL, NULL,
      pg_catalog.jsonb_build_object(
        'contract', 'parent-relationship-authority-v2',
        'edge_count', 2,
        'proposal_digest', e5f_proposal.proposal_digest
      ),
      pg_catalog.jsonb_build_object(
        'candidate_set_commitment',
        e5f_proposal.candidate_set_commitment,
        'contract', 'parent-relationship-preview-v1'
      ),
      pg_catalog.jsonb_build_array(
        pg_catalog.jsonb_build_object(
          'result', 'passed',
          'rule', 'authenticated_related_party_confirmation',
          'rule_version', 'e5f-v1'
        )
      ),
      'home-agent-mvp-v1', e5f_promotion.policy_digest,
      e5f_confirmation_digest, e5f_operation_time,
      e5f_operation_time, e5f_operation_time
    );

    FOR e5f_index IN 1..2 LOOP
      INSERT INTO knowledge.fact_versions (
        fact_version_id, fact_id, version, subject_type, subject_id,
        predicate, object, perspective_principal_id, valid_range,
        system_range, authority, support, contradiction, freshness,
        coverage, resolution, privacy_scope, memory_transaction_id,
        committed_at
      ) VALUES (
        e5f_fact_version_ids[e5f_index],
        e5f_fact_ids[e5f_index], 1, 'person',
        (e5f_edges[e5f_index]).parent_person_id, 'parent_of',
        pg_catalog.jsonb_build_object(
          'person_id', e5f_binding.child_person_id::text
        ),
        e5f_binding.principal_id,
        pg_catalog.tstzrange(e5f_operation_time, NULL, '[)'),
        pg_catalog.tstzrange(e5f_operation_time, NULL, '[)'),
        'explicit_related_party', 'explicit_authority', 'none',
        'not_applicable', 'not_applicable', 'accepted', 'private',
        new_memory_transaction_id, e5f_operation_time
      );

      INSERT INTO knowledge.fact_support (
        support_id, fact_version_id, artifact_id,
        root_observation_id, dependency_domain, support_role, created_at
      ) VALUES (
        e5f_confirmation_support_ids[e5f_index],
        e5f_fact_version_ids[e5f_index],
        new_confirmation_artifact_id, NULL,
        'authenticated_confirmation', 'confirmation',
        e5f_operation_time
      ), (
        e5f_legacy_support_ids[e5f_index],
        e5f_fact_version_ids[e5f_index],
        (e5f_edges[e5f_index]).legacy_label_id, NULL,
        'identity_migration', 'legacy_context', e5f_operation_time
      );
    END LOOP;

    UPDATE identity.parent_relationship_proposals AS proposal
       SET state = 'consumed',
           consumed_at = e5f_operation_time,
           confirmation_artifact_id = new_confirmation_artifact_id,
           memory_transaction_id = new_memory_transaction_id
     WHERE proposal.proposal_id = e5f_proposal.proposal_id
       AND proposal.state = 'ready'
       AND proposal.consumed_at IS NULL;
    GET DIAGNOSTICS e5f_affected_rows = ROW_COUNT;
    IF e5f_affected_rows <> 1 THEN
      RAISE EXCEPTION 'parent_relationship_e5f_graph_invalid'
        USING ERRCODE = '23514';
    END IF;

    UPDATE identity.parent_relationship_requests AS request
       SET state = 'consumed', closed_at = e5f_operation_time
     WHERE request.request_id = e5f_request.request_id
       AND request.state = 'staged'
       AND request.closed_at IS NULL;
    GET DIAGNOSTICS e5f_affected_rows = ROW_COUNT;
    IF e5f_affected_rows <> 1 THEN
      RAISE EXCEPTION 'parent_relationship_e5f_graph_invalid'
        USING ERRCODE = '23514';
    END IF;

    INSERT INTO operations.parent_relationship_authority_receipts (
      receipt_id, proposal_id, request_id, principal_id,
      child_person_id, binding_id, confirmation_artifact_id,
      memory_transaction_id, contract_version, edge_count,
      proposal_digest, policy_version, policy_digest,
      authority_result, database_transaction_id,
      receipt_commitment, committed_at
    ) VALUES (
      new_authority_receipt_id, e5f_proposal.proposal_id,
      e5f_request.request_id, e5f_binding.principal_id,
      e5f_binding.child_person_id, e5f_binding.binding_id,
      new_confirmation_artifact_id, new_memory_transaction_id,
      'parent-relationship-authority-v2', 2,
      e5f_proposal.proposal_digest, 'home-agent-mvp-v1',
      e5f_promotion.policy_digest, 'committed',
      e5f_database_transaction_id, e5f_receipt_commitment,
      e5f_operation_time
    );

    FOR e5f_index IN 1..2 LOOP
      INSERT INTO operations.parent_relationship_authority_receipt_edges (
        receipt_edge_id, receipt_id, ordinal, proposal_edge_id,
        fact_version_id, confirmation_support_id, legacy_support_id
      ) VALUES (
        e5f_receipt_edge_ids[e5f_index],
        new_authority_receipt_id, e5f_index - 1,
        (e5f_edges[e5f_index]).proposal_edge_id,
        e5f_fact_version_ids[e5f_index],
        e5f_confirmation_support_ids[e5f_index],
        e5f_legacy_support_ids[e5f_index]
      );
    END LOOP;
  EXCEPTION
    WHEN serialization_failure THEN
      RAISE;
    WHEN integrity_constraint_violation THEN
      RAISE EXCEPTION 'parent_relationship_e5f_graph_invalid'
        USING ERRCODE = '23514';
  END;

  RETURN QUERY SELECT
    new_authority_receipt_id,
    e5f_operation_time;
END;
"""

FUNCTION_BODY_SHA256 = hashlib.sha256(FUNCTION_BODY.encode("utf-8")).hexdigest()


def _predicate() -> str:
    return (
        f"session_user = '{CALLER_ROLE}' "
        f"AND current_user = '{KERNEL_ROLE}' "
        "AND NOT pg_catalog.pg_has_role("
        f"session_user, '{KERNEL_ROLE}', 'SET')"
    )


def _validate_preconditions() -> None:
    op.execute(
        f"""
        DO $parent_relationship_e5f_preconditions$
        DECLARE
          stage_oid oid;
          kernel_oid oid;
        BEGIN
          IF session_user <> 'home_agent_owner'
             OR current_user <> 'home_agent_owner'
             OR (
               SELECT pg_catalog.count(*)
                 FROM public.alembic_version
             ) <> 1
             OR (
               SELECT version_num FROM public.alembic_version
             ) IS DISTINCT FROM '0019_parent_stage_e5e' THEN
            RAISE EXCEPTION 'parent_relationship_e5f_revision_invalid'
              USING ERRCODE = '55000';
          END IF;

          stage_oid := pg_catalog.to_regprocedure('{STAGE_FUNCTION}');
          SELECT oid INTO STRICT kernel_oid
            FROM pg_catalog.pg_roles
           WHERE rolname = '{KERNEL_ROLE}';
          IF stage_oid IS NULL
             OR NOT EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_proc
                WHERE oid = stage_oid
                  AND proowner = kernel_oid
                  AND prosecdef
             )
             OR pg_catalog.to_regprocedure('{FUNCTION}') IS NOT NULL
             OR NOT pg_catalog.has_function_privilege(
                  '{CALLER_ROLE}', stage_oid, 'EXECUTE'
                )
             OR pg_catalog.pg_has_role(
                  '{CALLER_ROLE}', '{KERNEL_ROLE}', 'SET'
                ) THEN
            RAISE EXCEPTION 'parent_relationship_e5f_kernel_invalid'
              USING ERRCODE = '42501';
          END IF;
        END
        $parent_relationship_e5f_preconditions$;
        """
    )


def _install_policies_and_grants() -> None:
    predicate = _predicate()
    for index, table in enumerate(SELECT_TABLES):
        policy = f"{POLICY_PREFIX}_r{index:02d}_select"
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
        op.execute(
            f"CREATE POLICY {policy} ON {table} FOR SELECT "
            f"TO {KERNEL_ROLE} USING ({predicate})"
        )
    for index, table in enumerate(INSERT_TABLES):
        policy = f"{POLICY_PREFIX}_w{index:02d}_insert"
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
        op.execute(
            f"CREATE POLICY {policy} ON {table} FOR INSERT "
            f"TO {KERNEL_ROLE} WITH CHECK ({predicate})"
        )
    for index, table in enumerate(UPDATE_TABLES):
        policy = f"{POLICY_PREFIX}_u{index:02d}_update"
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
        op.execute(
            f"CREATE POLICY {policy} ON {table} FOR UPDATE "
            f"TO {KERNEL_ROLE} USING ({predicate}) "
            f"WITH CHECK ({predicate})"
        )

    op.execute(
        f"""
        GRANT SELECT, INSERT ON identity.confirmation_artifacts
          TO {KERNEL_ROLE};
        GRANT SELECT, INSERT ON privacy.artifact_registry
          TO {KERNEL_ROLE};
        GRANT SELECT, INSERT ON knowledge.memory_transactions
          TO {KERNEL_ROLE};
        GRANT INSERT ON knowledge.fact_versions TO {KERNEL_ROLE};
        GRANT SELECT, INSERT ON knowledge.fact_support TO {KERNEL_ROLE};
        GRANT SELECT, INSERT
          ON operations.parent_relationship_authority_receipts
          TO {KERNEL_ROLE};
        GRANT SELECT, INSERT
          ON operations.parent_relationship_authority_receipt_edges
          TO {KERNEL_ROLE};
        GRANT UPDATE (state, closed_at)
          ON identity.parent_relationship_requests TO {KERNEL_ROLE};
        GRANT UPDATE (
          state, consumed_at, confirmation_artifact_id,
          memory_transaction_id
        ) ON identity.parent_relationship_proposals TO {KERNEL_ROLE};
        """
    )


def _install_function() -> None:
    revoked = ", ".join(ALL_RUNTIME_ROLES)
    op.execute(
        f"""
        CREATE FUNCTION identity.commit_authenticated_parent_relationship_e5f(
          authenticated_ha_user_id character varying,
          target_proposal_id uuid,
          target_proposal_digest character varying,
          confirmation_nonce uuid,
          new_confirmation_artifact_id uuid,
          new_memory_transaction_id uuid,
          new_authority_receipt_id uuid,
          new_fact_id_0 uuid,
          new_fact_version_id_0 uuid,
          new_confirmation_support_id_0 uuid,
          new_legacy_support_id_0 uuid,
          new_receipt_edge_id_0 uuid,
          new_fact_id_1 uuid,
          new_fact_version_id_1 uuid,
          new_confirmation_support_id_1 uuid,
          new_legacy_support_id_1 uuid,
          new_receipt_edge_id_1 uuid
        )
        RETURNS TABLE (
          receipt_id uuid,
          committed_at timestamptz
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        SET row_security = on
        AS $parent_relationship_commit_e5f${FUNCTION_BODY}$parent_relationship_commit_e5f$;

        ALTER FUNCTION {FUNCTION} OWNER TO {KERNEL_ROLE};
        REVOKE ALL PRIVILEGES ON FUNCTION {FUNCTION} FROM {revoked};
        GRANT EXECUTE ON FUNCTION {FUNCTION} TO {CALLER_ROLE};
        """
    )


def _validate_installation() -> None:
    op.execute(
        f"""
        DO $parent_relationship_e5f_validation$
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
                  function_oid,
                  'EXECUTE'
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
              'parent_relationship_e5f_installation_invalid'
              USING ERRCODE = '42501';
          END IF;
        END
        $parent_relationship_e5f_validation$;
        """
    )


def upgrade() -> None:
    _validate_preconditions()
    _install_policies_and_grants()
    _install_function()
    _validate_installation()


def downgrade() -> None:
    op.execute(
        """
        DO $parent_relationship_e5f_nonempty$
        BEGIN
          IF EXISTS (
               SELECT 1
                 FROM operations.parent_relationship_authority_receipts
             )
             OR EXISTS (
               SELECT 1
                 FROM identity.parent_relationship_proposals
                WHERE state = 'consumed'
             ) THEN
            RAISE EXCEPTION
              'refusing to remove E5f with committed parent evidence'
              USING ERRCODE = '2BP01';
          END IF;
        END
        $parent_relationship_e5f_nonempty$;
        """
    )
    op.execute(f"DROP FUNCTION {FUNCTION}")
    for index, table in reversed(tuple(enumerate(UPDATE_TABLES))):
        op.execute(
            f"DROP POLICY IF EXISTS "
            f"{POLICY_PREFIX}_u{index:02d}_update ON {table}"
        )
    for index, table in reversed(tuple(enumerate(INSERT_TABLES))):
        op.execute(
            f"DROP POLICY IF EXISTS "
            f"{POLICY_PREFIX}_w{index:02d}_insert ON {table}"
        )
    for index, table in reversed(tuple(enumerate(SELECT_TABLES))):
        op.execute(
            f"DROP POLICY IF EXISTS "
            f"{POLICY_PREFIX}_r{index:02d}_select ON {table}"
        )
    op.execute(
        f"""
        REVOKE SELECT, INSERT ON identity.confirmation_artifacts
          FROM {KERNEL_ROLE};
        REVOKE SELECT, INSERT ON privacy.artifact_registry
          FROM {KERNEL_ROLE};
        REVOKE SELECT, INSERT ON knowledge.memory_transactions
          FROM {KERNEL_ROLE};
        REVOKE INSERT ON knowledge.fact_versions FROM {KERNEL_ROLE};
        REVOKE SELECT, INSERT ON knowledge.fact_support FROM {KERNEL_ROLE};
        REVOKE SELECT, INSERT
          ON operations.parent_relationship_authority_receipts
          FROM {KERNEL_ROLE};
        REVOKE SELECT, INSERT
          ON operations.parent_relationship_authority_receipt_edges
          FROM {KERNEL_ROLE};
        REVOKE UPDATE ON identity.parent_relationship_requests
          FROM {KERNEL_ROLE};
        REVOKE UPDATE ON identity.parent_relationship_proposals
          FROM {KERNEL_ROLE};
        """
    )
