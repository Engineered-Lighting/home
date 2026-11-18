"""Native elapsed cohorts on the real pinned tracker; no camera, model or host I/O."""
import copy
import dataclasses
import hashlib
import inspect
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import patch

import motion
from contracts import FrameRef, PlaneRef, TrackingSnapshot
from perceive import ClockOffset, Perceiver, claim_time

PIN = "a3298878be93964e29dea5eb156db9e9cd91d85d"
FIXTURE_SHA = {
    "kalman.py": "2da15ba9fb07fa586fe383572518f9cd5decb65f301f23328aac2373fd2ad2f1",
    "associate.py": "b31763b8b801d1f4e73eb4fa0dc75c7738092672ad581780f7bd898b8edb85f4",
    "tracker_core.py": "c443a76c314b1793ef028c40c7deebd56e2a85dc55dc43f13d0c16009b6b2a61",
    "records.py": "78a804264adb008b8f1d18b4e78cd481b394dabb7a1e1f21157e3d033152babc",
}


def pinned_core():
    """Exact reviewed source fixture: works in a clean CI/source-only checkout."""
    fixture = json.loads(Path(__file__).with_name("native_clock_fixture.json").read_text(encoding="utf-8"))
    if fixture["schema"] != "den-native-clock-test-fixture/v1" or fixture["source_commit"] != PIN:
        raise AssertionError("wrong native-clock fixture provenance")
    def read(name):
        value = fixture["files"][name]
        if hashlib.sha256(value["source"].encode()).hexdigest() != FIXTURE_SHA[name] or value["sha256"] != FIXTURE_SHA[name]:
            raise AssertionError("changed pinned tracker fixture")
        return value["source"]
    modules = {}
    for name in ("kalman", "associate", "tracker_core", "records"):
        module = ModuleType("native_clock_test_" + name)
        with patch.dict(sys.modules, modules):
            exec(compile(read(name + ".py"), PIN + "/" + name + ".py", "exec"), module.__dict__)
        modules[name] = module
    params = fixture["tracker"]
    expected = "a475b5a50ff2c717a65cb6761849487cefa0b9fd4142f50675ff486f37f2e9cf"
    if hashlib.sha256(json.dumps(params, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest() != expected:
        raise AssertionError("changed pinned tracker parameters")
    return modules["tracker_core"].TrackerCore, params, modules["records"]


def source_frame(index, source_s, captured, delay, *, epoch=1, age=.35, arrival=None):
    arrival = 500 + (index - 1) * .2 + delay if arrival is None else arrival
    rgb = PlaneRef(index, source_s - 20, source_s, 640, 360)
    depth = PlaneRef(1000 + index, source_s - 20, source_s, 640, 400)
    frame = FrameRef("run", epoch, index, "camera", "rgb", rgb, None, depth, "c" * 64,
                     "transform", "scan", captured, arrival, 1800000000 + captured)
    bundle = NS(session="camera", rgb_seq=index, t_host=source_s, arrival_mono=arrival,
                request_mono=arrival + 1000 - .02, receipt_arrival_mono=arrival + 1000,
                capture_age_receipt={"schema": "den-body-pair-age-1", "frame_key": ["camera", index, 1000 + index],
                                     "evidence_id": "age" + str(index), "rgb_age_upper_s": age,
                                     "depth_age_upper_s": age, "separation_upper_s": .01})
    meta = dict(session="camera", rgb_seq=index, depth_seq=1000 + index)
    meta["raw"] = {"depth_correction": {"calibration_sha256": "c" * 64}, "frames": {
        name: {"sequence": plane.seq, "device_timestamp_seconds": plane.device_s,
               "host_monotonic_timestamp_seconds": plane.host_s}
        for name, plane in (("rgb", rgb), ("depth", depth))}}
    bundle.data = (meta, None)
    bundle.fetch_s = bundle.parse_s = 0
    bundle.arrival_unix = 1800000000 + arrival
    return frame, bundle, meta


class NativeClockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.Core, cls.params, cls.Records = pinned_core()

    def perceiver(self, native=True):
        p = Perceiver.__new__(Perceiver)
        p.native_motion_clock = native
        p.t = NS(records=self.Records, observe=NS(observe=lambda row, *args, **kwargs: row["observations"]),
                 takes=NS(read_frame=lambda data: data, BundleError=ValueError),
                 roommodel=NS(RegistrationRefused=ValueError))
        p.track_method = NS(tracker=self.params, room={"exits_m": [], "floor_m": None})
        p.run_id, p.sessions, p.seq, p.home, p.frame_id = "run", 1, 0, "den", 0
        p.mode, p.problem, p.dark_since, p.light_since = "rgb", None, None, None
        p.scope, p.labels, p.body = {"transform_id": "transform", "content_revision": "scan"}, None, None
        p.counts = dict(frames=0, observations=0, weak_only_hidden=0, expired=0, out_of_order=0)
        p.head_direction = dict(frames=0, candidates=0, bound=0, published=0, frame_candidates=0, frame_bound=0, reasons={})
        p.camera, p.settings = NS(error_bound_m=.1, check_frame=lambda _: None), None
        p.lags, p.drops, p.arrivals, p.camera_ages, p.capture_to_stream = [], [], [], [], []
        p.clock, p.retired, p.session = ClockOffset(), set(), "camera"
        p.last_t_host = p.last_seq = p.last_input_mono = p.clock_reset_candidate = p.calibration_id = None
        p.require_source_correction, p.source_correction = False, None
        p.detector = p._head_detector = None
        p.now = lambda: 1800000000
        p._timing = p._transit = p._clear_body = lambda *args, **kwargs: None
        p.body_captures = []
        p._submit_body = lambda prepared, **kwargs: p.body_captures.append(prepared.ref.capture_mono)
        def event(ago, tracks, camera, now):
            p.seq += 1
            return {"seq": p.seq, "tracks": tracks, "camera": {"why": camera.get("why")}}
        p.event = event
        p._new_core()
        p.snapshot = TrackingSnapshot("run", 1, 0, None, "starting", None, "unknown", ())
        return p

    def replay(self, delays):
        core, clock, receipts, offset = self.Core(self.params), motion.NativeClock(), motion.Receipts(), ClockOffset()
        states, forecasts, filters, claims = [], [], [], []
        claim_cache = {}
        for i, delay in enumerate(delays, 1):
            source = 100 + (i - 1) * .2
            arrival = 500 + (i - 1) * .2 + delay
            offset.add(source, arrival)
            ref, bundle, meta = source_frame(i, source, source + offset.offset(), delay)
            step = clock.step(ref, bundle, meta)
            detection = {"id": str(i), "xy": [.5 + .4 * (source - 100), 1.0],
                         "R": [[.0009, 0], [0, .0009]], "obs_ids": ["obs" + str(i)], "heading": None}
            for raw, state in core.step(step.source_s, [detection]):
                receipts.audit_native(raw, frame=ref, native_step=step, filter_time=core.t)
                public_since = claim_time(claim_cache, raw.id, raw.since_ts, core.t, ref.capture_mono)
                value = receipts.update(raw, frame=ref, track_id="person", association_revision=1,
                    association_status="confident" if not raw.ambiguous else "ambiguous", state=state,
                    current_obs_ids=tuple(raw.obs_ids), available_mono=arrival + .02,
                    white_accel_sigma_mps32=self.params["accel_sigma"], registration_error_m=.1,
                    timing_error_s=motion.timing_bound(ref, bundle, meta), timing_evidence_id="age" + str(i),
                    native_step=step, filter_time=core.t)
                states.append(value)
                filters.append((copy.deepcopy(raw.x), copy.deepcopy(raw.P)))
                claims.append((raw.since_ts, public_since, ref))
                forecasts.append(motion.forecast(value, arrival + .02, frame=ref, track_id="person", association_revision=1))
        return states, forecasts, filters, claims, clock

    def test_real_native_cohort_emits_conditional_forecasts_under_variable_delay(self):
        variable = ([.30, .20, .05, .15, .10] * 10)
        states, forecasts, filters, _, clock = self.replay(variable)
        baseline = self.replay([.30] * len(variable))
        self.assertEqual(filters, baseline[2])  # same actual native KF positions, velocities and every P term
        live = [value for value in forecasts if value is not None]
        self.assertGreater(len(live), 10)
        self.assertGreater(clock.counts["native_absolute_intervals_overlap"], 0)
        self.assertEqual(clock.counts["native_clock_incidents"], 0)
        self.assertTrue(all(value.state.time_qualified is False for value in live))
        self.assertTrue(all(value.state.filter_clock == "rgb_host_elapsed" for value in live))
        self.assertTrue(all(value.state.source_rate_assumption == "nominal_1to1_unverified" for value in live))
        self.assertTrue(all(value.samples[-1].xy_m[0] > value.state.xy_m[0] for value in live))
        self.assertTrue(all(value.state.timing_error_s >= .1 for value in live))

    def test_public_claim_time_is_latched_despite_offset_changes_and_remains_ask_eligible(self):
        _, _, _, claims, _ = self.replay([.30] * 20 + [.20, .05, .15, .10] * 10)
        public = {}
        for source_since, public_since, ref in claims:
            self.assertIsNotNone(public_since)
            self.assertLessEqual(public_since, ref.capture_mono)
            if source_since in public:
                self.assertEqual(public[source_since], public_since)
            public[source_since] = public_since
        # Exercise the actual reading eligibility predicate without opening HA/Qwen.
        from ask import Asker
        ref = claims[-1][2]
        track = NS(current_obs_ids=("obs",), state="confirmed", association_status="confident",
                   association_since_mono=claims[-1][1])
        snap = TrackingSnapshot("run", 1, 1, ref, "fresh", None, "present", (track,))
        self.assertIs(Asker._track(snap, ref.arrival_mono + .02), track)

    def test_perceiver_finish_uses_native_core_and_preserves_public_frame_receipts(self):
        p = self.perceiver()
        forecasts, claim_stamps, captures, clamped = [], {}, [], 0
        source, previous_delay = 99.8, .30
        for i, delay in enumerate([.30] * 25 + [.02, .02, .10, .20, .30] * 8, 1):
            # Catch up to a later source frame when transit drops sharply: all
            # actual receiver arrivals are causal and strictly increasing.
            source += .4 if previous_delay - delay > .19 else .2
            previous_delay = delay
            arrival = 400 + source + delay
            _, bundle, meta = source_frame(i, source, 0, delay, arrival=arrival)
            prepared = p.prepare(bundle, arrival + .01)
            self.assertIsNotNone(prepared)
            ref = prepared.ref
            clamped += ref.capture_mono < bundle.t_host + p.clock.offset()
            self.assertLessEqual(ref.capture_mono, arrival)
            if captures:
                self.assertLess(captures[-1], ref.capture_mono)
            captures.append(ref.capture_mono)
            observed = dict(kind="person_box", obs_id="obs" + str(i), validity="valid", score=.9,
                            capture_ts=ref.capture_mono, source={"sensor_id": "camera"},
                            room_position={"x": .5 + .4 * (source - 100), "y": 1, "cov": [[.0009, 0], [0, .0009]]})
            result = p.finish(prepared, {"luma": 100, "row": {"observations": [observed]}}, {}, arrival + .02)
            self.assertIsNotNone(result)
            self.assertIs(p.snapshot.frame, ref)
            self.assertEqual(p.core.t, source)
            for track in p.snapshot.tracks:
                if track.association_status == "confident":
                    old = claim_stamps.setdefault(track.association_revision, track.association_since_mono)
                    self.assertEqual(old, track.association_since_mono)
                    self.assertEqual(result["tracks"][0]["age_s"], 0)
                    self.assertFalse(track.seated.state)
                if track.motion is not None:
                    self.assertEqual(track.motion.observed_mono, ref.capture_mono)
                    self.assertEqual(track.motion.filter_observed_s, source)
                    self.assertEqual(track.motion.valid_until_mono,
                                     ref.capture_mono + motion.MAX_AGE_S - motion.timing_bound(ref, bundle, meta))
                    forecasts.append(motion.forecast(track.motion, arrival + .03, frame=ref))
        self.assertGreater(sum(value is not None for value in forecasts), 10)
        self.assertEqual(p.sessions, 1)  # variable delivery delay does not reset the camera/track epoch
        self.assertEqual(len(claim_stamps), 1)  # no association revision churn after confirmation
        self.assertGreater(clamped, 0)  # crosses the rolling quantile's ignored-new-minimum threshold
        self.assertEqual(p.body_captures, captures)
        from ask import Asker
        self.assertIs(Asker._track(p.snapshot, arrival + .03), p.snapshot.tracks[0])
        old_core = p.core
        ref, bundle, meta = source_frame(i + 1, source + .2, arrival + .1, .1, arrival=arrival + .2)
        ref = dataclasses.replace(ref, rgb=dataclasses.replace(ref.rgb, device_s=0))
        result = p.finish(NS(ref=ref, bundle=bundle, meta=meta, depth=None),
                          {"luma": 100, "row": {"observations": [observed]}}, {}, arrival + .22)
        self.assertIsNotNone(result)
        self.assertEqual(result["tracks"], [])
        self.assertEqual(p.sessions, 2)
        self.assertIsNone(p.snapshot.frame)
        self.assertEqual(p.snapshot.status, "degraded")
        self.assertEqual(p.snapshot.presence, "unknown")
        self.assertEqual(p.snapshot.tracks, ())  # no subsequent prepare/expiry is needed to clear authority
        self.assertIsNot(p.core, old_core)
        self.assertIsNone(p.core.t)
        self.assertEqual(list(p.clock.samples), [])

    def test_backwards_sdk_or_host_clock_and_reused_ids_taint_once(self):
        for change in ("host", "device", "sequence", "session", "arrival"):
            with self.subTest(change=change):
                clock = motion.NativeClock()
                ref, bundle, meta = source_frame(1, 100, 500.1, .1)
                clock.step(ref, bundle, meta)
                ref, bundle, meta = source_frame(2, 100.2, 500.3, .1)
                if change == "host":
                    ref = dataclasses.replace(ref, rgb=dataclasses.replace(ref.rgb, host_s=99.9)); bundle.t_host = 99.9
                elif change == "device":
                    ref = dataclasses.replace(ref, rgb=dataclasses.replace(ref.rgb, device_s=79))
                elif change == "sequence":
                    ref = dataclasses.replace(ref, rgb=dataclasses.replace(ref.rgb, seq=1))
                elif change == "arrival":
                    ref = dataclasses.replace(ref, arrival_mono=499); bundle.arrival_mono = 499
                else:
                    ref = dataclasses.replace(ref, camera_session="other")
                for _ in range(3):
                    with self.assertRaises(ValueError):
                        clock.step(ref, bundle, meta)
                self.assertEqual(clock.counts["native_clock_incidents"], 1)
                self.assertEqual(clock.counts["native_clock_refusals"], 3)

    def test_refuted_nominal_rate_never_rebases_existing_cohort(self):
        clock = motion.NativeClock()
        first = source_frame(1, 100, 500.1, .1, age=.02)
        # For this tight fixture use the actual capture within the short receipt.
        clock.step(*first)
        old_bounds = clock.offset_bounds
        bad = source_frame(2, 100.8, 500.3, .1, age=.02)
        with self.assertRaisesRegex(ValueError, "rate_or_offset_changed"):
            clock.step(*bad)
        self.assertEqual(clock.offset_bounds, old_bounds)
        good = source_frame(3, 100.4, 500.5, .1, age=.02)
        with self.assertRaises(ValueError):
            clock.step(*good)  # a later clean frame cannot certify this old P

    def test_existing_epoch_reset_constructs_a_new_filter_and_clock(self):
        p = Perceiver.__new__(Perceiver)
        p.t = NS(records=NS(tracker_core=NS(TrackerCore=self.Core), body_heading=lambda _: None))
        p.track_method = NS(tracker=self.params, room={"exits_m": [], "floor_m": None})
        p.sessions, p.dark_since, p.light_since = 1, None, None
        p.native_motion_clock = True
        p._clear_body = lambda reason: None
        p._new_core()
        old = p.core
        old.step(100, [{"id": "one", "xy": [1, 1], "R": [[.01, 0], [0, .01]], "obs_ids": ["one"]}])
        p.native_clock_counts["native_clock_incidents"] = 1
        p._epoch()
        self.assertIsNot(p.core, old)
        self.assertIsNone(p.core.t)
        self.assertEqual(p.core.tracks, [])
        self.assertEqual(p.sessions, 2)
        self.assertIsNone(p.native_clock.previous)
        self.assertEqual(p.native_clock_counts["native_clock_incidents"], 1)

    def test_native_receipt_cannot_bless_filter_updated_on_a_different_clock(self):
        clock, receipts, core = motion.NativeClock(), motion.Receipts(), self.Core(self.params)
        ref, bundle, meta = source_frame(1, 100, 500.1, .1)
        step = clock.step(ref, bundle, meta)
        raw, _ = core.step(ref.capture_mono, [{"id": "one", "xy": [1, 1], "R": [[.01, 0], [0, .01]], "obs_ids": ["one"]}])[0]
        self.assertFalse(receipts.audit_native(raw, frame=ref, native_step=step, filter_time=core.t))

    def test_default_keeps_the_existing_public_filter_clock(self):
        self.assertIs(inspect.signature(Perceiver).parameters["native_motion_clock"].default, False)
        p = self.perceiver(native=False)
        self.assertIsNone(p.native_clock)
        ref, bundle, _ = source_frame(1, 100, 0, .1)
        prepared = p.prepare(bundle, bundle.arrival_mono + .01)
        p.finish(prepared, {"luma": 100, "row": {"observations": []}}, {}, bundle.arrival_mono + .02)
        self.assertEqual(p.core.t, prepared.ref.capture_mono)
        self.assertNotEqual(p.core.t, ref.rgb.host_s)

    def test_source_jumps_clear_authority_and_reanchor_next_epoch_without_reusing_P(self):
        for jump in (-10, 100):
            with self.subTest(jump=jump):
                p = self.perceiver()
                _, first, _ = source_frame(1, 100, 0, .1)
                good = p.prepare(first, 500.11)
                p.finish(good, {"luma": 100, "row": {"observations": []}}, {}, 500.12)
                old = p.core
                _, bad, _ = source_frame(2, 100.2 + jump, 0, .1)
                prepared = p.prepare(bad, 500.31)
                if prepared is not None:
                    self.assertLessEqual(prepared.ref.capture_mono, bad.arrival_mono)
                    result = p.finish(prepared, {"luma": 100, "row": {"observations": []}}, {}, 500.32)
                    self.assertIsNotNone(result)
                self.assertIsNone(p.snapshot.frame)  # no later frame/expiry required
                self.assertEqual(p.snapshot.tracks, ())
                self.assertEqual(p.snapshot.status, "degraded")
                self.assertEqual(p.sessions, 2)
                self.assertIsNot(p.core, old)
                self.assertIsNone(p.core.t)
                self.assertEqual(list(p.clock.samples), [])
                _, next_bundle, _ = source_frame(3, 100.4 + jump, 0, .1)
                next_frame = p.prepare(next_bundle, 500.51)
                self.assertEqual(next_frame.ref.capture_mono, next_bundle.arrival_mono)
                self.assertLessEqual(next_frame.ref.capture_mono, 500.51)
                p.finish(next_frame, {"luma": 100, "row": {"observations": []}}, {}, 500.52)
                self.assertEqual(p.core.t, next_bundle.t_host)
                self.assertEqual(p.sessions, 2)

    def test_active_claim_receipt_survives_more_than_128_unrelated_births(self):
        cache = {}
        since = claim_time(cache, "active", 100, 100, 500.3)
        for i in range(300):
            self.assertEqual(claim_time(cache, "active", 100, 101 + i, 501 + i), since)
            claim_time(cache, "birth" + str(i), 101 + i, 101 + i, 501 + i)
        self.assertEqual(claim_time(cache, "active", 100, 999, 1400), 500.3)
        self.assertLessEqual(len(cache), 128)

    def test_arrival_clamp_does_not_admit_a_known_expired_frame(self):
        p = self.perceiver()
        p.clock.samples.extend([400.30] * 25)
        p.last_t_host, p.last_seq = 104.8, 25
        _, bundle, _ = source_frame(26, 105.2, 0, .02, arrival=505.22)
        # The old quantile would estimate 505.50, which appears fresh at
        # 506.80. Its actual arrival bound makes it expired before dispatch.
        self.assertIsNone(p.prepare(bundle, 506.80))
        self.assertEqual(p.problem, "camera frame expired")
        self.assertEqual(p.counts["expired"], 1)
        self.assertIsNone(p.core.t)

    def test_synchronous_entry_returns_immediate_reset_event_on_source_backjump(self):
        p = self.perceiver()
        _, first, _ = source_frame(1, 100, 0, .1)
        good = p.prepare(first, 500.11)
        p.finish(good, {"luma": 100, "row": {"observations": []}}, {}, 500.12)
        self.assertIsNotNone(p.snapshot.frame)
        _, bad, _ = source_frame(2, 90, 0, .1)
        event = p.process(bad, {"status": "ok"}, 500.31)
        self.assertIsNotNone(event)
        self.assertEqual(event["tracks"], [])
        self.assertEqual(event["perception_status"], "degraded")
        self.assertIsNone(p.snapshot.frame)
        self.assertEqual(p.sessions, 2)

    def test_same_native_P_does_not_imply_same_receiver_target_center(self):
        variable = self.replay([.30] * 20 + [.20, .05, .15, .10] * 10)
        constant = self.replay([.30] * 60)
        a, b = variable[1][-1], constant[1][-1]
        self.assertIsNotNone(a)
        self.assertIsNotNone(b)
        self.assertEqual(a.state.covariance, b.state.covariance)
        # Forecast at the same receiver instant: the public absolute origin is
        # still biased by delivery, despite identical native x/P. Its explicit
        # age envelope remains in force; this is not measured containment.
        at = max(a.issued_mono, b.issued_mono)
        a = motion.forecast(a.state, at)
        b = motion.forecast(b.state, at)
        delta = abs(a.samples[0].xy_m[0] - b.samples[0].xy_m[0])
        self.assertGreater(delta, .05)
        self.assertLessEqual(delta, a.samples[0].sigma_m + b.samples[0].sigma_m)
        self.assertFalse(a.state.time_qualified)


if __name__ == "__main__":
    unittest.main()
