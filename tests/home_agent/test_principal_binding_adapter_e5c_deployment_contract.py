from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def function(path: str, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    tree = ast.parse(read(path))
    matches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    ]
    assert len(matches) == 1
    return matches[0]


def test_e5c_committer_secret_is_additive_atomic_and_independent() -> None:
    source = read(
        "stack/home-agent-deploy/add-binding-committer-role-secrets.sh"
    )
    bootstrap = read("stack/home-agent-deploy/bootstrap-secrets.sh")
    assert source.startswith("#!/bin/sh\nset -eu\numask 077\n")
    assert "must run as root" in source
    assert 'target="$master_root/binding-committer"' in source
    assert 'temporary="$master_root/.binding-committer.new.$$"' in source
    assert "openssl rand -hex 32" in source
    assert '"$master_root"/*/postgres_*_password' in source
    assert "home_agent_binding_committer" in source
    assert "mv -T --no-clobber" in source
    assert 'echo "$password"' not in source
    assert 'binding_committer_password="$(hex_secret 32)"' in bootstrap
    assert (
        "binding-committer/postgres_binding_committer_password" in bootstrap
    )
    assert "binding-committer/database_url_binding_committer" in bootstrap


def test_e5c_committer_role_is_login_bounded_and_membership_free() -> None:
    provision = read("stack/home-agent-deploy/provision-roles.sh")
    assert "CREATE ROLE home_agent_binding_committer LOGIN" in provision
    assert (
        "ALTER ROLE home_agent_binding_committer PASSWORD "
        ":'binding_committer_password'" in provision
    )
    for setting in (
        "statement_timeout = '15s'",
        "lock_timeout = '5s'",
        "idle_in_transaction_session_timeout = '15s'",
        "transaction_timeout = '30s'",
    ):
        assert f"home_agent_binding_committer SET {setting}" in provision.replace(
            "\n  ", " "
        )
    assert "CONNECTION LIMIT 8" in provision
    assert "'home_agent_binding_committer'" in provision
    assert (
        "GRANT home_agent_identity_binding_kernel TO "
        "home_agent_binding_committer" not in provision
    )


def test_e5c_secrets_reach_only_provisioner_and_core_api() -> None:
    materialize = read("stack/home-agent-deploy/materialize-secrets.sh")
    compose = read("stack/home-agent-compose.yml")
    preflight = read("stack/home-agent-deploy/preflight.sh")
    entrypoint = read(
        "stack/services/home-agent-core/docker-entrypoint.sh"
    )

    assert (
        "binding-committer/postgres_binding_committer_password"
        in materialize
    )
    assert (
        "binding-committer/database_url_binding_committer"
        in materialize
    )
    assert materialize.count("binding_commit_database_url") == 1
    assert compose.count("target: binding_commit_database_url") == 1
    assert (
        "HOME_AGENT_BINDING_COMMIT_DATABASE_URL_FILE:"
        " /run/secrets/binding_commit_database_url" in compose
    )
    assert (
        "binding committer database URL was materialized outside core-api"
        in preflight
    )
    assert "HOME_AGENT_BINDING_COMMIT_DATABASE_URL" in entrypoint


def test_e5c_kernel_caller_is_committer_not_staging_operator() -> None:
    migration = read(
        "stack/services/home-agent-core/alembic/versions/"
        "0016_principal_binding_kernel_e5b.py"
    )
    assert 'CALLER_ROLE = "home_agent_binding_committer"' in migration
    assert "session_user <> 'home_agent_binding_committer'" in migration
    assert "session_user <> 'home_agent_binding_operator'" not in migration
    assert '"home_agent_binding_operator",\n    CALLER_ROLE,' in migration
    assert "GRANT USAGE ON SCHEMA identity TO {CALLER_ROLE}" in migration


def test_e5c_commit_pool_exposes_only_kernel_call_and_close() -> None:
    path = "stack/services/home-agent-core/app/db.py"
    tree = ast.parse(read(path))
    commit_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "PrincipalBindingCommitDatabase"
    )
    methods = {
        node.name
        for node in commit_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert methods == {"__init__", "commit", "close"}
    commit = function(path, "commit")
    source = ast.get_source_segment(read(path), commit)
    assert source is not None
    assert source.count("connection.execute(") == 1
    assert "isolation_level=\"SERIALIZABLE\"" in source
    assert "commit_authenticated_principal_binding_e5b" in source
    assert "SELECT session_user" not in source
    assert "set_config(" not in source


def test_e5c_route_uses_fresh_forwarded_identity_and_split_connections() -> None:
    api = read("stack/services/home-agent-core/app/api.py")
    bff = read("stack/services/home-agent-bff/src/bff.mjs")
    route = function(
        "stack/services/home-agent-core/app/api.py",
        "principal_binding_confirmation",
    )
    route_source = ast.get_source_segment(api, route)
    assert route_source is not None
    assert (
        'PRINCIPAL_BINDING_ADAPTER_REVISION = '
        '"0017_authenticated_binding_e5c"' in api
    )
    assert route_source.index("readiness_migration") < route_source.index(
        "await request.body()"
    )
    assert "service.ha_user_id" in route_source
    assert "store.principal_binding_commit_context(" in route_source
    assert "adapter.commit(" in route_source
    assert "PRINCIPAL_BINDING_FRESH_AUTH_ROUTES" in bff
    assert "`POST ${PRINCIPAL_BINDING_CONFIRM_PATH}`" in bff
    assert bff.count("fetchWhoami(") >= 2


def test_e5c_activation_is_a_distinct_pinned_grant_boundary() -> None:
    migration = read(
        "stack/services/home-agent-core/alembic/versions/"
        "0017_authenticated_binding_e5c.py"
    )
    grants = read("stack/home-agent-deploy/apply-grants.sh")

    assert 'revision: str = "0017_authenticated_binding_e5c"' in migration
    assert "grants no runtime capability" in migration
    assert "activate_authenticated_binding_e5c" in grants
    assert "\\if :activate_authenticated_binding_e5c" in grants
    assert (
        "GRANT USAGE ON SCHEMA identity TO "
        "home_agent_binding_committer" in grants
    )
    assert (
        "TO home_agent_binding_committer;\nRESET ROLE;" in grants
    )
    assert "authenticated binding E5c active ACL contract mismatch" in grants
    assert (
        "GRANT SELECT ON TABLE" not in "\n".join(
            line
            for line in grants.splitlines()
            if "home_agent_binding_committer" in line
        )
    )
