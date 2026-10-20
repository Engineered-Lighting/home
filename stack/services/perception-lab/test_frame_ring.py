"""The always-on perceiver's RAM frame ring (speed plan S1.3): bounds, RAM only,
PreviewIngestor's acceptance rules, the shared window rule, camera chat first
on the process-local capture lock, and no capture while the gate is closed.
Scripted captures and gates; no camera, network or disk."""
import base64
import hashlib
import json
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import chrony_preview_client
import frame_ring as ring_module
import qwen_live
from frame_ring import CaptureTurns, FrameRing, RingCapture, preview_frame
from test_qwen_live import Clock, jpeg

ACCEPT = lambda data, header: None  # the shared camera contract, stood in for (tested separately below)


def causal_header(data, observed, *, sequence=1, session="cap-1", uncertainty_extra=0.0, schema=3, **edit):
    """A schema-3 causal receipt for data, observed (unix seconds), that the
    shared contract (home_shared.protocol._causal_frame) accepts as is."""
    send, receive, quantization, exposure, host_error = 10_000_000_000, 10_020_000_000, 1000, .01, .002
    uncertainty = (receive - send) / 2e9 + exposure + quantization / 1e9 + host_error + uncertainty_extra
    value = {"schema": schema, "session": session, "sequence": sequence, "timing_generation": 0, "width": 640,
             "height": 360, "quality": 85, "observed_monotonic_seconds": (send + receive) / 2e9,
             "observed_at_unix_ns": int(round(observed * 1e9)), "uncertainty_seconds": uncertainty,
             "validation_id": "clock-1", "exposure_duration_seconds": exposure,
             "source_clock": "depthai_device_monotonic", "monotonic_clock_scope": "python_monotonic_on_camera_host",
             "timestamp_semantics": "causal_exact_frame_exposure_end", "challenge_nonce": "0" * 32,
             "challenge_device_ns": 1_000_000, "frame_device_ns": 2_000_000, "host_send_monotonic_ns": send,
             "host_receive_monotonic_ns": receive, "timestamp_quantization_ns": quantization,
             "preview_sha256": "1" * 64, "jpeg_sha256": hashlib.sha256(data).hexdigest(),
             "host_boot_id": "00000000-0000-0000-0000-000000000000", "host_clock_validation_id": "host-clock-1",
             "host_clock_uncertainty_seconds": host_error, **edit}
    return base64.urlsafe_b64encode(json.dumps(value).encode()).decode()


def distinct_jpeg(index):
    """A 640x360 JPEG whose bytes differ for every index (a moving block)."""
    import io
    from PIL import Image
    image = Image.new("RGB", (640, 360), (40, 50, 60))
    image.paste((250, 250, 250), (16 * (index % 38), 16 * (index // 38 % 21), 16 * (index % 38) + 16,
                                  16 * (index // 38 % 21) + 16))
    encoded = io.BytesIO()
    image.save(encoded, format="JPEG")
    return encoded.getvalue()


class Frames:
    """Distinct JPEGs with matching causal headers, observed at given times."""
    def __init__(self):
        self.sequence = 0

    def __call__(self, observed, **edit):
        self.sequence += 1
        data = distinct_jpeg(self.sequence)
        return data, causal_header(data, observed, sequence=self.sequence, **edit)


class Gate:
    """An occupancy reader stand-in: state() and edge subscribers."""
    def __init__(self, state="occupied"):
        self.state, self.subscribers = state, set()

    def __call__(self):
        return {"state": self.state, "reason": "fixture", "observed_at": None}

    def subscribe(self, event):
        self.subscribers.add(event)

    def unsubscribe(self, event):
        self.subscribers.discard(event)

    def edge(self, state):
        self.state = state
        for event in list(self.subscribers):
            event.set()


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.frames = Frames()

    def test_a_commissioned_causal_preview_is_kept_with_its_bytes_digest_and_times(self):
        data, header = self.frames(999.5)
        frame = preview_frame(data, header, 1000.0, contract=ACCEPT)
        sha = hashlib.sha256(data).hexdigest()
        self.assertEqual({k: frame[k] for k in ("media_id", "frame_id", "capture_epoch", "observed_at", "available_at",
                                                 "size", "sha256")},
                         {"media_id": "ring-" + sha, "frame_id": "cap-1-0-1", "capture_epoch": "cap-1",
                          "observed_at": 999.5, "available_at": 1000.0, "size": len(data), "sha256": sha})
        self.assertIs(frame["data"], data)
        # Complete schema-3 bound: the causal bracket, exposure, quantization
        # and SAME host-clock error are each charged once by the original.
        source = json.loads(base64.urlsafe_b64decode(header))
        total = ((source["host_receive_monotonic_ns"] - source["host_send_monotonic_ns"]) / 2e9
                 + source["exposure_duration_seconds"] + source["timestamp_quantization_ns"] / 1e9
                 + source["host_clock_uncertainty_seconds"])
        self.assertAlmostEqual(source["uncertainty_seconds"], total)
        self.assertAlmostEqual(frame["valid_until"], 999.5 + 3 - total)

    def test_future_stale_uncertain_non_causal_and_wrong_size_previews_are_refused(self):
        cases = [self.frames(1000.5),                       # from the future
                 self.frames(997.0),                        # older than 3 s with its uncertainty
                 self.frames(999.5, uncertainty_extra=.99),  # uncertainty over 1 s
                 self.frames(999.5, schema=2),              # not the live causal client
                 self.frames(999.5, session="bad epoch")]   # not an identifier
        small = jpeg(size=(320, 180))
        cases.append((small, causal_header(small, 999.5)))
        cases.append((b"not a jpeg", causal_header(b"not a jpeg", 999.5)))
        for data, header in cases:
            with self.assertRaises(ValueError):
                preview_frame(data, header, 1000.0, contract=ACCEPT)
        with self.assertRaises(ValueError):
            preview_frame(*self.frames(999.5), 1000.0, contract=lambda data, header: (_ for _ in ()).throw(ValueError()))

    def test_the_shared_camera_contract_accepts_the_fixture_receipt(self):
        try:
            from home_shared.protocol import camera_frame  # noqa: F401  (tools/shared-home on PYTHONPATH)
        except ImportError:
            self.skipTest("home_shared not on this path (run with PYTHONPATH=../../../tools/shared-home)")
        data, header = self.frames(999.5)
        self.assertEqual(preview_frame(data, header, 1000.0)["observed_at"], 999.5)
        forged = data[:-2] + b"\x00\xd9"
        with self.assertRaises(ValueError):
            preview_frame(forged, header, 1000.0)  # the JPEG hash binding


class RingTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock(1000.0)
        self.frames = Frames()

    def offer_every_half_second(self, ring, seconds):
        for _ in range(int(seconds * 2)):
            self.clock.now += .5
            ring.offer(*self.frames(self.clock.now - .3))

    def test_the_ring_keeps_at_most_ten_seconds_and_a_bounded_count(self):
        ring = FrameRing(clock=self.clock, contract=ACCEPT)
        self.offer_every_half_second(ring, 30)
        snap = ring.snapshot()
        self.assertLessEqual(snap["frames"], ring_module.MAX_FRAMES)
        oldest = min(f["observed_at"] for f in ring.frames)
        self.assertGreaterEqual(oldest, self.clock.now - ring_module.RING_SECONDS)
        self.assertEqual(snap["frames"], 20)  # 10 s at 2 Hz
        small = FrameRing(clock=self.clock, contract=ACCEPT, max_frames=6)
        self.offer_every_half_second(small, 10)
        self.assertEqual(small.snapshot()["frames"], 6)
        self.assertEqual([f["observed_at"] for f in small.frames], sorted(f["observed_at"] for f in small.frames))
        # With nothing new, frames age out entirely.
        self.clock.now += ring_module.RING_SECONDS + 1
        ring.prune()
        self.assertEqual(ring.snapshot()["frames"], 0)
        with self.assertRaises(ValueError):
            FrameRing(seconds=30)

    def test_frames_stay_in_memory_only(self):
        ring = FrameRing(clock=self.clock, contract=ACCEPT)
        gate, fetches = Gate(), []

        def fetch():
            fetches.append(1)
            return self.frames(self.clock.now - .2)
        capture = RingCapture(ring, fetch, gate, turns=CaptureTurns(threading.Lock()))
        forbidden = AssertionError("the ring touched the file system")
        with patch("builtins.open", side_effect=forbidden), patch("os.open", side_effect=forbidden), \
                patch("os.fdopen", side_effect=forbidden), patch("os.replace", side_effect=forbidden):
            self.offer_every_half_second(ring, 4)
            capture.turns.enter()
            capture._capture()
            capture.turns.leave()
            selected = ring.select({"id": "x"}, self.clock.now)
            ring.snapshot()
        self.assertEqual(len(fetches), 1)
        self.assertEqual(capture.snapshot()["counts"], {"captured": 1})
        self.assertTrue(all(isinstance(f["data"], bytes) for f in selected))
        self.assertFalse(any(hasattr(ring_module, name) for name in ("Path", "tempfile", "shutil")))

    def test_select_uses_the_recording_window_rule_and_only_available_frames(self):
        ring = FrameRing(clock=self.clock, contract=ACCEPT)
        with self.assertRaises(qwen_live.FramesUnavailable) as caught:
            ring.select({}, self.clock.now)
        self.assertEqual(caught.exception.reason, "frames_unavailable")
        self.offer_every_half_second(ring, 5)
        now = self.clock.now
        chosen = ring.select({}, now, count=3, span=3.0)
        expected = qwen_live.spread(ring.candidates(now), now, count=3, span=3.0)
        self.assertEqual([f["media_id"] for f in chosen], [f["media_id"] for f in expected])
        self.assertEqual(len(chosen), 3)
        self.assertAlmostEqual(chosen[-1]["observed_at"] - chosen[0]["observed_at"], 3.0)
        self.assertEqual([c["data"] for c in chosen], [next(f["data"] for f in ring.frames if f["media_id"] == c["media_id"])
                                                       for c in chosen])
        # A frame is not usable before the ring received it (causal).
        self.assertEqual(ring.candidates(now - 1)[-1]["available_at"] <= now - 1, True)
        self.clock.now += 3.5
        with self.assertRaises(qwen_live.FramesUnavailable) as caught:
            ring.select({}, self.clock.now)
        self.assertEqual(caught.exception.reason, "frames_stale")

    def test_duplicates_are_dropped_and_each_new_frame_wakes_subscribers(self):
        ring = FrameRing(clock=self.clock, contract=ACCEPT)
        event = threading.Event()
        ring.subscribe(event)
        data, header = self.frames(999.8)
        self.assertTrue(ring.offer(data, header))
        self.assertTrue(event.is_set())
        event.clear()
        self.assertFalse(ring.offer(data, header))
        self.assertFalse(event.is_set())
        with self.assertRaises(ValueError):
            ring.offer(b"x", "e30")
        self.assertEqual(ring.snapshot()["counts"], {"accepted": 1, "duplicate": 1, "rejected": 1, "pruned": 0})
        ring.unsubscribe(event)
        ring.offer(*self.frames(999.9))
        self.assertFalse(event.is_set())


class CaptureTurnsTests(unittest.TestCase):
    def test_other_callers_keep_the_lock_semantics(self):
        turns = CaptureTurns(threading.Lock())
        self.assertTrue(turns.acquire(timeout=1))
        self.assertTrue(turns.locked())
        started = time.monotonic()
        got = []
        worker = threading.Thread(target=lambda: got.append(turns.acquire(timeout=.2)))
        worker.start()
        worker.join()
        self.assertEqual(got, [False])
        self.assertGreaterEqual(time.monotonic() - started, .15)
        turns.release()
        self.assertFalse(turns.locked())
        with turns:
            self.assertTrue(turns.locked())
        self.assertEqual(turns.waiting, 0)

    def test_the_ring_never_waits_and_yields_to_a_holder_a_waiter_or_a_capture_just_ended(self):
        mono = Clock(100.0)
        turns = CaptureTurns(threading.Lock(), monotonic=mono)
        results = {}

        def ring_try(label):
            turns.enter()
            try:
                started = time.monotonic()
                results[label] = turns.acquire(timeout=1)
                results[label + "_s"] = time.monotonic() - started
                if results[label]:
                    turns.release()
            finally:
                turns.leave()

        def in_ring_thread(label):
            thread = threading.Thread(target=ring_try, args=(label,))
            thread.start()
            thread.join()
        in_ring_thread("idle")
        self.assertTrue(results["idle"])
        self.assertIsNone(turns.other_ended)  # the ring's own captures are not "other"
        turns.acquire(timeout=1)  # camera chat captures
        in_ring_thread("held")
        self.assertFalse(results["held"])
        self.assertLess(results["held_s"], .1)  # refused at once, never queued
        # Camera chat waiting for the lock (here: behind a recording's capture).
        waiter = threading.Thread(target=lambda: (turns.acquire(timeout=2), turns.release()))
        turns.release()
        turns.acquire(timeout=1)
        waiter.start()
        deadline = time.monotonic() + 1
        while turns.waiting == 0 and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertEqual(turns.waiting, 1)
        self.assertTrue(turns.other_busy())
        turns.release()
        waiter.join()
        in_ring_thread("just_after")
        self.assertFalse(results["just_after"])  # the chat capture ended 0 s ago
        mono.now += .49
        in_ring_thread("within")
        self.assertFalse(results["within"])
        mono.now += .02
        in_ring_thread("after")
        self.assertTrue(results["after"])
        self.assertEqual(turns.refused, 3)

    def test_installed_turns_keep_the_commissioned_client_working_and_make_the_ring_yield(self):
        fake = SimpleNamespace(_CAPTURE_LOCK=threading.Lock())
        turns = ring_module.install_turns(fake)
        self.assertIs(ring_module.install_turns(fake), turns)  # once per process
        self.assertIsInstance(fake._CAPTURE_LOCK, CaptureTurns)
        config = SimpleNamespace(ntp=SimpleNamespace(server_ip='192.0.2.1', local_clock_quantization_ns=1000,
                                                     drift_slew_ppm=1000, timeout_seconds=.5, valid_for_seconds=1,
                                                     max_uncertainty_seconds=1))

        def accept(value, token, *, transport, publisher_factory):
            publisher_factory(value)
            transport('/frame.jpg', token)
            return {'status': 'accepted'}
        pilot = SimpleNamespace(CONFIG='fixed', load_config=lambda _: config, read_token=lambda: 'token', accept_once=accept,
                                fetch=lambda path, token: ({'x-victoria-frame-metadata': 'header'}, b'jpeg'))
        runtime = (pilot, lambda *args: args, lambda *args: None)
        with patch.object(chrony_preview_client, "_CAPTURE_LOCK", turns), \
                patch("chrony_preview_client.commissioned_runtime", return_value=runtime):
            client = chrony_preview_client.ChronyPreviewClient()
            self.assertEqual(client(), (b'jpeg', 'header'))  # camera chat, unchanged
            outcome = {}

            def ring_capture():
                turns.enter()
                try:
                    started = time.monotonic()
                    try:
                        client()
                    except ValueError as error:
                        outcome["error"] = str(error)
                    outcome["seconds"] = time.monotonic() - started
                finally:
                    turns.leave()
            thread = threading.Thread(target=ring_capture)
            thread.start()
            thread.join()
        # Chat captured a moment ago: the ring's capture is refused at once
        # (the client's 1 s wait does not apply to it) and nothing is fetched.
        self.assertEqual(outcome["error"], ring_module.BUSY)
        self.assertLess(outcome["seconds"], .5)


class ChatDuringRingCaptureTests(unittest.TestCase):
    """Camera chat arriving while a ring capture holds the commissioned lock,
    and the camera answers that capture with 503s (each attempt about 0.4 s)."""

    def chat_during_ring_capture(self, ring_client):
        turns = CaptureTurns(threading.Lock())
        ring_threads, ring_started, fetches = set(), threading.Event(), []
        config = SimpleNamespace(ntp=SimpleNamespace(server_ip='192.0.2.1', local_clock_quantization_ns=1000,
                                                     drift_slew_ppm=1000, timeout_seconds=.5, valid_for_seconds=1,
                                                     max_uncertainty_seconds=1))

        def fetch(path, token):
            if threading.get_ident() in ring_threads:
                fetches.append("ring")
                ring_started.set()
                time.sleep(.4)
                raise ValueError('camera response rejected')  # the camera's 503: a retryable rejection
            fetches.append("chat")
            return {'x-victoria-frame-metadata': 'header'}, b'jpeg'

        def accept(value, token, *, transport, publisher_factory):
            publisher_factory(value)
            transport('/frame.jpg', token)
            return {'status': 'accepted'}
        pilot = SimpleNamespace(CONFIG='fixed', load_config=lambda _: config, read_token=lambda: 'token',
                                accept_once=accept, fetch=fetch)
        ring_outcome = {}

        def ring():
            ring_threads.add(threading.get_ident())
            turns.enter()
            try:
                ring_client()
            except ValueError as error:
                ring_outcome["error"] = str(error)
            finally:
                turns.leave()
        with patch.object(chrony_preview_client, "_CAPTURE_LOCK", turns), \
                patch("chrony_preview_client.commissioned_runtime",
                      return_value=(pilot, lambda *args: args, lambda *args: None)):
            thread = threading.Thread(target=ring)
            thread.start()
            self.assertTrue(ring_started.wait(2))
            time.sleep(.05)
            started = time.monotonic()
            try:
                chat = chrony_preview_client.ChronyPreviewClient()()  # camera chat, unchanged
            except ValueError as error:
                chat = str(error)
            waited = time.monotonic() - started
            thread.join(5)
        return chat, waited, ring_outcome, fetches

    def test_chat_waits_for_one_ring_attempt_only_and_succeeds(self):
        chat, waited, ring, fetches = self.chat_during_ring_capture(ring_module.RingPreviewClient())
        self.assertEqual(chat, (b'jpeg', 'header'))
        self.assertLess(waited, .6)  # the rest of one 0.4 s attempt, well inside chat's 1 s turn
        self.assertEqual(ring["error"], 'Fresh preview temporarily unavailable')  # the slot is given up
        self.assertEqual(fetches, ["ring", "chat"])  # one ring attempt, never retried in the lock

    def test_the_commissioned_retry_loop_would_have_failed_chat(self):
        # Why the ring has its own client: inside the lock the commissioned
        # client retries for up to 2 s, longer than chat's 1 s wait.
        chat, waited, _ring, fetches = self.chat_during_ring_capture(chrony_preview_client.ChronyPreviewClient())
        self.assertEqual(chat, ring_module.BUSY)
        self.assertGreaterEqual(waited, .95)
        self.assertNotIn("chat", fetches)

    def test_the_ring_client_never_waits_for_the_lock(self):
        turns = CaptureTurns(threading.Lock())
        turns.acquire(timeout=1)  # another capture in progress
        try:
            with patch.object(chrony_preview_client, "_CAPTURE_LOCK", turns):
                started = time.monotonic()
                with self.assertRaisesRegex(ValueError, ring_module.BUSY):
                    ring_module.RingPreviewClient()()
                self.assertLess(time.monotonic() - started, .1)
        finally:
            turns.release()


class RingCaptureTests(unittest.TestCase):
    def setUp(self):
        self.frames = Frames()
        self.ring = FrameRing(contract=ACCEPT)
        self.fetched = []

    def fetch(self):
        self.fetched.append(time.monotonic())
        return self.frames(time.time() - .05)

    def capture(self, gate, **kwargs):
        capture = RingCapture(self.ring, self.fetch, gate, turns=CaptureTurns(threading.Lock()), **kwargs)
        self.addCleanup(capture.stop, 2)
        return capture

    def wait_for(self, predicate, timeout=2.0):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            time.sleep(.01)
        return predicate()

    def test_no_capture_while_the_gate_is_closed_and_an_edge_captures_at_once(self):
        gate = Gate("vacant")
        capture = self.capture(gate)
        capture.start()
        time.sleep(.6)
        self.assertEqual(self.fetched, [])
        self.assertEqual(capture.status, "closed")
        gate.state = "unknown"
        time.sleep(.3)
        self.assertEqual(self.fetched, [])
        edge = time.monotonic()
        gate.edge("occupied")
        self.assertTrue(self.wait_for(lambda: self.fetched))
        self.assertLess(self.fetched[0] - edge, .3)  # the on-edge wakes the capture, no 1 s wait
        gate.edge("vacant")
        time.sleep(.2)
        count = len(self.fetched)
        time.sleep(.7)
        self.assertEqual(len(self.fetched), count)

    def test_at_most_two_captures_a_second(self):
        capture = self.capture(Gate())
        capture.start()
        time.sleep(1.6)
        capture.stop(2)
        self.assertIn(len(self.fetched), (3, 4))
        gaps = [b - a for a, b in zip(self.fetched, self.fetched[1:])]
        self.assertTrue(all(gap >= .49 for gap in gaps), gaps)
        self.assertGreaterEqual(self.ring.snapshot()["counts"]["accepted"], 3)

    def test_each_capture_leaves_the_lock_idle_for_an_interval(self):
        spans = []

        def slow():
            started = time.monotonic()
            time.sleep(.3)  # a capture of about 0.3 s, as on the Jetson
            spans.append((started, time.monotonic()))
            return self.frames(time.time() - .05)
        capture = RingCapture(self.ring, slow, Gate(), turns=CaptureTurns(threading.Lock()))
        self.addCleanup(capture.stop, 2)
        capture.start()
        time.sleep(2.2)
        capture.stop(2)
        self.assertGreaterEqual(len(spans), 2)
        idle = [b[0] - a[1] for a, b in zip(spans, spans[1:])]
        self.assertTrue(all(gap >= .49 for gap in idle), idle)  # measured from the end of the capture
        busy = [(a[1] - a[0]) / (b[0] - a[0]) for a, b in zip(spans, spans[1:])]
        self.assertTrue(all(share < .5 for share in busy), busy)  # the lock is held under half of each cycle

    def test_poke_looks_again_at_once(self):
        wanted = {"on": False}
        capture = self.capture(Gate(), enabled=lambda: wanted["on"])
        capture.start()
        time.sleep(.3)
        self.assertEqual(self.fetched, [])
        wanted["on"] = True
        poked = time.monotonic()
        capture.poke()
        self.assertTrue(self.wait_for(lambda: self.fetched, 1))
        self.assertLess(self.fetched[0] - poked, .2)  # not the closed loop's one-second check

    def test_the_kill_switch_off_captures_nothing(self):
        switch = {"on": False}
        capture = self.capture(Gate(), enabled=lambda: switch["on"])
        capture.start()
        time.sleep(.6)
        self.assertEqual(self.fetched, [])
        self.assertFalse(capture.is_open())
        switch["on"] = True
        self.assertTrue(self.wait_for(lambda: self.fetched, 2))

    def test_camera_chat_comes_first(self):
        mono = time.monotonic
        turns = CaptureTurns(threading.Lock())
        capture = RingCapture(self.ring, self.fetch, Gate(), turns=turns)
        self.addCleanup(capture.stop, 2)
        turns.acquire(timeout=1)  # a camera-chat capture in progress
        capture.start()
        time.sleep(.7)
        self.assertEqual(self.fetched, [])
        self.assertGreaterEqual(capture.snapshot()["counts"]["yield_capture_busy"], 1)
        ended = mono()
        turns.release()
        self.assertTrue(self.wait_for(lambda: self.fetched, 2))
        self.assertGreaterEqual(self.fetched[0] - ended, ring_module.YIELD_SECONDS - .02)  # never within 0.5 s of chat

    def test_bounds(self):
        for interval in (.4, 2.5):
            with self.assertRaises(ValueError):
                RingCapture(self.ring, self.fetch, Gate(), interval=interval)
        with self.assertRaises(ValueError):
            RingCapture(self.ring, self.fetch, None)


if __name__ == "__main__":
    unittest.main()
