"""Transaction-boundary fixtures, not PostgreSQL or HA authentication acceptance."""
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app import shared_auth_proof as module


def submission(**overrides):
    return module.FreshAuthSubmission(**{
        "proof_id": uuid4(), "subject": "same-user-id",
        "session_commitment": "a" * 64, "challenge_commitment": "b" * 64,
        "authenticated_at": datetime(2026, 9, 26, tzinfo=UTC),
        "registration_revision": 2, **overrides})


class Engine:
    def __init__(self, row):
        self.row = row
        self.events = []
        self.expected_function = "identity.issue_shared_auth_proof_v1"

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
        except Exception:
            self.events.append("rollback")
            raise
        else:
            self.events.append("commit")

    async def execute(self, sql, parameters):
        self.events.append("kernel")
        assert str(sql).startswith(f"SELECT * FROM {self.expected_function}(")
        self.parameters = parameters
        return self

    def mappings(self):
        return self

    def one(self):
        if isinstance(self.row, Exception):
            raise self.row
        return self.row

    def one_or_none(self):
        return self.one()

    async def dispose(self):
        self.events.append("dispose")


def database(monkeypatch, value, *, linked=False, **changes):
    row = {**value.model_dump(), "issuer_id": "home-assistant:echo",
        "issued_at": value.authenticated_at + timedelta(seconds=1),
        "expires_at": value.authenticated_at + timedelta(minutes=5), **changes}
    engine = Engine(row)
    if linked: engine.expected_function = "identity.issue_shared_link_auth_proof_v1"
    def make_engine(url, **options):
        assert url.drivername == "postgresql+psycopg"
        assert options["max_overflow"] == 0
        assert options["pool_size"] == 2
        assert options["pool_timeout"] == 2
        assert options["connect_args"] == {"connect_timeout": 5, "options":
            "-c statement_timeout=7000 -c lock_timeout=5000 "
            "-c idle_in_transaction_session_timeout=10000"}
        assert options["hide_parameters"] is True
        return engine
    monkeypatch.setattr(module, "create_async_engine", make_engine)
    adapter = module.SharedLinkProofDatabase if linked else module.SharedAuthProofDatabase
    return adapter("postgresql://home_agent_shared_echo_proof_ingress@fixture/unused", issuer_id="home-assistant:echo",
        now=lambda: value.authenticated_at + timedelta(seconds=1)), engine


@pytest.mark.parametrize("url", ["sqlite:///unused", "postgresql+asyncpg://fixture/unused"])
def test_unsupported_driver_rejected_before_engine_creation(monkeypatch, url):
    def unexpected_engine(*args, **kwargs):
        pytest.fail("unsupported driver must not create an engine")
    monkeypatch.setattr(module, "create_async_engine", unexpected_engine)
    with pytest.raises(ValueError, match="requires PostgreSQL psycopg"):
        module.SharedAuthProofDatabase(url, issuer_id="home-assistant:echo")


@pytest.mark.parametrize("authority", ["postgres", "home_agent_shared_victoria_proof_ingress", ""])
def test_shared_auth_proof_rejects_wrong_credentials_before_engine(monkeypatch, authority):
    monkeypatch.setattr(module, "create_async_engine", lambda *a, **kw: pytest.fail("must not create engine"))
    with pytest.raises(ValueError, match="dedicated issuer"):
        module.SharedAuthProofDatabase(f"postgresql://{authority}@fixture/unused", issuer_id="home-assistant:echo")


@pytest.mark.parametrize("query", ["user=postgres", "host=other", "dbname=other", "options=-c%20role=postgres",
    "sslmode=disable", "sslmode=", "sslmode=verify-full&sslmode=verify-full", "sslrootcert=root",
    "sslmode=verify-full&user=", "service=other"])
def test_shared_auth_proof_rejects_connection_overrides(monkeypatch, query):
    monkeypatch.setattr(module, "create_async_engine", lambda *a, **kw: pytest.fail("must not create engine"))
    with pytest.raises(ValueError, match="connection options"):
        module.SharedAuthProofDatabase(f"postgresql://home_agent_shared_echo_proof_ingress@fixture/unused?{query}",
                                      issuer_id="home-assistant:echo")


@pytest.mark.parametrize("site", ["echo", "victoria"])
def test_shared_auth_proof_accepts_issuer_role_with_verified_tls(monkeypatch, site):
    calls = []
    monkeypatch.setattr(module, "create_async_engine", lambda url, **kw: calls.append((url, kw)))
    module.SharedAuthProofDatabase(f"postgresql://home_agent_shared_{site}_proof_ingress@fixture/unused?sslmode=verify-full&sslrootcert=root",
                                  issuer_id=f"home-assistant:{site}")
    assert calls[0][0].username == f"home_agent_shared_{site}_proof_ingress"
    assert calls[0][0].query == {"sslmode": "verify-full", "sslrootcert": "root"}


@pytest.mark.asyncio
async def test_issuer_bound_kernel_is_first_statement_and_validates_before_commit(monkeypatch):
    value = submission()
    db, engine = database(monkeypatch, value)
    receipt = await db.issue(value)
    assert receipt.proof_id == value.proof_id
    assert engine.parameters == value.model_dump()
    assert "issuer_id" not in engine.parameters
    assert engine.events == ["connect", "isolation", "begin", "kernel", "commit"]
    await db.close()
    assert engine.events[-1] == "dispose"


@pytest.mark.asyncio
async def test_shared_auth_proof_linked_adapter_calls_only_challenge_kernel(monkeypatch):
    value = submission()
    db, engine = database(monkeypatch, value, linked=True)
    assert (await db.issue(value)).proof_id == value.proof_id
    assert engine.events == ["connect", "isolation", "begin", "kernel", "commit"]
    assert engine.parameters == value.model_dump()


@pytest.mark.asyncio
async def test_shared_auth_proof_linked_adapter_rolls_back_substituted_receipt(monkeypatch):
    value = submission()
    db, engine = database(monkeypatch, value, linked=True, subject="substituted")
    with pytest.raises(ValueError, match="mismatch"):
        await db.issue(value)
    assert engine.events[-1] == "rollback"


@pytest.mark.parametrize("changes", [
    {"issuer_id": "home-assistant:victoria"}, {"subject": "other-user"},
    {"registration_revision": 3}, {"proof_id": uuid4()},
    {"challenge_commitment": "c" * 64},
    {"expires_at": datetime(2026, 9, 26, 0, 6, tzinfo=UTC)},
    {"issued_at": datetime(2026, 9, 25, tzinfo=UTC)},
])
@pytest.mark.asyncio
async def test_substitution_or_invalid_window_rolls_back(monkeypatch, changes):
    value = submission()
    db, engine = database(monkeypatch, value, **changes)
    with pytest.raises(ValueError):
        await db.issue(value)
    assert engine.events[-1] == "rollback"
    assert "commit" not in engine.events


@pytest.mark.asyncio
async def test_uncertain_execution_is_not_automatically_retried(monkeypatch):
    value = submission()
    db, engine = database(monkeypatch, value)
    engine.row = ConnectionError("fixture disconnected")
    with pytest.raises(ConnectionError):
        await db.issue(value)
    assert engine.events.count("kernel") == 1


@pytest.mark.parametrize("changes", [
    {"issuer_id": "home-assistant:echo"}, {"site_id": "echo"},
    {"subject": " padded "}, {"subject": "a\nuser"},
    {"authenticated_at": datetime(2026, 9, 26)},
    {"registration_revision": True}, {"registration_revision": 0},
    {"session_commitment": "not-a-commitment"},
])
def test_input_contract_cannot_select_issuer_or_relax_provenance(changes):
    with pytest.raises(ValidationError):
        submission(**changes)


@pytest.mark.parametrize("changes", [
    {"subject": " padded "}, {"registration_revision": True},
    {"session_commitment": "bad"}, {"authenticated_at": datetime(2026, 9, 26)},
])
@pytest.mark.asyncio
async def test_shared_auth_proof_revalidates_copied_submission_before_connection(monkeypatch, changes):
    value = submission()
    db, engine = database(monkeypatch, value)
    with pytest.raises(ValueError):
        await db.issue(value.model_copy(update=changes))
    assert engine.events == []


@pytest.mark.parametrize("post_commit", [False, True])
@pytest.mark.parametrize("clock", ["expired", "naive", "missing", "backwards"])
@pytest.mark.asyncio
async def test_shared_auth_proof_checks_delivery_freshness_without_retry(monkeypatch, post_commit, clock):
    value = submission()
    db, engine = database(monkeypatch, value)
    invalid = {"expired": value.authenticated_at + timedelta(minutes=5),
        "naive": value.authenticated_at.replace(tzinfo=None), "missing": None,
        "backwards": value.authenticated_at - timedelta(seconds=2)}[clock]
    db._now = lambda: (value.authenticated_at + timedelta(seconds=1)
        if post_commit and "commit" not in engine.events else invalid)
    with pytest.raises(ValueError, match="unavailable or expired"):
        await db.issue(value)
    assert engine.events.count("kernel") == 1
    assert engine.events[-1] == ("commit" if post_commit else "rollback")


@pytest.mark.parametrize("outcome", ["found", "missing", "substituted", "disconnected", "expired_after_commit"])
@pytest.mark.asyncio
async def test_shared_auth_proof_lookup_never_issues_or_retries(monkeypatch, outcome):
    value = submission()
    db, engine = database(monkeypatch, value, linked=True)
    engine.expected_function = "identity.inspect_shared_link_auth_proof_v1"
    if outcome == "missing":
        engine.row = None
    elif outcome == "substituted":
        engine.row["session_commitment"] = "c" * 64
    elif outcome == "disconnected":
        engine.row = ConnectionError("fixture disconnected")
    elif outcome == "expired_after_commit":
        db._now = lambda: value.authenticated_at + timedelta(
            seconds=300 if "commit" in engine.events else 1)
    if outcome in ("substituted", "expired_after_commit", "disconnected"):
        with pytest.raises((ValueError, ConnectionError)):
            await db.inspect(value)
    else:
        receipt = await db.inspect(value)
        assert receipt is None if outcome == "missing" else receipt.proof_id == value.proof_id
    assert engine.parameters == value.model_dump()
    assert engine.events.count("kernel") == 1
    assert engine.events[-1] == ("rollback" if outcome in ("substituted", "disconnected") else "commit")


@pytest.mark.asyncio
async def test_shared_auth_proof_lookup_revalidates_before_connecting(monkeypatch):
    value = submission()
    db, engine = database(monkeypatch, value, linked=True)
    with pytest.raises(ValueError):
        await db.inspect(value.model_copy(update={"subject": " padded "}))
    assert engine.events == []
    assert not hasattr(module.SharedAuthProofDatabase, "inspect")
