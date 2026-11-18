"""Exact-frame bridge contracts: tiny private CPU fixtures, no model or socket I/O."""
import copy
import dataclasses
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "den-models"))
from den_models.artifacts import load_profile
from den_models.contracts import (make_hint, profile_digest, store_rgb_blob, validate_request)
from den_models.runtime import Runtime

from contracts import FrameRef, PlaneRef
import enrichment_link as link
import intent
import model_projection
import test_intent

PROFILE = load_profile("vjepa-lm-qwen3-8b")


class Clock:
    value = 100.1
    def __call__(self):
        return self.value


def frame(stamp=100, index=1000):
    plane = PlaneRef(index, stamp, stamp, 16, 16)
    return FrameRef("run", 1, index, "session", "rgb", plane, None, plane,
                    "a" * 64, "transform", "scan", stamp, stamp, 1800000000 + stamp)


def snapshot(stamp=100):
    track = NS(track_id="person", state="confirmed", association_status="confident",
               current_obs_ids=("observation",), association_revision=2, position_basis="stereo")
    return NS(frame=frame(stamp), service_run_id="run", camera_epoch=1,
              tracks=(track,), status="fresh", presence="present")


def packet(request, status="running", hint=None):
    return dict(schema="den.intent-job/v1", request_id=request["request_id"], clip_id=request["clip_id"],
                profile_digest=request["profile_digest"], status=status, reason=None, hint=hint,
                submitted_at=100, finished_at=None if status == "running" else 100.2)


class FakeClient:
    def __init__(self, clock):
        # Runtime.health is the real sidecar schema. Every resource/native
        # dependency is injected; health never calls any of these placeholders.
        self.runtime = Runtime({}, PROFILE, clock=clock, control=NS(), probe=NS(), lease=NS(),
                               host_probe=NS(), admission=lambda: "d" * 64)
        self.runtime.admitted = self.runtime.resident = True
        self.jobs, self.submitted, self.cancelled = {}, [], []
        self.submit_failure = self.poll_hook = None

    def health(self):
        return self.runtime.health()

    def submit(self, request):
        self.jobs[request["request_id"]] = copy.deepcopy(request)
        self.submitted.append(copy.deepcopy(request))
        if self.submit_failure:
            raise self.submit_failure
        return packet(request, "queued")

    def poll(self, request_id):
        request = self.jobs[request_id]
        return self.poll_hook(request) if self.poll_hook else packet(request)

    def cancel(self, request_id):
        self.cancelled.append(request_id)
        return packet(self.jobs[request_id], "stopping")


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.clock, self.hints = Clock(), []
        self.client = FakeClient(self.clock)
        self.settings = {"enabled": True, "frame_root": str(self.root), "duration_s": 4}
        self.bridge = self.new_bridge()

    def new_bridge(self):
        return link.Bridge(self.settings, camera_id="camera", clock=self.clock,
                           client=self.client, profile=PROFILE, threaded=False, on_hint=self.hints.append,
                           decoder=lambda prepared: bytes(16 * 16 * 3))

    def history(self):
        blob = store_rgb_blob(self.root, bytes(16 * 16 * 3), 16, 16)
        self.bridge.owned.add(blob["blob_ref"])
        for i in range(41):
            source = frame(96 + i / 10, i + 1)
            self.bridge.samples.append(dict(frame_id=str(source.frame_id), **blob,
                                           captured_at=source.capture_mono,
                                           track_bbox_xyxy=[1, 1, 15, 15],
                                           source_frame=dataclasses.asdict(source)))
        self.bridge.latest = snapshot()
        self.bridge.dependencies, _ = link.binding(self.bridge.latest, "camera")
        return self.root / blob["blob_ref"]

    def request(self):
        self.history()
        request = self.bridge._clip(self.clock())
        self.assertIsNotNone(request)
        return validate_request(request, profile=PROFILE, now=self.clock())

    def register(self, request):
        self.bridge.requests[request["request_id"]] = request
        self.client.jobs[request["request_id"]] = copy.deepcopy(request)

    def test_unsubmitted_history_ages_out_even_when_worker_transport_fails(self):
        request = self.request()
        self.register(request)
        pinned = self.root / request["frames"][0]["blob_ref"]
        extra = store_rgb_blob(self.root, bytes([1]) * (16 * 16 * 3), 16, 16)
        self.bridge.owned.add(extra["blob_ref"])
        self.bridge.samples.append(dict(self.bridge.samples[-1], **extra))
        self.clock.value = 110
        self.client.health = lambda: (_ for _ in ()).throw(TimeoutError())
        self.client.poll = lambda _: (_ for _ in ()).throw(TimeoutError())
        with self.assertRaises(TimeoutError):
            self.bridge.step()
        self.assertFalse(self.bridge.samples)
        self.assertFalse((self.root / extra["blob_ref"]).exists())
        self.assertTrue(pinned.exists())

    def test_bridge_clip_obeys_real_worker_contract(self):
        request = self.request()
        self.assertEqual(len(request["frames"]), 32)
        self.assertEqual(request["source_size"], [16, 16])
        self.assertEqual(request["profile_digest"], profile_digest(PROFILE))

    def test_all_real_sidecar_terminal_statuses_release_only_finished_inputs(self):
        for status in ("succeeded", "failed", "cancelled", "expired", "invalidated", "superseded", "unsupported"):
            with self.subTest(status=status):
                request = self.request()
                path = self.root / request["frames"][0]["blob_ref"]
                self.register(request)
                self.client.poll_hook = lambda req, status=status: packet(req, status)
                self.bridge._results(snapshot(), self.clock())
                self.assertNotIn(request["request_id"], self.bridge.requests)
                self.bridge.samples.clear()
                self.bridge._cleanup()
                self.assertFalse(path.exists())

    def test_crossed_or_malformed_terminal_envelope_cannot_release_live_pins(self):
        request = self.request()
        path = self.root / request["frames"][0]["blob_ref"]
        self.register(request)
        for field, bad in (("schema", "wrong"), ("request_id", "other"),
                           ("clip_id", "other"), ("profile_digest", "f" * 64)):
            with self.subTest(field=field):
                self.client.poll_hook = lambda req, field=field, bad=bad: dict(packet(req, "cancelled"), **{field: bad})
                try:
                    self.bridge._results(snapshot(), self.clock())
                except ValueError:
                    pass  # Explicit protocol refusal is also an acceptable outcome.
                self.assertIn(request["request_id"], self.bridge.requests)
                self.bridge.samples.clear()
                self.bridge._cleanup()
                self.assertTrue(path.exists())

    def test_accepted_post_with_lost_response_retains_pins_until_terminal(self):
        path = self.history()
        self.client.submit_failure = TimeoutError("response lost after acceptance")
        self.bridge.step()
        self.assertEqual(len(self.client.submitted), 1)
        request = self.client.submitted[0]
        self.assertIn(request["request_id"], self.bridge.requests)
        self.bridge.samples.clear()
        self.bridge._cleanup()
        self.assertTrue(path.exists())
        self.client.poll_hook = lambda req: packet(req, "cancelled")
        self.bridge._results(snapshot(), self.clock())
        self.bridge._cleanup()
        self.assertFalse(path.exists())

    def test_result_produced_during_poll_uses_completion_clock(self):
        request = self.request()
        self.register(request)
        hint = make_hint(request, PROFILE, "d" * 64,
                         {"intent": "continue", "raw_option_scores": None, "text": None}, 100.15).as_dict()
        def poll(req):
            self.clock.value = 100.2
            return packet(req, "succeeded", hint)
        self.client.poll_hook = poll
        self.bridge._results(snapshot(), 100.1)
        self.assertEqual(len(self.hints), 1)

    def test_result_expiring_during_poll_cannot_be_published(self):
        request = self.request()
        self.register(request)
        self.clock.value = 101.9
        hint = make_hint(request, PROFILE, "d" * 64,
                         {"intent": "continue", "raw_option_scores": None, "text": None}, 101.8).as_dict()
        def poll(req):
            self.clock.value = 102.1
            return packet(req, "succeeded", hint)
        self.client.poll_hook = poll
        self.bridge._results(snapshot(101.9), 101.9)
        self.assertEqual(self.hints, [])

    def test_stalled_source_history_ages_out_without_new_frames(self):
        path = self.history()
        self.clock.value = 112
        self.bridge.step()
        self.assertEqual(len(self.bridge.samples), 0)
        self.assertFalse(path.exists())

    def test_stale_sample_trimming_preserves_unproven_native_request_pins(self):
        request = self.request()
        path = self.root / request["frames"][0]["blob_ref"]
        self.register(request)
        self.clock.value = 112
        self.bridge.step()
        self.assertEqual(len(self.bridge.samples), 0)
        self.assertIn(request["request_id"], self.bridge.requests)
        self.assertTrue(path.exists())

    def test_unexplained_existing_frame_root_is_not_silently_reused(self):
        store_rgb_blob(self.root, bytes(16 * 16 * 3), 16, 16)
        with self.assertRaises(ValueError):
            self.new_bridge()

    def test_close_with_live_io_thread_does_not_race_cleanup_or_drop_pins(self):
        path = self.history()
        self.bridge.thread = NS(join=lambda timeout: None, is_alive=lambda: True)
        self.bridge.close()
        self.assertTrue(path.exists())
        self.assertTrue(self.bridge.samples)

    def test_real_health_warmup_is_preparing_without_loaded_claim(self):
        self.client.runtime.resident = False
        self.client.runtime.active = {"request": {"_operator_warmup": True}}
        self.bridge._poll_health(self.clock())
        value = model_projection.stage(self.bridge.stages(self.clock())[0], self.clock())
        self.assertEqual(value["state"], "preparing")
        self.assertFalse(value["loaded"])

    def test_unobserved_enabled_stage_has_no_fabricated_lease(self):
        for now in (self.clock(), self.clock() + 20):
            raw = self.bridge.stages(now)[0]
            value = model_projection.stage(raw, now)
            self.assertTrue(value["configured"])
            self.assertEqual(value["id"], "vjepa-lm-qwen3-8b")
            self.assertEqual(value["state"], "unavailable")
            for key in ("observed_mono", "valid_until_mono", "loaded", "provisioned"):
                self.assertIsNone(raw[key])
                self.assertIsNone(value[key])

    def test_real_observation_lease_is_preserved_after_expiry(self):
        self.bridge._poll_health(self.clock())
        fresh = self.bridge.stages(self.clock())[0]
        self.assertTrue(fresh["loaded"])
        self.assertEqual(fresh["state"], "idle")
        self.assertEqual((fresh["observed_mono"], fresh["valid_until_mono"]), (self.clock(), self.clock() + 3))
        old = self.bridge.stages(self.clock() + 20)[0]
        self.assertEqual(old["state"], "unavailable")
        self.assertIsNone(old["loaded"])
        self.assertEqual((old["observed_mono"], old["valid_until_mono"]),
                         (fresh["observed_mono"], fresh["valid_until_mono"]))

    def test_disabled_stage_keeps_its_current_local_observation_lease(self):
        disabled = link.Bridge(camera_id="camera", threaded=False)
        for now in (100, 120):
            value = disabled.stages(now)[0]
            self.assertEqual(value["state"], "disabled")
            self.assertFalse(value["configured"])
            self.assertEqual((value["observed_mono"], value["valid_until_mono"]), (now, now + 3))

    def test_publisher_repository_id_survives_but_urls_do_not(self):
        self.bridge._poll_health(self.clock())
        value = self.bridge.stages(self.clock())[0]
        self.assertEqual(model_projection.stage(value, self.clock())["model_id"], PROFILE["repository"])
        for model_id in ("https://private/?token=secret", "/private/file", "../private"):
            self.assertIsNone(model_projection.stage(dict(value, model_id=model_id), self.clock())["model_id"])

    def test_public_hint_identifiers_reject_url_path_and_control_text(self):
        for key in ("request_id", "clip_id", "camera_id"):
            for unsafe in ("https://private/?token=secret", "/private/file", "line\nbreak"):
                with self.subTest(key=key, unsafe=unsafe):
                    wire = test_intent.wire()
                    (wire["dependencies"] if key == "camera_id" else wire)[key] = unsafe
                    with self.assertRaises(ValueError):
                        intent.from_wire(wire, now_mono=100.2)


class ClientTests(unittest.TestCase):
    def test_proxy_environment_and_redirect_never_leave_direct_loopback(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "token"
            path.write_text("a" * 64)
            path.chmod(0o600)
            connections = []
            class Connection:
                def __init__(self, host, port, **kwargs):
                    connections.append((host, port)); self.closed = False
                def request(self, *args, **kwargs):
                    pass
                def getresponse(self):
                    return NS(status=302, read=lambda count: b'{}')
                def close(self):
                    self.closed = True
            with patch.dict(os.environ, {"HTTP_PROXY": "http://must-not-connect.invalid:1234", "NO_PROXY": ""}):
                client = link.Client(9461, path, connection_factory=Connection)
                with self.assertRaises(ValueError):
                    client.health()
            self.assertEqual(connections, [("127.0.0.1", 9461)])


if __name__ == "__main__":
    unittest.main()
