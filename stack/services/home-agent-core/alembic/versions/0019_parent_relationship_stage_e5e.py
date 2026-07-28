"""Install the authenticated, atomic E5e parent-preview staging kernel.

Revision ID: 0019_parent_stage_e5e
Revises: 0018_parent_relationship_e5d
Create Date: 2026-07-27

This revision enables only the private proposal-staging half of the parent
relationship protocol. It derives the child from the authenticated HA binding
and derives both parents from the reviewed migration lineage. A caller can
choose neither identities nor the number of edges.

The function is a SECURITY DEFINER boundary owned by a dedicated NOLOGIN role.
It must be the first application operation in a SERIALIZABLE transaction, takes
the global identity write fence before reading semantic state, re-runs current
authority and privacy checks, and stages exactly two edges atomically. It
creates no fact, confirmation artifact, memory transaction, or receipt.

Production remains pinned to revision 0006a and record_only. The commit kernel,
private BFF route, and UI remain disabled.
"""

from __future__ import annotations

import hashlib
from typing import Sequence

from alembic import op


revision: str = "0019_parent_stage_e5e"
down_revision: str | None = "0018_parent_relationship_e5d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


CALLER_ROLE = "home_agent_binding_committer"
KERNEL_ROLE = "home_agent_parent_relationship_kernel"
AUTHORITY_KERNEL_ROLE = "home_agent_identity_authority_kernel"
FUNCTION = (
    "identity.stage_authenticated_parent_relationship_e5e("
    "character varying,uuid,uuid,uuid,uuid,uuid,"
    "character varying,character varying)"
)
POLICY_PREFIX = "parent_relationship_stage_e5e"
EMPTY_OPEN_PARENT_FACT_SET_DIGEST = (
    "cb991031ceed6e7e1ecf3532118ee2ad24f71bf18602b9e4ef2ace50035ed50e"
)

READ_TABLES = (
    "identity.ha_user_bindings",
    "identity.principals",
    "identity.people",
    "identity.privacy_directives",
    "identity.edge_privacy_user_blocks",
    "identity.legacy_role_labels",
    "knowledge.fact_versions",
    "operations.semantic_authority_promotions",
    "operations.reviewed_identity_migration_projection_lineage",
    "operations.reviewed_identity_migration_projection_subjects",
    "identity.parent_relationship_requests",
    "identity.parent_relationship_proposals",
    "identity.parent_relationship_proposal_edges",
)

WRITE_TABLES = (
    "identity.parent_relationship_requests",
    "identity.parent_relationship_proposals",
    "identity.parent_relationship_proposal_edges",
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
  e5e_authority_result varchar(32);
  e5e_promotion record;
  e5e_binding record;
  e5e_existing identity.parent_relationship_proposals%ROWTYPE;
  e5e_parent_ids uuid[];
  e5e_label_ids uuid[];
  e5e_parent_labels text[];
  e5e_person_source_digests text[];
  e5e_role_source_digests text[];
  e5e_person_snapshot_digests text[] := ARRAY[]::text[];
  e5e_role_snapshot_digests text[] := ARRAY[]::text[];
  e5e_edge_commitments text[] := ARRAY[]::text[];
  e5e_review_codes text[];
  e5e_edge_ids uuid[];
  e5e_candidate_set_commitment text;
  e5e_proposal_digest text;
  e5e_operation_time timestamptz;
  e5e_expires_at timestamptz;
  e5e_index integer;
  e5e_count bigint;
BEGIN
  IF session_user <> '{CALLER_ROLE}'
     OR current_user <> '{KERNEL_ROLE}'
     OR pg_catalog.pg_has_role(
          session_user, '{KERNEL_ROLE}', 'SET'
        ) THEN
    RAISE EXCEPTION 'parent_relationship_e5e_role_invalid'
      USING ERRCODE = '42501';
  END IF;

  IF pg_catalog.current_setting('transaction_isolation') <> 'serializable'
     OR pg_catalog.current_setting('transaction_read_only') <> 'off'
     OR pg_catalog.pg_is_in_recovery()
     OR pg_catalog.pg_current_xact_id_if_assigned() IS NOT NULL THEN
    RAISE EXCEPTION 'parent_relationship_e5e_transaction_invalid'
      USING ERRCODE = '25000';
  END IF;

  IF authenticated_ha_user_id IS NULL
     OR authenticated_ha_user_id <> pg_catalog.btrim(authenticated_ha_user_id)
     OR pg_catalog.length(authenticated_ha_user_id) NOT BETWEEN 1 AND 64
     OR new_request_id IS NULL
     OR new_proposal_id IS NULL
     OR new_operator_request_id IS NULL
     OR new_proposal_edge_id_0 IS NULL
     OR new_proposal_edge_id_1 IS NULL
     OR review_code_0 IS NULL
     OR review_code_1 IS NULL
     OR review_code_0 !~ '^[A-HJ-NP-Z2-9]{{16}}$'
     OR review_code_1 !~ '^[A-HJ-NP-Z2-9]{{16}}$'
     OR review_code_0 = review_code_1
     OR (
       SELECT pg_catalog.count(DISTINCT supplied_id)
         FROM pg_catalog.unnest(
           ARRAY[
             new_request_id,
             new_proposal_id,
             new_operator_request_id,
             new_proposal_edge_id_0,
             new_proposal_edge_id_1
           ]::uuid[]
         ) AS supplied(supplied_id)
     ) <> 5
     OR EXISTS (
       SELECT 1
         FROM pg_catalog.unnest(
           ARRAY[
             new_request_id,
             new_proposal_id,
             new_operator_request_id,
             new_proposal_edge_id_0,
             new_proposal_edge_id_1
           ]::uuid[]
         ) AS supplied(supplied_id)
        WHERE pg_catalog.substring(supplied_id::text, 15, 1) <> '7'
           OR pg_catalog.substring(supplied_id::text, 20, 1)
              NOT IN ('8','9','a','b')
     ) THEN
    RAISE EXCEPTION 'parent_relationship_e5e_input_invalid'
      USING ERRCODE = '22023';
  END IF;

  PERFORM pg_catalog.set_config('statement_timeout', '15s', true);
  PERFORM pg_catalog.set_config('lock_timeout', '5s', true);
  PERFORM pg_catalog.set_config(
    'idle_in_transaction_session_timeout', '15s', true
  );
  PERFORM pg_catalog.set_config('transaction_timeout', '30s', true);

  -- This is intentionally the first application-data operation. E5a takes
  -- the same fence again and retains its authority locks until outer commit.
  PERFORM privacy.lock_identity_semantic_write_fence();

  SELECT promotion.promotion_id,
         promotion.run_id,
         promotion.finalization_id,
         promotion.policy_digest,
         promotion.committed_at
    INTO e5e_promotion
    FROM operations.semantic_authority_promotions AS promotion
   WHERE promotion.authority_scope = 'identity_semantics';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'parent_relationship_e5e_authority_unavailable'
      USING ERRCODE = '55000';
  END IF;

  SELECT operations.evaluate_current_identity_semantic_authority(
           e5e_promotion.promotion_id
         )
    INTO e5e_authority_result;
  IF e5e_authority_result IS DISTINCT FROM
       'current_database_authority' THEN
    RAISE EXCEPTION 'parent_relationship_e5e_authority_not_current'
      USING ERRCODE = '55000';
  END IF;

  SELECT binding.binding_id,
         binding.principal_id,
         binding.person_id AS child_person_id,
         principal.display_label AS child_display_label,
         principal.status AS principal_status,
         principal.kind AS principal_kind,
         child.status AS child_status
    INTO e5e_binding
    FROM identity.ha_user_bindings AS binding
    JOIN identity.principals AS principal
      ON principal.principal_id = binding.principal_id
     AND principal.person_id = binding.person_id
    JOIN identity.people AS child
      ON child.person_id = binding.person_id
   WHERE binding.ha_user_id = authenticated_ha_user_id
     AND binding.revoked_at IS NULL;
  IF NOT FOUND
     OR e5e_binding.principal_status <> 'active'
     OR e5e_binding.principal_kind <> 'ha_user'
     OR e5e_binding.child_status <> 'active'
     OR privacy.identity_person_is_blocked(
          e5e_binding.child_person_id
        )
     OR privacy.identity_principal_is_blocked(
          e5e_binding.principal_id
        )
     OR EXISTS (
       SELECT 1
         FROM identity.privacy_directives AS directive
        WHERE directive.person_id = e5e_binding.child_person_id
          AND directive.enabled
          AND directive.directive IN (
            'auto_expire', 'do_not_track', 'ignored', 'silent'
          )
     )
     OR EXISTS (
       SELECT 1
         FROM identity.edge_privacy_user_blocks AS edge_block
        WHERE edge_block.ha_user_id = authenticated_ha_user_id
           OR edge_block.person_id = e5e_binding.child_person_id
     ) THEN
    RAISE EXCEPTION 'parent_relationship_e5e_binding_invalid'
      USING ERRCODE = '42501';
  END IF;

  SELECT pg_catalog.array_agg(
           candidate.parent_person_id
           ORDER BY candidate.parent_person_id
         ),
         pg_catalog.array_agg(
           candidate.label_id
           ORDER BY candidate.parent_person_id
         ),
         pg_catalog.array_agg(
           candidate.display_name
           ORDER BY candidate.parent_person_id
         ),
         pg_catalog.array_agg(
           candidate.person_source_digest
           ORDER BY candidate.parent_person_id
         ),
         pg_catalog.array_agg(
           candidate.role_source_digest
           ORDER BY candidate.parent_person_id
         ),
         pg_catalog.count(*)
    INTO e5e_parent_ids,
         e5e_label_ids,
         e5e_parent_labels,
         e5e_person_source_digests,
         e5e_role_source_digests,
         e5e_count
    FROM (
      SELECT parent.person_id AS parent_person_id,
             label.label_id,
             parent.display_name,
             pg_catalog.encode(
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
             ) AS person_source_digest,
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
             ) AS role_source_digest
        FROM identity.legacy_role_labels AS label
        JOIN identity.people AS parent
          ON parent.person_id = label.person_id
        JOIN operations.reviewed_identity_migration_projection_lineage
             AS lineage
          ON lineage.run_id = e5e_promotion.run_id
         AND lineage.decision_kind = 'legacy_role_candidate'
         AND lineage.projection_table_kind =
             'identity.legacy_role_labels'
         AND lineage.projection_id = label.label_id
        JOIN operations.reviewed_identity_migration_projection_subjects
             AS projection_subject
          ON projection_subject.lineage_id = lineage.lineage_id
         AND projection_subject.person_id = parent.person_id
         AND projection_subject.subject_role = 'primary'
       WHERE label.role_label = 'parent'
         AND label.perspective = 'unknown'
         AND parent.status = 'active'
         AND parent.person_id <> e5e_binding.child_person_id
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
    ) AS candidate;

  IF e5e_count <> 2
     OR e5e_parent_ids[1] = e5e_parent_ids[2]
     OR e5e_label_ids[1] = e5e_label_ids[2]
     OR pg_catalog.lower(e5e_parent_labels[1])
        = pg_catalog.lower(e5e_parent_labels[2])
     OR pg_catalog.lower(e5e_parent_labels[1])
        = pg_catalog.lower(e5e_binding.child_display_label)
     OR pg_catalog.lower(e5e_parent_labels[2])
        = pg_catalog.lower(e5e_binding.child_display_label) THEN
    RAISE EXCEPTION 'parent_relationship_e5e_candidate_set_invalid'
      USING ERRCODE = '23514';
  END IF;

  IF EXISTS (
    SELECT 1
      FROM knowledge.fact_versions AS fact
     WHERE fact.predicate = 'parent_of'
       AND fact.object ->> 'person_id' =
           e5e_binding.child_person_id::text
       AND fact.perspective_principal_id =
           e5e_binding.principal_id
       AND pg_catalog.upper_inf(fact.system_range)
       AND fact.resolution = 'accepted'
  ) THEN
    RAISE EXCEPTION 'parent_relationship_e5e_parent_fact_exists'
      USING ERRCODE = '23514';
  END IF;

  e5e_review_codes := ARRAY[review_code_0, review_code_1]::text[];
  e5e_edge_ids := ARRAY[
    new_proposal_edge_id_0, new_proposal_edge_id_1
  ]::uuid[];

  SELECT proposal.*
    INTO e5e_existing
    FROM identity.parent_relationship_proposals AS proposal
   WHERE proposal.operator_request_id = new_operator_request_id;

  IF FOUND THEN
    e5e_operation_time := e5e_existing.staged_at;
    e5e_expires_at := e5e_existing.expires_at;
  ELSE
    IF EXISTS (
      SELECT 1
        FROM identity.parent_relationship_requests AS request
       WHERE request.state IN ('pending','staged')
         AND (
           request.principal_id = e5e_binding.principal_id
           OR request.child_person_id = e5e_binding.child_person_id
         )
    ) OR EXISTS (
      SELECT 1
        FROM identity.parent_relationship_proposals AS proposal
       WHERE proposal.state = 'ready'
         AND (
           proposal.principal_id = e5e_binding.principal_id
           OR proposal.child_person_id = e5e_binding.child_person_id
         )
    ) THEN
      RAISE EXCEPTION 'parent_relationship_e5e_request_conflict'
        USING ERRCODE = '23505';
    END IF;
    e5e_operation_time := pg_catalog.clock_timestamp();
    e5e_expires_at := e5e_operation_time + interval '15 minutes';
  END IF;

  FOR e5e_index IN 1..2 LOOP
    e5e_person_snapshot_digests :=
      pg_catalog.array_append(
        e5e_person_snapshot_digests,
        e5e_person_source_digests[e5e_index]
      );
    e5e_role_snapshot_digests :=
      pg_catalog.array_append(
        e5e_role_snapshot_digests,
        e5e_role_source_digests[e5e_index]
      );
    e5e_edge_commitments := pg_catalog.array_append(
      e5e_edge_commitments,
      pg_catalog.encode(
        pg_catalog.sha256(
          pg_catalog.convert_to(
            pg_catalog.jsonb_build_object(
              'child_person_id',
              e5e_binding.child_person_id::text,
              'contract', 'parent-relationship-authority-v2',
              'legacy_label_id',
              e5e_label_ids[e5e_index]::text,
              'ordinal', e5e_index - 1,
              'parent_person_id',
              e5e_parent_ids[e5e_index]::text,
              'person_snapshot_digest',
              e5e_person_snapshot_digests[e5e_index],
              'predicate', 'parent_of',
              'proposal_edge_id',
              e5e_edge_ids[e5e_index]::text,
              'review_code',
              e5e_review_codes[e5e_index],
              'role_snapshot_digest',
              e5e_role_snapshot_digests[e5e_index]
            )::text,
            'UTF8'
          )
        ),
        'hex'
      )
    );
  END LOOP;

  e5e_candidate_set_commitment := pg_catalog.encode(
    pg_catalog.sha256(
      pg_catalog.convert_to(
        pg_catalog.jsonb_build_object(
          'contract', 'parent-candidate-set-v2',
          'edge_commitments', e5e_edge_commitments
        )::text,
        'UTF8'
      )
    ),
    'hex'
  );
  e5e_proposal_digest := pg_catalog.encode(
    pg_catalog.sha256(
      pg_catalog.convert_to(
        pg_catalog.jsonb_build_object(
          'candidate_count', 2,
          'candidate_set_commitment',
          e5e_candidate_set_commitment,
          'child_person_id',
          e5e_binding.child_person_id::text,
          'contract', 'parent-relationship-authority-v2',
          'expires_at',
          pg_catalog.to_char(
            e5e_expires_at AT TIME ZONE 'UTC',
            'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
          ),
          'open_parent_fact_set_digest',
          '{EMPTY_OPEN_PARENT_FACT_SET_DIGEST}',
          'operator_request_id',
          new_operator_request_id::text,
          'policy_digest', e5e_promotion.policy_digest,
          'policy_version', 'home-agent-mvp-v1',
          'principal_id', e5e_binding.principal_id::text,
          'proposal_id', new_proposal_id::text,
          'request_id', new_request_id::text,
          'staged_at',
          pg_catalog.to_char(
            e5e_operation_time AT TIME ZONE 'UTC',
            'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
          )
        )::text,
        'UTF8'
      )
    ),
    'hex'
  );

  IF e5e_existing.proposal_id IS NOT NULL THEN
    IF e5e_existing.proposal_id IS DISTINCT FROM new_proposal_id
       OR e5e_existing.request_id IS DISTINCT FROM new_request_id
       OR e5e_existing.principal_id IS DISTINCT FROM
          e5e_binding.principal_id
       OR e5e_existing.child_person_id IS DISTINCT FROM
          e5e_binding.child_person_id
       OR e5e_existing.contract_version <>
          'parent-relationship-authority-v2'
       OR e5e_existing.candidate_count <> 2
       OR e5e_existing.candidate_set_commitment IS DISTINCT FROM
          e5e_candidate_set_commitment
       OR e5e_existing.open_parent_fact_set_digest <>
          '{EMPTY_OPEN_PARENT_FACT_SET_DIGEST}'
       OR e5e_existing.proposal_digest IS DISTINCT FROM
          e5e_proposal_digest
       OR e5e_existing.policy_version <> 'home-agent-mvp-v1'
       OR e5e_existing.policy_digest IS DISTINCT FROM
          e5e_promotion.policy_digest
       OR e5e_existing.state <> 'ready'
       OR e5e_existing.consumed_at IS NOT NULL
       OR e5e_existing.expires_at <= pg_catalog.clock_timestamp()
       OR (
         SELECT pg_catalog.count(*)
           FROM identity.parent_relationship_proposal_edges AS edge
          WHERE edge.proposal_id = e5e_existing.proposal_id
            AND edge.proposal_edge_id =
                e5e_edge_ids[edge.ordinal + 1]
            AND edge.parent_person_id =
                e5e_parent_ids[edge.ordinal + 1]
            AND edge.child_person_id =
                e5e_binding.child_person_id
            AND edge.legacy_label_id =
                e5e_label_ids[edge.ordinal + 1]
            AND edge.review_code =
                e5e_review_codes[edge.ordinal + 1]
            AND edge.person_snapshot_digest =
                e5e_person_snapshot_digests[edge.ordinal + 1]
            AND edge.role_snapshot_digest =
                e5e_role_snapshot_digests[edge.ordinal + 1]
            AND edge.edge_commitment =
                e5e_edge_commitments[edge.ordinal + 1]
       ) <> 2 THEN
      RAISE EXCEPTION 'parent_relationship_e5e_replay_drift'
        USING ERRCODE = '55000';
    END IF;
  ELSE
    INSERT INTO identity.parent_relationship_requests (
      request_id, principal_id, child_person_id, binding_id, state,
      requested_at, expires_at, staged_at, closed_at
    ) VALUES (
      new_request_id, e5e_binding.principal_id,
      e5e_binding.child_person_id, e5e_binding.binding_id, 'staged',
      e5e_operation_time, e5e_expires_at, e5e_operation_time, NULL
    );

    INSERT INTO identity.parent_relationship_proposals (
      proposal_id, operator_request_id, request_id, principal_id,
      child_person_id, contract_version, candidate_count,
      candidate_set_commitment, open_parent_fact_set_digest,
      proposal_digest, policy_version, policy_digest, state,
      staged_at, expires_at, consumed_at,
      confirmation_artifact_id, memory_transaction_id
    ) VALUES (
      new_proposal_id, new_operator_request_id, new_request_id,
      e5e_binding.principal_id, e5e_binding.child_person_id,
      'parent-relationship-authority-v2', 2,
      e5e_candidate_set_commitment,
      '{EMPTY_OPEN_PARENT_FACT_SET_DIGEST}',
      e5e_proposal_digest, 'home-agent-mvp-v1',
      e5e_promotion.policy_digest, 'ready',
      e5e_operation_time, e5e_expires_at, NULL, NULL, NULL
    );

    FOR e5e_index IN 1..2 LOOP
      INSERT INTO identity.parent_relationship_proposal_edges (
        proposal_edge_id, proposal_id, ordinal, parent_person_id,
        child_person_id, legacy_label_id, review_code,
        person_snapshot_digest, role_snapshot_digest, edge_commitment,
        predicate, legacy_role_label, legacy_perspective,
        legacy_authoritative, required_authority, required_support,
        required_contradiction, required_freshness, required_coverage,
        required_resolution, privacy_scope
      ) VALUES (
        e5e_edge_ids[e5e_index], new_proposal_id, e5e_index - 1,
        e5e_parent_ids[e5e_index], e5e_binding.child_person_id,
        e5e_label_ids[e5e_index], e5e_review_codes[e5e_index],
        e5e_person_snapshot_digests[e5e_index],
        e5e_role_snapshot_digests[e5e_index],
        e5e_edge_commitments[e5e_index], 'parent_of', 'parent',
        'unknown', false, 'explicit_related_party',
        'explicit_authority', 'none', 'not_applicable',
        'not_applicable', 'accepted', 'private'
      );
    END LOOP;
  END IF;

  RETURN QUERY SELECT
    new_request_id,
    new_proposal_id,
    e5e_proposal_digest::varchar(64),
    e5e_expires_at,
    e5e_binding.child_display_label::varchar(255),
    e5e_parent_labels[1]::varchar(255),
    review_code_0::varchar(16),
    e5e_parent_labels[2]::varchar(255),
    review_code_1::varchar(16);
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
        DO $parent_relationship_e5e_preconditions$
        DECLARE
          caller_oid oid;
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
             ) IS DISTINCT FROM '0018_parent_relationship_e5d' THEN
            RAISE EXCEPTION 'parent_relationship_e5e_revision_invalid'
              USING ERRCODE = '55000';
          END IF;

          SELECT oid INTO STRICT caller_oid
            FROM pg_catalog.pg_roles
           WHERE rolname = '{CALLER_ROLE}';
          SELECT oid INTO STRICT kernel_oid
            FROM pg_catalog.pg_roles
           WHERE rolname = '{KERNEL_ROLE}';

          IF NOT EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_roles
                WHERE oid = caller_oid
                  AND rolcanlogin
                  AND NOT rolinherit
                  AND NOT rolsuper
                  AND NOT rolcreatedb
                  AND NOT rolcreaterole
                  AND NOT rolreplication
                  AND NOT rolbypassrls
             )
             OR EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_auth_members
                WHERE roleid = caller_oid
                   OR member = caller_oid
             )
             OR NOT EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_authid
                WHERE oid = kernel_oid
                  AND NOT rolcanlogin
                  AND NOT rolinherit
                  AND NOT rolsuper
                  AND NOT rolcreatedb
                  AND NOT rolcreaterole
                  AND NOT rolreplication
                  AND NOT rolbypassrls
                  AND rolconnlimit = 0
                  AND rolpassword IS NULL
             )
             OR EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_auth_members
                WHERE member = kernel_oid
             )
             OR pg_catalog.to_regprocedure('{FUNCTION}') IS NOT NULL THEN
            RAISE EXCEPTION 'parent_relationship_e5e_role_invalid'
              USING ERRCODE = '42501';
          END IF;
        END
        $parent_relationship_e5e_preconditions$;
        """
    )


def _install_policies_and_grants() -> None:
    predicate = _predicate()
    for index, table in enumerate(READ_TABLES):
        policy = f"{POLICY_PREFIX}_r{index:02d}_select"
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
        op.execute(
            f"CREATE POLICY {policy} ON {table} FOR SELECT "
            f"TO {KERNEL_ROLE} USING ({predicate})"
        )
    for index, table in enumerate(WRITE_TABLES):
        policy = f"{POLICY_PREFIX}_w{index:02d}_insert"
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
        op.execute(
            f"CREATE POLICY {policy} ON {table} FOR INSERT "
            f"TO {KERNEL_ROLE} WITH CHECK ({predicate})"
        )

    op.execute(
        f"""
        GRANT USAGE ON SCHEMA identity, knowledge, operations, privacy
          TO {KERNEL_ROLE};

        GRANT SELECT (
          binding_id, ha_user_id, principal_id, person_id, revoked_at
        ) ON identity.ha_user_bindings TO {KERNEL_ROLE};
        GRANT SELECT (
          principal_id, person_id, kind, display_label, status
        ) ON identity.principals TO {KERNEL_ROLE};
        GRANT SELECT (
          person_id, display_name, status, privacy_scope,
          legacy_source_sha256, status_source_sha256
        ) ON identity.people TO {KERNEL_ROLE};
        GRANT SELECT (person_id, directive, enabled)
          ON identity.privacy_directives TO {KERNEL_ROLE};
        GRANT SELECT (ha_user_id, person_id)
          ON identity.edge_privacy_user_blocks TO {KERNEL_ROLE};
        GRANT SELECT (
          label_id, person_id, role_label, perspective,
          source_snapshot_sha256
        ) ON identity.legacy_role_labels TO {KERNEL_ROLE};
        GRANT SELECT (
          fact_version_id, subject_id, predicate, object,
          perspective_principal_id, system_range, resolution
        ) ON knowledge.fact_versions TO {KERNEL_ROLE};
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

        GRANT SELECT (
          request_id, principal_id, child_person_id, binding_id, state,
          requested_at, expires_at, staged_at, closed_at
        ) ON identity.parent_relationship_requests TO {KERNEL_ROLE};
        GRANT INSERT (
          request_id, principal_id, child_person_id, binding_id, state,
          requested_at, expires_at, staged_at, closed_at
        ) ON identity.parent_relationship_requests TO {KERNEL_ROLE};
        GRANT SELECT ON identity.parent_relationship_proposals
          TO {KERNEL_ROLE};
        GRANT INSERT ON identity.parent_relationship_proposals
          TO {KERNEL_ROLE};
        GRANT SELECT ON identity.parent_relationship_proposal_edges
          TO {KERNEL_ROLE};
        GRANT INSERT ON identity.parent_relationship_proposal_edges
          TO {KERNEL_ROLE};

        GRANT EXECUTE ON FUNCTION
          operations.evaluate_current_identity_semantic_authority(uuid),
          privacy.lock_identity_semantic_write_fence(),
          privacy.identity_person_is_blocked(uuid),
          privacy.identity_principal_is_blocked(uuid)
          TO {KERNEL_ROLE};
        """
    )


def _install_function() -> None:
    revoked = ", ".join(ALL_RUNTIME_ROLES)
    op.execute(
        f"""
        CREATE FUNCTION identity.stage_authenticated_parent_relationship_e5e(
          authenticated_ha_user_id character varying,
          new_request_id uuid,
          new_proposal_id uuid,
          new_operator_request_id uuid,
          new_proposal_edge_id_0 uuid,
          new_proposal_edge_id_1 uuid,
          review_code_0 character varying,
          review_code_1 character varying
        )
        RETURNS TABLE (
          request_id uuid,
          proposal_id uuid,
          proposal_digest varchar(64),
          expires_at timestamptz,
          child_display_label varchar(255),
          parent_0_display_label varchar(255),
          parent_0_review_code varchar(16),
          parent_1_display_label varchar(255),
          parent_1_review_code varchar(16)
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        SET row_security = on
        AS $parent_relationship_stage_e5e${FUNCTION_BODY}$parent_relationship_stage_e5e$;

        ALTER FUNCTION {FUNCTION} OWNER TO {KERNEL_ROLE};
        REVOKE ALL PRIVILEGES ON FUNCTION {FUNCTION} FROM {revoked};
        GRANT EXECUTE ON FUNCTION {FUNCTION} TO {CALLER_ROLE};
        """
    )


def _validate_installation() -> None:
    op.execute(
        f"""
        DO $parent_relationship_e5e_validation$
        DECLARE
          function_oid oid;
          kernel_oid oid;
          caller_oid oid;
          observed_body_digest text;
        BEGIN
          function_oid := pg_catalog.to_regprocedure('{FUNCTION}');
          SELECT oid INTO STRICT kernel_oid
            FROM pg_catalog.pg_roles
           WHERE rolname = '{KERNEL_ROLE}';
          SELECT oid INTO STRICT caller_oid
            FROM pg_catalog.pg_roles
           WHERE rolname = '{CALLER_ROLE}';
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
                  caller_oid, function_oid, 'EXECUTE'
                )
             OR pg_catalog.has_function_privilege(
                  'home_agent_api', function_oid, 'EXECUTE'
                )
             OR pg_catalog.has_function_privilege(
                  'home_agent_binding_operator', function_oid, 'EXECUTE'
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
              'parent_relationship_e5e_installation_invalid'
              USING ERRCODE = '42501';
          END IF;
        END
        $parent_relationship_e5e_validation$;
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
        DO $parent_relationship_e5e_nonempty$
        BEGIN
          IF EXISTS (
               SELECT 1
                 FROM identity.parent_relationship_requests
             )
             OR EXISTS (
               SELECT 1
                 FROM identity.parent_relationship_proposals
             )
             OR EXISTS (
               SELECT 1
                 FROM identity.parent_relationship_proposal_edges
             ) THEN
            RAISE EXCEPTION
              'refusing to remove E5e with staged parent evidence'
              USING ERRCODE = '2BP01';
          END IF;
        END
        $parent_relationship_e5e_nonempty$;
        """
    )
    op.execute(f"DROP FUNCTION {FUNCTION}")
    for index, table in reversed(tuple(enumerate(WRITE_TABLES))):
        policy = f"{POLICY_PREFIX}_w{index:02d}_insert"
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
    for index, table in reversed(tuple(enumerate(READ_TABLES))):
        policy = f"{POLICY_PREFIX}_r{index:02d}_select"
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
    op.execute(
        f"""
        REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA
          identity, knowledge, operations, privacy FROM {KERNEL_ROLE};
        REVOKE USAGE ON SCHEMA identity, knowledge, operations, privacy
          FROM {KERNEL_ROLE};
        """
    )
