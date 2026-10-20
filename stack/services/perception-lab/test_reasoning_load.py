"""Live JEV reasoning load: a Victoria-shaped synthetic session, measured.

Production evidence (session d6f7dff2..., placement-best at 1 Hz sampling, one
CPU core): with reasoning on the controller saturated, 152 JEV requests and 63
replaced-pending receipts were written in ~85 s for ~108 perception outputs.
The scheduler offered on every output signal and every 1 s timer, re-requesting
unchanged evidence histories.

This module rebuilds that load deterministically: a simulated ledger clock,
~2 Hz preview media, two arms of detector+pose perception.output with the
recorded payload shape and size (~13 KB: 4 boxes/objects, one 17-joint pose,
processing provenance, arm snapshot), filler files standing in for the Lab's
media tree (reconciliation scans walk it), and the real LiveReasoning,
JevProvider and LabJevPolicy with a local fake transport and a synchronous
pool. Nothing leaves the process.

Workstation measurements (this module's Simulation, 90 s, 3600 filler files,
no profiler), per perception output, reasoning thread:
  before: 171 requests / 178 replaced for 118 outputs, 41-48 ms per output
  after:  118 requests / 0 replaced for 118 outputs, ~19 ms per output
That remaining cost was dominated by the exact quota scan (three per request:
admission, egress and publication). Quota checks now use the Store's exact
running total (test_storage_scan); on the same 90 s run with a 0.2 s recorder
capacity poll, process CPU per recorded second fell from ~63 ms (1456 full
scans) to ~19 ms at a 1 s offer interval and ~13 ms at the 2 s default
(2 full scans), ~6 ms and ~3 ms reasoning CPU per output.
"""
import json
import os
import random
import tempfile
import threading
import time
import types
import unittest
from concurrent.futures import Future
from pathlib import Path

from config import Settings
from store import Store
import live_reasoning
from live_reasoning import LiveReasoning
from jev_provider import JevProvider
from jev_policy import LabJevPolicy
from service_runtime import ServiceRuntime
import graph as graph_module

# Budget for the reasoning thread's CPU per perception output at the test scale
# below (30 s, 1500 filler files) on the development workstation: measured
# ~12 ms after this change and ~25 ms before it. Set
# PERCEPTION_LAB_SKIP_CPU_BUDGET=1 on a slower or loaded host; the request-count
# assertions always run.
REASONING_CPU_BUDGET_MS = 20.0


class SyncPool:
    """LiveReasoning's executor, run inline so one thread does all the work."""
    def submit(self, fn, *args):
        future = Future()
        try:
            future.set_result(fn(*args))
        except BaseException as error:  # surfaced through the ledger state
            future.set_exception(error)
        return future

    def shutdown(self, *args, **kwargs):
        pass


class StubRunner:
    def __init__(self):
        self.generation = 1
        self.reasoning_windows = {"a": 10, "b": 10}
        self.ha_evidence = {"a": True, "b": True}
        self.caption_evidence = {"a": False, "b": False}
        self.spatial_policy = {arm: {"localization": "none", "room_relations": False} for arm in ("a", "b")}
        self.lock = threading.RLock()
        self.futures = set()
        self.closed = False

    def snapshot(self):
        return {"active": {"a": None, "b": None}, "pending": {"a": None, "b": None}, "closed": self.closed}

    def close(self):
        self.closed = True


def _hex(rng, n):
    return "".join(rng.choice("0123456789abcdef") for _ in range(n))


def perception_payload(rng, sid, arm, *, now, started, frame_id, media_id, request_id, latency=1.5):
    """Shape and size of a recorded placement-best detector+pose output."""
    width, height = 1280, 720
    boxes, objects = [], []
    for i, label in enumerate(("person", "book", "cup", "chair")):
        box = {"confidence": round(rng.uniform(.3, .95), 6), "h": round(rng.uniform(50, 300), 4), "instance_id": f"det-{i}-{_hex(rng, 8)}",
               "label": label, "w": round(rng.uniform(40, 300), 4), "x": round(rng.uniform(10, 900), 4), "y": round(rng.uniform(10, 400), 4)}
        boxes.append(box)
        objects.append({"confidence": box["confidence"], "instance_id": box["instance_id"], "label": label})
    person = boxes[0]
    joints = [{"joint_id": j, "name": f"joint_{j}", "pixel": [round(person["x"]+rng.uniform(0, person["w"]), 3), round(person["y"]+rng.uniform(0, person["h"]), 3)],
               "score": round(rng.uniform(.2, .99), 6), "status": "supported" if j % 3 else "uncertain", "visibility": "unknown"} for j in range(17)]
    keypoints = [{"instance_id": person["instance_id"], "joint_id": j["joint_id"], "score": j["score"], "x": j["pixel"][0], "y": j["pixel"][1]} for j in joints]
    edges = [[i, i+1] for i in range(16)]
    processing = {"correlated_evidence": True, "crop_policy": {"max_people": 2, "minimum_confidence": .3, "revision": "crop-policy-3"},
                  "detector": {"artifact_sha256": _hex(rng, 64), "checkpoint": "detector-" + _hex(rng, 12), "device_id": "fixture-jetson",
                               "engine_epoch": "engine-" + _hex(rng, 32), "input_format": "rgb_uint8_nhwc", "precision": "fp16",
                               "preprocessing": {"letterbox": True, "size": [640, 640], "mean": [0, 0, 0], "std": [1, 1, 1], "revision": "pp-2"},
                               "rect": False, "runtime": "tensorrt", "runtime_version": "10.3.0"},
                  "manifest_sha256": _hex(rng, 64), "model_version": "placement-best-7", "pipeline": "detector_then_pose",
                  "pose": {"artifact_sha256": _hex(rng, 64), "checkpoint": "pose-" + _hex(rng, 12), "crop_count": 1,
                           "crop_reference": {"frame_id": frame_id, "digest": _hex(rng, 64), "kind": "person_crop"},
                           "crop_set_digest": _hex(rng, 64), "device_id": "fixture-jetson", "input_format": "bgr_float32",
                           "input_size_wh": [192, 256], "layout": "coco17", "model_call_ms": round(rng.uniform(40, 90), 3),
                           "model_input_channel_order": "bgr", "model_invoked": True, "onnxruntime_version": "1.19.2", "precision": "fp32",
                           "preprocessing_revision": "pose-pp-4", "runtime": "onnxruntime-cpu",
                           "runtime_threads": {"intra_op": 1, "inter_op": 1, "affinity": "none"}, "runtime_version": "1.19.2",
                           "score_threshold": .3, "source_sha256": {"frame": _hex(rng, 64), "crop": _hex(rng, 64), "model": _hex(rng, 64), "config": _hex(rng, 64)}},
                  "pose_preprocessing_revision": "pose-pp-4", "shared_source_request": _hex(rng, 32)}
    result = {"boxes": boxes, "coordinate_space": "source_pixels", "frame_id": frame_id, "keypoints": keypoints, "objects": objects,
              "pose_instances": [{"crop_xyxy": [person["x"], person["y"], person["x"]+person["w"], person["y"]+person["h"]],
                                  "instance_id": person["instance_id"], "joints": joints, "layout": "coco17",
                                  "score_semantics": "SimCC_response_not_probability"}],
              "processing": processing, "quality": {"coordinate_space": "source_pixels", "image_height": height, "image_width": width,
                                                    "metric_depth": "not_supplied", "pose_score_semantics": "SimCC_response_not_probability"},
              "schema_version": 1, "skeleton_edges": edges,
              "stage_timings_ms": {"detector": {"inference": 31.2, "postprocess": 2.1, "preprocess": 4.4}, "pose": {"model_call": 61.0}}}
    observed = now-latency
    snapshot = {"boxes": boxes, "frame": {"captured_at": observed, "generation": 0, "height": height, "id": frame_id, "media_id": media_id,
                                          "session_time_s": observed-started, "site_id": "victoria", "width": width},
                "frame_id": frame_id, "id": arm.upper(), "keypoints": keypoints,
                "layers": {"boxes": {"available": True}, "labels": {"available": True}, "skeleton": {"available": True}},
                "observations": ["person", "book", "cup", "chair"],
                "pose_summary": [{"depth": "not_supplied", "instance_id": person["instance_id"], "joint_count": 17, "layout": "coco17",
                                  "overlay": "verified", "scores": "SimCC_response_not_probability", "supported_joints": 11}],
                "profile": {"id": "placement-best-" + arm, "name": "placement-best-7", "runs_on": "fixture-jetson"},
                "relations": [], "skeleton_edges": edges, "status": "running",
                "timing": {"source_clock_uncertainty_ms": 12.5, "source_to_controller_ms": 1500.0, "stages_ms": {}}, "validation": []}
    return {"arm": arm, "arm_snapshot": snapshot, "artifact_sha256": _hex(rng, 64), "available_at": now, "available_at_s": now-started,
            "camera_id": "den", "capture_epoch": "capture-" + _hex(rng, 28), "completed_at": now, "current": True,
            "engine_epoch": "engine-" + _hex(rng, 32), "frame_id": frame_id, "generation": 1, "media_ids": [media_id], "mode": "live",
            "model_version": "placement-best-7", "observed_at": observed, "request_id": request_id, "result": result,
            "run_id": "specialist" + arm.upper(), "site_id": "victoria", "source_end_s": observed-started, "state": "completed",
            "status": "completed", "valid_until": observed+3}


def fake_transport(body, token, timeout):
    packet = json.loads(body)
    answers = {}
    for key, question in packet["questions"].items():
        if question["type"] == "noul":
            answers[key] = {"type": "noul", "noul": .2}
        else:
            answers[key] = {"type": "choice", "choice": "insufficient_evidence", "confidence": .6,
                            "probabilities": {k: 1.0 if k == "insufficient_evidence" else 0.0 for k in question["criteria"]}}
    return json.dumps({"model": "jev-1.13.0", "answers": answers, "usage": {"input_tokens": 1000, "output_tokens": 20}}).encode()


class Simulation:
    """A recording session driven on a simulated ledger clock."""

    def __init__(self, root, *, preview_hz=2.0, output_hz=0.65, filler_files=1500, seed=7, offer_interval=1.0):
        self.root = Path(root)
        self.now = 1_790_000_000.0
        self.preview_hz, self.output_hz = preview_hz, output_hz
        self.rng = random.Random(seed)
        filler = self.root/"media"/("f"*32)
        filler.mkdir(parents=True, exist_ok=True)
        for i in range(filler_files):
            (filler/f"{i:08d}.bin").write_bytes(b"x")
        self.store = Store(Settings(storage_root=self.root, min_free_bytes=0), clock=lambda: self.now)
        self.timers = []
        self.cpu = {"reasoning": 0.0, "live": 0.0, "graph": 0.0}
        self.calls = {k: 0 for k in self.cpu}
        self.outputs = {"a": 0, "b": 0}
        self.jpeg = bytes(self.rng.getrandbits(8) for _ in range(4096))*14
        self.media_id = None
        self.frame = 0
        self._saved = live_reasoning.threading
        live_reasoning.threading = types.SimpleNamespace(RLock=threading.RLock, Timer=self._timer, Thread=threading.Thread)
        store = self.store
        session, _ = store.create_session("den", "placement-best", "Load", "load-request", reasoning_enabled=True)
        self.sid = session["id"]
        store.transition(self.sid, "starting")
        store.transition(self.sid, "recording")
        self.started = store.get_session(self.sid)["started_at"]
        # The scheduler tests here pin the 1 s offer interval they were written
        # for; the configured default (2 s) is covered in test_reasoning_schedule.
        self.runtime = ServiceRuntime(store, {}, lambda *a: None, None, reasoning_factory=self.factory,
                                      reasoning_min_offer_interval_s=offer_interval)
        from runtime import PairedRunner
        profiles = {arm: {"profile_id": "p"+arm, "artifact_sha256": "a"*64, "model_version": "m", "engine_epoch": "e"} for arm in ("a", "b")}
        tables = PairedRunner(store, self.sid, profiles, lambda request: None, clock=store.clock)  # creates the paired tables
        tables.close()
        tables.pool.shutdown(wait=True)
        with store.transaction() as db:
            db.execute("INSERT INTO lab_experiment_state VALUES(?,?,?)", (self.sid, "placement-best", 1))
        store.append_event(self.sid, "experiment.activated", {"site_id": "victoria", "camera_id": "den", "generation": 1,
            "experiment_id": "placement-best", "effective_at": self.started, "reasoning_windows": {"a": 10, "b": 10},
            "sampling_hz": {"a": 1, "b": 1}, "spatial_policy": StubRunner().spatial_policy, "ha_evidence": {"a": True, "b": True},
            "caption_evidence": {"a": False, "b": False}, "profiles": profiles})
        self.runner = self.runtime.runners[self.sid] = StubRunner()

    def _timer(self, delay, fn, args=()):
        sim = self

        class SimTimer:
            daemon = True

            def start(inner):
                inner.due, inner.fn, inner.args = sim.now+delay, fn, args
                sim.timers.append(inner)

            def cancel(inner):
                if inner in sim.timers:
                    sim.timers.remove(inner)
        return SimTimer()

    def factory(self, store, sid):
        policy = {"schema_version": 1, "enabled": True, "scope": "household_structured", "revision": "load", "generation": 1,
                  "expires_at": self.now+10_000, "site_id": "victoria", "camera_ids": ["den"], "model_selector": "jev-1.13",
                  "max_calls_per_minute": 120}
        provider = JevProvider(model_selector="jev-1.13", response_version="jev-1.13.0", allow_recent_history=True, semantic=True,
                               authorize=LabJevPolicy(store, lambda: dict(policy), lambda: {"state": "on", "received_at": store.clock()}),
                               credential=lambda: "fixture", transport=fake_transport, clock=store.clock)
        reasoner = LiveReasoning(store, sid, provider)
        reasoner.pool.shutdown(wait=False)
        reasoner.pool = SyncPool()
        return reasoner

    def measure(self, kind, fn, *args, **kwargs):
        start = time.thread_time()
        try:
            return fn(*args, **kwargs)
        finally:
            self.cpu[kind] += time.thread_time()-start
            self.calls[kind] += 1

    def preview(self):
        self.frame += 1
        self.media_id = self.store.add_media(self.sid, self.jpeg, "image/jpeg", {"stream": "rgb", "frame_id": f"frame-{self.frame}",
            "observed_at": self.now-.2, "available_at": self.now, "valid_until": self.now+2.8})["id"]

    def output(self, arm):
        """One completed paired request of `arm`, recorded as production does."""
        store, now = self.store, self.now
        if self.media_id is None:
            self.preview()
        request_id = _hex(self.rng, 32)
        store.append_event(self.sid, "perception.offered", {"arm": arm, "generation": 1, "frame_id": f"frame-{self.frame}", "request_id": request_id})
        with store.transaction() as db:
            db.execute("INSERT INTO paired_requests VALUES(?,?,?,?,?,?,?,?,?)", (request_id, self.sid, arm, 1, "d"*64,
                json.dumps({"frame": {"valid_until": now+1.5, "frame_id": f"frame-{self.frame}"}, "padding": "q"*1500}),
                "completed", json.dumps({"result": {"objects": []}}), now))
            db.execute("INSERT INTO paired_dispatch VALUES(?,?,?,?,?)", (request_id, self.sid, arm, 1, now-.4))
        store.append_event(self.sid, "perception.delivery", {"arm": arm, "generation": 1, "request_id": request_id, "state": "completed",
            "encode_ms": 3.1, "transport_ms": 40.2, "body_bytes": 56000, "dispatched_at": now-.4, "padding": "p"*8000})
        store.append_event(self.sid, "perception.output", perception_payload(self.rng, self.sid, arm, now=now, started=self.started,
            frame_id=f"frame-{self.frame}", media_id=self.media_id, request_id=request_id))
        store.append_event(self.sid, "perception.dropped", {"arm": arm, "generation": 1, "reason": "source_sampling_cadence"})
        self.outputs[arm] += 1

    def tick(self):
        self.measure("reasoning", self.runtime.reasoning_tick)

    def advance(self, seconds):
        """Move the ledger clock, firing LiveReasoning's due dispatch timers."""
        self.now += seconds
        for timer in [t for t in self.timers if t.due <= self.now]:
            if timer in self.timers:
                self.timers.remove(timer)
                self.measure("reasoning", timer.fn, *timer.args)

    def run(self, seconds, *, live_period=0.5, graph_period=3.0, outputs=True, step=0.05):
        """Production cadence: preview media, outputs per arm, a scheduler tick
        on each completed output and at least once a second, UI polls."""
        end = self.now+seconds
        t0 = self.now
        next_preview, next_output = 0.0, {"a": 0.3, "b": 0.6}
        last_tick, next_live, next_graph = -1.0, 0.0, 0.0
        while self.now < end:
            t = self.now-t0
            signalled = False
            if t >= next_preview:
                next_preview += 1/self.preview_hz
                self.preview()
            for arm in ("a", "b"):
                if outputs and t >= next_output[arm]:
                    next_output[arm] += 1/self.output_hz
                    self.output(arm)
                    signalled = True
            if signalled or t-last_tick >= 1.0:
                last_tick = t
                self.tick()
            if live_period and t >= next_live:
                next_live += live_period
                self.measure("live", self.runtime.live, self.store.get_session(self.sid))
            if graph_period and t >= next_graph:
                next_graph += graph_period
                self.measure("graph", graph_module.graph, self.store, self.sid)
            self.advance(step)

    def requests(self):
        with self.store.lock:
            return dict(self.store.db.execute("SELECT state,count(*) FROM lab_reasoning_requests GROUP BY state").fetchall())

    def events(self, kind):
        with self.store.lock:
            return self.store.db.execute("SELECT count(*) FROM events WHERE session_id=? AND kind=?", (self.sid, kind)).fetchone()[0]

    def close(self):
        live_reasoning.threading = self._saved
        for reasoner in self.runtime.reasoners.values():
            reasoner.close()
        self.runtime.closed = True
        self.store.close()


class ReasoningLoadTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.sim = Simulation(self.directory.name)

    def tearDown(self):
        self.sim.close()
        self.directory.cleanup()

    def test_one_request_per_new_output_zero_for_unchanged_evidence_within_cpu_budget(self):
        sim = self.sim
        sim.run(30)
        outputs = sum(sim.outputs.values())
        self.assertGreaterEqual(outputs, 38)
        # Exactly one request per new output of each arm; no pending request is
        # created only to be replaced by the next timer tick.
        self.assertEqual(sim.events("reasoning.request"), outputs)
        self.assertEqual(sim.requests(), {"completed": outputs})
        self.assertEqual(sim.events("reasoning.lifecycle"), 0)
        status = sim.runtime.reasoning_scheduler_status(sim.sid)
        self.assertEqual(status["offered"], sim.outputs)
        self.assertGreater(sum(status["skipped"]["unchanged_evidence"].values()), 0, "timer ticks were counted, not requested")
        before = sim.requests()
        # Evidence stops: the 1 s scheduler keeps ticking but asks nothing.
        sim.run(12, outputs=False, live_period=0, graph_period=0)
        self.assertEqual(sim.requests(), before)
        self.assertEqual(sim.events("reasoning.lifecycle"), 0)
        after = sim.runtime.reasoning_scheduler_status(sim.sid)
        # Both arms skip each tick while the last outputs remain in the window.
        self.assertGreaterEqual(sum(after["skipped"]["unchanged_evidence"].values())-sum(status["skipped"]["unchanged_evidence"].values()), 10)
        self.assertEqual(after["offered"], status["offered"])
        per_output = 1000*sim.cpu["reasoning"]/outputs
        if os.environ.get("PERCEPTION_LAB_SKIP_CPU_BUDGET") != "1":
            self.assertLess(per_output, REASONING_CPU_BUDGET_MS,
                            f"reasoning CPU {per_output:.1f} ms per output exceeds the documented workstation budget")

    def test_live_status_reports_scheduler_counters_and_stop_writes_one_summary(self):
        sim = self.sim
        sim.run(6)
        live = sim.runtime.live(sim.store.get_session(sim.sid))
        self.assertEqual(live["reasoning_scheduler"]["basis"], "new_admitted_camera_evidence")
        self.assertEqual(live["reasoning_scheduler"]["offered"], sim.outputs)
        sim.runtime.stop_session(sim.store.get_session(sim.sid))
        sim.runtime.stop_session(sim.store.get_session(sim.sid))  # a retried stop
        with sim.store.lock:
            rows = sim.store.db.execute("SELECT payload FROM events WHERE session_id=? AND kind='reasoning.scheduler'", (sim.sid,)).fetchall()
        self.assertEqual(len(rows), 1, "one summary per session")
        summary = json.loads(rows[0][0])
        self.assertEqual(summary["offered"], sim.outputs)
        self.assertEqual(set(summary), {"basis", "min_offer_interval_s", "ticks", "offered", "skipped", "available_at",
                                        "cadence", "offer_reasons", "local_abstains"})

    def test_repeated_live_graph_polls_reuse_event_derived_projections(self):
        sim = self.sim
        sim.run(8, graph_period=0)
        calls = []
        original = graph_module._prepared_stage

        def counted(*args, **kwargs):
            calls.append(1)
            return original(*args, **kwargs)
        graph_module._prepared_stage = counted
        try:
            first = graph_module.graph(sim.store, sim.sid)
            built = len(calls)
            second = graph_module.graph(sim.store, sim.sid)
        finally:
            graph_module._prepared_stage = original
        self.assertEqual(built, 2)
        self.assertEqual(len(calls), built, "the same admitted requests are not projected again")
        # Same ledger clock: the reads must be identical, cached or not.
        encoded = lambda g: json.dumps(g, sort_keys=True)
        self.assertEqual(encoded(first), encoded(second))
        graph_module._memos.clear()
        self.assertEqual(encoded(first), encoded(graph_module.graph(sim.store, sim.sid)), "a cold read builds the same projection")
        self.assertLessEqual(len(graph_module._memos.get(sim.store, {})), graph_module.MEMO_ITEMS)


def reference_snippet(value, budget=graph_module.SNIPPET_BYTES):
    """The snippet algorithm before the early-abort rungs, kept as the oracle."""
    from store import encode
    TRUNCATED = graph_module.TRUNCATED

    def clean(item, redacted):
        if isinstance(item, dict):
            result = {}
            for key, inner in item.items():
                key = str(key)
                if graph_module._secret(key):
                    redacted[0] += 1
                    result[key] = "[redacted]"
                else:
                    result[key] = clean(inner, redacted)
            return result
        if isinstance(item, list):
            return [clean(inner, redacted) for inner in item]
        if isinstance(item, str) and (graph_module._PATH.match(item) or graph_module._BLOB.match(item)):
            redacted[0] += 1
            return "[redacted]"
        return item

    def bound(item, items, chars, depth):
        if isinstance(item, dict):
            keys = list(item)
            if depth <= 0:
                return {TRUNCATED: f"{len(keys)} keys"} if keys else {}
            result = {key: bound(item[key], items, chars, depth-1) for key in keys[:items*2]}
            if len(keys) > items*2:
                result[TRUNCATED] = f"+{len(keys)-items*2} keys"
            return result
        if isinstance(item, list):
            if depth <= 0:
                return [f"{TRUNCATED}:{len(item)} items"] if item else []
            result = [bound(inner, items, chars, depth-1) for inner in item[:items]]
            if len(item) > items:
                result.append(f"{TRUNCATED}:+{len(item)-items} items")
            return result
        if isinstance(item, str) and len(item) > chars:
            return item[:chars]+"\u2026"+TRUNCATED
        return item
    redacted = [0]
    cleaned = clean(value, redacted)
    size = len(encode(cleaned).encode())
    if size <= budget:
        return {"json": cleaned, "bytes": size, "truncated": False, "redacted": redacted[0]}
    for items, chars, depth in ((16, 240, 8), (8, 120, 6), (4, 80, 5), (3, 48, 4), (2, 32, 3), (1, 24, 2), (1, 16, 1)):
        bounded = bound(cleaned, items, chars, depth)
        if len(encode(bounded).encode()) <= budget:
            return {"json": bounded, "bytes": size, "truncated": True, "redacted": redacted[0]}
    return {"json": {TRUNCATED: "exceeds_snippet_budget"}, "bytes": size, "truncated": True, "redacted": redacted[0]}


class SnippetEquivalenceTests(unittest.TestCase):
    def value(self, rng, depth=0):
        kind = rng.random()
        if depth > 3 or kind < .35:
            return rng.choice([rng.randint(-10**6, 10**6), rng.random()*1e4, True, False, None,
                               "x"*rng.randint(0, 400), "\u00e9\u2026"*rng.randint(0, 90), "/home/private/file", "A"*600,
                               "".join(rng.choice('abc "\\\n') for _ in range(rng.randint(0, 60)))])
        if kind < .7:
            return [self.value(rng, depth+1) for _ in range(rng.choice((0, 1, 3, 20, 40) if depth < 2 else (0, 1, 3)))]
        keys = ["k%d" % i for i in range(rng.choice((0, 2, 9, 40) if depth < 2 else (0, 2)))]+rng.sample(["token", "path", "note", "id", "api_token", "\u00fcber"], 2)
        return {key: self.value(rng, depth+1) for key in keys}

    def test_early_abort_rungs_return_exactly_the_reference_snippet(self):
        rng = random.Random(21)
        for budget in (64, 512, graph_module.SNIPPET_BYTES):
            for _ in range(250):
                value = self.value(rng)
                self.assertEqual(graph_module.snippet(value, budget), reference_snippet(value, budget))
        # And a recorded request packet, the largest graph snippet.
        directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        sim = Simulation(directory.name, filler_files=0)
        try:
            sim.run(6, live_period=0, graph_period=0)
            with sim.store.lock:
                packets = [json.loads(r[0])["request"] for r in sim.store.db.execute("SELECT payload FROM events WHERE kind='reasoning.request'")]
            self.assertTrue(packets)
            for packet in packets:
                self.assertEqual(graph_module.snippet(packet), reference_snippet(packet))
                self.assertEqual(graph_module.snippet(packet["state"]["evidence"]), reference_snippet(packet["state"]["evidence"]))
        finally:
            sim.close()
            directory.cleanup()


if __name__ == "__main__":
    unittest.main()
