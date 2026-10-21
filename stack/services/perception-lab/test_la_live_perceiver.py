"""Always-on LA live perceiver (speed plan S1.3, section 15 #11): its config,
the household kill switch la-live-inference-1, no capture and no LA call while
vacant, the synthetic per-day session, pause and identity re-probe on an
indeterminate row, mutual exclusion with the recording pump, the controller
composition (one capture owner, preview tap, lifecycle) and edge wake to the
first send. Scripted transports, gates and captures; no camera or network."""
import json
import logging
import os
import tempfile
import threading
import time
import unittest
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import frame_ring
import la_fact_log
import la_live_perceiver as perceiver_module
import qwen_live
import qwen_scene as scene
from frame_ring import CaptureTurns, FrameRing
from la_fact_log import FactLog
from la_live_perceiver import ENTRY, LaLivePerceiver, check_config, day_session, household_granted
from test_frame_ring import ACCEPT, Frames, Gate
from test_qwen_live import PROFILE, TRANSPORT_CONFIG, Clock, FakeTransport, completed, terminal
from qwen_transport import CompletionUnproven

LOG = "perception_lab.la_live"
BLOCK = {"enabled": True, "transport_file": "/private/la-live-transport.json", "artifact_sha256": "a" * 64,
         "runtime_revision": "vllm-0.11.0", "fact_log_directory": "/var/lib/perception-lab/la-fact-log"}
READER = {"origin": "https://victoria-jetson.example.ts.net", "credential_file": "/nonexistent/ha.token"}
T0 = datetime(2026, 10, 8, 18, 0, 0, tzinfo=timezone.utc).timestamp()


class Reader(Gate):
    """An occupancy reader stand-in with the controller lifecycle."""
    def __init__(self, state="occupied"):
        super().__init__(state)
        self.started = self.stopped = 0

    def start(self):
        self.started += 1

    def stop(self, timeout=None):
        self.stopped += 1

    def snapshot(self):
        return {"state": self.state}


class PerceiverFixture:
    def build(self, *outcomes, lookups=(), granted=True, state="occupied", lab=False, clock=None, mono=None,
              block=None, fetch=None):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.clock = clock or Clock(T0)
        self.mono = mono or Clock(50.0)
        self.grant = {"on": granted}
        self.reader = Reader(state)
        self.transport = FakeTransport(*outcomes, lookups=lookups)
        self.ring = FrameRing(clock=self.clock, contract=ACCEPT)
        self.frames = Frames()
        self.fetched = []
        self.log = FactLog(self.directory.name, clock=self.clock, monotonic=self.mono, private=False)
        self.perceiver = LaLivePerceiver(
            transport=self.transport, profile=dict(PROFILE), reader=self.reader, fact_log=self.log,
            granted=lambda: self.grant["on"], fetch=fetch or self.fetch, settings=check_config({**BLOCK, **(block or {})}),
            site_id="victoria", camera_id="den", lab_qwen_enabled=lab, clock=self.clock, monotonic=self.mono,
            local_time=lambda epoch: (19, 40), ring=self.ring, turns=CaptureTurns(threading.Lock(), monotonic=self.mono))
        self.addCleanup(self.quiet_stop)
        return self.perceiver

    def quiet_stop(self):
        logger = logging.getLogger(LOG)
        disabled, logger.disabled = logger.disabled, True
        try:
            self.perceiver.stop(2)
        finally:
            logger.disabled = disabled

    def fetch(self):
        self.fetched.append(self.clock.now)
        return self.frames(self.clock.now - .1)

    def running(self):
        """Mark the perceiver running without its threads: ticks are driven by hand."""
        self.perceiver.state = "running"

    def fill(self, seconds=4.0):
        """Ring frames every 0.5 s up to now, as the capture would take them."""
        start = self.clock.now - seconds
        for k in range(int(seconds * 2) + 1):
            observed = start + .5 * k
            saved = self.clock.now
            self.clock.now = observed + .1
            self.ring.offer(*self.frames(observed))
            self.clock.now = saved

    def advance(self, seconds, *, frames=True):
        self.clock.now += seconds
        self.mono.now += seconds  # also past the kill switch's one-second cache
        if frames:
            self.fill()

    def lines(self):
        values = []
        for name in sorted(os.listdir(self.directory.name)):
            with open(os.path.join(self.directory.name, name), encoding="ascii") as source:
                values += [json.loads(line) for line in source.read().splitlines()]
        return values


class ConfigTests(unittest.TestCase):
    def test_block_defaults_bounds_and_exact_keys(self):
        settings = check_config(BLOCK)
        self.assertEqual({k: settings["pump"][k] for k in ("interval_seconds", "max_per_minute", "frames", "span_seconds")},
                         {"interval_seconds": 4.0, "max_per_minute": 15, "frames": 3, "span_seconds": 3.0})  # today's cadence
        self.assertEqual(settings["fact_log"], la_fact_log.DEFAULTS)
        self.assertEqual((settings["indeterminate_reprobe_seconds"], settings["capture_interval_seconds"]), (60.0, .5))
        self.assertEqual(check_config({**BLOCK, "interval_seconds": 1.0, "max_per_minute": 90, "retention_days": 7,
                                       "indeterminate_reprobe_seconds": 120})["indeterminate_reprobe_seconds"], 120)
        for change in ({"enabled": "yes"}, {"transport_file": "relative.json"}, {"fact_log_directory": "logs"},
                       {"artifact_sha256": "x"}, {"runtime_revision": ""}, {"timezone": "../etc"}, {"horizon_seconds": 60},
                       {"interval_seconds": .4}, {"indeterminate_reprobe_seconds": 29}, {"indeterminate_reprobe_seconds": 601},
                       {"capture_interval_seconds": .4}, {"capture_interval_seconds": 3}, {"retention_days": 0},
                       {"occupancy_entity": "binary_sensor.x"}, {"surprise": 1}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                check_config({**BLOCK, **change})
        with self.assertRaises(ValueError):
            check_config({k: v for k, v in BLOCK.items() if k != "fact_log_directory"})

    def test_the_live_config_needs_the_occupancy_reader_and_only_the_managed_controller_runs_it(self):
        from live_detector_app import create_live_detector_app, validate_config
        from test_ha_provisioning import live_config
        with self.assertRaisesRegex(ValueError, "requires the occupancy_reader block"):
            validate_config(live_config(la_live_perceiver=BLOCK))
        value = validate_config(live_config(la_live_perceiver=BLOCK, occupancy_reader=READER))
        self.assertEqual(value["la_live_perceiver"], BLOCK)
        validate_config(live_config(la_live_perceiver={**BLOCK, "enabled": False}))  # off needs nothing else
        with self.assertRaises(ValueError):
            validate_config(live_config(la_live_perceiver={**BLOCK, "extra": 1}, occupancy_reader=READER))
        with patch.dict(os.environ, {"PERCEPTION_LAB_LIVE_CONFIG": "/private/live.json"}), \
                patch("live_detector_app.private_json", return_value=live_config(la_live_perceiver=BLOCK, occupancy_reader=READER)):
            with self.assertRaisesRegex(ValueError, "requires the managed controller"):
                create_live_detector_app()

    def test_the_day_session_and_the_household_switch(self):
        first = day_session("victoria", "den", T0)
        self.assertRegex(first, r"^[a-f0-9]{32}$")  # LA's session id shape
        self.assertEqual(day_session("victoria", "den", T0 + 3600), first)
        midnight = datetime(2026, 10, 9, tzinfo=timezone.utc).timestamp()
        self.assertEqual(day_session("victoria", "den", midnight - .001), first)
        self.assertNotEqual(day_session("victoria", "den", midnight), first)
        self.assertNotEqual(day_session("victoria", "kitchen", T0), first)
        policies = {"granted": {"structured_sources": [ENTRY]}, "jev_only": {"structured_sources": ["lab-live-scene-1"]},
                    "absent": {}, "invalid": {"structured_sources": "la-live-inference-1"}, "not_a_dict": ["x"]}
        results = {name: household_granted(lambda value=value: value)() for name, value in policies.items()}
        self.assertEqual(results, {"granted": True, "jev_only": False, "absent": False, "invalid": False, "not_a_dict": False})

        def unreadable():
            raise PermissionError("policy")
        self.assertFalse(household_granted(unreadable)())


class ActivityMonitorControlTests(PerceiverFixture, unittest.TestCase):
    def loop(self):
        from activity_loop import ActivityLoop
        from activity_loop_runtime import ManagedDeepStack
        perceiver = self.build()
        self.running()
        runtime = SimpleNamespace(close=lambda **_: {'drained': True})
        perceiver_module.attach(runtime, perceiver)
        store = SimpleNamespace(settings=SimpleNamespace(max_session_seconds=300), clock=self.clock)
        controller = SimpleNamespace(store=store, runtime=runtime)
        deep = ManagedDeepStack(controller, clock=self.clock, release=SimpleNamespace(snapshot=lambda: {}))
        return ActivityLoop(deep=deep, occupancy=lambda: 'off', clock=self.clock,
                            publisher=SimpleNamespace(snapshot=lambda: {}))

    def test_monitor_stop_while_vacant_blocks_the_actual_always_on_qwen(self):
        loop = self.loop()
        loop.stop('monitor_backoff')
        self.assertFalse(self.perceiver.enabled(), 'the actual Qwen pump must be blocked even without a recording')
        self.assertEqual(loop.snapshot()['monitor_control_version'], 2)
        self.assertTrue(loop.deep.snapshot()['qwen_control']['blocked'])
        self.fill()
        with self.assertLogs(LOG, level='WARNING') as logged:
            self.perceiver.pump.tick()
        self.assertEqual(self.transport.envelopes, [])
        self.assertFalse(self.perceiver._capture_wanted())
        self.assertIn('resource monitor back-off', logged.output[0])
        self.assertEqual(self.perceiver.structured_context()['provider']['last_reason'], 'monitor_backoff')
        self.assertTrue(self.log.writable(), 'back-off must retain the original receipt log')

    def test_reduced_cadence_reaches_actual_qwen_before_a_recording_exists(self):
        loop = self.loop()
        original = self.perceiver.pump.config['interval_seconds']
        loop.set_cadence('reduced')
        self.assertEqual(self.perceiver.pump.config['interval_seconds'], original * 2)
        loop.set_cadence('reduced')
        self.assertEqual(self.perceiver.pump.config['interval_seconds'], original * 2, 'repeated back-off cannot compound')
        self.assertEqual(loop.cadence_status()['qwen'], dict(blocked=False, factor=2, interval_seconds=original * 2))
        loop.set_cadence('normal')
        self.assertEqual(self.perceiver.pump.config['interval_seconds'], original)

    def test_monitor_stop_cannot_replace_an_existing_operator_stop(self):
        from activity_loop import ResumeRefused
        loop = self.loop()
        loop.stop('operator_stop')
        loop.stop('monitor_backoff')
        with self.assertRaises(ResumeRefused):
            loop.resume('monitor_backoff')
        self.assertEqual(loop.snapshot()['stopped_reason'], 'operator_stop')
        self.assertFalse(self.perceiver.enabled())
        loop.resume()
        self.assertTrue(self.perceiver.enabled())

    def test_monitor_release_preserves_household_off_and_original_receipts(self):
        loop = self.loop()
        loop.stop('monitor_backoff')
        self.grant['on'] = False
        self.advance(2, frames=False)
        loop.resume('monitor_backoff')
        self.assertFalse(self.perceiver.enabled(), 'resource recovery cannot enable the household switch')
        self.assertTrue(self.log.writable())

    def test_pending_request_is_retained_without_reexecution_during_backoff(self):
        loop = self.loop()
        pending = {'request': 'same-original'}
        self.perceiver.pump.outstanding = pending
        loop.stop('monitor_backoff')
        with self.assertLogs(LOG, level='WARNING'):
            self.perceiver.pump.tick()
        self.assertIs(self.perceiver.pump.outstanding, pending)
        self.assertEqual(self.transport.envelopes, [])

    def test_attached_incumbent_cannot_be_replaced_or_invalid_cadence_applied(self):
        loop = self.loop()
        with self.assertRaises(ValueError):
            perceiver_module.attach(loop.deep.runtime, SimpleNamespace())
        for factor in (0, 3, True, 2.):
            with self.assertRaises(ValueError):
                self.perceiver.set_cadence(factor)

    def test_monitor_stop_during_a_household_file_read_cannot_reopen_admission(self):
        self.loop()
        def grant():
            self.perceiver.set_monitor_backoff(True)
            return True
        self.perceiver.granted = grant
        self.assertFalse(self.perceiver.enabled())

    def test_reduced_cadence_changes_the_real_pump_scheduler(self):
        loop = self.loop()
        original = self.perceiver.pump.config['interval_seconds']
        loop.set_cadence('reduced')
        waits = []
        def wait(stop, wake, delay, woken):
            waits.append(delay)
            return len(waits) == 2
        with patch.object(perceiver_module.occupancy_gate, 'wait', side_effect=wait), \
                patch.object(self.perceiver.pump, 'tick') as tick:
            self.perceiver.pump._run()
        self.assertEqual(waits, [original * 2, original * 2])
        tick.assert_called_once_with()


class PerceiverTests(PerceiverFixture, unittest.TestCase):
    def test_workload_status_counts_actual_completed_receipts_without_starting_work(self):
        from activity_loop_runtime import ManagedDeepStack
        self.build(completed())
        self.running()
        runtime = SimpleNamespace(close=lambda **_: {'drained': True})
        perceiver_module.attach(runtime, self.perceiver)
        controller = SimpleNamespace(runtime=runtime, store=SimpleNamespace(
            settings=SimpleNamespace(max_session_seconds=300), clock=self.clock))
        deep = ManagedDeepStack(controller, clock=self.clock, release=SimpleNamespace(snapshot=lambda: {}))
        before = deep.snapshot()['qwen_workload']
        self.assertEqual(before['completed'], 0)
        self.assertIsNone(before['model'])
        self.assertEqual(self.transport.envelopes, [])
        self.fill()
        self.perceiver.pump.tick()
        after = deep.snapshot()['qwen_workload']
        self.assertEqual(after['producer_epoch'], before['producer_epoch'])
        self.assertEqual(after['completed'], 1)
        self.assertEqual(after['model'], dict(PROFILE, engine_epoch='la-epoch'))
        self.assertEqual(set(after), {'schema', 'producer_epoch', 'state', 'blocked', 'completed', 'model'})
        self.assertEqual(len(self.transport.envelopes), 1)

    def test_workload_does_not_count_rejected_or_unproven_model_results(self):
        def wrong_model(envelope):
            result = completed()(envelope)
            result['artifact_sha256'] = 'b' * 64
            return result
        self.build(wrong_model)
        self.running()
        self.fill()
        self.perceiver.pump.tick()
        self.assertEqual(self.perceiver.workload_status()['completed'], 0)
        self.assertIsNone(self.perceiver.workload_status()['model'])

    def test_workload_counts_completed_invalid_answers_without_granting_reading_authority(self):
        self.build(completed(text='not JSON'))
        self.running()
        self.fill()
        self.perceiver.pump.tick()
        self.assertEqual(self.perceiver.workload_status()['completed'], 1)
        self.assertEqual(self.perceiver.structured_context()['freshness'], 'invalid')

    def test_workload_does_not_count_la_rejections_as_executed_inference(self):
        self.build(terminal('rejected', 'thermal_shed'))
        self.running()
        self.fill()
        self.perceiver.pump.tick()
        self.assertEqual(self.perceiver.workload_status()['completed'], 0)

    def test_workload_can_count_late_execution_without_extending_its_evidence(self):
        def late(envelope):
            self.clock.now += scene.MAX_AGE_SECONDS + 1
            return completed()(envelope)
        self.build(late)
        self.running()
        self.fill()
        self.perceiver.pump.tick()
        self.assertEqual(self.perceiver.workload_status()['completed'], 1)
        context = self.perceiver.structured_context()
        self.assertEqual(context['freshness'], 'stale')
        self.assertLess(context['latest']['receipt']['valid_until'], self.clock.now)

    def test_occupied_sends_the_ring_frames_and_writes_one_fact_line(self):
        self.build(completed())
        self.running()
        self.fill()
        self.assertEqual(self.perceiver.pump.tick(), {"state": "current", "reason": "recorded_valid"})
        [envelope] = self.transport.envelopes
        day = day_session("victoria", "den", T0)
        self.assertEqual((envelope["session_id"], envelope["site_id"]), (day, "victoria"))
        media = [f["media_id"] for f in self.ring.select({}, self.clock.now)]
        self.assertEqual(envelope["request_id"], scene.digest({"session_id": day, "media_ids": media,
                                                               "body_sha256": envelope["body_sha256"],
                                                               "prompt_version": scene.PROMPT_VERSION}))
        [line] = self.lines()
        self.assertEqual((line["request_id"], line["session_id"], line["outcome"]), (envelope["request_id"], day, "completed"))
        self.assertEqual(line["observation"]["facts"]["activity_hypothesis"], "reading")
        self.assertEqual(self.reader.started, 0)  # no thread was started by hand-driven ticks

    def test_the_kill_switch_off_captures_and_sends_nothing(self):
        self.build(completed(), granted=False)
        self.running()
        self.fill()
        with self.assertLogs(LOG, "WARNING") as logged:
            for _ in range(3):
                self.assertEqual(self.perceiver.pump.tick(), {"state": "disabled", "reason": "disabled"})
                self.advance(4)
        self.assertEqual(logged.output, ["WARNING:%s:la-live-perceiver: household kill switch %s is off; nothing is "
                                         "captured or sent" % (LOG, ENTRY)])  # once, at the transition
        self.assertEqual((self.transport.envelopes, self.transport.references), ([], []))  # not even the identity probe
        self.assertFalse(self.perceiver.capture.is_open())
        self.assertFalse(self.perceiver.offer_preview(*self.frames(self.clock.now - .2)))
        self.assertEqual(self.lines(), [])
        self.grant["on"] = True
        self.advance(4)
        with self.assertLogs(LOG, "WARNING"):
            self.assertEqual(self.perceiver.pump.tick()["reason"], "recorded_valid")

    def test_no_capture_and_no_la_call_while_vacant_or_unknown(self):
        self.build(completed(), state="vacant")
        self.running()
        self.fill()
        for state, reason in (("vacant", "no_person"), ("unknown", "occupancy_unknown")):
            self.reader.state = state
            self.assertEqual(self.perceiver.pump.tick(), {"state": "skipped", "reason": reason})
            self.assertFalse(self.perceiver.capture.is_open())
        self.assertEqual((self.transport.envelopes, self.transport.references), ([], []))
        before = self.ring.snapshot()["counts"]["accepted"]
        self.assertFalse(self.perceiver.offer_preview(*self.frames(self.clock.now - .2)))  # a recording's preview too
        self.assertEqual(self.ring.snapshot()["counts"]["accepted"], before)

    def test_an_indeterminate_row_pauses_with_a_60_s_identity_reprobe_until_unknown_resolved(self):
        self.build(CompletionUnproven("lost"), completed(),
                   lookups=[terminal("indeterminate"), terminal("indeterminate"), terminal("failed", "unknown_resolved")])
        self.running()
        self.fill()
        pump = self.perceiver.pump
        self.assertEqual(pump.indeterminate_seconds, 60.0)
        self.assertIsNone(qwen_live.QwenLivePump.__init__.__kwdefaults__["indeterminate_seconds"])  # the Lab pump halts
        self.assertEqual(pump.tick()["reason"], "outcome_unproven")
        self.advance(4)
        with self.assertLogs(LOG, "WARNING") as logged:
            self.assertEqual(pump.tick(), {"state": "awaiting_terminal", "reason": "la_indeterminate"})
        self.assertEqual(logged.output, ["WARNING:%s:la-live-perceiver paused: LA reports the outstanding request "
                                         "indeterminate; identity lookup every 60s, never resubmitted" % LOG])
        self.assertFalse(self.perceiver.capture.is_open())  # paused: no frames are taken for nothing
        lookups = len(self.transport.references)
        for _ in range(14):  # 56 s: still inside the 60 s re-probe interval (the Lab pump would look up at 30 s)
            self.advance(4)
            self.assertEqual(pump.tick()["reason"], "la_indeterminate_backoff")
        self.assertEqual((len(self.transport.references), len(self.transport.envelopes)), (lookups, 1))
        self.advance(4)
        self.assertEqual(pump.tick()["reason"], "la_indeterminate")  # re-probed at 60 s: still open on LA
        self.assertEqual(len(self.transport.references), lookups + 1)
        self.advance(60)
        with self.assertLogs(LOG, "WARNING") as logged:
            self.assertEqual(pump.tick(), {"state": "skipped", "reason": "la_unknown_resolved"})
        self.assertIn("la-live-perceiver resumed", logged.output[0])
        self.assertEqual((pump.outstanding, pump.snapshot()["halted"]), (None, None))
        self.assertTrue(self.perceiver.capture.is_open())
        self.advance(4)
        self.assertEqual(pump.tick()["reason"], "recorded_valid")  # the pump resumes
        self.assertEqual(len(self.transport.envelopes), 2)
        outcomes = [line["outcome"] for line in self.lines()]
        self.assertEqual(outcomes, ["unknown_resolved", "completed"])
        self.assertIsNone(self.lines()[0]["observation"])

    def test_the_day_session_moves_only_between_requests(self):
        midnight = datetime(2026, 10, 9, tzinfo=timezone.utc).timestamp()
        self.build(CompletionUnproven("lost"), completed(), lookups=[completed()], clock=Clock(midnight - 6))
        self.running()
        self.fill()
        pump = self.perceiver.pump
        before = day_session("victoria", "den", midnight - 1)
        self.assertEqual(pump.tick()["reason"], "outcome_unproven")
        self.advance(8)  # past midnight, the request still outstanding
        self.assertEqual(pump.tick()["reason"], "recorded_valid")  # resolved after midnight
        self.assertEqual(self.transport.references[-1]["session_id"], before)  # looked up under its own day's id
        self.advance(4)
        self.assertEqual(pump.tick()["reason"], "recorded_valid")
        self.assertEqual(self.transport.envelopes[-1]["session_id"], day_session("victoria", "den", midnight + 1))
        self.assertEqual([line["session_id"] for line in self.lines()],
                         [before, day_session("victoria", "den", midnight + 1)])

    def test_frames_are_taken_only_while_a_send_could_use_them(self):
        from qwen_transport import QwenNotExecuted
        self.build(completed(), QwenNotExecuted("thermal_shed"), completed(), QwenNotExecuted("transport_not_connected"),
                   completed(), CompletionUnproven("lost"), completed(), lookups=[completed()])
        self.running()
        self.fill()
        pump, capture = self.perceiver.pump, self.perceiver.capture
        self.assertFalse(capture.is_open())  # LA's engine epoch is not known yet: the probe comes first
        self.assertFalse(capture.wake.is_set())
        self.assertEqual(pump.tick()["reason"], "recorded_valid")
        self.assertTrue(capture.is_open())
        self.assertTrue(capture.wake.is_set())  # poked: the ring looks again at once
        capture.wake.clear()
        self.advance(4)
        self.assertEqual(pump.tick()["reason"], "thermal_shed")  # 30 s send backoff
        self.assertFalse(capture.is_open())
        self.advance(24)  # 6 s left, more than one window (3 s span plus a 1.5 s step)
        self.assertFalse(capture.is_open())
        self.advance(2)
        self.assertTrue(capture.is_open())  # frames for the first send after the backoff
        self.advance(4)
        self.assertEqual(pump.tick()["reason"], "recorded_valid")
        self.advance(4)
        probes = len(self.transport.references)
        self.assertEqual(pump.tick()["reason"], "transport_not_connected")  # LA unreachable
        self.assertFalse(capture.is_open())
        self.advance(4)
        self.assertEqual(pump.tick()["reason"], "recorded_valid")  # the epoch is probed again, then frames
        self.assertEqual(len(self.transport.references), probes + 1)
        self.advance(4)
        self.assertEqual(pump.tick()["reason"], "outcome_unproven")
        self.assertFalse(capture.is_open())  # nothing is sent until the outcome is known
        self.advance(4)
        self.assertEqual(pump.tick()["reason"], "recorded_valid")  # the lookup resolved it
        self.assertTrue(capture.is_open())
        self.assertTrue(self.perceiver.offer_preview(*self.frames(self.clock.now - .2)))

    def test_the_rate_cap_stops_the_capture_until_its_window(self):
        self.build(completed(), completed(), block={"max_per_minute": 1})
        self.running()
        self.fill()
        pump, capture = self.perceiver.pump, self.perceiver.capture
        self.assertEqual(pump.tick()["reason"], "recorded_valid")
        self.assertFalse(capture.is_open())  # the next send is a minute away
        self.advance(52)
        self.assertFalse(capture.is_open())
        self.advance(4)
        self.assertTrue(capture.is_open())
        self.advance(4)
        self.assertEqual(pump.tick()["reason"], "recorded_valid")

    def test_a_fact_log_that_cannot_be_written_stops_sending_and_capturing_and_logs_once_a_day(self):
        import errno
        self.build(*[completed()] * 8)
        self.running()
        self.fill()
        pump, capture = self.perceiver.pump, self.perceiver.capture
        self.assertEqual(pump.tick()["reason"], "recorded_valid")
        broken = {"on": True}
        rotate = self.log._rotate

        def read_only(day):
            if broken["on"]:
                raise OSError(errno.EROFS, "Read-only file system")
            return rotate(day)
        self.log._close_file()
        self.log._rotate = read_only
        with self.assertLogs(LOG, "WARNING") as logged:
            for _ in range(la_fact_log.MAX_WRITE_FAILURES):
                self.advance(4)
                self.assertEqual(pump.tick()["reason"], "record_failed")
        self.assertEqual(len(logged.output), 1)  # the first record_failed of the day only
        self.assertIn("could not be written to la-fact-log-1 (EROFS)", logged.output[0])
        self.assertEqual(self.log.snapshot()["last_failure"], "EROFS")
        sent = len(self.transport.envelopes)
        self.assertFalse(capture.is_open())
        for _ in range(10):  # 40 s: nothing captured and nothing sent
            self.advance(4)
            self.assertEqual(pump.tick()["reason"], "fact_log_unwritable")
        self.assertEqual(len(self.transport.envelopes), sent)
        self.advance(20)  # a minute after the last failure: one more request may try
        self.assertTrue(capture.is_open())
        broken["on"] = False
        self.assertEqual(pump.tick()["reason"], "recorded_valid")  # written again; the counter resets
        self.assertEqual(self.log.snapshot()["failures_in_a_row"], 0)
        self.assertEqual(self.lines()[-1]["outcome"], "completed")

    def test_a_record_failure_logs_once_per_utc_day(self):
        midnight = datetime(2026, 10, 9, tzinfo=timezone.utc).timestamp()
        self.build(*[completed()] * 4, clock=Clock(midnight - 9))
        self.running()
        self.fill()
        pump = self.perceiver.pump
        with patch("la_fact_log.line", side_effect=ValueError("receipt exceeds bound")):
            with self.assertLogs(LOG, "WARNING") as logged:
                self.assertEqual(pump.tick()["reason"], "record_failed")
                self.advance(4)
                self.assertEqual(pump.tick()["reason"], "record_failed")  # same day: counted only
                self.advance(6)  # past UTC midnight
                self.assertEqual(pump.tick()["reason"], "record_failed")
        self.assertEqual(len(logged.output), 2)
        self.assertIn("could not be written to la-fact-log-1 (record_failed)", logged.output[0])
        self.assertTrue(self.perceiver.fact_log.writable())  # not a write failure: the log stays writable

    def test_the_perceiver_refuses_to_start_while_the_lab_pump_is_enabled(self):
        self.build(lab=True)
        with self.assertLogs(LOG, "WARNING") as logged:
            self.assertFalse(self.perceiver.start())
        self.assertIn("la-live-perceiver refused: the Lab qwen_live block is enabled", logged.output[0])
        self.assertFalse(self.perceiver.running())
        self.assertEqual(self.reader.started, 0)
        self.assertFalse(self.perceiver.pump.thread.is_alive())
        self.assertFalse(self.perceiver.capture.thread.is_alive())
        self.assertFalse(self.perceiver.start())  # stays refused
        self.perceiver.stop(1)
        self.assertTrue(self.transport.closed)


class RuntimeExclusionTests(unittest.TestCase):
    """build_runtime: no recording pump while the perceiver runs; its ring is
    tapped from the recording's previews."""

    def setUp(self):
        import test_paired_runtime as fixtures
        fixtures.PairedRuntimeTests.setUp(self)
        self.addCleanup(fixtures.PairedRuntimeTests.tearDown, self)
        self.runtimes = []

    def runtime(self, la_live):
        from live_detector_app import build_runtime
        from test_ha_provisioning import live_config
        block = {"transport_file": "/private/qwen-live.json", "artifact_sha256": "a" * 64,
                 "runtime_revision": "vllm-0.11.0", "enabled": True}
        reasoning = SimpleNamespace(qwen_granted=lambda: True)
        # The recording pump's transport file "reads" (no trust files are opened here).
        with patch("live_detector_app.private_json", return_value=dict(TRANSPORT_CONFIG)), \
                patch("qwen_transport.load_config", lambda value, check_files=True: dict(value)):
            runtime = build_runtime(self.store, SimpleNamespace(camera_id="den"), live_config(qwen_live=block),
                                    transport_factory=lambda *_: (lambda *a: None), lease=SimpleNamespace(ready=lambda: False),
                                    reasoning_factory=reasoning, la_live=la_live)
        self.addCleanup(runtime.close)
        return runtime

    def test_no_lab_pump_while_the_perceiver_runs_and_its_ring_is_tapped(self):
        running = SimpleNamespace(running=lambda: True, offer_preview=lambda data, header: True)
        with self.assertLogs(LOG, "WARNING") as logged:
            runtime = self.runtime(running)
        self.assertIsNone(runtime.qwen_live)
        self.assertIn("lab qwen_live refused for this recording: the la-live-perceiver is running", logged.output[0])
        self.assertIs(runtime.preview_taps["den"], running.offer_preview)
        idle = SimpleNamespace(running=lambda: False, offer_preview=lambda data, header: True)
        runtime = self.runtime(idle)
        self.assertIsNotNone(runtime.qwen_live)  # a refused or stopped perceiver changes nothing
        self.assertEqual(runtime.preview_taps, {})
        self.assertIsNotNone(self.runtime(None).qwen_live)

    def test_a_tap_never_affects_the_recording(self):
        from service_runtime import ServiceRuntime
        calls = []
        host = SimpleNamespace(ingest_preview=lambda sid, data, header: calls.append(("ingest", data)) or {"media_ids": ["m"]})

        def tap(data, header):
            calls.append(("tap", data))
            raise RuntimeError("ring full")
        self.assertEqual(ServiceRuntime._ingest_and_tap(host, "s", b"jpeg", "h", tap), {"media_ids": ["m"]})
        self.assertEqual(calls, [("ingest", b"jpeg"), ("tap", b"jpeg")])
        host.ingest_preview = lambda *a: (_ for _ in ()).throw(ValueError("preview stale"))
        calls.clear()
        with self.assertRaisesRegex(ValueError, "preview stale"):
            ServiceRuntime._ingest_and_tap(host, "s", b"jpeg", "h", tap)
        self.assertEqual(calls, [])  # a capture the recording refused is never offered
        with self.assertRaises(ValueError):
            ServiceRuntime(self.store, {}, None, None, preview_taps={"den": "not callable"})


class CompositionTests(PerceiverFixture, unittest.TestCase):
    def provisioned(self, ha=None, transport=None):
        """private_json by path: the transport file and the HA source file."""
        from test_ha_provisioning import ha_file
        files = {BLOCK["transport_file"]: transport or dict(TRANSPORT_CONFIG), "/private/ha.json": ha or ha_file()}
        return patch("live_detector_app.private_json", side_effect=lambda path: deepcopy(files[path]))

    def test_build_reads_provisioning_without_starting_or_connecting(self):
        from test_ha_provisioning import live_config
        made = []

        def transport_factory(config):
            made.append(config)
            return FakeTransport()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        config = live_config(la_live_perceiver=BLOCK, occupancy_reader=READER, ha_source_file="/private/ha.json")
        kwargs = dict(site_id="victoria", camera_id="den", policy_file="/private/policy.json",
                      transport_factory=transport_factory, fetch=lambda: None, private=False)
        logs = []
        # The block's POSIX path stands for a private directory; here a temp one.
        with self.provisioned(), \
                patch("la_fact_log.FactLog", lambda path, **kw: logs.append(path) or FactLog(directory.name, **kw)):
            self.assertIsNone(perceiver_module.build({**config, "la_live_perceiver": {**BLOCK, "enabled": False}}, **kwargs))
            built = perceiver_module.build(config, **kwargs)
            self.addCleanup(built.stop, 1)
            self.assertEqual(logs, [BLOCK["fact_log_directory"]])
            self.assertFalse(built.running())  # nothing starts until the controller attaches it
            self.assertEqual(len(made), 1)
            self.assertEqual(built.pump.base_profile["artifact_sha256"], "a" * 64)
            self.assertEqual(built.pump.indeterminate_seconds, 60.0)
            self.assertFalse(built.lab_qwen_enabled)
            today = datetime.fromtimestamp(time.time(), timezone.utc).strftime("%Y-%m-%d")
            self.assertEqual(os.listdir(directory.name), ["la-fact-log-1-%s.jsonl" % today])  # opened at build
            lab = perceiver_module.build({**config, "qwen_live": {"enabled": True}}, **kwargs)
            self.addCleanup(lab.stop, 1)
            self.assertTrue(lab.lab_qwen_enabled)
            with self.assertRaisesRegex(ValueError, "occupancy_reader"):
                perceiver_module.build({k: v for k, v in config.items() if k != "occupancy_reader"}, **kwargs)
            with self.assertRaisesRegex(ValueError, "household policy file"):
                perceiver_module.build(config, **{**kwargs, "policy_file": None})
            default = perceiver_module.build(config, **{k: v for k, v in kwargs.items() if k != "fetch"})
            self.addCleanup(default.stop, 1)
            self.assertIsInstance(default.capture.fetch, frame_ring.RingPreviewClient)  # one attempt per ring capture
        with self.provisioned(transport={**TRANSPORT_CONFIG, "deadline_seconds": 12}):
            with self.assertRaisesRegex(ValueError, "5-8 seconds"):
                perceiver_module.build(config, **kwargs)

    def test_build_requires_the_camera_derived_occupancy_entity(self):
        from test_ha_provisioning import OCCUPANCY, ha_file, live_config
        other = "binary_sensor.den_desk_motion"
        config = live_config(la_live_perceiver=BLOCK, occupancy_reader=READER, ha_source_file="/private/ha.json")
        kwargs = dict(site_id="victoria", camera_id="den", policy_file="/private/policy.json",
                      transport_factory=lambda config: FakeTransport(), fetch=lambda: None, private=False)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        refused = ((ha_file(camera_derived_entities=[]), None),  # no camera-derived entity
                   (ha_file(entities=[OCCUPANCY, other], camera_derived_entities=[other]), None),  # another sensor
                   (ha_file(entities=[OCCUPANCY, other], camera_derived_entities=[OCCUPANCY, other]),
                    {other: "person_present"}))  # person_present is bound to another one
        with patch("la_fact_log.FactLog", lambda path, **kw: FactLog(directory.name, **kw)):
            for ha, bindings in refused:
                with self.subTest(ha=ha, bindings=bindings), self.provisioned(ha=ha), \
                        self.assertRaisesRegex(ValueError, "camera-derived person-occupancy entity"):
                    perceiver_module.build(config, ha_bindings=bindings, **kwargs)
            with self.assertRaisesRegex(ValueError, "HA source file"):
                perceiver_module.build({k: v for k, v in config.items() if k != "ha_source_file"}, **kwargs)
            for ha, bindings in ((ha_file(), None), (ha_file(), {OCCUPANCY: "person_present"}),
                                 (ha_file(entities=[OCCUPANCY, other], camera_derived_entities=[OCCUPANCY, other]),
                                  {OCCUPANCY: "person_present"})):
                with self.subTest(ha=ha, bindings=bindings), self.provisioned(ha=ha):
                    built = perceiver_module.build(config, ha_bindings=bindings, **kwargs)
                    self.addCleanup(built.stop, 1)
                    self.assertEqual(built.reader.entity, OCCUPANCY)

    def test_build_refuses_a_fact_log_directory_it_cannot_write(self):
        import errno
        from types import SimpleNamespace as Namespace
        from test_ha_provisioning import live_config
        config = live_config(la_live_perceiver=BLOCK, occupancy_reader=READER, ha_source_file="/private/ha.json")
        kwargs = dict(site_id="victoria", camera_id="den", policy_file="/private/policy.json",
                      transport_factory=lambda config: FakeTransport(), fetch=lambda: None, private=False)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        with self.provisioned(), patch("la_fact_log.FactLog", lambda path, **kw: FactLog(directory.name, **kw)):
            # A read-only mount (ProtectSystem=strict outside ReadWritePaths) ...
            with patch("la_fact_log.os.statvfs", create=True, return_value=Namespace(f_flag=getattr(os, "ST_RDONLY", 1))), \
                    self.assertRaisesRegex(ValueError, r"writable fact log directory required \(EROFS\).*ReadWritePaths"):
                perceiver_module.build(config, **kwargs)
            # ... or a directory the day file cannot be opened in.
            with patch("la_fact_log.os.open", side_effect=PermissionError(errno.EACCES, "Permission denied")), \
                    self.assertRaisesRegex(ValueError, r"writable fact log directory required \(EACCES\)"):
                perceiver_module.build(config, **kwargs)
        self.assertEqual(os.listdir(directory.name), [])

    def test_attach_starts_with_the_controller_and_stops_before_it_closes(self):
        self.build(*[completed()] * 4, state="vacant")
        order = []
        pair = SimpleNamespace(close=lambda timeout=10: order.append(("pair", self.perceiver.running())) or {"drained": True})
        with self.assertLogs(LOG, "WARNING") as logged:
            perceiver_module.attach(pair, self.perceiver)
            self.assertTrue(self.perceiver.running())
            self.assertEqual(self.reader.started, 1)
            self.assertTrue(self.perceiver.pump.thread.is_alive() and self.perceiver.capture.thread.is_alive())
            self.assertEqual(pair.close(timeout=3), {"drained": True})
        self.assertEqual(order, [("pair", False)])  # the perceiver stopped first
        self.assertFalse(self.perceiver.pump.thread.is_alive() or self.perceiver.capture.thread.is_alive())
        self.assertEqual(self.reader.stopped, 1)
        self.assertTrue(self.transport.closed)
        self.assertIn("la-live-perceiver started", logged.output[0])
        self.assertIn("la-live-perceiver stopped", logged.output[-1])
        with self.assertRaisesRegex(ValueError, "fact log closed"):
            self.log.terminal({}, "failed", None)  # the fact log is closed with it

    def test_the_managed_controller_builds_it_at_startup_from_the_household_policy_file(self):
        import managed_detector_app
        from test_ha_provisioning import live_config
        actual_perceiver = self.build(state="vacant")
        provision = {"schema_version": 1, "live_config": "/private/live.json", "jev_config": "/private/jev.json",
                     "lifecycle": {"a": {"kind": "jetson_local", "release": "/release"},
                                   "b": {"origin": "https://192.168.0.100:8792", "token_file": "/private/b.token",
                                         "ca_file": "/private/b.pem", "site_id": "echo"}}}
        files = {"/managed.json": provision, "/private/jev.json": {"policy_file": "/private/household-policy.json"},
                 "/private/live.json": live_config(la_live_perceiver=BLOCK, occupancy_reader=READER)}
        settings = SimpleNamespace(site_id="victoria", max_session_seconds=60)
        built, created = [], []

        def start(provision_file, reasoning):
            with patch.dict("os.environ", {"PERCEPTION_LAB_MANAGED_CONFIG": "/managed.json"}), \
                    patch("managed_detector_app.private_json", side_effect=lambda p: deepcopy(provision_file if p == "/managed.json" else files[p])), \
                    patch("capture_app.load_provisioning", return_value=(settings, SimpleNamespace(camera_id="den"), None, None, None)), \
                    patch("session_budget.provisioned_budget", return_value={"session_seconds": 60}), \
                    patch("jetson_lifecycle.JetsonLifecycleClient"), patch("worker_lifecycle_client.LifecycleClient"), \
                    patch("managed_jev.reasoning_factory", return_value=reasoning), \
                    patch("la_live_perceiver.build", side_effect=lambda config, **kw: built.append(kw) or actual_perceiver), \
                    patch("live_video.LiveVideo"), patch("live_planes.LivePlanes"), \
                    patch("app.create_app", side_effect=lambda *a, **k: created.append(k) or "app"), \
                    patch("structured_camera_routes.install_structured_camera_context") as install:
                # Ordinary viewing has no protected Den-session mount configured.
                os.environ.pop("PERCEPTION_LAB_DEN_SESSION_CONFIG", None)
                result = managed_detector_app.create_managed_detector_app()
                install.assert_called_once_with("app", source=actual_perceiver)
                self.assertIs(install.call_args.kwargs['source'], actual_perceiver)
                return result
        reasoning = SimpleNamespace(ha_bindings={}, readiness=lambda: {})
        self.assertEqual(start(provision, reasoning), "app")
        self.assertEqual(built, [{"site_id": "victoria", "camera_id": "den", "policy_file": "/private/household-policy.json",
                                  "ha_bindings": {}}])  # the JEV bindings, for the occupancy entity check
        without_jev = {k: v for k, v in provision.items() if k != "jev_config"}
        with self.assertRaisesRegex(ValueError, "requires the household policy"):
            start(without_jev, None)
        self.assertEqual((len(built), len(created)), (1, 1))  # nothing built or composed without it

    def test_the_managed_runtime_owns_the_perceiver_for_its_lifetime(self):
        from managed_detector_app import create_managed_runtime
        from test_managed_readiness import Host
        events = []
        la_live = SimpleNamespace(start=lambda: events.append("start") or True, stop=lambda timeout=None: events.append("stop"),
                                  running=lambda: True)
        runtime = create_managed_runtime(None, None, {}, {"a": Host(), "b": Host()}, la_live=la_live)
        self.assertEqual(events, ["start"])
        runtime.close(timeout=1)
        self.assertEqual(events, ["start", "stop"])
        plain = create_managed_runtime(None, None, {}, {"a": Host(), "b": Host()})
        self.assertNotIn("closing", getattr(plain.close, "__qualname__", ""))


class EdgeWakeTests(PerceiverFixture, unittest.TestCase):
    """Real threads and clocks: vacant -> on edge -> ring capture -> first send."""

    def test_an_occupancy_edge_reaches_the_first_send_well_inside_one_interval(self):
        frames, fetched, sent = Frames(), [], []

        def fetch():
            fetched.append(time.monotonic())
            return frames(time.time() - .05)

        class Timed(FakeTransport):
            def __call__(self, envelope):
                sent.append(time.monotonic())
                return super().__call__(envelope)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        reader, transport = Reader("vacant"), Timed(*[completed()] * 8)
        perceiver = LaLivePerceiver(
            transport=transport, profile=dict(PROFILE), reader=reader, fact_log=FactLog(directory.name, private=False),
            granted=lambda: True, fetch=fetch, settings=check_config(BLOCK), site_id="victoria", camera_id="den",
            local_time=lambda epoch: (19, 40), ring=FrameRing(contract=ACCEPT), turns=CaptureTurns(threading.Lock()))
        self.addCleanup(perceiver.stop, 2)
        with self.assertLogs(LOG, "WARNING"):
            self.assertTrue(perceiver.start())
            time.sleep(.6)
            self.assertEqual((fetched, sent, transport.references), ([], [], []))  # vacant: no capture, no LA call
            edge = time.monotonic()
            reader.edge("occupied")
            deadline = edge + 3
            while not sent and time.monotonic() < deadline:
                time.sleep(.01)
            perceiver.stop(2)
        self.assertTrue(sent, "no send after the edge")
        self.assertLess(fetched[0] - edge, .3)  # the edge wakes the ring capture at once
        # The first window needs frames about 0.75 s apart (3 frames over 3 s), then the
        # send follows the frame that completes it: well inside the 4 s interval.
        self.assertLess(sent[0] - edge, 2.0)
        self.assertEqual(len(sent), 1)
        gaps = [b - a for a, b in zip(fetched, fetched[1:])]
        self.assertTrue(all(gap >= .49 for gap in gaps), gaps)  # never above 2 Hz


if __name__ == "__main__":
    unittest.main()
