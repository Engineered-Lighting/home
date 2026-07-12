from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def compose_service(compose: str, name: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(name)}:\n(.*?)(?=^  [a-zA-Z0-9_-]+:|\Z)",
        compose,
    )
    if match is None:
        raise AssertionError(f"missing Compose service: {name}")
    return match.group(1)


class IdentityMigrationRoleDeploymentContractTests(unittest.TestCase):
    def test_login_is_expired_serializable_and_membership_isolated(self) -> None:
        roles = read("stack/home-agent-deploy/provision-roles.sh")
        self.assertIn("CREATE ROLE home_agent_identity_migration LOGIN", roles)
        self.assertRegex(
            roles,
            r"ALTER ROLE home_agent_identity_migration PASSWORD "
            r":'identity_migration_password'[\s\S]*?NOSUPERUSER NOCREATEDB "
            r"NOCREATEROLE NOREPLICATION NOINHERIT NOBYPASSRLS\s+"
            r"CONNECTION LIMIT 1 VALID UNTIL '1970-01-01 00:00:00\+00';",
        )
        self.assertIn(
            "ALTER ROLE home_agent_identity_migration SET "
            "default_transaction_isolation =\n  'serializable';",
            roles,
        )
        for expected in (
            "statement_timeout = '120s'",
            "lock_timeout = '5s'",
            "idle_in_transaction_session_timeout =\n  '15s'",
            "transaction_timeout = '180s'",
        ):
            self.assertIn(expected, roles)
        self.assertRegex(
            roles,
            r"GRANT CONNECT ON DATABASE home_agent TO[\s\S]*"
            r"home_agent_identity_migration;",
        )
        self.assertIn(
            "REVOKE CREATE, TEMPORARY ON DATABASE home_agent\n"
            "  FROM home_agent_identity_migration;",
            roles,
        )

        membership_purge = roles.split("FROM pg_auth_members AS membership", 1)[
            1
        ].split("\\gexec", 1)[0]
        self.assertIn("home_agent_identity_migration", membership_purge)
        self.assertIn("home_agent_identity_kernel", membership_purge)
        self.assertIn(
            "parent.rolname = 'home_agent_identity_kernel'",
            roles,
        )
        self.assertIn("member.rolname <> 'home_agent_owner'", roles)
        self.assertIn(
            "GRANT home_agent_identity_kernel TO home_agent_owner\n"
            "  WITH ADMIN FALSE, INHERIT FALSE, SET TRUE;",
            roles,
        )
        for online_role in (
            "home_agent_identity_migration",
            "home_agent_api",
            "home_agent_binding_operator",
            "home_agent_ingest",
            "home_agent_worker",
            "home_agent_erasure",
            "home_agent_rollout",
            "home_agent_backup",
        ):
            self.assertNotIn(
                f"GRANT home_agent_identity_kernel TO {online_role}",
                roles,
            )

    def test_kernel_is_nologin_and_has_no_database_or_default_authority(self) -> None:
        roles = read("stack/home-agent-deploy/provision-roles.sh")
        grants = read("stack/home-agent-deploy/apply-grants.sh")
        self.assertIn("CREATE ROLE home_agent_identity_kernel NOLOGIN", roles)
        self.assertRegex(
            roles,
            r"ALTER ROLE home_agent_identity_kernel NOLOGIN NOSUPERUSER "
            r"NOCREATEDB\s+NOCREATEROLE NOREPLICATION NOINHERIT NOBYPASSRLS "
            r"CONNECTION LIMIT 0;",
        )
        self.assertIn(
            "REVOKE ALL PRIVILEGES ON DATABASE home_agent "
            "FROM home_agent_identity_kernel;",
            roles,
        )
        connect_statement = roles.split("GRANT CONNECT ON DATABASE home_agent", 1)[
            1
        ].split(";", 1)[0]
        self.assertNotIn("home_agent_identity_kernel", connect_statement)
        default_privileges = re.findall(r"ALTER DEFAULT PRIVILEGES[\s\S]*?;", grants)
        self.assertTrue(default_privileges)
        for statement in default_privileges:
            self.assertNotIn("home_agent_identity_migration", statement)
            self.assertNotIn("home_agent_identity_kernel", statement)

    def test_secret_pair_is_independent_atomic_and_never_printed(self) -> None:
        bootstrap = read("stack/home-agent-deploy/bootstrap-secrets.sh")
        self.assertIn('identity_migration_password="$(hex_secret 32)"', bootstrap)
        self.assertIn(
            "identity-migration/postgres_identity_migration_password",
            bootstrap,
        )
        self.assertIn(
            "identity-migration/database_url_identity_migration",
            bootstrap,
        )
        self.assertIn(
            "postgresql+psycopg://home_agent_identity_migration:",
            bootstrap,
        )

        additive = read(
            "stack/home-agent-deploy/add-identity-migration-role-secrets.sh"
        )
        self.assertIn('target="$master_root/identity-migration"', additive)
        self.assertIn("openssl rand -hex 32", additive)
        self.assertIn("mv -T --no-clobber", additive)
        self.assertIn("already exists or is partial", additive)
        self.assertIn("chmod 0600", additive)
        self.assertIn("chown root:root", additive)
        self.assertIn("materialize-secrets.sh", additive)
        self.assertNotIn('echo "$password"', additive)
        for secret in (
            "postgres_owner_password",
            "postgres_api_password",
            "postgres_ingest_password",
            "postgres_worker_password",
            "postgres_erasure_password",
            "postgres_backup_password",
            "binding-operator/postgres_binding_operator_password",
            "rollout/postgres_rollout_password",
        ):
            self.assertIn(secret, additive)

    def test_materialization_replaces_old_bearers_with_exactly_one_file(self) -> None:
        materializer = read("stack/home-agent-deploy/materialize-secrets.sh")
        self.assertIn("install_exact_single_secret_service()", materializer)
        self.assertIn(
            "install_exact_single_secret_service identity-migration",
            materializer,
        )
        self.assertIn(
            "identity-migration/database_url_identity_migration database_url "
            "10001 10001",
            materializer,
        )
        self.assertIn("postgres_identity_migration_password 0 0", materializer)
        self.assertIn("previous_dir=", materializer)
        self.assertIn('find "$previous_dir"', materializer)
        self.assertNotRegex(
            materializer,
            r"install_secret identity-migration (?:operator|bootstrap)_token",
        )

        preflight = read("stack/home-agent-deploy/preflight.sh")
        self.assertIn(
            "identity migration master secret directory must contain exactly two files",
            preflight,
        )
        self.assertIn("identity migration password is not lowercase hex", preflight)
        self.assertIn("identity migration password has the wrong length", preflight)
        self.assertIn(
            "identity migration password must differ from every database role",
            preflight,
        )
        self.assertIn(
            "postgresql+psycopg://home_agent_identity_migration:",
            preflight,
        )
        self.assertIn(
            "identity migration runtime secret directory must contain only database_url",
            preflight,
        )
        self.assertIn("stale identity migration runtime publication exists", preflight)
        self.assertNotIn(
            'runtime/identity-migration/operator_token"',
            preflight,
        )
        self.assertNotIn(
            'runtime/identity-migration/bootstrap_token"',
            preflight,
        )

    def test_compose_profile_is_a_networkless_retired_tombstone(self) -> None:
        compose = read("stack/home-agent-compose.yml")
        service = compose_service(compose, "identity-migration")
        self.assertIn("profiles: [operator]", service)
        self.assertIn("network_mode: none", service)
        self.assertIn("driver: none", service)
        self.assertIn("read_only: true", service)
        self.assertIn(
            "sequential identity migration is retired; use the reviewed atomic finalizer",
            service,
        )
        self.assertIn("exit 78", service)
        self.assertNotIn(
            'entrypoint: ["python", "/operator/migrate_legacy_identity.py"]',
            service,
        )
        self.assertNotIn("HOME_AGENT_LEGACY_IDENTITY_DB", service)
        self.assertNotIn("database_url", service)
        self.assertNotIn("/operator", service)
        self.assertNotIn("/legacy", service)
        self.assertNotIn("depends_on:", service)
        self.assertNotIn("secrets:", service)
        self.assertNotIn("volumes:", service)
        self.assertNotIn("networks:", service)
        self.assertNotIn("api-net", service)
        self.assertNotRegex(service, r"(?m)^\s+ports:")
        self.assertNotIn("HOME_AGENT_MIGRATION_CORE_URL", service)
        for forbidden in (
            "postgres-net",
            "operator_token",
            "bootstrap_token",
            "service_token",
            "knowledge_encryption_key",
            "runtime_spool_key",
            "erasure_ledger_key",
        ):
            self.assertNotIn(forbidden, service)
        self.assertIn(
            "postgres_identity_migration_password_provision",
            compose_service(compose, "provision-roles"),
        )

    def test_grant_replay_is_manifest_only_and_has_no_finalizer(self) -> None:
        grants = read("stack/home-agent-deploy/apply-grants.sh")
        self.assertIn(
            "FROM home_agent_identity_migration, home_agent_identity_kernel;",
            grants,
        )
        self.assertNotIn(
            "FROM PUBLIC, home_agent_identity_migration, home_agent_identity_kernel;",
            grants.split("ALL FUNCTIONS IN SCHEMA", 1)[1].split(";", 1)[0],
        )
        self.assertIn(
            "operations.reviewed_identity_migration_capabilities()",
            grants,
        )
        self.assertIn(
            "operations.register_reviewed_identity_migration(jsonb)",
            grants,
        )
        _, quarantine, validation = grants.split("Quarantine each exact signature", 1)[
            1
        ].split("DO $$", 2)
        self.assertEqual(3, quarantine.count("REVOKE ALL ON FUNCTION"))
        self.assertIn("FROM PUBLIC, home_agent_api", quarantine)
        self.assertIn(
            "reviewed_replay_guard",
            validation,
        )
        self.assertIn(") IN (1, 2) THEN", validation)
        self.assertIn("partial identity migration kernel function set", validation)
        self.assertIn("FROM PUBLIC, home_agent_api", grants)
        self.assertIn("TO home_agent_identity_migration", grants)
        self.assertIn(
            "operations.reviewed_identity_migration_runs",
            grants,
        )
        self.assertIn(
            "authorization_id, from_mode, to_mode, rule_version, policy_version",
            grants,
        )
        self.assertIn(
            "operations.reviewed_identity_migration_erasure_impacts",
            validation,
        )
        self.assertIn("GRANT UPDATE (expires_at) ON TABLE", validation)
        self.assertIn(
            "reviewed_identity_migration_runs_replay_guard_trigger_select",
            validation,
        )
        self.assertIn(
            "reviewed_identity_migration_erasure_replay_guard",
            validation,
        )
        self.assertIn("pg_catalog.sha256", validation)
        for digest in (
            "1b26f6a57891eb6b35fc2c822ba4d92148c76c3f89eeff0b77b702225c6c1db2",
            "07a9d2d776de63360d8c473e674e7feb24c057b5cb308f56f57e2e7758eaf2a7",
            "627bb84f83baa6183144de5d94ddcc4f0da56dc02e64c544b4b679b5ed3c316b",
        ):
            self.assertIn(digest, validation)
        self.assertIn(
            "identity migration kernel ACL contract mismatch",
            validation,
        )
        self.assertNotIn("finalize_reviewed_identity_migration", grants)
        self.assertNotIn("semantic_authority_cutovers\n      TO", grants)

    def test_revision_remains_pinned_and_activation_is_not_implemented(self) -> None:
        env = read("stack/home-agent.env.example")
        roles = read("stack/home-agent-deploy/provision-roles.sh")
        documentation = read("stack/home-agent-deploy/IDENTITY-MIGRATION-ROLE.md")
        self.assertIn(
            "HOME_AGENT_EXPECTED_DB_REVISION=0006_worker_maintenance_health",
            env,
        )
        self.assertIn("VALID UNTIL '1970-01-01 00:00:00+00'", roles)
        self.assertIn("persisted URL therefore cannot connect", documentation)
        self.assertIn("provides none", documentation)
        deployment_files = "\n".join(
            path.name for path in (ROOT / "stack/home-agent-deploy").iterdir()
        )
        self.assertNotIn("activate-identity-migration", deployment_files)


if __name__ == "__main__":
    unittest.main()
