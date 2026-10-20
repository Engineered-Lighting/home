"""One source-born production member and its actual Home grant dispatcher.

The source owner already performed real inhibited boot/session establishment.
This additive adapter consumes that one-shot cursor; it creates neither a new
session nor a grant, and never infers sequence or grant floors from STATUS.
Historical transport gates remain unchanged.
"""
from copy import deepcopy
import hmac
import threading
import weakref

from fixture_canonical_dispatch import CanonicalFixtureDispatcher, CanonicalSubmissionPort
from fixture_execution_validation import require
from room_canonical_adapter import RoomCanonicalAdapter, _SimulationClock
from room_production_canonical_provider import load_production_canonical_modules

_CLAIMS = weakref.WeakKeyDictionary()
_CLAIM_LOCK = threading.Lock()


def _claim_cursor(owner, index):
    with _CLAIM_LOCK:
        claimed = _CLAIMS.setdefault(owner, set())
        require(index not in claimed, 'production cursor already attached to an adapter')
        claimed.add(index)


class ProductionCanonicalAdapter(RoomCanonicalAdapter):
    """Existing packet/clock/original-observation algorithms, separate birth gate."""
    def __init__(self, owner, *, native_tuple, dispatcher, submit_credential,
                 observe_credential, maximum_status_age_us=250000):
        from room_production_canonical_owner import ProductionCanonicalOwner
        require(type(owner) is ProductionCanonicalOwner, 'actual production source owner required')
        owner.require_current(); owner.require_native_tuple(native_tuple)
        modules = load_production_canonical_modules(); m = modules.adapter
        require(owner.modules is modules and type(dispatcher) is CanonicalFixtureDispatcher
            and owner.source.journal is dispatcher.journal,
            'same source-owned production SDK and Journal dispatcher required')
        authority = dispatcher.authority
        binding, cursor, index = native_tuple.binding, native_tuple.session_cursor, native_tuple.fixture_index
        require(type(index) is int and 0 <= index < 4 and owner.native_bindings[index] == binding
            and native_tuple.provider_pins == modules.provider_pins
            and type(binding) is modules.zha.RuntimeBinding and binding == authority.binding
            and authority.codec is dispatcher.codec is modules.plan and authority.journal is dispatcher.journal,
            'exact production member binding and codec required')
        require(type(cursor) is m.SessionCursor and cursor.boot_generation == binding.boot_generation
            and cursor.session_id == binding.session_id and cursor.context_digest == binding.context_digest.hex()
            and cursor.last_sequence > 0 and cursor.last_grant_id == cursor.last_semantic_revision == 0,
            'fresh original production cursor required; no STATUS floor resync')
        require(type(maximum_status_age_us) is int and 0 < maximum_status_age_us <= 250000,
                'bounded production original status age required')
        require(type(submit_credential) is str and type(observe_credential) is str
            and 0 < len(submit_credential) <= 256 and 0 < len(observe_credential) <= 256
            and submit_credential != observe_credential, 'separate private production adapter credentials required')
        header = owner.plant.recorder.header; member = header['fixtures'][index]
        require({key: header['producer'][name] for key, name in (
                ('manifest_sha256','manifest_sha256'),('source_sha256','source_sha256'),
                ('executable_sha256','executable_sha256'),('configuration_sha256','config_sha256'))}
            == modules.provider_pins == authority.policy.provider_pins
            and member['fixture_id'] == authority.policy.fixture_id == owner.plant.fixture_ids[index]
            and member['device_id'] == binding.device_id.hex() == authority.policy.device_id
            and member['boot'] == str(binding.boot_generation)
            and binding.context == modules.plan.encode_context(authority.policy.context),
            'actual production header/context/member differs')
        with dispatcher.journal.lock:
            meta = dispatcher._meta()
            require(meta['last_grant'] == meta['last_revision'] == meta['dispatched_grant'] == '0'
                and dispatcher.port is None, 'fresh one-shot production submission port required')
        self.owner, self.native_tuple = owner, native_tuple
        self.modules, self.plant, self.index, self.dispatcher = modules, owner.plant, index, dispatcher
        self.binding, self.maximum_status_age_us = binding, maximum_status_age_us
        self._observe_credential = observe_credential
        self._clock = _SimulationClock(modules, owner.clock.sample)
        self._clock.sample(); self._clock_successor = None
        owner.require_current(); owner.require_native_tuple(native_tuple)
        # A constructor failure after this point must not let another adapter
        # reuse the consumed authenticated sequence, even with another ledger.
        _claim_cursor(owner, index)
        selected = self
        class Auth:
            def authenticate(self, credential):
                owner.require_current(); owner.require_native_tuple(native_tuple)
                require(type(credential) is str, 'private production credential required')
                if hmac.compare_digest(credential, submit_credential):
                    return m.Principal(int(authority.policy.principal_id), (authority.policy.fixture_id,),
                        ('autonomous-ambient','autonomous-task'), 255, (3,)*7+(12,))
                require(hmac.compare_digest(credential, observe_credential), 'production credential refused')
                return m.Principal(int(authority.policy.principal_id), (authority.policy.fixture_id,), (),
                    255, (0,)*8, can_observe=True)
        class Lookup:
            def lookup(self, issuer_id, epoch, credential, grant_id, nonce):
                owner.require_current(); owner.require_native_tuple(native_tuple)
                found = authority.lookup(issuer_id, epoch, credential, grant_id, nonce)
                return m.LookupReceipt(True, found['issuer_id'], found['issuer_epoch'],
                                       found['request_nonce'], found['record'])
        class Transport:
            evidence_source = 'simulated'
            def exchange(self, packet): return selected._exchange(packet)
        policy = m.AdapterPolicy(authority.policy.fixture_id, binding.device_id, authority.policy.context,
            authority.policy.pins, tuple(m.IssuerPolicy(i.mode, i.issuer_id, int(i.issuer_epoch), i.lookup_credential)
                for i in (authority.policy.ambient_issuer, authority.policy.task_issuer)))
        self.adapter = m.TrustedAdapter(policy=policy, key=binding.key, auth=Auth(), lookup=Lookup(),
            clock=self._clock, transport=Transport(), session_cursor=cursor, reservation_gate=dispatcher)
        self.port = CanonicalSubmissionPort(self.adapter, submit_credential,
            deepcopy(modules.provider_pins), binding.binding_sha256)
        owner.require_current(); owner.require_native_tuple(native_tuple)
        dispatcher.install_submission_port(self.port)

    def _exchange(self, packet):
        self.owner.require_current(); self.owner.require_native_tuple(self.native_tuple)
        result = super()._exchange(packet)
        self.owner.require_current(); self.owner.require_native_tuple(self.native_tuple)
        return result
