"""Fixed canonical-authority/dispatcher sidecar; causal verification only.

The worker and native transcript archives retain their existing readers. This
sidecar proves retained transaction/state correspondence, never room truth,
native decoding, firmware execution, or a present execution permission.
"""
from copy import deepcopy
import hashlib
from pathlib import Path
import re

from archive import Recorder, ArchiveRefusal, KINDS as WORKER_KINDS, _json
from journal import canonical, digest
from fixture_execution_validation import (CAP_FIELDS, PIN_NAMES, PROJECTION,
    canonical_bytes, record_bytes, require, sha256, u64)

AUTHORITY_KINDS = frozenset({"fixture_canonical_grant_prepare", "fixture_canonical_grant_commit",
                            "fixture_canonical_grant_revocation"})
DISPATCH_KINDS = frozenset({"fixture_canonical_dispatch_prepare", "fixture_canonical_dispatch_commit"})
KINDS = AUTHORITY_KINDS | DISPATCH_KINDS
OPERATIONS = frozenset({"reserve", "prepare", "attempt", "before_prepare", "before_commit",
    "adapter_finish", "outcome", "refuse", "abandon", "quiesce", "recovery_unknown"})
PROVIDER = {"manifest_sha256", "source_sha256", "executable_sha256", "configuration_sha256"}
AUTHORITY_BINDING = {"namespace", "policy_sha256", "fixture_id", "device_id", "principal_id",
    "native_binding_sha256", "context", "context_hex", "pins", "home_scope", "room_binding", "issuers",
    "dispatch_policy", "dispatch_policy_sha256"}
SNAPSHOT_FIELDS = {"reservation", "admitted", "first_consumer_received_home_us",
    "attempt_started_home_us", "gate_state", "adapter_disposition", "state", "outcome",
    "allocator", "quiesced", "blocked", "call_timing"}
TIMING_FIELDS = {"call_timeout_ms", "attempt_started_monotonic_ns", "call_started_monotonic_ns",
                 "completed_monotonic_ns", "elapsed_ns", "observation_ended_monotonic_ns", "call_observation_timed_out"}


def _keys(value, fields, reason):
    require(type(value) is dict and set(value) == set(fields), reason)


def _id(value):
    require(type(value) is str and value.isascii() and 0 < len(value) <= 160
            and all(33 <= ord(c) <= 126 for c in value), "canonical_archive_identifier")


def _hash(value):
    return hashlib.sha256(record_bytes(value)).hexdigest()


def _hex(value, size=None):
    require(type(value) is str and len(value) % 2 == 0 and re.fullmatch("[0-9a-f]*", value) is not None,
            "canonical_archive_original_hex")
    require(size is None or len(value) == 2 * size, "canonical_archive_original_size")
    return bytes.fromhex(value)


def validate_binding(binding, *, codec=None):
    _keys(binding, {"schema", "version", "parent_manifest_sha256", "source_pins", "provider_pins", "authorities"},
          "canonical_archive_binding_fields")
    require(type(binding["version"]) is int and (binding["schema"], binding["version"]) in {
        ("home-fixture-canonical-archive-binding-1", 1),
        ("home-fixture-canonical-archive-binding-2", 2)}, "canonical_archive_binding_version")
    sha256(binding["parent_manifest_sha256"])
    sources = binding["source_pins"]
    # Declaration4 retains 139 source originals. Its explicit successor keeps
    # the legacy 128-pin bound and the same archive byte/resource budgets.
    maximum = 160 if binding["version"] == 2 else 128
    require(type(sources) is dict and 1 <= len(sources) <= maximum, "canonical_archive_source_pins")
    for name, pin in sources.items():
        require(type(name) is str and name.isascii() and 0 < len(name) <= 256
                and not name.startswith(("/", "\\")) and ":" not in name
                and ".." not in name.replace("\\", "/").split("/"), "canonical_archive_relative_source_pin")
        sha256(pin)
    _keys(binding["provider_pins"], PROVIDER, "canonical_archive_provider_pins")
    for pin in binding["provider_pins"].values():
        sha256(pin)
    rows = binding["authorities"]
    require(type(rows) is list and 1 <= len(rows) <= 4, "canonical_archive_fixed_authority_inventory")
    seen = set(); fixtures = set(); devices = set()
    for row in rows:
        _keys(row, AUTHORITY_BINDING, "canonical_archive_authority_binding_fields")
        for key in ("namespace", "policy_sha256", "native_binding_sha256"):
            sha256(row[key])
        _id(row["fixture_id"]); require(u64(row["principal_id"]) > 0, "canonical_archive_principal")
        require(type(row["device_id"]) is str and re.fullmatch("[0-9a-f]{32}", row["device_id"]) is not None,
                "canonical_archive_device_identity")
        require(row["namespace"] == _hash({"fixture_id": row["fixture_id"], "device_id": row["device_id"]}),
                "canonical_archive_namespace_mismatch")
        require(row["namespace"] not in seen and row["fixture_id"] not in fixtures and row["device_id"] not in devices,
                "canonical_archive_duplicate_authority")
        seen.add(row["namespace"]); fixtures.add(row["fixture_id"]); devices.add(row["device_id"])
        context_raw = _hex(row["context_hex"], 323)
        require(type(row["context"]) is dict, "canonical_archive_original_context")
        _keys(row["pins"], PIN_NAMES, "canonical_archive_home_pins")
        for pin in row["pins"].values():
            sha256(pin)
        _keys(row["home_scope"], {"site_id", "room_id", "session_id", "domain_epoch"}, "canonical_archive_home_scope")
        for value in row["home_scope"].values():
            _id(value)
        _keys(row["room_binding"], {"configuration_digest", "manifest_digest", "worker_version", "room_output_schema"},
              "canonical_archive_room_binding")
        sha256(row["room_binding"]["configuration_digest"]); sha256(row["room_binding"]["manifest_digest"])
        _id(row["room_binding"]["worker_version"])
        require(row["room_binding"]["room_output_schema"] == "home-live-room-output-1", "canonical_archive_room_schema")
        issuers = row["issuers"]
        require(type(issuers) is list and len(issuers) == 2, "canonical_archive_disjoint_issuers")
        for mode, issuer in zip(("autonomous-ambient", "autonomous-task"), issuers):
            _keys(issuer, {"mode", "issuer_id", "issuer_epoch"}, "canonical_archive_issuer_fields")
            require(issuer["mode"] == mode and u64(issuer["issuer_epoch"]) > 0, "canonical_archive_issuer_mode")
            _id(issuer["issuer_id"])
        require((issuers[0]["issuer_id"], issuers[0]["issuer_epoch"]) !=
                (issuers[1]["issuer_id"], issuers[1]["issuer_epoch"]), "canonical_archive_disjoint_issuers")
        dp = row["dispatch_policy"]
        _keys(dp, {"schema", "version", "authority_policy_sha256", "namespace", "max_operations", "call_timeout_ms"},
              "canonical_archive_dispatch_policy_fields")
        require(dp["schema"] == "home-fixture-canonical-dispatch-policy-1" and type(dp["version"]) is int
                and dp["version"] == 1 and dp["authority_policy_sha256"] == row["policy_sha256"]
                and dp["namespace"] == row["namespace"] and type(dp["max_operations"]) is int
                and 1 <= dp["max_operations"] <= 65536 and type(dp["call_timeout_ms"]) is int
                and 1 <= dp["call_timeout_ms"] <= 5000 and _hash(dp) == row["dispatch_policy_sha256"],
                "canonical_archive_dispatch_policy_mismatch")
        if codec is not None:
            require(codec.encode_context(deepcopy(row["context"])) == context_raw, "canonical_archive_context_codec_mismatch")
    return deepcopy(binding)


class CanonicalFixtureRecorder(Recorder):
    """The existing bounded/fsynced writer with an explicit successor kind set."""
    def __init__(self, filename, *, binding, max_records=8192, max_bytes=32 * 1024**2, min_free_bytes=1):
        binding = validate_binding(binding)
        manifest = {"schema": "home-live-session-manifest-1", "version": 1,
            "extension": {"schema": "home-fixture-canonical-archive-1", "version": 1,
                          "binding": binding, "physical_permission": False}}
        super().__init__(filename, manifest, max_records=max_records, max_bytes=max_bytes, min_free_bytes=min_free_bytes)

    def record(self, record, *, record_id=None):
        if type(record) is dict and set(record) == {"kind", "data"} and record["kind"] in KINDS:
            require(record_id is None, "canonical_archive_transaction_identity_only")
            require(len(canonical(record).encode()) <= 1024**2, "canonical_archive_record_bound")
            with self.lock:
                return self._append(deepcopy(record))
        if type(record) is dict and record.get("kind") == "session_end":
            return super().record(record, record_id=record_id)
        raise ArchiveRefusal("canonical_archive_record_kind_refused")


def canonical_recorders(worker_recorder, sidecar):
    require(callable(worker_recorder) and type(sidecar) is CanonicalFixtureRecorder,
            "canonical_archive_fixed_recorder_routes")
    def record(body):
        require(type(body) is dict and set(body) == {"kind", "data"}, "canonical_archive_route_fields")
        if body["kind"] in KINDS:
            return sidecar.record(body)
        require(body["kind"] in WORKER_KINDS, "canonical_archive_route_kind_refused")
        return worker_recorder(body)
    return record


def _admission(receipt, bound, provider, codec):
    _keys(receipt, {"schema", "version", "grant_body", "grant_body_sha256", "plan", "wire_hex", "wire_sha256",
        "context_hex", "reservation", "decision_ref", "originals", "policy_sha256", "physical_permission"},
        "canonical_archive_admission_fields")
    require(receipt["schema"] == "home-fixture-canonical-admission-1" and type(receipt["version"]) is int
            and receipt["version"] == 1 and receipt["physical_permission"] is False
            and receipt["policy_sha256"] == bound["policy_sha256"], "canonical_archive_admission_binding")
    g, plan, r, originals = (receipt[key] for key in ("grant_body", "plan", "reservation", "originals"))
    require(g["schema"] == "home-fixture-execution-grant-1" and type(g["version"]) is int and g["version"] == 1,
            "canonical_archive_grant_version")
    wire = _hex(receipt["wire_hex"], 409); context = _hex(receipt["context_hex"], 323)
    require(codec.encode_plan(deepcopy(plan)) == wire and context == _hex(bound["context_hex"])
            and hashlib.sha256(wire).hexdigest() == receipt["wire_sha256"]
            and hashlib.sha256(canonical_bytes(g)).hexdigest() == receipt["grant_body_sha256"]
            and plan["grant_body_sha256"] == receipt["grant_body_sha256"]
            and wire[-40:-32] == u64(g["grant_id"]).to_bytes(8, "little")
            and wire[-32:] == _hex(receipt["grant_body_sha256"], 32), "canonical_archive_original_grant_wire_mismatch")
    require(g["fixture_id"] == bound["fixture_id"] and g["device_id"] == bound["device_id"]
            and g["principal_id"] == bound["principal_id"] and g["pins"] == bound["pins"]
            and g["context_digest"] == hashlib.sha256(context).hexdigest() == plan["context_digest"]
            and g["session_principal_id"] == bound["context"]["principal_id"]
            and g["ownership_generation"] == bound["context"]["ownership_generation"], "canonical_archive_grant_scope_mismatch")
    require(any(all(g[k] == issuer[k] for k in ("mode", "issuer_id", "issuer_epoch")) for issuer in bound["issuers"]),
            "canonical_archive_grant_issuer_mismatch")
    content = b"C12-CONTENT-V1" + wire[:-40]
    require(g["command_content_hex"] == content.hex() and g["command_content_sha256"] == hashlib.sha256(content).hexdigest()
            and all(type(plan[k]) is type(g[k]) and plan[k] == g[k] for k in PROJECTION)
            and all(plan[k] == g["caps"][k] for k in CAP_FIELDS), "canonical_archive_grant_projection_mismatch")
    require(r["mode"] == g["mode"] and r["grant_id"] == plan["grant_id"] == g["grant_id"]
            and r["revision_id"] == plan["revision_id"] and r["state"] == "reserved", "canonical_archive_grant_reservation_mismatch")
    issued, expiry = u64(g["issued_home_us"]), u64(g["support_expiry_home_us"])
    require(u64(g["evidence_observed_home_us"]) <= issued <= u64(g["admitted_home_us"])
            == u64(g["original_available_home_us"]) < expiry <= min(u64(g["caps"][k]) for k in CAP_FIELDS),
            "canonical_archive_grant_original_timing")
    mask, fields = plan["domain_mask"], plan["field_mask"]
    require(plan["manual"] is False and plan["preset_id"] == "0" and plan["manual_duration_minutes"] == 0,
            "canonical_archive_manual_or_preset_scope")
    require(type(mask) is int and type(fields) is list and len(fields) == 8, "canonical_archive_domain_fields")
    if g["mode"] == "autonomous-ambient":
        require(plan["kind"] == "LEVELS" and 0 < mask < 128 and fields[7] == 0
                and all((fields[i] in (1, 2, 3)) if mask & (1 << i) else fields[i] == 0 for i in range(7)),
                "canonical_archive_ambient_scope")
    else:
        require(mask == 128 and fields[:7] == [0] * 7 and fields[7] in (4, 8, 12)
                and plan["kind"] in ("MOVE", "LEVELS", "CONTINUE"), "canonical_archive_task_scope")
        require(plan["kind"] != "MOVE" or fields[7] & 4, "canonical_archive_task_move_requires_aim")
        require(plan["kind"] != "LEVELS" or fields[7] == 8, "canonical_archive_task_levels_cannot_change_aim")
    _keys(originals, {"schema", "version", "home_scope", "room_commit_ref", "provider_pins", "current_reference",
                      "status3_hex", "capabilities2_hex"}, "canonical_archive_original_fields")
    room = originals["room_commit_ref"]; current = originals["current_reference"]
    require(originals["schema"] == "home-fixture-canonical-command-originals-1" and originals["version"] == 1
            and originals["home_scope"] == bound["home_scope"] and originals["provider_pins"] == provider
            and all(room[k] == v for k, v in bound["room_binding"].items())
            and receipt["decision_ref"] == _hash(room), "canonical_archive_room_original_binding")
    status = _hex(originals["status3_hex"], 998); cap = _hex(originals["capabilities2_hex"], 404)
    require(current["binding_sha256"] == bound["native_binding_sha256"]
            and current["context_digest"] == g["context_digest"] and current["evidence_source"] == "simulated"
            and current["snapshot_sha256"] == hashlib.sha256(status).hexdigest()
            and current["capabilities_sha256"] == hashlib.sha256(cap).hexdigest()
            and all(current[k] == int(bound["context"][k]) for k in ("boot_generation", "session_id", "ownership_generation"))
            and current["revision_id"] == u64(plan["predecessor_id"])
            and current["state_generation"] == u64(plan["expected_state_generation"])
            and current["domain_generations"] == plan["domain_generations"], "canonical_archive_native_original_binding")
    require(len(plan["domain_generations"]) == 8 and all(type(v) is int and 0 <= v < 2**32 for v in plan["domain_generations"]),
            "canonical_archive_all8_domains")
    return deepcopy(receipt)


class _DispatcherReplay:
    """Independent finite transition oracle, with no dispatcher code import."""
    def __init__(self, grants, requests, revoked, authorities):
        self.grants, self.requests, self.revoked = grants, requests, revoked
        self.authorities = authorities
        self.rows, self.meta, self.last_clock = {}, {}, {}
        self.native_revision = {}
        self.transitions = 0

    def _meta(self, ns):
        return self.meta.setdefault(ns, {"allocator": {"last_grant_id": "0", "last_revision_id": "0",
            "dispatched_grant_id": "0"}, "quiesced": False, "blocked": False})

    def grant_committed(self, ns, request, receipt):
        row = self.rows.get((ns, request))
        require(row is not None and row["state"] == "reserved"
                and record_bytes(row["reservation"]) == record_bytes(receipt["reservation"]),
                "canonical_archive_grant_reserved_original_missing")
        require(u64(receipt["grant_body"]["admitted_home_us"]) >= self.last_clock.get(ns, 0),
                "canonical_archive_grant_predates_reservation")
        require(receipt["plan"]["predecessor_id"] == self.native_revision.get(ns, "0"),
                "canonical_archive_foreign_native_predecessor")

    def prepare(self, data):
        _keys(data, {"tx_id", "namespace", "operation", "request_id", "available_home_us", "input_sha256", "record"},
              "canonical_archive_dispatch_prepare_fields")
        op, request, row, ns = (data[k] for k in ("operation", "request_id", "record", "namespace"))
        require(op in OPERATIONS and data["input_sha256"] == _hash({"operation": op, "request_id": request, "record": row}),
                "canonical_archive_dispatch_input_mismatch")
        _keys(row, SNAPSHOT_FIELDS, "canonical_archive_dispatch_snapshot_fields")
        _keys(row["allocator"], {"last_grant_id", "last_revision_id", "dispatched_grant_id"},
              "canonical_archive_allocator_fields")
        for value in row["allocator"].values():
            u64(value)
        require(type(row["quiesced"]) is bool and type(row["blocked"]) is bool, "canonical_archive_dispatch_flags")
        timing = row["call_timing"]
        if timing is not None:
            _keys(timing, TIMING_FIELDS, "canonical_archive_call_timing_fields")
            require(timing["call_timeout_ms"] == self.authorities[ns]["dispatch_policy"]["call_timeout_ms"],
                    "canonical_archive_call_timeout_changed")
            require(type(timing["call_observation_timed_out"]) is bool, "canonical_archive_timeout_observation_flag")
            for key in TIMING_FIELDS - {"call_timeout_ms", "call_observation_timed_out"}:
                require(timing[key] is None or type(timing[key]) is int and 0 <= timing[key] < 2**64,
                        "canonical_archive_original_monotonic_ns")
        now = u64(data["available_home_us"])
        require(now >= self.last_clock.get(ns, 0), "canonical_archive_dispatch_clock_rollback")
        meta = self._meta(ns)
        require(not meta["blocked"], "canonical_archive_blocked_namespace_reused")
        require(not meta["quiesced"] or op in ("adapter_finish", "outcome", "refuse", "abandon", "quiesce", "recovery_unknown"),
                "canonical_archive_effect_after_quiesce")
        if op == "quiesce":
            require(request is None and row["reservation"] is None and row["admitted"] is None
                    and row["first_consumer_received_home_us"] is None and row["attempt_started_home_us"] is None
                    and row["call_timing"] is None
                    and row["gate_state"] == "unclaimed" and row["adapter_disposition"] is None
                    and row["state"] == "namespace" and row["outcome"] is None
                    and row["allocator"] == meta["allocator"] and row["quiesced"] is True and row["blocked"] is False,
                    "canonical_archive_quiesce_transition")
            return
        _id(request)
        r = row["reservation"]
        _keys(r, {"request_id", "intent_sha", "mode", "grant_id", "revision_id", "content_sha", "grant_sha",
                  "state", "disposition", "barrier"}, "canonical_archive_reservation_fields")
        require(r["request_id"] == request and r["mode"] in ("autonomous-ambient", "autonomous-task")
                and u64(r["grant_id"]) > 0 and u64(r["revision_id"]) > 0, "canonical_archive_reservation_identity")
        sha256(r["intent_sha"])
        prior = self.rows.get((ns, request))
        if op == "reserve":
            require(prior is None and (ns, request) not in self.requests, "canonical_archive_duplicate_reservation")
            require(sum(other_ns == ns for other_ns, _ in self.rows) < self.authorities[ns]["dispatch_policy"]["max_operations"],
                    "canonical_archive_dispatch_capacity_exhausted")
            expected = deepcopy(meta["allocator"])
            expected["last_grant_id"] = str(u64(expected["last_grant_id"]) + 1)
            expected["last_revision_id"] = str(u64(expected["last_revision_id"]) + 1)
            require(r["grant_id"] == expected["last_grant_id"] and r["revision_id"] == expected["last_revision_id"]
                    and row["allocator"] == expected and r["state"] == row["state"] == "reserved"
                    and all(r[k] is None for k in ("content_sha", "grant_sha", "disposition", "barrier"))
                    and all(row[k] is None for k in ("admitted", "first_consumer_received_home_us",
                        "attempt_started_home_us", "adapter_disposition", "outcome", "call_timing"))
                    and row["gate_state"] == "unclaimed" and row["quiesced"] is False and row["blocked"] is False,
                    "canonical_archive_reserve_transition")
            return
        require(prior is not None, "canonical_archive_dispatch_predecessor_missing")
        expected = deepcopy(prior)
        expected.update(allocator=deepcopy(meta["allocator"]), quiesced=meta["quiesced"], blocked=meta["blocked"])
        require(all(r[k] == prior["reservation"][k] for k in
                ("request_id", "intent_sha", "mode", "grant_id", "revision_id")), "canonical_archive_reservation_changed")
        if op == "prepare":
            key = (ns, r["grant_id"])
            require(prior["state"] == "reserved" and key in self.grants and key not in self.revoked
                    and row["admitted"] == self.grants[key] and self.requests.get((ns, request)) == key,
                    "canonical_archive_admission_original_missing")
            a = row["admitted"]; received = u64(row["first_consumer_received_home_us"])
            require(u64(a["grant_body"]["original_available_home_us"]) <= received <= now
                    and received < min(u64(a["plan"]["support_expiry_home_us"]),
                        *(u64(a["plan"][k]) for k in CAP_FIELDS)), "canonical_archive_post_ack_consumer_clock")
            expected.update(admitted=deepcopy(a), first_consumer_received_home_us=str(received), state="prepared")
            expected["reservation"].update(state="bound", content_sha=a["grant_body"]["command_content_sha256"],
                                           grant_sha=a["grant_body_sha256"])
        elif op == "attempt":
            require(prior["state"] == "prepared" and prior["gate_state"] == "unclaimed", "canonical_archive_attempt_transition")
            started = u64(row["attempt_started_home_us"])
            require(u64(prior["first_consumer_received_home_us"]) <= started <= now,
                    "canonical_archive_attempt_clock")
            self._support(ns, prior, now)
            require(timing is not None and type(timing["attempt_started_monotonic_ns"]) is int
                    and timing["call_observation_timed_out"] is False
                    and all(timing[k] is None for k in ("call_started_monotonic_ns", "completed_monotonic_ns", "elapsed_ns",
                                                       "observation_ended_monotonic_ns")),
                    "canonical_archive_attempt_monotonic_original_missing")
            expected.update(state="attempting", attempt_started_home_us=str(started), call_timing=deepcopy(timing))
        elif op == "before_prepare":
            require(prior["state"] == "attempting" and prior["gate_state"] == "unclaimed"
                    and u64(r["grant_id"]) > u64(meta["allocator"]["dispatched_grant_id"]),
                    "canonical_archive_prepare_gate_transition")
            require(all(other["state"] != "attempting" and not (u64(other["reservation"]["grant_id"]) < u64(r["grant_id"])
                    and other["state"] in ("reserved", "prepared")) for (other_ns, other_request), other in self.rows.items()
                    if other_ns == ns and other_request != request), "canonical_archive_lower_pending_command")
            self._support(ns, prior, now)
            expected["allocator"]["dispatched_grant_id"] = r["grant_id"]
            expected["reservation"]["state"] = "dispatching"; expected["gate_state"] = "preparing"
        elif op == "before_commit":
            require(prior["state"] == "attempting" and prior["gate_state"] == "preparing"
                    and prior["reservation"]["state"] == "dispatching", "canonical_archive_commit_gate_transition")
            self._support(ns, prior, now)
            expected["gate_state"] = "committing"
        elif op == "adapter_finish":
            require(prior["state"] == "attempting" and prior["gate_state"] in ("preparing", "committing"),
                    "canonical_archive_adapter_finish_transition")
            disposition = row["adapter_disposition"]
            require(disposition in {"NOT_COMMITTED", "UNCERTAIN", "ADMITTED", "DUPLICATE", "REJECTED", "EXPIRED",
                "CONTEXT_MISMATCH", "NO_SESSION", "CHANGED_DUPLICATE", "REPLAY", "UNAUTHORIZED", "NO_KEY", "MALFORMED",
                "BUSY", "INCOMPLETE", "CONFLICT", "OUT_OF_RANGE", "OK"}
                and (disposition == "NOT_COMMITTED" or prior["gate_state"] == "committing"),
                "canonical_archive_native_outcome_without_commit")
            expected.update(gate_state="finished", adapter_disposition=disposition, blocked=disposition == "UNCERTAIN")
            expected["reservation"].update(state="terminal", disposition=disposition)
        elif op == "outcome":
            require(prior["state"] == "attempting", "canonical_archive_outcome_without_attempt")
            outcome = row["outcome"]
            _keys(outcome, {"first_received_home_us", "observed_home_us", "original_receipt_time_unavailable", "adapter"},
                  "canonical_archive_outcome_fields")
            require(type(outcome["original_receipt_time_unavailable"]) is bool, "canonical_archive_outcome_clock_flag")
            require(outcome["observed_home_us"] is None or u64(prior["attempt_started_home_us"]) <=
                    u64(outcome["observed_home_us"]) <= now, "canonical_archive_outcome_observation_clock")
            if outcome["first_received_home_us"] is not None:
                require(outcome["original_receipt_time_unavailable"] is False
                        and outcome["observed_home_us"] is not None
                        and u64(prior["attempt_started_home_us"]) <= u64(outcome["first_received_home_us"]) <=
                        u64(outcome["observed_home_us"]) <= now,
                        "canonical_archive_outcome_clock")
            else:
                require(outcome["original_receipt_time_unavailable"] is True and row["state"] == "unknown",
                        "canonical_archive_unknown_receipt_clock")
            require(row["state"] in ("terminal", "unknown"), "canonical_archive_outcome_state")
            prior_timing = prior["call_timing"]
            require(timing is not None and prior_timing is not None
                    and timing["attempt_started_monotonic_ns"] == prior_timing["attempt_started_monotonic_ns"]
                    and timing["call_timeout_ms"] == prior_timing["call_timeout_ms"], "canonical_archive_call_start_changed")
            measured = all(type(timing[k]) is int for k in
                ("attempt_started_monotonic_ns", "call_started_monotonic_ns", "completed_monotonic_ns", "elapsed_ns"))
            timely = measured and timing["attempt_started_monotonic_ns"] <= timing["call_started_monotonic_ns"] <= timing["completed_monotonic_ns"] \
                and timing["elapsed_ns"] == timing["completed_monotonic_ns"] - timing["call_started_monotonic_ns"] \
                and timing["elapsed_ns"] <= timing["call_timeout_ms"] * 1000000 \
                and timing["call_observation_timed_out"] is False \
                and type(timing["observation_ended_monotonic_ns"]) is int \
                and timing["completed_monotonic_ns"] <= timing["observation_ended_monotonic_ns"]
            if row["state"] == "terminal":
                a = outcome["adapter"]
                require(timely and prior["gate_state"] == "finished" and prior["adapter_disposition"] != "UNCERTAIN"
                        and a["schema"] == "c12-runtime-adapter-receipt-draft-1" and a["physical_permission"] is False
                        and a["evidence_source"] == "simulated" and a["finalization_complete"] is True
                        and a["wire_sha256"] == prior["admitted"]["wire_sha256"]
                        and a["semantic_revision_id"] == r["revision_id"]
                        and a["device_disposition"] == prior["adapter_disposition"], "canonical_archive_adapter_original_mismatch")
            expected.update(state=row["state"], blocked=row["state"] == "unknown", outcome=deepcopy(outcome),
                            call_timing=deepcopy(timing))
        elif op == "refuse":
            require((prior["state"] == "prepared" or prior["state"] == "attempting" and prior["gate_state"] == "unclaimed"
                    and prior["call_timing"]["call_started_monotonic_ns"] is None)
                    and row["outcome"].get("effect") == "not_attempted",
                    "canonical_archive_refusal_after_attempt")
            if timing != prior["call_timing"]:
                before = prior["call_timing"]
                require(before is not None and timing is not None
                        and timing["attempt_started_monotonic_ns"] == before["attempt_started_monotonic_ns"]
                        and timing["call_timeout_ms"] == before["call_timeout_ms"]
                        and timing["call_started_monotonic_ns"] is None and timing["elapsed_ns"] is None
                        and timing["call_observation_timed_out"] is False
                        and type(timing["completed_monotonic_ns"]) is int
                        and type(timing["observation_ended_monotonic_ns"]) is int
                        and timing["attempt_started_monotonic_ns"] <= timing["completed_monotonic_ns"] <=
                            timing["observation_ended_monotonic_ns"], "canonical_archive_no_invocation_proof_missing")
                expected["call_timing"] = deepcopy(timing)
            expected.update(state="refused", outcome=deepcopy(row["outcome"]))
            expected["reservation"].update(state="terminal", disposition="REFUSED")
        elif op == "abandon":
            require(prior["state"] in ("reserved", "prepared") and row["outcome"].get("effect") == "not_attempted",
                    "canonical_archive_abandon_after_attempt")
            reason = row["outcome"]["reason"]; _id(reason)
            expected.update(state="abandoned", outcome=deepcopy(row["outcome"]))
            expected["reservation"].update(state="abandoned", barrier=_hash({"request_id": request, "reason": reason}))
        elif op == "recovery_unknown":
            require(prior["state"] == "attempting" and row["outcome"] ==
                    {"effect": "unconfirmed", "reason": "restart_after_attempt_no_resubmit"},
                    "canonical_archive_recovery_without_attempt")
            expected.update(state="unknown", blocked=True, outcome=deepcopy(row["outcome"]))
        require(record_bytes(row) == record_bytes(expected), "canonical_archive_dispatch_transition_changed_fields")

    def _support(self, ns, row, now):
        a = row["admitted"]; g = a["grant_body"]
        require((ns, g["grant_id"]) not in self.revoked and now < min(u64(g["support_expiry_home_us"]),
                *(u64(g["caps"][k]) for k in CAP_FIELDS)), "canonical_archive_dispatch_support_expired_or_revoked")

    def commit(self, data, prepared):
        require(data == {"tx_id": prepared["tx_id"], "namespace": prepared["namespace"],
                "operation": prepared["operation"], "request_id": prepared["request_id"],
                "record_sha256": _hash(prepared["record"])}, "canonical_archive_dispatch_commit_mismatch")
        # Another retained transaction cannot make a previously checked post
        # snapshot a valid rewind of the now-current allocator or gate state.
        self.prepare(prepared)
        ns = data["namespace"]; row = deepcopy(prepared["record"])
        self.meta[ns] = {key: deepcopy(row[key]) for key in ("allocator", "quiesced", "blocked")}
        self.last_clock[ns] = u64(prepared["available_home_us"])
        if data["request_id"] is not None:
            self.rows[(ns, data["request_id"])] = row
            if row["state"] == "terminal" and row["adapter_disposition"] in ("ADMITTED", "DUPLICATE"):
                self.native_revision[ns] = row["reservation"]["revision_id"]
        self.transitions += 1

    def finish(self):
        require(all(not meta["blocked"] and meta["quiesced"] for meta in self.meta.values()),
                "canonical_archive_dispatcher_not_quiescent")
        require(all(row["state"] not in ("attempting", "unknown") and not row["blocked"] for row in self.rows.values()),
                "canonical_archive_effect_unresolved")


def replay_canonical_fixture_archive(filename, *, expected_binding, codec):
    """Verify exact retained causal transitions; no execution or current grant."""
    base = {"current": False, "physical_verified": False, "firmware_recomputed": False,
            "worker_recomputed": False, "room_component_recomputed": False,
            "authority_admission_recomputed": False, "clock_qualification_recomputed": False,
            "native_originals_decoded": False}
    try:
        require(callable(getattr(codec, "encode_plan", None)) and callable(getattr(codec, "encode_context", None)),
                "canonical_archive_pinned_codecs_required")
        binding = validate_binding(expected_binding, codec=codec)
        path = Path(filename)
        require(path.is_absolute() and not path.is_symlink() and path.is_file() and 0 < path.stat().st_size <= 64 * 1024**2,
                "canonical_archive_file_bound")
        with path.open("rb") as stream:
            raw = stream.read(64 * 1024**2 + 1)
        require(0 < len(raw) <= 64 * 1024**2 and raw.endswith(b"\n"), "canonical_archive_incomplete_tail")
        lines = raw.splitlines(); require(2 <= len(lines) <= 16384, "canonical_archive_record_count")
        authorities = {row["namespace"]: row for row in binding["authorities"]}
        previous = None; pending = {}; seen_tx = set(); grants = {}; requests = {}; revoked = set(); ended = False
        dispatcher = _DispatcherReplay(grants, requests, revoked, authorities)
        for seq, line in enumerate(lines):
            require(0 < len(line) <= 1024**2, "canonical_archive_record_bound")
            entry = _json(line)
            _keys(entry, {"schema", "version", "seq", "previous_digest", "record", "digest"}, "canonical_archive_entry_fields")
            require(entry["schema"] == "home-live-session-record-1" and type(entry["version"]) is int and entry["version"] == 1
                    and type(entry["seq"]) is int and entry["seq"] == seq and entry["previous_digest"] == previous
                    and entry["digest"] == digest({k: v for k, v in entry.items() if k != "digest"})
                    and canonical(entry).encode() == line, "canonical_archive_chain_mismatch")
            previous = entry["digest"]; record = entry["record"]
            _keys(record, {"kind", "data"}, "canonical_archive_record_fields")
            kind, data = record["kind"], record["data"]
            if seq == 0:
                require(kind == "session_manifest" and data["manifest"] == {
                    "schema": "home-live-session-manifest-1", "version": 1,
                    "extension": {"schema": "home-fixture-canonical-archive-1", "version": 1,
                                  "binding": binding, "physical_permission": False}}, "canonical_archive_manifest_mismatch")
                continue
            require(not ended, "canonical_archive_after_finalization")
            if kind == "session_end":
                require(seq == len(lines) - 1 and data.get("status") == "complete" and not pending,
                        "canonical_archive_incomplete_transactions")
                dispatcher.finish(); ended = True; continue
            require(kind in KINDS and data["namespace"] in authorities, "canonical_archive_kind_or_namespace")
            ns = data["namespace"]; bound = authorities[ns]
            if kind.endswith("_prepare"):
                tx = data["tx_id"]; _id(tx)
                require(tx not in seen_tx, "canonical_archive_duplicate_transaction")
                seen_tx.add(tx)
                if kind == "fixture_canonical_grant_prepare":
                    _keys(data, {"tx_id", "namespace", "request_id", "input_sha256", "receipt"}, "canonical_archive_grant_prepare_fields")
                    receipt = _admission(data["receipt"], bound, binding["provider_pins"], codec)
                    unsigned = deepcopy(receipt["plan"]); unsigned["grant_body_sha256"] = "0" * 64
                    require(data["input_sha256"] == _hash({"mode": receipt["grant_body"]["mode"],
                            "plan": unsigned, "reservation": receipt["reservation"]})
                            and receipt["reservation"]["request_id"] == data["request_id"], "canonical_archive_grant_input_mismatch")
                else:
                    dispatcher.prepare(data)
                pending[tx] = (kind, deepcopy(data)); continue
            if kind == "fixture_canonical_grant_revocation":
                _keys(data, {"namespace", "grant_id", "reason", "available_home_us"}, "canonical_archive_revocation_fields")
                key = (ns, data["grant_id"]); _id(data["reason"])
                require(key in grants and key not in revoked and u64(data["available_home_us"]) >=
                        max(u64(grants[key]["grant_body"]["admitted_home_us"]), dispatcher.last_clock.get(ns, 0)),
                        "canonical_archive_revocation_original_missing")
                revoked.add(key); continue
            tx = data["tx_id"]; require(tx in pending, "canonical_archive_commit_original_missing")
            prepared_kind, prepared = pending.pop(tx)
            require(kind == prepared_kind.replace("_prepare", "_commit") and ns == prepared["namespace"],
                    "canonical_archive_commit_kind_mismatch")
            if kind == "fixture_canonical_grant_commit":
                receipt = prepared["receipt"]; key = (ns, receipt["grant_body"]["grant_id"])
                require(data == {"tx_id": tx, "namespace": ns, "request_id": prepared["request_id"],
                        "grant_body_sha256": receipt["grant_body_sha256"]} and key not in grants
                        and (ns, prepared["request_id"]) not in requests, "canonical_archive_grant_commit_mismatch")
                grants[key] = receipt; requests[(ns, prepared["request_id"])] = key
                dispatcher.grant_committed(ns, prepared["request_id"], receipt)
            else:
                dispatcher.commit(data, prepared)
        require(ended and not pending, "canonical_archive_finalization_missing")
        return {**base, "status": "causal_archive_verified", "grants": len(grants), "revocations": len(revoked),
                "dispatcher_transitions": dispatcher.transitions, "last_digest": previous,
                "configured_fixtures": len(authorities), "observed_dispatcher_namespaces": len(dispatcher.meta),
                "scope": "retained_canonical_authority_and_dispatcher_causality_only"}
    except (ValueError, KeyError, TypeError, OverflowError, OSError, AttributeError, IndexError) as error:
        return {**base, "status": "incomplete", "reason": str(error)}
