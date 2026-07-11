"""Add reviewed People/privacy cutover records and expiry receipts.

Revision ID: 0002_people_privacy_cutover
Revises: 0001_greenfield_core
Create Date: 2026-07-11
"""

from __future__ import annotations

from typing import Sequence

from alembic import op

from app import schema


revision: str = "0002_people_privacy_cutover"
down_revision: str | None = "0001_greenfield_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _add_column(table: str, definition: str) -> None:
    op.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {definition}")


def upgrade() -> None:
    _add_column("identity.people", "status_source_ref varchar(255)")
    _add_column("identity.people", "status_source_version integer")
    _add_column("identity.people", "status_source_sha256 varchar(64)")

    _add_column(
        "identity.aliases",
        "alias_kind varchar(32) NOT NULL DEFAULT 'nickname'",
    )
    _add_column("identity.aliases", "source_ref varchar(255)")
    _add_column("identity.aliases", "source_version integer")
    _add_column("identity.aliases", "source_snapshot_sha256 varchar(64)")

    _add_column("identity.privacy_directives", "source_ref varchar(255)")
    _add_column("identity.privacy_directives", "source_version integer")
    _add_column("identity.privacy_directives", "source_snapshot_sha256 varchar(64)")

    # A collision across people is ambiguity, never a first-match lookup.
    # Migration deliberately fails here if legacy review has not resolved it.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_identity_alias_normalized_global "
        "ON identity.aliases (normalized_alias)"
    )

    bind = op.get_bind()
    schema.external_recognition_bindings.create(bind=bind, checkfirst=True)
    schema.edge_privacy_blocks.create(bind=bind, checkfirst=True)
    schema.edge_privacy_user_blocks.create(bind=bind, checkfirst=True)
    schema.legacy_relationship_candidates.create(bind=bind, checkfirst=True)
    schema.auto_expiry_schedules.create(bind=bind, checkfirst=True)
    schema.auto_expiry_receipts.create(bind=bind, checkfirst=True)
    _add_column(
        "privacy.auto_expiry_receipts",
        "residual_codes varchar(64)[] NOT NULL DEFAULT '{}'::varchar[]",
    )
    _add_column(
        "privacy.auto_expiry_receipts",
        "ledger_outbox_id uuid",
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_auto_expiry_receipt_ledger_outbox "
        "ON privacy.auto_expiry_receipts (ledger_outbox_id)"
    )

    op.execute(
        """
        DO $$ BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
             WHERE conrelid = 'identity.people'::regclass
               AND conname = 'ck_people_person_status_source_sha256'
          ) THEN
            ALTER TABLE identity.people
              ADD CONSTRAINT ck_people_person_status_source_sha256 CHECK (
                status_source_sha256 IS NULL OR
                status_source_sha256 ~ '^[0-9a-f]{64}$'
              );
          END IF;
        END $$;

        DO $$ BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
             WHERE conrelid = 'identity.aliases'::regclass
               AND conname = 'ck_aliases_alias_kind'
          ) THEN
            ALTER TABLE identity.aliases
              ADD CONSTRAINT ck_aliases_alias_kind
              CHECK (alias_kind IN ('name','nickname'));
          END IF;
        END $$;

        DO $$ BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
             WHERE conrelid = 'identity.privacy_directives'::regclass
               AND conname = 'ck_privacy_directives_privacy_expiry_shape'
          ) THEN
            ALTER TABLE identity.privacy_directives
              ADD CONSTRAINT ck_privacy_directives_privacy_expiry_shape CHECK (
                (directive = 'auto_expire' AND expires_at IS NOT NULL) OR
                (directive <> 'auto_expire' AND expires_at IS NULL)
              );
          END IF;
        END $$;
        """
    )
    # Exact SECURITY DEFINER erasure kernel: the worker receives EXECUTE on
    # this one function, never BYPASSRLS or broad identity/knowledge grants.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION privacy.apply_person_auto_expiry(
          target_schedule_id uuid
        ) RETURNS jsonb
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $$
        DECLARE
          target_person_id uuid;
          operation_time timestamptz := clock_timestamp();
          principal_ids uuid[];
          transaction_ids uuid[];
          fact_version_ids uuid[];
          fact_ids uuid[];
          artifact_ids uuid[];
          bound_entity_ids text[];
          bound_user_ids text[];
          place_ids uuid[];
          initiative_ids uuid[];
          recognition_count integer := 0;
          aliases_count integer := 0;
          source_bindings_count integer := 0;
          initiatives_count integer := 0;
          facts_count integer := 0;
          places_count integer := 0;
          place_locators_count integer := 0;
          lineage_count integer := 0;
          legacy_source_present boolean := false;
        BEGIN
          SELECT s.person_id
            INTO target_person_id
            FROM privacy.auto_expiry_schedules s
            JOIN identity.privacy_directives d
              ON d.directive_id = s.directive_id
             AND d.person_id = s.person_id
           WHERE s.schedule_id = target_schedule_id
             AND s.state = 'scheduled'
             AND s.due_at <= operation_time
             AND d.directive = 'auto_expire'
             AND d.enabled
             AND d.expires_at <= operation_time
           FOR UPDATE OF s, d;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'auto-expiry authorization missing' USING ERRCODE = '42501';
          END IF;
          SELECT legacy_source_ref IS NOT NULL INTO legacy_source_present
            FROM identity.people WHERE person_id = target_person_id;
          UPDATE privacy.auto_expiry_schedules
             SET state = 'running'
           WHERE schedule_id = target_schedule_id;
          PERFORM 1 FROM identity.people
           WHERE person_id = target_person_id FOR UPDATE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'auto-expiry person missing' USING ERRCODE = 'P0002';
          END IF;

          SELECT coalesce(array_agg(principal_id), '{}'::uuid[])
            INTO principal_ids
            FROM identity.principals WHERE person_id = target_person_id;
          SELECT coalesce(array_agg(entity_id), '{}'::text[])
            INTO bound_entity_ids
            FROM identity.source_entity_bindings
           WHERE person_id = target_person_id;
          SELECT coalesce(array_agg(ha_user_id), '{}'::text[])
            INTO bound_user_ids
            FROM identity.ha_user_bindings
           WHERE person_id = target_person_id;
          SELECT coalesce(array_agg(place_id), '{}'::uuid[])
            INTO place_ids
            FROM knowledge.places
           WHERE owner_principal_id = ANY(principal_ids);
          INSERT INTO identity.edge_privacy_blocks(
            block_id, source_system, entity_id, reason_code, person_id
          )
          SELECT binding_id, source_system, entity_id, 'auto_expired', person_id
            FROM identity.source_entity_bindings
           WHERE person_id = target_person_id
          ON CONFLICT (source_system, entity_id) DO UPDATE
            SET reason_code = 'auto_expired', person_id = excluded.person_id;
          INSERT INTO identity.edge_privacy_user_blocks(
            block_id, ha_user_id, reason_code, person_id
          )
          SELECT binding_id, ha_user_id, 'auto_expired', person_id
            FROM identity.ha_user_bindings
           WHERE person_id = target_person_id
          ON CONFLICT (ha_user_id) DO UPDATE
            SET reason_code = 'auto_expired', person_id = excluded.person_id;
          SELECT coalesce(array_agg(DISTINCT transaction_id), '{}'::uuid[])
            INTO transaction_ids
            FROM (
              SELECT memory_transaction_id AS transaction_id
                FROM knowledge.fact_versions
               WHERE subject_id = target_person_id
                  OR subject_id = ANY(place_ids)
                  OR object->>'person_id' = target_person_id::text
                  OR EXISTS (
                       SELECT 1 FROM unnest(place_ids) erased_place(place_id)
                        WHERE fact_versions.object->>'place_id' = erased_place.place_id::text
                     )
                  OR object @> jsonb_build_object(
                       'resolution_at_commit', jsonb_build_array(target_person_id::text)
                     )
                  OR perspective_principal_id = ANY(principal_ids)
              UNION
              SELECT transaction_id
                FROM knowledge.memory_transactions
               WHERE principal_id = ANY(principal_ids)
                  OR candidate @> jsonb_build_object(
                       'resolved_parent_person_ids',
                       jsonb_build_array(target_person_id::text)
                     )
                  OR preview @> jsonb_build_object(
                       'resolved_parent_person_ids',
                       jsonb_build_array(target_person_id::text)
                     )
            ) affected_transactions;
          SELECT coalesce(array_agg(fact_version_id), '{}'::uuid[])
            INTO fact_version_ids
            FROM knowledge.fact_versions
           WHERE memory_transaction_id = ANY(transaction_ids)
              OR subject_id = target_person_id
              OR subject_id = ANY(place_ids)
              OR object->>'person_id' = target_person_id::text
              OR EXISTS (
                   SELECT 1 FROM unnest(place_ids) erased_place(place_id)
                    WHERE fact_versions.object->>'place_id' = erased_place.place_id::text
                 )
              OR object @> jsonb_build_object(
                   'resolution_at_commit', jsonb_build_array(target_person_id::text)
                 )
              OR perspective_principal_id = ANY(principal_ids);
          SELECT coalesce(array_agg(DISTINCT fact_id), '{}'::uuid[])
            INTO fact_ids
            FROM knowledge.fact_versions
           WHERE fact_version_id = ANY(fact_version_ids);
          WITH RECURSIVE affected_artifacts(artifact_id) AS (
            SELECT artifact_id FROM knowledge.fact_support
             WHERE fact_version_id = ANY(fact_version_ids)
            UNION SELECT unnest(transaction_ids)
            UNION SELECT unnest(fact_ids)
            UNION SELECT artifact_id FROM identity.confirmation_artifacts
             WHERE principal_id = ANY(principal_ids)
            UNION SELECT artifact_id FROM privacy.artifact_registry
             WHERE owner_principal_id = ANY(principal_ids)
            UNION
            SELECT derived.artifact_id
              FROM affected_artifacts affected
              CROSS JOIN LATERAL (
                SELECT link.child_artifact_id AS artifact_id
                  FROM ingest.artifact_links link
                 WHERE link.parent_artifact_id = affected.artifact_id
                UNION
                SELECT version.fact_id
                  FROM knowledge.fact_support support
                  JOIN knowledge.fact_versions version
                    ON version.fact_version_id = support.fact_version_id
                 WHERE support.artifact_id = affected.artifact_id
                UNION
                SELECT version.memory_transaction_id
                  FROM knowledge.fact_support support
                  JOIN knowledge.fact_versions version
                    ON version.fact_version_id = support.fact_version_id
                 WHERE support.artifact_id = affected.artifact_id
              ) derived
          )
          SELECT coalesce(array_agg(DISTINCT artifact_id), '{}'::uuid[])
            INTO artifact_ids FROM affected_artifacts;
          SELECT coalesce(array_agg(DISTINCT fact_version_id), '{}'::uuid[])
            INTO fact_version_ids
            FROM knowledge.fact_versions
           WHERE fact_version_id = ANY(fact_version_ids)
              OR fact_id = ANY(artifact_ids)
              OR memory_transaction_id = ANY(artifact_ids)
              OR EXISTS (
                   SELECT 1 FROM knowledge.fact_support support
                    WHERE support.fact_version_id = fact_versions.fact_version_id
                      AND support.artifact_id = ANY(artifact_ids)
                 );
          SELECT coalesce(array_agg(DISTINCT memory_transaction_id), '{}'::uuid[])
            INTO transaction_ids
            FROM knowledge.fact_versions
           WHERE fact_version_id = ANY(fact_version_ids);
          SELECT coalesce(array_agg(DISTINCT fact_id), '{}'::uuid[])
            INTO fact_ids
            FROM knowledge.fact_versions
           WHERE fact_version_id = ANY(fact_version_ids);
          SELECT coalesce(array_agg(initiative_id), '{}'::uuid[])
            INTO initiative_ids
            FROM engagement.initiatives
           WHERE principal_id = ANY(principal_ids)
              OR source_fact_version_id = ANY(fact_version_ids);

          DELETE FROM engagement.presentation_attempts
           WHERE initiative_id = ANY(initiative_ids);
          DELETE FROM engagement.initiatives
           WHERE initiative_id = ANY(initiative_ids);
          GET DIAGNOSTICS initiatives_count = ROW_COUNT;
          DELETE FROM knowledge.fact_support
           WHERE fact_version_id = ANY(fact_version_ids);
          DELETE FROM ingest.artifact_links
           WHERE parent_artifact_id = ANY(artifact_ids)
              OR child_artifact_id = ANY(artifact_ids);
          GET DIAGNOSTICS lineage_count = ROW_COUNT;
          DELETE FROM privacy.artifact_registry
           WHERE artifact_id = ANY(artifact_ids);
          DELETE FROM knowledge.fact_versions
           WHERE fact_version_id = ANY(fact_version_ids);
          GET DIAGNOSTICS facts_count = ROW_COUNT;
          DELETE FROM knowledge.memory_transactions
           WHERE transaction_id = ANY(transaction_ids);
          DELETE FROM engagement.preferences
           WHERE principal_id = ANY(principal_ids);
          DELETE FROM knowledge.visits
           WHERE principal_id = ANY(principal_ids);
          UPDATE knowledge.visits SET place_id = NULL
           WHERE place_id = ANY(place_ids);
          DELETE FROM knowledge.place_locators
           WHERE place_id = ANY(place_ids);
          GET DIAGNOSTICS place_locators_count = ROW_COUNT;
          UPDATE knowledge.places SET parent_place_id = NULL
           WHERE parent_place_id = ANY(place_ids)
             AND NOT (place_id = ANY(place_ids));
          DELETE FROM knowledge.places WHERE place_id = ANY(place_ids);
          GET DIAGNOSTICS places_count = ROW_COUNT;
          DELETE FROM identity.confirmation_artifacts
           WHERE principal_id = ANY(principal_ids);
          DELETE FROM privacy.artifact_registry
           WHERE owner_principal_id = ANY(principal_ids);

          SELECT count(*) INTO recognition_count
            FROM identity.external_recognition_bindings
           WHERE person_id = target_person_id;
          DELETE FROM identity.external_recognition_bindings
           WHERE person_id = target_person_id;
          DELETE FROM identity.aliases WHERE person_id = target_person_id;
          GET DIAGNOSTICS aliases_count = ROW_COUNT;
          DELETE FROM identity.legacy_role_labels WHERE person_id = target_person_id;
          DELETE FROM identity.legacy_relationship_candidates
           WHERE from_person_id = target_person_id OR to_person_id = target_person_id;
          DELETE FROM identity.source_entity_bindings WHERE person_id = target_person_id;
          GET DIAGNOSTICS source_bindings_count = ROW_COUNT;
          INSERT INTO ingest.coverage_intervals(
            coverage_id, stream_id, entity_id, interval, status, reason_code,
            source_sequence_start, source_sequence_end
          )
          SELECT envelope_id, stream_id, NULL,
                 tstzrange(source_observed_at,
                   greatest(source_observed_at + interval '1 microsecond', edge_received_at),
                   '[)'),
                 'gap', 'privacy_auto_expiry_runtime_purge', sequence, sequence
            FROM ingest.envelopes
           WHERE entity_id = ANY(bound_entity_ids)
              OR metadata->>'derived_from_entity' = ANY(bound_entity_ids)
              OR ha_context->>'user_id' = ANY(bound_user_ids)
          ON CONFLICT (coverage_id) DO NOTHING;
          WITH identity_linked_envelopes AS (
            SELECT envelope_id, gen_random_uuid() AS replacement_root_id
              FROM ingest.envelopes
             WHERE entity_id = ANY(bound_entity_ids)
                OR metadata->>'derived_from_entity' = ANY(bound_entity_ids)
                OR ha_context->>'user_id' = ANY(bound_user_ids)
          )
          UPDATE ingest.envelopes AS envelope
             SET entity_id = NULL,
                  source_event_id = NULL,
                  root_observation_id = erased.replacement_root_id,
                  evidence_family_id = erased.replacement_root_id,
                  ha_context = '{}'::jsonb,
                 metadata = jsonb_build_object(
                   'raw_payload_status', 'erased_auto_expiry'
                 ),
                 dependency_domain = 'suppressed.identity_erased'
            FROM identity_linked_envelopes AS erased
           WHERE envelope.envelope_id = erased.envelope_id;
          UPDATE ingest.coverage_intervals SET entity_id = NULL
           WHERE entity_id = ANY(bound_entity_ids);
          DELETE FROM identity.ha_user_bindings WHERE person_id = target_person_id;
          DELETE FROM identity.privacy_directives
           WHERE person_id = target_person_id
             AND directive <> 'auto_expire';
          UPDATE identity.privacy_directives SET enabled = false
           WHERE person_id = target_person_id AND directive = 'auto_expire';
          UPDATE identity.principals
             SET status = 'revoked', display_label = 'Erased person'
           WHERE principal_id = ANY(principal_ids);
          UPDATE identity.people
             SET display_name = 'Erased person', pronouns = NULL,
                 status = 'erased', legacy_source_ref = NULL,
                 legacy_source_version = NULL, legacy_source_sha256 = NULL,
                 status_source_ref = NULL, status_source_version = NULL,
                 status_source_sha256 = NULL, updated_at = operation_time
           WHERE person_id = target_person_id;

          RETURN jsonb_build_object(
            'people_scrubbed', 1,
            'aliases', aliases_count,
            'recognition_bindings', recognition_count,
            'source_entity_bindings', source_bindings_count,
            'ha_user_bindings', cardinality(bound_user_ids),
            'initiatives', initiatives_count,
            'fact_versions', facts_count,
            'places', places_count,
            'place_locators', place_locators_count,
            'artifact_lineage_links', lineage_count
            ,'spool_entity_ids', to_jsonb(bound_entity_ids)
            ,'spool_user_ids', to_jsonb(bound_user_ids)
            ,'legacy_source_untracked', legacy_source_present
          );
        END;
        $$;
        REVOKE ALL ON FUNCTION privacy.apply_person_auto_expiry(uuid)
          FROM PUBLIC;

        CREATE OR REPLACE FUNCTION privacy.replay_person_auto_expiry(
          target_schedule_id uuid,
          target_person_id uuid,
          ledger_outbox_id uuid
        ) RETURNS jsonb
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $$
        DECLARE
          directive_id_value uuid;
          current_state text;
          current_person uuid;
          operation_time timestamptz := clock_timestamp();
        BEGIN
          PERFORM 1 FROM identity.people
           WHERE person_id = target_person_id FOR UPDATE;
          IF NOT FOUND THEN
            RETURN jsonb_build_object('already_absent', 1);
          END IF;

          SELECT person_id, state
            INTO current_person, current_state
            FROM privacy.auto_expiry_schedules
           WHERE schedule_id = target_schedule_id
           FOR UPDATE;
          IF FOUND THEN
            IF current_person <> target_person_id THEN
              RAISE EXCEPTION 'replay schedule person conflict' USING ERRCODE = '23514';
            END IF;
            IF current_state = 'scheduled' THEN
              RETURN privacy.apply_person_auto_expiry(target_schedule_id);
            END IF;
            IF current_state IN ('running','ledger_pending','complete') AND EXISTS (
              SELECT 1 FROM identity.people
               WHERE person_id = target_person_id AND status = 'erased'
            ) THEN
              RETURN jsonb_build_object('already_erased', 1);
            END IF;
            RAISE EXCEPTION 'replay schedule state conflict' USING ERRCODE = '23514';
          END IF;

          SELECT directive_id INTO directive_id_value
            FROM identity.privacy_directives
           WHERE person_id = target_person_id AND directive = 'auto_expire'
           FOR UPDATE;
          IF NOT FOUND THEN
            directive_id_value := ledger_outbox_id;
            INSERT INTO identity.privacy_directives(
              directive_id, person_id, directive, enabled, expires_at,
              source_ref, source_version, source_snapshot_sha256
            ) VALUES (
              directive_id_value, target_person_id, 'auto_expire', true,
              operation_time - interval '1 microsecond',
              'independent-erasure-ledger-replay', 0, repeat('0', 64)
            );
          ELSE
            UPDATE identity.privacy_directives
               SET enabled = true,
                   expires_at = least(expires_at, operation_time - interval '1 microsecond')
             WHERE directive_id = directive_id_value;
          END IF;
          INSERT INTO privacy.auto_expiry_schedules(
            schedule_id, person_id, directive_id, outbox_id, due_at, state
          ) VALUES (
            target_schedule_id, target_person_id, directive_id_value,
            ledger_outbox_id, operation_time - interval '1 microsecond', 'scheduled'
          );
          RETURN privacy.apply_person_auto_expiry(target_schedule_id);
        END;
        $$;
        REVOKE ALL ON FUNCTION privacy.replay_person_auto_expiry(uuid,uuid,uuid)
          FROM PUBLIC;
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP FUNCTION IF EXISTS privacy.replay_person_auto_expiry(uuid,uuid,uuid)"
    )
    op.execute("DROP FUNCTION IF EXISTS privacy.apply_person_auto_expiry(uuid)")
    schema.auto_expiry_receipts.drop(bind=op.get_bind(), checkfirst=True)
    schema.auto_expiry_schedules.drop(bind=op.get_bind(), checkfirst=True)
    schema.external_recognition_bindings.drop(bind=op.get_bind(), checkfirst=True)
    schema.edge_privacy_blocks.drop(bind=op.get_bind(), checkfirst=True)
    schema.edge_privacy_user_blocks.drop(bind=op.get_bind(), checkfirst=True)
    schema.legacy_relationship_candidates.drop(bind=op.get_bind(), checkfirst=True)
    op.execute("DROP INDEX IF EXISTS identity.uq_identity_alias_normalized_global")
    for table, column in (
        ("identity.privacy_directives", "source_snapshot_sha256"),
        ("identity.privacy_directives", "source_version"),
        ("identity.privacy_directives", "source_ref"),
        ("identity.aliases", "source_snapshot_sha256"),
        ("identity.aliases", "source_version"),
        ("identity.aliases", "source_ref"),
        ("identity.aliases", "alias_kind"),
        ("identity.people", "status_source_sha256"),
        ("identity.people", "status_source_version"),
        ("identity.people", "status_source_ref"),
    ):
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS {column}")
