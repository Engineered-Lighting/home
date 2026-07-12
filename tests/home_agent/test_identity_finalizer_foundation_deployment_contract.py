from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LOGIN_ROLE = "home_agent_identity_finalizer"
KERNEL_ROLE = "home_agent_identity_finalizer_kernel"
SERVICE = "identity-finalizer"


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


class IdentityFinalizerFoundationDeploymentContractTests(unittest.TestCase):
    def test_finalizer_role_pair_is_distinct_expired_and_non_escalatable(self) -> None:
        roles = read("stack/home-agent-deploy/provision-roles.sh")
        self.assertIn(f"CREATE ROLE {LOGIN_ROLE} LOGIN", roles)
        self.assertIn(f"CREATE ROLE {KERNEL_ROLE} NOLOGIN", roles)
        self.assertRegex(
            roles,
            rf"ALTER ROLE {LOGIN_ROLE} PASSWORD "
            rf":'identity_finalizer_password'[\s\S]*?NOSUPERUSER NOCREATEDB "
            rf"NOCREATEROLE NOREPLICATION NOINHERIT NOBYPASSRLS\s+"
            rf"CONNECTION LIMIT 1 VALID UNTIL '1970-01-01 00:00:00\+00';",
        )
        self.assertRegex(
            roles,
            rf"ALTER ROLE {KERNEL_ROLE} NOLOGIN NOSUPERUSER NOCREATEDB\s+"
            rf"NOCREATEROLE NOREPLICATION NOINHERIT NOBYPASSRLS "
            rf"CONNECTION LIMIT 0;",
        )
        self.assertIn(
            f"ALTER ROLE {LOGIN_ROLE} SET default_transaction_isolation =\n"
            "  'serializable';",
            roles,
        )
        for expected in (
            "statement_timeout = '120s'",
            "lock_timeout = '5s'",
            "idle_in_transaction_session_timeout =\n  '15s'",
            "transaction_timeout = '180s'",
        ):
            self.assertIn(expected, roles)

        self.assertIn(
            f"GRANT {KERNEL_ROLE} TO home_agent_owner\n"
            "  WITH ADMIN FALSE, INHERIT FALSE, SET TRUE;",
            roles,
        )
        self.assertNotIn(f"GRANT {KERNEL_ROLE} TO {LOGIN_ROLE}", roles)
        for online_role in (
            "home_agent_api",
            "home_agent_binding_operator",
            "home_agent_identity_migration",
            "home_agent_ingest",
            "home_agent_worker",
            "home_agent_erasure",
            "home_agent_rollout",
            "home_agent_backup",
        ):
            self.assertNotIn(f"GRANT {KERNEL_ROLE} TO {online_role}", roles)

        self.assertIn(
            f"REVOKE CREATE, TEMPORARY ON DATABASE home_agent\n  FROM {LOGIN_ROLE};",
            roles,
        )
        self.assertIn(
            f"REVOKE ALL PRIVILEGES ON DATABASE home_agent FROM {KERNEL_ROLE};",
            roles,
        )

    def test_secret_namespace_is_independent_atomic_and_not_reused(self) -> None:
        bootstrap = read("stack/home-agent-deploy/bootstrap-secrets.sh")
        self.assertIn('identity_finalizer_password="$(hex_secret 32)"', bootstrap)
        self.assertIn(
            f"{SERVICE}/postgres_identity_finalizer_password",
            bootstrap,
        )
        self.assertIn(
            f"{SERVICE}/database_url_identity_finalizer",
            bootstrap,
        )
        self.assertIn(f"postgresql+psycopg://{LOGIN_ROLE}:", bootstrap)

        additive = read(
            "stack/home-agent-deploy/add-identity-finalizer-role-secrets.sh"
        )
        self.assertIn(f'target="$master_root/{SERVICE}"', additive)
        self.assertIn("openssl rand -hex 32", additive)
        self.assertIn("mv -T --no-clobber", additive)
        self.assertIn("already exists or is partial", additive)
        self.assertIn("chmod 0600", additive)
        self.assertIn("chown root:root", additive)
        self.assertIn("materialize-secrets.sh", additive)
        self.assertNotIn('echo "$password"', additive)
        for independent_secret in (
            "postgres_owner_password",
            "postgres_api_password",
            "postgres_binding_operator_password",
            "postgres_identity_migration_password",
            "postgres_ingest_password",
            "postgres_worker_password",
            "postgres_erasure_password",
            "postgres_rollout_password",
            "postgres_backup_password",
        ):
            self.assertIn(independent_secret, additive)

    def test_materialization_preflight_and_compose_publish_only_database_url(
        self,
    ) -> None:
        materializer = read("stack/home-agent-deploy/materialize-secrets.sh")
        self.assertIn(
            f"install_exact_single_secret_service {SERVICE}",
            materializer,
        )
        self.assertIn(
            f"{SERVICE}/database_url_identity_finalizer database_url",
            materializer,
        )
        self.assertIn("postgres_identity_finalizer_password 0 0", materializer)

        preflight = read("stack/home-agent-deploy/preflight.sh")
        self.assertIn(
            "identity finalizer master secret directory must contain exactly two files",
            preflight,
        )
        self.assertIn("identity finalizer password is not lowercase hex", preflight)
        self.assertIn("identity finalizer password has the wrong length", preflight)
        self.assertIn(
            "identity finalizer password must differ from every database role",
            preflight,
        )
        self.assertIn(f"postgresql+psycopg://{LOGIN_ROLE}:", preflight)
        self.assertIn(
            "identity finalizer runtime secret directory must contain only database_url",
            preflight,
        )

        compose = read("stack/home-agent-compose.yml")
        service = compose_service(compose, SERVICE)
        self.assertIn("profiles: [operator]", service)
        self.assertIn("/run/secrets/database_url", service)
        self.assertIn("database_url_identity_finalizer_identity_finalizer", service)
        self.assertIn("networks: [postgres-net]", service)
        self.assertIn(
            "grant-runtime: {condition: service_completed_successfully}",
            service,
        )
        self.assertIn("driver: none", service)
        self.assertIn("read_only: true", service)
        self.assertIn("identity finalizer capability is not implemented", service)
        self.assertIn("exit 78", service)
        self.assertNotIn("api-net", service)
        self.assertNotIn("edge-net", service)
        self.assertNotRegex(service, r"(?m)^\s+ports:")
        for forbidden in (
            "operator_token",
            "bootstrap_token",
            "service_token",
            "knowledge_encryption_key",
            "runtime_spool_key",
            "erasure_ledger_key",
            "postgres_owner_password",
            "postgres_identity_migration_password",
        ):
            self.assertNotIn(forbidden, service)

    def test_grants_quarantine_both_roles_and_expose_no_finalizer(self) -> None:
        grants = read("stack/home-agent-deploy/apply-grants.sh")
        for role in (LOGIN_ROLE, KERNEL_ROLE):
            self.assertIn(role, grants)
        for statement in re.findall(r"(?is)GRANT\s+EXECUTE[\s\S]*?;", grants):
            self.assertNotIn(LOGIN_ROLE, statement)
            self.assertNotIn(KERNEL_ROLE, statement)
        self.assertNotIn("finalize_reviewed_identity_migration", grants)
        for statement in re.findall(r"ALTER DEFAULT PRIVILEGES[\s\S]*?;", grants):
            if " GRANT " in f" {statement.upper()} ":
                self.assertNotIn(LOGIN_ROLE, statement)
                self.assertNotIn(KERNEL_ROLE, statement)
        self.assertIn(
            "REVOKE USAGE ON TYPE %I.%I FROM home_agent_identity_finalizer",
            grants,
        )
        self.assertIn(
            "REVOKE ALL PRIVILEGES ON TYPES\n" f"  FROM {LOGIN_ROLE}, {KERNEL_ROLE};",
            grants,
        )
        self.assertIn(
            "operations.reviewed_identity_migration_erasure_operations",
            grants,
        )
        self.assertIn("pg_catalog.to_regclass", grants)

    def test_foundation_migration_exposes_no_callable_finalizer(self) -> None:
        migration = read(
            "stack/services/home-agent-core/alembic/versions/"
            "0009_identity_finalizer_foundation.py"
        )
        self.assertNotIn("finalize_reviewed_identity_migration", migration)

    def test_production_pin_and_expired_login_have_no_activation_path(self) -> None:
        env = read("stack/home-agent.env.example")
        roles = read("stack/home-agent-deploy/provision-roles.sh")
        self.assertIn(
            "HOME_AGENT_EXPECTED_DB_REVISION=0006_worker_maintenance_health",
            env,
        )
        self.assertRegex(
            roles,
            rf"ALTER ROLE {LOGIN_ROLE}[\s\S]*?VALID UNTIL "
            r"'1970-01-01 00:00:00\+00';",
        )
        deployment_names = "\n".join(
            path.name for path in (ROOT / "stack/home-agent-deploy").iterdir()
        )
        self.assertNotIn("activate-identity-finalizer", deployment_names)
        self.assertNotIn("enable-identity-finalizer", deployment_names)


if __name__ == "__main__":
    unittest.main()
