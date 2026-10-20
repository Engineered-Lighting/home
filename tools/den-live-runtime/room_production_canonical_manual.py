"""Independent, explicitly authorized manual software path on the owned plant.

The HA envelopes below are fictional in-memory SDK inputs. They call no HA,
network, serial or hardware factory. App replies are exact retained native
bytes. The real producer SDK issues activation/clock capabilities itself;
this module never manufactures those seals or converts automatic authority.
"""
from collections import deque
from copy import deepcopy
from decimal import Decimal, ROUND_FLOOR
import hashlib
import json

from room_canonical_adapter import CanonicalRoomObservation
from room_production_canonical_adapter import _claim_cursor
from room_production_canonical_provider import load_production_manual_modules, require


class _CanonicalMemoryBus:
    """Closed, finite simulated HA port for the actual typed SDK transport."""
    ORIGIN = 'https://production-canonical.invalid'

    def __init__(self, owner, index, authorization, *, sample_guard):
        self.owner, self.index, self.authorization = owner, index, authorization
        self.modules = owner.modules; self.binding = owner.native_bindings[index]
        self.queue = deque(); self.closed = False; self.connected = False; self.subscription = None
        self._sample_guard = sample_guard
        self.protective = False; self._protective_attempted = False

    def current(self):
        self.owner.manual_io_current(self.authorization,self.index,protective=self.protective)
        require(not self.closed, 'manual memory bus closed')

    def now(self):
        require(not self.closed, 'manual memory bus closed')
        return self.owner.manual_io_clock_sample(self.authorization,self.index,
            protective=self.protective).now_home_us*1000

    def connect(self, url, *, timeout):
        self.current()
        require(not self.connected and url == self.ORIGIN.replace('https://','wss://')+'/api/websocket'
            and 0 < timeout <= 5, 'single fixed manual memory connection required')
        self.connected = True
        self.queue.append(dict(type='auth_required', ha_version='2026.10.0'))
        return self

    def send(self, raw):
        self.current(); require(type(raw) is str and len(raw) <= 65536, 'bounded manual memory command')
        value = self.modules.zha.strict_json(raw); kind = value.get('type')
        if kind == 'auth':
            require(value == dict(type='auth', access_token=self.authorization.credential),
                    'fixed manual memory authentication input')
            response = dict(type='auth_ok', ha_version='2026.10.0')
        else:
            identifier = value.get('id'); require(type(identifier) is int and identifier > 0, 'manual memory command identity')
            if kind == 'zha/device' and value.get('ieee') == self.binding.coordinator_ieee:
                result = dict(ieee=self.binding.coordinator_ieee, active_coordinator=True)
            elif kind == 'zha/device' and value.get('ieee') == self.binding.device_ieee:
                result = dict(ieee=self.binding.device_ieee, manufacturer='EngineeredLight',
                    model='gimbal-c12-runtime-candidate', device_reg_id=self.binding.ha_device_id)
            elif kind == 'zha/devices/clusters/commands':
                require(value == dict(id=identifier, type=kind, ieee=self.binding.device_ieee,
                    endpoint_id=18, cluster_id=0xFC12, cluster_type='in'), 'fixed manual memory command schema')
                result = [dict(id=0,type='server',name='runtime_packet_once',schema=[dict(name='packet')])]
            elif kind == 'subscribe_events':
                require(value == dict(id=identifier,type=kind,event_type='zha_event') and self.subscription is None,
                        'single fixed manual memory subscription')
                self.subscription = identifier; result = None
            else: raise ValueError('unknown manual memory command')
            response = dict(id=identifier,type='result',success=True,result=result)
        require(len(self.queue) < 8, 'manual memory response queue bound')
        self.queue.append(response)

    def recv(self, *, timeout):
        self.current(); require(type(timeout) in (int,float) and 0 <= timeout <= 5, 'manual memory receive bound')
        if not self.queue: raise TimeoutError('no manual memory event')
        return json.dumps(self.queue.popleft(),sort_keys=True,separators=(',',':'))

    def post(self, url, token, payload, *, timeout):
        self.current()
        require(url == self.ORIGIN+'/api/services/zha/issue_zigbee_cluster_command'
            and token == self.authorization.credential and 0 < timeout <= 5
            and type(payload) is dict and set(payload) == {'ieee','endpoint_id','cluster_id','cluster_type',
                'command','command_type','manufacturer','params'}
            and payload['ieee'] == self.binding.device_ieee and payload['endpoint_id'] == 18
            and payload['cluster_id'] == 0xFC12 and payload['cluster_type'] == 'in'
            and payload['command'] == 0 and payload['command_type'] == 'server' and payload['manufacturer'] == -1
            and type(payload['params']) is dict and set(payload['params']) == {'packet'},
            'fixed manual memory packet route required')
        data = payload['params']['packet']
        require(type(data) is list and 30 <= len(data) <= 63 and all(type(x) is int and 0 <= x <= 255 for x in data),
                'bounded exact manual memory packet')
        packet = bytes([len(data)])+bytes(data)  # The SDK strips only the original LVBytes length.
        command = self.modules.wire.inspect_packet(packet)
        # The exact Home publication remains current while this one bounded
        # native packet is attempted. The existing Journal owns this lock;
        # neither the approval nor a translation can replace that owner.
        with self.owner.source.journal.lock, self.owner.manual_io_boundary(
                self.authorization,self.index,protective=self.protective,packet=packet):
            self.current()
            before = self.owner.manual_io_clock_sample(self.authorization,self.index,protective=self.protective)
            if self.protective:
                require(not self._protective_attempted and command.operation is self.modules.wire.Operation.WITHDRAW
                    and command.body == self.modules.wire.withdraw_body(255,3,True),
                    'only one exact independently authorized protective Travel packet allowed')
                self._protective_attempted = True
            elif command.operation not in (self.modules.wire.Operation.SESSION_OPEN,self.modules.wire.Operation.TIME_EXCHANGE,
                                         self.modules.wire.Operation.STATUS,self.modules.wire.Operation.CAPABILITIES):
                self._sample_guard(required=True)
            if self.protective:
                response = self.owner.protective_exchange(self.authorization,self.index,packet)
            else:
                response = self.owner.plant.step(self.index,'exchange', at_device_us=str(before.now_device_us),
                    ingress_device_us=str(before.now_device_us),packet_hex=packet.hex())
        require(response == self.owner.plant.recorder.validated_response(self.index)
            and response.get('ok') is True and response.get('op') == 'exchange'
            and response.get('physical_permission') is False and response.get('physical_truth') is False
            and response.get('evidence_source') == 'simulated', 'exact retained manual native app response required')
        self.current()
        after = self.owner.manual_io_clock_sample(self.authorization,self.index,protective=self.protective)
        require(before.now_device_us <= int(response['processed_device_us']) <= after.device_now_upper_us,
                'manual native response outside original clock bounds')
        reply = response.get('reply_hex')
        require(type(reply) is str and 62 <= len(reply) <= 128 and bytes.fromhex(reply).hex() == reply,
                'bounded exact original manual app reply')
        require(self.subscription is not None and len(self.queue) < 8, 'manual memory subscription/queue required')
        args = dict(schema='c12-runtime-ZHA-reply-1',version=1,packet_hex=reply,
            callback_sha256=self.binding.quirk_sha256, coordinator_ieee=self.binding.coordinator_ieee,
            source_ieee=self.binding.device_ieee,source_endpoint=18,profile_id=0x0104,frame_control=0x19,
            command_id=0,zcl_tsn=0,route_attribution='zigpy_cluster_instance_not_raw_APS')
        self.queue.append(dict(type='event',id=self.subscription,event=dict(event_type='zha_event',data=dict(
            device_ieee=self.binding.device_ieee,device_id=self.binding.ha_device_id,endpoint_id=18,
            cluster_id=0xFC12,command='c12_runtime_reply',args=args))))

    def close(self):
        self.closed = True; self.queue.clear()


class ProductionManualBridge:
    """One independently authorized ledger/cursor; automatic grants stay separate."""
    def __init__(self, owner, *, fixture_index, authorization=None):
        from room_production_canonical_owner import ProductionCanonicalOwner
        require(type(owner) is ProductionCanonicalOwner and type(fixture_index) is int and 0 <= fixture_index < 4,
                'actual production manual source owner and fixture required')
        owner.require_context()
        self.owner, self.index, self.authorization = owner, fixture_index, authorization
        self.modules = load_production_manual_modules(); self.started = self.attempted = self.closed = False
        self.transport = self.bootstrap = self.service = self.authority = self.producer = self.native_tuple = None
        self._active_sample = None; self._active_plan = None; self._terminal = False
        self._bus = None; self._stop_attempted = False; self._stop_receipt = None
        if authorization is not None: owner.require_manual_authorization(authorization, fixture_index)

    def _current(self):
        require(not self.closed and not self._terminal and self.authorization is not None,
                'independent manual authorization unavailable or terminal')
        self.owner.require_context(); self.owner.require_manual_authorization(self.authorization, self.index)

    def start(self):
        self._current(); require(not self.attempted, 'manual bootstrap single attempt')
        self.attempted = True; cap = self.authorization; core = self.modules.sdk
        binding = self.owner.native_bindings[self.index]
        require(type(cap.policy) is core.adapter.AdapterPolicy and type(cap.principal) is core.adapter.Principal
            and type(cap.source) is self.modules.manual.ManualSource
            and type(cap.home_qualification) is core.session.HomeClockQualification
            and type(cap.clock_limits) is core.session.ClockLimits
            and cap.home_qualification.provenance == cap.clock_limits.provenance == 'synthetic'
            and binding.policy_sha256 == core.zha.adapter_policy_sha256(cap.policy),
            'original typed independent manual policy and synthetic qualification required')
        # Independent ID ownership is exclusive. No adapter may share/restart
        # this native cursor, even if an activation/bootstrap result is lost.
        _claim_cursor(self.owner, self.index)
        ledger = self.modules.ledger.ExecutionLedger(cap.ledger_path,cap.ledger_scope,create=False)
        try:
            expected = self.modules.manual.manual_policy_sha256(policy=cap.policy,binding=binding,
                scope=cap.ledger_scope,principal=cap.principal,credential=cap.credential,
                source=cap.source,presets=cap.presets)
            row = ledger.db.execute('SELECT policy,revoked FROM c12_manual_meta WHERE id=1').fetchone()
            require(row is not None and row['policy'] == expected and row['revoked'] == 0
                and all(ledger.meta()[k] == '0' for k in ('last_grant','last_revision','dispatched_grant','terminal_revision'))
                and not ledger.meta()['blocked'], 'existing fresh original manual ledger required')
            bus = _CanonicalMemoryBus(self.owner,self.index,cap,sample_guard=self._require_sample)
            self._bus = bus
            configuration = core.zha.HaConfiguration(bus.ORIGIN,cap.credential,'2026.10.0',1000)
            self.transport = core.zha.RuntimeZhaTransport(binding,configuration,
                connect=bus.connect,post=bus.post,monotonic_ns=bus.now)
            activation = core.zha.OwnerActivation(cap.activation_approval_id,binding.binding_sha256,
                cap.valid_until_monotonic_ns)
            self.transport.activate(activation,
                lambda admission:self.owner.manual_approval_gate(cap,self.index,'activation',admission))
            self.bootstrap = core.session.BootstrapOwner(transport=self.transport,policy=cap.policy,key=binding.key,
                home_qualification=cap.home_qualification,clock_limits=cap.clock_limits,mapping_revision=1)
            admission = core.session.SessionAdmission(cap.session_approval_id,self.bootstrap.scope_sha256,
                cap.valid_until_monotonic_ns)
            self.bootstrap.run(admission,
                lambda approved:self.owner.manual_approval_gate(cap,self.index,'session',approved))
            require(type(self.bootstrap.clock) is core.session.QualifiedSessionClock,
                    'actual bootstrap-issued qualified session clock required')
            self.authority = self.modules.manual.ManualAuthority(policy=cap.policy,binding=binding,ledger=ledger,
                principal=cap.principal,credential=cap.credential,source=cap.source,
                clock=self.bootstrap.clock,presets=cap.presets,test_only=True)
            require(self.authority.policy_sha256 == expected, 'manual original policy changed during bootstrap')
            self.service = self.bootstrap.handoff(auth=self.authority,lookup=self.authority,reservation_gate=self.authority)
            self._service = self.service  # Exact ManualProducer client seam, not a replacement adapter.
            self.producer = self.modules.manual.ManualProducer(self.authority,credential=cap.credential)
            self.started = True
            self.observe()
            self.native_tuple = self.owner.complete_native_bootstrap(self.index,self.bootstrap,self.transport,
                service=self.service,observation=self._last_observation)
            self._current()
            return dict(status='ready',fixture_index=self.index,manual_ledger_scope=cap.ledger_scope.encoded(),
                clock=self.bootstrap.clock.receipt(),physical_permission=False,physical_truth=False)
        except BaseException:
            self._terminal = True
            if self.transport is not None:self.transport.close()
            raise
        finally: ledger.close()

    def observe(self):
        with self.owner.source.journal.lock:
            self._current(); self._require_sample()
            with self.owner.manual_read_boundary(self.authorization,self.index):
                result, observation = self._observe_original()
            # End verification must succeed before any CurrentState can be
            # cached, returned to the producer or adopted by the owner.
            self._last_observation = observation
            return result

    def _observe_original(self):
        require(self.started and self.service is not None, 'manual native session not ready')
        cap = self.authorization; core = self.modules.sdk
        original = self.service.observe(cap.credential); capabilities = self.service.capabilities(cap.credential)
        status = bytes.fromhex(original['document_hex']); raw_cap = bytes.fromhex(capabilities['document_hex'])
        require(len(status) == 998 and len(raw_cap) == 404, 'original installed STATUS3/CAP2 required')
        decoded = core.zha.decode_snapshot_v3(status,binding=self.owner.native_bindings[self.index],
            captured_device_us=int(original['captured_device_us']))
        limits = core.zha.decode_capabilities_v2(raw_cap,cap.policy.session_context['pins'])
        clock = self.bootstrap.clock; view = clock.view(); bounds = clock.device_bounds(); binding = self.transport.binding
        stamp = int(original['captured_device_us'])
        require(stamp <= bounds['upper_us'] and bounds['upper_us']-stamp < 250000,
                'manual original current snapshot expired')
        ref = core.outputs.CurrentStateReference(binding.binding_sha256,hashlib.sha256(status).hexdigest(),
            hashlib.sha256(raw_cap).hexdigest(),binding.boot_generation,binding.session_id,binding.ownership_generation,
            binding.context_digest.hex(),int(decoded['state_generation']),int(decoded['revision_id']),
            tuple(d['generation'] for d in decoded['domains']),stamp,bounds['lower_us'],bounds['upper_us'],250000,'simulated')
        observation = CanonicalRoomObservation(ref,status,raw_cap,view.mapping_revision)
        self._current()
        return (dict(source='original-production-runtime',evidence_source='simulated',original=deepcopy(original),
            original_capabilities=deepcopy(capabilities),decoded=decoded,capabilities=limits,physical_permission=False),
            observation)

    def _require_sample(self, *, required=False):
        if self._active_sample is None:
            require(not required, 'pinned current Home SAMPLE required before native output')
            return
        from room_production_spotlight_sample import TrustedCommittedSpotlightSample
        selected = self._active_sample
        with self.owner.source.journal.lock:
            self.owner.require_current()
            current = TrustedCommittedSpotlightSample(self.owner.source).require_current(selected)
            if self._active_plan is not None:
                lease = int((Decimal(str(current.valid_until))*1000000).to_integral_value(rounding=ROUND_FLOOR))
                require(int(self._active_plan['support_expiry_home_us']) <= lease,
                        'manual plan outlives original committed SAMPLE lease')

    def submit_plan(self, plan):
        """Guard the actual producer's immutable Plan409; never alter its caps."""
        require(type(plan) is dict and self._active_sample is not None, 'current pinned Home SAMPLE required')
        with self.owner.source.journal.lock:
            self._current(); self._active_plan = deepcopy(plan); self._require_sample()
            ledger = self.modules.ledger.ExecutionLedger(self.authorization.ledger_path,
                self.authorization.ledger_scope,create=False)
            try:
                row = ledger.db.execute('SELECT plan FROM c12_manual_grants WHERE request_id=?',
                    (self._active_sample.request_id,)).fetchone()
                require(row is not None and self.modules.sdk.zha.canonical(json.loads(row['plan']))
                    == self.modules.sdk.zha.canonical(plan), 'exact original SAMPLE manual grant/plan required')
            finally:ledger.close()
            # Exact producer authority verifies this same original grant and
            # ledger reservation at prepare and commit. The packet port also
            # rechecks the frozen publication under this Journal lock.
            return self.service.submit(self.authorization.credential,dict(plan=plan))

    def translate_and_dispatch(self, fixture_id, *, request_id):
        from room_production_spotlight_sample import TrustedCommittedSpotlightSample
        translation = TrustedCommittedSpotlightSample(self.owner.source).translate(fixture_id,request_id=request_id)
        result = deepcopy(translation.translation)
        if result['production_request'] is None:
            return dict(status='unmet',translation=result,dispatched=False,physical_permission=False)
        if self.authorization is None:
            return dict(status='unavailable',reason='independent_manual_authorization_unavailable',
                translation=result,dispatched=False,physical_permission=False)
        self._current(); require(self.started and fixture_id == self.authorization.policy.fixture_id,
                                'same ready manually authorized fixture required')
        # Re-read the exact original committed SAMPLE immediately before grant
        # issuance; a previously returned translation is never a capability.
        with self.owner.source.journal.lock:
            self._active_sample = translation; self._active_plan = None
            self._current(); self._require_sample()
            try:
                plan = self.producer.produce(result['production_request'],self)
                self._current(); receipt = self.submit_plan(plan)
                self._current()
            except BaseException:
                # The original ledger reservation/grant is retained, including
                # a lease refusal after issuance. No retry/reallocation follows.
                self._terminal = True
                raise
        if receipt.get('finalization_complete') is True and receipt.get('device_disposition') in ('ADMITTED','DUPLICATE'):
            self._active_sample = self._active_plan = None
        return dict(status='submitted',translation=result,plan=plan,adapter_receipt=receipt,
                    dispatched=True,physical_permission=False,physical_truth=False)

    def close(self):
        self.closed = True
        if self.transport is not None:self.transport.close()

    def protective_stop(self):
        """One genuine SDK Travel attempt; no SAMPLE lease or grant is renewed."""
        if self._stop_receipt is not None:return deepcopy(self._stop_receipt)
        if self._stop_attempted:
            return dict(status='incomplete',reason='protective_stop_outcome_unconfirmed',retry=False,
                        physical_permission=False,physical_rest_confirmed=False)
        self._stop_attempted = True
        if not self.started or self.service is None:
            self._stop_receipt = dict(status='incomplete' if self.attempted else 'not_started',
                reason='manual_bootstrap_unconfirmed' if self.attempted else None,
                native_session_started=False,native_owner_close_required=True,retry=False,
                physical_permission=False,physical_rest_confirmed=False)
            return deepcopy(self._stop_receipt)
        acknowledged = revoked = False; actual = None; reason = None
        try:
            self.owner.protective_context(self.authorization,self.index)
            require(self.authorization.principal.can_travel is True
                and self.authorization.principal.domain_mask == 255,'independent global protective authority required')
            self._active_sample = self._active_plan = None
            self._bus.protective = True
            actual = self.service.travel(self.authorization.credential)
            acknowledged = actual.get('device_disposition') in ('OK','ADMITTED','DUPLICATE')
            require(acknowledged and actual.get('domain_mask') == 255 and actual.get('reason') == 'travel',
                    'original protective Travel acknowledgement required')
        except BaseException as exc:
            reason = type(exc).__name__  # Do not print exception text containing private inputs.
        finally:
            self._terminal = True
            try:
                ledger = self.modules.ledger.ExecutionLedger(self.authorization.ledger_path,
                    self.authorization.ledger_scope,create=False)
                try:
                    with ledger.tx():
                        row = ledger.db.execute('SELECT policy FROM c12_manual_meta WHERE id=1').fetchone()
                        require(row is not None and row['policy'] == self.authority.policy_sha256,
                                'original manual policy required for revocation')
                        ledger.db.execute('UPDATE c12_manual_meta SET revoked=1 WHERE id=1')
                    revoked = ledger.db.execute('SELECT revoked FROM c12_manual_meta WHERE id=1').fetchone()[0] == 1
                finally:ledger.close()
            except BaseException as exc:
                reason = reason or type(exc).__name__
            self.close()
        self._stop_receipt = dict(status='protective_stop_acknowledged' if acknowledged and revoked else 'incomplete',
            acknowledgement=actual,manual_authority_revoked=revoked,reason=reason,retry=False,
            native_owner_close_required=True,physical_permission=False,physical_truth=False,physical_rest_confirmed=False)
        return deepcopy(self._stop_receipt)
