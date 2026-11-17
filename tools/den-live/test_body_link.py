"""Synthetic body-v2 worker ordering, exact-frame pipeline handoff and SSE replay."""
import json
import time
import unittest
import urllib.error
import urllib.request
from collections import deque
from types import SimpleNamespace
from unittest.mock import patch

from body_input import BodyInput, bundle_frame_key, qualified
from body_link import BodyLink
from contracts import TrackingSnapshot
from perceive import Perceiver, body_person
from person_fields import Facing, Speed
from seated import Published
from stream import BODY_LEASE_S, BODY_SCHEMA, BASE, PeopleStream


SCOPE = dict(home="den", run_id="synthetic-run", track_epoch=3, content_revision="synthetic-scan",
             transform_id="synthetic-transform", mode="rgb")
BINDING = {key: SCOPE[key] for key in ("run_id", "track_epoch", "content_revision", "transform_id")}
BINDING["depth_profile"] = "synthetic-depth"
SETUP = dict(binding=BINDING, left_k=[400, 400, 320, 200], room_from_left=[[[1, 0, 0], [0, 0, 1], [0, -1, 0]], [0, 0, 1.4]])
PERSON = dict(track_id="e3-one", xy_m=[0, 2], joints={}, facing_deg=0)
FRAME = dict(track_id=PERSON["track_id"], facing_deg=0, shoulder_midpoint_m=[0, 2, 1.2], pelvis_z_m=0.8,
             points=dict(count=1, key="QSwCAA==", position_mm="BQDQBwoF", normal="AIEA", kind="AA=="))


class Detector:
    def __init__(self):
        self.headers = []
        self.on_body = None

    def send_alone(self, header):
        self.headers.append(header)

    def setup(self):
        self.on_body({"body_ready": True, "binding": self.headers[-1]["setup"]["binding"]})

    def reply(self, *, header=None, frame=FRAME, why=None):
        header = header or self.headers[-1]
        self.on_body(dict(body_id=header["id"], request_id=header["request_id"], frame_key=header["frame_key"],
                          binding=header["binding"], frame=frame, why=why))


class LinkTests(unittest.TestCase):
    def setUp(self):
        self.detector = Detector()
        self.events = []
        self.now = 100.0
        stream = SimpleNamespace(publish_body=self.publish)
        self.link = BodyLink(self.detector, stream=stream, clock=lambda: self.now)

    def publish(self, scope, **event):
        self.events.append((scope, event))
        return True

    def submit(self, *, frame_id=8, scope=SCOPE, setup=SETUP, observation="person", person=PERSON, age=0.1):
        return self.link.submit(frame_id, frame_key=["camera", frame_id], scope=scope, setup=setup,
                                capture_mono=self.now - age, observation=observation, person=person)

    def setup_worker(self):
        self.assertFalse(self.submit())
        self.assertEqual(self.detector.headers[-1]["kind"], "setup_body")
        self.detector.setup()

    def test_async_setup_and_exact_reply(self):
        self.assertEqual(self.detector.headers, [])
        self.setup_worker()
        self.assertTrue(self.submit())
        request = self.detector.headers[-1]
        self.assertEqual(request["frame_key"], ["camera", 8])
        self.assertEqual(request["binding"], BINDING)
        self.assertFalse(self.submit(frame_id=9))
        self.detector.reply()
        self.assertEqual(self.events[-1], (SCOPE, dict(frame=FRAME, capture_mono=99.9)))
        self.assertFalse(self.submit(frame_id=9))

    def test_a_one_frame_miss_keeps_the_last_frame_until_its_lease_ends(self):
        self.setup_worker()
        self.submit()
        self.detector.reply()
        shown = len(self.events)
        for frame_id, why in ((9, "pose_unavailable"), (10, "insufficient_depth")):
            self.now += 0.5
            self.submit(frame_id=frame_id)
            self.detector.reply(frame=None, why=why)
            self.assertEqual(len(self.events), shown, "%s: the figure keeps its own lease" % why)
        self.now += BODY_LEASE_S
        self.submit(frame_id=11)
        self.detector.reply(frame=None, why="pose_unavailable")
        self.assertEqual(self.events[-1][1], dict(reason="pose_unavailable"), "past the lease, the clear goes out")
        self.submit(frame_id=12)
        self.detector.reply()
        self.now += 0.5
        self.submit(frame_id=13, observation="empty")
        self.detector.reply(frame=None, why="empty")
        self.assertEqual(self.events[-1][1], dict(reason="empty"), "the room empty: at once")
        self.submit(frame_id=14)
        self.detector.reply()
        self.link.clear("ambiguous")
        self.assertEqual(self.events[-1][1], dict(reason="ambiguous"), "two people: at once")

    def test_every_frame_of_a_four_frames_a_second_camera_carries_a_body(self):
        """The camera sends about 4.2 frames a second: frames 0.24 s apart each get a body when the worker has answered
        the one before. A 0.3 s gap skipped every other frame, and the figure moved at 2 a second."""
        self.setup_worker()
        for frame_id in range(20, 32):
            self.now += 0.238
            self.assertTrue(self.submit(frame_id=frame_id), "frame %d" % frame_id)
            self.detector.reply()
        self.assertEqual((self.link.counts["sent"], self.link.counts["skipped_rate"]), (12, 0))
        self.now += 0.1
        self.assertFalse(self.submit(frame_id=40), "a frame sooner than 0.2 s after the last is still skipped")
        self.assertEqual(self.link.counts["skipped_rate"], 1)

    def test_busy_shared_worker_retries_setup_and_preserves_learning_reset(self):
        with patch.object(self.detector, "send_alone", return_value=False):
            self.assertFalse(self.submit())
        self.setup_worker()
        with patch.object(self.detector, "send_alone", return_value=False):
            self.assertFalse(self.submit())
        self.assertTrue(self.submit())
        self.assertTrue(self.detector.headers[-1]["reset_learning"])
        self.assertEqual(self.link.counts["skipped_busy"], 2)
        self.assertEqual(self.link.counts["sent"], 1)

    def test_expiry_and_mismatched_key(self):
        self.setup_worker()
        self.submit()
        self.detector.reply(header={**self.detector.headers[-1], "frame_key": "wrong"})
        self.assertEqual(self.events[-1][1], dict(reason="unavailable"))
        self.now += 0.5
        self.submit()
        self.now += BODY_LEASE_S
        self.detector.reply()
        self.assertEqual(self.events[-1][1], dict(reason="stale"))
        self.assertFalse(self.submit(age=BODY_LEASE_S + 0.1))

    def test_ambiguity_invalidates_late_ready_and_scope_rebinds(self):
        self.setup_worker()
        self.submit()
        request = self.detector.headers[-1]
        self.link.clear("ambiguous", scope=SCOPE)
        self.detector.reply(header=request)
        self.assertEqual(self.events[-1][1], dict(reason="ambiguous"))
        scope = {**SCOPE, "track_epoch": 4, "content_revision": "new-scan"}
        setup = {**SETUP, "binding": {**BINDING, "track_epoch": 4, "content_revision": "new-scan"}}
        self.assertFalse(self.submit(scope=scope, setup=setup))
        self.detector.setup()
        self.assertTrue(self.submit(scope=scope, setup=setup))
        self.detector.reply()
        self.assertEqual(self.events[-1][0], scope)

    def test_empty_batch_is_reset_by_unknown_and_warmup_clear_is_not_repeated(self):
        self.setup_worker()
        self.submit(observation="empty", person=None)
        self.detector.reply(frame=None, why="warming_background")
        published = len(self.events)
        self.submit(observation="empty", person=None)
        self.assertFalse(self.detector.headers[-1]["reset_learning"])
        self.detector.reply(frame=None, why="warming_background")
        self.assertEqual(len(self.events), published)
        self.link.clear("pose_unavailable")
        self.submit(observation="empty", person=None)
        self.assertTrue(self.detector.headers[-1]["reset_learning"])

    def test_same_track_revision_change_rejects_old_body_reply(self):
        self.setup_worker()
        self.submit(person={**PERSON, "association_revision": 1})
        old = self.detector.headers[-1]
        self.link.prepare(SCOPE, BINDING, PERSON["track_id"], 2)
        self.detector.reply(header=old)
        self.assertEqual(self.events[-1][1], dict(reason="scope_changed"))
        self.assertFalse(any("frame" in event for _, event in self.events))
        self.now += 0.5
        self.assertTrue(self.submit(person={**PERSON, "association_revision": 2}))
        self.assertTrue(self.detector.headers[-1]["reset_learning"])
        self.detector.reply()
        self.assertEqual(self.events[-1][1]["frame"], FRAME)


class PipelineTests(unittest.TestCase):
    def test_exact_key_comes_from_retained_bundle_metadata(self):
        bundle = SimpleNamespace(session="camera", rgb_seq=10)
        meta = dict(session="camera", rgb_seq=10, depth_seq=21)
        self.assertEqual(bundle_frame_key(bundle, meta), ["camera", 10, 21])
        for changes in (dict(session="other"), dict(rgb_seq=11), dict(depth_seq=None), dict(depth_seq=True)):
            self.assertIsNone(bundle_frame_key(bundle, {**meta, **changes}))

    def test_pipeline_handoff_uses_bound_scope_and_body_joints(self):
        track = dict(track_id="one", state="confirmed", association="confident", xy_m=[0, 2])
        joints = [(-0.2, 2, 1.2), (0.2, 2, 1.2), (-0.16, 2, 0.8), (0.16, 2, 0.8)]
        pose = dict(kind="keypoints", validity="valid", joints_room=[
            dict(j=j, status="supported", xyz=list(xyz)) for j, xyz in zip((5, 6, 11, 12), joints)
        ] + [dict(j=0, status="supported", xyz=[0, 2, 1.4])])
        evidence = dict(frame_key="exact", binding=BINDING, bounds_px=[200, 30, 300, 100],
                        alignment_px=1, localization_px=2, motion_px=3)
        data = BodyInput("exact", "synthetic-run", 3, "synthetic-scan", "synthetic-transform", "synthetic-depth",
                         99.5, "infrared", "person", track, pose, evidence)
        self.assertTrue(qualified(data, SCOPE))
        self.assertFalse(qualified(data, {**SCOPE, "transform_id": "other"}))
        person = body_person(track, pose, lambda _: (0, 10), 30)
        self.assertEqual(set(person["joints"]), {"left_shoulder", "right_shoulder", "left_hip", "right_hip"})
        self.assertEqual(person["facing_deg"], 0)
        self.assertIsNone(body_person({**track, "state": "coasting"}, pose, lambda _: (0, 10), 30))
        self.assertIsNone(body_person({**track, "association": "ambiguous"}, pose, lambda _: (0, 10), 30))
        perceiver = Perceiver.__new__(Perceiver)
        perceiver.home = "den"
        perceiver.body = SimpleNamespace(prepare=lambda *args: None)
        perceiver.camera = SimpleNamespace(room_from_left=SETUP["room_from_left"], rig=SimpleNamespace(left_k=SETUP["left_k"]))
        perceiver.track_method = SimpleNamespace(tracker={"heading_max_sd_deg": 30})
        perceiver.t = SimpleNamespace(records=SimpleNamespace(body_heading=lambda _: (0, 10)))
        request = perceiver._body_request(data)
        self.assertEqual(request["scope"], {**SCOPE, "mode": "infrared"})
        self.assertEqual(request["setup"], SETUP)
        self.assertEqual(request["frame_key"], "exact")
        self.assertEqual(request["person"]["head_evidence"], evidence)
        rgb = dict(k=[400, 400, 320, 180, 0], d=[0] * 8,
                   from_left=[[[1, 0, 0], [0, 1, 0], [0, 0, 1]], [0.03, 0, 0]], size=[640, 360])
        perceiver.camera.rig = SimpleNamespace(left_k=SETUP["left_k"], rgb_k=rgb["k"], rgb_d=rgb["d"],
                                              rotation=rgb["from_left"][0], translation=rgb["from_left"][1],
                                              rgb_size=rgb["size"])
        from dataclasses import replace
        rgb_data = replace(data, mode="rgb", track={**track, "association_revision": 7})
        rgb_request = perceiver._body_request(rgb_data)
        self.assertEqual(rgb_request["setup"]["rgb"], rgb)
        self.assertEqual(rgb_request["person"]["association_revision"], 7)
        self.assertEqual(rgb_request["person"]["association_status"], "confident")
        self.assertEqual(rgb_request["person"]["mode"], "rgb")
        perceiver.depth_scale = 0.971   # step 2's depth scale reaches the figure's depth through the setup
        self.assertEqual(perceiver._body_request(data)["setup"], {**SETUP, "rgb": rgb, "depth_scale": 0.971})

    def pipeline_process(self, mode, provider):
        calls = []
        detector = SimpleNamespace(next_id=42)
        detector.detect = lambda bundle, delay, **key: calls.append(("detect", key)) or {"luma": 100 if mode == "rgb" else 0, "row": {}}
        camera = SimpleNamespace(check_frame=lambda _: None, error_bound_m=.1)
        observation = dict(kind="keypoints", obs_id="current-pose", validity="valid", score=.9)
        track = SimpleNamespace(id=1, obs_ids={"current-pose"}, heading_as_of=None, last_seen=100)
        track_record = dict(track_id="one", position=dict(x=0, y=1, cov=[[0.1, 0], [0, 0.1]]), heading=None,
                            association=dict(status="confident", since_ts=99.0))
        meta = dict(session="camera", rgb_seq=7, depth_seq=20,
                    raw={"frames": {"depth": {"sequence": 20, "host_monotonic_timestamp_seconds": 100.0}}})
        tools = SimpleNamespace(takes=SimpleNamespace(read_frame=lambda _: (meta, None)),
                                roommodel=SimpleNamespace(RegistrationRefused=ValueError),
                                observe=SimpleNamespace(observe=lambda *_, **__: [observation]),
                                records=SimpleNamespace(frames_from_observations=lambda *args: ([], None),
                                                        track_record=lambda *args: track_record))
        perceiver = Perceiver.__new__(Perceiver)
        perceiver.t, perceiver.camera, perceiver.detector = tools, camera, detector
        perceiver.home, perceiver.scope = "den", SCOPE
        perceiver.last_t_host, perceiver.last_capture, perceiver.session, perceiver.sessions = 99.0, 99.0, "camera", 3
        perceiver.counts = {"frames": 0, "observations": 0, "weak_only_hidden": 0}
        perceiver.arrivals, perceiver.lags, perceiver.drops = [], [], []
        perceiver.run_id, perceiver.frame_id, perceiver.retired, perceiver.last_seq = "synthetic-run", 0, set(), 6
        perceiver.clock_reset_candidate = perceiver.last_input_mono = perceiver.calibration_id = None
        perceiver.associations, perceiver.hops = {}, {}
        perceiver.seated = Published()
        perceiver.facing = Facing(lambda _: None, 30.0)   # no facing sample from these fakes
        perceiver.speed = Speed()   # posture's floor speed
        perceiver.labels, perceiver.now = None, lambda: 1000
        perceiver.snapshot = TrackingSnapshot(perceiver.run_id, 3, 1, None, "starting", None, "unknown", ())
        perceiver.settings, perceiver.track_method = None, SimpleNamespace(tracker={})
        perceiver.core, perceiver.strong = SimpleNamespace(step=lambda *args: [(track, "confirmed")]), set()
        perceiver.clock = SimpleNamespace(add=lambda *_: None, offset=lambda: 0)
        perceiver._transit = lambda *_: None
        perceiver.require_source_correction, perceiver.source_correction = False, None
        perceiver.mode, perceiver.light_since, perceiver.dark_since = mode, None, 99
        perceiver.event = lambda ago, tracks, camera, now: {**camera, "camera": {"why": None, **camera},
                                                            "seq": 2, "capture_ts": 123.0, "tracks": tracks}
        perceiver.body_provider = lambda *_, **data: calls.append(("provider", data)) or provider(data)
        perceiver._body_request = lambda value: {"frame_key": value.frame_key, "mode": value.mode}
        perceiver.body = SimpleNamespace(submit=lambda frame_id, **kwargs: calls.append(("submit", frame_id, kwargs)),
                                         clear=lambda reason: calls.append(("clear", reason)))
        bundle = SimpleNamespace(data=b"fake", session="camera", rgb_seq=7, t_host=100.0,
                                 arrival_mono=100.1, arrival_unix=1000, fetch_s=0, parse_s=0, receipt_arrival_mono=100.1)
        return calls, perceiver.process(bundle, {}, 100.2)

    def pipeline_input(self, data, *, key=None):
        return BodyInput(data["frame_key"] if key is None else key, SCOPE["run_id"], SCOPE["track_epoch"],
                         SCOPE["content_revision"], SCOPE["transform_id"], BINDING["depth_profile"],
                         99.5, "infrared" if data["mode"] == "none" else "rgb", "person")

    def test_rgb_provider_runs_after_current_track_association(self):
        calls, event = self.pipeline_process("rgb", self.pipeline_input)
        key = ["synthetic-run", 3, 1]
        self.assertEqual(calls[0], ("detect", {}))
        kind, data = calls[1]
        self.assertEqual((kind, data["mode"]), ("provider", "rgb"))
        self.assertEqual(data["observations"][0]["obs_id"], "current-pose")
        self.assertEqual(data["tracks"], [{**event["tracks"][0], "mask_association_status": "ambiguous"}])
        self.assertEqual(data["supported"], [{"current-pose"}])
        self.assertEqual(data["association"], {"synthetic-run-e3-one": ("confident", 99.0)})
        self.assertEqual(data["tracks"][0]["association_revision"], 0)
        self.assertEqual(data["tracks"][0]["mask_association_status"], "ambiguous")
        self.assertEqual(calls[2], ("submit", 42, {"frame_key": key, "mode": "rgb"}))

    def test_ir_body_submission_survives_legacy_rgb_darkness(self):
        calls, event = self.pipeline_process("none", self.pipeline_input)
        key = ["synthetic-run", 3, 1]
        self.assertEqual(calls, [("detect", {}),
                                 ("provider", {"frame_key": key, "mode": "none", "observations": (), "tracks": (),
                                               "supported": (), "association": {}, "capture_mono": 100.0}),
                                 ("submit", 42, {"frame_key": key, "mode": "infrared"})])
        self.assertEqual(event["why"], "too dark")

    def test_wrong_provider_key_clears_body_without_changing_markers(self):
        calls, event = self.pipeline_process("rgb", lambda data: self.pipeline_input(data, key=["camera", 7, 19]))
        self.assertEqual(calls[-1], ("clear", "unavailable"))
        self.assertEqual(event["tracks"][0]["track_id"], "synthetic-run-e3-one")


def read_event(response):
    fields = {}
    while True:
        raw = response.readline()
        if not raw:
            raise EOFError("stream closed before the next event")
        line = raw.decode().rstrip()
        if not line:
            if "event" in fields:
                return fields["event"], json.loads(fields["data"])
            fields.clear()
        if ": " in line and not line.startswith(":"):
            name, value = line.split(": ", 1)
            fields[name] = value


class StreamTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.stream = PeopleStream(port=0, clock=lambda: 10.0, body_clock=lambda: self.now,
                                   wall_clock=lambda: 1000.0, heartbeat_s=0.05).start()
        self.addCleanup(self.stream.close)
        self.base = f"http://127.0.0.1:{self.stream.port}{BASE}den/"

    def test_the_held_object_rides_along_only_when_the_frame_has_it(self):
        held = {"label": "book", "confidence": 0.91, "source": "detector"}
        self.assertTrue(self.stream.publish_body(SCOPE, frame={**FRAME, "object": held}, capture_mono=99.9))
        with urllib.request.urlopen(self.base + "body", timeout=3) as response:
            read_event(response)
            name, event = read_event(response)
            self.assertEqual((name, event["object"]), ("body", held))
        self.now += 0.1
        self.assertTrue(self.stream.publish_body(SCOPE, frame=FRAME, capture_mono=100.0))
        with urllib.request.urlopen(self.base + "body", timeout=3) as response:
            read_event(response)
            name, event = read_event(response)
            self.assertNotIn("object", event, "without the held-object pass, the frame is exactly as before")

    def test_capture_deadline_and_clear_replay_with_independent_markers(self):
        self.stream.publish({"seq": 42, "tracks": [{"track_id": "anonymous"}]})
        spot = {"epoch": "synthetic-preview", "valid_until_mono": 15, "seq": 10}
        self.stream.publish_spot(spot)
        self.assertTrue(self.stream.publish_body(SCOPE, frame={**FRAME, "head": "must never be forwarded"}, capture_mono=99.5))
        with urllib.request.urlopen(self.base + "body", timeout=3) as response:
            self.assertEqual(response.headers["X-Home-Den-Body-Schema"], BODY_SCHEMA)
            self.assertEqual(read_event(response), ("body_clock", {"server_ts": 1000}))
            name, first = read_event(response)
            self.assertEqual(name, "body")
            self.assertEqual(first["expires_ts"] - first["capture_ts"], BODY_LEASE_S)
            self.assertNotIn("head", first)
        self.now += 0.2
        with urllib.request.urlopen(self.base + "body-v2", timeout=3) as response:
            read_event(response)
            self.assertEqual(read_event(response), ("body", first))
        self.now += BODY_LEASE_S - 0.6   # past the lease: captured 0.5 s before, 0.2 s since
        with urllib.request.urlopen(self.base + "body-v2", timeout=3) as response:
            read_event(response)
            name, cleared = read_event(response)
            self.assertEqual((name, cleared["status"], cleared["reason"]), ("body", "clear", "stale"))
            self.assertGreater(cleared["seq"], first["seq"])
            self.assertNotIn("points", cleared)
        with urllib.request.urlopen(self.base + "stream", timeout=3) as response:
            self.assertEqual(read_event(response), ("spot_clock", {"epoch": spot["epoch"], "server_mono": 10.0}))
            self.assertEqual(read_event(response), ("spot_snapshot", spot))
            self.assertEqual(read_event(response)[1]["tracks"], [{"track_id": "anonymous"}])

    def test_slow_ready_expiry_preserves_newer_ready_and_clear_precedes_recovery(self):
        from stream import _Client
        client = _Client(2)
        self.stream.body_clients.add(client)
        self.stream.publish_body(SCOPE, frame=FRAME, capture_mono=99.1)
        old = client.events.popleft()
        self.stream.publish_body(SCOPE, reason="ambiguous")
        self.stream.publish_body(SCOPE, frame=FRAME, capture_mono=100)
        self.now += 0.2
        self.assertIsNone(self.stream.body_for_write(old, client))
        self.assertEqual([item[0]["status"] for item in client.events], ["clear", "ready"])
        self.assertEqual(self.stream.body_latest[0]["status"], "ready")

    def test_measured_object_label_survives_stream_allowlist(self):
        obj = dict(label="book", confidence=0.91, source="detector")
        self.assertTrue(self.stream.publish_body(SCOPE, frame={**FRAME, "object": obj, "private_masks": []}, capture_mono=100))
        ready = self.stream.body_latest[0]
        self.assertEqual(ready["object"], obj)
        self.assertNotIn("private_masks", ready)

    def test_missing_provider_leaves_people_and_spot_routes_available(self):
        self.stream.body_available = False
        self.stream.publish({"seq": 42, "tracks": [{"track_id": "anonymous"}]})
        spot = {"epoch": "synthetic-preview", "valid_until_mono": 15, "seq": 10}
        self.stream.publish_spot(spot)
        with self.assertRaises(urllib.error.HTTPError) as missing:
            urllib.request.urlopen(self.base + "body-v2", timeout=3)
        with missing.exception as error:
            self.assertEqual(error.code, 503)
            self.assertEqual(json.load(error)["error"], "body_unavailable")
            self.assertIsNone(error.headers.get("X-Home-Den-Body-Schema"))
        with urllib.request.urlopen(self.base + "stream", timeout=3) as response:
            self.assertEqual(read_event(response), ("spot_clock", {"epoch": spot["epoch"], "server_mono": 10.0}))
            self.assertEqual(read_event(response), ("spot_snapshot", spot))
            self.assertEqual(read_event(response)[1]["tracks"], [{"track_id": "anonymous"}])


class SharedSlotTest(unittest.TestCase):
    """The body and the Qwen ask on the detector process's one optional slot, with den-live's real pieces: BodyLink at
    its own MIN_INTERVAL_S, ask.Asker, and perceive.DetectorWorker.send_alone (test_ask.SharedSlot). Frames come at LA's
    pace, about 4.2 a second. Each frame, the last body pass answers, the body is submitted (Perceiver.finish), then
    the ask observes (Loop._publish)."""

    SPACINGS = [0.22, 0.25, 0.24, 0.23, 0.26, 0.238, 0.245, 0.235]

    def test_the_figure_moves_at_the_cameras_pace_and_the_asks_still_go_out(self):
        import test_ask

        class RealBody(test_ask.SharedSlot):
            on_body = None

            def __init__(self, clock):
                super().__init__(clock, 0.0)
                self.sent, self.frames_in = None, 0
                self.link = BodyLink(self, stream=SimpleNamespace(publish_body=lambda scope, **event: True), clock=clock)

            def frame(self):
                self.frames_in += 1
                if self.worker.optional is not None and self.worker.optional[0] == "body":
                    self.worker.optional = None   # the reader frees the slot, then hands the reply on
                    header = self.sent
                    if header["kind"] == "setup_body":
                        self.on_body({"body_ready": True, "binding": header["setup"]["binding"]})
                    else:
                        self.on_body(dict(body_id=header["id"], request_id=header["request_id"], why=None,
                                          frame_key=header["frame_key"], binding=header["binding"], frame=FRAME))
                self.link.submit(self.frames_in, frame_key=["camera", self.frames_in], scope=SCOPE, setup=SETUP,
                                 capture_mono=self.clock() - 0.1, observation="person", person=PERSON)

            def send_alone(self, header):
                if header["kind"] == "stills":
                    return super().send_alone(header)
                admitted = self.worker.send_alone(header)
                if admitted:
                    self.worker.writes.get_nowait()   # the writer thread
                    self.sent = header
                return admitted

        case = test_ask.BodyEveryFrameTest("test_body_every_0_2_s_with_frames_0_2_s_apart")
        case.setUp()
        slot = RealBody(case.clock)
        case.asker.detector, slot.on_stills, case.detector = slot, case.asker._stills, slot
        start, n = case.clock.t, 0
        while case.clock.t - start < 60.0:
            case.tick(seconds=self.SPACINGS[n % len(self.SPACINGS)])
            n += 1
        outcomes = [line["outcome"] for line in case.log_lines()]
        # 0.3 s gave 2.1 bodies a second here. Each ask's stills take the slot from one body pass.
        self.assertGreater(slot.link.counts["frames"] / 60.0, 3.5, slot.link.counts)
        self.assertNotIn("stills_failed", outcomes)
        self.assertGreaterEqual(outcomes.count("completed"), 20)


if __name__ == "__main__":
    unittest.main()
