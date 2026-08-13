from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "stack/services/home-agent-core/app/phase3_activation_probe.py"
ENTRYPOINT = ROOT / "stack/services/home-agent-core/docker-entrypoint.sh"
SPEC = importlib.util.spec_from_file_location("phase3_activation_probe", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


class _Mappings:
    def __init__(self, value):
        self.value = value

    def one(self):
        return self.value


class _Result:
    def __init__(self, value):
        self.value = value

    def mappings(self):
        return _Mappings(self.value)


class _Connection:
    def __init__(self, value):
        self.value = value
        self.statement = ""

    async def execute(self, statement):
        self.statement = str(statement)
        return _Result(self.value)


def test_owner_url_is_exact_and_content_free(monkeypatch) -> None:
    password = "a" * 64
    expected = (
        f"postgresql+psycopg://home_agent_owner:{password}" "@postgres:5432/home_agent"
    )
    monkeypatch.setenv("HOME_AGENT_DATABASE_URL", expected)
    assert module.database_url() == expected
    monkeypatch.setenv(
        "HOME_AGENT_DATABASE_URL",
        expected.replace("home_agent_owner", "home_agent_api"),
    )
    with pytest.raises(module.ActivationProbeError):
        module.database_url()


def test_authority_probe_requires_one_current_promotion() -> None:
    connection = _Connection({"promotion_count": 1, "current_count": 1})
    result = asyncio.run(module._authority(connection))
    assert result == {
        "semantic_authority_current": True,
        "promotion_count": 1,
        "current_promotion_count": 1,
    }
    assert "evaluate_current_identity_semantic_authority" in connection.statement

    connection = _Connection({"promotion_count": 2, "current_count": 1})
    assert (
        asyncio.run(module._authority(connection))["semantic_authority_current"]
        is False
    )


def test_binding_probe_requires_one_consumed_receipted_binding() -> None:
    connection = _Connection({"binding_count": 1, "receipt_count": 1, "exact_count": 1})
    result = asyncio.run(module._binding(connection))
    assert result["authenticated_binding_confirmed"] is True
    assert "binding.revoked_at IS NULL" in connection.statement
    assert "receipt.authority_result" in connection.statement

    connection = _Connection({"binding_count": 1, "receipt_count": 0, "exact_count": 0})
    assert (
        asyncio.run(module._binding(connection))["authenticated_binding_confirmed"]
        is False
    )


def test_parent_probe_requires_one_atomic_receipt_and_exactly_two_facts() -> None:
    connection = _Connection(
        {
            "authority_receipt_count": 1,
            "receipt_edge_count": 2,
            "exact_fact_count": 2,
        }
    )
    result = asyncio.run(module._parents(connection))
    assert result["exact_parent_relationship_confirmed"] is True
    for sentinel in (
        "receipt.edge_count = 2",
        "fact.predicate = 'parent_of'",
        "fact.authority = 'explicit_related_party'",
        "fact.support = 'explicit_authority'",
        "fact.resolution = 'accepted'",
    ):
        assert sentinel in connection.statement

    connection = _Connection(
        {
            "authority_receipt_count": 1,
            "receipt_edge_count": 2,
            "exact_fact_count": 1,
        }
    )
    assert (
        asyncio.run(module._parents(connection))["exact_parent_relationship_confirmed"]
        is False
    )


def test_entrypoint_exposes_only_the_fixed_probe_operation() -> None:
    source = ENTRYPOINT.read_text(encoding="utf-8")
    block = source.split("phase3-activation-probe)", 1)[1].split(";;", 1)[0]
    assert '[ "$#" -eq 2 ]' in block
    assert 'exec python -m app.phase3_activation_probe "$2"' in block
    assert "eval" not in block
