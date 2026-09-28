"""Offline transaction fixtures, not owner authentication or SQL acceptance."""
import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app import shared_link_confirmation as module
from .test_shared_auth_proof_adapter import Engine


def request():
    return module.SharedLinkConfirmation(ceremony_id=uuid4(), echo_subject="owner",
        session_commitment="a"*64, expected_revision=1, expected_generation=7,
        confirmation_commitment="b"*64, proposal_digest="c"*64, proposal_id=uuid4(),
        receipt_id=uuid4(), link_id=uuid4(), echo_binding_id=uuid4(), victoria_binding_id=uuid4())


def database(monkeypatch, value, **changes):
    stamp = datetime(2026, 9, 26, tzinfo=UTC)
    row = dict(link_id=value.link_id, authorization_generation=8, revision=1, confirmed_at=stamp)
    row.update(changes)
    engine = Engine(row)
    engine.expected_function = "identity.confirm_shared_link_ceremony_v1"
    def make_engine(url, **options):
        assert url.username == module.ROLE
        assert url.drivername == "postgresql+psycopg"
        assert options["hide_parameters"] and options["pool_size"] == 2 and options["max_overflow"] == 0
        assert options["connect_args"]["options"] == "-c statement_timeout=7000 -c lock_timeout=5000 -c idle_in_transaction_session_timeout=10000"
        return engine
    monkeypatch.setattr(module, "create_async_engine", make_engine)
    return module.SharedLinkConfirmationDatabase(f"postgresql://{module.ROLE}@fixture/unused", now=lambda: stamp), engine


@pytest.mark.asyncio
async def test_shared_link_confirmation_adapter_positive_first_statement(monkeypatch):
    value = request()
    db, engine = database(monkeypatch, value)
    result = await db.confirm(value)
    assert result.link_id == value.link_id and result.authorization_generation == 8
    assert engine.events == ["connect", "isolation", "begin", "kernel", "commit"]
    assert engine.parameters == value.model_dump(exclude={"expected_generation"})
    await db.close()
    assert engine.events[-1] == "dispose"


@pytest.mark.parametrize("change", [{"link_id": uuid4()}, {"authorization_generation": 9}, {"revision": 2},
    {"confirmed_at": datetime(2026, 9, 27, tzinfo=UTC)}, {"confirmed_at": datetime(2026, 9, 26)}, {"extra": True}])
@pytest.mark.asyncio
async def test_shared_link_confirmation_adapter_rejects_changed_receipt(monkeypatch, change):
    value = request()
    db, engine = database(monkeypatch, value, **change)
    with pytest.raises(ValueError): await db.confirm(value)
    assert engine.events[-1] == "rollback"


@pytest.mark.parametrize("change", [{"echo_subject": " other "}, {"expected_generation": True},
    {"expected_revision": 9223372036854775807}, {"session_commitment": "bad"}])
@pytest.mark.asyncio
async def test_shared_link_confirmation_adapter_revalidates_bypassed_model(monkeypatch, change):
    value = request()
    db, engine = database(monkeypatch, value)
    with pytest.raises(ValidationError): await db.confirm(value.model_copy(update=change))
    assert engine.events == []


@pytest.mark.asyncio
async def test_shared_link_confirmation_adapter_historical_replay_is_not_a_new_lease(monkeypatch):
    value = request()
    stamp = datetime(2026, 9, 25, tzinfo=UTC)
    db, engine = database(monkeypatch, value, confirmed_at=stamp)
    assert (await db.confirm(value)).confirmed_at == stamp
    assert engine.events.count("kernel") == 1


@pytest.mark.parametrize("url", ["sqlite:///unused", "postgresql://postgres@fixture/unused",
    f"postgresql://{module.ROLE}@fixture/unused?user=postgres",
    f"postgresql://{module.ROLE}@fixture/unused?sslmode=disable",
    f"postgresql://{module.ROLE}@fixture/unused?host=other"])
def test_shared_link_confirmation_adapter_rejects_connection_overrides(monkeypatch, url):
    monkeypatch.setattr(module, "create_async_engine", lambda *a, **kw: pytest.fail("must not create engine"))
    with pytest.raises(ValueError): module.SharedLinkConfirmationDatabase(url)


@pytest.mark.parametrize("stage", ["execute", "commit", "cleanup"])
@pytest.mark.asyncio
async def test_shared_link_confirmation_adapter_never_retries_uncertain_failure(monkeypatch, stage):
    value = request()
    db, engine = database(monkeypatch, value)
    if stage == "execute":
        async def failed_execute(sql, params):
            engine.events.append("kernel")
            raise ConnectionError("lost after dispatch")
        engine.execute = failed_execute
    elif stage == "commit":
        @asynccontextmanager
        async def failed_commit():
            engine.events.append("begin")
            yield
            engine.events.append("commit")
            raise ConnectionError("commit acknowledgement lost")
        engine.begin = failed_commit
    else:
        @asynccontextmanager
        async def failed_cleanup():
            engine.events.append("connect")
            yield engine
            raise ConnectionError("cleanup failed after commit")
        engine.connect = failed_cleanup
    with pytest.raises(ConnectionError): await db.confirm(value)
    assert engine.events.count("connect") == engine.events.count("kernel") == 1


@pytest.mark.asyncio
async def test_shared_link_confirmation_adapter_propagates_cancellation_without_resend(monkeypatch):
    value = request()
    db, engine = database(monkeypatch, value)
    async def cancelled_execute(sql, params):
        engine.events.append("kernel")
        raise asyncio.CancelledError()
    engine.execute = cancelled_execute
    with pytest.raises(asyncio.CancelledError): await db.confirm(value)
    assert engine.events.count("connect") == engine.events.count("kernel") == 1


@pytest.mark.parametrize("outcome", ["missing", "found", "mismatch"])
@pytest.mark.asyncio
async def test_shared_link_confirmation_adapter_inspection_never_confirms(monkeypatch, outcome):
    value = request()
    db, engine = database(monkeypatch, value)
    engine.expected_function = "identity.inspect_shared_link_confirmation_v1"
    if outcome == "mismatch": engine.row["link_id"] = uuid4()
    engine.one_or_none = lambda: None if outcome == "missing" else engine.row
    if outcome == "mismatch":
        with pytest.raises(ValueError, match="mismatch"): await db.inspect_confirmation(value)
        assert engine.events[-1] == "rollback"
    else:
        result = await db.inspect_confirmation(value)
        assert result is None if outcome == "missing" else result.link_id == value.link_id
        assert engine.events[-1] == "commit"
    assert engine.parameters == value.model_dump(exclude={"expected_generation"})
    assert engine.events.count("kernel") == 1
