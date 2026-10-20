"""Actual SDK activation/bootstrap with authored signed device I/O only.

No source qualification, native executable, process, network or physical I/O.
The owner/device are explicit unit ports; actual Home/native joins are separate.
"""
from copy import deepcopy
from contextlib import contextmanager
from dataclasses import replace
import hashlib
from pathlib import Path
import struct
from types import SimpleNamespace
import threading
import tempfile
import unittest
from unittest.mock import patch
import subprocess
import uuid

import room_production_canonical_manual as bridge
from room_canonical_adapter import SimulatedClockObservation
from room_production_canonical_provider import load_production_manual_modules


class AuthoredOwner:
    def __init__(self):
        self.mm=load_production_manual_modules();self.modules=m=self.mm.sdk
        self.now_home=1100000;self.device=100000;self.replies=[];self.context_checks=0;self.output_guards=0
        context=m.plan.example_context()
        self.policy=m.adapter.AdapterPolicy('unit-fixture',bytes(range(16)),context,
            {**context['pins'],**{n:hashlib.sha256(n.encode()).hexdigest() for n in ('model','home_source','home_schema')}},
            (m.adapter.IssuerPolicy('manual-spotlight','authored-spotlight',1,'unit-lookup-credential-only'),))
        b=m.zha.RuntimeBinding('00:11:22:33:44:55:66:77','00:11:22:33:44:55:66:80','01'*16,
            bytes(range(16)),bytes(range(32)),1,2,4,m.plan.encode_context(context),
            m.zha.adapter_policy_sha256(self.policy),'bb'*32)
        self.native_bindings=(b,);self.source=SimpleNamespace(journal=SimpleNamespace(lock=threading.RLock()))
        self.clock=SimpleNamespace(sample=lambda:SimulatedClockObservation(self.now_home,self.device,1100000,0,4100000,1,self.device,self.device))
        self.cap=SimpleNamespace(credential='unit-private-manual-credential',valid_until_monotonic_ns=4100000000)
        self.home=m.session.HomeClockQualification('authored-unit-clock','a1'*32,1100000000,1100000,0,0,4100000000,'synthetic')
        self.limits=m.session.ClockLimits('a2'*32,b.device_id.hex(),1,0,250000,50000,3000000,1000000,4100000000,0,'synthetic')
        self.original=None;self.corrupt=False;self.calls=[]
        self.output_expired=False;self.travel_reject=False;self.quiesced=False;self.protective_calls=0
        self.boundary_calls=0;self.original_changed=False
        self.read_scope=False;self.read_boundaries=0;self.read_pages=0;self.documents={};self.mutate_read_end=False
        self.plant=SimpleNamespace(step=self.step,recorder=SimpleNamespace(validated_response=lambda i:deepcopy(self.original)))
    def require_context(self):
        self.context_checks+=1
        if self.quiesced:raise ValueError('authored ordinary acquisition stopped')
    def require_current(self):raise AssertionError('bootstrap cannot require an accepted Home commit')
    def require_manual_authorization(self,cap,index,*,protective=False):
        if cap is not self.cap or index!=0 or self.monotonic_ns()>=cap.valid_until_monotonic_ns:raise ValueError('authored approval unavailable')
        if not protective and self.output_expired:raise ValueError('authored output lease expired')
    def monotonic_ns(self):return self.now_home*1000
    def protective_context(self,cap,index):
        self.require_manual_authorization(cap,index,protective=True)
        if cap.principal.can_travel is not True or cap.principal.domain_mask!=255:raise ValueError('authored protective authority unavailable')
    def protective_clock_sample(self,cap,index):
        self.protective_context(cap,index);return self.clock.sample()
    def protective_monotonic_ns(self,cap,index):
        self.protective_context(cap,index);return self.monotonic_ns()
    def protective_exchange(self,cap,index,packet):
        self.protective_context(cap,index);b=self.native_bindings[index];m=self.modules
        request=m.wire.open_packet(b.key,b.device_id,b.boot_generation,b.context_digest,m.wire.Direction.REQUEST,packet)
        if self.protective_calls or request.operation is not m.wire.Operation.WITHDRAW or request.body!=m.wire.withdraw_body(255,3,True):raise ValueError('authored exact protective packet required')
        self.protective_calls+=1
        return self.step(index,'exchange',at_device_us=str(self.device),ingress_device_us=str(self.device),packet_hex=packet.hex())
    def manual_io_current(self,cap,index,*,protective=False):
        self.require_manual_authorization(cap,index,protective=protective)
        if protective:self.protective_context(cap,index)
        elif self.quiesced:raise ValueError('authored ordinary acquisition stopped')
    def manual_io_clock_sample(self,cap,index,*,protective=False):
        self.manual_io_current(cap,index,protective=protective);return self.clock.sample()
    @contextmanager
    def manual_io_boundary(self,cap,index,*,protective=False,packet=None):
        if self.read_scope:
            m=self.modules;b=self.native_bindings[index]
            request=m.wire.open_packet(b.key,b.device_id,b.boot_generation,b.context_digest,m.wire.Direction.REQUEST,packet)
            if protective or index!=0 or request.operation not in (m.wire.Operation.STATUS,m.wire.Operation.CAPABILITIES):raise ValueError('authored read scope rejects nonread or foreign fixture')
            if self.read_pages>=94:raise ValueError('authored paired read page cap')
            self.read_pages+=1
            self.manual_io_current(cap,index)
            yield
            self.manual_io_current(cap,index)
            return
        if protective:self.protective_context(cap,index)
        else:self.require_context();self.require_manual_authorization(cap,index)
        if self.original_changed:raise ValueError('authored exact retained original changed')
        self.boundary_calls+=1
        try:yield
        finally:self.manual_io_current(cap,index,protective=protective)
    @contextmanager
    def manual_read_boundary(self,cap,index):
        self.require_context();self.require_manual_authorization(cap,index)
        if self.read_scope or self.original_changed:raise ValueError('authored read original changed')
        self.read_scope=True;self.read_boundaries+=1;self.read_pages=0
        try:
            yield
            self.require_context();self.require_manual_authorization(cap,index)
            if self.original_changed:raise ValueError('authored read original changed at end')
        finally:self.read_scope=False
    def sample_guard(self,*,required=False):
        self.output_guards+=1
        if required:raise ValueError('unit has no committed Home SAMPLE')
    def step(self,index,op,**fields):
        assert self.source.journal.lock._is_owned(),'packet must be fenced under actual Journal lock'
        m=self.modules;b=self.native_bindings[0];raw=bytes.fromhex(fields['packet_hex']);header=m.wire.inspect_packet(raw)
        context=m.wire.BOOTSTRAP_CONTEXT if header.operation is m.wire.Operation.SESSION_OPEN else b.context_digest
        request=m.wire.open_packet(b.key,b.device_id,b.boot_generation,context,m.wire.Direction.REQUEST,raw)
        self.calls.append(request)
        body=bytes([int(m.wire.Code.OK),0,0])
        if request.operation is m.wire.Operation.WITHDRAW and self.travel_reject:body=bytes([int(m.wire.Code.REJECTED),0,0])
        if request.operation is m.wire.Operation.TIME_EXCHANGE:
            if request.body[0]==0:
                sent=struct.unpack('<Q',request.body[1:])[0];body=bytes([0,0])+struct.pack('<3Q',sent,self.device,self.device)
            else:body=bytes([int(m.wire.Code.ADMITTED),0,0])
        if request.operation in self.documents:
            document=self.documents[request.operation];snapshot,offset,count=struct.unpack('<IHB',request.body)
            part=document[offset:offset+count]
            body=bytes([0])+struct.pack('<IQHHB',snapshot or int(request.operation),self.device,len(document),offset,len(part))+part
            if self.mutate_read_end and request.operation is m.wire.Operation.CAPABILITIES and offset+len(part)==len(document):self.original_changed=True
        reply=m.wire.seal_packet(b.key,b.device_id,b.boot_generation,context,m.wire.Direction.RESPONSE,
            request.operation,request.session,request.sequence,body)
        if self.corrupt:reply=reply[:-1]+bytes([reply[-1]^1])
        self.original=dict(op='exchange',ok=True,evidence_source='simulated',physical_permission=False,
            physical_truth=False,processed_device_us=str(self.device),reply_hex=reply.hex())
        return deepcopy(self.original)


class ProductionManualPortTests(unittest.TestCase):
    def setUp(self):
        self.spawn=patch.object(subprocess,'Popen',side_effect=AssertionError('native forbidden'));self.spawn.start();self.addCleanup(self.spawn.stop)
        self.run=patch.object(subprocess,'run',side_effect=AssertionError('external execution forbidden'));self.run.start();self.addCleanup(self.run.stop)
        self.owner=AuthoredOwner();self.m=self.owner.modules
    def start(self):
        o=self.owner;m=self.m;b=o.native_bindings[0]
        bus=bridge._CanonicalMemoryBus(o,0,o.cap,sample_guard=o.sample_guard)
        transport=m.zha.RuntimeZhaTransport(b,m.zha.HaConfiguration(bus.ORIGIN,o.cap.credential,'2026.10.0',1000),
            connect=bus.connect,post=bus.post,monotonic_ns=bus.now)
        self.addCleanup(transport.close)
        approvals=[]
        def activate(value):
            self.assertFalse(bus.connected);approvals.append(value);return True
        transport.activate(m.zha.OwnerActivation(str(uuid.uuid4()),b.binding_sha256,o.cap.valid_until_monotonic_ns),activate)
        bootstrap=m.session.BootstrapOwner(transport=transport,policy=o.policy,key=b.key,
            home_qualification=o.home,clock_limits=o.limits,mapping_revision=1)
        bootstrap.run(m.session.SessionAdmission(str(uuid.uuid4()),bootstrap.scope_sha256,o.cap.valid_until_monotonic_ns),
                      lambda value:approvals.append(value) is None)
        return bus,transport,bootstrap,approvals
    def test_actual_typed_sdk_activation_and_bootstrap_issue_their_own_clock(self):
        bus,transport,bootstrap,approvals=self.start()
        self.assertEqual(len(approvals),2)
        self.assertIs(type(transport.activation_proof),self.m.zha.ZhaActivationProof)
        self.assertIs(type(bootstrap.clock),self.m.session.QualifiedSessionClock)
        self.assertEqual(bootstrap.clock.receipt()['evidence_source'],'simulated')
        self.assertEqual(transport.evidence_source,'simulated')
        self.assertFalse(self.m.zha.verified_activation(transport,transport.activation_proof,policy=self.owner.policy,key=transport.binding.key))
        self.assertEqual([r.sequence for r in self.owner.calls],list(range(1,len(self.owner.calls)+1)))
        self.assertTrue(bootstrap._complete);self.assertFalse(bootstrap._transferred)
        self.assertEqual(self.owner.output_guards,0)
    def test_actual_clock_handoff_is_single_use_without_new_session(self):
        _,transport,bootstrap,_=self.start();count=len(self.owner.calls)
        service=bootstrap.handoff(auth=object(),lookup=object(),reservation_gate=object())
        self.assertIs(type(service),self.m.adapter.TrustedAdapter)
        self.assertIs(service._clock,bootstrap.clock);self.assertIs(bootstrap.clock._adapter,service)
        self.assertEqual(service._sequence,bootstrap._sequence)
        with self.assertRaisesRegex(ValueError,'one_time_session_handoff'):bootstrap.handoff(auth=object(),lookup=object(),reservation_gate=object())
        self.assertEqual(len(self.owner.calls),count)
    def test_output_packet_refuses_without_original_home_sample_before_native_port(self):
        _,transport,bootstrap,_=self.start();b=transport.binding;count=len(self.owner.calls)
        packet=self.m.wire.seal_packet(b.key,b.device_id,b.boot_generation,b.context_digest,self.m.wire.Direction.REQUEST,
            self.m.wire.Operation.PLAN_COMMIT,b.session_id,bootstrap._sequence+1,b'1'*32)
        with self.assertRaisesRegex(ValueError,'uncertain_HA_dispatch_terminal_no_retry'):transport.exchange(packet)
        self.assertEqual(len(self.owner.calls),count);self.assertFalse(transport.active)
        self.assertEqual(self.owner.output_guards,1)
    def test_corrupt_authenticated_reply_is_terminal_without_retry(self):
        self.owner.corrupt=True
        with self.assertRaisesRegex(ValueError,'uncertain_HA_dispatch_terminal_no_retry'):self.start()
        self.assertEqual(len(self.owner.calls),1)
    def test_one_actual_sdk_packet_has_one_full_admission_not_clock_reverification(self):
        _,transport,bootstrap,_=self.start();o=self.owner;m=self.m;b=transport.binding
        before=o.context_checks;packets=len(o.calls);boundaries=o.boundary_calls
        packet=m.wire.seal_packet(b.key,b.device_id,b.boot_generation,b.context_digest,m.wire.Direction.REQUEST,
            m.wire.Operation.STATUS,b.session_id,bootstrap._sequence+1,m.wire.status_body())
        transport.exchange(packet)
        self.assertEqual(len(o.calls),packets+1)
        self.assertEqual(o.boundary_calls,boundaries+1)
        self.assertLessEqual(o.context_checks-before,2,'full original verification belongs to actual packet/admission, not repeated SDK clock primitives')
    def test_each_actual_sdk_packet_rechecks_original_boundary_and_refuses_changed_original(self):
        _,transport,bootstrap,_=self.start();o=self.owner;m=self.m;b=transport.binding
        def packet(sequence):
            return m.wire.seal_packet(b.key,b.device_id,b.boot_generation,b.context_digest,m.wire.Direction.REQUEST,
                m.wire.Operation.STATUS,b.session_id,sequence,m.wire.status_body())
        transport.exchange(packet(bootstrap._sequence+1));before=len(o.calls)
        o.original_changed=True
        with self.assertRaisesRegex(ValueError,'uncertain_HA_dispatch_terminal_no_retry'):
            transport.exchange(packet(bootstrap._sequence+2))
        self.assertEqual(len(o.calls),before);self.assertFalse(transport.active)
    def test_actual_clock_original_expiry_refuses_and_does_not_extend(self):
        _,_,bootstrap,_=self.start();deadline=bootstrap.clock._deadline
        self.owner.now_home=(deadline+999)//1000
        with self.assertRaises(ValueError):bootstrap.clock.view()
        self.assertEqual(bootstrap.clock._deadline,deadline)

    def test_bus_clock_reads_validate_once_and_closed_bus_never_reads_owner_clock(self):
        o=self.owner;bus=bridge._CanonicalMemoryBus(o,0,o.cap,sample_guard=o.sample_guard)
        current=o.manual_io_current;calls=[]
        def counted(*args,**kwargs):calls.append('current');return current(*args,**kwargs)
        o.manual_io_current=counted
        self.assertEqual(bus.now(),o.now_home*1000)
        self.assertEqual(calls,['current'])
        bus.close();calls.clear()
        with self.assertRaisesRegex(ValueError,'manual memory bus closed'):bus.now()
        self.assertEqual(calls,[])

    def manual_stop_case(self):
        bus,transport,bootstrap,_=self.start();o=self.owner;m=self.m
        principal=m.adapter.Principal(3,('unit-fixture',),('manual-spotlight',),255,(0,)*7+(8,),
                                      can_observe=True,can_travel=True)
        class Auth:
            def authenticate(self,credential):
                if credential!=o.cap.credential:raise ValueError('unit credential')
                return principal
        service=bootstrap.handoff(auth=Auth(),lookup=object(),reservation_gate=object())
        directory=tempfile.TemporaryDirectory(prefix='authored-manual-stop-');self.addCleanup(directory.cleanup)
        ledger_path=Path(directory.name)/'manual.sqlite';b=transport.binding
        scope=o.mm.ledger.Scope(b.device_id.hex(),str(b.boot_generation),str(b.session_id),b.context_digest.hex(),'a4'*32)
        ledger=o.mm.ledger.ExecutionLedger(ledger_path,scope,create=True)
        o.mm.manual.initialize_manual_authority(ledger,'a3'*32);ledger.close()
        o.cap.principal=principal;o.cap.ledger_path=ledger_path;o.cap.ledger_scope=scope
        selected=bridge.ProductionManualBridge.__new__(bridge.ProductionManualBridge)
        selected.owner=o;selected.index=0;selected.authorization=o.cap;selected.modules=o.mm
        selected.started=True;selected.closed=False;selected.service=service;selected.transport=transport
        selected._bus=bus;selected._stop_attempted=False;selected._stop_receipt=None;selected._terminal=False;selected.bootstrap=bootstrap
        selected._active_sample=object();selected._active_plan={'original':'retained-unit-only'}
        selected.authority=SimpleNamespace(policy_sha256='a3'*32)
        return selected

    def assert_revoked(self):
        o=self.owner;ledger=o.mm.ledger.ExecutionLedger(o.cap.ledger_path,o.cap.ledger_scope,create=False)
        try:self.assertEqual(ledger.db.execute('SELECT revoked FROM c12_manual_meta WHERE id=1').fetchone()[0],1)
        finally:ledger.close()

    def test_genuine_sdk_travel_after_output_expiry_revokes_actual_ledger_once(self):
        selected=self.manual_stop_case();self.owner.output_expired=True;self.owner.quiesced=True;before=len(self.owner.calls)
        result=selected.protective_stop()
        self.assertEqual(result['status'],'protective_stop_acknowledged',result)
        self.assertEqual(len(self.owner.calls),before+1)
        self.assertEqual(self.owner.protective_calls,1)
        self.assertIs(self.owner.calls[-1].operation,self.m.wire.Operation.WITHDRAW)
        self.assertEqual(self.owner.calls[-1].body,self.m.wire.withdraw_body(255,3,True))
        self.assertFalse(result['physical_rest_confirmed']);self.assertTrue(selected.closed)
        self.assert_revoked();self.assertEqual(selected.protective_stop(),result)
        self.assertEqual(len(self.owner.calls),before+1)

    def test_negative_travel_ack_stays_incomplete_and_never_retries(self):
        selected=self.manual_stop_case();self.owner.output_expired=True;self.owner.travel_reject=True
        before=len(self.owner.calls);result=selected.protective_stop()
        self.assertEqual(result['status'],'incomplete');self.assertIsNone(result['acknowledgement'])
        self.assert_revoked();self.assertEqual(selected.protective_stop(),result)
        self.assertEqual(len(self.owner.calls),before+1)

    def test_protective_phase_rejects_even_authenticated_nonprotective_packet(self):
        selected=self.manual_stop_case();self.owner.output_expired=True
        selected.protective_stop();self.assertEqual(selected._bus._protective_attempted,True)
        # A new bus-phase exchange would be terminal; the actual transport is
        # already closed, so no CAP/PLAN/ordinary-withdraw can escape later.
        b=selected.transport.binding;before=len(self.owner.calls)
        raw=self.m.wire.seal_packet(b.key,b.device_id,b.boot_generation,b.context_digest,self.m.wire.Direction.REQUEST,
            self.m.wire.Operation.CAPABILITIES,b.session_id,selected.service._sequence+1,self.m.wire.status_body())
        with self.assertRaises(ValueError):selected.transport.exchange(raw)
        self.assertEqual(len(self.owner.calls),before)

    def test_original_sample_lease_refuses_before_normal_submit(self):
        selected=self.manual_stop_case()
        selected._active_sample=SimpleNamespace(request_id=str(uuid.uuid4()))
        selected._active_plan={'support_expiry_home_us':'1100001'}
        consumer=SimpleNamespace(require_current=lambda value:SimpleNamespace(valid_until=1.1))
        with patch.object(self.owner,'require_current'), \
                patch('room_production_spotlight_sample.TrustedCommittedSpotlightSample',return_value=consumer):
            with self.assertRaisesRegex(ValueError,'outlives original committed SAMPLE lease'):
                selected._require_sample(required=True)
        self.assertEqual(selected.service._grant_floor,0)

    def test_submit_preserves_original_ledger_plan_and_actual_sdk_request_envelope(self):
        selected=self.manual_stop_case();o=self.owner;identifier=str(uuid.uuid4())
        plan={'support_expiry_home_us':'1200000','retained':'authored-API-envelope-only'}
        ledger=o.mm.ledger.ExecutionLedger(o.cap.ledger_path,o.cap.ledger_scope,create=False)
        try:
            with ledger.tx():ledger.db.execute('INSERT INTO c12_manual_grants VALUES(?,?,?,?,?,?)',
                (identifier,'1','1','a1'*32,'{}',self.m.zha.canonical(plan).decode()))
        finally:ledger.close()
        selected._active_sample=SimpleNamespace(request_id=identifier)
        selected._require_sample=lambda **kwargs:None  # Explicit independent source-validation unit port.
        calls=[]
        selected.service=SimpleNamespace(submit=lambda credential,payload:calls.append((credential,payload)) or {'fixture':'authored-port'})
        result=selected.submit_plan(plan)
        self.assertEqual(result,{'fixture':'authored-port'})
        self.assertEqual(calls,[(o.cap.credential,{'plan':plan})])
        with self.assertRaisesRegex(ValueError,'exact original SAMPLE manual grant/plan'):
            selected.submit_plan(dict(plan,retained='changed'))
        self.assertEqual(len(calls),1)

    def test_partial_bootstrap_never_reports_a_confirmed_protective_stop(self):
        selected=bridge.ProductionManualBridge.__new__(bridge.ProductionManualBridge)
        selected._stop_receipt=None;selected._stop_attempted=False;selected.started=False
        selected.service=None;selected.attempted=True
        result=selected.protective_stop()
        self.assertEqual(result['status'],'incomplete')
        self.assertEqual(result['reason'],'manual_bootstrap_unconfirmed')
        self.assertTrue(result['native_owner_close_required'])
        self.assertFalse(result['physical_rest_confirmed'])
        self.assertEqual(selected.protective_stop(),result)

    def read_pair_case(self):
        selected=self.manual_stop_case();caps,status=self.installed_documents(selected.transport.binding)
        self.owner.cap.policy=self.owner.policy
        self.owner.documents={self.m.wire.Operation.STATUS:status,self.m.wire.Operation.CAPABILITIES:caps}
        selected._active_sample=None;selected._active_plan=None
        return selected

    def test_actual_sdk_status_cap_pair_uses_one_bounded_read_transaction(self):
        selected=self.read_pair_case();o=self.owner;before=len(o.calls)
        result=selected.observe()
        self.assertEqual(o.read_boundaries,1)
        self.assertEqual(o.read_pages,94);self.assertEqual(len(o.calls)-before,94)
        self.assertEqual(bytes.fromhex(result['original']['document_hex']),o.documents[self.m.wire.Operation.STATUS])
        self.assertIsNotNone(selected._last_observation)

    def test_changed_original_at_read_end_withholds_current_state(self):
        selected=self.read_pair_case();self.owner.mutate_read_end=True
        with self.assertRaisesRegex(ValueError,'authored read original changed at end'):selected.observe()
        self.assertFalse(hasattr(selected,'_last_observation'))
        self.assertFalse(self.owner.read_scope)

    def test_read_transaction_rejects_authenticated_nonread_before_device_port(self):
        selected=self.read_pair_case();o=self.owner;m=self.m;b=selected.transport.binding;before=len(o.calls)
        packet=m.wire.seal_packet(b.key,b.device_id,b.boot_generation,b.context_digest,m.wire.Direction.REQUEST,
            m.wire.Operation.PLAN_COMMIT,b.session_id,selected.service._sequence+1,b'1'*32)
        with o.manual_read_boundary(o.cap,0):
            with self.assertRaisesRegex(ValueError,'uncertain_HA_dispatch_terminal_no_retry'):
                selected.transport.exchange(packet)
        self.assertEqual(len(o.calls),before)
        self.assertFalse(hasattr(selected,'_last_observation'))

    def test_read_transaction_expiry_before_completion_withholds_current_state(self):
        selected=self.read_pair_case();o=self.owner;step=o.step
        def expire(index,op,**fields):
            result=step(index,op,**fields)
            if o.read_pages==2:o.output_expired=True
            return result
        o.plant.step=expire
        with self.assertRaises(ValueError):selected.observe()
        self.assertFalse(hasattr(selected,'_last_observation'))
        self.assertFalse(o.read_scope)

    def installed_documents(self,b):
        o=self.owner;m=self.m
        context=m.zha.decode_context(b.context)
        caps=struct.pack('<HBBB',404,2,1,0)+b''.join(bytes.fromhex(context['pins'][k]) for k in m.plan.PIN_NAMES)
        caps+=struct.pack('<BH3Q2I',1,255,1000,1000000,3000000,100000,100000)
        caps+=struct.pack('<24H24I',*([4096]*24),*([100]*24))
        for axis in range(2):caps+=struct.pack('<HHHii',0x141+axis,0x241+axis,30,-10000,10000)
        status=struct.pack('<HBBBB3Q8QHI',998,3,0,1,0,1,2,4,*([0]*8),32|4,0)
        status+=b.context_digest+b''.join(bytes.fromhex(context['pins'][k]) for k in m.plan.PIN_NAMES)
        status+=bytes(96+8*45+66+16)+bytes([3])
        for axis in range(2):status+=struct.pack('<BBiiQQQ??',axis,0x92,0,0,o.device,o.device,0,True,True)
        status+=struct.pack('<7B7H3H2i2H3Q',*([0]*7),*([556]*7),0,0,0,0,0,0,0,0,0,0)
        return caps,status

    def test_actual_manual_producer_initial_zero_issues_original_grant_but_nonzero_refuses(self):
        # Authored exact installed CAP2 + idle STATUS3 with fresh matched axes.
        # The actual native source defaults mechanical_settled=false until MOVE
        # settles. The exact producer successor permits only ordinary OFF before
        # that predecessor; the same nonzero request must still refuse.
        _,transport,bootstrap,_=self.start();o=self.owner;m=self.m;b=transport.binding
        caps,status=self.installed_documents(b)
        self.assertEqual(len(caps),404);self.assertEqual(len(status),998)
        decoded=m.zha.decode_snapshot_v3(status,binding=b,captured_device_us=o.device)
        self.assertFalse(decoded['mechanical_settled']);self.assertEqual(len(decoded['axis_observations']),2)
        directory=tempfile.TemporaryDirectory(prefix='authored-first-zero-');self.addCleanup(directory.cleanup)
        scope=o.mm.ledger.Scope(b.device_id.hex(),'1','2',b.context_digest.hex(),'a8'*32)
        path=Path(directory.name)/'manual.sqlite';ledger=o.mm.ledger.ExecutionLedger(path,scope,create=True)
        self.addCleanup(ledger.close)
        principal=m.adapter.Principal(3,('unit-fixture',),('manual-spotlight',),128,(0,)*7+(8,),can_observe=True)
        source=o.mm.manual.ManualSource(1,1,{name:'3500000' for name in m.plan.CAP_FIELDS},'a7'*32)
        authority=o.mm.manual.ManualAuthority(policy=o.policy,binding=b,ledger=ledger,principal=principal,
            credential=o.cap.credential,source=source,clock=bootstrap.clock,test_only=True)
        o.mm.manual.initialize_manual_authority(ledger,authority.policy_sha256)
        actual=bootstrap.handoff(auth=authority,lookup=authority,reservation_gate=authority)
        original=dict(source='original-production-runtime',evidence_source='simulated',
            original=dict(document_hex=status.hex(),captured_device_us=str(o.device)),
            original_capabilities=dict(document_hex=caps.hex()),decoded=decoded,
            capabilities=m.zha.decode_capabilities_v2(caps,m.zha.decode_context(b.context)['pins']))
        client=SimpleNamespace(_service=actual,observe=lambda:deepcopy(original))
        producer=o.mm.manual.ManualProducer(authority,credential=o.cap.credential)
        count=len(o.calls)
        request=dict(request_id=str(uuid.uuid4()),action='spotlight',levels=[0,0,0],transition_us=0)
        plan=producer.produce(request,client)
        self.assertIs(plan['manual'],True);self.assertEqual(plan['frame'][21:],[0,0,0])
        self.assertEqual(plan['predecessor_id'],'0');self.assertEqual(len(m.plan.encode_plan(plan)),409)
        row=ledger.db.execute('SELECT plan FROM c12_manual_grants WHERE request_id=?',(request['request_id'],)).fetchone()
        self.assertEqual(m.zha.strict_json(row['plan']),plan)
        with self.assertRaisesRegex(ValueError,'installed_spotlight_qualification_and_stationarity_required'):
            producer.produce(dict(request_id=str(uuid.uuid4()),action='spotlight',levels=[1,0,0],transition_us=0),client)
        self.assertEqual(len(o.calls),count)
        self.assertEqual(ledger.db.execute('SELECT COUNT(*) FROM c12_manual_grants').fetchone()[0],1)
        self.assertEqual(ledger.db.execute('SELECT COUNT(*) FROM reservations').fetchone()[0],1)

if __name__=='__main__':unittest.main()
