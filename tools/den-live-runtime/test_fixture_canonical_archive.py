"""Actual Journal/issuer and pinned pure codec; authored dispatcher snapshots.

These tests execute no native process, adapter, hardware, model or network. The
independent dispatcher transition oracle is not a connected command proof.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "stack/services/home-context"))
from archive import Recorder, ArchiveRefusal
from journal import canonical, digest
from fixture_execution_validation import record_bytes, canonical_bytes, PROJECTION
from test_fixture_canonical_authority import CanonicalAuthorityFixture
from fixture_canonical_archive import (CanonicalFixtureRecorder, canonical_recorders,
    replay_canonical_fixture_archive, KINDS)


def hashed(value):
    return hashlib.sha256(record_bytes(value)).hexdigest()


def archive_binding(f, dispatch_policy):
    row = dict(namespace=f.authority.namespace, policy_sha256=f.authority.policy_digest,
        fixture_id=f.policy.fixture_id, device_id=f.policy.device_id, principal_id=f.policy.principal_id,
        native_binding_sha256=f.binding.binding_sha256, context=deepcopy(f.context),
        context_hex=f.codec.encode_context(f.context).hex(), pins=deepcopy(f.pins),
        home_scope=deepcopy(f.home_scope), room_binding=deepcopy(f.room_binding),
        issuers=[dict(mode=i.mode, issuer_id=i.issuer_id, issuer_epoch=i.issuer_epoch)
                 for i in (f.policy.ambient_issuer, f.policy.task_issuer)],
        dispatch_policy=deepcopy(dispatch_policy), dispatch_policy_sha256=hashed(dispatch_policy))
    return dict(schema="home-fixture-canonical-archive-binding-1", version=1,
        parent_manifest_sha256="12" * 32, source_pins={"tools/den-live-runtime/fixture_canonical_archive.py": "34" * 32},
        provider_pins=deepcopy(f.provider), authorities=[row])


class CanonicalArchiveFixture:
    """The dispatcher rows are explicit authored originals, never native truth."""
    def __init__(self, owner):
        self.tmp = tempfile.TemporaryDirectory(); owner.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.f = CanonicalAuthorityFixture(self.directory)
        owner.addCleanup(self.f.journal.close)
        self.f.status["revision_id"] = "0"; self.f.set_originals()
        f = self.f
        policy = dict(schema="home-fixture-canonical-dispatch-policy-1", version=1,
            authority_policy_sha256=f.authority.policy_digest, namespace=f.authority.namespace,
            max_operations=4096, call_timeout_ms=1000)
        self.binding = archive_binding(f, policy)
        self.path = self.directory / "canonical.jsonl"
        self.recorder = CanonicalFixtureRecorder(self.path, binding=self.binding)
        owner.addCleanup(self.recorder.drain, {"status": "incomplete"})
        f.journal._recorder = canonical_recorders(f.record, self.recorder)
        self.ns = f.authority.namespace; self.ordinal = 0
        self.allocator = {"last_grant_id": "1", "last_revision_id": "1", "dispatched_grant_id": "0"}
        self.plan, self.reservation = f.request(identity="request-1", grant="1", revision="1")
        self.row = dict(reservation=deepcopy(self.reservation), admitted=None,
            first_consumer_received_home_us=None, attempt_started_home_us=None,
            gate_state="unclaimed", adapter_disposition=None, state="reserved", outcome=None,
            allocator=deepcopy(self.allocator), quiesced=False, blocked=False, call_timing=None)

    def transition(self, operation, row=None, request="request-1"):
        row = deepcopy(self.row if row is None else row)
        self.ordinal += 1; tx = "authored-tx-" + str(self.ordinal)
        data = dict(tx_id=tx, namespace=self.ns, operation=operation, request_id=request,
            available_home_us=str(self.f.now), input_sha256=hashed({"operation": operation, "request_id": request, "record": row}),
            record=row)
        self.recorder.record({"kind": "fixture_canonical_dispatch_prepare", "data": data})
        self.recorder.record({"kind": "fixture_canonical_dispatch_commit", "data": {
            "tx_id": tx, "namespace": self.ns, "operation": operation, "request_id": request, "record_sha256": hashed(row)}})
        if request is not None:
            self.row = row

    def admit(self):
        self.transition("reserve")
        admitted = self.f.admit(identity="request-1", plan=self.plan, reservation=self.reservation)
        self.row.update(admitted=deepcopy(admitted), state="prepared",
                        first_consumer_received_home_us=str(self.f.now))
        self.row["reservation"].update(state="bound", content_sha=admitted["grant_body"]["command_content_sha256"],
                                       grant_sha=admitted["grant_body_sha256"])
        self.transition("prepare")
        return admitted

    def complete(self):
        admitted = self.admit()
        self.attempt()
        self.row["gate_state"] = "preparing"; self.row["reservation"]["state"] = "dispatching"
        self.row["allocator"]["dispatched_grant_id"] = "1"; self.transition("before_prepare")
        self.row["gate_state"] = "committing"; self.transition("before_commit")
        self.row.update(gate_state="finished", adapter_disposition="ADMITTED")
        self.row["reservation"].update(state="terminal", disposition="ADMITTED"); self.transition("adapter_finish")
        adapter = dict(schema="c12-runtime-adapter-receipt-draft-1", physical_permission=False, evidence_source="simulated",
            finalization_complete=True, wire_sha256=admitted["wire_sha256"], semantic_revision_id="1", device_disposition="ADMITTED")
        self.row["call_timing"].update(call_started_monotonic_ns=11000, completed_monotonic_ns=13000, elapsed_ns=2000,
                                        observation_ended_monotonic_ns=14000)
        self.row.update(state="terminal", outcome={"first_received_home_us": str(self.f.now), "observed_home_us": str(self.f.now),
            "original_receipt_time_unavailable": False, "adapter": adapter}); self.transition("outcome")
        self.quiesce()
        self.recorder.drain({"status": "complete"})
        return admitted

    def attempt(self):
        self.row.update(state="attempting", attempt_started_home_us=str(self.f.now), call_timing={
            "call_timeout_ms": 1000, "attempt_started_monotonic_ns": 10000,
            "call_started_monotonic_ns": None, "completed_monotonic_ns": None, "elapsed_ns": None,
            "observation_ended_monotonic_ns": None, "call_observation_timed_out": False})
        self.transition("attempt")

    def quiesce(self):
        row = dict(reservation=None, admitted=None, first_consumer_received_home_us=None,
            attempt_started_home_us=None, gate_state="unclaimed", adapter_disposition=None,
            state="namespace", outcome=None, allocator=deepcopy(self.row["allocator"]), quiesced=True, blocked=False,
            call_timing=None)
        self.transition("quiesce", row, request=None)

    def replay(self, **kwargs):
        return replay_canonical_fixture_archive(self.path,
            expected_binding=kwargs.get("binding", self.binding), codec=self.f.codec)

    def rewrite(self, mutate):
        entries = [json.loads(line) for line in self.path.read_bytes().splitlines()]
        mutate(entries)
        previous = None; output = []
        for index, entry in enumerate(entries):
            entry.update(seq=index, previous_digest=previous)
            entry["digest"] = digest({k: v for k, v in entry.items() if k != "digest"})
            previous = entry["digest"]; output.append(canonical(entry).encode() + b"\n")
        self.path.write_bytes(b"".join(output))


class CanonicalArchiveTests(unittest.TestCase):
    def setUp(self):
        self.x = CanonicalArchiveFixture(self)

    def test_actual_journal_grant_and_authored_dispatch_causal_replay(self):
        x = self.x; admitted = x.complete(); result = x.replay()
        self.assertEqual(result["status"], "causal_archive_verified", result)
        self.assertEqual(result["grants"], 1); self.assertEqual(result["dispatcher_transitions"], 8)
        self.assertEqual(x.f.journal.db.execute("SELECT COUNT(*) FROM fixture_canonical_grants").fetchone()[0], 1)
        for key in ("current", "physical_verified", "firmware_recomputed", "worker_recomputed", "room_component_recomputed",
                    "authority_admission_recomputed", "clock_qualification_recomputed", "native_originals_decoded"):
            self.assertFalse(result[key])
        self.assertEqual(admitted["plan"]["frame"][21:], [0, 0, 0])

    def test_actual_dispatcher_pinned_adapter_and_journal_match_causal_reader(self):
        from test_fixture_canonical_dispatch import DispatchFixture
        with tempfile.TemporaryDirectory() as directory:
            d = DispatchFixture(directory)
            try:
                binding = archive_binding(d.f, d.dispatcher.policy)
                path = Path(directory) / "actual-dispatch-sidecar.jsonl"
                recorder = CanonicalFixtureRecorder(path, binding=binding)
                self.addCleanup(recorder.drain, {"status": "incomplete"})
                d.f.journal._recorder = canonical_recorders(d.f.record, recorder)
                self.assertFalse(d.dispatcher.export_archive()["records"])
                original = d.prepare()
                result = d.dispatch()
                self.assertEqual(result["state"], "terminal", result)
                self.assertEqual(result["record"]["adapter_disposition"], "ADMITTED")
                self.assertEqual(result["record"]["admitted"], original)
                self.assertIn("PLAN_COMMIT", d.effects)
                drain = d.dispatcher.drain()
                self.assertEqual(drain["status"], "drained", drain)
                self.assertTrue(drain["safe_to_close_journal"])
                self.assertEqual(recorder.drain({"status": "complete"})["status"], "drained")
                replay = replay_canonical_fixture_archive(path, expected_binding=binding, codec=d.f.codec)
                self.assertEqual(replay["status"], "causal_archive_verified", replay)
                self.assertEqual(replay["grants"], 1)
                self.assertEqual(replay["dispatcher_transitions"], 8)
                self.assertEqual((replay["configured_fixtures"], replay["observed_dispatcher_namespaces"]), (1, 1))
                self.assertFalse(replay["firmware_recomputed"]); self.assertFalse(replay["worker_recomputed"])
                self.assertFalse(replay["authority_admission_recomputed"])
            finally:
                d.f.journal.close()

    def test_fixed_router_preserves_worker_and_refuses_old_or_unknown_kinds(self):
        x = self.x; route = canonical_recorders(x.f.record, x.recorder)
        body = {"kind": "worker_tick", "data": {"t": 1}}
        route(body); self.assertEqual(x.f.records[-1], body)
        terminal = {"kind": "session_end", "data": {"status": "complete"}}
        route(terminal); self.assertEqual(x.f.records[-1], terminal)
        self.assertFalse(x.recorder.ended)
        for kind in ("fixture_grant_prepare", "caller_selected_route"):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                route({"kind": kind, "data": {}})
        old = Recorder(x.directory / "old.jsonl", {"schema": "home-live-session-manifest-1", "version": 1},
                       max_records=10, max_bytes=100000, min_free_bytes=1)
        self.addCleanup(old.drain, {"status": "incomplete"})
        for kind in KINDS:
            with self.subTest(kind=kind), self.assertRaises(ArchiveRefusal):
                old.record({"kind": kind, "data": {}})

    def test_foreign_parent_provider_context_and_inventory_refuse(self):
        x = self.x; x.complete()
        for field in ("parent_manifest_sha256", "provider_pins", "authorities"):
            binding = deepcopy(x.binding)
            if field == "parent_manifest_sha256": binding[field] = "ff" * 32
            elif field == "provider_pins": binding[field]["source_sha256"] = "ff" * 32
            else: binding[field][0]["context"]["session_id"] = "99"
            with self.subTest(field=field): self.assertEqual(x.replay(binding=binding)["status"], "incomplete")

    def test_missing_grant_original_refuses_even_reclosed_chain(self):
        x = self.x; x.complete()
        x.rewrite(lambda rows: rows.__setitem__(slice(None), [e for e in rows if e["record"]["kind"] != "fixture_canonical_grant_prepare"]))
        result = x.replay(); self.assertEqual(result["status"], "incomplete")
        self.assertIn("original_missing", result["reason"])

    def test_rehashed_changed_wire_and_original_native_bytes_refuse(self):
        x = self.x; x.complete()
        def change(rows):
            p = next(e["record"]["data"] for e in rows if e["record"]["kind"] == "fixture_canonical_grant_prepare")
            p["receipt"]["originals"]["status3_hex"] = "00" * 998
        x.rewrite(change)
        self.assertEqual(x.replay()["status"], "incomplete")

    def test_missing_second_gate_cannot_claim_native_success(self):
        x = self.x; x.complete()
        def remove(rows):
            rows[:] = [e for e in rows if e["record"]["data"].get("operation") != "before_commit"]
        x.rewrite(remove)
        result = x.replay(); self.assertEqual(result["status"], "incomplete")
        self.assertIn("native_outcome_without_commit", result["reason"])

    def test_reclosed_dispatch_input_and_commit_hash_do_not_hide_expired_attempt(self):
        x = self.x; x.complete()
        def expire(rows):
            for e in rows:
                d = e["record"]["data"]
                if e["record"]["kind"] == "fixture_canonical_dispatch_prepare" and d["operation"] == "attempt":
                    d["available_home_us"] = "3900000"
        x.rewrite(expire)
        result = x.replay(); self.assertEqual(result["status"], "incomplete")
        self.assertIn("support_expired", result["reason"])

    def test_lost_commit_and_partial_tail_refuse_complete_marker(self):
        x = self.x; x.complete()
        original = x.path.read_bytes()
        x.path.write_bytes(original[:-1]); self.assertEqual(x.replay()["status"], "incomplete")
        x.path.write_bytes(original)
        x.rewrite(lambda rows: rows.__setitem__(slice(None), [e for e in rows if not (
            e["record"]["kind"] == "fixture_canonical_dispatch_commit" and e["record"]["data"]["operation"] == "outcome")]))
        self.assertEqual(x.replay()["status"], "incomplete")

    def test_unknown_restart_effect_never_becomes_complete(self):
        x = self.x; x.admit()
        x.attempt()
        x.row.update(state="unknown", blocked=True,
            outcome={"effect": "unconfirmed", "reason": "restart_after_attempt_no_resubmit"})
        x.transition("recovery_unknown")
        x.recorder.drain({"status": "complete"})
        result = x.replay(); self.assertEqual(result["status"], "incomplete")
        self.assertIn("quiescent", result["reason"])

    def test_reclosed_monotonic_duration_cannot_claim_terminal_success_after_deadline(self):
        x = self.x; x.complete()
        def late(rows):
            for entry in rows:
                data = entry["record"]["data"]
                if entry["record"]["kind"] == "fixture_canonical_dispatch_prepare" and data["operation"] == "outcome":
                    data["record"]["call_timing"].update(completed_monotonic_ns=1000011001, elapsed_ns=1000000001,
                                                          observation_ended_monotonic_ns=1000011001)
                    data["input_sha256"] = hashed({"operation": data["operation"], "request_id": data["request_id"], "record": data["record"]})
                    committed = next(other["record"]["data"] for other in rows if
                        other["record"]["kind"] == "fixture_canonical_dispatch_commit" and other["record"]["data"]["tx_id"] == data["tx_id"])
                    committed["record_sha256"] = hashed(data["record"])
        x.rewrite(late)
        result = x.replay(); self.assertEqual(result["status"], "incomplete")
        self.assertIn("adapter_original_mismatch", result["reason"])

    def test_exact_original_retries_do_not_append_or_retime_authority(self):
        x = self.x; x.transition("reserve")
        first = x.f.admit(plan=x.plan, reservation=x.reservation)
        count = x.recorder.seq; x.f.now += 100
        second = x.f.admit(plan=x.plan, reservation=x.reservation)
        self.assertEqual(first, second); self.assertEqual(x.recorder.seq, count)

    def test_unquiesced_prepared_dispatch_is_not_a_complete_session(self):
        x = self.x; x.admit(); x.recorder.drain({"status": "complete"})
        self.assertEqual(x.replay()["status"], "incomplete")

    def test_duplicate_inventory_and_caller_alias_refuse(self):
        x = self.x; binding = deepcopy(x.binding); binding["authorities"] *= 2
        with self.assertRaisesRegex(ValueError, "duplicate_authority"):
            CanonicalFixtureRecorder(x.directory / "bad.jsonl", binding=binding)
        with self.assertRaises(ValueError):
            x.recorder.record({"kind": "fixture_canonical_dispatch_commit", "data": {}}, record_id="alias")

    def test_reclosed_manual_preset_and_task_field_scope_cannot_impersonate_authority(self):
        x = self.x; x.complete(); original = x.path.read_bytes()
        attacks = (({"manual": True}, "canonical_archive_manual_or_preset_scope"),
                   ({"preset_id": "1"}, "canonical_archive_manual_or_preset_scope"),
                   ({"manual_duration_minutes": 1}, "canonical_archive_manual_or_preset_scope"),
                   ({"kind": "MOVE", "domain_mask": 128, "field_mask": [0] * 7 + [8]}, "canonical_archive_task_move_requires_aim"),
                   ({"kind": "LEVELS", "domain_mask": 128, "field_mask": [0] * 7 + [4]}, "canonical_archive_task_levels_cannot_change_aim"))
        for change, refusal in attacks:
            with self.subTest(change=change):
                x.path.write_bytes(original)
                def forge(rows):
                    authority_prepare = next(e["record"]["data"] for e in rows
                        if e["record"]["kind"] == "fixture_canonical_grant_prepare")
                    receipt = authority_prepare["receipt"]
                    plan, grant, reservation = receipt["plan"], receipt["grant_body"], receipt["reservation"]
                    plan.update(deepcopy(change))
                    if "field_mask" in change:
                        issuer = x.binding["authorities"][0]["issuers"][1]
                        grant.update(deepcopy(issuer)); reservation["mode"] = "autonomous-task"
                    for key in PROJECTION:
                        grant[key] = deepcopy(plan[key])
                    unsigned = deepcopy(plan); unsigned["grant_body_sha256"] = "0" * 64
                    content = b"C12-CONTENT-V1" + x.f.codec.encode_plan(unsigned)[:-40]
                    grant.update(command_content_hex=content.hex(), command_content_sha256=hashlib.sha256(content).hexdigest())
                    grant_sha = hashlib.sha256(canonical_bytes(grant)).hexdigest()
                    plan["grant_body_sha256"] = grant_sha
                    wire = x.f.codec.encode_plan(plan)
                    receipt.update(grant_body_sha256=grant_sha, wire_hex=wire.hex(), wire_sha256=hashlib.sha256(wire).hexdigest())
                    authority_prepare["input_sha256"] = hashed({"mode": grant["mode"], "plan": unsigned, "reservation": reservation})
                    for entry in rows:
                        kind, data = entry["record"]["kind"], entry["record"]["data"]
                        if kind == "fixture_canonical_grant_commit":
                            data["grant_body_sha256"] = grant_sha
                        elif kind == "fixture_canonical_dispatch_prepare":
                            post = data["record"]
                            if post["reservation"] is not None:
                                post["reservation"]["mode"] = reservation["mode"]
                                if post["reservation"]["content_sha"] is not None:
                                    post["reservation"]["content_sha"] = grant["command_content_sha256"]
                                    post["reservation"]["grant_sha"] = grant_sha
                            if post["admitted"] is not None:
                                post["admitted"] = deepcopy(receipt)
                            if post["outcome"] is not None and "adapter" in post["outcome"]:
                                post["outcome"]["adapter"]["wire_sha256"] = receipt["wire_sha256"]
                            data["input_sha256"] = hashed({"operation": data["operation"], "request_id": data["request_id"], "record": post})
                            committed = next(e["record"]["data"] for e in rows if
                                e["record"]["kind"] == "fixture_canonical_dispatch_commit" and e["record"]["data"]["tx_id"] == data["tx_id"])
                            committed["record_sha256"] = hashed(post)
                    self.assertEqual(hashlib.sha256(canonical_bytes(grant)).hexdigest(), receipt["grant_body_sha256"])
                    self.assertEqual(x.f.codec.encode_plan(plan).hex(), receipt["wire_hex"])
                x.rewrite(forge)
                result = x.replay()
                self.assertEqual(result["status"], "incomplete", result)
                self.assertEqual(result["reason"], refusal, result)


if __name__ == "__main__":
    unittest.main()
