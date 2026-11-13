"""Evidence regressions on real observe/tracker pins and generated RGB/depth."""
import base64
import hashlib
import io
import json
import queue
import sys
import threading
import time
import unittest
import zipfile
from unittest.mock import patch

import ask
import contracts
import coordinator_live
import fixture_rgb
import occupancy
import perceive
import preview
import run_local
import sofa_check
import source
import stills
from contracts import INFERENCE_S, InferenceReply
from test_ask import completion, facts
from test_preview import BINDING


class EvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scene = fixture_rgb.Scene()
        cls.addClassCleanup(cls.scene.close)

    def setUp(self):
        self.p = self.scene.perceiver()

    def advance(self, at, seq, **kw):
        bundle, reply = self.scene.sample(at, seq, **kw)
        prepared = self.p.prepare(bundle, at)
        return self.p.finish(prepared, reply, {"status": "ok"}, at + 0.04)

    def confirmed(self):
        for i in range(17):
            self.advance(100.0 + i / 5, i)
        return self.p.snapshot.tracks[0]

    def test_current_metric_pose_and_private_same_frame_body_input(self):
        track = self.confirmed()
        self.assertEqual(track.association_status, "confident")
        self.assertAlmostEqual(track.supported_pose["left_hip"][0][2], 0.55, delta=0.025)
        message = self.p.body_input
        self.assertEqual(message["frame"], self.p.snapshot.frame)
        self.assertEqual(message["track_id"], track.track_id)
        self.assertEqual(len(message["head_native"]), 5)
        self.assertIsNotNone(message["depth_mm"])

    def test_tentative_rival_and_abrupt_darkness_invalidate_current_association(self):
        track = self.confirmed()
        self.advance(103.4, 17, extra=True)
        self.advance(103.6, 18, extra=True)
        after = next(t for t in self.p.snapshot.tracks if t.track_id == track.track_id)
        self.assertGreater(after.association_revision, track.association_revision)
        self.assertEqual(after.association_status, "ambiguous")
        self.assertIsNone(self.p.body_input)
        self.advance(103.8, 19, luma=10)
        self.assertEqual((self.p.snapshot.status, self.p.snapshot.presence), ("degraded", "unknown"))

    # ---- the grace (plan 02-3): identity survives one late frame or one unsafe frame ----

    def test_one_dropped_frame_keeps_the_epoch_and_the_track_id(self):
        """At about 2 frames a second, one frame lost is a gap past the pose's 1.5 s expiry: the loop publishes a
        stale snapshot (idle), and the next frame goes on in the same epoch with the same track."""
        track = self.confirmed()
        last = self.p.snapshot.frame.capture_mono
        self.p.idle({"status": "ok"}, last + perceive.FRESH_S + 0.1)   # what Loop does once the pose has expired
        self.assertEqual((self.p.snapshot.status, self.p.snapshot.camera_epoch), ("stale", 1))
        self.advance(last + 1.8, 17)
        self.assertEqual(self.p.snapshot.status, "fresh")
        self.assertEqual((self.p.sessions, self.p.snapshot.frame.camera_epoch), (1, 1))
        self.assertEqual([t.track_id for t in self.p.snapshot.tracks], [track.track_id])

    def test_three_seconds_without_a_fresh_frame_bumps_the_epoch(self):
        for gap, epoch in ((perceive.EPOCH_GAP_S - 0.2, 1), (perceive.EPOCH_GAP_S + 0.2, 2)):
            with self.subTest(gap=gap):
                self.p = self.scene.perceiver()
                track = self.confirmed()
                last = self.p.snapshot.frame.capture_mono
                self.p.idle({"status": "ok"}, last + perceive.FRESH_S + 0.1)
                self.advance(last + gap, 17)
                self.assertEqual((self.p.sessions, self.p.snapshot.camera_epoch), (epoch, epoch))
                self.assertEqual(self.p.snapshot.frame.camera_epoch, epoch)
                if epoch == 2:
                    self.assertNotIn(track.track_id, [t.track_id for t in self.p.snapshot.tracks])

    def test_one_unsafe_frame_keeps_the_revision_but_marks_the_frame_ambiguous(self):
        track = self.confirmed()
        self.advance(103.4, 17, extra=True)   # a second person box at 0.88: this frame isn't safe
        unsafe, = (t for t in self.p.snapshot.tracks if t.track_id == track.track_id)
        self.assertEqual(unsafe.association_status, "ambiguous", "the ask skips this frame")
        self.assertEqual(unsafe.association_revision, track.association_revision)
        self.assertIsNone(self.p.body_input)
        self.advance(103.6, 18)
        again, = self.p.snapshot.tracks
        self.assertEqual((again.track_id, again.association_status), (track.track_id, "confident"))
        self.assertEqual(again.association_revision, track.association_revision)

    def test_two_unsafe_frames_bump_the_revision(self):
        track = self.confirmed()
        self.advance(103.4, 17, extra=True)
        self.advance(103.6, 18, extra=True)
        unsafe, = (t for t in self.p.snapshot.tracks if t.track_id == track.track_id)
        self.assertEqual(unsafe.association_status, "ambiguous")
        self.assertEqual(unsafe.association_revision, track.association_revision + 1)
        self.advance(103.8, 19)   # the rival goes; its track stays a while, as a coasting one
        pending, = (t for t in self.p.snapshot.tracks if t.track_id == track.track_id)
        self.assertEqual(pending.association_status, "ambiguous", "safe again for one frame only")
        self.advance(104.0, 20)
        back, = (t for t in self.p.snapshot.tracks if t.track_id == track.track_id)
        self.assertEqual((back.association_status, back.association_revision),
                         ("confident", track.association_revision + 2))

    def test_brief_gap_retains_id_and_retired_session_cannot_return(self):
        track = self.confirmed()
        self.advance(103.6, 18)
        self.assertEqual(self.p.snapshot.tracks[0].track_id, track.track_id)
        self.advance(103.8, 0, session="oak-2")
        self.assertNotEqual(self.p.snapshot.camera_epoch, 1)
        old, _ = self.scene.sample(104.0, 19)
        self.assertIsNone(self.p.prepare(old, 104.0))
        self.assertNotEqual(self.scene.perceiver().run_id, self.p.run_id)

    def test_seated_is_published_once_and_resets_on_a_revision_bump(self):
        """Contract 2's seated on the real pins: the reader's hips are low, so after the hold the track says seated,
        with since_mono a frame's capture time; one unsafe frame keeps it; two move the revision, and seated starts
        its hold again."""
        self.confirmed()
        for i in range(17, 20):   # den-track is sure of the reader from 1.8 s; the 2 s hold ends 1.6 s later
            self.advance(100.0 + i / 5, i)
        track, = self.p.snapshot.tracks
        self.assertEqual((track.seated.state, track.seated.basis), (True, "hips"))
        captures = {round(100.0 + i / 5, 3) for i in range(20)}
        self.assertIn(round(track.seated.since_mono, 3), captures, "the hold's first sample")
        self.advance(104.0, 20, extra=True)
        kept, = (t for t in self.p.snapshot.tracks if t.track_id == track.track_id)
        self.assertEqual(kept.seated, track.seated, "one unsafe frame keeps the episode")
        self.advance(104.2, 21, extra=True)
        moved, = (t for t in self.p.snapshot.tracks if t.track_id == track.track_id)
        self.assertEqual(moved.association_revision, track.association_revision + 1)
        self.assertEqual(moved.seated, contracts.SeatedState(False, None, None))
        for i in range(22, 40):
            self.advance(100.0 + i / 5, i)
        again, = (t for t in self.p.snapshot.tracks if t.track_id == track.track_id)
        self.assertTrue(again.seated.state)
        self.assertGreater(again.seated.since_mono, 104.2, "a new hold, after the bump")

    def test_the_people_event_carries_the_person_binding(self):
        """The people projection (den-live-people-1) adds service_run_id, camera_epoch and association_revision to each
        marker: the binding every consumer matches, the same as the snapshot's."""
        self.confirmed()
        event = self.advance(103.4, 17)
        marker, = event["tracks"]
        track, = self.p.snapshot.tracks
        self.assertEqual(marker["track_id"], track.track_id)
        self.assertEqual((marker["service_run_id"], marker["camera_epoch"], marker["association_revision"]),
                         (self.p.run_id, self.p.snapshot.camera_epoch, track.association_revision))
        self.assertEqual(json.loads(json.dumps(event))["tracks"][0]["association_revision"], track.association_revision)
        moved = self.advance(103.6, 18, extra=True), self.advance(103.8, 19, extra=True)
        marker = next(m for m in moved[-1]["tracks"] if m["track_id"] == track.track_id)
        self.assertEqual(marker["association_revision"], track.association_revision + 1)

    def test_late_worker_result_expires_even_for_empty_detection(self):
        bundle, reply = self.scene.sample(100, 1, empty=True)
        prepared = self.p.prepare(bundle, 100)
        self.assertIsNone(self.p.finish(prepared, reply, {"status": "ok"}, 101.6))
        self.assertEqual(self.p.counts["expired"], 1)

    def test_rgb_depth_pair_boundary(self):
        self.confirmed()
        self.advance(103.4, 17, skew=0.067)
        self.assertIsNotNone(self.p.body_input)
        self.advance(103.6, 18, skew=0.068)
        self.assertIsNone(self.p.body_input)

    def test_known_delay_after_anchor_is_not_reset_by_advancing_sequences(self):
        self.confirmed()
        bundle, _ = self.scene.sample(103.4, 17, arrival=105.4)
        self.assertIsNone(self.p.prepare(bundle, 105.4))
        self.assertEqual(self.p.counts["expired"], 1)

    def test_frigate_and_seated_gate_deliver_bound_qwen_reply_to_coordinator(self):
        import numpy as np
        from PIL import Image

        scene = self.scene
        self.p.scope["content_revision"] = BINDING["scan_revision"]
        binding = dict(BINDING, transform_id=self.p.camera.transform_id)
        core, _ = coordinator_live.load_core(sofa_check.archive(scene.path, "coordinator"))
        now = 100.0
        trace, posts, handed_off = [], [], []
        state = scene.path / "bound-inference"
        fixtures = {"F3": {"aim_origin_m": [-1.25, 4.2, 2.5], "horizontal_world_reference": [0, -1, 0]}}
        coordinator = preview.Preview(core, binding=binding, fixtures=fixtures, state=state,
                                      clock=lambda: now, threaded=False, append=trace.append)
        coordinator.configure({"fade_in_s": 0, "pre_aim": False})

        frigate = "off"
        reader = occupancy.Occupancy(lambda: frigate, clock=lambda: now)
        ring = stills.Ring()

        class Detector:
            def send_alone(self, header):
                # Decode the generated gray NV12 plane without loading model weights.
                def decode(nv12, width, height):
                    return np.repeat(nv12[:height, :width, None], 3, axis=2)

                self.on_stills(stills.handle(header, ring, np, Image, decode, scene.tools.takes.check.read_bundle))

        def post(request):
            posts.append(request)
            reading = facts()
            if len(posts) > 1:
                reading["change_vs_prior"] = "no_change"
            return completion(json.dumps(reading))

        asker = ask.Asker(detector=Detector(), occupancy=reader, post=post, state=state,
                          site_id="fixture", camera_id="den-oak", clock=lambda: now,
                          wall=lambda: scene.now + now, start=False)
        self.addCleanup(asker.close)
        observe = run_local.evidence_observer(asker, coordinator)
        offer = coordinator.offer_inference

        def receive(reply):
            # Production must deliver the eligible snapshot before its reply.
            self.assertEqual(coordinator.track_seq, asker.snapshot.seq)
            self.assertEqual(coordinator.eligible_track, reply.track_id)
            self.assertEqual(coordinator.eligibility, reply.eligibility_revision)
            handed_off.append(reply)
            offer(reply)

        frames = {}

        def feed(seq, *, drain=True, **sample):
            nonlocal now
            at = 100 + seq / 5
            now = at + 0.04
            reader.poll_once()
            bundle, result = scene.sample(at, seq, **sample)
            # Use the same bundle for tracking and JPEG lookup. Its gray level
            # identifies the RGB plane in the decoded request.
            data = io.BytesIO()
            nv12 = bytes([seq]) * (640 * 360) + bytes([128]) * (640 * 180)
            with zipfile.ZipFile(io.BytesIO(bundle.data)) as original, \
                    zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as copied:
                for name in original.namelist():
                    copied.writestr(name, scene.synth.npy("|u1", (540, 640), nv12)
                                    if name == "rgb_nv12.npy" else original.read(name))
            bundle = source.Bundle(data.getvalue(), bundle.session, bundle.rgb_seq, bundle.t_host,
                                   bundle.arrival_unix, bundle.arrival_mono)
            prepared = self.p.prepare(bundle, at)
            event = self.p.finish(prepared, result, {"status": "ok"}, now)
            if self.p.snapshot.frame is not None:
                key = self.p.snapshot.frame.key
                ring.keep(key, bundle.data)
                frames[key] = self.p.snapshot.frame
            observe(event, self.p.snapshot)
            if drain:
                while not asker.jobs.empty():
                    job, args, kwargs = asker.jobs.get_nowait()
                    job(*args, **kwargs)

        with patch.object(coordinator, "offer_inference", side_effect=receive):
            for seq in range(25):
                feed(seq)
            track, = self.p.snapshot.tracks
            self.assertEqual(track.association_status, "confident")
            self.assertFalse(posts, "Frigate off must block a supported reader")
            self.assertFalse(handed_off)

            frigate = "on"
            self.assertTrue(track.seated.state, "seated already: perception's answer doesn't wait for Frigate")
            for seq in range(25, 80):
                feed(seq)
                if seq < 29:   # the stills: frames since Frigate read on, the oldest about 1.5 s before the newest
                    self.assertFalse(posts, "Frigate on still needs stills from its own window")
            self.assertTrue(handed_off)
            self.assertEqual({reply.gate["seated_since_mono"] for reply in handed_off}, {coordinator.episode.started})
            self.assertEqual(self.p.snapshot.tracks[0].seated.since_mono, coordinator.episode.started)
            self.assertTrue(coordinator.episode.need.engaged)
            self.assertTrue(coordinator.latest["fixtures"]["F3"]["on"],
                            (coordinator.latest_decision, coordinator.track))
            self.assertTrue(any(row["action"] == "aim_move" and row["outcome"] == "encoded_only"
                                for row in trace))
            self.assertEqual(coordinator.episode.requests[-1], handed_off[-1].request_id)

            # The newest post's reply can still be waiting for the next frame to hand it over.
            self.assertIn(len(posts) - len(handed_off), (0, 1))
            for raw, reply in zip(posts[:len(handed_off)], handed_off, strict=True):
                self.assertIsInstance(reply, InferenceReply)
                self.assertEqual(reply.request_body_sha256, hashlib.sha256(raw).hexdigest())
                request = json.loads(raw)
                self.assertEqual(request["model"], "qwen3-vl-30b")
                self.assertEqual(reply.model["served_alias"], request["model"])
                self.assertEqual(reply.track_id, track.track_id)
                self.assertEqual(reply.gate["seated_track_id"], track.track_id)
                self.assertEqual(reply.gate["frigate_effective"], "on")
                self.assertEqual(reply.association_revision, track.association_revision)
                self.assertEqual(reply.observation["facts"]["activity_hypothesis"], "reading")
                self.assertEqual(reply.valid_until_mono, reply.frames[-1].capture_mono + INFERENCE_S)
                self.assertEqual((reply.service_run_id, reply.camera_epoch), self.p.snapshot.frame.key[:2])
                self.assertEqual(tuple(frames[ref.key] for ref in reply.frames), reply.frames)
                images = [p["image_url"]["url"] for p in request["messages"][1]["content"] if p["type"] == "image_url"]
                self.assertIn(len(images), (2, 3))
                for image, ref in zip(images, reply.frames, strict=True):
                    with Image.open(io.BytesIO(base64.b64decode(image.split(",", 1)[1]))) as jpeg:
                        self.assertEqual(jpeg.getpixel((0, 0)), (ref.rgb.seq,) * 3)

            # Keep tracking the same person while the next model request stalls.
            last = handed_off[-1]
            for seq in range(80, 135):
                feed(seq, drain=False)
            self.assertGreater(now, last.valid_until_mono)
            self.assertEqual(self.p.snapshot.tracks[0].track_id, track.track_id)
            self.assertFalse(coordinator.episode.need.engaged)
            self.assertFalse(coordinator.latest["fixtures"]["F3"]["on"])

    def test_loop_invalidates_while_child_stops_reading_pipe(self):
        program = "import time; print('{\"ready\":true}', flush=True); time.sleep(60)"
        worker = perceive.DetectorWorker([sys.executable, "-B", "-c", program],
                                         self.scene.path / "blocked-worker.log", frame_timeout_s=4)
        self.addCleanup(worker.close)
        p = self.p
        self.confirmed()
        p.detector = worker
        baseline = p.snapshot.frame.capture_mono
        box = source.Mailbox()
        events = queue.Queue()
        anchor = time.monotonic()
        clock = lambda: baseline + time.monotonic() - anchor

        class Source:
            def state(self):
                return {"status": "ok"}

        # A message larger than a pipe buffer cannot drain while the child is idle.
        bundle, _ = self.scene.sample(103.4, 17)
        original = worker.submit

        def submit(item, delay, ref):
            item = source.Bundle(item.data + b"0" * (2 * 1024 * 1024), item.session, item.rgb_seq,
                                 item.t_host, item.arrival_unix, item.arrival_mono)
            return original(item, delay, ref)

        worker.submit = submit
        loop = perceive.Loop(Source(), box, p, events.put, clock=clock)
        box.put(bundle)
        loop.start()
        try:
            event = events.get(timeout=3)
            self.assertEqual(event["perception_status"], "stale")
            self.assertTrue(loop.is_alive())
        finally:
            loop.halt.set()
            loop.join(2)


if __name__ == "__main__":
    unittest.main()
