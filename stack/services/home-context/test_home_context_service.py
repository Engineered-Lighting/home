"""Deterministic synthetic-only unit/contract checks. No HTTP/host/model calls."""
import copy
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from journal import Grant, Journal, JournalError, Limits, canonical, digest
from producer_adapters import adapt_observation, clock_seconds, map_roots, scoped_person, to_geometry_sidecar
from server import LocalServer
from schema_registry import schema_digest


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SCOPE = {"site_id": "test-site", "room_id": "test-room", "session_id": "test-session", "domain_epoch": "domain-1"}


def grants():
    types = ["fact-1", "home-context-1", "home-lighting-shadow-decision-1", "home-evidence-request-1", "home-evidence-reply-1",
             "home-intent-event-1", "home-context-consumer-feedback-1", "home-context-revocation-1", "home-geometry-1", "home-source-health-1", "home-coverage-1", "home-engagement-state-1", "home-relation-hypothesis-1"]
    return {"principal_id": "test-principal", "producer_id": "test-producer", "token_sha256": hashlib.sha256(b"synthetic-test-token").hexdigest(),
            "scopes": [{"site_id": SCOPE["site_id"], "room_id": SCOPE["room_id"]}],
            "operations": ["read", "event", "intent", "feedback", "evidence_request", "evidence_reply", "worker_commit", "revoke"],
            "payload_types": types, "schema_pins": {name: schema_digest(name) for name in types},
            "revisions": ["1.0", "r1", "r2"], "source_ids": ["test-camera"], "sensor_roots": ["physical:test-camera", "physical:other-camera"], "expected_actor_ref": "test-owner",
            "clock_mappings": {"mapping-1": {"source_id": "test-camera", "original_unit": "utc_epoch_s", "max_uncertainty_s": 0.01},
                               "geometry-mapping-1": {"source_id": "test-producer", "original_unit": "utc_epoch_s", "max_uncertainty_s": 0.01}},
            "registration_bindings": {"registration-1": {"source_id": "test-producer", "geometry_revision": "geometry-revision-1", "registry_digest": "b" * 64, "coverage_refs": ["coverage-1"]},
                                      "camera-registration-1": {"source_id": "test-camera", "geometry_revision": "geometry-revision-1", "registry_digest": "b" * 64, "coverage_refs": ["coverage-1"]}}, "expected_worker_id": "worker-1"}


def person(epoch="track-1", pid=1):
    return {**SCOPE, "track_epoch": epoch, "pid": pid}


def fact(event_id="fact-1", now=1800000000):
    return {"schema": "fact-1", "version": 1, "id": event_id, "producer_event_id": "original-" + event_id, **SCOPE,
            "person_ref": person(), "layer": "observation", "kind": "person_presence", "status": "supplied", "value": {"track_status": "tracked"},
            "observed_at": now - 1, "available_at": now, "valid_until": now + 10, "source_id": "test-camera",
            "sensor_roots": ["physical:test-camera"], "observation_roots": ["frame:original-" + event_id], "parent_evidence_ids": [],
            "clock": {"status": "commissioned", "mapping_id": "mapping-1", "uncertainty_s": 0.01, "original_unit": "utc_epoch_s", "original_value": str(now - 1)},
            "qualification": "experimental_shadow", "probability": None}


def event(payload=None, event_id=None, now=1800000000):
    payload = fact(event_id or "fact-1", now) if payload is None else payload
    return {"schema": "home-context-event-1", "schema_version": "1.0", "id": event_id or payload.get("id", "output-1"),
            "producer_id": "test-producer", "stream_epoch": "producer-epoch-1", **SCOPE,
            "observed_at": payload.get("observed_at", now), "available_at": payload.get("available_at", now),
            "valid_until": payload.get("valid_until", now + 10), "payload_type": payload["schema"], "payload_version": "1.0", "revision": "r1", "payload": payload}


def request(request_id="request-1", now=1800000000):
    return {"schema": "home-evidence-request-1", "version": 1, "request_id": request_id, "correlation_id": "correlation-1", "consumer_id": "worker-1", **SCOPE,
            "person_ref": person(), "surface_id": "surface-1", "region_id": "region-1", "existing_evidence_refs": [],
            "geometry_revision": "geometry-1", "distinction": "manipulation_vs_proximity", "permitted_precision": "registered_surface", "max_observation_age_s": 10, "deadline": now + 5,
            "budget": {"frames": 1, "model_calls": 0, "wall_s": 3}}


def reply(request_id="request-1", now=1800000000, status="supported", refs=None):
    return {"schema": "home-evidence-reply-1", "version": 1, "request_id": request_id, "correlation_id": "correlation-1", **SCOPE, "terminal_status": status,
            "available_at": now, "reason": "test-supported" if status == "supported" else "test-abstention", "evidence_refs": ["fact-1"] if refs is None else refs, "alternatives": []}


def decision(event_id="decision-1", seq=1, now=1800000000):
    return {"schema": "home-lighting-shadow-decision-1", "version": 1, "id": event_id, **SCOPE, "worker_id": "worker-1", "t": now,
            "seq": seq, "input_cursor": 1, "run_id": "synthetic-run", "stream_epoch": None, "module_hashes": {}, "schema_pins": {}, "config_digest": "a" * 64, "engagement": None, "requests": {}, "allocation": {}, "execution": {},
            "evidence_requests": [], "registered_targets": [], "input_results": [], "input_receipts": [], "additional_input_result_count": 0, "intent_results": [],
            "actuated": False, "acceptance_eligible": False, "diagnostic": {"mode": "ordinary", "truth_used_as_input": False,
            "reference_ids": [], "bypassed_stages": [], "acceptance_eligible": False, "training_eligible": False}}


def declaration(event_id="intent-1", now=1800000000):
    return {"schema": "home-intent-event-1", "version": 1, "id": event_id, **SCOPE, "actor_ref": "test-owner", "kind": "brightness", "value": 0.4,
            "set_at": now, "available_at": now, "valid_until": now + 60, "origin": "human_declaration", "truth_role": "runtime_intent"}


def engagement(now=1800000000):
    return {"schema": "home-engagement-state-1", "version": 1, **SCOPE, "t": now, "seq": 1, "valid_until": now + 3,
            "persons": [], "relations": [], "blocked": [], "producer": {"version": "synthetic-reducer", "config_digest": "a" * 64}, "simulation_only": True, "acceptance_eligible": False}


def worker_commit(journal, event_id="commit-1", seq=1, input_cursor=0, expected_input_cursor=0, input_receipts=None):
    receipts = [] if input_receipts is None else input_receipts
    output = decision("output-" + event_id, seq)
    output.update(input_cursor=input_cursor, input_receipts=receipts, stream_epoch=journal.stream_epoch)
    return {"schema": "home-context-worker-commit-1", "schema_version": "1.0", "id": event_id, "worker_id": "worker-1", "producer_id": "test-producer",
            "stream_epoch": journal.stream_epoch, "site_id": SCOPE["site_id"], "room_id": SCOPE["room_id"], "expected_input_cursor": expected_input_cursor,
            "expected_context_seq": seq - 1, "input_cursor": input_cursor, "input_receipts": receipts,
            "checkpoint": {"config_digest": "a" * 64, "domain_epoch": SCOPE["domain_epoch"], "revocation_generation": 0, "state": {"seq": seq}},
            "publications": [event(output)]}


def geometry(now=1800000000):
    return {"schema": "home-geometry-1", "version": 1, "id": "geometry-1", **SCOPE, "frame": "z_up_metric_floor0", "units": "m",
            "geometry_revision": "geometry-revision-1", "registration_ref": "registration-1", "registry_digest": "b" * 64,
            "observed_at": now - 1, "available_at": now, "valid_until": now + 10, "coverage_refs": ["coverage-1"], "person_ref": person(),
            "association_ref": "association-1", "authorized_geometry": {"body_center": {"x": 0, "y": 0, "z": 1}},
            "clock": {"status": "commissioned", "mapping_id": "geometry-mapping-1", "uncertainty_s": 0.01, "original_unit": "utc_epoch_s", "original_value": str(now - 1)}}


class JournalTests(unittest.TestCase):
    def setUp(self):
        # This directory contains only files created by this test invocation.
        self.work = tempfile.TemporaryDirectory(prefix="synthetic-journal-test-", dir=HERE)
        self.path = Path(self.work.name) / "synthetic-journal.db"
        self.now = 1800000000
        self.journal = Journal(self.path, grants=[grants()], clock=lambda: self.now, evidence_router="registered-synthetic-pending")
        self.grant = self.journal.authenticate("synthetic-test-token")

    def tearDown(self):
        self.journal.close()
        self.work.cleanup()

    def code(self, code, action):
        with self.assertRaises(JournalError) as raised:
            action()
        self.assertEqual(raised.exception.code, code)

    def test_no_default_auth_and_scope_isolation(self):
        self.code("unauthorized", lambda: self.journal.authenticate("wrong"))
        self.code("forbidden", lambda: self.journal.snapshot(self.grant, "test-site", "other-room"))
        denied = Grant.from_dict({**grants(), "operations": []})
        self.code("forbidden", lambda: self.journal.append_event(event(), denied))
        wrong_source = event(); wrong_source["payload"]["source_id"] = "ungranted-source"
        self.code("forbidden", lambda: self.journal.append_event(wrong_source, self.grant))

    def test_durable_exact_retry_and_changed_payload(self):
        original = event()
        receipt = self.journal.append_event(original, self.grant)
        epoch = self.journal.stream_epoch
        self.journal.close()
        self.journal = Journal(self.path, grants=[grants()], clock=lambda: self.now)
        self.assertEqual(self.journal.stream_epoch, epoch)
        self.assertEqual(self.journal.append_event(original, self.grant), receipt)
        altered = copy.deepcopy(original); altered["payload"]["value"] = {"track_status": "coasting"}
        self.code("identity_conflict", lambda: self.journal.append_event(altered, self.grant))
        self.assertEqual(self.journal.snapshot(self.grant, "test-site", "test-room")["cursor"], 1)

    def test_payload_clock_and_pid_scope_rejected(self):
        bad = event(); bad["payload"]["person_ref"]["track_epoch"] = ""
        self.code("invalid_payload", lambda: self.journal.append_event(bad, self.grant))
        bad = event(); bad["payload"]["person_ref"]["room_id"] = "wrong"
        self.code("scope_mismatch", lambda: self.journal.append_event(bad, self.grant))
        bad = event(); bad["available_at"] += 1
        self.code("invalid_clock", lambda: self.journal.append_event(bad, self.grant))
        bad = event(); bad["payload"]["clock"]["status"] = "unverified"
        self.code("invalid_clock", lambda: self.journal.append_event(bad, self.grant))

    def test_retention_requires_atomic_snapshot_reset(self):
        self.journal.close()
        self.journal = Journal(self.path, grants=[grants()], clock=lambda: self.now, limits=Limits(max_publications=2))
        for n in range(4):
            self.journal.append_event(event(event_id=f"fact-{n}"), self.grant)
        self.code("reset_required", lambda: self.journal.page(self.grant, "test-site", "test-room", after=0))
        snapshot = self.journal.snapshot(self.grant, "test-site", "test-room")
        self.assertEqual(snapshot["cursor"], 4)
        self.assertEqual(snapshot["items"][-1]["cursor"], 4)
        page = self.journal.page(self.grant, "test-site", "test-room", after=snapshot["cursor"], stream_epoch=snapshot["stream_epoch"])
        self.assertEqual(page["items"], [])
        self.assertEqual(page["next_cursor"], 4)

    def test_worker_commit_atomic_checkpoint_output_and_restart(self):
        self.journal.append_event(event(), self.grant)
        output = event(decision(), "decision-1")
        commit = {"schema": "home-context-worker-commit-1", "schema_version": "1.0", "id": "commit-1", "worker_id": "worker-1", "producer_id": "test-producer",
                  "stream_epoch": self.journal.stream_epoch, "site_id": "test-site", "room_id": "test-room", "expected_input_cursor": 0,
                  "expected_context_seq": 0, "input_cursor": 1, "input_receipts": [], "checkpoint": {"config_digest": "a" * 64, "domain_epoch": "domain-1", "revocation_generation": 0, "state": {"seq": 1}}, "publications": [output]}
        self.code("unseen_cursor", lambda: self.journal.worker_commit(commit, self.grant))
        self.journal.page(self.grant, "test-site", "test-room", consumer_id="worker-1")
        saved = self.journal.worker_commit(commit, self.grant)
        self.assertEqual(self.journal.worker_commit(commit, self.grant), saved)
        self.assertEqual(saved["context_seq"], 1)
        self.assertEqual(self.journal.checkpoint("worker-1", self.grant, "test-site", "test-room")["input_cursor"], 1)
        failed = copy.deepcopy(commit); failed.update(id="commit-2", expected_input_cursor=1, expected_context_seq=1, input_cursor=2)
        failed["checkpoint"]["state"]["seq"] = 2
        failed["publications"][0]["payload"]["seq"] = 2
        self.code("unseen_cursor", lambda: self.journal.worker_commit(failed, self.grant))
        self.assertEqual(self.journal.snapshot(self.grant, "test-site", "test-room")["cursor"], 2)

    def test_worker_outputs_rollback_together_on_identity_conflict(self):
        self.journal.append_event(event(), self.grant)
        self.journal.snapshot(self.grant, "test-site", "test-room", "worker-1")
        one = event(decision("output-1"), "output-1")
        two = copy.deepcopy(one); two["payload"]["diagnostic"]["truth_used_as_input"] = True
        commit = {"schema": "home-context-worker-commit-1", "schema_version": "1.0", "id": "bad-commit", "worker_id": "worker-1", "producer_id": "test-producer", "stream_epoch": self.journal.stream_epoch,
                  "site_id": "test-site", "room_id": "test-room", "expected_input_cursor": 0, "expected_context_seq": 0, "input_cursor": 1, "input_receipts": [],
                  "checkpoint": {"config_digest": "b" * 64, "domain_epoch": "domain-1", "revocation_generation": 0, "state": {"seq": 1}}, "publications": [one, two]}
        self.code("identity_conflict", lambda: self.journal.worker_commit(commit, self.grant))
        self.assertEqual(self.journal.snapshot(self.grant, "test-site", "test-room")["cursor"], 1)
        self.assertIsNone(self.journal.checkpoint("worker-1", self.grant, "test-site", "test-room")["checkpoint"])

    def test_one_active_request_scoped_and_terminal_reply(self):
        self.journal.append_event(event(), self.grant)
        pending = self.journal.request_evidence(request(), self.grant)
        self.assertEqual(pending["status"], "pending")
        self.assertEqual(self.journal.request_evidence(request(), self.grant), pending)
        self.code("request_active", lambda: self.journal.request_evidence(request("request-2"), self.grant))
        self.code("unsupported_evidence", lambda: self.journal.reply_evidence(reply(refs=["missing-fact"]), self.grant))
        terminal = self.journal.reply_evidence(reply(), self.grant)
        self.assertEqual(terminal["status"], "supported")
        self.assertEqual(self.journal.reply_evidence(reply(), self.grant), terminal)
        self.code("terminal_conflict", lambda: self.journal.reply_evidence(reply(status="abstained", refs=[]), self.grant))

    def test_expiry_and_unconfigured_source_terminal(self):
        self.journal.request_evidence(request(), self.grant)
        self.now += 6
        found = self.journal.lookup_request("request-1", self.grant, "test-site", "test-room")
        self.assertEqual(found["terminal_reply"]["terminal_status"], "expired")
        self.assertEqual(found["terminal_reply"]["evidence_refs"], [])
        self.journal.evidence_router = None
        self.journal.request_evidence(request("request-unconfigured", self.now), self.grant)
        found = self.journal.lookup_request("request-unconfigured", self.grant, "test-site", "test-room")
        self.assertEqual(found["terminal_reply"]["terminal_status"], "unavailable")
        self.assertEqual(found["terminal_reply"]["reason"], "source_not_provisioned")

    def test_intent_actor_feedback_not_truth(self):
        declaration = {"schema": "home-intent-event-1", "version": 1, "id": "intent-1", **SCOPE, "actor_ref": "test-owner", "kind": "brightness", "value": 0.4,
                       "set_at": self.now, "available_at": self.now, "valid_until": self.now + 60, "origin": "human_declaration", "truth_role": "runtime_intent"}
        wrong = dict(declaration, actor_ref="other-owner")
        self.code("forbidden", lambda: self.journal.intent(wrong, self.grant))
        receipt = self.journal.intent(declaration, self.grant)
        self.assertEqual(receipt["status"], "accepted")
        self.assertEqual(self.journal.lookup_intent("intent-1", self.grant, "test-site", "test-room")["event"], declaration)
        feedback = {"schema": "home-context-consumer-feedback-1", "version": 1, "id": "feedback-1", "consumer_id": "worker-1", **SCOPE, "decision_ref": "decision-1", "input_results": [], "unresolved": [], "evidence_refs": [], "truth_role": "gold"}
        self.code("invalid_payload", lambda: self.journal.feedback(feedback, self.grant))

    def test_revocation_invalidates_snapshot_and_future_source(self):
        self.journal.append_event(event(), self.grant)
        control = event({"schema": "home-context-revocation-1", "version": 1, "id": "revoke-1", **SCOPE, "source_id": "test-camera", "revision": "r1", "reason": "source_retired", "available_at": self.now}, "revoke-1")
        receipt = self.journal.revoke(control, self.grant)
        self.assertEqual(self.journal.revoke(control, self.grant), receipt)
        self.assertEqual(self.journal.revocation_generation("test-site", "test-room"), 1)
        self.assertFalse(any(p["payload_type"] == "fact-1" for p in self.journal.snapshot(self.grant, "test-site", "test-room")["items"]))
        self.code("revoked", lambda: self.journal.append_event(event(event_id="new-fact"), self.grant))

    def test_identity_budget_fail_is_atomic(self):
        self.journal.close()
        self.journal = Journal(self.path, grants=[grants()], clock=lambda: self.now, limits=Limits(max_receipts=1))
        self.journal.append_event(event(), self.grant)
        self.code("journal_full", lambda: self.journal.append_event(event(event_id="second-fact"), self.grant))
        self.assertEqual(self.journal.snapshot(self.grant, "test-site", "test-room")["cursor"], 1)

    def test_loopback_only_rejected_before_socket(self):
        with self.assertRaises(ValueError):
            LocalServer(("0.0.0.0", 8768), self.journal)

    def test_schema_pins_required_exact_and_unknown_types_fail_closed(self):
        config = grants(); del config["schema_pins"]
        with self.assertRaises(ValueError):
            Journal(self.path, grants=[config])
        config = grants(); config["schema_pins"]["fact-1"] = "0" * 64
        with self.assertRaises(ValueError):
            Journal(self.path, grants=[config])
        config = grants(); config["payload_types"].append("unregistered-1"); config["schema_pins"]["unregistered-1"] = "0" * 64
        with self.assertRaises(ValueError):
            Journal(self.path, grants=[config])
        malformed = event(); malformed["payload"]["value"]["x"] = 1
        self.code("invalid_payload", lambda: self.journal.append_event(malformed, self.grant))
        malformed = event(); malformed["payload"]["kind"] = "new-predicate"
        self.code("invalid_payload", lambda: self.journal.append_event(malformed, self.grant))

    def test_read_grants_filter_types_revisions_and_advance_hidden_cursor(self):
        self.journal.append_event(event(), self.grant)
        self.journal.append_event(event(geometry()), self.grant)
        newer = event(event_id="fact-2"); newer["revision"] = "r2"; newer["payload"].update(kind="orientation", value=True)
        self.journal.append_event(newer, self.grant)
        config = grants(); config.update(operations=["read"], payload_types=["fact-1"], revisions=["r1"], schema_pins={"fact-1": schema_digest("fact-1")})
        reader = Grant.from_dict(config)
        snapshot = self.journal.snapshot(reader, "test-site", "test-room")
        self.assertEqual(snapshot["cursor"], 3)
        self.assertEqual([p["id"] for p in snapshot["items"]], ["fact-1"])
        first = self.journal.page(reader, "test-site", "test-room", limit=1, consumer_id="restricted-reader")
        self.assertEqual(first["next_cursor"], 1)
        hidden = self.journal.page(reader, "test-site", "test-room", after=1, consumer_id="restricted-reader")
        self.assertEqual(hidden["items"], [])
        self.assertEqual(hidden["next_cursor"], 3)
        self.code("forbidden", lambda: self.journal.lookup_request("unseen", reader, "test-site", "test-room"))
        self.code("forbidden", lambda: self.journal.lookup_intent("unseen", reader, "test-site", "test-room"))
        self.code("forbidden", lambda: self.journal.checkpoint("worker-1", reader, "test-site", "test-room"))

    def test_original_root_cannot_change_sensor_or_capture_time(self):
        self.journal.append_event(event(), self.grant)
        changed = event(event_id="fact-2"); changed["payload"]["observation_roots"] = ["frame:original-fact-1"]
        changed["payload"]["sensor_roots"] = ["physical:other-camera"]
        self.code("root_conflict", lambda: self.journal.append_event(changed, self.grant))
        changed["payload"]["sensor_roots"] = ["physical:test-camera"]
        changed["payload"]["observed_at"] += 1; changed["observed_at"] += 1
        changed["payload"]["clock"]["original_value"] = str(changed["observed_at"])
        self.code("root_time_conflict", lambda: self.journal.append_event(changed, self.grant))
        self.assertEqual(self.journal.snapshot(self.grant, "test-site", "test-room")["cursor"], 1)

    def test_clock_mapping_claim_requires_commissioned_source_grant(self):
        denied = grants(); denied["clock_mappings"] = {}
        self.code("forbidden", lambda: self.journal.append_event(event(), Grant.from_dict(denied)))
        bad = event(); bad["payload"]["clock"]["original_value"] = str(self.now - 10)
        self.code("invalid_clock", lambda: self.journal.append_event(bad, self.grant))
        bad = event(); bad["payload"]["clock"]["uncertainty_s"] = 0.02
        self.code("forbidden", lambda: self.journal.append_event(bad, self.grant))
        bad = event(); bad["payload"]["clock"]["mapping_id"] = "geometry-mapping-1"
        self.code("forbidden", lambda: self.journal.append_event(bad, self.grant))

    def test_multi_root_window_cannot_refresh_old_capture(self):
        bad = event(); roots = bad["payload"]["observation_roots"]
        bad["payload"]["observation_root_times"] = {roots[0]: self.now - 2}
        self.code("invalid_root_times", lambda: self.journal.append_event(bad, self.grant))
        bad["payload"]["observation_root_times"][roots[0]] = self.now - 1
        bad["payload"]["observation_window"] = {"start": self.now - 2, "end": self.now - 2}
        self.code("invalid_root_times", lambda: self.journal.append_event(bad, self.grant))
        bad["payload"].pop("observation_window"); bad["payload"].pop("observation_root_times")
        roots.append("frame:second-original")
        self.code("root_times_required", lambda: self.journal.append_event(bad, self.grant))

    def test_context_sequence_cas_even_when_consumed_cursor_does_not_advance(self):
        self.journal.snapshot(self.grant, "test-site", "test-room", "worker-1")
        first = worker_commit(self.journal)
        self.journal.worker_commit(first, self.grant)
        competing = worker_commit(self.journal, "competing-commit")
        self.code("checkpoint_conflict", lambda: self.journal.worker_commit(competing, self.grant))
        second = worker_commit(self.journal, "commit-2", seq=2)
        self.journal.worker_commit(second, self.grant)
        self.assertEqual(self.journal.checkpoint("worker-1", self.grant, "test-site", "test-room")["context_seq"], 2)

    def test_input_receipt_metadata_matches_durable_scoped_delivery(self):
        receipt = self.journal.append_event(event(), self.grant)
        self.journal.snapshot(self.grant, "test-site", "test-room", "worker-1")
        refs = [{"cursor": 1, "id": "fact-1", "payload_digest": receipt["payload_digest"], "received_at": self.now}]
        commit = worker_commit(self.journal, input_cursor=1, input_receipts=refs)
        bad = copy.deepcopy(commit)
        bad["input_receipts"][0]["payload_digest"] = "0" * 64
        bad["publications"][0]["payload"]["input_receipts"] = copy.deepcopy(bad["input_receipts"])
        self.code("invalid_input_receipt", lambda: self.journal.worker_commit(bad, self.grant))
        bad = copy.deepcopy(commit)
        bad["input_receipts"][0]["received_at"] = self.now - 1
        bad["publications"][0]["payload"]["input_receipts"] = copy.deepcopy(bad["input_receipts"])
        self.code("invalid_input_receipt", lambda: self.journal.worker_commit(bad, self.grant))
        self.journal.worker_commit(commit, self.grant)
        self.assertEqual(self.journal.worker_commit(commit, self.grant)["context_seq"], 1)

    def test_byte_age_ring_sustains_admission_and_hash_retries(self):
        self.journal.close()
        self.journal = Journal(self.path, grants=[grants()], clock=lambda: self.now,
                               limits=Limits(max_publications=4, max_publication_bytes=4096, retention_seconds=4))
        original, saved = event(), None
        for n in range(80):
            current = event(event_id=f"synthetic-{n}", now=self.now)
            if n == 0:
                original = current
            receipt = self.journal.append_event(current, self.grant)
            if n == 0:
                saved = receipt
            self.now += 1
        self.assertEqual(self.journal.append_event(original, self.grant), saved)
        self.assertLessEqual(self.journal.db.execute("SELECT count(*) FROM publications").fetchone()[0], 4)
        self.assertLessEqual(self.journal.db.execute("SELECT coalesce(sum(length(CAST(body AS BLOB))),0) FROM publications").fetchone()[0], 4096)
        self.code("reset_required", lambda: self.journal.page(self.grant, "test-site", "test-room", after=0))
        first_floor = int(self.journal._meta("retained_after"))
        self.now += 10
        self.journal.snapshot(self.grant, "test-site", "test-room")
        age_floor = int(self.journal._meta("retained_after"))
        self.assertGreater(age_floor, first_floor)
        self.journal.append_event(event(event_id="after-age-prune", now=self.now), self.grant)
        self.assertGreaterEqual(int(self.journal._meta("retained_after")), age_floor)
        stored = self.journal.db.execute("SELECT event FROM receipts WHERE event_id=?", (original["id"],)).fetchone()[0]
        self.assertEqual(json.loads(stored)["schema"], "home-context-event-tombstone-1")
        self.assertEqual(self.journal.append_event(original, self.grant), saved)

    def test_source_health_and_coverage_have_typed_source_scope(self):
        base = {"version": 1, **SCOPE, "source_id": "test-camera", "observed_at": self.now - 1, "available_at": self.now, "valid_until": self.now + 3}
        health = {**base, "schema": "home-source-health-1", "id": "health-1", "status": "functional", "reason": "synthetic_receipt"}
        coverage = {**base, "schema": "home-coverage-1", "id": "coverage-1", "status": "observed", "geometry_revision": "geometry-1", "registration_ref": "registration-1", "surface_ids": ["surface-1"], "evidence_refs": ["health-1"]}
        self.journal.append_event(event(health), self.grant)
        self.journal.append_event(event(coverage), self.grant)
        bad = copy.deepcopy(coverage); bad["evidence_refs"] = []
        self.code("invalid_evidence_refs", lambda: self.journal.append_event(event(bad), self.grant))

    def test_spatial_registration_and_coverage_require_commissioned_grants(self):
        bad = geometry(); bad["registration_ref"] = "uncommissioned-registration"
        self.code("forbidden", lambda: self.journal.append_event(event(bad), self.grant))
        bad = geometry(); bad["coverage_refs"] = ["uncommissioned-coverage"]
        self.code("forbidden", lambda: self.journal.append_event(event(bad), self.grant))
        bad = geometry(); bad["registry_digest"] = "0" * 64
        self.code("forbidden", lambda: self.journal.append_event(event(bad), self.grant))
        good = event()
        good["payload"]["spatial"] = {"geometry_revision": "geometry-revision-1", "registration_ref": "camera-registration-1", "registry_digest": "b" * 64,
                                      "coverage_refs": ["coverage-1"], "association_ref": "association-1", "registration_verified": True, "coverage_status": "observed", "association_status": "qualified"}
        self.journal.append_event(good, self.grant)
        denied = grants(); denied["registration_bindings"] = {}
        new_fact = copy.deepcopy(good); new_fact["id"] = "new-fact"; new_fact["payload"]["id"] = "new-fact"
        self.code("forbidden", lambda: self.journal.append_event(new_fact, Grant.from_dict(denied)))

    def test_hidden_revocation_generation_advances_on_page_and_snapshot(self):
        self.journal.append_event(event(), self.grant)
        config = grants(); config.update(operations=["read"], payload_types=["fact-1"], schema_pins={"fact-1": schema_digest("fact-1")})
        reader = Grant.from_dict(config)
        snapshot = self.journal.snapshot(reader, "test-site", "test-room")
        self.assertEqual(snapshot["revocation_generation"], 0)
        control = event({"schema": "home-context-revocation-1", "version": 1, "id": "revoke-1", **SCOPE,
                         "source_id": "test-camera", "revision": "r1", "reason": "source_retired", "available_at": self.now}, "revoke-1")
        self.journal.revoke(control, self.grant)
        page = self.journal.page(reader, "test-site", "test-room", after=snapshot["cursor"])
        self.assertEqual(page["items"], [])
        self.assertEqual(page["revocation_generation"], 1)
        self.assertEqual(page["next_cursor"], 2)
        after = self.journal.snapshot(reader, "test-site", "test-room")
        self.assertEqual(after["revocation_generation"], 1)
        self.assertEqual(after["items"], [])

    def test_worker_checkpoint_identity_bound_to_principal(self):
        self.code("forbidden", lambda: self.journal.checkpoint("other-worker", self.grant, "test-site", "test-room"))
        commit = worker_commit(self.journal); commit["worker_id"] = "other-worker"
        self.code("forbidden", lambda: self.journal.worker_commit(commit, self.grant))
        config = grants(); del config["expected_worker_id"]
        with self.assertRaises(ValueError):
            Grant.from_dict(config)

    def test_one_configured_worker_authority_per_room_before_database_opens(self):
        first = grants()
        for field in ("principal_id", "producer_id", "expected_worker_id"):
            other = copy.deepcopy(first)
            other[field] = "other-authority"
            other["token_sha256"] = hashlib.sha256(field.encode()).hexdigest()
            path = Path(self.work.name) / (field + ".db")
            with self.assertRaisesRegex(ValueError, "one configured worker authority"):
                Journal(path, grants=[first, other], clock=lambda: self.now)
            self.assertFalse(path.exists())
        rotation = copy.deepcopy(first)
        rotation["token_sha256"] = hashlib.sha256(b"rotated-test-token").hexdigest()
        other_room = copy.deepcopy(rotation)
        other_room.update(principal_id="other-authority", producer_id="other-producer", expected_worker_id="other-worker",
                          scopes=[{"site_id": "test-site", "room_id": "other-room"}], token_sha256=hashlib.sha256(b"other-room-token").hexdigest())
        accepted = Journal(Path(self.work.name) / "rotation.db", grants=[first, rotation, other_room], clock=lambda: self.now)
        accepted.close()

    def test_shared_outputs_require_worker_commit_and_legacy_context_remains_writable(self):
        for payload in (decision(), engagement()):
            self.code("worker_commit_required", lambda: self.journal.append_event(event(payload), self.grant))
        legacy = json.loads((ROOT / "contracts/home-context/fixtures/valid/home-context-1-minimal.json").read_text(encoding="utf-8"))
        legacy.update(site_id="victoria", room_id="test-room", t=self.now, valid_until=self.now + 3, domain_epoch="domain-1")
        envelope = event(legacy, "legacy-context-1"); envelope["site_id"] = "victoria"
        config = grants(); config["scopes"].append({"site_id": "victoria", "room_id": "test-room"})
        self.assertEqual(self.journal.append_event(envelope, Grant.from_dict(config))["status"], "accepted")

    def test_intent_application_lookup_survives_missed_decision_and_restart(self):
        original = self.journal.intent(declaration(), self.grant)
        self.assertNotIn("application_result", self.journal.lookup_intent("intent-1", self.grant, "test-site", "test-room"))
        self.journal.snapshot(self.grant, "test-site", "test-room", "worker-1")
        commit = worker_commit(self.journal, input_cursor=1)
        output = commit["publications"][0]["payload"]
        output["intent_results"] = [{"id": "intent-1", "status": "applied", "reason": "accepted", "decision_ref": output["id"]}]
        saved = self.journal.worker_commit(commit, self.grant)
        self.assertEqual(self.journal.worker_commit(commit, self.grant), saved)
        later = worker_commit(self.journal, "commit-2", seq=2, input_cursor=1, expected_input_cursor=1)
        self.journal.worker_commit(later, self.grant)
        self.assertTrue(all(not p["payload"].get("intent_results") for p in self.journal.snapshot(self.grant, "test-site", "test-room")["items"]))
        self.journal.close(); self.journal = Journal(self.path, grants=[grants()], clock=lambda: self.now)
        config = grants(); config.update(operations=["read"], payload_types=["home-intent-event-1"], schema_pins={"home-intent-event-1": schema_digest("home-intent-event-1")})
        found = self.journal.lookup_intent("intent-1", Grant.from_dict(config), "test-site", "test-room")
        self.assertEqual(found["receipt"], original)
        self.assertEqual(found["application_result"], {"schema": "home-intent-application-result-1", "version": 1, **output["intent_results"][0]})
        self.assertEqual(found["receipt"]["status"], "accepted")
        self.assertEqual(self.journal.intent(declaration(), self.grant), original)

    def test_intent_application_binds_unique_available_consumed_full_scope(self):
        self.journal.intent(declaration(), self.grant)
        self.journal.snapshot(self.grant, "test-site", "test-room", "worker-1")
        valid = worker_commit(self.journal, input_cursor=1)
        output = valid["publications"][0]["payload"]
        output["intent_results"] = [{"id": "intent-1", "status": "rejected", "reason": "unresolved_person", "decision_ref": output["id"]}]
        invalid = []
        bad = copy.deepcopy(valid); bad["publications"][0]["payload"]["intent_results"][0]["id"] = "invented-intent"; invalid.append(bad)
        bad = copy.deepcopy(valid); bad["publications"][0]["payload"]["intent_results"][0]["decision_ref"] = "other-decision"; invalid.append(bad)
        bad = copy.deepcopy(valid); bad["publications"][0]["session_id"] = bad["publications"][0]["payload"]["session_id"] = "other-session"; invalid.append(bad)
        bad = copy.deepcopy(valid); bad["input_cursor"] = bad["publications"][0]["payload"]["input_cursor"] = 0; invalid.append(bad)
        bad = copy.deepcopy(valid); bad["publications"][0]["payload"]["t"] = self.now - 1; invalid.append(bad)
        for bad in invalid:
            self.code("invalid_application_result", lambda: self.journal.worker_commit(bad, self.grant))
            self.assertEqual(self.journal.snapshot(self.grant, "test-site", "test-room")["cursor"], 1)
            self.assertNotIn("application_result", self.journal.lookup_intent("intent-1", self.grant, "test-site", "test-room"))
        other = grants(); other["producer_id"] = "second-intent-producer"
        self.journal.intent(declaration(), Grant.from_dict(other))
        self.journal.snapshot(self.grant, "test-site", "test-room", "worker-1")
        valid["input_cursor"] = valid["publications"][0]["payload"]["input_cursor"] = 2
        self.code("invalid_application_result", lambda: self.journal.worker_commit(valid, self.grant))

    def test_intent_application_rolls_back_and_conflicting_terminal_rejected(self):
        self.journal.intent(declaration(), self.grant)
        self.journal.snapshot(self.grant, "test-site", "test-room", "worker-1")
        commit = worker_commit(self.journal, input_cursor=1)
        output = commit["publications"][0]["payload"]
        output["intent_results"] = [{"id": "intent-1", "status": "rejected", "reason": "unresolved_person", "decision_ref": output["id"]}]
        duplicate = copy.deepcopy(commit["publications"][0]); duplicate["payload"]["diagnostic"]["truth_used_as_input"] = True
        commit["publications"].append(duplicate)
        self.code("identity_conflict", lambda: self.journal.worker_commit(commit, self.grant))
        self.assertNotIn("application_result", self.journal.lookup_intent("intent-1", self.grant, "test-site", "test-room"))
        self.assertIsNone(self.journal.checkpoint("worker-1", self.grant, "test-site", "test-room")["checkpoint"])
        commit["publications"].pop(); self.journal.worker_commit(commit, self.grant)
        prior = self.journal.lookup_intent("intent-1", self.grant, "test-site", "test-room")["application_result"]
        repeated = worker_commit(self.journal, "commit-2", seq=2, input_cursor=1, expected_input_cursor=1)
        repeated["publications"][0]["payload"]["intent_results"] = [dict(output["intent_results"][0], decision_ref=repeated["publications"][0]["payload"]["id"])]
        self.journal.worker_commit(repeated, self.grant)
        changed = worker_commit(self.journal, "commit-3", seq=3, input_cursor=1, expected_input_cursor=1)
        changed["publications"][0]["payload"]["intent_results"] = [dict(output["intent_results"][0], status="applied", decision_ref=changed["publications"][0]["payload"]["id"])]
        self.code("application_result_conflict", lambda: self.journal.worker_commit(changed, self.grant))
        self.assertEqual(self.journal.checkpoint("worker-1", self.grant, "test-site", "test-room")["context_seq"], 2)
        self.assertEqual(self.journal.lookup_intent("intent-1", self.grant, "test-site", "test-room")["application_result"], prior)

    def test_semantic_metadata_rejects_coordinate_objects(self):
        bad = event(); bad["payload"]["model"] = {"model_id": "model-1", "body_center": {"x": 1, "y": 2, "z": 3}}
        self.code("invalid_payload", lambda: self.journal.append_event(bad, self.grant))
        bad = event(); bad["payload"]["legacy_lineage"] = {"map": {"map_id": "map-1", "revision": "r1", "model_sha256": "a" * 64, "points": [[1, 2, 3]]}}
        self.code("invalid_payload", lambda: self.journal.append_event(bad, self.grant))

    def test_standalone_engagement_read_grant_excludes_lighting_allocation(self):
        self.journal.snapshot(self.grant, "test-site", "test-room", "worker-1")
        commit = worker_commit(self.journal)
        semantic = {"schema": "home-engagement-state-1", "version": 1, **SCOPE, "t": self.now, "seq": 1, "valid_until": self.now + 3,
                    "persons": [], "relations": [], "blocked": [], "producer": {"version": "synthetic-reducer", "config_digest": "a" * 64}, "simulation_only": True, "acceptance_eligible": False}
        output = event(semantic, "engagement-1"); output["valid_until"] = semantic["valid_until"]
        commit["publications"].append(output)
        self.journal.worker_commit(commit, self.grant)
        config = grants(); config.update(operations=["read"], payload_types=["home-engagement-state-1"], schema_pins={"home-engagement-state-1": schema_digest("home-engagement-state-1")})
        reader = Grant.from_dict(config)
        page = self.journal.page(reader, "test-site", "test-room")
        self.assertEqual([p["payload_type"] for p in page["items"]], ["home-engagement-state-1"])
        self.assertNotIn("allocation", page["items"][0]["payload"])
        self.assertEqual(self.journal.snapshot(reader, "test-site", "test-room")["items"], page["items"])
        control = event({"schema": "home-context-revocation-1", "version": 1, "id": "revoke-1", **SCOPE,
                         "source_id": "test-camera", "revision": "r1", "reason": "source_retired", "available_at": self.now}, "revoke-1")
        self.journal.revoke(control, self.grant)
        self.assertEqual(self.journal.snapshot(reader, "test-site", "test-room")["items"], [])


class AdapterTests(unittest.TestCase):
    def code(self, code, action):
        with self.assertRaises(JournalError) as raised:
            action()
        self.assertEqual(raised.exception.code, code)

    def test_clock_unit_and_explicit_origin(self):
        mapping = {"status": "commissioned", "mapping_id": "clock-1", "uncertainty_s": 0.01}
        seconds, clock = clock_seconds("1800000000123456789", "utc_epoch_ns", mapping)
        self.assertAlmostEqual(seconds, 1800000000.1234567)
        self.assertEqual(clock["original_value"], "1800000000123456789")
        self.code("adapter_clock_unverified", lambda: clock_seconds(1, "device_relative_s", mapping))
        self.code("adapter_clock_invalid", lambda: clock_seconds(1, "guess", mapping))

    def test_roots_and_observation_local_instance(self):
        self.code("adapter_roots_unbound", lambda: map_roots(["media:1"], {}))
        person_ref, association = scoped_person(SCOPE, instance_ref="object:7")
        self.assertIsNone(person_ref)
        self.assertEqual(association["status"], "unresolved")
        self.code("adapter_association_unqualified", lambda: scoped_person(SCOPE, {"status": "experimental", "person_ref": person()}))

    def test_model_receipt_keeps_original_ttl_and_correlation(self):
        source = {"schema": "qwen-observation-1", "status": "valid", **SCOPE, "observed_at": 1800000000, "available_at": 1800000001,
                  "first_received_at": 1800000001, "valid_until": 1800000010, "evidence_roots": ["media:1", "media:2"],
                  "arm_id": "synthetic-arm", "generation": 3, "capture_epoch": "synthetic-capture-epoch"}
        binding = {"dialect": "qwen-observation-1", **SCOPE, "source_id": "qwen-1", "producer_event_id": "request-1", "fact_id": "model-fact-1",
                   "clock": {"status": "commissioned", "mapping_id": "clock-1", "uncertainty_s": 0.01},
                   "root_bindings": {"media:1": {"sensor_root": "physical:test-camera", "observation_root": "frame:1", "observed_at": 1799999999},
                                     "media:2": {"sensor_root": "physical:test-camera", "observation_root": "frame:2", "observed_at": 1800000000}}}
        output = adapt_observation(source, dialect="qwen-observation-1", binding=binding, kind="model_cues", fact_value={"held_object": "book"})
        self.assertEqual(output["valid_until"], 1800000010)
        self.assertEqual(output["sensor_roots"], ["physical:test-camera"])
        self.assertIsNone(output["person_ref"])
        self.assertEqual(output["layer"], "interpretation")
        self.assertEqual(output["legacy_lineage"], {"dialect": "qwen-observation-1", "arm_id": "synthetic-arm", "generation": 3, "capture_epoch": "synthetic-capture-epoch"})
        self.code("adapter_dialect_unknown", lambda: adapt_observation(source, dialect="qwen-scene-projection-1", binding=binding, kind="x", fact_value=True))

    def test_geometry_sidecar_requires_matching_single_sensor_registration(self):
        observation = fact()
        observation["spatial"] = {"geometry_revision": "geometry-1", "registration_ref": "registration-1", "registry_digest": "b" * 64,
                                   "coverage_refs": ["coverage-1"], "association_ref": "association-1", "registration_verified": True,
                                   "coverage_status": "observed", "association_status": "qualified"}
        registration = {"id": "sidecar-1", "frame": "z_up_metric_floor0", "units": "m", "geometry_revision": "geometry-1", "registration_ref": "registration-1",
                        "registry_digest": "b" * 64, "coverage_refs": ["coverage-1"], "association_ref": "association-1", "authorized_geometry": {"body_center": {"x": 0, "y": 0, "z": 1}}}
        output = to_geometry_sidecar(observation, registration)
        self.assertEqual(output["schema"], "home-geometry-1")
        self.assertNotIn("authorized_geometry", observation)
        observation["sensor_roots"].append("physical:other-camera")
        self.code("adapter_geometry_unbound", lambda: to_geometry_sidecar(observation, registration))
        observation["sensor_roots"].pop(); registration["registration_ref"] = "different-registration"
        self.code("adapter_geometry_unbound", lambda: to_geometry_sidecar(observation, registration))


class CanonicalTests(unittest.TestCase):
    def test_cross_runtime_canonical_digest(self):
        values = [{"t": 1800000000.0, "value": -0.0, "unicode": "café ☕"},
                  {"tiny": 1e-7, "edge": 1e-6, "large": 1e20, "larger": 1e21, "precision": 0.01234},
                  {"😀": 1, "\uffff": 2, "nested": [True, None, 2.0]}]
        script = "const fs=require('fs');const c=require('./app/src/home-lighting-target-contract.js');const x=JSON.parse(fs.readFileSync(0,'utf8'));process.stdout.write(JSON.stringify(x.map(v=>({canonical:c.canonicalJson(v),digest:c.digest(v)}))));"
        result = subprocess.run(["node", "-e", script], input=json.dumps(values, ensure_ascii=False), encoding="utf-8", capture_output=True, check=True, cwd=ROOT, timeout=10)
        expected = json.loads(result.stdout)
        for value, shared in zip(values, expected):
            self.assertEqual(canonical(value), shared["canonical"])
            self.assertEqual(digest(value), shared["digest"])


if __name__ == "__main__":
    unittest.main()
