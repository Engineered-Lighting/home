from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[2]
EXECUTOR = (
    ROOT
    / "stack/services/home-agent-core/app/identity_authority_executor.py"
)
ENTRYPOINT = ROOT / "stack/services/home-agent-core/docker-entrypoint.sh"
COMPOSE = ROOT / "stack/home-agent-compose.yml"
RUNNER = ROOT / "tools/run-home-agent-e1-postgres-gate.py"
WORKFLOW = ROOT / ".github/workflows/home-agent-e1-postgres.yml"


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "home_agent_identity_authority_executor_e5n",
        EXECUTOR,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _uuid7() -> str:
    return "018f3f7a-8b4d-7abc-8def-0123456789ab"


def _request(contract: str, document: bytes = b"{}") -> bytes:
    return json.dumps(
        {
            "contract": contract,
            "admission_id": _uuid7(),
            "document_b64": base64.b64encode(document).decode("ascii"),
        },
        separators=(",", ":"),
    ).encode("utf-8")


@pytest.mark.parametrize("name", ("finalize", "cutover"))
def test_request_parser_accepts_only_the_exact_bounded_contract(name: str) -> None:
    module = _module()
    operation = module.OPERATIONS[name]

    request = module.parse_request(_request(operation.contract), operation)

    assert str(request.admission_id) == _uuid7()
    assert request.document == b"{}"


@pytest.mark.parametrize(
    "raw",
    (
        b"",
        b"{}",
        b'{"contract":"x","contract":"x"}',
        b'{"contract":"x","admission_id":"x","document_b64":"e30="}',
        b'{"contract":"identity-finalizer-execution-e5n-v1",'
        b'"admission_id":"018f3f7a-8b4d-4abc-8def-0123456789ab",'
        b'"document_b64":"e30="}',
        b'{"contract":"identity-finalizer-execution-e5n-v1",'
        b'"admission_id":"018f3f7a-8b4d-7abc-8def-0123456789ab",'
        b'"document_b64":"e30"}',
    ),
)
def test_request_parser_fails_closed_without_reflecting_content(raw: bytes) -> None:
    module = _module()
    with pytest.raises(module.AuthorityExecutionError) as captured:
        module.parse_request(raw, module.OPERATIONS["finalize"])
    assert str(captured.value) in {
        "authority request is missing or oversized",
        "authority request is invalid",
    }


@pytest.mark.parametrize(
    ("name", "username"),
    (
        ("finalize", "home_agent_identity_finalizer"),
        ("cutover", "home_agent_identity_cutover"),
    ),
)
def test_database_url_is_pinned_to_the_disposable_role(
    name: str, username: str, monkeypatch
) -> None:
    module = _module()
    expected = (
        f"postgresql+psycopg://{username}:{'a' * 64}"
        "@postgres:5432/home_agent"
    )
    monkeypatch.setenv("HOME_AGENT_DATABASE_URL", expected)
    assert module.database_url(module.OPERATIONS[name]) == expected

    for rejected in (
        expected.replace(username, "home_agent_owner"),
        expected.replace("@postgres:", "@127.0.0.1:"),
        expected + "?sslmode=disable",
        expected.replace("a" * 64, "password"),
    ):
        monkeypatch.setenv("HOME_AGENT_DATABASE_URL", rejected)
        with pytest.raises(module.AuthorityExecutionError):
            module.database_url(module.OPERATIONS[name])


def _service(compose: str, name: str, next_name: str) -> str:
    return compose.split(f"  {name}:\n", 1)[1].split(f"\n  {next_name}:", 1)[0]


def test_operator_services_are_stdin_only_and_keep_credentials_separate() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")
    finalizer = _service(compose, "identity-finalizer", "identity-cutover")
    cutover = compose.split("  identity-cutover:\n", 1)[1].split(
        "\nsecrets:", 1
    )[0]

    for service, command, secret in (
        (finalizer, "identity-finalize", "database_url_identity_finalizer"),
        (cutover, "identity-cutover", "database_url_identity_cutover"),
    ):
        assert "profiles: [operator]" in service
        assert 'restart: "no"' in service
        assert "stdin_open: true" in service
        assert "tty: false" in service
        assert "read_only: true" in service
        assert "cap_drop: [ALL]" in service
        assert "no-new-privileges:true" in service
        assert "HOME_AGENT_DATABASE_URL_FILE: /run/secrets/database_url" in service
        assert f"command: [{command}]" in service
        assert secret in service
        assert "networks: [postgres-net]" in service
        assert "logging:\n      driver: none" in service
        assert "ports:" not in service
        assert "api-net" not in service


def test_executor_uses_first_statement_serializable_kernel_and_safe_errors() -> None:
    source = EXECUTOR.read_text(encoding="utf-8")
    assert 'isolation_level="SERIALIZABLE"' in source
    assert "hide_parameters=True" in source
    assert "poolclass=NullPool" in source
    assert "log_parameter_max_length_on_error=0" in source
    assert "operations.finalize_reviewed_identity_migration" in source
    assert "operations.commit_reviewed_identity_cutover" in source
    assert 'RETRYABLE_SQLSTATES = frozenset({"40001", "40P01", "55P03"})' in source
    assert "print(error" not in source
    assert "str(error)" not in source
    assert "document_b64" not in source.split("def main", 1)[1]


def test_fixed_entrypoints_do_not_enable_the_expired_roles() -> None:
    entrypoint = ENTRYPOINT.read_text(encoding="utf-8")
    assert "identity-finalize)" in entrypoint
    assert "identity-cutover)" in entrypoint
    assert "python -m app.identity_authority_executor finalize" in entrypoint
    assert "python -m app.identity_authority_executor cutover" in entrypoint
    for forbidden in ("ALTER ROLE", "VALID UNTIL", "docker compose", "alembic"):
        assert forbidden not in EXECUTOR.read_text(encoding="utf-8")


def test_e5n_is_carried_by_the_hosted_gate() -> None:
    runner = RUNNER.read_text(encoding="utf-8")
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "identity_authority_executor.py" in runner
    assert "test_identity_authority_executor_e5n.py" in runner
    assert "test_identity_authority_executor_e5n.py" in workflow
    assert "E5n" in workflow
