"""Graph, timeline and replay reads: causal, bounded, read-only and honest."""
import asyncio
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import graph as lab_graph
from config import Settings
from reasoning import canonical, digest
from store import LabError, Store, encode

START = 1000.0


class Ledger:
    """Synthetic ledger shaped like the real writers (runtime, live_reasoning, jev_policy, ingestors)."""

    def __init__(self, directory, *, token=None, capabilities=("read",)):
        grants = ()
        if token:
            grants = ({"token_sha256": hashlib.sha256(token.encode()).hexdigest(), "site_id": "victoria",
                       "subject": "fixture", "capabilities": list(capabilities)},)
        self.settings = Settings(storage_root=Path(directory), min_free_bytes=0, cameras=({"id": "den"},), token_grants=grants)
        self.now = START
        self.store = Store(self.settings, clock=lambda: self.now)
        self.session, _ = self.store.create_session("den", "placement-best", "Fixture", "request1", True)
        self.sid = self.session["id"]
        self.store.transition(self.sid, "starting")
        self.store.transition(self.sid, "recording")
        with self.store.transaction() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS paired_requests(id TEXT PRIMARY KEY, session_id TEXT NOT NULL, arm TEXT NOT NULL,
                generation INTEGER NOT NULL, digest TEXT NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL, result TEXT, updated_at REAL NOT NULL)""")
            db.execute("CREATE INDEX IF NOT EXISTS paired_requests_arm_state ON paired_requests(session_id,arm,generation,state,updated_at)")
            db.execute("""CREATE TABLE IF NOT EXISTS lab_reasoning_requests(id TEXT PRIMARY KEY, session_id TEXT NOT NULL, arm TEXT NOT NULL,
                generation INTEGER NOT NULL, state TEXT NOT NULL, payload TEXT NOT NULL, dispatched_at REAL, result TEXT)""")
        self.sequence = 0

    def at(self, t):
        self.now = START+t
        return self

    def event(self, kind, payload):
        with self.store.transaction() as db:
            return self.store._event(db, self.sid, kind, payload)

    def activate(self, generation=1, *, t=0.5, sampling=None, ha=None, caption=None, spatial=None):
        self.at(t).event("experiment.activated", {"site_id": "victoria", "camera_id": "den", "generation": generation,
            "experiment_id": "placement-best", "effective_at": self.now, "reasoning_windows": {"a": 10, "b": 10},
            "sampling_hz": sampling or {"a": 2, "b": 2}, "spatial_policy": spatial or {"a": {"localization": "none"}, "b": {"localization": "none"}},
            "ha_evidence": ha or {"a": True, "b": True}, **({"caption_evidence": caption} if caption else {}),
            "profiles": {arm: {"profile_id": "profile-"+arm, "model_version": "fixture-"+arm, "artifact_sha256": arm*64,
                               "engine_epoch": "engine-"+arm} for arm in ("a", "b")}})

    def frame(self, t, *, observed=None):
        self.at(t)
        observed = START+(observed if observed is not None else t-0.3)
        self.sequence += 1
        meta = {"site_id": "victoria", "camera_id": "den", "capture_epoch": "capture1", "frame_id": f"frame-{self.sequence}",
                "stream": "rgb", "width": 640, "height": 360, "format": "jpeg", "observed_at": observed, "available_at": self.now,
                "valid_until": observed+3, "clock_mapping_status": "verified", "clock_mapping_id": "clock1",
                "session_time_s": observed-START, "source_timing_receipt": {"frame_device_ns": 1}}
        return self.store.add_media(self.sid, b"jpeg-%d" % self.sequence, "image/jpeg", meta, "frame-%d" % self.sequence)

    def output(self, arm, t, *, generation=1, objects=2, poses=1, joints=17, valid=2.5, frame=None):
        """perception.delivery then perception.output in one transaction, as PairedRunner.reconcile writes them."""
        self.at(t)
        media = frame or self.frame(t)
        meta = media["metadata"]
        request_id = hashlib.sha256(f"{arm}{t}{generation}".encode()).hexdigest()
        result = {"schema_version": 1, "frame_id": meta["frame_id"], "coordinate_space": "source_pixels",
                  "objects": [{"label": "person" if i == 0 else "book", "score": 0.8, "x": 10+i, "y": 10, "w": 50, "h": 80} for i in range(objects)],
                  "pose_instances": [{"instance_id": f"p{i}", "layout": "coco17", "joints": [{"joint_id": j, "x": 20.0+j, "y": 30.0, "score": 0.7,
                                      "status": "supported"} for j in range(joints)]} for i in range(poses)],
                  "timings_ms": {"preprocess": 1.5, "inference": 20.0, "postprocess": 2.0},
                  "stage_timings_ms": {"detector": {"inference": 12.0}, "pose": {"model_call": 8.0}},
                  "processing": {"pipeline": "detector-pose-v1", "checkpoint_path": "/home/lab/models/pose.onnx"}}
        delivery = {"request_id": request_id, "arm": arm, "state": "completed", "generation": generation, "current": True,
                    "completed_at": self.now, "observed_at": meta["observed_at"], "frame_id": meta["frame_id"],
                    "model_version": "fixture-"+arm, "result": result, "dispatched_at": self.now-0.4, "encode_ms": 3.0, "transport_ms": 90.0}
        output = {**{k: v for k, v in delivery.items() if k not in {"dispatched_at", "encode_ms", "transport_ms"}},
                  "status": "completed", "mode": "live", "run_id": "profile-"+arm, "site_id": "victoria", "camera_id": "den",
                  "capture_epoch": "capture1", "media_ids": [media["id"]], "artifact_sha256": arm*64, "engine_epoch": "engine-"+arm,
                  "available_at": self.now, "valid_until": meta["observed_at"]+valid,
                  "arm_snapshot": {"id": arm.upper(), "status": "running", "frame_id": meta["frame_id"], "validation": [],
                                   "frame": {"id": meta["frame_id"], "media_id": media["id"], "captured_at": meta["observed_at"],
                                             "session_time_s": meta["observed_at"]-START, "width": 640, "height": 360},
                                   "timing": {"stages_ms": {"inference": 20.0}, "source_to_controller_ms": 300.0}},
                  "source_end_s": meta["observed_at"]-START, "available_at_s": self.now-START}
        with self.store.transaction() as db:
            self.store._event(db, self.sid, "perception.delivery", delivery)
            event_id = self.store._event(db, self.sid, "perception.output", output)
            db.execute("INSERT INTO paired_requests VALUES(?,?,?,?,?,?,?,?,?)", (request_id, self.sid, arm, generation, "d",
                       canonical({"frame": {"frame_id": meta["frame_id"]}}), "completed", canonical({"result": {}}), self.now))
        return event_id

    def rejected(self, arm, t, reason="worker_ingress_busy", generation=1):
        self.at(t)
        with self.store.transaction() as db:
            db.execute("INSERT INTO paired_requests VALUES(?,?,?,?,?,?,?,?,?)", ("rej"+arm+str(t), self.sid, arm, generation, "d", "{}",
                       "rejected", canonical({"result": {"reason": reason}}), self.now))

    def reasoning(self, arm, t, *, answered_at=None, output=True, state="completed", status="ready", reason_row=None, evidence_ids=()):
        """reasoning.request (jev_policy) then reasoning.delivery (+ reasoning.output) as live_reasoning writes them."""
        self.at(t)
        request_id = hashlib.sha256(f"jev{arm}{t}".encode()).hexdigest()[:32]
        evidence = [{"id": str(e), "kind": "observation", "source_kind": "perception.output", "arm": arm, "generation": 1,
                     "site_id": "victoria", "camera_id": "den", "session_id": self.sid, "observed_at": START+t-1, "available_at": START+t-0.5,
                     "valid_until": START+t+1, "objects": [{"label": "book"}], "evidence_roots": [str(e)]} for e in evidence_ids]
        if arm == "a":
            evidence.append({"id": "ha1", "kind": "observation", "source_kind": "ha.observation", "arm": arm, "generation": 1,
                             "site_id": "victoria", "camera_id": "den", "session_id": self.sid, "observed_at": START+t-1,
                             "available_at": START+t-0.9, "valid_until": START+t+2, "ha": {"binary_sensor.den_occupancy": {"state": "on"}}})
        packet = {"model": "jev-model", "questions": {"activity": {"type": "choice"}, "reading_supported": {"type": "noul"}},
                  "state": {"site_id": "victoria", "observation_cutoff": START+t-0.2, "evidence": evidence,
                            "semantic": {"version": "den-semantics-2", "observations": []}}}
        self.event("reasoning.request", {"request_id": request_id, "arm": arm, "generation": 1, "available_at": self.now,
                                         "packet_sha256": digest(packet), "request": packet, "status": "admitted_before_transport"})
        if answered_at is None:
            return request_id
        self.at(answered_at)
        with self.store.transaction() as db:
            db.execute("INSERT INTO lab_reasoning_requests VALUES(?,?,?,?,?,?,?,?)", (request_id, self.sid, arm, 1, state,
                       canonical({"records": evidence}), START+t, canonical({"status": status, **({"reason": reason_row} if reason_row else {})})))
            self.store._event(db, self.sid, "reasoning.delivery", {"request_id": request_id, "arm": arm, "generation": 1, "state": state,
                                                                    "status": status, "current": True, "available_at": self.now})
            if output:
                probabilities = {"reading": 0.7, "phone_use": 0.1, "insufficient_evidence": 0.2}
                self.store._event(db, self.sid, "reasoning.output", {
                    "request_id": request_id, "site_id": "victoria", "arm": arm, "generation": 1, "mode": "live", "run_id": "jev"+arm.upper(),
                    "status": status, "provider_status": status, "fresh_on_arrival": True, "source_end_s": t-1,
                    "available_at_s": answered_at, "valid_until": START+t+1,
                    "judgment": {"activity": "reading", "confidence": 0.6, "probabilities": probabilities, "model_version": "jev-1.13.0",
                                 "provider": "JEV", "probability": 0.7, "distribution": [{"label": k, "p": v} for k, v in probabilities.items()],
                                 "diagnostics": {"request": packet, "answers": {"activity": {"choice": "reading", "probabilities": probabilities}},
                                                 "usage": {"input_tokens": 900, "output_tokens": 40}, "usage_status": "reported",
                                                 "projection_version": "structured-semantic-3", "question_version": "den-activity-5-semantic"}},
                    "policy_result": {"status": "needs_preference", "activity": "reading", "actuation": False},
                    "provenance": {"evidence_ids": [str(e) for e in evidence_ids]},
                    "prediction": {"time_basis": "session_relative_seconds", "start": t-1, "end": t+1, "available_at": answered_at,
                                   "valid_until": t+1, "activities": ["reading"], "confidence": 0.7, "mode": "live"}})
        return request_id

    def ha(self, t, *, valid=3):
        self.at(t)
        return self.event("ha.observation", {"observed_at": self.now-0.2, "ha": {"binary_sensor.den_occupancy": {"state": "on", "status": "known", "origin": "same_camera_visual"},
                                                                               "media_player.den_tv": {"state": None, "status": "unknown"}},
                                             "acquisition_duration_s": 0.1, "site_id": "victoria", "camera_id": "den", "session_id": self.sid,
                                             "available_at": self.now, "valid_until": self.now-0.2+valid, "evidence_roots": ["ha:src:1"],
                                             "snapshot_key": "ha:src:1", "snapshot_digest": "d"*64,
                                             "quality": {"timing_basis": "reconciled_snapshot_acquisition", "source_id": "den_ha", "atomic_snapshot": False}})

    def close(self):
        self.store.close()


class Base(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.ledger = Ledger(self.directory.name)
        self.store, self.sid = self.ledger.store, self.ledger.sid

    def tearDown(self):
        self.ledger.close()
        self.directory.cleanup()

    def populate(self):
        L = self.ledger
        L.activate(ha={"a": True, "b": False})
        L.event("reasoning.setting", {"enabled": True, "revision": 2})
        self.a1 = L.output("a", 2.0)
        self.b1 = L.output("b", 2.2)
        L.ha(2.5)
        self.request_a = L.reasoning("a", 3.0, answered_at=4.0, evidence_ids=(self.a1,))
        self.request_b = L.reasoning("b", 3.1, answered_at=4.1, state="failed", status="failed", output=False,
                                     reason_row="household_egress_not_authorized:rate_budget", evidence_ids=(self.b1,))
        L.at(4.5).event("perception.expired", {"request_id": "x1", "arm": "b", "reason": "expired_before_dispatch"})
        L.at(4.6).event("perception.dropped", {"request_id": "x2", "arm": "a", "reason": "source_sampling_cadence"})
        L.at(4.7).event("perception.dropped", {"request_id": "x3", "arm": "a", "reason": "runner_closed_before_dispatch"})
        L.rejected("a", 4.8)
        self.a2 = L.output("a", 6.0)
        self.pending_a = L.reasoning("a", 7.0)
        L.at(7.5)

    def node(self, result, key):
        return next(s for s in result["sources"] if s["id"] == key)

    def stage(self, result, arm, name):
        return next(s for s in result["arms"][arm]["chain"] if s["stage"] == name)


class GraphTests(Base):

    def test_live_graph_links_native_output_prepared_evidence_request_and_result(self):
        self.populate()
        result = lab_graph.graph(self.store, self.sid)
        self.assertEqual((result["contract"], result["cursor"]["mode"], result["inference"]), ("perception-lab-graph-1", "live", "none"))
        names = [s["stage"] for s in result["arms"]["a"]["chain"]]
        self.assertEqual(names, ["specialist_output", "prepared_evidence", "consumed_request", "result", *lab_graph.INACTIVE])
        for stage in result["arms"]["b"]["chain"][4:]:
            self.assertEqual((stage["status"], stage["reason"]), ("inactive", "actuation_deferred"))
        specialist = self.stage(result, "a", "specialist_output")
        self.assertEqual((specialist["event_id"], specialist["status"], specialist["counts"]["objects"], specialist["counts"]["pose_instances"]),
                         (self.a2, "fresh", 2, 1))
        self.assertEqual(specialist["timings"]["delivery"], {"encode_ms": 3.0, "transport_ms": 90.0})
        self.assertAlmostEqual(specialist["timings"]["dispatch_to_available_ms"], 400.0, places=3)
        # The in-flight request used no specialist output yet: consumption is per request.
        self.assertFalse(specialist["consumed_by_latest_request"])
        prepared = self.stage(result, "a", "prepared_evidence")
        self.assertEqual((prepared["status"], prepared["records"], prepared["by_source"]), ("saved", 1, {"ha.observation": 1}))
        self.assertEqual((prepared["semantic_version"], prepared["exclusions"]), ("den-semantics-2", "not_recorded"))
        consumed = self.stage(result, "a", "consumed_request")
        self.assertEqual((consumed["request_id"], consumed["status"], consumed["versions_basis"]), (self.pending_a, "admitted", "result_not_recorded"))
        self.assertEqual(consumed["question_ids"], ["activity", "reading_supported"])
        answer = self.stage(result, "a", "result")
        self.assertEqual((answer["request_id"], answer["activity"], answer["probability"], answer["confidence"]), (self.request_a, "reading", 0.7, 0.6))
        self.assertEqual((answer["same_request"], answer["in_flight"]["request_id"], answer["in_flight"]["status"]), (False, self.pending_a, "awaiting_result"))
        self.assertEqual(answer["usage"], {"input_tokens": 900, "output_tokens": 40})
        failed = self.stage(result, "b", "result")
        self.assertEqual((failed["status"], failed["failure_reason"]), ("failed", "household_egress_not_authorized:rate_budget"))
        self.assertIsNone(failed["in_flight"])
        versions = self.stage(result, "b", "consumed_request")
        self.assertEqual(versions["bytes"], len(encode(json.loads(json.dumps(versions["snippet"]["json"]))).encode()))

    def test_source_lifecycle_flags_stay_distinct_and_consumption_follows_the_request(self):
        self.populate()
        result = lab_graph.graph(self.store, self.sid)
        ha = self.node(result, "ha_snapshot")
        # Fresh at arrival, expired now; B withholds HA by recorded setup.
        self.assertEqual((ha["implemented"], ha["provisioned"], ha["freshness"], ha["status"]), (True, True, "stale", "stale"))
        self.assertEqual(ha["consumed_by"], {"a": "consumed", "b": "withheld_by_setup"})
        self.assertEqual(ha["declared_by_setup"], {"a": True, "b": False})
        self.assertEqual(ha["counts"], {"entities": 2, "known": 1, "camera_derived": 1})
        a = self.node(result, "specialist_a")
        self.assertEqual(a["consumed_by"], {"a": "not_in_request", "b": "other_arm"})
        self.assertEqual(a["version"]["profile"]["artifact_sha256"], "a"*64)
        # perception.rejected at 4.8 is older than the a2 output at 6.0, still valid at 7.5.
        self.assertEqual((a["failure"]["kind"], a["status"], a["freshness"]), ("perception.rejected", "producing", "fresh"))
        b = self.node(result, "specialist_b")
        self.assertEqual((b["failure"]["kind"], b["failure"]["reason"], b["status"]), ("perception.expired", "expired_before_dispatch", "failed"))
        self.assertEqual(b["consumed_by"], {"a": "other_arm", "b": "consumed"})
        caption = self.node(result, "caption_receipt")
        self.assertEqual((caption["status"], caption["provisioned"], caption["producing"], caption["native"]), ("no_records", None, False, None))
        labels = self.node(result, "human_labels")
        self.assertEqual((labels["consumed_by"], labels["status"]), ({"a": "excluded_by_policy", "b": "excluded_by_policy"}, "review_only"))
        rgb = self.node(result, "rgb_camera")
        self.assertEqual((rgb["provisioned"], rgb["counts"]["images"], rgb["native"]["content_type"]), (True, 3, "image/jpeg"))
        self.assertEqual(rgb["native"]["snippet"]["json"]["frame_id"], "frame-3")
        self.assertEqual(self.node(result, "depth_bundle")["status"], "no_records")

    def test_all_design_families_are_mapped_and_proposed_ones_carry_no_values(self):
        result = lab_graph.graph(self.store, self.sid)
        self.assertEqual([f["number"] for f in result["families"]], list(range(1, 38)))
        ids = {s["id"] for s in result["sources"]}
        for family in result["families"]:
            self.assertTrue(set(family["nodes"]) <= ids, family)
        proposed = [s for s in result["sources"] if s["kind"] == "proposed"]
        # Families 26/27 (clock/sun, weather) are the recorded ambient_context
        # node and family 17 (V-JEPA) the recorded vjepa_scene node.
        self.assertEqual(len(proposed), 17)
        for node in proposed:
            self.assertIn(node["status"], {"proposed", "not_provisioned", "excluded_by_policy"})
            self.assertTrue(node["reason"])
            self.assertEqual((node["implemented"], node["provisioned"], node["producing"], node["native"], node["last_available_at"], node["counts"]),
                             (False, False, False, None, None, None))
        # Nothing recorded yet: real nodes are not painted live.
        for node in result["sources"]:
            if node["kind"] == "ledger" and node["id"] != "human_labels":
                self.assertIn(node["status"], {"no_records"}, node["id"])
                self.assertFalse(node["producing"])

    def test_replay_cursor_is_causal_and_reads_do_not_write(self):
        self.populate()
        before = self.store.db.total_changes
        early = lab_graph.graph(self.store, self.sid, 3500)
        self.assertEqual(early["cursor"]["mode"], "replay")
        self.assertAlmostEqual(early["cursor"]["cutoff"], START+3.5)
        # Request A admitted at 3.0, answered at 4.0: at 3.5 it is still awaited.
        answer = self.stage(early, "a", "result")
        self.assertEqual((answer["status"], answer["in_flight"]["request_id"]), ("awaiting_result", self.request_a))
        self.assertEqual(self.stage(early, "a", "specialist_output")["event_id"], self.a1)
        self.assertTrue(self.stage(early, "a", "specialist_output")["consumed_by_latest_request"])
        self.assertEqual(self.stage(early, "b", "result")["status"], "awaiting_result")
        self.assertIsNone(self.node(early, "specialist_b")["failure"])
        self.assertEqual(self.node(early, "rgb_camera")["counts"]["images"], 2)
        before_all = lab_graph.graph(self.store, self.sid, 100)
        self.assertEqual(self.stage(before_all, "a", "specialist_output")["status"], "none")
        self.assertIsNone(before_all["experiment"])
        self.assertEqual(self.stage(before_all, "a", "prepared_evidence")["status"], "not_saved")
        lab_graph.timeline(self.store, self.sid)
        lab_graph.replay(self.store, self.sid, 3500)
        # Graph, timeline and replay only read the ledger.
        self.assertEqual(self.store.db.total_changes, before)
        with self.assertRaises(LabError) as caught:
            lab_graph.graph(self.store, self.sid, self.store.settings.max_session_seconds*1000+1)
        self.assertEqual(caught.exception.code, "invalid_cursor")
        # A cursor ahead of the ledger is clamped, never extrapolated.
        future = lab_graph.graph(self.store, self.sid, 60000)
        self.assertTrue(future["cursor"]["clamped_to_now"])

    def test_snippets_are_bounded_valid_json_with_explicit_markers_and_redaction(self):
        value = {"objects": [{"label": "person", "x": i} for i in range(400)], "note": "word "*1000,
                 "processing": {"checkpoint_path": "/home/lab/model.onnx", "location": "C:\\models\\a.onnx"},
                 "authorization": "Bearer secret", "api_token": "abc", "jpeg_base64": "A"*100, "blob": "A"*600,
                 "usage": {"input_tokens": 5}, "url": "/api/perception-lab/v1/sessions/s/media/m?generation=0"}
        result = lab_graph.snippet(value)
        text = encode(result["json"])
        self.assertLessEqual(len(text.encode()), lab_graph.SNIPPET_BYTES)
        self.assertEqual(json.loads(text), result["json"])
        self.assertTrue(result["truncated"])
        self.assertGreater(result["bytes"], lab_graph.SNIPPET_BYTES)
        self.assertIn(lab_graph.TRUNCATED, text)
        for secret in ("secret", "abc", "/home/lab", "C:\\\\models", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"):
            self.assertNotIn(secret, text)
        self.assertEqual(result["redacted"], 6)
        small = lab_graph.snippet({"usage": {"input_tokens": 5}, "url": "/api/perception-lab/v1/x/y"})
        self.assertEqual((small["truncated"], small["json"]["usage"]), (False, {"input_tokens": 5}))
        self.assertEqual(small["json"]["url"], "/api/perception-lab/v1/x/y")
        self.assertLessEqual(len(encode(lab_graph.snippet(["x"*9000]*9000)["json"]).encode()), lab_graph.SNIPPET_BYTES)

    def test_caption_prose_is_never_copied_and_invalid_ha_is_not_provisioned(self):
        L = self.ledger
        L.activate()
        L.at(1).event("caption.receipt", {"available_at": L.now, "first_received_at": L.now, "observed_at": L.now-2, "valid_until": L.now+20,
                                          "model": "caption-model", "text": "a private household sentence", "caption_digest": "c"*64,
                                          "projection": {"version": "caption-history-1", "activity": "unavailable"}, "cloud_status": "not_enabled"})
        L.at(2).event("ha.unavailable", {"reason": "ha_source_invalid", "available_at": L.now})
        # A malformed admitted row is reported, not a crash.
        L.at(3).event("reasoning.request", {"request_id": "q1", "arm": "a", "generation": 1, "available_at": L.now, "request": None})
        result = lab_graph.graph(self.store, self.sid)
        self.assertNotIn("private household sentence", json.dumps(result))
        caption = self.node(result, "caption_receipt")
        self.assertEqual((caption["status"], caption["provisioned"], caption["version"]["projection_version"]), ("producing", True, "caption-history-1"))
        ha = self.node(result, "ha_snapshot")
        self.assertEqual((ha["provisioned"], ha["provisioned_basis"], ha["status"], ha["failure"]["reason"]), (False, "ha_source_invalid", "failed", "ha_source_invalid"))
        prepared = self.stage(result, "a", "prepared_evidence")
        self.assertEqual((prepared["status"], prepared["records"]), ("saved", 0))
        self.assertEqual(self.node(result, "ha_snapshot")["consumed_by"]["a"], "not_in_request")

    def test_graph_native_outputs_never_leak_private_paths(self):
        self.populate()
        text = json.dumps(lab_graph.graph(self.store, self.sid))
        self.assertNotIn("/home/lab/models", text)
        self.assertIn('"checkpoint_path": "[redacted]"', text)

    def test_graph_reads_stay_bounded_as_history_grows(self):
        L = self.ledger
        L.activate()
        statements = []
        def measure():
            statements.clear()
            self.store.db.set_trace_callback(statements.append)
            try:
                lab_graph.graph(self.store, self.sid)
                lab_graph.graph(self.store, self.sid, 1000)
            finally:
                self.store.db.set_trace_callback(None)
            return len(statements)
        probe = "ORDER BY id LIMIT 1"
        for i in range(10):
            L.output("a" if i % 2 else "b", 1+i*0.25, poses=0)
        measure()
        small = [s for s in statements if probe not in s]
        for i in range(10, 400):
            L.output("a" if i % 2 else "b", 1+i*0.25, poses=0)
        measure()
        # Only the O(log n) horizon search grows with history; every other
        # statement walks a bounded index range, the same number of them.
        self.assertEqual(len([s for s in statements if probe not in s]), len(small))
        self.assertLessEqual(len([s for s in statements if probe in s]), 2+max(1, self.store.db.execute("SELECT max(id) FROM events").fetchone()[0]).bit_length())
        # Every events read names the per-kind index; none walks the session.
        statements.clear()
        self.store.db.set_trace_callback(statements.append)
        try:
            lab_graph.timeline(self.store, self.sid)
        finally:
            self.store.db.set_trace_callback(None)
        # store.get_session's own reasoning.setting read is not a graph read.
        reads = [s for s in statements if " FROM events" in s and "WHERE id" not in s and "min(id)" not in s and probe not in s
                 and "kind='reasoning.setting' ORDER BY id DESC LIMIT 1" not in s]
        self.assertTrue(reads)
        for sql in reads:
            self.assertIn("INDEXED BY events_session_kind_id", sql)
        with self.store.lock:
            plan = " ".join(str(row[3]) for row in self.store.db.execute("EXPLAIN QUERY PLAN "+lab_graph._LATEST, (self.sid, "perception.output", 10**9, 10)))
        self.assertIn("events_session_kind_id (session_id=? AND kind=? AND id<?)", plan)
        self.assertNotIn("TEMP B-TREE", plan)

    def test_horizon_is_the_last_event_recorded_by_the_limit(self):
        L = self.ledger
        ids = [L.at(t).event("bookmark", {"at_ms": 0, "label": "x"}) for t in (1, 2, 3, 4, 5)]
        self.assertEqual(lab_graph._horizon(self.store, self.sid, START+3.5), ids[2])
        self.assertEqual(lab_graph._horizon(self.store, self.sid, START+10), ids[-1])
        self.assertLess(lab_graph._horizon(self.store, self.sid, START-100), ids[0])


class TimelineTests(Base):
    def test_timeline_tracks_gaps_markers_jev_transitions_and_labels(self):
        self.populate()
        L = self.ledger
        L.at(8).event("workers.cleanup", {"request_id": "w1", "state": "stopped", "reason": None, "local_drain": "drained",
                                          "workers": {"a": {"state": "stopped", "reason": None}, "b": {"state": "stopped", "reason": None}}})
        L.at(8.1).event("bookmark", {"at_ms": 2500, "label": "picked up book", "request_id": "b1", "actor": "fixture"})
        self.store.save_annotation(self.sid, "l1", {"at_ms": 1000, "end_ms": 5000, "label": "reading", "origin": "human", "reviewed": True}, 0, "fixture")
        L.at(8.2).event("caption.receipt", {"available_at": L.now, "observed_at": L.now-1, "valid_until": L.now+20,
                                            "projection": {"version": "caption-history-1", "activity": "reading"}, "cloud_status": "not_enabled"})
        result = lab_graph.timeline(self.store, self.sid)
        self.assertEqual((result["range"], result["duration_ms"], result["inference"]), ({"from_ms": 0, "to_ms": 8200}, 8200, "none"))
        tracks = result["tracks"]
        a = tracks["perception"]["a"]
        self.assertEqual(([p["event_id"] for p in a["points"]], a["downsampled"]), ([self.a1, self.a2], False))
        self.assertEqual({k: a["points"][0][k] for k in ("t_ms", "source_ms", "status", "objects", "poses")},
                         {"t_ms": 2000, "source_ms": 1700, "status": "fresh", "objects": 2, "poses": 1})
        gaps = tracks["delivery_gaps"]["a"]["items"]
        self.assertEqual([(g["kind"], g["from_ms"], g["to_ms"]) for g in gaps], [("leading", 500, 2000), ("internal", 2000, 6000), ("trailing", 6000, 8200)])
        markers = tracks["perception_markers"]
        self.assertEqual([(m["kind"], m["reason"]) for m in markers["a"]["items"]],
                         [("dropped", "runner_closed_before_dispatch"), ("rejected", "worker_ingress_busy")])
        self.assertEqual(markers["a"]["routine_drops"], {"source_sampling_cadence": 1})
        self.assertEqual([(m["kind"], m["reason"]) for m in markers["b"]["items"]], [("expired", "expired_before_dispatch")])
        jev = tracks["jev"]
        self.assertEqual([r["request_id"] for r in jev["a"]["requests"]["items"]], [self.request_a, self.pending_a])
        self.assertEqual({k: jev["a"]["results"]["items"][0][k] for k in ("t_ms", "status", "activity", "probability", "abstained")},
                         {"t_ms": 4000, "status": "ready", "activity": "reading", "probability": 0.7, "abstained": False})
        self.assertEqual((jev["b"]["results"]["items"][0]["state"], jev["b"]["results"]["items"][0]["reason"]),
                         ("failed", "household_egress_not_authorized:rate_budget"))
        self.assertEqual([t["kind"] for t in tracks["transitions"]["items"]], ["reasoning.setting", "experiment.activated", "reasoning.setting"])
        self.assertEqual([r["state"] for r in tracks["recording"]["items"]][:3], ["requested", "starting", "recording"])
        self.assertEqual(tracks["labels"]["items"][0]["label"], "reading")
        self.assertEqual(tracks["bookmarks"]["items"][0]["t_ms"], 2500)
        self.assertEqual(tracks["context"]["ha"]["items"][0]["known"], 1)
        self.assertEqual(tracks["workers"]["items"][0]["workers"]["a"]["state"], "stopped")
        self.assertEqual(tracks["context"]["captions"]["items"][0]["projection_activity"], "reading")
        # Items outside an explicit range are not shown.
        early = lab_graph.timeline(self.store, self.sid, 0, 7000)["tracks"]
        self.assertEqual((early["workers"]["items"], early["context"]["captions"]["items"]), ([], []))
        self.assertEqual(result["clock"]["annotation_time_bases"], [])
        self.assertEqual(result["clock"]["analyzed_frames"], {"clock_mapped": 3, "total": 3})

    def test_timeline_range_filters_and_rejects_bad_bounds(self):
        self.populate()
        window = lab_graph.timeline(self.store, self.sid, 3000, 5000)
        self.assertEqual(window["tracks"]["perception"]["a"]["points"], [])
        self.assertEqual([r["t_ms"] for r in window["tracks"]["jev"]["a"]["results"]["items"]], [4000])
        for bad in ((5000, 3000), (-1, None), (None, 1800001)):
            with self.assertRaises(LabError):
                lab_graph.timeline(self.store, self.sid, *bad)

    def test_timeline_downsamples_deterministically_with_an_explicit_marker(self):
        L = self.ledger
        L.activate()
        times = [1+i*0.1 for i in range(60) if not 20 <= i < 35]
        for t in times:
            L.output("a", t, poses=0)
        with patch.object(lab_graph, "TRACK_POINTS", 10):
            first = lab_graph.timeline(self.store, self.sid)["tracks"]
            second = lab_graph.timeline(self.store, self.sid)["tracks"]
        self.assertEqual(first, second)
        points = first["perception"]["a"]
        self.assertEqual((points["downsampled"], points["total"], points["method"]), (True, len(times), "latest_per_bucket"))
        self.assertLessEqual(points["returned"], 10)
        # Gaps come from every point, not the downsampled ones.
        self.assertEqual([(g["kind"], g["from_ms"], g["to_ms"]) for g in first["delivery_gaps"]["a"]["items"]],
                         [("internal", round((times[19]-0)*1000), round(times[20]*1000))])

    def test_verified_video_clock_allows_session_labels(self):
        L = self.ledger
        init = self.store.add_media(self.sid, b"init", "video/mp4", {"kind": "encoded_init", "role": "init", "format": "fmp4"}, "encoded:init")
        self.assertEqual(lab_graph.timeline(self.store, self.sid)["clock"]["annotation_time_bases"], ["encoded_video"])
        self.store.add_media(self.sid, b"seg", "video/mp4", {"kind": "encoded_segment", "role": "media", "initialization_id": init["id"],
                             "clock_mapping_status": "verified", "timeline_start_ms": 0}, "segment:1")
        clock = lab_graph.timeline(self.store, self.sid)["clock"]
        self.assertEqual((clock["session_clock_verified"], clock["annotation_time_bases"], clock["encoded_video_init_id"]), (True, ["session"], init["id"]))

    def test_timeline_before_recording_is_explicitly_empty(self):
        self.store.transition(self.sid, "stopped")
        session, _ = self.store.create_session("den", "detection", "Later", "request2")
        result = lab_graph.timeline(self.store, session["id"])
        self.assertEqual((result["reason"], result["tracks"]), ("recording_not_started", None))
        with self.assertRaises(LabError) as caught:
            lab_graph.graph(self.store, session["id"], 0)
        self.assertEqual(caught.exception.code, "no_recording_origin")


class ReplayTests(Base):
    def test_replay_matches_outputs_at_and_room_without_history(self):
        self.populate()
        result = lab_graph.replay(self.store, self.sid, 4500)
        self.assertEqual(result["outputs"], self.store.outputs_at(self.sid, 4500))
        self.assertEqual((result["room"], result["at_ms"], result["inference"]), (None, 4500, "none"))
        self.assertNotIn("events", result)
        self.assertNotIn("media", result)
        with self.assertRaises(LabError):
            lab_graph.replay(self.store, self.sid, None)
        with patch.object(lab_graph, "RESPONSE_BYTES", 100), self.assertRaises(LabError) as caught:
            lab_graph.replay(self.store, self.sid, 4500)
        self.assertEqual(caught.exception.code, "replay_size_limit")


class OutputsAtEquivalenceTests(Base):
    def test_single_parse_walk_matches_store_outputs_at_on_randomized_ledgers(self):
        import random
        rng = random.Random(7)
        L = self.ledger
        L.activate(1, t=0.2)
        numbers = lambda: rng.choice([rng.uniform(0, 40), rng.randint(0, 40), None, True, "5", -1])
        for i in range(700):
            kind = rng.choice(lab_graph.OUTPUT_KINDS)
            t = 0.5+i*0.05
            payload = {"run_id": rng.choice(["detectorA", "detectorB", "jevA", "jevB", None, 3, 3.0, True, {"x": 1}]),
                       "arm": rng.choice(["a", "b", None, 1]), "mode": rng.choice(["live", "live", None, "offline", "saved_detector", True]),
                       "generation": rng.choice([1, 2]), "valid_until": rng.choice([START+t+rng.uniform(-2, 3), None, "x"]),
                       "status": "completed", "arm_snapshot": rng.choice([{"status": "running"}, None])}
            if rng.random() < .9:
                payload["source_end_s"] = numbers()
            if rng.random() < .8:
                payload["available_at_s"] = numbers()
            if kind == "analysis.output" or rng.random() < .1:
                payload["job_id"] = rng.choice(["j1", "j2", None])
                payload["judgment_end_s"] = rng.choice([rng.uniform(0, 40), None])
            L.at(t).event(kind, payload)
            if i == 350:
                L.activate(2, t=t)
        for at_ms in [0, 1, 500, 5000, 12345, 20000, 30000, 36000, 40000, 1800000]:
            try:
                expected = self.store.outputs_at(self.sid, at_ms)
            except LabError as error:
                with self.assertRaises(LabError) as caught:
                    lab_graph.outputs_at(self.store, self.sid, at_ms)
                self.assertEqual(caught.exception.code, error.code)
                continue
            self.assertEqual(lab_graph.outputs_at(self.store, self.sid, at_ms), expected, at_ms)
        for bad in (-1, 1800001, 1.5, "1"):
            with self.assertRaises(LabError):
                lab_graph.outputs_at(self.store, self.sid, bad)

    def test_run_limit_matches_the_store(self):
        L = self.ledger
        for i in range(130):
            L.at(1).event("analysis.output", {"job_id": f"j{i}", "run_id": "r", "arm": "a", "mode": "offline", "source_end_s": 0.5})
        for reader in (self.store.outputs_at, lambda sid, at: lab_graph.outputs_at(self.store, sid, at)):
            with self.assertRaises(LabError) as caught:
                reader(self.sid, 1000)
            self.assertEqual(caught.exception.code, "replay_run_limit")

    def test_walk_reads_every_row_once_in_bounded_chunks(self):
        ids = [self.ledger.at(t).event("bookmark", {"at_ms": t}) for t in range(10)]
        sql = "SELECT id FROM events INDEXED BY events_session_kind_id WHERE session_id=? AND kind='bookmark' AND id>? AND id<=? ORDER BY id"
        rows, cut = lab_graph._walk(self.store, sql, (self.sid,), 0, ids[-1], 100, chunk=3)
        self.assertEqual(([r[0] for r in rows], cut), (ids, False))
        rows, cut = lab_graph._walk(self.store, sql, (self.sid,), ids[1], ids[-2], 4, chunk=3)
        self.assertEqual(([r[0] for r in rows], cut), (ids[2:6], True))


class SetupAtCursorTests(Base):
    RECIPE = {"id": "placement-best",
              "a": {"input": "rgb-640", "detector": "d-now", "pose": None, "precision": "fp32", "localization": "none", "room_map": False,
                    "caption_evidence": True, "ha_evidence": True, "sampling_hz": 9, "history": 99},
              "b": {"input": "rgb-640", "detector": "d-now", "pose": None, "precision": "fp32", "localization": "stereo", "room_map": False,
                    "caption_evidence": True, "sampling_hz": 9, "history": 99}}

    def recipe(self, experiment_id):
        return self.RECIPE if experiment_id == "placement-best" else None

    def test_setup_and_declarations_come_from_the_recorded_activation(self):
        L = self.ledger
        L.activate(caption={"a": True, "b": False}, sampling={"a": 2, "b": 4},
                   spatial={"a": {"localization": "stereo", "room_relations": True}, "b": {"localization": "none", "room_relations": False}})
        L.reasoning("a", 2.0)
        L.reasoning("b", 2.1)
        L.at(3)
        with patch.object(lab_graph, "_recipe", self.recipe):
            live = lab_graph.graph(self.store, self.sid)
            replay = lab_graph.graph(self.store, self.sid, 2500)
            before = lab_graph.graph(self.store, self.sid, 100)
        a = live["arms"]["a"]
        # Recorded fields win over today's catalog (sampling 9, history 99, caption for b).
        self.assertEqual({k: a["setup"][k] for k in ("localization", "room_map", "sampling_hz", "history", "caption_evidence")},
                         {"localization": "stereo", "room_map": True, "sampling_hz": 2, "history": 10, "caption_evidence": True})
        self.assertEqual((a["setup_basis"]["localization"], a["setup_basis"]["precision"], a["setup"]["precision"]),
                         ("recorded_activation", "current_catalog", "fp32"))
        self.assertEqual((live["arms"]["b"]["setup"]["caption_evidence"], live["experiment"]["caption_evidence"]), (False, {"a": True, "b": False}))
        # A replay cursor never shows present configuration as what the arms declared.
        r = replay["arms"]["a"]
        self.assertEqual((r["setup"]["localization"], r["setup"]["precision"], r["setup_basis"]["precision"]), ("stereo", None, "not_recorded"))
        caption = self.node(replay, "caption_receipt")
        self.assertEqual((caption["declared_by_setup"], caption["declared_basis"]),
                         ({"a": True, "b": False}, {"a": "recorded_activation", "b": "recorded_activation"}))
        self.assertEqual(caption["consumed_by"], {"a": "not_in_request", "b": "withheld_by_setup"})
        self.assertEqual(self.node(replay, "depth_bundle")["declared_by_setup"], {"a": True, "b": False})
        self.assertEqual(self.node(replay, "room_observation")["declared_by_setup"], {"a": True, "b": False})
        self.assertEqual(self.node(replay, "specialist_a")["declared_basis"], {"a": "recorded_activation", "b": "recorded_activation"})
        # Before any activation a replay cursor never borrows the session's current experiment.
        self.assertEqual((before["arms"]["a"]["setup"], before["arms"]["a"]["setup_basis"]["detector"]), (None, "not_recorded"))
        self.assertEqual(self.node(before, "caption_receipt")["declared_by_setup"], {"a": None, "b": None})
        self.assertEqual(self.node(before, "specialist_a")["declared_by_setup"], {"a": None, "b": None})

    def test_live_cursor_without_activation_labels_the_catalog_and_never_withholds_from_it(self):
        with patch.object(lab_graph, "_recipe", self.recipe):
            live = lab_graph.graph(self.store, self.sid)
        a = live["arms"]["a"]
        self.assertEqual((a["setup"]["detector"], a["setup_basis"]["detector"], a["setup_basis"]["caption_evidence"]),
                         ("d-now", "current_catalog", "current_catalog"))
        caption = self.node(live, "caption_receipt")
        self.assertEqual((caption["declared_by_setup"], caption["consumed_by"]), ({"a": True, "b": True}, {"a": "no_request", "b": "no_request"}))
        self.assertEqual(self.node(live, "specialist_b")["declared_by_setup"], {"a": False, "b": True})
        self.assertEqual(self.node(live, "specialist_b")["declared_basis"], {"a": "current_catalog", "b": "current_catalog"})


class ScanBoundTests(Base):
    def test_an_arm_request_older_than_the_scan_bound_is_unknown_not_absent(self):
        L = self.ledger
        L.activate()
        L.reasoning("b", 1.0, answered_at=1.5)
        for i in range(lab_graph.SCAN_ROWS):
            L.reasoning("a", 10+i*0.01, answered_at=10+i*0.01+0.005)
        L.at(20)
        result = lab_graph.graph(self.store, self.sid)
        self.assertEqual({k: self.stage(result, "b", "prepared_evidence")[k] for k in ("status", "reason")},
                         {"status": "unknown", "reason": "older_than_scan_bound"})
        self.assertEqual({k: self.stage(result, "b", "consumed_request")[k] for k in ("status", "reason")},
                         {"status": "unknown", "reason": "older_than_scan_bound"})
        answer = self.stage(result, "b", "result")
        self.assertEqual((answer["status"], answer["reason"], answer["in_flight"]), ("unknown", "older_than_scan_bound", None))
        self.assertEqual(self.node(result, "ha_snapshot")["consumed_by"], {"a": "consumed", "b": "unknown_older_than_scan_bound"})
        self.assertEqual(self.node(result, "specialist_b")["consumed_by"], {"a": "other_arm", "b": "unknown_older_than_scan_bound"})
        self.assertEqual(self.stage(result, "a", "consumed_request")["status"], "admitted")
        # Where the bound reaches it (the horizon ends before arm A's burst) it is found.
        early = lab_graph.graph(self.store, self.sid, 1600)
        self.assertEqual((self.stage(early, "b", "prepared_evidence")["status"], self.stage(early, "b", "result")["status"]), ("saved", "ready"))


class TimelineBoundTests(Base):
    def test_jev_tracks_keep_the_latest_per_bucket_and_report_scan_cuts(self):
        L = self.ledger
        L.activate()
        for i in range(40):
            last = i == 39
            L.reasoning("ab"[i % 2], 1+i*0.5, answered_at=1+i*0.5+0.2, output=not last, state="failed" if last else "completed",
                        status="failed" if last else "ready", reason_row="provider_timeout" if last else None)
        L.at(25)
        with patch.object(lab_graph, "TRACK_ITEMS", 5):
            whole = lab_graph.timeline(self.store, self.sid)
        requests, results = whole["tracks"]["jev"]["a"]["requests"], whole["tracks"]["jev"]["b"]["results"]
        self.assertEqual((requests["total"], requests["downsampled"], requests["method"], requests["scan_truncated"]), (20, True, "latest_per_bucket", False))
        self.assertLessEqual(requests["returned"], 5)
        # The end of the session is shown, not only its first items.
        self.assertEqual(requests["items"][-1]["t_ms"], 20000)
        self.assertEqual((results["items"][-1]["t_ms"], results["items"][-1]["state"], results["items"][-1]["reason"]), (20700, "failed", "provider_timeout"))
        self.assertFalse(whole["scan_truncated"])
        with patch.object(lab_graph, "JEV_ROWS", 10):
            cut = lab_graph.timeline(self.store, self.sid)
        requests, results = cut["tracks"]["jev"]["a"]["requests"], cut["tracks"]["jev"]["a"]["results"]
        self.assertEqual((requests["scan_truncated"], requests["truncated"], requests["total"]), (True, True, 5))
        self.assertEqual((results["scan_truncated"], results["truncated"]), (True, True))
        self.assertTrue(cut["scan_truncated"])

    def test_a_cut_perception_scan_claims_no_gap_beyond_what_it_read(self):
        L = self.ledger
        L.activate()
        for i in range(20):
            L.output("ab"[(i+1) % 2], 1+i*0.5, poses=0)
        L.at(15)
        full = lab_graph.timeline(self.store, self.sid)["tracks"]["delivery_gaps"]["a"]
        self.assertEqual([(g["kind"], g["to_ms"]) for g in full["items"]], [("trailing", 15000)])
        with patch.object(lab_graph, "TIMELINE_ROWS", 10):
            cut = lab_graph.timeline(self.store, self.sid)
        gaps = cut["tracks"]["delivery_gaps"]["a"]
        self.assertEqual((gaps["items"], gaps["scan_truncated"], gaps["measured_to_ms"]), ([], True, 5500))
        self.assertTrue(cut["tracks"]["perception"]["a"]["scan_truncated"])
        self.assertTrue(cut["scan_truncated"])

    def test_every_track_reports_its_own_scan_cut(self):
        L = self.ledger
        for i in range(10):
            L.at(1+i*0.1).event("bookmark", {"at_ms": 1000+i*100, "label": "b"})
        L.at(3)
        self.assertFalse(lab_graph.timeline(self.store, self.sid)["tracks"]["bookmarks"]["scan_truncated"])
        with patch.object(lab_graph, "TRACK_ITEMS", 2):
            result = lab_graph.timeline(self.store, self.sid)
        self.assertEqual({k: result["tracks"]["bookmarks"][k] for k in ("truncated", "scan_truncated")}, {"truncated": True, "scan_truncated": True})
        self.assertTrue(result["scan_truncated"])

    def test_drops_are_counted_in_chunked_walks_never_one_whole_range_statement(self):
        L = self.ledger
        L.activate()
        expected = {"a": {}, "b": {}}
        with self.store.transaction() as db:
            for i in range(5000):
                L.now = START+1+i*0.001
                arm, reason = "ab"[i % 2], lab_graph.ROUTINE_DROPS[i % 3 == 0]
                expected[arm][reason] = expected[arm].get(reason, 0)+1
                self.store._event(db, self.sid, "perception.dropped", {"request_id": f"d{i}", "arm": arm, "reason": reason})
                if i == 2500:
                    self.store._event(db, self.sid, "perception.dropped", {"request_id": "odd", "arm": "b", "reason": "runner_closed_before_dispatch"})
        L.at(8)
        statements = []
        self.store.db.set_trace_callback(statements.append)
        try:
            result = lab_graph.timeline(self.store, self.sid)
        finally:
            self.store.db.set_trace_callback(None)
        markers = result["tracks"]["perception_markers"]
        self.assertEqual((markers["a"]["routine_drops"], markers["b"]["routine_drops"]), (expected["a"], expected["b"]))
        self.assertEqual([(m["kind"], m["reason"]) for m in markers["b"]["items"]], [("dropped", "runner_closed_before_dispatch")])
        self.assertFalse(markers["a"]["routine_drops_is_lower_bound"])
        drops = [sql for sql in statements if "perception.dropped" in sql]
        # Several bounded chunks, each under its own lock hold; no GROUP BY over the range.
        self.assertGreaterEqual(len(drops), 5)
        for sql in drops:
            self.assertIn("LIMIT", sql)
            self.assertNotIn("GROUP BY", sql)
        with patch.object(lab_graph, "DROP_ROWS", 100):
            cut = lab_graph.timeline(self.store, self.sid)
        self.assertTrue(cut["tracks"]["perception_markers"]["a"]["routine_drops_is_lower_bound"])
        self.assertTrue(cut["tracks"]["perception_markers"]["a"]["scan_truncated"])


class RouteTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from app import create_app
        self.directory = tempfile.TemporaryDirectory()
        token = "t"*32
        self.ledger = Ledger(self.directory.name, token=token)
        self.ledger.activate()
        self.ledger.output("a", 1.0)
        self.store, self.sid = self.ledger.store, self.ledger.sid
        self.client = TestClient(create_app(self.ledger.settings, store=self.store))
        self.headers = {"Authorization": "Bearer "+token}
        self.calls = []
        original = self.store.get_session
        def recorded(sid):
            try:
                asyncio.get_running_loop()
                self.calls.append("event_loop")
            except RuntimeError:
                self.calls.append("worker_thread")
            return original(sid)
        self.store.get_session = recorded

    def tearDown(self):
        self.client.close()
        self.ledger.close()
        self.directory.cleanup()

    def test_graph_timeline_and_replay_run_off_the_event_loop_with_exact_queries(self):
        base = "/api/perception-lab/v1/sessions/"+self.sid
        for path in ("/graph", "/graph?at_ms=1500", "/timeline", "/timeline?from_ms=0&to_ms=2000", "/replay?at_ms=1500"):
            self.calls.clear()
            response = self.client.get(base+path, headers=self.headers)
            self.assertEqual(response.status_code, 200, path)
            self.assertEqual(set(self.calls), {"worker_thread"}, path)
            self.assertLessEqual(len(response.content), lab_graph.RESPONSE_BYTES)
        for path in ("/graph?at_ms=1&at_ms=2", "/graph?cursor=1", "/timeline?at_ms=1", "/replay", "/replay?at_ms=1&from_ms=0"):
            response = self.client.get(base+path, headers=self.headers)
            self.assertEqual((response.status_code, response.json()["error"]["code"]), (422, "invalid_query"), path)
        response = self.client.get(base+"/graph?at_ms=1800001", headers=self.headers)
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (422, "invalid_cursor"))
        self.assertEqual(self.client.get(base+"/graph").status_code, 401)


class LifecyclePreparationAndAgeTests(Base):
    def lifecycle(self, arm, t, state, reason, request_id=None, generation=1):
        self.ledger.at(t)
        payload = {"request_id": request_id or f"{arm}{state}{t}", "arm": arm, "generation": generation, "state": state,
                   "reason": reason, "at": self.ledger.now, "available_at": self.ledger.now}
        return self.ledger.event("reasoning.lifecycle", payload)

    def test_lifecycle_receipts_are_jev_markers_with_routine_replacements_counted(self):
        L = self.ledger
        L.activate()
        for i in range(3):
            self.lifecycle("a", 1+i*0.1, "replaced", "superseded_by_newer_request")
        expired = self.lifecycle("b", 2.0, "expired", "experiment_generation_changed")
        cancelled = self.lifecycle("a", 3.0, "cancelled", "session_stopped")
        failed = self.lifecycle("b", 4.0, "failed", "local_dispatch_failed")
        L.at(4.5).event("reasoning.reconciled", {"request_id": "r0", "arm": "a", "state": "completed", "available_at": L.now})
        L.at(5)
        markers = lab_graph.timeline(self.store, self.sid)["tracks"]["jev_markers"]
        self.assertEqual([(m["t_ms"], m["kind"], m["arm"], m.get("state"), m["reason"], m["event_id"]) for m in markers["items"]],
                         [(2000, "reasoning.lifecycle", "b", "expired", "experiment_generation_changed", expired),
                          (3000, "reasoning.lifecycle", "a", "cancelled", "session_stopped", cancelled),
                          (4000, "reasoning.lifecycle", "b", "failed", "local_dispatch_failed", failed),
                          (4500, "reasoning.reconciled", "a", "completed", None, markers["items"][-1]["event_id"])])
        self.assertEqual(markers["items"][0]["generation"], 1)
        self.assertEqual((markers["routine_lifecycle"], markers["routine_lifecycle_is_lower_bound"], markers["scan_truncated"]),
                         ({"a": {"replaced": 3}, "b": {}}, False, False))
        # A range excludes receipts outside it, routine counts included.
        window = lab_graph.timeline(self.store, self.sid, 1500, 3500)["tracks"]["jev_markers"]
        self.assertEqual(([m["state"] for m in window["items"]], window["routine_lifecycle"]), (["expired", "cancelled"], {"a": {}, "b": {}}))
        with patch.object(lab_graph, "LIFECYCLE_ROWS", 2):
            cut = lab_graph.timeline(self.store, self.sid)
        markers = cut["tracks"]["jev_markers"]
        self.assertEqual((markers["scan_truncated"], markers["truncated"], markers["routine_lifecycle_is_lower_bound"], cut["scan_truncated"]),
                         (True, True, True, True))
        self.assertEqual(markers["routine_lifecycle"], {"a": {"replaced": 2}, "b": {}})

    def test_graph_result_stage_shows_the_latest_lifecycle_receipt_causally(self):
        L = self.ledger
        L.activate()
        L.event("reasoning.setting", {"enabled": True, "revision": 2})
        self.lifecycle("a", 2.0, "replaced", "superseded_by_newer_request", request_id="q1")
        event = self.lifecycle("a", 3.0, "cancelled", "reasoning_disabled", request_id="q2")
        L.at(4)
        live = self.stage(lab_graph.graph(self.store, self.sid), "a", "result")["latest_lifecycle"]
        self.assertEqual({k: live[k] for k in ("status", "event_id", "request_id", "state", "reason")},
                         {"status": "recorded", "event_id": event, "request_id": "q2", "state": "cancelled", "reason": "reasoning_disabled"})
        early = self.stage(lab_graph.graph(self.store, self.sid, 2500), "a", "result")["latest_lifecycle"]
        self.assertEqual((early["request_id"], early["state"]), ("q1", "replaced"))
        self.assertIsNone(self.stage(lab_graph.graph(self.store, self.sid, 1500), "a", "result")["latest_lifecycle"])
        self.assertIsNone(self.stage(lab_graph.graph(self.store, self.sid), "b", "result")["latest_lifecycle"])

    def semantic_request(self, t, *, observations=1, exclusions=None):
        L = self.ledger
        L.at(t)
        joints = [{"joint_id": j, "name": "joint", "pixel": [20.0+j, 30.0], "score": 0.7, "status": "supported"} for j in range(17)]
        evidence = [{"id": str(100+i), "source_kind": "perception.output", "arm": "a", "generation": 1, "observed_at": START+t-1-i*0.01,
                     "available_at": START+t-0.5, "valid_until": START+t+1,
                     "objects": [{"label": "person", "instance_ref": "object-0"}, {"label": "book", "instance_ref": "object-1"}],
                     "pose_instances": [{"layout": "coco17", "instance_ref": "object-0", "joints": joints}]} for i in range(observations)]
        semantic = {"version": "den-semantics-3", "latest_visual_evidence_id": "100",
                    "observations": [{"evidence_id": str(100+i), "age_seconds": 0.8+i*0.01, "freshness": "fresh" if i == 0 else "historical",
                                      "person_count": 1, "detected_objects": ["book", "person"],
                                      "facts": [{"fact": "left_wrist_near_book_in_image", "person_ref": "object-0", "object_ref": "object-1",
                                                 "quality": "heuristic_image_plane", "physical_contact": "unknown", "normalized_distance": 0.02}],
                                      "device_state": {"computer_active": "on"} if i == 0 else {}, "room_relations": [],
                                      "camera_derived_device_facts": [], "correlated_with": None, "depth": "not_supplied",
                                      "spatial_tracks": [], "tracking": "not_supplied", "attention": "not_measured",
                                      "book_open_or_closed": "not_measured"} for i in range(observations)],
                    "historical_caption": None,
                    "source_coverage": {"ha": "supplied", "room": "not_supplied", "pose": "supplied", "depth": "not_supplied",
                                        "rgb_floor_position": "not_supplied", "tracking": "not_supplied", "qwen_caption": "not_supplied"},
                    "limitations": "fixture"}
        state = {"site_id": "victoria", "observation_cutoff": START+t-0.2, "evidence": evidence, "semantic": semantic}
        if exclusions is not None:
            state["exclusions"] = exclusions
        packet = {"model": "jev-model", "questions": {"activity": {"type": "choice"}}, "state": state}
        return L.event("reasoning.request", {"request_id": f"prep{t}", "arm": "a", "generation": 1, "available_at": L.now,
                                             "packet_sha256": digest(packet), "request": packet, "status": "admitted_before_transport"})

    def test_preparation_block_copies_saved_semantic_sections_bounded(self):
        L = self.ledger
        L.activate()
        L.event("reasoning.setting", {"enabled": True, "revision": 2})
        event = self.semantic_request(2.0, observations=2)
        L.at(3)
        result = lab_graph.graph(self.store, self.sid)
        prep = result["arms"]["a"]["preparation"]
        self.assertEqual({k: prep[k] for k in ("status", "basis", "event_id", "request_id", "semantic_version", "semantic_recorded", "records", "exclusions")},
                         {"status": "saved", "basis": "admitted_request", "event_id": event, "request_id": "prep2.0",
                          "semantic_version": "den-semantics-3", "semantic_recorded": True, "records": 2, "exclusions": "not_recorded"})
        self.assertEqual(prep["source_coverage"]["pose"], "supplied")
        self.assertEqual(prep["freshness"]["counts"], {"fresh": 1, "historical": 1})
        self.assertEqual((prep["freshness"]["latest_visual_evidence_id"], prep["freshness"]["latest_visual_age_seconds"]), ("100", 0.8))
        sections = prep["sections"]
        self.assertEqual(sections["wrist_object_relations"]["snippet"]["json"][0]["facts"][0]["fact"], "left_wrist_near_book_in_image")
        self.assertEqual(sections["pose_geometry"]["items"], 2)
        self.assertEqual(sections["context_facts"]["items"], 1)
        # Saved but empty is recorded with zero items, never "not_recorded".
        self.assertEqual({k: sections["historical_caption"][k] for k in ("status", "items", "items_truncated")}, {"status": "recorded", "items": 0, "items_truncated": False})
        self.assertEqual(sections["historical_caption"]["snippet"]["json"], [])
        self.assertEqual(sections["observations"]["snippet"]["json"][1]["freshness"], "historical")
        for section in sections.values():
            if "snippet" in section:
                self.assertLessEqual(len(encode(section["snippet"]["json"]).encode()), lab_graph.SNIPPET_BYTES)
        # 17-joint poses exceed one snippet: bounded, still valid JSON, marked truncated.
        self.assertTrue(sections["pose_geometry"]["snippet"]["truncated"])
        json.loads(encode(sections["pose_geometry"]["snippet"]["json"]))
        # Arm B has no admitted request: nothing is invented.
        self.assertEqual(result["arms"]["b"]["preparation"], {"status": "not_saved", "basis": "admitted_request", "reason": "no_admitted_request"})
        # Causal: before the request was admitted, there is none.
        self.assertEqual(lab_graph.graph(self.store, self.sid, 1500)["arms"]["a"]["preparation"]["status"], "not_saved")

    def test_many_observations_are_counted_and_bounded(self):
        L = self.ledger
        L.activate()
        L.event("reasoning.setting", {"enabled": True, "revision": 2})
        self.semantic_request(2.0, observations=40)
        L.at(3)
        sections = lab_graph.graph(self.store, self.sid)["arms"]["a"]["preparation"]["sections"]
        self.assertEqual((sections["observations"]["items"], sections["observations"]["items_truncated"]), (40, True))
        self.assertLessEqual(len(encode(sections["observations"]["snippet"]["json"]).encode()), lab_graph.SNIPPET_BYTES)

    def test_recorded_exclusions_are_shown_and_absent_ones_stay_not_recorded(self):
        L = self.ledger
        L.activate()
        self.semantic_request(2.0, exclusions={"objects_outside_allowlist": 3})
        L.at(3)
        result = lab_graph.graph(self.store, self.sid)
        for value in (result["arms"]["a"]["preparation"]["exclusions"], self.stage(result, "a", "prepared_evidence")["exclusions"]):
            self.assertEqual((value["status"], value["basis"], value["snippet"]["json"]),
                             ("recorded", "admitted_request.state.exclusions", {"objects_outside_allowlist": 3}))

    def test_preparation_without_semantic_state_reports_sections_not_recorded(self):
        self.populate()
        prep = lab_graph.graph(self.store, self.sid)["arms"]["a"]["preparation"]
        self.assertEqual(prep["status"], "saved")
        # state.evidence was saved without pose instances: recorded, zero items.
        self.assertEqual({k: prep["sections"]["pose_geometry"][k] for k in ("status", "items")}, {"status": "recorded", "items": 0})
        # The fixture's state.semantic has no facts, context or caption key.
        self.assertEqual({k: prep["sections"]["wrist_object_relations"][k] for k in ("status", "items")}, {"status": "recorded", "items": 0})
        self.assertEqual(prep["sections"]["historical_caption"], {"status": "not_recorded"})

    def test_sections_are_not_recorded_only_when_the_request_saved_no_semantic_state(self):
        L = self.ledger
        L.activate()
        L.at(2.0)
        packet = {"model": "jev-model", "questions": {}, "state": {"site_id": "victoria", "observation_cutoff": START+1.8, "evidence": []}}
        L.event("reasoning.request", {"request_id": "plain", "arm": "a", "generation": 1, "available_at": L.now,
                                      "packet_sha256": digest(packet), "request": packet, "status": "admitted_before_transport"})
        L.at(3)
        prep = lab_graph.graph(self.store, self.sid)["arms"]["a"]["preparation"]
        self.assertEqual((prep["semantic_recorded"], prep["freshness"], prep["source_coverage"]), (False, {"basis": "not_recorded"}, None))
        self.assertEqual({k: prep["sections"]["pose_geometry"][k] for k in ("status", "items")}, {"status": "recorded", "items": 0})
        for key in ("wrist_object_relations", "observations", "context_facts", "historical_caption"):
            self.assertEqual(prep["sections"][key], {"status": "not_recorded"}, key)

    def test_live_frame_age_compares_analyzed_and_camera_frames_on_the_controller_clock(self):
        self.populate()
        L = self.ledger
        latest = L.frame(7.2)
        L.at(7.5)
        result = lab_graph.graph(self.store, self.sid)
        age = result["frame_age"]
        self.assertEqual((age["basis"], age["server_time"]), ("controller_clock", START+7.5))
        self.assertEqual({k: age["camera"][k] for k in ("source", "media_id", "clock_mapping_status")},
                         {"source": "recorded_preview_media", "media_id": latest["id"], "clock_mapping_status": "verified"})
        self.assertAlmostEqual(age["camera"]["observed_age_s"], 0.6)
        a, b = age["arms"]["a"], age["arms"]["b"]
        self.assertEqual((a["status"], a["event_id"]), ("recorded", self.a2))
        self.assertAlmostEqual(a["observed_age_s"], 1.8)
        self.assertAlmostEqual(a["behind_camera_s"], 1.2)
        self.assertAlmostEqual(b["behind_camera_s"], 5.0)
        self.assertIsNone(a["comparison_reason"])
        # A replay cursor has no server-time age.
        self.assertIsNone(lab_graph.graph(self.store, self.sid, 7000)["frame_age"])

    def test_camera_frame_is_the_rgb_stream_not_a_newer_stereo_view(self):
        self.populate()
        L = self.ledger
        rgb = L.frame(7.0)
        L.at(7.3)
        for modality in ("left", "right"):
            # Shaped like bundle_ingest views: PNG, other stream, no observed_at.
            self.store.add_media(self.sid, b"png-"+modality.encode(), "image/png", {"stream": modality, "modality": modality, "format": "gray8_png",
                                 "clock_mapping_status": "unverified", "timeline_basis": "controller_receipt"}, "view-"+modality)
        L.at(7.5)
        result = lab_graph.graph(self.store, self.sid)
        camera = result["frame_age"]["camera"]
        self.assertEqual((camera["media_id"], camera["clock_mapped"]), (rgb["id"], True))
        self.assertAlmostEqual(result["frame_age"]["arms"]["a"]["behind_camera_s"], 1.0)
        node = self.node(result, "rgb_camera")
        self.assertEqual((node["native"]["media_id"], node["native"]["content_type"]), (rgb["id"], "image/jpeg"))
        self.assertEqual((node["counts"]["rgb_images"], node["counts"]["images"]), (4, 6))

    def test_verified_status_without_a_mapping_id_is_not_clock_mapped(self):
        L = self.ledger
        L.activate()
        L.output("a", 2.0)
        L.at(2.5)
        self.store.add_media(self.sid, b"no-id", "image/jpeg", {"stream": "rgb", "observed_at": L.now-0.1, "available_at": L.now,
                                                                "clock_mapping_status": "verified"}, "no-id")
        age = lab_graph.graph(self.store, self.sid)["frame_age"]
        self.assertEqual((age["camera"]["clock_mapped"], age["arms"]["a"]["behind_camera_s"], age["arms"]["a"]["comparison_reason"]),
                         (False, None, "observation_time_not_clock_mapped"))

    def test_frame_age_is_honest_without_camera_mapping_or_output(self):
        L = self.ledger
        L.activate()
        L.at(1)
        empty = lab_graph.graph(self.store, self.sid)["frame_age"]
        self.assertEqual((empty["camera"], empty["arms"]["a"]["status"], empty["arms"]["a"]["behind_camera_s"]), (None, "none", None))
        L.output("a", 2.0)
        L.at(2.5)
        self.store.add_media(self.sid, b"unmapped", "image/jpeg", {"stream": "rgb", "available_at": L.now, "clock_mapping_status": "unverified"}, "unmapped")
        age = lab_graph.graph(self.store, self.sid)["frame_age"]
        self.assertEqual((age["camera"]["observed_at"], age["arms"]["a"]["behind_camera_s"], age["arms"]["a"]["comparison_reason"]),
                         (None, None, "observation_time_not_clock_mapped"))

    def test_ha_unavailable_items_have_a_documented_shape(self):
        L = self.ledger
        L.activate()
        first = L.at(1.0).event("ha.unavailable", {"reason": "ha_source_invalid", "available_at": L.now})
        second = L.at(2.0).event("ha.unavailable", {"reason": "ha_source_invalid", "available_at": None})
        L.at(3)
        context = lab_graph.timeline(self.store, self.sid)["tracks"]["context"]
        self.assertEqual(context["ha_unavailable"], [{"t_ms": 1000, "kind": "ha.unavailable", "event_id": first, "reason": "ha_source_invalid"},
                                                     {"t_ms": 2000, "kind": "ha.unavailable", "event_id": second, "reason": "ha_source_invalid"}])
        self.assertEqual((context["ha_unavailable_total"], context["ha_unavailable_truncated"], context["ha_unavailable_scan_truncated"]), (2, False, False))
        with patch.object(lab_graph, "TRACK_ITEMS", 1):
            capped = lab_graph.timeline(self.store, self.sid)["tracks"]["context"]
        self.assertEqual((len(capped["ha_unavailable"]), capped["ha_unavailable_total"], capped["ha_unavailable_truncated"]), (1, 2, True))

    def test_responses_carry_the_additive_contract_revision(self):
        self.populate()
        for result in (lab_graph.graph(self.store, self.sid), lab_graph.timeline(self.store, self.sid), lab_graph.replay(self.store, self.sid, 1000)):
            self.assertEqual((result["contract"], result["contract_revision"]), ("perception-lab-graph-1", 3))


class ReadScopeTests(unittest.TestCase):
    def test_graph_requires_read_capability(self):
        from fastapi.testclient import TestClient
        from app import create_app
        with tempfile.TemporaryDirectory() as directory:
            ledger = Ledger(directory, token="r"*32, capabilities=("record",))
            try:
                with TestClient(create_app(ledger.settings, store=ledger.store)) as client:
                    for path in ("/graph", "/timeline", "/replay?at_ms=0"):
                        response = client.get("/api/perception-lab/v1/sessions/"+ledger.sid+path, headers={"Authorization": "Bearer "+"r"*32})
                        self.assertEqual(response.status_code, 403, path)
            finally:
                ledger.close()


if __name__ == "__main__":
    unittest.main()
