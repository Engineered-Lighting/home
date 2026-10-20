"""The V-JEPA 2.1 den evidence source wired end to end: the per-session LA lease
and leased pump, the 'vjepa_live' provisioning, the runtime composition (after
the detector admission, recipe-gated, released on stop), the JEV projection in
the live v5 packet and the v6 facts wire, the household vjepa-den-1 grant, the
vjepa-onoff recipe and the graph node. Temp stores, fake lifecycle clients and
transports; no network, model or GPU."""
from copy import deepcopy
import json
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import vjepa_lease
import vjepa_live as live
import vjepa_scene as scene
from jev_evidence import QWEN_EGRESS, VJEPA_EGRESS, project, vjepa_projection
from reasoning import shared_evidence
from test_qwen_live import OCCUPIED, Clock
from test_vjepa_live import FakeTransport, Fixture as ClipFixture
from test_vjepa_scene import BOX, PROFILE, receipt, result, strings_and_numbers

EPOCH = PROFILE["engine_epoch"]
REVISION = PROFILE["runtime_revision"]


def vjepa_rows(cutoff=100.0, *, session="s", arms=("a", "b"), generation=1):
    """Per-arm shared V-JEPA scene records over three causal receipts."""
    receipts = [receipt(t) for t in (cutoff - 9, cutoff - 5, cutoff - 1.5)]
    projected = scene.projection(receipts, cutoff)
    record = scene.scene_record(projected, receipts, cutoff, site_id="victoria", camera_id="den", session_id=session)
    return scene.shared_records(record, arms=set(arms), generation=generation)


def observation(session, observed, *, request_id=None):
    """A valid vjepa.observation draft for a real session, latest frame at observed."""
    req = {"request_id": request_id or scene.digest([session["id"], observed]), "profile": dict(PROFILE),
           "person_box": list(BOX), "single_person": True, "previous_request_id": None}
    frames = [{"media_id": "m%d" % i, "frame_id": "f%d" % i, "capture_epoch": "cap-1", "observed_at": observed - 3.5 + .5 * i}
              for i in range(8)]
    return scene.build_observation(site_id="victoria", camera_id="den", session_id=session["id"], request=req, frames=frames,
                                   box_input={"frames": 8, "event_ids": [1]}, generated_at=observed + .4, result=result(req),
                                   body_sha256="b" * 64, response_sha256="c" * 64)


class FakeLifecycle:
    """The LA lifecycle supervisor as LifecycleClient returns it."""

    def __init__(self, store=None, *, states=("starting", "ready"), expires=None, profile=None, reject=None, reason=None,
                 clock=None):
        self.store, self.states, self.reject, self.reason = store, list(states), reject, reason
        self.clock = clock or (lambda: 1000.0)
        self.expires = expires
        self.profile = dict(PROFILE) if profile is None else profile
        self.started, self.stopped, self.state = [], [], None
        self.recorded_before_start = []
        self.fail_status = False

    def receipt(self, rid):
        return {"version": 1, "request_id": rid, "session_id": "x", "site_id": "echo", "state": self.state,
                "engine_epoch": EPOCH, "expires_at": self.clock() + 355 if self.expires is None else self.expires,
                "profile": self.profile if self.state == "ready" else None, "reason": self.reason, "variant": "detector"}

    def start(self, rid, session_id):
        from worker_lifecycle_client import StartRejected
        if self.store is not None:
            rows = self.store.db.execute("SELECT payload FROM events WHERE kind='vjepa.lease'").fetchall()
            self.recorded_before_start.append(any(json.loads(r[0]).get("request_id") == rid for r in rows))
        self.started.append((rid, session_id))
        if self.reject:
            raise StartRejected(self.reject)
        self.state = "starting"
        return self.receipt(rid)

    def status(self, rid):
        if self.fail_status:
            raise ValueError("Lifecycle response unavailable")
        if self.state == "stopping":
            self.state = "stopped"
        elif self.state == "starting" and self.states:
            self.state = self.states.pop(0)
        return self.receipt(rid)

    def stop(self, rid, epoch):
        self.stopped.append((rid, epoch))
        self.state = "stopping"
        return self.receipt(rid)


class Event:
    def __init__(self):
        self.set_ = False

    def is_set(self):
        return self.set_

    def wait(self, _seconds):
        return self.set_


class LeaseTests(ClipFixture, unittest.TestCase):
    def setUp(self):
        self.build()
        self.mono = Clock(50.0)

    def tearDown(self):
        self.close_store()

    def lease(self, client, **kw):
        return vjepa_lease.VjepaLease(self.store, self.session, client, runtime_revision=REVISION, clock=self.clock,
                                      monotonic=self.mono, sleep=lambda s: None, **kw)

    def lease_events(self):
        return [e["payload"] for e in self.store.list_events(self.session["id"]) if e["kind"] == "vjepa.lease"]

    def test_the_lease_id_is_recorded_before_start_and_the_ready_profile_is_exact(self):
        client = FakeLifecycle(self.store, states=("starting", "starting", "ready"))
        lease = self.lease(client)
        self.assertEqual(lease.acquire(Event()), PROFILE)
        self.assertEqual(client.recorded_before_start, [True])
        self.assertEqual([e["state"] for e in self.lease_events()], ["requesting", "ready"])
        self.assertEqual(client.started[0][1], self.session["id"])
        self.assertEqual(lease.acquire(Event()), PROFILE)  # reused, never a second start
        self.assertEqual(len(client.started), 1)
        text = json.dumps(self.lease_events())
        for private in ("token", "Bearer", "profile"):
            self.assertNotIn(private, text)

    def test_refusals_are_typed_back_off_and_are_bounded(self):
        client = FakeLifecycle(self.store, reject="worker_busy")
        lease = self.lease(client)
        with self.assertRaises(vjepa_lease.LeaseUnavailable) as caught:
            lease.acquire(Event())
        self.assertEqual(caught.exception.reason, "la_worker_busy")
        with self.assertRaises(vjepa_lease.LeaseUnavailable) as caught:
            lease.acquire(Event())
        self.assertEqual((caught.exception.reason, len(client.started)), ("lease_backoff", 1))
        client.reject, client.states, client.reason = None, ["failed"], "preflight_vram"
        self.mono.now += vjepa_lease.RETRY_SECONDS
        with self.assertRaises(vjepa_lease.LeaseUnavailable) as caught:
            lease.acquire(Event())
        self.assertEqual(caught.exception.reason, "lease_preflight_vram")
        client.states = ["failed"]
        self.mono.now += vjepa_lease.RETRY_SECONDS
        with self.assertRaises(vjepa_lease.LeaseUnavailable):
            lease.acquire(Event())
        self.mono.now += vjepa_lease.RETRY_SECONDS
        with self.assertRaises(vjepa_lease.LeaseUnavailable) as caught:
            lease.acquire(Event())
        self.assertEqual((caught.exception.reason, len(client.started)), ("lease_attempts_exhausted", vjepa_lease.MAX_ATTEMPTS))

    def test_a_foreign_profile_is_released_and_never_retried(self):
        for profile in ({**PROFILE, "runtime_revision": "other"}, {**PROFILE, "artifact_sha256": "0" * 64},
                        {**PROFILE, "engine_epoch": "la-live-" + "1" * 32}):
            with self.subTest(profile=profile):
                client = FakeLifecycle(self.store, profile=profile)
                lease = self.lease(client)
                with self.assertRaises(vjepa_lease.LeaseUnavailable) as caught:
                    lease.acquire(Event())
                self.assertEqual(caught.exception.reason, "lease_profile_mismatch")
                self.assertEqual(client.stopped, [(client.started[0][0], EPOCH)])
                self.mono.now += 3600
                with self.assertRaises(vjepa_lease.LeaseUnavailable):
                    lease.acquire(Event())
                self.assertEqual(len(client.started), 1)

    def test_a_worker_that_never_becomes_ready_is_stopped(self):
        client = FakeLifecycle(self.store, states=())
        lease = self.lease(client, ready_seconds=0)
        with self.assertRaises(vjepa_lease.LeaseUnavailable) as caught:
            lease.acquire(Event())
        self.assertEqual(caught.exception.reason, "lease_ready_timeout")
        self.assertEqual(len(client.stopped), 1)

    def test_no_clip_near_lease_expiry_and_release_is_proven_or_indeterminate(self):
        client = FakeLifecycle(self.store, expires=self.clock.now + 60)
        lease = self.lease(client)
        lease.acquire(Event())
        self.clock.now += 51
        with self.assertRaises(vjepa_lease.LeaseUnavailable) as caught:
            lease.acquire(Event())
        self.assertEqual(caught.exception.reason, "lease_expiring")
        self.assertEqual(lease.release(), "stopped")
        self.assertEqual(self.lease_events()[-1]["state"], "released")
        with self.assertRaises(vjepa_lease.LeaseUnavailable):
            lease.acquire(Event())
        # An unprovable release is recorded, never claimed.
        client = FakeLifecycle(self.store)
        lease = self.lease(client)
        lease.acquire(Event())
        client.fail_status = True
        self.assertEqual(lease.release(), "indeterminate")
        self.assertEqual(self.lease_events()[-1], {**self.lease_events()[-1], "state": "indeterminate", "reason": "release_unproven"})

    def test_readiness_is_polled_every_quarter_second(self):
        self.assertEqual(vjepa_lease.POLL_SECONDS, .25)
        waits = []

        class Cancelled(Event):
            def wait(inner, seconds):
                waits.append(seconds)
                return False
        client = FakeLifecycle(self.store, states=("starting", "starting", "ready"))
        self.assertEqual(self.lease(client).acquire(Cancelled()), PROFILE)
        self.assertEqual(waits, [.25, .25])

    def test_session_identity_is_required(self):
        with self.assertRaises(ValueError):
            vjepa_lease.VjepaLease(self.store, {**self.session, "id": "../x"}, FakeLifecycle(), runtime_revision=REVISION)


class LeasedPumpTests(ClipFixture, unittest.TestCase):
    def setUp(self):
        self.build()
        self.mono = Clock(50.0)
        self.scene_frames(990.0, 999.5)
        self.occupancy = {"state": "occupied", "reason": "person_detected"}

    def tearDown(self):
        self.close_store()

    def pump(self, client, transport):
        lease = vjepa_lease.VjepaLease(self.store, self.session, client, runtime_revision=REVISION, clock=self.clock,
                                       monotonic=self.mono, sleep=lambda s: None)
        return vjepa_lease.LeasedVjepaPump(self.store, self.session, lease=lease, transport=transport,
                                           runtime_revision=REVISION, clock=self.clock, monotonic=self.mono,
                                           occupied=lambda: dict(self.occupancy))

    def test_an_empty_room_never_leases_and_the_first_occupied_tick_does(self):
        client, transport = FakeLifecycle(self.store), FakeTransport()
        pump = self.pump(client, transport)
        self.occupancy = {"state": "vacant", "reason": "no_person"}
        self.assertEqual(pump.tick()["reason"], "no_person")
        self.assertEqual((client.started, transport.bodies), ([], []))
        self.occupancy = OCCUPIED()
        self.assertEqual(pump.tick(), {"state": "current", "reason": "recorded_valid"})
        [body] = transport.bodies
        self.assertEqual(body["profile"], PROFILE)  # the lease's exact worker profile
        self.assertEqual(pump.snapshot()["lease"]["state"], "ready")

    def test_an_occupancy_edge_wakes_the_pump_into_the_lease_and_a_vacant_room_never_leases(self):
        class EdgeGate:
            """An edge-reporting gate with occupancy_reader's interface."""
            def __init__(inner):
                inner.state, inner.subscribers = "vacant", set()

            def __call__(inner):
                return {"state": inner.state, "reason": "person_detected" if inner.state == "occupied" else "no_person"}

            def subscribe(inner, event):
                inner.subscribers.add(event)

            def unsubscribe(inner, event):
                inner.subscribers.discard(event)

            def edge(inner):
                inner.state = "occupied"
                for event in list(inner.subscribers):
                    event.set()
        gate, client, transport, seen = EdgeGate(), FakeLifecycle(), FakeTransport(), []
        start = client.start
        client.start = lambda rid, sid: seen.append(gate()["state"]) or start(rid, sid)
        lease = vjepa_lease.VjepaLease(self.store, self.session, client, runtime_revision=REVISION, clock=self.clock,
                                       monotonic=self.mono, sleep=lambda s: None)
        pump = vjepa_lease.LeasedVjepaPump(self.store, self.session, lease=lease, transport=transport,
                                           runtime_revision=REVISION, config={"interval_seconds": 10}, clock=self.clock,
                                           monotonic=self.mono, occupied=gate)
        self.assertEqual(pump.tick()["reason"], "no_person")
        pump.start()
        try:
            time.sleep(.3)
            self.assertEqual(client.started, [])  # vacant: asleep for its 10 s interval, no lease
            woke = time.monotonic()
            gate.edge()
            deadline = woke + 3
            while not transport.bodies and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertLess(time.monotonic() - woke, 2)  # the edge, not the interval, started the lease
            self.assertEqual((seen, len(client.started), len(transport.bodies)), (["occupied"], 1, 1))
        finally:
            pump.stop(timeout=5)
        self.assertFalse(pump.thread.is_alive())
        self.assertEqual(pump.snapshot()["lease"]["state"], "released")

    def test_a_refused_lease_sends_nothing(self):
        client, transport = FakeLifecycle(self.store, reject="worker_busy"), FakeTransport()
        pump = self.pump(client, transport)
        self.assertEqual(pump.tick(), {"state": "skipped", "reason": "la_worker_busy"})
        self.assertEqual(transport.bodies, [])
        self.assertFalse(self.events("vjepa.observation"))

    def test_stopping_the_pump_releases_the_lease(self):
        client, transport = FakeLifecycle(self.store), FakeTransport()
        pump = self.pump(client, transport)
        pump.config["interval_seconds"] = 1.5
        pump.tick()
        pump.start()
        pump.stop(timeout=5)
        self.assertFalse(pump.thread.is_alive())
        self.assertEqual(client.stopped, [(client.started[0][0], EPOCH)])
        self.assertEqual(pump.snapshot()["lease"]["state"], "released")
        self.assertTrue(transport.closed)


class ProjectionTests(unittest.TestCase):
    def test_only_an_exact_bucket_projection_leaves_and_it_stays_correlated(self):
        record = vjepa_rows()["b"][0]
        [projected] = project([record])
        self.assertEqual(projected["vjepa_scene"], record["vjepa_scene"])
        self.assertEqual(projected["quality"]["correlated_with"], {"relation": "same_camera_visual", "camera_id": "den"})
        self.assertEqual(project([projected]), [projected])  # the policy's re-projection
        strings, numbers = [], []
        strings_and_numbers(projected["vjepa_scene"], strings, numbers)
        self.assertEqual(numbers, [])
        self.assertEqual(projected["vjepa_scene"]["calibration"], "unvalidated")
        for private in ("media:", "c" * 64, EPOCH, "request_id", "0.71"):
            self.assertNotIn(private, json.dumps(projected))
        for change in (lambda p: p["latest"].update(age=3), lambda p: p["latest"]["facts"]["posture"].update(top="dancing"),
                       lambda p: p.update(probability=.8), lambda p: p.update(schema="vjepa-den-projection-2"),
                       lambda p: p.update(calibration="validated"), lambda p: p.update(independent_confirmation=True),
                       lambda p: p["summary_last_60s"].update(window="free text")):
            broken = deepcopy(record)
            change(broken["vjepa_scene"])
            with self.subTest(change=change):
                self.assertIsNone(vjepa_projection(broken["vjepa_scene"]))
                self.assertEqual(project([broken]), [])

    def test_one_shared_record_per_kind_beside_the_qwen_scene(self):
        from test_qwen_wiring import scene_record as qwen_record
        rows = [qwen_record()["a"][0], vjepa_rows()["a"][0]]
        kinds = [r["source_kind"] for r in shared_evidence(rows, site_id="victoria", session_id="s", arm="a", generation=1,
                                                           cutoff=100)]
        self.assertEqual(sorted(kinds), ["qwen.observation", "vjepa.observation"])
        with self.assertRaisesRegex(ValueError, "latest record only"):
            shared_evidence(rows + [dict(rows[1], id="vjepa-scene:other")], site_id="victoria", session_id="s", arm="a",
                            generation=1, cutoff=100)

    def test_a_worker_result_cannot_smuggle_a_vjepa_scene(self):
        from persisted_evidence import observation_record
        session = {"id": "s", "site_id": "victoria", "camera_id": "den"}
        value = {"site_id": "victoria", "camera_id": "den", "status": "completed", "arm": "a", "generation": 1,
                 "observed_at": 99, "available_at": 99.5, "valid_until": 101,
                 "result": {"objects": [], "vjepa_scene": vjepa_rows()["a"][0]["vjepa_scene"]}}
        self.assertNotIn("vjepa_scene", observation_record(5, "perception.output", value, session))


def visual(cutoff=100.0, session="s", arm="b"):
    return {"id": "7", "site_id": "victoria", "camera_id": "den", "session_id": session, "arm": arm, "generation": 1,
            "source_kind": "perception.output", "observed_at": cutoff - .5, "available_at": cutoff - .3,
            "valid_until": cutoff + 2, "objects": [{"label": "person"}, {"label": "book"}], "evidence_roots": ["7"]}


class SemanticTests(unittest.TestCase):
    def test_the_block_is_uncalibrated_correlated_and_absent_without_a_record(self):
        from jev_semantics import semantic_state
        plain = semantic_state(project([visual()]), 100)
        self.assertNotIn("vjepa_scene", plain)
        self.assertNotIn("vjepa_scene", plain["source_coverage"])
        state = semantic_state(project([visual()]) + project(vjepa_rows()["b"]), 100)
        block = state["vjepa_scene"]
        self.assertEqual((block["calibration"], block["independent_confirmation"]), ("unvalidated", False))
        self.assertEqual(block["latest_facts"]["posture"], {"top": "sitting", "confidence": "high"})
        self.assertEqual(state["source_coverage"]["vjepa_scene"], "supplied")
        self.assertIn("vjepa_scene block holds uncalibrated bucket words", state["limitations"])
        # Never an observation, never the latest visual evidence, never corroboration.
        self.assertEqual(state["latest_visual_evidence_id"], "7")
        self.assertNotIn(block["evidence_id"], [o["evidence_id"] for o in state["observations"]])
        self.assertEqual(state["corroboration"], plain["corroboration"])


class PolicyV5Tests(unittest.TestCase):
    import test_paired_runtime as fixtures
    setUp = fixtures.PairedRuntimeTests.setUp
    tearDown = fixtures.PairedRuntimeTests.tearDown

    def gate(self, sources):
        from jev_policy import LabJevPolicy
        cutoff = 100.0
        self.store.clock = lambda: cutoff + .5
        policy = {"schema_version": 1, "enabled": True, "scope": "household_structured", "revision": "v", "generation": 1,
                  "expires_at": cutoff + 100, "site_id": "victoria", "camera_ids": ["den"], "model_selector": "jev-1.13",
                  "max_calls_per_minute": 20, **({"structured_sources": sources} if sources is not None else {})}
        self.store.db.execute("CREATE TABLE IF NOT EXISTS lab_reasoning_requests(id TEXT PRIMARY KEY,session_id TEXT,state TEXT)")
        return LabJevPolicy(self.store, lambda: policy, lambda: {"state": "on", "received_at": cutoff + .5})

    def packet(self, arm, context=None):
        from reasoning import build_jev_request
        sid = self.session["id"]
        rows = [{**r, "session_id": sid} for r in vjepa_rows(session=sid)[arm]]
        packet, _ = build_jev_request(project([visual(session=sid, arm=arm)]), model="jev-1.13", site_id="victoria", session_id=sid,
                                      arm=arm, generation=1, cutoff=100.0, retain_history=True, semantic=True,
                                      shared_context=project(rows), context=context)
        self.assertIn("vjepa.observation", [r.get("source_kind") for r in packet["state"]["evidence"]])
        return packet

    def send(self, gate, arm, packet, request):
        self.store.db.execute("INSERT INTO lab_reasoning_requests VALUES(?,?,?)", (request, self.session["id"], "dispatching"))
        return gate({"site_id": "victoria", "session_id": self.session["id"], "arm": arm, "generation": 1,
                     "request_id": request}, packet, "send")

    def test_the_record_needs_the_exact_vjepa_grant(self):
        packet = self.packet("b")
        for index, sources in enumerate((None, [QWEN_EGRESS], ["vjepa-den-2"])):
            with self.subTest(sources=sources), self.assertRaisesRegex(ValueError, "V-JEPA scene egress not permitted"):
                self.send(self.gate(sources), "b", packet, "q%d" % index)
        self.assertTrue(self.send(self.gate([VJEPA_EGRESS]), "b", packet, "ok")["allowed"])

    def test_a_withheld_arm_cannot_carry_the_record(self):
        from ambient_context import shared_context
        from evidence_policy import source_policy
        grants = source_policy(None, ha={"a": True, "b": True})
        grants["temporal_encoder"] = {"a": "withheld_by_experiment", "b": "granted"}
        blocks = shared_context([], 100.0, grants=grants, lever=["temporal_encoder"], vjepa="supplied")
        self.assertEqual((blocks["a"]["coverage"]["temporal_encoder"], blocks["b"]["coverage"]["temporal_encoder"]),
                         ("withheld_by_experiment", "supplied"))
        self.assertEqual(blocks["a"]["shared_context_digest"], blocks["b"]["shared_context_digest"])
        gate = self.gate([VJEPA_EGRESS])
        with self.assertRaisesRegex(ValueError, "Structured source permission mismatch"):
            self.send(gate, "a", self.packet("a", context=blocks["a"]), "withheld")
        self.assertTrue(self.send(gate, "b", self.packet("b", context=blocks["b"]), "granted")["allowed"])


class WireV6Tests(unittest.TestCase):
    import test_jev_policy_v6 as v6
    setUp = v6.V6Case.setUp
    tearDown = v6.V6Case.tearDown
    output, records, refused = v6.V6Case.output, v6.V6Case.records, v6.V6Case.refused

    def block(self, status="supplied", arm="b"):
        from ambient_context import shared_context
        from evidence_policy import source_policy
        grants = source_policy(None, ha={"a": True, "b": True})
        grants["temporal_encoder"] = {"a": "withheld_by_experiment", "b": "granted"}
        return shared_context([], 100.0, grants=grants, lever=["temporal_encoder"], vjepa=status)[arm]

    def request(self, rid="v1", *, context, vjepa=True):
        import jev_wire as wire
        from test_jev_wire import qwen
        cutoff, window = 100.0, 60
        records = project(self.records(cutoff, window), ha_bindings=self.v6.BINDINGS)
        shared = project(qwen(cutoff=cutoff, session=self.sid))
        if vjepa:
            shared += project([{**r, "arm": "a"} for r in vjepa_rows(session=self.sid)["b"]])
        local, packet, binding = wire.prepare(records, shared, context, site_id="victoria", session_id=self.sid, arm="a",
                                              generation=1, cutoff=cutoff, window_seconds=window, model="jev-1.13.0",
                                              ring=wire.FrameRing())
        request = {"request_id": rid, "site_id": "victoria", "session_id": self.sid, "arm": "a", "generation": 1,
                   **{k: v for k, v in binding.items() if k not in ("packet_bytes", "estimated_tokens")}}
        self.store.db.execute("INSERT OR REPLACE INTO lab_reasoning_requests VALUES(?,?,?)", (rid, self.sid, "dispatching"))
        return request, packet, local

    def test_the_facts_block_is_words_only_and_admitted_under_the_grant(self):
        import jev_wire as wire
        self.policy["structured_sources"] = [QWEN_EGRESS, VJEPA_EGRESS, wire.WIRE_VERSION]
        context, packet, local = self.request(context=self.block())
        state = packet["state"]
        wire.validate_state(state)
        block = state["vjepa"]
        self.assertEqual((block["coverage"], block["calibration"], block["independent_confirmation"]), ("supplied", "unvalidated", False))
        self.assertEqual(block["latest"]["facts"]["posture"], {"top": "sitting", "confidence": "high"})
        self.assertEqual(block["history"]["window"], "last_60_s")
        strings, numbers = [], []
        strings_and_numbers(block, strings, numbers)
        self.assertEqual(numbers, [])
        self.assertEqual(state["coverage"]["temporal_encoder"], "supplied")
        self.assertEqual([r["source_kind"] for r in local["shared"]], ["qwen.observation", "vjepa.observation"])
        self.assertTrue(self.gate(context, packet, "send", local=local)["allowed"])
        # Without a V-JEPA record the sidecar and state are exactly the earlier ones.
        _, plain, plain_local = self.request("v2", context=self.block(), vjepa=False)
        self.assertNotIn("vjepa", plain["state"])
        self.assertEqual([r["source_kind"] for r in plain_local["shared"]], ["qwen.observation"])
        for value in (1, "not_a_word"):
            forged = deepcopy(packet)
            forged["state"]["vjepa"]["latest"]["age"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                wire.validate_state(forged["state"])

    def test_ungranted_or_withheld_blocks_are_refused(self):
        import jev_wire as wire
        self.policy["structured_sources"] = [QWEN_EGRESS, wire.WIRE_VERSION]
        self.refused(*self.request(context=self.block()), message="V-JEPA scene egress not permitted")
        self.policy["structured_sources"] = [QWEN_EGRESS, VJEPA_EGRESS, wire.WIRE_VERSION]
        self.refused(*self.request("v3", context=self.block(arm="a")))


class StubPump:
    def __init__(self, active, enabled, occupied=None):
        self.active, self.enabled, self.occupied = active, enabled, occupied
        self.thread = threading.Thread(target=lambda: None)
        self.started = self.stopped = False

    def start(self):
        self.started = True
        self.thread.start()
        self.thread.join()

    def stop(self, timeout=None):
        self.stopped = True

    def snapshot(self):
        return {"status": "current" if self.started and not self.stopped else "stopped", "last_reason": None, "counts": {}}


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        from dataclasses import replace
        from evidence_policy import source_policy
        from ha_ingest import HAIngestor
        from test_reasoning_load import Simulation
        self.directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.sim = Simulation(self.directory.name, filler_files=0)
        self.sim.preview()
        self.granted = {"vjepa": True}
        self.sim.runtime.reasoning_factory = self.factory()
        runner = self.sim.runner
        runner.source_policy = source_policy(None, ha=runner.ha_evidence)
        runner.source_policy["temporal_encoder"] = {"a": "withheld_by_experiment", "b": "granted"}
        runner.lever_families = ["temporal_encoder"]
        self.sim.store.settings = replace(self.sim.store.settings, cameras=({"id": "den"},))
        occupancy = "binary_sensor.den_oak_person_occupancy"
        self.sim.runtime.ha_sources = {"den": HAIngestor(self.sim.store, site_id="victoria", camera_id="den", source_id="victoria-ha",
                                                         entities=[occupancy], camera_derived_entities=[occupancy])}
        self.sim.runtime.ha_clients = {"den": lambda: None}
        self.pumps = []

        def vjepa_live_factory(store, session, *, active, enabled, occupied):
            pump = StubPump(active, enabled, occupied)
            self.pumps.append(pump)
            return pump
        vjepa_live_factory.kill_switch = lambda: self.granted["vjepa"]
        self.sim.runtime.vjepa_live = vjepa_live_factory
        self.ledger = live.VjepaLedger(self.sim.store)
        self.session = self.sim.store.get_session(self.sim.sid)

    def tearDown(self):
        self.sim.close()
        self.directory.cleanup()

    def factory(self):
        from jev_policy import LabJevPolicy
        from jev_provider import JevProvider
        from live_reasoning import LiveReasoning
        from test_reasoning_load import SyncPool, fake_transport
        sim, test = self.sim, self

        def policy():
            return {"schema_version": 1, "enabled": True, "scope": "household_structured", "revision": "vjepa", "generation": 1,
                    "expires_at": sim.now + 10_000, "site_id": "victoria", "camera_ids": ["den"], "model_selector": "jev-1.13",
                    "max_calls_per_minute": 120, **({"structured_sources": [VJEPA_EGRESS]} if test.granted["vjepa"] else {})}

        def create(store, sid):
            provider = JevProvider(model_selector="jev-1.13", response_version="jev-1.13.0", allow_recent_history=True, semantic=True,
                authorize=LabJevPolicy(store, policy, lambda: {"state": "on", "received_at": store.clock()}),
                credential=lambda: "fixture", transport=fake_transport, clock=store.clock)
            reasoner = LiveReasoning(store, sid, provider)
            reasoner.pool.shutdown(wait=False)
            reasoner.pool = SyncPool()
            return reasoner
        create.vjepa_granted = lambda: test.granted["vjepa"]
        return create

    def receipt(self, age=1.0):
        return self.ledger.record(self.sim.sid, observation(self.session, self.sim.now - age))

    def packets(self):
        with self.sim.store.lock:
            sent = [json.loads(r[0])["request"] for r in
                    self.sim.store.db.execute("SELECT payload FROM events WHERE kind='reasoning.request' ORDER BY id")]
            states = dict(self.sim.store.db.execute("SELECT arm,state FROM lab_reasoning_requests ORDER BY rowid").fetchall())
        return sent, states

    def offer(self):
        self.sim.output("a")
        self.sim.output("b")
        self.sim.tick()

    def test_the_pump_needs_an_active_pair_granting_the_family(self):
        runtime, sid = self.sim.runtime, self.sim.sid
        runner = runtime.runners.pop(sid)
        runtime._start_vjepa_live(self.session)
        self.assertEqual(runtime._vjepa_live_status(sid), {"status": "detector_admission_not_ready"})
        runtime.runners[sid] = runner
        granted = runner.source_policy["temporal_encoder"]
        runner.source_policy["temporal_encoder"] = {"a": "not_in_baseline", "b": "not_in_baseline"}
        runtime._start_vjepa_live(self.session)
        self.assertEqual(runtime._vjepa_live_status(sid), {"status": "temporal_encoder_not_in_recipe"})
        self.assertEqual(self.pumps, [])
        runner.source_policy["temporal_encoder"] = granted
        runtime._start_vjepa_live(self.session)
        [pump] = self.pumps
        self.assertTrue(pump.started and pump.active() and pump.enabled())
        self.assertEqual(pump.occupied.entity, "binary_sensor.den_oak_person_occupancy")
        self.granted["vjepa"] = False
        self.assertFalse(pump.enabled())
        self.granted["vjepa"] = True
        runtime._start_vjepa_live(self.session)  # idempotent per recording
        self.assertEqual(len(self.pumps), 1)
        runner.closed = True  # the pair (and its detector lease) ended: nothing more is sent
        self.assertFalse(pump.active())
        runner.closed = False
        runtime.stop_session(self.session)
        self.assertTrue(pump.stopped)

    def test_start_session_starts_the_pump_only_after_the_pair_is_activated(self):
        runtime, sid = self.sim.runtime, self.sim.sid
        runner = runtime.runners.pop(sid)
        runtime.recorders = {"den": type("Recorder", (), {"start": lambda self, session: {"state": "recording"}})()}
        with patch.object(runtime, "_prepare", return_value={"state": "blocked", "reason": "worker_busy"}):
            self.assertEqual(runtime.start_session(self.session)["inference"], "unavailable")
        self.assertEqual(self.pumps, [])

        def activate(*args, **kwargs):
            runtime.runners[sid] = runner
            return 1
        with patch.object(runtime, "_prepare", return_value={"state": "ready", "profiles": {}}), \
                patch.object(runtime, "_activate", side_effect=activate):
            self.assertEqual(runtime.start_session(self.session)["inference"], "ready")
        [pump] = self.pumps
        self.assertTrue(pump.started)

    def test_only_the_granted_arm_gets_the_record_and_the_context_digests_stay_equal(self):
        self.sim.runtime._start_vjepa_live(self.session)
        for age in (5.0, 3.0, 1.0):
            self.receipt(age)
        self.offer()
        sent, states = self.packets()
        self.assertEqual(states, {"a": "completed", "b": "completed"})
        latest = {p["state"]["evidence"][0]["arm"]: p for p in sent[-2:]}
        carried = {arm: [r for r in p["state"]["evidence"] if r.get("source_kind") == "vjepa.observation"] for arm, p in latest.items()}
        self.assertEqual({arm: len(v) for arm, v in carried.items()}, {"a": 0, "b": 1})
        coverage = {arm: p["state"]["semantic"]["context"]["coverage"]["temporal_encoder"] for arm, p in latest.items()}
        self.assertEqual(coverage, {"a": "withheld_by_experiment", "b": "supplied"})
        self.assertNotIn("vjepa_scene", latest["a"]["state"]["semantic"])
        self.assertEqual(latest["b"]["state"]["semantic"]["vjepa_scene"]["latest_facts"]["posture"]["top"], "sitting")
        digests = {arm: p["state"]["semantic"]["context"]["shared_context_digest"] for arm, p in latest.items()}
        self.assertEqual(digests["a"], digests["b"])

    def test_without_the_household_grant_nothing_leaves_and_dispatch_continues(self):
        self.sim.runtime._start_vjepa_live(self.session)
        self.receipt(1.0)
        self.granted["vjepa"] = False
        self.offer()
        sent, states = self.packets()
        self.assertEqual(states, {"a": "completed", "b": "completed"})
        for packet in sent:
            self.assertFalse([r for r in packet["state"]["evidence"] if r.get("source_kind") == "vjepa.observation"])
        coverage = {p["state"]["evidence"][0]["arm"]: p["state"]["semantic"]["context"]["coverage"]["temporal_encoder"] for p in sent}
        self.assertEqual(coverage, {"a": "withheld_by_experiment", "b": "not_granted"})

    def test_graph_shows_the_source_with_coverage_and_bucket_tokens_only(self):
        import graph as lab_graph
        self.sim.runtime._start_vjepa_live(self.session)
        self.receipt(1.0)
        self.offer()
        node = next(s for s in lab_graph.graph(self.sim.store, self.sim.sid)["sources"] if s["id"] == "vjepa_scene")
        self.assertEqual(node["consumed_by"], {"a": "withheld_by_setup", "b": "consumed"})
        self.assertEqual(node["version"]["coverage"], {"a": "withheld_by_experiment", "b": "supplied"})
        self.assertEqual(node["families"], [17])
        snippet = json.dumps(node["native"]["snippet"])
        self.assertIn(scene.PROJECTION_SCHEMA, snippet)
        for private in ("media:", "c" * 64, EPOCH, "request_id", "0.66", "0.71"):
            self.assertNotIn(private, snippet)


TRANSPORT = {"schema_version": 1, "origin": "https://192.168.0.100:9447", "site_id": "echo",
             "ca_file": "/home/jetson-user/perception-lab/private/lab-vjepa/ca.pem",
             "lifecycle_token_file": "/home/jetson-user/perception-lab/private/lab-vjepa/lifecycle.token",
             "execution_token_file": "/home/jetson-user/perception-lab/private/lab-vjepa/execution.token"}


class ProvisioningTests(unittest.TestCase):
    BLOCK = {"transport_file": "/home/jetson-user/perception-lab/private/lab-vjepa/vjepa-transport.json",
             "artifact_sha256": scene.ARTIFACT_SHA256, "runtime_revision": "e" * 64, "enabled": True}

    def test_block_is_exact_and_bounded(self):
        from live_detector_app import check_vjepa_live
        self.assertEqual(check_vjepa_live(self.BLOCK), live.pump_config())
        self.assertEqual(check_vjepa_live({**self.BLOCK, "interval_seconds": 3, "max_per_minute": 20})["max_per_minute"], 20)
        self.assertEqual(check_vjepa_live({**self.BLOCK, "box_match_seconds": 0})["box_match_seconds"], 0)  # exact frames
        occupancy = {"occupancy_entity": "binary_sensor.den_oak_person_occupancy", "occupancy_hold_seconds": 15}
        self.assertNotIn("occupancy_entity", check_vjepa_live({**self.BLOCK, **occupancy}))
        self.assertEqual(check_vjepa_live({**self.BLOCK, "interval_seconds": 1, "max_per_minute": 60})["max_per_minute"], 60)
        for change in ({"transport_file": "relative.json"}, {"artifact_sha256": "a" * 64}, {"runtime_revision": "fixture"},
                       {"enabled": "yes"}, {"interval_seconds": .9}, {"max_per_minute": 61}, {"frames": 8}, {"extra": 1},
                       {"occupancy_entity": "person.owner"}, {"box_match_seconds": 1.0}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                check_vjepa_live({**self.BLOCK, **change})

    def test_transport_file_is_exact(self):
        from live_detector_app import check_vjepa_transport
        self.assertEqual(check_vjepa_transport(TRANSPORT), TRANSPORT)
        for keep_alive in (True, False):  # optional; absent means kept alive
            self.assertEqual(check_vjepa_transport({**TRANSPORT, "keep_alive": keep_alive})["keep_alive"], keep_alive)
        for change in ({"origin": "https://192.168.0.100:9443"}, {"origin": "http://192.168.0.100:9447"}, {"site_id": "victoria"},
                       {"ca_file": "ca.pem"}, {"execution_token_file": TRANSPORT["lifecycle_token_file"]}, {"schema_version": 2},
                       {"extra": 1}, {"keep_alive": "no"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                check_vjepa_transport({**TRANSPORT, **change})

    def test_config_validation_and_managed_recipes(self):
        from live_detector_app import validate_config
        from managed_detector_app import enabled_recipes
        with patch("live_detector_app.check_vjepa_live", side_effect=ValueError("bad")) as check:
            with self.assertRaises(ValueError):
                validate_config({"schema_version": 1, "capture_config": "/c", "camera_env": "/e", "lease_file": "/l",
                                 "receiver_clock": {}, "arms": {}, "vjepa_live": {}})
            check.assert_called_once()
        self.assertEqual(enabled_recipes({}), {"placement-same"})
        self.assertEqual(enabled_recipes({"vjepa_live": self.BLOCK}), {"placement-same"})
        self.assertEqual(enabled_recipes({"pose_profiles": {}, "vjepa_live": {**self.BLOCK, "enabled": False}}),
                         {"placement-same", "placement-best"})
        self.assertEqual(enabled_recipes({"pose_profiles": {}, "vjepa_live": self.BLOCK}),
                         {"placement-same", "placement-best", "vjepa-onoff"})

    def test_factory_builds_leased_pumps_behind_the_household_grant(self):
        from live_detector_app import vjepa_live_factory
        reasoning = type("Factory", (), {})()
        reasoning.vjepa_granted = lambda: True
        made = {"client": [], "transport": []}

        def client_factory(origin, credential, ca_file, site_id):
            made["client"].append((origin, ca_file, site_id))
            return FakeLifecycle()

        def transport_factory(origin, credential, ca_file):
            made["transport"].append((origin, ca_file))
            return FakeTransport()
        with patch("live_detector_app.private_json", return_value=dict(TRANSPORT)):
            self.assertIsNone(vjepa_live_factory({"vjepa_live": {**self.BLOCK, "enabled": False}}))
            factory = vjepa_live_factory({"vjepa_live": self.BLOCK}, reasoning_factory=reasoning, client_factory=client_factory,
                                         transport_factory=transport_factory, check_files=False)
            with patch("live_detector_app.private_json", return_value={**TRANSPORT, "site_id": "victoria"}):
                with self.assertRaises(ValueError):
                    vjepa_live_factory({"vjepa_live": self.BLOCK}, check_files=False)
        self.assertTrue(factory.kill_switch())
        reasoning.vjepa_granted = lambda: False
        self.assertFalse(factory.kill_switch())
        self.assertEqual(factory.occupancy, {"entity": None, "hold_seconds": 10.0, "grace_seconds": 5.0})
        session = {"id": "a" * 32, "site_id": "victoria", "camera_id": "den", "state": "recording"}
        pump = factory(None, session, active=lambda: True, enabled=lambda: True, occupied=OCCUPIED)
        self.assertIsInstance(pump, vjepa_lease.LeasedVjepaPump)
        self.assertEqual(pump.lease.runtime_revision, "e" * 64)
        self.assertEqual(made, {"client": [(TRANSPORT["origin"], TRANSPORT["ca_file"], "echo")],
                                "transport": [(TRANSPORT["origin"], TRANSPORT["ca_file"])]})
        # The transport file's keep_alive switch reaches the clip transport.
        switches = []

        def switched(origin, credential, ca_file, **kwargs):
            switches.append(kwargs)
            return FakeTransport()
        with patch("live_detector_app.private_json", return_value={**TRANSPORT, "keep_alive": False}):
            factory = vjepa_live_factory({"vjepa_live": self.BLOCK}, check_files=False, transport_factory=switched,
                                         client_factory=lambda *a: FakeLifecycle())
        factory(None, session, active=lambda: True, enabled=lambda: True, occupied=OCCUPIED)
        self.assertEqual(switches, [{"keep_alive": False}])

    def test_managed_jev_reports_the_grant_per_tick(self):
        from managed_jev import reasoning_factory
        config = {"schema_version": 1, "policy_file": "policy", "credential_file": "key", "response_version": "jev-1.13.0"}
        policy = {"schema_version": 1, "enabled": True, "scope": "household_structured", "revision": "r", "generation": 1,
                  "expires_at": 4_000_000_000, "site_id": "victoria", "camera_ids": ["den"], "model_selector": "jev-1.13.0",
                  "max_calls_per_minute": 20}
        for sources, expected in ((None, "not_granted"), ([QWEN_EGRESS], "not_granted"), ([VJEPA_EGRESS], "granted")):
            value = dict(policy, **({"structured_sources": sources} if sources else {}))
            with self.subTest(sources=sources), \
                    patch("managed_jev.private_json", side_effect=lambda p: dict(config if p == "config" else value)):
                factory = reasoning_factory("config")
                self.assertEqual(factory.readiness()["vjepa_scene"], expected)
                self.assertEqual(factory.vjepa_granted(), expected == "granted")


class ManagedPairTests(unittest.TestCase):
    import test_managed_pair as pair
    setUp, tearDown, settle = pair.ManagedTests.setUp, pair.ManagedTests.tearDown, pair.ManagedTests.settle

    def test_vjepa_onoff_prepares_the_pose_pair_and_its_own_factory(self):
        self.runtime.catalog.append({"id": "vjepa-onoff", "status": "unverified"})
        selected = []
        self.runtime.factory = lambda receipts, experiment_id: selected.append(experiment_id) or self.pair.Inner()
        session = self.controller.start({**self.body, "experiment_id": "vjepa-onoff"})
        self.settle("recording", session["id"])
        self.assertEqual(selected, ["vjepa-onoff"])
        self.assertEqual([c.variant for c in self.clients.values()], ["pose", "pose"])
        self.controller.stop(session["id"])
        self.settle("stopped", session["id"])


if __name__ == "__main__":
    unittest.main()
