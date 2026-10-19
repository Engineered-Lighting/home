"""Actual four-member production family; explicit start, same bounded cleanup."""
from copy import deepcopy

from room_fake_plant import RoomFakePlant,_require,_request_value,_canonical
from room_canonical_plant import _RecordedMember
from room_production_canonical_provider import verified_production_provider_export,ABI
from room_production_canonical_recording import RoomProductionCanonicalRecorder,validate_header


class RoomProductionCanonicalPlant(RoomFakePlant):
    def __init__(self,manifest_path,expected_manifest_sha256,recorder,*,fixture_ids,qualification,process_factory):
        self.qualification=qualification
        def factory(*args,**kwargs):return _RecordedMember(process_factory(*args,**kwargs),recorder)
        super().__init__(manifest_path,expected_manifest_sha256,recorder,fixture_ids=fixture_ids,process_factory=factory)

    def _binding(self):
        from pathlib import Path
        _require(type(self.recorder) is RoomProductionCanonicalRecorder,'production_durable_recorder_required')
        profile,manifest,_=verified_production_provider_export()
        _require(Path(self.manifest_path)==profile.build/'manifest.json'
            and self.expected_manifest_sha256==profile.manifest_sha256,'fixed_production_manifest_required')
        header=self.recorder.header;validate_header(header,self.qualification)
        _require(tuple(self.fixture_ids)==tuple(f['fixture_id'] for f in header['fixtures']),
                 'production_fixture_inventory_changed')
        self.qualification.verify_plant(self)
        self._header=deepcopy(header)

    def step(self,index,op,**fields):
        def admission():
            owner=self.qualification._owner
            active=owner._active_manual_io()
            if active is not None:
                _require(active[1]==index and active[2] is False and op=='exchange','exact_manual_packet_boundary_required')
            self.qualification.require_context()
            if op=='exchange':
                packet=owner.modules.wire.inspect_packet(bytes.fromhex(fields['packet_hex']))
                w=owner.modules.wire
                if packet.operation not in (w.Operation.SESSION_OPEN,w.Operation.TIME_EXCHANGE,w.Operation.STATUS,w.Operation.CAPABILITIES):
                    owner.require_current()
        return self._step(index,op,fields,admission)

    def protective_exchange(self,index,cap,packet):
        owner=self.qualification._owner
        owner.protective_context(cap,index)
        w=owner.modules.wire;b=owner.native_bindings[index]
        opened=w.open_packet(b.key,b.device_id,b.boot_generation,b.context_digest,w.Direction.REQUEST,packet)
        _require(opened.operation is w.Operation.WITHDRAW and opened.session==b.session_id
                 and opened.body==w.withdraw_body(255,3,True),'exact_protective_travel_only')
        c=owner.protective_clock_sample(cap,index)
        return self._step(index,'exchange',dict(at_device_us=str(c.now_device_us),ingress_device_us=str(c.now_device_us),
            packet_hex=packet.hex()),lambda:owner.protective_context(cap,index))

    def _step(self,index,op,fields,admission):
        with self._state_lock:
            _require(self._started and not self._closing and self._terminal is None and self._receipt is None,'room_not_active')
            self._active_steps+=1
        try:
            admission()
            _require(self.recorder.ready and type(index) is int and 0<=index<4
                and op in ('exchange','observe','can_port','lighting_timer','hal_fault','tick','set_effect','disconnect'),
                'production_step_scope')
            _require(not set(fields)&{'schema','version','op','request_id'},'production_reserved_step_metadata')
            _request_value(fields)
            _require(len(_canonical(dict(schema=ABI,version=1,op=op,request_id='request-100000',**fields)))+1<=16384,
                     'production_request_byte_bound')
            member=self._members[index]
            if op=='can_port':result=member.can_port(int(fields['at_device_us']),**{k:v for k,v in fields.items() if k!='at_device_us'})
            elif op=='hal_fault':result=member.hal_fault(int(fields['at_device_us']),fields['mode'])
            else:result=member.request(op,**deepcopy(fields))
            with self._state_lock:_require(not self._closing and self._terminal is None,'room_not_active')
            return deepcopy(result)
        except BaseException:
            self._abort('production_member_step_failed');raise
        finally:
            with self._state_lock:self._active_steps-=1
