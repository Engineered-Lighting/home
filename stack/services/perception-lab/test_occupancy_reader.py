"""Victoria-LAN occupancy reader (plan S1.2): one entity polled at 2 Hz and kept
in memory with the gate's freshness, hold, grace and fail-closed rules, edge-only
logging, and the pumps' occupancy-edge wake. Scripted HA client and transports;
no network."""
import logging
import threading
import time
import unittest

import occupancy_gate as gate
import occupancy_reader as reader_module
import qwen_live
import vjepa_live
from occupancy_reader import OccupancyReader
from test_occupancy_gate import HAFixture, NoFrames, OCC
from test_qwen_live import PROFILE as QWEN_PROFILE, Clock, FakeTransport as QwenTransport, StoreFixture, completed
from test_vjepa_live import PROFILE as VJEPA_PROFILE, FakeTransport as VjepaTransport

LOG = "perception_lab.occupancy"


class ScriptedHA:
    """HAStateClient stand-in: the next state ('on', 'off', 'unavailable', None
    for a missing entity, or an Exception) observed `age` seconds before now."""
    def __init__(self, clock, state="off", *, age=.05):
        self.clock, self.state, self.age, self.calls = clock, state, age, 0

    def __call__(self):
        self.calls += 1
        if isinstance(self.state, Exception):
            raise self.state
        states = {} if self.state is None else {OCC: self.state}
        return {"snapshot_id": "s%d" % self.calls, "observed_at": self.clock() - self.age, "states": states,
                "acquisition_duration_s": .01}, None


class ReaderFixture:
    def build_reader(self, state="off", **kwargs):
        self.ha = ScriptedHA(self.clock, state)
        self.reader = OccupancyReader(self.ha, OCC, clock=self.clock, **kwargs)
        return self.reader

    def poll(self, state=None, *, advance=0):
        """One poll of the reader's own pump: True read, False failed."""
        self.clock.now += advance
        if state is not None:
            self.ha.state = state
        return self.reader.pump._attempt()


class ReaderTests(ReaderFixture, unittest.TestCase):
    def setUp(self):
        self.clock = Clock(1000.0)
        self.build_reader()

    def test_on_is_occupied_off_is_vacant_and_anything_else_fails_closed(self):
        reader = self.reader
        self.assertEqual(reader(), {"state": "unknown", "reason": "occupancy_unavailable", "observed_at": None})
        self.assertEqual(reader.state(), "unknown")
        self.assertTrue(self.poll("on"))
        self.assertEqual(reader(), {"state": "occupied", "reason": "person_detected", "observed_at": 999.95})
        self.assertEqual(reader.state(), "on")
        self.poll("off", advance=11)  # past the hold after that 'on'
        self.assertEqual((reader()["state"], reader.state()), ("vacant", "off"))
        self.poll("unavailable", advance=1)
        self.assertEqual(reader(), {"state": "unknown", "reason": "occupancy_state_unknown", "observed_at": 1011.95})
        self.poll(None, advance=1)  # the entity missing from the snapshot is unknown too
        self.assertEqual(reader()["reason"], "occupancy_state_unknown")

    def test_a_reading_is_fresh_for_its_ttl_and_grace_then_stale(self):
        self.poll("on")  # observed 999.95: valid until 1002.95, plus 5 s grace
        self.ha.state = ValueError("ha_snapshot_unavailable")
        for _ in range(16):  # failed polls keep nothing and refresh nothing
            self.assertFalse(self.poll(advance=.5))
        self.clock.now = 1007.95
        self.assertEqual(self.reader()["state"], "occupied")
        self.clock.now = 1008.0
        self.assertEqual(self.reader(), {"state": "unknown", "reason": "occupancy_stale", "observed_at": 999.95})
        self.assertEqual(self.reader.pump.counters()["failures"], {"fetch:ha_snapshot_unavailable": 16})
        strict = self.build_reader(grace_seconds=0)
        self.poll("on")
        self.clock.now += 3.06
        self.assertEqual(strict()["reason"], "occupancy_stale")

    def test_unusable_snapshots_are_refused_and_never_replace_the_reading(self):
        self.poll("on")
        before = self.reader()
        for age in (-1, 3.5):  # from the future, or older than its own ttl on arrival
            self.ha.age = age
            self.assertFalse(self.poll("off"))
        self.ha.age = .05
        self.assertFalse(self.poll(7))  # not a scalar state
        self.assertEqual(self.reader(), before)
        self.assertEqual(sum(self.reader.pump.counters()["failures"].values()), 3)

    def test_a_reading_from_the_future_and_a_stopped_reader_are_unknown(self):
        self.poll("on")
        self.assertEqual(self.reader(now=999.0)["reason"], "occupancy_unavailable")  # a clock step back
        self.reader.stop()
        self.assertEqual(self.reader(), {"state": "unknown", "reason": "occupancy_reader_stopped", "observed_at": None})

    def test_the_hold_keeps_a_brief_flicker_off_occupied_then_stops(self):
        reader = self.build_reader(hold_seconds=10)
        self.poll("on")  # observed 999.95
        for step in range(1, 10):
            self.poll("off", advance=1)
            self.assertEqual(reader(), {"state": "occupied", "reason": "occupancy_hold", "observed_at": 999.95 + step})
            self.assertEqual(reader.state(), "on")
        self.clock.now = 1009.95
        self.assertEqual(reader()["reason"], "occupancy_hold")  # exactly hold_seconds after the 'on'
        self.clock.now = 1010.0
        self.poll("off")
        self.assertEqual((reader()["state"], reader.state()), ("vacant", "off"))
        self.poll("on")
        self.assertEqual(reader()["state"], "occupied")

    def test_the_hold_never_bridges_an_unknown_or_stale_reading_or_a_clock_step_back(self):
        reader = self.build_reader(hold_seconds=15)
        self.poll("on")
        self.poll("unavailable", advance=1)
        self.assertEqual(reader()["state"], "unknown")
        self.clock.now += 9  # the last reading is stale too
        self.assertEqual(reader()["reason"], "occupancy_stale")
        self.poll("on")
        self.poll("off", advance=1)
        self.assertEqual(reader()["reason"], "occupancy_hold")
        self.assertEqual(reader(now=self.clock.now - 3)["reason"], "occupancy_unavailable")  # never the future 'on'
        self.assertEqual(reader()["state"], "vacant")  # that hold was forgotten, not resurrected

    def test_bounds_and_exact_provisioning_without_io(self):
        for hold in (3, 15):
            self.assertEqual(self.build_reader(hold_seconds=hold).hold, hold)
        for bad in ({"hold_seconds": 2.9}, {"hold_seconds": 16}, {"hold_seconds": True}, {"grace_seconds": 11},
                    {"interval_seconds": .4}, {"interval_seconds": float("nan")}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.build_reader(**bad)
        with self.assertRaisesRegex(ValueError, gate.NOT_PROVISIONED):
            OccupancyReader(self.ha, "person.owner")
        block = {"origin": "https://victoria-jetson.example.ts.net", "credential_file": "/nonexistent/ha.token"}
        config = reader_module.check_config(block)
        self.assertEqual({k: config[k] for k in ("entity", "api", "hold_seconds", "grace_seconds", "interval_seconds")},
                         {"entity": "binary_sensor.den_oak_person_occupancy", "api": "core", "hold_seconds": 10.0,
                          "grace_seconds": 5.0, "interval_seconds": .5})
        built = reader_module.from_config({**block, "hold_seconds": 5}, clock=self.clock)
        self.assertEqual((built.entity, built.hold, built.pump.interval, built.client.host, built.started),
                         (OCC, 5, .5, "victoria-jetson.example.ts.net", False))
        self.assertFalse(built.thread.is_alive())  # construction starts nothing and reads no credential
        for bad in ({**block, "origin": "http://192.168.1.60:8123"}, {**block, "credential_file": "ha.token"},
                    {**block, "entity": "sensor.den_person_count"}, {**block, "api": "websocket"},
                    {**block, "expected_hostname": "other.example"}, {**block, "surprise": 1},
                    {"origin": block["origin"]}, None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                reader_module.check_config(bad)

    def test_polls_on_the_two_hertz_preview_grid_and_stops_promptly(self):
        reader = OccupancyReader(ScriptedHA(time.time, "on"), OCC)
        self.assertEqual(reader.pump.interval, .5)
        started = time.monotonic()
        reader.start()
        reader.start()  # idempotent
        while reader.pump.counters()["ok"] < 2 and time.monotonic() - started < 3:
            time.sleep(.02)
        polls = reader.pump.counters()["attempts"]
        elapsed = time.monotonic() - started
        self.assertGreaterEqual(polls, 2)
        self.assertLessEqual(polls, elapsed / .5 + 1)  # never faster than 2 Hz
        self.assertEqual(reader()["state"], "occupied")
        stopping = time.monotonic()
        reader.stop(timeout=2)
        self.assertFalse(reader.thread.is_alive())
        self.assertLess(time.monotonic() - stopping, 1)


class EdgeTests(ReaderFixture, unittest.TestCase):
    def setUp(self):
        self.clock = Clock(1000.0)
        self.build_reader(hold_seconds=3)

    def test_only_edges_are_logged_never_each_poll(self):
        with self.assertLogs(LOG, level="INFO") as logs:
            for state in ("on",) * 5 + ("off",) * 5 + ("unavailable",) * 3 + ("on",) * 4 + ("off",) * 2:
                self.poll(state, advance=.5)
        self.assertEqual([line.split("occupancy edge: ")[1].rsplit(" observed_at=", 1)[0] for line in logs.output],
                         [f"entity={OCC} from=unknown to=on", f"entity={OCC} from=on to=off",
                          f"entity={OCC} from=unknown to=on", f"entity={OCC} from=on to=off"])
        self.assertEqual(self.reader.snapshot()["edges"], {"on": 2, "off": 2})
        with self.assertNoLogs(LOG, level="INFO"):
            for _ in range(10):
                self.poll("off", advance=.5)

    def test_a_stale_on_comes_back_as_an_unknown_to_on_edge(self):
        self.poll("on")
        self.clock.now += 9  # stale
        with self.assertLogs(LOG, level="INFO") as logs:
            self.poll("on")
        self.assertIn("from=unknown to=on", logs.output[0])

    def test_an_on_edge_wakes_every_subscriber_and_off_wakes_none(self):
        first, second = threading.Event(), threading.Event()
        self.reader.subscribe(first)
        self.reader.subscribe(second)
        self.poll("off")
        self.assertFalse(first.is_set())
        self.poll("on", advance=.5)
        self.assertTrue(first.is_set() and second.is_set())
        first.clear(), second.clear()
        self.poll("on", advance=.5)  # still on: no edge
        self.poll("off", advance=.5)  # on -> off: logged, never a wake
        self.assertFalse(first.is_set() or second.is_set())
        self.reader.unsubscribe(second)
        self.poll("on", advance=.5)
        self.assertEqual((first.is_set(), second.is_set()), (True, False))


class WaitTests(unittest.TestCase):
    def wait_in(self, delay, *, wake_after=None, stop_after=None, woken=lambda: True):
        stopped, wake = threading.Event(), threading.Event()
        if wake_after is not None:
            threading.Timer(wake_after, wake.set).start()
        if stop_after is not None:
            # stop() sets both, stopped first: one timer, so the wake can never
            # arrive before the stop it belongs to.
            threading.Timer(stop_after, lambda: (stopped.set(), wake.set())).start()
        started = time.monotonic()
        result = gate.wait(stopped, wake, delay, woken)
        return result, time.monotonic() - started, wake.is_set()

    def test_a_wake_ends_the_sleep_only_while_skipping_for_occupancy(self):
        result, elapsed, still_set = self.wait_in(5, wake_after=.05)
        self.assertEqual((result, still_set), (False, False))
        self.assertLess(elapsed, 1)
        result, elapsed, _ = self.wait_in(.4, wake_after=.05, woken=lambda: False)
        self.assertFalse(result)
        self.assertGreaterEqual(elapsed, .39)  # an edge never shortens a send interval
        result, elapsed, _ = self.wait_in(5, stop_after=.05)
        self.assertTrue(result)
        self.assertLess(elapsed, 1)
        self.assertEqual(self.wait_in(.05)[0], False)

    def test_occupancy_skips_are_the_only_woken_reasons(self):
        for reason in (None, "no_person", "occupancy_unknown"):
            self.assertTrue(gate.occupancy_skip(reason))
        for reason in ("recorded_valid", "rate_capped", "admission_closed_backoff", "transport_backoff"):
            self.assertFalse(gate.occupancy_skip(reason))


def wait_for(condition, seconds=3):
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        time.sleep(.01)
    return condition()


class PumpWakeTests(ReaderFixture, StoreFixture, unittest.TestCase):
    def setUp(self):
        self.build_store()
        self.frames_every_half_second(990.0, 999.5)
        self.build_reader()

    def tearDown(self):
        self.close_store()

    def qwen(self, transport, **kwargs):
        return qwen_live.QwenLivePump(self.store, self.session, transport=transport, profile=dict(QWEN_PROFILE),
                                      config={"interval_seconds": 10}, clock=self.clock, local_time=lambda epoch: (19, 40),
                                      occupied=self.reader, **kwargs)

    def test_the_qwen_pump_wakes_within_one_loop_of_an_edge_and_never_calls_la_while_vacant(self):
        transport = QwenTransport(completed())
        pump = self.qwen(transport)
        self.assertIn(pump.wake, self.reader.subscribers)
        self.poll("off")
        self.assertEqual(pump.tick()["reason"], "no_person")
        self.assertEqual((transport.envelopes, transport.references), ([], []))  # not even the engine-epoch probe
        pump.start()
        try:
            time.sleep(.2)
            self.assertEqual((transport.envelopes, transport.references), ([], []))  # its 10 s sleep is running
            woke = time.monotonic()
            self.poll("on")  # off -> on: the edge wakes the sleeping pump
            self.assertTrue(wait_for(lambda: pump.snapshot()["last_reason"] == "recorded_valid"))
            self.assertLess(time.monotonic() - woke, 2)  # one loop, not the 10 s interval
            self.assertEqual(len(transport.envelopes), 1)
            self.assertEqual(len(transport.references), 1)  # the epoch probe ran only once occupied
        finally:
            pump.stop(timeout=2)
        self.assertFalse(pump.thread.is_alive())
        self.assertNotIn(pump.wake, self.reader.subscribers)

    def test_an_edge_while_sending_never_adds_a_send(self):
        transport = QwenTransport(completed(), completed())
        pump = self.qwen(transport)
        self.poll("on")
        self.assertEqual(pump.tick()["reason"], "recorded_valid")
        pump.start()
        try:
            self.poll("off", advance=.5)
            self.poll("on", advance=.5)  # a raw edge inside the hold: the pump was never skipping
            time.sleep(.3)
            self.assertEqual(len(transport.envelopes), 1)
        finally:
            pump.stop(timeout=2)

    def test_stop_wakes_both_pumps_at_once(self):
        qwen = self.qwen(QwenTransport())
        vjepa = vjepa_live.VjepaLivePump(self.store, self.session, transport=VjepaTransport(), profile=dict(VJEPA_PROFILE),
                                         config={"interval_seconds": 10}, clock=self.clock, occupied=self.reader,
                                         frames=NoFrames())
        vjepa.status_event = lambda reason: None
        for pump in (qwen, vjepa):
            pump.start()
        time.sleep(.1)
        started = time.monotonic()
        for pump in (qwen, vjepa):
            pump.stop(timeout=2)
            self.assertFalse(pump.thread.is_alive())
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(self.reader.subscribers, set())

    def test_the_vjepa_pump_wakes_on_an_edge_and_reads_nothing_while_vacant(self):
        frames = NoFrames()
        transport = VjepaTransport()
        pump = vjepa_live.VjepaLivePump(self.store, self.session, transport=transport, profile=dict(VJEPA_PROFILE),
                                        config={"interval_seconds": 10}, clock=self.clock, occupied=self.reader,
                                        frames=frames)
        pump.status_event = lambda reason: None
        self.poll("off")
        self.assertEqual(pump.tick()["reason"], "no_person")
        ticks = []
        tick = pump.tick
        pump.tick = lambda: ticks.append(self.reader.state()) or {"state": "skipped", "reason": "x"}
        pump.start()
        try:
            time.sleep(.2)
            self.assertEqual(ticks, [])
            self.poll("on")
            self.assertTrue(wait_for(lambda: ticks == ["on"], 2))
        finally:
            pump.stop(timeout=2)
        pump.tick = tick
        self.assertEqual((transport.bodies, frames.reads), ([], 0))


class NoFrameYet:
    """A frame source with nothing to send: the tick stops right after the gate."""
    def select(self, *args, **kwargs):
        raise qwen_live.FramesUnavailable("frames_unavailable")

    select_clip = select


class RuntimeReaderTests(HAFixture, ReaderFixture, StoreFixture, unittest.TestCase):
    def setUp(self):
        self.build_store()
        self.build_reader()
        self.made, self.starts = [], 0
        reader_start = self.reader.start

        def start():
            self.starts += 1
            return None  # never polls: the tests feed it by hand
        self.reader.start = start
        self.reader_start = reader_start

    def tearDown(self):
        self.close_store()

    def runtime(self, *, ha=True, derived=(OCC,), occupancy=None, explicit=None, bindings=None):
        from service_runtime import ServiceRuntime
        test = self

        def factory(kind):
            def create(store, session, *, active, enabled, occupied):
                test.made.append((kind, occupied))
                cls = qwen_live.QwenLivePump if kind == "qwen" else vjepa_live.VjepaLivePump
                extra = {"local_time": lambda epoch: (19, 40)} if kind == "qwen" else {}
                pump = cls(store, session, transport=QwenTransport() if kind == "qwen" else VjepaTransport(),
                           profile=dict(QWEN_PROFILE if kind == "qwen" else VJEPA_PROFILE), clock=test.clock,
                           active=active, occupied=occupied, frames=NoFrameYet(), **extra)
                pump.start = lambda: None
                return pump
            if occupancy is not None:
                create.occupancy = occupancy
            if explicit is not None:
                create.occupancy_explicit = explicit
            return create
        extra = {}
        if ha:
            self.source = self.provision_ha(derived=derived, entities=(OCC, "binary_sensor.den_other_occupancy"))
            extra = {"ha_sources": {"den": self.source}, "ha_clients": {"den": lambda: None}}
        runtime = ServiceRuntime(self.store, {}, lambda *a: None, None, qwen_live=factory("qwen"),
                                 vjepa_live=factory("vjepa"), occupancy_readers={"den": self.reader}, **extra)
        runtime._vjepa_arms = lambda runner: {"b"}
        if bindings is not None:
            runtime.reasoning_factory = type("Reasoning", (), {"ha_bindings": bindings})()
        return runtime

    def start(self, runtime):
        runtime._start_qwen_live(self.session)
        runtime._start_vjepa_live(self.session)
        return runtime._qwen_live_status(self.session["id"]), runtime._vjepa_live_status(self.session["id"])

    def test_the_gate_prefers_the_reader_and_needs_no_ledger_rows(self):
        runtime = self.runtime()
        self.start(runtime)
        self.assertEqual([(k, o) for k, o in self.made], [("qwen", self.reader), ("vjepa", self.reader)])
        self.poll("on")
        for pumps in (runtime.qwen_live_pumps, runtime.vjepa_live_pumps):
            pumps[self.session["id"]].tick()
        live = runtime.live(self.session)
        self.assertEqual(live["qwen_live_status"]["occupancy"]["reason"], "person_detected")
        self.assertEqual(self.events("ha.observation"), [])  # read from memory, nothing written
        runtime.stop_session(self.session)
        self.assertEqual(self.reader.subscribers, set())
        self.assertEqual(self.starts, 2)  # each pump asks; the reader starts polling once

    def test_a_reader_that_is_not_the_bound_entity_falls_back_to_the_ledger_gate(self):
        # Round-2 review: a reader whose entity the HA source or the JEV
        # person_present binding does not bind is passed over (one log line)
        # for the bound entity's ledger gate, exactly as without the block.
        refused = {"status": "occupancy_gate_not_provisioned"}
        other = "binary_sensor.den_other_occupancy"
        # Nothing the ledger gate could bind either: refused, as without the reader.
        for case, logged in (({"occupancy": {"entity": other}}, True),  # the pump names a non-camera-derived entity
                             ({"derived": ()}, True), ({"ha": False}, False)):  # no HA source: nothing to check
            with self.subTest(case=case):
                self.made, self.starts = [], 0
                with (self.assertLogs(LOG, "WARNING") if logged else self.assertNoLogs(LOG, "WARNING")):
                    self.assertEqual(self.start(self.runtime(**case)), (refused, refused))
                self.assertEqual((self.made, self.starts), ([], 0))
        # Another entity bound: both pumps get its ledger gate; the reader never starts.
        for case in ({"derived": (other,)},  # the HA source lists the occupancy sensor under another id
                     # Several camera-derived entities: person_present bound to another one wins.
                     {"derived": (OCC, other), "bindings": {other: "person_present"}},
                     {"derived": (OCC, other), "occupancy": {"entity": other}}):
            with self.subTest(case=case):
                self.made, self.starts = [], 0
                with self.assertLogs(LOG, "WARNING") as logs:
                    self.start(self.runtime(**case))
                self.assertEqual([kind for kind, _ in self.made], ["qwen", "vjepa"])
                for _, occupied in self.made:
                    self.assertIsInstance(occupied, gate.OccupancyGate)
                    self.assertEqual(occupied.entity, other)
                self.assertEqual(self.starts, 0)
                self.assertEqual(logs.output, [f"WARNING:{LOG}:occupancy reader passed over: camera=den entity={OCC} "
                                               "is not the bound camera-derived occupancy entity; "
                                               "falling back to the ledger gate"])  # once per runtime, not per pump
        # The reader's own entity, bound or the explicit choice among several, is provisioned.
        for case in ({"derived": (OCC, other), "bindings": {OCC: "person_present"}}, {"derived": (OCC, other)},
                     {"occupancy": {"entity": OCC}}):
            with self.subTest(case=case):
                self.made = []
                with self.assertNoLogs(LOG, "WARNING"):
                    self.start(self.runtime(**case))
                self.assertEqual(self.made, [("qwen", self.reader), ("vjepa", self.reader)])

    def test_a_reader_that_cannot_start_falls_back_to_the_ledger_gate(self):
        def broken():
            raise RuntimeError("can't start new thread")
        self.reader.start = broken
        with self.assertLogs(LOG, "WARNING") as logs:
            self.start(self.runtime())
        self.assertEqual([kind for kind, _ in self.made], ["qwen", "vjepa"])
        for _, occupied in self.made:
            self.assertIsInstance(occupied, gate.OccupancyGate)
            self.assertEqual(occupied.entity, OCC)
        self.assertEqual(len(logs.output), 1)
        self.assertIn("cannot start", logs.output[0])

    def cadence(self, runtime, state, now):
        """The JEV facts-key cadence's (gated, vacant, occupied) at now on one
        schedule state, and the gate it bound (None: nothing is gated)."""
        return runtime._occupancy(self.session, state, now), state["occupancy"]["gate"]

    def test_the_jev_cadence_never_goes_ungated_because_a_reader_was_added(self):
        # Round-2 review: reader present, the bindings name another
        # camera-derived entity. The cadence gate is that entity's ledger gate,
        # not None (which would stop suppressing JEV requests while vacant).
        other = "binary_sensor.den_other_occupancy"
        runtime, state, now = self.runtime(derived=(OCC, other), bindings={other: "person_present"}), {}, self.clock()
        with self.assertLogs(LOG, "WARNING"):
            (gated, vacant, occupied), bound = self.cadence(runtime, state, now)
        self.assertIsInstance(bound, gate.OccupancyGate)
        self.assertEqual((bound.entity, self.starts), (other, 0))
        self.assertEqual((gated, vacant, occupied), (False, False, False))  # no reading yet: unknown
        (gated, vacant, occupied), _ = self.cadence(runtime, state, now + bound.hold + 1)
        self.assertEqual((gated, occupied), (True, False))  # unknown past the hold gates the cadence
        # The same gate as the runtime built without the reader block.
        bare = self.runtime(derived=(OCC, other), bindings={other: "person_present"})
        bare.occupancy_readers = {}
        _, before = self.cadence(bare, {}, now)
        self.assertEqual((type(before), before.entity, before.hold, before.grace),
                         (type(bound), bound.entity, bound.hold, bound.grace))
        # The reader's own bound entity: the cadence uses the reader itself.
        matched = self.runtime(derived=(OCC, other), bindings={OCC: "person_present"})
        with self.assertLogs(LOG, "WARNING"):
            self.poll("on")  # its own edge line
        with self.assertNoLogs(LOG, "WARNING"):
            (gated, vacant, occupied), used = self.cadence(matched, {}, self.clock())
        self.assertIs(used, self.reader)
        self.assertEqual((gated, vacant, occupied), (False, False, True))

    def test_a_pump_level_hold_or_grace_other_than_the_readers_is_never_widened(self):
        # Reviewer case: qwen_live hold 3 s with the reader's default 10 s.
        refused = {"status": "occupancy_gate_not_provisioned"}
        for explicit in ({"hold_seconds": 3}, {"grace_seconds": 0}, {"hold_seconds": 12}):
            with self.subTest(explicit=explicit):
                self.made = []
                self.assertEqual(self.start(self.runtime(explicit=explicit)), (refused, refused))
                self.assertEqual(self.made, [])
        self.made = []
        self.start(self.runtime(explicit={"hold_seconds": 10.0, "grace_seconds": 5}))  # the reader's own values
        self.assertEqual(self.made, [("qwen", self.reader), ("vjepa", self.reader)])

    def test_the_live_config_refuses_a_pump_hold_or_grace_that_differs_from_the_readers(self):
        import vjepa_scene
        from live_detector_app import validate_config
        from test_ha_provisioning import live_config
        reader = {"origin": "https://victoria-jetson.example.ts.net", "credential_file": "/private/occupancy.token"}
        qwen = {"transport_file": "/private/qwen-live.json", "artifact_sha256": "a" * 64,
                "runtime_revision": "vllm-0.11.0", "enabled": True}
        vjepa = {"transport_file": "/private/vjepa.json", "artifact_sha256": vjepa_scene.ARTIFACT_SHA256,
                 "runtime_revision": "b" * 64, "enabled": True}
        refused = (("qwen_live", {**qwen, "occupancy_hold_seconds": 3}, reader),  # the reviewer's case
                   ("qwen_live", {**qwen, "occupancy_grace_seconds": 0}, reader),
                   ("qwen_live", {**qwen, "occupancy_hold_seconds": 10}, {**reader, "hold_seconds": 5}),
                   ("qwen_live", {**qwen, "occupancy_entity": "binary_sensor.den_other_occupancy"}, reader),
                   ("vjepa_live", {**vjepa, "occupancy_hold_seconds": 3}, reader))
        for name, block, reader_block in refused:
            with self.subTest(name=name, block=block), self.assertRaisesRegex(ValueError, "occupancy_reader"):
                validate_config(live_config(**{name: block}, occupancy_reader=reader_block))
        accepted = ((qwen, reader), ({**qwen, "occupancy_hold_seconds": 5}, {**reader, "hold_seconds": 5}),
                    ({**qwen, "occupancy_hold_seconds": 10.0, "occupancy_grace_seconds": 5, "occupancy_entity": OCC}, reader),
                    ({**qwen, "occupancy_hold_seconds": 3, "enabled": False}, reader))  # a disabled block builds nothing
        for block, reader_block in accepted:
            with self.subTest(block=block):
                validate_config(live_config(qwen_live=block, occupancy_reader=reader_block))
        validate_config(live_config(qwen_live={**qwen, "occupancy_hold_seconds": 3}))  # no reader: the ledger gate, as before

    def test_runtime_close_stops_the_reader(self):
        self.reader.start = self.reader_start
        runtime = self.runtime()
        self.start(runtime)
        self.assertTrue(wait_for(self.reader.thread.is_alive, 1))
        for pumps in (runtime.qwen_live_pumps, runtime.vjepa_live_pumps):
            pumps.pop(self.session["id"]).stop()  # hand-ticked pumps have no thread to join
        self.assertTrue(runtime.close(timeout=3)["drained"])
        self.assertFalse(self.reader.thread.is_alive())
        self.assertEqual(self.reader()["reason"], "occupancy_reader_stopped")


if __name__ == "__main__":
    unittest.main()
