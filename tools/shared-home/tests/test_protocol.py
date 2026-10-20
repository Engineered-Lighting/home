"""Cross-component tests use a fake engine and explicit fake codec, no network."""
import base64
import json
from pathlib import Path
import tempfile
import unittest

from home_shared.adapter import CurrentContext, VictoriaAdapter
from home_shared.admission import AdmissionController, AdmissionLedger, ResourceManifest
from home_shared.gateway import Gateway, jpeg_dimensions
from home_shared.protocol import FrigateWire, camera_frame, finish_observation, inference_payload
from test_admission import FakeVerifier, manifest_fixture
from test_gateway import FakeBackend, binding_fixture, header_fixture


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.adapter = VictoriaAdapter()
        self.wire = FrigateWire(self.adapter)
        self.adapter.connect(clean_session=True)
        self.adapter.subscription_ack(self.adapter.subscriptions, success=True)
        for mono in (0, 5):
            self.adapter.health(camera_session="capture", frigate_instance="f", healthy=True,
                                checked_mono=mono, now_mono=mono)
            self.adapter.request_reconciliation(mono)
            self.wire.receive("frigate/camera_activity", b'{"den_oak":{"objects":[{"label":"person"}]}}',
                              retained=False, duplicate=False, now_mono=mono)
            self.adapter.tick(mono, 1000 + mono)
        self.work = self.adapter.take_work(5)

    def frame(self, **changes):
        metadata = {"schema": 1, "session": "capture", "sequence": 7, "width": 640, "height": 360, "quality": 85,
                    "source_clock": "depthai_host_monotonic", "observed_monotonic_seconds": 5,
                    "monotonic_clock_scope": "python_monotonic_on_camera_host", "observed_at_unix_ns": 1005_000_000_000,
                    "timestamp_semantics": "commissioned_source_frame_timestamp_mapping",
                    "uncertainty_seconds": .1, "validation_id": "fixture-map"}
        metadata.update(changes)
        header = base64.urlsafe_b64encode(json.dumps(metadata).encode()).decode()
        return camera_frame(header_fixture(), header)

    def test_pipeline_preserves_frame_identity_and_absolute_deadlines(self):
        payload = inference_payload(self.adapter, self.frame(schema=2, timing_generation=0,
                                    exposure_duration_seconds=.01), device_id="den-adapter", configuration_revision="rev-1",
                                    max_output_tokens=128, now_mono=5.2, now_unix=1005.2, cross_host_uncertainty=.2)
        with tempfile.TemporaryDirectory() as temp:
            with AdmissionLedger(Path(temp) / "ledger.sqlite") as ledger:
                controller = AdmissionController(ledger, ResourceManifest.from_dict(manifest_fixture()), FakeVerifier(),
                                                 clock=lambda: 1006)
                controller.enable("fixture-epoch")
                gateway = Gateway(controller, FakeBackend(controller), clock=lambda: 1006, image_validator=jpeg_dimensions)
                queued = gateway.submit(payload, binding_fixture())
                self.assertIsNone(finish_observation(self.adapter, self.work, queued, now_mono=6, now_unix=1006))
                self.assertIsNotNone(self.adapter.active)
                result = gateway.dispatch_once()
                observation = finish_observation(self.adapter, self.work, result, now_mono=6, now_unix=1006)
                self.assertIsNotNone(observation)
                self.assertEqual(observation["valid_until"], 1185)
                self.assertEqual(observation["capture_session"], "capture")
                self.assertEqual(observation["frame_id"], "g0-s7")
                self.assertEqual(observation["authority"], "untrusted_observation")
                self.assertAlmostEqual(observation["uncertainty_seconds"], .3)
                self.assertFalse(self.wire.publication(observation)[2])
                consumer = CurrentContext("victoria", "den_oak")
                consumer.availability(self.adapter.session, self.adapter.generation, True, 6)
                self.assertTrue(consumer.accept(observation, now_unix=1006, now_mono=6))
                self.assertIsNone(consumer.current(now_unix=1185, now_mono=185))

    def test_timing_generation_namespaces_frame_identity(self):
        legacy = self.frame()
        initial = self.frame(schema=2, timing_generation=0, exposure_duration_seconds=.01)
        reset = self.frame(schema=2, timing_generation=1, exposure_duration_seconds=.01)
        self.assertEqual(len({legacy.frame_id, initial.frame_id, reset.frame_id}), 3)
        self.assertEqual(reset.capture_session, initial.capture_session)
        self.assertEqual(reset.observed_at, initial.observed_at)

    def test_generation_and_exposure_are_required_in_new_schema(self):
        for changes in ({'timing_generation': None}, {'timing_generation': True},
                        {'timing_generation': -1}, {'timing_generation': 2**63},
                        {'exposure_duration_seconds': None}, {'exposure_duration_seconds': 0},
                        {'exposure_duration_seconds': .2}, {'exposure_duration_seconds': float('nan')}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.frame(**dict({'schema': 2, 'timing_generation': 0,
                                   'exposure_duration_seconds': .01}, **changes))
        with self.assertRaises(ValueError):
            self.frame(schema=1, timing_generation=1)

    def test_wrong_frame_and_generation_results_never_publish(self):
        payload = inference_payload(self.adapter, self.frame(), device_id="den-adapter", configuration_revision="rev-1",
                                    max_output_tokens=128, now_mono=5.2, now_unix=1005.2, cross_host_uncertainty=.1)
        self.adapter.health(camera_session="capture", frigate_instance="f", healthy=True,
                            checked_mono=6, now_mono=6, timing_generation=1)
        response = {"request_id": self.work.request_id, "site_id": "victoria", "status": "terminal", "outcome": "completed", "observation": {
            "site_id": "victoria", "camera_id": "den_oak", "room_id": "den",
            "producer_session": payload["producer_session"], "producer_sequence": payload["producer_sequence"],
            "occupancy_generation": payload["occupancy_generation"], "capture_session": "capture", "frame_id": "7",
            "observed_at": 1005, "valid_until": 1185, "description": "old result", "uncertainty_seconds": .2}}
        self.assertIsNone(finish_observation(self.adapter, self.work, response, now_mono=6, now_unix=1006))
        topic, value, retain = self.wire.last_will()
        self.assertFalse(retain)
        self.assertFalse(json.loads(value)["online"])

    def test_frame_from_new_generation_cannot_use_old_health(self):
        with self.assertRaisesRegex(ValueError, 'timing generation'):
            inference_payload(self.adapter, self.frame(schema=2, timing_generation=1,
                exposure_duration_seconds=.01), device_id='den-adapter', configuration_revision='rev-1',
                max_output_tokens=128, now_mono=5.2, now_unix=1005.2, cross_host_uncertainty=.1)

    def test_only_fixed_mqtt_inputs_and_missing_snapshot_is_unknown(self):
        self.assertFalse(self.wire.receive("ha/light/set", b"on", retained=False, duplicate=False, now_mono=6))
        self.adapter.count_hint()
        self.adapter.request_reconciliation(6)
        self.assertFalse(self.wire.receive("frigate/camera_activity", b'{"den_oak":{}}',
                                          retained=False, duplicate=False, now_mono=6))
        self.assertEqual(self.adapter.state, "unknown")


if __name__ == "__main__":
    unittest.main()
