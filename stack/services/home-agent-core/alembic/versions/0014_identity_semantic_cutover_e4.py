"""Add the dormant E4 reviewed identity semantic-cutover kernel.

Revision ID: 0014_identity_cutover_e4
Revises: 0013_identity_finalizer_e3
Create Date: 2026-07-27

E4 is database-only groundwork. Production remains pinned to revision 0006a
and ``record_only``. No API, BFF, UI, Compose command, activation helper,
principal binding, parent fact, memory, place, initiative, action, or rollout
change invokes this migration.

The runtime-append-only writer-freeze overlay can describe a separately
enforced legacy SQLite trigger fence and a blocked-write probe without
pretending the earlier point-in-time evidence was a freeze. The existing six
fixed-category privacy receipts and candidate cutover remain evidence only.
The trusted deployment/migration superuser may add one short-lived
content-free admission after offline verification. The separate SECURITY
DEFINER kernel accepts the exact admitted private document bytes and atomically
appends one content-free historical authority promotion.

``authoritative_at_commit`` is deliberately historical. Current authority must
also prove that no later run erasure impact or subject retrieval block exists;
no application read path or current-authority cache is installed here.
"""

from __future__ import annotations

import hashlib
from typing import Sequence

from alembic import op
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.schema import CreateTable


revision: str = "0014_identity_cutover_e4"
down_revision: str | None = "0013_identity_finalizer_e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


FINALIZER_ROLE = "home_agent_identity_cutover"
KERNEL_ROLE = "home_agent_identity_cutover_kernel"
FREEZE = "operations.enforced_legacy_identity_writer_freezes"
ADMISSION = "operations.reviewed_identity_cutover_admissions"
PROMOTION = "operations.semantic_authority_promotions"
FUNCTION = "operations.commit_reviewed_identity_cutover(bytea,uuid)"
E4_SELECT_POLICY = "identity_cutover_e4_select"
E4_INSERT_POLICY = "identity_cutover_e4_insert"
E4_UPDATE_POLICY = "identity_cutover_e4_update"
VERIFIER_BUNDLE_MANIFEST = (
    "home-agent-identity-cutover-verifier-bundle-v1\n"
    "postgresql-jsonb-canonical-text-utf8-v1\n"
)
VERIFIER_BUNDLE_DIGEST = (
    "eae0fd391a19a50a89fab9ee7665760499d7f154bfaf518abe56b8eed2090aa0"
)

E4_METADATA = MetaData(
    naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_name)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)
UUID_PK = UUID(as_uuid=True)


def _uuidv7_shape(column: str) -> str:
    return (
        f"substring({column}::text from 15 for 1) = '7' AND "
        f"substring({column}::text from 20 for 1) IN ('8','9','a','b')"
    )


def _hex64(*columns: str) -> str:
    return " AND ".join(
        f"{column} ~ '^[0-9a-f]{{64}}$'" for column in columns
    )


# Local FK targets let SQLAlchemy compile the three E4 relations without
# registering any future object in ``app.schema.metadata``. They are never
# passed to ``CreateTable``.
Table(
    "legacy_identity_writer_evidence",
    E4_METADATA,
    Column("run_id", UUID_PK),
    Column("evidence_id", UUID_PK),
    schema="operations",
)
Table(
    "reviewed_identity_migration_finalizations",
    E4_METADATA,
    Column("run_id", UUID_PK),
    Column("finalization_id", UUID_PK),
    schema="operations",
)
Table(
    "semantic_authority_cutovers",
    E4_METADATA,
    Column("cutover_id", UUID_PK),
    schema="operations",
)

E4_FREEZE_TABLE = Table(
    "enforced_legacy_identity_writer_freezes",
    E4_METADATA,
    Column("freeze_id", UUID_PK, primary_key=True),
    Column("run_id", UUID_PK, nullable=False, unique=True),
    Column("writer_evidence_id", UUID_PK, nullable=False, unique=True),
    Column("contract_version", String(64), nullable=False),
    Column("enforcement_status", String(32), nullable=False),
    Column("write_probe_result", String(16), nullable=False),
    Column("semantic_write_status", String(16), nullable=False),
    Column("legacy_context_cutoff_status", String(32), nullable=False),
    Column("recognition_mode", String(32), nullable=False),
    Column("source_installation_id", UUID_PK, nullable=False),
    Column("semantic_generation", BigInteger, nullable=False),
    Column("source_projection_commitment", String(64), nullable=False),
    Column("e3_source_manifest_commitment", String(64), nullable=False),
    Column("e3_projection_manifest_commitment", String(64), nullable=False),
    Column("e3_commitment_key_epoch", BigInteger, nullable=False),
    Column("writer_evidence_commitment", String(64), nullable=False),
    Column("trigger_set_commitment", String(64), nullable=False),
    Column("blocked_probe_commitment", String(64), nullable=False),
    Column("release_manifest_digest", String(64), nullable=False),
    Column("freeze_kernel_build_digest", String(64), nullable=False),
    Column("policy_digest", String(64), nullable=False),
    Column("freeze_commitment", String(64), nullable=False, unique=True),
    Column("signature_algorithm", String(16), nullable=False),
    Column("signing_key_fingerprint", String(64), nullable=False),
    Column("freeze_signature", String(128), nullable=False),
    Column("enforced_at", DateTime(timezone=True), nullable=False),
    Column("verified_at", DateTime(timezone=True), nullable=False),
    Column(
        "recorded_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("transaction_timestamp()"),
    ),
    ForeignKeyConstraint(
        ["run_id", "writer_evidence_id"],
        [
            "operations.legacy_identity_writer_evidence.run_id",
            "operations.legacy_identity_writer_evidence.evidence_id",
        ],
        name="fk_enforced_writer_freeze_exact_evidence",
    ),
    UniqueConstraint(
        "run_id", "freeze_id", name="enforced_writer_freeze_identity"
    ),
    CheckConstraint(
        f"{_uuidv7_shape('freeze_id')} AND "
        f"{_uuidv7_shape('source_installation_id')}",
        name="uuidv7_ids",
    ),
    CheckConstraint(
        "contract_version = 'enforced-legacy-identity-writer-freeze-v1' "
        "AND enforcement_status = 'enforced_offline' "
        "AND write_probe_result = 'blocked' "
        "AND semantic_write_status = 'frozen' "
        "AND legacy_context_cutoff_status = 'enforced_cutoff' "
        "AND recognition_mode = 'nonauthoritative_only'",
        name="fixed_enforcement_contract",
    ),
    CheckConstraint(
        _hex64(
            "source_projection_commitment",
            "e3_source_manifest_commitment",
            "e3_projection_manifest_commitment",
            "writer_evidence_commitment",
            "trigger_set_commitment",
            "blocked_probe_commitment",
            "release_manifest_digest",
            "freeze_kernel_build_digest",
            "policy_digest",
            "freeze_commitment",
            "signing_key_fingerprint",
        )
        + " AND freeze_signature ~ '^[0-9a-f]{128}$'",
        name="signed_commitment_shape",
    ),
    CheckConstraint(
        "semantic_generation > 0 "
        "AND e3_commitment_key_epoch > 0 "
        "AND signature_algorithm = 'ed25519' "
        "AND isfinite(enforced_at) "
        "AND isfinite(verified_at) "
        "AND isfinite(recorded_at) "
        "AND enforced_at <= verified_at "
        "AND verified_at <= recorded_at",
        name="signature_generation_and_time",
    ),
    schema="operations",
)

E4_ADMISSION_TABLE = Table(
    "reviewed_identity_cutover_admissions",
    E4_METADATA,
    Column("admission_id", UUID_PK, primary_key=True),
    Column("promotion_id", UUID_PK, nullable=False, unique=True),
    Column("run_id", UUID_PK, nullable=False, unique=True),
    Column("finalization_id", UUID_PK, nullable=False, unique=True),
    Column("candidate_cutover_id", UUID_PK, nullable=False, unique=True),
    Column("writer_freeze_id", UUID_PK, nullable=False, unique=True),
    Column("contract_version", String(64), nullable=False),
    Column("verification_status", String(32), nullable=False),
    Column("document_sha256", String(64), nullable=False, unique=True),
    Column("document_octets", Integer, nullable=False),
    Column("promotion_commitment", String(64), nullable=False, unique=True),
    Column("privacy_check_set_commitment", String(64), nullable=False),
    Column("freeze_commitment", String(64), nullable=False),
    Column("source_installation_id", UUID_PK, nullable=False),
    Column("semantic_generation", BigInteger, nullable=False),
    Column("e3_source_manifest_commitment", String(64), nullable=False),
    Column("e3_projection_manifest_commitment", String(64), nullable=False),
    Column("e3_commitment_key_epoch", BigInteger, nullable=False),
    Column("erasure_ledger_epoch", BigInteger, nullable=False),
    Column("erasure_ledger_head_hash", String(64), nullable=False),
    Column("erasure_ledger_verification", String(24), nullable=False),
    Column("release_manifest_digest", String(64), nullable=False),
    Column("core_schema_digest", String(64), nullable=False),
    Column("core_capability_digest", String(64), nullable=False),
    Column("policy_digest", String(64), nullable=False),
    Column("signing_key_fingerprint", String(64), nullable=False),
    Column("verifier_bundle_digest", String(64), nullable=False),
    Column(
        "admitted_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("transaction_timestamp()"),
    ),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("consumed_at", DateTime(timezone=True)),
    ForeignKeyConstraint(
        ["run_id", "finalization_id"],
        [
            "operations.reviewed_identity_migration_finalizations.run_id",
            "operations.reviewed_identity_migration_finalizations.finalization_id",
        ],
        name="fk_identity_cutover_admission_finalization",
    ),
    ForeignKeyConstraint(
        ["candidate_cutover_id"],
        ["operations.semantic_authority_cutovers.cutover_id"],
        name="fk_identity_cutover_admission_candidate",
    ),
    ForeignKeyConstraint(
        ["writer_freeze_id"],
        ["operations.enforced_legacy_identity_writer_freezes.freeze_id"],
        name="fk_identity_cutover_admission_freeze",
    ),
    CheckConstraint(
        f"{_uuidv7_shape('admission_id')} AND "
        f"{_uuidv7_shape('promotion_id')} AND "
        f"{_uuidv7_shape('source_installation_id')}",
        name="uuidv7_ids",
    ),
    CheckConstraint(
        "contract_version = 'reviewed-identity-cutover-admission-v1' "
        "AND verification_status = 'offline_verified'",
        name="fixed_contract",
    ),
    CheckConstraint(
        "document_octets BETWEEN 2 AND 1048576",
        name="document_size",
    ),
    CheckConstraint(
        _hex64(
            "document_sha256",
            "promotion_commitment",
            "privacy_check_set_commitment",
            "freeze_commitment",
            "e3_source_manifest_commitment",
            "e3_projection_manifest_commitment",
            "erasure_ledger_head_hash",
            "release_manifest_digest",
            "core_schema_digest",
            "core_capability_digest",
            "policy_digest",
            "signing_key_fingerprint",
            "verifier_bundle_digest",
        ),
        name="digest_shape",
    ),
    CheckConstraint(
        "semantic_generation > 0 "
        "AND e3_commitment_key_epoch > 0 "
        "AND erasure_ledger_epoch >= 0 "
        "AND erasure_ledger_verification = 'head_matched'",
        name="source_and_ledger_binding",
    ),
    CheckConstraint(
        "isfinite(admitted_at) AND isfinite(expires_at) "
        "AND expires_at > admitted_at "
        "AND expires_at <= admitted_at + interval '15 minutes' "
        "AND (consumed_at IS NULL "
        "OR (isfinite(consumed_at) "
        "AND consumed_at >= admitted_at "
        "AND consumed_at <= expires_at))",
        name="one_time_window",
    ),
    schema="operations",
)

E4_PROMOTION_TABLE = Table(
    "semantic_authority_promotions",
    E4_METADATA,
    Column("promotion_id", UUID_PK, primary_key=True),
    Column("authority_scope", String(32), nullable=False, unique=True),
    Column("run_id", UUID_PK, nullable=False, unique=True),
    Column("finalization_id", UUID_PK, nullable=False, unique=True),
    Column("candidate_cutover_id", UUID_PK, nullable=False, unique=True),
    Column("writer_freeze_id", UUID_PK, nullable=False, unique=True),
    Column("admission_id", UUID_PK, nullable=False, unique=True),
    Column("contract_version", String(64), nullable=False),
    Column("authority_status", String(40), nullable=False),
    Column("authoritative_at_commit", Boolean, nullable=False),
    Column("privacy_check_set_commitment", String(64), nullable=False),
    Column("freeze_commitment", String(64), nullable=False),
    Column("source_installation_id", UUID_PK, nullable=False),
    Column("semantic_generation", BigInteger, nullable=False),
    Column("e3_source_manifest_commitment", String(64), nullable=False),
    Column("e3_projection_manifest_commitment", String(64), nullable=False),
    Column("e3_commitment_key_epoch", BigInteger, nullable=False),
    Column("erasure_ledger_epoch", BigInteger, nullable=False),
    Column("erasure_ledger_head_hash", String(64), nullable=False),
    Column("erasure_ledger_verification", String(24), nullable=False),
    Column("promotion_commitment", String(64), nullable=False, unique=True),
    Column("release_manifest_digest", String(64), nullable=False),
    Column("core_schema_digest", String(64), nullable=False),
    Column("core_capability_digest", String(64), nullable=False),
    Column("policy_digest", String(64), nullable=False),
    Column("signature_algorithm", String(16), nullable=False),
    Column("signing_key_fingerprint", String(64), nullable=False),
    Column("cutover_signature", String(128), nullable=False),
    Column("database_transaction_id", BigInteger, nullable=False),
    Column(
        "committed_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("transaction_timestamp()"),
    ),
    ForeignKeyConstraint(
        ["run_id", "finalization_id"],
        [
            "operations.reviewed_identity_migration_finalizations.run_id",
            "operations.reviewed_identity_migration_finalizations.finalization_id",
        ],
        name="fk_identity_authority_promotion_finalization",
    ),
    ForeignKeyConstraint(
        ["candidate_cutover_id"],
        ["operations.semantic_authority_cutovers.cutover_id"],
        name="fk_identity_authority_promotion_candidate",
    ),
    ForeignKeyConstraint(
        ["writer_freeze_id"],
        ["operations.enforced_legacy_identity_writer_freezes.freeze_id"],
        name="fk_identity_authority_promotion_freeze",
    ),
    ForeignKeyConstraint(
        ["admission_id"],
        ["operations.reviewed_identity_cutover_admissions.admission_id"],
        name="fk_identity_authority_promotion_admission",
    ),
    CheckConstraint(
        f"{_uuidv7_shape('promotion_id')} AND "
        f"{_uuidv7_shape('source_installation_id')}",
        name="uuidv7_ids",
    ),
    CheckConstraint(
        "authority_scope = 'identity_semantics' "
        "AND contract_version = 'reviewed-identity-semantic-cutover-v1' "
        "AND authority_status = 'historical_authoritative_commit' "
        "AND authoritative_at_commit = true",
        name="fixed_authority_contract",
    ),
    CheckConstraint(
        _hex64(
            "privacy_check_set_commitment",
            "freeze_commitment",
            "e3_source_manifest_commitment",
            "e3_projection_manifest_commitment",
            "erasure_ledger_head_hash",
            "promotion_commitment",
            "release_manifest_digest",
            "core_schema_digest",
            "core_capability_digest",
            "policy_digest",
            "signing_key_fingerprint",
        )
        + " AND cutover_signature ~ '^[0-9a-f]{128}$'",
        name="signed_commitment_shape",
    ),
    CheckConstraint(
        "signature_algorithm = 'ed25519' "
        "AND semantic_generation > 0 "
        "AND e3_commitment_key_epoch > 0 "
        "AND erasure_ledger_epoch >= 0 "
        "AND erasure_ledger_verification = 'head_matched' "
        "AND database_transaction_id > 0 "
        "AND isfinite(committed_at)",
        name="signature_transaction_and_time",
    ),
    schema="operations",
)

FUNCTION_BODY = r"""
DECLARE
  e4_admission operations.reviewed_identity_cutover_admissions%ROWTYPE;
  e4_promotion operations.semantic_authority_promotions%ROWTYPE;
  e3_finalization operations.reviewed_identity_migration_finalizations%ROWTYPE;
  e3_admission operations.reviewed_identity_finalizer_admissions%ROWTYPE;
  e3_run operations.reviewed_identity_migration_runs%ROWTYPE;
  e4_candidate operations.semantic_authority_cutovers%ROWTYPE;
  e4_writer_freeze
    operations.enforced_legacy_identity_writer_freezes%ROWTYPE;
  e4_writer_evidence operations.legacy_identity_writer_evidence%ROWTYPE;
  e4_ledger_state operations.erasure_ledger_state%ROWTYPE;
  e4_document jsonb;
  e4_document_sha256 text;
  e4_current_txid bigint;
  e4_wall_clock_now timestamptz;
  e4_committed_at timestamptz;
  e4_privacy_count integer;
  e4_privacy_category_count integer;
  e4_blocked_subject_count integer;
  e4_affected_rows integer;
  e4_exact_replay boolean;
BEGIN
  IF session_user <> 'home_agent_identity_cutover'
     OR current_user <> 'home_agent_identity_cutover_kernel'
     OR pg_catalog.pg_has_role(
          session_user, 'home_agent_identity_cutover_kernel', 'SET'
        )
     OR pg_catalog.current_setting(
          'log_parameter_max_length_on_error'
        ) <> '0' THEN
    RAISE EXCEPTION 'identity_cutover_role_invalid'
      USING ERRCODE = '42501';
  END IF;
  IF pg_catalog.current_setting('transaction_isolation') <> 'serializable'
     OR pg_catalog.current_setting('transaction_read_only') <> 'off'
     OR pg_catalog.pg_is_in_recovery() THEN
    RAISE EXCEPTION 'identity_cutover_transaction_invalid'
      USING ERRCODE = '25000';
  END IF;

  -- Reject unbounded or structurally impossible input before taking the
  -- shared erasure/finalization fence.
  IF submitted_cutover_document IS NULL
     OR target_admission_id IS NULL
     OR pg_catalog.octet_length(submitted_cutover_document) < 2
     OR pg_catalog.octet_length(submitted_cutover_document) > 1048576
     OR pg_catalog.substring(target_admission_id::text, 15, 1) <> '7'
     OR pg_catalog.substring(target_admission_id::text, 20, 1)
        NOT IN ('8','9','a','b') THEN
    RAISE EXCEPTION 'identity_cutover_input_invalid'
      USING ERRCODE = '22023';
  END IF;

  -- This is the first application-data MVCC operation. It orders every
  -- promotion against E2 subject tombstones and E3 finalization.
  PERFORM privacy.lock_identity_semantic_write_fence();
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(
      'home-agent:identity-semantic-cutover:v1', 0
    )
  );

  SELECT candidate_admission.*
    INTO e4_admission
    FROM operations.reviewed_identity_cutover_admissions
         AS candidate_admission
   WHERE candidate_admission.admission_id = target_admission_id
   FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'identity_cutover_admission_missing'
      USING ERRCODE = '22023';
  END IF;

  IF pg_catalog.octet_length(submitted_cutover_document)
       IS DISTINCT FROM e4_admission.document_octets THEN
    RAISE EXCEPTION 'identity_cutover_document_size_invalid'
      USING ERRCODE = '22023';
  END IF;
  e4_document_sha256 := pg_catalog.encode(
    pg_catalog.sha256(submitted_cutover_document), 'hex'
  );
  IF e4_document_sha256 IS DISTINCT FROM e4_admission.document_sha256 THEN
    RAISE EXCEPTION 'identity_cutover_document_digest_invalid'
      USING ERRCODE = '22023';
  END IF;

  BEGIN
    e4_document :=
      pg_catalog.convert_from(submitted_cutover_document, 'UTF8')::jsonb;
  EXCEPTION
    WHEN character_not_in_repertoire OR untranslatable_character
         OR invalid_text_representation THEN
      RAISE EXCEPTION 'identity_cutover_private_document_rejected'
        USING ERRCODE = '22023';
  END;
  -- Bind one parser and one byte representation to the admitted digest.
  -- jsonb's text form is deterministic for this string-only object; requiring
  -- its exact UTF-8 bytes rejects duplicate keys, alternate whitespace,
  -- reordered keys, and other cross-parser ambiguity before interpretation.
  IF submitted_cutover_document IS DISTINCT FROM pg_catalog.convert_to(
       e4_document::text, 'UTF8'
     ) THEN
    RAISE EXCEPTION 'identity_cutover_document_not_canonical'
      USING ERRCODE = '22023';
  END IF;

  IF pg_catalog.jsonb_typeof(e4_document) IS DISTINCT FROM 'object'
     OR (
       SELECT pg_catalog.count(*)
         FROM pg_catalog.jsonb_object_keys(e4_document)
     ) <> 27
     OR EXISTS (
       SELECT 1
         FROM pg_catalog.jsonb_object_keys(e4_document)
              AS e4_document_key(key_name)
        WHERE e4_document_key.key_name NOT IN (
          'contract_version',
          'admission_id',
          'promotion_id',
          'run_id',
          'finalization_id',
          'candidate_cutover_id',
          'writer_freeze_id',
          'authority_scope',
          'promotion_commitment',
          'privacy_check_set_commitment',
          'freeze_commitment',
          'source_installation_id',
          'semantic_generation',
          'e3_source_manifest_commitment',
          'e3_projection_manifest_commitment',
          'e3_commitment_key_epoch',
          'erasure_ledger_epoch',
          'erasure_ledger_head_hash',
          'erasure_ledger_verification',
          'release_manifest_digest',
          'core_schema_digest',
          'core_capability_digest',
          'policy_digest',
          'signing_key_fingerprint',
          'verifier_bundle_digest',
          'signature_algorithm',
          'cutover_signature'
        )
     )
     OR EXISTS (
       SELECT 1
         FROM pg_catalog.jsonb_each(e4_document)
              AS e4_document_item(key_name, field_value)
        WHERE pg_catalog.jsonb_typeof(e4_document_item.field_value)
              IS DISTINCT FROM 'string'
     ) THEN
    RAISE EXCEPTION 'identity_cutover_document_shape_invalid'
      USING ERRCODE = '22023';
  END IF;

  IF e4_document ->> 'contract_version'
       IS DISTINCT FROM 'reviewed-identity-semantic-cutover-v1'
     OR e4_document ->> 'authority_scope'
       IS DISTINCT FROM 'identity_semantics'
     OR e4_document ->> 'admission_id'
       IS DISTINCT FROM e4_admission.admission_id::text
     OR e4_document ->> 'promotion_id'
       IS DISTINCT FROM e4_admission.promotion_id::text
     OR e4_document ->> 'run_id'
       IS DISTINCT FROM e4_admission.run_id::text
     OR e4_document ->> 'finalization_id'
       IS DISTINCT FROM e4_admission.finalization_id::text
     OR e4_document ->> 'candidate_cutover_id'
       IS DISTINCT FROM e4_admission.candidate_cutover_id::text
     OR e4_document ->> 'writer_freeze_id'
       IS DISTINCT FROM e4_admission.writer_freeze_id::text
     OR e4_document ->> 'promotion_commitment'
       IS DISTINCT FROM e4_admission.promotion_commitment
     OR e4_document ->> 'privacy_check_set_commitment'
       IS DISTINCT FROM e4_admission.privacy_check_set_commitment
     OR e4_document ->> 'freeze_commitment'
       IS DISTINCT FROM e4_admission.freeze_commitment
     OR e4_document ->> 'source_installation_id'
       IS DISTINCT FROM e4_admission.source_installation_id::text
     OR e4_document ->> 'semantic_generation'
       IS DISTINCT FROM e4_admission.semantic_generation::text
     OR e4_document ->> 'e3_source_manifest_commitment'
       IS DISTINCT FROM e4_admission.e3_source_manifest_commitment
     OR e4_document ->> 'e3_projection_manifest_commitment'
       IS DISTINCT FROM e4_admission.e3_projection_manifest_commitment
     OR e4_document ->> 'e3_commitment_key_epoch'
       IS DISTINCT FROM e4_admission.e3_commitment_key_epoch::text
     OR e4_document ->> 'erasure_ledger_epoch'
       IS DISTINCT FROM e4_admission.erasure_ledger_epoch::text
     OR e4_document ->> 'erasure_ledger_head_hash'
       IS DISTINCT FROM e4_admission.erasure_ledger_head_hash
     OR e4_document ->> 'erasure_ledger_verification'
       IS DISTINCT FROM e4_admission.erasure_ledger_verification
     OR e4_document ->> 'release_manifest_digest'
       IS DISTINCT FROM e4_admission.release_manifest_digest
     OR e4_document ->> 'core_schema_digest'
       IS DISTINCT FROM e4_admission.core_schema_digest
     OR e4_document ->> 'core_capability_digest'
       IS DISTINCT FROM e4_admission.core_capability_digest
     OR e4_document ->> 'policy_digest'
       IS DISTINCT FROM e4_admission.policy_digest
     OR e4_document ->> 'signing_key_fingerprint'
       IS DISTINCT FROM e4_admission.signing_key_fingerprint
     OR e4_document ->> 'verifier_bundle_digest'
       IS DISTINCT FROM e4_admission.verifier_bundle_digest
     OR e4_document ->> 'verifier_bundle_digest'
       IS DISTINCT FROM
          'eae0fd391a19a50a89fab9ee7665760499d7f154bfaf518abe56b8eed2090aa0'
     OR e4_document ->> 'signature_algorithm' IS DISTINCT FROM 'ed25519'
     OR e4_document ->> 'cutover_signature' !~ '^[0-9a-f]{128}$' THEN
    RAISE EXCEPTION 'identity_cutover_document_binding_invalid'
      USING ERRCODE = '22023';
  END IF;

  SELECT existing_promotion.*
    INTO e4_promotion
    FROM operations.semantic_authority_promotions AS existing_promotion
   WHERE existing_promotion.admission_id = e4_admission.admission_id;
  e4_exact_replay := FOUND;

  IF e4_exact_replay THEN
    IF e4_admission.consumed_at IS NULL
       OR e4_admission.consumed_at
          IS DISTINCT FROM e4_promotion.committed_at
       OR e4_promotion.promotion_id
          IS DISTINCT FROM e4_admission.promotion_id
       OR e4_promotion.run_id IS DISTINCT FROM e4_admission.run_id
       OR e4_promotion.finalization_id
          IS DISTINCT FROM e4_admission.finalization_id
       OR e4_promotion.candidate_cutover_id
          IS DISTINCT FROM e4_admission.candidate_cutover_id
       OR e4_promotion.writer_freeze_id
          IS DISTINCT FROM e4_admission.writer_freeze_id
       OR e4_promotion.promotion_commitment
          IS DISTINCT FROM e4_admission.promotion_commitment
       OR e4_promotion.privacy_check_set_commitment
          IS DISTINCT FROM e4_admission.privacy_check_set_commitment
       OR e4_promotion.freeze_commitment
          IS DISTINCT FROM e4_admission.freeze_commitment
       OR e4_promotion.source_installation_id
          IS DISTINCT FROM e4_admission.source_installation_id
       OR e4_promotion.semantic_generation
          IS DISTINCT FROM e4_admission.semantic_generation
       OR e4_promotion.e3_source_manifest_commitment
          IS DISTINCT FROM e4_admission.e3_source_manifest_commitment
       OR e4_promotion.e3_projection_manifest_commitment
          IS DISTINCT FROM e4_admission.e3_projection_manifest_commitment
       OR e4_promotion.e3_commitment_key_epoch
          IS DISTINCT FROM e4_admission.e3_commitment_key_epoch
       OR e4_promotion.erasure_ledger_epoch
          IS DISTINCT FROM e4_admission.erasure_ledger_epoch
       OR e4_promotion.erasure_ledger_head_hash
          IS DISTINCT FROM e4_admission.erasure_ledger_head_hash
       OR e4_promotion.erasure_ledger_verification
          IS DISTINCT FROM e4_admission.erasure_ledger_verification
       OR e4_promotion.release_manifest_digest
          IS DISTINCT FROM e4_admission.release_manifest_digest
       OR e4_promotion.core_schema_digest
          IS DISTINCT FROM e4_admission.core_schema_digest
       OR e4_promotion.core_capability_digest
          IS DISTINCT FROM e4_admission.core_capability_digest
       OR e4_promotion.policy_digest
          IS DISTINCT FROM e4_admission.policy_digest
       OR e4_promotion.signing_key_fingerprint
          IS DISTINCT FROM e4_admission.signing_key_fingerprint
       OR e4_promotion.cutover_signature
          IS DISTINCT FROM e4_document ->> 'cutover_signature' THEN
      RAISE EXCEPTION 'identity_cutover_exact_replay_drift'
        USING ERRCODE = '55000';
    END IF;
  ELSE
    IF EXISTS (
         SELECT 1
           FROM operations.semantic_authority_promotions
                AS other_promotion
          WHERE other_promotion.authority_scope = 'identity_semantics'
       ) THEN
      RAISE EXCEPTION 'identity_cutover_authority_already_promoted'
        USING ERRCODE = '23505';
    END IF;
    e4_wall_clock_now := pg_catalog.clock_timestamp();
    IF e4_admission.consumed_at IS NOT NULL
       OR e4_admission.verification_status <> 'offline_verified'
       OR e4_admission.contract_version
          <> 'reviewed-identity-cutover-admission-v1'
       OR e4_admission.admitted_at > e4_wall_clock_now
       OR e4_admission.expires_at < e4_wall_clock_now THEN
      RAISE EXCEPTION 'identity_cutover_admission_unavailable'
        USING ERRCODE = '55000';
    END IF;
  END IF;

  SELECT reviewed_run.*
    INTO e3_run
    FROM operations.reviewed_identity_migration_runs AS reviewed_run
   WHERE reviewed_run.run_id = e4_admission.run_id;
  IF NOT FOUND
     OR e3_run.contract_version <> 'reviewed-identity-migration-run-v1'
     OR e3_run.logical_source_manifest_commitment
        IS DISTINCT FROM e4_admission.e3_source_manifest_commitment
     OR e3_run.projection_manifest_commitment
        IS DISTINCT FROM e4_admission.e3_projection_manifest_commitment
     OR e3_run.commitment_key_epoch
        IS DISTINCT FROM e4_admission.e3_commitment_key_epoch
     OR e3_run.release_manifest_digest
        IS DISTINCT FROM e4_admission.release_manifest_digest
     OR e3_run.core_schema_digest
        IS DISTINCT FROM e4_admission.core_schema_digest
     OR e3_run.core_capability_digest
        IS DISTINCT FROM e4_admission.core_capability_digest
     OR e3_run.policy_digest IS DISTINCT FROM e4_admission.policy_digest
     OR e3_run.created_at > e4_admission.admitted_at THEN
    RAISE EXCEPTION 'identity_cutover_e3_run_invalid'
      USING ERRCODE = '55000';
  END IF;

  SELECT candidate_finalization.*
    INTO e3_finalization
    FROM operations.reviewed_identity_migration_finalizations
         AS candidate_finalization
   WHERE candidate_finalization.run_id = e4_admission.run_id
     AND candidate_finalization.finalization_id = e4_admission.finalization_id;
  IF NOT FOUND
     OR e3_finalization.contract_version
        <> 'reviewed-identity-migration-finalization-v1'
     OR e3_finalization.verification_status <> 'candidate_unverified'
     OR e3_finalization.authoritative
     OR e3_finalization.source_manifest_commitment
        IS DISTINCT FROM e4_admission.e3_source_manifest_commitment
     OR e3_finalization.projection_manifest_commitment
        IS DISTINCT FROM e4_admission.e3_projection_manifest_commitment
     OR e3_finalization.finalized_at > e4_admission.admitted_at THEN
    RAISE EXCEPTION 'identity_cutover_finalization_invalid'
      USING ERRCODE = '55000';
  END IF;

  SELECT consumed_e3_admission.*
    INTO e3_admission
    FROM operations.reviewed_identity_finalizer_admissions
         AS consumed_e3_admission
   WHERE consumed_e3_admission.run_id = e4_admission.run_id
     AND consumed_e3_admission.finalization_id =
         e4_admission.finalization_id;
  IF NOT FOUND
     OR e3_admission.consumed_at IS NULL
     OR e3_admission.consumed_at
        IS DISTINCT FROM e3_finalization.finalized_at
     OR e3_admission.finalization_commitment
        IS DISTINCT FROM e3_finalization.finalization_commitment THEN
    RAISE EXCEPTION 'identity_cutover_e3_finalizer_receipt_invalid'
      USING ERRCODE = '55000';
  END IF;

  SELECT candidate_cutover.*
    INTO e4_candidate
    FROM operations.semantic_authority_cutovers AS candidate_cutover
   WHERE candidate_cutover.cutover_id = e4_admission.candidate_cutover_id
     AND candidate_cutover.run_id = e4_admission.run_id
     AND candidate_cutover.finalization_id = e4_admission.finalization_id;
  IF NOT FOUND
     OR e4_candidate.contract_version
        <> 'semantic-authority-cutover-candidate-v1'
     OR e4_candidate.authority_status <> 'candidate_unenforced'
     OR e4_candidate.authoritative
     OR e4_candidate.required_privacy_check_result <> 'passed'
     OR e4_candidate.required_privacy_residual_code <> 'none'
     OR e4_candidate.privacy_check_set_commitment
        IS DISTINCT FROM e4_admission.privacy_check_set_commitment
     OR e4_candidate.policy_digest IS DISTINCT FROM e4_admission.policy_digest
     OR e4_candidate.attested_at > e4_admission.admitted_at THEN
    RAISE EXCEPTION 'identity_cutover_candidate_invalid'
      USING ERRCODE = '55000';
  END IF;

  SELECT enforced_freeze.*
    INTO e4_writer_freeze
    FROM operations.enforced_legacy_identity_writer_freezes
         AS enforced_freeze
   WHERE enforced_freeze.freeze_id = e4_admission.writer_freeze_id
     AND enforced_freeze.run_id = e4_admission.run_id;
  IF NOT FOUND
     OR e4_writer_freeze.writer_evidence_id
        IS DISTINCT FROM e4_candidate.writer_evidence_id
     OR e4_writer_freeze.contract_version
        <> 'enforced-legacy-identity-writer-freeze-v1'
     OR e4_writer_freeze.enforcement_status <> 'enforced_offline'
     OR e4_writer_freeze.write_probe_result <> 'blocked'
     OR e4_writer_freeze.semantic_write_status <> 'frozen'
     OR e4_writer_freeze.legacy_context_cutoff_status <> 'enforced_cutoff'
     OR e4_writer_freeze.recognition_mode <> 'nonauthoritative_only'
     OR e4_writer_freeze.freeze_commitment
        IS DISTINCT FROM e4_admission.freeze_commitment
     OR e4_writer_freeze.source_installation_id
        IS DISTINCT FROM e4_admission.source_installation_id
     OR e4_writer_freeze.semantic_generation
        IS DISTINCT FROM e4_admission.semantic_generation
     OR e4_writer_freeze.e3_source_manifest_commitment
        IS DISTINCT FROM e4_admission.e3_source_manifest_commitment
     OR e4_writer_freeze.e3_projection_manifest_commitment
        IS DISTINCT FROM e4_admission.e3_projection_manifest_commitment
     OR e4_writer_freeze.e3_commitment_key_epoch
        IS DISTINCT FROM e4_admission.e3_commitment_key_epoch
     OR e4_writer_freeze.source_projection_commitment
        IS DISTINCT FROM e4_admission.e3_projection_manifest_commitment
     OR e4_writer_freeze.policy_digest
        IS DISTINCT FROM e4_admission.policy_digest
     OR e4_writer_freeze.release_manifest_digest
        IS DISTINCT FROM e4_admission.release_manifest_digest
     OR e4_writer_freeze.enforced_at < e3_finalization.finalized_at
     OR e4_writer_freeze.verified_at < e3_finalization.finalized_at
     OR e4_writer_freeze.recorded_at > e4_admission.admitted_at
     OR e4_candidate.attested_at < e4_writer_freeze.verified_at THEN
    RAISE EXCEPTION 'identity_cutover_writer_freeze_invalid'
      USING ERRCODE = '55000';
  END IF;

  SELECT evidence.*
    INTO e4_writer_evidence
    FROM operations.legacy_identity_writer_evidence AS evidence
   WHERE evidence.run_id = e4_writer_freeze.run_id
     AND evidence.evidence_id = e4_writer_freeze.writer_evidence_id;
  IF NOT FOUND
     OR e4_writer_evidence.source_installation_id
        IS DISTINCT FROM e4_writer_freeze.source_installation_id
     OR e4_writer_evidence.semantic_generation
        IS DISTINCT FROM e4_writer_freeze.semantic_generation
     OR e4_writer_evidence.source_projection_commitment
        IS DISTINCT FROM e4_writer_freeze.source_projection_commitment
     OR e4_writer_evidence.evidence_commitment
        IS DISTINCT FROM e4_writer_freeze.writer_evidence_commitment
     OR e4_writer_evidence.integrity_result <> 'passed'
     OR e4_writer_evidence.checkpoint_result <> 'complete'
     OR e4_writer_evidence.journal_result <> 'clean'
     OR e4_writer_evidence.legacy_context_cutoff_status = 'not_observed'
     OR e4_writer_evidence.observed_at > e4_writer_freeze.enforced_at THEN
    RAISE EXCEPTION 'identity_cutover_writer_evidence_invalid'
      USING ERRCODE = '55000';
  END IF;

  SELECT current_ledger.*
    INTO e4_ledger_state
    FROM operations.erasure_ledger_state AS current_ledger
   WHERE current_ledger.state_key = 'current'
   FOR SHARE OF current_ledger;
  IF NOT FOUND
     OR e4_admission.erasure_ledger_verification <> 'head_matched'
     OR e4_ledger_state.recorded_epoch
        IS DISTINCT FROM e4_admission.erasure_ledger_epoch
     OR e4_ledger_state.recorded_head_hash
        IS DISTINCT FROM e4_admission.erasure_ledger_head_hash
     OR (
       NOT e4_exact_replay
       AND e4_ledger_state.updated_at > e4_admission.admitted_at
     ) THEN
    RAISE EXCEPTION 'identity_cutover_erasure_ledger_head_invalid'
      USING ERRCODE = '55000';
  END IF;

  SELECT pg_catalog.count(*),
         pg_catalog.count(DISTINCT privacy_receipt.check_category)
    INTO e4_privacy_count, e4_privacy_category_count
    FROM operations.privacy_cutover_check_receipts AS privacy_receipt
   WHERE privacy_receipt.run_id = e4_admission.run_id
     AND privacy_receipt.finalization_id = e4_admission.finalization_id;
  IF e4_privacy_count <> 6 OR e4_privacy_category_count <> 6 THEN
    RAISE EXCEPTION 'identity_cutover_privacy_set_incomplete'
      USING ERRCODE = '55000';
  END IF;
  IF EXISTS (
    SELECT 1
      FROM (
        VALUES
          ('ingress'::text, e4_candidate.ingress_check_id),
          ('retrieval'::text, e4_candidate.retrieval_check_id),
          ('prompt'::text, e4_candidate.prompt_check_id),
          ('initiative'::text, e4_candidate.initiative_check_id),
          ('export'::text, e4_candidate.export_check_id),
          ('edge_block'::text, e4_candidate.edge_block_check_id)
      ) AS required_check(check_category, check_id)
      LEFT JOIN operations.privacy_cutover_check_receipts AS privacy_receipt
        ON privacy_receipt.run_id = e4_admission.run_id
       AND privacy_receipt.finalization_id = e4_admission.finalization_id
       AND privacy_receipt.check_id = required_check.check_id
       AND privacy_receipt.check_category = required_check.check_category
       AND privacy_receipt.check_result = 'passed'
       AND privacy_receipt.residual_code = 'none'
       AND privacy_receipt.policy_digest = e4_admission.policy_digest
       AND privacy_receipt.checked_at >= e4_writer_freeze.verified_at
       AND privacy_receipt.checked_at <= e4_admission.admitted_at
     WHERE privacy_receipt.check_id IS NULL
  ) THEN
    RAISE EXCEPTION 'identity_cutover_privacy_set_invalid'
      USING ERRCODE = '55000';
  END IF;

  IF EXISTS (
       SELECT 1
         FROM operations.reviewed_identity_migration_erasure_impacts
              AS erasure_impact
        WHERE erasure_impact.run_id = e4_admission.run_id
     ) THEN
    RAISE EXCEPTION 'identity_cutover_erasure_impact_present'
      USING ERRCODE = '55000';
  END IF;
  SELECT pg_catalog.count(*)
    INTO e4_blocked_subject_count
    FROM operations.reviewed_identity_migration_projection_subjects
         AS projection_subject
    JOIN operations.reviewed_identity_migration_projection_lineage
         AS projection_lineage USING (lineage_id)
   WHERE projection_lineage.run_id = e4_admission.run_id
     AND privacy.identity_person_is_blocked(projection_subject.person_id);
  IF e4_blocked_subject_count <> 0 THEN
    RAISE EXCEPTION 'identity_cutover_subject_blocked'
      USING ERRCODE = '55000';
  END IF;

  -- Exact replay revalidates every runtime-append-only source, freeze,
  -- candidate, privacy, ledger-head, and erasure predicate before returning.
  IF e4_exact_replay THEN
    RETURN e4_promotion.promotion_id;
  END IF;

  e4_current_txid := pg_catalog.txid_current();
  e4_committed_at := pg_catalog.transaction_timestamp();
  INSERT INTO operations.semantic_authority_promotions AS inserted_promotion (
    promotion_id,
    authority_scope,
    run_id,
    finalization_id,
    candidate_cutover_id,
    writer_freeze_id,
    admission_id,
    contract_version,
    authority_status,
    authoritative_at_commit,
    privacy_check_set_commitment,
    freeze_commitment,
    source_installation_id,
    semantic_generation,
    e3_source_manifest_commitment,
    e3_projection_manifest_commitment,
    e3_commitment_key_epoch,
    erasure_ledger_epoch,
    erasure_ledger_head_hash,
    erasure_ledger_verification,
    promotion_commitment,
    release_manifest_digest,
    core_schema_digest,
    core_capability_digest,
    policy_digest,
    signature_algorithm,
    signing_key_fingerprint,
    cutover_signature,
    database_transaction_id,
    committed_at
  ) VALUES (
    e4_admission.promotion_id,
    'identity_semantics',
    e4_admission.run_id,
    e4_admission.finalization_id,
    e4_admission.candidate_cutover_id,
    e4_admission.writer_freeze_id,
    e4_admission.admission_id,
    'reviewed-identity-semantic-cutover-v1',
    'historical_authoritative_commit',
    true,
    e4_admission.privacy_check_set_commitment,
    e4_admission.freeze_commitment,
    e4_admission.source_installation_id,
    e4_admission.semantic_generation,
    e4_admission.e3_source_manifest_commitment,
    e4_admission.e3_projection_manifest_commitment,
    e4_admission.e3_commitment_key_epoch,
    e4_admission.erasure_ledger_epoch,
    e4_admission.erasure_ledger_head_hash,
    e4_admission.erasure_ledger_verification,
    e4_admission.promotion_commitment,
    e4_admission.release_manifest_digest,
    e4_admission.core_schema_digest,
    e4_admission.core_capability_digest,
    e4_admission.policy_digest,
    'ed25519',
    e4_admission.signing_key_fingerprint,
    e4_document ->> 'cutover_signature',
    e4_current_txid,
    e4_committed_at
  );
  UPDATE operations.reviewed_identity_cutover_admissions AS target_admission
     SET consumed_at = e4_committed_at
   WHERE target_admission.admission_id = e4_admission.admission_id
     AND target_admission.consumed_at IS NULL;
  GET DIAGNOSTICS e4_affected_rows = ROW_COUNT;
  IF e4_affected_rows <> 1 THEN
    RAISE EXCEPTION 'identity_cutover_admission_consume_conflict'
      USING ERRCODE = '40001';
  END IF;
  RETURN e4_admission.promotion_id;
EXCEPTION
  WHEN character_not_in_repertoire OR untranslatable_character
       OR invalid_text_representation OR invalid_datetime_format
       OR datetime_field_overflow OR numeric_value_out_of_range
       OR string_data_right_truncation THEN
    RAISE EXCEPTION 'identity_cutover_private_document_rejected'
      USING ERRCODE = '22023';
END;
"""

FUNCTION_BODY_SHA256 = hashlib.sha256(FUNCTION_BODY.encode("utf-8")).hexdigest()

NEW_TABLES = (
    E4_FREEZE_TABLE,
    E4_ADMISSION_TABLE,
    E4_PROMOTION_TABLE,
)
READ_EVIDENCE_TABLES = (
    "operations.reviewed_identity_migration_runs",
    "operations.reviewed_identity_migration_finalizations",
    "operations.reviewed_identity_finalizer_admissions",
    "operations.semantic_authority_cutovers",
    "operations.legacy_identity_writer_evidence",
    "operations.privacy_cutover_check_receipts",
    "operations.reviewed_identity_migration_erasure_impacts",
    "operations.reviewed_identity_migration_projection_lineage",
    "operations.reviewed_identity_migration_projection_subjects",
)
ALL_REVOKED_ROLES = (
    "PUBLIC",
    "home_agent_api",
    "home_agent_binding_operator",
    "home_agent_ingest",
    "home_agent_worker",
    "home_agent_erasure",
    "home_agent_rollout",
    "home_agent_backup",
    "home_agent_identity_migration",
    "home_agent_identity_kernel",
    FINALIZER_ROLE,
    KERNEL_ROLE,
)


def _qualified(table) -> str:
    return f'"{table.schema}"."{table.name}"'


def _validate_preconditions() -> None:
    names = ", ".join(f"'{_qualified(table)}'" for table in NEW_TABLES)
    op.execute(
        f"""
        DO $e4_preconditions$
        DECLARE
          owner_oid oid;
          login_oid oid;
          kernel_oid oid;
          e3_kernel_oid oid;
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
          SELECT oid INTO STRICT e3_kernel_oid
            FROM pg_catalog.pg_roles
           WHERE rolname = 'home_agent_identity_finalizer_kernel';
          IF (
               SELECT version_num FROM public.alembic_version
             ) IS DISTINCT FROM '0013_identity_finalizer_e3' THEN
            RAISE EXCEPTION 'identity_cutover_e4_revision_invalid'
              USING ERRCODE = '55000';
          END IF;
          IF pg_catalog.to_regprocedure('{FUNCTION}') IS NOT NULL
             OR EXISTS (
               SELECT 1
                 FROM pg_catalog.unnest(ARRAY[{names}]) AS object_name(name)
                WHERE pg_catalog.to_regclass(object_name.name) IS NOT NULL
             ) THEN
            RAISE EXCEPTION 'identity_cutover_e4_object_occupied'
              USING ERRCODE = '55000';
          END IF;
          IF NOT EXISTS (
            SELECT 1
              FROM pg_catalog.pg_roles AS login_role
             WHERE login_role.oid = login_oid
               AND login_role.rolcanlogin
               AND NOT login_role.rolinherit
               AND NOT login_role.rolsuper
               AND NOT login_role.rolcreatedb
               AND NOT login_role.rolcreaterole
               AND NOT login_role.rolreplication
               AND NOT login_role.rolbypassrls
               AND login_role.rolconnlimit = 1
               AND login_role.rolvaliduntil =
                   timestamptz '1970-01-01 00:00:00+00'
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
            RAISE EXCEPTION 'identity_cutover_e4_dormant_role_invalid'
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
               SELECT 1
                 FROM pg_catalog.pg_db_role_setting
                WHERE setrole IN (login_oid, kernel_oid)
                  AND setdatabase <> 0
             ) THEN
            RAISE EXCEPTION 'identity_cutover_e4_role_config_invalid'
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
                   OR roleid = login_oid
                   OR member = login_oid
             ) THEN
            RAISE EXCEPTION 'identity_cutover_e4_membership_invalid'
              USING ERRCODE = '42501';
          END IF;
          IF EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_shdepend
                WHERE refobjid IN (login_oid, kernel_oid)
                  AND deptype = 'o'
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
                 FROM pg_catalog.pg_proc AS object_row
                 CROSS JOIN LATERAL
                   pg_catalog.aclexplode(object_row.proacl) AS acl
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
            RAISE EXCEPTION 'identity_cutover_e4_pregrant_authority_invalid'
              USING ERRCODE = '42501';
          END IF;
          IF NOT EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_proc AS function_row
                 JOIN pg_catalog.pg_namespace AS namespace_row
                   ON namespace_row.oid = function_row.pronamespace
                WHERE namespace_row.nspname = 'operations'
                  AND function_row.proname =
                      'finalize_reviewed_identity_migration'
                  AND function_row.proowner = e3_kernel_oid
                  AND function_row.prosecdef
             )
             OR NOT pg_catalog.has_function_privilege(
               'home_agent_identity_finalizer',
               'operations.finalize_reviewed_identity_migration(bytea,uuid)',
               'EXECUTE'
             ) THEN
            RAISE EXCEPTION 'identity_cutover_e4_e3_contract_missing'
              USING ERRCODE = '55000';
          END IF;
          IF NOT EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_class AS table_row
                WHERE table_row.oid =
                      'operations.reviewed_identity_migration_runs'::regclass
                  AND table_row.relowner = owner_oid
                  AND table_row.relkind = 'r'
                  AND table_row.relpersistence = 'p'
                  AND table_row.relrowsecurity
                  AND table_row.relforcerowsecurity
             )
             OR NOT EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_class AS table_row
                WHERE table_row.oid =
                      'operations.erasure_ledger_state'::regclass
                  AND table_row.relowner = owner_oid
                  AND table_row.relkind = 'r'
                  AND table_row.relpersistence = 'p'
             ) THEN
            RAISE EXCEPTION 'identity_cutover_e4_evidence_contract_missing'
              USING ERRCODE = '55000';
          END IF;
        END
        $e4_preconditions$
        """
    )


def _create_tables_and_policies() -> None:
    for table in NEW_TABLES:
        op.execute(CreateTable(table))

    revoked = ", ".join(ALL_REVOKED_ROLES)
    for table in NEW_TABLES:
        qualified = _qualified(table)
        op.execute(f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {qualified} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"REVOKE ALL PRIVILEGES ON TABLE {qualified} FROM {revoked}"
        )

    op.execute(
        f"""
        COMMENT ON TABLE {FREEZE} IS
          'Runtime-append-only content-free E4 overlay attesting a separately '
          'enforced legacy semantic-writer trigger fence and blocked-write '
          'probe.';
        COMMENT ON TABLE {ADMISSION} IS
          'Content-free one-time admission for one exact private E4 cutover '
          'document digest; owner insertion is not authority by itself.';
        COMMENT ON TABLE {PROMOTION} IS
          'Single content-free historical identity authority promotion. '
          'Current authority must additionally prove no later erasure impact '
          'or subject retrieval block.';

        CREATE POLICY enforced_writer_freezes_owner_select
          ON {FREEZE} FOR SELECT TO home_agent_owner
          USING (session_user = 'home_agent_owner');
        CREATE POLICY enforced_writer_freezes_owner_insert
          ON {FREEZE} FOR INSERT TO home_agent_owner
          WITH CHECK (session_user = 'home_agent_owner');
        CREATE POLICY reviewed_identity_cutover_admissions_owner_select
          ON {ADMISSION} FOR SELECT TO home_agent_owner
          USING (session_user = 'home_agent_owner');
        CREATE POLICY reviewed_identity_cutover_admissions_owner_insert
          ON {ADMISSION} FOR INSERT TO home_agent_owner
          WITH CHECK (session_user = 'home_agent_owner');
        CREATE POLICY semantic_authority_promotions_owner_select
          ON {PROMOTION} FOR SELECT TO home_agent_owner
          USING (session_user = 'home_agent_owner');

        CREATE POLICY {E4_SELECT_POLICY}
          ON {FREEZE} FOR SELECT TO {KERNEL_ROLE}
          USING (
            session_user = '{FINALIZER_ROLE}'
            AND current_user = '{KERNEL_ROLE}'
            AND NOT pg_catalog.pg_has_role(
              session_user, '{KERNEL_ROLE}', 'SET'
            )
          );
        CREATE POLICY {E4_SELECT_POLICY}
          ON {ADMISSION} FOR SELECT TO {KERNEL_ROLE}
          USING (
            session_user = '{FINALIZER_ROLE}'
            AND current_user = '{KERNEL_ROLE}'
            AND NOT pg_catalog.pg_has_role(
              session_user, '{KERNEL_ROLE}', 'SET'
            )
          );
        CREATE POLICY {E4_UPDATE_POLICY}
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
        CREATE POLICY {E4_SELECT_POLICY}
          ON {PROMOTION} FOR SELECT TO {KERNEL_ROLE}
          USING (
            session_user = '{FINALIZER_ROLE}'
            AND current_user = '{KERNEL_ROLE}'
            AND NOT pg_catalog.pg_has_role(
              session_user, '{KERNEL_ROLE}', 'SET'
            )
          );
        CREATE POLICY {E4_INSERT_POLICY}
          ON {PROMOTION} FOR INSERT TO {KERNEL_ROLE}
          WITH CHECK (
            session_user = '{FINALIZER_ROLE}'
            AND current_user = '{KERNEL_ROLE}'
            AND NOT pg_catalog.pg_has_role(
              session_user, '{KERNEL_ROLE}', 'SET'
            )
          );

        GRANT SELECT, INSERT ON TABLE {FREEZE} TO home_agent_owner;
        GRANT SELECT, INSERT ON TABLE {ADMISSION} TO home_agent_owner;
        GRANT SELECT ON TABLE {PROMOTION} TO home_agent_owner;
        GRANT SELECT ON TABLE {FREEZE}, {PROMOTION} TO {KERNEL_ROLE};
        GRANT SELECT, UPDATE (consumed_at) ON TABLE {ADMISSION}
          TO {KERNEL_ROLE};
        GRANT INSERT ON TABLE {PROMOTION} TO {KERNEL_ROLE};
        """
    )
    predicate = (
        f"session_user = '{FINALIZER_ROLE}' "
        f"AND current_user = '{KERNEL_ROLE}' "
        "AND NOT pg_catalog.pg_has_role("
        f"session_user, '{KERNEL_ROLE}', 'SET')"
    )
    for table in READ_EVIDENCE_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {E4_SELECT_POLICY} ON {table}")
        op.execute(
            f"CREATE POLICY {E4_SELECT_POLICY} ON {table} FOR SELECT "
            f"TO {KERNEL_ROLE} USING ({predicate})"
        )
    op.execute(
        f"""
        GRANT USAGE ON SCHEMA operations TO {FINALIZER_ROLE}, {KERNEL_ROLE};
        GRANT USAGE ON SCHEMA privacy TO {KERNEL_ROLE};
        GRANT SELECT ON TABLE
          operations.reviewed_identity_migration_runs,
          operations.reviewed_identity_migration_finalizations,
          operations.reviewed_identity_finalizer_admissions,
          operations.semantic_authority_cutovers,
          operations.legacy_identity_writer_evidence,
          operations.privacy_cutover_check_receipts,
          operations.reviewed_identity_migration_erasure_impacts,
          operations.reviewed_identity_migration_projection_lineage,
          operations.reviewed_identity_migration_projection_subjects
          TO {KERNEL_ROLE};
        GRANT SELECT, UPDATE (updated_at) ON TABLE
          operations.erasure_ledger_state TO {KERNEL_ROLE};
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
        CREATE FUNCTION operations.commit_reviewed_identity_cutover(
          submitted_cutover_document bytea,
          target_admission_id uuid
        ) RETURNS uuid
        LANGUAGE plpgsql
        VOLATILE
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $e4${FUNCTION_BODY}$e4$;
        REVOKE ALL ON FUNCTION {FUNCTION}
          FROM PUBLIC, home_agent_owner, home_agent_api,
          home_agent_binding_operator, home_agent_ingest, home_agent_worker,
          home_agent_erasure, home_agent_rollout, home_agent_backup,
          home_agent_identity_migration, home_agent_identity_kernel,
          {KERNEL_ROLE}, {FINALIZER_ROLE};
        GRANT EXECUTE ON FUNCTION {FUNCTION} TO {FINALIZER_ROLE};
        RESET ROLE;
        REVOKE CREATE ON SCHEMA operations FROM {KERNEL_ROLE};
        """
    )


def _assert_installed_contract() -> None:
    tables = ", ".join(f"'{_qualified(table)}'::regclass" for table in NEW_TABLES)
    evidence_tables = ", ".join(
        f"'{table}'::regclass" for table in READ_EVIDENCE_TABLES
    )
    op.execute(
        f"""
        DO $e4_contract$
        DECLARE
          owner_oid oid;
          login_oid oid;
          kernel_oid oid;
          function_oid oid;
        BEGIN
          SELECT oid INTO STRICT owner_oid
            FROM pg_catalog.pg_roles
           WHERE rolname = 'home_agent_owner';
          SELECT oid INTO STRICT login_oid
            FROM pg_catalog.pg_roles
           WHERE rolname = '{FINALIZER_ROLE}';
          SELECT oid INTO STRICT kernel_oid
            FROM pg_catalog.pg_roles
           WHERE rolname = '{KERNEL_ROLE}';
          SELECT function_row.oid INTO STRICT function_oid
            FROM pg_catalog.pg_proc AS function_row
           WHERE function_row.oid = '{FUNCTION}'::regprocedure;

          IF EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_class AS table_row
                WHERE table_row.oid IN ({tables})
                  AND (
                    table_row.relowner <> owner_oid
                    OR table_row.relkind <> 'r'
                    OR table_row.relpersistence <> 'p'
                    OR NOT table_row.relrowsecurity
                    OR NOT table_row.relforcerowsecurity
                    OR table_row.relispartition
                  )
             )
             OR (
               SELECT pg_catalog.count(*)
                 FROM pg_catalog.pg_class
                WHERE oid IN ({tables})
             ) <> 3 THEN
            RAISE EXCEPTION 'identity_cutover_e4_table_contract_invalid'
              USING ERRCODE = '55000';
          END IF;
          IF EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_trigger AS trigger_row
                WHERE trigger_row.tgrelid IN ({tables})
                  AND NOT trigger_row.tgisinternal
             )
             OR EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_rewrite AS rule_row
                WHERE rule_row.ev_class IN ({tables})
             ) THEN
            RAISE EXCEPTION
              'identity_cutover_e4_trigger_or_rule_contract_invalid'
              USING ERRCODE = '55000';
          END IF;

          IF NOT EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_proc AS function_row
                WHERE function_row.oid = function_oid
                  AND function_row.proowner = kernel_oid
                  AND function_row.prosecdef
                  AND function_row.provolatile = 'v'
                  AND function_row.prokind = 'f'
                  AND function_row.proconfig =
                      ARRAY['search_path=pg_catalog, pg_temp']::text[]
                  AND pg_catalog.encode(
                        pg_catalog.sha256(
                          pg_catalog.convert_to(function_row.prosrc, 'UTF8')
                        ),
                        'hex'
                      ) = '{FUNCTION_BODY_SHA256}'
             )
             OR NOT pg_catalog.has_function_privilege(
               '{FINALIZER_ROLE}', function_oid, 'EXECUTE'
             )
             OR EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_proc AS function_row
                 CROSS JOIN LATERAL pg_catalog.aclexplode(
                   coalesce(
                     function_row.proacl,
                     pg_catalog.acldefault('f', function_row.proowner)
                   )
                 ) AS acl
                WHERE function_row.oid = function_oid
                  AND acl.grantee = 0
             )
             OR pg_catalog.has_table_privilege(
               '{FINALIZER_ROLE}', '{FREEZE}', 'SELECT'
             )
             OR pg_catalog.has_table_privilege(
               '{FINALIZER_ROLE}', '{ADMISSION}', 'SELECT'
             )
             OR pg_catalog.has_table_privilege(
               '{FINALIZER_ROLE}', '{PROMOTION}', 'INSERT'
             ) THEN
            RAISE EXCEPTION 'identity_cutover_e4_function_contract_invalid'
              USING ERRCODE = '42501';
          END IF;

          IF NOT pg_catalog.has_table_privilege(
               '{KERNEL_ROLE}', '{FREEZE}', 'SELECT'
             )
             OR NOT pg_catalog.has_table_privilege(
               '{KERNEL_ROLE}', '{ADMISSION}', 'SELECT'
             )
             OR NOT pg_catalog.has_column_privilege(
               '{KERNEL_ROLE}', '{ADMISSION}', 'consumed_at', 'UPDATE'
             )
             OR pg_catalog.has_column_privilege(
               '{KERNEL_ROLE}', '{ADMISSION}', 'expires_at', 'UPDATE'
             )
             OR NOT pg_catalog.has_table_privilege(
               '{KERNEL_ROLE}', '{PROMOTION}', 'INSERT'
             )
             OR NOT pg_catalog.has_table_privilege(
               '{KERNEL_ROLE}',
               'operations.reviewed_identity_migration_runs',
               'SELECT'
             )
             OR NOT pg_catalog.has_table_privilege(
               '{KERNEL_ROLE}',
               'operations.erasure_ledger_state',
               'SELECT'
             )
             OR NOT pg_catalog.has_column_privilege(
               '{KERNEL_ROLE}',
               'operations.erasure_ledger_state',
               'updated_at',
               'UPDATE'
             )
             OR pg_catalog.has_column_privilege(
               '{KERNEL_ROLE}',
               'operations.erasure_ledger_state',
               'recorded_epoch',
               'UPDATE'
             )
             OR EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_class AS promotion_table
                 CROSS JOIN LATERAL pg_catalog.aclexplode(
                   coalesce(
                     promotion_table.relacl,
                     pg_catalog.acldefault('r', promotion_table.relowner)
                   )
                 ) AS acl
                WHERE promotion_table.oid = '{PROMOTION}'::regclass
                  AND acl.grantee = 0
             ) THEN
            RAISE EXCEPTION 'identity_cutover_e4_acl_contract_invalid'
              USING ERRCODE = '42501';
          END IF;

          IF (
               SELECT pg_catalog.count(*)
                 FROM pg_catalog.pg_policy AS policy
                WHERE policy.polrelid IN ({tables})
             ) <> 10
             OR EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_policy AS policy
                WHERE policy.polrelid IN ({tables})
                  AND policy.polpermissive IS DISTINCT FROM true
             )
             OR (
               SELECT pg_catalog.count(*)
                 FROM pg_catalog.pg_policy AS policy
                WHERE policy.polrelid IN ({evidence_tables})
                  AND policy.polname = '{E4_SELECT_POLICY}'
                  AND policy.polcmd = 'r'
                  AND policy.polpermissive
                  AND policy.polroles = ARRAY[kernel_oid]::oid[]
             ) <> {len(READ_EVIDENCE_TABLES)}
             OR EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_class AS evidence_table
                WHERE evidence_table.oid IN ({evidence_tables})
                  AND (
                    evidence_table.relowner <> owner_oid
                    OR evidence_table.relkind <> 'r'
                    OR evidence_table.relpersistence <> 'p'
                    OR NOT evidence_table.relrowsecurity
                    OR NOT evidence_table.relforcerowsecurity
                  )
             ) THEN
            RAISE EXCEPTION 'identity_cutover_e4_policy_contract_invalid'
              USING ERRCODE = '55000';
          END IF;
        END
        $e4_contract$
        """
    )


def upgrade() -> None:
    _validate_preconditions()
    _create_tables_and_policies()
    _install_function()
    _assert_installed_contract()


def downgrade() -> None:
    op.execute(
        f"""
        DO $e4_downgrade$
        BEGIN
          IF EXISTS (SELECT 1 FROM {PROMOTION})
             OR EXISTS (SELECT 1 FROM {ADMISSION})
             OR EXISTS (SELECT 1 FROM {FREEZE}) THEN
            RAISE EXCEPTION
              'refusing to remove E4 semantic-cutover evidence'
              USING ERRCODE = '2BP01';
          END IF;
        END
        $e4_downgrade$
        """
    )
    op.execute(
        f"""
        GRANT USAGE ON SCHEMA operations TO {KERNEL_ROLE};
        SET LOCAL ROLE {KERNEL_ROLE};
        REVOKE ALL ON FUNCTION {FUNCTION}
          FROM PUBLIC, home_agent_owner, home_agent_api,
          home_agent_binding_operator, home_agent_ingest, home_agent_worker,
          home_agent_erasure, home_agent_rollout, home_agent_backup,
          home_agent_identity_migration, home_agent_identity_kernel,
          {KERNEL_ROLE}, {FINALIZER_ROLE};
        DROP FUNCTION operations.commit_reviewed_identity_cutover(bytea, uuid);
        RESET ROLE;
        REVOKE CREATE, USAGE ON SCHEMA operations FROM {KERNEL_ROLE};
        """
    )
    for table in READ_EVIDENCE_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {E4_SELECT_POLICY} ON {table}")
    op.execute(
        f"""
        REVOKE SELECT ON TABLE
          operations.reviewed_identity_migration_runs,
          operations.reviewed_identity_migration_finalizations,
          operations.reviewed_identity_finalizer_admissions,
          operations.semantic_authority_cutovers,
          operations.legacy_identity_writer_evidence,
          operations.privacy_cutover_check_receipts,
          operations.reviewed_identity_migration_erasure_impacts,
          operations.reviewed_identity_migration_projection_lineage,
          operations.reviewed_identity_migration_projection_subjects
          FROM {KERNEL_ROLE};
        REVOKE SELECT, UPDATE (updated_at) ON TABLE
          operations.erasure_ledger_state FROM {KERNEL_ROLE};
        REVOKE EXECUTE ON FUNCTION
          privacy.lock_identity_semantic_write_fence(),
          privacy.identity_person_is_blocked(uuid)
          FROM {KERNEL_ROLE};
        REVOKE USAGE ON SCHEMA operations
          FROM {FINALIZER_ROLE}, {KERNEL_ROLE};
        REVOKE USAGE ON SCHEMA privacy FROM {KERNEL_ROLE};
        """
    )
    for table in reversed(NEW_TABLES):
        op.drop_table(table.name, schema=table.schema)
