from __future__ import annotations

from dataclasses import dataclass

import pytest
from sqlalchemy import create_engine, text

from .e1_postgres_harness import (
    REVISION_0011,
    alembic_upgrade,
    assert_failed_upgrade_is_atomic,
    assert_revision,
    direct_acl_count,
    exception_sqlstate,
    exception_text,
    run_apply_grants,
)
from .e1_postgres_harness import _e1_0010_database as _e1_fixture  # noqa: F401


@dataclass(frozen=True)
class AdmissionTamper:
    name: str
    sql: str
    error: str


TAMPERS = (
    AdmissionTamper(
        "required_constraint_not_valid",
        """
        ALTER TABLE identity.principals
          DROP CONSTRAINT ck_principals_principal_kind;
        ALTER TABLE identity.principals
          ADD CONSTRAINT ck_principals_principal_kind
          CHECK (kind IN ('ha_user','voice_unknown','service')) NOT VALID;
        """,
        "identity_erasure_e1_support_constraints_invalid",
    ),
    AdmissionTamper(
        "required_constraint_definition_weakened",
        """
        ALTER TABLE identity.confirmation_artifacts
          DROP CONSTRAINT confirmation_nonce_once;
        ALTER TABLE identity.confirmation_artifacts
          ADD CONSTRAINT confirmation_nonce_once UNIQUE (artifact_id, principal_id);
        """,
        "identity_erasure_e1_support_definition_invalid",
    ),
    AdmissionTamper(
        "required_constraint_name_on_wrong_relation",
        """
        ALTER TABLE privacy.erasure_requests
          DROP CONSTRAINT ck_erasure_requests_erasure_state;
        ALTER TABLE identity.people
          ADD CONSTRAINT ck_erasure_requests_erasure_state CHECK (status IS NOT NULL);
        """,
        "identity_erasure_e1_support_constraints_invalid",
    ),
    AdmissionTamper(
        "operation_shape_extra_column",
        """
        ALTER TABLE operations.reviewed_identity_migration_erasure_operations
          ADD COLUMN unreviewed_note text;
        """,
        "identity_erasure_e1_predecessor_shape_invalid",
    ),
    AdmissionTamper(
        "impact_shape_missing_constraint",
        """
        ALTER TABLE operations.reviewed_identity_migration_erasure_impacts
          DROP CONSTRAINT
            ck_reviewed_identity_migration_erasure_impacts_impact_caps;
        """,
        "identity_erasure_e1_predecessor_shape_invalid",
    ),
    AdmissionTamper(
        "support_policy_extra",
        """
        CREATE POLICY e1_unreviewed_erasure_request_read
          ON privacy.erasure_requests FOR SELECT USING (true);
        """,
        "identity_erasure_e1_support_policies_invalid",
    ),
    AdmissionTamper(
        "support_policy_expression_changed",
        """
        DROP POLICY privacy_erasure_requests_principal
          ON privacy.erasure_requests;
        CREATE POLICY privacy_erasure_requests_principal
          ON privacy.erasure_requests USING (true) WITH CHECK (true);
        """,
        "identity_erasure_e1_support_policy_shape_invalid",
    ),
    AdmissionTamper(
        "support_policy_made_restrictive",
        """
        DROP POLICY confirmation_artifacts_trusted_maintenance
          ON identity.confirmation_artifacts;
        CREATE POLICY confirmation_artifacts_trusted_maintenance
          ON identity.confirmation_artifacts AS RESTRICTIVE
          USING (
            session_user = 'home_agent_owner'
            OR (
              current_user = 'home_agent_owner'
              AND session_user IN ('home_agent_worker','home_agent_erasure')
            )
          )
          WITH CHECK (
            session_user = 'home_agent_owner'
            OR (
              current_user = 'home_agent_owner'
              AND session_user IN ('home_agent_worker','home_agent_erasure')
            )
          );
        """,
        "identity_erasure_e1_support_policy_shape_invalid",
    ),
    AdmissionTamper(
        "predecessor_policy_expression_changed",
        """
        DROP POLICY reviewed_identity_migration_erasure_operations_owner_select
          ON operations.reviewed_identity_migration_erasure_operations;
        CREATE POLICY reviewed_identity_migration_erasure_operations_owner_select
          ON operations.reviewed_identity_migration_erasure_operations
          FOR SELECT TO home_agent_owner USING (true);
        """,
        "identity_erasure_e1_predecessor_shape_invalid",
    ),
    AdmissionTamper(
        "support_rls_disabled",
        "ALTER TABLE privacy.erasure_requests DISABLE ROW LEVEL SECURITY;",
        "identity_erasure_e1_support_boundary_invalid",
    ),
    AdmissionTamper(
        "binding_graph_validator_body_changed",
        """
        CREATE OR REPLACE FUNCTION identity.validate_principal_binding_graph(
          target_request_id uuid, target_proposal_id uuid
        )
        RETURNS void LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog AS $$ BEGIN RETURN; END; $$;
        """,
        "identity_erasure_e1_binding_graph_invalid",
    ),
    AdmissionTamper(
        "binding_graph_validator_security_invoker",
        """
        ALTER FUNCTION identity.validate_principal_binding_graph(uuid, uuid)
          SECURITY INVOKER;
        """,
        "identity_erasure_e1_binding_graph_invalid",
    ),
    AdmissionTamper(
        "binding_graph_validator_public_execute",
        """
        GRANT EXECUTE ON FUNCTION
          identity.validate_principal_binding_graph(uuid, uuid) TO PUBLIC;
        """,
        "identity_erasure_e1_binding_graph_invalid",
    ),
    AdmissionTamper(
        "binding_graph_trigger_function_body_changed",
        """
        CREATE OR REPLACE FUNCTION identity.validate_principal_binding_graph_trigger()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog AS $$ BEGIN RETURN NULL; END; $$;
        """,
        "identity_erasure_e1_binding_graph_invalid",
    ),
    AdmissionTamper(
        "replay_guard_body_changed",
        """
        CREATE OR REPLACE FUNCTION
          operations.bump_reviewed_identity_migration_replay_guard()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp AS $$ BEGIN RETURN NEW; END; $$;
        """,
        "identity_erasure_e1_identity_kernel_function_invalid",
    ),
    AdmissionTamper(
        "replay_guard_security_invoker",
        """
        ALTER FUNCTION operations.bump_reviewed_identity_migration_replay_guard()
          SECURITY INVOKER;
        """,
        "identity_erasure_e1_identity_kernel_function_invalid",
    ),
    AdmissionTamper(
        "replay_guard_public_execute",
        """
        GRANT EXECUTE ON FUNCTION
          operations.bump_reviewed_identity_migration_replay_guard() TO PUBLIC;
        """,
        "identity_erasure_e1_replay_guard_invalid",
    ),
    AdmissionTamper(
        "unreviewed_owner_security_definer_public_execute",
        """
        CREATE FUNCTION identity.e1_unreviewed_authority_bypass()
        RETURNS void LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog AS $$
        BEGIN
          UPDATE identity.confirmation_artifacts
             SET consumed_at = consumed_at;
        END;
        $$;
        """,
        "identity_erasure_e1_unreviewed_security_definer_authority",
    ),
    AdmissionTamper(
        "unreviewed_owner_security_definer_api_execute",
        """
        CREATE FUNCTION identity.e1_unreviewed_api_authority_bypass()
        RETURNS void LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog AS $$ BEGIN RETURN; END; $$;
        REVOKE ALL ON FUNCTION
          identity.e1_unreviewed_api_authority_bypass() FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION
          identity.e1_unreviewed_api_authority_bypass() TO home_agent_api;
        """,
        "identity_erasure_e1_unreviewed_security_definer_authority",
    ),
    AdmissionTamper(
        "unreviewed_owner_security_definer_worker_execute",
        """
        CREATE FUNCTION identity.e1_unreviewed_worker_authority_bypass()
        RETURNS void LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog AS $$ BEGIN RETURN; END; $$;
        REVOKE ALL ON FUNCTION
          identity.e1_unreviewed_worker_authority_bypass() FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION
          identity.e1_unreviewed_worker_authority_bypass()
          TO home_agent_worker;
        """,
        "identity_erasure_e1_unreviewed_security_definer_authority",
    ),
    AdmissionTamper(
        "rogue_schema_owner_security_definer_worker_execute",
        """
        CREATE SCHEMA e1_rogue_authority AUTHORIZATION home_agent_owner;
        CREATE FUNCTION e1_rogue_authority.worker_bypass()
        RETURNS void LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog AS $$ BEGIN RETURN; END; $$;
        REVOKE ALL ON FUNCTION e1_rogue_authority.worker_bypass() FROM PUBLIC;
        GRANT USAGE ON SCHEMA e1_rogue_authority TO home_agent_worker;
        GRANT EXECUTE ON FUNCTION e1_rogue_authority.worker_bypass()
          TO home_agent_worker;
        """,
        "identity_erasure_e1_unreviewed_security_definer_authority",
    ),
    AdmissionTamper(
        "predefined_role_owned_security_definer",
        """
        CREATE SCHEMA e1_predefined_authority
          AUTHORIZATION pg_write_all_data;
        CREATE FUNCTION e1_predefined_authority.api_bypass()
        RETURNS void LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog AS $$ BEGIN RETURN; END; $$;
        ALTER FUNCTION e1_predefined_authority.api_bypass()
          OWNER TO pg_write_all_data;
        REVOKE ALL ON FUNCTION e1_predefined_authority.api_bypass() FROM PUBLIC;
        GRANT USAGE ON SCHEMA e1_predefined_authority TO home_agent_api;
        GRANT EXECUTE ON FUNCTION e1_predefined_authority.api_bypass()
          TO home_agent_api;
        """,
        "identity_erasure_e1_security_definer_owner_invalid",
    ),
    AdmissionTamper(
        "reviewed_security_definer_body_changed",
        """
        CREATE OR REPLACE FUNCTION operations.stop_worker_maintenance(
          target_worker_instance_id uuid
        ) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp AS $$
        BEGIN
          RETURN false;
        END;
        $$;
        """,
        "identity_erasure_e1_security_definer_contract_invalid",
    ),
    AdmissionTamper(
        "reviewed_security_definer_catalog_flags_changed",
        """
        ALTER FUNCTION operations.stop_worker_maintenance(uuid)
          STABLE;
        """,
        "identity_erasure_e1_security_definer_contract_invalid",
    ),
    AdmissionTamper(
        "reviewed_security_definer_acl_missing",
        """
        REVOKE EXECUTE ON FUNCTION
          operations.stop_worker_maintenance(uuid) FROM home_agent_worker;
        """,
        "identity_erasure_e1_unreviewed_security_definer_authority",
    ),
    AdmissionTamper(
        "reviewed_security_definer_acl_grant_option",
        """
        GRANT EXECUTE ON FUNCTION
          operations.stop_worker_maintenance(uuid)
          TO home_agent_worker WITH GRANT OPTION;
        """,
        "identity_erasure_e1_unreviewed_security_definer_authority",
    ),
    AdmissionTamper(
        "binding_graph_trigger_disabled",
        """
        ALTER TABLE identity.principal_binding_requests DISABLE TRIGGER
          validate_principal_binding_graph_principal_binding_requests;
        """,
        "identity_erasure_e1_binding_graph_invalid",
    ),
    AdmissionTamper(
        "support_internal_constraint_trigger_disabled",
        """
        DO $tamper$
        DECLARE
          trigger_name name;
        BEGIN
          SELECT candidate.tgname INTO STRICT trigger_name
            FROM pg_catalog.pg_trigger AS candidate
           WHERE candidate.tgrelid =
                 'identity.confirmation_artifacts'::regclass
             AND candidate.tgisinternal
             AND candidate.tgconstraint <> 0
           ORDER BY candidate.tgname
           LIMIT 1;
          EXECUTE pg_catalog.format(
            'ALTER TABLE identity.confirmation_artifacts DISABLE TRIGGER %%I',
            trigger_name
          );
        END
        $tamper$;
        """,
        "identity_erasure_e1_constraint_trigger_invalid",
    ),
    AdmissionTamper(
        "binding_graph_trigger_dropped",
        """
        DROP TRIGGER validate_principal_binding_graph_confirmation_artifacts
          ON identity.confirmation_artifacts;
        """,
        "identity_erasure_e1_support_triggers_invalid",
    ),
    AdmissionTamper(
        "binding_graph_trigger_extra",
        """
        CREATE CONSTRAINT TRIGGER e1_unreviewed_binding_graph
          AFTER INSERT OR UPDATE OR DELETE ON identity.people
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
          EXECUTE FUNCTION identity.validate_principal_binding_graph_trigger();
        """,
        "identity_erasure_e1_support_triggers_invalid",
    ),
    AdmissionTamper(
        "confirmation_artifact_unreviewed_trigger_function",
        """
        CREATE FUNCTION identity.e1_unreviewed_confirmation_trigger()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$ BEGIN RETURN NEW; END; $$;
        REVOKE ALL ON FUNCTION
          identity.e1_unreviewed_confirmation_trigger() FROM PUBLIC;
        CREATE TRIGGER e1_unreviewed_confirmation_trigger
          BEFORE INSERT ON identity.confirmation_artifacts
          FOR EACH ROW EXECUTE FUNCTION
            identity.e1_unreviewed_confirmation_trigger();
        """,
        "identity_erasure_e1_support_triggers_invalid",
    ),
    AdmissionTamper(
        "erasure_request_unreviewed_trigger_function",
        """
        CREATE FUNCTION privacy.e1_unreviewed_request_trigger()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$ BEGIN RETURN NEW; END; $$;
        REVOKE ALL ON FUNCTION
          privacy.e1_unreviewed_request_trigger() FROM PUBLIC;
        CREATE TRIGGER e1_unreviewed_request_trigger
          BEFORE INSERT ON privacy.erasure_requests
          FOR EACH ROW EXECUTE FUNCTION privacy.e1_unreviewed_request_trigger();
        """,
        "identity_erasure_e1_support_triggers_invalid",
    ),
    AdmissionTamper(
        "unexpected_updatable_authority_view",
        """
        CREATE VIEW identity.e1_unreviewed_confirmation_view AS
          SELECT * FROM identity.confirmation_artifacts;
        GRANT SELECT, UPDATE ON identity.e1_unreviewed_confirmation_view
          TO home_agent_worker;
        """,
        "identity_erasure_e1_relation_projection_invalid",
    ),
    AdmissionTamper(
        "unexpected_authority_rewrite_rule",
        """
        CREATE RULE e1_unreviewed_erasure_request_insert AS
          ON INSERT TO privacy.erasure_requests DO INSTEAD NOTHING;
        """,
        "identity_erasure_e1_support_rules_invalid",
    ),
    AdmissionTamper(
        "unexpected_authority_inheritance_child",
        """
        CREATE TABLE identity.e1_unreviewed_confirmation_child ()
          INHERITS (identity.confirmation_artifacts);
        """,
        "identity_erasure_e1_support_inheritance_invalid",
    ),
    AdmissionTamper(
        "unexpected_event_trigger",
        """
        CREATE FUNCTION public.e1_unreviewed_event_trigger()
        RETURNS event_trigger LANGUAGE plpgsql
        SET search_path = pg_catalog AS $$ BEGIN RETURN; END; $$;
        REVOKE ALL ON FUNCTION public.e1_unreviewed_event_trigger() FROM PUBLIC;
        CREATE EVENT TRIGGER e1_unreviewed_event_trigger
          ON ddl_command_end WHEN TAG IN ('ALTER TABLE')
          EXECUTE FUNCTION public.e1_unreviewed_event_trigger();
        """,
        "identity_erasure_e1_event_trigger_invalid",
    ),
    AdmissionTamper(
        "unexpected_logical_publication",
        """
        CREATE PUBLICATION e1_unreviewed_identity_publication
          FOR TABLE identity.ha_user_bindings;
        """,
        "identity_erasure_e1_logical_replication_invalid",
    ),
    AdmissionTamper(
        "nondefault_replica_identity",
        """
        ALTER TABLE identity.ha_user_bindings REPLICA IDENTITY FULL;
        """,
        "identity_erasure_e1_logical_replication_invalid",
    ),
    AdmissionTamper(
        "system_relation_read_granted_to_online_role",
        """
        GRANT SELECT ON TABLE pg_catalog.pg_authid TO home_agent_api;
        """,
        "identity_erasure_e1_system_catalog_invalid",
    ),
    AdmissionTamper(
        "system_file_function_granted_to_online_role",
        """
        GRANT EXECUTE ON FUNCTION pg_catalog.pg_read_file(text)
          TO home_agent_api;
        """,
        "identity_erasure_e1_system_catalog_invalid",
    ),
    AdmissionTamper(
        "user_created_pg_catalog_security_definer",
        """
        CREATE FUNCTION pg_catalog.e1_unreviewed_system_authority()
        RETURNS void LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog AS $$ BEGIN RETURN; END; $$;
        """,
        "identity_erasure_e1_system_catalog_invalid",
    ),
    AdmissionTamper(
        "binding_graph_poison_with_catalog_restored",
        """
        ALTER TABLE identity.principal_binding_requests DISABLE TRIGGER
          validate_principal_binding_graph_principal_binding_requests;
        ALTER TABLE identity.principal_binding_proposals DISABLE TRIGGER
          validate_principal_binding_graph_principal_binding_proposals;
        INSERT INTO identity.people (
          person_id, display_name, status, privacy_scope
        ) VALUES (
          '01950000-0000-7000-8000-000000000001',
          'E1 poisoned graph fixture', 'active', 'private'
        );
        INSERT INTO identity.principal_binding_requests (
          request_id, ha_user_id, review_code, state, requested_at, expires_at
        ) VALUES (
          '01950000-0000-7000-8000-000000000002',
          'e1-poisoned-user', 'ABCDEFGHJKLMNPQR', 'pending',
          '2026-01-01 00:00:00+00', '2026-01-03 00:00:00+00'
        );
        INSERT INTO identity.principal_binding_proposals (
          proposal_id, operator_request_id, request_id, ha_user_id, person_id,
          reviewed_display_label, person_snapshot_digest, proposal_digest,
          state, stage_receipt_digest, staged_at, expires_at
        ) VALUES (
          '01950000-0000-7000-8000-000000000003',
          '01950000-0000-7000-8000-000000000004',
          '01950000-0000-7000-8000-000000000002',
          'e1-poisoned-user', '01950000-0000-7000-8000-000000000001',
          'E1 poisoned proposal', repeat('a', 64), repeat('b', 64),
          'ready', repeat('c', 64),
          '2026-01-01 01:00:00+00', '2026-01-02 00:00:00+00'
        );
        ALTER TABLE identity.principal_binding_requests ENABLE TRIGGER
          validate_principal_binding_graph_principal_binding_requests;
        ALTER TABLE identity.principal_binding_proposals ENABLE TRIGGER
          validate_principal_binding_graph_principal_binding_proposals;
        """,
        "identity_erasure_e1_binding_graph_data_invalid",
    ),
    AdmissionTamper(
        "binding_graph_trigger_not_deferred",
        """
        DROP TRIGGER validate_principal_binding_graph_ha_user_bindings
          ON identity.ha_user_bindings;
        CREATE TRIGGER validate_principal_binding_graph_ha_user_bindings
          AFTER INSERT OR UPDATE OR DELETE ON identity.ha_user_bindings
          FOR EACH ROW
          EXECUTE FUNCTION identity.validate_principal_binding_graph_trigger();
        """,
        "identity_erasure_e1_binding_graph_invalid",
    ),
    AdmissionTamper(
        "replay_guard_trigger_disabled",
        """
        ALTER TABLE operations.reviewed_identity_migration_erasure_impacts
          DISABLE TRIGGER reviewed_identity_migration_erasure_replay_guard;
        """,
        "identity_erasure_e1_replay_guard_invalid",
    ),
    AdmissionTamper(
        "replay_guard_extra_operation_trigger",
        """
        CREATE TRIGGER e1_unreviewed_operation_replay_guard
          AFTER INSERT OR UPDATE
          ON operations.reviewed_identity_migration_erasure_operations
          FOR EACH ROW EXECUTE FUNCTION
            operations.bump_reviewed_identity_migration_replay_guard();
        """,
        "identity_erasure_e1_replay_guard_invalid",
    ),
    AdmissionTamper(
        "principal_runtime_dml_grant",
        "GRANT UPDATE ON identity.principals TO home_agent_api;",
        "identity_erasure_e1_support_acl_invalid",
    ),
    AdmissionTamper(
        "confirmation_table_insert_grant",
        "GRANT INSERT ON identity.confirmation_artifacts TO home_agent_api;",
        "identity_erasure_e1_support_acl_invalid",
    ),
    AdmissionTamper(
        "confirmation_required_insert_column_revoked",
        """
        REVOKE INSERT (consumed_at)
          ON identity.confirmation_artifacts FROM home_agent_api;
        """,
        "identity_erasure_e1_support_acl_invalid",
    ),
    AdmissionTamper(
        "confirmation_worker_insert_grant",
        """
        GRANT INSERT (artifact_id)
          ON identity.confirmation_artifacts TO home_agent_worker;
        """,
        "identity_erasure_e1_support_acl_invalid",
    ),
    AdmissionTamper(
        "confirmation_api_insert_grant_option",
        """
        GRANT INSERT (
          artifact_id, principal_id, purpose, proposal_digest,
          client_nonce_sha256, issued_at, expires_at, consumed_at
        ) ON identity.confirmation_artifacts
          TO home_agent_api WITH GRANT OPTION;
        """,
        "identity_erasure_e1_unexpected_support_acl",
    ),
    AdmissionTamper(
        "binding_runtime_update_grant",
        "GRANT UPDATE ON identity.ha_user_bindings TO home_agent_erasure;",
        "identity_erasure_e1_support_acl_invalid",
    ),
    AdmissionTamper(
        "unexpected_role_confirmation_update_grant",
        """
        DO $$ BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_roles
             WHERE rolname = 'e1_unreviewed_support_acl'
          ) THEN
            CREATE ROLE e1_unreviewed_support_acl NOLOGIN;
          END IF;
        END $$;
        GRANT UPDATE ON identity.confirmation_artifacts
          TO e1_unreviewed_support_acl;
        """,
        "identity_erasure_e1_unexpected_support_acl",
    ),
    AdmissionTamper(
        "api_role_bypassrls",
        "ALTER ROLE home_agent_api BYPASSRLS;",
        "identity_erasure_e1_support_role_invalid",
    ),
    AdmissionTamper(
        "worker_role_bypassrls",
        "ALTER ROLE home_agent_worker BYPASSRLS;",
        "identity_erasure_e1_support_role_invalid",
    ),
    AdmissionTamper(
        "worker_role_superuser",
        "ALTER ROLE home_agent_worker SUPERUSER;",
        "identity_erasure_e1_support_role_invalid",
    ),
    AdmissionTamper(
        "worker_role_configuration_changed",
        "ALTER ROLE home_agent_worker SET search_path = public;",
        "identity_erasure_e1_support_role_invalid",
    ),
    AdmissionTamper(
        "standalone_unreviewed_superuser_role",
        "CREATE ROLE e1_unreviewed_cluster_superuser NOLOGIN SUPERUSER;",
        "identity_erasure_e1_support_role_invalid",
    ),
    AdmissionTamper(
        "standalone_unreviewed_plain_role",
        "CREATE ROLE e1_unreviewed_plain_role NOLOGIN;",
        "identity_erasure_e1_role_inventory_invalid",
    ),
    AdmissionTamper(
        "unreviewed_role_inherits_predefined_authority",
        """
        CREATE ROLE e1_unreviewed_predefined_member LOGIN;
        GRANT pg_read_all_data TO e1_unreviewed_predefined_member;
        """,
        "identity_erasure_e1_role_inventory_invalid",
    ),
    AdmissionTamper(
        "worker_role_has_unreviewed_member",
        """
        CREATE ROLE e1_unreviewed_worker_member NOLOGIN;
        GRANT home_agent_worker TO e1_unreviewed_worker_member
          WITH ADMIN FALSE, INHERIT FALSE, SET TRUE;
        """,
        "identity_erasure_e1_support_role_invalid",
    ),
    AdmissionTamper(
        "backup_required_membership_removed",
        "REVOKE pg_read_all_settings FROM home_agent_backup;",
        "identity_erasure_e1_support_role_invalid",
    ),
    AdmissionTamper(
        "backup_inherit_flag_changed",
        "ALTER ROLE home_agent_backup NOINHERIT;",
        "identity_erasure_e1_support_role_invalid",
    ),
    AdmissionTamper(
        "dormant_migration_expiry_removed",
        "ALTER ROLE home_agent_identity_migration VALID UNTIL 'infinity';",
        "identity_erasure_e1_support_role_invalid",
    ),
    AdmissionTamper(
        "api_role_has_unreviewed_parent",
        """
        CREATE ROLE e1_unreviewed_api_parent NOLOGIN;
        GRANT e1_unreviewed_api_parent TO home_agent_api
          WITH ADMIN FALSE, INHERIT FALSE, SET TRUE;
        """,
        "identity_erasure_e1_support_role_invalid",
    ),
    AdmissionTamper(
        "api_role_has_unreviewed_member",
        """
        CREATE ROLE e1_unreviewed_api_member NOLOGIN;
        GRANT home_agent_api TO e1_unreviewed_api_member
          WITH ADMIN FALSE, INHERIT FALSE, SET TRUE;
        """,
        "identity_erasure_e1_support_role_invalid",
    ),
    AdmissionTamper(
        "owner_role_has_unreviewed_member",
        """
        CREATE ROLE e1_unreviewed_owner_member NOLOGIN;
        GRANT home_agent_owner TO e1_unreviewed_owner_member
          WITH ADMIN FALSE, INHERIT FALSE, SET TRUE;
        """,
        "identity_erasure_e1_support_role_invalid",
    ),
    AdmissionTamper(
        "predecessor_operation_stray_grant",
        """
        GRANT SELECT
          ON operations.reviewed_identity_migration_erasure_operations
          TO home_agent_api;
        """,
        "identity_erasure_e1_predecessor_shape_invalid",
    ),
    AdmissionTamper(
        "online_role_has_parameter_set_authority",
        """
        GRANT SET ON PARAMETER session_replication_role TO home_agent_api;
        """,
        "identity_erasure_e1_system_catalog_invalid",
    ),
    AdmissionTamper(
        "database_defaults_to_replica_session",
        """
        ALTER DATABASE home_agent SET session_replication_role = 'replica';
        """,
        "identity_erasure_e1_system_catalog_invalid",
    ),
    AdmissionTamper(
        "offline_owner_defaults_to_replica_session",
        """
        ALTER ROLE home_agent_owner SET session_replication_role = 'replica';
        """,
        "identity_erasure_e1_system_catalog_invalid",
    ),
)


def _execute(database_url: str, sql: str) -> None:
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(sql)
    finally:
        engine.dispose()


@pytest.mark.parametrize("tamper", TAMPERS, ids=lambda tamper: tamper.name)
def test_e1_refuses_tampered_0010_atomically(
    e1_0010_database: str,
    tamper: AdmissionTamper,
) -> None:
    _execute(e1_0010_database, tamper.sql)

    with pytest.raises(Exception) as refused:
        alembic_upgrade(e1_0010_database)

    assert exception_sqlstate(refused.value) == "55000"
    assert tamper.error in exception_text(refused.value)
    assert_failed_upgrade_is_atomic(e1_0010_database)


def test_apply_grants_replay_restores_exact_acl_before_and_after_e1(
    e1_0010_database: str,
) -> None:
    _execute(
        e1_0010_database,
        """
        GRANT UPDATE ON identity.principals TO home_agent_api;
        GRANT INSERT ON identity.confirmation_artifacts TO home_agent_worker;
        GRANT SELECT
          ON operations.reviewed_identity_migration_erasure_operations
          TO home_agent_api;
        ALTER DEFAULT PRIVILEGES FOR ROLE home_agent_owner IN SCHEMA identity
          GRANT SELECT ON TABLES TO home_agent_identity_erasure_kernel;
        """,
    )

    replay_0010 = run_apply_grants(e1_0010_database)
    assert replay_0010.returncode == 0, replay_0010.stdout + replay_0010.stderr
    alembic_upgrade(e1_0010_database)
    assert_revision(e1_0010_database, REVISION_0011)

    for _ in range(2):
        replay_0011 = run_apply_grants(e1_0010_database)
        assert replay_0011.returncode == 0, replay_0011.stdout + replay_0011.stderr

    engine = create_engine(e1_0010_database)
    try:
        with engine.connect() as connection:
            confirmation_insert_columns = connection.execute(
                text(
                    "SELECT coalesce(array_agg(a.attname::text ORDER BY a.attnum) "
                    "FILTER (WHERE has_column_privilege('home_agent_api', "
                    "'identity.confirmation_artifacts', a.attname, 'INSERT')), "
                    "ARRAY[]::text[]) FROM pg_attribute a WHERE a.attrelid = "
                    "'identity.confirmation_artifacts'::regclass "
                    "AND a.attnum > 0 AND NOT a.attisdropped"
                )
            ).scalar_one()
            assert confirmation_insert_columns == [
                "artifact_id",
                "principal_id",
                "purpose",
                "proposal_digest",
                "client_nonce_sha256",
                "issued_at",
                "expires_at",
                "consumed_at",
            ]
            assert (
                connection.execute(
                    text(
                        "SELECT has_table_privilege('home_agent_api', "
                        "'identity.confirmation_artifacts', 'INSERT') OR "
                        "has_any_column_privilege('home_agent_api', "
                        "'identity.confirmation_artifacts', 'UPDATE,REFERENCES')"
                    )
                ).scalar_one()
                is False
            )
            for role in (
                "home_agent_api",
                "home_agent_worker",
                "home_agent_erasure",
            ):
                assert (
                    connection.execute(
                        text(
                            "SELECT has_column_privilege(:role, "
                            "'privacy.erasure_requests', "
                            "'identity_person_scope_commitment', "
                            "'SELECT,INSERT,UPDATE,REFERENCES')"
                        ),
                        {"role": role},
                    ).scalar_one()
                    is False
                )
            assert (
                direct_acl_count(connection, "home_agent_identity_erasure_kernel") == 0
            )
    finally:
        engine.dispose()


def test_e1_scrubs_stale_direct_kernel_acl_without_confusing_public_ambient_acl(
    e1_0010_database: str,
) -> None:
    _execute(
        e1_0010_database,
        """
        GRANT USAGE ON SCHEMA identity TO home_agent_identity_erasure_kernel;
        GRANT SELECT ON identity.people TO home_agent_identity_erasure_kernel;
        GRANT EXECUTE ON FUNCTION
          ingest.guard_envelope_immutability()
          TO home_agent_identity_erasure_kernel;
        ALTER DEFAULT PRIVILEGES FOR ROLE home_agent_owner IN SCHEMA privacy
          GRANT SELECT ON TABLES TO home_agent_identity_erasure_kernel;
        """,
    )
    alembic_upgrade(e1_0010_database)

    engine = create_engine(e1_0010_database)
    try:
        with engine.connect() as connection:
            assert (
                direct_acl_count(connection, "home_agent_identity_erasure_kernel") == 0
            )
            # Effective PUBLIC schema USAGE may remain true. The security
            # contract is absence of a direct kernel ACL, asserted above.
            assert (
                connection.execute(
                    text("SELECT to_regnamespace('public') IS NOT NULL")
                ).scalar_one()
                is True
            )
    finally:
        engine.dispose()
