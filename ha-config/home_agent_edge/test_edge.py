#!/usr/bin/env python3
"""Standalone tests for filtering, encrypted spooling, and acknowledgements."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import types
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# Import pure submodules without executing the HA-dependent integration
# package initializer. This keeps the contract tests runnable outside HA.
package = types.ModuleType("home_agent_edge")
package.__path__ = [str(Path(__file__).resolve().parent)]
sys.modules["home_agent_edge"] = package

from home_agent_edge.const import EVENT_CONVERSATION_FINISHED, EVENT_GAP  # noqa: E402
from home_agent_edge.crypto import EncryptedEnvelopeCodec  # noqa: E402
from home_agent_edge.delivery import DeliveryCoordinator  # noqa: E402
from home_agent_edge.model import EdgePolicy  # noqa: E402
from home_agent_edge.outbox import EdgeOutbox  # noqa: E402

# Minimal Home Assistant import surface for exercising the runtime policy
# transition without requiring a full HA test environment.
homeassistant = types.ModuleType("homeassistant")
homeassistant_const = types.ModuleType("homeassistant.const")
homeassistant_const.EVENT_STATE_CHANGED = "state_changed"
homeassistant_core = types.ModuleType("homeassistant.core")
homeassistant_core.Event = object
homeassistant_core.HomeAssistant = object
homeassistant_core.callback = lambda function: function
sys.modules.setdefault("homeassistant", homeassistant)
sys.modules.setdefault("homeassistant.const", homeassistant_const)
sys.modules.setdefault("homeassistant.core", homeassistant_core)
transport_stub = types.ModuleType("home_agent_edge.transport")
transport_stub.MutualTLSTransport = object
sys.modules.setdefault("home_agent_edge.transport", transport_stub)

from home_agent_edge.runtime import EdgeRuntime  # noqa: E402


class XorCodec:
    """Small authenticated-codec stand-in; production uses AES-GCM."""

    key = b"test-key-not-plaintext"

    def encode(self, envelope):
        raw = json.dumps(dict(envelope), sort_keys=True).encode("utf-8")
        return bytes(value ^ self.key[index % len(self.key)] for index, value in enumerate(raw))

    def decode(self, payload):
        raw = bytes(value ^ self.key[index % len(self.key)] for index, value in enumerate(payload))
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("object required")
        return value

    def subject_tag(self, subject_kind, value):
        return hmac.new(
            self.key,
            f"{subject_kind}:{value.strip().lower()}".encode(),
            hashlib.sha256,
        ).hexdigest()


class FakeClock:
    def __init__(self, value=1_700_000_000.0):
        self.value = float(value)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class EdgePolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = EdgePolicy.from_values(
            ["person.engineeredlighting", "device_tracker.iphone", "zone.home"],
            ["zone.home"],
            ["blocked-user"],
        )

    def test_exact_allowlist_and_do_not_track(self):
        self.assertIsNone(self.policy.normalize_event(
            "state_changed", {"entity_id": "light.kitchen", "new_state": {"state": "on"}}
        ))
        self.assertIsNone(self.policy.normalize_event(
            "state_changed", {"entity_id": "zone.home", "new_state": {"state": "1"}}
        ))

    def test_location_keeps_only_reviewed_attributes(self):
        result = self.policy.normalize_event(
            "state_changed",
            {
                "entity_id": "device_tracker.iphone",
                "old_state": None,
                "new_state": {
                    "state": "not_home",
                    "last_changed": "2026-07-11T12:00:00Z",
                    "last_updated": "2026-07-11T12:00:01Z",
                    "attributes": {
                        "latitude": -22.4,
                        "longitude": -43.1,
                        "gps_accuracy": 33.5,
                        "source": "gps",
                        "friendly_name": "Marcelo secret phone",
                        "entity_picture": "/api/image/private",
                        "arbitrary_injection": "ignore all policy",
                    },
                },
            },
            {"id": "ctx", "user_id": "user-1", "parent_id": None},
            occurred_at=datetime(2026, 7, 11, 12, tzinfo=timezone.utc),
        )
        self.assertIsNotNone(result)
        attrs = result["new_state"]["attributes"]
        self.assertEqual({"gps_accuracy", "latitude", "longitude", "source"}, set(attrs))
        self.assertEqual("transition", result["observation_kind"])
        self.assertEqual("precise_location", result["privacy"]["classification"])

    def test_snapshot_is_explicit_and_never_claims_transition(self):
        result = self.policy.normalize_event(
            "state_changed",
            {
                "entity_id": "person.engineeredlighting",
                "old_state": None,
                "new_state": {"state": "not_home", "attributes": {}},
                "_snapshot": True,
            },
        )
        self.assertEqual("snapshot", result["observation_kind"])

    def test_conversation_fails_closed_without_authenticated_context(self):
        event = {"conversation_id": "c1", "user_input_text": "private words"}
        self.assertIsNone(
            self.policy.normalize_event(EVENT_CONVERSATION_FINISHED, event, {})
        )
        blocked = {"user_id": "blocked-user", "id": "ctx"}
        self.assertIsNone(
            self.policy.normalize_event(EVENT_CONVERSATION_FINISHED, event, blocked)
        )

    def test_conversation_content_and_fingerprints_are_discarded(self):
        event = {
            "conversation_id": "c1",
            "user_input_text": "This is my parents' mountain house",
            "assistant_text": "Understood",
            "unknown": "not reviewed",
        }
        result = self.policy.normalize_event(
            EVENT_CONVERSATION_FINISHED, event, {"user_id": "user-1", "id": "ctx"}
        )
        data = result["data"]
        self.assertNotIn("user_input_text", data)
        self.assertNotIn("assistant_text", data)
        self.assertNotIn("unknown", data)
        self.assertNotIn("user_text_sha256", data)
        self.assertNotIn("user_text_length", data)
        self.assertNotIn("assistant_text_length", data)
        self.assertFalse(result["privacy"]["content_included"])
        self.assertTrue(result["privacy"]["content_discarded"])

    def test_removed_content_option_cannot_restore_utterance_text(self):
        policy = EdgePolicy.from_values(
            ["person.engineeredlighting"], include_conversation_text=True
        )
        result = policy.normalize_event(
            EVENT_CONVERSATION_FINISHED,
            {"user_input_text": "SENSITIVE-UTTERANCE-CANARY"},
            {"user_id": "user-1", "id": "ctx"},
        )
        serialized = json.dumps(result)
        self.assertNotIn("SENSITIVE-UTTERANCE-CANARY", serialized)
        self.assertNotIn("user_text_sha256", result["data"])

    def test_core_privacy_policy_blocks_entities_and_users_before_normalization(self):
        material = {
            "blocked_entity_ids": ["device_tracker.iphone"],
            "blocked_user_ids": ["user-1"],
            "version": 1,
        }
        document = {
            **material,
            "policy_digest": hashlib.sha256(
                json.dumps(
                    material, separators=(",", ":"), sort_keys=True
                ).encode("utf-8")
            ).hexdigest(),
        }
        policy = self.policy.with_core_privacy_policy(document)
        self.assertIsNone(
            policy.normalize_event(
                "state_changed",
                {"entity_id": "device_tracker.iphone", "new_state": {"state": "home"}},
            )
        )
        self.assertIsNone(
            policy.normalize_event(
                EVENT_CONVERSATION_FINISHED,
                {"conversation_id": "private"},
                {"user_id": "user-1"},
            )
        )
        with self.assertRaises(ValueError):
            self.policy.with_core_privacy_policy(
                {**document, "policy_digest": "0" * 64}
            )


class ProductionCodecTests(unittest.TestCase):
    def test_aes_gcm_detects_tampering_and_hides_plaintext(self):
        codec = EncryptedEnvelopeCodec(b"k" * 32)
        envelope = {"private": "SENSITIVE-COORDINATE-CANARY", "sequence": 1}
        encoded = codec.encode(envelope)
        self.assertNotIn(envelope["private"].encode(), encoded)
        self.assertEqual(envelope, codec.decode(encoded))
        tampered = encoded[:-1] + bytes([encoded[-1] ^ 1])
        with self.assertRaises(Exception):
            codec.decode(tampered)
        self.assertEqual(
            codec.subject_tag("ha_user", "User-1"),
            codec.subject_tag("ha_user", "user-1"),
        )
        self.assertNotIn(b"user-1", codec.subject_tag("ha_user", "user-1").encode())


class RuntimePrivacyPolicyTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _document(entities, users):
        material = {
            "blocked_entity_ids": sorted(entities),
            "blocked_user_ids": sorted(users),
            "version": 1,
        }
        return {
            **material,
            "policy_digest": hashlib.sha256(
                json.dumps(material, separators=(",", ":"), sort_keys=True).encode()
            ).hexdigest(),
        }

    async def test_refresh_is_serialized_and_unchanged_digest_does_not_repurge(self):
        class Hass:
            async def async_add_executor_job(self, function, *args):
                return function(*args)

        class Outbox:
            def __init__(self):
                self.calls = []

            def suppress_privacy_subjects(self, entities, users):
                self.calls.append((entities, users))

        class Transport:
            def __init__(self, documents):
                self.documents = list(documents)
                self.calls = 0
                self.concurrent = 0
                self.max_concurrent = 0

            async def fetch_privacy_policy(self):
                index = min(self.calls, len(self.documents) - 1)
                self.calls += 1
                self.concurrent += 1
                self.max_concurrent = max(self.max_concurrent, self.concurrent)
                await asyncio.sleep(0)
                self.concurrent -= 1
                return self.documents[index]

        first = self._document(["device_tracker.one"], ["user-one"])
        second = self._document(["device_tracker.two"], ["user-two"])
        transport = Transport([first, first, second])
        outbox = Outbox()
        runtime = EdgeRuntime(
            Hass(),
            EdgePolicy.from_values(["device_tracker.one", "device_tracker.two"]),
            outbox,
            transport,
            batch_size=10,
        )

        self.assertTrue(await runtime._async_refresh_privacy_policy())
        self.assertTrue(await runtime._async_refresh_privacy_policy())
        self.assertEqual(1, len(outbox.calls))
        await asyncio.gather(
            runtime._async_refresh_privacy_policy(),
            runtime._async_refresh_privacy_policy(),
        )
        self.assertEqual(1, transport.max_concurrent)
        self.assertEqual(2, len(outbox.calls))
        self.assertIn("device_tracker.two", runtime.policy.blocked_entity_ids)

    async def test_unrelated_state_event_is_prefiltered_without_policy_fetch(self):
        class Hass:
            async def async_add_executor_job(self, function, *args):
                return function(*args)

        class Outbox:
            def suppress_privacy_subjects(self, _entities, _users):
                raise AssertionError("unrelated state must not touch the spool")

        class Transport:
            calls = 0

            async def fetch_privacy_policy(self):
                self.calls += 1
                raise AssertionError("unrelated state must not fetch policy")

        transport = Transport()
        runtime = EdgeRuntime(
            Hass(),
            EdgePolicy.from_values(["device_tracker.reviewed"]),
            Outbox(),
            transport,
            batch_size=10,
        )
        await runtime._async_ingest(
            "state_changed",
            {"entity_id": "sensor.noisy_unrelated", "new_state": {"state": "1"}},
            None,
            datetime.now(timezone.utc),
        )
        self.assertEqual(0, transport.calls)

    async def test_policy_generation_is_held_through_enqueue(self):
        enqueue_entered = asyncio.Event()
        release_enqueue = asyncio.Event()

        class Outbox:
            def __init__(self):
                self.envelopes = []
                self.purges = []

            def enqueue(self, envelope):
                self.envelopes.append(envelope)

            def suppress_privacy_subjects(self, entities, users):
                self.purges.append((set(entities), set(users)))
                if "device_tracker.reviewed" in entities:
                    self.envelopes.clear()

            def quarantine(self, *_args):
                raise AssertionError("reviewed fixture must not quarantine")

        outbox = Outbox()

        class Hass:
            async def async_add_executor_job(self, function, *args):
                if function == outbox.enqueue:
                    enqueue_entered.set()
                    await release_enqueue.wait()
                return function(*args)

            def async_create_task(self, coroutine):
                coroutine.close()

        class Transport:
            def __init__(self):
                self.calls = 0

            async def fetch_privacy_policy(self):
                self.calls += 1
                return (
                    RuntimePrivacyPolicyTests._document([], [])
                    if self.calls == 1
                    else RuntimePrivacyPolicyTests._document(
                        ["device_tracker.reviewed"], []
                    )
                )

        transport = Transport()
        runtime = EdgeRuntime(
            Hass(),
            EdgePolicy.from_values(["device_tracker.reviewed"]),
            outbox,
            transport,
            batch_size=10,
        )
        event = {
            "entity_id": "device_tracker.reviewed",
            "old_state": None,
            "new_state": {"state": "home", "attributes": {}},
        }
        first = asyncio.create_task(
            runtime._async_ingest(
                "state_changed", event, None, datetime.now(timezone.utc)
            )
        )
        await enqueue_entered.wait()
        second = asyncio.create_task(
            runtime._async_ingest(
                "state_changed", event, None, datetime.now(timezone.utc)
            )
        )
        await asyncio.sleep(0)
        self.assertEqual(1, transport.calls)
        release_enqueue.set()
        await asyncio.gather(first, second)
        self.assertEqual(2, transport.calls)
        self.assertEqual([], outbox.envelopes)
        self.assertIn(
            "device_tracker.reviewed", runtime.policy.blocked_entity_ids
        )


class OutboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "edge.sqlite"
        self.clock = FakeClock()
        self.outbox = EdgeOutbox(
            self.path,
            XorCodec(),
            max_bytes=64 * 1024,
            max_age_seconds=60,
            now=self.clock,
        )
        self.outbox.initialize()

    def tearDown(self):
        self.temp.cleanup()

    def _event(self, marker):
        return {
            "schema_version": 1,
            "source_event_type": "state_changed",
            "entity_id": "device_tracker.iphone",
            "marker": marker,
        }

    def _sqlite_artifact_bytes(self, path=None):
        database = path or self.path
        candidates = (
            database,
            Path(f"{database}-wal"),
            Path(f"{database}-shm"),
        )
        return b"".join(
            candidate.read_bytes() for candidate in candidates if candidate.exists()
        )

    def _stored_ciphertext(self, path=None, sequence=1):
        database = path or self.path
        conn = sqlite3.connect(database)
        try:
            row = conn.execute(
                "SELECT ciphertext FROM outbox WHERE sequence_start = ?",
                (sequence,),
            ).fetchone()
            self.assertIsNotNone(row)
            return bytes(row[0])
        finally:
            conn.close()

    def test_sequence_is_durable_and_plaintext_is_absent(self):
        marker = "SENSITIVE-COORDINATE-CANARY"
        self.assertEqual(1, self.outbox.enqueue(self._event(marker)))
        self.assertEqual(2, self.outbox.enqueue(self._event("second")))
        batch = self.outbox.read_batch()
        self.assertEqual((1, 2), (batch.first_sequence, batch.last_sequence))
        self.assertEqual(marker, batch.items[0].payload["marker"])
        all_files = b"".join(
            path.read_bytes() for path in self.path.parent.iterdir() if path.is_file()
        )
        self.assertNotIn(marker.encode(), all_files)

        # Reopening the spool continues the same epoch and sequence.
        reopened = EdgeOutbox(
            self.path, XorCodec(), max_bytes=64 * 1024,
            max_age_seconds=60, now=self.clock,
        )
        reopened.initialize()
        self.assertEqual(3, reopened.enqueue(self._event("third")))
        self.assertEqual(self.outbox.epoch, reopened.epoch)

    def test_acknowledgement_must_end_on_an_envelope_boundary(self):
        self.outbox.enqueue(self._event("one"))
        self.outbox.enqueue(self._event("two"))
        batch = self.outbox.read_batch()
        with self.assertRaises(ValueError):
            self.outbox.acknowledge(3, batch)
        self.assertEqual(2, self.outbox.stats()["pending_rows"])
        self.outbox.acknowledge(1, batch)
        self.assertEqual(1, self.outbox.stats()["pending_rows"])

    def test_dynamic_privacy_block_replaces_spooled_ciphertext_with_gap(self):
        self.outbox.enqueue(self._event("private-location"))
        self.assertEqual(
            1,
            self.outbox.suppress_privacy_subjects(
                {"device_tracker.iphone"}, set()
            ),
        )
        item = self.outbox.read_batch().items[0]
        self.assertEqual(EVENT_GAP, item.payload["source_event_type"])
        self.assertEqual("privacy_directive", item.payload["reason_code"])
        self.assertNotIn("private-location", json.dumps(item.payload))

    def test_dynamic_privacy_block_replaces_spooled_user_context_with_gap(self):
        self.outbox.enqueue(
            {
                "schema_version": 1,
                "source_event_type": EVENT_CONVERSATION_FINISHED,
                "context": {"user_id": "blocked-user"},
                "data": {"conversation_id": "private"},
            }
        )
        self.assertEqual(
            1,
            self.outbox.suppress_privacy_subjects(set(), {"blocked-user"}),
        )
        item = self.outbox.read_batch().items[0]
        self.assertEqual(EVENT_GAP, item.payload["source_event_type"])
        artifacts = self._sqlite_artifact_bytes()
        self.assertNotIn(b"blocked-user", artifacts)

    def test_ack_and_expiry_remove_ciphertext_from_all_sqlite_artifacts(self):
        marker = "ACK-CIPHERTEXT-CANARY-" + ("a9Q!" * 2048)
        self.outbox.enqueue(self._event(marker))
        ciphertext = self._stored_ciphertext()
        canary = ciphertext[512:1536]
        self.assertIn(canary, self._sqlite_artifact_bytes())
        self.outbox.acknowledge(1, self.outbox.read_batch())
        self.assertNotIn(canary, self._sqlite_artifact_bytes())

        expiry_marker = "EXPIRY-CIPHERTEXT-CANARY-" + ("b7R?" * 2048)
        self.outbox.enqueue(self._event(expiry_marker))
        expiry_ciphertext = self._stored_ciphertext(sequence=2)
        expiry_canary = expiry_ciphertext[512:1536]
        self.assertIn(expiry_canary, self._sqlite_artifact_bytes())
        self.clock.advance(61)
        self.outbox.maintain()
        self.assertNotIn(expiry_canary, self._sqlite_artifact_bytes())
        self.assertEqual(
            "retention_expired",
            self.outbox.read_batch().items[0].payload["reason_code"],
        )

    def test_physical_main_wal_and_shm_size_is_hard_bounded(self):
        self.outbox.enqueue(self._event("large-" + ("x" * 256_000)))
        stats = self.outbox.stats()
        physical = sum(
            candidate.stat().st_size if candidate.exists() else 0
            for candidate in (
                self.path,
                Path(f"{self.path}-wal"),
                Path(f"{self.path}-shm"),
            )
        )
        self.assertEqual(physical, stats["physical_bytes"])
        self.assertLessEqual(physical, 64 * 1024)
        self.assertEqual(EVENT_GAP, self.outbox.read_batch().items[0].payload["source_event_type"])

    def test_existing_spool_is_safely_migrated_to_full_auto_vacuum(self):
        legacy_path = Path(self.temp.name) / "legacy.sqlite"
        conn = sqlite3.connect(legacy_path)
        try:
            conn.execute("PRAGMA auto_vacuum=NONE")
            conn.execute("CREATE TABLE legacy_canary(value TEXT NOT NULL)")
            conn.execute("INSERT INTO legacy_canary(value) VALUES ('preserved')")
            conn.commit()
        finally:
            conn.close()

        migrated = EdgeOutbox(
            legacy_path,
            XorCodec(),
            max_bytes=256 * 1024,
            max_age_seconds=60,
            now=self.clock,
        )
        migrated.initialize()
        with migrated._connect() as checked:  # pylint: disable=protected-access
            self.assertEqual(1, checked.execute("PRAGMA auto_vacuum").fetchone()[0])
            self.assertEqual(1, checked.execute("PRAGMA secure_delete").fetchone()[0])
            self.assertEqual(
                "preserved",
                checked.execute("SELECT value FROM legacy_canary").fetchone()[0],
            )

    def test_development_plaintext_user_column_is_rebuilt_as_keyed_tag(self):
        legacy_path = Path(self.temp.name) / "legacy-user.sqlite"
        codec = XorCodec()
        legacy = EdgeOutbox(
            legacy_path,
            codec,
            max_bytes=256 * 1024,
            max_age_seconds=60,
            now=self.clock,
        )
        legacy.initialize()
        legacy.enqueue(
            {
                "schema_version": 1,
                "source_event_type": EVENT_CONVERSATION_FINISHED,
                "context": {"user_id": "private-ha-user-uuid"},
                "data": {"conversation_id": "private"},
            }
        )
        with legacy._connect() as connection:  # pylint: disable=protected-access
            connection.execute("DROP INDEX idx_outbox_privacy_subject")
            connection.execute(
                "ALTER TABLE outbox RENAME COLUMN user_scope_tag TO user_id"
            )
            connection.execute(
                "UPDATE outbox SET user_id='private-ha-user-uuid'"
            )

        migrated = EdgeOutbox(
            legacy_path,
            codec,
            max_bytes=256 * 1024,
            max_age_seconds=60,
            now=self.clock,
        )
        migrated.initialize()
        with migrated._connect() as checked:  # pylint: disable=protected-access
            columns = {
                row[1] for row in checked.execute("PRAGMA table_info(outbox)")
            }
            self.assertIn("user_scope_tag", columns)
            self.assertNotIn("user_id", columns)
        self.assertNotIn(b"private-ha-user-uuid", legacy_path.read_bytes())
        self.assertEqual(
            1,
            migrated.suppress_privacy_subjects(set(), {"private-ha-user-uuid"}),
        )

    def test_quarantine_receipts_are_not_raw_metadata_fingerprints(self):
        metadata = {
            "latitude": -22.424242,
            "longitude": -43.131313,
            "utterance": "QUARANTINE-PRIVATE-CANARY",
        }
        self.outbox.quarantine("state_changed", "normalize_ValueError", metadata)
        self.outbox.quarantine("state_changed", "normalize_ValueError", metadata)
        conn = sqlite3.connect(self.path)
        try:
            receipts = [
                str(row[0])
                for row in conn.execute(
                    "SELECT metadata_sha256 FROM quarantine ORDER BY quarantine_id"
                ).fetchall()
            ]
        finally:
            conn.close()
        plain_digest = hashlib.sha256(
            json.dumps(metadata, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        self.assertEqual(2, len(receipts))
        self.assertEqual(2, len(set(receipts)))
        self.assertTrue(all(len(receipt) == 64 for receipt in receipts))
        self.assertNotIn(plain_digest, receipts)
        self.assertNotIn(
            b"QUARANTINE-PRIVATE-CANARY",
            self._sqlite_artifact_bytes(),
        )

    def test_expiry_preserves_sequence_as_compact_gap(self):
        self.outbox.enqueue(self._event("one"))
        self.outbox.enqueue(self._event("two"))
        self.clock.advance(61)
        stats = self.outbox.maintain()
        self.assertEqual(2, stats["expired_ranges"])
        batch = self.outbox.read_batch()
        self.assertEqual(2, len(batch.items))
        self.assertEqual((1, 2), (batch.first_sequence, batch.last_sequence))
        self.assertTrue(all(item.payload["source_event_type"] == EVENT_GAP for item in batch.items))
        self.assertTrue(all(item.payload["reason_code"] == "retention_expired" for item in batch.items))

    def test_corrupt_ciphertext_becomes_gap_not_acknowledged_data(self):
        self.outbox.enqueue(self._event("one"))
        conn = sqlite3.connect(self.path)
        try:
            with conn:
                conn.execute(
                    "UPDATE outbox SET ciphertext = X'00' WHERE sequence_start = 1"
                )
        finally:
            conn.close()
        batch = self.outbox.read_batch()
        self.assertEqual(EVENT_GAP, batch.items[0].payload["source_event_type"])
        self.assertEqual("ciphertext_unreadable", batch.items[0].payload["reason_code"])
        self.assertEqual(1, self.outbox.stats()["quarantine_rows"])

    def test_person_and_source_tracker_share_deterministic_lineage(self):
        shared_state = {
            "state": "not_home",
            "last_updated": "2026-07-11T12:00:00Z",
            "attributes": {
                "source": "device_tracker.iphone",
                "latitude": -22.4,
                "longitude": -43.1,
            },
        }
        for entity_id in ("device_tracker.iphone", "person.engineeredlighting"):
            self.outbox.enqueue({
                "schema_version": 1,
                "source_event_type": "state_changed",
                "observation_kind": "transition",
                "entity_id": entity_id,
                "occurred_at": "2026-07-11T12:00:00Z",
                "received_at": "2026-07-11T12:00:01Z",
                "context": {"id": f"context-{entity_id}"},
                "new_state": shared_state,
            })
        envelopes = self.outbox.read_batch().as_request()["envelopes"]
        self.assertEqual(envelopes[0]["root_observation_id"], envelopes[1]["root_observation_id"])
        self.assertEqual(envelopes[0]["evidence_family_id"], envelopes[1]["evidence_family_id"])
        self.assertEqual(
            "home_assistant.location:device_tracker.iphone",
            envelopes[0]["dependency_domain"],
        )
        coordinate_oracle = str(uuid.uuid5(
            uuid.NAMESPACE_URL,
            "home-agent-edge/location/device_tracker.iphone/"
            "2026-07-11T12:00:00Z/-22.4/-43.1",
        ))
        self.assertNotEqual(coordinate_oracle, envelopes[0]["root_observation_id"])

        changed_coordinates = dict(shared_state)
        changed_coordinates["attributes"] = {
            **shared_state["attributes"],
            "latitude": -22.5,
            "longitude": -43.2,
        }
        self.outbox.enqueue({
            "schema_version": 1,
            "source_event_type": "state_changed",
            "observation_kind": "transition",
            "entity_id": "device_tracker.iphone",
            "occurred_at": "2026-07-11T12:00:00Z",
            "received_at": "2026-07-11T12:00:02Z",
            "context": {"id": "contradictory-copy"},
            "new_state": changed_coordinates,
        })
        all_envelopes = self.outbox.read_batch().as_request()["envelopes"]
        self.assertEqual(
            envelopes[0]["root_observation_id"],
            all_envelopes[2]["root_observation_id"],
        )

    def test_restart_downtime_is_an_explicit_coverage_gap(self):
        self.assertIsNone(self.outbox.begin_runtime())
        self.clock.advance(30)
        self.outbox.heartbeat()
        self.clock.advance(20)
        self.assertEqual(1, self.outbox.begin_runtime())
        envelope = self.outbox.read_batch().as_request()["envelopes"][0]
        self.assertEqual("coverage_gap", envelope["event_type"])
        self.assertEqual("gap", envelope["coverage"])
        self.assertEqual("edge_unclean_restart", envelope["payload"]["reason_code"])
        self.assertLess(envelope["source_observed_at"], envelope["edge_received_at"])

        self.outbox.end_runtime()
        self.clock.advance(10)
        self.assertEqual(2, self.outbox.begin_runtime())
        envelopes = self.outbox.read_batch().as_request()["envelopes"]
        self.assertEqual(
            "edge_restart_after_clean_shutdown",
            envelopes[1]["payload"]["reason_code"],
        )


class FakeTransport:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.payloads = []

    async def send(self, payload):
        self.payloads.append(dict(payload))
        if self.error:
            raise self.error
        return self.response


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.clock = FakeClock()
        self.outbox = EdgeOutbox(
            Path(self.temp.name) / "edge.sqlite", XorCodec(),
            max_bytes=64 * 1024, max_age_seconds=60, now=self.clock,
        )
        self.outbox.initialize()
        self.outbox.enqueue(
            {
                "schema_version": 1,
                "source_event_type": "state_changed",
                "occurred_at": "2026-07-11T12:00:00Z",
                "received_at": "2026-07-11T12:00:01Z",
            }
        )

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_valid_ack_removes_only_durable_batch(self):
        key = f"{self.outbox.edge_instance_id}:home_assistant"
        transport = FakeTransport({
            "accepted": 1, "duplicates": 0, "quarantined": 0,
            "acknowledgements": {key: 1}, "opened_gaps": [],
        })
        result = await DeliveryCoordinator(self.outbox, transport).deliver_once()
        self.assertEqual("acknowledged", result.status)
        self.assertEqual(0, self.outbox.stats()["pending_rows"])
        request = transport.payloads[0]
        self.assertEqual({"envelopes"}, set(request))
        envelope = request["envelopes"][0]
        self.assertEqual(1, envelope["sequence"])
        self.assertEqual("home_assistant", envelope["source_name"])
        self.assertEqual({
            "edge_instance_id", "source_name", "epoch", "sequence",
            "event_type", "entity_id", "source_event_id", "source_observed_at",
            "edge_received_at", "payload", "root_observation_id",
            "evidence_family_id", "dependency_domain", "coverage",
            "clock_state", "ha_context", "metadata",
        }, set(envelope))
        uuid.UUID(envelope["epoch"])

    async def test_out_of_range_ack_fails_closed_and_retries(self):
        key = f"{self.outbox.edge_instance_id}:home_assistant"
        transport = FakeTransport({
            "accepted": 1, "duplicates": 0, "quarantined": 0,
            "acknowledgements": {key: 999}, "opened_gaps": [],
        })
        result = await DeliveryCoordinator(self.outbox, transport).deliver_once()
        self.assertEqual("retry_scheduled", result.status)
        self.assertEqual("ValueError", result.error_code)
        self.assertEqual(1, self.outbox.stats()["pending_rows"])
        self.assertEqual(1, self.outbox.stats()["max_attempt_count"])

    async def test_transport_disconnect_retains_batch_and_reconnect_delivers_once(self):
        disconnected = FakeTransport(error=ConnectionError("core unavailable"))
        first = await DeliveryCoordinator(self.outbox, disconnected).deliver_once()

        self.assertEqual("retry_scheduled", first.status)
        self.assertEqual("ConnectionError", first.error_code)
        self.assertEqual(1, self.outbox.stats()["pending_rows"])
        self.assertEqual(1, self.outbox.stats()["max_attempt_count"])

        key = f"{self.outbox.edge_instance_id}:home_assistant"
        reconnected = FakeTransport({
            "accepted": 1, "duplicates": 0, "quarantined": 0,
            "acknowledgements": {key: 1}, "opened_gaps": [],
        })
        # A disconnect intentionally schedules bounded backoff; reconnecting
        # must retain the batch, then deliver it once that durable delay ends.
        self.clock.advance(10)
        second = await DeliveryCoordinator(self.outbox, reconnected).deliver_once()

        self.assertEqual("acknowledged", second.status)
        self.assertEqual(1, len(disconnected.payloads))
        self.assertEqual(disconnected.payloads, reconnected.payloads)
        self.assertEqual(0, self.outbox.stats()["pending_rows"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
