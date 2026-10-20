"""Opt-in Apartment JPEG viewing from the incumbent camera owner, bounded RAM.

No new camera owner, durable analyzed JPEGs, metric geometry or physical authority. Exact
commissioned JPEG clocks remain original; display receipt never renews them.
The dedicated viewing recipe produces no encoded recording artifact. Ordinary
recipes retain the incumbent recorder; private observation metadata is retained.
"""
from collections import OrderedDict
from copy import deepcopy
import hashlib
import json
import threading
import time
import uuid

RECIPE='apartment-viewing'
FAMILY='home-apartment-viewing-jpeg-1'
PREFIX='view_'
POLICY=dict(schema='home-apartment-viewing-policy-1',version=1,enabled=True,
    session_seconds=300,max_frames=8,max_retained_bytes=8388608)


def policy(value):
    if value!=POLICY or value.get('enabled') is not True or any(type(value.get(k)) is not int for k in ('version','session_seconds','max_frames','max_retained_bytes')):
        raise ValueError('exact bounded Apartment viewing policy required')
    return deepcopy(value)


def capability(value):
    p=policy(value)
    return dict(provisioned=True,experiment_id=RECIPE,session_seconds=p['session_seconds'],media_storage='bounded_ram',
        max_frames=p['max_frames'],max_retained_bytes=p['max_retained_bytes'],automatic_renewal=False,
        viewing_only=True,physical_permission=False,metric_depth='unavailable',encoded_recording=False)


def is_viewing_id(mid):return isinstance(mid,str) and mid.startswith(PREFIX)


def _source(store,sid):
    from store import LabError
    found=getattr(store,'_apartment_viewing_sources',{}).get(sid)
    if found is None:raise LabError('viewing media unavailable',410,'media_unavailable')
    return found


def registered_media(store,sid,mid,generation=None,*,fresh=False,retained=False):
    # Namespace is explicit: missing/transient bytes never fall through to the
    # ordinary durable/qualified Store and cannot resurrect after restart.
    # retained=True (Home media routes and the preview dispatch read only) may
    # serve a development-retained metric PNG past its original deadline.
    if is_viewing_id(mid):return _source(store,sid).resolve(mid,generation,fresh=fresh,retained=retained)[0]
    return store.get_media(sid,mid,generation)


def viewing_bytes(store,sid,mid,generation,*,fresh=True,retained=False):
    return _source(store,sid).resolve(mid,generation,fresh=fresh,retained=retained)[1]


def prune(store,sid):
    source=getattr(store,'_apartment_viewing_sources',{}).get(sid)
    if source is not None:source.prune()


def development_preview(store,sid,media_ids):
    """The dispatch hold (s) for one registered metric PNG of a development-timing
    source, else None. Only the runner's arm-B request uses it."""
    source=getattr(store,'_apartment_viewing_sources',{}).get(sid)
    if source is None or source.metric is None or not source.metric.development:return None
    with source.lock:
        entry=source.entries.get(media_ids[0]) if isinstance(media_ids,list) and len(media_ids)==1 else None
        if entry is None or entry['record']['mime_type']!='image/png' or source.closed:return None
    from apartment_viewing_metric_source import DEVELOPMENT_LEASE_S
    return DEVELOPMENT_LEASE_S


class ViewingRecorder:
    """Owned analysis lifecycle only; never a claim that video was recorded."""
    thread=None
    process=None
    def __init__(self,store,camera_id):self.store,self.camera_id,self.sid=store,camera_id,None
    def start(self,session):
        current=self.store.get_session(session['id'])
        if (current['experiment_id']!=RECIPE or current['camera_id']!=self.camera_id
                or current['state'] not in ('starting','recording') or self.sid not in (None,current['id'])):
            return {'state':'blocked','reason':'viewing_session_scope_mismatch'}
        self.sid=current['id']
        return {'state':'recording','viewing_only':True,'encoded_recording':False,'recording_artifact':'unavailable'}
    def stop(self,session):
        if self.sid not in (None,session['id']):return {'state':'indeterminate','reason':'viewing_session_scope_mismatch'}
        return {'state':'stopped','viewing_only':True,'encoded_recording':False,'recording_artifact':'unavailable'}


class ViewingFactory:
    def __init__(self,store,*,camera_id,policy,metric_source=None):
        self.store,self.camera_id,self.policy=store,camera_id,globals()['policy'](policy)
        if metric_source is not None:
            from apartment_viewing_metric_source import MetricInputFactory
            if not isinstance(metric_source,MetricInputFactory):raise ValueError('trusted metric input factory required')
        self.metric_source=metric_source
        self.runtime=None
    def bind_runtime(self,runtime):
        if self.runtime is not None or runtime.store is not self.store:raise ValueError('one matching runtime required')
        self.runtime=runtime
    def new_session(self,session):
        if session['experiment_id']!=RECIPE or session['camera_id']!=self.camera_id:raise ValueError('viewing recipe scope required')
        return ViewingSource(self,session)


class ViewingSource:
    def __init__(self,factory,session):
        from preview_ingest import PreviewIngestor
        self.store,self.runtime,self.camera_id,self.policy=factory.store,factory.runtime,factory.camera_id,factory.policy
        self.sid,self.media_generation=session['id'],session['generation']
        self.generation=None;self.epoch=None;self.closed=False;self.reason=None
        self.lock=threading.RLock();self.entries=OrderedDict();self.keys={};self.accepted=0;self.bytes=0
        self.pinned={}
        self.created_mono=time.monotonic();self.timer=None
        self.expiry_dispatch_started=False;self.expiry_dispatch=None
        self.expiry_dispatch_done=threading.Event();self.expiry_dispatch_error=None
        registry=getattr(self.store,'_apartment_viewing_sources',None)
        if registry is None:registry={};self.store._apartment_viewing_sources=registry
        # ManagedPair owns one admitted recording. Refuse another active RAM
        # owner instead of silently replacing its buffer.
        for old in list(registry.values()):
            if not old.closed:raise ValueError('another viewing media owner exists')
        registry[self.sid]=self
        self.ingestor=PreviewIngestor(self.store,site_id=session['site_id'],camera_id=self.camera_id,
            clock_validation_id='original-causal-receipt-required',clock_uncertainty_s=0,media_writer=self)
        self.metric=factory.metric_source.new_session(self) if factory.metric_source is not None else None
        # Deadline starts with the owned source, even if every capture fails.
        # It is never created or renewed by receipt of a successful image.
        remaining=300
        if session.get('started_at') is not None:remaining=min(remaining,300-(self.store.clock()-session['started_at']))
        self.timer=threading.Timer(max(0,remaining),self.expire);self.timer.daemon=True;self.timer.start()
    def current(self):
        if self.closed:raise ValueError('viewing source stopped')
        session=self.store.get_session(self.sid);runner=self.runtime.runners.get(self.sid)
        if (self.runtime.closed or session['state']!='recording' or session['generation']!=self.media_generation
                or runner is None or runner.closed or (self.generation is not None and runner.generation!=self.generation)):
            self.retire('scope_changed');raise ValueError('viewing source scope changed')
        elapsed=time.monotonic()-self.created_mono
        if session.get('started_at') is not None:elapsed=max(elapsed,self.store.clock()-session['started_at'])
        if elapsed>=300:
            self.expire();raise ValueError('viewing session duration exhausted')
        return session,runner
    def expire(self):
        self.retire('duration_limit')
    def retire(self,reason):
        with self.lock:
            if self.closed and self.reason!=reason:return
        self.stop(reason)
        # current() is also called by presentation while PairedRunner holds its
        # lock. Never acquire ServiceRuntime.lock on that stack: ordinary input
        # holds runtime then runner. Retirement is synchronous; only this owned
        # dispatcher may perform lifecycle Stop after those caller locks release.
        with self.lock:
            if self.expiry_dispatch_started:return
            if self.runtime.closed:
                self.expiry_dispatch_done.set();return  # Parent close owns drain.
            self.expiry_dispatch_started=True
            self.expiry_dispatch=threading.Thread(target=self._finish_expiry,name='apartment-viewing-stop',daemon=True)
            dispatcher=self.expiry_dispatch
            try:dispatcher.start()
            except Exception as error:
                self.expiry_dispatch_error=type(error).__name__;self.expiry_dispatch_done.set()
    def _finish_expiry(self):
        try:
            session=self.store.get_session(self.sid)
            if session['state']=='recording':
                self.store.transition(self.sid,'stopping','viewing_'+self.reason)
                self.runtime.stop_session(self.store.get_session(self.sid))
        except Exception as error:self.expiry_dispatch_error=type(error).__name__
        finally:self.expiry_dispatch_done.set()
    def prior(self,sid,key):
        if sid!=self.sid:raise ValueError('viewing writer scope mismatch')
        with self.lock:
            mid=self.keys.get(key);entry=self.entries.get(mid)
            return deepcopy(entry['record']['metadata']) if entry else None
    def _leases(self):
        ids=set()
        with self.store.lock:
            # Two active and two replaceable pending slots, never a queue.
            rows=self.store.db.execute("SELECT payload FROM paired_requests WHERE session_id=? AND generation=? AND state IN ('offered','dispatching','running','indeterminate') LIMIT 8",(self.sid,self.generation)).fetchall()
            for row in rows:ids.update(json.loads(row[0])['frame']['media_ids'])
            for arm in ('a','b'):
                row=self.store.db.execute("SELECT payload FROM events WHERE session_id=? AND kind='perception.output' AND json_extract(payload,'$.arm')=? AND json_extract(payload,'$.generation')=? ORDER BY id DESC LIMIT 1",(self.sid,arm,self.generation)).fetchone()
                if row:ids.update(json.loads(row[0]).get('media_ids',[]))
        return ids
    def prune(self):
        if self.closed:return
        now=self.store.clock()
        with self.lock:
            if self.metric is not None:self.metric.prune_locked(now)
            for mid,entry in list(self.entries.items()):
                # Descriptor and range reads are separate authenticated calls.
                # A newer completion must not revoke a still-valid original
                # between them. Admission retains the same hard RAM bounds.
                if now>=entry.get('retain_until',entry['record']['metadata']['valid_until']):
                    self.bytes-=entry['charge'];self.keys.pop(entry['key'],None);del self.entries[mid]
    def retain_metric_png_locked(self,mid,until,*,supporting):
        # Development timing only: the published PNG (and the latest supporting
        # one) stays fetchable after its original deadline; never the reverse.
        entry=self.entries.get(mid)
        if entry is None:return
        entry['retain_until']=max(entry.get('retain_until',0),entry['record']['metadata']['valid_until'],until)
        self.pinned['preview']=mid
        if supporting:self.pinned['supporting']=mid
    def _evict_retained_locked(self,extra,metadata_charge):
        # Other past-deadline PNGs go first, oldest first, before any refusal.
        now=self.store.clock()
        for mid,entry in list(self.entries.items()):
            if (len(self.entries)<8 and self._resident_bytes_locked(extra=extra,metadata_charge=metadata_charge)
                    +self._reserved_bytes_locked()<=8388608):return
            if mid in self.pinned.values() or now<entry['record']['metadata']['valid_until']:continue
            self.bytes-=entry['charge'];self.keys.pop(entry['key'],None);del self.entries[mid]
    def add_media(self,sid,data,mime,meta,key):
        session,runner=self.current()
        if sid!=self.sid or mime!='image/jpeg' or not isinstance(data,bytes) or not 0<len(data)<=256*1024:
            raise ValueError('bounded exact viewing JPEG required')
        self.prune()
        metadata=deepcopy(meta)
        metadata.update(original_family=FAMILY,viewing_only=True,geometric_validity='unvalidated',
            operational_geometry=None,geometry_authority=False,beam_authority=False,physical_permission=False)
        raw_meta=json.dumps(metadata,sort_keys=True,separators=(',',':')).encode()
        charge=len(data)+len(raw_meta);sha=hashlib.sha256(data).hexdigest()
        with self.lock:
            if self.closed:raise ValueError('viewing source stopped')
            if key in self.keys:
                old=self.entries[self.keys[key]]['record']
                if old['sha256']!=sha or old['metadata']!=metadata:raise ValueError('viewing original identity changed')
                return deepcopy(old)
            self._evict_retained_locked((data,),len(raw_meta))
            if len(self.entries)>=8 or self._resident_bytes_locked(extra=(data,),metadata_charge=len(raw_meta))+self._reserved_bytes_locked()>8388608:raise ValueError('viewing RAM budget exhausted')
            mid=PREFIX+uuid.uuid4().hex
            record=dict(id=mid,media_id=mid,session_id=self.sid,site_id=session['site_id'],generation=self.media_generation,
                mime_type=mime,content_type=mime,size=len(data),size_bytes=len(data),sha256=sha,metadata=metadata,
                created_at=self.store.clock(),viewing_only=True,
                url=f'/api/perception-lab/v1/sessions/{self.sid}/media/{mid}?generation={self.media_generation}')
            self.entries[mid]=dict(record=record,data=data,key=key,charge=charge);self.keys[key]=mid;self.bytes+=charge
            self.accepted+=1
            return deepcopy(record)
    def _reserved_bytes_locked(self):return self.metric.reserved_bytes if self.metric is not None else 0
    def _resident_bytes_locked(self,*,extra=(),metadata_charge=0):
        buffers={id(entry['data']):len(entry['data']) for entry in self.entries.values()}
        overhead=sum(entry['charge']-len(entry['data']) for entry in self.entries.values())+metadata_charge
        if self.metric is not None:
            buffers.update({id(raw):len(raw) for raw in self.metric.retained_byte_allocations()})
        buffers.update({id(raw):len(raw) for raw in extra})
        return sum(buffers.values())+overhead
    def register_metric_frame(self,lease):
        from apartment_viewing_metric import PreparedMetricFrame
        if self.metric is None or type(lease) is not PreparedMetricFrame or self.metric.lease is not lease:
            raise ValueError('owned prepared metric lease required')
        session,runner=self.current();binding=lease.binding
        if (binding['session']!={key:session[key] for key in ('id','site_id','camera_id')} or binding['arm']!='b'
                or binding['model_generation']!=runner.generation or binding['media_generation']!=self.media_generation
                or binding['lab_profile']!=runner.profiles['b']):raise ValueError('metric frame binding changed')
        data,meta=lease.image_bytes,lease.image_metadata;charge=len(data)+len(json.dumps(meta,sort_keys=True,separators=(',',':')).encode())
        key=json.dumps(['metric',meta['capture_epoch'],meta['timing_generation'],meta['sequence']])
        self.prune()
        with self.lock:
            if self.closed:raise ValueError('viewing source stopped')
            if self.metric.lease is not lease or (self.store.clock()>=lease.descriptor['retirement_deadline']
                    and not self.metric.development):
                raise ValueError('metric_registration_retired')
            if key in self.keys:raise ValueError('metric original already registered')
            self._evict_retained_locked((data,),charge-len(data))
            if (len(self.entries)>=8 or self._resident_bytes_locked(extra=(data,),metadata_charge=charge-len(data))>8388608):
                raise ValueError('viewing RAM budget exhausted')
            mid=PREFIX+uuid.uuid4().hex
            record=dict(id=mid,media_id=mid,session_id=self.sid,site_id=session['site_id'],generation=self.media_generation,
                mime_type='image/png',content_type='image/png',size=len(data),size_bytes=len(data),sha256=hashlib.sha256(data).hexdigest(),
                metadata=meta,created_at=self.store.clock(),viewing_only=True,
                url=f'/api/perception-lab/v1/sessions/{self.sid}/media/{mid}?generation={self.media_generation}')
            self.entries[mid]=dict(record=record,data=data,key=key,charge=charge);self.keys[key]=mid;self.bytes+=charge;self.accepted+=1
            if self.metric.development:
                # The arm-B worker must still read this PNG while its lease waits.
                from apartment_viewing_metric_source import DEVELOPMENT_LEASE_S
                self.entries[mid]['retain_until']=max(meta['valid_until'],self.store.clock()+DEVELOPMENT_LEASE_S)
                self.pinned['lease']=mid
            return deepcopy(record)
    def ingest(self,sid,jpeg,header):
        if sid!=self.sid:raise ValueError('viewing input scope mismatch')
        if self.metric is not None:
            frame=self.metric.ingest(jpeg,header)
            if frame is not None:return frame
            self.current()
            # Normal anchor/model waits preserve the common metric input and
            # its per-arm sampling slot. Only an unavailable optional path
            # falls back; interleaving preview frames with a pending metric
            # image could otherwise consume that image's sampling slot.
            if self.metric.reason in ('awaiting_anchor','awaiting_metric_result','approximate_room_pose_available'):return None
            # Optional depth/registration refusal cannot suppress an otherwise
            # current original RGB. This is the exact commissioned JPEG, not
            # the metric PNG: its own clocks, identity and expiry are checked
            # below and it acquires no lease or depth/alignment provenance.
        from assessment_existing_api import _receipt
        _receipt(jpeg,header)  # Exact incumbent schema3; no legacy clock fallback.
        _,runner=self.current()
        if self.generation is None:self.generation=runner.generation
        frame=self.ingestor.ingest(sid,jpeg,header)
        if self.epoch is not None and frame['capture_epoch']!=self.epoch:
            self.retire('capture_epoch_changed');raise ValueError('viewing capture epoch changed')
        self.epoch=frame['capture_epoch']
        self.current()
        return frame
    def resolve(self,mid,generation,*,fresh,retained=False):
        from store import LabError
        try:self.current()
        except Exception:raise LabError('viewing media unavailable',410,'media_unavailable') from None
        self.prune()
        with self.lock:
            entry=self.entries.get(mid);now=self.store.clock()
            deadline=entry['record']['metadata']['valid_until'] if entry is not None else None
            until=entry.get('retain_until',deadline) if entry is not None and retained else deadline
            if (entry is None or generation is not None and generation!=self.media_generation
                    or fresh and now>=until):
                raise LabError('viewing media unavailable',410,'media_unavailable')
            record=deepcopy(entry['record'])
            if retained and until>deadline:
                # Development only: the original deadline is unchanged and named.
                record['retention']={'policy':'development','original_valid_until':deadline,'retained_until':until}
                if fresh and now>=deadline and self.metric is not None and mid in self.metric.frame_warnings:
                    self.metric._warn(self.metric.frame_warnings[mid],'media_expired',now,deadline)
            return record,entry['data']
    def stop(self,reason='stop'):
        with self.lock:
            if self.closed:return
            self.closed=True;self.reason=reason;self.entries.clear();self.keys.clear();self.bytes=0
            if self.metric is not None:self.metric.clear_locked()
            if self.timer is not None:self.timer.cancel();self.timer=None
        registry=getattr(self.store,'_apartment_viewing_sources',{})
        if registry.get(self.sid) is self:registry.pop(self.sid,None)
        from remote_specialist import retire_viewing_images
        retire_viewing_images(self.store,self.sid)
        pump=self.runtime.preview_pumps.get(self.sid)
        if pump is not None:pump.stop()
    def status(self):
        if not self.closed:self.prune()
        with self.lock:return dict(state='stopped' if self.closed else 'active',reason=self.reason,
            accepted_frames=self.accepted,resident_frames=len(self.entries),resident_bytes=self._resident_bytes_locked(),
            session_seconds=300,max_frames=8,max_retained_bytes=8388608,media_storage='bounded_ram',
            expiry_stop_pending=self.expiry_dispatch_started and not self.expiry_dispatch_done.is_set(),
            expiry_stop_error=self.expiry_dispatch_error,
            viewing_only=True,physical_permission=False,metric_depth='diagnostic_nominal_eeprom' if self.metric is not None else 'unavailable',encoded_recording=False,
            **({'metric':self.metric.status()} if self.metric is not None else {}))
