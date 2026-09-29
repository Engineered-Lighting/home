"""Lighting review, journal and service flow against a simulated home endpoint."""
import asyncio
import contextlib
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.crypto import FieldCipher
from app.errors import ForbiddenError
from app.lighting_authority import (LightingAuthority, LightingConsentCommitment, LightingConsentConfirmation,
                                    LightingGrantStorage)
from app.lighting_contract import LightingProposalRequest, LightOperation, from_wire
from app.lighting_edge_client import EdgeLightingClient
from app.lighting_journal import LightingJournal, MissingLightingRecord
from app.lighting_review import LightingActionCommitment, LightingActionConfirmation
from app.lighting_service import LightingDatabase, LightingNotPermitted, LightingService

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
SESSION = {"issuer_id": "home-assistant:echo", "subject": "owner", "session_commitment": "c" * 64}
SCOPE = {"subject": "owner", "session_commitment": "c" * 64}
REVISION = "b" * 64


def authority(granted=("echo", "victoria"), generation=1):
    def revision(site):
        return ({"source_revision": 1, "grant_revision": 1, "grant_id": uuid.uuid5(uuid.NAMESPACE_URL, site)}
                if site in granted else {"source_revision": 1, "grant_revision": 0, "grant_id": None})
    return LightingAuthority(principal_id=uuid.UUID(int=1), person_id=uuid.UUID(int=2), link_id=uuid.UUID(int=3),
                             link_revision=1, authorization_generation=generation, issuer_id="home-assistant:echo",
                             subject="owner", session_commitment="c" * 64, echo=revision("echo"),
                             victoria=revision("victoria"), valid_until=NOW + timedelta(seconds=60))


class Home:
    """A simulated light-only endpoint: records executes, answers outcomes."""

    def __init__(self, site, *, lights=None, behaviour=None):
        self.site, self.executed, self.records = site, [], {}
        self.behaviour = behaviour or (lambda value: httpx.Response(200, json={
            "version": 1, "request_id": value["request_id"], "operation_index": value["operation_index"],
            "status": "succeeded"}))
        self.lights = lights if lights is not None else [
            {"entity_id": "light.kitchen", "name": "Kitchen", "state": "on", "brightness_pct": 40, "dimmable": True},
            {"entity_id": "light.porch", "name": "Porch", "state": "off", "brightness_pct": None, "dimmable": False}]

    def __call__(self, request):
        value = json.loads(request.content)
        path = request.url.path
        if path.endswith("/inventory"):
            return httpx.Response(200, json={"version": 1, "site_id": self.site, "revision": REVISION,
                                             "lights": self.lights})
        key = (value["request_id"], value["operation_index"])
        if path.endswith("/execute"):
            self.executed.append(value)
            response = self.behaviour(value)
            if response.status_code == 200:
                self.records[key] = json.loads(response.content)["status"]
            return response
        return httpx.Response(200, json={"version": 1, "request_id": value["request_id"],
                                         "operation_index": value["operation_index"],
                                         "status": self.records.get(key, "absent")})


class Harness:
    def __init__(self, tmp_path, *, homes=None, current=None):
        self.homes = homes or {"echo": Home("echo"), "victoria": Home("victoria")}
        self.current = current or authority()
        self.database = LightingDatabase("postgresql://home_agent_lighting@db.invalid/home")

        @contextlib.asynccontextmanager
        async def transaction():
            yield object()
        self.database.transaction = transaction
        self.storage = LightingGrantStorage(LightingConsentCommitment(b"c" * 32))

        async def resolve(connection, **scope):
            assert scope == SCOPE
            return self.current
        self.storage.resolve = resolve
        self.journal = LightingJournal(tmp_path / "lighting.sqlite", cipher=FieldCipher(b"j" * 32))
        self.admitted = 0

        async def admission():
            self.admitted += 1
        clients = {site: EdgeLightingClient(site_id=site, base_url=f"https://{site}.example", secret=bytes([i]) * 32,
                                            client=httpx.AsyncClient(transport=httpx.MockTransport(home)),
                                            now_ms=lambda: int(NOW.timestamp() * 1000))
                   for i, (site, home) in enumerate(self.homes.items(), start=1)}
        self.service = LightingService(database=self.database, storage=self.storage,
            commitment=LightingActionCommitment(b"a" * 32), journal=self.journal, clients=clients,
            admission=admission, grant_lifetime=timedelta(days=30), now=lambda: NOW)

    def propose(self, **values):
        request = LightingProposalRequest(**{"operation_id": uuid.UUID(int=7), "sites": ("echo", "victoria"),
                                             "targets": ("kitchen",), "operation": "off", **values})
        return asyncio.run(self.service.propose(SESSION, request))

    def confirm(self, result, operation_id=uuid.UUID(int=7)):
        confirmation = LightingActionConfirmation(operation_id=operation_id,
                                                  reviewed_digest=result["review"]["reviewed_digest"])
        return asyncio.run(self.service.confirm(SESSION, confirmation))


@pytest.fixture
def harness(tmp_path):
    h = Harness(tmp_path)
    yield h
    h.journal.close()


def test_propose_freezes_one_operation_per_home_and_shows_no_identifiers(harness):
    result = harness.propose()
    assert result["status"] == "review"
    review = result["review"]
    assert [(op["site_id"], op["name"], op["operation"]) for op in review["operations"]] == [
        ("echo", "Kitchen", "off"), ("victoria", "Kitchen", "off")]
    assert "entity_id" not in json.dumps(review) and "owner" not in json.dumps(review)
    assert datetime.fromisoformat(review["expires_at"]) == NOW + timedelta(seconds=60)
    # Nothing is sent to a home before confirmation.
    assert all(home.executed == [] for home in harness.homes.values())


def test_confirm_sends_each_operation_once_and_reports_per_home_results(harness):
    proposal = harness.propose()
    result = harness.confirm(proposal)
    assert result["status"] == "done"
    assert [(r["site_id"], r["status"]) for r in result["results"]] == [("echo", "succeeded"), ("victoria", "succeeded")]
    sent = [value for home in harness.homes.values() for value in home.executed]
    assert [(v["entity_id"], v["operation"], v["operation_index"], v["revision"]) for v in sent] == [
        ("light.kitchen", "off", 0, REVISION), ("light.kitchen", "off", 1, REVISION)]
    assert {v["request_id"] for v in sent} == {str(uuid.UUID(int=7))}
    # Repeating the confirmation, or the proposal, reports the record and never sends again.
    assert harness.confirm(proposal)["status"] == "done"
    assert harness.propose()["status"] == "done"
    assert sum(len(home.executed) for home in harness.homes.values()) == 2


def test_a_wrong_digest_or_expired_review_is_never_dispatched(harness, tmp_path):
    proposal = harness.propose()
    with pytest.raises(ValueError):
        asyncio.run(harness.service.confirm(SESSION, LightingActionConfirmation(
            operation_id=uuid.UUID(int=7), reviewed_digest="0" * 64)))
    harness.service.now = lambda: NOW + timedelta(seconds=61)
    with pytest.raises(ValueError):
        harness.confirm(proposal)
    assert all(home.executed == [] for home in harness.homes.values())


def test_changed_authority_after_review_stops_dispatch(harness):
    proposal = harness.propose()
    harness.current = authority(generation=2)
    with pytest.raises(ForbiddenError):
        harness.confirm(proposal)
    assert all(home.executed == [] for home in harness.homes.values())


def test_lighting_requires_consent_for_every_named_home(tmp_path):
    h = Harness(tmp_path, current=authority(granted=("echo",)))
    try:
        with pytest.raises(LightingNotPermitted):
            h.propose()
        assert h.propose(sites=("echo",))["status"] == "review"
    finally:
        h.journal.close()


def test_uncertain_results_are_looked_up_not_resent(tmp_path):
    victoria = Home("victoria", behaviour=lambda value: httpx.Response(503, json={"error": "lighting_unavailable"}))
    h = Harness(tmp_path, homes={"echo": Home("echo"), "victoria": victoria})
    try:
        result = h.confirm(h.propose())
        assert result["status"] == "unknown"
        assert [r["status"] for r in result["results"]] == ["succeeded", "unknown"]
        # The home never recorded it: lookup finds 'absent', which is final there.
        settled = asyncio.run(h.service.outcome(SESSION, uuid.UUID(int=7)))
        assert [r["status"] for r in settled["results"]] == ["succeeded", "not_sent"]
        assert settled["status"] == "partial"
        assert len(victoria.executed) == 1
    finally:
        h.journal.close()


def test_definite_refusals_are_not_sent_results(tmp_path):
    victoria = Home("victoria", behaviour=lambda value: httpx.Response(409, json={"error": "allowlist_changed"}))
    h = Harness(tmp_path, homes={"echo": Home("echo"), "victoria": victoria})
    try:
        result = h.confirm(h.propose())
        assert [r["status"] for r in result["results"]] == ["succeeded", "not_sent"]
        assert result["status"] == "partial"
    finally:
        h.journal.close()


def test_unknown_names_ask_instead_of_guessing(harness):
    result = harness.propose(targets=("attic",))
    assert result["status"] == "clarify"
    assert result["clarification"]["reason"] == "unknown_light"
    assert result["clarification"]["candidates"] == ["Kitchen", "Porch"]


def test_consent_is_retained_confirmed_once_and_looked_up(harness):
    written = []

    async def confirm(connection, *, authority, review, confirmation):
        written.append(review.operation_id)
    harness.storage.confirm = confirm

    async def outcome(connection, **kwargs):
        return "committed" if written else "unknown"
    harness.storage.consent_outcome = outcome
    review = asyncio.run(harness.service.consent_propose(SESSION, uuid.UUID(int=9)))
    assert review["effect"] == "switch_allowlisted_lights_after_each_confirmation"
    confirmation = LightingConsentConfirmation(operation_id=uuid.UUID(int=9), reviewed_digest=review["reviewed_digest"])
    assert asyncio.run(harness.service.consent_confirm(SESSION, confirmation))["status"] == "committed"
    assert asyncio.run(harness.service.consent_confirm(SESSION, confirmation))["status"] == "committed"
    assert written == [uuid.UUID(int=9)]


def test_journal_survives_reopen_and_keeps_definite_results(tmp_path):
    path = tmp_path / "lighting.sqlite"
    journal = LightingJournal(path, cipher=FieldCipher(b"j" * 32))
    commitment = LightingActionCommitment(b"a" * 32)
    operations = (LightOperation(site_id="echo", entity_id="light.kitchen", name="Kitchen", operation="off"),)
    review = commitment.prepare(uuid.UUID(int=5), authority(), operations, [("echo", REVISION)], now=NOW)
    journal.retain_action(authority(), review)
    confirmation = LightingActionConfirmation(operation_id=uuid.UUID(int=5), reviewed_digest=review.reviewed_digest)
    journal.claim_action(confirmation, **SCOPE, commitment=commitment, now=NOW)
    journal.record_result(uuid.UUID(int=5), 0, "succeeded", **SCOPE)
    journal.record_result(uuid.UUID(int=5), 0, "unknown", **SCOPE)
    journal.close()
    reopened = LightingJournal(path, cipher=FieldCipher(b"j" * 32))
    try:
        value = reopened.read_action(uuid.UUID(int=5), **SCOPE)
        assert value.state == "dispatching" and value.results == ("succeeded",)
        with pytest.raises(ValueError):
            reopened.claim_action(confirmation, **SCOPE, commitment=commitment, now=NOW)
        with pytest.raises(ValueError):
            reopened.read_action(uuid.UUID(int=5), subject="someone-else", session_commitment="c" * 64)
        with pytest.raises(MissingLightingRecord):
            reopened.read_action(uuid.UUID(int=6), **SCOPE)
    finally:
        reopened.close()
    with pytest.raises(Exception):  # a different key cannot open the journal
        LightingJournal(path, cipher=FieldCipher(b"x" * 32))


def test_wire_requests_parse_json_arrays_and_uuid_strings():
    request = from_wire(LightingProposalRequest, {"version": 1, "operation_id": str(uuid.UUID(int=1)),
                                                  "sites": ["victoria"], "targets": ["kitchen"], "operation": "off",
                                                  "brightness": None})
    assert request.sites == ("victoria",) and request.targets == ("kitchen",)
    with pytest.raises(Exception):
        from_wire(LightingProposalRequest, {"version": True, "operation_id": str(uuid.UUID(int=1)),
                                            "sites": ["victoria"], "targets": "all", "operation": "off"})
