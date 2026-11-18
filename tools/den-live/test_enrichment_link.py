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

try:  # A worker tree supplied on PYTHONPATH is the contract under test; otherwise this checkout's own.
    import den_models  # noqa: F401
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "den-models"))
from den_models.artifacts import load_profile
from den_models.contracts import (ContractError, make_hint, profile_digest, store_rgb_blob, validate_request)
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


class WorkerClient(FakeClient):
    """The worker's HTTP answers: submit runs the real request contract (HTTP 400 and no job on refusal), and
    poll or cancel of a request it does not hold answers 404 job_not_found."""

    def __init__(self, clock):
        super().__init__(clock)
        self.refused, self.polled = [], []

    def submit(self, request):
        self.submitted.append(copy.deepcopy(request))
        if self.submit_failure:
            raise self.submit_failure
        try:
            accepted = validate_request(copy.deepcopy(request), profile=PROFILE, now=self.runtime.clock())
        except ContractError as exc:
            self.refused.append(str(exc))
            raise link.WorkerRefused(400, "invalid_request") from None
        self.jobs[request["request_id"]] = accepted
        return packet(request, "queued")

    def poll(self, request_id):
        self.polled.append(request_id)
        if request_id not in self.jobs:
            raise link.WorkerRefused(404, "job_not_found")
        return self.poll_hook(self.jobs[request_id]) if self.poll_hook else packet(self.jobs[request_id])

    def cancel(self, request_id):
        self.cancelled.append(request_id)
        if request_id not in self.jobs:
            raise link.WorkerRefused(404, "job_not_found")
        return packet(self.jobs[request_id], "stopping")


# Real-shaped live frames: Den's registered transform, 640x360 RGB with its own plane times, arrival after capture.
REGISTERED = test_intent.REGISTERED
WIDTH, HEIGHT = 640, 360


def live_frame(stamp, index, *, lag=.15, transform=REGISTERED):
    rgb = PlaneRef(index, stamp - 50, stamp, WIDTH, HEIGHT)
    depth = PlaneRef(index, stamp - 50, stamp, 640, 400)
    return FrameRef("run", 1, index, "session", "rgb", rgb, None, depth, "a" * 64, transform, "scan",
                    stamp, stamp + lag, 1800000000 + stamp)


def live_snapshot(source):
    track = NS(track_id="person", state="confirmed", association_status="confident",
               current_obs_ids=("observation",), association_revision=2, position_basis="stereo")
    return NS(frame=source, service_run_id="run", camera_epoch=1, tracks=(track,), status="fresh", presence="present")


def person_reply(box=(280, 40, 400, 330)):
    return {"row": {"frame": {"width": WIDTH, "height": HEIGHT},
                    "detections": [{"class": "person", "score": .9, "keypoints": [[0, 0, 1]] * 17,
                                    "box_xyxy": list(box)}]}}


def live_decoder(prepared):
    return bytes([prepared.ref.frame_id % 251]) * (WIDTH * HEIGHT * 3)


def holds(request):
    return [frame["sample_at"] - frame["captured_at"] for frame in request["frames"]]


class LiveBridge:
    """A bridge fed as the camera thread feeds it: one offer, then one step, per arriving frame."""

    def live(self, duration=4):
        self.client = WorkerClient(self.clock)
        self.bridge = link.Bridge(dict(self.settings, duration_s=duration), camera_id="camera", clock=self.clock,
                                  client=self.client, profile=PROFILE, threaded=False, on_hint=self.hints.append,
                                  decoder=live_decoder)
        return self.bridge

    def feed(self, stamps, *, start=1, lag=.15, noncausal=()):
        for k, stamp in enumerate(stamps):
            index = start + k
            source = live_frame(stamp, index, lag=-.02 if index in noncausal else lag)
            self.clock.value = max(source.capture_mono, source.arrival_mono) + .01
            self.bridge.offer_frame(live_snapshot(source), NS(ref=source), person_reply())
            self.bridge.step()
        return source


class BridgeTests(LiveBridge, unittest.TestCase):
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

    # M3 (b): one definitive worker refusal never wedges the bridge.
    def test_definitive_rejection_is_dropped_and_later_clips_go_normally(self):
        for status in (400, 422):
            with self.subTest(status=status):
                self.setUp()
                self.client = WorkerClient(self.clock)
                self.bridge = self.new_bridge()
                self.history()
                self.bridge.worker_health = self.client.health()
                self.bridge.last_poll = self.clock.value  # Health was just observed.
                self.client.submit_failure = link.WorkerRefused(status, "invalid_request")
                self.bridge.step()
                rejected = self.client.submitted[0]
                self.assertNotIn(rejected["request_id"], self.bridge.requests)
                self.assertEqual((self.bridge.counts["rejected"], self.bridge.counts["failures"]), (1, 0))
                self.assertEqual(self.bridge.last_drop_reason, "worker_rejected_request")
                self.client.submit_failure = None
                self.client.poll_hook = lambda req: packet(req, "expired")  # Each accepted job ends; the next may go.
                for k in range(30):  # 15 s of 10 Hz frames, the scratch wedge check's schedule.
                    self.clock.value += .5
                    self.bridge.last_poll = self.clock.value
                    for j in range(5):
                        source = frame(round(self.clock.value - .45 + j / 10, 3), 2000 + 5 * k + j)
                        self.bridge.samples.append(dict(self.bridge.samples[-1], frame_id=str(source.frame_id),
                                                        captured_at=source.capture_mono,
                                                        source_frame=dataclasses.asdict(source)))
                    self.bridge.latest = snapshot(self.clock.value - .05)
                    self.bridge.step()  # Never raises.
                self.assertEqual(len(self.client.submitted) - 1, 15)  # One clip a second, every one accepted.
                self.assertEqual(len(self.client.jobs), 15)
                self.assertEqual(self.client.refused, [])
                self.assertNotIn(rejected["request_id"], self.client.polled)
                # Every accepted job but the newest has ended; the newest is still pinned awaiting its status.
                self.assertEqual((self.bridge.counts["failures"], self.bridge.counts["expired"]), (0, 14))
                self.assertEqual(list(self.bridge.requests), [self.client.submitted[-1]["request_id"]])
                self.assertEqual(self.hints, [])

    def test_unknown_request_on_poll_or_cancel_is_dropped_without_a_hint(self):
        self.client = WorkerClient(self.clock)
        self.bridge = self.new_bridge()
        request = self.request()  # Pinned but not held by the worker (it restarted, or pruned it).
        self.bridge.requests[request["request_id"]] = request
        path = self.root / request["frames"][0]["blob_ref"]
        self.bridge._results(snapshot(), self.clock())
        self.assertNotIn(request["request_id"], self.bridge.requests)
        self.assertEqual((self.bridge.counts["unknown"], self.bridge.last_drop_reason), (1, "worker_unknown_request"))
        self.bridge.samples.clear()
        self.bridge._cleanup()
        self.assertFalse(path.exists())
        # A binding change cancels; a request the worker does not hold is dropped there too.
        request = self.request()
        self.bridge.requests[request["request_id"]] = request
        self.bridge.worker_health = self.client.health()
        self.bridge.last_poll = self.clock.value
        changed = snapshot()
        changed.tracks[0].association_revision = 3
        self.bridge.latest = changed
        self.bridge.step()
        self.assertIn(request["request_id"], self.client.cancelled)
        self.assertEqual(self.bridge.counts["unknown"], 2)
        self.assertEqual(self.bridge.counts["invalidated"], 0)  # Counted once, as unknown.
        self.assertFalse(self.bridge.requests)
        # Nothing was ever published for either dropped request, even if the worker later claims a result.
        self.client.jobs[request["request_id"]] = request
        self.client.poll_hook = lambda req: packet(req, "succeeded", make_hint(
            req, PROFILE, "d" * 64, {"intent": "continue", "raw_option_scores": None, "text": None}, 100.15).as_dict())
        self.bridge._results(snapshot(), self.clock())
        self.assertEqual(self.hints, [])

    def test_transient_or_unrecognised_failures_keep_pins_and_their_retry(self):
        request = self.request()
        self.register(request)
        path = self.root / request["frames"][0]["blob_ref"]
        for failure in (TimeoutError(), ValueError("worker_response_over_bound"),
                        link.WorkerRefused(404, "route_not_found"), link.WorkerRefused(404, None),
                        link.WorkerRefused(400, "invalid_request")):
            with self.subTest(failure=failure):
                self.client.poll = lambda _id, failure=failure: (_ for _ in ()).throw(failure)
                with self.assertRaises(type(failure)):
                    self.bridge._results(snapshot(), self.clock())
                self.assertIn(request["request_id"], self.bridge.requests)
                self.bridge.samples.clear()
                self.bridge._cleanup()
                self.assertTrue(path.exists())
        self.assertEqual(self.bridge.counts["unknown"], 0)
        # A submit refused other than as invalid proves nothing: pinned and unproven, as before.
        self.setUp()
        self.history()
        self.bridge.worker_health = self.client.health()
        self.bridge.last_poll = self.clock.value
        self.client.submit_failure = link.WorkerRefused(404, "route_not_found")
        self.bridge.step()
        self.assertIn(self.client.submitted[0]["request_id"], self.bridge.requests)
        self.assertEqual((self.bridge.reason, self.bridge.counts["rejected"]), ("submission_unproven", 0))

    # M3 (c): a frame whose capture maps after its own arrival is skipped, never re-timed.
    def test_noncausal_frames_are_skipped_counted_and_never_retimed(self):
        self.live()
        stamps = [200 + k / 2.3 for k in range(16)]
        bad = {2, 3}
        self.feed(stamps, noncausal=bad)
        self.assertEqual(self.bridge.counts["noncausal_skipped"], 2)
        kept = [int(sample["frame_id"]) for sample in self.bridge.samples]
        self.assertFalse(set(kept) & bad)
        for sample in self.bridge.samples:
            source = sample["source_frame"]
            self.assertEqual(sample["captured_at"], stamps[source["frame_id"] - 1])
            self.assertEqual(source["capture_mono"], sample["captured_at"])
            self.assertLessEqual(source["capture_mono"], source["arrival_mono"])
        self.assertTrue(self.client.jobs)
        self.assertEqual(self.client.refused, [])  # No noncausal_source_arrival.
        for request in self.client.submitted:
            self.assertFalse({int(f["frame_id"]) for f in request["frames"]} & bad)

    # M3 (d): a 0.5 s hold at the measured cadence, with exactly 32 uniform slots and true times.
    def test_half_second_hold_clips_at_measured_cadence_name_the_v2_rule(self):
        for duration in (4, 8):
            with self.subTest(duration=duration):
                self.setUp()
                self.live(duration)
                stamps = [300 + k / 2.3 for k in range(int((duration + 1) * 2.3) + 2)]
                self.feed(stamps)
                self.assertTrue(self.client.jobs)
                self.assertEqual(self.client.refused, [])
                request = self.client.submitted[0]
                self.assertEqual(request["sampling"], intent.SAMPLING)
                self.assertEqual(len(request["frames"]), 32)
                self.assertGreater(max(holds(request)), .4)  # The former 0.4 s rule would not have sent it.
                self.assertLessEqual(max(holds(request)), intent.MAX_HOLD_SECONDS)
                end, seen = request["cutoff_at"], set()
                for i, item in enumerate(request["frames"]):
                    self.assertEqual(item["sample_at"], end - duration + (i + .5) * duration / 32)
                    self.assertEqual(item["repeated"], item["frame_id"] in seen)
                    seen.add(item["frame_id"])
                    self.assertIn(item["captured_at"], stamps)
                    self.assertEqual(item["captured_at"], item["source_frame"]["capture_mono"])
                self.assertEqual(request["dependencies"]["transform_id"], REGISTERED)

    def test_holds_over_half_a_second_or_another_rule_send_nothing(self):
        self.live()
        self.feed([300 + k / 1.5 for k in range(12)])  # 0.67 s apart: some slot would hold a frame over 0.5 s.
        self.assertEqual(self.client.submitted, [])
        self.setUp()
        other = copy.deepcopy(PROFILE)
        other["inputs"]["sampling"] = "causal_slot_center_sample_hold/v1"
        self.bridge.profile = other
        self.history()
        self.assertIsNone(self.bridge._clip(self.clock()))
        self.assertEqual(self.bridge.reason, "sampling_unsupported")

    # Review: the worker requires a finite device time on every plane present, an rgb-mode frame and an IDENT
    # session. Den's planes carry device_s=None when the camera gives no finite device timestamp.
    def test_frames_the_worker_would_refuse_are_skipped_counted_and_never_sent(self):
        self.live()
        stamps = [400 + k / 2.3 for k in range(24)]
        untimed = {3: "rgb", 5: "depth", 8: "rgb_nan"}
        lineage = {11: dict(mode="ir"), 13: dict(camera_session="bad session")}
        for k, stamp in enumerate(stamps):
            index = 1 + k
            source = live_frame(stamp, index)
            if index in untimed:
                plane = "depth" if untimed[index] == "depth" else "rgb"
                device = float("nan") if untimed[index] == "rgb_nan" else None
                source = dataclasses.replace(source, **{plane: dataclasses.replace(getattr(source, plane), device_s=device)})
            if index in lineage:
                source = dataclasses.replace(source, **lineage[index])
            self.clock.value = source.arrival_mono + .01
            self.bridge.offer_frame(live_snapshot(source), NS(ref=source), person_reply())
            self.bridge.step()
        self.assertEqual((self.bridge.counts["untimed_skipped"], self.bridge.counts["lineage_skipped"]), (3, 2))
        self.assertFalse({int(sample["frame_id"]) for sample in self.bridge.samples} & (set(untimed) | set(lineage)))
        self.assertTrue(self.client.jobs)
        self.assertEqual(self.client.refused, [])
        self.assertEqual((self.bridge.counts["rejected"], self.bridge.counts["failures"]), (0, 0))
        for request in self.client.submitted:
            self.assertFalse({int(f["frame_id"]) for f in request["frames"]} & (set(untimed) | set(lineage)))
        # Nothing was re-timed: every sent frame keeps its own device and capture times.
        for request in self.client.submitted:
            for item in request["frames"]:
                self.assertEqual(item["source_frame"]["rgb"]["device_s"], item["captured_at"] - 50)
        # The same frames sent unskipped are refused by the worker contract, which is why they are skipped.
        for bad in (dataclasses.replace(live_frame(500, 1), rgb=dataclasses.replace(live_frame(500, 1).rgb, device_s=None)),
                    dataclasses.replace(live_frame(500, 1), mode="ir")):
            self.assertIsNotNone(link.frame_problem(bad))
            request = self.client.submitted[0]
            spoiled = copy.deepcopy(request)
            spoiled["frames"][-1]["source_frame"] = dataclasses.asdict(dataclasses.replace(
                bad, frame_id=int(spoiled["frames"][-1]["frame_id"]), capture_mono=spoiled["frames"][-1]["captured_at"],
                arrival_mono=spoiled["frames"][-1]["source_frame"]["arrival_mono"]))
            with self.assertRaises(ContractError):
                validate_request(spoiled, profile=PROFILE, now=request["cutoff_at"])

    def test_bindings_the_worker_would_refuse_are_never_advertised_or_sampled(self):
        self.live()
        for change in (dict(scan_revision=None), dict(scan_revision="bad revision"),
                       dict(depth_calibration_sha256="A" * 64), dict(transform_id="den oak")):
            with self.subTest(change=change):
                self.setUp()
                self.live()
                deps, _ = link.binding(live_snapshot(dataclasses.replace(live_frame(300, 1), **change)), "camera")
                self.assertTrue(deps is None or not link.binding_acceptable(deps))
                for k in range(12):
                    source = dataclasses.replace(live_frame(300 + k / 2.3, 1 + k), **change)
                    self.clock.value = source.arrival_mono + .01
                    self.bridge.offer_frame(live_snapshot(source), NS(ref=source), person_reply())
                    self.bridge.step()
                    self.assertIsNone(self.bridge.dependencies)
                self.assertEqual(self.bridge.counts["binding_refused"], 0 if deps is None else 1)
                self.assertEqual((list(self.bridge.samples), self.client.submitted), ([], []))
        # The registered binding the worker admits is unchanged.
        deps, _ = link.binding(live_snapshot(live_frame(300, 1)), "camera")
        self.assertTrue(link.binding_acceptable(deps))

    def test_status_words_count_drops_and_skips_and_stay_off_when_disabled(self):
        import run_local
        disabled = link.Bridge(camera_id="camera")
        self.assertIsNone(disabled.status_words())
        perceiver = NS(counts={"frames": 3, "refused_depth": 0, "detector_errors": 0, "unreadable": 0,
                               "out_of_order": 0, "expired": 0})
        event = {"camera": {"status": "ok"}, "health": {}, "tracks": []}
        self.assertEqual(run_local.status_line(perceiver, event, (), disabled), run_local.status_line(perceiver, event))
        self.bridge.counts.update(submitted=4, completed=1, rejected=2, unknown=1, noncausal_skipped=3,
                                  untimed_skipped=2, binding_refused=1)
        self.bridge._drop("absent", "worker_rejected_request", "rejected")
        words = self.bridge.status_words()
        self.assertEqual(words, "intent worker_rejected_request: 4 sent, 1 hints, 0 expired, 3 rejected, 1 unknown, "
                                "5 frames skipped (3 noncausal, 2 untimed, 0 lineage), 1 bindings refused, "
                                "last drop worker_rejected_request")
        self.assertTrue(run_local.status_line(perceiver, event, (), self.bridge).endswith(" | " + words))

    def test_a_held_frame_arriving_after_the_cutoff_is_not_submitted(self):
        self.history()
        held = self.bridge.samples[-2]  # Captured 99.9 s, held by the last slot of a clip cut at 100 s.
        held["source_frame"] = dict(held["source_frame"], arrival_mono=100.05)
        self.assertIsNone(self.bridge._clip(self.clock()))
        held["source_frame"] = dict(held["source_frame"], arrival_mono=100)
        self.assertIsNotNone(self.bridge._clip(self.clock()))


class ClientTests(unittest.TestCase):
    def test_definitive_refusals_carry_only_their_status_and_fixed_error_word(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "token"
            path.write_text("a" * 64)
            path.chmod(0o600)
            for status, body, word in ((400, b'{"error":"invalid_request"}', "invalid_request"),
                                       (404, b'{"error":"job_not_found"}', "job_not_found"),
                                       (422, b'{"error":"private /path or model text"}', None),
                                       (404, b"not json", None)):
                with self.subTest(status=status, body=body):
                    client = link.Client(9461, path, connection_factory=self.connection(status, body))
                    with self.assertRaises(link.WorkerRefused) as caught:
                        client.poll("request")
                    self.assertEqual((caught.exception.status, caught.exception.error), (status, word))
                    self.assertEqual(str(caught.exception), "worker_refused")
                    self.assertEqual(caught.exception.unknown_request, word == "job_not_found")
            for status in (302, 401, 409, 500):
                with self.subTest(status=status):
                    client = link.Client(9461, path, connection_factory=self.connection(status, b'{"error":"x"}'))
                    with self.assertRaises(ValueError) as caught:
                        client.submit({})
                    self.assertNotIsInstance(caught.exception, link.WorkerRefused)

    @staticmethod
    def connection(status, body):
        class Connection:
            def __init__(self, host, port, **kwargs):
                pass
            def request(self, *args, **kwargs):
                pass
            def getresponse(self):
                return NS(status=status, read=lambda count: body)
            def close(self):
                pass
        return Connection

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
