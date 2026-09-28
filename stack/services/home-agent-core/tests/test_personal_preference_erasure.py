from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from psycopg.types.range import Range
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from app import erasure
from app.ledger import ErasureLedgerRecord


NOW = datetime(2026, 9, 28, tzinfo=UTC)


def record(**overrides):
    return ErasureLedgerRecord(**{
        "outbox_id": uuid4(), "erasure_request_id": uuid4(),
        "subject_kind": "personal_preference_fact", "principal_id": uuid4(),
        "fact_id": uuid4(), "operation_codes": ["scrub_personal_preference"],
        "policy_digest": "a" * 64, "completed_at": NOW,
        "source_created_at": NOW, "checkpoint_affected": True, **overrides,
    })


def test_preference_ledger_round_trip_preserves_scope_without_values():
    value = record()
    assert ErasureLedgerRecord.model_validate_json(value.model_dump_json()) == value
    assert value.subject_kind == "personal_preference_fact"
    for patch in ({"principal_id": None}, {"fact_id": None},
                  {"person_id": uuid4()}, {"preference": "warm"}):
        with pytest.raises(ValidationError):
            record(**patch)


class Rows:
    def __init__(self, rows): self.rows = rows
    def mappings(self): return self
    def scalars(self): return self
    def all(self): return self.rows


@pytest.mark.asyncio
async def test_preference_erasure_scrubs_history_pending_reviews_and_descendants(monkeypatch):
    principal, fact, transaction, pending, descendant = (uuid4() for _ in range(5))
    versions = [dict(fact_version_id=uuid4(), memory_transaction_id=transaction,
                     system_range=Range(NOW-timedelta(days=1), None))]
    statements = []

    async def execute(statement, *args):
        statements.append(statement)
        if statement.is_select:
            return Rows(versions if "knowledge.fact_versions" in str(statement) else [pending])
        return Rows([])

    connection = type("Connection", (), {"execute": staticmethod(execute)})()
    descendants = AsyncMock(return_value=[descendant])
    monkeypatch.setattr(erasure, "governed_descendants", descendants)
    result = await erasure.apply_personal_preference_erasure(
        connection, principal_id=principal, fact_id=fact,
        erasure_request_id=uuid4(), now=NOW, require_existing=True,
    )
    assert result.transaction_ids == (transaction, pending)
    assert result.descendant_artifact_ids == (descendant,)
    descendants.assert_awaited_once_with(connection, fact)
    compiled = [(str(s.compile(dialect=postgresql.dialect())),
                 s.compile(dialect=postgresql.dialect()).params) for s in statements]
    fact_query = next(params for sql, params in compiled if sql.startswith("SELECT") and "knowledge.fact_versions" in sql)
    assert "personal_preference.evening_lighting" in fact_query.values()
    assert principal in fact_query.values() and fact in fact_query.values()
    pending_query = next(params for sql, params in compiled if sql.startswith("SELECT") and "knowledge.memory_transactions" in sql)
    assert {"preference_fact_id": str(fact)} in pending_query.values()
    scrub = next(params for sql, params in compiled if sql.startswith("UPDATE knowledge.memory_transactions") and "exact_text_ciphertext" in sql)
    assert scrub["candidate"] == scrub["preview"] == {"erased": True}
    assert scrub["exact_text_ciphertext"] is None
    assert set(scrub["transaction_id_1"]) == {transaction, pending}
    blocked = [params["artifact_id"] for sql, params in compiled if sql.startswith("INSERT INTO privacy.retrieval_blocks")]
    assert blocked == [fact, descendant]


@pytest.mark.asyncio
async def test_replay_blocks_a_fact_even_when_backup_predates_its_creation(monkeypatch):
    fact, principal = uuid4(), uuid4()
    statements = []

    async def execute(statement, *args):
        statements.append(statement)
        return Rows([])

    connection = type("Connection", (), {"execute": staticmethod(execute)})()
    monkeypatch.setattr(erasure, "governed_descendants", AsyncMock(return_value=[]))
    result = await erasure.apply_personal_preference_erasure(
        connection, principal_id=principal, fact_id=fact,
        erasure_request_id=uuid4(), now=NOW, require_existing=False,
    )
    assert result.fact_version_ids == result.transaction_ids == ()
    block = statements[0].compile(dialect=postgresql.dialect())
    assert block.params["artifact_id"] == fact
    assert "ON CONFLICT" in str(block)


@pytest.mark.asyncio
async def test_descriptor_entrypoint_keeps_its_original_scope(monkeypatch):
    delegated = AsyncMock(return_value=object())
    monkeypatch.setattr(erasure, "_apply_fact_erasure", delegated)
    await erasure.apply_descriptor_erasure(
        object(), principal_id=uuid4(), fact_id=uuid4(),
        erasure_request_id=uuid4(), now=NOW, require_existing=True,
    )
    assert delegated.await_args.kwargs["predicate"] == "place_social_descriptor"
    assert delegated.await_args.kwargs["candidate_key"] == "descriptor_fact_id"
