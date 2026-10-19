"""Actual nonrecording producer -> public journal -> actual worker -> replay.

Only camera/model/HA I/O is authored. No prebuilt geometry replaces the producer.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
for directory in (ROOT / "tools/den-live-runtime", ROOT / "stack/services/home-context",
                  ROOT / "stack/services/perception-lab", ROOT / "tools/shared-home", ROOT / "tools/la-detector-trial"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
from archive import Recorder, RecorderRPC
from supervisor import NodeWorkerDriver
from journal import digest
from live_worker_test_helpers import describe
from schema_registry import schema_digest
import test_live_interaction as interaction_fixture
from live_acquisition import create_production_acquisition
import live_capture_fixtures as capture


class ProducerJoinTests(unittest.TestCase):
    def test_actual_factory_originals_drain_and_replay_join_without_second_journal(self):
        # Enforced fixture-only boundary: Python cannot contact a camera, HA,
        # Home host, model endpoint or any unlisted local service in this test.
        allowed_ports = set()
        original_connect = socket.create_connection
        def local_only(address, *args, **kwargs):
            if address[0] not in {"127.0.0.1", "localhost", "::1"} or address[1] not in allowed_ports:
                raise AssertionError("nonfixture_network_forbidden")
            return original_connect(address, *args, **kwargs)
        guard = mock.patch("socket.create_connection", side_effect=local_only)
        guard.start()
        self.addCleanup(guard.stop)
        for target in ("ultralytics_worker.UltralyticsWorker.load", "rtmpose_worker.RTMPoseWorker.load"):
            patch = mock.patch(target, side_effect=AssertionError("native_model_loading_forbidden"))
            patch.start()
            self.addCleanup(patch.stop)
        authored = interaction_fixture.LiveInteractionTests()
        authored.setUp()
        self.addCleanup(authored.doCleanups)
        authored.prepare()
        authored.config["limits"]["max_cycles"] = 8
        authored.local_grant["configuration_digest"] = digest(authored.config)
        manifest = deepcopy(authored.live_config["manifest"])
        worker_id, worker_token = "authored-joined-worker", "authored-private-worker-grant"
        seed = {"profile": "home-live-shadow-1", "identity": deepcopy(manifest["identity"]),
                "source_pins": {key: digest(source) for key, source in manifest["sources"].items()},
                "engagement_policy": {"support_methods": [deepcopy(authored.interaction_method)],
                    "refinement_methods": [], "negative_methods": [], "allow_experimental": True},
                "worker_id": worker_id, "producer_id": worker_id, "producer_epoch": "authored-worker-epoch",
                "revision": capture.REVISION, "max_output_age_s": 3, "semantic_interval_s": 1}
        description = describe(seed)
        policy = {key: seed[key] for key in ("worker_id", "producer_id", "producer_epoch", "revision", "source_pins",
                    "engagement_policy", "max_output_age_s", "semantic_interval_s")}
        policy.update(config_digest=description["config_digest"], schema_pins=description["schema_pins"])
        manifest.update(schema="home-live-ingress-manifest-2", version=2, worker_interface_digest=description["worker_interface_digest"], worker=policy)
        manifest_ref = {"id": "authored-combined-manifest", "digest": digest(manifest)}
        live_config = {"schema": "home-live-ingress-config-2", "version": 2, "manifest_ref": manifest_ref, "manifest": manifest}
        producer_grant = deepcopy(authored.journal_grant)
        for binding in producer_grant["live_bindings"]:
            binding["manifest_ref"] = deepcopy(manifest_ref)
        types = ["home-live-fact-1", "home-live-geometry-1", "home-live-engagement-state-1", "home-live-shadow-decision-1",
                 "home-live-worker-checkpoint-1", "home-live-worker-commit-1", "home-live-input-cut-1"]
        source_ids = list(manifest["sources"])
        worker_grant = {"principal_id": "authored-worker-principal", "producer_id": worker_id, "producer_epoch": seed["producer_epoch"],
            "expected_consumer_id": worker_id, "expected_worker_id": worker_id, "token_sha256": hashlib.sha256(worker_token.encode()).hexdigest(),
            "scopes": [{"site_id": seed["identity"]["site_id"], "room_id": seed["identity"]["room_id"]}], "operations": ["read", "worker_commit"],
            "payload_types": types, "revisions": [capture.REVISION], "source_ids": source_ids,
            "sensor_roots": sorted({root for source in manifest["sources"].values() for root in source["sensor_roots"]}),
            "schema_pins": {name: schema_digest(name, profile="home-live-shadow-1") for name in types},
            "live_bindings": [{"manifest_ref": deepcopy(manifest_ref), "source_id": key, "capabilities": ["read", "worker_commit"]} for key in source_ids]}
        authored.live_config = live_config
        graph = create_production_acquisition(authored.config, live_config=live_config,
            capture_policy=authored.policy, capture_method=authored.method, spatial_policy=authored.spatial_policy,
            interaction_policy=authored.interaction_policy, shutdown_owner="supervisor",
            journal_path=Path(authored.work.name) / "joined.sqlite", journal_grants=[producer_grant, worker_grant],
            publication_token=capture.TOKEN, detector=authored.detector, pose=authored.pose_worker,
            local_authority=lambda: deepcopy(authored.local_grant), household_enabled=lambda: True, occupancy=authored.occupancy,
            clock=lambda: authored.now, monotonic=lambda: authored.mono, camera_dependencies=authored.camera,
            control_clock=authored.control_clock, rss=lambda: 64 * 1024**2)
        allowed_ports.add(graph.address[1])
        node = shutil.which("node")
        config = {**seed, "manifest_ref": manifest_ref, "live_manifest": manifest,
                  "token": worker_token, "service_url": "http://127.0.0.1:" + str(graph.address[1])}
        archive_config = {key: value for key, value in config.items() if key not in {"token", "service_url"}}
        archive_manifest = {"schema": "home-live-session-manifest-1", "version": 1, "worker_config": archive_config,
            "worker_description": describe(archive_config), "session_ref": seed["identity"], "configuration_digest": graph.configuration_digest,
            "source_bundle_digest": "b" * 64}
        with tempfile.TemporaryDirectory(prefix="den-producer-joined-") as directory:
            archive_file = Path(directory) / "joined.jsonl"
            recorder = Recorder(archive_file, archive_manifest, max_records=1024, max_bytes=16 * 1024 * 1024, min_free_bytes=1)
            graph.journal.attach_recorder(recorder.record)
            rpc = RecorderRPC(recorder)
            allowed_ports.add(rpc.server.server_port)
            driver = NodeWorkerDriver(node_runtime=node, config=config, recorder=rpc.start(), controlled_time=authored.now)
            withdrawal_receipt = None
            try:
                for seq in range(6):
                    if seq:
                        authored.advance()
                    result = graph.step()
                    self.assertEqual(result["interaction"], "completed", result)
                    driver.cycle(authored.now)
                self.assertEqual(graph.quiesce()["status"], "quiescent")
                authored.now += .1
                withdrawal = graph.withdraw()
                self.assertEqual(withdrawal["withdrawal"], "recorded", withdrawal)
                withdrawal_receipt = withdrawal["barrier_receipt"]
                # This is actual new consumer time, not fabricated original progression.
                driver.cycle(authored.now)
                drained = driver.drain(deadline_ms=1000, through_cursor=withdrawal_receipt["cursor"])
                self.assertEqual(drained["status"], "settled", drained)
                report = recorder.drain({"status": "complete", "terminal_cursor": drained["input_cursor"]})
                self.assertEqual(report["status"], "drained")
                driver.close()
                rpc.close()
                final = graph.finalize(drain_receipt={"schema": "home-acquisition-drain-1", "version": 1,
                    "session_ref": seed["identity"], "configuration_digest": graph.configuration_digest,
                    "withdrawal_receipt": withdrawal_receipt, "recorder_status": "drained", "worker_status": "settled",
                    "terminal_cursor": drained["input_cursor"]})
                self.assertEqual(final["status"], "stopped", final)
                run = subprocess.run([node, str(ROOT / "tools/den-live-runtime/replay.cjs"), str(archive_file)], cwd=ROOT,
                                     capture_output=True, text=True, timeout=15)
                self.assertEqual(run.returncode, 0, run.stdout)
                self.assertEqual(json.loads(run.stdout)["status"], "reproduced")
            finally:
                if not recorder.closed:
                    recorder.drain({"status": "incomplete"})
                if driver.process.poll() is None:
                    driver.close()
                if rpc.thread.is_alive():
                    rpc.close()
                if not graph.finished:
                    graph.quiesce()
                    withdrawal = graph.withdraw()
                    graph.finalize(drain_receipt={"schema": "home-acquisition-drain-1", "version": 1,
                        "session_ref": seed["identity"], "configuration_digest": graph.configuration_digest,
                        "withdrawal_receipt": withdrawal.get("barrier_receipt"), "recorder_status": "incomplete", "worker_status": "unconfirmed",
                        "terminal_cursor": (withdrawal.get("barrier_receipt") or {}).get("cursor", 0)})


if __name__ == "__main__":
    unittest.main()
