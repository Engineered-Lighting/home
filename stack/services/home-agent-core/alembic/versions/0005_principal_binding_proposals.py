"""Add staged, subject-confirmed principal binding proposals.

Revision ID: 0005_principal_binding_proposals
Revises: 0004_rollout_authorizations
Create Date: 2026-07-12
"""

from __future__ import annotations

from typing import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "0005_principal_binding_proposals"
down_revision: str | None = "0004_rollout_authorizations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _add_constraint_unless_present(
    table: str, constraint: str, definition: str
) -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1
              FROM pg_constraint
             WHERE conrelid = '{table}'::regclass
               AND conname = '{constraint}'
          ) THEN
            ALTER TABLE {table}
              ADD CONSTRAINT {constraint} {definition};
          END IF;
        END
        $$
        """
    )


def _rewrite_auto_expiry(*, install: bool) -> None:
    """Order binding-request cleanup ahead of confirmation-artifact erasure."""

    connection = op.get_bind()
    definition = connection.execute(
        text(
            "SELECT pg_get_functiondef("
            "'privacy.apply_person_auto_expiry(uuid)'::regprocedure)"
        )
    ).scalar_one()
    original_declarations = "          legacy_source_present boolean := false;"
    hardened_declarations = """          legacy_source_present boolean := false;
          binding_proposals_count integer := 0;
          binding_requests_count integer := 0;"""
    original_cleanup = """          DELETE FROM identity.confirmation_artifacts
           WHERE principal_id = ANY(principal_ids);"""
    hardened_cleanup = """          SELECT count(*) INTO binding_proposals_count
            FROM identity.principal_binding_proposals
           WHERE person_id = target_person_id;
          DELETE FROM identity.ha_user_bindings
           WHERE proposal_id IN (
             SELECT proposal.proposal_id
               FROM identity.principal_binding_proposals proposal
              WHERE proposal.person_id = target_person_id
           );
          DELETE FROM identity.principal_binding_requests
           WHERE request_id IN (
             SELECT proposal.request_id
               FROM identity.principal_binding_proposals proposal
              WHERE proposal.person_id = target_person_id
           );
          GET DIAGNOSTICS binding_requests_count = ROW_COUNT;
          DELETE FROM identity.confirmation_artifacts
           WHERE principal_id = ANY(principal_ids);"""
    original_result = "            ,'spool_entity_ids', to_jsonb(bound_entity_ids)"
    hardened_result = """            ,'binding_proposals', binding_proposals_count
            ,'binding_requests', binding_requests_count
            ,'spool_entity_ids', to_jsonb(bound_entity_ids)"""
    if install:
        if hardened_cleanup in definition:
            return
        replacements = (
            (original_declarations, hardened_declarations),
            (original_cleanup, hardened_cleanup),
            (original_result, hardened_result),
        )
        if any(definition.count(source) != 1 for source, _ in replacements):
            raise RuntimeError("auto-expiry binding cleanup insertion point changed")
        for source, replacement in replacements:
            definition = definition.replace(source, replacement)
    else:
        if hardened_cleanup not in definition:
            return
        replacements = (
            (hardened_declarations, original_declarations),
            (hardened_cleanup, original_cleanup),
            (hardened_result, original_result),
        )
        if any(definition.count(source) != 1 for source, _ in replacements):
            raise RuntimeError("auto-expiry binding cleanup is ambiguous")
        for source, replacement in replacements:
            definition = definition.replace(source, replacement)
    connection.exec_driver_sql(definition)


def upgrade() -> None:
    # Revision 0001 creates current metadata on pristine databases. CREATE IF
    # NOT EXISTS plus conditional ALTERs keep both that path and an upgrade
    # from deployed revision 0004 convergent and replay-safe.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS identity.principal_binding_requests (
          request_id uuid NOT NULL,
          ha_user_id varchar(64) NOT NULL,
          review_code varchar(16) NOT NULL,
          state varchar(32) NOT NULL DEFAULT 'pending',
          requested_at timestamptz NOT NULL DEFAULT timezone('utc', now()),
          expires_at timestamptz NOT NULL,
          staged_at timestamptz,
          closed_at timestamptz,
          CONSTRAINT pk_principal_binding_requests PRIMARY KEY (request_id),
          CONSTRAINT binding_request_user_identity
            UNIQUE (request_id, ha_user_id),
          CONSTRAINT uq_principal_binding_requests_review_code
            UNIQUE (review_code),
          CONSTRAINT ck_principal_binding_requests_binding_request_state CHECK (
            state IN ('pending','staged','consumed','cancelled','expired')
          ),
          CONSTRAINT ck_principal_binding_requests_binding_request_review_code
            CHECK (review_code ~ '^[A-HJ-NP-Z2-9]{16}$'),
          CONSTRAINT ck_principal_binding_requests_binding_request_temporal_order
            CHECK (
              expires_at > requested_at
              AND (
                staged_at IS NULL
                OR (staged_at >= requested_at AND staged_at < expires_at)
              )
              AND (closed_at IS NULL OR closed_at >= requested_at)
            ),
          CONSTRAINT ck_principal_binding_requests_binding_request_state_shape
            CHECK (
              (state = 'pending' AND staged_at IS NULL AND closed_at IS NULL)
              OR (
                state = 'staged'
                AND staged_at IS NOT NULL
                AND closed_at IS NULL
              )
              OR (
                state = 'consumed'
                AND staged_at IS NOT NULL
                AND closed_at IS NOT NULL
                AND closed_at <= expires_at
              )
              OR (state = 'cancelled' AND closed_at IS NOT NULL)
              OR (
                state = 'expired'
                AND closed_at IS NOT NULL
                AND closed_at >= expires_at
              )
            )
        )
        """
    )

    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS
          uq_principal_binding_requests_active_user
        ON identity.principal_binding_requests (ha_user_id)
        WHERE state IN ('pending','staged')
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS identity.principal_binding_proposals (
          proposal_id uuid NOT NULL,
          operator_request_id uuid NOT NULL,
          request_id uuid NOT NULL,
          ha_user_id varchar(64) NOT NULL,
          person_id uuid NOT NULL,
          reviewed_display_label varchar(255) NOT NULL,
          person_snapshot_digest varchar(64) NOT NULL,
          proposal_digest varchar(64) NOT NULL,
          state varchar(32) NOT NULL DEFAULT 'ready',
          stage_receipt_digest varchar(64) NOT NULL,
          staged_at timestamptz NOT NULL,
          expires_at timestamptz NOT NULL,
          consumed_at timestamptz,
          result_principal_id uuid,
          confirmation_artifact_id uuid,
          CONSTRAINT pk_principal_binding_proposals PRIMARY KEY (proposal_id),
          CONSTRAINT uq_principal_binding_proposals_operator_request_id
            UNIQUE (operator_request_id),
          CONSTRAINT uq_principal_binding_proposals_proposal_digest
            UNIQUE (proposal_digest),
          CONSTRAINT binding_proposal_request_once UNIQUE (request_id),
          CONSTRAINT binding_proposal_confirmation_once
            UNIQUE (confirmation_artifact_id),
          CONSTRAINT fk_principal_binding_proposals_request_id_principal_binding_requests
            FOREIGN KEY (request_id)
            REFERENCES identity.principal_binding_requests (request_id)
            ON DELETE CASCADE,
          CONSTRAINT fk_principal_binding_proposals_person_id_people
            FOREIGN KEY (person_id) REFERENCES identity.people (person_id),
          CONSTRAINT fk_principal_binding_proposals_result_principal_id_principals
            FOREIGN KEY (result_principal_id)
            REFERENCES identity.principals (principal_id),
          CONSTRAINT fk_principal_binding_proposals_confirmation_artifact_id_confirmation_artifacts
            FOREIGN KEY (confirmation_artifact_id)
            REFERENCES identity.confirmation_artifacts (artifact_id),
          CONSTRAINT binding_proposal_request_user
            FOREIGN KEY (request_id, ha_user_id)
            REFERENCES identity.principal_binding_requests
              (request_id, ha_user_id)
            ON DELETE CASCADE,
          CONSTRAINT ck_principal_binding_proposals_binding_proposal_digests
            CHECK (
              proposal_digest ~ '^[0-9a-f]{64}$'
              AND person_snapshot_digest ~ '^[0-9a-f]{64}$'
              AND stage_receipt_digest ~ '^[0-9a-f]{64}$'
            ),
          CONSTRAINT ck_principal_binding_proposals_binding_proposal_reviewed_label
            CHECK (btrim(reviewed_display_label) <> ''),
          CONSTRAINT ck_principal_binding_proposals_binding_proposal_state CHECK (
            state IN ('ready','consumed','cancelled','expired')
          ),
          CONSTRAINT ck_principal_binding_proposals_binding_proposal_temporal_order
            CHECK (
              expires_at > staged_at
              AND (
                consumed_at IS NULL
                OR (consumed_at >= staged_at AND consumed_at <= expires_at)
              )
            ),
          CONSTRAINT ck_principal_binding_proposals_binding_proposal_state_shape
            CHECK (
              (
                state = 'ready'
                AND consumed_at IS NULL
                AND result_principal_id IS NULL
                AND confirmation_artifact_id IS NULL
              )
              OR (
                state = 'consumed'
                AND consumed_at IS NOT NULL
                AND result_principal_id IS NOT NULL
                AND confirmation_artifact_id IS NOT NULL
              )
              OR (
                state IN ('cancelled','expired')
                AND consumed_at IS NULL
                AND result_principal_id IS NULL
                AND confirmation_artifact_id IS NULL
              )
            )
        )
        """
    )
    # A pre-0005 binding has no staged proposal provenance. The live rollout
    # was verified empty; never fabricate a proposal or confirmation receipt
    # to make an old row appear governed.
    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1
              FROM information_schema.columns
             WHERE table_schema = 'identity'
               AND table_name = 'ha_user_bindings'
               AND column_name = 'proposal_id'
          ) AND EXISTS (SELECT 1 FROM identity.ha_user_bindings) THEN
            RAISE EXCEPTION
              'pre-0005 principal bindings require explicit operator review'
              USING ERRCODE = '23514';
          END IF;
        END
        $$
        """
    )
    op.execute(
        "ALTER TABLE identity.ha_user_bindings "
        "ADD COLUMN IF NOT EXISTS proposal_id uuid"
    )
    op.execute(
        "ALTER TABLE identity.ha_user_bindings ALTER COLUMN proposal_id SET NOT NULL"
    )
    _add_constraint_unless_present(
        "identity.ha_user_bindings",
        "uq_ha_user_bindings_proposal_id",
        "UNIQUE (proposal_id)",
    )
    _add_constraint_unless_present(
        "identity.ha_user_bindings",
        "fk_ha_user_bindings_proposal_id_principal_binding_proposals",
        "FOREIGN KEY (proposal_id) "
        "REFERENCES identity.principal_binding_proposals (proposal_id) "
        "ON DELETE CASCADE",
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS
          uq_principal_binding_proposals_ready_user
        ON identity.principal_binding_proposals (ha_user_id)
        WHERE state = 'ready'
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS
          uq_principal_binding_proposals_ready_person
        ON identity.principal_binding_proposals (person_id)
        WHERE state = 'ready'
        """
    )

    _add_constraint_unless_present(
        "identity.principals",
        "principal_person_identity",
        "UNIQUE (principal_id, person_id)",
    )
    op.execute(
        "ALTER TABLE identity.ha_user_bindings "
        "DROP CONSTRAINT IF EXISTS uq_ha_user_bindings_ha_user_id"
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_ha_user_bindings_active_ha_user
        ON identity.ha_user_bindings (ha_user_id)
        WHERE revoked_at IS NULL
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_ha_user_bindings_active_person
        ON identity.ha_user_bindings (person_id)
        WHERE revoked_at IS NULL
        """
    )
    _add_constraint_unless_present(
        "identity.ha_user_bindings",
        "ha_binding_principal_person",
        "FOREIGN KEY (principal_id, person_id) "
        "REFERENCES identity.principals (principal_id, person_id)",
    )
    _add_constraint_unless_present(
        "identity.ha_user_bindings",
        "fk_ha_user_bindings_source_artifact_id_confirmation_artifacts",
        "FOREIGN KEY (source_artifact_id) "
        "REFERENCES identity.confirmation_artifacts (artifact_id)",
    )
    _add_constraint_unless_present(
        "identity.ha_user_bindings",
        "ck_ha_user_bindings_ha_binding_self_confirmation",
        "CHECK (confirmed_by_principal_id = principal_id)",
    )
    _add_constraint_unless_present(
        "identity.ha_user_bindings",
        "ck_ha_user_bindings_ha_binding_active_artifact",
        "CHECK (revoked_at IS NOT NULL OR source_artifact_id IS NOT NULL)",
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION identity.validate_principal_binding_graph(
          target_request_id uuid,
          target_proposal_id uuid
        ) RETURNS void
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        BEGIN
          IF EXISTS (
            SELECT 1
              FROM identity.principal_binding_requests request
              LEFT JOIN identity.principal_binding_proposals proposal
                ON proposal.request_id = request.request_id
             WHERE request.request_id = target_request_id
               AND (
                (request.state = 'pending' AND proposal.proposal_id IS NOT NULL)
               OR (
                 request.state = 'staged'
                 AND (proposal.proposal_id IS NULL OR proposal.state <> 'ready')
               )
               OR (
                 request.state = 'consumed'
                 AND (
                   proposal.proposal_id IS NULL
                   OR proposal.state <> 'consumed'
                   OR request.closed_at IS DISTINCT FROM proposal.consumed_at
                 )
               )
               OR (
                 request.state IN ('cancelled','expired')
                 AND proposal.proposal_id IS NOT NULL
                 AND proposal.state <> request.state
               )
               OR (
                 proposal.proposal_id IS NOT NULL
                 AND (
                   proposal.ha_user_id IS DISTINCT FROM request.ha_user_id
                   OR proposal.staged_at IS DISTINCT FROM request.staged_at
                   OR proposal.expires_at IS DISTINCT FROM request.expires_at
                  )
                )
               )
          ) THEN
            RAISE EXCEPTION 'principal binding request/proposal graph is invalid'
              USING ERRCODE = '23514';
          END IF;

          IF EXISTS (
            SELECT 1
              FROM identity.principal_binding_proposals proposal
              LEFT JOIN identity.confirmation_artifacts artifact
                ON artifact.artifact_id = proposal.confirmation_artifact_id
             WHERE proposal.proposal_id = target_proposal_id
               AND proposal.state = 'consumed'
               AND (
                 artifact.artifact_id IS NULL
                 OR artifact.principal_id
                    IS DISTINCT FROM proposal.result_principal_id
                 OR artifact.proposal_digest
                    IS DISTINCT FROM proposal.proposal_digest
                 OR artifact.purpose
                    IS DISTINCT FROM 'ha_user_person_binding.confirm'
                 OR artifact.consumed_at
                    IS DISTINCT FROM proposal.consumed_at
                )
          ) THEN
            RAISE EXCEPTION 'principal binding confirmation artifact is invalid'
              USING ERRCODE = '23514';
          END IF;

          IF EXISTS (
            SELECT 1
             FROM identity.ha_user_bindings binding
              LEFT JOIN identity.principal_binding_proposals proposal
                ON proposal.proposal_id = binding.proposal_id
             WHERE binding.proposal_id = target_proposal_id
               AND (
                 proposal.proposal_id IS NULL
                 OR proposal.state <> 'consumed'
                 OR proposal.ha_user_id IS DISTINCT FROM binding.ha_user_id
                 OR proposal.person_id IS DISTINCT FROM binding.person_id
                 OR proposal.result_principal_id
                    IS DISTINCT FROM binding.principal_id
                 OR proposal.confirmation_artifact_id
                    IS DISTINCT FROM binding.source_artifact_id
                 OR proposal.consumed_at
                    IS DISTINCT FROM binding.confirmed_at
               )
          ) OR EXISTS (
            SELECT 1
              FROM identity.principal_binding_proposals proposal
              LEFT JOIN identity.ha_user_bindings binding
                ON binding.proposal_id = proposal.proposal_id
             WHERE proposal.proposal_id = target_proposal_id
               AND (
                (proposal.state = 'consumed' AND binding.binding_id IS NULL)
               OR (
                 proposal.state <> 'consumed'
                  AND binding.binding_id IS NOT NULL
                )
               )
          ) THEN
            RAISE EXCEPTION 'principal binding proposal/result graph is invalid'
              USING ERRCODE = '23514';
          END IF;

          RETURN;
        END;
        $$
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION "
        "identity.validate_principal_binding_graph(uuid,uuid) FROM PUBLIC"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION identity.validate_principal_binding_graph_trigger()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        DECLARE
          new_row jsonb;
          old_row jsonb;
          target_request_id uuid;
          target_proposal_id uuid;
          old_request_id uuid;
          old_proposal_id uuid;
          artifact_id uuid;
          old_artifact_id uuid;
        BEGIN
          IF TG_OP <> 'DELETE' THEN
            new_row := to_jsonb(NEW);
          END IF;
          IF TG_OP <> 'INSERT' THEN
            old_row := to_jsonb(OLD);
          END IF;

          IF TG_TABLE_NAME = 'principal_binding_requests' THEN
            target_request_id := (new_row->>'request_id')::uuid;
            old_request_id := (old_row->>'request_id')::uuid;
          ELSIF TG_TABLE_NAME = 'principal_binding_proposals' THEN
            target_request_id := (new_row->>'request_id')::uuid;
            target_proposal_id := (new_row->>'proposal_id')::uuid;
            old_request_id := (old_row->>'request_id')::uuid;
            old_proposal_id := (old_row->>'proposal_id')::uuid;
          ELSIF TG_TABLE_NAME = 'ha_user_bindings' THEN
            target_proposal_id := (new_row->>'proposal_id')::uuid;
            old_proposal_id := (old_row->>'proposal_id')::uuid;
          ELSIF TG_TABLE_NAME = 'confirmation_artifacts' THEN
            artifact_id := (new_row->>'artifact_id')::uuid;
            old_artifact_id := (old_row->>'artifact_id')::uuid;
            SELECT proposal.request_id, proposal.proposal_id
              INTO target_request_id, target_proposal_id
              FROM identity.principal_binding_proposals proposal
             WHERE proposal.confirmation_artifact_id = artifact_id;
            IF target_proposal_id IS NULL THEN
              SELECT proposal.request_id, proposal.proposal_id
                INTO old_request_id, old_proposal_id
                FROM identity.principal_binding_proposals proposal
               WHERE proposal.confirmation_artifact_id = old_artifact_id;
            END IF;
          END IF;

          IF target_request_id IS NULL AND target_proposal_id IS NOT NULL THEN
            SELECT proposal.request_id INTO target_request_id
              FROM identity.principal_binding_proposals proposal
             WHERE proposal.proposal_id = target_proposal_id;
          END IF;
          IF old_request_id IS NULL AND old_proposal_id IS NOT NULL THEN
            SELECT proposal.request_id INTO old_request_id
              FROM identity.principal_binding_proposals proposal
             WHERE proposal.proposal_id = old_proposal_id;
          END IF;

          IF target_request_id IS NOT NULL OR target_proposal_id IS NOT NULL THEN
            PERFORM identity.validate_principal_binding_graph(
              target_request_id, target_proposal_id
            );
          END IF;
          IF (old_request_id, old_proposal_id) IS DISTINCT FROM
             (target_request_id, target_proposal_id)
             AND (old_request_id IS NOT NULL OR old_proposal_id IS NOT NULL) THEN
            PERFORM identity.validate_principal_binding_graph(
              old_request_id, old_proposal_id
            );
          END IF;
          RETURN NULL;
        END;
        $$
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION "
        "identity.validate_principal_binding_graph_trigger() FROM PUBLIC"
    )
    for table in (
        "principal_binding_requests",
        "principal_binding_proposals",
        "ha_user_bindings",
        "confirmation_artifacts",
    ):
        trigger = f"validate_principal_binding_graph_{table}"
        op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON identity.{table}")
        op.execute(
            f"CREATE CONSTRAINT TRIGGER {trigger} "
            f"AFTER INSERT OR UPDATE OR DELETE ON identity.{table} "
            "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
            "EXECUTE FUNCTION identity.validate_principal_binding_graph_trigger()"
        )

    _rewrite_auto_expiry(install=True)

    # The HA subject scope is transaction-local. Operator authority is tied to
    # the authenticated PostgreSQL login role: a caller-controlled custom GUC
    # must never be able to widen it. home_agent_owner is admitted only because
    # it owns migrations and the disposable integration-test database. The
    # maintenance clauses are reachable only while an explicitly granted
    # caller executes an owner-owned SECURITY DEFINER function; direct
    # runtime-role statements retain their ordinary current_user and do not
    # satisfy them. API is included only for the denial-only cancellation
    # function below, never for binding/artifact mutation.
    binding_work_maintenance_expression = (
        "(current_user = 'home_agent_owner' "
        "AND session_user IN "
        "('home_agent_api','home_agent_worker','home_agent_erasure'))"
    )
    expiry_maintenance_expression = (
        "(current_user = 'home_agent_owner' "
        "AND session_user IN ('home_agent_worker','home_agent_erasure'))"
    )
    for table in (
        "identity.principal_binding_requests",
        "identity.principal_binding_proposals",
    ):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON TABLE {table} FROM PUBLIC")
        policy = table.removeprefix("identity.") + "_subject_or_operator"
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
        expression = (
            "ha_user_id = nullif(current_setting('app.ha_user_id', true), '') "
            "OR session_user IN "
            "('home_agent_binding_operator','home_agent_owner') "
            f"OR {binding_work_maintenance_expression}"
        )
        op.execute(
            f"CREATE POLICY {policy} ON {table} "
            f"USING ({expression}) WITH CHECK ({expression})"
        )

    # Binding rows are scoped to either the authenticated HA user or the
    # resulting principal. The ingest role needs read-only projection access,
    # while operator review needs read-only collision checks; ACLs below the
    # deployment layer prevent both roles from mutating this authority table.
    op.execute("ALTER TABLE identity.ha_user_bindings ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE identity.ha_user_bindings FORCE ROW LEVEL SECURITY")
    op.execute("REVOKE ALL ON TABLE identity.ha_user_bindings FROM PUBLIC")
    op.execute(
        "DROP POLICY IF EXISTS ha_user_bindings_subject_or_operator "
        "ON identity.ha_user_bindings"
    )
    binding_expression = (
        "ha_user_id = nullif(current_setting('app.ha_user_id', true), '') "
        "OR principal_id = "
        "nullif(current_setting('app.principal_id', true), '')::uuid "
        "OR session_user IN "
        "('home_agent_binding_operator','home_agent_owner') "
        f"OR {expiry_maintenance_expression}"
    )
    op.execute(
        "CREATE POLICY ha_user_bindings_subject_or_operator "
        "ON identity.ha_user_bindings "
        f"USING ({binding_expression}) WITH CHECK ({binding_expression})"
    )
    op.execute(
        "DROP POLICY IF EXISTS ha_user_bindings_ingest_read "
        "ON identity.ha_user_bindings"
    )
    op.execute(
        "CREATE POLICY ha_user_bindings_ingest_read "
        "ON identity.ha_user_bindings FOR SELECT "
        "USING (session_user = 'home_agent_ingest')"
    )

    # confirmation_artifacts already has the principal policy from revision
    # 0001. Force it now and add only the owner/SECURITY-DEFINER maintenance
    # path required by governed expiry. The binding operator receives no ACL
    # on this table and is intentionally absent from this policy.
    op.execute(
        "ALTER TABLE identity.confirmation_artifacts "
        "ENABLE ROW LEVEL SECURITY"
    )
    op.execute(
        "ALTER TABLE identity.confirmation_artifacts "
        "FORCE ROW LEVEL SECURITY"
    )
    op.execute("REVOKE ALL ON TABLE identity.confirmation_artifacts FROM PUBLIC")
    op.execute(
        "DROP POLICY IF EXISTS confirmation_artifacts_trusted_maintenance "
        "ON identity.confirmation_artifacts"
    )
    confirmation_maintenance_expression = (
        "session_user = 'home_agent_owner' "
        f"OR {expiry_maintenance_expression}"
    )
    op.execute(
        "CREATE POLICY confirmation_artifacts_trusted_maintenance "
        "ON identity.confirmation_artifacts "
        f"USING ({confirmation_maintenance_expression}) "
        f"WITH CHECK ({confirmation_maintenance_expression})"
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION privacy.expire_principal_binding_work(
          operation_time timestamptz
        ) RETURNS jsonb
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $$
        DECLARE
          proposals_expired integer := 0;
          staged_requests_expired integer := 0;
          pending_requests_expired integer := 0;
          proposals_purged integer := 0;
          requests_purged integer := 0;
        BEGIN
          IF operation_time IS NULL OR operation_time > clock_timestamp() THEN
            RAISE EXCEPTION 'invalid principal binding expiry cutoff'
              USING ERRCODE = '22023';
          END IF;
          UPDATE identity.principal_binding_proposals proposal
             SET state = 'expired'
            FROM identity.principal_binding_requests request
           WHERE request.request_id = proposal.request_id
             AND request.state = 'staged'
             AND proposal.state = 'ready'
             AND proposal.expires_at <= operation_time
             AND request.expires_at <= operation_time;
          GET DIAGNOSTICS proposals_expired = ROW_COUNT;

          UPDATE identity.principal_binding_requests request
             SET state = 'expired', closed_at = operation_time
            FROM identity.principal_binding_proposals proposal
           WHERE proposal.request_id = request.request_id
             AND request.state = 'staged'
             AND proposal.state = 'expired'
             AND request.expires_at <= operation_time;
          GET DIAGNOSTICS staged_requests_expired = ROW_COUNT;

          UPDATE identity.principal_binding_requests
             SET state = 'expired', closed_at = operation_time
           WHERE state = 'pending' AND expires_at <= operation_time;
          GET DIAGNOSTICS pending_requests_expired = ROW_COUNT;

          SELECT count(*) INTO proposals_purged
            FROM identity.principal_binding_proposals proposal
            JOIN identity.principal_binding_requests request
              ON request.request_id = proposal.request_id
           WHERE proposal.state IN ('cancelled','expired')
             AND request.state IN ('cancelled','expired')
             AND request.closed_at <= operation_time - interval '7 days';
          DELETE FROM identity.principal_binding_requests
           WHERE state IN ('cancelled','expired')
             AND closed_at <= operation_time - interval '7 days';
          GET DIAGNOSTICS requests_purged = ROW_COUNT;

          RETURN jsonb_build_object(
            'proposals_expired', proposals_expired,
            'staged_requests_expired', staged_requests_expired,
            'pending_requests_expired', pending_requests_expired,
            'requests_expired',
              staged_requests_expired + pending_requests_expired,
            'proposals_purged', proposals_purged,
            'requests_purged', requests_purged
          );
        END;
        $$
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION "
        "privacy.expire_principal_binding_work(timestamptz) FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION "
        "privacy.expire_principal_binding_work(timestamptz) "
        "TO home_agent_worker, home_agent_erasure"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION
          privacy.cancel_principal_binding_work_for_person(
            target_person_id uuid,
            operation_time timestamptz
          ) RETURNS jsonb
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $$
        DECLARE
          work record;
          locked_proposal_ids uuid[] := ARRAY[]::uuid[];
          locked_request_ids uuid[] := ARRAY[]::uuid[];
          proposals_cancelled integer := 0;
          requests_cancelled integer := 0;
        BEGIN
          IF target_person_id IS NULL
             OR operation_time IS NULL
             OR operation_time > clock_timestamp() THEN
            RAISE EXCEPTION 'invalid principal binding cancellation input'
              USING ERRCODE = '22023';
          END IF;

          -- Lock each ready proposal before its request in a stable order. The
          -- function has no create/consume path: its only possible effect is
          -- to remove a staged proposal from consideration.
          FOR work IN
            SELECT proposal.proposal_id, request.request_id
              FROM identity.principal_binding_proposals proposal
              JOIN identity.principal_binding_requests request
                ON request.request_id = proposal.request_id
             WHERE proposal.person_id = target_person_id
               AND proposal.state = 'ready'
               AND request.state = 'staged'
               AND request.requested_at <= operation_time
             ORDER BY proposal.proposal_id, request.request_id
          LOOP
            PERFORM 1
              FROM identity.principal_binding_proposals proposal
             WHERE proposal.proposal_id = work.proposal_id
             FOR UPDATE;
            PERFORM 1
              FROM identity.principal_binding_requests request
             WHERE request.request_id = work.request_id
             FOR UPDATE;
            locked_proposal_ids := array_append(
              locked_proposal_ids, work.proposal_id
            );
            locked_request_ids := array_append(
              locked_request_ids, work.request_id
            );
          END LOOP;

          UPDATE identity.principal_binding_proposals proposal
             SET state = 'cancelled'
           WHERE proposal.proposal_id = ANY(locked_proposal_ids)
             AND proposal.person_id = target_person_id
             AND proposal.state = 'ready'
             AND EXISTS (
               SELECT 1
                 FROM identity.principal_binding_requests request
                WHERE request.request_id = proposal.request_id
                  AND request.state = 'staged'
                  AND request.requested_at <= operation_time
             );
          GET DIAGNOSTICS proposals_cancelled = ROW_COUNT;

          UPDATE identity.principal_binding_requests request
             SET state = 'cancelled', closed_at = operation_time
            FROM identity.principal_binding_proposals proposal
           WHERE proposal.request_id = request.request_id
             AND request.request_id = ANY(locked_request_ids)
             AND proposal.person_id = target_person_id
             AND proposal.state = 'cancelled'
             AND request.state = 'staged'
             AND request.requested_at <= operation_time;
          GET DIAGNOSTICS requests_cancelled = ROW_COUNT;

          IF proposals_cancelled <> requests_cancelled THEN
            RAISE EXCEPTION 'principal binding cancellation graph changed'
              USING ERRCODE = '40001';
          END IF;

          RETURN jsonb_build_object(
            'proposals_cancelled', proposals_cancelled,
            'requests_cancelled', requests_cancelled
          );
        END;
        $$
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION "
        "privacy.cancel_principal_binding_work_for_person(uuid,timestamptz) "
        "FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION "
        "privacy.cancel_principal_binding_work_for_person(uuid,timestamptz) "
        "TO home_agent_api, home_agent_erasure"
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM identity.principal_binding_requests)
             OR EXISTS (SELECT 1 FROM identity.principal_binding_proposals)
             OR EXISTS (SELECT 1 FROM identity.ha_user_bindings) THEN
            RAISE EXCEPTION
              'refusing to downgrade governed principal binding authority'
              USING ERRCODE = '2BP01';
          END IF;
        END
        $$
        """
    )
    op.execute(
        "REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA "
        "ingest, identity, knowledge, engagement, privacy, operations "
        "FROM home_agent_binding_operator"
    )
    op.execute(
        "REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA "
        "ingest, identity, knowledge, engagement, privacy, operations "
        "FROM home_agent_binding_operator"
    )
    op.execute(
        "REVOKE USAGE ON SCHEMA ingest, identity, knowledge, engagement, "
        "privacy, operations FROM home_agent_binding_operator"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "privacy.cancel_principal_binding_work_for_person(uuid,timestamptz)"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS " "privacy.expire_principal_binding_work(timestamptz)"
    )
    _rewrite_auto_expiry(install=False)
    op.execute(
        "DROP POLICY IF EXISTS confirmation_artifacts_trusted_maintenance "
        "ON identity.confirmation_artifacts"
    )
    op.execute(
        "ALTER TABLE identity.confirmation_artifacts NO FORCE ROW LEVEL SECURITY"
    )
    op.execute(
        "DROP POLICY IF EXISTS ha_user_bindings_ingest_read "
        "ON identity.ha_user_bindings"
    )
    op.execute(
        "DROP POLICY IF EXISTS ha_user_bindings_subject_or_operator "
        "ON identity.ha_user_bindings"
    )
    op.execute(
        "ALTER TABLE identity.ha_user_bindings NO FORCE ROW LEVEL SECURITY"
    )
    op.execute("ALTER TABLE identity.ha_user_bindings DISABLE ROW LEVEL SECURITY")
    for table in (
        "principal_binding_requests",
        "principal_binding_proposals",
        "ha_user_bindings",
        "confirmation_artifacts",
    ):
        trigger = f"validate_principal_binding_graph_{table}"
        op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON identity.{table}")
    op.execute(
        "DROP FUNCTION IF EXISTS " "identity.validate_principal_binding_graph_trigger()"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "identity.validate_principal_binding_graph(uuid,uuid)"
    )
    op.execute("DROP TABLE IF EXISTS identity.principal_binding_proposals CASCADE")
    op.execute("DROP TABLE IF EXISTS identity.principal_binding_requests CASCADE")

    op.execute("DROP INDEX IF EXISTS identity.uq_ha_user_bindings_active_person")
    op.execute("DROP INDEX IF EXISTS identity.uq_ha_user_bindings_active_ha_user")
    op.execute(
        "ALTER TABLE identity.ha_user_bindings "
        "DROP CONSTRAINT IF EXISTS "
        "fk_ha_user_bindings_proposal_id_principal_binding_proposals"
    )
    op.execute(
        "ALTER TABLE identity.ha_user_bindings "
        "DROP CONSTRAINT IF EXISTS uq_ha_user_bindings_proposal_id"
    )
    op.execute(
        "ALTER TABLE identity.ha_user_bindings " "DROP COLUMN IF EXISTS proposal_id"
    )
    op.execute(
        "ALTER TABLE identity.ha_user_bindings "
        "DROP CONSTRAINT IF EXISTS ck_ha_user_bindings_ha_binding_active_artifact"
    )
    op.execute(
        "ALTER TABLE identity.ha_user_bindings "
        "DROP CONSTRAINT IF EXISTS ck_ha_user_bindings_ha_binding_self_confirmation"
    )
    op.execute(
        "ALTER TABLE identity.ha_user_bindings "
        "DROP CONSTRAINT IF EXISTS "
        "fk_ha_user_bindings_source_artifact_id_confirmation_artifacts"
    )
    op.execute(
        "ALTER TABLE identity.ha_user_bindings "
        "DROP CONSTRAINT IF EXISTS ha_binding_principal_person"
    )
    _add_constraint_unless_present(
        "identity.ha_user_bindings",
        "uq_ha_user_bindings_ha_user_id",
        "UNIQUE (ha_user_id)",
    )
    op.execute(
        "ALTER TABLE identity.principals "
        "DROP CONSTRAINT IF EXISTS principal_person_identity"
    )
