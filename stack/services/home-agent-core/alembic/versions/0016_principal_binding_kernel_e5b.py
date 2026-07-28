"""Add the dormant E5b authenticated principal-binding commit kernel.

Revision ID: 0016_principal_binding_e5b
Revises: 0015_current_authority_e5a
Create Date: 2026-07-27

E5b is database-only groundwork. Production remains pinned to revision 0006a
and ``record_only``. It adds no API, BFF, UI, Compose command, OAuth adapter,
activation helper, parent fact, memory, place, initiative, camera autonomy, or
physical action.

The function can only consume an already staged proposal while E5a reports
current database authority in the same writable SERIALIZABLE transaction. It
does not prove that a future caller actually observed a Home Assistant OAuth
event; connecting authenticated ``whoami`` state remains the separate E5c
activation boundary.
"""

from __future__ import annotations

import hashlib
from typing import Sequence

from alembic import op


revision: str = "0016_principal_binding_e5b"
down_revision: str | None = "0015_current_authority_e5a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


CALLER_ROLE = "home_agent_binding_operator"
KERNEL_ROLE = "home_agent_identity_binding_kernel"
FUNCTION = (
    "identity.commit_authenticated_principal_binding_e5b("
    "uuid,character varying,character varying,uuid,uuid,uuid,uuid,uuid)"
)
AUTHORITY_FUNCTION = (
    "operations.evaluate_current_identity_semantic_authority(uuid)"
)
AUTHORITY_KERNEL_ROLE = "home_agent_identity_authority_kernel"
ERASURE_KERNEL_ROLE = "home_agent_identity_erasure_kernel"
AUTHORITY_FUNCTION_BODY_SHA256 = (
    "c731c8e56e8aabe9676685b86a6b3961ec83b83cf2b688138cc4d83a5f75585a"
)
FENCE_LOCK_BODY_SHA256 = (
    "fd6919a406140ac5bc82d1d1dededa1397c5e27dc54916fb572ce7bc33ba22a5"
)
FENCE_TRIGGER_BODY_SHA256 = (
    "f78a0f25ca2ea86f286c4bf288a1bcb8889213b0e272d8784852da67b110e69f"
)
PERSON_BLOCK_BODY_SHA256 = (
    "36032e050f94dd376dfffd59a25b54f5d0afa197ac13922156be46df753889ea"
)
PRINCIPAL_BLOCK_BODY_SHA256 = (
    "f5dee7490996d801b0e237cb2fd58d301dd7a7553edc6ff071485b72d278e39f"
)
RECEIPT = "operations.principal_binding_authority_receipts"

SELECT_POLICY = "principal_binding_e5b_select"
INSERT_POLICY = "principal_binding_e5b_insert"
UPDATE_POLICY = "principal_binding_e5b_update"
SUPPRESSION_POLICY = "principal_binding_e5b_suppression"
RECEIPT_OWNER_POLICY = "principal_binding_authority_receipts_owner_select"
RECEIPT_SELECT_POLICY = "principal_binding_authority_receipts_e5b_select"
RECEIPT_INSERT_POLICY = "principal_binding_authority_receipts_e5b_insert"

PEOPLE_FENCE_TRIGGER = "people_principal_binding_e5b_fence"
DIRECTIVES_FENCE_TRIGGER = "privacy_directives_principal_binding_e5b_fence"
EDGE_BLOCK_FENCE_TRIGGER = (
    "edge_privacy_user_blocks_principal_binding_e5b_fence"
)

PROPOSAL_EXACT_CONSTRAINT = (
    "principal_binding_proposals_e5b_exact_authority"
)
CONFIRMATION_EXACT_CONSTRAINT = (
    "confirmation_artifacts_e5b_exact_authority"
)
PROMOTION_EXACT_CONSTRAINT = (
    "semantic_authority_promotions_e5b_exact_authority"
)
LINEAGE_EXACT_CONSTRAINT = (
    "identity_projection_lineage_e5b_exact_person"
)
RECEIPT_BINDING_CONSTRAINT = (
    "principal_binding_receipts_e5b_binding_graph"
)
BINDING_RECEIPT_UNIQUE = "uq_ha_user_bindings_authority_receipt_id"
BINDING_RECEIPT_FK = "fk_ha_user_bindings_e5b_authority_receipt"
GLOBAL_NONCE_INDEX = "uq_confirmation_artifacts_binding_nonce_e5b"

SHARED_POLICY_TABLES: dict[str, tuple[str, ...]] = {
    "identity.principal_binding_requests": ("select", "update"),
    "identity.principal_binding_proposals": ("select", "update"),
    "identity.people": ("select",),
    "identity.principals": ("select", "insert"),
    "identity.confirmation_artifacts": ("select", "insert"),
    "identity.ha_user_bindings": ("select", "insert"),
    "privacy.artifact_registry": ("select", "insert"),
    "operations.semantic_authority_promotions": ("select",),
    "operations.reviewed_identity_migration_projection_lineage": (
        "select",
    ),
    "operations.reviewed_identity_migration_projection_subjects": (
        "select",
    ),
}

SUPPRESSION_EXPRESSIONS = {
    "identity.principal_binding_proposals": (
        "NOT privacy.identity_person_is_blocked(person_id) "
        "AND NOT privacy.identity_principal_is_blocked(result_principal_id)"
    ),
    "identity.people": (
        "NOT privacy.identity_person_is_blocked(person_id)"
    ),
    "identity.principals": (
        "NOT privacy.identity_person_is_blocked(person_id)"
    ),
    "identity.confirmation_artifacts": (
        "NOT privacy.identity_principal_is_blocked(principal_id)"
    ),
    "identity.ha_user_bindings": (
        "NOT privacy.identity_person_is_blocked(person_id) "
        "AND NOT privacy.identity_principal_is_blocked(principal_id)"
    ),
    "privacy.artifact_registry": (
        "NOT privacy.identity_principal_is_blocked(owner_principal_id)"
    ),
}

ALL_REVOKED_ROLES = (
    "PUBLIC",
    "home_agent_owner",
    "home_agent_api",
    CALLER_ROLE,
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
    KERNEL_ROLE,
)

RECEIPT_COLUMNS = (
    "receipt_id",
    "binding_id",
    "proposal_id",
    "operator_request_id",
    "request_id",
    "ha_user_id",
    "person_id",
    "person_snapshot_digest",
    "proposal_digest",
    "stage_receipt_digest",
    "proposal_staged_at",
    "proposal_expires_at",
    "principal_id",
    "principal_kind",
    "confirmation_artifact_id",
    "confirmation_purpose",
    "confirmation_nonce_sha256",
    "confirmation_issued_at",
    "confirmation_consumed_at",
    "confirmation_expires_at",
    "promotion_id",
    "promotion_run_id",
    "promotion_finalization_id",
    "promotion_policy_digest",
    "promotion_committed_at",
    "projection_lineage_id",
    "projection_decision_kind",
    "projection_table_kind",
    "projection_id",
    "projection_subject_role",
    "proposal_contract_version",
    "policy_version",
    "authority_result",
    "database_transaction_id",
    "evaluated_at",
)


FUNCTION_BODY = r"""
DECLARE
  e5b_authority_result varchar(32);
  e5b_promotion record;
  e5b_proposal identity.principal_binding_proposals%ROWTYPE;
  e5b_request record;
  e5b_person record;
  e5b_receipt operations.principal_binding_authority_receipts%ROWTYPE;
  e5b_projection_lineage_id uuid;
  e5b_projection_count bigint;
  e5b_confirmation_nonce_sha256 text;
  e5b_person_material text;
  e5b_stage_material text;
  e5b_proposal_material text;
  e5b_expected_person_snapshot_digest text;
  e5b_expected_stage_receipt_digest text;
  e5b_expected_proposal_digest text;
  e5b_operation_time timestamptz;
  e5b_database_transaction_id bigint;
  e5b_affected_rows integer;
BEGIN
  IF session_user <> 'home_agent_binding_operator'
     OR current_user <> 'home_agent_identity_binding_kernel'
     OR pg_catalog.pg_has_role(
          session_user, 'home_agent_identity_binding_kernel', 'SET'
        ) THEN
    RAISE EXCEPTION 'principal_binding_e5b_role_invalid'
      USING ERRCODE = '42501';
  END IF;
  -- An already-assigned XID proves that the caller performed prior DML or a
  -- row lock. Reject it so staged evidence must cross a committed transaction
  -- boundary before E5a evaluates and this kernel consumes it.
  IF pg_catalog.current_setting('transaction_isolation') <> 'serializable'
     OR pg_catalog.current_setting('transaction_read_only') <> 'off'
     OR pg_catalog.pg_is_in_recovery()
     OR pg_catalog.pg_current_xact_id_if_assigned() IS NOT NULL THEN
    RAISE EXCEPTION 'principal_binding_e5b_transaction_invalid'
      USING ERRCODE = '25000';
  END IF;
  IF target_promotion_id IS NULL
     OR authenticated_ha_user_id IS NULL
     OR pg_catalog.btrim(authenticated_ha_user_id) = ''
     OR target_proposal_digest IS NULL
     OR target_proposal_digest !~ '^[0-9a-f]{64}$'
     OR confirmation_nonce IS NULL
     OR new_authority_receipt_id IS NULL
     OR new_principal_id IS NULL
     OR new_confirmation_artifact_id IS NULL
     OR new_binding_id IS NULL
     OR pg_catalog.substring(target_promotion_id::text, 15, 1) <> '7'
     OR pg_catalog.substring(target_promotion_id::text, 20, 1)
        NOT IN ('8','9','a','b')
     OR pg_catalog.substring(new_authority_receipt_id::text, 15, 1) <> '7'
     OR pg_catalog.substring(new_authority_receipt_id::text, 20, 1)
        NOT IN ('8','9','a','b')
     OR pg_catalog.substring(new_principal_id::text, 15, 1) <> '7'
     OR pg_catalog.substring(new_principal_id::text, 20, 1)
        NOT IN ('8','9','a','b')
     OR pg_catalog.substring(new_confirmation_artifact_id::text, 15, 1)
        <> '7'
     OR pg_catalog.substring(new_confirmation_artifact_id::text, 20, 1)
        NOT IN ('8','9','a','b')
     OR pg_catalog.substring(new_binding_id::text, 15, 1) <> '7'
     OR pg_catalog.substring(new_binding_id::text, 20, 1)
        NOT IN ('8','9','a','b')
     OR pg_catalog.substring(confirmation_nonce::text, 15, 1) <> '4'
     OR pg_catalog.substring(confirmation_nonce::text, 20, 1)
        NOT IN ('8','9','a','b')
     OR (
       SELECT pg_catalog.count(DISTINCT supplied_id)
         FROM pg_catalog.unnest(
           ARRAY[
             new_authority_receipt_id,
             new_principal_id,
             new_confirmation_artifact_id,
             new_binding_id
           ]::uuid[]
         ) AS supplied(supplied_id)
     ) <> 4 THEN
    RAISE EXCEPTION 'principal_binding_e5b_input_invalid'
      USING ERRCODE = '22023';
  END IF;

  PERFORM pg_catalog.set_config('statement_timeout', '15s', true);
  PERFORM pg_catalog.set_config('lock_timeout', '5s', true);
  PERFORM pg_catalog.set_config(
    'idle_in_transaction_session_timeout', '15s', true
  );
  PERFORM pg_catalog.set_config('transaction_timeout', '30s', true);

  e5b_confirmation_nonce_sha256 := pg_catalog.encode(
    pg_catalog.sha256(pg_catalog.uuid_send(confirmation_nonce)), 'hex'
  );

  -- This must remain the first application-data operation. E5a acquires the
  -- shared identity-semantic write fence and all current-authority locks; the
  -- locks remain held through this function's binding write and outer commit.
  SELECT operations.evaluate_current_identity_semantic_authority(
           target_promotion_id
         )
    INTO e5b_authority_result;
  IF e5b_authority_result IS DISTINCT FROM 'current_database_authority' THEN
    RAISE EXCEPTION 'principal_binding_e5b_authority_not_current'
      USING ERRCODE = '55000';
  END IF;

  SELECT promotion.promotion_id,
         promotion.authority_scope,
         promotion.run_id,
         promotion.finalization_id,
         promotion.policy_digest,
         promotion.committed_at
    INTO e5b_promotion
    FROM operations.semantic_authority_promotions AS promotion
   WHERE promotion.promotion_id = target_promotion_id
     AND promotion.authority_scope = 'identity_semantics';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'principal_binding_e5b_graph_invalid'
      USING ERRCODE = '23514';
  END IF;

  SELECT proposal.*
    INTO e5b_proposal
    FROM identity.principal_binding_proposals AS proposal
   WHERE proposal.ha_user_id = authenticated_ha_user_id
     AND proposal.proposal_digest = target_proposal_digest
   FOR UPDATE OF proposal;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'principal_binding_e5b_proposal_unavailable'
      USING ERRCODE = 'P0002';
  END IF;

  SELECT request.request_id,
         request.ha_user_id,
         request.review_code,
         request.state,
         request.expires_at,
         request.staged_at,
         request.closed_at
    INTO e5b_request
    FROM identity.principal_binding_requests AS request
   WHERE request.request_id = e5b_proposal.request_id
     AND request.ha_user_id = authenticated_ha_user_id
   FOR UPDATE OF request;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'principal_binding_e5b_graph_invalid'
      USING ERRCODE = '23514';
  END IF;

  SELECT person.person_id,
         person.display_name,
         person.status,
         person.privacy_scope,
         person.legacy_source_sha256,
         person.status_source_sha256
    INTO e5b_person
    FROM identity.people AS person
   WHERE person.person_id = e5b_proposal.person_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'principal_binding_e5b_graph_invalid'
      USING ERRCODE = '23514';
  END IF;
  -- Deliberately do not tuple-lock identity.people. PostgreSQL row locks
  -- require UPDATE privilege, which this least-authority kernel must not
  -- receive. E5a already holds the global semantic fence, and E5b installs
  -- that fence on every relevant People update, so any concurrent drift is
  -- ordered without widening the kernel's table authority.

  SELECT pg_catalog.count(*),
         (pg_catalog.array_agg(
            lineage.lineage_id ORDER BY lineage.lineage_id
          ))[1]
    INTO e5b_projection_count, e5b_projection_lineage_id
    FROM operations.reviewed_identity_migration_projection_lineage
         AS lineage
    JOIN operations.reviewed_identity_migration_projection_subjects
         AS subject
      ON subject.lineage_id = lineage.lineage_id
     AND subject.person_id = e5b_proposal.person_id
     AND subject.subject_role = 'primary'
   WHERE lineage.run_id = e5b_promotion.run_id
     AND lineage.decision_kind = 'person'
     AND lineage.projection_table_kind = 'identity.people'
     AND lineage.projection_id = e5b_proposal.person_id;
  IF e5b_projection_count <> 1
     OR e5b_projection_lineage_id IS NULL THEN
    RAISE EXCEPTION 'principal_binding_e5b_graph_invalid'
      USING ERRCODE = '23514';
  END IF;

  -- Canonical JSON is assembled from independently JSON-escaped scalar
  -- values in lexicographic key order. This is byte-equivalent to the
  -- Python canonical_json(sort_keys=True,separators=(',',':'),
  -- ensure_ascii=False) contract and deliberately avoids jsonb text spaces.
  e5b_person_material :=
    '{"contract":"principal-binding-person-snapshot-v1"'
    ',"display_name":'
      || pg_catalog.to_jsonb(e5b_person.display_name)::text
    || ',"legacy_source_sha256":'
      || pg_catalog.coalesce(
           pg_catalog.to_jsonb(e5b_person.legacy_source_sha256)::text,
           'null'
         )
    || ',"person_id":'
      || pg_catalog.to_jsonb(e5b_person.person_id::text)::text
    || ',"privacy_scope":'
      || pg_catalog.to_jsonb(e5b_person.privacy_scope)::text
    || ',"status":'
      || pg_catalog.to_jsonb(e5b_person.status)::text
    || ',"status_source_sha256":'
      || pg_catalog.coalesce(
           pg_catalog.to_jsonb(e5b_person.status_source_sha256)::text,
           'null'
         )
    || '}';
  e5b_expected_person_snapshot_digest := pg_catalog.encode(
    pg_catalog.sha256(
      pg_catalog.convert_to(e5b_person_material, 'UTF8')
    ),
    'hex'
  );

  e5b_stage_material :=
    '{"contract":"principal-binding-stage-v1"'
    ',"ha_user_id":'
      || pg_catalog.to_jsonb(authenticated_ha_user_id)::text
    || ',"operator_request_id":'
      || pg_catalog.to_jsonb(e5b_proposal.operator_request_id::text)::text
    || ',"person_id":'
      || pg_catalog.to_jsonb(e5b_proposal.person_id::text)::text
    || ',"person_snapshot_digest":'
      || pg_catalog.to_jsonb(
           e5b_expected_person_snapshot_digest
         )::text
    || ',"policy_digest":'
      || pg_catalog.to_jsonb(e5b_promotion.policy_digest)::text
    || ',"policy_version":"home-agent-mvp-v1"'
    ',"request_id":'
      || pg_catalog.to_jsonb(e5b_request.request_id::text)::text
    || ',"review_code":'
      || pg_catalog.to_jsonb(e5b_request.review_code)::text
    || ',"reviewed_display_label":'
      || pg_catalog.to_jsonb(e5b_proposal.reviewed_display_label)::text
    || '}';
  e5b_expected_stage_receipt_digest := pg_catalog.encode(
    pg_catalog.sha256(
      pg_catalog.convert_to(e5b_stage_material, 'UTF8')
    ),
    'hex'
  );

  e5b_proposal_material :=
    '{"contract":"principal-binding-proposal-v1"'
    ',"expires_at":'
      || pg_catalog.to_jsonb(
           pg_catalog.to_char(
             e5b_proposal.expires_at AT TIME ZONE 'UTC',
             'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
           )
         )::text
    || ',"ha_user_id":'
      || pg_catalog.to_jsonb(authenticated_ha_user_id)::text
    || ',"operator_request_id":'
      || pg_catalog.to_jsonb(e5b_proposal.operator_request_id::text)::text
    || ',"person_id":'
      || pg_catalog.to_jsonb(e5b_proposal.person_id::text)::text
    || ',"person_snapshot_digest":'
      || pg_catalog.to_jsonb(
           e5b_expected_person_snapshot_digest
         )::text
    || ',"policy_digest":'
      || pg_catalog.to_jsonb(e5b_promotion.policy_digest)::text
    || ',"policy_version":"home-agent-mvp-v1"'
    ',"request_id":'
      || pg_catalog.to_jsonb(e5b_request.request_id::text)::text
    || ',"review_code":'
      || pg_catalog.to_jsonb(e5b_request.review_code)::text
    || ',"reviewed_display_label":'
      || pg_catalog.to_jsonb(e5b_proposal.reviewed_display_label)::text
    || ',"stage_receipt_digest":'
      || pg_catalog.to_jsonb(
           e5b_expected_stage_receipt_digest
         )::text
    || ',"staged_at":'
      || pg_catalog.to_jsonb(
           pg_catalog.to_char(
             e5b_proposal.staged_at AT TIME ZONE 'UTC',
             'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
           )
         )::text
    || '}';
  e5b_expected_proposal_digest := pg_catalog.encode(
    pg_catalog.sha256(
      pg_catalog.convert_to(e5b_proposal_material, 'UTF8')
    ),
    'hex'
  );

  IF e5b_proposal.person_snapshot_digest
       IS DISTINCT FROM e5b_expected_person_snapshot_digest
     OR e5b_proposal.stage_receipt_digest
        IS DISTINCT FROM e5b_expected_stage_receipt_digest
     OR e5b_proposal.proposal_digest
        IS DISTINCT FROM e5b_expected_proposal_digest
     OR target_proposal_digest
        IS DISTINCT FROM e5b_expected_proposal_digest
     OR e5b_request.request_id
        IS DISTINCT FROM e5b_proposal.request_id
     OR e5b_request.ha_user_id
        IS DISTINCT FROM e5b_proposal.ha_user_id
     OR e5b_request.staged_at
        IS DISTINCT FROM e5b_proposal.staged_at
     OR e5b_request.expires_at
        IS DISTINCT FROM e5b_proposal.expires_at
     OR NOT pg_catalog.isfinite(e5b_proposal.staged_at)
     OR NOT pg_catalog.isfinite(e5b_proposal.expires_at)
     OR e5b_proposal.staged_at < e5b_promotion.committed_at
     OR e5b_proposal.expires_at <= e5b_proposal.staged_at THEN
    RAISE EXCEPTION 'principal_binding_e5b_graph_invalid'
      USING ERRCODE = '23514';
  END IF;

  IF e5b_person.status <> 'active'
     OR privacy.identity_person_is_blocked(e5b_person.person_id)
     OR EXISTS (
       SELECT 1
         FROM identity.privacy_directives AS directive
        WHERE directive.person_id = e5b_person.person_id
          AND directive.enabled
          AND directive.directive IN (
            'auto_expire', 'do_not_track', 'ignored', 'silent'
          )
     )
     OR EXISTS (
       SELECT 1
         FROM identity.edge_privacy_user_blocks AS edge_block
        WHERE edge_block.ha_user_id = authenticated_ha_user_id
           OR edge_block.person_id = e5b_person.person_id
     ) THEN
    RAISE EXCEPTION 'principal_binding_e5b_privacy_blocked'
      USING ERRCODE = '42501';
  END IF;

  IF e5b_proposal.state = 'consumed' THEN
    IF e5b_request.state <> 'consumed'
       OR e5b_proposal.consumed_at IS NULL
       OR e5b_request.closed_at
          IS DISTINCT FROM e5b_proposal.consumed_at THEN
      RAISE EXCEPTION 'principal_binding_e5b_graph_invalid'
        USING ERRCODE = '23514';
    END IF;

    SELECT receipt.*
      INTO e5b_receipt
      FROM operations.principal_binding_authority_receipts AS receipt
     WHERE receipt.proposal_id = e5b_proposal.proposal_id;
    IF NOT FOUND
       OR e5b_receipt.receipt_id
          IS DISTINCT FROM new_authority_receipt_id
       OR e5b_receipt.principal_id
          IS DISTINCT FROM new_principal_id
       OR e5b_receipt.confirmation_artifact_id
          IS DISTINCT FROM new_confirmation_artifact_id
       OR e5b_receipt.binding_id
          IS DISTINCT FROM new_binding_id
       OR e5b_receipt.ha_user_id
          IS DISTINCT FROM authenticated_ha_user_id
       OR e5b_receipt.person_id
          IS DISTINCT FROM e5b_proposal.person_id
       OR e5b_receipt.person_snapshot_digest
          IS DISTINCT FROM e5b_proposal.person_snapshot_digest
       OR e5b_receipt.proposal_digest
          IS DISTINCT FROM e5b_proposal.proposal_digest
       OR e5b_receipt.stage_receipt_digest
          IS DISTINCT FROM e5b_proposal.stage_receipt_digest
       OR e5b_receipt.principal_id
          IS DISTINCT FROM e5b_proposal.result_principal_id
       OR e5b_receipt.confirmation_artifact_id
          IS DISTINCT FROM e5b_proposal.confirmation_artifact_id
       OR e5b_receipt.confirmation_nonce_sha256
          IS DISTINCT FROM e5b_confirmation_nonce_sha256
       OR e5b_receipt.promotion_id
          IS DISTINCT FROM e5b_promotion.promotion_id
       OR e5b_receipt.promotion_run_id
          IS DISTINCT FROM e5b_promotion.run_id
       OR e5b_receipt.promotion_finalization_id
          IS DISTINCT FROM e5b_promotion.finalization_id
       OR e5b_receipt.promotion_policy_digest
          IS DISTINCT FROM e5b_promotion.policy_digest
       OR e5b_receipt.projection_lineage_id
          IS DISTINCT FROM e5b_projection_lineage_id
       OR e5b_receipt.evaluated_at
          IS DISTINCT FROM e5b_proposal.consumed_at
       OR NOT EXISTS (
         SELECT 1
           FROM identity.principals AS principal
          WHERE principal.principal_id = e5b_receipt.principal_id
            AND principal.person_id = e5b_receipt.person_id
            AND principal.kind = 'ha_user'
            AND principal.status = 'active'
       )
       OR NOT EXISTS (
         SELECT 1
           FROM identity.ha_user_bindings AS binding
          WHERE binding.binding_id = e5b_receipt.binding_id
            AND binding.authority_receipt_id = e5b_receipt.receipt_id
            AND binding.proposal_id = e5b_receipt.proposal_id
            AND binding.ha_user_id = e5b_receipt.ha_user_id
            AND binding.principal_id = e5b_receipt.principal_id
            AND binding.person_id = e5b_receipt.person_id
            AND binding.confirmed_by_principal_id =
                e5b_receipt.principal_id
            AND binding.source_artifact_id =
                e5b_receipt.confirmation_artifact_id
            AND binding.confirmed_at = e5b_receipt.evaluated_at
            AND binding.revoked_at IS NULL
       )
       OR NOT EXISTS (
         SELECT 1
           FROM identity.confirmation_artifacts AS artifact
          WHERE artifact.artifact_id =
                e5b_receipt.confirmation_artifact_id
            AND artifact.principal_id = e5b_receipt.principal_id
            AND artifact.purpose = e5b_receipt.confirmation_purpose
            AND artifact.proposal_digest = e5b_receipt.proposal_digest
            AND artifact.client_nonce_sha256 =
                e5b_receipt.confirmation_nonce_sha256
            AND artifact.issued_at = e5b_receipt.confirmation_issued_at
            AND artifact.consumed_at =
                e5b_receipt.confirmation_consumed_at
            AND artifact.expires_at =
                e5b_receipt.confirmation_expires_at
       )
       OR NOT EXISTS (
         SELECT 1
           FROM privacy.artifact_registry AS registry
          WHERE registry.artifact_id =
                e5b_receipt.confirmation_artifact_id
            AND registry.artifact_kind = 'authenticated_confirmation'
            AND registry.store = 'postgresql'
            AND registry.external_ref IS NULL
            AND registry.content_sha256 = e5b_receipt.proposal_digest
            AND registry.owner_principal_id = e5b_receipt.principal_id
            AND registry.retention_class = 'governed_history'
            AND registry.status = 'active'
       ) THEN
      RAISE EXCEPTION 'principal_binding_e5b_graph_invalid'
        USING ERRCODE = '23514';
    END IF;
    RETURN e5b_receipt.evaluated_at;
  END IF;

  IF e5b_proposal.state <> 'ready'
     OR e5b_request.state <> 'staged'
     OR e5b_proposal.consumed_at IS NOT NULL
     OR e5b_proposal.result_principal_id IS NOT NULL
     OR e5b_proposal.confirmation_artifact_id IS NOT NULL
     OR e5b_request.closed_at IS NOT NULL
     OR e5b_proposal.expires_at <= pg_catalog.clock_timestamp() THEN
    RAISE EXCEPTION 'principal_binding_e5b_graph_invalid'
      USING ERRCODE = '23514';
  END IF;

  -- Predicate-read every conflicting binding. Row-locking would require
  -- UPDATE authority on the binding table, which this kernel must not have.
  -- SERIALIZABLE predicate locking plus both partial unique indexes orders
  -- empty-set insert races; a concurrent revocation can only make this
  -- conservative read reject or produce SQLSTATE 40001.
  PERFORM binding.binding_id
    FROM identity.ha_user_bindings AS binding
   WHERE binding.revoked_at IS NULL
     AND (
       binding.ha_user_id = authenticated_ha_user_id
       OR binding.person_id = e5b_proposal.person_id
     )
   ORDER BY binding.binding_id;
  IF FOUND THEN
    RAISE EXCEPTION 'principal_binding_e5b_graph_invalid'
      USING ERRCODE = '23514';
  END IF;

  IF EXISTS (
    SELECT 1
      FROM identity.confirmation_artifacts AS artifact
     WHERE artifact.client_nonce_sha256 = e5b_confirmation_nonce_sha256
  ) THEN
    RAISE EXCEPTION 'principal_binding_e5b_graph_invalid'
      USING ERRCODE = '23514';
  END IF;

  e5b_operation_time := pg_catalog.clock_timestamp();
  e5b_database_transaction_id := pg_catalog.txid_current();
  IF e5b_operation_time >= e5b_proposal.expires_at
     OR NOT pg_catalog.isfinite(e5b_operation_time)
     OR e5b_database_transaction_id <= 0 THEN
    RAISE EXCEPTION 'principal_binding_e5b_graph_invalid'
      USING ERRCODE = '23514';
  END IF;

  BEGIN
  INSERT INTO identity.principals (
    principal_id, person_id, kind, display_label, status, created_at
  ) VALUES (
    new_principal_id, e5b_proposal.person_id, 'ha_user',
    e5b_proposal.reviewed_display_label, 'active', e5b_operation_time
  );

  INSERT INTO identity.confirmation_artifacts (
    artifact_id, principal_id, purpose, proposal_digest,
    client_nonce_sha256, issued_at, expires_at, consumed_at
  ) VALUES (
    new_confirmation_artifact_id, new_principal_id,
    'ha_user_person_binding.confirm', e5b_proposal.proposal_digest,
    e5b_confirmation_nonce_sha256, e5b_operation_time,
    e5b_operation_time + interval '5 minutes', e5b_operation_time
  );

  INSERT INTO privacy.artifact_registry (
    artifact_id, artifact_kind, store, external_ref, content_sha256,
    owner_principal_id, retention_class, status, created_at
  ) VALUES (
    new_confirmation_artifact_id, 'authenticated_confirmation',
    'postgresql', NULL, e5b_proposal.proposal_digest, new_principal_id,
    'governed_history', 'active', e5b_operation_time
  );

  UPDATE identity.principal_binding_proposals AS proposal
     SET state = 'consumed',
         consumed_at = e5b_operation_time,
         result_principal_id = new_principal_id,
         confirmation_artifact_id = new_confirmation_artifact_id
   WHERE proposal.proposal_id = e5b_proposal.proposal_id
     AND proposal.state = 'ready'
     AND proposal.consumed_at IS NULL;
  GET DIAGNOSTICS e5b_affected_rows = ROW_COUNT;
  IF e5b_affected_rows <> 1 THEN
    RAISE EXCEPTION 'principal_binding_e5b_graph_invalid'
      USING ERRCODE = '23514';
  END IF;

  UPDATE identity.principal_binding_requests AS request
     SET state = 'consumed', closed_at = e5b_operation_time
   WHERE request.request_id = e5b_request.request_id
     AND request.state = 'staged'
     AND request.closed_at IS NULL;
  GET DIAGNOSTICS e5b_affected_rows = ROW_COUNT;
  IF e5b_affected_rows <> 1 THEN
    RAISE EXCEPTION 'principal_binding_e5b_graph_invalid'
      USING ERRCODE = '23514';
  END IF;

  INSERT INTO operations.principal_binding_authority_receipts (
    receipt_id, binding_id, proposal_id, operator_request_id, request_id,
    ha_user_id, person_id, person_snapshot_digest, proposal_digest,
    stage_receipt_digest, proposal_staged_at, proposal_expires_at,
    principal_id, principal_kind, confirmation_artifact_id,
    confirmation_purpose, confirmation_nonce_sha256,
    confirmation_issued_at, confirmation_consumed_at,
    confirmation_expires_at, promotion_id, promotion_run_id,
    promotion_finalization_id, promotion_policy_digest,
    promotion_committed_at, projection_lineage_id,
    projection_decision_kind, projection_table_kind, projection_id,
    projection_subject_role, proposal_contract_version, policy_version,
    authority_result, database_transaction_id, evaluated_at
  ) VALUES (
    new_authority_receipt_id, new_binding_id, e5b_proposal.proposal_id,
    e5b_proposal.operator_request_id, e5b_request.request_id,
    authenticated_ha_user_id, e5b_proposal.person_id,
    e5b_proposal.person_snapshot_digest, e5b_proposal.proposal_digest,
    e5b_proposal.stage_receipt_digest, e5b_proposal.staged_at,
    e5b_proposal.expires_at, new_principal_id, 'ha_user',
    new_confirmation_artifact_id, 'ha_user_person_binding.confirm',
    e5b_confirmation_nonce_sha256, e5b_operation_time,
    e5b_operation_time, e5b_operation_time + interval '5 minutes',
    e5b_promotion.promotion_id, e5b_promotion.run_id,
    e5b_promotion.finalization_id, e5b_promotion.policy_digest,
    e5b_promotion.committed_at, e5b_projection_lineage_id, 'person',
    'identity.people', e5b_proposal.person_id, 'primary',
    'principal-binding-proposal-v1', 'home-agent-mvp-v1',
    'current_database_authority', e5b_database_transaction_id,
    e5b_operation_time
  );

  INSERT INTO identity.ha_user_bindings (
    binding_id, proposal_id, authority_receipt_id, ha_user_id,
    principal_id, person_id, confirmed_by_principal_id, confirmed_at,
    revoked_at, source_artifact_id
  ) VALUES (
    new_binding_id, e5b_proposal.proposal_id,
    new_authority_receipt_id, authenticated_ha_user_id,
    new_principal_id, e5b_proposal.person_id, new_principal_id,
    e5b_operation_time, NULL, new_confirmation_artifact_id
  );

  SET CONSTRAINTS
    identity.validate_principal_binding_graph_principal_binding_requests,
    identity.validate_principal_binding_graph_principal_binding_proposals,
    identity.validate_principal_binding_graph_ha_user_bindings,
    identity.validate_principal_binding_graph_confirmation_artifacts
    IMMEDIATE;
  SET CONSTRAINTS
    identity.validate_principal_binding_graph_principal_binding_requests,
    identity.validate_principal_binding_graph_principal_binding_proposals,
    identity.validate_principal_binding_graph_ha_user_bindings,
    identity.validate_principal_binding_graph_confirmation_artifacts
    DEFERRED;
  EXCEPTION
    WHEN serialization_failure THEN
      RAISE;
    WHEN integrity_constraint_violation THEN
      RAISE EXCEPTION 'principal_binding_e5b_graph_invalid'
        USING ERRCODE = '23514';
  END;

  RETURN e5b_operation_time;
END
"""

FUNCTION_BODY_SHA256 = hashlib.sha256(
    FUNCTION_BODY.encode("utf-8")
).hexdigest()


def _role_predicate() -> str:
    return (
        f"session_user = '{CALLER_ROLE}' "
        f"AND current_user = '{KERNEL_ROLE}' "
        "AND NOT pg_catalog.pg_has_role("
        f"session_user, '{KERNEL_ROLE}', 'SET')"
    )


def _assert_prerequisites() -> None:
    op.execute(
        f"""
        DO $e5b_preconditions$
        DECLARE
          owner_oid oid;
          caller_oid oid;
          kernel_oid oid;
          authority_kernel_oid oid;
          erasure_kernel_oid oid;
          authority_function_oid oid;
          fence_lock_oid oid;
          fence_trigger_oid oid;
          person_block_oid oid;
          principal_block_oid oid;
        BEGIN
          IF current_user <> 'home_agent_owner'
             OR session_user <> 'home_agent_owner' THEN
            RAISE EXCEPTION 'principal_binding_e5b_migration_role_invalid'
              USING ERRCODE = '42501';
          END IF;
          IF (
               SELECT pg_catalog.count(*)
                 FROM public.alembic_version
             ) <> 1
             OR (
               SELECT version_num FROM public.alembic_version
             ) IS DISTINCT FROM '0015_current_authority_e5a' THEN
            RAISE EXCEPTION 'principal_binding_e5b_revision_invalid'
              USING ERRCODE = '55000';
          END IF;

          SELECT oid INTO STRICT owner_oid
            FROM pg_catalog.pg_roles
           WHERE rolname = 'home_agent_owner';
          SELECT oid INTO STRICT caller_oid
            FROM pg_catalog.pg_roles
           WHERE rolname = '{CALLER_ROLE}';
          SELECT oid INTO STRICT kernel_oid
            FROM pg_catalog.pg_roles
           WHERE rolname = '{KERNEL_ROLE}';
          SELECT oid INTO STRICT authority_kernel_oid
            FROM pg_catalog.pg_roles
           WHERE rolname = '{AUTHORITY_KERNEL_ROLE}';
          SELECT oid INTO STRICT erasure_kernel_oid
            FROM pg_catalog.pg_roles
           WHERE rolname = '{ERASURE_KERNEL_ROLE}';
          authority_function_oid :=
            pg_catalog.to_regprocedure('{AUTHORITY_FUNCTION}');
          fence_lock_oid := pg_catalog.to_regprocedure(
            'privacy.lock_identity_semantic_write_fence()'
          );
          fence_trigger_oid := pg_catalog.to_regprocedure(
            'privacy.fence_identity_tombstone_write()'
          );
          person_block_oid := pg_catalog.to_regprocedure(
            'privacy.identity_person_is_blocked(uuid)'
          );
          principal_block_oid := pg_catalog.to_regprocedure(
            'privacy.identity_principal_is_blocked(uuid)'
          );

          -- The authenticated caller is itself part of the authority
          -- boundary. Reassert the E5a role contract rather than assuming a
          -- same-named login retained its locked shape after revision 0015.
          IF NOT EXISTS (
            SELECT 1
              FROM pg_catalog.pg_roles AS caller_role
             WHERE caller_role.oid = caller_oid
               AND caller_role.rolcanlogin
               AND NOT caller_role.rolinherit
               AND NOT caller_role.rolsuper
               AND NOT caller_role.rolcreatedb
               AND NOT caller_role.rolcreaterole
               AND NOT caller_role.rolreplication
               AND NOT caller_role.rolbypassrls
               AND caller_role.rolconnlimit = 8
               AND caller_role.rolvaliduntil IS NULL
               AND caller_role.rolconfig = ARRAY[
                 'statement_timeout=15s',
                 'lock_timeout=5s',
                 'idle_in_transaction_session_timeout=15s',
                 'transaction_timeout=30s'
               ]::text[]
          )
             OR EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_auth_members
                WHERE roleid = caller_oid
                   OR member = caller_oid
             )
             OR EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_db_role_setting
                WHERE setrole = caller_oid
                  AND setdatabase <> 0
             ) THEN
            RAISE EXCEPTION 'principal_binding_e5b_caller_role_invalid'
              USING ERRCODE = '42501';
          END IF;

          IF EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_roles
                WHERE oid = kernel_oid
                  AND (
                    rolcanlogin OR rolinherit OR rolsuper OR rolcreatedb
                    OR rolcreaterole OR rolreplication OR rolbypassrls
                    OR rolconnlimit <> 0
                    OR rolvaliduntil IS NOT NULL
                    OR rolconfig IS NOT NULL
                    OR rolpassword IS NOT NULL
                  )
             )
             OR NOT pg_catalog.pg_has_role(
               'home_agent_owner', '{KERNEL_ROLE}', 'SET'
             )
             OR pg_catalog.pg_has_role(
               '{CALLER_ROLE}', '{KERNEL_ROLE}', 'SET'
             )
             OR (
               SELECT pg_catalog.count(*)
                 FROM pg_catalog.pg_auth_members
                WHERE roleid = kernel_oid
             ) <> 1
             OR NOT EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_auth_members
                WHERE roleid = kernel_oid
                  AND member = owner_oid
                  AND NOT admin_option
                  AND NOT inherit_option
                  AND set_option
             )
             OR EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_auth_members
                WHERE member = kernel_oid
             )
             OR EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_db_role_setting
                WHERE setrole = kernel_oid
             ) THEN
            RAISE EXCEPTION 'principal_binding_e5b_kernel_role_invalid'
              USING ERRCODE = '42501';
          END IF;

          IF EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_shdepend
                WHERE refobjid = kernel_oid
                  AND deptype = 'o'
             )
             OR EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_database AS object_row
                 CROSS JOIN LATERAL
                   pg_catalog.aclexplode(object_row.datacl) AS acl
                WHERE acl.grantee = kernel_oid
             )
             OR EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_namespace AS object_row
                 CROSS JOIN LATERAL
                   pg_catalog.aclexplode(object_row.nspacl) AS acl
                WHERE acl.grantee = kernel_oid
             )
             OR EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_class AS object_row
                 CROSS JOIN LATERAL
                   pg_catalog.aclexplode(object_row.relacl) AS acl
                WHERE acl.grantee = kernel_oid
             )
             OR EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_attribute AS object_row
                 CROSS JOIN LATERAL
                   pg_catalog.aclexplode(object_row.attacl) AS acl
                WHERE acl.grantee = kernel_oid
             )
             OR EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_proc AS object_row
                 CROSS JOIN LATERAL
                   pg_catalog.aclexplode(object_row.proacl) AS acl
                WHERE acl.grantee = kernel_oid
             )
             OR EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_type AS object_row
                 CROSS JOIN LATERAL
                   pg_catalog.aclexplode(object_row.typacl) AS acl
                WHERE acl.grantee = kernel_oid
             )
             OR EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_default_acl AS object_row
                 CROSS JOIN LATERAL
                   pg_catalog.aclexplode(object_row.defaclacl) AS acl
                WHERE acl.grantee = kernel_oid
             )
             OR EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_parameter_acl AS object_row
                 CROSS JOIN LATERAL
                   pg_catalog.aclexplode(object_row.paracl) AS acl
                WHERE acl.grantee = kernel_oid
             ) THEN
            RAISE EXCEPTION 'principal_binding_e5b_pregrant_invalid'
              USING ERRCODE = '42501';
          END IF;

          IF authority_function_oid IS NULL
             OR fence_lock_oid IS NULL
             OR fence_trigger_oid IS NULL
             OR person_block_oid IS NULL
             OR principal_block_oid IS NULL THEN
            RAISE EXCEPTION 'principal_binding_e5b_dependency_missing'
              USING ERRCODE = '55000';
          END IF;

          -- A same-signature replacement is not an admitted dependency.
          -- Pin the full E5a verifier and every transitive privacy/fence
          -- helper whose result or trigger behavior protects this commit.
          IF NOT EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_proc AS function_row
                 JOIN pg_catalog.pg_language AS language_row
                   ON language_row.oid = function_row.prolang
                WHERE function_row.oid = authority_function_oid
                  AND function_row.proowner = authority_kernel_oid
                  AND function_row.prosecdef
                  AND function_row.provolatile = 'v'
                  AND function_row.prokind = 'f'
                  AND NOT function_row.proleakproof
                  AND NOT function_row.proretset
                  AND function_row.prorettype =
                      'pg_catalog.varchar'::regtype
                  AND language_row.lanname = 'plpgsql'
                  AND function_row.proconfig =
                      ARRAY['search_path=pg_catalog, pg_temp']::text[]
                  AND pg_catalog.encode(
                        pg_catalog.sha256(
                          pg_catalog.convert_to(
                            function_row.prosrc, 'UTF8'
                          )
                        ),
                        'hex'
                      ) = '{AUTHORITY_FUNCTION_BODY_SHA256}'
             )
             OR (
               SELECT pg_catalog.count(*)
                 FROM pg_catalog.pg_proc AS function_row
                 CROSS JOIN LATERAL pg_catalog.aclexplode(
                   function_row.proacl
                 ) AS acl
                WHERE function_row.oid = authority_function_oid
                  AND acl.privilege_type = 'EXECUTE'
                  AND acl.grantee = caller_oid
                  AND acl.grantor = authority_kernel_oid
                  AND NOT acl.is_grantable
             ) <> 1
             OR (
               SELECT pg_catalog.count(*)
                 FROM pg_catalog.pg_proc AS function_row
                 CROSS JOIN LATERAL pg_catalog.aclexplode(
                   function_row.proacl
                 ) AS acl
                WHERE function_row.oid = authority_function_oid
                  AND acl.privilege_type = 'EXECUTE'
                  AND acl.grantee = authority_kernel_oid
                  AND acl.grantor = authority_kernel_oid
                  AND NOT acl.is_grantable
             ) <> 1
             OR EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_proc AS function_row
                 CROSS JOIN LATERAL pg_catalog.aclexplode(
                   function_row.proacl
                 ) AS acl
                WHERE function_row.oid = authority_function_oid
                  AND acl.privilege_type = 'EXECUTE'
                  AND (
                    acl.grantee NOT IN (
                      caller_oid, authority_kernel_oid
                    )
                    OR acl.grantor <> authority_kernel_oid
                    OR acl.is_grantable
                  )
             ) THEN
            RAISE EXCEPTION
              'principal_binding_e5b_authority_dependency_invalid'
              USING ERRCODE = '55000';
          END IF;

          IF (
               SELECT pg_catalog.count(*)
                 FROM (
                   VALUES
                     (
                       fence_lock_oid,
                       owner_oid,
                       'pg_catalog.void'::regtype,
                       'plpgsql'::name,
                       'v'::"char",
                       ARRAY[
                         'search_path=pg_catalog, pg_temp'
                       ]::text[],
                       '{FENCE_LOCK_BODY_SHA256}'::text
                     ),
                     (
                       fence_trigger_oid,
                       owner_oid,
                       'pg_catalog.trigger'::regtype,
                       'plpgsql'::name,
                       'v'::"char",
                       ARRAY[
                         'search_path=pg_catalog, pg_temp'
                       ]::text[],
                       '{FENCE_TRIGGER_BODY_SHA256}'::text
                     ),
                     (
                       person_block_oid,
                       erasure_kernel_oid,
                       'pg_catalog.boolean'::regtype,
                       'sql'::name,
                       's'::"char",
                       ARRAY['search_path=pg_catalog']::text[],
                       '{PERSON_BLOCK_BODY_SHA256}'::text
                     ),
                     (
                       principal_block_oid,
                       erasure_kernel_oid,
                       'pg_catalog.boolean'::regtype,
                       'sql'::name,
                       's'::"char",
                       ARRAY['search_path=pg_catalog']::text[],
                       '{PRINCIPAL_BLOCK_BODY_SHA256}'::text
                     )
                 ) AS expected(
                   function_oid, function_owner, return_type,
                   language_name, volatility, settings, body_digest
                 )
                 JOIN pg_catalog.pg_proc AS installed
                   ON installed.oid = expected.function_oid
                 JOIN pg_catalog.pg_language AS function_language
                   ON function_language.oid = installed.prolang
                WHERE installed.proowner = expected.function_owner
                  AND installed.prosecdef
                  AND installed.prokind = 'f'
                  AND NOT installed.proleakproof
                  AND NOT installed.proretset
                  AND installed.prorettype = expected.return_type
                  AND installed.provolatile = expected.volatility
                  AND installed.proconfig IS NOT DISTINCT FROM
                      expected.settings
                  AND function_language.lanname =
                      expected.language_name
                  AND pg_catalog.encode(
                        pg_catalog.sha256(
                          pg_catalog.convert_to(
                            installed.prosrc, 'UTF8'
                          )
                        ),
                        'hex'
                      ) = expected.body_digest
             ) <> 4 THEN
            RAISE EXCEPTION
              'principal_binding_e5b_privacy_dependency_invalid'
              USING ERRCODE = '55000';
          END IF;

          IF pg_catalog.to_regclass('{RECEIPT}') IS NOT NULL
             OR pg_catalog.to_regprocedure('{FUNCTION}') IS NOT NULL
             OR EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_attribute
                WHERE attrelid = 'identity.ha_user_bindings'::regclass
                  AND attname = 'authority_receipt_id'
                  AND attnum > 0
                  AND NOT attisdropped
             ) THEN
            RAISE EXCEPTION 'principal_binding_e5b_already_installed'
              USING ERRCODE = '55000';
          END IF;

          -- A confirmation nonce is globally single-use across every purpose.
          -- Existing cross-principal/cross-purpose duplicates require review
          -- before the global replay boundary can be installed.
          IF EXISTS (
            SELECT artifact.client_nonce_sha256
              FROM identity.confirmation_artifacts AS artifact
             GROUP BY artifact.client_nonce_sha256
            HAVING pg_catalog.count(*) > 1
          ) THEN
            RAISE EXCEPTION
              'principal_binding_e5b_existing_nonce_reuse_requires_review'
              USING ERRCODE = '23514';
          END IF;

          -- Never fabricate E5b authority for a pre-existing live binding or
          -- binding confirmation gesture. Staged proposals and distinct
          -- confirmation artifacts for other purposes are intentionally
          -- allowed.
          IF EXISTS (SELECT 1 FROM identity.ha_user_bindings)
             OR EXISTS (
               SELECT 1
                 FROM identity.confirmation_artifacts
                WHERE purpose = 'ha_user_person_binding.confirm'
             ) THEN
            RAISE EXCEPTION
              'principal_binding_e5b_existing_authority_requires_review'
              USING ERRCODE = '23514';
          END IF;
        END
        $e5b_preconditions$
        """
    )


def _install_support_graph() -> None:
    op.execute(
        f"""
        ALTER TABLE identity.principal_binding_proposals
          ADD CONSTRAINT {PROPOSAL_EXACT_CONSTRAINT} UNIQUE (
            proposal_id, operator_request_id, request_id, ha_user_id,
            person_id, person_snapshot_digest, proposal_digest,
            stage_receipt_digest, staged_at, expires_at,
            result_principal_id, confirmation_artifact_id, consumed_at
          );
        ALTER TABLE identity.confirmation_artifacts
          ADD CONSTRAINT {CONFIRMATION_EXACT_CONSTRAINT} UNIQUE (
            artifact_id, principal_id, purpose, proposal_digest,
            client_nonce_sha256, issued_at, consumed_at, expires_at
          );
        ALTER TABLE operations.semantic_authority_promotions
          ADD CONSTRAINT {PROMOTION_EXACT_CONSTRAINT} UNIQUE (
            promotion_id, run_id, finalization_id, policy_digest, committed_at
          );
        ALTER TABLE
          operations.reviewed_identity_migration_projection_lineage
          ADD CONSTRAINT {LINEAGE_EXACT_CONSTRAINT} UNIQUE (
            run_id, lineage_id, decision_kind, projection_table_kind,
            projection_id
        );
        CREATE UNIQUE INDEX {GLOBAL_NONCE_INDEX}
          ON identity.confirmation_artifacts (client_nonce_sha256);
        """
    )

    op.execute(
        f"""
        CREATE TABLE {RECEIPT} (
          receipt_id uuid NOT NULL,
          binding_id uuid NOT NULL,
          proposal_id uuid NOT NULL,
          operator_request_id uuid NOT NULL,
          request_id uuid NOT NULL,
          ha_user_id varchar(64) NOT NULL,
          person_id uuid NOT NULL,
          person_snapshot_digest varchar(64) NOT NULL,
          proposal_digest varchar(64) NOT NULL,
          stage_receipt_digest varchar(64) NOT NULL,
          proposal_staged_at timestamptz NOT NULL,
          proposal_expires_at timestamptz NOT NULL,
          principal_id uuid NOT NULL,
          principal_kind varchar(32) NOT NULL,
          confirmation_artifact_id uuid NOT NULL,
          confirmation_purpose varchar(128) NOT NULL,
          confirmation_nonce_sha256 varchar(64) NOT NULL,
          confirmation_issued_at timestamptz NOT NULL,
          confirmation_consumed_at timestamptz NOT NULL,
          confirmation_expires_at timestamptz NOT NULL,
          promotion_id uuid NOT NULL,
          promotion_run_id uuid NOT NULL,
          promotion_finalization_id uuid NOT NULL,
          promotion_policy_digest varchar(64) NOT NULL,
          promotion_committed_at timestamptz NOT NULL,
          projection_lineage_id uuid NOT NULL,
          projection_decision_kind varchar(48) NOT NULL,
          projection_table_kind varchar(64) NOT NULL,
          projection_id uuid NOT NULL,
          projection_subject_role varchar(16) NOT NULL,
          proposal_contract_version varchar(64) NOT NULL,
          policy_version varchar(128) NOT NULL,
          authority_result varchar(32) NOT NULL,
          database_transaction_id bigint NOT NULL,
          evaluated_at timestamptz NOT NULL,
          CONSTRAINT pk_principal_binding_authority_receipts
            PRIMARY KEY (receipt_id),
          CONSTRAINT uq_principal_binding_authority_receipts_binding
            UNIQUE (binding_id),
          CONSTRAINT uq_principal_binding_authority_receipts_proposal
            UNIQUE (proposal_id),
          CONSTRAINT uq_principal_binding_authority_receipts_principal
            UNIQUE (principal_id),
          CONSTRAINT uq_principal_binding_authority_receipts_confirmation
            UNIQUE (confirmation_artifact_id),
          CONSTRAINT {RECEIPT_BINDING_CONSTRAINT} UNIQUE (
            receipt_id, binding_id, proposal_id, ha_user_id,
            principal_id, person_id, confirmation_artifact_id, evaluated_at
          ),
          CONSTRAINT ck_principal_binding_authority_receipts_uuidv7_ids
            CHECK (
              substring(receipt_id::text from 15 for 1) = '7'
              AND substring(receipt_id::text from 20 for 1)
                  IN ('8','9','a','b')
              AND substring(binding_id::text from 15 for 1) = '7'
              AND substring(binding_id::text from 20 for 1)
                  IN ('8','9','a','b')
              AND substring(principal_id::text from 15 for 1) = '7'
              AND substring(principal_id::text from 20 for 1)
                  IN ('8','9','a','b')
              AND substring(
                    confirmation_artifact_id::text from 15 for 1
                  ) = '7'
              AND substring(
                    confirmation_artifact_id::text from 20 for 1
                  ) IN ('8','9','a','b')
            ),
          CONSTRAINT ck_principal_binding_authority_receipts_digests
            CHECK (
              person_snapshot_digest ~ '^[0-9a-f]{{64}}$'
              AND proposal_digest ~ '^[0-9a-f]{{64}}$'
              AND stage_receipt_digest ~ '^[0-9a-f]{{64}}$'
              AND confirmation_nonce_sha256 ~ '^[0-9a-f]{{64}}$'
              AND promotion_policy_digest ~ '^[0-9a-f]{{64}}$'
            ),
          CONSTRAINT ck_principal_binding_authority_receipts_fixed_contract
            CHECK (
              btrim(ha_user_id) <> ''
              AND principal_kind = 'ha_user'
              AND confirmation_purpose =
                  'ha_user_person_binding.confirm'
              AND projection_decision_kind = 'person'
              AND projection_table_kind = 'identity.people'
              AND projection_id = person_id
              AND projection_subject_role = 'primary'
              AND proposal_contract_version =
                  'principal-binding-proposal-v1'
              AND policy_version = 'home-agent-mvp-v1'
              AND authority_result = 'current_database_authority'
            ),
          CONSTRAINT ck_principal_binding_authority_receipts_time
            CHECK (
              database_transaction_id > 0
              AND isfinite(promotion_committed_at)
              AND isfinite(proposal_staged_at)
              AND isfinite(proposal_expires_at)
              AND isfinite(confirmation_issued_at)
              AND isfinite(confirmation_consumed_at)
              AND isfinite(confirmation_expires_at)
              AND isfinite(evaluated_at)
              AND promotion_committed_at <= proposal_staged_at
              AND proposal_staged_at <= evaluated_at
              AND evaluated_at < proposal_expires_at
              AND confirmation_issued_at = evaluated_at
              AND confirmation_consumed_at = evaluated_at
              AND confirmation_expires_at =
                  evaluated_at + interval '5 minutes'
            ),
          CONSTRAINT fk_principal_binding_authority_receipts_request
            FOREIGN KEY (request_id, ha_user_id)
            REFERENCES identity.principal_binding_requests
              (request_id, ha_user_id),
          CONSTRAINT fk_principal_binding_authority_receipts_proposal
            FOREIGN KEY (
              proposal_id, operator_request_id, request_id, ha_user_id,
              person_id, person_snapshot_digest, proposal_digest,
              stage_receipt_digest, proposal_staged_at, proposal_expires_at,
              principal_id, confirmation_artifact_id, evaluated_at
            )
            REFERENCES identity.principal_binding_proposals (
              proposal_id, operator_request_id, request_id, ha_user_id,
              person_id, person_snapshot_digest, proposal_digest,
              stage_receipt_digest, staged_at, expires_at,
              result_principal_id, confirmation_artifact_id, consumed_at
            ),
          CONSTRAINT fk_principal_binding_authority_receipts_principal
            FOREIGN KEY (principal_id, person_id, principal_kind)
            REFERENCES identity.principals
              (principal_id, person_id, kind),
          CONSTRAINT fk_principal_binding_authority_receipts_confirmation
            FOREIGN KEY (
              confirmation_artifact_id, principal_id,
              confirmation_purpose, proposal_digest,
              confirmation_nonce_sha256, confirmation_issued_at,
              confirmation_consumed_at, confirmation_expires_at
            )
            REFERENCES identity.confirmation_artifacts (
              artifact_id, principal_id, purpose, proposal_digest,
              client_nonce_sha256, issued_at, consumed_at, expires_at
            ),
          CONSTRAINT fk_principal_binding_authority_receipts_promotion
            FOREIGN KEY (
              promotion_id, promotion_run_id, promotion_finalization_id,
              promotion_policy_digest, promotion_committed_at
            )
            REFERENCES operations.semantic_authority_promotions (
              promotion_id, run_id, finalization_id, policy_digest,
              committed_at
            ),
          CONSTRAINT fk_principal_binding_authority_receipts_lineage
            FOREIGN KEY (
              promotion_run_id, projection_lineage_id,
              projection_decision_kind, projection_table_kind, projection_id
            )
            REFERENCES
              operations.reviewed_identity_migration_projection_lineage (
                run_id, lineage_id, decision_kind, projection_table_kind,
                projection_id
              ),
          CONSTRAINT fk_principal_binding_authority_receipts_subject
            FOREIGN KEY (
              projection_lineage_id, person_id, projection_subject_role
            )
            REFERENCES
              operations.reviewed_identity_migration_projection_subjects (
                lineage_id, person_id, subject_role
              )
        );

        ALTER TABLE identity.ha_user_bindings
          ADD COLUMN authority_receipt_id uuid NOT NULL;
        ALTER TABLE identity.ha_user_bindings
          ADD CONSTRAINT {BINDING_RECEIPT_UNIQUE}
          UNIQUE (authority_receipt_id);
        ALTER TABLE identity.ha_user_bindings
          ADD CONSTRAINT {BINDING_RECEIPT_FK}
          FOREIGN KEY (
            authority_receipt_id, binding_id, proposal_id, ha_user_id,
            principal_id, person_id, source_artifact_id, confirmed_at
          )
          REFERENCES {RECEIPT} (
            receipt_id, binding_id, proposal_id, ha_user_id,
            principal_id, person_id, confirmation_artifact_id, evaluated_at
          );
        """
    )


def _install_policies_and_grants() -> None:
    predicate = _role_predicate()
    op.execute(
        f"""
        ALTER TABLE {RECEIPT} ENABLE ROW LEVEL SECURITY;
        ALTER TABLE {RECEIPT} FORCE ROW LEVEL SECURITY;
        REVOKE ALL PRIVILEGES ON TABLE {RECEIPT}
          FROM {", ".join(ALL_REVOKED_ROLES)};
        CREATE POLICY {RECEIPT_OWNER_POLICY}
          ON {RECEIPT} FOR SELECT TO home_agent_owner
          USING (
            session_user = 'home_agent_owner'
            AND current_user = 'home_agent_owner'
          );
        CREATE POLICY {RECEIPT_SELECT_POLICY}
          ON {RECEIPT} FOR SELECT TO {KERNEL_ROLE}
          USING ({predicate});
        CREATE POLICY {RECEIPT_INSERT_POLICY}
          ON {RECEIPT} FOR INSERT TO {KERNEL_ROLE}
          WITH CHECK ({predicate});
        """
    )

    for table, commands in SHARED_POLICY_TABLES.items():
        if "select" in commands:
            op.execute(
                f"CREATE POLICY {SELECT_POLICY} ON {table} FOR SELECT "
                f"TO {KERNEL_ROLE} USING ({predicate})"
            )
        if "insert" in commands:
            op.execute(
                f"CREATE POLICY {INSERT_POLICY} ON {table} FOR INSERT "
                f"TO {KERNEL_ROLE} WITH CHECK ({predicate})"
            )
        if "update" in commands:
            op.execute(
                f"CREATE POLICY {UPDATE_POLICY} ON {table} FOR UPDATE "
                f"TO {KERNEL_ROLE} USING ({predicate}) "
                f"WITH CHECK ({predicate})"
            )

    for table, expression in SUPPRESSION_EXPRESSIONS.items():
        op.execute(
            f"CREATE POLICY {SUPPRESSION_POLICY} ON {table} "
            f"AS RESTRICTIVE FOR ALL TO {KERNEL_ROLE} "
            f"USING ({expression}) WITH CHECK ({expression})"
        )
    op.execute(
        f"""
        CREATE POLICY {SUPPRESSION_POLICY}
          ON {RECEIPT} AS RESTRICTIVE FOR ALL TO {KERNEL_ROLE}
          USING (
            NOT privacy.identity_person_is_blocked(person_id)
            AND NOT privacy.identity_principal_is_blocked(principal_id)
          )
          WITH CHECK (
            NOT privacy.identity_person_is_blocked(person_id)
            AND NOT privacy.identity_principal_is_blocked(principal_id)
          );

        GRANT USAGE ON SCHEMA identity, operations, privacy
          TO {KERNEL_ROLE};

        GRANT SELECT (
          request_id, ha_user_id, review_code, state, expires_at,
          staged_at, closed_at
        ) ON identity.principal_binding_requests TO {KERNEL_ROLE};
        GRANT UPDATE (state, closed_at)
          ON identity.principal_binding_requests TO {KERNEL_ROLE};

        GRANT SELECT (
          proposal_id, operator_request_id, request_id, ha_user_id,
          person_id, reviewed_display_label, person_snapshot_digest,
          proposal_digest, state, stage_receipt_digest, staged_at,
          expires_at, consumed_at, result_principal_id,
          confirmation_artifact_id
        ) ON identity.principal_binding_proposals TO {KERNEL_ROLE};
        GRANT UPDATE (
          state, consumed_at, result_principal_id, confirmation_artifact_id
        ) ON identity.principal_binding_proposals TO {KERNEL_ROLE};

        GRANT SELECT (
          person_id, display_name, status, privacy_scope,
          legacy_source_sha256, status_source_sha256
        ) ON identity.people TO {KERNEL_ROLE};

        GRANT SELECT (principal_id, person_id, kind, status)
          ON identity.principals TO {KERNEL_ROLE};
        GRANT INSERT (
          principal_id, person_id, kind, display_label, status, created_at
        ) ON identity.principals TO {KERNEL_ROLE};

        GRANT SELECT (
          artifact_id, principal_id, purpose, proposal_digest,
          client_nonce_sha256, issued_at, expires_at, consumed_at
        ) ON identity.confirmation_artifacts TO {KERNEL_ROLE};
        GRANT INSERT (
          artifact_id, principal_id, purpose, proposal_digest,
          client_nonce_sha256, issued_at, expires_at, consumed_at
        ) ON identity.confirmation_artifacts TO {KERNEL_ROLE};

        GRANT SELECT (
          binding_id, proposal_id, authority_receipt_id, ha_user_id,
          principal_id, person_id, confirmed_by_principal_id, confirmed_at,
          revoked_at, source_artifact_id
        ) ON identity.ha_user_bindings TO {KERNEL_ROLE};
        GRANT INSERT (
          binding_id, proposal_id, authority_receipt_id, ha_user_id,
          principal_id, person_id, confirmed_by_principal_id, confirmed_at,
          revoked_at, source_artifact_id
        ) ON identity.ha_user_bindings TO {KERNEL_ROLE};

        GRANT SELECT (
          artifact_id, artifact_kind, store, external_ref, content_sha256,
          owner_principal_id, retention_class, status, created_at
        ) ON privacy.artifact_registry TO {KERNEL_ROLE};
        GRANT INSERT (
          artifact_id, artifact_kind, store, external_ref, content_sha256,
          owner_principal_id, retention_class, status, created_at
        ) ON privacy.artifact_registry TO {KERNEL_ROLE};

        GRANT SELECT (
          promotion_id, authority_scope, run_id, finalization_id,
          policy_digest, committed_at
        ) ON operations.semantic_authority_promotions TO {KERNEL_ROLE};
        GRANT SELECT (
          lineage_id, run_id, decision_kind, projection_table_kind,
          projection_id
        ) ON operations.reviewed_identity_migration_projection_lineage
          TO {KERNEL_ROLE};
        GRANT SELECT (lineage_id, person_id, subject_role)
          ON operations.reviewed_identity_migration_projection_subjects
          TO {KERNEL_ROLE};
        GRANT SELECT (person_id, directive, enabled)
          ON identity.privacy_directives TO {KERNEL_ROLE};
        GRANT SELECT (ha_user_id, person_id)
          ON identity.edge_privacy_user_blocks TO {KERNEL_ROLE};
        GRANT SELECT ({", ".join(RECEIPT_COLUMNS)})
          ON {RECEIPT} TO {KERNEL_ROLE};
        GRANT INSERT ({", ".join(RECEIPT_COLUMNS)})
          ON {RECEIPT} TO {KERNEL_ROLE};
        GRANT SELECT ON TABLE {RECEIPT} TO home_agent_owner;

        GRANT EXECUTE ON FUNCTION
          {AUTHORITY_FUNCTION},
          privacy.identity_person_is_blocked(uuid),
          privacy.identity_principal_is_blocked(uuid)
          TO {KERNEL_ROLE};
        """
    )


def _install_fence_extensions() -> None:
    op.execute(
        f"""
        CREATE TRIGGER {PEOPLE_FENCE_TRIGGER}
        BEFORE UPDATE OF
          status, display_name, privacy_scope, legacy_source_ref,
          legacy_source_version, legacy_source_sha256, status_source_ref,
          status_source_version, status_source_sha256
        ON identity.people
        FOR EACH ROW EXECUTE FUNCTION
          privacy.fence_identity_tombstone_write();

        CREATE TRIGGER {DIRECTIVES_FENCE_TRIGGER}
        BEFORE INSERT OR UPDATE OF
          person_id, directive, enabled, expires_at
        ON identity.privacy_directives
        FOR EACH ROW EXECUTE FUNCTION
          privacy.fence_identity_tombstone_write();

        CREATE TRIGGER {EDGE_BLOCK_FENCE_TRIGGER}
        BEFORE INSERT OR UPDATE OF
          ha_user_id, person_id, reason_code
        ON identity.edge_privacy_user_blocks
        FOR EACH ROW EXECUTE FUNCTION
          privacy.fence_identity_tombstone_write();
        """
    )


def _install_function() -> None:
    revoked = ", ".join(ALL_REVOKED_ROLES)
    op.execute(
        f"""
        GRANT CREATE ON SCHEMA identity TO {KERNEL_ROLE};
        SET LOCAL ROLE {KERNEL_ROLE};
        CREATE FUNCTION identity.commit_authenticated_principal_binding_e5b(
          target_promotion_id uuid,
          authenticated_ha_user_id varchar(64),
          target_proposal_digest varchar(64),
          confirmation_nonce uuid,
          new_authority_receipt_id uuid,
          new_principal_id uuid,
          new_confirmation_artifact_id uuid,
          new_binding_id uuid
        ) RETURNS timestamptz
        LANGUAGE plpgsql
        VOLATILE
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $e5b${FUNCTION_BODY}$e5b$;
        REVOKE ALL ON FUNCTION {FUNCTION} FROM {revoked};
        GRANT EXECUTE ON FUNCTION {FUNCTION} TO {CALLER_ROLE};
        RESET ROLE;
        REVOKE CREATE ON SCHEMA identity FROM {KERNEL_ROLE};
        """
    )


def _assert_installed_contract() -> None:
    predicate_tables = ", ".join(
        f"'{table}'::regclass" for table in SHARED_POLICY_TABLES
    )
    op.execute(
        f"""
        DO $e5b_contract$
        DECLARE
          owner_oid oid;
          caller_oid oid;
          kernel_oid oid;
          function_oid oid;
        BEGIN
          SELECT oid INTO STRICT owner_oid
            FROM pg_catalog.pg_roles WHERE rolname = 'home_agent_owner';
          SELECT oid INTO STRICT caller_oid
            FROM pg_catalog.pg_roles WHERE rolname = '{CALLER_ROLE}';
          SELECT oid INTO STRICT kernel_oid
            FROM pg_catalog.pg_roles WHERE rolname = '{KERNEL_ROLE}';
          SELECT oid INTO STRICT function_oid
            FROM pg_catalog.pg_proc
           WHERE oid = '{FUNCTION}'::regprocedure;

          IF NOT EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_class
                WHERE oid = '{RECEIPT}'::regclass
                  AND relowner = owner_oid
                  AND relkind = 'r'
                  AND relpersistence = 'p'
                  AND relrowsecurity
                  AND relforcerowsecurity
                  AND NOT relispartition
             )
             OR NOT EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_attribute
                WHERE attrelid = 'identity.ha_user_bindings'::regclass
                  AND attname = 'authority_receipt_id'
                  AND attnum > 0
                  AND NOT attisdropped
                  AND attnotnull
             ) THEN
            RAISE EXCEPTION 'principal_binding_e5b_table_contract_invalid'
              USING ERRCODE = '55000';
          END IF;

          IF NOT EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_proc
                WHERE oid = function_oid
                  AND proowner = kernel_oid
                  AND prosecdef
                  AND provolatile = 'v'
                  AND prokind = 'f'
                  AND prorettype = 'timestamptz'::regtype
                  AND proconfig =
                      ARRAY['search_path=pg_catalog, pg_temp']::text[]
                  AND pg_catalog.encode(
                        pg_catalog.sha256(
                          pg_catalog.convert_to(prosrc, 'UTF8')
                        ),
                        'hex'
                      ) = '{FUNCTION_BODY_SHA256}'
             )
             OR NOT pg_catalog.has_function_privilege(
               '{CALLER_ROLE}', function_oid, 'EXECUTE'
             )
             OR (
               SELECT pg_catalog.count(*)
                 FROM pg_catalog.pg_proc AS function_row
                 CROSS JOIN LATERAL pg_catalog.aclexplode(
                   function_row.proacl
                 ) AS acl
                WHERE function_row.oid = function_oid
                  AND acl.privilege_type = 'EXECUTE'
                  AND acl.grantee = caller_oid
                  AND acl.grantor = kernel_oid
                  AND NOT acl.is_grantable
             ) <> 1
             OR (
               SELECT pg_catalog.count(*)
                 FROM pg_catalog.pg_proc AS function_row
                 CROSS JOIN LATERAL pg_catalog.aclexplode(
                   function_row.proacl
                 ) AS acl
                WHERE function_row.oid = function_oid
                  AND acl.privilege_type = 'EXECUTE'
                  AND acl.grantee = kernel_oid
                  AND acl.grantor = kernel_oid
                  AND NOT acl.is_grantable
             ) <> 1
             OR EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_proc AS function_row
                 CROSS JOIN LATERAL pg_catalog.aclexplode(
                   function_row.proacl
                 ) AS acl
                WHERE function_row.oid = function_oid
                  AND acl.privilege_type = 'EXECUTE'
                  AND (
                    acl.grantee NOT IN (caller_oid, kernel_oid)
                    OR acl.grantor <> kernel_oid
                    OR acl.is_grantable
                  )
             ) THEN
            RAISE EXCEPTION 'principal_binding_e5b_function_contract_invalid'
              USING ERRCODE = '42501';
          END IF;

          IF (
               SELECT pg_catalog.count(*)
                 FROM pg_catalog.pg_policy
                WHERE polrelid = '{RECEIPT}'::regclass
             ) <> 4
             OR (
               SELECT pg_catalog.count(*)
                 FROM pg_catalog.pg_policy
                WHERE polrelid IN ({predicate_tables})
                  AND polname IN (
                    '{SELECT_POLICY}', '{INSERT_POLICY}',
                    '{UPDATE_POLICY}', '{SUPPRESSION_POLICY}'
                  )
             ) <> 22 THEN
            RAISE EXCEPTION 'principal_binding_e5b_policy_contract_invalid'
              USING ERRCODE = '55000';
          END IF;

          IF (
               SELECT pg_catalog.count(*)
                 FROM pg_catalog.pg_trigger AS trigger_row
                WHERE (
                  (
                    trigger_row.tgrelid = 'identity.people'::regclass
                    AND trigger_row.tgname = '{PEOPLE_FENCE_TRIGGER}'
                  )
                  OR (
                    trigger_row.tgrelid =
                        'identity.privacy_directives'::regclass
                    AND trigger_row.tgname = '{DIRECTIVES_FENCE_TRIGGER}'
                  )
                  OR (
                    trigger_row.tgrelid =
                        'identity.edge_privacy_user_blocks'::regclass
                    AND trigger_row.tgname = '{EDGE_BLOCK_FENCE_TRIGGER}'
                  )
                )
                  AND NOT trigger_row.tgisinternal
                  AND trigger_row.tgenabled = 'O'
                  AND trigger_row.tgfoid =
                      'privacy.fence_identity_tombstone_write()'
                        ::regprocedure
             ) <> 3 THEN
            RAISE EXCEPTION 'principal_binding_e5b_fence_contract_invalid'
              USING ERRCODE = '55000';
          END IF;

          IF pg_catalog.has_table_privilege(
               '{KERNEL_ROLE}', '{RECEIPT}', 'DELETE'
             )
             OR pg_catalog.has_table_privilege(
               '{KERNEL_ROLE}', '{RECEIPT}', 'TRUNCATE'
             )
             OR pg_catalog.has_table_privilege(
               '{KERNEL_ROLE}', '{RECEIPT}', 'SELECT'
             )
             OR pg_catalog.has_table_privilege(
               '{KERNEL_ROLE}', '{RECEIPT}', 'INSERT'
             )
             OR pg_catalog.has_schema_privilege(
               '{KERNEL_ROLE}', 'identity', 'CREATE'
             )
             OR pg_catalog.has_schema_privilege(
               '{KERNEL_ROLE}', 'operations', 'CREATE'
             )
             OR pg_catalog.has_schema_privilege(
               '{KERNEL_ROLE}', 'privacy', 'CREATE'
             ) THEN
            RAISE EXCEPTION 'principal_binding_e5b_acl_contract_invalid'
              USING ERRCODE = '42501';
          END IF;
        END
        $e5b_contract$
        """
    )


def upgrade() -> None:
    _assert_prerequisites()
    _install_support_graph()
    _install_policies_and_grants()
    _install_fence_extensions()
    _install_function()
    _assert_installed_contract()


def downgrade() -> None:
    # Refuse contentfully populated rollback. No name, HA identity, digest,
    # nonce, or person identifier is included in the error.
    op.execute(
        f"""
        DO $e5b_downgrade$
        BEGIN
          IF EXISTS (SELECT 1 FROM {RECEIPT})
             OR EXISTS (
               SELECT 1
                 FROM identity.ha_user_bindings
                WHERE authority_receipt_id IS NOT NULL
             )
             OR EXISTS (
               SELECT 1
                 FROM identity.confirmation_artifacts
                WHERE purpose = 'ha_user_person_binding.confirm'
             ) THEN
            RAISE EXCEPTION
              'refusing to remove populated E5b principal-binding authority'
              USING ERRCODE = '2BP01';
          END IF;
        END
        $e5b_downgrade$
        """
    )

    revoked = ", ".join(ALL_REVOKED_ROLES)
    op.execute(f"REVOKE ALL ON FUNCTION {FUNCTION} FROM {revoked}")
    op.execute(
        f"""
        SET LOCAL ROLE {KERNEL_ROLE};
        DROP FUNCTION
          identity.commit_authenticated_principal_binding_e5b(
            uuid, varchar, varchar, uuid, uuid, uuid, uuid, uuid
          ) RESTRICT;
        RESET ROLE;
        """
    )

    op.execute(
        f"""
        DROP TRIGGER {PEOPLE_FENCE_TRIGGER} ON identity.people;
        DROP TRIGGER {DIRECTIVES_FENCE_TRIGGER}
          ON identity.privacy_directives;
        DROP TRIGGER {EDGE_BLOCK_FENCE_TRIGGER}
          ON identity.edge_privacy_user_blocks;
        """
    )

    for table, expression in reversed(SUPPRESSION_EXPRESSIONS.items()):
        del expression
        op.execute(
            f"DROP POLICY {SUPPRESSION_POLICY} ON {table}"
        )
    for table, commands in reversed(SHARED_POLICY_TABLES.items()):
        if "update" in commands:
            op.execute(f"DROP POLICY {UPDATE_POLICY} ON {table}")
        if "insert" in commands:
            op.execute(f"DROP POLICY {INSERT_POLICY} ON {table}")
        if "select" in commands:
            op.execute(f"DROP POLICY {SELECT_POLICY} ON {table}")

    op.execute(
        f"""
        DROP POLICY {SUPPRESSION_POLICY} ON {RECEIPT};
        DROP POLICY {RECEIPT_INSERT_POLICY} ON {RECEIPT};
        DROP POLICY {RECEIPT_SELECT_POLICY} ON {RECEIPT};
        DROP POLICY {RECEIPT_OWNER_POLICY} ON {RECEIPT};

        REVOKE EXECUTE ON FUNCTION
          {AUTHORITY_FUNCTION},
          privacy.identity_person_is_blocked(uuid),
          privacy.identity_principal_is_blocked(uuid)
          FROM {KERNEL_ROLE};

        REVOKE SELECT ({", ".join(RECEIPT_COLUMNS)}),
          INSERT ({", ".join(RECEIPT_COLUMNS)})
          ON {RECEIPT} FROM {KERNEL_ROLE};
        REVOKE SELECT (
          request_id, ha_user_id, review_code, state, expires_at,
          staged_at, closed_at
        ), UPDATE (state, closed_at)
          ON identity.principal_binding_requests FROM {KERNEL_ROLE};
        REVOKE SELECT (
          proposal_id, operator_request_id, request_id, ha_user_id,
          person_id, reviewed_display_label, person_snapshot_digest,
          proposal_digest, state, stage_receipt_digest, staged_at,
          expires_at, consumed_at, result_principal_id,
          confirmation_artifact_id
        ), UPDATE (
          state, consumed_at, result_principal_id, confirmation_artifact_id
        ) ON identity.principal_binding_proposals FROM {KERNEL_ROLE};
        REVOKE SELECT (
          person_id, display_name, status, privacy_scope,
          legacy_source_sha256, status_source_sha256
        ) ON identity.people FROM {KERNEL_ROLE};
        REVOKE SELECT (principal_id, person_id, kind, status),
          INSERT (
            principal_id, person_id, kind, display_label, status, created_at
          ) ON identity.principals FROM {KERNEL_ROLE};
        REVOKE SELECT (
          artifact_id, principal_id, purpose, proposal_digest,
          client_nonce_sha256, issued_at, expires_at, consumed_at
        ), INSERT (
          artifact_id, principal_id, purpose, proposal_digest,
          client_nonce_sha256, issued_at, expires_at, consumed_at
        ) ON identity.confirmation_artifacts FROM {KERNEL_ROLE};
        REVOKE SELECT (
          binding_id, proposal_id, authority_receipt_id, ha_user_id,
          principal_id, person_id, confirmed_by_principal_id, confirmed_at,
          revoked_at, source_artifact_id
        ), INSERT (
          binding_id, proposal_id, authority_receipt_id, ha_user_id,
          principal_id, person_id, confirmed_by_principal_id, confirmed_at,
          revoked_at, source_artifact_id
        ) ON identity.ha_user_bindings FROM {KERNEL_ROLE};
        REVOKE SELECT (
          artifact_id, artifact_kind, store, external_ref, content_sha256,
          owner_principal_id, retention_class, status, created_at
        ), INSERT (
          artifact_id, artifact_kind, store, external_ref, content_sha256,
          owner_principal_id, retention_class, status, created_at
        ) ON privacy.artifact_registry FROM {KERNEL_ROLE};
        REVOKE SELECT (
          promotion_id, authority_scope, run_id, finalization_id,
          policy_digest, committed_at
        ) ON operations.semantic_authority_promotions FROM {KERNEL_ROLE};
        REVOKE SELECT (
          lineage_id, run_id, decision_kind, projection_table_kind,
          projection_id
        ) ON operations.reviewed_identity_migration_projection_lineage
          FROM {KERNEL_ROLE};
        REVOKE SELECT (lineage_id, person_id, subject_role)
          ON operations.reviewed_identity_migration_projection_subjects
          FROM {KERNEL_ROLE};
        REVOKE SELECT (person_id, directive, enabled)
          ON identity.privacy_directives FROM {KERNEL_ROLE};
        REVOKE SELECT (ha_user_id, person_id)
          ON identity.edge_privacy_user_blocks FROM {KERNEL_ROLE};
        REVOKE ALL PRIVILEGES ON TABLE
          identity.principal_binding_requests,
          identity.principal_binding_proposals,
          identity.people,
          identity.principals,
          identity.confirmation_artifacts,
          identity.ha_user_bindings,
          identity.privacy_directives,
          identity.edge_privacy_user_blocks,
          privacy.artifact_registry,
          operations.semantic_authority_promotions,
          operations.reviewed_identity_migration_projection_lineage,
          operations.reviewed_identity_migration_projection_subjects,
          {RECEIPT}
          FROM {KERNEL_ROLE};

        ALTER TABLE identity.ha_user_bindings
          DROP CONSTRAINT {BINDING_RECEIPT_FK};
        ALTER TABLE identity.ha_user_bindings
          DROP CONSTRAINT {BINDING_RECEIPT_UNIQUE};
        ALTER TABLE identity.ha_user_bindings
          DROP COLUMN authority_receipt_id;

        DROP TABLE {RECEIPT};
        DROP INDEX identity.{GLOBAL_NONCE_INDEX};
        ALTER TABLE
          operations.reviewed_identity_migration_projection_lineage
          DROP CONSTRAINT {LINEAGE_EXACT_CONSTRAINT};
        ALTER TABLE operations.semantic_authority_promotions
          DROP CONSTRAINT {PROMOTION_EXACT_CONSTRAINT};
        ALTER TABLE identity.confirmation_artifacts
          DROP CONSTRAINT {CONFIRMATION_EXACT_CONSTRAINT};
        ALTER TABLE identity.principal_binding_proposals
          DROP CONSTRAINT {PROPOSAL_EXACT_CONSTRAINT};

        REVOKE CREATE, USAGE ON SCHEMA identity FROM {KERNEL_ROLE};
        REVOKE CREATE, USAGE ON SCHEMA operations FROM {KERNEL_ROLE};
        REVOKE CREATE, USAGE ON SCHEMA privacy FROM {KERNEL_ROLE};
        """
    )
