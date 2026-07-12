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


class BindingOperatorDeploymentContractTests(unittest.TestCase):
    def test_role_is_isolated_and_provisioning_only_grants_connect(self) -> None:
        roles = read("stack/home-agent-deploy/provision-roles.sh")
        self.assertIn("CREATE ROLE home_agent_binding_operator LOGIN", roles)
        self.assertRegex(
            roles,
            r"ALTER ROLE home_agent_binding_operator PASSWORD :'binding_operator_password'\s+"
            r"NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT NOBYPASSRLS;",
        )
        self.assertIn(
            "ALTER ROLE home_agent_binding_operator SET statement_timeout = '15s';",
            roles,
        )
        self.assertRegex(
            roles,
            r"GRANT CONNECT ON DATABASE home_agent TO[\s\S]*"
            r"home_agent_binding_operator(?:,|;)",
        )
        self.assertNotRegex(
            roles,
            r"GRANT (?:SELECT|INSERT|UPDATE|DELETE|USAGE|EXECUTE)[^;]*"
            r"home_agent_binding_operator",
        )
        migration = read(
            "stack/services/home-agent-core/alembic/versions/"
            "0005_principal_binding_proposals.py"
        )
        downgrade = migration.split("def downgrade() -> None:", 1)[1]
        self.assertIn("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA", downgrade)
        self.assertIn("REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA", downgrade)
        self.assertIn("FROM home_agent_binding_operator", downgrade)

    def test_secret_is_independent_and_materialized_only_for_core_api(self) -> None:
        bootstrap = read("stack/home-agent-deploy/bootstrap-secrets.sh")
        self.assertIn('binding_operator_password="$(hex_secret 32)"', bootstrap)
        self.assertIn("binding-operator/postgres_binding_operator_password", bootstrap)
        self.assertIn("binding-operator/database_url_binding_operator", bootstrap)

        materializer = read("stack/home-agent-deploy/materialize-secrets.sh")
        expected = (
            "install_secret core-api binding-operator/database_url_binding_operator "
            "\\\n  operator_database_url 10001 10001"
        )
        self.assertEqual(materializer.count(expected), 1)
        self.assertNotRegex(
            materializer,
            r"install_secret (?!core-api\b)[^\n]*database_url_binding_operator",
        )

        additive = read("stack/home-agent-deploy/add-binding-operator-role-secrets.sh")
        self.assertIn('target="$master_root/binding-operator"', additive)
        self.assertIn("openssl rand -hex 32", additive)
        self.assertIn("mv -T --no-clobber", additive)
        self.assertIn("already exists or is partial", additive)
        self.assertIn("chmod 0600", additive)
        self.assertIn("chown root:root", additive)
        self.assertNotIn('echo "$password"', additive)

        upgrade = read("stack/home-agent-deploy/BINDING-OPERATOR-ROLE.md")
        self.assertIn("add-binding-operator-role-secrets.sh", upgrade)
        self.assertIn("materialize-secrets.sh", upgrade)
        self.assertIn("run --rm provision-roles", upgrade)

    def test_only_core_api_receives_operator_database_url(self) -> None:
        compose = read("stack/home-agent-compose.yml")
        core_api = compose_service(compose, "core-api")
        self.assertIn(
            "HOME_AGENT_OPERATOR_DATABASE_URL_FILE: /run/secrets/operator_database_url",
            core_api,
        )
        self.assertIn("database_url_binding_operator_core_api", core_api)

        for service in (
            "postgres",
            "backup-gate",
            "migrate",
            "grant-runtime",
            "core-ingest",
            "core-worker",
            "bff",
            "identity-migration",
        ):
            block = compose_service(compose, service)
            self.assertNotIn("OPERATOR_DATABASE_URL", block, service)
            self.assertNotIn("database_url_binding_operator_core_api", block, service)
            self.assertNotIn("operator_database_url", block, service)

    def test_entrypoint_and_preflight_fail_closed_around_operator_secret(self) -> None:
        entrypoint = read("stack/services/home-agent-core/docker-entrypoint.sh")
        self.assertIn(
            "load_secret HOME_AGENT_OPERATOR_DATABASE_URL "
            '"${HOME_AGENT_OPERATOR_DATABASE_URL_FILE:-}" '
            '"${HOME_AGENT_OPERATOR_DATABASE_URL:-}"',
            entrypoint,
        )

        preflight = read("stack/home-agent-deploy/preflight.sh")
        self.assertIn("binding operator password is not lowercase hex", preflight)
        self.assertIn("binding operator password has the wrong length", preflight)
        self.assertIn(
            "binding operator password must be independent from every other database role",
            preflight,
        )
        self.assertIn(
            "postgresql+psycopg://home_agent_binding_operator:${binding_operator_password}",
            preflight,
        )
        self.assertIn(
            "runtime/core-api/operator_database_url",
            preflight,
        )
        self.assertIn(
            "binding operator database URL was materialized outside core-api",
            preflight,
        )


if __name__ == "__main__":
    unittest.main()
