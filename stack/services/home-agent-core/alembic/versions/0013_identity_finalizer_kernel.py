"""Add the dormant E3 reviewed-identity atomic finalizer kernel.

Revision ID: 0013_identity_finalizer_e3
Revises: 0012_identity_erasure_e2
Create Date: 2026-07-26

E3 is database-only groundwork. Production remains pinned to revision 0006a
and ``record_only``. No API, BFF, UI, Compose command, activation helper, or
live credential can invoke this function. The finalizer login remains expired
by provisioning policy.

An owner inserts one short-lived, content-free admission only after the offline
review and finalization signatures have been checked. The SECURITY DEFINER
kernel accepts the exact canonical verified document bytes plus that admission
ID. It rechecks the live manifests, privacy closure, E2 tombstones, collisions,
lineage, and auto-expiry effects under SERIALIZABLE isolation, then writes the
candidate semantic projections and all receipts atomically. It never creates a
principal, HA binding, parent fact, cutover/freeze/privacy-check row, memory,
initiative, place, or action.
"""

from __future__ import annotations

import hashlib
from typing import Sequence

from alembic import op
from sqlalchemy.schema import CreateTable

from app import schema


revision: str = "0013_identity_finalizer_e3"
down_revision: str | None = "0012_identity_erasure_e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


FINALIZER_ROLE = "home_agent_identity_finalizer"
KERNEL_ROLE = "home_agent_identity_finalizer_kernel"
ADMISSION = "operations.reviewed_identity_finalizer_admissions"
FUNCTION = "operations.finalize_reviewed_identity_migration(bytea,uuid)"
FENCE_TABLE = "privacy.identity_semantic_write_fence"
FENCE_FUNCTION = "privacy.lock_identity_semantic_write_fence()"
FENCE_TRIGGER_FUNCTION = "privacy.fence_identity_tombstone_write()"
EVIDENCE_SELECT_POLICY = "identity_finalizer_e3_select"
EVIDENCE_INSERT_POLICY = "identity_finalizer_e3_insert"
MIGRATION_RUN_LOCK_POLICY = "identity_finalizer_e3_migration_run_lock"
# SHA-256 of the UTF-8 bytes
# ``home-agent-identity-finalizer-verifier-bundle-v1\n``.  This is the
# domain-separated identifier for the offline verifier rules accepted by E3;
# it is not a digest of private review input.  The installed database function
# body is pinned independently by deployment grant replay.
VERIFIER_BUNDLE_MANIFEST = "home-agent-identity-finalizer-verifier-bundle-v1\n"
VERIFIER_BUNDLE_DIGEST = (
    "be7c581a949f1b5cc3e51748066d4bae812a7b44bee431cd0f0d9c9936f2c266"
)
FENCE_LOCK_BODY = r"""
DECLARE
  affected integer;
BEGIN
  UPDATE privacy.identity_semantic_write_fence
     SET fence_generation = fence_generation
   WHERE fence_key = 'global';
  GET DIAGNOSTICS affected = ROW_COUNT;
  IF affected <> 1 THEN
    RAISE EXCEPTION 'identity_semantic_write_fence_missing'
      USING ERRCODE = '55000';
  END IF;
END;
"""
FENCE_TRIGGER_BODY = r"""
BEGIN
  PERFORM privacy.lock_identity_semantic_write_fence();
  RETURN NEW;
END;
"""
FUNCTION_BODY = r"""
<<finalizer_state>>
DECLARE
  admission operations.reviewed_identity_finalizer_admissions%ROWTYPE;
  migration_run operations.reviewed_identity_migration_runs%ROWTYPE;
  document jsonb;
  target_projection jsonb;
  target_record jsonb;
  target_lineage jsonb;
  target_subject jsonb;
  target_effect jsonb;
  target_closure jsonb;
  person_ids uuid[];
  actual_document_sha256 text;
  projection_count integer;
  apply_decision_count integer;
  source_count integer;
  source_distinct_count integer;
  source_min_ordinal integer;
  source_max_ordinal integer;
  decision_count integer;
  decision_distinct_count integer;
  decision_min_ordinal integer;
  decision_max_ordinal integer;
  key_count integer;
  allowed_key_count integer;
  distinct_count integer;
  person_count integer;
  affected_rows integer;
  current_txid bigint;
  target_kind text;
  target_projection_table text;
  target_decision_id uuid;
  target_receipt_id uuid;
  target_projection_id uuid;
  target_person_id uuid;
  target_directive_id uuid;
  target_due_at timestamptz;
  exact_replay_id uuid;
  wall_clock_now timestamptz;
  finalized_marker timestamptz;
  exact_replay boolean;
BEGIN
  IF session_user <> 'home_agent_identity_finalizer'
     OR current_user <> 'home_agent_identity_finalizer_kernel'
     OR pg_catalog.pg_has_role(
          session_user, 'home_agent_identity_finalizer_kernel', 'SET'
        )
     OR pg_catalog.current_setting(
          'log_parameter_max_length_on_error'
        ) <> '0' THEN
    RAISE EXCEPTION 'identity_finalizer_role_invalid'
      USING ERRCODE = '42501';
  END IF;
  IF pg_catalog.current_setting('transaction_isolation') <> 'serializable'
     OR pg_catalog.current_setting('transaction_read_only') <> 'off'
     OR pg_catalog.pg_is_in_recovery() THEN
    RAISE EXCEPTION 'identity_finalizer_serializable_required'
      USING ERRCODE = '25001';
  END IF;
  IF finalizer_document IS NULL
     OR pg_catalog.octet_length(finalizer_document) NOT BETWEEN 2 AND 4194304
     OR admission_id IS NULL THEN
    RAISE EXCEPTION 'identity_finalizer_input_invalid'
      USING ERRCODE = '22023';
  END IF;

  -- Hash, decode, and validate the bounded top-level JSON shape before
  -- touching any table.  Malformed private bytes therefore never hold the
  -- erasure/finalization write fence.
  actual_document_sha256 := pg_catalog.encode(
    pg_catalog.sha256(finalizer_document), 'hex'
  );
  BEGIN
    document := pg_catalog.convert_from(finalizer_document, 'UTF8')::jsonb;
  EXCEPTION
    WHEN character_not_in_repertoire OR untranslatable_character
         OR invalid_text_representation THEN
      RAISE EXCEPTION 'identity_finalizer_document_invalid'
        USING ERRCODE = '22023';
  END;
  IF pg_catalog.jsonb_typeof(document) IS DISTINCT FROM 'object' THEN
    RAISE EXCEPTION 'identity_finalizer_document_invalid'
      USING ERRCODE = '22023';
  END IF;
  SELECT pg_catalog.count(*),
         pg_catalog.count(*) FILTER (
           WHERE key_name = ANY (ARRAY[
             'apply_decision_count','authoritative',
             'auto_expiry_effect_set_commitment','auto_expiry_effects',
             'contract_version','decision_count','decision_manifest_commitment',
             'finalization_attestation','finalization_commitment',
             'finalization_id','finalization_signing','lineage_set_commitment',
             'privacy_closure_set_commitment','privacy_closures','projections',
             'projection_manifest_commitment','receipt_count',
             'receipt_set_commitment','review_attestation','review_expires_at',
             'review_receipt_commitment','run_id','source_item_count',
             'source_manifest_commitment','verification_status'
           ]::text[])
         )
    INTO key_count, allowed_key_count
    FROM pg_catalog.jsonb_object_keys(document) AS keys(key_name);
  -- Type validation is deliberately its own statement. PostgreSQL may reorder
  -- boolean expressions inside one predicate, so casts and array operations
  -- must not share a predicate with the checks that make them safe.
  IF pg_catalog.jsonb_typeof(document -> 'projections')
           IS DISTINCT FROM 'array'
     OR pg_catalog.jsonb_typeof(document -> 'privacy_closures')
          IS DISTINCT FROM 'array'
     OR pg_catalog.jsonb_typeof(document -> 'auto_expiry_effects')
          IS DISTINCT FROM 'array'
     OR pg_catalog.jsonb_typeof(document -> 'review_attestation')
          IS DISTINCT FROM 'object'
     OR pg_catalog.jsonb_typeof(document -> 'finalization_signing')
          IS DISTINCT FROM 'object'
     OR pg_catalog.jsonb_typeof(document -> 'finalization_attestation')
          IS DISTINCT FROM 'object'
     OR EXISTS (
       SELECT 1
         FROM (VALUES
           ('contract_version'),
           ('verification_status'),
           ('finalization_id'),
           ('finalization_commitment'),
           ('decision_manifest_commitment'),
           ('receipt_set_commitment'),
           ('lineage_set_commitment'),
           ('privacy_closure_set_commitment'),
           ('auto_expiry_effect_set_commitment'),
           ('review_receipt_commitment'),
           ('projection_manifest_commitment'),
           ('review_expires_at'),
           ('run_id'),
           ('source_manifest_commitment')
         ) AS required_string(key_name)
        WHERE pg_catalog.jsonb_typeof(document -> required_string.key_name)
              IS DISTINCT FROM 'string'
     )
     OR EXISTS (
       SELECT 1
         FROM (VALUES
           ('apply_decision_count'),
           ('decision_count'),
           ('receipt_count'),
           ('source_item_count')
         ) AS required_count(key_name)
        WHERE pg_catalog.jsonb_typeof(document -> required_count.key_name)
              IS DISTINCT FROM 'number'
           OR document ->> required_count.key_name !~ '^(0|[1-9][0-9]*)$'
     )
     OR pg_catalog.jsonb_typeof(document -> 'authoritative')
           IS DISTINCT FROM 'boolean' THEN
    RAISE EXCEPTION 'identity_finalizer_document_contract_invalid'
      USING ERRCODE = '22023';
  END IF;
  -- E3 deliberately admits only a household-scale bounded batch while its
  -- exact-set verifier uses nested JSON scans. Larger reviewed runs remain
  -- candidate-only until a later verifier can prove a lower complexity bound
  -- without weakening privacy closure.
  IF key_count <> 25 OR allowed_key_count <> 25
     OR pg_catalog.jsonb_array_length(document -> 'projections') > 512
     OR pg_catalog.jsonb_array_length(document -> 'privacy_closures') > 512
     OR pg_catalog.jsonb_array_length(document -> 'auto_expiry_effects') > 512
     OR document ->> 'contract_version' IS DISTINCT FROM
          'reviewed-identity-migration-finalization-v1'
     OR document ->> 'verification_status' IS DISTINCT FROM
          'candidate_unverified'
     OR document -> 'authoritative' IS DISTINCT FROM 'false'::jsonb THEN
    RAISE EXCEPTION 'identity_finalizer_document_contract_invalid'
      USING ERRCODE = '22023';
  END IF;

  -- This no-op row update is deliberately the first MVCC operation. Every
  -- subject-tombstone INSERT/subject change executes the same write fence in
  -- its BEFORE trigger. Under SERIALIZABLE isolation, a concurrent committed
  -- tombstone therefore orders before this snapshot or forces SQLSTATE 40001.
  PERFORM privacy.lock_identity_semantic_write_fence();
  -- Supplemental same-purpose serialization; correctness does not depend on
  -- the advisory lock because tombstone writers do not acquire it.
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('home-agent:identity-finalizer:e3', 0)
  );
  wall_clock_now := pg_catalog.clock_timestamp();
  SELECT *
    INTO admission
    FROM operations.reviewed_identity_finalizer_admissions AS candidate
   WHERE candidate.admission_id = finalize_reviewed_identity_migration.admission_id
   FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'identity_finalizer_admission_missing'
      USING ERRCODE = '42501';
  END IF;
   IF actual_document_sha256 IS DISTINCT FROM admission.document_sha256
      OR pg_catalog.octet_length(finalizer_document)
           IS DISTINCT FROM admission.document_octets
      OR admission.contract_version IS DISTINCT FROM
           'reviewed-identity-finalizer-admission-v1'
      OR admission.verification_status IS DISTINCT FROM 'offline_verified'
      OR admission.verifier_bundle_digest IS DISTINCT FROM
           'be7c581a949f1b5cc3e51748066d4bae812a7b44bee431cd0f0d9c9936f2c266'
     THEN
    RAISE EXCEPTION 'identity_finalizer_admission_digest_mismatch'
      USING ERRCODE = '42501';
  END IF;

  exact_replay := admission.consumed_at IS NOT NULL;
  IF NOT exact_replay AND admission.expires_at <= wall_clock_now THEN
    RAISE EXCEPTION 'identity_finalizer_admission_expired'
      USING ERRCODE = '42501';
  END IF;

   IF document ->> 'run_id' IS DISTINCT FROM admission.run_id::text
      OR document ->> 'finalization_id'
           IS DISTINCT FROM admission.finalization_id::text
      OR document ->> 'run_id'
           IS DISTINCT FROM document ->> 'finalization_id'
      OR document ->> 'finalization_commitment' IS DISTINCT FROM
           admission.finalization_commitment
      OR document ->> 'decision_manifest_commitment' IS DISTINCT FROM
           admission.decision_manifest_commitment
      OR document ->> 'receipt_set_commitment' IS DISTINCT FROM
           admission.receipt_set_commitment
      OR document ->> 'lineage_set_commitment' IS DISTINCT FROM
           admission.lineage_set_commitment
      OR document ->> 'privacy_closure_set_commitment' IS DISTINCT FROM
           admission.privacy_closure_set_commitment
      OR document ->> 'auto_expiry_effect_set_commitment' IS DISTINCT FROM
           admission.auto_expiry_effect_set_commitment
      OR document ->> 'review_receipt_commitment' IS DISTINCT FROM
           admission.review_receipt_commitment THEN
    RAISE EXCEPTION 'identity_finalizer_document_admission_mismatch'
      USING ERRCODE = '42501';
  END IF;
   IF (
        SELECT pg_catalog.array_agg(key_name ORDER BY key_name)
         FROM pg_catalog.jsonb_object_keys(document -> 'review_attestation')
              AS keys(key_name)
     ) IS DISTINCT FROM ARRAY[
       'review_signature','signature_algorithm','signature_scope',
       'signing_key_fingerprint'
     ]::text[]
     OR (
       SELECT pg_catalog.array_agg(key_name ORDER BY key_name)
         FROM pg_catalog.jsonb_object_keys(document -> 'finalization_signing')
              AS keys(key_name)
     ) IS DISTINCT FROM ARRAY[
       'signature_algorithm','signature_scope','signing_key_fingerprint'
     ]::text[]
     OR (
       SELECT pg_catalog.array_agg(key_name ORDER BY key_name)
         FROM pg_catalog.jsonb_object_keys(
                document -> 'finalization_attestation'
              ) AS keys(key_name)
      ) IS DISTINCT FROM ARRAY[
        'finalization_signature','signature_algorithm','signature_scope',
        'signing_key_fingerprint'
      ]::text[]
      OR EXISTS (
        SELECT 1
          FROM (VALUES
            (document -> 'review_attestation', 'review_signature'),
            (document -> 'review_attestation', 'signature_algorithm'),
            (document -> 'review_attestation', 'signature_scope'),
            (document -> 'review_attestation', 'signing_key_fingerprint'),
            (document -> 'finalization_signing', 'signature_algorithm'),
            (document -> 'finalization_signing', 'signature_scope'),
            (document -> 'finalization_signing', 'signing_key_fingerprint'),
            (document -> 'finalization_attestation', 'finalization_signature'),
            (document -> 'finalization_attestation', 'signature_algorithm'),
            (document -> 'finalization_attestation', 'signature_scope'),
            (document -> 'finalization_attestation', 'signing_key_fingerprint')
          ) AS required_attestation(attestation, key_name)
         WHERE pg_catalog.jsonb_typeof(
                 required_attestation.attestation
                   -> required_attestation.key_name
               ) IS DISTINCT FROM 'string'
      )
      OR document -> 'review_attestation' ->> 'signature_algorithm'
           IS DISTINCT FROM 'ed25519'
      OR document -> 'review_attestation' ->> 'signature_scope'
           IS DISTINCT FROM
           'reviewed-identity-migration-review-v1'
      OR document -> 'review_attestation' ->> 'signing_key_fingerprint'
           IS DISTINCT FROM
           admission.review_signing_key_fingerprint
      OR (
        document -> 'review_attestation' ->> 'review_signature' IS NULL
        OR document -> 'review_attestation' ->> 'review_signature'
             !~ '^[0-9a-f]{128}$'
      )
      OR document -> 'finalization_signing' ->> 'signature_algorithm'
           IS DISTINCT FROM
           'ed25519'
      OR document -> 'finalization_signing' ->> 'signature_scope'
           IS DISTINCT FROM
           'reviewed-identity-migration-finalization-v1'
      OR document -> 'finalization_signing' ->> 'signing_key_fingerprint'
           IS DISTINCT FROM
           admission.finalization_signing_key_fingerprint
      OR document -> 'finalization_attestation' ->> 'signature_algorithm'
           IS DISTINCT FROM
           'ed25519'
      OR document -> 'finalization_attestation' ->> 'signature_scope'
           IS DISTINCT FROM
           'reviewed-identity-migration-finalization-v1'
      OR document -> 'finalization_attestation' ->> 'signing_key_fingerprint'
           IS DISTINCT FROM
           admission.finalization_signing_key_fingerprint
      OR (
        document -> 'finalization_attestation' ->> 'finalization_signature'
          IS NULL
        OR document -> 'finalization_attestation' ->> 'finalization_signature'
             !~ '^[0-9a-f]{128}$'
      ) THEN
    RAISE EXCEPTION 'identity_finalizer_attestation_mismatch'
      USING ERRCODE = '42501';
  END IF;

  SELECT *
    INTO migration_run
    FROM operations.reviewed_identity_migration_runs AS candidate
   WHERE candidate.run_id = admission.run_id
   FOR SHARE;
  IF NOT FOUND
     OR (NOT exact_replay AND migration_run.expires_at <= wall_clock_now)
     OR admission.expires_at > migration_run.expires_at
      OR (document ->> 'review_expires_at')::timestamptz IS DISTINCT FROM
           migration_run.expires_at
      OR migration_run.logical_source_manifest_commitment IS DISTINCT FROM
           document ->> 'source_manifest_commitment'
      OR migration_run.projection_manifest_commitment IS DISTINCT FROM
           document ->> 'projection_manifest_commitment'
      OR migration_run.review_receipt_commitment IS DISTINCT FROM
           admission.review_receipt_commitment
      OR migration_run.release_manifest_digest
           IS DISTINCT FROM admission.release_manifest_digest
      OR migration_run.migration_tool_bundle_digest IS DISTINCT FROM
           admission.migration_tool_bundle_digest
      OR migration_run.core_oci_manifest_digest
           IS DISTINCT FROM admission.core_oci_manifest_digest
      OR migration_run.core_schema_digest
           IS DISTINCT FROM admission.core_schema_digest
      OR migration_run.core_capability_digest
           IS DISTINCT FROM admission.core_capability_digest
      OR migration_run.policy_digest IS DISTINCT FROM admission.policy_digest
      OR migration_run.signature_algorithm IS DISTINCT FROM 'ed25519'
      OR migration_run.signing_key_fingerprint IS DISTINCT FROM
           admission.review_signing_key_fingerprint
      OR document -> 'review_attestation' ->> 'review_signature'
           IS DISTINCT FROM
           migration_run.review_signature THEN
    RAISE EXCEPTION 'identity_finalizer_live_run_mismatch'
      USING ERRCODE = '55000';
  END IF;
  IF EXISTS (
       SELECT 1
         FROM operations.reviewed_identity_migration_erasure_impacts AS impact
        WHERE impact.run_id = admission.run_id
     )
     OR EXISTS (
       SELECT 1
         FROM operations.legacy_identity_writer_evidence AS evidence
        WHERE evidence.run_id = admission.run_id
     )
     OR EXISTS (
       SELECT 1
         FROM operations.privacy_cutover_check_receipts AS check_receipt
        WHERE check_receipt.run_id = admission.run_id
     )
     OR EXISTS (
       SELECT 1
         FROM operations.semantic_authority_cutovers AS cutover
        WHERE cutover.run_id = admission.run_id
     ) THEN
    RAISE EXCEPTION 'identity_finalizer_run_not_candidate_only'
      USING ERRCODE = '55000';
  END IF;
  IF NOT exact_replay AND EXISTS (
       SELECT 1
         FROM operations.reviewed_identity_migration_finalizations AS existing
        WHERE existing.run_id = admission.run_id
           OR existing.finalization_id = admission.finalization_id
     ) THEN
    RAISE EXCEPTION 'identity_finalizer_finalization_collision'
      USING ERRCODE = '23505';
  END IF;

  SELECT pg_catalog.count(*), pg_catalog.count(DISTINCT ordinal),
         pg_catalog.min(ordinal), pg_catalog.max(ordinal)
    INTO source_count, source_distinct_count,
         source_min_ordinal, source_max_ordinal
    FROM operations.reviewed_identity_migration_source_items
   WHERE run_id = admission.run_id;
  SELECT pg_catalog.count(*), pg_catalog.count(DISTINCT ordinal),
         pg_catalog.min(ordinal), pg_catalog.max(ordinal)
    INTO decision_count, decision_distinct_count,
         decision_min_ordinal, decision_max_ordinal
    FROM operations.reviewed_identity_migration_decisions
   WHERE run_id = admission.run_id;
  IF source_count <> migration_run.source_item_count
     OR source_distinct_count <> source_count
     OR source_min_ordinal <> 0
     OR source_max_ordinal <> source_count - 1
     OR decision_count <> migration_run.decision_count
     OR decision_distinct_count <> decision_count
     OR decision_min_ordinal <> 0
     OR decision_max_ordinal <> decision_count - 1
     OR EXISTS (
       SELECT 1
         FROM operations.reviewed_identity_migration_source_items AS source
        WHERE source.run_id = admission.run_id
          AND NOT EXISTS (
            SELECT 1
              FROM operations.reviewed_identity_migration_decisions AS decision
             WHERE decision.run_id = source.run_id
               AND decision.source_item_id = source.source_item_id
          )
     )
     OR EXISTS (
       SELECT 1
         FROM operations.reviewed_identity_migration_source_items AS source
        WHERE source.run_id = admission.run_id
          AND source.source_table_kind = 'enrollments'
          AND NOT EXISTS (
            SELECT 1
              FROM operations.reviewed_identity_migration_decisions AS decision
             WHERE decision.run_id = source.run_id
               AND decision.source_item_id = source.source_item_id
          )
     ) THEN
    RAISE EXCEPTION 'identity_finalizer_manifest_density_invalid'
      USING ERRCODE = '55000';
  END IF;

  BEGIN
    IF (document ->> 'source_item_count')::integer <> source_count
       OR (document ->> 'decision_count')::integer <> decision_count THEN
      RAISE EXCEPTION 'identity_finalizer_manifest_count_mismatch'
        USING ERRCODE = '55000';
    END IF;
  EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range THEN
    RAISE EXCEPTION 'identity_finalizer_manifest_count_invalid'
      USING ERRCODE = '22023';
  END;
  projection_count := pg_catalog.jsonb_array_length(document -> 'projections');
  SELECT pg_catalog.count(*)
    INTO apply_decision_count
    FROM operations.reviewed_identity_migration_decisions
   WHERE run_id = admission.run_id
     AND disposition = 'apply';
  IF projection_count <> apply_decision_count
     OR (document ->> 'apply_decision_count')::integer <> apply_decision_count
     OR (document ->> 'receipt_count')::integer <> apply_decision_count THEN
    RAISE EXCEPTION 'identity_finalizer_apply_set_incomplete'
      USING ERRCODE = '55000';
  END IF;
  SELECT pg_catalog.count(DISTINCT projection ->> 'decision_id')
    INTO distinct_count
    FROM pg_catalog.jsonb_array_elements(document -> 'projections')
         AS projection;
  IF distinct_count <> projection_count THEN
    RAISE EXCEPTION 'identity_finalizer_projection_decision_duplicate'
      USING ERRCODE = '23505';
  END IF;
  SELECT pg_catalog.count(DISTINCT projection ->> 'receipt_id')
    INTO distinct_count
    FROM pg_catalog.jsonb_array_elements(document -> 'projections')
         AS projection;
  IF distinct_count <> projection_count THEN
    RAISE EXCEPTION 'identity_finalizer_projection_receipt_duplicate'
      USING ERRCODE = '23505';
  END IF;

  -- The person set is needed by the full per-projection verifier below.
  -- Establish the minimal object/string boundary before its first UUID cast;
  -- the common verifier subsequently proves the exact key and typed-record
  -- contract for every projection.
  IF EXISTS (
    SELECT 1
      FROM pg_catalog.jsonb_array_elements(document -> 'projections')
           AS item(projection)
     WHERE pg_catalog.jsonb_typeof(projection) IS DISTINCT FROM 'object'
        OR pg_catalog.jsonb_typeof(projection -> 'record')
             IS DISTINCT FROM 'object'
        OR pg_catalog.jsonb_typeof(projection -> 'decision_kind')
             IS DISTINCT FROM 'string'
        OR (
          projection ->> 'decision_kind' = 'person'
          AND pg_catalog.jsonb_typeof(projection -> 'record' -> 'person_id')
                IS DISTINCT FROM 'string'
        )
  ) THEN
    RAISE EXCEPTION 'identity_finalizer_projection_preflight_invalid'
      USING ERRCODE = '22023';
  END IF;

  SELECT coalesce(
           pg_catalog.array_agg(
             (projection -> 'record' ->> 'person_id')::uuid
             ORDER BY ordinal
           ),
           ARRAY[]::uuid[]
         )
    INTO person_ids
    FROM pg_catalog.jsonb_array_elements(document -> 'projections')
         WITH ORDINALITY AS item(projection, ordinal)
   WHERE projection ->> 'decision_kind' = 'person';
  person_count := pg_catalog.cardinality(person_ids);
  IF (projection_count > 0 AND person_count = 0)
     OR person_count <> (
       SELECT pg_catalog.count(DISTINCT person_id)
         FROM pg_catalog.unnest(person_ids) AS people(person_id)
     ) THEN
    RAISE EXCEPTION 'identity_finalizer_person_set_invalid'
      USING ERRCODE = '55000';
  END IF;

  -- Common verifier. Both first application and exact replay must traverse
  -- this entire input contract before either branch is selected. In
  -- particular, no unknown kind can fall through to the relationship writer
  -- and no replay can use stored rows to excuse malformed signed bytes.
  FOR target_projection IN
    SELECT projection
      FROM pg_catalog.jsonb_array_elements(document -> 'projections')
           WITH ORDINALITY AS item(projection, ordinal)
     ORDER BY ordinal
  LOOP
    IF pg_catalog.jsonb_typeof(target_projection) IS DISTINCT FROM 'object'
       OR pg_catalog.jsonb_typeof(target_projection -> 'record')
            IS DISTINCT FROM 'object'
       OR pg_catalog.jsonb_typeof(target_projection -> 'lineage')
            IS DISTINCT FROM 'object'
       OR (
         SELECT pg_catalog.array_agg(key_name ORDER BY key_name)
           FROM pg_catalog.jsonb_object_keys(target_projection)
                AS keys(key_name)
       ) IS DISTINCT FROM ARRAY[
         'candidate_commitment','decision_id','decision_kind','lineage',
         'projection_commitment','projection_ref_commitment',
         'projection_table_kind','receipt_commitment','receipt_id','record'
       ]::text[]
       OR EXISTS (
         SELECT 1
           FROM (VALUES
             ('candidate_commitment'),
             ('decision_id'),
             ('decision_kind'),
             ('projection_commitment'),
             ('projection_ref_commitment'),
             ('projection_table_kind'),
             ('receipt_commitment'),
             ('receipt_id')
           ) AS required_projection_string(key_name)
          WHERE pg_catalog.jsonb_typeof(
                  target_projection -> required_projection_string.key_name
                ) IS DISTINCT FROM 'string'
       ) THEN
      RAISE EXCEPTION 'identity_finalizer_projection_shape_invalid'
        USING ERRCODE = '22023';
    END IF;
    target_kind := target_projection ->> 'decision_kind';
    target_projection_table := target_projection ->> 'projection_table_kind';
    target_decision_id := (target_projection ->> 'decision_id')::uuid;
    target_receipt_id := (target_projection ->> 'receipt_id')::uuid;
    target_record := target_projection -> 'record';
    target_lineage := target_projection -> 'lineage';

    IF target_kind NOT IN (
         'person','person_status','alias','recognition_binding',
         'privacy_directive','legacy_role_candidate',
         'legacy_relationship_candidate'
       )
       OR (target_kind IN ('person','person_status')
          AND target_projection_table <> 'identity.people')
       OR (target_kind = 'alias'
          AND target_projection_table <> 'identity.aliases')
       OR (target_kind = 'recognition_binding'
          AND target_projection_table <>
                'identity.external_recognition_bindings')
       OR (target_kind = 'privacy_directive'
          AND target_projection_table <> 'identity.privacy_directives')
       OR (target_kind = 'legacy_role_candidate'
          AND target_projection_table <> 'identity.legacy_role_labels')
       OR (target_kind = 'legacy_relationship_candidate'
          AND target_projection_table <>
                'identity.legacy_relationship_candidates')
       OR target_projection ->> 'candidate_commitment'
            !~ '^[0-9a-f]{64}$'
       OR target_projection ->> 'projection_commitment'
            !~ '^[0-9a-f]{64}$'
       OR target_projection ->> 'projection_ref_commitment'
            !~ '^[0-9a-f]{64}$'
       OR target_projection ->> 'receipt_commitment'
            !~ '^[0-9a-f]{64}$'
       OR NOT EXISTS (
         SELECT 1
           FROM operations.reviewed_identity_migration_decisions AS decision
          WHERE decision.run_id = admission.run_id
            AND decision.decision_id = target_decision_id
            AND decision.decision_kind = target_kind
            AND decision.disposition = 'apply'
            AND decision.candidate_commitment =
                  target_projection ->> 'candidate_commitment'
       ) THEN
      RAISE EXCEPTION 'identity_finalizer_projection_contract_invalid'
        USING ERRCODE = '55000';
    END IF;

    IF (
         SELECT pg_catalog.array_agg(key_name ORDER BY key_name)
           FROM pg_catalog.jsonb_object_keys(target_lineage)
                AS keys(key_name)
       ) IS DISTINCT FROM ARRAY[
         'decision_id','decision_kind','lineage_commitment','lineage_id',
         'projection_id','projection_ref_commitment','projection_table_kind',
         'receipt_id','run_id','subjects'
       ]::text[]
       OR pg_catalog.jsonb_typeof(target_lineage -> 'subjects')
            IS DISTINCT FROM 'array'
       OR EXISTS (
         SELECT 1
           FROM (VALUES
             ('decision_id'),
             ('decision_kind'),
             ('lineage_commitment'),
             ('lineage_id'),
             ('projection_id'),
             ('projection_ref_commitment'),
             ('projection_table_kind'),
             ('receipt_id'),
             ('run_id')
           ) AS required_lineage_string(key_name)
          WHERE pg_catalog.jsonb_typeof(
                  target_lineage -> required_lineage_string.key_name
                ) IS DISTINCT FROM 'string'
       )
       OR target_lineage ->> 'lineage_id'
            IS DISTINCT FROM target_receipt_id::text
       OR target_lineage ->> 'run_id'
            IS DISTINCT FROM admission.run_id::text
       OR target_lineage ->> 'receipt_id'
            IS DISTINCT FROM target_receipt_id::text
       OR target_lineage ->> 'decision_id'
            IS DISTINCT FROM target_decision_id::text
       OR target_lineage ->> 'decision_kind' IS DISTINCT FROM target_kind
       OR target_lineage ->> 'projection_table_kind' IS DISTINCT FROM
            target_projection_table
       OR target_lineage ->> 'projection_ref_commitment' IS DISTINCT FROM
            target_projection ->> 'projection_ref_commitment'
       OR (
         target_lineage ->> 'lineage_commitment' IS NULL
         OR target_lineage ->> 'lineage_commitment' !~ '^[0-9a-f]{64}$'
       )
       OR EXISTS (
         SELECT 1
           FROM pg_catalog.jsonb_array_elements(target_lineage -> 'subjects')
                AS item(subject)
          WHERE pg_catalog.jsonb_typeof(subject) IS DISTINCT FROM 'object'
             OR (
               SELECT pg_catalog.array_agg(key_name ORDER BY key_name)
                 FROM pg_catalog.jsonb_object_keys(subject) AS keys(key_name)
              ) IS DISTINCT FROM ARRAY['person_id','subject_role']::text[]
             OR pg_catalog.jsonb_typeof(subject -> 'person_id')
                  IS DISTINCT FROM 'string'
             OR pg_catalog.jsonb_typeof(subject -> 'subject_role')
                  IS DISTINCT FROM 'string'
       )
       OR (
         SELECT pg_catalog.count(*)
           FROM pg_catalog.jsonb_array_elements(target_lineage -> 'subjects')
                AS item(subject)
       ) <> (
         SELECT pg_catalog.count(DISTINCT (
                  subject ->> 'person_id', subject ->> 'subject_role'
                ))
           FROM pg_catalog.jsonb_array_elements(target_lineage -> 'subjects')
                AS item(subject)
       ) THEN
      RAISE EXCEPTION 'identity_finalizer_lineage_contract_invalid'
        USING ERRCODE = '55000';
    END IF;
    target_projection_id := (target_lineage ->> 'projection_id')::uuid;

    IF target_kind = 'person' THEN
      IF (
           SELECT pg_catalog.array_agg(key_name ORDER BY key_name)
             FROM pg_catalog.jsonb_object_keys(target_record)
                  AS keys(key_name)
          ) IS DISTINCT FROM ARRAY[
            'display_name','legacy_source_ref','legacy_source_sha256',
            'legacy_source_version','person_id','privacy_scope','pronouns'
          ]::text[]
          OR EXISTS (
            SELECT 1
              FROM (VALUES
                ('display_name'),
                ('legacy_source_ref'),
                ('legacy_source_sha256'),
                ('person_id'),
                ('privacy_scope')
              ) AS required_person_string(key_name)
             WHERE pg_catalog.jsonb_typeof(
                     target_record -> required_person_string.key_name
                   ) IS DISTINCT FROM 'string'
          )
          OR target_record ->> 'privacy_scope' IS DISTINCT FROM 'private'
          OR pg_catalog.jsonb_typeof(target_record -> 'legacy_source_version')
               IS DISTINCT FROM 'number'
          OR target_record ->> 'legacy_source_version'
               !~ '^(0|[1-9][0-9]*)$'
          OR pg_catalog.btrim(target_record ->> 'display_name') = ''
         OR pg_catalog.char_length(target_record ->> 'display_name') > 255
         OR pg_catalog.jsonb_typeof(target_record -> 'pronouns')
              NOT IN ('string','null')
         OR (
           pg_catalog.jsonb_typeof(target_record -> 'pronouns') = 'string'
           AND (
             pg_catalog.btrim(target_record ->> 'pronouns') = ''
             OR pg_catalog.char_length(target_record ->> 'pronouns') > 64
           )
         )
         OR pg_catalog.btrim(target_record ->> 'legacy_source_ref') = ''
         OR pg_catalog.char_length(target_record ->> 'legacy_source_ref') > 255
         OR target_record ->> 'legacy_source_sha256'
              !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'identity_finalizer_person_record_invalid'
          USING ERRCODE = '22023';
      END IF;
    ELSIF target_kind = 'person_status' THEN
      IF (
           SELECT pg_catalog.array_agg(key_name ORDER BY key_name)
             FROM pg_catalog.jsonb_object_keys(target_record)
                  AS keys(key_name)
          ) IS DISTINCT FROM ARRAY[
            'person_id','source_ref','source_snapshot_sha256','source_version',
            'status'
          ]::text[]
          OR EXISTS (
            SELECT 1
              FROM (VALUES
                ('person_id'),
                ('source_ref'),
                ('source_snapshot_sha256'),
                ('status')
              ) AS required_status_string(key_name)
             WHERE pg_catalog.jsonb_typeof(
                     target_record -> required_status_string.key_name
                   ) IS DISTINCT FROM 'string'
          )
          OR target_record ->> 'status' IS DISTINCT FROM 'archived'
          OR pg_catalog.jsonb_typeof(target_record -> 'source_version')
               IS DISTINCT FROM 'number'
          OR target_record ->> 'source_version' !~ '^(0|[1-9][0-9]*)$'
         OR pg_catalog.btrim(target_record ->> 'source_ref') = ''
         OR pg_catalog.char_length(target_record ->> 'source_ref') > 255
         OR target_record ->> 'source_snapshot_sha256'
              !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'identity_finalizer_status_record_invalid'
          USING ERRCODE = '22023';
      END IF;
    ELSIF target_kind = 'alias' THEN
      IF (
           SELECT pg_catalog.array_agg(key_name ORDER BY key_name)
             FROM pg_catalog.jsonb_object_keys(target_record)
                  AS keys(key_name)
          ) IS DISTINCT FROM ARRAY[
            'alias','alias_id','alias_kind','normalized_alias','person_id',
            'source_ref','source_snapshot_sha256','source_version'
          ]::text[]
          OR EXISTS (
            SELECT 1
              FROM (VALUES
                ('alias'),
                ('alias_id'),
                ('alias_kind'),
                ('normalized_alias'),
                ('person_id'),
                ('source_ref'),
                ('source_snapshot_sha256')
              ) AS required_alias_string(key_name)
             WHERE pg_catalog.jsonb_typeof(
                     target_record -> required_alias_string.key_name
                   ) IS DISTINCT FROM 'string'
          )
          OR target_record ->> 'alias_id'
               IS DISTINCT FROM target_decision_id::text
          OR target_record ->> 'alias_kind' NOT IN ('name','nickname')
         OR pg_catalog.btrim(target_record ->> 'alias') = ''
         OR pg_catalog.char_length(target_record ->> 'alias') > 255
         OR pg_catalog.btrim(target_record ->> 'normalized_alias') = ''
         OR pg_catalog.char_length(target_record ->> 'normalized_alias') > 255
          OR pg_catalog.jsonb_typeof(target_record -> 'source_version')
               IS DISTINCT FROM 'number'
          OR target_record ->> 'source_version' !~ '^(0|[1-9][0-9]*)$'
         OR pg_catalog.btrim(target_record ->> 'source_ref') = ''
         OR pg_catalog.char_length(target_record ->> 'source_ref') > 255
         OR target_record ->> 'source_snapshot_sha256'
              !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'identity_finalizer_alias_record_invalid'
          USING ERRCODE = '22023';
      END IF;
    ELSIF target_kind = 'recognition_binding' THEN
      IF (
           SELECT pg_catalog.array_agg(key_name ORDER BY key_name)
             FROM pg_catalog.jsonb_object_keys(target_record)
                  AS keys(key_name)
          ) IS DISTINCT FROM ARRAY[
            'binding_id','external_id','external_system','person_id',
            'source_ref','source_snapshot_sha256','source_version','status'
          ]::text[]
          OR EXISTS (
            SELECT 1
              FROM (VALUES
                ('binding_id'),
                ('external_id'),
                ('external_system'),
                ('person_id'),
                ('source_ref'),
                ('source_snapshot_sha256'),
                ('status')
              ) AS required_recognition_string(key_name)
             WHERE pg_catalog.jsonb_typeof(
                     target_record -> required_recognition_string.key_name
                   ) IS DISTINCT FROM 'string'
          )
          OR target_record ->> 'binding_id'
               IS DISTINCT FROM target_decision_id::text
          OR target_record ->> 'external_system' IS DISTINCT FROM 'frigate'
         OR target_record ->> 'status' NOT IN ('active','retired')
         OR pg_catalog.btrim(target_record ->> 'external_id') = ''
         OR pg_catalog.char_length(target_record ->> 'external_id') > 255
          OR pg_catalog.jsonb_typeof(target_record -> 'source_version')
               IS DISTINCT FROM 'number'
          OR target_record ->> 'source_version' !~ '^(0|[1-9][0-9]*)$'
         OR pg_catalog.btrim(target_record ->> 'source_ref') = ''
         OR pg_catalog.char_length(target_record ->> 'source_ref') > 255
         OR target_record ->> 'source_snapshot_sha256'
              !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'identity_finalizer_recognition_record_invalid'
          USING ERRCODE = '22023';
      END IF;
    ELSIF target_kind = 'privacy_directive' THEN
      IF (
           SELECT pg_catalog.array_agg(key_name ORDER BY key_name)
             FROM pg_catalog.jsonb_object_keys(target_record)
                  AS keys(key_name)
          ) IS DISTINCT FROM ARRAY[
            'directive','directive_id','enabled','expires_at','person_id',
            'source_ref','source_snapshot_sha256','source_version'
          ]::text[]
          OR EXISTS (
            SELECT 1
              FROM (VALUES
                ('directive'),
                ('directive_id'),
                ('person_id'),
                ('source_ref'),
                ('source_snapshot_sha256')
              ) AS required_privacy_string(key_name)
             WHERE pg_catalog.jsonb_typeof(
                     target_record -> required_privacy_string.key_name
                   ) IS DISTINCT FROM 'string'
          )
          OR pg_catalog.jsonb_typeof(target_record -> 'enabled')
               IS DISTINCT FROM 'boolean'
          OR (
            target_record ->> 'directive' = 'auto_expire'
            AND pg_catalog.jsonb_typeof(target_record -> 'expires_at')
                  IS DISTINCT FROM 'string'
          )
          OR (
            target_record ->> 'directive' <> 'auto_expire'
            AND target_record -> 'expires_at' IS DISTINCT FROM 'null'::jsonb
          )
          OR pg_catalog.jsonb_typeof(target_record -> 'source_version')
               IS DISTINCT FROM 'number' THEN
        RAISE EXCEPTION 'identity_finalizer_privacy_record_invalid'
          USING ERRCODE = '22023';
      END IF;
      IF target_record ->> 'directive_id'
           IS DISTINCT FROM target_decision_id::text
         OR target_record ->> 'directive' NOT IN (
           'do_not_track','ignored','silent','private','auto_expire'
         )
         OR target_record -> 'enabled' IS DISTINCT FROM 'true'::jsonb
         OR (
           target_record ->> 'directive' = 'auto_expire'
           AND (
             NOT pg_catalog.isfinite(
               (target_record ->> 'expires_at')::timestamptz
             )
             OR (
               NOT exact_replay
               AND (
                 (target_record ->> 'expires_at')::timestamptz <=
                   wall_clock_now
                 OR (target_record ->> 'expires_at')::timestamptz >
                      wall_clock_now + interval '10 years'
               )
             )
           )
         )
          OR target_record ->> 'source_version' !~ '^(0|[1-9][0-9]*)$'
         OR pg_catalog.btrim(target_record ->> 'source_ref') = ''
         OR pg_catalog.char_length(target_record ->> 'source_ref') > 255
         OR target_record ->> 'source_snapshot_sha256'
              !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'identity_finalizer_privacy_record_invalid'
          USING ERRCODE = '22023';
      END IF;
    ELSIF target_kind = 'legacy_role_candidate' THEN
      IF (
           SELECT pg_catalog.array_agg(key_name ORDER BY key_name)
             FROM pg_catalog.jsonb_object_keys(target_record)
                  AS keys(key_name)
          ) IS DISTINCT FROM ARRAY[
            'label_id','person_id','perspective','role_label','source_ref',
            'source_snapshot_sha256','source_version'
          ]::text[]
          OR EXISTS (
            SELECT 1
              FROM (VALUES
                ('label_id'),
                ('perspective'),
                ('person_id'),
                ('role_label'),
                ('source_ref'),
                ('source_snapshot_sha256')
              ) AS required_role_string(key_name)
             WHERE pg_catalog.jsonb_typeof(
                     target_record -> required_role_string.key_name
                   ) IS DISTINCT FROM 'string'
          )
          OR target_record ->> 'label_id'
               IS DISTINCT FROM target_decision_id::text
          OR target_record ->> 'perspective' IS DISTINCT FROM 'unknown'
          OR pg_catalog.btrim(target_record ->> 'role_label') = ''
         OR pg_catalog.char_length(target_record ->> 'role_label') > 64
         OR pg_catalog.jsonb_typeof(target_record -> 'source_version')
              NOT IN ('number','null')
          OR (
            pg_catalog.jsonb_typeof(target_record -> 'source_version') = 'number'
            AND target_record ->> 'source_version'
                  !~ '^(0|[1-9][0-9]*)$'
         )
         OR pg_catalog.btrim(target_record ->> 'source_ref') = ''
         OR pg_catalog.char_length(target_record ->> 'source_ref') > 255
         OR target_record ->> 'source_snapshot_sha256'
              !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'identity_finalizer_role_record_invalid'
          USING ERRCODE = '22023';
      END IF;
    ELSE
      IF (
           SELECT pg_catalog.array_agg(key_name ORDER BY key_name)
             FROM pg_catalog.jsonb_object_keys(target_record)
                  AS keys(key_name)
          ) IS DISTINCT FROM ARRAY[
            'authoritative','candidate_id','from_person_id','perspective',
            'relationship_label','relationship_status','source_ref',
            'source_snapshot_sha256','to_person_id'
          ]::text[]
          OR EXISTS (
            SELECT 1
              FROM (VALUES
                ('candidate_id'),
                ('from_person_id'),
                ('perspective'),
                ('relationship_label'),
                ('relationship_status'),
                ('source_ref'),
                ('source_snapshot_sha256'),
                ('to_person_id')
              ) AS required_relationship_string(key_name)
             WHERE pg_catalog.jsonb_typeof(
                     target_record -> required_relationship_string.key_name
                   ) IS DISTINCT FROM 'string'
          )
          OR pg_catalog.jsonb_typeof(target_record -> 'authoritative')
               IS DISTINCT FROM 'boolean'
          OR target_record ->> 'candidate_id'
               IS DISTINCT FROM target_decision_id::text
          OR target_record ->> 'perspective' IS DISTINCT FROM 'unknown'
          OR target_record -> 'authoritative'
               IS DISTINCT FROM 'false'::jsonb
         OR target_record ->> 'relationship_status'
              NOT IN ('active','ended','paused')
         OR pg_catalog.btrim(target_record ->> 'relationship_label') = ''
         OR pg_catalog.char_length(
              target_record ->> 'relationship_label'
            ) > 64
         OR pg_catalog.btrim(target_record ->> 'source_ref') = ''
         OR pg_catalog.char_length(target_record ->> 'source_ref') > 255
         OR target_record ->> 'source_snapshot_sha256'
              !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'identity_finalizer_relationship_record_invalid'
          USING ERRCODE = '22023';
      END IF;
    END IF;

    IF target_kind IN (
         'person','person_status','alias','recognition_binding',
         'privacy_directive','legacy_role_candidate'
       ) THEN
      target_person_id := (target_record ->> 'person_id')::uuid;
      IF NOT target_person_id = ANY (person_ids)
         OR pg_catalog.jsonb_array_length(target_lineage -> 'subjects') <> 1
         OR target_lineage -> 'subjects' -> 0 ->> 'person_id' <>
              target_person_id::text
         OR target_lineage -> 'subjects' -> 0 ->> 'subject_role' <> 'primary'
         OR target_projection_id <> (CASE target_kind
              WHEN 'person' THEN target_person_id
              WHEN 'person_status' THEN target_person_id
              WHEN 'alias' THEN (target_record ->> 'alias_id')::uuid
              WHEN 'recognition_binding'
                THEN (target_record ->> 'binding_id')::uuid
              WHEN 'privacy_directive'
                THEN (target_record ->> 'directive_id')::uuid
              WHEN 'legacy_role_candidate'
                THEN (target_record ->> 'label_id')::uuid
            END) THEN
        RAISE EXCEPTION 'identity_finalizer_projection_subject_invalid'
          USING ERRCODE = '55000';
      END IF;
    ELSE
      IF (target_record ->> 'from_person_id')::uuid =
           (target_record ->> 'to_person_id')::uuid
         OR NOT (target_record ->> 'from_person_id')::uuid = ANY (person_ids)
         OR NOT (target_record ->> 'to_person_id')::uuid = ANY (person_ids)
         OR target_projection_id <>
              (target_record ->> 'candidate_id')::uuid
         OR pg_catalog.jsonb_array_length(target_lineage -> 'subjects') <> 2
         OR target_lineage -> 'subjects' -> 0 ->> 'person_id' <>
              target_record ->> 'from_person_id'
         OR target_lineage -> 'subjects' -> 0 ->> 'subject_role' <> 'from'
         OR target_lineage -> 'subjects' -> 1 ->> 'person_id' <>
              target_record ->> 'to_person_id'
         OR target_lineage -> 'subjects' -> 1 ->> 'subject_role' <> 'to' THEN
        RAISE EXCEPTION 'identity_finalizer_relationship_subject_invalid'
          USING ERRCODE = '55000';
      END IF;
    END IF;
  END LOOP;

  -- The shared fence precedes this all-subject block check, so a concurrent
  -- E2 tombstone either becomes visible here or forces a serialization retry.
  IF EXISTS (
    SELECT 1
      FROM pg_catalog.unnest(person_ids) AS candidate(person_id)
     WHERE privacy.identity_person_is_blocked(candidate.person_id)
  ) THEN
    RAISE EXCEPTION 'identity_erasure_blocked'
      USING ERRCODE = '42501';
  END IF;

  IF (
    SELECT pg_catalog.count(*)
      FROM pg_catalog.jsonb_array_elements(document -> 'projections') AS item(p)
     WHERE p ->> 'decision_kind' = 'person_status'
  ) <> (
    SELECT pg_catalog.count(DISTINCT p -> 'record' ->> 'person_id')
      FROM pg_catalog.jsonb_array_elements(document -> 'projections') AS item(p)
     WHERE p ->> 'decision_kind' = 'person_status'
  ) OR (
    SELECT pg_catalog.count(*)
      FROM pg_catalog.jsonb_array_elements(document -> 'projections') AS item(p)
     WHERE p ->> 'decision_kind' = 'privacy_directive'
  ) <> (
    SELECT pg_catalog.count(DISTINCT (
             p -> 'record' ->> 'person_id',
             p -> 'record' ->> 'directive'
           ))
      FROM pg_catalog.jsonb_array_elements(document -> 'projections') AS item(p)
     WHERE p ->> 'decision_kind' = 'privacy_directive'
  ) THEN
    RAISE EXCEPTION 'identity_finalizer_privacy_cardinality_invalid'
      USING ERRCODE = '55000';
  END IF;
  IF EXISTS (
    SELECT 1
      FROM pg_catalog.jsonb_array_elements(document -> 'projections') AS person(p)
     WHERE p ->> 'decision_kind' = 'person'
       AND EXISTS (
         SELECT 1
           FROM pg_catalog.jsonb_array_elements(document -> 'projections')
                AS directive(d)
          WHERE d ->> 'decision_kind' = 'privacy_directive'
            AND d -> 'record' ->> 'person_id' =
                  p -> 'record' ->> 'person_id'
            AND d -> 'record' ->> 'directive' = 'ignored'
       )
       AND (
         p -> 'record' ->> 'display_name' <> 'Suppressed legacy identity'
         OR p -> 'record' -> 'pronouns' <> 'null'::jsonb
         OR EXISTS (
           SELECT 1
             FROM pg_catalog.jsonb_array_elements(document -> 'projections')
                  AS prohibited(x)
            WHERE x ->> 'decision_kind' IN (
                    'alias','recognition_binding','legacy_role_candidate',
                    'legacy_relationship_candidate'
                  )
              AND (
                x -> 'record' ->> 'person_id' =
                  p -> 'record' ->> 'person_id'
                OR x -> 'record' ->> 'from_person_id' =
                  p -> 'record' ->> 'person_id'
                OR x -> 'record' ->> 'to_person_id' =
                  p -> 'record' ->> 'person_id'
              )
         )
       )
  ) OR EXISTS (
    SELECT 1
      FROM pg_catalog.jsonb_array_elements(document -> 'projections') AS binding(b)
     WHERE b ->> 'decision_kind' = 'recognition_binding'
       AND b -> 'record' ->> 'status' = 'active'
       AND EXISTS (
         SELECT 1
           FROM pg_catalog.jsonb_array_elements(document -> 'projections')
                AS directive(d)
          WHERE d ->> 'decision_kind' = 'privacy_directive'
            AND d -> 'record' ->> 'person_id' =
                  b -> 'record' ->> 'person_id'
            AND d -> 'record' ->> 'directive' IN ('ignored','do_not_track')
       )
  ) THEN
    RAISE EXCEPTION 'identity_finalizer_privacy_closure_invalid'
      USING ERRCODE = '55000';
  END IF;

  IF pg_catalog.jsonb_array_length(document -> 'privacy_closures') <>
       person_count
     OR (
       SELECT pg_catalog.count(DISTINCT closure ->> 'person_id')
         FROM pg_catalog.jsonb_array_elements(document -> 'privacy_closures')
              AS item(closure)
     ) <> person_count THEN
    RAISE EXCEPTION 'identity_finalizer_privacy_closure_set_invalid'
      USING ERRCODE = '55000';
  END IF;
  FOR target_closure IN
    SELECT closure
      FROM pg_catalog.jsonb_array_elements(document -> 'privacy_closures')
           WITH ORDINALITY AS item(closure, ordinal)
     ORDER BY ordinal
  LOOP
    IF pg_catalog.jsonb_typeof(target_closure) IS DISTINCT FROM 'object'
       OR (
         SELECT pg_catalog.array_agg(key_name ORDER BY key_name)
           FROM pg_catalog.jsonb_object_keys(target_closure)
                AS keys(key_name)
       ) IS DISTINCT FROM ARRAY[
         'directives','do_not_track','ignored','person_decision_id',
         'person_id','person_receipt_id','privacy_closure_commitment'
       ]::text[]
       OR pg_catalog.jsonb_typeof(target_closure -> 'directives')
            IS DISTINCT FROM 'array'
       OR pg_catalog.jsonb_typeof(target_closure -> 'ignored')
            IS DISTINCT FROM 'boolean'
       OR pg_catalog.jsonb_typeof(target_closure -> 'do_not_track')
            IS DISTINCT FROM 'boolean'
       OR EXISTS (
         SELECT 1
           FROM (VALUES
             ('person_decision_id'),
             ('person_id'),
             ('person_receipt_id'),
             ('privacy_closure_commitment')
           ) AS required_closure_string(key_name)
          WHERE pg_catalog.jsonb_typeof(
                  target_closure -> required_closure_string.key_name
                ) IS DISTINCT FROM 'string'
       )
       OR (
         target_closure ->> 'privacy_closure_commitment' IS NULL
         OR target_closure ->> 'privacy_closure_commitment'
              !~ '^[0-9a-f]{64}$'
       ) THEN
      RAISE EXCEPTION 'identity_finalizer_privacy_closure_shape_invalid'
        USING ERRCODE = '22023';
    END IF;
    target_person_id := (target_closure ->> 'person_id')::uuid;
    IF NOT target_person_id = ANY (person_ids)
       OR NOT EXISTS (
         SELECT 1
           FROM pg_catalog.jsonb_array_elements(document -> 'projections')
                AS item(p)
          WHERE p ->> 'decision_kind' = 'person'
            AND p -> 'record' ->> 'person_id' = target_person_id::text
            AND p ->> 'decision_id' =
                  target_closure ->> 'person_decision_id'
            AND p ->> 'receipt_id' =
                  target_closure ->> 'person_receipt_id'
       )
       OR (target_closure -> 'ignored')::boolean IS DISTINCT FROM EXISTS (
         SELECT 1
           FROM pg_catalog.jsonb_array_elements(document -> 'projections')
                AS item(p)
          WHERE p ->> 'decision_kind' = 'privacy_directive'
            AND p -> 'record' ->> 'person_id' = target_person_id::text
            AND p -> 'record' ->> 'directive' = 'ignored'
       )
       OR (target_closure -> 'do_not_track')::boolean IS DISTINCT FROM EXISTS (
         SELECT 1
           FROM pg_catalog.jsonb_array_elements(document -> 'projections')
                AS item(p)
          WHERE p ->> 'decision_kind' = 'privacy_directive'
            AND p -> 'record' ->> 'person_id' = target_person_id::text
            AND p -> 'record' ->> 'directive' = 'do_not_track'
       )
       OR pg_catalog.jsonb_array_length(target_closure -> 'directives') <> (
         SELECT pg_catalog.count(*)
           FROM pg_catalog.jsonb_array_elements(document -> 'projections')
                AS item(p)
          WHERE p ->> 'decision_kind' = 'privacy_directive'
            AND p -> 'record' ->> 'person_id' = target_person_id::text
       )
       OR EXISTS (
         SELECT 1
           FROM pg_catalog.jsonb_array_elements(target_closure -> 'directives')
                AS item(directive)
          WHERE pg_catalog.jsonb_typeof(directive) IS DISTINCT FROM 'object'
             OR (
               SELECT pg_catalog.array_agg(key_name ORDER BY key_name)
                 FROM pg_catalog.jsonb_object_keys(directive)
                      AS keys(key_name)
              ) IS DISTINCT FROM ARRAY[
                'decision_id','directive','directive_id','enabled','expires_at',
                'projection_ref_commitment','receipt_id'
              ]::text[]
              OR EXISTS (
                SELECT 1
                  FROM (VALUES
                    ('decision_id'),
                    ('directive'),
                    ('directive_id'),
                    ('projection_ref_commitment'),
                    ('receipt_id')
                  ) AS required_closure_directive_string(key_name)
                 WHERE pg_catalog.jsonb_typeof(
                         directive
                           -> required_closure_directive_string.key_name
                       ) IS DISTINCT FROM 'string'
              )
              OR pg_catalog.jsonb_typeof(directive -> 'enabled')
                   IS DISTINCT FROM 'boolean'
              OR directive -> 'enabled' IS DISTINCT FROM 'true'::jsonb
              OR (
                directive ->> 'directive' = 'auto_expire'
                AND pg_catalog.jsonb_typeof(directive -> 'expires_at')
                      IS DISTINCT FROM 'string'
              )
              OR (
                directive ->> 'directive' <> 'auto_expire'
                AND directive -> 'expires_at' IS DISTINCT FROM 'null'::jsonb
              )
              OR NOT EXISTS (
               SELECT 1
                 FROM pg_catalog.jsonb_array_elements(
                        document -> 'projections'
                      ) AS item(p)
                WHERE p ->> 'decision_kind' = 'privacy_directive'
                  AND p -> 'record' ->> 'person_id' = target_person_id::text
                  AND p -> 'record' ->> 'directive_id' =
                        directive ->> 'directive_id'
                  AND p ->> 'decision_id' = directive ->> 'decision_id'
                  AND p ->> 'receipt_id' = directive ->> 'receipt_id'
                  AND p -> 'record' ->> 'directive' =
                        directive ->> 'directive'
                  AND p -> 'record' -> 'enabled' = directive -> 'enabled'
                  AND p -> 'record' -> 'expires_at' =
                        directive -> 'expires_at'
                  AND p ->> 'projection_ref_commitment' =
                        directive ->> 'projection_ref_commitment'
             )
       )
       OR EXISTS (
         SELECT 1
           FROM pg_catalog.jsonb_array_elements(document -> 'projections')
                AS item(p)
          WHERE p ->> 'decision_kind' = 'privacy_directive'
            AND p -> 'record' ->> 'person_id' = target_person_id::text
            AND (
              SELECT pg_catalog.count(*)
                FROM pg_catalog.jsonb_array_elements(
                       target_closure -> 'directives'
                     ) AS item(directive)
               WHERE directive ->> 'directive_id' =
                       p -> 'record' ->> 'directive_id'
                 AND directive ->> 'decision_id' = p ->> 'decision_id'
                 AND directive ->> 'receipt_id' = p ->> 'receipt_id'
                 AND directive ->> 'directive' =
                       p -> 'record' ->> 'directive'
                 AND directive -> 'enabled' = p -> 'record' -> 'enabled'
                 AND directive -> 'expires_at' =
                       p -> 'record' -> 'expires_at'
                 AND directive ->> 'projection_ref_commitment' =
                       p ->> 'projection_ref_commitment'
            ) <> 1
       ) THEN
      RAISE EXCEPTION 'identity_finalizer_privacy_closure_exact_set_invalid'
        USING ERRCODE = '55000';
    END IF;
  END LOOP;

  IF pg_catalog.jsonb_array_length(document -> 'auto_expiry_effects') <> (
       SELECT pg_catalog.count(*)
         FROM pg_catalog.jsonb_array_elements(document -> 'projections')
              AS item(p)
        WHERE p ->> 'decision_kind' = 'privacy_directive'
          AND p -> 'record' ->> 'directive' = 'auto_expire'
     )
     OR (
       SELECT pg_catalog.count(DISTINCT effect ->> 'schedule_id')
         FROM pg_catalog.jsonb_array_elements(document -> 'auto_expiry_effects')
              AS item(effect)
     ) <> pg_catalog.jsonb_array_length(document -> 'auto_expiry_effects')
     OR (
       SELECT pg_catalog.count(DISTINCT effect ->> 'outbox_id')
         FROM pg_catalog.jsonb_array_elements(document -> 'auto_expiry_effects')
              AS item(effect)
     ) <> pg_catalog.jsonb_array_length(document -> 'auto_expiry_effects')
     OR (
       SELECT pg_catalog.count(DISTINCT effect ->> 'directive_id')
         FROM pg_catalog.jsonb_array_elements(document -> 'auto_expiry_effects')
              AS item(effect)
     ) <> pg_catalog.jsonb_array_length(document -> 'auto_expiry_effects')
     OR (
       SELECT pg_catalog.count(DISTINCT effect ->> 'effect_commitment')
         FROM pg_catalog.jsonb_array_elements(document -> 'auto_expiry_effects')
              AS item(effect)
     ) <> pg_catalog.jsonb_array_length(document -> 'auto_expiry_effects') THEN
    RAISE EXCEPTION 'identity_finalizer_auto_expiry_set_invalid'
      USING ERRCODE = '55000';
  END IF;
  FOR target_effect IN
    SELECT effect
      FROM pg_catalog.jsonb_array_elements(document -> 'auto_expiry_effects')
           WITH ORDINALITY AS item(effect, ordinal)
     ORDER BY ordinal
  LOOP
    IF pg_catalog.jsonb_typeof(target_effect) IS DISTINCT FROM 'object'
       OR (
         SELECT pg_catalog.array_agg(key_name ORDER BY key_name)
           FROM pg_catalog.jsonb_object_keys(target_effect)
                AS keys(key_name)
       ) IS DISTINCT FROM ARRAY[
         'directive_id','due_at','effect_commitment','outbox_id','person_id',
         'schedule_id','source_receipt_commitment','topic'
       ]::text[]
       OR EXISTS (
         SELECT 1
           FROM (VALUES
             ('directive_id'),
             ('due_at'),
             ('effect_commitment'),
             ('outbox_id'),
             ('person_id'),
             ('schedule_id'),
             ('source_receipt_commitment'),
             ('topic')
           ) AS required_effect_string(key_name)
          WHERE pg_catalog.jsonb_typeof(
                  target_effect -> required_effect_string.key_name
                ) IS DISTINCT FROM 'string'
       ) THEN
      RAISE EXCEPTION 'identity_finalizer_auto_expiry_shape_invalid'
        USING ERRCODE = '22023';
    END IF;
    target_directive_id := (target_effect ->> 'directive_id')::uuid;
    target_person_id := (target_effect ->> 'person_id')::uuid;
    target_due_at := (target_effect ->> 'due_at')::timestamptz;
    IF target_effect ->> 'schedule_id'
         IS DISTINCT FROM target_directive_id::text
       OR target_effect ->> 'topic'
            IS DISTINCT FROM 'privacy.person.auto_expire'
       OR target_effect ->> 'effect_commitment' !~ '^[0-9a-f]{64}$'
       OR target_effect ->> 'source_receipt_commitment'
            !~ '^[0-9a-f]{64}$'
       OR NOT pg_catalog.isfinite(target_due_at)
       OR (
         NOT exact_replay
         AND (
           target_due_at <= wall_clock_now
           OR target_due_at > wall_clock_now + interval '10 years'
         )
       )
       OR (
         SELECT pg_catalog.count(*)
           FROM pg_catalog.jsonb_array_elements(document -> 'projections')
                AS item(p)
          WHERE p ->> 'decision_kind' = 'privacy_directive'
            AND p -> 'record' ->> 'directive' = 'auto_expire'
            AND p -> 'record' ->> 'directive_id' = target_directive_id::text
            AND p -> 'record' ->> 'person_id' = target_person_id::text
            AND (p -> 'record' ->> 'expires_at')::timestamptz =
                  target_due_at
            AND p ->> 'receipt_id' = target_effect ->> 'outbox_id'
            AND p ->> 'receipt_commitment' =
                  target_effect ->> 'source_receipt_commitment'
       ) <> 1 THEN
      RAISE EXCEPTION 'identity_finalizer_auto_expiry_exact_set_invalid'
        USING ERRCODE = '55000';
    END IF;
  END LOOP;

  -- A consumed admission is replayable only as a complete immutable-result
  -- proof. It performs no repair writes and fails if any semantic, receipt,
  -- lineage, effect, privacy, tombstone, or erasure result drifted.
  IF exact_replay THEN
    SELECT finalization.finalization_id,
           finalization.database_transaction_id,
           finalization.finalized_at
      INTO exact_replay_id, current_txid, finalized_marker
      FROM operations.reviewed_identity_migration_finalizations AS finalization
     WHERE finalization.run_id = admission.run_id
       AND finalization.finalization_id = admission.finalization_id
       AND finalization.contract_version =
             'reviewed-identity-migration-finalization-v1'
       AND finalization.verification_status = 'candidate_unverified'
       AND NOT finalization.authoritative
       AND finalization.source_item_count = source_count
       AND finalization.decision_count = finalizer_state.decision_count
       AND finalization.apply_decision_count =
             finalizer_state.apply_decision_count
       AND finalization.receipt_count = projection_count
       AND finalization.source_manifest_commitment =
             migration_run.logical_source_manifest_commitment
       AND finalization.projection_manifest_commitment =
             migration_run.projection_manifest_commitment
       AND finalization.decision_manifest_commitment =
             admission.decision_manifest_commitment
       AND finalization.receipt_set_commitment =
             admission.receipt_set_commitment
       AND finalization.finalization_commitment =
             admission.finalization_commitment
       AND finalization.signature_algorithm = 'ed25519'
       AND finalization.signing_key_fingerprint =
             admission.finalization_signing_key_fingerprint
       AND finalization.finalization_signature =
             document -> 'finalization_attestation'
               ->> 'finalization_signature';
    IF exact_replay_id IS NULL
       OR admission.consumed_at IS DISTINCT FROM finalized_marker
       OR EXISTS (
         SELECT 1
           FROM pg_catalog.unnest(person_ids) AS candidate(person_id)
          WHERE privacy.identity_person_is_blocked(candidate.person_id)
       )
       OR (
         SELECT pg_catalog.count(*)
           FROM operations.reviewed_identity_migration_item_receipts AS receipt
          WHERE receipt.run_id = admission.run_id
       ) <> projection_count
       OR (
         SELECT pg_catalog.count(*)
           FROM operations.reviewed_identity_migration_projection_lineage AS lineage
          WHERE lineage.run_id = admission.run_id
       ) <> projection_count
       OR (
         SELECT pg_catalog.count(*)
           FROM operations.reviewed_identity_migration_projection_subjects AS subject
           JOIN operations.reviewed_identity_migration_projection_lineage AS lineage
             ON lineage.lineage_id = subject.lineage_id
          WHERE lineage.run_id = admission.run_id
       ) <> (
         SELECT coalesce(
                  pg_catalog.sum(
                    pg_catalog.jsonb_array_length(
                      projection -> 'lineage' -> 'subjects'
                    )
                  ),
                  0
                )
           FROM pg_catalog.jsonb_array_elements(document -> 'projections')
                AS item(projection)
       )
       OR (
         SELECT pg_catalog.count(*)
           FROM identity.people AS person
          WHERE person.person_id = ANY (person_ids)
       ) <> person_count
       OR (
         SELECT pg_catalog.count(*)
           FROM identity.aliases AS alias
          WHERE alias.person_id = ANY (person_ids)
       ) <> (
         SELECT pg_catalog.count(*)
           FROM pg_catalog.jsonb_array_elements(document -> 'projections')
                AS item(p)
          WHERE p ->> 'decision_kind' = 'alias'
       )
       OR (
         SELECT pg_catalog.count(*)
           FROM identity.external_recognition_bindings AS binding
          WHERE binding.person_id = ANY (person_ids)
       ) <> (
         SELECT pg_catalog.count(*)
           FROM pg_catalog.jsonb_array_elements(document -> 'projections')
                AS item(p)
          WHERE p ->> 'decision_kind' = 'recognition_binding'
       )
       OR (
         SELECT pg_catalog.count(*)
           FROM identity.privacy_directives AS directive
          WHERE directive.person_id = ANY (person_ids)
       ) <> (
         SELECT pg_catalog.count(*)
           FROM pg_catalog.jsonb_array_elements(document -> 'projections')
                AS item(p)
          WHERE p ->> 'decision_kind' = 'privacy_directive'
       )
       OR (
         SELECT pg_catalog.count(*)
           FROM identity.legacy_role_labels AS label
          WHERE label.person_id = ANY (person_ids)
       ) <> (
         SELECT pg_catalog.count(*)
           FROM pg_catalog.jsonb_array_elements(document -> 'projections')
                AS item(p)
          WHERE p ->> 'decision_kind' = 'legacy_role_candidate'
       )
       OR (
         SELECT pg_catalog.count(*)
           FROM identity.legacy_relationship_candidates AS relation
          WHERE relation.from_person_id = ANY (person_ids)
             OR relation.to_person_id = ANY (person_ids)
       ) <> (
         SELECT pg_catalog.count(*)
           FROM pg_catalog.jsonb_array_elements(document -> 'projections')
                AS item(p)
          WHERE p ->> 'decision_kind' = 'legacy_relationship_candidate'
       )
       OR (
         SELECT pg_catalog.count(*)
           FROM operations.outbox AS effect_outbox
          WHERE effect_outbox.topic = 'privacy.person.auto_expire'
            AND effect_outbox.aggregate_id = ANY (person_ids)
       ) <> pg_catalog.jsonb_array_length(document -> 'auto_expiry_effects') THEN
      RAISE EXCEPTION 'identity_finalizer_consumed_admission_drift'
        USING ERRCODE = '55000';
    END IF;
    FOR target_projection IN
      SELECT projection
        FROM pg_catalog.jsonb_array_elements(document -> 'projections')
             WITH ORDINALITY AS item(projection, ordinal)
       ORDER BY ordinal
    LOOP
      target_kind := target_projection ->> 'decision_kind';
      target_record := target_projection -> 'record';
      target_lineage := target_projection -> 'lineage';
      target_decision_id := (target_projection ->> 'decision_id')::uuid;
      target_receipt_id := (target_projection ->> 'receipt_id')::uuid;
      target_projection_id := (target_lineage ->> 'projection_id')::uuid;
      IF NOT EXISTS (
           SELECT 1
             FROM operations.reviewed_identity_migration_decisions AS decision
            WHERE decision.run_id = admission.run_id
              AND decision.decision_id = target_decision_id
              AND decision.decision_kind = target_kind
              AND decision.disposition = 'apply'
              AND decision.candidate_commitment =
                    target_projection ->> 'candidate_commitment'
         )
         OR NOT EXISTS (
           SELECT 1
             FROM operations.reviewed_identity_migration_item_receipts AS receipt
            WHERE receipt.run_id = admission.run_id
              AND receipt.receipt_id = target_receipt_id
              AND receipt.decision_id = target_decision_id
              AND receipt.decision_kind = target_kind
              AND receipt.decision_disposition = 'apply'
              AND receipt.candidate_commitment =
                    target_projection ->> 'candidate_commitment'
              AND receipt.outcome = 'inserted_exact'
              AND receipt.projection_table_kind =
                    target_projection ->> 'projection_table_kind'
              AND receipt.projection_ref_commitment =
                    target_projection ->> 'projection_ref_commitment'
              AND receipt.projection_commitment =
                    target_projection ->> 'projection_commitment'
              AND receipt.receipt_commitment =
                    target_projection ->> 'receipt_commitment'
              AND receipt.database_transaction_id = current_txid
              AND receipt.applied_at = finalized_marker
         )
         OR NOT EXISTS (
           SELECT 1
             FROM operations.reviewed_identity_migration_projection_lineage
                  AS lineage
            WHERE lineage.lineage_id = target_receipt_id
              AND lineage.run_id = admission.run_id
              AND lineage.receipt_id = target_receipt_id
              AND lineage.decision_id = target_decision_id
              AND lineage.decision_kind = target_kind
              AND lineage.projection_table_kind =
                    target_projection ->> 'projection_table_kind'
              AND lineage.projection_id = target_projection_id
              AND lineage.projection_ref_commitment =
                    target_projection ->> 'projection_ref_commitment'
              AND lineage.lineage_commitment =
                    target_lineage ->> 'lineage_commitment'
              AND lineage.linked_at = finalized_marker
         )
         OR EXISTS (
           SELECT 1
             FROM pg_catalog.jsonb_array_elements(
                    target_lineage -> 'subjects'
                  ) AS item(subject)
            WHERE NOT EXISTS (
              SELECT 1
                FROM operations.reviewed_identity_migration_projection_subjects
                     AS stored_subject
               WHERE stored_subject.lineage_id = target_receipt_id
                 AND stored_subject.person_id =
                       (subject ->> 'person_id')::uuid
                 AND stored_subject.subject_role =
                       subject ->> 'subject_role'
            )
         ) THEN
        RAISE EXCEPTION 'identity_finalizer_consumed_lineage_drift'
          USING ERRCODE = '55000';
      END IF;
      IF target_kind = 'person' THEN
        IF NOT EXISTS (
          SELECT 1
            FROM identity.people AS person
           WHERE person.person_id = (target_record ->> 'person_id')::uuid
             AND person.display_name = target_record ->> 'display_name'
             AND person.pronouns IS NOT DISTINCT FROM
                   NULLIF(target_record ->> 'pronouns', '')
             AND person.privacy_scope = 'private'
             AND person.legacy_source_ref =
                   target_record ->> 'legacy_source_ref'
             AND person.legacy_source_version =
                   (target_record ->> 'legacy_source_version')::integer
             AND person.legacy_source_sha256 =
                   target_record ->> 'legacy_source_sha256'
             AND person.created_at = finalized_marker
             AND person.updated_at = finalized_marker
             AND person.status = coalesce((
               SELECT status_projection -> 'record' ->> 'status'
                 FROM pg_catalog.jsonb_array_elements(
                        document -> 'projections'
                      ) AS item(status_projection)
                WHERE status_projection ->> 'decision_kind' = 'person_status'
                  AND status_projection -> 'record' ->> 'person_id' =
                        target_record ->> 'person_id'
             ), 'active')
        ) THEN
          RAISE EXCEPTION 'identity_finalizer_consumed_person_drift'
            USING ERRCODE = '55000';
        END IF;
      ELSIF target_kind = 'person_status' THEN
        IF NOT EXISTS (
          SELECT 1
            FROM identity.people AS person
           WHERE person.person_id = (target_record ->> 'person_id')::uuid
             AND person.status = 'archived'
             AND person.status_source_ref = target_record ->> 'source_ref'
             AND person.status_source_version =
                   (target_record ->> 'source_version')::integer
             AND person.status_source_sha256 =
                   target_record ->> 'source_snapshot_sha256'
        ) THEN
          RAISE EXCEPTION 'identity_finalizer_consumed_status_drift'
            USING ERRCODE = '55000';
        END IF;
      ELSIF target_kind = 'alias' THEN
        IF NOT EXISTS (
          SELECT 1 FROM identity.aliases AS alias
           WHERE alias.alias_id = (target_record ->> 'alias_id')::uuid
             AND alias.person_id = (target_record ->> 'person_id')::uuid
             AND alias.alias = target_record ->> 'alias'
             AND alias.normalized_alias = target_record ->> 'normalized_alias'
             AND alias.alias_kind = target_record ->> 'alias_kind'
             AND alias.source_ref = target_record ->> 'source_ref'
             AND alias.source_version =
                   (target_record ->> 'source_version')::integer
             AND alias.source_snapshot_sha256 =
                   target_record ->> 'source_snapshot_sha256'
             AND alias.created_at = finalized_marker
        ) THEN
          RAISE EXCEPTION 'identity_finalizer_consumed_alias_drift'
            USING ERRCODE = '55000';
        END IF;
      ELSIF target_kind = 'recognition_binding' THEN
        IF NOT EXISTS (
          SELECT 1 FROM identity.external_recognition_bindings AS binding
           WHERE binding.binding_id = (target_record ->> 'binding_id')::uuid
             AND binding.person_id = (target_record ->> 'person_id')::uuid
             AND binding.external_system =
                   target_record ->> 'external_system'
             AND binding.external_id = target_record ->> 'external_id'
             AND binding.status = target_record ->> 'status'
             AND binding.source_ref = target_record ->> 'source_ref'
             AND binding.source_version =
                   (target_record ->> 'source_version')::integer
             AND binding.source_snapshot_sha256 =
                   target_record ->> 'source_snapshot_sha256'
             AND binding.created_at = finalized_marker
             AND binding.updated_at = finalized_marker
        ) THEN
          RAISE EXCEPTION 'identity_finalizer_consumed_recognition_drift'
            USING ERRCODE = '55000';
        END IF;
      ELSIF target_kind = 'privacy_directive' THEN
        IF NOT EXISTS (
          SELECT 1 FROM identity.privacy_directives AS directive
           WHERE directive.directive_id =
                   (target_record ->> 'directive_id')::uuid
             AND directive.person_id = (target_record ->> 'person_id')::uuid
             AND directive.directive = target_record ->> 'directive'
             AND directive.enabled
             AND directive.expires_at IS NOT DISTINCT FROM
                   NULLIF(target_record ->> 'expires_at', '')::timestamptz
             AND directive.source_ref = target_record ->> 'source_ref'
             AND directive.source_version =
                   (target_record ->> 'source_version')::integer
             AND directive.source_snapshot_sha256 =
                   target_record ->> 'source_snapshot_sha256'
             AND directive.created_at = finalized_marker
        ) THEN
          RAISE EXCEPTION 'identity_finalizer_consumed_privacy_drift'
            USING ERRCODE = '55000';
        END IF;
      ELSIF target_kind = 'legacy_role_candidate' THEN
        IF NOT EXISTS (
          SELECT 1 FROM identity.legacy_role_labels AS label
           WHERE label.label_id = (target_record ->> 'label_id')::uuid
             AND label.person_id = (target_record ->> 'person_id')::uuid
             AND label.role_label = target_record ->> 'role_label'
             AND label.perspective = 'unknown'
             AND label.source_ref = target_record ->> 'source_ref'
             AND label.source_version IS NOT DISTINCT FROM
                   NULLIF(target_record ->> 'source_version', '')::integer
             AND label.source_snapshot_sha256 =
                   target_record ->> 'source_snapshot_sha256'
             AND label.imported_at = finalized_marker
        ) THEN
          RAISE EXCEPTION 'identity_finalizer_consumed_role_drift'
            USING ERRCODE = '55000';
        END IF;
      ELSE
        IF NOT EXISTS (
          SELECT 1 FROM identity.legacy_relationship_candidates AS relation
           WHERE relation.candidate_id =
                   (target_record ->> 'candidate_id')::uuid
             AND relation.from_person_id =
                   (target_record ->> 'from_person_id')::uuid
             AND relation.to_person_id =
                   (target_record ->> 'to_person_id')::uuid
             AND relation.relationship_label =
                   target_record ->> 'relationship_label'
             AND relation.relationship_status =
                   target_record ->> 'relationship_status'
             AND relation.perspective = 'unknown'
             AND NOT relation.authoritative
             AND relation.source_ref = target_record ->> 'source_ref'
             AND relation.source_snapshot_sha256 =
                   target_record ->> 'source_snapshot_sha256'
             AND relation.imported_at = finalized_marker
        ) THEN
          RAISE EXCEPTION 'identity_finalizer_consumed_relationship_drift'
            USING ERRCODE = '55000';
        END IF;
      END IF;
    END LOOP;
    IF (
      SELECT pg_catalog.count(*)
        FROM privacy.auto_expiry_schedules AS schedule
       WHERE schedule.person_id = ANY (person_ids)
    ) <> pg_catalog.jsonb_array_length(document -> 'auto_expiry_effects') THEN
      RAISE EXCEPTION 'identity_finalizer_consumed_effect_set_drift'
        USING ERRCODE = '55000';
    END IF;
    FOR target_effect IN
      SELECT effect
        FROM pg_catalog.jsonb_array_elements(document -> 'auto_expiry_effects')
             WITH ORDINALITY AS item(effect, ordinal)
       ORDER BY ordinal
    LOOP
      IF NOT EXISTS (
           SELECT 1 FROM privacy.auto_expiry_schedules AS schedule
            WHERE schedule.schedule_id =
                    (target_effect ->> 'schedule_id')::uuid
              AND schedule.person_id = (target_effect ->> 'person_id')::uuid
              AND schedule.directive_id =
                    (target_effect ->> 'directive_id')::uuid
              AND schedule.outbox_id = (target_effect ->> 'outbox_id')::uuid
              AND schedule.due_at =
                    (target_effect ->> 'due_at')::timestamptz
              AND schedule.state = 'scheduled'
              AND schedule.created_at = finalized_marker
              AND schedule.completed_at IS NULL
         )
         OR NOT EXISTS (
           SELECT 1 FROM operations.outbox AS effect_outbox
            WHERE effect_outbox.outbox_id =
                    (target_effect ->> 'outbox_id')::uuid
              AND effect_outbox.topic = 'privacy.person.auto_expire'
              AND effect_outbox.aggregate_id =
                    (target_effect ->> 'person_id')::uuid
              AND effect_outbox.state = 'pending'
              AND effect_outbox.attempts = 0
              AND effect_outbox.available_at =
                    (target_effect ->> 'due_at')::timestamptz
              AND effect_outbox.claimed_at IS NULL
              AND effect_outbox.claim_token IS NULL
              AND effect_outbox.last_error_code IS NULL
              AND effect_outbox.completed_at IS NULL
              AND effect_outbox.created_at = finalized_marker
              AND effect_outbox.payload = pg_catalog.jsonb_build_object(
                    'version', 1,
                    'schedule_id', target_effect ->> 'schedule_id',
                    'person_id', target_effect ->> 'person_id',
                    'directive_id', target_effect ->> 'directive_id',
                    'policy_digest', admission.policy_digest
                  )
         ) THEN
        RAISE EXCEPTION 'identity_finalizer_consumed_effect_drift'
          USING ERRCODE = '55000';
      END IF;
    END LOOP;
    RETURN exact_replay_id;
  END IF;

  FOR target_projection IN
    SELECT projection
      FROM pg_catalog.jsonb_array_elements(document -> 'projections')
           WITH ORDINALITY AS item(projection, ordinal)
     ORDER BY ordinal
  LOOP
    IF pg_catalog.jsonb_typeof(target_projection) IS DISTINCT FROM 'object'
       OR pg_catalog.jsonb_typeof(target_projection -> 'record')
            IS DISTINCT FROM 'object'
       OR pg_catalog.jsonb_typeof(target_projection -> 'lineage')
            IS DISTINCT FROM 'object' THEN
      RAISE EXCEPTION 'identity_finalizer_projection_shape_invalid'
        USING ERRCODE = '22023';
    END IF;
    SELECT pg_catalog.count(*),
           pg_catalog.count(*) FILTER (
             WHERE key_name = ANY (ARRAY[
               'candidate_commitment','decision_id','decision_kind','lineage',
               'projection_commitment','projection_ref_commitment',
               'projection_table_kind','receipt_commitment','receipt_id','record'
             ]::text[])
           )
      INTO key_count, allowed_key_count
      FROM pg_catalog.jsonb_object_keys(target_projection) AS keys(key_name);
    IF key_count <> 10 OR allowed_key_count <> 10 THEN
      RAISE EXCEPTION 'identity_finalizer_projection_keys_invalid'
        USING ERRCODE = '22023';
    END IF;
    target_kind := target_projection ->> 'decision_kind';
    target_projection_table := target_projection ->> 'projection_table_kind';
    target_decision_id := (target_projection ->> 'decision_id')::uuid;
    target_receipt_id := (target_projection ->> 'receipt_id')::uuid;
    target_record := target_projection -> 'record';
    target_lineage := target_projection -> 'lineage';
    target_projection_id := (target_lineage ->> 'projection_id')::uuid;
    IF NOT EXISTS (
      SELECT 1
        FROM operations.reviewed_identity_migration_decisions AS decision
       WHERE decision.run_id = admission.run_id
         AND decision.decision_id = target_decision_id
         AND decision.decision_kind = target_kind
         AND decision.disposition = 'apply'
         AND decision.candidate_commitment =
               target_projection ->> 'candidate_commitment'
    ) OR target_projection ->> 'candidate_commitment'
           !~ '^[0-9a-f]{64}$'
       OR target_projection ->> 'projection_commitment'
           !~ '^[0-9a-f]{64}$'
       OR target_projection ->> 'projection_ref_commitment'
           !~ '^[0-9a-f]{64}$'
       OR target_projection ->> 'receipt_commitment'
           !~ '^[0-9a-f]{64}$' THEN
      RAISE EXCEPTION 'identity_finalizer_projection_decision_mismatch'
        USING ERRCODE = '55000';
    END IF;
    IF (target_kind IN ('person','person_status')
          AND target_projection_table <> 'identity.people')
       OR (target_kind = 'alias'
          AND target_projection_table <> 'identity.aliases')
       OR (target_kind = 'recognition_binding'
          AND target_projection_table <>
                'identity.external_recognition_bindings')
       OR (target_kind = 'privacy_directive'
          AND target_projection_table <> 'identity.privacy_directives')
       OR (target_kind = 'legacy_role_candidate'
          AND target_projection_table <> 'identity.legacy_role_labels')
       OR (target_kind = 'legacy_relationship_candidate'
          AND target_projection_table <>
                'identity.legacy_relationship_candidates')
       OR target_kind NOT IN (
         'person','person_status','alias','recognition_binding',
         'privacy_directive','legacy_role_candidate',
         'legacy_relationship_candidate'
       ) THEN
      RAISE EXCEPTION 'identity_finalizer_projection_target_invalid'
        USING ERRCODE = '22023';
    END IF;
    IF target_lineage ->> 'lineage_id' <> target_receipt_id::text
       OR target_lineage ->> 'run_id' <> admission.run_id::text
       OR target_lineage ->> 'receipt_id' <> target_receipt_id::text
       OR target_lineage ->> 'decision_id' <> target_decision_id::text
       OR target_lineage ->> 'decision_kind' <> target_kind
       OR target_lineage ->> 'projection_table_kind' <>
            target_projection_table
       OR target_lineage ->> 'projection_ref_commitment' <>
            target_projection ->> 'projection_ref_commitment'
       OR target_lineage ->> 'lineage_commitment'
            !~ '^[0-9a-f]{64}$'
       OR pg_catalog.jsonb_typeof(target_lineage -> 'subjects')
            IS DISTINCT FROM 'array' THEN
      RAISE EXCEPTION 'identity_finalizer_lineage_invalid'
        USING ERRCODE = '55000';
    END IF;
    IF EXISTS (
         SELECT 1
           FROM operations.reviewed_identity_migration_item_receipts AS receipt
          WHERE receipt.receipt_id = target_receipt_id
             OR receipt.decision_id = target_decision_id
       )
       OR EXISTS (
         SELECT 1
           FROM operations.reviewed_identity_migration_projection_lineage AS lineage
          WHERE lineage.lineage_id = target_receipt_id
             OR lineage.receipt_id = target_receipt_id
       ) THEN
      RAISE EXCEPTION 'identity_finalizer_lineage_collision'
        USING ERRCODE = '23505';
    END IF;

    IF target_kind IN (
         'person','person_status','alias','recognition_binding',
         'privacy_directive','legacy_role_candidate'
       ) THEN
      target_person_id := (target_record ->> 'person_id')::uuid;
      IF NOT target_person_id = ANY (person_ids)
         OR pg_catalog.jsonb_array_length(target_lineage -> 'subjects') <> 1
         OR target_lineage -> 'subjects' -> 0 ->> 'person_id' <>
              target_person_id::text
         OR target_lineage -> 'subjects' -> 0 ->> 'subject_role' <> 'primary'
         OR target_projection_id <> (CASE target_kind
              WHEN 'person' THEN target_person_id
              WHEN 'person_status' THEN target_person_id
              WHEN 'alias' THEN (target_record ->> 'alias_id')::uuid
              WHEN 'recognition_binding'
                THEN (target_record ->> 'binding_id')::uuid
              WHEN 'privacy_directive'
                THEN (target_record ->> 'directive_id')::uuid
              WHEN 'legacy_role_candidate'
                THEN (target_record ->> 'label_id')::uuid
            END) THEN
        RAISE EXCEPTION 'identity_finalizer_projection_subject_invalid'
          USING ERRCODE = '55000';
      END IF;
    ELSE
      IF (target_record ->> 'from_person_id')::uuid =
           (target_record ->> 'to_person_id')::uuid
         OR NOT (target_record ->> 'from_person_id')::uuid = ANY (person_ids)
         OR NOT (target_record ->> 'to_person_id')::uuid = ANY (person_ids)
         OR target_projection_id <>
              (target_record ->> 'candidate_id')::uuid
         OR pg_catalog.jsonb_array_length(target_lineage -> 'subjects') <> 2
         OR target_lineage -> 'subjects' -> 0 ->> 'person_id' <>
              target_record ->> 'from_person_id'
         OR target_lineage -> 'subjects' -> 0 ->> 'subject_role' <> 'from'
         OR target_lineage -> 'subjects' -> 1 ->> 'person_id' <>
              target_record ->> 'to_person_id'
         OR target_lineage -> 'subjects' -> 1 ->> 'subject_role' <> 'to' THEN
        RAISE EXCEPTION 'identity_finalizer_relationship_subject_invalid'
          USING ERRCODE = '55000';
      END IF;
    END IF;

    -- Typed record and collision checks. The admission digest proves the
    -- private strings were canonicalized and signed by the offline verifier;
    -- this kernel independently checks exact database identity and categories.
    IF target_kind = 'person' THEN
      IF target_record ->> 'privacy_scope' <> 'private'
         OR pg_catalog.btrim(target_record ->> 'display_name') = ''
         OR pg_catalog.char_length(target_record ->> 'display_name') > 255
         OR target_record ->> 'legacy_source_sha256'
              !~ '^[0-9a-f]{64}$'
         OR EXISTS (
           SELECT 1 FROM identity.people
            WHERE person_id = target_person_id
         ) THEN
        RAISE EXCEPTION 'identity_finalizer_person_projection_invalid'
          USING ERRCODE = '23505';
      END IF;
    ELSIF target_kind = 'person_status' THEN
      IF target_record ->> 'status' <> 'archived'
         OR target_record ->> 'source_snapshot_sha256'
              !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'identity_finalizer_status_projection_invalid'
          USING ERRCODE = '22023';
      END IF;
    ELSIF target_kind = 'alias' THEN
      IF target_record ->> 'alias_kind' NOT IN ('name','nickname')
         OR pg_catalog.btrim(target_record ->> 'alias') = ''
         OR target_record ->> 'source_snapshot_sha256'
              !~ '^[0-9a-f]{64}$'
         OR EXISTS (
           SELECT 1 FROM identity.aliases
            WHERE alias_id = (target_record ->> 'alias_id')::uuid
               OR normalized_alias = target_record ->> 'normalized_alias'
         ) THEN
        RAISE EXCEPTION 'identity_finalizer_alias_projection_invalid'
          USING ERRCODE = '23505';
      END IF;
    ELSIF target_kind = 'recognition_binding' THEN
      IF target_record ->> 'external_system' <> 'frigate'
         OR target_record ->> 'status' NOT IN ('active','retired')
         OR target_record ->> 'source_snapshot_sha256'
              !~ '^[0-9a-f]{64}$'
         OR EXISTS (
           SELECT 1 FROM identity.external_recognition_bindings
            WHERE binding_id = (target_record ->> 'binding_id')::uuid
               OR (external_system = target_record ->> 'external_system'
                   AND external_id = target_record ->> 'external_id')
         ) THEN
        RAISE EXCEPTION 'identity_finalizer_recognition_projection_invalid'
          USING ERRCODE = '23505';
      END IF;
    ELSIF target_kind = 'privacy_directive' THEN
      IF target_record ->> 'directive' NOT IN (
           'do_not_track','ignored','silent','private','auto_expire'
         )
         OR target_record -> 'enabled' <> 'true'::jsonb
         OR (
           target_record ->> 'directive' = 'auto_expire'
           AND (
             target_record ->> 'expires_at' IS NULL
             OR NOT pg_catalog.isfinite(
                  (target_record ->> 'expires_at')::timestamptz
                )
             OR (target_record ->> 'expires_at')::timestamptz <= wall_clock_now
             OR (target_record ->> 'expires_at')::timestamptz >
                  wall_clock_now + interval '10 years'
           )
         )
         OR (
           target_record ->> 'directive' <> 'auto_expire'
           AND target_record -> 'expires_at' <> 'null'::jsonb
         )
         OR target_record ->> 'source_snapshot_sha256'
              !~ '^[0-9a-f]{64}$'
         OR EXISTS (
           SELECT 1 FROM identity.privacy_directives
            WHERE directive_id = (target_record ->> 'directive_id')::uuid
               OR (person_id = target_person_id
                   AND directive = target_record ->> 'directive')
         ) THEN
        RAISE EXCEPTION 'identity_finalizer_privacy_projection_invalid'
          USING ERRCODE = '23505';
      END IF;
    ELSIF target_kind = 'legacy_role_candidate' THEN
      IF target_record ->> 'perspective' <> 'unknown'
         OR pg_catalog.btrim(target_record ->> 'role_label') = ''
         OR target_record ->> 'source_snapshot_sha256'
              !~ '^[0-9a-f]{64}$'
         OR EXISTS (
           SELECT 1 FROM identity.legacy_role_labels
            WHERE label_id = (target_record ->> 'label_id')::uuid
               OR (person_id = target_person_id
                   AND source_ref = target_record ->> 'source_ref')
         ) THEN
        RAISE EXCEPTION 'identity_finalizer_role_projection_invalid'
          USING ERRCODE = '23505';
      END IF;
    ELSE
      IF target_record ->> 'perspective' <> 'unknown'
         OR target_record -> 'authoritative' <> 'false'::jsonb
         OR target_record ->> 'relationship_status'
              NOT IN ('active','ended','paused')
         OR target_record ->> 'source_snapshot_sha256'
              !~ '^[0-9a-f]{64}$'
         OR EXISTS (
           SELECT 1 FROM identity.legacy_relationship_candidates
            WHERE candidate_id = (target_record ->> 'candidate_id')::uuid
               OR source_ref = target_record ->> 'source_ref'
         ) THEN
        RAISE EXCEPTION 'identity_finalizer_relationship_projection_invalid'
          USING ERRCODE = '23505';
      END IF;
    END IF;
  END LOOP;

  IF EXISTS (
    SELECT 1
      FROM pg_catalog.unnest(person_ids) AS candidate(person_id)
     WHERE privacy.identity_person_is_blocked(candidate.person_id)
  ) THEN
    RAISE EXCEPTION 'identity_erasure_blocked'
      USING ERRCODE = '42501';
  END IF;
  IF (
    SELECT pg_catalog.count(*)
      FROM pg_catalog.jsonb_array_elements(document -> 'projections') AS item(p)
     WHERE p ->> 'decision_kind' = 'person_status'
  ) <> (
    SELECT pg_catalog.count(DISTINCT p -> 'record' ->> 'person_id')
      FROM pg_catalog.jsonb_array_elements(document -> 'projections') AS item(p)
     WHERE p ->> 'decision_kind' = 'person_status'
  ) OR (
    SELECT pg_catalog.count(*)
      FROM pg_catalog.jsonb_array_elements(document -> 'projections') AS item(p)
     WHERE p ->> 'decision_kind' = 'privacy_directive'
  ) <> (
    SELECT pg_catalog.count(DISTINCT (
             p -> 'record' ->> 'person_id',
             p -> 'record' ->> 'directive'
           ))
      FROM pg_catalog.jsonb_array_elements(document -> 'projections') AS item(p)
     WHERE p ->> 'decision_kind' = 'privacy_directive'
  ) THEN
    RAISE EXCEPTION 'identity_finalizer_privacy_cardinality_invalid'
      USING ERRCODE = '55000';
  END IF;
  IF EXISTS (
    SELECT 1
      FROM pg_catalog.jsonb_array_elements(document -> 'projections') AS person(p)
     WHERE p ->> 'decision_kind' = 'person'
       AND EXISTS (
         SELECT 1
           FROM pg_catalog.jsonb_array_elements(document -> 'projections') AS directive(d)
          WHERE d ->> 'decision_kind' = 'privacy_directive'
            AND d -> 'record' ->> 'person_id' =
                  p -> 'record' ->> 'person_id'
            AND d -> 'record' ->> 'directive' = 'ignored'
       )
       AND (
         p -> 'record' ->> 'display_name' <> 'Suppressed legacy identity'
         OR p -> 'record' -> 'pronouns' <> 'null'::jsonb
         OR EXISTS (
           SELECT 1
             FROM pg_catalog.jsonb_array_elements(document -> 'projections')
                  AS prohibited(x)
            WHERE x ->> 'decision_kind' IN (
                    'alias','recognition_binding','legacy_role_candidate',
                    'legacy_relationship_candidate'
                  )
              AND (
                x -> 'record' ->> 'person_id' =
                  p -> 'record' ->> 'person_id'
                OR x -> 'record' ->> 'from_person_id' =
                  p -> 'record' ->> 'person_id'
                OR x -> 'record' ->> 'to_person_id' =
                  p -> 'record' ->> 'person_id'
              )
         )
       )
  ) OR EXISTS (
    SELECT 1
      FROM pg_catalog.jsonb_array_elements(document -> 'projections') AS binding(b)
     WHERE b ->> 'decision_kind' = 'recognition_binding'
       AND b -> 'record' ->> 'status' = 'active'
       AND EXISTS (
         SELECT 1
           FROM pg_catalog.jsonb_array_elements(document -> 'projections') AS directive(d)
          WHERE d ->> 'decision_kind' = 'privacy_directive'
            AND d -> 'record' ->> 'person_id' =
                  b -> 'record' ->> 'person_id'
            AND d -> 'record' ->> 'directive' IN ('ignored','do_not_track')
       )
  ) THEN
    RAISE EXCEPTION 'identity_finalizer_privacy_closure_invalid'
      USING ERRCODE = '55000';
  END IF;

  IF pg_catalog.jsonb_array_length(document -> 'privacy_closures') <>
       person_count THEN
    RAISE EXCEPTION 'identity_finalizer_privacy_closure_set_invalid'
      USING ERRCODE = '55000';
  END IF;
  FOR target_closure IN
    SELECT closure
      FROM pg_catalog.jsonb_array_elements(document -> 'privacy_closures')
           WITH ORDINALITY AS item(closure, ordinal)
     ORDER BY ordinal
  LOOP
    target_person_id := (target_closure ->> 'person_id')::uuid;
    IF NOT target_person_id = ANY (person_ids)
       OR target_closure ->> 'privacy_closure_commitment'
            !~ '^[0-9a-f]{64}$'
       OR (target_closure -> 'ignored')::boolean IS DISTINCT FROM EXISTS (
         SELECT 1
           FROM pg_catalog.jsonb_array_elements(document -> 'projections') AS item(p)
          WHERE p ->> 'decision_kind' = 'privacy_directive'
            AND p -> 'record' ->> 'person_id' = target_person_id::text
            AND p -> 'record' ->> 'directive' = 'ignored'
       )
       OR (target_closure -> 'do_not_track')::boolean IS DISTINCT FROM EXISTS (
         SELECT 1
           FROM pg_catalog.jsonb_array_elements(document -> 'projections') AS item(p)
          WHERE p ->> 'decision_kind' = 'privacy_directive'
            AND p -> 'record' ->> 'person_id' = target_person_id::text
            AND p -> 'record' ->> 'directive' = 'do_not_track'
       )
       OR pg_catalog.jsonb_array_length(target_closure -> 'directives') <> (
         SELECT pg_catalog.count(*)
           FROM pg_catalog.jsonb_array_elements(document -> 'projections') AS item(p)
          WHERE p ->> 'decision_kind' = 'privacy_directive'
            AND p -> 'record' ->> 'person_id' = target_person_id::text
       ) THEN
      RAISE EXCEPTION 'identity_finalizer_privacy_closure_invalid'
        USING ERRCODE = '55000';
    END IF;
  END LOOP;
  SELECT pg_catalog.count(DISTINCT closure ->> 'person_id')
    INTO distinct_count
    FROM pg_catalog.jsonb_array_elements(document -> 'privacy_closures')
         AS item(closure);
  IF distinct_count <> person_count THEN
    RAISE EXCEPTION 'identity_finalizer_privacy_closure_duplicate'
      USING ERRCODE = '23505';
  END IF;

  IF pg_catalog.jsonb_array_length(document -> 'auto_expiry_effects') <> (
    SELECT pg_catalog.count(*)
      FROM pg_catalog.jsonb_array_elements(document -> 'projections') AS item(p)
     WHERE p ->> 'decision_kind' = 'privacy_directive'
       AND p -> 'record' ->> 'directive' = 'auto_expire'
  ) THEN
    RAISE EXCEPTION 'identity_finalizer_auto_expiry_set_invalid'
      USING ERRCODE = '55000';
  END IF;
  FOR target_effect IN
    SELECT effect
      FROM pg_catalog.jsonb_array_elements(document -> 'auto_expiry_effects')
           WITH ORDINALITY AS item(effect, ordinal)
     ORDER BY ordinal
  LOOP
    target_directive_id := (target_effect ->> 'directive_id')::uuid;
    target_person_id := (target_effect ->> 'person_id')::uuid;
    target_due_at := (target_effect ->> 'due_at')::timestamptz;
    IF target_effect ->> 'schedule_id' <> target_directive_id::text
       OR target_effect ->> 'topic' <> 'privacy.person.auto_expire'
       OR target_effect ->> 'effect_commitment' !~ '^[0-9a-f]{64}$'
       OR NOT pg_catalog.isfinite(target_due_at)
       OR target_due_at <= wall_clock_now
       OR target_due_at > wall_clock_now + interval '10 years'
       OR NOT EXISTS (
         SELECT 1
           FROM pg_catalog.jsonb_array_elements(document -> 'projections')
                AS item(p)
          WHERE p ->> 'decision_kind' = 'privacy_directive'
            AND p -> 'record' ->> 'directive' = 'auto_expire'
            AND p -> 'record' ->> 'directive_id' = target_directive_id::text
            AND p -> 'record' ->> 'person_id' = target_person_id::text
            AND (p -> 'record' ->> 'expires_at')::timestamptz = target_due_at
            AND p ->> 'receipt_id' = target_effect ->> 'outbox_id'
            AND p ->> 'receipt_commitment' =
                  target_effect ->> 'source_receipt_commitment'
       )
       OR EXISTS (
         SELECT 1 FROM privacy.auto_expiry_schedules
          WHERE schedule_id = target_directive_id
             OR person_id = target_person_id
             OR outbox_id = (target_effect ->> 'outbox_id')::uuid
       )
       OR EXISTS (
         SELECT 1 FROM operations.outbox
          WHERE outbox_id = (target_effect ->> 'outbox_id')::uuid
             OR (
               topic = 'privacy.person.auto_expire'
               AND aggregate_id = target_person_id
             )
       ) THEN
      RAISE EXCEPTION 'identity_finalizer_auto_expiry_invalid'
        USING ERRCODE = '23505';
    END IF;
  END LOOP;

  -- Recheck against a non-transaction-stable clock immediately before the
  -- first semantic write. Validation time cannot extend either one-time gate.
  wall_clock_now := pg_catalog.clock_timestamp();
  IF admission.expires_at <= wall_clock_now
     OR migration_run.expires_at <= wall_clock_now
     OR EXISTS (
       SELECT 1
        FROM pg_catalog.jsonb_array_elements(
                document -> 'auto_expiry_effects'
              ) AS item(effect)
        WHERE NOT pg_catalog.isfinite((effect ->> 'due_at')::timestamptz)
           OR (effect ->> 'due_at')::timestamptz <= wall_clock_now
           OR (effect ->> 'due_at')::timestamptz >
                wall_clock_now + interval '10 years'
     ) THEN
    RAISE EXCEPTION 'identity_finalizer_freshness_elapsed'
      USING ERRCODE = '42501';
  END IF;
  BEGIN
    current_txid := pg_catalog.txid_current();
    -- People are inserted first so every remaining typed projection can use
    -- its ordinary FK. Existing rows are never adopted or replayed as same-run
    -- rows.
    FOR target_projection IN
    SELECT projection
      FROM pg_catalog.jsonb_array_elements(document -> 'projections')
           WITH ORDINALITY AS item(projection, ordinal)
     WHERE projection ->> 'decision_kind' = 'person'
     ORDER BY ordinal
  LOOP
    target_record := target_projection -> 'record';
    INSERT INTO identity.people (
      person_id, display_name, pronouns, status, privacy_scope,
      legacy_source_ref, legacy_source_version, legacy_source_sha256
    ) VALUES (
      (target_record ->> 'person_id')::uuid,
      target_record ->> 'display_name',
      NULLIF(target_record ->> 'pronouns', ''),
      'active',
      'private',
      target_record ->> 'legacy_source_ref',
      (target_record ->> 'legacy_source_version')::integer,
      target_record ->> 'legacy_source_sha256'
    );
    END LOOP;

    FOR target_projection IN
    SELECT projection
      FROM pg_catalog.jsonb_array_elements(document -> 'projections')
           WITH ORDINALITY AS item(projection, ordinal)
     WHERE projection ->> 'decision_kind' <> 'person'
     ORDER BY ordinal
  LOOP
    target_kind := target_projection ->> 'decision_kind';
    target_record := target_projection -> 'record';
    IF target_kind = 'person_status' THEN
      UPDATE identity.people
         SET status = 'archived',
             status_source_ref = target_record ->> 'source_ref',
             status_source_version =
               (target_record ->> 'source_version')::integer,
             status_source_sha256 =
               target_record ->> 'source_snapshot_sha256',
             updated_at = pg_catalog.transaction_timestamp()
       WHERE person_id = (target_record ->> 'person_id')::uuid
         AND status = 'active';
      GET DIAGNOSTICS affected_rows = ROW_COUNT;
      IF affected_rows <> 1 THEN
        RAISE EXCEPTION 'identity_finalizer_status_apply_failed'
          USING ERRCODE = '55000';
      END IF;
    ELSIF target_kind = 'alias' THEN
      INSERT INTO identity.aliases (
        alias_id, person_id, alias, normalized_alias, alias_kind,
        source_ref, source_version, source_snapshot_sha256
      ) VALUES (
        (target_record ->> 'alias_id')::uuid,
        (target_record ->> 'person_id')::uuid,
        target_record ->> 'alias',
        target_record ->> 'normalized_alias',
        target_record ->> 'alias_kind',
        target_record ->> 'source_ref',
        (target_record ->> 'source_version')::integer,
        target_record ->> 'source_snapshot_sha256'
      );
    ELSIF target_kind = 'recognition_binding' THEN
      INSERT INTO identity.external_recognition_bindings (
        binding_id, person_id, external_system, external_id, status,
        source_ref, source_version, source_snapshot_sha256
      ) VALUES (
        (target_record ->> 'binding_id')::uuid,
        (target_record ->> 'person_id')::uuid,
        target_record ->> 'external_system',
        target_record ->> 'external_id',
        target_record ->> 'status',
        target_record ->> 'source_ref',
        (target_record ->> 'source_version')::integer,
        target_record ->> 'source_snapshot_sha256'
      );
    ELSIF target_kind = 'privacy_directive' THEN
      INSERT INTO identity.privacy_directives (
        directive_id, person_id, directive, enabled, expires_at,
        source_ref, source_version, source_snapshot_sha256
      ) VALUES (
        (target_record ->> 'directive_id')::uuid,
        (target_record ->> 'person_id')::uuid,
        target_record ->> 'directive',
        true,
        NULLIF(target_record ->> 'expires_at', '')::timestamptz,
        target_record ->> 'source_ref',
        (target_record ->> 'source_version')::integer,
        target_record ->> 'source_snapshot_sha256'
      );
    ELSIF target_kind = 'legacy_role_candidate' THEN
      INSERT INTO identity.legacy_role_labels (
        label_id, person_id, role_label, perspective, source_ref,
        source_version, source_snapshot_sha256
      ) VALUES (
        (target_record ->> 'label_id')::uuid,
        (target_record ->> 'person_id')::uuid,
        target_record ->> 'role_label',
        'unknown',
        target_record ->> 'source_ref',
        NULLIF(target_record ->> 'source_version', '')::integer,
        target_record ->> 'source_snapshot_sha256'
      );
    ELSE
      INSERT INTO identity.legacy_relationship_candidates (
        candidate_id, from_person_id, to_person_id, relationship_label,
        relationship_status, perspective, authoritative, source_ref,
        source_snapshot_sha256
      ) VALUES (
        (target_record ->> 'candidate_id')::uuid,
        (target_record ->> 'from_person_id')::uuid,
        (target_record ->> 'to_person_id')::uuid,
        target_record ->> 'relationship_label',
        target_record ->> 'relationship_status',
        'unknown',
        false,
        target_record ->> 'source_ref',
        target_record ->> 'source_snapshot_sha256'
      );
    END IF;
    END LOOP;

    FOR target_projection IN
    SELECT projection
      FROM pg_catalog.jsonb_array_elements(document -> 'projections')
           WITH ORDINALITY AS item(projection, ordinal)
     ORDER BY ordinal
  LOOP
    target_kind := target_projection ->> 'decision_kind';
    target_decision_id := (target_projection ->> 'decision_id')::uuid;
    target_receipt_id := (target_projection ->> 'receipt_id')::uuid;
    target_lineage := target_projection -> 'lineage';
    INSERT INTO operations.reviewed_identity_migration_item_receipts (
      receipt_id, run_id, decision_id, decision_kind, decision_disposition,
      candidate_commitment, outcome, projection_table_kind,
      projection_ref_commitment, projection_commitment, receipt_commitment,
      database_transaction_id
    ) VALUES (
      target_receipt_id, admission.run_id, target_decision_id, target_kind,
      'apply', target_projection ->> 'candidate_commitment', 'inserted_exact',
      target_projection ->> 'projection_table_kind',
      target_projection ->> 'projection_ref_commitment',
      target_projection ->> 'projection_commitment',
      target_projection ->> 'receipt_commitment', current_txid
    );
    INSERT INTO operations.reviewed_identity_migration_projection_lineage (
      lineage_id, run_id, receipt_id, decision_id, decision_kind,
      projection_table_kind, projection_id, projection_ref_commitment,
      lineage_commitment
    ) VALUES (
      target_receipt_id, admission.run_id, target_receipt_id,
      target_decision_id, target_kind,
      target_projection ->> 'projection_table_kind',
      (target_lineage ->> 'projection_id')::uuid,
      target_projection ->> 'projection_ref_commitment',
      target_lineage ->> 'lineage_commitment'
    );
    FOR target_subject IN
      SELECT subject
        FROM pg_catalog.jsonb_array_elements(target_lineage -> 'subjects')
             WITH ORDINALITY AS item(subject, ordinal)
       ORDER BY ordinal
    LOOP
      INSERT INTO operations.reviewed_identity_migration_projection_subjects (
        lineage_id, person_id, subject_role
      ) VALUES (
        target_receipt_id,
        (target_subject ->> 'person_id')::uuid,
        target_subject ->> 'subject_role'
      );
    END LOOP;
    END LOOP;

    FOR target_effect IN
    SELECT effect
      FROM pg_catalog.jsonb_array_elements(document -> 'auto_expiry_effects')
           WITH ORDINALITY AS item(effect, ordinal)
     ORDER BY ordinal
  LOOP
    INSERT INTO privacy.auto_expiry_schedules (
      schedule_id, person_id, directive_id, outbox_id, due_at, state
    ) VALUES (
      (target_effect ->> 'schedule_id')::uuid,
      (target_effect ->> 'person_id')::uuid,
      (target_effect ->> 'directive_id')::uuid,
      (target_effect ->> 'outbox_id')::uuid,
      (target_effect ->> 'due_at')::timestamptz,
      'scheduled'
    );
    INSERT INTO operations.outbox (
      outbox_id, topic, aggregate_id, payload, available_at
    ) VALUES (
      (target_effect ->> 'outbox_id')::uuid,
      'privacy.person.auto_expire',
      (target_effect ->> 'person_id')::uuid,
      pg_catalog.jsonb_build_object(
        'version', 1,
        'schedule_id', target_effect ->> 'schedule_id',
        'person_id', target_effect ->> 'person_id',
        'directive_id', target_effect ->> 'directive_id',
        'policy_digest', admission.policy_digest
      ),
      (target_effect ->> 'due_at')::timestamptz
    );
    END LOOP;

    INSERT INTO operations.reviewed_identity_migration_finalizations (
    finalization_id, run_id, contract_version, verification_status,
    authoritative, source_item_count, decision_count, apply_decision_count,
    receipt_count, source_manifest_commitment, projection_manifest_commitment,
    decision_manifest_commitment, receipt_set_commitment,
    finalization_commitment, database_transaction_id, signature_algorithm,
    signing_key_fingerprint, finalization_signature
  ) VALUES (
    admission.finalization_id, admission.run_id,
    'reviewed-identity-migration-finalization-v1',
    'candidate_unverified', false, source_count, decision_count,
    apply_decision_count, apply_decision_count,
    migration_run.logical_source_manifest_commitment,
    migration_run.projection_manifest_commitment,
    admission.decision_manifest_commitment,
    admission.receipt_set_commitment,
    admission.finalization_commitment, current_txid, 'ed25519',
    admission.finalization_signing_key_fingerprint,
    document -> 'finalization_attestation' ->> 'finalization_signature'
    );
    UPDATE operations.reviewed_identity_finalizer_admissions
           AS consumed_admission
       SET consumed_at = pg_catalog.transaction_timestamp()
     WHERE consumed_admission.admission_id = admission.admission_id
       AND consumed_admission.consumed_at IS NULL;
    GET DIAGNOSTICS affected_rows = ROW_COUNT;
    IF affected_rows <> 1 THEN
      RAISE EXCEPTION 'identity_finalizer_admission_consume_failed'
        USING ERRCODE = '55000';
    END IF;
    RETURN admission.finalization_id;
  EXCEPTION
    WHEN unique_violation OR exclusion_violation THEN
      RAISE EXCEPTION 'identity_finalizer_projection_conflict'
        USING ERRCODE = '23505';
    WHEN not_null_violation OR foreign_key_violation OR check_violation THEN
      RAISE EXCEPTION 'identity_finalizer_projection_rejected'
        USING ERRCODE = '55000';
  END;
EXCEPTION
  -- PostgreSQL's native cast and width errors can echo private source values
  -- in DETAIL. Replace only those data-bearing classes. Serialization,
  -- deadlock, lock-timeout, query-cancel, and transaction-timeout SQLSTATEs
  -- are intentionally not intercepted so callers retain correct retry logic.
  WHEN character_not_in_repertoire OR untranslatable_character
       OR invalid_text_representation OR invalid_datetime_format
       OR datetime_field_overflow OR numeric_value_out_of_range
       OR string_data_right_truncation THEN
    RAISE EXCEPTION 'identity_finalizer_private_document_rejected'
      USING ERRCODE = '22023';
END;
"""


EVIDENCE_TABLES = (
    "operations.reviewed_identity_migration_runs",
    "operations.reviewed_identity_migration_source_items",
    "operations.reviewed_identity_migration_decisions",
    "operations.reviewed_identity_migration_item_receipts",
    "operations.reviewed_identity_migration_finalizations",
    "operations.reviewed_identity_migration_projection_lineage",
    "operations.reviewed_identity_migration_projection_subjects",
    "operations.reviewed_identity_migration_erasure_impacts",
    "operations.legacy_identity_writer_evidence",
    "operations.privacy_cutover_check_receipts",
    "operations.semantic_authority_cutovers",
    ADMISSION,
)


def _prepare_admission_table() -> None:
    op.execute(
        f"""
        DO $admission_bootstrap$
        DECLARE
          occupied regclass := pg_catalog.to_regclass('{ADMISSION}');
          occupied_rows boolean;
          actual_columns text[];
          actual_constraints text[];
          actual_indexes text[];
          actual_column_mismatch_positions integer[];
          actual_constraint_mismatch_positions integer[];
          actual_index_mismatch_positions integer[];
          actual_columns_sha256 text;
          expected_columns_sha256 text;
          actual_constraints_sha256 text;
          expected_constraints_sha256 text;
          actual_indexes_sha256 text;
          expected_indexes_sha256 text;
          actual_owner_privileges text[];
          actual_unexpected_acl_count bigint;
          actual_missing_key_shape_count bigint;
          actual_invalid_constraint_count bigint;
          actual_policy_count bigint;
          actual_user_trigger_count bigint;
          actual_rewrite_count bigint;
          actual_security_label_count bigint;
          actual_publication_count bigint;
          actual_relkind text;
          actual_relpersistence text;
          actual_owner_matches boolean;
          actual_is_partition boolean;
          actual_row_security boolean;
          actual_force_row_security boolean;
          actual_has_rules boolean;
          actual_has_triggers boolean;
          actual_replica_identity text;
          actual_trigger_count integer;
          actual_insert_ri_trigger_count integer;
          actual_update_ri_trigger_count integer;
          expected_columns constant text[] := ARRAY[
            'admission_id|uuid|true|||',
            'run_id|uuid|true|||',
            'finalization_id|uuid|true|||',
            'contract_version|character varying(64)|true|||',
            'verification_status|character varying(32)|true|||',
            'document_sha256|character varying(64)|true|||',
            'document_octets|integer|true|||',
            'finalization_commitment|character varying(64)|true|||',
            'decision_manifest_commitment|character varying(64)|true|||',
            'receipt_set_commitment|character varying(64)|true|||',
            'lineage_set_commitment|character varying(64)|true|||',
            'privacy_closure_set_commitment|character varying(64)|true|||',
            'auto_expiry_effect_set_commitment|character varying(64)|true|||',
            'review_receipt_commitment|character varying(64)|true|||',
            'release_manifest_digest|character varying(64)|true|||',
            'migration_tool_bundle_digest|character varying(64)|true|||',
            'core_oci_manifest_digest|character varying(64)|true|||',
            'core_schema_digest|character varying(64)|true|||',
            'core_capability_digest|character varying(64)|true|||',
            'policy_digest|character varying(64)|true|||',
            'review_signing_key_fingerprint|character varying(64)|true|||',
            'finalization_signing_key_fingerprint|character varying(64)|true|||',
            'verifier_bundle_digest|character varying(64)|true|||',
            'admitted_at|timestamp with time zone|true|'
              'transaction_timestamp()||',
            'expires_at|timestamp with time zone|true|||',
            'consumed_at|timestamp with time zone|false|||'
          ]::text[];
          expected_constraints constant text[] := ARRAY[
            'ck_reviewed_identity_finalizer_admissions_digest_shape|c|true|false|false',
            'ck_reviewed_identity_finalizer_admissions_document_size|c|true|false|false',
            'ck_reviewed_identity_finalizer_admissions_fixed_contract|c|true|false|false',
            'ck_reviewed_identity_finalizer_admissions_one_time_window|c|true|false|false',
            'ck_reviewed_identity_finalizer_admissions_uuidv7_ids|c|true|false|false',
            'fk_identity_finalizer_admission_run|f|true|false|false',
            'pk_reviewed_identity_finalizer_admissions|p|true|false|false',
            'uq_reviewed_identity_finalizer_admissions_document_sha256|u|true|false|false',
            'uq_reviewed_identity_finalizer_admissions_finalization__1d9a|u|true|false|false',
            'uq_reviewed_identity_finalizer_admissions_finalization_id|u|true|false|false',
            'uq_reviewed_identity_finalizer_admissions_run_id|u|true|false|false'
          ]::text[];
          expected_indexes constant text[] := ARRAY[
            'pk_reviewed_identity_finalizer_admissions|true|true|true|true|true|btree',
            'uq_reviewed_identity_finalizer_admissions_document_sha256|true|false|true|true|true|btree',
            'uq_reviewed_identity_finalizer_admissions_finalization__1d9a|true|false|true|true|true|btree',
            'uq_reviewed_identity_finalizer_admissions_finalization_id|true|false|true|true|true|btree',
            'uq_reviewed_identity_finalizer_admissions_run_id|true|false|true|true|true|btree'
          ]::text[];
          owner_oid oid;
        BEGIN
          IF occupied IS NOT NULL THEN
            SELECT oid INTO STRICT owner_oid
              FROM pg_catalog.pg_roles
             WHERE rolname = 'home_agent_owner';
            SELECT table_row.relkind::text,
                   table_row.relpersistence::text,
                   table_row.relowner = owner_oid,
                   table_row.relispartition,
                   table_row.relrowsecurity,
                   table_row.relforcerowsecurity,
                   table_row.relhasrules,
                   table_row.relhastriggers,
                   table_row.relreplident::text
              INTO STRICT actual_relkind,
                          actual_relpersistence,
                          actual_owner_matches,
                          actual_is_partition,
                          actual_row_security,
                          actual_force_row_security,
                          actual_has_rules,
                          actual_has_triggers,
                          actual_replica_identity
              FROM pg_catalog.pg_class AS table_row
             WHERE table_row.oid = occupied;
            IF actual_relkind IS DISTINCT FROM 'r'
               OR actual_relpersistence IS DISTINCT FROM 'p'
               OR actual_owner_matches IS DISTINCT FROM true
               OR actual_is_partition IS DISTINCT FROM false
               OR actual_row_security IS DISTINCT FROM false
               OR actual_force_row_security IS DISTINCT FROM false
               OR actual_has_rules IS DISTINCT FROM false
               OR actual_has_triggers IS DISTINCT FROM true
               OR actual_replica_identity IS DISTINCT FROM 'd' THEN
              RAISE EXCEPTION
                'identity_finalizer_admission_bootstrap_shape_invalid'
                USING ERRCODE = '55000',
                      DETAIL = pg_catalog.format(
                        'class relkind=%s persistence=%s owner_match=%s '
                        'partition=%s rls=%s force_rls=%s rules=%s '
                        'triggers=%s replica_identity=%s',
                        actual_relkind,
                        actual_relpersistence,
                        actual_owner_matches,
                        actual_is_partition,
                        actual_row_security,
                        actual_force_row_security,
                        actual_has_rules,
                        actual_has_triggers,
                        actual_replica_identity
                      );
            END IF;
            SELECT pg_catalog.count(*),
                   pg_catalog.count(*) FILTER (
                     WHERE trigger_row.tgisinternal
                       AND trigger_row.tgenabled::text = 'O'
                       AND trigger_row.tgparentid = 0
                       AND NOT trigger_row.tgdeferrable
                       AND NOT trigger_row.tginitdeferred
                       AND trigger_row.tgnargs = 0
                       AND trigger_row.tgqual IS NULL
                       AND trigger_row.tgoldtable IS NULL
                       AND trigger_row.tgnewtable IS NULL
                       AND constraint_row.conrelid = occupied
                       AND constraint_row.conname =
                             'fk_identity_finalizer_admission_run'
                       AND constraint_row.contype::text = 'f'
                       AND trigger_row.tgconstrrelid =
                             constraint_row.confrelid
                       AND trigger_row.tgconstrindid =
                             constraint_row.conindid
                       AND trigger_row.tgfoid =
                             pg_catalog.to_regprocedure(
                               'pg_catalog."RI_FKey_check_ins"()'
                             )
                       AND trigger_row.tgtype = 5
                   ),
                   pg_catalog.count(*) FILTER (
                     WHERE trigger_row.tgisinternal
                       AND trigger_row.tgenabled::text = 'O'
                       AND trigger_row.tgparentid = 0
                       AND NOT trigger_row.tgdeferrable
                       AND NOT trigger_row.tginitdeferred
                       AND trigger_row.tgnargs = 0
                       AND trigger_row.tgqual IS NULL
                       AND trigger_row.tgoldtable IS NULL
                       AND trigger_row.tgnewtable IS NULL
                       AND constraint_row.conrelid = occupied
                       AND constraint_row.conname =
                             'fk_identity_finalizer_admission_run'
                       AND constraint_row.contype::text = 'f'
                       AND trigger_row.tgconstrrelid =
                             constraint_row.confrelid
                       AND trigger_row.tgconstrindid =
                             constraint_row.conindid
                       AND trigger_row.tgfoid =
                             pg_catalog.to_regprocedure(
                               'pg_catalog."RI_FKey_check_upd"()'
                             )
                       AND trigger_row.tgtype = 17
                   )
              INTO STRICT actual_trigger_count,
                          actual_insert_ri_trigger_count,
                          actual_update_ri_trigger_count
              FROM pg_catalog.pg_trigger AS trigger_row
              LEFT JOIN pg_catalog.pg_constraint AS constraint_row
                ON constraint_row.oid = trigger_row.tgconstraint
             WHERE trigger_row.tgrelid = occupied;
            IF actual_trigger_count <> 2
               OR actual_insert_ri_trigger_count <> 1
               OR actual_update_ri_trigger_count <> 1 THEN
              RAISE EXCEPTION
                'identity_finalizer_admission_bootstrap_shape_invalid'
                USING ERRCODE = '55000',
                      DETAIL = pg_catalog.format(
                        'ri_triggers total=%s insert_exact=%s update_exact=%s',
                        actual_trigger_count,
                        actual_insert_ri_trigger_count,
                        actual_update_ri_trigger_count
                      );
            END IF;
            EXECUTE 'SELECT EXISTS (SELECT 1 FROM {ADMISSION})'
              INTO occupied_rows;
            IF occupied_rows THEN
              RAISE EXCEPTION 'identity_finalizer_admission_preexisting_evidence'
                USING ERRCODE = '55000';
            END IF;
            SELECT pg_catalog.array_agg(
                     attribute.attname || '|' ||
                     pg_catalog.format_type(
                       attribute.atttypid, attribute.atttypmod
                     ) || '|' ||
                     attribute.attnotnull::text || '|' ||
                     coalesce(
                       pg_catalog.pg_get_expr(
                         default_value.adbin, default_value.adrelid
                       ),
                       ''
                     ) || '|' || attribute.attidentity::text || '|' ||
                     attribute.attgenerated::text
                     ORDER BY attribute.attnum
                   )
              INTO STRICT actual_columns
              FROM pg_catalog.pg_attribute AS attribute
              LEFT JOIN pg_catalog.pg_attrdef AS default_value
                ON default_value.adrelid = attribute.attrelid
               AND default_value.adnum = attribute.attnum
             WHERE attribute.attrelid = occupied
               AND attribute.attnum > 0
               AND NOT attribute.attisdropped;
            SELECT pg_catalog.array_agg(
                     constraint_row.conname || '|' ||
                     constraint_row.contype::text || '|' ||
                     constraint_row.convalidated::text || '|' ||
                     constraint_row.condeferrable::text || '|' ||
                     constraint_row.condeferred::text
                     ORDER BY constraint_row.conname
                   )
              INTO STRICT actual_constraints
              FROM pg_catalog.pg_constraint AS constraint_row
             WHERE constraint_row.conrelid = occupied;
            SELECT pg_catalog.array_agg(
                     index_row.relname || '|' ||
                     index_contract.indisunique::text || '|' ||
                     index_contract.indisprimary::text || '|' ||
                     index_contract.indisvalid::text || '|' ||
                     index_contract.indisready::text || '|' ||
                     index_contract.indislive::text || '|' ||
                     access_method.amname
                     ORDER BY index_row.relname
                   )
              INTO STRICT actual_indexes
              FROM pg_catalog.pg_index AS index_contract
              JOIN pg_catalog.pg_class AS index_row
                ON index_row.oid = index_contract.indexrelid
              JOIN pg_catalog.pg_am AS access_method
                ON access_method.oid = index_row.relam
             WHERE index_contract.indrelid = occupied;
            SELECT
              pg_catalog.encode(
                pg_catalog.sha256(
                  pg_catalog.convert_to(actual_columns::text, 'UTF8')
                ),
                'hex'
              ),
              pg_catalog.encode(
                pg_catalog.sha256(
                  pg_catalog.convert_to(expected_columns::text, 'UTF8')
                ),
                'hex'
              ),
              pg_catalog.encode(
                pg_catalog.sha256(
                  pg_catalog.convert_to(actual_constraints::text, 'UTF8')
                ),
                'hex'
              ),
              pg_catalog.encode(
                pg_catalog.sha256(
                  pg_catalog.convert_to(expected_constraints::text, 'UTF8')
                ),
                'hex'
              ),
              pg_catalog.encode(
                pg_catalog.sha256(
                  pg_catalog.convert_to(actual_indexes::text, 'UTF8')
                ),
                'hex'
              ),
              pg_catalog.encode(
                pg_catalog.sha256(
                  pg_catalog.convert_to(expected_indexes::text, 'UTF8')
                ),
                'hex'
              )
              INTO STRICT actual_columns_sha256,
                          expected_columns_sha256,
                          actual_constraints_sha256,
                          expected_constraints_sha256,
                          actual_indexes_sha256,
                          expected_indexes_sha256;
            SELECT pg_catalog.array_agg(
                     mismatch_position ORDER BY mismatch_position
                   )
              INTO STRICT actual_column_mismatch_positions
              FROM pg_catalog.generate_series(
                     1,
                     CASE
                       WHEN pg_catalog.cardinality(actual_columns) >
                            pg_catalog.cardinality(expected_columns)
                       THEN pg_catalog.cardinality(actual_columns)
                       ELSE pg_catalog.cardinality(expected_columns)
                     END
                   ) AS mismatch_series(mismatch_position)
             WHERE actual_columns[mismatch_position]
                   IS DISTINCT FROM expected_columns[mismatch_position];
            SELECT pg_catalog.array_agg(
                     mismatch_position ORDER BY mismatch_position
                   )
              INTO STRICT actual_constraint_mismatch_positions
              FROM pg_catalog.generate_series(
                     1,
                     CASE
                       WHEN pg_catalog.cardinality(actual_constraints) >
                            pg_catalog.cardinality(expected_constraints)
                       THEN pg_catalog.cardinality(actual_constraints)
                       ELSE pg_catalog.cardinality(expected_constraints)
                     END
                   ) AS mismatch_series(mismatch_position)
             WHERE actual_constraints[mismatch_position]
                   IS DISTINCT FROM expected_constraints[mismatch_position];
            SELECT pg_catalog.array_agg(
                     mismatch_position ORDER BY mismatch_position
                   )
              INTO STRICT actual_index_mismatch_positions
              FROM pg_catalog.generate_series(
                     1,
                     CASE
                       WHEN pg_catalog.cardinality(actual_indexes) >
                            pg_catalog.cardinality(expected_indexes)
                       THEN pg_catalog.cardinality(actual_indexes)
                       ELSE pg_catalog.cardinality(expected_indexes)
                     END
                   ) AS mismatch_series(mismatch_position)
             WHERE actual_indexes[mismatch_position]
                   IS DISTINCT FROM expected_indexes[mismatch_position];
            SELECT pg_catalog.array_agg(
                     acl.privilege_type ORDER BY acl.privilege_type
                   ) FILTER (
                     WHERE acl.grantee = owner_oid
                       AND acl.grantor = owner_oid
                       AND NOT acl.is_grantable
                   ),
                   pg_catalog.count(*) FILTER (
                     WHERE acl.grantee <> owner_oid
                        OR acl.grantor <> owner_oid
                        OR acl.is_grantable
                   )
              INTO STRICT actual_owner_privileges,
                          actual_unexpected_acl_count
              FROM pg_catalog.pg_class AS table_row
              CROSS JOIN LATERAL pg_catalog.aclexplode(
                coalesce(
                  table_row.relacl,
                  pg_catalog.acldefault('r', table_row.relowner)
                )
              ) AS acl
             WHERE table_row.oid = occupied;
            SELECT pg_catalog.count(*)
              INTO STRICT actual_missing_key_shape_count
              FROM (VALUES
                ('pk_reviewed_identity_finalizer_admissions',
                  ARRAY['admission_id']::text[]),
                ('uq_reviewed_identity_finalizer_admissions_run_id',
                  ARRAY['run_id']::text[]),
                ('uq_reviewed_identity_finalizer_admissions_finalization_id',
                  ARRAY['finalization_id']::text[]),
                ('uq_reviewed_identity_finalizer_admissions_document_sha256',
                  ARRAY['document_sha256']::text[]),
                ('uq_reviewed_identity_finalizer_admissions_finalization__1d9a',
                  ARRAY['finalization_commitment']::text[]),
                ('fk_identity_finalizer_admission_run',
                  ARRAY['run_id']::text[])
              ) AS expected_constraint(name, column_names)
             WHERE NOT EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_constraint AS constraint_row
                WHERE constraint_row.conrelid = occupied
                  AND constraint_row.conname = expected_constraint.name
                  AND ARRAY(
                    SELECT attribute.attname::text
                      FROM pg_catalog.unnest(constraint_row.conkey)
                           WITH ORDINALITY AS key_column(attnum, ordinal)
                      JOIN pg_catalog.pg_attribute AS attribute
                        ON attribute.attrelid = occupied
                       AND attribute.attnum = key_column.attnum
                     ORDER BY key_column.ordinal
                  ) = expected_constraint.column_names
             );
            SELECT pg_catalog.count(*)
              INTO STRICT actual_invalid_constraint_count
              FROM pg_catalog.pg_constraint AS constraint_row
             WHERE constraint_row.conrelid = occupied
               AND (
                 (
                   constraint_row.contype = 'c'
                   AND constraint_row.connoinherit
                 )
                 OR (
                   constraint_row.contype IN ('p','u')
                   AND (
                     constraint_row.conindid = 0
                     OR NOT EXISTS (
                       SELECT 1
                         FROM pg_catalog.pg_index AS backing_index
                        WHERE backing_index.indexrelid =
                              constraint_row.conindid
                          AND backing_index.indrelid = occupied
                          AND backing_index.indpred IS NULL
                          AND backing_index.indexprs IS NULL
                     )
                   )
                 )
                 OR (
                   constraint_row.contype = 'f'
                   AND (
                     constraint_row.confrelid <>
                       'operations.reviewed_identity_migration_runs'
                         ::regclass
                     OR constraint_row.confupdtype <> 'a'
                     OR constraint_row.confdeltype <> 'a'
                     OR constraint_row.confmatchtype <> 's'
                     OR constraint_row.conkey <> ARRAY[
                       (
                         SELECT attnum
                           FROM pg_catalog.pg_attribute
                          WHERE attrelid = occupied
                            AND attname = 'run_id'
                       )
                     ]::smallint[]
                   )
                 )
               );
            SELECT
              (SELECT pg_catalog.count(*) FROM pg_catalog.pg_policy
                WHERE polrelid = occupied),
              (SELECT pg_catalog.count(*) FROM pg_catalog.pg_trigger
                WHERE tgrelid = occupied AND NOT tgisinternal),
              (SELECT pg_catalog.count(*) FROM pg_catalog.pg_rewrite
                WHERE ev_class = occupied AND rulename <> '_RETURN'),
              (SELECT pg_catalog.count(*) FROM pg_catalog.pg_seclabel
                WHERE classoid = 'pg_catalog.pg_class'::regclass
                  AND objoid = occupied),
              (SELECT pg_catalog.count(*) FROM pg_catalog.pg_publication_rel
                WHERE prrelid = occupied)
              INTO STRICT actual_policy_count,
                          actual_user_trigger_count,
                          actual_rewrite_count,
                          actual_security_label_count,
                          actual_publication_count;
            IF actual_columns IS DISTINCT FROM expected_columns
               OR actual_constraints IS DISTINCT FROM expected_constraints
               OR actual_indexes IS DISTINCT FROM expected_indexes
               OR actual_owner_privileges IS DISTINCT FROM ARRAY[
                 'DELETE','INSERT','MAINTAIN','REFERENCES','SELECT','TRIGGER',
                 'TRUNCATE','UPDATE'
               ]::text[]
               OR actual_unexpected_acl_count <> 0
               OR actual_missing_key_shape_count <> 0
               OR actual_invalid_constraint_count <> 0
               OR actual_policy_count <> 0
               OR actual_user_trigger_count <> 0
               OR actual_rewrite_count <> 0
               OR actual_security_label_count <> 0
               OR actual_publication_count <> 0 THEN
              RAISE EXCEPTION
                'identity_finalizer_admission_bootstrap_shape_invalid'
                USING ERRCODE = '55000',
                      DETAIL = pg_catalog.format(
                        'columns count=%s actual_sha256=%s expected_sha256=%s '
                        'mismatch_positions=%s constraints count=%s '
                        'actual_sha256=%s expected_sha256=%s '
                        'mismatch_positions=%s indexes count=%s '
                        'actual_sha256=%s expected_sha256=%s '
                        'mismatch_positions=%s owner_privileges=%s '
                        'unexpected_acl_count=%s missing_key_shape_count=%s '
                        'invalid_constraint_count=%s policies=%s '
                        'user_triggers=%s rewrites=%s security_labels=%s '
                        'publications=%s',
                        pg_catalog.cardinality(actual_columns),
                        actual_columns_sha256,
                        expected_columns_sha256,
                        actual_column_mismatch_positions,
                        pg_catalog.cardinality(actual_constraints),
                        actual_constraints_sha256,
                        expected_constraints_sha256,
                        actual_constraint_mismatch_positions,
                        pg_catalog.cardinality(actual_indexes),
                        actual_indexes_sha256,
                        expected_indexes_sha256,
                        actual_index_mismatch_positions,
                        actual_owner_privileges,
                        actual_unexpected_acl_count,
                        actual_missing_key_shape_count,
                        actual_invalid_constraint_count,
                        actual_policy_count,
                        actual_user_trigger_count,
                        actual_rewrite_count,
                        actual_security_label_count,
                        actual_publication_count
                      );
            END IF;
            DROP TABLE {ADMISSION};
          END IF;
        END
        $admission_bootstrap$
        """
    )
    op.execute(CreateTable(schema.reviewed_identity_finalizer_admissions))
    op.execute(
        f"""
        COMMENT ON TABLE {ADMISSION} IS
          'Content-free, short-lived one-time admission for one exact verified '
          'E3 finalizer document digest; never semantic authority or cutover.';
        ALTER TABLE {ADMISSION} ENABLE ROW LEVEL SECURITY;
        ALTER TABLE {ADMISSION} FORCE ROW LEVEL SECURITY;
        CREATE POLICY reviewed_identity_finalizer_admissions_owner_select
          ON {ADMISSION} FOR SELECT TO home_agent_owner
          USING (session_user = 'home_agent_owner');
        CREATE POLICY reviewed_identity_finalizer_admissions_owner_insert
          ON {ADMISSION} FOR INSERT TO home_agent_owner
          WITH CHECK (session_user = 'home_agent_owner');
        CREATE POLICY reviewed_identity_finalizer_admissions_e3_kernel_select
          ON {ADMISSION} FOR SELECT TO {KERNEL_ROLE}
          USING (
            session_user = '{FINALIZER_ROLE}'
            AND current_user = '{KERNEL_ROLE}'
            AND NOT pg_catalog.pg_has_role(
              session_user, '{KERNEL_ROLE}', 'SET'
            )
          );
        CREATE POLICY reviewed_identity_finalizer_admissions_e3_kernel_update
          ON {ADMISSION} FOR UPDATE TO {KERNEL_ROLE}
          USING (
            session_user = '{FINALIZER_ROLE}'
            AND current_user = '{KERNEL_ROLE}'
            AND NOT pg_catalog.pg_has_role(
              session_user, '{KERNEL_ROLE}', 'SET'
            )
          )
          WITH CHECK (
            session_user = '{FINALIZER_ROLE}'
            AND current_user = '{KERNEL_ROLE}'
            AND NOT pg_catalog.pg_has_role(
              session_user, '{KERNEL_ROLE}', 'SET'
            )
          );
        """
    )


def _install_shared_write_fence() -> None:
    op.execute(
        f"""
        DO $fence_bootstrap$
        DECLARE
          occupied regclass := pg_catalog.to_regclass('{FENCE_TABLE}');
        BEGIN
          IF occupied IS NOT NULL THEN
            RAISE EXCEPTION 'identity_finalizer_write_fence_name_occupied'
              USING ERRCODE = '55000';
          END IF;
          IF pg_catalog.to_regprocedure('{FENCE_FUNCTION}') IS NOT NULL
             OR pg_catalog.to_regprocedure('{FENCE_TRIGGER_FUNCTION}') IS NOT NULL
             OR EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_trigger AS trigger_row
                WHERE trigger_row.tgrelid =
                      'privacy.subject_retrieval_blocks'::regclass
                  AND trigger_row.tgname =
                      'subject_retrieval_blocks_identity_write_fence'
                  AND NOT trigger_row.tgisinternal
             ) THEN
            RAISE EXCEPTION 'identity_finalizer_write_fence_object_occupied'
              USING ERRCODE = '55000';
          END IF;
        END
        $fence_bootstrap$;

        CREATE TABLE {FENCE_TABLE} (
          fence_key varchar(16) PRIMARY KEY,
          fence_generation bigint NOT NULL,
          created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
          CONSTRAINT ck_identity_semantic_write_fence_singleton
            CHECK (fence_key = 'global' AND fence_generation = 1)
        );
        INSERT INTO {FENCE_TABLE} (fence_key, fence_generation)
          VALUES ('global', 1);
        COMMENT ON TABLE {FENCE_TABLE} IS
          'Content-free singleton write fence shared by E3 finalization and '
          'every E2 subject-tombstone insertion; no-op updates establish order.';
        ALTER TABLE {FENCE_TABLE} ENABLE ROW LEVEL SECURITY;
        ALTER TABLE {FENCE_TABLE} FORCE ROW LEVEL SECURITY;
        CREATE POLICY identity_semantic_write_fence_owner
          ON {FENCE_TABLE} FOR ALL TO home_agent_owner
          USING (current_user = 'home_agent_owner')
          WITH CHECK (current_user = 'home_agent_owner');

        CREATE FUNCTION privacy.lock_identity_semantic_write_fence()
        RETURNS void
        LANGUAGE plpgsql
        VOLATILE
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $fence${FENCE_LOCK_BODY}$fence$;

        CREATE FUNCTION privacy.fence_identity_tombstone_write()
        RETURNS trigger
        LANGUAGE plpgsql
        VOLATILE
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $fence${FENCE_TRIGGER_BODY}$fence$;

        CREATE TRIGGER subject_retrieval_blocks_identity_write_fence
        BEFORE INSERT OR UPDATE OF person_id
        ON privacy.subject_retrieval_blocks
        FOR EACH ROW EXECUTE FUNCTION
          privacy.fence_identity_tombstone_write();

        REVOKE ALL ON FUNCTION {FENCE_FUNCTION},
          {FENCE_TRIGGER_FUNCTION}
          FROM PUBLIC, home_agent_api, home_agent_binding_operator,
          home_agent_ingest, home_agent_worker, home_agent_erasure,
          home_agent_rollout, home_agent_backup, home_agent_identity_migration,
          home_agent_identity_kernel, {FINALIZER_ROLE}, {KERNEL_ROLE};
        GRANT EXECUTE ON FUNCTION {FENCE_FUNCTION} TO {KERNEL_ROLE};
        """
    )


def _install_evidence_policies() -> None:
    selectable = (
        "operations.reviewed_identity_migration_runs",
        "operations.reviewed_identity_migration_source_items",
        "operations.reviewed_identity_migration_decisions",
        "operations.reviewed_identity_migration_erasure_impacts",
        "operations.legacy_identity_writer_evidence",
        "operations.privacy_cutover_check_receipts",
        "operations.semantic_authority_cutovers",
    )
    writable = (
        "operations.reviewed_identity_migration_item_receipts",
        "operations.reviewed_identity_migration_finalizations",
        "operations.reviewed_identity_migration_projection_lineage",
        "operations.reviewed_identity_migration_projection_subjects",
    )
    predicate = (
        f"session_user = '{FINALIZER_ROLE}' "
        f"AND current_user = '{KERNEL_ROLE}' "
        "AND NOT pg_catalog.pg_has_role("
        f"session_user, '{KERNEL_ROLE}', 'SET')"
    )
    for table in selectable:
        op.execute(f"DROP POLICY IF EXISTS {EVIDENCE_SELECT_POLICY} ON {table}")
        op.execute(
            f"CREATE POLICY {EVIDENCE_SELECT_POLICY} ON {table} "
            f"FOR SELECT TO {KERNEL_ROLE} USING ({predicate})"
        )
    # PostgreSQL applies UPDATE privilege and USING-policy checks to locking
    # reads. This policy admits only the finalizer's FOR SHARE; its false
    # WITH CHECK prevents the same column grant from changing a row.
    migration_run_table = "operations.reviewed_identity_migration_runs"
    op.execute(
        f"DROP POLICY IF EXISTS {MIGRATION_RUN_LOCK_POLICY} "
        f"ON {migration_run_table}"
    )
    op.execute(
        f"CREATE POLICY {MIGRATION_RUN_LOCK_POLICY} ON {migration_run_table} "
        f"FOR UPDATE TO {KERNEL_ROLE} USING ({predicate}) WITH CHECK (false)"
    )
    for table in writable:
        op.execute(f"DROP POLICY IF EXISTS {EVIDENCE_SELECT_POLICY} ON {table}")
        op.execute(f"DROP POLICY IF EXISTS {EVIDENCE_INSERT_POLICY} ON {table}")
        op.execute(
            f"CREATE POLICY {EVIDENCE_SELECT_POLICY} ON {table} FOR SELECT "
            f"TO {KERNEL_ROLE} USING ({predicate})"
        )
        op.execute(
            f"CREATE POLICY {EVIDENCE_INSERT_POLICY} ON {table} FOR INSERT "
            f"TO {KERNEL_ROLE} WITH CHECK ({predicate})"
        )


def _install_function_and_acl() -> None:
    op.execute(
        f"""
        GRANT USAGE, CREATE ON SCHEMA operations TO {KERNEL_ROLE};
        SET LOCAL ROLE {KERNEL_ROLE};
        CREATE FUNCTION operations.finalize_reviewed_identity_migration(
          finalizer_document bytea,
          admission_id uuid
        ) RETURNS uuid
        LANGUAGE plpgsql
        VOLATILE
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $e3${FUNCTION_BODY}$e3$;
        RESET ROLE;
        REVOKE CREATE ON SCHEMA operations FROM {KERNEL_ROLE};
        """
    )
    op.execute(
        f"""
        SET LOCAL ROLE {KERNEL_ROLE};
        REVOKE ALL ON FUNCTION {FUNCTION}
          FROM PUBLIC, home_agent_owner, home_agent_api,
          home_agent_binding_operator, home_agent_ingest, home_agent_worker,
          home_agent_erasure, home_agent_rollout, home_agent_backup,
          home_agent_identity_migration, home_agent_identity_kernel,
          {KERNEL_ROLE}, {FINALIZER_ROLE};
        GRANT EXECUTE ON FUNCTION {FUNCTION} TO {FINALIZER_ROLE};
        RESET ROLE;
        GRANT USAGE ON SCHEMA operations TO {FINALIZER_ROLE}, {KERNEL_ROLE};
        GRANT USAGE ON SCHEMA identity, privacy TO {KERNEL_ROLE};
        GRANT SELECT ON TABLE
          operations.reviewed_identity_migration_runs,
          operations.reviewed_identity_migration_source_items,
          operations.reviewed_identity_migration_decisions,
          operations.reviewed_identity_migration_erasure_impacts,
          operations.legacy_identity_writer_evidence,
          operations.privacy_cutover_check_receipts,
          operations.semantic_authority_cutovers
          TO {KERNEL_ROLE};
        GRANT UPDATE (expires_at) ON TABLE
          operations.reviewed_identity_migration_runs
          TO {KERNEL_ROLE};
        GRANT SELECT, UPDATE (consumed_at) ON TABLE {ADMISSION}
          TO {KERNEL_ROLE};
        GRANT SELECT, INSERT ON TABLE
          operations.reviewed_identity_migration_item_receipts,
          operations.reviewed_identity_migration_finalizations,
          operations.reviewed_identity_migration_projection_lineage,
          operations.reviewed_identity_migration_projection_subjects
          TO {KERNEL_ROLE};
        GRANT SELECT, INSERT ON TABLE identity.people TO {KERNEL_ROLE};
        GRANT UPDATE (
          status, status_source_ref, status_source_version,
          status_source_sha256, updated_at
        ) ON TABLE identity.people TO {KERNEL_ROLE};
        GRANT SELECT, INSERT ON TABLE
          identity.aliases,
          identity.external_recognition_bindings,
          identity.privacy_directives,
          identity.legacy_role_labels,
          identity.legacy_relationship_candidates,
          privacy.auto_expiry_schedules,
          operations.outbox
          TO {KERNEL_ROLE};
        GRANT EXECUTE ON FUNCTION privacy.identity_person_is_blocked(uuid)
          TO {KERNEL_ROLE};
        GRANT EXECUTE ON FUNCTION {FENCE_FUNCTION} TO {KERNEL_ROLE};
        """
    )


def _validate_dormant_role() -> None:
    op.execute(
        f"""
        DO $e3_role_admission$
        DECLARE
          owner_oid oid;
          login_oid oid;
          kernel_oid oid;
          login_config text[];
        BEGIN
          SELECT oid INTO STRICT owner_oid
            FROM pg_catalog.pg_roles
           WHERE rolname = 'home_agent_owner';
          SELECT oid, rolconfig INTO STRICT login_oid, login_config
            FROM pg_catalog.pg_roles
           WHERE rolname = '{FINALIZER_ROLE}';
          SELECT oid INTO STRICT kernel_oid
            FROM pg_catalog.pg_roles
           WHERE rolname = '{KERNEL_ROLE}';
          IF (
               SELECT version_num FROM public.alembic_version
             ) IS DISTINCT FROM '0012_identity_erasure_e2' THEN
            RAISE EXCEPTION 'identity_finalizer_e3_revision_invalid'
              USING ERRCODE = '55000';
          END IF;
          IF NOT EXISTS (
            SELECT 1
              FROM pg_catalog.pg_roles AS login_role
             WHERE login_role.rolname = '{FINALIZER_ROLE}'
               AND login_role.rolcanlogin
               AND NOT login_role.rolinherit
               AND NOT login_role.rolsuper
               AND NOT login_role.rolcreatedb
               AND NOT login_role.rolcreaterole
               AND NOT login_role.rolreplication
               AND NOT login_role.rolbypassrls
               AND login_role.rolconnlimit = 1
               AND login_role.rolvaliduntil IS NOT NULL
               AND login_role.rolvaliduntil =
                   timestamptz '1970-01-01 00:00:00+00'
          ) OR NOT EXISTS (
            SELECT 1
              FROM pg_catalog.pg_roles AS kernel_role
             WHERE kernel_role.rolname = '{KERNEL_ROLE}'
               AND NOT kernel_role.rolcanlogin
               AND NOT kernel_role.rolinherit
               AND NOT kernel_role.rolsuper
               AND NOT kernel_role.rolcreatedb
               AND NOT kernel_role.rolcreaterole
               AND NOT kernel_role.rolreplication
               AND NOT kernel_role.rolbypassrls
               AND kernel_role.rolconnlimit = 0
               AND kernel_role.rolvaliduntil IS NULL
               AND kernel_role.rolconfig IS NULL
          ) THEN
            RAISE EXCEPTION 'identity_finalizer_e3_dormant_role_invalid'
              USING ERRCODE = '42501';
          END IF;
          IF login_config IS NULL
             OR pg_catalog.cardinality(login_config) <> 6
             OR NOT (
               'default_transaction_isolation=serializable' =
                 ANY (login_config)
               AND 'statement_timeout=120s' = ANY (login_config)
               AND 'lock_timeout=5s' = ANY (login_config)
               AND 'idle_in_transaction_session_timeout=15s' =
                 ANY (login_config)
               AND 'transaction_timeout=180s' = ANY (login_config)
               AND 'log_parameter_max_length_on_error=0' =
                 ANY (login_config)
             )
             OR EXISTS (
               -- The six role-wide settings above are themselves represented
               -- here with setdatabase=0. Reject only per-database overlays,
               -- which could change the finalizer contract in one database.
               SELECT 1 FROM pg_catalog.pg_db_role_setting
                WHERE setrole IN (login_oid, kernel_oid)
                  AND setdatabase <> 0
             ) THEN
            RAISE EXCEPTION 'identity_finalizer_e3_role_config_invalid'
              USING ERRCODE = '42501';
          END IF;

          -- The sole membership involving the NOLOGIN kernel is the owner's
          -- non-inherited, SET-only administration edge established by
          -- provisioning. The finalizer login is not a member of any role and
          -- no role is a member of it.
          IF (
               SELECT pg_catalog.count(*)
                 FROM pg_catalog.pg_auth_members
                WHERE roleid = kernel_oid
             ) <> 1
             OR NOT EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_auth_members AS membership
                WHERE membership.roleid = kernel_oid
                  AND membership.member = owner_oid
                  AND NOT membership.admin_option
                  AND NOT membership.inherit_option
                  AND membership.set_option
             )
             OR EXISTS (
               SELECT 1 FROM pg_catalog.pg_auth_members
                WHERE member = kernel_oid
                   OR roleid = login_oid
                   OR member = login_oid
             ) THEN
            RAISE EXCEPTION 'identity_finalizer_e3_membership_invalid'
              USING ERRCODE = '42501';
          END IF;

          -- Before E3 grants anything, neither role may own an object or
          -- retain direct object/default/parameter ACLs from a partial install.
          IF EXISTS (
               SELECT 1 FROM pg_catalog.pg_shdepend
                WHERE refobjid IN (login_oid, kernel_oid)
                  AND deptype = 'o'
             )
             OR EXISTS (
               SELECT 1 FROM pg_catalog.pg_proc
                WHERE proowner IN (login_oid, kernel_oid)
             )
             OR EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_proc AS object_row
                 CROSS JOIN LATERAL
                   pg_catalog.aclexplode(object_row.proacl) AS acl
                WHERE acl.grantee IN (login_oid, kernel_oid)
             )
             OR EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_namespace AS object_row
                 CROSS JOIN LATERAL
                   pg_catalog.aclexplode(object_row.nspacl) AS acl
                WHERE acl.grantee IN (login_oid, kernel_oid)
             )
             OR EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_class AS object_row
                 CROSS JOIN LATERAL
                   pg_catalog.aclexplode(object_row.relacl) AS acl
                WHERE acl.grantee IN (login_oid, kernel_oid)
             )
             OR EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_attribute AS object_row
                 CROSS JOIN LATERAL
                   pg_catalog.aclexplode(object_row.attacl) AS acl
                WHERE acl.grantee IN (login_oid, kernel_oid)
             )
             OR EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_type AS object_row
                 CROSS JOIN LATERAL
                   pg_catalog.aclexplode(object_row.typacl) AS acl
                WHERE acl.grantee IN (login_oid, kernel_oid)
             )
             OR EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_default_acl AS object_row
                 CROSS JOIN LATERAL
                   pg_catalog.aclexplode(object_row.defaclacl) AS acl
                WHERE acl.grantee IN (login_oid, kernel_oid)
             )
             OR EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_parameter_acl AS object_row
                 CROSS JOIN LATERAL
                   pg_catalog.aclexplode(object_row.paracl) AS acl
                WHERE acl.grantee IN (login_oid, kernel_oid)
             ) THEN
            RAISE EXCEPTION 'identity_finalizer_e3_pregrant_authority_invalid'
              USING ERRCODE = '42501';
          END IF;

          IF NOT pg_catalog.has_database_privilege(
               '{FINALIZER_ROLE}', pg_catalog.current_database(), 'CONNECT'
             )
             OR pg_catalog.has_database_privilege(
               '{FINALIZER_ROLE}', pg_catalog.current_database(), 'CREATE'
             )
             OR pg_catalog.has_database_privilege(
               '{FINALIZER_ROLE}', pg_catalog.current_database(), 'TEMPORARY'
             )
             OR pg_catalog.has_database_privilege(
               '{KERNEL_ROLE}', pg_catalog.current_database(), 'CONNECT'
             )
             OR pg_catalog.has_database_privilege(
               '{KERNEL_ROLE}', pg_catalog.current_database(), 'CREATE'
             )
             OR pg_catalog.has_database_privilege(
               '{KERNEL_ROLE}', pg_catalog.current_database(), 'TEMPORARY'
             ) THEN
            RAISE EXCEPTION 'identity_finalizer_e3_database_acl_invalid'
              USING ERRCODE = '42501';
          END IF;
          IF EXISTS (
            SELECT 1
              FROM pg_catalog.pg_stat_activity
             WHERE usename IN ('{FINALIZER_ROLE}', '{KERNEL_ROLE}')
          ) THEN
            RAISE EXCEPTION 'identity_finalizer_e3_live_session_invalid'
              USING ERRCODE = '55000';
          END IF;
        END
        $e3_role_admission$
        """
    )


def _assert_installed_contract() -> None:
    if (
        hashlib.sha256(VERIFIER_BUNDLE_MANIFEST.encode("utf-8")).hexdigest()
        != VERIFIER_BUNDLE_DIGEST
    ):
        raise RuntimeError("identity finalizer verifier bundle digest mismatch")
    function_digest = hashlib.sha256(FUNCTION_BODY.encode("utf-8")).hexdigest()
    fence_lock_digest = hashlib.sha256(FENCE_LOCK_BODY.encode("utf-8")).hexdigest()
    fence_trigger_digest = hashlib.sha256(
        FENCE_TRIGGER_BODY.encode("utf-8")
    ).hexdigest()
    op.execute(
        f"""
        DO $e3_installed_contract$
        DECLARE
          owner_oid oid;
          login_oid oid;
          kernel_oid oid;
          actual_policies text[];
        BEGIN
          SELECT oid INTO STRICT owner_oid FROM pg_catalog.pg_roles
           WHERE rolname = 'home_agent_owner';
          SELECT oid INTO STRICT login_oid FROM pg_catalog.pg_roles
           WHERE rolname = '{FINALIZER_ROLE}';
          SELECT oid INTO STRICT kernel_oid FROM pg_catalog.pg_roles
           WHERE rolname = '{KERNEL_ROLE}';

          IF NOT EXISTS (
               SELECT 1 FROM pg_catalog.pg_class
                WHERE oid = '{ADMISSION}'::regclass
                  AND relowner = owner_oid
                  AND relkind = 'r'
                  AND relpersistence = 'p'
                  AND relrowsecurity
                  AND relforcerowsecurity
             )
             OR NOT EXISTS (
               SELECT 1 FROM pg_catalog.pg_class
                WHERE oid = '{FENCE_TABLE}'::regclass
                  AND relowner = owner_oid
                  AND relkind = 'r'
                  AND relpersistence = 'p'
                  AND relrowsecurity
                  AND relforcerowsecurity
             )
             OR (
               SELECT pg_catalog.count(*) FROM {FENCE_TABLE}
                WHERE fence_key = 'global' AND fence_generation = 1
             ) <> 1 THEN
            RAISE EXCEPTION 'identity_finalizer_e3_table_contract_invalid'
              USING ERRCODE = '55000';
          END IF;

          SELECT pg_catalog.array_agg(
                   policy.polname || '|' || policy.polcmd::text || '|' ||
                   policy.polpermissive::text || '|' ||
                   role_row.rolname || '|' ||
                   (policy.polqual IS NOT NULL)::text || '|' ||
                   (policy.polwithcheck IS NOT NULL)::text
                   ORDER BY policy.polname
                 )
            INTO STRICT actual_policies
            FROM pg_catalog.pg_policy AS policy
            CROSS JOIN LATERAL pg_catalog.unnest(policy.polroles)
              AS policy_role(role_oid)
            JOIN pg_catalog.pg_roles AS role_row
              ON role_row.oid = policy_role.role_oid
           WHERE policy.polrelid = '{ADMISSION}'::regclass;
          IF actual_policies IS DISTINCT FROM ARRAY[
            'reviewed_identity_finalizer_admissions_e3_kernel_select|r|true|'
              '{KERNEL_ROLE}|true|false',
            'reviewed_identity_finalizer_admissions_e3_kernel_update|w|true|'
              '{KERNEL_ROLE}|true|true',
            'reviewed_identity_finalizer_admissions_owner_insert|a|true|'
              'home_agent_owner|false|true',
            'reviewed_identity_finalizer_admissions_owner_select|r|true|'
              'home_agent_owner|true|false'
          ]::text[] THEN
            RAISE EXCEPTION 'identity_finalizer_e3_policy_contract_invalid'
              USING ERRCODE = '55000';
          END IF;

          IF (
               SELECT pg_catalog.count(*)
                 FROM pg_catalog.pg_policy AS lock_policy
                WHERE lock_policy.polrelid =
                      'operations.reviewed_identity_migration_runs'::regclass
                  AND lock_policy.polname = '{MIGRATION_RUN_LOCK_POLICY}'
             ) <> 1
             OR NOT EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_policy AS lock_policy
                 JOIN pg_catalog.pg_policy AS select_policy
                   ON select_policy.polrelid = lock_policy.polrelid
                  AND select_policy.polname = '{EVIDENCE_SELECT_POLICY}'
                WHERE lock_policy.polrelid =
                      'operations.reviewed_identity_migration_runs'::regclass
                  AND lock_policy.polname = '{MIGRATION_RUN_LOCK_POLICY}'
                  AND lock_policy.polpermissive
                  AND lock_policy.polcmd = 'w'
                  AND lock_policy.polroles = ARRAY[kernel_oid]::oid[]
                  AND lock_policy.polqual::text =
                      select_policy.polqual::text
                  AND pg_catalog.pg_get_expr(
                        lock_policy.polwithcheck,
                        lock_policy.polrelid,
                        true
                      ) = 'false'
             )
             OR EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_policy AS unexpected_policy
                WHERE unexpected_policy.polrelid =
                      'operations.reviewed_identity_migration_runs'::regclass
                  AND (
                    0 = ANY (unexpected_policy.polroles)
                    OR kernel_oid = ANY (unexpected_policy.polroles)
                  )
                  AND unexpected_policy.polcmd IN ('*', 'w')
                  AND unexpected_policy.polname <>
                      '{MIGRATION_RUN_LOCK_POLICY}'
             ) THEN
            RAISE EXCEPTION 'identity_finalizer_e3_policy_contract_invalid'
              USING ERRCODE = '55000';
          END IF;

          IF NOT EXISTS (
            SELECT 1
              FROM pg_catalog.pg_trigger AS trigger_row
             WHERE trigger_row.tgrelid =
                   'privacy.subject_retrieval_blocks'::regclass
               AND trigger_row.tgname =
                   'subject_retrieval_blocks_identity_write_fence'
               AND NOT trigger_row.tgisinternal
               AND trigger_row.tgenabled = 'O'
               AND trigger_row.tgfoid =
                   '{FENCE_TRIGGER_FUNCTION}'::regprocedure
               AND trigger_row.tgtype = 23
               AND trigger_row.tgnargs = 0
               AND trigger_row.tgargs = ''::bytea
               AND trigger_row.tgqual IS NULL
               AND trigger_row.tgoldtable IS NULL
               AND trigger_row.tgnewtable IS NULL
               AND trigger_row.tgconstraint = 0
               AND trigger_row.tgconstrrelid = 0
               AND trigger_row.tgconstrindid = 0
               AND NOT trigger_row.tgdeferrable
               AND NOT trigger_row.tginitdeferred
               AND trigger_row.tgparentid = 0
               AND pg_catalog.cardinality(
                     trigger_row.tgattr::smallint[]
                   ) = 1
               AND (
                 SELECT attribute.attnum
                   FROM pg_catalog.pg_attribute AS attribute
                  WHERE attribute.attrelid =
                        'privacy.subject_retrieval_blocks'::regclass
                    AND attribute.attname = 'person_id'
                    AND NOT attribute.attisdropped
               ) = ANY(trigger_row.tgattr::smallint[])
          ) OR (
            SELECT pg_catalog.count(*)
              FROM pg_catalog.pg_trigger
             WHERE tgrelid = 'privacy.subject_retrieval_blocks'::regclass
               AND tgname = 'subject_retrieval_blocks_identity_write_fence'
               AND NOT tgisinternal
          ) <> 1 THEN
            RAISE EXCEPTION 'identity_finalizer_e3_trigger_contract_invalid'
              USING ERRCODE = '55000';
          END IF;

          IF EXISTS (
            SELECT 1
              FROM (VALUES
                ('{FUNCTION}'::regprocedure, kernel_oid,
                 '{function_digest}', ARRAY[
                   'search_path=pg_catalog, pg_temp'
                 ]::text[]),
                ('{FENCE_FUNCTION}'::regprocedure, owner_oid,
                 '{fence_lock_digest}', ARRAY[
                   'search_path=pg_catalog, pg_temp'
                 ]::text[]),
                ('{FENCE_TRIGGER_FUNCTION}'::regprocedure, owner_oid,
                 '{fence_trigger_digest}', ARRAY[
                   'search_path=pg_catalog, pg_temp'
                 ]::text[])
              ) AS expected(function_oid, function_owner, body_digest, settings)
              JOIN pg_catalog.pg_proc AS installed
                ON installed.oid = expected.function_oid
             WHERE NOT installed.prosecdef
                OR installed.proowner <> expected.function_owner
                OR installed.provolatile <> 'v'
                OR installed.proleakproof
                OR installed.proconfig IS DISTINCT FROM expected.settings
                OR pg_catalog.encode(
                     pg_catalog.sha256(
                       pg_catalog.convert_to(installed.prosrc, 'UTF8')
                     ),
                     'hex'
                   ) <> expected.body_digest
          ) THEN
            RAISE EXCEPTION 'identity_finalizer_e3_function_contract_invalid'
              USING ERRCODE = '55000';
          END IF;

          IF NOT pg_catalog.has_function_privilege(
               '{FINALIZER_ROLE}', '{FUNCTION}', 'EXECUTE'
             )
             OR EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_proc AS function_row
                 CROSS JOIN LATERAL
                   pg_catalog.aclexplode(function_row.proacl) AS acl
                WHERE function_row.oid = '{FUNCTION}'::regprocedure
                  AND acl.grantee NOT IN (login_oid, kernel_oid)
             )
             OR EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_proc AS function_row
                 CROSS JOIN LATERAL
                   pg_catalog.aclexplode(function_row.proacl) AS acl
                WHERE function_row.oid = '{FUNCTION}'::regprocedure
                  AND acl.grantee = login_oid
                  AND (
                    acl.privilege_type <> 'EXECUTE'
                    OR acl.is_grantable
                  )
             )
             OR pg_catalog.has_schema_privilege(
               '{FINALIZER_ROLE}', 'identity', 'USAGE'
             )
             OR pg_catalog.has_schema_privilege(
               '{FINALIZER_ROLE}', 'privacy', 'USAGE'
             )
             OR NOT pg_catalog.has_schema_privilege(
               '{FINALIZER_ROLE}', 'operations', 'USAGE'
             )
             OR NOT pg_catalog.has_schema_privilege(
               '{KERNEL_ROLE}', 'operations', 'USAGE'
             )
             OR NOT pg_catalog.has_schema_privilege(
               '{KERNEL_ROLE}', 'identity', 'USAGE'
             )
             OR NOT pg_catalog.has_schema_privilege(
               '{KERNEL_ROLE}', 'privacy', 'USAGE'
             )
             OR pg_catalog.has_schema_privilege(
               '{KERNEL_ROLE}', 'operations', 'CREATE'
             )
             OR NOT pg_catalog.has_table_privilege(
               '{KERNEL_ROLE}', 'identity.people', 'SELECT'
             )
             OR NOT pg_catalog.has_table_privilege(
               '{KERNEL_ROLE}', 'identity.people', 'INSERT'
             )
             OR pg_catalog.has_table_privilege(
               '{KERNEL_ROLE}', 'identity.people', 'UPDATE'
             )
             OR pg_catalog.has_table_privilege(
               '{KERNEL_ROLE}',
               'operations.reviewed_identity_migration_runs',
               'UPDATE'
             )
             OR NOT pg_catalog.has_column_privilege(
               '{KERNEL_ROLE}',
               'operations.reviewed_identity_migration_runs',
               'expires_at',
               'UPDATE'
             )
             OR EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_attribute AS attribute
                WHERE attribute.attrelid =
                      'operations.reviewed_identity_migration_runs'::regclass
                  AND attribute.attnum > 0
                  AND NOT attribute.attisdropped
                  AND attribute.attname <> 'expires_at'
                  AND pg_catalog.has_column_privilege(
                        '{KERNEL_ROLE}',
                        attribute.attrelid,
                        attribute.attnum,
                        'UPDATE'
                      )
             )
             OR NOT pg_catalog.has_column_privilege(
               '{KERNEL_ROLE}', 'identity.people', 'status', 'UPDATE'
             )
             OR pg_catalog.has_column_privilege(
               '{KERNEL_ROLE}', 'identity.people', 'display_name', 'UPDATE'
             )
             OR pg_catalog.has_table_privilege(
               '{FINALIZER_ROLE}', 'identity.people', 'SELECT'
             ) THEN
            RAISE EXCEPTION 'identity_finalizer_e3_acl_contract_invalid'
              USING ERRCODE = '42501';
          END IF;
        END
        $e3_installed_contract$
        """
    )


def upgrade() -> None:
    _validate_dormant_role()
    _prepare_admission_table()
    _install_shared_write_fence()
    _install_evidence_policies()
    _install_function_and_acl()
    _assert_installed_contract()


def downgrade() -> None:
    op.execute(
        f"""
        DO $e3_downgrade$
        BEGIN
          IF EXISTS (SELECT 1 FROM {ADMISSION})
             OR EXISTS (
               SELECT 1
                 FROM operations.reviewed_identity_migration_finalizations
             ) THEN
            RAISE EXCEPTION 'refusing to remove used E3 identity finalizer'
              USING ERRCODE = '2BP01';
          END IF;
        END
        $e3_downgrade$
        """
    )
    op.execute(
        f"""
        SET LOCAL ROLE {KERNEL_ROLE};
        REVOKE ALL ON FUNCTION {FUNCTION}
          FROM PUBLIC, home_agent_owner, home_agent_api,
          home_agent_binding_operator, home_agent_ingest, home_agent_worker,
          home_agent_erasure, home_agent_rollout, home_agent_backup,
          home_agent_identity_migration, home_agent_identity_kernel,
          {KERNEL_ROLE}, {FINALIZER_ROLE};
        RESET ROLE;
        REVOKE ALL ON FUNCTION {FENCE_FUNCTION}, {FENCE_TRIGGER_FUNCTION}
          FROM PUBLIC, home_agent_api, home_agent_binding_operator,
          home_agent_ingest, home_agent_worker, home_agent_erasure,
          home_agent_rollout, home_agent_backup,
          home_agent_identity_migration, home_agent_identity_kernel,
          {KERNEL_ROLE}, {FINALIZER_ROLE};
        """
    )
    op.execute(
        "DROP TRIGGER subject_retrieval_blocks_identity_write_fence "
        "ON privacy.subject_retrieval_blocks"
    )
    op.execute(
        f"""
        SET LOCAL ROLE {KERNEL_ROLE};
        DROP FUNCTION operations.finalize_reviewed_identity_migration(
          bytea, uuid
        );
        RESET ROLE;
        """
    )
    op.execute(f"DROP FUNCTION {FENCE_TRIGGER_FUNCTION}")
    op.execute(f"DROP FUNCTION {FENCE_FUNCTION}")
    op.execute(f"DROP TABLE {FENCE_TABLE}")
    for table in EVIDENCE_TABLES:
        for policy in (
            EVIDENCE_SELECT_POLICY,
            EVIDENCE_INSERT_POLICY,
            MIGRATION_RUN_LOCK_POLICY,
        ):
            op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
    op.execute(
        f"""
        REVOKE SELECT, INSERT ON TABLE identity.people FROM {KERNEL_ROLE};
        REVOKE UPDATE (
          status, status_source_ref, status_source_version,
          status_source_sha256, updated_at
        ) ON TABLE identity.people FROM {KERNEL_ROLE};
        REVOKE UPDATE (expires_at) ON TABLE
          operations.reviewed_identity_migration_runs
          FROM {KERNEL_ROLE};
        REVOKE ALL PRIVILEGES ON TABLE
          identity.aliases,
          identity.external_recognition_bindings,
          identity.privacy_directives,
          identity.legacy_role_labels,
          identity.legacy_relationship_candidates,
          privacy.auto_expiry_schedules,
          operations.outbox,
          operations.reviewed_identity_migration_runs,
          operations.reviewed_identity_migration_source_items,
          operations.reviewed_identity_migration_decisions,
          operations.reviewed_identity_migration_item_receipts,
          operations.reviewed_identity_migration_finalizations,
          operations.reviewed_identity_migration_projection_lineage,
          operations.reviewed_identity_migration_projection_subjects,
          operations.reviewed_identity_migration_erasure_impacts,
          operations.legacy_identity_writer_evidence,
          operations.privacy_cutover_check_receipts,
          operations.semantic_authority_cutovers,
          {ADMISSION}
          FROM {KERNEL_ROLE}, {FINALIZER_ROLE};
        REVOKE EXECUTE ON FUNCTION
          privacy.identity_person_is_blocked(uuid)
          FROM {KERNEL_ROLE};
        REVOKE CREATE ON SCHEMA operations FROM {KERNEL_ROLE};
        REVOKE USAGE ON SCHEMA operations, identity, privacy
          FROM {KERNEL_ROLE}, {FINALIZER_ROLE};
        """
    )
    op.drop_table("reviewed_identity_finalizer_admissions", schema="operations")
