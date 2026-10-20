"""Source-born Home owner for one explicit production-canonical software family.

Birth is derived from the actual Store, acquisition, Journal, manifest and grant
objects. It is neither a JSON permission nor physical fixture qualification.
Accepted commits remain a separate currentness gate. No native launch occurs
until explicit start(), and no manual/command grant is minted here.
"""
from copy import deepcopy
from contextlib import contextmanager
from dataclasses import asdict,dataclass
import hashlib
import json
import os
from pathlib import Path
import secrets
import struct
import threading
import uuid

from journal import Journal,Grant,digest
from store import Store
from lab_live_runtime import LabRuntimeComposition
from lab_live_acquisition import LabLiveAcquisition
from room_committed_source import CommittedRoomSource,RoomCommitBinding
from room_command_factory import _SimulatedClock,_NativeMember,SIMULATION_PROFILE
from room_canonical_adapter import CanonicalRoomObservation
from room_production_canonical_provider import (FAMILY,PROTOCOL,FIRMWARE_SHA256,
    verified_production_provider_export,load_production_canonical_modules,load_production_manual_modules)
from room_production_canonical_client import ProductionCanonicalClientFactory

_BIRTH=object()
_MANUAL=object()
_SOURCE_FILES=('room_production_canonical_owner.py','room_production_canonical_plant.py',
    'room_production_canonical_recording.py','room_production_canonical_replay.py',
    'room_production_canonical_provider.py','room_production_canonical_client.py',
    'room_production_canonical_manual.py','room_production_canonical_adapter.py','room_production_spotlight_sample.py',
    'room_command_factory.py','room_canonical_adapter.py','room_fake_plant.py','room_fake_plant_recording.py',
    'room_fake_plant_replay.py','room_canonical_plant.py','room_canonical_plant_recording.py','archive.py',
    'room_production_command_factory.py','supervisor.py')


def need(ok,reason):
    if not ok:raise ValueError(reason)


def encoded(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode('ascii')
def sha(raw):return hashlib.sha256(raw).hexdigest()


def _exclusive(path,raw):
    path=Path(path)
    need(path.is_absolute() and path.parent.is_dir() and not path.exists()
        and not any(p.is_symlink() for p in (path,*path.parents)),'fresh_private_birth_file_required')
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_BINARY',0),0o600)
    try:
        offset=0
        while offset<len(raw):
            n=os.write(fd,raw[offset:]);need(n>0,'birth_short_write');offset+=n
        os.fsync(fd)
    finally:os.close(fd)
    if os.name=='posix':
        fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)


class ProductionSourceQualification:
    """Only this exact owner may register its retained born-context capability."""
    def __init__(self,owner,token):
        need(token is _BIRTH and type(owner) is ProductionCanonicalOwner,'actual_production_source_birth_required')
        self._owner=owner

    def public(self):
        o=self._owner
        need(o.qualification is self and o._birth_raw is not None,'unregistered_production_source_birth')
        need(o.birth_file.is_file() and not o.birth_file.is_symlink()
            and o.birth_file.read_bytes()==o._birth_raw,'retained_production_source_birth_changed')
        need(encoded(o.birth)+b'\n'==o._birth_raw and o.source.binding.scope==o.birth['home']['scope'],
             'production_birth_context_mutated')
        return deepcopy(dict(schema='home-production-canonical-source-birth-ref-1',version=1,
            selection=FAMILY,protocol=PROTOCOL,birth_id=o.birth['birth_id'],birth_sha256=sha(o._birth_raw),
            home_scope=o.source.binding.scope,provider_pins=o.modules.provider_pins,
            physical_permission=False,physical_truth=False,home_qualified=False))

    def verify_header(self,header):
        need(self._owner.qualification is self and encoded(header)==self._owner._header_raw
             and encoded(self._owner.header)==self._owner._header_raw,'production_birth_header_mismatch')
        self.public()

    def verify_plant(self,plant):
        need(self._owner.plant is plant,'production_birth_plant_mismatch')
        self.verify_header(plant.recorder.header)

    def require_current(self):return self._owner.require_current()
    def require_context(self):return self._owner.require_context()


@dataclass(frozen=True)
class ProductionNativeTuple:
    fixture_index:int
    binding:object
    session_cursor:object
    observation:object
    provider_pins:dict
    birth_sha256:str


class ProductionOfflineControlAuthorization:
    """Registered private capability; an approval JSON is never this authority."""
    def __init__(self,owner,index,token,**values):
        need(token is _MANUAL and type(owner) is ProductionCanonicalOwner,'actual_offline_owner_authorization_required')
        self._owner,self._index=owner,index
        for name,value in values.items():setattr(self,name,value)
        self._fingerprint=owner._manual_fingerprint(self)
        self._frozen=True

    def __setattr__(self,name,value):
        need(not getattr(self,'_frozen',False),'offline_authorization_is_immutable')
        object.__setattr__(self,name,value)


class _ProductionNativeMember(_NativeMember):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self.boot_attempted=False;self.handed_off=False

    def exchange(self,*args,**kwargs):
        raise ValueError('genuine_sdk_is_the_only_production_session_owner')

    def bootstrap(self):
        raise ValueError('use_boot_native_then_genuine_sdk_bootstrap')

    def boot_native(self):
        need(not self.boot_attempted,'production_bootstrap_not_repeatable');self.boot_attempted=True
        # Real peripheral time is an explicit independent input. Getters and
        # tick calls do not manufacture the zero fence or advance this counter.
        self.ask('lighting_timer',at_device_us='1000',counter=2048,wraps=0,timer_epoch='1')
        first=self.ask('tick',at_device_us='1000')
        need(first['report']['boot_ready'] is False and first['report']['boot_failed'] is False,
             'production_boot_zero_fence_pending_required')
        self.ask('lighting_timer',at_device_us='2000',counter=0,wraps=1,timer_epoch='1')
        ready=self.ask('tick',at_device_us='2000')
        need(ready['report']['boot_ready'] is True and ready['report']['boot_failed'] is False
             and ready['report']['original_plan'] is None
             and any(o['kind']=='boot_ready' for o in ready['canonical_ops']),
             'genuine_production_boot_ready_before_session_required')
        # The genuine SDK BootstrapOwner is the sole SESSION/clock owner.
        # This native peripheral fence deliberately sends no SESSION packet.


class ProductionCanonicalOwner:
    """One source/context and four explicit processes, without another scheduler."""
    def __init__(self,source,*,composition,graph,worker_recorder,recording,limits):
        from live_den_mount import _runtime_module
        _runtime_module()
        from home_den_runtime.archive import Recorder
        need(type(source) is CommittedRoomSource and type(composition) is LabRuntimeComposition
             and type(graph) is LabLiveAcquisition and type(worker_recorder) is Recorder,
             'actual_source_composition_graph_and_recorder_required')
        need(type(recording) is dict and set(recording)=={'native_file','birth_file','max_records','max_bytes','min_free_bytes'}
             and type(limits) is dict and set(limits)=={'max_runtime_s'}
             and type(limits['max_runtime_s']) in (int,float) and 0<limits['max_runtime_s']<=120,
             'explicit_bounded_production_owner_declaration_required')
        need(type(recording['max_records']) is int and 8<=recording['max_records']<=8192
             and type(recording['max_bytes']) is int and 4096<=recording['max_bytes']<=33554432
             and type(recording['min_free_bytes']) is int and recording['min_free_bytes']>=0,
             'bounded_production_recording_required')
        self.source,self.composition,self.graph,self.worker_recorder=source,composition,graph,worker_recorder
        self.recording,self.limits=deepcopy(recording),deepcopy(limits)
        self.cancel=threading.Event();self._lock=threading.RLock();self.closed=False;self.started=False;self._attempted=False
        self._output_closed=threading.Event();self._protective_attempts=set()
        self._manual_io_local=threading.local();self._born_grants=();self._born_native_bindings=()
        self._born_store=None
        self.plant=self.recorder=None;self.qualification=None;self._birth_raw=None;self.birth=None;self.header=None
        self._header_raw=None
        self.native_bindings=();self._native_members=[];self._tuples={};self._handed=set()
        self._manual_caps={};self._policies=();self._native_booted=False
        self._close_receipt=None
        self.birth_file=Path(recording['birth_file']);self._recorded_source_pins=None
        self._require_context()
        self.profile,self.manifest,_=verified_production_provider_export()
        self.modules=load_production_canonical_modules();self.client=ProductionCanonicalClientFactory()
        self._manual_modules=load_production_manual_modules()
        need(self._manual_modules.sdk is self.modules,'same_production_manual_sdk_required')
        self.clock=_SimulatedClock(composition,graph,limits['max_runtime_s'])

    def _require_context(self,*,_protective=False):
        c,g,s=self.composition,self.graph,self.source;j=g.journal
        need(not self.closed and not self.cancel.is_set() and type(c.store) is Store
             and g is c.graph and g.join is c.join and g.store is c.store and s.journal is j
             and type(j) is Journal,'production_source_context_not_current')
        if not _protective:need(c.command_current(g) is True,'production_source_context_not_current')
        else:
            need(self.birth is not None and digest(c.producer)==c.pin
                and digest(c.acquisition_config)==c.configuration_digest
                and g.configuration_digest==c.configuration_digest and c.scope==self.birth['home']['scope']
                and g.config==self.birth['home']['acquisition_config'],'protective_source_configuration_changed')
        need(c.producer['version']==8 and g.config['version']==8 and j.dynamic.config==c.producer['live_config']
             and j.dynamic.config['version']==13 and j.dynamic.worker.worker_version=='home-live-context-worker-12.0.0',
             'explicit_production_source8_config13_worker12_required')
        acquired=j.lock.acquire(blocking=not _protective)
        need(acquired,'protective_original_journal_busy')
        try:
            if not _protective:s._installation()
            else:
                need(j.dynamic.identity==c.scope and j._meta('epoch')==self.birth['home']['journal_stream_epoch'],
                     'protective_source_epoch_changed')
            worker=j.dynamic.worker.policy
            expected=RoomCommitBinding(deepcopy(c.scope),worker['worker_id'],worker['producer_id'],
                worker['config_digest'],j.dynamic.config['manifest_ref']['digest'],j.dynamic.worker.worker_version,
                tuple(f['fixture_id'] for f in worker['room_policy']['fixtures']))
            need(s.binding==expected and g.principal in j.grants
                 and j.grants==tuple(Grant.from_dict(v,profile='home-live-shadow-1') for v in c.producer['journal_grants']),
                 'actual_production_room_binding_or_grants_changed')
        finally:j.lock.release()
        need((_protective or self.worker_recorder.status()['status']=='ready')
             and self.worker_recorder.manifest['session_ref']==c.scope
             and self.worker_recorder.manifest['configuration_digest']==g.configuration_digest,
             'same_ready_parent_worker_recorder_required')
        if _protective:
            need(self.worker_recorder.manifest==self.birth['home']['parent_worker_manifest'],
                 'protective_parent_recording_manifest_changed')
            original=self.birth['home']['store_session']
            need(c.store.lock.acquire(False),'protective_original_store_busy')
            try:
                session=c.store.get_session(original['id'])
                need(all(session.get(k)==original.get(k) for k in
                    ('id','site_id','camera_id','experiment_id','request_id','generation','fixture')),
                    'protective_original_session_identity_changed')
                row=c.store.db.execute("SELECT payload FROM events WHERE session_id=? AND kind='experiment.activated' ORDER BY id DESC LIMIT 1",
                                       (original['id'],)).fetchone()
            finally:c.store.lock.release()
            active=json.loads(row['payload']) if row else {};binding=c.producer['lab_binding']
            profile=binding['lab_profile'];selected=active.get('profiles',{}).get('b',{})
            need(active.get('generation')==binding['model_generation'] and active.get('site_id')==original['site_id']
                and active.get('camera_id')==original['camera_id']
                and all(selected.get(k)==profile[k] for k in ('profile_id','artifact_sha256','engine_epoch','model_version')),
                'protective_original_model_epoch_changed')
            need(len(self.native_bindings)==4 and all(b.binding_sha256==self.birth['contexts'][i]['binding_sha256']
                and b.context.hex()==self.birth['contexts'][i]['context_hex'] for i,b in enumerate(self.native_bindings)),
                'protective_native_binding_changed')
        need(self.limits['max_runtime_s']<=c.producer['limits']['max_runtime_s'],'production_owner_budget_exceeds_source')
        return worker

    def prepare(self):
        from room_production_canonical_recording import RoomProductionCanonicalRecorder
        from room_production_canonical_plant import RoomProductionCanonicalPlant
        with self._lock:
            need(self.birth is None,'production_owner_preparation_not_repeatable')
            worker=self._require_context();c,g=self.composition,self.graph
            paths=(self.birth_file,Path(self.recording['native_file']))
            protected={Path(self.worker_recorder.path),Path(c.producer['journal_path']),Path(c.producer['original_archive']['filename'])}
            need(len(set(paths))==2 and not set(paths)&protected and all(p.is_absolute() and p.parent.is_dir()
                 and not p.exists() and not any(a.is_symlink() for a in p.parents) for p in paths),
                 'fresh_disjoint_production_recordings_required')
            import shutil
            need(shutil.disk_usage(self.birth_file.parent).free>=self.recording['min_free_bytes']+self.recording['max_bytes']+2097152,
                 'production_recording_storage_headroom')
            source_pins={n:sha((Path(__file__).parent/n).read_bytes()) for n in _SOURCE_FILES}
            session=c.store.get_session(c.scope['session_id'])
            home=dict(scope=deepcopy(c.scope),store_session=deepcopy(session),lab_binding=deepcopy(c.producer['lab_binding']),
                acquisition_config=deepcopy(g.config),model_ref=deepcopy(c.producer['model_ref']),
                semantic_policy=deepcopy(c.producer['semantic_policy']),desk_policy=deepcopy(c.producer['desk_policy']),
                live_manifest=deepcopy(g.journal.dynamic.config['manifest']),
                journal_stream_epoch=g.journal._meta('epoch'),journal_grants=deepcopy(c.producer['journal_grants']),
                parent_worker_manifest=deepcopy(self.worker_recorder.manifest),room_binding=deepcopy(vars(self.source.binding)))
            config=json.loads((self.profile.build/'source/contracts/c12/canonical-capture-config-v1.json').read_bytes())
            birth_id='production-home-birth:'+secrets.token_hex(16)
            home_hash=sha(encoded(home));contexts=[];bindings=[];policies=[]
            for i,f in enumerate(config['fixtures']):
                identity=dict(birth_id=birth_id,home_sha256=home_hash,fixture_id=self.source.binding.fixture_ids[i],index=i,
                              provider_pins=self.modules.provider_pins)
                numeric=lambda purpose:str(int(sha(encoded([purpose,identity]))[:15],16)+1)
                pins={name:bytes([n+1]).hex()*32 for n,name in enumerate(('fixture','firmware','protocol','profile','calibration','contract'))}
                context=dict(pins=pins,source_cut=home_hash,protection=sha(encoded([home['desk_policy'],identity])),
                    authority=sha(encoded([home['journal_grants'],identity])),boot_generation=f['boot'],session_id=numeric('session'),
                    principal_id='3',ownership_generation=numeric('owner'))
                raw=self.modules.plan.encode_context(context)
                m=self.modules.adapter
                full_pins={**pins,'model':sha(encoded(home['model_ref'])),'home_source':home_hash,
                           'home_schema':sha(encoded(home['live_manifest']))}
                policy=m.AdapterPolicy(identity['fixture_id'],bytes.fromhex(f['device_id']),context,full_pins,
                    tuple(m.IssuerPolicy(mode,'offline-owner-'+str(i)+'-'+str(n),1,secrets.token_hex(32))
                          for n,mode in enumerate(('manual-ambient','manual-spotlight','manual-preset'))))
                b=self.modules.zha.RuntimeBinding('ff:ff:ff:ff:ff:ff:ff:fe',':'.join(f['device_id'][n:n+2] for n in range(0,16,2)),
                    sha(encoded(identity))[:32],bytes.fromhex(f['device_id']),bytes.fromhex(f['key_hex']),int(f['boot']),
                    int(context['session_id']),int(context['ownership_generation']),raw,self.modules.zha.adapter_policy_sha256(policy),sha(encoded(self.client.binding())))
                bindings.append(b);policies.append(policy);contexts.append(dict(fixture_id=identity['fixture_id'],device_id=f['device_id'],context=context,
                    context_hex=raw.hex(),binding_sha256=b.binding_sha256))
            birth=dict(schema='home-production-canonical-source-birth-1',version=1,birth_id=birth_id,selection=FAMILY,
                protocol=PROTOCOL,provider_pins=deepcopy(self.modules.provider_pins),firmware_source_sha256=FIRMWARE_SHA256,
                home=home,home_sha256=home_hash,contexts=contexts,source_pins=source_pins,client_binding=self.client.binding(),
                clock_original=deepcopy(self.clock.original),simulation_profile=deepcopy(SIMULATION_PROFILE),
                physical_permission=False,physical_truth=False,home_qualified=False)
            raw=encoded(birth)+b'\n';need(len(raw)<=2097152,'production_birth_byte_bound')
            _exclusive(self.birth_file,raw);self._require_context()
            self.birth,self._birth_raw,self._recorded_source_pins=birth,raw,source_pins
            self.native_bindings=tuple(bindings)
            self._born_native_bindings=self.native_bindings;self._born_grants=tuple(g.journal.grants)
            self._born_store=c.store
            self._policies=tuple(policies)
            self.header=dict(session_id=c.scope['session_id'],producer=dict(abi=self.manifest['abi'],build_id=self.manifest['build_id'],
                manifest_sha256=self.profile.manifest_sha256,source_sha256=self.manifest['source_digest'],config_sha256=self.manifest['config_sha256'],
                executable_sha256=self.manifest['executable']['sha256']),fixtures=[dict(fixture_index=i,fixture_id=fid,
                device_id=config['fixtures'][i]['device_id'],boot=config['fixtures'][i]['boot'],launch_id=secrets.token_hex(16))
                for i,fid in enumerate(self.source.binding.fixture_ids)],evidence_source='simulated',physical_permission=False,physical_truth=False)
            self._header_raw=encoded(self.header)
            self.qualification=ProductionSourceQualification(self,_BIRTH)
            self.recorder=RoomProductionCanonicalRecorder(self.recording['native_file'],header=self.header,qualification=self.qualification,
                max_records=self.recording['max_records'],max_bytes=self.recording['max_bytes'])
            self.plant=RoomProductionCanonicalPlant(str(self.profile.build/'manifest.json'),self.profile.manifest_sha256,self.recorder,
                fixture_ids=self.source.binding.fixture_ids,qualification=self.qualification,process_factory=self.client)
            self._native_config=config
            return self.readiness()

    @property
    def recording_ready(self):return self.recorder is not None and self.recorder.ready and not self.cancel.is_set()

    def readiness(self):
        return dict(status='ready' if self.started else 'prepared' if self.recording_ready else 'unavailable',selection=FAMILY,
            source_birth=self.qualification.public() if self.qualification is not None else None,
            native_ready=self.started,physical_permission=False,physical_truth=False,home_qualified=False)

    def require_context(self):
        active=self._active_manual_io()
        if active is not None:
            need(active[2] is False,'protective_boundary_does_not_allow_ordinary_io')
            return self.manual_io_current(*active[:2],protective=active[2])
        self._require_context();return self._require_retained_context()

    def _require_retained_context(self):
        need(self.qualification is not None,'production_owner_not_prepared')
        self.qualification.public()
        need(self._recorded_source_pins==self.birth['source_pins'] and
             all(sha((Path(__file__).parent/n).read_bytes())==h for n,h in self._recorded_source_pins.items()),
             'production_home_source_changed')
        verified_production_provider_export()
        return True

    def require_current(self):
        need(not self._output_closed.is_set(),'production_output_admission_closed')
        self.require_context()
        current=self.source.current()
        need(not self._output_closed.is_set(),'production_output_admission_closed')
        return current

    def stop_output_admission(self):
        self._output_closed.set()
        return dict(status='output_admission_closed',physical_permission=False)

    def install_commit(self,commit):
        self._require_context();return self.source.install_commit(commit)

    def start_native_boot(self):
        with self._lock:
            need(self.recording_ready and not self._attempted,'production_start_not_repeatable')
            self.require_context()
            need(set(self._manual_caps)==set(range(4)),'four_independent_offline_manual_approvals_required_before_boot')
            for i in range(4):self.require_manual_authorization(self._manual_caps[i],i)
            self._attempted=True
        try:
            self.plant.start()
            for i,binding in enumerate(self.native_bindings):
                member=_ProductionNativeMember(self.plant,i,binding,self.modules,self.clock,self._native_config)
                self._native_members.append(member);member.boot_native()
                now=str(self.clock.sample().now_device_us)
                member.ask('set_effect',at_device_us=now,driver='can',effect='submitted')
                member.ask('set_effect',at_device_us=now,driver='lighting',effect='submitted')
                member.feedback(force=True)
            self._native_booted=True
            return dict(self.readiness(),native_boot_ready=True,session_opened=False)
        except BaseException:
            self.plant._abort('production_bootstrap_failed');raise

    def start(self):
        """Explicit boot only. The manual bridge owns the sole SDK session."""
        return self.start_native_boot()

    def monotonic_ns(self):return self.clock.sample().now_home_us*1000

    def _protective_clock_sample(self):
        """Same original clock/epoch and deadline, without acquisition liveness."""
        from room_canonical_adapter import SimulatedClockObservation
        value=self.composition.control_clock();now=_SimulatedClock._home(value)
        need({k:v for k,v in value['clock'].items() if k!='original_value'}==
             {k:v for k,v in self.clock.original.items() if k!='original_value'},'protective_clock_original_changed')
        need(self.clock.last<=now and now+self.clock.uncertainty<self.clock.until,
             'protective_original_owner_clock_rollback_or_expired')
        self.clock.last=now;device=1000000+now-self.clock.home
        return SimulatedClockObservation(now,device,self.clock.home,self.clock.uncertainty,self.clock.until,1,device,device)

    def protective_clock_sample(self,cap,index):
        self.protective_context(cap,index)
        return self._protective_clock_sample()

    def protective_monotonic_ns(self,cap,index):return self.protective_clock_sample(cap,index).now_home_us*1000

    def manual_approval_template(self,index,*,ledger_path,maximum_requests=64,modes=('manual-spotlight',)):
        """Data helper only; authorize_manual still needs an authenticated grant."""
        need(self.qualification is not None and type(index) is int and 0<=index<4,'prepared_production_member_required')
        return dict(schema='home-production-offline-control-approval-1',version=1,selection=FAMILY,
            scope=deepcopy(self.source.binding.scope),fixture_id=self.source.binding.fixture_ids[index],
            native_context_sha256=self.native_bindings[index].context_digest.hex(),principal_id=None,
            modes=list(modes),domain_mask=255,field_mask=[0]*7+[8],
            caps={name:str(self.clock.until) for name in self.modules.plan.CAP_FIELDS},
            ledger_path=str(ledger_path),maximum_requests=maximum_requests,provenance='authored-offline-owner-approval',
            can_travel=False,owner_valid_until_home_us=str(self.clock.until),
            physical_permission=False,physical_truth=False,hardware_enabled=False)

    def authorize_manual(self,index,*,grant,original,original_file):
        with self._lock:
            self._require_context();need(self.recording_ready and not self._attempted and type(index) is int and 0<=index<4
                and index not in self._manual_caps,'fresh_preboot_manual_authorization_required')
            j=self.source.journal;scope=self.source.binding.scope
            need(type(grant) is Grant and any(grant is g for g in j.grants)
                and 'intent' in grant.operations and 'read' in grant.operations
                and (scope['site_id'],scope['room_id']) in grant.scopes
                and 'home-live-intent-event-2' in grant.payload_types
                and grant.expected_actor_ref==grant.principal_id and grant.principal_id!=self.graph.principal.principal_id,
                'actual_authenticated_human_intent_grant_required')
            template=self.manual_approval_template(index,ledger_path='unused')
            need(type(original) is dict and set(original)==set(template),'closed_independent_offline_approval_required')
            for key in ('schema','version','selection','scope','fixture_id','native_context_sha256','provenance',
                        'physical_permission','physical_truth','hardware_enabled'):
                need(type(original[key]) is type(template[key]) and original[key]==template[key],'offline_approval_scope_or_mode_changed')
            need(original['principal_id']==grant.principal_id and original['modes']==['manual-spotlight'],
                 'explicit_supported_manual_spotlight_mode_required')
            need(type(original['domain_mask']) is int and 0<original['domain_mask']<=255
                and type(original['field_mask']) is list and len(original['field_mask'])==8
                and all(type(v) is int for v in original['field_mask'])
                and original['field_mask']==[0]*7+[8],'bounded_offline_manual_fields_required')
            need(type(original['maximum_requests']) is int and 1<=original['maximum_requests']<=256,'finite_offline_request_budget_required')
            need(type(original['can_travel']) is bool and (not original['can_travel'] or original['domain_mask']==255),
                 'separate_explicit_global_protective_scope_required')
            c=self.clock.sample();need(type(original['caps']) is dict and set(original['caps'])==set(self.modules.plan.CAP_FIELDS),
                                    'exact_original_offline_caps_required')
            for value in original['caps'].values():
                need(type(value) is str and value.isascii() and value.isdecimal() and str(int(value))==value
                    and c.now_home_us+c.uncertainty_us<int(value)<=self.clock.until,'offline_cap_expired_or_extended')
            owner_until=original['owner_valid_until_home_us']
            need(type(owner_until) is str and owner_until.isascii() and owner_until.isdecimal()
                 and str(int(owner_until))==owner_until
                 and max(int(v) for v in original['caps'].values())<=int(owner_until)<=self.clock.until,
                 'finite_independent_owner_deadline_required')
            need(type(original['ledger_path']) is str,'fixed_manual_ledger_path_required')
            ledger_path=Path(original['ledger_path']);path=Path(original_file)
            protected={self.birth_file,Path(self.recording['native_file']),Path(self.worker_recorder.path),
                Path(self.composition.producer['journal_path']),Path(self.composition.producer['original_archive']['filename'])}
            need(ledger_path.is_absolute() and ledger_path.parent.is_dir() and not ledger_path.exists()
                and path.is_absolute() and ledger_path!=path and not {ledger_path,path}&protected
                and not any(p.is_symlink() for p in (ledger_path,*ledger_path.parents)), 'fresh_disjoint_manual_ledger_required')
            raw=encoded(original)+b'\n';need(len(raw)<=16384,'offline_approval_byte_bound');_exclusive(path,raw)
            mm=self._manual_modules;m=self.modules.adapter;b=self.native_bindings[index];p=self._policies[index]
            valid_ns=int(owner_until)*1000
            h=self.modules.session.HomeClockQualification('home-production-offline-clock',sha(encoded(self.clock.original)),
                c.now_home_us*1000,c.now_home_us,c.uncertainty_us,0,valid_ns,'synthetic')
            limits=self.modules.session.ClockLimits(sha(encoded([original,self.clock.original])),b.device_id.hex(),b.boot_generation,
                0,250000,50000,5000000,3000000,valid_ns,32,'synthetic')
            principal=m.Principal(int(self.birth['contexts'][index]['context']['principal_id']),
                (self.source.binding.fixture_ids[index],),tuple(original['modes']),original['domain_mask'],
                tuple(original['field_mask']),can_observe=True,can_close_session=True,can_manage_clock=True,
                can_travel=original['can_travel'])
            credential=secrets.token_hex(32)
            source=mm.manual.ManualSource(1,1,deepcopy(original['caps']),sha(raw))
            ledger_scope=mm.ledger.Scope(b.device_id.hex(),str(b.boot_generation),str(b.session_id),b.context_digest.hex(),sha(raw))
            ledger=mm.ledger.ExecutionLedger(ledger_path,ledger_scope,create=True,capacity=original['maximum_requests'])
            try:
                policy_hash=mm.manual.manual_policy_sha256(policy=p,binding=b,scope=ledger_scope,principal=principal,
                    credential=credential,source=source,presets=())
                mm.manual.initialize_manual_authority(ledger,policy_hash)
            finally:ledger.close()
            cap=ProductionOfflineControlAuthorization(self,index,_MANUAL,policy=p,principal=principal,credential=credential,
                source=source,presets=(),ledger_path=ledger_path,ledger_scope=ledger_scope,home_qualification=h,clock_limits=limits,
                activation_approval_id=str(uuid.uuid4()),session_approval_id=str(uuid.uuid4()),valid_until_monotonic_ns=valid_ns,
                _original_file=path,_original_raw=raw,_grant=grant,_policy_sha256=policy_hash,_used=set())
            self._manual_caps[index]=cap
            return cap

    def manual_inputs(self,index):
        cap=self._manual_caps.get(index);self.require_manual_authorization(cap,index);return cap

    def _manual_fingerprint(self,cap):
        # The real, shared typed SDK was loaded and pin-verified by this owner.
        # This digest is pure; disk/provider freshness belongs to the actual
        # packet/admission boundary, not every fictional envelope/clock call.
        mm=self._manual_modules;b=self.native_bindings[cap._index]
        need(mm.sdk is self.modules,'same_production_manual_sdk_required')
        policy=mm.manual.manual_policy_sha256(policy=cap.policy,binding=b,scope=cap.ledger_scope,
            principal=cap.principal,credential=cap.credential,source=cap.source,presets=cap.presets)
        return sha(encoded(dict(policy=policy,ledger_path=str(cap.ledger_path),ledger_scope=cap.ledger_scope.encoded(),
            home=asdict(cap.home_qualification),limits=asdict(cap.clock_limits),activation=cap.activation_approval_id,
            session=cap.session_approval_id,valid_until=str(cap.valid_until_monotonic_ns),original_sha256=sha(cap._original_raw))))

    def require_manual_authorization(self,cap,index,*,protective=False):
        need(type(protective) is bool,'explicit_manual_route_required')
        active=self._active_manual_io()
        if active is not None and active[:3]==(cap,index,protective):
            return self.manual_io_current(cap,index,protective=protective)
        if protective:return self.protective_context(cap,index)
        self.require_context();self._validate_manual_original(cap,index)
        c=self.clock.sample()
        need(c.now_home_us*1000<cap.valid_until_monotonic_ns
             and c.now_home_us+c.uncertainty_us<min(int(v) for v in cap.source.caps.values()),
             'explicit_current_output_or_protective_manual_scope_required')
        return True

    def _validate_manual_original(self,cap,index):
        need(type(cap) is ProductionOfflineControlAuthorization and cap._owner is self and cap._index==index
            and self._manual_caps.get(index) is cap and any(cap._grant is g for g in self.source.journal.grants)
            and cap._original_file.is_file() and not cap._original_file.is_symlink()
            and cap._original_file.read_bytes()==cap._original_raw
            and cap._fingerprint==self._manual_fingerprint(cap),'registered_current_offline_manual_authorization_required')
        return True

    def protective_context(self,cap,index):
        active=self._active_manual_io()
        if active is not None and active[:3]==(cap,index,True):
            return self.manual_io_current(cap,index,protective=True)
        self._require_context(_protective=True);self._require_retained_context();self._validate_manual_original(cap,index)
        c=self._protective_clock_sample()
        need(cap.principal.can_travel is True and cap.principal.domain_mask==255
             and c.now_home_us*1000<cap.valid_until_monotonic_ns,'independent_finite_global_protective_scope_required')
        return True

    def _active_manual_io(self):
        stack=getattr(getattr(self,'_manual_io_local',None),'stack',())
        return stack[-1] if stack else None

    def manual_io_current(self,cap,index,*,protective=False):
        """No-effect live guard; each packet checks the fixed configuration."""
        need(type(protective) is bool and not self.closed and not self.cancel.is_set()
            and type(index) is int and 0<=index<4
            and type(cap) is ProductionOfflineControlAuthorization and self._manual_caps.get(index) is cap
            and cap._owner is self and cap._index==index,'current_registered_manual_io_required')
        c,g,s=self.composition,self.graph,self.source;j=s.journal
        need(g is c.graph and g.join is c.join and g.store is c.store and j is g.journal
            and c.scope==self.birth['home']['scope'] and j.dynamic.identity==c.scope
            and j._meta('epoch')==self.birth['home']['journal_stream_epoch']
            and c.store is self._born_store
            and len(j.grants)==len(self._born_grants) and all(a is b for a,b in zip(j.grants,self._born_grants))
            and any(cap._grant is value for value in j.grants)
            and self.native_bindings[index] is self._born_native_bindings[index]
            and cap._fingerprint==self._manual_fingerprint(cap),'manual_io_context_or_original_changed')
        if protective:
            need(digest(c.producer)==c.pin and digest(c.acquisition_config)==c.configuration_digest
                and j._meta('epoch')==self.birth['home']['journal_stream_epoch']
                and cap.principal.can_travel is True and cap.principal.domain_mask==255,
                'manual_io_protective_scope_changed')
        else:
            need(not self._output_closed.is_set(),'production_output_admission_closed')
            # Clock/envelope calls have no device effect. Keep their current
            # Store/model/epoch and sticky-liveness checks, without repeatedly
            # canonicalizing the whole configuration. The real packet boundary
            # independently checks command_current before every device write.
            need(not c.cancel.is_set() and not g.cancel.is_set() and not g.closed and not g.finished
                and g.problem is None and g.configuration_digest==c.configuration_digest,
                'manual_io_source_not_current')
            c._current()
        clock=self._protective_clock_sample()
        need(clock.now_home_us*1000<cap.valid_until_monotonic_ns,'manual_io_owner_deadline_expired')
        if not protective:
            need(clock.now_home_us+clock.uncertainty_us<min(int(v) for v in cap.source.caps.values()),
                 'manual_io_output_deadline_expired')
        return True

    def manual_io_clock_sample(self,cap,index,*,protective=False):
        self.manual_io_current(cap,index,protective=protective)
        return self._protective_clock_sample()

    def _active_manual_read(self):
        return getattr(self._manual_io_local,'read',None)

    def _full_manual_io_verification(self,cap,index,*,protective=False):
        local=self._manual_io_local;previous=getattr(local,'stack',[])
        local.stack=[]
        try:self.require_manual_authorization(cap,index,protective=protective)
        finally:local.stack=previous

    @contextmanager
    def manual_read_boundary(self,cap,index):
        """One STATUS998/CAP404 read, fully verified before any adoption.

        The Journal lock excludes publication changes during the closed read.
        This scope never makes ordinary require_context() cheaper: only an
        authenticated, ordered read packet may reuse the entry verification.
        """
        local=self._manual_io_local
        need(self._active_manual_read() is None and self._active_manual_io() is None,
             'manual_read_boundary_not_nestable')
        with self.source.journal.lock:
            self._full_manual_io_verification(cap,index)
            state=dict(cap=cap,index=index,thread=threading.get_ident(),phase=0,offset=0,
                       snapshot=None,pages=0)
            local.read=state
            try:
                yield
                need(state['phase']==2 and state['pages']==94,'complete_status998_cap404_read_required')
                self._full_manual_io_verification(cap,index)
                self.manual_io_current(cap,index)
            finally:
                local.read=None

    def _admit_manual_read_page(self,state,cap,index,packet,*,protective):
        need(not protective and state['cap'] is cap and state['index']==index
             and state['thread']==threading.get_ident(),'exact_manual_read_scope_required')
        need(self.composition.command_current(self.graph) is True,'manual_packet_source_not_current')
        self.manual_io_current(cap,index)
        self._validate_manual_original(cap,index)
        b=self.native_bindings[index];w=self.modules.wire
        need(type(packet) is bytes,'authenticated_manual_read_packet_required')
        opened=w.open_packet(b.key,b.device_id,b.boot_generation,b.context_digest,w.Direction.REQUEST,packet)
        need(opened.session==b.session_id and state['phase']<2 and len(opened.body)==7,
             'closed_authenticated_manual_read_required')
        phase=state['phase'];operation,total=(w.Operation.STATUS,998) if phase==0 else (w.Operation.CAPABILITIES,404)
        snapshot,offset,count=struct.unpack('<IHB',opened.body)
        need(opened.operation is operation and count==15 and offset==state['offset']
             and (snapshot==0 if offset==0 else snapshot>0)
             and (offset==0 or state['snapshot'] is None or snapshot==state['snapshot'])
             and state['pages']<94,'ordered_bounded_status998_cap404_read_required')
        if offset:state['snapshot']=snapshot
        state['offset']+=min(15,total-offset);state['pages']+=1
        if state['offset']==total:
            state['phase']+=1;state['offset']=0;state['snapshot']=None

    @contextmanager
    def manual_io_boundary(self,cap,index,*,protective=False,packet=None):
        """One real packet; read reuse is confined to one locked document pair."""
        local=self._manual_io_local
        previous=getattr(local,'stack',[])
        read=self._active_manual_read()
        if read is not None:self._admit_manual_read_page(read,cap,index,packet,protective=protective)
        else:self._full_manual_io_verification(cap,index,protective=protective)
        current=(cap,index,protective,threading.get_ident())
        local.stack=[*previous,current]
        try:
            yield
            self.manual_io_current(cap,index,protective=protective)
        finally:
            local.stack=previous

    def protective_exchange(self,cap,index,packet):
        """One authenticated Travel only; no generic late-input escape hatch."""
        self.protective_context(cap,index);b=self.native_bindings[index];w=self.modules.wire
        need(type(packet) is bytes and 30<=len(packet)<=64,'exact_protective_packet_required')
        opened=w.open_packet(b.key,b.device_id,b.boot_generation,b.context_digest,w.Direction.REQUEST,packet)
        need(opened.operation is w.Operation.WITHDRAW and opened.session==b.session_id
            and opened.body==w.withdraw_body(255,3,True),'only_exact_protective_travel_packet_allowed')
        with self._lock:
            need(index not in self._protective_attempts,'protective_travel_attempt_not_repeatable')
            self._protective_attempts.add(index)
        return self.plant.protective_exchange(index,cap,packet)

    def manual_approval_gate(self,cap,index,kind,admission):
        with self._lock:
            self.require_manual_authorization(cap,index)
            need(kind in ('activation','session') and kind not in cap._used,
                 'one_time_offline_manual_approval_required')
            b=self.native_bindings[index];s=self.modules.session
            if kind=='activation':
                need(type(admission) is self.modules.zha.OwnerActivation and admission.approval_id==cap.activation_approval_id
                    and admission.binding_sha256==b.binding_sha256,'exact_offline_activation_approval_required')
            else:
                expected=s.sha(dict(schema='c12-runtime-bootstrap-scope-1',binding=b.binding_sha256,
                    policy=self.modules.zha.adapter_policy_sha256(cap.policy),Home_qualification=asdict(cap.home_qualification),
                    clock_limits=asdict(cap.clock_limits),initial_mapping_revision=1,initial_packet_sequence=0))
                need(type(admission) is s.SessionAdmission and admission.approval_id==cap.session_approval_id
                    and admission.scope_sha256==expected,'exact_offline_session_approval_required')
            need(admission.valid_until_monotonic_ns==cap.valid_until_monotonic_ns,'offline_approval_deadline_changed')
            used=cap._original_file.with_name(cap._original_file.name+'.'+kind+'.used')
            _exclusive(used,encoded(dict(schema='home-production-offline-approval-consumption-1',kind=kind,
                original_sha256=sha(cap._original_raw),admission=asdict(admission)))+b'\n')
            cap._used.add(kind);return True

    def complete_native_bootstrap(self,index,bootstrap,transport,*,service,observation):
        self.require_context();cap=self.manual_inputs(index);self.require_manual_authorization(cap,index)
        s=self.modules.session;m=self.modules.adapter;b=self.native_bindings[index]
        need(self._native_booted and index not in self._tuples and type(bootstrap) is s.BootstrapOwner
            and type(transport) is self.modules.zha.RuntimeZhaTransport and bootstrap.transport is transport
            and transport.binding is b and bootstrap._complete and bootstrap._transferred and not bootstrap._failed
            and type(bootstrap.clock) is s.QualifiedSessionClock and type(service) is m.TrustedAdapter
            and bootstrap.clock._adapter is service and service._clock is bootstrap.clock and service._transport is transport
            and cap._used=={'activation','session'},'genuine_single_owner_sdk_bootstrap_handoff_required')
        need(type(observation) is CanonicalRoomObservation,'actual_production_native_observation_required')
        decoded=self.modules.zha.decode_snapshot_v3(observation.raw_status_v3,binding=b,
            captured_device_us=observation.current_ref.captured_device_us)
        need(decoded['session_open'] and decoded['context_digest']==b.context_digest.hex()
            and observation.current_ref.binding_sha256==b.binding_sha256
            and observation.current_ref.snapshot_sha256==sha(observation.raw_status_v3)
            and observation.current_ref.capabilities_sha256==sha(observation.raw_capabilities_v2),'original_sdk_native_observation_required')
        cursor=m.SessionCursor(b.boot_generation,b.session_id,b.context_digest.hex(),service._sequence,
                               service._upload_revision,service._grant_floor,service._revision_floor)
        need(type(cursor) is m.SessionCursor and cursor.last_sequence>=bootstrap._sequence
            and cursor.last_grant_id==cursor.last_upload_revision==cursor.last_semantic_revision==0,'sole_fresh_sdk_session_cursor_required')
        self._tuples[index]=ProductionNativeTuple(index,b,cursor,observation,deepcopy(self.modules.provider_pins),sha(self._birth_raw))
        self.member(index).handed_off=True;self._handed.add(index);self.started=len(self._tuples)==4
        return self._tuples[index]

    def member(self,index):
        need(type(index) is int and 0<=index<len(self._native_members),'production_member_unavailable')
        return self._native_members[index]

    def bootstrap_state(self,index):
        self.require_current();need(self.started and index in self._tuples and index not in self._handed,
             'production_native_tuple_handoff_not_repeatable')
        self.member(index).handed_off=True;self._handed.add(index)
        return self._tuples[index]

    def require_native_tuple(self,value):
        self.require_context();need(type(value) is ProductionNativeTuple and self.started
            and self._tuples.get(value.fixture_index) is value and value.birth_sha256==sha(self._birth_raw)
            and value.provider_pins==self.modules.provider_pins,'actual_registered_production_native_tuple_required')
        return True

    def advance_native(self):
        self.require_context()
        return [member.feedback() for member in self._native_members]

    def quiesce(self):self.stop_output_admission();self.cancel.set()

    def close(self):
        if self._close_receipt is not None:return deepcopy(self._close_receipt)
        self.quiesce()
        if self.plant is not None and not self._attempted:
            need(self.client._launches==0 and not self.plant._attempted and all(m is None for m in self.plant._members),
                 'actual_zero_native_launches_required_for_inert_close')
            result=self.recorder.close_unstarted()
        else:
            result=self.plant.close() if self.plant is not None else dict(status='incomplete',reason='production_never_prepared')
        self.closed=True
        self._close_receipt=deepcopy(result)
        return deepcopy(result)
