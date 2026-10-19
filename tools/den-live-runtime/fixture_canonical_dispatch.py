"""Same-Journal automatic fixture IDs, durable dispatch and pinned adapter gate.

This is a bounded command owner, not a scheduler, encoder or device transport.
The sole trusted composition supplies canonical authority/codec and installs an
actual pinned TrustedAdapter once. The old outbox/ledger remain unchanged.
"""
from __future__ import annotations
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json
import threading
import time
import uuid

from fixture_canonical_authority import CanonicalFixtureAuthority, MODES
from fixture_execution_validation import (CAP_FIELDS, MAX_U64, FixtureAuthorityDenied,
    canonical_bytes, record_bytes, require, sha256, u64)

KINDS = frozenset({"fixture_canonical_dispatch_prepare", "fixture_canonical_dispatch_commit"})
OPERATIONS = frozenset({"reserve", "prepare", "attempt", "before_prepare", "before_commit", "adapter_finish",
    "outcome", "refuse", "abandon", "quiesce", "recovery_unknown"})
DISPOSITIONS = frozenset({"NOT_COMMITTED", "UNCERTAIN", "ADMITTED", "DUPLICATE", "REJECTED", "EXPIRED",
    "CONTEXT_MISMATCH", "NO_SESSION", "CHANGED_DUPLICATE", "REPLAY", "UNAUTHORIZED", "NO_KEY", "MALFORMED",
    "BUSY", "INCOMPLETE", "CONFLICT", "OUT_OF_RANGE", "OK"})
POST_FIELDS = frozenset({"reservation", "admitted", "first_consumer_received_home_us", "attempt_started_home_us",
    "gate_state", "adapter_disposition", "state", "outcome", "allocator", "quiesced", "blocked", "call_timing"})


@dataclass(frozen=True)
class CanonicalSubmissionPort:
    adapter: object
    credential: object = field(repr=False)
    provider_pins: dict = field(default_factory=dict)
    binding_sha256: str = ""
    evidence_source: str = "simulated"
    physical_permission: bool = False


def _hash(value):
    return hashlib.sha256(record_bytes(value)).hexdigest()


def _id(value):
    require(type(value) is str and 0 < len(value) <= 128 and value.isascii()
            and all(33 <= ord(c) <= 126 for c in value), "bounded immutable request identity required")


class CanonicalFixtureDispatcher:
    def __init__(self, journal, *, authority, codec, submission_port=None, clock_home_us,
                 monotonic=time.monotonic_ns, create=False, max_operations=4096, call_timeout_ms=1000):
        require(type(authority) is CanonicalFixtureAuthority and authority.journal is journal,
                "same factory Journal canonical authority required")
        require(codec is authority.codec and callable(clock_home_us) and callable(monotonic),
                "fixed canonical codec/qualified clock required")
        require(type(max_operations) is int and 1 <= max_operations <= 65536
                and type(call_timeout_ms) is int and 1 <= call_timeout_ms <= 5000, "bounded dispatch policy")
        self.journal, self.authority, self.codec = journal, authority, codec
        self.namespace = authority.namespace
        self.clock, self.monotonic = clock_home_us, monotonic
        self.max_operations, self.call_timeout_ms = max_operations, call_timeout_ms
        self.port = None
        self.failed = False
        self._quiesce_requested = threading.Event()
        self._in_call = threading.Event()
        self._timeout_fence = threading.Event()
        self._handle = None
        self._pending_timeout = None
        policy = {"schema": "home-fixture-canonical-dispatch-policy-1", "version": 1,
            "authority_policy_sha256": authority.policy_digest, "namespace": self.namespace,
            "max_operations": max_operations, "call_timeout_ms": call_timeout_ms}
        self.policy_digest = _hash(policy)
        self.policy = deepcopy(policy)
        with journal.lock:
            exists = journal.db.execute("SELECT name FROM sqlite_master WHERE name='fixture_canonical_dispatch_meta'").fetchone()
            require(exists is not None or create is True, "dispatch ledger missing; no automatic reset")
            if exists is None:
                journal.db.execute("CREATE TABLE fixture_canonical_dispatch_meta(namespace TEXT PRIMARY KEY, policy TEXT NOT NULL, last_grant TEXT NOT NULL,last_revision TEXT NOT NULL,dispatched_grant TEXT NOT NULL,last_clock TEXT NOT NULL,blocked INTEGER NOT NULL,quiesced INTEGER NOT NULL)")
                journal.db.execute("CREATE TABLE fixture_canonical_dispatch_ops(namespace TEXT NOT NULL,request_id TEXT NOT NULL,grant_id TEXT NOT NULL,revision_id TEXT NOT NULL,initial_reservation TEXT NOT NULL,record TEXT NOT NULL,PRIMARY KEY(namespace,request_id),UNIQUE(namespace,grant_id),UNIQUE(namespace,revision_id))")
                journal.db.execute("CREATE TABLE fixture_canonical_dispatch_records(namespace TEXT NOT NULL,seq INTEGER NOT NULL,body TEXT NOT NULL,PRIMARY KEY(namespace,seq))")
            meta = self._meta(required=False)
            if meta is None:
                require(create is True, "fixture dispatch namespace missing; no new epoch inferred")
                require(journal.db.execute("SELECT COUNT(*) FROM fixture_canonical_grants WHERE namespace=?", (self.namespace,)).fetchone()[0] == 0,
                        "prior authority originals without dispatch history; no STATUS resync")
                # Composition-private source seam agreed with the authority
                # owner: fixed resolver + original decoders, under SAME lock.
                _, status, _, ref = authority.bootstrap_zero_native()
                require(ref["revision_id"] == 0 and status["motion_id"] == "0" and status["phase"] == "idle",
                        "fresh zero native session required; never seed IDs from STATUS")
                journal.db.execute("INSERT INTO fixture_canonical_dispatch_meta VALUES (?,?, '0','0','0','0',0,0)",
                                   (self.namespace, record_bytes(policy).decode()))
            else:
                require(meta["policy"] == record_bytes(policy).decode(), "fixed dispatcher policy changed; no migration")
                if meta["quiesced"]:
                    self._quiesce_requested.set()
                if not meta["blocked"]:
                    for row in self._all_rows():
                        if row["state"] == "attempting":
                            row.update(state="unknown", blocked=True, outcome={"effect": "unconfirmed", "reason": "restart_after_attempt_no_resubmit"})
                            self._write("recovery_unknown", row["reservation"]["request_id"], row)
                            break
        if submission_port is not None:
            self.install_submission_port(submission_port)

    def _meta(self, *, required=True):
        cursor = self.journal.db.execute("SELECT policy,last_grant,last_revision,dispatched_grant,last_clock,blocked,quiesced FROM fixture_canonical_dispatch_meta WHERE namespace=?", (self.namespace,))
        row = cursor.fetchone()
        require(row is not None or not required, "missing fixed dispatch metadata")
        return dict(zip(("policy", "last_grant", "last_revision", "dispatched_grant", "last_clock", "blocked", "quiesced"), row)) if row is not None else None

    def _ready(self, *, settle=False):
        require(not self.failed and not self.journal._recorder_failed and callable(self.journal._recorder),
                "canonical dispatcher/recorder unavailable")
        require(not self._timeout_fence.is_set(), "canonical timed-out call remains unconfirmed; no late gate/finish")
        meta = self._meta()
        require(not meta["blocked"], "canonical uncertain durable effect blocks dispatch")
        require(settle or not (meta["quiesced"] or self._quiesce_requested.is_set()), "canonical dispatcher quiesced")
        return meta

    def _now(self):
        value = self.clock()
        require(type(value) is int and 0 < value <= MAX_U64, "qualified receipt clock required")
        meta = self._meta()
        if value < int(meta["last_clock"]):
            self.journal.db.execute("UPDATE fixture_canonical_dispatch_meta SET blocked=1 WHERE namespace=?", (self.namespace,))
            raise FixtureAuthorityDenied("canonical receipt clock rollback; outcome unconfirmed")
        self.journal.db.execute("UPDATE fixture_canonical_dispatch_meta SET last_clock=? WHERE namespace=?", (str(value), self.namespace))
        return value

    def _row_record(self, request_id):
        row = self.journal.db.execute("SELECT record FROM fixture_canonical_dispatch_ops WHERE namespace=? AND request_id=?", (self.namespace, request_id)).fetchone()
        require(row is not None, "unknown exact canonical reservation")
        return json.loads(row[0])

    def _all_rows(self):
        return [json.loads(x[0]) for x in self.journal.db.execute("SELECT record FROM fixture_canonical_dispatch_ops WHERE namespace=?", (self.namespace,)).fetchall()]

    def _base(self, reservation=None):
        m = self._meta()
        return {"reservation": deepcopy(reservation), "admitted": None, "first_consumer_received_home_us": None,
            "attempt_started_home_us": None, "gate_state": "unclaimed", "adapter_disposition": None,
            "state": "reserved" if reservation else "namespace", "outcome": None, "call_timing": None,
            "allocator": {"last_grant_id": m["last_grant"], "last_revision_id": m["last_revision"], "dispatched_grant_id": m["dispatched_grant"]},
            "quiesced": bool(m["quiesced"]), "blocked": bool(m["blocked"])}

    def _write(self, operation, request_id, record):
        require(operation in OPERATIONS and type(record) is dict and set(record) == POST_FIELDS, "closed canonical dispatch transaction")
        # Caller owns journal.lock. Never perform adapter I/O under this lock.
        require(callable(self.journal._recorder) and not self.journal._recorder_failed, "new canonical archive READY required")
        now, tx = self._now(), str(uuid.uuid4())
        record = deepcopy(record)
        m = self._meta()
        # An older operation cannot roll shared counters/Stop back after a
        # newer reservation. Only reserve advances allocation; only the first
        # adapter gate advances the dispatched frontier.
        frontier = {"last_grant_id": m["last_grant"], "last_revision_id": m["last_revision"], "dispatched_grant_id": m["dispatched_grant"]}
        if operation == "reserve":
            require(record["allocator"]["last_grant_id"] == str(int(m["last_grant"]) + 1)
                    and record["allocator"]["last_revision_id"] == str(int(m["last_revision"]) + 1), "allocation frontier must advance exactly once")
            frontier.update(last_grant_id=record["allocator"]["last_grant_id"], last_revision_id=record["allocator"]["last_revision_id"])
        if operation == "before_prepare":
            frontier["dispatched_grant_id"] = record["reservation"]["grant_id"]
        record["allocator"] = frontier
        record["quiesced"] = bool(record["quiesced"] or m["quiesced"] or self._quiesce_requested.is_set())
        record["blocked"] = bool(record["blocked"] or m["blocked"])
        try:
            self.journal.db.execute("UPDATE fixture_canonical_dispatch_meta SET blocked=1 WHERE namespace=?", (self.namespace,))
            digest = _hash(record)
            data = {"tx_id": tx, "namespace": self.namespace, "operation": operation, "request_id": request_id,
                "available_home_us": str(now), "input_sha256": _hash({"operation": operation, "request_id": request_id, "record": record}),
                "record": record}
            self.journal._recorder({"kind": "fixture_canonical_dispatch_prepare", "data": data})
            require(not self.failed and not self.journal._recorder_failed, "dispatch preparation lost archive ACK")
            self.journal.db.execute("BEGIN IMMEDIATE")
            a = record["allocator"]
            self.journal.db.execute("UPDATE fixture_canonical_dispatch_meta SET last_grant=?,last_revision=?,dispatched_grant=?,quiesced=? WHERE namespace=?",
                (a["last_grant_id"], a["last_revision_id"], a["dispatched_grant_id"], int(record["quiesced"]), self.namespace))
            if request_id is not None:
                r = record["reservation"]
                old = self.journal.db.execute("SELECT initial_reservation FROM fixture_canonical_dispatch_ops WHERE namespace=? AND request_id=?", (self.namespace, request_id)).fetchone()
                initial = old[0] if old is not None else record_bytes(r).decode()
                self.journal.db.execute("INSERT OR REPLACE INTO fixture_canonical_dispatch_ops VALUES (?,?,?,?,?,?)",
                    (self.namespace, request_id, r["grant_id"], r["revision_id"], initial, record_bytes(record).decode()))
            seq = self.journal.db.execute("SELECT COUNT(*) FROM fixture_canonical_dispatch_records WHERE namespace=?", (self.namespace,)).fetchone()[0]
            self.journal.db.execute("INSERT INTO fixture_canonical_dispatch_records VALUES (?,?,?)", (self.namespace, seq, record_bytes(data).decode()))
            self.journal.db.execute("COMMIT")
            self.journal._recorder({"kind": "fixture_canonical_dispatch_commit", "data": {
                "tx_id": tx, "namespace": self.namespace, "operation": operation, "request_id": request_id, "record_sha256": digest}})
            require(not self.failed and not self.journal._recorder_failed, "dispatch commit lost archive ACK")
            self.journal.db.execute("UPDATE fixture_canonical_dispatch_meta SET blocked=? WHERE namespace=?", (int(record["blocked"]), self.namespace))
            return deepcopy(record)
        except BaseException:
            if self.journal.db.in_transaction:
                self.journal.db.execute("ROLLBACK")
            self.failed = self.journal._recorder_failed = True
            raise

    def reserve(self, request_id, intent_sha, mode):
        _id(request_id); sha256(intent_sha)
        require(mode in MODES, "automatic ambient/task reservation route only")
        with self.journal.lock:
            old = self.journal.db.execute("SELECT initial_reservation FROM fixture_canonical_dispatch_ops WHERE namespace=? AND request_id=?", (self.namespace, request_id)).fetchone()
            if old is not None:
                original = json.loads(old[0])
                require(original["intent_sha"] == intent_sha and original["mode"] == mode, "changed immutable reservation retry")
                return original
            m = self._ready()
            require(len(self._all_rows()) < self.max_operations, "bounded canonical operation capacity exhausted")
            grant, revision = int(m["last_grant"]) + 1, int(m["last_revision"]) + 1
            require(grant <= MAX_U64 and revision <= MAX_U64, "canonical counters exhausted; no reset")
            r = {"request_id": request_id, "intent_sha": intent_sha, "mode": mode, "grant_id": str(grant), "revision_id": str(revision),
                 "content_sha": None, "grant_sha": None, "state": "reserved", "disposition": None, "barrier": None}
            post = self._base(r)
            post["allocator"].update(last_grant_id=str(grant), last_revision_id=str(revision))
            self._write("reserve", request_id, post)
            return deepcopy(r)

    def row(self, grant, revision):
        u64(grant); u64(revision)
        with self.journal.lock:
            row = self.journal.db.execute("SELECT record FROM fixture_canonical_dispatch_ops WHERE namespace=? AND grant_id=? AND revision_id=?", (self.namespace, grant, revision)).fetchone()
            require(row is not None, "unknown original canonical reservation; never infer from counter")
            return json.loads(row[0])["reservation"]

    def prepare(self, request_id, admitted):
        _id(request_id)
        with self.journal.lock:
            record = self._row_record(request_id)
            if record["admitted"] is not None:
                require(record_bytes(record["admitted"]) == record_bytes(admitted), "changed immutable admitted-command retry")
                return self.status(request_id)
            self._ready()
            require(record["state"] == "reserved" and type(admitted) is dict
                    and admitted.get("schema") == "home-fixture-canonical-admission-1" and admitted.get("version") == 1,
                    "canonical admitted original required")
            r, p, g = record["reservation"], admitted["plan"], admitted["grant_body"]
            require(record_bytes(r) == record_bytes(admitted["reservation"]), "admitted reservation changed")
            require(self.authority.validate_admission(g, r, p) is True, "authority original unavailable after ACK")
            self._owned_predecessor(admitted)
            wire = self.codec.encode_plan(deepcopy(p))
            require(wire.hex() == admitted["wire_hex"] and hashlib.sha256(wire).hexdigest() == admitted["wire_sha256"]
                and hashlib.sha256(canonical_bytes(g)).hexdigest() == admitted["grant_body_sha256"] == p["grant_body_sha256"]
                and g["mode"] == r["mode"] and p["grant_id"] == r["grant_id"] and p["revision_id"] == r["revision_id"],
                "exact canonical plan/grant bytes differ")
            # Sample actual consumer receipt AFTER authority commit/ACK, not G1
            # creation time. This field freezes with the operation forever.
            received = self._now()
            require(u64(g["original_available_home_us"]) <= received, "consumer receipt predates grant creation")
            record.update(admitted=deepcopy(admitted), first_consumer_received_home_us=str(received), state="prepared")
            r.update(state="bound", content_sha=hashlib.sha256(b"C12-CONTENT-V1" + wire[:-40]).hexdigest(), grant_sha=p["grant_body_sha256"])
            self._write("prepare", request_id, record)
            return self.status(request_id)

    def install_submission_port(self, port):
        with self.journal.lock:
            require(self.port is None and type(port) is CanonicalSubmissionPort, "one-shot fixed canonical submission port required")
            self._port_ready(port)
            self.port = port

    def _port_ready(self, port=None):
        port = port if port is not None else self.port
        require(type(port) is CanonicalSubmissionPort and port.evidence_source == "simulated" and port.physical_permission is False
                and port.provider_pins == self.authority.policy.provider_pins
                and port.binding_sha256 == self.authority.binding.binding_sha256, "real/foreign canonical submission port refused")
        adapter = port.adapter
        require(callable(getattr(adapter, "submit", None)) and callable(getattr(adapter, "_evidence_source", None))
                and adapter._evidence_source() == "simulated" and adapter._reservation_gate is self
                and adapter._context_sha.hex() == self.authority.context_digest
                and adapter._policy.device_id.hex() == self.authority.policy.device_id,
                "pinned adapter gate/context/device/source differs")
        description = adapter.describe()
        require(description.get("schema") == "c12-runtime-adapter-draft-1" and description.get("physical_permission") is False,
                "pinned canonical adapter description refused")
        return port

    def _exact_plan(self, p, content_sha=None, grant_sha=None):
        row = self.journal.db.execute("SELECT record FROM fixture_canonical_dispatch_ops WHERE namespace=? AND grant_id=? AND revision_id=?",
            (self.namespace, p["grant_id"], p["revision_id"])).fetchone()
        require(row is not None, "unknown exact dispatch reservation")
        record = json.loads(row[0]); admitted = record["admitted"]
        require(admitted is not None and record_bytes(p) == record_bytes(admitted["plan"]), "pinned adapter changed original plan")
        wire = self.codec.encode_plan(deepcopy(p))
        expected = hashlib.sha256(b"C12-CONTENT-V1" + wire[:-40]).hexdigest()
        require(record["reservation"]["content_sha"] == expected
            and record["reservation"]["grant_sha"] == p["grant_body_sha256"]
            and (content_sha is None or content_sha == expected)
            and (grant_sha is None or grant_sha == p["grant_body_sha256"]), "canonical reservation C/G content changed")
        return record

    def _validate_authority(self, record):
        a = record["admitted"]
        self._owned_predecessor(a)
        return self.authority.validate_admission(a["grant_body"], a["reservation"], a["plan"])

    def _owned_predecessor(self, admitted):
        # A native STATUS high-water is not dispatch history. Only this
        # namespace's exact confirmed original outcomes can advance ownership.
        accepted = [u64(row["reservation"]["revision_id"]) for row in self._all_rows()
            if row["state"] == "terminal" and row["adapter_disposition"] in ("ADMITTED", "DUPLICATE")]
        expected = max(accepted, default=0)
        require(admitted["originals"]["current_reference"]["revision_id"] == expected
                and admitted["plan"]["predecessor_id"] == str(expected),
                "native predecessor is not owned by durable confirmed command history")

    def _monotonic_ns(self):
        value = self.monotonic()
        require(type(value) is int and 0 <= value <= MAX_U64, "fixed integer monotonic nanosecond source required")
        return value

    def check(self, plan, content_sha, grant_sha, stage):
        """Exact injected TrustedAdapter reservation_gate contract."""
        require(stage in ("before_prepare", "before_commit"), "closed pinned adapter gate stage")
        with self.journal.lock:
            m = self._ready()
            record = self._exact_plan(plan, content_sha, grant_sha)
            require(record["state"] == "attempting", "adapter gate has no durable outer attempt")
            self._validate_authority(record)
            if stage == "before_prepare":
                require(record["gate_state"] == "unclaimed" and u64(plan["grant_id"]) > int(m["dispatched_grant"]), "original reservation already claimed/stale")
                for other in self._all_rows():
                    if other["reservation"]["request_id"] == record["reservation"]["request_id"]:
                        continue
                    require(other["state"] != "attempting" and not (u64(other["reservation"]["grant_id"]) < u64(plan["grant_id"])
                            and other["state"] in ("reserved", "prepared")), "shared ambient/task lower pending operation must settle or abandon")
                record["allocator"]["dispatched_grant_id"] = plan["grant_id"]
                record["reservation"]["state"] = "dispatching"
                record["gate_state"] = "preparing"
            else:
                require(record["gate_state"] == "preparing" and record["reservation"]["state"] == "dispatching", "no original PREPARE claim before COMMIT")
                record["gate_state"] = "committing"
            self._write(stage, record["reservation"]["request_id"], record)
            self._ready()  # Stop or recorder failure may have won during ACK.
            self._validate_authority(record)
            return True

    def finish(self, plan, disposition):
        """Authenticated adapter terminal/uncertain disposition, not STATUS."""
        require(disposition in DISPOSITIONS, "closed original adapter disposition")
        with self.journal.lock:
            self._ready(settle=True)
            record = self._exact_plan(plan)
            require(record["state"] == "attempting" and record["gate_state"] in ("preparing", "committing"), "only original in-flight adapter can finish")
            require(disposition == "NOT_COMMITTED" or record["gate_state"] == "committing", "native outcome without COMMIT gate")
            record.update(gate_state="finished", adapter_disposition=disposition, blocked=(disposition == "UNCERTAIN"))
            record["reservation"].update(state="terminal", disposition=disposition)
            self._write("adapter_finish", record["reservation"]["request_id"], record)
            return True

    def dispatch(self, request_id):
        _id(request_id)
        with self.journal.lock:
            record = self._row_record(request_id)
            if record["state"] != "prepared":
                return self.status(request_id)
            # Reserve the sole external-call owner with the durable attempt,
            # while holding the same lock. A second prepared operation stays
            # prepared; it cannot replace a pending/live handle, including the
            # interval before thread.start or after native return but before
            # outcome retention has settled.
            if (self._in_call.is_set()
                    or (self._handle is not None and self._handle["thread"].is_alive())
                    or any(r["state"] == "attempting" for r in self._all_rows())):
                return self.status(request_id)
            try:
                self._ready(); port = self._port_ready()
                self._validate_authority(record)
            except ValueError:
                record.update(state="refused", outcome={"effect": "not_attempted", "reason": "current_authority_or_port_refused"})
                record["reservation"].update(state="terminal", disposition="REFUSED")
                self._write("refuse", request_id, record)
                return self.status(request_id)
            started = self._now()
            record.update(state="attempting", attempt_started_home_us=str(started))
            record["call_timing"] = {"call_timeout_ms": self.call_timeout_ms,
                "attempt_started_monotonic_ns": self._monotonic_ns(), "call_started_monotonic_ns": None,
                "completed_monotonic_ns": None, "elapsed_ns": None,
                "observation_ended_monotonic_ns": None, "call_observation_timed_out": False}
            self._write("attempt", request_id, record)
            try:
                self._ready(); self._validate_authority(record)
                self._port_ready()
            except ValueError:
                # This path has not invoked the adapter at all. Preserve the
                # durable attempt and a truthful no-effect refusal.
                record = self._row_record(request_id)
                record.update(state="refused", outcome={"effect": "not_attempted", "reason": "post_retention_current_or_stop_refusal"})
                record["reservation"].update(state="terminal", disposition="REFUSED")
                self._write("refuse", request_id, record)
                return self.status(request_id)
            self._in_call.set()  # Own the pre-thread-start interval as well.
        # Never hold Journal.lock across adapter.submit: its lookup/gates use
        # this same lock. The attempt has durable DB and archive ACKs already.
        timing = deepcopy(record["call_timing"])
        done = threading.Event()
        handle = {"done": done, "lock": threading.Lock(), "started": None, "completed": None,
                  "outcome": None, "error": False, "invoked": False, "received_home_us": None}
        def invoke():
            try:
                require(not self._quiesce_requested.is_set() and not self._timeout_fence.is_set(), "Stop before adapter invocation")
                at = self._monotonic_ns()
                require(at >= timing["attempt_started_monotonic_ns"], "monotonic clock regressed before invocation")
                with handle["lock"]:
                    handle["started"], handle["invoked"] = at, True
                value = port.adapter.submit(port.credential, {"plan": deepcopy(record["admitted"]["plan"])})
                with handle["lock"]:
                    handle["outcome"] = deepcopy(value)
            except BaseException:
                with handle["lock"]:
                    handle["error"] = True
            finally:
                try:
                    receipt_time = self.clock()
                    require(type(receipt_time) is int and 0 < receipt_time <= MAX_U64, "qualified first consumer receipt clock")
                except BaseException:
                    receipt_time = None
                try:
                    at = self._monotonic_ns()
                except BaseException:
                    at = None
                with handle["lock"]:
                    handle["completed"] = at
                    handle["received_home_us"] = receipt_time
                self._in_call.clear()
                done.set()
        thread = threading.Thread(target=invoke, name="canonical-fixture-" + request_id[:32], daemon=True)
        handle["thread"] = thread
        self._handle = handle
        try:
            thread.start()
        except BaseException:
            self._in_call.clear()
            raise
        # This bounds observation of the whole adapter call, including every
        # PREPARE chunk. It does not cancel a native call or release its owner.
        observed_done = done.wait(self.call_timeout_ms / 1000)
        if not observed_done:
            self._timeout_fence.set()
        with handle["lock"]:
            outcome = deepcopy(handle["outcome"])
            invoked, error = handle["invoked"], handle["error"]
            first_received = handle["received_home_us"]
            timing["call_started_monotonic_ns"] = handle["started"]
            timing["completed_monotonic_ns"] = handle["completed"]
        try:
            timing["observation_ended_monotonic_ns"] = self._monotonic_ns()
        except ValueError:
            timing["observation_ended_monotonic_ns"] = None
        timing["call_observation_timed_out"] = not observed_done
        if timing["completed_monotonic_ns"] is not None and timing["call_started_monotonic_ns"] is not None:
            timing["elapsed_ns"] = timing["completed_monotonic_ns"] - timing["call_started_monotonic_ns"]
        try:
            require(observed_done and not error and timing["elapsed_ns"] is not None
                    and 0 <= timing["elapsed_ns"] <= self.call_timeout_ms * 1000000
                    and timing["observation_ended_monotonic_ns"] is not None
                    and timing["completed_monotonic_ns"] <= timing["observation_ended_monotonic_ns"],
                    "adapter bounded observation unconfirmed; no cancellation claim")
            require(type(outcome) is dict and len(record_bytes(outcome)) <= 16384
                    and outcome.get("schema") == "c12-runtime-adapter-receipt-draft-1"
                    and outcome.get("physical_permission") is False and outcome.get("evidence_source") == "simulated"
                    and outcome.get("wire_sha256") == record["admitted"]["wire_sha256"]
                    and outcome.get("semantic_revision_id") == record["reservation"]["revision_id"]
                    and outcome.get("device_disposition") in DISPOSITIONS and outcome.get("finalization_complete") is True,
                    "original adapter receipt unconfirmed")
            # After a deadline, never wait on a gate's in-flight fsync lock.
            with self.journal.lock:
                current = self._row_record(request_id)
                require(current["gate_state"] == "finished" and current["adapter_disposition"] == outcome["device_disposition"], "adapter receipt differs from durable gate finish")
            state = "unknown" if outcome["device_disposition"] == "UNCERTAIN" else "terminal"
        except Exception:
            state = "unknown" if invoked or not observed_done else "refused"
            # Retain the exact returned original if present; exception text
            # from an adapter may contain private transport details.
            outcome = {"effect": "unconfirmed" if invoked else "not_attempted", "reason": "adapter_call_or_receipt_unconfirmed", "original_adapter_receipt": deepcopy(outcome)}
        if state == "unknown":
            self._timeout_fence.set()  # Late gates and finish cannot promote it.
        if not self.journal.lock.acquire(blocking=False):
            self._pending_timeout = (request_id, state, deepcopy(outcome), deepcopy(timing), started, first_received)
            return {"schema": "home-fixture-canonical-dispatch-status-1", "version": 1, "request_id": request_id,
                "state": "unconfirmed", "pending_retention": True, "active_call": self._in_call.is_set(),
                "namespace_blocked": True, "physical_permission": False}
        try:
            return self._persist_outcome(request_id, state, outcome, timing, started, first_received)
        finally:
            self.journal.lock.release()

    def _persist_outcome(self, request_id, state, outcome, timing, started, first_received):
        current = self._row_record(request_id)
        require(current["state"] == "attempting", "original unknown/terminal outcome cannot be overwritten")
        current["call_timing"] = deepcopy(timing)
        observed = self._now()
        if state != "refused" and (first_received is None or not started <= first_received <= observed):
            state = "unknown"
            self._timeout_fence.set()
        if state == "refused":
            current.update(state=state, outcome={"effect": "not_attempted", "reason": "stop_before_adapter_invocation"})
            current["reservation"].update(state="terminal", disposition="REFUSED")
        else:
            current.update(state=state, blocked=(state == "unknown"), outcome={
                "first_received_home_us": str(first_received) if first_received is not None else None,
                "observed_home_us": str(observed), "original_receipt_time_unavailable": first_received is None,
                "adapter": deepcopy(outcome)})
        self._write("refuse" if state == "refused" else "outcome", request_id, current)
        self._pending_timeout = None
        return self.status(request_id)

    def abandon(self, request_id, *, reason):
        _id(reason)
        with self.journal.lock:
            self._ready(settle=True)
            record = self._row_record(request_id)
            require(record["state"] in ("reserved", "prepared"), "cannot abandon an attempted/unknown effect")
            record.update(state="abandoned", outcome={"effect": "not_attempted", "reason": reason})
            record["reservation"].update(state="abandoned", barrier=_hash({"request_id": request_id, "reason": reason}))
            self._write("abandon", request_id, record)
            return self.status(request_id)

    def quiesce(self):
        self._quiesce_requested.set()
        if not self.journal.lock.acquire(blocking=False):
            return {"status": "unconfirmed", "in_flight": self._in_call.is_set(), "pending_retention": True, "physical_stop_confirmed": False}
        try:
            try:
                self._ready(settle=True)
            except ValueError:
                return {"status": "unconfirmed", "in_flight": self._in_call.is_set(), "physical_stop_confirmed": False}
            record = self._base(); record["quiesced"] = True
            self._write("quiesce", None, record)
            pending = sum(r["state"] in ("attempting", "unknown") for r in self._all_rows())
            return {"status": "unconfirmed" if pending else "quiescent", "in_flight": self._in_call.is_set(), "physical_stop_confirmed": False}
        finally:
            self.journal.lock.release()

    def status(self, request_id):
        with self.journal.lock:
            record = self._row_record(request_id)
            return {"schema": "home-fixture-canonical-dispatch-status-1", "version": 1,
                "request_id": request_id, "state": record["state"], "record": record,
                "namespace_blocked": bool(self._meta()["blocked"]), "physical_permission": False}

    def drain(self):
        """Observe owned-call settlement; never infer native cancellation."""
        self._quiesce_requested.set()
        live = self._in_call.is_set() or (self._handle is not None and self._handle["thread"].is_alive())
        if not self.journal.lock.acquire(blocking=False):
            return {"status": "incomplete", "active_calls": int(live), "pending_retention": True,
                    "safe_to_close_journal": False, "physical_stop_confirmed": False}
        try:
            try:
                require(not self.failed and not self.journal._recorder_failed
                        and callable(self.journal._recorder), "drain requires confirmed recorder effects")
                if self._pending_timeout is not None:
                    self._persist_outcome(*self._pending_timeout)
                if not self._meta()["quiesced"]:
                    record = self._base(); record["quiesced"] = True
                    self._write("quiesce", None, record)
                # DB state alone is insufficient after a lost commit ACK. No
                # healthy drain may advertise a retirement that can resume on
                # restart or whose durable recorder effect is unconfirmed.
                require(not self.failed and not self.journal._recorder_failed
                        and self._meta()["quiesced"], "durable quiesce ACK missing")
            except Exception:
                return {"status": "incomplete", "active_calls": int(live), "pending_retention": True,
                        "safe_to_close_journal": False, "physical_stop_confirmed": False}
            rows = self._all_rows()
            unresolved = sum(r["state"] in ("attempting", "unknown") for r in rows)
            awaiting_outcome = any(r["state"] == "attempting" for r in rows)
            return {"status": "incomplete" if unresolved or live or self._meta()["blocked"] else "drained", "active_calls": int(live),
                    "unconfirmed_operations": unresolved, "safe_to_close_journal": not live and not awaiting_outcome,
                    "physical_stop_confirmed": False}
        finally:
            self.journal.lock.release()

    def export_archive(self):
        with self.journal.lock:
            records = [json.loads(r[0]) for r in self.journal.db.execute("SELECT body FROM fixture_canonical_dispatch_records WHERE namespace=? ORDER BY seq", (self.namespace,)).fetchall()]
            return {"schema": "home-fixture-canonical-dispatch-archive-1", "version": 1,
                "namespace": self.namespace, "policy_sha256": self.policy_digest, "records": records,
                "policy": deepcopy(self.policy),
                "quiesced": bool(self._meta()["quiesced"]), "blocked": bool(self._meta()["blocked"]),
                "ordinary_replay_implemented": False, "physical_permission": False}
