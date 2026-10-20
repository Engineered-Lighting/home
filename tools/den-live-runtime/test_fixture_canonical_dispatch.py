"""Actual Journal, authority and pinned Python adapter/codec; authored raw I/O.

No native executable, radio, server, model or host calls. Only the coordinator
runs these tests. Native status, room resolution, clock and wire responses are
explicit authored doubles; none establishes physical movement or light output.
"""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import ModuleType, SimpleNamespace
from unittest import mock
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "stack/services/home-context"))

from fixture_canonical_dispatch import (CanonicalFixtureDispatcher, CanonicalSubmissionPort,
    KINDS, POST_FIELDS)
from fixture_execution_validation import FixtureAuthorityDenied, record_bytes
from test_fixture_canonical_authority import CanonicalAuthorityFixture, PINNED_BUILD


def pinned_adapter_modules(codec):
    """Load only manifest-hash-verified pure Python, with isolated import names."""
    manifest = json.loads((PINNED_BUILD / "manifest.json").read_bytes())
    modules = []
    for short in ("c12_runtime_wire", "c12_runtime_adapter"):
        relative = "tools/" + short + ".py"
        filename = Path(manifest["source"]["dir"]) / relative
        data = filename.read_bytes()
        if hashlib.sha256(data).hexdigest() != manifest["source"]["files"][relative]:
            raise ValueError("pinned pure adapter source changed")
        name = "canonical_dispatch_test_" + short
        module = ModuleType(name)
        module.__file__ = str(filename)
        sys.modules[name] = module  # dataclasses require its own module identity.
        imports = {"c12_runtime_plan": codec}
        if modules:
            imports["c12_runtime_wire"] = modules[0]
        with mock.patch.dict(sys.modules, imports):
            exec(compile(data, str(filename), "exec"), module.__dict__)
        modules.append(module)
    return modules


class DispatchFixture:
    def __init__(self, directory, *, call_timeout_ms=1000):
        self.f = CanonicalAuthorityFixture(directory)
        # Explicit authored fresh, same-context zero session, not STATUS resync.
        self.f.status.update(revision_id="0", state_generation="0")
        self.f.set_originals()
        self.ns = 1000
        self.call_timeout_ms = call_timeout_ms
        self.dispatcher = self.construct(create=True)
        self.f.authority.reservation_reader = self.dispatcher.row
        self.wire, self.adapter_module = pinned_adapter_modules(self.f.codec)
        self.effects, self.lose_commit, self.terminal = [], False, "ADMITTED"
        self.on_exchange = None
        self.port = self.connect()

    def construct(self, **kw):
        return CanonicalFixtureDispatcher(self.f.journal, authority=self.f.authority, codec=self.f.codec,
            clock_home_us=lambda: self.f.now, monotonic=lambda: self.ns,
            call_timeout_ms=self.call_timeout_ms, **kw)

    def connect(self):
        f, owner, m = self.f, self, self.adapter_module
        principal = m.Principal(3, ("fixture-A",), ("autonomous-ambient", "autonomous-task"), 255,
                                (3, 3, 3, 3, 3, 3, 3, 12))
        class Auth:
            def authenticate(self, credential):
                if credential != "authored-submit-only":
                    raise ValueError("wrong authored submit credential")
                return principal
        class Clock:
            def view(self):
                return m.ClockView(f.now, 100000, f.now, 0, 4000000, 1, True)
        class Lookup:
            def lookup(self, issuer_id, epoch, credential, grant, nonce):
                value = f.authority.lookup(issuer_id, epoch, credential, grant, nonce)
                return m.LookupReceipt(True, value["issuer_id"], value["issuer_epoch"], value["request_nonce"], value["record"])
        class Radio:
            evidence_source = "simulated"
            def exchange(self, packet):
                # These are authored encrypted wire responses, not actual
                # canonical engine acceptance/physics or GPIO operations.
                request = owner.wire.open_packet(bytes(range(32)), bytes(range(16)), 1,
                    bytes.fromhex(f.authority.context_digest), owner.wire.Direction.REQUEST, packet)
                row = owner.dispatcher._row_record(owner.active_request)
                if request.operation == owner.wire.Operation.PLAN_PREPARE:
                    assert row["state"] == "attempting" and row["gate_state"] == "preparing"
                elif request.operation == owner.wire.Operation.PLAN_COMMIT:
                    assert row["gate_state"] == "committing"
                    if owner.lose_commit:
                        owner.effects.append("COMMIT_RESPONSE_LOST")
                        raise OSError("authored lost commit response")
                if owner.on_exchange is not None:
                    owner.on_exchange(request.operation)
                owner.effects.append(request.operation.name)
                disposition = getattr(owner.wire.Code, owner.terminal) if request.operation == owner.wire.Operation.PLAN_COMMIT else owner.wire.Code.OK
                body = bytes((int(disposition), 0, 0))
                return owner.wire.seal_packet(bytes(range(32)), bytes(range(16)), 1,
                    bytes.fromhex(f.authority.context_digest), owner.wire.Direction.RESPONSE,
                    request.operation, request.session, request.sequence, body)
        nonce_counter = [0]
        def nonce():
            nonce_counter[0] += 1
            return hashlib.sha256(str(nonce_counter[0]).encode()).digest()
        policy = m.AdapterPolicy("fixture-A", bytes(range(16)), f.context, f.pins,
            tuple(m.IssuerPolicy(i.mode, i.issuer_id, int(i.issuer_epoch), i.lookup_credential)
                  for i in (f.policy.ambient_issuer, f.policy.task_issuer)))
        adapter = m.TrustedAdapter(policy=policy, key=bytes(range(32)), auth=Auth(), lookup=Lookup(), clock=Clock(),
            transport=Radio(), nonce_source=nonce, reservation_gate=self.dispatcher)
        port = CanonicalSubmissionPort(adapter, "authored-submit-only", deepcopy(f.provider), f.binding.binding_sha256)
        self.dispatcher.install_submission_port(port)
        return port

    def prepare(self, identity="ambient-1", mode="autonomous-ambient"):
        r = self.dispatcher.reserve(identity, hashlib.sha256(identity.encode()).hexdigest(), mode)
        p, _ = self.f.request(mode, identity=identity, grant=r["grant_id"], revision=r["revision_id"])
        method = self.f.authority.admit_ambient if mode == "autonomous-ambient" else self.f.authority.admit_task_joint
        admitted = method(identity, r, p)
        self.f.now += 100  # Actual first consumer receipt after authority ACK.
        self.dispatcher.prepare(identity, admitted)
        return admitted

    def dispatch(self, identity="ambient-1"):
        self.active_request = identity
        return self.dispatcher.dispatch(identity)

    def advance_native(self, revision):
        self.f.status.update(revision_id=str(revision), state_generation=str(int(self.f.status["state_generation"]) + 1))
        self.f.status["domains"][0]["generation"] += 1
        self.f.set_originals()


class CanonicalDispatchTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=ROOT / "artifacts")
        self.addCleanup(self.directory.cleanup)
        self.d = DispatchFixture(self.directory.name)
        self.addCleanup(self.d.f.journal.close)

    def bounded_fixture(self, *, call_timeout_ms=25):
        directory = tempfile.TemporaryDirectory(dir=ROOT / "artifacts")
        self.addCleanup(directory.cleanup)
        d = DispatchFixture(directory.name, call_timeout_ms=call_timeout_ms)
        self.addCleanup(d.f.journal.close)
        return d

    def hold_adapter(self, d):
        entered, release = threading.Event(), threading.Event()
        original = d.port.adapter.submit
        def blocked(*args):
            entered.set()
            if not release.wait(3):
                raise RuntimeError("authored barrier was not released")
            return original(*args)
        d.port.adapter.submit = blocked
        return entered, release

    def test_actual_pinned_adapter_calls_new_gate_and_original_receipt_is_retained(self):
        d = self.d
        admitted = d.prepare()
        result = d.dispatch()
        self.assertEqual(result["state"], "terminal")
        row = result["record"]
        self.assertEqual(row["adapter_disposition"], "ADMITTED")
        self.assertEqual(row["gate_state"], "finished")
        self.assertEqual(row["admitted"], admitted)
        self.assertGreater(int(row["first_consumer_received_home_us"]), int(admitted["grant_body"]["original_available_home_us"]))
        self.assertEqual(row["call_timing"]["elapsed_ns"], 0)
        self.assertEqual(row["call_timing"]["call_timeout_ms"], 1000)
        self.assertIn("PLAN_COMMIT", d.effects)
        records = d.dispatcher.export_archive()["records"]
        self.assertEqual([r["operation"] for r in records], ["reserve", "prepare", "attempt", "before_prepare", "before_commit", "adapter_finish", "outcome"])
        self.assertTrue(all(set(r["record"]) == POST_FIELDS for r in records))

    def test_exact_retries_never_allocate_retime_or_repeat_an_effect(self):
        d = self.d
        admitted = d.prepare()
        original_reservation = deepcopy(admitted["reservation"])
        terminal = d.dispatch()
        effects = list(d.effects)
        d.f.now = 3900000
        self.assertEqual(d.dispatcher.reserve("ambient-1", hashlib.sha256(b"ambient-1").hexdigest(), "autonomous-ambient"), original_reservation)
        self.assertEqual(d.dispatcher.prepare("ambient-1", admitted), terminal)
        self.assertEqual(d.dispatch(), terminal)
        self.assertEqual(d.effects, effects)
        with self.assertRaises(FixtureAuthorityDenied):
            d.dispatcher.reserve("ambient-1", "ff" * 32, "autonomous-ambient")

    def test_shared_modes_and_older_row_cannot_roll_back_allocator(self):
        d = self.d
        d.prepare()
        second = d.dispatcher.reserve("task-2", "bb" * 32, "autonomous-task")
        self.assertEqual(second["revision_id"], "2")
        d.dispatch()
        third = d.dispatcher.reserve("ambient-3", "cc" * 32, "autonomous-ambient")
        self.assertEqual((third["grant_id"], third["revision_id"]), ("3", "3"))
        self.assertEqual(d.dispatcher._meta()["last_revision"], "3")

    def test_ambient_then_task_uses_owned_actual_revision_and_advancing_domain_generations(self):
        d = self.d
        d.prepare(); d.dispatch(); d.advance_native(1)
        second = d.prepare("task-2", "autonomous-task")
        self.assertEqual(second["plan"]["predecessor_id"], "1")
        self.assertEqual(second["plan"]["domain_generations"][0], 1)
        result = d.dispatch("task-2")
        self.assertEqual(result["state"], "terminal")
        self.assertEqual(result["record"]["reservation"]["revision_id"], "2")

    def test_unowned_native_revision_is_not_adopted_from_status(self):
        d = self.d
        d.f.status["revision_id"] = "7"
        d.f.set_originals()
        r = d.dispatcher.reserve("foreign-native", "ee" * 32, "autonomous-ambient")
        # Counter is not seeded or bumped from STATUS. Source authority will
        # already refuse revision1<=native7, before any possible fake I/O.
        self.assertEqual(r["revision_id"], "1")
        p, _ = d.f.request(identity="foreign-native", grant="1", revision="1")
        with self.assertRaises(FixtureAuthorityDenied):
            d.f.authority.admit_ambient("foreign-native", r, p)
        self.assertFalse(d.effects)

    def test_lower_pending_cannot_be_bypassed_and_must_be_explicitly_abandoned(self):
        d = self.d
        d.dispatcher.reserve("unused-1", "11" * 32, "autonomous-task")
        d.prepare("ambient-2")
        result = d.dispatch("ambient-2")
        self.assertEqual(result["state"], "unknown")
        self.assertFalse(d.effects)
        self.assertTrue(result["namespace_blocked"])

    def test_abandoned_ids_are_not_reused_or_made_into_native_predecessors(self):
        d = self.d
        d.dispatcher.reserve("unused-1", "11" * 32, "autonomous-task")
        d.dispatcher.abandon("unused-1", reason="current-cut-replaced")
        admitted = d.prepare("ambient-2")
        self.assertEqual(admitted["plan"]["revision_id"], "2")
        self.assertEqual(admitted["plan"]["predecessor_id"], "0")
        self.assertEqual(d.dispatch("ambient-2")["state"], "terminal")

    def test_lost_commit_ack_is_sticky_unknown_no_retry_or_counter_reset(self):
        d = self.d
        d.prepare(); d.lose_commit = True
        result = d.dispatch()
        self.assertEqual(result["state"], "unknown")
        self.assertTrue(result["namespace_blocked"])
        effects = list(d.effects)
        self.assertEqual(d.dispatch(), result)
        self.assertEqual(d.effects, effects)
        with self.assertRaises(FixtureAuthorityDenied):
            d.dispatcher.reserve("task-2", "22" * 32, "autonomous-task")
        restarted = d.construct()
        self.assertTrue(restarted.status("ambient-1")["namespace_blocked"])

    def test_timeout_keeps_exact_monotonic_originals_and_unknown_effect(self):
        d = self.d
        d.prepare()
        original = d.port.adapter.submit
        def late(*args):
            result = original(*args)
            d.ns += 1000000001
            return result
        d.port.adapter.submit = late
        result = d.dispatch()
        self.assertEqual(result["state"], "unknown")
        timing = result["record"]["call_timing"]
        self.assertEqual(timing["elapsed_ns"], 1000000001)
        self.assertEqual(timing["completed_monotonic_ns"] - timing["call_started_monotonic_ns"], timing["elapsed_ns"])
        self.assertGreater(timing["elapsed_ns"], timing["call_timeout_ms"] * 1000000)

    def test_live_timeout_keeps_owner_until_exit_and_blocks_late_first_effect(self):
        d = self.bounded_fixture()
        d.prepare()
        entered, release = self.hold_adapter(d)
        try:
            result = d.dispatch()
            self.assertTrue(entered.is_set())
            self.assertEqual(result["state"], "unknown")
            self.assertTrue(result["record"]["call_timing"]["call_observation_timed_out"])
            self.assertIsNone(result["record"]["call_timing"]["completed_monotonic_ns"])
            self.assertIsNone(result["record"]["outcome"]["first_received_home_us"])
            self.assertTrue(d.dispatcher._handle["thread"].is_alive())
            self.assertFalse(d.dispatcher.drain()["safe_to_close_journal"])
        finally:
            release.set()
            if d.dispatcher._handle is not None:
                d.dispatcher._handle["thread"].join(2)
        self.assertFalse(d.dispatcher._handle["thread"].is_alive())
        self.assertFalse(d.effects)
        self.assertEqual(d.dispatcher.status("ambient-1"), result)
        settled = d.dispatcher.drain()
        self.assertEqual(settled["status"], "incomplete")
        self.assertTrue(settled["safe_to_close_journal"])
        self.assertEqual(settled["unconfirmed_operations"], 1)

    def test_commit_already_in_flight_cannot_promote_timeout_to_success(self):
        # Reach the intended COMMIT barrier after all actual PREPARE chunks;
        # the separate pre-effect cases retain their short 25 ms deadline.
        d = self.bounded_fixture(call_timeout_ms=1000)
        d.prepare()
        entered, release = threading.Event(), threading.Event()
        def blocked(operation):
            if operation == d.wire.Operation.PLAN_COMMIT:
                entered.set()
                if not release.wait(3):
                    raise RuntimeError("authored COMMIT barrier was not released")
        d.on_exchange = blocked
        try:
            result = d.dispatch()
            self.assertTrue(entered.is_set())
            self.assertEqual(result["state"], "unknown")
            self.assertEqual(result["record"]["gate_state"], "committing")
            self.assertFalse(d.dispatcher.drain()["safe_to_close_journal"])
        finally:
            release.set()
            if d.dispatcher._handle is not None:
                d.dispatcher._handle["thread"].join(2)
        self.assertIn("PLAN_COMMIT", d.effects)
        self.assertEqual(d.dispatcher.status("ambient-1"), result)
        self.assertEqual(d.dispatch()["state"], "unknown")

    def test_concurrent_second_dispatch_cannot_replace_owned_thread(self):
        d = self.d
        d.prepare()
        d.prepare("task-2", "autonomous-task")
        entered, release = self.hold_adapter(d)
        results = []
        caller = threading.Thread(target=lambda: results.append(d.dispatch()))
        try:
            caller.start()
            self.assertTrue(entered.wait(1))
            handle = d.dispatcher._handle
            second = d.dispatcher.dispatch("task-2")
            self.assertEqual(second["state"], "prepared")
            self.assertIs(d.dispatcher._handle, handle)
            self.assertTrue(d.dispatcher._in_call.is_set())
            self.assertFalse(any(r["operation"] == "attempt" and r["request_id"] == "task-2"
                                 for r in d.dispatcher.export_archive()["records"]))
        finally:
            release.set()
            caller.join(2)
            if d.dispatcher._handle is not None:
                d.dispatcher._handle["thread"].join(2)
        self.assertFalse(caller.is_alive())
        self.assertEqual(results[0]["state"], "terminal")
        self.assertEqual(d.dispatcher.status("task-2")["state"], "prepared")

    def test_quiesce_and_drain_report_live_owned_call_without_waiting_for_it(self):
        d = self.d
        d.prepare()
        entered, release = self.hold_adapter(d)
        results = []
        caller = threading.Thread(target=lambda: results.append(d.dispatch()))
        try:
            caller.start()
            self.assertTrue(entered.wait(1))
            stopped = d.dispatcher.quiesce()
            self.assertEqual(stopped["status"], "unconfirmed")
            self.assertTrue(stopped["in_flight"])
            draining = d.dispatcher.drain()
            self.assertEqual(draining["status"], "incomplete")
            self.assertFalse(draining["safe_to_close_journal"])
            self.assertTrue(d.dispatcher._meta()["quiesced"])
        finally:
            release.set()
            caller.join(2)
            if d.dispatcher._handle is not None:
                d.dispatcher._handle["thread"].join(2)
        self.assertFalse(caller.is_alive())
        self.assertFalse(d.effects)
        self.assertEqual(results[0]["state"], "unknown")

    def test_drain_before_thread_start_keeps_owned_attempt_and_prevents_late_invocation(self):
        d = self.d
        d.prepare()
        observed = []
        original_start = threading.Thread.start
        def before_start(thread):
            observed.append(d.dispatcher.drain())
            return original_start(thread)
        with mock.patch("fixture_canonical_dispatch.threading.Thread.start", before_start):
            result = d.dispatch()
        self.assertEqual(observed[0]["status"], "incomplete")
        self.assertEqual(observed[0]["active_calls"], 1)
        self.assertFalse(observed[0]["safe_to_close_journal"])
        self.assertEqual(result["state"], "refused")
        self.assertFalse(d.effects)

    def test_retention_blocked_at_timeout_stays_unconfirmed_until_first_outcome_ack(self):
        d = self.bounded_fixture()
        d.prepare()
        entered, release = threading.Event(), threading.Event()
        original = d.f.journal._recorder
        def blocked(record):
            original(record)
            if record["kind"] == "fixture_canonical_dispatch_commit" and record["data"]["operation"] == "before_prepare":
                entered.set()
                if not release.wait(3):
                    raise RuntimeError("authored recorder barrier was not released")
        d.f.journal._recorder = blocked
        try:
            result = d.dispatch()
            self.assertTrue(entered.is_set())
            self.assertEqual(result["state"], "unconfirmed")
            self.assertTrue(result["pending_retention"])
            self.assertFalse(d.dispatcher.drain()["safe_to_close_journal"])
        finally:
            release.set()
            if d.dispatcher._handle is not None:
                d.dispatcher._handle["thread"].join(2)
        self.assertFalse(d.effects)
        self.assertEqual(d.dispatcher.drain()["status"], "incomplete")
        final = d.dispatcher.status("ambient-1")
        self.assertEqual(final["state"], "unknown")
        self.assertTrue(final["record"]["outcome"]["original_receipt_time_unavailable"])

    def test_drained_requires_durable_quiesce_and_restart_stays_retired(self):
        d = self.d
        d.prepare(); d.dispatch()
        drained = d.dispatcher.drain()
        self.assertEqual(drained["status"], "drained")
        self.assertTrue(drained["safe_to_close_journal"])
        self.assertTrue(d.dispatcher._meta()["quiesced"])
        self.assertEqual(d.dispatcher.export_archive()["records"][-1]["operation"], "quiesce")
        reopened = d.construct()
        with self.assertRaises(FixtureAuthorityDenied):
            reopened.reserve("after-retirement", "22" * 32, "autonomous-task")

    def test_lost_quiesce_ack_cannot_report_healthy_drain_from_db_state_alone(self):
        d = self.d
        d.prepare(); d.dispatch()
        original = d.f.journal._recorder
        def lost(record):
            original(record)
            if record["kind"] == "fixture_canonical_dispatch_commit" and record["data"]["operation"] == "quiesce":
                raise OSError("authored lost quiesce ACK")
        d.f.journal._recorder = lost
        first = d.dispatcher.drain()
        self.assertEqual(first["status"], "incomplete")
        self.assertTrue(first["pending_retention"])
        self.assertFalse(first["safe_to_close_journal"])
        self.assertTrue(d.dispatcher._meta()["quiesced"])
        self.assertEqual(d.dispatcher.drain()["status"], "incomplete")

    def test_stop_during_attempt_retention_prevents_external_call_and_stays_quiesced(self):
        d = self.d
        d.prepare()
        original = d.f.journal._recorder
        def stop(record):
            original(record)
            if record["kind"] == "fixture_canonical_dispatch_commit" and record["data"]["operation"] == "attempt":
                d.dispatcher._quiesce_requested.set()
        d.f.journal._recorder = stop
        result = d.dispatch()
        self.assertEqual(result["state"], "refused")
        self.assertFalse(d.effects)
        self.assertTrue(d.dispatcher._meta()["quiesced"])

    def test_bad_gate_hash_and_direct_finish_without_attempt_refuse(self):
        d = self.d
        admitted = d.prepare()
        with self.assertRaises(FixtureAuthorityDenied):
            d.dispatcher.finish(admitted["plan"], "ADMITTED")
        with self.assertRaises(FixtureAuthorityDenied):
            d.dispatcher.check(admitted["plan"], "ff" * 32, admitted["grant_body_sha256"], "before_prepare")
        self.assertFalse(d.effects)

    def test_missing_and_nonzero_bootstrap_do_not_create_new_allocator_epoch(self):
        d = self.d
        d.f.status["revision_id"] = "5"; d.f.set_originals()
        d.f.journal.db.execute("DELETE FROM fixture_canonical_dispatch_meta")
        with self.assertRaises(FixtureAuthorityDenied):
            d.construct(create=False)
        with self.assertRaisesRegex(FixtureAuthorityDenied, "fresh zero"):
            d.construct(create=True)

    def test_recorder_commit_loss_latches_before_effect_and_survives_restart(self):
        d = self.d
        d.prepare()
        original = d.f.journal._recorder
        def lost(record):
            original(record)
            if record["kind"] == "fixture_canonical_dispatch_commit" and record["data"]["operation"] == "attempt":
                raise OSError("authored lost attempt archive ACK")
        d.f.journal._recorder = lost
        with self.assertRaises(OSError):
            d.dispatch()
        self.assertFalse(d.effects)
        self.assertTrue(d.f.journal._recorder_failed)
        d.f.journal._recorder_failed = False; d.f.journal._recorder = original
        reopened = d.construct()
        self.assertTrue(reopened.status("ambient-1")["namespace_blocked"])

    def test_post_state_hash_contract_and_no_credential_persistence(self):
        d = self.d
        d.prepare(); d.dispatch()
        prepares = {r["data"]["tx_id"]: r["data"] for r in d.f.records if r["kind"] == "fixture_canonical_dispatch_prepare"}
        commits = [r["data"] for r in d.f.records if r["kind"] == "fixture_canonical_dispatch_commit"]
        for commit in commits:
            p = prepares[commit["tx_id"]]
            self.assertEqual(commit["record_sha256"], hashlib.sha256(record_bytes(p["record"])).hexdigest())
            self.assertEqual(p["input_sha256"], hashlib.sha256(record_bytes({k: p[k] for k in ("operation", "request_id", "record")})).hexdigest())
        raw = json.dumps(d.dispatcher.export_archive(), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        self.assertNotIn(b"authored-submit-only", raw)
        self.assertNotIn(b"authored-ambient-lookup", raw)
        self.assertFalse(d.dispatcher.export_archive()["ordinary_replay_implemented"])


if __name__ == "__main__":
    unittest.main()
