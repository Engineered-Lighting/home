"""Owner-required occupancy gate: Qwen and V-JEPA send only while the
camera-derived Frigate person-occupancy sensor says someone is there.
Temp store, recorded ha.observation rows, scripted transports; no network."""
import unittest
from dataclasses import replace

import occupancy_gate as gate
import qwen_live
import vjepa_live
from ha_ingest import HAIngestor
from test_qwen_live import PROFILE as QWEN_PROFILE, Clock, FakeTransport as QwenTransport, StoreFixture, completed
from test_vjepa_live import PROFILE as VJEPA_PROFILE, Fixture as VjepaFixture, FakeTransport as VjepaTransport

OCC = "binary_sensor.den_oak_person_occupancy"


class NoFrames:
    """A frame source that must never be read while the gate is closed."""
    def __init__(self):
        self.reads = 0

    def select(self, *args, **kwargs):
        self.reads += 1
        raise AssertionError("frames read while the occupancy gate was closed")

    select_clip = select


class Recording:
    """store.db stand-in that records every statement the gate executes."""
    def __init__(self, db):
        self.db, self.statements = db, []

    def execute(self, sql, params=()):
        self.statements.append((sql, params))
        return self.db.execute(sql, params)


class HAFixture:
    def provision_ha(self, *, entities=(OCC, "media_player.tv"), derived=(OCC,), source_id="victoria-ha"):
        self.store.settings = replace(self.store.settings, cameras=({"id": "den"},))
        self.snapshots = 0
        return HAIngestor(self.store, site_id="victoria", camera_id="den", source_id=source_id,
                          entities=list(entities), camera_derived_entities=list(derived))

    def ha(self, state, *, age=.5, source=None):
        """One reconciled HA snapshot observed `age` seconds ago; None leaves the entity unknown."""
        self.snapshots += 1
        states = {"media_player.tv": "idle"}
        if state is not None:
            states[OCC] = state
        return (source or self.source).ingest(self.session["id"], snapshot_id=f"snap-{self.snapshots}",
                                              observed_at=self.clock.now - age, states=states)

    def gate(self, **kwargs):
        return gate.OccupancyGate(self.store, self.session, OCC, **kwargs)


class GateTests(HAFixture, StoreFixture, unittest.TestCase):
    def setUp(self):
        self.build_store()
        self.source = self.provision_ha()

    def tearDown(self):
        self.close_store()

    def test_on_is_occupied_off_is_vacant_and_anything_else_fails_closed(self):
        occupied = self.gate()
        self.assertEqual(occupied(), {"state": "unknown", "reason": "occupancy_unavailable", "observed_at": None})
        self.ha("on")
        self.assertEqual(occupied(), {"state": "occupied", "reason": "person_detected", "observed_at": 999.5})
        self.clock.now += 11  # past the hold after that 'on'
        self.ha("off")
        self.assertEqual(occupied()["state"], "vacant")
        self.clock.now += 1
        self.ha("unavailable")
        self.assertEqual(occupied(), {"state": "unknown", "reason": "occupancy_state_unknown", "observed_at": 1011.5})
        self.clock.now += 1
        self.ha(None)  # the entity missing from the snapshot is unknown too
        self.assertEqual(occupied()["reason"], "occupancy_state_unknown")

    def test_a_stale_reading_is_unknown_after_its_ttl_and_grace(self):
        self.ha("on")  # observed 999.5, valid_until 1002.5
        occupied = self.gate(grace_seconds=5)
        self.clock.now = 1007.5
        self.assertEqual(occupied()["state"], "occupied")
        self.clock.now = 1007.6
        self.assertEqual(occupied(), {"state": "unknown", "reason": "occupancy_stale", "observed_at": 999.5})
        strict = self.gate(grace_seconds=0)
        self.clock.now = 1002.6
        self.assertEqual(strict()["reason"], "occupancy_stale")

    def test_the_hold_keeps_a_brief_flicker_off_occupied_then_stops(self):
        occupied = self.gate(hold_seconds=10)
        self.ha("on")  # observed 999.5
        self.assertEqual(occupied()["state"], "occupied")
        for step in range(1, 10):  # the person sits still: occupancy flickers off
            self.clock.now = 1000 + step
            self.ha("off", age=0)
            self.assertEqual(occupied(), {"state": "occupied", "reason": "occupancy_hold", "observed_at": 1000.0 + step},
                             step)
        self.clock.now = 1009.5
        self.ha("off", age=0)
        self.assertEqual(occupied()["reason"], "occupancy_hold")  # exactly hold_seconds after the 'on'
        self.clock.now = 1009.6
        self.ha("off", age=0)
        self.assertEqual(occupied()["state"], "vacant")
        self.ha("on", age=0)
        self.assertEqual(occupied()["state"], "occupied")

    def test_the_hold_never_bridges_an_unknown_or_stale_reading(self):
        occupied = self.gate(hold_seconds=30)
        self.ha("on")
        self.clock.now += 1
        self.ha("unknown")
        self.assertEqual(occupied()["state"], "unknown")
        self.clock.now += 10  # the last reading is now stale
        self.assertEqual(occupied()["reason"], "occupancy_stale")

    def test_the_hold_remembers_an_on_that_left_the_scan_window(self):
        occupied = self.gate(hold_seconds=10, scan_rows=2)
        self.ha("on")
        self.assertEqual(occupied()["state"], "occupied")
        for _ in range(3):
            self.clock.now += 1
            self.ha("off")
        self.assertEqual(occupied()["reason"], "occupancy_hold")
        self.assertEqual(self.gate(hold_seconds=10, scan_rows=2)()["state"], "vacant")  # a new gate never saw it
        without_hold = self.gate(hold_seconds=0)
        self.assertEqual(without_hold()["state"], "vacant")

    def test_a_future_row_is_ignored(self):
        self.ha("off")  # observed 999.5, available 1000
        self.clock.now = 1005
        self.ha("on", age=.1)  # available at 1005
        self.clock.now = 1001  # a reader whose now precedes that row's availability
        occupied = self.gate(hold_seconds=30)
        self.assertEqual(occupied(), {"state": "vacant", "reason": "no_person", "observed_at": 999.5})
        self.clock.now = 1005
        self.assertEqual(occupied()["state"], "occupied")
        self.clock.now = 1001  # a clock step back never holds on the now-future 'on'
        self.assertEqual(occupied()["state"], "vacant")

    def test_only_rows_carrying_the_camera_derived_entity_count(self):
        presence = self.provision_ha(entities=("person.owner",), derived=(), source_id="la-presence")
        self.source = self.provision_ha()
        self.ha("on")
        self.clock.now += 1
        presence.ingest(self.session["id"], snapshot_id="p1", observed_at=self.clock.now - .2, states={"person.owner": "home"})
        self.assertEqual(self.gate()()["state"], "occupied")  # the interleaved presence row is skipped
        # The same entity without its camera-derived origin never counts.
        plain = self.provision_ha(derived=(), source_id="plain-ha")
        self.clock.now += 1
        self.ha("on", source=plain)  # fresh until 1009.5
        self.clock.now += 6  # the camera-derived 'on' (999.5) went stale at 1007.5
        self.assertEqual(self.gate(hold_seconds=0)()["reason"], "occupancy_stale")  # the plain row is newer but unusable

    def test_a_read_failure_is_unknown(self):
        db, self.store.db = self.store.db, None
        try:
            self.assertEqual(self.gate()()["reason"], "occupancy_read_failed")
        finally:
            self.store.db = db

    def test_one_bounded_indexed_query_whatever_the_session_length(self):
        for _ in range(300):
            self.clock.now += 1
            self.ha("off")
        self.clock.now += 1
        self.ha("on")
        db = self.store.db
        recording = Recording(db)
        self.store.db = recording
        try:
            self.assertEqual(self.gate()()["state"], "occupied")
        finally:
            self.store.db = db
        [(sql, params)] = recording.statements
        self.assertIn("LIMIT ?", sql)
        self.assertEqual(params[-1], gate.SCAN_ROWS)
        self.assertLessEqual(gate.SCAN_ROWS, 64)
        plan = " ".join(str(tuple(row)) for row in db.execute("EXPLAIN QUERY PLAN " + sql, params))
        self.assertIn("events_session_kind_id", plan)
        self.assertNotIn("SCAN", plan.replace("SCAN CONSTANT", ""))
        self.assertNotIn("TEMP B-TREE", plan)  # ordered by the index itself

    def test_configuration_and_entity_resolution(self):
        self.assertEqual(gate.gate_config(), {"entity": None, "hold_seconds": 10.0, "grace_seconds": 5.0})
        self.assertEqual(gate.gate_config({"occupancy_entity": OCC, "occupancy_hold_seconds": 0})["hold_seconds"], 0)
        for bad in ({"occupancy_hold_seconds": 31}, {"occupancy_hold_seconds": -1}, {"occupancy_grace_seconds": 11},
                    {"occupancy_grace_seconds": float("nan")}, {"occupancy_hold_seconds": True},
                    {"occupancy_entity": "person.owner"}, {"occupancy_entity": "binary_sensor.X"}, {"surprise": 1}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                gate.gate_config(bad)
        other = "binary_sensor.den_other_occupancy"
        self.assertEqual(gate.resolve_entity([OCC]), OCC)
        self.assertEqual(gate.resolve_entity([OCC, "sensor.den_person_count"]), OCC)
        self.assertIsNone(gate.resolve_entity([]))
        self.assertIsNone(gate.resolve_entity([OCC, other]))  # ambiguous without a binding or explicit key
        self.assertEqual(gate.resolve_entity([OCC, other], bindings={OCC: "person_present"}), OCC)
        self.assertEqual(gate.resolve_entity([OCC, other], explicit=other), other)
        self.assertIsNone(gate.resolve_entity([OCC], explicit=other))  # explicit must be camera-derived
        with self.assertRaisesRegex(ValueError, gate.NOT_PROVISIONED):
            gate.OccupancyGate(self.store, self.session, None)

    def test_the_gate_writes_nothing(self):
        self.ha("on")
        before = self.store.list_events(self.session["id"])
        occupied = self.gate()
        for _ in range(5):
            occupied()
        self.assertEqual(self.store.list_events(self.session["id"]), before)


class QwenPumpGateTests(HAFixture, StoreFixture, unittest.TestCase):
    def setUp(self):
        self.build_store()
        self.source = self.provision_ha()
        self.mono = Clock(50.0)
        self.frames_every_half_second(990.0, 999.5)

    def tearDown(self):
        self.close_store()

    def pump(self, transport, **kwargs):
        return qwen_live.QwenLivePump(self.store, self.session, transport=transport, profile=dict(QWEN_PROFILE),
                                      clock=self.clock, monotonic=self.mono, local_time=lambda epoch: (19, 40),
                                      **{"occupied": self.gate(), **kwargs})

    def test_occupied_sends(self):
        self.ha("on")
        transport = QwenTransport(completed())
        pump = self.pump(transport)
        self.assertEqual(pump.tick(), {"state": "current", "reason": "recorded_valid"})
        self.assertEqual(len(transport.envelopes), 1)
        self.assertEqual(pump.snapshot()["occupancy"], {"state": "occupied", "reason": "person_detected",
                                                        "skipped": {"no_person": 0, "occupancy_unknown": 0}})

    def test_vacant_skips_with_no_la_call_and_no_frame_read(self):
        self.ha("off")
        transport, frames = QwenTransport(completed()), NoFrames()
        pump = self.pump(transport, frames=frames)
        self.assertEqual(pump.tick(), {"state": "skipped", "reason": "no_person"})
        self.assertEqual((transport.envelopes, transport.references, frames.reads), ([], [], 0))  # not even the epoch probe
        snapshot = pump.snapshot()
        self.assertEqual((snapshot["status"], snapshot["last_reason"]), ("skipping", "no_person"))
        self.assertEqual(snapshot["occupancy"], {"state": "vacant", "reason": "no_person",
                                                 "skipped": {"no_person": 1, "occupancy_unknown": 0}})

    def test_unknown_or_stale_skips(self):
        transport, frames = QwenTransport(), NoFrames()
        pump = self.pump(transport, frames=frames)
        self.assertEqual(pump.tick()["reason"], "occupancy_unknown")  # no reading yet
        self.ha("unknown")
        self.assertEqual(pump.tick()["reason"], "occupancy_unknown")
        self.clock.now += 1
        self.ha("on")
        self.clock.now += 9  # stale: past TTL plus grace
        self.assertEqual(pump.tick()["reason"], "occupancy_unknown")
        self.assertEqual(pump.snapshot()["occupancy"], {"state": "unknown", "reason": "occupancy_stale",
                                                        "skipped": {"no_person": 0, "occupancy_unknown": 3}})
        self.assertEqual((transport.envelopes, transport.references, frames.reads), ([], [], 0))

    def test_a_failing_or_malformed_gate_is_unknown(self):
        def broken():
            raise RuntimeError("ledger closed")
        for occupied in (broken, lambda: None, lambda: {"state": "maybe"}):
            transport = QwenTransport()
            self.assertEqual(self.pump(transport, occupied=occupied).tick()["reason"], "occupancy_unknown")
            self.assertEqual(transport.references, [])

    def test_the_hold_keeps_sending_through_a_flicker_then_stops(self):
        transport = QwenTransport(completed(), completed())
        pump = self.pump(transport)
        self.ha("on")
        self.assertEqual(pump.tick()["reason"], "recorded_valid")
        self.clock.now += 4
        self.mono.now += 4
        self.frames_every_half_second(1000.5, self.clock.now - .5)
        self.ha("off")  # 3.5 s after the 'on': held
        self.assertEqual(pump.tick()["reason"], "recorded_valid")
        self.assertEqual(pump.snapshot()["occupancy"]["reason"], "occupancy_hold")
        self.clock.now += 8
        self.mono.now += 8
        self.frames_every_half_second(1004.5, self.clock.now - .5)
        self.ha("off")  # 11.5 s after the 'on': the hold is over
        self.assertEqual(pump.tick()["reason"], "no_person")
        self.assertEqual(len(transport.envelopes), 2)

    def test_an_outstanding_request_is_still_reconciled_while_vacant(self):
        from qwen_transport import CompletionUnproven
        from test_qwen_live import terminal
        transport = QwenTransport(CompletionUnproven("lost"), lookups=[terminal("queue_expired")])
        pump = self.pump(transport)
        self.ha("on")
        self.assertEqual(pump.tick()["reason"], "outcome_unproven")
        self.clock.now += 11
        self.ha("off")
        # Settling an already sent request is not a new dispatch: it never resends.
        self.assertEqual(pump.tick()["reason"], "la_queue_expired")
        self.assertEqual(pump.tick()["reason"], "no_person")
        self.assertEqual(len(transport.envelopes), 1)

    def test_a_pump_without_a_gate_refuses_to_exist(self):
        with self.assertRaisesRegex(ValueError, "occupancy gate not provisioned"):
            qwen_live.QwenLivePump(self.store, self.session, transport=QwenTransport(), profile=dict(QWEN_PROFILE))
        with self.assertRaisesRegex(ValueError, "occupancy gate not provisioned"):
            self.pump(QwenTransport(), occupied="yes")


class VjepaPumpGateTests(HAFixture, VjepaFixture, unittest.TestCase):
    def setUp(self):
        self.build()
        self.source = self.provision_ha()
        self.mono = Clock(50.0)
        self.scene_frames(990.0, 999.5)

    def tearDown(self):
        self.close_store()

    def pump(self, transport, **kwargs):
        return vjepa_live.VjepaLivePump(self.store, self.session, transport=transport, profile=dict(VJEPA_PROFILE),
                                        clock=self.clock, monotonic=self.mono, **{"occupied": self.gate(), **kwargs})

    def test_occupied_sends_and_vacant_or_unknown_sends_nothing(self):
        transport, frames = VjepaTransport(), NoFrames()
        closed = self.pump(transport, frames=frames)
        self.assertEqual(closed.tick(), {"state": "skipped", "reason": "occupancy_unknown"})
        self.ha("off")
        self.assertEqual(closed.tick(), {"state": "skipped", "reason": "no_person"})
        self.assertEqual((transport.bodies, frames.reads), ([], 0))
        self.assertEqual(closed.snapshot()["occupancy"], {"state": "vacant", "reason": "no_person",
                                                          "skipped": {"no_person": 1, "occupancy_unknown": 1}})
        self.clock.now += .5
        self.ha("on")
        pump = self.pump(transport)
        self.assertEqual(pump.tick(), {"state": "current", "reason": "recorded_valid"})
        self.assertEqual(len(transport.bodies), 1)
        self.assertEqual(pump.snapshot()["occupancy"]["state"], "occupied")

    def test_a_pump_without_a_gate_refuses_to_exist(self):
        with self.assertRaisesRegex(ValueError, "occupancy gate not provisioned"):
            vjepa_live.VjepaLivePump(self.store, self.session, transport=VjepaTransport(), profile=dict(VJEPA_PROFILE))


class RuntimeGateTests(HAFixture, StoreFixture, unittest.TestCase):
    def setUp(self):
        self.build_store()
        self.made = []

    def tearDown(self):
        self.close_store()

    def runtime(self, *, ha=True, derived=(OCC,), entities=(OCC, "media_player.tv"), occupancy=None, bindings=None):
        from service_runtime import ServiceRuntime
        test = self

        def factory(kind):
            def create(store, session, *, active, enabled, occupied):
                test.made.append((kind, occupied))
                if kind == "qwen":
                    pump = qwen_live.QwenLivePump(store, session, transport=QwenTransport(), profile=dict(QWEN_PROFILE),
                                                  clock=test.clock, local_time=lambda epoch: (19, 40), active=active,
                                                  occupied=occupied, frames=NoFrames())
                else:
                    pump = vjepa_live.VjepaLivePump(store, session, transport=VjepaTransport(), profile=dict(VJEPA_PROFILE),
                                                    clock=test.clock, active=active, occupied=occupied, frames=NoFrames())
                pump.start = lambda: None  # ticked by hand
                return pump
            if occupancy is not None:
                create.occupancy = occupancy
            return create
        extra = {}
        if ha:
            self.source = self.provision_ha(entities=entities, derived=derived)
            extra = {"ha_sources": {"den": self.source}, "ha_clients": {"den": lambda: None}}
        runtime = ServiceRuntime(self.store, {}, lambda *a: None, None, qwen_live=factory("qwen"), vjepa_live=factory("vjepa"),
                                 **extra)
        # The V-JEPA pump also needs an active pair granting temporal_encoder
        # (test_vjepa_wiring covers that); these tests exercise only its gate.
        runtime._vjepa_arms = lambda runner: {"b"}
        if bindings is not None:
            runtime.reasoning_factory = type("Reasoning", (), {"ha_bindings": bindings})()
        return runtime

    def start(self, runtime):
        runtime._start_qwen_live(self.session)
        runtime._start_vjepa_live(self.session)
        return runtime._qwen_live_status(self.session["id"]), runtime._vjepa_live_status(self.session["id"])

    def test_missing_provisioning_refuses_both_pumps(self):
        refused = {"status": "occupancy_gate_not_provisioned"}
        cases = ({"ha": False}, {"derived": ()}, {"occupancy": {"entity": "binary_sensor.den_other_occupancy"}},
                 {"entities": (OCC, "binary_sensor.den_other_occupancy"), "derived": (OCC, "binary_sensor.den_other_occupancy")})
        for case in cases:
            with self.subTest(case=case):
                runtime = self.runtime(**case)
                self.assertEqual(self.start(runtime), (refused, refused))
                self.assertEqual((runtime.qwen_live_pumps, runtime.vjepa_live_pumps, self.made), ({}, {}, []))
                self.assertEqual(runtime.live(self.session)["qwen_live_status"], refused)
                self.assertEqual(runtime.live(self.session)["vjepa_live_status"], refused)

    def test_a_person_present_binding_disambiguates(self):
        other = "binary_sensor.den_other_occupancy"
        runtime = self.runtime(entities=(OCC, other), derived=(OCC, other), bindings={OCC: "person_present"})
        self.start(runtime)
        self.assertEqual([(kind, occupied.entity) for kind, occupied in self.made], [("qwen", OCC), ("vjepa", OCC)])

    def test_both_pumps_get_their_own_gate_and_live_shows_its_state(self):
        runtime = self.runtime(occupancy={"entity": OCC, "hold_seconds": 4, "grace_seconds": 2})
        self.start(runtime)
        [(_, qwen_gate), (_, vjepa_gate)] = self.made
        self.assertIsNot(qwen_gate, vjepa_gate)
        self.assertEqual((qwen_gate.entity, qwen_gate.hold, qwen_gate.grace), (OCC, 4, 2))
        live = runtime.live(self.session)
        self.assertEqual(live["qwen_live_status"]["occupancy"]["state"], "unchecked")
        self.ha("off")
        runtime.qwen_live_pumps[self.session["id"]].tick()
        runtime.vjepa_live_pumps[self.session["id"]].tick()
        live = runtime.live(self.session)
        for key in ("qwen_live_status", "vjepa_live_status"):
            self.assertEqual(live[key]["last_reason"], "no_person")
            self.assertEqual(live[key]["occupancy"], {"state": "vacant", "reason": "no_person",
                                                      "skipped": {"no_person": 1, "occupancy_unknown": 0}})
        runtime.stop_session(self.session)
        self.assertTrue(runtime.qwen_live_pumps[self.session["id"]].stopped.is_set())
        self.assertTrue(runtime.vjepa_live_pumps[self.session["id"]].stopped.is_set())

    def test_recording_only_refuses_the_vjepa_pump(self):
        from service_runtime import ServiceRuntime
        with self.assertRaises(ValueError):
            ServiceRuntime(object(), {}, None, None, recording_only=True, vjepa_live=lambda *a, **k: None)


if __name__ == "__main__":
    unittest.main()
