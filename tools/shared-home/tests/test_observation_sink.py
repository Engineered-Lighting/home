"""Trusted sink integration fixtures; no network, camera hardware or model."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from home_shared.adapter import CurrentContext, Frame, VictoriaAdapter
from home_shared.observations import SourceBinding, VictoriaObservationStore
from home_shared.observation_sink import ObservationSink
from home_shared.protocol import FrigateWire, inference_payload
from test_gateway import header_fixture


class ObservationSinkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = 1006.0
        self.store = VictoriaObservationStore(
            Path(self.temp.name) / "observations.sqlite",
            SourceBinding("governed-owner", "registered-den-source", {"den_oak": "den"}),
            clock=lambda: self.now)
        self.addCleanup(self.store.close)
        self.adapter = VictoriaAdapter()
        self.sink = ObservationSink(self.adapter, self.store)
        self.wire = FrigateWire(self.adapter)
        self.adapter.connect(clean_session=True)
        self.adapter.subscription_ack(self.adapter.subscriptions, success=True)
        for mono in (0, 5):
            self.adapter.health(camera_session="capture", frigate_instance="frigate",
                                healthy=True, checked_mono=mono, now_mono=mono)
            self.adapter.request_reconciliation(mono)
            self.wire.receive("frigate/camera_activity", b'{"den_oak":{"objects":[{"label":"person"}]}}',
                              retained=False, duplicate=False, now_mono=mono)
            self.adapter.tick(mono, 1000 + mono)
        self.work = self.adapter.take_work(5)
        frame = Frame("capture", "7", 1005, 5, .1, "fixture-clock-map", header_fixture())
        payload = inference_payload(self.adapter, frame, device_id="den-adapter",
                                    configuration_revision="config-1", max_output_tokens=128,
                                    now_mono=5.2, now_unix=1005.2, cross_host_uncertainty=.2)
        self.response = {
            "request_id": self.work.request_id, "site_id": "victoria",
            "status": "terminal", "outcome": "completed", "observation": {
                "site_id": "victoria", "camera_id": "den_oak", "room_id": "den",
                "producer_session": payload["producer_session"],
                "producer_sequence": payload["producer_sequence"],
                "occupancy_generation": payload["occupancy_generation"],
                "capture_session": "capture", "frame_id": "7", "observed_at": 1005,
                "valid_until": 1185, "uncertainty_seconds": payload["clock_uncertainty_seconds"],
                "description": "A person is seated in the den.",
                "observations": [{"label": "person", "count": 1}],
                "model_revision": "fixture-model-revision",
                "configuration_revision": "config-1",
                "request_deadline": self.work.request_deadline,
            },
        }

    def finish(self, response=None, mono=6, unix=1006):
        return self.sink.finish(self.work, self.response if response is None else response,
                                now_mono=mono, now_unix=unix)

    def count(self):
        return self.store.db.execute("SELECT count(*) FROM observation_record").fetchone()[0]

    def test_persists_before_fixed_non_retained_publication_and_current_context(self):
        original = FrigateWire.publication

        def publication(observation):
            self.assertEqual(self.count(), 1)
            return original(observation)

        with patch.object(FrigateWire, "publication", side_effect=publication):
            topic, payload, retained = self.finish()
        self.assertEqual(topic, "home/shared/v1/victoria/den_oak/observation")
        self.assertFalse(retained)
        public = json.loads(payload)
        self.assertEqual(public["source_instance"], "registered-den-source")
        self.assertEqual(public["authority"], "untrusted_observation")
        row = self.store.db.execute("SELECT record_id FROM observation_record").fetchone()
        stored = self.store.read(row[0])
        self.assertEqual(stored.observation.producer_session, self.work.adapter_session)
        self.assertEqual(stored.observation.producer_sequence, self.work.sequence)
        self.assertEqual(stored.observation.capture_session, "capture")
        self.assertEqual(stored.observation.frame_id, "7")
        self.assertEqual(stored.observation.observed_at, 1005)
        self.assertEqual(stored.observation.received_at, 1006)
        self.assertEqual(stored.observation.observations, ({"label": "person", "count": 1},))
        provenance = stored.observation.provenance
        self.assertEqual(provenance.version, 1)
        self.assertEqual(provenance.model_revision, "fixture-model-revision")
        self.assertEqual(provenance.configuration_revision, "config-1")
        self.assertEqual(provenance.clock_mapping_revision, "fixture-clock-map")
        self.assertAlmostEqual(provenance.uncertainty_seconds, .3)
        self.assertEqual(provenance.scheduled_at, self.work.scheduled_at)
        self.assertEqual(provenance.request_deadline, self.work.request_deadline)
        self.assertEqual(provenance.valid_until, 1185)
        self.assertIsNone(self.adapter.active_frame)
        context = CurrentContext("victoria", "den_oak")
        context.availability(self.adapter.session, self.adapter.generation, True, 6)
        self.assertTrue(context.accept(public, now_unix=1006, now_mono=6))
        self.assertIsNone(context.current(now_unix=1185, now_mono=185))

    def test_queued_or_indeterminate_response_keeps_active_without_storage(self):
        for status in ("queued", "running", "indeterminate"):
            response = deepcopy(self.response)
            response["status"] = status
            self.assertIsNone(self.finish(response))
            self.assertEqual(self.adapter.active, self.work)
        self.assertEqual(self.count(), 0)

    def test_wrong_site_outer_envelope_does_not_consume_active(self):
        response = deepcopy(self.response)
        response["site_id"] = "echo"
        self.assertIsNone(self.finish(response))
        self.assertEqual(self.adapter.active, self.work)
        self.assertEqual(self.count(), 0)

    def test_wrong_site_observation_never_persists(self):
        self.response["observation"]["site_id"] = "echo"
        self.assertIsNone(self.finish())
        self.assertIsNone(self.adapter.active)
        self.assertEqual(self.count(), 0)

    def test_wrong_frame_observation_never_persists(self):
        self.response["observation"]["frame_id"] = "wrong"
        self.assertIsNone(self.finish())
        self.assertIsNone(self.adapter.active)
        self.assertEqual(self.count(), 0)

    def test_stale_terminal_result_is_consumed_without_publishing(self):
        self.now = 1020
        self.assertIsNone(self.finish(mono=20, unix=1020))
        self.assertIsNone(self.adapter.active)
        self.assertEqual(self.count(), 0)

    def test_health_generation_change_drops_terminal_result(self):
        self.adapter.unavailable()
        self.assertIsNone(self.finish())
        self.assertEqual(self.count(), 0)

    def test_terminal_duplicate_does_not_republish_or_change_received_time(self):
        first = self.finish()
        self.assertIsNotNone(first)
        self.now = 1007
        self.assertIsNone(self.finish(mono=7, unix=1007))
        self.assertEqual(self.count(), 1)
        row = self.store.db.execute("SELECT record_id FROM observation_record").fetchone()
        self.assertEqual(self.store.read(row[0]).observation.received_at, 1006)

    def test_storage_failure_returns_no_publication_or_retry(self):
        with patch.object(self.store, "ingest", side_effect=sqlite3.OperationalError("disk full")), \
                patch.object(FrigateWire, "publication") as publication:
            with self.assertRaises(sqlite3.OperationalError):
                self.finish()
            publication.assert_not_called()
        self.assertIsNone(self.adapter.active)
        self.assertIsNone(self.finish())
        self.assertEqual(self.count(), 0)

    def test_malformed_structured_observations_never_publish(self):
        self.response["observation"]["observations"] = {"action": "turn on lights"}
        with patch.object(FrigateWire, "publication") as publication:
            with self.assertRaises(ValueError):
                self.finish()
            publication.assert_not_called()
        self.assertIsNone(self.adapter.active)
        self.assertEqual(self.count(), 0)

    def test_failed_terminal_work_does_not_store_observation_body(self):
        self.response["outcome"] = "failed"
        self.assertIsNone(self.finish())
        self.assertIsNone(self.adapter.active)
        self.assertEqual(self.count(), 0)

    def test_model_source_and_extra_keys_cannot_replace_provisioned_binding(self):
        self.response["observation"].update(source_instance="echo-private-memory",
                                             owner_id="another-owner", arbitrary_service="light.turn_on")
        _, payload, _ = self.finish()
        public = json.loads(payload)
        self.assertEqual(public["source_instance"], "registered-den-source")
        self.assertNotIn("owner_id", public)
        self.assertNotIn("arbitrary_service", public)
        row = self.store.db.execute("SELECT record_id FROM observation_record").fetchone()
        self.assertTrue(row[0].startswith("victoria/registered-den-source/"))

    def test_source_mapping_and_deadline_use_captured_local_handles(self):
        self.response["observation"].update(clock_mapping_revision="model-chosen-map",
                                             request_deadline=9999, scheduled_at=9000)
        _, payload, _ = self.finish()
        self.assertEqual(json.loads(payload)["request_deadline"], self.work.request_deadline)
        row = self.store.db.execute("SELECT record_id FROM observation_record").fetchone()
        provenance = self.store.read(row[0]).observation.provenance
        self.assertEqual(provenance.clock_mapping_revision, "fixture-clock-map")
        self.assertEqual(provenance.request_deadline, self.work.request_deadline)
        self.assertEqual(provenance.scheduled_at, self.work.scheduled_at)

    def test_missing_legacy_gateway_revisions_stay_unknown(self):
        self.response["observation"].pop("model_revision")
        self.response["observation"].pop("configuration_revision")
        self.finish()
        row = self.store.db.execute("SELECT record_id FROM observation_record").fetchone()
        provenance = self.store.read(row[0]).observation.provenance
        self.assertIsNone(provenance.model_revision)
        self.assertIsNone(provenance.configuration_revision)
        self.assertEqual(provenance.clock_mapping_revision, "fixture-clock-map")

    def test_invalid_gateway_provenance_cannot_publish_or_persist(self):
        self.response["observation"]["model_revision"] = "bad\nrevision"
        with patch.object(FrigateWire, "publication") as publication:
            with self.assertRaises(ValueError):
                self.finish()
            publication.assert_not_called()
        self.assertEqual(self.count(), 0)
        self.assertIsNone(self.adapter.active_frame)

    def test_mismatched_store_camera_is_rejected_before_consuming_work(self):
        other = VictoriaObservationStore(Path(self.temp.name) / "other.sqlite",
                                        SourceBinding("governed-owner", "other-source", {"other_camera": "den"}),
                                        clock=lambda: self.now)
        self.addCleanup(other.close)
        with self.assertRaises(ValueError):
            ObservationSink(self.adapter, other)
        self.assertEqual(self.adapter.active, self.work)


if __name__ == "__main__":
    unittest.main()
