"""Synthetic body-v2 worker ordering, exact-frame pipeline handoff and SSE replay."""
import json
import time
import unittest
import urllib.request
from types import SimpleNamespace

from body_input import BodyInput, qualified
from body_link import BodyLink
from perceive import Perceiver, body_person
from stream import BODY_SCHEMA, BASE, PeopleStream


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

    def test_expiry_and_mismatched_key(self):
        self.setup_worker()
        self.submit()
        self.detector.reply(header={**self.detector.headers[-1], "frame_key": "wrong"})
        self.assertEqual(self.events[-1][1], dict(reason="unavailable"))
        self.now += 0.5
        self.submit()
        self.now += 1.0
        self.detector.reply()
        self.assertEqual(self.events[-1][1], dict(reason="stale"))
        self.assertFalse(self.submit(age=1.1))

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


class PipelineTests(unittest.TestCase):
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

    def test_body_submission_does_not_depend_on_the_legacy_rgb_mode(self):
        calls = []
        detector = SimpleNamespace(next_id=42)
        detector.detect = lambda bundle, delay, **key: calls.append(key) or {"luma": 0, "row": {}}
        camera = SimpleNamespace(check_frame=lambda _: None)
        tools = SimpleNamespace(takes=SimpleNamespace(read_frame=lambda _: ({}, None)),
                                roommodel=SimpleNamespace(RegistrationRefused=ValueError))
        perceiver = Perceiver.__new__(Perceiver)
        perceiver.t, perceiver.camera, perceiver.detector = tools, camera, detector
        perceiver.last_t_host, perceiver.last_capture, perceiver.session = 99.0, 99.0, "camera"
        perceiver.counts, perceiver.arrivals = {"frames": 0}, []
        perceiver.clock = SimpleNamespace(add=lambda *_: None, offset=lambda: 0)
        perceiver._transit = lambda *_: None
        perceiver.require_source_correction = False
        perceiver.mode, perceiver.light_since, perceiver.dark_since = "none", None, 1
        perceiver.event = lambda *args: args[2]
        perceiver._body_input = lambda *_: "qualified input"
        perceiver._body_request = lambda value: {"frame_key": "exact-infrared"} if value else None
        perceiver.body = SimpleNamespace(submit=lambda frame_id, **kwargs: calls.append((frame_id, kwargs)))
        bundle = SimpleNamespace(data=b"fake", session="camera", t_host=100.0, arrival_mono=100.1)
        event = perceiver.process(bundle, {}, 100.2)
        self.assertEqual(calls, [{"frame_key": "exact-infrared"}, (42, {"frame_key": "exact-infrared"})])
        self.assertEqual(event["why"], "too dark")


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
        self.stream = PeopleStream(port=0, clock=lambda: self.now, wall_clock=lambda: 1000.0, heartbeat_s=0.05).start()
        self.addCleanup(self.stream.close)
        self.base = f"http://127.0.0.1:{self.stream.port}{BASE}den/"

    def test_capture_deadline_and_clear_replay_with_independent_markers(self):
        self.stream.publish({"seq": 42, "tracks": [{"track_id": "anonymous"}]})
        self.assertTrue(self.stream.publish_body(SCOPE, frame={**FRAME, "head": "must never be forwarded"}, capture_mono=99.5))
        with urllib.request.urlopen(self.base + "body", timeout=3) as response:
            self.assertEqual(response.headers["X-Home-Den-Body-Schema"], BODY_SCHEMA)
            self.assertEqual(read_event(response), ("body_clock", {"server_ts": 1000}))
            name, first = read_event(response)
            self.assertEqual(name, "body")
            self.assertEqual(first["expires_ts"] - first["capture_ts"], 1)
            self.assertNotIn("head", first)
        self.now += 0.2
        with urllib.request.urlopen(self.base + "body", timeout=3) as response:
            read_event(response)
            self.assertEqual(read_event(response), ("body", first))
        self.now += 0.4
        with urllib.request.urlopen(self.base + "body", timeout=3) as response:
            read_event(response)
            name, cleared = read_event(response)
            self.assertEqual((name, cleared["status"], cleared["reason"]), ("body", "clear", "stale"))
            self.assertGreater(cleared["seq"], first["seq"])
            self.assertNotIn("points", cleared)
        with urllib.request.urlopen(self.base + "stream", timeout=3) as response:
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


if __name__ == "__main__":
    unittest.main()
