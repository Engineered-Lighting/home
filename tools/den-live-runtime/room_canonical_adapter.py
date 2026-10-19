"""Fixed simulated room transport for the pinned pure C12 TrustedAdapter.

No session bootstrap, discovery, scheduler, native process, device or model is
created here. The sole bootstrap owner hands over its exact consumed cursor.
The plant's existing recorder retains every exchanged byte before use.
"""
from __future__ import annotations
import builtins
from copy import deepcopy
from dataclasses import dataclass, field, replace
import hashlib
import hmac
import json
from pathlib import Path
import struct
import sys
import threading
from types import ModuleType

from fixture_canonical_dispatch import CanonicalFixtureDispatcher, CanonicalSubmissionPort
from fixture_execution_validation import require, MAX_U64
from room_canonical_plant import RoomCanonicalPlant
from room_canonical_plant_recording import RoomCanonicalRecorder
from room_canonical_provider import (WINDOWS_PROVIDER, MODULE_NAMES, SOURCE_PINS,
    verified_provider_export)

# Historical aliases remain the exact Windows fixture/test contract.
BUILD = WINDOWS_PROVIDER.build
MANIFEST_SHA256 = WINDOWS_PROVIDER.manifest_sha256
PLAN_SHA256 = dict(SOURCE_PINS)['c12_runtime_plan']
_MODULES = None
_LOAD_LOCK = threading.Lock()


@dataclass(frozen=True)
class CanonicalModules:
    plan: object
    wire: object
    zha: object
    adapter: object
    session: object
    outputs: object
    provider_pins: dict
    source_pins: dict


def load_canonical_modules():
    """Fixed immutable export only; no caller-selected imports or providers."""
    global _MODULES
    with _LOAD_LOCK:
        profile, manifest, originals = verified_provider_export()
        if _MODULES is not None:
            require(profile.matches(_MODULES.provider_pins) and _MODULES.source_pins == dict(SOURCE_PINS),
                'cached canonical provider differs from platform')
            return _MODULES
        imported = {}
        def fixed_import(name, globals=None, locals=None, fromlist=(), level=0):
            if level == 0 and name.startswith('c12_'):
                require(name in imported, 'canonical dependency outside fixed closure')
                return imported[name]
            return builtins.__import__(name, globals, locals, fromlist, level)
        for name in MODULE_NAMES:
            filename, data = originals[name]
            unique = '_home_room_canonical_' + profile.manifest_sha256[:16] + '_' + name
            require(unique not in sys.modules, 'canonical isolated module identity already occupied')
            module = ModuleType(unique); module.__file__ = str(filename)
            module.__dict__['__builtins__'] = {**vars(builtins), '__import__': fixed_import}
            sys.modules[unique] = module
            try: exec(compile(data, str(filename), 'exec'), module.__dict__)
            except BaseException:
                sys.modules.pop(unique, None); raise
            imported[name] = module
        _MODULES = CanonicalModules(*(imported[n] for n in MODULE_NAMES), profile.provider_pins, dict(SOURCE_PINS))
        return _MODULES


@dataclass(frozen=True)
class SimulatedClockObservation:
    now_home_us: int
    now_device_us: int
    observed_home_us: int
    uncertainty_us: int
    qualified_until_home_us: int
    mapping_revision: int
    device_now_lower_us: int
    device_now_upper_us: int


@dataclass(frozen=True)
class CanonicalRoomObservation:
    current_ref: object
    raw_status_v3: bytes = field(repr=False)
    raw_capabilities_v2: bytes = field(repr=False)
    clock_mapping_revision: int


@dataclass(frozen=True)
class SimulatedClockSuccessor:
    """Retained same-cursor mapping change; never renews a grant or horizon."""
    native_binding_sha256: str
    previous_mapping_revision: int
    mapping_revision: int
    observed_home_us: int
    qualified_until_home_us: int
    uncertainty_us: int
    query_packet_sha256: str
    install_packet_sha256: str
    grant_caps_renewed: bool = False


class _SimulationClock:
    def __init__(self, modules, observer):
        require(callable(observer), 'fixed simulated clock observer required')
        self.modules, self.observer = modules, observer
        self.last = None; self.original = None
        self.base_original = None; self.successor = None
        self.lock = threading.Lock()

    def sample(self):
        with self.lock: return self._sample()

    def _sample(self):
        v = self.observer()
        require(type(v) is SimulatedClockObservation and all(type(getattr(v, k)) is int
            and 0 <= getattr(v, k) <= MAX_U64 for k in v.__dataclass_fields__), 'closed simulated clock observation required')
        require(v.mapping_revision > 0 and v.observed_home_us <= v.now_home_us
            and v.now_home_us + v.uncertainty_us < v.qualified_until_home_us
            and v.device_now_lower_us <= v.now_device_us <= v.device_now_upper_us,
            'bounded original simulated clock required')
        raw_original = (v.observed_home_us, v.qualified_until_home_us, v.mapping_revision, v.uncertainty_us)
        require(self.base_original is None or raw_original == self.base_original,
                'clock renewal requires explicit successor')
        self.base_original = raw_original
        if self.successor is not None:
            v = replace(v, observed_home_us=self.successor.observed_home_us,
                mapping_revision=self.successor.mapping_revision)
        original = (v.observed_home_us, v.qualified_until_home_us, v.mapping_revision, v.uncertainty_us)
        require(self.original is None or original == self.original, 'clock renewal requires explicit successor')
        require(self.last is None or v.now_home_us >= self.last.now_home_us and v.now_device_us >= self.last.now_device_us,
                'simulated clock rollback')
        self.last, self.original = v, original
        return v

    def promote(self, receipt):
        with self.lock:
            current = self._sample()
            require(type(receipt) is SimulatedClockSuccessor
                and receipt.previous_mapping_revision == current.mapping_revision
                and receipt.mapping_revision == current.mapping_revision + 1
                and current.observed_home_us <= receipt.observed_home_us <= current.now_home_us
                and receipt.qualified_until_home_us == current.qualified_until_home_us
                and receipt.uncertainty_us == current.uncertainty_us
                and receipt.grant_caps_renewed is False, 'bounded explicit simulated successor required')
            self.successor = receipt
            self.original = (receipt.observed_home_us, receipt.qualified_until_home_us,
                receipt.mapping_revision, receipt.uncertainty_us)
            return self._sample()

    def view(self):
        v = self.sample()
        # This typed qualification is exclusively the explicit simulated
        # protocol input. It conveys no measured physical clock qualification.
        return self.modules.adapter.ClockView(v.now_home_us, v.now_device_us, v.observed_home_us,
            v.uncertainty_us, v.qualified_until_home_us, v.mapping_revision, True)


class RoomCanonicalAdapter:
    """One member, one inherited packet cursor, one same-Journal dispatcher.

    Bootstrap must already establish the actual canonical session and retain
    its initial STATUS/CAP originals. No floors are inferred from STATUS here.
    Authority/current-room composition and native tick/CAN observations remain
    the caller's existing explicit owners.
    """
    def __init__(self, plant, *, index, dispatcher, binding, session_cursor, clock_observer,
                 submit_credential, observe_credential, maximum_status_age_us=250000):
        modules = load_canonical_modules(); m = modules.adapter
        require(type(plant) is RoomCanonicalPlant and type(index) is int and 0 <= index < 4
            and type(dispatcher) is CanonicalFixtureDispatcher, 'actual fixed room/dispatcher required')
        authority = dispatcher.authority
        require(authority.journal is dispatcher.journal and authority.codec is modules.plan and dispatcher.codec is modules.plan,
                'same Journal and pinned canonical codec required')
        require(type(binding) is modules.zha.RuntimeBinding and binding == authority.binding
            and type(session_cursor) is m.SessionCursor, 'actual pinned binding and sole-owner cursor required')
        require(session_cursor.boot_generation == binding.boot_generation and session_cursor.session_id == binding.session_id
            and session_cursor.context_digest == binding.context_digest.hex()
            and session_cursor.last_sequence > 0
            and session_cursor.last_grant_id == 0 and session_cursor.last_semantic_revision == 0,
            'fresh bootstrap cursor required; no STATUS floor resync')
        require(type(maximum_status_age_us) is int and 0 < maximum_status_age_us <= 250000,
                'bounded current original status age required')
        require(type(submit_credential) is str and type(observe_credential) is str
            and 0 < len(submit_credential) <= 256 and 0 < len(observe_credential) <= 256
            and submit_credential != observe_credential, 'separate fixed private submit/read credentials required')
        header = plant.recorder.header
        member = header['fixtures'][index]
        require(type(plant.recorder) is RoomCanonicalRecorder and plant.recorder.ready is True and plant._started and not plant._closing
            and plant._terminal is None and plant._receipt is None
            and {key: header['producer'][name] for key,name in (
                ('manifest_sha256','manifest_sha256'),('source_sha256','source_sha256'),
                ('executable_sha256','executable_sha256'),('configuration_sha256','config_sha256'))} == modules.provider_pins
            and member['fixture_id'] == authority.policy.fixture_id == plant.fixture_ids[index]
            and member['device_id'] == binding.device_id.hex() == authority.policy.device_id
            and member['boot'] == str(binding.boot_generation)
            and authority.policy.provider_pins == modules.provider_pins,
            'actual active room member/provider binding differs')
        require(binding.context == modules.plan.encode_context(authority.policy.context), 'exact canonical context required')
        with dispatcher.journal.lock:
            meta = dispatcher._meta()
            require(meta['last_grant'] == meta['last_revision'] == meta['dispatched_grant'] == '0'
                    and dispatcher.port is None, 'one-shot fresh canonical port installation required')
        self.modules, self.plant, self.index, self.dispatcher = modules, plant, index, dispatcher
        self.binding, self.maximum_status_age_us = binding, maximum_status_age_us
        self._observe_credential = observe_credential
        self._clock = _SimulationClock(modules, clock_observer)
        self._clock.sample()
        self._clock_successor = None
        owner = self
        class Auth:
            def authenticate(self, credential):
                require(type(credential) is str, 'private adapter credential required')
                if hmac.compare_digest(credential, submit_credential):
                    return m.Principal(int(authority.policy.principal_id), (authority.policy.fixture_id,),
                        ('autonomous-ambient', 'autonomous-task'), 255, (3,) * 7 + (12,))
                require(hmac.compare_digest(credential, observe_credential), 'adapter credential refused')
                return m.Principal(int(authority.policy.principal_id), (authority.policy.fixture_id,), (),
                    255, (0,) * 8, can_observe=True)
        class Lookup:
            def lookup(self, issuer_id, epoch, credential, grant_id, nonce):
                found = authority.lookup(issuer_id, epoch, credential, grant_id, nonce)
                return m.LookupReceipt(True, found['issuer_id'], found['issuer_epoch'], found['request_nonce'], found['record'])
        class Transport:
            evidence_source = 'simulated'
            def exchange(self, packet): return owner._exchange(packet)
        policy = m.AdapterPolicy(authority.policy.fixture_id, binding.device_id, authority.policy.context,
            authority.policy.pins, tuple(m.IssuerPolicy(i.mode, i.issuer_id, int(i.issuer_epoch), i.lookup_credential)
                for i in (authority.policy.ambient_issuer, authority.policy.task_issuer)))
        self.adapter = m.TrustedAdapter(policy=policy, key=binding.key, auth=Auth(), lookup=Lookup(),
            clock=self._clock, transport=Transport(), session_cursor=session_cursor, reservation_gate=dispatcher)
        self.port = CanonicalSubmissionPort(self.adapter, submit_credential, deepcopy(modules.provider_pins), binding.binding_sha256)
        dispatcher.install_submission_port(self.port)

    @property
    def last_clock_successor(self):
        return self._clock_successor

    def clock_observation(self):
        """Local bounded read; no packet, process or Journal call."""
        return self._clock.sample()

    def refresh_simulated_clock(self):
        """Explicit fixed-owner operation for a bounded fictional clock only.

        The same SDK cursor authenticates both TIME operations. The original
        qualification horizon and source/grant caps cannot be extended. This
        supplies no physical clock qualification or request-selected authority.
        """
        with self.adapter._lock:
            require(not self.adapter._uncertain and not self.adapter._travel_attempted,
                    'healthy existing simulated session required')
            try:
                before = self._clock.sample()
                require(before.uncertainty_us <= 50_000
                    and before.device_now_lower_us == before.now_device_us == before.device_now_upper_us
                    and before.now_home_us - before.observed_home_us <= 5_000_000
                    and before.mapping_revision < 0xffffffff,
                    'current exact simulated mapping required before successor')
                operation = self.modules.wire.Operation.TIME_EXCHANGE
                code, body = self.adapter._exchange(operation,
                    self.modules.wire.clock_query_body(before.now_home_us))
                require(code is self.modules.wire.Code.OK and len(body) == 26 and body[1] == 0,
                        'retained simulated clock query refused')
                echoed, ingress, built = struct.unpack('<3Q', body[2:])
                after = self._clock.sample()
                require(echoed == before.now_home_us
                    and before.now_device_us <= ingress <= built <= after.device_now_upper_us
                    and after.now_home_us - before.now_home_us == after.now_device_us - before.now_device_us,
                    'exact simulated clock correlation changed')
                query = self.plant.recorder.validated_response(self.index)
                query_sha = hashlib.sha256(bytes.fromhex(query['packet_hex'])).hexdigest()
                anchor = before.now_home_us + built - before.now_device_us
                revision = before.mapping_revision + 1
                code, body = self.adapter._exchange(operation,
                    self.modules.wire.clock_install_body(revision, built, anchor, before.uncertainty_us, 0))
                require(code is self.modules.wire.Code.ADMITTED and body == bytes((int(code), 0, 0)),
                        'retained simulated clock successor not admitted')
                installed = self.plant.recorder.validated_response(self.index)
                receipt = SimulatedClockSuccessor(self.binding.binding_sha256,
                    before.mapping_revision, revision, anchor, before.qualified_until_home_us, before.uncertainty_us,
                    query_sha, hashlib.sha256(bytes.fromhex(installed['packet_hex'])).hexdigest())
                self._clock.promote(receipt)
                self._clock_successor = receipt
                return receipt
            except BaseException:
                # A lost ACK may mean the mapping changed. Never resubmit or
                # continue commands using the former mapping after uncertainty.
                self.adapter._uncertain = True
                raise

    def _exchange(self, packet):
        require(type(packet) is bytes and 0 < len(packet) <= 64, 'bounded canonical wire packet required')
        before = self._clock.sample()
        result = self.plant.step(self.index, 'exchange', at_device_us=str(before.now_device_us),
            ingress_device_us=str(before.now_device_us), packet_hex=packet.hex())
        require(result == self.plant.recorder.validated_response(self.index) and result.get('op') == 'exchange'
            and result.get('ok') is True and result.get('physical_permission') is False
            and result.get('physical_truth') is False and result.get('evidence_source') == 'simulated',
            'original retained canonical exchange required')
        after = self._clock.sample()
        require(before.now_device_us <= int(result['processed_device_us']) <= after.device_now_upper_us,
                'canonical original response exceeds simulated clock bound')
        reply = result.get('reply_hex')
        require(type(reply) is str and 0 < len(reply) <= 128 and len(reply) % 2 == 0,
                'original canonical wire reply required')
        raw = bytes.fromhex(reply)
        require(raw.hex() == reply, 'canonical reply bytes changed')
        return raw

    def decode_status(self, raw, *, binding, captured_device_us):
        require(binding == self.binding, 'fixed decoder binding required')
        return self.modules.zha.decode_snapshot_v3(raw, binding=binding, captured_device_us=captured_device_us)

    def decode_capabilities(self, raw, pins):
        require(pins == self.dispatcher.authority.policy.context['pins'], 'fixed capabilities pins required')
        return self.modules.zha.decode_capabilities_v2(raw, pins)

    def observe(self):
        # Actual SDK owns STATUS paging and the sole authenticated sequence.
        snapshot = self.adapter.observe(self._observe_credential)
        raw_status = bytes.fromhex(snapshot['document_hex'])
        require(len(raw_status) == 998 and hashlib.sha256(raw_status).hexdigest() == snapshot['document_sha256'],
                'exact original STATUS3 required')
        sid = offset = 0; captured = total = None; parts = []
        with self.adapter._lock:
            self.adapter._principal(self._observe_credential)
            for _ in range(32):
                code, body = self.adapter._exchange(self.modules.wire.Operation.CAPABILITIES,
                    self.modules.wire.status_body(sid, offset, 15))
                require(code is self.modules.wire.Code.OK and len(body) >= 18, 'original CAP2 page unavailable')
                page_id, stamp, length, returned, count = struct.unpack('<IQHHB', body[1:18])
                require(page_id > 0 and length == 404 and 0 < count <= 15 and len(body) == count + 18
                    and returned == offset and offset + count <= length, 'exact original CAP2 coverage required')
                if not sid: sid, captured, total = page_id, stamp, length
                require((page_id, stamp, length) == (sid, captured, total), 'CAP2 original lineage changed')
                parts.append(body[18:]); offset += count
                if offset == total: break
            else: require(False, 'bounded CAP2 page count exceeded')
        raw_cap = b''.join(parts)
        status = self.decode_status(raw_status, binding=self.binding, captured_device_us=int(snapshot['captured_device_us']))
        self.decode_capabilities(raw_cap, self.dispatcher.authority.policy.context['pins'])
        clock = self._clock.sample()
        stamp = int(snapshot['captured_device_us'])
        require(stamp <= clock.device_now_lower_us and clock.device_now_upper_us - stamp < self.maximum_status_age_us,
                'original STATUS3 unavailable through current bound')
        reference = self.modules.outputs.CurrentStateReference(self.binding.binding_sha256,
            hashlib.sha256(raw_status).hexdigest(), hashlib.sha256(raw_cap).hexdigest(), self.binding.boot_generation,
            self.binding.session_id, self.binding.ownership_generation, self.binding.context_digest.hex(),
            int(status['state_generation']), int(status['revision_id']), tuple(d['generation'] for d in status['domains']),
            stamp, clock.device_now_lower_us, clock.device_now_upper_us, self.maximum_status_age_us, 'simulated')
        return CanonicalRoomObservation(reference, raw_status, raw_cap, clock.mapping_revision)
