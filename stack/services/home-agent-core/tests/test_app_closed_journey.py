"""Cross-module replay of an app-closed HA Edge to Core journey."""

from __future__ import annotations

import base64
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
import sys
import types
import uuid

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from app import schema
from app.config import Settings
from app.db import Database
from app.models import (
    IngestBatch,
    PersonCreate,
    PlaceCreate,
    PlaceLocatorInput,
    PreferenceUpdate,
    SourceEntityBindingCreate,
)
from app.spool import EncryptedRuntimeSpool
from app.store import CoreStore
from tests.binding_helpers import complete_staged_principal_binding


ROOT = Path(__file__).resolve().parents[4]
EDGE_PACKAGE_ROOT = ROOT / "ha-config" / "home_agent_edge"

# Import the pure Edge components without executing the Home Assistant-bound
# integration initializer.  The harness intentionally starts no BFF, Tauri,
# webview, or model worker.
if "home_agent_edge" not in sys.modules:
    edge_package = types.ModuleType("home_agent_edge")
    edge_package.__path__ = [str(EDGE_PACKAGE_ROOT)]
    sys.modules["home_agent_edge"] = edge_package

from home_agent_edge.crypto import EncryptedEnvelopeCodec  # noqa: E402
from home_agent_edge.delivery import DeliveryCoordinator  # noqa: E402
from home_agent_edge.outbox import EdgeOutbox  # noqa: E402


pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason=(
        "TEST_DATABASE_URL is required because stable visit identity and "
        "durable acknowledgements are PostgreSQL invariants"
    ),
)


class ReplayClock:
    def __init__(self, value: float) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class DisconnectedCore:
    async def send(self, payload):
        del payload
        raise ConnectionError("core is intentionally offline")


class DirectCoreTransport:
    """The only journey receiver: typed Edge envelopes into Core ingest."""

    def __init__(self, store: CoreStore) -> None:
        self.store = store
        self.requests: list[dict[str, object]] = []

    async def send(self, payload):
        request = dict(payload)
        self.requests.append(request)
        result = await self.store.ingest(IngestBatch.model_validate(request))
        return result.model_dump(mode="json")


def edge_fix(
    *,
    entity_id: str,
    observed_at: datetime,
    latitude: float,
    longitude: float,
) -> dict[str, object]:
    stamp = observed_at.isoformat().replace("+00:00", "Z")
    return {
        "schema_version": 1,
        "source_event_type": "state_changed",
        "occurred_at": stamp,
        "received_at": stamp,
        "entity_id": entity_id,
        "context": {"id": str(uuid.uuid4())},
        "observation_kind": "transition",
        "new_state": {
            "state": "not_home",
            "last_updated": stamp,
            "attributes": {
                "latitude": latitude,
                "longitude": longitude,
                "gps_accuracy": 20,
            },
        },
    }


@pytest.mark.asyncio
async def test_app_closed_edge_restart_reconnect_and_core_replay_are_stable(
    tmp_path,
) -> None:
    """No interactive client participates in departure, arrival, or replay."""

    edge_key = b"e" * 32
    runtime_key = b"r" * 32
    knowledge_key = base64.urlsafe_b64encode(b"k" * 32).decode().rstrip("=")
    ledger_key = base64.urlsafe_b64encode(b"l" * 32).decode().rstrip("=")
    settings = Settings(
        database_url=SecretStr(os.environ["TEST_DATABASE_URL"]),
        runtime_spool_path=tmp_path / "core-runtime.sqlite",
        runtime_spool_key=SecretStr(
            base64.urlsafe_b64encode(runtime_key).decode().rstrip("=")
        ),
        knowledge_encryption_key=SecretStr(knowledge_key),
        erasure_ledger_path=tmp_path / "erasure-ledger.jsonl",
        erasure_ledger_head_path=tmp_path / "erasure-ledger.head.json",
        erasure_ledger_key=SecretStr(ledger_key),
        edge_token=SecretStr("edge-token-with-at-least-32-characters"),
        service_token=SecretStr("service-token-with-at-least-32-chars"),
        bootstrap_token=SecretStr("bootstrap-token-with-at-least-32-chars"),
        policy_digest="a" * 64,
        role="all",
        rollout_mode="canary",
    )
    database = Database(settings.async_database_url())
    runtime_spool = EncryptedRuntimeSpool(
        settings.runtime_spool_path, runtime_key
    )
    runtime_spool_closed = False
    store = CoreStore(database, runtime_spool, settings)
    reopened_runtime_spool = None
    try:
        suffix = uuid.uuid4().hex
        entity_id = f"device_tracker.app_closed_{suffix}"
        ha_user_id = f"app-closed-{suffix}"
        person = await store.create_person(
            PersonCreate(display_name=f"App Closed {suffix}")
        )
        await complete_staged_principal_binding(
            store,
            ha_user_id=ha_user_id,
            person_id=person.person_id,
        )
        principal = await store.resolve_principal(ha_user_id)
        await store.bind_source_entity(
            principal,
            SourceEntityBindingCreate(
                entity_id=entity_id,
                confirmation_artifact_id=uuid.uuid4(),
            ),
        )
        await store.set_preference(
            principal,
            PreferenceUpdate(
                key="location_memory",
                enabled=True,
                confirmation_artifact_id=uuid.uuid4(),
            ),
        )
        itaipava_id = await store.create_place(
            principal,
            PlaceCreate(
                canonical_name=f"Itaipava app-closed fixture {suffix}",
                place_type="locality",
                travel_greeting_eligible=False,
                locator=PlaceLocatorInput(
                    latitude=-22.4,
                    longitude=-43.14,
                    radius_m=1_000,
                    confirmation_artifact_id=uuid.uuid4(),
                ),
            ),
        )

        edge_path = tmp_path / "edge-runtime.sqlite"
        clock = ReplayClock(datetime.now(UTC).timestamp())
        first_edge = EdgeOutbox(
            edge_path,
            EncryptedEnvelopeCodec(edge_key),
            max_bytes=1024 * 1024,
            max_age_seconds=86_400,
            now=clock,
        )
        first_edge.initialize()
        assert first_edge.begin_runtime() is None
        edge_instance_id = first_edge.edge_instance_id
        epoch = first_edge.epoch
        first_edge.end_runtime()

        # Edge restarts before the journey. Its downtime is an explicit gap,
        # and the same instance/epoch/sequence authority survives the restart.
        clock.advance(30)
        restarted_edge = EdgeOutbox(
            edge_path,
            EncryptedEnvelopeCodec(edge_key),
            max_bytes=1024 * 1024,
            max_age_seconds=86_400,
            now=clock,
        )
        restarted_edge.initialize()
        assert restarted_edge.edge_instance_id == edge_instance_id
        assert restarted_edge.epoch == epoch
        assert restarted_edge.begin_runtime() == 1

        now = datetime.now(UTC)
        journey = (
            # Two stable fixes away from the reviewed locality establish the
            # departure side of the replay without inventing a home place.
            (now - timedelta(minutes=25), -22.7, -43.5),
            (now - timedelta(minutes=15), -22.70002, -43.5),
            # Two compatible fixes over ten minutes establish the arrival.
            (now - timedelta(minutes=11), -22.4, -43.14),
            (now - timedelta(minutes=1), -22.40003, -43.14),
        )
        for observed_at, latitude, longitude in journey:
            restarted_edge.enqueue(
                edge_fix(
                    entity_id=entity_id,
                    observed_at=observed_at,
                    latitude=latitude,
                    longitude=longitude,
                )
            )
        assert restarted_edge.stats()["pending_rows"] == 5
        encrypted_before_delivery = edge_path.read_bytes()
        assert b"-22.40003" not in encrypted_before_delivery
        assert b"-43.14" not in encrypted_before_delivery

        failed = await DeliveryCoordinator(
            restarted_edge, DisconnectedCore()
        ).deliver_once()
        assert failed.status == "retry_scheduled"
        assert failed.error_code == "ConnectionError"
        assert restarted_edge.stats()["pending_rows"] == 5

        # A second Edge process opens the same durable spool after the failed
        # attempt. No desktop/web app is present; delivery is Edge -> Core.
        clock.advance(300)
        recovered_edge = EdgeOutbox(
            edge_path,
            EncryptedEnvelopeCodec(edge_key),
            max_bytes=1024 * 1024,
            max_age_seconds=86_400,
            now=clock,
        )
        recovered_edge.initialize()
        transport = DirectCoreTransport(store)
        delivered = await DeliveryCoordinator(
            recovered_edge, transport
        ).deliver_once()
        assert delivered.status == "acknowledged"
        assert delivered.acknowledged_through == 5
        assert recovered_edge.stats()["pending_rows"] == 0
        assert len(transport.requests) == 1

        first_snapshot = await store.snapshot(principal)
        assert first_snapshot.latest_visit is not None
        assert first_snapshot.latest_visit["place_id"] == itaipava_id
        assert first_snapshot.latest_visit["state"] == "visiting"
        assert first_snapshot.latest_visit["coverage"] == "sufficient"
        stable_visit_id = first_snapshot.latest_visit["visit_id"]

        # Core also restarts. Replaying the exact at-least-once batch produces
        # duplicates, not another visit or arrival identity.
        request = transport.requests[0]
        runtime_spool.close()
        runtime_spool_closed = True
        reopened_runtime_spool = EncryptedRuntimeSpool(
            settings.runtime_spool_path, runtime_key
        )
        restarted_store = CoreStore(database, reopened_runtime_spool, settings)
        replay = await restarted_store.ingest(
            IngestBatch.model_validate(request)
        )
        assert replay.accepted == 0
        assert replay.duplicates == 5
        replay_snapshot = await restarted_store.snapshot(principal)
        assert replay_snapshot.latest_visit is not None
        assert replay_snapshot.latest_visit["visit_id"] == stable_visit_id
        async with database.transaction(
            principal_id=principal["principal_id"]
        ) as connection:
            visit_count = (
                await connection.execute(
                    select(func.count())
                    .select_from(schema.visits)
                    .where(
                        schema.visits.c.principal_id
                        == principal["principal_id"],
                        schema.visits.c.place_id == itaipava_id,
                    )
                )
            ).scalar_one()
        assert visit_count == 1
        idle = await DeliveryCoordinator(
            recovered_edge, transport
        ).deliver_once()
        assert idle.status == "idle"
    finally:
        if reopened_runtime_spool is not None:
            reopened_runtime_spool.close()
        elif not runtime_spool_closed:
            runtime_spool.close()
        await database.close()
