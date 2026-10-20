"""Authored external inputs; these tests do not qualify Victoria equipment."""
import base64
from contextlib import ExitStack
import json
import http.client
from pathlib import Path
import sys
import shutil
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from controlled_external_io import ControlledExternalIO, build_base_configuration, assemble_journal_grants


class ControlledExternalIOTests(unittest.TestCase):
    def test_opted_in_uncertain_material_reaches_public_semantics_through_the_original_factory(self):
        from live_acquisition import create_production_acquisition
        with self.assertRaisesRegex(ValueError, "scenario_refused"):
            ControlledExternalIO().set_scenario("material_uncertain")
        with ExitStack() as cleanup:
            directory = cleanup.enter_context(tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent, prefix="controlled-uncertain-"))
            bundle = build_base_configuration(session_ref={"site_id": "authored-site", "room_id": "authored-room",
                "session_id": "authored-uncertain-session", "domain_epoch": "authored-uncertain-domain"}, node_runtime=shutil.which("node"), clarification=True)
            self.addCleanup(bundle["occupancy"].stop)
            bundle["external_io"].set_scenario("material_uncertain")
            bundle["occupancy"].start()
            deadline = time.monotonic()+2
            while bundle["occupancy"].reading()["state"] != "occupied" and time.monotonic() < deadline: time.sleep(.01)
            grants = assemble_journal_grants(bundle["configuration"], bundle["worker_config"], publication_token="public-test-producer",
                worker_token="public-test-worker", human_token="public-test-human")
            graph = create_production_acquisition(bundle["acquisition_config"], live_config=bundle["configuration"],
                journal_path=Path(directory)/"uncertain.sqlite", journal_grants=grants, publication_token="public-test-producer",
                **{key: bundle[key] for key in ("capture_policy", "capture_method", "spatial_policy", "interaction_policy", "lifecycle_policy",
                    "detector", "pose", "local_authority", "household_enabled", "occupancy", "camera_dependencies", "control_clock", "assessment_recorder", "rss")})
            cleanup.callback(graph.close)
            cycle = graph.step()
            self.assertEqual(cycle["interaction"], "completed", cycle)
            connection = http.client.HTTPConnection(*graph.address, timeout=2)
            try:
                connection.request("GET", "/api/home-context/v1/snapshot?site_id=authored-site&room_id=authored-room", headers={"Authorization": "Bearer public-test-human"})
                response = connection.getresponse(); self.assertEqual(response.status, 200)
                items = json.loads(response.read())["items"]
            finally:
                connection.close()
            facts = {p["payload"]["kind"]: p["payload"] for p in items if p["payload_type"] == "home-live-fact-1"}
            self.assertEqual(facts["material_state"]["value"], "visible_state_unknown")
            self.assertEqual(facts["hand_material_relation"]["value"], "near")
            self.assertEqual((facts["object_interaction"]["status"], facts["object_interaction"]["value"], facts["object_interaction"]["reason"]),
                             ("unobservable", None, "unsupported_distinction"))
            spatial = next(p["payload"] for p in items if p["payload_type"] == "home-live-geometry-1" and p["payload"]["method_ref"]["id"] == "controlled-authored-stationary-spatial")
            self.assertTrue(all(spatial["parts"][name]["status"] == "supplied" for name in ("work", "body", "protection")))

    def test_original_factory_accepts_base_configuration_and_derives_public_outputs(self):
        from live_acquisition import create_production_acquisition
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent, prefix="controlled-test-") as directory:
            bundle = build_base_configuration(session_ref={"site_id": "authored-site", "room_id": "authored-room",
                "session_id": "authored-session", "domain_epoch": "authored-domain"}, node_runtime=shutil.which("node"))
            self.addCleanup(bundle["occupancy"].stop)
            bundle["occupancy"].start()
            deadline = time.monotonic() + 2
            while bundle["occupancy"].reading()["state"] != "occupied" and time.monotonic() < deadline:
                time.sleep(.01)
            grants = assemble_journal_grants(bundle["configuration"], bundle["worker_config"],
                publication_token="public-test-producer", worker_token="public-test-worker", human_token="public-test-human")
            graph = create_production_acquisition(bundle["acquisition_config"], live_config=bundle["configuration"],
                journal_path=Path(directory) / "controlled.sqlite", journal_grants=grants, publication_token="public-test-producer",
                **{key: bundle[key] for key in ("capture_policy", "capture_method", "spatial_policy", "interaction_policy", "lifecycle_policy",
                    "detector", "pose", "local_authority", "household_enabled", "occupancy", "camera_dependencies", "control_clock", "assessment_recorder", "rss")})
            try:
                self.assertEqual(graph.readiness()["inflight"], False)
                original_publish = graph.capture.publish
                errors = []
                def publish(value):
                    try:
                        return original_publish(value)
                    except Exception as error:
                        errors.append(repr(error))
                        raise
                graph.capture.publish = publish
                cycle = graph.step()
                self.assertEqual(cycle["interaction"], "completed", (cycle, errors))
                self.assertEqual(cycle["spatial"], "completed", cycle)
            finally:
                graph.close()

    def test_each_original_capture_uses_current_process_clocks_and_advances(self):
        io = ControlledExternalIO()
        before = time.time()
        first = json.loads(io.camera_pair("unused", timeout=1))
        time.sleep(.02)
        second = json.loads(io.camera_pair("unused", timeout=1))
        headers = [json.loads(base64.urlsafe_b64decode(row["header"])) for row in (first, second)]
        self.assertGreater(headers[1]["sequence"], headers[0]["sequence"])
        self.assertGreater(headers[1]["observed_at_unix_ns"], headers[0]["observed_at_unix_ns"])
        self.assertGreater(headers[1]["observed_monotonic_seconds"], headers[0]["observed_monotonic_seconds"])
        self.assertLess(abs(headers[0]["observed_at_unix_ns"] / 1e9 - before), 1)
        self.assertNotEqual(first["jpeg_b64"], second["jpeg_b64"])
        self.assertEqual(io.status()["source_mode"], "controlled_external_io")
        self.assertNotIn("header", io.status())

    def test_scenario_only_changes_the_next_external_sdk_observation(self):
        io = ControlledExternalIO()
        io.camera_pair("unused", timeout=1)
        io.set_scenario("empty")
        self.assertEqual(len(io.detector_result()[0].boxes.xyxy.tolist()), 2)
        io.camera_pair("unused", timeout=1)
        self.assertEqual(io.detector_result()[0].boxes.xyxy.tolist(), [])
        with self.assertRaisesRegex(ValueError, "scenario_refused"):
            io.set_scenario("bootstrap")
        io.set_scenario("source_loss")
        with self.assertRaisesRegex(OSError, "controlled_source_unavailable"):
            io.camera_pair("unused", timeout=1)


if __name__ == "__main__":
    unittest.main()
