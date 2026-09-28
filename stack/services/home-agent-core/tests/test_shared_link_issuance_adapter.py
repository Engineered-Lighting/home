"""Deterministic adapter transaction fixtures; not database/HA acceptance."""
import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app import shared_link_issuance as module
from app.shared_link_commitments import SharedLinkCommitments
from .test_shared_link_commitments import submission

NOW = datetime(2026, 9, 26, tzinfo=UTC)
URL = "postgresql://home_agent_shared_link_coordinator@fixture/unused"


class Engine:
    def __init__(self, row):
        self.row = row
        self.events = []
        self.commit_error = None

    @asynccontextmanager
    async def connect(self):
        self.events.append("connect")
        yield self

    async def execution_options(self, **options):
        assert options == {"isolation_level": "SERIALIZABLE"}
        self.events.append("isolation")
        return self

    @asynccontextmanager
    async def begin(self):
        self.events.append("begin")
        try:
            yield
        except BaseException:
            self.events.append("rollback")
            raise
        else:
            self.events.append("commit")
            if self.commit_error:
                raise self.commit_error

    async def execute(self, sql, parameters):
        assert sql is module.BEGIN
        self.events.append("issuance")
        self.parameters = parameters
        return self

    def mappings(self):
        return self

    def one(self):
        if isinstance(self.row, BaseException):
            raise self.row
        return self.row

    async def dispose(self):
        self.events.append("dispose")


def database(monkeypatch, **changes):
    row = dict(ceremony_id=submission().ceremony_id, authorization_generation=1, revision=1,
        created_at=NOW, expires_at=NOW+timedelta(minutes=5), echo_registration_revision=1,
        victoria_registration_revision=2)
    row.update(changes)
    engine = Engine(row)
    factory = SharedLinkCommitments(b"x"*32, key_id="fixture-v1")
    def make_engine(url, **options):
        assert url.drivername == "postgresql+psycopg" and url.username == module.COORDINATOR_ROLE
        assert options["pool_size"] == 2 and options["max_overflow"] == 0 and options["pool_timeout"] == 2
        assert options["hide_parameters"] is True
        assert options["connect_args"] == {"connect_timeout": 5, "options":
            "-c statement_timeout=7000 -c lock_timeout=5000 -c idle_in_transaction_session_timeout=10000"}
        return engine
    monkeypatch.setattr(module, "create_async_engine", make_engine)
    return module.SharedLinkIssuanceDatabase(URL, commitments=factory, now=lambda: NOW), engine, factory


@pytest.mark.asyncio
async def test_shared_link_issuance_adapter_derives_commitments_and_validates_before_commit(monkeypatch):
    db, engine, factory = database(monkeypatch)
    value = submission()
    receipt = await db.begin(value)
    expected = factory.prepare(value)
    assert engine.parameters == {**expected.parameters(), "key_id": expected.key_id, "key_fingerprint": expected.key_fingerprint}
    assert receipt.ceremony_id == value.ceremony_id
    assert engine.events == ["connect", "isolation", "begin", "issuance", "commit"]
    await db.close()
    assert engine.events[-1] == "dispose"


@pytest.mark.parametrize("changes", [
    {"ceremony_id": uuid4()}, {"revision": 2}, {"authorization_generation": 0},
    {"echo_registration_revision": True}, {"victoria_registration_revision": -1},
    {"created_at": NOW.replace(tzinfo=None)}, {"expires_at": NOW+timedelta(minutes=6)},
    {"created_at": NOW-timedelta(minutes=5), "expires_at": NOW},
    {"created_at": NOW+timedelta(seconds=2), "expires_at": NOW+timedelta(minutes=5, seconds=2)},
    {"unexpected": "field"},
])
@pytest.mark.asyncio
async def test_shared_link_issuance_adapter_bad_receipt_rolls_back(monkeypatch, changes):
    db, engine, _ = database(monkeypatch, **changes)
    with pytest.raises(ValueError):
        await db.begin(submission())
    assert engine.events[-1] == "rollback" and "commit" not in engine.events


@pytest.mark.parametrize("failure", ["statement", "commit", "cancel"])
@pytest.mark.asyncio
async def test_shared_link_issuance_adapter_uncertain_outcome_never_retries(monkeypatch, failure):
    db, engine, _ = database(monkeypatch)
    if failure == "commit":
        engine.commit_error = ConnectionError("fixture commit acknowledgement lost")
    else:
        engine.row = asyncio.CancelledError() if failure == "cancel" else ConnectionError("fixture disconnected")
    with pytest.raises(asyncio.CancelledError if failure == "cancel" else ConnectionError):
        await db.begin(submission())
    assert engine.events.count("issuance") == 1


@pytest.mark.asyncio
async def test_shared_link_issuance_adapter_invalid_input_never_connects(monkeypatch):
    db, engine, _ = database(monkeypatch)
    with pytest.raises(ValueError):
        await db.begin(submission().model_copy(update={"echo_subject": " padded "}))
    assert engine.events == []


@pytest.mark.parametrize("url", ["sqlite:///unused", "postgresql+asyncpg://fixture/unused",
    "postgresql://home_agent_owner@fixture/unused", "postgresql://postgres@fixture/unused", "postgresql://fixture/unused"])
def test_shared_link_issuance_adapter_rejects_wrong_driver_or_credentials(monkeypatch, url):
    monkeypatch.setattr(module, "create_async_engine", lambda *args, **kwargs: pytest.fail("must reject before engine construction"))
    with pytest.raises(ValueError):
        module.SharedLinkIssuanceDatabase(url, commitments=SharedLinkCommitments(b"x"*32, key_id="fixture-v1"))


@pytest.mark.parametrize("query", [
    "user=home_agent_owner", "host=other", "dbname=other", "service=other",
    "options=-c%20role=home_agent_owner", "password=other", "sslmode=disable",
    "sslrootcert=fixture.pem", "sslmode=verify-full&sslmode=disable",
    "sslmode=verify-full&sslrootcert=", "sslmode=verify-full&user=home_agent_owner",
])
def test_shared_link_issuance_adapter_rejects_connection_overrides(monkeypatch, query):
    monkeypatch.setattr(module, "create_async_engine", lambda *a, **kw: pytest.fail("must reject before engine construction"))
    with pytest.raises(ValueError):
        module.SharedLinkIssuanceDatabase(URL + "?" + query,
            commitments=SharedLinkCommitments(b"x"*32, key_id="fixture-v1"))


def test_shared_link_issuance_adapter_preserves_verified_tls(monkeypatch):
    from sqlalchemy.dialects.postgresql.psycopg import PGDialect_psycopg
    captured = []
    monkeypatch.setattr(module, "create_async_engine", lambda url, **kw: captured.append(url))
    module.SharedLinkIssuanceDatabase(URL + "?sslmode=verify-full&sslrootcert=fixture.pem",
        commitments=SharedLinkCommitments(b"x"*32, key_id="fixture-v1"))
    _, args = PGDialect_psycopg().create_connect_args(captured[0])
    assert args["user"] == module.COORDINATOR_ROLE
    assert args["sslmode"] == "verify-full" and args["sslrootcert"] == "fixture.pem"


@pytest.mark.parametrize("after", [NOW+timedelta(minutes=5), NOW.replace(tzinfo=None), None,
    NOW-timedelta(seconds=2)])
@pytest.mark.asyncio
async def test_shared_link_issuance_adapter_rechecks_after_commit(monkeypatch, after):
    db, engine, _ = database(monkeypatch)
    db._now = lambda: after if "commit" in engine.events else NOW
    with pytest.raises(ValueError, match="committed.*no longer fresh"):
        await db.begin(submission())
    assert engine.events == ["connect", "isolation", "begin", "issuance", "commit"]


@pytest.mark.parametrize("found", [False, True])
@pytest.mark.asyncio
async def test_shared_link_issuance_inspection_never_issues_or_renews(monkeypatch, found):
    db, engine, _ = database(monkeypatch)
    db._now = lambda: NOW+timedelta(days=1)
    engine.one_or_none = lambda: engine.row if found else None
    async def execute(sql, parameters):
        assert sql is module.INSPECT
        engine.events.append("inspection")
        engine.parameters = parameters
        return engine
    engine.execute = execute
    receipt = await db.inspect_issuance(submission())
    if found:
        assert receipt.expires_at == NOW+timedelta(minutes=5)
    else:
        assert receipt is None
    assert engine.events == ["connect", "isolation", "begin", "inspection", "commit"]


@pytest.mark.asyncio
async def test_shared_link_issuance_inspection_wrong_receipt_rolls_back(monkeypatch):
    db, engine, _ = database(monkeypatch, ceremony_id=uuid4())
    engine.one_or_none = lambda: engine.row
    async def execute(sql, parameters):
        assert sql is module.INSPECT
        engine.events.append("inspection")
        return engine
    engine.execute = execute
    with pytest.raises(ValueError, match="historical receipt"):
        await db.inspect_issuance(submission())
    assert engine.events[-1] == "rollback"
