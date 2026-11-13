"""Evidence regressions on real observe/tracker pins and generated RGB/depth."""
import queue
import sys
import threading
import time
import unittest

import fixture_rgb
import perceive
import source


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
        after = next(t for t in self.p.snapshot.tracks if t.track_id == track.track_id)
        self.assertGreater(after.association_revision, track.association_revision)
        self.assertEqual(after.association_status, "ambiguous")
        self.assertIsNone(self.p.body_input)
        self.advance(103.6, 18, luma=10)
        self.assertEqual((self.p.snapshot.status, self.p.snapshot.presence), ("degraded", "unknown"))

    def test_brief_gap_retains_id_and_retired_session_cannot_return(self):
        track = self.confirmed()
        self.advance(103.6, 18)
        self.assertEqual(self.p.snapshot.tracks[0].track_id, track.track_id)
        self.advance(103.8, 0, session="oak-2")
        self.assertNotEqual(self.p.snapshot.camera_epoch, 1)
        old, _ = self.scene.sample(104.0, 19)
        self.assertIsNone(self.p.prepare(old, 104.0))
        self.assertNotEqual(self.scene.perceiver().run_id, self.p.run_id)

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
