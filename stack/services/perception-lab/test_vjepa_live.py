"""Victoria V-JEPA pump: clip frames, detector person boxes, receipts ledger, transport and
the recording-owned pump. Temp store, scripted transports; no network or model."""
import http.client
import json
import socket
import time
import unittest

import vjepa_live as live
import vjepa_scene as scene
from qwen_live import FramesUnavailable
from test_qwen_live import OCCUPIED, Clock, StoreFixture
from test_vjepa_scene import PROFILE, strings_and_numbers


class Fixture(StoreFixture):
    def build(self, now=1000.0):
        self.build_store(now)
        self.media = {}

    def frame(self, observed, **kw):
        item = super().frame(observed, **kw)
        self.media[observed] = item
        return item

    def detect(self, observed, boxes, *, arm="a", status="completed", available=None):
        """A recorded Lab detector perception.output for the preview frame at observed."""
        item = self.media[observed]
        meta = item["metadata"]
        result = {"schema_version": 1, "frame_id": meta["frame_id"], "coordinate_space": "source_pixels", "objects": [],
                  "boxes": [{"x": x, "y": y, "w": w, "h": h, "label": label, "confidence": c,
                             "instance_id": "%s:%d" % (meta["frame_id"], i)} for i, (label, x, y, w, h, c) in enumerate(boxes)],
                  "quality": {}}
        value = {"request_id": "q", "arm": arm, "state": status, "status": status, "generation": 1, "mode": "live",
                 "observed_at": observed, "frame_id": meta["frame_id"], "capture_epoch": meta["capture_epoch"],
                 "media_ids": [item["id"]], "site_id": "victoria", "camera_id": "den",
                 "available_at": observed + .4 if available is None else available, "valid_until": observed + 2.5,
                 "result": result}
        return self.store.append_event(self.session["id"], "perception.output", value)

    def person(self, observed, *, x=200, confidence=.9, arm="a", extra=(), available=None):
        return self.detect(observed, [("person", x, 40, 180, 300, confidence), ("book", 250, 200, 40, 30, .8), *extra], arm=arm,
                           available=available)

    def scene_frames(self, start, end, *, people=True, **kw):
        t = start
        while t <= end + 1e-9:
            self.frame(round(t, 3))
            if people:
                self.person(round(t, 3), **kw)
            t += .5


def result_for(body, **changes):
    value = {"version": 1, "schema": scene.RESULT_SCHEMA, "request_id": body["request_id"],
             "engine_epoch": PROFILE["engine_epoch"], "artifact_sha256": PROFILE["artifact_sha256"],
             "posture": {"sitting": .9, "standing": .05, "walking": .02, "lying_down": .01, "bending": .01, "exercising_pose": .01},
             "reading": .66, "zero_shot": {"phone_use": .03, "computer_work": .05, "reading": .7} if body["single_person"] else None,
             "zero_shot_status": "supplied" if body["single_person"] else "single_person_gate_not_met",
             "motion": .03, "scene_drift": .02 if body["previous_request_id"] else None,
             "drift_reference": body["previous_request_id"],
             "timings_ms": {"decode": 10.0, "crop": 3.0, "encode": 35.0, "heads": 5.0, "total": 55.0}}
    value.update(changes)
    return value


class FakeTransport:
    timeout = 4.0

    def __init__(self, *outcomes):
        self.outcomes, self.bodies, self.closed = list(outcomes), [], False

    def __call__(self, body):
        self.bodies.append(body)
        outcome = self.outcomes.pop(0) if self.outcomes else (lambda b: result_for(b))
        if isinstance(outcome, Exception):
            raise outcome
        value = outcome(body) if callable(outcome) else outcome
        return value, "d" * 64

    def close(self):
        self.closed = True


class ClipFrameTests(Fixture, unittest.TestCase):
    def setUp(self):
        self.build()
        self.source = live.ClipFrames(self.store)

    def tearDown(self):
        self.close_store()

    def test_selects_eight_frames_on_the_half_second_grid_oldest_first(self):
        self.scene_frames(990.0, 999.5, people=False)
        self.frame(999.8, camera="other")
        frames = self.source.select_clip(self.session, 1000.0)
        self.assertEqual([f["observed_at"] for f in frames], [996.0 + .5 * i for i in range(8)])
        self.assertTrue(all(f["data"][:2] == b"\xff\xd8" for f in frames))

    def test_jittered_cadence_is_accepted_and_gaps_are_typed(self):
        for t in (996.04, 996.47, 997.02, 997.55, 997.98, 998.52, 999.03, 999.5):
            self.frame(t)
        self.assertEqual(len(self.source.select_clip(self.session, 1000.0)), 8)
        self.close_store()
        self.build()
        self.source = live.ClipFrames(self.store)
        for t in (996.0, 996.5, 997.0, 998.5, 999.0, 999.5):  # a 1.5 s hole no choice of frames can bridge
            self.frame(t)
        with self.assertRaises(FramesUnavailable) as caught:
            self.source.select_clip(self.session, 1000.0)
        self.assertEqual(caught.exception.reason, "frames_gap")
        self.close_store()
        self.build()
        self.source = live.ClipFrames(self.store)
        for t in (997.5, 998.0, 998.5, 999.0, 999.5):  # even, but under 3 s of history
            self.frame(t)
        with self.assertRaises(FramesUnavailable) as caught:
            self.source.select_clip(self.session, 1000.0)
        self.assertEqual(caught.exception.reason, "frames_insufficient")

    # Uneven stored cadence: slot 997.0 has no frame within 0.25 s (the old
    # strict grid), only 997.3; 998.35 is a spare frame the clip skips.
    UNEVEN = (996.05, 996.6, 997.3, 997.75, 998.1, 998.35, 998.5, 999.0, 999.5)

    def test_uneven_spacing_takes_the_nearest_frame_per_slot_within_the_tolerance(self):
        for t in self.UNEVEN:
            self.frame(t)
        frames = self.source.select_clip(self.session, 1000.0)
        times = [f["observed_at"] for f in frames]
        self.assertEqual(times, [996.05, 996.6, 997.3, 997.75, 998.1, 998.5, 999.0, 999.5])
        self.assertEqual(scene.check_times(times), times)
        clip = live.clip_input(frames, .4)
        self.assertEqual(clip, {"rule": live.CLIP_RULE, "slot_tolerance_seconds": .4,
                                "relative_times_seconds": [-3.45, -2.9, -2.2, -1.75, -1.4, -1.0, -.5, 0.0],
                                "grid_offsets_seconds": [.05, .1, .3, .25, .1, 0.0, 0.0, 0.0],
                                "max_grid_offset_seconds": .3, "span_seconds": 3.45, "max_gap_seconds": .7,
                                "min_gap_seconds": .35})
        strict = live.ClipFrames(self.store, slot_tolerance=.25)  # the old grid rule
        with self.assertRaises(FramesUnavailable) as caught:
            strict.select_clip(self.session, 1000.0)
        self.assertEqual(caught.exception.reason, "frames_insufficient")

    def test_selection_is_the_closest_valid_clip_and_never_breaks_the_wire_contract(self):
        """Against brute force over every choice of seven earlier frames: the
        search finds a clip exactly when one exists within the tolerance, it
        is the one nearest the grid, and it always passes scene.check_times."""
        import itertools
        import random
        rng = random.Random(7)
        found = 0
        for _ in range(300):
            times, t = [], 999.5
            while t > 993.0:
                times.append(round(t, 2))
                t -= rng.choice((.2, .3, .45, .5, .5, .5, .5, .55, .7, .8, 1.0))
            latest, earlier = times[0], sorted(times[1:])
            frame = lambda t: {"media_id": "m%07d" % round(t * 100), "observed_at": t}
            got = self.source._search(frame(latest), [frame(t) for t in earlier])
            best = None
            for combo in itertools.combinations([t for t in earlier if t >= latest - 4.5], 7):
                clip = list(combo) + [latest]
                offsets = [abs(c - (latest - .5 * (7 - i))) for i, c in enumerate(clip)]
                try:
                    scene.check_times(clip)
                except ValueError:
                    continue
                if max(offsets) <= .4 + 1e-9 and (best is None or sum(offsets) < best[0] - 1e-9):
                    best = (sum(offsets), clip)
            self.assertEqual(got is None, best is None, times)
            if got is not None:
                found += 1
                chosen = [f["observed_at"] for f in got]
                scene.check_times(chosen)
                self.assertAlmostEqual(sum(abs(c - (latest - .5 * (7 - i))) for i, c in enumerate(chosen)), best[0])
        self.assertGreater(found, 50)

    def test_slot_tolerance_is_bounded(self):
        for bad in (.2, .45, float("nan"), True, "0.4", None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                live.ClipFrames(self.store, slot_tolerance=bad)
            with self.subTest(config=bad), self.assertRaises(ValueError):
                live.pump_config({"clip_slot_tolerance_seconds": bad})
        self.assertEqual(live.pump_config()["clip_slot_tolerance_seconds"], .4)
        self.assertEqual(live.pump_config({"clip_slot_tolerance_seconds": .25})["clip_slot_tolerance_seconds"], .25)

    def test_future_stale_and_other_epoch_frames_are_not_used(self):
        self.scene_frames(990.0, 995.5, people=False)
        self.frame(1001.0)
        with self.assertRaises(FramesUnavailable) as caught:
            self.source.select_clip(self.session, 1000.0)
        self.assertEqual(caught.exception.reason, "frames_stale")
        self.close_store()
        self.build()
        self.source = live.ClipFrames(self.store)
        for t in (996.0, 996.5, 997.0, 997.5):
            self.frame(t, epoch="cap-0")
        for t in (998.0, 998.5, 999.0, 999.5):
            self.frame(t)
        with self.assertRaises(FramesUnavailable) as caught:
            self.source.select_clip(self.session, 1000.0)
        self.assertEqual(caught.exception.reason, "frames_insufficient")

    def test_changed_bytes_fail_the_integrity_check(self):
        self.scene_frames(996.0, 999.5, people=False)
        latest = self.source.select_clip(self.session, 1000.0)[-1]
        self.store.media_path(self.session["id"], latest["media_id"]).write_bytes(b"\xff\xd8changed")
        with self.assertRaises(FramesUnavailable) as caught:
            self.source.select_clip(self.session, 1000.0)
        self.assertEqual(caught.exception.reason, "frame_integrity_failed")


class PersonBoxTests(Fixture, unittest.TestCase):
    def setUp(self):
        self.build()
        self.boxes = live.PersonBoxes(self.store)

    def tearDown(self):
        self.close_store()

    def clip(self):
        return live.ClipFrames(self.store).select_clip(self.session, 1000.0)

    def test_union_of_both_arms_person_boxes_on_the_clip_frames(self):
        self.scene_frames(996.0, 999.5, x=200)
        self.person(999.0, x=230, arm="b", confidence=.8)
        self.detect(999.5, [("person", 150, 40, 180, 300, .95)], arm="b")
        box = self.boxes.union(self.session, self.clip(), 1000.0)
        self.assertEqual(box["box"], [150.0, 40.0, 410.0, 340.0])
        self.assertEqual((box["frames"], box["single_person"]), (8, True))
        self.assertEqual(len(box["event_ids"]), 10)

    def test_ntu_gate_needs_every_box_confident(self):
        self.scene_frames(996.0, 999.5)
        self.person(998.0, confidence=.6, arm="b")
        self.assertFalse(self.boxes.union(self.session, self.clip(), 1000.0)["single_person"])

    def test_typed_skips(self):
        self.scene_frames(996.0, 999.5, people=False)
        with self.assertRaises(live.BoxUnavailable) as caught:
            self.boxes.union(self.session, self.clip(), 1000.0)
        self.assertEqual(caught.exception.reason, "detector_output_unavailable")
        for t in (996.0, 996.5):
            self.person(t)
        self.detect(999.5, [("book", 1, 1, 10, 10, .9)])
        with self.assertRaises(live.BoxUnavailable) as caught:
            # 996.0, 996.5 and (nearest, 0.5 s) 997.0: three boxed frames.
            self.boxes.union(self.session, self.clip(), 1000.0)
        self.assertEqual(caught.exception.reason, "person_box_sparse")
        self.person(998.0, extra=[("person", 20, 20, 60, 200, .7)])
        with self.assertRaises(live.BoxUnavailable) as caught:
            self.boxes.union(self.session, self.clip(), 1000.0)
        self.assertEqual(caught.exception.reason, "multiple_people")

    def test_future_rejected_and_low_confidence_outputs_are_ignored(self):
        self.scene_frames(996.0, 999.5, people=False)
        for t in (996.0, 996.5, 997.0, 997.5):
            self.person(t)
        self.detect(999.0, [("person", 0, 0, 600, 350, .9)], available=1000.5)  # not yet available
        self.detect(999.5, [("person", 0, 0, 600, 350, .9)], status="failed")
        self.detect(998.5, [("person", 0, 0, 600, 350, .3)])  # below the crop threshold
        exact = live.PersonBoxes(self.store, match_seconds=0)
        box = exact.union(self.session, self.clip(), 1000.0)
        self.assertEqual((box["box"], box["frames"]), ([200.0, 40.0, 380.0, 340.0], 4))
        self.assertEqual(box["match"], {"rule": live.BOX_MATCH_RULE, "tolerance_seconds": 0, "max_offset_seconds": 0.0})
        # Nearest matching: 998.0 takes 997.5 (tied with the unboxed 998.5,
        # the earlier event wins); 999.0/999.5 only reach the unboxed 998.5.
        box = self.boxes.union(self.session, self.clip(), 1000.0)
        self.assertEqual((box["box"], box["frames"], box["match"]["max_offset_seconds"]),
                         ([200.0, 40.0, 380.0, 340.0], 5, .5))

    def test_one_hertz_lagging_detector_outputs_are_matched_to_the_nearest_frame(self):
        # The live den case: previews at 2 Hz, the detector at 1 Hz on every
        # other frame, its newest output not yet available (~0.6 s lag).
        self.scene_frames(996.0, 999.5, people=False)
        for t in (996.0, 997.0, 998.0):
            self.person(t, x=200 + int(t - 996) * 10)
        self.person(999.0, available=1000.6)  # not yet available at 1000.0
        with self.assertRaises(live.BoxUnavailable) as caught:
            live.PersonBoxes(self.store, match_seconds=0).union(self.session, self.clip(), 1000.0)
        self.assertEqual(caught.exception.reason, "person_box_sparse")  # 3 of 8 exact frames
        box = self.boxes.union(self.session, self.clip(), 1000.0)
        # 996.0-998.5 each reach an available output within 0.5 s; 999.0 and
        # 999.5 are 1.0 and 1.5 s from 998.0, beyond the 0.75 s tolerance.
        self.assertEqual((box["box"], box["frames"], box["single_person"]), ([200.0, 40.0, 400.0, 340.0], 6, True))
        self.assertEqual(box["match"], {"rule": live.BOX_MATCH_RULE, "tolerance_seconds": .75, "max_offset_seconds": .5})
        self.assertEqual(len(box["event_ids"]), 3)
        # A later available output is never used before it is available.
        later = self.boxes.union(self.session, self.clip(), 1000.6)
        self.assertEqual(later["frames"], 8)

    def test_a_matched_output_with_two_people_still_skips(self):
        self.scene_frames(996.0, 999.5, people=False)
        for t in (996.0, 997.0, 999.0):
            self.person(t)
        self.person(998.0, extra=[("person", 20, 20, 60, 200, .7)])
        with self.assertRaises(live.BoxUnavailable) as caught:
            self.boxes.union(self.session, self.clip(), 1000.0)
        self.assertEqual(caught.exception.reason, "multiple_people")

    def test_other_epoch_outputs_never_match(self):
        self.scene_frames(996.0, 999.5, people=False)
        for t in (996.0, 997.0, 998.0, 999.0):
            self.person(t)
        rows = self.store.db.execute("SELECT id,payload FROM events WHERE kind='perception.output'").fetchall()
        for row in rows:
            value = json.loads(row["payload"])
            value["capture_epoch"] = "cap-0"
            self.store.db.execute("UPDATE events SET payload=? WHERE id=?", (json.dumps(value), row["id"]))
        with self.assertRaises(live.BoxUnavailable) as caught:
            self.boxes.union(self.session, self.clip(), 1000.0)
        self.assertEqual(caught.exception.reason, "detector_output_unavailable")

    def test_match_tolerance_is_bounded(self):
        for bad in (-.1, .8, float("nan"), True, "0.5"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                live.PersonBoxes(self.store, match_seconds=bad)
            with self.subTest(config=bad), self.assertRaises(ValueError):
                live.pump_config({"box_match_seconds": bad})
        self.assertEqual(live.pump_config({"box_match_seconds": 0})["box_match_seconds"], 0)
        self.assertEqual(live.pump_config()["box_match_seconds"], .75)


class LedgerTests(Fixture, unittest.TestCase):
    def setUp(self):
        self.build()
        self.ledger = live.VjepaLedger(self.store)

    def tearDown(self):
        self.close_store()

    def draft(self, observed, *, reading=.66, request_id=None):
        frames = [{"media_id": "m%d" % i, "frame_id": "f%d" % i, "capture_epoch": "cap-1", "observed_at": observed - 3.5 + .5 * i}
                  for i in range(8)]
        request = {"request_id": request_id or scene.digest(observed), "profile": dict(PROFILE), "person_box": [1, 1, 50, 50],
                   "single_person": True, "previous_request_id": None}
        return scene.build_observation(site_id="victoria", camera_id="den", session_id=self.session["id"], request=request,
                                       frames=frames, box_input={"frames": 8, "event_ids": [1]}, generated_at=observed + .3,
                                       result=result_for({**request}, reading=reading), body_sha256="b" * 64,
                                       response_sha256="c" * 64)

    def test_first_receipt_is_durable_and_duplicates_do_not_refresh(self):
        sid = self.session["id"]
        first = self.ledger.record(sid, self.draft(999.5))
        self.assertEqual(first["state"], "available")
        self.clock.now = 1003.0
        self.assertEqual(self.ledger.record(sid, self.draft(999.5)), first)
        [saved] = self.events("vjepa.observation")
        self.assertEqual((saved["available_at"], saved["valid_until"]), (1000.0, 1005.5))
        conflict = self.ledger.record(sid, self.draft(999.5, reading=.1))
        self.assertEqual(conflict["state"], "identity_conflict")
        self.assertEqual(self.ledger.receipts(self.session, 1004.0), [])  # excluded once the conflict is known

    def test_out_of_order_and_expired_receipts_are_rejected_without_results(self):
        sid = self.session["id"]
        self.ledger.record(sid, self.draft(999.5))
        self.assertEqual(self.ledger.record(sid, self.draft(998.0))["state"], "out_of_order")
        self.clock.now = 1010.0
        self.assertEqual(self.ledger.record(sid, self.draft(1003.0))["state"], "expired_on_receipt")
        rejected = self.events("vjepa.rejected")
        self.assertEqual([r["reason"] for r in rejected], ["out_of_order", "expired_on_receipt"])
        self.assertTrue(all(r["result"] is None for r in rejected))

    def test_only_an_active_matching_recording_accepts_receipts(self):
        with self.assertRaises(ValueError):
            self.ledger.record(self.session["id"], {**self.draft(999.5), "camera_id": "kitchen"})
        self.store.transition(self.session["id"], "stopping")
        with self.assertRaises(ValueError):
            self.ledger.record(self.session["id"], self.draft(999.5))

    def test_context_is_causal_and_identical_for_both_arms(self):
        sid = self.session["id"]
        for t in range(960, 1000, 2):
            self.clock.now = t + .5
            self.ledger.record(sid, self.draft(float(t)))
        before = live.scene_context(self.store, self.session, 990.0, generation=2, arms={"a", "b"})
        self.clock.now = 1001.0
        self.ledger.record(sid, self.draft(1000.5, reading=.1))
        self.assertEqual(live.scene_context(self.store, self.session, 990.0, generation=2, arms={"a", "b"}), before)
        strip = lambda value: {k: v for k, v in value.items() if k != "arm"}
        self.assertEqual(strip(before["a"][0]), strip(before["b"][0]))
        projected = before["a"][0]["vjepa_scene"]
        self.assertEqual(projected["status"], "supplied")
        strings, numbers = [], []
        strings_and_numbers(projected, strings, numbers)
        self.assertEqual(numbers, [])
        self.assertEqual(list(live.scene_context(self.store, self.session, 990.0, generation=2, arms={"b"})), ["b"])


class TransportTests(unittest.TestCase):
    class Response:
        def __init__(self, status, body):
            self.status, self.body = status, body

        def read(self, limit):
            return self.body[:limit]

    class Connection:
        def __init__(self, reply, log):
            self.reply, self.log = reply, log

        def request(self, method, path, data, headers):
            self.log.append((method, path, json.loads(data), headers))

        def getresponse(self):
            if isinstance(self.reply, Exception):
                raise self.reply
            return TransportTests.Response(*self.reply)

        def close(self):
            pass

    def transport(self, reply, log, token="t" * 40):
        return live.VjepaTransport("https://192.168.0.100:9447", lambda: token, "/fixture/ca.pem",
                                   connection_factory=lambda: self.Connection(reply, log))

    def test_origin_is_the_fixed_private_supervisor(self):
        for origin in ("http://192.168.0.100:9447", "https://192.168.0.100:9443", "https://example.com:9447",
                       "https://192.168.0.100:9447/v1?x=1"):
            with self.assertRaises(ValueError):
                live.VjepaTransport(origin, lambda: "t" * 40, "/fixture/ca.pem", connection_factory=object)

    def test_success_refusal_and_failure_are_typed(self):
        log = []
        body = {"request_id": "r" * 64}
        value, digest = self.transport((200, b'{"ok":1}'), log)(body)
        self.assertEqual((value, len(digest)), ({"ok": 1}, 64))
        self.assertEqual(log[0][:3], ("POST", live.EXECUTE_PATH, body))
        self.assertEqual(log[0][3]["Authorization"], "Bearer " + "t" * 40)
        for reply, error, text in (((429, b'{"reason":"proxy_busy"}'), live.VjepaNotExecuted, "proxy_busy"),
                                   ((503, b'{"reason":"gpu_admission_refused"}'), live.VjepaNotExecuted, "gpu_admission_refused"),
                                   ((502, b'<html>'), live.TransportUnavailable, "response_not_json"),
                                   ((500, b'{"detail":"x"}'), live.TransportUnavailable, "status_500"),
                                   (OSError("down"), live.TransportUnavailable, "transport_unavailable"),
                                   ((200, b"x" * (live.MAX_RESPONSE_BYTES + 5)), live.TransportUnavailable, "response_too_large")):
            with self.assertRaises(error) as caught:
                self.transport(reply, [])(body)
            self.assertEqual(str(caught.exception), text)
        with self.assertRaises(live.VjepaNotExecuted):
            self.transport((200, b"{}"), [], token="short")(body)

    def test_a_thermal_shed_refusal_is_typed(self):
        with self.assertRaises(live.VjepaNotExecuted) as caught:
            self.transport((503, b'{"reason":"thermal_shed"}'), [])({"request_id": "r" * 64})
        self.assertEqual(str(caught.exception), "thermal_shed")


class KeepAliveTests(unittest.TestCase):
    """VjepaTransport's one kept connection (speed plan S1.7)."""

    class Response:
        def __init__(self, status, body, will_close):
            self.status, self.body, self.will_close, self.done = status, body, will_close, False

        def read(self, limit):
            self.done = len(self.body) <= limit
            return self.body[:limit]

        def isclosed(self):
            return self.done

    class Connection:
        """A connection with a real socket (readable once the peer closes or
        writes); script entries are (status, body), (status, body, 'close'),
        or an exception raised before any response byte."""

        def __init__(self, test):
            self.test, self.sent = test, []
            self.sock, self.peer = socket.socketpair()
            test.connections.append(self)

        def request(self, method, path, data, headers):
            self.sent.append(headers)
            if self.sock is None:
                raise http.client.NotConnected()

        def getresponse(self):
            reply = self.test.script.pop(0)
            if isinstance(reply, Exception):
                raise reply
            closing = reply[2:] == ("close",)
            response = KeepAliveTests.Response(reply[0], reply[1], closing)
            if closing:
                self.close()
            return response

        def close(self):
            for end in (self.sock, self.peer):
                if end is not None:
                    end.close()
            self.sock = self.peer = None

    def setUp(self):
        self.connections, self.script, self.now = [], [], 100.0
        self.addCleanup(lambda: [c.close() for c in self.connections])

    def transport(self, *script, **kw):
        self.script.extend(script)
        return live.VjepaTransport("https://192.168.0.100:9447", lambda: "t" * 40, "/fixture/ca.pem",
                                   connection_factory=lambda: self.Connection(self), monotonic=lambda: self.now, **kw)

    def test_one_connection_carries_successive_clips(self):
        transport = self.transport(*[(200, b'{"ok":1}')] * 3)
        for _ in range(3):
            self.assertEqual(transport({"request_id": "r" * 64})[0], {"ok": 1})
            self.now += 2
        [connection] = self.connections
        self.assertEqual(len(connection.sent), 3)
        self.assertNotIn("Connection", connection.sent[0])

    def test_reconnects_when_idle_readable_or_told_to_close(self):
        transport = self.transport(*[(200, b"{}")] * 2, (200, b"{}", "close"), *[(200, b"{}")] * 3)
        transport({})
        self.now += 15.5  # idle more than 15 s
        transport({})
        self.connections[1].peer.close()  # the supervisor closed it
        self.connections[1].peer = None
        transport({})  # a fresh connection, whose reply says Connection: close
        transport({})
        self.connections[3].peer.sendall(b"x")  # stray bytes before a send
        transport({})
        transport({})
        self.assertEqual([len(c.sent) for c in self.connections], [1, 1, 1, 1, 2])
        self.assertEqual([c.sock is None for c in self.connections], [True, True, True, True, False])

    def test_a_stale_kept_socket_is_typed_and_never_retried(self):
        for error in (ConnectionResetError(), http.client.RemoteDisconnected("closed"), BrokenPipeError()):
            with self.subTest(error=type(error).__name__):
                self.connections, self.script = [], []
                transport = self.transport((200, b"{}"), error)
                transport({})
                with self.assertRaises(live.TransportUnavailable) as caught:
                    transport({})
                self.assertEqual(str(caught.exception), live.STALE_SOCKET)
                self.assertEqual((len(self.connections), len(self.connections[0].sent)), (1, 2))  # no retry
                self.assertIsNone(self.connections[0].sock)  # closed on the error
        # The same failure on a fresh connection is an ordinary transport failure.
        self.connections, self.script = [], []
        with self.assertRaises(live.TransportUnavailable) as caught:
            self.transport(ConnectionResetError())({})
        self.assertEqual(str(caught.exception), "transport_unavailable")
        # A failure after response bytes on a kept connection is not stale either.
        self.connections, self.script = [], []
        transport = self.transport((200, b"{}"), (200, b"x" * (live.MAX_RESPONSE_BYTES + 5)))
        transport({})
        with self.assertRaises(live.TransportUnavailable) as caught:
            transport({})
        self.assertEqual(str(caught.exception), "response_too_large")
        self.assertIsNone(self.connections[0].sock)

    def test_keep_alive_off_and_close(self):
        transport = self.transport(*[(200, b"{}")] * 2, keep_alive=False)
        transport({})
        transport({})
        self.assertEqual([c.sent[0]["Connection"] for c in self.connections], ["close", "close"])
        self.assertEqual([c.sock for c in self.connections], [None, None])
        with self.assertRaises(ValueError):
            self.transport(keep_alive="yes")
        self.connections = []
        transport = self.transport((200, b"{}"))
        transport({})
        transport.close()
        self.assertIsNone(self.connections[0].sock)
        with self.assertRaises(live.TransportUnavailable) as caught:
            transport({})
        self.assertEqual(str(caught.exception), "transport_closed")


class PumpTests(Fixture, unittest.TestCase):
    def setUp(self):
        self.build()
        self.mono = Clock(50.0)
        self.scene_frames(990.0, 999.5)
        self.switch = {"on": True}

    def tearDown(self):
        self.close_store()

    def pump(self, transport, **config):
        return live.VjepaLivePump(self.store, self.session, transport=transport, profile=dict(PROFILE), config=config or None,
                                  clock=self.clock, monotonic=self.mono, enabled=lambda: self.switch["on"],
                                  occupied=OCCUPIED)

    def advance(self, seconds, people=True):
        start = self.clock.now
        self.clock.now += seconds
        self.mono.now += seconds
        self.scene_frames(start, self.clock.now - .5, people=people)

    def test_one_tick_sends_eight_stored_frames_and_records_a_valid_observation(self):
        transport = FakeTransport()
        pump = self.pump(transport)
        self.assertEqual(pump.tick(), {"state": "current", "reason": "recorded_valid"})
        [body] = transport.bodies
        self.assertEqual([f["observed_at"] for f in body["frames"]], [996.0 + .5 * i for i in range(8)])
        self.assertEqual((body["person_box"], body["single_person"], body["previous_request_id"]),
                         ([200.0, 40.0, 380.0, 340.0], True, None))
        [saved] = self.events("vjepa.observation")
        self.assertEqual((saved["status"], saved["observed_at"], saved["available_at"], saved["valid_until"]),
                         ("valid", 999.5, 1000.0, 1005.5))
        self.assertEqual(saved["result"]["reading"], .66)  # local ledger keeps the measurement
        self.assertNotIn("jpeg_base64", json.dumps(saved))

    def test_observation_keeps_how_frames_were_matched_to_detector_outputs(self):
        self.pump(FakeTransport()).tick()
        [saved] = self.events("vjepa.observation")
        self.assertEqual(saved["input"]["box_match"],
                         {"rule": live.BOX_MATCH_RULE, "tolerance_seconds": .75, "max_offset_seconds": 0.0})
        self.assertEqual(saved["input"]["box_frames"], 8)

    def test_observation_keeps_the_clip_frame_times_span_and_largest_gap(self):
        self.close_store()
        self.build()
        for t in ClipFrameTests.UNEVEN:
            self.frame(t)
            self.person(t)
        transport = FakeTransport()
        pump = self.pump(transport)
        self.assertEqual(pump.tick()["reason"], "recorded_valid")
        [body] = transport.bodies
        self.assertEqual([f["observed_at"] for f in body["frames"]], [996.05, 996.6, 997.3, 997.75, 998.1, 998.5, 999.0, 999.5])
        [saved] = self.events("vjepa.observation")
        clip = saved["input"]["clip"]
        self.assertEqual((clip["rule"], clip["slot_tolerance_seconds"], clip["span_seconds"], clip["max_gap_seconds"],
                          clip["max_grid_offset_seconds"]), (live.CLIP_RULE, .4, 3.45, .7, .3))
        self.assertEqual(clip["relative_times_seconds"], [-3.45, -2.9, -2.2, -1.75, -1.4, -1.0, -.5, 0.0])
        self.assertEqual(saved["fingerprint"], scene.fingerprint(saved))  # bound into the receipt
        self.assertEqual(saved["window_start"], 996.05)
        pump.status_event("periodic")
        [status] = self.events("vjepa.pump_status")
        self.assertEqual(status["clip"], {"rule": live.CLIP_RULE, "slot_tolerance_seconds": .4})

    def test_pump_status_events_record_the_typed_skip_counts(self):
        self.close_store()
        self.build()
        self.scene_frames(990.0, 999.5, people=False)  # frames, but no detector output
        pump = self.pump(FakeTransport())
        self.assertEqual(pump.tick()["reason"], "detector_output_unavailable")
        self.assertEqual(pump.tick()["reason"], "no_new_frame")
        pump.status_event("periodic")
        pump.start()
        pump.stop(timeout=5)
        periodic, stopped = self.events("vjepa.pump_status")
        self.assertEqual((periodic["schema"], periodic["reason"], stopped["reason"]),
                         (live.STATUS_SCHEMA, "periodic", "stopped"))
        self.assertEqual(periodic["counts"], {"detector_output_unavailable": 1, "no_new_frame": 1})
        self.assertEqual((periodic["ticks"], periodic["last_reason"], periodic["session_id"]),
                         (2, "no_new_frame", self.session["id"]))
        self.assertEqual(periodic["box_match"], {"rule": live.BOX_MATCH_RULE, "tolerance_seconds": .75, "min_box_frames": 4})
        self.assertEqual(stopped["status"], "stopped")

    def test_a_failing_status_write_never_breaks_the_pump(self):
        pump = self.pump(FakeTransport())
        pump.store = type("Broken", (), {"append_event": lambda *a, **k: (_ for _ in ()).throw(OSError("disk"))})()
        self.assertIsNone(pump.status_event("periodic"))

    def test_drift_chains_only_to_the_immediately_previous_valid_clip(self):
        transport = FakeTransport()
        pump = self.pump(transport)
        pump.tick()
        self.advance(2)
        self.assertEqual(pump.tick()["reason"], "recorded_valid")
        self.assertEqual(transport.bodies[1]["previous_request_id"], transport.bodies[0]["request_id"])
        self.assertIsNotNone(self.events("vjepa.observation")[1]["result"]["scene_drift"])
        self.advance(8)  # a gap longer than prior_max_gap_seconds
        pump.tick()
        self.assertIsNone(transport.bodies[2]["previous_request_id"])

    def test_invalid_results_are_recorded_without_results_and_never_chain(self):
        transport = FakeTransport(lambda b: result_for(b, reading=1.5), lambda b: {"unexpected": True},
                                  lambda b: result_for(b, request_id="0" * 64))
        pump = self.pump(transport)
        reasons = []
        for _ in range(3):
            reasons.append(pump.tick()["reason"])
            self.advance(2)
        self.assertEqual(reasons, ["recorded_invalid_probability_invalid", "recorded_invalid_keys_mismatch",
                                   "recorded_invalid_identity_mismatch"])
        self.assertEqual([s["result"] for s in self.events("vjepa.observation")], [None, None, None])
        pump.tick()
        self.assertIsNone(transport.bodies[3]["previous_request_id"])
        projected = live.scene_context(self.store, self.session, self.clock.now, generation=1, arms={"a"})["a"][0]["vjepa_scene"]
        self.assertEqual(projected["latest"]["facts"]["reading_signal"], "high")
        self.assertEqual(projected["summary_last_60s"]["invalid_samples"], "few")

    def test_no_person_box_skips_without_sending(self):
        self.close_store()
        self.build()
        self.scene_frames(990.0, 999.5, people=False)
        transport = FakeTransport()
        pump = self.pump(transport)
        self.assertEqual(pump.tick()["reason"], "detector_output_unavailable")
        self.assertEqual(pump.tick()["reason"], "no_new_frame")
        self.assertEqual(transport.bodies, [])

    def test_refusals_skip_and_repeated_transport_failures_back_off(self):
        down = live.TransportUnavailable("transport_unavailable")
        transport = FakeTransport(live.VjepaNotExecuted("proxy_busy"), down, down, down)
        pump = self.pump(transport)
        self.assertEqual(pump.tick()["reason"], "la_proxy_busy")
        for _ in range(3):
            self.advance(2)
            self.assertEqual(pump.tick()["reason"], "transport_unavailable")
        self.advance(2)
        self.assertEqual(pump.tick()["reason"], "transport_backoff")
        self.assertEqual(len(transport.bodies), 4)
        self.advance(live.BACKOFF_SECONDS)
        self.assertEqual(pump.tick()["reason"], "recorded_valid")

    def test_stale_kept_sockets_are_skips_that_never_count_toward_the_backoff(self):
        down, stale = live.TransportUnavailable("transport_unavailable"), live.TransportUnavailable(live.STALE_SOCKET)
        transport = FakeTransport(down, stale, stale, down, stale, stale, stale, down)
        pump = self.pump(transport)
        reasons = []
        for _ in range(8):
            reasons.append(pump.tick()["reason"])
            self.advance(2)
        self.assertEqual(reasons, ["transport_unavailable", live.STALE_SOCKET, live.STALE_SOCKET, "transport_unavailable",
                                   live.STALE_SOCKET, live.STALE_SOCKET, live.STALE_SOCKET, "transport_unavailable"])
        self.assertEqual(len(transport.bodies), 8)  # one send per tick: a stale socket is never retried in the tick
        self.assertEqual(pump.tick()["reason"], "transport_backoff")  # the third real failure
        self.assertEqual(self.events("vjepa.observation"), [])

    def test_a_thermal_shed_refusal_is_a_typed_skip_not_a_failure(self):
        shed = live.VjepaNotExecuted("thermal_shed")
        transport = FakeTransport(*[shed] * 4)
        pump = self.pump(transport)
        for _ in range(4):
            self.assertEqual(pump.tick(), {"state": "skipped", "reason": "la_thermal_shed"})
            self.advance(2)
        self.assertEqual((pump.failures, pump.backoff_until), (0, None))
        self.assertEqual(pump.tick()["reason"], "recorded_valid")
        self.assertEqual(pump.snapshot()["counts"]["la_thermal_shed"], 4)

    def test_one_request_in_flight_rate_cap_latest_frame_wins_and_kill_switch(self):
        transport = FakeTransport()
        pump = self.pump(transport, max_per_minute=2)
        pump.tick()
        self.assertEqual(pump.tick()["reason"], "no_new_frame")
        self.advance(2)
        pump.tick()
        self.advance(2)
        self.assertEqual(pump.tick()["reason"], "rate_capped")
        self.advance(60)
        self.assertEqual(pump.tick()["reason"], "recorded_valid")
        self.assertEqual(transport.bodies[-1]["frames"][-1]["observed_at"], self.clock.now - .5)
        self.switch["on"] = False
        self.advance(2)
        self.assertEqual(pump.tick(), {"state": "disabled", "reason": "disabled"})
        self.assertEqual(len(transport.bodies), 3)

    def test_skips_when_not_recording_or_frames_are_stale(self):
        transport = FakeTransport()
        pump = self.pump(transport)
        self.clock.now += 30
        self.assertEqual(pump.tick()["reason"], "frames_stale")
        self.store.transition(self.session["id"], "stopping")
        self.assertEqual(pump.tick()["reason"], "not_recording")
        self.assertEqual(transport.bodies, [])

    def test_configuration_and_profile_bounds(self):
        for config in ({"interval_seconds": .9}, {"interval_seconds": 11}, {"max_per_minute": 61},
                       {"min_box_frames": 0}, {"min_box_frames": 9}, {"ntu_confidence": .4}, {"surprise": 1}):
            with self.assertRaises(ValueError):
                live.pump_config(config)
        # Defaults unchanged (2 s, 40/min); the bounds now reach S1.4's 1 s and 60/min.
        self.assertEqual({k: live.pump_config()[k] for k in ("interval_seconds", "max_per_minute")},
                         {"interval_seconds": 2.0, "max_per_minute": 40})
        self.assertEqual({k: live.pump_config({"interval_seconds": 1.0, "max_per_minute": 60})[k]
                          for k in ("interval_seconds", "max_per_minute")}, {"interval_seconds": 1.0, "max_per_minute": 60})
        with self.assertRaises(ValueError):
            live.VjepaLivePump(self.store, self.session, transport=FakeTransport(),
                               profile={**PROFILE, "artifact_sha256": "0" * 64}, occupied=OCCUPIED)
        slow = FakeTransport()
        slow.timeout = 30
        with self.assertRaises(ValueError):
            self.pump(slow)

    def test_thread_starts_and_stops_cleanly_with_the_recording(self):
        transport = FakeTransport()
        pump = live.VjepaLivePump(self.store, self.session, transport=transport, profile=dict(PROFILE),
                                  config={"interval_seconds": 3}, occupied=OCCUPIED)
        pump.start()
        started = time.monotonic()
        pump.stop(timeout=2)
        self.assertFalse(pump.thread.is_alive())
        self.assertLess(time.monotonic() - started, 2)
        self.assertTrue(transport.closed)
        self.assertEqual(transport.bodies, [])
        self.assertEqual(pump.tick()["reason"], "stopped")


if __name__ == "__main__":
    unittest.main()
