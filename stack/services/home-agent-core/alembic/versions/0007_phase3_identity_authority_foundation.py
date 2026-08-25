"""Add owner-only Phase 3 reviewed identity-migration authority records.

Revision ID: 0007_phase3_identity_authority
Revises: 0006a_worker_lease_arbitration
Create Date: 2026-07-12

This revision is schema groundwork only. It deliberately installs no runtime
writer, finalizer function, readiness gate, or parent-confirmation authority.
The future finalizer must accept the complete confirmed payload set in one
SERIALIZABLE transaction, create every semantic projection and exact receipt,
and append the candidate finalization atomically. It must independently prove
dense zero-based source/decision ordinals, exact run counts and commitments,
all six passed privacy categories, signature/build bindings, and the exact
shadow predecessor. It must also reject collisions with or adoption of every
pre-0007 legacy-source projection.

Private source values never enter these tables. ``*_commitment`` columns are
domain-separated keyed HMAC commitments; public build/policy artifacts use
``*_digest``. Enrollment/recognition source rows are manifest items and require
an explicit apply, suppression, rule exclusion, or coalescing decision. The
fixed source-projection contract binds schema-v1 tables outside the selected
identity domain (including preferences, notes, and change logs) without
persisting their content or paths.

The legacy writer evidence strengths in this revision are point-in-time only.
Neither ``observed_stopped`` nor ``operator_attested`` means physically frozen
or enforced offline, so finalization and cutover rows remain explicitly
candidate/unverified and can never satisfy authoritative readiness.

The runtime migration pin intentionally remains revision 0006a on this branch.
Do not run the normal ``alembic upgrade head``/migrate deployment profile with
this groundwork release: Core must fail closed on revision 0007 until the later
atomic finalizer and authoritative-readiness release updates the runtime pin.


Table DDL in this revision is FROZEN. It is written out in full here rather than
rendered from ``app.schema``. ``app.schema`` describes the CURRENT shape of each
table and is rewritten whenever a later revision alters one, so a migration that
renders from it silently changes what it emits as the schema moves on. Revision
0010 rewrites the erasure-impacts table, which made this revision emit a foreign
key to a table 0010 does not create until three revisions later, so the chain
could not apply at all. Do not reintroduce table rendering from ``app.schema``
here. Edit this DDL only to correct a transcription error against the shape this
revision actually installed.
"""

from __future__ import annotations

from typing import Sequence

from alembic import op


revision: str = "0007_phase3_identity_authority"
down_revision: str | None = "0006a_worker_lease_arbitration"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


PHASE3_IDENTITY_TABLES = (
    ("operations", "reviewed_identity_migration_runs"),
    ("operations", "reviewed_identity_migration_source_items"),
    ("operations", "reviewed_identity_migration_decisions"),
    ("operations", "reviewed_identity_migration_item_receipts"),
    ("operations", "reviewed_identity_migration_finalizations"),
    ("operations", "legacy_identity_writer_evidence"),
    ("operations", "privacy_cutover_check_receipts"),
    ("operations", "semantic_authority_cutovers"),
    ("operations", "reviewed_identity_migration_erasure_impacts"),
)

FROZEN_TABLE_DDL = {
    "operations.reviewed_identity_migration_runs": """
CREATE TABLE IF NOT EXISTS operations.reviewed_identity_migration_runs (
	run_id UUID NOT NULL,
	operator_request_id UUID NOT NULL,
	contract_version VARCHAR(64) NOT NULL,
	source_schema_version INTEGER NOT NULL,
	source_projection_contract_version VARCHAR(64) NOT NULL,
	importer_version VARCHAR(64) NOT NULL,
	canonicalization_version VARCHAR(64) NOT NULL,
	projection_version VARCHAR(64) NOT NULL,
	shadow_rule_version VARCHAR(128) NOT NULL,
	commitment_algorithm VARCHAR(32) NOT NULL,
	commitment_key_fingerprint VARCHAR(64) NOT NULL,
	commitment_key_epoch BIGINT NOT NULL,
	source_item_count INTEGER NOT NULL,
	decision_count INTEGER NOT NULL,
	logical_source_manifest_commitment VARCHAR(64) NOT NULL,
	projection_manifest_commitment VARCHAR(64) NOT NULL,
	source_projection_contract_digest VARCHAR(64) NOT NULL,
	review_receipt_commitment VARCHAR(64) NOT NULL,
	policy_version VARCHAR(128) NOT NULL,
	policy_digest VARCHAR(64) NOT NULL,
	shadow_authorization_id UUID NOT NULL,
	release_manifest_digest VARCHAR(64) NOT NULL,
	migration_tool_bundle_digest VARCHAR(64) NOT NULL,
	core_oci_manifest_digest VARCHAR(64) NOT NULL,
	core_schema_digest VARCHAR(64) NOT NULL,
	core_capability_digest VARCHAR(64) NOT NULL,
	signature_algorithm VARCHAR(16) NOT NULL,
	signing_key_fingerprint VARCHAR(64) NOT NULL,
	review_signature VARCHAR(128) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT transaction_timestamp() NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	CONSTRAINT pk_reviewed_identity_migration_runs PRIMARY KEY (run_id),
	CONSTRAINT ck_reviewed_identity_migration_runs_uuidv7_ids CHECK (substring(run_id::text from 15 for 1) = '7' AND substring(run_id::text from 20 for 1) IN ('8','9','a','b') AND substring(operator_request_id::text from 15 for 1) = '7' AND substring(operator_request_id::text from 20 for 1) IN ('8','9','a','b')),
	CONSTRAINT ck_reviewed_identity_migration_runs_fixed_contract_versions CHECK (contract_version = 'reviewed-identity-migration-run-v1' AND source_schema_version = 1 AND source_projection_contract_version = 'legacy-identity-source-projection-v1' AND importer_version = 'legacy-identity-importer-v1' AND canonicalization_version = 'identity-canonicalization-v1' AND projection_version = 'semantic-people-projection-v1' AND shadow_rule_version = 'record-only-envelope-worker-gate-v3' AND commitment_algorithm = 'hmac-sha256-v1'),
	CONSTRAINT ck_reviewed_identity_migration_runs_admission_caps CHECK (source_item_count BETWEEN 1 AND 10000 AND decision_count BETWEEN 1 AND 10000),
	CONSTRAINT ck_reviewed_identity_migration_runs_signed_commitment_shape CHECK (logical_source_manifest_commitment ~ '^[0-9a-f]{64}$' AND projection_manifest_commitment ~ '^[0-9a-f]{64}$' AND source_projection_contract_digest ~ '^[0-9a-f]{64}$' AND review_receipt_commitment ~ '^[0-9a-f]{64}$' AND commitment_key_fingerprint ~ '^[0-9a-f]{64}$' AND policy_digest ~ '^[0-9a-f]{64}$' AND release_manifest_digest ~ '^[0-9a-f]{64}$' AND migration_tool_bundle_digest ~ '^[0-9a-f]{64}$' AND core_oci_manifest_digest ~ '^[0-9a-f]{64}$' AND core_schema_digest ~ '^[0-9a-f]{64}$' AND core_capability_digest ~ '^[0-9a-f]{64}$' AND signing_key_fingerprint ~ '^[0-9a-f]{64}$' AND review_signature ~ '^[0-9a-f]{128}$'),
	CONSTRAINT ck_reviewed_identity_migration_runs_signature_and_policy_shape CHECK (signature_algorithm = 'ed25519' AND commitment_key_epoch > 0 AND policy_version ~ '^[a-z0-9][a-z0-9._-]{0,127}$'),
	CONSTRAINT ck_reviewed_identity_migration_runs_database_expiry CHECK (expires_at > created_at AND expires_at <= created_at + interval '24 hours'),
	CONSTRAINT uq_reviewed_identity_migration_runs_operator_request_id UNIQUE (operator_request_id),
	CONSTRAINT uq_reviewed_identity_migration_runs_review_receipt_commitment UNIQUE (review_receipt_commitment),
	CONSTRAINT fk_identity_migration_run_shadow_authorization FOREIGN KEY(shadow_authorization_id) REFERENCES operations.rollout_authorizations (authorization_id)
)
""",
    "operations.reviewed_identity_migration_source_items": """
CREATE TABLE IF NOT EXISTS operations.reviewed_identity_migration_source_items (
	source_item_id UUID NOT NULL,
	run_id UUID NOT NULL,
	ordinal INTEGER NOT NULL,
	source_table_kind VARCHAR(32) NOT NULL,
	row_key_commitment VARCHAR(64) NOT NULL,
	allowed_projection_commitment VARCHAR(64) NOT NULL,
	recorded_at TIMESTAMP WITH TIME ZONE DEFAULT transaction_timestamp() NOT NULL,
	CONSTRAINT pk_reviewed_identity_migration_source_items PRIMARY KEY (source_item_id),
	CONSTRAINT fk_identity_migration_source_item_run FOREIGN KEY(run_id) REFERENCES operations.reviewed_identity_migration_runs (run_id),
	CONSTRAINT identity_source_item_ordinal UNIQUE (run_id, ordinal),
	CONSTRAINT identity_source_item_id UNIQUE (run_id, source_item_id),
	CONSTRAINT identity_source_row_key UNIQUE (run_id, row_key_commitment),
	CONSTRAINT ck_reviewed_identity_migration_source_items_uuidv7_id CHECK (substring(source_item_id::text from 15 for 1) = '7' AND substring(source_item_id::text from 20 for 1) IN ('8','9','a','b')),
	CONSTRAINT ck_reviewed_identity_migration_source_items_ordinal_cap CHECK (ordinal BETWEEN 0 AND 9999),
	CONSTRAINT ck_reviewed_identity_migration_source_items_source_table_kind CHECK (source_table_kind IN ('schema_meta','identities','identity_aliases','enrollments','relationships')),
	CONSTRAINT ck_reviewed_identity_migration_source_items_commitment_shape CHECK (row_key_commitment ~ '^[0-9a-f]{64}$' AND allowed_projection_commitment ~ '^[0-9a-f]{64}$')
)
""",
    "operations.reviewed_identity_migration_decisions": """
CREATE TABLE IF NOT EXISTS operations.reviewed_identity_migration_decisions (
	decision_id UUID NOT NULL,
	run_id UUID NOT NULL,
	source_item_id UUID NOT NULL,
	ordinal INTEGER NOT NULL,
	decision_kind VARCHAR(48) NOT NULL,
	disposition VARCHAR(32) NOT NULL,
	candidate_commitment VARCHAR(64),
	canonical_apply_decision_id UUID,
	canonical_apply_disposition VARCHAR(32),
	decision_commitment VARCHAR(64) NOT NULL,
	recorded_at TIMESTAMP WITH TIME ZONE DEFAULT transaction_timestamp() NOT NULL,
	CONSTRAINT pk_reviewed_identity_migration_decisions PRIMARY KEY (decision_id),
	CONSTRAINT fk_identity_migration_decision_source_item FOREIGN KEY(run_id, source_item_id) REFERENCES operations.reviewed_identity_migration_source_items (run_id, source_item_id),
	CONSTRAINT identity_decision_ordinal UNIQUE (run_id, ordinal),
	CONSTRAINT identity_decision_id UNIQUE (run_id, decision_id),
	CONSTRAINT identity_decision_exact UNIQUE (run_id, decision_id, decision_kind, disposition, candidate_commitment),
	CONSTRAINT fk_identity_decision_canonical_apply FOREIGN KEY(run_id, canonical_apply_decision_id, decision_kind, canonical_apply_disposition, candidate_commitment) REFERENCES operations.reviewed_identity_migration_decisions (run_id, decision_id, decision_kind, disposition, candidate_commitment),
	CONSTRAINT ck_reviewed_identity_migration_decisions_uuidv7_id CHECK (substring(decision_id::text from 15 for 1) = '7' AND substring(decision_id::text from 20 for 1) IN ('8','9','a','b')),
	CONSTRAINT ck_reviewed_identity_migration_decisions_ordinal_cap CHECK (ordinal BETWEEN 0 AND 9999),
	CONSTRAINT ck_reviewed_identity_migration_decisions_decision_kind CHECK (decision_kind IN ('person','privacy_directive','person_status','alias','recognition_binding','legacy_role_candidate','legacy_relationship_candidate','explicit_omission')),
	CONSTRAINT ck_reviewed_identity_migration_decisions_disposition CHECK (disposition IN ('apply','privacy_suppressed','out_of_scope_by_rule','coalesced_duplicate')),
	CONSTRAINT ck_reviewed_identity_migration_decisions_commitment_shape CHECK (decision_commitment ~ '^[0-9a-f]{64}$' AND (candidate_commitment IS NULL OR candidate_commitment ~ '^[0-9a-f]{64}$')),
	CONSTRAINT ck_reviewed_identity_migration_decisions_decision_shape CHECK ((disposition = 'apply' AND decision_kind <> 'explicit_omission' AND candidate_commitment IS NOT NULL AND canonical_apply_decision_id IS NULL AND canonical_apply_disposition IS NULL) OR (disposition IN ('privacy_suppressed','out_of_scope_by_rule') AND decision_kind = 'explicit_omission' AND candidate_commitment IS NULL AND canonical_apply_decision_id IS NULL AND canonical_apply_disposition IS NULL) OR (disposition = 'coalesced_duplicate' AND decision_kind <> 'explicit_omission' AND candidate_commitment IS NOT NULL AND canonical_apply_decision_id IS NOT NULL AND canonical_apply_decision_id <> decision_id AND canonical_apply_disposition = 'apply')),
	CONSTRAINT uq_reviewed_identity_migration_decisions_decision_commitment UNIQUE (decision_commitment)
)
""",
    "operations.reviewed_identity_migration_item_receipts": """
CREATE TABLE IF NOT EXISTS operations.reviewed_identity_migration_item_receipts (
	receipt_id UUID NOT NULL,
	run_id UUID NOT NULL,
	decision_id UUID NOT NULL,
	decision_kind VARCHAR(48) NOT NULL,
	decision_disposition VARCHAR(32) NOT NULL,
	candidate_commitment VARCHAR(64) NOT NULL,
	outcome VARCHAR(32) NOT NULL,
	projection_table_kind VARCHAR(64) NOT NULL,
	projection_ref_commitment VARCHAR(64) NOT NULL,
	projection_commitment VARCHAR(64) NOT NULL,
	receipt_commitment VARCHAR(64) NOT NULL,
	database_transaction_id BIGINT NOT NULL,
	applied_at TIMESTAMP WITH TIME ZONE DEFAULT transaction_timestamp() NOT NULL,
	CONSTRAINT pk_reviewed_identity_migration_item_receipts PRIMARY KEY (receipt_id),
	CONSTRAINT fk_identity_migration_receipt_apply_decision FOREIGN KEY(run_id, decision_id, decision_kind, decision_disposition, candidate_commitment) REFERENCES operations.reviewed_identity_migration_decisions (run_id, decision_id, decision_kind, disposition, candidate_commitment),
	CONSTRAINT identity_receipt_decision UNIQUE (run_id, decision_id),
	CONSTRAINT identity_receipt_id UNIQUE (run_id, receipt_id),
	CONSTRAINT lineage_identity UNIQUE (run_id, receipt_id, decision_id, decision_kind, projection_table_kind, projection_ref_commitment),
	CONSTRAINT identity_receipt_projection_ref UNIQUE (run_id, decision_kind, projection_ref_commitment),
	CONSTRAINT ck_reviewed_identity_migration_item_receipts_uuidv7_id CHECK (substring(receipt_id::text from 15 for 1) = '7' AND substring(receipt_id::text from 20 for 1) IN ('8','9','a','b')),
	CONSTRAINT ck_reviewed_identity_migration_item_receipts_apply_outcome CHECK (decision_disposition = 'apply' AND outcome IN ('inserted_exact','replayed_exact')),
	CONSTRAINT ck_reviewed_identity_migration_item_receipts_commitment_shape CHECK (candidate_commitment ~ '^[0-9a-f]{64}$' AND projection_ref_commitment ~ '^[0-9a-f]{64}$' AND projection_commitment ~ '^[0-9a-f]{64}$' AND receipt_commitment ~ '^[0-9a-f]{64}$'),
	CONSTRAINT ck_reviewed_identity_migration_item_receipts_transaction_marker CHECK (database_transaction_id > 0),
	CONSTRAINT ck_reviewed_identity_migration_item_receipts_projection_fcd0 CHECK ((decision_kind IN ('person','person_status') AND projection_table_kind = 'identity.people') OR (decision_kind = 'alias' AND projection_table_kind = 'identity.aliases') OR (decision_kind = 'recognition_binding' AND projection_table_kind = 'identity.external_recognition_bindings') OR (decision_kind = 'privacy_directive' AND projection_table_kind = 'identity.privacy_directives') OR (decision_kind = 'legacy_role_candidate' AND projection_table_kind = 'identity.legacy_role_labels') OR (decision_kind = 'legacy_relationship_candidate' AND projection_table_kind = 'identity.legacy_relationship_candidates')),
	CONSTRAINT uq_reviewed_identity_migration_item_receipts_receipt_commitment UNIQUE (receipt_commitment)
)
""",
    "operations.reviewed_identity_migration_finalizations": """
CREATE TABLE IF NOT EXISTS operations.reviewed_identity_migration_finalizations (
	finalization_id UUID NOT NULL,
	run_id UUID NOT NULL,
	contract_version VARCHAR(64) NOT NULL,
	verification_status VARCHAR(32) NOT NULL,
	authoritative BOOLEAN DEFAULT false NOT NULL,
	source_item_count INTEGER NOT NULL,
	decision_count INTEGER NOT NULL,
	apply_decision_count INTEGER NOT NULL,
	receipt_count INTEGER NOT NULL,
	source_manifest_commitment VARCHAR(64) NOT NULL,
	projection_manifest_commitment VARCHAR(64) NOT NULL,
	decision_manifest_commitment VARCHAR(64) NOT NULL,
	receipt_set_commitment VARCHAR(64) NOT NULL,
	finalization_commitment VARCHAR(64) NOT NULL,
	database_transaction_id BIGINT NOT NULL,
	signature_algorithm VARCHAR(16) NOT NULL,
	signing_key_fingerprint VARCHAR(64) NOT NULL,
	finalization_signature VARCHAR(128) NOT NULL,
	finalized_at TIMESTAMP WITH TIME ZONE DEFAULT transaction_timestamp() NOT NULL,
	CONSTRAINT pk_reviewed_identity_migration_finalizations PRIMARY KEY (finalization_id),
	CONSTRAINT fk_identity_migration_finalization_run FOREIGN KEY(run_id) REFERENCES operations.reviewed_identity_migration_runs (run_id),
	CONSTRAINT identity_finalization_identity UNIQUE (run_id, finalization_id),
	CONSTRAINT ck_reviewed_identity_migration_finalizations_uuidv7_id CHECK (substring(finalization_id::text from 15 for 1) = '7' AND substring(finalization_id::text from 20 for 1) IN ('8','9','a','b')),
	CONSTRAINT ck_reviewed_identity_migration_finalizations_non_author_49c1 CHECK (contract_version = 'reviewed-identity-migration-finalization-v1' AND verification_status = 'candidate_unverified' AND authoritative = false),
	CONSTRAINT ck_reviewed_identity_migration_finalizations_bounded_counts CHECK (source_item_count BETWEEN 1 AND 10000 AND decision_count BETWEEN 1 AND 10000 AND apply_decision_count BETWEEN 0 AND decision_count AND receipt_count = apply_decision_count),
	CONSTRAINT ck_reviewed_identity_migration_finalizations_signed_com_25c5 CHECK (source_manifest_commitment ~ '^[0-9a-f]{64}$' AND projection_manifest_commitment ~ '^[0-9a-f]{64}$' AND decision_manifest_commitment ~ '^[0-9a-f]{64}$' AND receipt_set_commitment ~ '^[0-9a-f]{64}$' AND finalization_commitment ~ '^[0-9a-f]{64}$' AND signing_key_fingerprint ~ '^[0-9a-f]{64}$' AND finalization_signature ~ '^[0-9a-f]{128}$'),
	CONSTRAINT ck_reviewed_identity_migration_finalizations_transactio_9879 CHECK (database_transaction_id > 0 AND signature_algorithm = 'ed25519'),
	CONSTRAINT uq_reviewed_identity_migration_finalizations_run_id UNIQUE (run_id),
	CONSTRAINT uq_reviewed_identity_migration_finalizations_finalizati_0fc1 UNIQUE (finalization_commitment)
)
""",
    "operations.legacy_identity_writer_evidence": """
CREATE TABLE IF NOT EXISTS operations.legacy_identity_writer_evidence (
	evidence_id UUID NOT NULL,
	run_id UUID NOT NULL,
	source_installation_id UUID NOT NULL,
	semantic_generation BIGINT NOT NULL,
	source_projection_commitment VARCHAR(64) NOT NULL,
	evidence_strength VARCHAR(32) NOT NULL,
	integrity_result VARCHAR(24) NOT NULL,
	checkpoint_result VARCHAR(24) NOT NULL,
	journal_result VARCHAR(24) NOT NULL,
	legacy_context_cutoff_status VARCHAR(32) NOT NULL,
	release_manifest_digest VARCHAR(64) NOT NULL,
	freeze_kernel_build_digest VARCHAR(64) NOT NULL,
	evidence_commitment VARCHAR(64) NOT NULL,
	signature_algorithm VARCHAR(16) NOT NULL,
	signing_key_fingerprint VARCHAR(64) NOT NULL,
	evidence_signature VARCHAR(128) NOT NULL,
	observed_at TIMESTAMP WITH TIME ZONE NOT NULL,
	recorded_at TIMESTAMP WITH TIME ZONE DEFAULT transaction_timestamp() NOT NULL,
	CONSTRAINT pk_legacy_identity_writer_evidence PRIMARY KEY (evidence_id),
	CONSTRAINT fk_legacy_identity_writer_evidence_run FOREIGN KEY(run_id) REFERENCES operations.reviewed_identity_migration_runs (run_id),
	CONSTRAINT legacy_writer_evidence_id UNIQUE (run_id, evidence_id),
	CONSTRAINT ck_legacy_identity_writer_evidence_uuidv7_ids CHECK (substring(evidence_id::text from 15 for 1) = '7' AND substring(evidence_id::text from 20 for 1) IN ('8','9','a','b') AND substring(source_installation_id::text from 15 for 1) = '7' AND substring(source_installation_id::text from 20 for 1) IN ('8','9','a','b')),
	CONSTRAINT ck_legacy_identity_writer_evidence_categorical_results CHECK (evidence_strength IN ('observed_stopped','operator_attested') AND integrity_result IN ('passed','failed','not_observed') AND checkpoint_result IN ('complete','incomplete','not_observed') AND journal_result IN ('clean','pending','not_observed') AND legacy_context_cutoff_status IN ('observed_cutoff','operator_attested_cutoff','not_observed')),
	CONSTRAINT ck_legacy_identity_writer_evidence_signed_commitment_shape CHECK (release_manifest_digest ~ '^[0-9a-f]{64}$' AND freeze_kernel_build_digest ~ '^[0-9a-f]{64}$' AND source_projection_commitment ~ '^[0-9a-f]{64}$' AND evidence_commitment ~ '^[0-9a-f]{64}$' AND signing_key_fingerprint ~ '^[0-9a-f]{64}$' AND evidence_signature ~ '^[0-9a-f]{128}$'),
	CONSTRAINT ck_legacy_identity_writer_evidence_signature_and_time CHECK (semantic_generation > 0 AND signature_algorithm = 'ed25519' AND observed_at <= recorded_at),
	CONSTRAINT uq_legacy_identity_writer_evidence_evidence_commitment UNIQUE (evidence_commitment)
)
""",
    "operations.privacy_cutover_check_receipts": """
CREATE TABLE IF NOT EXISTS operations.privacy_cutover_check_receipts (
	check_id UUID NOT NULL,
	run_id UUID NOT NULL,
	finalization_id UUID NOT NULL,
	check_category VARCHAR(24) NOT NULL,
	check_result VARCHAR(16) NOT NULL,
	residual_code VARCHAR(32) NOT NULL,
	check_commitment VARCHAR(64) NOT NULL,
	receipt_commitment VARCHAR(64) NOT NULL,
	policy_digest VARCHAR(64) NOT NULL,
	checked_at TIMESTAMP WITH TIME ZONE DEFAULT transaction_timestamp() NOT NULL,
	CONSTRAINT pk_privacy_cutover_check_receipts PRIMARY KEY (check_id),
	CONSTRAINT fk_privacy_check_identity_finalization FOREIGN KEY(run_id, finalization_id) REFERENCES operations.reviewed_identity_migration_finalizations (run_id, finalization_id),
	CONSTRAINT privacy_check_category UNIQUE (run_id, check_category),
	CONSTRAINT privacy_check_identity UNIQUE (run_id, check_id, check_category),
	CONSTRAINT privacy_check_exact_identity UNIQUE (run_id, check_id, check_category, check_result, residual_code),
	CONSTRAINT ck_privacy_cutover_check_receipts_uuidv7_id CHECK (substring(check_id::text from 15 for 1) = '7' AND substring(check_id::text from 20 for 1) IN ('8','9','a','b')),
	CONSTRAINT ck_privacy_cutover_check_receipts_check_category CHECK (check_category IN ('ingress','retrieval','prompt','initiative','export','edge_block')),
	CONSTRAINT ck_privacy_cutover_check_receipts_result_codes CHECK (check_result IN ('passed','blocked') AND residual_code IN ('none','legacy_untracked','backup_expiry_pending','external_deletion_pending','coverage_unproven')),
	CONSTRAINT ck_privacy_cutover_check_receipts_result_residual_shape CHECK ((check_result = 'passed' AND residual_code = 'none') OR (check_result = 'blocked' AND residual_code <> 'none')),
	CONSTRAINT ck_privacy_cutover_check_receipts_commitment_shape CHECK (check_commitment ~ '^[0-9a-f]{64}$' AND receipt_commitment ~ '^[0-9a-f]{64}$' AND policy_digest ~ '^[0-9a-f]{64}$'),
	CONSTRAINT uq_privacy_cutover_check_receipts_check_commitment UNIQUE (check_commitment),
	CONSTRAINT uq_privacy_cutover_check_receipts_receipt_commitment UNIQUE (receipt_commitment)
)
""",
    "operations.semantic_authority_cutovers": """
CREATE TABLE IF NOT EXISTS operations.semantic_authority_cutovers (
	cutover_id UUID NOT NULL,
	run_id UUID NOT NULL,
	finalization_id UUID NOT NULL,
	writer_evidence_id UUID NOT NULL,
	contract_version VARCHAR(64) NOT NULL,
	authority_status VARCHAR(32) NOT NULL,
	authoritative BOOLEAN DEFAULT false NOT NULL,
	ingress_check_id UUID NOT NULL,
	ingress_check_category VARCHAR(24) NOT NULL,
	retrieval_check_id UUID NOT NULL,
	retrieval_check_category VARCHAR(24) NOT NULL,
	prompt_check_id UUID NOT NULL,
	prompt_check_category VARCHAR(24) NOT NULL,
	initiative_check_id UUID NOT NULL,
	initiative_check_category VARCHAR(24) NOT NULL,
	export_check_id UUID NOT NULL,
	export_check_category VARCHAR(24) NOT NULL,
	edge_block_check_id UUID NOT NULL,
	edge_block_check_category VARCHAR(24) NOT NULL,
	required_privacy_check_result VARCHAR(16) NOT NULL,
	required_privacy_residual_code VARCHAR(32) NOT NULL,
	privacy_check_set_commitment VARCHAR(64) NOT NULL,
	cutover_commitment VARCHAR(64) NOT NULL,
	policy_digest VARCHAR(64) NOT NULL,
	signature_algorithm VARCHAR(16) NOT NULL,
	signing_key_fingerprint VARCHAR(64) NOT NULL,
	cutover_signature VARCHAR(128) NOT NULL,
	attested_at TIMESTAMP WITH TIME ZONE DEFAULT transaction_timestamp() NOT NULL,
	CONSTRAINT pk_semantic_authority_cutovers PRIMARY KEY (cutover_id),
	CONSTRAINT fk_semantic_cutover_identity_finalization FOREIGN KEY(run_id, finalization_id) REFERENCES operations.reviewed_identity_migration_finalizations (run_id, finalization_id),
	CONSTRAINT fk_semantic_cutover_writer_evidence FOREIGN KEY(run_id, writer_evidence_id) REFERENCES operations.legacy_identity_writer_evidence (run_id, evidence_id),
	CONSTRAINT fk_semantic_cutover_ingress_check FOREIGN KEY(run_id, ingress_check_id, ingress_check_category, required_privacy_check_result, required_privacy_residual_code) REFERENCES operations.privacy_cutover_check_receipts (run_id, check_id, check_category, check_result, residual_code),
	CONSTRAINT fk_semantic_cutover_retrieval_check FOREIGN KEY(run_id, retrieval_check_id, retrieval_check_category, required_privacy_check_result, required_privacy_residual_code) REFERENCES operations.privacy_cutover_check_receipts (run_id, check_id, check_category, check_result, residual_code),
	CONSTRAINT fk_semantic_cutover_prompt_check FOREIGN KEY(run_id, prompt_check_id, prompt_check_category, required_privacy_check_result, required_privacy_residual_code) REFERENCES operations.privacy_cutover_check_receipts (run_id, check_id, check_category, check_result, residual_code),
	CONSTRAINT fk_semantic_cutover_initiative_check FOREIGN KEY(run_id, initiative_check_id, initiative_check_category, required_privacy_check_result, required_privacy_residual_code) REFERENCES operations.privacy_cutover_check_receipts (run_id, check_id, check_category, check_result, residual_code),
	CONSTRAINT fk_semantic_cutover_export_check FOREIGN KEY(run_id, export_check_id, export_check_category, required_privacy_check_result, required_privacy_residual_code) REFERENCES operations.privacy_cutover_check_receipts (run_id, check_id, check_category, check_result, residual_code),
	CONSTRAINT fk_semantic_cutover_edge_block_check FOREIGN KEY(run_id, edge_block_check_id, edge_block_check_category, required_privacy_check_result, required_privacy_residual_code) REFERENCES operations.privacy_cutover_check_receipts (run_id, check_id, check_category, check_result, residual_code),
	CONSTRAINT ck_semantic_authority_cutovers_uuidv7_id CHECK (substring(cutover_id::text from 15 for 1) = '7' AND substring(cutover_id::text from 20 for 1) IN ('8','9','a','b')),
	CONSTRAINT ck_semantic_authority_cutovers_non_authoritative_contract CHECK (contract_version = 'semantic-authority-cutover-candidate-v1' AND authority_status = 'candidate_unenforced' AND authoritative = false),
	CONSTRAINT ck_semantic_authority_cutovers_privacy_check_categories CHECK (ingress_check_category = 'ingress' AND retrieval_check_category = 'retrieval' AND prompt_check_category = 'prompt' AND initiative_check_category = 'initiative' AND export_check_category = 'export' AND edge_block_check_category = 'edge_block' AND required_privacy_check_result = 'passed' AND required_privacy_residual_code = 'none'),
	CONSTRAINT ck_semantic_authority_cutovers_signed_commitment_shape CHECK (privacy_check_set_commitment ~ '^[0-9a-f]{64}$' AND cutover_commitment ~ '^[0-9a-f]{64}$' AND policy_digest ~ '^[0-9a-f]{64}$' AND signing_key_fingerprint ~ '^[0-9a-f]{64}$' AND cutover_signature ~ '^[0-9a-f]{128}$'),
	CONSTRAINT ck_semantic_authority_cutovers_signature_algorithm CHECK (signature_algorithm = 'ed25519'),
	CONSTRAINT uq_semantic_authority_cutovers_run_id UNIQUE (run_id),
	CONSTRAINT uq_semantic_authority_cutovers_finalization_id UNIQUE (finalization_id),
	CONSTRAINT uq_semantic_authority_cutovers_cutover_commitment UNIQUE (cutover_commitment)
)
""",
    "operations.reviewed_identity_migration_erasure_impacts": """
CREATE TABLE IF NOT EXISTS operations.reviewed_identity_migration_erasure_impacts (
	impact_id UUID NOT NULL,
	run_id UUID NOT NULL,
	erasure_request_id UUID NOT NULL,
	impact_code VARCHAR(32) NOT NULL,
	readiness_suspension VARCHAR(16) NOT NULL,
	removed_leaf_commitment_count INTEGER NOT NULL,
	unlinked_projection_count INTEGER NOT NULL,
	impact_commitment VARCHAR(64) NOT NULL,
	recorded_at TIMESTAMP WITH TIME ZONE DEFAULT transaction_timestamp() NOT NULL,
	CONSTRAINT pk_reviewed_identity_migration_erasure_impacts PRIMARY KEY (impact_id),
	CONSTRAINT fk_identity_migration_erasure_impact_run FOREIGN KEY(run_id) REFERENCES operations.reviewed_identity_migration_runs (run_id),
	CONSTRAINT fk_identity_migration_erasure_request FOREIGN KEY(erasure_request_id) REFERENCES privacy.erasure_requests (erasure_request_id),
	CONSTRAINT identity_erasure_impact_request UNIQUE (run_id, erasure_request_id),
	CONSTRAINT ck_reviewed_identity_migration_erasure_impacts_uuidv7_ids CHECK (substring(impact_id::text from 15 for 1) = '7' AND substring(impact_id::text from 20 for 1) IN ('8','9','a','b') AND substring(erasure_request_id::text from 15 for 1) = '7' AND substring(erasure_request_id::text from 20 for 1) IN ('8','9','a','b')),
	CONSTRAINT ck_reviewed_identity_migration_erasure_impacts_impact_shape CHECK (impact_code IN ('leaf_commitments_removed','linkage_unavailable') AND readiness_suspension = 'required'),
	CONSTRAINT ck_reviewed_identity_migration_erasure_impacts_impact_caps CHECK (removed_leaf_commitment_count BETWEEN 0 AND 30000 AND unlinked_projection_count BETWEEN 0 AND 10000),
	CONSTRAINT ck_reviewed_identity_migration_erasure_impacts_impact_c_4c72 CHECK ((impact_code = 'leaf_commitments_removed' AND removed_leaf_commitment_count > 0 AND unlinked_projection_count = 0) OR (impact_code = 'linkage_unavailable' AND unlinked_projection_count > 0)),
	CONSTRAINT ck_reviewed_identity_migration_erasure_impacts_commitment_shape CHECK (impact_commitment ~ '^[0-9a-f]{64}$'),
	CONSTRAINT uq_reviewed_identity_migration_erasure_impacts_impact_c_6f7b UNIQUE (impact_commitment)
)
""",
}

RUNTIME_AND_OPERATOR_ROLES = (
    "home_agent_api",
    "home_agent_binding_operator",
    "home_agent_ingest",
    "home_agent_worker",
    "home_agent_erasure",
    "home_agent_rollout",
    "home_agent_backup",
)

TABLE_COMMENTS = {
    "reviewed_identity_migration_runs": (
        "Owner-only signed review header. Aggregate counts are never sufficient; "
        "source items and decisions must exist before a future atomic finalizer. "
        "Pre-0007 semantic rows remain unadopted and unreviewed."
    ),
    "reviewed_identity_migration_source_items": (
        "Content-free, zero-based source-row manifest. Commitments are keyed and "
        "duplicate allowed-projection commitments are valid across distinct row keys."
    ),
    "reviewed_identity_migration_decisions": (
        "Content-free projection or explicit-omission decision manifest. Every "
        "enrollment row requires an explicit decision; silent omission is invalid."
    ),
    "reviewed_identity_migration_item_receipts": (
        "Candidate exact-projection receipts. A future finalizer must insert all "
        "projections and receipts in its single SERIALIZABLE transaction."
    ),
    "reviewed_identity_migration_finalizations": (
        "Candidate-only finalization record. Revision 0007 cannot verify density, "
        "completeness, transaction atomicity, or authority."
    ),
    "legacy_identity_writer_evidence": (
        "Signed point-in-time writer observation bound to one installation, semantic "
        "generation, projection commitment, and build; never a freeze assertion."
    ),
    "privacy_cutover_check_receipts": (
        "Fixed-category candidate privacy checks. A future cutover kernel must require "
        "all six categories passed with residual_code none."
    ),
    "semantic_authority_cutovers": (
        "Candidate-unenforced cutover attestation. It is non-authoritative until a "
        "future migration adds and verifies enforced-offline writer evidence."
    ),
    "reviewed_identity_migration_erasure_impacts": (
        "Run-level erasure suspension after linkable leaf commitments are removed. "
        "Presence must invalidate future readiness; no retained leaf FK is allowed."
    ),
}


def _qualified(table: tuple[str, str]) -> str:
    schema_name, table_name = table
    return f'"{schema_name}"."{table_name}"'


def upgrade() -> None:
    for table in PHASE3_IDENTITY_TABLES:
        op.execute(FROZEN_TABLE_DDL[f"{table[0]}.{table[1]}"])

    for table in PHASE3_IDENTITY_TABLES:
        qualified = _qualified(table)
        table_name = table[1]
        comment = TABLE_COMMENTS[table_name].replace("'", "''")
        op.execute(f"COMMENT ON TABLE {qualified} IS '{comment}'")
        op.execute(f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {qualified} FORCE ROW LEVEL SECURITY")
        select_policy = f"{table_name}_owner_select"
        insert_policy = f"{table_name}_owner_insert"
        op.execute(f"DROP POLICY IF EXISTS {select_policy} ON {qualified}")
        op.execute(f"DROP POLICY IF EXISTS {insert_policy} ON {qualified}")
        op.execute(
            f"CREATE POLICY {select_policy} ON {qualified} FOR SELECT "
            "TO home_agent_owner "
            "USING (session_user = 'home_agent_owner')"
        )
        op.execute(
            f"CREATE POLICY {insert_policy} ON {qualified} FOR INSERT "
            "TO home_agent_owner "
            "WITH CHECK (session_user = 'home_agent_owner')"
        )
        roles = ", ".join(RUNTIME_AND_OPERATOR_ROLES)
        op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {qualified} FROM PUBLIC, {roles}")
        op.execute(f"GRANT SELECT, INSERT ON TABLE {qualified} TO home_agent_owner")


def downgrade() -> None:
    predicates = " OR ".join(
        f"EXISTS (SELECT 1 FROM {_qualified(table)})"
        for table in PHASE3_IDENTITY_TABLES
    )
    op.execute(
        f"""
        DO $$
        BEGIN
          IF {predicates} THEN
            RAISE EXCEPTION
              'refusing to drop nonempty Phase 3 identity authority evidence'
              USING ERRCODE = '2BP01';
          END IF;
        END
        $$
        """
    )
    for table in reversed(PHASE3_IDENTITY_TABLES):
        op.drop_table(table[1], schema=table[0])
