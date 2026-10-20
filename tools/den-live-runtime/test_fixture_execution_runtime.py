"""Controlled runtime tests: real pinned PCB adapter/codec/ledger, fake I/O.

HOME_C12_SOURCE may point to a separately verified source-only packet. No source
is downloaded, installed, flashed or contacted by this suite.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import struct
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest

HERE = Path(__file__).resolve().parent
HOME = HERE.parents[1]
PCB_SOURCE = Path(os.environ.get("HOME_C12_SOURCE", r"C:\Users\Marcelo\Documents\Engineered Light Fixture\rt\runtime-host-444ac2d\source"))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HOME / "stack/services/home-context"))
sys.path.insert(0, str(PCB_SOURCE / "tools"))
import c12_runtime_adapter as pcb_adapter
import c12_runtime_ledger as pcb_ledger
import c12_runtime_plan as pcb_plan
import c12_runtime_wire as pcb_wire
from fixture_execution_validation import FixtureAuthorityPolicy, FixtureExecutionAuthority, canonical_bytes as grant_bytes
from fixture_client import FixtureClient
from fixture_dispatch import FixtureOutbox
from fixture_replay import replay_archive
from fixture_reports import FixtureRuntimeRefusal, canonical_bytes, parse_original, report_view, sha256


def native_event(scope, *, event="lighting_verified", result="ok", sampled="1000000"):
    return {"schema":"home-fixture-execution-report-1","version":1,"event":event,"result":result,"phase":"lit",
            "reported_device_us":"1000001","sampled_device_us":sampled,"received_device_us":sampled,
            "revision_id":"1","revision_sha256":"aa"*32,"session_context_sha256":scope["context_digest"],
            "motion_id":"10","state_generation":"1","effect_generation":"2","axis":255,"value":0,
            "frame":[0]*21+[4096,0,0],"evidence_source":"simulated","physical_pass":False}


class ControlledHarness:
    """Fake auth/clock/radio, actual Home issuer + actual PCB adapter/HA ledger."""
    def __init__(self, directory, *, journal=None, home_pins=None):
        self.directory = Path(directory)
        self.now = 1_100_000
        self.key = bytes(range(32))
        self.device = bytes(range(16))
        self.context = pcb_plan.example_context()
        self.context_digest = sha256(pcb_plan.encode_context(self.context))
        self.pins = {**self.context["pins"], "model": "0a"*32, "home_source": "0b"*32, "home_schema": "0c"*32}
        if home_pins is not None:
            self.pins.update(home_pins)
        self.scope = {"fixture_id": "fixture-A", "device_id": self.device.hex(), "boot_generation": "1", "session_id": "2",
                      "context_digest": self.context_digest, "ownership_generation": "4", "source_generation": "5",
                      "authority_generation": "6", "domain_generations": [0]*8, "pins": self.pins}
        self.records, self.packets, self.trace = [], [], []
        self.fail_commit = False
        self.fail_lookup = False
        self.state = {"source_generation": "5", "authority_generation": "6", "ownership_generation": "4",
                      "domain_generations": [0]*8, **{key: "4000000" for key in pcb_plan.CAP_FIELDS},
                      **{key: self.context[key] for key in pcb_plan.CAUSAL_NAMES}, "evidence_observed_home_us": "950000",
                      "decision_ref": "de"*32, "eligible": True}
        self.ledger = pcb_ledger.ExecutionLedger(self.directory / "ha.sqlite", pcb_ledger.Scope(
            self.device.hex(), "1", "2", self.context_digest, "0d"*32), create=True)
        self.owns_journal = journal is None
        db = sqlite3.connect(str(self.directory / "home.sqlite"), isolation_level=None) if self.owns_journal else None
        self.journal = journal or SimpleNamespace(db=db, lock=threading.RLock(), _recorder=self.records.append, _recorder_failed=False)
        self.authority = FixtureExecutionAuthority(self.journal, policy=FixtureAuthorityPolicy(
            "home-task", "7", "fixture-A", self.device.hex(), self.context, self.pins, "3", "lookup-only"),
            codec=pcb_plan, state_provider=lambda: copy.deepcopy(self.state), reservation_reader=self.ledger.row,
            command_validator=lambda plan, state: plan["target_centideg"] == [1000, -2000]
                and plan["kind"] in ("MOVE", "LEVELS", "CONTINUE") and state["eligible"] is True,
            clock=lambda: self.now, create=True)
        owner = self
        class Auth:
            def authenticate(self, credential):
                if credential != "submit-only":
                    raise pcb_adapter.AdapterDenied("scoped credential denied")
                return pcb_adapter.Principal(3, ("fixture-A",), ("autonomous-task",), 128, (0,)*7 + (12,), True, False)
        class Clock:
            def view(self):
                home_anchor = getattr(owner, "home_anchor_us", 1_000_000)
                device_anchor = getattr(owner, "device_anchor_us", 1_000_000)
                return pcb_adapter.ClockView(owner.now, device_anchor + owner.now - home_anchor,
                                             home_anchor, 0, home_anchor + 5_000_000, 1, True)
        class Lookup:
            def lookup(self, issuer_id, epoch, credential, grant_id, nonce):
                if owner.fail_lookup:
                    raise TimeoutError("fake configured lookup unavailable")
                result = owner.authority.lookup(issuer_id, epoch, credential, grant_id, nonce)
                return pcb_adapter.LookupReceipt(True, **result)
        class Radio:
            evidence_source = "simulated"
            def exchange(self, packet):
                decoded = pcb_wire.open_packet(owner.key, owner.device, 1, bytes.fromhex(owner.context_digest),
                                               pcb_wire.Direction.REQUEST, packet)
                owner.packets.append(decoded)
                if decoded.operation == pcb_wire.Operation.PLAN_PREPARE and hasattr(owner,"after_prepare"):
                    owner.after_prepare()
                body = bytes((int(pcb_wire.Code.OK), 0, 0))
                if decoded.operation == pcb_wire.Operation.PLAN_COMMIT:
                    if owner.fail_commit:
                        raise TimeoutError("fake lost commit acknowledgement")
                    body = bytes((int(pcb_wire.Code.ADMITTED), 0, 0))
                if decoded.operation == pcb_wire.Operation.STATUS:
                    snapshot, offset, count = struct.unpack("<IHB", decoded.body)
                    data = b"original-device-status"
                    part = data[offset:offset+count]
                    body = bytes((0,)) + struct.pack("<IQHHB", 9, 1_000_000, len(data), offset, len(part)) + part
                return pcb_wire.seal_packet(owner.key, owner.device, 1, bytes.fromhex(owner.context_digest),
                                            pcb_wire.Direction.RESPONSE, decoded.operation, decoded.session, decoded.sequence, body)
        self.service = pcb_adapter.TrustedAdapter(pcb_adapter.AdapterPolicy("fixture-A", self.device, self.context,
            self.pins, (pcb_adapter.IssuerPolicy("autonomous-task", "home-task", 7, "lookup-only"),)),
            self.key, Auth(), Lookup(), Clock(), Radio(), reservation_gate=self.ledger)
        class Facade:
            evidence_source = "simulated"
            scope = owner.scope
            def describe(self):
                return owner.service.describe()
            def reserve_execution_ids(self, credential, request):
                Auth().authenticate(credential)
                if request["scope"] != self.scope:
                    raise ValueError("fixed reservation scope mismatch")
                return owner.ledger.reserve(request["request_id"], request["intent_sha256"], request["mode"])
            def submit(self, credential, request):
                owner.trace.append("effect")
                if hasattr(owner, "before_effect"):
                    owner.before_effect()
                return owner.service.submit(credential, request)
            def withdraw(self, credential, request):
                return owner.service.withdraw(credential, request)
            def observe(self, credential):
                return owner.service.observe(credential)
        self.facade = Facade()
        self.client = FixtureClient(self.scope, transport=self.facade, credential="submit-only")

    def close(self):
        self.ledger.close()
        if self.owns_journal:
            self.journal.db.close()

    def admitted(self, request_id="task-one", **changes):
        reservation = self.client.reserve_execution_ids(request_id, sha256(request_id.encode()), "autonomous-task")
        plan = pcb_plan.example_plan()
        plan.update(grant_id=reservation["grant_id"], revision_id=reservation["revision_id"], grant_body_sha256="0"*64)
        plan.update(changes)
        admitted = self.authority.admit(request_id, reservation, plan)
        self.ledger.bind(plan["grant_id"], plan["revision_id"], admitted["grant_body"]["command_content_sha256"], admitted["grant_body_sha256"])
        return reservation, admitted

    def outbox(self, *, create=True, **options):
        return FixtureOutbox(self.directory / "outbox.sqlite", self.scope, create=create,
                            validate_admission=self.authority.validate_admission, encode_plan=pcb_plan.encode_plan,
                            encode_context=lambda: pcb_plan.encode_context(self.context), grant_canonical_bytes=grant_bytes,
                            clock_home_us=lambda: self.now, **options)


class FixtureExecutionRuntimeTests(unittest.TestCase):
    def setUp(self):
        scratch = HOME / "artifacts/den-hardware-runtime-tests"
        scratch.mkdir(parents=True, exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(prefix="home-fixture-controlled-", dir=scratch)
        self.addCleanup(self.directory.cleanup)
        self.h = ControlledHarness(self.directory.name)
        self.addCleanup(self.h.close)
        self.outbox = self.h.outbox()
        self.addCleanup(self.outbox.close)

    def prepare(self, request_id="task-one", **changes):
        reservation, admitted = self.h.admitted(request_id, **changes)
        self.outbox.prepare(request_id, reservation, admitted, available_home_us=str(self.h.now))
        return reservation, admitted

    def replay(self, archive=None, **options):
        return replay_archive(archive or self.outbox.export_archive(), encode_plan=pcb_plan.encode_plan,
                              encode_context=lambda: pcb_plan.encode_context(self.h.context), grant_canonical_bytes=grant_bytes, **options)

    def test_actual_issuer_ha_ledger_and_adapter_preserve_exact_wire_and_durable_before_effect(self):
        reservation, admitted = self.prepare()
        def before_effect():
            other = sqlite3.connect(str(self.outbox.path))
            try:
                self.assertEqual(other.execute("SELECT state FROM operations").fetchone()[0], "attempting")
                self.assertEqual(other.execute("SELECT kind FROM records ORDER BY sequence DESC").fetchone()[0], "attempt")
            finally:
                other.close()
        self.h.before_effect = before_effect
        result = self.outbox.dispatch("task-one", self.h.client, now_home_us=str(self.h.now))
        self.assertEqual(result["state"], "terminal", result)
        self.assertEqual(result["outcome"]["device_disposition"], "ADMITTED")
        self.assertEqual(result["outcome"]["wire_sha256"], admitted["wire_sha256"])
        self.assertEqual(len(self.h.packets), 17)
        self.assertEqual(self.h.packets[-1].body.hex(), admitted["wire_sha256"])
        self.assertEqual(self.h.ledger.row(reservation["grant_id"], reservation["revision_id"])["state"], "terminal")
        self.assertFalse(result["physical_permission"])

    def test_exact_retry_keeps_first_receipt_ids_and_no_extra_effect_after_expiry(self):
        reservation, admitted = self.prepare()
        first = self.outbox.dispatch("task-one", self.h.client, now_home_us=str(self.h.now))
        count = len(self.h.packets)
        self.h.now = 5_000_000
        self.assertEqual(self.outbox.prepare("task-one", reservation, admitted, available_home_us=str(self.h.now)), first)
        self.assertEqual(self.outbox.dispatch("task-one", self.h.client, now_home_us=str(self.h.now)), first)
        self.assertEqual(len(self.h.packets), count)
        original = self.outbox.export_archive()["records"][0]["payload"]["original"]
        self.assertEqual(original["original_available_home_us"], "1100000")

    def test_changed_same_identity_and_unadmitted_grant_refuse(self):
        reservation, admitted = self.prepare()
        forged = copy.deepcopy(admitted)
        forged["plan"]["transition_us"] = 1
        with self.assertRaisesRegex(ValueError, "changed_duplicate"):
            self.outbox.prepare("task-one", reservation, forged, available_home_us=str(self.h.now))
        reservation2, original = self.h.admitted("task-two")
        original["grant_body"]["authority_generation"] = "99"
        original["plan"]["grant_body_sha256"] = original["grant_body_sha256"] = sha256(grant_bytes(original["grant_body"]))
        original["wire_hex"] = pcb_plan.encode_plan(original["plan"]).hex()
        original["wire_sha256"] = sha256(bytes.fromhex(original["wire_hex"]))
        with self.assertRaisesRegex(ValueError, "grant_scope_mismatch"):
            self.outbox.prepare("task-two", reservation2, original, available_home_us=str(self.h.now))
        self.assertFalse(self.h.packets)

    def test_qualified_authority_changes_between_prepare_and_dispatch_refuse_without_effect(self):
        self.prepare()
        self.h.state["source_generation"] = "99"
        result = self.outbox.dispatch("task-one", self.h.client, now_home_us=str(self.h.now))
        self.assertEqual(result["state"], "refused")
        self.assertEqual(result["outcome"]["effect"], "not_attempted")
        self.assertFalse(self.h.packets)

    def test_expiry_equality_is_refused_before_external_call(self):
        self.prepare()
        self.h.now = 3_900_000
        result = self.outbox.dispatch("task-one", self.h.client, now_home_us=str(self.h.now))
        self.assertEqual(result["state"], "refused")
        self.assertFalse(self.h.packets)

    def test_lost_ack_is_unknown_and_never_blindly_resubmitted_after_restart(self):
        self.prepare()
        self.h.fail_commit = True
        result = self.outbox.dispatch("task-one", self.h.client, now_home_us=str(self.h.now))
        self.assertEqual(result["state"], "unknown")
        count = len(self.h.packets)
        self.assertEqual(self.outbox.dispatch("task-one", self.h.client, now_home_us=str(self.h.now)), result)
        self.outbox.close()
        self.outbox = self.h.outbox(create=False)
        self.addCleanup(self.outbox.close)
        self.assertEqual(self.outbox.dispatch("task-one", self.h.client, now_home_us=str(self.h.now))["state"], "unknown")
        self.assertEqual(len(self.h.packets), count)
        self.assertEqual(self.outbox.drain(available_home_us=str(self.h.now))["status"], "incomplete")
        self.assertEqual(self.replay()["status"], "incomplete")

    def test_restart_in_attempt_window_is_unknown_not_prepared(self):
        self.prepare()
        with self.outbox._lock, self.outbox._tx():
            self.outbox.db.execute("UPDATE operations SET state='attempting',reserved_records=1")
            row = self.outbox._get("task-one")
            self.outbox._append("attempt", "task-one", {"operation": "submit", "original_identity_sha256": row["identity_sha"]}, str(self.h.now))
        self.outbox.close()
        self.outbox = self.h.outbox(create=False)
        self.addCleanup(self.outbox.close)
        self.assertEqual(self.outbox.status("task-one")["state"], "unknown")
        self.assertFalse(self.h.packets)

    def test_wrong_boot_session_context_and_client_generation_refuse(self):
        self.prepare()
        for field, value in (("boot_generation", "2"), ("session_id", "3"), ("context_digest", "ab"*32), ("authority_generation", "7")):
            scope = copy.deepcopy(self.h.scope)
            scope[field] = value
            with self.assertRaisesRegex(ValueError, "outbox_scope_mismatch"):
                FixtureOutbox(self.outbox.path, scope)
            other = FixtureClient(scope)
            with self.assertRaisesRegex(ValueError, "client_scope_mismatch"):
                self.outbox.dispatch("task-one", other, now_home_us=str(self.h.now))
        self.assertFalse(self.h.packets)

    def test_private_original_reports_keep_first_receipt_and_never_claim_photometry(self):
        body = native_event(self.h.scope)
        raw = json.dumps(body,indent=1).encode()
        report = self.outbox.record_report("report-1", raw, first_received_home_us="1100000", category="led_registers", sampled_device_us="1000000")
        duplicate = self.outbox.record_report("report-1", raw, first_received_home_us="1200000", category="led_registers", sampled_device_us="1000000")
        self.assertEqual(report, duplicate)
        self.assertEqual(bytes.fromhex(report["original_hex"]), raw)
        view = report_view(report)
        self.assertFalse(view["measured_light"])
        self.assertFalse(view["physical_pose_confirmed"])
        body["frame"][21] = 1024
        with self.assertRaisesRegex(ValueError, "changed_duplicate_report"):
            self.outbox.record_report("report-1", canonical_bytes(body), first_received_home_us="1200000", category="led_registers")
        with self.assertRaisesRegex(ValueError, "duplicate_key"):
            self.outbox.record_report("report-2", b'{"x":1,"x":2}', first_received_home_us="1200000", category="axis_state")

    def test_drain_abandons_unsent_and_preserves_no_stop_claim(self):
        self.prepare()
        drained = self.outbox.drain(available_home_us=str(self.h.now))
        self.assertEqual(drained["status"], "drained")
        self.assertFalse(drained["physical_stop_confirmed"])
        self.assertEqual(self.outbox.status("task-one")["state"], "abandoned")
        self.assertEqual(self.outbox.dispatch("task-one", self.h.client, now_home_us=str(self.h.now))["state"], "abandoned")
        self.assertFalse(self.h.packets)

    def test_urgent_withdraw_remains_separate_from_unknown_submit(self):
        self.prepare()
        self.h.fail_commit = True
        self.outbox.dispatch("task-one", self.h.client, now_home_us=str(self.h.now))
        self.h.fail_commit = False
        self.outbox.prepare_withdraw("withdraw-one", domain_mask=128, available_home_us=str(self.h.now))
        result = self.outbox.dispatch("withdraw-one", self.h.client, now_home_us=str(self.h.now))
        self.assertEqual(result["state"], "terminal", result)
        self.assertEqual(self.h.packets[-1].operation, pcb_wire.Operation.WITHDRAW)
        self.assertEqual(self.outbox.status("task-one")["state"], "unknown")

    def test_replay_preserves_originals_and_requires_consumer_before_recompute_claim(self):
        self.prepare()
        self.outbox.dispatch("task-one", self.h.client, now_home_us=str(self.h.now))
        self.outbox.drain(available_home_us=str(self.h.now))
        result = self.replay()
        self.assertEqual(result["status"], "causal_archive_verified", result)
        self.assertFalse(result["firmware_recomputed"])
        self.assertFalse(result["acceptance_eligible"])
        # A literal independent wrong output is caught rather than accepted as
        # another byte-compatible hash chain. Actual C++ replay belongs to H1.
        wrong = self.replay(replay_consumer=lambda *_: {"forged": True})
        self.assertEqual(wrong["reason"], "canonical_consumer_output_mismatch")

    def test_rehashed_missing_originals_still_fail_completeness(self):
        self.prepare()
        self.outbox.dispatch("task-one", self.h.client, now_home_us=str(self.h.now))
        self.outbox.drain(available_home_us=str(self.h.now))
        archive = self.outbox.export_archive()
        archive["records"] = archive["records"][1:]
        previous = "0"*64
        for seq, row in enumerate(archive["records"], 1):
            row.update(sequence=seq, previous_sha256=previous)
            row["record_sha256"] = sha256(canonical_bytes({key: value for key, value in row.items() if key != "record_sha256"}))
            previous = row["record_sha256"]
        self.assertEqual(self.replay(archive)["reason"], "archive_attempt_without_original")

    def test_absent_transport_and_real_transport_marker_refuse(self):
        missing = FixtureClient(self.h.scope)
        self.assertFalse(missing.describe()["connected"])
        with self.assertRaisesRegex(ValueError, "unconfigured"):
            missing.observe()
        self.h.facade.evidence_source = "device-reported"
        with self.assertRaisesRegex(ValueError, "real_transport_unavailable"):
            self.h.client.observe()
        self.assertFalse(self.h.packets)

    def test_report_traffic_cannot_consume_reserved_attempt_and_outcome_capacity(self):
        self.outbox.close()
        small_path = Path(self.directory.name) / "small.sqlite"
        self.outbox = FixtureOutbox(small_path, self.h.scope, create=True, validate_admission=self.h.authority.validate_admission,
            encode_plan=pcb_plan.encode_plan, encode_context=lambda: pcb_plan.encode_context(self.h.context), grant_canonical_bytes=grant_bytes,
            clock_home_us=lambda: self.h.now, max_operations=1, max_records=3)
        self.addCleanup(self.outbox.close)
        self.prepare()
        with self.assertRaisesRegex(ValueError, "capacity_exhausted"):
            self.outbox.record_report("extra", canonical_bytes(native_event(self.h.scope,event="phase_changed",sampled=None)),
                                      first_received_home_us=str(self.h.now), category="unknown")
        self.assertEqual(self.outbox.dispatch("task-one", self.h.client, now_home_us=str(self.h.now))["state"], "terminal")
        self.assertEqual(self.outbox.drain(available_home_us=str(self.h.now))["status"], "drained")

    def test_status_snapshot_remains_original_and_is_not_a_dispatch_settlement(self):
        self.prepare()
        snapshot = self.h.client.observe()
        self.assertEqual(bytes.fromhex(snapshot["document_hex"]), b"original-device-status")
        self.assertEqual(snapshot["captured_device_us"], "1000000")
        self.assertEqual(self.outbox.status("task-one")["state"], "prepared")
        self.assertTrue(all(packet.operation == pcb_wire.Operation.STATUS for packet in self.h.packets))

    def test_dispatch_outcome_retains_actual_first_consumer_receipt_time(self):
        self.prepare()
        self.h.before_effect = lambda: setattr(self.h, "now", 1_125_000)
        result = self.outbox.dispatch("task-one", self.h.client, now_home_us="1100000")
        self.assertEqual(result["state"], "terminal", result)
        records = self.outbox.export_archive()["records"]
        self.assertEqual(records[0]["available_home_us"], "1100000")
        self.assertEqual(records[-1]["available_home_us"], "1125000")
        self.assertEqual(result["outcome"]["grant"]["original_available_home_us"], "1100000")

    def test_returned_after_deadline_stays_unknown_without_native_cancellation_claim(self):
        self.prepare()
        moments = iter((0, 2))
        client = FixtureClient(self.h.scope, transport=self.h.facade, credential="submit-only", monotonic=lambda: next(moments))
        result = self.outbox.dispatch("task-one", client, now_home_us=str(self.h.now))
        self.assertEqual(result["state"], "unknown")
        self.assertEqual(result["outcome"]["reason"], "adapter_call_deadline_unconfirmed")
        self.assertEqual(len(self.h.packets), 17)
        self.assertEqual(self.outbox.drain(available_home_us=str(self.h.now))["status"], "incomplete")

    def test_receipt_clock_rollback_preserves_unknown_effect_without_redating_grant(self):
        _, admitted = self.prepare()
        def rollback():
            original = self.h.now
            # Return a real simulated adapter disposition before the fake host
            # clock discontinuity is observed by the first consumer.
            self.h.now = original
        self.h.before_effect = rollback
        original_submit = self.h.client.submit
        def submit(plan):
            receipt = original_submit(plan)
            self.h.now = 900_000
            return receipt
        self.h.client.submit = submit
        result = self.outbox.dispatch("task-one", self.h.client, now_home_us="1100000")
        self.assertEqual(result["state"], "unknown")
        self.assertEqual(result["outcome"]["reason"], "receipt_clock_rollback_unconfirmed")
        self.assertEqual(result["outcome"]["original_adapter_receipt"]["wire_sha256"], admitted["wire_sha256"])
        self.assertEqual(self.outbox.export_archive()["records"][-1]["available_home_us"], "900000")

    def test_unknown_reconciliation_requires_independent_original_verifier_and_no_resend(self):
        _, admitted = self.prepare()
        self.h.fail_commit = True
        self.outbox.dispatch("task-one", self.h.client, now_home_us=str(self.h.now))
        packet_count = len(self.h.packets)
        receipt = {"device_disposition": "ADMITTED", "wire_sha256": admitted["wire_sha256"],
                   "semantic_revision_id": admitted["plan"]["revision_id"], "finalization_complete": True,
                   "physical_permission": False, "evidence_source": "simulated"}
        with self.assertRaisesRegex(ValueError, "verifier_unconfigured"):
            self.outbox.reconcile("task-one", receipt, available_home_us=str(self.h.now))
        # Explicit fake authenticated original-settlement channel, not STATUS
        # inference or a caller assertion exposed through the client.
        self.outbox._validate_reconciliation = lambda original, candidate, scope: candidate == receipt
        result = self.outbox.reconcile("task-one", receipt, available_home_us=str(self.h.now))
        self.assertEqual(result["state"], "terminal")
        self.assertEqual(len(self.h.packets), packet_count)
        self.outbox.drain(available_home_us=str(self.h.now))
        self.assertEqual(self.replay()["reason"], "archive_reconciliation_verifier_required")

    def test_native_report_wrong_context_admission_and_semantic_category_refuse(self):
        _, admitted = self.prepare()
        body = native_event(self.h.scope,event="axis_observed")
        body.update(axis=0,revision_sha256=admitted["wire_sha256"])
        self.outbox.record_report("valid-axis",canonical_bytes(body),first_received_home_us=str(self.h.now),admitted=admitted)
        wrong = copy.deepcopy(body)
        wrong["session_context_sha256"] = "bb"*32
        with self.assertRaisesRegex(ValueError,"context_mismatch"):
            self.outbox.record_report("wrong-context",canonical_bytes(wrong),first_received_home_us=str(self.h.now))
        wrong = copy.deepcopy(body)
        wrong["revision_sha256"] = "bb"*32
        with self.assertRaisesRegex(ValueError,"admission_mismatch"):
            self.outbox.record_report("wrong-wire",canonical_bytes(wrong),first_received_home_us=str(self.h.now),admitted=admitted)
        wrong = copy.deepcopy(body)
        wrong.update(event="refused",result="observation_rejected")
        with self.assertRaisesRegex(ValueError,"category_mismatch"):
            self.outbox.record_report("false-axis",canonical_bytes(wrong),first_received_home_us=str(self.h.now),category="axis_state")
        retained = self.outbox.record_report("refusal",canonical_bytes(wrong),first_received_home_us=str(self.h.now))
        self.assertEqual(retained["category"],"unknown")
        self.assertFalse(report_view(retained)["physical_pose_confirmed"])

    def test_native_report_null_sample_is_preserved_and_outer_retimestamp_refused(self):
        body = native_event(self.h.scope,event="phase_changed",sampled=None)
        retained = self.outbox.record_report("phase",canonical_bytes(body),first_received_home_us=str(self.h.now))
        self.assertIsNone(retained["sampled_device_us"])
        with self.assertRaisesRegex(ValueError,"sample_clock_mismatch"):
            self.outbox.record_report("invented-sample",canonical_bytes(body),first_received_home_us=str(self.h.now),sampled_device_us="1000001")
        body.update(event="axis_observed",axis=0)
        with self.assertRaisesRegex(ValueError,"axis_sample_missing"):
            self.outbox.record_report("missing-sample",canonical_bytes(body),first_received_home_us=str(self.h.now))

    def test_revocation_during_prepare_never_commits_and_recovers_only_original_disposition(self):
        reservation, admitted = self.prepare()
        def revoke_once():
            del self.h.after_prepare
            self.h.authority.revoke(reservation["grant_id"],reason="controlled-hazard-withdrawal")
        self.h.after_prepare = revoke_once
        result = self.outbox.dispatch("task-one",self.h.client,now_home_us=str(self.h.now))
        self.assertEqual(result["state"],"unknown")
        self.assertTrue(self.h.packets)
        self.assertFalse(any(packet.operation == pcb_wire.Operation.PLAN_COMMIT for packet in self.h.packets))
        original = copy.deepcopy(self.h.service._receipts["1"])
        self.assertEqual(original["device_disposition"],"NOT_COMMITTED")
        self.assertTrue(original["finalization_complete"])
        def verify(payload,receipt,scope):
            row = self.h.ledger.row(reservation["grant_id"],reservation["revision_id"])
            return receipt == original and row["state"] == "terminal" and row["disposition"] == "NOT_COMMITTED"
        self.outbox._validate_reconciliation = verify
        settled = self.outbox.reconcile("task-one",original,available_home_us=str(self.h.now))
        self.assertEqual(settled["state"],"terminal")
        self.assertEqual(settled["outcome"]["wire_sha256"],admitted["wire_sha256"])
        self.assertEqual(self.h.ledger.meta()["last_grant"],"1")

    def test_evidence_expiring_during_upload_cannot_commit_or_restart_relative_lease(self):
        self.prepare()
        def expire_once():
            del self.h.after_prepare
            self.h.now = 3_900_000
        self.h.after_prepare = expire_once
        result = self.outbox.dispatch("task-one",self.h.client,now_home_us="1100000")
        self.assertEqual(result["state"],"unknown")
        self.assertFalse(any(packet.operation == pcb_wire.Operation.PLAN_COMMIT for packet in self.h.packets))
        count = len(self.h.packets)
        self.assertEqual(self.outbox.dispatch("task-one",self.h.client,now_home_us=str(self.h.now))["state"],"unknown")
        self.assertEqual(len(self.h.packets),count)
        self.assertEqual(self.outbox.export_archive()["records"][-1]["available_home_us"],"3900000")

    def test_drain_during_pending_effect_remains_incomplete_until_original_response(self):
        _, admitted = self.prepare()
        entered, release = threading.Event(),threading.Event()
        drained = []
        owner = self
        class PendingClient:
            scope = owner.h.scope
            def submit(self,plan):
                entered.set()
                if not release.wait(2):
                    raise TimeoutError("controlled pending effect")
                return {"device_disposition":"ADMITTED","wire_sha256":admitted["wire_sha256"],"semantic_revision_id":plan["revision_id"],
                        "finalization_complete":True,"physical_permission":False,"evidence_source":"simulated"}
        def stop():
            if entered.wait(2):
                drained.append(self.outbox.drain(available_home_us=str(self.h.now)))
            release.set()
        thread = threading.Thread(target=stop)
        thread.start()
        result = self.outbox.dispatch("task-one",PendingClient(),now_home_us=str(self.h.now))
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(drained[0]["status"],"incomplete")
        self.assertFalse(drained[0]["physical_stop_confirmed"])
        self.assertEqual(result["state"],"terminal")
        self.assertEqual(self.outbox.drain(available_home_us=str(self.h.now))["status"],"drained")

    def test_missing_durable_outbox_is_not_reconstructed_from_counter_or_device_status(self):
        absent = Path(self.directory.name)/"missing.sqlite"
        with self.assertRaisesRegex(ValueError,"missing_no_automatic_reset"):
            FixtureOutbox(absent,self.h.scope)
        self.assertFalse(absent.exists())


if __name__ == "__main__":
    unittest.main()
