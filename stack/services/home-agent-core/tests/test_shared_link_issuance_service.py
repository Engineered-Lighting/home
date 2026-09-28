"""Real encrypted journal and adapter composition, fixture SQL transport only."""
from datetime import timedelta

import pytest

from app.auth import ServiceIdentity
from app.crypto import FieldCipher
from app.shared_link_commitments import SharedLinkCeremonyContext, SharedLinkCommitments
from app.shared_link_issuance_service import SharedLinkIssuanceService
from app.shared_link_journal import SharedLinkJournal
from app import shared_link_issuance as sql
from .test_shared_link_commitments import submission
from .test_shared_link_issuance_adapter import Engine, NOW, URL


class IssuanceEngine(Engine):
    def __init__(self):
        super().__init__(None)
        self.receipt = None
        self.unknown = False
        self.missing = False
        self.changed = False
        self.denied = False

    async def execute(self, statement, parameters):
        value = submission()
        if statement is sql.LOOKUP:
            self.events.append("owner")
            self.row = {k: getattr(value, k) for k in ("principal_id", "person_id", "legacy_binding_id")}
        elif statement is sql.BEGIN:
            self.events.append("issue")
            self.receipt = dict(ceremony_id=parameters["ceremony_id"], authorization_generation=1, revision=1,
                created_at=NOW, expires_at=NOW+timedelta(minutes=5), echo_registration_revision=1,
                victoria_registration_revision=2)
            if self.unknown: raise ConnectionError("lost acknowledgement")
            self.row = self.receipt
        else:
            assert statement is sql.INSPECT
            self.events.append("inspect")
            if self.denied: raise ValueError("authority denied")
            self.row = None if self.missing else dict(self.receipt)
            if self.changed and self.row: self.row["authorization_generation"] += 1
        return self

    def one_or_none(self):
        return self.one()


def setup(tmp_path, monkeypatch):
    engine = IssuanceEngine()
    monkeypatch.setattr(sql, "create_async_engine", lambda *a, **kw: engine)
    database = sql.SharedLinkIssuanceDatabase(URL, commitments=SharedLinkCommitments(b"x"*32, key_id="test"), now=lambda: NOW)
    path = tmp_path / "issuance.sqlite"
    journal = SharedLinkJournal(path, cipher=FieldCipher(b"j"*32), now=lambda: NOW)
    context = SharedLinkCeremonyContext(**{k: getattr(submission(), k) for k in SharedLinkCeremonyContext.model_fields})
    return journal, database, engine, context, path


@pytest.mark.asyncio
async def test_shared_link_issuance_service_issues_once_and_recovers_after_restart(tmp_path, monkeypatch):
    journal, database, engine, context, path = setup(tmp_path, monkeypatch)
    identity = ServiceIdentity(submission().echo_subject)
    service = SharedLinkIssuanceService(journal=journal, database=database, now=lambda: NOW)
    issued = await service.begin(identity, context)
    assert issued.beginning == submission()
    assert [e for e in engine.events if e in ("owner", "issue", "inspect")] == ["owner", "issue", "inspect"]
    with pytest.raises(ValueError, match="recovery"):
        await service.begin(identity, context)
    journal.close()
    journal = SharedLinkJournal(path, cipher=FieldCipher(b"j"*32), now=lambda: NOW)
    try:
        service = SharedLinkIssuanceService(journal=journal, database=database, now=lambda: NOW)
        assert await service.recover(identity, context) == issued
        assert engine.events.count("issue") == 1
        assert engine.events.count("owner") == 1
        assert engine.events.count("inspect") == 2
    finally: journal.close()


@pytest.mark.parametrize("outcome", ["recovered", "missing", "denied", "expired", "changed"])
@pytest.mark.asyncio
async def test_shared_link_issuance_service_uncertain_never_resends(tmp_path, monkeypatch, outcome):
    journal, database, engine, context, _ = setup(tmp_path, monkeypatch)
    identity = ServiceIdentity(submission().echo_subject)
    service = SharedLinkIssuanceService(journal=journal, database=database, now=lambda: NOW)
    engine.unknown = True
    try:
        with pytest.raises(ConnectionError): await service.begin(identity, context)
        assert journal.inspect(submission()).state == "indeterminate"
        engine.missing = outcome == "missing"
        engine.denied = outcome == "denied"
        if outcome == "expired": service._now = lambda: NOW+timedelta(minutes=5)
        if outcome == "changed":
            # Recover a durable receipt first, then change the authority result.
            await journal.reconcile(submission(), database)
            engine.changed = True
        if outcome == "recovered":
            assert (await service.recover(identity, context)).beginning == submission()
        else:
            with pytest.raises(ValueError): await service.recover(identity, context)
        assert engine.events.count("issue") == 1
        assert engine.events.count("owner") == 1
    finally: journal.close()


@pytest.mark.parametrize("change", ["owner", "issuer", "session", "challenge", "prepared", "missing"])
@pytest.mark.asyncio
async def test_shared_link_issuance_service_rejects_recovery_before_database(tmp_path, monkeypatch, change):
    journal, database, engine, context, _ = setup(tmp_path, monkeypatch)
    identity = ServiceIdentity(submission().echo_subject)
    if change != "missing": journal.retain(submission())
    if change == "owner": identity = ServiceIdentity("different-owner")
    if change == "issuer": identity = ServiceIdentity(submission().echo_subject, "home-assistant:victoria", "victoria")
    if change == "session": context = context.model_copy(update={"victoria_session_commitment": "d"*64})
    if change == "challenge": context = context.model_copy(update={"echo_challenge_commitment": "e"*64})
    try:
        service = SharedLinkIssuanceService(journal=journal, database=database, now=lambda: NOW)
        with pytest.raises(ValueError): await service.recover(identity, context)
        assert engine.events == []
    finally: journal.close()
