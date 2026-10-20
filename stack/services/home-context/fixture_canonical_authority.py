"""Private canonical fixture authority on the existing factory-owned Journal.

Two fixed automatic issuer routes share the Journal, not an ID allocator.
Current room commits, native originals, reservations, validation, codecs and
clocks are installed process capabilities. A frozen state is not authentication:
the current resolver must independently read the SAME Journal under its lock.
No body-supplied resolver, transport, provider import, manual route or scheduler
exists here. New recorder kinds deliberately require a successor archive router.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, is_dataclass
import hashlib
import hmac
import json
import re
import uuid

from fixture_execution_validation import (CAP_FIELDS, GEN_FIELDS, MAX_U64, PIN_NAMES,
    PROJECTION, FixtureAuthorityDenied, canonical_bytes, record_bytes, require, sha256, u64)
from fixture_joint_execution import CURRENT_FIELDS, WIRE_PINS

MODES = ("autonomous-ambient", "autonomous-task")
ROOM_BINDING_FIELDS = frozenset({"configuration_digest", "manifest_digest", "worker_version", "room_output_schema"})
ROOM_REF_FIELDS = ROOM_BINDING_FIELDS | {"commit_id", "commit_body_sha256", "decision_sha256",
                                        "room_output_revision_id", "room_output_sha256"}
PROVIDER_FIELDS = frozenset({"manifest_sha256", "source_sha256", "executable_sha256", "configuration_sha256"})
HOME_SCOPE_FIELDS = frozenset({"site_id", "room_id", "session_id", "domain_epoch"})
KINDS = frozenset({"fixture_canonical_grant_prepare", "fixture_canonical_grant_commit",
                   "fixture_canonical_grant_revocation"})


@dataclass(frozen=True)
class CanonicalIssuerPolicy:
    mode: str
    issuer_id: str
    issuer_epoch: str
    lookup_credential: str


@dataclass(frozen=True)
class CanonicalFixtureAuthorityPolicy:
    fixture_id: str
    device_id: str
    context: dict
    pins: dict
    principal_id: str
    ambient_issuer: CanonicalIssuerPolicy
    task_issuer: CanonicalIssuerPolicy
    home_scope: dict
    room_binding: dict
    provider_pins: dict
    max_support_us: int = 3_000_000
    max_lookup_age_us: int = 250_000
    clock_error_us: int = 0
    capacity: int = 4096


@dataclass(frozen=True)
class CanonicalAuthorityState:
    eligible: bool
    home_scope: dict
    room_commit_ref: dict
    source_generation: str
    authority_generation: str
    source_cut: str
    protection: str
    authority: str
    evidence_observed_home_us: str
    caps: dict
    current_ref: object
    raw_status_v3: bytes
    raw_capabilities_v2: bytes


@dataclass(frozen=True)
class CanonicalNativeBootstrapOriginal:
    """Fresh native zero original; never an eligible command state or grant."""
    home_scope: dict
    room_commit_ref: dict
    authority_generation: str
    sampled_home_us: int
    qualified_until_home_us: int
    clock_error_us: int
    current_ref: object
    raw_status_v3: bytes
    raw_capabilities_v2: bytes


def _closed(value, keys, name):
    require(type(value) is dict and set(value) == set(keys), "closed " + name)


def _identifier(value):
    require(type(value) is str and 0 < len(value) <= 160 and value.isascii()
            and all(33 <= ord(c) <= 126 for c in value), "bounded identifier")


def _frozen_body(value, fields, name):
    require(is_dataclass(value) and not isinstance(value, type) and value.__dataclass_params__.frozen,
            "fixed immutable " + name + " required; JSON is not authority")
    require(all(hasattr(value, key) for key in fields), "missing " + name + " fields")
    return {key: deepcopy(getattr(value, key)) for key in fields}


def _digest(value):
    return hashlib.sha256(record_bytes(value)).hexdigest()


class CanonicalFixtureAuthority:
    """No IDs or current-state assertions are obtained from a submitted plan.

    ``current_resolver(mode)`` must verify the current durable room publication
    and its receipt using this Journal while its lock is held. The mandatory
    ``command_validator(plan, state)`` independently proves that exact room
    component, original support, full24 power/mixing or qualified joint target.
    It must not merely check a supplied hash. No dispatcher is supplied here.
    """
    def __init__(self, journal, *, policy, codec, binding, decode_status, decode_capabilities,
                 current_resolver, reservation_reader, command_validator, clock, create=False,
                 bootstrap_resolver=None):
        require(type(policy) is CanonicalFixtureAuthorityPolicy and journal is not None,
                "fixed canonical authority composition required")
        require(all(callable(x) for x in (getattr(codec, "encode_plan", None), getattr(codec, "encode_context", None),
            decode_status, decode_capabilities, current_resolver, reservation_reader, command_validator, clock)),
            "fixed codec/resolver/independent validator/clock ports required")
        self.journal, self.policy, self.codec = journal, deepcopy(policy), codec
        self.binding = binding
        self.decode_status, self.decode_capabilities = decode_status, decode_capabilities
        self.current_resolver, self.reservation_reader = current_resolver, reservation_reader
        self.command_validator, self.clock = command_validator, clock
        if bootstrap_resolver is not None:
            owner = getattr(current_resolver, '__self__', None)
            require(owner is not None and getattr(bootstrap_resolver, '__self__', None) is owner
                    and bootstrap_resolver == getattr(owner, 'bootstrap_original', None)
                    and getattr(getattr(owner, 'source', owner), 'journal', None) is journal,
                    'same fixed resolver/Journal native bootstrap required')
        self.bootstrap_resolver = bootstrap_resolver
        self.failed = False
        p = self.policy
        require(type(p.max_support_us) is int and 0 < p.max_support_us <= 3_000_000
            and type(p.max_lookup_age_us) is int and 0 < p.max_lookup_age_us <= 250_000
            and type(p.clock_error_us) is int and 0 <= p.clock_error_us < p.max_lookup_age_us
            and type(p.capacity) is int and 1 <= p.capacity <= 65536, "bounded canonical authority policy")
        _identifier(p.fixture_id)
        require(type(p.device_id) is str and re.fullmatch(r"[0-9a-f]{32}", p.device_id) is not None, "fixed device identity")
        require(u64(p.principal_id) > 0, "nonzero principal")
        _closed(p.pins, PIN_NAMES, "profile/model/source/schema pins")
        _closed(p.home_scope, HOME_SCOPE_FIELDS, "Home scope")
        _closed(p.room_binding, ROOM_BINDING_FIELDS, "room binding")
        _closed(p.provider_pins, PROVIDER_FIELDS, "provider pins")
        for value in p.home_scope.values():
            _identifier(value)
        for value in (*p.pins.values(), *p.provider_pins.values(),
                      p.room_binding["configuration_digest"], p.room_binding["manifest_digest"]):
            sha256(value)
        _identifier(p.room_binding["worker_version"])
        require(p.room_binding["room_output_schema"] == "home-live-room-output-1", "explicit current room output required")
        self.issuers = {}
        for expected, issuer in zip(MODES, (p.ambient_issuer, p.task_issuer)):
            require(type(issuer) is CanonicalIssuerPolicy and issuer.mode == expected, "disjoint fixed issuer modes")
            _identifier(issuer.issuer_id)
            require(u64(issuer.issuer_epoch) > 0 and type(issuer.lookup_credential) is str
                    and 0 < len(issuer.lookup_credential) <= 4096, "fixed issuer epoch/lookup credential")
            self.issuers[expected] = issuer
        require((p.ambient_issuer.issuer_id, p.ambient_issuer.issuer_epoch)
                != (p.task_issuer.issuer_id, p.task_issuer.issuer_epoch)
                and p.ambient_issuer.lookup_credential != p.task_issuer.lookup_credential,
                "independent issuer namespaces and lookup credentials required")
        self.context_bytes = codec.encode_context(deepcopy(p.context))
        require(type(self.context_bytes) is bytes and len(self.context_bytes) == 323, "exact canonical context323 required")
        self.context_digest = hashlib.sha256(self.context_bytes).hexdigest()
        fixed = _frozen_body(binding, {"binding_sha256", "device_id", "context_digest", "context", "boot_generation", "session_id", "ownership_generation"}, "binding")
        require(fixed["context"] == self.context_bytes and fixed["context_digest"] == bytes.fromhex(self.context_digest), "fixed native context differs")
        require(type(fixed["device_id"]) is bytes and len(fixed["device_id"]) == 16 and any(fixed["device_id"])
                and fixed["device_id"].hex() == p.device_id,
                "fixed native device identity differs")
        sha256(fixed["binding_sha256"])
        for key in ("boot_generation", "session_id", "ownership_generation"):
            require(type(fixed[key]) is int and 0 < fixed[key] <= MAX_U64
                    and p.context[key] == str(fixed[key]), "fixed native binding generation mismatch")
        require(all(p.context["pins"][key] == p.pins[key] for key in WIRE_PINS), "context/profile pins differ")
        marker = {"schema": "home-fixture-canonical-authority-policy-1", "version": 1,
            "fixture_id": p.fixture_id, "device_id": p.device_id, "context_digest": self.context_digest,
            "binding_sha256": fixed["binding_sha256"], "pins": p.pins, "principal_id": p.principal_id,
            "home_scope": p.home_scope, "room_binding": p.room_binding, "provider_pins": p.provider_pins,
            "issuers": [{"mode": i.mode, "issuer_id": i.issuer_id, "issuer_epoch": i.issuer_epoch,
                         "lookup_credential_sha256": hashlib.sha256(i.lookup_credential.encode()).hexdigest()}
                        for i in self.issuers.values()], "max_support_us": p.max_support_us,
            "max_lookup_age_us": p.max_lookup_age_us, "clock_error_us": p.clock_error_us, "capacity": p.capacity}
        # Namespace is fixed per fixture/device; changed epochs/pins do not
        # silently select another empty table and discard outstanding effects.
        self.namespace = _digest({"fixture_id": p.fixture_id, "device_id": p.device_id})
        self.policy_digest = _digest(marker)
        with journal.lock:
            exists = journal.db.execute("SELECT name FROM sqlite_master WHERE name='fixture_canonical_meta'").fetchone()
            require(exists is not None or create is True, "canonical issuer absent; explicit commissioning required")
            if exists is None:
                journal.db.execute("CREATE TABLE fixture_canonical_meta(namespace TEXT PRIMARY KEY, policy TEXT NOT NULL, blocked INTEGER NOT NULL, last_clock_home_us TEXT NOT NULL)")
                journal.db.execute("CREATE TABLE fixture_canonical_grants(namespace TEXT NOT NULL, request_id TEXT NOT NULL, grant_id TEXT NOT NULL, input_sha TEXT NOT NULL, body TEXT NOT NULL, receipt TEXT NOT NULL, revoked INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(namespace,request_id), UNIQUE(namespace,grant_id))")
            row = journal.db.execute("SELECT policy FROM fixture_canonical_meta WHERE namespace=?", (self.namespace,)).fetchone()
            if row is None:
                require(create is True, "fixed canonical issuer absent; no automatic epoch creation")
                journal.db.execute("INSERT INTO fixture_canonical_meta VALUES (?,?,0,'0')", (self.namespace, record_bytes(marker).decode()))
            else:
                require(row[0] == record_bytes(marker).decode(), "canonical issuer context/policy changed; no migration")

    def describe(self):
        return {"schema": "home-fixture-canonical-authority-1", "version": 1, "shared_journal": True,
                "modes": list(MODES), "policy_sha256": self.policy_digest, "physical_permission": False,
                "id_allocator_implemented": False, "transport_implemented": False,
                "archive_router_and_ordinary_replay_implemented": False}

    def _ready(self):
        require(not self.failed and not self.journal._recorder_failed, "canonical authority unavailable/incomplete")
        require(callable(self.journal._recorder), "recorder READY required before canonical admission")
        row = self.journal.db.execute("SELECT blocked FROM fixture_canonical_meta WHERE namespace=?", (self.namespace,)).fetchone()
        require(row is not None and row[0] == 0, "canonical durable effect unconfirmed; explicit recovery required")

    def _block(self):
        self.journal.db.execute("UPDATE fixture_canonical_meta SET blocked=1 WHERE namespace=?", (self.namespace,))

    def _now(self):
        now = self.clock()
        require(type(now) is int and 0 < now <= MAX_U64 - self.policy.clock_error_us, "qualified integer Home clock required")
        last = self.journal.db.execute("SELECT last_clock_home_us FROM fixture_canonical_meta WHERE namespace=?", (self.namespace,)).fetchone()
        if now < int(last[0]):
            self._block()
            raise FixtureAuthorityDenied("canonical Home clock rollback; explicit recovery required")
        self.journal.db.execute("UPDATE fixture_canonical_meta SET last_clock_home_us=? WHERE namespace=?", (str(now), self.namespace))
        return now

    def _state(self, mode):
        state = deepcopy(self.current_resolver(mode))
        require(type(state) is CanonicalAuthorityState and state.eligible is True,
                "typed current committed room authority unavailable; JSON is not proof")
        require(state.home_scope == self.policy.home_scope, "current Home scope changed")
        _closed(state.room_commit_ref, ROOM_REF_FIELDS, "current room commit reference")
        require(all(state.room_commit_ref[k] == self.policy.room_binding[k] for k in ROOM_BINDING_FIELDS), "current room binding changed")
        for key in ("commit_id", "room_output_revision_id"):
            _identifier(state.room_commit_ref[key])
        for key in ("commit_body_sha256", "decision_sha256", "room_output_sha256"):
            sha256(state.room_commit_ref[key])
        for value in (state.source_generation, state.authority_generation, state.evidence_observed_home_us):
            u64(value)
        for value in (state.source_cut, state.protection, state.authority):
            sha256(value)
        _closed(state.caps, CAP_FIELDS, "original support caps")
        for value in state.caps.values():
            u64(value)
        return self._native_original(state, mode)

    def _native_original(self, state, mode):
        ref = _frozen_body(state.current_ref, CURRENT_FIELDS, "current native reference")
        for key in ("binding_sha256", "snapshot_sha256", "capabilities_sha256", "context_digest"):
            sha256(ref[key])
        require(ref["binding_sha256"] == self.binding.binding_sha256 and ref["context_digest"] == self.context_digest
                and ref["evidence_source"] == "simulated", "native reference binding/source mismatch")
        for key in ("boot_generation", "session_id", "ownership_generation"):
            require(type(ref[key]) is int and ref[key] == getattr(self.binding, key), "native reference generation changed")
        for key in ("state_generation", "revision_id", "captured_device_us", "device_now_lower_us", "device_now_upper_us", "maximum_status_age_us"):
            require(type(ref[key]) is int and 0 <= ref[key] <= MAX_U64, "native reference integer")
        require(type(ref["domain_generations"]) is tuple and len(ref["domain_generations"]) == 8
                and all(type(x) is int and 0 <= x < 2**32 for x in ref["domain_generations"]), "all eight native domain generations required")
        lower, upper, captured = ref["device_now_lower_us"], ref["device_now_upper_us"], ref["captured_device_us"]
        require(0 < ref["maximum_status_age_us"] <= 5_000_000 and lower <= upper
                and upper - lower <= 2 * self.policy.clock_error_us and captured <= upper
                and upper - captured < min(ref["maximum_status_age_us"], self.policy.max_lookup_age_us), "native original stale/future/clock bound")
        require(type(state.raw_status_v3) is bytes and len(state.raw_status_v3) == 998
                and type(state.raw_capabilities_v2) is bytes and len(state.raw_capabilities_v2) == 404,
                "original native STATUS3/CAP2 required")
        require(hashlib.sha256(state.raw_status_v3).hexdigest() == ref["snapshot_sha256"]
                and hashlib.sha256(state.raw_capabilities_v2).hexdigest() == ref["capabilities_sha256"], "native original digest mismatch")
        status = self.decode_status(state.raw_status_v3, binding=self.binding, captured_device_us=captured)
        cap = self.decode_capabilities(state.raw_capabilities_v2, {k: self.policy.pins[k] for k in WIRE_PINS})
        require(type(status) is dict and status.get("schema") == "c12-runtime-decoded-snapshot-1"
                and type(status.get("document_version")) is int and status["document_version"] == 3
                and status.get("original_document_hex") == state.raw_status_v3.hex()
                and status.get("original_document_sha256") == ref["snapshot_sha256"]
                and status.get("captured_device_us") == str(captured), "decoded STATUS does not match original")
        for key in ("boot_generation", "session_id", "ownership_generation", "state_generation", "revision_id"):
            require(status.get(key) == str(ref[key]), "decoded native generation differs from current reference")
        pins = {k: self.policy.pins[k] for k in WIRE_PINS}
        require(type(cap) is dict and cap.get("schema") == "c12-runtime-capabilities-1"
                and type(cap.get("document_version")) is int and cap["document_version"] == 2
                and cap.get("pins") == pins and status.get("pins") == pins
                and status.get("context_digest") == self.context_digest
                and tuple(x["generation"] for x in status.get("domains", [])) == ref["domain_generations"]
                and status.get("session_open") is True and status.get("thermal_latched") is False
                and status.get("simulated") is True and status.get("physical_permission") is False
                and cap.get("physical_permission") is False and status.get("phase") not in ("travel", "fault", "inhibited", "unconfirmed"),
                "current native context/session/capability refused")
        required_mask = 0 if mode is None else 0x09 if mode == "autonomous-ambient" else 0xBE
        require(type(cap.get("qualified_mask")) is int and 0 <= cap["qualified_mask"] <= 255
                and cap["qualified_mask"] & required_mask == required_mask, "canonical model flags unavailable")
        ref["domain_generations"] = list(ref["domain_generations"])
        return state, status, cap, ref

    def bootstrap_zero_native(self):
        """Initialize only zero dispatch IDs; command admission still uses _state."""
        if self.bootstrap_resolver is None:
            return self._state('autonomous-ambient')
        original = deepcopy(self.bootstrap_resolver())
        require(type(original) is CanonicalNativeBootstrapOriginal,
                'typed native bootstrap original required')
        require(original.home_scope == self.policy.home_scope, 'bootstrap Home scope changed')
        _closed(original.room_commit_ref, ROOM_REF_FIELDS, 'bootstrap room commit reference')
        require(all(original.room_commit_ref[k] == self.policy.room_binding[k] for k in ROOM_BINDING_FIELDS),
                'bootstrap room binding changed')
        for key in ('commit_id', 'room_output_revision_id'):
            _identifier(original.room_commit_ref[key])
        for key in ('commit_body_sha256', 'decision_sha256', 'room_output_sha256'):
            sha256(original.room_commit_ref[key])
        require(u64(original.authority_generation) == self.journal.revocation_generation(
                    self.policy.home_scope['site_id'], self.policy.home_scope['room_id']),
                'bootstrap authority changed')
        now = self.clock()
        require(all(type(value) is int and 0 <= value <= MAX_U64 for value in
                    (now, original.sampled_home_us, original.qualified_until_home_us, original.clock_error_us))
                and original.clock_error_us == self.policy.clock_error_us
                and original.sampled_home_us <= now
                and now - original.sampled_home_us < self.policy.max_lookup_age_us
                and now + original.clock_error_us < original.qualified_until_home_us,
                'bootstrap clock original expired or changed')
        _, status, cap, ref = self._native_original(original, None)
        semantic = status.get('current_semantic_target', {})
        require(ref['revision_id'] == ref['state_generation'] == 0 and status.get('motion_id') == '0'
                and status.get('phase') == 'idle' and status.get('applied') == [0] * 24
                and semantic.get('ambient_level') == [0] * 7
                and semantic.get('spotlight_frame') == [0] * 3,
                'fresh zero native session required; no prior effects or STATUS resync')
        return original, status, cap, ref

    def _plan_scope(self, mode, plan):
        require(plan.get("manual") is False and plan.get("preset_id") == "0"
                and plan.get("manual_duration_minutes") == 0, "manual/preset authority unavailable")
        mask, fields = plan.get("domain_mask"), plan.get("field_mask")
        require(type(mask) is int and type(fields) is list and len(fields) == 8
                and all(type(x) is int and 0 <= x <= 15 for x in fields), "exact selected field masks required")
        if mode == "autonomous-ambient":
            require(plan.get("kind") == "LEVELS" and 0 < mask < 128 and fields[7] == 0
                and all((fields[i] in (1, 2, 3)) if mask & (1 << i) else fields[i] == 0 for i in range(7)),
                "ambient issuer only authorizes first-seven fixed LEVELS fields")
        else:
            require(mask == 128 and fields[:7] == [0] * 7 and fields[7] in (4, 8, 12)
                    and plan.get("kind") in ("MOVE", "LEVELS", "CONTINUE"), "task issuer only authorizes spotlight128")
            require(plan["kind"] != "MOVE" or fields[7] & 4, "task MOVE requires aim field")
            require(plan["kind"] != "LEVELS" or fields[7] == 8, "task LEVELS cannot change aim")

    def _validate(self, mode, p, state, status, cap, ref, now):
        self._plan_scope(mode, p)
        require(p["context_digest"] == self.context_digest
                and p["predecessor_id"] == str(ref["revision_id"])
                and p["expected_state_generation"] == str(ref["state_generation"])
                and p["domain_generations"] == ref["domain_generations"], "actual native predecessor/CAS/all8 domain binding mismatch")
        require(u64(p["revision_id"]) > ref["revision_id"], "new command revision must advance actual native revision")
        for key in ("source_cut", "protection", "authority"):
            require(p[key] == getattr(state, key), "current room causal binding changed")
        self._temporal(p, state, now)
        if mode == "autonomous-ambient":
            semantic = status["current_semantic_target"]
            require(p["motion_id"] == status["motion_id"] and all(p[key] == semantic[key] for key in
                ("target_centideg", "speed_deg_s", "motion_timeout_us", "start_deadline_device_us", "preset_id"))
                and p["frame"][21:24] == semantic["spotlight_frame"], "ambient command changed retained task/motion/spotlight")
        # The fixed validator must prove current committed sample mapping,
        # every original support dependency and full24 power/mixing OR joint
        # path/quantization/current-axis qualification. A digest is not proof.
        require(self.command_validator(deepcopy(p), deepcopy(state)) is True,
                "independent current room/component/native command validation refused")
        completed = self._now()
        self._temporal(p, state, completed)
        return completed

    def _temporal(self, p, state, now):
        issued, expiry = u64(p["issued_home_us"]), u64(p["support_expiry_home_us"])
        require(u64(state.evidence_observed_home_us) <= issued <= now and now + self.policy.clock_error_us < expiry
                and expiry - issued <= self.policy.max_support_us, "original current support timing refused")
        for key in CAP_FIELDS:
            require(p[key] == state.caps[key] and now + self.policy.clock_error_us < u64(p[key])
                    and expiry <= u64(p[key]), "original evidence/protection/authority/clock cap mismatch")

    @staticmethod
    def _same_originals(left, right):
        # A newly qualified current-clock bound may advance while the original
        # STATUS/CAP/receipt identity and its capture time remain immutable.
        # Do not mistake an advancing clock for a new observation or authority.
        left, right = deepcopy(left), deepcopy(right)
        for value in (left, right):
            value["current_reference"].pop("device_now_lower_us", None)
            value["current_reference"].pop("device_now_upper_us", None)
        return left == right

    def _original(self, state, ref):
        return {"schema": "home-fixture-canonical-command-originals-1", "version": 1,
            "home_scope": deepcopy(state.home_scope), "room_commit_ref": deepcopy(state.room_commit_ref),
            "provider_pins": deepcopy(self.policy.provider_pins), "current_reference": deepcopy(ref),
            "status3_hex": state.raw_status_v3.hex(), "capabilities2_hex": state.raw_capabilities_v2.hex()}

    def admit_ambient(self, request_id, reservation, plan):
        return self._admit("autonomous-ambient", request_id, reservation, plan)

    def admit_task_joint(self, request_id, reservation, plan):
        return self._admit("autonomous-task", request_id, reservation, plan)

    def _admit(self, mode, request_id, reservation, plan):
        _identifier(request_id)
        p, r = deepcopy(plan), deepcopy(reservation)
        require(type(p) is dict and type(r) is dict, "detached plan and trusted reservation reference required")
        self._plan_scope(mode, p)
        issuer = self.issuers[mode]
        require(r.get("request_id") == request_id and r.get("mode") == mode
                and r.get("grant_id") == p.get("grant_id") and r.get("revision_id") == p.get("revision_id")
                and u64(p["grant_id"]) > 0 and u64(p["revision_id"]) > 0, "fixed reservation identity/mode mismatch")
        sha256(r.get("intent_sha"))
        require(p.get("grant_body_sha256") == "0" * 64, "unsigned grant placeholder required")
        original_wire = self.codec.encode_plan(deepcopy(p))
        require(type(original_wire) is bytes and len(original_wire) == 409, "exact canonical plan409 required")
        input_sha = _digest({"mode": mode, "plan": p, "reservation": r})
        with self.journal.lock:
            self._ready()
            old = self.journal.db.execute("SELECT input_sha,receipt FROM fixture_canonical_grants WHERE namespace=? AND request_id=?", (self.namespace, request_id)).fetchone()
            if old is not None:
                require(hmac.compare_digest(old[0], input_sha), "changed immutable canonical grant retry")
                return json.loads(old[1])
            require(self.journal.db.execute("SELECT COUNT(*) FROM fixture_canonical_grants WHERE namespace=?", (self.namespace,)).fetchone()[0] < self.policy.capacity,
                    "canonical grant capacity exhausted; no pruning/reset")
            checked = self.reservation_reader(p["grant_id"], p["revision_id"])
            require(type(checked) is dict and checked.get("state") == "reserved" and all(checked.get(key) == r.get(key)
                for key in ("request_id", "grant_id", "revision_id", "mode", "intent_sha")), "reservation not independently current/unconsumed")
            state, status, cap, ref = self._state(mode)
            now = self._now()
            completed = self._validate(mode, p, state, status, cap, ref, now)
            content = b"C12-CONTENT-V1" + original_wire[:-40]
            # Preserve the pinned G1 issuer's timestamp meaning: these are
            # creation/eligibility times, not durable DB commit, recorder ACK
            # or consumer availability. The namespace remains blocked through
            # both archive ACKs. The dispatcher must separately retain its
            # actual post-ACK first receipt time and pass ready/current lookup
            # before dispatch; neither retries nor recording latency retime G1.
            g = {"schema": "home-fixture-execution-grant-1", "version": 1, "issuer_id": issuer.issuer_id,
                "issuer_epoch": issuer.issuer_epoch, "grant_id": p["grant_id"], "mode": mode,
                "principal_id": self.policy.principal_id, "session_principal_id": self.policy.context["principal_id"],
                "fixture_id": self.policy.fixture_id, "device_id": self.policy.device_id, "context_digest": self.context_digest,
                "pins": deepcopy(self.policy.pins), "command_content_hex": content.hex(),
                "command_content_sha256": hashlib.sha256(content).hexdigest(),
                **{key: deepcopy(p[key]) for key in PROJECTION}, "source_generation": state.source_generation,
                "authority_generation": state.authority_generation, "ownership_generation": self.policy.context["ownership_generation"],
                "evidence_observed_home_us": state.evidence_observed_home_us, "admitted_home_us": str(completed),
                "original_available_home_us": str(completed), "caps": deepcopy(state.caps)}
            raw = canonical_bytes(g)
            p["grant_body_sha256"] = hashlib.sha256(raw).hexdigest()
            wire = self.codec.encode_plan(deepcopy(p))
            receipt = {"schema": "home-fixture-canonical-admission-1", "version": 1,
                "grant_body": g, "grant_body_sha256": p["grant_body_sha256"], "plan": p,
                "wire_hex": wire.hex(), "wire_sha256": hashlib.sha256(wire).hexdigest(),
                "context_hex": self.context_bytes.hex(), "reservation": r,
                "decision_ref": _digest(state.room_commit_ref), "originals": self._original(state, ref),
                "policy_sha256": self.policy_digest, "physical_permission": False}
            tx_id = str(uuid.uuid4())
            try:
                self._block()
                self.journal._recorder({"kind": "fixture_canonical_grant_prepare", "data": {
                    "tx_id": tx_id, "namespace": self.namespace, "request_id": request_id,
                    "input_sha256": input_sha, "receipt": deepcopy(receipt)}})
                require(not self.failed and not self.journal._recorder_failed, "session failed during canonical preparation")
                # Re-resolve after durable retention, before granting any effect.
                newer, newer_status, newer_cap, newer_ref = self._state(mode)
                require(self._same_originals(self._original(newer, newer_ref), receipt["originals"])
                        and newer.caps == state.caps and newer.source_cut == state.source_cut
                        and newer.protection == state.protection and newer.authority == state.authority
                        and newer.source_generation == state.source_generation
                        and newer.authority_generation == state.authority_generation
                        and newer.evidence_observed_home_us == state.evidence_observed_home_us,
                        "current original/cut changed during canonical retention")
                self._validate(mode, p, newer, newer_status, newer_cap, newer_ref, self._now())
                self.journal.db.execute("BEGIN IMMEDIATE")
                self.journal.db.execute("INSERT INTO fixture_canonical_grants(namespace,request_id,grant_id,input_sha,body,receipt) VALUES (?,?,?,?,?,?)",
                    (self.namespace, request_id, p["grant_id"], input_sha, raw.decode(), record_bytes(receipt).decode()))
                self.journal.db.execute("COMMIT")
                self.journal._recorder({"kind": "fixture_canonical_grant_commit", "data": {
                    "tx_id": tx_id, "namespace": self.namespace, "request_id": request_id, "grant_body_sha256": p["grant_body_sha256"]}})
                require(not self.failed and not self.journal._recorder_failed, "session failed during canonical admission")
                self.journal.db.execute("UPDATE fixture_canonical_meta SET blocked=0 WHERE namespace=?", (self.namespace,))
            except BaseException:
                if self.journal.db.in_transaction:
                    self.journal.db.execute("ROLLBACK")
                self.failed = self.journal._recorder_failed = True
                raise
            return deepcopy(receipt)

    def _row(self, grant_id):
        u64(grant_id)
        row = self.journal.db.execute("SELECT body,receipt,revoked FROM fixture_canonical_grants WHERE namespace=? AND grant_id=?", (self.namespace, grant_id)).fetchone()
        require(row is not None, "unknown original canonical grant")
        require(row[2] == 0, "canonical grant revoked")
        return json.loads(row[0]), json.loads(row[1])

    def _current(self, g, original):
        mode = g["mode"]
        require(mode in self.issuers, "unconfigured canonical issuer mode")
        state, status, cap, ref = self._state(mode)
        now = self._now()
        require(self._same_originals(self._original(state, ref), original["originals"]), "canonical grant superseded by native/current room original")
        require(g["source_generation"] == state.source_generation and g["authority_generation"] == state.authority_generation
                and g["ownership_generation"] == self.policy.context["ownership_generation"]
                and g["caps"] == state.caps and g["evidence_observed_home_us"] == state.evidence_observed_home_us,
                "canonical grant original generation/caps changed")
        require(u64(g["original_available_home_us"]) <= now, "canonical original availability in future")
        self._validate(mode, original["plan"], state, status, cap, ref, now)
        final_now = self._now()
        require(final_now + self.policy.clock_error_us < min(u64(g["support_expiry_home_us"]), *(u64(v) for v in state.caps.values())),
                "canonical support expired during validation")
        return state, final_now

    def validate_admission(self, grant, reservation, plan, scope=None):
        with self.journal.lock:
            self._ready()
            g, original = self._row(grant.get("grant_id"))
            require(canonical_bytes(grant) == canonical_bytes(g) and record_bytes(plan) == record_bytes(original["plan"])
                    and record_bytes(reservation) == record_bytes(original["reservation"]), "original canonical grant/plan/reservation changed")
            if scope is not None:
                require(type(scope) is dict and all(scope.get(key) == value for key, value in {
                    "fixture_id": self.policy.fixture_id, "device_id": self.policy.device_id,
                    "context_digest": self.context_digest, "pins": self.policy.pins}.items()), "canonical dispatcher identity differs")
            self._current(g, original)
            return True

    def lookup(self, issuer_id, issuer_epoch, credential, grant_id, nonce):
        routes = [i for i in self.issuers.values() if i.issuer_id == issuer_id and i.issuer_epoch == str(issuer_epoch)]
        require(len(routes) == 1 and type(credential) is str and hmac.compare_digest(credential, routes[0].lookup_credential),
                "canonical lookup issuer/credential denied")
        require(type(nonce) is bytes and len(nonce) == 32 and any(nonce), "bounded canonical lookup challenge")
        identity = str(grant_id)
        with self.journal.lock:
            self._ready()
            g, original = self._row(identity)
            require(g["mode"] == routes[0].mode, "lookup credential cannot cross canonical issuer mode")
            state, now = self._current(g, original)
            until = min(now + self.policy.max_lookup_age_us - self.policy.clock_error_us,
                        u64(g["support_expiry_home_us"]), *(u64(v) for v in state.caps.values()))
            record = {"schema": "home-fixture-execution-grant-lookup-1", "version": 1,
                "grant_body": g, "grant_body_sha256": hashlib.sha256(canonical_bytes(g)).hexdigest(),
                "admitted": True, "durable": True, "current": True, "superseded": False, "revoked": False,
                "status_observed_home_us": str(now), "status_available_home_us": str(now), "status_not_after_home_us": str(until),
                "current_source_generation": state.source_generation, "current_authority_generation": state.authority_generation,
                "current_ownership_generation": self.policy.context["ownership_generation"],
                "current_domain_generations": list(state.current_ref.domain_generations),
                "current_source_cut": state.source_cut, "current_protection": state.protection, "current_authority": state.authority}
            return {"issuer_id": issuer_id, "issuer_epoch": int(routes[0].issuer_epoch), "request_nonce": nonce, "record": record}

    def revoke(self, grant_id, *, reason):
        _identifier(reason)
        with self.journal.lock:
            self._ready()
            self._row(grant_id)
            try:
                now = self._now()
                self._block()
                self.journal._recorder({"kind": "fixture_canonical_grant_revocation", "data": {
                    "namespace": self.namespace, "grant_id": grant_id, "reason": reason, "available_home_us": str(now)}})
                self.journal.db.execute("UPDATE fixture_canonical_grants SET revoked=1 WHERE namespace=? AND grant_id=?", (self.namespace, grant_id))
                require(not self.failed and not self.journal._recorder_failed, "session failed during canonical revocation")
                self.journal.db.execute("UPDATE fixture_canonical_meta SET blocked=0 WHERE namespace=?", (self.namespace,))
            except BaseException:
                self.failed = self.journal._recorder_failed = True
                raise
