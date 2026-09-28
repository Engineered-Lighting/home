"""Workstation caller isolation; database lineage remains a separate gate."""
from uuid import uuid4

import pytest

from app.auth import ServiceIdentity
from app.errors import ForbiddenError
from app import shared_link_issuance as module
from .test_shared_link_issuance_adapter import database
from .test_shared_link_commitments import submission
from app.shared_link_commitments import SharedLinkCeremonyContext


def resolver(monkeypatch, row=None):
    db, engine, _ = database(monkeypatch)
    engine.row = row
    async def execute(sql, parameters):
        assert sql is module.LOOKUP
        engine.events.append("lookup")
        engine.parameters = parameters
        return engine
    engine.execute = execute
    return db, engine


@pytest.mark.asyncio
async def test_shared_link_owner_resolver_uses_existing_confirmed_anchor(monkeypatch):
    row = dict(principal_id=uuid4(), person_id=uuid4(), legacy_binding_id=uuid4())
    db, engine = resolver(monkeypatch, row)
    identity = ServiceIdentity("same-id")
    result = await db.resolve_owner(identity)
    assert result.model_dump() == row and str(row["person_id"]) not in repr(result)
    assert engine.parameters == {"subject": "same-id"}
    assert engine.events == ["connect", "isolation", "begin", "lookup", "commit"]


@pytest.mark.parametrize("identity", [None, {"ha_user_id": "owner"},
    ServiceIdentity("same-id", "home-assistant:victoria", "victoria"),
    ServiceIdentity("same-id", "home-assistant:echo", "victoria"),
    ServiceIdentity(" padded "), ServiceIdentity(""), ServiceIdentity("a\nuser"),
    ServiceIdentity("x"*65)])
@pytest.mark.asyncio
async def test_shared_link_owner_resolver_rejects_unqualified_context_before_database(monkeypatch, identity):
    db, engine = resolver(monkeypatch)
    with pytest.raises(ForbiddenError):
        await db.resolve_owner(identity)
    assert engine.events == []


@pytest.mark.parametrize("row", [{}, {"principal_id": "not-uuid"},
    dict(principal_id=uuid4(), person_id=uuid4(), legacy_binding_id=uuid4(), extra="private")])
@pytest.mark.asyncio
async def test_shared_link_owner_resolver_bad_anchor_rolls_back(monkeypatch, row):
    db, engine = resolver(monkeypatch, row)
    with pytest.raises(ValueError):
        await db.resolve_owner(ServiceIdentity("owner"))
    assert engine.events[-1] == "rollback"


@pytest.mark.asyncio
async def test_shared_link_owner_resolver_does_not_retry_failed_lookup(monkeypatch):
    db, engine = resolver(monkeypatch, ConnectionError("fixture"))
    with pytest.raises(ConnectionError):
        await db.resolve_owner(ServiceIdentity("owner"))
    assert engine.events.count("lookup") == 1


def context():
    value = submission().model_dump()
    return SharedLinkCeremonyContext(**{key: value[key] for key in SharedLinkCeremonyContext.model_fields})


@pytest.mark.asyncio
async def test_shared_link_owner_preparation_uses_resolved_ids_without_issuing(monkeypatch):
    row = dict(principal_id=uuid4(), person_id=uuid4(), legacy_binding_id=uuid4())
    db, engine = resolver(monkeypatch, row)
    requested = context()
    result = await db.prepare_begin(ServiceIdentity("verified-owner"), requested)
    assert result.model_dump() == {**requested.model_dump(), **row, "echo_subject": "verified-owner"}
    assert engine.events == ["connect", "isolation", "begin", "lookup", "commit"]
    assert result.ceremony_id == requested.ceremony_id


@pytest.mark.parametrize("field", ["principal_id", "person_id", "legacy_binding_id", "echo_subject"])
def test_shared_link_owner_preparation_context_cannot_choose_owner(field):
    with pytest.raises(ValueError):
        SharedLinkCeremonyContext.model_validate({**context().model_dump(), field: "caller-selected"})


@pytest.mark.parametrize("changes", [
    {"victoria_session_commitment": "bad"},
    {"victoria_challenge_id": submission().echo_challenge_id},
    {"victoria_challenge_commitment": submission().echo_challenge_commitment},
])
@pytest.mark.asyncio
async def test_shared_link_owner_preparation_revalidates_before_lookup(monkeypatch, changes):
    db, engine = resolver(monkeypatch)
    with pytest.raises(ValueError):
        await db.prepare_begin(ServiceIdentity("owner"), context().model_copy(update=changes))
    assert engine.events == []
