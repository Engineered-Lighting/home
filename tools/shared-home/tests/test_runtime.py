from dataclasses import replace
from contextlib import closing
import base64
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from home_shared.adapter import Frame, VictoriaAdapter
from home_shared.admission import canonical_digest
from home_shared.mqtt_transport import MqttBusy, MqttEvent
from home_shared.observations import SourceBinding, VictoriaObservationStore
from home_shared.runtime import HealthSample, RuntimeSettings, VictoriaRuntime
from home_shared.submissions import SubmissionJournal
from test_gateway import header_fixture


class FakeMqtt:
    def __init__(self, clock, adapter_session):
        self.clock = clock
        self.adapter_session = adapter_session
        self.events, self.published = [], []
        self.generation = 0
        self.busy_observations = False
        self.people = True

    def emit(self, kind, **payload):
        self.events.append(MqttEvent(kind, {"generation": self.generation, "received_mono": self.clock(), **payload}))

    def start(self):
        self.generation += 1
        self.emit("connected", clean_session=True)
        self.emit("subscribed", topics=VictoriaAdapter().subscriptions, success=True)

    def stop(self):
        pass

    def drain(self, max_events=64):
        events, self.events = self.events[:max_events], self.events[max_events:]
        return events

    def publish(self, topic, payload, retain=False):
        if self.busy_observations and topic.endswith("/observation"):
            raise MqttBusy("pending")
        self.published.append((topic, payload, retain))
        if topic == "frigate/onConnect":
            objects = [{"label": "person"}] if self.people else []
            self.emit("message", topic="frigate/camera_activity", payload=json.dumps({"den_oak": {"objects": objects}}).encode(),
                      retained=False, duplicate=False)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = 0.0
        self.healthy = True
        self.camera_calls, self.submits, self.lookups = [], [], []
        self.submit_ambiguous = self.lookup_ambiguous = self.camera_fails = False
        self.lookup_site = "victoria"
        self.reject = None
        self.payload = None
        self.journal_path = Path(self.temp.name) / "journal.sqlite"
        self.journal = self.open_journal()
        self.store = VictoriaObservationStore(Path(self.temp.name) / "observations.sqlite",
                                             SourceBinding("owner", "den-source", {"den_oak": "den"}),
                                             clock=lambda: 1000 + self.now)
        self.addCleanup(self.store.close)
        self.addCleanup(lambda: self.journal.close())
        self.runtime = self.make_runtime()
        self.addCleanup(lambda: self.runtime.close(wait_seconds=1))

    def open_journal(self):
        return SubmissionJournal(self.journal_path, device_id="den-adapter", gateway_identity="ingress",
                                 max_records=100, free_space_floor_bytes=0)

    def make_runtime(self):
        owner = self
        class Camera:
            def fetch(self, work, **kwargs):
                owner.camera_calls.append(work)
                if owner.camera_fails:
                    raise OSError("camera failed")
                return Frame("capture", str(work.sequence), 1000 + owner.now, owner.now, .1, "clock-map", header_fixture())
        class Gateway:
            config = SimpleNamespace(device_id="den-adapter", cameras={"den_oak": "den"}, configuration_revision="config-1")
            gateway_identity = "ingress"
            def submit(self, payload):
                with closing(sqlite3.connect(owner.journal_path)) as observer:
                    assert observer.execute("SELECT request_id FROM submissions WHERE state!='terminal'").fetchone()[0] == payload["request_id"]
                owner.submits.append(payload["request_id"])
                owner.payload = dict(payload)
                if owner.submit_ambiguous:
                    raise TimeoutError("acceptance unknown")
                if owner.reject == "post":
                    return owner.rejection(payload)
                return {"version": 1, "site_id": "victoria", "request_id": payload["request_id"], "status": "queued", "outcome": None}
            def status(self, request_id):
                owner.lookups.append(request_id)
                if owner.lookup_ambiguous:
                    raise OSError("not found or unavailable")
                p = owner.payload
                if owner.reject == "lookup":
                    return owner.rejection(p)
                observation = {key: p[key] for key in ("site_id", "device_id", "camera_id", "room_id", "producer_session",
                                                       "producer_sequence", "occupancy_generation", "capture_session", "frame_id")}
                observation.update(observed_at=p["captured_at"], valid_until=p["captured_at"] + 180,
                                   uncertainty_seconds=p["clock_uncertainty_seconds"], description="Person in den.",
                                   model_revision="fixture-model", configuration_revision=p["configuration_revision"])
                return {"version": 1, "site_id": owner.lookup_site, "request_id": request_id,
                        "status": "terminal", "outcome": "completed", "observation": observation}
        adapter = VictoriaAdapter()
        self.mqtt = FakeMqtt(lambda: self.now, adapter.session)
        return VictoriaRuntime(adapter=adapter, mqtt=self.mqtt, camera=Camera(), gateway=Gateway(), journal=self.journal,
                               store=self.store, settings=RuntimeSettings("den-adapter", "config-1", 128),
                               health_reader=lambda: HealthSample("capture", "frigate", self.now, self.healthy, .2, self.now + 5),
                               clock=lambda: 1000 + self.now, monotonic=lambda: self.now)

    @staticmethod
    def rejection(payload):
        return {"version": 1, "site_id": "victoria", "request_id": payload["request_id"],
                "status": "terminal", "outcome": "rejected_capacity",
                "rejection": {"version": 1, "device_id": payload["device_id"],
                              "request_digest": canonical_digest(payload), "reason": "capacity"}}

    def steps(self, now=None, times=1):
        if now is not None:
            self.now = now
        for _ in range(times):
            self.runtime.step()
            if self.runtime._job:
                self.runtime._job[3].join(timeout=1)

    def submit_first(self):
        self.steps(0, 4)
        self.steps(5, 3)
        self.assertEqual(len(self.submits), 1)

    def test_explicit_empty_room_request_uses_existing_journal_and_publication(self):
        self.mqtt.people = False
        self.steps(0, 4)
        work = self.runtime.request_refresh()
        self.steps(1, 6)
        self.steps(3, 4)
        self.assertEqual(self.submits, [work.request_id])
        self.assertEqual(self.payload["task_class"], "interactive")
        self.assertEqual(len(self.publications()), 1)
        value = json.loads(self.publications()[0])
        self.assertEqual(value["request_id"], work.request_id)
        self.assertEqual(value["site_id"], "victoria")
        self.assertIsNone(self.journal.outstanding())

    def test_explicit_request_cannot_open_closed_admission(self):
        self.steps(0, 4)
        self.runtime.admission_closed = True
        with self.assertRaises(ValueError):
            self.runtime.request_refresh()
        self.assertEqual(self.camera_calls, [])

    def test_durable_refresh_duplicate_uses_same_request_and_deadline(self):
        self.mqtt.people = False
        self.steps(0, 4)
        request_id = "01234567-1234-4234-8234-0123456789ab"
        principal = "a" * 64
        first = self.runtime.accept_refresh(principal, request_id)
        self.now = 1
        self.assertEqual(first, self.runtime.accept_refresh(principal, request_id))
        self.assertEqual(self.runtime.adapter.pending.request_id, request_id)
        self.assertIsNone(self.journal.refresh_status("b" * 64, request_id))
        with self.assertRaises(ValueError):
            self.runtime.accept_refresh("b" * 64, request_id)
        self.steps(1, 6)
        self.steps(3, 4)
        self.assertEqual(self.submits, [request_id])
        self.assertEqual(self.runtime.accept_refresh(principal, request_id)["outcome"], "completed")

    def publications(self):
        return [v for topic, v, retain in self.mqtt.published if topic.endswith("/observation")]

    def use_causal_frames(self):
        from PIL import Image
        from home_shared.protocol import camera_frame
        from test_causal_protocol import CausalProtocolTests
        fixture = CausalProtocolTests()
        image = io.BytesIO()
        Image.new("RGB", (640, 360)).save(image, format="JPEG", quality=85)
        fixture.jpeg = image.getvalue()
        self.causal_health_changes, self.causal_frame_changes = {}, {}
        boot = "12345678-1234-1234-1234-123456789abc"
        self.runtime.health_reader = lambda: replace(HealthSample(
            "capture", "frigate", self.now, self.healthy, .2, self.now + 5, 2, 2,
            boot, "independent-aggregate-clock", .2), **self.causal_health_changes)
        def fetch(work, **kwargs):
            self.camera_calls.append(work)
            receipt = fixture.receipt(
                observed_monotonic_seconds=self.now - .1,
                observed_at_unix_ns=round((1000 + self.now - .1) * 1e9),
                host_send_monotonic_ns=round((self.now - .2) * 1e9),
                host_receive_monotonic_ns=round(self.now * 1e9))
            receipt.update(self.causal_frame_changes)
            return camera_frame(fixture.jpeg, base64.urlsafe_b64encode(json.dumps(receipt).encode()).decode())
        self.runtime.camera.fetch = fetch

    def test_real_schema3_frame_preserves_context_and_counts_camera_clock_once(self):
        self.use_causal_frames()
        self.submit_first()
        self.assertAlmostEqual(self.payload["clock_uncertainty_seconds"], .1 + .01 + .000001 + .2 + .2)
        self.assertEqual(self.payload["frame_id"], "c2-s7")
        self.steps(7, 3)
        self.assertEqual(len(self.publications()), 1)

    def test_schema3_rejects_wrong_boot_and_legacy_evidence(self):
        self.use_causal_frames()
        self.causal_frame_changes["host_boot_id"] = "aaaaaaaa-1234-1234-1234-123456789abc"
        self.steps(0, 4)
        self.steps(5, 3)
        self.assertEqual(self.submits, [])
        self.assertIsNone(self.journal.outstanding())

    def test_schema3_cannot_use_v1_health_even_with_matching_capture_session(self):
        self.use_causal_frames()
        self.causal_health_changes.update(clock_evidence_version=1, camera_host_uncertainty_seconds=None)
        self.steps(0, 4)
        self.steps(5, 3)
        self.assertEqual(self.submits, [])

    def test_legacy_frame_cannot_use_host_only_health(self):
        legacy_fetch = self.runtime.camera.fetch
        self.use_causal_frames()
        self.runtime.camera.fetch = legacy_fetch
        self.steps(0, 4)
        self.steps(5, 3)
        self.assertEqual(self.submits, [])

    def test_host_bound_increase_after_journal_prepare_prevents_post(self):
        self.use_causal_frames()
        prepare = self.journal.prepare
        def changed(payload):
            result = prepare(payload)
            self.causal_health_changes["camera_host_uncertainty_seconds"] = .3
            return result
        self.journal.prepare = changed
        self.steps(0, 4)
        self.steps(5, 4)
        self.assertEqual(self.submits, [])
        self.assertIsNone(self.journal.outstanding())

    def test_same_session_new_boot_invalidates_inflight_result(self):
        self.use_causal_frames()
        self.submit_first()
        generation = self.runtime.adapter.generation
        self.causal_health_changes["camera_boot_id"] = "aaaaaaaa-1234-1234-1234-123456789abc"
        self.steps(7, 3)
        self.assertGreater(self.runtime.adapter.generation, generation)
        self.assertIsNone(self.journal.outstanding())
        self.assertEqual(self.publications(), [])

    def test_larger_la_error_rejects_inflight_result_without_retry(self):
        self.use_causal_frames()
        self.submit_first()
        self.causal_health_changes["cross_host_uncertainty"] = .3
        self.steps(7, 3)
        self.assertIsNone(self.journal.outstanding())
        self.assertEqual(self.publications(), [])
        self.assertEqual(len(self.submits), 1)

    def test_clock_change_while_mqtt_busy_drops_pending_caption(self):
        self.use_causal_frames()
        self.mqtt.busy_observations = True
        self.submit_first()
        self.steps(7, 3)
        self.assertIsNotNone(self.runtime._pending_publication)
        self.causal_health_changes["camera_host_uncertainty_seconds"] = .3
        self.mqtt.busy_observations = False
        self.steps(8)
        self.assertIsNone(self.runtime._pending_publication)
        self.assertEqual(self.publications(), [])

    def test_terminal_accounting_delay_past_request_expiry_does_not_store(self):
        self.use_causal_frames()
        self.submit_first()
        accept = self.journal.accept_status
        def delayed(result):
            accepted = accept(result)
            self.now = 21
            return accepted
        self.journal.accept_status = delayed
        self.steps(7, 3)
        self.assertIsNone(self.journal.outstanding())
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM observation_record").fetchone()[0], 0)
        self.assertEqual(self.publications(), [])

    def test_terminal_accounting_delay_past_clock_expiry_does_not_store(self):
        self.use_causal_frames()
        self.submit_first()
        frozen = self.runtime.health_reader()
        self.runtime.health_reader = lambda: frozen
        accept = self.journal.accept_status
        def delayed(result):
            accepted = accept(result)
            self.now = 11
            return accepted
        self.journal.accept_status = delayed
        self.steps(7, 3)
        self.assertIsNone(self.journal.outstanding())
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM observation_record").fetchone()[0], 0)
        self.assertEqual(self.publications(), [])

    def test_real_state_engines_join_camera_gateway_store_and_publication(self):
        self.submit_first()
        self.steps(7, 3)
        self.assertEqual(len(self.publications()), 1)
        result = json.loads(self.publications()[0])
        self.assertEqual(result["source_instance"], "den-source")
        self.assertEqual(result["valid_until"], 1185)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM observation_record").fetchone()[0], 1)
        self.assertIsNone(self.journal.outstanding())
        self.assertEqual(self.runtime.counters["observation_stored"], 1)

    def test_ambiguous_post_looks_up_original_without_resend(self):
        self.submit_ambiguous = True
        self.submit_first()
        self.steps(7, 3)
        self.assertEqual(self.lookups, self.submits)
        self.assertEqual(len(self.publications()), 1)

    def test_unknown_status_and_expiry_keep_admission_closed_but_mqtt_alive(self):
        self.lookup_ambiguous = True
        self.submit_first()
        for now in range(7, 131):
            self.steps(now, 2)
        self.assertEqual(len(self.submits), 1)
        self.assertEqual(len(self.camera_calls), 1)
        self.assertIsNotNone(self.journal.outstanding())
        self.assertEqual(self.publications(), [])
        self.assertGreater(len(self.lookups), 2)
        self.assertGreater(len([p for p in self.mqtt.published if p[0].endswith("/availability")]), 10)

    def test_restart_reconciles_old_request_without_replaying_its_caption(self):
        self.submit_first()
        old_id = self.submits[0]
        self.assertTrue(self.runtime.close(wait_seconds=1))
        self.journal.close()
        self.journal = self.open_journal()
        self.runtime = self.make_runtime()
        self.steps(6, 4)
        self.assertIn(old_id, self.lookups)
        self.assertEqual(len(self.submits), 1)
        self.assertEqual(self.publications(), [])
        self.assertEqual(self.runtime.counters["recovered_terminal"], 1)

    def test_wrong_site_terminal_cannot_release_or_publish(self):
        self.lookup_site = "echo"
        self.submit_first()
        self.steps(7, 3)
        self.assertIsNotNone(self.journal.outstanding())
        self.assertEqual(self.publications(), [])

    def test_health_loss_during_inference_rejects_late_caption(self):
        self.submit_first()
        self.healthy = False
        self.steps(7, 3)
        self.assertIsNone(self.journal.outstanding())
        self.assertEqual(self.publications(), [])
        self.assertEqual(self.runtime.adapter.state, "unknown")

    def test_timing_revocation_during_inference_accounts_without_publication(self):
        self.submit_first()
        self.runtime.health_reader = lambda: HealthSample(
            'capture', 'frigate', self.now, True, .2, self.now + 5, timing_generation=1)
        self.steps(7, 3)
        self.assertIsNone(self.journal.outstanding())
        self.assertEqual(self.publications(), [])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM observation_record').fetchone()[0], 0)
        self.assertEqual(len(self.submits), 1)

    def test_camera_failure_never_prepares_or_submits(self):
        self.camera_fails = True
        self.steps(0, 4)
        self.steps(5, 3)
        self.assertEqual(self.submits, [])
        self.assertIsNone(self.journal.outstanding())
        self.assertIsNone(self.runtime.adapter.active)

    def test_journal_storage_failure_prevents_first_upstream_byte(self):
        self.journal.free_space_floor_bytes = 2**100
        self.steps(0, 4)
        self.steps(5, 3)
        self.assertEqual(self.submits, [])
        self.assertTrue(self.runtime.admission_closed)

    def test_observation_storage_failure_does_not_publish_or_resubmit(self):
        self.submit_first()
        with patch.object(self.store, "ingest", side_effect=OSError("disk full")):
            self.steps(7, 3)
        self.assertIsNone(self.journal.outstanding())
        self.assertEqual(self.publications(), [])
        self.assertTrue(self.runtime.storage_failed)
        self.assertEqual(len(self.submits), 1)

    def test_busy_publication_expires_without_delivery_or_retry_of_inference(self):
        self.mqtt.busy_observations = True
        self.submit_first()
        self.steps(7, 3)
        self.assertIsNotNone(self.runtime._pending_publication)
        self.steps(20)
        self.mqtt.busy_observations = False
        self.steps(21)
        self.assertEqual(self.publications(), [])
        self.assertIsNone(self.runtime._pending_publication)
        self.assertEqual(len(self.submits), 1)

    def test_delayed_input_and_old_connection_cannot_reconcile_new_session(self):
        self.steps(0, 4)
        old = MqttEvent("message", {"generation": 0, "received_mono": 5, "topic": "frigate/camera_activity",
                                  "payload": b'{"den_oak":{"objects":[]}}', "retained": False, "duplicate": False})
        self.mqtt.events.append(old)
        self.steps(5)
        self.assertNotEqual(self.runtime.adapter.people, 0)
        stale = replace(old, payload={**old.payload, "generation": self.mqtt.generation, "received_mono": -10})
        self.mqtt.events.append(stale)
        self.steps(6)
        self.assertEqual(self.runtime.adapter.state, "unknown")

    def test_shutdown_retains_process_fence_while_submission_worker_is_live(self):
        entered, release = threading.Event(), threading.Event()
        original = self.runtime.gateway.submit
        def blocked(payload):
            entered.set()
            release.wait(timeout=3)
            return original(payload)
        self.runtime.gateway.submit = blocked
        self.steps(0, 4)
        self.steps(5, 2)
        self.assertTrue(entered.wait(timeout=1))
        try:
            self.assertFalse(self.runtime.close())
            from home_shared.admission import LedgerLocked
            with self.assertRaises(LedgerLocked):
                self.open_journal()
        finally:
            release.set()
        self.assertTrue(self.runtime.close(wait_seconds=1))
        self.assertIsNotNone(self.journal.outstanding())

    def test_retention_runs_even_when_inference_remains_unresolved(self):
        self.lookup_ambiguous = True
        self.submit_first()
        with patch.object(self.store, "prune", wraps=self.store.prune) as prune:
            self.steps(60, 2)
            self.assertEqual(prune.call_count, 1)
        self.assertIsNotNone(self.journal.outstanding())

    def test_slow_pruning_cannot_prepare_a_frame_after_its_deadline(self):
        self.steps(0, 4)
        self.steps(5)  # camera job has completed, but no POST is prepared
        self.runtime._next_prune = 6
        def slow_prune(**kwargs):
            self.now = 21
            return 0
        with patch.object(self.store, "prune", side_effect=slow_prune):
            self.steps(6)
        self.assertEqual(self.submits, [])
        self.assertIsNone(self.journal.outstanding())
        self.assertEqual(self.journal.db.execute("SELECT count(*) FROM submissions").fetchone()[0], 0)

    def test_locally_proven_not_sent_closes_preparation_without_lookup_or_resend(self):
        from home_shared.gateway_client import GatewayNotSent
        def not_sent(_payload):
            raise GatewayNotSent("local validation failed before worker launch")
        self.runtime.gateway.submit = not_sent
        self.steps(0, 4)
        self.steps(5, 3)
        self.assertIsNone(self.journal.outstanding())
        self.assertEqual(self.lookups, [])
        self.assertEqual(self.journal.db.execute("SELECT outcome FROM submissions").fetchone()[0], "client_not_sent")

    def test_mismatched_gateway_config_or_will_fails_before_any_work(self):
        for field, value in (("device_id", "other"), ("configuration_revision", "changed"), ("cameras", {"other": "den"})):
            original = getattr(self.runtime.gateway.config, field)
            setattr(self.runtime.gateway.config, field, value)
            try:
                with self.assertRaises(ValueError):
                    VictoriaRuntime(adapter=self.runtime.adapter, mqtt=self.mqtt, camera=self.runtime.camera,
                                    gateway=self.runtime.gateway, journal=self.journal, store=self.store,
                                    settings=self.runtime.settings, health_reader=self.runtime.health_reader)
            finally:
                setattr(self.runtime.gateway.config, field, original)
        self.assertEqual(self.camera_calls, [])
        self.assertIsNone(self.journal.outstanding())

    def test_runtime_through_actual_https_client_persists_bound_terminal_result(self):
        from test_gateway_client import GatewayClient, LocalTLS, config, reply
        def response(connection):
            payload = json.loads(server.requests[0][2])
            with closing(sqlite3.connect(self.journal_path)) as observer:
                self.assertEqual(observer.execute("SELECT request_id FROM submissions WHERE state!='terminal'").fetchone()[0], payload["request_id"])
            observation = {key: payload[key] for key in ("site_id", "device_id", "camera_id", "room_id", "producer_session",
                                                        "producer_sequence", "occupancy_generation", "capture_session", "frame_id", "configuration_revision")}
            observation.update(observed_at=payload["captured_at"], valid_until=payload["captured_at"] + 180,
                               uncertainty_seconds=payload["clock_uncertainty_seconds"], description="TLS fixture scene.",
                               model_revision="fixture-model")
            connection.sendall(reply({"version": 1, "request_id": payload["request_id"], "site_id": "victoria",
                                      "status": "terminal", "outcome": "completed", "observation": observation}))
        with LocalTLS(response, mutual=True) as server:
            from test_gateway_client import FIXTURES
            gateway = GatewayClient(config(server.port, configuration_revision="config-1",
                                           client_certificate=FIXTURES / "client.pem", client_key=FIXTURES / "client-test-only.key"),
                                    clock=lambda: 1000 + self.now, monotonic=lambda: self.now)
            self.journal.close()
            self.journal_path = Path(self.temp.name) / "tls-journal.sqlite"
            self.journal = SubmissionJournal(self.journal_path, device_id="den-adapter",
                                             gateway_identity=gateway.gateway_identity, max_records=100, free_space_floor_bytes=0)
            self.runtime = VictoriaRuntime(adapter=self.runtime.adapter, mqtt=self.mqtt, camera=self.runtime.camera,
                                           gateway=gateway, journal=self.journal, store=self.store, settings=self.runtime.settings,
                                           health_reader=self.runtime.health_reader, clock=lambda: 1000 + self.now,
                                           monotonic=lambda: self.now)
            self.steps(0, 4)
            self.steps(5, 4)
            self.assertEqual(len(server.requests), 1)
            self.assertTrue(server.peer)
            self.assertEqual(len(self.publications()), 1)
            self.assertIsNone(self.journal.outstanding())
            record = self.store.db.execute("SELECT record_id FROM observation_record").fetchone()
            self.assertEqual(self.store.read(record[0]).observation.provenance.model_revision, "fixture-model")

    def test_durable_rejection_counts_failure_and_allows_fresh_future_work(self):
        self.reject = "post"
        self.submit_first()
        self.assertIsNone(self.journal.outstanding())
        self.assertEqual(self.publications(), [])
        self.assertEqual(self.runtime.counters["inference_rejected"], 1)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM observation_record").fetchone()[0], 0)
        rejected_id = self.submits[0]
        self.reject = None
        for now in range(6, 129):
            self.steps(now, 2)
        self.assertEqual(len(self.submits), 2)
        self.assertNotEqual(self.submits[-1], rejected_id)
        self.assertEqual(len(self.publications()), 1)

    def test_lost_post_rejection_resolves_through_lookup_without_resend(self):
        self.submit_ambiguous = True
        self.reject = "lookup"
        self.submit_first()
        self.steps(7, 3)
        self.assertEqual(self.lookups, self.submits)
        self.assertIsNone(self.journal.outstanding())
        self.assertEqual(self.publications(), [])
        self.assertEqual(self.runtime.counters["inference_rejected"], 1)


if __name__ == "__main__":
    unittest.main()
