"""Add the dormant E5 current identity-authority verifier.

Revision ID: 0015_current_authority_e5a
Revises: 0014_identity_cutover_e4
Create Date: 2026-07-27

E5 is database-only groundwork. Production remains pinned to revision 0006a
and ``record_only``. It adds no API, BFF, UI, Compose command, activation
helper, principal binding, parent fact, memory, place, initiative, or action.

The verifier returns content-free categorical state. Its positive result means
only that the historical E4 promotion remains current with respect to the
database erasure overlay. It is not a fresh check of the encrypted external
ledger, Home Assistant identity, current privacy directives, or the external
legacy writer. It structurally rechecks the stored E4 graph and exact verifier
bundle marker; it does not reverify the private E4 document or its signature.
A future principal-binding kernel must call this function and write its binding
in the same SERIALIZABLE transaction.
"""

from __future__ import annotations

import hashlib
from typing import Sequence

from alembic import op


revision: str = "0015_current_authority_e5a"
down_revision: str | None = "0014_identity_cutover_e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


CALLER_ROLE = "home_agent_binding_operator"
INTERNAL_CALLER_ROLE = "home_agent_binding_committer"
KERNEL_ROLE = "home_agent_identity_authority_kernel"
FUNCTION = (
    "operations.evaluate_current_identity_semantic_authority(uuid)"
)
SELECT_POLICY = "identity_authority_e5_select"
RUN_LOCK_POLICY = "identity_authority_e5_run_lock"
IMPACT_ADVISORY_NAMESPACE = 72102026
E4_VERIFIER_BUNDLE_DIGEST = (
    "eae0fd391a19a50a89fab9ee7665760499d7f154bfaf518abe56b8eed2090aa0"
)
IMPACT_GUARD_BODY_SHA256 = (
    "627bb84f83baa6183144de5d94ddcc4f0da56dc02e64c544b4b679b5ed3c316b"
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

PROMOTION = "operations.semantic_authority_promotions"
ADMISSION = "operations.reviewed_identity_cutover_admissions"
FREEZE = "operations.enforced_legacy_identity_writer_freezes"
RUN = "operations.reviewed_identity_migration_runs"
FINALIZATION = "operations.reviewed_identity_migration_finalizations"
FINALIZER_ADMISSION = "operations.reviewed_identity_finalizer_admissions"
CANDIDATE = "operations.semantic_authority_cutovers"
WRITER_EVIDENCE = "operations.legacy_identity_writer_evidence"
PRIVACY_RECEIPT = "operations.privacy_cutover_check_receipts"
IMPACT = "operations.reviewed_identity_migration_erasure_impacts"
LINEAGE = "operations.reviewed_identity_migration_projection_lineage"
SUBJECT = "operations.reviewed_identity_migration_projection_subjects"
LEDGER_STATE = "operations.erasure_ledger_state"
REPLAY_RECEIPT = "operations.erasure_replay_receipts"

RLS_READ_TABLES = (
    PROMOTION,
    ADMISSION,
    FREEZE,
    RUN,
    FINALIZATION,
    FINALIZER_ADMISSION,
    CANDIDATE,
    WRITER_EVIDENCE,
    PRIVACY_RECEIPT,
    IMPACT,
    LINEAGE,
    SUBJECT,
)
DIRECT_READ_TABLES = (LEDGER_STATE, REPLAY_RECEIPT)

RESULTS = (
    "current_database_authority",
    "promotion_missing",
    "promotion_invalid",
    "ledger_state_missing",
    "ledger_regressed",
    "ledger_diverged",
    "ledger_continuity_invalid",
    "erasure_impact_present",
    "subject_retrieval_blocked",
)


FUNCTION_BODY = r"""
DECLARE
  e5_promotion operations.semantic_authority_promotions%ROWTYPE;
  e5_admission operations.reviewed_identity_cutover_admissions%ROWTYPE;
  e5_run operations.reviewed_identity_migration_runs%ROWTYPE;
  e5_finalization
    operations.reviewed_identity_migration_finalizations%ROWTYPE;
  e5_finalizer_admission
    operations.reviewed_identity_finalizer_admissions%ROWTYPE;
  e5_candidate operations.semantic_authority_cutovers%ROWTYPE;
  e5_freeze
    operations.enforced_legacy_identity_writer_freezes%ROWTYPE;
  e5_writer_evidence operations.legacy_identity_writer_evidence%ROWTYPE;
  e5_ledger_state operations.erasure_ledger_state%ROWTYPE;
  e5_anchor_receipt_hash text;
  e5_current_receipt_hash text;
  e5_privacy_count bigint;
  e5_privacy_category_count bigint;
  e5_later_receipt_count bigint;
  e5_blocked_subject_count bigint;
  e5_affected_rows integer;
BEGIN
  IF session_user NOT IN (
       'home_agent_binding_operator',
       'home_agent_binding_committer'
     )
     OR current_user <> 'home_agent_identity_authority_kernel'
     OR pg_catalog.pg_has_role(
          session_user, 'home_agent_identity_authority_kernel', 'SET'
        ) THEN
    RAISE EXCEPTION 'identity_authority_status_role_invalid'
      USING ERRCODE = '42501';
  END IF;
  IF pg_catalog.current_setting('transaction_isolation') <> 'serializable'
     OR pg_catalog.current_setting('transaction_read_only') <> 'off'
     OR pg_catalog.pg_is_in_recovery() THEN
    RAISE EXCEPTION 'identity_authority_status_transaction_invalid'
      USING ERRCODE = '25000';
  END IF;
  IF target_promotion_id IS NULL
     OR pg_catalog.substring(target_promotion_id::text, 15, 1) <> '7'
     OR pg_catalog.substring(target_promotion_id::text, 20, 1)
        NOT IN ('8','9','a','b') THEN
    RAISE EXCEPTION 'identity_authority_status_input_invalid'
      USING ERRCODE = '22023';
  END IF;

  -- These transaction-local limits remain active after this function returns,
  -- bounding every lock through the future same-transaction binding commit.
  -- They defend against an accidentally stranded pooled connection; the
  -- login role carries the same reviewed defaults as an independent layer.
  PERFORM pg_catalog.set_config('statement_timeout', '15s', true);
  PERFORM pg_catalog.set_config('lock_timeout', '5s', true);
  PERFORM pg_catalog.set_config(
    'idle_in_transaction_session_timeout', '15s', true
  );
  PERFORM pg_catalog.set_config('transaction_timeout', '30s', true);

  -- First application-data MVCC operation. It orders this evaluation after
  -- E2 subject-block insertion and against a future binding write.
  PERFORM privacy.lock_identity_semantic_write_fence();

  -- The reviewed historical graph is append-only through its normal protocol
  -- and this kernel is deliberately SELECT-only on those relations.  Do not
  -- add tuple-locking clauses to those reads: PostgreSQL requires UPDATE
  -- authority for row locks, which would widen this kernel's capability.
  -- The mutable current ledger head is the sole exception and receives only
  -- UPDATE(updated_at), below. The SERIALIZABLE snapshot plus the semantic
  -- fence and exact impact advisory/run-row protocol provide the rest of the
  -- required ordering.
  SELECT stored_promotion.*
    INTO e5_promotion
    FROM operations.semantic_authority_promotions AS stored_promotion
   WHERE stored_promotion.promotion_id = target_promotion_id
     AND stored_promotion.authority_scope = 'identity_semantics';
  IF NOT FOUND THEN
    RETURN 'promotion_missing';
  END IF;

  -- Mirror the installed erasure-impact insertion guard. Its trigger takes
  -- this exact advisory lock and no-op updates this exact run row. Under
  -- SERIALIZABLE isolation a race therefore orders before us or raises 40001.
  PERFORM pg_catalog.pg_advisory_xact_lock(
    72102026,
    pg_catalog.hashtext(e5_promotion.run_id::text)
  );
  UPDATE operations.reviewed_identity_migration_runs AS locked_run
     SET expires_at = locked_run.expires_at
   WHERE locked_run.run_id = e5_promotion.run_id;
  GET DIAGNOSTICS e5_affected_rows = ROW_COUNT;
  IF e5_affected_rows <> 1 THEN
    RETURN 'promotion_invalid';
  END IF;

  IF e5_promotion.contract_version
       <> 'reviewed-identity-semantic-cutover-v1'
     OR e5_promotion.authority_status
        <> 'historical_authoritative_commit'
     OR NOT e5_promotion.authoritative_at_commit
     OR e5_promotion.erasure_ledger_verification <> 'head_matched'
     OR e5_promotion.signature_algorithm <> 'ed25519'
     OR e5_promotion.semantic_generation <= 0
     OR e5_promotion.e3_commitment_key_epoch <= 0
     OR e5_promotion.erasure_ledger_epoch < 0
     OR e5_promotion.database_transaction_id <= 0
     OR NOT pg_catalog.isfinite(e5_promotion.committed_at) THEN
    RETURN 'promotion_invalid';
  END IF;

  SELECT stored_admission.*
    INTO e5_admission
    FROM operations.reviewed_identity_cutover_admissions
         AS stored_admission
   WHERE stored_admission.admission_id = e5_promotion.admission_id;
  IF NOT FOUND
     OR e5_admission.promotion_id
        IS DISTINCT FROM e5_promotion.promotion_id
     OR e5_admission.run_id IS DISTINCT FROM e5_promotion.run_id
     OR e5_admission.finalization_id
        IS DISTINCT FROM e5_promotion.finalization_id
     OR e5_admission.candidate_cutover_id
        IS DISTINCT FROM e5_promotion.candidate_cutover_id
     OR e5_admission.writer_freeze_id
        IS DISTINCT FROM e5_promotion.writer_freeze_id
     OR e5_admission.contract_version
        <> 'reviewed-identity-cutover-admission-v1'
     OR e5_admission.verification_status <> 'offline_verified'
     OR e5_admission.verifier_bundle_digest
        <> 'eae0fd391a19a50a89fab9ee7665760499d7f154bfaf518abe56b8eed2090aa0'
     OR e5_admission.consumed_at IS NULL
     OR e5_admission.consumed_at
        IS DISTINCT FROM e5_promotion.committed_at
     OR e5_admission.promotion_commitment
        IS DISTINCT FROM e5_promotion.promotion_commitment
     OR e5_admission.privacy_check_set_commitment
        IS DISTINCT FROM e5_promotion.privacy_check_set_commitment
     OR e5_admission.freeze_commitment
        IS DISTINCT FROM e5_promotion.freeze_commitment
     OR e5_admission.source_installation_id
        IS DISTINCT FROM e5_promotion.source_installation_id
     OR e5_admission.semantic_generation
        IS DISTINCT FROM e5_promotion.semantic_generation
     OR e5_admission.e3_source_manifest_commitment
        IS DISTINCT FROM e5_promotion.e3_source_manifest_commitment
     OR e5_admission.e3_projection_manifest_commitment
        IS DISTINCT FROM e5_promotion.e3_projection_manifest_commitment
     OR e5_admission.e3_commitment_key_epoch
        IS DISTINCT FROM e5_promotion.e3_commitment_key_epoch
     OR e5_admission.erasure_ledger_epoch
        IS DISTINCT FROM e5_promotion.erasure_ledger_epoch
     OR e5_admission.erasure_ledger_head_hash
        IS DISTINCT FROM e5_promotion.erasure_ledger_head_hash
     OR e5_admission.erasure_ledger_verification
        IS DISTINCT FROM e5_promotion.erasure_ledger_verification
     OR e5_admission.release_manifest_digest
        IS DISTINCT FROM e5_promotion.release_manifest_digest
     OR e5_admission.core_schema_digest
        IS DISTINCT FROM e5_promotion.core_schema_digest
     OR e5_admission.core_capability_digest
        IS DISTINCT FROM e5_promotion.core_capability_digest
     OR e5_admission.policy_digest
        IS DISTINCT FROM e5_promotion.policy_digest
     OR e5_admission.signing_key_fingerprint
        IS DISTINCT FROM e5_promotion.signing_key_fingerprint THEN
    RETURN 'promotion_invalid';
  END IF;

  SELECT stored_run.*
    INTO e5_run
    FROM operations.reviewed_identity_migration_runs AS stored_run
   WHERE stored_run.run_id = e5_promotion.run_id;
  IF NOT FOUND
     OR e5_run.contract_version <> 'reviewed-identity-migration-run-v1'
     OR e5_run.logical_source_manifest_commitment
        IS DISTINCT FROM e5_promotion.e3_source_manifest_commitment
     OR e5_run.projection_manifest_commitment
        IS DISTINCT FROM e5_promotion.e3_projection_manifest_commitment
     OR e5_run.commitment_key_epoch
        IS DISTINCT FROM e5_promotion.e3_commitment_key_epoch
     OR e5_run.release_manifest_digest
        IS DISTINCT FROM e5_promotion.release_manifest_digest
     OR e5_run.core_schema_digest
        IS DISTINCT FROM e5_promotion.core_schema_digest
     OR e5_run.core_capability_digest
        IS DISTINCT FROM e5_promotion.core_capability_digest
     OR e5_run.policy_digest IS DISTINCT FROM e5_promotion.policy_digest
     OR e5_run.created_at > e5_admission.admitted_at THEN
    RETURN 'promotion_invalid';
  END IF;

  SELECT stored_finalization.*
    INTO e5_finalization
    FROM operations.reviewed_identity_migration_finalizations
         AS stored_finalization
   WHERE stored_finalization.run_id = e5_promotion.run_id
     AND stored_finalization.finalization_id =
         e5_promotion.finalization_id;
  IF NOT FOUND
     OR e5_finalization.contract_version
        <> 'reviewed-identity-migration-finalization-v1'
     OR e5_finalization.verification_status <> 'candidate_unverified'
     OR e5_finalization.authoritative
     OR e5_finalization.source_manifest_commitment
        IS DISTINCT FROM e5_promotion.e3_source_manifest_commitment
     OR e5_finalization.projection_manifest_commitment
        IS DISTINCT FROM e5_promotion.e3_projection_manifest_commitment
     OR e5_finalization.finalized_at > e5_admission.admitted_at THEN
    RETURN 'promotion_invalid';
  END IF;

  SELECT stored_finalizer_admission.*
    INTO e5_finalizer_admission
    FROM operations.reviewed_identity_finalizer_admissions
         AS stored_finalizer_admission
   WHERE stored_finalizer_admission.run_id = e5_promotion.run_id
     AND stored_finalizer_admission.finalization_id =
         e5_promotion.finalization_id;
  IF NOT FOUND
     OR e5_finalizer_admission.consumed_at IS NULL
     OR e5_finalizer_admission.consumed_at
        IS DISTINCT FROM e5_finalization.finalized_at
     OR e5_finalizer_admission.finalization_commitment
        IS DISTINCT FROM e5_finalization.finalization_commitment THEN
    RETURN 'promotion_invalid';
  END IF;

  SELECT stored_candidate.*
    INTO e5_candidate
    FROM operations.semantic_authority_cutovers AS stored_candidate
   WHERE stored_candidate.cutover_id = e5_promotion.candidate_cutover_id
     AND stored_candidate.run_id = e5_promotion.run_id
     AND stored_candidate.finalization_id =
         e5_promotion.finalization_id;
  IF NOT FOUND
     OR e5_candidate.contract_version
        <> 'semantic-authority-cutover-candidate-v1'
     OR e5_candidate.authority_status <> 'candidate_unenforced'
     OR e5_candidate.authoritative
     OR e5_candidate.required_privacy_check_result <> 'passed'
     OR e5_candidate.required_privacy_residual_code <> 'none'
     OR e5_candidate.privacy_check_set_commitment
        IS DISTINCT FROM e5_promotion.privacy_check_set_commitment
     OR e5_candidate.policy_digest
        IS DISTINCT FROM e5_promotion.policy_digest
     OR e5_candidate.attested_at > e5_admission.admitted_at THEN
    RETURN 'promotion_invalid';
  END IF;

  SELECT stored_freeze.*
    INTO e5_freeze
    FROM operations.enforced_legacy_identity_writer_freezes AS stored_freeze
   WHERE stored_freeze.freeze_id = e5_promotion.writer_freeze_id
     AND stored_freeze.run_id = e5_promotion.run_id;
  IF NOT FOUND
     OR e5_freeze.writer_evidence_id
        IS DISTINCT FROM e5_candidate.writer_evidence_id
     OR e5_freeze.contract_version
        <> 'enforced-legacy-identity-writer-freeze-v1'
     OR e5_freeze.enforcement_status <> 'enforced_offline'
     OR e5_freeze.write_probe_result <> 'blocked'
     OR e5_freeze.semantic_write_status <> 'frozen'
     OR e5_freeze.legacy_context_cutoff_status <> 'enforced_cutoff'
     OR e5_freeze.recognition_mode <> 'nonauthoritative_only'
     OR e5_freeze.freeze_commitment
        IS DISTINCT FROM e5_promotion.freeze_commitment
     OR e5_freeze.source_installation_id
        IS DISTINCT FROM e5_promotion.source_installation_id
     OR e5_freeze.semantic_generation
        IS DISTINCT FROM e5_promotion.semantic_generation
     OR e5_freeze.e3_source_manifest_commitment
        IS DISTINCT FROM e5_promotion.e3_source_manifest_commitment
     OR e5_freeze.e3_projection_manifest_commitment
        IS DISTINCT FROM e5_promotion.e3_projection_manifest_commitment
     OR e5_freeze.e3_commitment_key_epoch
        IS DISTINCT FROM e5_promotion.e3_commitment_key_epoch
     OR e5_freeze.source_projection_commitment
        IS DISTINCT FROM e5_promotion.e3_projection_manifest_commitment
     OR e5_freeze.policy_digest
        IS DISTINCT FROM e5_promotion.policy_digest
     OR e5_freeze.release_manifest_digest
        IS DISTINCT FROM e5_promotion.release_manifest_digest
     OR e5_freeze.enforced_at < e5_finalization.finalized_at
     OR e5_freeze.verified_at < e5_finalization.finalized_at
     OR e5_freeze.recorded_at > e5_admission.admitted_at
     OR e5_candidate.attested_at < e5_freeze.verified_at THEN
    RETURN 'promotion_invalid';
  END IF;

  SELECT stored_writer_evidence.*
    INTO e5_writer_evidence
    FROM operations.legacy_identity_writer_evidence
         AS stored_writer_evidence
   WHERE stored_writer_evidence.run_id = e5_freeze.run_id
     AND stored_writer_evidence.evidence_id = e5_freeze.writer_evidence_id;
  IF NOT FOUND
     OR e5_writer_evidence.source_installation_id
        IS DISTINCT FROM e5_freeze.source_installation_id
     OR e5_writer_evidence.semantic_generation
        IS DISTINCT FROM e5_freeze.semantic_generation
     OR e5_writer_evidence.source_projection_commitment
        IS DISTINCT FROM e5_freeze.source_projection_commitment
     OR e5_writer_evidence.evidence_commitment
        IS DISTINCT FROM e5_freeze.writer_evidence_commitment
     OR e5_writer_evidence.integrity_result <> 'passed'
     OR e5_writer_evidence.checkpoint_result <> 'complete'
     OR e5_writer_evidence.journal_result <> 'clean'
     OR e5_writer_evidence.legacy_context_cutoff_status = 'not_observed'
     OR e5_writer_evidence.observed_at > e5_freeze.enforced_at THEN
    RETURN 'promotion_invalid';
  END IF;

  SELECT pg_catalog.count(*),
         pg_catalog.count(DISTINCT stored_privacy_receipt.check_category)
    INTO e5_privacy_count, e5_privacy_category_count
    FROM operations.privacy_cutover_check_receipts
         AS stored_privacy_receipt
   WHERE stored_privacy_receipt.run_id = e5_promotion.run_id
     AND stored_privacy_receipt.finalization_id =
         e5_promotion.finalization_id;
  IF e5_privacy_count <> 6 OR e5_privacy_category_count <> 6 THEN
    RETURN 'promotion_invalid';
  END IF;
  IF EXISTS (
    SELECT 1
      FROM (
        VALUES
          ('ingress'::text, e5_candidate.ingress_check_id),
          ('retrieval'::text, e5_candidate.retrieval_check_id),
          ('prompt'::text, e5_candidate.prompt_check_id),
          ('initiative'::text, e5_candidate.initiative_check_id),
          ('export'::text, e5_candidate.export_check_id),
          ('edge_block'::text, e5_candidate.edge_block_check_id)
      ) AS required_check(check_category, check_id)
      LEFT JOIN operations.privacy_cutover_check_receipts
           AS stored_privacy_receipt
        ON stored_privacy_receipt.run_id = e5_promotion.run_id
       AND stored_privacy_receipt.finalization_id =
           e5_promotion.finalization_id
       AND stored_privacy_receipt.check_id = required_check.check_id
       AND stored_privacy_receipt.check_category =
           required_check.check_category
       AND stored_privacy_receipt.check_result = 'passed'
       AND stored_privacy_receipt.residual_code = 'none'
       AND stored_privacy_receipt.policy_digest =
           e5_promotion.policy_digest
       AND stored_privacy_receipt.checked_at >= e5_freeze.verified_at
       AND stored_privacy_receipt.checked_at <= e5_admission.admitted_at
     WHERE stored_privacy_receipt.check_id IS NULL
  ) THEN
    RETURN 'promotion_invalid';
  END IF;

  SELECT current_ledger.*
    INTO e5_ledger_state
    FROM operations.erasure_ledger_state AS current_ledger
   WHERE current_ledger.state_key = 'current'
   FOR SHARE OF current_ledger;
  IF NOT FOUND THEN
    RETURN 'ledger_state_missing';
  END IF;
  IF e5_ledger_state.recorded_epoch < e5_promotion.erasure_ledger_epoch THEN
    RETURN 'ledger_regressed';
  END IF;
  IF e5_ledger_state.recorded_epoch = e5_promotion.erasure_ledger_epoch THEN
    IF e5_ledger_state.recorded_head_hash
         IS DISTINCT FROM e5_promotion.erasure_ledger_head_hash THEN
      RETURN 'ledger_diverged';
    END IF;
  ELSE
    IF e5_ledger_state.recorded_head_hash
         IS NOT DISTINCT FROM e5_promotion.erasure_ledger_head_hash THEN
      RETURN 'ledger_continuity_invalid';
    END IF;
    IF e5_promotion.erasure_ledger_epoch = 0 THEN
      IF e5_promotion.erasure_ledger_head_hash <> repeat('0', 64) THEN
        RETURN 'ledger_continuity_invalid';
      END IF;
    ELSE
      SELECT starting_receipt.record_hash
        INTO e5_anchor_receipt_hash
        FROM operations.erasure_replay_receipts AS starting_receipt
       WHERE starting_receipt.ledger_epoch =
             e5_promotion.erasure_ledger_epoch;
      IF NOT FOUND
         OR e5_anchor_receipt_hash
            IS DISTINCT FROM e5_promotion.erasure_ledger_head_hash THEN
        RETURN 'ledger_continuity_invalid';
      END IF;
    END IF;

    SELECT pg_catalog.count(*)
      INTO e5_later_receipt_count
      FROM operations.erasure_replay_receipts AS later_receipt
     WHERE later_receipt.ledger_epoch >
           e5_promotion.erasure_ledger_epoch
       AND later_receipt.ledger_epoch <= e5_ledger_state.recorded_epoch;
    IF e5_later_receipt_count
         IS DISTINCT FROM (
           e5_ledger_state.recorded_epoch
           - e5_promotion.erasure_ledger_epoch
         ) THEN
      RETURN 'ledger_continuity_invalid';
    END IF;

    SELECT final_receipt.record_hash
      INTO e5_current_receipt_hash
      FROM operations.erasure_replay_receipts AS final_receipt
     WHERE final_receipt.ledger_epoch = e5_ledger_state.recorded_epoch;
    IF NOT FOUND
       OR e5_current_receipt_hash
          IS DISTINCT FROM e5_ledger_state.recorded_head_hash THEN
      RETURN 'ledger_continuity_invalid';
    END IF;
  END IF;

  IF EXISTS (
       SELECT 1
         FROM operations.reviewed_identity_migration_erasure_impacts
              AS erasure_impact
        WHERE erasure_impact.run_id = e5_promotion.run_id
     ) THEN
    RETURN 'erasure_impact_present';
  END IF;

  SELECT pg_catalog.count(*)
    INTO e5_blocked_subject_count
    FROM operations.reviewed_identity_migration_projection_subjects
         AS projection_subject
    JOIN operations.reviewed_identity_migration_projection_lineage
         AS projection_lineage USING (lineage_id)
   WHERE projection_lineage.run_id = e5_promotion.run_id
     AND privacy.identity_person_is_blocked(projection_subject.person_id);
  IF e5_blocked_subject_count <> 0 THEN
    RETURN 'subject_retrieval_blocked';
  END IF;

  RETURN 'current_database_authority';
END;
"""

FUNCTION_BODY_SHA256 = hashlib.sha256(FUNCTION_BODY.encode("utf-8")).hexdigest()


def _policy_predicate() -> str:
    return (
        f"session_user IN ('{CALLER_ROLE}','{INTERNAL_CALLER_ROLE}') "
        f"AND current_user = '{KERNEL_ROLE}' "
        "AND NOT pg_catalog.pg_has_role("
        f"session_user, '{KERNEL_ROLE}', 'SET')"
    )


def _validate_preconditions() -> None:
    rls_tables = ", ".join(f"'{table}'::regclass" for table in RLS_READ_TABLES)
    policy_tables = ", ".join(f"'{table}'::regclass" for table in RLS_READ_TABLES)
    op.execute(
        f"""
        DO $e5_preconditions$
        DECLARE
          owner_oid oid;
          caller_oid oid;
          kernel_oid oid;
          migration_kernel_oid oid;
          erasure_kernel_oid oid;
          impact_guard_oid oid;
          fence_lock_oid oid;
          fence_trigger_oid oid;
          person_block_oid oid;
        BEGIN
          SELECT oid INTO STRICT owner_oid
            FROM pg_catalog.pg_roles
           WHERE rolname = 'home_agent_owner';
          SELECT oid INTO STRICT caller_oid
            FROM pg_catalog.pg_roles
           WHERE rolname = '{CALLER_ROLE}';
          SELECT oid INTO STRICT kernel_oid
            FROM pg_catalog.pg_roles
           WHERE rolname = '{KERNEL_ROLE}';
          SELECT oid INTO STRICT migration_kernel_oid
            FROM pg_catalog.pg_roles
           WHERE rolname = 'home_agent_identity_kernel';
          SELECT oid INTO STRICT erasure_kernel_oid
            FROM pg_catalog.pg_roles
           WHERE rolname = 'home_agent_identity_erasure_kernel';
          impact_guard_oid := pg_catalog.to_regprocedure(
            'operations.bump_reviewed_identity_migration_replay_guard()'
          );
          fence_lock_oid := pg_catalog.to_regprocedure(
            'privacy.lock_identity_semantic_write_fence()'
          );
          fence_trigger_oid := pg_catalog.to_regprocedure(
            'privacy.fence_identity_tombstone_write()'
          );
          person_block_oid := pg_catalog.to_regprocedure(
            'privacy.identity_person_is_blocked(uuid)'
          );

          IF (
               SELECT version_num FROM public.alembic_version
             ) IS DISTINCT FROM '0014_identity_cutover_e4' THEN
            RAISE EXCEPTION 'identity_authority_e5_revision_invalid'
              USING ERRCODE = '55000';
          END IF;
          IF pg_catalog.to_regprocedure('{FUNCTION}') IS NOT NULL THEN
            RAISE EXCEPTION 'identity_authority_e5_object_occupied'
              USING ERRCODE = '55000';
          END IF;
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
               AND caller_role.rolconfig @> ARRAY[
                 'statement_timeout=15s',
                 'lock_timeout=5s',
                 'idle_in_transaction_session_timeout=15s',
                 'transaction_timeout=30s'
               ]::text[]
          ) OR NOT EXISTS (
            SELECT 1
              FROM pg_catalog.pg_roles AS kernel_role
             WHERE kernel_role.oid = kernel_oid
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
            RAISE EXCEPTION 'identity_authority_e5_role_invalid'
              USING ERRCODE = '42501';
          END IF;
          IF (
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
                   OR roleid = caller_oid
                   OR member = caller_oid
             )
             OR EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_db_role_setting
                WHERE setrole = caller_oid
                  AND setdatabase <> 0
             ) THEN
            RAISE EXCEPTION 'identity_authority_e5_membership_invalid'
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
            RAISE EXCEPTION 'identity_authority_e5_pregrant_invalid'
              USING ERRCODE = '42501';
          END IF;
          IF (
               SELECT pg_catalog.count(*)
                 FROM pg_catalog.pg_class AS table_row
                WHERE table_row.oid IN ({rls_tables})
                  AND table_row.relowner = owner_oid
                  AND table_row.relkind = 'r'
                  AND table_row.relpersistence = 'p'
                  AND table_row.relrowsecurity
                  AND table_row.relforcerowsecurity
             ) <> {len(RLS_READ_TABLES)}
             OR (
               SELECT pg_catalog.count(*)
                 FROM pg_catalog.pg_class AS table_row
                WHERE table_row.oid IN (
                  '{LEDGER_STATE}'::regclass,
                  '{REPLAY_RECEIPT}'::regclass
                )
                  AND table_row.relowner = owner_oid
                  AND table_row.relkind = 'r'
                  AND table_row.relpersistence = 'p'
             ) <> 2 THEN
            RAISE EXCEPTION 'identity_authority_e5_evidence_invalid'
              USING ERRCODE = '55000';
          END IF;
          IF EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_policy AS policy_row
                WHERE policy_row.polrelid IN ({policy_tables})
                  AND policy_row.polname IN (
                    '{SELECT_POLICY}', '{RUN_LOCK_POLICY}'
                  )
             ) THEN
            RAISE EXCEPTION 'identity_authority_e5_policy_occupied'
              USING ERRCODE = '55000';
          END IF;
          IF impact_guard_oid IS NULL
             OR fence_lock_oid IS NULL
             OR fence_trigger_oid IS NULL
             OR person_block_oid IS NULL
             OR NOT EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_trigger AS trigger_row
                WHERE trigger_row.tgrelid = '{IMPACT}'::regclass
                  AND trigger_row.tgname =
                      'reviewed_identity_migration_erasure_replay_guard'
                  AND trigger_row.tgfoid = impact_guard_oid
                  AND trigger_row.tgenabled = 'O'
                  AND NOT trigger_row.tgisinternal
                  AND trigger_row.tgtype = 7
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
             )
             OR (
               SELECT pg_catalog.count(*)
                 FROM pg_catalog.pg_trigger AS trigger_row
                WHERE trigger_row.tgrelid = '{IMPACT}'::regclass
                  AND trigger_row.tgname =
                      'reviewed_identity_migration_erasure_replay_guard'
                  AND NOT trigger_row.tgisinternal
             ) <> 1
             OR NOT EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_trigger AS trigger_row
                WHERE trigger_row.tgrelid =
                      'privacy.subject_retrieval_blocks'::regclass
                  AND trigger_row.tgname =
                      'subject_retrieval_blocks_identity_write_fence'
                  AND trigger_row.tgfoid = fence_trigger_oid
                  AND trigger_row.tgenabled = 'O'
                  AND NOT trigger_row.tgisinternal
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
             )
             OR (
               SELECT pg_catalog.count(*)
                 FROM pg_catalog.pg_trigger AS trigger_row
                WHERE trigger_row.tgrelid =
                      'privacy.subject_retrieval_blocks'::regclass
                  AND trigger_row.tgname =
                      'subject_retrieval_blocks_identity_write_fence'
                  AND NOT trigger_row.tgisinternal
             ) <> 1
             OR (
               SELECT pg_catalog.count(*)
                 FROM (
                   VALUES
                     (
                       impact_guard_oid,
                       migration_kernel_oid,
                       'plpgsql'::name,
                       'v'::"char",
                       ARRAY[
                         'search_path=pg_catalog, pg_temp'
                       ]::text[],
                       '{IMPACT_GUARD_BODY_SHA256}'::text
                     ),
                     (
                       fence_lock_oid,
                       owner_oid,
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
                       'sql'::name,
                       's'::"char",
                       ARRAY['search_path=pg_catalog']::text[],
                       '{PERSON_BLOCK_BODY_SHA256}'::text
                     )
                 ) AS expected(
                   function_oid, function_owner, language_name,
                   volatility, settings, body_digest
                 )
                 JOIN pg_catalog.pg_proc AS installed
                   ON installed.oid = expected.function_oid
                 JOIN pg_catalog.pg_language AS function_language
                   ON function_language.oid = installed.prolang
                WHERE installed.proowner = expected.function_owner
                  AND installed.prosecdef
                  AND installed.prokind = 'f'
                  AND installed.provolatile = expected.volatility
                  AND NOT installed.proleakproof
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
            RAISE EXCEPTION 'identity_authority_e5_ordering_contract_invalid'
              USING ERRCODE = '55000';
          END IF;
        END
        $e5_preconditions$;
        """
    )


def _install_policies_and_grants() -> None:
    predicate = _policy_predicate()
    for table in RLS_READ_TABLES:
        op.execute(
            f"CREATE POLICY {SELECT_POLICY} ON {table} FOR SELECT "
            f"TO {KERNEL_ROLE} USING ({predicate})"
        )
    op.execute(
        f"""
        CREATE POLICY {RUN_LOCK_POLICY}
          ON {RUN} FOR UPDATE TO {KERNEL_ROLE}
          USING ({predicate})
          WITH CHECK ({predicate});

        GRANT USAGE ON SCHEMA operations TO {CALLER_ROLE}, {KERNEL_ROLE};
        GRANT USAGE ON SCHEMA privacy TO {KERNEL_ROLE};
        GRANT SELECT ON TABLE
          {PROMOTION},
          {ADMISSION},
          {FREEZE},
          {RUN},
          {FINALIZATION},
          {FINALIZER_ADMISSION},
          {CANDIDATE},
          {WRITER_EVIDENCE},
          {PRIVACY_RECEIPT},
          {IMPACT},
          {LINEAGE},
          {SUBJECT},
          {LEDGER_STATE},
          {REPLAY_RECEIPT}
          TO {KERNEL_ROLE};
        GRANT UPDATE (expires_at) ON TABLE {RUN} TO {KERNEL_ROLE};
        GRANT UPDATE (updated_at) ON TABLE {LEDGER_STATE}
          TO {KERNEL_ROLE};
        GRANT EXECUTE ON FUNCTION
          privacy.lock_identity_semantic_write_fence(),
          privacy.identity_person_is_blocked(uuid)
          TO {KERNEL_ROLE};
        """
    )


def _install_function() -> None:
    op.execute(
        f"""
        GRANT CREATE ON SCHEMA operations TO {KERNEL_ROLE};
        SET LOCAL ROLE {KERNEL_ROLE};
        CREATE FUNCTION operations.evaluate_current_identity_semantic_authority(
          target_promotion_id uuid
        ) RETURNS varchar(32)
        LANGUAGE plpgsql
        VOLATILE
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $e5${FUNCTION_BODY}$e5$;
        REVOKE ALL ON FUNCTION {FUNCTION}
          FROM PUBLIC, home_agent_owner, home_agent_api,
          home_agent_binding_operator, home_agent_binding_committer,
          home_agent_ingest, home_agent_worker,
          home_agent_erasure, home_agent_rollout, home_agent_backup,
          home_agent_identity_migration, home_agent_identity_kernel,
          home_agent_identity_finalizer,
          home_agent_identity_finalizer_kernel,
          home_agent_identity_erasure_kernel,
          home_agent_identity_cutover,
          home_agent_identity_cutover_kernel;
        GRANT EXECUTE ON FUNCTION {FUNCTION} TO {CALLER_ROLE};
        RESET ROLE;
        REVOKE CREATE ON SCHEMA operations FROM {KERNEL_ROLE};
        """
    )


def _assert_installed_contract() -> None:
    evidence_tables = ", ".join(
        f"'{table}'::regclass" for table in RLS_READ_TABLES
    )
    op.execute(
        f"""
        DO $e5_contract$
        DECLARE
          owner_oid oid;
          caller_oid oid;
          kernel_oid oid;
          function_oid oid;
        BEGIN
          SELECT oid INTO STRICT owner_oid
            FROM pg_catalog.pg_roles
           WHERE rolname = 'home_agent_owner';
          SELECT oid INTO STRICT caller_oid
            FROM pg_catalog.pg_roles
           WHERE rolname = '{CALLER_ROLE}';
          SELECT oid INTO STRICT kernel_oid
            FROM pg_catalog.pg_roles
           WHERE rolname = '{KERNEL_ROLE}';
          SELECT function_row.oid INTO STRICT function_oid
            FROM pg_catalog.pg_proc AS function_row
            JOIN pg_catalog.pg_namespace AS namespace_row
              ON namespace_row.oid = function_row.pronamespace
           WHERE namespace_row.nspname = 'operations'
             AND function_row.proname =
                 'evaluate_current_identity_semantic_authority'
             AND function_row.pronargs = 1
             AND function_row.proargtypes = '2950'::oidvector;

          IF NOT EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_proc AS function_row
                 JOIN pg_catalog.pg_language AS language_row
                   ON language_row.oid = function_row.prolang
                WHERE function_row.oid = function_oid
                  AND function_row.proowner = kernel_oid
                  AND function_row.prorettype =
                      'pg_catalog.varchar'::regtype
                  AND function_row.provolatile = 'v'
                  AND function_row.prosecdef
                  AND language_row.lanname = 'plpgsql'
                  AND function_row.proconfig =
                      ARRAY['search_path=pg_catalog, pg_temp']::text[]
                  AND pg_catalog.encode(
                        pg_catalog.sha256(
                          pg_catalog.convert_to(function_row.prosrc, 'UTF8')
                        ),
                        'hex'
                      ) = '{FUNCTION_BODY_SHA256}'
             ) THEN
            RAISE EXCEPTION 'identity_authority_e5_function_invalid'
              USING ERRCODE = '55000';
          END IF;
          IF (
               SELECT pg_catalog.count(*)
                 FROM pg_catalog.aclexplode(
                   (SELECT proacl FROM pg_catalog.pg_proc
                     WHERE oid = function_oid)
                 ) AS acl
                WHERE acl.privilege_type = 'EXECUTE'
                  AND acl.grantee = caller_oid
                  AND acl.grantor = kernel_oid
                  AND NOT acl.is_grantable
             ) <> 1
             OR (
               SELECT pg_catalog.count(*)
                 FROM pg_catalog.aclexplode(
                   (SELECT proacl FROM pg_catalog.pg_proc
                     WHERE oid = function_oid)
                 ) AS acl
                WHERE acl.privilege_type = 'EXECUTE'
                  AND acl.grantee = kernel_oid
                  AND acl.grantor = kernel_oid
                  AND NOT acl.is_grantable
             ) <> 1
             OR EXISTS (
               SELECT 1
                 FROM pg_catalog.aclexplode(
                   (SELECT proacl FROM pg_catalog.pg_proc
                     WHERE oid = function_oid)
                ) AS acl
                WHERE acl.privilege_type = 'EXECUTE'
                  AND (
                    acl.grantee NOT IN (caller_oid, kernel_oid)
                    OR acl.grantor <> kernel_oid
                    OR (
                      acl.grantee = caller_oid AND acl.is_grantable
                    )
                    OR (
                      acl.grantee = kernel_oid AND acl.is_grantable
                    )
                  )
             ) THEN
            RAISE EXCEPTION 'identity_authority_e5_function_acl_invalid'
              USING ERRCODE = '55000';
          END IF;
          IF (
               SELECT pg_catalog.count(*)
                 FROM pg_catalog.pg_policy AS policy_row
                 JOIN pg_catalog.pg_policy AS reference_policy
                   ON reference_policy.polrelid = '{PROMOTION}'::regclass
                  AND reference_policy.polname = '{SELECT_POLICY}'
                WHERE policy_row.polrelid IN ({evidence_tables})
                  AND policy_row.polname = '{SELECT_POLICY}'
                  AND policy_row.polcmd = 'r'
                  AND policy_row.polpermissive
                  AND policy_row.polroles = ARRAY[kernel_oid]::oid[]
                  AND policy_row.polqual IS NOT NULL
                  AND policy_row.polwithcheck IS NULL
                  AND policy_row.polqual::text =
                      reference_policy.polqual::text
             ) <> {len(RLS_READ_TABLES)}
             OR NOT EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_policy AS lock_policy
                 JOIN pg_catalog.pg_policy AS reference_policy
                   ON reference_policy.polrelid = '{RUN}'::regclass
                  AND reference_policy.polname = '{SELECT_POLICY}'
                WHERE lock_policy.polrelid = '{RUN}'::regclass
                  AND lock_policy.polname = '{RUN_LOCK_POLICY}'
                  AND lock_policy.polcmd = 'w'
                  AND lock_policy.polpermissive
                  AND lock_policy.polroles = ARRAY[kernel_oid]::oid[]
                  AND lock_policy.polqual IS NOT NULL
                  AND lock_policy.polwithcheck IS NOT NULL
                  AND lock_policy.polqual::text =
                      reference_policy.polqual::text
                  AND lock_policy.polwithcheck::text =
                      reference_policy.polqual::text
             ) THEN
            RAISE EXCEPTION 'identity_authority_e5_policy_invalid'
              USING ERRCODE = '55000';
          END IF;
          IF NOT pg_catalog.has_schema_privilege(
               kernel_oid, 'operations', 'USAGE'
             )
             OR pg_catalog.has_schema_privilege(
               kernel_oid, 'operations', 'CREATE'
             )
             OR NOT pg_catalog.has_schema_privilege(
               kernel_oid, 'privacy', 'USAGE'
             )
             OR NOT pg_catalog.has_function_privilege(
               kernel_oid,
               'privacy.lock_identity_semantic_write_fence()',
               'EXECUTE'
             )
             OR NOT pg_catalog.has_function_privilege(
               kernel_oid,
               'privacy.identity_person_is_blocked(uuid)',
               'EXECUTE'
             )
             OR NOT pg_catalog.has_column_privilege(
               kernel_oid, '{RUN}', 'expires_at', 'UPDATE'
             )
             OR pg_catalog.has_column_privilege(
               kernel_oid, '{RUN}', 'run_id', 'UPDATE'
             )
             OR NOT pg_catalog.has_column_privilege(
               kernel_oid, '{LEDGER_STATE}', 'updated_at', 'UPDATE'
             )
             OR pg_catalog.has_column_privilege(
               kernel_oid, '{LEDGER_STATE}', 'recorded_epoch', 'UPDATE'
             ) THEN
            RAISE EXCEPTION 'identity_authority_e5_grant_invalid'
              USING ERRCODE = '55000';
          END IF;
          IF (
               SELECT pg_catalog.count(*)
                 FROM pg_catalog.unnest(
                   ARRAY[{evidence_tables},
                         '{LEDGER_STATE}'::regclass,
                         '{REPLAY_RECEIPT}'::regclass]
                 ) AS expected_table(table_oid)
                WHERE pg_catalog.has_table_privilege(
                  kernel_oid, expected_table.table_oid, 'SELECT'
                )
             ) <> {len(RLS_READ_TABLES) + 2}
             OR EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_class AS table_row
                 CROSS JOIN LATERAL
                   pg_catalog.aclexplode(table_row.relacl) AS acl
                WHERE acl.grantee = kernel_oid
                  AND acl.privilege_type IN (
                    'INSERT', 'DELETE', 'TRUNCATE', 'TRIGGER'
                  )
             ) THEN
            RAISE EXCEPTION 'identity_authority_e5_table_acl_invalid'
              USING ERRCODE = '55000';
          END IF;
          IF EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_trigger AS trigger_row
                WHERE trigger_row.tgrelid IN ({evidence_tables})
                  AND NOT trigger_row.tgisinternal
                  AND NOT (
                    trigger_row.tgrelid = '{IMPACT}'::regclass
                    AND trigger_row.tgname =
                        'reviewed_identity_migration_erasure_replay_guard'
                  )
             )
             OR EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_rewrite AS rule_row
                WHERE rule_row.ev_class IN ({evidence_tables})
                  AND rule_row.rulename <> '_RETURN'
             ) THEN
            RAISE EXCEPTION 'identity_authority_e5_surface_invalid'
              USING ERRCODE = '55000';
          END IF;
        END
        $e5_contract$;
        """
    )


def upgrade() -> None:
    _validate_preconditions()
    _install_policies_and_grants()
    _install_function()
    _assert_installed_contract()


def downgrade() -> None:
    # E5 owns no durable rows. Removing it must leave the historical E4
    # promotion/admission/freeze evidence byte-for-byte untouched.
    op.execute(
        f"""
        GRANT USAGE ON SCHEMA operations TO {KERNEL_ROLE};
        SET LOCAL ROLE {KERNEL_ROLE};
        REVOKE ALL ON FUNCTION {FUNCTION}
          FROM PUBLIC, home_agent_owner, home_agent_api,
          home_agent_binding_operator, home_agent_binding_committer,
          home_agent_ingest, home_agent_worker,
          home_agent_erasure, home_agent_rollout, home_agent_backup,
          home_agent_identity_migration, home_agent_identity_kernel,
          home_agent_identity_finalizer,
          home_agent_identity_finalizer_kernel,
          home_agent_identity_erasure_kernel,
          home_agent_identity_cutover,
          home_agent_identity_cutover_kernel;
        DROP FUNCTION
          operations.evaluate_current_identity_semantic_authority(uuid)
          RESTRICT;
        RESET ROLE;

        DROP POLICY {RUN_LOCK_POLICY} ON {RUN};
        """
    )
    for table in reversed(RLS_READ_TABLES):
        op.execute(f"DROP POLICY {SELECT_POLICY} ON {table}")
    op.execute(
        f"""
        REVOKE UPDATE (expires_at) ON TABLE {RUN} FROM {KERNEL_ROLE};
        REVOKE UPDATE (updated_at) ON TABLE {LEDGER_STATE}
          FROM {KERNEL_ROLE};
        REVOKE SELECT ON TABLE
          {PROMOTION},
          {ADMISSION},
          {FREEZE},
          {RUN},
          {FINALIZATION},
          {FINALIZER_ADMISSION},
          {CANDIDATE},
          {WRITER_EVIDENCE},
          {PRIVACY_RECEIPT},
          {IMPACT},
          {LINEAGE},
          {SUBJECT},
          {LEDGER_STATE},
          {REPLAY_RECEIPT}
          FROM {KERNEL_ROLE};
        REVOKE EXECUTE ON FUNCTION
          privacy.lock_identity_semantic_write_fence(),
          privacy.identity_person_is_blocked(uuid)
          FROM {KERNEL_ROLE};
        REVOKE CREATE, USAGE ON SCHEMA privacy FROM {KERNEL_ROLE};
        REVOKE CREATE, USAGE ON SCHEMA operations
          FROM {KERNEL_ROLE}, {CALLER_ROLE};
        """
    )
