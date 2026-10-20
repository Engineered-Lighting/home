"""The continuous Qwen perceiver wired into the live runtime and the JEV
packet: shared (never visual) context, identical for both arms, allowlisted
enum projection, household structured-source grant, semantics and graph.
Temp stores, stub pumps and fake transports; no network or model."""
from copy import deepcopy
import json
import tempfile
import threading
import unittest
from unittest.mock import patch

import qwen_scene as scene
from jev_evidence import AMBIENT_EGRESS, QWEN_EGRESS, project, qwen_projection
from reasoning import shared_evidence
import test_paired_runtime as fixtures

FACTS = {"people_visible": "one", "subject_posture": "sitting", "held_object": "book", "book_state": "open",
         "gaze_target": "book", "typing_visible": "no", "screen_facing_subject": "not_visible",
         "activity_hypothesis": "reading", "visible_basis": ["person_visible", "book_in_hands"], "change_vs_prior": "no_prior"}


def draft(session, observed, *, facts=FACTS, request=None):
    frames = [{"media_id": "m%d" % i, "frame_id": "cap-1-0-%d" % i, "capture_epoch": "cap-1", "observed_at": observed - 3 + 1.5 * i}
              for i in range(3)]
    return scene.build_observation(site_id="victoria", camera_id="den", session_id=session["id"],
                                   request_id=request or scene.digest([session["id"], observed]), frames=frames,
                                   generated_at=observed + .5, prompt_version=scene.PROMPT_VERSION,
                                   context=scene.model_context("evening 19:40"), body_sha256="b" * 64,
                                   engine_epoch="epoch-1", artifact_sha256="a" * 64,
                                   content=json.dumps(facts), finish_reason="stop", prior_supplied=False)


def scene_record(cutoff=100.0, **kw):
    receipts = []
    for i, t in enumerate((cutoff - 8, cutoff - 4, cutoff - 1)):
        value = {**draft({"id": "s"}, t, request="r%d" % i), "available_at": t + .6, "first_received_at": t + .6}
        receipts.append(value)
    projected = scene.projection(receipts, cutoff)
    record = scene.scene_record(projected, receipts, cutoff, site_id="victoria", camera_id="den", session_id="s")
    return scene.shared_records(record, arms={"a", "b"}, generation=1)


class ProjectionTests(unittest.TestCase):
    def test_only_an_exact_token_projection_leaves_and_it_stays_correlated(self):
        record = scene_record()["a"][0]
        [projected] = project([record])
        self.assertEqual(projected["qwen_scene"], record["qwen_scene"])
        self.assertEqual(projected["quality"]["correlated_with"], {"relation": "same_camera_visual", "camera_id": "den"})
        self.assertEqual(project([projected]), [projected])  # the policy's re-projection
        text = json.dumps(projected)
        for private in ("media:", "b" * 64, "epoch-1", "response_sha256"):
            self.assertNotIn(private, text)
        # Anything outside the vocabulary drops the whole record: numbers,
        # unknown words, extra keys, another schema.
        for change in (lambda p: p["latest"].update(age=3), lambda p: p["latest"]["facts"].update(held_object="a red mug"),
                       lambda p: p.update(note="free text"), lambda p: p.update(schema="qwen-scene-projection-2"),
                       lambda p: p["latest"]["facts"].update(visible_basis=["person_visible", 1.5])):
            broken = deepcopy(record)
            change(broken["qwen_scene"])
            with self.subTest(change=change):
                self.assertIsNone(qwen_projection(broken["qwen_scene"]))
                self.assertEqual(project([broken]), [])

    def test_one_shared_record_per_source_kind(self):
        qwen = scene_record()["a"][0]
        caption = {"id": "c1", "kind": "observation", "source_kind": "caption.receipt", "site_id": "victoria", "session_id": "s",
                   "arm": "a", "generation": 1, "observed_at": 98, "available_at": 98.5, "valid_until": 120,
                   "historical_caption": {"activity": "reading"}}
        kinds = [r["source_kind"] for r in shared_evidence([caption, qwen], site_id="victoria", session_id="s", arm="a",
                                                           generation=1, cutoff=100)]
        self.assertEqual(sorted(kinds), ["caption.receipt", "qwen.observation"])
        with self.assertRaisesRegex(ValueError, "latest record only"):
            shared_evidence([qwen, dict(qwen, id="qwen-scene:other")], site_id="victoria", session_id="s", arm="a",
                            generation=1, cutoff=100)

    def test_a_worker_result_cannot_smuggle_a_scene(self):
        from persisted_evidence import observation_record
        session = {"id": "s", "site_id": "victoria", "camera_id": "den"}
        value = {"site_id": "victoria", "camera_id": "den", "status": "completed", "arm": "a", "generation": 1,
                 "observed_at": 99, "available_at": 99.5, "valid_until": 101,
                 "result": {"objects": [], "qwen_scene": scene_record()["a"][0]["qwen_scene"]}}
        self.assertNotIn("qwen_scene", observation_record(5, "perception.output", value, session))


class SemanticTests(unittest.TestCase):
    def test_scene_block_is_unvalidated_and_corroboration_separates_independent_signals(self):
        from jev_semantics import VERSION, semantic_state
        visual = {"id": "7", "site_id": "victoria", "camera_id": "den", "session_id": "s", "arm": "a", "generation": 1,
                  "source_kind": "perception.output", "observed_at": 99.5, "available_at": 99.7, "valid_until": 102,
                  "objects": [{"label": "person"}, {"label": "book"}], "evidence_roots": ["7"]}
        ha = {"id": "8", "site_id": "victoria", "camera_id": "den", "session_id": "s", "arm": "a", "generation": 1,
              "source_kind": "ha.observation", "observed_at": 99.6, "available_at": 99.7, "valid_until": 102.6,
              "ha": {"light.den": {"state": "on"}, "binary_sensor.occ": {"state": "on", "origin": "same_camera_visual"},
                     "derived.computer_input_recency": {"state": "over_5_min"}}}
        bindings = {"light.den": "den_light", "binary_sensor.occ": "person_present",
                    "derived.computer_input_recency": "computer_input_recency"}
        records = project([visual, ha], ha_bindings=bindings) + project(scene_record()["a"])
        state = semantic_state(records, 100)
        self.assertEqual(VERSION, "den-semantics-7")
        block = state["qwen_scene"]
        self.assertEqual((block["calibration"], block["independent_confirmation"]), ("unvalidated", False))
        self.assertEqual(block["latest_facts"]["activity_hypothesis"], "reading")
        self.assertEqual(state["source_coverage"]["qwen_scene"], "supplied")
        self.assertIn("qwen_scene block is an unvalidated model interpretation", state["limitations"])
        # Qwen never becomes an observation or the latest visual evidence.
        self.assertEqual(state["latest_visual_evidence_id"], "7")
        self.assertNotIn(block["evidence_id"], [o["evidence_id"] for o in state["observations"]])
        reading = state["corroboration"]["activities"]["reading"]
        self.assertEqual(reading, {"same_camera": ["qwen_scene", "camera_occupancy"], "independent": ["den_light"],
                                   "independent_support": "one"})
        self.assertEqual(state["corroboration"]["activities"]["resting"]["independent"], ["computer_input_recency"])
        self.assertEqual(state["corroboration"]["activities"]["computer_work"]["independent_support"], "none")


class PolicyTests(unittest.TestCase):
    setUp = fixtures.PairedRuntimeTests.setUp
    tearDown = fixtures.PairedRuntimeTests.tearDown

    def test_scene_records_need_the_structured_source_grant(self):
        from jev_policy import LabJevPolicy
        from reasoning import build_jev_request
        cutoff = 100.0
        self.store.clock = lambda: cutoff + .5
        policy = {"schema_version": 1, "enabled": True, "scope": "household_structured", "revision": "q", "generation": 1,
                  "expires_at": cutoff + 100, "site_id": "victoria", "camera_ids": ["den"], "model_selector": "jev-1.13",
                  "max_calls_per_minute": 20}
        gate = LabJevPolicy(self.store, lambda: policy, lambda: {"state": "on", "received_at": cutoff + .5})
        self.store.db.execute("CREATE TABLE IF NOT EXISTS lab_reasoning_requests(id TEXT PRIMARY KEY,session_id TEXT,state TEXT)")
        context = {"site_id": "victoria", "session_id": self.session["id"], "arm": "a", "generation": 1}
        visual = {**context, "id": "7", "camera_id": "den", "observed_at": 99.5, "available_at": 99.7, "valid_until": 102,
                  "objects": [{"label": "book"}]}
        qwen = [{**r, "session_id": self.session["id"]} for r in scene_record(cutoff)["a"]]
        packet, _ = build_jev_request(project([visual]), model="jev-1.13", site_id="victoria", session_id=self.session["id"],
                                      arm="a", generation=1, cutoff=cutoff, retain_history=True, semantic=True,
                                      shared_context=project(qwen))
        self.assertIn("qwen.observation", [r["source_kind"] for r in packet["state"]["evidence"] if "source_kind" in r])
        for index, grant in enumerate((None, ["lab-live-scene-2"], "lab-live-scene-1", ["lab-live-scene-1", "lab-live-scene-1"])):
            if grant is None:
                policy.pop("structured_sources", None)
            else:
                policy["structured_sources"] = grant
            request = "q%d" % index
            self.store.db.execute("INSERT INTO lab_reasoning_requests VALUES(?,?,?)", (request, self.session["id"], "dispatching"))
            with self.subTest(grant=grant), self.assertRaises(ValueError):
                gate({**context, "request_id": request}, packet, "send")
            self.store.db.execute("UPDATE lab_reasoning_requests SET state='failed' WHERE id=?", (request,))
        policy["structured_sources"] = [QWEN_EGRESS]
        self.store.db.execute("INSERT INTO lab_reasoning_requests VALUES(?,?,?)", ("ok", self.session["id"], "dispatching"))
        self.assertTrue(gate({**context, "request_id": "ok"}, packet, "send")["allowed"])


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
        return {"status": "current" if self.started and not self.stopped else "stopped", "last_reason": None, "counts": {},
                "outstanding": False, "halted": None}


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        from evidence_policy import source_policy
        from qwen_live import QwenSceneLedger
        from test_reasoning_load import Simulation
        self.directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.sim = Simulation(self.directory.name, filler_files=0)
        self.sim.preview()
        self.granted = {"qwen": True}
        self.sim.runtime.reasoning_factory = self.factory()
        runner = self.sim.runner
        runner.source_policy = source_policy(None, ha=runner.ha_evidence)
        runner.source_policy["qwen_scene"] = {"a": "granted", "b": "granted"}
        self.pumps = []
        # The pumps run only behind the camera-derived Frigate occupancy gate,
        # so the recording's HA source provisions that entity.
        from dataclasses import replace
        from ha_ingest import HAIngestor
        self.sim.store.settings = replace(self.sim.store.settings, cameras=({"id": "den"},))
        occupancy = "binary_sensor.den_oak_person_occupancy"
        self.sim.runtime.ha_sources = {"den": HAIngestor(self.sim.store, site_id="victoria", camera_id="den", source_id="victoria-ha",
                                                         entities=[occupancy], camera_derived_entities=[occupancy])}
        self.sim.runtime.ha_clients = {"den": lambda: None}

        def qwen_live(store, session, *, active, enabled, occupied):
            pump = StubPump(active, enabled, occupied)
            self.pumps.append(pump)
            return pump
        qwen_live.kill_switch = lambda: self.granted["qwen"]
        qwen_live.horizon_seconds = 120
        self.sim.runtime.qwen_live = qwen_live
        self.ledger = QwenSceneLedger(self.sim.store)
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
            return {"schema_version": 1, "enabled": True, "scope": "household_structured", "revision": "qwen", "generation": 1,
                    "expires_at": sim.now + 10_000, "site_id": "victoria", "camera_ids": ["den"], "model_selector": "jev-1.13",
                    "max_calls_per_minute": 120, **({"structured_sources": [QWEN_EGRESS]} if test.granted["qwen"] else {})}

        def create(store, sid):
            provider = JevProvider(model_selector="jev-1.13", response_version="jev-1.13.0", allow_recent_history=True, semantic=True,
                authorize=LabJevPolicy(store, policy, lambda: {"state": "on", "received_at": store.clock()}),
                credential=lambda: "fixture", transport=fake_transport, clock=store.clock)
            reasoner = LiveReasoning(store, sid, provider)
            reasoner.pool.shutdown(wait=False)
            reasoner.pool = SyncPool()
            return reasoner
        create.qwen_granted = lambda: test.granted["qwen"]
        return create

    def receipt(self, age=1.0):
        observed = self.sim.now - age
        return self.ledger.record(self.sim.sid, draft(self.session, observed))

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

    def test_pump_lifecycle_follows_the_recording(self):
        runtime, sid = self.sim.runtime, self.sim.sid
        runtime._start_qwen_live(self.session)
        [pump] = self.pumps
        self.assertTrue(pump.started and pump.active())
        self.assertEqual(pump.occupied.entity, "binary_sensor.den_oak_person_occupancy")
        # Sends only while reasoning is enabled and the household kill switch is on.
        self.assertTrue(pump.enabled())
        self.granted["qwen"] = False
        self.assertFalse(pump.enabled())
        self.granted["qwen"] = True
        self.assertEqual(runtime.live(self.session)["qwen_live_status"]["status"], "current")
        runtime._start_qwen_live(self.session)  # idempotent per recording
        self.assertEqual(len(self.pumps), 1)
        runtime.stop_session(self.session)
        self.assertTrue(pump.stopped)
        self.sim.store.transition(sid, "stopping")
        self.sim.store.transition(sid, "stopped")
        self.assertIn(sid, runtime.reap_finished_sessions()["closed_sessions"])
        self.assertFalse(pump.active())

    def test_a_failing_factory_leaves_the_recording_untouched(self):
        def broken(*args, **kwargs):
            raise ValueError("transport trust unavailable")
        self.sim.runtime.qwen_live = broken
        self.sim.runtime._start_qwen_live(self.session)
        self.assertEqual(self.sim.runtime._qwen_live_status(self.sim.sid), {"status": "qwen_live_unavailable"})

    def test_both_arms_get_the_identical_scene_record_and_coverage(self):
        self.sim.runtime._start_qwen_live(self.session)
        for age in (9.0, 5.0, 1.0):
            self.receipt(age)
        self.offer()
        sent, states = self.packets()
        self.assertEqual(states, {"a": "completed", "b": "completed"})
        scenes = []
        for packet in sent:
            records = [r for r in packet["state"]["evidence"] if r.get("source_kind") == "qwen.observation"]
            self.assertEqual(len(records), 1)
            scenes.append({k: v for k, v in records[0].items() if k != "arm"})
            context = packet["state"]["semantic"]["context"]
            self.assertEqual(context["coverage"]["qwen_scene"], "supplied")
            self.assertEqual(packet["state"]["semantic"]["qwen_scene"]["latest_facts"]["activity_hypothesis"], "reading")
        self.assertEqual(scenes[0], scenes[1])
        self.assertEqual(sent[0]["state"]["semantic"]["context"], sent[1]["state"]["semantic"]["context"])

    def test_a_new_scene_receipt_alone_never_triggers_a_call(self):
        self.sim.runtime._start_qwen_live(self.session)
        self.receipt(2.0)
        self.offer()
        before = len(self.packets()[0])
        self.sim.advance(3)
        self.receipt(.5)
        self.sim.tick()
        self.sim.tick()
        self.assertEqual(len(self.packets()[0]), before)
        skipped = self.sim.runtime.reasoning_scheduler_status(self.sim.sid)["skipped"]
        self.assertGreater(skipped["unchanged_evidence"]["a"] + skipped["context_only"]["a"], 0)

    def test_without_the_household_grant_no_scene_leaves_and_dispatch_continues(self):
        self.sim.runtime._start_qwen_live(self.session)
        self.receipt(1.0)
        self.granted["qwen"] = False
        self.offer()
        sent, states = self.packets()
        self.assertEqual(states, {"a": "completed", "b": "completed"})
        for packet in sent:
            self.assertFalse([r for r in packet["state"]["evidence"] if r.get("source_kind") == "qwen.observation"])
            self.assertEqual(packet["state"]["semantic"]["context"]["coverage"]["qwen_scene"], "not_granted")

    def test_coverage_without_a_pump_or_receipts_and_with_a_withheld_arm(self):
        self.offer()  # the family is granted but no pump runs for this recording
        sent, _ = self.packets()
        self.assertEqual({p["state"]["semantic"]["context"]["coverage"]["qwen_scene"] for p in sent}, {"not_provisioned"})
        self.sim.runtime._start_qwen_live(self.session)
        self.sim.advance(3)
        self.offer()
        sent, _ = self.packets()
        self.assertEqual({p["state"]["semantic"]["context"]["coverage"]["qwen_scene"] for p in sent[-2:]}, {"stale"})
        self.receipt(1.0)
        self.sim.runner.source_policy["qwen_scene"] = {"a": "withheld_by_experiment", "b": "granted"}
        self.sim.runner.lever_families = ["qwen_scene"]
        self.sim.advance(3)
        self.offer()
        sent, _ = self.packets()
        latest = {p["state"]["evidence"][0]["arm"]: p for p in sent[-2:]}
        has_scene = {arm: any(r.get("source_kind") == "qwen.observation" for r in p["state"]["evidence"]) for arm, p in latest.items()}
        self.assertEqual(has_scene, {"a": False, "b": True})
        coverage = {arm: p["state"]["semantic"]["context"]["coverage"]["qwen_scene"] for arm, p in latest.items()}
        self.assertEqual(coverage, {"a": "withheld_by_experiment", "b": "supplied"})
        digests = {arm: p["state"]["semantic"]["context"]["shared_context_digest"] for arm, p in latest.items()}
        self.assertEqual(digests["a"], digests["b"])

    def test_graph_shows_the_scene_source_with_coverage_and_tokens_only(self):
        import graph as lab_graph
        self.sim.runtime._start_qwen_live(self.session)
        self.receipt(1.0)
        self.offer()
        node = next(s for s in lab_graph.graph(self.sim.store, self.sim.sid)["sources"] if s["id"] == "qwen_scene")
        self.assertEqual(node["consumed_by"], {"a": "consumed", "b": "consumed"})
        self.assertEqual(node["version"]["coverage"], {"a": "supplied", "b": "supplied"})
        self.assertEqual(node["families"], [19])
        snippet = json.dumps(node["native"]["snippet"])
        self.assertIn("qwen-scene-projection-1", snippet)
        for private in ("media:", "b" * 64, "epoch-1", "request_id"):
            self.assertNotIn(private, snippet)


class ProvisioningTests(unittest.TestCase):
    BLOCK = {"transport_file": "/etc/perception-lab/qwen-live-transport.json", "artifact_sha256": "a" * 64,
             "runtime_revision": "vllm-0.11.0", "enabled": True}

    def test_block_is_exact_and_bounded(self):
        from live_detector_app import check_qwen_live
        self.assertEqual(check_qwen_live(self.BLOCK)["interval_seconds"], 4.0)
        self.assertEqual(check_qwen_live({**self.BLOCK, "interval_seconds": 5, "horizon_seconds": 60})["horizon_seconds"], 60)
        occupancy = {"occupancy_entity": "binary_sensor.den_oak_person_occupancy", "occupancy_hold_seconds": 15,
                     "occupancy_grace_seconds": 3}
        self.assertNotIn("occupancy_entity", check_qwen_live({**self.BLOCK, **occupancy}))  # gate settings, not pump settings
        # S1.4 widened bounds; the defaults stay 4 s and 15/min.
        self.assertEqual(check_qwen_live({**self.BLOCK, "interval_seconds": 1.0, "max_per_minute": 90})["max_per_minute"], 90)
        for change in ({"transport_file": "relative.json"}, {"artifact_sha256": "x"}, {"enabled": "yes"}, {"interval_seconds": .4},
                       {"max_per_minute": 121}, {"frames": 6}, {"horizon_seconds": 90}, {"timezone": "../etc"}, {"extra": 1},
                       {"occupancy_entity": "person.owner"}, {"occupancy_hold_seconds": 31}, {"occupancy_grace_seconds": -1}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                check_qwen_live({**self.BLOCK, **change})
        with self.assertRaises(ValueError):
            check_qwen_live({k: v for k, v in self.BLOCK.items() if k != "enabled"})

    def test_factory_builds_per_recording_pumps_gated_by_the_household_grant(self):
        from live_detector_app import qwen_live_factory
        from test_qwen_live import TRANSPORT_CONFIG
        transport = {**TRANSPORT_CONFIG}
        reasoning = type("Factory", (), {})()
        reasoning.qwen_granted = lambda: True
        made = []

        def transport_factory(config):
            made.append(config)
            return type("T", (), {"config": config, "close": lambda self: None})()
        with patch("live_detector_app.private_json", return_value=transport):
            self.assertIsNone(qwen_live_factory({"qwen_live": {**self.BLOCK, "enabled": False}}))
            factory = qwen_live_factory({"qwen_live": self.BLOCK}, reasoning_factory=reasoning, transport_factory=transport_factory)
            with self.assertRaisesRegex(ValueError, "5-8 seconds"):
                with patch("live_detector_app.private_json", return_value={**transport, "deadline_seconds": 12}):
                    qwen_live_factory({"qwen_live": self.BLOCK}, transport_factory=transport_factory)
        self.assertTrue(factory.kill_switch())
        reasoning.qwen_granted = lambda: False
        self.assertFalse(factory.kill_switch())
        self.assertEqual(factory.horizon_seconds, 120)
        session = {"id": "s" * 32, "site_id": "victoria", "camera_id": "den", "state": "recording"}
        pump = factory(None, session, active=lambda: True, enabled=lambda: True, occupied=lambda: {"state": "vacant"})
        self.assertEqual(pump.occupied(), {"state": "vacant"})
        self.assertEqual(factory.occupancy, {"entity": None, "hold_seconds": 10.0, "grace_seconds": 5.0})
        self.assertEqual(pump.base_profile["adapter_revision"], scene.PROMPT_VERSION)
        self.assertEqual(pump.base_profile["artifact_sha256"], "a" * 64)
        self.assertEqual(len(made), 1)

    def test_runtime_refuses_the_perceiver_in_recording_only_mode(self):
        from service_runtime import ServiceRuntime
        with self.assertRaises(ValueError):
            ServiceRuntime(object(), {}, None, None, recording_only=True, qwen_live=lambda *a, **k: None)


if __name__ == "__main__":
    unittest.main()
